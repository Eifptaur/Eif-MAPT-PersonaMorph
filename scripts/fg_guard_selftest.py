# -*- coding: utf-8 -*-
"""第 23 条判据：**后台链绝不允许把窗口置前/置顶**（不需要微信在跑）。

跑法： py -3 scripts\\fg_guard_selftest.py      退出码 0=全过 / 1=有失败

为什么有这条（2026-09-18 作者发火）：
  作者用浏览器把微信**盖住**、明确要求"不要让窗口到前台"，结果我开的探针把微信**顶到最上面**。
  链路：`_send_poke_locate → gui.get_input_box()` → 探针连失 → `calibrate_layout()`
  → **`bring_to_front()`** → `SetWindowPos(HWND_TOPMOST)` + SetForegroundWindow + SetFocus
  （还先清掉系统前台锁）。走的是**置顶**，所以"盖住"防不住。
  ⇒ 从此所有置前/置顶调用走 `ui_adapt.fg_allowed()` 一道闸，并且**往 GUI 对象上上闸**，
    这样连"库内部自己触发的"那一类也挡住（只改自己的调用点是挡不住的）。

本判据的核心是**第 ⑦ 条**：用一个"会把置顶拖出来"的假 GUI 复现当时的链路，上闸后必须一次都不置顶。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import config as cfg_mod       # noqa: E402
from agent import ui_adapt as ua          # noqa: E402

WECHAT_PY = os.path.join(ROOT, "agent", "wechat.py")
UI_PY = os.path.join(ROOT, "agent", "ui_adapt.py")

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def func_body(src, name):
    i = src.find("    def %s(" % name)
    if i < 0:
        return None
    ends = [x for x in (src.find("\n    def ", i + 12), src.find("\n    # ──", i + 12)) if x > 0]
    return src[i:min(ends)] if ends else src[i:]


def code_of(src, name):
    body = func_body(src, name) or ""
    return "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))


class ExplodingGUI:
    """假 GUI：库里那几个"会置前/置顶"的方法一旦被调到就**记账**（原始方法不许被执行）。"""

    def __init__(self):
        self.calls = []

    def bring_to_front(self, **k):
        self.calls.append("bring_to_front")
        return True

    def calibrate_layout(self, **k):
        self.calls.append("calibrate_layout")
        return True

    def ensure_visible(self):
        self.calls.append("ensure_visible")
        return True

    def _minimize_blockers(self):
        self.calls.append("_minimize_blockers")

    def restore_zorder(self):
        self.calls.append("restore_zorder")

    def get_input_box(self):
        """**复现库的真实行为**：探针失败 ⇒ 自动重校准（guia.py:1388 → calibrate_layout → bring_to_front）。"""
        self.calibrate_layout(save=True)
        return None


def main():
    real_get = cfg_mod.get_config

    def _cfg(background_only, allow_real):
        return {"wechat": {"background_only": background_only},
                "input": {"allow_real_fallback": allow_real}}

    try:
        # ① 默认档＝拒绝（且理由写清楚）
        cfg_mod.get_config = lambda: _cfg(True, False)
        allowed, why = ua.fg_allowed()
        ok("① 默认档（background_only=开）⇒ 不许置前", allowed is False, why)
        ok("① 拒绝理由带上开关名", "background_only" in (why or ""), why)
        # ② 只翻一个开关仍然拒绝（两个都要满足）
        cfg_mod.get_config = lambda: _cfg(False, False)
        ok("② 只放开 background_only ⇒ 仍拒绝（真鼠标兜底是关的）", ua.fg_allowed()[0] is False)
        cfg_mod.get_config = lambda: _cfg(True, True)
        ok("② 只放开 allow_real_fallback ⇒ 仍拒绝", ua.fg_allowed()[0] is False)
        # ③ 两个都放开才允许
        cfg_mod.get_config = lambda: _cfg(False, True)
        ok("③ 两个开关都放开 ⇒ 允许（不误杀真鼠标档）", ua.fg_allowed()[0] is True)

        # ④ 配置读不到 ⇒ fail-closed
        def _boom():
            raise RuntimeError("模拟读配置失败")

        cfg_mod.get_config = _boom
        ok("④ 读配置失败 ⇒ 拒绝（fail-closed）", ua.fg_allowed()[0] is False)

        # ⑤ 默认档下：四个方法全部被拦，原始方法一次都没被调到
        cfg_mod.get_config = lambda: _cfg(True, False)
        g = ua.harden_gui(ExplodingGUI())
        g.bring_to_front(keep_topmost=True)
        g.calibrate_layout(save=True)
        g.ensure_visible()
        g._minimize_blockers()
        g.restore_zorder()
        ok("⑤ 默认档下 bring_to_front/calibrate_layout/ensure_visible/_minimize_blockers/restore_zorder 全拦",
           g.calls == [], g.calls)
        ok("⑤ 返回假值（调用方按「没做到」处理，不抛异常）",
           g.bring_to_front() is False and g.calibrate_layout() is False and g.ensure_visible() is False)
        ok("⑤ 有留痕（fg_refused 记到了次数）", ua.fg_refused().get("n", 0) >= 5, ua.fg_refused())

        # ⑥ 真鼠标档下：照常放行（没被闸门误杀）
        cfg_mod.get_config = lambda: _cfg(False, True)
        g2 = ua.harden_gui(ExplodingGUI())
        g2.bring_to_front(keep_topmost=True)
        g2.calibrate_layout(save=True)
        ok("⑥ 两个开关都放开 ⇒ 原方法照常执行",
           g2.calls == ["bring_to_front", "calibrate_layout"], g2.calls)

        # ⑦ ⭐ 回归：复现当时的链路 —— 库的 get_input_box 内部自己触发置顶
        cfg_mod.get_config = lambda: _cfg(True, False)
        g3 = ua.harden_gui(ExplodingGUI())
        g3.get_input_box()                       # ＝`_send_poke_locate` 当年那一跳
        ok("⑦ 回归：库内 `get_input_box → calibrate_layout → bring_to_front` 链被彻底掐断",
           g3.calls == [], g3.calls)

        # ⑧ 静态：我们自己的代码里不许再留裸调用
        wsrc = open(WECHAT_PY, encoding="utf-8").read()
        usrc = open(UI_PY, encoding="utf-8").read()
        b_loc = code_of(wsrc, "_send_poke_locate")
        ok("⑧ `_send_poke_locate` 不再调 `gui.get_input_box()`（拆掉肇事那一跳）",
           "_send_poke_locate" in wsrc and "get_input_box" not in (b_loc or ""))
        b_get = func_body(wsrc, "_get_gui")
        ok("⑧ `_get_gui` 里先上闸再校准（harden_gui 出现在 calibrate_layout 之前）",
           bool(b_get) and "harden_gui" in b_get
           and b_get.find("harden_gui") < b_get.find("calibrate_layout"))
        ok("⑧ `_get_gui` 的校准被 `fg_allowed()` 分支包住",
           bool(b_get) and "fg_allowed()" in b_get and "跳过启动布局校准" in b_get)
        b_ef = code_of(wsrc, "_ensure_foreground")
        ok("⑧ `_ensure_foreground` 有 fail-closed 闸（闸不过直接 False，不悄悄置顶）",
           "fg_allowed()" in (b_ef or "") and "拒绝置前" in (b_ef or ""))
        b_rm = code_of(wsrc, "_real_mouse_allowed")
        ok("⑧ `_real_mouse_allowed` 与闸门同源（唯一事实源，两处口径不许分叉）",
           "fg_allowed()" in (b_rm or ""))
        ok("⑧ 闸门本身只有一个定义处（ui_adapt.fg_allowed）",
           usrc.count("def fg_allowed(") == 1 and wsrc.count("def fg_allowed(") == 0)
        _wrapped, _missing = True, []
        for _n in ("bring_to_front", "calibrate_layout", "ensure_visible", "_minimize_blockers"):
            if ('_wrap("%s"' % _n) not in usrc:
                _wrapped, _ = False, _missing.append(_n)
        ok("⑧ 闸门覆盖库的四个入口（bring_to_front / calibrate_layout / ensure_visible / _minimize_blockers）",
           _wrapped)

        # ⑨ ⭐ 行为回归：走一遍 `_limit_wechat_window`（**每次取 GUI 都会跑**的那条）
        #    事故：`u.MoveWindow(hwnd, ..., True)` **会激活顶层窗** ⇒ 每次取 GUI 都把微信顶到浏览器前面
        #    （作者原话：「我一直在把浏览器往上放」）。修后＝只改几何、绝不激活。
        import ctypes as _ct
        from agent.wechat import WeChatAdapter

        calls = []

        def _make_rect():
            """预填好窗口矩形的**真 ctypes Structure**（`byref()` 只认真 ctypes 对象）。"""
            class _R(_ct.Structure):
                _fields_ = [("left", _ct.c_long), ("top", _ct.c_long),
                            ("right", _ct.c_long), ("bottom", _ct.c_long)]

            r = _R()
            r.left, r.top, r.right, r.bottom = 100, 100, 1460, 1100        # 1360x1000 > 1160x900
            return r

        class _FakeU32:
            def __init__(self):
                # ⚠️ 必须是**普通函数**（不是 bound method）：生产里 `u.SetWindowPos` 是 ctypes 的
                #    `_FuncPtr`，能挂 `argtypes`；bound method 挂不上会抛异常⇒被吞⇒假"没调"。
                self.SetWindowPos = _fake_set_window_pos
                self.MoveWindow = _fake_move
                self.ShowWindow = _fake_show
                self.SetForegroundWindow = _fake_fg
                self.SetActiveWindow = _fake_act

            def GetWindowRect(self, hwnd, pref):
                return 1

            def GetSystemMetrics(self, i):
                return 2560 if i == 0 else 1600

        def _fake_set_window_pos(hwnd, after, x, y, w, h, flags):
            calls.append(("SetWindowPos", int(flags), int(x), int(y), int(w), int(h)))
            return 1

        def _fake_move(*a):
            calls.append(("MoveWindow",))
            return 1

        def _fake_show(hwnd, cmd):
            calls.append(("ShowWindow", int(cmd)))
            return 1

        def _fake_fg(hwnd):
            calls.append(("SetForegroundWindow",))
            return 1

        def _fake_act(hwnd):
            calls.append(("SetActiveWindow",))
            return 1

        class _FakeWinDll:
            user32 = _FakeU32()

        class _G:
            main_hwnd = 1234567

            def refresh(self):
                pass

        _old_windll, _old_rect = _ct.windll, _ct.wintypes.RECT
        cfg_mod.get_config = lambda: {"ui": {"lock_window_pos": True},
                                      "wechat": {"limit_window": "shrink_only"}}
        ad = WeChatAdapter.__new__(WeChatAdapter)          # 不跑 __init__（它会连微信库）
        ad.cfg = cfg_mod.get_config()
        _ct.windll = _FakeWinDll()
        _ct.wintypes.RECT = _make_rect
        try:
            ad._limit_wechat_window(_G())
        finally:
            _ct.windll, _ct.wintypes.RECT = _old_windll, _old_rect

        kinds = [c[0] for c in calls]
        ok("⑨ 限位不再调 `MoveWindow`（它会激活顶层窗）", "MoveWindow" not in kinds, calls)
        ok("⑨ 限位不出现任何置前/激活调用（SetForegroundWindow/SetActiveWindow/ShowWindow 9）",
           "SetForegroundWindow" not in kinds and "SetActiveWindow" not in kinds
           and ("ShowWindow", 9) not in calls, calls)
        _sp = [c for c in calls if c[0] == "SetWindowPos"]
        ok("⑨ 限位只走一次 SetWindowPos，且带 SWP_NOACTIVATE(0x10)",
           len(_sp) == 1 and (_sp[0][1] & 0x0010) != 0, _sp)
        ok("⑨ 限位**几何真的被改了**（没被 try 吞掉）",
           bool(_sp) and _sp[0][2:] == (100, 100, 1160, 900), _sp[0][2:] if _sp else None)

        # ⑨ 静态：全仓 agent/ 不许再出现 `MoveWindow(`
        _mw = []
        for _f in sorted(os.listdir(os.path.join(ROOT, "agent"))):
            if not _f.endswith(".py"):
                continue
            for _i, _ln in enumerate(open(os.path.join(ROOT, "agent", _f), encoding="utf-8")
                                     .read().splitlines(), 1):
                if "MoveWindow(" in _ln and not _ln.strip().startswith("#"):
                    _mw.append("%s:%d" % (_f, _i))
        ok("⑨ 全仓 agent/ 不许再出现 `MoveWindow(`（会激活窗口）", not _mw, _mw)
    finally:
        cfg_mod.get_config = real_get

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
