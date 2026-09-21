"""适配层单测（W1 判据）——不碰微信、不碰真数据库：用假 DB 复现"跨分片 / 老版本 / 公开 API 优先"三条路径。

跑法：  py -3 scripts/replica_adapter_selftest.py     （退出码 0=全过 / 1=有失败）
"""
from __future__ import annotations

import importlib.metadata as md
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:   # Windows 控制台默认 GBK：不切 UTF-8 的话，印中文/符号会 UnicodeEncodeError 崩掉
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import replica_adapter as ra  # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


class Row(dict):
    """支持 r["col"] 与 r[0] 两种取法（真库返回 sqlite3.Row）。"""

    def __init__(self, pairs):
        super().__init__(pairs)
        self._vals = [v for _, v in pairs]

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._vals[k]
        return dict.__getitem__(self, k)


class FakeConn:
    def __init__(self, kind, rows, log):
        self.kind = kind
        self.rows = rows
        self.log = log
        self.closed = False

    def execute(self, sql, params=()):
        self.log.append((self.kind, sql.strip()[:40], params))
        rows = self.rows
        if "server_id=?" in sql:
            pass          # 该分片有没有这条记录由 FakeDB 预置（真库由 SQL 过滤；行里只放 SELECT 出的列）
        elif "@chatroom" in sql:
            rows = [r for r in rows if str(r.get("username", "")).endswith("@chatroom")]
            self.rows = rows   # 让 fetchall/fetchone 看到过滤后的结果
        return self

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        self.closed = True


class FakeDB:
    """1.2.x 风格：有 _msg_conns（跨分片）+ _db_files + _open + get_groups。"""

    def __init__(self, with_get_groups=True):
        self.log = []
        self.contact_rows = [Row([("username", "wxid_a"), ("nick_name", "昵称A"), ("remark", "备注A")]),
                             Row([("username", "wxid_b"), ("nick_name", "昵称B"), ("remark", "")]),
                             Row([("username", "12345@chatroom"), ("nick_name", "群一"), ("remark", "")])]
        # 分片 1 没有该 server_id，分片 2 有 ⇒ 旧写法（只看第一个分片）必然查不到
        self.shard_rows = [{}, {"server_id": 777, "local_id": 42}]
        self.conns = []
        self._db_files = [("contact.db", os.path.join("db", "contact.db"), 1),
                          ("message_0.db", os.path.join("db", "message_0.db"), 2)]
        if not with_get_groups:
            # 模拟老库没有公开接口：删掉类上的方法
            self.__class__ = type("FakeDBNoGroups", (FakeDB,), {"get_groups": None})

    def _open(self, rel):
        if rel == "contact.db":
            c = FakeConn("contact", self.contact_rows, self.log)
        else:
            idx = 0 if rel.endswith("0.db") else 1
            c = FakeConn("shard" + str(idx), [Row([("local_id", 42)])] if idx == 1 else [], self.log)
        self.conns.append(c)
        return c

    def _msg_conns(self, user):
        # 真库返回 [(conn, table)]；分片 0 没有我们要的 server_id，分片 1 有
        return [(self._open("message_0.db"), "Msg_a"), (self._open("message_1.db"), "Msg_a")]

    def _msg_conn(self, user):
        return self._open("message_0.db")

    def get_groups(self):
        return [{"username": "12345@chatroom", "name": "群一", "owner": "wxid_a", "member_count": 3, "members": []}]


class OldFakeDB(FakeDB):
    """1.1.x 风格：只有 _msg_conn、没有 _msg_conns、没有 get_groups。"""

    def __init__(self):
        super().__init__(with_get_groups=False)
        self._msg_conns = None
        self.get_groups = None


