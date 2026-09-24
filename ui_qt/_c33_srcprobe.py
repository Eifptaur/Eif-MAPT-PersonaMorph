# -*- coding: utf-8 -*-
"""丙-33 验证：_grab_src（shell.grab(QRect) 新路径）产物 vs grab 基线的一致性。

新抓源路径的 src_img 必须与窗口真实显示（grab 全窗）在 rect 区域逐像素一致
（interior≈0、border≈0）——「方形玻璃+细刮痕」的根因就是旧 render 路径产物错乱。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

X, Y, W, H = 510, 232, 1080, 440          # rect_dev（dpr=1.5）
S = os.path.join(HERE, "shots")


def to_rgba(img):
    """QImage → numpy(H,W,4)。分步持引用——链式临时 + constBits 会 access violation。"""
    c = img.convertToFormat(QImage.Format.Format_RGBA8888)
    b = bytes(c.constBits())
    return np.frombuffer(b, dtype=np.uint8).reshape(c.height(), c.width(), 4).copy()


def main():
    from stylekit_qt import THEMES, apply_font_to_app, resolve_family  # noqa: PLC0415
    from shell import Shell  # noqa: PLC0415

    app = QApplication.instance() or QApplication(sys.argv)
    t = THEMES["whale"]
    fam = resolve_family(t.font_family)
    if fam != t.font_family:
        for tk in THEMES.values():
            tk.font_family = fam
    apply_font_to_app(app, t)
    w = Shell(t)
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(1120, 720)
    w.show()
    app.processEvents()
    # 真实路径：手动喂光标 → _grab_src（内部 shell.grab(QRect)）
    from PySide6.QtCore import QPointF  # noqa: PLC0415

    w._wavefx._pos = QPointF(700.0, 300.0)    # 落在中间卡片上（与探针 lens_rect 同域）
    w._wavefx._grab_src()
    src_img = w._wavefx._src_img
    grab = to_rgba(w.grab().toImage())
    res = {"src_ok": src_img is not None and not src_img.isNull(),
           "src_size": None if src_img is None else [src_img.width(), src_img.height()],
           "src_dpr": None if src_img is None else src_img.devicePixelRatioF()}
    if src_img is not None and not src_img.isNull():
        s = to_rgba(src_img)
        g = grab[Y:Y + H, X:X + W]
        d = np.abs(g.astype(np.int16) - s.astype(np.int16)).max(axis=2)
        res["border_top6_max"] = int(d[:6].max())
        res["border_top6_mean"] = round(float(d[:6].mean()), 2)
        res["border_left6_max"] = int(d[:, :6].max())
        res["border_bottom6_max"] = int(d[-6:].max())
        res["border_right6_max"] = int(d[:, -6:].max())
        res["interior_mean"] = round(float(d[72:-72, 72:-72].mean()), 3)
        res["interior_max"] = int(d[72:-72, 72:-72].max())
        res["verdict"] = ("一致" if res["interior_mean"] < 1.0 and res["border_top6_max"] < 6
                          else "仍不一致")
    with open(os.path.join(HERE, "_c33_srcprobe.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))
    w.close()


if __name__ == "__main__":
    main()
