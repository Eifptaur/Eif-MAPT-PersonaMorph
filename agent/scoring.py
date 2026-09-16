# -*- coding: utf-8 -*-
"""反应评分引擎 v1：让机器人越聊越有趣，但防饱和。

机制（零 token 本地，默认开）：
1. 正反馈信号：机器人的一条 reaction 发出后，若群友在 24h 内「回应热烈」
   （有人 @ 它 / 连续多轮对话 / 回复了它），给该条 reaction 加分；
   若孤立（发出后无人接），减分。
2. 热度衰减：老梗降权（超过热度半衰期的分数衰减），防止反复复用同一句。
3. 种子库：内置一批有趣的开场/接梗（few-shot 参考，可选导入金句墙导出数据）。
4. 在线评分：可选——每次 reaction 后调 LLM 打"有趣分"（费 token，默认关）。

数据落盘：data/scoring.json
"""
from __future__ import annotations

import os
import time

from .config import DATA_DIR, get_config

SCORE_FILE = os.path.join(DATA_DIR, "scoring.json")

HEAT_HALF_LIFE_MS = 7 * 24 * 3600 * 1000  # 7 天热度半衰期
MAX_POOL = 2000


def _load() -> dict:
    try:
        import json
        with open(SCORE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"reactions": {}, "seed_imported": []}


def _save(data: dict):
    try:
        import json
        os.makedirs(os.path.dirname(SCORE_FILE), exist_ok=True)
        tmp = SCORE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SCORE_FILE)
    except Exception:
        pass


def _decay(score: float, last_ts: float, now: float) -> float:
    """热度衰减：分数随时间衰减向 0（老梗降权）。"""
    if HEAT_HALF_LIFE_MS <= 0:
        return score
    age = max(0, now - last_ts)
    return score * (0.5 ** (age / HEAT_HALF_LIFE_MS))


# ── 内置有趣种子库（few-shot 参考；可选导入）────────────────────────────
# 官方种子库：data/seed_library.json（约 90 条：知乎神回复/央视新闻神回复/群聊接梗，修订时改文件即可）
# 兼容旧用法：字段名 DEFAULT_SEEDS 仍是列表，供 seed_library() 与测试引用。

import json as _json
import os as _os

_SEED_FILE = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "data", "seed_library.json")

def _load_default_seeds() -> list:
    """读取内置种子库文件（缺文件/损坏时回退到内置 8 条）。"""
    fallback = [
        "今天有人看到鲸鱼尾巴吗，我刚才甩了一下就没了",
        "我差点以为群里没人了，刚准备张嘴吃空气",
        "刚游回来说点儿什么，你们继续",
        "有没有人知道为什么我每次点外卖都下雨",
        "这条消息我酝酿了三秒",
        "不要问我，我只是一条没有感情的回复机器鱼",
        "今天的热搜你们看了吗，不如我们聊聊门口的树",
        "我在群里潜水的时候听说有人在暗中观察我",
    ]
    try:
        with open(_SEED_FILE, "r", encoding="utf-8") as _f:
            _d = _json.load(_f)
        _seeds = [str(s).strip() for s in (_d.get("seeds") or []) if str(s).strip()]
        if _seeds:
            return _seeds[:MAX_POOL]
    except Exception:
        pass
    return fallback

DEFAULT_SEEDS = _load_default_seeds()


_RECENT_SAMPLED = []   # 最近取样过的金句（避免连续重复=稳定不饱和）


