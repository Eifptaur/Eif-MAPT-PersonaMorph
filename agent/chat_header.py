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

    三条踩出来的规矩：
      ① 不用"缩略灰度图"（文字缩到 24×6 被抹平，不同会话名也能得 0.976 相似度）；
      ② **按最强列归一化**（不归一化时暗点占比只有 20~50/255，差异被整体压平）；
      ③ **锚点要实测面板左沿**（`detect_pane_left`）——会话列表是固定像素宽、不按窗口比例，
         只按比例放带会在换尺寸时漂进会话列表（实测相似度掉到 0.70~0.90）。
    """
    try:
        if not hasattr(img, "size"):
            return []
        pl = 0
        if pane_left_rel is None and band_px is None:
            pl = detect_pane_left(img)
        box = crop_box(img.size, pane_left_rel, band_px, pane_left_px=pl)
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


def size_key(img_or_size) -> str:
    """指纹的"尺寸键"：不同窗口尺寸下微信会重排表头，指纹不可跨尺寸复用。"""
    try:
        s = img_or_size.size if hasattr(img_or_size, "size") else img_or_size
        return "%dx%d" % (int(s[0]), int(s[1]))
    except Exception:
        return ""


def remember(chat_id: str, fp, note: str = "", path: str = None, size: str = "*") -> dict:
    """记住某会话**某个窗口尺寸下**的会话头指纹（覆盖式）。"""
    data = load(path)
    ent = data.get(str(chat_id)) or {}
    sizes = ent.get("sizes") or {}
    if ent.get("fp") and "*" not in sizes:          # 兼容旧格式（无尺寸键）
        sizes["*"] = {"fp": list(ent.get("fp") or []), "when": ent.get("when", "")}
    sizes[str(size or "*")] = {"fp": list(fp or []), "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "note": note or ""}
    data[str(chat_id)] = {"sizes": sizes, "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    save(data, path)
    return data


def reference(chat_id: str, path: str = None, size: str = None, strict: bool = False) -> list:
    """取参照。`strict=True` ⇒ **只认该尺寸**的记录（校验用，避免拿别的尺寸的指纹误判）。

    为什么校验必须 strict：实测指纹**不可跨窗口尺寸复用**（微信按尺寸重排表头，
    同会话在 1000×760 下相似度只有 0.65）⇒ 拿错尺寸的参照去比会**误拦正常发送**。
    """
    ent = (load(path).get(str(chat_id)) or {})
    sizes = ent.get("sizes") or {}
    if not sizes and ent.get("fp"):
        return [] if strict else list(ent.get("fp") or [])
    if size and (sizes.get(str(size)) or {}).get("fp"):
        return list(sizes[str(size)]["fp"])
    if strict:
        return []
    if (sizes.get("*") or {}).get("fp"):
        return list(sizes["*"]["fp"])
    for v in sizes.values():
        if (v or {}).get("fp"):
            return list(v["fp"])
    return []


def ref_sizes(chat_id: str, path: str = None) -> list:
    return sorted((load(path).get(str(chat_id)) or {}).get("sizes", {}).keys())


# ── 与微信窗口对接 ─────────────────────────────────────────────────────
def _print_window(hwnd, flags: int = 2):
    """`PrintWindow(PW_RENDERFULLCONTENT)` 取窗口**自身画面**（遮挡也拿得到）。失败返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes
        from PIL import Image
        u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
        r = wintypes.RECT()
        u32.GetWindowRect(int(hwnd), ctypes.byref(r))
        w, h = int(r.right - r.left), int(r.bottom - r.top)
        if w <= 0 or h <= 0:
            return None
        hdc = u32.GetWindowDC(int(hwnd))
        mdc = g32.CreateCompatibleDC(hdc)
        bmp = g32.CreateCompatibleBitmap(hdc, w, h)
        g32.SelectObject(mdc, bmp)
        ok = u32.PrintWindow(int(hwnd), mdc, int(flags))
        if not ok:
            g32.DeleteObject(bmp); g32.DeleteDC(mdc); u32.ReleaseDC(int(hwnd), hdc)
            return None

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                        ("biClrImportant", ctypes.c_uint32)]
        buf = ctypes.create_string_buffer(w * h * 4)
        bi = BIH()
        bi.biSize = ctypes.sizeof(BIH)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        g32.DeleteObject(bmp); g32.DeleteDC(mdc); u32.ReleaseDC(int(hwnd), hdc)
        return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    except Exception:
        return None


