#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未读批切分判据（第九轮 V-R9-15/16/17 ＋ 作者口径「给模型喂的前几分钟就够了」）。

守四件事：
  A **纯函数**：`feed_window.pick_feed` 的三桶（keep/skip/retry）、@ 与引用的**时间窗豁免**、
    条数上限优先保"指向它"的、顺序不乱；
  B **store 口径**：`peek_unread` 取的是**最新** N 条（旧实现取最旧 ⇒ 积压 >200 时最新那条 @ 被吞），
    `mark_read` 只标**点名的**那几条（不碰别的）；
  C **wake 真的按新口径走**（端到端，用假 wechat + 真 ChatStore，临时目录）：陈旧闲聊不回、
    被 @ 的再旧也回、超上限的**退回未读**、全是旧闲聊时压根不叫模型；
  D 源码形态兜底：wake 里不再有 `drain_unread`／`mark_all_read` 这两个"整批标读"的动作。

边界（诚实）：wake 的端到端用的是**打桩的模型调用**（`run_agent` 不真跑），所以只验"选哪一批喂"，
不验模型输出；`is_at_me` 用的是真实现。
用法：runtime\\python\\python.exe scripts\\feed_window_selftest.py
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from agent import feed_window as FW          # noqa: E402
from agent import store as ST                # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  OK   " if cond else "  FAIL ") + name + (("  [" + str(extra) + "]") if extra else ""))


NOW = 1_800_000_000_000          # 固定"现在"（毫秒）


def _m(mid, age_min, text="", ts=None):
    return {"id": mid, "mid": "m%s" % mid, "text": text,
            "ts": (NOW - int(age_min * 60000)) if ts is None else ts}


print("── A. `pick_feed` 纯函数：三桶 + 豁免 + 上限 ──")
_r = FW.pick_feed([_m(1, 1, "刚说的"), _m(2, 40, "四十钟前的闲聊")], now_ms=NOW, window_min=10)
ok("A1 窗内进 keep、窗外非指向的进 skip", [m["id"] for m in _r["keep"]] == [1]
   and [m["id"] for m in _r["skip"]] == [2], _r)
ok("A2 记下了最早一条有多旧（给提示词/日志用）",
   abs(float(_r["oldest_age_min"]) - 40.0) < 0.5, _r["oldest_age_min"])
_r2 = FW.pick_feed([_m(1, 200, "@我 你还在吗")], now_ms=NOW, window_min=10,
                   directed=lambda m: "@我" in str(m.get("text") or ""))
ok("A3 **@ 我 / 引用我的消息不受时间窗限制**（200 分钟前的也留着）",
   [m["id"] for m in _r2["keep"]] == [1] and not _r2["skip"], _r2)
_r3 = FW.pick_feed([_m(i, 0.5, "第 %d 条" % i) for i in range(1, 8)], now_ms=NOW,
                   window_min=10, max_n=3)
ok("A4 超上限 ⇒ 只留最新 3 条，其余进 **retry（退回未读）**（不是丢掉）",
   [m["id"] for m in _r3["keep"]] == [5, 6, 7] and [m["id"] for m in _r3["retry"]] == [1, 2, 3, 4],
   (_r3["keep"], _r3["retry"]))
_r4 = FW.pick_feed([_m(1, 99, "旧 @我", ), _m(2, 0.1, "新"), _m(3, 0.2, "新2")], now_ms=NOW,
                   window_min=10, max_n=2, directed=lambda m: m["id"] == 1)
ok("A5 上限之下**优先保「指向它」的**，其余只留最新",
   [m["id"] for m in _r4["keep"]] == [1, 3], _r4["keep"])
_r5 = FW.pick_feed([_m(3, 0.1, "b"), _m(1, 0.2, "a"), _m(2, 0.3, "c")], now_ms=NOW, window_min=10)
ok("A6 三桶都保持**传入顺序**（不乱序）", [m["id"] for m in _r5["keep"]] == [3, 1, 2], _r5["keep"])
ok("A7 window_min=0 ⇒ 不限时间（全进 keep）",
   len(FW.pick_feed([_m(1, 9999, "很早")], now_ms=NOW, window_min=0)["keep"]) == 1)
