# -*- coding: utf-8 -*-
"""音乐推荐（第 139 项）：**按「标签 + 情绪/场景」给 2~3 首，每首一句理由**。

## 口径（决定这东西像不像样）

· **本地、离线、零 key**：本模块**不接任何需要申请密钥的商业 API**（Spotify/Apple Music 那类），
  也不下载、不转发任何音频 —— 只给「**歌名 + 艺人**」，让用户自己去放。
· **以「标签 + 情绪/场景」为主干**：`data/music_tags.jsonl` 里存「歌 → 标签（曲风/情绪/场景/年代）」，
  推荐就是**按标签匹配**。协同过滤退化成「**种子曲目 + 相似关系**」（第 ① 类里唯一可离线做的形态），
  因为真正的协同过滤要**行为数据**，我们没有。
· ⭐ **每条推荐必须带 `why`**：「没有理由的推荐看起来就像随机」。
  理由来自**命中的那个标签**（如「今天下雨」⇒ 命中 `雨天` ⇒ 说「因为今天下雨」），
  **不许编一个理由**（编的理由比没理由更糟：用户会发现它在瞎说）。
· ⛔ **库里没数据 ⇒ 如实说「我还不太熟这块」**，**绝不编歌名**。

## 为什么数据是 JSONL 而不是代码常量

标签要靠**用出来慢慢长**：用户说"这首适合雨天"⇒ 就追加一行。写在代码里等于每次都要改代码。
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger("persona-morph")

#: 一次最多给几首（提示词口径是 2~3；给多了就是"列歌单"，不是推荐）
MAX_PICKS = 3
#: 标签表条数封顶（超了丢最老的）
TAGS_MAX = 800
#: 预设标签表（**离线自带**）：情绪 / 场景 / 曲风三类。
#   ⛔ 只放"大家都懂、不用解释"的标签 —— 它要能对上用户随口说的一句话（"适合下雨天的"）。
PRESET_TAGS = {
    "雨天": ("雨天", "下雨", "阴天", "潮湿"),
    "深夜": ("深夜", "半夜", "凌晨", "失眠", "睡不着"),
    "通勤": ("通勤", "上班路上", "地铁", "赶路"),
    "工作": ("工作", "写代码", "专注", "学习", "自习"),
    "开心": ("开心", "高兴", "快乐", "嗨", "上头"),
    "难过": ("难过", "伤心", "emo", "失恋", "低落", "哭"),
    "治愈": ("治愈", "温暖", "安慰", "放松", "舒服"),
    "安静": ("安静", "轻音乐", "纯音乐", "舒缓", "慢"),
    "热闹": ("热闹", "嗨歌", "蹦迪", "聚会", "派对"),
    "怀旧": ("怀旧", "老歌", "以前", "小时候", "回忆"),
    "跑步": ("跑步", "运动", "健身", "有节奏"),
}


def _data_path() -> str:
    """标签表路径（`data/music_tags.jsonl`，与其它运行数据同处，便于一起备份/清理）。"""
    try:
        from .config import DATA_DIR
        return os.path.join(DATA_DIR, "music_tags.jsonl")
    except Exception:  # noqa: BLE001
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "data", "music_tags.jsonl")


def _load() -> list:
    """读标签表（坏行跳过，**不抛**）。"""
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
                if isinstance(r, dict) and r.get("title"):
                    rows.append(r)
    except Exception as e:  # noqa: BLE001
        log.debug("读音乐标签表失败（按空处理）：%s", e, exc_info=True)
        return []
    return rows


def _write(rows: list) -> bool:
    """整体覆写（原子替换；超过封顶丢最老的）。"""
    try:
        p = _data_path()
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        rows = rows[-TAGS_MAX:]
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, p)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("写音乐标签表失败：%s", e, exc_info=True)
        return False


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).strip().lower()


def remember(title: str, artist: str = "", tags=None) -> bool:
    """记一首歌的标签 ⇒ 成功返 `True`。

    ⛔ `title` 空 ⇒ **不记**（没歌名记什么）；`tags` 里的空项丢掉。
    ⛔ **同名同艺人 ⇒ 合并标签**（不新增一行 —— 否则同一个歌会重复出现在推荐里）。
    """
    t = str(title or "").strip()[:80]
    if not t:
        return False
    a = str(artist or "").strip()[:60]
    tg = [str(x).strip()[:20] for x in (tags or []) if str(x).strip()]
    if not tg:
        return False
    rows = _load()
    for r in rows:
        if _norm(r.get("title")) == _norm(t) and _norm(r.get("artist")) == _norm(a):
            old = [str(x) for x in (r.get("tags") or [])]
            for x in tg:
                if x not in old:
                    old.append(x)
            r["tags"] = old
            return _write(rows)
    rows.append({"title": t, "artist": a, "tags": tg})
    return _write(rows)


def _hit_tags(query: str) -> list:
    """把用户那句话映射成标签 ⇒ 命中的标签列表（**按预设表对齐**，不臆测）。"""
    q = _norm(query)
    if not q:
        return []
    out = []
    for tag, words in PRESET_TAGS.items():
        if any(_norm(w) in q for w in words):
            out.append(tag)
    # 用户直接说了标签名也算命中（如「来点怀旧的」）
    for tag in PRESET_TAGS:
        if _norm(tag) in q and tag not in out:
            out.append(tag)
    return out


def recommend(query: str = "", *, ctx: str = "", limit: int = MAX_PICKS) -> dict:
    """给一句"想要什么样的" ⇒ `{ok, picks:[{title, artist, why}], note}`。

    ⛔ **库里没数据 ⇒ `ok=False` + 如实说「我还不太熟这块」**（**绝不编歌名**）。
    ⛔ 每条 `why` 必须来自**真实命中的标签**；命不中标签 ⇒ `why` 写**别的原因**
      （如「同艺人」），**不编标签**。
    """
    rows = _load()
    if not rows:
        return {"ok": False, "picks": [],
                "note": "我还不太熟这块（本地还没存过任何歌的标签）—— 你可以先告诉我几首你常听的，"
                        "我记下来下次就能推了；⛔ 我不会凭空编歌名。"}
    hits = _hit_tags(query)
    # 也把 `ctx`（场景补充，如时段/天气）当作可命中的输入
    if ctx:
        for t in _hit_tags(ctx):
            if t not in hits:
                hits.append(t)
    scored = []
    for r in rows:
        tags = [str(x) for x in (r.get("tags") or [])]
        hit = [t for t in hits if t in tags]
        if not hit:
            continue
        scored.append((len(hit), r, hit))
    if not scored:
        if not hits:
            return {"ok": False, "picks": [],
                    "note": "没听懂想要什么样的（我这边的标签有：%s）。说个场景或心情我就好办了 —— "
                            "⛔ 但我不会随便挑几首来凑数。" % "、".join(list(PRESET_TAGS)[:8])}
        return {"ok": False, "picks": [],
                "note": "我这边的库里没有对上「%s」的歌（标签有：%s）。要的话让我记几首？"
                        "⛔ 我不会编歌名。" % ("、".join(hits), "、".join(list(PRESET_TAGS)[:8]))}
    # 命中标签多的排前面；并列时**按原序**（稳定、可复算）
    scored.sort(key=lambda x: -x[0])
    picks = []
    seen = set()
    for _n, r, hit in scored:
        key = (_norm(r.get("title")), _norm(r.get("artist")))
        if key in seen:
            continue
        seen.add(key)
        why = "因为%s" % ("、".join(hit)) if hit else ""
        picks.append({"title": str(r.get("title")), "artist": str(r.get("artist")),
                      "why": ("适合%s的时候听" % "、".join(hit)) if hit else "（暂无理由）"})
        if len(picks) >= max(1, int(limit or MAX_PICKS)):
            break
    return {"ok": True, "picks": picks, "hits": hits,
            "note": "只给「歌名 + 艺人」，你自己去放（⛔ 我不发音频文件、不转发、不绕版权）。"
                    "每条都写了**为什么推它**；给 2~3 首就够了，别一次列十首。"}


def stats() -> dict:
    """标签表概况（控制台/排查用）。"""
    rows = _load()
    tags = {}
    for r in rows:
        for t in (r.get("tags") or []):
            tags[str(t)] = tags.get(str(t), 0) + 1
    return {"songs": len(rows), "max": TAGS_MAX, "tags": tags}
