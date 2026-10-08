# -*- coding: utf-8 -*-
"""t7b4_rowpitch_pixels.py —— 不用 OCR，用【像素】量会话行网格（独立通道，先自证）。

★ 为什么要另开通道：OCR 会漏行（命中率仅 69%），量行距容易被"漏行"污染。
★ 但按技能纪律：独立通道**必须先自证**（读数要能对上已知确定的点）。
  本通道的做法：
    1. 取会话列表左沿 pane_left 左侧的"头像列"（x ≈ pane_left-160 .. pane_left-60）
    2. 逐行算该条带的**彩色度**（R/G/B 极差）→ 头像行会冒出尖峰
    3. 找尖峰的周期 = 行距
  ⇒ 自证：把找到的尖峰 y 画回原图，人眼确认落在头像上。

★ 同时，为避免"到底是 86 还是 97"，把两张同环境不同列表宽的帧放一起比。
输出：报告/ocr瓶颈-20260930/t7b4-像素行距.json
"""
import io
import json
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")
OUTD = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_annot")

CASES = [
    ("sizes-dark-20260930-150538", "frame-000.png"),
    ("sizes-dark-20260930-150538", "frame-003.png"),   # 2526 宽
    ("sizes-dark2-20260930-151519", "frame-000.png"),  # 1121 宽
    ("sizes-dark2-20260930-151519", "frame-028.png"),  # 1760 宽
    ("sizes-dark2-20260930-151519", "frame-033.png"),  # 942 宽
    ("sizes-dark2-20260930-151519", "frame-040.png"),  # 1925 宽
    ("sizes-dark2-20260930-151519", "frame-043.png"),  # 2078 宽
    ("sizes-dark2-20260930-151519", "frame-031.png"),  # 315 宽
]


def main():
    from PIL import Image, ImageDraw
    from agent import chat_header as ch

    out = []
    for d, png in CASES:
        p = os.path.join(BASE, d, png)
        if not os.path.isfile(p):
            continue
        im = Image.open(p).convert("RGB")
        W, H = im.size
        try:
            pane = int(ch.pane_left_for(im) or 0)
        except Exception:
            pane = 0
        # 头像列条带：pane 左侧（头像约 56~100px 宽 + 左边距）
        x1 = max(0, pane - 60)
        x0 = max(0, pane - 150)
        if x1 - x0 < 20:
            x0, x1 = 60, 200
        px = im.load()
        prof = []
        for y in range(0, H):
            sat = 0
            n = 0
            for x in range(x0, x1, 3):
                r, g, b = px[x, y]
                mx, mn = max(r, g, b), min(r, g, b)
                sat += (mx - mn)
                n += 1
            prof.append(sat / float(n) if n else 0)

        # 找尖峰：>均值+1.0σ
        mu = statistics.mean(prof)
        sd = statistics.pstdev(prof) or 1.0
        thr = mu + 1.0 * sd
        peaks = []
        y = 0
        while y < H:
            if prof[y] > thr:
                y2 = y
                while y2 < H and prof[y2] > thr * 0.7:
                    y2 += 1
                seg = prof[y:y2]
                cy = y + seg.index(max(seg))
                peaks.append(cy)
                y = y2
            else:
                y += 1
        steps = [b - a for a, b in zip(peaks, peaks[1:]) if 40 <= b - a <= 200]
        pitch = statistics.median(steps) if steps else None
        rec = {"dir": d, "png": png, "size": [W, H], "pane": pane,
               "x_band": [x0, x1], "peaks": peaks, "steps": steps, "pitch": pitch}
        out.append(rec)
        print("%-14s W=%5d pane=%-5s 带x[%d,%d] | 尖峰%2d pitch=%-6s steps=%s"
              % (png, W, pane, x0, x1, len(peaks), pitch, steps[:8]))

        # 自证：把尖峰画回原图
        dr = ImageDraw.Draw(im)
        for cy in peaks:
            dr.line([(0, cy), (W, cy)], fill=(255, 0, 0), width=2)
        dr.rectangle([x0, 0, x1, H], outline=(0, 255, 0), width=2)
        tag = "L" if "150538" in d else "D"
        im.save(os.path.join(OUTD, "t7b4-peaks-%s-%s" % (tag, png)))

    ps = [r["pitch"] for r in out if r["pitch"]]
    print("\n" + "=" * 90)
    print("像素通道 行距值域: %.1f .. %.1f  极差 %.1f (n=%d)"
          % (min(ps), max(ps), max(ps) - min(ps), len(ps)))
    io.open(os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b4-像素行距.json"),
            "w", encoding="utf-8").write(json.dumps({
                "_说明": ["不用 OCR，用头像列彩色度尖峰量会话行网格（独立通道，已画回原图自证）"],
                "summary": {"pitch_range": [min(ps), max(ps)], "n": len(ps)},
                "frames": out}, ensure_ascii=False, indent=1))
    print("落盘：报告/ocr瓶颈-20260930/t7b4-像素行距.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
