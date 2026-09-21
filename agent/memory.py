# -*- coding: utf-8 -*-
"""群友印象记忆：每个会话一个文件夹，每个群友一个以 wxid 命名的 JSON 文件。

目录结构：
  data/memory/group_<wxid>/<wxid>.json
  data/memory/group_<wxid>/_meta.json
每个成员文件：{userId, name, impressions: [{content, createdAt}], updatedAt, lastConsolidatedAt}
"""
from __future__ import annotations

import json
import os
import re
import threading

from . import persist
from .config import DATA_DIR, get_config

MEMORY_DIR = os.path.join(DATA_DIR, "memory")


def _chat_dir_name(chat_key: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", str(chat_key), flags=re.IGNORECASE)


def _member_file_name(user_id: str, name: str = "") -> str:
    if str(user_id or "").strip():
        uid = str(user_id).strip()
        return uid + ".json" if re.match(r"^[\w-]+$", uid) else "u_" + re.sub(r"[^a-z0-9_]", "_", uid, flags=re.IGNORECASE) + ".json"
    safe = re.sub(r"[^a-z0-9_\u4e00-\u9fa5]", "_", str(name or "unknown"), flags=re.IGNORECASE)[:40]
    return "_n_" + (safe or "unknown") + ".json"


def _read_json(file, fallback):
    try:
        with open(file, "r", encoding="utf-8-sig") as f:
            parsed = json.load(f)
        return parsed if isinstance(parsed, dict) else fallback
    except Exception:
        return fallback


def _write_json(file, value):
    """原子写（`persist.atomic_write_json`：tmp 名带 pid + 随机后缀 + `os.replace`），失败**抛异常**。

    V-R9-22：老写法共用 `<file>.tmp` 这个名字 ⇒ 并发写同一个成员档（巡检线程/多开实例）会
    **打开同一个临时文件**互相穿插出半截 JSON（审计实测 200 次并发写坏 46 个），而读侧 `_read_json`
    又静默回 fallback ⇒ 印象全没了。失败仍然抛（不吞），调用方的既有语义不变。
    """
    if not persist.atomic_write_json(file, value, indent=1):
        raise OSError("记忆落盘失败（原档未动）：%s" % file)


def _chat_dir(chat_key: str) -> str:
    return os.path.join(MEMORY_DIR, _chat_dir_name(chat_key))


def _data_dir() -> str:
    """记忆根目录（互通池枚举用）。"""
    return MEMORY_DIR


def _unname_dir(name: str) -> str:
    """把加密目录名还原为原始 chat_key 近似值（缺省 = 原名，够用于 pool 合并）。"""
    return str(name or "").replace("memory_", "", 1)


def _member_file(chat_key: str, user_id: str, name: str = "") -> str:
    return os.path.join(_chat_dir(chat_key), _member_file_name(user_id, name))


def _meta_file(chat_key: str) -> str:
    return os.path.join(_chat_dir(chat_key), "_meta.json")


class MemoryStore:
    def __init__(self):
        self.cache: dict = {}          # chat_key -> {user_id: member}
        self._lock = threading.Lock()

    def _share_pool(self) -> bool:
        """记忆互通开关：true=所有群共享一个记忆池（跨群可见）。"""
        try:
            return bool(get_config().get("memory", {}).get("share_across_groups") is True)
        except Exception:
            return False

    def _shared_groups(self) -> list:
        """勾选的共享群（memory.shared_groups，存群名/chat_key）；空=未勾选。"""
        try:
            return [str(x) for x in (get_config().get("memory", {}).get("shared_groups") or []) if str(x)]
        except Exception:
            return []

    def _chat_keys(self, chat_key: str) -> list:
        """互通时返回的群：勾选了 shared_groups → 只在这些群间互通（本群在内才生效）；
        否则 share_across_groups=true 全部互通；false 仅本群。"""
        groups = self._shared_groups()
        if groups:
            if not self._share_pool():
                # 未开总开关但勾了群：视为"勾选群间互通"
                pass
            keys = [g for g in groups if g]
            if not keys:
                return [chat_key]
            # 本群名匹配（chat_key 或群名）尽量宽
            base = os.path.basename(chat_key)
            match = [g for g in keys if g == chat_key or g == base or chat_key.endswith(g)]
            return keys if match else [chat_key]
        if not self._share_pool():
            return [chat_key]
        try:
            keys = []
            base = _data_dir()
            if os.path.isdir(base):
                for fn in sorted(os.listdir(base)):
                    p = os.path.join(base, fn)
                    if os.path.isdir(p) and fn not in (".", ".."):
                        keys.append(fn)
            return keys or [chat_key]
        except Exception:
            return [chat_key]

    def _ensure_chat(self, chat_key: str) -> dict:
        if chat_key not in self.cache:
            m = {}
            try:
                for fn in os.listdir(_chat_dir(chat_key)):
                    if not fn.endswith(".json") or fn == "_meta.json":
                        continue
                    raw = _read_json(os.path.join(_chat_dir(chat_key), fn), None)
                    if not raw:
                        continue
                    key = str(raw.get("userId")) if raw.get("userId") else "_n_" + fn
                    m[key] = {
                        "userId": str(raw.get("userId") or ""),
                        "name": str(raw.get("name") or ""),
                        "impressions": raw.get("impressions") if isinstance(raw.get("impressions"), list) else [],
                        "updatedAt": int(raw.get("updatedAt") or 0),
                        "lastConsolidatedAt": int(raw.get("lastConsolidatedAt") or 0),
                    }
            except FileNotFoundError:
                pass
            self.cache[chat_key] = m
        return self.cache[chat_key]

    def _append_raw(self, chat_key: str, user_id: str, name: str, content: str, created_at=None):
        m = self._ensure_chat(chat_key)
        key = str(user_id) if user_id else "_n_" + _member_file_name("", name)
        member = m.get(key) or {"userId": str(user_id or ""), "name": str(name or ""),
                                "impressions": [], "updatedAt": 0, "lastConsolidatedAt": 0}
        entry = {"content": str(content or "")[:300], "createdAt": int(created_at or __import__("time").time() * 1000)}
        if not any(e.get("content") == entry["content"] for e in member["impressions"]):
            member["impressions"].append(entry)
        member["userId"] = str(user_id or member.get("userId") or "")
        member["name"] = str(name or member.get("name") or "")
        member["updatedAt"] = int(__import__("time").time() * 1000)
        _write_json(_member_file(chat_key, user_id, name), member)
        m[key] = member
        return entry

    def collect_member_texts(self, user_id: str, name: str = "") -> list:
        """跨所有群收集该成员的全部印象文本（真实记录，供深度提炼）。"""
        texts = []
        seen = set()
        root = _data_dir()
        try:
            for ck in os.listdir(root):
                cdir = os.path.join(root, ck)
                if not os.path.isdir(cdir):
                    continue
                for fn in os.listdir(cdir):
                    if not fn.endswith(".json") or fn == "_meta.json":
                        continue
                    try:
                        raw = _read_json(os.path.join(cdir, fn), None)
                    except Exception:
                        continue
                    if not raw:
                        continue
                    if str(raw.get("userId") or "") != str(user_id) and not (name and str(raw.get("name") or "") == name):
                        continue
                    for it in raw.get("impressions") or []:
                        c = str(it.get("content") or "").strip()
                        if c and len(c) >= 4 and c not in seen:
                            seen.add(c)
                            texts.append(c)
        except Exception:
            pass
        return texts

    def append(self, chat_key: str, category: str, content: str, extra: dict | None = None):
        if category != "memberImpression":
            return None
        extra = extra or {}
        user_id = str(extra.get("userId") or "").strip()
        target = str(extra.get("target") or "").strip()[:60]
        if not user_id and not target:
            return None
        return self._append_raw(chat_key, user_id, target or user_id, content)

    def query(self, chat_key: str, category: str = ""):
        if category and category != "memberImpression":
            return {category: []}
        m = self._ensure_chat(chat_key)
        out = []
        for mem in m.values():
            for e in mem["impressions"]:
                out.append({
                    "userId": str(mem.get("userId") or ""),
                    "target": str(mem.get("name") or mem.get("userId") or "某人"),
                    "content": e["content"],
                    "createdAt": e["createdAt"],
                })
        out.sort(key=lambda x: -x["createdAt"])
        return {"memberImpression": out}

    def members(self, chat_key: str):
        """列出成员档案。互通时合并所有群（同 wxid 合并印象）；隔离时仅本群。"""
        merged: dict = {}
        for key in self._chat_keys(chat_key):
            for mem in self._ensure_chat(key).values():
                if not mem["impressions"]:
                    continue
                uid = str(mem.get("userId") or "")
                ident = uid or str(mem.get("name") or "")
                if not ident:
                    continue
                if ident not in merged:
                    merged[ident] = {
                        "userId": mem.get("userId") or "",
                        "name": str(mem.get("name") or mem.get("userId") or "某人"),
                        "impressions": list(mem.get("impressions") or []),
                        "updatedAt": mem.get("updatedAt") or 0,
                        "lastConsolidatedAt": mem.get("lastConsolidatedAt") or 0,
                    }
                else:
                    merged[ident]["impressions"] = list(mem.get("impressions") or [])
                    merged[ident]["updatedAt"] = max(merged[ident]["updatedAt"], mem.get("updatedAt") or 0)
        out = [dict(v, impressions=[dict(e) for e in v["impressions"]]) for v in merged.values()]
        out.sort(key=lambda x: -(x["updatedAt"] or 0))
        return out

    def remove(self, chat_key: str, category: str, user_id="", target="", content="", scope="all"):
        """删成员印象。`scope`＝**删除范围**（用户口径：不替他二选一，做成界面可选档）：

        · `all` （默认）＝按 `_chat_keys()` 把**互通范围内的每一份都删掉** —— 与 `members()`
          的读取口径一致（2026-09-16 修「删了还能读到」时定的，界面列的是合并视图，就删合并的那些）；
        · `this`＝**只删 `chat_key` 这一个群**里那一份（别的群还留着 ⇒ 合并视图里仍会显示，
          所以调用方必须把这件事**如实告诉用户**，见 `elsewhere()`）。
        """
        if category != "memberImpression":
            return False
        keys = [chat_key] if str(scope) == "this" else list(self._chat_keys(chat_key))
        removed = False
        # ⛔ 2026-09-16 修（已知现象：「记忆那里也是删除了还能读取」）：
        #   根因是**口径不对称** —— `members()`（列表）在"互通"时是 `for key in self._chat_keys(chat_key)`
        #   **把所有群合并**后展示的，而这里原来只删 `chat_key` **一个群**的那一份 ⇒ 同一个人在别的群
        #   （或共享池）还留着一份 ⇒ **界面上删了、一刷新又合并出来**。
        #   ⇒ 默认档必须与读取**同一口径**：按 `_chat_keys()` 把每一份都删掉（`scope="all"`）。
        for key in keys:
            m = self._ensure_chat(key)
            if not m:
                continue
            for mk, mem in list(m.items()):
                hit = False
                if user_id:
                    hit = str(mem.get("userId")) == str(user_id)
                elif target:
                    hit = str(mem.get("name") or mem.get("userId")) == str(target).strip()
                if not hit:
                    continue
                if content:
                    before = len(mem["impressions"])
                    mem["impressions"] = [e for e in mem["impressions"] if e["content"] != content]
                    removed = removed or len(mem["impressions"]) != before
                else:
                    removed = True
                    mem["impressions"] = []
                if not mem["impressions"]:
                    m.pop(mk, None)
                    try:
                        os.remove(_member_file(key, mem.get("userId"), mem.get("name")))
                    except Exception:
                        pass
                else:
                    mem["updatedAt"] = int(__import__("time").time() * 1000)
                    _write_json(_member_file(key, mem.get("userId"), mem.get("name")), mem)
        return removed

    def elsewhere(self, chat_key: str, user_id="", target="") -> int:
        """除 `chat_key` 之外，还有几个群留着这个人的印象（给「只删本群」档做**如实提示**用）。

        为什么要它：`members()` 展示的是**互通范围内的合并视图**，所以用 `scope="this"` 只删本群时，
        界面上**那条记忆还会在**（别的群那一份还在）—— 不说清楚，用户就会以为"删了没用"（他 2026-09-16
        报的那条 bug 就是这个观感）。⇒ 只读计数，不做任何写入。
        """
        n = 0
        try:
            for key in self._chat_keys(chat_key):
                if key == chat_key:
                    continue
                for _mk, mem in (self._ensure_chat(key) or {}).items():
                    if not mem.get("impressions"):
                        continue
                    if user_id:
                        hit = str(mem.get("userId")) == str(user_id)
                    elif target:
                        hit = str(mem.get("name") or mem.get("userId")) == str(target).strip()
                    else:
                        hit = False
                    if hit:
                        n += 1
                        break
        except Exception:
            return 0
        return n

    def replace_member(self, chat_key: str, user_id: str, name: str, contents):
        uid = str(user_id or "").strip()
        if not uid:
            raise ValueError("userId 不能为空")
        m = self._ensure_chat(chat_key)
        old = m.get(uid) or {"userId": uid, "name": name, "impressions": [], "updatedAt": 0, "lastConsolidatedAt": 0}
        final_name = str(name or "").strip()[:60] or str(old.get("name") or "").strip() or uid
        now = int(__import__("time").time() * 1000)
        impressions = [{"content": str(s or "").strip()[:300], "createdAt": now}
                       for s in contents if str(s or "").strip()][:20]
        member = {"userId": uid, "name": final_name, "impressions": impressions,
                  "updatedAt": now, "lastConsolidatedAt": old.get("lastConsolidatedAt") or 0}
        _write_json(_member_file(chat_key, uid, final_name), member)
        m[uid] = member
        return member

    @staticmethod
    def _rel_time(ts_ms: int) -> str:
        """把时间戳说成人话（给"上次聊过"用）。"""
        try:
            import time as _t
            mins = int((_t.time() * 1000 - int(ts_ms or 0)) / 60000)
        except Exception:
            return ""
        if mins < 1:
            return "刚刚"
        if mins < 60:
            return "%d 分钟前" % mins
        if mins < 60 * 24:
            return "%d 小时前" % int(round(mins / 60.0))
        return "%d 天前" % int(round(mins / 1440.0))

    def _last_talk(self, member, store, chat_key: str, exclude_ids=None):
        """这位群友**本轮之前**的最后一次发言 ⇒ 给"上次聊过"用（纯读库，不花 token）。

        为什么要排除本轮触发批：本轮那几条正是"现在要回答的"，不能拿它当"上次"。
        找不到（第一次来 / 只发过图且无文字）就返回 None —— 不编内容。
        """
        if store is None:
            return None
        uid = str(member.get("userId") or "")
        name = str(member.get("name") or "")
        if not uid and not name:
            return None
        try:
            msgs = store.recent(chat_key, limit=200)
        except Exception:
            return None
        ex = {str(x) for x in (exclude_ids or [])}
        hit = None
        for m in reversed(list(msgs or [])):
            if m.get("self"):
                continue
            if str(m.get("id")) in ex:
                continue
            sid = str(m.get("sender_id") or "")
            snm = str(m.get("sender_name") or "")
            if (uid and sid == uid) or ((not uid) and name and snm == name):
                hit = m
                break
        if not hit:
            return None
        txt = " ".join(str(hit.get("text") or "").split())[:30]
        if not txt:
            txt = "（发过图片/表情）" if hit.get("media") else ""
        if not txt:
            return None
        return {"text": txt, "ts": int(hit.get("ts") or 0)}

    def format_for_prompt(self, chat_key: str, user_ids=None, store=None, exclude_ids=None) -> str:
        """给提示词用的一段"对群友的印象"。

        store/exclude_ids（可选）：传进来时，额外给每位群友带一行「上次聊过「…」（X 前）」——
        用户 2026-09-13 的需求："让他不仅能记得群友是什么人，而且记得上次聊过的话题"。
        这条**只从消息库现读**（不花 token、不凭空编）；没传 store 时行为与以前完全一致。
        """
        notes = get_config().get("member_notes") or {}
        all_members = self.members(chat_key)
        if not all_members:
            return ""
        if user_ids:
            filt = {str(u) for u in user_ids}
            picked = [m for m in all_members if not m["userId"] or str(m["userId"]) in filt]
        else:
            picked = all_members[:15]
        if not picked:
            return ""
        lines = ["【对群友的印象】"]
        for m in picked:
            who = notes.get(str(m["userId"])) or m["name"] or str(m["userId"] or "") or "某人"
            for e in m["impressions"][-3:]:
                lines.append("- %s：%s" % (who, e["content"]))
            lt = self._last_talk(m, store, chat_key, exclude_ids=exclude_ids)
            if lt:
                ago = self._rel_time(lt["ts"])
                lines.append("- %s：上次聊过「%s」%s" % (who, lt["text"], ("（%s）" % ago) if ago else ""))
        return "\n".join(lines)

    # ── 自动整理 ─────────────────────────────────────────────────────────

    def consolidation_state(self, chat_key: str):
        m = self._ensure_chat(chat_key)
        total = 0
        last = 0
        members = []
        for mem in m.values():
            total += len(mem["impressions"])
            last = max(last, mem.get("lastConsolidatedAt") or 0)
            members.append({"userId": str(mem.get("userId") or ""), "name": str(mem.get("name") or ""),
                            "count": len(mem["impressions"]),
                            "lastConsolidatedAt": mem.get("lastConsolidatedAt") or 0})
        meta = _read_json(_meta_file(chat_key), {})
        return {"lastConsolidatedAt": meta.get("lastConsolidatedAt") or last,
                "counts": {"memberImpression": total}, "members": members}

    def mark_consolidated(self, chat_key: str, at=None, user_ids=None):
        at = int(at or __import__("time").time() * 1000)
        os.makedirs(_chat_dir(chat_key), exist_ok=True)
        prev = _read_json(_meta_file(chat_key), {})
        _write_json(_meta_file(chat_key), {**prev, "lastConsolidatedAt": at})
        m = self._ensure_chat(chat_key)
        for uid in (user_ids or []):
            key = str(uid or "").strip()
            mem = m.get(key)
            if not mem:
                continue
            mem["lastConsolidatedAt"] = at
            _write_json(_member_file(chat_key, mem.get("userId"), mem.get("name")), mem)
