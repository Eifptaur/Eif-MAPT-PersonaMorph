# -*- coding: utf-8 -*-
"""「不打扰」开关守卫判据（`wechat.background_only` / `input.allow_real_fallback` / `ui.allow_foreground`）。

**为什么有这条**：群相反馈（另一台机器）——告"抢窗口/动鼠标"，而报告里
`wechat.background_only=False`、`input.allow_real_fallback=True`。查证：默认值、一次性迁移、
示例配置**三者都是安全侧**，后端也是"提交里有才覆盖" ⇒ 唯一能翻过去的形态是
**前端整份回写**把**没被正确回填**的复选框（undefined ⇒ checked=false）写下了盘。

守四件事：
  ① **老客户端/Qt 不破坏**：没声明 `_touched` ⇒ 只审计、不拦（0 回归）；
  ② **新控制台未亲手点过 ⇒ 拦下并保持磁盘上的安全值**（这就是"连带翻掉"的那一刀）；
  ③ **用户亲手点过 ⇒ 放行**（不能把路堵死）；
  ④ **往安全侧永远放行** + 每次改动都留审计（下次反馈能看出是谁翻的）。

用法：`py -3 scripts/safe_switch_selftest.py`
"""
import io
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

import _srcmatch as _sm # noqa: E402
from agent import config as C # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


SAFE_BASE = {"wechat": {"background_only": True}, "input": {"allow_real_fallback": False},
             "ui": {"allow_foreground": False}}
UNSAFE = {"wechat": {"background_only": False}, "input": {"allow_real_fallback": True},
          "ui": {"allow_foreground": True}}

print("── A. 守卫语义四条 ──")
# ① 老客户端（没声明 touched）⇒ 只审计不拦
_d1 = json.loads(json.dumps(UNSAFE))
_r1 = C.guard_safe_switches(_d1, touched=None, source="legacy-qt", cur=SAFE_BASE)
ok("A1 老客户端/Qt 没声明 `_touched` ⇒ 只审计不拦（不破坏兼容）",
   all(act == "applied" for _k, _o, _n, act in _r1) and _d1["wechat"]["background_only"] is False,
   str([(k, act) for k, _o, _n, act in _r1]))
# ② 新控制台、用户没碰 ⇒ 拦下并保持安全值（★ 用户那台机器的场景）
_d2 = json.loads(json.dumps(UNSAFE))
_r2 = C.guard_safe_switches(_d2, touched=set(), source="console", cur=SAFE_BASE)
ok("A2 ★ 新控制台**没亲手点过** ⇒ 拦下 + 保持磁盘上的安全值（这就是「连带翻掉」那一刀）",
   all(act == "blocked" for _k, _o, _n, act in _r2)
   and _d2["wechat"]["background_only"] is True
   and _d2["input"]["allow_real_fallback"] is False
   and _d2["ui"]["allow_foreground"] is False,
   "落盘 %s" % {k: _d2[k.split('.')[0]][k.split('.')[1]] for k in C.SAFE_SWITCHES})
# ③ 用户亲手点过 ⇒ 放行
_d3 = json.loads(json.dumps(UNSAFE))
_r3 = C.guard_safe_switches(_d3, touched=set(C.SAFE_SWITCHES), source="console", cur=SAFE_BASE)
ok("A3 用户**亲手点过** ⇒ 放行（不能把路堵死）",
   all(act == "applied" for _k, _o, _n, act in _r3) and _d3["wechat"]["background_only"] is False)
# ④ 往安全侧：永远放行
_d4 = json.loads(json.dumps(SAFE_BASE))
_r4 = C.guard_safe_switches(_d4, touched=set(), source="console", cur=json.loads(json.dumps(UNSAFE)))
ok("A4 ★ 往**安全侧**翻（重新打开「只走后台」）⇒ 永远放行",
   all(act == "applied" for _k, _o, _n, act in _r4) and _d4["wechat"]["background_only"] is True)
# ⑤ 没提这个键 ⇒ 不管（不产生噪声）
_d5 = {"wechat": {"poll_interval": 3.0}}
ok("A5 配置里没提这些键 ⇒ 一条都不记（不产生噪声）",
   C.guard_safe_switches(_d5, touched=set(), source="console", cur=SAFE_BASE) == [])

