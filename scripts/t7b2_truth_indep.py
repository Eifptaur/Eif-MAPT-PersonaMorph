# -*- coding: utf-8 -*-
"""t7b2_truth_indep.py —— T7b-2 真值回炉：用「非 OCR」的独立通道重读真值

⛔ 为什么必须新开一条通道（避免循环论证）
   T7b 定稿时，浅色帧首行 y=154 / 深色 252 是**从 OCR 框表读出来的**。
   如果拿它当"真值"再去算 OCR 的误差 ⇒ **拿程序验程序**（B2.5/第十一条红线）。
   ⇒ 本脚本**完全不调用 OCR**，只用「像素颜色」这一条独立通道：
       ① 行高：找"会话列表文字行"的**墨迹行**（暗色像素成行的密度峰）间距 —— 纯几何；
       ② 首行：第一条**绿底高亮条**（WeChat 选中会话的浅绿底）的上下边界；
       ③ 名字列左沿：头像列与名字列之间的**白/暗背景列**。
   ⇒ 只有"像素通道"与"OCR 通道"**各自独立给出接近的数**，才算真值站得住。

输出：报告/ocr瓶颈-20260930/t7b2-真值独立通道.json
用法：./runtime/python/python.exe -u scripts/t7b2_truth_indep.py
"""
import io
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SAMPLES = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")

#: 只量这 6 帧（与 T7b 定稿同一批，便于两通道直接对照）
TARGETS = [
    ("sizes-dark-20260930-150538",  "frame-000.png", 1886, 1421, "light"),
    ("sizes-dark-20260930-150538",  "frame-001.png", 2265, 1421, "light"),
    ("sizes-dark-20260930-150538",  "frame-003.png", 2526,  855, "light"),
    ("sizes-dark2-20260930-151519", "frame-000.png", 1121, 1091, "dark"),
    ("sizes-dark2-20260930-151519", "frame-028.png", 1760, 1358, "dark"),
    ("sizes-dark2-20260930-151519", "frame-043.png", 2078, 1490, "dark"),
]

#: 名字列 x 区间（来自 T7b 定稿的 x∈[170,470)，此处只用来取"一行墨迹"的采样窗）
NAME_X0, NAME_X1 = 170, 460
#: 只在会话列表纵向范围内找（跳过顶部搜索框那一带）
Y_TOP = 120


