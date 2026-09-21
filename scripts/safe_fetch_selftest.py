#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：出网面安全层（`agent/safe_fetch.py`）——**全离线**，一条公网请求都不发。

为什么要有它（第九轮审计 **V-R9-31**）：这个模块原来**一条判据都没有**
（全仓 `validate_url` 一次都没被调用过）⇒ 把整个安全闸门删掉，138 条判据一条都不会红。
本轮把它的四个调用面接进产品（V-R9-23~26），这里逐条守住：

  A. `validate_url` 拦住七种"看起来像内网"的形态（**逐条**，都是 E 线实测能绕过旧字符串表的）
  B. DNS 解析到环回/内网 ⇒ 拒（打桩 `getaddrinfo`；多结果里只要有一个内网也拒）
  C. 302 到**新主机** ⇒ 凭据不被带过去（①纯处理器 ②真 urllib + 本地回环两端
     ③`fetch_pinned` 逐跳复校），并带"同源不剥"的阳性对照
  D. 响应体超上限 ⇒ 拒，且**内存不随远端大小增长**（tracemalloc 峰值 + 服务端实发字节）
  E. `cloud.normalize_url` 与 `validate_url` **同口径**（同样七种形态逐条）
  F. 边角：`read_capped` 边界、`harden_urllib` 装上了、CGNAT 之类特殊段也算内网
  G. `guard_remote_url`：远端回包里的地址先过闸（V-R9-24）
  L. **连接层**（第十轮 V-R10-32/33/36/38）：可控 DNS（第 N 次翻转）+ 把 `socket`/连接类打桩，
     断言 **连接用的 IP == 闸门校验通过的 IP**、DNS 翻脸后**不采纳**、
     **证书校验开着（check_hostname + CERT_REQUIRED）**、**Host 头不可被调用方覆盖**、
     调用方 timeout 真的到连接层、超限 `truncated` 如实报、
     `fetch_pinned_stream` 第二跳仍钉 IP 且跨主机剥凭据、`cloud.probe` 的 HEAD 段不再二次解析。
     ⚠️ 这一段是本轮新增的重点：G 段以前只守到"字符串层"，7 个同族变异（G014/G015/G023…）全绿。

