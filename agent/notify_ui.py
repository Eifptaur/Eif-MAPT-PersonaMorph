# -*- coding: utf-8 -*-
"""把「要用户拍板的事」**弹到眼前，但一秒都不抢他的前台**（用户口径 2026-09-14）。

用户原话：「**把弹窗切出来的那一秒，就应该立刻让它到后台**」＋「persona morph 不能自己把弹窗切出来吗」
⇒ 本模块只做三件事，且每一件都**不许把前台留在我们这边**：

  ① **找得到就复用、找不到就自己开**：控制台窗口（我们自己的 WebView2 窗口或浏览器）不在就
     自己开一个（`一键启动.exe --console <url>`，与 `scripts/onestart.py` 同一条路），并且只在
     **窗口不是当前前台** 时才动手（用户正看着控制台时不必再弹）。
  ② **抬起来但不激活**：`ShowWindow(SW_SHOWNOACTIVATE)` + `SetWindowPos(SWP_NOACTIVATE|SWP_SHOWWINDOW)`
     （最小化了才 `SW_RESTORE`）；再用 `FlashWindowEx(FLASHW_TRAY|FLASHW_TIMERNOFG)` 让任务栏闪几下
     —— 这才是"看得见但不打断"。SW_RESTORE 会激活窗口，所以**紧接着必须把前台还回去**。
  ③ **立刻还前台**：记下动手前的 `GetForegroundWindow()`，事后 `SetForegroundWindow(prev)`；
     这一步的成败如实写进报告（`restored` 字段），不假装成功。

**托盘兜底**：拿不到窗口（无桌面会话 / 开不出来）时退化成"只 FlashWindow + 留台账"——
决策本身在 `data/pending_decisions.json` 里，下次打开控制台照样会弹（不丢）。

只读/低风险：不点任何按钮、不改微信、不装包；所有 API 失败都返回报告而不是抛给调用方。
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
from ctypes import wintypes

from .config import ROOT

log = logging.getLogger("persona-morph")

TITLE_HINTS = ("群相", "控制台", "Persona Morph", "persona morph")
EXE_HINTS = ("一键启动.exe", "Agent启动器.exe", "一键启动", "Agent启动器")
FOREGROUND_HINT = ("控制台",)          # 兜底：前台不是控制台时才需要弹

SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9
HWND_TOP = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
FLASHW_TRAY = 2
FLASHW_TIMERNOFG = 12          # 一直闪到窗口到前台为止
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", wintypes.HWND), ("dwFlags", ctypes.c_uint),
                ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]


def _u():
    """user32，**带 argtypes**。

    为什么必须声明（AGENTS.md 记过的一条坑）：不声明时 `HWND` 会按 32 位 int 传，
    `SetWindowPos` 直接报 1400「无效窗口句柄」——本模块每个函数都要真调，漏一个就白测。
    """
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    u.EnumWindows.restype = wintypes.BOOL
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.IsWindow.argtypes = [wintypes.HWND]
    u.IsWindow.restype = wintypes.BOOL
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.IsIconic.argtypes = [wintypes.HWND]
    u.IsIconic.restype = wintypes.BOOL
    u.GetForegroundWindow.argtypes = []
    u.GetForegroundWindow.restype = wintypes.HWND
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.SetForegroundWindow.restype = wintypes.BOOL
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.ShowWindow.restype = wintypes.BOOL
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.SetWindowPos.restype = wintypes.BOOL
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
    u.FlashWindowEx.restype = wintypes.BOOL
    return u


def _k():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return k


def _title(hwnd) -> str:
    try:
        u = _u()
        n = int(u.GetWindowTextLengthW(hwnd))
        buf = ctypes.create_unicode_buffer(n + 2)
        u.GetWindowTextW(hwnd, buf, n + 2)
        return buf.value or ""
    except Exception:
        return ""


def _exe_of(hwnd) -> str:
    try:
        u, k = _u(), _k()
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid.value))
        if not h:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value or "")
        finally:
            k.CloseHandle(h)
    except Exception:
        pass
    return ""


def foreground() -> int:
    """当前前台窗口句柄（0＝拿不到）。"""
    try:
        return int(_u().GetForegroundWindow() or 0)
    except Exception:
        return 0


def set_foreground(hwnd) -> bool:
    try:
        if not hwnd:
            return False
        return bool(_u().SetForegroundWindow(wintypes.HWND(int(hwnd))))
    except Exception:
        return False


def flash(hwnd, count: int = 6) -> bool:
    """让任务栏闪几下（**不激活窗口**）——"看得见但不打断"的正解。"""
    try:
        if not hwnd:
            return False
        fi = FLASHWINFO(ctypes.sizeof(FLASHWINFO), wintypes.HWND(int(hwnd)),
                        FLASHW_TRAY | FLASHW_TIMERNOFG, int(count), 0)
        return bool(_u().FlashWindowEx(ctypes.byref(fi)))
    except Exception:
        return False


def find_console_window() -> int:
    """找控制台窗口（我们自己的 WebView2 窗口优先，浏览器页次之）。找不到返回 0。"""
    found = []

    def _cb(hwnd, _lparam):
        try:
            u = _u()
            if not u.IsWindowVisible(hwnd):
                return True
            t = _title(hwnd)
            exe = _exe_of(hwnd)
            score = 0
            if any(h in t for h in TITLE_HINTS):
                score += 3
            if any(h in exe for h in EXE_HINTS):
                score += 2
            if score:
                found.append((score, int(hwnd), t, exe))
        except Exception:
            pass
        return True

    try:
        _u().EnumWindows(_WNDUMPROC(_cb), 0)
    except Exception as e:
        log.debug("枚举窗口失败：%s", e)
    if not found:
        return 0
    found.sort(key=lambda x: -x[0])
    return found[0][1]


def console_url(anchor: str = "") -> str:
    """控制台地址（带 token 与锚点）。

    地址来源**只有一个权威顺序**（2026-09-14 定，起因：另一台机器打开控制台报
    `{"error":"unauthorized"}`——启动器自己用 `IndexOf("\\"token\\"")` 在 config.json 里找口令，
    但 config.json 里**排在前面的 `cloud.token` 是空串**，于是拼出 `/?token=` ⇒ 401）：
      ① `logs/console.url`（拥有 token 的进程写出来的**现成地址**，别人只读，不含解析）；
      ② 兜底：配置里的 `server.port` + `server.token`（结构化读取，绝不手写字符串找字段）。
    """
    base = ""
    try:
        from .util import read_console_url
        base = read_console_url()
    except Exception:
        base = ""
    if not base:
        port, tok = 3210, ""
        try:
            from .config import get_config
            sc = (get_config() or {}).get("server", {}) or {}
            port = int(sc.get("port") or 3210)
            tok = str(sc.get("token") or "")
        except Exception:
            pass
        base = "http://127.0.0.1:%d/" % port
        if tok:
            base += "?token=" + tok
    if anchor:
        base += anchor if str(anchor).startswith("#") else ("#" + str(anchor))
    return base


# WebView2 运行时（常青版）注册表位置：任一命中且 pv 非空 ⇒ 系统装了运行时
_WV2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
_WV2_KEYS = (
    (0x80000002, "SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\" + _WV2_GUID),   # HKLM
    (0x80000002, "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\" + _WV2_GUID),
    (0x80000001, "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\" + _WV2_GUID),               # HKCU
)


def webview_ready() -> dict:
    """我们自己的控制台窗口能不能开：exe 在 + WebView2 程序集在 + 运行时已装。

    这是"点打开控制台却弹出浏览器"的第一判据——先查清楚再决定，而不是开了才发现。
    """
    exe = os.path.join(ROOT, "一键启动.exe")
    dll = os.path.join(ROOT, "lib", "Microsoft.Web.WebView2.WinForms.dll")
    rep = {"exe": os.path.exists(exe), "dll": os.path.exists(dll),
           "runtime": False, "runtime_ver": "", "why": "", "ok": False}
    try:
        import winreg
        for hive, key in _WV2_KEYS:
            try:
                with winreg.OpenKey(hive, key) as k:
                    pv = str(winreg.QueryValueEx(k, "pv")[0] or "").strip()
                if pv:
                    rep["runtime"] = True
                    rep["runtime_ver"] = pv
                    break
            except OSError:
                continue
        if not rep["runtime"]:
            rep["why"] = "系统没装 WebView2 运行时"
    except Exception:
        rep["runtime"] = True          # 查不出来就按"可能就绪"（exe 自己还有一次回退），别把人挡在门外
        rep["why"] = "运行时检测不可用（按就绪处理）"
    if not rep["exe"]:
        rep["why"] = "目录里没有 一键启动.exe"
    elif not rep["dll"]:
        rep["why"] = "缺 lib\\Microsoft.Web.WebView2.WinForms.dll"
    rep["ok"] = bool(rep["exe"] and rep["dll"] and rep["runtime"])
    return rep


def open_console(url: str = "", browser_path: str = "", take_lock: bool = True) -> dict:
    """自己开一个控制台窗口（**单点**：所有入口共用一把锁 + 一个优先级）。

    优先级（2026-09-13 口径：控制台不再依赖浏览器）：
      ① 我们自己的 WebView2 窗口（`一键启动.exe --console <url>`，前提 `webview_ready()`）；
      ② 只有①确实不成立（exe 缺 / 缺 DLL / 没装 WebView2 运行时 / 启动抛异常）才回退浏览器。
    返回报告里如实写 `how`（`webview` / `browser` / `skip`）与 `why`，绝不假报"已在我们窗口里打开"。
    """
    url = url or console_url()
    if not url:
        return {"ok": False, "how": "", "why": "拿不到控制台地址（config.json 里没有 server.port）"}
    if take_lock:
        try:
            from .util import take_console_lock
            if not take_console_lock():
                return {"ok": True, "how": "skip", "why": "90 秒内刚有人开过，本次不重复打开（防双窗）"}
        except Exception:
            pass
    ready = webview_ready()
    try:
        from .util import write_console_url
        write_console_url(url)                 # 顺手把地址落盘：别的入口（启动器/托盘）直接读，别再自己拼
    except Exception:
        pass
    if ready["ok"]:
        try:
            subprocess.Popen([os.path.join(ROOT, "一键启动.exe"), "--console", url],
                             creationflags=0x08000000)
            return {"ok": True, "how": "webview", "why": "", "ready": ready}
        except Exception as e:
            ready["why"] = "自家窗口启动异常：%s" % e
    bp = ""
    try:
        from .util import pick_browser
        bp = pick_browser(browser_path)
    except Exception:
        bp = ""
    try:
        if bp:
            subprocess.Popen([bp, url], creationflags=0x08000000)
        else:
            import webbrowser
            webbrowser.open(url)
        return {"ok": True, "how": "browser", "why": ready["why"], "ready": ready}
    except Exception as e:
        return {"ok": False, "how": "", "why": "打开控制台失败：%s（请手动访问）" % e, "ready": ready}


def raise_without_stealing(hwnd=None) -> dict:
    """抬起来但不激活，并**立刻把前台还回去**。返回一份过程报告（不抛异常）。"""
    rep = {"ok": False, "hwnd": int(hwnd or 0), "prev": 0, "restored": None,
           "raised": False, "flash_tried": False, "flashed": False, "why": ""}
    try:
        u = _u()
        hwnd = int(hwnd or find_console_window() or 0)
        rep["hwnd"] = hwnd
        prev = foreground()
        rep["prev"] = prev
        if not hwnd or not u.IsWindow(hwnd):
            rep["why"] = "没找到控制台窗口（可能没开、或没有桌面会话）"
            return rep
        if int(prev) == hwnd:
            rep.update({"ok": True, "why": "控制台已经是前台，什么都不做"})
            return rep
        if u.IsIconic(hwnd):
            u.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)          # 会激活 ⇒ 下面必须还前台
        u.ShowWindow(wintypes.HWND(hwnd), SW_SHOWNOACTIVATE)
        u.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(HWND_TOP), 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        rep["raised"] = True
        # 闪烁"试过没有"是确定的；`FlashWindowEx` 的回值跟窗口状态有关（已经在闪、或由系统策略接管
        # 时会回 FALSE）⇒ 报告里分开记，自检只锚"试过"这一条，不把 OS 的回值当功能断言。
        rep["flash_tried"] = True
        rep["flashed"] = flash(hwnd)
        if prev:
            rep["restored"] = set_foreground(prev)
        rep["ok"] = True
        rep["why"] = "已抬起并闪烁任务栏；前台已还给原窗口" if rep["restored"] else \
            "已抬起并闪烁任务栏；还前台没成功（可能被系统策略挡住）"
        return rep
    except Exception as e:                                        # noqa: BLE001
        rep["why"] = "弹窗失败：%s: %s" % (type(e).__name__, e)
        return rep


def pop_decision_ui(url: str = "", anchor: str = "#sec-vermat", wait_s: float = 8.0) -> dict:
    """**Persona Morph 自己把弹窗切出来**：没窗口就开一个，然后不打扰你（可能短暂置前约 1~3 秒后自动还回）地抬起来。

    只在"控制台不是当前前台"时才动手——用户正看着控制台时不必再弹一次。
    返回报告：`{ok, action, hwnd, opened, why, …}`，`action ∈ skip-visible / raised / opened+raised / none`。
    """
    rep = {"ok": False, "action": "none", "hwnd": 0, "opened": False, "why": ""}
    try:
        hwnd = find_console_window()
        if hwnd and int(foreground()) == int(hwnd):
            rep.update({"ok": True, "action": "skip-visible", "hwnd": hwnd,
                        "why": "控制台就在前台，不再弹（用户已经看得见）"})
            return rep
        if not hwnd:
            o = open_console(url or console_url(anchor))
            rep["opened"] = bool(o.get("ok"))
            rep["how"] = o.get("how") or ""
            if not o.get("ok"):
                rep["why"] = o.get("why") or "开不出控制台窗口"
                return rep
            deadline = time.monotonic() + max(0.0, float(wait_s))       # 等窗口起来（有上限）
            while time.monotonic() < deadline and not hwnd:
                time.sleep(0.25)
                hwnd = find_console_window()
        r = raise_without_stealing(hwnd)
        rep.update(r)
        rep["action"] = "opened+raised" if rep["opened"] else "raised"
        rep["ok"] = bool(r.get("ok"))
        return rep
    except Exception as e:                                             # noqa: BLE001
        rep["why"] = "弹出流程异常：%s: %s" % (type(e).__name__, e)
        return rep


def brief(rep: dict | None = None) -> str:
    """一行摘要（日志用）。"""
    r = rep if rep is not None else raise_without_stealing()
    return "控制台弹窗：%s（hwnd=%s opened=%s prev=%s restored=%s）%s" % (
        r.get("action") or ("ok" if r.get("ok") else "失败"), r.get("hwnd"), r.get("opened"),
        r.get("prev"), r.get("restored"), (" · " + str(r.get("why") or "")) if r.get("why") else "")
