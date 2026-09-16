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

    print("\n== 适配层单测：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
