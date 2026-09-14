# -*- coding: utf-8 -*-
"""对照审计三条修复的判据（2026-09-15）——不需要微信、不需要起服务。

背景：用户拿他朋友 QQ Agent 的三个毛病问"我们也会有类似的问题吗"。查证结果：**三条我们都有**，
本条判据就是把这三次修法钉住，防止回退：
  A **缓存命中率**：用户消息的分段顺序必须"历史在前、易变在后"，且历史块开头不许挂会变的句子
     ⇒ 用"两轮之间的公共前缀占比"当硬判据（这才是缓存的真实语义）。
  B **思考入记忆**：内心判断必须能留痕、且被记忆整理的输入引用（不靠打开思考）。
  C **三档被随机数卡住**：引用/回复我、接着我的话往下说 ⇒ **不吃随机数**必回。
  D **顺带修的真 bug**：生产路径必须把 chat_key/group_name/store 传进闸门
     （否则每群独立档位/屏蔽名单/指令禁言全是死代码）。
"""
import io
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("PASS" if cond else "FAIL", name, (" · " + extra) if extra else ""))


from agent import prompt as P            # noqa: E402
from agent import thought_trace as TT    # noqa: E402

# ── A 缓存：分段顺序 + 前缀复用率 ────────────────────────────────────────
print("[A] 缓存命中率（历史在前、易变在后；前缀能复用）")


class FakeStore:
    """最小的 store：recent() 返回按时间排序的消息（旧→新）。"""

    def __init__(self, msgs):
        self._m = list(msgs)

    def recent(self, chat_key, limit=80, **kw):
        return self._m[-limit:]

    def past(self, chat_key, limit=80):
        return self._m[-limit:]


def mk(i, text, self_=False):
    return {"id": i, "mid": i, "ts": int(time.time() * 1000) - (100 - i) * 60000, "sender_id": "me" if self_ else "u%d" % i,
            "sender_name": "我" if self_ else "群友%d" % i, "text": text, "self": self_, "read": True,
            "reply": None, "media": []}


OLD = [mk(i, "第 %d 条历史" % i) for i in range(1, 9)]


def build(msgs, seq, trigger=None):
    ctx = {"store": FakeStore(msgs), "chat_key": "group:g1", "chat_id": "g1", "kind": "group",
           "chat_name": "测试群", "trigger_entries": trigger or [mk(99, "@我 在吗")],
           "self_nickname": "小鲸鱼", "recent_count": 3, "run_seq": seq,
           "more_unread_during_run": False, "memory": _FakeMem(), "last_message_at": int(time.time() * 1000),
           "self_last_message_at": int(time.time() * 1000) - 300000, "session": None}
    return P.build_user_prompt(ctx)


class _FakeMem:
    def format_for_prompt(self, *a, **k):
        return ""


p1 = build(OLD, 1)
p2 = build(OLD + [mk(20, "第 9 条新历史")], 2)
i_time1 = p1.find("【当前时间】")
i_time2 = p2.find("【当前时间】")
i_past1 = p1.find("【过去状态】")
i_past2 = p2.find("【过去状态】")
ck("A1 历史排在时间前面", 0 <= i_past1 < i_time1 and 0 <= i_past2 < i_time2,
   "past=%s time=%s" % (i_past1, i_time1))
def _hist(txt):
    """取【过去状态】那一段（到下一个【 段之前）。"""
    seg = txt.split("【过去状态】", 1)
    if len(seg) < 2:
        return []
    body = seg[1]
    cut = body.find("\n\n【")
    if cut >= 0:
        body = body[:cut]
    # 末尾那句「（提醒：…）」是**尾巴标记**（stale 时才出现、内容按时长变），
    # 它不属于追加序列⇒ 比附加式时先剔掉（否则永远假红）。
    return [l for l in body.splitlines()[1:] if l.strip() and not l.strip().startswith("（提醒：")]


h1, h2 = _hist(p1), _hist(p2)
_appended = len(h1) > 0 and len(h2) > len(h1) and all(h1[i] == h2[i] for i in range(len(h1)))
ck("A2 历史块是**附加式**：上一轮的每一行在下一轮原位逐字重现（缓存才吃得下）",
   _appended, "上轮 %d 行 / 本轮 %d 行" % (len(h1), len(h2)))
_share = len("\n".join(h1)) / max(1, len(p1))
print("      信息：历史块占整条 user prompt 的 %.0f%%（其余段每轮必然不同，属设计如此）" % (_share * 100))
ck("A2b 历史块非空且行数与配置相符（不该被压没）", len(h1) >= 8, "%d 行" % len(h1))
_blk = p1.split("【过去状态】")[1]
_mlines = [l for l in _blk.splitlines()[1:] if l.strip()]
ck("A3 历史块里第一条消息之前没有会变的提醒句（提醒只在末尾）",
   bool(_mlines) and not _mlines[0].strip().startswith("（提醒："), _mlines[0][:40] if _mlines else "空")
ck("A4 系统提示词里没有「当前时间」这类每轮必变的段",
   "【当前时间】" not in P.build_system_prompt())

