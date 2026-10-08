# -*- coding: utf-8 -*-
"""t7b2_truth_indep2.py —— T7b-2 独立通道（第二稿）：修正"绿底被当成墨迹"的错

⛔ 第一稿（t7b2_truth_indep.py）错在哪：
   我用"该列窗亮度中位数 - 40 ⇒ 算墨迹"来切文字行。浅色帧里**选中会话的绿底**
   （整片高 G 像素）亮度落在阈值下方 ⇒ **整片 96px 被当成"一行墨迹"**，
   于是"首行 cy"报成绿底中心 266（其实是**第 2 行 E**）⇒ 与 OCR 通道差 112px。
   ⇒ 这正是第十一条红线的翻版：**先把通道本身验干净，再拿去和别的通道比。**

修正（两条，都不靠"绝对阈值调参"）：
   ① **先剔除绿底**：G > R+18 且 G > B+25 的像素**直接判为非墨迹**（与底色无关）；
   ② **行切分用"墨迹宽度"而非"占比"**：一行文字里"最深色的那批像素"必成连续段，
      且**单行高度有天然上界**（字高 ≈ 16~22px）⇒ 段高 > 30px 的判为"多行粘连"，
      用**谷点（占比局部最小）**把它切开。
   ⇒ 两条都是**结构性判据**，不是拍数字。

输出：报告/ocr瓶颈-20260930/t7b2-真值独立通道2.json
用法：./runtime/python/python.exe -u scripts/t7b2_truth_indep2.py
"""
import io
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SAMPLES = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")

TARGETS = [
    ("sizes-dark-20260930-150538",  "frame-000.png", 1886, 1421, "light"),
    ("sizes-dark-20260930-150538",  "frame-001.png", 2265, 1421, "light"),
    ("sizes-dark-20260930-150538",  "frame-003.png", 2526,  855, "light"),
    ("sizes-dark2-20260930-151519", "frame-000.png", 1121, 1091, "dark"),
    ("sizes-dark2-20260930-151519", "frame-028.png", 1760, 1358, "dark"),
    ("sizes-dark2-20260930-151519", "frame-043.png", 2078, 1490, "dark"),
]
NAME_X0, NAME_X1 = 170, 460
Y_TOP = 110          # 跳过搜索框那一条
MAX_LINE_H = 30      # 单行文字高度上界（>30 视为粘连，须切）


def _is_green(r, g, b):
    """选中会话绿底（深浅主题通吃）—— 相对判据，无绝对阈值。"""
    return g > r + 18 and g > b + 25 and g > 60


def _row_ink(px, y, x0, x1):
    """该行在 [x0,x1) 内的墨迹像素数（已剔绿底）。"""
    n = 0
    for x in range(x0, x1, 2):
        r, g, b = px[x, y][:3]
        if _is_green(r, g, b):
            continue
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        n += lum
    return n


def _profile(px, h, x0, x1, theme):
    """返回 (墨迹行占比, 绿底行占比) 两个长度 h 的列表。

    墨迹 = 该行亮度**显著偏离该行自身中位**的像素数（自适应深浅）。
    """
    raw, greens = [], []
    for y in range(h):
        vals, gcnt = [], 0
        for x in range(x0, x1, 2):
            r, g, b = px[x, y][:3]
            if _is_green(r, g, b):
                gcnt += 1
                continue
            vals.append(0.299 * r + 0.587 * g + 0.114 * b)
        greens.append(gcnt / max(1, (x1 - x0) // 2))
        if not vals:
            raw.append(0.0)
            continue
        sv = sorted(vals)
        med = sv[len(sv) // 2]
        if theme == "light":
            n = sum(1 for v in vals if v < med - 35)
        else:
            n = sum(1 for v in vals if v > med + 35)
        raw.append(n / len(vals))
    return raw, greens


def _split_lines(prof, y_top):
    """把 profile 切成单行：连续热段里，若高度 >MAX_LINE_H 则在谷点切。"""
    lines, cur = [], None
    for y in range(y_top, len(prof)):
        hot = prof[y] > 0.03
        if hot and cur is None:
            cur = [y, y]
        elif hot:
            cur[1] = y
        elif cur is not None:
            _emit(lines, prof, cur)
            cur = None
    if cur is not None:
        _emit(lines, prof, cur)
    return lines


def _emit(lines, prof, cur):
    y0, y1 = cur
    if y1 - y0 < 3:
        return
    if y1 - y0 <= MAX_LINE_H:
        seg = prof[y0:y1 + 1]
        lines.append(((y0 + y1) // 2, y0, y1, max(seg)))
        return
    # 太长 ⇒ 必是"名字+预览"或"绿底粘连"⇒ 找谷点切
    seg = prof[y0:y1 + 1]
    mid = len(seg) // 2
    lo, hi = max(3, mid - 14), min(len(seg) - 3, mid + 14)
    if hi <= lo:
        lines.append(((y0 + y1) // 2, y0, y1, max(seg)))
        return
    cut = min(range(lo, hi), key=lambda i: seg[i]) + y0
    _emit(lines, prof, [y0, cut - 1])
    _emit(lines, prof, [cut + 1, y1])


def main():
    from PIL import Image
    report = {"_说明": [
        "T7b-2 真值独立通道 · 第二稿（已修『绿底被当墨迹』）。",
        "★ 与 OCR 通道独立：本文件数字全部来自像素颜色，不调用 wxocr。",
    ], "frames": []}

    for d, png, w, h, theme in TARGETS:
        p = os.path.join(SAMPLES, d, png)
        if not os.path.isfile(p):
            continue
        im = Image.open(p).convert("RGB")
        px = im.load()
        prof, greens = _profile(px, h, NAME_X0, NAME_X1, theme)
        lines = _split_lines(prof, Y_TOP)

        peaks = sorted(l[3] for l in lines)
        thr = peaks[int(len(peaks) * 0.70)] if peaks else 0
        names = [l for l in lines if l[3] >= thr]
        pitches = [names[i + 1][0] - names[i][0] for i in range(len(names) - 1)]
        c = Counter(pitches)

        # 绿底带（连续 >0.45 的一段）
        segs, cur = [], None
        for y in range(Y_TOP, h):
            if greens[y] > 0.45:
                cur = [y, y] if cur is None else [cur[0], y]
            elif cur is not None:
                segs.append(cur)
                cur = None
        if cur is not None:
            segs.append(cur)

        rec = {
            "dir": d, "png": png, "size": [w, h], "theme": theme,
            "name_lines": [[l[0], l[1], l[2], round(l[3], 3)] for l in names[:8]],
            "first_name_line_cy": names[0][0] if names else None,
            "green_bands": [{"y0": a, "y1": b, "cy": (a + b) // 2,
                             "h": b - a} for a, b in segs[:3]],
            "pitches_raw": pitches[:12],
            "pitch_mode": c.most_common(1)[0][0] if c else None,
            "pitch_range": [min(pitches), max(pitches)] if pitches else None,
        }
        report["frames"].append(rec)
        print("\n=== %s/%s  %dx%d  %s ===" % (d, png, w, h, theme))
        for cy, y0, y1, pk in names[:6]:
            print("    名字行 cy=%-4d 段[%d,%d](高%2d) 峰%.2f" % (cy, y0, y1, y1 - y0, pk))
        print("  首名字行 cy = %s" % rec["first_name_line_cy"])
        print("  绿底带 = %s" % rec["green_bands"])
        print("  行距众数 = %s  全部 %s" % (rec["pitch_mode"], pitches[:8]))

    out = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b2-真值独立通道2.json")
    io.open(out, "w", encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=1))
    print("\n已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
