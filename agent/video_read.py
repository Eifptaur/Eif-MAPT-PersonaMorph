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

_lock = threading.RLock()

MAX_FRAMES = 8
DEFAULT_FRAMES = 4
DEFAULT_MAX_SECONDS = 60
_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


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


def probe() -> dict:
    ff = ffmpeg_path()
    asr = asr_status()
    why = ""
    if not ff:
        why = "本机没有 ffmpeg（抽帧与音频都要它）"
    return {"ffmpeg": ff, "asr": asr, "ready": bool(ff), "why": why,
            "note": "抽帧走 ffmpeg、音频识别走本机 SAPI；都离线，不出网"}


def extract_frames(path: str, out_dir: str, count: int = DEFAULT_FRAMES) -> dict:
    """均匀抓 count 张帧。返回 {ok, frames:[png...], duration, error}。"""
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
    frames = []
    for i in range(count):
        t = dur * (i + 0.5) / count
        out = os.path.join(out_dir, "frame_%02d.png" % i)
        try:
            r = _run([ff, "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % t, "-i", path,
                      "-frames:v", "1", "-y", out], timeout=60)
        except Exception as e:
            return {"ok": False, "frames": frames, "duration": dur, "error": "抽帧异常：%s" % str(e)[:80]}
        if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
            frames.append(out)
    if not frames:
        return {"ok": False, "frames": [], "duration": dur, "error": "抽帧失败（ffmpeg 没产出图片）"}
    return {"ok": True, "frames": frames, "duration": dur, "error": ""}


def extract_audio(path: str, out_wav: str, max_seconds: int = DEFAULT_MAX_SECONDS) -> bool:
    ff = ffmpeg_path()
    if not ff:
        return False
    try:
        r = _run([ff, "-hide_banner", "-loglevel", "error", "-t", str(max(1, int(max_seconds))),
                  "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-y", out_wav], timeout=120)
        return r.returncode == 0 and os.path.isfile(out_wav) and os.path.getsize(out_wav) > 0
    except Exception:
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
            # ⚠️ 约定：`recognize_wav()` 返回 **(文本, 错误说明)**。2026-09-15 查出一个真 bug——
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


def cleanup(dir_path: str) -> None:
    """删掉临时目录（帧图与音频只在本次读取里用）。"""
    try:
        if dir_path and os.path.isdir(dir_path) and os.path.basename(dir_path).startswith("pm-video-"):
            shutil.rmtree(dir_path, ignore_errors=True)
    except Exception:
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
