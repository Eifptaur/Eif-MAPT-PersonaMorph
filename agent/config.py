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
        "model": "deepseek-flash",   # 2026-09-14 官方正名（V4.1-Flash，支持视觉）；旧名 deepseek-v4-flash-vision-exp 仍被接受但已退役
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
        "fallback_models": [],       # 备选模型（第 3 条）：主模型失败时按顺序逐个改用；空=关闭；最多 3 个
        "model_routes": {            # 图/文/视频分流选模型（第 20 条）：留空＝用主模型（默认行为不变）
            "text": "",              # 纯文字请求
            "image": "",             # 带图请求（群友发图、看图工具、视频抽帧）
            "video": "",             # 视频链路专用；留空＝跟 image 同一个
        },
    },
    # ── 微信接入（源自 wechat 机器人整合包）────────────────────────────
    "wechat": {
        "bot_nickname": "群deepseek",       # 机器人微信昵称（群里 @ 这个名字触发）
        "group_name_white_list": [],        # 允许回复的群名列表；空 = 所有群
        "poll_interval": 3,                 # 消息轮询间隔（秒）
        # 「自己发的消息」回声判定窗（秒，默认 120）。库回读到自己的话不算别人的话，靠它 + 文本比对。
        # 2026-09-18 由 30 秒放宽到 120：现场出过「机器人跟自己吵了 8 条」——发出到回读之间夹着
        # 发图/切会话/OCR，超过 30 秒就被当成别人的话 ⇒ 回自己。调小＝更激进地当"别人"，调大＝更保守。
        "echo_window_s": 120,
        "rate_limit_per_minute": 20,        # 每分钟最大调用上限（工具触发兜底）
        "media_dir": "media",               # 下载图片保存目录（相对项目根）
        "db_dir": "",                       # 微信数据库目录（留空自动探测）
        "minimize_warning": True,           # 提醒「最好别最小化」微信窗口（日志/控制台）
        "restore_minimized": True,          # 主窗被最小化时**不激活地**还原（不动光标、不打扰你（可能短暂置前约 1~3 秒后自动还回））后再干活；关掉＝如实拒绝
        # 窗口尺寸规整（2026-09-17 用户当面投诉「你为什么老是把我的窗口改得那么大…限位不是让你把窗口限大限小一点」）：
        #   shrink_only（默认）＝**只在你窗口过大时缩到标定尺寸，绝不放大**（你调小了就保持你的尺寸）；
        #   force              ＝老行为：双向强制拉到标定尺寸 1160×900（副作用＝每次都会把你调小的窗口放大回来）；
        #   off                ＝完全不碰你的窗口（尺寸不合标定，OCR/指纹可能变差）。
        # ⚠️ 标定尺寸的依据：微信侧栏在**高度 <900** 时会把「发现/朋友圈」等图标收进「…」⇒ force 档保证图标常显；
        #    选 shrink_only 时若你把窗口调得较矮，微信自己会收起部分侧栏图标，那不是我们改坏的。
        "limit_window": "shrink_only",
        # 暂停期间收到的消息，恢复后要不要补处理（默认关＝不补）。
        # 关（默认）：暂停期间把水位推到最新**并落盘** ⇒ 恢复时**不重放**那批积压（否则恢复瞬间
        #   会把暂停期间的几十条旧消息逐批触发，表现为"每条都回"）。
        # 开：暂停期间**不推进水位** ⇒ 恢复后按水位把积压消息补上（长暂停会产生一阵集中回复）。
        "replay_on_resume": False,
        "start_paused": True,               # 启动后默认暂停（控制台点「恢复」才开始监听）
        # 盲试点击（默认关）：侧栏图标找不到"自证"时，是否允许按图标列/比例猜位置真点几下。
        # 2026-09-14 用户实测：「它在乱点我的头像、联系人、收藏，但是唯独没点朋友圈里的发现」
        # ⇒ 默认关：宁可如实报"没自证到"，也不拿用户的界面乱试。
        "allow_click_hunting": False,
        # 只走后台（默认关）：真鼠标档的路径（拍一拍 / 引用 / 朋友圈点赞·评论·发表 / 标定）
        # 一律**跳过并如实说明**，绝不悄悄动你的光标。后台能力矩阵见 agent/bg_status.py。
        # 只走后台（**2026-09-16 默认改为 True** —— 已知现象：「为什么鼠标会滑我的控制台」：
        #   原来默认 False ＝ 默认允许那 5 条真鼠标路径执行，它们是裸的 SetCursorPos + mouse_event，
        #   没遮挡校验，会把点击打到"当前最上面的窗口"（可能就是用户的控制台）。
        #   ⇒ 按最高目标「不动鼠标」把默认改成安全的一侧：要用这 5 条（拍一拍 / 引用 /
        #     朋友圈点赞评论 / 发朋友圈纯文字 / UI 标定）必须显式打开本开关。
        #   注意与 `input.allow_real_fallback` 的区别：那个管的是"投递自检不过时**退不退回**真鼠标"，
        #   本开关管的是"这条路径**本来就走**真鼠标时要不要执行"。
        "background_only": True,
        # 搜索框切会话没成时，要不要**退回「在会话列表里找行 + 滚轮」那条老路**（默认 False＝不回退）。
        # 2026-09-16 已知现象：「他点了一下搜索框，又不点，又搁那划会话列表」⇒ 老路的滚轮虽然**不动光标**，
        # 但**会话列表会在用户眼前滚**，他看到的"它在划"就是它。⇒ 默认**停手**并如实说明；
        # 想让成功率优先（列表在屏幕上动几下无所谓）就打开本开关。
        "scroll_list_fallback": False,
        # ⚡ 2026-09-19：**发表情方式**（作者要求「做成可选项，让他自己收藏表情还是发图片，把代价也写清楚」）：
        #   · "auto"（默认）＝先走微信表情面板（真表情），面板不通就改发图片；
        #   · "real" ＝只用真表情面板 —— 它是"浮层"，**必须被激活才能渲染** ⇒ 那一下前台会闪
        #              （实测约 2~7 秒），而且与"摁住微信"冲突（得临时松手）；
        #   · "image" ＝只用图片通道（剪贴板 + 输入框右键「粘贴」）—— **不需要浮层、全程能摁住**
        #              （实测 7.2 秒发出、微信占前台 0.05 秒），代价＝**对方收到的是图片**，
        #              动态表情会变成静态首帧。
        "emoji_send_mode": "auto",
        # 我的其他账号（2026-09-16 已知现象：「这个是用的我的小号 他无法识别我的大号 之前版本也有这个问题」）：
        #   机器人跑在**小号**上，而**主人的大号**在群里说话时，程序原先把大号当**普通群友**
        #   （会去回你自己的话）。这里登记"哪些账号是我自己"：**wxid 最准、优先匹配**，
        #   昵称做兜底（控制台会把匹配到的账号列出来让你核对，认错一眼能发现）。
        "owner_accounts": [],
        # 认出主人之后的反应（既有口径：**映射到 UI 上让用户自己选**）：
        #   off            = 不启用这个机制
        #   skip           = 完全不回复主人自己那些号的消息
        #   owner_at_only  = **只在群里 @ 它或引用它的时候才回**（其余不回；私聊不受影响）—— 2026-09-16 新增
        #   know           = 照常回复，但让模型知道"这是主人"（不当陌生群友对待）
        "owner_mode": "know",
        # 私聊（2026-09-16 用户：「他也许是那种私聊的想法，**大号跟小号对谈**，相当于借一个智能体
        #   进来跟自己聊天，这个应该也可以做吧」）：
        #   off        ＝不监听私聊
        #   owner_only ＝**只**监听「我的其他账号（大号）」那些号发来的私聊（推荐、默认）
        #   all        ＝所有私聊都监听（慎用：会给陌生人回消息）
        "private_chat": "owner_only",
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
                     "model": "deepseek-flash", "timeout_ms": 60000},
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
        # 兜底自动补发的过滤（2026-09-15 加；不过滤时模型把"内心分析"写进最终文本会被原样发进群）
        "fallback_autosend": True,      # 兜底总开关：模型没调发送工具时，把最终文本当回复发出去
        "fallback_max_chars": 60,       # 兜底只发短话（长文多半是分析）；0=不限
        "fallback_block_selfref": True, # 拦掉"我不打算回 / 没什么可说"这类自我指涉（内心判断不该进群）
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
        # 图源顺序＝"先试谁"（2026-09-18 改：**国内源排最前** —— 用户口径「国内的相对来说通路会比较
        # 好的吧，也不容易被 ban」；实测必应 0.5~2.7 秒出图、360 0.8 秒，而国际站夜里常 403/超时）。
        # ⚠️ 百度/搜狗图片**实测会被判爬虫**（要 Cookie）⇒ 没放进来；要接得自己做 Cookie 池。
        "sources": ["so360", "bing", "wallhaven", "danbooru",
                    "pixiv", "safebooru", "nekos", "konachan", "waifu", "yande"],
        # 有人**按关键词**要图时，在线没取到要不要拿"以前下载过的旧图"顶？默认**否**（如实说没找到）。
        # 2026-09-18 定：以前默认兜底，还谎称"已找到并发出一张「鲸鱼」的图" ⇒ 用户收到一张毫不相干的
        # 动漫图（对外可见的错，比"没找到"更糟）。要旧行为就把它设 true（那也只适合"随机图"场景）。
        "cache_fallback": False,
        "sources_per_try": 4,        # 一次最多试几个图源（每张都要过过滤链）
        # ── 速度（2026-09-17 加；起因＝用户实测"发张图一分多钟"）────────────────
        "meta_timeout_ms": 6000,     # 向单个图源要"图片地址"的超时（并行问，实际耗时≈最慢那个）
        "download_timeout_ms": 6000, # **整张图**的总时长上限（原来 socket 超时只管单次 recv，
                                     #   慢速代理能涓流几分钟 —— 实测一张 4.26MB 拖了 160 秒）
        "download_race": 4,          # 候选图**并发赛跑**几个（谁先下完并通过过滤链就用谁）
        "total_budget_ms": 12000,    # 这一次"要图"总共允许多久，用完就用本地已有的图兜底/如实说没找到
        "source_cooldown_s": 600,    # 图源失败后冷却多久不再试（四个死源曾每次白等 27 秒）
        "slow_cooldown_s": 120,      # 下载超时的图源冷却多久（慢，但没死）
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
    # ── 更新（本体 + DLC 的公告系统；2026-09-15 加）─────────────────────────────
    #   url＝线上清单地址（留空＝不检查，界面**什么都不显示**）；muted＝不再提醒（总开关）；
    #   skip_version＝"不再提醒这个版本"。口径：拉不到就静默跳过、清单坏了如实说、
    #   **公告只在本机 UI 出现，绝不往微信侧发**（与风险闸门同一口径）。
    "update": {
        "url": "https://raw.githubusercontent.com/Eifptaur/Eif-MAPT-PersonaMorph/main/persona-morph-manifest.json",  # 默认指向本项目的线上清单（留空＝用内置默认；真要关掉更新检查用 muted）
        "muted": False,          # 不再提醒（总开关）
        "skip_version": "",      # 不再提醒这个版本
        # ⛔ 2026-09-20 修 **V-R3-7（第三轮审计）**：这两个开关是"只信官方域 + 本仓库"那套机制的
        #   一部分，**必须跟机制一起交付**（有默认值、有注释、拒绝文案里点名）——否则在旧版填过
        #   自定义源的用户升级后，会看到"更新源异常"却查不到怎么放行（等于把老路径悄悄切断）。
        "trust_custom_url": False,   # true＝显式信任你自己填的 update.url（自建中转/自选镜像）
        "allow_local": False,        # true＝允许把本地文件当更新源（只建议离线自测用；也可用环境变量 PM_ALLOW_LOCAL_UPDATE=1）
    },
    # ── 真语音条（2026-09-17 用户：「我让他发语音条，**不是发音频文件**」）──
    #   形态区别：`voice_reply` 发出去的是**音频文件**；这一块走"虚拟声卡 + 微信自己录"那条路，
    #   发出去的是**微信语音条**。前提三件：成对虚拟声卡（如 VB-CABLE）、sounddevice、合成引擎。
    #   ⚠️ 位置不用用户标定（2026-09-17 实机后改）：每次发送前**扫输入条图标行现算**那个圆圈，
    #   下面的 record_btn 只当兜底——实测这一行图标会随右侧栏开关左右漂（0.745 ↔ 0.878）。
    "voice_strip": {
        "enabled": True,         # 机制开关（默认开：形态默认就是"真语音条"，前提不齐会如实回退成文件）
        "record_btn": [0.878, 0.943],  # 兜底用的圆圈位置（渲染区比例）；正常走运行时扫描
        "send_btn": [0.932, 0.945],   # 录音态里那个绿簇"发送"（渲染区比例，正常走绿簇检测）
        "out_device": "cable",   # 播到哪块输出设备（名字子串，默认抓 CABLE；Scream 用户可改成 scream）
        "meter_guard": True,     # 播的时候录音音量点一直不亮 ⇒ 取消（不发静音语音条）
        "real_click": True,      # 真点路（进录音态/点发送）会动光标，用完立刻还原
        # 进录音态走哪条路（2026-09-17 实测后定）：
        #   alt   ＝**按住右 Alt**（SendInput 注入）：**不动鼠标、不依赖任何坐标**，代价＝那几秒微信要在前台
        #   click ＝真鼠标点那个圆圈（老路，会动两下光标，用完还原）
        "enter_via": "alt",
        "fallback_click": True,  # Alt 路不成时自动退回真点（个别微信版本不认右 Alt 时不至于发不出去）
    },
    "voice_reply": {
        "enabled": False,        # 总开关（控制台可切）
        "engine": "sapi",        # 旧键（保留兼容）；真正的音源开关是下面的 backend
        # 音源（2026-09-15 起默认 edge）：edge＝edge-tts 免费神经语音（需联网，音质接近真人）
        #                                 sapi＝本机系统声音（离线兜底，机械音）
        #                                 http＝用户自带的模型服务（见 http_url / vc_url）
        "backend": "edge",
        "edge_voice": "zh-CN-XiaoxiaoNeural",  # edge-tts 音色（8 个中文音色见 voice_models.EDGE_VOICES）
        "edge_fallback": True,                 # edge 失败/没网 ⇒ 退回系统声音（关掉就如实报错、不发）
        "voice": "",             # 指定声音（子串匹配；留空＝优先中文声音）
        "rate": 0,               # 语速 -10~10（0＝默认；edge 档按 ×5 换算成百分比，2026-09-17 接通）
        # 语气段加速（2026-09-17 加，用户原话：连续同字是「语气偏快的连读」）：句子里有连续同一个字
        # ≥3（行行行/好好好）时，那几段**单独合成并加速这么多 %**，其余照常，再拼回一句（段间不加停顿）。
        # 0＝关掉这条（就按普通语速一口气念完）。
        "run_boost": 25,
        "format": "mp3",         # mp3（有 ffmpeg 时转）| wav（无 ffmpeg 自动回落 wav）
        "dir": "media/tts",      # 合成产物目录
        "max_chars": 120,        # 单条合成上限（太长又慢又不合适）
        "min_gap_seconds": 30,   # 同一会话两条相同语音的最小间隔（防刷屏）
        "trigger_mode": "on_request",  # 触发条件（交给用户自定义）：on_request＝只被要求时才发 | sometimes＝偶尔主动 | off＝不主动
        # ── 形态（2026-09-17 用户：「给个选项，让用户选是发音频文件还是真发一个语音条，**默认就是发语音条**」）──
        "form": "strip",         # strip＝真语音条（微信气泡）| file＝音频文件
        "fallback_file": True,   # form=strip 但前提不齐（没虚拟声卡/没引擎）时：如实回退成音频文件并说明；false＝干脆不发
        # ── 念法纠正（2026-09-17 用户纠正后定稿：「行行行」是**语气偏快的连读**，不是三个字一顿）──
        #   一行一条「原文=念法」，两种写法：
        #     `行行行=形形形`  —— **同音字替换**（推荐；edge/sapi/自建服务**任何档都生效**）
        #     `行=拼音:xing2`  —— 拼音标注（要音源有音素级入口；目前**没有一档能吃到**，如实不生效）
        #   ⚠️ 我们**不会自动往正文里插标点**：连续同字常是快连读，插逗号＝一字一顿、语气全丢。
        "pronounce": "",
        # ── 变声段（第二段；用户 2026-09-15：「最主要是要兼容那些用户本地的，比方说 GPT-SoVITS 的、RVC 的」）──
        #   GPT-SoVITS = 文字→音频（走上面的 http_url）；**RVC = 音频→音频** ⇒ 要两段串：
        #   文本 →(合成)→ 音频 →(变声)→ 音频。两段都是用户自己的本地服务，内容不出网。
        "vc_url": "",            # 变声服务地址；留空＝不变声（默认：只用第一段）
        "vc_mode": "multipart",  # multipart（表单上传音频文件，RVC 系常见）| base64（JSON 里放内容编码）
        "vc_params": "",         # 原样附在请求里的参数（JSON 文本），如 {"f0_up_key":0,"index_rate":0.7,"speaker":"x"}
        "vc_json_field": "",     # 变声端点回 JSON 时取哪个字段（回音频字节就留空）
        "vc_fail_open": False,   # 变声失败时是否照发未变声的原音（默认否＝不发：「不假装」红线）
        "vc_timeout_ms": 60000,  # 变声（含本地模型推理）通常比合成慢，给足时间
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
        # 🔴 2026-09-18 用户拍板：**默认出网**。原话：「不妨这样吧，默认出网，反正用户出的网也够多了，
        #   也不在生图这一个。我觉得在线生成的图片有没有可能会比这个更好一点？如果用户本地有更好的
        #   模型，再让他自己调」。
        #   实测依据（同一天、同一条提示词）：本地＝**动漫模型 Animagine XL 3.1 + 8 步蒸馏** ⇒ 出写意
        #   动漫图（用户原话「你觉得这是一个猫吗？…写实是完全不行的」）；在线免密钥那条 **3.6 秒**出
        #   **照片级**图。⇒ 默认在线优先、本地当兜底；用户想用本地或另接服务，在控制台自己切。
        "online_allowed": True,
        # 在线后端预设（控制台下拉选；表＝ agent/image_gen.py::ONLINE_PRESETS）：
        #   pollinations＝免密钥零配置（**右下角有水印**，nologo 现在要 token）
        #   siliconflow / zhipu / volc＝OpenAI 兼容、**无水印**，填各自 key 即可（都有免费额度）
        #   custom＝任何 OpenAI 兼容出图接口
        "online_preset": "pollinations",
        "online_api": {"url": "", "key": "", "model": "", "timeout": 120},   # 自定义/填 key 那家
        "online_first": True,            # 在线优先（本地兜底）；关掉＝只走本地
        # 去水印（2026-09-18 用户要求「找找有没有去水印的，把它加到这条链里面」）：
        #   ①带 token / 要 key 的后端**本来就没水印**（智谱 cogview-3-flash 免费、硅基流动有免费额度）；
        #   ②免密钥那条（pollinations）的水印**只有注册免费账号拿 token 才去得掉**（官方 APIDOCS 原话：
        #     `nologo` = Remove the Pollinations watermark **(needs account)**）⇒ 把 token 填进「出图密钥」；
        #   ③两者都没有时，按下面这个开关**裁掉底部水印带**（诚实的修剪，不做局部涂改）。
        "strip_watermark": True,
        "watermark_crop": 0.06,          # 裁掉底部比例（pollinations 水印实测在 y≈0.94~0.99）
        "backends": "",                  # `id | local/online | url`，分号分隔
        "style_allow": "",               # 风格白名单（逗号分隔；非空＝只放行这些）
        "style_block": "",               # 风格黑名单（逗号分隔；命中即拒）
        "filter_chain": {"size": True, "dup": True, "blacklist": True, "text": True, "classifier": True},
        # ── 本地轻量生图后端（2026-09-17 用户口径：「你帮用户装，做成一个可选项；用户选了就弹安装提示；
        #    在线安装看用户开不开；**不要让用户搞这搞那**；要**实时看到进度**（一共多少/下了多少/百分比）；
        #    还要**后台进行**——别让用户盯着弹窗等，可以去办别的事」）──
        #   装什么＝SDXL-Turbo 模型 6.46 GB + torch/diffusers 运行库 ≈4.35 GB，**合计 ≈10.8 GB**；
        #   本机实测：走国内源整条链约 **20 分钟**（模型 25 MB/s、依赖 4.5 MB/s）；
        #   **走被限速的源只有 0.02~0.06 MB/s ⇒ 会变成几十小时** ⇒ 安装前**当场测速**，把预计时间写在弹窗里。
        #   装完自动：写 `backends` + 打开总开关 + 起服务（首次加载模型约 15 秒），用户不用填任何地址。
        "local_sd": {
            "enabled": True,                # 允许走"安装/使用本地轻量后端"这条路（不装也无副作用）
            "port": 7860,                   # 本地服务端口（默认 A1111 的口，产品能自动探到）
            "model_dir": "data/sd_model",   # 模型放哪（相对仓库根；也可写绝对路径）
            "allow_online_install": False,  # **默认关**：不开就不联网下载（要下 10.8 GB，必须用户明确同意）
            "auto_start": True,             # 装了之后：生成前若服务没跑就顺手拉起来（用户零操作）
            "steps": 4,                     # SDXL-Turbo 用 1~4 步
        },
    },
    # ── AI 视频生成（2026-09-15 加；形制照 image_gen）────────────────────────────
    #  口径（既有口径：）："后端肯定是让用户自己选啊，我们给他提供最多的选项…不是非得二择一的"
    #  ⇒ backends 支持**填多个、一行一个**，程序按顺序挨个试；本地与在线都留着。
    #  写法：`http://127.0.0.1:8189/generate`（generic）· `comfyui:http://127.0.0.1:8188`（本机 ComfyUI）
    "video_gen": {
        "enabled": False,                # 总开关（默认关：没配后端时模型会如实说"还没配后端"）
        "trigger_mode": "on_request",    # on_request＝被要求时 | sometimes＝偶尔主动 | off＝不主动
        "backends": "",                  # 可填多个，逗号/换行分隔；`协议:地址` 或直接写地址
        "comfy_workflow": "",            # 走 ComfyUI 时要指一个 API 格式工作流 JSON 的路径
        "online_allowed": False,         # 允许出网到在线视频 API（默认关 ⇒ 在线后端不会被试）
        "seconds_default": 5,            # 默认生成多少秒（硬上限见 agent/video_gen.py::MAX_SECONDS）
        "timeout": 300,                  # 单次生成最长等多少秒（视频比图慢得多）
        "filter_chain": {"size": True, "duration": True, "dup": True, "redline": True, "classifier": True},
    },
    # ── 输入后端（最高目标「全程后台、不抢鼠标」的档位；实现与实测证据见 agent/input_backend.py）──
    #   auto＝有微信主窗就走投递（L5），找不到窗口退回真鼠标（L0）；message＝强制投递；real＝强制真鼠标
    "input": {
        "backend": "auto",
        "press_ms": 60,      # 投递点击的按住时长（ms）
        "activate": True,    # 点击前发 WM_ACTIVATE/WM_NCACTIVATE 伪激活（让目标自认为被激活，不改前台窗口）
        # ⚠️ 投递档确认不了目标会话时，**允不允许退回真鼠标/真键盘（L0）**——默认 **False**（不退回）。
        # 2026-09-16 跨机 r12 事故：对面那台跑我们自己的「一键检验（生成报告）」（**自检工具**）时，
        # 投递切会话失败 ⇒ 自动退回真实路径 ⇒ **动了 16 秒光标**。与最高目标②③直接冲突 ⇒
        # 默认关，要用真鼠标必须在这里显式打开（自检/诊断路径另外用环境变量强制关）。
        "allow_real_fallback": False,
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
        "tier_mode": "fixed",         # fixed=固定 4 档（滑条不参与，第 15 条）| slider=允许滑条连续微调（旧行为）
        "tier_schedule": {            # 峰谷映射（第 16 条）：时段 → 档位；tier=0 表示该时段完全不回应
            "enabled": False,
            # 表内顺序＝优先级（取第一个命中的窗口）；支持跨午夜（如 22:00 → 02:00）
            "table": [{"from": "09:00", "to": "12:00", "tier": 2, "note": "工作时段：只回艾特/关键词"},
                      {"from": "00:00", "to": "08:00", "tier": 0, "note": "夜间静默"}],
        },
        "tier_cmd_admins": [],        # 指令禁言白名单（第 18 条）：昵称或 wxid；**留空＝谁都不能下指令**
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
        "archive_block_chats": [],    # 屏蔽存档的会话（群名或 group:wxid）——整会话消息不入存档（第 10 条）
        "sticker_level": 0,           # 表情包积极度 0~3：不鼓励/偶尔/较积极/爱好者（提示词引导）
        "recall": {                   # 撤回后剔除上下文（第三方 v0.4 对账清单第 14 条）
            "enabled": True,          # 关掉＝撤回事件只记日志、不动存档与记忆
            "window_sec": 180,        # 拿不到 newmsgid 时的兜底时间窗（秒）；只找窗口内最近一条
            "heuristic": True,        # 兜底匹配开关：关掉则只认 newmsgid 精确匹配（宁可不删）
        },
    },
    # ── 上云（预留接口：默认关＝不上传任何数据）──────────────────────────────
    # 既有口径：（2026-09-14）：「上云三件先不做，但是留可以输网址的接口，在 UI 里面也要相应地做一切的交互设计」
    "cloud": {
        "enabled": False,          # ⛔ 总开关：默认关 ⇒ upload() 直接拒绝、一个字节都不发
        "persona_url": "",         # 人设上云的接收端网址（留空＝未配置）
        "blocklist_url": "",       # 屏蔽名单上云的接收端网址（留空＝未配置）
        "token": "",               # 接收端要求的凭据（打码回显；只存本机）
        "auth_style": "bearer",    # 凭据怎么带：bearer＝Authorization 头（判 2xx）｜body_key＝请求体 key 字段（判回包 ok:true）
        "timeout_ms": 8000,
        "allow_private": False,    # 允许环回/内网接收端（默认拒）
    },
    # ── 系统提示词编辑（第 11 条）──────────────────────────────────────────
    # 自定义补充追加在系统提示词**末尾**（落盘即生效，改完下一轮就变、不用重启）；
    # 三个模块开关默认全开；**安全规则与工具协议不可关**（红线，见 agent/system_prompt.py::ALWAYS_ON）。
    "system_prompt": {
        "custom": "",
        "enable_scene_rules": True,
        "enable_memory_rules": True,
        "enable_holiday_hint": True,
    },
    # ── 计时提醒 + 节假日问候（第 12/13 条）────────────────────────────────
    # 红线（既有口径："只允许被动或显式开启"）：定时消息只能设到**当前会话**、到点仍过风险闸门、
    # 每会话/全局都有条数上限；节假日默认只"在提示词里提一句"，主动问候必须显式开启 + 配白名单。
    "timers": {
        "enabled": True,              # 群友在对话里让你提醒 ⇒ 允许（工具仍受条数/时长上限约束）
    },
    "holiday": {
        "mode": "passive",            # off=不提 | passive=只在提示词里提一句（默认，绝不主动发）| active=到点主动问候
        "greet_chats": [],            # active 的白名单会话名（群名 / 文件传输助手）；空＝不主动问候
        "greet_hour": 9,              # active 的起始小时（只在 9~21 点之间发）
    },
    # ── 视频读取（第 20 条）：抽帧给视觉模型 + 本机离线识别音频 ──────────────
    "video_read": {
        "enabled": True,
        "max_frames": 4,              # 默认抽 4 帧（上限 8，见 agent/video_read.py::MAX_FRAMES）
        "max_seconds": 60,            # 音频最多识别这么多秒
    },
    # ── B 站视频（2026-09-15）：群友丢链接时能去查标题/UP/简介/字幕；"听"那段会下音频轨 ──
    "bilibili": {
        "enabled": True,              # 关掉＝模型拿不到这个工具（它会如实说"这功能被关了"）
        "listen_max_seconds": 120,    # "听一遍"最多听多少秒（只下声音那一轨）
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
    # ── 反馈（控制台左侧导航「反馈」一栏；用户填完自动提交，程序整理后发邮件）──────
    # ⚠️ 收件人邮箱 / SMTP 授权码属**个人信息**：只写在本机 config.json（已 gitignore），
    #    代码与 config.example.json 里一律留空——外发包的 PII 扫描闸门也会拦。
    "feedback": {
        "enabled": True,              # 是否在控制台显示「反馈」栏
        "to": "",                     # 收件人（多个用逗号分隔）；留空＝不发邮件
        "upload_url": "",             # 可选：你自己的中转网址（POST JSON，优先于推送）
        # ⚠️ 这几个键**只存在于 config.json**（运维侧配置，不出现在界面上）：
        #   「推送到你」＝填任意一个 webhook 地址即可（钉钉/飞书/企业微信 群机器人、PushPlus 配口令、
        #   或你自己的中转），请求体会按域名自动选形态（见 `feedback._post_webhook`）。
        # 2026-09-17 填入产品自带的反馈接收端（企业微信内部群「消息推送 → 自定义消息推送」机器人）：
        # 已用 `_scratch/check_webhook.py` 实测 `errcode:0`（能发文本、也能 upload_media）⇒ 用户侧**零配置**即可提交。
        "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=c8de1c5e-22d0-4528-9413-43d3d464805b",
        "webhook_token": "",
        "smtp": {
            "host": "smtp.qq.com",    # QQ 邮箱 465 SSL；163 用 smtp.163.com
            "port": 465,              # 465=SSL（推荐）| 587=STARTTLS
            "user": "",               # 发件邮箱（如 xxx@qq.com）
            "password": "",           # **授权码**（不是登录密码）
        },
        # ── 防刷限流（2026-09-15 用户要求：「要是有人一瞬间给我发 100 封怎么办」）──────
        # 数值依据：正常用户一天写 2~3 条 ⇒ 留约 6 倍余量；真被刷时收件箱最多 20 封/天。
        # ⚠️ 咽喉点：控制台表单 / HTTP 接口 / 机器人工具都走 `feedback.submit()`，改一处全生效。
        #    被拦下的**不落盘**（防撑爆存档），只在 data/feedback_rejected.jsonl 留一行痕迹。
        "limit": {
            "per_minute": 3,          # 同一分钟内最多 3 条（0=不限）
            "per_hour": 10,           # 同一小时内最多 10 条
            "per_day": 20,            # 同一天最多 20 条
            "dup_window_s": 600,      # 同内容 10 分钟内只发一次
        },
    },
    # ── 界面 ───────────────────────────────────────────────────────────
    "ui": {
        "coord_scale": "auto",        # 显示缩放 auto | 1.25 等
        "clean_overlays": True,       # 点击前清遮挡
        # ── 后台纪律（2026-09-14 用户实测反馈后立的两个默认值）──────────────
        # 已知现象：「我一打开它，它会把我的微信窗口切出来，还会乱动我的鼠标…还会导致微信卡死」
        # ① 绝不主动摆弄用户的微信窗口（不移动/不缩放/不还原最小化）——默认关；
        # ② 绝不为了点击把微信抢到前台——默认关（需要前台的旧路径会**如实报"跳过"**而不是硬来）。
        "lock_window_pos": True,     # 「限位」：把微信主窗摆到固定尺寸/位置（默认**开**——用户 2026-09-16
                                 # 口径：「用户在后台都不在意这个，而且也能防止点错」；关掉＝绝不移动你的窗口）
        # ③（2026-09-15 用户拍板方案 A）**借来的窗口用完要还**：`_limit_wechat_window()` 为了不让
        #    驱动库的布局校准失效，每次取 GUI 会把主窗钉到 1160×900（把他手动拉过的尺寸改掉）⇒
        #    现在改成「借 → 用完（空闲 IDLE_S 秒）自动还回原 rect」，详见 agent/window_borrow.py。
        "restore_window_after_use": True,   # 默认开：动过的窗口几何，用完自动还原
        "allow_foreground": False,    # 是否允许把微信置前（默认关：抢前台＝打扰用户，属最高目标禁止项）
        "theme": "whale",             # 主题：whale（默认鲸落深海）| light | dark | system
        # 余额的**显示伪装**（2026-09-16 用户要求：「有没有一键隐藏剩余金额功能或者一键修改剩余金额功能…
        # 可以在界面显示上把金额改掉，但是实际上还是那么多」）⇒ 这是**显示层**的事：
        # `real`＝照实显示 / `hide`＝显示成"已隐藏" / `fake`＝显示成 balance_fake 填的数字。
        # **任何情况下都不改真实余额**（查询与账目照旧）。
        "balance_display": "real",
        "balance_fake": "",
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


_BOOL_STRINGS = {"true": True, "false": False, "yes": True, "no": False, "on": True, "off": False,
                 "1": True, "0": False}
# 载入归一化**只认这两个**（`"1"`/`"0"`/`"on"`/`"off"` 可能是真的字符串值 ⇒ 不许在载入时乱转）
_COERCE_STRINGS = ("true", "false")


def as_bool(v, default: bool = False) -> bool:
    """把「开关」读成布尔：**字符串 `"false"` / `"no"` / `"off"` 都是假**。

    ⛔ 2026-09-21（第四轮审计 **V-R4-13，P3**）：`config.json` 是人手改的，写成 `"false"`（带引号）
    完全可能；而全项目有几十处 `bool(cfg.get("某个开关"))` —— `bool("false")` 是 **True**
    ⇒ 开关被**反向打开**（本该关掉的红线开关反而开了）。⇒ 两件事：
    ①`get_config()` 载入时**统一归一化**（见 `_coerce_bool_strings`）—— 一处生效，不必改那几十处；
    ②**新代码读开关一律用本函数**，别再写裸 `bool()`。
    """
    if isinstance(v, bool):
        return v
    if v is None:
        return bool(default)
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in _BOOL_STRINGS:
        return _BOOL_STRINGS[s]
    return bool(s)


def _coerce_bool_strings(node):
    """把配置里**写成字符串的开关**归一成真布尔 —— 只认 `"true"`/`"false"`，且**只走字典**。

    为什么这么窄：`"1"`/`"0"`/`"on"`/`"off"` 都可能是**真的字符串值**（型号名、屏蔽关键词…），
    贸然转换会改坏别的语义；而 `"false"` 当字符串用**没有任何理由** ⇒ 只转它。
    **列表一律不动**（例：`risk.block_keywords` 里真可能出现 `"off"` 这种词，转了就坏）。
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if isinstance(v, str) and v.strip().lower() in _COERCE_STRINGS:
                out[k] = (v.strip().lower() == "true")
            else:
                out[k] = _coerce_bool_strings(v)
        return out
    return node                                     # 列表/标量原样返回


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
    # 只对**真正那份** config.json 做一次性迁移（带自定义 path 的调用多半是自检/夹具，
    # 绝不能把关卡写回用户真实的那份）
    try:
        if os.path.abspath(path) == os.path.abspath(CONFIG_FILE):
            cfg = _migrate_once(cfg)
    except Exception as e:
        print("[config] 一次性迁移跳过：%s" % e)
    # ⛔ V-R4-13：把"写成字符串的开关"（`"false"`）归一成真布尔 —— 一处生效，
    #   全项目几十处 `bool(cfg.get("开关"))` 于是自动正确（否则它们会把开关**反向**打开）。
    try:
        cfg = _coerce_bool_strings(cfg)
    except Exception as e:
        print("[config] 开关归一化跳过：%s" % e)
    return cfg


_current_config: dict | None = None
_config_stamp: tuple | None = None       # 内存里这份配置对上的**磁盘指纹**（mtime_ns, size）


def _stamp(path: str):
    """磁盘上那份配置的指纹；读不到（还没写过/无权限）返回 None。"""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def get_config() -> dict:
    """当前配置。**配置一变就作废**：盘上那份的指纹跟内存里这份对不上 ⇒ 直接重读。

    为什么要有这一步（2026-09-18 用户反馈：「他回我之前自定义的地址里去看文件了」）：
    原来只有 `set_config()`（控制台 `/api/config` 保存）这一条路会换掉内存里那份；
    **手工改 `config.json`、或另一个进程写的值，跑着的这个进程永远看不到** —— 旧值就一直生效。
    指纹比对只是一次 `os.stat`，比"整个进程重启一次"便宜得多。
    """
    global _current_config, _config_stamp
    if _current_config is None:
        return reload_config()
    if _stamp(CONFIG_FILE) != _config_stamp:
        return reload_config()
    return _current_config


def reload_config(path: str | None = None) -> dict:
    """**显式的失效入口**：丢掉内存里那份，从磁盘重读一遍，并记下新的指纹。"""
    global _current_config, _config_stamp
    p = path or CONFIG_FILE
    _current_config = load_config(p)
    _config_stamp = _stamp(p)
    return _current_config


def set_config(cfg: dict) -> None:
    global _current_config, _config_stamp
    _current_config = cfg
    # 记下此刻盘上的指纹：紧接着的 `save_config()` 会把它改掉，下次 get_config 比一次
    # （比中就重读同一份内容，不会把刚设进去的值冲掉）。
    _config_stamp = _stamp(CONFIG_FILE)


def save_config(cfg: dict | None = None) -> None:
    cfg = cfg or get_config()
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


# ── 一次性迁移：把「安全默认值」补到**已存在**的 config.json 上（2026-09-16 立）─────────
#   为什么必须单做一件事：`deep_merge(DEFAULT_CONFIG, config.json)` 是**用户文件覆盖默认值**，
#   而在线包**不带 config.json、更新也不覆盖用户那份** ⇒ 把某个默认值改成"安全的一侧"
#   对老用户**完全无效**。2026-09-16 实测踩到：v2.1.5 把 `wechat.background_only` 默认改成
#   True（"不再动你的鼠标"就是这条），可用户那份 config.json 里早写着 `false`
#   ⇒ 他升级后那几条真鼠标路径照旧执行、鼠标照旧会滑到他的控制台上。
#   ⇒ 规则三条：①**只做一次**（标记记在 data/ 里，用户之后怎么改都不再动他）；
#     ②**只往安全的一侧搬**，并在日志里说清搬了什么；③标记落 `data/`（运行期数据，不进包）。
MIGRATIONS_MARK = os.path.join(DATA_DIR, "config_migrations.json")
_SAFE_DEFAULTS_TAG = "safe_defaults_2026_09_16"
_WINDOW_POS_TAG = "lock_window_pos_2026_09_16"     # 「限位」默认开（2026-09-16 既有口径：）


def _safe_defaults(cfg: dict) -> list:
    """把安全默认值补上；返回「改了哪些」的人类可读列表（没改就是空表）。"""
    changed = []
    w = cfg.setdefault("wechat", {})
    if w.get("background_only") is not True:
        w["background_only"] = True
        changed.append("wechat.background_only = true（真鼠标档默认不执行：不动你的鼠标）")
    i = cfg.setdefault("input", {})
    if i.get("allow_real_fallback") is not False:
        i["allow_real_fallback"] = False
        changed.append("input.allow_real_fallback = false（投递判据不过时不退回真鼠标）")
    return changed


def _window_pos_once(cfg: dict) -> list:
    """把「限位」（固定微信窗口位置/尺寸）搬到**默认开**（2026-09-16 用户口径）。

    用户原话：「你把限位设成默认吧，因为用户在后台都不在意这个，而且也能防止点错」
    ⇒ 把主窗摆成固定尺寸/位置 ⇒ 坐标标定不随窗口漂、少点错；后台跑的人本来也不看窗口。
    只做一次（标记记在 `data/`），之后用户在控制台「界面」面板自己关掉就不再动他。
    """
    changed = []
    u = cfg.setdefault("ui", {})
    if u.get("lock_window_pos") is not True:
        u["lock_window_pos"] = True
        changed.append("ui.lock_window_pos = true（微信窗口固定尺寸/位置：坐标更稳、少点错）")
    return changed


_MIGRATIONS = (
    (_SAFE_DEFAULTS_TAG, _safe_defaults, "控制台「微信」面板勾「只走后台」"),
    (_WINDOW_POS_TAG, _window_pos_once, "控制台「界面」面板取消勾「固定微信窗口位置」"),
)


def _migrate_once(cfg: dict) -> dict:
    """跑**还没做过**的一次性迁移（各自记标记）；跑完把标记写进 `data/config_migrations.json`，
    之后用户的改动不再被覆盖。"""
    try:
        with open(MIGRATIONS_MARK, "r", encoding="utf-8") as f:
            done = set(json.load(f) or [])
    except Exception:
        done = set()
    todo = [(t, fn, how) for (t, fn, how) in _MIGRATIONS if t not in done]
    if not todo:
        return cfg
    all_changed, undoes = [], []
    for tag, fn, how in todo:
        changed = fn(cfg)
        done.add(tag)
        if changed:
            all_changed += changed
            undoes.append(how)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(MIGRATIONS_MARK, "w", encoding="utf-8") as f:
            json.dump(sorted(done), f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    if all_changed:
        print("[config] 一次性迁移（要改回来：%s）：%s" % ("；".join(undoes), "；".join(all_changed)))
        try:
            save_config(cfg)
        except Exception:
            pass
    return cfg


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
