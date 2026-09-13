# -*- coding: utf-8 -*-
"""每会话（group:wxid / private:wxid）一个不断增长的 JSON 消息存储。

条目格式：
  id        本地递增序号
  mid       微信 local_id
  ts        时间戳（毫秒）
  sender_id 发送者 wxid（自己发送的为 'self'）
  sender_name 群名片/昵称
  text      解析后的纯文本（[图片] 等占位符已内联）
  self      是否机器人自己发的
  read      已读状态
  reply     可选 {sender, text}
  media     可选 [{kind, local_id, url, ...}]
"""
from __future__ import annotations

import json
import os
import re
import threading

from .config import DATA_DIR, get_config

MESSAGES_DIR = os.path.join(DATA_DIR, "messages")


def chat_file(chat_key: str) -> str:
    safe = re.sub(r"[^a-z0-9_]", "_", str(chat_key), flags=re.IGNORECASE)
    return os.path.join(MESSAGES_DIR, safe + ".json")


def _load_chat(chat_key: str) -> dict:
    try:
        with open(chat_file(chat_key), "r", encoding="utf-8-sig") as f:
            parsed = json.load(f)
        if isinstance(parsed, dict) and isinstance(parsed.get("messages"), list):
            return parsed
    except Exception:
        pass
    return {"chat_key": chat_key, "next_local_id": 1, "messages": []}


