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

import os
import re
import threading
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


# ————————————————— OCR 硬超时 / 熔断 / 预算（2026-09-14，测机手册 ④） —————————————————
# 为什么必须自己再加一层：驱动库的 `ScreenOCR.recognize` 写的是
# `asyncio.run(asyncio.wait_for(_run(), timeout=8))`——**那个 8 秒是"软"的**：`wait_for` 取消的是
# asyncio 任务，而里面 await 的是 WinRT 的 `IAsyncOperation`；原生操作不响应取消时，`asyncio.run`
# 收尾（取消剩余任务 + 关事件循环）会**一直等下去**。实测本机某次 OCR 步骤卡了 **8 分 19 秒**
# 而不是 8 秒。⇒ 定式：**绝不在调用方线程里直接跑它**，一律「daemon 线程 + 硬 join 超时」——
# 到点就放弃、返回空、记成"判据不可用"。
# 代价：超时那一次会**漏一个后台线程**（daemon，进程退出不受影响）⇒ 用熔断器限流：
# 连续 `BREAK_AFTER` 次超时就**停止再试** `BREAK_COOLDOWN_S` 秒，直接返回空并给出原因。
# ⚠️ 空返回只表示"**没拿到文字**"，不表示"画面上没有文字"：调用方必须按"判据不可用"处理
#   （项目铁律：判据不可用 ⇒ 不发；绝不许当成"没证据也能发"）。
DEFAULT_TIMEOUT_S = 25.0        # 单次 OCR 硬上限（秒）；环境变量 WXAGENT_OCR_TIMEOUT 可覆盖
BREAK_AFTER = 2                 # 连续硬超时几次 ⇒ 熔断
BREAK_COOLDOWN_S = 120.0        # 熔断时长（秒）；环境变量 WXAGENT_OCR_COOLDOWN 可覆盖
SEND_WINDOW_S = 60.0            # **一笔发送链**允许花在 OCR 上的总时间（秒）；一次 OCR 正常 0.3~1s
WINDOW_MIN_SLICE_S = 2.0        # 窗里只剩这么点时，不再开新的 OCR（那一次注定超时）

_local = threading.local()
_lock = threading.RLock()
_health = {"calls": 0, "timeouts": 0, "budget_hits": 0, "breaks": 0, "consecutive": 0,
           "last_why": "", "last_timeout_ts": 0.0, "open_until": 0.0}


def timeout_s() -> float:
    """单次 OCR 的硬上限（秒）。"""
    try:
        v = float(os.environ.get("WXAGENT_OCR_TIMEOUT", "") or DEFAULT_TIMEOUT_S)
    except Exception:
        v = DEFAULT_TIMEOUT_S
    return max(1.0, min(600.0, v))


def cooldown_s() -> float:
    """熔断时长（秒）。"""
    try:
        v = float(os.environ.get("WXAGENT_OCR_COOLDOWN", "") or BREAK_COOLDOWN_S)
    except Exception:
        v = BREAK_COOLDOWN_S
    return max(0.0, min(3600.0, v))


def _wins() -> list:
    st = getattr(_local, "wins", None)
    if st is None:
        st = _local.wins = []
    return st


def begin_window(seconds=None) -> float:
    """给"**这一笔操作**"开一个 OCR 总时间窗（秒），窗内所有 OCR 只能花剩余时间。

    用法：在发送链入口各加一行 `begin_window()`（不必配 `end_window`——窗口到点**自动失效**，
    且会随下一次 OCR 调用被清理掉，**不会**拖累之后无关的调用）。
    为什么要它：单次硬超时只保证"一次 OCR 不卡死"，一笔操作里若有 4~6 次 OCR，最坏仍会累加几分钟
    ⇒ 用总窗把整笔卡住。开窗的人拿返回的 token 自己用 `budget_out(token)` 判"预算用完没"。
    返回本次窗口的 token（`end_window(token)` 可提前关掉它）。
    """
    w = float(timeout_s() if seconds is None else seconds)
    tok = time.monotonic() + max(0.0, w)
    st = _wins()
    st.append(tok)
    if len(st) > 32:                     # 兜底：只留最近 8 个（正常每次 OCR 都会顺手清理过期项）
        del st[:-8]
    return tok


def end_window(token=None) -> None:
    """提前关掉时间窗（`token` 省略＝关最里面那个）。"""
    st = _wins()
    if not st:
        return
    if token is None:
        st.pop()
        return
    try:
        st.remove(token)
    except ValueError:
        pass


def window_left():
    """本线程当前窗口还剩多少秒；**没有活着的窗口返回 None**。

    过期窗口在这里就地失效（只清掉、不当成 0）——这一点很重要：如果把它当 0，那么"很久以前某笔操作
    留下的过期窗口"会让**紧接着的一次无关 OCR** 静默失败（假失败）。所以口径是：
      · **活着**的窗口：参与限时（`_left` 取 min）；
      · **过期**的窗口：清掉、不参与 ⇒ 后续调用照常；
      · 想知道"我这笔的预算用完没有"，用 `budget_out(token)`（由开窗的人自己判）。
    """
    st = _wins()
    if not st:
        return None
    now = time.monotonic()
    for tok in list(st):
        if float(tok) <= now:
            st.remove(tok)
    live = [float(tok) - now for tok in st]
    return min(live) if live else None


