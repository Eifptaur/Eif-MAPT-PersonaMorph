# -*- coding: utf-8 -*-
"""群友印象记忆：每个会话一个文件夹，每个群友一个以 wxid 命名的 JSON 文件。

目录结构：
  data/memory/group_<wxid>/<wxid>.json
  data/memory/group_<wxid>/_meta.json
每个成员文件：{userId, name, impressions: [{content, createdAt}], updatedAt, lastConsolidatedAt}
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from . import persist
from .config import DATA_DIR, get_config

log = logging.getLogger(__name__)

MEMORY_DIR = os.path.join(DATA_DIR, "memory")

#: 覆盖审计面（对标 mem0 的 history 表；来源与理由见 `research\对标-mem0.md` §五 L1）。
#  ⛔ 故意**不放在** `MEMORY_DIR` 下面：`_chat_keys()` 把 `MEMORY_DIR` 的每个子目录都当"一个群"
#  （跨群互通的枚举口径），往里塞一个 `_history` 就等于凭空多出一个群。
HISTORY_DIR = os.path.join(DATA_DIR, "memory_history")


#: 目录名长度上限（字符）。Windows 单个文件名的字节上限是 255，而中文/emoji 在 UTF-8 里
#  一个字占 3~4 字节 ⇒ 取 80 字符能保证最坏情况（全 4 字节）也只有 320 字节……所以再压到 60：
#  60×4=240 < 255，安全。超长的一律截断 + 挂 8 位哈希尾巴（唯一性由哈希保证，见 `_chat_dir_name`）。
_DIR_NAME_MAX = 60


def _chat_dir_name(chat_key: str) -> str:
    """会话 key → 目录名（**转义 + 定长 + 短哈希**）。

    ⛔ 两代缺陷叠在这一个函数上：
    ① 最早只做转义、不截断 ⇒ 群名一长就炸（Windows 单文件名上限 255 **字节**，中文/emoji
       一个字占 3~4 字节 ⇒ ~85 字的中文群名就让 `os.makedirs` 抛 `OSError: WinError 123`），
       记忆整个写不进去；
    ② 修① 时只在**超长**时挂哈希，短 key 只做转义 ⇒ **不同 chat_key 会撞同一个目录**：
       `group:wxid_a-b` 与 `group:wxid_a_b` 都归一成 `group_wxid_a_b` ⇒ 两个群的印象/记忆
       写进同一份文件（用户视角＝"两个群的记忆串了"，与 `store.chat_file()` 当年那个
       "两个会话共用一个档案、互相覆盖"是同一个病）。
    ⇒ 现在**一律**挂 `_key_hash(chat_key)` 的 8 位尾巴（与 `store.chat_file()` 同口径）：
       归一化只用来"给人看"，唯一性由哈希保证。老目录靠 `_chat_dir()` 的向后兼容继续用。
    """
    from .keys import safe_name as _safe_name_impl
    safe = _safe_name_impl(chat_key)
    if len(safe) > _DIR_NAME_MAX:
        safe = safe[:_DIR_NAME_MAX]
    return "%s_%s" % (safe, _key_hash(chat_key))


def _chat_dir_name_legacy(chat_key: str) -> str:
    """**只有超长才挂哈希**的老命名（向后兼容用，不写新数据）。"""
    safe = re.sub(r"[^a-z0-9_]", "_", str(chat_key), flags=re.IGNORECASE)
    if len(safe) <= _DIR_NAME_MAX:
        return safe
    return "%s_%s" % (safe[:_DIR_NAME_MAX], _key_hash(chat_key))


def _key_hash(chat_key: str) -> str:
    from .keys import key_hash

    return key_hash(chat_key)


def _member_file_name(user_id: str, name: str = "") -> str:
    """成员档名。**必须定长**：wxid 通常很短，但异常输入（或含中文/emoji 的 uid）
    一样会撞上 Windows 单文件名 255 字节上限 ⇒ 超过 `_DIR_NAME_MAX` 的截断 + 挂哈希尾巴。
    """
    if str(user_id or "").strip():
        uid = str(user_id).strip()
        if re.match(r"^[\w-]+$", uid) and len(uid) <= _DIR_NAME_MAX:
            return uid + ".json"
        safe = re.sub(r"[^a-z0-9_]", "_", uid, flags=re.IGNORECASE)
        if len(safe) <= _DIR_NAME_MAX:
            return "u_" + safe + ".json"
        return "u_%s_%s.json" % (safe[:_DIR_NAME_MAX], _key_hash(uid))
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

    老写法共用 `<file>.tmp` 这个名字 ⇒ 并发写同一个成员档（巡检线程/多开实例）会
    **打开同一个临时文件**互相穿插出半截 JSON（审计实测 200 次并发写坏 46 个），而读侧 `_read_json`
    又静默回 fallback ⇒ 印象全没了。失败仍然抛（不吞），调用方的既有语义不变。
    """
    if not persist.atomic_write_json(file, value, indent=1):
        raise OSError("记忆落盘失败（原档未动）：%s" % file)


