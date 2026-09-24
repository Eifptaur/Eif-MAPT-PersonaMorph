# -*- coding: utf-8 -*-
"""丙-8 H 取证：Popover 底色到底透不透？（offscreen 像素级实锤）

用户原话：「调节按钮和更新菜单……全透明的话，会和下面的字混在一起」。
本探针把 Popover 抓成像素，直接读中心/边角颜色，验证 QSS 实底是否真渲染。
"""

import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

app = QApplication([])

from stylekit_qt import THEMES
from popover import Popover

out = []


def snap(label, t_key, fix=False):
    t = THEMES[t_key]
    pop = Popover(t)
    if fix:
        # 假设验证：QFrame 的 QSS background 需要 WA_StyledBackground 才会自绘
        pop.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pop.add(QLabel("测试文字 ABC 🐋"))
    pop.setFixedWidth(240)
    pop.resize(240, 120)
    pm = pop.grab()
    img = pm.toImage()
    w, h = img.width(), img.height()
    center = img.pixelColor(w // 2, h // 2)
    corner = img.pixelColor(0, 0)
    out.append(f"[{t_key}] fix_WA_StyledBackground={fix}")
    out.append(f"  中心 {center.name(QColor.NameFormat.HexArgb)} a={center.alpha()}")
    out.append(f"  角点 a={corner.alpha()}（圆角外应透明 a=0）")
    out.append("")


for k in ("whale", "light", "dark"):
    snap("popover", k, fix=False)
    snap("popover", k, fix=True)

report = "\n".join(out)
print(report)
Path(HERE / "_c8_popprobe_out.log").write_text(report, encoding="utf-8")
