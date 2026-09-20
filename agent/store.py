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
import logging
import os
import re
import threading
import time

from .config import DATA_DIR, get_config

log = logging.getLogger("persona-morph")

MESSAGES_DIR = os.path.join(DATA_DIR, "messages")


def chat_file(chat_key: str) -> str:
    """⛔ 2026-09-21 修（第四轮审计候选 **S-1**）：老实现只做字符替换 ⇒ **不同 chat_key 会撞同一个文件**。

    实测：`group:wxid_a-b` 与 `group:wxid_a_b` 都归一成 `group_wxid_a_b.json` ⇒ 两个会话共用一个档案，
    互相覆盖（用户视角＝"两个群的消息串了 / 有一个群的记录莫名少了一半"）。
    ⇒ 现在文件名 = 归一化名 + **原有 chat_key 的短哈希**（8 位），归一化只用来"给人看"，
    唯一性由哈希保证。
    """
    return os.path.join(MESSAGES_DIR, _safe_name(chat_key) + "_" + _key_hash(chat_key) + ".json")


def _safe_name(chat_key: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", str(chat_key), flags=re.IGNORECASE)


def _key_hash(chat_key: str) -> str:
    import hashlib
    return hashlib.md5(str(chat_key).encode("utf-8")).hexdigest()[:8]


def chat_file_legacy(chat_key: str) -> str:
    """老命名（无哈希）——**只读兼容**：老版本的档案仍按这个名字存在磁盘上。"""
    return os.path.join(MESSAGES_DIR, _safe_name(chat_key) + ".json")


def chat_file_existing(chat_key: str) -> str:
    """该会话**实际在用**的文件路径：新命名优先，没有就退回老命名（老档案不搬家、不丢）。"""
    p = chat_file(chat_key)
    if os.path.exists(p):
        return p
    q = chat_file_legacy(chat_key)
    if os.path.exists(q):
        return q
    return p


_KEY_RE = re.compile(r'"chat_key"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _read_key_of(path: str) -> str:
    """只读文件头几 KB 抠出 `chat_key`（扫描磁盘上的档案时用，不必整份解析）。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            head = f.read(8192)
    except OSError:
        return ""
    m = _KEY_RE.search(head)
    if not m:
        return ""
    try:
        return str(json.loads('"%s"' % m.group(1)))
    except Exception:
        return ""


def _filename_key(fn: str) -> str:
    """老命名 `group_xxx.json` → `group:xxx`（抠掉新命名的 8 位哈希尾巴，兼容两种命名）。"""
    base = fn[:-5] if fn.lower().endswith(".json") else fn
    base = re.sub(r"_[0-9a-f]{8}$", "", base)
    for pre in ("group_", "private_"):
        if base.startswith(pre):
            return "%s:%s" % (pre[:-1], base[len(pre):])
    return ""


def _load_chat(chat_key: str) -> dict:
    """读某会话的档案。**读失败不再悄悄返回空壳**。

    ⛔ 2026-09-21 修（第四轮审计 **V-R4-6，P1**）：老实现无论什么失败都返回空壳，
    而调用方随后一保存就**用"空壳 + 新条目"覆盖掉原文件** —— 审计实测：8 条历史 165B → 1 条 279B，
    等于**静默丢数据**（用户只会觉得"它把我之前的记录清了"）。⇒ 分三种情况：
      · 文件**不存在** ＝ 新会话 ⇒ 正常空壳（合法路径，别拦）；
      · 内容**坏**（解析不了）⇒ 把原文件**隔离**成 `*.corrupt-<时间戳>.json`（数据不丢、可人工恢复），
        再开新档，并在新档里记 `quarantined`（日志/控制台看得见）；
      · **读不动**（权限/被占用/IO 错）⇒ **绝不碰那个文件**，返回带 `_loadFailed` 的档，
        `_save_chat` 见到它就**拒绝写盘**（fail-closed）：宁可这条不入档，也不覆盖别人的数据。
    """
    p = chat_file_existing(chat_key)
    if not os.path.exists(p):
        return {"chat_key": chat_key, "next_local_id": 1, "messages": []}
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            raw = f.read()
    except OSError as e:
        log.warning("会话档案读不动（%s）：%s: %s ⇒ **本次不写盘**（不覆盖原文件），这条不入档",
                    chat_key, type(e).__name__, str(e)[:80])
        return {"chat_key": chat_key, "next_local_id": 1, "messages": [],
                "_loadFailed": "%s: %s" % (type(e).__name__, str(e)[:80])}
    bad = ""
    try:
        parsed = json.loads(raw)
        if not (isinstance(parsed, dict) and isinstance(parsed.get("messages"), list)):
            bad = "结构不对（不是 {chat_key, next_local_id, messages:[...]}）"
    except Exception as e:
        bad = "%s: %s" % (type(e).__name__, str(e)[:80])
    if not bad:
        return parsed
    # 内容坏 ⇒ **先隔离**（保数据），再开新档
    q = "%s.corrupt-%s.json" % (p[:-5] if p.lower().endswith(".json") else p,
                                time.strftime("%Y%m%d-%H%M%S"))
    try:
        os.replace(p, q)
        log.warning("会话档案内容坏（%s：%s）⇒ 已隔离到 %s（旧数据没丢，可人工恢复），另开新档",
                    chat_key, bad, os.path.basename(q))
        return {"chat_key": chat_key, "next_local_id": 1, "messages": [],
                "quarantined": os.path.basename(q)}
    except OSError as e:
        log.warning("会话档案内容坏、且隔离失败（%s：%s）⇒ **本次不写盘**（不覆盖原文件）",
                    type(e).__name__, str(e)[:80])
        return {"chat_key": chat_key, "next_local_id": 1, "messages": [],
                "_loadFailed": "内容坏且隔离失败：%s" % str(e)[:60]}


def _save_chat(state: dict) -> None:
    # ⛔ V-R4-6：读失败（`_loadFailed`）时**拒绝写回** —— 否则空壳会把原档案覆盖掉。
    if state.get("_loadFailed"):
        log.warning("会话档案先前读失败（%s：%s）⇒ 本次**不写盘**（保原文件）",
                    state.get("chat_key"), str(state.get("_loadFailed"))[:80])
        return
    os.makedirs(MESSAGES_DIR, exist_ok=True)
    dst = chat_file(state["chat_key"])
    if not os.path.exists(dst) and os.path.exists(chat_file_legacy(state["chat_key"])):
        # 老命名档案 → 首次写新命名：**只写不搬**（老文件可能属于撞名的另一个会话，搬走＝抢数据）
        log.info("会话档案改用带哈希的新命名（%s）：老文件保留在 %s，新档从它读入后另存",
                 str(state.get("chat_key"))[:40], os.path.basename(chat_file_legacy(state["chat_key"])))
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)


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
                if not re.match(r"^(group|private)_.+\.json$", fn):
                    continue
                # ⛔ S-1：文件名现在带哈希尾巴 ⇒ **不能**再从文件名反推 chat_key（会得到
                # `group:wxid_x_1a2b3c4d` 这种幽灵会话）。以档案里的 `chat_key` 为准，
                # 读不到才退回文件名推导（老档案 / 手工放进去的文件）。
                p = os.path.join(MESSAGES_DIR, fn)
                ck = _read_key_of(p) or _filename_key(fn)
                if ck:
                    files.add(ck)
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
