# -*- coding: utf-8 -*-
"""「群梗词典」判据 —— 守第 137 项。**不接真机、不出网、不写产品 `data/`**。

守四件事：
  A **模块形态**：`detect_new` / `explain` / `timeline` 三个入口都在，且都是**纯函数式**（能喂假数据）。
  B **不编**：样本不够 ⇒ `ok=False` 且**说清原因**；查不到 ⇒ **照实说**（⛔ 不许返一个编出来的释义）；
    **一条记录都没有 ⇒ 不编时间线**。
  C **省**：⭐ **查过就不出网**（命中笔记 ⇒ `cached=True` 且**不调 `search_meme`**）；
    ⛔ `first_ts` **只写一次**（被后面的写入覆盖 ⇒ "什么时候开始出现"就查不出来了）。
  D **接线**：工具表里有 `meme_dict`、执行体真调三个动作、提示词里有它。

用法：runtime\\python\\python.exe scripts\\meme_dict_selftest.py
"""
import io
import os
import sys
import tempfile
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
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


import _srcslice as _ss  # noqa: E402
from agent import meme_dict as MD  # noqa: E402

TOOLS_SRC = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
PROMPT_SRC = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()

# ⛔ 判据自己把词典文件换到**临时目录** —— 绝不碰产品 `data/`（用户真机上记的词不能被判据改写）。
_TD = tempfile.mkdtemp(prefix="md-")
_ORIG = MD._data_path
MD._data_path = lambda: os.path.join(_TD, "memes.jsonl")
NOW = time.time()

