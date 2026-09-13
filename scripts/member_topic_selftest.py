#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""群友印象加强判据：除了「他是谁」，还要记得「上次聊过什么」（2026-09-13 用户需求）

用户原话：「需不需要加强群友印象的功能，让他不仅能记得群友是什么人，而且记得上次聊过的话题？」
做法（纯读消息库，不花 token、不凭空编）：`MemoryStore.format_for_prompt(..., store=, exclude_ids=)`
额外给每位群友带一行 `- 小明：上次聊过「周末去哪玩」（2 小时前）`。

判据（不需要微信、不需要起服务）：
  ① 有历史发言 ⇒ 带出「上次聊过」且内容是**本轮之前**那条，并带相对时间
  ② 本轮触发批里的发言**不许**被当成"上次聊过"
  ③ 只有本轮发言（第一次来）⇒ 不出现「上次聊过」（不许编）
  ④ 印象行照旧（不能因为加功能把"他是谁"挤掉）
  ⑤ 不传 store ⇒ 行为与老实现完全一致（向后兼容：其他调用点不受影响）
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import memory as M  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


class FakeStore:
    def __init__(self, msgs):
        self._m = list(msgs)

    def recent(self, chat_key, limit=200):
        return list(self._m)[-limit:]


NOW = int(time.time() * 1000)


def msg(mid, minutes_ago, text, uid="u1", name="小明", self_=False):
    return {"id": mid, "ts": NOW - minutes_ago * 60000, "text": text,
            "sender_id": uid, "sender_name": name, "self": self_}


class Mem(M.MemoryStore):
    """只替换 members()，其余用真实现（_last_talk/_rel_time/format_for_prompt 都是真代码）"""

    def __init__(self, members):
        self._fake = members

    def members(self, chat_key):
        return self._fake


m1 = {"userId": "u1", "name": "小明", "impressions": [{"content": "喜欢打游戏", "createdAt": NOW}], "updatedAt": NOW}

print("── A. 有历史发言 ⇒ 带出上次聊过 ──")
store = FakeStore([msg("old1", 120, "周末去哪玩啊"), msg("old2", 60, "我投一票爬山", uid="u2", name="小红")])
out = Mem([m1]).format_for_prompt("group:x", user_ids=["u1"], store=store, exclude_ids=["new1"])
ok("出现「上次聊过」", "上次聊过" in out, out.replace("\n", " / ")[:90])
ok("取的是这位群友本轮之前那条", "周末去哪玩啊" in out and "爬山" not in out)
ok("带相对时间（小时前）", "小时前" in out, out.split("上次聊过")[-1][:24])
ok("印象行照旧还在", "喜欢打游戏" in out)

print("── B. 本轮触发批不算「上次」──")
store_b = FakeStore([msg("old1", 120, "周末去哪玩啊"), msg("new1", 0, "@机器人 你说呢")])
out_b = Mem([m1]).format_for_prompt("group:x", user_ids=["u1"], store=store_b, exclude_ids=["new1"])
ok("用的是旧那条，不是触发那条", "周末去哪玩啊" in out_b and "你说呢" not in out_b, out_b.replace("\n", " / ")[:90])

print("── C. 第一次来（只有本轮发言）⇒ 不编 ──")
store_c = FakeStore([msg("new1", 0, "@机器人 你好")])
out_c = Mem([m1]).format_for_prompt("group:x", user_ids=["u1"], store=store_c, exclude_ids=["new1"])
ok("不出现「上次聊过」", "上次聊过" not in out_c, out_c.replace("\n", " / ")[:70])
ok("印象仍在", "喜欢打游戏" in out_c)

print("── D. 不传 store ⇒ 与老实现一致 ──")
out_d = Mem([m1]).format_for_prompt("group:x", user_ids=["u1"])
ok("只有印象、没有上次聊过", "上次聊过" not in out_d and "喜欢打游戏" in out_d, out_d.replace("\n", " / ")[:70])

print("── E. 只发过图片（无文字）⇒ 给占位而不是空白 ──")
store_e = FakeStore([{"id": "pic1", "ts": NOW - 30 * 60000, "text": "", "sender_id": "u1",
                      "sender_name": "小明", "media": ["x.jpg"], "self": False}])
out_e = Mem([m1]).format_for_prompt("group:x", user_ids=["u1"], store=store_e, exclude_ids=[])
ok("给「（发过图片/表情）」占位", "发过图片" in out_e, out_e.replace("\n", " / ")[:80])

print("== [member-topic] 判据：{} 通过 / {} 失败 ==".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
