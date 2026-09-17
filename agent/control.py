# -*- coding: utf-8 -*-
"""控制信号：**暂停/停止的文件级真相**（2026-09-18 加）。

背景（作者现场）：「说机器已暂停的那一刻，后面一秒他又引用了一下我的消息」——日志实证：`机器人已暂停`
之后 54 秒它仍跑完了一整轮（引用 → 菜单 → 回车 → 退普通发送）。真因＝**暂停原来只在内存里**
（`orch.paused`），`wechat`/`sender` 等模块拿不到 ⇒ 已开工的链中途不检查它。

用法（任何模块都能调，零依赖、读文件即可）：
    from .control import is_paused
    if is_paused(): return False, "机器人已暂停 ⇒ 不发"
"""
from __future__ import annotations

import os
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE = {"at": 0.0, "paused": False, "stopped": False, "ttl": 0.4}   # 0.4s 缓存：避免每条都摸盘


def _flags() -> tuple:
    now = time.time()
    if now - float(_CACHE["at"] or 0) < float(_CACHE["ttl"] or 0.4):
        return bool(_CACHE["paused"]), bool(_CACHE["stopped"])
    p = os.path.join(_ROOT, "data", "paused.flag")
    s = os.path.join(_ROOT, "data", "stopped.flag")
    _CACHE["paused"] = os.path.exists(p)
    _CACHE["stopped"] = os.path.exists(s)
    _CACHE["at"] = now
    return _CACHE["paused"], _CACHE["stopped"]


def is_paused() -> bool:
    """用户按了「暂停」（或峰谷静默档落盘）⇒ 长链的每一步都应当**立刻停手**。"""
    return _flags()[0]


def is_stopped() -> bool:
    """用户按了「停止」⇒ 只允许收尾，不许再发起任何新动作。"""
    return _flags()[1]


def halt_reason() -> str:
    """给失败话术用的一句话（没暂停/停止时返回空串）。"""
    p, s = _flags()
    if s:
        return "机器人已停止 ⇒ 这条不发"
    if p:
        return "机器人已暂停 ⇒ 这条不发"
    return ""


def set_paused_flag(on: bool) -> bool:
    """给**非主进程**（脚本/控制台接口）写暂停标记用；主进程走 `orch.set_paused()`。"""
    p = os.path.join(_ROOT, "data", "paused.flag")
    try:
        if on:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        elif os.path.exists(p):
            os.remove(p)
        _CACHE["at"] = 0.0
        return True
    except Exception:
        return False
