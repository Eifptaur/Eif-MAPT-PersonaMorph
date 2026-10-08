# -*- coding: utf-8 -*-
"""控制信号：**暂停/停止的文件级真相**。

背景——日志实证：`机器人已暂停`
之后 54 秒它仍跑完了一整轮（引用 → 菜单 → 回车 → 退普通发送）。真因＝**暂停原来只在内存里**
（`orch.paused`），`wechat`/`sender` 等模块拿不到 ⇒ 已开工的链中途不检查它。

用法（任何模块都能调，零依赖、读文件即可）：
    from .control import is_paused
    if is_paused(): return False, "机器人已暂停 ⇒ 不发"
"""
from __future__ import annotations

import os
import time
import logging

log = logging.getLogger("persona-morph")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: 两个标记的路径 —— **可注入**（第十三/十四轮：判据只能打桩函数、没法传参隔离，
#: 于是 `risk_selftest` 跑一次就在**产品目录**里创建了 `data\paused.flag`，
#: 而每个发送链每一步都查它 ⇒ **跑一次复核就把用户的机器人暂停了**）。
#: 判据/探针请用 `set_paths(...)` 或直接改这两个模块级变量指到 `%TEMP%`。
PAUSED_PATH = os.path.join(_ROOT, "data", "paused.flag")
STOPPED_PATH = os.path.join(_ROOT, "data", "stopped.flag")
_CACHE = {"at": 0.0, "paused": False, "stopped": False, "ttl": 0.4} # 0.4s 缓存：避免每条都摸盘


def set_paths(paused: str = "", stopped: str = "") -> dict:
    """把两个标记路径换掉（判据隔离用）；返回**旧值**方便还原。传空串＝保持不动。

    ⚠️ **同时把嵌套计数归零** —— 换了档还在用旧计数的层数没有意义，且会让判据互相串。
    """
    global PAUSED_PATH, STOPPED_PATH
    old = {"paused": PAUSED_PATH, "stopped": STOPPED_PATH}
    if paused:
        PAUSED_PATH = str(paused)
    if stopped:
        STOPPED_PATH = str(stopped)
    _CACHE["at"] = 0.0 # 换路径必须让缓存立刻失效
    _DEPTH["n"] = 0
    _DEPTH["tickets"] = ()
    _DEPTH["seq"] = 0
    _REASON["last"] = ""
    return old


def _flags() -> tuple:
    now = time.time()
    if now - float(_CACHE["at"] or 0) < float(_CACHE["ttl"] or 0.4):
        return bool(_CACHE["paused"]), bool(_CACHE["stopped"])
    _CACHE["paused"] = os.path.exists(PAUSED_PATH)
    _CACHE["stopped"] = os.path.exists(STOPPED_PATH)
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


def set_paused_flag(on: bool, path: str = "") -> bool:
    """给**非主进程**（脚本/控制台接口）写暂停标记用；主进程走 `orch.set_paused()`。

    `path` 不给就用模块级的 `PAUSED_PATH`（判据可传临时档；见文件头那段）。
    """
    p = str(path or PAUSED_PATH)
    try:
        if on:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        elif os.path.exists(p):
            os.remove(p)
        _CACHE["at"] = 0.0
        return True
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 嵌套暂停：用**计数器 + 持票人**，不用布尔
#
# 病：布尔暂停只能表达"暂停/未暂停"两态。若两个来源**各自**暂停（例如：用户按了「暂停」，
#   同时某个后台流程也临时挂起），先结束的那个一恢复就把整机恢复了 —— 另一个来源还在等，
#   机器人却已经开始发消息（提前恢复）。嵌套越深，越容易踩。
#
# 口径（两层保险）：
#   ① **计数**：`suspend_depth` 从 0 起加；**只有归零才真恢复**（flag 才消失）。
#   ② **持票**：每次 push 发一张票（token）；`suspend_pop(token)` **只退自己那张票**。
#      ⇒ 某个来源多退一次、或退错对象，**不会把别人的那一层解掉**（裸计数做不到这点：
#        多退一次就直接减掉一个别人的层，仍会提前恢复）。
#   · 状态是**进程内内存态**（嵌套发生在同一次运行里；跨进程的"暂停"仍走 flag，见上）。
#   · flag 是**派生结果**：`depth > 0` ⇒ 写 flag；`depth == 0` ⇒ 清 flag。
#     每个既有读者（`is_paused()` / 发送链每一步）不用改，语义不变。
#   · 即使某个来源**忘了 pop**，也不会"提前恢复"（只会暂停更久）—— 宁可多停，不可早发。
# ══════════════════════════════════════════════════════════════════════════════
_DEPTH = {"n": 0, "tickets": (), "seq": 0}
_DEPTH_LOCK = None


