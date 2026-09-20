# -*- coding: utf-8 -*-
"""按会话 / 按条屏蔽存档消息（第三方 v0.4 对账清单第 10 条）。

原来只有"清全部"（把存档一锅端）。这一层补两件事：

1. **按会话屏蔽存档**（`store.archive_block_chats`）：名单里的会话**消息不写进存档** ——
   不存档 ⇒ 也就没有上下文可回、不进记忆、不进未读触发（等价于"只监听、不参与"）。
   命中时仍**推进水位**（不会反复重扫同一条），日志里如实写"因屏蔽存档被丢弃"。
   支持两种写法：群名（如 `群deepseek`）或 chat_key（如 `group:wxid_xxx`）。
   ⚠️ 与既有的 `store.group_blocklist`（按群按人屏蔽**某个群友**）不是一件事：这条是**整个会话**。
2. **按条屏蔽 / 按条清除**：`blocked` 标记与 `recalled` 共用同一道门（`ChatStore.recent()` 默认都不返回），
   区别是**屏蔽不删**——条目留在存档里、带「已屏蔽」标记，随时可解除；「清除」才是真删。

红线/纪律：①**不做"顺手清空"**——所有删除都必须点名 id，`delete_entries` 空 ids 时返回 0；
②屏蔽会话是显式配置，不因为"看着像广告"就自动屏蔽（自动判定交给风险闸门，两者别混）。
"""
from __future__ import annotations

import re
import threading

_lock = threading.RLock()


def _split(v) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in re.split(r"[,，\n]", v) if s.strip()]
    return [str(s).strip() for s in (v or []) if str(s).strip()]


def blocked_list() -> list:
    """配置里的屏蔽会话名单（群名或 chat_key）。"""
    try:
        from .config import get_config
        return _split(((get_config() or {}).get("store") or {}).get("archive_block_chats"))
    except Exception:
        return []


def is_blocked(chat_key: str = "", group_name: str = "") -> bool:
    """这个会话是否被屏蔽存档（群名或 chat_key 命中都算）。"""
    pool = {_norm(x) for x in blocked_list()}
    if not pool:
        return False
    for cand in (group_name, chat_key):
        if cand and _norm(cand) in pool:
            return True
    return False


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).casefold()


def chat_options(store, wechat=None) -> list:
    """给控制台用的会话清单：存档里有的会话 + 已配置的屏蔽名单。"""
    out = []
    try:
        for ck in (store.list_chats() if store else []):
            out.append({"chat_key": ck, "messages": len(store.list_entries(ck, limit=100000))})
    except Exception:
        pass
    known = {o["chat_key"] for o in out}
    for name in blocked_list():
        if name and not any(_norm(o["chat_key"]) == _norm(name) for o in out):
            out.append({"chat_key": name, "messages": 0, "only_in_blocklist": True})
    return out


def snapshot(store, name_of=None) -> dict:
    """控制台读数：名单 + 存档里被屏蔽的条数。

    ⛔ 2026-09-21（第五轮回执 **V-R5B-7**，P4）：名单可能是**按群名**写的，而这里原来只拿
    `chat_key` 去判 ⇒ 那种名单在面板上恒报"命中 0 个"。⇒ 允许调用方给一个"会话名解析器"
    （`name_of(chat_key) -> 展示名`）；拿不到就照旧（只是少一档匹配，不假装）。
    """
    blocked = blocked_list()
    n_blocked_entries = 0
    hits = []
    try:
        for ck in (store.list_chats() if store else []):
            _nm = ""
            try:
                _nm = str(name_of(ck) or "") if callable(name_of) else ""
            except Exception:
                _nm = ""
            if is_blocked(chat_key=ck, group_name=_nm):
                hits.append(ck)
                continue
            for m in store.list_entries(ck, limit=100000):
                if m.get("blocked"):
                    n_blocked_entries += 1
    except Exception:
        pass
    return {"chats": blocked, "counting": len(hits), "blocked_entries": n_blocked_entries,
            "note": "名单里的会话：消息不入存档、不回、不进记忆；命中时仍推进监听水位"}


def list_chat(store, chat_key: str, limit: int = 30) -> dict:
    """列某个会话最近的消息（**带 recalled/blocked 标记**，供控制台做"屏蔽/解除/清除"）。"""
    items = []
    try:
        for m in store.list_entries(chat_key, limit=limit):
            items.append({"id": m.get("id"), "ts": m.get("ts"), "self": bool(m.get("self")),
                          "sender": str(m.get("sender_name") or m.get("sender_id") or ""),
                          "text": str(m.get("text") or "")[:160],
                          "recalled": bool(m.get("recalled")), "blocked": bool(m.get("blocked")),
                          "block_reason": str(m.get("block_reason") or "")})
    except Exception as e:
        return {"ok": False, "error": str(e), "items": []}
    return {"ok": True, "chat_key": chat_key, "items": items, "count": len(items)}


def block(store, chat_key: str, ids, reason: str = "面板操作") -> dict:
    with _lock:
        n = store.block_entries(chat_key, ids, reason=reason)
    return {"ok": True, "changed": n, "action": "block", "chat_key": chat_key}


def unblock(store, chat_key: str, ids) -> dict:
    with _lock:
        n = store.unblock_entries(chat_key, ids)
    return {"ok": True, "changed": n, "action": "unblock", "chat_key": chat_key}


def delete(store, chat_key: str, ids) -> dict:
    """真删（不可恢复）。**必须点名 id**，空 ids 一律拒绝。"""
    ids = [str(x) for x in (ids or []) if str(x).strip()]
    if not ids:
        return {"ok": False, "error": "必须点名要删的条目 id（本功能不做「清空」）", "changed": 0}
    with _lock:
        n = store.delete_entries(chat_key, ids)
    return {"ok": True, "changed": n, "action": "delete", "chat_key": chat_key, "ids": ids}
