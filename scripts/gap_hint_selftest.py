# -*- coding: utf-8 -*-
"""「空窗延续」判据 —— 守第 127 项（隔一天以上重新聊，要自然接上次的话头）。

核心几条：
  · **判据必须锚「本轮之前」** —— 排掉本轮触发批，否则「距上一条消息」恒为 0，永远测不出空窗；
  · **阈值可关**（`memory.gap_hours <= 0` ⇒ 一个字都不注入）；
  · **不需要就不说**（没超阈值 / 只有自己的消息 / 拿不到时间 ⇒ **空串**，⛔ 不编话）；
  · **不许把间隔说破**（提示词里必须明确「别提好久不见」—— 否则等于教模型说套话）；
  · **接线**：`prompt` 注入段必须真调它，且**走 `_mem_cfg` 口径**（防读错配置层）。

用法：runtime\\python\\python.exe scripts\\gap_hint_selftest.py
"""
import io
import os
import sys
import time

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
CFG_SRC = io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
PR_SRC = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
from agent import memory as M  # noqa: E402


class FakeStore:
    def __init__(self, msgs):
        self._m = msgs

    def recent(self, ck, limit=200):
        return self._m


NOW = time.time()


def _st(hours_ago, name="小王", text="上次说的那个电影", mid="a", self_=False):
    return {"id": mid, "self": self_, "ts": (NOW - hours_ago * 3600) * 1000,
            "sender_name": name, "text": text}


print("── A. 配置面（阈值可调 · 可关）──")
ok("A1 分母守卫：拿到 agent/memory.py 正文（>50000 字符）", len(MEM_SRC) > 50000, "%d 字符" % len(MEM_SRC))
ok("A2 配置里有 gap_hours（默认 20 —— 隔夜即算、当天连续聊不算）", "gap_hours" in CFG_SRC, "")
ok("A3 注释写明了关掉的写法", "gap_hours" in CFG_SRC and "<=0" in CFG_SRC, "")
ok("A4 config.example.json 同步了这个键",
   "gap_hours" in io.open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read(), "")

print("── B. 该说的时候说（判据锚本轮之前）──")
_short = FakeStore([_st(2)])          # 2 小时前 ⇒ 不算空窗
_long = FakeStore([_st(30)])          # 30 小时前 ⇒ 算空窗
ok("B1 短间隔（2 小时前，小于阈值 20h）⇒ 空串（当天连续聊不该被当成隔了很久）",
   M.gap_hint("g:1", _short, cfg={}) == "", repr(M.gap_hint("g:1", _short, cfg={}))[:40])
_b = M.gap_hint("g:1", _long, cfg={})
ok("B2 长间隔（30 小时前，大于阈值）⇒ 给出一段提示", bool(_b), "%d 字符" % len(_b))
ok("B3 提示里带上了上次说的话（上次说的那个电影）—— 好让模型真的接得上",
   "上次说的那个电影" in _b, "")
ok("B4 提示里带上了说话人", "小王" in _b, "")
ok("B5 阈值可调：gap_hours=1 时 2 小时前也算空窗（证明阈值真在起作用）",
   M.gap_hint("g:1", _short, cfg={"memory": {"gap_hours": 1}}) != "", "")

print("── C. 判据必须锚本轮之前（排掉本轮触发批）──")
# ⚠️ 顺序口径：`ChatStore.recent()` 返回的是**旧→新**（`all_msgs[-limit:]`）⇒
#    fixture 必须按同一口径排；排错方向会得到"看似红"的假象（我第一版就排反了）。
_mix = FakeStore([_st(30, text="旧消息", mid="a"), _st(2, text="新消息", mid="x")])
ok("C1 反向锚：不排触发批 ⇒ 只看到 2 小时前那条 ⇒ 空串（这正是要防的假阴性）",
   M.gap_hint("g:1", _mix, cfg={}) == "", repr(M.gap_hint("g:1", _mix, cfg={}))[:40])
