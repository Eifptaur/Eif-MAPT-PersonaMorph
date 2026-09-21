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

跑法：py -3 scripts\\safe_fetch_selftest.py      退出码 0=全过 / 1=有失败
"""
from __future__ import annotations

import http.server
import ipaddress
import os
import socket
import sys
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
    # ⚠️ 这三个是"OS 会当数字 IP 解析"的写法（inet_aton 语义）：**替身要照 OS 的行为
    #    解析到 127.0.0.1**，否则"拦住它们"只是因为替身解析失败 ⇒ 判据等于没守这一条。
    "127.1": "127.0.0.1",
    "0x7f000001": "127.0.0.1",
    "2130706433": "127.0.0.1",
}
_REAL_GAI = socket.getaddrinfo
_REAL_FQDN = socket.getfqdn


def _install_offline_dns():
    """装离线解析替身。名字只认表（含 localhost），字面 IP 直接本地转换（不碰网络）。"""
    def fake_gai(host, port, *a, **k):
        h = str(host or "").strip().lower()
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


class _FakeResp:
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
    rr = _FakeResp(b"y" * (1024 * 1024 + 10))
    try:
        SF.read_capped(rr, 1024 * 1024, "判据用回包")
        _over = False
    except SF.FetchError:
        _over = True
    ok("D1 回包超过上限 ⇒ 抛（不截断后当成功）", _over)
    ok("D2 只向上游要「上限+1」字节（不整包收）", rr.reads and rr.reads[0] == 1024 * 1024 + 1, str(rr.reads))
    rr2 = _FakeResp(b"z" * 100)
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

    print("\nsafe_fetch 判据：%d 通过 / %d 失败" % (PASS, FAIL))
    if FAIL:
        print("失败项数：%d" % FAIL)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        socket.getaddrinfo = _REAL_GAI
        socket.getfqdn = _REAL_FQDN
    sys.exit(rc)
