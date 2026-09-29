# -*- coding: utf-8 -*-
"""探针：`_green_span` 横向越界（圈进聊天区）的覆盖面 + 修复效果。

## 发现的缺陷
`_green_span` 取"y0~y1 里**最长连续绿段**"，**全图扫描、不限列表区**。
当右侧聊天区有一块**更长的绿色区域**时（实测 `6c29e9f4` 的 `article...` 浅绿底，宽 963px），
它就把聊天区那段当绿底行的横向边界 ⇒ `_band_name` 裁出聊天区 ⇒ 读出英文垃圾
`article/intertextuality-and-parallelomania`，而真值会话名是 **`E`**。

## 量什么
① 越界覆盖面：多少绿带的 span 右沿 > pane_left；
② 修复效果：把扫描限制在 `[0, pane_left]` 后，绿底行的名字能否读对。
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'C:\Users\ptmou\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402
import glob  # noqa: E402


def norm(s):
    return ''.join(c for c in str(s) if c.strip())


def span_bounded(img, y0, y1, left, ratio=0.5):
    """`_green_span` 的**限域版**：只在 [0, left] 内找最长连续绿段。"""
    try:
        import numpy as np
        rgb = np.asarray(img.convert("RGB")).astype(int)
        _y0, _y1 = max(0, int(y0)), min(img.size[1] - 1, int(y1))
        if _y1 <= _y0:
            return 0, 0
        sub = rgb[_y0:_y1 + 1, :max(1, int(left))]
        r, g, b = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
        gm = (((np.abs(r - co.GREEN[0]) <= co.GREEN_TOL) & (np.abs(g - co.GREEN[1]) <= co.GREEN_TOL)
               & (np.abs(b - co.GREEN[2]) <= co.GREEN_TOL))
              | ((g - r >= 12) & (g - b >= 10) & (g >= 90) & (g >= np.maximum(r, b) + 8)))
        col = gm.mean(axis=0)
        idx = [i for i, v in enumerate(col) if v > float(ratio)]
        if not idx:
            return 0, 0
        segs, s, e = [], idx[0], idx[0]
        for i in idx[1:]:
            if (i - e) > 3:
                segs.append((s, e))
                s = i
            e = i
        segs.append((s, e))
        x0, x1 = max(segs, key=lambda p: p[1] - p[0])
        return int(x0), int(x1)
    except Exception:
        return 0, 0


print('=== ① 越界覆盖面 ===')
tot = bad = 0
worst = []
for d in ('wechat', 'local'):
    for p in sorted(glob.glob(os.path.join(BASE, d, '*.png'))):
        try:
            img = Image.open(p).convert('RGB')
            left = ch.pane_left_for(img) or int(img.size[0] * ch.PANE_LEFT_REL)
            for b in co.green_bands(img):
                tot += 1
                gx0, gx1 = co._green_span(img, b['y0'], b['y1'])
                if gx1 > left + 5:
                    bad += 1
                    worst.append((gx1 - left, os.path.basename(p)[:34], left, b, gx0, gx1))
        except Exception:
            pass
worst.sort(reverse=True)
for over, nm, left, b, gx0, gx1 in worst[:6]:
    print('   越界 %-5d %-34s 左沿=%-5d span=%d~%d' % (over, nm, left, gx0, gx1))
print('   合计：绿带 %d 条，越界 %d 条（%.0f%%）' % (tot, bad, 100.0 * bad / max(1, tot)))

print()
print('=== ② 修复效果（限域到 [0, pane_left]）===')
f = '6c29e9f4-2c81-4b4d-9a71-a826c521f403.png'
img = Image.open(os.path.join(BASE, 'wechat', f)).convert('RGB')
left = ch.pane_left_for(img)
b = co.green_bands(img)[0]
y0, y1 = b['y0'], b['y1']
ox0, ox1 = co._green_span(img, y0, y1)
nx0, nx1 = span_bounded(img, y0, y1, left)
print('   图宽=%d pane_left=%d 绿带 y=%d~%d（真值会话名=E，y≈343）' % (img.size[0], left, y0, y1))
print('   老 span = %d~%d（宽 %d）← 全在聊天区' % (ox0, ox1, ox1 - ox0))
print('   新 span = %d~%d（宽 %d）← 限在列表区' % (nx0, nx1, nx1 - nx0))

# 按新 span 裁名字行，看读出什么
_hy0 = int(y0)
_hy1 = int(y0) + max(8, (int(y1) - int(y0)) // 2)
for tag, (cx0, cx1) in (('老（全图 span）', (ox0, ox1)), ('新（限域 span）', (nx0, nx1))):
    if cx1 - cx0 < 24:
        print('   %-16s：span 太窄，跳过' % tag)
        continue
    c = img.crop((cx0, _hy0, cx1, _hy1))
    c = c.resize((c.width * 3, c.height * 3))
    reads = [str(t) for t, *_ in co.recognize(c)]
    print('   %-16s：%r' % (tag, reads))
