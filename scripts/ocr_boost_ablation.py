# -*- coding: utf-8 -*-
"""**硬读档消融工具**（按需跑，不进 run_all）：单档成绩 × 耗时一览。

用途：以后再想动"多尺度/预处理/引擎"时，**先跑这个拿数据**，别凭感觉加档（这就是本轮把
20.5s/张压到 4.47s/张的方法）。当前结论（6 张 tier-1 图 / 31 条真值）：

  · 内置引擎九种预处理的**天花板是 26/31**（1x / 1.5x / 2x / 3x / 2x锐化 / 2x·3x灰度对比 /
    2x二值 / 3x反色 / 1x反色，全加起来也是 26）⇒ 剩下 5 条**只有第二引擎读得出**；
  · 内置单档里最强的是 **2x锐化（25/31，2.7s/6 张）**；2x 是 24、1x 是 23；
  · 第二引擎 **≈1s 固定开销**（400×40 的一条也要 1.10s，整图 2.63s）⇒ **裁 ROI 省不掉**；
  · 多线程分块**无收益**（4 块并行 3.20s vs 整图 2.47s）⇒ 已放弃。

原始问题：还有哪些**便宜的内置预处理**能拿下那 6 条（只有第二引擎能读的）？

思路：内置引擎很便宜（0.27s/张）⇒ 只要能找到一种预处理让它读到那 6 条，就能做到"既 100% 又快"。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.chdir(ROOT)
os.environ.setdefault("PM_NO_WINDOW_TOUCH", "1")

from PIL import Image, ImageFilter, ImageOps # noqa: E402

from agent import chat_ocr as O # noqa: E402
import ocr_bench_web as B # noqa: E402

TOT = {}


def rd(im, tag):
    t0 = time.time()
    try:
        r = [str(x[0]) for x in O.recognize(im)]
    except Exception as e:  # noqa: BLE001
        print("   （%s 失败：%s）" % (tag, str(e)[:40]))
        r = []
    TOT[tag] = TOT.get(tag, 0.0) + (time.time() - t0)
    return r


def variants(im):
    w, h = im.size
    g = im.convert("L")
    out = {
        "1x": rd(im, "1x"),
        "2x": rd(im.resize((w * 2, h * 2), 1), "2x"),
        "3x": rd(im.resize((w * 3, h * 3), 1), "3x"),
        "1.5x": rd(im.resize((int(w * 1.5), int(h * 1.5)), 1), "1.5x"),
        "2x锐化": rd(im.resize((w * 2, h * 2), 1).filter(ImageFilter.SHARPEN), "2x锐化"),
        "2x灰度对比": rd(ImageOps.autocontrast(g, cutoff=1).resize((w * 2, h * 2), 1).convert("RGB"), "2x灰度对比"),
        "2x二值": rd(ImageOps.autocontrast(g, cutoff=1).resize((w * 2, h * 2), 1)
                     .point(lambda v: 255 if v > 140 else 0).convert("RGB"), "2x二值"),
        "3x灰度对比": rd(ImageOps.autocontrast(g, cutoff=1).resize((w * 3, h * 3), 1).convert("RGB"), "3x灰度对比"),
        "3x反色": rd(ImageOps.invert(im.resize((w * 3, h * 3), 1).convert("RGB")), "3x反色"),
    }
    return out


def hit(lines, truths):
    return sum(1 for t in truths if any(O.norm(t) in O.norm(x) for x in lines))


CASES = []
for name, *truths in B.CASES:
    p = os.path.join(B.WEB, name)
    if os.path.exists(p):
        CASES.append((name, truths, Image.open(p).convert("RGB")))

# 每张图只跑一次预处理（下面组合复用缓存，别重复读）
CACHE = []
KEYS = list(variants(CASES[0][2]).keys())
for name, truths, im in CASES:
    CACHE.append((name, truths, variants(im)))

res = {k: [] for k in KEYS}
for name, truths, v in CACHE:
    for k in KEYS:
        res[k].append("%d/%d" % (hit(v[k], truths), len(truths)))

print("\n%-12s %s   合计   耗时" % ("单档（内置引擎）", "  ".join("图%d" % (i + 1) for i in range(len(CASES)))))
for k in KEYS:
    print("  %-10s %s   %2d/31   %.1fs" % (k, "  ".join(res[k]),
                                           sum(int(x.split("/")[0]) for x in res[k]), TOT.get(k, 0)))
print("\n组合（多用几档，仍然全内置）：")
for combo in (["1x", "2x"], ["1x", "2x", "2x锐化"], ["1x", "2x", "2x锐化", "2x灰度对比"],
              ["1x", "2x", "2x锐化", "2x灰度对比", "3x"], KEYS):
    tot = 0
    for name, truths, v in CACHE:
        lines = [x for k in combo for x in v[k]]
        tot += hit(lines, truths)
    print("  %-52s %2d/31" % ("+".join(combo), tot))
