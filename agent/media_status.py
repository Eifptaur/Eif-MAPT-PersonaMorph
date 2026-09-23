# -*- coding: utf-8 -*-
"""媒体与语音的只读状态快照（控制台「媒体与语音」面板的唯一数据源）。

面板上每一句话都必须来自**实测**：引擎链是现场探测的，图库数量是现场数的，
转发开关是现场读配置的——**不许写死"支持/可用"**。字段与 `agent/console_html.py` 里
那段渲染一一对应（改字段名要同步改面板，判据见 `scripts/media_ui_selftest.py`）。

返回结构：
    {
      "voice":     agent.voice.status() 的结果（ok / why / decode / recognize / dir / enabled / cfg_engine）
      "voice_cfg": {"enabled": bool, "engine": "auto|sapi|off"}
      "image":     {"enabled", "mode", "dir"(绝对路径), "count"(图库里几张), "sources"}
      "forward":   {"optin": bool, "note": "为什么默认关"}
      "video":     {"video_read"(ffmpeg/ASR 就绪), "bilibili"(开关), "video_url"(开关/yt-dlp)}
    }
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def image_dir() -> str:
    """本地图库目录（`image_reply.dir`，支持绝对路径）。"""
    try:
        from .config import get_config
        d = str((get_config().get("image_reply") or {}).get("dir") or "assets/anime")
    except Exception:
        d = "assets/anime"
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


def image_count(path: str = "") -> int:
    """图库里有几张可用图（数不到就是 0，不抛异常）。"""
    p = path or image_dir()
    try:
        return len([f for f in os.listdir(p) if os.path.splitext(f)[1].lower() in IMG_EXT])
    except OSError:
        return 0


def snapshot() -> dict:
    from . import tts as T
    from . import voice as V
    from .config import get_config
    cfg = get_config()
    vcfg = cfg.get("voice") or {}
    icfg = cfg.get("image_reply") or {}
    rcfg = cfg.get("voice_reply") or {}
    d = image_dir()
    return {
        "voice": V.status(),
        "voice_cfg": {"enabled": bool(vcfg.get("enabled")), "engine": str(vcfg.get("engine") or "auto")},
        "image": {"enabled": bool(icfg.get("enabled")), "mode": str(icfg.get("mode") or "local"),
                  "dir": d, "count": image_count(d), "sources": list(icfg.get("sources") or [])},
        "forward": {"optin": bool((cfg.get("send") or {}).get("file_forward_optin")),
                    "note": "转发视频/文件要过一次系统「选择文件」对话框，会短暂抢前台 ⇒ 默认关"},
        # 语音回复（TTS）：合成是实测的；**发出去是音频文件不是语音条**（库里没有发语音条的接口）
        "tts": {"status": T.status(),
                "models": _voice_models_snapshot(),
                "cfg": {"enabled": bool(rcfg.get("enabled")), "voice": str(rcfg.get("voice") or ""),
                        "backend": str(rcfg.get("backend") or "sapi"),
                        "edge_voice": str(rcfg.get("edge_voice") or ""),
                        "rate": int(rcfg.get("rate") or 0), "format": str(rcfg.get("format") or "mp3"),
                        "max_chars": int(rcfg.get("max_chars") or 120),
                        "min_gap_seconds": int(rcfg.get("min_gap_seconds") or 30)},
                "note": "当前形态：把回复合成为音频**文件**发出去（不是微信语音条）"},
        # 群友要图（生图链条）：只暴露只读快照（开关/触发条件/后端数/过滤链/红线）——
        # 真后端待用户拍板（本地 ComfyUI 还是在线 API），没配后端时 generate() 会明确说"没后端"
        "image_gen": _image_gen_snapshot(),
        "video_gen": _video_gen_snapshot(),
        # 大图自动压缩（对账清单第 22 条）：只读快照，面板上的键是 send.image_compress.*
        "img_compress": _img_compress_snapshot(),
        # 视频通路（丙-11 C1，2026-09-24）：控制台原来看不到视频死活（ffmpeg/ASR/yt-dlp）。
        # 全部**现场探测**，不写死"可用"（本模块的开篇口径）。
        "video": _video_snapshot(),
    }


def _voice_models_snapshot() -> dict:
    """`agent/voice_models.py` 的只读快照（语音回复真正的音源那一层：sapi / edge / http）。

    为什么要它：面板上的「声音来源」现在有三档，而 `tts.status()` 只描述系统声音那一档——
    只看它会显示成「引擎状态：<系统声音>」，跟用户选的那一档对不上。这个模块 import 失败
    也不该把整个 status 拖挂，所以照样包一层。
    """
    try:
        from . import voice_models as VM
        return VM.status()
    except Exception as e:
        return {"ok": False, "backend": "", "why": "语音音源模块不可用：%s" % str(e)[:80],
                "error": type(e).__name__, "voices": [], "engine": ""}


def _img_compress_snapshot() -> dict:
    try:
        from . import img_compress as IC
        return IC.snapshot()
    except Exception as e:
        return {"enabled": False, "error": type(e).__name__, "why": str(e)[:80]}


def _video_gen_snapshot() -> dict:
    """`agent/video_gen.py` 的只读快照（同样包一层：它挂了不该把整个 status 拖挂）。"""
    try:
        from . import video_gen as VG
        return VG.snapshot()
    except Exception as e:
        return {"enabled": False, "error": type(e).__name__, "why": str(e)[:80], "backends": []}


def _video_snapshot() -> dict:
    """视频通路只读快照（丙-11 C1）：本地视频读取 + B 站 + 外链解析三条腿的就绪状态。

    为什么要它（工单 R2）：`snapshot()` 原来**完全没收视频这一块** ⇒ 控制台上 ffmpeg 有没有、
    本机识别能不能用、yt-dlp 装没装，用户全看不到（坏在哪只能猜）。这里**现场探测**，任一项
    取不到都如实写 why（`probe()` / `ytdlp_bin()` 自己就是 fail-closed 的如实返回）。
    三块任一 import 失败也不该把整个 status 拖挂 ⇒ 各自包一层。
    """
    out = {
        # 本地视频读取：ffmpeg（抽帧/抽音频）+ 本机 ASR（听音频）
        "video_read": {"ok": False, "ready": False, "ffmpeg": "", "asr": None, "why": "取不到"},
        # B 站通路：开关 + 听视频上限
        "bilibili": {"enabled": False, "listen_max_seconds": 0},
        # 外链通路（丙-11 A）：开关 + 下载器 + 抽帧上限
        "video_url": {"enabled": False, "max_frames": 0, "max_seconds": 0,
                      "ytdlp": "", "ytdlp_ready": False, "why": ""},
    }
    try:
        from . import video_read as VR
        snap = VR.snapshot()
        out["video_read"] = {"ok": bool(snap.get("ready")), "ready": bool(snap.get("ready")),
                             "ffmpeg": str(snap.get("ffmpeg") or ""), "asr": snap.get("asr") or {},
                             "why": str(snap.get("why") or ""), "note": str(snap.get("note") or ""),
                             "limits": snap.get("limits") or {}}
    except Exception as e:
        out["video_read"] = {"ok": False, "ready": False, "ffmpeg": "", "asr": None,
                             "why": "视频读取模块不可用：%s" % str(e)[:80]}
    try:
        from .config import get_config
        cfg = get_config() or {}
        bc = cfg.get("bilibili") or {}
        out["bilibili"] = {"enabled": bool(bc.get("enabled")),
                           "listen_max_seconds": int(bc.get("listen_max_seconds") or 0)}
        uc = cfg.get("video_url") or {}
        out["video_url"].update({"enabled": bool(uc.get("enabled")),
                                 "max_frames": int(uc.get("max_frames") or 0),
                                 "max_seconds": int(uc.get("max_seconds") or 0)})
    except Exception as e:
        out["video_url"]["why"] = "配置读不到：%s" % str(e)[:60]
    # 下载器（yt-dlp）：**现场探测**（有/没有都如实报，没装时 why 指路）
    try:
        from . import bilibili as B
        exe = str(B.ytdlp_bin() or "")
        out["video_url"]["ytdlp"] = exe
        out["video_url"]["ytdlp_ready"] = bool(exe)
        if not exe:
            out["video_url"]["why"] = "还没装下载器 yt-dlp（外链视频解析需要它）"
    except Exception as e:
        out["video_url"]["ytdlp"] = ""
        out["video_url"]["ytdlp_ready"] = False
        out["video_url"]["why"] = "下载器探测失败：%s" % str(e)[:60]
    return out


def _image_gen_snapshot() -> dict:
    """`agent/image_gen.py` 的只读快照（这个模块 import 它失败也不该把整个 status 拖挂）。"""
    try:
        from . import image_gen as IG
        return IG.snapshot()
    except Exception as e:
        return {"enabled": False, "error": type(e).__name__, "why": str(e)[:80]}


if __name__ == "__main__":                     # 直接跑这个文件看一眼（相对导入要靠包路径）
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.path.insert(0, ROOT)                   # 让 `agent` 包可导入（脚本方式跑时）
    from agent.media_status import snapshot as _snap
    print(json.dumps(_snap(), ensure_ascii=False, indent=2))