def _too_dark(img) -> bool:
    """整幅近乎全黑 ⇒ PrintWindow 没渲染出内容（隐藏窗口常见），别拿它当指纹。"""
    try:
        from PIL import ImageStat
        return ImageStat.Stat(img.convert("L")).mean[0] < 6
    except Exception:
        return False


def grab_render(gui=None, render=None):
    """取"渲染区"图像：**优先 PrintWindow（遮挡/后台也能拿）**，失败才退回抓屏。"""
    render = render or (gui.render_rect if gui else None)
    try:
        main = int(getattr(gui, "main_hwnd", 0) or 0) if gui else 0
        if main and render:
            import ctypes
            from ctypes import wintypes
            img = _print_window(main)
            if img is not None and not _too_dark(img):
                r = wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(main, ctypes.byref(r))
                l = int(render[0]) - int(r.left)
                t = int(render[1]) - int(r.top)
                sub = img.crop((max(0, l), max(0, t),
                                min(img.size[0], l + int(render[2] - render[0])),
                                min(img.size[1], t + int(render[3] - render[1]))))
                if sub.size[0] > 60 and sub.size[1] > 40:
                    return sub
    except Exception:
        pass
    try:
        from PIL import ImageGrab
        return ImageGrab.grab((int(render[0]), int(render[1]), int(render[2]), int(render[3])))
    except Exception as e:
        log.warning("渲染区抓取失败：%s", e)
        return None


def capture_image(gui=None, render=None):
    """抓"渲染区"图像（PrintWindow 优先，失败退回抓屏）。"""
    if render is None:
        gui = gui or _gui()
        render = gui.render_rect or gui._update_render_rect()
    return grab_render(gui=gui, render=render)


def capture(gui=None, render=None):
    """抓当前主窗渲染区 → 会话头指纹（拿不到返回 []）。"""
    img = capture_image(gui=gui, render=render)
    return fingerprint(img) if img is not None else []


def check(chat_id: str, gui=None, path: str = None,
          threshold: float = DEFAULT_THRESHOLD) -> dict:
    """**生产用入口**：返回 {status, sim, size, note}，status ∈ ok/mismatch/no_ref/no_capture。

    为什么需要三态而不是布尔：实测**指纹不可跨窗口尺寸复用**（微信会按尺寸重排表头：
    同一会话在 1000×760 / 1400×1000 下相似度掉到 0.65）。所以设计成：
      · 当前尺寸**有**参照且不匹配 ⇒ `mismatch`（**拦**，这是"用户在同一个布局里切了会话"的常见风险）；
      · 当前尺寸**没有**参照（刚改过窗口大小/第一次用）⇒ `no_ref`（**不拦**，但必须留痕，
        并在一次 DB 回读成功的发送之后**自动学一条该尺寸的参照**——见 remember(size=…)）。
    """
    img = capture_image(gui=gui)
    if img is None:
        return {"status": "no_capture", "sim": 0.0, "size": "", "note": "抓不到渲染区（窗口不可见/权限不足）"}
    key = size_key(img)
    fp = fingerprint(img)
    if not fp:
        return {"status": "no_capture", "sim": 0.0, "size": key, "note": "抓到的画面里没有会话头（特殊皮肤/深色主题？）"}
    ref = reference(chat_id, path, size=key, strict=True)
    if not ref:
        return {"status": "no_ref", "sim": 0.0, "size": key,
                "note": "该尺寸（%s）没有参照；本次不拦，成功发送后会自动补一条" % key}
    sim = similarity(ref, fp)
    if sim >= threshold:
        return {"status": "ok", "sim": sim, "size": key, "note": "相似度 %.3f" % sim}
    return {"status": "mismatch", "sim": sim, "size": key,
            "note": "相似度 %.3f < %.2f（当前打开的很可能不是 %s）" % (sim, threshold, chat_id)}


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
    """把**当前打开会话**的会话头存成 chat_id 的参照（按当前窗口尺寸建档）。"""
    img = capture_image()
    if img is None:
        return False, "抓不到渲染区"
    fp = fingerprint(img)
    if not fp:
        return False, "抓到的画面里没有会话头"
    remember(chat_id, fp, note=note, path=path, size=size_key(img))
    return True, "已记住 %s 的会话头（%s，%d 维）" % (chat_id, size_key(img), len(fp))
