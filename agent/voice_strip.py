# -*- coding: utf-8 -*-
"""真语音条：**合成 → 播进虚拟声卡 → 让微信自己录 → 点发送**（发出去是微信语音条，不是文件）。

为什么单开一个模块（用户 2026-09-17 原话：「我让他发语音条，**不是发音频文件**」）：
`tools.send_voice_reply` 走的是"发文件"那条路（微信里显示成文件）；真语音条要靠**成对的虚拟声卡**
（VB-CABLE 之类）+ 微信自己在录音态里录。这条链 2026-09-15 在实验脚本里跑通过，但**产品里一直没接**
（实验脚本后来清掉了）。这个模块就是那次实验的产品化。

三道闸（缺哪道就如实说，绝不假装发过）：
  ① **虚拟麦克风**（成对虚拟声卡）——`agent/audio_devices.virtual_mics()`
  ② **sounddevice**（把 PCM 播到虚拟声卡"扬声器"那一端）
  ③ **合成引擎**——`agent/voice_models.status()`
还有一道"位置标定"：微信那个"进录音态"的圆圈位置随版本/尺寸变，所以位置存在配置里
（`voice_strip.record_btn` / `send_btn`，渲染区比例），没标定时**拒发并让你先跑 `calibrate`**。

产物与判据：人看的是"语音条"，机器只认 **DB 回读 `type=语音`**（不信 GUI 返回值）。
"""
import ctypes
import os
import time
import wave

from .config import ROOT, get_config

#: 进录音态后"发送"那个绿簇：默认取渲染区右下（老实测 (0.927, 0.924) 量级）
DEFAULT_SEND = (0.932, 0.945)
#: 试标定时沿这一行扫的候选 x 比例（y 取 DEFAULT_SEND 同一带）
CALIB_XS = (0.86, 0.89, 0.92, 0.95)

#: 输入条图标行的机械定位（2026-09-17 实机取证）：右侧那个圆圈＝"按住说话"入口。
#: ⚠️ 它的比例**会漂**——右侧栏（聊天信息）开着时实测 0.745，关着时 0.878
#: （同一台机器、同一个窗口尺寸，差 0.13＝155px，足够点到空白处）⇒ **不许硬编码**：
#: 每次发送前扫一遍图标行现算（扫不到才退回配置值/兜底候选）。
RECORD_X_MIN = 0.60     # 圆圈一定在输入条右半（左半是表情/盒子/文件夹/剪刀/麦克风）
RECORD_X_MAX = 0.92     # 别撞上最右边那个「发送」按钮
RECORD_FALLBACKS = (0.878, 0.90, 0.86)   # 兜底候选：全 ≥0.8，绝不会点到文件夹/表情那些"会弹窗"的图标
METER_X = (0.56, 0.82)  # 录音态里那串绿色**音量点**的横带（用来判"音频有没有真进微信的麦克风"）
METER_Y = (0.86, 0.98)
GREEN_X_MIN = 0.78      # 「绿色发送 ↑」一定在这一带右侧（音量点在它左边，别把均值带偏）
CANCEL_X = 0.588        # 录音态里那个 ✕（实测 1400/2382；只在前一轮没退干净时用来救场）
#: 右 Alt（VK_RMENU）＝微信 PC 发语音的键盘入口（用户 2026-09-17 亲口确认，实测成立）：
#: **按住录音、松开发送**，走 `SendInput` 注入（不动鼠标）；投递键盘消息**不认**（实测 A 段失败）。
VK_RMENU = 0xA5
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort), ("wParamH", ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    """⚠️ x64 下这个结构必须是 **40 字节**（type 4 + 填充 + 联合体 32）；写小了 SendInput 直接失败。"""
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


def _send_alt(down: bool) -> bool:
    """注入**真·右 Alt**（SendInput + 扩展位）：**不动鼠标**，消息发给当前焦点窗口。

    为什么不用投递（实测 A 段失败）：键盘这类"按住才有效"的消息要求**真实按键队列**，
    `PostMessage` 投给微信主窗时绿簇毫无反应；`SendInput` 才行。
    """
    try:
        flags = (0 if down else KEYEVENTF_KEYUP) | KEYEVENTF_EXTENDEDKEY
        inp = _INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(VK_RMENU, 0, flags, 0, None)))
        return bool(ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)))
    except Exception:
        return False


