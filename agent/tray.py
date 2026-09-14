# -*- coding: utf-8 -*-
"""托盘气泡兜底：控制台开不出来时，至少让「要你拍板的事」在任务栏上说一句话（⑦d）。

为什么还要它（用户口径「弹窗按你推荐的做」＋ 最高目标"不打扰"）：
  `notify_ui` 能自己开控制台、能闪任务栏；但**开不出来**的时候（WebView2 起不来、浏览器被策略挡住、
  没有桌面会话）用户就完全不知道有件事在等他。托盘气泡是最低成本的兜底：不抢前台、不弹窗、
  点一下才去开控制台。

实现要点（都在这里写死，别再各写一份）：
  · 一个**消息窗**（`HWND_MESSAGE`，不可见、不进任务栏）+ 自己的窗口过程，跑在**单独的 daemon 线程**里；
  · 图标用**我们自己的素材** `assets/app.ico`（`LoadImageW`），不用系统默认图标；
  · `NIM_ADD` 建档 → `NIM_MODIFY + NIF_INFO` 出气泡 → 点气泡（`NIN_BALLOONUSERCLICK`）回调开控制台；
  · 全程 best-effort：任何一步失败都返回报告（不抛），并遵守 `WX_NO_UI_POP=1`（判据/无人值守一律关）。
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes

log = logging.getLogger("persona-morph")

WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
HWND_MESSAGE = -3
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO = 0x00000001
NIN_BALLOONUSERCLICK = 0x0405
IDI_APPLICATION = 32512

# ⚠️ 结构体必须按 Windows 的原样写：cbSize 对不上时 Shell_NotifyIcon 静默失败（什么都不弹）
class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HANDLE), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HANDLE)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, ctypes.c_uint,
                              ctypes.c_ulonglong, ctypes.c_longlong)

_state = {"hwnd": 0, "icon": False, "thread": None, "ready": False, "last": "", "count": 0,
          "click": 0, "err": "", "class": ""}
_lock = threading.Lock()
_wndproc_ref = []          # ⚠️ 必须留引用：回调被 GC 掉后窗口过程就没了（窗口会收到野指针）
_click_handler = None


def _u():
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    u.RegisterClassW.restype = wintypes.ATOM
    u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    u.CreateWindowExW.restype = wintypes.HWND
    u.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong]
    u.DefWindowProcW.restype = ctypes.c_longlong
    u.DestroyWindow.argtypes = [wintypes.HWND]
    u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint]
    u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_uint,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.LoadImageW.restype = wintypes.HANDLE
    u.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    u.LoadIconW.restype = wintypes.HANDLE
    u.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong]
    return u


def _sh():
    sh = ctypes.WinDLL("shell32", use_last_error=True)
    sh.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    sh.Shell_NotifyIconW.restype = wintypes.BOOL
    return sh


def _icon_path() -> str:
    try:
        from .config import ROOT
        p = os.path.join(ROOT, "assets", "app.ico")
        return p if os.path.exists(p) else ""
    except Exception:
        return ""


def _load_icon():
    """我们自己素材里的图标（不用系统默认图标）。"""
    u = _u()
    p = _icon_path()
    h = 0
    if p:
        h = u.LoadImageW(None, p, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
    if not h:
        h = u.LoadIconW(None, wintypes.LPCWSTR(IDI_APPLICATION))
    return h


def _data(hwnd, icon, tip="群相", info_title="", info_text="") -> NOTIFYICONDATAW:
    d = NOTIFYICONDATAW()
    d.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    d.hWnd = hwnd
    d.uID = 1
    d.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | (NIF_INFO if info_text else 0)
    d.uCallbackMessage = WM_TRAY
    d.hIcon = icon
    d.szTip = str(tip)[:127]
    if info_text:
        d.szInfo = str(info_text)[:255]
        d.szInfoTitle = str(info_title or "群相")[:63]
        d.dwInfoFlags = NIIF_INFO
    return d


def _wnd_proc(hwnd, msg, wparam, lparam):
    try:
        if msg == WM_TRAY:
            if int(lparam) == NIN_BALLOONUSERCLICK:
                with _lock:
                    _state["click"] += 1
                if callable(_click_handler):
                    threading.Thread(target=_click_handler, daemon=True, name="tray-click").start()
            return 0
        if msg == WM_CLOSE:
            _u().DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            _u().PostQuitMessage(0)
            return 0
    except Exception as e:                                     # noqa: BLE001
        log.debug("托盘窗口过程异常：%s", e)
    return _u().DefWindowProcW(hwnd, msg, ctypes.c_ulonglong(wparam), ctypes.c_longlong(lparam))


def _run(cls_name: str) -> None:
    """消息窗 + 消息循环（跑在 daemon 线程里）。"""
    u = _u()
    try:
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(_WNDPROC(_wnd_proc), ctypes.c_void_p)
        wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc.lpszClassName = cls_name
        _wndproc_ref.append(wc)                                # 留引用，防 GC
        if not u.RegisterClassW(ctypes.byref(wc)):
            with _lock:
                _state["err"] = "RegisterClass 失败（err=%s）" % ctypes.get_last_error()
            return
        hwnd = u.CreateWindowExW(0, cls_name, "PMTray", 0, 0, 0, 0, 0, wintypes.HWND(HWND_MESSAGE),
                                 None, wc.hInstance, None)
        if not hwnd:
            with _lock:
                _state["err"] = "消息窗创建失败（err=%s）" % ctypes.get_last_error()
            return
        with _lock:
            _state["hwnd"] = int(hwnd)
            _state["ready"] = True
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
    except Exception as e:                                     # noqa: BLE001
        with _lock:
            _state["err"] = "%s: %s" % (type(e).__name__, e)
    finally:
        with _lock:
            _state["ready"] = False


def _ensure_thread(timeout: float = 3.0) -> bool:
    with _lock:
        if _state["ready"]:
            return True
        if _state["thread"] is not None and _state["thread"].is_alive():
            t = _state["thread"]
        else:
            cls_name = "PersonaMorphTray_%d" % (int(time.time() * 1000) % 100000)
            _state["class"] = cls_name
            t = threading.Thread(target=_run, args=(cls_name,), daemon=True, name="tray-msg")
            _state["thread"] = t
            t.start()
    deadline = time.time() + max(0.1, float(timeout))
    while time.time() < deadline:
        with _lock:
            if _state["ready"] or _state["err"]:
                return bool(_state["ready"])
        time.sleep(0.05)
    return False


def available() -> bool:
    """本机能不能用托盘（能建消息窗就行）。"""
    if os.name != "nt":
        return False
    try:
        return _ensure_thread()
    except Exception:
        return False


def notify(title: str, text: str, tip: str = "群相", click=None) -> dict:
    """出一次托盘气泡（best-effort）。返回报告：{ok, why, clicked_opens}。

    `click` 是点气泡时的回调（默认去开控制台）。遵守 `WX_NO_UI_POP=1`：设了就不弹。
    """
    global _click_handler
    rep = {"ok": False, "why": "", "skipped": "", "icon_added": False, "balloon_shown": False}
    if os.environ.get("WX_NO_UI_POP") == "1":
        rep.update({"ok": True, "skipped": "已按 WX_NO_UI_POP=1 关掉托盘气泡"})
        return rep
    if os.name != "nt":
        rep["why"] = "非 Windows，没有托盘"
        return rep
    try:
        with _lock:
            _state["last"] = "%s｜%s" % (title, text)
            _state["count"] += 1
        if not _ensure_thread():
            with _lock:
                rep["why"] = _state["err"] or "托盘消息窗没起来"
            return rep
        click = click or _default_click
        _click_handler = click
        sh, u = _sh(), _u()
        with _lock:
            hwnd = _state["hwnd"]
        icon = _load_icon()
        d = _data(hwnd, icon, tip=tip)
        if not sh.Shell_NotifyIconW(NIM_ADD, ctypes.byref(d)):
            rep["why"] = "NIM_ADD 失败（err=%s）" % ctypes.get_last_error()
            return rep
        with _lock:
            _state["icon"] = True
        rep["icon_added"] = True
        d2 = _data(hwnd, icon, tip=tip, info_title=title, info_text=text)
        rep["balloon_shown"] = bool(sh.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(d2)))
        rep["ok"] = True
        rep["why"] = "已出气泡" if rep["balloon_shown"] else "图标已在，气泡没弹出来（系统可能关了通知）"
        return rep
    except Exception as e:                                     # noqa: BLE001
        rep["why"] = "%s: %s" % (type(e).__name__, e)
        return rep


def _default_click():
    """点气泡 ⇒ 去开控制台（尽力而为）。"""
    try:
        from . import notify_ui as _nu
        _nu.open_console()
    except Exception as e:                                     # noqa: BLE001
        log.warning("点托盘气泡后开控制台失败：%s", e)


def shutdown() -> dict:
    """收掉图标与消息窗（判据/退出用）。"""
    rep = {"ok": False, "removed": False}
    try:
        sh = _sh()
        with _lock:
            hwnd, has = _state["hwnd"], _state["icon"]
        if hwnd and has:
            d = _data(hwnd, 0)
            rep["removed"] = bool(sh.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(d)))
            with _lock:
                _state["icon"] = False
        if hwnd:
            _u().PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
        rep["ok"] = True
        return rep
    except Exception as e:                                     # noqa: BLE001
        rep["why"] = "%s: %s" % (type(e).__name__, e)
        return rep


def status() -> dict:
    with _lock:
        return {k: v for k, v in _state.items() if k != "thread"}
