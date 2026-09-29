# -*- coding: utf-8 -*-
"""深色样本的 OCR 行为实测。

验证三件事：
  A. 深色样本里，绿底高亮行（选中行）能否被 green_bands 找到；
  B. 该行的字到底是"白字"还是"黑字"（用户口径：深色下会话列表字恒为白）；
  C. invert=True / invert=False 两种读法，哪种能读出名字。

全程只读，不改生产代码。
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'C:\Users\ptmou\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples\local'

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402


def lum(p):
    return 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]


CASES = [
    'local-022-ScreenShot_2026-09-29_225605_285.png',  # 2084x1158 L=541 宽绿带
    'local-024-ScreenShot_2026-09-29_225621_538.png',  # 同尺寸 窄绿带
    'local-031-ScreenShot_2026-09-29_225743_509.png',  # 1065 宽 L=276
    'local-033-ScreenShot_2026-09-29_225759_044.png',  # 1524 宽 L=396
    'local-001-2_.png',
    'local-007-_____2026-09-07_002204.png',
]

for name in CASES:
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        print('缺文件：%s' % name)
        continue
    img = Image.open(p).convert('RGB')
    w, h = img.size
    print('=' * 78)
    print('%s  (%dx%d)' % (name[:44], w, h))

    left = ch.pane_left_for(img) or int(w * ch.PANE_LEFT_REL)
    print('  面板左沿 left=%d' % left)

    # --- A. 绿底高亮行 ---
    bands = co.green_bands(img)
    print('  A. green_bands 命中 %d 条' % len(bands))
    if bands:
        y0, y1 = bands[0]['y0'], bands[0]['y1']
        print('     第 1 条 y=%d..%d（高 %d，score=%.3f）'
              % (y0, y1, y1 - y0, bands[0]['score']))
        # --- B. 绿带行的真实横向边界 + 字色极性 ---
        try:
            gs = co._green_span(img, y0, y1)
            print('     _green_span -> %s' % (gs,))
            gx0, gx1 = gs[0], gs[1] if len(gs) >= 2 else (None, None)
            if gx0 is not None:
                crop = img.crop((max(0, int(gx0)), y0, min(w, int(gx1)), y1))
                px = list(crop.getdata())
                if px:
                    lums = [lum(q) for q in px]
                    avg = sum(lums) / len(lums)
                    bright = sum(1 for v in lums if v > 160) / len(lums)
                    dark = sum(1 for v in lums if v < 80) / len(lums)
                    print('  B. 绿带行内：平均亮度=%.1f  亮点(>160)=%.2f  暗点(<80)=%.2f'
                          % (avg, bright, dark))
                    if bright < 0.20 and dark > 0.50:
                        print('     → 底暗、亮点少 ⇒ 白字+深绿底（符合用户口径）')
                    elif dark < 0.20 and bright > 0.50:
                        print('     → 底亮、暗点少 ⇒ 黑字+浅底')
        except Exception as e:
            print('     _green_span 异常：%r' % (e,))
    else:
        print('     ⚠️ 无绿底行 —— 深色下选中行可能不是"绿底"')

    # --- C. invert 两种读法（裁宽，排除裁框干扰）---
    x0 = max(0, left - 240)
    for inv in (False, True):
        crop = img.crop((x0, 30, w, h))
        try:
            texts = [str(t) for t, *_ in co.recognize_boost(
                crop, profile='fast', invert=inv)]
        except Exception as e:
            print('     invert=%-5s 异常 %r' % (inv, e))
            continue
        head = [t for t in texts[:8] if t.strip()]
        print('  C. invert=%-5s -> %2d 段  前几段=%s' % (inv, len(texts), head))
    print()
