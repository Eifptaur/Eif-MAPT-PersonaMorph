# -*- coding: utf-8 -*-
"""「主动私聊」判据 —— 守第 122 项。

⛔ 主动开口最容易变成"不请自来" ⇒ **六条闸门缺一不可**，判据的核心就是逐条证明它们真拦得住：
  · **G1 默认关**：`cfg={}` ⇒ 恒 `fire=False`；
  · **G2 只认一个明确会话**：配成列表/字典 ⇒ 当作没配（⛔ 不许"猜猜用户想要哪个"）；
  · **G3 过风控**：风控拦住 ⇒ 不发；**风控读不出来也按不许发**（fail-closed）；
  · **G5 数量封顶**：每会话/全局**硬上限**（配置填再大也不越）；
  · **G6 必须显式开启**：有 `target` 但没 `enabled` ⇒ 仍按关。
另有四条时机/内容规则，逐条给「该跳」与「不该跳」两面：
  · `skip_if_recent_min`（对方刚发过 ⇒ 跳过）· `min_gap_min`（防连发）；
  · `topic_dedup`（同一天不重复问）· `time_aware`（时段直方图）；
以及验收原话那条：**跨午夜排程不丢当天剩下的条数**。

用法：runtime\\python\\python.exe scripts\\proactive_private_selftest.py
"""
import io
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PASS = FAIL = 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    tag = "OK  " if cond else "FAIL"
    print("  {} {}{}".format(tag, name, "  [{}]".format(detail) if detail else ""))


import _srcslice as _ss  # noqa: E402
from agent import proactive as P  # noqa: E402

CFG_SRC = io.open(os.path.join(ROOT, "agent", "config.py"), encoding="utf-8").read()
EX_SRC = io.open(os.path.join(ROOT, "config.example.json"), encoding="utf-8").read()
PR_SRC = io.open(os.path.join(ROOT, "agent", "proactive.py"), encoding="utf-8").read()

TARGET = "private:测试会话甲"
NOW = time.time()


class FakeStore:
    def __init__(self, msgs):
        self._m = msgs

    def recent(self, ck, limit=30):
        return self._m


def _cfg(**kw):
    d = dict(P.DEFAULTS)
    d.update(kw)
    return {"proactive_private": d}


def _ok_state(**kw):
    """一个"什么闸门都不该拦"的起点：显式开启、target 对上、刚过最小间隔。"""
    d = dict(P.DEFAULTS)
    d.update(kw)
    return {"proactive_private": d}


print("── A. 模块形态 ──")
for _n in ("should_fire", "plan_times", "in_window", "note_fired", "topic_line", "snapshot"):
    ok("A. 有 `%s`" % _n, callable(getattr(P, _n, None)))
ok("A1 零第三方依赖（只 import 标准库 + 延迟导入自己的模块）",
   all(x not in PR_SRC.split('"""')[2] for x in ("requests", "PIL", "numpy")), "")
ok("A2 ⛔ **不发送**：模块里不许出现发送类调用（发了就绕过了调用方的统一闸）",
   not any(k in PR_SRC for k in ("send_message(", "send_text(", "click(", "send_image(")), "")

print("── B. 六条闸门（逐条证明拦得住）──")
ok("B1 G1 默认关：空配置 ⇒ fire=False 且报 G1",
   (lambda r: r["fire"] is False and r["gate"] == "G1")(P.should_fire(TARGET, cfg={})), "")
ok("B2 G2 没配 target ⇒ 不发",
   (lambda r: r["fire"] is False and r["gate"] == "G2")(
       P.should_fire(TARGET, cfg=_cfg(enabled=True, target=""))), "")
ok("B3 ⛔ G2 target 配成**列表** ⇒ 当作没配（不许猜用户要哪个）",
   (lambda r: r["fire"] is False and r["gate"] == "G2")(
       P.should_fire(TARGET, cfg=_cfg(enabled=True, target=["private:a", "private:b"]))), "")
ok("B4 ⛔ G2 target 配成**字典** ⇒ 同上",
   (lambda r: r["fire"] is False and r["gate"] == "G2")(
       P.should_fire(TARGET, cfg=_cfg(enabled=True, target={"a": 1}))), "")
ok("B5 G2 会话对不上 ⇒ 不发",
   (lambda r: r["fire"] is False and r["gate"] == "G2")(
       P.should_fire("private:别的", cfg=_cfg(enabled=True, target=TARGET,
                                             window_start="00:00", window_end="23:59"))), "")
