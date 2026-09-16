# -*- coding: utf-8 -*-
"""微信窗口「借用 → 归还」（2026-09-15 用户拍板：方案 A 用完还原）。

**要解决的矛盾**：`wechat._limit_wechat_window()` 为了不让驱动库的布局校准失效（窗口尺寸与校准
差 >15% 时库会忽略校准、坐标漂移），每次取 GUI 都把主窗钉到 1160×900 —— 于是用户手动拉过的尺寸
会被我们改掉。他问：「不是说要限位吗，为什么我的微信窗口还是被改了」。方案 A＝**借来用、用完还**。

三条硬规矩（与最高目标「不打扰」一致）：
  · 归还只撤销**我们自己那一次**改动：当前 rect 已经不等于「我们钉的那一版」（用户中途又动过）⇒
    **不还**，把窗口交回用户，免得把他的动作也抹掉；
  · 归还走 `SetWindowPos(..., SWP_NOZORDER|SWP_NOACTIVATE)`：不动光标、不打扰你（可能短暂置前约 1~3 秒后自动还回）、不改 Z 序；
  · 窗口已经没了（`IsWindow` 假）⇒ 静默清状态，不报错、不重试。

**活动信号** `touch()`：两个咽喉点会调它——`input_backend.select_backend()`（每次取后端＝一次输入
动作）与 `wechat._limit_wechat_window()`（每次取 GUI）。空闲 `IDLE_S` 秒后由 daemon 看门线程归还。

可关：`ui.restore_window_after_use=False` ⇒ 借了不还（回到旧行为＝永久钉尺寸）。
有账：`snapshot()` 给控制台/日志；借与还都写 log（谁的窗口、多大、什么时候还的）。
"""
from __future__ import annotations

import atexit
import ctypes
import json
import logging
import os
import threading
import time

log = logging.getLogger("persona-morph")

IDLE_S = 60.0        # 空闲多久算「用完了」（这期间没有任何输入动作就归还）
_POLL_S = 1.0

_test_api = None     # 自检用的替身（None ⇒ 用真 user32）
_lock = threading.Lock()
_state = {"borrowed": False, "hwnd": 0, "rect": None, "forced": None,
          "at": 0.0, "last_touch": 0.0, "restored": 0, "skipped": 0,
          "last_reason": "", "last_skip": ""}
_watcher = None


def _api():
    """拿 user32（判据里可换成替身）。"""
    if _test_api is not None:
        return _test_api
    u = ctypes.windll.user32
    try:
        u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.GetWindowRect.restype = ctypes.c_bool
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
        u.SetWindowPos.restype = ctypes.c_bool
    except Exception:
        pass
    return u


def enabled() -> bool:
    """读配置（默认开）。读不到配置时按「开」处理——宁可用完还回去。"""
    try:
        from .config import get_config
        return bool((get_config().get("ui") or {}).get("restore_window_after_use", True))
    except Exception:
        return True


def _persist_path() -> str:
    """借用记录的落盘位置（`data/window_borrow.json`）—— 给「被强杀/崩溃」兜底用。"""
    try:
        from .config import ROOT
        d = os.path.join(ROOT, "data")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "window_borrow.json")
    except Exception:
        return ""


def _persist() -> None:
    """把当前借用状态落盘（没借用 ⇒ 删掉记录）。**调用点必须在 `_lock` 之外**。"""
    p = _persist_path()
    if not p:
        return
    with _lock:
        s = dict(_state)
    try:
        if not s["borrowed"]:
            if os.path.exists(p):
                os.remove(p)
            return
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"hwnd": s["hwnd"], "rect": list(s["rect"] or []),
                       "forced": list(s["forced"]) if s["forced"] else None,
                       "at": s["at"]}, f)
    except Exception:
        pass


def recover(reason: str = "上次进程留下的借用") -> bool:
    """进程侧兜底：上次被**强杀/崩溃**留下的借用记录 ⇒ 现在还回去（只还我们自己那一版）。

    为什么需要（2026-09-15 跨机 P16② 实测）：`一键关闭.exe` 强杀时 `atexit` 跑不到，
    窗口就停在「钉住」状态 ⇒ 下次谁先碰到这个模块，就先还一次。
    """
    p = _persist_path()
    if not p or not os.path.exists(p):
        return False
    with _lock:
        if _state["borrowed"]:
            # 本进程**正借着**呢（记录就是我们自己刚写的）⇒ 不是"陈旧记录"，别乱还。
            return False
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        try:
            os.remove(p)
        except Exception:
            pass
        return False
    hwnd = int(d.get("hwnd") or 0)
    rect = list(d.get("rect") or [])
    forced = d.get("forced")
    if not hwnd or len(rect) != 4:
        try:
            os.remove(p)
        except Exception:
            pass
        return False
    with _lock:
        _state.update({"borrowed": True, "hwnd": hwnd, "rect": tuple(rect),
                       "forced": tuple(forced) if forced else None,
                       "at": float(d.get("at") or 0), "last_touch": time.time()})
    log.info("发现上次留下的窗口借用记录（hwnd=%s → %s），现在还原", hwnd, rect)
    return restore(reason)


