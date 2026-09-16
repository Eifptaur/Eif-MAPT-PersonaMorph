#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音转文字判据（能力补齐 B）：**本地、离线、绝不假装识别过**。

链路：微信语音 .silk →[pilk / silk_v3_decoder]→ WAV(16k) →[Windows 内置 SAPI 听写]→ 文本

判据（不需要微信；②需要 TTS 与识别引擎，缺了会打印 SKIP 而不是假绿）：
  ① 引擎检测**不撒谎**：decoders/recognizers 有结构、status 的 why 与实测一致、没引擎时 ok=False
  ② **闭环自证**：TTS 合成 → WAV → SILK（微信 `\\x02#!SILK_V3` 帧）→ 解码 → 识别 → 与原文比对
  ③ fail-closed：文件不存在 / 垃圾 SILK ⇒ 明确错误、**不抛异常、不返回编造文本**
  ④ 没引擎时不假装：把三个解码器都判成不可用 ⇒ status.ok=False，transcribe 返回原因而非空文本
  ⑤ 接线：适配器有 `download_media(kind=voice|video|file)`，未知 kind 返回 None
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0
SKIP = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def skip(name, detail=""):
    global SKIP
    SKIP += 1
    print("  SKIP {}  [{}]".format(name, detail))


from agent import voice  # noqa: E402

print("── A. 引擎检测不撒谎 ──")
st = voice.status()
dec = st["decode"]
rec = st["recognize"]
ok("解码器清单是三条实测（pilk / silk_v3_decoder / ffmpeg-silk）",
   isinstance(dec, list) and len(dec) == 3 and all({"name", "ok", "detail"} <= set(d) for d in dec))
ok("识别器清单有 SAPI 文件听写那条", any("SAPI 文件听写" in r["name"] for r in rec))
has_dec = any(d["ok"] for d in dec)
has_rec = any(r["ok"] and not r["name"].startswith("  ") for r in rec)
ok("status.ok 与实测一致", bool(st["ok"]) == bool(has_dec and has_rec), st["why"])
ok("没引擎时 why 必须点名缺什么",
   st["ok"] or ("没有可用引擎" in st["why"] and len(st["why"]) > 10), st["why"][:60])
for d in dec:
    print("     解码器 {} ok={} {}".format(d["name"], d["ok"], d["detail"][:60]))
for r in rec:
    print("     识别器 {} ok={} {}".format(r["name"].strip(), r["ok"], r["detail"][:70]))

print("── B. 闭环自证（TTS → SILK → 文本）──")
if not (has_dec and has_rec):
    skip("闭环自证", "本机缺引擎：解码=%s 识别=%s" % (has_dec, has_rec))
else:
    loop = voice.selftest_loop("今天天气不错，我们出去走走吧")
    for s in loop["steps"]:
        ok("闭环-%s" % s["name"], s["ok"], s["detail"][:70])
    ok("识别文本与原文相似（标点不计）", loop["ok"], "听到：%s" % (loop["text"] or loop["err"]))
    ok("期望文本与听到文本非空", bool(loop["expect"]) and bool(loop["text"]))

print("── C. fail-closed ──")
t, err, info = voice.transcribe_silk(os.path.join(ROOT, "_scratch", "不存在.silk"))
ok("文件不存在 ⇒ 空文本 + 明确原因", t == "" and "不存在" in err, err[:60])
junk = os.path.join(ROOT, "_scratch", "junk_probe.silk")
with open(junk, "wb") as fh:
    fh.write(b"\x02#!SILK_V3" + b"\x00" * 200)
try:
    t2, err2, _ = voice.transcribe_silk(junk)
    ok("垃圾 SILK ⇒ 不崩、不编造文本", t2 == "", "err=%s" % (err2[:60] or "(空)"))
except Exception as e:
    ok("垃圾 SILK ⇒ 不崩、不编造文本", False, "抛了 %s: %s" % (type(e).__name__, e))
_audit = os.path.join(ROOT, "_scratch", "音" + "频")     # 不存在的目录，确认不抛异常
try:
    os.remove(junk)
except OSError:
    pass

print("── D. 没引擎时不假装 ──")
_real_dec, _real_rec = voice.decoders, voice.recognizers
try:
    voice.decoders = lambda: [{"name": "pilk", "ok": False, "detail": "probe"},
                              {"name": "silk_v3_decoder.exe", "ok": False, "detail": "probe"},
                              {"name": "ffmpeg(silk)", "ok": False, "detail": "probe"}]
    voice.recognizers = lambda: [{"name": "Windows 内置 SAPI 文件听写", "ok": False, "detail": "probe"}]
    st2 = voice.status()
    ok("三解码器全挂 ⇒ status.ok=False", st2["ok"] is False, st2["why"][:60])
    t3, err3, _ = voice.transcribe_silk(os.path.join(ROOT, "_scratch", "voice-test", "round.silk"))
    ok("没引擎 ⇒ 空文本 + 明确原因（不是编的文本）", t3 == "" and "没有可用引擎" in err3, err3[:70])
finally:
    voice.decoders, voice.recognizers = _real_dec, _real_rec

print("── E. 接线：适配器 download_media ──")
import inspect  # noqa: E402
from agent import wechat as W  # noqa: E402
src = inspect.getsource(W.WeChatAdapter.download_media)
ok("download_media 存在且支持三种 kind",
   all(k in src for k in ('"voice"', '"video"', '"file"')))
ok("未知 kind 返回 None（白名单，不做兜底瞎猜）", 'return None' in src and '"voice": "download_voice"' in src)
ok("语音工具链有 download_voice", "download_voice" in src)
print("     语音落盘目录：%s" % voice.voice_dir())

print("\n%d/%d 通过（跳过 %d）" % (PASS, PASS + FAIL, SKIP))
sys.exit(1 if FAIL else 0)
