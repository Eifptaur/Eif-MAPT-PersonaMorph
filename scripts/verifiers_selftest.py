#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""症状检验器判据（2026-09-18 立，作者口径：「用户有哪方面的问题，就点那个检验器，把报告发给我」）。

好的检验器长什么样（检索到的共识 + 本项目既有形状）：
  ① **原子化二值检查**：一条检查只问一件事，`ok` + **自带证据**，不做分数聚合；
  ② **一个总判决**（不是分数）：卡在哪一条 + 下一步做什么；
  ③ **症状驱动**：用户按自己的话术点，不需要懂内部；
  ④ **可复制报告**：一段纯文本，能直接粘进「反馈」；
  ⑤ **只读**：不动窗口、不发消息、不改配置。

本判据守六件事：
  ① 每个检验器都能跑通（离线、只读）且返回**同一形状**；
  ② 判决逻辑正确：**第一条不通过的检查＝卡点**，通过时给正向结论；
  ③ 报告文本自带"症状 / 判决 / 下一步 / 逐项证据 / 可粘进反馈"；
  ④ 未知 id、内部异常都**不抛**（返回同样形状，别把前端打崩）；
  ⑤ 接线：两个 API + 控制台面板 + 复制按钮都在；
  ⑥ **只读**：源码里不许出现发消息/点击/改配置的调用（这是"点一下不会出事"的底线）。
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import verifiers as V          # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % extra if extra else ""))


print("── A. 清单与形状 ──")
cat = V.catalog()
ok("清单非空且每项有 id/name", bool(cat) and all(x.get("id") and x.get("name") for x in cat), str(cat[:2]))
ok("覆盖作者点名的几类症状",
   {"send_blocked", "self_echo", "no_reply", "emoji_blank", "fg_disturb", "update_stuck",
    "console_dead"} <= {x["id"] for x in cat}, str([x["id"] for x in cat]))

print("── B. 每个检验器都能跑（离线、只读、同形状）──")
SHAPE = ("id", "name", "symptom", "ok", "verdict", "action", "checks", "report")
for item in cat:
    r = V.run(item["id"])
    miss = [k for k in SHAPE if k not in r]
    ok("%s：形状齐 + 有逐项检查" % item["id"],
       not miss and isinstance(r["checks"], list) and len(r["checks"]) >= 2,
       "缺 %s / checks=%d" % (miss, len(r.get("checks") or [])))
    ok("%s：每条检查都带证据文本" % item["id"],
       all(c.get("name") and c.get("detail") for c in r["checks"]))
    ok("%s：报告包含症状/判决/证据/可粘进反馈" % item["id"],
       all(x in r["report"] for x in ("症状：", "判决：", "逐项证据", "粘进「反馈」")), r["report"][:80])

print("── C. 判决逻辑：第一条不通过的检查＝卡点 ──")
_r = V.run("send_blocked")
if _r["checks"]:
    bad = [c for c in _r["checks"] if not c["ok"]]
    if bad:
        ok("有卡点时 verdict 点名第一条坏检查",
           bad[0]["name"] in _r["verdict"] and _r["ok"] is False, _r["verdict"][:90])
        ok("卡点带下一步动作", bool(_r["action"]), _r["action"][:60])
    else:
        ok("全通过时 verdict 给正向结论 + 不催动作",
           _r["ok"] is True and "正常" in _r["verdict"] or "成立" in _r["verdict"], _r["verdict"][:90])

print("── D. 异常与未知 id 都不许抛（别把前端打崩）──")
_u = V.run("不存在的东西")
ok("未知 id ⇒ 同形状 + 说清可选清单", _u["ok"] is False and "没有这个检验器" in _u["verdict"], _u["verdict"][:80])
ok("未知 id 也带 report", bool(_u.get("report")))
ok("run(None) 不抛", V.run(None)["ok"] is False)


def _boom():
    raise RuntimeError("故意炸")


_saved = V.VERIFIERS.get("send_blocked")
V.VERIFIERS["__boom"] = ("会炸的检验器", _boom)
try:
    _b = V.run("__boom")
    ok("检验器内部抛异常 ⇒ 返回同形状并说清（不抛给前端）",
       _b["ok"] is False and "检验器自己出错" in _b["verdict"], _b["verdict"][:70])
finally:
    V.VERIFIERS.pop("__boom", None)

print("── E. 接线：两个 API + 面板 + 复制按钮 ──")
_W = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
_C = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
ok("webui 有 /api/verifiers", '"/api/verifiers"' in _W)
ok("webui 有 /api/verify（带 id 查询串）", '"/api/verify"' in _W and "_vf.run(" in _W)
ok("控制台有面板容器 + 复制按钮", 'id="vfBtns"' in _C and 'id="vfCopy"' in _C and 'id="vfResult"' in _C)
ok("控制台按清单动态生成按钮", "/api/verifiers" in _C and "v.name" in _C)
ok("面板文案写明「只读检查」", "只读检查" in _C)

print("── F. 只读底线：检验器不许发消息/点击/改配置 ──")
_V = io.open(os.path.join(ROOT, "agent", "verifiers.py"), encoding="utf-8").read()
_bad = [x for x in ("send_text(", "send_image", "backend.click", "click_real", "save_config",
                    "write_config", "os.remove", "shutil.rmtree", "SetForegroundWindow",
                    "bring_to_front", "SetCursorPos") if x in _V]
ok("源码里没有会改变状态的调用（%s）" % (_bad or "无"), not _bad, str(_bad))

print("\n==== 症状检验器判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
