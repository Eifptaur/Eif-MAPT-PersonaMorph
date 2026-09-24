# -*- coding: utf-8 -*-
"""丙-32：接缝/方形量化验证——开/关波纹差异图在 src_rect 边界带的形态。

判据：
- rect 外差异应≈0（波纹不越模块边界）；
- rect 边界 2px 带内差异应≈0（边缘渐隐 + mask 合成 ⇒ 无缝）；
- rect 内部差异显著（波纹真的在扭）。
若「边界带差异 ≫ 内部边缘」 ⇒ 仍有硬接缝（方形可见）。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

# src_rect 从 _c31_wave2.json 读取（逻辑坐标），PNG 是设备像素 ⇒ 比较前乘 dpr。


def main():
    with open(os.path.join(HERE, "_c31_wave2.json"), encoding="utf-8") as f:
        meta = json.load(f)
    rx, ry, rw, rh = meta["src_rect"]
    ww, wh = meta.get("win") or [1120, 720]
    on = np.asarray(Image.open(os.path.join(HERE, "shots", "c31-wave-on1.png"))
                    .convert("RGB")).astype(np.int16)
    off = np.asarray(Image.open(os.path.join(HERE, "shots", "c31-wave-off.png"))
                     .convert("RGB")).astype(np.int16)
    d = np.abs(on - off).max(axis=2)          # 逐像素最大通道差（PNG=设备像素）
    dpr = d.shape[1] / float(ww)              # 屏幕 DPR = PNG 宽 / 窗口逻辑宽
    X, Y = int(round(rx * dpr)), int(round(ry * dpr))
    W, Hh = int(round(rw * dpr)), int(round(rh * dpr))
    F = int(round(48 * dpr))                  # 渐隐带（48 逻辑 px）的设备宽
    res = {"png_size": [d.shape[1], d.shape[0]], "dpr": round(dpr, 3),
           "rect_dev": [X, Y, W, Hh]}
    res["outside_left"] = float(d[Y:Y + Hh, X - 40:X - 4].mean())
    res["outside_right"] = float(d[Y:Y + Hh, X + W + 4:X + W + 40].mean())
    res["outside_above"] = float(d[Y - 40:Y - 4, X:X + W].mean())
    res["outside_below"] = float(d[Y + Hh + 4:Y + Hh + 40, X:X + W].mean())
    res["border_max_top"] = int(d[Y:Y + 6, X:X + W].max())
    res["border_max_bottom"] = int(d[Y + Hh - 6:Y + Hh, X:X + W].max())
    res["border_max_left"] = int(d[Y:Y + Hh, X:X + 6].max())
    res["border_max_right"] = int(d[Y:Y + Hh, X + W - 6:X + W].max())
    res["interior"] = float(d[Y + F:Y + Hh - F, X + F:X + W - F].mean())
    res["interior_max"] = int(d[Y:Y + Hh, X:X + W].max())
    res["seam_free"] = bool(
        res["border_max_top"] < 6 and res["border_max_bottom"] < 6
        and res["border_max_left"] < 6 and res["border_max_right"] < 6
        and res["outside_left"] < 1.0 and res["outside_right"] < 1.0
        and res["outside_above"] < 1.0 and res["outside_below"] < 1.0)
    with open(os.path.join(HERE, "_c32_seam.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))


main()
