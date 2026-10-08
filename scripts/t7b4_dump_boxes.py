# -*- coding: utf-8 -*-
"""t7b4_dump_boxes.py —— 把几帧的原始 OCR 框全量打出来，找"结构不变量"。

目的：要求「找一个能跳过 DPI 的方法」。
思路：不看绝对像素，看**相对结构**。先把原始数据摆出来：
  · 每帧的全部框 (text, x, y, w, h) 按 y 排序
  · 会话列表的左右边界（用 chat_header.pane_left_for / 绿带）
  · 头像列在哪（名字列应紧邻头像右侧）
对比 86-pitch 帧与 97-pitch 帧，找出"跨档仍不变"的量。

用法：./runtime/python/python.exe -u scripts/t7b4_dump_boxes.py
"""
import io
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")

TARGETS = [
    ("sizes-dark2-20260930-151519", "frame-033.png"),   # 列表宽 329, pitch 85
    ("sizes-dark2-20260930-151519", "frame-040.png"),   # 窗口 1925, pitch 86
    ("sizes-dark-20260930-150538", "frame-000.png"),    # pitch 96.5
    ("sizes-dark2-20260930-151519", "frame-000.png"),   # 窗口 1121, pitch 97
    ("sizes-dark2-20260930-151519", "frame-031.png"),   # 窗口 315, pitch 95
]


def main():
    from PIL import Image
    from agent import chat_ocr as co
    from agent import chat_header as ch

    out = []
    for d, png in TARGETS:
        p = os.path.join(BASE, d, png)
        if not os.path.isfile(p):
            print("缺帧:", p)
            continue
        im = Image.open(p).convert("RGB")
        W, H = im.size
        try:
            pane_left = ch.pane_left_for(im)
        except Exception as e:
            pane_left = None
            print("pane_left_for 失败:", e)
        items = co.recognize(im, timeout=40.0) or []
        boxes = sorted([(str(t).strip(), x, y, w, h) for (t, x, y, w, h) in items],
                       key=lambda b: (b[2], b[1]))
        print("\n" + "=" * 104)
        print("%s / %s   窗口 %dx%d   列表左沿(pane_left)=%s" % (d, png, W, H, pane_left))
        print("=" * 104)
        print("%-28s %6s %6s %5s %5s %6s %8s" % ("text", "x", "y", "w", "h", "right", "cy"))
        for t, x, y, w, h in boxes:
            print("%-28s %6d %6d %5d %5d %6d %8.1f"
                  % (t[:28], x, y, w, h, x + w, y + h / 2.0))
        # 高度分布
        hs = sorted(set(b[4] for b in boxes))
        print("-- 框高取值集合:", hs)
        out.append({"dir": d, "png": png, "size": [W, H], "pane_left": pane_left,
                    "boxes": [list(b) for b in boxes]})

    outp = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "t7b4-原始框转储.json")
    io.open(outp, "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=1))
    print("\n落盘:", outp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
