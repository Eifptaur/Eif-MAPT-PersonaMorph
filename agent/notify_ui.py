# -*- coding: utf-8 -*-
"""把「要用户拍板的事」弹到眼前：**不动光标、不要求可见；可能短暂置前约 1~3 秒后自动还回**（口径 2026-09-14）。

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

# ── 「哪个窗口是我们的控制台」的唯一判据（2026-09-19 收紧，起因见 classify_console_window）──
# 唯一权威口径＝控制台窗口标题「群相 控制台」。这个串在三处同源：`agent/console_html.py` 的
# `<title>群相 控制台</title>`、`launcher-src/launcher.cs:1165` 的 `StyleKit.Apply(this, "群相 控制台")`、
# 以及 `launcher.cs:1250` 的 `Ui.ConsoleWindowAlive()`（它本来就按这个串判，exe 那边口径是对的）。
CONSOLE_CAPTION = "群相 控制台"
# 启动器自家的窗体标题也带「群相」⇒ **必须先排除**，否则被认成控制台（launcher.cs 里的窗体标题）：
# 「群相 一键启动」/「群相 正在启动」/「群相 启动完成」/「群相 已就绪」。
LAUNCHER_CAPTIONS = ("一键启动", "正在启动", "启动完成", "已就绪", "已在运行", "正在关闭")
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


def classify_console_window(title: str, exe: str) -> int:
    """给一个顶层窗口打「是不是我们的控制台」的分（0＝不是）。**纯函数，判据可单测**。

    ⛔ 2026-09-19 修（作者报「刚刚点了一下『一键启动』，怎么没有打开控制台呀？还得我再点一下」）：
      旧判据是**标题子串** `TITLE_HINTS=("群相","控制台","Persona Morph",…)` + `EXE_HINTS`（含裸 "一键启动"）。
      但启动器自己的窗口就叫**「群相 一键启动」**、完成时叫**「群相 启动完成」**，而**控制台窗口与启动器窗口
      是同一个 exe**（一键启动.exe）⇒ 启动器窗口拿 3+2 分，被当成"控制台已经开着"。
      现场的连锁（05:09 实测日志）：机器人侧 `open_console` 判成 `reuse`（"控制台已经开着 ⇒ 复用那个窗口"）
      ⇒ 它不开；2 秒后 `onestart._probe_browser_was_opened()` 也看到那个窗口 ⇒ 判"机器人侧已打开"，启动器
      也不开 ⇒ **两边都不开、屏幕上一片空白**；他再点一次时启动器窗口已经关掉，这才开出来。
      顺带：标题里带「群相」的浏览器标签页（包括我们自己的 DSH 会话标签）同样会撞上这条判据。
      ⇒ 现在只认 `CONSOLE_CAPTION`（浏览器标签页会带 " - Google Chrome" 后缀，故用**包含**），
        并先排除 `LAUNCHER_CAPTIONS`；`EXE_HINTS` 降级为**排序加分**（自家窗口优先于浏览器页），不再能单独成立。
    """
    t = (title or "").strip()
    if not t:
        return 0
    if any(x in t for x in LAUNCHER_CAPTIONS):
        return 0
    if CONSOLE_CAPTION not in t:
        return 0
    return 4 + (3 if any(h in (exe or "") for h in EXE_HINTS) else 0)


def window_title(hwnd) -> str:
    """给一个窗口句柄取标题（**诊断用**：把"我当时认的是哪个窗口"写进日志，取不到返回空串）。"""
    try:
        return _title(int(hwnd))
    except Exception:
        return ""


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
            score = classify_console_window(t, exe)
            if score:
                found.append((score, int(hwnd), t, exe))
        except Exception:
            pass
        return True

    try:
        _u().EnumWindows(_WNDENUMPROC(_cb), 0)
    except Exception as e:
        # ⚠️ 2026-09-17 实测踩坑：这里原来写的是 `_WNDUMPROC`（本模块里根本不存在），
        #   `NameError` 被这句 `except` 吃掉 ⇒ **函数永远返回 0**（"找不到控制台窗口"），
        #   于是 `open_console()` 的复用分支从没生效过、每重启一次就多开一个窗口。
        #   教训：吞异常的兜底必须**至少留一条 warning 级痕迹**，否则一个拼写错误能静默半年。
        log.warning("枚举窗口失败（找不到控制台窗口）：%s: %s", type(e).__name__, e)
    if not found:
        return 0
    found.sort(key=lambda x: -x[0])
    return found[0][1]


# 开窗前的"等窗口出现"次数（每次 0.5 秒）——判据里会调小，产品用默认 12（＝6 秒）
_WAIT_WINDOW_TRIES = 12


def _url_live(u: str) -> bool:
    """这个地址的端口有人在听吗？（0.4 秒超时，连不上就 False —— 用来挡"死链被落盘/被拿去开窗"）"""
    try:
        from urllib.parse import urlparse
        import socket as _s
        p = urlparse(str(u or ""))
        with _s.create_connection((p.hostname or "127.0.0.1", int(p.port or 80)), 0.4):
            return True
    except Exception:
        return False


def console_url(anchor: str = "") -> str:
    """控制台地址（带 token 与锚点）。

    地址来源**只有一个权威顺序**（2026-09-14 定，起因：另一台机器打开控制台报
    `{"error":"unauthorized"}`——启动器自己用 `IndexOf("\\"token\\"")` 在 config.json 里找口令，
    但 config.json 里**排在前面的 `cloud.token` 是空串**，于是拼出 `/?token=` ⇒ 401）：
      ① `logs/console.url`（拥有 token 的进程写出来的**现成地址**，别人只读，不含解析）；
      ② 兜底：配置里的 `server.port` + `server.token`（结构化读取，绝不手写字符串找字段）。
    🔴 2026-09-18 加**活性检查**：①那份文件可能被"起在随机端口上的实例/判据"写脏（实测被写成
      `…:14675` 而没人听）⇒ 直接拿它开窗就是 `ERR_CONNECTION_REFUSED`（作者现场就撞上了）。
      ⇒ 文件里的端口**连不上就弃用**，回落配置地址并留 warning；两边都连不上才返回文件值。
    """
    base = ""
    try:
        from .util import read_console_url
        base = read_console_url()
    except Exception:
        base = ""

    def _live(u: str) -> bool:
        return _url_live(u)

    _fallback = ""
    try:
        from .config import get_config
        sc = (get_config() or {}).get("server", {}) or {}
        _port = int(sc.get("port") or 3210)
        _tok = str(sc.get("token") or "")
        _fallback = "http://127.0.0.1:%d/" % _port + (("?token=" + _tok) if _tok else "")
    except Exception:
        _fallback = ""
    if base and not _live(base) and _fallback:
        # ⚠️ 2026-09-18 晚修（判据 `console_open_selftest` 那条"死链回落"一直红）：
        #   老写法要求 **文件死 且 配置地址活** 才回落 ⇒ 控制台恰好没在跑时（配置地址也连不上）
        #   就把死链原样交出去，正是要防的那件事；而且判据结果取决于"本机此刻有没有在听的控制台"
        #   ⇒ **不可复跑**。改成：文件值连不上（复查一次，0.4s 在忙机器上会误判）⇒ 一律以**配置地址**
        #   为准（配置是文档化的唯一权威来源）；文件值活着则照旧以它为准（随机端口实例仍优先）。
        if not _live(base):
            log.warning("logs/console.url 指向的端口连不上（多半是被随机端口实例写脏了）⇒ 改用配置地址 %s",
                        _fallback.split("?")[0])
            base = _fallback
    if not base:
        base = _fallback or "http://127.0.0.1:3210/"
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


def open_console(url: str = "", browser_path: str = "", take_lock: bool = True, mode: str = "") -> dict:
    """自己开一个控制台窗口（**单点**：所有入口共用一把锁 + 一个优先级）。

    优先级（2026-09-13 口径：控制台不再依赖浏览器；2026-09-17 加"复用"）：
      ⓪ **已经开着控制台窗口 ⇒ 复用那个窗口**（抬起来 + 闪任务栏，不新开）——
         起因：用户报「重启几次就攒出 4 个控制台，互相抢」；原先这里是无条件新开，唯一的防双窗手段
         只是"90 秒内刚有人开过就跳过"，间隔一超就失效 ⇒ 越重启越多窗口。
      ① 我们自己的 WebView2 窗口（`一键启动.exe --console <url>`，前提 `webview_ready()`）；
      ② 只有①确实不成立（exe 缺 / 缺 DLL / 没装 WebView2 运行时 / 启动抛异常）才回退浏览器。
    返回报告里如实写 `how`（`reuse` / `webview` / `browser` / `skip`）与 `why`，绝不假报"已在我们窗口里打开"。
    """
    url = url or console_url()
    if not url:
        return {"ok": False, "how": "", "why": "拿不到控制台地址（config.json 里没有 server.port）"}
    # 🔴 2026-09-18 修（作者在另一台机器实测：「更新之后，一键启动不弹窗口，还得再点一次」）：
    #   原来这里是**先抢锁、抢不到就 `skip` 返回**，而"锁新鲜"只证明"90 秒内有人开过"，
    #   **不证明屏幕上真的有一个控制台窗口**——更新完新机器人起来时会开一次窗并落锁；用户紧接着点
    #   「一键启动」，这一跳判"锁新鲜 ⇒ 机器人侧已打开" ⇒ **直接 skip、什么都不弹**；等 90 秒锁过期
    #   再点才出来（＝"还得再点一次"）。⇒ 顺序反过来：**先看真窗口**（在就复用，不占锁）；不在才谈锁，
    #   而且锁抢不到也要**等窗口出现**，等不到就照开 —— 锁只用来防"同时开两个"，不许吞掉"根本没有窗口"。
    try:
        _ex0 = int(find_console_window() or 0)
        _ex0 = _ex0 if (_ex0 and _u().IsWindow(_ex0)) else 0
    except Exception:
        _ex0 = 0
    if not _ex0 and take_lock:
        _got = False
        try:
            from .util import take_console_lock
            _got = bool(take_console_lock())
        except Exception:
            _got = False
        if not _got:
            for _ in range(int(_WAIT_WINDOW_TRIES)):     # 最多等 6 秒（可能别人正在开）
                time.sleep(0.5)
                try:
                    _w = int(find_console_window() or 0)
                except Exception:
                    _w = 0
                if _w and _u().IsWindow(_w):
                    _ex0 = _w
                    break
            if not _ex0:
                log.warning("开窗锁被别人拿着、但 6 秒内一个控制台窗口都没有 ⇒ 照开"
                            "（锁不阻止「根本没有窗口」的情况）")
    ready = webview_ready()
    try:
        from .util import write_console_url
        # 只把**活着的**地址落盘（2026-09-18）：判据/试验里传进来的死链（例：随机空闲端口
        # `…:39998`）一旦被记下，启动器下次就照它开窗 ⇒ 一屏 `ERR_CONNECTION_REFUSED`（实测踩过）。
        if url and _url_live(url):
            write_console_url(url)             # 顺手把地址落盘：别的入口（启动器/托盘）直接读，别再自己拼
        elif url:
            log.warning("不落盘控制台地址（端口连不上，留着会害下次开窗）：%s", url.split("?")[0])
    except Exception:
        pass
    # ⚠️ 2026-09-17 修（用户报「现在这里有 4 个控制台，它们可能相互抢」）：
    #   实测：4 个「群相 控制台」窗口**各由一个 `一键启动.exe` 托管**，来自 4 次**间隔 >90 秒**的重启——
    #   而这里原来是**无条件新开**（唯一防双窗手段是"90 秒内刚有人开过就跳过"，间隔一超就失效）⇒ 越重启越多窗。
    #   正解＝**先复用已经开着的那个窗口**（只抬起来 + 闪任务栏，不动光标、不要求可见；可能短暂置前约 1~3 秒后自动还回），找不到才新开。
    try:
        _ex = int(find_console_window() or 0)
    except Exception:
        _ex = 0
    # ⛔ 2026-09-22 加（第十五轮 **V-R15-3** · 网友报「打不开控制台」）：**先把"死页"这件事说清楚**。
    #   复用分支只 `flash`/抬起、**从不导航或刷新** ⇒ 窗口里若是 `ERR_CONNECTION_REFUSED` 或 401 的旧页，
    #   用户怎么点都是那一屏死页。这里**如实留痕**（要打开的那个地址连不上 ⇒ 很可能控制台服务没在跑）。
    #   ⚠️ 这一版**不改复用语义**（窗口在就复用，2026-09-18 定的"不攒窗口"口径不放宽）——
    #   真正"把死页刷回来"需要一个 `--console-reuse` 的导航口（C# 侧加 `CoreWebView2.Navigate`），
    #   属下一版的事；这里只保证用户/我们**看得见原因**（日志里有这一行）。
    if _ex and url:
        try:
            if not _url_live(url):
                log.warning("已有控制台窗口，但要打开的地址 %s 连不上（很可能控制台服务没在跑）——"
                            "仍按既定口径复用它；若窗口里是错误页，请点「重启」或重新「一键启动」",
                            str(url).split("?")[0])
        except Exception:
            pass
    if _ex and _u().IsWindow(_ex):
        try:
            if str(mode or "") == "quiet":
                # 🔴 2026-09-18（用户实测反馈：「我在打游戏，这玩意还是会跳出来」）：
                #   机器人**自己**在后台开的窗（启动时那一次、以及任何自动调用）**不许把控制台抬到前台** ——
                #   只闪任务栏就够了：用户想看的自然会点任务栏，不想看的不该被顶出游戏。
                #   要抬起来只有一种情况：**用户自己点了**「打开控制台」（调用方显式要求 attention）。
                flash(_ex)
                return {"ok": True, "how": "reuse", "why": "已复用在后台开着的控制台窗口（只闪任务栏，不抬前台）",
                        "hwnd": _ex, "ready": ready, "raised": False}
            _rp = raise_without_stealing(_ex)
            return {"ok": True, "how": "reuse", "why": "已复用开着的控制台窗口（不新开）",
                    "hwnd": _ex, "ready": ready, "raise": _rp}
        except Exception as e:
            log.debug("复用控制台窗口失败，改为新开：%s", e)
    # ⛔ 2026-09-22 加（**同一屏 ERR_CONNECTION_REFUSED 的第二个入口**）：地址**现在是死的**吗？
    #   机器人还没起来/刚被关掉时，`console_url()` 只能给出"配置文件里那个端口"——它没人听；
    #   老实现照样 `Process.Start(浏览器, 死地址)` ⇒ 用户看到的就是「无法访问此页面 / 127.0.0.1 拒绝连接」。
    #   ① 自家 WebView2 窗口：**照开**（C# 侧撞到导航失败会显示我们自己的"正在重试"页并在端口起来后自动接上，
    #      比一屏 Edge 错误页强得多，也不会把用户引到"程序坏了"）；② 浏览器兜底：**没人在听就不开**，
    #      如实返回 `how=dead`，由调用方（启动器/一键启动）用自家面板告诉用户"控制台还没就绪"。
    _live = False
    try:
        _live = _url_live(url)
    except Exception:
        _live = False
    if ready["ok"]:
        try:
            subprocess.Popen([os.path.join(ROOT, "一键启动.exe"), "--console", url],
                             creationflags=0x08000000)
            return {"ok": True, "how": "webview", "why": "", "ready": ready, "live": _live}
        except Exception as e:
            ready["why"] = "自家窗口启动异常：%s" % e
    if not _live:
        return {"ok": False, "how": "dead", "ready": ready, "live": False,
                "why": ("控制台地址 %s 没人应答（多半是机器人没在跑/还没就绪）⇒ "
                        "这次**不开一屏 ERR_CONNECTION_REFUSED**；请点「一键启动」把控制台拉起来，"
                        "或稍等几秒后重试" % str(url).split("?")[0])}
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
        return {"ok": True, "how": "browser", "why": ready["why"], "ready": ready, "live": True}
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