def main():
    print("== A. version policy ==")
    ok("ver_tuple 逐段可比", ra.ver_tuple("1.2.2.2") == (1, 2, 2, 2) and ra.ver_tuple("1.1.5.1") < ra.ver_tuple("1.2.2.2"))
    real = md.version
    try:
        for v, lvl, want_ok in (("1.1.5.1", "ok", True), ("1.2.2.2", "ok", True), ("1.0.9", "too_old", False), ("1.9.0", "untested", True)):
            md.version = lambda pkg, _v=v: _v
            rep = ra.version_report()
            ok("版本 %s ⇒ %s（ok=%s）" % (v, lvl, want_ok), rep["level"] == lvl and rep["ok"] is want_ok, rep["note"][:48])
        md.version = lambda pkg: (_ for _ in ()).throw(Exception("not installed"))
        ok("未安装 ⇒ missing 且 ok=False", ra.version_report()["level"] == "missing")
    finally:
        md.version = real

    print("== B. private API funnel (shards / contact lookup) ==")
    db = FakeDB()
    ok("iter_shards 枚举 2 个分片", len(ra.iter_shards(db)) == 2)
    ok("contact_db_rel 定位 contact.db", ra.contact_db_rel(db) == "contact.db")
    ok("capabilities 报出能力位", ra.capabilities(db)["msg_conns"] and ra.capabilities(db)["db_files"])
    m = ra.load_nickname_map(db)
    ok("昵称映射：备注优先、回退昵称", m.get("wxid_a") == "备注A" and m.get("wxid_b") == "昵称B", len(m))

    print("— C. 群列表：公开 API 优先，缺失才回退私有 —")
    dbg = FakeDB()                       # 用新实例：上一个用例已经查过 contact.db，日志里会带噪音
    g = ra.load_groups(dbg)
    ok("有 get_groups ⇒ 走公开 API", g and g[0]["wxid"] == "12345@chatroom" and g[0]["name"] == "群一")
    ok("走公开 API 时**没有**碰 contact.db", all(k != "contact" for k, _, _ in dbg.log), str([k for k, _, _ in dbg.log]))
    db2 = FakeDB(with_get_groups=False)
    g2 = ra.load_groups(db2)
    ok("没有 get_groups ⇒ 回退查 contact.db 也能拿到群", g2 and g2[0]["wxid"] == "12345@chatroom", str(g2)[:60])

    print("— C2. 读库失败**必须抛**（2026-09-20 网友报「微信已连接却找不到群聊」的根因）—")

    class BrokenDB(FakeDB):
        """两条路都失败：公开接口抛 + contact.db 也打不开。"""
        def get_groups(self):
            raise RuntimeError("database is locked")

        def _open(self, rel):
            raise RuntimeError("database is locked")

    class EmptyGroupsDB(FakeDB):
        """查询**成功**但确实一个群都没有（这是事实，不是错误）。"""
        def __init__(self):
            super().__init__()
            self.contact_rows = []

        def get_groups(self):
            return []

    class NoShardDB(FakeDB):
        """连 contact.db 都定位不到（顺便把公开接口也去掉 ⇒ 只能走"回退查库"那条路）。
        V-R7-11：原来这个替身还留着可用的 `get_groups`，于是 `load_groups` 走公开接口就返回了、
        永远碰不到 `if rel is None: raise` 那一行（把 raise 改成 `return []` 判据仍全绿）。"""
        def __init__(self):
            super().__init__(with_get_groups=False)
            self._db_files = []

    try:
        ra.load_groups(BrokenDB())
        _g_raised = False
    except Exception:
        _g_raised = True
    ok("get_groups 抛 + contact.db 打不开 ⇒ **抛**（旧版本在这里吞成「0 个群」）", _g_raised)
    try:
        _empty = ra.load_groups(EmptyGroupsDB())
        _e_raised = False
    except Exception:
        _e_raised = True
    ok("查询成功但没有群 ⇒ 返回空列表且**不抛**（那是事实，不是错误）",
       (not _e_raised) and _empty == [], "raised=%s got=%r" % (_e_raised, _empty))
    try:
        ra.load_privates(NoShardDB())
        _p_raised = False
    except Exception:
        _p_raised = True
    ok("load_privates：定位不到 contact.db ⇒ **抛**（不许吞成「没有私聊联系人」）", _p_raised)
    # V-R7-11：原先只有 load_privates 那条守着「定位不到 contact.db」这一路，
    # load_groups 里的同名守卫变异成 `return []` 时判据仍全绿 ⇒ 这里补上同一条守卫的断言（消息里要说出 contact.db）
    try:
        ra.load_groups(NoShardDB())
        _gg_raised, _gg_why = False, ""
    except Exception as _ge:
        _gg_raised, _gg_why = True, "%s: %s" % (type(_ge).__name__, _ge)
    ok("load_groups：定位不到 contact.db ⇒ **抛**且说清是 contact.db（不许静默返回「0 个群」）",
       _gg_raised and "contact.db" in _gg_why, _gg_why)

    print("— D. 跨分片查询：旧写法的静默少查已修 —")
    db3 = FakeDB()
    conns = ra.message_conns(db3, "wxid_a")
    ok("1.2.x 走 _msg_conns（拿到全部分片）", len(conns) == 2, "conns=" + str(len(conns)))
    ra.close_all([c for c, _ in conns])
    ok("close_all 真关掉连接", all(c.closed for c in db3.conns))
    db4 = FakeDB()
    lid = ra.find_server_id_local_id(db4, "wxid_a", 777)
    ok("在**第二个分片**里找到了 local_id（旧写法只看第一个 ⇒ 找不到）", lid == 42, "local_id=" + str(lid))
    db5 = OldFakeDB()
    conns5 = ra.message_conns(db5, "wxid_a")
    ok("老版本（无 _msg_conns）退化成单分片且不炸", len(conns5) == 1, "conns=" + str(len(conns5)))

    print("— E. 自检行 —")
    rows = ra.selfcheck()
    ok("selfcheck 返回结论行且第一项是版本", rows and rows[0]["item"] == "适配层版本", (rows[0]["detail"][:44] if rows else ""))

    print("— F. 懒创建的新分片没有密钥 ⇒ 补一次再读，仍不行就跳过（网友 A 的 KeyError 复现）—")

    class _ShardDB(object):
        """假 DB：分片表与密钥表**分离** —— 复现"新分片有文件、没密钥"。"""

        def __init__(self, heal=True):
            self._db_files = [("contact.db", "C:/x/contact.db", 10),
                              ("message\\media 1.db", "C:/x/media 1.db", 20)]
            self._keys = {"contact.db": b"k"}          # 故意缺 media 1.db
            self.heal = heal
            self.refreshed = 0
            self.extracted = 0
            self.opens = []

        def _key_works(self, rel):
            return rel in self._keys

        def _refresh_db_files(self):
            self.refreshed += 1

        def _load_or_extract_keys(self):
            self.extracted += 1
            if self.heal:
                self._keys["message\\media 1.db"] = b"k2"

        def _open(self, rel):
            if rel not in self._keys:
                raise KeyError(rel)
            self.opens.append(rel)
            return "CONN:" + rel

    _sd = _ShardDB()
    ok("missing_key_shards 点出没密钥的那个分片",
       ra.missing_key_shards(_sd) == ["message\\media 1.db"], str(ra.missing_key_shards(_sd)))
    _c = ra.open_shard(_sd, "message\\media 1.db")
    ok("撞上 KeyError ⇒ **先补一次密钥再试**，补到就正常返回",
       _c == "CONN:message\\media 1.db" and _sd.extracted == 1, "%s / extracted=%s" % (_c, _sd.extracted))
    ok("好分片照旧一次到位（不白刷）", ra.open_shard(_sd, "contact.db") == "CONN:contact.db" and _sd.extracted == 1,
       "extracted=%s" % _sd.extracted)
    _sd2 = _ShardDB(heal=False)
    ok("补了也补不到 ⇒ 返回 None（**不抛**，让调用方跳过这个分片）",
       ra.open_shard(_sd2, "message\\media 1.db") is None and _sd2.extracted == 1, "extracted=%s" % _sd2.extracted)
    _rep = ra.refresh_shards(_ShardDB())
    ok("refresh_shards 回报 missing（接入时据此记日志/显示）",
       _rep.get("missing") == [] and _rep.get("refreshed") is True, str(_rep))

    print("— G. 二级兜底：补不到密钥的分片**从分片表摘掉**（换整条链可用，但要留痕）—")
    _sd3 = _ShardDB(heal=False)
    _r3 = ra.refresh_shards(_sd3)
    ok("摘掉的是补不到密钥的那个分片", _r3.get("dropped") == ["message\\media 1.db"], str(_r3.get("dropped")))
    ok("摘完之后它能用的分片都不缺密钥", _r3.get("missing") == [] and ra.missing_key_shards(_sd3) == [],
       str(_r3.get("missing")))
    ok("分片表里只剩拿得到密钥的那个（驱动库内部遍历不再撞 KeyError）",
       [r for r, _p in ra.iter_shards(_sd3)] == ["contact.db"], str(ra.iter_shards(_sd3)))
    _sd4 = _ShardDB(heal=False)
    _sd4._keys = {}                       # 全部都没密钥 ⇒ **不许把分片表清空**
    _r4 = ra.refresh_shards(_sd4)
    ok("一个都补不到时**不清空**分片表（安全线）",
       _r4.get("dropped") == [] and len(ra.iter_shards(_sd4)) == 2, "%s / %d" % (_r4.get("dropped"), len(ra.iter_shards(_sd4))))

    print("── V-R9-33：zstd 压缩正文还原（真 zstd 帧 ⇒ 真正文；这一族以前**零判据**）──")
    # 为什么要有它：微信 4.x 把**长文本/文件卡**的 content 用 zstd 压缩存库，还原走
    # `recover_text`/`message_text`/`fill_text`。审计实测：把这三个函数改坏，本判据
    # **31/0 全绿** ⇒ 用户粘来的长段落会被读成 `[文本]`、机器人"看不见"内容（正对反馈里的
    # "读得到/读不到都是这一环"）。
    try:
        _z = ra.zstd_module()
        if _z is None:
            ok("zstd 模块可用（驱动库自带 zstandard）", False,
               "runtime 里没有 zstandard ⇒ 长消息还原这族在本机不可测（**不是通过**）")
        else:
            _long = ("这是一条超过九百个字符的长消息，用来验证压缩正文能被完整还原。" * 40)
            _frame = _z.ZstdCompressor().compress(_long.encode("utf-8"))
            ok("zstd 帧的 magic 对得上（`28 b5 2f fd`）", bytes(_frame[:4]) == ra.ZSTD_MAGIC,
               bytes(_frame[:4]).hex())
            ok("`recover_text` 把长消息**原样还原**（不是 `[文本]` 占位）",
               ra.recover_text(_frame) == _long, "还原 %d 字 / 原文 %d 字"
               % (len(ra.recover_text(_frame) or ""), len(_long)))
            ok("已经是 str ⇒ 原样返回", ra.recover_text("普通短消息") == "普通短消息")
            ok("拿不到 / 解不开 ⇒ 空串（不抛异常、也不给占位符）",
               ra.recover_text(None) == "" and ra.recover_text(b"") == ""
               and ra.recover_text(b"\x28\xb5\x2f\xfd-not-a-frame") == "")

            _db = type("D", (), {"get_message_row": staticmethod(
                lambda c, l: {"content": None, "compress_content": _frame})})()
            ok("`message_text` 走同一条还原路径（压缩帧在 compress_content 里）",
               ra.message_text(_db, "group:g", 703) == _long,
               len(ra.message_text(_db, "group:g", 703) or ""))
            ok("`fill_text`：友好化给的是 `[文本]` 而库里有长正文 ⇒ 用正文换回去",
               ra.fill_text(_db, "group:g", 703, "[文本]") == _long)
            ok("`fill_text`：本来就不是类型标签 ⇒ 原样返回（不乱查库）",
               ra.fill_text(_db, "group:g", 703, "这就是正文") == "这就是正文")
            # 反例锚：把 zstd 拿掉 ⇒ 上面那两条"还原"必须变红（证明这组断言有区分力）
            _orig_z = ra.zstd_module
            try:
                ra.zstd_module = lambda: None
                ok("反例锚：zstd 不可用时 `recover_text` 只给空串（上面那条确实在守还原）",
                   ra.recover_text(_frame) == "")
            finally:
                ra.zstd_module = _orig_z
    except Exception as _e_z:
        ok("zstd 正文还原这组能跑起来", False, str(_e_z)[:120])

    print("\n== 适配层单测：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
