# -*- coding: utf-8 -*-
"""投稿前的**本地**预检：选完文件、还没发任何请求之前，先判"这文件能不能传"。

⛔ 为什么要在界面侧再来一份：用户选完文件就该**立刻**知道"这种格式不行 / 太大了"，
   而不用等一轮网络往返。但**白名单只能有一处真值** —— 所以这里不另抄一份表，
   直接读 `agent/market_upload.py` 的 `ALLOWED_EXT` / `MAX_BYTES`（同一个进程，
   它就是权威）。这样"界面允许"与"直传声明的 content-type"永远一致。

⛔ 本模块**不联网、不写盘**。
"""
from __future__ import annotations

import os
import sys


def _rules() -> tuple:
    """取（扩展名白名单, 体积上限）。拿不到就返回空表 —— 调用方会退回"不拦"。"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from agent import market_upload  # noqa: PLC0415

        return dict(market_upload.ALLOWED_EXT), int(market_upload.MAX_BYTES)
    except Exception:  # noqa: BLE001
        return {}, 20 * 1024 * 1024


def precheck_local(path: str) -> tuple:
    """`(ok, code, message, size, content_type)` —— 与后端 `market_upload.precheck` 同口径。"""
    allowed, max_bytes = _rules()
    p = str(path or "").strip()
    if not p or not os.path.isfile(p):
        return False, "no-file", "没找到这个文件", 0, ""
    ext = os.path.splitext(p)[1].lower()
    if allowed and ext not in allowed:
        return (False, "unsupported-file-type",
                "这种文件不能投稿，只能传：" + "、".join(sorted(allowed)), 0, "")
    try:
        size = os.path.getsize(p)
    except OSError as e:
        return False, "no-file", "读不到这个文件：%s" % e, 0, ""
    if size > max_bytes:
        return (False, "payload-too-large",
                "文件 %.1f MB，超过 %d MB 上限" % (size / 1048576.0, max_bytes // 1048576),
                size, "")
    return True, "ok", "", size, allowed.get(ext, "")


def kind_save_dir(kind: str) -> str:
    """口令取件的落点目录（按栏）。⛔ 与 `agent/market.kind_dir` 同源，不自己拼路径。"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from agent import market  # noqa: PLC0415

        return market.kind_dir(str(kind or ""))
    except Exception:  # noqa: BLE001
        return ""
