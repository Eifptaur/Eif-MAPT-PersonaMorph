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
