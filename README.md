wx-agent —— 微信智能机器人（QQ Agent 大脑 + wechatauto UIA 微信接入）

════════════════════════════════════════════════════

⚠️ 风险提示
微信官方不欢迎自动化工具，用个人微信有封号风险（请用有使用时长的小号，别用主力号）。
本项目是 UIA 无注入模式：只读微信本地数据库 + 模拟正常屏幕操作（打字/点发送），
不注入、不 Hook、不改微信文件；控制回复频率（默认每分钟 ≤20 条）可进一步降低风险。
是否使用、用哪个号，请自行评估。

════════════════════════════════════════════════════

快速安装（三步）

1. 装环境：Windows 10/11（64 位）+ Python 3.10+，双击 scripts\安装依赖.bat（或 pip install -r requirements.txt）。
2. 填配置：把 config.example.json 复制一份改名为 config.json，填 api.api_key（DeepSeek 密钥）。
3. 跑起来：登录电脑微信 4.x（小号，勾选自动登录）→ 双击「启动机器人.vbs」→ 群里 @机器人 测试。

> 详细教程（含离线安装、Web 控制台、识图/拍一拍/引用）见《使用说明.md》。
> 运行时微信窗口可缩小，但别最小化到任务栏；机器人在群里发言时别抢鼠标键盘。

════════════════════════════════════════════════════

功能一览

· 无状态会话：每次唤醒独立会话，提示词 = 静态人设 + 存档摘要 + 最新消息，成本不随历史膨胀。
· 响应档位：4 档（仅艾特 / +关键词 / +随机 / 全响应），没命中不调模型（零 token）。
· 工具集：发消息（分条 / @ / 引用）、看图、发图、翻历史、查活跃成员、记忆增删查、联网搜索/抓网页、拍一拍（自动回拍 90% + 30 分钟冷却，主动皮一下低频）。
· 联网搜索：Bing/DeepSeek/智谱/博查/百度/秘塔/自定义，web_fetch 带 SSRF 防护。
· 群友长期记忆：每成员一条 JSON，后台自动整理。
· 人设模板：小鲸鱼（默认）/ 傲娇 / 毒舌，可自定义。
· 发送保护：限频、真人化间隔、Markdown→纯文本、超长切分；新对话开始 70% 自动引用对方最近一句。
· 用量统计：今日 / 本周 / 累计 token 与成本估算、账户余额、每轮消耗；运行明细（思考过程/token/工具调用）。
· 多模态识图：引用图片后说「分析这张」，机器人解密看图作答。
· Web 控制台：浏览器里改设置 / 看状态 / 看日志 / 一键体检 / 拍一拍诊断 / 测试 API / 查余额。
· 小鲸鱼余额挂件：右下角常驻（余额刷新、今日已用、每轮消耗泡泡、拖拽吸附、Q 弹、音效台词）。
· 界面适配：DPI 缩放自动检测 + 手动覆盖、点击前自动清理遮挡、点击归属校验、自检按钮——换电脑无需逐个适配。

技术来源：整合自开源项目 qq-agent（大脑）、wechat-deepseek-bot / wechatauto（微信 UIA 接入）、
DeepSeek-Balance-Whale-Widget（挂件，MIT，随包附原 LICENSE）。仅供学习交流，上游版权归原作者。

常用配置（config.json，改完重启生效）

· api.base_url / api_key / model —— 大模型接口（必填）
· api.use_official_price / model_prices —— 成本按内置价目/自定义单价（元/百万 token）估算
· wechat.bot_nickname —— 机器人微信昵称（群里 @ 这个触发）
· wechat.group_name_white_list —— 允许回复的群名；空 = 所有群
· wechat.poll_interval —— 消息轮询秒数
· persona.bot_name / role_text / participation —— 人设名 / 人设文本 / 参与度
· store.context_tier / keywords —— 响应档位 1~4 / 关键词
· send.max_per_minute / max_per_hour —— 发送限频
· send.quote_on_new_talk / quote_reply_probability / quote_new_talk_gap_s —— 新对话自动引用规则
· poke.reply_probability / cooldown_seconds / active_probability / active_daily_limit —— 拍一拍概率
· stats.period —— 用量统计周期（daily/weekly/monthly，默认 weekly）
· server.port / token —— 控制台端口 / 访问口令（留空自动生成）
· ui.coord_scale / ui.clean_overlays —— 显示缩放（auto 或 1.25~2.0）/ 点击前清遮挡

Web 控制台

启动后自动用浏览器打开（形如 http://127.0.0.1:3210/?token=xxxx）：
概览（状态/今日/本周/累计用量/目标群）、体检与功能自检、运行明细（每轮思考/token/工具）、
模型 API、微信、记忆、人设与响应、发送限制、联网搜索、服务器、界面适配、运行日志、原始 JSON。

运维（无需 PowerShell，全程无窗口）

· 启动机器人.vbs —— 启动（pyw 隐藏跑 watchdog.py → pythonw 跑主程序，崩溃自动重启）
· 停止机器人.vbs —— 停止（按 PID 文件静默结束机器人+看门狗）
· scripts\watchdog.py / stop_bot.py —— 看门狗 / 停止器本体
· scripts\安装依赖.bat / 自检.bat / 备份配置.bat —— 装依赖 / 环境自检 / 备份
· scripts\开机自启.bat / 取消开机自启.bat —— 注册/取消登录自启

离线部署：仓库已含 offline\（全部依赖 wheels + 绿色版 Python 3.10），目标电脑没网时
直接双击 scripts\安装依赖.bat 即可（自动识别离线模式）；微信 4.x 请自行安装。
