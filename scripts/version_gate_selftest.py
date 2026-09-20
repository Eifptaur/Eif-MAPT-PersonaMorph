#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版本门判据（W7 余项）：没实测过的版本对 ⇒ 暂停自动发送 + 控制台可临时放行（重启失效）

为什么要有这条：`version_matrix.gate()` 早就会给出「没实测记录」，但以前**没有任何地方问它** ——
用户升级微信后发送照旧跑，出问题才知道这版没验证过。本判据守住接线与三级行为。

判据（不需要微信、不起服务）：
  ① 默认拦：版本对没实测（本机 adapter=1.2.2.2 / wechat 未知或未记录）⇒ allow=False、level=warn
  ② 临时放行：allow_session() 后 allow=True（且 level 仍是 warn —— 放行不等于"已验证"）
  ③ clear_allow() / 新进程 ⇒ 重新拦；且实现里**不许落盘**（放行不写配置/文件）
  ④ 接线：三个 send 入口都要问门（send_text / send_image / send_file_posted）
  ⑤ 控制台：能力矩阵面板 + 版本门横幅 + 「本次允许发送」按钮 + /api/version/allow 端点
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


from agent import version_gate as vg  # noqa: E402

print("── A. 三级行为（先把版本强制成「读不到」，这样在哪台机器上结论都一样）──")
# 🔴 2026-09-18 改口径（两位网友报障 + 作者「保险加多了，能发出去的也变成发不出去」）：
#   **默认不拦发送** —— 读不到版本＝环境态；未实测版本＝告警但照发。只有用户显式开
#   `version_gate.strict=true` 时，原来的"暂停自动发送 + 点本次允许发送"才生效。
_REAL_LOOKUP = vg.current_wechat_version
vg.current_wechat_version = lambda *a, **k: ""
vg.clear_allow()
a = vg.check()
ok("默认（非 strict）：读不到版本 ⇒ **不拦**（allow=True）", a["allow"] is True, "level=%s allow=%s" % (a["level"], a["allow"]))
ok("告警的 level=warn（不是 ok）", a["level"] == "warn", a["level"])
ok("理由里写明「不拦发送/照常发」+ 让用户反馈", ("不拦发送" in a["reason"]) or ("照常发" in a["reason"]), a["reason"][:60])
_r_real = vg.wechat_running
vg.wechat_running = lambda: True
_r_running = vg.check()["reason"]
vg.wechat_running = lambda: False
_r_absent = vg.check()["reason"]
vg.wechat_running = _r_real
ok("「微信在跑但读不到版本号」与「微信没在跑」给出**不同**的话（不许混成一句）",
   _r_running != _r_absent and ("没在跑" in _r_absent) and ("没在跑" not in _r_running),
   "在跑=>%s ｜ 没在跑=>%s" % (_r_running[:34], _r_absent[:34]))
# 严格档：用户显式开了 strict 才恢复"暂停发送 + 本次允许发送"
_strict_real = vg._strict
vg._strict = lambda: True
c = vg.check()
ok("strict 档：读不到版本 ⇒ 拦（allow=False）", c["allow"] is False, "level=%s" % c["level"])
ok("strict 档：理由里说清怎么放行", ("放行" in c["reason"]) and ("strict" in c["reason"]), c["reason"][:70])
vg.allow_session("selftest")
b = vg.check()
ok("strict 档下临时放行后 allow=True", b["allow"] is True)
ok("放行后 level 仍是 warn（放行≠已验证）", b["level"] == "warn", b["level"])
vg.clear_allow()
ok("清掉后重新拦（strict 档）", vg.check()["allow"] is False)
vg._strict = _strict_real
# 版本门自己出错时**不许**成为新的故障点（fail-open）
_exc_real = vg.current_wechat_version
vg.current_wechat_version = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
ok("版本门异常 ⇒ fail-open（不拦发送）", vg.check()["allow"] is True)
vg.current_wechat_version = _exc_real
vg.current_wechat_version = _REAL_LOOKUP
st = vg.status()
ok("status() 带 allowed_session 字段", "allowed_session" in st, str(st.get("allowed_session")))

print("── A2. 展示侧兜底：不带参数也要自己查到版本 ──")
_ver = vg.current_wechat_version()
if _ver:
    vg._ver_cache.update({"at": 0, "wechat": ""})
    c = vg.check()
    ok("不带参数的 check() 自己查到了版本（不再是 unknown）", c["wechat"] == _ver, "wechat=%r" % c["wechat"])
    e = vg.check("send", wechat=_ver)
    ok("与显式传版本结论一致（allow/level 都一样）",
       (c["allow"], c["level"]) == (e["allow"], e["level"]),
       "no-arg=%s/%s explicit=%s/%s" % (c["allow"], c["level"], e["allow"], e["level"]))
    ok("理由里不再出现「读不到微信版本」", "读不到" not in c["reason"], c["reason"][:60])
