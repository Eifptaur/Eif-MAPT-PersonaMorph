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
  · **窗口**＝同一会话里 **(上一条运行记录的 ts, 本条 ts]**（上一条不存在时下界取"本条 ts − 30 分钟"，
    避免把开天辟地以来的历史全清掉）。一轮的"触发语"在 ts 之前、"它的回复"在 ts 之前 ⇒ 都在窗口内。
  · 删掉窗口内的存档条（**必须点名 id**，走 `archive_filter.delete`，不做"清空"）；
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


def prune_for_deleted_runs(store, all_entries, deleted, trash_root: str = "", stamp: str = "",
                           limit: int = 2000) -> dict:
    """按窗口删存档条；删前整份备份到 `trash_root`。返回回显用的统计。"""
    res = {"chats": {}, "removed": 0, "backed": []}
    wins = run_history_windows(all_entries, deleted)
    if not wins:
        return res
    try:
        from . import archive_filter as _af
    except Exception:
        return res
    for ck, wss in wins.items():
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
            if any(lo <= ms <= hi for lo, hi in wss):
                _id = (it or {}).get("id")
                if _id not in (None, ""):
                    ids.append(str(_id))
        if not ids:
            continue
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
