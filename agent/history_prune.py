# -*- coding: utf-8 -*-
"""删「运行明细」时，把那一轮对应的**对话历史**一并删掉。

⛔ 为什么要有它（作者 2026-09-18 原话）：
  「**就应该删的是历史啊**，因为我删运行明细那个地方，就是删他回了什么。也就是说，我删明细就等于
   我想要删历史，就等于我想删掉『**我说什么而他回什么**』的这一段。怎么能只能删明细呢？
   这就是错的，**从根上就是错的**，赶紧修啊」
  · 事实：模型的上下文来自 `agent/store.py`（`data/messages/<会话>.json`，`store.recent()`），
    而控制台删的 `data/sessions/*.jsonl` **只是运行日志**（计费/复盘用）⇒ **只删它等于什么都没删**
    （现场表现：作者把运行明细删光，机器人照样叫他"复读机"，因为它还看得见那些一模一样的旧消息）。

做法（可解释、可单测）：
  · **归属**（第二版，2026-09-18 改）：每条存档消息**归它所属的那一轮**，只删"归属于被删轮次"的条目。
    · 我们自己发的（`self=True`，＝那一轮的回复）⇒ 归**此前最近的一轮**；
    · 别人发的（触发语 / 拍一拍 / 系统提示）⇒ 若**紧邻其后**的一轮在 60 秒内，归**那一轮**（它就是触发语），
      否则归此前最近的一轮。
    ⛔ 第一版用的是"时间窗 (上一条 ts, 本条 ts]"——实测会**连累留下来那一轮的回复**：
       例 03:27:50 那轮留着、03:28:28 那轮被删，而它的回复落在 03:28:26，按窗口就被一起删了，
       于是"只有我问、没有他答"。归属法按"谁发的"分开判，这一例的回复正确留在 03:27:50 那轮里。
  · 删掉这些存档条（**必须点名 id**，走 `archive_filter.delete`，不做"清空"）；
  · 删前把**整份** `data/messages/<会话>.json` 备到 `data/_trash/messages/`，供「撤销上次删除」还原。
"""
from __future__ import annotations

import datetime
import json
import os
import shutil

# 窗口下界兜底：找不到"上一条运行记录"时，只回看这么久（毫秒）
_DEFAULT_LOOKBACK_MS = 30 * 60 * 1000
# 窗口上界余量：本轮的回复可能比运行记录晚几毫秒落库
_SLACK_MS = 5000
# 触发语通常早于运行记录几秒～几十秒（例：触发 07:12:19 → 运行记录 07:12:30）
_LEAD_MS = 60 * 1000


def iso_to_ms(ts) -> int:
    """`2026-09-18T07:06:02` → epoch 毫秒（失败返回 0）。"""
    try:
        s = str(ts or "").strip().replace("Z", "")
        if not s:
            return 0
        if s.isdigit():                       # 已经是毫秒戳
            return int(s)
        return int(datetime.datetime.fromisoformat(s).timestamp() * 1000)
    except Exception:
        return 0


def run_history_windows(all_entries, deleted, lookback_ms: int = _DEFAULT_LOOKBACK_MS,
                        slack_ms: int = _SLACK_MS) -> dict:
    """算出"被删的那几轮"各自该覆盖的会话消息时间窗。**纯函数，可单测**。

    返回 `{chat_key: [(lo_ms, hi_ms), …]}`。
    """
    by_chat = {}
    for e in (all_entries or []):
        ck = str((e or {}).get("chat_key") or "")
        ms = iso_to_ms((e or {}).get("ts"))
        if ck and ms:
            by_chat.setdefault(ck, []).append(ms)
    for ck in by_chat:
        by_chat[ck].sort()
    out = {}
    for e in (deleted or []):
        ck = str((e or {}).get("chat_key") or "")
        ms = iso_to_ms((e or {}).get("ts"))
        if not ck or not ms:
            continue
        prev = [x for x in by_chat.get(ck, []) if x < ms]
        lo = (max(prev) if prev else (ms - int(lookback_ms))) + 1
        out.setdefault(ck, []).append((lo, ms + int(slack_ms)))
    for ck in out:
        out[ck] = sorted(out[ck])
    return out


def run_timeline(all_entries, deleted) -> dict:
    """每个会话**完整的轮次时间线**（现存 + 已删，合并去重后升序）。返回 `{chat_key: [ts_ms, …]}`。

    必须带上"已删的那几轮"——否则算归属时会把它当成不存在，界线就错到隔壁轮次去了。
    """
    tl = {}
    for e in list(all_entries or []) + list(deleted or []):
        ck = str((e or {}).get("chat_key") or "")
        ms = iso_to_ms((e or {}).get("ts"))
        if ck and ms:
            tl.setdefault(ck, set()).add(ms)
    return {ck: sorted(v) for ck, v in tl.items()}


