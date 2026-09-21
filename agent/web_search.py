# -*- coding: utf-8 -*-
"""联网搜索（移植自 qq-agent src/web-search.js）：多引擎，Bing 网页解析无需 key。"""
from __future__ import annotations

import html as html_mod
import json
import os
import re
import time
from urllib.parse import quote, urlencode, urlparse, parse_qsl, urlunparse

import requests

from .config import get_config
from .safe_fetch import safe_fetch, read_stream

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# ── V-R9-26 / V-R9-27 的两个数：**回包上限**与**整轮墙钟预算** ────────────────────
# HTML_MAX_BYTES＝2MB：搜索结果页正常 100~500KB（Bing/Google 都塞内联样式），2MB 是宽裕上限。
# JSON_MAX_BYTES＝4MB：各搜索 API 的 JSON（前 6~10 条结果）正常几十 KB。
# WALL_S＝20s：一次搜索的**总耗时**上限（含连接）。为什么不是 requests 的 `timeout=`：
#   它只管**单次 recv**，对面每 6 秒吐 1 字节就能把"声明 15 秒"的请求拖到 **63 秒**
#   （审计 V-R9E-6 实测），而 `web_search` 是在唤醒链路上被模型调的 ⇒ 等于把主流程挂住。
HTML_MAX_BYTES = 2 * 1024 * 1024
JSON_MAX_BYTES = 4 * 1024 * 1024
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 8
WALL_S = 20.0


def _get_capped(resp, max_bytes: int, what: str, t0: float = None) -> bytes:
    """V-R9-26/27：带上限 + 整轮墙钟预算地读回包（超限/超时**抛**，不整包收进内存）。"""
    return read_stream(resp, max_bytes, budget_s=WALL_S, t0=t0, what=what)


def _decode(resp, raw: bytes) -> str:
    """按回包声明的编码解码（没有就用 utf-8）；等价于原来的 `resp.text` 但不整包进内存两次。"""
    enc = getattr(resp, "encoding", None) or "utf-8"
    try:
        return raw.decode(str(enc), "replace")
    except Exception:
        return raw.decode("utf-8", "replace")


def _body_text(resp, max_bytes: int, what: str, t0: float = None) -> str:
    return _decode(resp, _get_capped(resp, max_bytes, what, t0=t0))


def _err_text(resp, cap: int = 300) -> str:
    """错误回包的短摘要（**也带上限**：错误页同样可能被对面灌成 200MB）。"""
    try:
        return _body_text(resp, max(cap * 4, 4096), "错误回包")[:cap]
    except Exception:
        return ""


def _json_capped(resp, what: str, t0: float = None) -> dict:
    return json.loads(_body_text(resp, JSON_MAX_BYTES, what, t0=t0) or "{}")


def sanitize_query(query) -> str:
    return re.sub(r"\[CQ:[^\]]*\]", " ", str(query or ""), flags=re.IGNORECASE) \
        .replace("\x00", " ").strip()[:120]


