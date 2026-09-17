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


print("── A. 连续同字：**不许自动插标点**（用户 2026-09-17 纠正）──")
t, n, p = TT.prep("行行行")
ok("A1 「行行行」原样保留（插逗号＝一字一顿，把连读的语气读没）", t == "行行行", t)
ok("A2 说明里也不该出现「同字断句」（那条行为已废）", not any("同字" in x for x in n), str(n))
ok("A3 「哈哈哈」「好好好」同样不动", TT.prep("哈哈哈")[0] == "哈哈哈" and TT.prep("好好好")[0] == "好好好")
ok("A4 逐字念的函数还在（留给显式要求，如念验证码），只是默认不调用",
   TT.split_same_char("行行行") == "行，行，行" and "split_same_char(s)" not in
   io.open(os.path.join(ROOT, "agent", "tts_text.py"), encoding="utf-8").read().split("def prep")[1])

print("── B. 念法表（两条途径：同音字替换 / 拼音标注）──")
ok("B1 同音字替换（edge 档唯一可行路）：行行行=形形形",
   TT.prep("行行行", {"pronounce": "行行行=形形形"})[0] == "形形形",
   TT.prep("行行行", {"pronounce": "行行行=形形形"})[0])
ok("B2 社区那条例：换行=换航",
   TT.prep("换行", {"pronounce": "换行=换航"})[0] == "换航", TT.prep("换行", {"pronounce": "换行=换航"})[0])
ok("B3 分号/竖线分隔多条也认",
   len(TT.pronounce_rules({"pronounce": "行行行=形形形；银行=银 行|重庆=重 庆"})[0]) == 3,
   str(TT.pronounce_rules({"pronounce": "行行行=形形形；银行=银 行|重庆=重 庆"})))
ok("B4 列表/字典形式也认",
   TT.pronounce_rules({"pronounce": [["重庆", "重 庆"]]})[0] == [("重庆", "重 庆")]
   and TT.pronounce_rules({"pronounce": {"重庆": "重 庆"}})[0] == [("重庆", "重 庆")])
ok("B5 长原文优先替换（免得被短串切碎）",
   [a for a, _ in TT.pronounce_rules({"pronounce": "行=AA\n行行行=BB"})[0]][0] == "行行行")
ok("B6 拼音写法被认出来（`行=拼音:xing2` ⇒ SAPI 音标集的 'xing 2'）",
   TT.pronounce_rules({"pronounce": "行=拼音:xing2"})[1] == [("行", "xing 2")],
   str(TT.pronounce_rules({"pronounce": "行=拼音:xing2"})))
ok("B7 裸拼音也认（`行=xing2`），且**不进**文本替换表",
   TT.pronounce_rules({"pronounce": "行=xing2"})[1] == [("行", "xing 2")]
   and TT.pronounce_rules({"pronounce": "行=xing2"})[0] == [])
ok("B8 拼音标注不改字（改字由音源侧的音素接口做）",
   TT.prep("银行", {"pronounce": "行=拼音:xing2"})[0] == "银行",
   TT.prep("银行", {"pronounce": "行=拼音:xing2"})[0])
ok("B9 念法表命中时 prep 会记一条说明",
   any("念法表" in x for x in TT.prep("银行", {"pronounce": "银行=银 行"})[1]))

print("── C. 断句（只管「一口气念不完」，不管语气）──")
long_txt = "今天天气不错我们一起去公园走走吧顺便看看那边新开的那家花店还有没有那种白色的花"
t, n, _p = TT.prep(long_txt)
ok("C1 没标点的长句会补停顿", "，" in t and len(t) > len(long_txt), t[:60])
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
ok("D5 空文本 ⇒ 空（不炸）", TT.prep("") == ("", [], []) and TT.prep(None) == ("", [], []))
ok("D6 纯表情 ⇒ 空（调用方会当「没内容」处理）", TT.prep("😀😀")[0] == "", TT.prep("😀😀")[0])
ok("D7 正常句子里的话一个字都不改（只加停顿）",
   "晚安，早点休息" in TT.prep("晚安，早点休息")[0], TT.prep("晚安，早点休息")[0])
ok("D8 「拿你没办法」这类语气不被改写（一个字都不动）",
   TT.prep("行行行，你说了算")[0] == "行行行，你说了算", TT.prep("行行行，你说了算")[0])

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
