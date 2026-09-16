# -*- coding: utf-8 -*-
"""「兼容用户本地语音模型」第二段 = 变声（音频→音频）+ 虚拟麦克风检测的判据。

背景（用户 2026-09-15 原话）：「**虚拟声卡可以装啊，大小不大就行。最主要是要兼容那些用户本地的，
比方说 GPT-SoVITS 的、RVC 的。我不是一直说吗？效果至上，用户至上，对用户有好处就加**」
⇒ 两件事：① **RVC 是「音频→音频」**（GPT-SoVITS 是「文字→音频」，早已支持）⇒ 必须两段串起来；
② 真语音条要一个"虚拟麦克风"，我们**只检测 + 引导**，不替用户装驱动（守「四不」第 ④ 条）。

不需要微信、不联网（网络出口 `_post_audio` 用替身）。守：
  A `_tone_wav()` 是真 WAV（能当测试音送进去）
  B 两种请求形态的请求体正确（表单上传 / 配置格式）
  C `vc_convert` 四种结局都**如实**（成功 / HTTP 错 / 不是音频 / 连不上）
  D `make()` 串两段：无地址不串 · 成功带 pipeline · **失败默认不发**（不假装）· 打开开关才降级发原音
  E `probe_vc` 永不抛异常
  F 能力口径文案必须含"未声明"，引导文案必须含"不替你装驱动"
  G 配置键、控制台字段、HTTP 路由三处都在
  H 音频端点检测：`0x10000001`（本机"立体声混音"的真实状态）必须判成**不可用**
  I 生产路径把变声结果/失败如实带回给模型
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


from agent import voice_models as V            # noqa: E402
from agent import audio_devices as AD          # noqa: E402
from agent.config import DEFAULT_CONFIG as D   # noqa: E402

TMP = tempfile.mkdtemp(prefix="vvc_")
V._out_dir = lambda: TMP

print("[A] 测试音是真的 WAV")
w = V._tone_wav(0.1, 16000, 440.0)
ck("A1 RIFF/WAVE 头齐", w[:4] == b"RIFF" and w[8:12] == b"WAVE")
_n = int(16000 * 0.1)
ck("A2 data 段长度与帧数一致", w[36:40] == b"data" and len(w) == 44 + _n * 2,
   "len=%d 期望=%d" % (len(w), 44 + _n * 2))

print("\n[B] 两种请求形态的请求体")


class _Resp:
    def __init__(self, raw, ctype="audio/wav", status=200):
        self._raw, self.headers, self.status = raw, {"Content-Type": ctype}, status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


CAP = {}


def _fake_open(req, timeout=None):
    CAP["req"] = req
    return _Resp(CAP.get("raw", b"RIFFxxxxWAVE"), CAP.get("ctype", "audio/wav"))


_orig_open = V.urllib.request.urlopen
V.urllib.request.urlopen = _fake_open
try:
    CAP["raw"], CAP["ctype"] = b"RIFFxxxxWAVE", "audio/wav"
    cfg_m = {"vc_mode": "multipart", "vc_params": '{"f0_up_key": 3, "speaker": "x"}'}
    V._post_audio("http://127.0.0.1:1/infer", w, cfg_m, 5, "in.wav")
    req = CAP["req"]
    bd = re.search(r"boundary=([\w-]+)", req.headers.get("Content-type") or
                   req.headers.get("Content-Type") or "")
    body = req.data
    ck("B1 表单形态：Content-Type 带 boundary", bool(bd))
    ck("B2 表单形态：参数与音频字节都在体里",
       b'name="f0_up_key"' in body and b"3" in body and b'filename="in.wav"' in body and w in body)
    ck("B3 表单形态：以 --boundary-- 收尾",
       bool(bd) and body.rstrip().endswith(("--%s--" % bd.group(1)).encode()))
    cfg_b = {"vc_mode": "base64"}
    V._post_audio("http://127.0.0.1:1/infer", w, cfg_b, 5, "in.wav")
    j = json.loads(CAP["req"].data.decode("utf-8"))
    import base64 as _b64
    ck("B4 配置格式形态：audio 是内容编码且能解回原字节", _b64.b64decode(j["audio"]) == w)
    ck("B5 配置格式形态：Content-Type 是 JSON",
       "application/json" in (CAP["req"].headers.get("Content-type") or CAP["req"].headers.get("Content-Type") or ""))
finally:
    V.urllib.request.urlopen = _orig_open

print("\n[C] vc_convert 四种结局都要如实")
cfg1 = {"vc_url": "http://127.0.0.1:1/infer", "vc_mode": "multipart"}
# ⚠️ 先真的造一个待变声文件（第一版忘了 ⇒ 三条断言全落在"读不了文件"上，是我自检自己的错）
_IN = os.path.join(TMP, "a.wav")
with open(_IN, "wb") as _f:
    _f.write(V._tone_wav(0.1))
V._post_audio = lambda *a, **k: (b"RIFFabcdWAVE", "audio/wav", 200)
p, why, info = V.vc_convert(_IN, cfg1, 5)
ck("C1 成功：落盘 + info 带字节数", bool(p) and os.path.exists(p) and info.get("vc") == "ok", str(info))
V._post_audio = lambda *a, **k: (_ for _ in ()).throw(urllib.error.HTTPError("u", 404, "nf", {}, None))
p2, why2, _i2 = V.vc_convert(_IN, cfg1, 5)
ck("C2 HTTP 错：判 None 且说明含 HTTP 码", p2 is None and "404" in why2, why2[:40])
V._post_audio = lambda *a, **k: ("<html>not audio</html>".encode(), "text/html", 200)
p3, why3, _i3 = V.vc_convert(_IN, cfg1, 5)
ck("C3 回的不是音频：判 None 且说明，绝不当成功", p3 is None and why3)
V._post_audio = lambda *a, **k: (_ for _ in ()).throw(OSError("refused"))
p4, why4, _i4 = V.vc_convert(_IN, cfg1, 5)
ck("C4 连不上：判 None 且说明", p4 is None and "连不上" in why4, why4[:40])
ck("C5 没配地址 ⇒ 明确拒绝（不静默跳过）", V.vc_convert("x", {})[0] is None and "vc_url" in V.vc_convert("x", {})[1])

print("\n[D] make() 串两段")
SRC = os.path.join(TMP, "src.wav")
with open(SRC, "wb") as f:
    f.write(V._tone_wav(0.1))
_orig_raw = V._make_raw
V._make_raw = lambda t, c, to: (SRC, "", {"voice": "stub"})
try:
    p, why, info = V.make("hi", {"backend": "sapi"})
    ck("D1 没配变声地址 ⇒ 原样返回、不串第二段", p == SRC and not info.get("pipeline"), str(info))
    V._post_audio = lambda *a, **k: (b"RIFFvcvcWAVE", "audio/wav", 200)
    p, why, info = V.make("hi", dict(cfg1, backend="sapi"))
    ck("D2 配上地址且成功 ⇒ pipeline=tts→vc", bool(p) and info.get("pipeline") == "tts→vc", str(info))
    V._post_audio = lambda *a, **k: (_ for _ in ()).throw(OSError("refused"))
    p, why, info = V.make("hi", dict(cfg1, backend="sapi"))
    ck("D3 变声失败 + 默认口径 ⇒ **不发**（返回 None）", p is None and "没有发出去" in why, why[:44])
    p, why, info = V.make("hi", dict(cfg1, backend="sapi", vc_fail_open=True))
    ck("D4 打开 vc_fail_open ⇒ 降级发原音，但**明说变了声没成功**",
       p == SRC and why and "未变声" in why, why[:44])
finally:
    V._make_raw = _orig_raw

print("\n[E] probe_vc 永不抛异常")
ck("E1 空地址给出原因", V.probe_vc("")["ok"] is False and V.probe_vc("")["why"] == "没填地址")
V._post_audio = lambda *a, **k: (b"RIFFabcdWAVE", "audio/wav", 200)
_r = V.probe_vc("http://127.0.0.1:1/infer", cfg=cfg1)
ck("E2 成功报字节与耗时", _r["ok"] and _r["bytes"] > 0 and _r["mode"] == "multipart", str(_r["why"]))
V._post_audio = lambda *a, **k: (_ for _ in ()).throw(OSError("refused"))
ck("E3 连不上也不炸", V.probe_vc("http://127.0.0.1:1/infer", cfg=cfg1)["ok"] is False)

print("\n[F] 能力口径与引导口径（都是用户可见的话）")
ck("F1 变声能力标注含「未声明」", "未声明" in V.VC_CAPABILITY_NOTE)
ck("F2 引导文案写明「不替你装驱动」", "不替你装驱动" in AD.GUIDE_NOTE)
ck("F3 引导文案点出立体声混音会串音", "混音" in AD.GUIDE_NOTE and "录进去" in AD.GUIDE_NOTE)

print("\n[G] 配置键 / 控制台字段 / HTTP 路由")
KEYS = ["vc_url", "vc_mode", "vc_params", "vc_json_field", "vc_fail_open"]
ck("G1 五个配置键都在 DEFAULT_CONFIG", all(k in D["voice_reply"] for k in KEYS),
   str([k for k in KEYS if k not in D["voice_reply"]]))
_ch = io.open(os.path.join(ROOT, "agent", "console_html.py"), encoding="utf-8").read()
ck("G2 控制台有对应输入项", all(('data-cfg="voice_reply.%s"' % k) in _ch for k in KEYS))
ck("G3 控制台有变声连通测试按钮", 'id="vcProbe"' in _ch and "/api/voice/vc-probe" in _ch)
_wu = io.open(os.path.join(ROOT, "agent", "webui.py"), encoding="utf-8").read()
ck("G4 路由存在且调 probe_vc", '"/api/voice/vc-probe"' in _wu and "probe_vc" in _wu)
ck("G5 status 暴露 vc 字段（控制台能显示）",
   all(k in V.status({}) for k in ("vc_url", "vc_mode", "vc_capability")))

print("\n[H] 音频端点检测（本机「立体声混音」实测状态就是 0x10000001）")
ck("H1 0x10000001 判成**不可用**（只看低位会把它当可用 ⇒ 以为能录却没有）",
   AD.is_active({"state": 268435457}) is False)
ck("H2 正常的 1 判成可用", AD.is_active({"state": 1}) is True)
ck("H3 停用态 2 判成不可用", AD.is_active({"state": 2}) is False)
_st = AD.status()
ck("H4 status 结构齐全且不炸",
   all(k in _st for k in ("ok", "virtual_mics", "inactive_virtual_mics", "capture", "why", "guide")))
ck("H5 本机确实枚举到了录音设备（判据自己没瞎）", len(_st["capture"]) >= 1, "%d 个" % len(_st["capture"]))
print("      本机读数：%s" % _st["why"])

print("\n[I] 生产路径如实带回")
_tp = io.open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
ck("I1 变声结果/失败都带回给模型（不许静默）",
   'out["vc"]' in _tp and 'out["warn"]' in _tp and "别声称用的是目标音色" in _tp)

print("\n==== 变声/虚拟麦克风判据：%d 通过 / %d 失败 ====" % (len(OK), len(BAD)))
for b in BAD:
    print("  FAIL " + b)
sys.exit(1 if BAD else 0)
