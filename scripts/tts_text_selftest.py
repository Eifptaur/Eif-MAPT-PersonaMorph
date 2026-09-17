#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""念之前的文本整形判据（`agent/tts_text.py`）—— 离线、纯字符串。

守的东西（用户 2026-09-17 原话：「他说的明明是"行行行"，但是变成了"行行hang行"」）：
  ① 连续同字 ≥3 ⇒ 断成「行，行，行」（这是那条 bug 的正解：切词才把中间那个念成 háng）；
  ② 用户念法表最高优先（用户永远能一票否决自动处理）；
  ③ 没标点的长句按呼吸断句，**有标点的地方不乱动**；
  ④ 表情/换行/重复标点清掉（引擎对这些要么乱念要么噎住）；
  ⑤ 这条规则接在**合成入口**上 ⇒ 所有后端（edge/sapi/http）都吃到。

用法：py -3 scripts/tts_text_selftest.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import tts_text as TT          # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(("  OK   " if cond else "  FAIL ") + name + (("   [%s]" % detail) if detail else ""))


def src(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


print("── A. 多音字/连续同字（那条 bug 的正解）──")
t, n = TT.prep("行行行")
ok("A1 行行行 ⇒ 行，行，行", t == "行，行，行", t)
ok("A2 说明里写明改了什么（不许默默改用户的话）", any("同字" in x or "断句" in x for x in n), str(n))
ok("A3 哈哈哈 ⇒ 哈，哈，哈", TT.prep("哈哈哈")[0] == "哈，哈，哈", TT.prep("哈哈哈")[0])
ok("A4 两个相同字不拆（行行 = 可以/能行的语气词）", TT.prep("行行")[0] == "行行", TT.prep("行行")[0])
ok("A5 句中的连续同字也拆：'好的好好好我这就去' 里有分隔",
   "好好好" not in TT.prep("好的好好好我这就去")[0], TT.prep("好的好好好我这就去")[0])
ok("A6 连续标点不拆（……）", TT.prep("嗯……好吧")[0].count("，") <= 1, TT.prep("嗯……好吧")[0])

print("── B. 念法表（用户最高优先）──")
ok("B1 字符串形式（一行一条）",
   TT.prep("银行", {"pronounce": "银行=银 行"})[0] == "银 行", TT.prep("银行", {"pronounce": "银行=银 行"})[0])
ok("B2 分号/竖线分隔多条也认",
   len(TT.pronounce_rules({"pronounce": "行行行=行，行，行；银行=银 行|重庆=重 庆"})) == 3,
   str(TT.pronounce_rules({"pronounce": "行行行=行，行，行；银行=银 行|重庆=重 庆"})))
ok("B3 列表形式 [[原,念]] 也认", TT.pronounce_rules({"pronounce": [["重庆", "重 庆"]]}) == [("重庆", "重 庆")])
ok("B4 字典形式也认", TT.pronounce_rules({"pronounce": {"重庆": "重 庆"}}) == [("重庆", "重 庆")])
ok("B5 长原文优先替换（免得被短串切碎）",
   [a for a, _ in TT.pronounce_rules({"pronounce": "行=AA\n行行行=BB"})][0] == "行行行")
ok("B6 念法表命中时 prep 会记一条说明",
   any("念法表" in x for x in TT.prep("银行", {"pronounce": "银行=银 行"})[1]))

print("── C. 断句 ──")
long_txt = "今天天气不错我们一起去公园走走吧顺便看看那边新开的那家花店还有没有那种白色的花"
t, n = TT.prep(long_txt)
ok("C1 没标点的长句会被断（出现停顿）", "，" in t and len(t) > len(long_txt), t[:60])
ok("C2 每一段都不超过上限+2（别把一口气做得更长）",
   all(len(s) <= TT.SPLIT_LEN + 2 for s in t.split("，")), str([len(s) for s in t.split("，")]))
ok("C3 有标点的句子不乱动", TT.prep("你好，世界。")[0] == "你好，世界。", TT.prep("你好，世界。")[0])
ok("C4 短的没标点句子也不乱加逗号", TT.prep("早上好呀")[0] == "早上好呀", TT.prep("早上好呀")[0])
ok("C5 断句优先落在虚词后（末尾那几段不该以'的/了/是'打头）",
   not any(s[:1] in "了的是在和就都也" for s in t.split("，") if s))

print("── D. 清理 ──")
ok("D1 表情去掉", TT.prep("晚安😴🌙")[0] == "晚安", TT.prep("晚安😴🌙")[0])
ok("D2 换行当停顿（不留换行给引擎）", "\n" not in TT.prep("第一句\n第二句")[0], TT.prep("第一句\n第二句")[0])
ok("D3 重复逗号折叠", TT.prep("嗯，，好的")[0] == "嗯，好的", TT.prep("嗯，，好的")[0])
ok("D4 逗号紧跟句号 ⇒ 只留句号", TT.prep("好的，。")[0] == "好的。", TT.prep("好的，。")[0])
ok("D5 空文本 ⇒ 空（不炸）", TT.prep("") == ("", []) and TT.prep(None) == ("", []))
ok("D6 纯表情 ⇒ 空（调用方会当「没内容」处理）", TT.prep("😀😀")[0] == "", TT.prep("😀😀")[0])
ok("D7 正常句子里的话一个字都不改（只加停顿）",
   "晚安，早点休息" in TT.prep("晚安，早点休息")[0], TT.prep("晚安，早点休息")[0])

print("── E. 接线（规则必须挂在合成入口上，不然只有「我调过」才生效）──")
_vm = src(os.path.join("agent", "voice_models.py"))
ok("E1 voice_models.make 里接了 tts_text.prep", "tts_text" in _vm and "_tt.prep(" in _vm)
ok("E2 整形结果随 info 回传（日志/产物里能看出改了啥）",
   "tts_text_notes" in _vm and "tts_text" in _vm)
ok("E3 整形失败不连坐（按原文念，不因为整形挂掉就不发声）",
   "按原文念" in _vm)
_cfg = src(os.path.join("agent", "config.py"))
ok("E4 配置里有 voice_reply.pronounce（用户能填例外）", '"pronounce"' in _cfg)

print("\n== 语音文本整形判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
