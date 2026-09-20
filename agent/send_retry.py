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
_DROPPED = {"n": 0}                       # 因队列满被丢掉的条数（stats 里给人看）
# 停机/暂停的话术（`agent/control.halt_reason()` 给的原文）——它们**不是失败**，
# 而是"现在不能发"：条目要**留着**，等解禁再补发（第六轮 V-R6-5）。
_HALT_WORDS = ("机器人已暂停", "机器人已停止", "已暂停", "已停止")


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
        # ⛔ 2026-09-21 修（第六轮 **V-R6-16②**）：docstring 说"原文件不动，人工可查"，
        #   但 `enqueue` 第一次 `_save` 就会 `os.replace` 把它覆盖掉。⇒ 读坏时先把坏文件
        #   **改名留证**（`.bad.<ts>`），这样"人工可查"才是真的。
        log.warning("发送重试队列读不动（%s）⇒ 按空队列起（坏文件改名留证，人工可查）", str(e)[:60])
        try:
            if os.path.exists(PATH):
                os.replace(PATH, "%s.bad.%d" % (PATH, int(time.time())))
        except Exception:
            pass
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
                # ⛔ 2026-09-21 修（第六轮 **V-R6-16③**）：原来每次都把 `next_at` 重置成 `now+15`
                #   ⇒ 反复入队会让本该到点的条目**永远到不了点**（饥饿），饿到 MAX_AGE_S 后一失败就丢。
                #   ⇒ 只允许**往早提**（min），且不许超过 `created + MAX_AGE_S`。
                _cap = float(it.get("created") or _now) + MAX_AGE_S
                it["next_at"] = min(float(it.get("next_at") or 0) or (_now + BACKOFF[0]),
                                    _now + BACKOFF[0], _cap)
                if it["next_at"] < _now:                 # 已经过点了 ⇒ 立刻可试，别再往后压
                    it["next_at"] = _now
                _save(items)
                return {"ok": True, "deduped": True, "pending": len(items)}
        if len(items) >= MAX_ITEMS:
            # ⛔ 2026-09-21 修（第六轮 **V-R6-16①**）：原来静默丢最旧（与 docstring"绝不静默"相悖）
            #   ⇒ 记一条 warning，并把丢弃次数留在 stats() 里给人看。
            _drop = len(items) - MAX_ITEMS + 1
            log.warning("重试队列已满（%d）⇒ 丢掉最旧的 %d 条（%s）",
                        MAX_ITEMS, _drop, ", ".join(str(x.get("chat_key") or "")[:12] for x in items[:_drop]))
            items = items[-MAX_ITEMS + 1:]
            _DROPPED["n"] = int(_DROPPED.get("n") or 0) + _drop
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
            "overflowDropped": int(_DROPPED.get("n") or 0),
            "items": [{"chat_key": it.get("chat_key"), "tries": int(it.get("tries") or 0),
                       "age_s": int(time.time() - float(it.get("created") or 0)),
                       "why": str(it.get("why") or "")[:80]} for it in items[:10]]}


def due(now: float = None) -> list:
    """到点该重试的条目（**不改队列**）。

    ⚠️ 2026-09-21（第六轮 **V-R6-4**）：**超龄条目不算"到点"** —— 它们只会被 `tick` 清掉并记账，
    **绝不给它们发出去的机会**（原来只看 `next_at` ⇒ 停机一夜后早上第一跳把昨天的补发进群）。
    """
    _now = float(now if now is not None else time.time())
    return [it for it in _load()
            if float(it.get("next_at") or 0) <= _now
            and (_now - float(it.get("created") or _now)) < MAX_AGE_S]


