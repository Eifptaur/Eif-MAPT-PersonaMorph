# -*- coding: utf-8 -*-
"""语音转文字（**本地、离线、零下载优先**）。

链路：微信语音 `.silk` →[解码]→ WAV →[识别]→ 文本

    ① 解码 SILK → WAV（按顺序试，谁在就用谁）
       · `pilk`（Python 包，`pilk.encode/decode`，本机实测能读写微信那套 `\\x02#!SILK_V3` 帧）
       · `silk_v3_decoder.exe`（用户自己放的官方解码器）
       · `ffmpeg`（只有带 silk 解码器的构建才行；本机这版实测**没有**）
    ② 识别 WAV → 文本
       · **Windows 内置 SAPI 进程内听写**（`SAPI.SpInProcRecoContext` + `SpFileStream`）
         本机实测：识别器 `MS-2052-80-DESK`（Microsoft Speech Recognizer 8.0 Chinese Simplified-PRC）
         + OneCore `MS-2052-110-WINMO-DNN`；实测识别「今天天气不错，我们出去走走吧」得
         「今天天气不错我们出去走走吧」（只差标点）。

⛔ **铁律：探不到引擎就如实说"没有可用引擎"，绝不假装识别过**（宁可返回空文本 + 明确原因）。
   本机没有的引擎一律标注"未验证"，不许写成"支持"。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCK = threading.RLock()          # SAPI 进程内识别器一次只服务一个请求


def _cfg() -> dict:
    try:
        from .config import get_config
        return dict((get_config().get("voice") or {}))
    except Exception:
        return {}


def voice_dir() -> str:
    """语音落地目录（`media/voice/`，可用 `voice.dir` 改）。"""
    d = str(_cfg().get("dir") or "media/voice")
    p = d if os.path.isabs(d) else os.path.join(ROOT, d)
    return p


def _which(name: str) -> str:
    p = shutil.which(name)
    return p or ""


# ── ① 解码链 ────────────────────────────────────────────────────────────────

def _pilK_ok() -> tuple:
    try:
        import pilk                                   # noqa: F401
        return True, "已装（pip pilk）"
    except Exception as e:
        return False, "未装：%s（pip install pilk）" % type(e).__name__


def _silk_exe_ok() -> tuple:
    p = _which("silk_v3_decoder") or _which("silk_v3_decoder.exe")
    if not p:
        for c in (os.path.join(ROOT, "tools", "silk_v3_decoder.exe"),):
            if os.path.exists(c):
                p = c
                break
    return (True, p) if p else (False, "没找到 silk_v3_decoder.exe")


def _ffmpeg_silk_ok() -> tuple:
    p = _which("ffmpeg")
    if not p:
        return False, "没找到 ffmpeg"
    try:
        # ⛔ 2026-09-16（已知现象：「运行的时候极短时间内闪一个透明小窗」；探针抓到 4 个
        #   `PseudoConsoleWindow`、其中两个明确是 ffmpeg）：ffmpeg 是**控制台程序**，
        #   父进程不给 `CREATE_NO_WINDOW` 的话，每次调用都会新建一个控制台窗一闪而过。
        out = subprocess.run([p, "-hide_banner", "-decoders"], capture_output=True, timeout=20,
                             text=True, errors="ignore",
                             creationflags=0x08000000 if os.name == "nt" else 0).stdout or ""
    except Exception as e:
        return False, "ffmpeg 调不动：%s" % type(e).__name__
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lower() == "silk":
            return True, p
    return False, "这版 ffmpeg 没有 silk 解码器（本机实测如此）"


def decoders() -> list:
    """解码器清单（**实测**，不是猜的）。"""
    out = []
    ok, why = _pilK_ok()
    out.append({"name": "pilk", "ok": ok, "detail": why})
    ok2, why2 = _silk_exe_ok()
    out.append({"name": "silk_v3_decoder.exe", "ok": ok2, "detail": why2})
    ok3, why3 = _ffmpeg_silk_ok()
    out.append({"name": "ffmpeg(silk)", "ok": ok3, "detail": why3})
    return out


SILK_RATE = 24000            # 微信 SILK 解出来是 24kHz 单声道 16bit
WAV_RATE = 16000             # 给 SAPI 吃之前降到 16kHz（SAPI 听写对 8/11/16/22.05kHz 最稳）


def _pcm_to_wav(pcm_path: str, wav_path: str, rate: int = SILK_RATE, out_rate: int = WAV_RATE) -> tuple:
    """裸 PCM（16bit 单声道）→ 真正的 WAV；必要时用 stdlib 重采样（不额外依赖 ffmpeg）。"""
    import wave
    with open(pcm_path, "rb") as fh:
        raw = fh.read()
    if not raw:
        return False, "解码出来的 PCM 是空的"
    if out_rate and int(out_rate) != int(rate):
        try:
            import audioop                              # Python ≥3.13 已移除；没有就按原采样率写
            raw, _ = audioop.ratecv(raw, 2, 1, int(rate), int(out_rate), None)
            rate = int(out_rate)
        except Exception:
            pass
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(raw)
    return True, ""


def decode_silk(silk_path: str, wav_path: str = "") -> tuple:
    """SILK → WAV。返回 (wav 路径 或 None, 错误说明)。"""
    if not silk_path or not os.path.exists(silk_path):
        return None, "语音文件不存在：%s" % silk_path
    wav_path = wav_path or os.path.join(tempfile.gettempdir(), "pm_voice_%d.wav" % int(time.time() * 1000))
    ok, _ = _pilK_ok()
    if ok:
        try:
            import pilk
            pcm = os.path.join(tempfile.gettempdir(), "pm_voice_%d.pcm" % int(time.time() * 1000))
            pilk.decode(silk_path, pcm, pcm_rate=SILK_RATE)      # 注意：pilk 写出的是**裸 PCM**，不是 WAV
            good, err = _pcm_to_wav(pcm, wav_path)
            try:
                os.remove(pcm)
            except OSError:
                pass
            if good and os.path.exists(wav_path) and os.path.getsize(wav_path) > 44:
                return wav_path, ""
            return None, err or "pilk 解出来是空文件"
        except Exception as e:
            return None, "pilk 解码失败：%s: %s" % (type(e).__name__, str(e)[:120])
    ok2, exe = _silk_exe_ok()
    if ok2:
        try:
            subprocess.run([exe, silk_path, wav_path], capture_output=True, timeout=60,
                           creationflags=0x08000000 if os.name == "nt" else 0)
            if os.path.exists(wav_path) and os.path.getsize(wav_path) > 44:
                return wav_path, ""
            return None, "silk_v3_decoder 没产出 WAV"
        except Exception as e:
            return None, "silk_v3_decoder 调不动：%s" % type(e).__name__
    return None, "没有可用的 SILK 解码器（pilk / silk_v3_decoder / ffmpeg-silk 都没有；见引擎状态）"


# ── ② 识别链（Windows 内置 SAPI）────────────────────────────────────────────

def _registry_recognizers() -> list:
    """从注册表读 SAPI/OneCore 已装识别器（**只读**）。"""
    out = []
    try:
        import winreg
        keys = [(r"SOFTWARE\Microsoft\Speech\Recognizers\Tokens", "SAPI"),
                (r"SOFTWARE\Microsoft\Speech_OneCore\Recognizers\Tokens", "OneCore")]
        for path, tag in keys:
            for root in (winreg.HKEY_LOCAL_MACHINE,):
                try:
                    with winreg.OpenKey(root, path) as k:
                        i = 0
                        while True:
                            try:
                                name = winreg.EnumKey(k, i)
                            except OSError:
                                break
                            i += 1
                            try:
                                with winreg.OpenKey(k, name) as sk:
                                    desc = str(winreg.QueryValueEx(sk, "")[0])
                            except Exception:
                                desc = name
                            out.append({"tag": tag, "id": name, "desc": desc,
                                        "zh": ("Chinese" in desc or "zh-CN" in desc or "中文" in desc)})
                except OSError:
                    pass
    except Exception:
        pass
    return out


def sapi_file_ok() -> tuple:
    """能不能用 SAPI 对**文件**做听写（真起一个进程内识别器试一下）。"""
    try:
        import pythoncom
        import win32com.client
    except Exception as e:
        return False, "缺 pywin32：%s" % type(e).__name__
    try:
        pythoncom.CoInitialize()
        ctx = win32com.client.Dispatch("SAPI.SpInProcRecoContext")
        _ = ctx.CreateGrammar()
        return True, "可用（SAPI 进程内 + SpFileStream 读文件）"
    except Exception as e:
        return False, "起不来：%s: %s" % (type(e).__name__, str(e)[:100])
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def recognizers() -> list:
    out = []
    regs = _registry_recognizers()
    zh = [r for r in regs if r["zh"]]
    ok, detail = sapi_file_ok()
    if ok:
        d = ("可用（SAPI 进程内 + SpFileStream 读文件）；注册表里%s中文识别器（%s）"
             % ("有" if zh else "**没有**", zh[0]["desc"] if zh else "—"))
        out.append({"name": "Windows 内置 SAPI 文件听写", "ok": bool(zh), "detail": d})
    else:
        out.append({"name": "Windows 内置 SAPI 文件听写", "ok": False, "detail": detail})
    out.append({"name": "Windows 内置 OneCore DNN", "ok": False,
                "detail": "装了引擎（zh-CN DNN v11.1）但**没有文件入口**（WinRT SpeechRecognizer 只吃麦克风）⇒ 本功能用不上"})
    for r in regs:
        out.append({"name": "  注册表：%s" % r["desc"], "ok": True, "detail": "%s / %s" % (r["tag"], r["id"])})
    return out


def recognize_wav(wav_path: str, max_seconds: int = 60) -> tuple:
    """WAV → 文本（SAPI 进程内听写）。返回 (文本, 错误说明)。"""
    if not wav_path or not os.path.exists(wav_path):
        return "", "WAV 不存在：%s" % wav_path
    try:
        import pythoncom
        import win32com.client
    except Exception as e:
        return "", "缺 pywin32：%s" % type(e).__name__

    with _LOCK:
        pythoncom.CoInitialize()

        class _H:
            def __init__(self):
                self.hits = []
                self.ended = False

            def OnRecognition(self, StreamNumber, StreamPosition, RecognitionType, Result):
                try:
                    self.hits.append(win32com.client.Dispatch(Result).PhraseInfo.GetText())
                except Exception:
                    pass

            def OnEndStream(self, *a):
                self.ended = True

            def OnFalseRecognition(self, *a):
                pass

        h = _H()
        try:
            ctx = win32com.client.Dispatch("SAPI.SpInProcRecoContext")
            g = ctx.CreateGrammar()
            g.DictationSetState(1)                     # 1 = SGDSActive（听写模式）
            fs = win32com.client.Dispatch("SAPI.SpFileStream")
            fs.Open(wav_path, 0, False)                # 0 = SSFRead
            ctx.Recognizer.AudioInputStream = fs
            ev = win32com.client.WithEvents(ctx, _H)   # ⚠ 传**类**，传实例会 metaclass conflict
            deadline = time.time() + max(5, min(int(max_seconds or 60), 180))
            while time.time() < deadline and not ev.ended:
                pythoncom.PumpWaitingMessages()
                time.sleep(0.05)
            try:
                fs.Close()
            except Exception:
                pass
            text = "".join(x for x in ev.hits if x).strip()
            if text:
                return text, ""
            if not ev.ended:
                return "", "识别超时（%ds 内没出结果）" % int(max_seconds or 60)
            return "", "识别器没给出文本（可能是静音、太短，或本机识别器不认这段音频）"
        except Exception as e:
            return "", "识别失败：%s: %s" % (type(e).__name__, str(e)[:140])
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


# ── 对外 ──────────────────────────────────────────────────────────────────

def status() -> dict:
    """引擎链状态（控制台面板直接渲染这个）。"""
    dec = decoders()
    rec = recognizers()
    cfg = _cfg()
    dec_ok = next((d for d in dec if d["ok"]), None)
    rec_ok = next((r for r in rec if r["ok"] and not r["name"].startswith("  ")), None)
    ok = bool(dec_ok and rec_ok)
    if ok:
        why = "可用：%s 解码 + %s" % (dec_ok["name"], rec_ok["name"])
    else:
        miss = []
        if not dec_ok:
            miss.append("SILK 解码器（可 pip install pilk）")
        if not rec_ok:
            miss.append("本地识别引擎（需 Windows 中文语音识别器）")
        why = "**没有可用引擎**：缺 " + "、".join(miss)
    return {"ok": ok, "enabled": bool(cfg.get("enabled")), "dir": voice_dir(),
            "decode": dec, "recognize": rec, "why": why,
            "cfg_engine": str(cfg.get("engine") or "auto")}


def transcribe_silk(silk_path: str, keep_wav: bool = False) -> tuple:
    """SILK → 文本。返回 (文本, 错误说明, 过程信息 dict)。"""
    info = {"decoder": "", "engine": "", "wav": ""}
    # 先看文件在不在（2026-09-14 修正顺序）：本机没有 SILK 解码器时，老顺序会先报「没有可用引擎」，
    # 把「文件根本不存在」这个更具体、更可操作的原因盖掉 —— `voice_selftest` 的 C 段因此在缺引擎的
    # 机器上长期假红（脚本 10/11、exit 1，看着像功能坏了，其实是自检被引擎前置条件挡了）。
    if not silk_path or not os.path.exists(str(silk_path)):
        return "", "语音文件不存在：%s" % silk_path, info
    st = status()
    if not st["ok"]:
        return "", st["why"], info
    wav, err = decode_silk(silk_path)
    if not wav:
        return "", err, info
    info["wav"] = wav
    info["decoder"] = next((d["name"] for d in st["decode"] if d["ok"]), "")
    text, err = recognize_wav(wav, int(_cfg().get("max_seconds") or 60))
    info["engine"] = "SAPI 文件听写"
    if not keep_wav:
        try:
            os.remove(wav)
            info["wav"] = ""
        except OSError:
            pass
    return text, err, info


def transcribe_message(adapter, chat_id: str, local_id) -> tuple:
    """微信语音消息 → 文本：下载 `download_voice` → 落地 `media/voice/` → 识别。

    返回 (文本, 错误说明, 过程信息 dict)。**任何一步不成立都如实返回原因，不返回编造的文本。**
    """
    info = {"silk": "", "decoder": "", "engine": ""}
    dl = getattr(adapter, "download_media", None)
    if dl is None:
        return "", "适配器没有 download_media（版本不对）", info
    path = dl(chat_id, local_id, "voice")
    if not path or not os.path.exists(path):
        return "", "语音没下下来（该消息可能不是语音，或 media 库分片里没找到）", info
    info["silk"] = path
    text, err, more = transcribe_silk(path, keep_wav=bool(_cfg().get("keep_audio")))
    info.update({k: v for k, v in more.items() if v})
    return text, err, info


def selftest_loop(text: str = "今天天气不错，我们出去走走吧") -> dict:
    """**闭环自证**（不需要微信）：TTS 合成 → WAV → SILK（微信那套帧）→ 解码 → 识别 → 比对。

    控制台「测试引擎」按钮与 `scripts/voice_selftest.py` 共用这一条。
    """
    out = {"steps": [], "ok": False, "text": "", "expect": text, "err": ""}
    try:
        import pythoncom
        import win32com.client
        import pilk
    except Exception as e:
        out["err"] = "缺依赖：%s" % type(e).__name__
        out["steps"].append({"name": "依赖", "ok": False, "detail": out["err"]})
        return out
    tmp = tempfile.mkdtemp(prefix="pm_voice_")
    wav1 = os.path.join(tmp, "tts.wav")
    silk = os.path.join(tmp, "round.silk")
    wav2 = os.path.join(tmp, "round.wav")
    pythoncom.CoInitialize()
    try:
        v = win32com.client.Dispatch("SAPI.SpVoice")
        picked = ""
        for i in range(v.GetVoices().Count):
            d = v.GetVoices().Item(i).GetDescription()
            if "Chinese" in d or "中文" in d or "Huihui" in d:
                v.Voice = v.GetVoices().Item(i)
                picked = d
                break
        st = win32com.client.Dispatch("SAPI.SpFileStream")
        st.Open(wav1, 3, True)                        # 3 = SSFMCreateForWrite
        v.AudioOutputStream = st
        v.Speak(text)
        st.Close()
        out["steps"].append({"name": "TTS 合成", "ok": os.path.getsize(wav1) > 44,
                             "detail": "%s / %d bytes" % (picked or "默认声音", os.path.getsize(wav1))})
    except Exception as e:
        out["err"] = "TTS 失败：%s" % type(e).__name__
        out["steps"].append({"name": "TTS 合成", "ok": False, "detail": out["err"]})
        return out
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
    try:
        pilk.encode(wav1, silk, pcm_rate=24000, tencent=True)
        with open(silk, "rb") as fh:
            head = fh.read(10)
        out["steps"].append({"name": "WAV→SILK（微信帧）", "ok": head[:1] == b"\x02",
                             "detail": "头=%r / %d bytes" % (head[:9], os.path.getsize(silk))})
    except Exception as e:
        out["err"] = "pilk 编码失败：%s" % type(e).__name__
        out["steps"].append({"name": "WAV→SILK（微信帧）", "ok": False, "detail": out["err"]})
        return out
    got, err = decode_silk(silk, wav2)
    out["steps"].append({"name": "SILK→WAV", "ok": bool(got), "detail": os.path.basename(got or "") + (" " + err if err else "")})
    if not got:
        out["err"] = err
        return out
    heard, err2 = recognize_wav(wav2, 30)
    out["steps"].append({"name": "WAV→文本", "ok": bool(heard), "detail": heard or err2})
    out["text"] = heard
    out["err"] = err2
    out["ok"] = bool(heard) and _similar(text, heard) >= 0.6
    return out


def _similar(a: str, b: str) -> float:
    """只比汉字/字母/数字（标点与空格不算），返回 0~1。"""
    import difflib
    ka = "".join(ch for ch in a if ch.isalnum())
    kb = "".join(ch for ch in b if ch.isalnum())
    if not ka or not kb:
        return 0.0
    return difflib.SequenceMatcher(None, ka, kb).ratio()


if __name__ == "__main__":                                # 手动看一眼
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import json
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    r = selftest_loop()
    print(json.dumps(r, ensure_ascii=False, indent=2))