ok("B6 G6 有 target 但**没写** enabled ⇒ 仍按关（缺 key ⇒ 关，不许「没配就是开」）",
   (lambda r: r["fire"] is False and r["gate"] in ("G1", "G6"))(
       P.should_fire(TARGET, cfg={"proactive_private": {"target": TARGET}})), "")
ok("B7 G6 显式写 enabled=false ⇒ 关",
   (lambda r: r["fire"] is False)(P.should_fire(TARGET, cfg=_cfg(enabled=False))), "")
ok("B8 G5 每会话封顶：已发 3 次（默认 cap=3）⇒ 到顶",
   (lambda r: r["fire"] is False and r["gate"] == "G5")(P.should_fire(
       TARGET, store=FakeStore([]), has_topic=True,
       state={"day": {"date": time.strftime("%Y-%m-%d"), "per_chat": {TARGET: 3}, "total": 3}},
       cfg=_cfg(enabled=True, target=TARGET, window_start="00:00", window_end="23:59",
                skip_if_recent_min=0, min_gap_min=0))), "")
ok("B9 G5 全局封顶：全局已发 20 次 ⇒ 到顶",
   (lambda r: r["fire"] is False and r["gate"] == "G5")(P.should_fire(
       TARGET, store=FakeStore([]), has_topic=True,
       state={"day": {"date": time.strftime("%Y-%m-%d"), "per_chat": {}, "total": 20}},
       cfg=_cfg(enabled=True, target=TARGET, window_start="00:00", window_end="23:59",
                skip_if_recent_min=0, min_gap_min=0))), "")
ok("B10 ⛔ G5 **硬上限**：配置把每会话填 999 ⇒ 实际仍按 5 算",
   P.HARD_MAX_PER_CHAT == 5 and P.snapshot(_cfg(max_per_chat=999))["cap"]["per_chat"] == 5, "")
ok("B11 ⛔ G5 **硬上限**：全局填 999 ⇒ 实际仍按 30 算",
   P.HARD_MAX_PER_DAY == 30 and P.snapshot(_cfg(max_per_day=999))["cap"]["per_day"] == 30, "")

print("── C. 风控闸 G3（含 fail-closed）──")
try:
    from agent import risk as _R
    _real_check = _R.check

    class _V(object):
        def __init__(self, allowed, reason=""):
            self.allowed = allowed
            self.reason = reason

    _R.check = lambda ck, t="", **kw: _V(False, "今天发太多了")
    _r3 = P.should_fire(TARGET, store=FakeStore([]), has_topic=True,
                        cfg=_cfg(enabled=True, target=TARGET, window_start="00:00",
                                 window_end="23:59", skip_if_recent_min=0, min_gap_min=0))
    ok("C1 风控拦住 ⇒ 不发，且原因写清（gate=G3）",
       _r3["fire"] is False and _r3["gate"] == "G3" and "风控" in _r3["why"], str(_r3))

    def _boom(ck, t="", **kw):
        raise RuntimeError("风控文件坏了")

    _R.check = _boom
    _r4 = P.should_fire(TARGET, store=FakeStore([]), has_topic=True,
                        cfg=_cfg(enabled=True, target=TARGET, window_start="00:00",
                                 window_end="23:59", skip_if_recent_min=0, min_gap_min=0))
    ok("C2 ⛔ 风控**读不出来**也按不许发（fail-closed —— 主动开口最该保守）",
       _r4["fire"] is False and _r4["gate"] == "G3", str(_r4))
    _R.check = _real_check
except Exception as e: # noqa: BLE001
    ok("C. 风控闸", False, str(e)[:200])

print("── D. 时机四条（该跳 / 不该跳，两面都测）──")
_sf = P.should_fire
_BASE = dict(enabled=True, target=TARGET, window_start="00:00", window_end="23:59",
             skip_if_recent_min=0, min_gap_min=0)
# D1/D2 skip_if_recent_min
_r = _sf(TARGET, store=FakeStore([{"self": False, "ts": (NOW - 5 * 60) * 1000, "text": "在吗"}]),
         has_topic=True, cfg=_cfg(**dict(_BASE, skip_if_recent_min=20)))
ok("D1 对方 5 分钟前刚发过（阈值 20 分钟）⇒ 跳过，不打断",
   _r["fire"] is False and "跳过" in _r["why"], _r["why"])
