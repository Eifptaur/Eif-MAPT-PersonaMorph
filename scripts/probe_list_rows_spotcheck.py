# -*- coding: utf-8 -*-
"""抽查：把某张图"裁窄(旧)"和"探测驱动(新)"两种读法并排打出来，人工判定是否真丢。"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'C:\Users\ptmou\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402

CASES = [
    ('local', 'local-013-_____2026-09-08_045804.png'),
    ('local', 'local-021-ScreenShot_2026-09-29_225525_975.png'),
    ('local', 'local-031-ScreenShot_2026-09-29_225743_509.png'),
    ('local', 'local-022-ScreenShot_2026-09-29_225605_285.png'),
    ('wechat', '0af83564-b60d-4812-aa4f-3c330c6399b6.png'),
]

for d, f in CASES:
    p = os.path.join(BASE, d, f)
    if not os.path.exists(p):
        p2 = os.path.join(BASE, d, f.replace('6399b6', '6399c0'))
        if os.path.exists(p2):
            p = p2
        else:
            print('缺：%s' % f)
            continue
    img = Image.open(p).convert('RGB')
    w, h = img.size
    left = ch.pane_left_for(img) or int(w * ch.PANE_LEFT_REL)
    x0 = max(0, left - 240)
    old_x1 = max(x0 + 40, left - 6)
    new_x1 = co._list_right_edge(img, left, w)

    print('=' * 76)
    print('%s  (%dx%d)  left=%d' % (f[:44], w, h, left))
    print('  旧右沿=%d   新右沿=%d' % (old_x1, new_x1))

    def read(x1):
        crop = img.crop((x0, 30, x1, h))
        c2 = crop.resize((crop.width * 2, crop.height * 2))
        out = []
        for text, x, y, ww, hh in co.recognize(c2):
            t = str(text).strip()
            if t:
                out.append(t)
        return out

    o = read(old_x1)
    n = read(new_x1)
    print('  旧 %d 段: %s' % (len(o), o[:8]))
    print('  新 %d 段: %s' % (len(n), n[:8]))
    print()
