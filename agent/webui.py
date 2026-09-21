# -*- coding: utf-8 -*-
"""Web 控制台：在浏览器里改设置、看状态、看日志、测试 API（移植自 qq-agent 的控制台思路）。

零第三方依赖，纯标准库 http.server，单文件 HTML（内联 CSS/JS，无框架）。
只监听本机回环地址，含可选访问口令。
"""
from __future__ import annotations

import hmac as _hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import persist        # V-R10-26：原子写（唯一临时名 + fsync + os.replace）
from .config import as_bool, deep_merge, get_config, save_config, set_config
from . import local_guard                # V-R3-8：回环 Host 校验（与本地生图服务共用同一份实现）
from .whale_text import DICT as WHALE_DICT, SKIP as WHALE_SKIP
from .console_html import HTML  # 界面模板（蓝白设计，设置项全量，独立文件便于改版）
from .util import mask_secret, redact_secrets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _truthy(v):
    """把「开关」读成布尔：字符串 `"false" / "0" / "off" / "no"` 都是假；**`None` 保持 `None`**。

    ⛔ 2026-09-21（第四轮审计 V-R4-15 / V-R4-13）：`bool("false")` 是 **True** ⇒ 前端哪怕老实传
    `"false"`，开关也会被**反向打开**；GET 路由更严重（查询串里一切都是字符串）。
    ⚠️ **一处实现**：真值表在 `config.as_bool()`，这里只多一层"保留 None"（"没传"要和"传了假"分开）。
    """
    if v is None:
        return None
    return as_bool(v)


# ── 数据迁移包（导出/导入：计费+对话记录 data/sessions/*.jsonl）────────────

def cal_date_ok(d) -> bool:
    """`/api/stats/cal` 的日期闸（第六轮 **V-R6-26c**）：只收严格 `YYYY-MM-DD`；空串＝「今天」，放行。

    抽成模块级**纯函数**是为了能被行为判据直接调 —— 原来这段内联在 POST 处理里，
    判据只能 grep 源码文本 ⇒ 分支被写死也看不出来（第七轮 V-R7-3 的要求）。
    """
    s = str(d or "")
    return (not s) or bool(re.match(r"^\d{4}-\d{2}-\d{2}$", s))


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
            {"app": "Persona Morph", "v": 1, "files": files}, ensure_ascii=False))
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


# ── 凭据字段表（**掩码侧与恢复侧共用同一份**）─────────────────────────────────────────
# 为什么抽成表而不是逐字段手写（V-R3-3，2026-09-20）：脱敏原来是**手写枚举**——V8 修完
# `webhook_token` 就收工，紧接着 `feedback.webhook_url` 又漏了（同一个函数、同一条威胁模型，
# 只是它"看起来只是个网址"）。⇒ 现在 `masked_config()`（打码）与 `_protect_secrets()`（恢复）
# **都遍历这张表**：加一个字段只加一处，两个方向一起生效，**不存在"只改了一侧"**。
#   kind: "secret" = 纯密钥（`mask_secret` 首尾截断）/ "url" = URL 本体即凭据（掩掉里面的凭据参数）
# ⚠️ 故意不在表里的：`server.token`（用户自己的控制台钥匙：掩了面板上就再也看不到它，且废掉
#   「显示」按钮；而它本来就写在 logs/console.url 与启动日志里 ⇒ 掩它不改变任何实际暴露面）。
CRED_FIELDS = (
    (("api", "api_key"), "secret"),
    (("cloud", "token"), "secret"),
    (("feedback", "webhook_token"), "secret"),
    # ⭐ V-R3-3 本体：`agent/feedback.py:498-502` 原话「群机器人这条国内可达、**URL 即凭据**」
    #   ⇒ 它比 webhook_token 更隐蔽（看着只是个"网址"，其实 `?key=xxx` 就是那把钥匙）。
    (("feedback", "webhook_url"), "url"),
    (("feedback", "smtp", "password"), "secret"),
)
# 只看"像凭据"的参数名：颜色/尺寸之类无害参数不该被一起掩掉（否则用户认不出是哪条通道）
_CRED_Q_RE = re.compile(
    r"(?i)([a-z0-9_]*?(?:key|token|secret|passwo?r?d|signature|sign|webhook)[a-z0-9_]*)=([^&#\s]+)")


def mask_url_credential(url: str) -> str:
    """URL 本体的脱敏：**凭据参数的值**打码（保留参数名，便于认出是哪条通道）。

    ⛔ 关键约定：**绝不原样返回**（URL 即凭据）。一个凭据参数都没识别出来时退回"整体首尾截断"
    —— 打码串里一定含 `••••`，于是恢复侧 `_is_masked()` 认得它、用户保存时真值不会被覆盖。
    """
    s = str(url or "")
    if not s:
        return s
    out = _CRED_Q_RE.sub(lambda m: m.group(1) + "=" + mask_secret(m.group(2)), s)
    if out != s:
        return out
    _head, _sep, _tail = s.partition("://")
    return (_head + _sep + mask_secret(_tail)) if _sep else mask_secret(s)


