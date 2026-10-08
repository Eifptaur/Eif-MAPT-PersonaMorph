# -*- coding: utf-8 -*-
"""热词词云（第 136 项）：把 `memory.hot_words()` 的结果**画成一张图**。

## 为什么要出图

群里问"我们最近都聊啥"时，**一串 `[('在吗', 37), ('下周', 22), …]` 没人想看**；
一张图能一眼看出话题重心。⇒ 本模块只做两件事：**排版**（`layout`）与**出图**（`render_png`）。

## 四个"不做对就等于没做"的点（判据都盯着）

1. **不许重叠**：词挤在一起还不如不出图。⇒ 螺旋外扩 + **包围盒冲突检测**（逐个两两判）。
2. **不许出血**：所有词必须**在画布内**（裁掉半个字看起来就是坏的）。
3. **字号必须跟频次走**（高频 ≥ 低频）—— 否则图是"好看但没信息"。
4. **样本为空 ⇒ 明确说"没数据"，不出空白图**（空图会让用户以为坏了）。

## 两个坑

· **字体**：中文必须显式指定字体文件（PIL 默认字体画中文＝方块）。**找不到字体就如实报错**，
  **不许"画一堆方块还说成功了"**。
· **转义**：词里可能有 `<`、`&`、emoji、甚至换行（用户真的会发）。⇒ 画之前**把不可打印字符清掉**、
  太长的词截断；PNG 没有转义问题，但 SVG/JSON 里有（本模块只出 PNG，仍做长度与空白清洗）。
"""
from __future__ import annotations

import logging
import os
import re
from collections import Counter

from .util import BoundedDict

log = logging.getLogger(__name__)

#: 画布默认尺寸（16:9，够摆 40 个词）
W, H = 900, 520
#: 最多画多少个词（再多就糊了）
MAX_WORDS = 40
#: 字号范围（最大 / 最小）
SIZE_MAX, SIZE_MIN = 60, 16
#: 词长上限（超长会横穿整张图）
WORD_MAX = 10

#: 字体对象缓存 —— **必须有上限**：键是 (字体路径, 字号)，字号由排版算出来、随图长；
#: 值是字体对象（**每个持有文件句柄**）⇒ 裸 dict 会随字号种类无界涨（PLAN 第 161 项）。
_font_cache: BoundedDict = BoundedDict(64)

#: 候选中文字体（按优先级）——**必须显式给**，PIL 默认字体画中文是方块
_FONT_CANDIDATES = ("msyhbd.ttc", "msyh.ttc", "simhei.ttf", "simsun.ttc")


def find_font() -> str:
    """找一个能画中文的字体文件；**找不到就返回空串**（调用方据此如实报错，不画方块）。"""
    win = os.environ.get("WINDIR") or "C:/Windows"
    for name in _FONT_CANDIDATES:
        p = os.path.join(win, "Fonts", name)
        if os.path.exists(p):
            return p
    return ""


def _clean(w: str) -> str:
    """清洗一个词：去掉空白/控制字符，截断超长。⛔ 不做"艺术加工"，只做去噪。"""
    s = re.sub(r"[\s\u3000\x00-\x1f]+", "", str(w or ""))
    return s[:WORD_MAX]


