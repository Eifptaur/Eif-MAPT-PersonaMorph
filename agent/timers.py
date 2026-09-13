# -*- coding: utf-8 -*-
"""计时/闹钟（第三方 v0.4 对账清单第 12 条）。

**红线（用户口径：只允许被动或显式开启）**——定时消息天然是"主动发消息"，所以这一层做了四道收紧：
1. **只能定时到"当前会话"**：工具参数里没有"群列表/多会话"这种东西，调用方上下文决定唯一目标；
2. **到点仍要过风险闸门**：发送统一走 `sender.send_text_batch()`（内容/频率/群发特征闸门都在那儿），
   另加"暂停中不发"和"会话被指令禁言期间不发"两道；
3. **数量封顶**：每会话最多 `MAX_PENDING_PER_CHAT` 条、全局最多 `MAX_PENDING_TOTAL` 条 ⇒ 批量定时＝直接被拒；
4. **时长封顶**：30 秒 ~ 7 天，越界夹断并如实说明。

状态落 `data/timers.json`（原子写）；到点发送**最多重试 MAX_ATTEMPTS 次**，仍失败就留痕放弃（不静默、也不无限重试）。
"""
from __future__ import annotations

import json
import os
import threading
import time

from .config import DATA_DIR

_lock = threading.RLock()

MIN_SECONDS = 30
MAX_SECONDS = 7 * 24 * 3600
MAX_PENDING_PER_CHAT = 3
MAX_PENDING_TOTAL = 20
MAX_ATTEMPTS = 3
HISTORY_KEEP = 20


def path() -> str:
    return os.path.join(DATA_DIR, "timers.json")


def _now_ms(now=None) -> int:
    if now is None:
        return int(time.time() * 1000)
    return int(now) if now > 1e11 else int(float(now) * 1000)


def load() -> dict:
    try:
        with open(path(), "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            d.setdefault("history", [])
            d.setdefault("next_id", 1)
            return d
    except Exception:
        pass
    return {"items": [], "history": [], "next_id": 1, "updatedAt": 0}


def _save(st: dict) -> None:
    with _lock:
        try:
            os.makedirs(os.path.dirname(path()), exist_ok=True)
            st["updatedAt"] = int(time.time() * 1000)
            tmp = path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path())
        except Exception:
            pass


def _pending(st: dict) -> list:
    return [it for it in (st.get("items") or []) if it.get("status") == "pending"]


def add(chat_key: str, note: str, seconds=None, minutes=None, by: str = "", now=None) -> dict:
    """登记一个定时提醒。返回 {ok, id, fire_at, seconds, note} 或 {ok: False, error}。"""
    text = str(note or "").strip()
    if not text:
        return {"ok": False, "error": "提醒内容不能为空"}
    text = text[:200]
    try:
        secs = int(seconds) if seconds not in (None, "") else int(minutes) * 60 if minutes not in (None, "") else 0
    except (TypeError, ValueError):
        secs = 0
    if secs <= 0:
        return {"ok": False, "error": "时间要写成秒或分钟（例如 seconds=300 或 minutes=5）"}
    clamped = False
    if secs < MIN_SECONDS:
        secs, clamped = MIN_SECONDS, True
    if secs > MAX_SECONDS:
        secs, clamped = MAX_SECONDS, True
    with _lock:
        st = load()
        pend = _pending(st)
        if len(pend) >= MAX_PENDING_TOTAL:
            return {"ok": False, "error": "全局待触发提醒已达上限 %d 条，先等它们响完或取消几条" % MAX_PENDING_TOTAL}
        mine = [it for it in pend if it.get("chat") == str(chat_key)]
        if len(mine) >= MAX_PENDING_PER_CHAT:
            return {"ok": False, "error": "本会话待触发提醒已达上限 %d 条（定时消息不许批量堆）" % MAX_PENDING_PER_CHAT}
        tid = int(st.get("next_id") or 1)
        st["next_id"] = tid + 1
        item = {"id": tid, "chat": str(chat_key), "note": text, "by": str(by or ""),
                "fire_at": _now_ms(now) + secs * 1000, "seconds": secs, "status": "pending",
                "attempts": 0, "created": _now_ms(now), "clamped": clamped, "error": ""}
        st.setdefault("items", []).append(item)
        _save(st)
    return {"ok": True, "id": tid, "fire_at": item["fire_at"], "seconds": secs,
            "note": text, "clamped": clamped, "max_per_chat": MAX_PENDING_PER_CHAT}


