# -*- coding: utf-8 -*-
"""统一配置管理：config.json + 内置默认值（合并 qq-agent 与 wechat 机器人参数）。

所有字段都有默认值；磁盘上的 config.json 只覆盖有差异的字段（深合并）。
"""
from __future__ import annotations

import copy
import json
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.environ.get("WX_AGENT_CONFIG") or os.path.join(ROOT, "config.json")
DATA_DIR = os.environ.get("WX_AGENT_DATA_DIR") or os.path.join(ROOT, "data")


DEFAULT_CONFIG = {
    # ── 大模型 API（OpenAI 兼容，必填才能跑）────────────────────────────
    "api": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "provider": "",              # 多提供商目录当前选中项（可选）
        "vision": True,              # 模型是否支持图片输入（关掉则移除看图工具）
        "temperature": 0.8,
        "max_rounds": 8,             # 单次运行最多工具轮数（每轮都重发上下文，调低更省 token）
        "thinking": "off",           # 思考模式：auto=跟随模型默认 | on=强制思考 | off=关闭思考（默认，推理文本按输出价计费且占大头）
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
        "start_paused": True,               # 启动后默认暂停（控制台点「恢复」才开始监听）
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
        "uia_setvalue": True,      # 输入用 UIA SetValue 后台直写（不点输入框/不粘贴），发送回车仍需瞬时置前
    },
    # ── 拍一拍（回拍 90% + 冷却 30 分钟 + 主动皮一下低频）────────────────
    "poke": {
        "reply_probability": 0.9,     # 别人拍你，回拍概率（0~1）
        "cooldown_seconds": 1800,     # 同一人不重复回拍的冷却（秒）
        "delay_seconds": 18.0,        # 收到拍一拍到回拍的延迟（秒，先让模型回应+落库）
        "active_probability": 0.1,    # 模型主动皮一下的概率（0~1）
        "active_daily_limit": 3,      # 每天最多主动拍几次
    },
    # ── 人性化行为决策（省 token 规则引擎；人设 participation/sticker_level 只调频率系数）──
    "behavior": {
        "collect_emoji": {"enabled": True, "probability": 0.8, "cooldown_s": 900, "daily_limit": 6},
        "send_emoji": {"enabled": True, "probability": 0.35, "cooldown_s": 600, "daily_limit": 6},
        "like_moments": {"enabled": False, "probability": 0.4, "cooldown_s": 3600, "daily_limit": 5},
        "moments_comment": {"enabled": False, "probability": 0.3, "cooldown_s": 7200, "daily_limit": 3},
        "moments_publish": {"enabled": False, "probability": 0.15, "cooldown_s": 21600, "daily_limit": 1},
        "moments_surf": {"enabled": False, "probability": 0.2, "cooldown_s": 10800, "daily_limit": 3},
        "at_member": {"enabled": True, "probability": 0.18, "cooldown_s": 1200, "daily_limit": 10},
        "poke_active": {"enabled": True, "probability": 0.1, "cooldown_s": 600, "daily_limit": 3},
    },
    # ── 主动开话题（可选，默认关）──────────────────────────────────────
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
        "context_tier": 2,            # 1=仅艾特 2=+关键词 3=+随机 4=全读（默认 2 档：省 token 且够活跃）
        "context_slider_pos": None,   # 滑条位置（可选，优先于 context_tier）
        "at_count": 12,
        "keyword_count": 10,
        "keywords": [],
        "random_percent": 60,         # 3 档随机回复概率（3 档=艾特/关键词必回 + 普通消息按此概率回，60% 适中活跃）
        "random_count": 6,
        "all_count": 30,
        "past_window_min": 30,        # 历史上下文只带最近 N 分钟（0=不限，防回应很久前的旧艾特/旧话题）
        "unified_tier": True,         # true=上方档位对所有群生效；false=可按群单独设置（group_tier）
        "group_tier": {},             # {群名: 1~4} 仅 unified_tier=false 时生效；未设置的群跟随全局
        "group_blocklist": {},        # {群名: [昵称, wxid...]} 被屏蔽群员：不存档、不触发、不进提示词
        "sticker_level": 0,           # 表情包积极度 0~3：不鼓励/偶尔/较积极/爱好者（提示词引导）
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
        "share_across_groups": False, # true=所有群共享一个记忆池（群间互通）；false=每群独立（默认）
        "shared_groups": [],          # 可选：只在这几个群间共享记忆（填群名；比全共享更精准，需勾选下方群）
    },
    # ── 反应评分引擎（v1：正反馈 + 种子库，让机器人越聊越有趣）──────────
    "scoring": {
        "enabled": True,              # 本地正反馈评分（零 token，防饱和）
        "seed_library": True,         # 内置有趣种子库（few-shot 参考）
        "online_scoring": False,      # 可选：每次 reaction 后调 LLM 打分（费 token，默认关）
        "heat_decay": True,           # 热度衰减（老梗降权，防饱和）
        "import_seed_file": "",       # 可选：从金句墙导出的 JSON/文本导入种子库
    },
    # ── 社区分享（本地导出 + 可选上传 URL，默认关）──────────────────────
    "community": {
        "export_dir": "exports",      # 导出目录（金句/意见/聊天记录落地文件）
        "holyshits_upload_url": "",   # 可选：金句上传接收端 URL（留空=仅本地导出）
        "feedback_upload_url": "",    # 可选：意见反馈上传接收端 URL
        "upload_enabled": False,      # 总开关：关闭时一律只本地导出
    },
    # ── 界面 ───────────────────────────────────────────────────────────
    "ui": {
        "coord_scale": "auto",        # 显示缩放 auto | 1.25 等
        "clean_overlays": True,       # 点击前清遮挡
        "poke_degraded": False,
        "poke_fail_count": 0,
        "theme": "whale",             # 主题：whale（默认鲸落深海）| light | dark | system
        "whale_cursor": True,         # 鲸鱼指针光标（点击时向下点头）
        "cursor_image": "",           # 自定义光标图片名（assets/custom-cursor.png 或留空=默认鲸鱼 22）
        "whale_anim": {               # 拖拽返回动画时长系数（倍率；1=标准；距离×系数=毫秒）
            "worm": 1.0,              # 蠕动（最慢）：速度系数，距离每 100px ≈ 260ms×系数
            "plane": 1.0,             # 纸飞机（较快）：距离每 100px ≈ 170ms×系数
            "zap": 1.0,               # 扎入（距离自适应，含 0.5s 消失+0.3s 冒出）：每 100px ≈ 90ms×系数
        },
        "moments_entry": "",          # 朋友圈入口坐标 "x,y"（渲染区相对；留空=自动尝试；各电脑校准一次）
        "obscure_url": False,         # 地址栏乱码化（默认关）：进入页面把路径换随机乱码，防复制 URL 登入
        "wave_fx": {                  # 水光波纹（鼠标投石入水效果；控制台可调）
            "enabled": False,         # 总开关（默认关——需用户在「🌊 水光波纹」卡手动开启）
            "scale": 17,              # 扭曲强度（feDisplacementMap 基础位移）
            "speed": 5.2,             # 基础波速 rad/s
            "mouse_gain": 0.02,       # 鼠标拖动提速增益（拖动越快波光越快）
            "max_gain": 8.0,          # 鼠标提速上限 rad/s
            "radius": 260,            # 透镜覆盖半径 px（影响范围；空白处回退用）
            "falloff": 4.0,           # 环带衰减指数（越大边缘衰减越强，越不影响阅读）
            "rings": 1,               # 可见波纹环数（1=单环一波接一波，时间差清晰）
            "ring_speed": 0.40,       # 波纹环扩散速度（0.1~1.5；一圈≈2.5s 肉眼可见一波荡开）
        },
    },
    # ── Web 控制台（浏览器里改设置 / 看状态 / 看日志 / 测试 API）────────
    "server": {
        "enabled": True,              # 是否启动 Web 控制台
        "host": "127.0.0.1",          # 只监听本机
        "port": 3210,                 # 端口被占用会自动顺延
        "token": "",                  # 访问口令；留空 = 启动时自动生成一串随机口令（保存回 config.json）
        "auto_open_browser": True,    # 启动后是否自动打开控制台
        "browser_path": "",           # 指定浏览器 exe（如 QQ/Edge/Chrome 路径）；留空=自动探测或系统默认
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
