# -*- coding: utf-8 -*-
"""probe_content_ratio.py —— 直观说明"抓到的图里到底有多少内容"（不是只有底色）

做法：对每张图，把「与主色接近的像素」涂白、其余保留 ⇒ 剩下的就是**真正的内容像素**；
     再统计内容像素占比 + 输出可视化图。用来对比：
       · 我方 PrintWindow 抓的独立窗
       · 我方 BitBlt 抓的（含抓到别人窗口那张）
       · 用户给的完整截图（真值参照）
用法：./runtime/python/python.exe -u scripts/probe_content_ratio.py
"""
import logging
import os
import sys
from collections import Counter

logging.disable(logging.INFO)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from PIL import Image  # noqa: E402

D = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "_临界样本")
CASES = [
    ("我方 PrintWindow flag=2", "popup-grab-pwflag2.png"),
    ("我方 BitBlt(屏幕DC)", "popup-grab-bitblt-captureblt.png"),
    ("用户给的完整截图", "popup-用户截图.png"),
]


def main():
    print("=" * 78)
    print("内容占比分析：把「接近主色的像素」涂白后，还剩多少真内容")
    print("=" * 78)
    for tag, fn in CASES:
        p = os.path.join(D, fn)
        if not os.path.isfile(p):
            print("  %-24s 缺文件 %s" % (tag, fn))
            continue
        im = Image.open(p).convert("RGB")
        w, h = im.size
        px = im.load()
        c = Counter()
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                r, g, b = px[x, y][:3]
                c[(r // 8 * 8, g // 8 * 8, b // 8 * 8)] += 1
        tot = sum(c.values()) or 1
        bg, bgn = c.most_common(1)[0]
        # 涂白"接近主色"的像素（每通道差 <=14），其余保留
        out = Image.new("RGB", (w, h), (255, 255, 255))
        po = out.load()
        keep = 0
        for y in range(h):
            for x in range(w):
                r, g, b = px[x, y][:3]
                if abs(r - bg[0]) <= 14 and abs(g - bg[1]) <= 14 and abs(b - bg[2]) <= 14:
                    continue
                po[x, y] = (r, g, b)
                keep += 1
        ratio = 100.0 * keep / float(w * h)
        f2 = os.path.join(D, "content-only-" + fn)
        out.save(f2)
        print("  %-24s %-11s 主色=%s 占%.1f%%   ⇒ 内容像素 %.2f%%  （已存 %s）"
              % (tag, "%dx%d" % (w, h), str(bg), 100.0 * bgn / tot, ratio,
                 os.path.basename(f2)))
    print()
    print("判读：主色 = 出现最多的那个颜色（本机是浅灰白，聊天区背景色）；")
    print("      '内容像素占比' 就是**除背景以外的真内容**（文字/气泡/输入框/头像/按钮）所占的比例。")
    print("      · 若只有 1~3% ⇒ 基本只抓到背景，**没有内容**；")
    print("      · 正常的聊天窗口应在 8~20%（视消息多少）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