else:
    print("  · 本机读不到微信版本（机器相关）⇒ 跳过这一节")
try:
    from agent import wechat as _W
    _n = {"c": 0}
    _orig_gate_ver = _W.wx_version_for_gate

    def _fake_ver():
        _n["c"] += 1
        return "4.1.15.8"

    _W.wx_version_for_gate = _fake_ver
    vg._ver_cache.update({"at": 0, "wechat": ""})
    for _ in range(3):
        vg.check()
    ok("60 秒内只查一次版本（不每次轮询都枚举进程）", _n["c"] == 1, "查了 %d 次" % _n["c"])
    _W.wx_version_for_gate = _orig_gate_ver
except Exception as _e:
    ok("60 秒内只查一次版本（不每次轮询都枚举进程）", False, str(_e)[:80])

print("── B. 放行不许落盘（重启就失效）──")
G = open(os.path.join(ROOT, "agent", "version_gate.py"), encoding="utf-8", errors="replace").read()
ok("version_gate 里没有写文件/写配置", (re.search(r"open\(", G) is None) and ("json.dump" not in G) and ("SaveKey" not in G))

print("── C. 接线：三个 send 入口都要问门 ──")
W = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8", errors="replace").read()
n = len(re.findall(r"version_gate as _vg", W))
ok("三个 send 入口都接了门", n >= 3, "命中 %d 处" % n)
for sig in ("def send_text(", "def send_image(", "def send_file_posted("):
    i = W.find(sig)
    seg = W[i:i + 1400] if i >= 0 else ""
    ok("%s 里就问门" % sig.split("(")[0].replace("def ", ""), "version_gate" in seg)

print("── D. 控制台与端点 ──")
WEB = open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8", errors="replace").read()
from agent import console_html as _ch  # noqa: E402
H = _ch.HTML
ok("/api/version/allow 端点在位", "/api/version/allow" in WEB)
ok("status 带 version_gate", "version_gate" in WEB and "st[\"version_gate\"]" in WEB)
ok("能力矩阵面板在位", all(k in H for k in ("sec-vermat", "vmVer", "vmList")))
ok("版本门横幅 + 放行按钮在位", ("vmGate" in H) and ("vmAllow" in H))
ok("按钮接的是放行端点", "getJSON('/api/version/allow')" in H)
ok("横幅文案说清「只对本次运行有效」", "只对本次运行有效" in H)
ok("能力矩阵读的是 status.version（不是另拉一份）", "const vm = s.version || {}" in H)

# ── ⛔ 2026-09-21（第四轮审计 **V-R4-10，P2**）：富化段被吞 ⇒ 前端把"读不到"画成
#    「版本未实测：按严格档暂停发送（可在配置里关掉 version_gate.strict）」= 把用户指去改一个
#    根本没拦他的开关。口径：**读数读不到 ≠ 不许发**，两侧都要如实表达。 ──
print("── D2. 读不到 ≠ 不许发（V-R4-10） ──")
ok("D2a status 里 version_gate **单独一层 try**（别的富化段炸了也不许把它一起丢掉）",
   'st["version_gate"] = _vg2.status()' in WEB
   and '{"allow": None, "level": "unknown",' in WEB)
ok("D2b 出错时放的是 `allow=None`（读不到），**不是** False（不许发）",
   '"allow": None, "level": "unknown"' in WEB)
ok("D2c 外层 except 也兜底放好这两个键（不再 `except: pass` 悄悄丢）",
   "st.setdefault(\"version_gate\", {\"allow\": None" in WEB and "st.setdefault(\"version\"," in WEB)
ok("D2d 前端三态：只有 `allow === false` 才画红字「按严格档暂停发送」",
   "typeof vg.allow === 'boolean'" in H and "(vg && typeof vg.allow === 'boolean') ? vg.allow : null" in H)
ok("D2e 前端对「读不到」有中性文案（并说明不影响发送）",
   "版本门读数读不到" in H and "：不影响发送" in H)
ok("D2f 老写法（`vg.allow ? … : 红字`）已被替换掉",
   "v2.textContent = vg.allow" not in H)
ok("D2g 反例锚：老形状（`except: pass` ＋ 二值前端）**确实**会被这组判据判不合格",
   (lambda w, h: ("st.setdefault(\"version\"," not in w) and ("typeof vg.allow" not in h)
                 and ("st[\"version_gate\"] = _vg2.status()" in w) and ("v2.textContent = vg.allow" in h))(
       '                            st["version"] = _vm.current()\n'
       '                            st["version_gate"] = _vg2.status()\n'
       '                    except Exception:\n'
       '                        pass\n',
       "          v2.textContent = vg.allow ? 'ok' : '版本未实测：按严格档暂停发送';\n"))

print("== [version-gate] 判据：{} 通过 / {} 失败 ==".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