def expired(now: float = None) -> list:
    """**超龄**（超过 `MAX_AGE_S`）但还留在队列里的条目（给 `tick` 先清掉，只读）。"""
    _now = float(now if now is not None else time.time())
    return [it for it in _load() if (_now - float(it.get("created") or _now)) >= MAX_AGE_S]


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
            # ⛔ 2026-09-21 修（第六轮 **V-R6-4**）：**年龄闸在最前** —— 原来 `if ok:` 排在它前面，
            #   于是"停机一夜的超龄条目"早上照样被真发出去（"过期就丢弃并记账"的 docstring 是假的）。
            if (_now - float(it.get("created") or _now)) >= MAX_AGE_S:
                items.remove(it)
                _save(items)
                log.warning("这条已超过 %.0f 秒（%s）⇒ 过期丢弃，不再补发（最后原因：%s）",
                            MAX_AGE_S, it.get("chat_key"), str(why)[:90])
                return "expired"
            if ok:
                items.remove(it)
                _save(items)
                log.info("重试成功，已出队（%s）：%s", item_id, it.get("chat_key"))
                return "done"
            # ⛔ 2026-09-21 修（第六轮 **V-R6-4/5**）：
            #   ①**年龄闸提到最前**：原来 `if ok:` 在年龄闸之前 ⇒ 停机一夜的超龄条目早上照样被真发出去
            #     （"过期就丢弃并记账"的 docstring 是假的）。
            #   ②**停机/暂停不算失败**：那种话术原来落进"不可重试"⇒ `items.remove` **永久销毁**条目
            #     （而 config 默认 `start_paused`、队列又跨重启落盘 ⇒ 一重启就把队列烧空）。
            #     ⇒ 这类"现在不能发"只把 `next_at` 推到下一轮，**保留条目、不记 tries**。
            if (_now - float(it.get("created") or _now)) >= MAX_AGE_S:
                items.remove(it)
                _save(items)
                log.warning("这条已超过 %.0f 秒（%s）⇒ 过期丢弃，不再补发（最后原因：%s）",
                            MAX_AGE_S, it.get("chat_key"), str(why)[:90])
                return "expired"
            if any(w in str(why or "") for w in _HALT_WORDS):
                it["why"] = str(why or it.get("why") or "")[:200]
                it["next_at"] = _now + BACKOFF[0]          # 解禁后下一轮再来；**不销毁、不计次**
                _save(items)
                log.info("现在不能发（%s）⇒ 这条留着，等解禁再补发：%s",
                         str(why)[:60], it.get("chat_key"))
                return "held"
            it["tries"] = int(it.get("tries") or 0) + 1
            if not retryable(why):
                items.remove(it)
                _save(items)
                log.warning("重试失败但原因**不可重试**（%s）⇒ 出队，不再试：%s",
                            str(why)[:90], it.get("chat_key"))
                return "dropped"
            if it["tries"] >= MAX_TRIES:
                items.remove(it)
                _save(items)
                log.warning("重试 %d 次仍未成功 ⇒ 放弃这条：%s；最后原因：%s",
                            it["tries"], it.get("chat_key"), str(why)[:90])
                return "expired"
            it["why"] = str(why or it.get("why") or "")[:200]
            it["next_at"] = _now + BACKOFF[min(it["tries"], len(BACKOFF) - 1)]
            _save(items)
            log.info("第 %d 次重试仍未发出去（%s）：%s ⇒ %.0fs 后再试",
                     it["tries"], item_id, it.get("chat_key"), BACKOFF[min(it["tries"], len(BACKOFF) - 1)])
            return "again"
        return "missing"


def tick(send_fn, now: float = None, limit: int = 3, halt_fn=None) -> dict:
    """把到点的条目真的重发一次。`send_fn(chat_id, text) -> (ok, why)`（就是 `wechat.send_text`）。

    只试前 `limit` 条（一轮别把窗口操作堆满）；返回计数给人看。

    ⛔ 2026-09-21 加 `halt_fn`（第六轮 **V-R6-5**）：机器人被暂停/停止时**一条都不许试**
    （原来 tick 不看闸门 ⇒ 停机一夜后一开机就把队列烧空）。`halt_fn()` 返回非空字符串＝现在不能发。
    """
    out = {"tried": 0, "done": 0, "again": 0, "dropped": 0, "expired": 0, "held": 0}
    try:
        _halt = str(halt_fn() or "") if callable(halt_fn) else ""
    except Exception:                                             # noqa: BLE001
        _halt = ""
    if _halt:
        out["held"] = len(due(now)[:max(1, int(limit))])
        if out["held"]:
            log.info("发送重试：现在不能发（%s）⇒ 本轮一条都不试，%d 条留着等解禁", _halt[:60], out["held"])
        return out
    # ⚠️ 先把**超龄**的清掉并记账（第六轮 V-R6-4）：它们**不许**有任何被发出去的机会。
    for _it in expired(now)[:max(1, int(limit))]:
        if resolve(str(_it.get("id")), False, "超过 %.0f 秒 ⇒ 过期丢弃" % MAX_AGE_S, now=now) == "expired":
            out["expired"] += 1
    for it in due(now)[:max(1, int(limit))]:
        out["tried"] += 1
        try:
            ok, why = send_fn(str(it.get("chat_key") or "").split(":", 1)[-1], str(it.get("text") or ""))
        except Exception as e:                                   # noqa: BLE001
            ok, why = False, "%s: %s" % (type(e).__name__, str(e)[:60])
        _act = resolve(str(it.get("id")), bool(ok), str(why), now=now)
        out[{"done": "done", "again": "again", "dropped": "dropped",
             "expired": "expired", "held": "held"}.get(_act, "dropped")] += 1
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
