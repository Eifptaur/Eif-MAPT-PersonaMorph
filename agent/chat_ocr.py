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
import time

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
    for r in rows:                       # 名字里可能粘着预览（`E:提交信息…`）⇒ 统一切干净
        r["full"] = r["name"]
        r["name"] = split_name(r["name"])
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


_sep_re = re.compile(r"[:：]")


def split_name(text: str) -> str:
    """从会话行文本里切出**名字**：先清时间/省略号，再取第一个 `:`/`：` 之前的部分。

    为什么要切（2026-09-13 实测）：OCR 会把一行读成「名字 + 预览」连在一起，
    例如 E 的那行读出来是 `E:提交信息还．“` —— 单字母名字在 `matches()` 里只走"完全相等"分支，
    带着预览就永远配不上 ⇒ **E 这类会话以前永远选不中**。切开之后名字就是 `E`。
    """
    t = clean(text) or ""
    if not t:
        return ""
    head = _sep_re.split(t, 1)[0].strip()
    return head or t.strip()


def click_allowed(last_ts: float, now: float, cooldown_s: float = 3.0) -> tuple:
    """离"上一枪"不足 `cooldown_s` 秒就不许再点（返回 `(允许, 说明)`）。

    用户 2026-09-13 当场定的规矩：「**点击不能点两下，不然聊天框都关掉了。之前不是有这个问题吗**」
    —— 早前实验里那次"多点了会话框一下，把会话和聊天框全都点掉了"就是这么来的。
    ⇒ 冷却期内**只许重新读图复核，绝不补第二枪**；一次切会话最多一枪。
    """
    try:
        dt = float(now) - float(last_ts or 0.0)
    except Exception:
        dt = 999.0
    if last_ts and dt < float(cooldown_s):
        return False, "距上一枪仅 %.2fs（冷却 %.1fs：连点两下会把聊天框关掉）" % (dt, float(cooldown_s))
    return True, ""


def find_row_info(img, name: str, zoom: int = 2):
    """同 `find_row`，但返回整条信息 `{'pos':(x,y),'y_abs':int,'name':str}`（点击后要拿 y_abs 复核高亮）。"""
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
                return {"pos": (max(0, left - 150), min(h - 2, int(r["y_abs"]) + 16)),
                        "y_abs": int(r["y_abs"]), "name": r.get("name") or ""}
        return None
    except Exception:
        return None


def highlight(img, min_green: float = 0.12):
    """当前**绿底高亮行**（＝打开的会话行）：返回 `{'y_abs','score','name'}`，没有则 None（只读）。

    ⚠️ 阈值为什么是 0.12：实测高亮行占比随帧质量在 **0.18~0.74** 之间跳（2026-09-13 同一窗口连续测），
       而普通行只有 **0.00~0.01** ⇒ 0.12 仍留十倍余量；用 0.20 会把"真高亮但帧偏糊"的那一帧判成没有。
    """
    try:
        if img is None:
            return None
        best, score = None, 0.0
        for r in session_rows(img):
            sc = _green_at(img, r["y_abs"])
            if sc > score:
                best, score = r, sc
        if best is not None and score >= float(min_green):
            return {"y_abs": int(best["y_abs"]), "score": float(score), "name": best.get("name") or ""}
        return None
    except Exception:
        return None


