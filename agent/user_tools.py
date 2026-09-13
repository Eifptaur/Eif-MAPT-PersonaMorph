# -*- coding: utf-8 -*-
"""用户自定义工具（**声明式 HTTP 工具**）：让用户往 `tools.d/*.json` 里丢清单，勾选后给模型用。

用户 2026-09-13（看了对方控制台的「工具与插件」后）：「**用户可以自己加工具进去，要给模型用就勾选**」
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
    if isinstance(props, dict) and name in props:                   # 参数名当工具名那种乱象，直接掐掉
        return None, "params 里出现了与工具同名的字段「%s」，像是把参数名当工具名了" % name
    return {"name": name, "description": desc, "method": method, "url": url,
            "allow_hosts": [str(h).strip().lower() for h in hosts if str(h).strip()],
            "headers": mf.get("headers") or {}, "query": mf.get("query") or {},
            "body": mf.get("body") or {}, "params": params,
            "timeout_ms": int(mf.get("timeout_ms") or _cfg().get("timeout_ms") or 8000),
            "response_path": str(mf.get("response_path") or ""),
            "max_chars": int(mf.get("max_chars") or _cfg().get("max_chars") or 4000),
            "enabled": bool(mf.get("enabled", True)), "file": ""}, ""


def load(builtin_names=()) -> tuple:
    """扫清单目录：返回 `(tools, problems, dir)`。缺目录＝空列表 + 一条提示（不是错误）。"""
    d = manifest_dir()
    tools, problems, seen = [], [], []
    if not os.path.isdir(d):
        return tools, [{"file": d, "why": "清单目录不存在（自己建一个 tools.d/ 放 *.json 即可）"}], d
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".json"):
            continue
        p = os.path.join(d, fn)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                mf = json.load(fh)
        except Exception as e:
            problems.append({"file": p, "why": "JSON 读不出来：%s" % type(e).__name__})
            continue
        clean, why = validate(mf, builtin_names=builtin_names, seen=seen)
        if not clean:
            problems.append({"file": p, "why": why})
            continue
        clean["file"] = p
        seen.append(clean["name"])
        tools.append(clean)
    if len(tools) > int(_cfg().get("max_tools") or 30):
        problems.append({"file": d, "why": "清单超过上限（%s 个），只加载了前 %s 个"
                                            % (len(tools), int(_cfg().get("max_tools") or 30))})
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


def call(tool: dict, args: dict, _fetch=None) -> dict:
    """执行一个自定义工具（**只发 HTTP**）。返回 `{content, is_error}`。"""
    try:
        from . import safe_fetch
    except Exception:
        safe_fetch = None
    all_args = dict(args or {})
    url = _render(str(tool.get("url") or ""), all_args)
    q = _render(tool.get("query") or {}, all_args)
    if q:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode({k: v for k, v in q.items()})
    if not _host_allowed(url, tool.get("allow_hosts")):
        return {"content": "错误：请求地址的主机不在白名单里（%s）" % url, "is_error": True}
    if _is_private_host(url):
        return {"content": "错误：地址指向内网/本机，被拒（%s）" % url, "is_error": True}
    # 真发请求时才做 DNS 级校验（`safe_fetch` 会解析域名——离线/测试注入 fetch 时跳过，
    # 但上面的字面级内网判定始终生效）
    if _fetch is None and safe_fetch is not None:
        try:
            safe_fetch.validate_url(url, allow_private=False)
        except Exception as e:
            return {"content": "错误：地址被安全策略拒绝：%s" % e, "is_error": True}
    method = str(tool.get("method") or "GET").upper()
    data = None
    headers = {str(k): str(v) for k, v in (tool.get("headers") or {}).items()}
    if method == "POST":
        body = _render(tool.get("body") or {}, all_args)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("User-Agent", "PersonaMorph/1.0 (user tool)")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    _f = _fetch or urllib.request.urlopen
    try:
        with _f(req, timeout=max(1.0, int(tool.get("timeout_ms") or 8000) / 1000.0)) as r:
            raw = r.read(int(tool.get("max_chars") or 4000) * 4 + 1)
        txt = raw.decode("utf-8", "ignore")
    except Exception as e:
        return {"content": "错误：请求失败（%s: %s）" % (type(e).__name__, str(e)[:120]), "is_error": True}
    if tool.get("response_path"):
        try:
            got = _dig(json.loads(txt), tool["response_path"])
            if got is not None:
                txt = got if isinstance(got, str) else json.dumps(got, ensure_ascii=False)
        except Exception:
            pass
    txt = txt[:int(tool.get("max_chars") or 4000)]
    return {"content": txt or "（接口返回了空内容）", "is_error": False}


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
    while os.path.exists(p):                        # 不覆盖已有清单
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
                    "calls": int(counts.get(key) or 0), "errors": int((stats.get("errors") or {}).get(key) or 0),
                    "last": int(last.get(key) or 0)})
    return {"enabled": enabled(), "dir": d, "tools": out, "problems": problems,
            "counts_total": int(stats.get("total") or 0), "kinds": int(stats.get("kinds") or 0),
            "note": "自定义工具＝别人写的 HTTP 接口：只发 HTTP、不跑本地代码、域名白名单、默认关"}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
