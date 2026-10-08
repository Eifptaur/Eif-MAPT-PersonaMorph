# -*- coding: utf-8 -*-
"""row_geometry.py —— **与 DPI 无关**的会话行几何（T7b-3 "D0 绿带尺"落地）。

════════════════════════════════════════════════════════════════════════
★ 为什么要有这个模块
  要**跨 DPI** 定位会话行，而 DPI/缩放值是**不可穷举**的（用户可能用任何非常规缩放）。
  ⇒ 答案：**不查表。把"一行有多高"直接从图里量出来，用它当尺子。**
════════════════════════════════════════════════════════════════════════

★ 两把"图内尺子"（都不需要 DPI 数据、不需要任何实测数据）

  【尺子 1 · 绿带尺】`row_h_from_green(im)`
      微信把**当前打开的会话行整行填成绿色** ⇒ 这条绿带的高度**就等于一个行高**。
      实测（21 帧、窗口宽 315→2526px、深浅两套主题）：**96~97px，极差 1px**。
      为什么它能跨 DPI：绿带是**微信自己按真实字号排版画出来的**，
      DPI/缩放/字号怎么变，它都跟着变，而"它等于一行"永远成立。

  【尺子 2 · 比例尺】`ui_adapt.detect_scale()`
      运行时算 `物理宽 ÷ 感知宽` ⇒ 得到缩放**比例**（本机 1.5），
      任何"不在常规表里"的缩放值都能算出来（它是**比值**，不是**表项**）。
      与 `ui_adapt.to_click()` 配对，把截图坐标换回鼠标坐标。

★ 本模块的降级顺序（**绝不猜**，量不到就如实返回 None + 原因）
      ① 绿带尺（最准，等于真行高）
      ② 名字行 cy 差的**众数**（自校准；用尺子 1 的 ±35% 约束步长，防把"预览行"算进来）
      ③ 都没有 ⇒ `row_h=None`，`source="none"`，调用方自己决定怎么退

★ 与旧口径的关系（**推翻的错误**）
      ⛔ 旧结论「行距两档 86 / 97」**是测量污染**，已作废：
        86 来自**调试面板文字**（x≈453~467）落进了绝对窗口 `x∈[170,470)`，
        那是调试面板自己的行距。逐框核对 + 绿带独立通道**双重证伪**。
      ✅ 真实行距：**单档 96~97px**（= 绿带高）。
      ⇒ 因此本模块**不用任何绝对像素窗口**、**不做"分档检测"**。

用法：
    from agent import row_geometry as rg
    g = rg.measure(im)            # 传 PIL.Image（整屏或列表区截图）
    g.row_h                       # 一行高（px，图内空间）；量不到是 None
    g.source                      # "green" / "ocr-mode" / "none"
    g.to_mouse()                  # 缩放比例（可直接喂给点击换算）
"""
from __future__ import annotations

import collections
import logging
import os
import statistics
import time

from .config import ROOT

log = logging.getLogger(__name__)

__all__ = [
    "RowGeometry",
    "row_h_from_green",
    "green_band",
    "name_column",
    "measure",
    "MIN_GREEN_H",
    "GREEN_H_EXPECT",
]


#: 绿带高合理下界（px）—— 低于此值不认为它是"一整行高亮"
#: 说明：这是**兜底护栏**，不是判据。正常由 UI 自己的行高决定（本机 96~97）。
MIN_GREEN_H = 24

#: 本机实测的绿带高（仅用于**自测断言**，生产逻辑**不依赖**它）
GREEN_H_EXPECT = (96, 97)

