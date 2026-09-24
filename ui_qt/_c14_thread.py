# -*- coding: utf-8 -*-
"""丙-14 黑屏二分：**生产线程环境**复现器（不改 shell.py，只在本进程内 monkeypatch）。

复刻生产要点：
  · Qt 事件循环跑在 daemon 子线程（直接复用 ui_qt/app.py::start_qt_shell，生产唯一通路）；
  · 主线程模拟机器人主循环：CPU 突发（抢 GIL）+ 短睡；
  · 另起 2 个 worker 线程做 CPU+睡（模拟 webui / 微信守护线程）；
  · 窗口按标题「群相 控制台」FindWindow 找到（onestart 同款判据），
    GetWindowRect + PIL ImageGrab 抓**系统合成后的真实画面**（不是 QWidget.grab）。

用法：python _c14_thread.py <outname> [variant] [wait_ms]
  variant:
    base     现状代码原样（等价生产）
    noframe  把 _ensure_resize_style 换成 no-op（等价 QT_NO_THICKFRAME 逃生门）
    nocorner 把 _apply_round_corners 换成 no-op（隔离 DWM 圆角 × THICKFRAME 交互）
    defer    showEvent 里的注入改为 QTimer.singleShot(0,...)（验证候选修法 A）
    repaint  注入后补一次 repaint()（验证候选修法 B）
输出：stdout + ui_qt/shots/<outname>.json + <outname>.png
"""
from __future__ import annotations

import os
import sys
import json
import time
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

TITLE = "群相 控制台"


