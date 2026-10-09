# -*- coding: utf-8 -*-
"""会话控制三件套：**重说 / 回溯 / 重启**（第 140 项）—— 说错话能当场纠正。

三件事的语义（**别混，混了就出大事**）
-------------------------------------
· **重说（redo）**   —— **不动上下文**，只是让上层重新生成一句（"这句话不对，重来"）。
                      ⛔ 它的验收里专门有一条"**不回退**"：发「重说」**不许**把上下文清掉。
· **回溯（rewind）** —— 把机器人**自己**在「最后一条用户消息」**之后**发的那几条
                      标成"不进上下文"（用户仍能在控制台看到，**不是删**）。
                      ⇒ 效果＝"我那句说错了，当我没说"，但用户刚说的话**留着**。
· **重启（restart）**—— 把**整个会话**的消息标成"不进上下文"（彻底失忆，重开一段）。
                      ⛔ 破坏性最大 ⇒ **只许白名单**（`store.tier_cmd_admins`）。

权限分层（**这是本模块的安全核心**）
----------------------------------
· `redo` / `rewind`：**任何 @ 到机器人的人**都能用 —— 它们只影响机器人自己刚说的话，
  无损、可预期；但要**限频**（防有人连点刷屏）。
· `restart`：**只许白名单** —— 让任何人一键把机器人的上下文清空，等于给了个破坏开关。

⛔ 实现上**不删消息**：一律走 `ChatStore.block_entries(..., reason=…)`
  （与"屏蔽/解除"同一套机制，`recent()` 默认就不返回被屏蔽的）⇒ 可回溯、可撤销、有理由留痕。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from .config import DATA_DIR
from . import persist

log = logging.getLogger(__name__)

_lock = threading.RLock()

#: 指令词 → 动作。**只认"整句就等于这个词"**（与禁言指令同一口径）——
#   否则群里随口一句"我刚说的再说一遍"就会被当成指令。
WORDS = {
    "重说": "redo", "重新说": "redo", "再说一遍": "redo", "重生一句": "redo",
    "回溯": "rewind", "当我没说": "rewind", "撤回这轮": "rewind", "撤了这轮": "rewind",
    "重启": "restart", "清空上下文": "restart", "忘掉这个群": "restart",
}
#: 需要白名单的动作（破坏性）
NEED_ADMIN = ("restart",)
#: 限频：同一会话 `WINDOW_S` 秒内同一动作最多 `MAX_IN_WINDOW` 次
WINDOW_S = 30
MAX_IN_WINDOW = 2
_AT_PREFIX_RE = re.compile(r"^@\S+\s*")


def state_path() -> str:
    return os.path.join(DATA_DIR, "session_cmd.json")


def _load() -> dict:
    try:
        with open(state_path(), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(state_path()), exist_ok=True)
        tmp = persist.tmp_for(state_path())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        persist.replace_into(tmp, state_path())
    except Exception as e:  # noqa: BLE001
        log.debug("会话控制状态写盘失败（不影响本次动作）：%s", str(e)[:80])


def parse(text) -> dict | None:
    """识别一条**会话控制指令**；不是 ⇒ `None`（调用方负责"确实 @ 了机器人"这件事）。"""
    t = _AT_PREFIX_RE.sub("", str(text or "").strip()).strip()
    if not t:
        return None
    t = t.rstrip("。！!~～ ")
    act = WORDS.get(t)
    return {"action": act, "raw": t} if act else None


def _allow(chat_key: str, action: str, now=None) -> tuple:
    """限频检查 ＋ 记账 ⇒ `(ok, note)`。"""
    now = float(now if now is not None else time.time())
    with _lock:
        st = _load()
        rec = st.setdefault("recent", {}).setdefault(str(chat_key), [])
        rec[:] = [(a, t) for (a, t) in rec if now - float(t) < WINDOW_S]
        hit = [1 for (a, t) in rec if a == action]
        if len(hit) >= MAX_IN_WINDOW:
            return False, "这个动作刚刚连点了 %d 次，先缓 %d 秒再来" % (len(hit), int(WINDOW_S))
        rec.append((action, now))
        st["recent"][str(chat_key)] = rec[-20:]
        _save(st)
    return True, ""


def apply(store, chat_key: str, action: str, *, now=None, is_admin: bool = False) -> dict:
    """执行一个会话控制动作 ⇒ `{ok, action, note, cut}`（`cut`＝影响的条数）。

    `store=None` / 读不到 ⇒ **如实说做不到**（不假装成功）。`restart` 无白名单权限 ⇒ 拒绝。
    """
    act = str(action or "").strip()
    if act not in ("redo", "rewind", "restart"):
        return {"ok": False, "action": act, "note": "不认识的会话控制动作", "cut": 0}
    if act in NEED_ADMIN and not is_admin:
        return {"ok": False, "action": act, "note": "「%s」只允许管理员用（store.tier_cmd_admins 白名单）" % act,
                "cut": 0}
    _okf, _whyf = _allow(chat_key, act, now=now)
    if not _okf:
        return {"ok": False, "action": act, "note": _whyf, "cut": 0}
    if act == "redo":
        # ⛔ **一个字都不改**：只把"重说"这个信号交回上层（上层重新生成并追加一条新回复）。
        #   验收里有一条专门盯这个：发「重说」**不许**把上下文清掉。
        return {"ok": True, "action": act, "note": "已让机器人换一句重说（上下文没动）", "cut": 0}
    if store is None:
        return {"ok": False, "action": act, "note": "读不到会话消息（消息库不可用）", "cut": 0}
    try:
        rows = list(store.recent(chat_key, limit=1000, include_self=True) or [])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": act, "note": "读会话失败：%s" % str(e)[:60], "cut": 0}
    if not rows:
        return {"ok": True, "action": act, "note": "这个会话里还没有消息，没什么可操作的", "cut": 0}

    if act == "rewind":
        # 找到**最后一条入站消息**（不是我发的）；只清"它之后我自己发的"那些。
        cut_idx = -1
        for i in range(len(rows) - 1, -1, -1):
            if not rows[i].get("self"):
                cut_idx = i
                break
        if cut_idx < 0:
            return {"ok": True, "action": act, "note": "还没看到群友的消息，不用回溯", "cut": 0}
        victims = [r.get("id") for r in rows[cut_idx + 1:] if r.get("self")]
        if not victims:
            return {"ok": True, "action": act, "note": "我自己最后那句话之前没有我说过的话，不用回溯", "cut": 0}
    else:  # restart
        victims = [r.get("id") for r in rows]
    try:
        n = int(store.block_entries(chat_key, victims, reason="会话控制：%s" % act) or 0)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": act, "note": "标记失败：%s" % str(e)[:60], "cut": 0}
    note = ("已经把我说错的那 %d 条从我自己的上下文里拿掉（消息还在，控制台能看到）" % n) if act == "rewind" \
        else ("已经把这个会话的 %d 条从我上下文里清掉（消息还在，控制台能看到）" % n)
    return {"ok": True, "action": act, "note": note, "cut": n}