def list_all(chat_key: str | None = None) -> list:
    st = load()
    now_ms = _now_ms()
    out = []
    for it in _pending(st):
        if chat_key and it.get("chat") != str(chat_key):
            continue
        out.append(dict(it, left_seconds=max(0, int((int(it.get("fire_at") or 0) - now_ms) / 1000))))
    return sorted(out, key=lambda x: x.get("fire_at") or 0)


def cancel(chat_key: str, tid=None) -> bool:
    with _lock:
        st = load()
        hit = False
        for it in st.get("items") or []:
            if it.get("status") != "pending" or it.get("chat") != str(chat_key):
                continue
            if tid is None or str(it.get("id")) == str(tid):
                it["status"] = "cancelled"
                hit = True
                if tid is not None:
                    break
        if hit:
            _save(st)
    return hit


def due(now=None) -> list:
    now_ms = _now_ms(now)
    return [it for it in _pending(load()) if int(it.get("fire_at") or 0) <= now_ms]


def _finish(item: dict, status: str, error: str = "") -> None:
    with _lock:
        st = load()
        for it in st.get("items") or []:
            if str(it.get("id")) == str(item.get("id")) and it.get("chat") == item.get("chat"):
                it["status"] = status
                it["error"] = str(error or "")[:200]
                break
        st.setdefault("history", []).append({"id": item.get("id"), "chat": item.get("chat"),
                                             "note": item.get("note"), "status": status,
                                             "error": str(error or "")[:200], "at": _now_ms()})
        st["history"] = st["history"][-HISTORY_KEEP:]
        _save(st)


def run_once(send, now=None, paused: bool = False, log=None, muted=None) -> dict:
    """扫一遍到期提醒并发出去。

    · `send(chat_key, text) -> bool`：发送回调（生产环境接 `sender.send_text_batch`，即过风险闸门）；
    · `paused=True`：机器人暂停中 ⇒ 一条都不发、也不推进状态（恢复后补发）；
    · `muted(chat_key) -> bool`：会话被指令禁言期间不发（同样保留待触发）。
    """
    out = {"due": 0, "sent": 0, "failed": 0, "skipped": ""}
    if paused:
        out["skipped"] = "paused"
        return out
    items = due(now=now)
    out["due"] = len(items)
    for it in items:
        try:
            if muted and muted(it.get("chat")):
                out["skipped"] = "muted"
                continue
            text = "⏰ 提醒：%s" % it.get("note")
            ok_flag = False
            try:
                ok_flag = bool(send(it.get("chat"), text))
            except Exception as e:
                ok_flag = False
                _log(log, "warning", "定时提醒发送异常：%s", e)
            if ok_flag:
                _finish(it, "fired")
                out["sent"] += 1
                _log(log, "info", "定时提醒已发出：会话=%s 内容=「%s」", it.get("chat"), str(it.get("note"))[:40])
            else:
                attempts = int(it.get("attempts") or 0) + 1
                if attempts >= MAX_ATTEMPTS:
                    _finish(it, "failed", error="连续 %d 次发送失败（被闸门拦或发送失败）" % attempts)
                    out["failed"] += 1
                    _log(log, "warning", "定时提醒连续失败，已放弃：会话=%s", it.get("chat"))
                else:
                    with _lock:
                        st = load()
                        for x in st.get("items") or []:
                            if str(x.get("id")) == str(it.get("id")):
                                x["attempts"] = attempts
                        _save(st)
                    out["failed"] += 1
        except Exception as e:
            _log(log, "warning", "定时提醒处理异常：%s", e)
    return out


def _log(log, level, fmt, *a):
    if log is None:
        return
    try:
        fn = getattr(log, level, None)
        if callable(fn):
            fn(fmt, *a)
        elif callable(log):
            log(level, fmt, *a)
    except Exception:
        pass


def snapshot() -> dict:
    st = load()
    pend = _pending(st)
    by_chat = {}
    for it in pend:
        by_chat[it.get("chat")] = by_chat.get(it.get("chat"), 0) + 1
    return {"pending": len(pend), "by_chat": by_chat,
            "next": (sorted(pend, key=lambda x: x.get("fire_at") or 0)[0] if pend else None),
            "history": (st.get("history") or [])[-3:],
            "limits": {"per_chat": MAX_PENDING_PER_CHAT, "total": MAX_PENDING_TOTAL,
                       "min_seconds": MIN_SECONDS, "max_seconds": MAX_SECONDS}}
