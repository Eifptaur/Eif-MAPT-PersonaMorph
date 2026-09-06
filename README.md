wx-agent —— 微信智能机器人（QQ Agent 大脑 + wechatauto 微信接入）

════════════════════════════════════════════════════

⚠️ 风险警告（务必先读）

本项目是第三方微信机器人，微信官方不欢迎自动化工具，使用个人微信有账号被封禁的风险（轻则限制登录、功能受限，重则封号）。

但本项目的机制把风险压到了较低水平：
· 采用 UIA 无注入模式——只「读」微信本地数据库 + 「模拟」屏幕上的正常操作（打字、点发送）。
· 不注入微信进程、不 Hook、不修改微信任何文件、不走破解协议。
· 对微信而言，就像一个真人在正常打字聊天。
· 相比「注入 / 协议破解」类机器人，封号风险显著更低，但并非零风险。

降低风险的建议：
1. 用有使用时长的小号，绝不用主力微信号；
2. 控制回复频率（默认每分钟 ≤20 条），别让它刷屏；
3. 避免短时间大量群发、避免深夜高频活跃；
4. 机器人在群里发言时别去抢鼠标键盘。

是否使用、用哪个号，请自行评估并承担后果。

════════════════════════════════════════════════════

快速安装（三步）

1. 装环境：Windows 10/11（64 位）+ Python 3.10+，双击 scripts\安装依赖.bat（或 pip install -r requirements.txt）。
2. 填配置：把 config.example.json 复制一份改名为 config.json，填 api.api_key（你的 DeepSeek 密钥）、wechat.bot_nickname（机器人微信昵称）。
3. 跑起来：登录电脑微信 4.x（小号，勾选自动登录）→ 双击 scripts\启动机器人.bat → 群里 @机器人 测试。

> 详细的分步教程（含离线安装、Web 控制台、识图/拍一拍/发图、常见问题）见《使用说明.md》。
> 运行时不要最小化微信窗口（可缩小，别缩到任务栏）。

════════════════════════════════════════════════════

工程介绍

本工程整合两个开源项目制作而成：
· 智能大脑来自 qq-agent——由 B站网友 Kondius 基于 qq-bridge（https://github.com/Derpyu520/qq-bridge）修改而来（相关 B站视频 BV1ss8R6zERG；修改版未上架 GitHub，下载地址 https://t.bilibili.com/1244553403559837713）。
· 微信接入来自 wechat-deepseek-bot——B站视频 BV1Mz4267EHQ / GitHub（https://github.com/bdydgz114514/wechat-deepseek-bot），底层用 wechatauto UIA 无注入。

功能：把 DeepSeek 多模态大模型接入微信群——群里 @机器人 提问 / 引用图片识图 / 联网搜索，用「小鲸鱼」人设像真人一样聊天、能记住群友长期印象，每次回复成本恒定可控（无状态会话），并带浏览器控制台。

一句话说明：群里 @机器人 问题，机器人用「小鲸鱼」人设自然回复：能接梗、能装傻、能联网搜、能看图、能记住群友长期印象，且每次回复成本恒定。

核心特性

· 无状态会话：每次唤醒新开独立会话，提示词 = 静态系统提示 + 存档摘要 + 本次新消息，成本不随历史膨胀。
· 响应档位滑条：4 档（仅艾特 / +关键词 / +随机 / 全响应），没命中不调模型（零 token）。
· 原生工具集：发消息（分条/@/引用）、看图、发图、翻历史、查活跃成员、记忆增删查、联网搜索/抓网页、拍一拍。
· 联网搜索：Bing/DeepSeek/智谱/博查/百度/秘塔/自定义，web_fetch 带完整 SSRF 防护。
· 群友长期记忆：每群友一个 JSON，memory_append/query/remove，后台自动整理。
· 人设模板：小鲸鱼（默认）/ 傲娇助手 / 毒舌老哥，可自定义。
· 发送保护：限频、真人化间隔、Markdown→纯文本、超长切分。
· 用量统计：token / 轮次 / 联网次数 / 成本估算 / 账户余额。
· 多模态识图：引用图片后说「分析这张」，机器人解密看图作答（视觉模型）。
· Web 控制台：浏览器里改设置 / 看状态 / 看日志 / 测试 API / 查余额（默认 http://127.0.0.1:3210）。
· 运维脚本：启动/停止/备份/开机自启/看门狗崩溃自启/自检。

已移除的 QQ 专属能力（微信 UIA 无法可靠实现）：收藏表情库、合并转发展开。
对应新增了微信可用的 @、发图、拍一拍、引用。

目录结构

