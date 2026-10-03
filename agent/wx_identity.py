# -*- coding: utf-8 -*-
"""微信身份解析：从库里直接拿"谁发的"和"该叫他什么"。

★ 本模块存在的理由
   以前"这条消息是谁发的"要靠**认画面**（读绿底行、比头像、对名字）——这正是同事反馈里
   「**OCR 认错人**」的根：同一个人换群名片、改昵称、或者名字没被看清，就会被认成别人。
   而微信自己**在库里记得死死的**：消息正文带发送者 wxid、消息行带 real_sender_id、
   联系人库带昵称/备注、群聊的 ext_buffer 里带**群名片**。
   ⇒ 本模块把这些读出来，**认人不再依赖 OCR**；OCR 只留作"库里没有"时的兜底。

★ 四类名字
   ① `username`   微信 ID（wxid_xxx / xxx@chatroom / 系统号）——库里的主键，**永久不变**
   ② `nick_name`  对方**自设的微信昵称**——对方随时可改，库里是**快照**
   ③ `remark`     我方给设的**备注名**——只有自己给某人设过备注才有值
   ④ `room_card`  群名片（**只在群里显示的名字**）——存在 `chat_room.ext_buffer`（protobuf）

★ 显示名优先级（`display_name`）
   群里先看群名片，再按 remark → nick_name → username；私聊无群名片，从 remark 起。
   这与微信界面上看到的顺序一致，所以"我们说的名字"和"用户眼睛看到的"是同一个。

★ ext_buffer 格式（实测确认，protobuf）
   ```
   顶层  field 1 (repeated) ─┐
     成员 field 1 = username (string)
          field 2 = 群名片    (string，可为空)
          field 3 = 邀请人/变更人 wxid
          field 4 = 变更时间戳
   ```
   自己写了个**最小 varint/protobuf 读法**——不为一个字段引 protobuf 依赖。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_RE_SYS = re.compile(r"^(gh_|weixin$|notifymessage$|fmessage$|medianote$|newsapp$|filehelper$|mmo9|weclaw)")


# ─────────────────────────── 最小 protobuf 读法 ───────────────────────────

def _read_varint(b: bytes, i: int):
    """返回 (值, 新下标)；读到尾或畸形返回 (None, i)。"""
    r = 0
    s = 0
    n = len(b)
    while i < n:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        if not (c & 0x80):
            return r, i
        s += 7
        if s > 63: # 畸形 varint（>64 位）——不许无限读
            return None, i
    return None, i


def _fields(b: bytes, i: int = 0, end: int = None):
    """把一段 protobuf 拆成 {字段号: [(wire_type, 值), ...]}。

    只实现用得到的 wire type：0=varint、2=长度前缀。其余（定长 64/32 位）照长度跳过，
    跳过不了就停 —— **宁可少读几个字段，也不许读串**。
    """
    if end is None:
        end = len(b)
    out = {}
    while i < end:
        key, i = _read_varint(b, i)
        if key is None:
            break
        f, w = key >> 3, key & 7
        if f == 0: # 字段号 0 非法 ⇒ 后面不可信，停
            break
        if w == 0:
            v, i = _read_varint(b, i)
            if v is None:
                break
        elif w == 2:
            L, i = _read_varint(b, i)
            if L is None or L < 0 or i + L > end:
                break
            v = b[i:i + L]
            i += L
        elif w == 5:
            if i + 4 > end:
                break
            v = b[i:i + 4]
            i += 4
        elif w == 1:
            if i + 8 > end:
                break
            v = b[i:i + 8]
            i += 8
        else: # wire type 非法
            break
        out.setdefault(f, []).append((w, v))
    return out


def parse_room_members(blob: bytes) -> dict:
    """解群聊 `ext_buffer` ⇒ `{username: 群名片}`（群名片为空也保留键，值为 ""）。

    非群 / 空 blob / 格式变了 ⇒ 返回空 dict（**绝不抛**：这是锦上添花的字段，
    读不到名字就退回昵称，不该把整条链打断）。
    """
    out = {}
    if not blob:
        return out
    try:
        top = _fields(bytes(blob))
        for w, rec in top.get(1, []):
            if w != 2 or not isinstance(rec, (bytes, bytearray)):
                continue
            m = _fields(rec)
            u = m.get(1, [(2, b"")])[0][1]
            nm = m.get(2, [(2, b"")])[0][1]
            if not isinstance(u, (bytes, bytearray)):
                continue
            try:
                u = u.decode("utf-8")
            except Exception:
                continue
            if not u:
                continue
            try:
                nm = nm.decode("utf-8") if isinstance(nm, (bytes, bytearray)) else str(nm or "")
            except Exception:
                nm = ""
            out[str(u)] = nm
    except Exception as e:
        log.debug("解群名片失败：%s", e)
    return out


# ─────────────────────────── 从库读四类名字 ───────────────────────────

def _contact_rel(db):
    try:
        from . import replica_adapter as _ra
        return _ra.contact_db_rel(db)
    except Exception:
        return None


def load_identity(db) -> dict:
    """一次读全：`{"names": {username: {...}}, "rooms": {room_username: {username: 群名片}}}`。

    `names[u]` 字段：`nick`（自设昵称）/ `remark`（我方备注）/ `alias`（微信号）/
    `kind`（'group'|'system'|'stranger'）。

    ⛔ 读不到（库打不开 / 表不存在）⇒ **返回空结构并记一条日志**，不抛：
       调用方是消息归一化主链，"认不出名字"只该退化，不该让消息处理整个失败。
    """
    from . import replica_adapter as _ra
    out = {"names": {}, "rooms": {}}
    rel = _contact_rel(db)
    if rel is None:
        log.debug("load_identity：找不到 contact.db 分片")
        return out
    conn = None
    try:
        conn = _ra.open_shard(db, rel)
        if conn is None:
            return out
        for r in conn.execute("SELECT username, nick_name, remark, alias FROM contact"):
            u = str(r["username"] or "")
            if not u:
                continue
            out["names"][u] = {
                "nick": str(r["nick_name"] or ""),
                "remark": str(r["remark"] or ""),
                "alias": str(r["alias"] or ""),
                "kind": ("group" if u.endswith("@chatroom")
                         else ("system" if _RE_SYS.match(u) else "stranger")),
            }
        # 群名片：room_username -> {member_username: 群名片}
        try:
            for r in conn.execute("SELECT username, ext_buffer FROM chat_room"):
                ru = str(r["username"] or "")
                if ru:
                    out["rooms"][ru] = parse_room_members(r["ext_buffer"] or b"")
        except Exception as e:
            log.debug("读 chat_room 失败（群名片不可用，其余照常）：%s", e)
    except Exception as e:
        log.debug("load_identity 读库失败：%s", e)
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    return out


class IdentityMap:
    """一次加载、随取随用的身份表。**线程安全**（只读快照，重建时整体替换引用）。"""

    def __init__(self, data: dict = None):
        self._data = data or {"names": {}, "rooms": {}}
        self._wxid = ""      # 机器人自己的 wxid
        self._self_nick = "" # 机器人自己的昵称

    # ── 构造 ────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, db, wxid: str = "", self_nick: str = ""):
        m = cls(load_identity(db))
        m._wxid = str(wxid or "")
        m._self_nick = str(self_nick or "")
        return m

    def reload(self, db) -> "IdentityMap":
        """整表重读（换号 / 有人改备注 / 新进群之后调一次）。"""
        self._data = load_identity(db)
        return self

    @property
    def raw(self) -> dict:
        return self._data

    # ── 查询 ────────────────────────────────────────────────────────────
    def has(self, username: str) -> bool:
        return str(username or "") in self._data.get("names", {})

    def info(self, username: str) -> dict:
        """该 username 的原始三件套（没有就给空串，**不抛**）。"""
        return self._data.get("names", {}).get(str(username or ""), {}) or {}

    def nick(self, username: str) -> str:
        """**对方自设的微信昵称**（对方改了就变；库里是上次同步的快照）。"""
        return str(self.info(username).get("nick") or "")

    def remark(self, username: str) -> str:
        """**我方给设的备注名**（没设过 ⇒ 空串）。"""
        return str(self.info(username).get("remark") or "")

    def room_card(self, room: str, username: str) -> str:
        """**群名片**（只在那个群里的显示名；没设/不在群 ⇒ 空串）。"""
        return str((self._data.get("rooms", {}).get(str(room or ""), {}) or {})
                   .get(str(username or ""), "") or "")

    def display_name(self, username: str, room: str = "") -> str:
        """按**微信界面上的顺序**给名字：群里 群名片 → 备注 → 昵称 → wxid。

        为什么是这个顺序：用户眼睛看到的是这个名字，我们跟着用同一个，
        才不会出现"日志说张三、用户看到李四"。
        """
        u = str(username or "")
        if not u:
            return ""
        if u == self._wxid and self._self_nick:
            base = self._self_nick
        else:
            base = ""
        if room:
            c = self.room_card(room, u)
            if c:
                return c
        if base:
            return base
        i = self.info(u)
        return str(i.get("remark") or i.get("nick") or u)

    def is_known(self, username: str) -> bool:
        """库里有这个人（含系统号）⇒ 名字可信；返回 False 表示名字来源不可靠。"""
        u = str(username or "")
        return bool(u) and (u in self._data.get("names", {})
                            or u == self._wxid
                            or any(u in (v or {}) for v in self._data.get("rooms", {}).values()))

    # ── 统计 ────────────────────────────────────────────────────────────
    def stats(self) -> dict:
        names = self._data.get("names", {})
        rooms = self._data.get("rooms", {})
        return {
            "contacts": len(names),
            "with_remark": sum(1 for v in names.values() if v.get("remark")),
            "with_nick": sum(1 for v in names.values() if v.get("nick")),
            "groups": sum(1 for k in names if k.endswith("@chatroom")),
            "rooms_with_cards": sum(1 for v in rooms.values() if any(v.values())),
            "cards": sum(len([x for x in v.values() if x]) for v in rooms.values()),
        }
