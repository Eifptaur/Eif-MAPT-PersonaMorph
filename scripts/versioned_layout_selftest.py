#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群相 · **版本目录 + 指针**判据（第 162 项）——「新版本装进 `releases\\<版本>\\` 再翻指针」这条链。

背景（`报告/任务书-更新机制版本目录-20261008.md`）——
老布局是"就地覆盖"：正在跑的那份代码会被新包覆盖 ⇒ 更新期间进程状态与新文件半新半旧，
且**回滚只能靠备份还原**（还原还要再覆盖一次）。
新布局把每个版本各存一份：`<安装根>\\群相.exe`（瘦启动器，**从不参与更新**）
＋ `releases\\current.txt`（指针）＋ `releases\\<版本>\\`（各版本代码一份）。
装新版本 ⇒ 写进**新目录**、校验过了**才翻指针**；翻指针是原子写 ⇒ 要么新、要么旧，没有中间态。

本判据守四件事：
  ① `layout` 参数默认 `inplace`（老路径一字不变，不许偷偷改行为）
  ② `layout="inplace"` 时**不产生 `releases\\` 目录、不写指针**（老用户不会被塞新结构）
  ③ `layout="versioned"` 时：**文件落在 `releases\\<版本>\\` 下**、安装根已有文件**原样不动**、
     且**指针最后才翻**（装失败 ⇒ 指针不翻 ⇒ 仍跑旧那份）
  ④ 指针**写入端就挡非法名**（空/带分隔符/含 `..`）—— 纵深防御第一道；
     读到被别人改坏的指针由启动器 `ResolveVersionedRoot` 挡（C# 侧，见真机验收记录）

