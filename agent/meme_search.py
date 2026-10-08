# -*- coding: utf-8 -*-
"""梗搜索与分析：让模型主动去查「近期热门梗」。

『哏周报』的职能」。

「出网是被选中才发生」（见 `agent/bilibili.py` 模块头第 3 条）——搜索这件事只在模型判断
"这次该聊梗"时才发生，不做常驻抓取（既省 token，也不给人"这程序一直在偷偷联网"的印象）。

**复用不重造**：检索走 `agent/web_search.py::web_search()`（多引擎、已带回包上限与墙钟预算），
本模块**不自己写 HTTP**。梗释义/用法/时效性由**调工具的模型自己总结**——本模块只负责
"把带来源的检索结果取回来"，**绝不在这里假装懂梗、绝不编释义**。

**成本护栏**：同一话题的检索结果**加缓存**（`_CACHE`，TTL 见 `CACHE_TTL_S`），
"""
from __future__ import annotations

import re
import threading
import time

#: 缓存存活时长（秒）。梗的时效性以天计 ⇒ 6 小时足够"同一场对话里不重复出网"，
#: 又不会陈到把昨天的梗当今天的。取小一点（宁可多搜一次，也别给用户过时信息）。
CACHE_TTL_S = 6 * 3600
#: 缓存容量上限（防止话题多了内存无界增长；超了清空最老的）
CACHE_MAX = 64

_CACHE: dict = {}
_LOCK = threading.RLock()

#: 检索时用的查询词模板（**多打几路**：单一路径容易被单一站点占满，覆盖不到新梗）
QUERY_TEMPLATES = (
    "近期网络热梗 最新",
    "梗指南 最新 网络热梗",
    "本周 网络流行语 热梗",
)

#: 已知话题时**额外打的两路"专门站"查询**：梗站是按梗组织的，比泛搜索准得多 ——
#  泛搜索常把同名的**人／事／商品**排在前面，专门站直接给"这个梗是什么、怎么来的"。
#  ⛔ 只在**给了话题**时用（拿它当泛查询会搜不到"最近有什么梗"）。
TOPIC_SITE_QUERIES = (
    'site:zh.moegirl.org.cn "{t}" 梗',
    '"{t}" 是什么意思 梗 出处',
)


def _norm_topic(topic: str) -> str:
    """把话题归一成缓存键（去空白/标点、限长）。空串 ⇒ 用固定键（＝"最新热梗"泛查询）。"""
    t = re.sub(r"[\s，。,.!！?？、；;：:'\"“”‘’]+", "", str(topic or ""))
    return t[:60] or "__latest__"


def _ttl_s() -> float:
    """缓存存活时长：优先读 `meme.cache_ttl_s`，配置缺失/坏 ⇒ 用模块常量兜底。"""
    try:
        v = (_cfg().get("cache_ttl_s"))
        v = float(v)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    return float(CACHE_TTL_S)


def _cache_get(key: str):
    with _LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        ts, val = item
        if (time.time() - float(ts)) > _ttl_s():
            _CACHE.pop(key, None)
            return None
        return val


