#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「发错会话」判据。

**报障原话**（最严重的一条；转述自网友）。

**查到的事实**：`send_text_posted` **不会切会话** —— 它靠会话头指纹/四档屏幕证据证明
"当前打开的就是目标"。证据一旦误判（指纹假阳性、OCR 读错名字、活动行时间恰好撞上），
文字就会被打进**当时打开的另一个会话**并真的发出去；而我们的成功判据是"在**目标会话**里
回读到新行"，查不到 ⇒ 表观症状只是"发送未生效"，**发错会话这件事被完全掩盖**
。
更糟的是**重试**：每多打一枪，就往那个错会话**再发一遍**同一句话。

本判据守四件事：
  ① 目标会话回读失败时，能**当场查出**这句其实落到了哪个会话（全离线、只读库）；
  ② 命中就**立刻停手**（不许再补枪 ⇒ 不许重复发到错会话）；
  ③ 只在另一个会话里**时间接近**的相同文字才算（防"同一句话历史上说过"式假阳性）；
  ④ 正常发送（目标会话回读成功）根本不走这条 ⇒ 不给主路径加开销。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) # 同目录的 `_srcslice`
import _srcslice
from _probe import assert_probe_hit   # 分母守卫：先自证探针有效，再断言结论
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent.wechat import WeChatAdapter # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  OK   " + name)
    else:
        FAIL += 1
        print("  FAIL " + name + ("   [%s]" % extra if extra else ""))


class _FakeDB(object):
    def __init__(self, rows):
        self.rows = rows # {chat_id: [ {content, create_time}, … ]}
        self.asked = []

    def get_messages(self, chat_id, limit=4):
        self.asked.append(str(chat_id))
        return list(self.rows.get(str(chat_id), []))[:limit]


def _adapter(rows, groups=None, privates=None):
    ad = WeChatAdapter.__new__(WeChatAdapter) # 不走 __init__：只测这一个纯函数
    ad._db = _FakeDB(rows)
    ad._groups = groups if groups is not None else [{"wxid": "群A@chatroom", "nickname": "实验群"},
                                                    {"wxid": "群B@chatroom", "nickname": "大群"}]
    ad._privates = privates or []
    ad._echo_norm = WeChatAdapter._echo_norm
    import types
    ad._text_landed_in_other_chat = types.MethodType(
        WeChatAdapter._text_landed_in_other_chat, ad)
    return ad


import time # noqa: E402

NOW = int(time.time())


def _searched(ad, target: str) -> int:
    """**分母**：这次调用真的往"目标以外"的会话查过几个库。
    ⛔ 为什么要它：本判据好几条是"找不到 ⇒ None"这种**负向断言** ——
       探针一失效（`_groups`/`_privates` 空了、排除逻辑把谁都排掉了、读库整个没跑），
       "别处没有这句"就**自动成立**、判据静默变绿。⇒ 每个"0 次/Nothing"前面先钉分母。"""
    return len({str(c) for c in ad._db.asked} - {str(target)})


def _adapter_raising():
    """读库就抛异常的适配器（真实场景：库被清空/锁住）。"""
    ad = _adapter({})

    class _Boom(object):
        def __init__(self):
            self.asked = []

        def get_messages(self, chat_id, limit=4):
            self.asked.append(str(chat_id))
            raise RuntimeError("库坏了")

    ad._db = _Boom()
    return ad

print("── A. 目标会话回读失败 ⇒ 查得出这句落到了哪个会话 ──")
ad = _adapter({
    "群B@chatroom": [{"content": "晚上一起去吃火锅吧", "create_time": NOW - 5}],
})
hit = ad._text_landed_in_other_chat("晚上一起去吃火锅吧", "群A@chatroom")
assert_probe_hit(ok, "A0 探针有效：真的往**目标以外**的会话查过库", _searched(ad, "群A@chatroom"))
ok("在别的会话里找到 ⇒ 返回那个会话", bool(hit) and hit[0] == "群B@chatroom", str(hit))
ok("带上可读的会话名（给用户/日志看得懂）", bool(hit) and hit[1] == "大群", str(hit))

print("── B. 没落到别处 ⇒ 不误报（正常发送路径不受影响）──")
ad2 = _adapter({"群B@chatroom": [{"content": "别的话", "create_time": NOW - 5}]})
_b2 = ad2._text_landed_in_other_chat("晚上一起去吃火锅吧", "群A@chatroom")
assert_probe_hit(ok, "B0 探针有效：B 段也真的往别的会话查了（否则『没落到别处 ⇒ None』是空转）",
                 _searched(ad2, "群A@chatroom"))
