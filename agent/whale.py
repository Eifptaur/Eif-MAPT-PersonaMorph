# -*- coding: utf-8 -*-
"""DSH 小鲸鱼余额挂件（DeepSeek-Balance-Whale-Widget）适配层。

原版是 DSH 插件（whale-widget/lib-index.js 宿主侧走 Node/DSH 事件系统），
这里把它整体迁移进来：
  - 浏览器侧脚本：whale-widget/client/widget.js（原样提取自原项目 WIDGET_JS，零改动）
  - 服务端：本模块按原项目的 /dsh-whale/* 接口约定用 Python 重新实现，
    挂到 wx-agent Web 控制台（agent/webui.py）下。

接口约定（与原版一致）：
  GET  /dsh-whale/widget.js       浏览器侧脚本（按 token 注入，webui 处理）
  GET  /dsh-whale/image.png       小鲸鱼本体图（assets/DSniang1.png）
  GET  /dsh-whale/rua.gif         随机台词 gif（缺失时前端静默降级）
  GET  /dsh-whale/sound/(press|release).mp3?set=duck|fx1  按压/松手音效
  GET  /dsh-whale/balance.json    {ok,totalBalance,currency,todayUsage,isPeak}
  GET  /dsh-whale/size.json       前端配置（scale/音量/模式/开关…）
  PUT  /dsh-whale/size.json       保存前端配置（webui 的 do_POST 转调）
  GET  /dsh-whale/last-turn.json  {ok,seq,turn,amount,tokens}，seq 递增

与 DSH 版的两处适配：
  1) 「今日已用」不再依赖平台令牌/余额差值，直接用本机每轮 API 调用的真实
     usage 按峰谷定价折算（调用发生时才记账，机器人不跑就不计费，比余额差值更准）。
  2) 「每轮对话消耗」= 一次唤醒里所有 LLM 调用的总成本（会话结束时结算），
     替代 DSH 的 turn/end 事件。
"""
from __future__ import annotations

import json
import os
import threading
import time

from .llm import query_balance

# ── 峰谷定价表（与原版 lib/index.js 顶端一致；DeepSeek 调价时改这里）────────
# 单位：元 / 百万 token。格式 [空闲时段价, 高峰时段价]。
# 高峰时段：北京时间工作日 9:00–12:00 与 14:00–18:00；2026-08-23 起周末全天谷价。
PEAK_HOURS = [(9, 12), (14, 18)]
BASE_PRICE = {"hit": [0.05, 0.1], "miss": [1.5, 3.0], "out": [4.5, 9.0]}
PRO_PRICE = {"hit": [0.15, 0.3], "miss": [4.5, 9.0], "out": [13.5, 27.0]}
PRICING = {
    "deepseek-v4-flash-vision-exp": BASE_PRICE,
    "deepseek-v4-flash": BASE_PRICE,
    "deepseek-v4-pro": PRO_PRICE,
    "deepseek-chat": BASE_PRICE,
    "deepseek-reasoner": BASE_PRICE,
    "_default": BASE_PRICE,
}
_VALUE = "deepseek-v4-pro"  # 映射时作为子串匹配，仅作占位
# 北京时间 2026-08-23 00:00 的 epoch 秒（周末谷价生效分界）
import datetime as _dt
_WEEKEND_VALLEY_FROM_SEC = _dt.datetime(2026, 8, 23, tzinfo=_dt.timezone(_dt.timedelta(hours=8))).timestamp()
# 本项目与 DSH 环境的时区不同，直接用本地时间 +8 换算北京日历日
_BJ_OFFSET = 8 * 3600


def price_for(model: str) -> dict:
    m = str(model or "").lower()
    for key in ("deepseek-v4-pro", "deepseek-v4-flash-vision-exp", "deepseek-v4-flash",
                "deepseek-chat", "deepseek-reasoner"):
        if key in m:
            return PRO_PRICE if key == "deepseek-v4-pro" else BASE_PRICE
    return BASE_PRICE


def is_peak_time(time_sec: float) -> bool:
    """按北京时间判断是否高峰时段。"""
    try:
        n = float(time_sec)
    except (TypeError, ValueError):
        return False
    bj = time.gmtime(n + _BJ_OFFSET)  # gmtime+偏移 = 北京时间日历（UTC 读法）
    if n >= _WEEKEND_VALLEY_FROM_SEC:
        if bj.tm_wday in (5, 6):  # 周六/周日（gmtime 的 tm_wday: 5=六 6=日）
            return False
    hour = bj.tm_hour
    for start, end in PEAK_HOURS:
        if start <= hour < end:
            return True
    return False


def _today_key() -> str:
    return time.strftime("%Y-%m-%d")


