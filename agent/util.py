# -*- coding: utf-8 -*-
"""通用小工具：无业务逻辑（移植自 qq-agent src/util.js + md-to-plain.js + tier-slider.js）。"""
from __future__ import annotations

import json
import os
import random
import re
import time

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def sleep(ms: float) -> None:
    time.sleep(max(0.0, float(ms or 0) / 1000.0))


def rand_int(min_v: float, max_v: float) -> int:
    lo = int(min(min_v, max_v))
    hi = int(max(min_v, max_v))
    if hi <= lo:
        return lo
    return random.randint(lo, hi)


# ── 打开控制台浏览器：配置优先 → 自动探测 Edge/Chrome → 系统默认 ──────────

def pick_browser(exe_path: str = "") -> str:
    """返回要用于打开控制台的浏览器路径；空串 = 用系统默认浏览器。

    优先级：显式路径（server.browser_path）→ 常见 Edge/Chrome 安装位置 → ""。
    解决 Server 系统没有设置默认浏览器（start 不起作用/弹选择框/IE 白屏）的问题。
    """
    if exe_path and os.path.exists(exe_path):
        return exe_path
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Tencent\QQBrowser\QQBrowser.exe",
        r"C:\Program Files (x86)\Tencent\QQBrowser\QQBrowser.exe",
        r"C:\Program Files (x86)\360\360se6\Application\360se.exe",
        r"C:\Program Files\360\360se6\Application\360se.exe",
    ):
        if os.path.exists(p):
            return p
    return ""


# ── 密钥脱敏（控制台/日志不暴露完整 API Key）─────────────────────────────

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,})")


def mask_url_token(url: str) -> str:
    """控制台地址里 ?token= 后的访问口令掩码显示（日志防泄露）；浏览器打开仍用完整地址。"""
    if "?token=" in url:
        head, tail = url.split("?token=", 1)
        return head + "?token=" + (tail[:3] + "***" if tail else "***")
    return url


def mask_secret(secret) -> str:
    """sk-xxxx…后4位 的展示形式；非 sk 前缀也按首尾截断。"""
    s = str(secret or "")
    if not s:
        return ""
    if len(s) <= 8:
        return "••••"
    return s[:5] + "••••" + s[-4:]


def redact_secrets(text) -> str:
    """把文本里的 sk- 长密钥替换为 sk-***（日志/存档脱敏用）。"""
    t = str(text or "")
    if "sk-" not in t:
        return t
    return _SECRET_RE.sub(lambda m: (m.group(1)[:3] + "***"), t)


def pad2(n: int) -> str:
    return str(n).zfill(2)


def format_full_time(ts: float | None = None) -> str:
    """2026-08-30 21:33:05（周六）"""
    if ts is None:
        ts = time.time()
    lt = time.localtime(ts / 1000.0 if ts > 1e12 else ts)
    return "%d-%s-%s %s:%s:%s（%s）" % (
        lt.tm_year, pad2(lt.tm_mon), pad2(lt.tm_mday),
        pad2(lt.tm_hour), pad2(lt.tm_min), pad2(lt.tm_sec),
        WEEKDAYS[lt.tm_wday],
    )


def format_short_time(ts: float | None = None) -> str:
    """08-30 21:33"""
    if ts is None:
        ts = time.time()
    lt = time.localtime(ts / 1000.0 if ts > 1e12 else ts)
    return "%s-%s %s:%s" % (pad2(lt.tm_mon), pad2(lt.tm_mday), pad2(lt.tm_hour), pad2(lt.tm_min))


def format_clock_time(ts: float | None = None) -> str:
    """21:33:05"""
    if ts is None:
        ts = time.time()
    lt = time.localtime(ts / 1000.0 if ts > 1e12 else ts)
    return "%s:%s:%s" % (pad2(lt.tm_hour), pad2(lt.tm_min), pad2(lt.tm_sec))


def today_key(ts: float | None = None) -> str:
    if ts is None:
        ts = time.time()
    lt = time.localtime(ts / 1000.0 if ts > 1e12 else ts)
    return "%d-%s-%s" % (lt.tm_year, pad2(lt.tm_mon), pad2(lt.tm_mday))


# ── 文本处理 ─────────────────────────────────────────────────────────────

