# -*- coding: utf-8 -*-
"""OpenAI 兼容 Chat Completions 客户端（非流式）。

支持工具调用、usage 统计、可重试错误分类、成本估算。
"""
from __future__ import annotations

import re
import time

import requests

from .config import get_config, resolve_api_key

# 复用的 HTTP 会话（长连接复用，省去每次请求的 TCP/TLS 握手，实测延迟可降 100~300ms）
_session = requests.Session()


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": "Bearer " + api_key} if api_key else {}


def effective_api(cfg: dict | None = None) -> dict:
    cfg = cfg or get_config()
    api = dict(cfg.get("api", {}))
    api["api_key"] = resolve_api_key(cfg)
    return api


def is_retryable_error(error) -> bool:
    """判断错误是否值得重试（5xx/429/超时/网络/解析失败 = 重试；4xx = 不重试）。"""
    msg = str(getattr(error, "message", None) or error or "")
    if re.search(r"aborted|中止|已取消|cancel", msg, re.IGNORECASE):
        return False
    if re.search(r"HTTP\s*(401|400|403|404|405|409|413|422)", msg, re.IGNORECASE):
        return False
    if re.search(r"unauthorized|forbidden|invalid api.?key|incorrect api.?key", msg, re.IGNORECASE):
        return False
    if re.search(r"HTTP\s*5\d\d", msg, re.IGNORECASE):
        return True
    if re.search(r"429|rate.?limit|限流|too many requests|quota", msg, re.IGNORECASE):
        return True
    if re.search(r"超时|timeout|timed out", msg, re.IGNORECASE):
        return True
    if re.search(r"ETIMEDOUT|ECONNRESET|ECONNREFUSED|ECONNABORTED|ENOTFOUND|EAI_AGAIN|EHOSTUNREACH|EPIPE|socket hang up|fetch failed|network|ConnectionError|ConnectTimeout|ReadTimeout", msg, re.IGNORECASE):
        return True
    if re.search(r"无法解析的 JSON|Unexpected end|unexpected token|JSONDecodeError|Expecting value", msg, re.IGNORECASE):
        return True
    return False


class LLMError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def chat_completion(messages, tools=None, tool_choice="auto", temperature=None, overrides=None):
    """单次对话请求。返回 {message, finish_reason, usage, model, raw}。"""
    api = overrides or effective_api()
    if not str(api.get("base_url") or "").strip():
        raise LLMError("模型 API Base URL 未配置")
    body = {"model": api.get("model"), "messages": messages, "stream": False}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    temp = api.get("temperature", 0.8) if temperature is None else temperature
    if temp is not None:
        try:
            body["temperature"] = float(temp)
        except (TypeError, ValueError):
            pass
    timeout_ms = max(5000, int(api.get("timeout_ms") or 180000))
    headers = {"Content-Type": "application/json", **_auth_headers(str(api.get("api_key") or ""))}
    try:
        resp = _session.post(join_url(str(api.get("base_url")), "/chat/completions"),
                             headers=headers, json=body, timeout=timeout_ms / 1000.0)
    except requests.exceptions.Timeout:
        raise LLMError("模型请求超时（%dms）" % timeout_ms)
    except requests.exceptions.RequestException as e:
        raise LLMError("模型请求失败：%s" % e)
    if resp.status_code != 200:
        raise LLMError("模型 API HTTP %d：%s" % (resp.status_code, resp.text[:500]))
    try:
        data = resp.json()
    except Exception:
        raise LLMError("模型 API 返回了无法解析的 JSON")
    choice = (data.get("choices") or [None])[0]
    if not choice:
        raise LLMError("模型 API 响应缺少 choices：%s" % str(data)[:300])
    return {
        "message": choice.get("message") or {},
        "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage"),
        "model": data.get("model") or api.get("model"),
        "raw": data,
    }


def chat_completion_with_retry(args, retries=2):
    """带重试的单次请求（仅对可重试错误，1s→2s 指数退避）。"""
    last_error = None
    for attempt in range(retries + 1):
        try:
            return chat_completion(**args)
        except Exception as e:
            last_error = e
            if attempt >= retries or not is_retryable_error(e):
                raise
            wait = 1.0 * (2 ** attempt)
            print("[llm] 请求失败（第 %d 次尝试），%.0fms 后重试：%s" % (attempt + 1, wait * 1000, getattr(e, "message", e)))
            time.sleep(wait)
    raise last_error


def empty_usage() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0, "calls": 0}


def add_usage(target: dict, usage) -> dict:
    if not usage:
        return target
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    target["prompt_tokens"] += prompt
    target["completion_tokens"] += completion
    target["total_tokens"] += int(usage.get("total_tokens") or (prompt + completion))
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or usage.get("cached_tokens") or 0
    target["cached_tokens"] += int(cached or 0)
    target["calls"] += 1
    return target


def cache_hit_rate(usage: dict) -> float:
    p = int(usage.get("prompt_tokens") or 0)
    if not p:
        return 0.0
    return min(1.0, max(0.0, int(usage.get("cached_tokens") or 0) / p))


