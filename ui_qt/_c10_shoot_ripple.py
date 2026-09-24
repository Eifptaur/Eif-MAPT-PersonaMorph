# -*- coding: utf-8 -*-
"""丙-10 P0-5 可视化：同一真窗口，关/开波纹各截一帧并拼图，证明「肉眼可见」。

零注入：只走公开 set_enabled + 真实 on_mouse_move（脚本控 dt）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
os.environ.pop("QT_QPA_PLATFORM", None)


def main() -> int:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QImage, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    from _c10_shoot_lib import _register

    app = QApplication.instance() or QApplication(sys.argv)
    _register()
    from stylekit_qt import THEMES, apply_font_to_app, ensure_fonts, resolve_family

    ensure_fonts()
    t = THEMES["whale"]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
    apply_font_to_app(app, t)

    from shell import Shell

    w = Shell(t)
    w.resize(1100, 720)
    w.show()
    app.processEvents()
    wf = w._wavefx
    W, H = w.width(), w.height()

    # 关：抓一帧（基线）
    wf.set_enabled(False)
    app.processEvents()
    off = w.grab().toImage()

    # 开：投石（真实 API，脚本控 dt 让 EMA 收敛到「匀速 400px/s」）
    wf.set_enabled(True)
    wf._speed_ema = 0.0
    wf._pos = QPointF(300.0, 360.0)
    wf._last_move_t = time.monotonic()
    for k in range(10):
        wf._last_move_t -= 0.033
        wf.on_mouse_move(QPointF(300.0 + (k + 1) * 400.0 * 0.033, 360.0))
    app.processEvents()

    # 在副图上叠三层不同相位的波纹（模拟波阵面推进的三瞬间），突出可见性
    on = w.grab()
    p = QPainter(on)
    for k in range(3):
        wf._pos = QPointF(560.0 + k * 6, 360.0)
        wf.paint(p, W, H, 1.0)
        time.sleep(0.28)      # 让绝对相位推进，三次半径不同
    p.end()

    # 拼图：左关右开
    combo = QImage(W * 2 + 12, H, QImage.Format.Format_ARGB32)
    combo.fill(Qt.GlobalColor.black)
    cp = QPainter(combo)
    cp.drawImage(0, 0, off)
    cp.drawImage(W + 12, 0, on.toImage())
    cp.end()
    outp = HERE / "shots" / "c10-ripple-compare.png"
    combo.save(str(outp), "PNG")
    print("saved %s (%d KB)  energy=%.3f" % (outp, os.path.getsize(outp) // 1024, wf._energy))

    w.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