def _ink_profile(px, w, h, x0, x1, theme):
    """逐行统计 [x0,x1) 内"文字墨迹像素"的占比，返回长度 h 的 list。

    墨迹判据（不依赖任何固定阈值的方向性）：
      light 主题：文字是深色 ⇒ 亮度 < 中位亮度 - 40
      dark  主题：文字是浅色 ⇒ 亮度 > 中位亮度 + 40
    中位亮度取该帧该列窗的中位数 ⇒ 自适应深浅主题，不用拍阈值。
    """
    lum = []
    for y in range(h):
        row = []
        for x in range(x0, x1, 2):          # 隔点采样，够用且快
            r, g, b = px[x, y][:3]
            row.append(0.299 * r + 0.587 * g + 0.114 * b)
        lum.append(row)
    flat = sorted(v for r in lum for v in r)
    med = flat[len(flat) // 2]
    out = []
    for r in lum:
        if theme == "light":
            n = sum(1 for v in r if v < med - 40)
        else:
            n = sum(1 for v in r if v > med + 40)
        out.append(n / max(1, len(r)))
    return out


def _find_text_lines(prof, y_top, min_gap=6):
    """把墨迹profile切成"文字行"：连续 >0.02 的段 ⇒ 一段=一行。

    返回 [(y_center, y_start, y_end, peak_ratio)]，按 y 升序。
    """
    lines, cur = [], None
    for y in range(y_top, len(prof)):
        hot = prof[y] > 0.02
        if hot and cur is None:
            cur = [y, y]
        elif hot:
            cur[1] = y
        elif cur is not None:
            # 收段：太薄的（<3px）当噪声丢掉
            if cur[1] - cur[0] >= 3:
                seg = prof[cur[0]:cur[1] + 1]
                lines.append(((cur[0] + cur[1]) // 2, cur[0], cur[1], max(seg)))
            cur = None
    if cur is not None and cur[1] - cur[0] >= 3:
        seg = prof[cur[0]:cur[1] + 1]
        lines.append(((cur[0] + cur[1]) // 2, cur[0], cur[1], max(seg)))
    return lines


def _green_band(px, w, h, y_top):
    """找第一条"会话选中绿底"的纵向带。

    WeChat 选中项底色（浅色主题）≈ RGB(149,236,105) 系（高 G、G>R>B 明显）；
    深色主题选中底 ≈ RGB(60,80,60) 系（G 最高但整体暗）。
    ⇒ 判据：G 明显大于 R 且 G 明显大于 B（相对，不是绝对阈值）。
    """
    best = None
    for y in range(y_top, h):
        cnt = 0
        for x in range(20, 430, 4):
            r, g, b = px[x, y][:3]
            if g > r + 18 and g > b + 25 and g > 60:
                cnt += 1
        ratio = cnt / max(1, (430 - 20) // 4)
        if ratio > 0.45:
            if best is None:
                best = [y, y, ratio]
            else:
                if y - best[1] <= 2:
                    best[1] = y
                    best[2] = max(best[2], ratio)
    return best


def main():
    from PIL import Image
    report = {"_说明": [
        "T7b-2 真值回炉：**独立通道**（像素颜色/墨迹密度）重读真值，全程不调用 OCR。",
        "目的：与 T7b 的『OCR 通道读数』互证 —— 两条独立通道都给近似值 ⇒ 真值可用。",
        "行高定义：相邻两个**名字行**（墨迹最粗的行）中心间距。",
        "★ 与 OCR 通道互为独立：本文件任何数字都不来自 wxocr。",
    ], "frames": []}

    for d, png, w, h, theme in TARGETS:
        p = os.path.join(SAMPLES, d, png)
        if not os.path.isfile(p):
            print("缺文件：%s" % p)
            continue
        im = Image.open(p).convert("RGB")
        px = im.load()
        prof = _ink_profile(px, w, h, NAME_X0, NAME_X1, theme)
        lines = _find_text_lines(prof, Y_TOP)
        gb = _green_band(px, w, h, Y_TOP)

        # 名字行 = 峰高最大的一批（粗体名字的墨迹占比最高）；用峰高的 0.75 分位当界
        peaks = sorted(l[3] for l in lines)
        thr = peaks[int(len(peaks) * 0.72)] if peaks else 0
        names = [l for l in lines if l[3] >= thr]

        pitches = [names[i + 1][0] - names[i][0] for i in range(len(names) - 1)]
        # 条目行距 = 名字行间距里"占多数"的那个值（去重取众数）
        from collections import Counter
        c = Counter(pitches)
        pitch_mode = c.most_common(1)[0][0] if c else None

        rec = {
            "dir": d, "png": png, "size": [w, h], "theme": theme,
            "text_lines_n": len(lines),
            "name_lines": [[l[0], l[1], l[2], round(l[3], 3)] for l in names[:8]],
            "first_name_line_cy": names[0][0] if names else None,
            "green_band": ({"y0": gb[0], "y1": gb[1], "cy": (gb[0] + gb[1]) // 2,
                            "ratio": round(gb[2], 3)} if gb else None),
            "pitches_raw": pitches[:12],
            "pitch_mode": pitch_mode,
            "pitch_range": [min(pitches), max(pitches)] if pitches else None,
        }
        report["frames"].append(rec)
        print("\n=== %s/%s  %dx%d  %s ===" % (d, png, w, h, theme))
        print("  文字行数 %d ；名字行(峰高≥%.2f) %d 条" % (len(lines), thr, len(names)))
        for cy, y0, y1, pk in names[:6]:
            print("    名字行 cy=%-4d  段[%d,%d]  峰高 %.2f" % (cy, y0, y1, pk))
        print("  首名字行 cy = %s" % rec["first_name_line_cy"])
        print("  绿底带 = %s" % rec["green_band"])
        print("  行距众数 = %s  原始 %s" % (pitch_mode, pitches[:8]))

    out = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b2-真值独立通道.json")
    io.open(out, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))
    print("\n已写 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
