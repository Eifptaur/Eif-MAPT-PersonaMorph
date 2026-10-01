# -*- coding: utf-8 -*-
"""拟人节奏层 —— **只做"慢一点"，不做"发不发"**。

为什么需要（来历）
------------------
外部项目 `wechatauto-replica` 的 `rhythm.py`。它的作者 **2026-09-21 账号触发微信风控、
被强制重新登录**，之后写了那一层。它的核心洞察（原文）：

    微信风控看到的**不是「点了哪里」，而是「动作的时间分布」**：每次都相同的间隔、
    光标瞬时传送、永远命中同一个像素、逐字 20ms 的匀速键入、几秒内连发多条
    —— **这些是人手做不出来的签名**。

★ 与 `agent/risk.py` 的**分工**（**不要把两者混起来**）
--------------------------------------------------------
    `risk.check()`  —— **判"发不发"**：拦下 ⇒ 消息**不发出**（用户可见的硬拦）
    `rhythm`        —— **判"多慢发"**：只 `sleep` ⇒ 消息**照发，只是晚一点**

⇒ 因此**两者默认值是相反的、而且都正确**：
    · `risk.*` 节奏阈值**默认 0＝不限** —— 口径来自用户原话：
      「全局节奏、会话节奏**应该可以交给用户自定义**，让他们自己把控账号的风险」
    · `rhythm` **默认开一个极温和档** —— 因为它**不改变"消息最终会发出"这个事实**，
      只改时间分布。用户把控的是"**发不发**"，不是"**以什么节奏发**" ⇒ **不越权**。

本层**照抄**它的两条设计纪律
----------------------------
1. ★ **抖动只加长、不缩短**（`nap` 的倍率下限钉死 1.0）。它的理由：
   "各处已有的 `time.sleep(x)` 都是在等微信把画面渲染完，**缩短就是拿稳定性换抖动，
   不划算**"。⇒ 本层只可能让等待**变长**，绝不破坏既有渲染稳定性。
2. ★ **状态落盘、跨进程生效**。它的理由："**一个演示脚本一个新 Python 进程，
   纯内存限速对这种用法等于没有**"。⇒ 我方 `scripts/persona_morph.py` 同样是独立
   进程，纯内存限速等于没做。落盘路径复用本项目的 `persist` 原子换档。

⛔ 本层**刻意不做**的（与我方安全红线一致）
------------------------------------------
    · **不做光标轨迹 / 落点随机** —— 红线：真鼠标＝危险源，所有操作优先**投递**，
      本层不移动光标（那是外部项目做的，我方不适用）。
    · **不做逐字键入节奏** —— 我方走投递 `WM_CHAR`，不是逐字键入。
    · **不拦消息** —— 拦是 `risk.py` 的事。

节流范围
--------
"对外可见的写动作"＝**发文本 / 发图片 / 发文件**（我方**全部**对外写动作；点赞/评论/
撤回/拍一拍我方不做）。读数据库、截图、OCR、定位控件**一律不节流**（那些是本机行为，
既不是骚扰、也不改变账号对外的表现）。
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Optional, Tuple

from .config import DATA_DIR
from . import persist

#: 节流状态文件（跨进程）。放在 data/ 下，与其它运行态一致。
STATE_PATH = os.path.join(DATA_DIR, "rhythm.json")

#: 状态数据的形状版本（改字段时 +1，旧档自动丢弃重建）。
STATE_VERSION = 1

#: 滚动窗口内最多记多少个时间戳（超出裁掉，防文件无限长大）。
_MAX_STAMPS = 64


class Profile(object):
    """一套节奏参数。数值区间写成 `(下限, 上限)`，运行时按均匀分布取。"""

    __slots__ = ("name", "gap", "burst", "window", "cooloff", "nap")

    def __init__(self, name, gap=(0.0, 0.0), burst=0, window=120.0,
                 cooloff=(0.0, 0.0), nap=(1.0, 1.0)):
        self.name = name
        self.gap = tuple(gap)          # 两次写动作之间的最小间隔
        self.burst = int(burst)        # 窗口内写动作上限（<=0 不限）
        self.window = float(window)    # 突发统计窗口
        self.cooloff = tuple(cooloff)  # 撞到上限后的冷却
        self.nap = tuple(nap)          # 等待时间倍率（**下限必须 >= 1.0**）

    def to_dict(self) -> dict:
        return {"name": self.name, "gap": list(self.gap), "burst": self.burst,
                "window": self.window, "cooloff": list(self.cooloff),
                "nap": list(self.nap)}

    def __repr__(self):
        return ("Profile(%r, gap=%s, burst=%d, window=%.0f, cooloff=%s, nap=%s)"
                % (self.name, self.gap, self.burst, self.window, self.cooloff, self.nap))


#: 档位表。
#:   `gentle` = **默认档**：只保证"同一会话不会被几秒内连刷"，
#:              间隔远小于真人对话节奏（真人回复间隔通常 5~60 秒），
#:              对正常使用**几乎无感**，但抹掉了"脚本式连发"这个签名。
#:   `calm`   = 长时间挂机 / 面向真人会话时的保守档。
#:   `off`    = 精确还原"加这层之前"的行为（区间退化到 0，nap 倍率 1.0）。
PROFILES = {
    "gentle": Profile("gentle", gap=(1.5, 4.0), burst=12, window=120.0,
                      cooloff=(15.0, 40.0), nap=(1.0, 1.25)),
    "calm": Profile("calm", gap=(5.0, 12.0), burst=5, window=300.0,
                    cooloff=(45.0, 120.0), nap=(1.05, 1.5)),
    "off": Profile("off"),
}

DEFAULT_PROFILE = "gentle"

_rng = random.Random()
_lock = threading.RLock()
_profile: Optional[Profile] = None
_loaded = False
_last_write = 0.0
_stamps = []


def _cfg() -> dict:
    """读 `config.rhythm` 段；读不到时用默认（**默认开 gentle**，见模块 docstring）。"""
    try:
        from .config import get_config
        c = (get_config() or {}).get("rhythm") or {}
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def _from_cfg() -> Profile:
    """按配置构造档位：`profile` 选档，`enabled=False` ⇒ 等价 off。"""
    c = _cfg()
    if not c.get("enabled", True):
        return PROFILES["off"]
    name = str(c.get("profile") or DEFAULT_PROFILE).strip().lower()
    if name not in PROFILES:
        name = DEFAULT_PROFILE
    base = PROFILES[name]
    # 允许单项覆盖（用户自控的旋钮；与 risk.* 同样的"交给用户"口径）
    gap = base.gap
    try:
        lo = float(c["gap_min_seconds"])
        hi = float(c["gap_max_seconds"]) if c.get("gap_max_seconds") is not None else lo * 2.0
        if lo <= 0:
            gap = (0.0, 0.0)          # 显式设 0 ⇒ 关掉间隔（用户自控）
        elif hi > lo:
            gap = (lo, hi)
        else:
            gap = (lo, lo)
    except (KeyError, TypeError, ValueError):
        pass
    burst = base.burst
    try:
        if c.get("burst") is not None:
            burst = int(c["burst"])
    except (TypeError, ValueError):
        pass
    # ★ 抖动**只加长**：即便配置写错也不允许把倍率压到 1.0 以下
    nap_lo = max(1.0, float(base.nap[0]))
    nap_hi = max(nap_lo, float(base.nap[1]))
    return Profile(name if gap == base.gap else name + "+", gap=gap, burst=burst,
                   window=base.window, cooloff=base.cooloff, nap=(nap_lo, nap_hi))


def profile() -> Profile:
    """当前档位（首次调用时读配置）。"""
    global _profile
    if _profile is None:
        _profile = _from_cfg()
    return _profile


def set_profile(name: str) -> Profile:
    """切换档位（测试/脚本用）。环境变量给的覆盖在切档后重新套用。"""
    global _profile
    if name not in PROFILES:
        return profile()
    with _lock:
        _profile = PROFILES[name]
    return _profile


def reload() -> Profile:
    """重新按配置构造（配置改动后调用）。"""
    global _profile
    with _lock:
        _profile = _from_cfg()
    return _profile


# ── 1) 抖动等待（★ 只加长，不缩短）──────────────────────────────────────

def spread(lo: float, hi: float) -> float:
    """区间内取随机数；`off` 档或区间退化时返回下限。"""
    if hi <= lo or profile().name == "off":
        return lo
    return _rng.uniform(lo, hi)


def nap(base: float) -> float:
    """睡掉 `base` 的**抖动版**并返回实际秒数。

    ★ 倍率**下限钉死 1.0**：本层只让等待更**长**。
    各处已有的 `time.sleep(x)` 都是在等微信把画面渲染完，
    **缩短就是拿稳定性换抖动，不划算**（外部项目原话，照抄）。
    """
    lo, hi = profile().nap
    k = _rng.uniform(max(1.0, lo), max(1.0, hi))
    actual = max(0.0, float(base)) * k
    if actual > 0:
        time.sleep(actual)
    return actual


# ── 2) 写动作节流（跨进程落盘）─────────────────────────────────────────

def _load_locked() -> None:
    """把盘上的节流状态读进内存。**时钟回拨/陈旧数据一律丢弃**（防永久卡死）。"""
    global _last_write, _stamps, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if not os.path.isfile(STATE_PATH):
            return
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or int(data.get("v") or 0) != STATE_VERSION:
            return
        now = time.time()
        last = float(data.get("last") or 0.0)
        # 未来时间戳（>60s 超前）= 时钟被回拨 ⇒ 丢弃，否则会把闸门永久锁死
        _last_write = last if 0 <= now - last <= 86400 else 0.0
        win = max(profile().window, 60.0)
        _stamps = [float(s) for s in (data.get("stamps") or [])
                   if isinstance(s, (int, float)) and 0 <= now - float(s) <= win]
    except Exception:
        _last_write, _stamps = 0.0, []


def _save_locked() -> None:
    """原子落盘，**复用 `persist.atomic_write_json`**（不自己拼临时名）。

    ⛔ 为什么必须走 `persist` 而不是自己 `open(tmp)`：本层的 `gate()` 会被**多线程
    /多进程**同时调用，而固定临时名会让两个写者打开同一个临时档、内容互相穿插
    （外部项目 v1.2.4 修过的正是这个：listener 抛 `FileNotFoundError`，来自 `os.replace`；
    本项目 `persist` 的注释另记着"audit 实测 memory 并发写 200 次坏 46 个"）。
    ⇒ `persist` 已带 pid + 随机后缀唯一化 + `os.replace` 退避重试，这里直接用即可。
    """
    try:
        payload = {"v": STATE_VERSION, "last": _last_write,
                   "stamps": _stamps[-_MAX_STAMPS:], "profile": profile().name}
        # 热路径：换档预算压短，失败返回 False（原档一字不动），下一条再试
        persist.atomic_write_json(STATE_PATH, payload, replace_budget_s=0.6)
    except Exception:
        pass   # 落盘失败不影响本次动作（下次仍会尝试）


def gate(kind: str = "send") -> float:
    """**对外可见的写动作之前**调用；该等多久就等多久，返回实际等待秒数。

    ⛔ 与 `risk.check()` 的区别：本函数**从不拦消息**，只让它慢一点。
    ⛔ 读路径（数据库/截图/OCR/控件查找）**不要**调本函数。
    """
    global _last_write, _stamps
    p = profile()
    if p.gap[1] <= 0 and p.burst <= 0:
        return 0.0
    with _lock:
        _load_locked()
        waited = 0.0
        need = _rng.uniform(*p.gap) if p.gap[1] > p.gap[0] else p.gap[0]
        if need > 0:
            ahead = need - (time.time() - _last_write)
            if ahead > 0:
                time.sleep(ahead)
                waited += ahead
        now = time.time()
        _stamps[:] = [s for s in _stamps if 0 <= now - s <= p.window]
        if p.burst > 0 and len(_stamps) >= p.burst:
            cool = _rng.uniform(*p.cooloff) if p.cooloff[1] > p.cooloff[0] else p.cooloff[0]
            if cool > 0:
                time.sleep(cool)
                waited += cool
                _stamps[:] = []
        _last_write = time.time()
        _stamps.append(_last_write)
        _save_locked()
        return waited


def snapshot() -> dict:
    """当前节奏状态（控制台/排查用，也给判据断言）。"""
    with _lock:
        _load_locked()
        now = time.time()
        p = profile()
        return {
            "profile": p.name,
            "gap": list(p.gap),
            "burst": p.burst,
            "window": p.window,
            "since_last": None if not _last_write else round(now - _last_write, 3),
            "recent_writes": sum(1 for s in _stamps if 0 <= now - s <= p.window),
        }


def reset() -> None:
    """清空节流状态（内存 + 盘上）。测试之间互不污染时用。"""
    global _last_write, _stamps, _loaded
    with _lock:
        _last_write, _stamps = 0.0, []
        _loaded = True
        try:
            if os.path.isfile(STATE_PATH):
                os.remove(STATE_PATH)
        except Exception:
            pass


__all__ = ["Profile", "PROFILES", "DEFAULT_PROFILE", "STATE_PATH",
           "profile", "set_profile", "reload", "spread", "nap", "gate",
           "snapshot", "reset"]