print("\n── B. 审计（下次反馈能看出是谁翻的）──")
_tmp = os.path.join(tempfile.mkdtemp(prefix="pm-audit-"), "audit.jsonl")
_old_audit = C.AUDIT_PATH
try:
    C.AUDIT_PATH = _tmp
    C.guard_safe_switches(json.loads(json.dumps(UNSAFE)), touched=set(), source="console",
                          cur=SAFE_BASE)
    lines = [ln for ln in io.open(_tmp, encoding="utf-8").read().splitlines() if ln.strip()]
    rec = json.loads(lines[-1]) if lines else {}
    ok("B1 每次翻转都落一条审计（键/新旧值/来源/动作）",
       bool(rec) and rec.get("key") in C.SAFE_SWITCHES and rec.get("action") == "blocked"
       and rec.get("src") == "console" and "t" in rec, str(rec)[:110])
    ok("B2 审计里**没有隐私**（只有键名/布尔/来源/时间）",
       not any(x in json.dumps(rec, ensure_ascii=False) for x in ("wxid", "@chatroom", "C:\\")),
       "")
finally:
    C.AUDIT_PATH = _old_audit
    try:
        os.remove(_tmp)
    except OSError as e:
        print("   （临时审计文件清理失败：%s）" % e)

print("\n── C. 前端接线（第一道防线；源码级）──")
_html = src("assets/console/index.html")
ok("C1 `syncFromForm` 里对这三个键有「没亲手点过就跳过」",
   _sm.has(_html, "SAFE_SWITCH_KEYS.indexOf(path) >= 0") and _sm.has(_html, "window._touchedCfg && window._touchedCfg.has(path)"))
ok("C2 用户真点复选框时被记录（代理监听 + 程序性填表排除）",
   _sm.has(_html, "document.addEventListener('change'") and _sm.has(_html, "window._touchedCfg.add(el.dataset.cfg)")
   and _sm.has(_html, "if(window._applying) return;"))
ok("C3 `/api/config` 的 POST 统一带上 `_touched`（唯一入口包装，覆盖 10 处提交点）",
   _sm.has(_html, "_o._touched = Array.from(window._touchedCfg)") and _sm.has(_html, "getJSON = async function(url, opts)"))
ok("C4 ★ 没有自递归（`_getJSONRaw` 先取原函数，再覆盖 getJSON）",
   _html.find("const _getJSONRaw = getJSON;") > 0
   and _html.find("const _getJSONRaw = getJSON;") < _html.find("getJSON = async function(url, opts)"))
ok("C5 后端路由把 `_touched` 从 payload 里**摘出来**（不许落进 config.json）",
   _sm.has(src("agent/webui.py"), 'if isinstance(data, dict) and "_touched" in data:')
   and _sm.has(src("agent/webui.py"), '_tv = data.pop("_touched")'))

print("\n── D. 真实链路：整份回写 + deep_merge（复现用户那台机器的场景）──")
_cfg_cur = json.loads(json.dumps(SAFE_BASE))
_submitted = json.loads(json.dumps(SAFE_BASE))
_submitted["wechat"]["background_only"] = False      # 没被回填的复选框 → false
_submitted["input"]["allow_real_fallback"] = True
_merged = C.deep_merge(_cfg_cur, _submitted)
C.guard_safe_switches(_merged, touched=set(), source="console", cur=_cfg_cur)
ok("D1 ★ 复现场景：整份回写后，`background_only` 仍是 True（不再被连带翻掉）",
   _merged["wechat"]["background_only"] is True and _merged["input"]["allow_real_fallback"] is False,
   "background_only=%s allow_real_fallback=%s" % (_merged["wechat"]["background_only"],
                                                  _merged["input"]["allow_real_fallback"]))
ok("D2 反向锚：同一份 payload 里**别的键**照旧生效（守卫不误伤）",
   C.deep_merge({"wechat": {"poll_interval": 2.0}}, {"wechat": {"poll_interval": 4.0}})["wechat"]["poll_interval"] == 4.0)

print("")
print("不打扰开关判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
