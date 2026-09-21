# -*- coding: utf-8 -*-
"""节假日问候（第三方 v0.4 对账清单第 13 条）。

**红线（用户口径：只允许被动或显式开启）**——这里刻意分成两个模式：
· `passive`（**默认**）：只往提示词里加一句「今天是 X 节」+ 一条"别硬凑"的规则；
  **一条消息都不会主动发**，问候只在群友本来就在说话时自然带出。
· `active`（**必须显式开启**）：到点主动问候，但受四道收紧——①必须配 `holiday.greet_chats` 白名单（空＝不主动）
  ②每天每个会话最多一次 ③只在 09:00~21:00 这个时段发 ④发送仍走 `sender`（风险闸门照过）。
这样"节日问候"永远不可能变成"定时群发"。

节日表：公历固定节日 + **预置的农历节日日期**（不做农历换算——换算容易错，宁可逐年补表；用户也能在
`data/holidays.json` 里自己加/改，格式 `{"2027-02-06": "春节"}`）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import persist
from .config import DATA_DIR

log = logging.getLogger("persona-morph")

_lock = threading.RLock()

# 本进程内「已经问候过」的 (day, chat_key)：**不管状态有没有写进盘**都算——
# V-R9-19：巡检 20 秒一轮、去重只靠 `holiday_state.json`，写盘一失败就会同一个节日同一个会话
# 每 20 秒重发一次（审计实测一个节日最多 2160 次）。这张内存表就是"本轮不再重试"的标记。
_MEM_GREETED: set = set()

# 公历固定节日（MM-DD）
FIXED = {
    "01-01": "元旦",
    "02-14": "情人节",
    "03-08": "妇女节",
    "05-01": "劳动节",
    "05-04": "青年节",
    "06-01": "儿童节",
    "07-01": "建党节",
    "08-01": "建军节",
    "09-10": "教师节",
    "10-01": "国庆节",
    "12-24": "平安夜",
    "12-25": "圣诞节",
}
# 预置农历/调休节日（按年份写死；只写我们确定的那几年，绝不猜）
PRESET = {
    "2026-02-17": "春节",
    "2026-03-03": "元宵节",
    "2026-04-05": "清明节",
    "2026-06-19": "端午节",
    "2026-09-25": "中秋节",
    "2027-02-06": "春节",
    "2027-06-09": "端午节",
    "2027-09-15": "中秋节",
}
GREET_HOUR_FROM = 9
GREET_HOUR_TO = 21


def custom_path() -> str:
    return os.path.join(DATA_DIR, "holidays.json")


def state_path() -> str:
    return os.path.join(DATA_DIR, "holiday_state.json")


def table() -> dict:
    """合并 内置公历 + 内置预置 + 用户自定义（用户自定义优先）。"""
    out = {}
    for k, v in PRESET.items():
        out[k] = v
    try:
        with open(custom_path(), "r", encoding="utf-8-sig") as f:
            user = json.load(f)
        if isinstance(user, dict):
            for k, v in user.items():
                if str(k).strip() and str(v).strip():
                    out[str(k).strip()] = str(v).strip()
    except Exception:
        pass
    return out


def named(day=None, now=None) -> str | None:
    """这一天是什么节（day 可以是 'YYYY-MM-DD' 或 date/datetime；不传＝今天）。没有就返回 None。"""
    if day is None:
        t = time.localtime() if now is None else time.localtime(now)
        ymd = "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
    elif hasattr(day, "strftime"):
        ymd = day.strftime("%Y-%m-%d")
    else:
        ymd = str(day).strip()
    tb = table()
    if ymd in tb:
        return tb[ymd]
    return FIXED.get(ymd[5:]) if len(ymd) >= 10 else None


def scene_line(now=None) -> str:
    """给提示词用的一句（非节日返回空串）。"""
    n = named(now=now)
    if not n:
        return ""
    return "今天是「%s」。" % n


def greeting(name: str, nickname: str = "") -> str:
    who = ("%s，" % nickname) if nickname else ""
    return "%s%s快乐！" % (who, name)


def load_state() -> dict:
    """读问候状态；**坏档走统一招式 `persist.load_or_quarantine`**（改名 `.bad.<时间戳>` 留证 + 记 warn）。

    V-R9-19 的放大器：坏档原先静默回 `{"greeted": {}}` ⇒ 今天已经问候过的会话全成了"没发过"
    ⇒ 20 秒一轮的巡检接着重发。留证之后至少能一眼看出「是状态丢了，不是没发过」。
    """
    _BAD = object()                      # 哨兵：分得清"读到的东西"与"走的默认值"
    d, ok_overwrite = persist.load_checked(state_path(), _BAD)
    if isinstance(d, dict) and isinstance(d.get("greeted"), dict):
        if not ok_overwrite:
            d["_refuse_overwrite"] = True          # V-R10-23：原档还在 ⇒ 禁止覆盖
        return d
    if d is not _BAD:
        # 形状不对（greeted 不是对象）同样是坏档：留证再回默认值，别让它被下一次写盘盖掉
        kept = persist.quarantine(state_path())
        log.warning("节日问候状态形状不对 ⇒ %s",
                    ("已按坏档留证：%s" % kept) if kept else "**留证失败 ⇒ 原档保持原样、禁止覆盖**")
        out = {"greeted": {}, "updatedAt": 0}
        if not kept and not ok_overwrite:
            out["_refuse_overwrite"] = True
        return out
    out = {"greeted": {}, "updatedAt": 0}
    if not ok_overwrite:
        out["_refuse_overwrite"] = True
    return out


def _save_state(st: dict) -> bool:
    """原子写状态，**返回是否真的落盘**；失败**必须留日志**（V-R9-19）。

    老写法是 `except: pass`（`agent/holidays.py:119` 旧版）——连一行日志都没有，于是
    "状态没写成功 ⇒ 每 20 秒重发一次"这件事**没人看得出来**。现在：走 `persist.atomic_write_json`
    （tmp 名带 pid+随机后缀，V-R9-22），失败记一条 warn，调用方据此在内存里打"已问候"标记。
    V-R10-23：`st` 带 `_refuse_overwrite`（坏档还在原地、留证失败）时**拒绝写**——
    盖掉它就是"今天已问候的会话全变没发过"，接着 20 秒一轮地重发。
    """
    with _lock:
        if st.get("_refuse_overwrite"):
            log.warning("节日问候状态档读不出来且留证失败 ⇒ **拒绝覆盖**（本次不落盘，靠内存标记防重发）：%s",
                        state_path())
            return False
        st["updatedAt"] = int(time.time() * 1000)
        payload = {k: v for k, v in st.items() if not str(k).startswith("_")}
        ok = persist.atomic_write_json(state_path(), payload, indent=1)
        if not ok:
            log.warning("节日问候状态落盘失败 ⇒ 本进程内已记「已问候」，这一轮不再重试、不再重发：%s",
                        state_path())
        return ok


def mark_greeted(day: str, chat_key: str) -> bool:
    """记账（返回状态是否真的落盘）。

    V-R9-19：**无论落盘成不成，先在内存里打标记** —— 巡检是 20 秒一轮，去重只靠这个状态文件，
    写不进去就等于"下一轮再发一次"，一个节日能刷到 2160 次。
    """
    with _lock:
        st = load_state()
        st.setdefault("greeted", {}).setdefault(str(day), [])
        if chat_key not in st["greeted"][str(day)]:
            st["greeted"][str(day)].append(chat_key)
        # 只留最近 7 天，别让状态文件无限长
        keys = sorted(st["greeted"].keys())
        for k in keys[:-7]:
            st["greeted"].pop(k, None)
        _MEM_GREETED.add((str(day), str(chat_key)))      # 先打内存标记，再看落盘成不成
        return _save_state(st)


def due_greetings(cfg: dict, groups: list, now=None) -> list:
    """`active` 模式下该发哪些问候。返回 [{chat_key, name, text}]（可能为空）。

    四道收紧都在这里：模式=active · 白名单非空 · 时段 09:00~21:00 · 当天该会话没发过。
    """
    h = (cfg.get("holiday") or {})
    if str(h.get("mode") or "passive") != "active":
        return []
    names = [str(x).strip() for x in (h.get("greet_chats") or []) if str(x).strip()]
    if not names:
        return []                      # 没白名单 ⇒ 一律不主动（防"节日变群发"）
    t = time.localtime() if now is None else time.localtime(now)
    day = "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
    fest = named(day=day)
    if not fest:
        return []
    try:
        hour_from = int(h.get("greet_hour") if h.get("greet_hour") is not None else GREET_HOUR_FROM)
    except (TypeError, ValueError):
        hour_from = GREET_HOUR_FROM
    if not (hour_from <= t.tm_hour < GREET_HOUR_TO):
        return []
    st = load_state()
    done = set(st.get("greeted", {}).get(day) or [])
    with _lock:
        # V-R9-19：本进程内发过、但状态**没写进盘**的那些也要算"已问候"，
        # 否则写盘一失败就变成每 20 秒重发一次（一个节日最多 2160 次）。
        done |= {k for (d0, k) in _MEM_GREETED if d0 == day}
    out = []
    for g in (groups or []):
        nm = str(g.get("name") or "")
        if nm not in names:
            continue
        chat_key = "group:" + str(g.get("wxid") or g.get("id") or "")
        if chat_key in done:
            continue
        out.append({"chat_key": chat_key, "name": nm, "day": day, "festival": fest,
                    "text": greeting(fest)})
    return out


def snapshot(now=None) -> dict:
    from .config import get_config
    h = (get_config().get("holiday") or {})
    t = time.localtime() if now is None else time.localtime(now)
    day = "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
    st = load_state()
    return {"mode": str(h.get("mode") or "passive"), "today": named(day=day), "day": day,
            "greet_chats": [str(x) for x in (h.get("greet_chats") or [])],
            "greet_hour": h.get("greet_hour"),
            "greeted_today": list(st.get("greeted", {}).get(day) or []),
            "custom_table": len(table())}
