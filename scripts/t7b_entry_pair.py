# -*- coding: utf-8 -*-
"""T7b 终版：**用「名字行+预览行成对」判据量会话条目几何**。

★ 判据定死（本轮第三测数据直接推出，见 t7b-自洽校验-第三测.json）：
    实测：跨 8 帧 / 2 主题 / 6 宽度，OCR 量出的「相邻文字行中心距」众数**恒为 32px**，
         极差 **0**。而目视会话条目高 ≈ 63px ≈ 2 × 32。
    ⇒ 结论：OCR 把每个会话读成**两行**（名字行 y1、预览行 y2，y2-y1 ≈ 32）。
       **一个会话条目 = 一对相邻文字行**，条目高 ≈ 64，**条目中心 y ≈ y1 + 16**。
    ⇒ 这才是「第一行会话」的正确取法：
         取 x 在名字列区间内、y 最小的**一对**，其**条目中心** = 第一行会话中心。

与旧口径对比（旧 = "取列表区最靠上文字框中心"）：
    旧口径会被『搜索框』『头像图标』『时间角标』骗走，且在深色帧把 `pane_left` 当右沿用导致全滤空。

⚠️ 只读脚本。
"""
import sys
import os
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image
from agent import chat_ocr as co

BASE = os.path.join(ROOT, "报告", "ocr瓶颈-20260930")

FRAMES = [
    ("sizes-dark-20260930-150538", "frame-000.png", "light"),
    ("sizes-dark-20260930-150538", "frame-001.png", "light"),
    ("sizes-dark-20260930-150538", "frame-003.png", "light"),
    ("sizes-dark2-20260930-151519", "frame-000.png", "dark"),
    ("sizes-dark2-20260930-151519", "frame-028.png", "dark"),
    ("sizes-dark2-20260930-151519", "frame-043.png", "dark"),
]

# 名字列区间。实测：头像/图标列 x≈110..150；名字/预览文字列 x≈177..~400
X_NAME_LO = 165
X_NAME_HI = 520          # 会话列表文字列右界（时间角标 x≈336..452 也在此内，无所谓）
MIN_LEN = 2
PAIR_GAP = 32            # 名字行↔预览行 标准间距（第三测实测）
PAIR_TOL = 10            # 配对容差

# 目视复核值（本轮 3x 放大裁剪图读出，原坐标）：(第一条会话中心y, 条目高)
VISION = {
    ("sizes-dark-20260930-150538", "frame-000.png"): (115, 64),
    ("sizes-dark-20260930-150538", "frame-001.png"): (115, 64),
    ("sizes-dark-20260930-150538", "frame-003.png"): (115, 64),
    ("sizes-dark2-20260930-151519", "frame-000.png"): (115, 64),
    ("sizes-dark2-20260930-151519", "frame-028.png"): (90, 64),
    ("sizes-dark2-20260930-151519", "frame-043.png"): (90, 64),
}


def name_col_boxes(items):
    out = []
    for (t, x, y, w, h) in items:
        s = str(t).strip()
        if len(s) < MIN_LEN:
            continue
        if not (X_NAME_LO <= x <= X_NAME_HI):
            continue
        out.append((s, x, y, w, h))
    out.sort(key=lambda r: (r[2], r[1]))
    return out


def first_entry(boxes):
    """找第一对「名字行 + 预览行」，返回 (条目中心y, 条目高, 名字, 预览)。

    做法：从 y 最小的一行开始，若其下 PAIR_GAP±PAIR_TOL 内存在另一行
          ⇒ 视为一对（同一会话的两行）；条目中心 = 两行中心的均值。
    """
    if not boxes:
        return None
    cy = [y + h // 2 for (s, x, y, w, h) in boxes]
    for i in range(len(boxes)):
        y1 = cy[i]
        for j in range(i + 1, len(boxes)):
            d = cy[j] - y1
            if d < 20:
                continue
            if abs(d - PAIR_GAP) <= PAIR_TOL:
                mid = (y1 + cy[j]) / 2.0
                return (mid, d, boxes[i][0], boxes[j][0])
            if d > PAIR_GAP + PAIR_TOL:
                break
    # 找不到成对 → 退化为第一行中心
    return (cy[0], None, boxes[0][0], None)


print("=" * 100)
print("T7b 终版：名字行+预览行 成对判据")
print("=" * 100)

rows = []
for d, png, theme in FRAMES:
    path = os.path.join(BASE, "多尺寸样本", d, png)
    if not os.path.exists(path):
        continue
    img = Image.open(path).convert("RGB")
    W, H = img.size
    items = co.recognize(img, timeout=30.0) or []
    bx = name_col_boxes(items)
    fe = first_entry(bx)
    vis = VISION.get((d, png))

    print("\n%s / %s  %dx%d [%s]" % (d, png, W, H, theme))
    if fe:
        mid, gap, n1, n2 = fe
        print("  第一对：名字='%s'  预览='%s'  配对间距=%s" % (n1[:16], (n2 or "")[:16], gap))
        print("  → 条目中心 y = %.0f   条目高 = %s" % (mid, gap))
        if vis:
            print("  目视复核：中心 y = %d   条目高 = %d   ⇒ **误差 = %+.0f / %+.0f**"
                  % (vis[0], vis[1], mid - vis[0], (gap or 0) - vis[1]))
    else:
        mid = gap = None
        print("  ⚠️ 名字列无框（可能该帧布局不符或 OCR 未读数）")
    rows.append({"dir": d, "png": png, "theme": theme,
                 "entry_center_y": mid, "entry_h": gap,
                 "first_name": fe[2] if fe else None,
                 "first_preview": fe[3] if fe else None,
                 "vision": {"center_y": vis[0], "h": vis[1]} if vis else None})

print("\n" + "=" * 100)
ok = [r for r in rows if r["vision"] and r["entry_center_y"] is not None]
if ok:
    ec = [abs(r["entry_center_y"] - r["vision"]["center_y"]) for r in ok]
    eh = [abs((r["entry_h"] or 0) - r["vision"]["h"]) for r in ok]
    print("条目中心 y 误差：%s ⇒ 最大 %.0fpx 均值 %.1fpx（口径 ≤12px）｜≤12px 命中 %d/%d"
          % (["%.0f" % v for v in ec], max(ec), sum(ec) / len(ec), sum(1 for v in ec if v <= 12), len(ec)))
    print("条目高   误差：%s ⇒ 最大 %.0fpx 均值 %.1fpx" % (["%.0f" % v for v in eh], max(eh), sum(eh) / len(eh)))

outp = os.path.join(BASE, "t7b-成对判据-终测.json")
with open(outp, "w", encoding="utf-8") as fp:
    json.dump(rows, fp, ensure_ascii=False, indent=2)
print("落盘：" + outp)
