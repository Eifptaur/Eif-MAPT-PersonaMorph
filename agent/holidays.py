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
import os
import threading
import time

from .config import DATA_DIR

_lock = threading.RLock()

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
    try:
        with open(state_path(), "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("greeted"), dict):
            return d
    except Exception:
        pass
    return {"greeted": {}, "updatedAt": 0}


def _save_state(st: dict) -> None:
    with _lock:
        try:
            os.makedirs(os.path.dirname(state_path()), exist_ok=True)
            st["updatedAt"] = int(time.time() * 1000)
            tmp = state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False, indent=1)
            os.replace(tmp, state_path())
        except Exception:
            pass


def mark_greeted(day: str, chat_key: str) -> None:
    with _lock:
        st = load_state()
        st.setdefault("greeted", {}).setdefault(str(day), [])
        if chat_key not in st["greeted"][str(day)]:
            st["greeted"][str(day)].append(chat_key)
        # 只留最近 7 天，别让状态文件无限长
        keys = sorted(st["greeted"].keys())
        for k in keys[:-7]:
            st["greeted"].pop(k, None)
        _save_state(st)


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