def _mask_path(cfg: dict, path: tuple, kind: str) -> None:
    """按路径把配置里某个字段打码；路径中间不存在就跳过，**顺路把容器复制出来**（不动真配置）。"""
    cur = cfg
    for k in path[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            return
        nxt = dict(nxt)
        cur[k] = nxt
        cur = nxt
    leaf = path[-1]
    if cur.get(leaf):
        cur[leaf] = mask_url_credential(cur[leaf]) if kind == "url" else mask_secret(cur[leaf])


def _restore_path(new_cfg: dict, old: dict, path: tuple) -> None:
    """`new_cfg` 里 path 处的值若是打码串 ⇒ 换回 `old` 里的真值（路径中间不存在就跳过，不建结构）。"""
    ncur, ocur = new_cfg, old
    for k in path[:-1]:
        ncur = ncur.get(k) if isinstance(ncur, dict) else None
        ocur = ocur.get(k) if isinstance(ocur, dict) else None
        if not isinstance(ncur, dict):
            return
    leaf = path[-1]
    if isinstance(ncur, dict) and _is_masked(ncur.get(leaf)):
        ncur[leaf] = (ocur.get(leaf) if isinstance(ocur, dict) else None) or ""


def _protect_secrets(new_cfg: dict):
    """保存配置时：**表里任何"还是打码值"的字段都不覆盖真实值**（与 `masked_config` 同一张表）。

    为什么要同一张表（V-R3-3）：脱敏侧补了字段而恢复侧忘了补，用户一点保存就把真值写成 `••••`
    串（原值当场丢）。两侧遍历同一份 `CRED_FIELDS` ⇒ "只改一侧"这类错**不可能**再发生。
    """
    old = get_config()
    api_new = new_cfg.get("api")
    if isinstance(api_new, dict):
        # `provider_keys` 是"名字→密钥"的字典，进不了路径表 ⇒ 单独按同一口径处理（secret）
        pk_new = api_new.get("provider_keys")
        if isinstance(pk_new, dict):
            api_old = old.get("api") or {}
            pk_old = (api_old.get("provider_keys") or {}) if isinstance(api_old, dict) else {}
            for k, v in pk_new.items():
                if _is_masked(v):
                    pk_new[k] = pk_old.get(k) or ""
    for path, _kind in CRED_FIELDS:
        _restore_path(new_cfg, old, path)


def _current_wx(parent):
    """运行中的微信实例（按既有口径找：谁有 `self_identity` 就是它）；找不到返回 None。"""
    try:
        for _n in ("wechat", "adapter", "wx", "wxadapter", "bot", "worker"):
            _o = getattr(parent, _n, None)
            if _o is not None and hasattr(_o, "self_identity"):
                return _o
    except Exception:
        return None
    return None


def _current_dir_how(parent) -> dict:
    """运行中的微信实例手里的 `_db_how` —— **"实际在用哪个目录"的铁证**（不是配置值）。"""
    try:
        _o = _current_wx(parent)
        if _o is not None:
            return dict(getattr(_o, "_db_how", None) or {})
    except Exception:
        return {}
    return {}


def _wechat_dir_conflict(new_cfg: dict) -> dict:
    """POST `/api/config` 里改动了「微信数据目录」时的**校验闸**：不过关就不写盘。

    只在**这一项真的变了**的时候拦 —— 改别的设置时，那条旧值不该把人挡在门外（旧值不可用
    由运行期回落 + 报告/控制台如实报出来兜）。不过关时把"原因 + 现在实际会回落到哪"一起给出。

    用户反馈（2026-09-18 22:23）：「自己自定义的地址他检测不到」⇒ 手动指定必须**校验**
    （存在 + 里面有 db_storage 或消息库文件），否则明确报错并回落自动检测，绝不静默写进去。
    """
    try:
        old = str(((get_config() or {}).get("wechat") or {}).get("db_dir") or "")
        new = str(((new_cfg or {}).get("wechat") or {}).get("db_dir") or "")
    except Exception:
        return {}
    if new.strip() == old.strip():
        return {}
    from . import wechat_dir as _wdir
    p = _wdir.expand(new)
    if not p:
        return {}                       # 清空＝回到自动检测，永远合法
    c = _wdir.check(p)
    if c.get("ok"):
        return {}
    d = _wdir.decide(p)
    return {"ok": False, "saved": False,
            "error": "这个目录用不了：%s" % (c.get("why") or "用不了"),
            "reason": c.get("why") or "",
            "fallback": d.get("effective") or "",
            "wechat_dir": _wdir.status(explicit=old)}


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
        T = WHALE_DICT
        MARK = "鲸语版界面文案"
        idx = html.find(MARK)
        head, tail = (html[:idx], html[idx:]) if idx >= 0 else (html, "")
        # ⛔ 只换"整个文本节点"（`>文案<`），**不做子串替换**（2026-09-14 重写）：
        #    旧版是全局 replace —— 短键会吃掉长键（「停止」吞「停止检测」），还会改坏 JS 里的字符串；
        #    整节点匹配后，字典可以放心扩到几百条（行标签/按钮/表头全覆盖）。
        #    看起来不像文案的片段（含 {}()=; 或过长）一律跳过。
        import re as _re
        try:
            from .whale_text import NAV as _NAV
        except Exception:
            _NAV = {}
        # ① 左导航单独走短表（252px 窄列，长文案会被省略号吃掉）——必须在总替换之前做
        def _nav(m):
            v = _NAV.get(m.group(1).strip())
            return '<span class="lb">' + v + '</span>' if v else m.group(0)
        head = _re.sub(r'<span class="lb">([^<]*)</span>', _nav, head)
        # ② 其余可见文案：只换"整个文本节点"
        pat = _re.compile(r">([^<>]+)<")

        def _swap(m):
            raw = m.group(1)
            key = raw.strip()
            if not key or len(key) > 40 or any(c in key for c in "{}()=;<>\n"):
                return m.group(0)
            val = T.get(key)
            if not val:
                return m.group(0)
            lead = raw[:len(raw) - len(raw.lstrip())]
            trail = raw[len(raw.rstrip()):]
            return ">" + lead + val + trail + "<"

        return pat.sub(_swap, head) + tail

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
                 recalibrate_fn=None, open_path_fn=None,
                 selfcheck_stop_fn=None,
                 persona_scores_fn=None, persona_rate_fn=None,
                 persona_score_custom_fn=None, persona_ai_enrich_fn=None,
                 community_export_fn=None, community_upload_fn=None, scoring_import_fn=None,
                 watermark_reset_fn=None,
                 store=None):
        self.status_provider = status_provider      # () -> dict
        self.log_buffer = log_buffer                # collections.deque[str]
        self.test_api_fn = test_api_fn              # () -> dict
        self.on_save = on_save                      # (new_cfg) -> None（可选，用于通知运行中组件）
        self.watermark_reset_fn = watermark_reset_fn or (lambda: {"ok": False, "error": "未提供"})
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
        self.store = store                                   # ChatStore（第 10 条：按会话/按条屏蔽存档）
        self.emojis_fn = emojis_fn or (lambda: [])            # () -> list（表情包收藏夹）
        self.recalibrate_fn = recalibrate_fn or (lambda: {"ok": False, "error": "未提供"})
        self.open_path_fn = open_path_fn or (lambda path: {"ok": False, "error": "未提供"})
        self.selfcheck_stop_fn = selfcheck_stop_fn or (lambda: None)
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
        elif path in ("/dsh-whale/bubble.json", "/dsh-whale/audio.json"):
            # 上游 0.3.x 新增：泡泡 / 音频配置（纯配置，本移植版能落地 ⇒ 真存真读）
            try:
                handler._json(whale.cfg_payload(os.path.basename(path)))
            except Exception as e:
                handler._json({"ok": False, "error": str(e)[:200]})
        elif path in tuple("/dsh-whale/" + n for n in whale._UNSUPPORTED):
            # 上游 0.3.x 有、本移植版没有的：**如实说不支持**（客户端会保留默认值 ⇒ 干净降级）
            handler._json(whale.unsupported(path.rsplit("/", 1)[-1]))
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
        """返回配置副本：**凭据字段表 `CRED_FIELDS` 里的每一项都打码**（真实值只存服务器 config.json）。

        ⭐ 2026-09-20 改成**表驱动**（V-R3-3）：原先逐字段手写 —— 2026-09-16 掩了
        `feedback.smtp.password` 与 `cloud.token`、2026-09-20 掩了 `feedback.webhook_token`，
        可「原始 JSON」按钮（打的正是 `GET /api/config`）仍然把 `feedback.webhook_url`
        （`feedback.py` 自己写着"URL 即凭据"）明文铺在网页上，录屏/截图即外泄。
        ⇒ 掩码侧与恢复侧现在遍历**同一份表**，加字段只加一处。
        """
        cfg = dict(get_config())
        api = dict(cfg.get("api") or {})
        # provider_keys 是"名字→密钥"的字典，进不了路径表 ⇒ 单独处理（口径同表里的 "secret"）
        pk = api.get("provider_keys")
        if isinstance(pk, dict):
            api["provider_keys"] = {k: mask_secret(v) for k, v in pk.items() if v}
        cfg["api"] = api
        for path, kind in CRED_FIELDS:
            _mask_path(cfg, path, kind)
        return cfg

    def _safe_join(self, base: str, raw: str) -> str:
        """把 URL 里给的名字安全拼到 base 下：**先 unquote、再只取 basename**，最后用 realpath
        断言结果确实落在 base 内；任何一步可疑就返回空串（调用方一律 404）。

        ⚠️ 2026-09-20 修 **V1（P0 免认证路径穿越）**：原来是 `os.path.basename(path)` **之后**才
        `unquote()` —— 此时 `%2F` 还不是分隔符，basename 原样返回 `..%2F..%2Fconfig.json`；随后
        unquote 把 `%2F` 还原成 `/`，`..` 就生效了 ⇒ **不带口令**就能 GET
        `/assets/emoji/..%2F..%2Fconfig.json` 读走含 `api.api_key` 与控制台口令的 config.json
        （实测 200 / 26262 字节）。顺序反过来 + realpath 勾边，两头都堵。
        """
        try:
            import urllib.parse as _up
            nm = os.path.basename(_up.unquote(str(raw or "")).replace("\\", "/"))
            if not nm or nm in (".", "..") or "/" in nm or "\\" in nm or ":" in nm:
                return ""
            b = os.path.realpath(base)
            fp = os.path.realpath(os.path.join(b, nm))
            if fp != b and not fp.startswith(b + os.sep):
                return ""
            return fp
        except Exception:
            return ""

    def _serve_wallpaper(self, path: str, handler, query):
        """视频壁纸（免认证，支持 Range 分段）：assets/wallpaper/<file>，供 <video> 背景流式播放。"""
        try:
            wdir = os.path.join(self._asset_root, "wallpaper")
            fp = self._safe_join(wdir, path)
            if not fp or not os.path.exists(fp):
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
                # ⚠️ V1：这里原来是 `unquote(basename)`（顺序反了）⇒ 可穿越到仓库根读 config.json。
                # 现在统一走 `_safe_join`（先 unquote 再 basename + realpath 勾边），并且只认
                # **表情缓存那种文件名**（md5 十六进制 + 图片后缀），别的一律 404。
                import re as _re
                emoji_dir = self._data_path("emojis")
                fp = self._safe_join(emoji_dir, path)
                if not fp or not _re.match(r"^[0-9a-zA-Z_-]{6,}\.(jpg|jpeg|png|gif|webp|bmp)$",
                                           os.path.basename(fp), _re.I):
                    raise FileNotFoundError(path)
                with open(fp, "rb") as f:
                    body = f.read()
            elif name == "icon.png":
                body = self._icon_bytes
            elif name == "DSniang1.png":
                body = (self.whale.asset_bytes(name) if self.whale else None) or b""
            else:
                # 默认资源：assets/ 根，其次 assets/wallpaper/（ocean1.jpg 等）
                body = None
                for base in (self._asset_root, os.path.join(self._asset_root, "wallpaper")):
                    fp = self._safe_join(base, name)
                    if fp and os.path.exists(fp):
                        with open(fp, "rb") as f:
                            body = f.read()
                        break
                if body is None:
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
        # 回环收口：控制台里有个人聊天记录与访问口令，非回环地址一律拒（要远程先显式开开关）
        if host not in ("127.0.0.1", "localhost", "::1", ""):
            if not cfg.get("allow_remote"):
                logging.getLogger("persona-morph").warning(
                    "server.host=%s 不是回环地址，已强制改回 127.0.0.1（确需远程访问请显式设 server.allow_remote=true）", host)
                host = "127.0.0.1"
        port = int(cfg.get("port") or 3210)
        # 控制台地址（含 token）落盘：**token 的拥有者写，别人只读**（2026-09-14）。
        # 起因：启动器/托盘各自拼地址，启动器在 config.json 里抓到排在前面的 cloud.token（空）⇒ 401。
        try:
            from .util import write_console_url
            _tok0 = str(cfg.get("token") or "").strip()
            # ⚠️ `console_url_root` 只给**判据/隔离实例**用：非空时地址落到那个根目录，绝不碰产品的
            #   `logs/console.url`（2026-09-18 事故：console_open_selftest 的 E 段真起了一个 WebUI 在
            #   **随机空闲端口**上，`start()` 把产品那份地址文件覆写成 `…:14675/?token=…`，而那个端口
            #   随判据结束就没了 ⇒ 之后启动器照着它开窗 ⇒ 控制台一屏 `ERR_CONNECTION_REFUSED`）。
            write_console_url("http://127.0.0.1:%d/" % port + (("?token=" + _tok0) if _tok0 else ""),
                              root=str(getattr(self, "console_url_root", "") or ""))
        except Exception:
            pass


        parent = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "Persona Morph/1.0"

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
                # ⛔ V-R3-8（2026-09-20 第三轮审计，两侧同源）：**光有口令还不够** ——
                #    浏览器里的任何页面都能向本机端口"发"请求（CORS 只挡读不挡发），DNS rebinding
                #    还能让外域解析到 127.0.0.1 后带着**外域 Host** 打进来 ⇒ Host 必须是回环。
                #    判据实现见 agent/local_guard.py（本地生图服务用同一份，别再各写一套）。
                if not local_guard.host_ok(self.headers.get("Host")):
                    return False
                token = str(get_config().get("server", {}).get("token") or "").strip()
                if not token:
                    # 旧行为是「空口令 = 放行」，等于控制台裸奔（本机任何进程、任何网页都能进）。
                    # 现在：拒绝 + 明确告诉怎么修（口令在启动时自动生成，见 scripts/persona_morph.py）。
                    try:
                        if not getattr(self.server, "_warned_no_token", False):
                            self.server._warned_no_token = True
                            logging.getLogger("persona-morph").warning(
                                "控制台访问口令为空 ⇒ 已拒绝全部请求。请重启机器人（会自动生成口令），"
                                "或在 config.json 的 server.token 里手填一个。")
                    except Exception:
                        pass
                    return False
                # ① 会话 Cookie（登录后 URL 不带 token，防他人复制地址登入）
                try:
                    import http.cookies as _hc
                    for m in re.findall(r"(?:^|;\s*)wxauth=([^;]+)", str(self.headers.get("Cookie") or "")):
                        # ⛔ 2026-09-21 修（第六轮 **V-R6-26**）：口令比较原来是 `==`（逐字符早停）
                        #   ⇒ 理论上可被计时侧信道逐位猜出。改成**常数时间比较**。
                        if _hmac.compare_digest(str(m).strip(), token):
                            return True
                except Exception:
                    pass
                # ② 支持 ?token= 或 Authorization: Bearer
                q = urlparse(self.path).query
                from urllib.parse import parse_qs
                if any(_hmac.compare_digest(str(x), token) for x in parse_qs(q).get("token", [])):
                    return True
                auth = str(self.headers.get("Authorization", "") or "")
                if auth.startswith("Bearer ") and _hmac.compare_digest(auth[7:], token):
                    return True
                return False

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

            def _archive_view(self):
                """存档屏蔽：列消息（带 recalled/blocked 标记）/ 屏蔽名单 / 读数。

                ⛔ 2026-09-17 修（用户报「每次我一打开，右下角都是读取会话失败 error，但是又能连上」）：
                **真因＝这条路由原来只注册在 POST 分支里**（它和 `/api/config`（保存配置）同一条
                `elif` 链），而控制台前端用的是 **GET**（`getJSON('/api/archive')`）⇒ **每次都 404**
                ⇒ 前端 catch 到就弹"读会话列表失败"。**整个「屏蔽存档」面板因此一直是死的**
                （列表那一步也走 GET）。⇒ 抽成这一个方法，**GET 与 POST 都接**（一个能力一处实现）。
                """
                from . import archive_filter as _af
                st = getattr(parent, "store", None)
                if st is None:
                    # 还没就绪：屏蔽名单本身是**磁盘数据**，没有 store 也能给出名单 ⇒ 回 `ok:True` +
                    # 空会话列表 + 一句"正在连接"，由前端静默重试；**不要回 `ok:False`**
                    # （前端会当失败弹红字，用户看到的就是"error 但又能连上"）。
                    self._json({"ok": True, "chats": [], "snapshot": _af.snapshot(None),
                                "note": "正在连接微信（会话列表稍后自动刷新）"})
                    return
                q = parse_qs(urlparse(self.path).query)
                ck = str((q.get("chat_key") or [""])[0]).strip()
                lim = int((q.get("limit") or ["30"])[0] or 30)
                # ⛔ V-R5B-7：名单可能是按**群名**写的 ⇒ 把"会话名解析器"传进去，否则面板恒报"命中 0 个"
                def _name_of(_ck):
                    try:
                        _w = getattr(parent, "wechat", None)
                        if _w is None and hasattr(parent, "wechat_box"):
                            _w = (parent.wechat_box or [None])[0]
                        if _w is None:
                            return ""
                        return str(_w.display_name(str(_ck).split(":", 1)[-1]) or "")
                    except Exception:
                        return ""
                out = {"ok": True, "snapshot": _af.snapshot(st, name_of=_name_of),
                       "chats": _af.chat_options(st)}
                if ck:
                    out.update(_af.list_chat(st, ck, limit=max(1, min(200, lim))))
                self._json(out)

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                # ⛔ 2026-09-21 修（第四轮审计 **V-R4-15，P2**）：`data` 原来**只在 `do_POST` 里定义**，
                #   而 do_GET 里有三处分支读 `data.get(...)`（本地生图的 `/api/image_gen/local/install`、
                #   历史目录、打开路径）⇒ **必抛 NameError**，再被外层 `except` 吞成
                #   `{ok:false, "error":"name 'data' is not defined"}` —— 4 条 GET 路由白坏，界面上还看不出来。
                #   GET 没有请求体 ⇒ 用**查询串**当 data（`?dir=…&allow_online=1` 这类调用照样能用）。
                try:
                    data = {k: (v[0] if isinstance(v, list) and v else v)
                            for k, v in parse_qs(parsed.query).items()}
                except Exception:
                    data = {}
                # 静态素材（图标/光标图）免认证：<img> 不带 token，但素材不含隐私
                # ⛔ 2026-09-21 修（第六轮 **V-R6-26**）：这三条**免认证**路由原来**不校验 Host**
                #   （`_auth_ok` 才校验）⇒ 外域页面/DNS rebinding 能带着外域 Host 读它们（版本号泄露 + 探测本机是否有本产品）。
                #   ⇒ 免认证 ≠ 免 Host 校验：先过同一份回环校验再放行。
                if path.startswith("/assets/") or path.startswith("/wallpaper/") or path == "/api/version":
                    if not local_guard.host_ok(self.headers.get("Host")):
                        return self._json({"error": "bad host"}, 403)
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
                if path == "/api/update":
                    # 更新检查（**只读**）：拉清单判五态。口径＝拉不到/没配 ⇒ off（界面什么都不显示）、
                    # 清单坏了 ⇒ error 如实说、有新版 ⇒ newer + notes。**公告只在本机 UI，绝不往微信侧发**。
                    try:
                        from . import update_check as _uc
                        from . import update_apply as _ua
                        _st = _uc.state()
                        _st["job"] = _ua.job()      # 自更新作业的实时进度（控制台按钮轮询这里）
                        self._json(_st)
                    except Exception as _e:
                        self._json({"status": "error", "why": "更新检查不可用：%s" % str(_e)[:60]})
                    return
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
                    # 鲸语字典随页面下发（**一份来源**）：前端 applyWhale 用它处理"动态刷新出来的文案"
                    # （暂停/恢复/状态行等），静态部分由 _apply_whale 在服务端就换好。两种模式都下发。
                    try:
                        import json as _json
                        body = body.replace("__WHALE_TXT__", _json.dumps(WHALE_DICT, ensure_ascii=False))
                    except Exception:
                        body = body.replace("__WHALE_TXT__", "{}")
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
                elif path == "/api/voice/probe":
                    # ③ 自带模型：连通测试（只真发一次极短文本，**不改任何配置**）
                    try:
                        from . import voice_models as _vmod
                        _q2 = parse_qs(parsed.query)
                        self._json(dict({"ok": True}, **_vmod.probe((_q2.get("url") or [""])[0])))
                    except Exception as _e2:
                        self._json({"ok": False, "error": str(_e2)}, 500)
                elif path == "/api/voice/vc-probe":
                    # 变声段（第二段）连通测试：造一段 440Hz 测试音送进去，**不改任何配置**
                    try:
                        from . import voice_models as _vmod
                        _q3 = parse_qs(parsed.query)
                        self._json(dict({"ok": True}, **_vmod.probe_vc((_q3.get("url") or [""])[0])))
                    except Exception as _e3:
                        self._json({"ok": False, "error": str(_e3)}, 500)
                elif path == "/api/local-models":
                    # 本机模型端点探测（2026-09-15 任务书 ②）：只探测与展示，**绝不自动启用**——
                    # 切换 Base URL/模型必须由用户在面板上点（route 只读 discover/test_chat，不写配置）。
                    try:
                        from . import local_models as _lm
                        _q = parse_qs(parsed.query)
                        if (_q.get("test") or [""])[0] == "1":
                            self._json(dict({"ok": True}, **_lm.test_chat(
                                (_q.get("base_url") or [""])[0], (_q.get("model") or [""])[0])))
                        else:
                            _found, _meta = _lm.discover()
                            self._json({"ok": True, "found": _found, "meta": _meta,
                                        "capability": _lm.CAPABILITY_NOTE})
                    except Exception as _e:
                        self._json({"ok": False, "error": str(_e)}, 500)
                elif path == "/api/verifiers":
                    # 症状检验器（2026-09-18）：控制台用 GET 取清单（POST 那条链里也留了同样的入口，两条路都能用）
                    try:
                        from . import verifiers as _vf
                        self._json({"ok": True, "verifiers": _vf.catalog()})
                    except Exception as e:                                   # noqa: BLE001
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/verify":
                    try:
                        from urllib.parse import urlparse as _up, parse_qs as _pq
                        from . import verifiers as _vf
                        _q = _pq(_up(self.path).query)
                        # ⛔ 2026-09-21 加（第九轮 V-R9-11 / 第十轮 V-R10-8）：把**运行中实例**的
                        #   `_db_how` 与 `_cap` 喂给检验器 —— 否则"我读的是不是正在写的那个号"
                        #   与"哪张表读失败了"（「消息库读不到」那条链的核心）只能判假绿。
                        try:
                            _wo = _current_wx(parent)
                            _vf.set_runtime_how(getattr(_wo, "_db_how", None),
                                                getattr(_wo, "_cap", None))
                        except Exception:
                            pass
                        self._json(_vf.run(str((_q.get("id") or [""])[0] or "")))
                    except Exception as e:                                   # noqa: BLE001
                        self._json({"ok": False, "error": str(e)})
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
                    st = parent.status_provider()
                    try:
                        from . import risk as _risk
                        from . import input_backend as _ib
                        from . import version_matrix as _vm
                        from . import dep_heal as _dh
                        if isinstance(st, dict):
                            st = dict(st)
                            st["risk"] = _risk.snapshot()
                            # 当前输入后端档位（AGENTS §2.1 第 4 条：用了哪一档必须看得见）
                            st["input"] = _ib.status()
                            # 后台能力矩阵（⑥ 全后台审计）：每条路径是"全程后台"还是"真鼠标"，
                            # 单一事实源在 agent/bg_status.py —— 控制台照它显示，不另写一份。
                            try:
                                from . import bg_status as _bg
                                st["bg"] = _bg.status()
                            except Exception as _be:
                                st["bg"] = {"paths": [], "error": str(_be)}
                            # 「我自己是谁」（2026-09-16，已知现象：「无法识别大号用户 / 无法识别我的账号」）：
                            # `wechat.py` 只从驱动库的 `get_self_info()` 拿自己的账号，**拿不到时那一串
                            # "这条是不是我发的"判断会静默失效**（会回自己/@ 自己不理）。这里如实暴露：
                            # `ok=False` ⇒ 控制台**写"没认出来"**，不许装没事。
                            try:
                                _wx = None
                                for _n in ("wechat", "adapter", "wx", "wxadapter", "bot", "worker"):
                                    _o = getattr(parent, _n, None)
                                    if _o is not None and hasattr(_o, "self_identity"):
                                        _wx = _o
                                        break
                                st["self"] = (_wx.self_identity() if _wx is not None
                                              else {"ok": False, "why": "还拿不到微信实例（机器人未启动？）"})
                            except Exception as _se:
                                st["self"] = {"ok": False, "why": str(_se)}
                            # 「我的其他账号（大号）」（2026-09-16 已知现象：「无法识别我的大号」）：
                            # 登记了几项、昵称有几项**真在群成员里匹配上了**、当前反应档位 —— 摆出来让用户核对。
                            try:
                                st["owner"] = (_wx.owner_status() if _wx is not None
                                               and hasattr(_wx, "owner_status")
                                               else {"count": 0, "mode": "know", "why": "还拿不到微信实例（机器人未启动？）"})
                            except Exception as _oe:
                                st["owner"] = {"count": 0, "mode": "know", "why": str(_oe)}
                            # 图标指纹表（⑦ 点击正确性）：按 微信版本×尺寸×DPI 存了几条、什么时候取的
                            try:
                                from . import ui_fingerprint as _ufp
                                st["ui_fp"] = _ufp.hits()
                                st["ui_fp"]["digest"] = _ufp.digest()
                            except Exception as _fe:
                                st["ui_fp"] = {"keys": {}, "error": str(_fe)}
                            # 版本能力矩阵 + 版本门（W7：版本变了要出横幅、按未验证处理）
                            # ⛔ 2026-09-21 修（第四轮审计 **V-R4-10，P2**）：这两行原来跟其它富化段挤在
                            #   **同一个大 try** 里，任何别的段抛异常都会把它们**一起丢掉** ⇒ 前端拿到
                            #   `version/version_gate` 缺失 ⇒ 把"读不到"画成红字
                            #   「版本未实测：按严格档暂停发送（可在配置里关掉 version_gate.strict）」
                            #   ⇒ 把用户指去改一个根本没拦他的开关。⇒ 各自单独一层 try，
                            #   出错就放 **`allow=None`（读不到）**，绝不是 False（不许发）。
                            from . import version_gate as _vg2
                            try:
                                st["version"] = _vm.current()
                            except Exception as _vme:
                                st["version"] = {"wechat": "unknown", "adapter": "",
                                                 "error": str(_vme)[:80]}
                            try:
                                st["version_gate"] = _vg2.status()
                            except Exception as _vge:
                                st["version_gate"] = {"allow": None, "level": "unknown",
                                                      "error": str(_vge)[:80]}
                            # 出站闸门读数（内部故障话术拦截 / 去重窗）——前端横幅要显示，见 console_html
                            try:
                                from . import sender as _sd3
                                st["outbound_gate"] = _sd3.outbound_gate_status()
                            except Exception as _oe:
                                st["outbound_gate"] = {"blocked_internal": 0, "error": str(_oe)[:60]}
                            # 待决单（⑦ 版本不匹配四选一）：门没过就把这件事开成一张单，控制台据此弹模态。
                            # 开单是幂等的（同一对版本只开一次、问过就不再问），所以这里每次轮询调用是安全的。
                            try:
                                from . import pending_decisions as _pd
                                _pnd = _vg2.pending()
                                _pit = _pnd.get("item") or {}
                                st["pending_decisions"] = {
                                    "open": len(_pd.open_items()),
                                    "summary": _pd.summary(),
                                    "needed": bool(_pnd.get("needed")),
                                    "item": _pit if str(_pit.get("status")) == "open" else None,
                                }
                            except Exception as _pe:
                                st["pending_decisions"] = {"open": 0, "error": str(_pe)}
                            # 一键动作的后台作业（⑦）：升级适配层 / 更新本体 都是分钟级，
                            # 起在后台线程里（agent/jobs.py），这里把状态给控制台显示跑到哪、成没成。
                            try:
                                from . import jobs as _jobs
                                st["jobs"] = _jobs.status().get("jobs") or {}
                            except Exception as _je:
                                st["jobs"] = {"_error": str(_je)}
                            # 微信装没装（2026-09-13：没装就带用户去官网，不做静默安装）
                            try:
                                from .wechat import wechat_version_info as _wvi
                                _wi = _wvi() or {}
                                st["wechat_install"] = _wi.get("install") or {
                                    "state": _wi.get("state") or "unknown",
                                    "installed": bool(_wi.get("installed")),
                                    "detail": _wi.get("detail") or "",
                                    "official_url": "https://weixin.qq.com/",
                                    "action": "none"}
                            except Exception as _e:
                                st["wechat_install"] = {"state": "unknown", "installed": False,
                                                        "detail": "检测异常：" + str(_e),
                                                        "official_url": "https://weixin.qq.com/",
                                                        "action": "none"}
                            # 微信数据目录（2026-09-18 用户反馈：「他回我之前自定义的地址里去看文件了」）：
                            # 这一项报的是**当前实际在读的目录**（不是配置值），还带上"你填的那个为什么
                            # 没用、现在回落到哪"。控制台「微信数据目录」那一行直接显示它。
                            try:
                                from . import wechat_dir as _wdir_s
                                _wxo = _current_wx(parent)
                                st["wechat_dir"] = _wdir_s.status(
                                    how=getattr(_wxo, "_db_how", None) if _wxo is not None else None,
                                    dir_info=getattr(_wxo, "_db_dir_info", None) if _wxo is not None else None)
                            except Exception as _wde:
                                st["wechat_dir"] = {"ok": False, "effective": "", "src": "?",
                                                    "now": "取不到", "candidates": [],
                                                    "text": "取不到微信数据目录状态",
                                                    "note": "取不到微信数据目录状态：%s" % _wde}
                            # 依赖体检（盯项目运行时的 site-packages，不是当前进程）
                            st["deps"] = {"summary": _dh.summary_line(),
                                          "offline_available": _dh.offline_available(),
                                          "source": _dh.probe_source()}
                            # 媒体与语音（能力 A/B/C）：引擎链实测状态 + 图库现状 + 转发开关
                            try:
                                from . import media_status as _ms
                                st["media"] = _ms.snapshot()
                            except Exception as _e2:
                                st["media"] = {"error": str(_e2)}
                            # 上云（预留接口）：端点三态 + 是否配了 Token
                            try:
                                from . import cloud as _cl2
                                st["cloud"] = _cl2.snapshot()
                            except Exception as _e11:
                                st["cloud"] = {"error": str(_e11)}
                            # 图/文/视频分流 + 视频读取（第 20 条）
                            try:
                                from . import model_routes as _mrt
                                from . import video_read as _vrd
                                st["model_routes"] = _mrt.snapshot()
                                st["video_read"] = _vrd.snapshot()
                            except Exception as _e10:
                                st["model_routes"] = {"error": str(_e10)}
                            # 存档屏蔽（第 10 条）：名单 + 存档里被屏蔽的条数
                            try:
                                from . import archive_filter as _af2
                                st["archive"] = _af2.snapshot(getattr(parent, "store", None))
                            except Exception as _e9:
                                st["archive"] = {"error": str(_e9)}
                            # 计时提醒 + 节假日问候（第 12/13 条）
                            try:
                                from . import timers as _tmr
                                from . import holidays as _hol2
                                st["timers"] = _tmr.snapshot()
                                st["holiday"] = _hol2.snapshot()
                            except Exception as _e8:
                                st["timers"] = {"error": str(_e8)}
                            # 响应等级三件（第 15/16/18 条）：档位模式 / 峰谷映射 / 指令禁言现状
                            try:
                                from . import tier_control as _tcl
                                st["tier"] = _tcl.snapshot()
                            except Exception as _e7:
                                st["tier"] = {"error": str(_e7)}
                            # 备选模型（第 3 条）：清单 + 最近一次"改用备选"的现场
                            try:
                                from .llm import fallback_status as _fbs
                                st["fallback"] = _fbs()
                            except Exception as _e6:
                                st["fallback"] = {"error": str(_e6)}
                            # 撤回剔除（第 14 条）：已剔除多少条 + 最近一条的现场
                            try:
                                from . import recall as _rcl
                                st["recall"] = _rcl.summary()
                            except Exception as _e5:
                                st["recall"] = {"error": str(_e5)}
                            # 本地文件搜索（找文件并发送）：现场读目录状态 + 台账
                            try:
                                from . import file_search as _fsx
                                st["file_search"] = _fsx.snapshot()
                            except Exception as _e4:
                                st["file_search"] = {"error": str(_e4)}
                            # 自定义工具（工具与插件面板）：清单现场扫 + 调用统计
                            try:
                                from . import user_tools as _ut2
                                st["user_tools"] = _ut2.snapshot()
                            except Exception as _e3:
                                st["user_tools"] = {"error": str(_e3)}
                    except Exception as _se:
                        # ⛔ V-R4-10：**富化段出错不许悄悄丢** —— 至少把"读不到"如实放进去，
                        #   让前端能区分「读数读不到」与「真的不许发」（`allow=None` vs `False`）。
                        try:
                            if isinstance(st, dict):
                                st.setdefault("version", {"wechat": "unknown", "adapter": "",
                                                          "error": str(_se)[:80]})
                                st.setdefault("version_gate", {"allow": None, "level": "unknown",
                                                               "error": str(_se)[:80]})
                                st["status_error"] = str(_se)[:120]
                        except Exception:
                            pass
                    self._json(st)
                elif path in ("/api/file_search/add", "/api/file_search/del"):
                    # 管理"可搜目录"（面板上加入/移除）
                    try:
                        from . import file_search as _fsd
                        from .config import get_config as _gc4, save_config as _scv4, set_config as _sc4
                        d = ""
                        if isinstance(data, dict):
                            d = str(data.get("dir") or "")
                        if not d:
                            _q4 = parse_qs(urlparse(self.path).query)
                            d = str((_q4.get("dir") or [""])[0])
                        d = d.strip()
                        c4 = _gc4()
                        c4.setdefault("file_search", {})
                        cur = [str(x) for x in (c4["file_search"].get("dirs") or [])]
                        if path.endswith("/add"):
                            if d and d not in cur:
                                cur.append(d)
                            note = ("已加入：%s" % d) if d else "没给目录"
                        else:
                            cur = [x for x in cur if x != d]
                            note = ("已移除：%s" % d) if d else "没给目录"
                        c4["file_search"]["dirs"] = cur
                        _sc4(c4)
                        _scv4(c4)
                        self._json({"ok": True, "note": note, "file_search": _fsd.snapshot()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/tools/new_manifest":
                    # 「怎么加工具」弹窗的一键动作：在 tools.d/ 里生成一份可编辑模板
                    try:
                        from . import user_tools as _ut5
                        p, why = _ut5.write_template()
                        self._json({"ok": bool(p), "path": p or "", "why": why,
                                    "note": "改完点「重新加载清单」，再勾选即可；坏清单会在面板里逐条列出"})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/tools/reload":
                    # 重新扫清单目录（快照本来就是现场算的；这个端点让"重新加载"有个明确的动作与回执）
                    try:
                        from . import user_tools as _ut3
                        snap = _ut3.snapshot()
                        self._json({"ok": True, "tools": snap,
                                    "note": "清单已重扫；正在跑的会话在下一轮构建工具清单时生效"})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/tools/toggle":
                    # 勾选启停：直接改清单文件里的 enabled
                    try:
                        from . import user_tools as _ut4
                        q = parse_qs(urlparse(self.path).query)
                        nm = str((q.get("name") or [""])[0])
                        on = str((q.get("on") or ["1"])[0]) not in ("0", "false", "False", "")
                        ok_t, why_t = _ut4.set_enabled(nm, on)
                        self._json({"ok": bool(ok_t), "why": why_t, "tools": _ut4.snapshot()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/ui_fingerprint/take":
                    # 「重新取指纹」（⑦ 点击正确性）：给图标库里的命名目标各取一份 dHash 指纹。
                    # 只**看**不点：抓渲染区画面裁小块，抓不到/全黑就如实报失败，绝不写假指纹。
                    try:
                        from . import ui_fingerprint as _ufp
                        from . import wechat as _wx3
                        q = parse_qs(urlparse(self.path).query)
                        names = [s for s in str((q.get("names") or [""])[0]).split(",") if s.strip()]
                        gui = _wx3.WeChatAdapter()._get_gui()
                        r = _ufp.take(gui, names or None)
                        self._json({"ok": True, "result": r, "status": _ufp.hits(),
                                    "digest": _ufp.digest()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/ui_fingerprint/forget":
                    try:
                        from . import ui_fingerprint as _ufp2
                        q = parse_qs(urlparse(self.path).query)
                        k = str((q.get("key") or [""])[0]) or None
                        self._json({"ok": True, "result": _ufp2.forget(k), "status": _ufp2.hits()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/image_gen/local":
                    # 本地轻量生图后端：状态 + 「要装的话，要下多少 / 大概多久」（估时**当场测速**）
                    #   ⚠️ 这是 GET 分支：**没有 `data`**（那是 POST 的解析体）——要从查询串取 `estimate`。
                    try:
                        from urllib.parse import parse_qs as _pq, urlparse as _up
                        from . import sd_local as _sd
                        _qs = _pq(_up(self.path).query)
                        _out = {"ok": True, "status": _sd.status()}
                        if str((_qs.get("estimate") or ["0"])[0]).lower() not in ("", "0", "false"):
                            _out["estimate"] = _sd.estimate(do_probe=True)
                        self._json(_out)
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/image_gen/local/progress":
                    # 安装进度（控制台每秒轮询它画进度条：已下/总量/百分比/速度/预计剩余）
                    try:
                        from . import sd_local as _sd
                        self._json({"ok": True, "progress": _sd.progress(), "status": _sd.status()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/image_gen/local/install":
                    # **后台安装**（立刻返回；关掉弹窗也会继续下）
                    try:
                        from . import sd_local as _sd
                        _ao = _truthy(data.get("allow_online"))
                        _ok, _why, _info = _sd.install_async(allow_online=(None if _ao is None else bool(_ao)))
                        self._json({"ok": bool(_ok), "note": _why, "info": _info, "progress": _sd.progress()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/image_gen/local/start":
                    try:
                        from . import sd_local as _sd
                        _ok, _why = _sd.start_server()
                        self._json({"ok": bool(_ok), "note": _why})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/image_gen/local/stop":
                    try:
                        from . import sd_local as _sd
                        _ok, _why = _sd.stop_server()
                        self._json({"ok": bool(_ok), "note": _why})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/image_gen/test":
                    try:
                        from . import image_gen as _ig
                        _r = _ig.generate("", "帮我画一张两只猫的图")
                        self._json({"ok": bool(_r.get("ok")), "result": _r})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/tts/test":
                    # 「试听一句」：真跑一遍**当前选的那一档**的合成（不发送；系统档不出网，edge 档联网）。
                    # 走 voice_models.make() 而不是 tts.make()——否则面板上写着「edge 神经语音」，
                    # 试听放出来的却是系统机械音（2026-09-15 加第三档音源时一并收口）。
                    try:
                        from . import voice_models as _vm
                        txt = "这是一条语音回复的试听"
                        try:
                            q = parse_qs(urlparse(self.path).query)
                            if (q.get("text") or [""])[0]:
                                txt = str((q.get("text") or [""])[0])[:120]
                        except Exception:
                            pass
                        p, err, info = _vm.make(txt)
                        _inf = dict(info or {})
                        _be = str(_inf.get("engine") or _vm.backend())
                        self._json({"ok": bool(p), "path": p or "", "err": err or "",
                                    "info": _inf, "engine": _be,
                                    "size": (os.path.getsize(p) if p and os.path.exists(p) else 0),
                                    "note": "试听只做合成，不会发送；发出去的是音频文件，不是微信语音条。"
                                            "当前这一档＝%s" % _be})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/voice/test":
                    # 「测试引擎」：真跑一遍 TTS→WAV→SILK(微信帧)→解码→识别（不需要微信、不出网）
                    try:
                        from . import voice as _vt
                        r = _vt.selftest_loop()
                        self._json({"ok": bool(r.get("ok")), "result": r})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/wechat/recheck":
                    try:
                        from .wechat import wechat_version_info as _wvi2
                        info = _wvi2() or {}
                        self._json({"ok": True, "version": info, "install": info.get("install") or {}})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/wechat/dir":
                    # 微信数据目录（2026-09-18 用户反馈：「能不能让我自己选微信的地址」）：
                    # GET＝**只探测**：候选目录逐个给出"能不能用 + 为什么"，外加**当前实际在读哪个**
                    # （不是配置值）。写配置走 POST 那条（那里先过校验）。
                    try:
                        from . import wechat_dir as _wdir2
                        _q4 = parse_qs(parsed.query)
                        _extra = (_q4.get("path") or [""])[0]
                        _r4 = _wdir2.status(_current_dir_how(parent),
                                            explicit=(_extra or None))
                        _r4["candidates"] = _wdir2.probe(_extra or "").get("candidates") or []
                        _r4["ok"] = True
                        self._json(_r4)
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/version/allow":
                    try:
                        from . import version_gate as _vg3
                        _vg3.allow_session("console")
                        self._json({"ok": True, "gate": _vg3.status()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/balance":
                    try:
                        self._json(parent.balance_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/logs":
                    self._json({"lines": list(parent.log_buffer)})
                elif path == "/api/wechat-groups":
                    # 检测到的群聊列表（白名单勾选用，GET 的旧处理器）；`?refresh=1` 同上面那条口径
                    try:
                        _rf2 = ""
                        try:
                            from urllib.parse import urlparse as _up4, parse_qs as _pq4
                            _rf2 = str((_pq4(_up4(self.path).query).get("refresh") or [""])[0] or "").lower()
                        except Exception:
                            _rf2 = ""
                        if _rf2 in ("1", "true", "yes", "on"):
                            _o4 = _current_wx(parent)
                            if _o4 is not None and hasattr(_o4, "refresh_groups"):
                                try:
                                    _o4.refresh_groups()
                                except Exception as _e4:
                                    self._json({"ok": False, "attach_ok": True, "degraded": True,
                                                "error": ("重读群列表失败（联系人库被微信占用？）⇒ "
                                                          "消息收发不受影响；稍等几秒再试。（%s）"
                                                          % str(_e4)[:100]), "groups": []})
                                    return
                        self._json(parent.groups_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e), "groups": []})
                elif path == "/api/emojis":
                    # 表情包收藏夹列表（GET）
                    try:
                        self._json({"ok": True, "emojis": parent.emojis_fn()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e), "emojis": []})
                elif path == "/api/personas/scores":
                    # 角色评分表：系统自动贴合分 + 用户分（GET）
                    try:
                        self._json(parent.persona_scores_fn())
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/feedback":
                    # 反馈队列状态（GET）：待发/已发/当前通道 —— 界面顶部那条状态行用它
                    try:
                        from . import feedback as FB
                        st = FB.stats()
                        st["ok"] = True
                        st["recent"] = [{"id": it.get("id"), "kind": it.get("kind"),
                                         "at_h": it.get("at_h"), "sent_h": it.get("sent_h", ""),
                                         "text": str(it.get("text") or "")[:60]}
                                        for it in sorted(FB._read_all(), key=lambda x: x.get("at") or 0)[-5:]]
                        self._json(st)
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
                elif path == "/api/selfcheck-stop":
                    # 停止当前一键体检（POST；设置取消标志，体检循环下一步即退出）
                    try:
                        parent.selfcheck_stop_fn()
                        self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/archive":
                    # 存档屏蔽（第 10 条）：**GET 也接**（2026-09-17 修：原来只注册在 POST 分支，
                    # 前端用 GET ⇒ 每次 404 ⇒ 弹"读会话列表失败"、整个面板是死的）
                    try:
                        self._archive_view()
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
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
                # ⛔ 2026-09-21 修（第十轮 **V-R10-34**）：原来按 `Content-Length` **全收** ——
                #   实测 64MB 全读进内存；更糟的是"声明 200MB、只发 1MB"会把处理线程**卡死**
                #   （守着一个永远读不满的体）。⇒ 加**上限**（超限直接 413，不再读了），
                #   并把读取本身容错（客户端半途断开 ⇒ 当空体，交给各路由自己报错）。
                _MAX_BODY = 8 * 1024 * 1024
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except Exception:
                    length = 0
                if length > _MAX_BODY:
                    return self._json({"error": "请求体过大（上限 %d 字节）" % _MAX_BODY}, 413)
                try:
                    raw = self.rfile.read(length) if length else b"{}"
                except Exception:
                    raw = b"{}"
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    data = {}
                if path == "/api/update_skip":
                    # 「不再提醒这个版本」：只写本机 config.json（update.skip_version），不外发任何东西
                    try:
                        from . import update_check as _uc
                        self._json(_uc.skip_version(str((data or {}).get("version") or "")))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)[:80]}, 500)
                elif path == "/api/update_apply":
                    # 「立即更新」真干活（2026-09-16 用户：「做出来居然不给用户用，你是什么意思」）：
                    # 下载在线包 → **文件树组合哈希校验**（与 make_manifest 同一算法）→ 换入本体
                    # → 逐件组合校验 → 失败回滚；data/、config.json、日志一概不碰。
                    # 起后台线程、立刻返回；进度由 `GET /api/update` 的 `job` 字段带出去。
                    try:
                        from . import update_apply as _ua
                        self._json(_ua.start_async())
                    except Exception as e:
                        self._json({"ok": False, "why": "起不动更新作业：%s" % str(e)[:80]}, 500)
                elif path == "/api/risk":
                    # 风险闸门：暂停/恢复/查看（只影响本机行为，绝不往微信侧发任何提示）
                    try:
                        from . import risk as _risk
                        act = str((data or {}).get("action") or "show")
                        if act == "pause":
                            _risk.pause(str((data or {}).get("reason") or "手动暂停"))
                        elif act == "resume":
                            _risk.resume()
                        elif act == "recover":
                            # ⛔ 2026-09-21（第十轮 **V-R10-24** 的收尾）：`risk.recover()`
                            #   原来**全仓零调用者**（B 线复核时点出：它只活在模块里）——
                            #   而它干的事跟 `resume()` 不是一件：**两套停机开关一起清**
                            #   （config 的 `risk.paused` + 控制台横幅认的 `data/paused.flag`），
                            #   坏档 fail-closed 把用户锁住时，这就是那把"一键恢复"的钥匙。
                            _risk.recover()
                        self._json({"ok": True, "risk": _risk.snapshot()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/version/action":
                    # ⑦ 四选一里"真能一键做"的两件事：升级适配层 / 更新本体 —— 起后台作业（分钟级，
                    # 不阻塞控制台）；「仅本次允许」只放行本会话；「微信本身要处理」只回指引，
                    # **绝不装/降级微信本体**（口径写死在 version_gate.run_action 里）。
                    try:
                        from . import version_gate as _vg6
                        r = _vg6.run_action(str((data or {}).get("choice") or ""),
                                            str((data or {}).get("id") or ""))
                        self._json({"ok": bool(r.get("ok")), "result": r,
                                    "message": str(r.get("message") or "")})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/decide":
                    # 待决单表态（⑦ 版本不匹配四选一）：落台账 + 写回能力矩阵。
                    # 只有「仅本次允许」会立刻放行本会话；「升级适配层 / 更新本体」只回命令与说明；
                    # 「微信本身要处理」只给指引——**任何一条都不会在这里装包或降级微信**。
                    try:
                        from . import version_gate as _vg5
                        r = _vg5.decide(str((data or {}).get("id") or ""),
                                        str((data or {}).get("choice") or ""),
                                        note=str((data or {}).get("note") or ""))
                        _msg5 = str((r.get("action") or {}).get("message") or "")
                        self._json({"ok": True, "result": r, "message": _msg5})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/config":
                    try:
                        new_cfg = data if isinstance(data, dict) and data else get_config()
                        # 部分字段保存不丢段：与当前配置深合并（新值优先，缺失键保留旧值）
                        # 注意：deep_merge 返回全新深拷贝，绝不能原地改 _current_config，
                        # 否则 _protect_secrets 拿到的"旧值"已被掩码写脏，真实 key 会丢失。
                        new_cfg = deep_merge(get_config(), new_cfg)
                        _protect_secrets(new_cfg)  # 掩码值不覆盖真实密钥
                        # 「微信数据目录」**手动指定必须过校验**（2026-09-18 用户反馈）：
                        # 不过关就不写盘，把原因与回落目标返回给控制台显示。
                        _bad_dir = _wechat_dir_conflict(new_cfg)
                        if _bad_dir:
                            self._json(_bad_dir)
                        else:
                            set_config(new_cfg)
                            save_config(new_cfg)
                            if parent.on_save:
                                parent.on_save(new_cfg)
                            self._json({"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/wechat/dir":
                    # 手动指定微信数据目录（POST）：**先校验再写**；不通过就把原因与回落目标返回，
                    # 一个字都不写进配置（绝不静默用旧值）。
                    try:
                        from . import wechat_dir as _wdir3
                        _p3 = str((data or {}).get("path") or "")
                        _r3 = _wdir3.save(_p3, on_save=parent.on_save)
                        # 保存后**立即重探一遍**并把候选回显（用户口径：保存后要看到结果，不靠刷新）
                        _st3 = _wdir3.status(_current_dir_how(parent))
                        _st3["candidates"] = _wdir3.probe(_p3).get("candidates") or []
                        _r3["wechat_dir"] = _st3
                        self._json(_r3)
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/archive":
                    # 存档屏蔽（第 10 条）：列消息 / 屏蔽名单 / 读数
                    # 方法体在 `_archive_view()`，**GET 与 POST 共用一处实现**（2026-09-17 修 404）
                    try:
                        self._archive_view()
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path in ("/api/archive/block", "/api/archive/unblock", "/api/archive/delete"):
                    # 按条屏蔽 / 解除 / 清除（清除必须点名 id，绝不做"清空"）
                    try:
                        from . import archive_filter as _af
                        st = getattr(parent, "store", None)
                        if st is None:
                            self._json({"ok": False, "error": "机器人未启动：拿不到存档句柄"})
                        else:
                            ck = str((data or {}).get("chat_key") or "").strip()
                            ids = (data or {}).get("ids") or []
                            if not ck:
                                self._json({"ok": False, "error": "chat_key 不能为空"})
                            elif path.endswith("block"):
                                self._json(_af.block(st, ck, ids, reason=str((data or {}).get("reason") or "面板操作")))
                            elif path.endswith("unblock"):
                                self._json(_af.unblock(st, ck, ids))
                            else:
                                self._json(_af.delete(st, ck, ids))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/cloud/test":
                    # 上云预留接口：只探测连通性（DNS→TCP→TLS→HEAD），**不带凭据、不发任何用户数据**
                    try:
                        from . import cloud as _cl
                        which = str((data or {}).get("which") or "")
                        url = str((data or {}).get("url") or "")
                        self._json(_cl.probe(which=which, url=url))
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
                elif path == "/api/prompt/preview":
                    # 系统提示词编辑（第 11 条）：让用户看见"此刻真正送出的系统提示词"
                    try:
                        from . import system_prompt as _spv
                        self._json(_spv.preview())
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
                                                 str(data.get("user_id") or ""),
                                                 scope=str(data.get("scope") or "all"))
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
                elif path in ("/dsh-whale/bubble.json", "/dsh-whale/audio.json"):
                    # 上游 0.3.x 新增：泡泡 / 音频配置保存（纯配置 ⇒ 本移植版真存）
                    if parent.whale is None:
                        self._json({"error": "not found"}, 404)
                    else:
                        try:
                            self._json(parent.whale.save_cfg(os.path.basename(path), data))
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
                elif path == "/api/verifiers":
                    # 症状检验器（2026-09-18）：给控制台列清单
                    try:
                        from . import verifiers as _vf
                        self._json({"ok": True, "verifiers": _vf.catalog()})
                    except Exception as e:                                   # noqa: BLE001
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/verify":
                    # 跑一个检验器：**只读**（不动窗口/不发消息/不改配置），返回里带可复制的报告
                    try:
                        from urllib.parse import urlparse as _up, parse_qs as _pq
                        from . import verifiers as _vf
                        _q = _pq(_up(self.path).query)
                        # ⛔ 2026-09-21 加（第九轮 V-R9-11 / 第十轮 V-R10-8）：同上一处 —— 把运行中
                        #   实例的 `_db_how` 与 `_cap` 都喂给检验器，"我读的是不是正在写的那个号"
                        #   与"哪张表读失败了"才有铁证（否则只能判假绿）。
                        try:
                            _wo2 = _current_wx(parent)
                            _vf.set_runtime_how(getattr(_wo2, "_db_how", None),
                                                getattr(_wo2, "_cap", None))
                        except Exception:
                            pass
                        self._json(_vf.run(str((_q.get("id") or [""])[0] or "")))
                    except Exception as e:                                   # noqa: BLE001
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
                    # 检测到的群聊列表（白名单勾选用）；`?refresh=1` ⇒ **强制重读一次**（第九轮 V-R9-11：
                    # 群列表只在接入那一跳读一次，用户新加群/改群名/换号后没有刷新入口，只能重启）
                    try:
                        _rf = ""
                        try:
                            from urllib.parse import urlparse as _up3, parse_qs as _pq3
                            _rf = str((_pq3(_up3(self.path).query).get("refresh") or [""])[0] or "").lower()
                        except Exception:
                            _rf = ""
                        if _rf in ("1", "true", "yes", "on"):
                            _o3 = _current_wx(parent)
                            if _o3 is None or not hasattr(_o3, "refresh_groups"):
                                self._json({"ok": False, "attach_ok": False,
                                            "error": "微信还没接上 ⇒ 没法重读群列表", "groups": []})
                                return
                            try:
                                _o3.refresh_groups()
                            except Exception as _e3:
                                self._json({"ok": False, "attach_ok": True, "degraded": True,
                                            "error": ("重读群列表失败（联系人库 contact.db 被微信占用？）⇒ "
                                                      "消息收发与监听不受影响；稍等几秒再点一次。（%s）"
                                                      % str(_e3)[:100]), "groups": []})
                                return
                            # ⛔ 第十轮 **V-R10-14**：刷完必须**重算监听目标**（否则新群不进 targets、
                            #   显示名还是 wxid，界面同时给出"读到 N 个群 / 监听目标 0 个"两个结论）。
                            try:
                                _rt = getattr(parent, "refresh_targets_fn", None)
                                if callable(_rt):
                                    _rt("控制台「刷新群列表」")
                            except Exception as _e3b:
                                print("重算监听目标失败（不影响群列表本身）：%s" % str(_e3b)[:80])
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
                elif path == "/api/image_gen/local/install":
                    # **后台安装**（POST；立刻返回，进度去 /api/image_gen/local/progress 轮询）
                    try:
                        from . import sd_local as _sd
                        _ao = data.get("allow_online")
                        _ok, _why, _info = _sd.install_async(allow_online=(None if _ao is None else bool(_ao)),
                                                             pid=str(data.get("preset") or ""))
                        self._json({"ok": bool(_ok), "note": _why, "info": _info, "progress": _sd.progress()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/image_gen/local/preset":
                    # 切档（速度档 / 画质档）：写配置并按新档重启本地服务。用户口径「不二选一」⇒ 两档都能装、能切。
                    try:
                        from . import sd_local as _sd
                        _ok, _why = _sd.set_preset(str(data.get("preset") or ""))
                        self._json({"ok": bool(_ok), "note": _why, "status": _sd.status()})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/image_gen/local/start":
                    try:
                        from . import sd_local as _sd
                        _ok, _why = _sd.start_server()
                        self._json({"ok": bool(_ok), "note": _why})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/image_gen/local/stop":
                    try:
                        from . import sd_local as _sd
                        _ok, _why = _sd.stop_server()
                        self._json({"ok": bool(_ok), "note": _why})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/resume":
                    parent.resume_fn()
                    self._json({"ok": True})
                elif path == "/api/watermark/reset":
                    # 重新对齐监听水位（用户零操作版：不删文件、不重启）——
                    # 清空微信聊天记录后序号回落会让新消息被判成"处理过"，这里一键对齐。
                    try:
                        r = parent.watermark_reset_fn() or {"ok": True}
                        self._json(r if isinstance(r, dict) else {"ok": True})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)}, 500)
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
                            # 唯一实现：与 build_system_prompt 同一套回落（不再各写一份、也不再读死键 prefer_key）
                            from agent.prompt import role_text_of
                            _txt = role_text_of()
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
                elif path == "/api/sessions/delete":
                    # ⑨ 勾选删除运行明细 —— **按条删**（POST {items:[{date,ts},…]}；兼容老参数 {dates:[…]}＝按天删）
                    #    用户 2026-09-17 原话：「而且就不能改成删单条吗？用户本来就不希望全删，然后你还让他去回收站找」
                    #    ⇒ 粒度＝条；删前把**整份原文件**备到 `data/_trash/sessions/`（内部安全网，**不需要用户去翻**），
                    #      并返回 `undo` token 给面板上的「撤销」用；兼容老 dates 参数（仍按天整文件搬走）。
                    try:
                        import re as _re2
                        import time as _t9
                        _sd = os.path.join(parent._data_path("sessions"))
                        _troot = os.path.join(parent._data_path("_trash"), "sessions")
                        _stamp = _t9.strftime("%Y%m%d-%H%M%S")
                        _items = []
                        for _it in (data.get("items") or []):
                            _d = str((_it or {}).get("date") or "")
                            _ts = str((_it or {}).get("ts") or "")
                            if _re2.match(r"^\d{4}-\d{2}-\d{2}$", _d) and _ts:
                                _items.append((_d, _ts))
                        _dates = [str(d) for d in (data.get("dates") or [])
                                  if _re2.match(r"^\d{4}-\d{2}-\d{2}$", str(d))]
                        if not _items and not _dates:
                            return self._json({"ok": False, "error": "没有有效的记录"})
                        _backup = {}
                        _removed, _kept_days, _whole = 0, [], []
                        # ① 先整体备份要动的那些天（原文件照抄一份，供「撤销」）
                        _touch = sorted({d for d, _ in _items} | set(_dates))
                        for _d in _touch:
                            _f = os.path.join(_sd, _d + ".jsonl")
                            if not os.path.exists(_f):
                                continue
                            try:
                                os.makedirs(_troot, exist_ok=True)
                                _b = os.path.join(_troot, "%s.jsonl.%s" % (_d, _stamp))
                                with open(_f, "rb") as _src, open(_b, "wb") as _dst:
                                    _dst.write(_src.read())
                                _backup[_d] = _b
                            except Exception:
                                pass
                        # ② 按天处理：整文件搬（老 dates 参数）或**剔掉指定 ts**（新 items 参数）
                        for _d in set(_dates):
                            _f = os.path.join(_sd, _d + ".jsonl")
                            if os.path.exists(_f):
                                try:
                                    os.remove(_f)
                                    _whole.append(_d)
                                except Exception:
                                    pass
                        _per_day = {}
                        for _d, _ts in _items:
                            _per_day.setdefault(_d, set()).add(_ts)
                        # ⭐ 2026-09-18 修（作者原话：「我删明细就等于我想删历史，就等于我想删掉
                        #   『我说什么而他回什么』的这一段…从根上就是错的」）：
                        #   在这上面删的是**这一轮对话**，所以**同时把对应的会话历史删掉**
                        #   （模型的上下文来自 `data/messages/<会话>.json`，只删 sessions 等于没删）。
                        from . import history_prune as _hp
                        _all_entries = _hp.load_recent_entries(_sd, 100)
                        _deleted_entries = []
                        for _d, _tss in _per_day.items():
                            _f = os.path.join(_sd, _d + ".jsonl")
                            if not os.path.exists(_f):
                                continue
                            try:
                                with open(_f, "r", encoding="utf-8") as fh:
                                    _lines = fh.readlines()
                                _keep, _hit = [], 0
                                for _ln in _lines:
                                    _s = _ln.strip()
                                    if not _s:
                                        continue
                                    try:
                                        _e = json.loads(_s)
                                    except Exception:
                                        _keep.append(_ln)
                                        continue
                                    if str(_e.get("ts") or "") in _tss:
                                        _hit += 1
                                        _deleted_entries.append(_e)
                                        continue
                                    _keep.append(_ln if _ln.endswith("\n") else _ln + "\n")
                                if _hit:
                                    # V-R10-26：自己拼 `<path>.tmp` 的话，两个写者会撞同一个临时档；
                                    #   崩溃留下的孤儿也没人清 ⇒ 走统一的原子写（唯一临时名 + fsync + replace）
                                    if not persist.atomic_write_text(_f, "".join(_keep), newline="\n"):
                                        raise IOError("日志重写没写进磁盘（原档未动）：%s" % _f)
                                    _removed += _hit
                                    _kept_days.append(_d)
                            except Exception:
                                pass
                        if not _removed and not _whole:
                            return self._json({"ok": False, "error": "没有匹配到要删的记录（可能刚被删过或文件已不存在）"})
                        # ③ 把这些轮次覆盖的**会话历史**也删掉（并把整份存档备份进 _trash/messages）
                        _hres = {"removed": 0, "chats": {}, "backed": []}
                        try:
                            _hres = _hp.prune_for_deleted_runs(
                                getattr(parent, "store", None), _all_entries, _deleted_entries,
                                trash_root=os.path.join(parent._data_path("_trash"), "messages"),
                                stamp=_stamp)
                        except Exception as _e10:
                            _hres = {"removed": 0, "chats": {}, "backed": [], "error": str(_e10)[:80]}
                        _note = ("已删除 %d 条记录" % _removed) if _removed else ("已删除 %d 天的记录" % len(_whole))
                        if _hres.get("removed"):
                            _note += "，并清掉对应的对话历史 %d 条（它之后不会再拿这些旧话当真）" % _hres["removed"]
                        # ⛔ V-R5B-11：备份失败 ⇒ 那些会话**这次没删**（不可撤销的删除不做），如实说
                        _bk_fail = _hres.get("backupFailed") or []
                        if _bk_fail:
                            _note += ("；有 %d 个会话**没备份成功** ⇒ 它们的历史这次**没删**"
                                      "（撤销必须真能撤销）" % len(_bk_fail))
                        self._json({"ok": True, "note": _note, "removed": _removed,
                                    "whole_days": _whole, "days": sorted(set(_kept_days) | set(_whole)),
                                    "history_removed": _hres.get("removed", 0),
                                    "history_backup_failed": len(_bk_fail),
                                    "history_chats": _hres.get("chats", {}),
                                    "undo": _stamp if _backup else "",
                                    "backed": sorted(_backup)})
                    except Exception as e:
                        self._json({"ok": False, "error": str(e)})
                elif path == "/api/sessions/restore":
                    # 撤销上一次删除：把 `_trash/sessions/<日期>.jsonl.<stamp>` 原样放回（POST {undo:"<stamp>"}）
                    try:
                        import glob as _gl
                        import re as _re3
                        _u = str(data.get("undo") or "")
                        if not _re3.match(r"^\d{8}-\d{6}$", _u):
                            return self._json({"ok": False, "error": "撤销凭据无效"})
                        _sd = os.path.join(parent._data_path("sessions"))
                        _troot = os.path.join(parent._data_path("_trash"), "sessions")
                        _n = 0
                        for _b in _gl.glob(os.path.join(_troot, "*.jsonl." + _u)):
                            _name = os.path.basename(_b)
                            _day = _name.split(".jsonl.")[0]
                            if not _re3.match(r"^\d{4}-\d{2}-\d{2}$", _day):
                                continue
                            try:
                                os.makedirs(_sd, exist_ok=True)
                                with open(_b, "rb") as _src, open(os.path.join(_sd, _day + ".jsonl"), "wb") as _dst:
                                    _dst.write(_src.read())
                                os.remove(_b)
                                _n += 1
                            except Exception:
                                pass
                        if not _n:
                            return self._json({"ok": False, "error": "找不到可撤销的备份（可能已撤销过）"})
                        # ⭐ 2026-09-18：撤销时**连对话历史一起还原**（同一次删除在 `_trash/messages/`
                        #   里也备了整份存档；两份用一个 stamp 绑定）
                        _hn = 0
                        try:
                            from . import history_prune as _hp2
                            _hn = _hp2.restore_history(
                                os.path.join(parent._data_path("_trash"), "messages"), _u)
                        except Exception:
                            _hn = 0
                        _note2 = "已撤销，恢复了 %d 天的记录" % _n
                        if _hn:
                            _note2 += " + %d 个会话的对话历史" % _hn
                        self._json({"ok": True, "note": _note2, "restored": _n,
                                    "history_restored": _hn})
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
                            if not persist.atomic_write_json(_p, _us, indent=1):
                                raise IOError("写盘失败（原档一个字节都没动）：%s" % _p)
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
                            if not persist.atomic_write_json(_p, _us, indent=1):
                                raise IOError("写盘失败（原档一个字节都没动）：%s" % _p)
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
                        # ⛔ 2026-09-21 修（第六轮 **V-R6-26c**）：`d` 原来**不校验**就拼进文件名
                        #   （`d + ".jsonl"`）⇒ `d="../../config"` 这类能读到 data/ 之外的 .jsonl 形状的路径。
                        #   ⇒ 只收严格 `YYYY-MM-DD`（`cal_list` 那边早就这么判了，这里漏了）。
                        if not cal_date_ok(d):
                            return self._json({"error": "bad date"}, 400)
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
                        # ⛔ 2026-09-16 删掉这里的 `import threading`（已知现象：「我点了重启，咋没动静啊」）：
                        #    Python 的规则是**函数体内只要有 import 该名字，整个函数里它就是局部变量**
                        #    ⇒ 本函数早得多的分支（`/api/restart`，第 1420 行那句 `threading.Timer`）
                        #    会在赋值前引用它，抛 `UnboundLocalError: local variable 'threading'
                        #    referenced before assignment` ⇒ **「重启」按钮点了没反应**（HTTP 还回
                        #    200「正在后台重启机器人…」，所以前端看不出错）。模块顶部第 12 行本来就有
                        #    `import threading`，这里不需要再导一次。
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
                elif path == "/api/feedback/submit":
                    # 既有口径：提交（POST {kind,text,contact}）：落盘 → 立刻试投递 → 三态如实回报
                    try:
                        from . import feedback as FB
                        # 环境一律**服务端现取**（不信前端传上来的那点东西）：已知现象：「用不了」时，
                        # "微信到底连上没有、卡在哪一步"是最关键的定位信息 ⇒ 每条反馈都带上。
                        _att = {}
                        try:
                            _att = (self.status_provider() or {}).get("wechat_attach") or {}
                        except Exception:
                            _att = {}
                        env = {"wechat": str((data.get("env") or {}).get("wechat") or "")[:60],
                               "ui": str((data.get("env") or {}).get("ui") or "")[:40],
                               "attach": {"short": str(_att.get("short") or ""),
                                          "step": str(_att.get("step") or ""),
                                          "tries": int(_att.get("tries") or 0),
                                          "detail": str(_att.get("reason") or "")[:400],
                                          "steps": list(_att.get("steps") or [])}}
                        # 附件（2026-09-17 用户：「可以让用户选填一个联系邮箱」+ 图片/文件都要能提交）：
                        # 前端把文件读成 base64 一起 POST 上来；这里只做**总量闸**，具体上限与落盘在 FB 里。
                        _files = data.get("files")
                        if not isinstance(_files, list):
                            _files = []
                        _b64 = sum(len(str((f or {}).get("data") or ""))
                                   for f in _files if isinstance(f, dict))
                        if _b64 > 40 * 1024 * 1024:
                            self._json({"ok": False, "state": "error",
                                        "why": "附件加起来太大了（合计不超过 20MB）"})
                        else:
                            self._json(FB.submit(str(data.get("kind") or "其他"),
                                                 str(data.get("text") or ""),
                                                 str(data.get("contact") or ""), env, _files))
                    except Exception as e:
                        self._json({"ok": False, "state": "error", "why": str(e)})
                elif path == "/api/feedback/flush":
                    # 补发排队中的反馈（POST）
                    try:
                        from . import feedback as FB
                        self._json(FB.flush())
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
                            if not persist.atomic_write_json(_p, user_cats, indent=1):
                                raise IOError("写盘失败（原档一个字节都没动）：%s" % _p)
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
                        if not persist.atomic_write_json(cats_p, user_cats, indent=1):
                            raise IOError("写盘失败（原档一个字节都没动）：%s" % cats_p)
                        # 该分区下的卡移到默认
                        try:
                            with open(pers_p, "r", encoding="utf-8") as f:
                                items = _json.load(f)
                        except Exception:
                            items = {}
                        for k, v in items.items():
                            if (v or {}).get("cat") == name:
                                v["cat"] = "📝 自定义"
                        if not persist.atomic_write_json(pers_p, items, indent=1):
                            raise IOError("写盘失败（原档一个字节都没动）：%s" % pers_p)
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
                            if not persist.atomic_write_json(_p, items, indent=1):
                                raise IOError("写盘失败（原档一个字节都没动）：%s" % _p)
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
                        if not persist.atomic_write_json(_p, items, indent=1):
                            raise IOError("写盘失败（原档一个字节都没动）：%s" % _p)
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


