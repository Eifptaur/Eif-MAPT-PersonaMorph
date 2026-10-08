# -*- coding: utf-8 -*-
"""t7b4_pix2.py —— 先"看"头像列到底在 x 的哪一段，再量行距。

上一稿的 x 带假定错了（pane-150..pane-60 落到了左侧图标栏）。
正确做法：**先扫全图找"彩色方块列"** —— 头像 = 高饱和方框，
统计每个 x 列上"高饱和像素"的数量，头像列会成一个峰。
"""
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
    ("sizes-dark2-20260930-151519", "frame-000.png"),
    ("sizes-dark2-20260930-151519", "frame-040.png"),
    ("sizes-dark2-20260930-151519", "frame-033.png"),
]


def col_profile(im):
    """每列的"高饱和像素计数"。"""
    W, H = im.size
    px = im.load()
    prof = [0] * W
    for x in range(W):
        c = 0
        for y in range(0, H, 2):
            r, g, b = px[x, y]
            if max(r, g, b) - min(r, g, b) > 60:   # 明显有色
                c += 1
        prof[x] = c
    return prof


def row_profile(im, x0, x1):
    """给定 x 带，每行的"高饱和像素计数"。"""
    W, H = im.size
    px = im.load()
    prof = [0] * H
    for y in range(H):
        c = 0
        for x in range(x0, x1, 2):
            r, g, b = px[x, y]
            if max(r, g, b) - min(r, g, b) > 60:
                c += 1
        prof[y] = c
    return prof


def main():
    from PIL import Image, ImageDraw

    for d, png in CASES:
        p = os.path.join(BASE, d, png)
        if not os.path.isfile(p):
            continue
        im = Image.open(p).convert("RGB")
        W, H = im.size
        cp = col_profile(im)
        # 找彩色列峰（只看左半，避开聊天区气泡）
        half = W // 2
        # 平滑
        sm = [sum(cp[max(0, i-2):i+3]) / 5.0 for i in range(half)]
        top = sorted(range(half), key=lambda i: -sm[i])[:1]
        # 取彩色列密集段：阈值 = 全局左半最大 × 0.3
        thr = sm[top[0]] * 0.30 if top else 0
        xs = [i for i in range(half) if sm[i] >= thr]
        if xs:
            # 取最左的一段
            seg = []
            cur = [xs[0]]
            for a, b in zip(xs, xs[1:]):
                if b - a <= 6:
                    cur.append(b)
                else:
                    seg.append(cur); cur = [b]
            seg.append(cur)
            band = seg[0]
            bx0, bx1 = band[0], band[-1]
        else:
            bx0, bx1 = 0, 0
        rp = row_profile(im, bx0, bx1) if bx1 > bx0 else []
        mu = statistics.mean(rp) if rp else 0
        sd = statistics.pstdev(rp) if rp else 1
        thr2 = mu + 0.8 * sd
        peaks, y = [], 0
        while rp and y < H:
            if rp[y] > thr2:
                y2 = y
                while y2 < H and rp[y2] > thr2 * 0.7:
                    y2 += 1
                seg2 = rp[y:y2]
                peaks.append(y + seg2.index(max(seg2)))
                y = y2
            else:
                y += 1
        steps = [b - a for a, b in zip(peaks, peaks[1:]) if 40 <= b - a <= 200]
        pitch = statistics.median(steps) if steps else None
        print("%-14s W=%5d | 头像列 x=[%d,%d] | 尖峰%2d pitch=%-6s"
              % (png, W, bx0, bx1, len(peaks), pitch))
        print("             peaks=%s" % peaks[:14])
        print("             steps=%s" % steps[:14])
        dr = ImageDraw.Draw(im)
        for cy in peaks:
            dr.line([(0, cy), (W, cy)], fill=(255, 0, 0), width=2)
        dr.rectangle([bx0, 0, bx1, H], outline=(0, 255, 0), width=3)
        tag = "L" if "150538" in d else "D"
        im.crop((0, 0, min(W, 500), min(H, 1000))).save(
            os.path.join(OUTD, "t7b4-pix2-%s-%s" % (tag, png)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
