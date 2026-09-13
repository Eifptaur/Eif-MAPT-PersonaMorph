# -*- coding: utf-8 -*-
"""响应等级三件（第三方 v0.4 对账清单第 15/16/18 条）。

· **固定 4 档**（第 15 条）：档位就是 1/2/3/4 四个离散值（1=只回艾特 · 2=+关键词 · 3=+随机 · 4=全响应）；
  `store.tier_mode="fixed"` 时**滑条不再参与**（滑条那种连续微调属旧行为，想用就把模式切回 `slider`）。
· **峰谷映射**（第 16 条）：`store.tier_schedule` 给「时段 → 档位」的映射表，命中哪个时段就用哪个档位；
  **档位 0 = 该时段完全不回应（静默）**；跨午夜窗口（如 22:00-02:00）也支持；没命中任何窗口就回落到全局档位。
· **指令禁言**（第 18 条）：群友 **@机器人 + 指令**（`禁言` / `禁言 30` / `解除禁言`）⇒ 该会话**档位固定降到 1 档**
  （不是完全不说话，符合"等级固定 1 档"的原意），到期自动恢复。**默认谁都不能下这个指令**（`store.tier_cmd_admins`
  留空即可），否则群里任何一个人喊一句「@机器人 闭嘴」就能把机器人按住 —— 这是刻意的 fail-closed。

三件都在**同一处生效**：`agent/prompt.py::resolve_context_tier()` 是"要不要回应、带多少条历史"的唯一闸门，
本模块只提供判定所需的状态与查表。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

from .config import DATA_DIR

_lock = threading.RLock()

TIER_MIN, TIER_MAX = 1, 4
MUTE_DEFAULT_MIN = 30
MUTE_MAX_MIN = 1440           # 最长 24 小时（再长就是配置错误，夹断并如实说明）

_MUTE_WORDS = ("禁言", "闭嘴", "闭麦", "静一静", "安静")
_UNMUTE_WORDS = ("解除禁言", "取消禁言", "解除静默", "解禁", "恢复")
_TIME_RE = re.compile(r"^(\d{1,2})[:：](\d{2})$")
_AT_PREFIX_RE = re.compile(r"^@\S+\s*")


def state_path() -> str:
    return os.path.join(DATA_DIR, "tier_control.json")


def _now_ms(now=None) -> int:
    if now is None:
        return int(time.time() * 1000)
    if isinstance(now, (int, float)):
        return int(now) if now > 1e11 else int(now * 1000)
    return int(now.timestamp() * 1000)


# ── 状态落盘（禁言表）────────────────────────────────────────────────────
def load() -> dict:
    try:
        with open(state_path(), "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("muted"), dict):
            return d
    except Exception:
        pass
    return {"muted": {}, "updatedAt": 0}


def _save(state: dict) -> None:
    with _lock:
        try:
            os.makedirs(os.path.dirname(state_path()), exist_ok=True)
            tmp = state_path() + ".tmp"
            state["updatedAt"] = int(time.time() * 1000)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, state_path())
        except Exception:
            pass


def is_muted(chat_key: str, now=None) -> dict | None:
    """该会话现在是否处于指令禁言；返回 {until, left_min, by, minutes} 或 None（含已过期）。"""
    st = load()
    rec = (st.get("muted") or {}).get(str(chat_key))
    if not isinstance(rec, dict):
        return None
    now_ms = _now_ms(now)
    until = int(rec.get("until") or 0)
    if until <= now_ms:
        # 过期清理：只在磁盘里真有这条记录时写一次（读操作不制造无谓写盘）
        with _lock:
            cur = load()
            if str(chat_key) in (cur.get("muted") or {}):
                cur["muted"].pop(str(chat_key), None)
                _save(cur)
        return None
    return {"until": until, "left_min": max(0, int((until - now_ms) / 60000)),
            "by": str(rec.get("by") or ""), "minutes": int(rec.get("minutes") or 0)}


def mute(chat_key: str, minutes=None, by: str = "", note: str = "", now=None) -> dict:
    try:
        mins = int(minutes) if minutes not in (None, "") else MUTE_DEFAULT_MIN
    except (TypeError, ValueError):
        mins = MUTE_DEFAULT_MIN
    clamped = False
    if mins < 1:
        mins, clamped = 1, True
    if mins > MUTE_MAX_MIN:
        mins, clamped = MUTE_MAX_MIN, True
    until = _now_ms(now) + mins * 60000
    with _lock:
        st = load()
        st.setdefault("muted", {})[str(chat_key)] = {"until": until, "minutes": mins,
                                                     "by": str(by or ""), "note": str(note or "")[:80]}
        _save(st)
    return {"chat": str(chat_key), "until": until, "minutes": mins, "by": str(by or ""), "clamped": clamped}


def unmute(chat_key: str) -> bool:
    with _lock:
        st = load()
        had = str(chat_key) in (st.get("muted") or {})
        st.setdefault("muted", {}).pop(str(chat_key), None)
        _save(st)
    return had


# ── 峰谷映射（时段 → 档位）───────────────────────────────────────────────
def _parse_time(s):
    m = _TIME_RE.match(str(s or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def parse_table(table) -> list:
    """把配置里的映射表规整成 [{"from","to","tier","note"}]；坏行直接跳过（不吃异常）。"""
    if isinstance(table, str):
        try:
            table = json.loads(table) if table.strip() else []
        except Exception:
            return []
    out = []
    for row in (table or []):
        if not isinstance(row, dict):
            continue
        a, b = _parse_time(row.get("from")), _parse_time(row.get("to"))
        if a is None or b is None or a == b:
            continue                      # 时间非法或区间为空：跳过（宁可不生效，也不乱改档位）
        try:
            tier = int(row.get("tier"))
        except (TypeError, ValueError):
            continue
        if tier < 0 or tier > TIER_MAX:
            continue
        out.append({"from": a, "to": b, "tier": tier, "note": str(row.get("note") or "")[:40],
                    "from_text": str(row.get("from")), "to_text": str(row.get("to"))})
    return out


def _cur_min(now=None) -> int:
    """把 now 归一成"当天第几分钟"：支持 None（现在）/ struct_time / datetime / 时间戳。"""
    if now is None:
        t = time.localtime()
    elif hasattr(now, "tm_hour"):
        t = now
    elif hasattr(now, "hour") and hasattr(now, "minute"):
        return int(now.hour) * 60 + int(now.minute)
    elif isinstance(now, (int, float)):
        t = time.localtime(now if now < 1e11 else now / 1000.0)
    else:
        t = time.localtime()
    return int(t.tm_hour) * 60 + int(t.tm_min)


def scheduled_tier(now=None, cfg=None) -> dict | None:
    """当前时段命中哪一档；没开/没命中返回 None。**按表内顺序取第一个命中的窗口**。"""
    if cfg is None:
        from .config import get_config
        cfg = get_config()
    sc = (cfg.get("store") or {}).get("tier_schedule") or {}
    if not sc.get("enabled"):
        return None
    table = parse_table(sc.get("table"))
    if not table:
        return None
    cur = _cur_min(now)
    for row in table:
        inside = (row["from"] <= cur < row["to"]) if row["from"] < row["to"] else (cur >= row["from"] or cur < row["to"])
        if inside:
            return {"tier": row["tier"], "window": "%s-%s" % (row["from_text"], row["to_text"]),
                    "note": row["note"]}
    return None


# ── 指令禁言（@机器人 + 指令）───────────────────────────────────────────
def parse_command(text) -> dict | None:
    """识别指令本身（调用方负责确认「确实 @ 了机器人」与「发送者在白名单里」）。"""
    t = _AT_PREFIX_RE.sub("", str(text or "").strip()).strip()
    if not t:
        return None
    for w in _UNMUTE_WORDS:
        if t == w or t == w + "。":
            return {"action": "unmute"}
    for w in _MUTE_WORDS:
        if t == w or t == w + "。":
            return {"action": "mute", "minutes": MUTE_DEFAULT_MIN}
        m = re.match(r"^%s\s*(\d{1,5})\s*(分钟|分|min|m)?[。]?$" % re.escape(w), t)
        if m:
            return {"action": "mute", "minutes": int(m.group(1))}
    return None


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).casefold()


def is_admin(admins, name="", wxid="") -> bool:
    """白名单匹配（昵称或 wxid，忽略大小写与空格）。**白名单为空 ⇒ 任何人都不算管理员**（fail-closed）。"""
    pool = [_norm(a) for a in (admins or []) if str(a).strip()]
    if not pool:
        return False
    return _norm(name) in pool or _norm(wxid) in pool


def handle_command(chat_key: str, entry: dict, at_me: bool, cfg=None) -> dict:
    """处理一条可能是指令的消息。返回 {"handled": bool, ...}；handled=True 表示**不要触发回复**。"""
    if not at_me:
        return {"handled": False}
    cmd = parse_command((entry or {}).get("text"))
    if not cmd:
        return {"handled": False}
    if cfg is None:
        from .config import get_config
        cfg = get_config()
    admins = (cfg.get("store") or {}).get("tier_cmd_admins") or []
    who = str((entry or {}).get("sender_name") or "")
    wid = str((entry or {}).get("sender_id") or "")
    if not is_admin(admins, who, wid):
        return {"handled": True, "action": "denied", "who": who or wid,
                "note": "不在 store.tier_cmd_admins 白名单里，指令未生效"}
    if cmd["action"] == "unmute":
        had = unmute(chat_key)
        return {"handled": True, "action": "unmute", "who": who or wid, "note": "已恢复" if had else "本来就没禁言"}
    res = mute(chat_key, cmd.get("minutes"), by=who or wid, note="群内指令")
    return {"handled": True, "action": "mute", "who": who or wid, "minutes": res["minutes"],
            "until": res["until"], "clamped": res["clamped"],
            "note": "已禁言 %d 分钟" % res["minutes"]}


def snapshot(now=None) -> dict:
    """给控制台/`/api/status` 的现场读数。"""
    from .config import get_config
    cfg = get_config()
    sc = (cfg.get("store") or {}).get("tier_schedule") or {}
    st = load()
    now_ms = _now_ms(now)
    active = {}
    for k, v in (st.get("muted") or {}).items():
        try:
            if int((v or {}).get("until") or 0) > now_ms:
                active[k] = {"until": int(v.get("until") or 0), "by": str(v.get("by") or ""),
                             "left_min": max(0, int((int(v.get("until") or 0) - now_ms) / 60000))}
        except Exception:
            continue
    sch = scheduled_tier(now=now, cfg=cfg) if sc.get("enabled") else None
    return {"mode": str((cfg.get("store") or {}).get("tier_mode") or "fixed"),
            "admins": [str(a) for a in ((cfg.get("store") or {}).get("tier_cmd_admins") or [])],
            "schedule": {"enabled": bool(sc.get("enabled")), "rows": len(parse_table(sc.get("table"))),
                         "now": sch},
            "muted": active}
