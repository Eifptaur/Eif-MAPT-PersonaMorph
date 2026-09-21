# -*- coding: utf-8 -*-
"""风险闸门自测（不需要微信、不需要网络）。

跑法：runtime\\python\\python.exe scripts\\risk_selftest.py
判据：全部 PASS、0 FAIL —— 覆盖拦截四层、防误报（时钟回拨/手工发送不计数）、
状态原子落盘、停机开关与自动升级、以及"提示只留本机"。
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import config as C
from agent import risk as R

PASS = FAIL = 0
DETAIL = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, extra))
        DETAIL.append(name)


def set_risk(**kw):
    cfg = copy.deepcopy(C.get_config())
    risk = dict(cfg.get("risk") or {})
    risk.update(kw)
    cfg["risk"] = risk
    C.set_config(cfg)


def new_gate():
    d = tempfile.mkdtemp(prefix="risk_")
    return R.RiskGate(path=os.path.join(d, "risk_state.json"),
                      event_path=os.path.join(d, "risk_events.jsonl")), d


TMPDIRS = []
BASE = time.time()

# ── 1 放行 + 记账 ───────────────────────────────────────────────────────
set_risk(enabled=True, paused=False, per_minute=3, per_hour=100, per_day=100,
         per_chat_per_hour=2, min_gap_seconds=3, quiet_hours=[], max_links=1,
         watch_keywords=["发票"], block_keywords=["刷单"], escalate_after=99,
         dup_window_seconds=120, dup_min_len=8)
g, d = new_gate(); TMPDIRS.append(d)
v = g.check("group:aa", "你好呀", now=BASE)
check("正常消息放行", v.allowed and v.code == "ok", repr(v))
g.note_sent("group:aa", "你好呀", now=BASE)
snap = g.snapshot()
check("note_sent 记账", snap["minute"] == 1 and snap["day"] == 1, str(snap))

# ── 2 同会话最小间隔 ────────────────────────────────────────────────────
v = g.check("group:aa", "第二条", now=BASE + 1)
check("同会话间隔太短被拦", (not v.allowed) and v.code == "chat_gap", repr(v))
check("间隔拦截带 retry_after", v.retry_after >= 1, repr(v))
v = g.check("group:aa", "第二条", now=BASE + 4)
check("过了间隔就放行", v.allowed, repr(v))

# ── 3 每分钟上限（全局）─────────────────────────────────────────────────
g2, d2 = new_gate(); TMPDIRS.append(d2)
for i in range(3):
    g2.note_sent("group:x", "msg%d" % i, now=BASE + i * 10)
v = g2.check("group:y", "再发", now=BASE + 40)
check("每分钟上限拦下", (not v.allowed) and v.code == "global_min", repr(v))
check("每分钟拦截文案含上限", "每分钟上限 3 条" in v.message, v.message)

# ── 4 同会话每小时上限 ──────────────────────────────────────────────────
set_risk(per_minute=99, per_hour=100, per_chat_per_hour=2)
g3, d3 = new_gate(); TMPDIRS.append(d3)
g3.note_sent("group:z", "a", now=BASE)
g3.note_sent("group:z", "b", now=BASE + 60)
v = g3.check("group:z", "c", now=BASE + 120)
check("同会话每小时上限拦下", (not v.allowed) and v.code == "chat_hour", repr(v))

# ── 5 重复内容 ──────────────────────────────────────────────────────────
set_risk(per_chat_per_hour=99, min_gap_seconds=0)
g4, d4 = new_gate(); TMPDIRS.append(d4)
g4.note_sent("group:d", "今天天气不错我们出去玩吧", now=BASE)
v = g4.check("group:d", "今天天气不错我们出去玩吧", now=BASE + 5)
check("重复内容拦下", (not v.allowed) and v.code == "duplicate", repr(v))
v = g4.check("group:d", "短", now=BASE + 6)
check("过短内容不做重复判定", v.allowed, repr(v))

# ── 6 夜间静默 ──────────────────────────────────────────────────────────
set_risk(quiet_hours=[0, 24], min_gap_seconds=0, per_chat_per_hour=99, per_minute=99)
g5, d5 = new_gate(); TMPDIRS.append(d5)
v = g5.check("group:q", "夜里发", now=BASE)
check("静默时段拦下", (not v.allowed) and v.code == "quiet_hours", repr(v))
check("静默文案给出时段", "00:00-24:00" in v.message, v.message)
set_risk(quiet_hours=[])

# ── 7 停机开关（L0）──────────────────────────────────────────────────────
g6, d6 = new_gate(); TMPDIRS.append(d6)
g6.pause("连续 5 次被风险闸门拦下")
v = g6.check("group:k", "还想发", now=BASE)
check("停机开关拦下", (not v.allowed) and v.level == "L0", repr(v))
check("停机文案含原因", "连续 5 次被风险闸门拦下" in v.message, v.message)
g6.resume()
check("恢复后可发", g6.check("group:k", "现在可以发", now=BASE).allowed)

# ── 8 连续被拦自动升级暂停 ──────────────────────────────────────────────
set_risk(escalate_after=2, min_gap_seconds=99, per_chat_per_hour=99, quiet_hours=[])
g7, d7 = new_gate(); TMPDIRS.append(d7)
g7.note_sent("group:e", "先发一条", now=BASE)
v1 = g7.check("group:e", "马上再发", now=BASE + 1)
check("第一次被拦", not v1.allowed, repr(v1))
v2 = g7.check("group:e", "又发", now=BASE + 2)
check("第二次被拦", not v2.allowed, repr(v2))
check("连续被拦后自动暂停", g7.is_paused(), "paused=%s" % g7.is_paused())

# ── 9 状态原子落盘 + 重新载入 ───────────────────────────────────────────
g8, d8 = new_gate(); TMPDIRS.append(d8)
g8.note_sent("group:p", "落盘测试", now=BASE)
check("无 .tmp 残留", not os.path.exists(g8.path + ".tmp"))
reload_gate = R.RiskGate(path=g8.path, event_path=g8.event_path)
check("计数跨实例保留", len(reload_gate._st["min"]) == 1, str(reload_gate._st["min"]))

# ── 10 时钟回拨（未来时间戳）不卡死 ─────────────────────────────────────
g9, d9 = new_gate(); TMPDIRS.append(d9)
future = time.time() + 3600
g9.note_sent("group:t", "未来时间戳", now=future)
set_risk(per_minute=1)
v = g9.check("group:t", "时钟回拨后再发", now=future - 3600)
check("未来时间戳被丢弃、闸门不卡死", v.allowed, repr(v))

# ── 11 L3：链接过多 → 放行但记录 ───────────────────────────────────────
set_risk(per_minute=99, max_links=1, min_gap_seconds=0, per_chat_per_hour=99)
g10, d10 = new_gate(); TMPDIRS.append(d10)
v = g10.check("group:l", "看这个 https://a.com 和 https://b.com", now=BASE)
check("链接过多放行(L3)", v.allowed and v.level == "L3" and v.code == "many_links", repr(v))

# ── 12 禁止词拦截 ───────────────────────────────────────────────────────
v = g10.check("group:l", "帮我刷单", now=BASE)
check("禁止词拦下", (not v.allowed) and v.code == "block_keyword", repr(v))

# ── 13 观察词只记录 ─────────────────────────────────────────────────────
v = g10.check("group:l", "需要发票吗", now=BASE)
check("观察词放行并记录", v.allowed and v.code == "watch_keyword", repr(v))

# ── 14 enabled=False 全放行 ─────────────────────────────────────────────
set_risk(enabled=False, paused=True)
g11, d11 = new_gate(); TMPDIRS.append(d11)
g11.pause("停机")
v = g11.check("group:off", "关闸门就别拦", now=BASE)
check("enabled=False 时全放行", v.allowed and v.code == "disabled", repr(v))
set_risk(enabled=True, paused=False)

# ── 15 manual=True 不计数、不升级 ───────────────────────────────────────
set_risk(escalate_after=1, min_gap_seconds=99, per_chat_per_hour=99)
g12, d12 = new_gate(); TMPDIRS.append(d12)
g12.note_sent("group:m", "x", now=BASE)
v = g12.check("group:m", "手动测试发送", now=BASE + 1, manual=True)
check("手工发送也被拦（规则一致）", not v.allowed, repr(v))
check("手工发送不触发自动暂停", not g12.is_paused(), "paused=%s" % g12.is_paused())

# ── 16 事件留痕（只在本机文件里，不往微信侧发）──────────────────────────
g13, d13 = new_gate(); TMPDIRS.append(d13)
g13.check("group:ev", "正常", now=BASE)
lines = []
if os.path.exists(g13.event_path):
    lines = [l for l in open(g13.event_path, encoding="utf-8").read().splitlines() if l.strip()]
check("事件写入本机 jsonl", len(lines) >= 1, str(lines[:1]))
ev = json.loads(lines[0]) if lines else {}
check("事件含 level/code/chat 字段", {"level", "code", "chat"} <= set(ev.keys()), str(ev))

# ── 17 snapshot 给控制台的字段齐全 ──────────────────────────────────────
sn = g13.snapshot()
need = {"enabled", "paused", "paused_reason", "blocks", "minute", "minute_cap",
        "hour", "hour_cap", "day", "day_cap", "quiet_hours", "events"}
check("snapshot 字段齐全", need <= set(sn.keys()), str(need - set(sn.keys())))

for d in TMPDIRS:
    shutil.rmtree(d, ignore_errors=True)

# ── 18 默认（节奏不限）必须不拦：口径＝节奏交给用户，闸门重心在内容/任务层 ──
C.set_config(copy.deepcopy(C.get_config()))     # 清掉前面 set_risk 的覆盖
g14, d14 = new_gate(); TMPDIRS.append(d14)
allowed_all = True
for i in range(10):
    v = g14.check("group:u%d" % i, "今天聊点别的 %d" % i, now=BASE + i * 0.5)
    if not v.allowed:
        allowed_all = False
        break
check("默认节奏不限：连发 10 个会话都放行", allowed_all, repr(v))
check("默认默认值就是 0/关", (R.DEFAULTS["per_minute"] == 0 and R.DEFAULTS["per_hour"] == 0
                              and R.DEFAULTS["per_day"] == 0
                              and R.DEFAULTS["per_chat_per_hour"] == 0
                              and R.DEFAULTS["min_gap_seconds"] == 0
                              and R.DEFAULTS["quiet_hours"] == []), str(R.DEFAULTS))

# ── 19 任务层红线：同一内容发给多个会话 ⇒ 判群发 ────────────────────────
set_risk(broadcast_chats=3, broadcast_window_seconds=300, per_minute=99,
         per_chat_per_hour=99, min_gap_seconds=0, quiet_hours=[])
g15, d15 = new_gate(); TMPDIRS.append(d15)
same = "今晚八点直播间不见不散快来下单"
g15.note_sent("group:a1", same, now=BASE)
v1 = g15.check("group:a2", same, now=BASE + 1)
check("发到第 2 个会话还没判群发", v1.allowed, repr(v1))
g15.note_sent("group:a2", same, now=BASE + 1)
v2 = g15.check("group:a3", same, now=BASE + 2)
check("同一内容第 3 个会话 ⇒ 拦（群发特征）", (not v2.allowed) and v2.code == "broadcast", repr(v2))
check("群发拦截文案说明是任务层红线", "群发" in v2.message and "不做" in v2.message, v2.message)
check("群发拦截带会话明细", isinstance(v2.detail.get("chats"), list) and len(v2.detail["chats"]) >= 2, str(v2.detail))

# ── 20 群发判定的防误报 ─────────────────────────────────────────────────
set_risk(broadcast_chats=3, broadcast_window_seconds=300)
g16, d16 = new_gate(); TMPDIRS.append(g16)
for i in range(4):
    g16.note_sent("group:b%d" % i, "各聊各的 %d" % i, now=BASE + i)
v = g16.check("group:b9", "又是另一句完全不同的内容", now=BASE + 5)
check("内容各不相同 ⇒ 不判群发（防误报）", v.allowed, repr(v))
g17, d17 = new_gate(); TMPDIRS.append(d17)
for i in range(3):
    g17.note_sent("group:c%d" % i, "嗯嗯", now=BASE + i)
v = g17.check("group:c9", "嗯嗯", now=BASE + 5)
check("过短内容不判群发（防误报）", v.allowed, repr(v))
set_risk(broadcast_chats=0)
g18, d18 = new_gate(); TMPDIRS.append(d18)
for i in range(4):
    g18.note_sent("group:d%d" % i, "同一条广告词发发发", now=BASE + i)
v = g18.check("group:d9", "同一条广告词发发发", now=BASE + 5)
check("broadcast_chats=0 ⇒ 用户关掉群发判定就完全不拦", v.allowed, repr(v))

# ── 21 关键词写成"逗号分隔字符串"也要正确切分（防逐字符误拦）────────────
set_risk(block_keywords="刷单,加粉", watch_keywords="发票 退税", paused=False,
         escalate_after=0, per_minute=0, per_chat_per_hour=0, min_gap_seconds=0,
         broadcast_chats=0)
g19, d19 = new_gate(); TMPDIRS.append(d19)
check("字符串型禁止词被切成列表", R._as_list("刷单,加粉") == ["刷单", "加粉"],
      str(R._as_list("刷单,加粉")))
check("空格/分号也能切", R._as_list("a;b c，d") == ["a", "b", "c", "d"], str(R._as_list("a;b c，d")))
v = g19.check("group:kw", "帮我刷单", now=BASE)
check("字符串型禁止词：完整词命中 ⇒ 拦", (not v.allowed) and v.code == "block_keyword", repr(v))
v = g19.check("group:kw", "今天刷了个单子", now=BASE)
check("字符串型禁止词：单字'单'不误拦（证明没逐字符）", v.allowed, repr(v))
v = g19.check("group:kw", "需要开发票吗", now=BASE)
check("字符串型观察词命中 ⇒ 放行但记录", v.allowed and v.code == "watch_keyword", repr(v))

# ⛔ 2026-09-22 加（第十三轮 **V-R13-8** · P3）：事件台账**默认跟随 `path` 所在目录** ——
#   老写法默认写死产品 `data/risk_events.jsonl` ⇒ 只把 state 指到临时档的判据/探针照样写**产品台账**
#   （第十二轮我自己的探针就这么写进去 3 行）。这条锚：只给 `path` 时，事件必须落在它旁边。
_d8 = tempfile.mkdtemp(prefix="risk_ev8_")
TMPDIRS.append(_d8)
_g8sep = R.RiskGate(path=os.path.join(_d8, "risk_state.json"))       # ⚠️ **不传** event_path
check("V-R13-8 只给 `path` ⇒ 事件台账写在**它旁边**（不写产品 data/）",
      os.path.abspath(_g8sep.event_path) == os.path.abspath(os.path.join(_d8, "risk_events.jsonl"))
      and os.path.abspath(_g8sep.event_path) != os.path.abspath(getattr(R, "EVENT_PATH", "")),
      _g8sep.event_path)
check("V-R13-8 产品调用点（两个都不传）行为不变：仍然落在 `data/risk_events.jsonl`",
      os.path.abspath(R.RiskGate().event_path) == os.path.abspath(getattr(R, "EVENT_PATH", "")),
      R.RiskGate().event_path)

print("\n=== 风险闸门自测：%d PASS / %d FAIL ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
