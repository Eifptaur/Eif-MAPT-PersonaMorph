# -*- coding: utf-8 -*-
"""内存水位日志（PLAN 第 161 项 ⑤）。

**为什么要它**：运存问题最难的是"**看不出来是哪块在涨**" —— 泄漏时只能看到总量慢慢变大，
不知道去查哪个结构。这个模块只做一件事：**周期性把"总内存 + 几个已知缓存的条目数"记一行**，
超阈值打 WARNING ⇒ 事后翻日志就能看出"是哪个计数在跟内存一起涨"。

⛔ 刻意**不用 `tracemalloc`**：它开销大（要拦截所有分配），会给产品主流程加负担；
本模块的判据口径是"**RSS ＋ 已知计数器**"这种**零侵入**的组合。

⛔ 也刻意**不做自动清理**：这里只**看**，清理各自归各自的模块（如 `housekeeping`）。
   一个"看到就删"的水位线是很危险的东西（分不清"缓存该清"与"用户数据在涨"）。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

log = logging.getLogger("persona-morph")

#: 超过这个值（MB）打 WARNING（默认 2 GB —— 便携运行时本身占几百 MB，留足余量）
WARN_MB = 2048.0
#: 两次采样的间隔（秒）。900 s ＝ 15 min：够看出趋势，又不刷屏。
INTERVAL_S = 900.0

_timer = None
_lock = threading.Lock()
_last: dict = {}


def rss_mb() -> float:
    """当前进程的常驻内存（MB）。拿不到就返回 0.0（**不当错误** —— 日志功能不该影响运行）。

    ⛔ **必须显式声明 `argtypes`/`restype`**：`GetCurrentProcess()` 返回的句柄在 64 位下是
       `HANDLE`（64 位），ctypes 默认按 C `int`（32 位）处理 ⇒ **句柄被截断**、
       `GetProcessMemoryInfo` 直接失败返回 0（实测：不声明时读出来是 **0.0 MB**，
       声明后是 13.1 MB —— 一个"静默错值"，不报异常，最难查）。
    """
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            _psapi = ctypes.WinDLL("psapi")
            _psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC),
                                                    wintypes.DWORD]
            _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            _k32 = ctypes.WinDLL("kernel32")
            _k32.GetCurrentProcess.restype = wintypes.HANDLE
            _pmc = _PMC()
            _pmc.cb = ctypes.sizeof(_PMC)
            _h = _k32.GetCurrentProcess()
            if _psapi.GetProcessMemoryInfo(_h, ctypes.byref(_pmc), _pmc.cb):
                return round(_pmc.WorkingSetSize / 1048576.0, 1)
            return 0.0
        # 非 Windows（判据/lint 环境）：读 /proc，或退回 0
        with open("/proc/self/statm", encoding="utf-8") as f:
            pages = int(f.read().split()[1])
        return round(pages * (os.sysconf("SC_PAGE_SIZE") / 1048576.0), 1)
    except Exception:
        return 0.0


def counters() -> dict:
    """几个**已知会涨**的结构的条目数（只读、拿不到就跳过 —— 不许因为观测而改状态）。

    口径：**只看"按外部输入当键/按使用增长"的那几个**（其余缓存的键是封闭集合，不涨）。
    """
    out = {}
    try:
        from agent import chat_store as _cs
        st = getattr(_cs, "_STORE", None) or getattr(_cs, "STORE", None)
        if st is not None and hasattr(st, "chats"):
            out["chats"] = len(st.chats)
            out["chats_msgs"] = sum(len(v) for v in st.chats.values()) if isinstance(st.chats, dict) else 0
    except Exception:
        pass
    for name, mod, attr in (("meme_cache", "agent.meme_search", "_CACHE"),
                            ("voice_cache", "agent.voice", "_VOICE_CACHE"),
                            ("probe_cache", "agent.voice_models", "_probe_cache")):
        try:
            __import__(mod)
            m = sys.modules[mod]
            v = getattr(m, attr, None)
            if isinstance(v, dict):
                out[name] = len(v)
        except Exception:
            pass
    return out


def sample(now: float | None = None) -> dict:
    """采一次样 ⇒ `{mb, counters, delta_mb, thread, warn}`（并写一行日志）。"""
    mb = rss_mb()
    cs = counters()
    prev_mb = _last.get("mb")
    delta = round(mb - prev_mb, 1) if isinstance(prev_mb, (int, float)) and prev_mb else 0.0
    warn = mb >= WARN_MB
    rep = {"mb": mb, "counters": cs, "delta_mb": delta,
           "thread": threading.active_count(), "warn": warn}
    _last.update(rep)
    line = ("内存水位：RSS %.1f MB（较上次 %+.1f）· 线程 %d · %s"
            % (mb, delta, rep["thread"],
               " · ".join("%s=%d" % (k, v) for k, v in sorted(cs.items())) or "无计数器"))
    (log.warning if warn else log.info)(line)
    return rep


def _tick(interval_s: float):
    """周期回调：采一次样 → 再排下一次（⛔ 不用 while+sleep：那样会占住一个线程）。"""
    global _timer
    try:
        sample()
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
    with _lock:
        _timer = threading.Timer(max(30.0, float(interval_s)), _tick, args=(interval_s,))
        _timer.daemon = True
        _timer.start()


def start(interval_s: float = INTERVAL_S) -> bool:
    """开始周期采样（启动时调一次）。已在跑 ⇒ 返回 False（不重复起）。"""
    global _timer
    with _lock:
        if _timer is not None and _timer.is_alive():
            return False
        _timer = threading.Timer(max(30.0, float(interval_s)), _tick, args=(interval_s,))
        _timer.daemon = True
        _timer.start()
    return True


def stop() -> None:
    """停（判据收尾用；产品里让它是 daemon、随进程退出即可）。"""
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
