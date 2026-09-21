# -*- coding: utf-8 -*-
"""安全抓取层（移植自 qq-agent src/safe-fetch.js 的 SSRF 全防护）。

- 仅 http/https；禁止 URL 内嵌凭据；
- 禁止 localhost / .local / 私有 IP / 环回 / 链路本地 / CGNAT 等内网地址；
- 域名先 DNS 解析并检查全部解析结果；连接固定到已校验的 IP（防 DNS rebinding）；
- HTTPS 时 SNI/证书校验仍针对原始域名；
- 手动跟随重定向，每一跳重新校验；
- 响应体限量读取，避免超大响应拖垮进程。

第九轮审计（V-R9-23~26）补的三件：
- `CredentialStrippingRedirectHandler` + `harden_urllib()`：urllib 跟 302 时**凭据不许带到新主机**
  （`requests` 有 `rebuild_auth()`，urllib 没有 ⇒ 这是产品里"出图 key 被 302 带去 localhost"的根因）；
- `guard_remote_url()`：**远端回包里的地址**在下手前统一过闸门（假后端拿它当内网探测器用）；
- `fetch_pinned()` / `read_capped()` / `read_stream()`：复用本模块的**钉 IP** 连接与"带上限读体"，
  供 `user_tools` 等调用方直接用（免得再写一套）。
"""
from __future__ import annotations

import http.client
import ipaddress
import logging
import socket
import ssl
import time
import urllib.parse
import urllib.request
from urllib.parse import urlsplit, urljoin

from .config import get_config

log = logging.getLogger("persona-morph")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Persona Morph/1.0"

#: 响应体默认上限（V-R9-26）。取值理由：产品所有外发请求里最大的正常回包是几 MB 的图/音视频
#: （图片 1~2MB、音频条 0.1~1MB、TTS/变声 wav 几 MB）⇒ 8MB 足够覆盖正常业务，
#: 又能在"对面无限灌数据"时把峰值内存钉在这个量级。各调用点可给更小的值（见那里的注释）。
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

#: 跟重定向时**不许带到新主机**的凭据类头（与 `requests` 的 `rebuild_auth()` 同一口径，另加本机口令头）。
CREDENTIAL_HEADERS = frozenset((
    "authorization", "proxy-authorization", "cookie", "cookie2",
    "x-api-key", "api-key", "x-auth-token", "x-access-token", "x-pm-token",
))


class FetchError(Exception):
    pass


# ── 跨主机重定向剥凭据（V-R9-23）────────────────────────────────────────────

def _origin(url) -> tuple:
    """把 URL 归一成 `(scheme, host, port)`（缺省端口按 scheme 补），用来判"同源"。"""
    p = urlsplit(str(url or ""))
    scheme = str(p.scheme or "").lower()
    return (scheme, str(p.hostname or "").lower(),
            p.port or (443 if scheme == "https" else 80))


def _same_origin(a, b) -> bool:
    try:
        return bool(a) and bool(b) and _origin(a) == _origin(b)
    except Exception:
        return False


def _strip_credentials(req) -> int:
    """把一个 Request 上的凭据类头全删掉，返回删了几条。"""
    n = 0
    for store in (getattr(req, "headers", None), getattr(req, "unredirected_hdrs", None)):
        if not isinstance(store, dict):
            continue
        for k in [k for k in list(store) if str(k).lower() in CREDENTIAL_HEADERS]:
            store.pop(k, None)
            n += 1
    return n


class CredentialStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """跟 301/302/303/307/308 时，落点只要**换了主机或换了 scheme** 就把凭据类头剥掉。

    urllib 默认的 `redirect_request()` 只排除 `content-length/content-type` ⇒ `Authorization`
    （以及 `X-Api-Key` / `Cookie` / 本机口令 `X-PM-Token`）会跟着跳到**新主机**上；
    `requests` 有 `rebuild_auth()` 会剥，urllib 没有（审计实测：302 之后对面那一侧真的收到了凭据头 ——
    用的是审计自己的假 key，这里不复写那串字面量，免得**这行注释本身**撞上出包闸门的"凭据形态"规则）。
    **同源跳转不剥**——同主机换路径必须保留鉴权，否则正常功能会坏。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        try:
            if not _same_origin(getattr(req, "full_url", ""), getattr(new, "full_url", "")):
                _strip_credentials(new)
        except Exception:
            pass
        return new


_HARDENED = {"ok": False}


def harden_urllib() -> bool:
    """把"跨主机剥凭据"的处理器装进 **urllib 的全局 opener**（幂等、永不抛）。

    为什么装全局而不是逐处改：产品里有 6 处以上直接 `urllib.request.urlopen()` 发带凭据的请求
    （出图 key / 清单里的 `X-Key` / cloud Bearer / feedback / voice_models），逐处改容易漏一处；
    装一次则**所有** urllib 调用点都过这道闸，且对"自己 `build_opener()`"的调用方零影响。
    """
    if _HARDENED["ok"]:
        return True
    try:
        urllib.request.install_opener(
            urllib.request.build_opener(CredentialStrippingRedirectHandler))
        _HARDENED["ok"] = True
        return True
    except Exception as e:                                   # pragma: no cover - 极端环境
        log.warning("安装「跨主机剥凭据」重定向处理器失败：%s", e)
        return False


# ── 带上限读体（V-R9-26）──────────────────────────────────────────────────

def read_capped(resp, max_bytes: int = 0, what: str = "响应体") -> bytes:
    """**带上限**读一次响应体：先要 `上限+1` 字节，超了立刻抛（绝不整包收进内存）。

    ⚠️ 超限＝**拒绝**而不是截断：被截断的 JSON 解析出来是错的，而"错了还当成功"更难查。
    """
    cap = int(max_bytes or DEFAULT_MAX_BYTES)
    body = resp.read(cap + 1)
    if body is None:
        return b""
    if len(body) > cap:
        raise FetchError("%s超过 %.1fMB 上限，已按拒绝处理（V-R9-26）" % (what, cap / 1048576.0))
    return body


def iter_response(resp, chunk: int = 65536):
    """把 `requests` 的流式响应 / `urlopen` 的响应统一成"一块一块的字节"。"""
    if hasattr(resp, "iter_content"):
        return resp.iter_content(chunk_size=int(chunk))

    def _gen():
        while True:
            b = resp.read(int(chunk))
            if not b:
                break
            yield b
    return _gen()


def read_stream(resp, max_bytes: int = 0, budget_s: float = 0.0, t0: float = None,
                what: str = "响应体") -> bytes:
    """**分块**读完，边读边核"总上限"与"整轮墙钟预算"（超了就抛并尽力断开）。

    为什么不是一把 `resp.read()`：`urlopen(timeout=)` / `requests(timeout=)` 都只管**单次 recv**，
    对面每 6 秒吐 1 字节就能把"声明 15 秒"的请求拖到 63 秒（审计实测 V-R9E-6）。
    这里在**每块之间**核一次墙钟 ⇒ 总耗时有上限（≈ budget + 一次 recv 的 timeout）。
    """
    cap = int(max_bytes or DEFAULT_MAX_BYTES)
    t0 = time.monotonic() if t0 is None else t0
    got = bytearray()
    for buf in iter_response(resp):
        if not buf:
            continue
        got += buf
        if len(got) > cap:
            _close_quiet(resp)
            raise FetchError("%s超过 %.1fMB 上限，已中止读取（V-R9-26）" % (what, cap / 1048576.0))
        if budget_s and (time.monotonic() - t0) > float(budget_s):
            _close_quiet(resp)
            raise TimeoutError("总耗时超过 %.1f 秒的墙钟预算，已放弃（V-R9-27）" % float(budget_s))
    return bytes(got)


def _close_quiet(resp) -> None:
    try:
        resp.close()
    except Exception:
        pass


def _is_private_ip(ip_str: str) -> bool:
    h = str(ip_str or "").strip().lower().strip("[]")
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return True
    # ⚠️ 2026-09-21（第九轮 V-R9-25）：**再加一条 `not ip.is_global`** —— 只看
    # `is_private/loopback/link_local/multicast/reserved/unspecified` 会漏掉 CGNAT（`100.64.0.0/10`，
    # 实测 `100.64.0.1`：`is_private=False`、`is_global=False`）以及 192.0.0.0/24、198.18.0.0/15 这些
    # 特殊段（它们同样不是"能被外网访问的正常目标"）。判据 `safe_fetch_selftest` 里逐条守。
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified or not ip.is_global)


def _resolve_safe_host(hostname: str, port: int, allow_private: bool = False) -> str:
    h = str(hostname or "").strip().lower().strip("[]")
    if not h:
        raise FetchError("主机名为空")
    if not allow_private and (h == "localhost" or h.endswith(".localhost") or h.endswith(".local")):
        raise FetchError("禁止访问内网/本机地址")
    # 字面量 IP
    try:
        ipaddress.ip_address(h)
        if not allow_private and _is_private_ip(h):
            raise FetchError("禁止访问内网/本机地址")
        return h
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(h, port, type=socket.SOCK_STREAM)
    except Exception as e:
        raise FetchError("域名解析失败：%s" % e)
    addrs = []
    for info in infos:
        ip = info[4][0]
        if not allow_private and _is_private_ip(ip):
            raise FetchError("域名解析到内网/本机地址，已阻止")
        addrs.append(ip)
    if not addrs:
        raise FetchError("域名没有解析结果")
    return addrs[0]


def validate_url(raw: str, allow_private: bool = False):
    """校验 URL 的 scheme 与主机（DNS 级）。返回 (scheme, host, port, path, ip)。"""
    try:
        parts = urlsplit(str(raw or "").strip())
    except Exception:
        raise FetchError("URL 无效")
    if parts.scheme not in ("http", "https"):
        raise FetchError("仅允许 http/https")
    if not parts.hostname:
        raise FetchError("URL 缺少主机名")
    if parts.username or parts.password:
        raise FetchError("URL 不能包含凭据")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    ip = _resolve_safe_host(parts.hostname, port, allow_private)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.scheme, parts.hostname, port, path, ip


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """连接固定 IP，但 SNI/证书校验仍针对原始域名（防 DNS rebinding 的关键）。"""

    def __init__(self, host, ip, port=None, timeout=20, **kw):
        self._pinned_ip = ip
        super().__init__(host, port=port, timeout=timeout, **kw)

    def connect(self):
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if self._tunnel_host:
            self._tunnel()
        if self._context is None:
            self._context = ssl._create_default_https_context()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, ip, port=None, timeout=20, **kw):
        self._pinned_ip = ip
        super().__init__(host, port=port, timeout=timeout, **kw)

    def connect(self):
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if self._tunnel_host:
            self._tunnel()


def _pinned_exchange(scheme, host, port, path, ip, method="GET", data=None, headers=None,
                     max_bytes=DEFAULT_MAX_BYTES):
    """用**已校验的 IP** 发一次请求（连接时不再解析域名 ⇒ 没有 TOCTOU/rebinding 窗口）。"""
    cls = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
    conn = cls(host, ip, port=port, timeout=20)
    h = {"Host": host, "User-Agent": UA,
         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8,image/*;q=0.8",
         "Accept-Language": "zh-CN,zh;q=0.9"}
    for k, v in (headers or {}).items():
        if str(k).lower() == "host":
            continue
        h[str(k)] = str(v)
    conn.request(str(method or "GET").upper(), path, body=data, headers=h)
    resp = conn.getresponse()
    status = resp.status
    hdrs = {}
    try:
        hdrs = {k.lower(): v for k, v in (resp.getheaders() or [])}
    except Exception:
        pass
    cap = int(max_bytes or DEFAULT_MAX_BYTES)
    body = resp.read(cap + 1)
    truncated = len(body) > cap
    body = body[:cap]
    conn.close()
    return {"status": status, "headers": hdrs,
            "location": str(resp.getheader("location") or ""),
            "content_type": str(resp.getheader("content-type") or ""),
            "body": body, "truncated": truncated}


def _pinned_request(scheme, host, port, path, ip, max_bytes, as_binary):
    """（保留旧签名与旧返回形状：审计/判据里 stub 的就是它）不带凭据的钉 IP GET。"""
    r = _pinned_exchange(scheme, host, port, path, ip, "GET", None, None, max_bytes)
    return {"status_code": r["status"], "content_type": r["content_type"],
            "location": r["location"], "body": r["body"], "truncated": r["truncated"]}


MAX_REDIRECTS = 5


def fetch_pinned(url, method="GET", data=None, headers=None, timeout=20, max_bytes=DEFAULT_MAX_BYTES,
                 allow_private=False, host_allowed=None, max_redirects=MAX_REDIRECTS):
    """带 SSRF 闸门的**钉 IP** 请求 ⇒ `{url, status, headers, body, truncated}`。

    V-R9-25（`user_tools` 的 TOCTOU）：校验时解析到的 IP **直接钉进 socket**，连接时不再解析
    ⇒ DNS rebinding 打不到环回。逐跳复校（每一跳重新 `validate_url`），跨主机/跨 scheme 的跳转
    **剥掉凭据类头**（与 `CredentialStrippingRedirectHandler` 同口径）。
    `host_allowed(url) -> bool`：调用方自己的白名单（每跳都问一次）；不通过抛 `FetchError`。
    """
    cur = str(url or "")
    hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
    m = str(method or "GET").upper()
    body = data
    for _ in range(int(max_redirects) + 1):
        if host_allowed is not None and not host_allowed(cur):
            raise FetchError("地址的主机不在允许清单里：%s" % cur)
        scheme, host, port, path, ip = validate_url(cur, allow_private=allow_private)
        r = _pinned_exchange(scheme, host, port, path, ip, m, body, hdrs, max_bytes)
        if r["status"] in (301, 302, 303, 307, 308) and r["location"]:
            nxt = urljoin(cur, r["location"])
            if not _same_origin(cur, nxt):
                for k in [k for k in list(hdrs) if k.lower() in CREDENTIAL_HEADERS]:
                    hdrs.pop(k, None)
            if r["status"] == 303 or (r["status"] in (301, 302) and m == "POST"):
                m, body = "GET", None               # 与 urllib / requests 同口径
            cur = nxt
            continue
        return {"url": cur, "status": r["status"], "headers": r["headers"],
                "body": r["body"], "truncated": r["truncated"]}
    raise FetchError("重定向次数过多，已停止")


def guard_remote_url(url: str, base_url: str = "") -> str:
    """**远端回包里的地址**在下手之前统一过闸门（V-R9-24）；被拒抛 `FetchError`。

    · 与 `base_url`（我们主动联系的那个后端）**同源** ⇒ 只是把同一台服务上的静态资源取回来，
      不增加可达面（本机后端回本机地址是本地部署的常态）⇒ 允许；
    · 其它一律按公网口径核 ⇒ `127.0.0.1` / `169.254.169.254` / `localhost.` / `[::ffff:127.0.0.1]` /
      `127.1` / `0x7f000001` / `2130706433` / `100.64.0.1` 全部拦（`validate_url` 实测全拦）。
    """
    u = str(url or "").strip()
    if not u:
        raise FetchError("远端没有给出可用地址")
    validate_url(u, allow_private=_same_origin(u, base_url))
    return u


def safe_fetch(url_string: str, max_chars: int = 50000):
    """抓取网页文本（≤50000 字符），SSRF 全防护。"""
    scheme, host, port, path, ip = validate_url(url_string)
    current_url = url_string
    for _ in range(MAX_REDIRECTS + 1):
        result = _pinned_request(scheme, host, port, path, ip, max_chars, as_binary=False)
        if result["status_code"] in (301, 302, 303, 307, 308):
            if not result["location"]:
                raise FetchError("重定向缺少 Location：%d" % result["status_code"])
            next_url = urljoin(current_url, result["location"])
            current_url = next_url
            scheme, host, port, path, ip = validate_url(next_url)
            continue
        body = result["body"]
        try:
            text = body.decode("utf-8", "ignore")
        except Exception:
            text = body.decode("latin-1", "ignore")
        return {"url": current_url, "status_code": result["status_code"],
                "truncated": result["truncated"], "body": text}
    raise FetchError("重定向次数过多，已停止")
