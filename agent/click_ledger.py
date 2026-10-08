# -*- coding: utf-8 -*-
"""**点击账本**：把"点了哪个目标、成了没有、为什么"记下来 —— 这是"点准率"的唯一事实来源。

## 为什么要记账（而不是只打日志）
日志能看，但**算不出率**：哪个图标常点不中、换主题后哪些目标变差、失败原因是"没变"还是"指纹对不上"……
这些都要**按（环境 × 目标）**聚合。没有它，后面所有学习（多模板、偏移、点后自愈）都只能靠感觉。

## 口径（三条，都有判据守着）
  ① **三态**：`ok`（效果发生了）/ `fail`（看着没发生）/ `unknown`（**判不了**）。
     **`unknown` 不进成功率分母** —— 判不了就如实说判不了，否则成功率是假的。
  ② **失败必须带原因**（"没变"/"指纹对不上"/"点击抛错"…）：只记"失败"两个字的账本没有用。
  ③ **有界 + 落盘 + 线程安全**：按 (环境, 目标) 聚合，每个目标只留最近 N 条样本；
     重启后仍在（`data/click_ledger.json`，原子写）。
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import time

from . import persist # noqa: E402 原子换档：撞共享冲突要退避重试（见 persist.replace_into）

log = logging.getLogger("persona-morph")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "click_ledger.json")
#: 每个目标最多留最近这么多条样本（有界：账本不能无限长）
MAX_SAMPLES = 40
_LOCK = threading.Lock()


def _load() -> dict:
    try:
        with io.open(PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        log.debug("点击账本读不出来（按空账起）：%s", e)
        return {}


def _save(db: dict) -> bool:
    """原子写。**必须复用 `persist.atomic_write_json`，不许自己写 `<path>.tmp`**：

    ⛔ 手写固定临时名会撞本仓棘轮——那条棘轮是对的：
       固定名在两个写者并发时会打开**同一个**临时文件，写出半截 JSON（`persist` 的注释里记着
       "并发写 200 次坏 46 个"的实测）。`persist` 那套带 `pid + 随机后缀` + fsync +
       有界退避重试 + 失败记账，直接用它的。
    """
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        return bool(persist.atomic_write_json(PATH, db, indent=1))
    except Exception as e:  # noqa: BLE001
        log.warning("点击账本落盘失败（本次不落，账还在内存里）：%s", e)
        return False


def record(env_key: str, name: str, outcome: str, why: str = "") -> dict:
    """记一条：`outcome` ∈ {`ok`,`fail`,`unknown`}。返回该目标的最新统计。"""
    oc = str(outcome or "").lower()
    if oc not in ("ok", "fail", "unknown"):
        log.warning("点击账本收到未知结果类型 %r（按 unknown 记）", outcome)
        oc = "unknown"
    with _LOCK:
        db = _load()
        b = db.get(env_key)
        if not isinstance(b, dict):
            b = {}
        rec = b.get(name)
        if not isinstance(rec, dict):
            rec = {}
        sm = list(rec.get("samples") or [])
        sm.append({"r": oc, "why": str(why or "")[:160], "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        rec["samples"] = sm[-MAX_SAMPLES:]
        rec["n"] = len(rec["samples"])
        b[name] = rec
        db[env_key] = b
        _save(db)
        return _summ(rec)


def _summ(rec: dict) -> dict:
    sm = list((rec or {}).get("samples") or [])
    ok = sum(1 for s in sm if s.get("r") == "ok")
    fail = sum(1 for s in sm if s.get("r") == "fail")
    unk = sum(1 for s in sm if s.get("r") == "unknown")
    den = ok + fail                      # ⛔ unknown 不进分母
    last_fail = next((s.get("why") for s in reversed(sm) if s.get("r") == "fail"), "")
    return {"n": len(sm), "ok": ok, "fail": fail, "unknown": unk,
            "rate": (ok / float(den)) if den else None, "last_fail": last_fail}


def stats(env_key: str = None) -> dict:
    """按目标给点准率（只按 `ok/fail` 算；`unknown` 单独列）。"""
    with _LOCK:
        db = _load()
    out = {}
    for k, b in (db or {}).items():
        if env_key and k != env_key:
            continue
        if not isinstance(b, dict):
            continue
        for nm, rec in b.items():
            out["%s|%s" % (k, nm)] = _summ(rec)
    return out


def worst(limit: int = 5, min_n: int = 3) -> list:
    """最该修的目标：样本 ≥ `min_n` 且成功率最低（`unknown` 太多的也列出来）。"""
    rows = [(k, v) for k, v in stats().items() if v.get("n", 0) >= min_n]
    rows.sort(key=lambda kv: ((kv[1].get("rate") if kv[1].get("rate") is not None else 1.0),
                              -kv[1].get("unknown", 0)))
    return rows[:max(1, int(limit))]


def clear(reason: str = "") -> int:
    with _LOCK:
        db = _load()
        n = sum(len(b) for b in db.values() if isinstance(b, dict))
        try:
            if os.path.exists(PATH):
                os.remove(PATH)
        except Exception as e:  # noqa: BLE001
            log.warning("点击账本清空失败：%s", e)
    log.info("点击账本已清空（%d 个目标）%s", n, ("：%s" % reason) if reason else "")
    return n
