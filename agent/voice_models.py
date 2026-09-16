# -*- coding: utf-8 -*-
"""自带模型：把「合成声音」接到用户自己的**本地 HTTP 合成服务**（GPT-SoVITS / RVC-WebUI / 任意兼容端点）。

这是任务书 ③ 的第二半。第一半（开箱可用）已经在 `agent/voice.py`（SAPI 听写）与 `agent/tts.py`（SAPI 合成）里：
**零下载、纯离线**；本模块只提供**可选**的"自带模型"通道，绝不改变默认行为。

口径（与 ② 本地模型完全同一套）：
  1. **开箱可用优先**：`voice_reply.backend` 默认 `sapi`；自带模型是用户显式选择后才走。
  2. **能力如实标注**：本模块只能证明「端点通不通 / 返回的字节是不是音频 / 多快」。
     **证明不了**它是不是某个角色的音色；更不改变这个事实——**微信 PC 发不出"真语音条"**
     （要虚拟声卡 + 录音按钮，属待拍板项），发出去的仍然是**音频文件**。
  3. **绝不假装**：端点不通、返回不是音频、文件写不出来 ⇒ **明确报错**（返回 `None` + 原因），
     绝不返回空文件或编造的音频（"没引擎却装作合成过"是本项目反复钉的红线）。

契约**与 `tts` 对齐**：`make(text) -> (路径|None, 错误说明, info)`、`status() -> {ok,why,...}`，
所以生产路径（`agent/tools.py::_exec_send_voice_reply`）只换一层皮就能用上自带模型。

支持的两种响应形态（覆盖最常见的两家）：
  ① **直接回音频字节**（GPT-SoVITS 的 `api_v2` 就回 `audio/wav`）——默认；
  ② **回 JSON**，音频在某个字段里（base64 或本地文件路径），字段名由 `voice_reply.http_json_field` 指定
     （如 `data` / `audio` / `url`）。
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TIMEOUT = 30
PROBE_TEXT = "你好"
#: 能力口径（自检断言它必须含"未声明/证明不了"这类词，不许出现"已支持某音色"）
CAPABILITY_NOTE = ("本通道只能证明端点可用与返回的是音频；音色是否为目标角色未声明。"
                   "微信 PC 发不出真语音条，发出去的仍是音频文件。")


def _cfg() -> dict:
    try:
        from .config import get_config
        return dict(get_config().get("voice_reply") or {})
    except Exception:
        return {}


def backend(cfg: dict | None = None) -> str:
    c = cfg if isinstance(cfg, dict) else _cfg()
    b = str(c.get("backend") or "sapi").strip().lower()
    return b if b in ("sapi", "http", "edge") else "sapi"


def http_url(cfg: dict | None = None) -> str:
    c = cfg if isinstance(cfg, dict) else _cfg()
    return str(c.get("http_url") or "").strip()


def _out_dir() -> str:
    try:
        from . import tts
        return tts.out_dir()
    except Exception:
        d = os.path.join(ROOT, "media", "tts")
        os.makedirs(d, exist_ok=True)
        return d


def _post(url: str, text: str, timeout: int, cfg: dict):
    """POST 一段文本，返回 (raw_bytes, content_type, status)。判据里替身这个函数，不联网。"""
    body = {"text": text, "text_lang": str(cfg.get("http_lang") or "zh")}
    if cfg.get("http_text_field"):
        body = {str(cfg["http_text_field"]): text}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), (r.headers.get("Content-Type") or ""), getattr(r, "status", 200)


def _looks_audio(raw: bytes, ctype: str) -> bool:
    c = (ctype or "").lower()
    if c.startswith("audio/") or "octet-stream" in c:
        return True
    return raw[:4] in (b"RIFF", b"OggS", b"fLaC") or raw[:3] == b"ID3"


def _extract(raw: bytes, ctype: str, cfg: dict):
    """把响应体变成音频字节：直接是音频就用；是 JSON 就按配置字段取 base64 或本地文件。"""
    if _looks_audio(raw, ctype):
        return raw, ""
    field = str(cfg.get("http_json_field") or "").strip()
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return b"", "端点返回的既不是音频、也不是 JSON（响应前 40 字节：%r）" % raw[:40]
    if not field:
        return b"", "端点回的是 JSON，但没配 voice_reply.http_json_field（不知道该取哪个字段）"
    val = obj
    for part in field.split("."):
        if isinstance(val, dict) and part in val:
            val = val[part]
        else:
            return b"", "JSON 里找不到字段 %s" % field
    if not isinstance(val, str) or not val:
        return b"", "字段 %s 不是非空字符串" % field
    if val.startswith("data:") and ";base64," in val:
        val = val.split(";base64,", 1)[1]
    try:
        return base64.b64decode(val, validate=False), ""
    except Exception:
        pass
    if os.path.exists(val):                      # 有的后端回"落盘路径"
        try:
            with open(val, "rb") as f:
                return f.read(), ""
        except Exception as e:
            return b"", "字段是路径但读不出来：%s" % e
    return b"", "字段 %s 既不是 base64 也不是可读路径" % field


# ── 变声段（音频 → 音频）：兼容用户本地的 RVC / GPT-SoVITS 变声 / 任意同形态端点 ──────
# 用户 2026-09-15 原话：「**最主要是要兼容那些用户本地的，比方说 GPT-SoVITS 的、RVC 的**」。
# 这两家**形态不同**，所以必须两段串起来：
#   · GPT-SoVITS 是「文本 → 音频」（上面已支持，`api_v2` 直回 wav）
#   · **RVC 是「音频 → 音频」的变声** ⇒ 文本 →(TTS)→ 音频 →(**变声**)→ 音频
# 口径：变声失败**默认不发**（`voice_reply.vc_fail_open=false`）——用户指定了音色却发出去另一个声音，
#   就是"假装"，是本项目反复钉的红线；打开 fail_open 时才发未变声的原音，并在返回值里写明。
VC_CAPABILITY_NOTE = ("变声通道只能证明端点可用与返回的是音频；**音色是否为目标角色未声明**"
                      "（要自己听一遍）。微信 PC 发不出真语音条，发出去的仍是音频文件。")


def vc_url(cfg: dict | None = None) -> str:
    c = cfg if isinstance(cfg, dict) else _cfg()
    return str(c.get("vc_url") or "").strip()


def vc_boundary(cfg: dict | None = None) -> str:
    b = str((cfg or {}).get("vc_mode") or "multipart").strip().lower()
    return b if b in ("multipart", "base64") else "multipart"


def _vc_params(cfg: dict) -> dict:
    raw = (cfg or {}).get("vc_params")
    if isinstance(raw, dict):
        return dict(raw)
    try:
        obj = json.loads(str(raw or "{}"))
        return dict(obj) if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _tone_wav(seconds: float = 0.4, sr: int = 16000, freq: float = 440.0) -> bytes:
    """纯标准库造一段正弦 WAV —— 给变声端点做连通测试当输入（不引任何第三方依赖）。"""
    import math
    import struct
    n = int(sr * seconds)
    frames = b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * freq * i / sr)))
                      for i in range(n))
    return (b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
            + b"data" + struct.pack("<I", len(frames)) + frames)


def _post_audio(url: str, audio: bytes, cfg: dict, timeout: int, filename: str = "in.wav"):
    """把音频 POST 给变声端点 ⇒ `(raw, content_type, status)`。判据替身本函数，不联网。

    multipart 用标准库手搓（不引第三方）；`base64` 形态拼 JSON。两种覆盖了 RVC 系常见实现。
    """
    params = _vc_params(cfg)
    if vc_boundary(cfg) == "base64":
        body = {"audio": base64.b64encode(audio).decode("ascii"), "filename": filename}
        body.update(params)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "*/*"})
    else:
        bd = "----personamorph%s" % time.strftime("%H%M%S")
        chunks = []
        for k, v in params.items():
            chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                           % (bd, k, v)).encode("utf-8"))
        chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"%s\"\r\n"
                       "Content-Type: audio/wav\r\n\r\n" % (bd, filename)).encode("utf-8"))
        chunks.append(audio)
        chunks.append(("\r\n--%s--\r\n" % bd).encode("utf-8"))
        req = urllib.request.Request(url, data=b"".join(chunks), method="POST",
                                     headers={"Content-Type": "multipart/form-data; boundary=%s" % bd,
                                              "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), (r.headers.get("Content-Type") or ""), getattr(r, "status", 200)


def vc_convert(path: str, cfg: dict | None = None, timeout: int = DEFAULT_TIMEOUT):
    """把一个音频文件送去变声端点 ⇒ `(新路径|None, 错误说明, info)`。"""
    c = cfg if isinstance(cfg, dict) else _cfg()
    u = vc_url(c)
    if not u:
        return None, "没配变声服务地址（voice_reply.vc_url）", {}
    try:
        with open(path, "rb") as f:
            audio = f.read()
    except Exception as e:
        return None, "读不了要变声的音频：%s" % str(e)[:60], {}
    try:
        raw, ctype, st = _post_audio(u, audio, c, timeout, os.path.basename(path or "in.wav"))
    except urllib.error.HTTPError as e:
        return None, "变声端点 HTTP %s（接口路径/参数可能不对）" % e.code, {}
    except Exception as e:
        return None, "变声端点连不上：%s" % (str(e)[:80] or type(e).__name__), {}
    got, why = _extract(raw, ctype, _vc_field_cfg(c))
    if not got:
        return None, "变声端点没回音频：%s" % (why or "原因不明"), {}
    ext = "wav" if got[:4] == b"RIFF" else ("mp3" if got[:3] == b"ID3" else "bin")
    out = os.path.join(_out_dir(), "vc_%s.%s" % (time.strftime("%H%M%S"), ext))
    try:
        with open(out, "wb") as f:
            f.write(got)
    except Exception as e:
        return None, "写变声结果失败：%s" % str(e)[:60], {}
    return out, "", {"vc": "ok", "bytes": len(got), "http": st, "fmt": ext}


def _vc_field_cfg(c: dict) -> dict:
    cc = dict(c)
    if c.get("vc_json_field"):
        cc["http_json_field"] = c.get("vc_json_field")
    return cc


def probe_vc(url: str = "", timeout: int = DEFAULT_TIMEOUT, cfg: dict | None = None) -> dict:
    """变声端点连通测试：造一段 440Hz 正弦 WAV 送进去，报**实测**结果。**永不抛异常**。"""
    c = dict(cfg or _cfg())
    u = (url or vc_url(c)).strip()
    out = {"url": u, "ok": False, "ms": 0, "bytes": 0, "mode": vc_boundary(c),
           "content_type": "", "why": "", "capability": VC_CAPABILITY_NOTE}
    if not u:
        out["why"] = "没填地址"
        return out
    t0 = time.time()
    try:
        raw, ctype, st = _post_audio(u, _tone_wav(), c, timeout, "probe.wav")
        out["ms"] = int((time.time() - t0) * 1000)
        out["content_type"] = ctype
        got, why = _extract(raw, ctype, _vc_field_cfg(c))
        out["bytes"] = len(got)
        if not got:
            out["why"] = why or "拿不到音频"
            return out
        out["ok"] = True
        out["why"] = "通；返回 %d 字节音频（HTTP %s）" % (len(got), st)
    except urllib.error.HTTPError as e:
        out["ms"] = int((time.time() - t0) * 1000)
        out["why"] = ("HTTP %s（端点有应答但拒绝了这次调用；RVC 系大多要 multipart 的 audio 字段）"
                      % e.code)
    except Exception as e:
        out["ms"] = int((time.time() - t0) * 1000)
        out["why"] = "连不上：%s" % (str(e)[:80] or type(e).__name__)
    return out


def make(text: str, cfg: dict | None = None, timeout: int = DEFAULT_TIMEOUT):
    """`make` = TTS 合成（+ 可选的**变声段**）。返回契约与 `tts.make()` 完全相同。"""
    c = cfg if isinstance(cfg, dict) else _cfg()
    path, why, info = _make_raw(text, c, timeout)
    if not path:
        return path, why, info
    if not vc_url(c):
        return path, why, info
    try:                                  # 变声（本地模型推理/检索）通常比合成慢 ⇒ 单独给时间
        _vto = max(int(timeout), int(c.get("vc_timeout_ms") or 0) // 1000)
    except Exception:
        _vto = timeout
    vpath, vwhy, vinfo = vc_convert(path, c, _vto)
    if vpath:
        n = dict(info or {})
        n.update(vinfo)
        n["pipeline"] = "tts→vc"
        return vpath, "", n
    if c.get("vc_fail_open"):
        n = dict(info or {})
        n["vc"] = "failed"
        n["vc_why"] = vwhy
        return path, "变声失败（已按 vc_fail_open 发未变声的原音）：%s" % vwhy, n
    return None, ("变声失败、按「不假装」口径**没有发出去**（要发未变声的原音请打开 "
                  "voice_reply.vc_fail_open）：%s" % vwhy), {"vc": "failed", "vc_why": vwhy}


def status(cfg: dict | None = None) -> dict:
    """给控制台与工具用的**如实**状态（与 `tts.status()` 同形：ok / why / voices / engine）。"""
    c = cfg if isinstance(cfg, dict) else _cfg()
    out = {"ok": False, "backend": backend(c), "http_url": http_url(c),
           "capability": CAPABILITY_NOTE, "voices": [], "engine": "sapi", "why": "",
           "vc_url": vc_url(c), "vc_mode": vc_boundary(c),
           "vc_capability": VC_CAPABILITY_NOTE}
    if out["backend"] == "edge":
        out["engine"] = "edge-tts"
        out["voice"] = edge_voice(c)
        out["voices"] = [{"name": v, "label": lab} for v, lab in EDGE_VOICES]
        try:
            import edge_tts            # noqa: F401
            out["ok"] = True
            out["why"] = "edge-tts（免费神经语音，需要联网；失败会%s）" % (
                "按 edge_fallback 退回系统声音" if c.get("edge_fallback", True) else "如实报错、不静默")
        except Exception:
            out["ok"] = False
            out["why"] = "选了 edge-tts 但本机没装它（py -3 -m pip install edge-tts）"
        return out
    if out["backend"] == "http":
        out["engine"] = "custom-http"
        if not out["http_url"]:
            out["why"] = "选了自带模型，但没填地址（voice_reply.http_url）"
        else:
            out["ok"] = True
            out["why"] = "自带模型通道（地址已填、未验证连通；发送时不通会如实报错）"
        return out
    try:
        from . import tts
        st = tts.status()
        out["ok"] = bool(st.get("ok"))
        out["why"] = st.get("why") or ""
        out["voices"] = list(st.get("voices") or [])
    except Exception as e:
        out["why"] = "系统声音不可用：%s" % str(e)[:60]
    # 真语音条要用它：本机有没有"虚拟麦克风"（只检测，绝不替用户装驱动/改系统设置）
    try:
        from . import audio_devices as _ad
        out["mic"] = _ad.status()
    except Exception as e:
        out["mic"] = {"ok": False, "why": "音频设备检测失败：%s" % str(e)[:60]}
    return out


def probe(url: str = "", timeout: int = DEFAULT_TIMEOUT, cfg: dict | None = None) -> dict:
    """连通测试：真发一次极短文本，报**实测**状态/延迟/字节数/是不是音频。**永不抛异常**。"""
    c = dict(cfg or _cfg())
    u = (url or http_url(c)).strip()
    out = {"url": u, "ok": False, "ms": 0, "bytes": 0, "content_type": "", "why": "", "capability": CAPABILITY_NOTE}
    if not u:
        out["why"] = "没填地址"
        return out
    t0 = time.time()
    try:
        raw, ctype, st = _post(u, PROBE_TEXT, timeout, c)
        out["ms"] = int((time.time() - t0) * 1000)
        out["content_type"] = ctype
        audio, why = _extract(raw, ctype, c)
        out["bytes"] = len(audio)
        if not audio:
            out["why"] = why or "拿不到音频"
            return out
        out["ok"] = True
        out["why"] = "通；返回 %d 字节音频（HTTP %s）" % (len(audio), st)
    except urllib.error.HTTPError as e:
        out["ms"] = int((time.time() - t0) * 1000)
        out["why"] = "HTTP %s（端点有应答但拒绝了这次调用，看看它的接口路径/参数）" % e.code
    except Exception as e:
        out["ms"] = int((time.time() - t0) * 1000)
        out["why"] = "连不上：%s" % (str(e)[:80] or type(e).__name__)
    return out


# ── edge-tts 音源（2026-09-15 新增）：**免费、无需 key**的神经语音，中文 8 个音色 ─────────
# 为什么加它：群相原来只有 SAPI（机械音）与"用户自带模型"（要自己跑服务）两档；
# edge-tts 是中间那一档——开箱可用、音质接近真人、不要凭据，适合做**默认音源**。
# 口径：失败**如实报错**，并按 voice_reply.edge_fallback（默认开）退回系统声音，绝不静默出空音频。
EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
EDGE_VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 女声 · 通用"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 女声 · 年轻"),
    ("zh-CN-YunxiNeural", "云希 · 男声 · 阳光"),
    ("zh-CN-YunjianNeural", "云健 · 男声 · 浑厚"),
    ("zh-CN-YunyangNeural", "云扬 · 男声 · 播报"),
    ("zh-CN-YunxiaNeural", "云夏 · 男声 · 少年"),
    ("zh-CN-liaoning-XiaobeiNeural", "小北 · 女声 · 东北"),
    ("zh-CN-shaanxi-XiaoniNeural", "小妮 · 女声 · 陕西"),
]


def edge_voice(cfg: dict | None = None) -> str:
    c = cfg if isinstance(cfg, dict) else _cfg()
    return str(c.get("edge_voice") or EDGE_DEFAULT_VOICE).strip() or EDGE_DEFAULT_VOICE


def _ffmpeg_bin() -> str:
    """ffmpeg 可执行文件（唯一解析入口：配置 → PATH → imageio-ffmpeg 自带的那份）。"""
    try:
        from .ffmpeg_bin import path as _p
        return _p()
    except Exception:
        pass
    import shutil as _sh
    return _sh.which("ffmpeg") or ""


def _edge_make(text: str, cfg: dict, timeout: int):
    """edge-tts 合成 ⇒ ffmpeg 转 wav。返回 (路径 或 None, 错误说明, info)。"""
    try:
        import edge_tts
    except Exception:
        return None, "没装 edge-tts（py -3 -m pip install edge-tts）", {}
    import asyncio
    import subprocess
    voice = edge_voice(cfg)
    d = _out_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        return None, "输出目录建不出来：%s" % str(e)[:60], {}
    stamp = time.strftime("%H%M%S") + ("%03d" % (int(time.time() * 1000) % 1000))
    mp3 = os.path.join(d, "tts_edge_%s.mp3" % stamp)
    wav = os.path.join(d, "tts_edge_%s.wav" % stamp)

    async def _go():
        await edge_tts.Communicate(text, voice).save(mp3)

    try:
        asyncio.run(_go())
    except Exception as e:
        return None, "edge-tts 合成失败（联网了没？音色名对不对？）：%s" % str(e)[:90], {}
    try:
        if not os.path.exists(mp3) or os.path.getsize(mp3) < 1000:
            return None, "edge-tts 没产出有效音频（文件缺或过小）", {}
    except OSError as e:
        return None, "读不到 edge-tts 的产物：%s" % str(e)[:60], {}

    ff = _ffmpeg_bin()
    if not ff:
        # 没有 ffmpeg：**如实报错**，但把 mp3 路径带回去（下游若支持 mp3 还能用）
        return None, "要 wav 得先有 ffmpeg（PATH 里没找到）", {"edge_mp3": mp3, "voice": voice}
    try:
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", mp3, "-ar", "22050", "-ac", "1", wav],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=max(30, int(timeout)),
                           creationflags=0x08000000 if os.name == "nt" else 0)   # 不许闪控制台窗
    except Exception as e:
        return None, "ffmpeg 转换失败：%s" % str(e)[:70], {}
    if r.returncode != 0 or not os.path.exists(wav) or os.path.getsize(wav) < 1000:
        return None, "ffmpeg 转换失败（rc=%s）" % r.returncode, {}
    return wav, "", {"voice": voice, "engine": "edge-tts", "fmt": "wav",
                     "bytes": os.path.getsize(wav), "mp3": os.path.basename(mp3)}


def _make_raw(text: str, cfg: dict | None = None, timeout: int = DEFAULT_TIMEOUT):
    """按当前后端合成，返回 `(路径 或 None, 错误说明, info)` —— **与 `tts.make()` 同契约**。
    走系统声音时原样转发 `tts.make()`；走自带模型时只接受"确实拿到了音频字节"这一种成功；
    走 edge-tts 时失败会按 `voice_reply.edge_fallback`（默认开）退回系统声音，并**在说明里写明退回了**。"""
    c = cfg if isinstance(cfg, dict) else _cfg()
    text = (text or "").strip()
    if not text:
        return None, "文本为空（不合成）", {}
    b = backend(c)
    if b == "edge":
        p, why, info = _edge_make(text, c, timeout)
        if p:
            return p, "", info
        if c.get("edge_fallback", True):
            try:
                from . import tts
                p2, _w2, i2 = tts.make(text)
                if p2:
                    n = dict(i2 or {})
                    n["edge_failed"] = why
                    return p2, "edge-tts 没成功，已按 edge_fallback 退回系统声音：%s" % why, n
            except Exception as e:
                return None, "edge-tts 失败（%s），退回系统声音也失败：%s" % (why, str(e)[:50]), {}
        return None, why, {}
    if b != "http":
        try:
            from . import tts
            return tts.make(text)
        except Exception as e:
            return None, "系统声音合成失败：%s" % str(e)[:80], {}
    u = http_url(c)
    if not u:
        return None, "选了自带模型但没填地址（voice_reply.http_url）——要么填地址，要么切回系统声音", {}
    try:
        raw, ctype, _st = _post(u, text, timeout, c)
    except Exception as e:
        return None, "自带模型端点不通：%s" % str(e)[:80], {}
    audio, why = _extract(raw, ctype, c)
    if not audio:
        return None, "自带模型没返回音频：%s" % (why or "原因不明"), {}
    ext = "wav" if audio[:4] == b"RIFF" else ("mp3" if audio[:3] == b"ID3" else "bin")
    out = os.path.join(_out_dir(), "tts_custom_%s.%s" % (time.strftime("%H%M%S"), ext))
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "wb") as f:
            f.write(audio)
    except Exception as e:
        return None, "写文件失败：%s" % str(e)[:60], {}
    return out, "", {"voice": "custom-http", "fmt": ext, "bytes": len(audio), "note": CAPABILITY_NOTE}
