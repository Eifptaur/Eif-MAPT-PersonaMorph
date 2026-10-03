# -*- coding: utf-8 -*-
"""导出/截图文件名 —— **可读名 ＋ 会话短指纹**（P0-7）。

**为什么需要**：会话名（群名/备注/昵称）**不唯一**，而且清理非法字符之后
不同的会话名可能塌成同一个串（`"a:b"` 与 `"a/b"` 都变成 `a_b`，`"张三"` 与 `"张三 "` 同形）。
批量导出时就会**互相覆盖**，最后只剩最后一个会话的产物，且**当场看不出来**
（文件存在、内容也对，只是数量少了）。

**做法**：文件名 = `sanitize(label)` ＋ `-` ＋ `sha256(session_id)[:10]`。
- **不是**把会话名整串拼进去（Windows 路径长度/非法字符/中文都麻烦），
  而是用**稳定短指纹**保证唯一；
- **不暴露完整会话 ID**（截 10 个 hex ≈ 40 bit，冲突概率对"本机会话数"这一量级可忽略）；
- **同一会话重跑 ⇒ 文件名稳定不变**（哈希是确定的），便于覆盖旧产物、便于判据比对。

**与 P0-6 的关系**：本模块**只管算名字**，落盘一律交 `atomic_io`/`persist`
（否则"名字唯一"也挡不住"写了一半被中断"）。
"""
from __future__ import annotations

import hashlib
import re

#: Windows 非法文件名字符 ＋ 控制字符。**顺带把 `.` 与空格收进替换集**：
#: 首尾的点与空格在 Windows 上会被系统悄悄吃掉（`"a."` 存成 `"a"`），
#: 于是"算出来的名字"与"盘上的名字"对不上 —— 判据比对会假失败。
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: 文件名主干长度上限。Windows 单段上限 255，留出 `-<10位指纹>-<序号><扩展名>` 的余量。
#: 中文在多数文件系统里按 1 字符计，但保守取 48 个字符。
_MAX_STEM = 48


def session_fingerprint(session_id) -> str:
    """会话短指纹：`sha256(str(session_id))[:10]`。

    用 `str()` 而不是要求某个具体类型：调用方给 wxid / 群 id / 数字都不必先转换。
    **空值也产指纹**（`sha256("")`）——这样不会因为"这次没拿到 id"就退化成无后缀，
    从而与其它空 id 的产物互相覆盖。
    """
    return hashlib.sha256(str(session_id if session_id is not None else "").encode("utf-8")).hexdigest()[:10]


def sanitize(label, max_len: int = _MAX_STEM) -> str:
    """把任意文本变成**可读**的文件名主干（不含扩展名）。

    · 非法字符/控制字符 → `_`；
    · 连续的 `_` 收成一个、首尾的 `_`/`.`/空格 去掉（避免 `"..."`、`"a ."` 这类）；
    · 超长截断（`max_len`）—— 截断后**可能变成空串**（例：全是非法字符），
      此时返回 `"unnamed"`（调用方不用再判空）。
    """
    s = _ILLEGAL.sub("_", str(label if label is not None else ""))
    s = re.sub(r"_{2,}", "_", s)
    s = s.strip("_. \t")
    s = s[:int(max_len)]
    s = s.strip("_. ")
    return s or "unnamed"


def build_output_name(session_id, label, ext: str = "", *, suffix: str = "") -> str:
    """`build_output_name(session_id, "张三")` → `"张三-<10位hex>"`（`ext` 非空则接上）。

    `suffix`：同一会话**同一时刻要落多个文件**时用（如多帧：`suffix="-2"`）——
    拼在扩展名之前，**不参与指纹**（指纹只认会话，便于"重跑 → 名字稳定"）。
    `ext`：`"png"` 或 `".png"` 都行，会自动补点。
    """
    head = "%s-%s" % (sanitize(label), session_fingerprint(session_id))
    if suffix:
        head += sanitize(suffix, max_len=16)
    e = str(ext or "").strip()
    if e and not e.startswith("."):
        e = "." + e
    return head + e


def is_same_session_name(a: str, b: str) -> bool:
    """两个文件名是不是**同一个会话**的产物（按指纹段比对）。

    口径：取**最后一段 `-<hex>`** 里的 hex（10 位小写十六进制）。取不到 ⇒ 一律 False
    （"认不出来"不等于"同一个"，宁可当成不同）。
    """
    def _fp(name: str) -> str:
        stem = str(name or "").rsplit(".", 1)[0]
        m = re.search(r"-([0-9a-f]{10})(?:-[^\-]*)?$", stem)
        return m.group(1) if m else ""
    fa, fb = _fp(a), _fp(b)
    return bool(fa) and fa == fb
