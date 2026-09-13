# -*- coding: utf-8 -*-
"""风险闸门：决定「这条消息现在能不能发」。

口径（`AGENTS.md §3.2`）：**闸门只管行为特征**——频率、时段、重复度、内容可疑度；
判据里**不出现**"是否后台""是否动鼠标"（那是实现手段，不是风险）。

四层（L0 最严 → L3 只记录）：
    L0  停机开关        `risk.paused`（控制台大红开关；`escalate_after>0` 时连续被拦会自动升级）
    L1  可选节奏旋钮   每分钟/每小时/每天总量、夜间静默 —— **默认全部 0/关**，只给"想自己掐"的用户
    L2  内容与任务层   ★**现在的重心**：群发特征（同一内容短时间发给多个不同会话）· 同会话重复内容
    L3  内容可疑度     链接过多 / 命中观察词 ⇒ **放行但记录**（控制台可见）；禁止词 ⇒ 拦

**为什么节奏默认不限（用户 2026-09-13 定，原话）**：
    "L1 全局节奏…L2 会话节奏没必要限制得这么死，只要限制发送的内容，或者某些任务不做就行，
     就是之前说的那些红线。只在内容层上做筛选。"
    "这个全局节奏、会话节奏应该可以交给用户自定义，让他们自己把控账号的风险。"
    "到时候我会在介绍里面声明，让他们自己把控账号，账号封了也别怪我。"
⇒ 我们的默认把关点是**内容 / 任务**（营销群发、群控加粉这类能力本来就不提供），
  节奏类阈值全部作为**可配置旋钮**（config.json 的 `risk.*`，控制台面板待接）。

**误报来源与防误报规则（用户必问的三件套之一，写在这里备查）**
    · 系统休眠/挂起后恢复、系统时间被改 → 窗口统计用 `time.time()` 单调推进，遇到
      "未来时间戳"（超前 > 60s，说明时钟被回拨）**丢弃该条**而不是永久卡住闸门。
    · 多开实例/多进程同时发送 → 状态文件里记 `owner_pid`；文件被别人改过时**重新载入**
      再判定（不缓存太久）；同一进程内用 RLock 串行。
    · 人工从控制台测试发送被计入配额 → 计数**只记机器人出站路径**（sender 调用
      `note_sent`），控制台"测试发送"走 `check(..., manual=True)` 只记录不计数。
    · 夜间静默误伤白天 → 静默时段默认**关闭**（`quiet_hours: []`），启用时按本机本地时间、
      半开区间 `[start, end)`，跨零点写成 `[22, 7]` 形式。
    · 把"冷场后的一次回复"当成刷屏 → 会话间隔只约束**同一会话的连续发送**，且
      `min_gap_seconds` 默认 3 秒（远小于真人对话节奏）。

**用户可见面（第二件套）**：①控制台顶部状态条（`snapshot()` 供控制台渲染）②被拦时
返回给模型/控制台的中文原因（`Verdict.message`）③`data/risk_events.jsonl` 逐条留痕
④**绝不往微信侧发任何停机/拦截提示**（只在本人机器与控制台出现）。

**弹窗/提示文案（第三件套，逐字）**：
    停机开关：　「已暂停所有自动发送」/「原因：连续 %d 次被风险闸门拦下」/「恢复发送」
    频率拦截：　「发送过快，已拦下这条（每分钟上限 %d 条），约 %d 秒后可再发」
    夜间静默：　「现在是静默时段（%02d:00-%02d:00），已拦下这条；可在控制台『风险闸门』里关闭」
    重复内容：　「和刚发过的内容几乎一样，已拦下（防刷屏）」
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from .config import DATA_DIR, get_config

log = logging.getLogger("persona-morph")

STATE_PATH = os.path.join(DATA_DIR, "risk_state.json")
EVENT_PATH = os.path.join(DATA_DIR, "risk_events.jsonl")

# 闸门自己的默认值（config.json 的 risk 段缺哪项就用这里的；不在 config 里也能跑）
#
# ⚠️ 口径（用户 2026-09-13 定，**改变闸门重心**）：原话——
#    "L1 全局节奏…L2 会话节奏没必要限制得这么死，只要限制发送的内容，或者某些任务不做就行，
#     就是之前说的那些红线。只在内容层上做筛选。"
#    "这个全局节奏、会话节奏应该可以交给用户自定义，让他们自己把控账号的风险。"
#    "到时候我会在介绍里面声明，让他们自己把控账号，账号封了也别怪我。"
# ⇒ **频率/节奏类阈值默认全部为 0（＝不限）**，只作为"用户想自己掐时"的旋钮（config.json / 控制台）；
#   闸门默认真正把关的是**内容层与任务层**：群发特征（同一内容短时间发给多个会话）、链接堆积、
#   禁止词（用户自己填）；"某些任务不做"落在功能层（群控/加粉/营销群发这些能力我们本来就不提供）。
DEFAULTS = {
    "enabled": True,
    "paused": False,
    "per_minute": 0,             # 0 = 不限（节奏交给用户自己把控账号风险）
    "per_hour": 0,
    "per_day": 0,
    "per_chat_per_hour": 0,
    "min_gap_seconds": 0,
    "quiet_hours": [],           # 例：[22, 7]；空 = 不启用夜间静默（默认关）
    "max_links": 3,              # 单条链接超限 ⇒ 记录（L3），不拦
    "watch_keywords": [],        # 命中只记录（L3）
    "block_keywords": [],        # 命中直接拦（默认空，用户按自己的红线填）
    "broadcast_chats": 3,        # 同一内容在窗口内发给 ≥N 个不同会话 ⇒ 判为群发（任务层红线）
    "broadcast_window_seconds": 300,
    "escalate_after": 0,         # 0 = 不自动暂停（频率不限时"连续被拦"没有意义）
    "dup_window_seconds": 120,   # 重复内容判定窗口
    "dup_min_len": 8,            # 多短算"同一句"
}

_LINK_RE = re.compile(r"https?://|www\.", re.I)


class RiskBlocked(RuntimeError):
    """被闸门拦下（带用户可见原因，调用方直接把 message 转给模型/控制台）。"""

    def __init__(self, verdict):
        super().__init__(verdict.message)
        self.verdict = verdict


class Verdict(object):
    __slots__ = ("allowed", "level", "code", "message", "retry_after", "detail")

    def __init__(self, allowed, level, code, message="", retry_after=0, detail=None):
        self.allowed = allowed
        self.level = level          # L0 / L1 / L2 / L3
        self.code = code            # 机器可读：paused / per_minute / quiet_hours / dup / link ...
        self.message = message      # 中文用户可见原因
        self.retry_after = retry_after
        self.detail = detail or {}

    def to_dict(self):
        return {"allowed": self.allowed, "level": self.level, "code": self.code,
                "message": self.message, "retry_after": self.retry_after,
                "detail": self.detail}

    def __repr__(self):
        return "<Verdict %s %s %s>" % ("OK" if self.allowed else "BLOCK",
                                       self.level, self.code)


def _cfg() -> dict:
    raw = {}
    try:
        raw = dict(get_config().get("risk") or {})
    except Exception:
        raw = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in raw.items() if v is not None})
    return out


class RiskGate(object):
    """有状态的闸门：窗口计数 + 停机开关 + 事件留痕（状态原子落盘）。"""

    def __init__(self, path: str = STATE_PATH, event_path: str = EVENT_PATH):
        self.path = path
        self.event_path = event_path
        self._lock = threading.RLock()
        self._st = {"paused": False, "paused_reason": "", "blocks": 0,
                    "min": [], "hour": [], "day": [], "day_key": "",
                    "chats": {}, "events": [], "recent": []}
        self._load()

    # ── 状态读写 ────────────────────────────────────────────────────────
    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    d = json.load(f) or {}
                if isinstance(d, dict):
                    self._st.update({k: v for k, v in d.items() if k in self._st})
        except Exception as e:
            log.warning("风险闸门状态读取失败（按空状态继续）：%s", e)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._st, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except Exception as e:
            log.warning("风险闸门状态落盘失败：%s", e)

    def _event(self, verdict, chat_key, text=""):
        rec = {"ts": int(time.time() * 1000), "level": verdict.level, "code": verdict.code,
               "chat": str(chat_key or "")[:60], "allowed": bool(verdict.allowed),
               "msg": (verdict.message or "")[:120], "text": str(text or "")[:60]}
        self._st["events"] = (self._st.get("events") or [])[-49:] + [rec]
        try:
            os.makedirs(os.path.dirname(self.event_path), exist_ok=True)
            with open(self.event_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── 停机开关 ────────────────────────────────────────────────────────
    def pause(self, reason: str = ""):
        with self._lock:
            self._st["paused"] = True
            self._st["paused_reason"] = str(reason or "")[:120]
            self._save()

    def resume(self):
        with self._lock:
            self._st["paused"] = False
            self._st["paused_reason"] = ""
            self._st["blocks"] = 0
            self._save()

    def is_paused(self) -> bool:
        return bool(self._st.get("paused"))

    # ── 主判定 ──────────────────────────────────────────────────────────
    def check(self, chat_key: str, text: str = "", now: float = None,
              manual: bool = False) -> Verdict:
        cfg = _cfg()
        now = float(now if now is not None else time.time())
        text = str(text or "")

        def verdict(allowed, level, code, message="", retry_after=0, detail=None):
            v = Verdict(allowed, level, code, message, retry_after, detail)
            self._event(v, chat_key, text)
            if not allowed and not manual:
                self._st["blocks"] = int(self._st.get("blocks") or 0) + 1
                if cfg.get("escalate_after") and self._st["blocks"] >= int(cfg["escalate_after"]):
                    self._st["paused"] = True
                    self._st["paused_reason"] = "连续 %d 次被风险闸门拦下" % self._st["blocks"]
                    log.warning("风险闸门：连续被拦 %d 次，已自动暂停自动发送", self._st["blocks"])
            elif allowed:
                self._st["blocks"] = 0
            self._save()
            return v

        if not cfg.get("enabled", True):
            return Verdict(True, "L3", "disabled", "")

        with self._lock:
            # L0 停机开关
            if self._st.get("paused"):
                why = self._st.get("paused_reason") or "手动暂停"
                return verdict(False, "L0", "paused",
                               "已暂停所有自动发送（原因：%s）；在控制台『风险闸门』里点『恢复发送』即可" % why)

            self._prune(cfg, now)

            # L1 夜间静默
            qh = cfg.get("quiet_hours") or []
            if isinstance(qh, (list, tuple)) and len(qh) == 2:
                try:
                    hs, he = int(qh[0]), int(qh[1])
                    h = time.localtime(now).tm_hour
                    in_quiet = (hs <= h < he) if hs < he else (h >= hs or h < he)
                    if in_quiet:
                        return verdict(False, "L1", "quiet_hours",
                                       "现在是静默时段（%02d:00-%02d:00），已拦下这条；"
                                       "可在控制台『风险闸门』里关闭静默" % (hs, he))
                except Exception:
                    pass

            # L1 全局总量
            for key, cap, win, label in (("min", "per_minute", 60, "每分钟"),
                                         ("hour", "per_hour", 3600, "每小时"),
                                         ("day", "per_day", 86400, "每天")):
                cap = int(cfg.get(cap) or 0)
                if cap > 0 and len(self._st[key]) >= cap:
                    oldest = min(self._st[key]) if self._st[key] else now
                    return verdict(False, "L1", "global_" + key,
                                   "发送过快，已拦下这条（%s上限 %d 条），约 %d 秒后可再发"
                                   % (label, cap, max(1, int(oldest + win - now))),
                                   retry_after=max(1, int(oldest + win - now)))

            # L2 会话节奏
            ch = self._st["chats"].setdefault(str(chat_key), {"hour": [], "last_ts": 0.0, "last_texts": []})
            cap_ch = int(cfg.get("per_chat_per_hour") or 0)
            if cap_ch > 0 and len(ch.get("hour") or []) >= cap_ch:
                return verdict(False, "L2", "chat_hour",
                               "这个会话一小时内已经发了 %d 条，先缓一缓" % cap_ch)
            gap = float(cfg.get("min_gap_seconds") or 0)
            if gap > 0 and ch.get("last_ts") and (now - float(ch["last_ts"])) < gap:
                wait = int(gap - (now - float(ch["last_ts"]))) + 1
                return verdict(False, "L2", "chat_gap",
                               "同一会话发送间隔太短（最小 %s 秒），约 %d 秒后再发" % (gap, wait),
                               retry_after=wait)

            # L2 重复内容
            dup_win = float(cfg.get("dup_window_seconds") or 0)
            dup_min = int(cfg.get("dup_min_len") or 8)
            norm = re.sub(r"\s+", "", text)[:200]
            if dup_win > 0 and len(norm) >= dup_min:
                for prev, ts in (ch.get("last_texts") or []):
                    if prev == norm and (now - float(ts)) < dup_win:
                        return verdict(False, "L2", "duplicate",
                                       "和刚发过的内容几乎一样，已拦下（防刷屏）")

            # ★ 任务层红线（闸门现在的重心）：群发特征 —— 同一内容短时间发给多个不同会话
            bc = int(cfg.get("broadcast_chats") or 0)
            bw = float(cfg.get("broadcast_window_seconds") or 0)
            if bc > 0 and bw > 0 and len(norm) >= max(4, dup_min):
                peers = {str(c) for s, c, ts in (self._st.get("recent") or [])
                         if s == norm and (now - float(ts)) < bw}
                peers.add(str(chat_key))
                if len(peers) >= bc:
                    return verdict(False, "L2", "broadcast",
                                   "同一内容将发给 %d 个不同会话（%d 秒内 ≥%d 个即判群发），已拦下"
                                   "——营销群发是我们不做的任务" % (len(peers), int(bw), bc),
                                   detail={"chats": sorted(peers)[:6]})

            # L3 内容可疑度（放行，只记录）
            links = len(_LINK_RE.findall(text))
            max_links = int(cfg.get("max_links") or 0)
            for kw in (cfg.get("block_keywords") or []):
                if kw and str(kw) in text:
                    return verdict(False, "L3", "block_keyword", "内容命中禁止词『%s』，已拦下" % kw)
            if max_links and links > max_links:
                return verdict(True, "L3", "many_links",
                               "这条里有 %d 个链接（上限 %d），已放行但记录在案" % (links, max_links),
                               detail={"links": links})
            for kw in (cfg.get("watch_keywords") or []):
                if kw and str(kw) in text:
                    return verdict(True, "L3", "watch_keyword",
                                   "内容命中观察词『%s』，已放行但记录在案" % kw,
                                   detail={"keyword": kw})
            return verdict(True, "L2", "ok")

    # ── 发送成功后记账（只记机器人出站路径）────────────────────────────
    def note_sent(self, chat_key: str, text: str = "", now: float = None):
        cfg = _cfg()
        now = float(now if now is not None else time.time())
        with self._lock:
            self._prune(cfg, now)
            self._st["min"].append(now)
            self._st["hour"].append(now)
            self._st["day"].append(now)
            ch = self._st["chats"].setdefault(str(chat_key), {"hour": [], "last_ts": 0.0,
                                                              "last_texts": []})
            ch["hour"].append(now)
            ch["last_ts"] = now
            norm = re.sub(r"\s+", "", str(text or ""))[:200]
            if norm:
                ch["last_texts"] = (ch.get("last_texts") or [])[-4:] + [[norm, now]]
                # 跨会话留一条"最近发过什么"（群发特征检测用；不记明文全文，只留归一化片段）
                self._st["recent"] = (self._st.get("recent") or [])[-49:] + [[norm, str(chat_key), now]]
            self._save()

    def _prune(self, cfg: dict, now: float):
        """清掉过期窗口；**未来时间戳**（时钟被回拨）直接丢弃，别让闸门永久卡住。"""
        def keep(ts, win):
            try:
                ts = float(ts)
            except Exception:
                return False
            if ts - now > 60:
                return False
            return (now - ts) < win
        self._st["min"] = [t for t in (self._st.get("min") or []) if keep(t, 60)]
        self._st["hour"] = [t for t in (self._st.get("hour") or []) if keep(t, 3600)]
        day_key = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._st.get("day_key") != day_key:
            self._st["day_key"] = day_key
            self._st["day"] = []
        self._st["day"] = [t for t in (self._st.get("day") or []) if keep(t, 86400)]
        for ch in (self._st.get("chats") or {}).values():
            ch["hour"] = [t for t in (ch.get("hour") or []) if keep(t, 3600)]
            ch["last_texts"] = [[s, t] for s, t in (ch.get("last_texts") or [])
                                if keep(t, max(60.0, float(cfg.get("dup_window_seconds") or 120)))]
            # last_ts 是标量，同样要挡「未来时间戳」（系统时钟被回拨时会把闸门永久卡死）
            try:
                if float(ch.get("last_ts") or 0) - now > 60:
                    ch["last_ts"] = 0.0
            except Exception:
                ch["last_ts"] = 0.0
        # 跨会话近期发送记录（群发特征检测）
        self._st["recent"] = [[s, c, t] for s, c, t in (self._st.get("recent") or [])
                              if keep(t, max(60.0, float(cfg.get("broadcast_window_seconds") or 300)))]

    # ── 给控制台 ────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        cfg = _cfg()
        with self._lock:
            now = time.time()
            self._prune(cfg, now)
            return {
                "enabled": bool(cfg.get("enabled", True)),
                "paused": bool(self._st.get("paused")),
                "paused_reason": self._st.get("paused_reason") or "",
                "blocks": int(self._st.get("blocks") or 0),
                "minute": len(self._st["min"]), "minute_cap": int(cfg.get("per_minute") or 0),
                "hour": len(self._st["hour"]), "hour_cap": int(cfg.get("per_hour") or 0),
                "day": len(self._st["day"]), "day_cap": int(cfg.get("per_day") or 0),
                "quiet_hours": list(cfg.get("quiet_hours") or []),
                "events": list(self._st.get("events") or [])[-10:],
            }


_gate = None
_gate_lock = threading.Lock()


def gate() -> RiskGate:
    """全局单例（懒加载；测试可传 path 造独立实例）。"""
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                _gate = RiskGate()
    return _gate


def check(chat_key: str, text: str = "", **kw) -> Verdict:
    return gate().check(chat_key, text, **kw)


def note_sent(chat_key: str, text: str = "", **kw):
    gate().note_sent(chat_key, text, **kw)


def snapshot() -> dict:
    return gate().snapshot()


def pause(reason: str = ""):
    gate().pause(reason)


def resume():
    gate().resume()