wx-agent/
├── wx_agent.py          ← 主程序（微信接入 + 消息循环 + 工具调用 + 编排 + Web 控制台）
├── config.example.json  ← 配置模板（复制改成 config.json 再填 Key）
├── config.json          ← 你的配置（含 API Key，已被 .gitignore 忽略，勿上传）
├── requirements.txt     ← 依赖清单
├── LICENSE              ← MIT 许可（含上游项目许可说明）
├── .gitignore           ← 忽略敏感文件/运行时产物
├── README.md / 使用说明.md / 更新日志.md
├── agent/               ← 智能大脑（14 个模块）
│   ├── config.py  util.py  llm.py  web_search.py  safe_fetch.py
│   ├── store.py  memory.py  sender.py  persona.py  prompt.py
│   ├── tools.py  wechat.py  webui.py
├── scripts/             ← 运维脚本（启动/停止/备份/开机自启/看门狗/自检/安装依赖）
├── offline/             ← 离线部署包（wheels + 绿色版 Python，可 gitignore）
├── data/ logs/ media/   ← 运行时生成（勿上传）

常用配置（config.json，改完重启生效）

· api.base_url / api_key / model —— 大模型接口（必填）
· api.vision —— 是否启用看图
· api.temperature / max_rounds —— 活泼度 / 单次最多工具轮数
· api.price_input_per_m / price_output_per_m / price_cached_per_m —— 自定义单价（元/百万 token）；留 0 用内置官方价
· api.use_official_price —— 是否用内置官方单价表估算成本
· wechat.bot_nickname —— 机器人微信昵称（群里 @ 这个触发）
· wechat.group_name_white_list —— 允许回复的群名；空 = 所有群
· wechat.poll_interval —— 消息轮询秒数
· persona.bot_name / role_text —— 人设名 / 自定义人设文本（留空用内置小鲸鱼）
· persona.participation —— 参与度 low / medium / high
· store.context_tier —— 响应档位 1~4（1=仅艾特 … 4=全响应）
· store.keywords —— 2 档关键词表
· web_search.provider —— 搜索引擎
· send.max_per_minute / max_per_hour —— 发送限频
· server.port / token / auto_open_browser —— 控制台端口 / 访问口令（留空自动生成随机口令）/ 自动开浏览器

Web 控制台

启动后自动用默认浏览器打开（网址带随机口令，形如 http://127.0.0.1:3210/?token=xxxx）：
· 状态：在线灯、目标群、会话数 / 总 token / 已发消息数 / 估算成本
· 余额：顶栏显示 DeepSeek 账户余额（充值 + 赠送），点击刷新
· 模型 API：改 Base URL / Key / 模型 / 温度，一键「测试 API 连通」
· 微信 / 人设与档位 / 联网搜索 / 完整 JSON / 运行日志 / 暂停恢复

工具集（会话内自动限定，无令牌）

· 发送：send_message（分条 / 引用 / @）
· 看图：get_message_images
· 发图：send_image（转发某条消息里的图片）
· 上下文：get_recent_messages、get_active_members、get_message_detail
· 联网：web_search + web_fetch（SSRF 全防护）
· 记忆：memory_append / query / remove、report_feedback、finish
· 拍一拍：send_poke（右键对方头像→菜单选「拍一拍」，实验性，可能失败）

注：微信「收藏表情包 / 从表情库发送」因表情面板是自绘 UI、无法可靠定位，暂未实现；发图用 send_image 代替。

运维脚本

· scripts\启动机器人.bat —— 日常启动（带看门狗自动重启）
· scripts\停止机器人.bat —— 一键停止
· scripts\安装依赖.bat —— 一键装依赖（自动识别离线/联网）
· scripts\自检.bat —— 环境/依赖/逻辑自检
· scripts\备份配置.bat —— 备份 config.json 与 data/
· scripts\开机自启.bat / 取消开机自启.bat —— 注册/取消登录自启
· scripts\看门狗.ps1 —— 崩溃/退出自动重启守护

致谢与免责声明

· 本整合基于开源项目 qq-agent（B站网友 Kondius 基于 qq-bridge 修改，未上架 GitHub）、wechat-deepseek-bot（B站视频 BV1Mz4267EHQ / GitHub bdydgz114514/wechat-deepseek-bot）、wechatauto-replica、chatgpt-on-wechat、DeepSeek-Balance-Whale-Widget 的思路构建，仅供学习交流。
· 上游各项目版权归原作者所有，使用请遵守各自许可证（qq-agent 为 MIT）。
· 微信安装包版权归腾讯所有。
· UIA 模式不注入、不 Hook、不改微信文件，但使用个人微信与账号风控请自行评估并承担风险。
