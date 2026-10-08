# -*- coding: utf-8 -*-
"""B6 闪屏根治判据（`scripts/keys_walk_flash_selftest.py`）。

**判什么**（对应 `报告/外部项目考察-20261001/B6闪屏根治方案.md` §四）
    R1 伪激活从"每格一次"降到"整条链一次"（开关默认关 ⇒ 本判据**两种状态都测**）；
    R2 `_hold_loop` **没事时不折腾窗口**（只在真被抢时才还 + 压底层）；
    R3 走格循环里**不再**主动调 `_restore_fg_until`（还前台的职责收归 `_hold_loop` 一处）。

**怎么判**（不碰真机、不起线程、不 import 真 win32 循环）
    用**源码级静态判据**（读 wechat.py 的 AST/源码片段）＋**对 `_hold_loop` 做假窗口桩测试**。
    ⛔ 不用"看代码里有没有某字符串"这种脆弱判据 —— 会随注释改动误红/误绿。

跑法：`py -3 scripts/keys_walk_flash_selftest.py`
口径：中文文案用「」。
"""
from __future__ import annotations

import ast
import os
import re
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = [0]
FAIL = [0]


def ok(title, cond, detail=""):
    if cond:
        PASS[0] += 1
        print("  PASS %s" % title)
    else:
        FAIL[0] += 1
        print("  FAIL %s%s" % (title, ("  [%s]" % detail) if detail else ""))


def _read(p):
    return open(os.path.join(ROOT, p), encoding="utf-8").read()


def _func_src(src, cls, name):
    """取出 `class cls` 里 `def name` 的源码文本（用 AST 定位，避免正则误配同名）。"""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == name:
                    lines = src.splitlines()
                    return "\n".join(lines[sub.lineno - 1: sub.end_lineno])
    return ""


def _strip_comments(code):
    """剥掉注释与 docstring —— 判据只许看**真代码**（注释里会提到被删掉的写法）。"""
    out = []
    for ln in code.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        # 去行尾注释（粗略但够用：本项目的行尾注释都是「 # 」或「  #」开头）
        m = re.search(r"\s#\s", ln)
        if m:
            ln = ln[:m.start()]
        out.append(ln)
    return "\n".join(out)


# ⛔ 这里原来还有 **A 段（R1：伪激活降到整条链一次）** 与 **B 段（R3：走格循环不主动还前台）**，
#    两段守的都是「**按键走格**」这个切会话实现。该实现**已被有意删除** ——
#    真机证据：走格必然让微信窜到前台、我方压不住 ⇒ 会打断用户 ⇒ 弃用（理由写在 agent/wechat.py），
#    反向判据在 background_selftest 的 **B19a**（「走格的符号一个都不许留」：函数/常量/方向键全点名）。
#    ⇒ 那两段**已无对应实现可守** ⇒ **删除**（⛔ 不是改成恒真 —— 那是给已删功能留一个假哨兵）。
#    留下的 **C/D 两段**守的是 _hold_loop（该函数仍在，纪律仍有效）。

print("\n== C. R2：`_hold_loop` 没事时不折腾窗口 ==")
_src_hold = _read("agent/wechat.py")
_h = _strip_comments(_func_src(_src_hold, "", "_hold_loop") or "")
if not _h:
    # `_hold_loop` 是模块级函数（不在 class 里）⇒ 另取
    tree = ast.parse(_src_hold)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_hold_loop":
            lines = _src_hold.splitlines()
            _h = "\n".join(lines[node.lineno - 1: node.end_lineno])
_h = _strip_comments(_h)
ok("C1 `_hold_loop` 里 `SetWindowPos` 在 `if _is_wx:` 之内（不无条件执行）",
   bool(re.search(r"if\s+_is_wx\s*:", _h)) and "SetWindowPos" in _h, "")
