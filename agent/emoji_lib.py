# -*- coding: utf-8 -*-
"""模型-程序协作表情库。

设计（对接文档第四节第6条）：
  · 模型收藏表情时，程序把「该表情在微信表情面板(爱心收藏)网格里的格序号 index、极简概述」
    一起写入 data/emoji_index.json（附总数/行数等周转信息）。
  · 模型想发表情时：程序把全部概述报给模型 → 模型返回选第几个 → 程序按 index ===>
    换算行列(row=index//5, col=index%5) ===> 滚动 → 点选发送（见 wechat.emoji_panel_send）。
  本文件只负责「概述生成 / 索引入库 / 清单 / 模型选择」，不含鼠标操作（操作在 wechat.py）。
"""
from __future__ import annotations

import json
import os
import re
from . import persist # noqa: E402 原子换档：撞共享冲突要退避重试（见 persist.replace_into）
import logging

log = logging.getLogger("persona-morph")

INDEX_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "emoji_index.json")
COLS = 5 # 微信表情面板：每行 5 列
VISIBLE = 4 # 可见完整行数


def _load() -> dict:
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return {"emojis": [], "total": 0}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    tmp = persist.tmp_for(INDEX_PATH)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    persist.replace_into(tmp, INDEX_PATH)


def refresh_meta() -> dict:
    """重建元信息：total / rows / top_bottom（供模型参考网格规模）。"""
    d = _load()
    n = len(d["emojis"])
    d["total"] = n
    d["rows"] = (n + COLS - 1) // COLS
    d["visible"] = VISIBLE
    d["cols"] = COLS
    _save(d)
    return d


# ── 批量占位登记（第 131 项）─────────────────────────────────────────
#   问题：用户**早就**在微信里收藏了一堆表情，但机器人从没登记过 index
#         ⇒ 那些表情**一张也用不了**（`pick()` 的清单是空的）。
#   做法：扫一次收藏面板 ⇒ 按网格数出有几格 ⇒ **批量占位登记**（只写格序号 + 占位概述、
#         `path` 留空）⇒ 之后**免扫描**即可用（按格序号点选发送）。
#   ⛔ **诚实开关**：占位登记的 `path` 是空的（我们没真的把图存下来）⇒
#     `is_scanned()` 为真只说明"格子在、能按序号点"，**不说明"知道每格是什么"**：
#     全无真实 `path` ⇒ 走**随机选**；一旦有真实 `path`/真实概述 ⇒ 才敢走 `pick()` 按语境选。
#     ⛔ 这条必须能被上层看见 —— **不许把"随机选"说成"按语境选"**。

#: 占位概述的前缀（用来区分"真概述"与"只是占了个位"）
PLACEHOLDER_PREFIX = "收藏表情"


def _is_placeholder(e: dict) -> bool:
    """这条是不是纯占位（没有真实图片路径、概述也是占位串）。"""
    if str(e.get("path") or "").strip():
        return False
    return str(e.get("summary") or "").startswith(PLACEHOLDER_PREFIX)


def register_scanned(count: int, *, keep_existing: bool = True) -> dict:
    """扫过收藏面板后**批量占位登记**前 `count` 格（格序号 0 起）。

    只登记**格序号 + 占位概述**（`path` 空）—— 目的是让"按序号点选发送"这条路先通，
    不必等每格的图都存下来。
    · `count <= 0` ⇒ **什么都不做**（如实返回 0，不编造"扫到了几张"）；
    · `keep_existing=True`（默认）：已有**真实** `path` 的那几条**不覆盖**（别把辛苦存下来的图冲掉）；
      纯占位的那几条**按新 count 对齐**（面板格数变了就以新扫描为准）。
    """
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        n = 0
    d = _load()
    if n <= 0:
        return {"added": 0, "kept_real": 0, "total": len(d.get("emojis") or []),
                "why": "扫到的格数为 0 ⇒ 不动索引"}
    real = [e for e in (d.get("emojis") or []) if not _is_placeholder(e)] if keep_existing else []
    old_ph = [e for e in (d.get("emojis") or []) if _is_placeholder(e)]
    ph = []
    for i in range(n):
        # 沿用旧占位里同格号的概述（若有），否则给统一占位串
        old = next((e for e in old_ph if int(e.get("index", -1)) == i), None)
        ph.append({"index": i,
                   "summary": (old or {}).get("summary") or ("%s%d" % (PLACEHOLDER_PREFIX, i + 1)),
                   "path": ""})
    d["emojis"] = real + ph
    d["total"] = len(d["emojis"])
    d["rows"] = (d["total"] + COLS - 1) // COLS
    d["visible"] = VISIBLE
    d["cols"] = COLS
    # 扫描时间戳：上层据此判断"这份索引有多旧"（不参与判定，只给读数）
    try:
        import time as _t
        d["scanned_at"] = int(_t.time())
    except Exception:  # noqa: BLE001
        pass
    _save(d)
    return {"added": len(ph), "kept_real": len(real), "total": d["total"], "why": ""}


def is_scanned() -> bool:
    """收藏面板**扫过没有**（＝索引里有没有登记过格子）。

    ⛔ 它只回答"格子在不在"，**不回答"知不知道每格是什么"**：
       想让上层知道后者，看 `has_real_paths()`。
    """
    d = _load()
    return bool(d.get("emojis"))


