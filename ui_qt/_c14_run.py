# -*- coding: utf-8 -*-
"""丙-14 黑屏二分跑壳基建（独立进程，不改动 shell.py / selftest.py）。

两种模式：
  shoot <theme> <wait_ms> <outname>
      真屏显示 Shell → 等 wait_ms → 用 PIL ImageGrab 抓「系统合成后的真实窗口画面」
      （不是 QWidget.grab，后者会重画内容、掩盖黑屏）→ 落 ui_qt/shots/<outname>.png
      → 输出 JSON：{black, mean_bright, frac_dark, w, h, png}
  boot <theme> <run_ms>
      同上真屏跑，但分段计时：import / Shell.__init__ / show / 首帧 paint / 首次探活 HTTP，
      输出 JSON。探活连不上后端是常态（壳仍应渲染），正是判定黑屏的基线。

判黑逻辑：whale/light 主题正常底图不是黑；若窗口中心区平均亮度很低且近黑像素占比极高
⇒ 判定黑屏。具体阈值与原始数值一起输出，供人工复核。
"""
from __future__ import annotations

import os
import sys
import time
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))


def _record_first_paint(Shell, store):
    orig = Shell.paintEvent

    def patched(self, ev):
        if store["t"] is None:
            store["t"] = time.perf_counter()
        return orig(self, ev)

    Shell.paintEvent = patched


def _wrap_probe(Shell, store):
    orig = Shell._probe

    def patched(self):
        t0 = time.perf_counter()
        try:
            return orig(self)
        finally:
            store.append(time.perf_counter() - t0)

    Shell._probe = patched


def _capture_window(w, outname):
    import ctypes
    import ctypes.wintypes
    from PIL import ImageGrab
    import numpy as np

    hwnd = int(w.winId())
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.25)  # 让前台/合成稳定一帧
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    bbox = (r.left, r.top, r.right, r.bottom)
    img = ImageGrab.grab(bbox)
    p = HERE / "shots" / f"{outname}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(p))
    arr = np.asarray(img.convert("RGB"), dtype=float)
    h, wd = arr.shape[0], arr.shape[1]
    # 中心区（避开圆角透明边与顶栏）
    y0, y1 = int(h * 0.14), int(h * 0.86)
    x0, x1 = int(wd * 0.16), int(wd * 0.84)
    sub = arr[y0:y1, x0:x1, :]
    mx = sub.max(axis=2)
    mean_bright = float(sub.mean())
    frac_dark = float((mx < 25).mean())
    # whale/light 正常底图非黑；中心区近全黑才判黑
    black = bool(mean_bright < 35 or frac_dark > 0.9)
    return {
        "black": black,
        "mean_bright": round(mean_bright, 1),
        "frac_dark": round(frac_dark, 3),
        "w": int(wd),
        "h": int(h),
        "png": str(p),
    }


def _shoot(theme, wait_ms, outname):
    t_start = time.perf_counter()
    import PySide6  # noqa: F401
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QTimer
    from stylekit_qt import THEMES, apply_font_to_app, resolve_family
    from shell import Shell, set_per_monitor_dpi

    fp = {"t": None}
    _record_first_paint(Shell, fp)

    set_per_monitor_dpi()
    app = QApplication.instance() or QApplication(sys.argv)
    t_import = time.perf_counter()
    t = THEMES[theme]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
    apply_font_to_app(app, t)
    t_init0 = time.perf_counter()
    w = Shell(t)
    w.move(80, 80)
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
    w.show()
    app.processEvents()
    t_show = time.perf_counter()
    init_ms = (t_init0 - t_import) * 1000
    show_ms = (t_show - t_init0) * 1000

    result = {}

    def doit():
        r = _capture_window(w, outname)
        r["import_ms"] = (t_import - t_start) * 1000
        r["init_ms"] = init_ms
        r["show_ms"] = show_ms
        r["first_paint_ms"] = (fp["t"] - t_start) * 1000 if fp["t"] else None
        result.update(r)
        try:
            w.close()
        except Exception:
            pass
        app.quit()

    QTimer.singleShot(int(wait_ms), doit)
    app.exec()
    print(json.dumps(result, ensure_ascii=False))


def _boot(theme, run_ms):
    t_start = time.perf_counter()
    import PySide6  # noqa: F401
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QTimer
    from stylekit_qt import THEMES, apply_font_to_app, resolve_family
    from shell import Shell, set_per_monitor_dpi

    fp = {"t": None}
    _record_first_paint(Shell, fp)
    probes = []
    _wrap_probe(Shell, probes)

    set_per_monitor_dpi()
    app = QApplication.instance() or QApplication(sys.argv)
    t_import = time.perf_counter()
    t = THEMES[theme]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
    apply_font_to_app(app, t)
    t_init0 = time.perf_counter()
    w = Shell(t)
    w.move(80, 80)
    w.show()
    app.processEvents()
    t_show = time.perf_counter()
    init_ms = (t_init0 - t_import) * 1000
    show_ms = (t_show - t_init0) * 1000

    def doit():
        out = {
            "import_ms": (t_import - t_start) * 1000,
            "init_ms": init_ms,
            "show_ms": show_ms,
            "first_paint_ms": (fp["t"] - t_start) * 1000 if fp["t"] else None,
            "first_probe_ms": (probes[0] * 1000) if probes else None,
            "probe_count": len(probes),
            "first_paint_after_show_ms": ((fp["t"] - t_show) * 1000) if fp["t"] else None,
        }
        print(json.dumps(out, ensure_ascii=False))
        try:
            w.close()
        except Exception:
            pass
        app.quit()

    QTimer.singleShot(int(run_ms), doit)
    app.exec()


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "shoot"
    if mode == "shoot":
        theme = args[1] if len(args) > 1 and args[1] in ("whale", "light", "normal") else "whale"
        wait_ms = int(args[2]) if len(args) > 2 else 3000
        outname = args[3] if len(args) > 3 else "c14_shot"
        _shoot(theme, wait_ms, outname)
    elif mode == "boot":
        theme = args[1] if len(args) > 1 and args[1] in ("whale", "light", "normal") else "whale"
        run_ms = int(args[2]) if len(args) > 2 else 4000
        _boot(theme, run_ms)
    else:
        print(json.dumps({"error": "unknown mode"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