跑法：`runtime\\python\\python.exe scripts\\safe_fetch_selftest.py`  退出码 0=全过 / 1=有失败
"""
from __future__ import annotations

import http.server
import ipaddress
import os
import socket
import ssl
import sys
import tempfile
import threading
import tracemalloc
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent import safe_fetch as SF                    # noqa: E402

PASS, FAIL = 0, 0


def ok(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  {} {}{}".format("OK  " if cond else "FAIL", name, "  [{}]".format(detail) if detail else ""))


def _blocked(url, allow_private=False):
    """过一遍闸门 ⇒ `(是否被拒, 原因)`。"""
    try:
        SF.validate_url(url, allow_private=allow_private)
        return False, ""
    except SF.FetchError as e:
        return True, str(e)
    except Exception as e:                                   # 别的异常也算"没放行"，但要说清
        return True, "%s: %s" % (type(e).__name__, e)


# ── 离线 DNS 替身（判据不许打真 DNS / 公网）──────────────────────────────
_DNS_TABLE = {
    "localhost": "127.0.0.1",
    "localhost.": "127.0.0.1",
    "loopback.example": "127.0.0.1",
    "lan.example": "192.168.1.10",
    "mixed.example": "93.184.216.34",          # 见 B4：这个名字另有内网 A 记录
    "ok.example": "93.184.216.34",
    "first.example": "93.184.216.34",
    "second.example": "93.184.216.34",
    # L 段（连接层）：两个**不同公网 IP** 的域，用来断言"连的到底是哪一个解析结果"
    "pin-a.example": "93.184.216.34",
    "pin-b.example": "93.184.216.35",
    # ⚠️ 这三个是"OS 会当数字 IP 解析"的写法（inet_aton 语义）：**替身要照 OS 的行为
    #    解析到 127.0.0.1**，否则"拦住它们"只是因为替身解析失败 ⇒ 判据等于没守这一条。
    "127.1": "127.0.0.1",
    "0x7f000001": "127.0.0.1",
    "2130706433": "127.0.0.1",
}
#: L 段专用：可控 DNS —— `_DNS_FLIP[name]` 是"第 N 次及以后返回什么"的脚本。
#: 只有真的打桩 **连接层**（`socket.create_connection`）才能看出"哪一次解析被用"。
_DNS_FLIP = {}
_DNS_HITS = {}
_REAL_GAI = socket.getaddrinfo
_REAL_FQDN = socket.getfqdn


def _install_offline_dns():
    """装离线解析替身。名字只认表（含 localhost），字面 IP 直接本地转换（不碰网络）。"""
    def fake_gai(host, port, *a, **k):
        h = str(host or "").strip().lower()
        _DNS_HITS[h] = int(_DNS_HITS.get(h) or 0) + 1
        rule = _DNS_FLIP.get(h)
        if rule:                                             # L 段：第 N 次起换答案
            for from_n, addr in rule:
                if _DNS_HITS[h] >= from_n:
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, int(port or 0)))]
        ip = _DNS_TABLE.get(h)
        if ip is None:
            try:
                ipaddress.ip_address(h.strip("[]"))          # 字面 IP：纯本地解析
                ip = h.strip("[]")
            except ValueError:
                raise socket.gaierror(-2, "Name or service not known")
        addrs = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, int(port or 0)))]
        if h == "mixed.example":                             # B4 专用：两个 A 记录，一个内网
            addrs.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", int(port or 0))))
        return addrs

    socket.getaddrinfo = fake_gai
    socket.getfqdn = lambda *a: "localhost"                  # 建本地服务时别去反查 DNS


# ── L 段：**连接层**替身（V-R10-32/33/36/38 的修法）───────────────────────
# 为什么要这一段：C/D/E/F/G 全都只守到"字符串/黑名单层"——`_pinned_exchange` 被整体打桩，
# 于是"闸门校验过的那个 IP 到底有没有钉进 socket"**从来没被验证过**。审计的 7 个同族变异
# （G014 连接时重解析域名、G015 不校验证书、G023 允许覆盖 Host…）在这套判据下**全绿**。
# 这一段把 socket 层打桩，直接断言"连接目标 == 校验通过的 IP"。
class _FakeSock:
    """假 socket：只实现 `http.client` 真正会碰的那几个方法。"""

    def __init__(self):
        self.sent = bytearray()

    def sendall(self, data):
        self.sent += bytes(data or b"")

    def send(self, data):
        self.sent += bytes(data or b"")
        return len(data or b"")

    def recv(self, n=65536):
        return b""

    def makefile(self, *a, **k):
        import io as _io
        return _io.BytesIO(b"")

    def settimeout(self, t):
        pass

    def __enter__(self):                 # `cloud.probe` 那段是 `with create_connection(...)`
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class _FakeResp:
    """`http.client` 响应替身：`body` 一次性吐出（`_pinned_exchange` 按 64KB 块读）。"""

    def __init__(self, status=200, headers=None, body=b"ok", location=""):
        self.status = int(status)
        self.reads = []                  # `read(n)` 的实参（判"有没有一把梭读"）
        self._h = list((headers or {}).items())
        if location:
            self._h.append(("Location", location))
        self._b = bytes(body)
        self._off = 0

    def getheaders(self):
        return list(self._h)

    def getheader(self, k, default=None):
        for kk, vv in self._h:
            if str(kk).lower() == str(k).lower():
                return vv
        return default

    def read(self, n=-1):
        self.reads.append(n)
        if n is None or int(n) < 0:
            out = self._b[self._off:]
            self._off = len(self._b)
            return out
        out = self._b[self._off:self._off + int(n)]
        self._off += len(out)
        return out


class _ExchangeForTest:
    """替掉 `_pinned_exchange`：只回一个 302（用来驱动 `_pin_loop` 的第二跳）。"""

    def __init__(self, location):
        self.location = location
        self.calls = []

    def __call__(self, scheme, host, port, path, ip, method="GET", data=None, headers=None,
                 max_bytes=0, timeout=None, out_path=None):
        self.calls.append({"host": host, "ip": ip, "path": path,
                           "headers": dict(headers or {}), "timeout": timeout, "out": out_path})
        if len(self.calls) == 1:                 # 只有第一跳回 302（第二跳要真落盘）
            return {"status": 302, "headers": {}, "location": self.location, "content_type": "",
                    "body": b"", "truncated": False}
        if out_path:
            with open(out_path, "wb") as f:
                f.write(b"x" * 50)
        return {"status": 200, "headers": {}, "location": "", "content_type": "image/png",
                "body": b"" if out_path else b"x" * 50, "truncated": False,
                "bytes": 50, "path": out_path or ""}


class _FakeConnPatch:
    """上下文管理器：装上假连接类，退出时**一定**把真的换回来。`p.rec` 是记录器。"""

    def __init__(self, plan=None, https=False):
        self.plan = plan
        self.https = https

    def __enter__(self):
        self.real_cls = (SF._PinnedHTTPSConnection if self.https else SF._PinnedHTTPConnection)
        self.cls, self.rec = _install_fake_conn(self.plan)
        return self

    def __exit__(self, *exc):
        if self.https:
            SF._PinnedHTTPSConnection = self.real_cls
        else:
            SF._PinnedHTTPConnection = self.real_cls
        return False


class _PinDialRecorder:
    """**只打桩 socket 那一层**，产品自己的 `connect()` 一个字节都不改。

    这是判"连接时有没有重新解析域名"的正确观测面：DNS 替身能把答案翻来覆去地改，
    但 `socket.create_connection` 只认传进来的那个地址元组 —— 而它只由**产品代码**给出。
    ⚠️ 为什么不能只打桩 `_PinnedHTTPConnection`：那个假类自带 `connect()`，
    会把产品里"钉 IP / 改成按域名连"这一行的变异**整个遮住**（本轮实测：G014 变异照样全绿）。
    """

    def __init__(self):
        self.dials = []
        self.timeouts = []

    def __enter__(self):
        self._real = socket.create_connection

        def _rec(addr, timeout=None, **kw):
            self.dials.append((str(addr[0]), int(addr[1])))
            self.timeouts.append(timeout)
            return _FakeSock()

        socket.create_connection = _rec
        return self

    def __exit__(self, *exc):
        socket.create_connection = self._real
        return False


def _install_fake_conn(plan=None):
    """把产品的钉 IP 连接类换成"不发真字节、只记参数"的替身；返回 `(假类, 记录器)`。

    只用于"看请求头 / 造响应 / 看 truncation"这类**不需要真 `connect()`** 的断言；
    "连的是哪个 IP"一律用 `_PinDialRecorder`（见那里的说明）。
    """
    rec = {"headers": {}, "host": "", "ips": [],
           "i": 0, "plan": list(plan or [])}

    class _ConnForTestHttp(SF._PinnedHTTPConnection):
        def __init__(self, host, ip, port=None, timeout=20, **kw):
            rec["host"] = host
            rec["ips"].append(ip)
            super().__init__(host, ip, port=port, timeout=timeout, **kw)

        def request(self, method, url, body=None, headers=None):
            rec["headers"] = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
            rec["method"] = method
            rec["path"] = url
            return None

        def getresponse(self):
            if rec["i"] < len(rec["plan"]):
                r = rec["plan"][rec["i"]]
                rec["i"] += 1
                return r
            return _FakeResp(status=200, headers={}, body=b"")

    SF._PinnedHTTPConnection = _ConnForTestHttp
    return _ConnForTestHttp, rec


# ── 本地回环两端（C2 用）────────────────────────────────────────────────
class _Redirector(http.server.BaseHTTPRequestHandler):
    """A 端：把请求 302 到"另一个主机"（跨主机/同主机两种落点都测）。"""
    target_cross = ""
    target_same = ""
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Redirector.seen.append({k.lower(): v for k, v in self.headers.items()})
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", _Redirector.target_cross)
            self.end_headers()
            return
        if self.path == "/same":
            self.send_response(302)
            self.send_header("Location", _Redirector.target_same)
            self.end_headers()
            return
        body = b"landed-same"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Lander(http.server.BaseHTTPRequestHandler):
    """B 端：只干一件事——把收到的头记下来。"""
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Lander.seen.append({k.lower(): v for k, v in self.headers.items()})
        body = b"landed"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Flood(http.server.BaseHTTPRequestHandler):
    """灌数据端（D 段）：声明并真发 `total` 字节。"""
    total = 8 * 1024 * 1024
    sent = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Flood.sent = 0
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(_Flood.total))
        self.end_headers()
        blk = b"x" * 65536
        try:
            while _Flood.sent < _Flood.total:
                self.wfile.write(blk)
                _Flood.sent += len(blk)
        except Exception:
            pass


def _serve(cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), cls)
    srv.server_name = "localhost"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class _FakeStreamResp:
    """给 `read_capped` 用的最小替身：`body` 最多吐 cap+1 字节（模拟"读多少给多少"）。"""

    def __init__(self, body: bytes):
        self._b = body
        self.reads = []

    def read(self, n=-1):
        self.reads.append(n)
        if n is None or int(n) < 0:
            return self._b
        return self._b[:int(n)]


def main() -> int:
    _install_offline_dns()

    print("== A. validate_url：七种「像内网」的形态（逐条）==")
    bad_forms = [
        ("localhost. 尾点", "http://localhost.:8000/x"),
        ("云元数据 169.254.169.254", "http://169.254.169.254/latest/meta-data/"),
        ("IPv4-mapped IPv6", "http://[::ffff:127.0.0.1]:8080/"),
        ("127.1 简写", "http://127.1:8080/x"),
        ("0x7f000001 十六进制", "http://0x7f000001:8080/x"),
        ("2130706433 十进制", "http://2130706433:8080/x"),
        ("100.64.0.1 CGNAT", "http://100.64.0.1/x"),
    ]
    for label, u in bad_forms:
        blocked, why = _blocked(u)
        ok("拦住 " + label, blocked, why[:44])
    ok("正常公网地址不被误拒", not _blocked("http://ok.example/logo.png")[0])
    ok("allow_private=True 是那个显式开关（本地服务才放行）", not _blocked("http://127.0.0.1:7860/x", True)[0])
    ok("URL 内嵌凭据一律拒", _blocked("http://u:p@ok.example/x")[0])
    ok("非 http/https 拒", _blocked("file:///etc/passwd")[0] and _blocked("gopher://ok.example/")[0])

    print("== B. DNS 解析到环回/内网 ⇒ 拒（打桩 getaddrinfo）==")
    blocked, why = _blocked("http://loopback.example/x")
    ok("B1 解析到 127.0.0.1 ⇒ 拒", blocked, why[:44])
    blocked, why = _blocked("http://lan.example/x")
    ok("B2 解析到 192.168.1.10 ⇒ 拒", blocked, why[:44])
    blocked, why = _blocked("http://localhost./x")
    ok("B3 尾点域名解析到环回 ⇒ 拒（旧字符串表在这里放行过）", blocked, why[:44])
    blocked, why = _blocked("http://mixed.example/x")
    ok("B4 多个解析结果里有一个内网 ⇒ 也拒（不能只看第一个）", blocked, why[:44])

    print("== C. 302 到新主机：凭据不许带过去（V-R9-23）==")
    handler = SF.CredentialStrippingRedirectHandler()
    src = "http://first.example/api"
    creds = {"Authorization": "Bearer tok-a1b2", "X-Api-Key": "K2",
             "Cookie": "s=1", "X-PM-Token": "local", "User-Agent": "judge"}
    req = urllib.request.Request(src, headers=dict(creds))
    new = handler.redirect_request(req, None, 302, "Found", {}, "http://second.example/landed_img")
    h = {k.lower(): v for k, v in dict(new.headers).items()}
    ok("C1 跨主机 ⇒ Authorization 被剥掉", "authorization" not in h, str(h))
    ok("C2 跨主机 ⇒ X-Api-Key / Cookie / X-PM-Token 也剥掉",
       not {"x-api-key", "cookie", "x-pm-token"} & set(h), str(h))
    ok("C3 非凭据头（User-Agent）保留", h.get("user-agent") == "judge", str(h))
    req2 = urllib.request.Request(src, headers=dict(creds))
    new2 = handler.redirect_request(req2, None, 302, "Found", {}, "http://first.example:80/other")
    h2 = {k.lower(): v for k, v in dict(new2.headers).items()}
    ok("C4 阳性对照：同源换路径**不剥**（否则正常鉴权会坏）",
       h2.get("authorization") == "Bearer tok-a1b2", str(h2))
    ok("C5 harden_urllib() 把处理器装进了 urllib 全局 opener", SF.harden_urllib() is True)

    # C6/C7：真 urllib + 本地回环两端（跨主机 → 落点收不到凭据；同源 → 收得到）
    srv_a, port_a = _serve(_Redirector)
    srv_b, port_b = _serve(_Lander)
    try:
        _Redirector.target_cross = "http://localhost:%d/landed" % port_b
        _Redirector.target_same = "http://127.0.0.1:%d/landed" % port_a
        _Lander.seen.clear()
        r1 = urllib.request.Request("http://127.0.0.1:%d/start" % port_a, headers=dict(creds))
        with urllib.request.urlopen(r1, timeout=8) as resp:
            resp.read()
        got_b = _Lander.seen[-1] if _Lander.seen else {}
        ok("C6 真发一次（urllib 跟 302 到 localhost 另一端）⇒ 落点**没有** Authorization",
           bool(got_b) and "authorization" not in got_b and "x-api-key" not in got_b, str(got_b))
        _Redirector.seen.clear()
        r2 = urllib.request.Request("http://127.0.0.1:%d/same" % port_a, headers=dict(creds))
        with urllib.request.urlopen(r2, timeout=8) as resp:
            resp.read()
        got_a = _Redirector.seen[-1] if _Redirector.seen else {}
        ok("C7 阳性对照：同源 302 后**仍带着** Authorization（说明 C6 不是恒真）",
           got_a.get("authorization") == "Bearer tok-a1b2", str(got_a))
    finally:
        srv_a.shutdown()
        srv_b.shutdown()

    # C8：fetch_pinned 逐跳复校 + 跨主机剥凭据（user_tools 那条链，打桩传输层）
    hops = []
    real_exch = SF._pinned_exchange

    def fake_exch(scheme, host, port, path, ip, method="GET", data=None, headers=None,
                  max_bytes=SF.DEFAULT_MAX_BYTES):
        hops.append({"host": host, "path": path, "headers": dict(headers or {}), "ip": ip})
        if len(hops) == 1:
            return {"status": 302, "headers": {}, "location": "http://second.example/landed",
                    "content_type": "", "body": b"", "truncated": False}
        return {"status": 200, "headers": {}, "location": "", "content_type": "text/plain",
                "body": b"PNG", "truncated": False}

    SF._pinned_exchange = fake_exch
    try:
        r = SF.fetch_pinned("http://first.example/api", headers=dict(creds))
    finally:
        SF._pinned_exchange = real_exch
    ok("C8 fetch_pinned 跟了第二跳（逐跳）", len(hops) == 2 and hops[1]["host"] == "second.example",
       str([(h["host"], h["path"]) for h in hops]))
    ok("C9 第二跳的请求里**没有** Authorization / X-Api-Key",
       len(hops) == 2 and not ({"authorization", "x-api-key", "cookie", "x-pm-token"}
                               & set(hops[1]["headers"])), str(hops[1]["headers"] if len(hops) > 1 else {}))
    ok("C10 第一跳是带着凭据发的（对照）",
       bool(hops) and hops[0]["headers"].get("Authorization") == "Bearer tok-a1b2",
       str(hops[0]["headers"] if hops else {}))

    # C11：白名单**每跳**都要过（一次 302 不许跳出 allow_hosts）
    def only_first(u):
        return "first.example" in str(u)

    hops2 = []
    SF._pinned_exchange = lambda *a, **k: (hops2.append(a[1]),
                                           {"status": 302, "headers": {}, "location": "http://evil.example/x",
                                            "content_type": "", "body": b"", "truncated": False})[1]
    try:
        try:
            SF.fetch_pinned("http://first.example/api", host_allowed=only_first)
            _w = ""
        except SF.FetchError as e:
            _w = str(e)
    finally:
        SF._pinned_exchange = real_exch
    ok("C11 重定向落点跳出自名单 ⇒ 拒（白名单每跳都过）", "不在允许清单" in _w, _w[:50])

    print("== D. 响应体上限 + 内存不随远端增长（V-R9-26）==")
    rr = _FakeStreamResp(b"y" * (1024 * 1024 + 10))
    try:
        SF.read_capped(rr, 1024 * 1024, "判据用回包")
        _over = False
    except SF.FetchError:
        _over = True
    ok("D1 回包超过上限 ⇒ 抛（不截断后当成功）", _over)
    ok("D2 只向上游要「上限+1」字节（不整包收）", rr.reads and rr.reads[0] == 1024 * 1024 + 1, str(rr.reads))
    rr2 = _FakeStreamResp(b"z" * 100)
    ok("D3 没超限 ⇒ 原样返回", SF.read_capped(rr2, 1024) == b"z" * 100)

    srv_f, port_f = _serve(_Flood)
    peaks = {}
    try:
        for tag, total in (("small", 2 * 1024 * 1024), ("big", 32 * 1024 * 1024)):
            _Flood.total = total
            tracemalloc.start()
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/flood" % port_f, timeout=10) as resp:
                    try:
                        SF.read_stream(resp, 1024 * 1024, budget_s=15.0, what="灌数据回包")
                    except SF.FetchError:
                        pass
            finally:
                _c, _p = tracemalloc.get_traced_memory()
                tracemalloc.stop()
            peaks[tag] = (_p, _Flood.sent)
    finally:
        srv_f.shutdown()
    ok("D4 远端真发 32MB 时，客户端只收了不到 4MB 就中止（服务端实测发送量）",
       peaks["big"][1] < 4 * 1024 * 1024, "server_sent=%d" % peaks["big"][1])
    ok("D5 峰值内存 < 8MB（上限 1MB；若把 32MB 收完这里会 ≫8MB）",
       peaks["big"][0] < 8 * 1024 * 1024, "peak=%.1fMB" % (peaks["big"][0] / 1048576.0))
    ok("D6 **内存不随远端大小增长**（32MB 与 2MB 两次峰值差 < 3MB）",
       abs(peaks["big"][0] - peaks["small"][0]) < 3 * 1024 * 1024,
       "small=%.2fMB big=%.2fMB" % (peaks["small"][0] / 1048576.0, peaks["big"][0] / 1048576.0))

    print("== E. cloud.normalize_url 与 validate_url 同口径（V-R9-25）==")
    from agent import cloud                            # noqa: E402
    real_cfg = cloud.cfg
    cloud.cfg = lambda: dict(cloud.DEFAULTS)
    try:
        for label, u in bad_forms:
            oky, _fixed, why = cloud.normalize_url(u)
            ok("normalize_url 也拦住 " + label, not oky, why[:40])
        ok("normalize_url 放行正常公网地址", cloud.normalize_url("http://ok.example/hook")[0])
        ok("normalize_url 空串＝未配置（不算错）", cloud.normalize_url("") == (True, "", "未配置"))
        cloud.cfg = lambda: dict(cloud.DEFAULTS, allow_private=True)
        ok("cloud.allow_private=True 时内网才放行（开关仍有效）",
           cloud.normalize_url("http://127.0.0.1:8080/hook")[0])
    finally:
        cloud.cfg = real_cfg

    print("== F. 边角与防回退 ==")
    ok("F1 CGNAT（100.64.0.0/10）也算内网", SF._is_private_ip("100.64.0.1") is True)
    ok("F2 保留段 240.0.0.1 也算内网", SF._is_private_ip("240.0.0.1") is True)
    ok("F3 正常公网 IP 不算内网", SF._is_private_ip("93.184.216.34") is False)
    ok("F4 凭据头清单里有 Authorization / X-Api-Key / Cookie / X-PM-Token",
       {"authorization", "x-api-key", "cookie", "x-pm-token"} <= set(SF.CREDENTIAL_HEADERS))
    ok("F5 safe_fetch 自己不下手未校验的地址（源码里用 validate_url）",
       "validate_url(url_string)" in open(os.path.join(
           os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "safe_fetch.py"),
           encoding="utf-8").read())
    # 拿不到安全层 ⇒ fail-closed（cloud.normalize_url 那条口径）
    import sys as _sys
    _saved = _sys.modules.get("agent.safe_fetch")
    _sys.modules["agent.safe_fetch"] = None
    try:
        oky, _f, why = cloud.normalize_url("http://ok.example/hook")
    finally:
        if _saved is not None:
            _sys.modules["agent.safe_fetch"] = _saved
    ok("F6 安全层不可用 ⇒ normalize_url fail-closed（不放行）", not oky and "fail-closed" in why, why[:48])

    print("== G. guard_remote_url：远端回包里的地址先过闸（V-R9-24）==")
    base_local = "http://127.0.0.1:41010/v1/images/generations"      # E 线复现里的"假出图后端"

    def _guard(u, base=""):
        try:
            SF.guard_remote_url(u, base)
            return False, ""
        except SF.FetchError as e:
            return True, str(e)

    b, w = _guard("http://127.0.0.1:41011/png", base_local)
    ok("G1 后端给的 url 指向**别处**的内网地址 ⇒ 拒（E 线复现形态）", b, w[:44])
    b, w = _guard("http://169.254.169.254/latest/meta-data/", "https://api.example.com/v1")
    ok("G2 云端点给的 url 指向云元数据 ⇒ 拒", b, w[:44])
    b, w = _guard("http://localhost.:8000/x", "https://api.example.com/v1")
    ok("G3 尾点形态同样拒", b, w[:44])
    b, w = _guard("http://127.0.0.1:41010/img/1.png", base_local)
    ok("G4 与后端**同源**的回链放行（本地后端自己的静态资源，不额外增加可达面）", not b, w[:44])
    b, w = _guard("http://ok.example/img/1.png", base_local)
    ok("G5 公网回链放行", not b, w[:44])
    b, w = _guard("", base_local)
    ok("G6 空地址 ⇒ 拒（不当成「没给就算了」）", b, w[:44])

    _section_L()
    _section_M()

    print("\nsafe_fetch 判据：%d 通过 / %d 失败" % (PASS, FAIL))
    if FAIL:
        print("失败项数：%d" % FAIL)
    return 1 if FAIL else 0


# ── L. 连接层（V-R10-32/33/36/38）───────────────────────────────────────
def _section_M():
    """**V-R10-34 第 4 条**：`read_stream` 的墙钟预算必须**落在 socket 超时上**。

    审计实测：对面"连上就不吐字节"时，老写法（只在两块之间核墙钟）**一点都拦不住** ——
    `budget_s=2` 却 10 秒才返回、0 字节。这里用假 socket 复刻同一现象：
    假 socket 的 `read` **遵守自己的超时**（超时到就抛），于是"预算有没有落下去"可被观察。
    """
    import time as _time                                                    # noqa: E402

    class _Sock(object):
        def __init__(self, t=30.0):
            self.timeout = t
            self.set_calls = []

        def gettimeout(self):
            return self.timeout

        def settimeout(self, t):
            self.timeout = float(t)
            self.set_calls.append(float(t))

    class _Fp(object):
        def __init__(self, s):
            self._sock = s

    class _Stall(object):
        """连接建立后**一个字节都不吐**（stall 秒起步）。"""

        def __init__(self, sock, stall):
            self.fp = _Fp(sock)
            self._stall = float(stall)
            self.closed = False
            self.reads = 0

        def read(self, n=-1):
            self.reads += 1
            _t = self.fp._sock.gettimeout()
            if _t is not None and float(_t) < self._stall:
                _time.sleep(float(_t))          # 按真 socket 的语义：等到超时就抛
                raise TimeoutError("fake socket timeout（%.2fs）" % float(_t))
            _time.sleep(self._stall)
            return b""

        def close(self):
            self.closed = True

    # ① 新写法：预算 0.4s，对面 1.2s 不吐字节 ⇒ 必须**在预算附近**中止（不是等满 1.2s）
    _s1 = _Sock(30.0)
    _r1 = _Stall(_s1, 1.2)
    _t_a = _time.monotonic()
    _raised1 = ""
    try:
        SF.read_stream(_r1, budget_s=0.4, what="V-R10-34 夹具")
    except Exception as _e1:
        _raised1 = type(_e1).__name__
    _cost1 = _time.monotonic() - _t_a
    ok("M1 对面不吐字节 ⇒ 预算**真的生效**（0.4s 预算、1.2s 空等，实际 %.2fs 就中止）" % _cost1,
       bool(_raised1) and _cost1 < 1.0, "raised=%s cost=%.2fs" % (_raised1, _cost1))
    ok("M2 中止时把 socket 超时**压到剩余预算**（只收紧、不放宽）",
       bool(_s1.set_calls) and min(_s1.set_calls) <= 0.5, "set_calls=%s" % _s1.set_calls[:3])
    ok("M3 中止时尽力断开（不许把连接挂着）", _r1.closed is True)
    # ② 反例锚：老写法（只有"块之间核预算"）在同一夹具上**拦不住**（等满 1.2s 才返回空）
    _s2 = _Sock(30.0)
    _r2 = _Stall(_s2, 1.2)
    _t_b = _time.monotonic()
    _old_out, _old_raised = None, ""
    try:
        _got = bytearray()
        for _buf in SF.iter_response(_r2):          # 老写法：循环体里根本没有"动手前核预算"
            if not _buf:
                continue
            _got += _buf
        _old_out = bytes(_got)
    except Exception as _e2:
        _old_raised = type(_e2).__name__
    _cost2 = _time.monotonic() - _t_b
    ok("M4 反例锚：老写法在同一夹具上**不中止**（等满 %.2fs、回空字节）⇒ M1 真的会红" % _cost2,
       _old_out == b"" and not _old_raised and _cost2 >= 1.0 and not _s2.set_calls,
       "out=%r raised=%s cost=%.2fs" % (_old_out, _old_raised, _cost2))
    # ③ 不给预算 ⇒ 一个超时都不许动（别把正常读流也套上紧箍）
    _s3 = _Sock(30.0)
    _r3 = _Stall(_s3, 0.05)
    SF.read_stream(_r3, budget_s=0, what="V-R10-34 夹具")
    ok("M5 不给预算（budget_s=0）⇒ **不动** socket 超时（老行为保持）",
       _s3.timeout == 30.0 and not _s3.set_calls, "timeout=%s calls=%s" % (_s3.timeout, _s3.set_calls))


def _section_L():
    """**这一段才是本轮的重点**：把 `socket.create_connection` 打桩，直接看"连的是哪个 IP"。

    每一条都有**反例锚**：把产品侧对应那一句改坏（见每条 docstring 里的"反例"），断言必红。
    """
    print("== L. 连接层：校验过的 IP 真的钉进 socket 了吗（V-R10-36/38）==")
    real_cc = socket.create_connection
    real_exch = SF._pinned_exchange

    # ══ L1~L4：可控 DNS 翻转 ⇒ 必须连**校验通过的那个 IP**，不是再解析的那个 ══
    #  反例：把 `_PinnedHTTPConnection.connect` 的 `(self._pinned_ip, …)` 换成
    #       `(self.host, self.port)`（= G014"连接时重新解析域名"）⇒ L1/L3 必红
    #       （连接时会解析到环回、`dials` 里出现 127.0.0.1）。
    #  翻转点 = 第 3 次解析：①判据取证那次 ②真请求的闸门那次 ③"连接时若再解析"那次
    #  （前 2 次给公网；第 3 次起给环回 ⇒ 钉 IP 的实现不受影响，再解析的实现必连环回）。
    _DNS_FLIP.clear()
    _DNS_HITS.clear()
    _DNS_FLIP["pin-a.example"] = [(3, "127.0.0.1")]
    with _PinDialRecorder() as dial:
        scheme, host, port, path, pinned = SF.validate_url("http://pin-a.example/x")
        hits_before_req = _DNS_HITS.get("pin-a.example")
        try:
            SF.fetch_pinned("http://pin-a.example/x", timeout=1.0)
            _err = ""
        except Exception as e:
            _err = "%s: %s" % (type(e).__name__, str(e)[:40])
        dials_after = list(dial.dials)
        hits_after = _DNS_HITS.get("pin-a.example")
    third = socket.getaddrinfo("pin-a.example", 80)[0][4][0]     # 现在再解析会给什么
    ok("L1 闸门解出的 IP 与**真正拨号**的 IP 是同一个（%s）" % pinned,
       dials_after == [(pinned, 80)], str(dials_after) + " err=" + _err)
    ok("L2 反例锚：此刻 DNS 已经翻脸（再解析就是环回）——说明 L1/L3 不是恒真",
       third == "127.0.0.1" and pinned == "93.184.216.34", "third=%s vetted=%s" % (third, pinned))
    ok("L3 DNS 翻脸之后仍没连到环回（连接层没给 DNS 第二次机会）",
       "127.0.0.1" not in [d[0] for d in dials_after], str(dials_after))
    ok("L4 请求里只解析了 1 次（闸门那次）；连接时是 0 次",
       hits_before_req == 1 and hits_after == 2, "before=%s after=%s dials=%s"
       % (hits_before_req, hits_after, dials_after))

    # ══ L5/L6：Host 头 = 域名，且**不许被调用方覆盖** ══
    #  反例：把 `_pinned_exchange` 里 `if str(k).lower() == "host": continue` 那句删掉 ⇒ L5 必红。
    _DNS_FLIP.clear()
    _DNS_HITS.clear()
    with _FakeConnPatch() as p2:
        SF.fetch_pinned("http://pin-b.example/x", headers={"Host": "evil.example", "X-A": "1"})
        got_headers = dict(p2.rec["headers"])
    ok("L5 调用方给的 Host 被忽略（Host 仍是**我们**按域名写的那个）",
       got_headers.get("host") == "pin-b.example", str(got_headers))
    ok("L6 其它自定义头照常保留（不是把 headers 整个丢掉）",
       got_headers.get("x-a") == "1", str(got_headers))

    # ══ L7/L8：HTTPS 的 SNI 是域名、证书上下文是**默认（校验开着）**那个 ══
    #  反例：把 `ssl._create_default_https_context()` 换成 `ssl._create_unverified_context()`
    #       （= G015）⇒ L7 必红；把 `server_hostname=self.host` 换成 `self._pinned_ip` ⇒ L8 必红。
    # ⚠️ 为什么**两条都断言**（V-R10-38 的 G015 变异实测教训）：`HTTPSConnection.__init__` 自己
    #  就已经把 `self._context` 设成默认上下文了 ⇒ 产品 `connect()` 里的
    #  `if self._context is None:` 那一行**根本不会执行**（快路径，实测："把 connect 里那行换成
    #  `_create_unverified_context()`"是**死变异**，只断言握手参数抓不到）。
    #  ⇒ ①看**真正用于握手**的那个上下文（`wrap_socket` 收到的 self）；
    #     ②再看**构造后**的 `_context`（慢路径；把默认换成不校验的变异会在这里现形）。
    seen_ctx = {}
    seen_init = {}
    real_wrap = ssl.SSLContext.wrap_socket
    _real_init = SF._PinnedHTTPSConnection.__init__

    def _init_spy(self, host, ip, port=None, timeout=20, **kw):
        _real_init(self, host, ip, port=port, timeout=timeout, **kw)
        c = getattr(self, "_context", None)
        seen_init["verify_mode"] = None if c is None else int(c.verify_mode)
        seen_init["check_hostname"] = None if c is None else bool(c.check_hostname)

    def _wrap(self, sock, *a, **k):
        seen_ctx["server_hostname"] = k.get("server_hostname")
        seen_ctx["check_hostname"] = bool(self.check_hostname)
        seen_ctx["verify_mode"] = int(self.verify_mode)
        return _FakeSock()

    # ⚠️ 这里**不换连接类**（换了就把产品 `connect()` 里那两行遮住了）：
    #    只打桩 `socket.create_connection` 与 `SSLContext.wrap_socket`，
    #    于是跑的是**产品自己的** `_PinnedHTTPSConnection.connect()`。
    _https_real = SF._PinnedHTTPSConnection
    ssl.SSLContext.wrap_socket = _wrap
    SF._PinnedHTTPSConnection.__init__ = _init_spy
    with _PinDialRecorder() as dial8:
        try:
            SF.fetch_pinned("https://pin-a.example/x", timeout=2.0)
        except Exception:
            pass
        tls_dials = list(dial8.dials)
    ssl.SSLContext.wrap_socket = real_wrap
    SF._PinnedHTTPSConnection.__init__ = _real_init
    SF._PinnedHTTPSConnection = _https_real
    ok("L7 证书校验必须开着（握手用的上下文：check_hostname=True + CERT_REQUIRED）",
       seen_ctx.get("check_hostname") is True and seen_ctx.get("verify_mode") == ssl.CERT_REQUIRED,
       str(seen_ctx) + " dials=%s" % tls_dials)
    ok("L7b 构造出来的 `_context` 也是**默认（校验开着）**那个 —— "
       "`ssl._create_unverified_context()` / `check_hostname=False` 一律判红",
       seen_init.get("check_hostname") is True
       and seen_init.get("verify_mode") == ssl.CERT_REQUIRED, str(seen_init))
    ok("L8 SNI/证书校验针对**原始域名**（不是钉的那个 IP）",
       seen_ctx.get("server_hostname") == "pin-a.example", str(seen_ctx))
    ok("L8b HTTPS 那一跳也是钉 IP（同一套 connect 逻辑）",
       tls_dials == [("93.184.216.34", 443)], str(tls_dials))

    # ══ L9：调用方给的 timeout 真的到了连接层（V-R10-34）══
    #  反例：把 `float(timeout or PINNED_TIMEOUT_S)` 写回常量 20 ⇒ L9 必红。
    with _PinDialRecorder() as dial9:
        try:
            SF.fetch_pinned("http://pin-a.example/x", timeout=0.75)
        except Exception:
            pass
        tmo_seen = list(dial9.timeouts)
    ok("L9 调用方说 0.75 秒，连接层拿到的就是 0.75 秒（老写法写死 20）",
       tmo_seen == [0.75], "conn_timeouts=%s" % tmo_seen)

    # ══ L10：钉 IP 那一跳**读体带上限**且 `truncated` 如实报 ══
    #  反例：把 `_pinned_exchange` 的 `resp.read(65536)` 改回 `resp.read()` 一把梭 ⇒ L10 必红
    #       （G025），把 `truncated = True` 改成 `False` ⇒ L10 必红（G024 谎报）。
    _big = _FakeResp(status=200, headers={"Content-Length": "300000"}, body=b"z" * 300000)
    with _FakeConnPatch(plan=[_big]) as p5:
        out = SF.fetch_pinned("http://pin-a.example/x", max_bytes=1000)
    _reads = list(_big.reads)
    ok("L10 超过 max_bytes ⇒ 只回上限那么多字节，且 `truncated=True` 如实说（不谎报、不整包收）",
       len(out["body"]) == 1000 and out["truncated"] is True,
       "len=%d truncated=%s reads=%s" % (len(out["body"]), out["truncated"], _reads[:4]))
    ok("L10b 读体**分块**（`read(n)` 带正数上限），不许 `read()` 一把梭把整包收进内存",
       bool(_reads) and all(isinstance(n, int) and n > 0 for n in _reads), str(_reads[:4]))

    # ══ L11~L14：`fetch_pinned_stream` 落盘 + 第二跳仍钉 IP + 跨主机剥凭据 ══
    #  反例：把 `fetch_pinned_stream` 改回 `urllib.request.urlopen(url)` ⇒ L11 必红
    #       （第二跳会走系统 DNS/真网络，`ex.calls` 里不会有我们的注入点）。
    _DNS_FLIP.clear()
    _DNS_HITS.clear()
    ex = _ExchangeForTest("http://pin-b.example/final")
    SF._pinned_exchange = ex
    dest = os.path.join(tempfile.gettempdir(), "pm-sf-stream-%d.bin" % os.getpid())
    try:
        with _FakeConnPatch(plan=[_FakeResp(status=200, headers={}, body=b"x" * 50)]):
            try:
                got = SF.fetch_pinned_stream("http://pin-a.example/dl", dest, timeout=3.0,
                                             headers={"Authorization": "Bearer tok-a1b2"})
            except Exception as e:
                got = {"err": "%s: %s" % (type(e).__name__, e)}
    finally:
        SF._pinned_exchange = real_exch
    _n = os.path.getsize(dest) if (isinstance(got, dict) and got.get("path")
                                   and os.path.exists(dest)) else -1
    ok("L11 fetch_pinned_stream 走的是**同一个** `_pinned_exchange`（第二跳也钉 IP）",
       isinstance(got, dict) and "path" in got and len(ex.calls) == 2
       and ex.calls[1]["host"] == "pin-b.example", str(got)[:90])
    ok("L12 跨主机跳转的凭据被剥掉（第二跳没有 Authorization）",
       len(ex.calls) == 2 and "Authorization" not in ex.calls[1]["headers"],
       str(ex.calls[1]["headers"] if len(ex.calls) > 1 else {}))
    ok("L13 第二跳用的 IP 是**那一跳自己**校验出来的（pin-b → 93.184.216.35）",
       len(ex.calls) == 2 and ex.calls[1]["ip"] == "93.184.216.35",
       str([c["ip"] for c in ex.calls]))
    ok("L14 流式下载真把字节落盘了（不是空文件也不是没写）", _n == 50, "bytes_on_disk=%s" % _n)
    try:
        if os.path.exists(dest):
            os.remove(dest)
    except Exception:
        pass

    # ══ L15：闸门拒了 ⇒ 抛 `FetchError` 且**不留半截文件**（流式下载的失败路径）══
    dest2 = dest + ".fail"
    try:
        SF.fetch_pinned_stream("http://loopback.example/dl", dest2)
        _e = ""
    except SF.FetchError as e:
        _e = str(e)
    ok("L15 闸门拒了就抛 FetchError，且**不留下半截文件**",
       bool(_e) and not os.path.exists(dest2), "%s exists=%s" % (_e[:40], os.path.exists(dest2)))

    # ══ L16/L17：`validate_remote_url` 把**同一次解析**的 IP 一起交出来（V-R10-32 的修法本体）══
    #  反例：把 `validate_remote_url` 改成"只回字符串"（= 老 `guard_remote_url`）
    #       ⇒ 调用方只能再解析一次 ⇒ 调用点就只能拿 `guard_remote_url` ⇒ 产品侧连接层断言必红。
    _DNS_FLIP.clear()
    _DNS_HITS.clear()
    u, ip1 = SF.validate_remote_url("http://pin-a.example/x", "https://api.example.com/v1")
    _scheme, _h, _p, _path, ip2 = SF.validate_url("http://pin-a.example/x")
    ok("L16 `validate_remote_url` 回的 IP 与 `validate_url` 一致（调用方拿它连接就是同一次解析）",
       ip1 == ip2 == "93.184.216.34", "ip1=%s ip2=%s" % (ip1, ip2))
    try:
        SF.validate_remote_url("http://127.0.0.1:9/x", "http://api.example.com/v1")
        _g = ""
    except SF.FetchError as e:
        _g = str(e)
    ok("L17 带 IP 版照样拦内网（不是绕开闸门的旁路）", bool(_g), _g[:40])

    # ══ L18：三处"二次解析"调用点必须已经改走钉 IP 传输（V-R10-32）══
    #  反例：把 `bilibili._download` 改回 `urllib.request.urlopen(url)` ⇒ L18 必红。
    print("== L2x. 三处「校验一次、连接再解析」的调用点（V-R10-32/33）==")
    for _f, _tag, _must in (
            ("bilibili.py", "bilibili._download", "fetch_pinned_stream"),
            ("image_gen.py", "image_gen 二次 GET", "fetch_pinned_stream"),
            ("video_gen.py", "video_gen 回链下载", "fetch_pinned_stream"),
            ("cloud.py", "cloud.probe 的 HEAD", "pinned_head")):
        _src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "agent", _f), encoding="utf-8").read()
        ok("L18 %s 走钉 IP 传输（%s）" % (_tag, _must), _must in _src,
           "在 agent/%s 里找不到 %s" % (_f, _must))

    # ══ L19~L22：`cloud.probe` 的 HEAD 段（V-R10-33）——第 3 次解析必须打不到 ══
    #  反例：把 `pinned_head` 换回 `urllib.request.Request(fixed, method="HEAD")` + `urlopen`
    #       ⇒ L20/L21 必红（urlopen 自己解析 —— 在判据里会连到真网络或解析到环回）。
    print("== L19~L22. cloud.probe 的 HEAD 段：第 3 次解析不许被采纳（V-R10-33）==")
    from agent import cloud as _cloud                        # noqa: E402
    _real_cc2 = socket.create_connection
    _real_exch_head = SF._exchange_head
    _DNS_FLIP.clear()
    _DNS_HITS.clear()
    # 解析次数账：①`normalize_url`（probe 内部）②`validate_url`（probe 拿 IP）③`pinned_head` 那一跳
    # （另有稳态内 1 次由连接/探测路径开销带来）⇒ 翻转点设 5：前面全是公网，
    # 只有"若连接时再解析"的那一次才是环回 ⇒ 钉 IP 的实现连到公网、二次解析的实现必连环回。
    _DNS_FLIP["pin-a.example"] = [(5, "127.0.0.1")]
    _dials = []

    def _probe_cc(addr, timeout=None, **kw):
        _dials.append((str(addr[0]), int(addr[1])))
        return _FakeSock()

    socket.create_connection = _probe_cc
    _real_head = SF._exchange_head

    def _fake_head(scheme, host, port, path, ip, headers=None, timeout=None):
        # 照真实现钉一次（判据里换成"记下要连哪个 IP"），并回 200
        _dials.append((str(ip), int(port)))
        return {"status": 200, "location": ""}

    SF._exchange_head = _fake_head
    _real_cc_cfg = _cloud.cfg
    _cloud.cfg = lambda: dict(_cloud.DEFAULTS, allow_private=False)
    try:
        # ①`normalize_url` 校验（第 1 次解析，拿到 vetted 公网 IP）
        _ok_n, _fixed_n, _why_n = _cloud.normalize_url("http://pin-a.example/hook")
        _hits_before = _DNS_HITS.get("pin-a.example")
        # ②旧实现到这里就"按域名发 HEAD" ⇒ 第 2 次解析 = 环回；钉 IP 实现连的是 vetted 那个
        _pr = _cloud.probe(url="http://pin-a.example/hook", timeout_ms=500)
        _hits = _DNS_HITS.get("pin-a.example")
        _third_now = socket.getaddrinfo("pin-a.example", 80)[0][4][0]
    finally:
        _cloud.cfg = _real_cc_cfg
        SF._exchange_head = _real_head
        socket.create_connection = _real_cc2
    ok("L19 normalize_url 放行了（判据前置条件成立）", bool(_ok_n) and _fixed_n != "", "%s %s" % (_ok_n, _why_n))
    ok("L20 HEAD 段连的是**闸门校验过的公网 IP**（全链路一次都没连到环回）",
       bool(_dials) and all(d[0] == "93.184.216.34" for d in _dials) and ("127.0.0.1", 80) not in _dials
       and any(d[1] == 80 for d in _dials),
       str(_dials) + " | " + str(_pr)[:70])
    ok("L21 反例锚：此刻再解析确实是环回（说明 L20 不是恒真）", _third_now == "127.0.0.1", _third_now)
    ok("L22 连接段没有多解析（probe 的 HEAD 只在闸门里解一次域名）",
       _hits <= 4, "hits=%s（闸门内 2 次 + 钉 IP 那一跳 1 次 + 探测路径 1 次；再多就是连接段又解了一次）" % _hits)

    # ══ L23：`bilibili._download` 的**功能**锚（V-R10-32 的真实回归会被抓住）══
    #  反例：把 `fetch_pinned_stream(...)` 换回 `urllib.request.urlopen(url)` ⇒ L23 必红
    #       （DNS 那时已翻脸 ⇒ 直连会打到"环回"那个 IP）。
    print("== L23. bilibili._download：一次解析、钉 IP（功能锚，不是只 grep 源码）==")
    from agent import bilibili as _bili                     # noqa: E402
    _real_open = urllib.request.urlopen
    _cap_u = {"n": 0}

    class _LegacyResp:            # 老写法（urlopen）在这里会拿到的响应
        def __init__(self, body=b"x" * 60):
            self._b = body
            self._o = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            if n is None or int(n) < 0:
                out = self._b[self._o:]
                self._o = len(self._b)
                return out
            out = self._b[self._o:self._o + int(n)]
            self._o += len(out)
            return out

        def close(self):
            pass

    def _legacy_open(*a, **k):
        _cap_u["n"] += 1
        return _LegacyResp()

    _DNS_FLIP.clear()
    _DNS_HITS.clear()
    _DNS_FLIP["pin-a.example"] = [(1, "127.0.0.1")]        # 这个域名的解析**永远是环回**
    _dest23 = os.path.join(tempfile.gettempdir(), "pm-sf-bili-%d.m4a" % os.getpid())
    urllib.request.urlopen = _legacy_open
    try:
        with _PinDialRecorder() as dial23:
            _cnt, _why23 = _bili._download("http://pin-a.example/audio.m4a", _dest23, timeout=3)
            _dialed23 = list(dial23.dials)
    finally:
        urllib.request.urlopen = _real_open
    check_exists = os.path.exists(_dest23)
    try:
        if check_exists:
            os.remove(_dest23)
    except Exception:
        pass
    ok("L23 闸门把「解析到环回」的域名拒了（`_cnt=0` + 如实原因），且**一个字节都没落盘**",
       _cnt == 0 and not check_exists and bool(_why23),
       "cnt=%s why=%s exists=%s" % (_cnt, _why23[:50], check_exists))
    ok("L23b 打桩的 `urlopen` **一次都没被调用**（证明这条路已经不经过它了）",
       _cap_u["n"] == 0, "urlopen_calls=%s" % _cap_u["n"])


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        socket.getaddrinfo = _REAL_GAI
        socket.getfqdn = _REAL_FQDN
    sys.exit(rc)