def _decode_html(s: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def bing_search(query: str, search_url: str | None = None) -> dict:
    cfg = get_config().get("web_search", {})
    url = search_url or cfg.get("search_url") or "https://cn.bing.com/search"
    max_results = max(1, min(10, int(cfg.get("max_results") or 6)))
    target = url + ("&" if "?" in url else "?") + urlencode({"q": query})
    t0 = time.monotonic()
    resp = requests.get(target, headers={"user-agent": UA, "accept-language": "zh-CN,zh;q=0.9"},
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True)
    if resp.status_code != 200:
        raise RuntimeError("搜索服务 HTTP %d" % resp.status_code)
    html_text = _body_text(resp, HTML_MAX_BYTES, "搜索页 HTML", t0=t0)
    results = []
    for block in html_text.split('<li class="b_algo"')[1:]:
        href_m = re.search(r'<a[^>]+href="(https?://[^"]+)"', block, re.IGNORECASE)
        if not href_m:
            continue
        url_str = _decode_html(href_m.group(1))
        title_m = re.search(r"<h2[^>]*>([\s\S]*?)</h2>", block, re.IGNORECASE)
        title = _decode_html(title_m.group(1)) if title_m else ""
        snip_m = re.search(r"<p[^>]*>([\s\S]*?)</p>", block, re.IGNORECASE)
        snippet = _decode_html(snip_m.group(1)) if snip_m else ""
        if url_str and title:
            results.append({"title": title, "url": url_str, "snippet": snippet})
        if len(results) >= max_results:
            break
    return {"query": query, "results": results}


def google_search(query: str) -> dict:
    """Google 网页搜索（免费 HTML 解析，无需 key）。不可达/反爬时抛错，由调用方回退。"""
    cfg = get_config().get("web_search", {})
    max_results = max(1, min(10, int(cfg.get("max_results") or 6)))
    url = "https://www.google.com/search?" + urlencode({"q": query, "hl": "zh-CN"})
    t0 = time.monotonic()
    resp = requests.get(url, headers={"user-agent": UA,
                                      "accept-language": "zh-CN,zh;q=0.9,en;q=0.8"},
                        timeout=(3, 8), stream=True)
    if resp.status_code != 200:
        raise RuntimeError("Google 搜索 HTTP %d" % resp.status_code)
    html_text = _body_text(resp, HTML_MAX_BYTES, "Google 搜索页", t0=t0)
    results = []
    for block in re.split(r'<div class="[^"]*(?:MjjYud|g)"', html_text)[1:]:
        href_m = re.search(r'href="(/url\?q=([^"&]+)|https?://[^"]+)"', block, re.IGNORECASE)
        if not href_m:
            continue
        if href_m.group(1).startswith("/url?q="):
            from urllib.parse import unquote as _uq
            url_str = _uq(_decode_html(href_m.group(2)))
        else:
            url_str = _decode_html(href_m.group(1))
        title_m = re.search(r"<h3[^>]*>([\s\S]*?)</h3>", block, re.IGNORECASE)
        title = _decode_html(title_m.group(1)) if title_m else ""
        snip_m = (re.search(r'<div class="VwiC3b[^"]*"[^>]*>([\s\S]*?)</div>', block, re.IGNORECASE)
                  or re.search(r'<span class="aCOpRe[^"]*"[^>]*>([\s\S]*?)</span>', block, re.IGNORECASE))
        snippet = _decode_html(snip_m.group(1)) if snip_m else ""
        if url_str and title:
            results.append({"title": title, "url": url_str, "snippet": snippet})
        if len(results) >= max_results:
            break
    if not results:
        raise RuntimeError("Google 搜索没有解析到结果")
    return {"query": query, "results": results}


def web_search(query: str) -> dict:
    clean = sanitize_query(query)
    if not clean:
        raise RuntimeError("查询词为空")
    cfg = get_config().get("web_search", {})
    provider = str(cfg.get("provider") or "bing").lower()
    # DeepSeek 联网搜索优先（复用主 API Key）；显式配置了第三方引擎（zhipu/bocha/baidu/metaso/custom）才按其用
    if provider in ("", "bing", "google", "deepseek"):
        try:
            return _deepseek_search(clean)
        except Exception:
            pass  # 无 key/服务不可用 → 下方免费引擎回退
    if provider == "zhipu":
        return _zhipu_search(clean)
    if provider == "bocha":
        return _bocha_search(clean)
    if provider == "baidu":
        return _baidu_search(clean)
    if provider == "metaso":
        return _metaso_search(clean)
    if provider == "custom" or provider.startswith("custom:"):
        return _custom_search(clean, provider)
    # 免费网页解析：Google 优先（有谷歌用谷歌），不可达自动回退 Bing
    if cfg.get("google_first", True) is not False or provider == "google":
        try:
            return google_search(clean)
        except Exception:
            pass  # Google 不可达（无代理/超时/反爬）→ 回退
    return bing_search(clean)


def web_fetch(url: str) -> dict:
    return safe_fetch(url)


# ── 各引擎实现（结构与 qq-agent 保持一致）────────────────────────────────

def _deepseek_search(query: str) -> dict:
    cfg = get_config().get("web_search", {}).get("deepseek", {})
    # 优先搜索段自己的 key，其次主 API Key（api.api_key），最后环境变量
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        api_key = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        api_key = str(get_config().get("api", {}).get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("DeepSeek 搜索需要 API Key（可用主 API 配置）")
    base = str(cfg.get("base_url") or "https://api.deepseek.com/responses").rstrip("/")
    model = str(cfg.get("model") or "deepseek-chat")
    t0 = time.monotonic()
    resp = requests.post(base, headers={"content-type": "application/json", "authorization": "Bearer " + api_key},
                         json={"model": model,
                               "input": "请联网搜索并回答（用中文，简洁、只给结论和关键信息）：%s" % query,
                               "tools": [{"type": "web_search"}], "stream": False},
                         timeout=(CONNECT_TIMEOUT, max(10, int(cfg.get("timeout_ms") or 60000)) / 1000.0),
                         stream=True)
    if resp.status_code != 200:
        raise RuntimeError("DeepSeek 搜索 HTTP %d：%s" % (resp.status_code, _err_text(resp)))
    data = _json_capped(resp, "DeepSeek 搜索回包", t0=t0)
    # Responses API：正文在 output[] 中 type=message 的 content[].text
    out = ""
    for item in (data.get("output") or []):
        if isinstance(item, dict) and item.get("type") == "message":
            for c in (item.get("content") or []):
                if isinstance(c, dict) and c.get("type") == "output_text":
                    out += str(c.get("text") or "")
    out = out.strip() or str(data.get("output_text") or "").strip()
    if not out:
        raise RuntimeError("DeepSeek 搜索没有返回文本")
    return {"query": query, "results": [{"title": "DeepSeek 搜索", "url": "", "snippet": out}]}


def _zhipu_search(query: str) -> dict:
    cfg = get_config().get("web_search", {}).get("zhipu", {})
    api_key = str(cfg.get("api_key") or os.environ.get("ZHIPU_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("智谱搜索需要 API Key")
    endpoint = str(cfg.get("base_url") or "https://open.bigmodel.cn/api/paas/v4/web_search").rstrip("/")
    count = min(50, max(1, int(cfg.get("count") or 10)))
    t0 = time.monotonic()
    resp = requests.post(endpoint, headers={"content-type": "application/json", "authorization": "Bearer " + api_key},
                         json={"search_engine": cfg.get("engine") or "search_std", "search_query": query, "count": count},
                         timeout=(CONNECT_TIMEOUT, max(10, int(cfg.get("timeout_ms") or 20000)) / 1000.0),
                         stream=True)
    if resp.status_code != 200:
        raise RuntimeError("智谱搜索 HTTP %d：%s" % (resp.status_code, _err_text(resp)))
    data = _json_capped(resp, "智谱搜索回包", t0=t0)
    arr = data.get("search_result") or []
    max_r = max(1, int(get_config().get("web_search", {}).get("max_results") or 6))
    results = [{"title": (r.get("title") or r.get("name") or "（无标题）").strip(),
                "url": r.get("link") or r.get("url") or "",
                "snippet": (r.get("content") or r.get("summary") or "").strip()}
               for r in arr if (r.get("link") or r.get("url"))][:max_r]
    if not results:
        raise RuntimeError("智谱搜索没有返回有效结果")
    return {"query": query, "results": results}


def _bocha_search(query: str) -> dict:
    cfg = get_config().get("web_search", {}).get("bocha", {})
    api_key = str(cfg.get("api_key") or os.environ.get("BOCHA_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("博查搜索需要 API Key")
    endpoint = str(cfg.get("base_url") or "https://api.bochaai.com/v1/web-search").rstrip("/")
    count = min(50, max(1, int(cfg.get("count") or 10)))
    t0 = time.monotonic()
    resp = requests.post(endpoint, headers={"content-type": "application/json", "authorization": "Bearer " + api_key},
                         json={"query": query, "count": count, "freshness": "noLimit", "summary": False},
                         timeout=(CONNECT_TIMEOUT, max(10, int(cfg.get("timeout_ms") or 20000)) / 1000.0),
                         stream=True)
    if resp.status_code != 200:
        raise RuntimeError("博查搜索 HTTP %d：%s" % (resp.status_code, _err_text(resp)))
    data = _json_capped(resp, "博查搜索回包", t0=t0)
    if data.get("code") and int(data["code"]) != 200:
        raise RuntimeError("博查搜索 API 错误：%s" % (data.get("message") or data.get("msg") or "未知"))
    arr = (((data.get("data") or {}).get("webPages") or {}).get("value")) or []
    max_r = max(1, int(get_config().get("web_search", {}).get("max_results") or 6))
    results = [{"title": (r.get("name") or r.get("title") or "（无标题）").strip(),
                "url": r.get("url") or "",
                "snippet": (r.get("snippet") or r.get("summary") or r.get("content") or "").strip()}
               for r in arr if r.get("url")][:max_r]
    if not results:
        raise RuntimeError("博查搜索没有返回网页结果")
    return {"query": query, "results": results}


def _baidu_search(query: str) -> dict:
    cfg = get_config().get("web_search", {}).get("baidu", {})
    api_key = str(cfg.get("api_key") or os.environ.get("BAIDU_SEARCH_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("百度搜索需要 API Key")
    endpoint = str(cfg.get("base_url") or "https://qianfan.baidubce.com/v2/ai_search/web_search").rstrip("/")
    top_k = min(10, max(1, int(cfg.get("count") or 6)))
    t0 = time.monotonic()
    resp = requests.post(endpoint, headers={"content-type": "application/json", "authorization": "Bearer " + api_key},
                         json={"messages": [{"role": "user", "content": query}],
                               "search_source": "baidu_search_v2",
                               "resource_type_filter": [{"type": "web", "top_k": top_k}]},
                         timeout=(CONNECT_TIMEOUT, max(10, int(cfg.get("timeout_ms") or 20000)) / 1000.0),
                         stream=True)
    if resp.status_code != 200:
        raise RuntimeError("百度搜索 HTTP %d：%s" % (resp.status_code, _err_text(resp)))
    data = _json_capped(resp, "百度搜索回包", t0=t0)
    if data.get("error_code") and int(data["error_code"]) != 0:
        raise RuntimeError("百度搜索 API 错误：%s" % (data.get("error_msg") or data.get("message") or "未知"))
    arr = data.get("references") or []
    max_r = max(1, int(get_config().get("web_search", {}).get("max_results") or 6))
    results = [{"title": (r.get("title") or r.get("name") or "（无标题）").strip(),
                "url": r.get("url") or r.get("link") or "",
                "snippet": (r.get("content") or r.get("snippet") or r.get("summary") or "").strip()}
               for r in arr if (r.get("url") or r.get("link"))][:max_r]
    if not results:
        raise RuntimeError("百度搜索没有返回有效结果")
    return {"query": query, "results": results}


def _metaso_search(query: str) -> dict:
    cfg = get_config().get("web_search", {}).get("metaso", {})
    api_key = str(cfg.get("api_key") or os.environ.get("METASO_API_KEY") or "").strip()
    endpoint = str(cfg.get("base_url") or "https://metaso.cn/api/open/v1/search").rstrip("/")
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = "Bearer " + api_key
    t0 = time.monotonic()
    resp = requests.post(endpoint, headers=headers,
                         json={"query": query, "top_k": min(10, max(1, int(cfg.get("count") or 6)))},
                         timeout=(CONNECT_TIMEOUT, max(10, int(cfg.get("timeout_ms") or 20000)) / 1000.0),
                         stream=True)
    if resp.status_code != 200:
        raise RuntimeError("秘塔搜索 HTTP %d：%s" % (resp.status_code, _err_text(resp)))
    data = _json_capped(resp, "秘塔搜索回包", t0=t0)
    arr = data.get("results") or data.get("data") or data.get("sources") or []
    max_r = max(1, int(get_config().get("web_search", {}).get("max_results") or 6))
    results = [{"title": (r.get("title") or r.get("name") or "（无标题）").strip(),
                "url": r.get("url") or r.get("link") or "",
                "snippet": (r.get("content") or r.get("snippet") or r.get("summary") or "").strip()}
               for r in arr if (r.get("url") or r.get("link"))][:max_r]
    if not results:
        raise RuntimeError("秘塔搜索没有返回有效结果")
    return {"query": query, "results": results}


def _custom_search(query: str, provider_id: str) -> dict:
    ws = get_config().get("web_search", {})
    cfg = ws.get("custom") or {}
    if provider_id.startswith("custom:"):
        cid = provider_id[len("custom:"):]
        for p in (ws.get("providers") or []):
            if str(p.get("id")) == cid:
                cfg = p
                break
    ctype = str(cfg.get("type") or "openai").lower()
    if ctype == "bing":
        return bing_search(query, str(cfg.get("base_url") or ""))
    endpoint = str(cfg.get("base_url") or "").rstrip("/")
    if not endpoint:
        raise RuntimeError("自定义搜索未配置接口地址")
    api_key = str(cfg.get("api_key") or "").strip()
    model = str(cfg.get("model") or "").strip()
    top_k = min(10, max(1, int(cfg.get("count") or 6)))
    body = {"query": query, "q": query, "top_k": top_k, "count": top_k}
    if model:
        body["model"] = model
        body["messages"] = [{"role": "user", "content": query}]
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = "Bearer " + api_key
    t0 = time.monotonic()
    resp = requests.post(endpoint, headers=headers, json=body,
                         timeout=(CONNECT_TIMEOUT, max(10, int(cfg.get("timeout_ms") or 20000)) / 1000.0),
                         stream=True)
    if resp.status_code != 200:
        raise RuntimeError("自定义搜索 HTTP %d：%s" % (resp.status_code, _err_text(resp)))
    data = _json_capped(resp, "自定义搜索回包", t0=t0)
    arr = data.get("results") or data.get("data") or data.get("sources") or \
        data.get("references") or ((data.get("webPages") or {}).get("value")) or \
        (data if isinstance(data, list) else [])
    max_r = max(1, int(ws.get("max_results") or 6))
    results = [{"title": (r.get("title") or r.get("name") or r.get("headline") or "（无标题）").strip(),
                "url": r.get("url") or r.get("link") or "",
                "snippet": (r.get("content") or r.get("snippet") or r.get("summary") or r.get("body") or "").strip()}
               for r in arr if isinstance(r, dict) and (r.get("url") or r.get("link"))][:max_r]
    if not results:
        raise RuntimeError("自定义搜索没有返回可识别的结果")
    return {"query": query, "results": results}
