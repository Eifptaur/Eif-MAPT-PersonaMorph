# -*- coding: utf-8 -*-
"""统一配置管理：config.json + 内置默认值（合并 qq-agent 与 wechat 机器人参数）。

所有字段都有默认值；磁盘上的 config.json 只覆盖有差异的字段（深合并）。
"""
from __future__ import annotations

import copy
import json
import os

from .persona import PERSONAS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.environ.get("WX_AGENT_CONFIG") or os.path.join(ROOT, "config.json")
DATA_DIR = os.environ.get("WX_AGENT_DATA_DIR") or os.path.join(ROOT, "data")


def _default_persona_text() -> str:
    try:
        return PERSONAS["xiaojingyu"]["text"]
    except Exception:
        return ""


DEFAULT_CONFIG = {
    # ── 大模型 API（OpenAI 兼容，必填才能跑）────────────────────────────
    "api": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "provider": "",              # 多提供商目录当前选中项（可选）
        "vision": True,              # 模型是否支持图片输入（关掉则移除看图工具）
        "temperature": 0.8,
        "max_rounds": 12,            # 单次运行最多工具轮数
        "timeout_ms": 180000,
        # 成本核算（仅本地估算展示，不参与任何请求）
        "price_input_per_m": 0.0,    # 输入单价（元/百万 token）
        "price_output_per_m": 0.0,   # 输出单价
        "price_cached_per_m": 0.0,   # 命中缓存输入单价；0 时按输入价计
        "use_official_price": True,
        "model_prices": {},          # {模型id: {in, out, cached}} 按模型单价，优先级最高
    },
    # ── 微信接入（源自 wechat 机器人整合包）────────────────────────────
    "wechat": {
        "bot_nickname": "群deepseek",       # 机器人微信昵称（群里 @ 这个名字触发）
        "group_name_white_list": [],        # 允许回复的群名列表；空 = 所有群
        "poll_interval": 3,                 # 消息轮询间隔（秒）
        "rate_limit_per_minute": 20,        # 每分钟最大调用上限（工具触发兜底）
        "media_dir": "media",               # 下载图片保存目录（相对项目根）
        "db_dir": "",                       # 微信数据库目录（留空自动探测）
        "minimize_warning": True,           # 提醒不要最小化微信窗口（日志）
    },
    # ── 人设与行为 ─────────────────────────────────────────────────────
    "persona": {
        "bot_name": "小鲸鱼",               # 角色身份名
        "self_nickname": "",                # 群内展示名（留空用 bot_nickname）
        "role_text": "",                    # 留空 = 内置"小鲸鱼"角色卡
        "participation": "medium",          # low | medium | high
        "custom_rules": "",
    },
    # ── 联网搜索（保留 qq-agent 的完整实现）────────────────────────────
    "web_search": {
        "enabled": True,
        "provider": "bing",                 # bing | deepseek | zhipu | bocha | baidu | metaso | custom
        "search_url": "https://cn.bing.com/search",
        "max_results": 6,
        "deepseek": {"api_key": "", "base_url": "https://api.deepseek.com/responses",
                     "model": "deepseek-chat", "timeout_ms": 60000},
        "zhipu": {"api_key": "", "base_url": "https://open.bigmodel.cn/api/paas/v4/web_search",
                  "engine": "search_std", "count": 10, "timeout_ms": 20000},
        "bocha": {"api_key": "", "base_url": "https://api.bochaai.com/v1/web-search",
                  "count": 10, "timeout_ms": 20000},
        "baidu": {"api_key": "", "base_url": "https://qianfan.baidubce.com/v2/ai_search/web_search",
                  "count": 6, "timeout_ms": 20000},
        "metaso": {"api_key": "", "base_url": "https://metaso.cn/api/open/v1/search",
                   "count": 6, "timeout_ms": 20000},
        "custom": {"name": "", "type": "openai", "base_url": "", "api_key": "",
                   "model": "", "count": 6, "timeout_ms": 20000},
        "providers": [],
    },
    # ── 安全例外（默认全部关闭）────────────────────────────────────────
    "security": {"allow_private_image_hosts": False},
    # ── 白名单 / 黑名单（群名，空规则 = 不限制）────────────────────────
    "allow": {"groups": [], "private": []},
    "deny": {"groups": [], "private": []},
    # ── 运行节奏 ───────────────────────────────────────────────────────
    "wake_delay_ms": 2000,        # 收到消息到发起运行的防抖窗口
    "drain_delay_ms": 1200,       # 运行结束发现还有未读，到下一次运行的间隔
    "max_concurrent_runs": 2,     # 全局同时进行的 agent 运行数
    # ── 发送保护 ───────────────────────────────────────────────────────
    "send": {
        "min_gap_ms": 1000,
        "max_gap_ms": 3000,
        "by_length_ms": 20,
        "max_per_minute": 20,
        "max_per_hour": 500,
        "hard_split_at": 2000,     # 微信单条消息安全切分长度
    },
    # ── 主动开话题（可选）──────────────────────────────────────────────
    "proactive": {
        "enabled": False,
        "check_interval_min_ms": 1800000,
        "check_interval_max_ms": 5400000,
        "idle_threshold_ms": 1800000,
        "probability": 0.25,
    },
    # ── 存储 ───────────────────────────────────────────────────────────
    "store": {
        "max_messages_per_chat": 0,   # 0 = 不限制
        "context_tier": 4,            # 1=仅艾特 2=+关键词 3=+随机 4=全读
        "context_slider_pos": None,   # 滑条位置（可选，优先于 context_tier）
        "at_count": 20,
        "keyword_count": 15,
        "keywords": [],
        "random_percent": 10,
        "random_count": 8,
        "all_count": 80,
    },
    # ── 记忆 ───────────────────────────────────────────────────────────
    "memory": {
        "consolidate_enabled": True,
        "consolidate_min_interval_ms": 21600000,   # 6 小时
        "consolidate_min_impressions": 4,
        "max_impressions_per_member": 5,
        "discover_min_messages": 20,
        "discover_max_members": 3,
        "use_chat_model": True,
        "provider": "",
        "model": "",
    },
    # ── Web 控制台（浏览器里改设置 / 看状态 / 看日志 / 测试 API）────────
    "server": {
        "enabled": True,              # 是否启动 Web 控制台
        "host": "127.0.0.1",          # 只监听本机
        "port": 3210,                 # 端口被占用会自动顺延
        "token": "",                  # 访问口令；留空 = 启动时自动生成一串随机口令（保存回 config.json）
        "auto_open_browser": True,    # 启动后是否自动用默认浏览器打开控制台
    },
    # ── 群友备注（管理员设置，模型优先用备注称呼）──────────────────────
    "member_notes": {},
}


def deep_merge(base, override):
    if override is None:
        return copy.deepcopy(base)
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path: str | None = None) -> dict:
    path = path or CONFIG_FILE
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            parsed = json.load(f)
        cfg = deep_merge(cfg, parsed)
    except FileNotFoundError:
        pass
    except Exception as e:
        print("[config] 读取配置失败，使用默认值：%s" % e)
    return cfg


_current_config: dict | None = None


def get_config() -> dict:
    global _current_config
    if _current_config is None:
        _current_config = load_config()
    return _current_config


def set_config(cfg: dict) -> None:
    global _current_config
    _current_config = cfg


def save_config(cfg: dict | None = None) -> None:
    cfg = cfg or get_config()
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


def resolve_api_key(cfg: dict) -> str:
    """解析真正该用的 API Key（当前目录提供商 Key 优先于顶层 api.api_key）。"""
    api = cfg.get("api", {})
    pid = str(api.get("provider") or "").strip()
    if pid:
        for p in cfg.get("providers", []) or []:
            if str(p.get("id")) == pid:
                k = str(p.get("api_key") or "").strip()
                if k and k != "******":
                    return k
    direct = str(api.get("api_key") or "").strip()
    return "" if direct == "******" else direct
