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

from .config import deep_merge, get_config, save_config, set_config
from .console_html import HTML  # 界面模板（蓝白设计，设置项全量，独立文件便于改版）
from .util import mask_secret, redact_secrets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 数据迁移包（导出/导入：计费+对话记录 data/sessions/*.jsonl）────────────

def _data_export(root: str) -> bytes:
    """所有计费/对话记录导成一个 zip（sessions/YYYY-MM-DD.jsonl + manifest）。"""
    import io
    import zipfile
    import json as _j
    import glob
    buf = io.BytesIO()
    sdir = os.path.join(root, "data", "sessions")
    files = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.isdir(sdir):
            for fp in sorted(glob.glob(os.path.join(sdir, "*.jsonl"))):
                z.write(fp, "sessions/" + os.path.basename(fp))
                files.append(os.path.basename(fp))
        z.writestr("manifest.json", _j.dumps(
            {"app": "wx-agent", "v": 1, "files": files}, ensure_ascii=False))
    return buf.getvalue()


def _data_import(root: str, body: bytes) -> dict:
    """导入迁移包：按日文件合并，行级内容 md5 去重（同一条记录不重复）。"""
    import io
    import zipfile
    import os as _os
    import hashlib
    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except Exception:
        return {"ok": False, "error": "不是有效的迁移包（应选择「导出记录」生成的文件）"}
    sdir = _os.path.join(root, "data", "sessions")
    _os.makedirs(sdir, exist_ok=True)
    added = 0
    days = []
    for name in zf.namelist():
        base = _os.path.basename(name)
        if not (name.startswith("sessions/") and base.endswith(".jsonl")):
            continue
        data = zf.read(name)
        lines = [l for l in data.decode("utf-8", "replace").splitlines() if l.strip()]
        dst = _os.path.join(sdir, base)
        exists = set()
        if _os.path.exists(dst):
            with open(dst, "r", encoding="utf-8", errors="replace") as f:
                for l in f:
                    exists.add(hashlib.md5(l.strip().encode("utf-8", "replace")).hexdigest())
        new_lines = []
        for l in lines:
            l = l.strip()
            h = hashlib.md5(l.encode("utf-8", "replace")).hexdigest()
            if h in exists:
                continue
            exists.add(h)
            new_lines.append(l)
        if new_lines:
            with open(dst, "a", encoding="utf-8", errors="replace") as f:
                f.write("\n".join(new_lines) + "\n")
            added += len(new_lines)
            days.append(base)
    return {"ok": True, "added": added, "days": days,
            "note": "新导入 %d 条记录（覆盖 %d 个日期文件，同内容自动去重）" % (added, len(days))}

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

    def _data_path(self, name: str) -> str:
        """data 目录文件（尊重 WX_AGENT_DATA_DIR 环境，兼容测试隔离）。"""
        base = os.environ.get("WX_AGENT_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data")
        return os.path.join(base, name)

    def _apply_whale(self, html: str) -> str:
        """鲸语文案服务端注入（保存后刷新必然正确；与前端 JS 时序无关）。

        注意要点（修复"切不回正常/切了没生效"）：
        1) 只替换「鲸语 JS 字典标记」之前的区域（head+可见UI）——字典本身绝不能替换，
           否则 applyWhale 的前端映射键被改坏；JS 字典用 ── 🐋 鲸语版界面文案 注释标记。
        2) 全部匹配（不再是 replace(...,1)——旧版只替换每键第一处，被 CSS 注释/标题吃掉，
           导航/按钮全变不回鲸语）；按 key 长度倒序替换（长 key 先换，防"停止"吞"停止检测"）。
        """
        try:
            cfg = get_config().get("ui", {}) or {}
            if str(cfg.get("text_style") or "") != "whale":
                return html
        except Exception:
            return html
        T = {
            "wx-agent 控制台": "🐋 鲸鲸号 · 深度摸鱼",
            "概览": "🐋 概览 · 我是AI，别催，CPU还在烧",
            "检测中心（代码检测 / 鼠标操作检测）": "检测中心（先体检，再摸鱼）",
            "体检与功能自检": "检测中心 · 出远门前先体检",
            "功能自检清单（按重要性排序）": "功能自检清单（按重要性，一个一个过）",
            "调试 · 高级功能": "调试 · 高级功能（一般人我不告诉他）",
            "调试·高级功能": "调试·高级功能（一般人我不告诉他）",
            "运行明细": "📋 运行明细 · 内心戏全程有记录",
            "模型 API": "🧊 模型 API · 让我先推理一下，别插嘴",
            "微信": "💬 微信 · 收到，正在假装思考",
            "拍一拍（行为）": "👋 拍一拍 · 拍我干嘛，我只是个蓝鲸",
            "拍一拍": "👋 拍一拍 · 拍我干嘛，我只是个蓝鲸",
            "记忆（群友印象）": "🧠 记忆 · 好像记得…算了不装了",
            "记忆（共享设置）": "🤝 记忆共享 · 它记得=我记得，别问",
            "记忆": "🧠 记忆 · 好像记得…算了不装了",
            "记忆共享": "🤝 记忆共享 · 它记得=我记得，别问",
            "人设与响应": "🎭 人设 · 今天演谁？剧本拿来",
            "社区与学习": "📚 社区 · 好东西先白嫖再说",
            "发送限制": "🚦 发送限制 · 我不回你，就是我在偷懒",
            "联网搜索": "🔎 联网搜索 · 我去搜搜，先不告诉你结果",
            "服务器": "🖥️ 服务器 · 服务器繁忙，再试一次",
            "界面适配（DPI / 遮挡 / 主题）": "🎨 界面 · AI也要体面",
            "界面适配": "🎨 界面 · AI也要体面",
            "🐋 光标设置": "🖱️ 光标设置 · 别看我，看我的鼠标",
            "光标设置": "🖱️ 光标设置 · 别看我，看我的鼠标",
            "🌊 水光波纹（鼠标投石入水）": "🌊 水光波纹 · 人家怕水，我就爱摸鱼",
            "🌊 水光波纹": "🌊 水光波纹 · 人家怕水，我就爱摸鱼",
            "水光波纹": "🌊 水光波纹 · 人家怕水，我就爱摸鱼",
            "运行日志": "📜 运行日志 · 我的内心OS全在这",
            "完整配置 JSON（高级）": "📄 原始 JSON · 底裤都给你看",
            "原始 JSON": "📄 原始 JSON · 底裤都给你看",
            "保存全部设置": "保存全部设置（存好了，我不会失忆的）",
            "暂停": "⏸ 暂停（歇会儿）",
            "恢复": "▶ 恢复（满血）",
            "停止": "停止（打烊）",
            "重启": "重启（我又行了）",
            "测试 API 连通": "测试 API 连通（先冲个电，马上好）",
            "代码检测＋依赖核对": "代码检测＋依赖核对（少了什么先补课）",
            "代码检测": "代码检测（先查bug，再查心情）",
            "查看进度条": "查看进度条（别催，在跑了）",
            "鼠标操作检测": "鼠标操作检测（AI也要做视力检查）",
            "一键体检": "鼠标操作检测（AI也要做视力检查）",
            "停止检测": "停止检测（不测了，我摊牌）",
            "拍一拍检测": "拍一拍检测（别真拍我）",
            "模型评分": "模型评分（AI打分，绝不偏袒）",
            "模型补足": "模型补足（拾掇拾掇，更像本人）",
            "根据角色卡推荐行为档": "根据角色卡推荐行为档（我懂你）",
            "保存 Key": "保存 Key（钥匙收好了）",
            "重置 Key（重新填写）": "重置 Key（换把钥匙）",
        }
        MARK = "鲸语版界面文案"
        idx = html.find(MARK)
        head, tail = (html[:idx], html[idx:]) if idx >= 0 else (html, "")
        # 单趟正则替换（最长key优先）——替换值不再被扫描，杜绝"功能自检清单"二次污染
        import re as _re
        keys = sorted(T.keys(), key=lambda k: -len(k))
        pat = _re.compile("|".join(_re.escape(k) for k in keys))
        head = pat.sub(lambda m: T[m.group(0)], head)
        return head + tail

    def _build_tag(self):
        """构建号（方便辨别新旧实例：console_html.py 修改时间 + 启动概率）。"""
        try:
            mt = os.path.getmtime(_HTML_SRC if "_HTML_SRC" in globals() else os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "console_html.py"))
            import datetime
            return "b." + datetime.datetime.fromtimestamp(mt).strftime("%m%d-%H%M")
        except Exception:
            return "b?"

    def __init__(self, status_provider, log_buffer, test_api_fn=None, on_save=None,
                 pause_fn=None, resume_fn=None, balance_fn=None, shutdown_fn=None,
                 whale=None, poke_test_fn=None, selfcheck_fn=None, restart_fn=None,
                 groups_fn=None, memory_fn=None, sessions_fn=None, emojis_fn=None,
                 recalibrate_fn=None, open_path_fn=None, ui_test_fn=None,
                 selfcheck_stop_fn=None, ui_stop_fn=None,
                 persona_scores_fn=None, persona_rate_fn=None,
                 persona_score_custom_fn=None, persona_ai_enrich_fn=None,
                 community_export_fn=None, community_upload_fn=None, scoring_import_fn=None):
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
        self.emojis_fn = emojis_fn or (lambda: [])            # () -> list（表情包收藏夹）
        self.recalibrate_fn = recalibrate_fn or (lambda: {"ok": False, "error": "未提供"})
        self.open_path_fn = open_path_fn or (lambda path: {"ok": False, "error": "未提供"})
        self.ui_test_fn = ui_test_fn or (lambda kind: {"ok": False, "error": "未提供"})
        self.selfcheck_stop_fn = selfcheck_stop_fn or (lambda: None)
        self.ui_stop_fn = ui_stop_fn or (lambda: {"ok": False, "error": "未提供"})
        self.persona_scores_fn = persona_scores_fn or (lambda: {"ok": False, "error": "未提供"})
        self.persona_rate_fn = persona_rate_fn or (lambda k, s, n: {"ok": False, "error": "未提供"})
        self.persona_score_custom_fn = persona_score_custom_fn or (lambda t, l: {"ok": False, "error": "未提供"})
        self.persona_ai_enrich_fn = persona_ai_enrich_fn or (lambda n, t: {"ok": False, "error": "未提供"})
        self.community_export_fn = community_export_fn    # (kind) -> dict 金句/意见/聊天记录导出
        self.community_upload_fn = community_upload_fn    # (data) -> dict 上传到可配 URL
        self.scoring_import_fn = scoring_import_fn        # (text) -> dict 导入种子库
        self._server = None
        self._thread = None
        self.port = 0
        self._whale_js_cache = {}  # token -> bytes（注入口令后的挂件脚本缓存）
        # 静态素材根目录（assets\，含 logo-bg / icon-whale / cursor / custom-cursor）
        self._asset_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
        # 加载图标（assets/icon.png），用于 favicon
        self._icon_bytes = b""
        try:
            icon_path = os.path.join(self._asset_root, "icon.png")
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

    def _serve_wallpaper(self, path: str, handler, query):
        """视频壁纸（免认证，支持 Range 分段）：assets/wallpaper/<file>，供 <video> 背景流式播放。"""
        try:
            import urllib.parse as _up
            name = _up.unquote(os.path.basename(path))
            wdir = os.path.join(self._asset_root, "wallpaper")
            fp = os.path.join(wdir, name)
            if not os.path.exists(fp):
                handler._bytes(b"", "video/mp4", 404)
                return
            size = os.path.getsize(fp)
            ftype = "video/mp4"
            rng = handler.headers.get("Range")
            start, end = 0, size - 1
            if rng and rng.startswith("bytes="):
                try:
                    part = rng[6:].split(",", 1)[0]
                    s, _, e = part.partition("-")
                    start = int(s) if s else 0
                    end = int(e) if e else size - 1
                except Exception:
                    start, end = 0, size - 1
            end = min(end, size - 1)
            length = max(0, end - start + 1)
            handler.send_response(206 if rng else 200)
            handler.send_header("Content-Type", ftype)
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Length", str(length))
            if rng:
                handler.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            handler.end_headers()
            with open(fp, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            try:
                handler._bytes(b"", "video/mp4", 404)
            except Exception:
                pass

    def _serve_asset(self, path: str, handler):
        """静态素材服务（免认证）：favicon / 图标背景 / 鲸鱼主体 / 光标 / 表情收藏夹。"""
        name = os.path.basename(path)
        try:
            if name == "ui-bg.jpg":
                # 自定义背景图（用户上传）
                fp = self._data_path("ui_bg.jpg")
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        handler._bytes(f.read(), "image/jpeg")
                    return
                handler._bytes(b"", "image/jpeg", 404)
                return
            if name.startswith("custom-cursor"):
                # 自定义光标（用户上传到 assets/custom-cursor.png）；缺失=404（浏览器光标回退系统默认）
                fp = os.path.join(self._asset_root, "custom-cursor.png")
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        handler._bytes(f.read(), "image/png")
                    return
                handler._bytes(b"", "image/png", 404)
                return
            if path.startswith("/assets/emoji/"):
                import urllib.parse as _up
                name = _up.unquote(name)
                emoji_dir = self._data_path("emojis")
                with open(os.path.join(emoji_dir, name), "rb") as f:
                    body = f.read()
            elif name == "icon.png":
                body = self._icon_bytes
            elif name == "DSniang1.png":
                body = (self.whale.asset_bytes(name) if self.whale else None) or b""
            else:
                # 默认资源：assets/ 根，其次 assets/wallpaper/（ocean1.jpg 等）
                for base in (self._asset_root, os.path.join(self._asset_root, "wallpaper")):
                    fp = os.path.join(base, name)
                    if os.path.exists(fp):
                        with open(fp, "rb") as f:
                            body = f.read()
                        break
                else:
                    raise FileNotFoundError(name)
        except FileNotFoundError:
            handler._bytes(b"", "application/octet-stream", 404)
            return
        except Exception:
            body = b""
        handler._bytes(body, "image/png")

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
                # ① 会话 Cookie（登录后 URL 不带 token，防他人复制地址登入）
                try:
                    import http.cookies as _hc
                    for m in re.findall(r"(?:^|;\s*)wxauth=([^;]+)", str(self.headers.get("Cookie") or "")):
                        if m.strip() == token:
                            return True
                except Exception:
                    pass
                # ② 支持 ?token= 或 Authorization: Bearer
                q = urlparse(self.path).query
                from urllib.parse import parse_qs
                if token in parse_qs(q).get("token", []):
                    return True
                auth = self.headers.get("Authorization", "")
                return auth == "Bearer " + token

            def _set_session_cookie(self):
                """登录成功时种会话 Cookie（HttpOnly，防 JS 读取）。
                注意：必须在 send_response() 之后调用（先 send_header 会让 Set-Cookie 排到状态行前面，响应直接坏掉）。"""
                try:
                    token = str(get_config().get("server", {}).get("token") or "").strip()
                    self.send_header("Set-Cookie", "wxauth=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000" % token)
                except Exception:
                    pass

            def _json(self, obj, code=200):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                if code == 200:
                    self._set_session_cookie()   # 成功响应才种 cookie（在状态行/Server/Date 之后）
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                # 静态素材（图标/光标图）免认证：<img> 不带 token，但素材不含隐私
                if path.startswith("/assets/"):
                    return parent._serve_asset(path, self)
                if path.startswith("/wallpaper/"):
                    return parent._serve_wallpaper(path, self, parsed.query)
                if path == "/api/version":
                    # 免认证版本指纹（启动器/新实例探测旧实例用；不含隐私）
                    self._json({"ver": parent._build_tag()})
                    return
                if not self._auth_ok():
                    return self._json({"error": "unauthorized"}, 401)
                # 防窥视：地址栏乱码路径（单段 /aB3$xy…，无 API/静态前缀）也返回控制台页面
                if path == "/" or path == "/index.html":
                    pass  # 正常控制台页
                elif path.startswith("/api/") or path.startswith("/dsh-whale/") \
                        or path.startswith("/assets/") or path.startswith("/wallpaper/"):
                    pass  # 正常 API/静态路由（下方继续匹配）
                elif "/" not in path[1:]:
                    # 单段乱码路径 → 当控制台页
                    import re as _repath
                    if not _repath.fullmatch(r"/[A-Za-z0-9#$~_\-]{6,64}", path):
                        return self._json({"error": "not found"}, 404)
                    path = "/"
                else:
                    return self._json({"error": "not found"}, 404)
                if path in ("/", "/index.html"):
                    token = str(get_config().get("server", {}).get("token") or "").strip()
                    body = HTML.replace("__TKN__", token)
                    body = body.replace("__VER__", parent._build_tag())
                    body = parent._apply_whale(body)
                    body = body.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                    self._set_session_cookie()   # 必须在 send_response 之后（Set-Cookie 排在状态行/Server/Date 后）
                    self.end_headers()
                    self.wfile.write(body)
                elif path.startswith("/dsh-whale/"):
                    parent._whale_get(self, path, parsed.query)
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
                elif path == "/api/emojis":
                    # 表情包收藏夹列表（GET）
                    try:
                        self._json({"ok": True, "emojis": parent.emojis_fn()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e), "emojis": []})
                elif path == "/api/ui-test/stop":
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/personas/scores":
                    # 角色评分表：系统自动贴合分 + 用户分（GET）
                    try:
                        self._json(parent.persona_scores_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/personas":
                    # 热门人设选单（GET，带分区 cat）
                    try:
                        from agent.persona import PERSONAS, PERSONA_CATS
                        self._json({"ok": True, "personas": [
                            {"key": k, "name": v.get("name") or k, "text": v.get("text") or "",
                             "cat": PERSONA_CATS.get(k, "🔥 网络热门")}
                            for k, v in PERSONAS.items()]})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/persona/cats":
                    # 分区列表（GET）：内置 + 用户新建分区
                    try:
                        import json as _json
                        _p = os.path.join(parent._asset_root, "..", "data", "persona_cats.json")
                        try:
                            with open(_p, "r", encoding="utf-8") as f:
                                user_cats = _json.load(f)
                        except Exception:
                            user_cats = {}
                        from agent.persona import PERSONA_CATS
                        built = sorted(set(PERSONA_CATS.values()))
                        self._json({"ok": True, "built": built,
                                    "user": [{"name": k, "desc": (v or {}).get("desc", "")} for k, v in user_cats.items()]})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/personas/custom":
                    # 自定义角色卡列表（GET）
                    try:
                        import json as _json
                        _p = os.path.join(parent._asset_root, "..", "data", "custom_personas.json")
                        try:
                            with open(_p, "r", encoding="utf-8") as f:
                                items = _json.load(f)
                        except Exception:
                            items = {}
                        self._json({"ok": True, "custom": [
                            {"key": k, "name": (v or {}).get("name", k), "text": (v or {}).get("text", ""),
                             "cat": (v or {}).get("cat") or "📝 自定义"}
                            for k, v in items.items()]})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/ui-layout":
                    # 微信 UI 图标库标定状态（GET）
                    try:
                        from agent.wechat_ui import _load_layout
                        self._json({"ok": True, "layout": _load_layout()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/ui/recalibrate":
                    # 重新标定微信 UI 图标库（POST；接管鼠标瞬间，需微信在前台）
                    try:
                        self._json(parent.recalibrate_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/open-path":
                    # 打开导出文件所在位置（POST {path}）
                    try:
                        self._json(parent.open_path_fn(str(data.get("path") or "")))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/ui-test":
                    # 程序鼠标检验（POST {kind}：程序直接操控鼠标执行对应操作；data 透传给检验函数）
                    try:
                        self._json(parent.ui_test_fn(str(data.get("kind") or ""), data))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/selfcheck-stop":
                    # 停止当前一键体检（POST；设置取消标志，体检循环下一步即退出）
                    try:
                        parent.selfcheck_stop_fn()
                        self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
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
                        # 部分字段保存不丢段：与当前配置深合并（新值优先，缺失键保留旧值）
                        # 注意：deep_merge 返回全新深拷贝，绝不能原地改 _current_config，
                        # 否则 _protect_secrets 拿到的"旧值"已被掩码写脏，真实 key 会丢失。
                        new_cfg = deep_merge(get_config(), new_cfg)
                        _protect_secrets(new_cfg)  # 掩码值不覆盖真实密钥
                        set_config(new_cfg)
                        save_config(new_cfg)
                        if parent.on_save:
                            parent.on_save(new_cfg)
                        self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/cursor/upload":
                    # 自定义光标：base64 PNG/JPEG → assets/custom-cursor.png
                    # 浏览器 css cursor 硬限制：≤128×128、PNG/SVG/ICO、透明底最佳、加载失败静默回退（白箭头根因=404空图）
                    try:
                        import base64
                        b64 = str(data.get("image") or "")
                        if len(b64) > 12 * 1024 * 1024:
                            return self._json({"ok": False, "error": "图片过大（≤8MB 源图）"}, 400)
                        if b64.startswith("data:"):
                            b64 = b64.split(",", 1)[1]
                        img = base64.b64decode(b64)
                        if not img.startswith(b"\x89PNG") and not img.startswith(b"\xff\xd8"):
                            return self._json({"ok": False, "error": "仅支持 PNG/JPEG 图片"}, 400)
                        # 强制 ≤64px（CSS 光标在部分 DPI 下 128 会变糊/超限兼容不佳；64 最稳）+ RGBA 透明保底
                        from PIL import Image as _PILImg
                        import io as _io
                        try:
                            _im = _PILImg.open(_io.BytesIO(img)).convert("RGBA")
                            _im.thumbnail((64, 64), _PILImg.LANCZOS)
                            _buf = _io.BytesIO()
                            _im.save(_buf, "PNG", optimize=True)
                            _img_out = _buf.getvalue()
                        except Exception:
                            return self._json({"ok": False, "error": "图片解析失败，请换 PNG/JPEG"}, 400)
                        with open(os.path.join(parent._asset_root, "custom-cursor.png"), "wb") as f:
                            f.write(_img_out)
                        # 同步生成"点头帧"（点击时换帧，与默认光标同机制）
                        try:
                            _nod = _im.rotate(10, resample=_PILImg.BICUBIC, expand=False, fillcolor=(0, 0, 0, 0))
                            _nod.thumbnail((60, 58), _PILImg.LANCZOS)
                            _nc = _PILImg.new("RGBA", (64, 64), (0, 0, 0, 0))
                            _nc.paste(_nod, (2, 6), _nod)
                            _nb = _io.BytesIO()
                            _nc.save(_nb, "PNG", optimize=True)
                            with open(os.path.join(parent._asset_root, "custom-cursor-nod.png"), "wb") as f:
                                f.write(_nb.getvalue())
                        except Exception:
                            pass
                        self._json({"ok": True, "note": "自定义光标已保存（≤64px PNG，含点头帧）"})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/cursor/reset":
                    # 重置为默认鲸鱼：删除自定义光标残留文件（否则页面刷新后预览仍探测到旧文件——030538）
                    try:
                        _p = os.path.join(parent._asset_root, "custom-cursor.png")
                        if os.path.exists(_p):
                            os.remove(_p)
                        self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/memory":
                    # 记忆页：delete（删某成员印象） / update（编辑成员印象）
                    try:
                        action = str(data.get("action") or "delete")
                        if action == "update":
                            r = parent.memory_fn("update", str(data.get("chat_key") or ""),
                                                 str(data.get("user_id") or ""),
                                                 str(data.get("name") or ""),
                                                 data.get("contents") or [])
                        else:
                            r = parent.memory_fn(action, str(data.get("chat_key") or ""),
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
                    # body.mode="code" = 只做代码与依赖级检查（不动鼠标；首次向导用）
                    try:
                        _mode = "code" if str((data or {}).get("mode") or "") == "code" else "full"
                        self._json(parent.selfcheck_fn(_mode))
                    except Exception as e:
                        self._json({"ok": False, "checks": [], "summary": str(e)})
                elif path == "/api/prices":
                    # 内置官方价目（llm._OFFICIAL_PRICES，费用计算器用）
                    try:
                        from . import llm as _llm_mod
                        self._json(getattr(_llm_mod, "_OFFICIAL_PRICES", {}) or {})
                    except Exception as _e:
                        self._json({"__err": str(_e)})
                elif path == "/api/data/export":
                    # 导出全部计费+对话记录为一个迁移包（zip）
                    try:
                        self._bytes(_data_export(ROOT), "application/zip")
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/data/import":
                    # 导入迁移包（body=zip 原始字节；合并、按内容去重）
                    try:
                        self._json(_data_import(ROOT, raw if isinstance(raw, (bytes, bytearray)) else b""))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/wechat-groups":
                    # 检测到的群聊列表（白名单勾选用）
                    try:
                        self._json(parent.groups_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e), "groups": []})
                elif path == "/api/community/export":
                    # 导出：金句/意见/聊天记录/角色评分 → 本地文件
                    try:
                        if not parent.community_export_fn:
                            self._json({"ok": False, "error": "未提供导出功能"})
                        else:
                            self._json(parent.community_export_fn(str(data.get("kind") or "holyshits")))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/community/upload":
                    # 提交到可配置上传 URL（holyshits/feedback，默认关）——社区分享
                    try:
                        if not parent.community_upload_fn:
                            self._json({"ok": False, "error": "未提供上传功能"})
                        else:
                            self._json(parent.community_upload_fn(data))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/scoring/import":
                    # 从金句墙导出数据导入评分种子库
                    try:
                        if not parent.scoring_import_fn:
                            self._json({"ok": False, "error": "未提供评分导入"})
                        else:
                            self._json(parent.scoring_import_fn(str(data.get("text") or "")))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/scoring/stats":
                    try:
                        from agent.scoring import stats as _s
                        self._json({"ok": True, "data": _s()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/learning/start":
                    # 确定学习：仅默认角色卡（小鲸鱼）可用（AI 本体学完不 OOC；其他角色卡不应用机器学习）
                    try:
                        from agent.config import get_config as _gc, save_config as _sc
                        _c = _gc()
                        # 默认卡判定：未自定义角色文本 / 名称是内置小鲸鱼
                        _role = str(_c.get("persona", {}).get("role_text") or "").strip()
                        _name = str(_c.get("persona", {}).get("bot_name") or "").strip()
                        if _role or (_name and "小鲸鱼" not in _name and "小鲷鱼" not in _name):
                            self._json({"ok": False,
                                        "error": "机器学习（金句素材库训练/学习评估）仅默认角色卡（小鲸鱼）可用——其他角色卡不应用机器学习，防止 OOC；联网收集/模型补足不受限"})
                            return
                        _c.setdefault("scoring", {})["enabled"] = True; _sc(_c)
                        self._json({"ok": True, "note": "✅ 机器学习已开启：之后每条发言，群友24h内热烈回应(接话/追问/@)会为该话术加分，冷场降权；会话越久越贴合。评分引擎已在工作。（默认角色卡·小鲸鱼）"})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/learning/evaluate":
                    # 学习评估：按评分细则让模型评「学习到的反响话术」的质量（五维+相对未学习的提升幅度）
                    try:
                        from agent import llm
                        from agent.scoring import top_reactions
                        rxns = top_reactions(14)
                        # 只取"机器人自己发的、群友反响好"的话术；剔除系统/平台侧文本（如"撤回/一拍/xx加入了"这类非机器人发言）
                        import re as _re
                        _JUNK = ("撤回", "拍一拍", "拍拍", "加入了", "邀请", "退出了", "对方撤回", "你撤回", "以上是", "语音", "图片", "[表情]")
                        rxns = [r for r in rxns if str(r.get("text") or "").strip() and not any(j in str(r.get("text") or "") for j in _JUNK)]
                        if rxns:
                            lines = ["下面是我【机器人自己发的】、且群友反响好的话术（含热度分，供评估学习效果）："]
                            for r in rxns:
                                lines.append("- “%s”（热度 %.2f)" % (str(r.get("text") or "")[:60], float(r.get("score") or 0)))
                            sample = "\n".join(lines)
                        else:
                            sample = "（当前没有可评估的机器人高反应发言——请让机器人多聊、等群友有热烈回应后评分引擎再积累。）"
                        RULES = """【机器学习效果评分细则】（每维 0~100.00 精确到百分位，总分=8 维加权均值，保留 2 位小数）
务必只针对上面【机器人自己发的】话术评分，绝不把系统提示/用户消息当机器人发言。
维度：
1 自然度(12%)：像真人口吻，无AI腔/总结腔；
2 有趣度(16%)：有梗、机灵、让人想接；
3 人设贴合(20%)：是不是该角色会说的话（绝不换魂）；
4 机敏度(12%)：接话时机、回球、处理冷场/被调侃；
5 生活气息(12%)：是不是有"真人日常"的味道，而非机械应答；
6 观察力(10%)：有没有抓住群里细节/梗/前后文；
7 节奏感(10%)：长短句、停顿、分条像不像真人打字；
8 口语真实(8%)：用词口语化、不书面、不列点。
【禁止】不许给整分/整五/整十（如 80.00/85.00/90.00 一律不得出现）——每个维度必须按真实感受给出带小数的分数（如 84.37、79.15、91.03），百分位不得为 0。
输出格式（务必）：
各维分：自然=X.XX 有趣=X.XX 人设=X.XX 机敏=X.XX 生活=X.XX 观察=X.XX 节奏=X.XX 口语=X.XX
总分：XX.XX
相比未学习前的对话质量提升幅度：XX.X%
一句话点评：……（并指出哪一维进步最明显）
"""
                        sys = [{"role": "system", "content": RULES}, {"role": "user", "content": sample}]
                        r = llm.chat_completion(sys, temperature=0.2)
                        out = (r.get("message") or {}).get("content", "")
                        # 校验：若所有分都是整分（百分位全 0），强制重试一次并警告
                        import re as _re2
                        nums = _re2.findall(r"=(\d+\.\d{2})", out)
                        if nums and all(n.endswith(".00") or n.endswith(".50") for n in nums):
                            sys2 = sys + [{"role": "assistant", "content": out},
                                          {"role": "user", "content": "你上面的分数全是整分/半整分，违反细则。请重新按真实细微差异打分，每维必须带非零百分位（如 84.37），禁止 80.00/85.00 之类的整分。"}]
                            r2 = llm.chat_completion(sys2, temperature=0.3)
                            out = (r2.get("message") or {}).get("content", "") or out
                        self._json({"ok": True, "eval": out,
                                    "note": "已按8维细则(model评分)评估，分数精确到百分位（禁止整分）；仅评机器人发言"})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/pause":
                    parent.pause_fn()
                    self._json({"ok": True})
                elif path == "/api/resume":
                    parent.resume_fn()
                    self._json({"ok": True})
                elif path == "/api/shutdown":
                    self._json({"ok": True, "note": "正在停止机器人…"})
                    # 立即强退（handler 线程里 os._exit 杀全进程 + taskkill 自己兜底）——不依赖 Timer/shutdown_fn 线程，
                    # 之前 os._exit 放 Timer 线程里偶尔没杀干净，导致"停止关不掉"。
                    try:
                        parent.shutdown_fn()   # 写 stopped.flag + 杀看门狗 + os._exit(0)
                    except Exception:
                        pass
                    try:
                        import os as _o, subprocess
                        subprocess.run(["taskkill", "/F", "/PID", str(_o.getpid())],
                                       capture_output=True, creationflags=0x08000000)
                    except Exception:
                        pass
                elif path == "/api/restart":
                    # 重启：后台无窗口拉起新实例（释放端口后接替），当前实例退出
                    self._json({"ok": True, "note": "正在后台重启机器人…"})
                    threading.Timer(0.5, parent.restart_fn).start()
                elif path == "/api/persona/behavior-recommend":
                    # 角色卡行为推荐（POST {text?，默认当前 persona.role_text 或内置卡}）：
                    # ① 本地启发式先给一版；② 模型按多维度严肃规则复核（消费少量 token，可传 llm=false 关闭）
                    try:
                        from agent.behavior_recommend import recommend as _br
                        _txt = str(data.get("text") or "")
                        if not _txt.strip():
                            _txt = str(get_config().get("persona", {}).get("role_text") or "")
                            if not _txt.strip():
                                from agent.persona import PERSONAS
                                _txt = str((PERSONAS.get(get_config().get("persona", {}).get("prefer_key", "xiaojingyu")) or {}).get("text") or "")
                        local = _br(_txt)
                        res = dict(local)
                        res["via"] = "local"
                        if data.get("llm", True) and _txt.strip():
                            try:
                                from agent import llm
                                _rules = (
                                    "你是行为风格评估员。根据角色卡文本，评出机器人作为群友的行为档（只依据角色本体，禁止编造）。\n"
                                    "输出严格 JSON：{\"participation\":\"low|medium|high\",\"sticker\":0-3,\"reason\":\"一句话说明\"}\n"
                                    "维度：participation=参与度（low 安静旁观/medium 普通群友/high 话多活跃）；"
                                    "sticker=表情包接受度（0 不爱发/1 偶尔/2 较多/3 爱好者）；"
                                    "规则：说话极简/高冷/庄重类必为 low~medium 且 sticker≤1；话痨/气氛组/爱玩梗类为 high 且 sticker≥2；"
                                    "每维必须给出确定值，不得写不确定。只输出 JSON。\n\n角色卡：\n" + _txt[:2400]
                                )
                                _r = llm.chat_completion([{"role": "user", "content": _rules}], temperature=0.1)
                                _c = str((_r.get("message") or {}).get("content", ""))
                                import re as _re3, json as _json3
                                _m = _re3.search(r"\{.*\}", _c, _re3.S)
                                if _m:
                                    _d = _json3.loads(_m.group(0))
                                    _p = str(_d.get("participation") or "")
                                    if _p in ("low", "medium", "high"):
                                        res["participation"] = _p
                                        res["via"] = "llm"
                                    _sv = str(_d.get("sticker"))
                                    if _sv.strip() in ("0", "1", "2", "3"):
                                        res["sticker"] = int(_sv)
                                    res["reason"] = str(_d.get("reason") or "")
                            except Exception:
                                pass
                        self._json(res)
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/ui-test":
                    # 程序鼠标检验（POST {kind}：程序直接操控鼠标执行对应操作；data 透传给检验函数）
                    try:
                        self._json(parent.ui_test_fn(str(data.get("kind") or ""), data))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/ui-test/stop":
                    # 单项鼠标检验「停止」（POST）
                    try:
                        self._json(parent.ui_stop_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/sessions/delete":
                    # ⑨ 勾选删除运行明细：按日期删除 data/sessions/YYYY-MM-DD.jsonl（POST {dates:[...]}）
                    try:
                        import re as _re2
                        _dates = [str(d) for d in (data.get("dates") or []) if _re2.match(r"^\d{4}-\d{2}-\d{2}$", str(d))]
                        if not _dates:
                            return self._json({"ok": False, "error": "没有有效的日期"})
                        _sd = os.path.join(parent._data_path("sessions"))
                        _deleted = []
                        for _d in _dates:
                            _f = os.path.join(_sd, _d + ".jsonl")
                            if os.path.exists(_f):
                                os.remove(_f); _deleted.append(_d)
                        self._json({"ok": True, "note": "已删除 %d 个日期的运行明细" % len(_deleted), "deleted": _deleted})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/stats/cal_clear":
                    # ⑨ 一键清空全部计费历史（按天文件真实删除 + 清零累计/周期/今日）
                    try:
                        import glob as _gl
                        _sd = parent._data_path("sessions")
                        _n = 0
                        for _f in _gl.glob(os.path.join(_sd, "????-??-??.jsonl")):
                            try:
                                os.remove(_f); _n += 1
                            except Exception:
                                pass
                        _p = parent._data_path("usage_stats.json")
                        if os.path.exists(_p):
                            import json as _j2
                            with open(_p, "r", encoding="utf-8") as f:
                                _us = _j2.load(f)
                            _us["history"] = []
                            _us["day"] = {"sessions": 0, "calls": 0, "tokens": 0, "sent": 0, "cost": 0.0}
                            _us["period"] = {"sessions": 0, "calls": 0, "tokens": 0, "sent": 0, "cost": 0.0}
                            _us["total"] = {"sessions": 0, "calls": 0, "tokens": 0, "sent": 0, "cost": 0.0}
                            with open(_p, "w", encoding="utf-8") as f:
                                _j2.dump(_us, f, ensure_ascii=False, indent=1)
                        self._json({"ok": True, "note": "已清空计费历史（%d 天记录全部删除，累计归零）" % _n})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/stats/cal_list":
                    # 勾选删除弹窗：列出全部计费日志——按天聚合自 data/sessions/YYYY-MM-DD.jsonl（精确到年月日）
                    try:
                        import json as _j2, glob as _gl
                        _sd = parent._data_path("sessions")
                        _days = {}
                        for _f in _gl.glob(os.path.join(_sd, "????-??-??.jsonl")):
                            _day = os.path.basename(_f)[:10]
                            if not re.match(r"^\d{4}-\d{2}-\d{2}$", _day):
                                continue
                            _d = _days.setdefault(_day, {"tokens": 0, "cost": 0.0, "calls": 0,
                                                         "sessions": 0, "sent": 0})
                            try:
                                with open(_f, encoding="utf-8") as fh:
                                    for _ln in fh:
                                        _ln = _ln.strip()
                                        if not _ln:
                                            continue
                                        try:
                                            _o = _j2.loads(_ln)
                                            _d["tokens"] += int(_o.get("tokens") or 0)
                                            _d["cost"] += float(_o.get("cost") or 0)
                                            _d["calls"] += int(_o.get("calls") or 0)
                                            _d["sessions"] += 1
                                            if _o.get("reply"):
                                                _d["sent"] += 1
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                        # 同日期内多文件合并（罕见）
                        _out = []
                        for _day, _d in sorted(_days.items(), reverse=True):
                            for k in ("tokens", "calls", "sessions", "sent"):
                                _d[k] = int(_d[k])
                            _d["cost"] = round(float(_d["cost"]), 4)
                            _out.append({"day": _day, **_d})
                        # 旧版 period 归档（history 的 start/end 字段）也并入
                        _p = parent._data_path("usage_stats.json")
                        if os.path.exists(_p):
                            with open(_p, "r", encoding="utf-8") as f:
                                _us = _j2.load(f)
                            for h in _us.get("history") or []:
                                if not isinstance(h, dict):
                                    continue
                                day = str(h.get("day") or h.get("start") or "")[:10]
                                if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
                                    continue
                                if any(x["day"] == day for x in _out):
                                    continue   # 已有按天记录
                                _out.append({"day": day, "tokens": int(h.get("tokens") or 0),
                                             "cost": round(float(h.get("cost") or 0), 4),
                                             "calls": int(h.get("calls") or 0),
                                             "sessions": int(h.get("sessions") or 0),
                                             "sent": int(h.get("sent") or 0)})
                        _out.sort(key=lambda x: x["day"], reverse=True)
                        self._json({"ok": True, "bills": _out})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/stats/cal_delete":
                    # 勾选删除：按天删除计费日志（POST {days:[...]}）——真实删除当天文件 + 同步回调累计/周期/今日
                    try:
                        import json as _j2, glob as _gl
                        _days = set(str(d) for d in (data.get("days") or [])
                                    if re.match(r"^\d{4}-\d{2}-\d{2}$", str(d)))
                        if not _days:
                            return self._json({"ok": False, "error": "没有有效的日期"})
                        _sd = parent._data_path("sessions")
                        _gone = []
                        _del_days = {}     # day -> sums（删除前算好，用于回补累计）
                        for _f in _gl.glob(os.path.join(_sd, "????-??-??.jsonl")):
                            _day = os.path.basename(_f)[:10]
                            if _day not in _days:
                                continue
                            agg = {"tokens": 0, "cost": 0.0, "calls": 0, "sessions": 0, "sent": 0}
                            try:
                                with open(_f, encoding="utf-8") as fh:
                                    for _ln in fh:
                                        _ln = _ln.strip()
                                        if not _ln:
                                            continue
                                        try:
                                            _o = _j2.loads(_ln)
                                            agg["tokens"] += int(_o.get("tokens") or 0)
                                            agg["cost"] += float(_o.get("cost") or 0)
                                            agg["calls"] += int(_o.get("calls") or 0)
                                            agg["sessions"] += 1
                                            if _o.get("reply"):
                                                agg["sent"] += 1
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                            _del_days[_day] = agg
                            try:
                                os.remove(_f)
                                _gone.append(_day)
                            except Exception:
                                pass
                        if not _gone:
                            # 没有对应文件：尝试只清历史归档
                            _gone = list(_days)
                        # 回补 usage_stats：删除天对应的 累计/周期/今日
                        _p = parent._data_path("usage_stats.json")
                        if os.path.exists(_p):
                            with open(_p, "r", encoding="utf-8") as f:
                                _us = _j2.load(f)
                            _us["history"] = [h for h in (_us.get("history") or [])
                                              if not isinstance(h, dict)
                                              or str(h.get("day") or h.get("start") or "")[:10] not in _days]
                            _today = time.strftime("%Y-%m-%d")
                            for _k in ("total", "period"):
                                _t = _us.get(_k) or {}
                                for _day, agg in _del_days.items():
                                    _t["tokens"] = max(0, int(_t.get("tokens") or 0) - int(agg["tokens"]))
                                    _t["cost"] = max(0.0, float(_t.get("cost") or 0) - float(agg["cost"]))
                                    _t["calls"] = max(0, int(_t.get("calls") or 0) - int(agg["calls"]))
                                    _t["sessions"] = max(0, int(_t.get("sessions") or 0) - int(agg["sessions"]))
                                    _t["sent"] = max(0, int(_t.get("sent") or 0) - int(agg["sent"]))
                                _us[_k] = _t
                            if _today in _del_days:
                                _t = _us.get("day") or {}
                                agg = _del_days[_today]
                                _t["tokens"] = max(0, int(_t.get("tokens") or 0) - int(agg["tokens"]))
                                _t["cost"] = max(0.0, float(_t.get("cost") or 0) - float(agg["cost"]))
                                _t["calls"] = max(0, int(_t.get("calls") or 0) - int(agg["calls"]))
                                _t["sessions"] = max(0, int(_t.get("sessions") or 0) - int(agg["sessions"]))
                                _t["sent"] = max(0, int(_t.get("sent") or 0) - int(agg["sent"]))
                                _us["day"] = _t
                            with open(_p, "w", encoding="utf-8") as f:
                                _j2.dump(_us, f, ensure_ascii=False, indent=1)
                        self._json({"ok": True,
                                    "note": "已删除 %d 天的计费日志（%s）" % (len(_gone), ", ".join(sorted(_gone)[:12])),
                                    "removed": sorted(_gone)})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/stats/cal":
                    # 日历：读取某日会话明细（data/sessions/YYYY-MM-DD.jsonl 汇总）
                    try:
                        import json as _j
                        d = str(data.get("d") or "")
                        _sf = os.path.join(parent._data_path("sessions"), (d + ".jsonl"))
                        agg = {"date": d, "sessions": 0, "tokens": 0, "cost": 0.0, "calls": 0, "sent": 0}
                        if d and os.path.exists(_sf):
                            with open(_sf, encoding="utf-8") as fh:
                                for line in fh:
                                    line = line.strip()
                                    if not line:
                                        continue
                                    try:
                                        o = _j.loads(line)
                                        agg["sessions"] += 1
                                        agg["tokens"] += int(o.get("tokens") or 0)
                                        agg["cost"] += float(o.get("cost") or 0)
                                        agg["calls"] += int(o.get("calls") or 0)
                                        if o.get("reply"):
                                            agg["sent"] += 1
                                    except Exception:
                                        pass
                        self._json({"ok": True, **agg})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/emojis/delete":
                    # 删除一个收藏的表情文件（POST {name}）
                    try:
                        import urllib.parse as _up
                        name = _up.unquote(str(data.get("name") or ""))
                        emoji_dir = parent._data_path("emojis")
                        fp = os.path.join(emoji_dir, os.path.basename(name))
                        if os.path.exists(fp):
                            os.remove(fp)
                            self._json({"ok": True})
                        else:
                            self._json({"ok": False, "error": "文件不存在：" + name})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/code-check":
                    # 代码检测（POST；纯代码层检查，不接管鼠标）。改为后台线程跑，前端轮询进度。
                    try:
                        import threading
                        from agent.code_check import run as _code_run
                        def _bg():
                            try:
                                _code_run._res = _code_run(bool(data.get("deps")))
                            except Exception as e:
                                _code_run._res = {"ok": False, "checks": [], "summary": "代码检测失败：%s" % e}
                            _code_run._done = True
                        # 若上一次已彻底完成，则清掉旧结果以便重跑
                        if getattr(_code_run, "_done", False) or getattr(_code_run, "_res", None):
                            _code_run._done = False
                            _code_run._res = None
                        threading.Thread(target=_bg, daemon=True).start()
                        self._json({"ok": True, "started": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/code-check/progress":
                    # 代码检测进度（GET/POST）：{done, progress:{done,total,current}, items?, result?}
                    try:
                        from agent.code_check import run as _code_run
                        done = bool(getattr(_code_run, "_done", False))
                        self._json({"ok": True, "done": done,
                                    "progress": getattr(_code_run, "_prog", None),
                                    "items": list(getattr(_code_run, "_checks", None) or []) if not done else None,
                                    "result": getattr(_code_run, "_res", None) if done else None})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/persona/cats/save":
                    # 新建/更新分区（POST {name, desc?}）
                    try:
                        import json as _json
                        _p = os.path.join(parent._asset_root, "..", "data", "persona_cats.json")
                        try:
                            with open(_p, "r", encoding="utf-8") as f:
                                user_cats = _json.load(f)
                        except Exception:
                            user_cats = {}
                        name = str(data.get("name") or "").strip()
                        if not name:
                            self._json({"ok": False, "error": "分区名不能为空"})
                        else:
                            cur = user_cats.get(name, {}) or {}
                            cur["desc"] = str(data.get("desc") or cur.get("desc") or "")
                            user_cats[name] = cur
                            with open(_p, "w", encoding="utf-8") as f:
                                _json.dump(user_cats, f, ensure_ascii=False, indent=1)
                            self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/persona/cats/del":
                    # 删除用户分区（POST {name}）：分区下自定义卡"移回 📝 自定义"，分区移除
                    try:
                        import json as _json
                        cats_p = os.path.join(parent._asset_root, "..", "data", "persona_cats.json")
                        pers_p = os.path.join(parent._asset_root, "..", "data", "custom_personas.json")
                        name = str(data.get("name") or "").strip()
                        try:
                            with open(cats_p, "r", encoding="utf-8") as f:
                                user_cats = _json.load(f)
                        except Exception:
                            user_cats = {}
                        user_cats.pop(name, None)
                        with open(cats_p, "w", encoding="utf-8") as f:
                            _json.dump(user_cats, f, ensure_ascii=False, indent=1)
                        # 该分区下的卡移到默认
                        try:
                            with open(pers_p, "r", encoding="utf-8") as f:
                                items = _json.load(f)
                        except Exception:
                            items = {}
                        for k, v in items.items():
                            if (v or {}).get("cat") == name:
                                v["cat"] = "📝 自定义"
                        with open(pers_p, "w", encoding="utf-8") as f:
                            _json.dump(items, f, ensure_ascii=False, indent=1)
                        self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/persona/score":
                    # 自定义角色卡评分（POST {text, llm?}）：默认本地；llm=true 时交模型
                    try:
                        self._json(parent.persona_score_custom_fn(str(data.get("text") or ""),
                                                                  bool(data.get("llm"))))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/personas/custom":
                    # 自定义角色卡保存（POST {name, text, key?, cat?}；无 key=新建；cat=分区名）
                    try:
                        import json as _json
                        _p = os.path.join(parent._asset_root, "..", "data", "custom_personas.json")
                        items = {}
                        try:
                            with open(_p, "r", encoding="utf-8") as f:
                                items = _json.load(f)
                        except Exception:
                            pass
                        key = str(data.get("key") or "")
                        name = str(data.get("name") or "").strip()
                        text = str(data.get("text") or "").strip()
                        cat = str(data.get("cat") or "").strip() or "📝 自定义"
                        if not text:
                            self._json({"ok": False, "error": "角色文本不能为空"})
                        else:
                            if not key:
                                key = "custom_" + str(int(time.time()))
                            cur = items.get(key, {}) or {}
                            cur["name"] = name or cur.get("name") or "自定义"
                            if text:
                                cur["text"] = text
                            cur["cat"] = cat
                            items[key] = cur
                            with open(_p, "w", encoding="utf-8") as f:
                                _json.dump(items, f, ensure_ascii=False, indent=1)
                            self._json({"ok": True, "key": key})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/personas/custom/del":
                    # 删除自定义角色卡（POST {key}）
                    try:
                        import json as _json
                        _p = os.path.join(parent._asset_root, "..", "data", "custom_personas.json")
                        try:
                            with open(_p, "r", encoding="utf-8") as f:
                                items = _json.load(f)
                        except Exception:
                            items = {}
                        items.pop(str(data.get("key") or ""), None)
                        with open(_p, "w", encoding="utf-8") as f:
                            _json.dump(items, f, ensure_ascii=False, indent=1)
                        self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/memory/deep-profile":
                    # 群友深度印象：采集该成员全部历史真实记录 → 本地统计提炼（口癖/语气/行为），附真实原话
                    try:
                        from agent import memory as _mem
                        wxid = str((data or {}).get("user_id") or "").strip()
                        name = str((data or {}).get("name") or "").strip()
                        if not wxid and not name:
                            raise ValueError("缺少成员标识")
                        mm = _mem.MemoryStore()
                        texts = mm.collect_member_texts(wxid, name)
                        if not texts:
                            self._json({"ok": False, "note": "该成员暂无印象记录（机器人还没留意过 TA）；先让机器人在群里互动积累"})
                        else:
                            # 提炼（全部基于真实记录，不做虚构）
                            import collections as _co
                            import re as _re
                            emojis = _co.Counter()
                            for t in texts:
                                for ch in t:
                                    if ord(ch) > 0x2600:
                                        emojis[ch] += 1
                            top_emoji = [("%s×%d" % (e, c)) for e, c in emojis.most_common(5)]
                            lens = [len(t) for t in texts]
                            avg_len = int(sum(lens) / max(1, len(lens)))
                            ends = _co.Counter()
                            for t in texts:
                                tail = t.strip()[-2:] if len(t.strip()) >= 2 else t.strip()
                                if tail:
                                    ends[tail] += 1
                            top_ends = ["%s×%d" % (e, c) for e, c in ends.most_common(5)]
                            profile = ("【群友深度印象 · 由 %d 条真实记录自动整理（未做虚构）】\n"
                                        "高频符号/表情：%s\n平均句长：%d 字；常用结尾语气：%s\n"
                                        "真实原话示例（逐字保留）：\n%s" %
                                        (len(texts), "、".join(top_emoji) or "无", avg_len,
                                         "、".join(top_ends) or "—",
                                         "\n".join("· %s" % t[:80] for t in texts[:6])))
                            self._json({"ok": True, "count": len(texts), "text": profile, "note": "已整理；可点「追加为印象」写入记忆"})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/persona/ai-enrich":
                    try:
                        self._json(parent.persona_ai_enrich_fn(str(data.get("name") or ""),
                                                               str(data.get("text") or ""),
                                                               int(data.get("rounds") or 1)))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/persona/web-fetch":
                    # 联网收集角色真实资料（搜索其说过的话/做过的事；只返回搜索摘要，绝不编造）
                    try:
                        from . import web_search as _ws
                        name = str((data or {}).get("name") or "").strip()
                        if not name:
                            raise ValueError("请填写角色名")
                        results, notes = [], []
                        for q in (name + " 经典语录", name + " 访谈 原话", name + " 名言 金句"):
                            try:
                                r = _ws.web_search(q)
                                items = (r or {}).get("results") or (r or {}).get("items") or []
                                ans = (r or {}).get("answer")
                                if ans:
                                    notes.append(str(ans)[:300])
                                for it in items[:6]:
                                    results.append({
                                        "title": str(it.get("title") or "")[:120],
                                        "url": str(it.get("url") or ""),
                                        "snippet": str(it.get("snippet") or "")[:300]})
                            except Exception as e:
                                notes.append("查询「%s」失败：%s" % (q[:16], str(e)[:80]))
                        quotes = []
                        seen = set()
                        for it in results:
                            for seg in (it["snippet"] + " " + it["title"]).split("。"):
                                seg = seg.strip(" \n\t·")
                                if not seg or len(seg) < 4 or len(seg) > 200:
                                    continue
                                if any(c in seg for c in ("“", "”", "「", "」", "\"", "\u201c", "\u201d")) and seg not in seen:
                                    seen.add(seg)
                                    quotes.append(seg)
                        if not results:
                            self._json({"ok": False, "name": name,
                                        "note": "未检索到第一手资料（搜索引擎无相关真实资料）；请人工核实后再填入，切勿编造"})
                        else:
                            self._json({"ok": True, "name": name, "results": results[:20],
                                        "quotes": quotes[:10], "notes": notes[:5],
                                        "note": "以上均为搜索引擎返回的真实资料摘要（含来源链接，未做任何编造）；请人工核对后提取进角色卡。"})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/ui/background":
                    # 自定义背景图（POST {data: base64(dataURL)}, 或 {clear:true}）
                    try:
                        import base64 as _b64
                        _p = parent._data_path("ui_bg.jpg")
                        if data.get("clear"):
                            if os.path.exists(_p):
                                os.remove(_p)
                            try:
                                from agent.config import get_config as _gc, save_config as _sc
                                _c = _gc(); _c.setdefault("ui", {})["background"] = ""; _sc(_c)
                            except Exception:
                                pass
                            self._json({"ok": True, "note": "已恢复默认背景"})
                        else:
                            raw = str(data.get("data") or "")
                            if "base64," in raw[:60]:
                                raw = raw.split("base64,", 1)[1]
                            raw = re.sub(r"[\s\r\n]", "", raw)       # 清洗空白
                            raw += "=" * (-len(raw) % 4)             # padding 补全
                            try:
                                img_bytes = _b64.b64decode(raw, validate=False)
                            except Exception:
                                raise ValueError("base64 解码失败（文件数据损坏？请重试）")
                            # 格式探测（图片 + 视频全兼容：png/jpg/jpeg/webp/gif/mp4/webm/ogg）
                            import io as _io2
                            head = img_bytes[:64]
                            mime = ""
                            if head[:8] == b"\x89PNG\r\n\x1a\n":
                                mime, ext, is_video = "image/png", "png", False
                            elif head[:3] == b"\xff\xd8\xff":
                                mime, ext, is_video = "image/jpeg", "jpg", False
                            elif head[:12] == b"RIFF" and head[8:12] == b"WEBP":
                                mime, ext, is_video = "image/webp", "webp", False
                            elif head[:6] in (b"GIF87a", b"GIF89a"):
                                mime, ext, is_video = "image/gif", "gif", False
                            elif head[4:12] == b"ftypmp4" or head[4:12] == b"ftypisom" or b"ftyp" in head[4:12]:
                                mime, ext, is_video = "video/mp4", "mp4", True
                            elif head[:4] == b"\x1aE\xdf\xa3":
                                mime, ext, is_video = "video/webm", "webm", True
                            elif head[:4] == b"OggS":
                                mime, ext, is_video = "video/ogg", "ogg", True
                            else:
                                raise ValueError("格式无法识别：图片支持 PNG/JPEG/WEBP/GIF；视频支持 MP4/WEBM/OGG")
                            _p2 = parent._data_path("ui_bg." + ext)
                            with open(_p2, "wb") as _fw:
                                _fw.write(img_bytes)
                            # 清掉旧的其他扩展（避免残留）
                            for _old in ("jpg", "png", "webp", "gif", "mp4", "webm", "ogg"):
                                if _old != ext:
                                    try:
                                        _oo = parent._data_path("ui_bg." + _old)
                                        if os.path.exists(_oo):
                                            os.remove(_oo)
                                    except Exception:
                                        pass
                            try:
                                from agent.config import get_config as _gc, save_config as _sc
                                _c = _gc(); _c.setdefault("ui", {})["background"] = "custom"; _c.setdefault("ui", {})["bg_type"] = "video" if is_video else "image"; _sc(_c)
                            except Exception:
                                pass
                            self._json({"ok": True, "note": "背景已保存并应用"})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e).split("\n")[0][:140] or "图片处理失败"})
                elif path == "/api/personas/favs":
                    # 人设星标集合（GET）
                    try:
                        import json as _json
                        try:
                            with open(parent._data_path("persona_favs.json"), "r", encoding="utf-8") as f:
                                favs = _json.load(f)
                        except Exception:
                            favs = {}
                        self._json({"ok": True, "favs": favs})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/personas/fav":
                    # 设/取消星标（POST {key, fav}）
                    try:
                        import json as _json
                        try:
                            with open(parent._data_path("persona_favs.json"), "r", encoding="utf-8") as f:
                                favs = _json.load(f)
                        except Exception:
                            favs = {}
                        k = str(data.get("key") or "")
                        if data.get("fav"):
                            favs[k] = 1
                        else:
                            favs.pop(k, None)
                        with open(parent._data_path("persona_favs.json"), "w", encoding="utf-8") as f:
                            _json.dump(favs, f, ensure_ascii=False, indent=1)
                        self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/personas/rate":
                    # 用户为角色打分（POST {key, score, note?}，落盘）
                    try:
                        self._json(parent.persona_rate_fn(str(data.get("key") or ""),
                                                          data.get("score"),
                                                          str(data.get("note") or "")))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/selfcheck-stop":
                    # 停止当前一键体检（POST；设置取消标志，体检循环下一步即退出）
                    try:
                        parent.selfcheck_stop_fn()
                        self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/ui/recalibrate":
                    # 重新标定微信 UI 图标库（POST；接管鼠标瞬间，需微信在前台）
                    try:
                        self._json(parent.recalibrate_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/open-path":
                    # 打开导出文件所在位置（POST {path}）
                    try:
                        self._json(parent.open_path_fn(str(data.get("path") or "")))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
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
