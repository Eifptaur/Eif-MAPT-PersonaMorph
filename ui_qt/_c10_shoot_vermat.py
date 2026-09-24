# -*- coding: utf-8 -*-
"""丙-10 P0-3 拍图（直接抓面板本体，绕开 ScrollArea 视口背景问题）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from _c10_shoot_lib import _register, save  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from stylekit_qt import THEMES, apply_font_to_app, ensure_fonts, resolve_family  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
_register()
ensure_fonts()
t = THEMES["light"]
fam = resolve_family(t.font_family)
if fam != t.font_family:
    for tk in THEMES.values():
        tk.font_family = fam
apply_font_to_app(app, t)

import panels_qt  # noqa: E402

w = panels_qt.build_panel(t, "vermat")
w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
w.resize(900, 900)
w.show()
app.processEvents()
print("vermat panel:", save(w, "c10-vermat-panel"))
w.close()
