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
        "provider": "bing",                 # 默认 DeepSeek 优先；显式选第三方才用该引擎（zhipu/bocha/baidu/metaso/custom）
        "google_first": True,               # DeepSeek 不可用时，免费搜索 Google 优先（再回退 Bing）
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
    #   allow_private_image_hosts：允许「在线图源」指向内网/环回地址（默认关＝SSRF 防护全开）。
    #   只有自建图库服务（本机 http 图源）才需要开；**故意不做控制台开关**，要放开请手改 config.json。
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
        # 大图自动压缩（对账清单第 22 条）：发送前按"最长边 / 文件大小"双阈值压一压，见 agent/img_compress.py。
        #   默认**开**：这是"省事"型能力（不改语义、压不动就原样发并说明），关掉也不会更安全。
        "image_compress": {
            "enabled": True,
            "max_mb": 8.0,         # 超过这个大小就压
            "max_px": 1600,        # 最长边上限
            "quality": 82,         # JPEG 质量
        },
        # 转发「文件/视频」必须过一次系统「选择文件」对话框（会短暂抢前台一次）⇒ 与最高目标冲突，
        # **默认关**：开了模型才允许调 forward_media 转发文件/视频；链接转发不需要它（走文本投递，纯后台）。
        "file_forward_optin": False,
    },
    # ── 随机图（机器人想'发张图'时用；默认关：开了才会真的发图）────────────────
    #   mode=local：从本地图库随机挑一张（**零出网**，推荐）；mode=api：从公开图源接口取一张再发。
    #   发送走 send_image 本地直发（投递档，不动鼠标）；风险闸门与会话头闸照常生效。
    "image_reply": {
        "enabled": False,            # 总开关（控制台可切）
        "mode": "local",             # local＝本地图库 | online＝从图源取（pixiv 等）| api＝单个自定义接口
        "dir": "assets/anime",       # 本地图库目录（相对项目根；可填绝对路径）
        "api_url": "",               # mode=api 时的单个接口地址（留空＝不联网）
        "api_timeout_ms": 8000,
        # ── 在线图源（mode=online）：按顺序尝试，取不到就换下一个 ──────────────────
        #   pixiv＝经公开代理接口取 Pixiv 作品（**强制 r18=0**）· konachan/yande＝强制 rating:safe
        #   safebooru/nekos＝全年龄站 · waifu＝只走 waifu.pics 的 /sfw/ 端点
        "sources": ["pixiv", "safebooru", "nekos", "konachan", "waifu", "yande"],
        "sources_per_try": 4,        # 一次最多试几个图源（每张都要过过滤链）
        "tag": "",                   # 可选：给 pixiv 图源加个偏好标签（如 "风景"）
        "allow_search": True,        # 允许模型**按请求里的关键词**去找图（send_image_search）；false 就只能用本地图库
        "trigger_mode": "on_request",  # 触发条件（交给用户自定义）：on_request＝只被点名要图时才发 | sometimes＝偶尔主动发 | off＝不主动
        # ── 过滤（纵深防御：任何一道说不行就不发；细节见 agent/image_filter.py）──
        "safe_only": True,           # 只允许安全分级（限制级一律拒）
        "allow_questionable": False, # 是否放宽到"暧昧级"（默认否，不建议开）
        "tag_blacklist": [],         # 空＝用内置黑名单（r18/explicit/nsfw/hentai/エロ/裸/色情/福利/guro/loli…）
        "skin_max_ratio": 0.45,      # 肤色像素占比上限（本地启发式；0＝关）
        "min_side": 300,             # 最小边长（太小的图丢掉）
        "vision_filter": True,       # 发之前让视觉模型再看一眼（SAFE/UNSAFE；不确定按 UNSAFE 处理）
        "vision_fail_open": False,   # 视觉审核失败时是否放行（默认否＝宁可发不出）
        # ── 其它 ────────────────────────────────────────────────────────────
        "max_mb": 8,                 # 单张图大小上限（超过就跳过，不发）
        "min_gap_seconds": 20,       # 同一会话两次"随机图"的最小间隔（防刷屏）
        "avoid_recent": 30,          # 记住最近发过的 N 张，尽量不重复
        "include_gif": True,         # 是否把 .gif 也算进图库
    },
    # ── 语音转文字（本地、离线、零下载优先；默认关）─────────────────────────
    #   链路：微信语音 .silk →[pilk / silk_v3_decoder]→ WAV →[Windows 内置 SAPI 听写]→ 文本。
    #   **探不到引擎就如实报「没有可用引擎」，绝不假装识别过**（实现与实测见 agent/voice.py）。
    "voice": {
        "enabled": False,            # 总开关（控制台可切）
        "engine": "auto",            # auto＝按实测可用性自动挑 | sapi＝只用 Windows 内置 | off＝关
        "dir": "media/voice",        # 音频落地目录（相对项目根）
        "max_seconds": 60,           # 单条识别时限（SAPI 听写不适合长音频）
        "keep_audio": False,         # 识别后是否保留中间 wav（默认只留 .silk）
    },
    # ── 语音回复（TTS；默认关）─────────────────────────────────────────────
    #   合成＝Windows 内置 SAPI（零下载，本机实测有中文女声）；**发出去的是「音频文件」不是微信语音条**
    #   （驱动库没有"把任意音频发成语音条"的接口）。真语音条＝虚拟麦克风 + 微信录音按钮，属待拍板项。
    "voice_reply": {
        "enabled": False,        # 总开关（控制台可切）
        "engine": "sapi",        # 目前只有 sapi（在线 TTS 预留位）
        "voice": "",             # 指定声音（子串匹配；留空＝优先中文声音）
        "rate": 0,               # 语速 -10~10（0＝默认）
        "format": "mp3",         # mp3（有 ffmpeg 时转）| wav（无 ffmpeg 自动回落 wav）
        "dir": "media/tts",      # 合成产物目录
        "max_chars": 120,        # 单条合成上限（太长又慢又不合适）
        "min_gap_seconds": 30,   # 同一会话两条相同语音的最小间隔（防刷屏）
        "trigger_mode": "on_request",  # 触发条件（交给用户自定义）：on_request＝只被要求时才发 | sometimes＝偶尔主动 | off＝不主动
    },
    # ── 本地文件搜索（「把某个文件发给我」；默认关）─────────────────────────────
    #   机器人可以自己找文件 — 但**只在你配的目录里找**（默认空＝不搜），而且**发出去**另过
    #   send.file_forward_optin（发文件要过一次系统对话框、会短暂抢前台 ⇒ 默认关）。
    "file_search": {
        "enabled": False,        # 总开关（控制台可切）
        "dirs": [],              # 可搜目录（绝对路径或相对项目根）；空＝不搜
        "max_results": 20,       # 最多返回几个候选
        "max_mb": 100,           # 单文件大小上限（超过就拒绝发送）
        "trigger_mode": "on_request",  # 触发条件：on_request＝被要求时才找 | sometimes＝偶尔主动 | off＝不主动
    },
    # ── 用户自定义工具（声明式 HTTP 工具；默认关）────────────────────────────
    #   用户往 `tools.d/*.json` 丢清单，**勾选后**才给模型用。只发 HTTP、**不跑本地代码**、域名白名单必填、
    #   参数必须是合法 JSON Schema；坏清单不静默（收集问题给控制台）。实现在 agent/user_tools.py。
    "user_tools": {
        "enabled": False,        # 总开关（控制台可切）
        "dir": "tools.d",        # 清单目录（相对项目根）
        "max_tools": 30,         # 最多加载几个
        "timeout_ms": 8000,      # 单次请求超时
        "max_chars": 4000,       # 结果截断
    },
    # ── 群友要图：按需求生成（设计稿 docs/设计-群友要图-生图链条.md；实现在 agent/image_gen.py）──
    #   七段链条＝触发→意图解析→挑后端→生成→**可插拔过滤链**→发送→回执；红线硬编码（不做真人换脸、无 r18 入口）。
    #   backends 在控制台是一个输入框，格式 `id | local/online | url`，多个用分号分隔（image_gen.cfg() 负责解析）。
    "image_gen": {
        "enabled": False,                # 总开关（默认关）
        "trigger_mode": "on_request",    # on_request＝被要求时 | sometimes＝偶尔主动 | off＝不主动
        "max_count": 2,                  # 单次最多生成几张
        "size_default": "square",        # square | portrait | landscape
        "online_allowed": False,         # 允许出网到在线生图 API（默认关 ⇒ 在线后端根本不会被选中）
        "backends": "",                  # `id | local/online | url`，分号分隔
        "style_allow": "",               # 风格白名单（逗号分隔；非空＝只放行这些）
        "style_block": "",               # 风格黑名单（逗号分隔；命中即拒）
        "filter_chain": {"size": True, "dup": True, "blacklist": True, "text": True, "classifier": True},
    },
    # ── 输入后端（最高目标「全程后台、不抢鼠标」的档位；实现与实测证据见 agent/input_backend.py）──
    #   auto＝有微信主窗就走投递（L5），找不到窗口退回真鼠标（L0）；message＝强制投递；real＝强制真鼠标
    "input": {
        "backend": "auto",
        "press_ms": 60,      # 投递点击的按住时长（ms）
        "activate": True,    # 点击前发 WM_ACTIVATE/WM_NCACTIVATE 伪激活（让目标自认为被激活，不改前台窗口）
    },
    # ── 风险闸门（重心＝内容/任务层；细节与「误报来源/用户可见面/弹窗文案」见 agent/risk.py）──
    # 口径（用户 2026-09-13）：节奏没必要掐那么死，"只要限制发送的内容，或者某些任务不做就行"；
    #   "全局节奏、会话节奏应该可以交给用户自定义，让他们自己把控账号的风险"（介绍里会声明）。
    # ⇒ 所有频率类阈值默认 0＝不限，只作为用户自控的旋钮；默认把关的是内容与任务层。
    "risk": {
        "enabled": True,
        "paused": False,             # 停机开关（控制台可一键暂停/恢复）
        "per_minute": 0,             # 0 = 不限
        "per_hour": 0,
        "per_day": 0,
        "per_chat_per_hour": 0,
        "min_gap_seconds": 0,        # 0 = 不限
        "quiet_hours": [],           # 例 [22, 7]；空 = 不启用夜间静默
        "max_links": 3,              # 超过只在控制台记录（L3），不拦
        "watch_keywords": [],        # 命中只记录
        "block_keywords": [],        # 命中直接拦（默认空，用户按自己的红线填）
        "broadcast_chats": 3,        # 同一内容窗口内发给 ≥N 个不同会话 ⇒ 判群发（任务层红线）
        "broadcast_window_seconds": 300,
        "escalate_after": 0,         # 0 = 不自动暂停
        "dup_window_seconds": 120,
        "dup_min_len": 8,
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
        "past_floor_count": 8,       # 时间窗外至少保留最近 N 条（防止长时间静默后看不到上文；0=关闭兜底）
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
        "share_across_groups": False, # true=所有群共享一个记忆池（群间互通）；false=每群独立（默认）
        "shared_groups": [],          # 可选：只在这几个群间共享记忆（填群名；比全共享更精准，需勾选下方群）
    },
    # ── 反应评分引擎（v1：正反馈 + 种子库，让机器人越聊越有趣）──────────
    "scoring": {
        "enabled": True,              # 本地正反馈评分（零 token，防饱和）
        "seed_library": True,         # 内置有趣种子库（few-shot 参考）
        "online_scoring": False,      # 可选：每次 reaction 后调 LLM 打分（费 token，默认关）
        "heat_decay": True,           # 热度衰减（老梗降权，防饱和）
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
        "theme": "whale",             # 主题：whale（默认鲸落深海）| light | dark | system
        # ── 背景（默认＝海：实拍海浪 assets/wallpaper/ocean1.jpg + CSS 波浪；鲸鱼主题另有视频壁纸）──
        #   用户可在控制台「界面适配 → 自定义背景」上传图片/视频；留空＝保持默认的海。
        "background": "",             # ""＝默认海 | "custom"＝用上传的背景（bg_type 决定是图还是视频）
        "bg_type": "image",           # image | video（上传时自动写）
        "whale_cursor": True,         # 鲸鱼指针光标（点击时向下点头）
        "cursor_image": "",           # 自定义光标图片名（assets/custom-cursor.png 或留空=默认鲸鱼 22）
        "whale_anim": {               # 拖拽返回动画时长系数（倍率；1=标准；距离×系数=毫秒）
            "worm": 1.0,              # 蠕动（最慢）：速度系数，距离每 100px ≈ 260ms×系数
            "plane": 1.0,             # 纸飞机（较快）：距离每 100px ≈ 170ms×系数
            "zap": 1.0,               # 扎入（距离自适应，含 0.5s 消失+0.3s 冒出）：每 100px ≈ 90ms×系数
        },
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
