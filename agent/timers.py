# -*- coding: utf-8 -*-
"""计时/闹钟（第三方 v0.4 对账清单第 12 条）。

**红线（用户口径：只允许被动或显式开启）**——定时消息天然是"主动发消息"，所以这一层做了四道收紧：
1. **只能定时到"当前会话"**：工具参数里没有"群列表/多会话"这种东西，调用方上下文决定唯一目标；
2. **到点仍要过风险闸门**：发送统一走 `sender.send_text_batch()`（内容/频率/群发特征闸门都在那儿），
   另加"暂停中不发"和"会话被指令禁言期间不发"两道；
3. **数量封顶**：每会话最多 `MAX_PENDING_PER_CHAT` 条、全局最多 `MAX_PENDING_TOTAL` 条 ⇒ 批量定时＝直接被拒；
4. **时长封顶**：30 秒 ~ 7 天，越界夹断并如实说明。

状态落 `data/timers.json`（原子写，**写失败会如实回 ok:False**）；到点发送**最多重试 MAX_ATTEMPTS 次**，
每次失败按 `RETRY_BACKOFF_SECONDS × 次数` 秒退避，仍失败就留痕放弃（不静默、也不无限重试）。
"""
from __future__ import annotations

import logging
import os
import threading
import time

from . import persist
from .config import DATA_DIR

log = logging.getLogger("persona-morph")

_lock = threading.RLock()

MIN_SECONDS = 30
MAX_SECONDS = 7 * 24 * 3600
MAX_PENDING_PER_CHAT = 3
MAX_PENDING_TOTAL = 20
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 60        # 第 N 次重试的等待＝该秒数 × N（V10：以前一失败就立刻重排，1 分钟用光 3 次）
HISTORY_KEEP = 20


def path() -> str:
    return os.path.join(DATA_DIR, "timers.json")


def _now_ms(now=None) -> int:
    if now is None:
        return int(time.time() * 1000)
    return int(now) if now > 1e11 else int(float(now) * 1000)


def load() -> dict:
    """读状态；**坏档走统一招式 `persist.load_or_quarantine`**（改名 `.bad.<时间戳>` 留证 + 记一条 warn）。

    V-R9-18：老写法是 `except: pass` ⇒ 坏档静默变默认值，紧接着 `_save()` **整体覆盖**
    ⇒ "坏文件 + 一次写入 = 旧提醒全没"，而 `add()` 还照旧回 `ok=True`（模型据此对用户说"已定好"）。
    改成留证后，坏档一个字节都不丢、还能人工修回来。
    """
    _BAD = object()                      # 哨兵：分得清"读到的东西"与"走的默认值"
    d = persist.load_or_quarantine(path(), _BAD)
    if isinstance(d, dict) and isinstance(d.get("items"), list):
        d.setdefault("history", [])
        d.setdefault("next_id", 1)
        return d
    if d is not _BAD:
        # 能解析但**形状不对**（顶层不是 dict / items 不是列表）同样是坏档：留证再回默认值，
        # 否则下一次 `_save` 会把它整体盖掉（同一个 V-R9-18 的后果）。
        log.warning("定时提醒状态形状不对 ⇒ 已按坏档留证：%s", persist.quarantine(path()) or "留证失败")
    return {"items": [], "history": [], "next_id": 1, "updatedAt": 0}


def _save(st: dict) -> bool:
    """原子写。**返回是否真的落盘成功**（V10：写失败不许吞成 `pass`——调用方要据它如实回报）。

    V-R9-22：改走 `persist.atomic_write_json`（tmp 名带 pid + 随机后缀 + `os.replace`），
    不再共用 `timers.json.tmp` 这个名字。
    """
    with _lock:
        st["updatedAt"] = int(time.time() * 1000)
        return persist.atomic_write_json(path(), st, indent=1)


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
                "attempts": 0, "created": _now_ms(now), "clamped": clamped, "error": "",
                "next_try_at": 0}      # 0＝未设退避（只看 fire_at）；失败重试后写 fire_at + 退避秒数
        st.setdefault("items", []).append(item)
        saved = _save(st)
    if not saved:
        # V10：写不进去就**不许**回 ok:True——模型会拿它当依据对用户说"已定好"，而它永远不会响
        return {"ok": False, "error": "登记没能落盘（data/ 可写？）"}
    return {"ok": True, "id": tid, "fire_at": item["fire_at"], "seconds": secs,
            "note": text, "clamped": clamped, "max_per_chat": MAX_PENDING_PER_CHAT}


def list_all(chat_key: str | None = None) -> list:
    st = load()
    now_ms = _now_ms()
    out = []
    for it in _pending(st):
        if chat_key and it.get("chat") != str(chat_key):
            continue
        # 剩余秒数要按"还要等到什么时候"算：退避期间只看 fire_at 会显示「0 秒后」，看着像卡住了
        _eff = max(int(it.get("fire_at") or 0), int(it.get("next_try_at") or 0))
        out.append(dict(it, left_seconds=max(0, int((_eff - now_ms) / 1000))))
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
    """到点、且**过了退避时刻**的待触发条目。

    V10：失败重试不再"一失败就立刻重排"——`next_try_at = fire_at + RETRY_BACKOFF_SECONDS × 已试次数`。
    20 秒一次的巡检原先会把 3 次机会在 1 分钟内用光并**永久放弃**，一次临时故障（微信正忙/闸门限流）就白定了。
    """
    now_ms = _now_ms(now)
    return [it for it in _pending(load())
            if int(it.get("fire_at") or 0) <= now_ms and int(it.get("next_try_at") or 0) <= now_ms]


def _finish(item: dict, status: str, error: str = "") -> bool:
    """把条目推进到终态并落盘，**返回是否落盘成功**（失败时调用方要留痕：状态写不下去⇒可能重复发）。"""
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
        return _save(st)


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
                if not _finish(it, "fired"):
                    # 状态写不下去 ⇒ 下轮/重启后会**再发一次**；宁可多一条日志，也不静默（V10 同一族）
                    _log(log, "warning", "定时提醒已发出、但状态没能落盘 ⇒ 可能重复发一次：会话=%s", it.get("chat"))
                out["sent"] += 1
                _log(log, "info", "定时提醒已发出：会话=%s 内容=「%s」", it.get("chat"), str(it.get("note"))[:40])
            else:
                attempts = int(it.get("attempts") or 0) + 1
                if attempts >= MAX_ATTEMPTS:
                    _finish(it, "failed", error="连续 %d 次发送失败（被闸门拦或发送失败）" % attempts)
                    out["failed"] += 1
                    _log(log, "warning", "定时提醒连续失败，已放弃：会话=%s", it.get("chat"))
                else:
                    _nxt = int(it.get("fire_at") or 0) + RETRY_BACKOFF_SECONDS * attempts * 1000
                    with _lock:
                        st = load()
                        for x in st.get("items") or []:
                            if str(x.get("id")) == str(it.get("id")):
                                x["attempts"] = attempts
                                x["next_try_at"] = _nxt
                        if not _save(st):
                            _log(log, "warning", "定时提醒重试计数没能落盘（data/ 可写？）：会话=%s", it.get("chat"))
                    out["failed"] += 1
                    _log(log, "info", "定时提醒发送失败，第 %d 次重试推迟到约 %d 秒后",
                         attempts, RETRY_BACKOFF_SECONDS * attempts)
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
