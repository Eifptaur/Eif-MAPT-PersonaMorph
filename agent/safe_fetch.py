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
import os
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


def _sock_of(resp):
    """尽量摸到响应背后的 socket（拿不到就 None，绝不抛）。"""
    try:
        for _o in (getattr(resp, "fp", None), getattr(getattr(resp, "raw", None), "_fp", None), resp):
            if _o is None:
                continue
            _s = getattr(_o, "_sock", None) or getattr(getattr(_o, "raw", None), "_sock", None)
            if _s is not None:
                return _s
    except Exception:
        pass
    return None


def _arm_budget(resp, t0: float, budget_s: float, what: str) -> None:
    """把"整轮墙钟预算"落到**下一次 recv 的 socket 超时**上（V-R10-34 第 4 条）。

    为什么老写法不够（审计实测）：只在**两块之间**核墙钟 ⇒ 对面"连上就不吐字节"时，
    卡在单次 `recv` 上的那段时间**完全不进预算**（`budget_s=2` 实测 10 秒才返回、0 字节）。
    现在每轮动手前先算剩余预算：①已经超了 ⇒ 当场抛；②还没超 ⇒ 把 socket 超时**压到**
    剩余预算（只收紧、不放宽；下限 0.2 秒免得 0 超时变成非阻塞空转）。
    拿不到 socket 时退化成"只在块之间核"（老行为，至少不比原来差）。
    """
    if not budget_s:
        return
    left = float(budget_s) - (time.monotonic() - float(t0))
    if left <= 0:
        _close_quiet(resp)
        raise TimeoutError("总耗时超过 %.1f 秒的墙钟预算，已放弃（V-R9-27）" % float(budget_s))
    _s = _sock_of(resp)
    if _s is None:
        return
    try:
        _cur = _s.gettimeout()
    except Exception:
        _cur = None
    _new = max(0.2, left)
    try:
        if _cur is None or _cur <= 0 or _new < float(_cur):
            _s.settimeout(_new)
    except Exception:
        pass


