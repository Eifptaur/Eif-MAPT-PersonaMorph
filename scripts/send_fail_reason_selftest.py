#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「发送失败的原因必须透传出来」判据（2026-09-16 用户转述报障后立）。

**报障原话**：「然后就是发送消息可能会失败 不清楚什么情况 **看思维链说是工具没有发送成功**，用户反馈啥情况」

**查到的事实**：`agent/tools.py::_exec_send_message` 在"部分成功、部分失败"时，
只把**条数**写进给模型的 note（"另有 N 条发送失败，请稍后再试或减少条数"），
把 `send_text_batch` 逐条带上来的 `error`（真原因：会话头不匹配拒发 / 判据不可用 / 三枪没打出去…）
**原样丢掉** ⇒ 模型不知道为什么失败（只能瞎重试）、用户看运行明细也只看到"发送失败"一句。
**全失败**那条路（`send_text_batch` 直接抛 RuntimeError）本来是把原因带出去的 —— 两条路不一致，
用户看到的正好是"丢了原因"的那条。

本判据守三件事：
  ① 部分失败时，**每条的真实原因**出现在工具返回值里（模型与用户都看得到）；
  ② 原因同时落日志（`log.warning`，排障时不用去翻模型上下文）；
  ③ 全失败那条路继续带原因（不许在修这条时把那条改坏）。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import tools as T          # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % extra if extra else ""))


class _FakeSender(object):
    """假发送层：部分成功 + 部分失败，每条失败都带真原因。"""

    def __init__(self, fatal=False):
        self.fatal = fatal
        self.calls = []

    def send_text_batch(self, chat_key, messages, **kw):
        self.calls.append({"chat_key": chat_key, "messages": list(messages)})
        if self.fatal:
            raise RuntimeError("第1条「你好」：投递发送失败：三枪都没打出去（投递回车(没打出去)→…）")
        return {"sent": [{"text": "第一句", "at": "12:00"}],
                "failed": [{"index": 1, "text": "第二句",
                            "error": "会话头不匹配，拒绝投递（防发错会话）：当前尺寸没目标会话的参照"},
                           {"index": 2, "text": "第三句",
                            "error": "已投递 3 枪但 DB 没等到新行；**文字可能还留在输入框里**"}]}


class _FakeStore(object):
    def find_by_mid(self, chat_key, mid):
        return None


def _ctx(sender):
    return {"sender": sender, "store": _FakeStore(), "session": {"sent": []},
            "chat_key": "group:123@chatroom"}


print("── A. 部分失败：每条原因必须进工具返回值（模型与用户都看得见）──")
r = T._exec_send_message(_ctx(_FakeSender()), {"messages": ["第一句", "第二句", "第三句"]})
ok("不是错误态（有成功的部分 ⇒ 正常返回）", not r.get("is_error"), str(r)[:80])
body = r.get("content") or ""
ok("返回值里有 failed 明细字段", "failed" in body, body[:120])
ok("**第二条的真实原因在返回值里**（会话头不匹配）", "会话头不匹配" in body, body[:200])
ok("**第三条的真实原因在返回值里**（文字可能还留在输入框里）", "文字可能还留在输入框里" in body, body[:200])
ok("写清了哪几条失败（按 1 起算的序号）", "第2条" in body and "第3条" in body, body[:200])
ok("保留原来的纪律：成功的不需要重发", "不需要重发" in body, body[:200])
ok("明确叫模型**照原因处理**、别盲目重试", "别盲目重试" in body, body[:220])
try:
    data = json.loads(body)
    ok("返回值是可解析的 JSON（模型好读）", isinstance(data, dict) and isinstance(data.get("failed"), list),
       str(type(data)))
    ok("failed 条数 = 2", len(data.get("failed") or []) == 2, str(data.get("failed"))[:100])
    ok("每条 failed 带 index/text/error 三个字段",
       all({"index", "text", "error"} <= set(x) for x in data["failed"]), str(data["failed"])[:120])
except Exception as e:
    ok("返回值是可解析的 JSON（模型好读）", False, str(e)[:60])

print("── B. 全部成功：不许画蛇添足 ──")


class _AllOk(_FakeSender):
    def send_text_batch(self, chat_key, messages, **kw):
        return {"sent": [{"text": t, "at": "12:00"} for t in messages], "failed": []}


r2 = T._exec_send_message(_ctx(_AllOk()), {"messages": ["只有一句"]})
ok("成功时是 ok 且没有 failed 字段", (not r2.get("is_error")) and ("failed" not in (r2.get("content") or "")),
   str(r2.get("content"))[:100])
ok("成功时仍带原来的 note（不许输出「已发送」类汇报）",
   "不要输出" in (r2.get("content") or ""), str(r2.get("content"))[:120])

print("── C. 全失败那条路：原因照样带出去（别在修 A 时改坏它）──")
r3 = T._exec_send_message(_ctx(_FakeSender(fatal=True)), {"messages": ["你好"]})
ok("全失败 ⇒ is_error", bool(r3.get("is_error")), str(r3)[:80])
ok("全失败 ⇒ 原因在错误文本里（三枪都没打出去）", "三枪都没打出去" in (r3.get("content") or ""),
   str(r3.get("content"))[:160])

print("── D. 空内容仍然拒绝（别为了透传原因放宽入口）──")
r4 = T._exec_send_message(_ctx(_FakeSender()), {"messages": []})
ok("空消息 ⇒ 错误", bool(r4.get("is_error")), str(r4)[:80])

print("── E. 源码级：原因透传与日志两处都在（防以后有人又把它简化成「只报条数」）──")
_src = open(os.path.join(ROOT, "agent", "tools.py"), encoding="utf-8").read()
_seg = _src[_src.index("def _exec_send_message("):]
_seg = _seg[:_seg.index("def _exec_get_recent(")]
ok("返回值里带 failed 明细", '"failed": _why' in _seg, _seg[:0])
ok("逐条原因拼进 note", '_f["error"]' in _seg)
ok("同时落日志 log.warning", "log.warning(\"发送失败" in _seg)
ok("旧那句「只报条数」的话术已经不在", "请稍后再试或减少条数" not in _seg)
ok("tools.py 有模块级 logger（与 wechat.py 同名）",
   'log = logging.getLogger("persona-morph")' in _src)

print("\n==== 失败原因透传判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
