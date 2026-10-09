# -*- coding: utf-8 -*-
"""视频读取（第三方 v0.4 对账清单第 20 条的后半）：抽帧给视觉模型 + 本机离线识别音频。

原来只有"下载/转发"（`download_media` / `forward_media`），**理解视频内容没有**。做法：
1. **抽帧**：`ffmpeg` 按均匀时间点抓 N 张 PNG（默认 4 张、上限 8 张）⇒ 交给 `api.model_routes.image` 指定的视觉模型
   （没配就是主模型）"看图说话"——视频理解在现有链路里**就等于看图**，不另做一个模型调用。
2. **音频**：`ffmpeg` 抽 16k 单声道 WAV ⇒ 复用 `agent/voice.py::recognize_wav()`（Windows 内置 SAPI，离线、零下载）。
3. **诚实边界**：没有 ffmpeg ⇒ 明确返回"读不了"并给原因（**不假装看过视频**）；没有识别引擎 ⇒ 只给帧、如实说"这段音频听不出来"。
   帧抓不到（文件损坏/时长解析失败）⇒ 如实报，绝不返回空帧当成功。

纪律（沿用本项目口径）：**只读不写**（不动微信、不动用户文件；帧与音频落在临时目录）、**子进程静默**
（Windows 下 `CREATE_NO_WINDOW`，不许弹黑框）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import logging

log = logging.getLogger("persona-morph")

_lock = threading.RLock()

MAX_FRAMES = 8
DEFAULT_FRAMES = 4
DEFAULT_MAX_SECONDS = 60
#: 画面字幕采样（"内嵌字幕优先"那条路）：每秒 1 帧就够（字幕不会每帧都变），
#: 只 OCR **下方 45%**（字幕带一般在下部，这样能避开顶部的作者/点赞等 UI）。
SUBTITLE_FPS = 1.0
SUBTITLE_CROP_TOP = 0.55
SUBTITLE_MAX_FRAMES = 600
_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def ffmpeg_path() -> str:
    """ffmpeg 在哪（唯一解析入口：配置 → PATH → imageio-ffmpeg 自带的那份）。"""
    try:
        from .ffmpeg_bin import path as _p
        return _p()
    except Exception:
        pass
    try:
        from .voice import _which
        p = _which("ffmpeg")
        if p:
            return p
    except Exception:
        pass
    return shutil.which("ffmpeg") or ""


def _run(args, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          creationflags=_NO_WINDOW, encoding="utf-8", errors="ignore")


def duration_seconds(path: str):
    """从 `ffmpeg -i` 的输出里读时长（没有 ffprobe 也能用）。读不到返回 None。"""
    ff = ffmpeg_path()
    if not ff:
        return None
    try:
        r = _run([ff, "-hide_banner", "-i", path], timeout=30)
        m = _DUR_RE.search((r.stderr or "") + (r.stdout or ""))
        if not m:
            return None
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        return None


def asr_status() -> dict:
    """本机离线识别（SAPI）能不能用——沿用语音那条链，不另起一套。"""
    try:
        from . import voice
        ok_flag, why = voice.sapi_file_ok()
        return {"ok": bool(ok_flag), "why": str(why or "")}
    except Exception as e:
        return {"ok": False, "why": "语音模块不可用：%s" % str(e)[:80]}


def asr_backend() -> dict:
    """**现在能用哪个**语音识别引擎 —— 优先真 ASR，退回系统那套（**现算，不写死**）。

    返回 `{"ok", "kind", "detail"}`；`kind ∈ {faster_whisper, openai_whisper, sapi, none}`。

    ⛔ 为什么必须区分：**SAPI 是"听写"引擎**，对长音频/音乐/多人又慢又不准（它的本职是
    "对着麦克风说一句、识别成文字"）⇒ 只配当**兜底**；真 ASR（faster-whisper / openai-whisper）
    才是"把一段音频转成带时间戳的字幕"的正解。
    ⛔ 但"装没装"是**环境事实** ⇒ 这里**现算**：装了就优先走真的，没装就如实说在用兜底 ——
    **不许在提示词/面板里声称"我们有语音识别"而实际用的是听写引擎**。
    ⛔ 探测必须用 `find_spec`，**不许 `import` 试**：`import whisper` 会把 torch 拖进常驻内存
       （实测 +446 MB —— whisper 硬依赖 torch）；本函数被状态面板调用，用户没点语音也会触发。
    """
    import importlib.util as _ilu
    if _ilu.find_spec("faster_whisper") is not None:
        return {"ok": True, "kind": "faster_whisper",
                "detail": "faster-whisper（CTranslate2，CPU 友好、体积小）"}
    if _ilu.find_spec("whisper") is not None:
        return {"ok": True, "kind": "openai_whisper", "detail": "openai-whisper"}
    st = asr_status()
    if st.get("ok"):
        return {"ok": True, "kind": "sapi",
                "detail": "Windows SAPI（**听写**引擎，仅兜底：长音频/音乐/多人不准）"}
    return {"ok": False, "kind": "none", "detail": st.get("why") or "没有任何可用的识别引擎"}


def probe() -> dict:
    ff = ffmpeg_path()
    asr = asr_status()
    why = ""
    if not ff:
        why = "本机没有 ffmpeg（抽帧与音频都要它）"
    return {"ffmpeg": ff, "asr": asr, "backend": asr_backend(), "ready": bool(ff), "why": why,
            "note": "抽帧走 ffmpeg；音频识别优先真 ASR（whisper），没有才退 SAPI；都离线，不出网"}


#: 读视频失败时**用户能直接照做**的话（照外部技能"错误路由表"那套：错误码 → 一句可执行的建议）。
#   ⛔ 口径：**只给用户能做的动作**，不写"内部异常名"（那个留在日志里）。
VIDEO_READ_HINTS = {
    "no_ffmpeg": "本机没有 ffmpeg —— 抽帧与探流都要它。装一个 ffmpeg，或让我用自带的那份。",
    "no_file": "这个视频不在本地：先把它下载/转存过来，再让我读。",
    "bad_media": "这条文件读不出时长（可能损坏，或根本不是视频）：换一个文件试。",
    "no_subtitle": "画面里没读到字幕 —— 这条视频可能**没有内嵌字幕**；那就该走语音那条路（需要 ASR）。",
    "ocr_unavailable": "本机的 OCR 识别组件不可用（WinRT 识别组件缺失）：装上识别语言包再试。",
    "no_frames": "抽帧没产出图片（ffmpeg 跑失败）：确认文件完整、或换个格式再试。",
}


def media_kind(path: str) -> dict:
    """探流：这条文件**有没有视频轨 / 音频轨** —— "纯音频就直接进 ASR，跳过抽帧与 OCR"。

    为什么需要它：语音条、播客、录音（mp3/m4a/wav）**根本没有画面可抽**，
    硬走"抽帧 ＋ 画面 OCR"是白跑一趟；它们的正解是**直接进识别管线**。
    这个判断就是那条岔路口（外部技能 `media_probe` 里也是这一步）。

    返回 `{"ok", "has_video", "has_audio", "duration", "error"}`；
    ⛔ **探不出就 `ok=False`**（不猜、也不假装"没有视频"）。
    """
    none = {"ok": False, "has_video": False, "has_audio": False, "duration": 0.0}
    ff = ffmpeg_path()
    if not ff:
        return dict(none, error="no_ffmpeg")
    if not os.path.isfile(path):
        return dict(none, error="no_file")
    try:
        r = _run([ff, "-hide_banner", "-i", path], timeout=30)
    except Exception as e: # noqa: BLE001
        return dict(none, error="bad_media")
    out = (r.stderr or "") + (r.stdout or "")
    has_v = "Video:" in out
    has_a = "Audio:" in out
    return {"ok": bool(has_v or has_a), "has_video": has_v, "has_audio": has_a,
            "duration": duration_seconds(path) or 0.0, "error": ""}


def probe_subtitle_band(path: str, out_dir: str, samples: int = 6) -> dict:
    """**先探帧、定位字幕带**（别把裁切比例写死）：抽几个时间点全图 OCR，
    找出"文字最密的那条横带"。

    为什么必须探：录屏类视频的正片嵌在中间、**字幕不一定在底部**（外部技能实测抖音录屏常在
    y≈0.72 一带），写死一个比例必然把字幕裁掉或把 UI 裁进来。
    返回 `{"ok", "top", "reason"}` —— `top` 是**归一化比例**，直接喂给
    `extract_subtitles(..., crop_top=top)`。
    ⛔ 探不出（本机没 OCR、或整帧都没字）⇒ `ok=False` 并**如实说明**（调用方退回默认值）。
    """
    ff = ffmpeg_path()
    if not ff:
        return {"ok": False, "top": 0.0, "reason": "no_ffmpeg"}
    dur = duration_seconds(path)
    if not dur or dur <= 0:
        return {"ok": False, "top": 0.0, "reason": "bad_media"}
    try:
        from . import chat_ocr as _ocr
        from PIL import Image
    except Exception as e: # noqa: BLE001
        return {"ok": False, "top": 0.0, "reason": "ocr_unavailable"}
    os.makedirs(out_dir, exist_ok=True)
    ys = []
    for i in range(max(1, int(samples))):
        t = dur * (i + 0.5) / max(1, int(samples))
        fp = os.path.join(out_dir, "probe_%02d.png" % i)
        try:
            _run([ff, "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % t,
                  "-i", path, "-frames:v", "1", "-y", fp], timeout=60)
        except Exception:
            continue
        if not (os.path.isfile(fp) and os.path.getsize(fp) > 0):
            continue
        try:
            im = Image.open(fp)
            w, h = im.size
            items = _ocr.recognize(im) or []
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            continue
        for it in items:
            try:
                txt = str(it[0]) if not isinstance(it, dict) else str(it.get("text") or "")
                y0 = (it[2] if not isinstance(it, dict) else it.get("y")) or 0
                hh = (it[4] if not isinstance(it, dict) else it.get("h")) or 0
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
            if not txt or not _has_cjk(txt):
                continue
            yc = (float(y0) + float(hh) / 2.0) / max(1.0, float(h))
            if 0.25 <= yc <= 0.98:           # 只在下 3/4 找（上半屏多是标题/UI）
                ys.append(yc)
    if len(ys) < 2:
        return {"ok": False, "top": 0.0,
                "reason": "no_subtitle" if not ys else "too_few"}
    # 取**下四分位**：字幕是"成片出现的一条带"，取靠下的那一撮更稳（避开偶发的中部文字）
    ys.sort()
    q = ys[int(len(ys) * 0.25)]
    top = max(0.30, min(0.90, q - 0.06))     # 带一点上边距，别把字切一半
    return {"ok": True, "top": round(top, 3),
            "reason": "命中 %d 处含中文字（下四分位 %.3f）" % (len(ys), q)}


#: 场景采帧：先按 `probe_fps` 密采**小图**做帧间差分，再回原片按选中时刻取**全尺寸**帧。
SCENE_PROBE_FPS = 0.5
SCENE_MAX_FRAMES = 8


def extract_scene_frames(path: str, out_dir: str, max_frames: int = SCENE_MAX_FRAMES,
                         probe_fps: float = SCENE_PROBE_FPS) -> dict:
    """**按场景采关键帧**（不是均匀抽）：先密采一序列小图算**帧间差分**，
    取"画面真的换了"的那几处，再回原片按那些时刻取全尺寸帧。

    为什么比均匀好：一条十分钟的视频里**大半时间画面是同一个场景**，
    均匀抽 4 张常常 4 张全落在同一场景；而按变化取，选中的才是"镜头/画面换了"的地方
    （外部技能 `visual_analyzer` 也是按 `sampling_interval` 配场景边界做的）。

    返回 `{ok, frames:[png...], cues:[秒...], duration, error}`；**失败如实报**（不返回空当成功）。
    """
    ff = ffmpeg_path()
    if not ff:
        return {"ok": False, "frames": [], "cues": [], "error": "no_ffmpeg"}
    if not os.path.isfile(path):
        return {"ok": False, "frames": [], "cues": [], "error": "no_file"}
    dur = duration_seconds(path)
    if not dur or dur <= 0:
        return {"ok": False, "frames": [], "cues": [], "error": "bad_media"}
    n = max(1, min(MAX_FRAMES, int(max_frames or SCENE_MAX_FRAMES)))
    small = os.path.join(out_dir, "scene_small")
    big = os.path.join(out_dir, "scene_big")
    os.makedirs(small, exist_ok=True)
    os.makedirs(big, exist_ok=True)
    try:
        # ① 密采小图（160 宽足够算差分，且快）
        _run([ff, "-hide_banner", "-loglevel", "error", "-i", path,
              "-vf", "fps=%.6f,scale=160:-1" % max(0.05, float(probe_fps)),
              "-frames:v", str(min(600, int(dur * max(0.05, float(probe_fps))) + 2)),
              "-y", os.path.join(small, "s_%04d.png")], timeout=max(60, int(dur) + 30))
    except Exception as e: # noqa: BLE001
        return {"ok": False, "frames": [], "cues": [], "duration": dur,
                "error": "抽小图异常：%s" % str(e)[:70]}
    names = sorted(x for x in os.listdir(small) if x.startswith("s_") and x.endswith(".png"))
    if len(names) < 2:
        return {"ok": False, "frames": [], "cues": [], "duration": dur, "error": "小图太少，算不了差分"}
    try:
        from PIL import Image, ImageChops, ImageStat
    except Exception as e: # noqa: BLE001
        return {"ok": False, "frames": [], "cues": [], "duration": dur, "error": "PIL 不可用"}
    # ② 相邻帧差分（平均绝对差 0..255）
    scores = []
    prev = None
    for i, nm in enumerate(names):
        try:
            im = Image.open(os.path.join(small, nm)).convert("L")
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            continue
        if prev is not None:
            try:
                m = ImageStat.Stat(ImageChops.difference(prev, im)).mean[0]
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                m = 0.0
            scores.append((float(m), i))
        prev = im
    if not scores:
        return {"ok": False, "frames": [], "cues": [], "duration": dur, "error": "差分算不出来"}
    # ③ 取变化最大的 n 个时刻（外加首帧），**去重**（相邻采样点挨太近只留一个）
    scores.sort(reverse=True)
    step = max(1, int(round(len(names) / float(max(1, n)))))
    picked, used = [0], {0}
    for _m, i in scores:
        if len(picked) >= n:
            break
        if any(abs(i - u) < step for u in used):
            continue
        used.add(i)
        picked.append(i)
    picked.sort()
    cues = [round(min(dur, i / max(0.05, float(probe_fps))), 2) for i in picked]
    # ④ 回原片按选中时刻取全尺寸帧
    frames = []
    for k, t in enumerate(cues):
        fp = os.path.join(big, "b_%02d.png" % k)
        try:
            _run([ff, "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % t,
                  "-i", path, "-frames:v", "1", "-y", fp], timeout=60)
        except Exception:
            continue
        if os.path.isfile(fp) and os.path.getsize(fp) > 0:
            frames.append(fp)
    if not frames:
        return {"ok": False, "frames": [], "cues": cues, "duration": dur, "error": "no_frames"}
    return {"ok": True, "frames": frames, "cues": cues, "duration": dur, "error": ""}


def extract_frames(path: str, out_dir: str, count: int = DEFAULT_FRAMES) -> dict:
    """均匀抓 count 张帧（**一次 ffmpeg 调用搞定**）。返回 {ok, frames:[png...], duration, error}。

    ⏱ **为什么改成"一次调用"**：老写法在 `for i in range(count)` 里**每帧起一个 ffmpeg 进程**
    （`-ss t -i path -frames:v 1 out`）⇒ **同一个文件被重新打开、重新解码 count 次**
    （默认 4 次，且每帧各带 60 秒超时）。改成"一次按 `fps=count/dur` 抽满 count 张"之后
    **只解一遍** —— 这是"发视频给机器人会卡住"里**最容易拿掉的一段时间**。

    ⚠️ 帧数与均匀性不变：`fps = count / dur` ⇒ 每 `dur/count` 秒一张，仍是近似均匀采样；
    `-frames:v count` 是**硬上限**，短视频也不会多产出。
    """
    ff = ffmpeg_path()
    if not ff:
        return {"ok": False, "frames": [], "error": "本机没有 ffmpeg，读不了视频（可以只发文件让对方自己看）"}
    if not os.path.isfile(path):
        return {"ok": False, "frames": [], "error": "视频文件不存在：%s" % path}
    count = max(1, min(MAX_FRAMES, int(count or DEFAULT_FRAMES)))
    dur = duration_seconds(path)
    if not dur or dur <= 0:
        return {"ok": False, "frames": [], "error": "读不出视频时长（文件可能损坏或不是视频）"}
    os.makedirs(out_dir, exist_ok=True)
    out_pat = os.path.join(out_dir, "frame_%02d.png")
    try:
        _run([ff, "-hide_banner", "-loglevel", "error", "-i", path,
              "-vf", "fps=%.6f" % (float(count) / float(dur)),
              "-frames:v", str(count), "-y", out_pat],
             timeout=max(60, int(dur) + 30))
    except Exception as e:
        return {"ok": False, "frames": [], "duration": dur,
                "error": "抽帧异常：%s" % str(e)[:80]}
    frames = []
    for name in sorted(os.listdir(out_dir)):
        if name.startswith("frame_") and name.endswith(".png"):
            fp = os.path.join(out_dir, name)
            if os.path.isfile(fp) and os.path.getsize(fp) > 0:
                frames.append(fp)
    if not frames:
        return {"ok": False, "frames": [], "duration": dur, "error": "抽帧失败（ffmpeg 没产出图片）"}
    return {"ok": True, "frames": frames[:count], "duration": dur, "error": ""}


def extract_subtitles(path: str, out_dir: str, max_seconds: float = 0,
                      fps: float = SUBTITLE_FPS, crop_top: float = 0.0) -> dict:
    """**从画面里读字幕**—— 不抽音频、不做语音转写。

    为什么这是首选（短视频/短剧尤其）：**字幕本来就画在画面里**（官方文字，比听写准），
    而 OCR 只读图、**不下载任何模型**（走 `chat_ocr.recognize`，本机 WinRT 引擎即有）
    ⇒ 比"抽音频 ＋ SAPI 转写"**快一个数量级**，也不受"纯音乐/多人/噪声"干扰。

    做法（照外部技能那套的**最小可用版**）：
      ① **一次 ffmpeg** 按 `fps`（默认每秒 1 帧）抽到 `max_seconds` 上限；
      ② 每帧只裁**下方 45%**（字幕带一般在下部，顺手避开顶部作者/点赞 UI）后 OCR；
      ③ **只留含中文的行**，且**相邻重复的丢掉**（字幕一秒内常不变）；
      ④ 输出带时间轴的 `[{t, text}]`。

    ⚠️ **诚实边界**：画面里真没字幕（或字幕太糊）⇒ 返回 `ok=False` 并说明
    「该走语音那条路」，**绝不返回空当成功**。
    """
    ff = ffmpeg_path()
    if not ff:
        return {"ok": False, "lines": [], "frames": 0, "error": "本机没有 ffmpeg，读不了视频"}
    if not os.path.isfile(path):
        return {"ok": False, "lines": [], "frames": 0, "error": "视频文件不存在：%s" % path}
    dur = duration_seconds(path)
    if not dur or dur <= 0:
        return {"ok": False, "lines": [], "frames": 0, "error": "读不出视频时长（文件可能损坏）"}
    cap = float(max_seconds) if max_seconds and max_seconds > 0 else float(dur)
    cap = max(1.0, min(cap, float(dur)))
    os.makedirs(out_dir, exist_ok=True)
    try:
        _run([ff, "-hide_banner", "-loglevel", "error",
              "-t", "%.3f" % cap, "-i", path,
              "-vf", "fps=%.6f" % max(0.01, float(fps)),
              "-frames:v", str(SUBTITLE_MAX_FRAMES),
              "-y", os.path.join(out_dir, "sub_%04d.png")],
             timeout=max(60, int(cap) + 30))
    except Exception as e:
        return {"ok": False, "lines": [], "frames": 0, "error": "抽帧异常：%s" % str(e)[:80]}
    frames = []
    for name in sorted(os.listdir(out_dir)):
        if name.startswith("sub_") and name.endswith(".png"):
            fp = os.path.join(out_dir, name)
            if os.path.isfile(fp) and os.path.getsize(fp) > 0:
                frames.append(fp)
    if not frames:
        return {"ok": False, "lines": [], "frames": 0, "error": "抽帧失败（没有产出图片）"}
    try:
        from . import chat_ocr as _ocr
    except Exception as e: # noqa: BLE001
        return {"ok": False, "lines": [], "frames": len(frames),
                "error": "OCR 模块不可用：%s" % str(e)[:80]}
    try:
        from PIL import Image
    except Exception as e: # noqa: BLE001
        return {"ok": False, "lines": [], "frames": len(frames),
                "error": "PIL 不可用：%s" % str(e)[:80]}

    lines, prev = [], None
    for i, fp in enumerate(frames):
        try:
            im = Image.open(fp)
            w, h = im.size
            crop = im.crop((0, int(h * (crop_top or SUBTITLE_CROP_TOP)), w, h))
            # ⭐ **放大 2× 再认**（外部技能实测的做法）：短视频字幕字号小，
            #   本机 WinRT OCR 在原始尺寸上会**漏字**（实测"测试字幕一二三"只读出"测试字幕一"）；
            #   放大后小字变成"常态字"，识别率明显回来。
            crop = crop.resize((crop.width * 2, crop.height * 2))
            items = _ocr.recognize(crop) or []
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            continue
        parts = []
        for it in items:
            try:
                txt = str(it[0]) if not isinstance(it, dict) else str(it.get("text") or "")
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                continue
            if txt and _has_cjk(txt):
                parts.append(txt)
        text = " ".join(parts).strip()
        if text and text != prev:           # 相邻重复丢掉（字幕一秒内常不变）
            lines.append({"t": round(i / max(0.01, float(fps)), 2), "text": text})
            prev = text
    if not lines:
        return {"ok": False, "lines": [], "frames": len(frames),
                "error": "画面里没读到字幕（这条视频可能没有内嵌字幕 ⇒ 该走语音那条路）",
                "hint": VIDEO_READ_HINTS["no_subtitle"]}
    return {"ok": True, "lines": lines, "frames": len(frames), "error": ""}


def extract_audio(path: str, out_wav: str, max_seconds: int = DEFAULT_MAX_SECONDS) -> bool:
    ff = ffmpeg_path()
    if not ff:
        return False
    try:
        r = _run([ff, "-hide_banner", "-loglevel", "error", "-t", str(max(1, int(max_seconds))),
                  "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-y", out_wav], timeout=120)
        return r.returncode == 0 and os.path.isfile(out_wav) and os.path.getsize(out_wav) > 0
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False


def read(path: str, max_frames: int = DEFAULT_FRAMES, max_seconds: int = DEFAULT_MAX_SECONDS,
         work_dir: str | None = None) -> dict:
    """读一个视频：抽帧 + 音频转文字。**任何一步做不到都如实写进 note，不假装成功**。"""
    started = time.time()
    tmp = work_dir or tempfile.mkdtemp(prefix="pm-video-")
    out = {"ok": False, "frames": [], "audio_text": "", "audio_ok": False, "audio_why": "",
           "duration": None, "error": "", "note": "", "dir": tmp, "seconds": 0.0}
    fr = extract_frames(path, tmp, count=max_frames)
    out["duration"] = fr.get("duration")
    if not fr.get("ok"):
        out["error"] = fr.get("error") or "抽帧失败"
        out["seconds"] = round(time.time() - started, 2)
        return out
    out["frames"] = fr["frames"]
    out["ok"] = True
    wav = os.path.join(tmp, "audio.wav")
    if extract_audio(path, wav, max_seconds=max_seconds):
        try:
            from . import voice
            # ⚠️ 约定：`recognize_wav()` 返回 **(文本, 错误说明)**。查出一个真 bug——
            # 这里原来写成 `ok_flag, text = ...`（顺序反了）⇒ 识别到的文本被当成"成功标志"、
            # 错误说明被当成"文本"，于是**音频识别结果永远传不出来**（`audio_text` 恒为空）。
            text, aerr = voice.recognize_wav(wav, max_seconds=max_seconds)
            out["audio_text"] = str(text or "")
            out["audio_ok"] = bool(out["audio_text"].strip())
            if aerr:
                out["audio_why"] = str(aerr)
            elif not out["audio_text"].strip():
                # 识别跑通了但一个字都没听出来（纯音乐/环境声很常见）⇒ 也要如实说，别让它看起来"没提音频"
                out["audio_why"] = "音频识别跑通了但没听出可辨认的说话内容（可能只是音乐/环境声）"
            elif not _audio_text_trustworthy(out["audio_text"]):
                # ⚡ 低置信度档：SAPI 对**纯音乐/环境声**常常**不返回空**，而是
                #   **乱猜一个字**（实测 440Hz 正弦 ⇒ `'子'`）。老口径只看"空不空"⇒ 这种乱猜
                #   会被当成"有人说了这个字"传进 prompt（模型收到「视频里的说话内容：子」），
                #   正好踩中 prompt 里那句「不确定的地方别猜」。
                #   ⇒ 非空但**明显不可信**时，不写成"说话内容"，改如实说听不出。
                #   ⚠️ 阈值刻意保守（≤2 字 且 无实义）——宁可漏掉一句真实的两字短句（如"好"），
                #     也不许把环境声当成人话（**宁可不说，绝不乱说**，与本项目 fail-closed 同源）。
                out["audio_text"] = ""
                out["audio_ok"] = False
                out["audio_why"] = ("音频里没听出可辨认的说话内容（识别只给出「%s」，"
                                    "太短且无实义，很可能是音乐/环境声）"
                                    % str(text or "").strip()[:8])
        except Exception as e:
            out["audio_why"] = "识别异常：%s" % str(e)[:80]
    else:
        st = asr_status()
        out["audio_why"] = "抽不出音频轨（视频可能没有声音）" if st["ok"] else str(st["why"])
    bits = ["抽了 %d 帧（时长约 %s 秒）" % (len(out["frames"]),
                                        ("%.1f" % out["duration"]) if out["duration"] else "?")]
    if out["audio_ok"] and out["audio_text"]:
        bits.append("音频识别到：「%s」" % out["audio_text"][:80])
    elif out["audio_why"]:
        bits.append("音频没能识别：%s" % out["audio_why"])
    out["note"] = "；".join(bits)
    out["seconds"] = round(time.time() - started, 2)
    return out


_MEANINGLESS_RE = re.compile(r"^[\W_\d]+$")   # 纯标点/空白/数字（一个实义字都没有）

# ⛔ 真实应答词白名单：
#   真人的应答**恰恰高频且短**（好/嗯/对/行/是/收到…），
#     它们是**最需要被正确回应**的那些话，绝不能因为"短"就被判掉。
#   本表用来区分「SAPI 对纯音乐乱猜的孤立字」（如 `'子'`）与「真人真实应答的单字」（如 `'好'`）。
_COMMON_SHORT_REPLIES = frozenset([
    "好", "嗯", "对", "行", "是", "中", "可", "能", "来", "去",
    "在", "有", "没", "真", "牛", "强", "妙", "绝", "赞", "顶",
    "哈", "呵", "嘿", "诶", "哦", "噢", "呀", "啊", "嘛", "呗", "哈",
    "好嘞", "好吧", "好的", "行吧", "嗯嗯", "对对", "是是",
    "收到", "了解", "明白", "懂了", "知道", "可以", "没问题", "谢谢", "多谢",
    "神了", "绝了", "牛啊", "厉害", "牛逼", "太强", "很强",
])


def _audio_text_trustworthy(text: str) -> bool:
    """音频转写结果**是否可信**。

    ⛔ 判据的来历（两轮修）：
      · 第一轮：`voice.recognize_wav()`（Windows SAPI）对**纯音乐/环境声**
        常**不返回空**，而是**乱猜一个字**（实测 440Hz 正弦 ⇒ `'子'`）⇒ 老口径只判"空不空"
        ⇒ 这种乱猜会被当成"有人说了这个字"写进给模型的话里。当时用 **<3 字** 一刀切。
      · 第二轮（**已推翻上一轮**）：**"短" ≠ "不可信"** ——
        实测 9 个真实应答词（好/嗯/对/行/是/收到/神了/嗯嗯/好的）**全被上一轮判掉** ⇒ 靶子打错了。
      ⇒ 现在改成看「**像不像人话**」，不看长度：
        · **孤立的单个字符**_且_不在常用应答词表里 ⇒ 不可信（＝SAPI 乱猜的典型形态，如 `'子'`）；
        · 纯标点/数字/符号（一个实义字都没有）⇒ 不可信；
        · **其余一律保留**（含"好"、"嗯"、'神了'、'说句话'、整句等 —— 真人说什么就照传）。
    ⚠️ 为什么"孤立单字 + 不在表里"是个好判据：
      真人哪怕只说一个字，**几乎一定是常见应答词**；而乱猜倾向给出与语境无关的孤立字。
      ⇒ 这条判据**既拦住乱猜，又不会误伤"好"/"嗯"**。
    """
    t = str(text or "").strip()
    if not t:
        return False
    bare = re.sub(r"[\s\W_]+", "", t)
    if not bare:
        return False
    if _MEANINGLESS_RE.match(t):      # 全是标点/数字/符号
        return False
    # 唯一按"长度"出手的地方：**孤立的单个字符**（乱猜的典型形态）
    if len(bare) == 1:
        return bare in _COMMON_SHORT_REPLIES
    return True
    bare = re.sub(r"[\s\W_]+", "", t)
    if len(bare) < 3:
        return False
    if _MEANINGLESS_RE.match(t):
        return False
    return True


def cleanup(dir_path: str) -> None:
    """删掉临时目录（帧图与音频只在本次读取里用）。"""
    try:
        if dir_path and os.path.isdir(dir_path) and os.path.basename(dir_path).startswith("pm-video-"):
            shutil.rmtree(dir_path, ignore_errors=True)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        pass


def read_message(adapter, chat_id: str, local_id, max_frames: int = DEFAULT_FRAMES,
                 max_seconds: int = DEFAULT_MAX_SECONDS) -> dict:
    """按消息下载并读取（adapter.download_media 已存在，这里不重复实现下载）。"""
    path = ""
    try:
        path = adapter.download_media(chat_id, local_id, "video") or ""
    except Exception as e:
        return {"ok": False, "error": "下载失败：%s" % str(e)[:120], "frames": [], "note": ""}
    if not path or not os.path.isfile(path):
        return {"ok": False, "frames": [], "note": "",
                "error": "没下下来（微信本地缓存里可能已经没有这个视频了：让对方在微信里点开一次再试）"}
    return read(path, max_frames=max_frames, max_seconds=max_seconds)


def snapshot() -> dict:
    p = probe()
    p["limits"] = {"default_frames": DEFAULT_FRAMES, "max_frames": MAX_FRAMES, "max_seconds": DEFAULT_MAX_SECONDS}
    return p