def read_stream(resp, max_bytes: int = 0, budget_s: float = 0.0, t0: float = None,
                what: str = "响应体") -> bytes:
    """**分块**读完，边读边核"总上限"与"整轮墙钟预算"（超了就抛并尽力断开）。

    为什么不是一把 `resp.read()`：`urlopen(timeout=)` / `requests(timeout=)` 都只管**单次 recv**，
    对面每 6 秒吐 1 字节就能把"声明 15 秒"的请求拖到 63 秒（审计实测 V-R9E-6）。
    V-R10-34 第 4 条：光在**块之间**核还不够 —— 对面**一块都不吐**时预算根本没生效
    （实测 `budget_s=2` / 10 秒才返回 / 0 字节）⇒ 现在**每次 recv 之前**先把 socket
    超时压到剩余预算（`_arm_budget`），不吐字节也会在预算附近退出。
    """
    cap = int(max_bytes or DEFAULT_MAX_BYTES)
    t0 = time.monotonic() if t0 is None else t0
    got = bytearray()
    it = iter_response(resp)
    try:
        while True:
            _arm_budget(resp, t0, budget_s, what)     # ⬅ 动手前先核预算 + 压超时
            try:
                buf = next(it)
            except StopIteration:
                break
            if not buf:
                continue
            got += buf
            if len(got) > cap:
                _close_quiet(resp)
                raise FetchError("%s超过 %.1fMB 上限，已中止读取（V-R9-26）" % (what, cap / 1048576.0))
            if budget_s and (time.monotonic() - t0) > float(budget_s):
                _close_quiet(resp)
                raise TimeoutError("总耗时超过 %.1f 秒的墙钟预算，已放弃（V-R9-27）" % float(budget_s))
    except BaseException:
        # 任何异常路径都**尽力断开**（socket 超时是从 `read` 里抛出来的那一种，
        # 老写法在这里会把连接挂着 —— V-R10-34 第 4 条配套）。
        _close_quiet(resp)
        raise
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
    """连接固定 IP，但 SNI/证书校验仍针对原始域名（防 DNS rebinding 的关键）。

    ⚠️ 证书校验**不许关**（V-R10-38 的 G015 变异点）：这里用的是
    `ssl._create_default_https_context()`（= `create_default_context()`，`CERT_REQUIRED` +
    `check_hostname=True`）。任何人把它换成 `_create_unverified_context()` /
    `check_hostname=False` / `verify_mode=CERT_NONE` 都等于把 HTTPS 降成明文，
    判据 `safe_fetch_selftest` 的 L2 段逐条钉住这一点。
    """

    def __init__(self, host, ip, port=None, timeout=20, **kw):
        self._pinned_ip = ip
        super().__init__(host, port=port, timeout=timeout, **kw)

    def connect(self):
        # ⚠️ 连接目标是**已校验的那个 IP**（`self._pinned_ip`），不是域名 ⇒ 不给 DNS 第二次机会。
        # 判据 L1 段把 `socket.create_connection` 打桩，断言这里拿到的就是闸门校验过的 IP。
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
                     max_bytes=DEFAULT_MAX_BYTES, timeout=None, out_path=None):
    """用**已校验的 IP** 发一次请求（连接时不再解析域名 ⇒ 没有 TOCTOU/rebinding 窗口）。

    `timeout`：**调用方的超时**要能传进去（V-R10-34：老写法写死 20s，`user_tools` 的
    `timeout_ms` 形同虚设——实测 20.0s vs urlopen 1.0s）。不给才回落到 20s。
    `out_path`：给了就**边收边写文件**（`fetch_pinned_stream` 用），否则字节放 `body`。
    两者都按 `max_bytes` 上限截断并如实回 `truncated`（不谎报，V-R10-38 的 G024）。
    """
    cls = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
    conn = cls(host, ip, port=port, timeout=float(timeout or PINNED_TIMEOUT_S))
    # `Host` 由**我们**按域名给出：调用方同名的头一律跳过 ⇒ 不许覆盖（V-R10-38 的 G023）。
    h = {"Host": host, "User-Agent": UA,
         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8,image/*;q=0.8",
         "Accept-Language": "zh-CN,zh;q=0.9"}
    for k, v in (headers or {}).items():
        if str(k).lower() == "host":
            continue
        h[str(k)] = str(v)
    try:
        conn.request(str(method or "GET").upper(), path, body=data, headers=h)
        resp = conn.getresponse()
        status = resp.status
        hdrs = {}
        try:
            hdrs = {k.lower(): v for k, v in (resp.getheaders() or [])}
        except Exception:
            pass
        cap = int(max_bytes or DEFAULT_MAX_BYTES)
        location = str(resp.getheader("location") or "")
        ctype = str(resp.getheader("content-type") or "")
        got = 0
        truncated = False
        buf = bytearray()
        fh = None
        write = None
        try:
            if out_path:
                fh = open(out_path, "wb")
                write = fh.write
            else:
                write = buf.extend
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                got += len(chunk)
                if got > cap:
                    # 超限：**拒绝**（与 `read_capped` 同口径），把已收的按 cap 截断并把
                    # `truncated=True` 如实回给调用方（不许谎报，V-R10-38 的 G024）
                    write(chunk[:max(0, cap - (got - len(chunk)))])
                    got = cap
                    truncated = True
                    break
                write(chunk)
        finally:
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
        body = b"" if out_path else bytes(buf)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {"status": status, "headers": hdrs, "location": location, "content_type": ctype,
            "body": body, "truncated": truncated, "bytes": got, "path": out_path or ""}


def _pinned_request(scheme, host, port, path, ip, max_bytes, as_binary):
    """（保留旧签名与旧返回形状：审计/判据里 stub 的就是它）不带凭据的钉 IP GET。"""
    r = _pinned_exchange(scheme, host, port, path, ip, "GET", None, None, max_bytes)
    return {"status_code": r["status"], "content_type": r["content_type"],
            "location": r["location"], "body": r["body"], "truncated": r["truncated"]}


MAX_REDIRECTS = 5

#: 钉 IP 传输层的**默认**超时（调用方不给才用它）。V-R10-34：老写法把 20s **写死**在
#: `fetch_pinned` 里 ⇒ `user_tools` 的 `timeout_ms` 完全失效（实测 20.0s vs urlopen 1.0s）。
PINNED_TIMEOUT_S = 20.0


def _exchange(scheme, host, port, path, ip, method, data, headers, max_bytes,
              timeout=None, out_path=None):
    """调 `_pinned_exchange`，**按需要加长参数表**（判据/替身只声明 9 个形参时也能打桩）。

    为什么要这层：`timeout`/`out_path` 是本轮新加的两个能力（V-R10-34 的"调用方超时"、
    V-R10-32 的"流式落盘"），只在**真的需要**时才多传两个位置参数——否则旧判据里那些
    9 参替身（`def fake_exch(scheme, host, port, path, ip, method="GET", data=None,
    headers=None, max_bytes=...)`）会 TypeError 炸掉。**不是放水**：
    `timeout` 有值（含显式 0/默认 20）与 `out_path` 有值这两种"新能力真被用到"的场合，
    一律传满 11 个位置参数，打桩者必须如实声明。
    """
    if timeout is None and out_path is None:
        return _pinned_exchange(scheme, host, port, path, ip, method, data, headers, max_bytes)
    return _pinned_exchange(scheme, host, port, path, ip, method, data, headers, max_bytes,
                            timeout, out_path)


def _exchange_head(scheme, host, port, path, ip, headers=None, timeout=None):
    """`pinned_head` 的传输面：**只发 HEAD、不读体**，且可被打桩（判据靠它断言连接目标）。"""
    cls = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
    conn = cls(host, ip, port=port, timeout=float(timeout or PINNED_TIMEOUT_S))
    h = {"Host": host, "User-Agent": UA, "Connection": "close"}
    for k, v in (headers or {}).items():
        if str(k).lower() == "host":
            continue
        h[str(k)] = str(v)
    try:
        conn.request("HEAD", path, body=None, headers=h)
        resp = conn.getresponse()
        return {"status": int(resp.status), "location": str(resp.getheader("location") or "")}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def pinned_head(url, headers=None, timeout=None, max_redirects=MAX_REDIRECTS):
    """带 SSRF 闸门的**钉 IP HEAD**（V-R10-33 的修法）⇒ `{url, status}`。

    为什么不能"TCP/TLS 段钉了 IP、HTTP 段还按域名发"（老 `cloud.probe` 就是这么写的）：
    那一段会**第 3 次解析域名**，审计实测第 3 次给环回 ⇒ HEAD 真打到 `127.0.0.1`，
    而面板报 `ok=true / stage=http / 200`（把内网当成了"可达"）。
    这里每一跳都 `validate_url` 解一次、**拿到的 IP 直接连**，与 `fetch_pinned` 同一口径；
    手动跟 30x 且跨源剥凭据（本函数不带凭据，剥的是调用方给的头）。
    """
    cur = str(url or "")
    hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
    for _ in range(int(max_redirects) + 1):
        scheme, host, port, path, ip = validate_url(cur)
        r = _exchange_head(scheme, host, port, path, ip, hdrs, timeout)
        if r["status"] in (301, 302, 303, 307, 308) and r["location"]:
            nxt = urljoin(cur, r["location"])
            if not _same_origin(cur, nxt):
                for k in [k for k in list(hdrs) if k.lower() in CREDENTIAL_HEADERS]:
                    hdrs.pop(k, None)
            cur = nxt
            continue
        return {"url": cur, "status": r["status"]}
    raise FetchError("重定向次数过多，已停止")


def _pin_loop(url, method="GET", data=None, headers=None, timeout=None, max_bytes=DEFAULT_MAX_BYTES,
              allow_private=False, host_allowed=None, max_redirects=MAX_REDIRECTS, out_path=None):
    """**唯一**的"校验 + 钉 IP + 逐跳复校 + 跨主机剥凭据"循环（`fetch_pinned` /
    `fetch_pinned_stream` / `cloud.probe` 都走它，不许各写一套）。

    口径（V-R10-32/33 的核心）：**校验用的地址与连接用的 IP 必须是同一次解析的结果**——
    `validate_url` 一次解出 IP，这个 IP 直接交给 `_pinned_exchange` 钉进 socket；
    绝不允许"校验一次、连接时再解析一次"（那就是 DNS rebinding 的穿透窗口）。
    """
    cur = str(url or "")
    hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
    m = str(method or "GET").upper()
    body = data
    for _ in range(int(max_redirects) + 1):
        if host_allowed is not None and not host_allowed(cur):
            raise FetchError("地址的主机不在允许清单里：%s" % cur)
        scheme, host, port, path, ip = validate_url(cur, allow_private=allow_private)
        r = _exchange(scheme, host, port, path, ip, m, body, hdrs, max_bytes,
                      timeout, out_path)
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
                "location": r["location"], "content_type": r["content_type"],
                "body": r["body"], "truncated": r["truncated"],
                "bytes": r.get("bytes", len(r["body"])), "path": r.get("path", "")}
    raise FetchError("重定向次数过多，已停止")


def fetch_pinned(url, method="GET", data=None, headers=None, timeout=None, max_bytes=DEFAULT_MAX_BYTES,
                 allow_private=False, host_allowed=None, max_redirects=MAX_REDIRECTS):
    """带 SSRF 闸门的**钉 IP** 请求 ⇒ `{url, status, headers, body, truncated}`。

    V-R9-25（`user_tools` 的 TOCTOU）：校验时解析到的 IP **直接钉进 socket**，连接时不再解析
    ⇒ DNS rebinding 打不到环回。逐跳复校（每一跳重新 `validate_url`），跨主机/跨 scheme 的跳转
    **剥掉凭据类头**（与 `CredentialStrippingRedirectHandler` 同口径）。
    `host_allowed(url) -> bool`：调用方自己的白名单（每跳都问一次）；不通过抛 `FetchError`。
    `timeout`：**用调用方给的那个**（V-R10-34；不给则由 `_pinned_exchange` 回落到
    `PINNED_TIMEOUT_S`）。⚠️ 显式给值时它一定会传进传输层——判据 L4 段把
    `socket.create_connection` 打桩，断言"调用方说 1 秒，socket 拿到的就是 1 秒"。
    """
    r = _pin_loop(url, method=method, data=data, headers=headers, timeout=timeout,
                  max_bytes=max_bytes, allow_private=allow_private, host_allowed=host_allowed,
                  max_redirects=max_redirects)
    return {"url": r["url"], "status": r["status"], "headers": r["headers"],
            "body": r["body"], "truncated": r["truncated"]}


def fetch_pinned_stream(url, dest, method="GET", data=None, headers=None, timeout=None,
                        max_bytes=DEFAULT_MAX_BYTES, allow_private=False, host_allowed=None,
                        max_redirects=MAX_REDIRECTS):
    """**钉 IP 流式下载到文件**（V-R10-32/34 的统一出口）⇒ `{url, status, headers, bytes,
    truncated}`；失败**不留半截文件**。

    为什么要有它：`bilibili._download` / `image_gen` 的二次 GET / `video_gen` 的回链下载
    原先都是"过一遍 `guard_remote_url` 闸门，然后 `urllib.urlopen(url)` **再解析一次域名**"
    ⇒ 闸门校验的是第 1 次解析，真正连接用的是第 2 次解析 —— 审计实测**把环回内容落盘**。
    走这里则与 `fetch_pinned` 同一条循环：**一次解析、钉进 socket**，顺便把
    "整轮墙钟 / 体积上限 / 无上限读体"三个老毛病一起按统一口径收掉。
    """
    dest = str(dest or "")
    if not dest:
        raise FetchError("流式下载要一个目标文件路径")
    d = os.path.dirname(os.path.abspath(dest))
    if d:
        os.makedirs(d, exist_ok=True)
    tmo = PINNED_TIMEOUT_S if timeout is None else timeout
    ok = False
    try:
        r = _pin_loop(url, method=method, data=data, headers=headers, timeout=tmo,
                      max_bytes=max_bytes, allow_private=allow_private, host_allowed=host_allowed,
                      max_redirects=max_redirects, out_path=dest)
        ok = True
        return {"url": r["url"], "status": r["status"], "headers": r["headers"],
                "bytes": int(r.get("bytes") or 0), "truncated": bool(r["truncated"]),
                "content_type": r["content_type"], "path": dest}
    finally:
        if not ok:
            try:
                if os.path.exists(dest):
                    os.remove(dest)
            except Exception:
                pass



def validate_remote_url(url: str, base_url: str = "", allow_private=None):
    """`guard_remote_url` 的**带 IP 版**（V-R10-32/33 的修法）⇒ `(url, ip)`。

    ⚠️ 出网面的核心口径：**校验用的地址与连接用的 IP 必须是同一次解析的结果**。
    老口径只回一个字符串，调用方拿着字符串去 `urllib.urlopen(...)` ⇒ **连接时又解析一次**
    ⇒ 审计实测第 2/3 次解析给环回，环回内容真被落盘（DNS rebinding 穿透闸门）。
    这里把 `validate_url` 那一次解析出的 IP **一起交出来**，调用方必须用它连接
    （`fetch_pinned` / `fetch_pinned_stream` 已经这么做了）。
    """
    u = str(url or "").strip()
    if not u:
        raise FetchError("远端没有给出可用地址")
    if allow_private is None:
        allow_private = _same_origin(u, base_url)
    _scheme, _host, _port, _path, ip = validate_url(u, allow_private=bool(allow_private))
    return u, ip


def guard_remote_url(url: str, base_url: str = "", allow_private=None) -> str:
    """**远端回包里的地址**在下手之前统一过闸门（V-R9-24）；被拒抛 `FetchError`。

    · 与 `base_url`（我们主动联系的那个后端）**同源** ⇒ 只是把同一台服务上的静态资源取回来，
      不增加可达面（本机后端回本机地址是本地部署的常态）⇒ 允许；
    · 其它一律按公网口径核 ⇒ `127.0.0.1` / `169.254.169.254` / `localhost.` / `[::ffff:127.0.0.1]` /
      `127.1` / `0x7f000001` / `2130706433` / `100.64.0.1` 全部拦（`validate_url` 实测全拦）。

    ⚠️ 只回字符串 ⇒ **别拿它配 `urllib.urlopen`**（那是二次解析，V-R10-32）。
    要连接就用 `validate_remote_url()` 拿 IP 后走 `fetch_pinned*`，或 `allow_private` 显式开关
    （V-R10-35：三处 guard 原先没有这个开关，Docker 里回链 `compose` 服务名会被硬拒）。
    """
    u, _ip = validate_remote_url(url, base_url, allow_private)
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
