# -*- coding: utf-8 -*-
"""丙-10 拍图公用：字体注册 + 建壳 + 落盘（离屏，不闪真机屏）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)


def _register() -> int:
    from PySide6.QtGui import QFontDatabase

    n = 0
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                if QFontDatabase.addApplicationFont(p) >= 0:
                    n += 1
            except Exception:
                pass
    return n


def mk_shell(key: str = "light"):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from stylekit_qt import THEMES, apply_font_to_app, ensure_fonts, resolve_family

    app = QApplication.instance() or QApplication(sys.argv)
    _register()
    ensure_fonts()
    t = THEMES[key]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
    apply_font_to_app(app, t)
    from shell import Shell

    w = Shell(t)
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(1180, 760)
    w.show()
    app.processEvents()
    return w, t


def save(w, name: str) -> str:
    HERE.joinpath("shots").mkdir(parents=True, exist_ok=True)
    p = HERE / "shots" / f"{name}.png"
    w.grab().save(str(p), "PNG")
    return f"{p} ({os.path.getsize(p) // 1024} KB)"
