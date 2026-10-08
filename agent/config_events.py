"""配置变更事件：**写配置即广播**，订阅方不必轮询。

为什么要单独一个模块：
配置的消费方散布在很多处（Qt 壳重绘、后台线程读开关、控制台回填）。它们都需要在
"配置被换掉"的那一刻**立刻**知道，而不是各自定时去 `get_config()` 轮询 ——
轮询要么慢（间隔大 ⇒ 界面/行为滞后），要么贵（间隔小 ⇒ 白耗 CPU），
而且漏读一次就是"界面显示的开关与实际生效的不一致"。

设计要点：
· **进程内**回调表，不做跨进程（跨进程那份由 `config.py` 的内容指纹兜底，
  见 `_stamp`：别的进程改了档，`get_config()` 比对指纹会重读）。
· **派发必须容错**：某个订阅者抛异常**不许**影响别的订阅者，更不许让
  "配置已经改好、只是通知失败"被当成"保存失败"。
· **事件载荷只带"变了哪些键"**，不带整份配置值（避免把口令一类敏感值散到各处日志里）；
  订阅方要新值就自己 `get_config()`。
· **线程安全**：订阅/退订/派发都走同一把锁取快照，派发在锁外进行
  （防订阅者回调里再订阅/退订造成死锁）。

触发点（谁调用 `emit`）：
· `config.set_config()`  —— 控制台保存后换掉内存那份；
· `config.reload_config()` —— 显式失效重读（含 `get_config()` 检测到指纹变了的那次重读）。
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

#: 事件名。订阅方按这个字符串挂号；写死常量防各处拼错。
CONFIG_CHANGED = "config-changed"

_lock = threading.RLock()
_subs: list = []          # [(token, fn)]，token 用来退订（同一个 fn 可能被多次订阅）


def subscribe(fn) -> int:
    """订阅配置变更，返回**退订令牌**。`fn` 收到一个参数：变了哪些键的列表。

    ⛔ 同一个函数**可以被多次订阅**（例如同一个对象在两个生命周期里各挂一次），
    所以退订用令牌而不是函数本身 —— 用函数退订会误退掉另一处挂的那份。
    """
    if not callable(fn):
        raise TypeError("订阅者必须可调用：%r" % (fn,))
    with _lock:
        tok = len(_subs) + 1
        # 防令牌撞车（退订后 len 会减小，可能重新用上旧号）
        used = {t for t, _ in _subs}
        while tok in used:
            tok += 1
        _subs.append((tok, fn))
        return tok


def unsubscribe(token: int) -> bool:
    """按令牌退订；退到返回 True，令牌不存在返回 False（**幂等**，重复退不报错）。"""
    with _lock:
        for i, (t, _fn) in enumerate(_subs):
            if t == token:
                _subs.pop(i)
                return True
        return False


def emit(changed=None) -> int:
    """广播 `config-changed`；返回**成功投递的订阅者数**。

    `changed` ＝ 变了哪些配置键（点号路径列表）；拿不到就传空列表。
    ⛔ 单个订阅者抛异常只记日志（debug 级，避免刷屏）、**不影响其余订阅者**，
    也不把异常抛给调用方 —— 配置本身已经写好了，通知失败不该回滚"保存成功"这个事实。
    """
    keys = list(changed or [])
    with _lock:
        targets = [fn for _t, fn in _subs]   # 取快照，锁外派发
    ok = 0
    for fn in targets:
        try:
            fn(keys)
            ok += 1
        except Exception as e: # noqa: BLE001
            log.debug("配置变更订阅者回调失败（已忽略）：%s", e)
    return ok


def subscriber_count() -> int:
    """当前订阅者数（自检/排障用）。"""
    with _lock:
        return len(_subs)


def reset() -> None:
    """清空所有订阅（自检隔离用：一个用例不该被上一个用例挂的回调影响）。"""
    with _lock:
        _subs.clear()
