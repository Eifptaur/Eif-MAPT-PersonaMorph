# -*- coding: utf-8 -*-
"""环境自适应判据（探测 → 策略 → 逐机记忆；**默认不改行为**）。

守五件事：
  ① 探测**只读**且**不含隐私**（机器指纹里不许出现账号/群号/用户名）；
  ② 策略每条都能说清"依据哪条探测"（`evidence`），且族在白名单里；
  ③ **默认关 ⇒ 行为完全不变**（反向锚：`auto_apply` 关着时 `route_order` 原样返回）；
  ④ 记忆语义：成功过 ⇒ 排最前；**连败到阈值 ⇒ 丢弃**（防"记错了"锁死）；人工覆盖优先级最高；
  ⑤ **红线**：只调"路线"（先试哪个），代码里**不碰**安全闸门（不动光标/不抢前台/不越版本门）。

用法：`py -3 scripts/env_profile_selftest.py`
"""
import io
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

import _srcmatch as _sm # noqa: E402
import _srcslice as _ss # noqa: E402
from agent import env_profile as EP # noqa: E402

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


#: PII 形态（⛔ 夹具/断言里一律运行时拼，免得判据文件自己命中出包 PII 闸门）
RAW_ID = re.compile("wxid" + "_" + r"[A-Za-z0-9]{6,}")
EP_SRC = src("agent/env_profile.py")

print("── A. 探测：只读、结构齐、不含隐私 ──")
_p = EP.probe(force=True)
ok("A1 快照结构齐（env 环境面 + op 操作面 + 机器指纹 + 时间）",
   isinstance(_p.get("env"), dict) and isinstance(_p.get("op"), dict)
   and bool(_p.get("machine")) and bool(_p.get("at_text")), _p.get("machine"))
ok("A2 操作面五轴都在（输入档位/前台/切会话台账/点击目标/OCR）",
   {"input_backend", "foreground", "switch_fails", "click_target", "ocr"} <= set((_p.get("op") or {})),
   "、".join(list((_p.get("op") or {}).keys())))
_mid = EP.machine_id(_p)
ok("A3 机器指纹稳定（同一次探测两次取值一致）", _mid == EP.machine_id(_p), _mid)
ok("A4 ★ 机器指纹**不含隐私**（无账号/群号/用户名/路径）",
   not RAW_ID.search(_mid) and "@chatroom" not in _mid and "\\" not in _mid and "/" not in _mid,
   _mid)
_snapshot_txt = repr(_p.get("op"))
ok("A5 操作面读数里也没有账号形态（只报档位/计数/状态）",
   not RAW_ID.search(_snapshot_txt), "")

print("\n── B. 策略：每条都能说清依据，族在白名单里 ──")
_ss2 = EP.strategies(_p)
ok("B1 有建议且每条字段齐（id/family/title/why/evidence/risk）",
   len(_ss2) >= 4 and all({"id", "family", "title", "why", "evidence", "risk"} <= set(s) for s in _ss2),
   "%d 条" % len(_ss2))
ok("B2 ★ 每条策略的族都在白名单里（红线：只有这三族能被自适应影响）",
   all(s["family"] in EP.ROUTE_FAMILIES for s in _ss2), "、".join(sorted(set(s["family"] for s in _ss2))))
ok("B3 每条策略都带**依据**（不是拍脑袋）", all(str(s.get("evidence") or "").strip() for s in _ss2))
_zoom = [s for s in _ss2 if s["id"] == "ocr_zoom_target"]
ok("B4 OCR 档位那条的依据是**本机实测值**（防有人改回文献里的 20~30）",
   bool(_zoom) and "45" in str(_zoom[0]["evidence"]), str(_zoom[0]["evidence"]) if _zoom else "")

