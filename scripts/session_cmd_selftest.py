# -*- coding: utf-8 -*-
"""「会话控制三件套」判据（第 140 项）—— 重说 / 回溯 / 重启。

守四件事（都配反向锚）：
  A **解析**：只认"整句就是那个词"（群里随口一句"我刚才说的再说一遍"**不是**指令）。
  B **重说不动上下文**（★验收里专盯这条）：发「重说」**不许**把上下文清掉 ——
    而且要真的能"重来"（接线侧：它**不进拦截**，让消息照常触发一轮回复）。
  C **回溯只清"我自己在最后一条用户消息之后"说的**：用户的话不动、更早的自己的话也不动。
  D **重启要白名单**（破坏性最大）：非白名单 ⇒ 拒；白名单 ⇒ 清全部。
另守两条"诚实"：读不到消息库 / 空会话 ⇒ **如实说做不到或没什么可做**，不假装成功。

用法：runtime\\python\\python.exe scripts/session_cmd_selftest.py
"""
import io
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
import _iso14  # noqa: E402
_iso14.all_()

from agent import session_cmd as SC  # noqa: E402
from agent import tier_control as TC  # noqa: E402
from agent.store import ChatStore  # noqa: E402

PASS = FAIL = 0
CK = "sc-%d" % os.getpid()
NOW_MS = int(time.time() * 1000)


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


print("── A. 解析：只认「整句就是那个词」──")
ok("A1 三个词都认（含几种同义说法）",
   SC.parse("@小鲸鱼 重说")["action"] == "redo" and SC.parse("@小鲸鱼 回溯")["action"] == "rewind"
   and SC.parse("@小鲸鱼 重启")["action"] == "restart", "")
ok("A2 带句号也认", SC.parse("@bot 重说。")["action"] == "redo", "")
ok("A3★ 反向锚：**普通句子不误判**（群里随口一句不是指令）",
   SC.parse("我刚才说的那句话你再说一遍我看看") is None
   and SC.parse("回溯一下上周的事") is None and SC.parse("今天要不要重启服务器") is None, "")
ok("A4 `tier_control.parse_command` 把它规范成 `action=session`（**同一个入口**，没另开一套）",
   TC.parse_command("@bot 重说") == {"action": "session", "cmd": "redo", "raw": "重说"}, "")
ok("A5 禁言族没被破坏（老指令照旧）",
   TC.parse_command("@bot 禁言 30") == {"action": "mute", "minutes": 30}, "")

st = ChatStore()
st.append_incoming(CK, "m1", NOW_MS - 60000, "uid_a", "甲", "在吗")
st.append_self(CK, "第一条回答", ts=(NOW_MS - 50000) / 1000.0)
st.append_incoming(CK, "m2", NOW_MS - 30000, "uid_a", "甲", "我问你个事")
st.append_self(CK, "第二条回答（说错了）", ts=(NOW_MS - 20000) / 1000.0)
_before = len(st.recent(CK, limit=100, include_self=True))
ok("A6 分母守卫：会话里确实有 4 条（否则下面是空转）", _before == 4, str(_before))

print("\n── B. 重说：**一个字都不改** ──")
_r = SC.apply(st, CK, "redo")
_after = len(st.recent(CK, limit=100, include_self=True))
ok("B1★ 发「重说」⇒ **上下文条数不变**（'不回退'那条验收）",
   _r["ok"] and _after == _before, "%d → %d" % (_before, _after))
ok("B2 它**不动**任何消息（block 数 0 ⇒ 没有偷偷标记）",
   len(st.recent(CK, limit=100, include_self=True, include_blocked=True)) == _before, "")
ok("B3★ 连点限频：同一动作 30 秒内最多 2 次 ⇒ 第 3 次被拦下并说明",
   SC.apply(st, CK, "redo")["ok"] and (not SC.apply(st, CK, "redo")["ok"]), "")

