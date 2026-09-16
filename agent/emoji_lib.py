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

INDEX_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "emoji_index.json")
COLS = 5            # 微信表情面板：每行 5 列
VISIBLE = 4         # 可见完整行数


def _load() -> dict:
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"emojis": [], "total": 0}


def _save(d: dict) -> None:
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INDEX_PATH)


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
    except Exception:
        return int(summaries[0]["index"])


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