def _depth_lock():
    global _DEPTH_LOCK
    if _DEPTH_LOCK is None:
        import threading
        _DEPTH_LOCK = threading.RLock()
    return _DEPTH_LOCK


def suspend_depth() -> int:
    """当前嵌套暂停层数（0 ＝ 没被暂停）。"""
    with _depth_lock():
        return int(_DEPTH["n"] or 0)


def suspend_push(reason: str = "") -> int:
    """**进一层暂停**（可嵌套）。返回**这一层的票号**（token）。

    ⚠️ 返回值从"层数"改成了"票号" —— 调用方要**留着它**，恢复时原样传给 `suspend_pop`：
        `tok = suspend_push` … `suspend_pop(tok)`。
      票号是**单调递增整数**，不是层数（层数用 `suspend_depth()` 取）。

    到 0→1 这一跳才真正写 flag（落盘可见），1→2 不重复写。
    """
    with _depth_lock():
        _DEPTH["seq"] = int(_DEPTH["seq"] or 0) + 1
        tok = _DEPTH["seq"]
        _DEPTH["n"] = int(_DEPTH["n"] or 0) + 1
        _DEPTH["tickets"] = tuple(_DEPTH["tickets"]) + (tok,)
        if _DEPTH["n"] == 1:
            set_paused_flag(True)
            if reason:
                try:
                    log_reason(reason)
                except Exception:  # noqa: BLE001
                    pass
        return tok


def suspend_pop(token=None, reason: str = "") -> int:
    """**退自己那一层**。返回剩余层数。

    ⛔ **只退持票的那一层** —— 这正是本机制存在的理由：
      · `token` 是自己 push 时拿到的票号 ⇒ 只把它从持票集合里去掉；
      · `token` 不在持票集合里（多退/退错对象）⇒ **什么都不做**（不减少别人的层数）；
      · `token=None`（**兼容旧调用**）⇒ 退**最近**那一层（后进先出），行为与裸计数器一致。
    层数归零才清 flag（非零不恢复）。
    """
    with _depth_lock():
        n = int(_DEPTH["n"] or 0)
        if n <= 0:
            return 0
        tks = list(_DEPTH["tickets"] or ())
        if token is None:
            # 兼容旧签名：退最近一层
            if tks:
                tks.pop()
        else:
            if int(token) not in tks:
                # 不是自己的票 ⇒ 不动（这正是"不解除别人的暂停"的落点）
                return n
            tks.remove(int(token))
        _DEPTH["tickets"] = tuple(tks)
        _DEPTH["n"] = len(tks)
        if _DEPTH["n"] == 0:
            set_paused_flag(False)
        return _DEPTH["n"]


def suspend_reset() -> int:
    """**强制归零**（判据/收尾用；也用于"用户显式恢复"这一路）。

    显式恢复＝用户意图高于嵌套计数 ⇒ 清零（含持票集合）并清 flag。
    票号序列**不回退**（否则旧票可能撞上新票）。
    """
    with _depth_lock():
        old = int(_DEPTH["n"] or 0)
        _DEPTH["n"] = 0
        _DEPTH["tickets"] = ()
        set_paused_flag(False)
        return old


#: 暂停原因的多行台账（`logs/` 下？不 —— 与 flag 同目录，便于人工查看）。
_REASON = {"last": ""}


def log_reason(reason: str) -> None:
    """把「谁按的暂停」记下来（同目录 `paused.reason`，人工可查；失败不影响主流程）。"""
    try:
        _REASON["last"] = str(reason or "")[:200]
        p = os.path.join(os.path.dirname(PAUSED_PATH), "paused.reason")
        with open(p, "w", encoding="utf-8") as f:
            f.write("%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), _REASON["last"]))
    except Exception:  # noqa: BLE001
        pass


def last_reason() -> str:
    """最近一次暂停原因（内存里的那份；进程重启后从文件读）。"""
    if _REASON["last"]:
        return _REASON["last"]
    try:
        p = os.path.join(os.path.dirname(PAUSED_PATH), "paused.reason")
        with open(p, encoding="utf-8") as f:
            ln = f.read().strip().splitlines()
        return ln[-1].split("\t", 1)[-1] if ln else ""
    except Exception:  # noqa: BLE001
        return ""