_c = M.gap_hint("g:1", _mix, ["x"], cfg={})
ok("C2 排掉触发批 ⇒ 算到 30 小时前那条 ⇒ 给出提示",
   bool(_c) and "旧消息" in _c, repr(_c)[:60])

print("── D. 不需要就一个字都不说（不编）──")
ok("D1 关掉（gap_hours=0）⇒ 空串（与没有这个功能时完全一致）",
   M.gap_hint("g:1", _long, cfg={"memory": {"gap_hours": 0}}) == "", "")
ok("D2 负值也当关（gap_hours=-1）⇒ 空串",
   M.gap_hint("g:1", _long, cfg={"memory": {"gap_hours": -1}}) == "", "")
ok("D3 话题库里只有自己的消息 ⇒ 空串（自己跟自己不算空窗）",
   M.gap_hint("g:1", FakeStore([_st(30, self_=True)]), cfg={}) == "", "")
ok("D4 空库 ⇒ 空串", M.gap_hint("g:1", FakeStore([]), cfg={}) == "", "")
ok("D5 没给 store ⇒ 空串（拿不到就不说）", M.gap_hint("g:1", None, cfg={}) == "", "")
ok("D6 store 抛错 ⇒ 空串（绝不把异常冒到回复链上）",
   M.gap_hint("g:1", type("B", (), {"recent": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("造"))})(),
              cfg={}) == "", "")
ok("D7 时间戳是 0 或缺失 ⇒ 空串（无从算起就不说）",
   M.gap_hint("g:1", FakeStore([{"id": "a", "self": False, "ts": 0,
                                 "sender_name": "小王", "text": "x"}]), cfg={}) == "", "")

print("── E. 不许把间隔说破（否则等于教模型说套话）──")
ok("E1 提示里明确写了这条纪律", "别提「好久不见」" in _b, "")
ok("E2 提示里要求自然接上次的话头（而不是从头寒暄）",
   "接上次的话头" in _b and "从头寒暄" in _b, "")
ok("E3 长间隔的说法是粗粒度的（差不多一天 / N 天 / 一两周），不报精确小时"
   "（报 31 小时会把间隔说得很精确、更像在算账）",
   ("差不多一天" in _b) and ("31 小时" not in _b), "")
_h7 = M.gap_hint("g:1", FakeStore([_st(24 * 5)]), cfg={})
ok("E4 5 天前 ⇒ 说 5 天", "5 天" in _h7, "")
_h30 = M.gap_hint("g:1", FakeStore([_st(24 * 40)]), cfg={})
ok("E5 40 天前 ⇒ 说挺长一段时间（不报具体天数）", "挺长一段时间" in _h30, "")

print("── F. 接线面（写了要真有人用）──")
_G = _ss.func_src(MEM_SRC, "gap_hint")
ok("F1 分母守卫：拿到 gap_hint 函数体（>800 字符）", len(_G) > 800, "%d 字符" % len(_G))
ok("F2 它用 _mem_cfg(cfg) 读配置 —— 调用方传的是整个配置，直接读顶层 key 永远读不到",
   "_mem_cfg(cfg)" in _G, "")
ok("F3 有 MemoryStore.gap_hint 薄封装（让 ctx memory 能直接调）",
   "def gap_hint(self" in MEM_SRC, "")
_BUP = _ss.func_src(PR_SRC, "build_user_prompt")
ok("F4 build_user_prompt 真调了 gap_hint（否则能力写了没人用＝假开关）",
   "gap_hint" in _BUP, "")
ok("F5 注入时把本轮触发批的 id 传进去（排掉它 —— 否则判据锚错）",
   "gap_hint" in _BUP and "trigger_entries" in _BUP.split("gap_hint")[1][:400], "")
ok("F6 拿不到或没有内容 ⇒ 什么都不加（不硬凑）",
   "_gap" in _BUP and "if _gap:" in _BUP, "")

print("")
print("空窗延续判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