def _enter_record_alt(wechat, gui, cfg=None, wait_s: float = 4.0) -> tuple:
    """**按住右 Alt 进录音态** ⇒ (ok, 说明)。判据＝绿簇；**先确认抓得到帧**，否则是假阴性。

    这条路的好处：**不碰光标、不依赖任何坐标**（DPI/窗口尺寸/右侧栏怎么变都不影响）。
    前置＝微信在前台（`SendInput` 发给焦点窗口；提权窗口在最前时同样被 UIPI 挡 ⇒ 由 `_borrow_foreground` 覆盖）。
    """
    try:
        from . import chat_header as ch
    except Exception:
        ch = None
    if ch is not None:
        try:
            if ch.grab_render(gui) is None:
                return False, ("抓不到微信画面（窗口被遮挡/最小化）⇒ 没法确认有没有进录音态，"
                               "这一枪不发（先让微信可见）")
        except Exception:
            pass
    t0 = time.time()
    shot = 0
    while time.time() - t0 < float(wait_s):
        shot += 1
        if not _send_alt(True):
            return False, "SendInput 注入右 Alt 失败（被系统挡了？）"
        time.sleep(1.0)
        if _green_cluster(gui):
            return True, "按住右 Alt 进录音态（第 %d 次）" % shot, "alt"
        _send_alt(False)                      # 没进态：先松开，免得误触发
        time.sleep(0.5)
    return False, "按住右 Alt 也没进录音态（试了 %d 次）" % shot, "alt"


def _cancel_alt(gui) -> None:
    """Alt 路中途要放弃：**先发 Esc 取消**再松开 Alt（直接松开会把这段录进去发出去）。"""
    try:
        pw = ctypes.windll.user32
        for vk in (0x1B,):                    # VK_ESCAPE
            inp = _INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(vk, 0, 0, 0, None)))
            pw.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            inp = _INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None)))
            pw.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        time.sleep(0.3)
    except Exception:
        pass
    _send_alt(False)
    time.sleep(0.4)


def pick_record_x(cols, width, x_min=RECORD_X_MIN, x_max=RECORD_X_MAX):
    """从**暗点列剖面**里挑「进录音态那个圆圈」的 x 比例（纯函数，便于离线自检）。

    ① 列计数聚簇（空 1 列以上即断开）；② 只留中心比例落在 [x_min, x_max] 的簇；
    ③ 取**最左**那个 —— 输入框里打了字时「发送」也会变深成簇，而它在圆圈右边，
    所以"右区最左的簇"才是圆圈（实测：圆圈 0.878、发送 0.93）。
    找不到 ⇒ None（调用方退回配置值 / 兜底候选）。
    """
    try:
        w = float(width or 0)
        if w <= 0 or not cols:
            return None
        thr = max(2, int(max(cols) * 0.12))
        cl, s = [], None
        for x, v in enumerate(cols):
            on = int(v) >= thr
            if on and s is None:
                s = x
            elif not on and s is not None:
                if x - s >= 3:
                    cl.append((s, x - 1))
                s = None
        if s is not None:
            cl.append((s, len(cols) - 1))
        m = []
        for c in cl:
            if m and c[0] - m[-1][1] <= 14:
                m[-1] = (m[-1][0], c[1])
            else:
                m.append((c[0], c[1]))
        good = [c for c in m if x_min <= ((c[0] + c[1]) / 2.0 / w) <= x_max]
        if not good:
            return None
        a, b = good[0]
        return (a + b) / 2.0 / w
    except Exception:
        return None


def _icon_cols(gui):
    """抓一帧渲染图 → 找输入条图标行 → (列剖面, 图宽, 行 y) 或 None。"""
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return None
        g = img.convert("L")
        w, h = g.size
        band = g.crop((0, int(h * 0.86), w, h))
        px = band.load()
        bw, bh = band.size
        rows = []
        for y in range(0, bh, 2):
            n = 0
            for x in range(0, bw, 2):
                if px[x, y] < 128:
                    n += 1
            rows.append((n, y))
        rows.sort(reverse=True)
        y0 = rows[0][1] if rows else 0
        cols = [0] * bw
        for y in range(max(0, y0 - 12), min(bh, y0 + 13)):
            for x in range(bw):
                if px[x, y] < 128:
                    cols[x] += 1
        return cols, bw, int(h * 0.86) + y0, h
    except Exception:
        return None