def budget_out(token=None) -> bool:
    """开窗的人用它问"我这笔的 OCR 预算用完了吗"（`token` ＝ `begin_window()` 的返回值）。"""
    if token is not None:
        return time.monotonic() >= float(token)
    rem = window_left()
    return rem is not None and rem <= WINDOW_MIN_SLICE_S


def _left(default: float) -> float:
    """本次 OCR 最多还能花多少秒（单次硬上限 ∩ 活着的窗口）。

    窗里只剩不到 `WINDOW_MIN_SLICE_S` 时直接给 0（不再开一次注定超时的 OCR）——注意这**只会**
    发生在窗口还活着的时候，也就是这笔操作确实已经把 OCR 预算花光了，属于预期的 fail-closed。
    """
    best = float(default)
    rem = window_left()
    if rem is None:
        return max(0.0, best)
    if rem < WINDOW_MIN_SLICE_S:
        return 0.0
    return max(0.0, min(best, rem))


def health() -> dict:
    """OCR 健康度快照（控制台/日志/判据用）：超时次数、熔断状态、最后一次原因。"""
    with _lock:
        d = dict(_health)
    d["open"] = d["open_until"] > time.monotonic()
    d["open_left"] = max(0.0, d["open_until"] - time.monotonic())
    d["timeout_s"] = timeout_s()
    d["window_left"] = window_left()
    return d


def reset_health() -> None:
    """清空计数与熔断/时间窗（判据与人工排障用）。"""
    with _lock:
        _health.update({"calls": 0, "timeouts": 0, "budget_hits": 0, "breaks": 0,
                        "consecutive": 0, "last_why": "", "last_timeout_ts": 0.0,
                        "open_until": 0.0})
    _local.wins = []


def recent_timeout(window_s: float = 60.0) -> bool:
    """最近 window_s 秒内 OCR 是否硬超时过。

    调用方据此把结论标成"**判据不可用**"（而不是"画面上没这个字"）——这两种情况对发送闸门的
    含义不同：前者必须说清楚"是 OCR 卡住了"，后者才是"内容对不上"。
    """
    with _lock:
        ts = float(_health["last_timeout_ts"] or 0.0)
    return bool(ts) and (time.monotonic() - ts) <= max(0.0, float(window_s))


def health_line() -> tuple:
    """控制台「一键体检」用的那一行：返回 `(status, detail, hint)`，status ∈ ok/warn/info。

    UI/文案只在这里写一次（控制台照抄），顺带让判据可以机械检查"熔断/超时两种状态都有可读文案"。
    """
    try:
        h = health()
    except Exception as e:                                   # noqa: BLE001
        return "info", "读不到 OCR 健康度：%s" % e, ""
    if h.get("open"):
        return ("warn",
                "正在熔断：连续卡住后暂停重试，%.0f 秒后自动恢复" % float(h.get("open_left") or 0.0),
                "OCR 卡住时发送链会放弃本次发送而不是乱猜；等一下或重启机器人")
    if h.get("timeouts"):
        return ("warn",
                "卡住过 %d 次，最近一次：%s" % (h["timeouts"], str(h["last_why"])[:60]),
                "卡住的那一次按「判据不可用」处理：不发送；反复出现请重启机器人")
    if h.get("calls"):
        return "ok", "已调用 %d 次，单次最多 %.0f 秒，从没卡住" % (h["calls"], h["timeout_s"]), ""
    return "ok", "还没用过，单次最多 %.0f 秒，从没卡住" % h["timeout_s"], ""


def blocked() -> str:
    """现在能不能做 OCR：能 ⇒ ""；不能 ⇒ 原因（熔断中 / 本笔预算用尽）。"""
    with _lock:
        until = float(_health["open_until"] or 0.0)
    now = time.monotonic()
    if until > now:
        return "OCR 连续超时已熔断，%.0f 秒后自动恢复" % (until - now)
    if window_left() is not None and _left(1.0) <= 0.0:
        return "本笔操作的 OCR 时间预算已用尽"
    return ""


def _note(why: str, timeout: bool = False, budget_hit: bool = False) -> None:
    now = time.monotonic()
    with _lock:
        _health["last_why"] = str(why)
        if timeout:
            _health["timeouts"] += 1
            _health["last_timeout_ts"] = now
            _health["consecutive"] += 1
            if _health["consecutive"] >= BREAK_AFTER:
                _health["breaks"] += 1
                _health["consecutive"] = 0
                _health["open_until"] = now + cooldown_s()
                _health["last_why"] = "%s ⇒ 连续超时，已熔断 %.0f 秒" % (why, cooldown_s())
        elif budget_hit:
            _health["budget_hits"] += 1


