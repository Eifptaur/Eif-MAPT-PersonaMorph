# -*- coding: utf-8 -*-
"""**学会停手**：动作族的熔断 + "借用前台"的配额。

## 为什么要有它（现场）

2026-09-28 网友反馈：*"他一直把微信界面顶出来，虽然不操控鼠标，但是一直顶界面，闪来闪去的，
并且一直在划群列表"*。他自己带的原因码说明得很清楚：`foreground_not_target` / `identity_unconfirmed`
—— **每一枪都被我们自己的安全闸拒了**（拒绝是对的），但**拒绝了还接着来**：
每条要回的消息都再借一次前台（短暂置前约 1.8~3.2 秒后还回）、再切一次会话，
于是用户看到的就是"顶出来 + 闪 + 列表在动"。**根因不是某处抢窗口，是失败之后不肯停手。**

## 口径（照抄业界成熟做法，不发明新词）

- **重试要"少、有界、逐步加长"**（AWS/Polly/Resilience4j 的通行结论：无界重试＝自己 DoS 自己）；
- **熔断**：连续失败到阈值 ⇒ **打开**，冷却期内**一次都不再试**；冷却后**半开**只放一发试探，
  成功即合上、失败则重新冷却（Martin Fowler 的 circuit breaker）；
- **失败要如实说**：停手不是"悄悄放弃"，要把"这台机器现在做不到什么、下一步能做什么"讲出来；
- **对同一个失败目标反复重试不是重试，是重复**（同一台机器、同一条路线，再试一次不会有新结果）。

## 形状

- `Breaker`：按**动作族**（`switch_chat`＝切会话/确认目标、`send`＝投递发送）记连续失败；
- `TokenBucket`：给**借用前台**这类"有外部可见代价"的动作配额（默认 1 分钟 6 次、且两次之间至少 10 秒）；
- 时钟可注入（`clock=`），判据才能不靠 `sleep` 就把"冷却到了没有"钉住。

## 边界（不许越的线）

本模块**只决定"要不要再动手"**，不改任何判据、不碰安全闸门（`ui_adapt.fg_allowed()` 等照旧先行）。
换句话说：它只会让动作**更少**，永远不会让动作**更多**。
"""
from __future__ import annotations

import logging
import random
import threading
import time

log = logging.getLogger("persona-morph")

#: 默认参数（可被 `config.wechat.*` 覆盖；缺键/读不到一律用这里的保守值）
DEF = {
    # 连续失败几次就打开（"少"：3 次足够判定"这条路线在这台机器上不行"）
    "stop_after_failures": 3,
    # 打开后冷却多久（秒）。90 秒 ≈ 用户手动点开一次会话的时间尺度
    "stop_cooldown_s": 90.0,
    # 借用前台：窗口内配额 + 两次之间的最小间隔（"闪来闪去"的直接解药）
    "foreground_per_minute": 6,
    "foreground_min_gap_s": 10.0,
    # 失败后的退避基数与上限（指数 + 抖动；抖动是为了不让"多条消息同时失败"再同时来）
    "backoff_base_s": 8.0,
    "backoff_max_s": 120.0,
}

_LOCK = threading.RLock()


def _now() -> float:
    return time.monotonic()


def _jitter(sec: float) -> float:
    """±25% 抖动（业界常用档；纯指数退避在"多条消息同时失败"时会同步复发）。"""
    try:
        return max(0.0, float(sec) * (1.0 + random.uniform(-0.25, 0.25)))
    except Exception: # noqa: BLE001
        return max(0.0, float(sec))


class Breaker:
    """一个动作族的熔断器：closed → open（冷却）→ half-open（放一发）→ …"""

    def __init__(self, name: str, threshold: int = 3, cooldown_s: float = 90.0,
                 base_s: float = 8.0, max_s: float = 120.0, clock=_now):
        self.name = name
        self.threshold = max(1, int(threshold))
        self.cooldown_s = float(cooldown_s)
        self.base_s = float(base_s)
        self.max_s = float(max_s)
        self._clock = clock
        self.fails = 0           # 连续失败数（成功即清零）
        #: 打开时刻；**用 -1.0 当"没打开"的哨兵**（0.0 会是合法时钟值 —— 判据里注入 clock 时
        #  第一个采样点就是 0，用 0 当哨兵会把"刚打开"误判成"没打开"，实测踩过）
        self.opened_at = -1.0
        self.half_probe = False  # 本次冷却里那一发试探是否已放
        self.last_why = ""

    # ── 状态 ──
    @property
    def open(self) -> bool:
        return self.opened_at >= 0.0

    def can_try(self) -> tuple:
        """现在允许动手吗？→ `(ok, 为什么不行)`

        打开期间**一次都不许**（这就是"停手"）；冷却到点后只放**一发**试探（半开）。
        """
        if not self.open:
            return True, ""
        t = self._clock()
        if (t - self.opened_at) < self.cooldown_s:
            left = self.cooldown_s - (t - self.opened_at)
            return False, ("这条路线刚连续失败 %d 次 ⇒ 已停手（还等 %.0f 秒再试一发）；"
                           "上一次的原因：%s" % (self.fails or self.threshold, left,
                                               self.last_why or "（未记）"))
        if self.half_probe:
            return False, ("试探那一发已经放过了、还没成功 ⇒ 继续停手（上一次：%s）"
                           % (self.last_why or "（未记）"))
        self.half_probe = True   # 半开：只放这一发
        return True, ""

    def ok(self) -> None:
        """这一枪成了 ⇒ 合上、清零。"""
        with _LOCK:
            self.fails = 0
            self.opened_at = -1.0
            self.half_probe = False
            self.last_why = ""

    def fail(self, why: str = "") -> float:
        """这一枪没成 ⇒ 累计；到阈值就打开。返回**建议的退避秒数**（0＝不用退避）。"""
        with _LOCK:
            self.fails += 1
            self.last_why = str(why or "")[:200]
            self.half_probe = False
            if self.fails >= self.threshold and not self.open:
                self.opened_at = self._clock()
                return self.cooldown_s
            if self.open:
                self.opened_at = self._clock()   # 试探又失败 ⇒ 重新冷却
                return self.cooldown_s
            # 还没到阈值：指数退避（第 1 次失败也退一点，别立刻再来）
            return _jitter(min(self.max_s, self.base_s * (2 ** max(0, self.fails - 1))))

    def reset(self) -> None:
        """外部条件变了（例：来了**新消息** / 用户手动处理过）⇒ 给一次重新开始的机会。"""
        with _LOCK:
            self.fails = 0
            self.opened_at = -1.0
            self.half_probe = False

    def status(self) -> dict:
        return {"name": self.name, "open": self.open, "fails": self.fails,
                "threshold": self.threshold, "cooldown_s": self.cooldown_s,
                "last_why": self.last_why}


