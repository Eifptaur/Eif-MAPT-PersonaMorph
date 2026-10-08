# -*- coding: utf-8 -*-
"""「打不开消息库」两个失败模式的判据。

现场原话（两个失败模式都在他那一行报错里）：
  `RuntimeError: 未找到任何已登录账号的数据库（试过 3 条路
   RuntimeError: 未找到任何已登录账号的数据库；「…\\xwechat_files」KeyError: 'message\\message_1.db'；…）`
  ⇒ ①**填到 db_storage 那一层**：驱动库只在 db_dir 底下找"带 db_storage 的账号目录"，一个都找不到；
    ②**认了账号目录之后**又 `KeyError: 'message\\message_1.db'`（密钥表里没有这个分片）。

本判据守四条（全部离线，不需要微信）：
  A. 路径规范化：填 `db_storage` ⇒ 自动补上"账号目录"与"它的上一级"两条路（三档依次试）；
  B. 缺密钥的分片**不许把整条链弄崩**：先补一次密钥，补不到就摘掉那个分片并**如实报出分片名**；
  C. 摘掉的是坏分片，好分片一个都不动；
  D. 结论话术分得清 —— 密钥缺口不能说成"权限或占用"。

追加 E 段（第二位网友的 02:39 报告，**切到另一个微信号**之后）：
  仍然是没有办法解决**」；报告里那行是 `打不开消息库：KeyError: 'message\\message_2.db'`。
  ⇒ 由头**不是权限**（管理员也不行就是反证）：驱动库 `_load_or_extract_keys` 会拿**密钥缓存里**
  每个 `rel` 去 `_key_works(rel)`，而 `_key_works → _db_path` 在当前账号文件表里找不到那条分片时
  `raise KeyError(rel)` ⇒ 构造函数当场抛。多账号/换号后分片布局一变就会踩到。
  E 段守：**只摘那一条陈旧分片**（其余密钥一个不动、留 .bak）、**构造与运行期两条路都自愈**、
  **摘不了就照原样抛**（不许吞掉）。

用法：runtime\\python\\python.exe scripts\\db_open_selftest.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import wechat as W # noqa: E402

# 判据里要用「像数据库目录那样」的路径，但**不许把真实机器路径或真实账号写进仓库**（隐私闸会拦，也确实该拦）
# ⇒ 一律运行时拼出来：既练到「填到 db_storage 那一层也要能退回账号目录/上一级」这条逻辑，又不留任何真数据。
_SEP = os.sep
_FAKE_ROOT = "D:" + _SEP + "wxdata"
_ACCT = "acct" + "_0001"
_FAKE_DB = _SEP.join([_FAKE_ROOT, _ACCT, "db_storage"])

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [%s]" % detail if detail else ""))


print("── A. 路径规范化（填哪一层都算对）──")
variants = W._db_dir_variants(_FAKE_DB)
ok("db_storage ⇒ 退回账号目录、再退回上一级",
   len(variants) == 3 and variants[0].endswith("db_storage")
   and variants[1].endswith(_ACCT) and variants[2].endswith(os.path.basename(_FAKE_ROOT)),
   " → ".join(os.path.basename(v) for v in variants))
tries = W.db_open_tries(_FAKE_DB)
srcs = [s for _d, s in tries]
ok("三档依次试，且出处标得出来（config → config↑ → …）",
   srcs[0] == "config" and "config↑" in srcs and srcs[-1] == "auto", str(srcs))
ok("规范化后的路径都真的进了候选表",
   any(d.endswith(_ACCT) for d, _s in tries) and any(d.endswith(os.path.basename(_FAKE_ROOT)) for d, _s in tries),
   str(len(tries)) + " 条")
ok("普通路径不会被乱改（原样在第一位）",
   W._db_dir_variants("E:" + os.sep + "wxdata")[0] == "E:" + os.sep + "wxdata")

print("── B/C. 缺密钥的分片：补一次 → 摘掉 → 如实报 ──")
cls = W._db_class()
ok("拿到的是 WeChatDB 的**子类**（不是另起一套）",
   cls is not None and cls.__name__ == "_SafeDB" and cls.__mro__[1].__name__ == "WeChatDB", cls.__mro__[1].__name__)
db = cls.__new__(cls) # 不走 __init__（不需要真微信）
db._keys = {}
db._db_files = {"message/message_1.db": "x", "message/message_2.db": "y"}
calls = {"n": 0}


def _fake_load(master_key=None):
    calls["n"] += 1
    db._keys["message/message_2.db"] = b"k" # 只能补回一半 ⇒ 另一个仍是缺的
    return None


db._load_or_extract_keys = _fake_load
try:
    db._open("message/message_1.db")
    ok("缺密钥的分片要报出**具体分片名**（不许静默返回坏连接）", False, "没有抛错")
except Exception as e:
    ok("缺密钥的分片要报出**具体分片名**（不许静默返回坏连接）",
       "没有密钥" in str(e) and "message_1.db" in str(e), "%s: %s" % (type(e).__name__, str(e)[:60]))
ok("撞上时先**补了一次**密钥", calls["n"] == 1, "补了 %d 次" % calls["n"])
ok("补不到的坏分片已从 _db_files 摘掉", "message/message_1.db" not in db._db_files, str(list(db._db_files)))
ok("**好分片一个都没动**", "message/message_2.db" in db._db_files, str(list(db._db_files)))

print("── D. 话术：密钥缺口 ≠ 权限问题 ──")
probe = {"found": [_FAKE_DB], "dbs": 73, "tried": "x"}
v_key = W._db_open_verdict(probe, [("d", "auto", "RuntimeError: 这个库分片没有密钥，已跳过：message/message_1.db")])
ok("密钥缺口那条：说密钥、给可照做的动作（微信要运行 + 同权限）",
   "密钥" in v_key and ("正在运行" in v_key or "登录" in v_key) and "权限或占用" not in v_key, v_key[-46:])
v_perm = W._db_open_verdict(probe, [("d", "auto", "PermissionError: [WinError 5] 拒绝访问")])
ok("真权限问题那条：仍然说权限/占用",
   "权限或占用" in v_perm, v_perm[-30:])
ok("两种结论**不一样**（不能一句糊过去）", v_key != v_perm)

print("── E. 切号后 `KeyError: 'message\\message_2.db'`（陈旧密钥缓存条目）要能定点自愈 ──")
TMP = tempfile.mkdtemp(prefix="pm_dbopen_")
try:
    _cache = os.path.join(TMP, "keys.json")
    _stable = os.path.join(TMP, "stable", "acct_0001.json")

    def _write_cache(more=None):
        d = {"message/message_1.db": "aa", "message/message_2.db": "bb"}
        d.update(more or {})
        with open(_cache, "w", encoding="utf-8") as f:
            json.dump(d, f)

    class _StubDB:
        """假驱动库：模拟"构造时拿缓存里每个 rel 去 _key_works ⇒ 撞上陈旧条目就 KeyError"。"""

        def __init__(self, *a, **kw):
            self.keys_file = kw.pop("keys_file", _cache)
            self.account = "acct_0001"
            self._files = {"message/message_1.db"}
            for rel in list(json.load(open(self.keys_file, encoding="utf-8"))):
                self._key_works(rel)
            self._keys = {}

        def _key_works(self, rel):
            if rel not in self._files:
                raise KeyError(rel) # 与驱动库 `_db_path(rel)` 同型
            return True

        def _open(self, rel):
            return "conn:" + rel

        def _stable_key_file(self):
            return _stable

    _real_base = W.__dict__.get("_SAFE_DB_CACHE")
    import wechatauto as _wa
    _saved_wc = getattr(_wa, "WeChatDB", None)
    _saved_cache = dict(W._SAFE_DB_CACHE)
    try:
        _wa.WeChatDB = _StubDB
        W._SAFE_DB_CACHE.clear()
        _cls = W._db_class()
        _write_cache()
        os.makedirs(os.path.dirname(_stable), exist_ok=True)
        with open(_stable, "w", encoding="utf-8") as f:
            json.dump({"message/message_2.db": "bb", "contact/contact.db": "cc"}, f)
        _db = _cls(keys_file=_cache) # 第一枪：缓存里有陈旧条目 ⇒ 自愈后应当成功
        ok("陈旧条目 ⇒ 构造**自愈**（不再当场抛 KeyError）", _db is not None)
        _after = json.load(open(_cache, encoding="utf-8"))
        ok("只摘掉那条**当前账号里不存在**的分片",
           "message/message_2.db" not in _after and "message/message_1.db" in _after, str(_after))
        _after_st = json.load(open(_stable, encoding="utf-8"))
        ok("稳定目录那份缓存也一起修（跨 TEMP 清理）",
           "message/message_2.db" not in _after_st and "contact/contact.db" in _after_st, str(_after_st))
        ok("留了 .bak（原样可回看）", os.path.isfile(_cache + ".bak"))
        ok("好密钥一条都没动（摘的是那一条，不是整份删缓存）",
           json.load(open(_cache + ".bak", encoding="utf-8")) ==
           {"message/message_1.db": "aa", "message/message_2.db": "bb"})
        # 摘不动就照原样抛（不许吞）
        _write_cache({"message/message_2.db": "bb"})
        _StubDB._files = {"message/message_1.db", "message/message_2.db"} # 全都存在 ⇒ 没有陈旧条目
        _cls2 = W._db_class()
        W._SAFE_DB_CACHE.clear()
        W._SAFE_DB_CACHE[_StubDB] = _cls2
        _bad = _cls2.__new__(_cls2)
        _bad.keys_file = _cache
        _bad.account = "acct_0001"
        _bad._files = {"message/message_1.db"}
        ok("摘不了（不是 .db / 缓存里没有）时 helper 返回 0（不乱删东西）",
           W._pm_drop_stale_key_entry(_bad, "没有这种 rel") == 0
           and W._pm_drop_stale_key_entry(_bad, "") == 0)
        # 运行期这条路（_open 里 _load_or_extract_keys 抛 KeyError）也要自愈
        _cls3 = W._db_class()
        _db3 = _cls3.__new__(_cls3)
        _db3._keys = {}
        _db3._db_files = {"message/message_1.db": "x", "message/message_2.db": "y"}
        _db3.keys_file = _cache
        _db3.account = "acct_0001"
        _db3._stable_key_file = lambda: _stable
        _c3 = {"n": 0}

        def _load3(master_key=None):
            _c3["n"] += 1
            if _c3["n"] == 1:
                raise KeyError("message/message_2.db") # 运行期撞上同一条陈旧缓存
            _db3._keys["message/message_1.db"] = b"k"

        _db3._load_or_extract_keys = _load3
        _conn = _db3._open("message/message_1.db")
        ok("运行期那条路也自愈（刷新 → 摘陈旧 → 再刷新 → 开库）",
           _conn == "conn:message/message_1.db" and _c3["n"] >= 2, "第 %d 次刷新" % _c3["n"])

        print("── E2. 开库**之前**就摘掉「盘上已经没有的条目」（第一枪就不抛）──")
        _base2 = os.path.join(TMP, "xwechat_files", "acct_0001")
        os.makedirs(os.path.join(_base2, "db_storage", "message"), exist_ok=True)
        with open(os.path.join(_base2, "db_storage", "message", "message_0.db"), "wb") as _f2:
            _f2.write(b"x")
        with open(os.path.join(_base2, "db_storage", "contact.db"), "wb") as _f2:
            _f2.write(b"x")
        _cache2 = os.path.join(TMP, "keys2.json")
        with open(_cache2, "w", encoding="utf-8") as _f2:
            json.dump({"message\\message_0.db": "aa", "message\\message_9.db": "bb",
                       "contact.db": "cc"}, _f2)
        _real_paths = W._key_cache_paths
        W._key_cache_paths = lambda _db: [_cache2] # 只认这份（不碰机器上真实的密钥缓存）
        try:
            _n_dropped = W._pm_prune_dead_key_entries(
                os.path.join(TMP, "xwechat_files"), "acct_0001", log_it=False)
            ok("盘上不存在的条目被摘掉（例：message_9.db）", _n_dropped >= 1, "摘了 %d 条" % _n_dropped)
            _after2 = json.load(open(_cache2, encoding="utf-8"))
            ok("**还存在的条目一条都没动**（不许误删密钥）",
               "message\\message_0.db" in _after2 and "contact.db" in _after2
               and "message\\message_9.db" not in _after2, str(_after2))
            ok("幂等：再跑一次没有可摘的（返回 0）",
               W._pm_prune_dead_key_entries(os.path.join(TMP, "xwechat_files"), "acct_0001",
                                            log_it=False) == 0)
            ok("账号目录不存在时不动任何缓存（返回 0）",
               W._pm_prune_dead_key_entries(os.path.join(TMP, "xwechat_files"), "不存在的账号",
                                            log_it=False) == 0)
        finally:
            W._key_cache_paths = _real_paths
    finally:
        if _saved_wc is not None:
            _wa.WeChatDB = _saved_wc
        W._SAFE_DB_CACHE.clear()
        W._SAFE_DB_CACHE.update(_saved_cache)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

# ── F 段：页 1 模式**以磁盘事实为准** —— 修「file is not a database / 数据库合并失败」──
#  症状／根因见 `agent/replica_adapter.py::fix_page1_plaintext_header`（那里有完整来龙去脉）。
#  ⚠️ **机制澄清（重要）**：我方 **不是**改 site-packages 里那个 db.py ——
#     那份文件重装依赖/更新就会被覆盖（`runtime/` 还是 gitignore 的）。正解是
#     `replica_adapter.fix_page1_plaintext_header()` 在**进程内**替换库模块里的函数，
#     由 `_init_db` 在读库之前调 `ensure_compat_patches()` 装上。
#  ⇒ 所以 F 段测的是**这个运行时补丁**：装上后的行为 + 「没装补丁时确实是坏的」的灵敏度对照。
from agent import replica_adapter as _ra # noqa: E402
from wechatauto import db as _wadb # noqa: E402

print("── F. 页 1 模式以磁盘事实为准（运行时补丁，不改 vendored 文件）──")
_SQLITE_HDR = b"SQLite format 3\x00"


def _mk_page1(first16: bytes) -> bytes:
    """造一页 4096B 的第 1 页：前 16 字节用给定值，其余是密文/IV 占位。"""
    _pg = bytearray(b"\xab" * _wadb.PAGE_SZ)
    _pg[:16] = first16
    return bytes(_pg)


_K32 = b"K" * 32
_K48 = b"K" * 48

# ⚠️ 必须在装补丁**之前**取一份原始实现（补丁装过之后 `_wadb._decrypt_page` 就是我们的了）
_orig_dec = _wadb._decrypt_page
_st = _ra.ensure_compat_patches()
ok("兼容补丁装上了（`ensure_compat_patches`）",
   ("page1" in str(_st)) or (_st in ("patched", "skip:already")), str(_st)[:90])

# ① 48B 候选 + 磁盘上是**明文头** ⇒ 保留原明文头（真·明文头库）
_r1 = _wadb._decrypt_page(_K48, _mk_page1(_SQLITE_HDR), 1)
ok("48B 候选 + 磁盘是明文头 ⇒ 解出页 1 仍带 SQLite 头", _r1[:16] == _SQLITE_HDR, repr(_r1[:16]))

# ② ★48B 候选 + 磁盘上是**密文 salt**（全加密库被误当明文头模式）⇒ 必须自己补标准头
_r2 = _wadb._decrypt_page(_K48, _mk_page1(b"\xcd" * 16), 1)
ok("★48B 候选 + 磁盘是密文 salt ⇒ 仍补出标准 SQLite 头", _r2[:16] == _SQLITE_HDR, repr(_r2[:16]))

# ③ 32B 候选（全加密库主路径）⇒ 自己补标准头
_r3 = _wadb._decrypt_page(_K32, _mk_page1(b"\xef" * 16), 1)
ok("32B 候选 ⇒ 页 1 补标准 SQLite 头", _r3[:16] == _SQLITE_HDR, repr(_r3[:16]))

# ④ 第 2 页及以后**不套页 1 规则**（前 16 字节是密文，不该被塞头）
_r4 = _wadb._decrypt_page(_K32, _mk_page1(_SQLITE_HDR), 2)
ok("第 2 页不受页 1 规则影响（不许被塞 SQLite 头）", _r4[:16] != _SQLITE_HDR, repr(_r4[:16]))

# ⑤ 尾部 reserve 区必须补回 80 字节（IV16+HMAC64），否则 sqlite 解析错位
ok("页 1/2 尾部 reserve 均补满 80B", len(_r1) == _wadb.PAGE_SZ and len(_r4) == _wadb.PAGE_SZ,
   "%d / %d" % (len(_r1), len(_r4)))

# ⑥ ★**灵敏度**：补丁前的实现必须**真的**会把密文当明文头（否则整段判据是恒真的摆设）
_old = _ra.original_decrypt_page() or _orig_dec
ok("★灵敏度：**补丁前**的实现把密文 salt 当明文头（首 16 字节不是魔数）⇒ 判据真能红",
   _old(_K48, _mk_page1(b"\xcd" * 16), 1)[:16] != _SQLITE_HDR,
   repr(_old(_K48, _mk_page1(b"\xcd" * 16), 1)[:16]))
ok("灵敏度对照：补丁前遇到真·明文头也照旧保留（不是把两路都改坏）",
   _old(_K48, _mk_page1(_SQLITE_HDR), 1)[:16] == _SQLITE_HDR)

# ⑦ 接线：`_init_db` 必须在**读库之前**装补丁（不然库自己先坏在模式上）
_wxa = open(os.path.join("agent", "wechat.py"), encoding="utf-8").read()
ok("接线：`_init_db` 在读库之前调 `ensure_compat_patches()`",
   "_ra_cp.ensure_compat_patches()" in _wxa)
ok("补丁实现**不改 vendored 文件**（进程内替换；`runtime/` 是 gitignore 的，改文件必丢）",
   "不改 site-packages 里的文件" in open(os.path.join("agent", "replica_adapter.py"),
                                          encoding="utf-8").read())

print("\n打不开消息库判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