def _run_hard(fn, seconds: float, what: str):
    """在 daemon 线程里跑 fn，最多等 seconds 秒 ⇒ (ok, 值, 原因)。**超时绝不阻塞调用方。**"""
    box = {}

    def _t():
        try:
            box["v"] = fn()
        except Exception as e:                       # noqa: BLE001
            box["e"] = "%s: %s" % (type(e).__name__, e)

    th = threading.Thread(target=_t, daemon=True, name="ocr-%s" % (what or "hard"))
    th.start()
    th.join(max(0.1, float(seconds)))
    if th.is_alive():
        return False, None, "超时（%.0f 秒未返回，已放弃）" % float(seconds)
    if "e" in box:
        return False, None, "异常 %s" % box["e"]
    return True, box.get("v"), ""


def recognize(img, timeout=None) -> list:
    """对 PIL 图像跑 OCR，返回 [(text, x, y, w, h)]；不可用/失败/超时返回 []。

    **硬超时**（2026-09-14 加）：单次最多 `timeout_s()`（默认 25 秒，环境变量可调），到点返回 `[]`
    并把原因记进 `health()`；连续超时达 `BREAK_AFTER` 次会熔断 `BREAK_COOLDOWN_S` 秒。
    """
    try:
        from wechatauto.guia import ScreenOCR
    except Exception:
        return []
    why_blocked = blocked()
    if why_blocked:
        _note(why_blocked, budget_hit=("预算" in why_blocked))
        return []
    left = _left(timeout_s() if timeout is None else float(timeout))
    if left <= 0.0:
        _note("本笔操作的 OCR 时间预算已用尽", budget_hit=True)
        return []
    with _lock:
        _health["calls"] += 1
    ok, val, why = _run_hard(lambda: list(ScreenOCR.recognize(img) or []), left, "OCR")
    if not ok:
        _note("OCR %s" % why, timeout=("超时" in why))
        return []
    with _lock:
        _health["consecutive"] = 0
    return list(val or [])


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
    """OCR 文本与目标会话名是否算同一个（互相包含即可，容忍 OCR 漏字/多字/截断省略号）。

    ⚠️ 单字/单字母名字要单独一条路（2026-09-13 实测 bug）：会话行文本是"名字＋预览＋时间"拼起来的
    （E 那一行 OCR 出来是 `[草稿]EE` ＝ 草稿标记 ＋ 名字 E ＋ 草稿内容 E），而老实现要求 `len(name) >= 2`
    才走包含判断 ⇒ **名字只有一个字母的会话永远定位不到**（E 明明在第一行，`find_row_info` 返回 None，
    `switch_chat_posted` 于是报"没定位到 E"）。⇒ 单字名字：去掉草稿标记后**要求以它开头**；
    敢这样放宽的底气是——点完之后的**内容级身份闸**才是发不发的最后一道闸（点错 ⇒ 不发送）。
    """
    a, b = norm(text), norm(name)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(b) == 1:
        t = a
        for _p in ("草稿", "draft"):
            if t.startswith(_p):
                t = t[len(_p):]
                break
        return t.startswith(b)
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


