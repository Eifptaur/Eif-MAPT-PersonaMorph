# -*- coding: utf-8 -*-
"""身份隔离桶 —— **"发错人"最直接的一条防线**（P0-3）。

**要解决的病**：身份识别失败（OCR 读不到绿底行、窗口被遮挡、库不可读…）时，
若**退化成"全局默认"或"上一次的值"**，就会把 A 会话的操作落到 B 会话上：

    「当前是谁」读不到 ⇒ 拿上一次的会话名继续用 ⇒ 用户在 B 会话里点了发送
    ⇒ 消息落到 A。

它之所以最难查：**失败路径上一切"看起来都正常"** —— 没有异常、没有错误码，
只是"名字沿用了一个恰好存在的旧值"。所以本条要求：

> 识别失败 ⇒ **新建一个一次性的隔离桶**，后续所有读写都带它；
> 桶与桶之间**永远不相等**，且**显式排除"未解析态"**参与相等比较。

**三个概念**：

| 概念 | 含义 |
|---|---|
| `pending_scope()` | **一次性的隔离桶**。每次识别失败各新建一个，**绝不复用**（`pending:<时间>:<序号>`） |
| 已解析身份 | 真正读到了"是谁"（`group:xxx`）—— **原样使用，不加前缀**（见 `resolved_scope()`） |
| `default` / 空串 | **未解析态的兜底名**。以它们参与相等比较 ⇒ 等于"谁都能对上" ⇒ 必须排除 |

**为什么不用 `time` 做唯一性**：同一毫秒内可能发生两次"识别失败"（同一轮里两条路径都读不到），
时间戳会给出**同一个桶名** ⇒ 两个本应隔离的会话被并进同一个桶。序号（进程内单调递增）
才是真正不重复的那个分量，时间戳只用来让人读日志时有个大概位置。

**为什么桶名要带前缀而不是纯随机**：故障现场要靠日志还原"这一串读写是不是同一个身份"，
纯随机串看不出"这是隔离态"还是"这是真会话名"。
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("persona-morph")

#: 未解析态的兜底名。以它参与相等比较 ⇒ 任何一侧是它都判"不同身份"。
UNRESOLVED = "default"

#: 隔离桶前缀。桶名形如 `pending:<毫秒时间戳>:<序号>`。
PENDING_PREFIX = "pending:"

#: 已解析身份的族标 —— **保留常量，不参与构造**。
#: ⛔ 不要拿它去拼 scope：scope 要与裸会话 key（`group:xxx`）直接相等比较，
#: 加前缀会让同一个会话出现两种写法 ⇒ 正常情况也被判成"不同身份"。
#: 它的作用是**命名这个概念**（"已解析"是一档状态），判状态请用 `is_resolved()`。
RESOLVED_PREFIX = "resolved:"

_seq_lock = threading.Lock()
_seq = 0


def _next_seq() -> int:
    global _seq
    with _seq_lock:
        _seq += 1
        return _seq


def pending_scope() -> str:
    """新建一个**一次性**隔离桶（识别失败时调用）。**每次调用都返回新桶，绝不复用。**

    用法：身份读不到的那一刻建一个，然后让**这一次操作的全部读写**都带上它 ——
    这样即便下游某处拿"桶名"去查缓存，也只会查到空（因为没有别的操作用过这个桶）。
    """
    _n = _next_seq()
    # 时间戳只为日志可读（`pending:1759512345678:7`）；唯一性由序号保证。
    import time as _t
    _scope = "%s%d:%d" % (PENDING_PREFIX, int(_t.time() * 1000), _n)
    log.info("身份未解析 ⇒ 新建隔离桶 %s", _scope)
    return _scope


def resolved_scope(name: str) -> str:
    """把**真正读到的**会话身份规整成已解析 scope。

    ★ **刻意不加前缀**：已解析身份**原样返回**（`group:xxx` 仍是 `group:xxx`）。
    为什么不像隔离桶那样加个 `resolved:` 族标：scope 是要拿去和**裸会话 key**
    （调用方手上的 `chat_key`、`store` 里的键）做相等比较的。加前缀 ⇒ 一个身份有
    "带前缀"和"不带前缀"两种写法 ⇒ 同一个会话被判成不同 ⇒ **正常情况也被 fail-closed**。
    区分"已解析 / 未解析"不需要靠前缀 —— 靠 `is_resolved()` 的三条判据即可。

    空串 / `default` / 已是隔离桶 ⇒ **原样返回**（它们就是未解析态，规整不该把它们变成已解析）。
    """
    _n = str(name or "").strip()
    if not _n or _n == UNRESOLVED or _n.startswith(PENDING_PREFIX):
        return _n
    return _n


def is_pending(scope: str) -> bool:
    """这个 scope 是不是隔离桶（未解析态）？"""
    return str(scope or "").startswith(PENDING_PREFIX)


def is_resolved(scope: str) -> bool:
    """这个 scope 是不是**已解析**的确定身份？

    判据三条同时成立：非空、不是 `default`、不是隔离桶。
    ⛔ 刻意**不要求** `resolved:` 前缀 —— 历史调用点传进来的裸会话名（`group:xxx`）
       也是已解析身份，要求前缀会把它们全判成未解析 ⇒ 全线 fail-closed。
       前缀只是**新建**身份时的写法，不是判定已解析的**前提**。
    """
    _s = str(scope or "").strip()
    if not _s or _s == UNRESOLVED:
        return False
    if _s.startswith(PENDING_PREFIX):
        return False
    return True


def is_same_resolved_scope(a: str, b: str) -> bool:
    """两个 scope 是不是**同一个已解析身份**？

    **任一侧未解析 ⇒ 一律判不同**（`False`）。这一条是本模块存在的全部意义：
    "不知道是谁"不能与任何东西相等 —— 包括与另一个"不知道是谁"相等。
    （两边都未解析时判 `False`，看起来"保守得过头"，但方向是对的：
      **宁可多建一个桶、多算一次，也不能让 A 的操作认成 B 的**。）
    """
    if not is_resolved(a) or not is_resolved(b):
        return False
    return str(a).strip() == str(b).strip()


def scope_of(name: str) -> str:
    """调用点的**统一入口**：给一个"读到的身份"（可能是空串＝没读到），返回该用的 scope。

    - 读到了 ⇒ 已解析 scope（可直接参与相等比较）；
    - 读不到 ⇒ **新建隔离桶**。

    ⛔ 调用方**不要自己判空再决定** —— 每一处自己判都会漏一处，漏掉的那一处就是
       那条"沿用上一次的值"的路径。收敛到一个名字里，语义才守得住。
    """
    _s = resolved_scope(name)
    if is_resolved(_s):
        return _s
    return pending_scope()


__all__ = ["UNRESOLVED", "PENDING_PREFIX", "RESOLVED_PREFIX",
           "pending_scope", "resolved_scope", "scope_of",
           "is_pending", "is_resolved", "is_same_resolved_scope"]
