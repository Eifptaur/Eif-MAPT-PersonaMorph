# -*- coding: utf-8 -*-
"""本机模型端点探测（Ollama / LM Studio / vLLM / llama.cpp / text-generation-webui…）

用户口径（2026-09-15 ②）：探测本机 OpenAI 兼容端点 + 一键连通测试 + **能力如实标注** + 界面画在「模型」面板。

三条纪律（写死在代码里，别在界面层再解释一遍）：
  1. **一律不自动启用**：本模块只探测与展示；切换模型要用户自己点（写 `api.base_url` / `api.model`）。
  2. **能力如实标注**：探测只能证明「端点活着 / 有哪些模型 / 多快」，
     **证明不了支持工具或视觉** ⇒ 面板上一律标「未声明」；真支持与否只能由一次真实调用判定
     （控制台已有 `/api/test-api` 就是干这个的，不许在这里猜）。
  3. **短超时、不阻塞**：并发探测 + 总预算 2.5 秒；任一端口没人听就如实报「未发现」。

对外只暴露四个函数：`endpoints()` / `probe_models()` / `discover()` / `test_chat()`。
网络访问全部走 `_http_get_json` / `_http_post_json` 两个小函数（判据里直接替身它们，不联网）。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

#: 常见本机 OpenAI 兼容端点（端口是各家的默认值；配置里可追加）
DEFAULT_ENDPOINTS = [
    {"id": "ollama", "name": "Ollama", "base_url": "http://127.0.0.1:11434/v1"},
    {"id": "lmstudio", "name": "LM Studio", "base_url": "http://127.0.0.1:1234/v1"},
    {"id": "vllm", "name": "vLLM", "base_url": "http://127.0.0.1:8000/v1"},
    {"id": "llamacpp", "name": "llama.cpp server", "base_url": "http://127.0.0.1:8080/v1"},
    {"id": "textgen", "name": "text-generation-webui", "base_url": "http://127.0.0.1:5000/v1"},
]

#: 能力一律按"未声明"出口径——探测能证明什么就说什么（见模块 docstring 第 2 条）
CAPABILITY_NOTE = "未声明（本探测只能证明端点可用与模型清单；工具/视觉是否支持必须真跑一轮）"

DEFAULT_TIMEOUT = 1.2
DISCOVER_BUDGET = 2.5


def endpoints(cfg=None):
    """返回候选端点列表：内置 5 个 + 配置 `api.local_endpoints` 里追加的。"""
    out = [dict(x) for x in DEFAULT_ENDPOINTS]
    cfg = cfg if isinstance(cfg, dict) else (_safe_cfg() or {})
    extra = ((cfg.get("api") or {}).get("local_endpoints") or [])
    seen = {x["base_url"].rstrip("/") for x in out}
    for i, item in enumerate(extra):
        if isinstance(item, str) and item.strip():
            url = item.strip()
            name = url
        elif isinstance(item, dict) and str(item.get("base_url") or "").strip():
            url = str(item["base_url"]).strip()
            name = str(item.get("name") or url)
        else:
            continue
        if url.rstrip("/") in seen:
            continue
        seen.add(url.rstrip("/"))
        out.append({"id": "custom%d" % (i + 1), "name": name, "base_url": url})
    return out


def _safe_cfg():
    try:
        from .config import get_config
        return get_config()
    except Exception:
        return {}


# ── 两个网络出口（判据里替身它们；不改这两处就没法离线测）────────────────────
def _http_get_json(url, timeout):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _http_post_json(url, payload, timeout, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ── 探测：/v1/models ─────────────────────────────────────────────────────────
def probe_models(base_url, timeout=DEFAULT_TIMEOUT):
    """探一个端点。**永远不会抛异常**——失败也返回如实的原因（界面据此显示）。"""
    url = base_url.rstrip("/") + "/models"
    t0 = time.time()
    out = {"base_url": base_url, "reachable": False, "models": [], "ms": 0,
           "error": "", "capability": CAPABILITY_NOTE}
    try:
        data = _http_get_json(url, timeout)
        out["ms"] = int((time.time() - t0) * 1000)
        items = (data or {}).get("data")
        if not isinstance(items, list):
            out["error"] = "端点有应答但不是 OpenAI 兼容的 /models 结构（data 不是数组）"
            return out
        ids = []
        for it in items:
            if isinstance(it, dict) and it.get("id"):
                ids.append(str(it["id"]))
        out["reachable"] = True
        out["models"] = ids
        if not ids:
            out["error"] = "端点可用，但一个模型都没装（Ollama 请先 `ollama pull`）"
        return out
    except urllib.error.HTTPError as e:
        out["ms"] = int((time.time() - t0) * 1000)
        out["error"] = "HTTP %s（端点不是 OpenAI 兼容，或该路径未开）" % e.code
        return out
    except (socket.timeout, TimeoutError):
        out["ms"] = int((time.time() - t0) * 1000)
        out["error"] = "超时 %ss（可能在启动中）" % timeout
        return out
    except urllib.error.URLError as e:
        out["ms"] = int((time.time() - t0) * 1000)
        reason = getattr(e, "reason", e)
        out["error"] = "连不上：%s" % ("连接被拒绝" if "refused" in str(reason).lower() else str(reason)[:80])
        return out
    except Exception as e:                                    # 含 JSON 解析失败
        out["ms"] = int((time.time() - t0) * 1000)
        out["error"] = "应答无法解析：%s" % str(e)[:80]
        return out


def discover(cfg=None, timeout=DEFAULT_TIMEOUT, budget=DISCOVER_BUDGET):
    """并发探所有候选端口；总预算内给结果。返回 (可用列表, 全部结果)。"""
    cands = endpoints(cfg)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(cands)))) as ex:
        results = list(ex.map(lambda c: dict(c, **probe_models(c["base_url"], timeout)), cands))
    found = [r for r in results if r.get("reachable")]
    return found, {"all": results, "elapsed_ms": int((time.time() - t0) * 1000),
                   "budget_s": budget, "checked": len(cands)}


# ── 连通测试：真发一次最小 /chat/completions（1 token）─────────────────────────
def test_chat(base_url, model, timeout=20, api_key=""):
    """真调一次，拿真实延迟与真实报错。**只用于用户点「连通测试」时**，不在探测路径里调。"""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 1, "temperature": 0}
    t0 = time.time()
    try:
        data = _http_post_json(url, payload, timeout, {"Authorization": "Bearer %s" % api_key} if api_key else None)
        ms = int((time.time() - t0) * 1000)
        text = ""
        try:
            text = (data["choices"][0]["message"].get("content") or "")[:40]
        except Exception:
            pass
        return {"ok": True, "ms": ms, "model": (data or {}).get("model") or model, "reply": text,
                "capability": CAPABILITY_NOTE,
                "note": "能对话 ≠ 支持工具/视觉；要判这两项请用「测试 API」（真跑一轮工具调用）"}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "error": "HTTP %s %s" % (e.code, body)}
    except Exception as e:
        return {"ok": False, "ms": int((time.time() - t0) * 1000), "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
