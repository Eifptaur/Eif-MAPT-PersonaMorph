# -*- coding: utf-8 -*-
"""语音合成（TTS）：把文字变成能发出去的音频。

**合成侧**用 Windows 内置 SAPI（零下载：本机实测有中文女声 `Microsoft Huihui Desktop`），
合成时**顺便**告诉我们有没有 ffmpeg（有就转成 mp3，体积小、对方点开就能播；没有就发 wav）。

⚠️ 边界（用户 2026-09-13 问"能不能在群里以语音形式回复"）：
   本模块只解决**合成**；**发出去**是另一件事——驱动库只有 `ForwardVoiceMessage`（转发别人的语音条），
   **没有"把任意音频发成语音条"的接口**。所以当前产品形态是 **A 档：把音频当文件发**（对方收到文件卡片，
   点开能听）；**B 档真语音条**需要虚拟麦克风 + 微信录音按钮，属待拍板项（见 AGENTS/交接件）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg() -> dict:
    try:
        from .config import get_config
        return dict(get_config().get("voice_reply") or {})
    except Exception:
        return {}


def out_dir() -> str:
    d = str(_cfg().get("dir") or "media/tts")
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


def ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or ""


def voices() -> list:
    """本机可用的合成声音（只读）。"""
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()                       # ⚠️ 只初始化、**不配对去初始化**（否则后续 Dispatch 报"没有注册类"）
        v = win32com.client.Dispatch("SAPI.SpVoice")
        toks = v.GetVoices()
        return [str(toks.Item(i).GetDescription()) for i in range(toks.Count)]
    except Exception:
        return []


def pick_voice(name: str = ""):
    """挑一个声音：指定名（子串匹配）优先，否则优先中文声音。返回 (声音对象, 名字)。"""
    import win32com.client
    v = win32com.client.Dispatch("SAPI.SpVoice")
    toks = v.GetVoices()
    allv = [(toks.Item(i), str(toks.Item(i).GetDescription())) for i in range(toks.Count)]
    if name:
        for obj, desc in allv:
            if str(name).lower() in desc.lower():
                return obj, desc
    for obj, desc in allv:
        if any(k in desc for k in ("Chinese", "中文", "Huihui", "Yaoyao", "Kangkang")):
            return obj, desc
    return (allv[0] if allv else (None, ""))


def status() -> dict:
    """引擎状态（控制台面板直接渲染）。"""
    vs = voices()
    ff = ffmpeg_path()
    ok = bool(vs)
    why = ("可用：Windows 内置 SAPI（%s）" % (vs[0] if vs else "—")) if ok else "不可用：本机没有 SAPI 合成声音"
    return {"ok": ok, "why": why, "voices": vs, "engine": "sapi",
            "ffmpeg": bool(ff), "ffmpeg_path": ff,
            "dir": out_dir(), "enabled": bool(_cfg().get("enabled")),
            "cfg_voice": str(_cfg().get("voice") or ""), "fmt": str(_cfg().get("format") or "mp3"),
            "note": "本模块只做**合成**；发送见 send_voice_reply（当前形态＝发音频文件，不是语音条）"}


def synthesize(text: str, out_wav: str = "") -> tuple:
    """文字 → WAV（SAPI）。返回 `(wav 路径 或 None, 错误说明)`。空文本直接拒。"""
    t = str(text or "").strip()
    if not t:
        return None, "文本为空，没什么可合成的"
    if not voices():
        return None, "本机没有可用的合成声音（Windows 语音组件缺失）"
    out_wav = out_wav or os.path.join(out_dir(), "tts_%s.wav" % time.strftime("%H%M%S"))
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)
    try:
        import win32com.client
        cfg = _cfg()
        obj, desc = pick_voice(str(cfg.get("voice") or ""))
        v = win32com.client.Dispatch("SAPI.SpVoice")
        if obj is not None:
            v.Voice = obj
        try:
            rate = int(cfg.get("rate") or 0)
            if rate:
                v.Rate = max(-10, min(10, rate))
        except Exception:
            pass
        st = win32com.client.Dispatch("SAPI.SpFileStream")
        st.Open(out_wav, 3, True)                     # 3 = SSFMCreateForWrite
        v.AudioOutputStream = st
        v.Speak(t)
        st.Close()
        if os.path.exists(out_wav) and os.path.getsize(out_wav) > 200:
            return out_wav, ""
        return None, "合成出来是空文件（声音对象没生效？）"
    except Exception as e:
        return None, "合成失败：%s: %s" % (type(e).__name__, str(e)[:140])


def to_playable(wav_path: str, fmt: str = "") -> tuple:
    """把 WAV 转成便于发送/播放的格式：有 ffmpeg 且要 mp3 就转 mp3，否则原样返回 WAV。

    返回 `(路径, 错误说明, 实际格式)`。
    """
    fmt = str(fmt or _cfg().get("format") or "mp3").lower()
    if fmt not in ("mp3", "wav") or fmt == "wav":
        return wav_path, "", "wav"
    ff = ffmpeg_path()
    if not ff:
        return wav_path, "", "wav"                        # 没 ffmpeg 就发 wav（不是错误，只是大一点）
    mp3 = os.path.splitext(wav_path)[0] + ".mp3"
    try:
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", wav_path, "-b:a", "64k", mp3],
                           capture_output=True, timeout=60,
                           creationflags=0x08000000 if os.name == "nt" else 0)   # 不许闪控制台窗
        if r.returncode == 0 and os.path.exists(mp3) and os.path.getsize(mp3) > 200:
            return mp3, "", "mp3"
        return wav_path, "ffmpeg 转 mp3 失败，改用 wav", "wav"
    except Exception as e:
        return wav_path, "ffmpeg 调不动（%s），改用 wav" % type(e).__name__, "wav"


def make(text: str) -> tuple:
    """一步到位：文字 → 可发送的音频。返回 `(路径 或 None, 错误说明, 信息 dict)`。"""
    info = {"engine": "sapi", "voice": "", "fmt": "", "wav": ""}
    obj, desc = pick_voice(str(_cfg().get("voice") or "")) if voices() else (None, "")
    info["voice"] = desc
    wav, err = synthesize(text)
    if not wav:
        return None, err, info
    info["wav"] = wav
    path, warn, fmt = to_playable(wav)
    info["fmt"] = fmt
    return path, (warn or ""), info


if __name__ == "__main__":                             # 手动看一眼
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    p, err, info = make("今天天气不错，我们出去走走吧")
    print("合成：", p, "｜", err, "｜", info)
