# -*- coding: utf-8 -*-
"""t7b2_overlay_grid.py —— T7b-2 真值回炉：把"候选 y"画回原图，人眼逐框核对

⛔ 为什么改用"画回去看"（第二稿像素通道也失败了）
   我连着两稿用"自适应中位亮度"切文字行，两稿结论都自相矛盾
   （一稿把绿底当墨迹报 266；二稿把真名字行 154/252 漏掉报 184/282）。
   ⇒ 命中技能里的纪律：**阈值被我调过两次以上 ⇒ 方法错了，该换判据，不是继续调数字**。
   ⇒ 回到真值文件自己规定的口径：**"四列真值必须由人看图读出"**。
     本脚本只做一件事：**把候选 y 画成红/绿细线 + 数字**，让我在放大图上
     **看线与字是否对齐**，从而人工判定 cy。脚本**不给结论**。

输出：_annot/t7b2-grid-<帧名>.png（放大 2x，带 y 刻度）
用法：./runtime/python/python.exe -u scripts/t7b2_overlay_grid.py
"""


import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SAMPLES = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")
OUT = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_annot")

#: (目录, 帧, 裁剪区, 候选 y 列表) —— 候选来自两条通道的并集，让图自己说话
JOBS = [
    ("sizes-dark-20260930-150538", "frame-000.png", (100, 100, 500, 620),
     [154, 184, 220, 252, 266, 282, 315, 349, 379]),
    ("sizes-dark2-20260930-151519", "frame-028.png", (0, 60, 420, 660),
     [74, 120, 168, 217, 252, 282, 315, 349, 379]),
]


def main():
    from PIL import Image, ImageDraw
    for d, png, crop, ys in JOBS:
        p = os.path.join(SAMPLES, d, png)
        if not os.path.isfile(p):
            print("缺 %s" % p)
            continue
        im = Image.open(p).convert("RGB").crop(crop)
        S = 2
        im = im.resize((im.width * S, im.height * S), Image.LANCZOS)
        dr = ImageDraw.Draw(im)
        x0, y0 = crop[0], crop[1]
        for y in ys:
            zy = (y - y0) * S
            if zy < 0 or zy >= im.height:
                continue
            dr.line([(0, zy), (im.width, zy)], fill=(255, 0, 0), width=1)
            dr.text((2, zy + 1), str(y), fill=(255, 0, 0))
        # 横向刻度：每 50px 原坐标一条淡蓝线，便于估 x
        for x in range(x0 // 50 * 50, crop[2] + 1, 50):
            zx = (x - x0) * S
            if 0 <= zx < im.width:
                dr.line([(zx, 0), (zx, im.height)], fill=(120, 170, 255), width=1)
                dr.text((zx + 1, 2), str(x), fill=(0, 90, 255))
        f = os.path.join(OUT, "t7b2-grid-%s-%s.png" % (d.split("-")[1], png.replace(".png", "")))
        im.save(f)
        print("已存 %s  (%dx%d)" % (f, im.width, im.height))
    return 0


if __name__ == "__main__":
    sys.exit(main())