def locate_record(gui, cfg=None) -> tuple:
    """现算「按住说话」圆圈的 x 比例 ⇒ (x, 来源说明)。"""
    c = _cfg(cfg)
    got = _icon_cols(gui)
    if got:
        x = pick_record_x(got[0], got[1])
        if x:
            return x, "扫图标行现算"
    rb = c.get("record_btn") or []
    try:
        if rb and float(rb[0]) >= 0.80:
            return float(rb[0]), "配置 voice_strip.record_btn"
    except Exception:
        pass
    return RECORD_FALLBACKS[0], "兜底候选"


def _cfg(cfg=None) -> dict:
    try:
        return dict((cfg or get_config()).get("voice_strip") or {})
    except Exception:
        return {}


def _virtual_mic() -> tuple:
    """有没有可用的虚拟麦克风（成对虚拟声卡）⇒ (名字, 说明)。"""
    try:
        from . import audio_devices as ad
        mics = ad.virtual_mics(only_active=True) or []
        clean = [m for m in mics if not ad._is_mixing(m)] if hasattr(ad, "_is_mixing") else mics
        if mics:
            pick = (clean or mics)[0]
            return str(pick.get("name") or ""), ""
        return "", "没检测到虚拟麦克风（要成对的虚拟声卡，如 VB-CABLE；装完在控制台点「重检」）"
    except Exception as e:
        return "", "虚拟声卡检测失败：%s" % e


def _out_device() -> tuple:
    """把声音播到哪块设备（虚拟声卡的"播放"端）⇒ (索引, 名字, 说明)。"""
    try:
        import sounddevice as sd
    except Exception as e:
        return -1, "", "没装 sounddevice（%s）⇒ 播不进虚拟声卡" % type(e).__name__
    want = str(_cfg().get("out_device") or "").strip().lower()
    try:
        devs = sd.query_devices()
    except Exception as e:
        return -1, "", "sounddevice 查不到设备：%s" % e
    for i, d in enumerate(devs):
        n = str(d.get("name") or "")
        if int(d.get("max_output_channels") or 0) <= 0:
            continue
        if want:
            if want in n.lower():
                return i, n, ""
            continue
        if "cable" in n.lower() or "scream" in n.lower():
            return i, n, ""
    if want:
        return -1, "", "配置里指定的输出设备没找到：%s" % want
    return -1, "", "没找到虚拟声卡的播放端（名字里含 CABLE / Scream 的那块）"


def status(cfg=None) -> dict:
    """能不能发真语音条 ⇒ {ok, why, mic, out_dev, out_idx, engine, calibrated}。"""
    c = _cfg(cfg)
    mic, why_mic = _virtual_mic()
    idx, name, why_out = _out_device()
    eng = {}
    try:
        from . import voice_models as vm
        eng = vm.status() or {}
    except Exception as e:
        eng = {"ok": False, "why": "合成引擎查不了：%s" % e}
    calibrated = bool(c.get("record_btn"))
    why = ""
    if not mic:
        why = why_mic
    elif idx < 0:
        why = why_out
    elif not eng.get("ok"):
        why = "没有可用的合成引擎：%s" % (eng.get("why") or "未知")
    # ⚠️ 位置**不再是前置条件**（2026-09-17）：`_enter_record` 每次发送前都会扫输入条图标行现算，
    #   配置里的 record_btn 只当兜底 ⇒ 这里不再因为"没标定"而拒发（那条老提示会把能用的人挡在门外）。
    return {"ok": not why, "why": why, "mic": mic, "out_dev": name, "out_idx": idx,
            "engine": (eng.get("engine") or eng.get("backend") or ""), "calibrated": calibrated,
            "auto_locate": True,
            "switch": bool(c.get("enabled"))}


