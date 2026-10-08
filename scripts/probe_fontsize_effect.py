# -*- coding: utf-8 -*-
"""★验证用户的想法：字号变大 ⇒ OCR 变准？

做法：取"现在读不出名字"的外部真机图，把它放大成"等价于字号调大"的样子，
     看名字能不能读出来、在什么倍率下稳定读出。
注意：放大 ≠ 真调字号（真调字号是重新排版、笔画更清晰），但可用于判断"字高"这条线索是否成立。
"""
import os
import sys
import logging

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
logging.disable(logging.CRITICAL)
from PIL import Image
from agent import chat_ocr as co

S = os.path.join(os.path.dirname(ROOT), 'dev-workspace', 'persona-morph', 'ocr-samples', 'wechat')

CASES = [
    ('external-全屏原图-1920x1080.png', '测试', (75, 30, 420, 1080)),
    ('external-反馈来源-1151x1185.png', '测试', (61, 30, 1151, 1185)),
    ('0af83564-b60d-4812-aa4f-3c330c6399c0.png', '微信团队', (123, 30, 363, 1281)),
    ('7bcf1579-61e9-4c80-a54a-47eb45341b53.png', '某会话', (158, 30, 363, 1281)),
]

for fn, key, box in CASES:
    im = Image.open(os.path.join(S, fn)).convert('RGB')
    print('=== %s  找 %r ===' % (fn[:42], key))
    for z in (1, 2, 3, 4, 6):
        crop = im.crop(box)
        if z > 1:
            crop = crop.resize((crop.width * z, crop.height * z))
        ts = [str(t) for t, *_ in co.recognize(crop)]
        hit = any(key in t for t in ts)
        if hit:
            note = '  '.join(ts)[:60]
        else:
            near = [t for t in ts if key[:1] in t]
            note = ('近似: ' + ' / '.join(near[:2])) if near else ' '.join(ts)[:60]
        print('   zoom=%d  %s   %s' % (z, '✅' if hit else '❌', note))
    print()
