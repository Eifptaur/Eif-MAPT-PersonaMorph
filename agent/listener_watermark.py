"""监听水位（W2）：**落盘 + 成功才推进 + 失败重试留痕 + 每会话串行**。

现状问题（2026-09-13 取证 `scripts/persona_morph.py:2374-2443`）：
  - `since_seq` 只活在内存里 ⇒ **重启后水位丢失**，启动时又拿 `latest_seq` 当起点
    ⇒ 停机期间的新消息**既不补也不重放**（静默丢）；
  - `since_seq[wxid] = max_seq` 在批处理末尾**无条件推进** ⇒ 只要下游没真落库，消息就没了；
  - 失败没有重试、没有留痕；同一会话也没有"同一时刻只有一个处理者"的显式保证。

本模块给出三件事：
  1. `Watermark`：`{chat_key: seq}` 落盘（**原子写** temp+os.replace），只前进不回退（除非显式 reset）；
  2. `process_batch()`：逐条交给 handler，**返回真值才算成功**并推进水位；失败**重试 3 次**（线性退避）、
     仍失败则写 **dead-letter**（`listener_failed.jsonl`）**并把水位越过它**——绝不静默跳过、也绝不卡死队列；
  3. 每会话一把锁：同一 chat_key 的处理**串行**（并发调用者排队），保证顺序与水位单调。

⛔ **假定：水位表只有一个写者（V-R14-5，第十四轮审计实测，P3，按设计确认）**
  落盘是"**整表** 覆盖写"（`persist.atomic_write_json` 写的是 `self.data` 全量快照）⇒ 两个进程各持一份内存态
  同时写，后写者会把先写者的键整个盖掉。审计实测（两个进程各写 7 个群 × 150 次 flush）：期望 14 个键、
  实测只剩 **8 个** —— 文件**永远合法**（唯一临时名 + `os.replace`，没有半截档、无遗留临时档），但**会丢键**
  （水位回退到更早位置 ⇒ 重放或漏判）。
  为什么现实中打不到：产品有**单实例锁**（`agent/single_instance.py`），监听循环只有一个进程 ⇒ 内存态唯一。
  ⇒ **要支持"两个监听进程 / 两个号各跑一个进程"之前，必须先把这里换成"读-改-写 + 跨进程锁"**
  （`chat_lock()` 是**进程内**的 `threading.RLock`，跨进程不管用）。这份口径写在代码里，不靠口口相传。

判据：`py -3 scripts/watermark_selftest.py`（不需要微信在跑）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import persist

log = logging.getLogger("persona-morph")

DEFAULT_RETRY = 3
DEFAULT_RETRY_SLEEP = 0.5
_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


def chat_lock(chat_key: str) -> threading.RLock:
    """每会话一把锁 —— 同一 chat 的处理不会并发（跨调用者共享）。"""
    with _LOCKS_GUARD:
        lk = _LOCKS.get(chat_key)
        if lk is None:
            lk = threading.RLock()
            _LOCKS[chat_key] = lk
        return lk


class Watermark:
    """持久化水位：`{账号维|chat_key: int}`（账号维可为空）。原子写；默认只前进。

    V-R10-30（2026-09-21）：老实现**只有 chat_key 一维** ⇒ 切号后两个账号的同一个群
    共用一个序号格子（`group:<wxid>`）：A 号把水位推到 900，切到 B 号后 B 号同一群的
    1~900 号消息会被判成"处理过了"⇒ **静默不回**；切回 A 号又被 B 号的低水位拖着重放。
    ⇒ 现在按**账号**分命名空间（`account|group:<wxid>`）：
      · `account` 认不出来时用**保留名 `?`**（V-R11-5：绝不回退到"无前缀"的老键名 —— 那正是
        升级前两个号共用的那一格）；
      · 老文件里的无前缀键**原样留着但不参与读写**，新命名空间从 0 起 ⇒ 上层"水位 0 就对齐到最新"
        （`persona_morph.py` 那段）保证**不重放历史、也不会漏掉新消息**；
      · 切号只换命名空间（`set_account`），两个账号的格子同时在文件里，切回来继续用。
    """

    def __init__(self, path: str, account: str = ""):
        self.path = str(path)
        self.account = str(account or "")
        self._dirty = False
        self._refuse_overwrite = False      # V-R10-23：坏档留证失败 ⇒ 置 True，flush 拒绝写
        self.fail_count = 0                 # V-R10-30：flush 真失败（或拒写）的累计次数
        self.last_error = ""
        self.data: dict = {}
        self.load()

    def set_account(self, account) -> str:
        """切号：只换命名空间。**不写盘**（切号本身没改任何水位）。"""
        a = str(account or "")
        if a != self.account:
            self.account = a
        return self.account

    def _ns(self, chat_key) -> str:
        """给 chat_key 加上账号前缀。**认不出账号时用保留名 `?`**（见下面的 V-R11-5 说明）。

        ⛔ 2026-09-21 修（第十一轮 **V-R11-5** · P2）：老写法"认不出账号 ⇒ 不加前缀、沿用老键名"，
        而老键名在多账号机器上的语义恰好是「**升级前两个号共用的那一格**」——`db_account()` 拿不到
        账号（新 adapter 还没开库 / 旧 adapter 的 `_db` 被 `_release_adapter` 置空）时就会塌回老键，
        把 V-R10-30 的原始症状（两号水位互相污染）重新拿出来用。
        ⇒ 现在**永远带前缀**，认不出账号就用保留名 `?`。代价是升级后第一枪（以及账号时有时无的
        机器）会走一次"水位 0 ⇒ 对齐到最新"（与账号可识别时的行为一致）——**不重放历史、不漏新消息**。
        """
        a = self.account or "?"
        return "%s|%s" % (a, str(chat_key))

    # ---- 读写 ----
    def load(self) -> dict:
        """读水位表：**按条目校验**，一条坏值只丢那一条。

        V-R9-18 的原始症状：老写法 `{str(k): int(v or 0) for k, v in d.items()}`
        ——**只要有一个值不是能转 int 的东西**（手工改过、被第三方工具动过），`int()` 抛异常
        ⇒ 整表归零 ⇒ 下一次 `flush()` 只写回本次动过的那个键 ⇒ **别的会话的水位全没了**
        （要么重放、要么静默丢）。现在：坏条目单独丢、日志里如实说丢了几条，其余键原样保留。

        整档读不出来（解析失败 / 顶层不是对象）时走 `persist.load_or_quarantine`：
        坏档改名 `.bad.<时间戳>` 留证，不让下一次 flush 把它静默覆盖掉。
        """
        _BAD = object()                      # 哨兵：分得清"读到的东西"与"走的默认值"
        d, ok_overwrite = persist.load_checked(self.path, _BAD)
        # V-R10-23：原档读不出来**且留证失败** ⇒ 它还留在原地；这一次 flush 写出去就是
        # "整表被空表覆盖"（水位全丢 ⇒ 要么重放、要么静默丢）。⇒ 把"禁止覆盖"顶到 flush 上。
        self._refuse_overwrite = not ok_overwrite
        if d is _BAD:
            self.data = {}
        elif not isinstance(d, dict):
            kept = persist.quarantine(self.path)
            if not kept:
                self._refuse_overwrite = True
            log.warning("水位表顶层不是对象（形状不对）⇒ %s",
                        ("已按坏档留证：%s" % kept) if kept else "**留证失败 ⇒ 原档保持原样、禁止覆盖**")
            self.data = {}
        else:
            data, dropped = {}, 0
            for k, v in d.items():
                try:
                    data[str(k)] = int(v)
                except (TypeError, ValueError):
                    dropped += 1             # 只丢这一条，**不**把整表归零
            if dropped:
                log.warning("水位表有 %d 条坏条目（值不是整数）已丢弃、其余 %d 条保留：%s",
                            dropped, len(data), self.path)
            self.data = data
        self._dirty = False
        return self.data

    def get(self, chat_key: str, default: int = 0) -> int:
        try:
            return int(self.data.get(self._ns(chat_key), default) or 0)
        except Exception:
            return int(default or 0)

    def set(self, chat_key: str, seq, forward_only: bool = True) -> int:
        """写入水位。forward_only=True（默认）时不允许回退 —— 水位倒退会导致重复处理。

        ⛔ 2026-09-21 修（第十一轮 **V-R11-1** · P1）：`k` 已经带过账号前缀，**不能再喂给
        `get()`**（`get()` 内部还会套一次 `_ns`）——老写法 `cur = self.get(k)` 让 `cur` **恒为 0**
        ⇒ 账号维一旦开启，"默认只前进"这道闸**当场失效**（任何更小的值都能写进去＝水位倒退＝
        从旧位置重放）。现在直接查表（键就是 `k`），并把这个"自己写的键自己读"的语义写死在这里。
        """
        try:
            s = int(seq or 0)
        except Exception:
            return self.get(chat_key)
        k = self._ns(chat_key)
        try:
            cur = int(self.data.get(k, 0) or 0)      # ⬅ 键已带前缀，**直接查表**，别再走 get()
        except (TypeError, ValueError):
            cur = 0
        if forward_only and s <= cur:
            return cur
        self.data[k] = s
        self._dirty = True
        return s

    def flush(self) -> bool:
        """原子落盘（`persist.atomic_write_json`：tmp 名带 pid+随机后缀 + `os.replace`）；没有变化就不写。

        V-R9-22：老写法共用 `<path>.tmp` ⇒ 并发/多进程写会互相穿插出坏 JSON；失败返回 False（不吞）。
        V-R10-23：坏档**留证失败**（原档还在原地）时**拒绝写**——写出去就是整表被覆盖。
        ⚠️ V-R14-5（第十四轮，P3 按设计确认）：写的是**整表快照** ⇒ **本表假定单写者**（单实例锁保证）；
           真要两进程共享，得改成"读-改-写 + 跨进程锁"（详见文件头那段）。
        """
        if not self._dirty:
            return True
        if getattr(self, "_refuse_overwrite", False):
            log.warning("水位表读不出来且留证失败 ⇒ **拒绝覆盖**（本次 flush 不落盘）：%s", self.path)
            self.fail_count += 1
            self.last_error = "坏档留证失败 ⇒ 拒绝覆盖"
            return False
        if persist.atomic_write_json(self.path, self.data, indent=1, sort_keys=True):
            self._dirty = False
            return True
        self.fail_count += 1
        self.last_error = self.last_error or "原子写失败（目标被占用 / 磁盘不可写？）"
        return False


_FLUSH_WARN_AT: dict = {}


def flush_checked(wm: "Watermark", log=None, why: str = "", warn_gap: float = 60.0) -> bool:
    """**看返回值**地 flush 水位表（V-R10-30）。

    为什么要有它：`persist.atomic_write_json` 在"目标被以不共享 DELETE 的方式占用"或
    真并发时**必然** `WinError 5`（审计实测：两进程同时 flush 双双失败）——老代码 8 个
    调用点全是 `wm.flush()` **丢掉返回值**，于是水位明明没落盘、程序却当成写成功了，
    重启后从旧水位重放（重复回复）或被上层误判。

    失败时：①`wm.fail_count/last_error` 留痕（控制台/自检可读）②按 `warn_gap` 限频告警
    （每 60 秒至多一条，不刷屏）③返回 False 交给调用方处置。**不抛异常**。
    """
    try:
        ok = bool(wm.flush())
    except Exception as e:                  # flush 本身崩了也算失败，不许把异常抛给主循环
        ok = False
        try:
            wm.fail_count += 1
            wm.last_error = "%s: %s" % (type(e).__name__, e)
        except Exception:
            pass
    if ok:
        return True
    try:
        now = time.time()
        key = str(getattr(wm, "path", "") or "")
        if now - float(_FLUSH_WARN_AT.get(key, 0) or 0) >= float(warn_gap):
            _FLUSH_WARN_AT[key] = now
            _msg = ("水位表**没写进磁盘**（%s）—— 序号已在内存里，重启会从旧水位接着读"
                    "（可能重放/漏判）：%s") % (why or "flush 失败", getattr(wm, "last_error", "") or "?")
            if log is not None:
                if hasattr(log, "warning"):          # logging.Logger
                    log.warning("%s", _msg)
                else:                                # process_batch 那种 (level, fmt, *args) 回调
                    log("warn", "%s", _msg)
    except Exception:
        pass
    return False


def dead_letter(path: str, record: dict) -> bool:
    """失败留痕（一行一条 JSON）；写不进去也不能影响主流程。"""
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def process_batch(chat_key: str, items, handler, wm: "Watermark", log=None,
                  max_retry: int = DEFAULT_RETRY, retry_sleep: float = DEFAULT_RETRY_SLEEP,
                  deadletter_path: str = "", seq_key: str = "sort_seq", id_key: str = "mid",
                  sleep=time.sleep) -> dict:
    """按序处理一批消息；**成功才推进水位**。

    handler(item) 返回真值＝这条已被下游接受（例如 append_incoming 返回了 entry）；
    抛异常或返回假值＝失败 ⇒ 重试至多 max_retry 次（线性退避）⇒ 仍失败写 dead-letter，
    水位**越过该条**（否则毒消息会让队列永远卡住），日志里明确写"已越过、不再重试"。

    返回 {"processed": n, "retried": r, "failed": f, "skipped": s, "advanced_to": seq}
    """
    stats = {"processed": 0, "retried": 0, "failed": 0, "skipped": 0,
             "advanced_to": wm.get(chat_key)}
    log = log or (lambda *a, **k: None)

    def _seq(it):
        try:
            return int(it.get(seq_key) or 0)
        except Exception:
            return 0

    def _mid(it):
        try:
            return it.get(id_key)
        except Exception:
            return None

    with chat_lock(chat_key):          # 同一会话串行：并发调用者在这里排队
        for it in list(items or []):
            seq = _seq(it)
            if seq and seq <= wm.get(chat_key):
                stats["skipped"] += 1       # 已在处理过的范围内（重放保护）
                continue
            ok, last_err = False, ""
            for attempt in range(1, max(1, int(max_retry)) + 1):
                try:
                    ok = bool(handler(it))
                    if ok:
                        break
                    last_err = "handler 返回假值"
                except Exception as e:      # 单条失败不许打断整批
                    last_err = "%s: %s" % (type(e).__name__, e)
                if attempt < max_retry:
                    stats["retried"] += 1
                    log("warn", "chat=%s mid=%s 第 %d/%d 次处理失败（%s），%.2fs 后重试",
                        chat_key, _mid(it), attempt, max_retry, last_err, retry_sleep * attempt)
                    try:
                        sleep(retry_sleep * attempt)
                    except Exception:
                        pass
            if ok:
                stats["processed"] += 1
                wm.set(chat_key, seq)
                flush_checked(wm, log=log, why="批内成功推进（chat=%s）" % chat_key)
            else:
                stats["failed"] += 1
                rec = {"ts": int(time.time() * 1000), "chat": chat_key, "mid": _mid(it),
                       "seq": seq, "attempts": max_retry, "error": last_err,
                       "note": "重试 %d 次仍失败，已越过；原始报文见本文件同名字段" % max_retry}
                try:
                    rec["item"] = json.loads(json.dumps(it, ensure_ascii=False, default=str))
                except Exception:
                    rec["item"] = None
                if deadletter_path:
                    dead_letter(deadletter_path, rec)
                log("error", "chat=%s mid=%s seq=%s 重试 %d 次仍失败（%s）⇒ 已记入 %s 并**越过**该条",
                    chat_key, _mid(it), seq, max_retry, last_err, deadletter_path or "(未配置 dead-letter)")
                wm.set(chat_key, seq)
                flush_checked(wm, log=log, why="越过毒消息推进（chat=%s）" % chat_key)
        stats["advanced_to"] = wm.get(chat_key)
    return stats


def make_log(logger):
    """把 logging.Logger 适配成 process_batch 要的 (level, fmt, *args) 回调。"""
    def _l(level, fmt, *args):
        try:
            getattr(logger, level if level in ("warn", "error", "info") else "info")(fmt, *args)
        except Exception:
            pass
    return _l