print("\n── C. 回溯：只清「我在最后一条用户消息之后」说的 ──")
_r2 = SC.apply(st, CK, "rewind")
_vis = st.recent(CK, limit=100, include_self=True)
_texts = "|".join(str(m.get("text") or "") for m in _vis)
ok("C1★ 说错的那条**不进上下文**了（'第二条回答' 不在可见列表里）",
   _r2["ok"] and "第二条回答" not in _texts and _r2["cut"] == 1, "%s cut=%s" % (_texts, _r2["cut"]))
ok("C2★ **用户的话一条都没动**（'我问你个事' 还在）",
   "我问你个事" in _texts and "在吗" in _texts, _texts)
ok("C3★ **更早的自己的话也不动**（「第一条回答」还在 ⇒ 不是「一把全清」）",
   "第一条回答" in _texts, _texts)
ok("C4 是**标记**不是删除（`include_blocked=True` 还能看到那条，理由也留了）",
   any("第二条回答" in str(m.get("text") or "")
       for m in st.recent(CK, limit=100, include_self=True, include_blocked=True)), "")

print("\n── D. 重启：只许白名单 ──")
ok("D1★ 非白名单 ⇒ **拒**（不能让任何人一键清空上下文）",
   (not SC.apply(st, CK, "restart", is_admin=False)["ok"]), "")
_r3 = SC.apply(st, CK, "restart", is_admin=True)
ok("D2 白名单 ⇒ 清掉整个会话（4 条都进不了上下文）",
   _r3["ok"] and _r3["cut"] == 3 and st.recent(CK, limit=100, include_self=True) == [],
   "cut=%s 剩=%d" % (_r3["cut"], len(st.recent(CK, limit=100, include_self=True))))

print("\n── E. 诚实：做不到就说做不到 ──")
ok("E1 读不到消息库（store=None）⇒ 如实报、不假装成功",
   (lambda d: (not d["ok"]) and "读不到" in d["note"])(SC.apply(None, CK, "rewind")), "")
_ck2 = "sc-empty-%d" % os.getpid()
ok("E2 空会话 ⇒ 如实说「没什么可做」（**不是**报错、也不是假装清了几条）",
   (lambda d: d["ok"] and d["cut"] == 0 and "还没有消息" in d["note"])(SC.apply(st, _ck2, "rewind")), "")
ok("E3 不认识的动作 ⇒ 拒（不静默当成功）",
   (lambda d: not d["ok"])(SC.apply(st, CK, "重启宇宙")), "")

print("\n── F. 接线：`tier_control` 真的过了这一族 ──")
_tc = io.open(os.path.join(ROOT, "agent", "tier_control.py"), encoding="utf-8").read()
ok("F1 `parse_command` 里引了 `session_cmd`（唯一实现点）",
   _sm.has(_tc, "from . import session_cmd as _sc") and _sm.has(_tc, 'cmd": _c["action"]'), "")
ok("F2★ **「重说」不进拦截**（`handled` 必须是 False）—— 拦下来它就什么都不会发生",
   _sm.has(_tc, '"handled": False, "action": "session_redo"'), "")
ok("F3 回溯/重启**要拦**（`handled: True`，不必再回一句）",
   _sm.has(_tc, '"action": "session_" + str(cmd.get("cmd"))'), "")
ok("F4 权限分层写在唯一处：`restart` 要白名单、`redo`/`rewind` 不要",
   SC.NEED_ADMIN == ("restart",), str(SC.NEED_ADMIN))
# ⛔ 用**另一个会话 key**：限频状态是按会话存的，B 段已经把 `CK` 的重说额度用掉了
#   （同一 key 会拿到"刚连点过"的拒绝，F5 就变成看运气的了）。
_ck5 = "sc-f5-%d" % os.getpid()
_h = TC.handle_command(_ck5, {"text": "@bot 重说", "sender_name": "甲", "sender_id": "uid_a"}, True)
ok("F5 端到端：`handle_command` 对「重说」返回 `handled=False`（于是它会照常触发一轮回复）",
   _h.get("handled") is False and _h.get("action") == "session_redo", str(_h))

print("\n== 会话控制判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
