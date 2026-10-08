# -*- coding: utf-8 -*-
"""有界重试的**统一口径**：只重试"可重试"的错误 + 显式退避表 + 每轮重查代数。

病（三个各自独立、但常一起出现）：
  1. **什么都重试** —— 把"路径不存在""参数非法""磁盘满"也当瞬态反复试，
     白等一轮又一轮，最后失败原因还被重试噪声盖住（看不到真因）。
  2. **退避是算出来的** —— `delay *= 1.6` 这类经验公式：退避节奏**随实现而变**，
     测试钉不住、换个人改一下就飘。
  3. **重试点不复核前提** —— 重试的这几百毫秒里，外部状态可能**已经变了**
     （文件被换成新版本、会话被切走、作业被取消）⇒ 第二轮拿着**过期的前提**继续干，
     比不重试更糟（可能把新状态覆盖掉）。

口径（三条一起用）：
  · `RETRYABLE_ERRNOS`：**白名单**（不在名单里的一次都不重试）；Windows 的 `winerror`
    同样有一份白名单（`5 / 32 / 33`：拒绝访问 / 被别的进程占用 / 区域被锁）。
  · `BACKOFF_MS`：**显式退避表**（毫秒），按轮次取；表走完 ⇒ 用最后一档（不无限翻倍）。
  · `call_with_retry(..., gen=None, gen_getter=None)`：**每轮开始前**用 `gen_getter()` 取
    当前代数与调用方给的 `gen` 对比；不一致 ⇒ **立刻停**（前提已变，重试没意义）。

⚠️ 本模块**不做任何 I/O 之外的事**，也不记全局状态 ⇒ 谁都能用、判据好写。
"""
from __future__ import annotations

import errno
import logging
import time

log = logging.getLogger("persona-morph")

#: **只重试这些 errno**（POSIX 语义）。不在名单里 ⇒ 一次都不重试（如 ENOENT/EINVAL/ENOSPC）。
RETRYABLE_ERRNOS = frozenset({
    errno.EPERM,   # 操作不允许（Windows 上常表现为"文件被占用"）
    errno.EACCES,  # 权限被拒（杀毒/索引器短暂持有句柄）
    errno.EBUSY,   # 设备或资源忙
    errno.EEXIST,  # 已存在（某些替换语义下的瞬态）
})

#: Windows `winerror` 白名单：5=拒绝访问、32=被别的进程占用、33=区域被锁。
RETRYABLE_WINERRORS = frozenset({5, 32, 33})

#: **显式退避表**（毫秒）。轮次超出表长 ⇒ 用最后一档（不无限翻倍）。
BACKOFF_MS = (25, 50, 100, 200, 400, 800)


def is_retryable(exc) -> bool:
    """这个异常**值不值得**重试（白名单判定；拿不准一律 False）。

    ⛔ 不按**消息文本**猜 —— 猜关键词是我们吃过大亏的脆判据。只看 `errno` / `winerror`。
    """
    try:
        e = exc
        we = getattr(e, "winerror", None)
        if we is not None and int(we) in RETRYABLE_WINERRORS:
            return True
        en = getattr(e, "errno", None)
        if en is not None and int(en) in RETRYABLE_ERRNOS:
            return True
        # `OSError` 的 `args[0]` 有时直接放 errno（老式构造）
        if isinstance(e, OSError) and e.args and isinstance(e.args[0], int):
            if int(e.args[0]) in RETRYABLE_ERRNOS:
                return True
        # Windows 上 `os.replace` 撞占用常抛 `PermissionError` 但 errno 缺失
        if isinstance(e, PermissionError):
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def backoff_ms(round_idx: int) -> int:
    """第 `round_idx` 轮（**从 0 起**）用多少毫秒退避。超出表长 ⇒ 最后一档。"""
    try:
        i = max(0, int(round_idx))
    except Exception:  # noqa: BLE001
        i = 0
    return int(BACKOFF_MS[min(i, len(BACKOFF_MS) - 1)])


def call_with_retry(fn, *, gen=None, gen_getter=None, tries=None,
                    total_budget_s: float | None = None, what: str = ""):
    """带**显式退避 + 代数复核**的重试。

    参数：
      · `fn`：无参可调用；**返回什么就返回什么**（抛异常才进重试判定）。
      · `gen`：调用方手里那份"代数/版本"（任意可比较对象；`None` 表示不复核）。
      · `gen_getter`：无参可调用，取**当前**代数。给了它且 `gen` 不是 None ⇒ 每轮开始前比对；
        不一致 ⇒ 抛 `StaleGeneration`（**不重试**，因为前提已经变了）。
      · `tries`：最多试几轮（默认 `len(BACKOFF_MS)+1`）。
      · `total_budget_s`：总耗时上限（秒）；超了就不再等下一轮。
      · `what`：日志用的一句话（如"换档 xxx.json"）。

    返回 `(ok, value, last_exc)`：
      · `ok=True` ⇒ `value` 是 `fn` 的返回值；
      · `ok=False` ⇒ `value=None`、`last_exc` 是最后一次异常（**不可重试**或**重试耗尽**都走这里）。
    """
    n = int(tries) if tries else (len(BACKOFF_MS) + 1)
    t0 = time.monotonic()
    last = None
    for i in range(max(1, n)):
        # ★ 每轮开始前**重查代数**：这一轮拿到的前提是否还成立
        if (gen is not None) and (gen_getter is not None):
            try:
                if gen_getter() != gen:
                    raise StaleGeneration("重试前代数已变（%r ⇒ %r）⇒ 停止重试"
                                          % (gen, gen_getter()))
            except StaleGeneration:
                raise
            except Exception:  # noqa: BLE001 — 取不到代数 ⇒ fail-closed，不冒险继续
                raise StaleGeneration("取不到当前代数 ⇒ 不敢带着过期前提重试")
        try:
            return True, fn(), None
        except Exception as e:  # noqa: BLE001
            last = e
            if not is_retryable(e):
                break # 不可重试 ⇒ 立刻停（别用重试噪声盖住真因）
            if i >= n - 1:
                break
            if total_budget_s is not None and (time.monotonic() - t0) >= float(total_budget_s):
                break
            time.sleep(backoff_ms(i) / 1000.0)
    if last is not None:
        log.debug("重试结束（%s）：%s", what or "未注明", last)
    return False, None, last


class StaleGeneration(Exception):
    """重试期间"前提"变了（代数不一致）⇒ 不再重试，由调用方决定怎么收场。"""
