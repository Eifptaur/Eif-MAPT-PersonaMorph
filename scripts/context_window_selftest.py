#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上下文兜底判据：长时间静默后触发，bot 仍然看得到上文（2026-09-13）

用户报的现象：「机器人长时间不触发，突然触发一次时完全不看上文」。
本机复现到的根因：`build_past_state` 只带 `past_window_min`（本机 30 分钟）窗内的消息，
群里静默两小时后被触发 ⇒ 窗内一条都没有 ⇒ 过去状态为空 ⇒ 提示词里写「暂无历史记录，这是你第一次参与这个会话」。
修法：新增 `store.past_floor_count`（默认 8，0=关闭）——窗内不足 N 条时把窗外最近的消息补进来，
并插入"这些是更早的历史、不是本轮问题"的提醒。

判据（不需要微信、不需要起服务）：
  ① 长静默：只有 2 小时前的 5 条 ⇒ 仍能取到全部（count ≥ 5），且有"已经是 X 之前"的提醒
  ② 混合：窗内 1 条 + 窗外 5 条 ⇒ 两条都在（窗外历史不被丢）
  ③ 真·首次（store 空）⇒ count=0（这才该说"第一次参与"）
  ④ 关闭兜底（past_floor_count=0）⇒ 行为回退到老时间窗（只有窗内消息）
  ⑤ 提示词层：store 有消息但取不到时，**不许**再说"这是你第一次参与这个会话"
  ⑥ 时间窗仍然生效（窗内消息多于兜底条数时，不会把窗口外的旧消息也拉进来）
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import prompt as P  # noqa: E402

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

    def recent(self, chat_key, limit=20):
        return list(self._m)[-limit:]


NOW = int(time.time() * 1000)


def mk(msg_id, minutes_ago, text):
    return {"id": msg_id, "ts": NOW - minutes_ago * 60000, "text": text, "sender_name": "小明"}


def cfg(**kw):
    base = {"all_count": 30, "past_window_min": 30}
    base.update(kw)
    return {"store": base}


old5 = [mk("o%d" % i, 120 + i, "两小时前的第 %d 条：周末去哪儿" % i) for i in range(1, 6)]

print("── A. 长静默（只有 2 小时前的 5 条）──")
P.get_config = lambda: cfg()
r = P.build_past_state(FakeStore(old5), "group:x", limit=30)
ok("补到了全部历史（count ≥ 5）", r["count"] >= 5, "count=%s widened=%s" % (r["count"], r["widened"]))
ok("文本里有那 5 条的内容", all("第 %d 条" % i in r["text"] for i in range(1, 6)))
ok("有『已经是 X 之前』的提醒", "已经是" in r["text"] and "2 小时" in r["text"], r["text"][:46])
ok("取证件 stale/gap_min 正确", r["stale"] is True and r["gap_min"] >= 110, "gap_min=%s" % r["gap_min"])

print("── B. 混合（窗内 1 条 + 窗外 5 条）──")
mixed = old5 + [mk("n1", 1, "@机器人 你还在吗")]
rb = P.build_past_state(FakeStore(mixed), "group:x", limit=30)
ok("窗外历史也在（不被丢）", "第 1 条" in rb["text"] and "你还在吗" in rb["text"], "count=%s" % rb["count"])
# 2026-09-15 改判据口径：提醒句从**开头**挪到了**末尾**（为了前缀缓存：历史块开头不再每轮变），
# 所以"新消息在末尾"要允许末尾挂着那句提醒——真正要守的性质是"旧的在前、新的在后"。
_lines = [l for l in rb["text"].rstrip().splitlines() if l.strip()]
_body = [l for l in _lines if not l.strip().startswith("（提醒：")]
ok("窗内那条在旧消息之后（不是第一行）",
   bool(_body) and any("你还在吗" in l for l in _body)
   and _body.index([l for l in _body if "你还在吗" in l][0]) > 0, "共 %d 行" % len(_lines))
ok("提醒句挪到末尾（不再插在历史开头 ⇒ 前缀缓存稳）",
   bool(_lines) and _lines[-1].strip().startswith("（提醒："), (_lines[-1][:36] if _lines else ""))

print("── C. 真·首次（store 空）──")
rc = P.build_past_state(FakeStore([]), "group:x", limit=30)
ok("空 store ⇒ count=0 且 store_has=0", rc["count"] == 0 and rc["store_has"] == 0)

print("── D. 关闭兜底（past_floor_count=0）＝老行为 ──")
P.get_config = lambda: cfg(past_floor_count=0)
rd = P.build_past_state(FakeStore(old5), "group:x", limit=30)
ok("关掉兜底后窗外的全丢（回退老口径）", rd["count"] == 0, "count=%s" % rd["count"])

print("── E. 时间窗仍然生效（窗内够多时不拉窗外）──")
P.get_config = lambda: cfg()
many = [mk("w%d" % i, 10 + i, "窗内第 %d 条" % i) for i in range(1, 13)] + [mk("far", 300, "很久以前的话题")]
re_ = P.build_past_state(FakeStore(many), "group:x", limit=30)
ok("窗内有 12 条时，不把窗外的『很久以前』拉进来", "很久以前" not in re_["text"], "count=%s" % re_["count"])

print("── F. 提示词层：不许谎称『第一次参与』──")


class Ctx(dict):
    """缺 key 返回 None（build_user_prompt 会用到很多可选字段）"""

    def __missing__(self, k):
        return None


class FakeMemory:
    def format_for_prompt(self, chat_key, user_ids=None):
        return ""


def make_ctx(store):
    return Ctx({"store": store, "chat_key": "group:x", "trigger_entries": [], "context_limit": 30,
                "kind": "group", "chat_id": "123", "chat_name": "测试群", "run_seq": 1, "recent_count": 0,
                "last_message_at": NOW - 120 * 60000, "self_last_message_at": 0, "proactive": None,
                "memory": FakeMemory(), "session": None, "self_nickname": "机器人", "more_unread_during_run": False})


P.get_config = lambda: cfg(past_floor_count=0)   # 关掉兜底，制造"有消息但取不到"的极端场景
up_stale = P.build_user_prompt(make_ctx(FakeStore(old5)))
ok("store 有消息但取不到时，不再说『这是你第一次参与这个会话』",
   "这是你第一次参与这个会话" not in up_stale and "没能取到" in up_stale,
   (up_stale.split("【过去状态】")[1][:52] if "【过去状态】" in up_stale else up_stale[:52]))

P.get_config = lambda: cfg()
up_old = P.build_user_prompt(make_ctx(FakeStore(old5)))
ok("开着兜底时，提示词里带上了两小时前的历史 + 提醒",
   "两小时前" in up_old and "已经是" in up_old, "长度 %d" % len(up_old))

P.get_config = lambda: cfg()
up_empty = P.build_user_prompt(make_ctx(FakeStore([])))
ok("真·首次（store 空）仍然会说『第一次参与』（这句没被删掉）",
   "这是你第一次参与这个会话" in up_empty, "长度 %d" % len(up_empty))

print("== [context-window] 判据：{} 通过 / {} 失败 ==".format(PASS, FAIL))
sys.exit(1 if FAIL else 0)
