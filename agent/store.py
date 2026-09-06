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
            for fn in os.listdir(MESSAGES_DIR):
                m = re.match(r"^(group|private)_(.+)\.json$", fn)
                if m:
                    self._state("%s:%s" % (m.group(1), m.group(2)))
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
        """快照当前未读并全部置为已读。"""
        with self._lock:
            st = self._state(chat_key)
            unread = [m for m in st["messages"] if not m["read"] and not m["self"]]
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
        return sum(1 for m in st["messages"] if not m["read"] and not m["self"])

    def peek_unread(self, chat_key: str, limit: int = 3):
        st = self._state(chat_key)
        return [m for m in st["messages"] if not m["read"] and not m["self"]][: max(1, int(limit or 3))]

    def recent(self, chat_key: str, limit: int = 80, offset: int = 0, include_self: bool = True):
        st = self._state(chat_key)
        all_msgs = st["messages"] if include_self else [m for m in st["messages"] if not m["self"]]
        start = max(0, len(all_msgs) - max(0, int(offset or 0)))
        return all_msgs[:start][-max(1, int(limit or 1)):]

    def find_by_mid(self, chat_key: str, mid):
        st = self._state(chat_key)
        target = str(mid)
        for m in st["messages"]:
            if str(m.get("mid")) == target:
                return m
        return None

    def find_by_local_id(self, chat_key: str, local_id: int):
        st = self._state(chat_key)
        for m in st["messages"]:
            if m.get("id") == int(local_id):
                return m
        return None

    def active_members(self, chat_key: str, limit: int = 10):
        st = self._state(chat_key)
        by_id: dict = {}
        for m in st["messages"]:
            if m["self"] or not m.get("sender_id"):
                continue
            sid = m["sender_id"]
            prev = by_id.get(sid)
            if not prev or prev["last_ts"] < m["ts"]:
                by_id[sid] = {"user_id": sid, "name": m.get("sender_name") or "", "last_ts": m["ts"],
                              "count": (prev["count"] if prev else 0) + 1}
            else:
                prev["count"] += 1
        return sorted(by_id.values(), key=lambda x: -x["last_ts"])[: max(1, int(limit))]

    def update_by_mid(self, chat_key: str, mid, text=None, append_media=None) -> bool:
        with self._lock:
            st = self._state(chat_key)
            target = str(mid)
            for m in st["messages"]:
                if str(m.get("mid")) == target:
                    if text is not None:
                        m["text"] = str(text)
                    if append_media:
                        seen = {x.get("url") for x in m.get("media", []) if x and x.get("url")}
                        for x in append_media:
                            if x and x.get("url") and x["url"] not in seen:
                                m.setdefault("media", []).append(x)
                                seen.add(x["url"])
                    _save_chat(st)
                    return True
            return False
