# -*- coding: utf-8 -*-
"""深色样本的绿底行裁切导出，供肉眼确认"底是什么色、字是什么色"。"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'C:\Users\ptmou\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples\local'
OUT = os.path.join(os.path.dirname(__file__), '..', '报告', 'ocr瓶颈-20260930')

from agent import chat_ocr as co  # noqa: E402

CASES = [
    ('local-024-ScreenShot_2026-09-29_225621_538.png', '深色-绿底行-窄列表'),
    ('local-022-ScreenShot_2026-09-29_225605_285.png', '深色-绿底行-宽列表'),
]

for name, tag in CASES:
    img = Image.open(os.path.join(BASE, name)).convert('RGB')
    bands = co.green_bands(img)
    if not bands:
        print('%s 无绿带' % name)
        continue
    b = bands[0]
    gs = co._green_span(img, b['y0'], b['y1'])
    x0, x1 = int(gs[0]), int(gs[1])
    crop = img.crop((x0, b['y0'], x1, b['y1']))
    # 放大 4 倍便于肉眼
    big = crop.resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
    p = os.path.join(OUT, '%s.png' % tag)
    big.save(p)
    print('%s  绿带 y=%d..%d  x=%d..%d  -> %s (%dx%d)'
          % (name[:34], b['y0'], b['y1'], x0, x1, os.path.basename(p),
             big.width, big.height))
