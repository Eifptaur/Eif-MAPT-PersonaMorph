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


def _row_bg_and_ink(img, y, xs):
    """该行的 (背景色众数, 墨点数)。背景＝众数（文字只占少数像素）。"""
    px = img.load()
    c = Counter()
    for x in xs:
        r, g, b = px[x, y][:3]
        c[(r // 16 * 16, g // 16 * 16, b // 16 * 16)] += 1
    bg = c.most_common(1)[0][0]
    ink = 0
    for x in xs:
        r, g, b = px[x, y][:3]
        if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > 90:
            ink += 1
    return bg, ink


def detect_title_band_y(img, pl, x_lo_off=5, x_hi_frac=0.22,
                        ink_min_pts=2, pad=6, h_frac=0.075):
    """**当帧实测**标题带的 y 范围 ⇒ `(y0, y1)`；定不出给 None。

    ⛔ 为什么不靠"绝对墨量"（2026-09-30 两次实测教训）：
      深色帧的标题可能是**单字母 `E`** ⇒ 全行只有 2~5 个墨点。
      "墨占比 ≥0.02"、"连续 4 行 ≥3 点"这类**绝对阈值判据必然失败**
      （实测都抓到了 y≈128~219 的**消息区第一条消息**）。
      ⇒ 改用「**先限定范围，再在范围内找第一个有墨的行**」。

    范围怎么定（**色差**，用户提示的方向）：
      · 逐行算聊天区左半段的**背景色众数**，找从上往下**第一个明显变化**的 y = `y_edge`；
      · `y_edge` **上方很暗**（亮度 < 80）⇒ 深色主题：上方就是标题带 ⇒ 范围 = `[0, y_edge]`；
      · `y_edge` **上方是中等灰**（80~210）⇒ 浅色主题：上方是**窗口控制区** ⇒
        范围 = `[y_edge, y_edge + h_frac*h]`（控制区下面是标题带；下界只能估，故给得宽松）。
    """
    w, h = img.size
    x0 = max(0, min(w - 1, pl + x_lo_off))
    x1 = min(w, pl + int(w * x_hi_frac))
    if x1 - x0 < 30:
        return None
    # ⛔ 扫描区**必须收窄**（2026-09-30 实测）：取到 0.42w 时会把**聊天区右上角的图标**
    #   （x≈890，y=16~32）圈进来 ⇒ "第一个墨行"命中的是图标而不是标题（标题在 x≈470、y=76~88）。
    #   标题的**左端**紧贴面板左沿（实测 pl+27）⇒ 只需扫 `[pl+5, pl+0.22w]`。
    step = max(1, (x1 - x0) // 110)          # 密采样：单字母标题只有十几个像素宽
    xs = list(range(x0, x1, step))
    y_scan = min(h, int(h * 0.30))
    seq = []
    for y in range(0, y_scan):
        bg, ink = _row_bg_and_ink(img, y, xs)
        seq.append((y, bg, ink))
    if len(seq) < 20:
        return None

    # ① 找第一个"背景色明显变化"的 y（用亮度差，避免逐通道比较被噪声带偏）
    def _lum(c):
        return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    # ⛔ 阈值必须降到 **12**：实测**深色主题的标题带与消息区只差 16 个亮度单位**
    #   （背景 (32,32,32) → (48,48,48)），原来写 18 ⇒ **深色帧全部判不出**（返回 None）；
    #   而浅色差 112（(112,112,120) → (248,248,248)）⇒ 降到 12 后两边都能检出。
    y_edge, bg_above = None, None
    for i in range(3, len(seq) - 3):
        a = _lum(seq[i - 3][1])
        b = _lum(seq[i + 3][1])
        if abs(b - a) > 12:
            y_edge, bg_above = seq[i][0], seq[i - 3][1]
            break

    # ② 定范围
    if y_edge is None:
        y_lo, y_hi = 0, int(h * 0.12)        # 找不到边 ⇒ 保守取顶部一小段
    elif _lum(bg_above) < 80:
        y_lo, y_hi = 0, y_edge               # 深色：上方＝标题带
    else:
        y_lo, y_hi = y_edge, min(h, y_edge + int(h * h_frac))  # 浅色：上方＝控制区

    # ③ 范围内从上往下找**第一个有墨的行** ⇒ 标题
    for y, bg, ink in seq:
        if y < y_lo or y > y_hi:
            continue
        if ink >= ink_min_pts:
            return (max(y_lo, y - pad), min(h, y + pad + max(8, int(h * 0.018))))
    return None


def main():
    """**量化验证**：新方法（色差定范围＋范围内首个墨行）vs 旧常量 `BAND_PX` 的
    标题 OCR 非空率 —— 命中标题的判据就是"裁出来的带能 OCR 出东西"（与目标直接相关）。"""
    from PIL import Image
    from agent import chat_header as CH
    from agent import chat_ocr as CO
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    _base = os.path.join(_ROOT, "报告", "ocr瓶颈-20260930", "多尺寸样本")
    _dirs = ([os.path.join(_base, _args[0])] if _args
             else sorted(d for d in glob.glob(os.path.join(_base, "sizes-*"))
                         if os.path.isdir(d)))
    _all = {"new_ok": 0, "old_ok": 0, "n": 0}
    for _d in _dirs:
        rows = [json.loads(l) for l in open(os.path.join(_d, "samples.jsonl"), encoding="utf-8")]
        _new = _old = _tot = 0
        print("=== %s" % os.path.basename(_d))
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
            # 新：色差定范围 + 范围内首个墨行
            ty = detect_title_band_y(img, pl)
            _tn = ""
            if ty:
                _c = img.crop((max(0, pl + 10), ty[0], min(w, pl + int(w * 0.40)), ty[1]))
                _c = _c.resize((_c.width * 2, _c.height * 2))
                _tn = "".join(str(x[0]) for x in CO.recognize_dual(_c)).strip()
            # 旧：BAND_PX（dx=10, y=38, w=340, h=56）
            _c2 = img.crop((max(0, pl + 10), 38, min(w, pl + 10 + 340), 94))
            _c2 = _c2.resize((_c2.width * 2, _c2.height * 2))
            _to = "".join(str(x[0]) for x in CO.recognize_dual(_c2)).strip()
            _tot += 1
            _new += 1 if _tn else 0
            _old += 1 if _to else 0
            if _tot <= 12:
                print("  %-11s pl=%-5d 新y=%-13s 新OCR=%-16r 旧OCR=%r"
                      % ("%dx%d" % (w, h), pl, str(ty), _tn[:16], _to[:16]))
        print("  ⇒ 本目录 %d 帧：新方法标题OCR非空 %d (%.0f%%) · 旧常量 %d (%.0f%%)"
              % (_tot, _new, 100.0 * _new / max(1, _tot), _old, 100.0 * _old / max(1, _tot)))
        _all["new_ok"] += _new
        _all["old_ok"] += _old
        _all["n"] += _tot
    print("=" * 74)
    print("⇒ 合计 %d 帧：新 **%d (%.0f%%)** · 旧 %d (%.0f%%)"
          % (_all["n"], _all["new_ok"], 100.0 * _all["new_ok"] / max(1, _all["n"]),
             _all["old_ok"], 100.0 * _all["old_ok"] / max(1, _all["n"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
