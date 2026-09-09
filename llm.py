# -*- coding: utf-8 -*-
from __future__ import annotations
"""OpenAI 兼容 Chat Completions 客户端（非流式）。

支持工具调用、usage 统计、可重试错误分类、成本估算。"""
_USAGE_HOOK = [None]


def set_usage_hook(fn):
    """注册每次 LLM 调用的用量回调（usage dict）——供工具类消耗统计。"""
    _USAGE_HOOK[0] = fn


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
    # 思考模式：auto=不传参数（跟随模型默认）；on/off 显式控制（DeepSeek 等支持 thinking 参数的模型）。
    # 兼容性：thinking 是 DeepSeek 私有参数，非 DeepSeek 地址/免费 API 会报未知参数——只在
    # base_url 含 deepseek（或显式 api.thinking_force=true 时）才发送；其余厂商一律不传。
    thinking = str(api.get("thinking") or "auto").strip().lower()
    _deepseek_url = "deepseek" in str(api.get("base_url") or "").lower()
    _force_thinking = bool(api.get("thinking_force"))
    if _deepseek_url or _force_thinking:
        if thinking in ("on", "true", "enabled", "1"):
            body["thinking"] = {"type": "enabled"}
        elif thinking in ("off", "false", "disabled", "0"):
            body["thinking"] = {"type": "disabled"}
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
    _ret = {
        "message": choice.get("message") or {},
        "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage"),
        "model": data.get("model") or api.get("model"),
        "raw": data,
    }
    if _USAGE_HOOK[0] is not None:
        try:
            _USAGE_HOOK[0](data.get("usage"))
        except Exception:
            pass
    return _ret


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
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0,
            "reasoning_tokens": 0, "calls": 0}


def add_usage(target: dict, usage) -> dict:
    if not usage:
        return target
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    target["prompt_tokens"] += prompt
    target["completion_tokens"] += completion
    target["total_tokens"] += int(usage.get("total_tokens") or (prompt + completion))
    # 思考过程 token（DeepSeek/部分模型在 completion_tokens_details.reasoning_tokens，
    # 旧协议在顶层 reasoning_tokens）——思考全部按输出价计费，是 token 大头
    details = usage.get("completion_tokens_details") or {}
    reasoning = (details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0)
    try:
        target["reasoning_tokens"] += int(reasoning or 0)
    except (TypeError, ValueError):
        pass
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or usage.get("cached_tokens") or 0
    target["cached_tokens"] += int(cached or 0)
    target["calls"] += 1
    return target