def _save_chat(state: dict) -> None:
    os.makedirs(MESSAGES_DIR, exist_ok=True)
    tmp = chat_file(state["chat_key"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, chat_file(state["chat_key"]))


class ChatStore:
    def __init__(self, max_per_chat: int = 0):
        self.max_per_chat = max(0, int(max_per_chat or 0))
        self.chats: dict = {}
        self._lock = threading.Lock()

    def _state(self, chat_key: str) -> dict:
        if chat_key not in self.chats:
            self.chats[chat_key] = _load_chat(chat_key)
        return self.chats[chat_key]

    def _trim(self, st: dict) -> None:
        if self.max_per_chat > 0 and len(st["messages"]) > self.max_per_chat:
            del st["messages"][: len(st["messages"]) - self.max_per_chat]

    def list_chats(self):
        try:
            files = set()
            for fn in os.listdir(MESSAGES_DIR):
                m = re.match(r"^(group|private)_(.+)\.json$", fn)
                if m:
                    files.add("%s:%s" % (m.group(1), m.group(2)))
            # 与磁盘同步：内存中已被删除的 chat（手动删 json/清数据）一并移除，避免"幽灵群"出现在记忆页
            for k in [k for k in self.chats if k not in files]:
                del self.chats[k]
            for ck in files:
                self._state(ck)
        except FileNotFoundError:
            pass
        return list(self.chats.keys())

    def append_incoming(self, chat_key: str, mid, ts, sender_id, sender_name, text, reply=None, media=None):
        with self._lock:
            st = self._state(chat_key)
            entry = {
                "id": st["next_local_id"],
                "mid": mid,
                "ts": ts or int(__import__("time").time() * 1000),
                "sender_id": str(sender_id or ""),
                "sender_name": str(sender_name or ""),
                "text": str(text or ""),
                "self": False,
                "read": False,
                "reply": reply or None,
                "media": media or [],
            }
            st["next_local_id"] += 1
            st["messages"].append(entry)
            self._trim(st)
            _save_chat(st)
            return entry

    def append_self(self, chat_key: str, text, ts=None, mid=None):
        with self._lock:
            st = self._state(chat_key)
            entry = {
                "id": st["next_local_id"],
                "mid": mid,
                "ts": ts or int(__import__("time").time() * 1000),
                "sender_id": "self",
                "sender_name": "我",
                "text": str(text or ""),
                "self": True,
                "read": True,
                "reply": None,
                "media": [],
            }
            st["next_local_id"] += 1
            st["messages"].append(entry)
            self._trim(st)
            _save_chat(st)
            return entry

    def drain_unread(self, chat_key: str):
        """快照当前未读并全部置为已读（**已撤回的不算未读**，不触发回复）。"""
        with self._lock:
            st = self._state(chat_key)
            unread = [m for m in st["messages"]
                      if not m["read"] and not m["self"] and not m.get("recalled") and not m.get("blocked")]
            for m in st["messages"]:
                m["read"] = True
            _save_chat(st)
            return unread

    def mark_all_read(self, chat_key: str) -> int:
        with self._lock:
            st = self._state(chat_key)
            n = 0
            for m in st["messages"]:
                if not m["read"] and not m["self"]:
                    m["read"] = True
                    n += 1
            if n:
                _save_chat(st)
            return n

    def unread_count(self, chat_key: str) -> int:
        st = self._state(chat_key)
        return sum(1 for m in st["messages"]
                   if not m["read"] and not m["self"] and not m.get("recalled") and not m.get("blocked"))

    def peek_unread(self, chat_key: str, limit: int = 3):
        st = self._state(chat_key)
        return [m for m in st["messages"]
                if not m["read"] and not m["self"] and not m.get("recalled")
                and not m.get("blocked")][: max(1, int(limit or 3))]

    def recent(self, chat_key: str, limit: int = 80, offset: int = 0, include_self: bool = True,
               include_recalled: bool = False, include_blocked: bool = False):
        """最近 N 条。**默认不返回已撤回、已屏蔽的**——提示词、记忆提炼、工具取上下文都在这一处生效。"""
        st = self._state(chat_key)
        all_msgs = st["messages"] if include_self else [m for m in st["messages"] if not m["self"]]
        if not include_recalled:
            all_msgs = [m for m in all_msgs if not m.get("recalled")]
        if not include_blocked:
            all_msgs = [m for m in all_msgs if not m.get("blocked")]
        start = max(0, len(all_msgs) - max(0, int(offset or 0)))
        return all_msgs[:start][-max(1, int(limit or 1)):]

    def list_entries(self, chat_key: str, limit: int = 30, include_self: bool = True):
        """给控制台/接口用的原始列表（**带上 recalled/blocked 标记**，便于"屏蔽/解除"这类操作）。"""
        st = self._state(chat_key)
        msgs = st["messages"] if include_self else [m for m in st["messages"] if not m["self"]]
        return list(msgs[-max(1, int(limit or 1)):])

    def _flag_entries(self, chat_key: str, entry_ids, field: str, value=True, reason: str = "") -> int:
        """批量打/清标记（blocked / recalled 用的是同一套）。返回实际改动条数。"""
        want = {str(x) for x in (entry_ids or [])}
        if not want:
            return 0
        with self._lock:
            st = self._state(chat_key)
            n = 0
            for m in st["messages"]:
                if str(m.get("id")) not in want:
                    continue
                if field == "blocked":
                    m["blocked"] = bool(value)
                    m["block_reason"] = str(reason or "")[:120] if value else ""
                    m["read"] = True
                else:
                    m[field] = value
                n += 1
            if n:
                _save_chat(st)
            return n

    def block_entries(self, chat_key: str, entry_ids, reason: str = "") -> int:
        """按条屏蔽：条目留在存档里（可追溯、可解除），但**不再进上下文/记忆/未读触发**。"""
        return self._flag_entries(chat_key, entry_ids, "blocked", True, reason)

    def unblock_entries(self, chat_key: str, entry_ids) -> int:
        return self._flag_entries(chat_key, entry_ids, "blocked", False)

    def delete_entries(self, chat_key: str, entry_ids) -> int:
        """按条真删（不可恢复）。只删指定 id，**不做"顺手清空"**。"""
        want = {str(x) for x in (entry_ids or [])}
        if not want:
            return 0
        with self._lock:
            st = self._state(chat_key)
            before = len(st["messages"])
            st["messages"] = [m for m in st["messages"] if str(m.get("id")) not in want]
            n = before - len(st["messages"])
            if n:
                _save_chat(st)
            return n

    def find_by_id(self, chat_key: str, entry_id):
        st = self._state(chat_key)
        for m in st["messages"]:
            if str(m.get("id")) == str(entry_id):
                return m
        return None

    def mark_recalled(self, chat_key: str, entry_id, info: dict | None = None):
        """给一条存档打「已撤回」标记（保留记录本身，只让它不再进上下文）。

        返回被标记的条目；找不到该 id 时返回 None（调用方据此如实报告，不假装成功）。
        """
        info = dict(info or {})
        with self._lock:
            st = self._state(chat_key)
            for m in st["messages"]:
                if str(m.get("id")) != str(entry_id):
                    continue
                m["recalled"] = True
                m["recall_ts"] = int(info.get("ts") or __import__("time").time() * 1000)
                m["recall_who"] = str(info.get("who") or ("自己" if info.get("self") else ""))
                m["recall_note"] = str(info.get("note") or "")[:120]
                m["read"] = True
                _save_chat(st)
                return m
            return None

    def find_by_mid(self, chat_key: str, mid):
        st = self._state(chat_key)
        target = str(mid)
        for m in st["messages"]:
            if str(m.get("mid")) == target:
                return m
        return None

    def active_members(self, chat_key: str, limit: int = 10):
        st = self._state(chat_key)
        by_id: dict = {}
        for m in st["messages"]:
            if m["self"] or not m.get("sender_id") or m.get("recalled") or m.get("blocked"):
                continue
            sid = m["sender_id"]
            prev = by_id.get(sid)
            if not prev or prev["last_ts"] < m["ts"]:
                by_id[sid] = {"user_id": sid, "name": m.get("sender_name") or "", "last_ts": m["ts"],
                              "count": (prev["count"] if prev else 0) + 1}
            else:
                prev["count"] += 1
        return sorted(by_id.values(), key=lambda x: -x["last_ts"])[: max(1, int(limit))]
