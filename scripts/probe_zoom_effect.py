# -*- coding: utf-8 -*-
"""调研：会话列表读取 vs 会话头读取 —— 两条路的能力差在哪。

背景：`_read_band_crop`（会话头）走 auto_zoom 多档 + 候选集择优；
      `list_rows`（会话列表）走固定 zoom=2、单档。
本探针量：如果让会话列表也走"多档"，能提升多少（用真值召回口径，前后可比）。
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402

# 真值（同 probe_name_recall.py，只写屏幕上确实完整可见的）
TRUTH = {
    ('local', 'local-031-ScreenShot_2026-09-29_225743_509.png'):
        ['海绵宝', '腾讯新闻', '微信', 'aaa', '服务', '文件传'],
    ('local', 'local-021-ScreenShot_2026-09-29_225525_975.png'):
        ['课堂', '全量审计', '当地通报'],
    ('local', 'local-022-ScreenShot_2026-09-29_225605_285.png'):
        ['课堂', '全量审计', '当地通报'],
    ('local', 'local-030-ScreenShot_2026-09-29_225733_355.png'):
        ['海绵', '真鼠标', '腾讯新闻', '微信团队'],
    ('wechat', '0af83564-b60d-4812-aa4f-3c330c6399c0.png'):
        ['aaa', '服务号', '文件传输'],
    ('wechat', '6c29e9f4-2c81-4b4d-9a71-a826c521f403.png'):
        ['微信团队', 'aaa', '服务号'],
    ('wechat', '7bcf1579-61e9-4c80-a54a-47eb45341b53.png'):
        ['服务号', '文件传输', '微信团队'],
}


def norm(s):
    return ''.join(c for c in str(s) if c.strip())


def read_rows(img, x0, x1, h, zoom):
    crop = img.crop((x0, 30, int(x1), h))
    if zoom > 1:
        crop = crop.resize((crop.width * zoom, crop.height * zoom))
    rows = []
    for text, x, y, ww, hh in co.recognize(crop):
        rows.append(str(text))
    return '|'.join(norm(t) for t in rows)


def best_edge(img, left, w):
    """当前生产口径的右沿。"""
    x0 = max(0, left - 240)
    return x0, max(x0 + 40, left - 6)


print('%-38s %-10s %-10s %-10s %-10s' % ('图', '现有z=2', 'z=3', 'z=4', 'auto_zoom'))
print('-' * 82)
tot = {'z2': 0, 'z3': 0, 'z4': 0, 'az': 0, 'must': 0}
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
    x0, x1 = best_edge(img, left, w)

    # auto_zoom 自己算一档
    try:
        az = co.auto_zoom(img.crop((x0, 30, x1, h)))
    except Exception:
        az = 2

    res = {}
    for tag, z in (('z2', 2), ('z3', 3), ('z4', 4), ('az', az)):
        blob = read_rows(img, x0, x1, h, z)
        hit = sum(1 for m in musts if norm(m) in blob)
        res[tag] = (hit, len(musts))
        tot[tag] += hit
    tot['must'] += len(musts)
    print('%-38s %-10s %-10s %-10s %-10s' % (
        f[:36],
        '%d/%d' % res['z2'], '%d/%d' % res['z3'],
        '%d/%d' % res['z4'], '%d/%d (z=%d)' % (res['az'][0], res['az'][1], az)))

print('-' * 82)
print('总召回： z=2 %d/%d | z=3 %d/%d | z=4 %d/%d | auto_zoom %d/%d'
      % (tot['z2'], tot['must'], tot['z3'], tot['must'],
         tot['z4'], tot['must'], tot['az'], tot['must']))
