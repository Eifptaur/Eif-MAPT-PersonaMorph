#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""媒体工具判据（能力补齐 C 的接线 + B 的模型入口）：下载/转写/转发三件，红线不许破。

判据（不需要微信、不出网）：
  ① 消息管线：`normalize()` 给 [语音]/[视频] 消息带上 `media:[{kind,local_id}]`（文件那条靠源码断言）
  ② 工具注册：`build_tool_defs()` 里有 transcribe_voice / download_media / forward_media，且都接了 execute
  ③ **转发默认关**：`send.file_forward_optin=False` 时 forward_media 必须拒绝并说明原因，
     且**一次都不许调用发送**（用会记账的假适配器验）
  ④ 引擎缺失不编造：`voice.status()` 说没引擎时，transcribe_voice 返回"没有可用引擎"，不返回任何文本
  ⑤ kind 白名单：download_media 传别的 kind 直接报错，不去猜
"""
import os
import sys

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


import json  # noqa: E402

from agent import tools as T  # noqa: E402
from agent import wechat as W  # noqa: E402
from agent.wechat import WeChatAdapter  # noqa: E402

print("── A. 消息管线带 media ──")
ad = WeChatAdapter.__new__(WeChatAdapter)          # 不走 __init__，只测纯函数
ad._self_wxid = "wxid_me"
ad._nick_map = {}
ad._recent_sent = []
for mtype, want in (("语音", "voice"), ("视频", "video")):
    got = ad.normalize({"type": mtype, "local_id": 4321, "create_time": 0, "sort_seq": 0,
                        "sender_id": "7", "content": ""})
    media = (got or {}).get("media") or []
    ok("%s 消息带 media kind=%s" % (mtype, want),
       bool(media) and media[0].get("kind") == want and media[0].get("local_id") == 4321,
       json.dumps(media, ensure_ascii=False))
src = open(os.path.join("agent", "wechat.py"), encoding="utf-8").read()
ok("文件消息也带 media kind=file（源码断言）", '{"kind": "file", "local_id": local_id}' in src)

print("── B. 工具注册 ──")
defs = T.build_tool_defs()
byname = {d["name"]: d for d in defs}
for name in ("transcribe_voice", "download_media", "forward_media"):
    d = byname.get(name) or {}
    ok("注册了 %s 且接了 execute" % name, callable(d.get("execute")))
    ok("%s 有中文描述" % name, len(str(d.get("description") or "")) > 20)
ok("forward_media 的描述里写明会抢前台", "抢" in str(byname.get("forward_media", {}).get("description")))


class FakeStore:
    def __init__(self, entry):
        self.entry = entry

    def find_by_mid(self, chat_key, mid):
        return self.entry

    def recent(self, chat_key, limit=60):
        return [{"mid": 4321}]


class FakeWeChat:
    def __init__(self, path="/tmp/x.mp4"):
        self.path = path
        self.calls = []

    def download_media(self, chat_id, local_id, kind):
        self.calls.append(("download_media", kind, local_id))
        return self.path

    def send_file_posted(self, chat_id, path, **kw):
        self.calls.append(("send_file_posted", path))
        return True, "投递发文件成功（DB 回读 local_id=1 type=文件）"


def mkctx(entry, wechat=None):
    return {"store": FakeStore(entry), "chat_key": "group:x", "chat_id": "x",
            "wechat": wechat or FakeWeChat(), "session": {"sent": []}, "sender": None}


ENTRY_VIDEO = {"mid": 4321, "media": [{"kind": "video", "local_id": 777}], "text": "[视频]"}
ENTRY_VOICE = {"mid": 4321, "media": [{"kind": "voice", "local_id": 888}], "text": "[语音]"}
ENTRY_TEXT = {"mid": 4321, "media": [], "text": "普通文本"}

print("── C. 转发默认关（红线）──")
from agent.config import get_config  # noqa: E402
ok("默认 file_forward_optin=False", not bool((get_config().get("send") or {}).get("file_forward_optin")))
fw = FakeWeChat()
r = T._exec_forward_media(mkctx(ENTRY_VIDEO, fw), {"message_id": 4321, "kind": "video"})
body = r.get("content") or ""
ok("默认关时拒绝并说明原因", "默认关闭" in body and "前台" in body, body[:60])
ok("默认关时一次都没碰发送（也没下载）", fw.calls == [], str(fw.calls))
ok("拒绝时提示链接不受影响", "链接" in body)

print("── D. 引擎缺失不编造 ──")
from agent import voice as V  # noqa: E402
_real_status = V.status
try:
    V.status = lambda: {"ok": False, "why": "缺 SILK 解码器（可 pip install pilk）"}
    r2 = T._exec_transcribe_voice(mkctx(ENTRY_VOICE), {"message_id": 4321})
    b2 = r2.get("content") or ""
    ok("没引擎 ⇒ 明说没有引擎", "没有可用的语音识别引擎" in b2, b2[:70])
    ok("没引擎 ⇒ 不返回任何转写文本", "voice_text" not in b2)
finally:
    V.status = _real_status

print("── E. kind 白名单与空条目 ──")
r3 = T._exec_download_media(mkctx(ENTRY_VIDEO), {"message_id": 4321, "kind": "audio"})
ok("未知 kind 直接报错", r3.get("is_error") is True, (r3.get("content") or "")[:40])
r4 = T._exec_download_media(mkctx(ENTRY_TEXT), {"message_id": 4321, "kind": "video"})
ok("没有该类型媒体 ⇒ 说清楚而不是崩", r4.get("is_error") is not True and "没有可下载的" in (r4.get("content") or ""))
fw2 = FakeWeChat()
r5 = T._exec_download_media(mkctx(ENTRY_VIDEO, fw2), {"message_id": 4321, "kind": "video"})
ok("正常下载：只调 download_media，不发送", fw2.calls == [("download_media", "video", 777)], str(fw2.calls))

print("\n%d/%d 通过" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
