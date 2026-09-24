# -*- coding: utf-8 -*-
import os
import sys
import traceback

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_qt")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from stylekit_qt import THEMES  # noqa: E402

import panels_qt  # noqa: E402

try:
    panels_qt.build_panel(THEMES["light"], "wechat")
    print("BUILD OK")
except Exception:
    tb = traceback.format_exc()
    io.open(os.path.join(os.environ["TEMP"], "pm_diag_tb.txt"), "w",
            encoding="utf-8").write(tb)
    print("TB written")
