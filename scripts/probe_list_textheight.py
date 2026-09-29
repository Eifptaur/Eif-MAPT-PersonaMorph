# -*- coding: utf-8 -*-
"""用"绿底带内的文字块高"量列表名字的真实字高（避开 text_height_hint 的多行粘连问题）。

为什么另开：`text_height_hint` 量的是"整块带子里连续墨迹行的高"，适合**单行标题带**；
列表区是几十行堆叠 ⇒ 会粘连成整区高度（实测量出 848/870，明显失效）。

本探针改用 `_band_col_blocks()` —— 它本来就是"按块高区分头像与文字"，天然给出**文字块的高度**。
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

BASE = r'C:\Users\ptmou\Desktop\WX-chatbot\dev-workspace\persona-morph\ocr-samples'

from agent import chat_ocr as co  # noqa: E402
from agent import chat_header as ch  # noqa: E402

CASES = [
    ('local', 'local-021-ScreenShot_2026-09-29_225525_975.png'),
    ('local', 'local-022-ScreenShot_2026-09-29_225605_285.png'),
    ('local', 'local-030-ScreenShot_2026-09-29_225733_355.png'),
    ('local', 'local-031-ScreenShot_2026-09-29_225743_509.png'),
    ('local', 'local-033-ScreenShot_2026-09-29_225759_044.png'),
    ('wechat', '0af83564-b60d-4812-aa4f-3c330c6399c0.png'),
    ('wechat', '7bcf1579-61e9-4c80-a54a-47eb45341b53.png'),
    ('wechat', '聊天窗-1884x1439.png'),
]

print('%-40s %-10s %-12s %-10s' % ('图', 'text_height', '块高(文字)', '最优zoom'))
print('-' * 78)
for d, f in CASES:
    p = os.path.join(BASE, d, f)
    if not os.path.exists(p):
        print('缺 %s' % f)
        continue
    img = Image.open(p).convert('RGB')
    w, h = img.size
    try:
        left = ch.pane_left_for(img) or int(w * ch.PANE_LEFT_REL)
    except Exception:
        left = int(w * ch.PANE_LEFT_REL)
    x0 = max(0, left - 240)
    x1 = max(x0 + 40, left - 6)

    th = co.text_height_hint(img.crop((x0, 30, x1, h)))

    # 用绿底带的列块量文字高
    blk = []
    try:
        bands = co.green_bands(img, min_ratio=0.30, min_h=24)
        if bands:
            b = bands[0]
            gx0, gx1 = co._green_span(img, b['y0'], b['y1'])
            bg = co._band_bg(img, b['y0'], b['y1'], max(0, int(gx0)), max(1, int(gx1)))
            blocks = co._band_col_blocks(img, b['y0'], b['y1'], left, bg)
            for bx0, bx1, byt, byb in blocks:
                bh = int(byb) - int(byt)
                if 6 <= bh <= 80:      # 合理文字高范围
                    blk.append(bh)
    except Exception as e:
        print('  块量失败 %r' % (e,))

    print('%-40s %-10s %-12s' % (f[:38], th, blk))
