# -*- coding: utf-8 -*-
"""撤回处理：把「已被撤回」的消息从上下文里剔除（存档标记 + 记忆清理 + 审计留痕）。

背景（第三方 v0.4 对账清单第 14 条）：`agent/wechat.py::normalize()` 过去对系统消息一律
`return None`（只留「拍一拍」）⇒ **对方撤回之后，已经进过上下文/存档的那条消息仍然留着**，
模型还可能引用它。本模块只做三件事：

1. **认出**撤回事件（两种形态都认）：
   · 4.x 的 `<sysmsg type="revokemsg">…<newmsgid>…</newmsgid>…<replacemsg>…</replacemsg>`（XML，可能被 zstd 压过）
   · 纯文本提示语：`"E" 撤回了一条消息` / `你撤回了一条消息` / `E recalled a message`
2. **定位**被撤的是哪一条：首选 `newmsgid`（＝server_id）→ `local_id` → 存档里的 `mid`；
   取不到就按「同发送者的最近一条（时间窗内）」兜底（可关；**兜底只在窗口内找最近一条，绝不全局猜**）。
3. **剔除**：给存档条目打 `recalled` 标记（`ChatStore.recent()` 默认不再返回它 ⇒ 提示词、
   记忆提炼、工具取上下文**一处生效、处处生效**），并把该成员长期印象里内容完全相同的那条删掉；
   同时把「撤了什么」写进 `data/recall_log.jsonl`（审计）与 `data/recall_stats.json`（控制台读数）。

设计纪律（沿用本项目既有口径）：
· **不静默**：认不出形态的系统消息会写 `data/recall_unmatched.jsonl` 留证据，不假装处理过；
· **不猜**：拿不到 newmsgid 时只在时间窗内按发送者匹配最近一条，且结果写进审计；
· **不打断主链**：任何异常都被调用方吞掉并留日志，绝不因为一条撤回事件卡住监听循环。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

from .config import DATA_DIR

_lock = threading.RLock()

LOG_PATH = os.path.join(DATA_DIR, "recall_log.jsonl")
STATS_PATH = os.path.join(DATA_DIR, "recall_stats.json")
UNMATCHED_PATH = os.path.join(DATA_DIR, "recall_unmatched.jsonl")

# ── 形态识别 ────────────────────────────────────────────────────────────
_SYSMSG_RE = re.compile(r"<sysmsg[^>]*\btype\s*=\s*[\"']revokemsg[\"']", re.I)
_NEWMSGID_RE = re.compile(r"<newmsgid>\s*(\d{3,})\s*</newmsgid>", re.I)
_REPLACE_RE = re.compile(r"<replacemsg>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</replacemsg>", re.S | re.I)
_SESSION_RE = re.compile(r"<session>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</session>", re.S | re.I)

# 纯文本提示语（中/英）——整句匹配，避免把正常聊天里的"撤回"两字当成事件
_SELF_TEXT_RE = re.compile(r"^\s*(?:你|我)\s*(?:撤回了一条消息|撤回了一条消息|撤回了一條訊息)\s*[。.]?\s*$")
_OTHER_TEXT_RE = re.compile(
    r"^\s*[\"'“”‘’「『]?\s*([^\"'“”‘’「』」]{1,32}?)\s*[\"'“”‘’」』]?\s*"
    r"(?:撤回了一条消息|撤回了?一条訊息|撤回了一條訊息|撤回了一条|recalled a message|revoked a message)"
    r"\s*[。.]?\s*$", re.I)
_PEER_TEXT_RE = re.compile(r"^\s*对方\s*撤回了一条消息\s*[。.]?\s*$")

# 疑似撤回但认不出形态的线索（用于留证，不用于处理）
_SUSPECT_RE = re.compile(r"撤回|revokemsg|recalled a message", re.I)


def _log(log, level: str, fmt: str, *a) -> None:
    """兼容两种日志口子：`logging.Logger`（有 .info/.warning）与
    `listener_watermark.make_log()` 那种 `(level, fmt, *args)` 回调。任何异常都不许冒泡。"""
    if log is None:
        return
    try:
        fn = getattr(log, level, None)
        if callable(fn):
            fn(fmt, *a)
        elif callable(log):
            log(level, fmt, *a)
    except Exception:
        pass


def _plain(text) -> str:
    if isinstance(text, bytes):
        try:
            return text.decode("utf-8", "ignore")
        except Exception:
            return ""
    return str(text or "")


def parse_recall(content, self_wxid: str = "") -> dict | None:
    """认出撤回事件；不是撤回则返回 None。

    返回：{"svrid": int|None, "who": str, "self": bool, "note": str, "form": "xml"|"text",
           "session": str}
    """
    txt = _plain(content).strip()
    if not txt:
        return None
    out = None
    if _SYSMSG_RE.search(txt):
        note = ""
        m = _REPLACE_RE.search(txt)
        if m:
            note = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1)).strip()
        svrid = None
        m2 = _NEWMSGID_RE.search(txt)
        if m2:
            try:
                svrid = int(m2.group(1))
            except Exception:
                svrid = None
        who, is_self = "", False
        if note:
            sub = parse_recall(note, self_wxid)
            if sub:
                who, is_self = sub.get("who") or "", bool(sub.get("self"))
        sess = ""
        m3 = _SESSION_RE.search(txt)
        if m3:
            sess = re.sub(r"<!\[CDATA\[|\]\]>", "", m3.group(1)).strip()
        out = {"svrid": svrid, "who": who, "self": is_self, "note": note or txt[:80],
               "form": "xml", "session": sess}
    if out is None:
        if _SELF_TEXT_RE.match(txt):
            out = {"svrid": None, "who": "", "self": True, "note": txt[:80], "form": "text", "session": ""}
        elif _PEER_TEXT_RE.match(txt):
            out = {"svrid": None, "who": "", "self": False, "note": txt[:80], "form": "text", "session": ""}
        else:
            m = _OTHER_TEXT_RE.match(txt)
            if m:
                who = m.group(1).strip().strip("\"'“”‘’「」『』")
                out = {"svrid": None, "who": who, "self": False, "note": txt[:80], "form": "text", "session": ""}
    if out is None:
        return None
    if self_wxid and out.get("who") and str(out["who"]) == str(self_wxid):
        out["self"] = True
        out["who"] = ""
    return out


def _norm_name(s) -> str:
    return re.sub(r"\s+", "", str(s or "").strip().strip("\"'“”‘’「」『』")).casefold()


def pick_target(messages, who: str = "", self_only: bool = False,
                before_ts: int | None = None, window_ms: int = 180000):
    """从后往前挑「被撤回的那条」：同发送者（或机器人自己）+ 时间不晚于撤回事件 + 在窗口内。

    `who` 与 `self_only` 都为空 ⇒ 返回 None（**不猜**）。
    """
    if not who and not self_only:
        return None
    want = _norm_name(who)
    for m in reversed(list(messages or [])):
        if m.get("recalled"):
            continue
        ts = int(m.get("ts") or 0)
        if before_ts and ts and ts > int(before_ts):
            continue
        if self_only:
            if not m.get("self"):
                continue
        else:
            if m.get("self"):
                continue
            if want and _norm_name(m.get("sender_name")) != want:
                continue
        if window_ms and before_ts and ts and (int(before_ts) - ts) > int(window_ms):
            continue
        return m
    return None


def _append_jsonl(path: str, rec: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _write_stats(rec: dict) -> None:
    with _lock:
        cur = {"count": 0, "last": None}
        try:
            with open(STATS_PATH, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            if isinstance(d, dict):
                cur = d
        except Exception:
            pass
        cur["count"] = int(cur.get("count") or 0) + (1 if rec.get("removed") else 0)
        if rec.get("removed"):
            cur["last"] = rec
        cur["updatedAt"] = int(time.time() * 1000)
        try:
            os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
            tmp = STATS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=1)
            os.replace(tmp, STATS_PATH)
        except Exception:
            pass


def summary() -> dict:
    """给控制台/`/api/status` 用的只读读数（不写盘）。"""
    try:
        with open(STATS_PATH, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return {"count": int(d.get("count") or 0), "last": d.get("last") or None,
                    "updatedAt": int(d.get("updatedAt") or 0)}
    except Exception:
        pass
    return {"count": 0, "last": None, "updatedAt": 0}


def note_unmatched(content, extra: dict | None = None) -> None:
    """疑似撤回但认不出形态 ⇒ 留证（下次照它把解析器补齐，而不是猜着改）。"""
    txt = _plain(content)[:400]
    if not _SUSPECT_RE.search(txt):
        return
    rec = {"ts": int(time.time() * 1000), "content": txt}
    if extra:
        rec.update(extra)
    _append_jsonl(UNMATCHED_PATH, rec)


def purge(store, chat_key: str, info: dict, resolver=None, memory=None,
          window_ms: int = 180000, heuristic: bool = True, log=None) -> dict:
    """把这一条撤回落实到位。**永远返回 dict（真值）**，让水位可以推进。"""
    now_ms = int(time.time() * 1000)
    info = dict(info or {})
    before_ts = int(info.get("ts") or now_ms)
    res = {"ok": False, "removed": 0, "how": "none", "chat": chat_key,
           "who": info.get("who") or ("自己" if info.get("self") else ""),
           "note": info.get("note") or "", "mid": None, "text": "", "ts": before_ts,
           "form": info.get("form") or ""}
    target = None
    svrid = info.get("svrid")
    if svrid and resolver is not None:
        try:
            mid = resolver(int(svrid))
        except Exception as e:
            _log(log, "debug", "撤回：newmsgid→local_id 解析失败 %s: %s", svrid, e)
            mid = None
        if mid is not None:
            try:
                target = store.find_by_mid(chat_key, mid)
            except Exception:
                target = None
            if target is not None:
                res["how"] = "svrid"
    if target is None and heuristic:
        try:
            msgs = store.recent(chat_key, limit=200, include_recalled=True)
        except Exception:
            msgs = []
        target = pick_target(msgs, who=info.get("who") or "", self_only=bool(info.get("self")),
                             before_ts=before_ts, window_ms=window_ms)
        if target is not None:
            res["how"] = "heuristic"
    if target is not None:
        text = str(target.get("text") or "")
        try:
            store.mark_recalled(chat_key, target.get("id"), info)
            res.update({"ok": True, "removed": 1, "mid": target.get("mid"),
                        "text": text[:120]})
        except Exception as e:
            res["error"] = "标记失败：%s" % str(e)[:80]
            _log(log, "warning", "撤回：标记存档失败 %s", e)
        if res["ok"] and memory is not None and not target.get("self"):
            # 长期印象里若已沉淀过同样内容，一并删掉（只删内容完全相同的，不做模糊匹配）
            uid = str(target.get("sender_id") or "")
            name = str(target.get("sender_name") or "")
            key = text[:300]
            for kw in ({"user_id": uid} if uid else {}, {"target": name} if name else {}):
                if not kw:
                    continue
                try:
                    memory.remove(chat_key, "memberImpression", content=key, **kw)
                except Exception as e:
                    _log(log, "debug", "撤回：清理长期印象失败 %s", e)
    _log(log, "info", "撤回处理：会话=%s 谁=%s 方式=%s 撤掉=「%s」",
         chat_key, res["who"] or "?", res["how"], (res.get("text") or "")[:40])
    _append_jsonl(LOG_PATH, res)
    _write_stats(res)
    return res
