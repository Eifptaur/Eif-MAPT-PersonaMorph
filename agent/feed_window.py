# -*- coding: utf-8 -*-
"""未读批的两个切分口径：**时间窗**与**条数上限**（第九轮 V-R9-15/16/17 + 作者口径）。

**为什么要它**（两条独立来源，结论一致）：
  · 作者 2026-09-21 原话：「他说一次性发好多是不是该改一改，**给模型喂的前几分钟就够了**，我觉得。」
  · 审计（V-R9-15/16）：`peek_unread(…,200)` 取的是**最旧** 200 条（积压 >200 时最新那条 @ 落在窗口外
    ⇒ 判成"没触发"并被整批标已读＝**永久吞掉**）；`drain_unread` **无上限**（1000 条积压 ⇒ 单轮 ≈4.1 万
    token），而一轮最多只能连发 20 条/分钟，超限的直接丢。

**三条口径**（每条都有硬理由，改之前先读）：
  ① **时间窗**：只把最近 `window_min` 分钟的消息喂给模型 —— 停机/卡顿后补进来的旧闲聊不再一次性倒给它；
  ② **明确指向它的不受时间窗限制**：`directed(m)` 为真（@ 我 / 引用我）的消息**再旧也要留着** ——
     否则就是把"它不理我"换成另一种形态（审计明确警告过：硬时间窗会造出新的"它不理我"）；
  ③ **条数上限**：超出 `max_n` 的部分**退回未读**（`retry`），不是丢掉也不是标已读 —— 下一轮还在窗口里
     就能被处理；旧的闲聊才进 `skip`（标已读、本次不回）。

纯函数、不碰盘、不 import 产品状态 ⇒ 判据可以离线把三种切分都跑一遍。
"""
from __future__ import annotations

import time

DEFAULT_WINDOW_MIN = 10          # 喂给模型的时间窗（分钟）
DEFAULT_MAX_COUNT = 150          # 单轮最多喂多少条


def entry_ts_ms(m) -> int:
    """取一条消息的时间戳（毫秒）。取不到返回 0（那就算"很旧"）。"""
    try:
        v = (m or {}).get("ts")
        if v in (None, ""):
            return 0
        v = int(float(v))
    except Exception:
        return 0
    # 兼容秒级时间戳（历史上见过 10 位的）
    return v * 1000 if v < 10 ** 11 else v


def pick_feed(pending, now_ms: int = None, window_min: int = DEFAULT_WINDOW_MIN,
              max_n: int = DEFAULT_MAX_COUNT, directed=None) -> dict:
    """把"未读批"切成三桶：`keep`（喂模型）/ `skip`（陈旧闲聊：标已读、不回）/ `retry`（超限：退回未读）。

    返回 `{"keep": [...], "skip": [...], "retry": [...], "oldest_age_min": float}`，三桶都保持原顺序。
    """
    items = [m for m in (pending or []) if isinstance(m, dict)]
    try:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
    except Exception:
        now = int(time.time() * 1000)
    try:
        win_ms = max(0, int(float(window_min or 0))) * 60 * 1000
    except Exception:
        win_ms = DEFAULT_WINDOW_MIN * 60 * 1000
    try:
        cap = int(max_n or DEFAULT_MAX_COUNT)
    except Exception:
        cap = DEFAULT_MAX_COUNT
    # ⛔ 2026-09-21 加（第十轮 V-R10-21）：`feed_max_count <= 0` ⇒ **不限**（以前 0 会静默回落 150、
    #   负数变成"每轮只喂 1 条"，都不符合"0＝不限"这个全仓统一口径）。
    if cap <= 0:
        cap = len(items) or 1

    def _is_dir(m) -> bool:
        if directed is None:
            return False
        try:
            return bool(directed(m))
        except Exception:
            return False

    keep, skip = [], []
    for m in items:
        _ts = entry_ts_ms(m)
        fresh = (win_ms <= 0) or (not _ts) or ((now - _ts) <= win_ms)
        if fresh or _is_dir(m):
            keep.append(m)
        else:
            skip.append(m)

    retry = []
    if len(keep) > cap:
        # ⛔ 2026-09-21 改（第十轮 **V-R10-17 / V-R10-19**，两条都是本文件自己引入的）：
        #   ① **V-R10-17 饥饿**：原来超限只留"**最新** cap 条" ⇒ 到货 ≥ 上限时最旧的那些**永远排不上**
        #      （审计夹具：A=200/cap=150 ⇒ 每轮 +50，A=cap 是活锁；A=149 要 392 轮才轮到尾巴）。
        #      ⇒ 改成 **FIFO**：先喂**最旧**的，把"最新的"退回未读（它们下轮就是"较旧"了，很快轮到）
        #      ⇒ 没有消息会永远排不上。
        #   ② **V-R10-19 directed 洪泛**：原来 `_room = max(0, cap - len(_dir))` ⇒ 全是 @ 时
        #      `keep == _dir`、上限形同不存在（审计实测 200 条全 @ ⇒ keep=200；纯函数 1000 条 ⇒ 1000）
        #      ⇒ 第九轮那 4.1 万 token 从这条路原样回来。⇒ **directed 也二次夹上限**（默认占一半名额；
        #      余下的 directed 进 retry 退回未读，下一轮继续喂，不会丢）。
        _dir_all = [m for m in keep if _is_dir(m)]
        _rest = [m for m in keep if not _is_dir(m)]
        # directed 优先占名额，但**总量封顶在 cap**（V-R10-19：全 @ 时不能再无限喂）；
        # 剩下的名额按 FIFO 补（没有别的消息就把整份额度都给 directed）。
        _keep_dir = _dir_all[:cap]
        _dir_over = _dir_all[cap:]
        _room = max(0, cap - len(_keep_dir))
        _take = _rest[:_room]                      # FIFO：取最旧的
        _rest_over = _rest[_room:]
        _kept_ids = {id(m) for m in (_keep_dir + _take)}
        retry = _dir_over + _rest_over             # 退回未读（不是丢、也不是标已读）
        keep = [m for m in keep if id(m) in _kept_ids]

    _ages = [max(0.0, (now - entry_ts_ms(m)) / 60000.0) for m in items if entry_ts_ms(m)]
    return {"keep": keep, "skip": skip, "retry": retry,
            "oldest_age_min": (max(_ages) if _ages else 0.0)}
