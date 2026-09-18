# -*- coding: utf-8 -*-
"""DeepSeek 峰谷价目表 —— **唯一来源**（照抄上游 `dsh-whale-widget@0.3.5` lib/index.js L436-495）。

为什么要单开一个模块（2026-09-19，起因＝把挂件从 0.2.10 升到 0.3.5）：
  仓里原本**有三份**价目表 —— `agent/whale.py`（挂件移植那份）、`agent/llm.py::_OFFICIAL_PRICES`
  （成本估算那份）、`agent/stats.py` 注释里那份。三份互相打架，后果是**同一个 usage 会算出两个数**：
  控制台的「今日已用／每轮消耗」（挂件口径，峰谷定价）与统计里的 cost（平铺单价）不一致。
  上游 0.3.5 同时改了两件事，两份表就更容易对不上：
    ① **价目表**：2026-09-10 起 Flash 系列降价（缓存命中 0.05→0.02、未命中 1.5→1、输出 4.5→4；
       高峰＝空闲×2）。**Pro 没跟着降** —— 上游注释引官方 2026-09-14 公告：「此前『9/14 12:00 起
       pro 请求路由到 V4.1 Flash 并按 Flash 价计费』的安排已取消，因此这里不做按日期的降级切换」。
       ⚠️ 所以口头常说的「Pro = Flash 的 3 倍」**今天已经不成立**（那是 2026-08-17 时对**旧 Flash 价**
       0.05/1.5/4.5 说的；现在按 miss 算 Pro 是 Flash 的 4.5 倍、按 out 是 3.375 倍、按 hit 是 7.5 倍）。
       ⚠️ 我们 `llm.py` 里 pro 那两行原本写的是 2 / 8 / 0.04（≈ 新 Flash 的 2 倍），与挂件差 2.25 倍
       —— 按「挂件＝权威口径」统一到挂件。
    ② **计费公式**：`reasoningTokens ⊆ outputTokens`（上游 issue #89 / PR #83）⇒ 输出侧**只能算一次**，
       不能再 `output + reasoning`（那会把思考重复计费，上游实测偏高约一倍）。
⇒ 现在：挂件（`whale.py`）、成本估算（`llm.py` 的 DeepSeek 段）、报账脚本都从**这里**取。
⚠️ 上游调价时改这一个文件的 `BASE_PRICE` / `PRO_PRICE`（连同注释里的日期与来源），别再各改各的。

来源：`https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget`（npm 包名 `dsh-whale-widget`）。
素材许可见上游 `PROVENANCE.md`：代码 MIT，`assets/**` 不在 MIT 范围内（自 0.3.1 起随包 PNG 已剥离
eXIf/iTXt/tEXt/zTXt 元数据块）；本项目移植该挂件已获原作者同意。
"""
from __future__ import annotations

import datetime as _dt
import time

# ── 峰谷时段（北京时间；工作日 09:00-12:00 与 14:00-18:00 为高峰）──────────────────
PEAK_HOURS = ((9, 12), (14, 18))
# 元 / 百万 token，格式 [空闲时段价, 高峰时段价]
BASE_PRICE = {"hit": [0.02, 0.04], "miss": [1.0, 2.0], "out": [4.0, 8.0]}    # Flash（V4.1-Flash）
PRO_PRICE = {"hit": [0.15, 0.3], "miss": [4.5, 9.0], "out": [13.5, 27.0]}    # Pro（V4-Pro-0813）
# 模型名 → 价档。**顺序有讲究**：先比对"更具体"的名字，别让 `deepseek-v4-pro` 被前缀误判。
_ALIASES = (
    ("deepseek-v4-pro", PRO_PRICE),
    ("deepseek-reasoner", PRO_PRICE),            # 老别名 ⇒ 按 V4-Pro 价近似（llm.py 既有口径）
    ("deepseek-flash", BASE_PRICE),              # 现行正名（V4.1-Flash，2026-09-14 官方页 + /models 实测）
    ("deepseek-v4-flash-vision-exp", BASE_PRICE),  # 已退役旧名 ⇒ 由 V4.1-Flash 服务
    ("deepseek-v4-flash", BASE_PRICE),           # 已退役旧名
    ("deepseek-chat", BASE_PRICE),               # 老别名 ⇒ 实测由 V4.1-Flash 服务
)
# 兼容既有代码/判据里的 `PRICING` 字典写法（`agent/whale.py` 曾导出过它）
PRICING = dict(_ALIASES)
PRICING["_default"] = BASE_PRICE

