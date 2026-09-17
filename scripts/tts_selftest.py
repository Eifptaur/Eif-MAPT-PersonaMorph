#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音回复（TTS）判据：**合成是真的、权限是真的、边界要说清楚**（不需要微信、不出网）。

判据：
  ① 引擎状态不撒谎：有声音 ⇒ ok=True；拿掉声音 ⇒ ok=False 且给出原因
  ② 合成真出文件：合成一句中文得到 >200 字节的音频（wav 保底；有 ffmpeg 时给 mp3）
  ③ fail-closed：空文本 / 超长文本 / 功能没开 ⇒ 明确拒绝，**不许合成也不许发送**
  ④ 权限红线：`voice_reply.enabled=False` 时 `send_voice_reply` 一次都不许调发送（记账假适配器验）
  ⑤ 形态写清楚：工具描述与文案里必须写明"**发出去的是音频文件、不是微信语音条**"（用户的期待差别很大）
  ⑥ 防刷屏：同会话同内容在 min_gap_seconds 内第二次调用不再发
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


from agent import tts as T  # noqa: E402
from agent import tools as TL  # noqa: E402

print("── A. 引擎状态 ──")
st = T.status()
ok("status 有 ok/why/voices/ffmpeg/dir", all(k in st for k in ("ok", "why", "voices", "ffmpeg", "dir")))
ok("ok 与实测一致（有声音才 true）", bool(st["ok"]) == bool(T.voices()), st["why"][:60])
ok("说清了本模块只做合成", "合成" in st.get("note", ""))
_real_voices = T.voices
try:
    T.voices = lambda: []
    st2 = T.status()
    ok("没有声音 ⇒ ok=False 且给原因", st2["ok"] is False and "不可用" in st2["why"], st2["why"][:50])
    w, err = T.synthesize("测试")
    ok("没引擎时合成 ⇒ 明确失败不产出", w is None and "没有可用的合成声音" in err, err[:50])
finally:
    T.voices = _real_voices

print("── B. 合成真出文件 ──")
if not st["ok"]:
    print("  SKIP 本机没有 SAPI 合成声音")
else:
    wav, err = T.synthesize("这是一条语音回复的测试")
    ok("合成出 WAV 且不是空文件", bool(wav) and os.path.exists(wav) and os.path.getsize(wav) > 200,
       "%s bytes" % (os.path.getsize(wav) if wav and os.path.exists(wav) else 0))
    path, warn, fmt = T.to_playable(wav) if wav else ("", "", "")
    ok("转可发送格式（mp3 或 wav 保底）", bool(path) and fmt in ("mp3", "wav"), "%s %s" % (fmt, warn))
    ok("产物落在配置目录里", os.path.abspath(path).startswith(os.path.abspath(T.out_dir())))

print("── C/D/E. 工具层：权限 + 边界 + 防刷屏 ──")


class FakeWeChat:
    def __init__(self):
        self.calls = []

    def send_file_posted(self, chat_id, path, **kw):
        self.calls.append((chat_id, path))
        return True, "投递发文件成功（DB 回读 local_id=1 type=文件/链接/卡片）"


def mkctx(wechat=None):
    return {"store": None, "chat_key": "group:x", "chat_id": "x",
            "wechat": wechat or FakeWeChat(), "session": {"sent": []}, "sender": None}


d = {x["name"]: x for x in TL.build_tool_defs()}
ok("注册了 send_voice_reply", "send_voice_reply" in d)
ok("描述里写明默认是真语音条、前提不齐才回退成文件",
   "真语音条" in str(d.get("send_voice_reply", {}).get("description"))
   and "音频文件" in str(d.get("send_voice_reply", {}).get("description")))
ok("描述里提到功能没开会返回原因", "返回原因" in str(d.get("send_voice_reply", {}).get("description")))

# 功能默认关 ⇒ 一次都不许发送
# ⚠️ 判据必须**自带夹具**：这两条原来直接吃真实 `config.json` ⇒ 一旦把语音回复打开（演示/自用都要开），
#    判据就假红（2026-09-17 实际踩到，出包前全场判据当场红）。这里显式钉住"关着"。
_saved_get_gate = TL.get_config
TL.get_config = lambda: {"voice_reply": {"enabled": False}}
try:
    fw = FakeWeChat()
    r = TL._exec_send_voice_reply(mkctx(fw), {"text": "你好呀"})
    body = r.get("content") or ""
finally:
    TL.get_config = _saved_get_gate
ok("默认关时拒绝并说明原因", "默认关闭" in body and "语音回复" in body, body[:54])
ok("默认关时一次都没碰发送", fw.calls == [], str(fw.calls))

# 打开开关后：空文本/超长要拒
_real_get = TL.get_config


def fake_cfg(enabled=True, mx=120, gap=30):
    return lambda: {"voice_reply": {"enabled": enabled, "max_chars": mx, "min_gap_seconds": gap,
                                    "format": "wav", "dir": "media/tts", "voice": "", "rate": 0}}


try:
    TL.get_config = fake_cfg(enabled=True)
    TL._VOICE_LAST.clear()
    fw2 = FakeWeChat()
    r2 = TL._exec_send_voice_reply(mkctx(fw2), {"text": "   "})
    ok("空文本 ⇒ 报错且不发送", r2.get("is_error") is True and fw2.calls == [])
    fw3 = FakeWeChat()
    r3 = TL._exec_send_voice_reply(mkctx(fw3), {"text": "长" * 200})
    ok("超长 ⇒ 拒绝且不发送", "太长" in (r3.get("content") or "") and fw3.calls == [])
except Exception as e:
    ok("工具层拒绝路径可跑", False, "%s: %s" % (type(e).__name__, e))
finally:
    TL.get_config = _real_get

print("── F. 防刷屏（同会话同内容 short 间隔内不发第二次）──")
try:
    TL.get_config = fake_cfg(enabled=True, gap=30)
    TL._VOICE_LAST.clear()
    fw4 = FakeWeChat()
    ctx4 = mkctx(fw4)
    first = TL._exec_send_voice_reply(ctx4, {"text": "防刷屏测试一句"})
    second = TL._exec_send_voice_reply(ctx4, {"text": "防刷屏测试一句"})
    ok("第一次真的发了", len(fw4.calls) == 1, str(len(fw4.calls)))
    ok("第二次被防刷屏拦下（不发）", len(fw4.calls) == 1 and "不重复发" in (second.get("content") or ""),
       (second.get("content") or "")[:40])
    TL._VOICE_LAST.clear()
finally:
    TL.get_config = _real_get

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