def find_row_info(img, name: str, zoom: int = 2, want_time: str = ""):
    """同 `find_row`，但返回整条信息 `{'pos':(x,y),'y_abs':int,'name':str,'why':str}`。

    `want_time`＝目标会话**最后一条消息的时间**（`HH:MM`，由调用方从 DB 取）——给一条**不依赖名字**的路：
    2026-09-13 实测，名字只有一个字母的会话（E）靠 OCR 认不稳（认不出、或被别的行的草稿文本骗到），
    而每行右侧那个时间戳 OCR 读得很准（实测 19：41 / 21：41 / 20：36 都读得出）。两条信号合起来用：
      · 有 `want_time`：优先选**时间命中**且（名字也命中 或 名字那一行 OCR 为空/不可信）的行；
        如果这行名字能读出来、而且明显是别的会话 ⇒ 这一行不算（宁可找不到，不许点错）。
      · 没有 `want_time`：退回原来的名字匹配（含长度 ≤2 时的名字行全等复核）。
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
        want = (want_time or "").strip().replace("：", ":")
        rows = session_rows(img, zoom=zoom)
        for r in rows:
            nm = r.get("name") or ""
            y = int(r["y_abs"])
            hit_t = False
            if want:
                # ⚠️ 时间戳在**整行拼起来**的文本里（`full`＝名字＋预览＋时间，实测 '文件传．“19：41'），
                #    只看 `name` 永远找不到时间 ⇒ 一开始就是这么写错的（按时间定位一直返回 None）。
                _blob = str(r.get("full") or "") + " " + nm
                for m in _time_re.finditer(_blob):
                    t = m.group(0).replace("：", ":").replace(" ", "")
                    try:
                        hh, mm = t.split(":")
                        if "%d:%02d" % (int(hh), int(mm)) == want:
                            hit_t = True
                            break
                    except Exception:
                        pass
            if want and hit_t:
                # 时间命中：再看名字那一行——读得出且明显不是它 ⇒ 不算（宁可找不到，不许点错）
                got = ""
                try:
                    got = name_of_row(img, y, nm, zoom=3)
                except Exception:
                    got = ""
                if got and not matches(got, name):
                    continue
                return {"pos": (max(0, left - 150), min(h - 2, y + 16)), "y_abs": y, "name": nm,
                        "why": "按最后消息时间 %s 命中（该行名字 OCR=%r）" % (want, got)}
            if want:
                continue
            if not matches(nm, name):
                continue
            # ⚠️ 单字/单字母名字必须**复核这一行的名字行**（2026-09-13 实测假阳性：目标行是第 2 行，
            #    而第 3 行"宋孟"的预览里带着草稿内容 `[草稿]EE` ⇒ 放宽后的前缀匹配把**宋孟那一行**认成了 E，
            #    点下去打开了别的会话）。复核用 `name_of_row()`（只 OCR 名字那一行、zoom=3）。
            # ⛔ 2026-09-14 修：原来这里要 `norm(got) == norm(name)` **裸全等**，可 E 那种行的**名字行**
            #    会被 OCR 成 `[草稿]EE`（草稿标记＋名字＋草稿内容）⇒ 裸全等永远不等 ⇒ 单字母会话
            #    **根本切不过去**（⑤ 重发实测：列表里明明有 `[草稿]EE`，这里全否、`switch_chat_posted`
            #    报"没定位到 E"，白滚了 6 轮）。改成跟 `matches()` 同一套口径（剥「草稿/draft」前缀，
            #    单字只要求**以它开头**）。安全性不变：挡假阳性的仍然是**名字行** —— 那一次名字行读出来是
            #    「宋孟」，`matches("宋孟", "E")` 照样为假；放宽的只是"名字行自己带草稿标记"这一种形态。
            if len(norm(name)) <= 2:
                got = ""
                try:
                    got = name_of_row(img, y, nm, zoom=3)
                except Exception:
                    got = ""
                if not matches(got, name):
                    continue
            return {"pos": (max(0, left - 150), min(h - 2, y + 16)), "y_abs": y, "name": nm,
                    "why": "按名字匹配（%r）" % nm}
        return None
    except Exception:
        return None


def row_time_at(img, y_abs: int, tol: int = 34) -> str:
    """离 `y_abs` 最近的那一行里出现的时间戳（归一化成 `H:MM`）；取不到返回 ''。"""
    best, best_d = "", 10 ** 9
    try:
        for r in session_rows(img):
            d = abs(int(r["y_abs"]) - int(y_abs))
            if d > tol or d >= best_d:
                continue
            for m in _time_re.finditer(str(r.get("full") or "")):
                t = m.group(0).replace("：", ":").replace(" ", "")
                try:
                    hh, mm = t.split(":")
                    best, best_d = "%d:%02d" % (int(hh), int(mm)), d
                    break
                except Exception:
                    pass
    except Exception:
        return ""
    return best


def highlight_time(img):
    """当前**高亮行**（＝打开的会话）的时间戳与它所在的 y：返回 `(H:MM, y)`，取不到给 `("", None)`。

    为什么要这个：单字母/短名字的会话行**名字读不出来**（实测 E: `name_of_row` 给空串），
    但那一行右侧的时间戳读得准 ⇒ "高亮行的时间 == 目标会话最后一条消息的时间"是还读得出来的身份信号。
    """
    hl = None
    try:
        hl = highlight_relative(img)[0] or highlight(img)
    except Exception:
        hl = None
    if not hl:
        return "", None
    y = int(hl["y_abs"])
    return row_time_at(img, y), y


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


def highlight_relative(img, min_top: float = 0.05, ratio: float = 2.5) -> tuple:
    """**相对**判据找高亮行：绿底最强的行 vs 次强行（返回 `(行 或 None, 说明)`）。

    为什么不用绝对阈值：帧质量会让真高亮的占比在 0.18~0.74 之间跳，绝对阈值总会在某一帧误杀
    （2026-09-13 实测：判 0.20 时把占比 0.159 的真高亮判成"没有高亮"）。相对比较稳得多——
    实测高亮行 0.159 / 其余行 0.000：要求「最高 ≥ min_top 且 ≥ ratio × 次高」即可。
    """
    try:
        rows = session_rows(img)
        if not rows:
            return None, "没有读到会话行"
        scored = sorted(((_green_at(img, r["y_abs"]), r) for r in rows), key=lambda t: -t[0])
        top, second = scored[0], (scored[1][0] if len(scored) > 1 else 0.0)
        why = "最高 %.3f（%s）次高 %.3f" % (top[0], str(top[1].get("name"))[:10], second)
        if top[0] < float(min_top):
            return None, why + "·最高也不够"
        if second > 0 and top[0] < float(ratio) * second:
            return None, why + "·不够突出"
        return {"y_abs": int(top[1]["y_abs"]), "score": float(top[0]),
                "name": top[1].get("name") or ""}, why
    except Exception as e:
        return None, "相对判据异常：%s" % type(e).__name__


def _nz(s: str) -> str:
    return "".join(ch for ch in str(s or "") if ch.isalnum())


def norm_alnum(s: str) -> str:
    """只留字母数字（给"短指纹严格子串"用）。"""
    return _nz(s)


def content_match(pane: str, needle: str) -> bool:
    """聊天区 OCR 文本里能不能认出「目标会话最近的内容」——**按内容认会话**，不靠名字。

    为什么需要（2026-09-13 发错会话事故）：名字判据会骗人——群聊行的预览里带着**发言人前缀**
    （`E: 提交信息…`），被当成"会话名 = E"后就点进了那个群。内容比对不依赖任何名字：
    把目标会话最近一条**文本**拿来，在当前聊天区里找它的显著片段即可。
    """
    a, b = _nz(pane), _nz(needle)
    if len(a) < 6 or len(b) < 6:
        return False
    if b[:16] and b[:16] in a:
        return True
    for n in (12, 10, 8, 6):                       # OCR 常吞字 ⇒ 多尺度片段逐一找
        step = max(1, n // 2)
        for i in range(0, max(0, len(b) - n) + 1, step):
            if b[i:i + n] in a:
                return True
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.5


def pane_text(img, limit: int = 200, zoom: int = 2) -> str:
    """聊天区（面板左沿往右那一块）的 OCR 文字摘要——判"当前打开的是谁 / 切会话发没发生"（只读）。

    ⛔ 2026-09-14 修（⑤ 重发实测）：原来是**单帧 + 原尺寸**读一次，实测在"聊天区全是文件卡"的那一屏
    读出**空串** ⇒ 内容级身份闸拿到空证据（于是有了 `chat_identity_ok` 里"读不到就返回 None"那条）。
    现在两件事：①放大 `zoom` 倍再认（小字放大显著提识别率，与会话行同一套路）；
    ②整块读不到就**分三段**各认一次（覆盖"中间被一张大图/文件卡隔开"的屏）。
    ⚠️ 有判据断言过旧的单帧写法（`pane_text` 的调用点/切片），改这里要跟着看 `session_pick_selftest`。
    """
    try:
        if img is None:
            return ""
        w, h = img.size
        left = 0
        try:
            left = ch.detect_pane_left(img) or 0
        except Exception:
            left = 0
        if not left:
            left = int(w * ch.PANE_LEFT_REL)
        box = (left + 10, 120, max(left + 40, w - 10), int(h * 0.62))

        def _read(b):
            crop = img.crop(b)
            if zoom and int(zoom) > 1:
                crop = crop.resize((crop.width * int(zoom), crop.height * int(zoom)))
            return "".join(str(i[0]) for i in recognize(crop))

        txt = _read(box)
        if not txt.strip():
            parts = []
            y0, y1 = int(box[1]), int(box[3])
            step = max(40, (y1 - y0) // 3)
            for i in range(3):
                b = (box[0], y0 + i * step, box[2], min(y1, y0 + (i + 1) * step + 20))
                if b[3] - b[1] > 20:
                    parts.append(_read(b))
            txt = "".join(parts)
        return txt[:int(limit)]
    except Exception:
        return ""


def find_row_scrolled(capture_fn, find_fn, scroll_fn=None, max_steps: int = 6,
                      per_step: int = 3, settle_s: float = 0.45,
                      tries_per_step: int = 1, gap_s: float = 0.35,
                      budget_s=None) -> tuple:
    """**先看当前视野，找不到就平滑下滚再找**（后台：`scroll_fn` 由调用方注入投递滚轮，不碰鼠标）。

    为什么要滚（用户 2026-09-13 原话：「你滚得太不顺滑了，**一下一下地滚，导致没有看到**」）：
    截图一次只覆盖会话列表露出来的那几行 ⇒ 目标在下面时**永远找不到**；要一格一格连滚、每轮重新读图。
    这里把「捕获 / 查找 / 滚动」三个动作都做成注入式，判据可以完全脱机自测（`chat_ocr_selftest`）。
    **时间预算**（2026-09-14 加）：最坏情况本来是 7 轮 × 3 帧 ＝ 21 次 OCR，卡起来就是几分钟；
    现在整段共用一个 `budget_s`（默认 `timeout_s()`），用完即停并在过程串里写明。
    返回 `(info 或 None, 过程说明)`。
    """
    logs = []
    steps = max(0, int(max_steps))
    tries = max(1, int(tries_per_step))
    tok = begin_window(budget_s)
    try:
        for step in range(steps + 1):
            info = None
            for _t in range(tries):
                if budget_out(tok):
                    logs.append("OCR 时间预算用尽（%.0f 秒），停在第 %d 轮" % (
                        float(timeout_s() if budget_s is None else budget_s), step + 1))
                    return None, "；".join(logs)
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
    finally:
        end_window(tok)


def capture_best(gui=None, frames: int = 3, img=None, good_rows: int = 8,
                 budget_s=None) -> object:
    """多抓几帧，挑「会话列表读到行数最多」的那帧返回（只读，不碰鼠标）。

    为什么需要（2026-09-13 实测）：同一窗口连续抓图，OCR 行数会在 **2 行 ↔ 13 行**之间跳——
    抓到没渲染完/被遮挡的那一帧时，会话列表几乎读不出来 ⇒ 单帧判定会得出"找不到该会话"的**假结论**。
    宁可多抓两帧（每帧约 0.3s），也不要拿一帧坏图下结论。
    **时间预算**（2026-09-14 加）：整段共用 `budget_s`（默认 `timeout_s()`），用完就拿已拿到的最好那帧走。
    """
    best, best_n = None, -1
    tok = begin_window(budget_s)
    try:
        for _i in range(max(1, int(frames))):
            if _i and budget_out(tok):
                log_why = "OCR 时间预算用尽，取已抓到的最好一帧（%d 行）" % max(0, best_n)
                _note(log_why, budget_hit=True)
                break
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
    finally:
        end_window(tok)


def current_chat_name(img=None, gui=None, min_green: float = 0.12, retries: int = 3,
                      budget_s=None) -> tuple:
    """只读：返回 (当前打开的会话名, 依据)。依据串里写明是靠哪一行的绿底判出来的。

    ⚠️ 不能让"标题"来当判据：实测微信 4.1.15.8 的会话标题是**浅灰细字**，WinRT OCR 读不出来
       （同一张图里会话列表的名字/预览/时间戳都读得出）⇒ 用**会话列表 + 绿底高亮行**这两个独立信号。
    ⚠️ 抓图会**偶发拿到没渲染完的一帧**（实测：同一次调用里 `detect_pane_left=0`、只识别到 2 行、
       找不到绿底；紧接着再抓就正常 331/13 行/0.96）⇒ 自己抓图时**重试几帧、取最好的一帧**。
    ⛔ **硬超时**（2026-09-14 加，测机手册 ④）：整段共用 `budget_s`（默认 `timeout_s()`＝25 秒）。
       实测本函数曾卡 **8 分 19 秒**（根因：库的 8 秒 asyncio 软超时取消不了 WinRT 原生操作）。
       到点就停、返回 `("", 原因)`——调用方必须按"**判据不可用**"处理（不放行发送），
       绝不许把它当成"画面上没有这个会话"。OCR 卡住的痕迹同时留在 `health()` / `recent_timeout()` 里。
    """
    tries = 1 if img is not None else max(1, int(retries))
    total = float(timeout_s() if budget_s is None else budget_s)
    best = ("", "抓图失败")
    best_rows = -1
    tok = begin_window(total)
    try:
        for _i in range(tries):
            if budget_out(tok):
                best = (best[0], "OCR 卡住：本笔预算 %.0f 秒用完，只抓了 %d/%d 帧（%s）" % (
                    total, _i, tries, best[1]))
                return best
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
        if recent_timeout(total):
            best = (best[0], "%s（⚠️ 期间 OCR 有硬超时，结论按「判据不可用」看）" % best[1])
        return best
    finally:
        end_window(tok)


# ————————————————— 「搜索」入口：**两套 UI 都要认** —————————————————
# 用户 2026-09-13 口径：「没有搜索框了，只有搜索的一个图标，摁了之后才有搜索框」+
# 「我另外一台电脑是有搜索框的，你要把两套 UI 的兼容做好」⇒ 入口有两种形态，都得能定位：
#   · **box**（老 UI / 另一台机）：顶部直接摆着搜索框（占位文本「搜索」/「Search」）⇒ 点文字
#   · **icon**（本机 4.1.15.8 实测）：顶部**只有放大镜图标**（旁边还有「＋」圆圈），点它才展开搜索框
# 判据优先级：**先认字**（读到"搜索"就一定是 box 形态，点它最稳）→ 读不到再**认图标**
# （标题带里的深色连通块：放大镜是其中最靠左、宽高都 ≈ 30px 的那个；「＋」在它右边）。
SEARCH_BAND = (30, 135)              # 兜底用的标题带（正常走 `_search_band()` 现算）
PANEL_TOP_MAX = 170                  # 会话列表面板上沿的搜索上限（超过就认为没找到）
SEARCH_ICON_W = (9, 46)              # 图标横向宽度合理区间
SEARCH_ICON_H = (9, 46)              # 图标纵向高度合理区间


def _panel_top(img, x0, x1, light: int = 205, need: float = 0.8):
    """会话列表**面板的上沿**：跳过窗口顶部那条（浅灰或深灰的）标题栏带，返回第一个足够亮的 y。

    为什么必须有这一步（2026-09-13 实测）：`PrintWindow` 抓到的帧**有的带上标题栏、有的不带**——
    带上时 y∈[0,45] 是一整条通宽深色（实测灰值 117），`_dark_blocks` 会把它并成**一个 234×105
    的大块**（超宽高，被滤掉），而标题栏下面的放大镜/加号反而**一个都认不出来**。
    """
    try:
        g = img.convert("L")
        px = g.load()
        w = int(x1) - int(x0)
        if w < 8:
            return 0
        step = max(1, w // 40)
        xs = list(range(int(x0), int(x1), step))
        for y in range(0, min(PANEL_TOP_MAX, img.size[1])):
            hit = sum(1 for x in xs if px[x, y] > light)
            if hit >= need * len(xs):
                return y
        return 0
    except Exception:
        return 0


def _search_band(img, x0, x1):
    """标题带的纵向范围（面板上沿往下 4..82px：实测放大镜中心在面板上沿 +37px）。"""
    top = _panel_top(img, x0, x1)
    y0 = max(0, int(top) + 4)
    y1 = min(img.size[1], y0 + 78)
    return y0, y1


def _list_span(img, left=None):
    """会话列表列的横向范围 (x0, x1)（与 `list_rows` 同一套口径）。"""
    w = img.size[0]
    if left is None:
        try:
            left = ch.detect_pane_left(img) or 0
        except Exception:
            left = 0
    if not left:
        left = int(w * ch.PANE_LEFT_REL)
    x0 = max(0, left - 240)
    x1 = max(x0 + 40, left - 6)
    return x0, x1, int(left)


def _rail_right(img, max_frac: float = 0.25, dark: int = 120, need: float = 0.6) -> int:
    """左侧导航栏的右沿（实测本机 ≈89px）：拿它当"图标不许出现在更左边"的下界。

    为什么要它：`detect_pane_left()` 在 PrintWindow 抓到的帧上**有时返回 0**，此时列范围会按比例
    兜底（本机 0.26·w）⇒ 左边会把导航栏（连头像那张深色图）圈进来，头像块的大小恰好落在
    "图标"的尺寸区间里，会被误当成放大镜 ⇒ 必须显式把导航栏排除。
    """
    try:
        g = img.convert("L")
        px = g.load()
        w, h = img.size
        ys = list(range(int(h * 0.25), int(h * 0.9), max(1, h // 40)))
        if not ys:
            return 0
        right = 0
        for x in range(0, int(w * max_frac)):
            n = sum(1 for y in ys if px[x, y] < dark)
            if n >= need * len(ys):
                right = x
        return right
    except Exception:
        return 0


def _dark_blocks(img, x0, y0, x1, y1, thr: int = 150, min_px: int = 14):
    """标题带里的深色块：**二维连通域**（不是按列投影）。

    2026-09-13 实测：按列投影时，窗口标题栏那条通宽深色带会和它下面的图标**并成一个大块**
    ⇒ 图标一个都认不出。二维连通域能把"上面一条带、下面两个图标"干净地分开。
    返回 [(cx, cy, w, h)]（绝对坐标）。
    """
    g = img.convert("L").crop((int(x0), int(y0), int(x1), int(y1)))
    px = g.load()
    w, h = g.size
    if w <= 0 or h <= 0:
        return []
    mask = bytearray(w * h)
    for y in range(h):
        base = y * w
        for x in range(w):
            if px[x, y] < thr:
                mask[base + x] = 1
    seen = bytearray(w * h)
    out = []
    for i in range(w * h):
        if not mask[i] or seen[i]:
            continue
        stack = [i]
        seen[i] = 1
        n = 0
        mnx = mxx = i % w
        mny = mxy = i // w
        while stack:
            j = stack.pop()
            n += 1
            jx, jy = j % w, j // w
            if jx < mnx:
                mnx = jx
            elif jx > mxx:
                mxx = jx
            if jy < mny:
                mny = jy
            elif jy > mxy:
                mxy = jy
            if jx > 0 and mask[j - 1] and not seen[j - 1]:
                seen[j - 1] = 1
                stack.append(j - 1)
            if jx + 1 < w and mask[j + 1] and not seen[j + 1]:
                seen[j + 1] = 1
                stack.append(j + 1)
            if jy > 0 and mask[j - w] and not seen[j - w]:
                seen[j - w] = 1
                stack.append(j - w)
            if jy + 1 < h and mask[j + w] and not seen[j + w]:
                seen[j + w] = 1
                stack.append(j + w)
        if n >= min_px:
            out.append((int(x0) + int((mnx + mxx) / 2), int(y0) + int((mny + mxy) / 2),
                        int(mxx - mnx + 1), int(mxy - mny + 1)))
    return out


def find_search_entry(img, left=None, zoom: int = 2):
    """定位「搜索」入口，**兼容两套 UI**。返回 dict 或 None。

    {'variant': 'box'|'icon', 'x': 图内x, 'y': 图内y, 'why': 依据, 'cands': [...]}
    """
    if img is None:
        return None
    x0, x1, left = _list_span(img, left=left)
    y0, y1 = _search_band(img, x0, x1)
    # ① 认字：老 UI 的搜索框占位文本（box 形态优先——读到字就一定点字）
    try:
        crop = img.crop((x0, y0, x1, y1))
        if zoom > 1:
            crop = crop.resize((crop.width * zoom, crop.height * zoom))
        for t, x, y, w, hh in recognize(crop):
            s = str(t)
            if "搜索" in s or "search" in s.lower():
                return {"variant": "box", "x": x0 + int(x / zoom), "y": y0 + int(y / zoom),
                        "why": "读到搜索框占位文本 %r" % s[:12]}
    except Exception:
        pass
    # ② 认图标：**整条上部区域**扫二维深色块（有的帧带标题栏、有的不带，不写死 y）。
    #    三条规矩（都来自实测）：a) 大小要像图标 b) 要在导航栏右边（头像那张深色图大小也像图标）
    #    c) **取"最上面那一排"里最靠左的那个**——放大镜与「＋」同一排，会话行的头像/名字在更下面
    try:
        rail = _rail_right(img)
        ceil = min(img.size[1], max(y1 + 60, 170))
        cands = []
        for cx, cy, bw, bh in _dark_blocks(img, x0, 0, x1, ceil):
            if not (SEARCH_ICON_W[0] <= bw <= SEARCH_ICON_W[1] and SEARCH_ICON_H[0] <= bh <= SEARCH_ICON_H[1]):
                continue
            if cx <= rail + 4:
                continue
            cands.append((cx, cy, bw, bh))
        if cands:
            top = min(b[1] for b in cands)
            row = [b for b in cands if b[1] <= top + 14]      # 同一排（图标行）
            row.sort(key=lambda b: b[0])
            cx, cy, bw, bh = row[0]
            return {"variant": "icon", "x": cx, "y": cy,
                    "why": "最上一排最靠左的图标块 %dx%d（该排 %d 块 / 共 %d 块，导航栏右沿 %d）"
                           % (bw, bh, len(row), len(cands), rail),
                    "cands": cands}
    except Exception:
        pass
    return None


def band_signature(img, left=None, size=(72, 18)):
    """会话列表**标题带**的低分辨率灰度指纹（只看"这一带变没变"，不判内容）。"""
    if img is None:
        return None
    try:
        x0, x1, _l = _list_span(img, left=left)
        y0, y1 = _search_band(img, x0, x1)
        return img.convert("L").crop((x0, y0, x1, y1)).resize(size).tobytes()
    except Exception:
        return None


def band_diff(a, b) -> float:
    """两个标题带指纹的平均绝对差（0~1）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(abs(int(p) - int(q)) for p, q in zip(a, b)) / (255.0 * len(a))


