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
import math
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


def detect_pane_left_alt(img, rail_max_rel: float = 0.12, list_w: int = 300) -> int:
    """**结构锚**版的面板左沿：竖导航栏右沿 + 会话列表**固定像素宽**（≈300px），认不出给 0。

    为什么另开一条（2026-09-21 真机实测，代价＝切会话整条第③路失效）：老口径
    （从 `0.15w` 起找连续 24 列"近纯白"）在**聊天区左列被消息气泡占满**时，会话列表右沿那里
    根本找不到白列 ⇒ 一路扫到气泡右边，实测报 **660**（真值 384）⇒ 会话列裁剪框跟着偏进聊天区
    （`left-240 .. left-6`）⇒ `find_row_info` 把聊天气泡当会话行读 ⇒ "列表里没看到「×××」那一行"。

    微信的会话列表是**固定像素宽、不随窗口变**（老口径的注释里也写了这一条），
    所以"竖栏右沿 + 固定宽"是更稳的结构锚。竖栏是深色底 ⇒ 从 0 往右第一个"不再深色"的列就是栏右沿。
    认不出（浅色主题/皮肤）返回 0，由调用方忽略本条（fail-safe）。
    """
    try:
        g = img.convert("L")
        w, h = g.size
        px = g.load()
        y0, y1 = int(h * 0.2), int(h * 0.8)
        step = max(1, (y1 - y0) // 20)
        rail_right = 0
        for x in range(0, max(8, int(w * rail_max_rel))):
            vals = [px[x, y] for y in range(y0, y1, step)]
            if not vals:
                continue
            mean = sum(vals) / len(vals)
            if mean >= 150:                     # 不再是深色底 ⇒ 竖栏在这一列结束了
                rail_right = x
                break
        if not rail_right:
            return 0
        pl = int(rail_right + int(list_w))
        if pl <= 0 or pl >= w:
            return 0
        return pl
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


#: **第二条件**：暗点分布的**形状**一致性（余弦）。见 `pattern_score` 的说明。
DEFAULT_COSINE = 0.95


def pattern_score(a, b) -> float:
    """两边归一化后的**余弦** —— 只看"暗点分布的形状"，不看整体幅度。

    ⛔ 为什么必须加第二条件（2026-09-21 由网友 v0919 的真机 `chat_headers.json` 定案）：
      `similarity` 是 `1 − 平均绝对差/255`，而会话头指纹绝大多数维是 0、只有中间十几维有值
      ⇒ 值域被压在 0.85~1.0 这条窄带里 ⇒ **两个不同的短群名**能拿到很高的分。真机实测
      （他的两个群「KC」/「测试」，1160x900 与 1562x1324 两档）：
        · `similarity` = **0.9480 ≥ 0.90** ⇒ 判"同一个会话"（**假阳性**）；
        · 同一会话 vs 自己 = 1.0000；跟 filehelper = 0.8783 ⇒ 0.90 这个阈值**没有安全间隔**。
      而形状指标把这三者拉开：同一会话 **1.0000**、这两个短名 **0.8635**、filehelper **0.6471**
      ⇒ 两个条件同时要求（`≥0.90` 且 `≥0.95`）就有 0.086 的安全余量，且同一会话仍然过。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return max(0.0, sum(float(x) * float(y) for x, y in zip(a, b)) / (na * nb))


def match(a, b, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """两个条件都过才算匹配（幅度相似 **且** 形状一致）。"""
    return (similarity(a, b) >= float(threshold)) and (pattern_score(a, b) >= DEFAULT_COSINE)


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
    """记住某会话**某个窗口尺寸下**的会话头指纹（覆盖式）。**空白图一律不记**（见 `is_blank`）。

    ⛔ 2026-09-21 加**退化指纹**这一关（`degenerate_reason`）：网友真机库里那条
    `[0]*63 + [255]` 就是被这里放进来的，进库以后那个尺寸档长期误判 ⇒ 学之前在门口拦掉。
    """
    data = load(path)
    if is_blank(fp):
        log.warning("拒绝记住空白会话头指纹：%s（尺寸 %s）——大概率是窗口最小化/抓不到画面",
                    chat_id, size)
        return data
    _deg = degenerate_reason(fp)
    if _deg:
        log.warning("拒绝记住**退化**的会话头指纹：%s（尺寸 %s）—— %s（这条指纹没抓到名字那条带子；"
                    "记进去会让这个尺寸档长期误判）", chat_id, size, _deg)
        return data
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


def _crop_render(img, main, render):
    """从"整个顶层窗"的图里裁出渲染区（顶层窗那条路要减窗口原点）。"""
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(int(main), ctypes.byref(r))
        l = int(render[0]) - int(r.left)
        t = int(render[1]) - int(r.top)
        sub = img.crop((max(0, l), max(0, t),
                        min(img.size[0], l + int(render[2] - render[0])),
                        min(img.size[1], t + int(render[3] - render[1]))))
        return sub if (sub.size[0] > 60 and sub.size[1] > 40) else None
    except Exception:
        return None


def _frame_ok(img) -> bool:
    """判"这幅图是不是真画面"：既不能近乎全黑，也不能是**纯色**（黑屏/白屏/没画完的帧）。

    实测依据（2026-09-13）：好帧 mean≈198~236、std≈75；PrintWindow 失败帧 mean 0~6.6、std≈0。
    只看 mean 会把"半黑半白/没画完"的帧放进来，所以**同时要求 std > 12**。
    """
    try:
        from PIL import ImageStat
        st = ImageStat.Stat(img.convert("L"))
        return st.mean[0] > 25 and st.stddev[0] > 12
    except Exception:
        return False


def _mono(img, span: float = 8.0) -> bool:
    """整幅近**纯色**（全白 / 全黑 / 没画完）⇒ True。

    与 `_frame_ok` 的分工：`_frame_ok` 是"可用帧"的**严格**判据（还要求不太暗 + std>12）；
    `_mono` 只判"有没有内容"，用在**兜底帧**的准入上（严格判据不过、但不许是纯色）。

    为什么要补它（2026-09-15 跨机实测抓到的真缺陷）：微信**最小化**时 `PrintWindow` 会返回
    **纯白帧**（实测 mean=255 / std=0，指纹 [255,255,255,…]），而 `grab_render` 原来把
    "质量可疑但有内容"的帧无条件留作兜底 ⇒ 上层拿到全白图、报告还写"会话头指纹：可抓"。
    """
    try:
        from PIL import ImageStat
        st = ImageStat.Stat(img.convert("L"))
        lo, hi = st.extrema[0]
        return st.stddev[0] < 2.0 or (hi - lo) < span
    except Exception:
        return True


def is_blank(fp) -> bool:
    """指纹是不是"空白图"、或者**里面其实没有字**的帧。

    实测（2026-09-15 跨机同一台机器两次跑的对照）：正常画面 = `[0, 14, 83, 185, 97, 153]`
    （极差 191）；微信最小化时 = 64 维全 `255`（极差 0）⇒ 判据一＝极差 < 8。

    ⛔ 2026-09-17 **补判据二：一个暗列都没有 ＝ 那条带子里没有字**。起因＝用户报「机器人有时不回话」，
    真因是**自动学习把一帧"标题带里一个字都没有"的画面学了进去**（实测那版指纹
    `min=139 / max=255`，极差 116 ⇒ 老的"极差<8"判据照样放行），此后该尺寸档**永远判"不匹配"**
    ⇒ **打好的回复被整条丢掉**。实测好帧 `min=0`（标题文字必然产生全暗列）。
    ⇒ 判据二：**最暗列 > 110 就当作没字**（0 与 139 之间取的安全分界）。
    """
    try:
        v = [float(x) for x in fp]
    except Exception:
        return True
    if not v:
        return True
    if (max(v) - min(v)) < 8:          # 全平：最小化 / 抓不到画面
        return True
    return min(v) > 110                 # 有起伏，但整条带子里一个暗列都没有 ⇒ 没有字


def degenerate_reason(fp, bins: int = BINS) -> str:
    """指纹是不是**退化的**（不是"没字"，而是"根本没抓到名字那条带"）——返回人话原因，空串＝正常。

    ⛔ 真机证据（2026-09-21，网友 v0919 的 `data/chat_headers.json`）：
    `49615732107@chatroom` 的 `1716x900` 参照 ＝ `[0]*63 + [255]`：**63 维是"没有墨"、只有最右边一维满墨**
    （本文件的指纹是"逐列暗点密度"，`0`＝这一列没有字、`255`＝这一列满墨；正常一帧＝中间十几列有墨）。
    同一会话其它四个尺寸（1562x1324 / 1160x900 / 1107x1324 / 1107x900）**逐字节完全相同且正常**
    ⇒ 那一条是在画面异常时学到的。`is_blank` 抓不住它（极差 255、最暗列 0 都过），
    而它一旦进了库，该尺寸档就**长期误判**（好帧跟它比只有 0.8713 < 0.90）。
    判据三条（都取自"名字一定从这条带子的左边开始、且只占其中一段"）：
      ① **有墨的列**（≥110）少于 2 个  ⇒ 那不是一行字；
      ② 有墨的列全在右半段 ⇒ 名字不可能从那里开始；
      ③ 整条带子几乎每一列都是墨（≥ 总数−8）⇒ 没抓到名字那条带（黑屏/整块背景）。
    """
    try:
        v = [int(x) for x in fp]
    except Exception:
        return "指纹不是整数序列"
    if not v:
        return "空指纹"
    ink = [i for i, x in enumerate(v) if x >= 110]
    if len(ink) < 2:
        return "有墨的列只有 %d 个（不是一行字；位置 %s）" % (len(ink), ink[:5])
    if min(ink) >= max(1, len(v) // 2):
        return "有墨的列全在右半边（最左墨列 第%d 维）⇒ 名字不可能从那里开始" % min(ink)
    if len(ink) >= max(2, len(v) - 8):
        return "几乎每一列都有墨（%d/%d）⇒ 没抓到名字那条带（黑屏或整块背景）" % (len(ink), len(v))
    return ""


def _window_belongs_to(hwnd, allow) -> bool:
    """hwnd 是不是 allow 集合里的窗口**或它的子窗**（`WindowFromPoint` 常命中深层子窗）。"""
    try:
        import win32gui
        h = int(hwnd)
        for _ in range(12):
            if h in allow:
                return True
            p = int(win32gui.GetParent(h) or 0)
            if not p or p == h:
                break
            h = p
    except Exception:
        return False
    return False


def _occlusion_verdict(seen, main_pid: int) -> bool:
    """纯函数：`seen` = [(命中窗口的 pid, 这个窗口是不是"我们允许的")] ⇒ 渲染区算不算被遮挡。

    口径（2026-09-18 改）：**不是我们允许的窗口**都算遮挡——既含别的进程，也含**同进程的兄弟窗**
    （搜索窗 / 表情面板 / 朋友圈编辑窗）。旧口径只看 pid，兄弟窗盖上来会被判"没遮挡"。
    """
    hits = [s for s in seen if int(s[0])]
    if not hits:
        return False
    bad = sum(1 for _pid, ours in hits if not ours)
    return bad >= max(1, len(hits) // 4)


# ⚡ 2026-09-18 晚（**真缺陷，有现场图**）：本次抓图"允许盖在渲染区上"的自家窗口集合
#   （＝主窗 + 渲染子窗；由 `grab_render` 每次填）。
#   取证：`wechatauto_logs\fail\20260918-220433_search_entry\shot.png` —— 这张号称"主窗渲染区"的帧，
#   画面其实是**微信自己的「搜索聊天记录」独立窗**（带标题栏，搜索框里还留着上次查询「E」）。
#   成因：PrintWindow 连失 12 枪 ⇒ 退回 `ImageGrab`；而旧 `_region_occluded` 只把**别的进程**算遮挡，
#   同进程的兄弟窗（搜索窗 / 表情面板）盖上来时判"没被遮挡" ⇒ 抓了兄弟窗的像素，下游全用错画面
#   （找不到搜索入口 → 从**文字碎片**里挑到 9×10 → 一枪点到会话行）。
_OCCLUDE_ALLOW = set()


def _region_occluded(render, main_pid: int, samples: int = 3) -> bool:
    """渲染区是不是被**别的窗口**盖着（盖着时不许退回抓屏——那读到的是别人家的像素）。

    实测（2026-09-13）：用户的浏览器盖在微信上时，退回 `ImageGrab` 会拿到 Chrome 的画面，
    下游 OCR 于是"读到"浏览器内容（会话列表 0~1 行、搜索框区域被污染），**全程不报错**。
    2026-09-18 扩到**同进程兄弟窗**（见 `_OCCLUDE_ALLOW` 的现场取证）：没设允许集时退回旧口径（只比 pid）。
    """
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        x0, y0, x1, y1 = [int(v) for v in render]
        if x1 - x0 < 8 or y1 - y0 < 8:
            return False
        seen = []
        for i in range(samples):
            for j in range(samples):
                x = x0 + (x1 - x0) * (2 * i + 1) // (2 * samples)
                y = y0 + (y1 - y0) * (2 * j + 1) // (2 * samples)
                h = u32.WindowFromPoint(wintypes.POINT(x, y))
                pid = wintypes.DWORD()
                u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if not pid.value:
                    continue
                if _OCCLUDE_ALLOW:
                    ours = _window_belongs_to(h, _OCCLUDE_ALLOW)
                else:
                    ours = int(pid.value) == int(main_pid)
                seen.append((int(pid.value), bool(ours)))
        return _occlusion_verdict(seen, main_pid)
    except Exception:
        return False


def grab_render(gui=None, render=None, tries: int = 12):
    """取"渲染区"图像：**优先 PrintWindow（遮挡也能拿）**，失败才退回抓屏（且**遮挡时不退**）。

    2026-09-13 实测四条（"抓图不可靠"的真因，别再回退）：
      ① **渲染子窗 `MMUIRenderSubWindowHW` 整幅就是渲染区**（实测 1139×890 ＝ render_rect 的尺寸）
         ⇒ 对它 PrintWindow 得到的图**坐标＝渲染区相对**，省掉"按窗口原点裁剪"的换算。
      ② **PrintWindow 对微信是"冷启动会连失几枪"**：实测同一窗口，连打 4~6 枪全是 None，之后
         连中两枪（mean 236）。⇒ 必须**多试几枪**（`tries=12`，等价约 1 秒预算），一枪不成不能退。
      ③ **抓到的帧要看质量**：`_frame_ok` 同时要求"不太暗 + 有内容（std>12）"——只看 mean 会把
         "没画完的纯色帧"当成功。
      ④ **退回抓屏必须带遮挡校验**：`WindowFromPoint` 采样发现渲染区被别的进程盖着 ⇒ **不退回抓屏**，
         宁可返回 None 让上层说"抓不到画面"（实测过：退回后读到的是用户的浏览器画面，全程不报错）。
    """
    render = render or (gui.render_rect if gui else None)
    main = int(getattr(gui, "main_hwnd", 0) or 0) if gui else 0
    best = None
    if main:
        cands = []
        try:                                   # 渲染子窗优先：省换算、1:1 对齐渲染区
            from . import input_backend as _ib
            ch = _ib.find_render_child(main)
            if ch:
                cands.append(int(ch))
        except Exception:
            pass
        cands.append(main)
        for _round in range(max(1, int(tries))):
            for hwnd in cands:
                try:
                    img = _print_window(hwnd)
                except Exception:
                    img = None
                if img is None or _too_dark(img):
                    continue
                if not _frame_ok(img):
                    # 兜底帧也必须是"有内容"的：纯色帧（最小化时的全白 / 没画完的黑）一律不留，
                    # 否则上层会把它当"抓到了"（2026-09-15 跨机实测的真缺陷，见 _mono 注释）。
                    if best is None and not _mono(img):
                        best = img            # 留一帧"质量可疑但有内容"的兜底
                    continue
                if hwnd != main:
                    return img                # 渲染子窗的图就是渲染区，零换算
                sub = _crop_render(img, main, render) if render else img
                if sub is not None and _frame_ok(sub):
                    return sub
            time.sleep(0.08)
        # ⚡ 2026-09-18 晚：把"允许盖在渲染区上的自家窗口"告诉遮挡校验（否则同进程的搜索窗/表情面板
        #    盖上来会被判"没遮挡" ⇒ 退回抓屏抓到的是**它们**的画面，见 `_OCCLUDE_ALLOW` 的现场取证）。
        _OCCLUDE_ALLOW.clear()
        _OCCLUDE_ALLOW.update(int(c) for c in cands)
    if best is not None:
        return _crop_render(best, main, render) if (render and main) else best
    try:
        from PIL import ImageGrab
        if not render:
            return None
        main_pid = 0
        if main:
            import ctypes
            from ctypes import wintypes
            _p = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(main, ctypes.byref(_p))
            main_pid = int(_p.value)
        if main_pid and _region_occluded(render, main_pid):
            log.warning("渲染区被别的窗口遮挡（PrintWindow 也没拿到帧）⇒ 不退回抓屏："
                        "抓屏会读到别人家的像素，返回 None 更诚实")
            return None
        return ImageGrab.grab((int(render[0]), int(render[1]), int(render[2]), int(render[3])))
    except Exception as e:
        log.warning("渲染区抓取失败：%s", e)
        return None


def shot_window(hwnd, tries: int = 8, sleep_s: float = 0.12):
    """抓**任意独立顶层窗**自己的画面（浮层/面板/子窗用；它们不在主窗渲染区里）。

    为什么要重试：实测微信的 `Qt51514QWindowToolSaveBits` 浮层（表情面板 / 搜索浮层）**第一枪常常
    返回 0**，第二三枪才成功（本机实测 0/2 → 1/2 成功）⇒ 单次调用会得到 None 让人误判"抓不到"。
    """
    for _i in range(max(1, int(tries))):
        for fl in (2, 0):
            try:
                im = _print_window(int(hwnd), fl)
            except Exception:
                im = None
            if im is None:
                continue
            if _frame_ok(im):
                return im
        time.sleep(sleep_s)
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
    if is_blank(ref):
        # ⛔ 2026-09-17：**参照自身是学歪的**（那一帧的标题带里没有字）⇒ 按 `no_ref` 放行。
        #   否则这个尺寸档会**永远**判 mismatch，把打好的每一条回复都丢掉
        #   （用户报「机器人有时不回话」的真因；日志长相＝"投递切会话后发送失败：会话头不匹配…
        #   相似度 0.530 < 0.90"）。这条只是"不拦"，不是"放行错会话"——发送前另有
        #   切会话 / OCR 名字确认两道独立证据（见 `wechat.send_text`）。
        log.warning("该尺寸（%s）的会话头参照本身学歪了（没有字）⇒ 按 no_ref 放行，不再拦发：%s",
                    key, chat_id)
        return {"status": "no_ref", "sim": 0.0, "size": key,
                "note": "参照学歪了（那条带子里没有字）⇒ 本次不拦，成功发送后会重学"}
    _deg = degenerate_reason(ref)
    if _deg:
        # ⛔ 2026-09-21：**退化参照**也按 no_ref 放行（同"学歪了"的道理）——
        #   网友真机库里那条 `[0]*63+[255]` 就属于这一类，拿它比会长期误判（好帧只有 0.8713）。
        log.warning("该尺寸（%s）的会话头参照是**退化**的（%s）⇒ 按 no_ref 放行、不再拦发：%s",
                    key, _deg, chat_id)
        return {"status": "no_ref", "sim": 0.0, "size": key,
                "note": "参照退化（%s）⇒ 本次不拦，成功发送后会重学" % _deg}
    sim = similarity(ref, fp)
    cos = pattern_score(ref, fp)
    if sim >= threshold and cos >= DEFAULT_COSINE:
        return {"status": "ok", "sim": sim, "cos": cos, "size": key,
                "note": "相似度 %.3f · 形状 %.3f" % (sim, cos)}
    return {"status": "mismatch", "sim": sim, "cos": cos, "size": key,
            "note": ("相似度 %.3f < %.2f" % (sim, threshold)) if sim < threshold
                    else ("形状一致性 %.3f < %.2f（分数像但暗点分布不像 ⇒ 多半是**另一个短名会话**）"
                          % (cos, DEFAULT_COSINE))}


def _gui():
    from .wechat import WeChatAdapter
    return WeChatAdapter()._get_gui()


def verify(chat_id: str, gui=None, render=None, threshold: float = DEFAULT_THRESHOLD,
           path: str = None) -> tuple:
    """当前打开的会话是 chat_id 吗？返回 (ok, 说明)。没存过参照 ⇒ (False, 说明)。"""
    ref = reference(chat_id, path)
    if not ref:
        return False, "没有 %s 的会话头参照（先用 remember() 存一次）" % chat_id
    _deg = degenerate_reason(ref)
    if _deg:
        return False, "参照已失效（%s）⇒ 请重新学一次（下次发送成功会自动重学）" % _deg
    cur = capture(gui=gui, render=render)
    if not cur:
        return False, "这次没抓到会话头（窗口不可见？）"
    s = similarity(ref, cur)
    c = pattern_score(ref, cur)
    ok = (s >= threshold) and (c >= DEFAULT_COSINE)
    return ok, "相似度 %.3f（阈值 %.2f）· 形状 %.3f（阈值 %.2f）" % (s, threshold, c, DEFAULT_COSINE)


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
