# -*- coding: utf-8 -*-
"""任务槽位**所有权**：只有槽位当前的主人才能写它 / 删它。

病（一种很隐蔽的竞态）：
  同一个名字的活儿可能被**先后起两次**（用户点了重试、作业自动重启、看门线程被替换）。
  先起的那次如果还活着，它结束时会把结果**写回同一个槽位** —— 而那时槽位已经换人了。
  于是：新的那次还在跑，界面却显示"已完成"（旧结果）；或者旧的收尾把新的状态**清掉**。
  这类 bug 的难处是**它不常发生**，且现场过去之后无从复现。

口径（源自 WeFlow 的 Promise 所有权写法）：
  · 每次占用槽位都拿一个**令牌**（token，本进程内唯一）；
  · **写 / 删之前先问**：`is_owner(slot, token)` 为真才动；不是主人就**什么都不做**；
  · 删槽位同样只允许主人删（`if tasks.get(k) is me: tasks.pop(k)`）——
    否则"后来者的收尾"会把**新主人**的槽位一起抹掉。

为什么不用线程 id / 时间戳：线程 id 会被系统复用、时间戳同毫秒会撞。自增序号 + 进程内锁，
比那两者都稳（与 `agent/verdict.py` 的"代次"同一思路）。
"""
from __future__ import annotations

import threading

_lock = threading.RLock()
_seq = 0
_slots: dict = {} # 槽位名 → 当前主人的令牌


def claim(slot) -> int:
    """**占用槽位**（成为新主人），返回本主人在该槽位的令牌。

    同一个槽位被再次 claim ⇒ 主人换成后来者，旧主人的令牌立刻失效（见 `is_owner`）。
    """
    global _seq
    with _lock:
        _seq += 1
        tok = _seq
        _slots[str(slot)] = tok
        return tok


def is_owner(slot, token) -> bool:
    """`token` 是不是该槽位**当前**的主人。

    槽位不存在（已被清）或已换人 ⇒ False。**拿不准一律 False**（fail-closed：
    宁可这次不写，也不许把过期结果写进别人的槽位）。
    """
    try:
        with _lock:
            cur = _slots.get(str(slot))
        return (cur is not None) and (int(cur) == int(token))
    except Exception: # noqa: BLE001
        return False


def release(slot, token) -> bool:
    """**主人自己**释放槽位。只有当前主人能释放（非主人调用**无副作用**，返回 False）。

    ⛔ 这正是 `if tasks.get(k) is me: tasks.pop(k)` 的等价实现 —— 非主人释放
    会把**新主人**的槽位误删（于是新主人后续的 `is_owner` 全 False，状态再也写不进去）。
    """
    with _lock:
        s = str(slot)
        cur = _slots.get(s)
        if cur is None:
            return False
        if int(cur) != int(token):
            return False
        _slots.pop(s, None)
        return True


def current(slot):
    """该槽位当前主人的令牌（没有则 None）。给日志/判据用。"""
    with _lock:
        return _slots.get(str(slot))


def snapshot() -> dict:
    """全部槽位 → 主人令牌（给控制台/日志；**只读**）。"""
    with _lock:
        return dict(_slots)


def reset() -> None:
    """清空全部槽位（判据/收尾用）。"""
    with _lock:
        _slots.clear()
