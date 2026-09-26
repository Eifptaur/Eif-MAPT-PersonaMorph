# -*- coding: utf-8 -*-
"""键 → 磁盘名 的转义/哈希 —— **唯一实现点**。

为什么要有这个文件
------------------
`chat_key`（形如 `group:wxid_xxx@chatroom`）要变成文件名/目录名，两套存储各做过一遍：
  · `store`（消息档案 `data/messages/<safe>_<md5前8>.json`）；
  · `memory`（记忆目录 `data/memory/<转义目录>/`）。
两者的 `_key_hash()` **逐字相同**（`md5(key)[:8]`，各 3 行）——同一件事的两份实现，
改一处漏一处就会出现"**同一个群在两套存储里算出不同的哈希**"，
而这正是当初"记忆目录与消息档挂不上"那一类问题的形状。

⇒ 哈希与安全名收口到这里，两边都转发（旧名保留 ⇒ 调用点与既有判据不用动）。

⚠️ **只搬"同一个问题"的那部分**：
  · `_key_hash` / `safe_name`：两处同名同逻辑 ⇒ 合一；
  · `memory._chat_dir_name`（带 legacy 目录名的双向兼容）与 `store.chat_file`（拼完整文件名）
    是**各自的问题**，留在原处 —— 它们本来就调用这里的 `key_hash()`。

⛔ 纪律（`memory` / `store` 都踩过）：**目录名/文件名是单向的，不许反推 chat_key**。
   要还原真名就回写 `_meta.json` 里的原始 `chat_key`（`chat_keys_on_disk()` 那个口径）。
"""
from __future__ import annotations

import hashlib
import re


def key_hash(chat_key: str) -> str:
    """`chat_key` → 8 位稳定哈希（两套存储共用；同 key 必同值、跨进程可复现）。"""
    return hashlib.md5(str(chat_key).encode("utf-8")).hexdigest()[:8]


def safe_name(chat_key: str) -> str:
    """`chat_key` → "给人看"的那一段（只保留 `[a-z0-9_]`，其余换 `_`；**不截断**）。

    ⛔ **它是单向的**：`group:wxid_a-b` 与 `group:wxid_a_b` 会归一成同一个串
    ⇒ 拼目录名/文件名时**必须**再挂 `key_hash()`，唯一性由哈希保证、归一化只用来"给人看"
    （两套存储都因此各踩过一次"两个群共用一份存储、互相覆盖"）。

    截断（Windows 单文件名 255 **字节**上限）是 `memory._chat_dir_name` 自己的事，
    不放在这里 —— 那是"目录名"的问题，不是"安全名"的问题。

    ⚠️ `feedback._safe_name` 是**同名异义**（它处理的是"用户上传的附件名"：取 basename、
    去掉 Windows 非法字符），**不是这里的兄弟** —— 不合并、也不改名到一起去。
    """
    return re.sub(r"[^a-z0-9_]", "_", str(chat_key), flags=re.IGNORECASE)
