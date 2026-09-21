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
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Tencent\QQBrowser\QQBrowser.exe",
        r"C:\Program Files (x86)\Tencent\QQBrowser\QQBrowser.exe",
        r"C:\Program Files (x86)\360\360se6\Application\360se.exe",
        r"C:\Program Files\360\360se6\Application\360se.exe",
    ):
        if os.path.exists(p):
            return p
    return ""


# ── 控制台地址与"谁去开窗"的唯一来源 ────────────────────────────────────
# 2026-09-14 定：地址（含 token）只由**拥有 token 的那一方**写出来，别的人一律读文件。
# 起因（另一台机器实测）：启动器用 IndexOf("\"token\"") 在 config.json 里瞎找口令，
# 结果抓到的是**排在前面的 `cloud.token`（空串）**，于是打开 `/?token=` ⇒ 控制台回
# `{"error":"unauthorized"}`。凡是"手写字符串找 JSON 字段"的路子都会这样踩序问题。

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSOLE_URL_FILE = os.path.join(ROOT, "logs", "console.url")
CONSOLE_LOCK_FILE = os.path.join(ROOT, "logs", "browser_opened.lock")
CONSOLE_LOCK_SECONDS = 90


def console_url_path(root: str = "") -> str:
    return os.path.join(root or ROOT, "logs", "console.url")


def console_lock_path(root: str = "") -> str:
    return os.path.join(root or ROOT, "logs", "browser_opened.lock")


_ACL_ONCE = {"console_url": False}


def _tighten_console_url_acl(p: str) -> None:
    """V-R9-27：`logs/console.url` 里是**带口令的完整地址**（明文），只许本人读。

    与 `local_guard` 对 `sd_local.token` 的做法同一套（`icacls` 断继承 + 只授本人/SYSTEM/管理员）——
    复用那个实现，不再写第二份。每进程只做一次（`icacls` 要起两个进程，别挂在热路径上）。
    收紧失败**不当成功**：写一条 warning（口令仍可用，风险只是同机其它账号能读到）。
    """
    if _ACL_ONCE["console_url"]:
        return
    _ACL_ONCE["console_url"] = True
    try:
        import logging
        from .local_guard import _tighten_acl
        why = _tighten_acl(p)
        if why:
            logging.getLogger("persona-morph").warning(
                "logs/console.url 的 ACL 没能收紧：%s（口令仍可用，但同机其它账号可能读得到）", why)
    except Exception as e:                                   # pragma: no cover - 极端环境
        try:
            import logging
            logging.getLogger("persona-morph").warning("收紧 console.url ACL 时异常：%s", e)
        except Exception:
            pass


def write_console_url(url: str, root: str = "") -> bool:
    """把"可直接使用的控制台地址"原子落盘（temp + os.replace）。"""
    try:
        p = console_url_path(root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(url or ""))
        os.replace(tmp, p)
        _tighten_console_url_acl(p)      # V-R9-27：口令文件只许本人读
        return True
    except Exception:
        return False


def read_console_url(root: str = "") -> str:
    """读回控制台地址；没有/空就返回空串（调用方再走配置兜底）。"""
    try:
        with open(console_url_path(root), encoding="utf-8") as f:
            return (f.read() or "").strip()
    except Exception:
        return ""


_NT = os.name == "nt"


def _pid_alive(pid: int) -> bool:
    """进程还在不在（用来判断"写锁的那一方"有没有退出）。

    ⚠️ **绝不能用 `os.kill(pid, 0)` 判活**——Windows 上它等价于 TerminateProcess，
    会把对方**真的杀掉**（Python 文档明写）。Windows 走 OpenProcess + GetExitCodeProcess。
    """
    if pid <= 0:
        return False
    if not _NT:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))     # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong(0)
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return True                            # 查不到 ⇒ 保守当活着
            return code.value == 259                   # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:
        return True


def _read_lock(root: str = ""):
    """读锁 ⇒ (写入时刻, 写入者 pid)。老格式只写了时间戳，那时 pid=0。"""
    try:
        with open(console_lock_path(root), encoding="utf-8") as f:
            parts = (f.read() or "").split()
        t = float(parts[0]) if parts else 0.0
        pid = int(parts[1]) if len(parts) > 1 else 0
        return t, pid
    except Exception:
        return 0.0, 0


