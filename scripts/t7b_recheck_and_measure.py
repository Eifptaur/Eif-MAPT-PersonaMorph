# -*- coding: utf-8 -*-
"""T7b 第二步：**先复核真值，再量误差**。

★ 本轮（第十四轮）发现的关键问题：
    `标注真值.json` 里 `first_row_y` / `row_height` 两列**与图像不符**。
    证据（浅色 frame-000，裁剪区 560x520 原坐标）：
      - 图像上第一条会话 = `腾讯新闻`，名字中心 y ≈ 99
      - 绿底第二条 = `E`，名字中心 y ≈ 162
      - 相邻名字中心距（行高）≈ 63
      - 而真值写 first_row_y=372 / row_height=95  ⇒ **两个都对不上**
    ⇒ 拿这份真值算误差，会得出"OCR 差 288px"的**假结论**。
    ⇒ 本脚本改为：**先用"名字列"判据把 OCR 的行几何量出来**，
       再与"本轮程序辅助目视复核"的图像读值并列，**由人判定**。

判据（本轮定死，写进报告）：
    会话列表文字列 x 区间：`x_lo <= x <= x_hi`
      浅色/深色标准布局：文字列起于头像右侧，实测 x ≈ 177..190
      头像图标列 x ≈ 110..150（噪声）
    ⇒ 取 `x >= 160`（排除头像/图标）、`x <= pane_right`（排除右侧聊天区）
    且 文本**长度 >= 2 个字符**（排除单字符图标碎片）
    取 y 最小者 = **第一条会话的名字行**（不是绿底判据）

⚠️ 只读脚本：不点微信、不投递、不改原文件。
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

# ── 本轮"程序辅助目视复核"值（从 3x 放大裁剪图读出，原坐标）────────────
# 只对标准布局帧填；格式： (首行名字中心y, 行高, 第一行文字)
VISION = {
    ("sizes-dark-20260930-150538", "frame-000.png"): (99, 63, "腾讯新闻"),
    ("sizes-dark-20260930-150538", "frame-001.png"): (99, 63, "腾讯新闻"),
    ("sizes-dark-20260930-150538", "frame-003.png"): (99, 63, "腾讯新闻"),
    ("sizes-dark2-20260930-151519", "frame-000.png"): (99, 63, "腾讯新闻"),
    # #6/#8 深色宽列表：E 是第一条
    ("sizes-dark2-20260930-151519", "frame-028.png"): (74, 63, "E"),
    ("sizes-dark2-20260930-151519", "frame-043.png"): (74, 63, "E"),
}

X_LO = 160          # 排除头像/图标列
MIN_LEN = 2         # 至少 2 字符才算"文字"


def list_text_boxes(items, pane_right):
    """从 OCR 框里挑出「会话列表的名字/预览文字」框。

    排除：① x 落在头像图标列（x < X_LO）
          ② x 超出会话列表右沿（> pane_right + 8）
          ③ 单字符碎片（len(text.strip()) < MIN_LEN）
    """
    out = []
    for (t, x, y, w, h) in items:
        s = str(t).strip()
        if len(s) < MIN_LEN:
            continue
        if x < X_LO:
            continue
        if x > pane_right + 8:
            continue
        out.append((s, x, y, w, h))
    out.sort(key=lambda r: r[2])
    return out


print("=" * 96)
print("T7b 第二步：真值复核 + 名字列判据量行几何")
print("=" * 96)

_ANN = json.load(open(os.path.join(BASE, "_annot", "标注真值.json"), encoding="utf-8"))
all_rows = []
for f in _ANN.get("frames", []):
    png = f.get("png")
    d = f.get("dir")
    if d is None:
        continue
    path = os.path.join(BASE, "多尺寸样本", d, png)
    if not os.path.exists(path):
        continue
    truth_pl = f.get("pane_left_x")
    truth_fy = f.get("first_row_y")
    truth_rh = f.get("row_height")

    img = Image.open(path).convert("RGB")
    W, H = img.size
    items = co.recognize(img, timeout=30.0) or []

    pane_right = truth_pl if truth_pl else 400
    lb = list_text_boxes(items, pane_right)

    got_fy = got_rh = None
    top3 = []
    if lb:
        got_fy = lb[0][2] + lb[0][4] // 2
        top3 = [(s[:14], x, y + h // 2) for (s, x, y, w, h) in lb[:4]]
        # 行高：取前两个"名字行"的间距。
        # 名字行 vs 预览行的区别：同一会话的名字在上、预览在下，间距小于行高。
        # 简单可靠做法：找 ≥2 行，取出现次数最多的相邻差（众数），
        # 它就是"名字→名字"的间距。
        ys = [y + h // 2 for (s, x, y, w, h) in lb]
        diffs = {}
        for a, b in zip(ys, ys[1:]):
            dv = b - a
            if 20 <= dv <= 200:
                diffs[dv] = diffs.get(dv, 0) + 1
        if diffs:
            # 取"出现 >=2 次的最常见差"，否则取第一个
            cand = sorted(diffs.items(), key=lambda kv: (-kv[1], kv[0]))
            got_rh = cand[0][0] if cand[0][1] >= 2 else (cand[0][0] if cand else None)

    vis = VISION.get((d, png))
    vis_fy, vis_rh, vis_first = vis if vis else (None, None, None)

    def e(a, b):
        return "-" if (a is None or b is None) else "%+d" % (a - b)

    print("\n%s / %s  %dx%d" % (d, png, W, H))
    print("  真值(旧)   first_row_y=%-6s row_height=%-6s" % (truth_fy, truth_rh))
    print("  目视复核   first_row_y=%-6s row_height=%-6s  第一行='%s'" % (vis_fy, vis_rh, vis_first))
    print("  OCR(新判据) first_row_y=%-6s row_height=%-6s  误差(vs目视)=%s / %s" % (got_fy, got_rh, e(got_fy, vis_fy), e(got_rh, vis_rh)))
    print("  列表区前 4 框：%s" % top3)

    all_rows.append({
        "dir": d, "png": png, "size": [W, H],
        "truth_old": {"first_row_y": truth_fy, "row_height": truth_rh},
        "vision_recheck": {"first_row_y": vis_fy, "row_height": vis_rh, "first_text": vis_first},
        "ocr_new": {"first_row_y": got_fy, "row_height": got_rh, "top4": top3},
    })

outp = os.path.join(BASE, "t7b-真值复核与误差-第二测.json")
with open(outp, "w", encoding="utf-8") as fp:
    json.dump(all_rows, fp, ensure_ascii=False, indent=2)
print("\n落盘：" + outp)
