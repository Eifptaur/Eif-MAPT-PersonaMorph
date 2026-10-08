# -*- coding: utf-8 -*-
"""群活跃度自适应（第 134 项）：**发言频率跟着群的冷热走**。

要解决的问题：一个**固定**的"每小时最多发几条"在两种群里都不对 ——
- **冷群**（半天没人说话）：机器人一开口就是"最活跃的那个人"，很扎眼、也最容易招烦；
- **热群**（消息刷得飞快）：固定上限把它压得插不上话，等于不会聊。

⇒ 做法：拿**这个群近期的消息密度**与**它自己的长期均值**比 ⇒ 得出冷/正常/热 ⇒ 把"每单位时间上限"
**按系数缩放**（往下缩得谨慎、往上加要封顶）。

★ 与邻居的分工（别混）
  · `risk.check()` —— 判"**发不发**"（硬拦，用户可见）；
  · `rhythm`       —— 判"**多慢发**"（只 sleep，照发）；
  · **本模块**     —— 判"**这条会话这一段最多发几条**"（改的是**上限**，不是单条速度）。
⛔ **默认关**（`group_adaptive.enabled = false`）⇒ 不开启时**行为与以前一字不差**（零回归）。
   与 `risk` 的 `quiet_hours` 同一套口径：这类"旋钮"默认不替用户做主。

数据源：`agent/store.py::ChatStore.recent()`（消息自带 **毫秒** `ts`）。读不到 ⇒ 一律按"正常"档，
**不猜、不因为读不到就把群当冷群**（那会把消息憋住）。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

LEVELS = ("cold", "normal", "hot")
#: 人话（进日志/回执用）
LEVEL_CN = {"cold": "冷（近期几乎没人说话）", "normal": "正常", "hot": "热（消息刷得快）"}

DEFAULTS = {
    "enabled": False,      # ⛔ 默认关：开了才按活跃度缩放上限
    "window_min": 30,      # 近期窗口（分钟）
    "baseline_days": 7,    # 长期均值取多少天
    "cold_ratio": 0.33,    # 近期密度 / 长期密度 < 这个 ⇒ 冷
    "hot_ratio": 1.5,      # ................................ > 这个 ⇒ 热
    "cold_scale": 0.6,     # 冷群把上限降到 60%
    "hot_scale": 1.4,      # 热群提到 140%
    "max_scale": 1.5,      # 再怎么热也不超过这个倍数（封顶，防"越聊越上头"）
    "min_cap": 1,          # 缩完至少留 1 条（不许缩成 0 ⇒ 那等于把嘴封死）
}


def _cfg(cfg=None) -> dict:
    out = dict(DEFAULTS)
    try:
        if cfg is None:
            from .config import get_config
            cfg = get_config()
        out.update(dict((cfg or {}).get("group_adaptive") or {}))
    except Exception:  # noqa: BLE001
        pass
    return out


_STORE = None


def _store(store=None):
    """调用方没给 store ⇒ **自己建一个**（只建一次）。

    为什么允许"自己建"：本模块的调用点（`risk.check()`）拿不到 store，
    而"群冷热"这件事必须从消息库读 ⇒ 与其把 store 一路透传进风控，不如这里自给自足。
    """
    global _STORE
    if store is not None:
        return store
    if _STORE is None:
        try:
            from .store import ChatStore
            _STORE = ChatStore()
        except Exception as e:  # noqa: BLE001
            log.debug("建 ChatStore 失败（按正常档处理）：%s", str(e)[:80])
            return None
    return _STORE


def _msgs(store, chat_key: str, limit: int = 600) -> list:
    """拿最近的原始消息（**读不到 ⇒ 空表**，调用方据此走"正常"档）。"""
    st = _store(store)
    if st is None:
        return []
    try:
        return list(st.recent(chat_key, limit=limit, include_self=True) or [])
    except Exception as e:  # noqa: BLE001
        log.debug("取消息失败（按正常档处理）：%s", str(e)[:80])
        return []


def _ts_of(m) -> float:
    try:
        t = float((m or {}).get("ts") or 0)
    except Exception:  # noqa: BLE001
        return 0.0
    if t > 1e11:      # 毫秒（`store.append_incoming` 存的就是毫秒）
        t = t / 1000.0
    return t


def activity(chat_key: str, *, store=None, cfg=None, now=None) -> dict:
    """这个群的活跃度 ⇒ `{level, recent_per_min, base_per_min, ratio, n_recent, n_base, note}`。

    · 近期密度＝**窗口内**的消息数 ÷ 窗口分钟数；
    · 长期密度＝（**基线天数里**的消息数 − 窗口内那些）÷ 剩余分钟数；
      基线里不足 1 条 ⇒ **当作"样本不够"** ⇒ 直接判 `normal`（**不猜**）。
    ⛔ 消息数按**全部发言**算（含自己发的）——"这个群在不在聊"跟谁发的无关。
    """
    c = _cfg(cfg)
    import time as _t
    now = float(now if now is not None else _t.time())
    win_s = max(60.0, float(c["window_min"]) * 60.0)
    base_s = max(win_s * 2, float(c["baseline_days"]) * 86400.0)
    _st = _store(store)
    msgs = _msgs(_st, chat_key) if _st is not None else []
    ts = [_ts_of(m) for m in msgs]
    ts = [t for t in ts if t > 0]
    n_recent = sum(1 for t in ts if t > now - win_s)
    n_base = sum(1 for t in ts if t > now - base_s)
    recent_rate = n_recent / (win_s / 60.0)
    # ⛔ 长期密度**不能拿"名义天数"当分母**：消息库只留最近 N 条（`recent(limit=…)`），
    #   热群很容易把 7 天截断成十几个小时 ⇒ 分母按 7 天算会把 base_rate **算小**
    #   ⇒ 于是"正常"被误判成"热"（实测踩过）。⇒ 分母用**实际覆盖到的时间跨度**。
    _oldest = min(ts) if ts else now
    base_rest = max(1.0, (now - max(_oldest, now - base_s)) / 60.0)
    base_n = max(0, n_base - n_recent)
    base_rate = base_n / base_rest
    out = {"level": "normal", "recent_per_min": round(recent_rate, 4),
           "base_per_min": round(base_rate, 4), "ratio": 0.0,
           "n_recent": n_recent, "n_base": n_base, "note": ""}
    if _st is None or base_n < 1:
        out["note"] = "样本不够（长期基线不足 1 条）⇒ 按正常档，不缩放"
        return out
    ratio = (recent_rate / base_rate) if base_rate > 0 else 0.0
    out["ratio"] = round(ratio, 3)
    if ratio < float(c["cold_ratio"]):
        out["level"] = "cold"
    elif ratio > float(c["hot_ratio"]):
        out["level"] = "hot"
    out["note"] = "%s：近期 %.2f 条/分，长期 %.2f 条/分（比 %.2f）" % (
        LEVEL_CN[out["level"]], recent_rate, base_rate, ratio)
    return out


def cap_scale(chat_key: str, *, store=None, cfg=None, now=None) -> float:
    """按活跃度给"每单位时间上限"的**缩放系数**（未开启 ⇒ 恒 `1.0`）。"""
    c = _cfg(cfg)
    if not c.get("enabled"):
        return 1.0
    # ⛔ 这里必须把**原始 cfg** 传下去：`activity()` 自己也会过一遍 `_cfg`，
    #   若把已经合并好的扁平 `c` 再传进去，`c.get("group_adaptive")` 是 None
    #   ⇒ 又回到默认值（enabled=False）⇒ **开了也永远返回 1.0**（这个坑踩过）。
    lv = activity(chat_key, store=store, cfg=cfg, now=now).get("level") or "normal"
    if lv == "cold":
        s = float(c["cold_scale"])
    elif lv == "hot":
        s = float(c["hot_scale"])
    else:
        s = 1.0
    return max(0.1, min(float(c["max_scale"]), s))


def adaptive_cap(chat_key: str, base_cap: int, *, store=None, cfg=None, now=None) -> int:
    """把配置里的上限按活跃度缩放 ⇒ **整数条数**（`base_cap <= 0` ⇒ 原样返回 0＝不限）。

    ⛔ 缩放后的下限是 `min_cap`（默认 1）——**不许缩成 0**（那等于把机器人的嘴封死，
      而"群冷"本身并不等于"不许说话"）。
    """
    try:
        base = int(base_cap or 0)
    except Exception:  # noqa: BLE001
        return 0
    if base <= 0:
        return 0
    c = _cfg(cfg)
    n = int(round(base * cap_scale(chat_key, store=store, cfg=cfg, now=now)))
    return max(int(c["min_cap"]), n)