def find_row_scrolled(capture_fn, find_fn, scroll_fn=None, max_steps: int = 6,
                      per_step: int = 3, settle_s: float = 0.45,
                      tries_per_step: int = 1, gap_s: float = 0.35) -> tuple:
    """**先看当前视野，找不到就平滑下滚再找**（后台：`scroll_fn` 由调用方注入投递滚轮，不碰鼠标）。

    为什么要滚（用户 2026-09-13 原话：「你滚得太不顺滑了，**一下一下地滚，导致没有看到**」）：
    截图一次只覆盖会话列表露出来的那几行 ⇒ 目标在下面时**永远找不到**；要一格一格连滚、每轮重新读图。
    这里把「捕获 / 查找 / 滚动」三个动作都做成注入式，判据可以完全脱机自测（`chat_ocr_selftest`）。
    返回 `(info 或 None, 过程说明)`。
    """
    logs = []
    steps = max(0, int(max_steps))
    tries = max(1, int(tries_per_step))
    for step in range(steps + 1):
        info = None
        for _t in range(tries):
            img = capture_fn()
            info = find_fn(img) if img is not None else None
            if info:
                break
            if _t + 1 < tries:
                time.sleep(max(0.0, float(gap_s)))
        if info:
            logs.append("第 %d 轮第 %d 帧命中" % (step + 1, _t + 1))
            return info, "；".join(logs)
        if step >= steps or scroll_fn is None:
            break
        try:
            ok = bool(scroll_fn(int(per_step)))
        except Exception as e:
            ok = False
            logs.append("滚轮异常 %s" % type(e).__name__)
        logs.append("第 %d 次未命中→下滚 %d 格%s" % (step + 1, per_step, "" if ok else "（失败）"))
        time.sleep(max(0.0, float(settle_s)))
    if not logs:
        logs.append("一次都没捕获到画面")
    return None, "；".join(logs)


def capture_best(gui=None, frames: int = 3, img=None, good_rows: int = 8) -> object:
    """多抓几帧，挑「会话列表读到行数最多」的那帧返回（只读，不碰鼠标）。

    为什么需要（2026-09-13 实测）：同一窗口连续抓图，OCR 行数会在 **2 行 ↔ 13 行**之间跳——
    抓到没渲染完/被遮挡的那一帧时，会话列表几乎读不出来 ⇒ 单帧判定会得出"找不到该会话"的**假结论**。
    宁可多抓两帧（每帧约 0.3s），也不要拿一帧坏图下结论。
    """
    best, best_n = None, -1
    for _i in range(max(1, int(frames))):
        im = img if img is not None else ch.capture_image(gui=gui)
        if im is None:
            time.sleep(0.3)
            continue
        n = len(list_rows(im))
        if n > best_n:
            best, best_n = im, n
        if n >= int(good_rows):
            break
        time.sleep(0.35)
    return best


def current_chat_name(img=None, gui=None, min_green: float = 0.12, retries: int = 3) -> tuple:
    """只读：返回 (当前打开的会话名, 依据)。依据串里写明是靠哪一行的绿底判出来的。

    ⚠️ 不能让"标题"来当判据：实测微信 4.1.15.8 的会话标题是**浅灰细字**，WinRT OCR 读不出来
       （同一张图里会话列表的名字/预览/时间戳都读得出）⇒ 用**会话列表 + 绿底高亮行**这两个独立信号。
    ⚠️ 抓图会**偶发拿到没渲染完的一帧**（实测：同一次调用里 `detect_pane_left=0`、只识别到 2 行、
       找不到绿底；紧接着再抓就正常 331/13 行/0.96）⇒ 自己抓图时**重试几帧、取最好的一帧**。
    """
    tries = 1 if img is not None else max(1, int(retries))
    best = ("", "抓图失败")
    best_rows = -1
    for _i in range(tries):
        use = img if img is not None else ch.capture_image(gui=gui)
        if use is None:
            best = ("", "抓图失败")
        else:
            rows = session_rows(use)
            if not rows:
                if best_rows < 0:
                    best = ("", "会话列表没读到文字")
            else:
                pick, score = None, 0.0
                for r in rows:
                    sc = _green_at(use, r["y_abs"])
                    if sc > score:
                        pick, score = r, sc
                if pick is not None and score >= min_green:
                    name = name_of_row(use, pick["y_abs"], pick["name"])
                    if name:
                        return name, "绿底行「%s」占比 %.2f（整行 OCR：%s）" % (name[:16], score, pick["name"][:22])
                if len(rows) > best_rows:
                    best_rows = len(rows)
                    best = ("", "没找到绿底高亮行（最高占比 %.2f，共 %d 行）" % (score, len(rows)))
        if img is not None:
            break
        time.sleep(0.45)
    return best
