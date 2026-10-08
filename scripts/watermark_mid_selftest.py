# -*- coding: utf-8 -*-
"""B7 根治判据：同 sort_seq 多条不许漏 / 同一 mid 不许重（`scripts/watermark_mid_selftest.py`）。

**为什么要这份判据**
    现有 `watermark_selftest.py`（73/0）覆盖了"落盘/账号维/flush 失败告警"，
    但**没有覆盖** B7 的两个现场：
      · 同一会话内两条消息 `sort_seq` 相同 ⇒ 旧判重（纯位置 `seq <= 水位`）
        **会把同 seq 的其余条一次性跳过**（永久漏读）；
      · 起点语义是 `>` 还是 `>=`（`latest` 还是 `latest-1`）。
    ⇒ 本判据用**纯内存构造**（不碰真机、不碰产品 config、不读真库）把这两条钉住。

跑法：`py -3 scripts/watermark_mid_selftest.py`
口径：中文文案用「」（半角双引号会与 Python 字符串定界符打架）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import listener_watermark as LW

PASS = [0]
FAIL = [0]


def ok(title, cond, detail=""):
    if cond:
        PASS[0] += 1
        print("  PASS %s" % title)
    else:
        FAIL[0] += 1
        print("  FAIL %s%s" % (title, ("  [%s]" % detail) if detail else ""))


def _mk(tmpname, chat="group:g1"):
    """建一个落盘到临时文件的水位表（不碰真 data/）。"""
    import tempfile
    p = os.path.join(tempfile.gettempdir(), tmpname)
    try:
        os.remove(p)
    except OSError:
        pass  # 清理型：静默合法
    return LW.Watermark(p), p


def _handler_recorder():
    seen = []

    def h(it):
        seen.append((it.get("sort_seq"), it.get("mid")))
        return True
    return h, seen


print("== A. 同 sort_seq 多条：一条都不许漏（B7 漏读侧）==")
wm, p = _mk("wm_mid_a.json")
h, seen = _handler_recorder()
wm.set("group:g1", 99)
# 同一条 seq=100 下有三条（不同 mid）—— 现场来源：同秒入库 / 并发 / 微信自己写重
items = [{"sort_seq": 100, "mid": 5, "text": "A"},
         {"sort_seq": 100, "mid": 6, "text": "B"},
         {"sort_seq": 100, "mid": 7, "text": "C"}]
st = LW.process_batch("group:g1", items, h, wm, max_retry=1, retry_sleep=0)
ok("A1 同 seq 三条**全部**被交给 handler（不是只处理第一条）",
   len(seen) == 3, "实收 %d 条：%s" % (len(seen), seen))
ok("A2 水位推进到 100（三条都算处理过）", wm.get("group:g1") == 100,
   "水位=%s" % wm.get("group:g1"))

print("\n== B. 同一 mid 第二次进来：不许重复处理（B7 重复侧）==")
wm, p = _mk("wm_mid_b.json")
h0, seen0 = _handler_recorder()
# ⛔ 必须**先真的处理一遍**（这样 mid=5 才进 seen）；不能只 `wm.set(水位)` ——
#   水位是"位置账"，不会自动把 mid 记进已见环（那是 process_batch 的职责）。
wm.set("group:g1", 99)
LW.process_batch("group:g1", [{"sort_seq": 100, "mid": 5, "text": "首次"}],
                 h0, wm, max_retry=1, retry_sleep=0)
ok("B0 前情：首次处理确实进了 handler", len(seen0) == 1, "实收：%s" % seen0)
h, seen = _handler_recorder()
items = [{"sort_seq": 100, "mid": 5, "text": "重放"}]
st = LW.process_batch("group:g1", items, h, wm, max_retry=1, retry_sleep=0)
ok("B1 已被 seen 记下的 mid ⇒ **不再交给 handler**",
   len(seen) == 0, "实收：%s" % seen)
ok("B2 计入 skipped（有账，不是静默丢）", st.get("skipped", 0) >= 1, "stats=%s" % st)

print("\n== C. `mid=None` 的行要有合成 id（否则同 seq 互相顶掉）==")
wm, p = _mk("wm_mid_c.json")
h, seen = _handler_recorder()
wm.set("group:g1", 99)
items = [{"sort_seq": 100, "mid": None, "text": "丢行1"},
         {"sort_seq": 100, "mid": None, "text": "丢行2"}]
LW.process_batch("group:g1", items, h, wm, max_retry=1, retry_sleep=0)
ok("C1 两条无 mid 的行都要进 handler（不许靠位置互相顶掉）",
   len(seen) == 2, "实收 %d 条" % len(seen))

print("\n== D. 失败/毒消息仍要越过（别把现有能力改坏）==")
wm, p = _mk("wm_mid_d.json")
calls = {"n": 0}


def bad(it):
    calls["n"] += 1
    raise RuntimeError("毒消息")
wm.set("group:g1", 99)
st = LW.process_batch("group:g1", [{"sort_seq": 100, "mid": 5}], bad, wm,
                      max_retry=2, retry_sleep=0.01)
ok("D1 毒消息重试后**仍被越过**（水位推到 100）", wm.get("group:g1") == 100,
   "水位=%s" % wm.get("group:g1"))
ok("D2 返回 failed=1（有账）", st.get("failed") == 1, "stats=%s" % st)

print("\n== E. 起点语义：多扫一条不产生重复（绝不漏读的来源）==")
# 模拟主循环：先**真的处理过** mid=5，下一轮从 >= 100 扫 ⇒ 又扫到 mid=5 那条
wm, p = _mk("wm_mid_e.json")
h0, seen0 = _handler_recorder()
wm.set("group:g1", 99)
LW.process_batch("group:g1", [{"sort_seq": 100, "mid": 5}], h0, wm,
                 max_retry=1, retry_sleep=0)
ok("E0 前情：首次处理过 mid=5", len(seen0) == 1, "实收：%s" % seen0)
h, seen = _handler_recorder()
LW.process_batch("group:g1", [{"sort_seq": 100, "mid": 5}], h, wm,
                 max_retry=1, retry_sleep=0)
ok("E1 起点放宽后，扫回的同一条被**跳过**（不重复处理）",
   len(seen) == 0, "实收：%s" % seen)
# 但同 seq 的另一条（mid=6，从没处理过）**必须**被处理 —— 这就是"绝不漏读"
h2, seen2 = _handler_recorder()
LW.process_batch("group:g1", [{"sort_seq": 100, "mid": 6}], h2, wm,
                 max_retry=1, retry_sleep=0)
ok("E2 同 seq 的**新** mid ⇒ 必须被处理（绝不漏读）",
   len(seen2) == 1, "实收：%s" % seen2)

print("\n== F. seen 落盘与账号命名空间 ==")
wm, p = _mk("wm_mid_f.json")
wm.set("group:g1", 100)
LW.process_batch("group:g1", [{"sort_seq": 100, "mid": 5}], _handler_recorder()[0],
                 wm, max_retry=1, retry_sleep=0)
wm.flush()
wm2 = LW.Watermark(p)
ok("F1 换实例读回：水位还在（100）", wm2.get("group:g1") == 100,
   "读回=%s" % wm2.get("group:g1"))
ok("F2 换实例读回：seen 里仍记得 mid=5（否则重启即重读）",
   5 in (wm2.seen_for("group:g1") if hasattr(wm2, "seen_for") else []),
   "seen=%s" % (wm2.seen_for("group:g1") if hasattr(wm2, "seen_for") else "（无 seen_for）"))

print("\n== G. 换库/清记录：seen 必须整表清空（防误跳）==")
if hasattr(LW.Watermark, "clear_seen"):
    wm3, p3 = _mk("wm_mid_g.json")
    wm3.set("group:g1", 100)
    LW.process_batch("group:g1", [{"sort_seq": 100, "mid": 5}], _handler_recorder()[0],
                     wm3, max_retry=1, retry_sleep=0)
    wm3.clear_seen("group:g1")
    h4, seen4 = _handler_recorder()
    # 换库后行号重排：新库的第 5 行是**别人的新消息**，不该因为"见过 mid=5"被跳过
    LW.process_batch("group:g1", [{"sort_seq": 100, "mid": 5, "text": "新库的另一条"}],
                     h4, wm3, max_retry=1, retry_sleep=0)
    ok("G1 清空 seen 后，重排的行号**不再被误跳**（新消息能进来）",
       len(seen4) == 1, "实收：%s" % seen4)
else:
    ok("G1 清空 seen 后，重排的行号不再被误跳（新消息能进来）", False,
       "未实现 clear_seen")

print("\n== B7 根治判据：%d 通过 / %d 失败 ==" % (PASS[0], FAIL[0]))
sys.exit(1 if FAIL[0] else 0)