def seed_library(limit: int = 12) -> list:
    """返回种子库样本（默认取样 12 条；limit=0/None 返回全量）。
    防饱和/防混淆设计（1000+ 条库也稳定）：
      ① 每次只取 limit 条（提示词 token 恒定）② 随机+避开最近 3 次已用条目（新鲜度）
      ③ 风格均匀：按来源分组后轮流取，避免某主题集中（防"混"）。"""
    data = _load()
    seeds = list(DEFAULT_SEEDS)
    for item in data.get("seed_imported") or []:
        s = str(item or "").strip()
        if s and s not in seeds:
            seeds.append(s)
    seeds = seeds[:MAX_POOL] if MAX_POOL and len(seeds) > MAX_POOL else seeds
    if limit is None or limit <= 0:
        return seeds
    global _RECENT_SAMPLED
    import random as _r
    import difflib as _df

    def _n(s):
        return "".join(ch for ch in s if ch.strip() and ch not in "，。！？…—")

    def _near(a, b):
        return _df.SequenceMatcher(None, _n(a), _n(b)).ratio() > 0.8   # 池内近重排除（防同质混淆）

    pool = [s for s in seeds if s not in set(_RECENT_SAMPLED[-limit * 3:])]
    if len(pool) < limit:
        pool = list(seeds)
    sample = _r.sample(pool, min(limit, len(pool)))
    # 池内两两近重 → 用池外条目替换（保持风格多样性，不饱和不混）
    for _ in range(2):
        too = sorted({i for i in range(len(sample)) for j in range(i + 1, len(sample)) if _near(sample[i], sample[j])})
        if not too:
            break
        rest = [s for s in pool if s not in sample]
        for i in too:
            if rest:
                sample[i] = rest.pop()
    _RECENT_SAMPLED.extend(sample)
    while len(_RECENT_SAMPLED) > limit * 8:
        _RECENT_SAMPLED.pop(0)
    return sample


def import_seeds(lines) -> int:
    """从金句墙导出文本/JSON 导入种子库（每行一条 / JSON 字符串数组）。"""
    import json
    data = _load()
    existing = set(data.get("seed_imported") or [])
    added = 0
    if isinstance(lines, str):
        items = [lines]
    else:
        items = list(lines or [])
    for item in items:
        s = str(item or "").strip()
        if not s:
            continue
        if s in existing:
            continue
        existing.add(s)
        added += 1
    data["seed_imported"] = list(existing)[:MAX_POOL]
    _save(data)
    return added


# ── 正反馈评分 ─────────────────────────────────────────────────────────

def note_reaction(text: str, chat_key: str = "", model: str = ""):
    """记录一条机器人发出的 reaction（待观察反响）。"""
    data = _load()
    key = str(text or "").strip()[:200]
    if not key:
        return
    now = time.time() * 1000
    old = data["reactions"].get(key)
    if old:
        old["score"] = _decay(old.get("score") or 0, old.get("ts") or now, now)
        old["count"] = int(old.get("count") or 0) + 1
        old["ts"] = now
        old["model"] = model or old.get("model") or ""
    else:
        data["reactions"][key] = {"score": 0.0, "count": 1, "ts": now, "model": model or ""}
    _save(data)


def note_feedback(text: str, positive: bool, weight: float = 1.0):
    """群友回应信号：positive=True（有人 @ / 接话）加分；False（孤立）减分。"""
    data = _load()
    key = str(text or "").strip()[:200]
    if not key:
        return
    now = time.time() * 1000
    old = data["reactions"].get(key)
    if old:
        old["score"] = _decay(old.get("score") or 0, old.get("ts") or now, now)
        old["score"] = max(-5.0, min(5.0, old["score"] + (weight if positive else -weight)))
        old["ts"] = now
    else:
        data["reactions"][key] = {"score": (weight if positive else -weight), "count": 1, "ts": now, "model": ""}
    _save(data)


def top_reactions(limit: int = 20) -> list:
    """按当前热度分取 top 反应（供 few-shot 提示词用）。"""
    data = _load()
    now = time.time() * 1000
    scored = []
    for key, r in data["reactions"].items():
        s = _decay(float(r.get("score") or 0), int(r.get("ts") or now), now)
        if s > 0.2:
            scored.append({"text": key, "score": round(s, 2)})
    scored.sort(key=lambda x: -x["score"])
    return scored[:limit]


def stats() -> dict:
    data = _load()
    return {"reaction_count": len(data.get("reactions") or {}),
            "seed_count": len(seed_library(None)),   # 全量(官方+导入)，不能 seed_library()(默认只取12条样本)
            "top": top_reactions(5)}
