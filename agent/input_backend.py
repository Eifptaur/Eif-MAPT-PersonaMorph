# -*- coding: utf-8 -*-
"""输入后端（最高目标「全程后台、不抢鼠标」的落地模块）：**投递优先，真实输入兜底**。

为什么要有这一层：项目里原先到处直接 `mouse_event`（`wechat.py` 21 处 / `wechat_ui.py` 7 处），
"不动鼠标"根本无从保证。本模块把 UI 动作收成两个原语（`click` / `send_text`），
按 L5 → L0 顺序取用，并把「当前用的是哪一档」暴露给控制台。

两档实现（判据全部来自本项目实测，不是外推）：
  · `MessageBackend`（L5，默认）：`PostMessage` 发 `WM_ACTIVATE`/`WM_NCACTIVATE` 伪激活 + 鼠标/键盘消息。
    实测（2026-09-13）：①投递 `WM_CHAR` + 投递点「发送」按钮 ⇒ **3/3 DB 回读命中**，光标与前台未变；
    ②投递点笑脸 ⇒ 表情面板弹出（新顶层窗 `Qt51514QWindowToolSaveBits`）；③投递点面板里的收藏格
    ⇒ **DB 回读 `type=动画表情`**（`local_id 550→551`）⇒ 发送与表情两条链都能纯后台走完。
  · `RealInputBackend`（L0，兜底）：走 `ui_adapt.click` 的真鼠标路径；用完 `heal_input()` 恢复光标。

三条硬纪律（写进代码而不是只写文档）：
  1. **本模块 import 时就锁 PerMonitorV2**——实测同一进程里不锁 DPI 时 `GetWindowRect` 给的是
     虚拟坐标（面板 514×514），而微信是物理坐标（771×771，差 1.5 倍）⇒ 投递坐标落到窗口外、点了等于没点。
  2. **不置顶、不 `SetForegroundWindow`、不动光标**：投递就是投递，绝不靠"把窗口怼到用户脸上"换成功率。
  3. **投递点击要发给"真正接收该点击的那个窗口"**：主窗用于点笑脸，面板弹出后要发给面板窗
     （已实测：面板 0 个子窗，`WindowFromPoint` 归属就是面板本身）。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from .config import get_config

# ── ① DPI：import 即锁，任何坐标运算之前 ──────────────────────────────────
_user32 = ctypes.windll.user32


def _lock_dpi() -> str:
    try:
        _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PerMonitorV2
        return "PerMonitorV2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "shcore/2"
    except Exception:
        return "none"


DPI_MODE = _lock_dpi()

_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND

# ── 消息常量（投递序列用到的全部）────────────────────────────────────────
WM_ACTIVATE, WM_NCACTIVATE, WM_MOUSEACTIVATE = 0x0006, 0x0086, 0x0021
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
WM_CHAR, WM_KEYDOWN, WM_KEYUP = 0x0102, 0x0100, 0x0101

MAIN_CLASS = "Qt51514QWindowIcon"        # 微信主窗
PANEL_CLASS = "Qt51514QWindowToolSaveBits"   # 表情面板等弹层
LEVEL_MESSAGE = "message"                # L5
LEVEL_REAL = "real"                      # L0

_post = _user32.PostMessageW             # 单点可替换，供自检做无微信单测


def pack_lparam(x: int, y: int) -> int:
    """把客户区坐标打包成 lParam（低 16 位 x，高 16 位 y；各限 16 位）。"""
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def to_client(hwnd: int, screen_pt) -> tuple:
    """屏幕坐标 → 目标窗口客户区坐标（同一进程里必须已是物理像素口径）。"""
    p = wintypes.POINT(int(screen_pt[0]), int(screen_pt[1]))
    _user32.ScreenToClient(int(hwnd), ctypes.byref(p))
    return (int(p.x), int(p.y))


def screen_point(rect, rel) -> tuple:
    """渲染区相对坐标 → 屏幕坐标。rect = (left, top, right, bottom)。"""
    return (int(rect[0]) + int(rel[0]), int(rect[1]) + int(rel[1]))


def window_rect(hwnd: int) -> tuple:
    r = wintypes.RECT()
    _user32.GetWindowRect(int(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def _class_of(hwnd: int) -> str:
    b = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(int(hwnd), b, 256)
    return b.value


def _pid_of(hwnd: int) -> int:
    p = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(p))
    return int(p.value)


def _child_classes(hwnd: int) -> set:
    out = set()
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        out.add(_class_of(h))
        return True

    _user32.EnumChildWindows(int(hwnd), CB(cb), 0)
    return out


RENDER_CHILD = "MMUIRenderSubWindowHW"     # 主窗特有的渲染子窗


def find_main_window() -> int:
    """微信**主窗**（投递键盘消息、点笑脸都发它）。

    ⚠️ 不能用 `FindWindow(类名)`：表情面板之外的**朋友圈纯文字编辑窗也是
    `Qt51514QWindowIcon`**，`FindWindow` 返回的是第一个命中的那个（2026-09-13 实测踩到——
    投递全打到编辑窗上，发送自然失败）。判据：**带渲染子窗 `MMUIRenderSubWindowHW` 的那个**；
    退而取同进程里面积最大的。
    """
    cands = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        if _user32.IsWindowVisible(h) and _class_of(h) == MAIN_CLASS:
            cands.append(int(h))
        return True

    _user32.EnumWindows(CB(cb), 0)
    if not cands:
        return 0
    best, best_area = 0, -1
    for h in cands:
        r = window_rect(h)
        area = max(0, (r[2] - r[0])) * max(0, (r[3] - r[1]))
        if RENDER_CHILD in _child_classes(h) and area > best_area:
            best, best_area = h, area
    if best:
        return best
    for h in cands:                          # 没有渲染子窗（异常形态）⇒ 取最大的
        r = window_rect(h)
        area = max(0, (r[2] - r[0])) * max(0, (r[3] - r[1]))
        if area > best_area:
            best, best_area = h, area
    return best


def find_panel_window(main_hwnd: int = 0) -> int:
    """微信弹层窗（表情面板）：按类名找，同进程优先。找不到返回 0。"""
    hit = [0]
    want_pid = _pid_of(main_hwnd) if main_hwnd else 0
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        if _user32.IsWindowVisible(h) and _class_of(h) == PANEL_CLASS:
            if not want_pid or _pid_of(h) == want_pid:
                hit[0] = int(h)
                return False
        return True

    _user32.EnumWindows(CB(cb), 0)
    return hit[0]


# ── 后端 ────────────────────────────────────────────────────────────────
class InputBackend:
    """输入后端基类：两个原语 + 一档名字。"""

    name = "base"
    touches_cursor = False

    def click(self, hwnd: int, screen_pt, right: bool = False) -> tuple:
        raise NotImplementedError

    def send_text(self, hwnd: int, text: str) -> tuple:
        raise NotImplementedError

    def status(self) -> dict:
        return {"backend": self.name, "level": "?", "touches_cursor": self.touches_cursor}


class MessageBackend(InputBackend):
    """L5：投递消息。不动光标、不抢前台、不需要目标窗口在前台。"""

    name = LEVEL_MESSAGE
    touches_cursor = False
    level = "L5"

    def __init__(self, press_ms: int = 60, activate: bool = True):
        self.press_ms = int(press_ms)
        self.activate = bool(activate)

    def _wake(self, hwnd: int) -> None:
        """伪激活：让目标自认为被激活（不等于改前台窗口）。"""
        if not self.activate:
            return
        _post(int(hwnd), WM_ACTIVATE, 1, 0)
        _post(int(hwnd), WM_NCACTIVATE, 1, 0)
        time.sleep(0.08)

    def click(self, hwnd: int, screen_pt, right: bool = False) -> tuple:
        if not hwnd:
            return False, "窗口句柄为空"
        if right:
            return False, "投递右键尚未实测（右键菜单类请先用真鼠标路径）"
        cx, cy = to_client(hwnd, screen_pt)
        self._wake(hwnd)
        _post(int(hwnd), WM_MOUSEMOVE, 0, pack_lparam(cx, cy))
        time.sleep(0.05)
        _post(int(hwnd), WM_LBUTTONDOWN, 1, pack_lparam(cx, cy))
        time.sleep(self.press_ms / 1000.0)
        _post(int(hwnd), WM_LBUTTONUP, 0, pack_lparam(cx, cy))
        return True, ""

    def send_text(self, hwnd: int, text: str) -> tuple:
        """逐字投递 `WM_CHAR`。实测：不点输入框、不预设焦点也能进框（提交由调用方点发送按钮）。"""
        if not hwnd:
            return False, "窗口句柄为空"
        if not text:
            return False, "文本为空"
        for ch in str(text):
            _post(int(hwnd), WM_CHAR, ord(ch), 1)
        return True, ""


class RealInputBackend(InputBackend):
    """L0：真鼠标路径（`ui_adapt.click`）。**用完必须还原光标**——`ui_adapt.heal_input()` 负责。"""

    name = LEVEL_REAL
    touches_cursor = True
    level = "L0"

    def __init__(self, gui=None):
        self._gui = gui

    def bind(self, gui) -> None:
        self._gui = gui

    def click(self, hwnd: int, screen_pt, right: bool = False) -> tuple:
        try:
            from . import ui_adapt
            gui = self._gui
            if gui is None:
                return False, "真实后端未绑定 gui"
            # ui_adapt.click 收「渲染相对」坐标，这里把屏幕坐标换回去
            rel_x = int(screen_pt[0]) - int(gui.origin_x)
            rel_y = int(screen_pt[1]) - int(gui.origin_y)
            return ui_adapt.click(gui, rel_x, rel_y, right=right)
        except Exception as e:
            return False, str(e)

    def send_text(self, hwnd: int, text: str) -> tuple:
        return False, "真实后端不实现投递打字（请用 MessageBackend 或平台的 paste 路径）"


def select_backend(cfg: dict | None = None, gui=None) -> InputBackend:
    """按 `config.input.backend` 选档：auto（默认，有主窗就用投递）/ message / real。"""
    want = str(((cfg if cfg is not None else get_config()).get("input") or {}).get("backend", "auto")).lower()
    if want == LEVEL_REAL:
        return RealInputBackend(gui)
    if want == LEVEL_MESSAGE:
        return MessageBackend()
    # auto：Windows 上主窗在就走投递；找不到窗口就退回真鼠标（保守）
    return MessageBackend() if find_main_window() else RealInputBackend(gui)


_BACKEND: InputBackend | None = None


def active(gui=None, refresh: bool = False) -> InputBackend:
    """取当前生效的后端（进程内缓存；控制台改配置后传 refresh=True 重选）。"""
    global _BACKEND
    if _BACKEND is None or refresh or isinstance(_BACKEND, RealInputBackend):
        _BACKEND = select_backend(gui=gui)
        if isinstance(_BACKEND, RealInputBackend):
            _BACKEND.bind(gui) if gui is not None else None
    return _BACKEND


def status(gui=None) -> dict:
    """给控制台/日志用的现状快照（含"当前用的是哪一档"，AGENTS §2.1 第 4 条要求）。"""
    b = active(gui)
    main = find_main_window()
    return {
        "backend": b.name,
        "level": getattr(b, "level", "?"),
        "touches_cursor": b.touches_cursor,
        "dpi_mode": DPI_MODE,
        "wechat_main_hwnd": main,
        "wechat_pid": _pid_of(main) if main else 0,
        "note": "投递档不动光标、不抢前台；真实档用完由 ui_adapt.heal_input() 还原光标",
    }
