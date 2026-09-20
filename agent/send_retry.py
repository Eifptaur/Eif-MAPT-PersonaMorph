# -*- coding: utf-8 -*-
"""**发送重试队列**：把"身份判不了 ⇒ 这次不发"变成"晚点再发一次"。

背景（2026-09-21，用户原话：「现在还是会影响用户体验的，**宁可不发也不发错，但用户本身是想发的**」）
＋ 业界调研结论（TOCTOU：不要在 use 之前做 check；判不了的消息应进**失败可见的重试队列**，
而不是直接丢掉；出处：CWE-367 + SQS DLQ 模式）。

设计口径（都是硬规矩）：
  · **只收"可重试"的失败**：发送层在身份类拒发文案前统一打了 `【可重试】` 前缀
    （`wechat.send_text*` / `chat_identity_ok` / `_open_chat_guarded`）⇒ `retryable()` 只认这个前缀，
    **不做关键词猜测**（猜关键词是我们吃过大亏的"脆判据"）。
  · **有界**：每条最多 `MAX_TRIES` 次、最久 `MAX_AGE_S`（默认 10 分钟）⇒ 过期就**丢弃并记账**
    （过期 ≠ 失败：日志/统计里看得见"没发出去、原因是什么"）。
  · **退避**：15s → 30s → 60s → 120s → 240s（封顶 4 分钟）—— 等的是"两个群同一分钟都有消息"
    这种**瞬态**，不是无限重试。
  · **成功即出队**；失败仍可重试 ⇒ 留在队列；换成**别的**（不可重试）原因 ⇒ 立刻出队并记账
    （说明现场变了，重试也没用）。
  · **落盘**：`data/send_retry.json`，原子写（tmp + os.replace）+ 进程内锁；坏了就按空队列起（留痕）。
  · **绝不静默**：入队/重试/成功/过期/放弃 五类事件都走日志，并在 `stats()` 里给控制台读数。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from .config import DATA_DIR

log = logging.getLogger("persona-morph")

PATH = os.path.join(DATA_DIR, "send_retry.json")
RETRY_MARK = "【可重试】"
MAX_TRIES = 5
MAX_AGE_S = 600.0
BACKOFF = (15.0, 30.0, 60.0, 120.0, 240.0)
MAX_ITEMS = 50

_lock = threading.RLock()
_cache = None


def retryable(why) -> bool:
    """这条失败原因**值不值得晚点再试**（只认发送层打的前缀，不猜关键词）。"""
    return RETRY_MARK in str(why or "")


def _load() -> list:
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        items = d.get("items") if isinstance(d, dict) else d
        _cache = [x for x in (items or []) if isinstance(x, dict) and x.get("text")]
    except FileNotFoundError:
        _cache = []
    except Exception as e:
        log.warning("发送重试队列读不动（%s）⇒ 按空队列起（原文件不动，人工可查）", str(e)[:60])
        _cache = []
    return _cache


def _save(items: list) -> str:
    """原子写；失败**不抛**（重试只是补救手段，绝不许因为它把主流程搞崩），返回原因。"""
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"items": items, "when": time.strftime("%Y-%m-%d %H:%M:%S")},
                      fh, ensure_ascii=False, indent=1)
        os.replace(tmp, PATH)
        return ""
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:60])


def enqueue(chat_key: str, text: str, why: str = "", now: float = None) -> dict:
    """排一条待重发。同会话同文本**已在队列里** ⇒ 只更新原因，不重复排。"""
    with _lock:
        items = _load()
        _now = float(now if now is not None else time.time())
        for it in items:
            if it.get("chat_key") == chat_key and it.get("text") == text:
                it["why"] = str(why or it.get("why") or "")[:200]
                it["next_at"] = _now + BACKOFF[0]
                _save(items)
                return {"ok": True, "deduped": True, "pending": len(items)}
        if len(items) >= MAX_ITEMS:
            items = items[-MAX_ITEMS + 1:]
        _id = "r%d.%d" % (int(_now * 1000), len(items))
        items.append({"id": _id, "chat_key": chat_key, "text": text,
                      "why": str(why or "")[:200], "tries": 0, "created": _now,
                      "next_at": _now + BACKOFF[0], "last": ""})
        _why_save = _save(items)
        log.info("这条没能发出去，已排进重试队列（%s）：%s ⇒ %.0fs 后重试；原因：%s",
                 _id, chat_key, BACKOFF[0], str(why)[:90])
        if _why_save:
            log.warning("重试队列落盘失败（%s）⇒ 这条只在内存里有效（重启即丢）", _why_save)
        _cache[:] = items
        return {"ok": True, "id": _id, "pending": len(items), "saveError": _why_save}


def stats() -> dict:
    items = _load()
    return {"pending": len(items),
            "items": [{"chat_key": it.get("chat_key"), "tries": int(it.get("tries") or 0),
                       "age_s": int(time.time() - float(it.get("created") or 0)),
                       "why": str(it.get("why") or "")[:80]} for it in items[:10]]}


def due(now: float = None) -> list:
    """到点该重试的条目（**不改队列**）。"""
    _now = float(now if now is not None else time.time())
    return [it for it in _load() if float(it.get("next_at") or 0) <= _now]


def resolve(item_id: str, ok: bool, why: str = "", now: float = None) -> str:
    """记一次重试结果：成功 ⇒ 出队；仍可重试 ⇒ 退避后再排；其它原因 ⇒ 出队并记账。

    返回处理动作（`"done"` / `"again"` / `"dropped"` / `"expired"`）。
    """
    with _lock:
        items = _load()
        _now = float(now if now is not None else time.time())
        for it in list(items):
            if it.get("id") != item_id:
                continue
            it["last"] = "%s %s" % (time.strftime("%H:%M:%S"), str(why or "ok")[:120])
            if ok:
                items.remove(it)
                _save(items)
                log.info("重试成功，已出队（%s）：%s", item_id, it.get("chat_key"))
                return "done"
            it["tries"] = int(it.get("tries") or 0) + 1
            if not retryable(why):
                items.remove(it)
                _save(items)
                log.warning("重试失败但原因**不可重试**（%s）⇒ 出队，不再试：%s",
                            str(why)[:90], it.get("chat_key"))
                return "dropped"
            if it["tries"] >= MAX_TRIES or (_now - float(it.get("created") or _now)) >= MAX_AGE_S:
                items.remove(it)
                _save(items)
                log.warning("重试 %d 次仍未成功（或已超 %.0f 秒）⇒ 放弃这条：%s；最后原因：%s",
                            it["tries"], MAX_AGE_S, it.get("chat_key"), str(why)[:90])
                return "expired"
            it["why"] = str(why or it.get("why") or "")[:200]
            it["next_at"] = _now + BACKOFF[min(it["tries"], len(BACKOFF) - 1)]
            _save(items)
            log.info("第 %d 次重试仍未发出去（%s）：%s ⇒ %.0fs 后再试",
                     it["tries"], item_id, it.get("chat_key"), BACKOFF[min(it["tries"], len(BACKOFF) - 1)])
            return "again"
        return "missing"


def tick(send_fn, now: float = None, limit: int = 3) -> dict:
    """把到点的条目真的重发一次。`send_fn(chat_id, text) -> (ok, why)`（就是 `wechat.send_text`）。

    只试前 `limit` 条（一轮别把窗口操作堆满）；返回计数给人看。
    """
    out = {"tried": 0, "done": 0, "again": 0, "dropped": 0, "expired": 0}
    for it in due(now)[:max(1, int(limit))]:
        out["tried"] += 1
        try:
            ok, why = send_fn(str(it.get("chat_key") or "").split(":", 1)[-1], str(it.get("text") or ""))
        except Exception as e:                                   # noqa: BLE001
            ok, why = False, "%s: %s" % (type(e).__name__, str(e)[:60])
        _act = resolve(str(it.get("id")), bool(ok), str(why), now=now)
        out[{"done": "done", "again": "again", "dropped": "dropped",
             "expired": "expired"}.get(_act, "dropped")] += 1
    return out


def clear(reason: str = "") -> int:
    """清空（控制台/排障用）；返回清掉的条数。"""
    with _lock:
        n = len(_load())
        _cache.clear()
        _save([])
        if n:
            log.info("重试队列已清空 %d 条（%s）", n, reason or "手动")
        return n
