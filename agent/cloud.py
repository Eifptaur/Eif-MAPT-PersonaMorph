# -*- coding: utf-8 -*-
"""上云接口（第 10 条排期里"上云三件"的**预留接口层**）。

用户口径（2026-09-13/14）：**上云三件先不做**，但①**留"可以输网址"的接口** ②**UI 里做完整的交互设计**
（未配置 / 已配置 / 测不通 三态 + 中文文案）③顺带把"能不能真传到那个网址、怎么传、被墙 / 需要对方有接收端怎么办"讲清楚。

本模块只做三件事，**一条数据都不上传**：
1. `normalize_url()`：URL 校验（只允许 http/https；环回/内网默认拒，除非显式 `allow_private`）；
2. `probe()`：**连通性探测**（DNS → TCP → TLS → HEAD/GET），用来回答"这个网址到不到得了"。
   探测**不带凭据、不带任何用户数据**（不发 Authorization、不发 body）；
3. `upload()`：真正要发数据的那一步——**默认关**，没显式开启时直接拒绝并说明（留接口、不启用）。

被墙 / 需要对方接收端 / 需要鉴权这三种情况，`probe()` 的返回会把**卡在哪一段**说清楚（dns/tcp/tls/http），
配合 `docs/上云接口契约.md` 里给对方的接口要求，就能判断是"我们发不出去"还是"对方没接"。
"""
from __future__ import annotations

import json
import logging
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULTS = {
    "enabled": False,          # ⛔ 总开关：默认关＝永不真上传（既有口径："先不做"）
    "persona_url": "",         # 人设上云的接收端（留空＝未配置）
    "blocklist_url": "",       # 屏蔽名单上云的接收端（留空＝未配置）
    "token": "",               # 接收端要求时的 Bearer Token（打码回显、只存本机）
    "timeout_ms": 8000,
    "allow_private": False,    # 允许环回/内网地址（默认拒：防止误把私人接口当公网接收端）
}

KINDS = [("persona", "人设", "persona_url"), ("blocklist", "屏蔽名单", "blocklist_url")]


def cfg() -> dict:
    try:
        from .config import get_config
        c = dict(DEFAULTS)
        c.update(((get_config() or {}).get("cloud") or {}))
        return c
    except Exception:
        return dict(DEFAULTS)


def _harden_redirects() -> bool:
    """V-R9-23：确保 urllib 跟 302 时**不把 `Bearer cloud.token` 带到新主机**。

    实现只有一处（`safe_fetch.CredentialStrippingRedirectHandler`）——这里只负责"装上去"。
    装不上就如实记日志（不静默）：安全层不可用时至少留下痕迹。
    """
    try:
        from .safe_fetch import harden_urllib
        return bool(harden_urllib())
    except Exception as e:                                   # pragma: no cover - 极端环境
        logging.getLogger("persona-morph").warning(
            "安全层不可用，重定向凭据剥离没装上（V-R9-23）：%s", e)
        return False


def normalize_url(raw: str) -> tuple:
    """URL 校验 → (ok, 规范化后的 url, 原因)。空串＝未配置（不算错）。

    ⛔ 2026-09-21（第九轮审计 **V-R9-25**）：这里原来是一张**字符串表**（`localhost` / `127.0.0.1`
    / `::1` / `0.0.0.0` / `.local` / 三个私有段）。E 线实测 `localhost.`（尾点）、
    `169.254.169.254`、`[::ffff:127.0.0.1]`、`127.1`、`0x7f000001`、`2130706433`、`100.64.0.1`
    **七种形态全部放行**，而且 `probe()` 会**真连过去**（`stage=tcp/http` 就是回包）
    ⇒ 拿到控制台口令的人可以把它当**内网端口扫描器**用。
    现在整段改调 `safe_fetch.validate_url`（它**解析后核 IP**，同一份实测全拦），
    `cloud.allow_private` 仍是那个显式开关（`validate_url` 正好有这个形参）。
    ⚠️ 代价：这里现在会做一次 DNS 解析（原来只比字符串）——这是"fail-closed"必须付的。
    """
    u = str(raw or "").strip()
    if not u:
        return True, "", "未配置"
    if not re.match(r"^https?://", u, re.I):
        return False, "", "必须以 http:// 或 https:// 开头"
    try:
        p = urllib.parse.urlsplit(u)
    except Exception as e:
        return False, "", "URL 解析失败：%s" % str(e)[:60]
    if not p.netloc:
        return False, "", "缺少主机名"
    c = cfg()
    try:
        from .safe_fetch import validate_url, FetchError as _FetchError
    except Exception as e:
        return False, "", "安全层不可用，拒绝校验（fail-closed）：%s" % str(e)[:60]
    try:
        validate_url(u, allow_private=bool(c.get("allow_private")))
    except _FetchError as e:
        why = str(e)[:60]
        if ("内网" in why) or ("本机" in why):
            return False, "", ("看起来是环回/内网地址（%s）；确实要发到内网请显式打开 "
                              "cloud.allow_private" % why)
        return False, "", "网址不可用：%s" % why
    except Exception as e:
        return False, "", "网址校验异常：%s" % str(e)[:60]
    return True, u.rstrip("/"), ""