_r = _sf(TARGET, store=FakeStore([{"self": False, "ts": (NOW - 40 * 60) * 1000, "text": "在吗"}]),
         has_topic=True, cfg=_cfg(**dict(_BASE, skip_if_recent_min=20)))
ok("D2 反向锚：对方 40 分钟前发过（超阈值）⇒ **不**跳，照常走",
   _r["fire"] is True, _r["why"])
# D3/D4 min_gap_min
_r = _sf(TARGET, store=FakeStore([]), has_topic=True,
         state={"last": {TARGET: NOW - 10 * 60}}, cfg=_cfg(**dict(_BASE, min_gap_min=90)))
ok("D3 距上次主动开口 10 分钟（阈值 90）⇒ 再等等",
   _r["fire"] is False and "再等等" in _r["why"], _r["why"])
_r = _sf(TARGET, store=FakeStore([]), has_topic=True,
         state={"last": {TARGET: NOW - 200 * 60}}, cfg=_cfg(**dict(_BASE, min_gap_min=90)))
ok("D4 反向锚：隔了 200 分钟 ⇒ 不拦", _r["fire"] is True, _r["why"])
# D5/D6 topic_dedup
_r = _sf(TARGET, store=FakeStore([]), has_topic=True, asked_today=True,
         cfg=_cfg(**dict(_BASE, topic_dedup=True)))
ok("D5 同一天已问过的话题 ⇒ 跳过（软去重）",
   _r["fire"] is False and "软去重" in _r["why"], _r["why"])
_r = _sf(TARGET, store=FakeStore([]), has_topic=True, asked_today=True,
         cfg=_cfg(**dict(_BASE, topic_dedup=False)))
ok("D6 反向锚：关掉软去重 ⇒ 不拦", _r["fire"] is True, _r["why"])
# D7/D8 没有话题就不发
_r = _sf(TARGET, store=FakeStore([]), has_topic=False, cfg=_cfg(**_BASE))
ok("D7 没有可自然开口的话题 ⇒ 不发（不硬找话说）",
   _r["fire"] is False and "话题" in _r["why"], _r["why"])
_r = _sf(TARGET, store=FakeStore([]), has_topic=True, cfg=_cfg(**_BASE))
ok("D8 有话题且全过 ⇒ 发", _r["fire"] is True and _r["gate"] == "", _r["why"])

print("── E. 时间窗（含跨次日）──")
ok("E1 全天窗 ⇒ 永远在窗内",
   P.in_window(cfg=_cfg(window_start="00:00", window_end="23:59")) is True)
def _at(hh, mm=0):
    import datetime as _dt
    d = _dt.datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    return d.timestamp()
ok("E2 09:00 不在 10:00~21:30 窗内",
   P.in_window(_at(9, 0), cfg=_cfg(window_start="10:00", window_end="21:30")) is False)
ok("E3 15:00 在窗内",
   P.in_window(_at(15, 0), cfg=_cfg(window_start="10:00", window_end="21:30")) is True)
ok("E4 ⛔ 时间窗写坏了（`25:99`）⇒ **不开**（fail-closed，不许「看不懂就全天开」）",
   P.in_window(cfg=_cfg(window_start="25:99", window_end="21:30")) is False)
# 跨次日：22:00~02:00
_W = _cfg(window_start="22:00", window_end="02:00")
ok("E5 跨次日窗：23:00 在窗内", P.in_window(_at(23, 0), cfg=_W) is True)
ok("E6 跨次日窗：01:00 也在窗内（跨到次日那一半）", P.in_window(_at(1, 0), cfg=_W) is True)
ok("E7 反向锚：跨次日窗：12:00 **不在**窗内", P.in_window(_at(12, 0), cfg=_W) is False)

print("── F. 排程（验收原话：跨午夜不丢当天剩下的条数）──")
_p = P.plan_times("2026-10-08", 3, seed=1, cfg=_cfg(window_start="10:00", window_end="21:30"))
ok("F1 排 3 条 ⇒ 就是 3 条（一条不多一条不少）", len(_p) == 3, str(_p))
ok("F2 排出来的时刻都在窗内", all(10 * 60 <= m < 21 * 60 + 30 for m in _p), str(_p))
ok("F3 从小到大有序（便于按序触发）", _p == sorted(_p), str(_p))
_pc = P.plan_times("2026-10-08", 3, seed=2, cfg=_cfg(window_start="22:00", window_end="02:00"))
ok("F4 ⭐ 跨午夜窗排 3 条 ⇒ **仍是 3 条**（不因跨了午夜就丢）", len(_pc) == 3, str(_pc))
ok("F5 跨午夜的时刻都落在 [22:00,24:00) ∪ [00:00,02:00)",
   all(m >= 22 * 60 or m < 2 * 60 for m in _pc), str(_pc))