def _chat_dir(chat_key: str) -> str:
    """会话的记忆目录：**老目录优先沿用**，没有才用新命名建。

    ⛔ 为什么必须认老目录：目录名换新命名（一律挂哈希）之后，已有用户的旧目录若不认，
    读侧会看到空目录 ⇒ 用户视角＝"印象/记忆全没了"。所以这里先看老命名是否已经在磁盘上，
    在就继续用它（读写闭环不变、零迁移风险）；新会话才落新命名（唯一性由哈希保证，
    从此不会再出现"两个群共用一份记忆"）。
    """
    legacy = os.path.join(MEMORY_DIR, _chat_dir_name_legacy(chat_key))
    if os.path.isdir(legacy):
        return legacy
    return os.path.join(MEMORY_DIR, _chat_dir_name(chat_key))


def _history_file(chat_key: str) -> str:
    """会话的历史档（`.jsonl`）：与 `_chat_dir()` 同口径 —— 老文件优先沿用。"""
    legacy = os.path.join(HISTORY_DIR, "%s.jsonl" % _chat_dir_name_legacy(chat_key))
    if os.path.exists(legacy):
        return legacy
    return os.path.join(HISTORY_DIR, "%s.jsonl" % _chat_dir_name(chat_key))


def audit_overwrite(chat_key: str, user_id: str, before, after) -> dict:
    """整份覆盖**之前**记一笔（只追加）：丢了哪些旧印象、多了哪些新印象。

    为什么要有：`replace_member()` 是**整份覆盖**，
    唯一守门是调用方的长度比较（`len(新) <= len(旧)`）⇒ 模型整理时"顺手少写一条"就是**静默丢**：
    旧条目直接没了，没有任何痕迹、也捞不回来。这里只做**机械集合差**（逐字比对，
    不做语义猜测、不改任何存储行为）；审计本身失败绝不打断主流程。
    """
    b = [str(x) for x in (before or [])]
    a = [str(x) for x in (after or [])]
    rec = {"at": int(time.time() * 1000), "userId": str(user_id or ""),
           "before": len(b), "after": len(a),
           "dropped": [x for x in b if x not in a], "added": [x for x in a if x not in b]}
    if not (rec["dropped"] or rec["added"]):
        return rec
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        with open(_history_file(chat_key), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("记忆覆盖审计没写下去（主流程不受影响）：%s", e)
    return rec


def history(chat_key: str, limit: int = 50) -> list:
    """读回这个群的覆盖审计（旧 → 新，最多 `limit` 条）。读不出来就返回空表，不抛。"""
    out = []
    try:
        with open(_history_file(chat_key), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("记忆覆盖审计读不出来：%s", e)
        return []
    return out[-max(1, int(limit)):]


def _data_dir() -> str:
    """记忆根目录（互通池枚举用）。"""
    return MEMORY_DIR


def _unname_dir(name: str) -> str:
    """把加密目录名还原为原始 chat_key 近似值（缺省 = 原名，够用于 pool 合并）。"""
    return str(name or "").replace("memory_", "", 1)


def _chat_dir_meta_path(dir_name: str) -> str:
    return os.path.join(MEMORY_DIR, dir_name, "_meta.json")


def chat_keys_on_disk() -> list:
    """磁盘上**实际有印象数据**的会话 chat_key（不依赖消息库）。

    ⛔ 为什么要有：记忆落盘在 `data/memory/`，与消息档案库（`data/messages/`）是**两套独立存储**。
    上层若只用 `store.list_chats()` 枚举记忆页的群下拉，那么"消息库读不到 / 某群只收过消息没被
    监听落档 / 手动清过 messages 目录"时，用户**明明有印象却看不到、选不中、删不掉** —— 数据在，
    界面说"没有"。所以这里直接以记忆目录为准枚举。

    目录名是 `_chat_dir_name()` 的**单向转义**（`group:x@chatroom` → `group_x_chatroom`），
    无法反推 ⇒ 优先取 `_meta.json` 里回写的原始 `chat_key`；老档案没有这一项时退回
    「文件名归一半还原」（`group_x_chatroom` → `group:x_chatroom`），够用于展示与选择
    （`_chat_dir` 再转义回去仍是同一个目录 ⇒ 读写闭环成立）。
    """
    out = []
    seen = set()
    try:
        names = sorted(os.listdir(MEMORY_DIR))
    except OSError:
        return []
    for fn in names:
        p = os.path.join(MEMORY_DIR, fn)
        if not os.path.isdir(p) or fn in (".", ".."):
            continue
        # 这个目录里是否有真印象档（空壳目录不算一个"群"）
        has_data = False
        meta = {}
        try:
            for sub in os.listdir(p):
                if sub == "_meta.json":
                    meta = _read_json(os.path.join(p, sub), {}) or {}
                    continue
                if not sub.endswith(".json"):
                    continue
                raw = _read_json(os.path.join(p, sub), None)
                if raw and (raw.get("impressions") or raw.get("userId") or raw.get("name")):
                    has_data = True
        except OSError:
            continue
        if not has_data:
            continue
        ck = str(meta.get("chat_key") or "").strip()
        if not ck:
            # 老档案没回写 chat_key ⇒ 从目录名归半还原。⛔ 目录名可能是**截断+哈希**的
            #   （见 `_chat_dir_name`），这种还原不回来 ⇒ 如实跳过（不编一个假 key，
            #   编了会让"选中它"落到别的目录）。新写入路径都会回写，故只影响老档案。
            base = fn
            m = re.match(r"^(group|private)_(.+)_([0-9a-f]{8})$", base)
            if m and _key_hash("%s:%s" % (m.group(1), m.group(2))) == m.group(3):
                # 尾巴真是我们挂的哈希 ⇒ 原名被截断过，反推不回来
                log.debug("记忆目录 %s 是截断名且无 _meta.chat_key，跳过（不编假 key）", fn)
                continue
            m = re.match(r"^(group|private)_(.+)$", base)
            ck = "%s:%s" % (m.group(1), m.group(2)) if m else fn
        if ck and ck not in seen:
            seen.add(ck)
            out.append(ck)
    return out


def _member_file(chat_key: str, user_id: str, name: str = "") -> str:
    return os.path.join(_chat_dir(chat_key), _member_file_name(user_id, name))


def _meta_file(chat_key: str) -> str:
    return os.path.join(_chat_dir(chat_key), "_meta.json")


class MemoryStore:
    def __init__(self):
        self.cache: dict = {} # chat_key -> {user_id: member}
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
        否则 share_across_groups=true 全部互通；false 仅本群。

        ⛔ 全互通档原来直接把**目录名**当 key 返回（`group_x_chatroom`）—— 目录名是
        `_chat_dir_name()` 的单向转义，与真实 chat_key（`group:x@chatroom`）**不相等**；
        于是 `members()` / `remove()` 拿这个假 key 去 `_chat_dir()` 再转义一次，
        虽然大多数情况还能碰巧对回同一个目录，但凡是转义后不同的 key（含中文群名、大写 wxid、
        点号/连字符 wxid）就会**读到空 / 删错档**。⇒ 统一用 `chat_keys_on_disk()` 拿回真实
        chat_key（读 `_meta.json` 回写项 + 文件名归半还原），与 `members()` 的读取口径一致。
        """
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
        keys = chat_keys_on_disk()
        return keys or [chat_key]

    def _ensure_chat(self, chat_key: str) -> dict:
        if chat_key not in self.cache:
            m = {}
            cdir = _chat_dir(chat_key)
            try:
                for fn in os.listdir(cdir):
                    if not fn.endswith(".json") or fn == "_meta.json":
                        continue
                    raw = _read_json(os.path.join(cdir, fn), None)
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
            except OSError:
                # 目录不在（首次）／被换成文件／权限被收 —— 都是"这次读不到"，按空档处理，
                # 绝不把异常抛给上层（上层只是要一份成员列表，不该因存储故障整页炸掉）。
                pass
            # 顺手把原始 chat_key 补写回 `_meta.json`：老档案（没有这一项）从此可被
            # `chat_keys_on_disk()` 精确还原成真实会话，而不是退化成转义目录名。
            if m:
                try:
                    mp = _meta_file(chat_key)
                    meta = _read_json(mp, {}) or {}
                    if not str(meta.get("chat_key") or "").strip():
                        os.makedirs(cdir, exist_ok=True)
                        _write_json(mp, {**meta, "chat_key": str(chat_key)})
                except Exception:
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
        """删成员印象。`scope`＝**删除范围**：

        · `all` （默认）＝按 `_chat_keys()` 把**互通范围内的每一份都删掉** —— 与 `members()`
          的读取口径一致；
        · `this`＝**只删 `chat_key` 这一个群**里那一份（别的群还留着 ⇒ 合并视图里仍会显示，
          所以调用方必须把这件事**如实告诉用户**，见 `elsewhere()`）。
        """
        if category != "memberImpression":
            return False
        keys = [chat_key] if str(scope) == "this" else list(self._chat_keys(chat_key))
        removed = False
        # ⛔ （已知现象）：
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
                        pass  # 清理型：静默合法
                else:
                    mem["updatedAt"] = int(__import__("time").time() * 1000)
                    _write_json(_member_file(key, mem.get("userId"), mem.get("name")), mem)
        return removed

    def elsewhere(self, chat_key: str, user_id="", target="") -> int:
        """除 `chat_key` 之外，还有几个群留着这个人的印象（给「只删本群」档做**如实提示**用）。

        为什么要它：`members()` 展示的是**互通范围内的合并视图**，所以用 `scope="this"` 只删本群时，
        界面上**那条记忆还会在**（别的群那一份还在）—— 不说清楚，用户就会以为"删了没用"（他 
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
        if old.get("impressions"):
            # 整份覆盖前先记一笔（丢了什么、多了什么）——静默丢印象正是这条链的老毛病
            audit_overwrite(chat_key, uid,
                            [str(e.get("content") or "") for e in (old.get("impressions") or [])],
                            [e["content"] for e in impressions])
        member = {"userId": uid, "name": final_name, "impressions": impressions,
                  "updatedAt": now, "lastConsolidatedAt": old.get("lastConsolidatedAt") or 0}
        _write_json(_member_file(chat_key, uid, final_name), member)
        m[uid] = member
        return member

    def overwrite_history(self, chat_key: str, limit: int = 20) -> list:
        """这个群"整份覆盖过哪些印象"的审计（旧 → 新，最多 limit 条；读不出来就是空表）。

        实现是模块函数 `history()`；单独留一个方法是因为主流程（记忆整理）与记忆页都按
        `orch.memory.xxx` 的写法调用。
        """
        return history(chat_key, limit)

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
        # 记下原始 chat_key：目录名是单向转义、无法反推，靠它才能把记忆目录对回真实会话
        _write_json(_meta_file(chat_key), {**prev, "lastConsolidatedAt": at, "chat_key": str(chat_key)})
        m = self._ensure_chat(chat_key)
        for uid in (user_ids or []):
            key = str(uid or "").strip()
            mem = m.get(key)
            if not mem:
                continue
            mem["lastConsolidatedAt"] = at
            _write_json(_member_file(chat_key, mem.get("userId"), mem.get("name")), mem)

    def clear_all(self) -> int:
        """抹掉**全部**成员印象档（所有群、含互通池），返回清掉的成员档个数。

        口径与 `chat_keys_on_disk()` 一致：**以记忆目录为准**逐个群清，不依赖消息库 ——
        否则"消息库读不到 / 只有印象没有消息"的群会被漏掉（用户以为清干净了，其实还留着）。
        删除逐个成员档；`_meta.json` 只清 `lastConsolidatedAt`（保留 `chat_key` 回写项，
        否则那份原始会话标识就没了，记忆页的群名会退化成转义目录名）。
        读不动的档**不删、跳过并计数**，绝不静默当成"清掉了"。
        """
        removed = 0
        failed = 0
        tgt = list(self.cache.keys())
        try:
            for fn in os.listdir(MEMORY_DIR):
                p = os.path.join(MEMORY_DIR, fn)
                if os.path.isdir(p) and fn not in (".", ".."):
                    tgt.append(fn)
        except OSError:
            pass
        for key in tgt:
            cdir = _chat_dir(key) if key in self.cache else os.path.join(MEMORY_DIR, key)
            try:
                files = list(os.listdir(cdir))
            except OSError:
                continue
            for fn in files:
                if not fn.endswith(".json") or fn == "_meta.json":
                    continue
                fp = os.path.join(cdir, fn)
                if _read_json(fp, None) is None:
                    # 读不动（权限/被占用/坏 JSON）⇒ 不动它，如实记账
                    failed += 1
                    continue
                try:
                    os.remove(fp)
                    removed += 1
                except OSError:
                    failed += 1
            try:
                mp = os.path.join(cdir, "_meta.json")
                meta = _read_json(mp, {})
                if meta:
                    meta.pop("lastConsolidatedAt", None)
                    _write_json(mp, meta)
            except Exception:
                pass
        self.cache.clear()
        if failed:
            raise OSError("有 %d 个印象档读不动、没敢删（其余 %d 个已清）" % (failed, removed))
        return removed