def _cache_put(key: str, val) -> None:
    with _LOCK:
        if len(_CACHE) >= CACHE_MAX:
            # 清掉最老的一批（简单策略：按时间戳排序丢前 1/4）
            old = sorted(_CACHE.items(), key=lambda kv: kv[1][0])[:max(1, CACHE_MAX // 4)]
            for k, _v in old:
                _CACHE.pop(k, None)
        _CACHE[key] = (time.time(), val)


def clear_cache() -> None:
    """清空缓存（控制台/自检用；也给"我想让它现在就重新查"留个口子）。"""
    with _LOCK:
        _CACHE.clear()


def _cfg() -> dict:
    """读 `meme` 段（读不到就用模块常量兜底 —— 配置坏了不代表这功能该瘫掉）。"""
    try:
        from .config import get_config
        return (get_config() or {}).get("meme") or {}
    except Exception:
        return {}


def search_meme(topic: str = "", max_results: int = 5, use_cache: bool = True) -> dict:
    """检索「近期热门梗」⇒ `{"ok", "topic", "results":[{title,url,snippet}], "cached", "why"}`。

    · `topic` 空 ⇒ 查「近期最火的是什么」（泛查询）；给了话题 ⇒ 查该话题相关的梗的来龙去脉。
    · **失败如实报**（`ok=False` + `why`），**绝不返回编造的梗**、绝不假装搜过。
    · 命中缓存时 `cached=True`（省一次出网）。
    """
    from .web_search import web_search
    c = _cfg()
    try:
        cap = max(1, min(10, int(c.get("max_results") or max_results or 5)))
    except (TypeError, ValueError):
        cap = 5
    if max_results:
        try:
            cap = min(cap, max(1, min(10, int(max_results))))
        except (TypeError, ValueError):
            pass
    topic = str(topic or "").strip()
    key = _norm_topic(topic)
    # ⭐ **以前查过就直接给**：口径是"**见过一次就记下来**" ⇒
    #    同名梗**不再重复出网**（既省 token 也省时间）。
    #    ⛔ 只认**完全同名**（`recall_meme` 内部已归一大小写/空白）；**没记过就照常出网**。
    if topic:
        try:
            _old = recall_meme(topic)
            if _old:
                return {"ok": True, "topic": topic, "cached": True, "from_note": True,
                        "note": "这条以前查过并记下了：%s" % (_old.get("gist") or ""),
                        "results": []}
        except Exception: # noqa: BLE001
            pass
    if use_cache:
        hit = _cache_get(key)
        if hit is not None:
            out = dict(hit)
            out["cached"] = True
            return out
    # 查询词：有话题 ⇒ 直接问这个话题；无话题 ⇒ 用模板轮询几路（合并去重）
    if topic:
        queries = ["%s 是什么梗" % topic]
    else:
        queries = list(QUERY_TEMPLATES)
        # ⭐ 给了话题 ⇒ **先打"专门站"那两路**（梗站按梗组织，比泛搜索准得多），再补一路泛查询兜底。
        #    ⛔ 泛查询（没给话题）**不加** `site:` —— 那会搜不到"最近有什么梗"。
        _t = str(topic or "").strip()
        if _t:
            try:
                queries = [q.format(t=_t) for q in TOPIC_SITE_QUERIES] + list(QUERY_TEMPLATES[:1])
            except Exception: # noqa: BLE001
                queries = list(QUERY_TEMPLATES)
    results: list = []
    seen = set()
    errs: list = []
    for q in queries:
        try:
            r = web_search(q) or {}
        except Exception as e:
            errs.append("%s：%s" % (q, str(e)[:60]))
            continue
        for item in (r.get("results") or []):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            dedup = url or title
            if dedup in seen:
                continue
            seen.add(dedup)
            results.append({"title": title[:120], "url": url,
                            "snippet": str(item.get("snippet") or "").strip()[:400],
                            "from_query": q})
            if len(results) >= cap:
                break
        if len(results) >= cap:
            break
    out = {
        "ok": bool(results),
        "topic": topic,
        "results": results[:cap],
        "cached": False,
        "why": "" if results else ("搜索没返回结果" + ("；%s" % "；".join(errs) if errs else "")),
        "note": "这些是**检索到的原始来源**（标题/链接/摘要）。请据它们总结「这是什么梗、怎么用、"
                "现在还流行吗」，并写清来源；**不确定的就说不确定，别编释义**。",
    }
    if results and use_cache:
        _cache_put(key, out)
    return out


# ── 已验证梗的**长期笔记**──────────────────────────────
#   口径：**见到过一次就要自己记下来**，但**尽量省着点、保护用户的存储空间**。
#   ⇒ 只落**结论**（这个梗是什么、什么时候记的、来源标题一行），
#     **不落正文/全文/链接内容**（那才是吃空间的大头）；
#     单文件 JSONL、**条数封顶**（超了丢最老的）⇒ 长期占用几十 KB 量级，可忽略。
MEME_NOTE_MAX = 400


def _note_path() -> str:
    """笔记落盘路径（`data/` 下，与其它运行数据同处，便于一起备份/清理）。"""
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    return _os.path.join(root, "data", "meme_notes.jsonl")


def note_meme(topic: str, gist: str, *, source: str = "") -> bool:
    """把"查过的梗"记一行：`{ts, topic, gist, source}`。成功返 `True`。

    ⛔ 三条边界：① `topic` 或 `gist` 为空 ⇒ **不记**（空条目没意义还占空间）；
      ② `gist` **截断到 200 字**（只留结论）；③ 条数超过 `MEME_NOTE_MAX` ⇒ **丢最老的**。
    """
    import json as _json
    import os as _os
    import time as _t
    _tp, _g = str(topic or "").strip()[:60], str(gist or "").strip()[:200]
    if not _tp or not _g:
        return False
    p = _note_path()
    try:
        _os.makedirs(_os.path.dirname(p), exist_ok=True)
        rec = {"ts": round(_t.time(), 1), "topic": _tp, "gist": _g,
               "source": str(source or "")[:120]}
        rows = []
        if _os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as fh:
                rows = [ln for ln in fh.read().splitlines() if ln.strip()]
        rows.append(_json.dumps(rec, ensure_ascii=False))
        if len(rows) > MEME_NOTE_MAX:                 # 封顶：丢最老的（省空间）
            rows = rows[-MEME_NOTE_MAX:]
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("\n".join(rows) + "\n")
        return True
    except Exception: # noqa: BLE001
        return False


def recall_meme(topic: str):
    """查这条梗**以前记过没有** ⇒ 记过返回 `{ts, topic, gist, source}`，没有返回 `None`。

    用途：**以前查过的就不必再出网**（既省 token 也省时间）。
    ⛔ 只认**完全同名**（大小写/空白归一后相等），不做模糊匹配 —— 模糊匹配会把"同音的另一个梗"认成同一个。
    """
    import json as _json
    import os as _os
    _tp = str(topic or "").strip().lower()
    if not _tp:
        return None
    p = _note_path()
    try:
        if not _os.path.isfile(p):
            return None
        last = None
        with open(p, "r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = _json.loads(ln)
                except Exception: # noqa: BLE001
                    continue
                if str(r.get("topic") or "").strip().lower() == _tp:
                    last = r
        return last
    except Exception: # noqa: BLE001
        return None
