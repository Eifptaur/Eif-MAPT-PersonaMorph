# -*- coding: utf-8 -*-
"""T7b 诊断：把标注帧的 OCR 框**全量**打出来（不预筛、不预设列表区），
目的是看清：
  ① OCR 到底读到了哪些框（文字 + 坐标 + 尺寸）；
  ② 哪些是"会话列表的名字行"，哪些是"搜索框/图标/标题/聊天区"噪声；
  ③ 真值 first_row_y=372（浅色）/ 74（深色）在框表里对应哪一条。

★ 不改文件、不点微信。只打印，然后人工定"第一行"判法。
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
D = json.load(open(os.path.join(BASE, "_annot", "标注真值.json"), encoding="utf-8"))

TARGETS = [
    ("frame-000.png", "sizes-dark-20260930-150538", 407, 372),
    ("frame-001.png", "sizes-dark-20260930-150538", 407, 372),
    ("frame-003.png", "sizes-dark-20260930-150538", 407, 372),
    ("frame-000.png", "sizes-dark2-20260930-151519", 407, 238),
    ("frame-028.png", "sizes-dark2-20260930-151519", 45, 74),
    ("frame-043.png", "sizes-dark2-20260930-151519", 45, 74),
]

for png, d, tpl, tfy in TARGETS:
    path = os.path.join(BASE, "多尺寸样本", d, png)
    if not os.path.exists(path):
        print("!! missing", path)
        continue
    img = Image.open(path).convert("RGB")
    W, H = img.size
    items = co.recognize(img, timeout=30.0) or []
    print("=" * 90)
    print("%s / %s  %dx%d   truth pane_left=%s first_row_y=%s  OCR框=%d"
          % (d, png, W, H, tpl, tfy, len(items)))
    print("-" * 90)
    # 按 y 排序打印全部
    for i, (t, x, y, w, h) in enumerate(sorted(items, key=lambda r: r[2])):
        mark = ""
        cy = y + h // 2
        if abs(cy - tfy) <= 12:
            mark = "  <<< 命中真值首行 y"
        print("  [%2d] y=%4d cy=%4d x=%4d w=%4d h=%3d  %-24s%s"
              % (i, y, cy, x, w, h, repr(str(t))[:24], mark))
