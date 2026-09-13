# -*- coding: utf-8 -*-
"""会话头指纹：判断"当前打开的会话是不是 X" —— 投递类能力的**前置条件检查**。

为什么必须有它：投递（`send_text_posted`、发收藏表情、朋友圈操作）**都不会切会话**，
发错会话就是对外可见的事故。而现有判据都不好使：
  · 窗口标题不可靠（实测：标题一直是 `群deepseek`，实际打开的却是文件传输助手）；
  · 会话头文本走 OCR 要截图 + 识别，慢且易错；
  · UIA 树在本机微信 4.1.15.8 上没有物化（只有 2 个节点）。
⇒ 换成**会话头区域的像素指纹**：把会话名那一小块缩成固定尺寸灰度缩略图当指纹，
   同一会话像素几乎一致、不同会话差异巨大；**不需要 OCR、不需要联网、不打扰用户**。

用法：
    fp = capture()                      # 抓当前主窗 → 会话头指纹
    remember("filehelper", fp)          # 记住（第一次确认某会话说开着时）
    ok, info = verify("filehelper")     # 以后每次投递前问一句：还是它吗？
"""
from __future__ import annotations

import json
import logging
import os
import time

from .config import ROOT

log = logging.getLogger("persona-morph")

STORE_PATH = os.path.join(ROOT, "data", "chat_headers.json")
# 会话名区域（**渲染区相对比例**）：避开左侧会话列表（<0.26）、右侧按钮与窗口按钮
PANE_LEFT_REL = 0.26          # 会话列表面板右边界（**兜底**比例；优先用 detect_pane_left 实测）
BAND_PX = (10, 38, 340, 56)   # 文字带：(距面板左边界 dx, y, 宽, 高) —— **固定物理像素**
                              # ⇒ 与窗口尺寸无关（实测：会话列表是"固定像素宽"，不是按窗口比例缩放）
BINS = 64                     # 逐列暗点密度剖面维数
DARK = 165                    # 暗点阈值（会话名是深色字，背景近白）
WHITE = 250                   # 判定"聊天面板底色"（浅色主题下会话列表是浅灰、聊天区是纯白）
DEFAULT_THRESHOLD = 0.90      # 相似度阈值（1.0=完全一致）


