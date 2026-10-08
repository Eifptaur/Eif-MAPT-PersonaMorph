"""监听水位（W2）：**落盘 + 成功才推进 + 失败重试留痕 + 每会话串行**。

现状问题：
  - `since_seq` 只活在内存里 ⇒ **重启后水位丢失**，启动时又拿 `latest_seq` 当起点
    ⇒ 停机期间的新消息**既不补也不重放**（静默丢）；
  - `since_seq[wxid] = max_seq` 在批处理末尾**无条件推进** ⇒ 只要下游没真落库，消息就没了；
  - 失败没有重试、没有留痕；同一会话也没有"同一时刻只有一个处理者"的显式保证。

本模块给出三件事：
  1. `Watermark`：`{chat_key: seq}` 落盘（**原子写** temp+os.replace），只前进不回退（除非显式 reset）；
  2. `process_batch()`：逐条交给 handler，**返回真值才算成功**并推进水位；失败**重试 3 次**（线性退避）、
     仍失败则写 **dead-letter**（`listener_failed.jsonl`）**并把水位越过它**——绝不静默跳过、也绝不卡死队列；
  3. 每会话一把锁：同一 chat_key 的处理**串行**（并发调用者排队），保证顺序与水位单调。

⛔ **假定：水位表只有一个写者**
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
#: B7：每会话保留的"已见 mid"个数（环形）。与 `HISTORY_KEEP` 同量级。
#: 溢出丢最旧的 —— 被丢的靠**位置水位**兜底（水位单调前进，天然是"很久以前的一定处理过"的粗证据）。
SEEN_KEEP = 200
#: B7：`seen` 在平铺表里的键后缀（与水位键共存一个 dict，靠值类型 int/list 分流）。
_SEEN_SUFFIX = "::seen"
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

    老实现**只有 chat_key 一维** ⇒ 切号后两个账号的同一个群
    共用一个序号格子（`group:<wxid>`）：A 号把水位推到 900，切到 B 号后 B 号同一群的
    1~900 号消息会被判成"处理过了"⇒ **静默不回**；切回 A 号又被 B 号的低水位拖着重放。
    ⇒ 现在按**账号**分命名空间（`account|group:<wxid>`）：
      · `account` 认不出来时用**保留名 `?`**（绝不回退到"无前缀"的老键名 —— 那正是
        升级前两个号共用的那一格）；
      · 老文件里的无前缀键**原样留着但不参与读写**，新命名空间从 0 起 ⇒ 上层"水位 0 就对齐到最新"
        （`persona_morph.py` 那段）保证**不重放历史、也不会漏掉新消息**；
      · 切号只换命名空间（`set_account`），两个账号的格子同时在文件里，切回来继续用。
    """

    def __init__(self, path: str, account: str = ""):
        self.path = str(path)
        self.account = str(account or "")
        self._dirty = False
        self._refuse_overwrite = False # 坏档留证失败 ⇒ 置 True，flush 拒绝写
        self.fail_count = 0 # flush 真失败（或拒写）的累计次数
        self.last_error = ""
        self.data: dict = {}
        self._seen: dict = {} # B7：{带账号前缀的键::seen: [已见 mid …]}，环形
        self.load()

    def set_account(self, account) -> str:
        """切号：只换命名空间。**不写盘**（切号本身没改任何水位）。"""
        a = str(account or "")
        if a != self.account:
            self.account = a
        return self.account

    def _ns(self, chat_key) -> str:
        """给 chat_key 加上账号前缀。**认不出账号时用保留名 `?`**。

        ⛔ 老写法"认不出账号 ⇒ 不加前缀、沿用老键名"，
        而老键名在多账号机器上的语义恰好是「**升级前两个号共用的那一格**」——`db_account()` 拿不到
        账号（新 adapter 还没开库 / 旧 adapter 的 `_db` 被 `_release_adapter` 置空）时就会塌回老键，
        把原始症状（两号水位互相污染）重新拿出来用。
        ⇒ 现在**永远带前缀**，认不出账号就用保留名 `?`。代价是升级后第一枪（以及账号时有时无的
        机器）会走一次"水位 0 ⇒ 对齐到最新"（与账号可识别时的行为一致）——**不重放历史、不漏新消息**。
        """
        a = self.account or "?"
        return "%s|%s" % (a, str(chat_key))

    # ---- 读写 ----
    def load(self) -> dict:
        """读水位表：**按条目校验**，一条坏值只丢那一条。

         的原始症状：老写法 `{str(k): int(v or 0) for k, v in d.items()}`
        ——**只要有一个值不是能转 int 的东西**（手工改过、被第三方工具动过），`int()` 抛异常
        ⇒ 整表归零 ⇒ 下一次 `flush()` 只写回本次动过的那个键 ⇒ **别的会话的水位全没了**
        （要么重放、要么静默丢）。现在：坏条目单独丢、日志里如实说丢了几条，其余键原样保留。

        整档读不出来（解析失败 / 顶层不是对象）时走 `persist.load_or_quarantine`：
        坏档改名 `.bad.<时间戳>` 留证，不让下一次 flush 把它静默覆盖掉。
        """
        _BAD = object() # 哨兵：分得清"读到的东西"与"走的默认值"
        d, ok_overwrite = persist.load_checked(self.path, _BAD)
        # 原档读不出来**且留证失败** ⇒ 它还留在原地；这一次 flush 写出去就是
        # "整表被空表覆盖"（水位全丢 ⇒ 要么重放、要么静默丢）。⇒ 把"禁止覆盖"顶到 flush 上。
        self._refuse_overwrite = not ok_overwrite
        if d is _BAD:
            self.data = {}
            self._seen = {}
        elif not isinstance(d, dict):
            kept = persist.quarantine(self.path)
            if not kept:
                self._refuse_overwrite = True
            log.warning("水位表顶层不是对象（形状不对）⇒ %s",
                        ("已按坏档留证：%s" % kept) if kept else "**留证失败 ⇒ 原档保持原样、禁止覆盖**")
            self.data = {}
            self._seen = {}
        else:
            data, seen, dropped = {}, {}, 0
            for k, v in d.items():
                ks = str(k)
                # ── B7：`seen` 用**同表另一种值类型**（list）表示已见 mid 环 ──
                #   键后缀固定 `::seen`，值是一个整数列表。这样：
                #   · 不新开文件、不新开字段 ⇒ 老的 `{键: 整数}` 档照常读；
                #   · 老的 `int(v)` 校验遇到 list 会当"坏条目"丢掉 ⇒ **所以必须先分流**。
                if ks.endswith(_SEEN_SUFFIX):
                    if isinstance(v, list):
                        buf = []
                        for x in v:
                            m = self._mid_key(x)
                            if m is not None:
                                buf.append(m)
                        seen[ks] = buf[-SEEN_KEEP:] if len(buf) > SEEN_KEEP else buf
                    else:
                        dropped += 1
                    continue
                try:
                    data[ks] = int(v)
                except (TypeError, ValueError):
                    dropped += 1 # 只丢这一条，**不**把整表归零
            if dropped:
                log.warning("水位表有 %d 条坏条目（值不是整数）已丢弃、其余 %d 条保留：%s",
                            dropped, len(data), self.path)
            self.data = data
            self._seen = seen
        self._dirty = False
        return self.data

    def get(self, chat_key: str, default: int = 0) -> int:
        try:
            return int(self.data.get(self._ns(chat_key), default) or 0)
        except Exception:
            return int(default or 0)

    def set(self, chat_key: str, seq, forward_only: bool = True) -> int:
        """写入水位。forward_only=True（默认）时不允许回退 —— 水位倒退会导致重复处理。

        ⛔ `k` 已经带过账号前缀，**不能再喂给
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
            cur = int(self.data.get(k, 0) or 0) # ⬅ 键已带前缀，**直接查表**，别再走 get()
        except (TypeError, ValueError):
            cur = 0
        if forward_only and s <= cur:
            return cur
        self.data[k] = s
        self._dirty = True
        return s

    # ---- B7：已见 mid 环（位置水位之外的**第二本账**）----
    @staticmethod
    def _mid_key(mid):
        """把 mid 归一成**可哈希的稳定键**（int 原样；其它转字符串并挂牌）。

        ⛔ 为什么不能只收 int：产品现在 `mid` 是 `local_id`（整数），但**没有契约保证**它永远是整数
        （换库/换适配器可能给字符串 id，判据也在用 `"m5"` 这种）。老写法 `int(mid)` 对非数字
        **静默丢弃** ⇒ 去重悄悄失效（而且看不出来）。现在：整数走整数键，其余走 `"s:" + str(mid)`，
        两类**不会互相撞**（int 键 与 "s:…" 字符串键在同一个 list 里天然不同）。
        """
        if mid is None:
            return None
        if isinstance(mid, bool): # bool 是 int 子类，排除掉以免 True 被当成 1
            return "s:" + str(mid)
        if isinstance(mid, int):
            return mid
        try:
            f = float(mid)
        except (TypeError, ValueError):
            return "s:" + str(mid)
        # 数字型字符串归一到 int，避免 "123" 与 123 被当成两条
        return int(f) if f == int(f) else ("s:" + str(mid))

    def seen_for(self, chat_key: str) -> list:
        """返回该会话已见 mid 列表（副本，调用方改它不影响本表）。"""
        return list(self._seen.get(self._ns(chat_key) + _SEEN_SUFFIX, []))

    def seen_has(self, chat_key: str, mid) -> bool:
        """这条 mid 是否**已经被处理过**。

        ⛔ 没有 mid（`None`）时**一律回 False**（＝"没见过"）——宁可让它再过一次
        （重复可以接受），也绝不因为"取不到 id"就把它当成处理过（那会漏读）。
        """
        k = self._mid_key(mid)
        if k is None:
            return False
        return k in self._seen.get(self._ns(chat_key) + _SEEN_SUFFIX, [])

    def remember_seen(self, chat_key: str, mids) -> int:
        """把一批 mid 记进已见环（去重 + 环形截断）。返回**本次新增**的条数。"""
        k = self._ns(chat_key) + _SEEN_SUFFIX
        buf = list(self._seen.get(k, []))
        have = set(buf)
        added = 0
        for mid in (mids or []):
            m = self._mid_key(mid)
            if m is None or m in have:
                continue
            buf.append(m)
            have.add(m)
            added += 1
        if added:
            if len(buf) > SEEN_KEEP:
                buf = buf[-SEEN_KEEP:]
            self._seen[k] = buf
            self._dirty = True
        return added

    def clear_seen(self, chat_key: str = "") -> int:
        """清空已见环。**换库/清聊天记录后必须调它** ——

        为什么：`local_id` 是**单库内**的行号；微信换库（清记录重建）后行号会**重排**，
        旧 `seen` 里的 mid 会**撞上新行的行号** ⇒ 新消息被当成"见过" ⇒ **静默漏读**。
        ⇒ 宁可清空重来（最多重判几条，符合的口径），绝不许误跳。

        `chat_key` 为空 ⇒ 清**整个账号命名空间**下的全部会话。
        """
        if not chat_key:
            pre = (self.account or "?") + "|"
            gone = [k for k in self._seen if k.startswith(pre)]
            for k in gone:
                del self._seen[k]
            if gone:
                self._dirty = True
            return len(gone)
        k = self._ns(chat_key) + _SEEN_SUFFIX
        if k in self._seen:
            del self._seen[k]
            self._dirty = True
            return 1
        return 0

    def _snapshot(self) -> dict:
        """落盘用快照：水位（int）与已见环（list）**合进同一张平铺表**。

        ⛔ 必须每次现拼，不能缓存 —— 缓存会让 `_seen` 的改动漏进盘（重启即重读）。
        """
        snap = dict(self.data)
        for k, v in self._seen.items():
            if v:
                snap[k] = list(v)
        return snap

    def flush(self) -> bool:
        """原子落盘（`persist.atomic_write_json`：tmp 名带 pid+随机后缀 + `os.replace`）；没有变化就不写。

        老写法共用 `<path>.tmp` ⇒ 并发/多进程写会互相穿插出坏 JSON；失败返回 False（不吞）。
        坏档**留证失败**（原档还在原地）时**拒绝写**——写出去就是整表被覆盖。
        ⚠️ 写的是**整表快照** ⇒ **本表假定单写者**（单实例锁保证）；
           真要两进程共享，得改成"读-改-写 + 跨进程锁"（详见文件头那段）。
        """
        if not self._dirty:
            return True
        if getattr(self, "_refuse_overwrite", False):
            log.warning("水位表读不出来且留证失败 ⇒ **拒绝覆盖**（本次 flush 不落盘）：%s", self.path)
            self.fail_count += 1
            self.last_error = "坏档留证失败 ⇒ 拒绝覆盖"
            return False
        if persist.atomic_write_json(self.path, self._snapshot(), indent=1, sort_keys=True):
            self._dirty = False
            return True
        self.fail_count += 1
        self.last_error = self.last_error or "原子写失败（目标被占用 / 磁盘不可写？）"
        return False


_FLUSH_WARN_AT: dict = {}


def flush_checked(wm: "Watermark", log=None, why: str = "", warn_gap: float = 60.0) -> bool:
    """**看返回值**地 flush 水位表。

    为什么要有它：`persist.atomic_write_json` 在"目标被以不共享 DELETE 的方式占用"或
    真并发时**必然** `WinError 5`（审计实测：两进程同时 flush 双双失败）——老代码 8 个
    调用点全是 `wm.flush()` **丢掉返回值**，于是水位明明没落盘、程序却当成写成功了，
    重启后从旧水位重放（重复回复）或被上层误判。

    失败时：①`wm.fail_count/last_error` 留痕（控制台/自检可读）②按 `warn_gap` 限频告警
    （每 60 秒至多一条，不刷屏）③返回 False 交给调用方处置。**不抛异常**。
    """
    try:
        ok = bool(wm.flush())
    except Exception as e: # flush 本身崩了也算失败，不许把异常抛给主循环
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
                if hasattr(log, "warning"): # logging.Logger
                    log.warning("%s", _msg)
                else: # process_batch 那种 (level, fmt, *args) 回调
                    log("warn", "%s", _msg)
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
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
    except Exception as _e:
        log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
        return False


def process_batch(chat_key: str, items, handler, wm: "Watermark", log=None,
                  max_retry: int = DEFAULT_RETRY, retry_sleep: float = DEFAULT_RETRY_SLEEP,
                  deadletter_path: str = "", seq_key: str = "sort_seq", id_key: str = "mid",
                  sleep=time.sleep) -> dict:
    """按序处理一批消息；**成功才推进水位**。

    handler(item) 返回真值＝这条已被下游接受（例如 append_incoming 返回了 entry）；
    抛异常或返回假值＝失败 ⇒ 重试至多 max_retry 次（线性退避）⇒ 仍失败写 dead-letter，
    水位**越过该条**（否则毒消息会让队列永远卡住），日志里明确写"已越过、不再重试"。

    ★ **B7 根治**：判"处理过没有"从**纯位置比较**改为「**mid 命中已见环** 或 位置已越过」：
      · **只看位置**的老实现在"同一 `sort_seq` 有多条"时会**一次性跳过其余条**（永久漏读，
        现场见 `wechat.py:2415` 那段"机器人每两分钟自己念一句"的注释）——因为一个整数
        表达不了"这一批处理过哪些"；
      · 现在若 `mid` 命中 `wm.seen_has()` ⇒ 跳过（**精确**，不误跳同 seq 的其他条）；
        否则按位置比较（老行为，作为 `mid` 缺失时的兜底）；
      · 处理完（无论成功/失败/无关）都 `remember_seen(mid)` ⇒ 下次不重。
      · ⛔ 无 `mid` 的行用**合成 id**（`"seq:%d" % seq`）参与判重，避免同 seq 的多条无 id 行互相顶掉。

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

    def _has_seen_api():
        return hasattr(wm, "seen_has") and hasattr(wm, "remember_seen")

    def _seen_key(mid, seq, dup_i=0):
        """判重与记账**共用**的键：有 mid 用 mid；无 mid 用**合成 id**。

        ⛔ 两处必须用**同一个函数** —— 否则"记账用合成 id、判重用位置"，无 id 的行照样互相顶掉。
        ⛔ 合成 id 必须把**同 seq 的第几条**（`dup_i`）算进去 —— 只用 `seq` 的话，
           同 seq 下的多条无 id 行会得到**同一个键** ⇒ 第二条被当成"见过"跳过（＝漏读）。
        """
        if mid is not None:
            return mid
        try:
            return int(hash("seq:%d#%d" % (int(seq or 0), int(dup_i))) & 0x7FFFFFFF)
        except Exception:
            return None

    #: 批内"同一 seq 已出现几条无 id 行"的计数器（合成 id 的区分位）
    _dup_no: dict = {}

    with chat_lock(chat_key): # 同一会话串行：并发调用者在这里排队
        for it in list(items or []):
            seq = _seq(it)
            mid = _mid(it)
            if mid is None:
                _dup_no[seq] = int(_dup_no.get(seq, 0)) + 1
                skey = _seen_key(mid, seq, _dup_no[seq])
            else:
                skey = _seen_key(mid, seq)
            # ── B7：先看这条有没有见过（精确账）──
            if _has_seen_api() and skey is not None and wm.seen_has(chat_key, skey):
                stats["skipped"] += 1 # 这条**确实处理过**（同 seq 的其他条不受影响）
                continue
            # ── 位置账（两条规则，合起来既防重放又不漏同 seq）──
            #   · `seq < 水位` ⇒ **一定处理过**（水位是"已处理的最大 seq"）⇒ 跳过。
            #   · `seq == 水位` ⇒ **交界格**，两种可能都真实存在：
            #       - 它是"已处理过的那条"（mid 已在 seen）⇒ 上面那一关已经跳过它了；
            #       - 它是"同 seq 但没处理过的条"（mid 不在 seen）⇒ **必须放行**（这就是 B7 的漏读修复）。
            #   · `seq > 水位` ⇒ 新条，正常放行。
            #   ⇒ 所以这里**只看 `<`**（不看 `==`）。老实现在这里是 `<=`，
            #     正是"同 seq 的其余条被永久跳过"的元凶。
            _waterline = wm.get(chat_key)
            if seq and seq < _waterline:
                stats["skipped"] += 1 # 明确在水位之前的重放 ⇒ 跳过（不重放）
                continue
                continue
            ok, last_err = False, ""
            for attempt in range(1, max(1, int(max_retry)) + 1):
                try:
                    ok = bool(handler(it))
                    if ok:
                        break
                    last_err = "handler 返回假值"
                except Exception as e: # 单条失败不许打断整批
                    last_err = "%s: %s" % (type(e).__name__, e)
                if attempt < max_retry:
                    stats["retried"] += 1
                    log("warn", "chat=%s mid=%s 第 %d/%d 次处理失败（%s），%.2fs 后重试",
                        chat_key, mid, attempt, max_retry, last_err, retry_sleep * attempt)
                    try:
                        sleep(retry_sleep * attempt)
                    except Exception:
                        pass
            if ok:
                stats["processed"] += 1
                wm.set(chat_key, seq)
                _mark_seen(wm, chat_key, skey, log)
                flush_checked(wm, log=log, why="批内成功推进（chat=%s）" % chat_key)
            else:
                stats["failed"] += 1
                rec = {"ts": int(time.time() * 1000), "chat": chat_key, "mid": mid,
                       "seq": seq, "attempts": max_retry, "error": last_err,
                       "note": "重试 %d 次仍失败，已越过；原始报文见本文件同名字段" % max_retry}
                try:
                    rec["item"] = json.loads(json.dumps(it, ensure_ascii=False, default=str))
                except Exception as _e:
                    log.debug("宽捕处出错（留栈，便于归因）：%s", _e, exc_info=True)
                    rec["item"] = None
                if deadletter_path:
                    dead_letter(deadletter_path, rec)
                log("error", "chat=%s mid=%s seq=%s 重试 %d 次仍失败（%s）⇒ 已记入 %s 并**越过**该条",
                    chat_key, mid, seq, max_retry, last_err, deadletter_path or "(未配置 dead-letter)")
                wm.set(chat_key, seq)
                _mark_seen(wm, chat_key, skey, log)
                flush_checked(wm, log=log, why="越过毒消息推进（chat=%s）" % chat_key)
        stats["advanced_to"] = wm.get(chat_key)
    return stats


def _mark_seen(wm, chat_key: str, skey, log) -> None:
    """把这条记进已见环（`skey` 已由 `_seen_key()` 算好，判重与记账共用同一个键）。

    ⛔ 无 `mid` 的行也要记：同一 seq 下若有多条"归一化阶段被丢弃"的行（自己发的/系统/空内容），
    不记就会**互相顶掉**（只有第一条被处理，其余被位置比较跳过）。
    """
    if skey is None or not hasattr(wm, "remember_seen"):
        return
    try:
        wm.remember_seen(chat_key, [skey])
    except Exception as e:
        try:
            log("warn", "记已见 mid 失败（不影响本条处理，重启后可能重读）：%s", e)
        except Exception:
            pass


def make_log(logger):
    """把 logging.Logger 适配成 process_batch 要的 (level, fmt, *args) 回调。"""
    def _l(level, fmt, *args):
        try:
            getattr(logger, level if level in ("warn", "error", "info") else "info")(fmt, *args)
        except Exception:
            pass
    return _l
