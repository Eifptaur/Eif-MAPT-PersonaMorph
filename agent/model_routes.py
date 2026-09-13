# -*- coding: utf-8 -*-
"""图 / 文 / 视频分流选模型（第三方 v0.4 对账清单第 20 条的前半）。

问题：一个会话里，纯文字闲聊用便宜模型就够，而**带图的请求**必须用能看图的模型；
视频抽帧后同样是"看图"。原来只有一个 `api.model`，要么全用贵的视觉模型，要么看图就瞎。

做法（和备选模型链同一思路：**落在 `chat_completion()` 里**，让 17 个调用点一处生效）：
1. `kind_of(messages)` 自动判定这次请求是哪种输入——**任何一条消息带 `image_url` 就是 `image`**，
   否则 `text`（显式传 `kind=` 可覆盖，将来接视频/语音分流也用同一个口子）；
2. `route(api, kind)` 查配置 `api.model_routes.{text,image,video}`：**留空＝用主模型**（默认行为不变）；
3. 命中的模型当**主模型**用，后面照旧接备选模型链（`llm.candidates()` 已按这个顺序拼）。
   路由失败（配置里写了个不存在的模型）由备选链兜底，不会因为分流把请求打死。

读数：`data/model_routes_stats.json` 记"每种输入各用了哪个模型、各多少次"，控制台可见。
"""
from __future__ import annotations

import json
import os
import threading
import time

from .config import DATA_DIR

_lock = threading.RLock()

KINDS = [("text", "纯文字", "不带图的请求（闲聊、思考、工具结果都是文字）"),
         ("image", "带图", "请求里有图片（群友发图、看图工具、视频抽帧后）"),
         ("video", "视频", "显式指定给视频链路用的模型；留空＝跟「带图」同一个")]


def stats_path() -> str:
    return os.path.join(DATA_DIR, "model_routes_stats.json")


def kind_of(messages) -> str:
    """判定这次请求的输入类型：**带图就是 image**（视频抽帧也是图，走同一个视觉模型）。"""
    try:
        for m in (messages or []):
            c = (m or {}).get("content")
            if isinstance(c, list):
                for p in c:
                    if isinstance(p, dict) and p.get("type") == "image_url":
                        return "image"
    except Exception:
        pass
    return "text"


def routes() -> dict:
    try:
        from .config import get_config
        r = ((get_config() or {}).get("api") or {}).get("model_routes") or {}
        return {k: str(r.get(k) or "").strip() for k, _n, _d in KINDS}
    except Exception:
        return {k: "" for k, _n, _d in KINDS}


def route(api: dict | None = None, kind: str = "text") -> str:
    """这次该用哪个模型；返回空串＝用主模型（保持原行为）。"""
    r = routes()
    if api is not None:
        raw = (api.get("model_routes") or {})
        r = {k: str(raw.get(k) or r.get(k) or "").strip() for k, _n, _d in KINDS}
    kind = str(kind or "text")
    if kind == "video" and not r.get("video"):
        kind = "image"                     # 视频没单独配 ⇒ 跟带图共用（默认语义，写进文档）
    return r.get(kind) or ""


def note(kind: str, model: str, reason: str = "") -> None:
    """记一笔"这次走的是哪条分流"（控制台读数 + 复盘）。异常一律吞掉，绝不影响主流程。"""
    try:
        with _lock:
            cur = {"counts": {}, "last": None}
            try:
                with open(stats_path(), "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    cur = d
            except Exception:
                pass
            counts = cur.get("counts") or {}
            key = "%s→%s" % (kind, model or "主模型")
            counts[key] = int(counts.get(key) or 0) + 1
            cur["counts"] = counts
            cur["last"] = {"ts": int(time.time() * 1000), "kind": kind, "model": model or "",
                           "reason": str(reason or "")[:120]}
            os.makedirs(os.path.dirname(stats_path()), exist_ok=True)
            tmp = stats_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=1)
            os.replace(tmp, stats_path())
    except Exception:
        pass


def snapshot() -> dict:
    """给控制台/`/api/status` 用的只读读数。"""
    r = routes()
    out = {"routes": r, "kinds": [{"id": k, "name": n, "desc": d, "model": r.get(k) or ""} for k, n, d in KINDS],
           "counts": {}, "last": None}
    try:
        with open(stats_path(), "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if isinstance(d, dict):
            out["counts"] = d.get("counts") or {}
            out["last"] = d.get("last") or None
    except Exception:
        pass
    return out
