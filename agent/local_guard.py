# -*- coding: utf-8 -*-
"""本机监听面的**统一门禁**：回环 Host 校验 + 一条只在本机共享的口令。

为什么要有它（2026-09-20，第三轮审计 **V-R3-8**）：产品里不止一个监听面 —— 控制台
`agent/webui.py`（有口令）与本地生图 `agent/sd_local_server.py`（**原来什么都没有**）。
"只绑 127.0.0.1" **不等于安全**：
  ① 浏览器里的任何页面都能向本机端口**发起**请求（CORS 只挡"读"，不挡"发"）；
  ② DNS rebinding 能让外域解析到 127.0.0.1 后，带着**外域 Host** 打进来。
业界口径（OWASP SSRF 备忘单"本机服务自检"那一条）是：**每条路由都要 token + 同源/Host 校验**。
本模块是这条口径的**唯一实现**，两个监听面共用 —— 免得又出现"只改了一半"。

口令放哪：`logs/sd_local.token`（随机 url-safe，只在本机；`logs/` 不进版本库、不进包）。
客户端怎么带：`client_headers(url)` —— **只对回环地址**加 `X-PM-Token` 头；别的后端一律不加，
免得把本机口令发给第三方服务。
"""
import os
import secrets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_REL = os.path.join("logs", "sd_local.token")
ENV_KEY = "PM_LOCAL_TOKEN"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
_CACHE = {"tok": ""}


def token_path(root: str = "") -> str:
    return os.path.join(root or ROOT, TOKEN_REL)


def token(root: str = "", create: bool = True) -> str:
    """读（或首建）本机共享口令。**永不抛**；`create=False` 且读不到就返回空串。"""
    env = str(os.environ.get(ENV_KEY) or "").strip()
    if env:
        return env
    if root is None or root == "":
        if _CACHE["tok"]:
            return _CACHE["tok"]
    p = token_path(root)
    try:
        with open(p, encoding="utf-8") as fh:
            t = fh.read().strip()
        if t:
            if not root:
                _CACHE["tok"] = t
            return t
    except Exception:
        pass
    if not create:
        return ""
    t = secrets.token_urlsafe(24)
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(t + "\n")
        os.replace(tmp, p)
    except Exception:
        pass
    if not root:
        _CACHE["tok"] = t
    return t


def host_ok(host_header: str, port: int = 0) -> bool:
    """`Host` 头必须是**回环**（带不带端口都算）。空 Host 直接拒（HTTP/1.1 里它本就必需）。"""
    h = str(host_header or "").strip().lower()
    if not h:
        return False
    if h.startswith("["):                       # IPv6 形式：`[::1]:7860`
        h = h.split("]")[0].lstrip("[")
    elif ":" in h:
        h = h.split(":")[0]
    return h in LOOPBACK


def is_loopback_url(u: str) -> bool:
    try:
        from urllib.parse import urlparse
        return (urlparse(str(u)).hostname or "").lower() in LOOPBACK
    except Exception:
        return False


def _from_query(path: str) -> str:
    try:
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(str(path)).query)
        return str((q.get("token") or [""])[0]).strip()
    except Exception:
        return ""


def req_token(headers, path: str = "") -> str:
    """从请求里取口令：`X-PM-Token` → `Authorization: Bearer` → `?token=`（A1111 兼容客户端只有 URL 可用）。"""
    try:
        t = str(headers.get("X-PM-Token") or "").strip()
    except Exception:
        t = ""
    if t:
        return t
    try:
        a = str(headers.get("Authorization") or "").strip()
    except Exception:
        a = ""
    if a.lower().startswith("bearer "):
        return a[7:].strip()
    return _from_query(path)


def client_headers(url: str, base: dict = None) -> dict:
    """给**回环地址**的请求加上口令头；非回环原样返回（本机口令只发给本机）。"""
    h = dict(base or {})
    if is_loopback_url(url):
        t = token()
        if t:
            h.setdefault("X-PM-Token", t)
    return h


def check(handler, port: int = 0) -> tuple:
    """给 `BaseHTTPRequestHandler` 用的一站式检查 ⇒ `(ok, http_code, why)`（**fail-closed**）。"""
    if not host_ok(handler.headers.get("Host"), port):
        return False, 403, "Host 不是回环地址"
    want = token()
    if not want:
        # 口令建不出来（logs 写不了）⇒ **宁可拒服务，也不开一个没有门禁的本机监听面**
        return False, 503, "本机口令不可用（logs 目录不可写）"
    if req_token(handler.headers, getattr(handler, "path", "")) != want:
        return False, 401, "缺少/错误的本机口令"
    return True, 200, ""