def take_console_lock(seconds: float = CONSOLE_LOCK_SECONDS, root: str = "") -> bool:
    """原子抢占"打开控制台"锁：并发下（启动器 / 机器人 / 托盘 / 面板按钮）只有一方成功。

    这是"双窗口"那个 bug 的收口点——所有开窗入口都必须先拿这把锁。

    ⛔ 2026-09-16 修（用户实测："第一次有窗口，我关掉之后第二次点连窗口都不弹了"）：
    锁原来只记时间、新鲜期 90 秒 ⇒ **写锁的那个机器人早已退出**（用户手动关窗 + 关进程）之后，
    启动器仍判"机器人会开窗"（`console_lock_fresh` 为真），而新拉起的机器人
    `take_console_lock()` 又拿不到这把锁 ⇒ **两边都不开窗**，用户永远看不到窗口。
    ⇒ 锁文件改成"时间戳 + pid"，**写锁的进程已死就视为过期**（它不可能再开窗了）。
    """
    mk = console_lock_path(root)
    try:
        os.makedirs(os.path.dirname(mk), exist_ok=True)
        if os.path.exists(mk):
            if console_lock_fresh(seconds, root):
                return False                       # 别人刚开过，且那一方还活着
            try:
                os.remove(mk)                      # 过期锁：清掉再抢
            except Exception:
                pass
        fd = os.open(mk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, ("%r %d" % (time.time(), os.getpid())).encode("ascii", "replace"))
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        return True                                # 极端情况放开，避免"谁都不开"


def console_lock_fresh(seconds: float = CONSOLE_LOCK_SECONDS, root: str = "") -> bool:
    """锁还作不作数 ＝ 没过期 **且** 写锁的进程还活着（= 那一方确实开过窗、且还可能再开）。"""
    t, pid = _read_lock(root)
    if t <= 0:
        return False
    if (time.time() - t) >= float(seconds):
        return False
    if pid != 0 and not _pid_alive(pid):
        return False                               # 写锁的进程已经退出（或 pid 本身非法）⇒ 它不会再开窗
    return True


# ── 密钥脱敏（控制台/日志不暴露完整 API Key）─────────────────────────────
# ⛔ 2026-09-21（第九轮审计 **V-R9-27**）：原来只认 `sk-`（`if "sk-" not in t: return t`），
#   E 线实测**智谱 / 火山方舟 uuid / 百度千帆 / 企微 webhook key / 钉钉 access_token 全部原样漏出**。
#   现在四类都认：①各家 key 的字面形态 ②`键=值`形态（key/access_token/api_key/token/secret/
#   password/authorization）③UUID 形态（企微 webhook 与火山都用它）④`Bearer xxx`。
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{6,}")
_SECRET_RES = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), lambda m: m.group(0)[:3] + "***"),
    (re.compile(r"bce-v3/ALTAK-[A-Za-z0-9]+/[0-9a-f]+", re.I), lambda m: "bce-v3/ALTAK-***"),
    (re.compile(r"[0-9a-f]{32}\.[A-Za-z0-9]{12,}"), lambda m: m.group(0)[:6] + "***"),      # 智谱
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
     lambda m: m.group(0)[:8] + "-****-****-****-************"),                          # 企微 key / 火山
    (_BEARER_RE, lambda m: "Bearer ***"),
    (re.compile(r"(?i)\b(key|access_token|api[-_]?key|token|secret|password|passwd|pwd|authorization)"
                r"(\s*[:=]\s*)(?![Bb]earer\b)([A-Za-z0-9._\-]{6,})"),
     lambda m: m.group(1) + m.group(2) + m.group(3)[:3] + "***"),
)
#: 快速预筛（`redact_secrets` 挂在每一条日志的 formatter 上）：一次预扫决定要不要跑那 6 条规则。
#  ⚠️ 必须把"没有关键词的形态"也扫进来 —— 智谱 key（`32hex.16字母`）与 UUID 形态都不含任何关键词，
#  第一版只用关键词表预筛 ⇒ 这两种当场漏掉（自己踩的，写在判据里守）。
_REDACT_QUICK = re.compile(
    r"sk-|bce-v3|[Kk]ey|KEY|[Tt]oken|TOKEN|secret|Secret|Bearer|bearer|password|passwd|pwd"
    r"|authorization|Authorization|[0-9a-fA-F]{8}-|[0-9a-f]{32}\.")


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
    """把文本里的各家密钥替换成 `前缀***`（日志/存档脱敏用，见上方 `_SECRET_RES`）。"""
    t = str(text or "")
    if not t or not _REDACT_QUICK.search(t):
        return t
    for rx, rep in _SECRET_RES:
        t = rx.sub(rep, t)
    return t


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