# ── ★ 行高**缓存** ────────────────────────────────────────────────────────
# 为什么要缓存：`measure()` 原来**每帧都从绿带重测**（每次都要抓图 + 找绿带）。
#   而**行高由"微信自己的字号设置"决定，用户几乎不会改** ⇒ 属于**慢变量**，
#   每帧重测是纯浪费，还占着抓图/OCR 的时间预算。
# ⇒ 口径：**第一次能量到时量一次并落盘；以后直接读缓存**，
#   只在"缓存缺失"或"量出来的值与缓存严重不符"时才回退到重测。
# ⚠️ 缓存**不是判据**：它只是省一次测量。任何时刻重测失败都允许退回缓存值
#   （总比"量不到 ⇒ row_h=None ⇒ 整条定位链放弃"要好）。
_ROW_H_CACHE_PATH = os.path.join(ROOT, "data", "row_geometry.json")
_ROW_H_CACHE = None      # 进程内快照；None=还没读盘
_ROW_H_TOL = 0.35        # 重测值与缓存差多少算"字号变了"（相对）

__all__ = [
    "RowGeometry",
    "row_h_from_green",
    "green_band",
    "name_column",
    "measure",
    "MIN_GREEN_H",
    "GREEN_H_EXPECT",
    "cached_row_h",
    "remember_row_h",
    "forget_row_h",
]


def cached_row_h() -> int:
    """读**记住的行高**；没有 ⇒ `0`。

    落盘位置 `data/row_geometry.json`（走产品的原子写）。读不到/坏了 ⇒ **当没缓存**（返回 0），不抛。
    """
    global _ROW_H_CACHE
    if _ROW_H_CACHE is None:
        try:
            from . import persist as _ps
            # ⚠️ `load_checked` 返回的是**二元组** `(值, 可覆盖)`，**不是值本身**：
            #    早期直接 `dict(它)` ⇒ 对 2 元组构造 dict 会抛 ⇒ 被 except 吞掉 ⇒
            #    **永远读回 0**（"写了但读不出来"，静默失效）。这里必须解包取 `[0]`。
            d, _ok = _ps.load_checked(_ROW_H_CACHE_PATH, {})
            _ROW_H_CACHE = dict(d) if isinstance(d, dict) else {}
        except Exception as e:
            log.debug("读行高缓存失败（当没缓存）：%s", e)
            _ROW_H_CACHE = {}
    try:
        v = int((_ROW_H_CACHE or {}).get("row_h") or 0)
    except Exception:
        v = 0
    return v if v > 0 else 0


def remember_row_h(row_h: int, source: str = "") -> bool:
    """把量到的行高**记下来**（落盘）。`row_h<=0` 或不合理 ⇒ **不记**（不许拿"没量到"覆盖好值）。"""
    global _ROW_H_CACHE
    try:
        n = int(row_h)
    except Exception:
        return False
    if n <= 0 or n < MIN_GREEN_H:
        return False
    if cached_row_h() == n:      # 值没变就不重写（省磁盘写、避免无谓 mtime 变化）
        return True
    d = {"row_h": n, "source": str(source or ""), "ts": int(time.time())}
    _ROW_H_CACHE = dict(d)
    try:
        from . import persist as _ps
        _ps.atomic_write_json(_ROW_H_CACHE_PATH, d)
        return True
    except Exception as e:
        log.debug("写行高缓存失败（只影响下次要重量）：%s", e)
        return False


def forget_row_h() -> bool:
    """忘掉记住的行高（用户改了字号 / 换机器 / 怀疑不对时用）。"""
    global _ROW_H_CACHE
    _ROW_H_CACHE = {}
    try:
        if os.path.exists(_ROW_H_CACHE_PATH):
            os.remove(_ROW_H_CACHE_PATH)
        return True
    except Exception as e:
        log.debug("删行高缓存失败：%s", e)
        return False


