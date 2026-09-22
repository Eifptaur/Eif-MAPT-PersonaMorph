# -*- coding: utf-8 -*-
"""**预设信息**：按会话提前设定的背景事实 —— 只在本会话相关时注入，到期自动丢弃。

来源（B站网友原话，2026-09-22）：
  「提前设定信息，当遇到和设定信息有关的内容就好已经提前注入的信息思考，**设置截止日期**，
   截止后**自动舍弃**注入的信息，信息**针对每个单独群聊不外泄**」
⇒ 四条要求逐条落地成本模块的硬口径：
  ① **按会话隔离**（`chats[<chat_key>]`）：注入只读**本会话**那一条列表，**结构上**就不可能串群；
     注入给模型的那段文字里也写明"只属于本会话、不许据此推断别的会话"。
  ② **截止日期**：每条可带 `until`（epoch 秒；按日期填的按**当天 23:59:59** 存）；到点后
     `active()` 不再返回它，`prune()` 把它**真删掉**（"自动舍弃"），并在日志/接口回执里说清丢了几条。
  ③ **相关才注入**：`relevant()` 用**字面重叠**（2-gram，去标点、去虚词）判断；重叠不上就**不注入**
     （宁可这次不用上，也不往模型里塞无关的背景）。也可给某条打 `always` 表示每次都用。
     诚实边界：这里是**字面**匹配，不是语义 —— 换个说法就匹配不上，会在接口回执里如实说明。
  ④ **不外泄**：注入块只含本会话的条目；`block()` 的入参就是 `chat_key`，**没有"全都会话"这条路**。

落盘：`data/briefs.json`（原子写），坏了按空处理并改名 `.bad.<时间戳>` 留证（与其它状态文件同口径）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

from .config import DATA_DIR

log = logging.getLogger("persona-morph")

PATH = os.path.join(DATA_DIR, "briefs.json")
SCHEMA = 1
MAX_PER_CHAT = 20          # 单会话最多几条（再多就该用记忆档了）
MAX_CHARS = 200            # 单条最长字符（超了截断并如实告知）
_inject_max = 5            # 单次最多注入几条（配置可调，见 config.briefs.inject_max）
_lock = threading.RLock()

# 虚词表：2-gram 里含这些字的不算"实词重叠"（否则"这个""的话"就能把任何两句连上）
_STOP = set("的了是在我你他她它们和与就都也很这那个上下吗呢吧啊要把被会想说要给对与及其之而且但"
            "如果因为所以什么怎么什么时候现在一个没有不是可以")


def _path() -> str:
    return PATH


def _now(t=None) -> float:
    return float(t if t is not None else time.time())


def _load() -> dict:
    """读整份（坏文件 ⇒ 改名留证 + 按空起；**永不抛**）。"""
    try:
        p = _path()
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict) or not isinstance(d.get("chats"), dict):
            raise ValueError("结构不对")
        return d
    except FileNotFoundError:
        return {"schema": SCHEMA, "chats": {}}
    except Exception as e:                                        # noqa: BLE001
        try:
            p = _path()
            if os.path.exists(p):
                bad = "%s.bad.%d" % (p, int(time.time()))
                os.replace(p, bad)
                log.warning("预设信息文件读不了（%s）⇒ 已改名留证：%s，本次按空起", e, os.path.basename(bad))
        except Exception:
            pass
        return {"schema": SCHEMA, "chats": {}}


def _save(d: dict) -> str:
    """原子写；返回空串＝成功，否则是要展示给用户的原因。

    ⚠️ 走**统一招式** `persist.atomic_write_json`（不再自己拼固定 `<path>.tmp`）——
    固定 tmp 名在两个进程同时写时会互相覆盖（第十四轮 V-R14-3 的棘轮就是为这个立的）。
    """
    try:
        from . import persist as _ps
        if _ps.atomic_write_json(_path(), d):
            return ""
        return "落盘失败（详见日志；本次改动只在内存里有效）"
    except Exception as e:                                        # noqa: BLE001
        return "%s: %s" % (type(e).__name__, str(e)[:80])


def parse_until(v, now=None) -> float:
    """把用户填的截止日期转成 epoch 秒。支持：空/0/None＝永久 · `YYYY-MM-DD`（当天 23:59:59）·
    epoch 数字。**填错一律当永久**（宁可留着，也不因解析失败把用户刚写的东西丢掉）。"""
    s = str(v or "").strip()
    if not s or s in ("0", "永久", "永远", "不设"):
        return 0.0
    try:
        if re.match(r"^\d{9,}$", s):
            return float(s)
        m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
        if m:
            y, mo, dd = (int(x) for x in m.groups())
            return time.mktime((y, mo, dd, 23, 59, 59, 0, 0, -1))
        m2 = re.match(r"^(\d{1,2})[-/.](\d{1,2})$", s)      # 只填月-日 ⇒ 按今年
        if m2:
            mo, dd = (int(x) for x in m2.groups())
            return time.mktime((time.localtime().tm_year, mo, dd, 23, 59, 59, 0, 0, -1))
    except Exception:
        return 0.0
    return 0.0


def _clean_text(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()[:MAX_CHARS]


def add(chat_key, text, until=None, always=False, now=None) -> dict:
    """加一条（同会话同文本视为重复 ⇒ 刷新截止日期，不新增条目）。"""
    ck = str(chat_key or "").strip()
    tx = _clean_text(text)
    if not ck or not tx:
        return {"ok": False, "why": "会话或内容为空"}
    with _lock:
        d = _load()
        items = list((d.get("chats") or {}).get(ck) or [])
        u = parse_until(until, now)
        for it in items:
            if str(it.get("text") or "") == tx:
                it["until"] = u
                it["always"] = bool(always)
                _err = _save(d)
                return {"ok": not _err, "why": _err, "updated": True, "count": len(items)}
        if len(items) >= MAX_PER_CHAT:
            return {"ok": False, "why": "这个会话的预设信息最多 %d 条，先删几条" % MAX_PER_CHAT}
        items.append({"id": "b%d%s" % (int(_now(now) * 1000), os.urandom(2).hex()),
                      "text": tx,
                      "until": u, "always": bool(always), "created": int(_now(now))})
        d.setdefault("chats", {})[ck] = items
        _err = _save(d)
        if _err:
            return {"ok": False, "why": _err}
        log.info("预设信息 +1（%s）：%s%s", ck, tx[:40],
                 "（%s 到期）" % time.strftime("%Y-%m-%d", time.localtime(u)) if u else "（永久）")
        return {"ok": True, "why": "", "count": len(items)}


def remove(chat_key, bid) -> dict:
    """按 id 删一条（**只在本会话里找** ⇒ 拿别的会话的 id 过来删不动）。"""
    ck = str(chat_key or "").strip()
    with _lock:
        d = _load()
        items = list((d.get("chats") or {}).get(ck) or [])
        keep = [it for it in items if str(it.get("id")) != str(bid)]
        if len(keep) == len(items):
            return {"ok": False, "why": "本会话没有这条（id=%s）" % bid}
        if keep:
            d["chats"][ck] = keep
        else:
            (d.get("chats") or {}).pop(ck, None)
        _err = _save(d)
        return {"ok": not _err, "why": _err, "count": len(keep)}


def prune(chat_key=None, now=None) -> int:
    """**自动舍弃到期条目**（真删）。`chat_key=None` ⇒ 清所有会话里的到期条目。返回删了几条。"""
    t = _now(now)
    n = 0
    with _lock:
        d = _load()
        chats = d.get("chats") or {}
        for ck in list(chats.keys()):
            if chat_key and ck != str(chat_key):
                continue
            keep = [it for it in (chats.get(ck) or [])
                    if not (float(it.get("until") or 0) and float(it.get("until") or 0) <= t)]
            n += len(chats.get(ck) or []) - len(keep)
            if keep:
                chats[ck] = keep
            else:
                chats.pop(ck, None)
        if n:
            _save(d)
            log.info("预设信息：已按截止日期**自动舍弃** %d 条", n)
    return n


def list_for(chat_key, now=None) -> dict:
    """本会话的条目（分开给"还在用"和"已到期待清"两类；**只读本会话**）。"""
    t = _now(now)
    ck = str(chat_key or "").strip()
    items = list(((_load().get("chats") or {}).get(ck) or []))
    live, dead = [], []
    for it in items:
        u = float(it.get("until") or 0)
        row = {"id": it.get("id"), "text": it.get("text"), "always": bool(it.get("always")),
               "until": u,
               "until_text": time.strftime("%Y-%m-%d", time.localtime(u)) if u else "永久"}
        (dead if (u and u <= t) else live).append(row)
    return {"ok": True, "chat": ck, "active": live, "expired": dead,
            "count": len(live), "expired_count": len(dead)}


def all_counts() -> dict:
    """每个会话各几条（给控制台显示"哪些会话设过"；**只给计数与截断名，不给内容**）。"""
    d = _load()
    return {ck: len(v or []) for ck, v in (d.get("chats") or {}).items()}


def _grams(s) -> set:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(s or ""))
    out = set()
    for i in range(len(s) - 1):
        a, b = s[i], s[i + 1]
        if a in _STOP or b in _STOP:
            continue
        out.add(a + b)
    return out


def relevant(text, entries) -> list:
    """**相关才注入**：与本轮文本有"实词 2-gram 重叠"的条目才算相关（`always` 的无条件算相关）。"""
    t = _grams(text)
    hit = []
    for e in entries or []:
        if e.get("always"):
            hit.append(e)
            continue
        if t and (_grams(e.get("text")) & t):
            hit.append(e)
    return hit


def block(chat_key, incoming_text, now=None) -> str:
    """生成本轮要注入的那段文字；不需要注入（没条目/都不相关/都到期）⇒ **返回空串**。

    ⚠️ 只读 `chat_key` 这一个会话的条目 —— 这就是"不外泄"的结构保证。
    "**到期自动舍弃**"的落点也在这里：每次要用之前先 `prune(本会话)`（用户不动手也会被清掉）。
    """
    try:
        try:
            from .config import get_config
            _bf = ((get_config() or {}).get("briefs") or {})
        except Exception:
            _bf = {}
        if _bf.get("enabled") is False:
            return ""
        t = _now(now)
        try:
            prune(str(chat_key or "").strip(), now=t)      # 到期自动舍弃（本会话）
        except Exception:
            pass
        items = list_for(chat_key, now=t)["active"]
        if not items:
            return ""
        try:
            _mx = int(_bf.get("inject_max") or _inject_max)
        except Exception:
            _mx = _inject_max
        use = relevant(incoming_text, items)[:max(1, _mx)]
        if not use:
            return ""
        lines = ["【预设信息（只属于本会话 %s，是管理员提前设好的背景；**不许**据此推断或提及任何别的会话）】"
                 % chat_key]
        for e in use:
            lines.append("- %s%s" % (str(e.get("text") or "")[:MAX_CHARS],
                                    ("（%s 到期）" % e.get("until_text")) if e.get("until") else ""))
        lines.append("（只当背景事实用，不要逐字复述；与本轮无关就别提。）")
        return "\n".join(lines)
    except Exception:                                             # noqa: BLE001
        return ""                                                 # 注入永远不许把这一轮搞崩