class TokenBucket:
    """有外部可见代价的动作（借用前台）的配额：`per_minute` 次 / 分钟 + 两次之间最小间隔。"""

    def __init__(self, per_minute: int = 6, min_gap_s: float = 10.0, clock=_now):
        self.capacity = max(1, int(per_minute))
        self.min_gap_s = max(0.0, float(min_gap_s))
        self._clock = clock
        self._hits = []          # 窗口内的取用时刻
        #: 上次取用时刻；**-1.0 当"没取过"的哨兵**（0.0 是合法时钟值 —— 判据注入 clock 时第一个
        #  采样点就是 0，用 0 当哨兵会让"最小间隔"那道检查整条被跳过，实测踩过两次）
        self._last = -1.0
        self.refused = 0
        self.last_why = ""

    def take(self) -> tuple:
        """取一个配额 → `(ok, 为什么不行)`。取不到时**不要动手**。"""
        with _LOCK:
            t = self._clock()
            self._hits = [x for x in self._hits if (t - x) < 60.0]
            gap = t - self._last
            if self.min_gap_s and self._last >= 0.0 and gap < self.min_gap_s:
                self.refused += 1
                self.last_why = "距上一次借用前台才 %.0f 秒（最少隔 %.0f 秒）" % (gap, self.min_gap_s)
                return False, self.last_why
            if len(self._hits) >= self.capacity:
                self.refused += 1
                self.last_why = "这一分钟已经借了 %d 次前台（上限 %d）" % (len(self._hits), self.capacity)
                return False, self.last_why
            self._hits.append(t)
            self._last = t
            return True, ""

    def status(self) -> dict:
        with _LOCK:
            t = self._clock()
            used = len([x for x in self._hits if (t - x) < 60.0])
            return {"used_this_minute": used, "capacity": self.capacity,
                    "min_gap_s": self.min_gap_s, "refused": self.refused,
                    "last_why": self.last_why}


# ── 进程级单例（按动作族）──────────────────────────────────────────────
_BREAKERS = {}
_FG = None


def _cfg() -> dict:
    """读 `config.wechat.*` 的停手参数；读不到就用 `DEF`（保守侧）。

    ⛔ 读配置失败**要留一行痕**（本仓静默点棘轮只许降）：这里不是"没事发生"，
       而是"用户调过的参数这次没生效"——排障时需要看得见。
    """
    out = dict(DEF)
    try:
        from .config import get_config
        w = (get_config() or {}).get("wechat") or {}
    except Exception as e: # noqa: BLE001
        log.debug("停手参数读取失败 ⇒ 用默认值：%s", e)
        return out
    for k in DEF:
        if k in w:
            out[k] = w[k]
    return out


def breaker(family: str = "switch_chat") -> Breaker:
    """拿一个动作族的熔断器（第一次调用时按配置建；之后复用同一只）。"""
    with _LOCK:
        b = _BREAKERS.get(family)
        if b is None:
            c = _cfg()
            b = Breaker(family, threshold=int(c["stop_after_failures"]),
                        cooldown_s=float(c["stop_cooldown_s"]),
                        base_s=float(c["backoff_base_s"]), max_s=float(c["backoff_max_s"]))
            _BREAKERS[family] = b
        return b


def foreground() -> TokenBucket:
    """借用前台的配额桶（进程级单例）。"""
    global _FG # noqa: PLW0603
    with _LOCK:
        if _FG is None:
            c = _cfg()
            _FG = TokenBucket(per_minute=int(c["foreground_per_minute"]),
                              min_gap_s=float(c["foreground_min_gap_s"]))
        return _FG


def rebind() -> None:
    """配置改了（或判据要换夹具）⇒ 丢掉旧单例，下次按新配置重建。"""
    global _FG # noqa: PLW0603
    with _LOCK:
        _BREAKERS.clear()
        _FG = None


def status() -> dict:
    """给控制台/检验报告用的一眼读数。"""
    with _LOCK:
        return {"breakers": [b.status() for b in _BREAKERS.values()],
                "foreground": foreground().status(),
                "defaults": dict(DEF)}