class RowGeometry(object):
    """一次测量的结果容器（纯数据，不持有图像）。"""

    __slots__ = ("row_h", "source", "green_y", "green_score", "n_names",
                 "pitch_ocr", "scale", "pane", "size", "h_mode", "note")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    # ── 派生量 ──────────────────────────────────────────────────────────
    @property
    def ok(self) -> bool:
        """是否拿到了可用的行高。"""
        return bool(self.row_h)

    def row_center(self, index: int, top: int = 0) -> float:
        """第 `index` 行（0 起）的中心 y（**图内坐标**）。

        `top` = 列表第一行中心（不给就是 0 起算）。
        ⇒ 这就是"用图内尺子推所有几何"的意思：只靠 `row_h`，不需要任何绝对常量。
        """
        if not self.row_h:
            raise ValueError("row_h 未知（source=%s），不能推行位置" % (self.source,))
        return float(top) + float(self.row_h) * (index + 0.5)

    def row_span(self, index: int, top: int = 0) -> tuple:
        """第 `index` 行的 `(y0, y1)`（图内坐标）。"""
        if not self.row_h:
            raise ValueError("row_h 未知（source=%s），不能推行位置" % (self.source,))
        y0 = float(top) + float(self.row_h) * index
        return y0, y0 + float(self.row_h)

    def to_mouse(self, scale=None) -> float:
        """截图坐标 → 鼠标坐标的换算比例（交给 `ui_adapt.to_click`）。"""
        if scale is not None:
            return float(scale)
        if self.scale:
            return float(self.scale)
        try:
            from agent import ui_adapt as ua
            return float(ua.detect_scale() or 1.0)
        except Exception:
            return 1.0

    def __repr__(self):
        return ("<RowGeometry row_h=%s src=%s green_y=%s n=%s pitch=%s "
                "scale=%s pane=%s size=%s>" %
                (self.row_h, self.source, self.green_y, self.n_names,
                 self.pitch_ocr, self.scale, self.pane, self.size))


# ────────────────────────────────────────────────────────────────────────────
# 尺子 1：绿带
# ────────────────────────────────────────────────────────────────────────────
def green_band(im, min_h: int = MIN_GREEN_H, x0=None, x1=None) -> dict:
    """取"当前打开行"的绿带 `{'y0','y1','h','score'}`；量不到返回 `{}`。

    直接复用 `chat_ocr.green_bands()`（纯像素通道，**不调用 OCR**）。
    取 `score` 最高的一条 —— 它是"整行填绿"，绿占比远高于头像绿等假绿。

    ⚠️ **取样窗自愈（315px 极窄窗口实测，）**：
    `green_bands()` 默认取样窗来自 `chat_ocr._green_x()`，即"面板左沿往左 144~14px"。
    当窗口极窄（实测 W=315）时 `pane_left_for()` 给出 `81`（≈ `PANE_LEFT_REL*W`），
    取样窗退化成 `(0, 67)` —— **整块落在窗口左缘的头像/图标区，那里没有绿**
    ⇒ 判据失效（实测该帧绿其实铺在 `x=96~312`，与取样窗**零重叠**）。
    ⇒ 这里做**自愈**：默认窗量不到时，改用"**全宽**"再扫一次。
      宁可慢一点，也不要因为"比例拍出来的窗"把真实绿带判丢。
    """
    if im is None:
        return {}
    try:
        from agent import chat_ocr as co
    except Exception:
        return {}

    def _try(_x0, _x1):
        try:
            bands = co.green_bands(im, min_h=int(min_h), x0=_x0, x1=_x1) or []
        except Exception:
            return []
        if not bands:
            return []
        b = bands[0]                  # 已按 score 降序
        h = int(b.get("y1", 0)) - int(b.get("y0", 0))
        if h < int(min_h):
            return []
        return [{"y0": int(b["y0"]), "y1": int(b["y1"]), "h": h,
                 "score": b.get("score"), "y_abs": b.get("y_abs")}]

    # ① 调用方显式给了窗 ⇒ 尊重调用方，不自愈
    if x0 is not None or x1 is not None:
        r = _try(x0, x1)
        return r[0] if r else {}

    # ② 默认窗
    r = _try(None, None)
    if r:
        return r[0]

    # ③ 自愈：全宽重扫（只在不给窗、且默认窗量不到时触发）
    try:
        W = int(im.size[0])
    except Exception:
        W = 0
    if W:
        r = _try(0, W)
        if r:
            r[0]["self_healed"] = True
            return r[0]
    return {}