try:
    print("── A. 模块形态 ──")
    ok("A1 三个入口都在（`detect_new` / `explain` / `timeline`）",
       all(callable(getattr(MD, n, None)) for n in ("detect_new", "explain", "timeline")), "")
    ok("A2 词典文件落在临时目录（判据不碰产品 data/）", _TD in MD._data_path(), "")
    ok("A3 空词典 ⇒ `lookup` 给 `None`（不抛、不编）", MD.lookup("随便") is None, "")

    print("── B. 不编（样本不够 / 查不到 / 没记录）──")
    class _Mem0:
        def history(self, ck, limit=2000):
            return []

    _d0 = MD.detect_new("group:x", memory=_Mem0())
    ok("B1★ 历史为空 ⇒ `ok=False` 且**说清原因**（不硬凑一个梗）",
       (not _d0["ok"]) and ("太少" in _d0["note"]), _d0["note"][:40])
    ok("B2★ ⛔ **只出现在一两条消息里**不算梗（不管它在那条里重复多少遍）——"
       "那是「同一句话说很多次」，不是「大家都在说」",
       (not MD.detect_new("group:x", memory=type("M", (), {
           "history": lambda self, ck, limit=2000: (
               [{"text": "今天天气不错" * 40}] * 2 + [{"text": "唔"}] * 60)})())["ok"]),
       "")
    _e0 = MD.explain("")
    ok("B3 没给词 ⇒ `ok=False`（查不了就说查不了）", (not _e0["ok"]) and "没给词" in _e0["note"], "")
    _t0 = MD.timeline("不存在的词", memory=_Mem0(), chat_key="group:x")
    ok("B4★ 本群一条记录都没有 ⇒ `ok=False` + **明确说不编时间线**",
       (not _t0["ok"]) and ("不编" in _t0["note"]), _t0["note"][:40])
    _t1 = MD.timeline("", memory=_Mem0(), chat_key="group:x")
    ok("B5 没给词/没给群 ⇒ 查不了（不抛）", (not _t1["ok"]), "")

    print("── C. 省（查过就不出网 / first_ts 只写一次）──")
    ok("C1 记一条 ⇒ `lookup` 能查到", MD.remember("绝绝子", "一种夸人用语", source="某站") is True
       and (MD.lookup("绝绝子") or {}).get("gist") == "一种夸人用语", "")
    ok("C2 空词/空释义 ⇒ **不记**（空条目没意义还占空间）",
       MD.remember("", "x") is False and MD.remember("有词", "") is False, "")
    # ⭐ **查过就不出网**：命中笔记 ⇒ 不调 search_meme
    _called = {"n": 0}
    import agent.meme_search as _MS  # noqa: E402

    _orig_search = _ms_search = _MS.search_meme

    def _boom(*a, **kw):
        _called["n"] += 1
        raise AssertionError("命中笔记时**不该**再调 search_meme")
    _MS.search_meme = _boom
    try:
        _e1 = MD.explain("绝绝子")
    finally:
        _MS.search_meme = _orig_search
    ok("C3★ **查过就不出网**：命中笔记 ⇒ `cached=True` 且 search_meme **一次都没被调**",
       _e1["ok"] and _e1["cached"] and _called["n"] == 0, "调用 %d 次" % _called["n"])
    ok("C4★ first_ts **只写一次**（被后来的写入覆盖 ⇒ 就问不出「什么时候开始出现」）",
       (lambda: (MD.remember("绝绝子", "改过的释义", ts=NOW + 100),
                 True)[1] and
        MD.lookup("绝绝子")["first_ts"] <= NOW + 1)() is True,
       "first=%s" % str(MD.lookup("绝绝子").get("first_ts"))[:16])
    ok("C5 追加后 `last_ts` 前进、`history` 记了两次",
       (lambda r: r["last_ts"] >= NOW and len(r["history"]) >= 2)(MD.lookup("绝绝子")), "")
    ok("C6 释义**截断到 200 字**（只留结论，不存正文 —— 那才是吃空间的大头）",
       (lambda: (MD.remember("长词", "x" * 500), len(MD.lookup("长词")["gist"]))[1])() == 200, "")
    ok("C7 大小写/空白归一后**算同一个词**",
       (lambda: (MD.remember("ABC", "一"), MD.remember(" abc ", "二"),
                 MD.lookup("aBc")["gist"])[2])() == "二", "")
    ok("C8 `stats()` 给得出条数（控制台/排查用）", int(MD.stats().get("count") or 0) >= 3,
       str(MD.stats()))

    print("── D. 高频词与时间线（合成历史）──")
    class _Mem:
        def __init__(self, rows):
            self._r = rows

        def history(self, ck, limit=2000):
            return self._r

    _rows = ([{"text": "群主又在发癫", "ts": (NOW - 3600) * 1000}] * 30 +
             [{"text": "今天天气不错", "ts": NOW * 1000}] * 30)
    _d1 = MD.detect_new("group:x", memory=_Mem(_rows))
    _ws = [w["word"] for w in (_d1.get("words") or [])]
    ok("D1 高频词被挑出来（'又在发癫' 在 30 条不同消息里出现）",
       _d1["ok"] and "又在发癫" in _ws, str(_ws))
    ok("D1b★ ⛔ **被更长候选完全包含**的短词被丢掉（「今天」只是「今天天气」的前两字，"
       "与长词出现在**完全相同**的那批消息里 ⇒ 不算独立梗）；"
       "⭐ 反向锚：长词本身**确实在结果里**（不是「全都滤没了」）",
       ("今天" not in _ws) and ("今天天气" in _ws), str(_ws))
    ok("D2★ 样本够（≥40 条）才判 ⇒ 40 条以下 `ok=False`",
       (not MD.detect_new("group:x", memory=_Mem(_rows[:20]))["ok"]), "")
    ok("D3 提示词/结果里说明「**别自动去查**」"
       "（没有新词也去查＝一直出网、白烧 token）",
       "别" in str(_d1.get("note") or "") and "自动" in str(_d1.get("note") or ""),
       str(_d1.get("note"))[:30])
    _tl = MD.timeline("发癫", memory=_Mem(_rows), chat_key="group:x")
    ok("D4 时间线按天出桶、条数对得上", _tl["ok"] and sum(b["n"] for b in _tl["buckets"]) == 30,
       str(_tl.get("buckets"))[:60])
    ok("D5 时间线**只用本群历史、不出网**（函数体里没有 web_search/search_meme）",
       (lambda b: ("web_search" not in b) and ("search_meme" not in b))(
           _ss.func_src(io.open(os.path.join(ROOT, "agent", "meme_dict.py"),
                                encoding="utf-8").read(), "timeline")), "")

    print("── E. 接线 ──")
    ok("E1 工具表里有 `meme_dict`（名字对得上）", '"name": "meme_dict"' in TOOLS_SRC, "")
    _EX = _ss.func_src(TOOLS_SRC, "_exec_meme_dict")
    ok("E2 分母守卫：拿到 `_exec_meme_dict` 函数体（>300 字符）", len(_EX) > 300, "%d 字符" % len(_EX))
    ok("E3 三个动作都接上了（detect / explain / timeline）",
       all(k in _EX for k in ("detect_new", "explain", "timeline")), "")
    ok("E4★ 失败 ⇒ 走 `_err`（**照实转达**，不让模型自己编）", "_err(" in _EX, "")
    ok("E5 提示词里有 `meme_dict`（否则模型不知道有这个工具）", "meme_dict" in PROMPT_SRC, "")
    ok("E6★ 提示词写明「**别有事没事 detect/explain**」（防一直出网）",
       "别有事没事" in PROMPT_SRC, "")
    ok("E7 提示词写明「**拿不到就照实说，绝不编释义**」",
       "绝不编释义" in PROMPT_SRC, "")
finally:
    MD._data_path = _ORIG

print("")
print("群梗词典判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
