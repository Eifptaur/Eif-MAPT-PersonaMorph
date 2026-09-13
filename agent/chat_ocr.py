# -*- coding: utf-8 -*-
"""会话头 OCR（Windows 内置 WinRT OCR，离线、零下载）：回答"当前打开的会话到底是谁"。

为什么需要它（2026-09-13）：
  · 投递不切会话 ⇒ 发送前必须确认"当前会话＝目标会话"；
  · `agent/chat_header.py` 的**指纹**能判"像不像参照"，但**没有参照时它答不出"现在是谁"**；
  · 驱动库的只读判据 `gui._chat_is_open()` 实测**不稳定**（同一次调用里 True、紧接着外面查是 False），
    不能拿它授权写动作。
⇒ 用 WinRT OCR 直接读标题文字做"**按名字确认**"，与指纹互补：
    指纹＝快（不用 OCR）但需要参照；OCR 名字＝慢 ~1s，但没有参照也能用，而且是**独立信号**。

只读、无副作用：任何异常都返回空串/False，绝不抛给调用方。
"""
from __future__ import annotations

import re

from . import chat_header as ch

_norm_re = re.compile(r"[\s\u3000·,，.。．:：;；!！?？\"'“”‘’()（）\[\]【】<>《》\-—_/\\|]+")
_count_re = re.compile(r"[（(]\s*\d+\s*[)）]\s*$")


def available() -> bool:
    """本机 WinRT OCR 能不能用（winsdk + 用户语言包）。"""
    try:
        from winsdk.windows.media.ocr import OcrEngine
        return OcrEngine.try_create_from_user_profile_languages() is not None
    except Exception:
        return False


def recognize(img) -> list:
    """对 PIL 图像跑 OCR，返回 [(text, x, y, w, h)]；不可用/失败返回 []。"""
    try:
        from wechatauto.guia import ScreenOCR
        return list(ScreenOCR.recognize(img) or [])
    except Exception:
        return []


def header_box(img) -> tuple:
    """会话头文字带的像素矩形 (x0,y0,x1,y1)：优先用实测聊天面板左沿，退比例兜底。"""
    w, h = img.size
    left = 0
    try:
        left = ch.detect_pane_left(img) or 0
    except Exception:
        left = 0
    if not left:
        left = int(w * ch.PANE_LEFT_REL)
    dx, dy, bw, bh = ch.BAND_PX
    x0 = max(0, left + dx)
    x1 = min(w, x0 + bw)
    y0 = max(0, dy)
    y1 = min(h, y0 + bh)
    return (x0, y0, x1, y1)


def header_text(img=None, gui=None, zoom: int = 2) -> str:
    """OCR 会话头，返回识别到的文字（读不到返回 ""）。zoom＝放大倍数（小字放大后识别率更高）。"""
    try:
        if img is None:
            img = ch.capture_image(gui=gui)
        if img is None:
            return ""
        box = header_box(img)
        if box[2] - box[0] < 8 or box[3] - box[1] < 6:
            return ""
        crop = img.crop(box)
        if zoom and zoom > 1:
            crop = crop.resize((crop.width * zoom, crop.height * zoom))
        items = recognize(crop)
        return "".join(str(i[0]) for i in items).strip()
    except Exception:
        return ""


_time_re = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}")
_date_re = re.compile(r"\d{1,2}\s*[/月]\s*\d{1,2}\s*日?")
_ellip_re = re.compile(r"[.．…]{2,}")


def clean(text: str) -> str:
    """清掉 OCR 常混进来的时间/日期/省略号（会话行是「名字 + 预览 + 时间」挤在一起）。"""
    if not text:
        return ""
    t = str(text)
    t = _time_re.sub("", t)
    t = _date_re.sub("", t)
    t = _ellip_re.sub("", t)
    return t.strip()


def norm(text: str) -> str:
    """归一化：去掉时间/日期/空白/标点/常见装饰，便于比对（保留中文与字母数字）。"""
    t = clean(text)
    if not t:
        return ""
    t = _count_re.sub("", t)          # 先去掉群名后的成员数「（8）」——必须在去括号之前做
    t = _norm_re.sub("", t)
    return t.lower()


def matches(text: str, name: str) -> bool:
    """OCR 文本与目标会话名是否算同一个（互相包含即可，容忍 OCR 漏字/多字/截断省略号）。"""
    a, b = norm(text), norm(name)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(b) >= 2 and (b in a or a in b) and min(len(a), len(b)) >= 2:
        return True
    return False