# 内置官方价格表（DeepSeek 常用模型，单位：元 / 百万 token，仅供参考——价格会变，
# 最新以 DeepSeek 官网定价页为准；可随时在 config.json 的 api.model_prices 里覆盖）
_OFFICIAL_PRICES = {
    "deepseek-chat": {"in": 2.0, "out": 8.0, "cached": 0.2},
    "deepseek-reasoner": {"in": 4.0, "out": 16.0, "cached": 1.0},
}
# 未知/新模型（如内置实验型号）也按主流档位估算，避免成本恒显示 0.0000
_DEFAULT_PRICE = {"in": 2.0, "out": 8.0, "cached": 0.2}


def estimate_cost(usage: dict, model: str | None = None) -> dict:
    """按配置单价折算成本（元）。返回 {cost, source, breakdown, prices, matched}。"""
    cfg = get_config()
    api = cfg.get("api", {})
    model = model or str(api.get("model") or "")

    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cached = min(int(usage.get("cached_tokens") or 0), prompt)
    fresh = max(0, prompt - cached)

    # 单价来源优先级：按模型自定义价 > 内置官方表 > 全局兜底
    in_price = float(api.get("price_input_per_m") or 0)
    out_price = float(api.get("price_output_per_m") or 0)
    cached_price = float(api.get("price_cached_per_m") or 0) or in_price
    source = "default"
    matched = False

    model_prices = api.get("model_prices") or {}
    if model in model_prices:
        p = model_prices[model]
        in_price = float(p.get("in") or 0)
        out_price = float(p.get("out") or 0)
        cached_price = float(p.get("cached") or 0) or in_price
        source = "custom"
        matched = True
    elif api.get("use_official_price", True) is not False and model in _OFFICIAL_PRICES:
        p = _OFFICIAL_PRICES[model]
        in_price = float(p.get("in") or 0)
        out_price = float(p.get("out") or 0)
        cached_price = float(p.get("cached") or 0) or in_price
        source = "official"
        matched = True
    else:
        # 未知模型：按主流档位估算（可选择关闭 use_official_price 或配置 model_prices 精确覆盖）
        in_price = float(_DEFAULT_PRICE["in"])
        out_price = float(_DEFAULT_PRICE["out"])
        cached_price = float(_DEFAULT_PRICE["cached"])
        source = "estimated"
        matched = False

    cost = (fresh / 1e6) * in_price + (cached / 1e6) * cached_price + (completion / 1e6) * out_price
    return {
        "cost": cost,
        "source": source,
        "matched": matched,
        "breakdown": {
            "fresh": (fresh / 1e6) * in_price,
            "cached": (cached / 1e6) * cached_price,
            "output": (completion / 1e6) * out_price,
        },
        "prices": {"in": in_price, "out": out_price, "cached": cached_price},
    }


# ── DeepSeek 账户余额查询（参考 DeepSeek-Balance-Whale-Widget）───────────

import time as _time
from urllib.parse import urlsplit

_balance_cache = {"ts": 0.0, "data": None}


def query_balance(cache_seconds: int = 30) -> dict:
    """查询 DeepSeek 账户余额（调用官方 /user/balance，带缓存避免频繁请求）。

    返回 {is_available, currency, total_balance, granted_balance, topped_up_balance}
    （字符串形式的金额）。30 秒内重复查询走缓存，不重复请求。
    注意：走中转站/自定义 base_url 时，余额接口地址可能不同，查询失败会抛 LLMError。
    """
    now = _time.time()
    if _balance_cache["data"] is not None and (now - _balance_cache["ts"]) < cache_seconds:
        return dict(_balance_cache["data"])

    api = effective_api()
    base = str(api.get("base_url") or "").strip()
    key = str(api.get("api_key") or "").strip()
    if not key or "在这里填" in key or key == "******":
        raise LLMError("未配置有效的 API Key，无法查询余额")
    if not base:
        raise LLMError("未配置 Base URL，无法查询余额")

    parts = urlsplit(base)
    origin = "%s://%s" % (parts.scheme or "https", parts.netloc)
    url = origin + "/user/balance"

    try:
        resp = _session.get(url, headers={"Authorization": "Bearer " + key}, timeout=15)
    except requests.exceptions.RequestException as e:
        raise LLMError("余额查询请求失败：%s" % e)
    if resp.status_code != 200:
        raise LLMError("余额查询 HTTP %d：%s" % (resp.status_code, resp.text[:200]))
    try:
        data = resp.json()
    except Exception:
        raise LLMError("余额查询返回了无法解析的 JSON")

    infos = data.get("balance_infos") or []
    info = infos[0] if infos else {}
    result = {
        "is_available": bool(data.get("is_available")),
        "currency": str(info.get("currency") or "CNY"),
        "total_balance": str(info.get("total_balance") or "0"),
        "granted_balance": str(info.get("granted_balance") or "0"),
        "topped_up_balance": str(info.get("topped_up_balance") or "0"),
    }
    _balance_cache["ts"] = now
    _balance_cache["data"] = result
    return dict(result)