def layout(words, *, width: int = W, height: int = H, max_words: int = MAX_WORDS,
           scale: float = 1.0) -> list:
    """把 `[(词, 次数)]` 排成不重叠的一堆方块 ⇒ `[{word,count,size,x,y,w,h}]`。

    算法（够用就好，不引第三方）：按频次降序、**从中心往外螺旋**找第一个不与已放置方块相交的位置；
    螺旋走满也没位置 ⇒ **丢掉这个词**（宁缺勿滥，不硬塞导致重叠）。
    ⛔ 尺寸与坐标全部**就近取整**，判据里直接比整数，免得浮点误差造成"看着重叠"的假红。
    """
    from PIL import ImageFont  # 只在真的有 PIL 时才需要
    font_path = find_font()
    if not font_path:
        return []
    items = [(_clean(w), int(c)) for (w, c) in (words or []) if _clean(w)]
    items = items[:max(1, int(max_words))]
    if not items:
        return []
    _mx = max(c for _w, c in items) or 1
    _mn = min(c for _w, c in items)
    placed: list = []
    # 从中心螺旋外扩：角度步进 + 半径缓增（比"网格排"更像词云，也比"随机撒"可复现）
    import math
    cx, cy = width // 2, height // 2
    for idx, (w, c) in enumerate(items):
        ratio = 1.0 if _mx == _mn else (c - _mn) / float(_mx - _mn)
        size = max(10, int(round((SIZE_MIN + (SIZE_MAX - SIZE_MIN) * ratio) * float(scale))))
        try:
            f = _font_cache.get((font_path, size)) or ImageFont.truetype(font_path, size)
            _font_cache[(font_path, size)] = f
            box = f.getbbox(w)
        except Exception as e:  # noqa: BLE001
            log.debug("量字失败（跳过这个词）：%s", str(e)[:60])
            continue
        tw, th = max(1, box[2] - box[0]), max(1, box[3] - box[1])
        spot = None
        for step in range(0, 320):
            ang = step * 0.7 + idx * 0.31           # 每个词起点错开 ⇒ 不会全挤在一条线上
            # ⛔ 半径增长曲线决定"图有没有排开"：太慢 ⇒ 所有词挤在正中间一小团（留白一大片，
            #    看着像没排好）；太快 ⇒ 大字先占外圈、小的没地方去。0.72 次幂实测铺得最匀。
            rad = 9.0 * (step ** 0.72) * (width / 900.0)
            x = int(round(cx + rad * math.cos(ang) - tw / 2.0))
            y = int(round(cy + rad * math.sin(ang) * 0.62 - th / 2.0))
            if x < 2 or y < 2 or x + tw > width - 2 or y + th > height - 2:
                continue                             # ⛔ 出血的不要
            rect = (x - 3, y - 3, x + tw + 3, y + th + 3)
            if any(_hit(rect, (p["x"] - 3, p["y"] - 3, p["x"] + p["w"] + 3, p["y"] + p["h"] + 3))
                   for p in placed):
                continue                             # ⛔ 重叠的不要
            spot = (x, y, tw, th)
            break
        if spot:
            placed.append({"word": w, "count": c, "size": size,
                           "x": spot[0], "y": spot[1], "w": spot[2], "h": spot[3]})
    return placed


def _hit(a, b) -> bool:
    """两个矩形是否相交（含相切 ⇒ 算相交，宁可多留一点白）。"""
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _fit_canvas(words, *, width: int, height: int, rounds: int = 4) -> tuple:
    """**把字号整体放大到内容铺满画布** ⇒ `(items, scale)`。

    为什么是"放大字号"而不是"缩小画布"：出图时会按内容裁边 ⇒ **最终图的大小＝内容包围盒**。
    缩画布只是把词挤到一起，最终图反而更小更糊；放大字号才能让图**又大又满**。
    做法：排一轮 → 量填充率 → 按 `sqrt(fill)` 调字号再排（最多 4 轮，取"排得下且最满"的那一轮）。
    """
    import math as _m
    best, best_scale, best_fill = [], 1.0, 0.0
    scale = 1.0
    for _ in range(max(1, int(rounds))):
        items = layout(words, width=width, height=height, scale=scale)
        if not items:
            break
        _w = max(p["x"] + p["w"] for p in items) - min(p["x"] for p in items)
        _h = max(p["y"] + p["h"] for p in items) - min(p["y"] for p in items)
        fill = min(1.0, (_w * _h) / float(max(1, width * height)))
        if fill > best_fill and len(items) >= len(best):
            best, best_scale, best_fill = items, scale, fill
        if fill >= 0.80:
            break
        _k = 1.0 / _m.sqrt(max(0.05, fill)) * 0.94
        _k = min(2.6, max(1.02, _k))
        scale = min(4.0, scale * _k)
    return best, best_scale