# ── 会话列表：读名字 + 找绿色高亮行（两个独立信号 ⇒ 可用于"当前会话是谁"的可信判据）──
# ⚠️ 实测（2026-09-13）：微信 4.1.15.8 的**会话标题文字**是浅灰细字，WinRT OCR 整幅都读不出来
#    （同一张图里会话列表的名字/预览/时间戳都能读出来）⇒ 不能靠"标题"确认，改靠**会话列表**
#    ＋**绿色高亮行**：OCR 名字能读、高亮是可测的像素信号，两者对得上才认。
GREEN = (81, 167, 116)          # 实测（2026-09-13 本机 4.1.15.8）：活动会话行背景色
GREEN_TOL = 34                  # 颜色容差（每通道）——库里写的 (21,172,112) 在本机**量不到**，
                                # 实测绿底是 (81,167,116)，容差 26 时判定为 0 ⇒ 这就是"找不到高亮行"的真因
ROW_PITCH = 64                  # 会话行高（名字行 + 预览行 ≈ 64px，实测 144→241→338…）


def list_rows(img, zoom: int = 2) -> list:
    """OCR 会话列表并按行聚合，返回 [{'text','y','x0','x1'}...]（按 y 升序）。"""
    try:
        w, h = img.size
        left = 0
        try:
            left = ch.detect_pane_left(img) or 0
        except Exception:
            left = 0
        if not left:
            left = int(w * ch.PANE_LEFT_REL)
        # 会话列表列：面板左沿往左约 240px（实测本机列表文字 x≈177、面板左沿 331）
        x0 = max(0, left - 240)
        x1 = max(x0 + 40, left - 6)
        box = (x0, 30, x1, h)
        crop = img.crop(box)
        if zoom > 1:
            crop = crop.resize((crop.width * zoom, crop.height * zoom))
        items = recognize(crop)
        rows = []
        for text, x, y, ww, hh in items:
            yy = y / float(zoom)
            hit = None
            for r in rows:
                if abs(r["y"] - yy) <= max(8, hh / float(zoom)):
                    hit = r
                    break
            if hit is None:
                rows.append({"text": str(text), "y": yy, "x0": x / float(zoom), "x1": (x + ww) / float(zoom)})
            else:
                # 同一行左右两段拼起来（名字 + 预览），名字取最左那段
                if x / float(zoom) < hit["x0"]:
                    hit["text"] = str(text) + hit["text"]
                    hit["x0"] = x / float(zoom)
                else:
                    hit["text"] = hit["text"] + str(text)
                    hit["x1"] = max(hit["x1"], (x + ww) / float(zoom))
        rows.sort(key=lambda r: r["y"])
        for r in rows:
            r["y_abs"] = int(r["y"]) + 30
            r["x0_abs"] = int(r["x0"]) + x0
            r["x1_abs"] = int(r["x1"]) + x0
        return rows
    except Exception:
        return []


def green_score(img, y_abs: int, half: int = 7, x0: int = None, x1: int = None) -> float:
    """给定行中心 y（图内绝对坐标），返回该行"绿色像素占比"（0~1）。"""
    try:
        g = img.convert("RGB")
        px = g.load()
        w, h = g.size
        if x0 is None or x1 is None:
            left = 0
            try:
                left = ch.detect_pane_left(img) or 0
            except Exception:
                left = 0
            if not left:
                left = int(w * ch.PANE_LEFT_REL)
            x0, x1 = max(0, left - 240), max(0, left - 10)
        x0, x1 = max(0, int(x0)), min(w, int(x1))
        y0, y1 = max(0, y_abs - half), min(h, y_abs + half)
        tot = hit = 0
        for yy in range(y0, y1):
            for xx in range(x0, x1, 2):
                r, gg, b = px[xx, yy][:3]
                tot += 1
                if abs(r - GREEN[0]) <= GREEN_TOL and abs(gg - GREEN[1]) <= GREEN_TOL and abs(b - GREEN[2]) <= GREEN_TOL:
                    hit += 1
        return (hit / float(tot)) if tot else 0.0
    except Exception:
        return 0.0


def session_rows(img, zoom: int = 2) -> list:
    """把 OCR 行聚成"会话行"：每行 = 名字行 +（可选）预览行，按 ~64px 步距归并。

    返回 [{'name','preview','y_name','y_abs'}...]（y_abs ＝ 名字行的图内绝对 y）。
    """
    lines = list_rows(img, zoom=zoom)
    if not lines:
        return []
    rows = []
    for ln in lines:
        if rows and (ln["y_abs"] - rows[-1]["y_abs"]) < ROW_PITCH * 0.6:
            if rows[-1]["preview"] is None:
                rows[-1]["preview"] = ln["text"]
            else:
                rows[-1]["preview"] += ln["text"]
        else:
            rows.append({"name": ln["text"], "preview": None, "y_abs": ln["y_abs"]})
    return rows