# 北京时间 2026-08-23 00:00 的 epoch 秒（周末全天谷价生效分界）
WEEKEND_VALLEY_FROM_SEC = _dt.datetime(
    2026, 8, 23, tzinfo=_dt.timezone(_dt.timedelta(hours=8))).timestamp()
_BJ_OFFSET = 8 * 3600      # 本项目与 DSH 环境的时区不同 ⇒ 统一按 UTC+8 换算北京日历


def price_for(model: str) -> dict:
    """按模型名取价档（认不出就按 Flash 档，与上游 `_default` 一致）。"""
    m = str(model or "").lower()
    for key, price in _ALIASES:
        if key in m:
            return price
    return BASE_PRICE


def is_peak_time(time_sec: float) -> bool:
    """按北京时间判断是否高峰时段（含 2026-08-23 起的"周末全天谷价"）。"""
    try:
        n = float(time_sec)
    except (TypeError, ValueError):
        return False
    bj = time.gmtime(n + _BJ_OFFSET)          # gmtime + 偏移 = 北京时间日历
    if n >= WEEKEND_VALLEY_FROM_SEC:
        if bj.tm_wday in (5, 6):              # 5=周六 6=周日
            return False
    for start, end in PEAK_HOURS:
        if start <= bj.tm_hour < end:
            return True
    return False


def usage_parts(usage: dict) -> dict:
    """把一次调用的 usage 拆成计费要用的几份（**唯一实现**，别再各写一套）。

    字段兼容三种写法（我们这侧是 OpenAI 兼容形态）：
      `prompt_tokens` ＝ 输入总量（含缓存命中那部分）；`cached_tokens` ⊆ prompt；
      `completion_tokens` ＝ 输出总量（**已含思考**）；`reasoning_tokens` ⊆ completion。
    ⇒ 「按输出价计费」= completion（见 `billable_output`），token 总数 = prompt + completion。
    """
    try:
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens")
                     or usage.get("cached_tokens") or 0)
        reasoning = int(usage.get("reasoning_tokens") or 0)
        if not reasoning:
            cdet = usage.get("completion_tokens_details") or {}
            reasoning = int(cdet.get("reasoning_tokens") or 0)
    except Exception:
        return {"prompt": 0, "cached": 0, "fresh": 0, "output": 0, "reasoning": 0,
                "billable_out": 0, "tokens": 0}
    cached = max(0, min(cached, prompt))
    fresh = max(0, prompt - cached)
    return {"prompt": prompt, "cached": cached, "fresh": fresh, "output": completion,
            "reasoning": reasoning, "billable_out": billable_output(usage),
            "tokens": prompt + completion}


def billable_output(usage: dict) -> int:
    """按输出价计费的输出 token。

    `reasoningTokens ⊆ outputTokens`（上游 issue #89 / PR #83，dsh 侧由 token-meter 保证；
    DeepSeek API 的 `completion_tokens` 同样包含思考）⇒ **只取 completion**，绝不再加 reasoning。
    兜底：万一某家 provider 把思考报在 completion 之外（会看到 reasoning > completion），
    取两者较大值，宁可照实多计一点也不漏计。
    """
    try:
        completion = int(usage.get("completion_tokens") or 0)
        reasoning = int(usage.get("reasoning_tokens") or 0)
        if not reasoning:
            cdet = usage.get("completion_tokens_details") or {}
            reasoning = int(cdet.get("reasoning_tokens") or 0)
    except Exception:
        return 0
    return max(completion, reasoning)


def cost_of(usage: dict, model: str, ts: float | None = None) -> tuple:
    """一次调用的 `(成本元, 计费 token 数)`，按 `ts` 时刻的峰谷档位定价。"""
    parts = usage_parts(usage)
    if not parts["tokens"]:
        return 0.0, 0
    p = price_for(model)
    pi = 1 if is_peak_time(float(ts if ts is not None else time.time())) else 0
    cost = ((parts["cached"] / 1e6) * p["hit"][pi]
            + (parts["fresh"] / 1e6) * p["miss"][pi]
            + (parts["billable_out"] / 1e6) * p["out"][pi])
    return cost, parts["tokens"]
