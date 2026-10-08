# -*- coding: utf-8 -*-
"""OCR **同图缓存**（给"同一张界面截图被反复读"省时间）——做到极致的三层。

## 为什么值得做（本仓实测）
硬读档（全对档）单张 **4.5s**（热态），而真实流程里**同一张截图会被读多次**：
定位会话行、确认标题、找按钮、复核发送对象……每次都重跑一遍 OCR 是纯浪费。
截图之间通常只差**很小一块**（光标闪、时钟走字、正在打字的那一行）⇒ 完全可以只重读变化的那一块。

## ⭐ 实战怎么用（实测：8 帧、每帧只动一条状态栏，热态）
| 用法 | 每帧均摊 | 说明 |
|---|---|---|
| 无缓存（全对档） | ≈2.6s | 每次都重读整图 |
| 缓存 + **全对档**读变化带 | 1.97s | 变化带仍要付第二引擎 ≈1s 固定开销 ⇒ 提速有限 |
| 缓存 + **快速档**读变化带 | **0.21s** | **推荐**：未变的行直接复用，变化的行用便宜的内置档读 ⇒ 比无缓存快 ≈12 倍 |

结论：**缓存要配"快速档读变化带"才发挥到极致**（变化带很小，不需要动用第二引擎）；
要找那 5 条"只有第二引擎读得出"的字时，再对**整图**用全对档（那是另一条路，别混着用）。

## 三层（从快到慢，逐层兜底）
1. **整图精确命中**：整图像素哈希一致 ⇒ 直接返回上次结果，**OCR 调用 0 次**。
2. **分块复用（tile）**：把图切成固定大小的块；**块哈希命中就复用那几块的文字**（坐标平移回整图），
   只有**变化的块**需要重读。
3. **合并未命中块再读**：把没命中的块**合并成矩形**后**一次性**读 —— 这条是性能关键：
   第二引擎有 ≈1s **固定开销**（实测 400×40 的一条也要 1.10s），逐块各付一次会把收益吃光。
   未命中面积超过整图的一半时，直接整图读一遍（更划算）。

## 正确性纪律（缓存最容易在这里出事）
- **只缓存"成功的读数"**：读空/被熔断（fail-closed）时**不得写入**，否则会把"这次没读到"钉成长期结论。
  ⚠️ **例外（实测补的，别再删）**：整图读取**成功**时，某条带里**本来就没有文字**（纯背景带）
  属于"读成功且此处无字"，**必须归档**（`put_tile(..., trusted_empty=True)`）——
  它是**可信结论**而非读失败。不归档的话这条带**每帧都会再读一遍**：871×547 的真实截图
  7 条带里有 3 条纯背景 ⇒ **每帧固定重读 47% 面积**（A4 实测从红到绿的完整归因）。
  安全性由"**键是内容**"兜底：带内容一变（哪怕只是**从无字变成有字**）哈希就不同 ⇒ 必 miss，
  所以归档空带**不可能**把"已被删掉的文字"钉死（B1b 专门守这条）。
- **键是内容，不是时间**：所以**不存在"过期数据"**——内容变了就必然不命中（这也是不设 TTL 的原因）。
- **有界**：块与整图都是 LRU，条目上限可设；内存不会随截图张数无限涨。
- **线程安全**：读写都在一把锁里（产品里监听线程与发送线程都会读同一张图）。
- **命中必须可见**：`stats()` 给命中率；全静默的"省时间"没法验证也无法报警。
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict

log = logging.getLogger("persona-morph")

#: ⭐ 默认块＝**整宽 × 96 高的横带**（宽写 0 表示"用整图宽度"）。
#: ⛔ 为什么不是方块（尺寸踩过坑）：界面文字**又宽又扁**（一行 100~200px），方块块会把行切断
#:   ⇒ "整条文字完整落在一块里"的判定几乎永不成立 ⇒ **块缓存 0 命中**（判据 A4 当场抓到：
#:   统计里 `hits_tile: 0 / misses_tile: 756`）。改成横带后，一行天然落在一条带里，复用才成立。
#: 96 高：界面列表行高多在 40~90px ⇒ 改一行通常只影响 1~2 条带。
DEFAULT_TILE = (0, 96)
#: 相邻块重叠像素：文字行**压在块缝上**时必须靠它救（否则被切断的行两边的块都读不全）
DEFAULT_OVERLAP = 16


def _digest(raw: bytes) -> str:
    return hashlib.blake2b(raw, digest_size=12).hexdigest()


def image_key(img) -> str:
    """整图像素哈希（**用像素而非文件字节**：同一画面换个 PNG 压缩级别也该命中）。"""
    return _digest(img.tobytes())


class OcrCache:
    """块级 + 整图级的 OCR 结果缓存（LRU 有界、线程安全）。"""

    def __init__(self, max_images: int = 48, max_tiles: int = 4096,
                 tile: tuple = DEFAULT_TILE, overlap: int = DEFAULT_OVERLAP):
        self.max_images = max(1, int(max_images))
        self.max_tiles = max(64, int(max_tiles))
        self.tile = (int(tile[0]), int(tile[1]))
        self.overlap = max(0, int(overlap))
        self._lock = threading.Lock()
        self._images = OrderedDict()   # 整图 key -> (w, h, items)
        self._tiles = OrderedDict()    # 块 key -> items（**相对该块的坐标**）
        self.hits_full = 0
        self.hits_tile = 0
        self.misses_tile = 0
        self.puts = 0

    # ── 整图级 ────────────────────────────────────────────────────────
    def get_full(self, img):
        k = image_key(img)
        with self._lock:
            it = self._images.get(k)
            if it is None:
                return None
            self._images.move_to_end(k)
            self.hits_full += 1
            return list(it[2])

    def put_full(self, img, items):
        if not items:
            return          # ⛔ 空结果不入缓存（fail-closed / 读空都别钉死）
        k = image_key(img)
        with self._lock:
            self._images[k] = (img.size[0], img.size[1], list(items))
            self._images.move_to_end(k)
            while len(self._images) > self.max_images:
                self._images.popitem(last=False)
            self.puts += 1

    # ── 块级 ──────────────────────────────────────────────────────────
    def tiles_of(self, img):
        """把图切成 `(块哈希, (x0,y0))` 列表（带重叠；尺寸不足则整图一块）。"""
        w, h = img.size
        tw, th = self.tile
        tw = tw or w            # 宽 0 ＝ 整宽（横带模式，默认）
        ov = self.overlap
        out = []
        if w <= tw and h <= th:
            out.append((_digest(img.tobytes()), (0, 0), (w, h)))
            return out
        step_x = max(1, tw - ov)
        step_y = max(1, th - ov)
        y = 0
        while y < h:
            x = 0
            while x < w:
                x1, y1 = min(w, x + tw), min(h, y + th)
                box = (x, y, x1, y1)
                out.append((_digest(img.crop(box).tobytes()), (x, y), (x1 - x, y1 - y)))
                if x1 >= w:
                    break
                x += step_x
            if y1 >= h:
                break
            y += step_y
        return out

    def get_tile(self, h: str):
        with self._lock:
            it = self._tiles.get(h)
            if it is None:
                self.misses_tile += 1
                return None
            self._tiles.move_to_end(h)
            self.hits_tile += 1
            return list(it)

    def put_tile(self, h: str, items, trusted_empty: bool = False):
        """归档一条块的读数。

        ⛔ **空结果默认不入缓存**（fail-closed / "这次没读到"不许被钉死）—— 这是原来的纪律，保留。
        ✅ 但有一类"空"是**可信的**：**整图读取成功**时，某个块里**本来就没有文字**（纯背景带）。
           它不是"读失败"，是"读成功且此处无字"。若不归档它，这条带**每帧都会再读一遍**
           ⇒ A4 实测抓到：871×547 的图有 3/7 条带永远没有完整文字，**每帧固定重读 47% 面积**。
           ⇒ 所以调用方在"整图成功读取"这条路径上，可以传 `trusted_empty=True` 显式归档空块。
           ⚠️ 纪律没变松：只有**上游读取确实成功**（如已经拿到非空整图结果）时才允许传这个标记；
           纯空读（整图都没读到东西）**绝不许**走这条路（fail-closed 由 `put_full` 把着门）。
        """
        if not items and not trusted_empty:
            return
        with self._lock:
            self._tiles[h] = list(items)
            self._tiles.move_to_end(h)
            while len(self._tiles) > self.max_tiles:
                self._tiles.popitem(last=False)
            self.puts += 1

    # ── 观测与维护 ────────────────────────────────────────────────────
    def stats(self) -> dict:
        with self._lock:
            tot = self.hits_full + self.hits_tile + self.misses_tile
            return {"images": len(self._images), "tiles": len(self._tiles),
                    "hits_full": self.hits_full, "hits_tile": self.hits_tile,
                    "misses_tile": self.misses_tile, "puts": self.puts,
                    "hit_rate": (self.hits_full + self.hits_tile) / tot if tot else 0.0}

    def clear(self, why: str = "") -> int:
        with self._lock:
            n = len(self._images) + len(self._tiles)
            self._images.clear()
            self._tiles.clear()
        log.info("OCR 缓存已清空（%d 条）%s", n, ("：%s" % why) if why else "")
        return n


#: 进程级默认缓存（产品里共用一份；判据会自带独立实例，互不干扰）
DEFAULT = OcrCache()


def cached_recognize(img, reader, cache: OcrCache = None) -> list:
    """**带缓存的读取**：`reader(crop) -> items` 是"真去读"的函数（调用方传 recognize_boost 等）。

    流程：
      ① 整图精确命中 ⇒ 直接返回（OCR 0 次）；
      ② 否则按块查：命中的块复用文字（坐标平移回整图），未命中的块**合并成矩形**后交给 `reader`；
      ③ 读到的结果按块补齐缓存，并返回"命中块 ∪ 新读块"的并集（去重）。

    ⛔ 未命中面积 > 整图一半时直接整图读（分块反而更慢）。
    ⛔ 结果**单调**：缓存只做复用，不会让读数变少（判据 `ocr_cache_selftest` 逐条比对着守）。
    """
    c = cache if cache is not None else DEFAULT
    hit = c.get_full(img)
    if hit is not None:
        return hit
    w, h = img.size
    tiles = c.tiles_of(img)
    out, seen = [], set()
    miss_boxes = []
    for th, (x0, y0), (tw, thh) in tiles:
        items = c.get_tile(th)
        if items is None:
            miss_boxes.append((x0, y0, x0 + tw, y0 + thh))
            continue
        for txt, x, y, ww, hh in items:      # 坐标平移回整图
            n = str(txt)
            if n in seen:
                continue
            seen.add(n)
            out.append((n, x + x0, y + y0, ww, hh))
    if not miss_boxes and out:
        return out
    # 未命中面积太大 ⇒ 整图读一遍（分块不划算）
    miss_area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in miss_boxes)
    if miss_area * 2 >= w * h or not miss_boxes:
        items = reader(img)
        c.put_full(img, items)
        # ⛔ 这一步**必须有**：首次读取（或大改）走整图路径时，若只写整图缓存、不写横带，
        #   后续帧就永远没有横带可复用 ⇒ 缓存退化成"只能整图精确命中"（判据 A4/A5 当场抓到：
        #   `tiles: 0` 而 `misses_tile` 一直在涨）。补写横带，下一帧才能只重读变化的那条。
        # ✅ `trusted_empty=True`：整图读**成功了**（要么拿到字、要么整体无字且被 `put_full` 拦下），
        #   所以"这条带里没字"是**可信结论**而不是读失败 ⇒ 必须归档，否则每条纯背景带每帧重读。
        _fill_tiles(c, img, tiles, [(str(i[0]), i[1], i[2], i[3], i[4]) for i in items],
                    trusted_empty=True)
        for txt, x, y, ww, hh in items:
            n = str(txt)
            if n not in seen:
                seen.add(n)
                out.append((n, x, y, ww, hh))
        return out
    # 合并成"行/列带"后逐带读（带之间最多 2 次引擎调用，避免第二引擎固定开销被重复付）
    read_ok_boxes = []
    for box in _merge_boxes(miss_boxes):
        crop = img.crop(box)
        items = reader(crop)
        read_ok_boxes.append(box)
        x0, y0 = box[0], box[1]
        for txt, x, y, ww, hh in items:
            n = str(txt)
            if n in seen:
                continue
            seen.add(n)
            out.append((n, x + x0, y + y0, ww, hh))
    # 把"这次读过的块"写进缓存（块内容 → 该块范围内的文字，坐标转回相对块）
    # ✅ 可信空归档的**边界**：只有**完全落在这次成功读取的矩形内**的块，才能把"空"当可信结论。
    #   跨在读取矩形边上的块（只读到一半）**不许**归档空 —— 它另一半根本没被读过。
    _fill_tiles(c, img, tiles, out, trusted_empty=False)
    for th, (x0, y0), (tw, thh) in tiles:
        if any(bx0 <= x0 and by0 <= y0 and (x0 + tw) <= bx1 and (y0 + thh) <= by1
               for (bx0, by0, bx1, by1) in read_ok_boxes):
            c.put_tile(th, [(str(t), x - x0, y - y0, ww, hh) for (t, x, y, ww, hh) in out
                            if x0 <= x and y0 <= y and (x + ww) <= (x0 + tw) and (y + hh) <= (y0 + thh)],
                       trusted_empty=True)
    return out


def _merge_boxes(boxes: list) -> list:
    """把未命中块合并成尽量少的**矩形带**。

    两条合并规则（都为省**引擎调用次数**，因为第二引擎每次都有 ≈1s 固定开销）：
      1. 同一水平行的块 ⇒ 合成一条横带（本仓默认就是横带，所以这条主要给方块模式用）；
      2. **纵向相邻/重叠的带 ⇒ 再合成一个更大的矩形**（相邻带的重叠像素本来就要读两遍，合并更划算）。

    ⛔ 返回的元组顺序必须是 `(左, 上, 右, 下)` —— PIL 的 `crop()` 就按这个顺序要；
      第一版返回了 `(x0, x1, y0, y1)`，当场被 `ValueError: Coordinate 'lower' is less than 'upper'`
      抓住（判据里那条"小块变化"直接崩），别再写错。
    """
    rows = {}
    for x0, y0, x1, y1 in boxes:
        r = rows.get(y0)
        if r is None:
            rows[y0] = [x0, y0, x1, y1]
        else:
            r[0] = min(r[0], x0)
            r[2] = max(r[2], x1)
            r[3] = max(r[3], y1)
    bands = [b for b in sorted(rows.values(), key=lambda b: b[1]) if b[0] < b[2] and b[1] < b[3]]
    # 规则 2：纵向相邻（含重叠）就并成一个矩形
    merged = []
    for b in bands:
        if merged and b[1] <= merged[-1][3]:      # 上边 ≤ 前一条的下边 ⇒ 相邻/重叠
            merged[-1][2] = max(merged[-1][2], b[2])
            merged[-1][3] = max(merged[-1][3], b[3])
        else:
            merged.append(list(b))
    return merged


def _fill_tiles(c: OcrCache, img, tiles: list, items: list, trusted_empty: bool = False):
    """把**已读到的整图坐标结果**按块归档（这样下一帧只有变化的块会 miss）。

    `trusted_empty=True` 时**空块也归档**——理由见 `OcrCache.put_tile`
    的文档：不归档的话，没有完整文字的那些带**每帧都会重读**（A4 实测固定重读 47% 面积）。
    """
    for th, (x0, y0), (tw, thh) in tiles:
        sub = [(str(t), x - x0, y - y0, ww, hh) for (t, x, y, ww, hh) in items
               if x0 <= x and y0 <= y and (x + ww) <= (x0 + tw) and (y + hh) <= (y0 + thh)]
        c.put_tile(th, sub, trusted_empty=trusted_empty)