ok("F6 同 seed 可复现（判据要能钉住随机）",
   P.plan_times("2026-10-08", 3, seed=1, cfg=_cfg(window_start="10:00", window_end="21:30")) == _p)
ok("F7 `n=0` ⇒ 空表（不发就是不发，不凑数）",
   P.plan_times("2026-10-08", 0, seed=1, cfg=_cfg()) == [])

print("── G. 记账与话题（`note_fired` / `topic_line`）──")
_s = P.note_fired(TARGET, now=NOW)
ok("G1 记一次 ⇒ 会话计数 +1", _s["day"]["per_chat"][TARGET] == 1, str(_s["day"]))
ok("G2 记一次 ⇒ 全局计数 +1", _s["day"]["total"] == 1)
ok("G3 连记两次 ⇒ 会话计数 2", P.note_fired(TARGET, state=_s, now=NOW)["day"]["per_chat"][TARGET] == 2)
ok("G4 记了 `last`（下次按它算最小间隔）", bool(_s.get("last", {}).get(TARGET)))
# topic_line 不许调 LLM（零 token）
_BODY = _ss.func_src(PR_SRC, "topic_line")
ok("G5 分母守卫：拿到 `topic_line` 函数体（>300 字符）", len(_BODY) > 300, "%d 字符" % len(_BODY))
# ⛔ 只扫**真调用**（先按行滤掉注释）：注释里写着"不调 LLM"是**说明**，
#   拿它当证据会得出反向结论（"提了 LLM ⇒ 一定在调"）—— 这正是"锚意图不锚文案"。
_CODE_LINES = [ln for ln in _BODY.split("\n")
               if not ln.strip().startswith("#") and "不调" not in ln]
ok("G6 ⛔ `topic_line` **不调 LLM**（本模块零 token）",
   not any(("chat_completion" in ln) or ("llm" in ln.lower() and "import" in ln)
           for ln in _CODE_LINES), "")
ok("G7 没有 mem ⇒ 空串（判据可直接验，不用真记忆）", P.topic_line(None, TARGET) == "")

print("── H. 配置与定档 ──")
ok("H1 `config.py` 有 `proactive_private` 段", '"proactive_private"' in CFG_SRC)
# ⛔ 段头注释在**键名之前** ⇒ 取"键名前 900 字符 + 键后到下一顶层键"（否则漏掉段头）
_SEG = CFG_SRC.split('"proactive_private"')[0][-900:] + \
       CFG_SRC.split('"proactive_private"')[1].split('"persona"')[0]
ok("H2 ⛔ 配置注释里写明「只认一个会话 / 不许群列表」",
   "一个" in _SEG and "列表" in _SEG, "%d 字符的段注释" % len(_SEG))
ok("H3 `config.example.json` 同步了该段（否则 fullcheck 的同键判据会红）",
   '"proactive_private": {' in EX_SRC, "")
# ⛔ 定位到**段本体**（不是注释键名那处）再查默认值
_EXSEG = EX_SRC.split('"proactive_private": {')[1][:200]
ok("H4 ⛔ 默认 `enabled=false`（示例配置里也是关的）",
   '"enabled": false' in _EXSEG, _EXSEG[:80])

