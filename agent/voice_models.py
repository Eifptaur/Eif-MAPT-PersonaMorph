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
#: 能力口径（判据断言它必须含"未声明/证明不了"这类词，不许出现"已支持某音色"）
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
    return b if b in ("sapi", "http") else "sapi"


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


def status(cfg: dict | None = None) -> dict:
    """给控制台与工具用的**如实**状态（与 `tts.status()` 同形：ok / why / voices / engine）。"""
    c = cfg if isinstance(cfg, dict) else _cfg()
    out = {"ok": False, "backend": backend(c), "http_url": http_url(c),
           "capability": CAPABILITY_NOTE, "voices": [], "engine": "sapi", "why": ""}
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


def make(text: str, cfg: dict | None = None, timeout: int = DEFAULT_TIMEOUT):
    """按当前后端合成，返回 `(路径 或 None, 错误说明, info)` —— **与 `tts.make()` 同契约**。
    走系统声音时原样转发 `tts.make()`；走自带模型时只接受"确实拿到了音频字节"这一种成功。"""
    c = cfg if isinstance(cfg, dict) else _cfg()
    text = (text or "").strip()
    if not text:
        return None, "文本为空（不合成）", {}
    if backend(c) != "http":
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