def _read_pcm(path: str):
    """读成 (采样率, 声道, 样本宽度, 裸 PCM 字节)。"""
    with wave.open(path, "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())


def _ensure_wav(path: str) -> tuple:
    """不是 RIFF 就用 ffmpeg 转一份 wav（合成端可能是 mp3）。⇒ (wav 路径, 说明)"""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except Exception as e:
        return "", "音频读不了：%s" % e
    if head == b"RIFF":
        return path, ""
    try:
        from . import ffmpeg_bin as fb
        ff = fb.path()
    except Exception:
        ff = ""
    if not ff:
        return "", "产物不是 wav，而这台机器没有 ffmpeg 可转"
    out = os.path.splitext(path)[0] + "_pcm.wav"
    import subprocess
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", path, "-ar", "22050", "-ac", "1", out],
                       capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return "", "ffmpeg 转 wav 失败：%s" % e
    return (out, "") if os.path.exists(out) else ("", "ffmpeg 没产出 wav")


def play_to_cable(wav_path: str, out_name: str = "") -> tuple:
    """把 wav 播进虚拟声卡（阻塞到播完）。⇒ (ok, 说明)"""
    try:
        import sounddevice as sd
    except Exception as e:
        return False, "没装 sounddevice：%s" % e
    idx, name, why = _out_device()
    if idx < 0:
        return False, why
    try:
        rate, ch, width, pcm = _read_pcm(wav_path)
    except Exception as e:
        return False, "wav 读不了（%s）：%s" % (type(e).__name__, e)
    dtype = {1: "int8", 2: "int16", 4: "int32"}.get(width, "int16")
    try:
        with sd.RawOutputStream(samplerate=rate, channels=ch, dtype=dtype, device=idx) as st:
            st.write(pcm)
        return True, "已播到 %s（%.2f 秒）" % (name, len(pcm) / float(max(1, rate * ch * width)))
    except Exception as e:
        return False, "播到 %s 失败：%s" % (name, e)


def _real_click(wechat, gui, rel_x: int, rel_y: int) -> tuple:
    """**真鼠标**点一下就回来（用完立刻把光标还原到原位）。

    ⛔ 为什么必须真点（2026-09-17 实测 A/B）：微信输入区那两个控件（"进录音态的圆圈"和录音态里的
    绿色发送）**对投递点击只出悬停高亮、不进录音态**；同一位置改用真鼠标点一下就进了（绿簇出现）。
    ⇒ 真语音条这条路**必然要动两下光标**，所以它：
      ① 必须显式开启（`voice_strip.real_click`，默认 true 但控制台写明"会动两下光标"）；
      ② 用完**立刻把光标放回原位**并在日志/返回里说明（不许悄悄动）。

    实现落在 `ui_adapt.click_real_hold`（L0 唯一下沉点）：那边动手前会过 `real_guard`
    （确认这点真属于微信，用户正在用鼠标导致光标没到位就**不打这一枪**），并负责把光标放回去。
    """
    try:
        from . import ui_adapt
    except Exception as e:                                    # pragma: no cover
        return False, "拿不到 ui_adapt：%s" % e
    return ui_adapt.click_real_hold(gui, int(rel_x), int(rel_y))


def _green_center(gui):
    """在画面里找那个**绿色发送**簇的中心（渲染相对像素）⇒ 录音态里的发送按钮，比硬编码比例稳。"""
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return None
        w, h = img.size
        img = img.crop((int(w * 0.55), int(h * 0.80), w, h)).convert("RGB")
        px = img.load()
        bw, bh = img.size
        xs, ys = [], []
        for y in range(0, bh, 2):
            for x in range(0, bw, 2):
                rr, gg, bb = px[x, y]
                if gg > rr + 25 and gg > bb + 25 and gg > 90:
                    xs.append(x)
                    ys.append(y)
        if len(xs) < 10:
            return None
        return (int(w * 0.55) + sum(xs) // len(xs), int(h * 0.80) + sum(ys) // len(ys))
    except Exception:
        return None


def _click(wechat, gui, rel_x: int, rel_y: int, cfg=None) -> tuple:
    """点一下：默认走**真鼠标**（投递对这俩控件无效，见 `_real_click`）；配置关掉才用投递。"""
    c = _cfg(cfg)
    if bool(c.get("real_click", True)):
        return _real_click(wechat, gui, rel_x, rel_y)
    try:
        return wechat._click(gui, int(rel_x), int(rel_y))
    except Exception as e:
        return False, "投递点击失败：%s" % e


def _green_cluster(gui) -> bool:
    """输入区右半边有没有**绿色簇**（进录音态后那个绿色↑发送）⇒ 用它当"进没进录音态"的判据。"""
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return False
        w, h = img.size
        band = img.crop((int(w * 0.55), int(h * 0.86), w, h)).convert("RGB")
        px = band.load()
        bw, bh = band.size
        hits = 0
        for y in range(0, bh, 2):
            for x in range(0, bw, 2):
                r, g, b = px[x, y]
                if g > r + 25 and g > b + 25 and g > 90:
                    hits += 1
        return hits >= 12
    except Exception:
        return False


def _meter_level(gui) -> int:
    """录音态里那串**绿色音量点**亮了多少像素 ⇒ 判"音频到底有没有进微信的麦克风"。

    为什么要有它（2026-09-17）：整条链能"发出一个语音条"，但**没人能保证条里有声音**——
    虚拟声卡没被微信选成麦克风时，发出去的就是一条静音语音条（比文件更糟：对方点开什么都听不到，
    而我们自己还报"成功"）。这条读数让产品能**在发之前**发现并如实拒发。
    """
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return 0
        w, h = img.size
        box = (int(w * METER_X[0]), int(h * METER_Y[0]), int(w * METER_X[1]), int(h * METER_Y[1]))
        band = img.crop(box).convert("RGB")
        px = band.load()
        bw, bh = band.size
        n = 0
        for y in range(0, bh, 2):
            for x in range(0, bw, 2):
                r, g, b = px[x, y]
                if g > r + 25 and g > b + 25 and g > 90:
                    n += 1
        return n
    except Exception:
        return 0


def _green_send(gui):
    """录音态里那个**绿色发送 ↑**的坐标（渲染相对像素）。

    取**最右**的绿簇，不用全部绿像素的均值——左边那串音量点也是绿的，均值会被它带偏
    （实测均值落在 (1112,831)＝比例 0.934，而 ↑ 与音量点混在一张图里时这个均值不稳）。
    """
    try:
        from . import chat_header as ch
        img = ch.grab_render(gui)
        if img is None:
            return None
        w, h = img.size
        band = img.crop((int(w * GREEN_X_MIN), int(h * 0.84), w, h)).convert("RGB")
        px = band.load()
        bw, bh = band.size
        xs, ys = [], []
        for y in range(0, bh, 2):
            for x in range(0, bw, 2):
                r, g, b = px[x, y]
                if g > r + 25 and g > b + 25 and g > 90:
                    xs.append(x)
                    ys.append(y)
        if len(xs) < 8:
            return None
        return (int(w * GREEN_X_MIN) + sum(xs) // len(xs), int(h * 0.84) + sum(ys) // len(ys))
    except Exception:
        return None


def _play_with_meter(gui, wav: str, out_name: str = "") -> tuple:
    """**边播边量**音量点 ⇒ (播放结果, 播放前底色, 播放中峰值)。"""
    import threading
    res = {}

    def _run():
        try:
            res["r"] = play_to_cable(wav, out_name)
        except Exception as e:
            res["r"] = (False, "播放异常：%s" % e)

    base = _meter_level(gui)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    peak = 0
    while t.is_alive():
        peak = max(peak, _meter_level(gui))
        time.sleep(0.15)
    t.join(timeout=8.0)
    return res.get("r", (False, "播放线程没有返回")), base, peak


def _enter_record(wechat, gui, cfg=None, rw: int = 0, rh: int = 0, wait_s: float = 8.0) -> tuple:
    """进录音态：**逐个候选位置真点**，谁点出绿簇就是它 ⇒ (ok, 说明, x比例)。

    候选顺序＝扫图标行现算 → 兜底表（全 ≥0.8，点空/点发送都无害）。

    ⚠️ 2026-09-17 实测的关键一环（用户问「怎么又在发音频文件，是不是没改代码」时钉死）：
    `ui_adapt.click_real_hold` 动手前要过 `real_guard`，而**用户正在用鼠标时 `SetCursorPos`
    会返回 0**（Windows 层面拒绝）⇒ guard 按红线**不打这一枪**（原话：「已放弃这一枪，不打扰你」）。
    于是"点了却没反应"的真实原因往往是"**那一枪压根没打出去**"。⇒ 这里**等一等再试**：
    被 guard 拦下时每隔 0.6s 重试一次，最多等 `wait_s` 秒（用户手一离开鼠标，下一枪就能中）；
    只有"打出去了但没进态"才换下一个候选位置。
    """
    c = _cfg(cfg)
    rb = c.get("record_btn") or [0.878, 0.943]
    try:
        ry = int(rh * float(rb[1]))
    except Exception:
        ry = int(rh * 0.943)
    x_scan, src = locate_record(gui, cfg)
    cands = [x_scan] + [x for x in RECORD_FALLBACKS if abs(x - x_scan) > 0.01]
    why, fired_never = [], True
    for x in cands:
        t0 = time.time()
        shot = 0
        while True:
            shot += 1
            try:
                ok, why1 = _click(wechat, gui, int(rw * x), ry, c)
            except Exception as e:
                ok, why1 = False, "点击异常：%s" % str(e)[:40]
            time.sleep(1.0)
            if _green_cluster(gui):
                tag = src if abs(x - x_scan) < 1e-9 else "兜底候选"
                if shot > 1:
                    tag += "·第%d枪才中" % shot
                return True, "在 x=%.3f 进录音态（%s）" % (x, tag), x
            if ok:
                fired_never = False
                # 打出去了但没进态：同一位置再补一枪（自绘控件偶发丢第一下），仍不进就换候选
                if shot >= 2:
                    why.append("x=%.3f 打了两枪没反应" % x)
                    break
                continue
            # 没打出去（多半是 real_guard：你正在用鼠标）⇒ 等一等再试同一个位置
            if time.time() - t0 >= float(wait_s):
                why.append("x=%.3f 一直没能打出去（%s）" % (x, str(why1)[:70]))
                break
            time.sleep(0.6)
    tail = "（这几枪都被防打扰闸拦下了：你正在用鼠标 ⇒ 按红线不打；手一离开鼠标我就重试）" if fired_never else ""
    return False, "候选位置都没进录音态：%s%s" % ("；".join(why), tail), x_scan


def calibrate(wechat, save: bool = True, log=None) -> dict:
    """**标定**那个"进录音态"的圆圈：沿输入区右下扫几个候选点，谁点了出现绿簇就是它。

    进录音态后**立刻点 ✕（取消）退出**，不留残余；标定结果写回配置 `voice_strip.record_btn`。
    """
    c = _cfg()
    send = tuple(c.get("send_btn") or DEFAULT_SEND)
    gui = wechat._get_gui()
    gui._update_render_rect()
    # ⚠️ 标定必须在"**看得见**"的前提下做：控制台/浏览器常常盖着微信（实测：渲染区被遮挡时
    #    PrintWindow 都拿不到帧 ⇒ 绿簇永远判 False、四个候选点全假阴性）。⇒ 先借前台、标完立刻还。
    import ctypes
    _u32 = ctypes.windll.user32
    _stashed = 0
    try:
        from . import wechat as _w
        _stashed = int(_w._fg_now() or 0)
        _hwnd = int(getattr(gui, "main_hwnd", 0) or 0)
        if _hwnd:
            _w._force_foreground(_u32, _hwnd)
            time.sleep(0.7)
    except Exception:
        _stashed = 0
    r = gui.render_rect or (0, 0, 0, 0)
    rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
    tried = []
    _result = None
    for rx in CALIB_XS:
        pt = (int(rw * rx), int(rh * send[1]))
        try:
            ok, why = _click(wechat, gui, pt[0], pt[1], c)
        except Exception as e:
            tried.append((rx, "点击异常：%s" % e))
            continue
        time.sleep(0.7)
        hit = _green_cluster(gui)
        tried.append((rx, "绿簇=%s" % hit))
        if hit:
            # 退出录音态（✕ 取消在最左）
            try:
                _click(wechat, gui, int(rw * 0.30), int(rh * send[1]), c)
            except Exception:
                pass
            time.sleep(0.4)
            if save:
                cfg = get_config()
                seg = cfg.setdefault("voice_strip", {})
                seg["record_btn"] = [rx, send[1]]
                seg.setdefault("send_btn", list(send))
                try:
                    from .config import save_config
                    save_config(cfg)
                except Exception as e:
                    if log:
                        log("warn", "标定结果写配置失败：%s", e)
            _give_back(_stashed)
            return {"ok": True, "record_btn": [rx, send[1]], "tried": tried}
    _give_back(_stashed)
    return {"ok": False, "why": "几个候选点都没进录音态（微信窗口被挡住 / 没打开聊天 / 位置完全不同？）",
            "tried": tried}


def _give_back(stashed_fg: int):
    """把借走的前台还给用户原来的那个窗口（盯着还，最多 2.5 秒）——标定/试发之后必调。"""
    if not stashed_fg:
        return
    try:
        from . import wechat as _w
        _w._FG_STASH["hwnd"] = int(stashed_fg)
        _w._restore_fg_until("语音条标定/试发回还", timeout=2.5, keep=False)
    except Exception:
        pass


def _borrow_foreground(gui) -> int:
    """**借一下前台**（把微信主窗置前），返回原来那个前台窗口句柄（给 `_give_back` 还回去）。

    为什么非借不可（2026-09-17 A/B 实测，`_scratch/uipi_probe.py`）：真点要 `SetCursorPos`，而
    **最前面的窗口是提权进程时 Windows 直接拒绝**（`GetLastError=5` ACCESS_DENIED）——本机最常见的就是
    **我们自己的控制台**（`一键启动.exe` 带提权 manifest 起的 WebView2 窗）。实测：控制台在前 ⇒ 三处候选
    连 30 秒全失败；把微信置前 ⇒ 立刻成功。⇒ 发之前借、发完还（与 `calibrate()` 同一套借还机制）。
    """
    try:
        import ctypes
        from . import wechat as _w
        stashed = int(_w._fg_now() or 0)
        hwnd = int(getattr(gui, "main_hwnd", 0) or 0)
        if hwnd and stashed != hwnd:
            _w._force_foreground(ctypes.windll.user32, hwnd)
            time.sleep(0.6)
        return stashed
    except Exception:
        return 0


def send(wechat, chat_id: str, text: str, cfg=None, timeout: float = 60.0) -> tuple:
    """发一条**真语音条**。⇒ (ok, 说明, info)

    流程：查三闸 → 合成 → 播进虚拟声卡（同时微信在录音）→ 点发送 → **只认 DB 回读 `type=语音`**。
    """
    c = _cfg(cfg)
    if not c.get("enabled"):
        return False, "真语音条默认关（控制台「语音回复」里打开「发真语音条」）——关了就不发，不假装。", {}
    st = status(cfg)
    if not st["ok"]:
        return False, st["why"], st
    if not str(text or "").strip():
        return False, "要念的内容是空的", {}
    try:
        from . import voice_models as vm
        path, err, info = vm.make(str(text).strip(), cfg=cfg, timeout=int(timeout))
    except Exception as e:
        return False, "合成失败：%s" % e, {}
    if not path:
        return False, "合成没产出音频：%s" % (err or "未知"), info or {}
    wav, why2 = _ensure_wav(path)
    if not wav:
        return False, why2, info or {}
    try:
        gui = wechat._get_gui()
        gui._update_render_rect()
    except Exception as e:
        # 拿不到窗口（微信没开 / 适配器不可用）⇒ 如实失败，绝不抛出去（抛出去连"回退成文件"都没机会）
        return False, "拿不到微信窗口（%s）⇒ 真语音条发不了" % str(e)[:60], info or {}
    r = gui.render_rect or (0, 0, 0, 0)
    rw, rh = int(r[2] - r[0]), int(r[3] - r[1])
    rb = c.get("record_btn") or [0.878, 0.943]
    # ⓿ 会话闸（2026-09-17 补）：录音只会进**当前打开**的会话 ⇒ 目标不对就先投递切过去，
    #   切不过去就**不发**。为什么要这道闸：把语音发到错的人那里是不可逆的社交事故，
    #   而投递切会话本来就有 OCR 确认（`switch_chat_posted`），成本很低。
    try:
        name = ""
        try:
            name = wechat.display_name(chat_id) or chat_id
        except Exception:
            name = chat_id
        okc, whyc = wechat.chat_is_open(chat_id, gui=gui, name=name)
    except Exception as e:
        okc, whyc = False, "会话检查不可用：%s" % str(e)[:50]
    if not okc:
        try:
            oks, whys = wechat.switch_chat_posted(chat_id, gui=gui, name=name)
        except Exception as e:
            oks, whys = False, "切会话异常：%s" % str(e)[:60]
        if not oks:
            return False, ("当前打开的会话不是目标会话，切不过去 ⇒ **不发语音条**"
                           "（发错人不可逆；%s）" % str(whys)[:80]), info or {}
    before = _latest_voice_seq(wechat, chat_id)
    base = peak = 0
    x_used = None
    via = "alt"
    # ⭐ 借前台：①Alt 路要微信在前台（SendInput 发给焦点窗口）②真点路要 SetCursorPos
    #    （最前面是提权窗口时系统直接拒绝，见 `_borrow_foreground`）
    stashed_fg = _borrow_foreground(gui)
    try:
        # ⓵ 开录前先确保**不在**录音态：上一轮若没退干净，再点那个圆圈就变成"结束/取消"（实测踩过）
        try:
            if _green_cluster(gui):
                _click(wechat, gui, int(rw * CANCEL_X), int(rh * rb[1]), c)   # 点 ✕ 退出来
                time.sleep(0.8)
        except Exception:
            pass
        # ⓶ 进录音态：**默认走右 Alt（不动鼠标）**；不认右 Alt 的版本才退回真点
        want = str(c.get("enter_via") or "alt").lower()
        ok_in, why_in, x_used = False, "", None
        if want in ("alt", "auto"):
            ok_in, why_in, x_used = _enter_record_alt(wechat, gui, cfg)
            if ok_in:
                via = "alt"
        if not ok_in:
            if want == "alt" and bool(c.get("fallback_click", True)):
                ok2, why2, x2 = _enter_record(wechat, gui, cfg, rw, rh)
                if ok2:
                    ok_in, why_in, x_used, via = ok2, why2, x2, "click"
                else:
                    why_in = "%s；退回首击也不成：%s" % (why_in, why2)
            elif want not in ("alt", "auto"):
                ok_in, why_in, x_used = _enter_record(wechat, gui, cfg, rw, rh)
                via = "click" if ok_in else "click"
        if not ok_in:
            return False, ("没能进录音态（%s）——按住右 Alt 与真点两条路都试过了。"
                           "Alt 路的前提：**微信在前台的那几秒别切窗口**。" % why_in), info or {}
        (okp, whyp), base, peak = _play_with_meter(gui, wav)                 # ⓷ 边录边播
        if not okp:
            if via == "alt":
                _cancel_alt(gui)
            else:
                try:
                    _click(wechat, gui, int(rw * CANCEL_X), int(rh * rb[1]), c)
                except Exception:
                    pass
            return False, whyp, info or {}
        if bool(c.get("meter_guard", True)) and peak <= base:
            if via == "alt":
                _cancel_alt(gui)
            else:
                try:
                    _click(wechat, gui, int(rw * CANCEL_X), int(rh * rb[1]), c)
                except Exception:
                    pass
            return False, ("音频**没进到微信的麦克风**（录音那条音量点一直没亮：底色 %d、播放峰值 %d）"
                           "⇒ 已取消，不给你发一条静音语音条。查一下微信的麦克风是不是选成了"
                           "「CABLE Output」（关掉这道校验：voice_strip.meter_guard）" % (base, peak)), info or {}
        time.sleep(0.5)
        if via == "alt":
            _send_alt(False)                                                # ⓸ 松开右 Alt＝发送
        else:
            g = _green_send(gui) or _green_center(gui)                      # ⓸ 点绿色发送 ↑
            if not g:
                try:
                    _click(wechat, gui, int(rw * CANCEL_X), int(rh * rb[1]), c)
                except Exception:
                    pass
                return False, "录音态里找不到绿色发送按钮（已取消，没发出去）", info or {}
            _click(wechat, gui, g[0], g[1], c)
    except Exception as e:
        return False, "微信侧操作失败：%s" % e, info or {}
    finally:
        # 借走的前台**无论成败都还回去**（用户原来的窗口不能被我们占着）
        try:
            _give_back(stashed_fg)
        except Exception:
            pass
    ok, msg = _wait_voice(wechat, chat_id, before, timeout=12.0)
    if isinstance(info, dict):
        info = dict(info)
        info["meter"] = {"base": base, "peak": peak}
        info["record_x"] = x_used
        info["enter_via"] = via
    return ok, (msg if ok else ("语音条没发出去：%s" % msg)), info or {}


def _latest_voice_seq(wechat, chat_id: str):
    """当前会话里最后一条**语音**的 id（回读判据的基线）。"""
    try:
        rows = wechat._db.get_messages(chat_id, limit=20) or []
        for row in rows:
            if str(row.get("type") or "") == "语音":
                return row.get("local_id") or row.get("id")
        return 0
    except Exception:
        return 0


def _wait_voice(wechat, chat_id: str, before, timeout: float = 12.0) -> tuple:
    """等 DB 里出现**新的**语音行（只认回读，不信 GUI）。

    ⚠️ 2026-09-17 修（差点造成"明明发出去了却报失败，然后按回退设置又发一个文件"）：
    旧写法在第一次查库时**只要第一条语音行还是旧的就直接 return False** —— 而微信**写库有延迟**，
    松手那一刻库里通常还是上一条 ⇒ 假阴性。现在改成：看到旧的就**继续轮询**，直到超时。
    """
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            rows = wechat._db.get_messages(chat_id, limit=20) or []
            for row in rows:
                if str(row.get("type") or "") == "语音":
                    cur = row.get("local_id") or row.get("id")
                    last = cur
                    if before in (None, 0) or (cur and str(cur) != str(before)):
                        return True, "DB 回读确认：新语音条 local_id=%s" % cur
                    break            # 最新那条还是旧的 ⇒ 继续等（微信写库要几秒）
        except Exception:
            pass
        time.sleep(1.0)
    return False, "等了 %.0f 秒，DB 里没出现新语音条（最新还是 %s）" % (timeout, last)
