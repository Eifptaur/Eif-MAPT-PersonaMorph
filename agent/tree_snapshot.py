# -*- coding: utf-8 -*-
"""tree_snapshot.py —— **目录树快照差分**：让"这次到底新建/改了哪些文件"能逐条列出来。

⛔ 为什么需要它：**外部引擎不告诉你它写了哪些文件**。
  现场（`agent/bilibili.py::download`）：跑完 `yt-dlp` 之后只能在输出目录里"挑**最大最新**的那个" ——
  而那个目录是**调用方给的**、很可能**本来就有旧文件**（上一次下的、别的用途的）
  ⇒ 会把**上一次的产物**当成本次结果（表观症状："这条视频读出来的其实是另一条"，而且**不报错**）。
  正解：**开工前后各做一次快照、取差集** —— 差集里的才是"这次新建的"。

三条踩过的坑（都写进判据）：
  · **Windows 文件系统大小写不敏感**（`A.MP4` 与 `a.mp4` 是同一个文件）⇒ 比较前**路径转小写**，
    否则"只是改了个大小写"会被读成"删了一个、建了一个"。
  · **中间件不算产物**：`yt-dlp` 会写 `.part`/`.ytdl`；本项目的原子写临时名是
    `.xxx.partial-<pid>-<rand>` ⇒ 默认**跳过**这些（与"落盘原子化"同一条口径）。
  · **只记文件、不记目录**：目录的 mtime 在子项变动时也会变，记进来会造出一堆假变更。

纯函数、不落盘、不碰窗口：进出都是 `dict`，便于在判据里直接构造。
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

#: 快照默认跳过的目录名（不递归进去；都在里面找产物属于误用）
DEFAULT_SKIP_DIRS = ("__pycache__", ".git", ".svn", "node_modules")
#: 中间件后缀 —— 引擎的"还没写完/只是元数据"的文件，不算产物
TEMP_SUFFIX = (".part", ".ytdl", ".tmp", ".temp", ".crdownload")
#: 原子写临时名里的标记（`.name.partial-1234-abcd`）
PARTIAL_MARK = ".partial-"


def _is_temp_name(name: str) -> bool:
    n = str(name or "")
    if PARTIAL_MARK in n:
        return True
    low = n.lower()
    return any(low.endswith(s) for s in TEMP_SUFFIX)


def _norm(rel: str) -> str:
    """比较用的键：**统一小写 + 统一 `/`**（Windows 大小写不敏感，别把改大小写读成增删）。"""
    return str(rel or "").replace("\\", "/").lower()


def snapshot(base: str, *, recurse: bool = True, skip_dirs=DEFAULT_SKIP_DIRS,
             skip_temp: bool = True) -> dict:
    """对一棵树做快照 ⇒ `{归一化相对路径: (字节数, mtime_ns)}`。

    读不到的项**跳过**（不抛）：快照是"尽力而为的观测"，不该因为一个正在被删的文件把整条链带崩。
    `base` 不存在 / 不是目录 ⇒ 返回空 dict（调用方据此知道"没东西可比"）。
    """
    out = {}
    root = str(base or "")
    if not root or not os.path.isdir(root):
        return out
    _skip = set(skip_dirs or ())
    if recurse:
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in _skip]
            for fn in fns:
                _put(out, root, os.path.join(dp, fn), skip_temp)
    else:
        try:
            fns = os.listdir(root)
        except Exception:  # noqa: BLE001
            return out
        for fn in fns:
            p = os.path.join(root, fn)
            if os.path.isfile(p):
                _put(out, root, p, skip_temp)
    return out


def _put(out: dict, root: str, path: str, skip_temp: bool) -> None:
    name = os.path.basename(path)
    if skip_temp and _is_temp_name(name):
        return
    try:
        st = os.stat(path)
        if not os.path.isfile(path):
            return
        rel = os.path.relpath(path, root)
        out[_norm(rel)] = (int(st.st_size), int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))))
    except Exception:  # noqa: BLE001
        return


def new_since(before: dict, after: dict) -> list:
    """**这次新建了哪些**（排序输出，便于逐条列）。"""
    return sorted(k for k in (after or {}) if k not in (before or {}))


def removed_since(before: dict, after: dict) -> list:
    """**这次消失/被改名了哪些**。"""
    return sorted(k for k in (before or {}) if k not in (after or {}))


def changed_since(before: dict, after: dict) -> list:
    """**这次被改写了哪些**（字节数或 mtime 变了；两边都存在的才算）。"""
    return sorted(k for k, v in (after or {}).items()
                  if k in (before or {}) and tuple(before[k]) != tuple(v))


def describe(before: dict, after: dict, *, limit: int = 8) -> str:
    """人话一行：`新建 N（a, b…）· 改写 M · 消失 K` —— 直接进日志/报错，省得回看代码。"""
    _n, _c, _r = new_since(before, after), changed_since(before, after), removed_since(before, after)
    def _head(xs):
        return "、".join(xs[:limit]) + ("…" if len(xs) > limit else "")
    parts = ["新建 %d%s" % (len(_n), ("（%s）" % _head(_n)) if _n else ""),
             "改写 %d" % len(_c), "消失 %d" % len(_r)]
    return " · ".join(parts)
