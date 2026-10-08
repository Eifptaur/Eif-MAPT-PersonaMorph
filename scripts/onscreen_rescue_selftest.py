# -*- coding: utf-8 -*-
"""窗口卫生：① 设施「把窗口放回屏内」② ⭐ 棘轮「移出屏幕的调用不许绕过还原出口」。

⛔ 为什么要有这条判据：把窗口移到屏外时若**只调 `__enter__()`、不走还原出口**，
   窗口会停在"屏外且可见"这种**谁也救不回来**的状态（`rcNormalPosition` 也被写成屏外
   ⇒ 从任务栏点它还往外飞）。
   ⇒ 所以守两件：
     A. **有个正式入口能把窗口救回屏内**（原来只能人肉算坐标）；
     B. ⭐ **任何"把窗口移出屏幕"的调用，都要走到还原出口** ——
        只 `__enter__` / 只赋值而从不还原 ⇒ **当场报红**。

⚠️ 本判据会**自己造一个 Win32 窗口**（`CreateWindowExW` + `STATIC`）来做端到端验证 ——
   那是**我自己的测试窗**，**不碰用户的微信**（第 261 项的闸禁的是"构造微信真 GUI"）。
   为什么非要端到端：我第一版 `put_window_onscreen` 调了一个**不存在的** `_set_window_placement`
   ⇒ 异常被外层 `except` 吞掉 ⇒ **窗口根本不会被挪**，而桩测试看不出来（它自己也是假的）。
"""
from __future__ import annotations

import ast
import ctypes as ct
import io
import os
import sys
from ctypes import wintypes as wt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.environ["PM_JUDGE_NO_GUI"] = "1"      # 第 261 项的闸：本判据不碰微信真 GUI

from agent import voice_strip as VS # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


u = ct.windll.user32
u.CreateWindowExW.restype = wt.HWND
_SWP_NZ, _SWP_NA = 0x0004, 0x0010


def _fg():
    try:
        return int(u.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


print("\n══ A. 设施：把窗口放回屏内（端到端，自造窗口）══")
_h = u.CreateWindowExW(0, "STATIC", "pm-onscreen-judge", 0x00CF0000,
                       200, 150, 700, 520, None, None, None, None)
# ⚠️ `CreateWindowExW` 的返回值在 ctypes 里可能是 `int`、也可能是 `c_void_p` 包装
#   ⇒ 统一成一个 int 用，别到处 `.value`（我第一版就这么炸的）。
_H = int(getattr(_h, "value", _h) or 0)
try:
    ok("A0 造出了测试窗（自造；不碰微信）", bool(_H), "hwnd=%s" % _H)
    _vx, _vy, _vw, _vh = VS._virtual_screen()
    _rc0 = VS._window_rect(_H)
    ok("A1 起始在屏内", not VS._rect_off_screen(_rc0, _vw, _vh, _vx, _vy), str(_rc0))

    _fg0 = _fg()
    _o1 = VS.put_window_onscreen(_H)
    ok("A2 ⭐ **幂等**：已在屏内 ⇒ 什么都不做（`moved=False`）",
       _o1["moved"] is False and VS._window_rect(_H) == _rc0, str(_o1["why"])[:40])

    u.SetWindowPos(wt.HWND(_H), wt.HWND(0), _vx - 2000, _vy - 2000, 700, 520, _SWP_NZ | _SWP_NA)
    _rc_off = VS._window_rect(_H)
    ok("A3 前置：确实把它挪到了屏外（否则下面测的是空气）",
       VS._rect_off_screen(_rc_off, _vw, _vh, _vx, _vy), str(_rc_off))

    _o2 = VS.put_window_onscreen(_H)
    _rc2 = VS._window_rect(_H)
    ok("A4 ⭐ **救回来了**：屏外 ⇒ 搬回屏内",
       _o2["moved"] is True and not VS._rect_off_screen(_rc2, _vw, _vh, _vx, _vy),
       "%s → %s" % (_rc_off, _rc2))
    _wpn = (VS._get_window_placement(_H) or {}).get("rcNormalPosition")
    ok("A5 ⭐ **同时修了 `rcNormalPosition`**（否则任务栏还原还会跑回屏外）",
       bool(_wpn) and not VS._rect_off_screen(_wpn, _vw, _vh, _vx, _vy), str(_wpn))
    ok("A6 ⭐ **没抢前台**（救回前后前台窗口不变）", _fg() == _fg0, "fg %s → %s" % (_fg0, _fg()))
    ok("A7 救回后再调一次仍**幂等**", VS.put_window_onscreen(_H)["moved"] is False)
finally:
    try:
        u.DestroyWindow(wt.HWND(_H))
    except Exception:  # noqa: BLE001
        pass

print("\n══ B. ⭐ 棘轮：移出屏幕的调用**不许绕过还原出口** ══")
# 我干过的错法（反例，必须被认出来）：只调 `__enter__`、不退出 with。
_BAD_SNIPPET = "box = VS._OffscreenShown(h); box.__enter__()"
_GOOD_SNIPPET = "with VS._OffscreenShown(h) as box:\n    pass"


def _exit_forms(tree) -> list:
    """返回 `_OffscreenShown(...)` 的每个调用点 `(行号, 是否合规)`。

    ⛔ **合规的三种形式**（我第一版只认第 1 种 ⇒ **误报 4 个正确文件**，
       而"狼来了"式的误报会让这条棘轮被无视 —— 比没有更坏）：
      ① `with _OffscreenShown(h):`            —— 直接嵌在 `with` 里；
      ② `box = _OffscreenShown(h)` ＋ `with box:`  —— 先赋值再 `with`（常见写法）；
      ③ `box = _OffscreenShown(h)` ＋ `box.__exit__(...)` / `box._restore()` —— 显式还原（含 `finally` 里）。
    ⛔ **不合规**：只 `__enter__()`（或只赋值）而**从不还原** —— 那正是把窗口按在屏外的写法。
    """
    _in_with, _assign_names = set(), {}
    _names_ok = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.With):
            for item in n.items:
                for c in ast.walk(item.context_expr):
                    if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "_OffscreenShown":
                        _in_with.add(id(c))
                if isinstance(item.context_expr, ast.Name):
                    _names_ok.add(item.context_expr.id)          # 形式②
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
                and getattr(n.value.func, "attr", "") == "_OffscreenShown":
            for t in n.targets:
                if isinstance(t, ast.Name):
                    _assign_names[id(n.value)] = t.id
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr in ("__exit__", "_restore") and isinstance(n.func.value, ast.Name):
            _names_ok.add(n.func.value.id)                        # 形式③
        # `box.__enter__()` 单独出现 ⇒ **只有在同一文件里也有还原调用**时才算合规（形式③）
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "__enter__" and isinstance(n.func.value, ast.Name):
            _assign_names.setdefault(("enter", n.func.value.id), n.lineno)
    rows = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "_OffscreenShown":
            _v = _assign_names.get(id(n))
            _okv = bool(_v) and (_v in _names_ok)
            rows.append((n.lineno, (id(n) in _in_with) or _okv))
    return rows


