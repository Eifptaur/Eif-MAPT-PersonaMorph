# -*- coding: utf-8 -*-
"""视频音频转写的「低置信度」诚实边界判据。

背景（`报告/外部项目考察-20261001/视频下载后续动作-核实书.md` §六）：
  `video_read.read()` 的音频分支只在 **`recognize_wav` 返回空串** 时才写 `audio_why`
  （既有判据 `model_video_selftest.py` H 段已钉住这一档，39/0）。
  但 **SAPI 对纯音乐/环境声常常不返回空，而是乱猜一个字** —— 实测 440Hz 正弦 ⇒ `'子'`。
  此时 `audio_ok=True`、`audio_why=""` ⇒ 模型收到「视频里的说话内容：子」
  ⇒ 把环境声当成"有人说了那个字"，正好踩中 prompt 里「不确定的地方别猜」。

本判据钉的是 **"非空但不可信"这一档**（既有判据没覆盖的那一档）：
  · `recognize_wav` 返回**单个乱猜字** ⇒ **不得**写成"说话内容"（应如实说听不出）
  · `recognize_wav` 返回**真实短句**   ⇒ **必须**照常带出（防误伤，阴性对照）

⛔ 先写判据、**在未改代码上必须见红**；改完再转绿（纪律 16 双向验证）。

跑法：py scripts/video_audio_confidence_selftest.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

from agent import video_read as vr  # noqa: E402
from agent import voice as _voice  # noqa: E402

PASS, FAIL = [0], [0]


def ok(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print("  PASS %s" % name)
    else:
        FAIL[0] += 1
        print("  FAIL %s  %s" % (name, extra))


def _with_asr(fake_ret, frames=1):
    """把 extract_frames/extract_audio/recognize_wav 都换成替身，跑一次 read()。"""
    _r_fr, _r_ex, _r_rec = vr.extract_frames, vr.extract_audio, _voice.recognize_wav
    try:
        vr.extract_frames = lambda p, d, count=4: {"ok": True, "frames": ["f.jpg"], "duration": 3.0}
        vr.extract_audio = lambda p, w, max_seconds=60: True
        _voice.recognize_wav = lambda w, max_seconds=60: fake_ret
        r = vr.read("fake.mp4", max_frames=frames, max_seconds=5)
        vr.cleanup(r.get("dir") or "")
        return r
    finally:
        vr.extract_frames, vr.extract_audio, _voice.recognize_wav = _r_fr, _r_ex, _r_rec


print("== A. 核心：单个乱猜字不得写成「说话内容」==")

# A1: 孤立单字乱猜（SAPI 对纯音乐的典型输出：与语境无关、不成词的孤立字）
_r = _with_asr(("子", ""))
ok("A1 转写返回孤立乱猜字（子）⇒ 不得标 audio_ok=True",
   _r.get("audio_ok") is not True,
   "audio_ok=%r audio_text=%r（模型会以为有人说了这个字）" % (_r.get("audio_ok"), _r.get("audio_text")))

ok("A2 转写返回孤立乱猜字 ⇒ 必须给出 audio_why（如实说听不出）",
   bool(str(_r.get("audio_why") or "").strip()),
   "audio_why 为空 ⇒ 模型收到「说话内容：子」而没有任何提醒")

# A3: 纯标点型乱猜
_r3 = _with_asr(("。", ""))
ok("A3 转写返回单个标点 ⇒ 不得标 audio_ok=True",
   _r3.get("audio_ok") is not True,
   "audio_ok=%r audio_text=%r" % (_r3.get("audio_ok"), _r3.get("audio_text")))

# A4: 孤立拉丁单字母（非中文，同样不成词）
_r8 = _with_asr(("x", ""))
ok("A4 转写返回孤立单字母 ⇒ 不得标 audio_ok=True",
   _r8.get("audio_ok") is not True,
   "audio_ok=%r audio_text=%r" % (_r8.get("audio_ok"), _r8.get("audio_text")))

print("\n== B. ★ 阴性对照（短 ≠ 不可信；常用表达不许一棍打死）==")
# ⛔ 这一段来历：
#   「好多在三字以下的都是常用表达，比方说：『神了，你神了。』『说句话』『嗯』，也可以用来确认。
#     好，也可以用来确认，那你不是一棍打死了吗」
#   ⇒ 真实应答词**恰恰高频且短**（好/嗯/对/行），判据不许按"长度"一刀切。

_REAL_SHORT = ["好", "嗯", "对", "行", "是", "收到", "神了", "说句话", "嗯嗯", "好的"]
for _w in _REAL_SHORT:
    _rw = _with_asr((_w, ""))
    ok("B-真实短应答「%s」⇒ 必须算说话内容（不许判掉）" % _w,
       _rw.get("audio_ok") is True and _rw.get("audio_text") == _w,
       "audio_ok=%r text=%r（被一刀切误伤了）" % (_rw.get("audio_ok"), _rw.get("audio_text")))

_r9 = _with_asr(("神了，你神了。", ""))
ok("B-用户举的整句「神了，你神了。」⇒ 必须算说话内容",
   _r9.get("audio_ok") is True,
   "audio_ok=%r text=%r" % (_r9.get("audio_ok"), _r9.get("audio_text")))

_r4 = _with_asr(("这是一条测试语音", ""))
ok("B-普通短句 ⇒ audio_ok=True（不许把正常识别也判掉）",
   _r4.get("audio_ok") is True and _r4.get("audio_text") == "这是一条测试语音",
   "audio_ok=%r text=%r" % (_r4.get("audio_ok"), _r4.get("audio_text")))

ok("B-普通短句 ⇒ 不该写 audio_why",
   not str(_r4.get("audio_why") or "").strip(),
   "audio_why=%r" % (_r4.get("audio_why"),))

_r5 = _with_asr(("好的收到", ""))
ok("B-四字短句（好的收到）⇒ 仍算说话内容",
   _r5.get("audio_ok") is True,
   "audio_ok=%r text=%r" % (_r5.get("audio_ok"), _r5.get("audio_text")))

print("\n== C. 既有诚实路径不许被破坏（回归锚）==")

_r6 = _with_asr(("   ", ""))
ok("C1 空串 ⇒ 如实说「没听出可辨认的说话内容」（既有口径，不许改坏）",
   _r6.get("audio_ok") is False and "没听出可辨认" in str(_r6.get("audio_why") or ""),
   "audio_why=%r" % (_r6.get("audio_why"),))

_r7 = _with_asr(("", "本机没装识别引擎"))
ok("C2 引擎报错 ⇒ 原因照原样带出（既有口径，不许改坏）",
   _r7.get("audio_ok") is False and "识别引擎" in str(_r7.get("audio_why") or ""),
   "audio_why=%r" % (_r7.get("audio_why"),))

print("\n== 音频置信度判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
