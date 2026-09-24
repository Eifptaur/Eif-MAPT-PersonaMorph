# -*- coding: utf-8 -*-
"""丙-33 诊断：border 带的热差异到底是「接缝」还是「抓图间隙的内容动态本底」。

探针已存 off / on1 / on2 三张 PNG（off 先抓，on1/on2 开波纹后相隔 10 tick）。
- d_on  = |on1 - off|  —— seamcheck 用的差图
- d_base= |on1 - on2|  —— 同为开波纹、间隔≈抓图间隙 ⇒ 纯内容动态本底
若 d_base 在 border 带同样热 ⇒ 热差异是本底（视频壁纸/海浪逐帧在动），非接缝；
若 d_base≈0 而 d_on 热 ⇒ 真接缝（render vs grab 或波纹管线），继续深挖。
"""
import os

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
S = os.path.join(HERE, "shots")
X, Y, W, H = 510, 232, 1080, 440          # rect_dev（_c32_seam.json 同款）


def load(name):
    return np.asarray(Image.open(os.path.join(S, name)).convert("RGB")).astype(np.int16)


def border_stats(a):
    d = np.abs(a).max(axis=2)
    return {
        "top6_max": int(d[Y:Y + 6, X:X + W].max()),
        "top6_mean": round(float(d[Y:Y + 6, X:X + W].mean()), 2),
        "left6_max": int(d[Y:Y + H, X:X + 6].max()),
        "bottom6_max": int(d[Y + H - 6:Y + H, X:X + W].max()),
        "right6_max": int(d[Y:Y + H, X + W - 6:X + W].max()),
        "hot_top6": int(np.count_nonzero(d[Y:Y + 6, X:X + W] > 6)),
    }


on1 = load("c31-wave-on1.png")
on2 = load("c31-wave-on2.png")
off = load("c31-wave-off.png")

out = {
    "d_on_vs_off": border_stats(on1 - off),
    "d_base_on1_vs_on2": border_stats(on1 - on2),
    "interior_mean_on": round(float(np.abs(on1 - off).max(axis=2)[Y + 72:Y + H - 72, X + 72:X + W - 72].mean()), 2),
    "interior_mean_base": round(float(np.abs(on1 - on2).max(axis=2)[Y + 72:Y + H - 72, X + 72:X + W - 72].mean()), 2),
}
# 热像素的 x 坐标分布（top 带）：零星=未初始化/锐利点，整段=系统性偏移
d_on2d = np.abs(on1 - off).max(axis=2)
hot = np.nonzero(d_on2d[Y:Y + 6, X:X + W] > 6)[0]
out["hot_x_first20"] = [int(v) for v in hot[:20]]
out["hot_x_count"] = int(hot.size)
print(out)