POPOVER_MARKERS = ("最近在搜", "搜索网络结果", "搜索", "联系人", "群聊", "聊天记录", "公众号", "视频号")


def looks_like_search_popover(img) -> tuple:
    """判这张图是不是**搜索浮层**：返回 (bool, 依据)。

    为什么必须靠内容判：搜索浮层与**表情面板**的窗口类名、所属进程完全一样
    （都是 `Qt51514QWindowToolSaveBits`、都是微信），尺寸还会随查询结果变化
    （实测同一浮层：输入前 552×338、出结果后 552×891；换成搜过的词还会直接高着开）
    ⇒ 按"宽高比/尺寸"筛**必然误判**（实测过一次：把高浮层当表情面板排除掉 ⇒ 误报"浮层没弹出来"）。
    表情面板里没有下面这些字样，所以用它们当判据。
    """
    if img is None:
        return False, "没有图"
    try:
        txt = " ".join(str(t) for t, *_ in recognize(img))
    except Exception as e:
        return False, "OCR 失败：%s" % e
    for m in POPOVER_MARKERS:
        if m in txt:
            return True, "画面里有浮层标志「%s」" % m
    return False, "画面里没有搜索浮层的标志字样（读到：%s）" % txt[:40]


POPOVER_SECTIONS = ("联系", "群聊", "最常", "聊天记录", "Contacts", "Group", "Recent", "Chat")