# ── B 内心留痕 ──────────────────────────────────────────────────────────
print("[B] 思考/内心判断入记忆（低成本留痕）")
_bak = TT.FILE
_tmpdir = tempfile.mkdtemp(prefix="tt_")
TT.FILE = os.path.join(_tmpdir, "thoughts.jsonl")
try:
    TT.note("group:g1", "tier", tier=0, should=False, why="未触发", snippet="今天好累")
    TT.note("group:g1", "tier", tier=1, should=True, why="引用/回复的是我", snippet="你觉得呢")
    TT.note("group:g2", "tier", tier=0, should=False, why="未触发")
    ck("B1 分会话读回", len(TT.recent("group:g1", 10)) == 2 and len(TT.recent("group:g2", 10)) == 1)
    ck("B2 落盘是 JSONL（一行一条，能解析）",
       len([json.loads(l) for l in io.open(TT.FILE, encoding="utf-8").read().splitlines() if l.strip()]) == 3)
    fmt = TT.format_for_memory("group:g1", 10)
    ck("B3 记忆可用文本带「没回也记」的信息（回应=否 + 原因）", "回应=否" in fmt and "未触发" in fmt, fmt[:80])
    ck("B4 不写个空文件出来（没记录时返回空串）", TT.format_for_memory("group:zzz", 5) == "")
    _mk = TT.note("group:g1", "reasoning", think="先想清楚再答")
    ck("B5 推理片段也能留（可选）", any(x.get("kind") == "reasoning" for x in TT.recent("group:g1", 10)))
finally:
    TT.FILE = _bak
SRC_PM = io.open("scripts/persona_morph.py", encoding="utf-8").read()
ck("B6 档位判定处留痕（没回也记）", 'thought_trace as _tt' in SRC_PM and '_tt.note(chat_key, "tier"' in SRC_PM)
ck("B7 记忆整理吃进了内心判断", "format_for_memory" in SRC_PM and "机器人的内心判断" in SRC_PM)
ck("B8 留痕只落本地（模块里没有任何发送/网络调用）",
   "chat_completion" not in io.open("agent/thought_trace.py", encoding="utf-8").read()
   and "requests" not in io.open("agent/thought_trace.py", encoding="utf-8").read())

# ── C 确定性触发（不吃随机数）───────────────────────────────────────────
print("[C] 三档：引用我 / 接我的话头 —— 不吃随机数")


def tier(msgs, trigger, roll=99, **kw):
    return P.resolve_context_tier(trigger, "小鲸鱼", "群deepseek", "me", roll=roll,
                                 chat_key="group:g1", store=FakeStore(msgs), **kw)


_base = [mk(1, "别人说的话"), mk(2, "我上次说的那句很有用", self_=True)]
r_quote = tier(_base, [mk(9, "我上次说的那句很有用")])            # 引用我的原话（文本比对）
ck("C1 引用/回复的是我 ⇒ 必回（roll=99 也没用）",
   r_quote["should_respond"] is True and "引用" in r_quote["reason"], r_quote["reason"])
r_quote2 = tier(_base, [{"id": 9, "sender_id": "u9", "sender_name": "群友", "text": "那你说呢",
                         "self": False, "reply": {"sender_id": "me", "sender_name": "小鲸鱼"}}])
ck("C2 条目自带 reply 指向我 ⇒ 也必回", r_quote2["should_respond"] is True, r_quote2["reason"])
_just_now = dict(mk(2, "我先抛个话题", self_=True))
_just_now["ts"] = int(time.time() * 1000) - 1000          # ⚠️ 必须"刚刚"：接话头的时间窗是 180s
r_cont = tier([mk(1, "别人"), _just_now],
              [{"id": 9, "mid": 9, "ts": int(time.time() * 1000), "sender_id": "u9", "sender_name": "群友",
                "text": "那我接着说", "self": False, "reply": None, "media": []}])
ck("C3 紧接着我的话往下说 ⇒ 必回", r_cont["should_respond"] is True and "接着我" in r_cont["reason"], r_cont["reason"])
r_none = tier([mk(1, "别人"), mk(2, "很久以前我说的", self_=True)],
              [{"id": 9, "mid": 9, "ts": int(time.time() * 1000) - 3600 * 1000, "sender_id": "u9",
                "sender_name": "群友", "text": "另一件事", "self": False, "reply": None, "media": []}],
              roll=99)
ck("C4 既不是引用我、也不是接话头 ⇒ 随机数没中就照旧不回（不滥回）",
   r_none["should_respond"] is False, r_none["reason"])
r_at = tier(_base, [mk(9, "@小鲸鱼 在吗")], roll=99)
ck("C5 原有确定性触发没被破坏（被 @ ⇒ 必回）", r_at["should_respond"] is True and "艾特" in r_at["reason"])

# ── D 生产调用点传全参数（真 bug 回归）─────────────────────────────────
print("[D] 生产路径把 chat_key/group_name/store 传进闸门（否则三个功能是死代码）")
ck("D1 调用点传了 chat_key", "chat_key=chat_key" in SRC_PM)
ck("D2 调用点传了 group_name", "group_name=_group_name" in SRC_PM)
ck("D3 调用点传了 store", "store=self.store" in SRC_PM)
ck("D4 注释里记了这次修的是什么（防后人再删）", "从来没生效" in SRC_PM)

print("\n[结论] %d 通过 / %d 失败" % (len(OK), len(BAD)))
if BAD:
    print("失败项：%s" % BAD)
sys.exit(1 if BAD else 0)
