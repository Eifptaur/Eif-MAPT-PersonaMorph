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
import logging
import os
import secrets

log = logging.getLogger("persona-morph")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_REL = os.path.join("logs", "sd_local.token")
ENV_KEY = "PM_LOCAL_TOKEN"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
_CACHE = {"tok": ""}
_WARNED = set()


def _warn_once(msg: str) -> None:
    """同一句只提醒一次（这个模块会被反复调用，别刷屏）。"""
    try:
        if msg in _WARNED:
            return
        _WARNED.add(msg)
        log.warning("%s", msg)
    except Exception:
        pass


def token_path(root: str = "") -> str:
    return os.path.join(root or ROOT, TOKEN_REL)


def _new_token_file(p: str, gen: str) -> str:
    """竞态安全地建立口令文件：**独占创建**（`O_CREAT|O_EXCL`）。

    ⛔ 2026-09-20 修（我自己在 V-R3-8 里留下的竞态）：原来是"读不到就生成一个再 `os.replace`"
    —— 两个进程**同时**首建时会互相覆盖，各自 `_CACHE` 住自己那份 ⇒ **同一台机器上出现两个口令**，
    于是本地生图服务拒掉产品自己的请求（表现成"本地生图突然不可用"）。
    现在：独占创建成功 ⇒ 用我这份；已经存在（别人刚建好）⇒ **读回它**，绝不覆盖。
    """
    d = os.path.dirname(p)
    if d:
        try:
            os.makedirs(d, exist_ok=True)          # `logs/` 在全新解压出来的包里可能还没有
        except Exception:
            return ""
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            with open(p, encoding="utf-8") as fh:
                return fh.read().strip()
        except Exception:
            return ""
    except Exception:
        return ""
    try:
        os.write(fd, (gen + "\n").encode("utf-8"))
    finally:
        try:
            os.close(fd)
        except Exception:
            pass
    return gen


def _tighten_acl(p: str) -> str:
    """把口令文件收紧成「只有本人（＋SYSTEM/管理员）能读写」。返回空串＝成功，否则回**为什么**。

    ⛔ 2026-09-21（第四轮审计 **V-R4-13**）：`os.open(..., 0o600)` 的 mode 在 NTFS 上**不改 ACL**
    （实测：文件照样继承父目录的 `BUILTIN\\Users` 等）⇒ 同机其它用户/进程能读到本机口令。
    ⇒ 真用 `icacls` 收紧：**断继承**（`/inheritance:r`）+ 只授本人与系统账号；
    然后**复核**（读回 ACL，只要还有"人人可读"那类主体就报失败）。
    ⚠️ 收紧失败**绝不当成功**（返回原因，调用方写日志）—— 口令仍可用，风险只是本机暴露。
    """
    try:
        import subprocess
        _user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if not _user:
            return "拿不到当前用户名（不收紧，避免把文件锁死）"
        _flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(["icacls", p, "/inheritance:r",
                            "/grant:r", "%s:(R,W)" % _user,
                            "/grant:r", "SYSTEM:(R,W)",
                            "/grant:r", "Administrators:(R,W)"],
                           capture_output=True, text=True, creationflags=_flags)
        if int(getattr(r, "returncode", 1) or 0) != 0:
            return "icacls 收紧失败：%s" % str(getattr(r, "stderr", "") or getattr(r, "stdout", ""))[:80].strip()
        r2 = subprocess.run(["icacls", p], capture_output=True, text=True, creationflags=_flags)
        out = str(getattr(r2, "stdout", "") or "")
        # 复核：`icacls` 每行形如 `<路径> <主体>:(权限)`（第一行带路径、后面只给主体）。
        # ⚠️ 主体名**可能带空格**（`NT AUTHORITY\SYSTEM`）⇒ 不能按空白切词、也不能只截冒号前一段，
        #   否则会把 `NT AUTHORITY\SYSTEM` 截成 `AUTHORITY\SYSTEM` ⇒ 假失败（第一版就踩了）。
        allowed = {str(_user).lower(), "system", "administrators"}          # 只比**末段**（域前缀不算）
        for _line in out.splitlines():
            _i = _line.find(":(")
            if _i < 0:
                continue
            _who = _line[:_i].strip()
            if _who.lower().startswith(str(p).lower()):
                _who = _who[len(str(p)):].strip()
            _last = _who.split("\\")[-1].strip().lower() if _who else ""
            if _last and _last not in allowed:
                return "收紧后仍有其它主体可访问：%s" % _who[:40]
        return ""
    except Exception as e:
        return "收紧异常：%s" % str(e)[:80]


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
            # ⛔ V-R4-13：**读到的既有文件也补一次收紧**（老装机留下的文件 ACL 是宽的）——
            #   每进程只做一次，别在热路径上反复跑 icacls。
            if not _CACHE.get("acl_ok"):
                _why = _tighten_acl(p)
                _CACHE["acl_ok"] = (not _why)
                if _why:
                    _warn_once("口令文件 ACL 没能收紧：%s（口令仍可用，但同机其它用户可能读得到）" % _why)
            if not root:
                _CACHE["tok"] = t
            return t
    except Exception:
        pass
    if not create:
        return ""
    t = _new_token_file(p, secrets.token_urlsafe(24))
    _why = _tighten_acl(p)                       # ⛔ V-R4-13：建完立刻收紧
    if _why:
        _warn_once("新建口令文件的 ACL 没能收紧：%s" % _why)
    else:
        _CACHE["acl_ok"] = True
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
