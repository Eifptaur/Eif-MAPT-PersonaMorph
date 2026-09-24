# -*- coding: utf-8 -*-
"""丙-29：波纹「没起效」定位——①ui.wave_fx.enabled 当前值 ②开启后位移效果是否可见（截图对比）。"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import PySide6  # noqa: F401,E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QTimer, QPointF  # noqa: E402
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
    from config_io import read_path  # noqa: PLC0415

    set_per_monitor_dpi()
    app = QApplication.instance() or QApplication(sys.argv)
    t = THEMES["light"]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
    apply_font_to_app(app, t)
    w = Shell(t)
    w.move(60, 60)
    w.resize(1120, 720)
    w.show()

    res = {
        "cfg_enabled": read_path("ui.wave_fx.enabled"),
        "cfg_scale": read_path("ui.wave_fx.scale"),
        "mount_ok": bool(getattr(w, "_thickframe_ok", None)),
        "lens_mount": bool(getattr(sys.modules["wavefx"], "_LENS_MOUNT_OK", None)),
    }
    app.processEvents()

    def stage_on():
        w._wavefx.set_enabled(True)
        w._wavefx.on_mouse_move(QPointF(560, 360))
        w._wavefx._pos = QPointF(560, 360)
        app.processEvents()
        res["enabled_after"] = bool(w._wavefx._enabled)
        res["effect_attached"] = (w.graphicsEffect() is w._wavefx._fx)
        res["timer_active"] = w._wavefx.active
        # 位移核心直接采样：有波纹时同一区域两次相位输出是否不同（证明位移在动）
        from PySide6.QtGui import QImage  # noqa: PLC0415
        from PySide6.QtCore import QRect  # noqa: PLC0415

        img = QImage(200, 160, QImage.Format.Format_RGBA8888)
        img.fill(0xFF808080)
        rect = QRect(0, 0, 200, 160)
        o1 = w._wavefx._displace_region(img, rect, QPointF(100, 80), 120.0, 0.1)
        o2 = w._wavefx._displace_region(img, rect, QPointF(100, 80), 120.0, 0.6)
        try:
            res["displace_differs"] = (o1 is not None and o2 is not None
                                       and o1.bits().asstring() != o2.bits().asstring())
        except Exception:
            res["displace_differs"] = "n/a"
        res["shot_on"] = grab(w, "c29-wave-on.png")
        w._wavefx.set_enabled(False)
        app.processEvents()
        res["shot_off"] = grab(w, "c29-wave-off.png")
        io.open(os.path.join(HERE, "_c29_wave.json"), "w", encoding="utf-8").write(
            json.dumps(res, ensure_ascii=False, indent=1))
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass
        app.quit()

    QTimer.singleShot(1500, stage_on)
    app.exec()


main()
