# -*- coding: utf-8 -*-
"""「辟谣解析」判据 —— 守第 132 项。**不联网、不真调模型、不写产品 `data/`**。

守四件事（照验收两条 + 三条硬规矩）：
  A 六档：`VERDICTS` 就是那六个，**互斥**；模型自创的档位 ⇒ 归「信息不足」（⛔ 不许自创一档）。
  B ⭐ **"信息不足"这一档必须有**：没有它，模型遇到查不到的东西就只会倒向一边（而倒向哪边都是编）。
  C **强制结构化**：非法 JSON ⇒ `parsed=False` + **原样带回** ⇒ `finalize` 走 `ok=False` 兜底
    （⛔ 不给默认结论）；合法 JSON ⇒ 解析出五字段；`confidence` 夹在 0~1。
  D ⭐ **每条结论强制附免责**：`finalize` 的结果**一定**带 `disclaimer`；**不问模型要不要**。
  E ⭐ **私聊共情优于当众打脸**：群里与私聊的措辞口径**不同**（`tone_of` / `build_prompt`）。
  F **接线**：工具表 / 执行体 / 提示词三处都在，且提示词写明「查不到落信息不足」「别点名打脸」。

用法：runtime\\python\\python.exe scripts\\rumor_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


import _srcslice as _ss  # noqa: E402
from agent import rumor as R  # noqa: E402

TOOLS_SRC = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
PROMPT_SRC = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
R_SRC = io.open(os.path.join(ROOT, "agent", "rumor.py"), encoding="utf-8").read()

print("── A. 六档结论 ──")
ok("A1 六档就是那六个、且顺序对（真实→…→信息不足）",
   tuple(R.VERDICTS) == ("真实", "大概率真实", "存疑", "大概率不实", "已证实谣言", "信息不足"),
   str(R.VERDICTS))
ok("A2 档位**互斥**（没有重复）", len(set(R.VERDICTS)) == len(R.VERDICTS), "")
ok("A3★ ⛔ 模型自创档位 ⇒ 归「信息不足」（**不许自创一档**）",
   R._ok_verdict("半真半假") == "信息不足" and R._ok_verdict("真的吧") == "信息不足", "")
ok("A4 六档里的每一档都能原样认出来（不被归一掉）",
   all(R._ok_verdict(x) == x for x in R.VERDICTS), "")
ok("A5 档位里的空白被容忍（「 存疑 」⇒ 存疑）", R._ok_verdict(" 存疑 ") == "存疑", "")

print("── B. 必须留「信息不足」这个出口 ──")
ok("B1★ 「信息不足」在六档里（**没有它，查不到时模型只会倒向一边**）",
   "信息不足" in R.VERDICTS, "")
ok("B2★ 指令里**明确要求**查不到必须填「信息不足」",
   "必须" in R.build_prompt("x") and "信息不足" in R.build_prompt("x"), "")
ok("B3★ 指令里写明「**不许倒向真或假任何一边**」",
   "不许倒向" in R.build_prompt("x"), "")

print("── C. 强制结构化 ──")
_good = R.parse_verdict('{"verdict": "大概率不实", "confidence": 0.8, "summary": "查无此事", '
                        '"points": ["官方没发过"], "sources": ["http://a.example"]}')
ok("C1 合法 JSON ⇒ 五字段都解出来", _good["parsed"] and _good["verdict"] == "大概率不实"
   and _good["confidence"] == 0.8 and _good["summary"] == "查无此事"
   and _good["points"] == ["官方没发过"] and _good["sources"] == ["http://a.example"], str(_good)[:80])
ok("C2 带 ```json 围栏也认（抠出第一个 JSON 对象）",
   R.parse_verdict('```json\n{"verdict": "真实", "summary": "x"}\n```')["verdict"] == "真实", "")
ok("C3 前后有解释文字也认（只抠 JSON 那段）",
   R.parse_verdict('我的判断是：{"verdict": "存疑", "summary": "y"} 就这样')["verdict"] == "存疑", "")
_bad = R.parse_verdict("这条我觉得应该是不太可信的谣言吧")
ok("C4★ 非法 JSON ⇒ `parsed=False` 且**原样带回**（走原文兜底）",
   (not _bad["parsed"]) and _bad["raw"].startswith("这条我觉得"), str(_bad["raw"])[:20])
ok("C5★ `confidence` 夹在 0~1（给了 9 ⇒ 1；给了 -3 ⇒ 0）",
   R.parse_verdict('{"verdict":"存疑","confidence":9}')["confidence"] == 1.0
   and R.parse_verdict('{"verdict":"存疑","confidence":-3}')["confidence"] == 0.0, "")
ok("C6 空输出 ⇒ 也算 `parsed=False`（不炸）", R.parse_verdict("")["parsed"] is False, "")
ok("C7 `points` / `sources` 类型不对（给了字符串）⇒ 当空表，不炸",
   R.parse_verdict('{"verdict":"存疑","points":"一条","sources":"x"}')["points"] == [], "")
_f0 = R.finalize(_bad)
ok("C8★ ⛔ 解析失败 ⇒ `finalize` 给 `ok=False`，**绝不套一个默认结论**",
   (not _f0["ok"]) and "没有下结论" in _f0["note"], _f0["note"][:40])
ok("C9 `sources` 上限 `SRC_MAX`（防一次塞十几条）",
   len(R.parse_verdict('{"verdict":"存疑","sources":%s}' % str(list(range(30))))["sources"])
   <= R.SRC_MAX, "")

print("── D. 每条结论强制附免责 ──")
_f1 = R.finalize(_good)
ok("D1★ 解析成功 ⇒ 结果**一定**带 `disclaimer`（不问模型要不要）",
   _f1["ok"] and bool(str(_f1.get("disclaimer") or "")), "")
ok("D1b★ 解析失败 ⇒ 也带 `disclaimer`（不管哪条路都得有）",
   bool(str(_f0.get("disclaimer") or "")), "")
ok("D2★ 免责里写明「**仅供参考 / 不能替代你自己去看原始信源**」",
   "仅供参考" in R.DISCLAIMER and "原始信源" in R.DISCLAIMER, "")
ok("D3★ 免责点名「健康/法律/钱」这几类要**以官方发布为准**",
   "健康" in R.DISCLAIMER and "官方" in R.DISCLAIMER, "")
ok("D4★ 一个来源都没有时**明说可信度低**（不装作有来源）",
   "一个来源都没检索到" in R.finalize(
       {"parsed": True, "verdict": "存疑", "confidence": 0.4, "summary": "",
        "points": [], "sources": []}, results=[])["note"], "")
ok("D5★ 模型没写来源但检索到了 ⇒ 把检索结果附上（**如实标「非我方结论」**）",
   (lambda o: bool(o["sources"]) and "不是我方结论" in o["note"])(
       R.finalize({"parsed": True, "verdict": "存疑", "confidence": 0.5, "summary": "",
                   "points": [], "sources": []},
                  results=[{"url": "http://b.example", "title": "t"}])), "")

print("── E. 私聊共情优于当众打脸 ──")
ok("E1★ 群里/私聊的措辞口径**不同**", R.tone_of("group") != R.tone_of("private"), "")
ok("E2★ 群里口径写明「**别当众打脸 / 别点名 / 别说对方传谣**」",
   "别当众打脸" in R.tone_of("group") and "别点名" in R.tone_of("group"), "")
ok("E3★ 群里口径给的是**替代说法**「我刚查了一下…」（把信息补上，不是指责）",
   "我刚查了一下" in R.tone_of("group"), "")
ok("E4 私聊口径可以直说「这条可能不太准」，但仍要求**别评价对方**",
   "不太准" in R.tone_of("private") and "别评价" in R.tone_of("private"), "")
ok("E5★ 拼给模型的指令里也**带着这个口径**（不只是模块里有个函数没人用）",
   "别把转发的人当众打成传谣的" in R.build_prompt("x", where="group")
   and "私聊" in R.build_prompt("x", where="private"), "")
ok("E6 指令里要求 `sources` **只写真看到过的**，没有就留空（绝不编来源）",
   "绝不编来源" in R.build_prompt("x"), "")

print("── F. 接线 ──")
ok("F1 工具表里有 `rumor_check`", '"name": "rumor_check"' in TOOLS_SRC, "")
_EX = _ss.func_src(TOOLS_SRC, "_exec_rumor_check")
ok("F2 分母守卫：拿到执行体（>300 字符）", len(_EX) > 300, "%d 字符" % len(_EX))
ok("F3 执行体调了 `rumor.check`，且**注入了 llm**（模块自己不建 HTTP 客户端）",
   "_rm.check(" in _EX and "llm=_llm" in _EX, "")
ok("F4★ 拿不到结果 ⇒ 走 `_err`（**不让模型自己下结论**）", "_err(" in _EX, "")
ok("F5 提示词里有 `rumor_check`", "rumor_check" in PROMPT_SRC, "")
ok("F6★ 提示词写明「查不到就落信息不足、绝不倒向一边」",
   "信息不足" in PROMPT_SRC and "倒向" in PROMPT_SRC, "")
ok("F7★ 提示词写明「别点名、别说对方传谣」（当众打脸那条）",
   "别点名" in PROMPT_SRC and "传谣" in PROMPT_SRC, "")
ok("F8★ 提示词写明「**别见到转发就自动去核**」（防刷屏）",
   "别见到转发就自动去核" in PROMPT_SRC, "")

print("── G. 没 llm ⇒ 不假装核查过 ──")
_r = R.check("某地说要停水三天", llm=None)
ok("G1★ 没有可用模型 ⇒ `ok=False` + **明说没核查**（不假装核过）",
   (not _r["ok"]) and "不假装" in _r["note"], _r["note"][:40])
ok("G2 但它仍把**拼好的指令**给出来（上层想看可以看）", bool(str(_r.get("prompt") or "")), "")
ok("G3 `llm` 抛异常 ⇒ `ok=False`（**没有下结论**）",
   (lambda: (not R.check("x", llm=lambda p: (_ for _ in ()).throw(RuntimeError("boom")))["ok"]))(), "")

print("")
print("辟谣解析判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