def has_real_paths() -> bool:
    """有没有**至少一条真实图片路径**（＝我们真见过某张图长什么样）。

    ⛔ 这条决定发送时走哪条路：**没有 ⇒ 只能随机选**（且要如实这么讲）；
       **有 ⇒ 可以走 `pick()` 按语境选**。别把两者混为一谈。
    """
    d = _load()
    return any(str(e.get("path") or "").strip() for e in (d.get("emojis") or []))


def record(summary: str, index: int, path: str = "", meta: dict | None = None) -> dict:
    """把一个已收藏表情写入索引。index=面板格序号(0 起)。同 path 覆盖更新。"""
    d = _load()
    for e in d["emojis"]:
        if path and e.get("path") == path:
            e.update({"summary": summary, "index": index})
            break
    else:
        e = {"index": index, "summary": summary, "path": path}
        if meta:
            e.update(meta)
        d["emojis"].append(e)
    d["total"] = len(d["emojis"])
    d["rows"] = (d["total"] + COLS - 1) // COLS
    d["visible"] = VISIBLE
    d["cols"] = COLS
    _save(d)
    return e


def list_summaries() -> list[dict]:
    """返回 [{index, summary, path?}]，供报给模型选择。"""
    d = _load()
    return [{"index": e.get("index"), "summary": e.get("summary", "")}
            for e in d["emojis"]]


def gen_summary(text: str = "", sender_name: str = "", max_len: int = 32) -> str:
    """生成极简概述：优先用消息文本/发送者；否则给中性占位。
    若要「模型生成」，可在此调 chat_completion 一句概述；默认本地规则，稳且免费。"""
    t = (text or "").strip()
    if t:
        return t if len(t) <= max_len else t[:max_len] + "…"
    s = (sender_name or "").strip()
    return ("%s 发的表情" % s) if s else "收藏表情"


def pick(context: str = "", summaries: list[dict] | None = None) -> int:
    """模型从概述清单里挑最合适的一个，返回其「面板格序号」(index, 0 起)。
    失败 → 返回第一个的 index；无清单 → -1。"""
    summaries = summaries if summaries is not None else list_summaries()
    if not summaries:
        return -1
    if len(summaries) == 1:
        return int(summaries[0]["index"])
    try:
        names = "；".join("%d. %s" % (i + 1, s["summary"]) for i, s in enumerate(summaries[:40]))
        from agent.llm import chat_completion
        r = chat_completion([{"role": "user", "content":
            "你是表情包选择器。下面是已收藏表情包的概述清单（序号.概述）：\n" + names +
            "\n当前语境：%s\n只输出一个整数=最合适的表情包序号（1 起）；没有合适的输出 0。"
            % (context or "(无语境)")}])
        m = re.search(r"(?<!\d)(\d{1,2})(?!\d)", (r.get("message") or {}).get("content") or "")
        if m:
            v = int(m.group(1))
            v = max(1, min(len(summaries), v))
            return int(summaries[v - 1]["index"])
        return int(summaries[0]["index"])
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return int(summaries[0]["index"])


def pick_random(seed=None) -> int:
    """**随机选**一个已登记的格序号（占位登记时唯一诚实的选法）。

    为什么要有它：占位登记只登记了"格子在"，**不知道每格是什么** ⇒ 让模型"按语境选"
    是**假精确**（它没有信息可依）。这时只能随机挑一个，并且上层要**如实**这么讲。
    无清单 ⇒ -1（不编造一个不存在的格号）。
    """
    summaries = list_summaries()
    if not summaries:
        return -1
    try:
        import random as _r
        rng = _r.Random(seed) if seed is not None else _r
        return int(rng.choice(summaries)["index"])
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return int(summaries[0]["index"])


def choose(context: str = "", *, seed=None) -> tuple:
    """发送前选格（**唯一入口**）：`(index, how)`，`how` ∈ `"context"` / `"random"` / `""`。

    · 有**真实图片路径** ⇒ 走 `pick(context)`（按语境选，`how="context"`）；
    · 只有**占位登记** ⇒ 走 `pick_random()`（`how="random"`）—— 上层**必须**据此如实措辞；
    · 没有登记 ⇒ `-1, ""`。
    ⛔ 这条分叉就是第 131 项的验收点：**「随机选」不许被说成「按语境选」**。
    """
    if not is_scanned():
        return -1, ""
    if has_real_paths():
        return pick(context), "context"
    return pick_random(seed), "random"


def self_check() -> dict:
    """可运行自检：无清单→记录两条→重建元信息→选择。"""
    _save({"emojis": []})
    record("猫祟祟", 0, "cat.png")
    record("哈哈大笑", 1, "haha.png")
    meta = refresh_meta()
    lids = list_summaries()
    assert meta["total"] == 2 and meta["rows"] == 1, meta
    assert len(lids) == 2
    # pick 无模型时回退第一个
    idx = pick("测试语境")
    assert idx in (0, 1), idx
    _save({"emojis": [], "total": 0})
    return {"ok": True, "meta": meta, "summaries": lids, "pick_hint": idx}


if __name__ == "__main__":
    print(json.dumps(self_check(), ensure_ascii=False, default=str))
