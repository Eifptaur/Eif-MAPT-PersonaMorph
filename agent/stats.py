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
from . import persist # noqa: E402 原子换档：撞共享冲突要退避重试（见 persist.replace_into）
import logging

log = logging.getLogger("persona-morph")

_PERIODS = ("daily", "weekly", "monthly")
_HISTORY_MAX = 24


def _period_start(t: float | None = None, period: str = "weekly") -> str:
    """当前周期的起始时刻（ISO 本地时间）。daily=当日0点；weekly=本周一0点；monthly=1号0点。"""
    dt = datetime.fromtimestamp(t or time.time())
    if period == "daily":
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "monthly":
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else: # weekly：周一 0 点
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=dt.weekday())
    return start.isoformat()


def _day_start() -> str:
    from datetime import datetime
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _empty() -> dict:
    # 三分口径：只记"总 token"看不出钱花在哪——缓存读/未命中/输出的单价差 30~90 倍
    # （0.05 / 1.5 / 4.5 元每百万）。缺省为 0 ⇒ 老账本（没有这三个字段）读进来自动补 0，不会崩。
    return {"sessions": 0, "calls": 0, "tokens": 0, "sent": 0, "cost": 0.0,
            "fresh_tokens": 0, "cached_tokens": 0, "output_tokens": 0}


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
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass

    def _archive(self):
        try:
            p = self.data.get("period") or {}
            if p.get("tokens") or p.get("cost"):
                self.data["history"].append({
                    "start": self.data.get("period_start"),
                    "end": _period_start(None, self.period),
                    **{k: p.get(k, 0) for k in ("sessions", "calls", "tokens", "sent", "cost",
                                               "fresh_tokens", "cached_tokens", "output_tokens")},
                })
                self.data["history"] = self.data["history"][-_HISTORY_MAX:]
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass

    def record(self, sessions: int = 0, calls: int = 0, tokens: int = 0, sent: int = 0,
               cost: float = 0.0, fresh: int = 0, cached: int = 0, output: int = 0):
        """追加一轮的增量统计（自动处理周期/当日切换）。fresh/cached/output＝三分口径。"""
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
                                 ("sent", sent), ("cost", float(cost or 0.0)),
                                 ("fresh_tokens", int(fresh or 0)),
                                 ("cached_tokens", int(cached or 0)),
                                 ("output_tokens", int(output or 0))):
                    for t in targets:
                        t[key] = t.get(key, 0) + val
                self._save()
            except Exception as _e:
                log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                pass

    def by_month(self, months: int = 6, now=None) -> list:
        """按**自然月**归并用量与花费（**降序，含当月**）⇒ 供「月账单」用。

        为什么要单独一个口径：现有 `period` 能切成 `monthly`，但它只回答"**本周期**花了多少"，
        回答不了"**上个月、上上个月各花了多少**" —— 而用户想看的恰恰是后者。

        ⛔ 两个不能省的点：
          · **没花费的月份要给 0，不是缺项** —— "这个月没花钱"和"没这个月"是两回事，
            前者必须显示出来（否则用户看到的是**空白**，会以为页面坏了）；
          · **同一自然月里的多条历史（周账）要相加** —— 周期是周/日时，一个月会有好几条。
        """
        n = max(1, int(months or 6))
        agg = {}
        for it in (self.data.get("history") or []):
            k = str((it or {}).get("start") or "")[:7]
            if len(k) != 7:
                continue
            acc = agg.setdefault(k, _empty())
            for f in acc:
                try:
                    acc[f] = acc[f] + (float(it.get(f) or 0) if f == "cost" else int(it.get(f) or 0))
                except Exception:  # noqa: BLE001
                    pass
        # 当期（可能还没归档）也并进它所属的那个月
        _cur = self.data.get("period") or {}
        _ck = str(self.data.get("period_start") or "")[:7]
        if len(_ck) == 7 and (_cur.get("tokens") or _cur.get("cost") or _cur.get("calls")):
            acc = agg.setdefault(_ck, _empty())
            for f in acc:
                try:
                    acc[f] = acc[f] + (float(_cur.get(f) or 0) if f == "cost" else int(_cur.get(f) or 0))
                except Exception:  # noqa: BLE001
                    pass
        # 生成"最近 n 个自然月"（**含没有记录的月份** ⇒ 给 0）
        base = datetime.fromtimestamp(now or time.time())
        out = []
        for i in range(n):
            y, m = base.year, base.month - i
            while m <= 0:
                m += 12
                y -= 1
            key = "%04d-%02d" % (y, m)
            row = dict(agg.get(key) or _empty())
            row["month"] = key
            row["label"] = "%d 年 %d 月" % (y, m)
            row["current"] = (i == 0)
            out.append(row)
        return out

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
                # 月账单（第 151 项）：两个前端都读这一处（web 的 stat 格 / Qt 的 cost 格）
                "months": self.by_month(6),
            }

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = persist.tmp_for(self.path)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            persist.replace_into(tmp, self.path)
        except Exception as _e:
            log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
            pass