ok("A8 时间戳缺失 ⇒ 当「刚收到」处理（进 keep；取不到时间就不该把它当旧闲聊丢掉）",
   [m["id"] for m in FW.pick_feed([{"id": 9, "text": "没时间戳"}], now_ms=NOW,
                                  window_min=10)["keep"]] == [9],
   FW.pick_feed([{"id": 9, "text": "没时间戳"}], now_ms=NOW, window_min=10))

print("── B. store：peek 取最新、mark_read 只标点名的 ──")
_tmp = tempfile.mkdtemp(prefix="pm-fw-")
_ST_MSG_DIR = ST.MESSAGES_DIR
ST.MESSAGES_DIR = os.path.join(_tmp, "sessions")
try:
    st = ST.ChatStore()
    for i in range(1, 6):
        st.append_incoming("group:g", "mid%d" % i, (NOW - (10 - i) * 1000), "u1", "甲", "第%d条" % i)
    _pk = st.peek_unread("group:g", 3)
    ok("B1 `peek_unread` 取的是**最新** 3 条（旧实现取最旧 ⇒ 最新的 @ 被吞）",
       [m["text"] for m in _pk] == ["第3条", "第4条", "第5条"], [m["text"] for m in _pk])
    _pk_all = st.peek_unread("group:g", 9)
    ok("B2 条数超过未读总数 ⇒ 全都给（顺序仍是时间序）",
       [m["text"] for m in _pk_all] == ["第1条", "第2条", "第3条", "第4条", "第5条"],
       [m["text"] for m in _pk_all])
    _n = st.mark_read("group:g", [_pk_all[0]["id"], _pk_all[1]["id"]])
    ok("B3 `mark_read` 只标点名的那 2 条（其余仍算未读）",
       _n == 2 and st.unread_count("group:g") == 3, "n=%d 剩=%d" % (_n, st.unread_count("group:g")))
    ok("B4 `mark_read` 传空 ⇒ 什么都不动（不误标）",
       st.mark_read("group:g", []) == 0 and st.unread_count("group:g") == 3)

    print("── C. wake 端到端：只喂「该喂的那一批」 ──")
    import persona_morph as pm                                              # noqa: E402
    from agent import thought_trace as TT                                   # noqa: E402

    _saved = (pm.get_config, pm.resolve_context_tier, TT.note, ST.MESSAGES_DIR)
    _calls = []
    try:
        ST.MESSAGES_DIR = os.path.join(_tmp, "sessions2")
        pm.get_config = lambda: {"api": {"base_url": "http://x", "model": "m"},
                                 "store": {"feed_window_min": 10, "feed_max_count": 150},
                                 "persona": {"self_nickname": "小助手"}}
        pm.resolve_context_tier = lambda *a, **k: {"tier": 3, "should_respond": True,
                                                   "reason": "测试桩", "tier_source": "stub"}
        TT.note = lambda *a, **k: None

        class _W(object):
            self_wxid = "wxid_me"
            self_nickname = "小助手"

            def group_name(self, ck):
                return "演示群"

        class _Self(object):
            def __init__(self, st_):
                self.store = st_
                self.wechat = _W()
                self._last_trigger = {}
                self.session_log = type("SL", (), {"append": staticmethod(lambda *a, **k: None)})()

            def _maybe_human_behaviors(self, *a, **k):
                return None

            def run_agent(self, chat_key, trigger, tier_result, session):
                _calls.append([m.get("text") for m in trigger])

        # ⚠️ C 段用**真时钟**：wake 内部取 `time.time()`，若拿固定的未来时间戳当"现在"，
        #    消息会显得"在将来" ⇒ 一律被当新鲜（第一次跑就是这么红的）。
        REAL = int(time.time() * 1000)

        # C1：1 条陈旧闲聊 + 1 条新鲜 ⇒ 只喂新鲜那条；陈旧的被标已读
        _s1 = ST.ChatStore()
        _s1.append_incoming("group:g", "old", REAL - 40 * 60000, "u1", "甲", "四十钟前的闲聊")
        _s1.append_incoming("group:g", "new", REAL - 30 * 1000, "u1", "甲", "刚说的")
        pm.Orchestrator.wake(_Self(_s1), "group:g")
        ok("C1 陈旧闲聊**不喂模型**，新鲜的那条喂了", _calls and _calls[-1] == ["刚说的"], _calls)
        ok("C2 陈旧那条被**标已读**（本次故意不回，不是忘了）", _s1.unread_count("group:g") == 0,
           _s1.unread_count("group:g"))

        # C3：陈旧但**被 @** ⇒ 仍然喂（时间窗豁免）
        _calls[:] = []
        _s2 = ST.ChatStore()
        _s2.append_incoming("group:g", "at", REAL - 90 * 60000, "u1", "甲", "@小助手 在吗")
        pm.Orchestrator.wake(_Self(_s2), "group:g")
        ok("C3 **被 @ 的再旧也喂**（否则就是新的「它不理我」）",
           _calls and _calls[-1] == ["@小助手 在吗"], _calls)

        # C4：全是旧闲聊 ⇒ **压根不叫模型**
        _calls[:] = []
        _s3 = ST.ChatStore()
        _s3.append_incoming("group:g", "o1", REAL - 200 * 60000, "u1", "甲", "很久以前")
        pm.Orchestrator.wake(_Self(_s3), "group:g")
        ok("C4 整批都是旧闲聊 ⇒ 不调模型、全标已读", _calls == [] and _s3.unread_count("group:g") == 0,
           (_calls, _s3.unread_count("group:g")))

        # C5：超上限 ⇒ 喂最新的，剩下的**退回未读**
        _calls[:] = []
        pm.get_config = lambda: {"api": {"base_url": "http://x", "model": "m"},
                                 "store": {"feed_window_min": 10, "feed_max_count": 2},
                                 "persona": {"self_nickname": "小助手"}}
        _s4 = ST.ChatStore()
        for i in range(1, 6):
            _s4.append_incoming("group:g", "n%d" % i, REAL - (10 - i) * 1000, "u1", "甲", "第%d条" % i)
        pm.Orchestrator.wake(_Self(_s4), "group:g")
        ok("C5 上限 2 ⇒ 只喂最新 2 条", _calls and _calls[-1] == ["第4条", "第5条"], _calls)
        ok("C6 被挤出来的 3 条**仍在未读**（退回下一轮，不丢）", _s4.unread_count("group:g") == 3,
           _s4.unread_count("group:g"))
    finally:
        (pm.get_config, pm.resolve_context_tier, TT.note, ST.MESSAGES_DIR) = _saved

    print("── D. 源码形态兜底：wake 里不再「整批标读」 ──")
    _wake_src = open(os.path.join(HERE, "persona_morph.py"), encoding="utf-8").read()
    _wake = _wake_src.split("def wake(self, chat_key: str):")[1].split("\n    def ", 2)[0]
    ok("D1 wake 里调了 `feed_window.pick_feed`", "pick_feed(" in _wake)
    ok("D2 wake 里用 `mark_read`（点名标读），不再用 `mark_all_read`/`drain_unread` 整批标读",
       "mark_read(" in _wake and "mark_all_read(" not in _wake and "drain_unread(" not in _wake)
    ok("D3 喂给模型的就是切出来的那一批（`trigger = list(pending)`）", "trigger = list(pending)" in _wake)
finally:
    ST.MESSAGES_DIR = _ST_MSG_DIR
    import shutil                                                            # noqa: E402
    shutil.rmtree(_tmp, ignore_errors=True)

print("\n==== 未读批切分判据：%d 通过 / %d 失败 ====" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