def find_popover_row(img, name: str, zoom: int = 2):
    """在**搜索浮层**的截图里找目标那一行，返回 {'x','y','why'}（浮层客户区坐标）或 None。

    实测口径（2026-09-13，本机 4.1.15.8，浮层 552×891）：
      · 分区标题「联系人」在 (54,112)，**第一行＝头像 + 名字（名字在 x≈122）**，行中心 ≈ 标题下方 60px；
      · 右边 (≈0.86·w) 有个 ⓘ 按钮 ⇒ 落点固定取 **0.35·w**，绝不碰右边那半；
      · 单字母名字（如「E」）OCR 会读成别的（这次读成 'O'）⇒ 名字命中用**整体相等**判、不相等就退到
        「联系人」段第一行——最终能不能用仍由**内容级复核**说了算（点了错行＝发不出去，不会误发）。
    """
    if img is None:
        return None
    try:
        w, h = img.size
        items = recognize(img.crop((0, 80, int(w * 0.8), min(h, int(h * 0.55)))))
    except Exception:
        return None
    sec_y = None
    sec_name = ""
    for t, x, y, ww, hh in items:
        s = str(t)
        cy = int(y) + 80
        if norm(s) == norm(name) and norm(name):
            return {"x": max(60, int(w * 0.35)), "y": int(cy + hh / 2), "why": "OCR 命中 %r" % s[:12]}
        if sec_y is None:
            for k in POPOVER_SECTIONS:
                if k in s:
                    sec_y, sec_name = cy, k
                    break
    if sec_y is not None:
        # ⚠️ 段的标题**不一定是「联系人」**（2026-09-13 实测：同一个搜索词，浮层有时给的是
        #    「最常使用」→ 结果行，有时是「联系人」；而单字母名字（E）OCR 根本读不出来）
        #    ⇒ 只要认到**任一段标题**，就取它下面第一行（实测行中心 ≈ 标题下方 55px）；
        #      点到的是不是目标会话，仍由点完之后的**内容级复核**说了算（错行 ⇒ 不发送）。
        return {"x": max(60, int(w * 0.35)), "y": int(sec_y + 55),
                "why": "「%s」段第一行（标题 y=%d）" % (sec_name, sec_y)}
    return None
