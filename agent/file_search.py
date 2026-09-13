# -*- coding: utf-8 -*-
"""本地文件搜索（给「把某个文件发给我」这类请求用）：**只在用户配置的目录里找，永远不许越界**。

设计口径（用户 2026-09-13）：机器人可以自己找文件并发群，但——
  · **只搜用户配的目录**（`file_search.dirs`，默认空 ⇒ 不搜，明确告诉用户去控制台加目录）
  · 搜索本身是只读；**发出去**另过一道 `send.file_forward_optin`（发文件会短暂抢前台，默认关）
  · 路径必须落在允许目录内（`is_inside` 用 realpath 判，防 `..` 与软链接绕出去）
  · 跳过 `.git/node_modules/__pycache__/AppData` 这类噪声目录，限制深度与结果数
"""
from __future__ import annotations

import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "AppData", "$RECYCLE.BIN",
             "System Volume Information", ".idea", ".vscode"}
DEFAULT_MAX = 20


def _cfg() -> dict:
    try:
        from .config import get_config
        return dict(get_config().get("file_search") or {})
    except Exception:
        return {}


def enabled() -> bool:
    return bool(_cfg().get("enabled"))


def raw_dirs() -> list:
    return [str(d) for d in (_cfg().get("dirs") or []) if str(d).strip()]


def dirs() -> list:
    """配置里的可搜目录（规范成绝对路径）。"""
    out = []
    for d in raw_dirs():
        p = d if os.path.isabs(d) else os.path.join(ROOT, d)
        p = os.path.abspath(os.path.expandvars(os.path.expanduser(p)))
        if p not in out:
            out.append(p)
    return out


def dirs_status() -> list:
    out = []
    for d in dirs():
        try:
            n = len([f for f in os.listdir(d) if not f.startswith(".")])
        except OSError:
            n = -1
        out.append({"dir": d, "exists": os.path.isdir(d), "count": n})
    return out


def is_inside(path: str, allow=None) -> bool:
    """路径是否落在允许目录内（realpath 判定，防 `..`/软链接越界）。"""
    try:
        p = os.path.realpath(os.path.abspath(str(path or "")))
    except Exception:
        return False
    for d in (allow or dirs()):
        try:
            base = os.path.realpath(os.path.abspath(d))
        except Exception:
            continue
        if p == base or p.startswith(base + os.sep):
            return True
    return False


def search(name: str, allow=None, max_results: int = 0, max_depth: int = 3) -> tuple:
    """在允许目录里按文件名找（不区分大小写，含子串匹配）。返回 `(候选列表, 说明)`。

    候选＝`[{'path','name','size','mtime','dir'}]`，按修改时间新→旧排序。
    """
    q = str(name or "").strip().lower()
    if not q:
        return [], "没给要搜的文件名"
    if not enabled():
        return [], "「找文件」功能没开（控制台 → 媒体与语音 → 本地文件：打开总开关）"
    allow = allow or dirs()
    if not allow:
        return [], "还没配可搜目录（控制台 → 媒体与语音 → 本地文件：加一个目录，比如「下载」或「报告」）"
    limit = int(max_results or _cfg().get("max_results") or DEFAULT_MAX)
    hits, scanned = [], 0
    for base in allow:
        if not os.path.isdir(base):
            continue
        base_depth = base.rstrip(os.sep).count(os.sep)
        for cur, subs, files in os.walk(base):
            subs[:] = [s for s in subs if s not in SKIP_DIRS and not s.startswith(".")]
            if cur.rstrip(os.sep).count(os.sep) - base_depth >= int(max_depth):
                subs[:] = []
            for fn in files:
                scanned += 1
                if q in fn.lower():
                    p = os.path.join(cur, fn)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    hits.append({"path": p, "name": fn, "size": int(st.st_size),
                                 "mtime": int(st.st_mtime), "dir": cur})
            if len(hits) >= limit * 3:
                break
        if len(hits) >= limit * 3:
            break
    hits.sort(key=lambda h: -h["mtime"])
    hits = hits[:limit]
    if not hits:
        return [], "在 %d 个目录里找了 %d 个文件，没有文件名含「%s」的" % (len(allow), scanned, name)
    return hits, "找到 %d 个候选（扫了 %d 个文件）" % (len(hits), scanned)


def resolve(name_or_path: str) -> tuple:
    """把"文件名或路径"解析成一个**确定**的本地文件。返回 `(路径 或 None, 说明)`。

    · 直接给路径：必须在允许目录内
    · 给文件名：必须**唯一命中**才算确定；多个命中就把候选列出来让调用方去挑
    """
    s = str(name_or_path or "").strip()
    if not s:
        return None, "没给文件名"
    if os.path.isabs(s) or s.startswith(".") or os.sep in s:
        p = s if os.path.isabs(s) else os.path.join(ROOT, s)
        if not is_inside(p):
            return None, "这个路径不在允许目录内（只允许发 %s 里的文件）" % "、".join(dirs() or ["（未配置）"])
        return (os.path.abspath(p), "") if os.path.isfile(p) else (None, "文件不存在：%s" % s)
    hits, why = search(s, max_results=5)
    if not hits:
        return None, why
    exact = [h for h in hits if h["name"].lower() == s.lower()]
    pool = exact or hits
    if len(pool) > 1:
        return None, ("命中多个同名/近名文件，请让用户指定一个：%s"
                      % "；".join(os.path.join(h["dir"], h["name"]) for h in pool[:4]))
    return pool[0]["path"], ""


def recent_sent(limit: int = 10) -> list:
    """最近发出去的本地文件台账（只读展示用；由发送侧写入 data/sent_local_files.json）。"""
    import json
    p = os.path.join(ROOT, "data", "sent_local_files.json")
    try:
        with open(p, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        rows = d.get("sent") if isinstance(d, dict) else d
        return list(rows or [])[-limit:][::-1]
    except Exception:
        return []


def note_sent(path: str, chat_id: str = "") -> None:
    """记一笔"已发过"（发文件台账；同时给防重复用）。失败静默。"""
    import json
    p = os.path.join(ROOT, "data", "sent_local_files.json")
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            d = {"sent": []}
        d.setdefault("sent", []).append({"path": path, "chat_id": chat_id, "ts": int(time.time())})
        d["sent"] = d["sent"][-200:]
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception:
        pass


def snapshot() -> dict:
    """控制台「本地文件」面板的数据源（现场读配置 + 目录 + 台账）。"""
    conf = _cfg()
    send_cfg = {}
    try:
        from .config import get_config
        send_cfg = get_config().get("send") or {}
    except Exception:
        pass
    return {"enabled": enabled(), "dirs": dirs_status(), "raw": raw_dirs(),
            "max_results": int(conf.get("max_results") or DEFAULT_MAX),
            "max_mb": float(conf.get("max_mb") or 100),
            "trigger_mode": str(conf.get("trigger_mode") or "on_request"),
            "send_optin": bool(send_cfg.get("file_forward_optin")),
            "recent": recent_sent(8),
            "note": "只在配好的目录里找；发出去另过「转发文件」开关（会短暂抢前台，默认关）"}


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
    if len(sys.argv) > 1:
        print(json.dumps(search(sys.argv[1]), ensure_ascii=False, indent=2))
