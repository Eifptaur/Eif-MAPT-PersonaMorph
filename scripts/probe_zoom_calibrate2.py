# -*- coding: utf-8 -*-
"""标定 v2：用**列块法的文字块高**当真值，找"字高 → 最优 zoom"的关系。

列块法（`_band_col_blocks`）能按形状把头像（高大块 53/47）与文字（小块 9~19）分开
⇒ 文字块高才是真字高。这是稳健口径（不依赖行间空隙是否够大）。
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402
import logging

log = logging.getLogger("persona-morph")

TRUTH = {
    ('local', 'local-021-ScreenShot_2026-09-29_225525_975.png'):
        ['课堂', '全量审计', '当地通报'],
    ('local', 'local-022-ScreenShot_2026-09-29_225605_285.png'):
        ['课堂', '全量审计', '当地通报'],
    ('local', 'local-030-ScreenShot_2026-09-29_225733_355.png'):
        ['海绵', '真鼠标', '腾讯新闻', '微信团队'],
    ('local', 'local-031-ScreenShot_2026-09-29_225743_509.png'):
        ['海绵宝', '腾讯新闻', '微信', 'aaa', '服务', '文件传'],
    ('local', 'local-033-ScreenShot_2026-09-29_225759_044.png'):
        ['海绵', '腾讯新闻', '文件传'],
    ('local', 'local-034-ScreenShot_2026-09-29_225805_323.png'):
        ['海绵', '腾讯新闻'],
    ('wechat', '0af83564-b60d-4812-aa4f-3c330c6399c0.png'):
        ['aaa', '服务号', '文件传输'],
    ('wechat', '6c29e9f4-2c81-4b4d-9a71-a826c521f403.png'):
        ['微信团队', 'aaa', '服务号'],
    ('wechat', '7bcf1579-61e9-4c80-a54a-47eb45341b53.png'):
        ['服务号', '文件传输', '微信团队'],
    ('wechat', '聊天窗-1884x1439.png'):
        ['海绵', '流离', '真鼠标'],
}


def norm(s):
    return ''.join(c for c in str(s) if c.strip())


def text_height_from_blocks(img, x0, left):
    """列表区文字块高的**中位数**（滤掉头像那种高大块）。量不出返回 0。"""
    hs = []
    try:
        for b in co.green_bands(img, min_ratio=0.30, min_h=24):
            gx0, gx1 = co._green_span(img, b['y0'], b['y1'])
            if gx1 <= gx0:
                continue
            bg = co._band_bg(img, b['y0'], b['y1'], max(0, int(gx0)), max(1, int(gx1)))
            for bx0, bx1, byt, byb in co._band_col_blocks(img, b['y0'], b['y1'], left, bg):
                bh = int(byb) - int(byt)
                if 6 <= bh <= 40:          # 文字块
                    hs.append(bh)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass
    if not hs:
        return 0
    hs.sort()
    return hs[len(hs) // 2]


rows = []
print('%-38s %-9s %-9s %s' % ('图', '字高', '最优z', '各档召回'))
print('-' * 84)
for (d, f), musts in TRUTH.items():
    p = os.path.join(BASE, d, f)
    if not os.path.exists(p):
        continue
    img = Image.open(p).convert('RGB')
    w, h = img.size
    try:
        left = ch.pane_left_for(img) or int(w * ch.PANE_LEFT_REL)
    except Exception:
        left = int(w * ch.PANE_LEFT_REL)
    x0 = max(0, left - 240)
    x1 = max(x0 + 40, left - 6)

    th = text_height_from_blocks(img, x0, left)

    scores = {}
    for z in (1, 2, 3, 4, 5, 6):
        try:
            crop = img.crop((x0, 30, x1, h))
            if z > 1:
                crop = crop.resize((crop.width * z, crop.height * z))
            blob = '|'.join(norm(str(t)) for t, *_ in co.recognize(crop))
        except Exception:
            blob = ''
        scores[z] = sum(1 for m in musts if norm(m) in blob)
    best = max(scores, key=lambda z: scores[z])
    rows.append((th, best))
    print('%-38s %-9s %-9s %s' % (f[:36], th, best,
                                  ' '.join('z%d=%d' % (z, s) for z, s in scores.items())))

print()
print('【字高 → 最优zoom】')
for th, bz in sorted(rows):
    print('   字高 %-4s → 最优 z=%d  (放大后字高 ≈ %d)' % (th, bz, th * bz))