def row_h_from_green(im, min_h: int = MIN_GREEN_H, x0=None, x1=None):
    """**尺子 1**：绿带高 ＝ 一个行高。返回 `(row_h, band_dict)`，量不到 `(None, {})`。

    ⚠️ `green_bands()` 返回的是 **dict 列表**（`{'y0','y1','y_abs','score'}`），
    **不是元组** —— 早期脚本按元组解包，静默失败过一次，这里显式按键取。
    """
    b = green_band(im, min_h=min_h, x0=x0, x1=x1)
    if not b:
        return None, {}
    return b["h"], b


# ────────────────────────────────────────────────────────────────────────────
# 名字列：**结构性定位**，不写死像素窗口
# ────────────────────────────────────────────────────────────────────────────
def name_column(boxes, pane, width, bin_px: int = 8, min_count: int = 2):
    """结构性定位"会话名字列" —— 替代旧写法 `x∈[170,470)`。

    会话行的结构永远是：`[头像方块] [名字(上) / 预览(下)]`
    ⇒ 名字文字 x 落在"**列表左沿左侧、且是全帧最左的那一簇文字**"。

    做法：取 `x < pane` 的框，对 x 做 `bin_px` 分箱，返回"计数 ≥ min_count 的最小带"
    作为名字列起点。

    ⚠️ 为什么**不**用绝对窗口：旧写法 `x∈[170,470)` 会**罩住屏幕上别的 UI**
    （实测罩住了调试面板，把面板的行距当成了会话行距 ⇒ 造出"行距两档"的假象）。
    ⚠️ 为什么用 `pane`：`pane` 是**列表右沿**（`chat_header.pane_left_for`），
    它本身也是结构量（列表与聊天区的分界），不是拍脑袋的常量。
    """
    bound = int(pane) if pane else int(width or 0)
    if not bound:
        bound = int(width or 0)
    cand = [b for b in boxes if b[1] < bound]
    if not cand:
        return None, []
    hist = collections.Counter((int(b[1]) // int(bin_px)) * int(bin_px) for b in cand)
    bands = sorted(x for x, c in hist.items() if c >= int(min_count))
    if not bands:
        # 退化：至少给一个最左的框（宁可用一个，也不返回 None 装作没数据）
        return min(b[1] for b in cand), cand
    return bands[0], cand


# ────────────────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────────────────
# ── ★ 投影补行────────────────────────────────────────────
#   为什么：实测 OCR **只认得 69% 的会话行**（单字符名字整条漏、浅色帧漏整行）
#   ⇒ 任何"数第几条 → 点它"的逻辑都会**错位**（方向可能是"点到相邻行"）。
#   投影（每行有多少个"有内容的像素"）**不依赖识别** ⇒ **不会漏行**：
#   一段连续的有内容区 = 一行；两段之间的空带 = 行间分隔。
def project_row_edges(im, pane: int, x0: int | None = None, x1: int | None = None,
                      min_ink: float = 6.0, min_h: int = 4, gap_max: int = 3):
    """用**文字列的纵向投影**找出每行的上下边界 ⇒ `[(y_top, y_bot), ...]`（图内像素，自上而下）。

    `pane` ＝ 会话列表右沿（用它反推名字列大致范围）；`x0/x1` 给了就用给的。
    `min_ink`：一行的"边缘强度"低于此值不算行（滤噪点）；`min_h`：太矮的段不算一行；
    `gap_max`：同一次识别里被字间空隙切开的段，间隔 ≤ 此值就并回一行。

    ⛔ 本函数**只做几何**：不判断"这行是不是会话"（那是上层的事），也**不猜**——找不到就返回 `[]`。
    """
    try:
        import numpy as np
    except Exception:
        return []
    if im is None:
        return []
    g = im.convert("L")
    W, H = g.size
    a = np.asarray(g, dtype=np.int16)
    if not x0:
        x0 = max(0, int(pane or W) - 320)     # 名字列：从右沿往回一段
    if not x1:
        x1 = max(x0 + 1, int(pane or W) - 40) # 让开时间戳那一条
    x0, x1 = max(0, min(int(x0), W - 1)), max(1, min(int(x1), W))
    col = a[:, x0:x1]
    if col.shape[1] < 2 or col.shape[0] < 8:
        return []
    # 逐行的"边缘强度"：相邻像素差之和（文字笔画多 ⇒ 边缘多；纯背景 ⇒ 接近 0）
    ink = np.abs(np.diff(col, axis=1)).sum(axis=1).astype(float)
    thr = max(float(min_ink), float(ink.mean() + ink.std() * 0.5))
    on = ink > thr
    segs, s = [], None
    for y in range(on.shape[0]):
        if on[y] and s is None:
            s = y
        elif (not on[y]) and s is not None:
            segs.append((s, y - 1))
            s = None
    if s is not None:
        segs.append((s, H - 1))
    merged = []
    for y0, y1 in segs:
        if merged and (y0 - merged[-1][1] - 1) <= gap_max:
            merged[-1] = (merged[-1][0], y1)
        else:
            merged.append((y0, y1))
    return [(y0, y1) for y0, y1 in merged if (y1 - y0 + 1) >= min_h]


def merge_rows_with_projection(im, boxes, pane: int, row_h: int | None = None,
                               x0: int | None = None, x1: int | None = None) -> dict:
    """**把 OCR 认到的行与投影找到的行合并**（补上 OCR 漏掉的）。

    返回 `{"rows": [{y, y0, y1, text, from_ocr}], "ocr_n", "proj_n", "merged_n", "补回": n}`。

    ⛔ 口径：**投影负责"有几行、每行在哪"，OCR 只负责"这行叫什么"** ——
    两者**不是二选一**：投影不会漏行但不知名字；OCR 知道名字但会漏行。合并后：
    · 每一行都出现（不会因为 OCR 漏了就"数错条数"）；
    · 认到名字的行带上 `text`，认不到的 `text=None`（**如实为空，不编**）。
    """
    rows = []
    proj = project_row_edges(im, pane, x0=x0, x1=x1)
    ocr_rows = []
    for b in (boxes or []):
        try:
            _t, _x, _y, _w, _h = b[0], int(b[1]), int(b[2]), int(b[3]), int(b[4])
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            continue
        cy = _y + _h / 2.0
        ocr_rows.append({"text": str(_t or ""), "cy": cy, "y0": _y, "y1": _y + _h})
    used = set()
    for (py0, py1) in proj:
        hit = None
        for i, o in enumerate(ocr_rows):
            if i in used:
                continue
            if py0 - 2 <= o["cy"] <= py1 + 2:
                hit = (i, o)
                break
        if hit:
            used.add(hit[0])
            rows.append({"y": int((py0 + py1) / 2), "y0": py0, "y1": py1,
                         "text": hit[1]["text"], "from_ocr": True})
        else:
            rows.append({"y": int((py0 + py1) / 2), "y0": py0, "y1": py1,
                         "text": None, "from_ocr": False})
    # OCR 认到、但投影没覆盖的（投影在极端帧可能失败）⇒ 也保留，别丢
    for i, o in enumerate(ocr_rows):
        if i not in used:
            rows.append({"y": int(o["cy"]), "y0": o["y0"], "y1": o["y1"],
                         "text": o["text"], "from_ocr": True})
    rows.sort(key=lambda r: r["y"])
    _补 = sum(1 for r in rows if not r["from_ocr"])
    return {"rows": rows, "ocr_n": len(ocr_rows), "proj_n": len(proj),
            "merged_n": len(rows), "补回": _补}


def first_row_y_from_projection(im, pane: int, **kw):
    """**首行 y**（图内像素）—— 取投影里**第一段的上沿**；取不到 ⇒ `None`（**不猜**）。

    为什么要单独一个入口：上层算"第 N 行 y"（`session_order.row_y(order)`）时，
    第一步就是要一个**可靠的首行 y**。以前靠"扫名字列找第一个文字块"——
    **OCR 漏行时这一步就会算偏**（进而点到相邻行）。改用投影后**不依赖识别** ⇒ 首行稳。
    """
    edges = project_row_edges(im, pane, **kw)
    return int(edges[0][0]) if edges else None


def measure(im, boxes=None, pane=None, timeout: float = 40.0,
            allow_ocr_fallback: bool = False) -> RowGeometry:
    """量出**与 DPI 无关**的行几何。

    参数
    ----
    im      : PIL.Image —— 整屏或列表区截图
    boxes   : `[(text,x,y,w,h)]`，给了就不重新 OCR（省一次识别）
    pane    : 会话列表右沿；不给就调 `chat_header.pane_left_for(im)`
    timeout : OCR 超时（仅在需要 OCR 时生效）
    allow_ocr_fallback : 是否允许"无绿带时"用 OCR 文字行距**降级估**行高。
        默认 **False** —— 因为全量回归证明：**没有绿带的帧大多根本不是会话列表**
        （登录通知页/下载页/网页预览），此时任何文字行距都是噪声。
        调用方只有在**已确证本帧是会话列表**时才该打开它。

    返回 `RowGeometry`；**量不到行高时 `row_h=None, source="none"`，绝不猜**。

    ★ 行高**缓存**（加，`source="cached"`）：
      行高是**慢变量**（只跟微信的字号设置走，用户几乎不改）⇒
      **第一次量到就落盘**（`remember_row_h`），以后本帧没有绿带时**先读缓存**，
      不再每帧重测。缓存的优先级**高于现场 OCR 降级**（缓存来自真绿带实测，
      OCR 估的值本就有噪声）。想强制重量 ⇒ `forget_row_h()`。
      ⚠️ 缓存**不是判据**，只是省一次测量；绿带一旦重新量到就以**实测值**为准并覆盖缓存。
    """
    if im is None:
        return RowGeometry(row_h=None, source="none", note="im is None")

    W, H = im.size

    # 缩放比例（尺子 2）—— 失败不影响主流程
    scale = None
    try:
        from agent import ui_adapt as ua
        scale = float(ua.detect_scale() or 0) or None
    except Exception:
        scale = None

    # 列表右沿
    if pane is None:
        try:
            from agent import chat_header as ch
            pane = int(ch.pane_left_for(im) or 0) or None
        except Exception:
            pane = None

    # ① 尺子 1：绿带高
    g_h, g_band = row_h_from_green(im)
    if g_h:
        # ★ 量到了 ⇒ **记下来**（下次不必再重量）。只在用户改字号时才该变值。
        try:
            remember_row_h(g_h, "green")
        except Exception:
            pass
        return RowGeometry(
            row_h=g_h, source="green",
            green_y=(g_band.get("y0"), g_band.get("y1")),
            green_score=g_band.get("score"),
            n_names=None, pitch_ocr=None,
            scale=scale, pane=pane, size=(W, H), h_mode=None,
            note="绿带高 = 行高（图内实测，无需 DPI 表）")

    # ★ ①' 本帧量不到 ⇒ **先用记住的行高**（慢变量，用户没改字号就还是它）。
    #    为什么放在"降级到 OCR"之前：OCR 估出来的行高**本就有噪声**
    #    （模块头注释里就写着"无绿带的帧大多根本不是会话列表"），
    #    而缓存值来自**当初那条绿带的真实测量** ⇒ **缓存比现场 OCR 更可信**。
    _cached = cached_row_h()
    if _cached:
        return RowGeometry(
            row_h=_cached, source="cached",
            green_y=None, green_score=None,
            n_names=None, pitch_ocr=None,
            scale=scale, pane=pane, size=(W, H), h_mode=None,
            note="本帧无绿带 ⇒ 用**记住的行高**（首次打开时实测并落盘；用户改字号才会变）")

    # ② 降级：OCR 名字行 cy 差的众数（自校准）
    if boxes is None:
        try:
            from agent import chat_ocr as co
            items = co.recognize(im, timeout=timeout) or []
            boxes = [(str(t).strip(), x, y, w, h) for (t, x, y, w, h) in items]
        except Exception:
            boxes = []
    if not boxes:
        return RowGeometry(row_h=None, source="none", scale=scale,
                           pane=pane, size=(W, H),
                           note="无绿带、无 OCR 框 ⇒ 量不到行高")

    x_band, cand = name_column(boxes, pane, W)
    if x_band is None or not cand:
        return RowGeometry(row_h=None, source="none", scale=scale,
                           pane=pane, size=(W, H), note="名字列定位失败")

    hs = [b[4] for b in cand]
    h_mode = collections.Counter(hs).most_common(1)[0][0] if hs else None

    names = [b for b in cand if x_band <= b[1] < x_band + 24]
    names.sort(key=lambda b: b[2])
    cys = [b[2] + b[4] / 2.0 for b in names]

    # 无尺子约束时用宽窗兜底（60~140），有则用 ±35%
    steps = [b - a for a, b in zip(cys, cys[1:]) if 60 <= (b - a) <= 140]
    if not steps:
        return RowGeometry(row_h=None, source="none", scale=scale, pane=pane,
                           size=(W, H), h_mode=h_mode, n_names=len(names),
                           note="名字行不足两行 ⇒ 无法估行距")
    pitch = statistics.median(steps)

    # ★ 降级通道的可信度问题（全量回归暴露，**必须正视**）
    #   全量 130 帧里有 13 帧量不到绿带。逐帧转储一看，**这 13 帧全不是会话列表**：
    #     · "登录操作通知"页 / "文件下载"页 / 网页预览(apple.CloudDocs) / 文件传输助手面板；
    #     · 它们照样有一堆 OCR 文字（n_names 可达 20），
    #       ⇒ 任何"按文字行数/文字间距"的判据都会被它们**骗过**，
    #         算出来的 62 / 74 / 86 **是相邻两段无关文字的间距，不是行高**。
    #   ⇒ 结论：**没有绿带就没有可靠的图内尺子**。
    #     降级通道只在"**能确证这是会话列表**"时才有意义；
    #     而"能不能确证"本身又需要一个判据 —— 与其再造一层可能被骗的判据，
    #     **不如如实返回 None**（技能纪律：不许猜、宁可说量不到）。
    #   ⇒ 因此：**无绿带默认返回 None**；若调用方**确信**当前帧是会话列表
    #     （例如刚从 `chat_header` 拿到 pane 且列表可见），
    #     可传 `allow_ocr_fallback=True` 显式打开降级通道，并**自行承担校验责任**。
    if not allow_ocr_fallback:
        return RowGeometry(row_h=None, source="none", scale=scale, pane=pane,
                           size=(W, H), h_mode=h_mode, n_names=len(names),
                           pitch_ocr=pitch if steps else None,
                           note="无绿带 ⇒ 无可信图内尺子；本帧是否含会话列表未经确证，"
                                "拒绝给行高（如需降级通道请显式传 allow_ocr_fallback=True）")

    if len(names) < 3:
        return RowGeometry(row_h=None, source="none", scale=scale, pane=pane,
                           size=(W, H), h_mode=h_mode, n_names=len(names),
                           pitch_ocr=pitch if steps else None,
                           note="无绿带，且会话名字行仅 %d 条（<3）⇒ 降级通道不可信"
                                % len(names))

    return RowGeometry(row_h=int(round(pitch)), source="ocr-mode",
                       green_y=None, green_score=None,
                       n_names=len(names), pitch_ocr=pitch,
                       scale=scale, pane=pane, size=(W, H), h_mode=h_mode,
                       note="无绿带 ⇒ 降级用名字行 cy 差众数（自校准；"
                            "已由调用方 allow_ocr_fallback 显式背书）")