# 内置官方/公开参考单价表（元 / 百万 token；cached 为缓存命中价，缺省按输入价近似）
# 139 条来自 QQ-agent 同源价目（2026-09-03 采集），其余为控制台预设目录历史型号的补充行。
_OFFICIAL_PRICES = {
    'deepseek-v4-flash': { 'in': 1, 'out': 4, 'cached': 0.02, 'note': '官方2026-09-10闲时价；高峰×2（2/8/0.04）' },
    'deepseek-v4-flash-0731': { 'in': 1, 'out': 4, 'cached': 0.02, 'note': '官方2026-09-10闲时价；高峰×2' },
    'deepseek-v4-flash-vision-exp': { 'in': 1, 'out': 4, 'cached': 0.02, 'note': '视觉版，官方2026-09-10闲时价；高峰×2；图片另按384token/张上限' },
    'deepseek-v4-pro': { 'in': 2, 'out': 8, 'cached': 0.04, 'note': '官方2026-09-10闲时价；高峰×2' },
    'deepseek-v4-pro-0813': { 'in': 2, 'out': 8, 'cached': 0.04, 'note': '官方2026-09-10闲时价；高峰×2' },
    'deepseek-chat': { 'in': 1, 'out': 4, 'cached': 0.02, 'note': '映射 V4-Flash：官方2026-09-10闲时价' },
    'deepseek-reasoner': { 'in': 2, 'out': 8, 'cached': 0.04, 'note': '映射 V4-Pro：官方2026-09-10闲时价' },
    'deepseek-v3.1-terminus': { 'in': 1.5, 'out': 4.5, 'cached': 0.05, 'note': '旧代，按现价近似' },
    'deepseek-r1-0528': { 'in': 4.5, 'out': 13.5, 'cached': 0.15, 'note': '旧代，按现价近似' },
    'glm-5.3': { 'in': 8, 'out': 28, 'cached': 2, 'note': '1M 上下文；缓存存储限时免费' },
    'glm-5.3-flash': { 'in': 0.4, 'out': 1.4, 'cached': 0.115, 'note': '促销价；原价 0.8/1.4/0.23（输入与缓存五折，输出不打折）' },
    'glm-5.2': { 'in': 8, 'out': 28, 'cached': 2, 'note': '1M 上下文' },
    'glm-5.1': { 'in': 6, 'out': 24, 'cached': 1.3, 'note': '≤32K 档；>32K 为 8/28/2' },
    'glm-5-turbo': { 'in': 5, 'out': 22, 'cached': 1.2, 'note': '≤32K 档；>32K 为 7/26/1.8' },
    'glm-5': { 'in': 4, 'out': 18, 'cached': 1, 'note': '≤32K 档；>32K 为 6/22/1.5' },
    'glm-5v-turbo': { 'in': 5, 'out': 22, 'cached': 1.2, 'note': '多模态；>32K 为 7/26/1.8' },
    'glm-4.7': { 'in': 2, 'out': 8, 'cached': 0.4, 'note': '≤32K 且输出<0.2K；其余档更高' },
    'glm-4.7-flash': { 'in': 0, 'out': 0, 'cached': 0, 'note': '官方免费' },
    'glm-4.7-flashx': { 'in': 0.5, 'out': 3, 'cached': 0.1, 'note': '200K 上下文' },
    'glm-4.6': { 'in': 4, 'out': 16, 'cached': 0.8, 'note': '按 4.7 同档近似' },
    'glm-4.5': { 'in': 4, 'out': 16, 'cached': 0.8, 'note': '按 4.7 同档近似' },
    'glm-4.5-air': { 'in': 0.8, 'out': 2, 'cached': 0.16, 'note': '≤32K 且输出<0.2K' },
    'glm-4.6v': { 'in': 1, 'out': 3, 'cached': 0.2, 'note': '≤32K；32-128K 为 2/6/0.4' },
    'glm-4.6v-flashx': { 'in': 0.15, 'out': 1.5, 'cached': 0.03, 'note': '≤32K' },
    'glm-4.5v': { 'in': 2, 'out': 6, 'cached': 0.4, 'note': '≤32K；32-64K 为 4/12/0.8' },
    'glm-4-plus': { 'in': 5, 'out': 5, 'note': '旧代 GLM-4 系列' },
    'glm-4-long': { 'in': 1, 'out': 1, 'note': '旧代，1M 上下文' },
    'glm-4-flash': { 'in': 0, 'out': 0, 'cached': 0, 'note': '官方免费' },
    'kimi-k3': { 'in': 20, 'out': 100, 'cached': 2, 'note': '缓存命中率>90% 时实际成本低得多' },
    'kimi-k2.7-code': { 'in': 20, 'out': 100, 'cached': 2, 'note': '编程版，按 K3 档近似' },
    'kimi-k2.6': { 'in': 8, 'out': 32, 'cached': 1, 'note': '旧代，按美元价折算' },
    'kimi-k2': { 'in': 4.32, 'out': 14.4, 'cached': 0.5, 'note': '旧代' },
    'minimax-m3': { 'in': 2.1, 'out': 8.4, 'cached': 0.42, 'note': '≤512K 五折价；>512K 为 4.2/16.8/0.84' },
    'minimax-m2.7': { 'in': 2.1, 'out': 8.4, 'cached': 0.42, 'note': '缓存写入 2.625' },
    'minimax-m2.7-highspeed': { 'in': 4.2, 'out': 16.8, 'cached': 0.42, 'note': '高速版，缓存写入 2.625' },
    'minimax-m2.5': { 'in': 2.1, 'out': 8.4, 'cached': 0.21, 'note': '缓存写入 2.625' },
    'minimax-m2.5-highspeed': { 'in': 4.2, 'out': 16.8, 'cached': 0.21, 'note': '高速版' },
    'minimax-m2.1': { 'in': 2.1, 'out': 8.4, 'cached': 0.21, 'note': '缓存写入 2.625' },
    'minimax-m2.1-highspeed': { 'in': 4.2, 'out': 16.8, 'cached': 0.21, 'note': '高速版' },
    'minimax-m2': { 'in': 2.1, 'out': 8.4, 'cached': 0.21, 'note': '缓存写入 2.625' },
    'minimax-m1': { 'in': 2.1, 'out': 8.4, 'cached': 0.21, 'note': '旧代，按现价近似' },
    'mimo-v2.5': { 'in': 1, 'out': 2, 'cached': 0.02, 'note': '对标 DeepSeek V4-Flash 同档' },
    'mimo-v2.5-pro': { 'in': 3, 'out': 6, 'cached': 0.025, 'note': '对标 DeepSeek V4-Pro 同档' },
    'mimo-v2.5-pro-ultraspeed': { 'in': 9, 'out': 18, 'cached': 0.075, 'note': '极速版，为 Pro 版 3 倍价' },
    'mimo-v2.5-flash': { 'in': 0.72, 'out': 2.16, 'note': '轻量档，按美元价折算（$0.10/$0.30）' },
    'qwen3.8-max': { 'in': 14.4, 'out': 43.2, 'cached': 3.6, 'note': '1M 上下文；旗舰档' },
    'qwen3.8-max-0902': { 'in': 14.4, 'out': 43.2, 'cached': 3.6, 'note': '1M 上下文' },
    'qwen3.8-flash': { 'in': 0.94, 'out': 3.1, 'cached': 0.115, 'note': '1M 上下文；轻量主力（$0.13/$0.43）' },
    'qwen3.7-max': { 'in': 9, 'out': 27, 'note': '按 $1.25/$3.75 折算' },
    'qwen3.7-plus': { 'in': 2.3, 'out': 9, 'cached': 0.23, 'note': '1M 上下文；均衡主力（$0.32/$1.25）' },
    'qwen3.7-plus-2026-05-26': { 'in': 2.3, 'out': 9, 'cached': 0.23, 'note': '快照版' },
    'qwen3.7-flash': { 'in': 0.29, 'out': 1.15, 'note': '1M 上下文（$0.04/$0.16）' },
    'qwen3.6-flash': { 'in': 1.37, 'out': 8.14, 'cached': 0.137, 'note': '按 $0.19/$1.13 折算' },
    'qwen3.5-plus': { 'in': 2.88, 'out': 12.96, 'note': '旧代' },
    'qwen3.5-397b-a17b': { 'in': 4.32, 'out': 25.92, 'note': '开源大尺寸' },
    'qwen3-235b-a22b': { 'in': 1.44, 'out': 5.76, 'note': '开源' },
    'qwen3-235b-a22b-thinking-2507': { 'in': 2.16, 'out': 21.6, 'note': '思考版' },
    'qwen3-coder-plus': { 'in': 4, 'out': 20, 'note': '编程版，≤32K 档' },
    'qwen-plus': { 'in': 5.76, 'out': 14.4, 'note': '旧代' },
    'qwen-max': { 'in': 14.4, 'out': 43.2, 'note': '映射到 3.8-Max 档' },
    'qwen-flash': { 'in': 0.29, 'out': 1.15, 'note': '映射到 3.7-Flash 档' },
    'qwen-turbo': { 'in': 2.16, 'out': 4.32, 'note': '旧代' },
    'qwen-long': { 'in': 3.6, 'out': 14.4, 'note': '10M 上下文' },
    'hunyuan-a13b': { 'in': 0.5, 'out': 2, 'note': '腾讯云刊例价' },
    'hunyuan-role-latest': { 'in': 2.4, 'out': 9.6, 'note': '腾讯云刊例价' },
    'hunyuan-translation': { 'in': 1.2, 'out': 3.6, 'note': '翻译模型' },
    'hunyuan-translation-lite': { 'in': 1, 'out': 3, 'note': '翻译轻量版' },
    'hunyuan-embedding': { 'in': 0.7, 'out': 0.7, 'note': '向量模型' },
    'hy4-preview': { 'in': 6, 'out': 18, 'cached': 0.3, 'note': '960K 输入 / 64K 输出' },
    'hy3': { 'in': 1.15, 'out': 4.6, 'cached': 0.29, 'note': '262K 上下文，按美元价折算（$0.16/$0.64）' },
    'hunyuan': { 'in': 0.5, 'out': 2, 'note': '映射到 a13b 档（腾讯云官方刊例）' },
    'doubao-pro': { 'in': 3.2, 'out': 7.2, 'note': '旗舰档' },
    'doubao-lite': { 'in': 0.54, 'out': 1.44, 'note': '轻量档' },
    'ernie-5.0': { 'in': 8.64, 'out': 25.92, 'note': '旗舰档' },
    'ernie-4.5': { 'in': 2.88, 'out': 8.64, 'note': '旧代' },
    'gpt-5.6-sol': { 'in': 28.8, 'out': 144, 'cached': 2.88, 'note': '促销价；长上下文 57.6/216' },
    'gpt-5.6-terra': { 'in': 14.4, 'out': 86.4, 'cached': 1.44, 'note': '促销价；长上下文 28.8/129.6' },
    'gpt-5.6-luna': { 'in': 1.44, 'out': 8.64, 'cached': 0.14, 'note': '促销价；长上下文 2.88/12.96' },
    'gpt-5.6-cyber': { 'in': 90, 'out': 540, 'cached': 9, 'note': 'Daybreak 计划' },
    'gpt-5.5': { 'in': 36, 'out': 216, 'cached': 3.6, 'note': '旧代旗舰' },
    'gpt-5.5-pro': { 'in': 216, 'out': 1296, 'note': 'Pro 档' },
    'gpt-5.4': { 'in': 18, 'out': 108, 'cached': 1.8, 'note': '旧代' },
    'gpt-5.4-mini': { 'in': 5.4, 'out': 32.4, 'cached': 0.54, 'note': '旧代' },
    'gpt-5.4-nano': { 'in': 1.44, 'out': 9, 'cached': 0.14, 'note': '旧代' },
    'gpt-5.2': { 'in': 12.6, 'out': 100.8, 'note': '旧代' },
    'gpt-5.1': { 'in': 9, 'out': 72, 'note': '旧代' },
    'gpt-5': { 'in': 9, 'out': 72, 'note': '旧代' },
    'gpt-5-mini': { 'in': 1.8, 'out': 14.4, 'note': '旧代' },
    'gpt-5-nano': { 'in': 0.36, 'out': 2.88, 'note': '旧代' },
    'gpt-4.1': { 'in': 14.4, 'out': 57.6, 'cached': 2.88, 'note': '旧代' },
    'gpt-4.1-mini': { 'in': 2.88, 'out': 11.52, 'cached': 0.58, 'note': '旧代' },
    'gpt-4.1-nano': { 'in': 0.72, 'out': 2.88, 'cached': 0.14, 'note': '旧代' },
    'gpt-4o': { 'in': 18, 'out': 72, 'note': '旧代' },
    'gpt-4o-mini': { 'in': 1.08, 'out': 4.32, 'note': '旧代' },
    'gpt-oss-120b': { 'in': 0.22, 'out': 1.22, 'note': '开源权重' },
    'gpt-oss-20b': { 'in': 0.14, 'out': 0.72, 'note': '开源权重' },
    'claude-fable-5.1': { 'in': 72, 'out': 360, 'cached': 1.8, 'note': '缓存读已降 75%' },
    'claude-mythos-5.1': { 'in': 72, 'out': 360, 'cached': 1.8, 'note': '受限供应' },
    'claude-fable-5': { 'in': 72, 'out': 360, 'cached': 7.2, 'note': '旧版缓存贵 4 倍' },
    'claude-mythos-5': { 'in': 72, 'out': 360, 'cached': 7.2, 'note': '受限供应' },
    'claude-opus-5': { 'in': 36, 'out': 180, 'cached': 3.6, 'note': '1M 上下文，无长上下文档附加费' },
    'claude-opus-4.8': { 'in': 36, 'out': 180, 'cached': 3.6, 'note': '旧代旗舰' },
    'claude-opus-4.7': { 'in': 36, 'out': 180, 'cached': 3.6, 'note': '旧代' },
    'claude-opus-4.6': { 'in': 36, 'out': 180, 'cached': 3.6, 'note': '旧代' },
    'claude-opus-4.5': { 'in': 36, 'out': 180, 'cached': 3.6, 'note': '旧代' },
    'claude-opus-4.1': { 'in': 108, 'out': 540, 'note': '已退役' },
    'claude-sonnet-5': { 'in': 14.4, 'out': 72, 'cached': 1.44, 'note': '促销价已转正' },
    'claude-sonnet-4.6': { 'in': 21.6, 'out': 108, 'cached': 2.16, 'note': '旧代' },
    'claude-haiku-4.5': { 'in': 7.2, 'out': 36, 'cached': 0.72, 'note': '最便宜' },
    'claude-haiku-4.5-batch': { 'in': 3.6, 'out': 18, 'cached': 0.36, 'note': 'Batch 五折' },
    'gemini-3.8-flash': { 'in': 5.4, 'out': 27, 'cached': 0.54, 'note': '促销价至 2026-12-31' },
    'gemini-3.7-flash': { 'in': 5.4, 'out': 27, 'cached': 0.54, 'note': '促销价至 2026-12-31' },
    'gemini-3.6-flash': { 'in': 5.4, 'out': 27, 'cached': 0.54, 'note': '促销价至 2026-12-31' },
    'gemini-3.5-flash': { 'in': 10.8, 'out': 64.8, 'cached': 1.08, 'note': '原价档' },
    'gemini-3.5-flash-lite': { 'in': 2.16, 'out': 18, 'cached': 0.22, 'note': '轻量档' },
    'gemini-3.1-pro': { 'in': 14.4, 'out': 86.4, 'cached': 1.44, 'note': '≤200K；>200K 翻倍' },
    'gemini-3.1-flash-lite': { 'in': 1.8, 'out': 10.8, 'cached': 0.18, 'note': '预览' },
    'gemini-3-flash': { 'in': 3.6, 'out': 21.6, 'cached': 0.36, 'note': '预览' },
    'gemini-2.5-pro': { 'in': 9, 'out': 72, 'cached': 0.9, 'note': '旧代' },
    'gemini-2.5-flash': { 'in': 2.16, 'out': 18, 'cached': 0.22, 'note': '旧代' },
    'gemini-2.5-flash-lite': { 'in': 0.72, 'out': 2.88, 'cached': 0.07, 'note': '旧代' },
    'grok-4.6': { 'in': 14.4, 'out': 43.2, 'cached': 3.6, 'note': '≥200K 输入翻倍' },
    'grok-4.5': { 'in': 21.6, 'out': 64.8, 'note': '旧代' },
    'grok-4': { 'in': 21.6, 'out': 64.8, 'note': '旧代' },
    'muse-spark-1.3': { 'in': 9, 'out': 30.6, 'note': 'Contributor 档 0.72/1.44' },
    'muse-spark-1.2': { 'in': 9, 'out': 30.6, 'note': 'Contributor 档 0.72/1.44' },
    'muse-spark-1.1': { 'in': 9, 'out': 30.6, 'note': '旧代' },
    'llama-4-maverick': { 'in': 1.44, 'out': 5.01, 'note': '开源权重' },
    'llama-3.3-70b': { 'in': 1.44, 'out': 1.44, 'note': '开源权重' },
    'mistral-large-3': { 'in': 3.6, 'out': 10.8 },
    'mistral-medium-3.5': { 'in': 10.8, 'out': 54 },
    'command-a': { 'in': 18, 'out': 72, 'note': 'Cohere' },
    'nemotron-3-ultra': { 'in': 4.32, 'out': 25.92, 'note': 'NVIDIA' },
    'nemotron-3.5-lightning': { 'in': 0, 'out': 0, 'note': '免费额度' },
    'solar-pro-4': { 'in': 0.22, 'out': 0.86, 'cached': 0.043, 'note': 'Upstage' },
    'step-3.7-flash': { 'in': 1.15, 'out': 6.62, 'note': '阶跃星辰' },
    'longcat-2.0': { 'in': 2.16, 'out': 8.64, 'note': '美团；促销 0.3/1.2' },
    'ling-3.0-flash': { 'in': 0.15, 'out': 0.45, 'cached': 0.029, 'note': 'InclusionAI' },
    'granite-4.0-h-micro': { 'in': 0.12, 'out': 0.81, 'note': 'IBM；最便宜付费档' },
    'deepseek-v3.2': { 'in': 2.0, 'out': 3.0, 'note': '旧代，官方公开价' },
    'kimi-k2-0905-preview': { 'in': 4.32, 'out': 14.4, 'cached': 0.5, 'note': '映射到 K2 档' },
    'kimi-k2-0711-preview': { 'in': 4.32, 'out': 14.4, 'cached': 0.5, 'note': '映射到 K2 档' },
    'moonshot-v1-8k': { 'in': 12.0, 'out': 12.0, 'note': '旧代' },
    'moonshot-v1-32k': { 'in': 24.0, 'out': 24.0, 'note': '旧代' },
    'moonshot-v1-128k': { 'in': 60.0, 'out': 60.0, 'note': '旧代' },
    'glm-4v-plus': { 'in': 6.0, 'out': 12.0, 'note': '视觉旧代' },
    'qwen3.8-2.4t-a95b': { 'in': 14.4, 'out': 43.2, 'cached': 3.6, 'note': '映射到 3.8-Max 档' },
    'qwen3-max': { 'in': 4.0, 'out': 24.0, 'note': '旧代' },
    'qwen3-plus': { 'in': 2.0, 'out': 12.0, 'note': '旧代' },
    'qwen3-32b': { 'in': 2.0, 'out': 12.0, 'note': '旧代' },
    'qwen-vl-max': { 'in': 3.0, 'out': 9.6, 'note': '视觉旧代' },
    'qwen-vl-plus': { 'in': 1.5, 'out': 9.6, 'note': '视觉旧代' },
    'abab6.5s-chat': { 'in': 1.0, 'out': 8.0, 'note': '旧代' },
    'doubao-seed-1.6-250615': { 'in': 0.8, 'out': 2.0, 'note': '官方公开价' },
    'doubao-1.5-pro-32k': { 'in': 0.8, 'out': 2.0, 'note': '官方公开价' },
    'doubao-vision-pro-32k': { 'in': 3.0, 'out': 9.0, 'note': '视觉官方公开价' },
    'o3': { 'in': 2.0, 'out': 8.0, 'note': '推理档旧代' },
    'o3-mini': { 'in': 2.0, 'out': 8.0, 'note': '推理档旧代' },
    'o4-mini': { 'in': 2.0, 'out': 8.0, 'note': '推理档旧代' },
    'gpt-4-turbo': { 'in': 10.0, 'out': 30.0, 'note': '旧代' },
    'claude-opus-4-1-20250805': { 'in': 15.0, 'out': 75.0, 'note': '旧代' },
    'claude-sonnet-4-5-20250929': { 'in': 3.0, 'out': 15.0, 'note': '旧代' },
    'claude-3-7-sonnet-20250219': { 'in': 3.0, 'out': 15.0, 'note': '旧代' },
    'claude-3-5-haiku-20241022': { 'in': 0.8, 'out': 4.0, 'note': '旧代' },
    'gemini-3-pro-preview': { 'in': 2.0, 'out': 12.0, 'note': '旧代预览' },
    'gemini-2.0-flash': { 'in': 0.1, 'out': 0.4, 'note': '旧代' },
    'gemini-1.5-pro': { 'in': 1.25, 'out': 5.0, 'note': '旧代' },
    'grok-3': { 'in': 3.0, 'out': 15.0, 'note': '旧代' },
    'grok-3-mini': { 'in': 0.3, 'out': 0.5, 'note': '旧代' },
    'grok-2-latest': { 'in': 2.0, 'out': 10.0, 'note': '旧代' },
    'inkling-with-ai': { 'in': 3.0, 'out': 15.0, 'note': '聚合商浮动价' },
}


