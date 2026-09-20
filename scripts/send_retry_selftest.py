#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：**发送重试队列**（把"身份判不了 ⇒ 这次不发"变成"晚点再发一次"）。

跑法： runtime\\python\\python.exe scripts\\send_retry_selftest.py   退出码 0=全过 / 1=有失败

为什么（用户 2026-09-21 原话：「现在还是会影响用户体验的，**宁可不发也不发错，但用户本身是想发的**」
＋ 业界调研：TOCTOU 的正解是"让动作发生在已绑定的句柄上"，判不了的那条应进**失败可见的重试队列**
而不是直接丢）：

本判据守六件：
  ① 只收"可重试"的失败（认发送层打的 `【可重试】` 前缀，**不猜关键词**）＋ 反例锚；
  ② 入队去重 / 上限 / 落盘（**绝不写产品 data/**，判据把自己的路径指到临时文件）；
  ③ 退避与到点判定（`due` 不到点不给、到点才给）；
  ④ 结果处理四态：成功出队 · 仍可重试 ⇒ 再排 · 不可重试 ⇒ 出队 · 次数/时长用尽 ⇒ 放弃；
  ⑤ `tick()` 真调发送函数并把结果落回队列（成功/失败两条都验）；
  ⑥ 坏文件 / 落盘失败**不许抛**（重试只是补救，不能反过来把主流程搞崩）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import send_retry as SR                                             # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))


def main():
    # 夹具统一时间基准：`now` 之间只差一点点 —— 别用 2000/6000 这种假纪元
    # （年龄会算出上万秒，直接撞上 MAX_AGE_S ⇒ 测的就变成「过期」而不是被测的那条规则）。
    _T = 1_700_000_000.0
    _tmp = tempfile.mkdtemp(prefix="pm-sr-")
    _orig_path, _orig_cache = SR.PATH, SR._cache
    SR.PATH = os.path.join(_tmp, "send_retry.json")
    SR._cache = None
    try:
        print("── A. 只收「可重试」的失败（认前缀，不猜关键词）──")
        ok("A1 发送层打的 `【可重试】` 前缀 ⇒ 收", SR.retryable("【可重试】会话头不匹配，拒绝投递（防发错会话）") is True)
        ok("A2 普通失败（内容为空 / 图源挂了）⇒ **不收**", SR.retryable("消息内容为空") is False
           and SR.retryable("图片下载失败：TimeoutError") is False)
        # ⛔ 2026-09-21 修（第六轮 **V-R6-31**）：原来第一段是 `"会话" in "会话里没有…"`（两个字面量
        #   ⇒ 恒真）。改成**显式建模老做法**，这样它验的是"新老口径真的不同"，而不是一句常真话。
        _old_guess = lambda _w: "会话" in _w            # noqa: E731  老做法：见「会话」就收（已废弃）
        ok("A3 反例锚：老做法（按关键词猜）会把**不可重试**的也收进来",
           _old_guess("会话里没有这条消息（内容为空）") is True
           and SR.retryable("会话里没有这条消息（内容为空）") is False)
        ok("A4 前缀在**发送层源码**里真的有（`wechat.py` 至少 5 处）",
           open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read().count("【可重试】") >= 5,
           open(os.path.join(ROOT, "agent", "wechat.py"), encoding="utf-8").read().count("【可重试】"))

        print("\n── B. 入队 / 去重 / 上限 / 落盘 ──")
        _r1 = SR.enqueue("group:A", "第一句", "【可重试】当前开着的很可能不是目标会话", now=_T + 0)
        ok("B1 入队成功且写了盘", _r1.get("ok") is True and os.path.exists(SR.PATH), _r1)
        _r2 = SR.enqueue("group:A", "第一句", "【可重试】又试了一次", now=_T + 1)
        ok("B2 同会话同文本 ⇒ **去重**（只更新原因，不排第二条）",
           _r2.get("deduped") is True and SR.stats()["pending"] == 1, SR.stats())
        SR.enqueue("group:A", "第二句", "【可重试】原因", now=_T + 2)
        ok("B3 同会话**不同**文本 ⇒ 各排一条", SR.stats()["pending"] == 2, SR.stats())
        for i in range(SR.MAX_ITEMS + 5):
            SR.enqueue("group:Z%d" % i, "t%d" % i, "【可重试】原因", now=_T + 100.0 + i)
        ok("B4 队列有上限（不会无限长）", SR.stats()["pending"] <= SR.MAX_ITEMS, SR.stats()["pending"])
        SR.clear("判据重置")
        ok("B5 `clear()` 真清空", SR.stats()["pending"] == 0)

        print("\n── C. 退避与到点 ──")
        SR.enqueue("group:A", "要发的话", "【可重试】现场没认准", now=_T + 10)
        ok("C1 刚入队 ⇒ **不到点**（不立刻重试）", len(SR.due(now=_T + 11)) == 0)
        ok("C2 过了退避（15s）⇒ 到点", len(SR.due(now=_T + 10 + SR.BACKOFF[0] + 0.1)) == 1)

        print("\n── D. 结果处理四态 ──")
        _it = SR.due(now=_T + 30)[0] if SR.due(now=_T + 30) else SR._load()[0]
        _id = str(_it["id"])
        ok("D1 仍可重试的失败 ⇒ 留在队列、次数 +1、退避变长",
           SR.resolve(_id, False, "【可重试】还是没认准", now=_T + 30) == "again"
           and SR._load()[0]["tries"] == 1 and SR._load()[0]["next_at"] > 3000.0)
        ok("D2 不可重试的失败 ⇒ **出队**（现场变了，重试没意义）",
           SR.resolve(_id, False, "消息内容为空", now=_T + 31) == "dropped" and SR.stats()["pending"] == 0)
        SR.enqueue("group:B", "成功的", "【可重试】原因", now=_T + 40)
        _b = SR._load()[0]
        ok("D3 成功 ⇒ 出队", SR.resolve(str(_b["id"]), True, "", now=_T + 41) == "done"
           and SR.stats()["pending"] == 0)
        SR.enqueue("group:C", "总不成功", "【可重试】原因", now=_T + 50)
        _c = SR._load()[0]
        _acts = [SR.resolve(str(_c["id"]), False, "【可重试】还是不认准", now=_T + 51 + i)
                 for i in range(SR.MAX_TRIES)]
        ok("D4 次数用尽 ⇒ 放弃（`expired`）且出队", _acts and _acts[-1] == "expired" and SR.stats()["pending"] == 0,
           _acts)
        SR.enqueue("group:D", "太久了", "【可重试】原因", now=_T + 60)
        _d = SR._load()[0]
        ok("D5 超过最长时限 ⇒ 放弃（不许挂到天荒地老）",
           SR.resolve(str(_d["id"]), False, "【可重试】原因", now=_T + 60 + SR.MAX_AGE_S + 1) == "expired")

        print("\n── E. tick() 真调发送函数 ──")
        SR.clear("E 段重置")
        SR.enqueue("group:OK", "这条会成功", "【可重试】原因", now=_T + 70)
        SR.enqueue("group:NO", "这条还会被拦", "【可重试】原因", now=_T + 70)
        _calls = []

        def _send(cid, txt):
            _calls.append((cid, txt))
            return (True, "投递发送成功") if cid == "OK" else (False, "【可重试】还是没认准")

        _t = SR.tick(_send, now=_T + 70 + SR.BACKOFF[0] + 1, limit=5)
        ok("E1 tick 真调了发送函数（两条都试了）", len(_calls) == 2, _calls)
        ok("E2 成功的出队、可重试的留着 ⇒ 队列剩 1 条", SR.stats()["pending"] == 1, SR.stats())
        ok("E3 计数如实（tried=2 / done=1 / again=1）", _t["tried"] == 2 and _t["done"] == 1 and _t["again"] == 1, _t)

        print("\n── F. 坏文件 / 落盘失败都不许抛 ──")
        with io.open(SR.PATH, "w", encoding="utf-8") as f:
            f.write("{这不是 JSON")
        SR._cache = None
        ok("F1 队列文件坏了 ⇒ 按空队列起（不抛）＋**坏文件改名留证**（`.bad.<ts>`，内容仍可查）",
           SR.stats()["pending"] == 0
           and any(".bad." in x for x in os.listdir(os.path.dirname(SR.PATH))),
           str([x for x in os.listdir(os.path.dirname(SR.PATH)) if ".bad." in x])[:80])
        SR._cache = None
        _bad = SR.enqueue("group:E", "写不进去", "【可重试】原因", now=_T + 80)
        ok("F2 路径不可写时也不抛（`ok=True` + `saveError` 带原因）",
           _bad.get("ok") is True and isinstance(_bad.get("saveError"), str), _bad)
        # ⛔ 2026-09-21 加（第六轮 **V-R6-16**）：三条"静默面"各一条断言
        SR.clear("判据重置")
        SR._cache = None
        for _i in range(SR.MAX_ITEMS + 3):
            SR.enqueue("group:O%d" % _i, "o%d" % _i, "【可重试】原因", now=_T + 200.0 + _i)
        ok("F3 队列满 ⇒ 丢最旧，但**计入 stats**（不再静默）",
           SR.stats().get("overflowDropped", 0) >= 1, str(SR.stats().get("overflowDropped")))
        SR.clear("判据重置")
        SR._cache = None
        SR.enqueue("group:P", "反复入队", "【可重试】原因", now=_T + 300)
        SR.enqueue("group:P", "反复入队", "【可重试】原因", now=_T + 310)     # 反复入队不得把到点推走
        ok("F4 反复入队**不重置到点时间**（防饥饿）",
           len(SR.due(now=_T + 300 + SR.BACKOFF[0] + 0.2)) == 1,
           str(len(SR.due(now=_T + 300 + SR.BACKOFF[0] + 0.2))))
        # ⛔ 2026-09-21 加（第六轮 **V-R6-4/5**）：年龄闸提前 + 停机不是失败
        SR.clear("判据重置")
        SR._cache = None
        _e = SR.enqueue("group:Q", "隔夜那条", "【可重试】原因", now=_T + 400)
        _acts = []
        _calls = {"n": 0}

        def _fake_send(_cid, _txt):
            _calls["n"] += 1
            return True, "ok"

        _r_old = SR.tick(_fake_send, now=_T + 400 + SR.MAX_AGE_S + 5, limit=3)
        ok("F5 超龄条目 ⇒ **先过期丢弃、不许真发**（年龄闸在 `if ok:` 之前）",
           _calls["n"] == 0 and _r_old.get("expired") == 1, str(_r_old))
        SR.clear("判据重置")
        SR._cache = None
        SR.enqueue("group:R", "暂停时那条", "【可重试】原因", now=_T + 500)
        _r_halt = SR.tick(_fake_send, now=_T + 500 + SR.BACKOFF[0] + 1, limit=3,
                          halt_fn=lambda: "机器人已暂停 ⇒ 这条不发")
        ok("F6 暂停/停止时 tick **一条都不试**（条目留着，不被 dropped 销毁）",
           _r_halt.get("tried") == 0 and _r_halt.get("held") == 1 and SR.stats()["pending"] == 1,
           str(_r_halt) + " pending=%d" % SR.stats()["pending"])
        _r_hold = SR.resolve(SR._load()[0]["id"], False, "机器人已停止 ⇒ 这条不发", now=_T + 600)
        ok("F7 停机话术走 resolve 也只**保留**（held），不当成「不可重试」丢掉",
           _r_hold == "held" and SR.stats()["pending"] == 1, "%s pending=%d" % (_r_hold, SR.stats()["pending"]))

        # ── G. **接线**（跨模块）：工具层的失败真的会入队 ──────────────────────────────
        # ⛔ 2026-09-21 加（第六轮 **V-R6-9**，这是本轮最贵的漏检）：原来的判据只测 `send_retry` 模块自己，
        #   **不 import `tools.py`** ⇒ 接线断掉（`str.get` on str ⇒ 异常被吞成 DEBUG）时判据照样全绿，
        #   而生产里"判不了就晚点补发"从来没生效过。这里用假 sender 造一条失败，断言真入了队。
        SR.clear("判据重置")
        SR._cache = None
        from agent import tools as T

        class _FakeSender:
            def __init__(self, err):
                self.err = err
                self.seen = []

            def send_text_batch(self, chat_key, messages, **kw):
                self.seen.append(list(messages))
                return {"sent": [], "failed": [{"index": 0, "text": str(messages[0])[:20],
                                                "error": self.err, "src": str(messages[0])}]}

        class _FakeStore:
            def find_by_mid(self, *a, **k):
                return None

        _ctx = {"chat_key": "group:filehelper", "sender": _FakeSender("【可重试】现场没认准"),
                "store": _FakeStore(), "session": {"sent": []}}
        _res = T._exec_send_message(_ctx, {"messages": ["整条原文：带 markdown [链接](http://x) 的那条"]})
        _q = SR._load()
        ok("G1 工具层收到「可重试」失败 ⇒ **真入队**（接线通）",
           SR.stats()["pending"] == 1, str(SR.stats())[:120])
        ok("G2 入队的是**原文**（不是前 20 字盲找、也不是已发成功那条）",
           bool(_q) and str(_q[0].get("text") or "").startswith("整条原文"), str(_q[:1])[:120])
        SR.clear("判据重置")
        SR._cache = None
        _ctx2 = {"chat_key": "group:filehelper", "sender": _FakeSender("内容为空，发送失败"),
                 "store": _FakeStore(), "session": {"sent": []}}
        T._exec_send_message(_ctx2, {"messages": ["这条不该入队"]})
        ok("G3 非「可重试」失败 ⇒ 不入队（fail-closed 但不乱补）", SR.stats()["pending"] == 0)
        SR.clear("判据重置")
        SR._cache = None
    finally:
        SR.PATH, SR._cache = _orig_path, _orig_cache
        import shutil
        shutil.rmtree(_tmp, ignore_errors=True)

    print("\n== 汇总：%d 通过 / %d 失败 ==" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