print("\n── C. 记忆语义（夹具 PATH，绝不碰产品 data/）──")
_tmp = tempfile.mkdtemp(prefix="pm-envprofile-")
_old_path = EP.PATH
_real_aa = EP.auto_apply_enabled      # 保存原函数，测完必须还原
EP.PATH = os.path.join(_tmp, "env_profile.json")
try:
    EP.record_probe(_p)
    _st = EP._load()
    ok("C1 `record_probe` 落盘（轴 + 策略 + 机器指纹）",
       bool(_st.get("machine")) and bool(_st.get("axes")) and bool(_st.get("strategies")), "")
    EP.remember("switch_route", "search", True)
    ok("C2 记一枪成功（ok=1、带 last_ok 时间）",
       (EP.memory("switch_route").get("search") or {}).get("ok") == 1
       and (EP.memory("switch_route").get("search") or {}).get("last_ok", 0) > 0)
    _base = ["row", "search", "wheel"]
    ok("C3 ★ 反向锚：`auto_apply` **关** ⇒ 顺序原样返回（行为不变）",
       EP.route_order("switch_route", _base) == _base, str(EP.route_order("switch_route", _base)))
    EP.auto_apply_enabled = lambda: True
    ok("C4 打开后 ⇒ 成功过的路线排最前",
       EP.route_order("switch_route", _base)[0] == "search", str(EP.route_order("switch_route", _base)))
    EP.remember("switch_route", "search", False)
    EP.remember("switch_route", "search", False)
    ok("C5 ★ 连败到阈值 ⇒ 该路线**标记失效**（退回默认顺序，不再排前）",
       bool((EP.memory("switch_route").get("search") or {}).get("disabled"))
       and EP.route_order("switch_route", _base)[0] != "search",
       str(EP.route_order("switch_route", _base)))
    EP.remember("switch_route", "row", True)
    EP.set_manual("switch_route", "row", enabled=False)
    ok("C6 人工覆盖优先级最高：被禁用的路线排**最后**（不是删掉，还要兜底）",
       EP.route_order("switch_route", _base)[-1] == "row", str(EP.route_order("switch_route", _base)))
    EP.remember("not_a_family", "x", True)
    ok("C7 ★ 非白名单族一律不记（红线：只有三族能被影响）", "not_a_family" not in (EP._load().get("memory") or {}))
    EP.reset("all")
    ok("C8 `reset` 清空记忆与人工覆盖", EP.memory("switch_route") == {} and not EP.manual_state())
    # OCR 引擎顺序（真实自适应点）
    EP.remember("ocr_engine", "rapid", True)
    ok("C9 OCR 族：记着 RapidOCR 成过 ⇒ 打开后它排最前",
       EP.route_order("ocr_engine", ["winrt", "rapid"])[0] == "rapid")
    EP.auto_apply_enabled = _real_aa
    ok("C10 OCR 族：关着 ⇒ 原样（winrt 优先，与历史一致）",
       EP.route_order("ocr_engine", ["winrt", "rapid"])[0] == "winrt")
finally:
    EP.PATH = _old_path
    EP.auto_apply_enabled = _real_aa

print("\n── D. 配置与接线 ──")
_cfg = src("agent/config.py")
_ocr = src("agent/chat_ocr.py")
ok("D1 配置里有 `env_profile.auto_apply` 且**默认 False**",
   _sm.has(_cfg, '"env_profile"') and _sm.has(_cfg, '"auto_apply": False'))
ok("D2 `config.example.json` 同步了（fullcheck 那条闸门会查）",
   "env_profile" in src("config.example.json"))
ok("D3 OCR 双引擎接了自适应顺序（`_ocr_route_order` 真被用上）",
   _sm.has(_ocr, "for _name in _ocr_route_order():") and _sm.has(_ocr, "def _ocr_route_order()"))
ok("D4 OCR 顺序在 `auto_apply` 关着时**恒为 winrt 优先**（反向锚）",
   _sm.has(_ocr, "if not _ep.auto_apply_enabled():") and _sm.has(_ocr, 'return order'))

print("\n── E. 红线：只调路线，不碰约束 ──")
_ep_body = "\n".join(l for l in EP_SRC.splitlines() if not l.strip().startswith("#"))
# ⛔ 读一眼闸门状态是**允许**的（本模块要如实汇报"前台能不能借"）；禁止的是**动窗口/越版本门**。
_ep_lines = [l.strip() for l in _ep_body.splitlines() if l.strip()]
_gate_reads = [l for l in _ep_lines if "fg_allowed(" in l]
ok("E1 代码里**不碰**安全约束（无 SetForegroundWindow / SetWindowPos / version_gate / allow_real_fallback）",
   all(k not in _ep_body for k in ("SetForegroundWindow", "SetWindowPos",
                                   "import version_gate", "allow_real_fallback")), "")
ok("E1b 只**读**闸门状态（那一行是赋值取 `(ok, why)`，不是设置/改窗口）",
   len(_gate_reads) == 1 and _gate_reads[0].startswith("ok, why = "), str(_gate_reads[:1]))
ok("E2 不自己写盘路径（走 `DATA_DIR` + `persist.replace_into` 原子落盘）",
   "DATA_DIR" in EP_SRC and "persist.replace_into" in EP_SRC)
ok("E3 记忆只在白名单族（代码里钉着 `ROUTE_FAMILIES`）",
   _sm.has(EP_SRC, "if family not in ROUTE_FAMILIES") and _sm.has(EP_SRC, "ROUTE_FAMILIES = ("))
ok("E4 文件头写明「只调路线、不调约束」这条边界", "只调" in EP_SRC and "不调" in EP_SRC)

print("")
print("环境自适应判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