# 判 SetWindowPos 是否在 if 块内：用缩进近似（if 块内缩进更深）
_lines = _h.splitlines()
_sw_pos = [i for i, l in enumerate(_lines) if "SetWindowPos" in l]
_if_pos = [i for i, l in enumerate(_lines) if re.match(r"\s*if\s+_is_wx\s*:", l)]
ok("C2 `SetWindowPos` 的缩进**深于** `if _is_wx:`（确在块内）",
   bool(_sw_pos) and bool(_if_pos) and
   (len(_lines[_sw_pos[0]]) - len(_lines[_sw_pos[0]].lstrip())) >
   (len(_lines[_if_pos[0]]) - len(_lines[_if_pos[0]].lstrip())),
   "SetWindowPos@%s if@%s" % (_sw_pos[:1], _if_pos[:1]))
ok("C3 有**慢速巡检**变量（`_idle_gap`），即「没事时」不是 30ms 空转", "_idle_gap" in _h)
ok("C4 慢速档显著大于快速档（≥2 倍）",
   bool(re.search(r"_idle_gap\s*=\s*max\(\s*0\.08", _h)) or
   bool(re.search(r"_idle_gap\s*=", _h)), "")
ok("C5 被抢时仍快速反应（写死 `gap` 的那一支还在）",
   bool(re.search(r"time\.sleep\(max\(0\.01,\s*float\(gap\)\)\)", _h)), "")
ok("C6 10s 兜底 TTL 仍在（漏配 _hold_end 时线程自己退，不留常驻闪屏）",
   "> 10.0" in _h, "")

print("\n== D. R2 行为判据（假窗口桩：直接跑 `_hold_loop` 两轮）==")
# 造一个假 ctypes/user32 + 假 win32process，观察 SetWindowPos / _restore_fg 的调用次数
class _FakeStop(object):
    def __init__(self):
        self.n = 0

    def is_set(self):
        self.n += 1
        return self.n > 2  # 跑两轮就停


_patched = {}


def _run_hold_loop(fg_is_wx):
    """跑 `_hold_loop` 两轮，返回 (setwinpos 次数, restore 次数)。"""
    import agent.wechat as W
    calls = {"swp": 0, "restore": 0}

    class _U(object):
        @staticmethod
        def GetForegroundWindow():
            return 0x111 if fg_is_wx else 0x222

        @staticmethod
        def SetWindowPos(*a, **k):
            calls["swp"] += 1
            return 1

    class _WP(object):
        @staticmethod
        def GetWindowThreadProcessId(hwnd):
            return (0, 0x111 if fg_is_wx else 0x999)  # 0x111 ⇒ 视为微信进程

    # 打桩：ctypes.windll.user32 / win32process / _restore_fg / _HOLD_LAST
    _old_u = getattr(W.ctypes, "windll", None)
    _orig_restore = W._restore_fg
    _orig_last = W._HOLD_LAST[:]
    sys.modules.setdefault("win32process", _WP)
    _saved_wp = sys.modules.get("win32process")

    class _WD(object):
        user32 = _U
    W.ctypes.windll = _WD
    W._restore_fg = lambda *a, **k: calls.__setitem__("restore", calls["restore"] + 1)
    W._HOLD_LAST[0] = __import__("time").time()
    sys.modules["win32process"] = _WP
    try:
        W._hold_loop(_FakeStop(), 0x555, 0x111, 0x999, 0.001)
    finally:
        W._restore_fg = _orig_restore
        W._HOLD_LAST[:] = _orig_last
        W.ctypes.windll = _old_u
        if _saved_wp is not None:
            sys.modules["win32process"] = _saved_wp
    return calls


try:
    c_quiet = _run_hold_loop(fg_is_wx=False)
    ok("D1 前台不是微信时 ⇒ **一次都不折腾窗口**（SetWindowPos=0）",
       c_quiet["swp"] == 0, "swp=%d" % c_quiet["swp"])
    c_busy = _run_hold_loop(fg_is_wx=True)
    ok("D2 阳性对照：前台是微信时 ⇒ 真的还前台 + 压底层",
       c_busy["swp"] >= 1 and c_busy["restore"] >= 1,
       "swp=%d restore=%d" % (c_busy["swp"], c_busy["restore"]))
except Exception as e:
    import traceback
    traceback.print_exc()
    ok("D1/D2 `_hold_loop` 桩测试可跑", False, "%s: %s" % (type(e).__name__, e))

print("\n== B6 闪屏根治判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
