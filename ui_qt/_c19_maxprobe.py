# -*- coding: utf-8 -*-
"""批0：最大化白条/漏桌面定位 —— 真窗口跑壳 → 最大化 → 几何对账 + 截图。"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import PySide6  # noqa: F401,E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from stylekit_qt import THEMES, apply_font_to_app, resolve_family  # noqa: E402

OUT = os.path.join(HERE, "shots")


def grab(w, name):
    p = os.path.join(OUT, name)
    try:
        w.grab().save(p)
    except Exception as e:  # noqa: BLE001
        p = "grab-fail:%r" % e
    return p


def main():
    from shell import Shell, set_per_monitor_dpi  # noqa: PLC0415

    set_per_monitor_dpi()
    app = QApplication.instance() or QApplication(sys.argv)
    t = THEMES["light"]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
    apply_font_to_app(app, t)
    w = Shell(t)
    w.move(80, 80)
    w.show()
    res = {"thickframe_ok": bool(getattr(w, "_thickframe_ok", None))}
    app.processEvents()

    def stage_normal():
        g, c = w.frameGeometry(), w.geometry()
        res["normal"] = {"frame": [g.x(), g.y(), g.width(), g.height()],
                         "client": [c.x(), c.y(), c.width(), c.height()],
                         "shot": grab(w, "c19-normal.png")}
        w.showMaximized()

    def stage_max():
        app.processEvents()
        g, c = w.frameGeometry(), w.geometry()
        scr = w.screen()
        ag, ng = scr.availableGeometry(), scr.geometry()
        res["max"] = {
            "isMaximized": w.isMaximized(),
            "frame": [g.x(), g.y(), g.width(), g.height()],
            "client": [c.x(), c.y(), c.width(), c.height()],
            "avail": [ag.x(), ag.y(), ag.width(), ag.height()],
            "native": [ng.x(), ng.y(), ng.width(), ng.height()],
            "dpr": round(float(scr.devicePixelRatio()), 2),
            "shot": grab(w, "c19-max.png"),
        }
        io.open(os.path.join(HERE, "_c19_max.json"), "w", encoding="utf-8").write(
            json.dumps(res, ensure_ascii=False, indent=1))
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass
        app.quit()

    QTimer.singleShot(1500, stage_normal)
    QTimer.singleShot(3000, stage_max)
    app.exec()


main()
