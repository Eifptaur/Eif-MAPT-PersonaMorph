# -*- coding: utf-8 -*-
"""群梗词典（第 137 项）：**新梗自动捕获 → 释义（带出处）→ 进化时间线**。

## 为什么要有它

`agent/meme_search.py` 解决的是「**去外面查一个梗**」；本模块解决的是「**我们自己这个群里，
哪些词是我们自己的梗**」—— 后者是**长在群里**的，外面查不到（只有这几个人懂）。

## 三件事

· `detect_new(chat_key)` —— 从群历史里挑出「**像是新梗**」的词（高频 + 反复出现在相近语境）。
· `explain(word)` —— 给一个词的释义：**先看笔记**（查过就不再出网），没有才去 `meme_search` 查，
                         把**带来源的原始结果**交给模型自己总结（本模块**不编释义**）。
· `timeline(word)` —— 这个词在我们群里**什么时候开始出现、热度怎么变**（只用本群历史，不出网）。

## 三条纪律

1. **不编释义**：本模块只负责"取回带来源的材料"与"记账"，**释义由调工具的模型写**。
2. **见过就记下来**：查过的词落 `data/memes.jsonl`，下次**不再出网**（省 token 也省存储 —— 只存结论，不存正文）。
3. **样本不够就说不够**：历史太短 ⇒ `detect_new` 返回空表并说清为什么，**不硬凑一个"梗"出来**
   （把普通词当梗是最坏的结果：机器人会开始"玩"一个根本不存在的梗）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

from . import persist # noqa: E402 原子换档：撞共享冲突要退避重试（见 persist.replace_into）

log = logging.getLogger("persona-morph")

#: 词表条数封顶（超了丢最老的）—— 单文件 JSONL，长期占用几十 KB 量级
MEME_MAX = 500
#: 至少要多少条历史才敢判"新梗"（太少 ⇒ 随便一个词都会显得"高频"）
MIN_SAMPLE = 40
#: 判定"新梗"时至少出现多少次
MIN_HITS = 3
#: 判定"新梗"时至少出现在**多少条不同消息**里 —— 这是"梗"与"同一句话重复"的分界线
MIN_MSGS = 3
#: 时间线最多回溯多少天
TIMELINE_DAYS = 90


def _data_path() -> str:
    """梗词典文件（`data/memes.jsonl`，与其它运行数据同处，便于一起备份/清理）。"""
    try:
        from .config import DATA_DIR
        return os.path.join(DATA_DIR, "memes.jsonl")
    except Exception:  # noqa: BLE001
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "data", "memes.jsonl")


def _meme_cfg(cfg=None) -> dict:
    """取 `meme` 段（拿不到 ⇒ 空 dict）。"""
    try:
        if cfg is None:
            from .config import get_config
            cfg = get_config()
        return dict((cfg or {}).get("meme") or {})
    except Exception:  # noqa: BLE001
        return {}


def _load() -> list:
    """读全部词条（坏行跳过，**不抛**）。"""
    rows = []
    p = _data_path()
    try:
        if not os.path.isfile(p):
            return []
        with open(p, "r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(r, dict):
                    rows.append(r)
    except Exception as e:  # noqa: BLE001
        log.debug("读梗词典失败（按空处理）：%s", e, exc_info=True)
        return []
    return rows


def _write(rows: list) -> bool:
    """整体覆写词表（**原子替换**；超过封顶丢最老的）。"""
    try:
        p = _data_path()
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        rows = rows[-MEME_MAX:]
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        persist.replace_into(tmp, p)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("写梗词典失败：%s", e, exc_info=True)
        return False


def _norm(word: str) -> str:
    """归一成查找键（去空白、全角转半角空格、小写）。"""
    return re.sub(r"\s+", "", str(word or "")).strip().lower()


def lookup(word: str):
    """按词查词条 ⇒ `{word, gist, source, first_ts, last_ts, history}`，没有 ⇒ `None`。

    ⛔ **只认完全同名**（归一后相等）：模糊匹配会把"同音的另一个梗"认成同一个。
    """
    k = _norm(word)
    if not k:
        return None
    out = None
    for r in _load():
        if _norm(r.get("word")) == k:
            out = r
    return out


def remember(word: str, gist: str, *, source: str = "", ts=None) -> bool:
    """记一条（或追加到已有词条）⇒ 成功返 `True`。

    · `gist` 截断 200 字（**只留结论**，不存正文 —— 那才是吃空间的大头）；
    · 已有词条 ⇒ 追加一次 `history` 记录（时间线用它），并**更新 `last_ts`**；
      ⛔ `first_ts` **只写一次**（新→旧的顺序一旦被后面的写入覆盖，"什么时候开始出现"就查不出来了）；
    · `word` 或 `gist` 空 ⇒ **不记**（空条目没意义还占空间）。
    """
    w = str(word or "").strip()[:40]
    g = str(gist or "").strip()[:200]
    if not w or not g:
        return False
    t = float(ts if ts is not None else time.time())
    rows = _load()
    k = _norm(w)
    for r in rows:
        if _norm(r.get("word")) == k:
            r["gist"] = g
            r["source"] = str(source or "")[:120]
            r["last_ts"] = t
            if not r.get("first_ts"):
                r["first_ts"] = t
            h = r.get("history") if isinstance(r.get("history"), list) else []
            h.append({"ts": t, "gist": g})
            r["history"] = h[-20:]      # 单个词的历史封顶（省空间）
            return _write(rows)
    rows.append({"word": w, "gist": g, "source": str(source or "")[:120],
                 "first_ts": t, "last_ts": t, "history": [{"ts": t, "gist": g}]})
    return _write(rows)


def detect_new(chat_key: str, *, store=None, memory=None, cfg=None, limit: int = 8) -> dict:
    """从本群历史里挑「**像是新梗**」的词 ⇒ `{ok, words:[{word,n,msgs,note}], note}`。

    ⛔ 样本不够 ⇒ `ok=False` + 说清原因（**不硬凑一个"梗"出来**）。
    ⭐ 判定口径（**"梗"的定义**）：一个词要算候选，必须
      ① 出现 `MIN_HITS` 次以上；② 出现在 `MIN_MSGS` 条**不同消息**里；
      ③ 不是停用词、不是"专有名词式的巧合子串"。
      ⛔ **为什么必须加 ②**：只算"总次数"的话，**同一条消息里重复一个词**（或一整句被复制 N 遍）
        就能把普通词顶到最上面 —— 那不是梗，那是同一句话说了很多次。
        实测踩过：60 条一模一样的「今天天气不错」⇒ 单靠次数会把"今天/天气"当成高频词。
      ⛔ **为什么必须加 ③**：滑窗 n-gram 会造出"今天"这种**通用词**（它只是"今天天气"的前两个字）。
        判法＝**若存在一个更长的候选词 A 包含本词、且 A 的"不同消息数"与**本词**完全相同 ⇒ 本词判为
        A 的一部分，不算独立候选**（"今天"与"今天天气"同时出现在同样那 N 条消息里 ⇒ 只留长的）。
        ⛔ 阈值取「**完全相同**」而不是"差不多"：2 字梗很常见（"发癫"就是 2 字），
          长词只是**多包了两个字的语气词**时（"又在发癫"），短词的消息数会**少于**长词，
          这时两个都留（用户想查哪个都查得到）。
    """
    c = _meme_cfg(cfg)
    if not c.get("enabled", True):
        return {"ok": False, "words": [], "note": "梗功能被配置关了（`meme.enabled=false`）"}
    rows = []
    try:
        if memory is not None:
            rows = memory.history(chat_key, limit=min(2000, max(200, int(limit) * 100))) or []
    except Exception as e:  # noqa: BLE001
        log.debug("读群历史失败：%s", e, exc_info=True)
        rows = []
    if len(rows) < MIN_SAMPLE:
        return {"ok": False, "words": [],
                "note": "这个群的历史只有 %d 条，太少（至少要 %d 条）⇒ 判不出「梗」，不硬凑"
                        % (len(rows), MIN_SAMPLE)}
    # ⭐ **复用 `memory.hot_words` 的 n-gram 口径**（2~4 字滑窗 + 停用词）——不另写一套分词。
    #   同时记 **命中该词的消息条数**（`msgs`）—— 这是"梗"与"同一句话重复"的分界线。
    cnt: dict = {}
    msgc: dict = {}
    for m in rows:
        t = str((m or {}).get("text") or (m or {}).get("content") or "")
        if not t:
            continue
        _seen = set()
        for seg in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+", t):
            _L = len(seg)
            for _n in (2, 3, 4):
                if _L < _n:
                    continue
                for _i in range(_L - _n + 1):
                    w = seg[_i:_i + _n]
                    cnt[w] = cnt.get(w, 0) + 1
                    _seen.add(w)
        for w in _seen:
            msgc[w] = msgc.get(w, 0) + 1
    try:
        from .memory import _STOP
    except Exception:  # noqa: BLE001
        _STOP = set()
    known = {_norm(r.get("word")) for r in _load()}
    # ① ② 先滤：出现次数 + **不同消息数**都要够
    cand = [w for w, n in cnt.items()
            if n >= MIN_HITS and msgc.get(w, 0) >= MIN_MSGS and w not in _STOP]
    # ③ 再滤：被更长的候选"包含"且消息数相当的 ⇒ 判为那个长词的一部分，丢掉本词
    out = []
    for w in sorted(cand, key=lambda x: (-msgc.get(x, 0), -cnt.get(x, 0), x)):
        _part = False
        for other in cand:
            if other != w and len(other) > len(w) and w in other \
                    and msgc.get(other, 0) == msgc.get(w, 0):
                _part = True
                break
        if _part:
            continue
        out.append({"word": w, "n": int(cnt[w]), "msgs": int(msgc.get(w, 0)),
                    "note": "已记过" if _norm(w) in known else "没见过这个（可能是新梗）"})
        if len(out) >= max(1, int(limit or 8)):
            break
    if not out:
        return {"ok": False, "words": [],
                "note": ("这个群里没有反复出现（≥%d 次、且出现在 ≥%d 条不同消息里）的候选词 ⇒ 不硬凑"
                         % (MIN_HITS, MIN_MSGS))}
    return {"ok": True, "words": out,
            "note": "这些是**本群高频词**；标「没见过这个」的还没查过。要不要我查一下某个词，"
                    "你说了算 —— ⛔ 别有新词就自动去查（那会一直出网）。"}


def explain(word: str, *, cfg=None) -> dict:
    """给一个词**取回带来源的材料** ⇒ `{ok, word, cached, results, note}`。

    ⛔ **先看笔记**：`lookup()` 有 ⇒ 直接给结论（`cached=True`，**不再出网**）。
    ⛔ 本模块**不写释义** —— 只把检索结果交回去，由调工具的模型自己总结。
    """
    w = str(word or "").strip()
    if not w:
        return {"ok": False, "word": "", "cached": False, "results": [],
                "note": "没给词，查不了"}
    old = lookup(w)
    if old and str(old.get("gist") or "").strip():
        return {"ok": True, "word": w, "cached": True, "results": [],
                "gist": str(old.get("gist") or ""), "source": str(old.get("source") or ""),
                "note": "这条以前查过并记下了（**不再出网**）"}
    try:
        from . import meme_search as _ms
        r = _ms.search_meme(w, cfg=cfg) or {}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "word": w, "cached": False, "results": [],
                "note": "查不了：%s" % str(e)[:80]}
    if not r.get("ok"):
        return {"ok": False, "word": w, "cached": bool(r.get("cached")), "results": [],
                "note": "没查到来源（如实转达即可，**别自己编一个释义**）：%s"
                        % str(r.get("why") or "搜索无结果")[:80]}
    return {"ok": True, "word": w, "cached": bool(r.get("cached")),
            "results": r.get("results") or [],
            "note": "这些是**检索到的原始来源**。请据它们自己总结「这是什么梗、怎么用」，"
                    "**写清来源**；不确定就说不确定。"}


def timeline(word: str, *, memory=None, chat_key: str = "", days: int = TIMELINE_DAYS) -> dict:
    """这个词在**本群**的出现时间线 ⇒ `{ok, word, buckets:[{date,n}], first, last, note}`。

    ⛔ **只用本群历史，不出网**（"我们群里什么时候开始说这个词"只有我们自己的数据知道）。
    ⛔ 一条都没有 ⇒ `ok=False` + 说清（**不编一条"2020 年就有了"**）。
    """
    w = str(word or "").strip()
    if not w or memory is None or not chat_key:
        return {"ok": False, "word": w, "buckets": [], "note": "没有词或没有群历史，查不了"}
    try:
        rows = memory.history(chat_key, limit=2000) or []
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "word": w, "buckets": [], "note": "读群历史失败：%s" % str(e)[:80]}
    cut = time.time() - max(1, int(days or TIMELINE_DAYS)) * 86400
    buckets = {}
    first = last = 0
    for m in rows:
        t = str((m or {}).get("text") or (m or {}).get("content") or "")
        if w not in t:
            continue
        try:
            ts = float((m or {}).get("ts") or 0) / 1000.0
        except (TypeError, ValueError):
            continue
        if ts < cut:
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        buckets[day] = buckets.get(day, 0) + 1
        first = min(first, ts) if first else ts
        last = max(last, ts)
    if not buckets:
        return {"ok": False, "word": w, "buckets": [],
                "note": "本群最近 %d 天的记录里没出现过「%s」⇒ 我不编时间线" % (days, w)}
    return {"ok": True, "word": w, "days": int(days),
            "buckets": [{"date": d, "n": n} for d, n in sorted(buckets.items())],
            "first": first, "last": last,
            "note": "按天统计的是**本群**历史里这个词的出现次数（只读、不出网）。"}


def stats() -> dict:
    """词典概况（控制台/排查用）：记了多少词、最早/最新。"""
    rows = _load()
    ts = [float(r.get("last_ts") or 0) for r in rows if r.get("last_ts")]
    return {"count": len(rows), "max": MEME_MAX,
            "first_ts": min(ts) if ts else 0, "last_ts": max(ts) if ts else 0}
