# -*- coding: utf-8 -*-
"""标定：会话列表名字的实际字高，与"放大到多少最优"的关系。

目的：`auto_zoom` 的目标区间（45~90px）是为**会话头**调的。
列表名字的字高与之不同 ⇒ 要重新标定，而不是直接沿用。

做法：
  1. 量每张图列表区里**名字文字的实际像素高**（用墨迹行高，避开头像）；
  2. 对每个 zoom，量真值召回；
  3. 找出"放大后字高"与"召回最好"的关系。
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402

TRUTH = {
    ('local', 'local-031-ScreenShot_2026-09-29_225743_509.png'):
        ['海绵宝', '腾讯新闻', '微信', 'aaa', '服务', '文件传'],
    ('local', 'local-030-ScreenShot_2026-09-29_225733_355.png'):
        ['海绵', '真鼠标', '腾讯新闻', '微信团队'],
    ('local', 'local-021-ScreenShot_2026-09-29_225525_975.png'):
        ['课堂', '全量审计', '当地通报'],
    ('local', 'local-022-ScreenShot_2026-09-29_225605_285.png'):
        ['课堂', '全量审计', '当地通报'],
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


def read(img, x0, x1, h, z):
    crop = img.crop((x0, 30, x1, h))
    if z > 1:
        crop = crop.resize((crop.width * z, crop.height * z))
    return '|'.join(norm(str(t)) for t, *_ in co.recognize(crop))


print('%-38s %-8s %-8s' % ('图', '字高(px)', '最优zoom'))
print('-' * 60)
rows = []
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

    # ① 量名字文字的实际像素高（在列表区取样，用 text_height_hint）
    try:
        th = co.text_height_hint(img.crop((x0, 30, x1, min(h, 900))))
    except Exception:
        th = 0

    # ② 各档召回
    scores = {}
    for z in (2, 3, 4, 5, 6):
        try:
            blob = read(img, x0, x1, h, z)
        except Exception:
            blob = ''
        scores[z] = sum(1 for m in musts if norm(m) in blob)
    best = max(scores, key=lambda z: scores[z])
    rows.append((th, best, scores, f[:36]))
    print('%-38s %-8s %-8s  %s' % (f[:36], th, best,
                                   ' '.join('z%d=%d' % (z, s) for z, s in scores.items())))

print()
print('字高分布：%s' % sorted(set(r[0] for r in rows)))
print('最优 zoom 分布：%s' % {z: sum(1 for r in rows if r[1] == z) for z in (2, 3, 4, 5, 6)})
