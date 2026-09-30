# -*- coding: utf-8 -*-
"""probe_title_y.py —— 标题带的 y，能不能**直接从当帧量出来**（不用绝对常量）

⛔ 背景：`BAND_PX = (10, 38, 340, 56)` 的 `y0=38` 是**绝对像素常量**，实测已过期
  （深色帧里标题上沿在 y≈58，浅色在 y≈79；而框给 38 ⇒ 压在字的上边缘 ⇒ 字被切掉 ⇒ 读不出）。
  用户提示"绿条不能直接推搜索/标题的下沿" ⇒ 也对（绿底行 y=120~217，比标题低 60px）。

本探针试一条**不依赖任何常量**的路：
  **"标题是聊天区左半段里，从上往下第一段有'墨'的行"** ——
  窗口控制按钮（🔔 — □ ✕）在**右侧边缘**（x > 0.8w）⇒ 扫左半段扫不到；
  消息在标题**下方** ⇒ 取"第一段"即可。
  背景色按**每行的众数**算（文字只占少数像素 ⇒ 众数＝该行背景），墨 = 与背景差异明显的像素。

用法：./runtime/python/python.exe -u scripts/probe_title_y.py [采集目录名]
"""
import glob
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def title_band_y(img, pl, x_lo_off=15, x_hi_frac=0.42,
                 ink_thr=90, ink_min_pts=3, run_min=4, y_max_frac=0.30):
    """聊天区左半段里"第一段有墨的行" ⇒ `(y0, y1)`；找不到给 None。

    ⛔ 判据用**墨点绝对数**而不是"墨占比"：
      实测踩过 —— 深色帧的标题就是**单字母 `E`**，在 0.42w 宽的扫描段里
      "墨占比"只有千分之几，被"占比 ≥0.02"直接滤掉 ⇒ 结果抓到的是**消息区第一条消息**
      （y≈128~219），而标题在 y≈58~79。
    """
    w, h = img.size
    px = img.load()
    x0 = max(0, min(w - 1, pl + x_lo_off))
    x1 = min(w, pl + int(w * x_hi_frac))
    if x1 - x0 < 30:
        return None
    step = max(1, (x1 - x0) // 50)
    xs = list(range(x0, x1, step))
    run, start = 0, None
    for y in range(0, min(h, int(h * y_max_frac))):
        c = Counter()
        for x in xs:
            r, g, b = px[x, y][:3]
            c[(r // 16 * 16, g // 16 * 16, b // 16 * 16)] += 1
        bg = c.most_common(1)[0][0]
        ink = 0
        for x in xs:
            r, g, b = px[x, y][:3]
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > ink_thr:
                ink += 1
        if ink >= ink_min_pts:
            if run == 0:
                start = y
            run += 1
            if run >= run_min:
                return (start, y)
        else:
            run, start = 0, None
    return None


def main():
    from PIL import Image
    from agent import chat_header as CH
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    _base = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")
    _dirs = ([os.path.join(_base, _args[0])] if _args
             else sorted(glob.glob(os.path.join(_base, "sizes-*")))[-2:])
    for _d in _dirs:
        if not os.path.isdir(_d):
            print("⛔ 目录不存在：%s" % _d)
            continue
        rows = [json.loads(l) for l in open(os.path.join(_d, "samples.jsonl"), encoding="utf-8")]
        print("=== %s" % os.path.basename(_d))
        n = 0
        for r in rows:
            if not r.get("png"):
                continue
            p = os.path.join(_d, r["png"])
            if not os.path.isfile(p):
                continue
            try:
                img = Image.open(p).convert("RGB")
            except Exception:
                continue
            w, h = img.size
            if w < 500:
                continue
            pl = CH.pane_left_for(img)
            ty = title_band_y(img, pl)
            tb = r.get("title_box") or [None, None, None, None]
            print("  %-11s pl=%-5d 墨段y=%-13s 采集器框y=%s~%s 标题OCR=%r"
                  % ("%dx%d" % (w, h), pl, str(ty), tb[1], tb[3], (r.get("title") or "")[:14]))
            n += 1
            if n >= 10:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
