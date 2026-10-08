# -*- coding: utf-8 -*-
"""群名片名录（第 148 项）：把「**识别到的成员**」和「**人工起的别名**」放在一起，能看、能改。

## 解决什么

机器人早就会**认人**（记忆层按 `wxid` 建成员档），也早就会**用备注**
（`member_notes`：`{wxid: 别名}`，`prompt.py` 的「成员备注」段与 `memory.format_for_prompt`
都会优先用它）。但这两半边**从来没在界面上见过面** ——
用户既看不到"机器人认出了谁"，也没有地方给谁起个名字（只能手改 `config.json`）。

⇒ 本模块把这两半边**接起来**，并只做三件事：**列出来 / 起别名 / 去掉别名**。

## 口径（写进代码，也写进判据）

1. ⛔ **别名的唯一来源是 `member_notes`**（配置里那一份）—— 本模块**不另存一份**，
   也不在别处缓存。两处存同一个事实 ⇒ 迟早不一致。
2. ⛔ **按 `wxid` 存**（人是谁只看 wxid；昵称怎么重都没关系）—— 与"模型输入只给昵称"那条红线配套：
   **内部认人用 uid，给模型看的只有昵称**。
3. ⛔ **只读成员档、不写记忆**：起别名**不会**动 `MemoryStore` 里的任何东西。
4. ⛔ **写配置失败就如实报错**（不假装成功）——「改了个名字但没生效」比"根本没这功能"更让人困惑。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: 一次最多列多少人（名录是给人看的，不是导出全量；超了截断并在返回里说明）
LIST_MAX = 200


def _cfg_read(cfg=None) -> dict:
    if cfg is not None:
        return dict(cfg or {})
    try:
        from .config import get_config
        return dict(get_config() or {})
    except Exception as e:  # noqa: BLE001
        log.debug("读配置失败：%s", str(e)[:80])
        return {}


def _notes(cfg=None) -> dict:
    """现存的别名表（**唯一来源**）。"""
    d = (_cfg_read(cfg).get("member_notes") or {})
    return {str(k): str(v) for k, v in d.items() if str(k).strip()} if isinstance(d, dict) else {}


def _members_of(chat_key: str, store=None) -> list:
    """某个会话里**机器人认出来的成员**（读记忆层；读不到 ⇒ 空表，不猜）。"""
    try:
        if store is not None:
            return list(store.members(chat_key) or [])
        from .memory import MemoryStore
        return list(MemoryStore().members(chat_key) or [])
    except Exception as e:  # noqa: BLE001
        log.debug("读成员档失败：%s", str(e)[:80])
        return []


def _chats(store=None) -> list:
    try:
        if store is not None:
            return list(store.list_chats() or [])
        from .store import ChatStore
        return list(ChatStore().list_chats() or [])
    except Exception:  # noqa: BLE001
        return []


def list_book(chat_key: str = "", *, store=None, memory=None, cfg=None) -> dict:
    """名录 ⇒ `{ok, members:[{userId, name, alias, impressions}], total, truncated, notes}`。

    `chat_key` 给了 ⇒ 只看这个会话；不给 ⇒ **跨会话合并**（同一个人只出现一次 —— 按 `wxid` 去重）。
    ⚠️ 跨会话合并要遍历所有会话，所以有条数上限（`LIST_MAX`），截断时**如实标出来**。
    """
    mem = memory if memory is not None else _members_of  # 可注入（判据用）
    notes = _notes(cfg)
    keys = [chat_key] if str(chat_key or "").strip() else _chats(store)
    seen, rows = set(), []
    for ck in keys:
        cur = mem(ck, store) if memory is not None else _members_of(ck, store)
        for m in cur or []:
            if not isinstance(m, dict):
                continue
            uid = str(m.get("userId") or "").strip()
            nm = str(m.get("name") or "").strip()
            ident = uid or ("n:" + nm)          # 没有 uid 的档（关机总结那条路）按昵称算一个人
            if not ident or ident in seen:
                continue
            seen.add(ident)
            rows.append({"userId": uid, "key": ident, "name": nm,
                         "alias": notes.get(uid, ""),
                         "impressions": len(m.get("impressions") or [])})
    # 有别名的排前面（用户更关心自己起过名的），其余按印象条数降序 —— 稳定的可预期顺序
    rows.sort(key=lambda r: (0 if r["alias"] else 1, -int(r["impressions"] or 0), r["name"] or r["key"]))
    total = len(rows)
    out = rows[:LIST_MAX]
    return {"ok": True, "members": out, "total": total, "truncated": total > len(out),
            "notes": len(notes), "chats": len(keys)}


def set_alias(user_id: str, alias: str, *, cfg=None) -> dict:
    """给某个人起别名（空字符串 ⇒ 等同于去掉）⇒ `{ok, user_id, alias, note}`。

    ⛔ 只改 `member_notes` 这一处，只按 `uid` 存（**不看昵称** —— 昵称重名不影响身份）；
    写盘失败**如实报**。
    """
    uid = str(user_id or "").strip()
    if not uid:
        return {"ok": False, "user_id": "", "alias": "", "note": "没给成员 id，不知道给谁起名"}
    if " " in uid or len(uid) < 2:
        return {"ok": False, "user_id": uid, "alias": "", "note": "成员 id 不像账号，拒绝写入"}
    val = str(alias or "").strip()[:24]
    try:
        from .config import get_config, save_config
        cur = dict(get_config() or {})
        notes = dict(cur.get("member_notes") or {})
        if val:
            notes[uid] = val
        else:
            notes.pop(uid, None)
        cur["member_notes"] = notes
        save_config(cur)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "user_id": uid, "alias": val, "note": "保存失败：%s" % str(e)[:80]}
    return {"ok": True, "user_id": uid, "alias": val,
            "note": ("已记下「%s」，以后用这个称呼他" % val) if val else "已经去掉这个名字（回到他的群昵称）"}


def stats(*, cfg=None) -> dict:
    """名录概览（给状态/概览页用）：起了几个别名。"""
    n = _notes(cfg)
    return {"notes": len(n), "sample": [{"user_id": k, "alias": v} for k, v in list(n.items())[:5]]}