def rect_of(hwnd: int):
    """窗口当前 rect（拿不到返回 None）。"""
    try:
        from ctypes import wintypes
        r = wintypes.RECT()
        if not _api().GetWindowRect(ctypes.c_void_p(int(hwnd)), ctypes.byref(r)):
            return None
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def note_original(hwnd: int, rect=None) -> bool:
    """**改窗口之前**记下原始 rect（同一次借用期间不覆盖）。返回是否新借了一次。"""
    if not enabled():
        return False
    recover()                       # 先还掉上次进程留下的借用（强杀/崩溃的场景，见 P16②）
    rect = rect_of(hwnd) or rect    # ⚠️ 以**当下**的 rect 为准：recover 之后窗口可能已经变了
    if not rect:
        return False
    with _lock:
        if _state["borrowed"] and int(_state["hwnd"]) == int(hwnd):
            _state["last_touch"] = time.time()          # 同一次借用：只刷新活动时间
            return False
        _state.update({"borrowed": True, "hwnd": int(hwnd), "rect": tuple(rect),
                       "forced": None, "at": time.time(), "last_touch": time.time()})
    _persist()
    _start_watcher()
    log.info("借用了微信窗口几何：hwnd=%s 原 rect=%s（用完会自动还原）", hwnd, tuple(rect))
    return True


def note_forced(rect) -> None:
    """记下「我们把它改成了多少」——归还时用它判断当前这版是不是我们改的。"""
    with _lock:
        if _state["borrowed"]:
            _state["forced"] = tuple(rect) if rect else None
            _state["last_touch"] = time.time()
    _persist()


def touch() -> None:
    """刷新活动时间（每次输入动作/取 GUI 都调）。"""
    with _lock:
        if _state["borrowed"]:
            _state["last_touch"] = time.time()


def restore(reason: str = "idle") -> bool:
    """归还。返回是否真的处理了一次借用（含「按规矩不还」的情况）。"""
    with _lock:
        if not _state["borrowed"]:
            return False
        hwnd, orig, forced = int(_state["hwnd"]), _state["rect"], _state["forced"]
    skipped = ""
    try:
        if not orig:
            skipped = "没有原始 rect"
        else:
            u = _api()
            try:
                alive = bool(u.IsWindow(ctypes.c_void_p(hwnd)))
            except Exception:
                alive = True
            cur = rect_of(hwnd)
            if not alive:
                skipped = "窗口已经没了"
            elif forced and cur and tuple(cur) != tuple(forced):
                skipped = "窗口已被用户改过（cur=%s ≠ 我们钉的 %s）" % (cur, forced)
            else:
                u.SetWindowPos(ctypes.c_void_p(hwnd), None, int(orig[0]), int(orig[1]),
                               int(orig[2] - orig[0]), int(orig[3] - orig[1]), 0x0004 | 0x0010)
                log.info("已还原微信窗口几何：hwnd=%s → %s（%s）", hwnd, orig, reason)
    except Exception as e:                                   # noqa: BLE001
        skipped = "还原失败：%s" % str(e)[:120]
    with _lock:
        _state.update({"borrowed": False, "hwnd": 0, "rect": None, "forced": None,
                       "last_reason": reason, "last_skip": skipped})
        if skipped:
            _state["skipped"] += 1
            log.info("不还原微信窗口几何：%s", skipped)
        else:
            _state["restored"] += 1
    _persist()
    return True


def _start_watcher() -> None:
    global _watcher
    with _lock:
        if _watcher is not None and _watcher.is_alive():
            return
        _watcher = threading.Thread(target=_watch, name="pm-window-borrow", daemon=True)
        _watcher.start()
    try:
        atexit.register(lambda: restore("exit"))
    except Exception:
        pass


def _watch() -> None:
    while True:
        time.sleep(_POLL_S)
        with _lock:
            borrowed = bool(_state["borrowed"])
            idle = time.time() - float(_state["last_touch"] or 0)
        if borrowed and idle >= IDLE_S:
            restore("空闲 %.0fs" % idle)


def snapshot() -> dict:
    """给控制台/日志的现状快照。"""
    with _lock:
        s = dict(_state)
    s["enabled"] = enabled()
    s["idle_s"] = IDLE_S
    s["idle"] = round(time.time() - float(s.get("last_touch") or 0), 1) if s.get("borrowed") else None
    return s
