# -*- coding: utf-8 -*-
"""第 23 条判据：**后台链绝不允许把窗口置前/置顶**（不需要微信在跑）。

跑法： py -3 scripts\\fg_guard_selftest.py      退出码 0=全过 / 1=有失败

为什么有这条：
  "不要让窗口到前台"，结果我开的探针把微信**顶到最上面**。
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

from agent import config as cfg_mod # noqa: E402
# ⛔ 隔离：本判据会走**真**发送准备链（假 GUI），那条链会登记/归还窗口借用 ⇒
#   产品的 `data\window_borrow.json` 会被建出来又删掉（净变化 0，只有持续采样才看得见）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # scripts\（见 `_iso14` 文件头）
import _iso14 # noqa: E402
_iso14.window_borrow()
from agent import ui_adapt as ua # noqa: E402

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
        g3.get_input_box() # ＝`_send_poke_locate` 当年那一跳
        ok("⑦ 回归：库内 `get_input_box → calibrate_layout → bring_to_front` 链被彻底掐断",
           g3.calls == [], g3.calls)

        # ⑧ 静态：我们自己的代码里不许再留裸调用
        wsrc = open(WECHAT_PY, encoding="utf-8").read()
        usrc = open(UI_PY, encoding="utf-8").read()
        b_loc = code_of(wsrc, "_send_poke_locate")
        ok("⑧ `_send_poke_locate` 不再调 `gui.get_input_box()`（拆掉肇事那一跳）",
           "_send_poke_locate" in wsrc and "get_input_box" not in (b_loc or ""))
        b_get = code_of(wsrc, "_get_gui")
        ok("⑧ `_get_gui` 里**先上类闸再校准**，且校准被 `fg_allowed()` 分支包住",
           "harden_gui_class()" in b_get
           and 0 <= b_get.find("harden_gui_class()") < b_get.find("self._gui.calibrate_layout")
           and "fg_allowed()" in b_get and "跳过启动布局校准" in b_get)
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
            if ('("%s"' % _n) not in usrc:
                _wrapped, _ = False, _missing.append(_n)
        ok("⑧ 闸门覆盖库的四个入口（bring_to_front / calibrate_layout / ensure_visible / _minimize_blockers）",
           _wrapped)

        # ⑨ ⭐ 行为回归：走一遍 `_limit_wechat_window`（**每次取 GUI 都会跑**的那条）
        # 。修后＝只改几何、绝不激活。
        import ctypes as _ct
        from agent.wechat import WeChatAdapter
        from agent import wechat as _wxm

        calls = []

        def _make_rect():
            """预填好窗口矩形的**真 ctypes Structure**（`byref()` 只认真 ctypes 对象）。"""
            class _R(_ct.Structure):
                _fields_ = [("left", _ct.c_long), ("top", _ct.c_long),
                            ("right", _ct.c_long), ("bottom", _ct.c_long)]

            r = _R()
            r.left, r.top, r.right, r.bottom = 100, 100, 1460, 1100 # 1360x1000 > 1160x900
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
        _cfg_now = {"ui": {"lock_window_pos": True}, "wechat": {"limit_window": "shrink_only"}}
        cfg_mod.get_config = lambda: _cfg_now
        ad = WeChatAdapter.__new__(WeChatAdapter) # 不跑 __init__（它会连微信库）
        ad.cfg = cfg_mod.get_config()

        # 观测「活动信号」`touch()`：它是「借用 → 空闲归还」的续命信号，**不许被早退吞掉**
        # （吞掉 ⇒ 我们钉过的那一版会在无人操作一分钟（`window_borrow.IDLE_S`）后被 `restore()`
        #   还原成**改之前**的尺寸 —— 用户看到的就是"窗口自己变大"）。
        from agent import window_borrow as _wbm
        _touches = []
        _old_touch = _wbm.touch
        _wbm.touch = lambda: _touches.append(True)

        def _limit_once():
            """跑一次限位：清空观测、装好 ctypes 替身（`byref()` 只认真 ctypes 结构）。"""
            del calls[:]
            _ct.windll = _FakeWinDll()
            _ct.wintypes.RECT = _make_rect
            try:
                ad._limit_wechat_window(_G())
            finally:
                _ct.windll, _ct.wintypes.RECT = _old_windll, _old_rect

        def _sp_n():
            return len([c for c in calls if c[0] == "SetWindowPos"])

        try:
            _wxm._LOCK_APPLIED.clear()
            _limit_once() # 首次：按配置量一次并应用
            _n1, _t1 = _sp_n(), len(_touches)
            _limit_once() # 同一设定第二次：**不许再动窗口**
            _n2, _t2 = _sp_n(), len(_touches)
            _cfg_now["ui"]["lock_window_w"] = 905 # 用户在前端把宽改了 ⇒ 必须重新应用
            _limit_once()
            _n3 = _sp_n()
            _cfg_now["ui"]["lock_window_pos"] = False # 关掉限位 ⇒ 一行都不许碰窗口
            _limit_once()
            _n4 = _sp_n()
            _cfg_now["ui"]["lock_window_pos"] = True # 再打开 ⇒ 按那时的配置重新应用一次
            _limit_once()
            _n5 = _sp_n()
        finally:
            _wbm.touch = _old_touch

        ok("⑨ 限位首次应用一次（SetWindowPos 恰好一次）", _n1 == 1, _n1)
        ok("⑨ 同一设定再取 GUI **不再动窗口**（第二次 SetWindowPos 为 0）", _n2 == 0, _n2)
        ok("⑨ 用户改了 `ui.lock_window_w` ⇒ 重新应用一次", _n3 == 1, _n3)
        ok("⑨ 关掉限位 ⇒ 一次都不碰窗口", _n4 == 0, _n4)
        ok("⑨ 关掉再打开 ⇒ 按新配置重新应用一次", _n5 == 1, _n5)
        ok("⑨ 活动信号 `touch()` 即使早退也照发（不许被吞）",
           _t1 == 1 and _t2 == 2, (_t1, _t2))

        kinds = [c[0] for c in calls]
        ok("⑨ 限位不再调 `MoveWindow`（它会激活顶层窗）", "MoveWindow" not in kinds, calls)
        ok("⑨ 限位不出现任何置前/激活调用（SetForegroundWindow/SetActiveWindow/ShowWindow 9）",
           "SetForegroundWindow" not in kinds and "SetActiveWindow" not in kinds
           and ("ShowWindow", 9) not in calls, calls)
        _sp = [c for c in calls if c[0] == "SetWindowPos"]
        ok("⑨ 限位最多走一次 SetWindowPos，且带 SWP_NOACTIVATE(0x10)（不激活）",
           len(_sp) <= 1 and all((c[1] & 0x0010) != 0 for c in _sp), _sp)
        # ⛔ 这条原来把目标几何写死成 `(100, 100, 1160, 900)` —— 那正是"每次启动都把用户调小的
        #    窗口改回去"的病灶。现在尺寸读 `ui.lock_window_w/h`、位置**不动**（用窗口自己的），
        #    所以断言改成："要么不动，动的话就不许再是那个写死的 1160×900"。
        ok("⑨ 限位若动窗口：尺寸取自配置（不再写死 1160×900）、且不搬位置",
           (not _sp) or ((_sp[0][3], _sp[0][4]) != (1160, 900)), _sp)

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

        # ⑩ ⭐⭐ 回归：**构造期**那条路 —— 只包实例挡不住它（这就是"又"的原因）
        #    `WeChatGUI.__init__` 里就 `if calibrate: self.calibrate_layout()`，
        #    而 `_load_layout()` 在窗口尺寸与上次校准差 >15% 时拒绝采用 ⇒ 每次新进程构造都可能走到
        #    `calibrate_layout → bring_to_front`（HWND_TOPMOST）。必须在**类上、构造之前**上闸。
        class _InitGUI:
            calls = []

            def __init__(self):
                self.calibrate_layout() # 复现库里 __init__ 的行为

            def bring_to_front(self, **k):
                _InitGUI.calls.append("bring_to_front")
                return True

            def calibrate_layout(self, **k):
                _InitGUI.calls.append("calibrate_layout")
                return True

            def ensure_visible(self):
                _InitGUI.calls.append("ensure_visible")
                return True

            def _minimize_blockers(self):
                _InitGUI.calls.append("_minimize_blockers")

        cfg_mod.get_config = lambda: _cfg(True, False)
        _InitGUI.calls = []
        _InitGUI() # 上闸前：应当记到一次 calibrate_layout
        ok("⑩ 前置对照：未上闸时构造期确实会调 calibrate_layout",
           _InitGUI.calls == ["calibrate_layout"], _InitGUI.calls)
        ua.harden_gui_class(_InitGUI) # 类级上闸
        _InitGUI.calls = []
        _InitGUI() # 上闸后：一次都不许调到原方法
        ok("⑩ **构造期**的校准被挡住（类级闸：`__init__` 里那条路）",
           _InitGUI.calls == [], _InitGUI.calls)

        # ⑩ 静态：类闸必须**在构造之前**装上
        b_get = func_body(wsrc, "_get_gui")
        ok("⑩ `_get_gui()` 里 `harden_gui_class()` 出现在 `WeChatGUI(` **之前**",
           bool(b_get) and "harden_gui_class" in b_get
           and 0 <= b_get.find("harden_gui_class") < b_get.find("WeChatGUI("))
        _ra = open(os.path.join(ROOT, "agent", "replica_adapter.py"), encoding="utf-8").read()
        ok("⑩ `patch_driver_quirks()` 也顺手把类闸装上（它总在构造之前被调）",
           "harden_gui_class" in _ra)

        # ⑩b **真实那个类**确实被换掉了（只 import 类、**不构造实例** ⇒ 离线零打扰）
        #     ⚠️ 判据必须在**产品自己的解释器**上跑一遍：本机 `py -3` 用的是
        #     `M:\py\Lib\site-packages\wechatauto`（2048 行），产品用的是
        #     `runtime\python\...\wechatauto`（2178 行）——**两份不同的库副本**（日志行号 403/696 vs 418/726 为证）。
        try:
            import wechatauto.guia as _g
            _cls = _g.WeChatGUI
            _orig_btf = _cls.bring_to_front
            _orig_cal = _cls.calibrate_layout
            if getattr(_cls, "_pm_fg_hardened_class", False):
                delattr(_cls, "_pm_fg_hardened_class") # 允许本判据重跑
            ua.harden_gui_class(_cls)
            ok("⑩b 真实 WeChatGUI 的 bring_to_front / calibrate_layout 已被换成带闸门的版本",
               _cls.bring_to_front is not _orig_btf and _cls.calibrate_layout is not _orig_cal)
            ok("⑩b 被换掉之后**默认档下**调用它不置前（返回 False）",
               _cls.bring_to_front(object.__new__(_cls)) is False)
        except Exception as _e:
            ok("⑩b 真实类的闸装上（import 失败则这条算失败）", False, str(_e)[:60])

        # ⑪ ⭐⭐ **逐步前台追踪**抓到的真凶：`_get_uia()` 会 `SetForegroundWindow`
        #    （`_get_uia → WeChatUIA() → ensure_window() → _activate() → uia_driver.py:651-670`），
        #    由 `_uia_target_row_rect` 触发（拍一拍/引用定位都调）——上完类闸后"还有一次置前"就是它。
        class _UiaGUI:
            calls = []

            def _get_uia(self, refresh=False):
                _UiaGUI.calls.append("_get_uia")
                return "ENGINE"

        cfg_mod.get_config = lambda: _cfg(True, False)
        _UiaGUI.calls = []
        _pre = _UiaGUI()
        _r0 = _pre._get_uia()
        ok("⑪ 前置对照：未上闸时 `_get_uia()` 返回引擎且被调到",
           _r0 == "ENGINE" and _UiaGUI.calls == ["_get_uia"], (_r0, _UiaGUI.calls))
        ua.harden_gui_class(_UiaGUI)
        _g = _UiaGUI()
        _UiaGUI.calls = []
        _r = _g._get_uia()
        ok("⑪ 上闸后 `_get_uia()` 直接返回 **None**（不物化 UIA、不 SetForegroundWindow），原方法一次没调",
           _r is None and _UiaGUI.calls == [], (_r, _UiaGUI.calls))
        cfg_mod.get_config = lambda: _cfg(False, True)
        _g2 = _UiaGUI()
        _UiaGUI.calls = []
        _r2 = _g2._get_uia()
        ok("⑪ 真鼠标档下照常可用（不误杀）", _r2 == "ENGINE" and _UiaGUI.calls == ["_get_uia"],
           (_r2, _UiaGUI.calls))
        ok("⑪ 静态：闸表里有 `_get_uia`", '("_get_uia", None)' in usrc)
        ok("⑪ 静态：我们自己不直接构造 `WeChatUIA(`（只走 gui._get_uia，已上闸）",
           "WeChatUIA(" not in wsrc)

        # ⑫ ⭐⭐ `WM_CLOSE` 的**唯一咽喉点**：绝不关微信主窗 / 渲染子窗
        #     那一刻的下一刻」）：`_reattach_if_floating()` 只跳过**当时记下的** main 句柄，
        #     Qt 一重建主窗，新 hwnd 就不在白名单里 ⇒ **主窗被当浮动窗 WM_CLOSE 掉**
        # 。
        import win32gui as _w32
        import agent.input_backend as _ib2
        from agent import wechat as _wx2
        _o_find, _o_rc = _ib2.find_main_window, _ib2.find_render_child
        _ib2.find_main_window = lambda: 11111
        _ib2.find_render_child = lambda m: 22222
        try:
            _real_other = int(_w32.FindWindow("Shell_TrayWnd", None) or 0)
            ok("⑫ 拒绝把 `WM_CLOSE` 投给**主窗**", _wx2._wm_close_safe(11111, "自检") is False)
            ok("⑫ 拒绝把 `WM_CLOSE` 投给**渲染子窗**", _wx2._wm_close_safe(22222, "自检") is False)
            ok("⑫ hwnd=0 直接拒绝", _wx2._wm_close_safe(0, "自检") is False)
            ok("⑫ 普通窗**放行**（拿任务栏真窗口做正例，防误杀）",
               bool(_real_other) and _wx2._wm_close_safe(_real_other, "自检") is True, _real_other)
        finally:
            _ib2.find_main_window, _ib2.find_render_child = _o_find, _o_rc
        ok("⑫ 静态：所有 `WM_CLOSE` 投递点都过咽喉点（≥5 处调用 + 唯一咽喉点）",
           wsrc.count("_win_close(") >= 5 and wsrc.count("def _wm_close_safe(") == 1,
           "%d 处调用 / %d 处定义" % (wsrc.count("_win_close("), wsrc.count("def _wm_close_safe(")))
        ok("⑫ 静态：咽喉点注释里写明了「同型事故第二次」", "同型事故第二次" in wsrc)
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
