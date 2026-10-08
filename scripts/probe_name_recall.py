# -*- coding: utf-8 -*-
"""终局判据：**双人独立读同一张图，比"会话名"这件事**。

为什么换判据：串级 diff 分不清"残字被更完整结果取代"（属改善）与"真丢字"。
⇒ 改成**可独立复核**的口径：对每张**真会话列表**图，人工列出应当读出的真实会话名，
   再比对改动前后的召回。判据不依赖"新旧结果互相比较"，因此不会自欺。

真值来源：从图里肉眼可读的会话名（本次逐张看过 local-019..035 / wechat 全部）。
只统计"这些名字在读出结果里出现与否"，**不比较其它噪声文本**（噪声多少不影响召回）。
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'%USERPROFILE%\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402

# 真值：{(目录, 文件名): [必须读出的会话名（允许 OCR 错字，用宽松子串匹配）]}
# ⚠️ 只写"**屏幕上确实完整可见**"的名字 —— 列表窄时微信本身会把名字截断成 `海绵宝...`，
#    那种图上"读全名"物理上不可能，不该写进真值（写了就是拿不可能当判据）。
# 已逐张肉眼核对：真值核对-1065宽.png / 真值核对-2294宽.png
TRUTH = {
    ('local', 'local-020-ScreenShot_2026-09-29_225511_619.png'):
        ['海绵宝', '腾讯新闻', '微信团队'],
    ('local', 'local-021-ScreenShot_2026-09-29_225525_975.png'):
        ['课堂', '全量审计', '当地通报'],
    ('local', 'local-022-ScreenShot_2026-09-29_225605_285.png'):
        ['课堂', '全量审计', '当地通报'],
    ('local', 'local-023-ScreenShot_2026-09-29_225613_033.png'):
        ['课堂', '全量审计', '当地通报'],
    ('local', 'local-024-ScreenShot_2026-09-29_225621_538.png'):
        ['课堂', '全量审计', '当地通报'],
    ('local', 'local-025-ScreenShot_2026-09-29_225630_669.png'):
        ['海绵', '流离', '真鼠标', '腾讯新闻', '微信团队'],
    ('local', 'local-030-ScreenShot_2026-09-29_225733_355.png'):
        ['海绵', '真鼠标', '腾讯新闻', '微信团队'],
    ('local', 'local-031-ScreenShot_2026-09-29_225743_509.png'):
        ['海绵宝', '腾讯新闻', '微信', 'aaa', '服务', '文件传'],
    ('local', 'local-033-ScreenShot_2026-09-29_225759_044.png'):
        ['海绵', '腾讯新闻', '文件传'],
    ('local', 'local-035-ScreenShot_2026-09-29_225814_787.png'):
        ['课堂', '全量审计', '当地'],
    ('wechat', '0af83564-b60d-4812-aa4f-3c330c6399c0.png'):
        ['aaa', '服务号', '文件传输'],
    ('wechat', '6c29e9f4-2c81-4b4d-9a71-a826c521f403.png'):
        ['微信团队', 'aaa', '服务号'],
    ('wechat', '7bcf1579-61e9-4c80-a54a-47eb45341b53.png'):
        ['服务号', '文件传输', '微信团队'],
    ('wechat', '聊天窗-1884x1439.png'):
        ['海绵', '流离', '真鼠标'],
    ('wechat', '聊天窗-气泡最宽-2294x1439.png'):
        ['海绵', '流离', '真鼠标', '腾讯'],
}


def norm(s):
    return ''.join(c for c in str(s) if c.strip())


def evaluate(rows, musts):
    blob = '|'.join(norm(r.get('name', '')) + '|' + norm(r.get('text', ''))
                    for r in rows)
    hit = [m for m in musts if norm(m) in blob]
    return hit, [m for m in musts if norm(m) not in blob]


def read_fixed_edge(img, x0, x1, h, zoom=2):
    crop = img.crop((x0, 30, int(x1), h))
    if zoom > 1:
        crop = crop.resize((crop.width * zoom, crop.height * zoom))
    rows = []
    for text, x, y, ww, hh in co.recognize(crop):
        yy = y / float(zoom)
        hit = None
        for r in rows:
            if abs(r['y'] - yy) <= max(8, hh / float(zoom)):
                hit = r
                break
        if hit is None:
            rows.append({'name': str(text), 'text': str(text), 'y': yy})
        else:
            hit['text'] += str(text)
    return rows


tot_old = tot_new = tot_must = 0
print('%-42s %-8s %-8s' % ('图', '老口径', '改动后'))
print('-' * 66)
for (d, f), musts in TRUTH.items():
    p = os.path.join(BASE, d, f)
    if not os.path.exists(p):
        print('缺：%s' % f)
        continue
    img = Image.open(p).convert('RGB')
    w, h = img.size
    try:
        left = ch.pane_left_for(img) or int(w * ch.PANE_LEFT_REL)
    except Exception:
        left = int(w * ch.PANE_LEFT_REL)
    x0 = max(0, left - 240)
    narrow = max(x0 + 40, left - 6)

    old_rows = read_fixed_edge(img, x0, narrow, h)
    new_rows = co.session_rows(img)

    ho, mo = evaluate(old_rows, musts)
    hn, mn = evaluate(new_rows, musts)
    tot_old += len(ho); tot_new += len(hn); tot_must += len(musts)
    flag = ' ★+' if len(hn) > len(ho) else (' ⚠-' if len(hn) < len(ho) else '')
    print('%-42s %-8s %-8s%s' % (f[:40], '%d/%d' % (len(ho), len(musts)),
                                 '%d/%d' % (len(hn), len(musts)), flag))
    if mn:
        print('      未召回: %s' % mn)

print('-' * 66)
print('总召回：老口径 %d/%d  →  改动后 %d/%d'
      % (tot_old, tot_must, tot_new, tot_must))