def entry_owner(ts: int, is_self, times, lead_ms: int = _LEAD_MS,
                lookback_ms: int = _DEFAULT_LOOKBACK_MS) -> int:
    """**这条存档消息属于哪一轮**（返回那一轮的 ts；判不出来返回 0）。纯函数，可单测。

    · `self=True`（我们自己发的＝那一轮的回复）⇒ 归**此前最近的一轮**；
    · 别人发的（触发语 / 拍一拍事件 / 系统提示）⇒ 紧邻其后的一轮在 `lead_ms` 内就归它，否则归此前最近的一轮；
    · 早于该会话第一轮的内容：只在 `lookback_ms` 内才归第一轮，否则不归任何一轮（不去动它）。
    """
    times = list(times or [])
    if not times:
        return 0
    prev = nxt = 0
    for t in times:
        if t <= ts:
            prev = t
        elif not nxt:
            nxt = t
    if not is_self and nxt and (nxt - ts) <= int(lead_ms):
        return nxt
    if prev:
        return prev
    if nxt and (nxt - ts) <= int(lookback_ms):
        return nxt
    return 0


def deleted_entry_ids(store, all_entries, deleted, lead_ms: int = _LEAD_MS, limit: int = 2000) -> dict:
    """`{chat_key: [存档条目 id]} —— 归属于"被删掉的那几轮"的条目。**只点名、不清空**。"""
    gone = set()
    for e in (deleted or []):
        ck = str((e or {}).get("chat_key") or "")
        ms = iso_to_ms((e or {}).get("ts"))
        if ck and ms:
            gone.add((ck, ms))
    if not gone:
        return {}
    out = {}
    for ck, times in run_timeline(all_entries, deleted).items():
        try:
            items = store.recent(ck, limit=max(50, int(limit))) or []
        except Exception:
            continue
        ids = []
        for it in items:
            try:
                ms = int(str((it or {}).get("ts") or "0"))
            except Exception:
                continue
            owner = entry_owner(ms, bool((it or {}).get("self")), times, lead_ms)
            if owner and (ck, owner) in gone:
                _id = (it or {}).get("id")
                if _id not in (None, ""):
                    ids.append(str(_id))
        if ids:
            out[ck] = ids
    return out


def prune_for_deleted_runs(store, all_entries, deleted, trash_root: str = "", stamp: str = "",
                           limit: int = 2000, lead_ms: int = _LEAD_MS) -> dict:
    """删掉"归属于被删轮次"的存档条；删前整份备份到 `trash_root`。返回回显用的统计。"""
    res = {"chats": {}, "removed": 0, "backed": []}
    plan = deleted_entry_ids(store, all_entries, deleted, lead_ms=lead_ms, limit=limit)
    if not plan:
        return res
    try:
        from . import archive_filter as _af
    except Exception:
        return res
    for ck, ids in plan.items():
        # 删前整份备份（供「撤销上次删除」还原）
        if trash_root:
            try:
                src = None
                try:
                    from . import store as _st
                    src = _st.chat_file(ck)
                except Exception:
                    src = None
                if src and os.path.exists(src):
                    os.makedirs(trash_root, exist_ok=True)
                    dst = os.path.join(trash_root, "%s.json.%s" % (os.path.basename(src), stamp))
                    shutil.copyfile(src, dst)
                    res["backed"].append(dst)
            except Exception:
                pass
        try:
            r = _af.delete(store, ck, ids)
            n = int((r or {}).get("changed") or 0)
        except Exception:
            n = 0
        if n:
            res["chats"][ck] = n
            res["removed"] += n
    return res


def restore_history(trash_root: str, stamp: str) -> int:
    """按 stamp 把 `_trash/messages/*.json.<stamp>` 放回原位，返回还原的文件数。"""
    n = 0
    try:
        if not (trash_root and stamp and os.path.isdir(trash_root)):
            return 0
        for name in os.listdir(trash_root):
            if not name.endswith("." + stamp):
                continue
            src = os.path.join(trash_root, name)
            base = name[: -len("." + stamp)]                 # group_xxx_chatroom.json
            if not base.endswith(".json"):
                continue
            try:
                from . import store as _st
                dst = os.path.join(_st.MESSAGES_DIR, base)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(src, "rb") as f1, open(dst, "wb") as f2:
                    f2.write(f1.read())
                os.remove(src)
                n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n


def load_recent_entries(sessions_dir: str, limit: int = 100) -> list:
    """读最近若干条运行明细（供算窗口用；读不到就返回空表）。"""
    try:
        from .session_log import SessionLog
        return SessionLog(os.path.dirname(sessions_dir)).recent(limit) or []
    except Exception:
        return []
