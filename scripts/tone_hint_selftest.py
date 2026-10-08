# -*- coding: utf-8 -*-
"""「看脸色」判据 —— 守第 128 项（回话前先读对方语气，据此换口吻与长度）。

核心几条：
  · **三档互斥** —— 低落/忙/敷衍**不许同时成立**（否则两个相反指令一起进提示词）；
  · **只吃对方的字** —— 自己发的不能当"脸色"，触发批那几条也不能当（那是要答的，不是脸色）；
  · **判不出就不说** —— 没数据 / 普通聊天 / 读库抛错 ⇒ **空串**，⛔ 不许瞎猜一个情绪；
  · **三档的指令不同** —— 低落档必须写"别急着给建议"，忙档必须写"简短"，敷衍档必须写"别硬凑"；
  · **接线**：`prompt` 注入段必须真调它（写了没人用＝假开关）。

用法：runtime\\python\\python.exe scripts\\tone_hint_selftest.py
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
    tag = "OK  " if cond else "FAIL"
    print("  {} {}{}".format(tag, name, "  [{}]".format(detail) if detail else ""))


import _srcslice as _ss  # noqa: E402

MEM_SRC = io.open(os.path.join(ROOT, "agent", "memory.py"), encoding="utf-8").read()
PR_SRC = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
from agent import memory as M  # noqa: E402


class FakeStore:
    """顺序口径与 `ChatStore.recent()` 一致：**旧→新**。"""

    def __init__(self, msgs):
        self._m = msgs

    def recent(self, ck, limit=200):
        return self._m


class BoomStore:
    def recent(self, ck, limit=200):
        raise RuntimeError("库坏了")


def _st(text, mid="a", self_=False):
    return {"id": mid, "self": self_, "ts": 1700000000000, "sender_name": "小王", "text": text}


print("── A. 模块形态（三档并存 · 判据锚意图）──")
ok("A1 `_tone_of` 纯函数在（判档的唯一实现）", callable(getattr(M, "_tone_of", None)), "")
ok("A2 `tone_hint` 入口在（注入用的那个）", callable(getattr(M, "tone_hint", None)), "")
ok("A3 三档常量表都在（low / busy / dry 各自一套线索）",
   all(hasattr(M, n) for n in ("_TONE_LOW_HINTS", "_TONE_BUSY_HINTS", "_TONE_DRY_TEXTS")), "")
ok("A4 三档取值只可能是 low/busy/dry/空（⛔ 不许冒出第四档）",
   M._tone_of([_st("今天天气不错")]) == ""
   and all(M._tone_of([_st(t)]) in ("low", "busy", "dry", "")
           for t in ("我好难过", "我在开会", "嗯嗯", "今天天气不错")), "")
ok("A5 反向锚：`_tone_of` 必须只吃非自己的消息 —— 全是我自己的话 ⇒ 判不出",
   M._tone_of([_st("我好难过啊", self_=True)]) == "", "")

print("── B. 低落档（先共情、别急着给建议）──")
_low = M.tone_hint("g:1", FakeStore([_st("今天真的好难过"), _st("什么都不想干了", "b")]))
ok("B1 说难过 ⇒ 判低落档", "情绪低落" in _low, "")
ok("B2 低落档明写「别急着给建议」", "别急着" in _low and "建议" in _low, "")
ok("B3 低落档要求先接住情绪", "接住情绪" in _low or "共情" in _low, "")
ok("B4 低落档要求话少", "两三句以内" in _low, "")
ok("B5 低落档不许讲道理（明确写出来）", "别讲道理" in _low, "")
_low2 = M.tone_hint("g:1", FakeStore([_st("唉，最近压力好大")]))
ok("B6 「唉 / 压力」也判低落", "情绪低落" in _low2, "")

print("── C. 忙档（简短）──")
_busy = M.tone_hint("g:1", FakeStore([_st("我在开会，晚点说")]))
ok("C1 说开会/晚点说 ⇒ 判忙档", "在忙" in _busy, "")
ok("C2 忙档明写「简短」", "简短" in _busy, "")
ok("C3 忙档要求别展开、别连着发", "别展开" in _busy and "别连着发" in _busy, "")
ok("C4 忙档不许追问", "别追问" in _busy, "")

print("── D. 敷衍档（别长篇大论硬凑）──")
_dry = M.tone_hint("g:1", FakeStore([_st("哦")]))
ok("D1 只回一个「哦」⇒ 判敷衍档", "敷衍" in _dry, "")
ok("D2 敷衍档明写「别长篇大论硬凑」", "别长篇大论" in _dry and "硬凑" in _dry, "")
ok("D3 敷衍档要求就一两句", "一两句" in _dry, "")
ok("D4 敷衍档禁止追问「怎么了」（会把敷衍变成压力）", "怎么了" in _dry and "压力" in _dry, "")
_dry2 = M.tone_hint("g:1", FakeStore([_st("嗯嗯", "b")]))
ok("D5 「嗯嗯」也判敷衍", "敷衍" in _dry2, "")
# 分母守卫：干字后面若对方其实说了不少话，就**不该**判敷衍（否则"嗯"开头的话全被误判）
_not_dry = M.tone_hint("g:1", FakeStore([_st("嗯，我今天去看了那个展览，还挺有意思的，回头给你看照片")]))
ok("D6 分母守卫：干字开头但整句很长 ⇒ **不**判敷衍",
   "敷衍" not in _not_dry, "")

print("── E. 互斥与优先级（⛔ 不许两档同时成立）──")
_mix = M.tone_hint("g:1", FakeStore([_st("我在忙，但最近真的很累很难过")]))
ok("E1 低落优先于忙（同一段里两条都命中 ⇒ 只出低落档）",
   "情绪低落" in _mix and "在忙" not in _mix, "")
_mix2 = M.tone_hint("g:1", FakeStore([_st("嗯"), _st("别烦我", "b")]))
ok("E2 忙优先于敷衍", "在忙" in _mix2 and "敷衍" not in _mix2, "")
ok("E3 三档文案互不同（不是同一段的复制）",
   len({_low, _busy, _dry}) == 3, "%d 种" % len({_low, _busy, _dry}))

print("── F. 不需要就不说（判不出 ⇒ 空串，绝不瞎猜）──")
ok("F1 普通聊天（没情绪线索）⇒ 空串", M.tone_hint("g:1", FakeStore([_st("周末去看电影")])) == "", "")
ok("F2 store=None ⇒ 空串（不抛）", M.tone_hint("g:1", None) == "", "")
ok("F3 读库抛错 ⇒ 空串（不抛）", M.tone_hint("g:1", BoomStore()) == "", "")
ok("F4 空消息表 ⇒ 空串", M.tone_hint("g:1", FakeStore([])) == "", "")
ok("F5 只有我自己的消息 ⇒ 空串（自己的话不算脸色）",
   M.tone_hint("g:1", FakeStore([_st("我好难过", self_=True)])) == "", "")
# 反向锚：触发批那几条是"要答的"，不是"脸色" ⇒ 排掉之后应该判不出
_trigger_only = M.tone_hint("g:1", FakeStore([_st("我好难过啊", "t1"), _st("唉", "t2")]),
                            ["t1", "t2"])
ok("F6 反向锚：把触发批排掉后判不出 ⇒ 空串（⛔ 不许拿本轮要答的消息当脸色）",
   _trigger_only == "", "")

print("── G. 接线面（写了要真有人用）──")
_T = _ss.func_src(MEM_SRC, "tone_hint")
ok("G1 分母守卫：拿到 tone_hint 函数体（>600 字符）", len(_T) > 600, "%d 字符" % len(_T))
ok("G2 它有 MemoryStore.tone_hint 薄封装（让 ctx memory 能直接调）",
   "def tone_hint(self" in MEM_SRC, "")
_BUP = _ss.func_src(PR_SRC, "build_user_prompt")
ok("G3 build_user_prompt 真调了 tone_hint（否则能力写了没人用＝假开关）",
   "tone_hint" in _BUP, "")
ok("G4 注入时把本轮触发批的 id 传进去（排掉它 —— 否则把自己要答的话当脸色）",
   "tone_hint" in _BUP and "trigger_entries" in _BUP.split("tone_hint")[1][:400], "")
ok("G5 拿不到或没有内容 ⇒ 什么都不加（不硬凑）", "_tone" in _BUP and "if _tone:" in _BUP, "")
ok("G6 提示词里带「【对方现在的状态】」这个标题（模型才认得出这是当下状态）",
   "【对方现在的状态】" in MEM_SRC, "")

print("")
print("看脸色判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
