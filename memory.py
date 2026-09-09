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
    os.makedirs(os.path.dirname(file), exist_ok=True)
    tmp = file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, file)


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

    def remove(self, chat_key: str, category: str, user_id="", target="", content=""):
        if category != "memberImpression":
            return False
        m = self._ensure_chat(chat_key)
        removed = False
        for key, mem in list(m.items()):
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
                m.pop(key, None)
                try:
                    os.remove(_member_file(chat_key, mem.get("userId"), mem.get("name")))
                except Exception:
                    pass
            else:
                mem["updatedAt"] = int(__import__("time").time() * 1000)
                _write_json(_member_file(chat_key, mem.get("userId"), mem.get("name")), mem)
        return removed

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

    def format_for_prompt(self, chat_key: str, user_ids=None) -> str:
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
