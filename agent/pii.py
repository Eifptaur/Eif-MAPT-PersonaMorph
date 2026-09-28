# -*- coding: utf-8 -*-
"""隐私收口：**凡是要离开本机（或进日志/群聊）的文本，先到这里过一遍**。

为什么单开一个模块：同一个"掩码"需求原来散在两处、口径还不一样 ——
  · `agent/wechat.py::_mask_id`（日志里掩自己是谁）；
  · `agent/compat.py` 与 `scripts/collect_report.py` 的**诊断面**却把账号**原名**拼进正文
    （2026-09-28 现场：用户在控制台提交反馈，反馈消息里带着 `账号=wxid_<真号>` 发到了群里）。
    ⇒ 收敛成一份实现，诊断面一律调用它；再加一层 `scrub_text()` 兜底，
    这样"某个小节忘了掩"也不会漏出去（白名单漏一处，兜底还能拦住）。

口径（与原有 `_mask_id` 一致，只是搬到这里）
  长度 ≤8：`前2 + ***`；更长：`前4 + *** + 后3`。
  例：`wxid_txd5z5k7iuy022_e1df` → `wxid***_e1df`（够核对"是不是同一个号"，不足以还原）。
"""
from __future__ import annotations

import re

#: 需要掩掉的 id 形态：微信个人号 / 群聊号 / 两者带后缀的目录名
_ID_PAT = re.compile(r"(?:wxid_[A-Za-z0-9_\-]{4,}|\b\d{4,}@chatroom\b)")


def mask_id(s, head: int = 4, tail: int = 3) -> str:
    """掩码显示一个 id（账号 / 群号）。空值原样返回（别把空印成 `***`）。

    形态尽量保留**可辨认的前后缀**（排障要能对着看"是不是同一个号"），但不足以还原：
      `wxid_txd5z5k7iuy022_e1df` → `wxid_***e1df`（目录后缀在，个人号被掩）
      `12345678901@chatroom`     → `1234***@chatroom`（群号前半 + 群标记）
      其它（短串/自定义名）      → 原口径 `前4 + *** + 后3`（≤8 字符则 `前2 + ***`）
    """
    t = str(s or "")
    if not t:
        return ""
    if t.startswith("wxid_"):
        body = t[5:]
        return "wxid_***" + (body[-4:] if len(body) > 4 else "")
    if t.endswith("@chatroom"):
        body = t[:-9]
        return (body[:head] if len(body) > head + 2 else body[:1]) + "***@chatroom"
    if len(t) <= 8:
        return t[:2] + "***"
    return t[:head] + "***" + t[-tail:]


def scrub_text(s) -> str:
    """把**整段文本**里所有 id 形态掩掉（兜底用：报告/摘要/日志出口各过一次）。

    只做替换，不改其它字符；`None` 当空串。⛔ 这里**不套 try/except**：`str()` 不会抛，
    包一层只会多出一个"取了值"的静默点（本仓静默点棘轮只许降）。
    """
    t = "" if s is None else str(s)
    return _ID_PAT.sub(lambda m: mask_id(m.group(0)), t)
