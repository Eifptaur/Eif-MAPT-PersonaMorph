# -*- coding: utf-8 -*-
"""检测语料库里有没有深色模式样本。"""
import os

from PIL import Image

BASE = r'C:\Users\ptmou\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'
for d in ('local', 'wechat'):
    S = os.path.join(BASE, d)
    print('=== %s ===' % d)
    n_dark = 0
    for f in sorted(os.listdir(S)):
        if not f.lower().endswith(('.png', '.jpg', '.jpeg')):
            continue
        im = Image.open(os.path.join(S, f)).convert('L')
        im.thumbnail((300, 300))
        px = list(im.getdata())
        avg = sum(px) / len(px)
        dark = sum(1 for p in px if p < 90) / len(px)
        tag = ''
        if avg < 120 or dark > 0.5:
            tag = '  ★★深色?'
            n_dark += 1
        print('  %-44s 平均亮度=%5.1f  暗像素占比=%.2f%s' % (f[:42], avg, dark, tag))
    print('  → 深色样本数：%d' % n_dark)
    print()
