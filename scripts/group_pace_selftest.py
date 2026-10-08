# -*- coding: utf-8 -*-
"""「群活跃度自适应」判据（第 134 项）—— 不碰网络、不碰窗口、不写产品 `data/`。

守三件事：
  A **判定**：冷/正常/热 分得准；**样本不够 ⇒ 按正常档**（⛔ 不许猜成「冷」—— 那会把消息憋住）。
  B **缩放**：默认关 ⇒ **恒 1.0（零回归）**；开了才生效，且**上封顶、下有底（至少 1 条）**；
    `base_cap<=0`（＝不限）**原样返回**（别把「不限」变成「最多 1 条」）。
  C **接线**：`risk.py` 的每单位时间上限**真的过了这一层**，且缩放排在比较**之前**；
    缩放出异常时按原上限走（不因缩放失败把消息憋住）。

用法：runtime\\python\\python.exe scripts/group_pace_selftest.py
"""
import io
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _srcmatch as _sm  # noqa: E402
from agent import group_pace as GP  # noqa: E402

PASS = FAIL = 0
NOW = time.time()


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


class FakeStore:
    """只提供 `recent()` 的最小桩（判据不去碰真消息库）。"""

    def __init__(self, ts_list):
        self._m = [{"ts": int(t * 1000)} for t in ts_list]   # ⛔ 存**毫秒**，与真实 store 一致

    def recent(self, chat_key, limit=600, include_self=True, **kw):
        return self._m[-int(limit):]


def _cfg(**kw):
    d = dict(GP.DEFAULTS)
    d.update(kw)
    return {"group_adaptive": d}


DAY = 86400.0
# 长期基线：过去 7 天**每天 200 条**（≈0.14 条/分 —— 像真群；基线太稀会让"任何动静"都判成热）
# ⛔ 总量要**小于消息库上限**（判据桩只有 600 条窗口）——否则最老那几天读不到，
#   基线会被截断、ratio 失真（实测踩过：正常被误判成热）。
_BASE = [NOW - DAY * d - h * 1400 for d in range(1, 8) for h in range(60)]

print("── A. 冷 / 正常 / 热 ──")
_cold = FakeStore(_BASE)                                   # 近 30 分钟一条都没有
ok("A1★ 近期几乎没人说话（历史有）⇒ **冷**",
   GP.activity("g", store=_cold, cfg=_cfg(), now=NOW)["level"] == "cold",
   str(GP.activity("g", store=_cold, cfg=_cfg(), now=NOW)["level"]))
_hot = FakeStore(_BASE + [NOW - i * 20 for i in range(120)])   # 近 30 分钟 120 条
_h = GP.activity("g", store=_hot, cfg=_cfg(), now=NOW)
ok("A2★ 近期消息刷得快 ⇒ **热**", _h["level"] == "hot", "%s ratio=%s" % (_h["level"], _h["ratio"]))
_norm = FakeStore(_BASE + [NOW - 300])
ok("A3 与长期水平相当 ⇒ **正常**",
   GP.activity("g", store=_norm, cfg=_cfg(), now=NOW)["level"] == "normal",
   str(GP.activity("g", store=_norm, cfg=_cfg(), now=NOW)["level"]))
_thin = FakeStore([NOW - 30])     # 历史不足 1 条 ⇒ 样本不够
_a = GP.activity("g", store=_thin, cfg=_cfg(), now=NOW)
ok("A4★ **样本不够 ⇒ 按正常档**，并把原因写出来（⛔ 不许猜成「冷」—— 那会把消息憋住）",
   _a["level"] == "normal" and "样本不够" in _a["note"], "%s / %s" % (_a["level"], _a["note"][:24]))
_empty = FakeStore([])
ok("A5 读不到任何消息 ⇒ 正常档（不抛、不猜）",
   GP.activity("g", store=_empty, cfg=_cfg(), now=NOW)["level"] == "normal", "")

print("\n── B. 缩放：默认关＝零回归；开了才动 ──")
ok("B1★★ **默认关 ⇒ 恒 1.0**（`adaptive_cap(100)` 必须还是 100 —— 开了才变）",
   GP.cap_scale("g", store=_cold, cfg=_cfg()) == 1.0
   and GP.adaptive_cap("g", 100, store=_cold, cfg=_cfg()) == 100, "")
ok("B1b 反向锚：把 `enabled` 打开后**就不再是 1.0**（证明 B1 不是恒真）",
   GP.cap_scale("g", store=_cold, cfg=_cfg(enabled=True)) != 1.0, "")
ok("B2 冷群 ⇒ 降到 60%（100 ⇒ 60）",
   GP.adaptive_cap("g", 100, store=_cold, cfg=_cfg(enabled=True)) == 60,
   str(GP.adaptive_cap("g", 100, store=_cold, cfg=_cfg(enabled=True))))
ok("B3 热群 ⇒ 提到 140%（100 ⇒ 140）",
   GP.adaptive_cap("g", 100, store=_hot, cfg=_cfg(enabled=True)) == 140,
   str(GP.adaptive_cap("g", 100, store=_hot, cfg=_cfg(enabled=True))))
ok("B4★ **上封顶**：就算把 `hot_scale` 配成 9，也最多 `max_scale`（1.5 ⇒ 150）",
   GP.adaptive_cap("g", 100, store=_hot, cfg=_cfg(enabled=True, hot_scale=9)) == 150,
   str(GP.adaptive_cap("g", 100, store=_hot, cfg=_cfg(enabled=True, hot_scale=9))))
ok("B5★ **下有底**：冷群 + 上限本来就是 1 ⇒ 仍是 **1**（⛔ 不许缩成 0＝把嘴封死）",
   GP.adaptive_cap("g", 1, store=_cold, cfg=_cfg(enabled=True)) == 1,
   str(GP.adaptive_cap("g", 1, store=_cold, cfg=_cfg(enabled=True))))
ok("B6 `base_cap<=0`（＝不限）⇒ **原样返回 0**（别把「不限」变成「最多 1 条」）",
   GP.adaptive_cap("g", 0, store=_cold, cfg=_cfg(enabled=True)) == 0
   and GP.adaptive_cap("g", -3, store=_cold, cfg=_cfg(enabled=True)) == 0, "")

print("\n── C. 接线：风闸真的过了这一层 ──")
_risk = io.open(os.path.join(ROOT, "agent", "risk.py"), encoding="utf-8").read()
ok("C1★ `risk.py` 的每单位时间上限处**真的调了** `group_pace.adaptive_cap`",
   _sm.has(_risk, "group_pace as _gp") and _sm.has(_risk, "_gp.adaptive_cap(chat_key, cap)"), "")
_i_cap = _risk.find("_gp.adaptive_cap(chat_key, cap)")
_i_cmp = _risk.find("len(self._st[key]) >= cap")
ok("C2★ 顺序即意图：缩放必须排在**比较之前**（排在后面等于没接）",
   0 <= _i_cap < _i_cmp, "缩放@%d 比较@%d" % (_i_cap, _i_cmp))
ok("C3 缩放**出异常时按原上限**走（不因缩放失败把消息憋住）",
   _sm.has(_risk, "按原上限处理"), "")
ok("C4 分母守卫：`risk.py` 里确实有那三档上限（别把判据挂在一个不存在的地方）",
   _sm.has(_risk, '"per_minute"') and _sm.has(_risk, '"per_hour"') and _sm.has(_risk, '"per_day"'), "")

print("\n== 群活跃度自适应判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