def _patch(variant: str, record: dict) -> None:
    """在本进程内对 Shell 打补丁（绝不改文件）。必须在 start_qt_shell 之前。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QWidget
    from shell import Shell

    # 首帧 paint 记录（所有 variant 都要）
    _orig_paint = Shell.paintEvent

    def _paint(self, ev):
        if record.get("first_paint") is None:
            record["first_paint"] = time.perf_counter()
        return _orig_paint(self, ev)

    Shell.paintEvent = _paint

    if variant == "noframe":
        Shell._ensure_resize_style = lambda self: None
    elif variant == "nocorner":
        Shell._apply_round_corners = lambda self: None
    elif variant == "defer":
        _orig_show = Shell.showEvent

        def _show(self, ev):
            QWidget.showEvent(self, ev)
            self._ocean.set_active(False)
            self._apply_round_corners()
            # 候选修法 A：注入推迟到 show 流程完全结束后（事件循环再进一拍）
            QTimer.singleShot(0, self._ensure_resize_style)

        Shell.showEvent = _show
    elif variant == "repaint":
        _orig_ers = Shell._ensure_resize_style

        def _ers(self):
            _orig_ers(self)
            # 候选修法 B：注入后强制重画
            try:
                self.repaint()
            except Exception:
                pass

        Shell._ensure_resize_style = _ers
    elif variant in ("brokenwave", "brokenwave-noframe"):
        # ⛔ 复现作者真机失败态（bc37525 半成品波纹 + 46ff1a0 同存期）：
        #   1) 强制打开 effect 挂载（生产靠 config ui.wave_fx.enabled=true + 当时无止血门）
        #   2) draw() 还原成 commit bc37525 的错签名写法（每帧 TypeError）
        #   brokenwave-noframe = 同上 + 跳过 THICKFRAME 注入（等价 QT_NO_THICKFRAME=1）
        #   ⇒ 若该态仍黑，则黑屏与 c13 无关、全在波纹 —— 交叉二分的关键一组。
        import wavefx as _wf
        from PySide6.QtWidgets import QGraphicsEffect

        _wf._LENS_MOUNT_OK = True

        def _broken_draw(self, painter):
            # 与 bc37525 原文一致：mode 当第一个参数 ⇒ TypeError 每帧
            src = self.sourcePixmap(QGraphicsEffect.PixmapPadMode.NoPad)
            if src.isNull():
                return
            painter.drawPixmap(0, 0, src)

        _wf._WaveLensEffect.draw = _broken_draw
        if variant == "brokenwave-noframe":
            Shell._ensure_resize_style = lambda self: None


def _busy_worker(stop: threading.Event) -> None:
    while not stop.is_set():
        t0 = time.perf_counter()
        x = 0
        while time.perf_counter() - t0 < 0.02:
            x += 1
        time.sleep(0.01)


def _main() -> None:
    outname = sys.argv[1] if len(sys.argv) > 1 else "c14-thread"
    variant = sys.argv[2] if len(sys.argv) > 2 else "base"
    wait_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 3000

    t0 = time.perf_counter()
    record: dict = {"variant": variant, "wait_ms": wait_ms}
    _patch(variant, record)

    # ── 模拟机器人的多线程环境 ──
    stop = threading.Event()
    workers = [threading.Thread(target=_busy_worker, args=(stop,), daemon=True) for _ in range(2)]
    for wth in workers:
        wth.start()

    # 生产同款：ui_qt.app.start_qt_shell（内部自己 import shell/stylekit）
    from ui_qt.app import start_qt_shell  # noqa: PLC0415

    ok = start_qt_shell(theme="whale")
    record["start_qt_shell_dispatched"] = bool(ok)

    # ── 主线程：机器人主循环式忙碌（CPU 抢 GIL + 短睡），同时等窗口出现 ──
    import ctypes
    import ctypes.wintypes
    from PIL import ImageGrab
    import numpy as np

    user32 = ctypes.windll.user32
    mypid = os.getpid()
    cand = []          # [(hwnd, title, w, h, visible, iconic)]
    found_at = None

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _enum_cb(hwnd, lparam):
        h = int(hwnd) if hwnd else 0
        if not h:
            return True
        pid = ctypes.c_uint(0)
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if int(pid.value) != mypid:
            return True
        n = user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(h, buf, n + 2)
        title = buf.value or ""
        if "群相" not in title:
            return True
        r = ctypes.wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        cand.append({
            "hwnd": h, "title": title,
            "w": r.right - r.left, "h": r.bottom - r.top,
            "visible": bool(user32.IsWindowVisible(h)),
            "iconic": bool(user32.IsIconic(h)),
        })
        return True

    deadline = time.time() + 15
    while time.time() < deadline:
        cand.clear()
        user32.EnumWindows(EnumProc(_enum_cb), None)
        # 主窗口判据：可见 + 面积最大（托盘隐藏小窗会被排除）
        vis = [c for c in cand if c["visible"] and c["w"] > 300 and c["h"] > 300]
        if vis:
            vis.sort(key=lambda c: c["w"] * c["h"], reverse=True)
            found_at = time.perf_counter()
            break
        # 主线程别闲着——保持 GIL 压力
        tq = time.perf_counter()
        while time.perf_counter() - tq < 0.015:
            pass
        time.sleep(0.05)

    record["find_elapsed_ms"] = ((found_at or time.perf_counter()) - t0) * 1000
    record["candidates"] = cand

    main_win = None
    if cand:
        vis = [c for c in cand if c["visible"] and c["w"] > 300 and c["h"] > 300]
        if vis:
            vis.sort(key=lambda c: c["w"] * c["h"], reverse=True)
            main_win = vis[0]

    result = dict(record)
    result["hwnd_found"] = bool(main_win)
    if main_win:
        hwnd = main_win["hwnd"]
        record["hwnd"] = hwnd
        # 等 wait_ms（从进程起算），期间继续模拟主循环
        while (time.perf_counter() - t0) * 1000 < wait_ms:
            tq = time.perf_counter()
            while time.perf_counter() - tq < 0.02:
                pass
            time.sleep(0.01)

        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.3)
        import ctypes.wintypes as wt

        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        img = ImageGrab.grab((r.left, r.top, r.right, r.bottom))
        shots = HERE / "shots"
        shots.mkdir(parents=True, exist_ok=True)
        png = shots / f"{outname}.png"
        img.save(str(png), "PNG")
        arr = np.asarray(img.convert("RGB"), dtype=float)
        h, wd = arr.shape[0], arr.shape[1]
        sub = arr[int(h * 0.14):int(h * 0.86), int(wd * 0.16):int(wd * 0.84), :]
        mx = sub.max(axis=2)
        mean_bright = float(sub.mean())
        frac_dark = float((mx < 25).mean())
        result.update({
            "png": str(png),
            "w": int(wd), "h": int(h),
            "mean_bright": round(mean_bright, 1),
            "frac_dark": round(frac_dark, 3),
            "black": bool(mean_bright < 35 or frac_dark > 0.9),
        })
    else:
        result.update({"png": None, "black": None, "note": "15s 内没找到窗口"})

    # Shell 类状态（注入是否真的发生）
    try:
        from shell import Shell as _S

        result["thickframe_ok"] = bool(_S._thickframe_ok)
        result["resize_style_done"] = bool(_S._resize_style_done)
    except Exception:
        pass
    fp = record.get("first_paint")
    result["first_paint_ms"] = (fp - t0) * 1000 if fp else None

    stop.set()
    js = json.dumps(result, ensure_ascii=False)
    shots = HERE / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{outname}.json").write_text(js, encoding="utf-8")
    print(js, flush=True)
    sys.stdout.flush()
    os._exit(0)   # daemon 线程直接收，不留窗口残留


if __name__ == "__main__":
    _main()
