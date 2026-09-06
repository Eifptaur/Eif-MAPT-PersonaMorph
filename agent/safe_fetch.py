# -*- coding: utf-8 -*-
"""安全抓取层（移植自 qq-agent src/safe-fetch.js 的 SSRF 全防护）。

- 仅 http/https；禁止 URL 内嵌凭据；
- 禁止 localhost / .local / 私有 IP / 环回 / 链路本地 / CGNAT 等内网地址；
- 域名先 DNS 解析并检查全部解析结果；连接固定到已校验的 IP（防 DNS rebinding）；
- HTTPS 时 SNI/证书校验仍针对原始域名；
- 手动跟随重定向，每一跳重新校验；
- 响应体限量读取，避免超大响应拖垮进程。
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urljoin

from .config import get_config


class FetchError(Exception):
    pass


def _is_private_ip(ip_str: str) -> bool:
    h = str(ip_str or "").strip().lower().strip("[]")
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


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


def _pinned_request(scheme, host, port, path, ip, max_bytes, as_binary):
    cls = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
    conn = cls(host, ip, port=port, timeout=20)
    conn.request("GET", path, headers={
        "Host": host,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) wx-agent/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8,image/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    resp = conn.getresponse()
    status = resp.status
    content_type = resp.getheader("content-type") or ""
    location = resp.getheader("location") or ""
    body = resp.read(max_bytes + 1)
    truncated = len(body) > max_bytes
    body = body[:max_bytes]
    conn.close()
    return {"status_code": status, "content_type": content_type,
            "location": location, "body": body, "truncated": truncated}


MAX_REDIRECTS = 5


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


def safe_fetch_binary(url_string: str, max_bytes: int = 12 * 1024 * 1024):
    """下载二进制（图片），返回 {buffer, content_type}。"""
    allow_private = get_config().get("security", {}).get("allow_private_image_hosts") is True
    scheme, host, port, path, ip = validate_url(url_string, allow_private)
    current_url = url_string
    for _ in range(MAX_REDIRECTS + 1):
        result = _pinned_request(scheme, host, port, path, ip, max_bytes, as_binary=True)
        if result["status_code"] in (301, 302, 303, 307, 308):
            if not result["location"]:
                raise FetchError("重定向缺少 Location：%d" % result["status_code"])
            next_url = urljoin(current_url, result["location"])
            current_url = next_url
            scheme, host, port, path, ip = validate_url(next_url, allow_private)
            continue
        if result["status_code"] != 200:
            raise FetchError("HTTP %d" % result["status_code"])
        return {"buffer": result["body"], "content_type": result["content_type"]}
    raise FetchError("重定向次数过多，已停止")


def validate_image_url(raw: str) -> str:
    """图片地址校验：默认内网地址一律拒绝。"""
    try:
        parts = urlsplit(str(raw or "").strip())
    except Exception:
        raise FetchError("图片地址不合法")
    if parts.scheme not in ("http", "https"):
        raise FetchError("只允许 http(s) 图片地址")
    if get_config().get("security", {}).get("allow_private_image_hosts") is True:
        return raw
    scheme, host, port, path, ip = validate_url(raw)
    return "%s://%s%s" % (scheme, host, path)
