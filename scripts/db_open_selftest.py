# -*- coding: utf-8 -*-
"""「打不开消息库」两个失败模式的判据（2026-09-17 立，用户「佬」的报告）。

现场原话（两个失败模式都在他那一行报错里）：
  `RuntimeError: 未找到任何已登录账号的数据库（试过 3 条路：「…\\wxid_yu586z7rt3ad22_482e\\db_storage」
   RuntimeError: 未找到任何已登录账号的数据库；「…\\xwechat_files」KeyError: 'message\\message_1.db'；…）`
  ⇒ ①**填到 db_storage 那一层**：驱动库只在 db_dir 底下找"带 db_storage 的账号目录"，一个都找不到；
    ②**认了账号目录之后**又 `KeyError: 'message\\message_1.db'`（密钥表里没有这个分片）。

本判据守四条（全部离线，不需要微信）：
  A. 路径规范化：填 `db_storage` ⇒ 自动补上"账号目录"与"它的上一级"两条路（三档依次试）；
  B. 缺密钥的分片**不许把整条链弄崩**：先补一次密钥，补不到就摘掉那个分片并**如实报出分片名**；
  C. 摘掉的是坏分片，好分片一个都不动；
  D. 结论话术分得清 —— 密钥缺口不能说成"权限或占用"。

用法：runtime\\python\\python.exe scripts\\db_open_selftest.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from agent import wechat as W  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [%s]" % detail if detail else ""))


print("── A. 路径规范化（填哪一层都算对）──")
variants = W._db_dir_variants(r"C:\Users\lenovo\Documents\xwechat_files\wxid_yu586z7rt3ad22_482e\db_storage")
ok("db_storage ⇒ 退回账号目录、再退回 xwechat_files",
   len(variants) == 3 and variants[0].endswith("db_storage")
   and variants[1].endswith("wxid_yu586z7rt3ad22_482e") and variants[2].endswith("xwechat_files"),
   " → ".join(os.path.basename(v) for v in variants))
tries = W.db_open_tries(r"C:\Users\lenovo\Documents\xwechat_files\wxid_yu586z7rt3ad22_482e\db_storage")
srcs = [s for _d, s in tries]
ok("三档依次试，且出处标得出来（config → config↑ → …）",
   srcs[0] == "config" and "config↑" in srcs and srcs[-1] == "auto", str(srcs))
ok("规范化后的路径都真的进了候选表",
   any(d.endswith("wxid_yu586z7rt3ad22_482e") for d, _s in tries) and any(d.endswith("xwechat_files") for d, _s in tries),
   str(len(tries)) + " 条")
ok("普通路径不会被乱改（原样在第一位）",
   W._db_dir_variants(r"D:\xwechat_files")[0] == r"D:\xwechat_files")

print("── B/C. 缺密钥的分片：补一次 → 摘掉 → 如实报 ──")
cls = W._db_class()
ok("拿到的是 WeChatDB 的**子类**（不是另起一套）",
   cls is not None and cls.__name__ == "_SafeDB" and cls.__mro__[1].__name__ == "WeChatDB", cls.__mro__[1].__name__)
db = cls.__new__(cls)                       # 不走 __init__（不需要真微信）
db._keys = {}
db._db_files = {"message/message_1.db": "x", "message/message_2.db": "y"}
calls = {"n": 0}


def _fake_load(master_key=None):
    calls["n"] += 1
    db._keys["message/message_2.db"] = b"k"      # 只能补回一半 ⇒ 另一个仍是缺的
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
probe = {"found": ["C:\\Users\\lenovo\\Documents\\xwechat_files"], "dbs": 73, "tried": "x"}
v_key = W._db_open_verdict(probe, [("d", "auto", "RuntimeError: 这个库分片没有密钥，已跳过：message/message_1.db")])
ok("密钥缺口那条：说密钥、给可照做的动作（微信要运行 + 同权限）",
   "密钥" in v_key and ("正在运行" in v_key or "登录" in v_key) and "权限或占用" not in v_key, v_key[-46:])
v_perm = W._db_open_verdict(probe, [("d", "auto", "PermissionError: [WinError 5] 拒绝访问")])
ok("真权限问题那条：仍然说权限/占用",
   "权限或占用" in v_perm, v_perm[-30:])
ok("两种结论**不一样**（不能一句糊过去）", v_key != v_perm)

print("\n打不开消息库判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
