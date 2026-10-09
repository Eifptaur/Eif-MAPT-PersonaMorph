# -*- coding: utf-8 -*-
"""主动私聊排程（第 122 项）—— **纯规则、零 LLM、零发送**。

它只回答两个问题：**「现在该不该主动开口」** 与 **「先开口说什么」**；
真正的发送由调用方执行（这样本模块可以在判据里全量验证，不碰微信、不出网）。

⛔ **六条闸门缺一不可**（这是本模块存在的意义 —— 主动开口最容易变成"不请自来"）：
  · **G1 默认关** —— `proactive_private.enabled` 缺省/非真 ⇒ 恒 `should=False`；
  · **G2 只能定到一个明确会话** —— 配置 schema 里**不许出现"群列表"**：只认 `target`
    这一个 `chat_key`（数组/别的形状一律当作没配 ⇒ 关）。①「少就是多」：一个就够；
  · **G3 过 `risk.check()`** —— 风控说不行就不行（复用既有闸门，不自己另写一套）；
  · **G4 过 `rhythm`** —— 排出来的时刻要过拟人节奏（1.5~4s 抖动），**不在本模块 sleep**；
  · **G5 数量封顶** —— 每会话 `max_per_chat`（默认 3）、全局 `max_per_day`（默认 20）；
  · **G6 必须显式开启** —— 缺 key ⇒ **关**，绝不许"没配就是开"。

另有四条**内容/时机**规则（它们不是闸门，是"什么时候该闭嘴"）：
  · `skip_if_recent_min` —— 对方最近 N 分钟内刚发过话 ⇒ **这轮跳过，不打断**；
  · `min_gap_min` —— 两次之间至少隔这么久（防连发）；
  · `topic_dedup` —— 同一天不重复问同一个待跟进（**软去重**：只压当天的重复）；
  · `time_aware` —— 优先挑**对方常在的时段**（走 `memory.active_hours()` 直方图）。
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("persona-morph")

#: 默认参数（全部可被 `proactive_private` 段覆盖）
DEFAULTS = {
    "enabled": False,          # G1/G6：默认关
    "target": "",              # G2：只认一个明确会话（chat_key）
    "times_per_day": 2,        # 一天随机发几条
    "window_start": "10:00",   # 允许开口的起点
    "window_end": "21:30",     # 允许开口的终点（<= start ⇒ 跨到次日）
    "min_gap_min": 90,         # 两次最小间隔（分钟）
    "skip_if_recent_min": 20,  # 对方最近 N 分钟内刚发过 ⇒ 跳过
    "respect_paused": True,    # 风控暂停时不动
    "topic_dedup": True,       # 同一天不重复问同一个待跟进（软去重）
    "share_enabled": True,     # 可主动分享"我这边的近况/趣事"
    "time_aware": True,        # 优先挑对方常在的时段
    "anniversary_enabled": True,  # 纪念日当天可主动提
    "max_per_chat": 3,         # G5：每会话封顶（**不许被配置放大到离谱**）
    "max_per_day": 20,         # G5：全局封顶
}

#: 每会话/全局封顶的**硬上限** —— 配置填再大也不许越（防"配错一个数就刷屏"）
HARD_MAX_PER_CHAT = 5
HARD_MAX_PER_DAY = 30


def _cfg(cfg=None) -> dict:
    """取 `proactive_private` 段（拿不到 ⇒ 空 dict ⇒ 后面按默认关）。"""
    try:
        if cfg is None:
            from .config import get_config
            cfg = get_config()
        return dict((cfg or {}).get("proactive_private") or {})
    except Exception:  # noqa: BLE001
        return {}


def _opt(c: dict, key: str, default):
    v = c.get(key)
    if v is None:
        return default
    try:
        if isinstance(default, bool):
            # ⛔ 显式开启口径：字符串 "false"/"0" 不许被 `bool()` 读成真
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on", "是", "开")
            return bool(v)
        if isinstance(default, int):
            return int(v)
        return v
    except (TypeError, ValueError):
        return default


def _target_of(c: dict) -> str:
    """G2：**只认一个明确会话**。数组/列表形状一律当作没配（⇒ 关）。

    ⛔ 为什么不支持群列表：主动开口是"不请自来"的行为，一旦能撒到多个会话，
      出错的代价（打扰一批人）与"只打扰一个"不是一个量级。要发多个 ⇒ 配多份，各自开。
    """
    t = c.get("target")
    if isinstance(t, str):
        return t.strip()
    if isinstance(t, (list, tuple, dict)):
        # 形状不对 ⇒ 关（⛔ 不取第一个"猜猜用户想要哪个"）
        return ""
    return ""


def _hm(s: str) -> int:
    """`HH:MM` ⇒ 分钟数；取不出 ⇒ -1。"""
    try:
        h, m = str(s or "").strip().split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return -1
        return h * 60 + m
    except Exception:  # noqa: BLE001
        return -1


def in_window(now_ts=None, *, cfg=None) -> bool:
    """现在在不在"允许开口"的时间窗里。`window_end <= window_start` ⇒ **跨次日**。"""
    c = _cfg(cfg)
    lo = _hm(_opt(c, "window_start", DEFAULTS["window_start"]))
    hi = _hm(_opt(c, "window_end", DEFAULTS["window_end"]))
    if lo < 0 or hi < 0:
        return False      # ⛔ 时间窗写了看不懂 ⇒ 不开（fail-closed，不许"看不懂就全天开"）
    t = time.localtime(float(now_ts if now_ts is not None else time.time()))
    cur = t.tm_hour * 60 + t.tm_min
    if hi > lo:
        return lo <= cur < hi
    # 跨次日：要么在 [lo, 24:00)，要么在 [00:00, hi)
    return cur >= lo or cur < hi


def plan_times(day: str, n: int, *, seed=None, cfg=None) -> list:
    """给 **一天** 排 n 个时刻（返回分钟数列表，已排序）。

    ⛔ 只排**当天**、不排"明天" —— 排程的持久化交给调用方（本模块无状态，判据才好验）。
    ⛔ 窗跨次日时，`n` 条里**不许因为"跨了午夜"就丢掉**（验收原话："跨午夜排程不丢当天剩下的条数"）。
    """
    c = _cfg(cfg)
    lo = _hm(_opt(c, "window_start", DEFAULTS["window_start"]))
    hi = _hm(_opt(c, "window_end", DEFAULTS["window_end"]))
    if lo < 0 or hi < 0:
        return []
    try:
        k = max(0, int(n or 0))
    except (TypeError, ValueError):
        k = 0
    if k <= 0:
        return []
    # 窗长（跨次日时＝(24:00-lo) + hi）
    span = (hi - lo) if hi > lo else (24 * 60 - lo + hi)
    if span <= 0:
        return []
    import random as _r
    rng = _r.Random(seed) if seed is not None else _r
    mins = sorted(int(lo + rng.random() * span) % (24 * 60) for _ in range(k))
    return mins


def _recent_self_talk(store, chat_key: str, now_ms: float, within_min: float) -> bool:
    """对方最近 `within_min` 分钟内发过话没有（发过 ⇒ 别主动打断）。"""
    if store is None or within_min <= 0:
        return False
    try:
        msgs = store.recent(chat_key, limit=30)
    except Exception as e:  # noqa: BLE001
        log.debug("读最近消息失败（按「没发过」处理）：%s", e, exc_info=True)
        return False
    lo = now_ms - within_min * 60000.0
    for m in (msgs or []):
        if m.get("self"):
            continue
        try:
            ts = float(m.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts >= lo:
            return True
    return False


def _last_fire(state: dict, chat_key: str) -> float:
    try:
        return float((state.get("last") or {}).get(chat_key) or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _today_count(state: dict, chat_key: str, day: str) -> tuple:
    """⇒ (该会话今天发了几条, 全局今天发了几条)。"""
    d = state.get("day") if isinstance(state.get("day"), dict) else {}
    if str(d.get("date") or "") != str(day):
        return 0, 0
    per = d.get("per_chat") if isinstance(d.get("per_chat"), dict) else {}
    return int(per.get(str(chat_key)) or 0), int(d.get("total") or 0)


def should_fire(chat_key: str = "", *, store=None, state=None, now=None, cfg=None,
                has_topic: bool = False, asked_today: bool = False) -> dict:
    """**唯一判定入口**：现在该不该主动开口 ⇒ `{fire, why, gate}`。

    `why` 一律给**人能看懂**的理由（命中哪条闸门就报哪条）；`gate` 给闸门代号（G1..G6 或 `""`）。
    ⛔ 任何一条闸门拦下 ⇒ `fire=False` 且**原因写清**（不许静默返回 False 让人猜）。
    """
    c = _cfg(cfg)
    target = _target_of(c)
    # G1 / G6：默认关 + 必须显式开启
    if not _opt(c, "enabled", DEFAULTS["enabled"]):
        return {"fire": False, "gate": "G1", "why": "主动私聊默认关（要显式打开才生效）"}
    # G2：只能定到一个明确会话
    if not target:
        return {"fire": False, "gate": "G2", "why": "没配 target（或配成了列表/别的形状）⇒ 不发"}
    if chat_key and str(chat_key) != target:
        return {"fire": False, "gate": "G2", "why": "当前会话不是配的那个（只认一个明确会话）"}
    # G6：显式开启
    if "enabled" not in c:
        return {"fire": False, "gate": "G6", "why": "配置里没写 enabled ⇒ 按关处理"}
    now_ts = float(now if now is not None else time.time())
    now_ms = now_ts * 1000.0
    day = time.strftime("%Y-%m-%d", time.localtime(now_ts))
    st = state if isinstance(state, dict) else {}
    # 时间窗
    if not in_window(now_ts, cfg=cfg):
        return {"fire": False, "gate": "", "why": "不在允许开口的时间窗里"}
    # 对方刚发过 ⇒ 跳过（不打断）
    _skip_min = _opt(c, "skip_if_recent_min", DEFAULTS["skip_if_recent_min"])
    if _recent_self_talk(store, target, now_ms, float(_skip_min or 0)):
        return {"fire": False, "gate": "",
                "why": "对方 %d 分钟内刚发过话 ⇒ 这轮跳过，不打断" % int(_skip_min or 0)}
    # 两次最小间隔
    _gap = float(_opt(c, "min_gap_min", DEFAULTS["min_gap_min"]) or 0)
    _last = _last_fire(st, target)
    if _last and (now_ts - _last) < _gap * 60:
        return {"fire": False, "gate": "",
                "why": "距上次主动开口不到 %d 分钟 ⇒ 再等等" % int(_gap)}
    # G5：数量封顶
    _per, _tot = _today_count(st, target, day)
    _cap_chat = min(int(_opt(c, "max_per_chat", DEFAULTS["max_per_chat"]) or 0), HARD_MAX_PER_CHAT)
    _cap_day = min(int(_opt(c, "max_per_day", DEFAULTS["max_per_day"]) or 0), HARD_MAX_PER_DAY)
    if _cap_chat and _per >= _cap_chat:
        return {"fire": False, "gate": "G5", "why": "这个会话今天已经主动开口 %d 次（到顶了）" % _per}
    if _cap_day and _tot >= _cap_day:
        return {"fire": False, "gate": "G5", "why": "今天全局主动开口已达 %d 次（到顶了）" % _tot}
    # topic_dedup：同一天不重复问同一个待跟进（软去重）
    if _opt(c, "topic_dedup", DEFAULTS["topic_dedup"]) and asked_today:
        return {"fire": False, "gate": "", "why": "这个话题今天已经问过一次了（软去重）"}
    # G3：风控
    if _opt(c, "respect_paused", DEFAULTS["respect_paused"]):
        try:
            from . import risk as _risk
            v = _risk.check(target, "")
            if not getattr(v, "allowed", True):
                return {"fire": False, "gate": "G3",
                        "why": "风控说这次不行：%s" % (getattr(v, "reason", "") or "被拦")}
        except Exception as e:  # noqa: BLE001
            # ⛔ 风控**读不出来**时按"不许发"处理（fail-closed）—— 主动开口是最该保守的一类
            log.debug("风控不可用（按不许发处理）：%s", e, exc_info=True)
            return {"fire": False, "gate": "G3", "why": "风控状态读不出来（保守起见不发）"}
    # ⛔ 没有可说的**也**不发（不想说就直接结束，别为"发而发"）
    if not has_topic:
        return {"fire": False, "gate": "", "why": "这轮没有可自然开口的话题 ⇒ 不硬找话说"}
    return {"fire": True, "gate": "", "why": "可以开口（过全部闸门）"}


def note_fired(chat_key: str, *, state=None, now=None) -> dict:
    """记一次"真的开口了" ⇒ 返回更新后的 state（调用方负责持久化）。"""
    st = dict(state) if isinstance(state, dict) else {}
    t = float(now if now is not None else time.time())
    day = time.strftime("%Y-%m-%d", time.localtime(t))
    d = st.get("day") if isinstance(st.get("day"), dict) else {}
    if str(d.get("date") or "") != day:
        d = {"date": day, "per_chat": {}, "total": 0}
    per = d.get("per_chat") if isinstance(d.get("per_chat"), dict) else {}
    per[str(chat_key)] = int(per.get(str(chat_key)) or 0) + 1
    d["per_chat"] = per
    d["total"] = int(d.get("total") or 0) + 1
    st["day"] = d
    last = st.get("last") if isinstance(st.get("last"), dict) else {}
    last[str(chat_key)] = t
    st["last"] = last
    return st


# ── 状态持久化 ──────────────────────────────────────────
#   为什么必须落盘：排程的一天是**跨进程**的（跑几个小时、中途可能重启），
#   纯内存计数一重启就归零 ⇒ "今天已经发了 N 条"会退化成"每次启动都能再发 N 条"。
def _state_path() -> str:
    """排程状态文件（`data/` 下，与其它运行数据同处，便于一起备份/清理）。"""
    import os as _os
    try:
        from .config import DATA_DIR
        return _os.path.join(DATA_DIR, "proactive_state.json")
    except Exception:  # noqa: BLE001
        root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        return _os.path.join(root, "data", "proactive_state.json")


def load_state() -> dict:
    """读排程状态（`{day:{date,per_chat,total}, last:{chat_key:ts}, plan:{date,mins}}`）。

    ⛔ 读不出来 / 文件坏 ⇒ 返回**空 dict**（⇒ 计数归零、当天重新排）。
      为什么不 fail-closed：状态文件没了只该"重排一天"，**不该让功能瘫掉**；
      而"多发几条"这个风险由 `should_fire` 的 `min_gap_min` 与封顶共同兜着。
    """
    import json as _json
    import os as _os
    try:
        p = _state_path()
        if not _os.path.isfile(p):
            return {}
        with open(p, "r", encoding="utf-8") as fh:
            d = _json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        log.debug("读排程状态失败（按空状态处理）：%s", e, exc_info=True)
        return {}


def save_state(state: dict) -> bool:
    """写排程状态（原子替换：先写临时文件再 `persist.replace_into`，断电不留半个坏文件）。"""
    import json as _json
    import os as _os
    from . import persist # noqa: PLC0415 原子换档：撞共享冲突要退避重试
    try:
        p = _state_path()
        d = _os.path.dirname(p)
        if d:
            _os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump(state if isinstance(state, dict) else {}, fh, ensure_ascii=False)
        persist.replace_into(tmp, p)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("写排程状态失败（不影响本轮，最多下次重排）：%s", e, exc_info=True)
        return False


def plan_for_today(state: dict, *, now=None, cfg=None) -> list:
    """取今天的排程时刻（分钟数列表）；**没有就现排并存进 state**。

    返回 `(mins, state)` —— 调用方把 state 落盘（本函数不自己写盘，判据才好验）。
    ⛔ 只排**配置里那个 target 会话**该发的条数（`times_per_day`），不像既有 `proactive` 那样
      在多个群之间随机挑 —— 这是"只对一个明确会话开口"这条闸门的一致口径。
    """
    c = _cfg(cfg)
    st = state if isinstance(state, dict) else {}
    t = float(now if now is not None else time.time())
    day = time.strftime("%Y-%m-%d", time.localtime(t))
    pl = st.get("plan") if isinstance(st.get("plan"), dict) else {}
    if str(pl.get("date") or "") == day:
        try:
            return [int(x) for x in (pl.get("mins") or [])], st
        except (TypeError, ValueError):
            pass
    n = int(_opt(c, "times_per_day", DEFAULTS["times_per_day"]) or 0)
    mins = plan_times(day, n, cfg=cfg)
    st["plan"] = {"date": day, "mins": mins}
    return mins, st


def due_now(state: dict, *, now=None, cfg=None, tol_min: float = 1.0) -> bool:
    """现在到点了没有：当天排程里有一个时刻落在此刻 `±tol_min` 分钟内。

    ⛔ 用"窗口命中"而不是"精确相等"：主循环每 `poll_interval`（默认 3 秒）转一圈，
      排出来的分钟数几乎不可能与轮询瞬间精确对齐 —— 精确相等等于**永远不触发**。
    ⛔ 已在今天用掉的条数不在这里扣（那是 `should_fire` 的 `min_gap_min`/封顶的活），
      否则两处都扣 ⇒ 实际能发的比 `times_per_day` 少。
    """
    mins, _st = plan_for_today(state, now=now, cfg=cfg)
    t = float(now if now is not None else time.time())
    cur = time.localtime(t).tm_hour * 60 + time.localtime(t).tm_min
    for m in mins:
        if abs(cur - int(m)) <= max(0.0, float(tol_min)):
            return True
    return False


def topic_line(mem, chat_key: str, *, cfg=None, now=None) -> str:
    """**该说什么**：从记忆的主动数据源里取一句（没有 ⇒ 空串，不硬凑）。

    优先级：**纪念日到点** > **提前预告** > **该问还没问的** > **分享我这边的事**。
    ⛔ 全都没有 ⇒ 空串 ⇒ `should_fire(has_topic=False)` 会拦下（不硬找话说）。
    ⛔ 不调 LLM：本模块零 token。
    """
    if mem is None:
        return ""
    c = _cfg(cfg)
    try:
        s = mem.proactive_seeds(chat_key, cfg=cfg, now=now) or {}
    except Exception as e:  # noqa: BLE001
        log.debug("取主动数据源失败（按「没话题」处理）：%s", e, exc_info=True)
        return ""
    if _opt(c, "anniversary_enabled", DEFAULTS["anniversary_enabled"]):
        for a in (s.get("anniversaries") or []):
            return "今天是「%s」——主动提一句，别硬念日期" % str(a.get("what") or "").strip()[:60]
        for a in (s.get("upcoming") or []):
            return ("「%s」快到了——想说什么可以提前一点说"
                    % str(a.get("what") or "").strip()[:60])
    for f in (s.get("followups") or []):
        return "有件事该问一下：%s" % str(f.get("what") or "").strip()[:60]
    if _opt(c, "share_enabled", DEFAULTS["share_enabled"]):
        for e in (s.get("events") or []):
            if str(e.get("emotion") or "") == "good":
                return "可以顺口分享一下：%s" % str(e.get("what") or "").strip()[:60]
    return ""


def snapshot(cfg=None) -> dict:
    """给控制台：当前配置与闸门状态（**只读**）。"""
    c = _cfg(cfg)
    return {"enabled": _opt(c, "enabled", False),
            "target": _target_of(c),
            "window": "%s~%s" % (_opt(c, "window_start", DEFAULTS["window_start"]),
                                 _opt(c, "window_end", DEFAULTS["window_end"])),
            "times_per_day": int(_opt(c, "times_per_day", DEFAULTS["times_per_day"]) or 0),
            "cap": {"per_chat": min(int(_opt(c, "max_per_chat", DEFAULTS["max_per_chat"]) or 0),
                                    HARD_MAX_PER_CHAT),
                    "per_day": min(int(_opt(c, "max_per_day", DEFAULTS["max_per_day"]) or 0),
                                   HARD_MAX_PER_DAY)}}
