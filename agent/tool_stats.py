# -*- coding: utf-8 -*-
"""工具调用统计（按工具名计数 + 最后调用时间）。

为什么要它（用户 2026-09-13 提到对方控制台里那张"工具名 × 调用次数"表，那是它唯一值得抄的东西）：
   我们原先只有会话/token 用量，**看不出哪些工具真在用、哪些是摆设**。这里在**唯一的分发点**
   `tools.execute_tool()` 里记一笔，内置与自定义工具都自动覆盖。

只记名字/次数/时间，**不记参数内容**（避免把消息内容落进统计文件）。原子写（temp + os.replace）。
"""
from __future__ import annotations

import json
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "tool_stats.json")
_LOCK = threading.RLock()


def _load() -> dict:
    try:
        with open(PATH, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict):
            d.setdefault("counts", {})
            d.setdefault("last", {})
            d.setdefault("errors", {})
            return d
    except Exception:
        pass
    return {"counts": {}, "last": {}, "errors": {}}


def note(name: str, ok: bool = True) -> None:
    """记一笔（失败静默——统计不该影响工具本身）。"""
    n = str(name or "").strip()
    if not n:
        return
    with _LOCK:
        d = _load()
        d["counts"][n] = int(d["counts"].get(n) or 0) + 1
        d["last"][n] = int(time.time())
        if not ok:
            d["errors"][n] = int(d["errors"].get(n) or 0) + 1
        try:
            os.makedirs(os.path.dirname(PATH), exist_ok=True)
            tmp = PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(d, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, PATH)
        except Exception:
            pass


def snapshot() -> dict:
    d = _load()
    counts = {k: int(v) for k, v in (d.get("counts") or {}).items()}
    return {"counts": counts, "last": d.get("last") or {}, "errors": d.get("errors") or {},
            "total": sum(counts.values()), "kinds": len(counts), "path": PATH}


def reset() -> None:
    with _LOCK:
        try:
            os.remove(PATH)
        except OSError:
            pass


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
