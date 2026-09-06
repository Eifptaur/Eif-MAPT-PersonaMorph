# -*- coding: utf-8 -*-
"""Web 控制台：在浏览器里改设置、看状态、看日志、测试 API（移植自 qq-agent 的控制台思路）。

零第三方依赖，纯标准库 http.server，单文件 HTML（内联 CSS/JS，无框架）。
只监听本机回环地址，含可选访问口令。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import get_config, save_config, set_config
from .console_html import HTML  # 界面模板（蓝白设计，设置项全量，独立文件便于改版）
from .util import mask_secret, redact_secrets

# 给挂件脚本（whale-widget/client/widget.js）注入访问口令：把脚本里的 /dsh-whale/*
# 绝对路径都补上 ?token=xxx，保证前端轮询/音频请求都带上口令
_WHALE_URL_RE = re.compile(r"(/dsh-whale/[^'\"\s?]+)(\?[^'\"\s]*)?")

_MASKED_MARK = "••••"


def _is_masked(v) -> bool:
    return isinstance(v, str) and (_MASKED_MARK in v or v.startswith("sk-***"))


def _protect_secrets(new_cfg: dict):
    """保存配置时：若密钥字段还是打码值，则不覆盖真实密钥。"""
    old = get_config()
    api_new = new_cfg.get("api")
    if isinstance(api_new, dict):
        api_old = old.get("api") or {}
        if _is_masked(api_new.get("api_key")):
            api_new["api_key"] = api_old.get("api_key") or ""
        pk_new = api_new.get("provider_keys")
        if isinstance(pk_new, dict):
            pk_old = (api_old.get("provider_keys") or {}) if isinstance(api_old, dict) else {}
            for k, v in pk_new.items():
                if _is_masked(v):
                    pk_new[k] = pk_old.get(k) or ""


class WebUI:
    """启动一个仅监听本机的 HTTP 服务，提供设置/状态/日志/测试 API 接口。"""

    def __init__(self, status_provider, log_buffer, test_api_fn=None, on_save=None,
                 pause_fn=None, resume_fn=None, balance_fn=None, shutdown_fn=None,
                 whale=None, poke_test_fn=None, selfcheck_fn=None, restart_fn=None,
                 groups_fn=None, memory_fn=None, sessions_fn=None):
        self.status_provider = status_provider      # () -> dict
        self.log_buffer = log_buffer                # collections.deque[str]
        self.test_api_fn = test_api_fn              # () -> dict
        self.on_save = on_save                      # (new_cfg) -> None（可选，用于通知运行中组件）
        self.pause_fn = pause_fn or (lambda: None)  # () -> None
        self.resume_fn = resume_fn or (lambda: None)  # () -> None
        self.balance_fn = balance_fn or (lambda: {"error": "未提供 balance_fn"})  # () -> dict
        self.shutdown_fn = shutdown_fn or (lambda: None)  # () -> None
        self.restart_fn = restart_fn or (lambda: None)    # () -> None（后台无窗口重启）
        self.whale = whale                          # agent.whale.WhaleWidget（小鲸鱼挂件，可选）
        self.poke_test_fn = poke_test_fn or (lambda: {"error": "未提供 poke_test_fn"})  # () -> dict
        self.selfcheck_fn = selfcheck_fn or (lambda: {"ok": False, "error": "未提供 selfcheck_fn"})  # () -> dict
        self.groups_fn = groups_fn or (lambda: {"ok": True, "groups": []})  # () -> dict（群列表）
        self.memory_fn = memory_fn or (lambda action, chat_key="", user_id="": {"ok": True,
                                                                               "chats": [], "members": []})  # (action, chat_key, user_id) -> dict
        self.sessions_fn = sessions_fn or (lambda limit: [])  # (limit) -> list（运行明细）
        self._server = None
        self._thread = None
        self.port = 0
        self._whale_js_cache = {}  # token -> bytes（注入口令后的挂件脚本缓存）
        # 加载图标（assets/icon.png），用于 favicon
        self._icon_bytes = b""
        try:
            icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.png")
            with open(icon_path, "rb") as f:
                self._icon_bytes = f.read()
        except Exception:
            self._icon_bytes = b""

    # ── 小鲸鱼挂件路由（/dsh-whale/*，实现与原版插件一致的接口）───────────

    def _whale_get(self, handler, path: str, query: str):
        """GET /dsh-whale/* 分发。handler 是当前 HTTP Handler（带 _json/_bytes）。"""
        whale = self.whale
        if whale is None:
            return handler._json({"error": "not found"}, 404)
        if path == "/dsh-whale/balance.json":
            try:
                handler._json(whale.balance_payload())
            except Exception as e:
                handler._json({"ok": False, "error": str(e)[:200]})
        elif path == "/dsh-whale/size.json":
            handler._json(whale.size_payload())
        elif path == "/dsh-whale/last-turn.json":
            handler._json(whale.last_turn_payload())
        elif path == "/dsh-whale/image.png":
            handler._bytes(whale.asset_bytes("DSniang1.png") or b"", "image/png")
        elif path == "/dsh-whale/rua.gif":
            handler._bytes(whale.asset_bytes("rua.gif") or b"", "image/gif")
        elif path in ("/dsh-whale/sound/press.mp3", "/dsh-whale/sound/release.mp3"):
            kind = "press" if path.endswith("press.mp3") else "release"
            sound_set = (parse_qs(query).get("set") or [""])[0]
            data = whale.sound_bytes(kind, sound_set)
            handler._bytes(data or b"", "audio/mpeg")
        elif path == "/dsh-whale/widget.js":
            handler._bytes(self._whale_js_injected(), "application/javascript; charset=utf-8")
        else:
            handler._json({"error": "not found"}, 404)

    def _whale_js_injected(self) -> bytes:
        """返回注入口令后的挂件脚本字节（带缓存）。"""
        token = str(get_config().get("server", {}).get("token") or "").strip()
        if token in self._whale_js_cache:
            return self._whale_js_cache[token]
        try:
            js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "whale-widget", "client", "widget.js")
            with open(js_path, "r", encoding="utf-8") as f:
                js = f.read()
        except Exception:
            return b""
        if token:
            def _inj(m):
                base, q = m.group(1), (m.group(2) or "")[1:]
                return base + "?token=" + token + ("&" + q if q else "")
            js = _WHALE_URL_RE.sub(_inj, js)
        body = js.encode("utf-8")
        if len(self._whale_js_cache) > 4:
            self._whale_js_cache.clear()
        self._whale_js_cache[token] = body
        return body

    # ── 配置脱敏 ──────────────────────────────────────────────────────────

    def masked_config(self) -> dict:
        """返回配置副本：所有 API Key 打码（真实值只存服务器 config.json）。"""
        cfg = dict(get_config())
        api = dict(cfg.get("api") or {})
        if api.get("api_key"):
            api["api_key"] = mask_secret(api["api_key"])
        pk = api.get("provider_keys")
        if isinstance(pk, dict):
            api["provider_keys"] = {k: mask_secret(v) for k, v in pk.items() if v}
        cfg["api"] = api
        return cfg

    def start(self) -> int:
        cfg = get_config().get("server", {})
        if cfg.get("enabled") is False:
            return 0
        host = str(cfg.get("host") or "127.0.0.1")
        port = int(cfg.get("port") or 3210)

        parent = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "wx-agent/1.0"

            def log_message(self, fmt, *args):
                pass  # 静默，避免刷屏

            def _json(self, obj, code=200):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _bytes(self, body, ctype="application/octet-stream", code=200):
                if not body:
                    body = b""
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _auth_ok(self):
                token = str(get_config().get("server", {}).get("token") or "").strip()
                if not token:
                    return True
                # 支持 ?token= 或 Authorization: Bearer
                q = urlparse(self.path).query
                from urllib.parse import parse_qs
                if token in parse_qs(q).get("token", []):
                    return True
                auth = self.headers.get("Authorization", "")
                return auth == "Bearer " + token

            def do_GET(self):
                if not self._auth_ok():
                    return self._json({"error": "unauthorized"}, 401)
                parsed = urlparse(self.path)
                path = parsed.path
                if path in ("/", "/index.html"):
                    token = str(get_config().get("server", {}).get("token") or "").strip()
                    body = HTML.replace("__TKN__", token).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path.startswith("/dsh-whale/"):
                    parent._whale_get(self, path, parsed.query)
                elif path == "/assets/icon.png":
                    body = parent._icon_bytes
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif path == "/api/config":
                    self._json(parent.masked_config())
                elif path == "/api/memory":
                    # 记忆页面：?chat_key= 传群则返回该群成员印象列表
                    q = parse_qs(parsed.query)
                    chat_key = (q.get("chat_key") or [""])[0]
                    try:
                        self._json(parent.memory_fn("list", chat_key))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/sessions":
                    # 运行明细（思考/token/工具）：?limit=30
                    q = parse_qs(parsed.query)
                    try:
                        self._json({"ok": True, "sessions": parent.sessions_fn(
                            int((q.get("limit") or ["30"])[0]))})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/status":
                    self._json(parent.status_provider())
                elif path == "/api/balance":
                    try:
                        self._json(parent.balance_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/logs":
                    self._json({"lines": list(parent.log_buffer)})
                elif path == "/api/wechat-groups":
                    # 检测到的群聊列表（白名单勾选用，GET）
                    try:
                        self._json(parent.groups_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e), "groups": []})
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                if not self._auth_ok():
                    return self._json({"error": "unauthorized"}, 401)
                self._handle_body_request()

            def do_PUT(self):
                # 小鲸鱼挂件前端用 PUT 保存配置（fetch SIZE_URL, {method:'PUT'}）
                if not self._auth_ok():
                    return self._json({"error": "unauthorized"}, 401)
                self._handle_body_request()

            def _handle_body_request(self):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    data = {}
                if path == "/api/config":
                    try:
                        new_cfg = data if isinstance(data, dict) and data else get_config()
                        _protect_secrets(new_cfg)  # 掩码值不覆盖真实密钥
                        set_config(new_cfg)
                        save_config(new_cfg)
                        if parent.on_save:
                            parent.on_save(new_cfg)
                        self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/memory":
                    # 删除某成员的印象（记忆页）
                    try:
                        r = parent.memory_fn("delete", str(data.get("chat_key") or ""),
                                             str(data.get("user_id") or ""))
                        self._json(r)
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/sessions":
                    # 运行明细：思考过程 / token / 工具调用
                    try:
                        self._json({"ok": True, "sessions": parent.sessions_fn(
                            int(data.get("limit") or 30))})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/dsh-whale/size.json":
                    # 小鲸鱼挂件配置保存（前端 PUT）
                    if parent.whale is None:
                        self._json({"error": "not found"}, 404)
                    else:
                        try:
                            self._json(parent.whale.save_size(data))
                        except Exception as e:
                            self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/test-api":
                    try:
                        if parent.test_api_fn:
                            self._json(parent.test_api_fn())
                        else:
                            self._json({"ok": False, "error": "未提供 test_api_fn"})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/poke-test":
                    # 拍一拍诊断：完整跑一遍并返回分步结果
                    # body: {group_wxid?, verify_only?}
                    try:
                        self._json(parent.poke_test_fn(str(data.get("group_wxid") or ""),
                                                       bool(data.get("verify_only"))))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/selfcheck":
                    # 一键体检：配置/微信/数据/界面适配/命中测试 全套
                    try:
                        self._json(parent.selfcheck_fn())
                    except Exception as e:
                        self._json({"ok": False, "checks": [], "summary": str(e)})
                elif path == "/api/wechat-groups":
                    # 检测到的群聊列表（白名单勾选用）
                    try:
                        self._json(parent.groups_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e), "groups": []})
                elif path == "/api/pause":
                    parent.pause_fn()
                    self._json({"ok": True})
                elif path == "/api/resume":
                    parent.resume_fn()
                    self._json({"ok": True})
                elif path == "/api/shutdown":
                    self._json({"ok": True, "note": "正在停止机器人…"})
                    # 稍等响应返回后再触发停止，避免连接被切断
                    threading.Timer(0.5, parent.shutdown_fn).start()
                elif path == "/api/restart":
                    # 重启：后台无窗口拉起新实例（释放端口后接替），当前实例退出
                    self._json({"ok": True, "note": "正在后台重启机器人…"})
                    threading.Timer(0.5, parent.restart_fn).start()
                else:
                    self._json({"error": "not found"}, 404)

        # 端口自适应：被占用则顺延
        for offset in range(20):
            try:
                self._server = ThreadingHTTPServer((host, port + offset), Handler)
                self.port = port + offset
                break
            except OSError:
                continue
        if self._server is None:
            raise RuntimeError("无法启动 Web 控制台：端口 %d-%d 均被占用" % (port, port + 19))

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
