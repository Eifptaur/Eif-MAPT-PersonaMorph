# -*- coding: utf-8 -*-
"""「目录树快照差分」判据（第 46 项）—— 不碰网络、不碰窗口，全在临时目录里跑。

守的是这件事：**外部引擎不告诉你它写了哪些文件** ⇒ 只能靠「开工前后取差集」。
现场（`agent/bilibili.py::download`）：旧口径是「在输出目录里挑**最大最新**的那个」——
而那个目录是调用方给的、**可能本来就有旧文件** ⇒ 会把**上一次的产物**当成本次结果，
且**不报错**（表观症状：「这条视频读出来其实是另一条」）。

判据分两半：
  A. 纯函数（`agent/tree_snapshot.py`）：新建/改写/消失 认得准，且**不是恒真**；
  B. 接线（`bilibili.download`）：**开工前**拍快照、产物**只从新建的里面挑**、
     老口径（拿旧文件当结果）**已经不在**；快照不可用时才回退。

用法：runtime\\python\\python.exe scripts/tree_snapshot_selftest.py
"""
import io
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402 空白容忍的源码断言
import _srcslice  # noqa: E402  AST 锚点取函数正文（别用文本位置切）
from agent import tree_snapshot as TS  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def _write(d, name, data=b"x" * 12000):
    p = os.path.join(d, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


print("── A. 纯函数：新建 / 改写 / 消失 ──")
_td = tempfile.mkdtemp(prefix="ts-")
_old = _write(_td, "old.mp4", b"o" * 20000)          # 目录里**本来就有**的旧产物
_s0 = TS.snapshot(_td)
ok("A0 分母守卫：目录非空（否则「没变就没差集」是空转）", len(_s0) == 1, str(_s0))
ok("A1★ 什么都没动 ⇒ 三个差集**全空**（判据不是恒真）",
   TS.new_since(_s0, _s0) == [] and TS.changed_since(_s0, _s0) == []
   and TS.removed_since(_s0, _s0) == [], "")
_newp = _write(_td, "new_a.mp4", b"n" * 30000)
_write(_td, "sub/new_b.mp4", b"n" * 11000)
_s1 = TS.snapshot(_td)
ok("A2★ 新建的能**逐条列出**（含子目录里的）",
   TS.new_since(_s0, _s1) == ["new_a.mp4", "sub/new_b.mp4"], str(TS.new_since(_s0, _s1)))
ok("A3★ 旧文件**不算**新建（这条正是治「拿上一次的产物当结果」）",
   "old.mp4" not in TS.new_since(_s0, _s1), str(TS.new_since(_s0, _s1)))
with open(_old, "ab") as f:
    f.write(b"more")
_s2 = TS.snapshot(_td)
ok("A4 字节数变了 ⇒ 进「改写」（不是「新建」）",
   TS.changed_since(_s1, _s2) == ["old.mp4"] and "old.mp4" not in TS.new_since(_s1, _s2),
   str(TS.changed_since(_s1, _s2)))
os.remove(_newp)
_s3 = TS.snapshot(_td)
ok("A5 文件被删 ⇒ 进「消失」", TS.removed_since(_s2, _s3) == ["new_a.mp4"],
   str(TS.removed_since(_s2, _s3)))
os.makedirs(os.path.join(_td, "emptydir"), exist_ok=True)
ok("A6 目录**不算**产物：新建一个空目录 ⇒ 差集为空（目录 mtime 会造一堆假变更）",
   TS.new_since(_s3, TS.snapshot(_td)) == [], str(TS.new_since(_s3, TS.snapshot(_td))))

print("\n── B. 三条踩过的坑 ──")
_td2 = tempfile.mkdtemp(prefix="ts2-")
_u = _write(_td2, "Video.MP4", b"u" * 20000)
_b1 = TS.snapshot(_td2)
os.rename(_u, os.path.join(_td2, "video.mp4"))   # 只改大小写（Win 是同一个文件）
_b2 = TS.snapshot(_td2)
ok("B1★ Windows 大小写不敏感：只改大小写**不算**「删一个建一个」",
   TS.new_since(_b1, _b2) == [] and TS.removed_since(_b1, _b2) == [],
   "new=%s removed=%s" % (TS.new_since(_b1, _b2), TS.removed_since(_b1, _b2)))
_write(_td2, "v.part", b"p" * 20000)
_write(_td2, "v.ytdl", b"y" * 100)
_write(_td2, ".v.mp4.partial-1234-abcd", b"q" * 20000)
_b3 = TS.snapshot(_td2)
ok("B2★ 中间件不算产物（`.part` / `.ytdl` / 原子写的 `.partial-` 都不进快照）",
   TS.new_since(_b2, _b3) == [], str(TS.new_since(_b2, _b3)))
ok("B3 跳过开关可关（`skip_temp=False` 时中间件会被记 ⇒ 证明 B2 是「过滤」不是「看不见」）",
   len(TS.snapshot(_td2, skip_temp=False)) > len(_b3),
   "%d vs %d" % (len(TS.snapshot(_td2, skip_temp=False)), len(_b3)))
ok("B4 `base` 不存在 / 是空串 ⇒ 空 dict（不抛）",
   TS.snapshot(os.path.join(_td2, "nope")) == {} and TS.snapshot("") == {}, "")
ok("B5 `describe()` 是人话一行（新建/改写/消失三个数都在）",
   all(k in TS.describe(_b1, _b3) for k in ("新建", "改写", "消失")), TS.describe(_b1, _b3)[:70])

print("\n── C. 接线：`bilibili.download` 真的按差集挑产物 ──")
_bi = io.open(os.path.join(ROOT, "agent", "bilibili.py"), encoding="utf-8").read()
# ⛔ 换成 AST 锚点：原来按"函数在文件里的**文本位置**"切、再按下一个 `def` 截断 ⇒
#   函数一搬位置就崩/切错。（说明故意不照抄旧语法：照抄会被同文件的"可机械改写形态"普查
#   当成新写的一处。）
_dl = _srcslice.func_src(_bi, "download")
ok("C1 `download` 里引了 `tree_snapshot`", _sm.has(_dl, "tree_snapshot as _ts"), "")
_i_snap = _dl.find("_before = ")
_i_run = _dl.find("subprocess.run(args")
ok("C2★ **顺序即意图**：快照必须拍在**跑引擎之前**（拍在后面就永远没有「新建」）",
   0 <= _i_snap < _i_run, "快照@%d 跑引擎@%d" % (_i_snap, _i_run))
ok("C3★ 产物**只从新建的里面挑**（`new_since` 的返回值进候选）",
   _sm.has(_dl, "_new = _ts.new_since(_before, _after)") and _sm.has(_dl, "_fresh.append(_p)"), "")
ok("C4★ 能用快照却没有新文件 ⇒ **如实报没产物**（不许退回拿旧文件顶）",
   _sm.has(_dl, "没有在下载目录里新建任何文件"), "")
ok("C5 快照不可用（拿不到模块）时才回退旧口径，并**注明是回退**",
   _sm.has(_dl, "if _ts is None:") and _sm.has(_dl, "回退旧口径"), "")
_i_fb = _dl.find("if _ts is None:")
_i_old = _dl.find("os.path.getmtime(p) > os.path.getmtime(_best)")
ok("C6★ 老口径（最新 mtime 胜出）**只许留在回退分支里** —— 主路不许再用它"
   "（主路上它就是「拿上一次的产物当结果」的根源）",
   _i_old > 0 and _i_fb > 0 and _i_fb < _i_old, "回退@%d 老口径@%d" % (_i_fb, _i_old))
ok("C6b 反向锚：把老口径挪到主路（回退之前）⇒ C6 的位置检测必须报红",
   not (_i_old > 0 and _i_fb > 0 and _i_old < _i_fb), "")
ok("C7 多产物取**正片**（按字节数最大）而不是第一个",
   _sm.has(_dl, "max(_fresh, key=lambda p: os.path.getsize(p))"), "")

print("\n== 目录树快照差分判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
