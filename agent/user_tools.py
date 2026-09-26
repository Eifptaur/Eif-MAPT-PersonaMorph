# -*- coding: utf-8 -*-
"""用户自定义工具（**声明式 HTTP 工具**）：让用户往 `tools.d/*.json` 里丢清单，勾选后给模型用。

用户 （看了对方控制台的「工具与插件」后）
⇒ 我们做同一件事，但**先做最安全的那一档**：

    · 只发 **HTTP**（GET/POST），**绝不执行本地代码**（本模块里没有 eval/exec/import 第三方）
    · **域名白名单必填**（`allow_hosts`），并复用 `safe_fetch.validate_url` 的 SSRF 闸门（默认拒内网/环回）
    · **命名规范 + 去重**：名字必须 `[a-z_][a-z0-9_]{2,30}`、不许与内置工具重名、清单内不许重名
    · **参数必须是合法的 JSON Schema 对象**（不许把参数名当工具名那种乱象）
    · **坏清单不静默**：每条问题都收集起来给控制台显示（宁可面板一片红，也不要假装加载成功）
    · 逐项 `enabled` 启停 + 总开关 `user_tools.enabled`（默认关）

清单字段（`tools.d/example.json`）：
    {
      "name": "get_weather",            // 必填
      "description": "查某城市天气",      // 必填（会进提示词）
      "enabled": true,                  // 逐项启停
      "method": "GET",                  // GET | POST
      "url": "https://api.example.com/w",   // 必填，http/https
      "allow_hosts": ["api.example.com"],   // 必填：白名单
      "headers": {"X-Key": "…"},        // 可选
      "query": {"city": "{city}"},      // 可选：模板变量从模型参数取
      "body": {"q": "{city}"},          // 可选（POST）
      "params": {"type": "object", "properties": {...}, "required": [...]},   // 可选，默认空对象
      "timeout_ms": 8000,               // 可选
      "response_path": "data.answer",   // 可选：从 JSON 里取哪一段当结果
      "max_chars": 4000                 // 可选：结果截断
    }
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{2,30}$")
_METHODS = ("GET", "POST")


def _cfg() -> dict:
    try:
        from .config import get_config
        return dict(get_config().get("user_tools") or {})
    except Exception:
        return {}


def manifest_dir() -> str:
    d = str(_cfg().get("dir") or "tools.d")
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


def enabled() -> bool:
    return bool(_cfg().get("enabled"))


def _render(obj, args: dict):
    """把模板里的 `{key}` 换成参数值（只做字符串替换，不做任何表达式求值）。"""
    if isinstance(obj, dict):
        return {k: _render(v, args) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_render(v, args) for v in obj]
    if isinstance(obj, str):
        out = obj
        for k, v in (args or {}).items():
            out = out.replace("{%s}" % k, str(v))
        return out
    return obj


def _host_allowed(url: str, allow_hosts) -> bool:
    try:
        host = (urllib.parse.urlparse(str(url)).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    for h in (allow_hosts or []):
        hh = str(h or "").strip().lower()
        if hh and (host == hh or host.endswith("." + hh)):
            return True
    return False


def validate(mf: dict, builtin_names=(), seen=()) -> tuple:
    """校验一份清单。返回 `(clean 或 None, 问题说明)`。**任何问题都返回原因，不静默跳过。**"""
    if not isinstance(mf, dict):
        return None, "清单不是一个 JSON 对象"
    name = str(mf.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        return None, "name「%s」不合规（要 [a-z_][a-z0-9_]{2,30}）" % (mf.get("name") or "")
    if name in set(builtin_names or ()):
        return None, "name「%s」与内置工具重名（不许覆盖内置）" % name
    if name in set(seen or ()):
        return None, "name「%s」与另一份清单重复（同一能力不许起两个名）" % name
    desc = str(mf.get("description") or "").strip()
    if len(desc) < 6:
        return None, "description 太短（要写清这个工具做什么，它会进提示词）"
    method = str(mf.get("method") or "GET").strip().upper()
    if method not in _METHODS:
        return None, "method 只能是 GET 或 POST（收到 %r）" % mf.get("method")
    url = str(mf.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None, "url 必须是 http/https"
    hosts = mf.get("allow_hosts") or []
    if not isinstance(hosts, list) or not hosts:
        return None, "allow_hosts 必填（域名白名单，至少一项）"
    if not _host_allowed(url, hosts):
        return None, "url 的主机不在 allow_hosts 白名单里（%s）" % url
    params = mf.get("params") or {"type": "object", "properties": {}, "required": []}
    if not isinstance(params, dict) or str(params.get("type") or "object") != "object":
        return None, "params 必须是 JSON Schema 的对象（type=object）"
    props = params.get("properties")
    if props is not None and not isinstance(props, dict):
        return None, "params.properties 必须是对象"
    if isinstance(props, dict) and name in props: # 参数名当工具名那种乱象，直接掐掉
        return None, "params 里出现了与工具同名的字段「%s」，像是把参数名当工具名了" % name
    # 展示用字段（对标 nonebot2 / koishi：这些**只用于显示**，我们不解析、不拉取、不校验签名）
    version = str(mf.get("version") or "").strip()[:20]
    author = str(mf.get("author") or "").strip()[:40]
    homepage = str(mf.get("homepage") or "").strip()
    if homepage and not homepage.lower().startswith(("http://", "https://")):
        return None, "homepage 必须是 http/https 链接（它只是给你自己写的工具留个认领位）"
    # 「这个工具怎么用」：只进控制台、**不进提示词**（省 token 的边界见 tools.py 的裁剪注释）
    usage = str(mf.get("usage") or "").strip()[:200]
    examples = mf.get("examples") or []
    if not isinstance(examples, list):
        return None, "examples 必须是数组（每个元素＝一条参数的 JSON 字符串）"
    examples = [str(x).strip()[:200] for x in examples if str(x or "").strip()][:3]
    return {"name": name, "description": desc, "method": method, "url": url,
            "allow_hosts": [str(h).strip().lower() for h in hosts if str(h).strip()],
            "headers": mf.get("headers") or {}, "query": mf.get("query") or {},
            "body": mf.get("body") or {}, "params": params,
            "timeout_ms": int(mf.get("timeout_ms") or _cfg().get("timeout_ms") or 8000),
            "response_path": str(mf.get("response_path") or ""),
            "max_chars": int(mf.get("max_chars") or _cfg().get("max_chars") or 4000),
            "enabled": bool(mf.get("enabled", True)), "file": "",
            "version": version, "author": author, "homepage": homepage,
            "usage": usage, "examples": examples}, ""


def _fix_hint(why: str) -> str:
    """坏清单的「人话修法」（只给控制台看；**不进提示词、不改任何发给模型的内容**）。

    对标 koishi 的"必填未填＝红条"：光说哪里不对不够，得说**怎么改**。
    """
    w = str(why or "")
    if "allow_hosts" in w and "白名单" in w:
        return "把 url 的域名原样写进 allow_hosts（至少一项；子域可以用 .前缀 一次覆盖）"
    if "JSON" in w:
        return "点「生成模板清单」重新生成一份，或拿模板逐行比对是不是少了引号/逗号"
    if "name" in w and "不合规" in w:
        return "name 要 [a-z_][a-z0-9_]{2,30}：小写字母或下划线开头，别用中文/大写/连字符"
    if "重名" in w:
        return "换一个 name（内置工具的名字不许被覆盖）"
    if "description" in w:
        return "description 写清「什么时候该用它」，至少 6 个字——它会进提示词，模型靠它挑工具"
    if "url" in w:
        return "url 要完整的 http/https 地址，例：https://api.example.com/weather"
    if "method" in w:
        return "method 只支持 GET 或 POST"
    if "params" in w:
        return "params 用 {\"type\":\"object\",\"properties\":{…},\"required\":[…]} 这个形状"
    if "homepage" in w:
        return "homepage 要么不写，要么写成 http/https 链接"
    if "examples" in w:
        return "examples 要写成数组，每个元素是一条参数的 JSON 字符串，例：[\"{\\\"city\\\": \\\"北京\\\"}\"]"
    if "内网" in w or "本机" in w:
        return "内网/本机地址永远拒绝（写进 allow_hosts 也一样）——换一个公网地址"
    if "目录不存在" in w:
        return "建一个 tools.d/ 目录（点「生成模板清单」会自动建）"
    if "上限" in w:
        return "调大「最多加载」，或先停用几个清单文件"
    return "对着「怎么加工具」弹窗里的模板逐项核一遍"


def _bad(file: str, why: str) -> dict:
    """坏清单条目：人话在前，**附上码与修法**（码只给控制台/日志/统计，不影响任何发给模型的文本）。"""
    try:
        from . import reason_codes as _rc
        code_label = _rc.label("manifest_bad") # 变量名带 code：码的去向一眼可辨（判据也按这个认）
    except Exception:
        code_label = "清单不合法"
    return {"file": file, "why": why, "fix": _fix_hint(why), "code": "manifest_bad",
            "code_label": code_label}


def load(builtin_names=()) -> tuple:
    """扫清单目录：返回 `(tools, problems, dir)`。缺目录＝空列表 + 一条提示（不是错误）。"""
    d = manifest_dir()
    tools, problems, seen = [], [], []
    if not os.path.isdir(d):
        return tools, [_bad(d, "清单目录不存在（自己建一个 tools.d/ 放 *.json 即可）")], d
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".json"):
            continue
        p = os.path.join(d, fn)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                mf = json.load(fh)
        except Exception as e:
            problems.append(_bad(p, "JSON 读不出来：%s" % type(e).__name__))
            continue
        clean, why = validate(mf, builtin_names=builtin_names, seen=seen)
        if not clean:
            problems.append(_bad(p, why))
            continue
        clean["file"] = p
        seen.append(clean["name"])
        tools.append(clean)
    if len(tools) > int(_cfg().get("max_tools") or 30):
        problems.append(_bad(d, "清单超过上限（%s 个），只加载了前 %s 个"
                                % (len(tools), int(_cfg().get("max_tools") or 30))))
        tools = tools[:int(_cfg().get("max_tools") or 30)]
    return tools, problems, d


def _dig(obj, path: str):
    cur = obj
    for part in [x for x in str(path or "").split(".") if x]:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _is_private_host(url: str) -> bool:
    """**字面级**内网判定（不做 DNS）：localhost/.local、私有/环回/链路本地 IP 一律算内网。"""
    import ipaddress
    try:
        host = (urllib.parse.urlparse(str(url)).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return True
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local") or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except ValueError:
        return False


def _finish(tool: dict, raw: bytes) -> dict:
    """把原始字节变成工具结果（decode → response_path → 截断）。两条发送路径共用。"""
    txt = (raw or b"").decode("utf-8", "ignore")
    if tool.get("response_path"):
        try:
            got = _dig(json.loads(txt), tool["response_path"])
            if got is not None:
                txt = got if isinstance(got, str) else json.dumps(got, ensure_ascii=False)
        except Exception:
            pass
    txt = txt[:int(tool.get("max_chars") or 4000)]
    return {"content": txt or "（接口返回了空内容）", "is_error": False}


def call(tool: dict, args: dict, _fetch=None) -> dict:
    """执行一个自定义工具（**只发 HTTP**）。返回 `{content, is_error}`。

    ⛔ 原来这里是"`safe_fetch.validate_url()` 判一下过不过
    ⇒ 真正连接时 `urllib` **再解析一次域名**"，校验到的 IP 被丢掉 —— E 线实测把
    `getaddrinfo` 做成"首次给公网、二次给环回"就能绕过（返回成功、环回服务收到请求＝典型 TOCTOU）。
    现在**校验与连接是同一个 IP**：走 `safe_fetch.fetch_pinned()`（钉 IP + 逐跳复校 + 跨主机剥凭据头）。
    `_fetch` 只给判据注入用（那条路径不发真请求）。
    """
    try:
        from . import safe_fetch
    except Exception:
        safe_fetch = None
    all_args = dict(args or {})
    url = _render(str(tool.get("url") or ""), all_args)
    q = _render(tool.get("query") or {}, all_args)
    if q:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode({k: v for k, v in q.items()})
    hosts = tool.get("allow_hosts")
    if not _host_allowed(url, hosts):
        return {"content": "错误：请求地址的主机不在白名单里（%s）" % url, "is_error": True}
    if _is_private_host(url):
        return {"content": "错误：地址指向内网/本机，被拒（%s）" % url, "is_error": True}
    method = str(tool.get("method") or "GET").upper()
    data = None
    headers = {str(k): str(v) for k, v in (tool.get("headers") or {}).items()}
    if method == "POST":
        body = _render(tool.get("body") or {}, all_args)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("User-Agent", "PersonaMorph/1.0 (user tool)")
    max_chars = int(tool.get("max_chars") or 4000)
    timeout = max(1.0, int(tool.get("timeout_ms") or 8000) / 1000.0)
    if _fetch is None:
        # 生产路径：**安全层拿不到就 fail-closed**（不发一个字节）
        if safe_fetch is None:
            return {"content": "错误：安全抓取层不可用，拒绝外发（fail-closed）", "is_error": True}
        try:
            r = safe_fetch.fetch_pinned(
                url, method=method, data=data, headers=headers, timeout=timeout,
                max_bytes=max_chars * 4 + 1, allow_private=False,
                # 白名单**每一跳都要过**：否则一次 302 就跳出 allow_hosts 了
                host_allowed=lambda u: _host_allowed(u, hosts))
        except Exception as e:
            return {"content": "错误：请求失败（%s: %s）" % (type(e).__name__, str(e)[:120]), "is_error": True}
        return _finish(tool, r.get("body") or b"")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _fetch(req, timeout=timeout) as r:
            raw = r.read(max_chars * 4 + 1) # 读取带上限
    except Exception as e:
        return {"content": "错误：请求失败（%s: %s）" % (type(e).__name__, str(e)[:120]), "is_error": True}
    return _finish(tool, raw)


def as_tool_defs(builtin_names=()) -> tuple:
    """把**启用**的自定义工具转成工具定义（给 `build_tool_defs()` 合并）。返回 `(defs, problems, all_tools)`。"""
    tools, problems, _d = load(builtin_names)
    defs = []
    if not enabled():
        return defs, problems, tools
    for t in tools:
        if not t.get("enabled"):
            continue
        def _mk(tool):
            def _run(ctx, args):
                from . import tool_stats
                res = call(tool, args)
                tool_stats.note("user:%s" % tool["name"], ok=not res.get("is_error"))
                return res
            return _run
        defs.append({"name": t["name"], "description": "[自定义工具] " + t["description"],
                     "parameters": t["params"], "execute": _mk(t), "source": "user",
                     "manifest": t.get("file") or ""})
    return defs, problems, tools


TEMPLATE = {
    "name": "my_tool",
    "description": "这个工具做什么、什么时候该用它（会进提示词）",
    "enabled": False,
    "method": "GET",
    "url": "https://api.example.com/x",
    "allow_hosts": ["api.example.com"],
    "params": {"type": "object", "properties": {}, "required": []},
    # 下面三个**只给控制台看**（不进提示词）：怎么用、参数长什么样、谁写的
    "usage": "什么时候点它、参数怎么填（一句话）",
    "examples": ["{}"],
    "author": "",
}


def write_template(name: str = "") -> tuple:
    """在清单目录里写一份**可编辑的模板**（控制台「怎么加工具」弹窗的一键动作）。返回 `(路径, 说明)`。"""
    d = manifest_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        return None, "建目录失败：%s" % type(e).__name__
    base = str(name or "my-tool").strip() or "my-tool"
    p = os.path.join(d, base + ".json")
    i = 2
    while os.path.exists(p): # 不覆盖已有清单
        p = os.path.join(d, "%s-%d.json" % (base, i))
        i += 1
    tmpl = dict(TEMPLATE)
    tmpl["name"] = os.path.splitext(os.path.basename(p))[0].replace("-", "_").lower()
    tmpl["name"] = re.sub(r"[^a-z0-9_]", "_", tmpl["name"])
    if not NAME_RE.match(tmpl["name"]):
        tmpl["name"] = "my_tool"
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(tmpl, fh, ensure_ascii=False, indent=2)
        return p, "模板已生成（改完点「重新加载清单」再勾选）"
    except Exception as e:
        return None, "写模板失败：%s" % type(e).__name__


def set_enabled(name: str, on: bool) -> tuple:
    """在清单文件里改 `enabled`（控制台勾选启停用）。返回 `(ok, 说明)`。原子写。"""
    n = str(name or "").strip().lower()
    if not n:
        return False, "没给工具名"
    tools, _problems, _d = load()
    for t in tools:
        if t["name"] != n:
            continue
        p = t.get("file") or ""
        try:
            with open(p, "r", encoding="utf-8") as fh:
                mf = json.load(fh)
            mf["enabled"] = bool(on)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(mf, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
            return True, "%s 已%s（下次构建工具清单时生效）" % (n, "启用" if on else "停用")
        except Exception as e:
            return False, "写清单失败：%s: %s" % (type(e).__name__, str(e)[:80])
    return False, "清单里没有名为 %s 的工具" % n


def _builtin_names() -> tuple:
    """内置工具名（导入时用来拦"覆盖内置"）。拿不到就退回空表——**不许因此让导入失败**。

    ⚠️ 名字来源是 `tools._builtin_tool_defs()`（不是 `tools.defs`：**那个名字不存在**，
    判据里踩过一次——写成 `from .tools import defs` 会静默退回空表，
    于是"与内置重名"这条拦不住）。
    """
    try:
        from .tools import _builtin_tool_defs
        return tuple(d.get("name") for d in (_builtin_tool_defs() or []) if d.get("name"))
    except Exception:
        return ()


BUNDLE_KIND = "persona-morph-tools"


def export_text(name: str = "") -> tuple:
    """把工具导出成**一份文档**（给用户备份/分享）。返回 `(文本, 说明)`。

    · 传 `name`：只导出那一个工具的原始清单（最干净，别人改个名字就能用）
    · 不传：导出全部，外面套一层**捆**（`kind` / `version` / `exported_at` / `tools`）
      导入时两种形态都认（单独一份清单 / 捆 / 裸数组都行）。

    ⛔ 只读清单文件、只生成文本：不执行任何代码、不访问网络。
    """
    tools, _problems, _d = load()
    if name:
        hit = [t for t in tools if t["name"] == str(name or "").strip().lower()]
        if not hit:
            return "", "没有名为 %s 的工具（先点「重新加载清单」看看它有没有被列出来）" % name
        raw = _read_manifest(hit[0].get("file") or "")
        if raw is None:
            return "", "读不到清单文件：%s" % (hit[0].get("file") or "")
        return json.dumps(raw, ensure_ascii=False, indent=2), "已导出 1 个工具"
    items = []
    for t in tools:
        raw = _read_manifest(t.get("file") or "")
        if raw is not None:
            items.append(raw)
    doc = {"kind": BUNDLE_KIND, "version": "1.0", "exported_at": int(time.time() * 1000),
           "count": len(items), "tools": items}
    return json.dumps(doc, ensure_ascii=False, indent=2), "已导出 %d 个工具" % len(items)


def _read_manifest(path: str):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _import_items(text: str) -> tuple:
    """把导入的文本拆成"待写入的清单列表"。认三种形态：单份清单 / 捆 / 裸数组。"""
    obj = json.loads(text)
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get("tools"), list):
        return obj["tools"]
    if isinstance(obj, dict):
        return [obj]
    return []


def import_text(text: str, overwrite: bool = False) -> dict:
    """**导入＝把文档变成插件**：校验后写进 `tools.d/<name>.json`。

    · 校验走**同一套** `validate()`（白名单必填、内网一律拒、不许与内置重名、清单内不许重名）
    · 重名默认**不覆盖**（跳过并说明）；`overwrite=True` 才替换同名的那一份
    · 返回 `{ok, added[], replaced[], skipped[{name,why,fix}], dir, note}`，**坏的一份都不写**
    · ⛔ 只写 JSON 清单：不执行任何代码、不下载任何东西、不碰 `data/`
    """
    try:
        items = _import_items(text)
    except Exception as e:
        return {"ok": False, "error": "不是合法的 JSON：%s" % str(e)[:120], "added": [], "replaced": [], "skipped": []}
    if not items:
        return {"ok": False, "error": "这份文档里没有工具（认单份清单 / 捆 / 数组三种形态）",
                "added": [], "replaced": [], "skipped": []}
    d = manifest_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": "建目录失败：%s" % type(e).__name__, "added": [], "replaced": [], "skipped": []}
    _tools, _problems, _d = load()
    existing = {t["name"]: t for t in _tools}
    on_disk = set()
    try:
        on_disk = {fn[:-5] for fn in os.listdir(d) if fn.lower().endswith(".json")}
    except Exception:
        pass
    builtin = _builtin_names()
    added, replaced, skipped, seen = [], [], [], []
    from . import persist
    for mf in items:
        clean, why = validate(mf, builtin_names=builtin, seen=seen)
        if not clean:
            _nm_bad = str(mf.get("name") or "") if isinstance(mf, dict) else ""
            skipped.append({"name": _nm_bad or "?", "why": why, "fix": _fix_hint(why)})
            continue
        nm = clean["name"]
        if nm in existing or nm in on_disk:
            if not overwrite:
                skipped.append({"name": nm, "why": "已经有一个同名工具了（要替换就先勾上「覆盖同名」）",
                                "fix": "改名，或勾上「覆盖同名」再导一次"})
                continue
            replaced.append(nm)
        else:
            added.append(nm)
        seen.append(nm)
        p = os.path.join(d, nm + ".json")
        on_disk.add(nm)
        clean.pop("file", None)
        if not persist.atomic_write_json(p, clean, indent=2):
            skipped.append({"name": nm, "why": "写文件失败（原档未动）", "fix": "检查 tools.d 目录权限"})
            if nm in added:
                added.remove(nm)
            if nm in replaced:
                replaced.remove(nm)
    ok_any = bool(added or replaced)
    return {"ok": ok_any or not skipped, "added": added, "replaced": replaced, "skipped": skipped, "dir": d,
            "note": "已写进 %s：新增 %d 个、替换 %d 个、跳过 %d 个。回面板点「重新加载清单」再勾选。"
                    % (os.path.basename(d) or d, len(added), len(replaced), len(skipped))}


def _next_step(snap: dict) -> dict:
    """**只读已有状态**推导"下一步该干什么"（不引入新概念）。

    `step` 指向控制台 `GUIDES.tools.steps` 的第几步（1-based；0＝没有对应步骤）——
    **文案只有一份**：前端拿 step 去取引导里那几步的原话，后端不另写一份说明。
    对标结论（nonebot2/koishi）：两家都只有"装完让你自己去配置页"，**"装完怎么引导"这一格是空的**。
    """
    tools = snap.get("tools") or []
    if snap.get("problems"):
        return {"code": "fix_manifest", "step": 2}
    if not tools:
        return {"code": "gen_template", "step": 1}
    if not snap.get("enabled"):
        return {"code": "enable_switch", "step": 3}
    ticked = [t for t in tools if t.get("enabled")]
    if not ticked:
        return {"code": "tick_tool", "step": 3}
    if all(int(t.get("calls") or 0) == 0 for t in ticked):
        return {"code": "try_call", "step": 4}
    return {"code": "done", "step": 0}


def snapshot() -> dict:
    """控制台「工具与插件」面板的数据源（现场读清单目录 + 统计）。"""
    from . import tool_stats
    tools, problems, d = load()
    stats = tool_stats.snapshot()
    counts = stats.get("counts") or {}
    last = stats.get("last") or {}
    out = []
    for t in tools:
        key = "user:%s" % t["name"]
        out.append({"name": t["name"], "description": t["description"], "enabled": bool(t.get("enabled")),
                    "host": (urllib.parse.urlparse(t["url"]).hostname or ""), "method": t["method"],
                    "file": os.path.basename(t.get("file") or ""), "source": "第三方",
                    "version": t.get("version") or "", "author": t.get("author") or "",
                    "homepage": t.get("homepage") or "", "usage": t.get("usage") or "",
                    "examples": list(t.get("examples") or []),
                    "calls": int(counts.get(key) or 0), "errors": int((stats.get("errors") or {}).get(key) or 0),
                    "last": int(last.get(key) or 0)})
    snap = {"enabled": enabled(), "dir": d, "tools": out, "problems": problems,
            "counts_total": int(stats.get("total") or 0), "kinds": int(stats.get("kinds") or 0),
            "ticked": len([t for t in out if t["enabled"]]),
            "note": "自定义工具＝别人写的 HTTP 接口：只发 HTTP、不跑本地代码、域名白名单、默认关"}
    snap["next_step"] = _next_step(snap)
    return snap


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
