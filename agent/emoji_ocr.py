# -*- coding: utf-8 -*-
"""**纯色文字层提取** —— 把表情包里的"字"和"配图"分开，只喂字给 OCR。

## 由头，实测有效）
> 「一般字体都是**纯色**的，你把那些**纯色的字抠出来直接读**，**不要管配图**，
>   把**配图和配文分开读**。这样你就能得到准确的配文了，
>   **一起读的话，你会和背景混在一起**。」

⇒ 表情包的配文几乎总是在图上看作**一块纯色**（白字描边、或黑字、或红字），
  而背景是照片/插画（颜色杂、渐变多）。⇒ 按"颜色纯度 + 出现面积"把字色找出来，
  **只保留该颜色像素**，其余抹掉 ⇒ OCR 面对的就是"白底黑字/黑底白字"，识别率大幅上升。

## 做法
1. **量化取色**：把图缩到中等尺寸，统计颜色（按 32 级桶），找**出现面积足够大**
   且**该颜色像素在局部高度聚集**的几个候选（= 可能是字色/描边色）。
2. **逐候选生成掩膜**：该颜色的像素 ⇒ 前景；其余 ⇒ 背景。**同时给正片和反相**两版
   （因为字可能比底亮，也可能比底暗）。
3. 交给 OCR，**谁读出的字多、像句子，就用谁**。

只读：不发送、不改原图（可选写出中间图供人工核对）。
"""
from __future__ import annotations

import os
from collections import Counter


def _quantize(im, step: int = 32):
    """按 `step` 级桶统计颜色出现次数（近似取色）。"""
    small = im.convert("RGB")
    w, h = small.size
    if max(w, h) > 400:                      # 缩小加速，颜色分布不变
        k = 400.0 / max(w, h)
        small = small.resize((max(1, int(w * k)), max(1, int(h * k))))
    px = small.load()
    cnt = Counter()
    for y in range(small.size[1]):
        for x in range(small.size[0]):
            r, g, b = px[x, y]
            cnt[(r // step * step, g // step * step, b // step * step)] += 1
    return small, cnt


def candidate_colors(im, top: int = 4, min_ratio: float = 0.03):
    """给出"可能是字色"的候选 ⇒ **优先极端色（纯白/纯黑/高饱和）**，再补面积大的色。

    ⛔ 踩过的坑：只按"出现面积"取色 ⇒ 在**照片类**表情包上会失败 ——
      照片颜色极分散（最多的色常不到 1%），而**配文通常是纯白或纯黑**，
      被 32 级桶拆散后每个桶都达不到阈值 ⇒ **字色被漏掉、OCR 全空**。
    ⇒ 所以：**极端色优先**，面积只作补充。
    """
    small, cnt = _quantize(im)
    total = max(1, sum(cnt.values()))
    out = []

    def _add(c):
        if c not in out:
            out.append(c)

    # ① 极端色优先（用原图精确取色）
    px = small.load()
    w, h = small.size
    best_white, best_black, best_sat = None, None, None
    nw = nb = ns = 0
    sc = Counter()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if min(r, g, b) > 215:                    # 近白
                nw += 1
                best_white = (255, 255, 255)
            elif max(r, g, b) < 40:                   # 近黑
                nb += 1
                best_black = (0, 0, 0)
            elif max(r, g, b) - min(r, g, b) > 90:    # 高饱和
                ns += 1
                sc[(r // 32 * 32, g // 32 * 32, b // 32 * 32)] += 1
    if best_white and nw / float(total) >= 0.02:
        _add(best_white)
    if best_black and nb / float(total) >= 0.01:
        _add(best_black)
    if sc:
        best_sat = sc.most_common(1)[0][0]
        if ns / float(total) >= 0.01:
            _add(best_sat)
    # ② 再按面积补
    for c, n in cnt.most_common(40):
        if len(out) >= top:
            break
        if n / float(total) >= min_ratio:
            _add(c)
    return out[:top]


def text_layer(im, color, tol: int = 48, invert: bool = False,
               out_size=None, pad: int = 8):
    """按颜色 `color` 抠出文字层 ⇒ 高对比黑白图（可直接喂 OCR）。

    `tol` 越大，允许字色有更多渐变（描边、抗锯齿）；`invert` 给反相版。
    """
    from PIL import Image
    src = im.convert("RGB")
    w, h = src.size
    if out_size:
        src = src.resize(out_size)
        w, h = src.size
    px = src.load()
    mask = Image.new("L", (w, h), 0)
    mpx = mask.load()
    cr, cg, cb = color
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if abs(r - cr) <= tol and abs(g - cg) <= tol and abs(b - cb) <= tol:
                mpx[x, y] = 255
    # 前景=字（黑），背景=白；可反相
    bg = 0 if invert else 255
    fg = 255 - bg
    out = Image.new("L", (w, h), bg)
    opx = out.load()
    for y in range(h):
        for x in range(w):
            if mpx[x, y]:
                opx[x, y] = fg
    # 放大 2x + 留白，OCR 更稳
    out = out.resize((w * 2, h * 2))
    canvas = Image.new("L", (out.size[0] + pad * 2, out.size[1] + pad * 2), bg)
    canvas.paste(out, (pad, pad))
    return canvas


def ocr_variants(path: str, *, tol: int = 48, top: int = 4, save_dir: str = ""):
    """对一个表情图做「纯色抠字」多版本 OCR ⇒ `[{color, invert, text}]`（按字数降序）。

    `save_dir` 非空则把中间图写出来（文件名含颜色与是否反相），便于人工核对。
    """
    from PIL import Image
    from agent import chat_ocr as co
    im = Image.open(path)
    try:
        im.seek(0)
    except Exception:
        pass
    out = []
    for c in candidate_colors(im, top=top):
        for inv in (False, True):
            g = text_layer(im, c, tol=tol, invert=inv)
            if save_dir:
                os.makedirs(save_dir, exist_ok=True)
                g.save(os.path.join(save_dir, "%s_%s_%s.png"
                                    % (os.path.splitext(os.path.basename(path))[0],
                                       "%02x%02x%02x" % c, "inv" if inv else "pos")))
            try:
                items = co.recognize(g, timeout=40) or []
            except Exception:
                items = []
            txt = " ".join(str(i[0]) for i in items if i and str(i[0]).strip())
            out.append({"color": "#%02x%02x%02x" % c, "invert": inv, "text": txt})
    out.sort(key=lambda x: -len(x["text"]))
    return out
