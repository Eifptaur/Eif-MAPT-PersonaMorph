# -*- coding: utf-8 -*-
import io
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
import sec_meta  # noqa: E402

out = []
for sec in sorted(sec_meta.secs().keys()):
    try:
        panels_qt.build_panel(THEMES["light"], sec)
        out.append("OK   %s" % sec)
    except Exception:
        out.append("FAIL %s\n%s" % (sec, traceback.format_exc()))
io.open(os.path.join(os.environ["TEMP"], "pm_diag_all.txt"), "w",
        encoding="utf-8").write("\n".join(out))
print("written", len(out))