def match_official_price(model) -> dict | None:
    """按模型 id 查内置价目。匹配顺序：精确（忽略大小写）→ 去厂商前缀 → 最长前缀匹配。
    查不到返回 None（此时走主流档位估算，或由用户用 api.model_prices 精确覆盖）。"""
    table = _OFFICIAL_PRICES
    raw = str(model or "").strip().lower()
    if not raw:
        return None
    if raw in table:
        return table[raw]
    bare = raw[raw.index("/") + 1:] if "/" in raw else raw
    if bare != raw and bare in table:
        return table[bare]
    best = None
    for key in table:
        if bare.startswith(key + "/") or bare.startswith(key + "-") or bare.startswith(key + "@") or bare.startswith(key + ":"):
            if best is None or len(key) > len(best):
                best = key
    return table[best] if best else None

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
    elif api.get("use_official_price", True) is not False:
        p = match_official_price(model)
        if p is not None:
            in_price = float(p.get("in") or 0)
            out_price = float(p.get("out") or 0)
            cached_price = float(p.get("cached") or 0) or in_price
            source = "official"
            matched = True
    if source == "default":
        # 全局单价兜底：用户显式配置了 price_* 就尊重它；全为 0 才按主流档位估算
        if not (in_price or out_price or cached_price):
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
