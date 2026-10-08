# -*- coding: utf-8 -*-
"""「记忆的时效 / 纪念日 / 待跟进 / 亲近度 / 活跃时段」判据（第 113·114·115·116·121·125 项）。

守五件事（每件都配**反向锚**，不许恒真）：
  A 事件**会淡出**（过期就不进提示词，但**文件里仍在**）；`long_term` 永不过期；`<=0` 全部不过期。
  B **纪念日按 `MM-DD` 比**（`YYYY-MM-DD` 也行 ⇒ 跨年自动重复生效）；**抽不出月日的不算纪念日**。
  C 待跟进：**答过就不再提**；**问过没答 ⇒ 还能再提**（第 125 项）；过期/上限/追加式改动都成立。
  D 亲近度与活跃时段：**极端样本必须明显不同**；没数据 ⇒ 0 / 空（**不许编**）。
  E 接线：`MemoryStore` 上有那几个方法，且提示词**真的调了** `seeds_for_prompt`。

全部在**临时数据目录**里跑（`_iso14` 把产品的 `data/` 与 `logs/` 都指到临时区）。

用法：runtime\\python\\python.exe scripts/memory_expiry_selftest.py
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
_iso14.all_()            # 判据不许写产品的 data/ 与 logs/

from agent import memory as M  # noqa: E402

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name,
                             "  [{}]".format(detail) if detail else ""))


NOW = time.time()
DAY = 86400.0
# ⛔ **群名必须每次跑都不一样**：判据临时区是按"群 key"分目录的，固定群名 ⇒
#   第二次跑会读到**上一次**留下的 followups/events（而且成员档按内容去重 ⇒
#   连时间戳都是上一轮的）⇒ 判据变成"看运气"（实测踩过：C1/D5 时好时坏）。
CK = "tg-%d" % os.getpid()
CK2 = "empty-%d" % os.getpid()


def _cfg(**kw):
    base = {"followup_expire_days": 30, "followup_show_max": 3, "event_expire_days": 60}
    base.update(kw)
    return {"memory": base}


print("── A. 事件会淡出 / long_term 永不过期 ──")
M.add_event(CK, "刚发生的事", who="甲", emotion="good")
_old = M.add_event(CK, "70天前的事", who="乙")
_lt = M.add_event(CK, "三年前的承诺", who="丙", long_term=True, date="2023-03-02")
# 手改落盘时间：把"70 天前""三年前"真挪到过去（append-only 文件本来就是历史）
_p = M._events_file(CK)
_rows = [io.open(_p, encoding="utf-8").read()]  # 只读一次，下面按行重写临时文件
import json as _json  # noqa: E402
import logging

log = logging.getLogger("persona-morph")
_lines = [l for l in _rows[0].splitlines() if l.strip()]
_fixed = []
for _l in _lines:
    try:
        _r = _json.loads(_l)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        continue
    if _r.get("what") == "70天前的事":
        _r["ts"] = NOW - 70 * DAY
    if _r.get("what") == "三年前的承诺":
        _r["ts"] = NOW - 1100 * DAY
    _fixed.append(_json.dumps(_r, ensure_ascii=False))
io.open(_p, "w", encoding="utf-8").write("\n".join(_fixed) + "\n")

_names = [str(e.get("what")) for e in M.pending_events(CK, cfg=_cfg())]
ok("A1 没过期的在（刚发生的）", "刚发生的事" in _names, str(_names))
ok("A2★ 过了 `event_expire_days` 的**不再进提示词**", "70天前的事" not in _names, str(_names))
ok("A3★ 但**文件里那条仍在**（淡出 ≠ 删除）",
   any(str(e.get("what")) == "70天前的事" for e in M.list_events(CK, limit=99)), "")
ok("A4★ `long_term=True` ⇒ **永不过期**（三年前那条也在）", "三年前的承诺" in _names, str(_names))
ok("A5 `event_expire_days<=0` ⇒ 全部不过期（70 天前那条也回来）",
   "70天前的事" in [str(e.get("what")) for e in M.pending_events(CK, cfg=_cfg(event_expire_days=0))], "")
ok("A6 分母守卫：**没有任何事件的群** ⇒ 空表（判据不是恒真）",
   M.pending_events(CK2, cfg=_cfg()) == [], "")

print("\n── B. 纪念日按 月-日 比（跨年生效）──")
ok("B1★ `YYYY-MM-DD` 与今天的 `MM-DD` 相同 ⇒ 命中（跨年自动重复）",
   any(str(e.get("what")) == "三年前的承诺" for e in M.due_anniversaries(CK, "03-02")), "")
ok("B2★ 反向锚：别的日期 ⇒ **不命中**", M.due_anniversaries(CK, "03-03") == [],
   str(M.due_anniversaries(CK, "03-03")))
M.add_event(CK, "明年去旅行", who="丁")
ok("B3★ **抽不出月日的**（只写「明年去旅行」）**不算纪念日**（该走待跟进那一档）",
   not any(str(e.get("what")) == "明年去旅行" for e in M.due_anniversaries(CK, "03-02")), "")
ok("B4 `_mmdd` 三种写法都认（`2027-03-02` / `3-2` / `03-02`）",
   (M._mmdd("2027-03-02"), M._mmdd("3-2"), M._mmdd("03-02")) == ("03-02", "03-02", "03-02"), "")

print("\n── C. 待跟进：答过不提 / 问过没答还能再提 ──")
M.append_followup(CK, "他明天考试", who="甲")
M.append_followup(CK, "明年结婚", who="乙", long_term=True)
M.append_followup(CK, "下周三一起吃饭", who="甲")
_pend = [str(x.get("what")) for x in M.pending_followups(CK, cfg=_cfg())]
ok("C1 新加的都在", "他明天考试" in _pend and "下周三一起吃饭" in _pend, str(_pend))
ok("C2★ `answered=True`（问到了答案）⇒ **不再出现**",
   (M.resolve_followup(CK, "他明天考试", answered=True) is not None)
   and "他明天考试" not in [str(x.get("what")) for x in M.pending_followups(CK, cfg=_cfg())], "")
ok("C3★ `answered=False`（**问了对方没答**）⇒ **仍然出现**，且 `asked_count` 记上了"
   "（第 125 项：过一阵自然再提一次的依据）",
   (M.resolve_followup(CK, "下周三一起吃饭", answered=False) is not None)
   and any(str(x.get("what")) == "下周三一起吃饭" and int(x.get("asked_count") or 0) >= 1
           for x in M.pending_followups(CK, cfg=_cfg())), "")
_rowsnow = len([1 for l in io.open(M._followups_file(CK), encoding="utf-8").read().splitlines() if l.strip()])
M.resolve_followup(CK, "下周三一起吃饭", answered=False)
ok("C4★ 追加式：记状态**不改老行**（文件行数只增）",
   len([1 for l in io.open(M._followups_file(CK), encoding="utf-8").read().splitlines() if l.strip()]) == _rowsnow + 1,
   "%d → 见文件" % _rowsnow)
# 过期：造一条 40 天前的 add 行
_pf = M._followups_file(CK)
_first = io.open(_pf, encoding="utf-8").read().splitlines()[1]
_r = _json.loads(_first)
_r["ts"] = NOW - 40 * DAY
io.open(_pf, "a", encoding="utf-8").write(_json.dumps(_r, ensure_ascii=False) + "\n")
ok("C5 过期（40 天 > 30 天）的**不再出现**",
   not any(str(x.get("what")) == str(_r.get("what")) and not x.get("long_term")
           for x in M.pending_followups(CK, cfg=_cfg())), str(_r.get("what")))
ok("C6★ `followup_expire_days=0` ⇒ 全部不过期（过期那条回来）",
   any(str(x.get("what")) == str(_r.get("what"))
       for x in M.pending_followups(CK, cfg=_cfg(followup_expire_days=0))), "")
ok("C7 `long_term` 的**不受过期影响**（明年结婚那条一直在）",
   any(str(x.get("what")) == "明年结婚" for x in M.pending_followups(CK, cfg=_cfg())), "")
for i in range(6):
    M.append_followup(CK, "凑数第 %d 件" % i, who="甲")
ok("C8★ `followup_show_max` 生效（造 9 条以上 ⇒ 只回 3 条，防提示词被塞爆）",
   len(M.pending_followups(CK, cfg=_cfg())) == 3, str(len(M.pending_followups(CK, cfg=_cfg()))))
ok("C9 `user_ids` 过滤（只要甲的事 ⇒ 乙的「明年结婚」不出现）",
   not any(str(x.get("what")) == "明年结婚"
           for x in M.pending_followups(CK, ["甲"], cfg=_cfg())), "")

print("\n── D. 亲近度 / 活跃时段 ──")
# ⛔ 造样本必须**钉在某个本地小时**上：直接拿"现在减 N 天"当样本，桶会全落在**当前小时**
#   （第一版就是这么踩的：断言 21 点、实际全在 17 点）。⇒ 用本地日历算一个 21:00 的基准。
_lt0 = time.localtime(NOW)
_BASE21 = time.mktime((_lt0.tm_year, _lt0.tm_mon, _lt0.tm_mday, 21, 0, 0, 0, 0, -1))
_st = M.MemoryStore()
for i in range(60):        # 「天天聊」：120 条、最新一条就在今天 21 点
    _st._append_raw(CK, "uid_busy", "忙人", "印象 %d" % i,
                    created_at=(_BASE21 - i * DAY) * 1000)
    _st._append_raw(CK, "uid_busy", "忙人", "夜间 %d" % i,
                    created_at=int(_BASE21 - i * DAY) * 1000)
_c_busy = M.closeness(CK, "uid_busy", now=NOW)
_st._append_raw(CK, "uid_cold", "冷人", "半年前聊过一句", created_at=(NOW - 180 * DAY) * 1000)
_c_cold = M.closeness(CK, "uid_cold", now=NOW)
ok("D1★ 极端样本**明显不同**（天天聊 vs 半年前一句）", _c_busy > 0.7 > _c_cold,
   "忙人=%.4f 冷人=%.4f" % (_c_busy, _c_cold))
ok("D2 两者都在 0~1 内", 0.0 <= _c_cold <= 1.0 and _c_busy <= 1.0, "")
ok("D3 没有任何记录 ⇒ **0.0**（不编成中位数）",
   M.closeness(CK, "uid_never", now=NOW) == 0.0, str(M.closeness(CK, "uid_never", now=NOW)))
_h = M.active_hours(CK, "uid_busy")
ok("D4★ 24 个桶、总数＝样本数（分母守卫）", len(_h) == 24 and sum(_h) == 120,
   "len=%d sum=%d" % (len(_h), sum(_h)))
ok("D5★ 样本都是 21 点附近 ⇒ 21 点那桶最大，`busiest_hours` 首位是 21",
   _h[21] == max(_h) and M.busiest_hours(CK, "uid_busy")[0] == 21,
   "h21=%d max=%d busiest=%s" % (_h[21], max(_h), M.busiest_hours(CK, "uid_busy")[:3]))
ok("D6 没记录的成员 ⇒ 全 0（不编）", set(M.active_hours(CK, "uid_never")) == {0}, "")

print("\n── E. 接线：提示词真的调了 ──")
_seed_none = M.seeds_for_prompt(CK2)
_e_py = io.open(os.path.join(ROOT, "agent", "prompt.py"), encoding="utf-8").read()
ok("E1★ `MemoryStore` 上有那几个方法（薄封装在位 ⇒ 注入不会静默死）",
    all(hasattr(_st, n) for n in ("seeds_for_prompt", "proactive_seeds", "pending_followups",
                                  "pending_events", "due_anniversaries", "closeness", "active_hours")), "")
ok("E2★ `prompt.py` **真的调了** `seeds_for_prompt`",
   _sm.has(_e_py, "seeds_for_prompt(") and _sm.has(_e_py, "ctx[\"memory\"]"), "")
_i_mem = _e_py.find("【记忆】")
_i_seed = _e_py.find("seeds_for_prompt(")
ok("E3★ 顺序即意图：注入排在【记忆】**之后**（先人后事；反了就是两段各说各的）",
   0 <= _i_mem < _i_seed, "记忆@%d 种子@%d" % (_i_mem, _i_seed))
ok("E4 没有任何内容 ⇒ **空串**（绝不硬凑一段空话进提示词）",
   _seed_none == "", repr(_seed_none)[:60])
_has = M.seeds_for_prompt(CK, cfg=_cfg())
ok("E5 有内容时是人话段（带「你记得的事」标题 ＋ **至少一条具体的事**）",
   _has.startswith("【你记得的事】") and ("有件事该问一下：" in _has or "今天是「" in _has),
   _has[:60].replace("\n", "⏎"))
ok("E6 反向锚：**不存在的群** ⇒ 三样都空、不抛",
   (lambda d: d.get("followups") == [] and d.get("anniversaries") == [] and d.get("events") == []
    )(M.proactive_seeds(CK2)), "")

print("\n── F. 折叠「同名两套键」（第 117 项）──")
CK3 = "fold-%d" % os.getpid()
_st2 = M.MemoryStore()
_st2._append_raw(CK3, "uid_a", "小明", "按 wxid 存的印象")     # 有 wxid 那一档
_st2._append_raw(CK3, "", "小明", "关机总结留下的印象")          # 按名字的 `_n_` 档
_ms1 = _st2.members(CK3)
ok("F1★ 同一人两条键（一条有 wxid、一条没有）⇒ **折成一行**",
   len(_ms1) == 1 and str(_ms1[0].get("userId")) == "uid_a", str([(m.get("userId"), m.get("name")) for m in _ms1]))
ok("F1b 折叠时**印象取并集**（两边的内容都在，且没有重复行）",
   len(_ms1[0].get("impressions") or []) == 2, str(len(_ms1[0].get("impressions") or [])))
ok("F1c `list_members()` 是 `members()` 的别名（面板与工具两处都在用）",
   len(_st2.list_members(CK3)) == len(_ms1), "")
CK4 = "fold2-%d" % os.getpid()
_st3 = M.MemoryStore()
_st3._append_raw(CK4, "uid_x", "张三", "X 的印象")
_st3._append_raw(CK4, "uid_y", "张三", "Y 的印象")   # **两个不同的人、同名**
_st3._append_raw(CK4, "", "张三", "不知道是谁的印象")
_ms2 = _st3.members(CK4)
ok("F2★★ 反向锚：名字出现**三次以上**（可能真是两个同名的人）⇒ **不许折**"
   "（宁可多显示一行，也不把两个人的记忆串到一起）",
   len(_ms2) == 3, str([(m.get("userId"), m.get("name")) for m in _ms2]))
ok("F2b 分不清的那些行**如实标注**（`ambiguous_name`），不装作已干净",
   all(m.get("ambiguous_name") for m in _ms2), "")

print("\n== 记忆时效/待跟进判据：%d 通过 / %d 失败 ==" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