ok("别处没有这句 ⇒ None", _b2 is None)
ad3 = _adapter({"群A@chatroom": [{"content": "晚上一起去吃火锅吧", "create_time": NOW - 5}]})
ok("只在自己（目标）会话里 ⇒ None（不算发错）",
   ad3._text_landed_in_other_chat("晚上一起去吃火锅吧", "群A@chatroom") is None)

print("── C. 时间窗：陈年旧话不算（防同一句话历史上说过就误报）──")
ad4 = _adapter({"群B@chatroom": [{"content": "晚上一起去吃火锅吧", "create_time": NOW - 3600}]})
_c4 = ad4._text_landed_in_other_chat("晚上一起去吃火锅吧", "群A@chatroom")
assert_probe_hit(ok, "C0 探针有效：时间窗用例也真的查过别的会话", _searched(ad4, "群A@chatroom"))
ok("1 小时前的那句 ⇒ None", _c4 is None)
ad5 = _adapter({"群B@chatroom": [{"content": "晚上一起去吃火锅吧", "create_time": NOW - 30}]})
ok("30 秒前的那句 ⇒ 命中", bool(ad5._text_landed_in_other_chat("晚上一起去吃火锅吧", "群A@chatroom")))
ok("窗口可调（window_s=10 时 30 秒前不算）",
   ad5._text_landed_in_other_chat("晚上一起去吃火锅吧", "群A@chatroom", window_s=10) is None)

print("── D. 归一化：空白/全角标点差异也算同一句 ──")
ad6 = _adapter({"群B@chatroom": [{"content": "晚上一起去吃火锅吧 ！", "create_time": NOW - 3}]})
ok("多一个空格 + 全角叹号 ⇒ 仍命中",
   bool(ad6._text_landed_in_other_chat("晚上一起去吃火锅吧！", "群A@chatroom")))
ad7 = _adapter({"群B@chatroom": [{"content": "wxid_abc: 晚上一起去吃火锅吧", "create_time": NOW - 3}]})
ok("库回读带发送者前缀 ⇒ 包含关系也命中",
   bool(ad7._text_landed_in_other_chat("晚上一起去吃火锅吧", "群A@chatroom")))

print("── E. 私聊也在扫描范围内（不只群）──")
ad8 = _adapter({"wxid_E": [{"content": "晚上一起去吃火锅吧", "create_time": NOW - 3}]},
               groups=[], privates=[{"wxid": "wxid_E", "name": "E"}])
h8 = ad8._text_landed_in_other_chat("晚上一起去吃火锅吧", "群A@chatroom")
assert_probe_hit(ok, "E0 探针有效：私聊真的进了扫描面（只配私聊、不配群）",
                 _searched(ad8, "群A@chatroom"))
ok("私聊里命中 ⇒ 报出私聊", bool(h8) and h8[0] == "wxid_E", str(h8))

print("── F. 边界：空文本/极短文本不判（防乱报）──")
ok("空文本 ⇒ None", ad._text_landed_in_other_chat("", "群A@chatroom") is None)
ok("1 个字 ⇒ None", ad._text_landed_in_other_chat("好", "群A@chatroom") is None)
_raise_ad = _adapter_raising()
_raise_r = _raise_ad._text_landed_in_other_chat("晚上一起去吃火锅吧", "群A@chatroom")
assert_probe_hit(ok, "F0 探针有效：读库真的被调用过（否则『不崩』也是空转）",
                 len(_raise_ad._db.asked))
ok("读库抛异常 ⇒ 不崩、按未命中", _raise_r is None)

print("── G. 源码级接线：命中就停手 + 只在回读失败时问 ──")
_SRC = open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read()
_seg = _srcslice.from_func(_SRC, "send_text_posted")
_seg = _seg[:_seg.index("def send_image_posted(")]
ok("开枪循环里有发错会话自检", "_text_landed_in_other_chat(text, chat_id)" in _seg)
_i_loop = _seg.index("for _i in range(1, 4):")
_i_guard = _seg.index("_text_landed_in_other_chat(text, chat_id)")
ok("自检在开枪循环**之内**（下一枪之前就能拦住）", _i_loop < _i_guard)
ok("只在**至少打过一枪**之后才问（没开枪就没什么可查的）", "if _fired:" in _seg[:_i_guard + 1][-400:])
ok("命中即返回失败（不再补枪）",
   "已立刻停止重试" in _seg and "return False, (\"❗**发错会话**" in _seg)
ok("同时落 error 级日志（现场可查）", 'log.error("❗发错会话' in _seg)
ok("正常路径不调用它（只在回读失败后走到）",
   _seg.index('_self_local_note(self, chat_id, head.get("local_id")') < _i_guard)

print("\n==== 发错会话判据：%d 通过 / %d 失败 ====" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
