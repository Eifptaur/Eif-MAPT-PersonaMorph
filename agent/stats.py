# -*- coding: utf-8 -*-
"""用量统计的持久化 + 按周期重置（data/usage_stats.json）。

累计（total）永远保留；周期（period）按配置周期（daily/weekly/monthly）重置，
保留最近 history 条记录；控制台展示「本周期」与「累计」两组数据。
操作成本为 0，失败不阻塞机器人运行。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta

_PERIODS = ("daily", "weekly", "monthly")
_HISTORY_MAX = 24


def _period_start(t: float | None = None, period: str = "weekly") -> str:
    """当前周期的起始时刻（ISO 本地时间）。daily=当日0点；weekly=本周一0点；monthly=1号0点。"""
    dt = datetime.fromtimestamp(t or time.time())
    if period == "daily":
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "monthly":
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:  # weekly：周一 0 点
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=dt.weekday())
    return start.isoformat()


def _day_start() -> str:
    from datetime import datetime
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _empty() -> dict:
    return {"sessions": 0, "calls": 0, "tokens": 0, "sent": 0, "cost": 0.0}


class UsageStats:
    def __init__(self, data_dir: str, period: str = "weekly"):
        self.path = os.path.join(data_dir, "usage_stats.json")
        self._lock = threading.Lock()
        self.period = period if period in _PERIODS else "weekly"
        self.data = {"total": _empty(), "period": _empty(),
                     "period_start": _period_start(None, self.period),
                     "day": _empty(), "day_start": _day_start(),
                     "history": []}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.data["total"] = {**_empty(), **(d.get("total") or {})}
            self.data["history"] = list(d.get("history") or [])[-_HISTORY_MAX:]
            ps = str(d.get("period_start") or "")
            if ps >= _period_start(None, self.period):
                # 同一周期内（或文件时间在未来）：保留周期数据
                self.data["period_start"] = ps
                self.data["period"] = {**_empty(), **(d.get("period") or {})}
            else:
                # 周期已过 → 归档并重置
                self.data["period_start"] = _period_start(None, self.period)
                self._archive()
                self.data["period"] = _empty()
            # 当日统计：跨天重置
            if str(d.get("day_start") or "") >= _day_start():
                self.data["day_start"] = str(d.get("day_start") or _day_start())
                self.data["day"] = {**_empty(), **(d.get("day") or {})}
            else:
                self.data["day_start"] = _day_start()
                self.data["day"] = _empty()
            self._save()
        except Exception:
            pass

    def _archive(self):
        try:
            p = self.data.get("period") or {}
            if p.get("tokens") or p.get("cost"):
                self.data["history"].append({
                    "start": self.data.get("period_start"),
                    "end": _period_start(None, self.period),
                    **{k: p.get(k, 0) for k in ("sessions", "calls", "tokens", "sent", "cost")},
                })
                self.data["history"] = self.data["history"][-_HISTORY_MAX:]
        except Exception:
            pass

    def record(self, sessions: int = 0, calls: int = 0, tokens: int = 0, sent: int = 0, cost: float = 0.0):
        """追加一轮的增量统计（自动处理周期/当日切换）。"""
        with self._lock:
            try:
                if self.data["period_start"] < _period_start(None, self.period):
                    self._archive()
                    self.data["period_start"] = _period_start(None, self.period)
                    self.data["period"] = _empty()
                if self.data["day_start"] < _day_start():
                    self.data["day_start"] = _day_start()
                    self.data["day"] = _empty()
                targets = (self.data["total"], self.data["period"], self.data["day"])
                for key, val in (("sessions", sessions), ("calls", calls), ("tokens", tokens),
                                 ("sent", sent), ("cost", float(cost or 0.0))):
                    for t in targets:
                        t[key] = t.get(key, 0) + val
                self._save()
            except Exception:
                pass

    def snapshot(self):
        with self._lock:
            return {
                "period_type": self.period,
                "period_start": self.data.get("period_start"),
                "total": dict(self.data.get("total") or {}),
                "period": dict(self.data.get("period") or {}),
                "day": dict(self.data.get("day") or {}),
                "day_start": self.data.get("day_start"),
                "history": list(self.data.get("history") or [])[-_HISTORY_MAX:],
            }

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            pass