def unquote_json_string(value):
    """兼容模型把单条消息序列化成 JSON 字符串的情况：'"你好"' -> '你好'。"""
    if not isinstance(value, str):
        return value
    t = value.strip()
    if t.startswith('"'):
        try:
            parsed = json.loads(t)
            if isinstance(parsed, str):
                return parsed
        except Exception:
            pass
    return value


def normalize_message_list(value):
    """兼容模型把数组序列化成 JSON 字符串传入；字符串 -> 单元素数组。"""
    v = value
    if isinstance(v, str):
        t = v.strip()
        if t.startswith("["):
            try:
                parsed = json.loads(t)
                if isinstance(parsed, list):
                    v = parsed
            except Exception:
                pass
        elif t.startswith('"'):
            uq = unquote_json_string(t)
            if isinstance(uq, str):
                v = uq
    if isinstance(v, list):
        return [str(m or "").strip() for m in v if str(m or "").strip()]
    return [str(v or "").strip()] if str(v or "").strip() else []


def truncate(text: str, max_len: int = 400) -> str:
    s = str(text or "")
    return s if len(s) <= max_len else "%s…(共%d字)" % (s[:max_len], len(s))


# ── Markdown → 纯文本（微信/QQ 都不渲染 Markdown）────────────────────────

def md_to_plain(md: str) -> str:
    s = str(md or "")
    # 代码块：保留内容，去掉围栏
    s = re.sub(r"```[a-zA-Z0-9_+-]*\n?([\s\S]*?)```", lambda m: m.group(1).rstrip("\n"), s)
    # 行内代码
    s = re.sub(r"`([^`\n]+)`", r"\1", s)
    # 图片/链接：保留文字，链接附在括号里
    s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", lambda m: (m.group(1) or m.group(2)), s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1 (\2)", s)
    # 粗体/斜体/删除线
    s = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"~~([^~]+)~~", r"\1", s)
    s = re.sub(r"__([^_]+)__", r"\1", s)
    # 标题符 / 引用符
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"^>\s?", "", s, flags=re.MULTILINE)
    # 列表符
    s = re.sub(r"^\s*[-*+]\s+", "• ", s, flags=re.MULTILINE)
    # 表格：去竖线
    s = re.sub(r"^\s*\|", "", s, flags=re.MULTILINE)
    s = re.sub(r"\|\s*$", "", s, flags=re.MULTILINE)
    # 折叠连续空行
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def split_for_wx(text: str, max_len: int = 2000):
    """按微信单条消息长度上限切分（群聊长文安全切分）。"""
    safe_max = max(1, int(max_len))
    parts = []
    rest = text
    while len(rest) > safe_max:
        cut = rest.rfind("\n", 0, safe_max)
        eat = 0
        if cut <= 0:
            cut = safe_max
        else:
            eat = 1
        # 切点落在 emoji 代理对中间时前移
        if cut > 0 and 0xD800 <= ord(rest[cut - 1]) <= 0xDBFF:
            cut -= 1
        if cut <= 0:
            cut = 1
            eat = 0
        parts.append(rest[: cut + eat])
        rest = rest[cut + eat:]
    if rest:
        parts.append(rest)
    return parts


# ── 响应档位滑条换算（tier-slider.js）────────────────────────────────────

TIER_SLIDER_BANDS = {"tier1_end": 10, "tier2_end": 20, "tier3_end": 90}


def slider_to_tier(pos) -> dict:
    """滑条位置 0~100 -> {tier, randomPercent}。非法值按 100（4 档）处理。"""
    b = TIER_SLIDER_BANDS
    try:
        raw = float(pos)
    except (TypeError, ValueError):
        return {"tier": 4, "randomPercent": 100}
    p = min(100, max(0, raw))
    if p <= b["tier1_end"]:
        return {"tier": 1, "randomPercent": 0}
    if p <= b["tier2_end"]:
        return {"tier": 2, "randomPercent": 0}
    if p <= b["tier3_end"]:
        pct = (p - b["tier2_end"]) / (b["tier3_end"] - b["tier2_end"]) * 100
        return {"tier": 3, "randomPercent": round(pct * 10) / 10}
    return {"tier": 4, "randomPercent": 100}