print("── I. 状态持久化与到点判定 ──")
# ⛔ 判据自己把状态文件换到临时目录 —— **绝不碰产品 `data/`**（用户真机上的排程状态不能被判据改写）。
import tempfile as _tf  # noqa: E402
_TD = _tf.mkdtemp(prefix="pp-")
_ORIG_PATH = P._state_path
P._state_path = lambda: os.path.join(_TD, "proactive_state.json")
try:
    _p1 = P._state_path()
    ok("I1 状态文件落在临时目录（判据不碰产品 data/）", _TD in _p1, _p1[-40:])
    ok("I2 文件不存在 ⇒ `load_state()` 给**空 dict**（不抛、不编）", P.load_state() == {}, "")
    ok("I3 `save_state` 真写盘（原子替换：写完临时文件已被 os.replace 掉）",
       P.save_state({"x": 1}) is True and P.load_state().get("x") == 1
       and (not os.path.exists(_p1 + ".tmp")), "")
    # 写坏文件 ⇒ 读到空状态（**不该让功能瘫掉**；多发几条的风险由 min_gap/封顶兜住）
    with io.open(_p1, "w", encoding="utf-8") as _fh:
        _fh.write("{这不是合法 JSON")
    ok("I4★ 状态文件坏 ⇒ 回退空状态（不抛异常）", P.load_state() == {}, str(P.load_state()))
    _mins, _st = P.plan_for_today({}, now=NOW, cfg=_cfg(times_per_day=3))
    ok("I5 当天没排过 ⇒ 现排 3 个时刻并写进 state", len(_mins) == 3 and len(_st["plan"]["mins"]) == 3,
       str(_mins))
    _mins2, _st2 = P.plan_for_today(_st, now=NOW, cfg=_cfg(times_per_day=9))
    ok("I6★ **同一天不重排**（即使把 times_per_day 改了 —— 否则一天里条数会漂）",
       _mins2 == _mins, "%s vs %s" % (_mins, _mins2))
    # 到点判定：**必须用窗口命中**（主循环 3 秒转一圈，精确相等 ⇒ 永远不触发）
    _hit = P.due_now({"plan": {"date": time.strftime("%Y-%m-%d", time.localtime(NOW)),
                               "mins": [time.localtime(NOW).tm_hour * 60 + time.localtime(NOW).tm_min]}},
                     now=NOW, cfg=_cfg())
    ok("I7★ 排程时刻＝此刻 ⇒ `due_now` 命中（窗口口径，不是精确相等）", _hit is True, "")
    _far = (time.localtime(NOW).tm_hour * 60 + time.localtime(NOW).tm_min + 600) % 1440
    ok("I8 排程时刻在 10 小时外 ⇒ 不命中（反向锚：命中不是恒真）",
       P.due_now({"plan": {"date": time.strftime("%Y-%m-%d", time.localtime(NOW)), "mins": [_far]}},
                 now=NOW, cfg=_cfg()) is False, "")
    ok("I9 没有排程 ⇒ 不命中（不抛）", P.due_now({}, now=NOW, cfg=_cfg()) is False, "")

    print("── J. 主循环接线（执行侧真的被挂上了）──")
    PM_SRC = io.open(os.path.join(ROOT, "scripts", "persona_morph.py"), encoding="utf-8").read()
    ok("J1 主循环里调了 `tick_private_proactive`（不是只定义了没接）",
       "tick_private_proactive" in PM_SRC
       and PM_SRC.count("tick_private_proactive") >= 2, "出现 %d 次" % PM_SRC.count("tick_private_proactive"))
    ok("J2 方法在「扫群」之前调用（微信没接上的轮次不会误开口）",
       PM_SRC.index("orch.tick_private_proactive()") < PM_SRC.index("for g in targets:\n                wxid"), "")
    _TICK = _ss.func_src(PM_SRC, "tick_private_proactive")
    ok("J3 分母守卫：拿到 `tick_private_proactive` 函数体（>400 字符）", len(_TICK) > 400, "%d 字符" % len(_TICK))
    ok("J4 ⛔ tick **只排程、不发送**（发送由 `_schedule_proactive_wake` 那条既有链走）",
       "_schedule_proactive_wake" in _TICK and "send_message" not in _TICK, "")
    ok("J5 ⛔ tick 过 `should_fire`（判定逻辑只在那一处，不在这里另写一套）",
       "should_fire" in _TICK and "has_topic" in _TICK, "")
    ok("J6 暂停/退出时不排（尊重暂停）", "self.paused" in _TICK and "self.stopped" in _TICK, "")
    _ONCE = _ss.func_src(PM_SRC, "_proactive_once")
    ok("J7★ 发出后**记账**（不记账 ⇒ 封顶永远不生效，每次都能再发一条）",
       "note_fired" in _ONCE, "")
    ok("J8★ ⛔ **真发出去了才记**（`session['sent']` 非空才记）——"
       "模型可以选择「不想聊就结束」，那一轮一条没发，记进去会让封顶提前耗尽",
       "session.get(\"sent\")" in _ONCE, "")
finally:
    P._state_path = _ORIG_PATH

print("")
print("主动私聊判据：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
