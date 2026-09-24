# -*- coding: utf-8 -*-
"""丙-10 P0-2/P0-3 渲染取证：vermat / wavefx 两页实际渲染出的控件类型与数量。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QPushButton, QComboBox, QLineEdit, QLabel, QTableWidget, QCheckBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import panels_qt  # noqa: E402
import sec_meta  # noqa: E402
from stylekit_qt import THEMES  # noqa: E402

t = THEMES["light"]
out = []
for sec in ("vermat", "wavefx", "wechat", "advanced", "cursor"):
    try:
        w = panels_qt.build_panel(t, sec)
    except Exception as e:  # noqa: BLE001
        out.append("%-10s BUILD-FAIL %s: %s" % (sec, type(e).__name__, e))
        continue
    counts = {}
    for cls in (QPushButton, QComboBox, QLineEdit, QCheckBox, QTableWidget):
        n = len(w.findChildren(cls))
        if n:
            counts[cls.__name__] = n
    labels = len(w.findChildren(QLabel))
    # 解析层对照
    meta = sec_meta.secs().get(sec)
    kinds = {}
    if meta:
        for r in meta.rows:
            kinds[r.kind] = kinds.get(r.kind, 0) + 1
    out.append("%-10s 渲染: %s  QLabel=%d  | 解析: %s"
               % (sec, counts or "(无控件)", labels, kinds))

open("_c10_render_probe_out.log", "w", encoding="utf-8").write("\n".join(out))
print("done")
