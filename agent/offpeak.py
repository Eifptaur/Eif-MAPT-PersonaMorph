# -*- coding: utf-8 -*-
"""谷段（错峰）判定 —— **纯函数，方便判据钉住**。

## 为什么需要
LLM 供应商普遍有**峰谷定价**：DeepSeek 官方口径是 **北京时间 00:30–08:30** 为优惠时段
（对应 UTC 16:30–00:30），谷段价格约为峰段的一半。而**记忆整理这类后台任务没有实时性要求**
（每群每 6 小时一次、每次约 1.5 万 token），最适合"能等就等到谷段"：
同一批 token，谷段跑等于**直接打五折**，与缓存折扣是**乘法叠加**。

## 口径
- 窗口可配（`memory.consolidate_off_peak_window`，默认 `"00:30-08:30"`），**支持跨零点**（如 `22:00-06:00`）。
- 判定只用**本机时间**（用户在哪儿就按哪儿的钟走）；时区/夏令时交给系统。
- 解析不出来 ⇒ **当作"不在谷段"**（保守：宁可白天多花点钱，也不猜）。
"""
from __future__ import annotations

import datetime
import re

#: DeepSeek 官方谷段（北京时间）——写在这里当默认值，可被配置覆盖
DEFAULT_WINDOW = "00:30-08:30"

_WIN_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def parse_window(text: str):
    """`"00:30-08:30"` → `(30, 510)`（当日分钟数区间）；非法返回 `None`。"""
    m = _WIN_RE.match(str(text or ""))
    if not m:
        return None
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return None
    a, b = h1 * 60 + m1, h2 * 60 + m2
    if a == b:
        return None          # 起止相同＝不成立（不是"全天"）
    return a, b


def in_off_peak(now: datetime.datetime = None, window: str = DEFAULT_WINDOW) -> bool:
    """现在是不是谷段。`window` 跨零点也支持（如 `22:00-06:00`）。"""
    w = parse_window(window or DEFAULT_WINDOW)
    if not w:
        return False
    now = now or datetime.datetime.now()
    cur = now.hour * 60 + now.minute
    a, b = w
    if a < b:
        return a <= cur < b
    return cur >= a or cur < b      # 跨零点


def describe(window: str = DEFAULT_WINDOW) -> str:
    """人话（状态/日志用）。"""
    w = parse_window(window)
    if not w:
        return "谷段窗口「%s」看不懂 ⇒ 一律按峰段处理" % window
    a, b = w
    return "谷段 %02d:%02d–%02d:%02d（本机时间）" % (a // 60, a % 60, b // 60, b % 60)