def render_png(words, path: str, *, width: int = W, height: int = H, bg=(255, 255, 255)) -> dict:
    """出图 ⇒ `{ok, path, words, note}`。**任何一步不成立都如实返回原因**，不产半成品。"""
    font_path = find_font()
    if not font_path:
        return {"ok": False, "path": "", "words": 0,
                "note": "本机没找到中文字体（%s），不画方块糊弄" % "、".join(_FONT_CANDIDATES)}
    items, _sc = _fit_canvas(words, width=width, height=height)
    if not items:
        return {"ok": False, "path": "", "words": 0, "note": "一个词都没排下（样本为空或画布太小）"}
    try:
        from PIL import Image, ImageDraw, ImageFont
        # ⛔ **按内容裁边**（autofit）：词少的时候，固定画布会留下大片空白 —— 图看着像"没排好"。
        #    先在大画布上排，再把内容包围盒裁出来（留一点边距）⇒ 不管 10 个词还是 40 个词都铺得满。
        _pad = 18
        _x0 = max(0, min(p["x"] for p in items) - _pad)
        _y0 = max(0, min(p["y"] for p in items) - _pad)
        _x1 = min(width, max(p["x"] + p["w"] for p in items) + _pad)
        _y1 = min(height, max(p["y"] + p["h"] for p in items) + _pad)
        _w, _h = max(80, _x1 - _x0), max(60, _y1 - _y0)
        img = Image.new("RGB", (_w, _h), bg)
        dr = ImageDraw.Draw(img)
        # 单色系：按频次给三档深浅（同一张图里**只用一种主色**，比彩虹色好读）
        _pal = [(37, 99, 235), (59, 130, 246), (96, 165, 250)]
        _mx = max(p["count"] for p in items) or 1
        for p in items:
            f = _font_cache.get((font_path, p["size"])) or ImageFont.truetype(font_path, p["size"])
            _font_cache[(font_path, p["size"])] = f
            k = 0 if p["count"] >= _mx * 0.6 else (1 if p["count"] >= _mx * 0.3 else 2)
            dr.text((p["x"] - _x0, p["y"] - _y0), p["word"], font=f, fill=_pal[k])
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        img.save(path, "PNG")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": "", "words": 0, "note": "出图失败：%s" % str(e)[:80]}
    return {"ok": True, "path": path, "words": len(items),
            "note": "已出图（%d 个词；字号按出现次数，越大的最近聊得越多）" % len(items)}


def export(chat_key: str, path: str = "", *, limit: int = MAX_WORDS, store=None) -> dict:
    """**从群聊记录直接出图**（对外只需要这一个入口）⇒ `{ok, path, words, note, top}``。

    ⛔ 样本为空 ⇒ **明确说"没数据"**（不出空白图）；`path` 不给 ⇒ 落在 `data/wordcloud/<群>.png`。
    """
    try:
        from .memory import hot_words
        ws = list(hot_words(chat_key, limit=int(limit)) or [])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": "", "words": 0, "note": "读热词失败：%s" % str(e)[:80], "top": []}
    if not ws:
        return {"ok": False, "path": "", "words": 0,
                "note": "这个会话还没有可统计的聊天记录（先去群里聊两句再出图）", "top": []}
    if not path:
        from .config import DATA_DIR
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(chat_key or "chat"))[:60]
        path = os.path.join(DATA_DIR, "wordcloud", safe + ".png")
    r = render_png(ws, path)
    r["top"] = [{"word": w, "count": c} for w, c in ws[:10]]
    return r


def stats() -> dict:
    """本模块的档位（给状态/自检用）：字体在不在、能画多少词。"""
    return {"font": find_font(), "max_words": MAX_WORDS, "size": [SIZE_MIN, SIZE_MAX],
            "canvas": [W, H]}


def _words_of(rows) -> Counter:
    """给判据用的计数器（本模块内部不用，但导出它比让判据自己写一份更可靠）。"""
    c = Counter()
    for w, n in (rows or []):
        c[_clean(w)] += int(n)
    return c
