# -*- coding: utf-8 -*-
"""t7b4_overlay_pick.py —— 把我"选中的名字框"画回原图，人眼核对。（兜底手法）

跑太多启发式都散，说明我在**猜结构**而不是**看结构**。
按技能纪律：连调两稿不收敛 ⇒ 停止调参，**把候选画回原图**。
"""


import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
BASE = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")
OUT = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_annot")

CASES = [
    ("sizes-dark2-20260930-151519", "frame-033.png"),   # 列表宽 329
    ("sizes-dark2-20260930-151519", "frame-040.png"),   # 窗口 1925
    ("sizes-dark-20260930-150538", "frame-000.png"),    # light
    ("sizes-dark2-20260930-151519", "frame-000.png"),   # dark 1121
    ("sizes-dark2-20260930-151519", "frame-031.png"),   # 315 窄窗
]


def main():
    from PIL import Image, ImageDraw
    from agent import chat_ocr as co
    from agent import chat_header as ch

    for d, png in CASES:
        p = os.path.join(BASE, d, png)
        if not os.path.isfile(p):
            continue
        im = Image.open(p).convert("RGB")
        W, H = im.size
        try:
            pane = ch.pane_left_for(im)
        except Exception:
            pane = 0
        items = co.recognize(im, timeout=40.0) or []
        dr = ImageDraw.Draw(im)
        # 画列表左沿
        if pane:
            dr.line([(pane, 0), (pane, H)], fill=(255, 0, 255), width=3)
        for (t, x, y, w, h) in items:
            # 全部框：灰细框
            dr.rectangle([x, y, x + w, y + h], outline=(0, 160, 255), width=1)
            dr.text((x, max(0, y - 12)), "%d,%d,%d" % (x, y, h), fill=(255, 80, 0))
        outp = os.path.join(OUT, "t7b4-allboxes-%s-%s" % ("L" if "sizes-dark-2" in d or d.endswith("150538") else "D", png))
        im.save(outp)
        print("写出:", outp, " pane=", pane, " 框数=", len(items))


if __name__ == "__main__":
    sys.exit(main())
