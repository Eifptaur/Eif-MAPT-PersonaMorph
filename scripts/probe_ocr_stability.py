# -*- coding: utf-8 -*-
"""剥离变量：同一张图，只变右沿，看读出的段集合怎么变。

目的：确认 local-013 / local-031 的"退化"到底是"裁宽造成"还是"OCR 抖动"。
做法：对同一张图跑**多次相同输入**，看结果是否稳定（稳定性锚）。
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402


def read(img, x0, x1, zoom=2):
    crop = img.crop((x0, 30, x1, img.size[1]))
    if zoom > 1:
        crop = crop.resize((crop.width * zoom, crop.height * zoom))
    return [str(t).strip() for t, *_ in co.recognize(crop) if str(t).strip()]


CASES = [
    ('local', 'local-013-_____2026-09-08_045804.png'),
    ('local', 'local-031-ScreenShot_2026-09-29_225743_509.png'),
]

for d, f in CASES:
    img = Image.open(os.path.join(BASE, d, f)).convert('RGB')
    w, h = img.size
    left = ch.pane_left_for(img) or int(w * ch.PANE_LEFT_REL)
    x0 = max(0, left - 240)
    old_x1 = max(x0 + 40, left - 6)
    new_x1 = co._list_right_edge(img, left, w)

    print('=' * 76)
    print('%s  (%dx%d) left=%d 旧x1=%d 新x1=%d' % (f[:40], w, h, left, old_x1, new_x1))

    # 1) 同一输入重复 3 次 → 稳定性
    for tag, x1 in (('旧', old_x1), ('新', new_x1)):
        runs = [sorted(read(img, x0, x1)) for _ in range(3)]
        stable = runs[0] == runs[1] == runs[2]
        print('  %s 右沿=%d 重复3次稳定=%s  段数=%s'
              % (tag, x1, stable, [len(r) for r in runs]))
        if not stable:
            print('      run1=%s' % runs[0][:6])
            print('      run2=%s' % runs[1][:6])
    print()