def _split_host(url: str):
    p = urllib.parse.urlsplit(url)
    return p.hostname or "", (p.port or (443 if p.scheme == "https" else 80)), p.scheme


def probe(which: str = "", url: str = "", timeout_ms: int = 0) -> dict:
    """连通性探测：DNS → TCP → TLS → HTTP(HEAD)。**不带凭据、不带用户数据。**

    返回 {ok, stage, status, ms, url, why}；stage 告诉你卡在哪一段（用于判断是否"被墙"）。
    """
    c = cfg()
    if not url:
        key = dict((k, f) for k, _l, f in KINDS).get(which)
        if not key:
            return {"ok": False, "stage": "config", "why": "不知道要测哪一项（which 只能是 persona / blocklist）",
                    "status": 0, "ms": 0, "url": ""}
        url = str(c.get(key) or "")
    ok, fixed, why = normalize_url(url)
    if not ok:
        # `normalize_url` 现在也做 DNS 校验（V-R9-25）⇒ 解析失败这一种仍要如实报 `stage=dns`
        # （探测器的意义就是"告诉用户卡在哪一段"："URL 解析失败"不算 DNS，"域名解析失败"才算）
        _stage = "dns" if "域名解析" in str(why) else "config"
        return {"ok": False, "stage": _stage, "why": why, "status": 0, "ms": 0, "url": url}
    if not fixed:
        return {"ok": False, "stage": "config", "why": "未配置接收端网址", "status": 0, "ms": 0, "url": ""}
    t0 = time.time()
    ms = lambda: int((time.time() - t0) * 1000)  # noqa: E731
    host, port, scheme = _split_host(fixed)
    # V-R9-25（TOCTOU）：**校验时解析到哪个 IP，就用哪个 IP 连** —— 原来这里是
    # `getaddrinfo()` 看一眼、`create_connection((host, port))` 再解析一次，两次结果可以不同
    # （DNS rebinding 实测能让第二次解析落到环回）。
    try:
        from .safe_fetch import validate_url as _vurl
        _scheme, _host, _port, _path, ip = _vurl(
            fixed, allow_private=bool(c.get("allow_private")))
    except Exception as e:
        return {"ok": False, "stage": "dns", "why": "域名解析不了（%s）——常见于网址写错、或本机 DNS/网络被限制" % str(e)[:60],
                "status": 0, "ms": ms(), "url": fixed}
    try:
        with socket.create_connection((ip, port), timeout=max(2.0, (timeout_ms or c["timeout_ms"]) / 1000.0)):
            pass
    except Exception as e:
        return {"ok": False, "stage": "tcp", "why": "连不上端口 %d（%s）——可能被墙/防火墙挡、或对方没起服务"
                % (port, str(e)[:60]), "status": 0, "ms": ms(), "url": fixed}
    if scheme == "https":
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((ip, port), timeout=max(2.0, (timeout_ms or c["timeout_ms"]) / 1000.0)) as s:
                with ctx.wrap_socket(s, server_hostname=host):
                    pass
        except Exception as e:
            return {"ok": False, "stage": "tls", "why": "TLS 握手失败（%s）——证书/中间人/需要信任链" % str(e)[:60],
                    "status": 0, "ms": ms(), "url": fixed}
    # HTTP 段：只发 HEAD，不带 Authorization、不带 body
    # ⛔ V-R10-33（第十轮）：老写法是"上面 TCP/TLS 两段钉了 IP，这一段却**按域名**发" ⇒
    #    `urllib` 自己**第 3 次解析**域名，审计实测第 3 次给环回 ⇒ HEAD 真打到 `127.0.0.1`，
    #    而面板报 `ok=true / stage=http / 200`（把内网当成了"可达"）。
    #    ⇒ 改用 `safe_fetch.pinned_head()`：**校验用的 IP 与连接用的 IP 是同一次解析**。
    try:
        from .safe_fetch import pinned_head as _phead
    except Exception as e:
        return {"ok": False, "stage": "http", "why": "安全抓取层不可用（fail-closed）：%s" % str(e)[:60],
                "status": 0, "ms": ms(), "url": fixed}
    try:
        _hr = _phead(fixed, headers={"User-Agent": "PersonaMorph/probe"},
                     timeout=max(2.0, (timeout_ms or c["timeout_ms"]) / 1000.0))
        _code = int(_hr.get("status") or 0)
        if _code >= 400:
            # 4xx/5xx 也算"能连上"：说明地址通、只是不接受 HEAD 或路径不对
            # （多数接收端只收 POST，看到 405/404 属正常）
            return {"ok": True, "stage": "http", "status": _code, "ms": ms(),
                    "url": _hr.get("url") or fixed,
                    "why": "地址可达，但服务返回 HTTP %d（多数接收端只收 POST，看到 405/404 属正常）" % _code}
        return {"ok": True, "stage": "http", "status": _code,
                "ms": ms(), "url": _hr.get("url") or fixed, "why": "可达（HEAD 成功）"}
    except Exception as e:
        return {"ok": False, "stage": "http", "why": "连上了但这个地址没给出 HTTP 应答（%s）" % str(e)[:60],
                "status": 0, "ms": ms(), "url": fixed}