def _green_at(img, y_abs: int, half: int = 6) -> float:
    """给定 y，量"绿底占比"（用实测绿 + 容差）。"""
    try:
        rgb = img.convert("RGB")
        px = rgb.load()
        w, h = rgb.size
        left = 0
        try:
            left = ch.detect_pane_left(img) or 0
        except Exception:
            left = 0
        if not left:
            left = int(w * ch.PANE_LEFT_REL)
        x0, x1 = max(0, left - 240), max(0, left - 10)
        y0, y1 = max(0, y_abs - half), min(h, y_abs + half)
        tot = hit = 0
        for yy in range(y0, y1):
            for xx in range(x0, x1, 2):
                r, g, b = px[xx, yy][:3]
                tot += 1
                if abs(r - GREEN[0]) <= GREEN_TOL and abs(g - GREEN[1]) <= GREEN_TOL and abs(b - GREEN[2]) <= GREEN_TOL:
                    hit += 1
        return (hit / float(tot)) if tot else 0.0
    except Exception:
        return 0.0


def name_of_row(img, y_abs: int, text: str = "", zoom: int = 3) -> str:
    """只 OCR 该行的**名字区**（左侧、上半天），拿更干净的名字；失败退回整行文本。"""
    try:
        w, h = img.size
        left = 0
        try:
            left = ch.detect_pane_left(img) or 0
        except Exception:
            left = 0
        if not left:
            left = int(w * ch.PANE_LEFT_REL)
        box = (max(0, left - 235), max(0, y_abs - 14), max(0, left - 95), min(h, y_abs + 16))
        crop = img.crop(box)
        if crop.width < 8 or crop.height < 6:
            return text
        if zoom > 1:
            crop = crop.resize((crop.width * zoom, crop.height * zoom))
        from PIL import ImageOps
        try:
            crop = ImageOps.autocontrast(crop.convert("L")).convert("RGB")   # 白字绿底：先拉对比再识别
        except Exception:
            pass
        got = clean("".join(str(i[0]) for i in recognize(crop))).strip()
        return got or clean(text)
    except Exception:
        return text


def find_row(img, name: str, zoom: int = 2):
    """在会话列表里按名字找会话行（**纯读图，不动鼠标**），返回可点击的 (x, y)（图内坐标）。

    ⚠️ 不要用驱动库的 `find_session()` 取而代之：它在找不到时会**用真实鼠标悬停/滚动会话列表**
    （实测调用期间光标位置会变），违反"不动鼠标"。这里的查找只用截屏 + OCR。
    只比对**名字列**，不比对预览（预览里常出现别人的名字，会误配）。
    """
    try:
        w, h = img.size
        left = 0
        try:
            left = ch.detect_pane_left(img) or 0
        except Exception:
            left = 0
        if not left:
            left = int(w * ch.PANE_LEFT_REL)
        for r in session_rows(img, zoom=zoom):
            if matches(r.get("name") or "", name):
                return (max(0, left - 150), min(h - 2, int(r["y_abs"]) + 16))
        return None
    except Exception:
        return None


def current_chat_name(img=None, gui=None, min_green: float = 0.20) -> tuple:
    """只读：返回 (当前打开的会话名, 依据)。依据串里写明是靠哪一行的绿底判出来的。

    ⚠️ 不能让"标题"来当判据：实测微信 4.1.15.8 的会话标题是**浅灰细字**，WinRT OCR 读不出来
       （同一张图里会话列表的名字/预览/时间戳都读得出）⇒ 用**会话列表 + 绿底高亮行**这两个独立信号。
    """
    if img is None:
        img = ch.capture_image(gui=gui)
    if img is None:
        return "", "抓图失败"
    rows = session_rows(img)
    if not rows:
        return "", "会话列表没读到文字"
    best, score = None, 0.0
    for r in rows:
        sc = _green_at(img, r["y_abs"])
        if sc > score:
            best, score = r, sc
    if best is None or score < min_green:
        return "", "没找到绿底高亮行（最高占比 %.2f，共 %d 行）" % (score, len(rows))
    name = name_of_row(img, best["y_abs"], best["name"])
    return name, "绿底行「%s」占比 %.2f（整行 OCR：%s）" % (name[:16], score, best["name"][:22])