自包含：临时目录里造"旧版树 + 在线包 zip + 清单"，真跑 `apply_full()`。
"""
import hashlib
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _iso14  # noqa: E402
_iso14.all_()  # 判据不许写产品的 data/ 与 logs/
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import update_apply as UA  # noqa: E402
from agent import version as V  # noqa: E402

PASS = FAIL = 0


def ok(cond, msg, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + msg)
    else:
        FAIL += 1
        print("  FAIL " + msg + ("   [%s]" % extra if extra else ""))


def write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(s)


def tree_of(items):
    files = {r: hashlib.sha256(b.encode()).hexdigest() for r, b in items.items()}
    h = hashlib.sha256()
    for r in sorted(files):
        h.update(r.encode("utf-8"))
        h.update(files[r].encode("ascii"))
    return h.hexdigest(), files


def make_pkg(zp, items):
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, body in items.items():
            z.writestr("persona morph/" + rel, body)


def snap(root):
    out = {}
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root).replace("\\", "/")
            out[rel] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


NEW_V = "Vᛞ"      # 用现行体系里的真符号（`version.RUNE_VALUES` 已登记 ᛞ=4）
tmp = tempfile.mkdtemp(prefix="pm-verlayout-")
try:
    # ── ① layout 默认值：不传就是 inplace（老路径一字不变）──
    import inspect
    sig = inspect.signature(UA.apply_full)
    ok(sig.parameters["layout"].default == "inplace",
       "行为锚：`apply_full` 的 `layout` 默认是 `inplace`（老调用点不传 ⇒ 行为与改造前完全一致）",
       "default=%r" % (sig.parameters["layout"].default,))

    # ── ② inplace：不产生 releases\、不写指针 ──
    old = {"agent/a.py": "A=1\n", "agent/b.py": "B=1\n", "README.md": "old\n"}
    new = {"agent/a.py": "A=2\n", "agent/b.py": "B=2\n", "README.md": "new\n",
           "agent/c.py": "C=1\n"}
    t_i = os.path.join(tmp, "t_inplace")
    for rel, body in old.items():
        write(os.path.join(t_i, rel.replace("/", os.sep)), body)
    z_i = os.path.join(tmp, "inplace.zip")
    make_pkg(z_i, new)
    sum_i, files_i = tree_of(new)
    man_i = {"schema": 1, "base": {"version": NEW_V, "sha256": sum_i, "files": files_i,
                                   "url": "https://x/y.zip", "build": "b1"}}
    rc_i, msg_i, info_i = UA.apply_full(man_i, z_i, target=t_i)
    ok(rc_i == 0, "inplace：真装成功（rc=0）", "rc=%s msg=%s" % (rc_i, msg_i))
    ok(not os.path.exists(os.path.join(t_i, "releases")),
       "行为锚：`layout=inplace`（默认）**不产生 `releases\\` 目录** —— 老用户不会被塞新结构",
       "已存在 releases\\")
    ok(open(os.path.join(t_i, "agent", "c.py"), encoding="utf-8").read() == "C=1\n",
       "inplace：新文件就地落在安装根（`agent/c.py`）")

    # ── ③ versioned：文件落 releases\<ver>\、安装根原文件不动、指针最后翻 ──
    t_v = os.path.join(tmp, "t_versioned")
    for rel, body in old.items():
        write(os.path.join(t_v, rel.replace("/", os.sep)), body)
    before = snap(t_v)
    z_v = os.path.join(tmp, "versioned.zip")
    make_pkg(z_v, new)
    man_v = {"schema": 1, "base": {"version": NEW_V, "sha256": sum_i, "files": files_i,
                                   "url": "https://x/y.zip", "build": "b1"}}
    rc_v, msg_v, info_v = UA.apply_full(man_v, z_v, target=t_v, layout="versioned")
    ok(rc_v == 0, "versioned：真装成功（rc=0）", "rc=%s msg=%s" % (rc_v, msg_v))
    vdir = os.path.join(t_v, "releases", NEW_V)
    ok(os.path.isfile(os.path.join(vdir, "agent", "a.py")),
       "versioned：新文件落在 `releases\\<版本>\\agent\\a.py`", vdir)
    ok(open(os.path.join(vdir, "agent", "a.py"), encoding="utf-8").read() == "A=2\n",
       "versioned：版本目录里是**新**内容（A=2）")
    # 安装根上原有的三个文件必须**一字未动**（这就是"正在跑的那份不被覆盖"）
    #   ⛔ `data/installed.json` 是**更新链自己的台账**（记录装过什么版本），本来就写安装根
    #      ⇒ 断言只比"代码文件"，不把台账算进"被覆盖"。
    after_root = {k: v for k, v in snap(t_v).items()
                  if not k.startswith("releases/") and not k.startswith("data/")}
    ok(after_root == before,
       "行为锚：`versioned` 布局下**安装根原有代码文件一字未动**"
       "（正在跑的那份不被覆盖 —— 这就是换布局的全部理由）",
       "before=%s after=%s" % (sorted(before), sorted(after_root)))
    ok(UA.read_pointer(t_v) == NEW_V,
       "versioned：指针最后翻到新版本（先装完、校验过，才翻）", repr(UA.read_pointer(t_v)))
    ok(str(info_v.get("layout") or "") == "versioned" and str(info_v.get("pointer") or "") == NEW_V,
       "versioned：返回值自述布局与指针（可供界面如实显示）", repr(info_v))

    # ── ③b 装失败 ⇒ 指针**不翻**（仍跑旧那份）──
    t_f = os.path.join(tmp, "t_fail")
    for rel, body in old.items():
        write(os.path.join(t_f, rel.replace("/", os.sep)), body)
    z_bad = os.path.join(tmp, "bad.zip")
    make_pkg(z_bad, {"agent/a.py": "TAMPERED\n"})   # 内容与清单不符
    man_bad = {"schema": 1, "base": {"version": NEW_V, "sha256": sum_i, "files": files_i,
                                     "url": "https://x/y.zip", "build": "b1"}}
    rc_b, msg_b, _ = UA.apply_full(man_bad, z_bad, target=t_f, layout="versioned")
    ok(rc_b != 0, "行为锚：包与清单不符 ⇒ **拒装**（rc≠0）", "rc=%s msg=%s" % (rc_b, msg_b))
    ok(not UA.read_pointer(t_f),
       "行为锚：装失败 ⇒ **指针不翻**（读回来是空/None ⇒ 启动器会退回安装根那份，仍跑旧的）",
       repr(UA.read_pointer(t_f)))

    # ── ④ 指针写入端挡非法名（纵深防御第一道）──
    t_p = os.path.join(tmp, "t_ptr")
    os.makedirs(UA.releases_root(t_p), exist_ok=True)
    for bad in ("..\\..\\evil", "a/b", "a:b", "", "   "):
        e = UA.write_pointer(t_p, bad)
        ok(bool(e), "指针写入端拒绝非法名 %r（返回失败原因，不落盘）" % (bad,),
           "err=%r" % (e,))
    ok(not os.path.exists(os.path.join(UA.releases_root(t_p), "current.txt")),
       "非法名试了一轮 ⇒ `current.txt` **根本没被创建**（不是「写了坏的」，是「没写」）")
    ok(UA.write_pointer(t_p, NEW_V) == "" and UA.read_pointer(t_p) == NEW_V,
       "合法名写入成功且读回一致")
    ok(not [f for f in os.listdir(UA.releases_root(t_p)) if f.endswith(".tmp")],
       "原子写不留任何 `*.tmp` 残留（收口 `persist.atomic_write_text` 已把它换走）",
       str(os.listdir(UA.releases_root(t_p))))

    # ── ⑤ 指针文件形状：UTF-8、无 BOM、无换行（写 `str(ver)` 原样）──
    raw = open(os.path.join(UA.releases_root(t_p), "current.txt"), "rb").read()
    ok(not raw.startswith(b"\xef\xbb\xbf"), "指针**无 BOM**（有 BOM ⇒ 启动器读到 `\\ufeffVᛞ` 认不出）",
       "raw=%r" % (raw[:8],))
    ok(raw.decode("utf-8") == NEW_V, "指针内容就是版本名本身（不带换行/空格）", repr(raw))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n==== 版本目录判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