_bt = ast.parse(_BAD_SNIPPET)
_gt = ast.parse(_GOOD_SNIPPET)
ok("B1 反例锚（灵敏度）：**只调 `__enter__` 不退出**的写法会被认出来（＝我干过的那种）",
   _exit_forms(_bt) and not _exit_forms(_bt)[0][1], str(_exit_forms(_bt)))
ok("B2 正例：`with` 写法被认成合规", bool(_exit_forms(_gt)) and _exit_forms(_gt)[0][1], str(_exit_forms(_gt)))

_bad, _scanned = [], 0
for _d, _dn, _fs in os.walk(ROOT):
    # ⛔ **`_scratch/` 不排除** —— 我那次犯错的代码就在 `_scratch/_plan/` 里（辅助脚本一样能
    #    把用户窗口按在屏外）。第一版把它排除了 ⇒ 这条棘轮**抓不到真凶**（＝空守）。
    _dn[:] = [x for x in _dn if x not in ("__pycache__", ".git", "runtime", "offline")]
    for _f in _fs:
        if not _f.endswith(".py"):
            continue
        _p = os.path.join(_d, _f)
        try:
            with io.open(_p, encoding="utf-8", errors="ignore") as _fh:
                _t = ast.parse(_fh.read())
        except Exception:  # noqa: BLE001
            continue
        _scanned += 1
        for _ln, _inwith in _exit_forms(_t):
            if not _inwith:
                _bad.append("%s:%d" % (os.path.relpath(_p, ROOT).replace("\\", "/"), _ln))
ok("B3 前置（防空过）：真的扫了仓库源码", _scanned >= 100 and os.path.isfile("agent/voice_strip.py"),
   "扫了 %d 个 .py" % _scanned)
ok("B4 ⭐⛔ **全仓 `_OffscreenShown` 的每个调用点都必须走还原出口**"
   "（合规＝`with` / 先赋值再 `with` / 显式 `__exit__`·`_restore`；只 `__enter__` ⇒ 报红）",
   not _bad, "违规：%s" % (_bad[:5] if _bad else "无 ✅"))

print("\n══ C. 静态锚：缺口与本项的由来 ══")
_pr = io.open(os.path.join(ROOT, "agent", "voice_strip.py"), encoding="utf-8").read()
_we = io.open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
# ⛔ 取函数体一律走项目既有的切片助手（`_srcslice.func_src`），**不许手写"文本切函数"**
#   —— 那是 `structure_guard_selftest` 明令的"可机械改写形态"（我第一版就踩了）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _srcmatch as _sm # noqa: E402
import _srcslice as _ss # noqa: E402
_put_src = _ss.func_src(_pr, "put_window_onscreen")
# ⛔ 断言源码一律用 `_srcmatch.has()`（**空白容忍、且不计入"脆断言"普查**）：
#   直接写 `"带空白的整段源码" in src` 会随缩进/换行一改就红，而行为根本没变
#   —— `judge_hygiene_selftest` 明令这类写法**只许降不许升**（我第一版就踩了两处）。
ok("C1 设施在案（`put_window_onscreen` 存在且有 docstring 说明由来）",
   _sm.has(_pr, "def put_window_onscreen(") and "救不回来" in _pr)
ok("C2 ⛔ 缺口也写在案：`_ensure_main_visible` 只认「最小化/隐藏」两态（「屏外但可见」它不动）",
   "屏外但可见" in _we or "屏外" in _we)
ok("C3 反向 helper 也补齐了（原来只有 `_get_window_placement`、没有 set 版）",
   _sm.has(_pr, "def _set_window_placement("))
ok("C4 `put_window_onscreen` 里**不再调用不存在的函数**"
   "（它要用的 reverse helper 必须真的定义过，否则异常被吞 ⇒ 窗口根本不会被挪）",
   "_set_window_placement(" in _put_src and _sm.has(_pr, "def _set_window_placement("))
ok("C5 ⭐ 它**查过前台闸**（`fg_allowed` 是「能不能置前/置顶」的唯一真源）"
   "—— 本函数只用不激活API，查它是为了决定要不要额外激活",
   "fg_allowed" in _put_src)

print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for _x in FAIL:
        print("  - " + _x)
sys.exit(1 if FAIL else 0)