class WhaleWidget:
    """小鲸鱼挂件服务端：记账 + 每轮消耗 + 前端配置持久化。"""

    def __init__(self, data_dir: str, asset_dir: str | None = None):
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._state_path = os.path.join(data_dir, "whale-state.json")
        # 静态资源目录（whale-widget/assets）
        self._asset_dir = asset_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "whale-widget", "assets")
        self._lock = threading.Lock()
        self._state = self._load_state()
        # 当前这一轮（一次唤醒）内的累计：{"model","cost","tokens","start_ts"}
        self._cur = None

    # ── 状态持久化 ─────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("size", {})
                data.setdefault("days", {})
                data.setdefault("lastTurn", {})
                return data
        except Exception:
            pass
        return {"size": {}, "days": {}, "lastTurn": {}}

    def _save_state(self):
        try:
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._state_path)
        except Exception:
            pass

    def _usage_cost(self, usage: dict, model: str, ts: float) -> tuple:
        """按峰谷定价折算一次调用的 (成本, token 数)。usage 形如 chat_completions 返回。"""
        try:
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            details = usage.get("prompt_tokens_details") or {}
            cached = int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens")
                         or usage.get("cached_tokens") or 0)
            reasoning = int(usage.get("reasoning_tokens") or 0)
        except Exception:
            return 0.0, 0
        cached = min(cached, prompt)
        fresh = max(0, prompt - cached)
        p = price_for(model)
        pi = 1 if is_peak_time(ts) else 0
        cost = (fresh / 1e6) * p["miss"][pi] + (cached / 1e6) * p["hit"][pi] + ((completion + reasoning) / 1e6) * p["out"][pi]
        return cost, prompt + completion + reasoning

    # ── 记账 API（由 wx_agent 调用）─────────────────────────────────────

    def note_call(self, model: str, usage: dict, ts: float | None = None):
        """每次 LLM 调用成功后计入当前轮的累计（价格按调用时刻的峰谷档位）。"""
        if not usage:
            return
        ts = float(ts or time.time())
        cost, tokens = self._usage_cost(usage, model, ts)
        if cost <= 0 and tokens <= 0:
            return
        with self._lock:
            if self._cur is None:
                self._cur = {"cost": 0.0, "tokens": 0}
            self._cur["cost"] += cost
            self._cur["tokens"] += tokens

    def note_turn_done(self):
        """会话结束：把当前轮累计结算进“最近一轮消耗”并记入今日账本。"""
        with self._lock:
            cur, self._cur = self._cur, None
            if not cur or cur.get("cost", 0) <= 0:
                return None
            lt = self._state.get("lastTurn") or {}
            seq = int(lt.get("seq") or 0) + 1
            turn = int(lt.get("turn") or 0) + 1
            last = {"seq": seq, "turn": turn,
                    "amount": round(cur["cost"], 6), "tokens": int(cur["tokens"]),
                    "ts": int(time.time() * 1000)}
            self._state["lastTurn"] = last
            day = _today_key()
            days = self._state.setdefault("days", {})
            days[day] = round(float(days.get(day) or 0) + cur["cost"], 6)
            # 只保留最近 30 天
            try:
                keep = sorted(k for k in days if k <= day)[-30:]
                self._state["days"] = {k: days[k] for k in keep} if len(days) > 30 else days
            except Exception:
                pass
            self._save_state()
            return last

    def today_usage(self) -> float:
        with self._lock:
            days = self._state.get("days") or {}
            return float(days.get(_today_key()) or 0.0)

    # ── HTTP 接口（webui 转调）─────────────────────────────────────────

    def balance_payload(self) -> dict:
        """GET /dsh-whale/balance.json"""
        try:
            b = query_balance()
        except Exception as e:
            return {"ok": False, "code": "ERROR", "error": str(e)[:200]}
        return {
            "ok": True,
            "totalBalance": float(b.get("total_balance") or 0),
            "currency": str(b.get("currency") or "CNY"),
            "todayUsage": round(self.today_usage(), 6),
            "isPeak": is_peak_time(time.time()),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def size_payload(self) -> dict:
        """GET /dsh-whale/size.json（返回 {} 时前端用默认值）"""
        with self._lock:
            return dict(self._state.get("size") or {})

    def save_size(self, obj: dict) -> dict:
        """PUT /dsh-whale/size.json"""
        if not isinstance(obj, dict):
            return {"ok": False, "error": "missing body"}
        if not isinstance(obj.get("scale"), (int, float)) or isinstance(obj.get("scale"), bool):
            return {"ok": False, "error": "missing scale"}
        with self._lock:
            size = dict(self._state.get("size") or {})
            size.update(obj)
            self._state["size"] = size
            self._save_state()
        return {"ok": True}

    def last_turn_payload(self) -> dict:
        """GET /dsh-whale/last-turn.json"""
        with self._lock:
            lt = self._state.get("lastTurn") or {}
            if lt:
                return {"ok": True, "seq": int(lt.get("seq") or 0),
                        "turn": lt.get("turn"), "amount": lt.get("amount"),
                        "tokens": lt.get("tokens"), "ts": lt.get("ts")}
        return {"ok": True, "seq": 0, "turn": None, "amount": None, "tokens": None, "ts": None}

    # ── 静态资源 ───────────────────────────────────────────────────────

    def asset_bytes(self, name: str) -> bytes | None:
        """读取 whale-widget/assets 下的静态文件。"""
        safe = os.path.basename(name)
        path = os.path.join(self._asset_dir, safe)
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            return None

    def sound_bytes(self, kind: str, sound_set: str) -> bytes | None:
        """按压/松手音效：press→Ya1/D1，release→Ya2/D2。"""
        table = {
            ("press", "duck"): "Ya1.mp3",
            ("release", "duck"): "Ya2.mp3",
            ("press", "fx1"): "D1.mp3",
            ("release", "fx1"): "D2.mp3",
        }
        return self.asset_bytes(table.get((kind, sound_set if sound_set == "fx1" else "duck"), ""))
