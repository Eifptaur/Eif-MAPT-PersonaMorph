# -*- coding: utf-8 -*-
"""「月账单」判据（第 151 项）—— 后端按月归并 ＋ **两个前端都显示**（web ＋ Qt）。

守四件事：
  A **归并**：同一自然月里的多条账（日账/周账）**要相加**；**没花费的月份给 0、不是缺项**；
    含当月；降序；**跨年边界**（1 月往前推要落到去年 12 月）。
  B **接线**：`snapshot()` 带 `months`；web 与 Qt **两边都有同一格**（只改一边 ⇒ 两边数不一样）。
  C **不许显示过期数字**：web 在"选中某一天"那个视图里必须把这一格置 `—`，
    而不是留着上一次快照的月度值（那比空白更糟）。
  D 反向锚：把月数参数调成 1 ⇒ 只剩当月（证明这条不是恒真）。

用法：runtime\\python\\python.exe scripts/stats_month_selftest.py
"""
import io
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
import _iso14  # noqa: E402
_iso14.all_()

from agent.stats import UsageStats  # noqa: E402

PASS = FAIL = 0
TD = os.path.join(ROOT, "..", "_scratch", "_stats_tmp")


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


def _mk(history, period=None, period_start=None):
    s = UsageStats(TD)
    s.data["history"] = list(history)
    if period is not None:
        s.data["period"] = dict(period)
    if period_start is not None:
        s.data["period_start"] = period_start
    return s


NOW = datetime(2026, 10, 15, 12, 0, 0).timestamp()

print("── A. 按月归并 ──")
_hist = [
    {"start": "2026-09-01T00:00:00", "cost": 1.5, "tokens": 100, "calls": 2, "sessions": 1, "sent": 3},
    {"start": "2026-09-15T00:00:00", "cost": 2.5, "tokens": 200, "calls": 3, "sessions": 2, "sent": 4},  # 同月另一条
    {"start": "2026-07-01T00:00:00", "cost": 9.0, "tokens": 900, "calls": 9, "sessions": 9, "sent": 9},
]
s = _mk(_hist, period={"cost": 0.25, "tokens": 10, "calls": 1, "sessions": 1, "sent": 1},
        period_start="2026-10-01T00:00:00")
_ms = s.by_month(6, now=NOW)
ok("A0 分母守卫：`months=6` ⇒ 正好 6 条", len(_ms) == 6, str(len(_ms)))
_d = {m["month"]: m for m in _ms}
ok("A1★ **同一自然月的多条账要相加**（9 月 = 1.5 + 2.5 = 4.0）",
   abs(float(_d["2026-09"]["cost"]) - 4.0) < 1e-9 and int(_d["2026-09"]["tokens"]) == 300,
   "cost=%s tokens=%s" % (_d["2026-09"]["cost"], _d["2026-09"]["tokens"]))
ok("A1b★ **没花费的月份给 0，不是缺项**（月份齐全、2016-08 也在表里且为 0）",
   "2026-08" in _d and float(_d["2026-08"]["cost"]) == 0.0 and int(_d["2026-08"]["tokens"]) == 0,
   str(sorted(_d.keys())))
ok("A2 当月在里面且 `current=True`（其余为 False）",
   _d["2026-10"]["current"] is True and _d["2026-09"]["current"] is False, "")
ok("A3★ 当期（还没归档的那笔）**也并进它所属的月**（10 月 cost = 0.25）",
   abs(float(_d["2026-10"]["cost"]) - 0.25) < 1e-9, str(_d["2026-10"]["cost"]))
ok("A4 降序：第一条就是当月", _ms[0]["month"] == "2026-10", _ms[0]["month"])
ok("A5★ 跨年边界：把 `now` 挪到 1 月 ⇒ 上一格必须是**去年 12 月**",
   [m["month"] for m in _mk(_hist).by_month(3, now=datetime(2026, 1, 15, 12).timestamp())]
   == ["2026-01", "2025-12", "2025-11"],
   str([m["month"] for m in _mk(_hist).by_month(3, now=datetime(2026, 1, 15, 12).timestamp())]))
ok("A6 月份有 `label`（人话）", _d["2026-10"]["label"] == "2026 年 10 月", _d["2026-10"]["label"])

print("\n── D. 反向锚 ──")
ok("D1★ `months=1` ⇒ 只剩当月那一条（证明月数参数真的生效，不是恒返回固定值）",
   [m["month"] for m in s.by_month(1, now=NOW)] == ["2026-10"],
   str([m["month"] for m in s.by_month(1, now=NOW)]))
ok("D2★ 清空账本 ⇒ 六格**全是 0**（不是空表、也不是报错）",
   all(float(m["cost"]) == 0.0 for m in _mk([]).by_month(6, now=NOW)), "")

print("\n── B/C. 接线：后端带出来 ＋ 两个前端都显示 ──")
_st = io.open(os.path.join(ROOT, "agent", "stats.py"), encoding="utf-8").read()
ok("B1 `snapshot()` 里带 `months`（两个前端都从这一处读）",
   _sm.has(_st, '"months": self.by_month(6)'), "")
_web = io.open(os.path.join(ROOT, "assets", "console", "index.html"), encoding="utf-8").read()
ok("B2★ web 有这一格（`st-mcost` ＋ 中文标题「本月花费」）",
   _sm.has(_web, 'id="st-mcost"') and "本月花费" in _web, "")
ok("B3★ web 真的填了它（从 `u.months[0]`）", _sm.has(_web, "fmt((u.months||[])[0])"), "")
ok("B4★★ **两个实现面同批改**：Qt 那边也有同一格（只改一边 ⇒ 两边数不一样）",
   _sm.has(io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read(),
           '("mcost", "本月花费"'), "")
_qt = io.open(os.path.join(ROOT, "ui_qt", "panels_custom.py"), encoding="utf-8").read()
ok("B5 Qt 真的填了它（取 `months` 第一项）", _sm.has(_qt, 'ucells["mcost"].setText'), "")
ok("C1★ **不许显示过期数字**：选中某一天那个视图里必须把这一格置 `—`"
   "（留着上一次的月度值 ⇒ 显示的数与当前视图不一致，比空白更糟）",
   _sm.has(_web, "$('st-mcost').textContent='—'"), "")

print("\n== 月账单判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