# ── 纯函数（可用合成图单测，不需要微信）──────────────────────────────────
def detect_pane_left(img, lo_rel: float = 0.15, hi_rel: float = 0.60, need: int = 24) -> int:
    """探测**聊天面板左沿**（像素）：从 lo 往右找连续 need 列都接近纯白的起点。

    为什么不能只按比例：实测会话列表是**固定像素宽（约 300px）**，窗口一变窄，
    `0.26×宽` 就漂进会话列表里，指纹跟着错（相似度掉到 0.70~0.90）。
    找不到（深色主题/特殊皮肤）就返回 0，由调用方退回比例兜底。
    """
    try:
        g = img.convert("L")
        w, h = g.size
        px = g.load()
        y0, y1 = int(h * 0.25), int(h * 0.75)
        step = max(1, (y1 - y0) // 12)
        run, start = 0, 0
        for x in range(int(w * lo_rel), min(w, int(w * hi_rel))):
            tot = n = 0
            for y in range(y0, y1, step):
                tot += px[x, y]
                n += 1
            bright = tot / max(1, n)
            if bright >= WHITE:
                if run == 0:
                    start = x
                run += 1
                if run >= need:
                    return start
            else:
                run = 0
        return 0
    except Exception:
        return 0


def crop_box(size, pane_left_rel=None, band_px=None, pane_left_px: int = 0) -> tuple:
    """按渲染区尺寸算出会话头文字带的像素框 (l, t, r, b)（夹在图像内）。"""
    w, h = int(size[0]), int(size[1])
    if pane_left_px and pane_left_px > 0:
        pl = int(pane_left_px)
    else:
        pl = int(w * (PANE_LEFT_REL if pane_left_rel is None else pane_left_rel))
    dx, y0, bw, bh = band_px or BAND_PX
    l = max(0, pl + int(dx))
    t = max(0, int(y0))
    r = min(w, l + int(bw))
    b = min(h, t + int(bh))
    return (l, t, max(l + 1, r), max(t + 1, b))


def fingerprint(img, pane_left_rel=None, band_px=None, bins: int = BINS) -> list:
    """会话头区域的**逐列暗点密度剖面**（bins 维，0~255）。

    为什么不用"缩略灰度图"：自测当场证否 —— 文字缩到 24×6 就被抹平，
    不同会话名也能得到 0.997 的相似度（等于没有区分度）。改成"每列有多少暗点"，
    对字形/字数极敏感、对轻微纵向偏移不敏感，维数还固定（与窗口尺寸无关）。
    """
    try:
        if not hasattr(img, "size"):
            return []
        box = crop_box(img.size, pane_left_rel, band_px)
        g = img.convert("L").crop(box)
        w, h = g.size
        if w < bins or h < 4:
            return []
        px = g.load()
        step = max(1, h // 24)
        raw = []
        for i in range(bins):
            x0 = int(i * w / bins)
            x1 = max(x0 + 1, int((i + 1) * w / bins))
            dark = tot = 0
            for x in range(x0, x1):
                for y in range(0, h, step):
                    tot += 1
                    if px[x, y] < DARK:
                        dark += 1
            raw.append(dark / float(max(1, tot)))
        # 按"最强列"归一化：不这么做的话暗点占比只有 20~50/255，
        # 不同会话名的差异会被整体压平（实测相似度高达 0.976＝等于没区分度）。
        mx = max(raw) if raw else 0.0
        if mx <= 0.0:
            return []                     # 一点墨都没有 ⇒ 视为无效指纹（不许当"匹配"）
        return [int(round(255.0 * v / mx)) for v in raw]
    except Exception:
        return []


def similarity(a, b) -> float:
    """1 - 归一化平均绝对差。两边长度不等或为空 ⇒ 0.0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    diff = sum(abs(int(x) - int(y)) for x, y in zip(a, b)) / float(len(a))
    return max(0.0, 1.0 - diff / 255.0)


def match(a, b, threshold: float = DEFAULT_THRESHOLD) -> bool:
    return similarity(a, b) >= float(threshold)


# ── 落盘（原子写）──────────────────────────────────────────────────────
def load(path: str = None) -> dict:
    p = path or STORE_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save(data: dict, path: str = None) -> str:
    p = path or STORE_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return p


def remember(chat_id: str, fp, note: str = "", path: str = None) -> dict:
    """记住某会话的会话头指纹（覆盖式）。"""
    data = load(path)
    data[str(chat_id)] = {"fp": list(fp or []), "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                          "note": note or ""}
    save(data, path)
    return data


def reference(chat_id: str, path: str = None) -> list:
    return list((load(path).get(str(chat_id)) or {}).get("fp") or [])


# ── 与微信窗口对接 ─────────────────────────────────────────────────────
def capture(gui=None, render=None):
    """抓当前主窗渲染区 → 会话头指纹（拿不到返回 []）。"""
    try:
        from PIL import ImageGrab
        if render is None:
            gui = gui or _gui()
            render = gui.render_rect or gui._update_render_rect()
        img = ImageGrab.grab((int(render[0]), int(render[1]), int(render[2]), int(render[3])))
        return fingerprint(img)
    except Exception as e:
        log.warning("会话头指纹抓取失败：%s", e)
        return []


def _gui():
    from .wechat import WeChatAdapter
    return WeChatAdapter()._get_gui()


def verify(chat_id: str, gui=None, render=None, threshold: float = DEFAULT_THRESHOLD,
           path: str = None) -> tuple:
    """当前打开的会话是 chat_id 吗？返回 (ok, 说明)。没存过参照 ⇒ (False, 说明)。"""
    ref = reference(chat_id, path)
    if not ref:
        return False, "没有 %s 的会话头参照（先用 remember() 存一次）" % chat_id
    cur = capture(gui=gui, render=render)
    if not cur:
        return False, "这次没抓到会话头（窗口不可见？）"
    s = similarity(ref, cur)
    return (s >= threshold), "相似度 %.3f（阈值 %.2f）" % (s, threshold)


def seed_from_main(chat_id: str, note: str = "seeded", path: str = None) -> tuple:
    """把**当前打开会话**的会话头存成 chat_id 的参照（只在"确认过当前就是它"时调用）。"""
    fp = capture()
    if not fp:
        return False, "抓不到会话头"
    remember(chat_id, fp, note=note, path=path)
    return True, "已记住 %s 的会话头（%d 维）" % (chat_id, len(fp))
