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
    """持久化水位：{chat_key: int}。原子写；默认只前进。"""

    def __init__(self, path: str):
        self.path = str(path)
        self._dirty = False
        self.data: dict = {}
        self.load()

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
        d = persist.load_or_quarantine(self.path, _BAD)
        if d is _BAD:
            self.data = {}
        elif not isinstance(d, dict):
            log.warning("水位表顶层不是对象（形状不对）⇒ 已按坏档留证：%s",
                        persist.quarantine(self.path) or "留证失败")
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
            return int(self.data.get(str(chat_key), default) or 0)
        except Exception:
            return int(default or 0)

    def set(self, chat_key: str, seq, forward_only: bool = True) -> int:
        """写入水位。forward_only=True（默认）时不允许回退 —— 水位倒退会导致重复处理。"""
        try:
            s = int(seq or 0)
        except Exception:
            return self.get(chat_key)
        k = str(chat_key)
        cur = self.get(k)
        if forward_only and s <= cur:
            return cur
        self.data[k] = s
        self._dirty = True
        return s

    def flush(self) -> bool:
        """原子落盘（`persist.atomic_write_json`：tmp 名带 pid+随机后缀 + `os.replace`）；没有变化就不写。

        V-R9-22：老写法共用 `<path>.tmp` ⇒ 并发/多进程写会互相穿插出坏 JSON；失败返回 False（不吞）。
        """
        if not self._dirty:
            return True
        if persist.atomic_write_json(self.path, self.data, indent=1, sort_keys=True):
            self._dirty = False
            return True
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
                wm.flush()
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
                wm.flush()
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