def endpoints() -> list:
    c = cfg()
    out = []
    for k, label, field in KINDS:
        raw = str(c.get(field) or "")
        ok, fixed, why = normalize_url(raw)
        out.append({"id": k, "label": label, "url": raw, "normalized": fixed,
                    "configured": bool(fixed), "valid": bool(ok), "why": why,
                    "token_set": bool(str(c.get("token") or "").strip())})
    return out


def upload(which: str, payload: dict, dry: bool = True) -> dict:
    """真上传这一步——**默认永远拒绝**（除非 cloud.enabled 显式打开）。

    留接口的意义：接收端确定后，只要打开开关 + 填 URL，就能直接接上；
    现在调用它只会拿到一句明确的拒绝，**不会发出任何请求**（判据里有"打桩计数必须为 0"）。
    """
    c = cfg()
    if not c.get("enabled"):
        return {"ok": False, "stage": "disabled", "sent": False,
                "why": "上云总开关是关的（cloud.enabled=false）⇒ 一个字节都没发。要真发：先在控制台填好接收端网址并打开总开关"}
    key = dict((k, f) for k, _l, f in KINDS).get(which)
    if not key:
        return {"ok": False, "stage": "config", "sent": False, "why": "which 只能是 persona / blocklist"}
    ok, fixed, why = normalize_url(str(c.get(key) or ""))
    if not ok or not fixed:
        return {"ok": False, "stage": "config", "sent": False, "why": why or "接收端网址未配置"}
    if dry:
        return {"ok": True, "stage": "dry", "sent": False, "url": fixed,
                "bytes": len(json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")),
                "why": "干跑：只算体积，没发出去"}
    # 凭据怎么带（`cloud.auth_style`）：
    #   · `bearer`（默认）＝放 `Authorization: Bearer <token>` 头，**判 2xx 算成功**；
    #   · `body_key`＝放**请求体的 `key` 字段**、不发 Authorization 头，**判回包 `ok:true` 才算成功**
    #     （朋友的站 kondius.cn 那类：未知路径也回 200，但体里是 `{"ok":false,"error":"not found"}`，
    #      只看 2xx 会把"没接住"当成"收到了"）。
    style = str(c.get("auth_style") or "bearer").strip().lower()
    tok = str(c.get("token") or "").strip()
    out_obj = dict(payload or {})
    headers = {"content-type": "application/json; charset=utf-8", "user-agent": "PersonaMorph/1.0"}
    if style == "body_key":
        if tok:
            out_obj["key"] = tok
    elif tok:
        headers["authorization"] = "Bearer " + tok
    body = json.dumps(out_obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(fixed, data=body, headers=headers, method="POST")
    _harden_redirects()                    # V-R9-23：跨主机跳转时剥掉 Bearer
    try:
        with urllib.request.urlopen(req, timeout=max(2.0, c["timeout_ms"] / 1000.0)) as r:
            status = int(getattr(r, "status", 0) or 0)
            base = {"stage": "sent", "sent": True, "url": fixed, "status": status,
                    "bytes": len(body), "auth_style": style}
            if style != "body_key":
                return dict(base, ok=True)
            # body_key 形态：必须**回包确认** ok:true，否则如实说"没接住"
            # V-R9-26：读取带上限（接收端回包正常只有几百字节；1MB 足够，超了就是异常 ⇒ 判"没接住"）
            try:
                from .safe_fetch import read_capped
                raw = read_capped(r, 1024 * 1024, "接收端回包") if hasattr(r, "read") else b""
            except ImportError:
                raw = r.read(1024 * 1024 + 1)[:1024 * 1024]
            except Exception as e:
                return dict(base, ok=False, why="接收端回包超限/读不动，不敢当成功：%s" % str(e)[:60])
            try:
                j = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                return dict(base, ok=False, why="该站要求回包 ok:true 才算收下，但回包不是配置格式（无法确认）")
            if isinstance(j, dict) and j.get("ok") is True:
                return dict(base, ok=True, confirmed=True)
            return dict(base, ok=False,
                        why="接收端回了 ok=false：%s" % str((j or {}).get("error") or (j if isinstance(j, dict) else ""))[:80])
    except Exception as e:
        return {"ok": False, "stage": "sent", "sent": True, "url": fixed, "bytes": len(body),
                "auth_style": style,
                "why": "发出去了但这个地址没接住：%s" % str(e)[:120]}


def snapshot() -> dict:
    c = cfg()
    return {"enabled": bool(c.get("enabled")), "token_set": bool(str(c.get("token") or "").strip()),
            "timeout_ms": int(c.get("timeout_ms") or 0), "allow_private": bool(c.get("allow_private")),
            "endpoints": endpoints(),
            "note": "接口已留好：填网址 + 打开总开关即可接上；默认关＝不会上传任何数据"}
