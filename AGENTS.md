# AGENTS.md — 群相灵（Persona Morph）· 新会话进门第一条

> **2026-09-13 更名**：产品定名 **群相灵（Persona Morph）**（此前沿用的临时名已全部替换；仓库名同步改为 `Eif-MAPT-PersonaMorph`）。

> 与 `dsh启动器/AGENTS.md` 同一套约定：读完能正确改，不必重新摸。改到"入口 / 自动化路线 / 打包方式"就回来更新本文件。

## 1. 这是什么

群相灵（Persona Morph）后端（Python）。入口：`persona_morph.py`（主流程）、`wechat.py`（微信客户端操作）、`llm.py`（模型）、`memory.py`（记忆）、`console_html.py` / `webui.py`（浏览器控制台）。产物：`一键启动.exe` / `一键关闭.exe`。
既定方向（用户 2026-09-11）是**控制台不再依赖浏览器**；**2026-09-13 用户把口径讲细了**（原话："重绘一下 Persona Morph 的启动弹窗，太简陋了，其他弹窗也重绘一下，把弹窗清晰度提上来，然后要做和启动器一样的软件自己开弹窗显示控制台，不再依赖浏览器…你自己考虑分析清楚"）⇒ **不是把控制台重做成原生 GUI/exe**（PyInstaller / Nuitka / jpackage 那批**不在本轮范围**），而是**换承载窗口**：web 控制台原样保留，用我们自己的 **WebView2 内嵌窗口**显示（照 `dsh启动器` 已跑通的 `FrmDshWindow`：无边框 + DWM 圆角 + 自绘拖动条 + 边缘缩放子窗钩子 + `SetThreadDpiAwarenessContext(-4)` PerMonitorV2 + WebView2 数据目录固定）；只改"谁来开窗"这一跳（端口/token 定源、`/api/version` 同版本判定、token 打码、就绪轮询全部保留；WebView2 缺失时退回 `webbrowser.open`）。**弹窗族一并重绘、做到 0 系统 MessageBox**（清单与取证见交接件 §3-4 的 W6）。

**最高目标（用户 2026-09-13 定，压过本项目其它目标）**：原话——「我们微信这个项目的最高目标就是全程后台，不抢鼠标，在 Windows 系统下兼容一切情况，让人能在上班的时候也运行」。⇒ 拆成三条**硬判据**（任何方案的准入，不只是记录项）：
①**全程后台**：不要求微信窗口处于前台、不要求可见（最小化 / 被遮挡 / 别的程序占满屏时也要能工作）；
②**不抢鼠标**：任何一次操作前后 `GetCursorPos` 不变；
③**不抢前台 / 不打扰**：用户正在别的程序里打字时，不许把前台切走。
**「兼容一切情况」**＝按 `_scratch\兼容性矩阵.md` 的 13 条轴逐条适配（本机测不了的**明写缺什么条件**）。
**推论（据此排优先级）**：**读取侧天然达标**（`WeChatDB` 读库 + `listener_watermark` 水位，全程不碰窗口）✓；**发送侧是唯一难点**——L0（`mouse_event` + 前台）**只许当最后的兜底且用完恢复光标，不许作为默认**；若 W0 实测证明 L5/L4 都不成立，**必须在交付里写明「发送这一环无法满足最高目标」**，并给可选模式（例如仅用户空闲时执行 / 显式开启「会短暂置前」模式且控制台标注），**不许悄悄降级**。

## 2. 窗口自动化分层（本文的重点：怎么"不动鼠标"操作窗口）

分层，从最稳到最脏；**能用上层就别用下层**。L5/L4 是 2026-09-13 学 MAA 后新增的两档（依据 `_scratch\refs\MAA-可借鉴.md` 机制 3）：

| 层 | 手段 | 能不能"不动鼠标" | 适用对象 | 备注 |
|---|---|---|---|---|
| **L5 后台消息注入** | `SendMessageW`/`PostMessageW` 发 `WM_LBUTTONDOWN`/`WM_MOUSEMOVE`/`WM_LBUTTONUP`、`WM_KEY*`、`WM_CHAR`、`WM_MOUSEWHEEL`；**发前先发 `WM_ACTIVATE/WA_ACTIVE` 伪激活**（让目标自认为被激活，但**不改前台窗口**）；lParam 必须用**目标窗口客户区坐标** | ✅ 完全不动光标、不动窗口、可在后台 | **MAA 上验证有效的一档**；对微信**尚未验证**（见下方"必须先做的最小实验"） | MAA 实测：`prepare_mouse_position()` 是空实现 ⇒ 真不动光标。⚠️ 对 Chromium/Electron 类窗口的输入消息常被忽略，微信 4.x 就是这类 ⇒ **只当"待验证"用，不许先假定它能点** |
| **L4 挪窗对齐** | `SendMessageWithWindowPos/PostMessageWithWindowPos`：**挪窗口去对齐光标，光标本身不动** | ✅ 不动光标（窗口会瞬移/闪烁） | 会读真实光标位置的自绘控件 | 官方原话 "The cursor is not moved, so there is no mouse seizure"；MAA 用 `WH_MOUSE_LL` 吞位移 + 60fps 批释放 + `NtSuspendProcess`。必须配 `save/restore_window_position` 用完还原 |
| **L4 进程内** | 直接调函数 / `Control.PerformClick` / 状态注入 / Electron·WebView2 走 CDP | ✅ 完全不动 | **我们自己的**窗口/页面（含将来的 exe 控制台） | 最快最稳，零 UI 脆弱性；自家控制台自动化**优先走它的 HTTP/内部 API，不要点页面** |
| **L2 UI Automation** | `uiautomation` / `pywinauto` / `wxauto`；找控件后用 Invoke / ValuePattern / LegacyIAccessible | ✅ 不动鼠标、不抢焦点 | 原生控件（WinForms/WPF/部分 Chromium 开无障碍） | **微信客户端自动化的正路**（这条技术早在启动器取证里被验证过：`--dragprobe`/`--maxprobe` 走命中码与 UIA 式探测，全程不碰真实光标） |
| **L1 消息投递（读）** | `SendMessage/PostMessage`：`WM_NCHITTEST` / `WM_GETTEXT` / 自定义消息 / 命名事件握手 | ✅ 不动鼠标 | 自家 Win32 控件；跨进程"读"（命中码、文本、窗口状态） | **读是可靠的、写（输入类）不可靠** ⇒ 只当"读"用 |
| **L0 真实输入注入** | `SendInput` / `mouse_event` / `SetCursorPos` | ❌ 会真的动鼠标、抢前台 | 前几层都走不通时的兜底 | 用户看得见、会和人的操作打架；必须"用完把光标还回去" |
| （备选，暂不采用）**合成触点** | `InjectSyntheticPointerInput` → 目标收 `WM_POINTER`（MAA 叫 AnchoredTouch） | ✅ 不动光标、不改前台 | 触摸类应用 | 需 Win10 1809+、**只支持 click/swipe（滚轮无解）**、遮挡时要短暂置顶（约 70ms 闪烁、需 `WS_EX_LAYERED`）⇒ 微信是 Win32 应用，性价比低，记录在案即可 |

**当前 群相灵 的实测状态**：`wechat.py` 里大量使用 `user32.mouse_event(...)`（如 L787/L1026/L1087/L1268/L1302）＝ **目前主要靠 L0 真实输入**；未见 `uiautomation` / `pywinauto` / `wxauto` 引用。⇒ 想做到"不动鼠标"，需要把 UI 动作**抽成一个后端接口**（`click/send_text/scroll/find` 四个原语），按 L5 → L4 → L2 → L0 顺序尝试，上层命中就绝不动光标。

**W0-1 只读发现（2026-09-13，微信 4.1.13.65，脚本 `_scratch\w0_discover.py` → `_scratch\w0-ui-discovery.{txt,json}`）**：主窗 `Qt51514QWindowIcon`（hwnd 24250632，1161×901）+ 渲染子窗 `MMUIRenderSubWindowHW`（1139×890）；**UIA 从主窗只拿到 2 个节点、输入框候选 0** ⇒ **L2（UI Automation）在微信 4.x 上基本不可依赖**（同一口径：别人能不代表这儿能，必须本机实测）；另：`data/ui_layout.json` 的标定尺寸（237）与当前窗口（1139）差一个量级，库自己打了"忽略本次校准" ⇒ **UI 校准/指纹已经过期**（W7c 要的就是"按版本+尺寸的指纹"）。L5/L4 两档能否成立，由 W0 的三档实测给出。

**兼容性矩阵（2026-09-13 用户提问"另一台是旧版 UI 是不是因为 Win10"后立）**：`_scratch\兼容性矩阵.md` —— **13 条兼容轴**（A 微信版本/UI 代 · B 主题/字体/语言 · C DPI 缩放 · D 分辨率/窗口尺寸 · E 多显示器混合 DPI · F 系统叠加层（触控键盘/输入法候选/遮挡）· G 权限级别 · H 会话类型与界面状态 · I 输入内容 · J RDP/VM · K 多开多账号）逐条给出"取值 × 本机可测否 × 测法 × 判据"。**当前结论（待他那台机器验证）**：**"旧版 UI"更可能是微信 3.x 的经典界面，而不是 Windows 版本**——4.x 是 Qt 自绘（本机实测 `Qt51514QWindowIcon` + `MMUIRenderSubWindowHW`），Windows 10/11 只影响系统级叠加层（DWM 圆角/UIA provider/触控键盘）。**钉死方法**：在他的机器上跑 `scripts\selftest.py`（看"微信版本检测"那行）和 `_scratch\w0_discover.py`（看主窗类名）——若显示 3.x/非 Qt 类名 ⇒ 是微信版本差异，需单独查 `wechatauto-replica` 对 3.x 的支持能力，**不许假定"能一样"**。**UIPI 提醒（G 轴）**：微信以管理员运行而脚本不是时，`SendMessage` 类注入会被直接挡掉 ⇒ L5 的可用性必须**按权限级别分开记**。

**W0-2 输入后端实测：方法学与结论（2026-09-13 实测；实验台 `_scratch\w0_backends.py`，明细 `_scratch\输入后端对比.md`）**
- **已确认（2026-09-13 15:16，决定性 · 微信 4.1.15.8）**：**L5 后台注入可行** —— 把消息**投给主窗 `Qt51514QWindowIcon`**（伪激活 `WM_ACTIVATE` → 逐字 `WM_CHAR` → `Enter`），在**微信不在前台**（前台是 Chrome）、**光标全程不动**、**前台不被抢**的条件下 **3/3 成功**（判据＝DB 回读唯一 token，2/3 干净、一次带上了探针残留字符）⇒ **最高目标的「不抢鼠标 + 不抢前台」这一环有实测支撑了。**
- ⚠️ **投递对象是关键**：同一套消息投给**渲染子窗 `MMUIRenderSubWindowHW`** 是 **0/3**（`WM_CHAR` 与 `WM_KEYDOWN` 两种都试了）⇒ 先前"L5 无效"的印象来自**投错了窗口**（别投渲染子窗，投主窗）。
- **尚未验证（别当成"已支持"）**：①微信**最小化**时能不能发（最严条件）②输入框**没有预先聚焦**时能不能发（本轮阳性都是在 setup 点过输入框之后取得的）③30 次量级与跨微信版本稳定性（已知数据点：4.1.13.65 / 4.1.15.8）。
- **三条硬规矩（少一条结果就不可信）**：① 任何真点击之前先过 `agent.ui_adapt.ensure_point` 遮挡校验（本机实测：Chrome 全屏覆盖时点击被吃，脚本会把点击打到用户正在用的窗口上）；② 聚焦后必须做**阳性对照**（真实键击一个字符 ⇒ 输入框文字区深色点必须上升），对照不过就中止，别解读后面的注入结果；③ **成功只认 DB 回读**且要轮询（微信写库延迟数秒～十几秒），不许信 GUI 返回值。
- **坐标一律现算，绝不硬编码**：窗口尺寸会被用户随时改（本轮实测从 1160×900 变成 773×600，写死的 `(795,785)` 当场落到窗口外、被 Chrome 吃掉）⇒ 落点与探针区都从 `gui.get_input_box()`（render 相对，随窗口缩放）现算，并**夹在窗口内**（越界读到桌面像素会把信号淹掉）。
- **点消息区会把输入框焦点抢走**（之后打什么都进不去）——本轮最容易踩的假阴性来源；焦点必须做**阳性对照**才敢往下读数据。
- **真实点击前必须过遮挡校验**（`agent.ui_adapt.ensure_point`）：用户的全屏浏览器（F11 那类 topmost 窗）会盖住微信，普通置前压不过它 ⇒ setup 阶段可**短暂 `SetWindowPos(HWND_TOPMOST)`** 把微信抬上来做「点焦点 + 阳性对照」，做完立刻 `HWND_NOTOPMOST`。
- **ctypes 陷阱**：不声明 `argtypes` 时 `HWND` 会按 32 位 int 传参 ⇒ `SetWindowPos` 直接报 `1400 无效窗口句柄`（本轮实测）。用 `windll.user32` 调窗口类 API 前先把 `argtypes/restype`（尤其 `HWND`/`BOOL`/`LONG`）声明好。
- 库两个坑（并入 W7）：① `open_chat()` 在会话区退化时以 `ValueError: height and width must be > 0` **未捕获直接崩**；② `get_input_box()` 的探针在 4.1.13.65 上**失效并静默走兜底比例值**（兜底值恰好在框内，所以生产路径没出事，但那是猜的，窗口尺寸一变就可能乱点）。
- ⚠️ `open_chat()` 会触发 **UIA 热激活**（往 `Weixin.dll` 写 gate 字节；日志 `热激活 UIA：PID=… Weixin.dll+0xae2b0c8: 0 -> 1`）——正是 §3.1 里「待拍板、拍板前默认关」那一条，**目前是默认开**，要收口成显式开关。
### 2.0 ⚠️ 动手之前必须先做的最小实验（1 小时，别跳过）
**MAA 只证明了"后台注入在它面对的程序上可行"，没有任何一手证据表明微信 PC 客户端接受它**（MaaFramework 官方原话：*"Different programs on Win32 handle input differently, so there is no universal method."*，且从未针对微信表态）。所以**不许从 MAA 外推**：

- 实验：同一个动作（打开某聊天窗口 → 给「文件传输助手」发一条消息）分别用 **L5 `SendMessage`** / **L4 `WithWindowPos`** / **L0 `mouse_event`** 三档各跑 30 次。
- 每条记录：成功率、是否成功时窗口仍非前台、是否需要先把窗口置前、鼠标是否被移动（`GetCursorPos` 前后比对）。**（2026-09-13 起这四项是「选档门槛」而不只是记录项：任一项违反最高目标的方案不能当默认档，只能 opt-in 兜底。）**
- 产出：`_scratch\输入后端对比.md`（一张表 + 结论）⇒ **用这张表决定默认后端是哪一档**，再动手做 `InputBackend` 抽象。

### 2.1 迁移步骤（建议顺序）
1. 抽接口：在 `wechat.py` 上包一层 `InputBackend`（`find(control)` / `click(target)` / `send_text(target, text)` / `scroll(target, delta)`），现有 `mouse_event` 实现降级为 `RealInputBackend`（兜底）。
2. 先实现 L5 `MessageBackend`：`SendMessageW` 发鼠标/键盘消息 + **先发 `WM_ACTIVATE/WA_ACTIVE` 伪激活** + `ClientToScreen`/`ScreenToClient` 客户区坐标换算（MAA 也踩过这条）。
3. 起 L2 实现：`UiaBackend` —— 用 `uiautomation` 找微信窗口的控件树，点击用 `InvokePattern`、输入用 `ValuePattern`，跑通"发一条消息给文件传输助手"这条最小闭环。
4. 切换默认后端（按 §2.0 实验结论），降级时**记录降级日志**（`wechatauto_logs/`），并把「当前用的是哪一档」暴露到控制台。
5. **机械验收判据**：全项目 grep `mouse_event|SetCursorPos|SendInput`，**只允许命中 backend 文件**（这条可直接做成自检脚本）——做到了才算"输入已收口"。
6. 控制台换成 exe 时，控制台自身的自动化一律走 **L4 进程内**（内部 API / CDP），不要模拟点击它自己的界面。

**投递鼠标消息（L5 鼠标版）怎么测 —— 2026-09-13 实测踩坑（用户问"发表情和朋友圈能不能不用鼠标"时得出）**
- **结论状态：已定论 —— 投递鼠标消息有效（2026-09-13 16:06 实测）**：`WM_MOUSEMOVE` + `WM_LBUTTONDOWN/UP` 投给**主窗**，微信 Qt **认**——判据＝微信 PID 的**新弹层窗口**（`Qt51514QWindowToolSaveBits` / 标题 `Weixin` / 771×771）：真实点击弹 1 个、**投递点击同样弹 1 个，且光标全程不动**（脚本 `_scratch/mouse_panel_ab.py`，含真实点击阳性对照）。⇒ **表情面板可以纯后台打开**；朋友圈/拍一拍/图片等其余坐标操作按同一机制"有望可行"，但仍需逐个实测才许写"支持"。已确定的是：①键盘消息投主窗（`WM_CHAR`）**能进输入框**，带焦点时能真发出（DB 回读为证）；不带焦点时"文字进框、回车不提交"（DB 回读 0/3）。②其它能力（`moments_*` 朋友圈 / 表情面板 / 拍一拍 / 图片 / `close_subwindow` / `heal_input` / `_test_chat`）**当前确实在用真实鼠标**（审计：`wechat.py` 21 处、`wechat_ui.py` 7 处、`ui_adapt.py` 4 处、`persona_morph.py` 1 处）。③**投递的鼠标消息（`WM_MOUSEMOVE`+`WM_LBUTTONDOWN/UP`）到主窗，用像素差分判据测不出任何变化**（窗口内与全屏都是 0.000）。
- ⚠️ **像素差分不能当这类实验的判据**：同一坐标的**真实鼠标点击**也没有引起任何像素变化 ⇒ 表情面板/浮层很可能是**独立合成层或子窗**，抓屏看不到。⇒ 正确判据用**项目自带的可见性检测**（`gui._panel_visible(...)`；或 `emoji_panel_open` 内部那套判定），或用**新顶层窗口枚举**（右键菜单类）——**且必须做真实点击的阳性对照**（对照不过就不许下结论）。
- **两个硬前提**：①窗口必须可见——主窗被收进托盘时驱动库直接抛"微信主窗口不可见（恢复失败）"，实验连启动都做不了（可在实验里临时 `ShowWindow(SW_SHOWNOACTIVATE)`，测完还原用户状态）；②用户在用电脑时像素/光标/前台类读数会被污染（实测光标与前台会飘），此时只能用 DB 回读一类不依赖屏幕的判据。
- 实验台：`_scratch/mouse_post_test.py`（窗口内差分，含真实点击对照）· `_scratch/mouse_post_fullscreen.py`（全屏差分 + 新顶层窗枚举）· `_scratch/mouse_post_win.py`（三组对照：真实右键/投递右键/投递左键）。**顺带修掉的真缺陷（同一次实验发现，已提交）**：`agent/wechat.py::_emoji_btn_pos()` 老实现把 `get_input_box()` 返回的**渲染相对** box 又减了一次渲染原点（`box[0]-rx`）＝双重换算 ⇒ 笑脸坐标**偏左约 100px、偏下约 40px**，点不开面板（库算 (367,881) vs 实测 (470,930)）。修法＝以渲染区比例为主 `(0.324·w, h-50)`、box 只做 ±8% 内的细校正；**机械验收＝REAL 点击能打开面板**（修后实测：REAL 1 个弹层 / POST 1 个弹层）。
**下一步**：用同一判据（新弹层窗口）把 **朋友圈点赞 / 拍一拍右键菜单 / 图片发送** 各跑一次 REAL vs POST；全部通过后，控制台那 55 项「鼠标操作检测」才按用户口径整体删除（否则保留并分层改名）。
### 2.2 顺带要抄的两条（同源，成本极低）
- **窗口/帧探活与整轮重连**：`IsWindow(hwnd)` 探活；截图尺寸与首帧不一致 ⇒ **不就地改 ROI，整轮重连重定位**（MAA 的做法，明确"不原地修补"）；重连事件带次数（"正在重连 2/5"）让用户看得见。
- **失败留全现场（MAA 的反面教材）**：MAA **只留原图**，带 ROI 框的图只在 DEBUG 编译里、**ROI 坐标从不落盘** ⇒ 我们要做成**自包含目录** `wechatauto_logs\fail\<时间戳>_<原因>\`：`shot.png`（原图）+ `shot.annotated.png`（ROI 框 + 命中分数）+ `probe.json`（ROI 矩形 / 模板名 / 阈值 / 实际得分 / 候选列表）+ `context.json`（联系人 / 上一步动作）。判据：**任何人拿到这个目录，不看日志就能判断"是没找到还是找错了"**。

### 2.3 ⛔ 现状与三件必办（2026-09-13 对标取证得出；动手前先照这三条核一遍）
**我们已经在用 `wechatauto-replica`，但钉死在 1.1.5.1，而且耦合了它的私有 API**（下面三处均已回读复核，不是推断）：

- `agent/wechat.py:122` `from wechatauto import WeChatDB, MediaDownloader`；`:417` `from wechatauto.guia import WeChatGUI`
- `agent/wechat.py:2681` `MIN_VER["wechatauto-replica"] = "1.1.5.1"`，`:2712-2713` 对它做的是**严格等值**判定；离线包也是 `wx-agent\offline\wheels\wechatauto_replica-1.1.5.1-py3-none-any.whl`
- `agent/wechat.py:266` `self._db._msg_conn(chat_id)`；`:145`/`:163` 直接遍历 `_db._db_files` 与 `_open(rel)` ＝ **私有 API 耦合**

⇒ 1.2.2 的**跨分片消息合并**、Listener 水位语义、**UIA 物化自愈**、`AutomationId` 点分路径兼容、`quick_check` 坏缓存自愈、密钥三层修复——我们**一个都没有**。
⚠️ 更要紧的：1.2.2 已把 `_msg_conn` 降级为"只返回第一个命中分片"的兼容接口 ⇒ **只升级不改调用点不会报错，只会静默少查**。

**三件必办（验收写死，细节见 `_scratch\四个对标项目-可借鉴总报告.md` §6 第 0 条）**
1. ✅ **已做（2026-09-13）**：驱动库升到 **1.2.2.2**（装进 `runtime\python`；顺带拉进 pyautogui/opencv 等新依赖 ⇒ 离线包清单要同步）；私有 API 收进 **`agent/replica_adapter.py`（唯一收口点 + 版本守卫：≥1.1.5.1 最低、>1.2.2.2 只提示"未实测"）**；`wechat.py` 三处调用点改写（昵称映射 / 群列表 / 引用消息取图）——其中引用消息取图**从"只看第一个命中分片"改成遍历全部分片**（1.2.2 起 `_msg_conn` 已是兼容降级接口，不改就是静默少查）；`dep_check` 去掉对它的**严格等值**判定（实测 `1.2.2.2 / min 1.1.5.1 / ok=True`）。判据＝`py -3 scripts\replica_adapter_selftest.py`（假 DB 单测，**18/0**，不需要微信在跑）+ `scripts\selftest.py` **55/55**。
2. ✅ **已做（2026-09-13）**：新增 **`agent/listener_watermark.py`**（`Watermark` 原子落盘 + `process_batch`：handler 返回真值才推进水位 / 失败重试 3 次线性退避 / 仍失败写 dead-letter 并**越过**毒消息 / 每会话 RLock 串行）；`scripts/persona_morph.py` 监听循环改为**从 `data/listener_watermark.json` 读回水位**（重启后继续，停机期间消息会补上，不再跳 `latest_seq`）、逐条以 `append_incoming` **是否返回 entry** 判成功、失败留痕 `data/listener_failed.jsonl`、退出前 `flush`。判据＝`py -3 scripts/watermark_selftest.py`（**17/0**，不需要微信）+ `scripts/selftest.py` **55/55**。**改前基线（取证）**：`since_seq` 只在内存、批末**无条件推进**、失败无重试无留痕、重启直接跳 `latest_seq`。
3. 新增只读 `agent/uia_probe.py`：输出 `is_materialized()` + `describe_layout()` 到 `wechatauto_logs/ui_probe/<微信版本>.json`，控制台加按钮；并实测 `uiautomation.Control.Click()` 是否移动光标（本机 2.0.29 走 `SetCursorPos`）——**这条决定 §2 的 L2 选型能不能成立**，必须落到 §2.0 那套"三档各 30 次"的实测里。

> 取证来源：`_scratch\refs\wechatauto-replica-可借鉴.md`（14 条机制 / 37 行结论表 / 我没验证到的）；插件体系与热更新参照 `_scratch\refs\miloto-可借鉴.md`（机制 1..20）。
> 第 4 个对标对象：张苹果「最新微信群聊机器人」v1.8.1（**闭源分发包，但已按用户要求逐层扒开**，产物在 `~/.dsh/scratch/refs/zhangpingguo/`，结论见 `_scratch\refs\张苹果-微信群聊机器人-可借鉴.md` §3）。
> **它是什么**：NSIS 壳 → Electron+Vue 前端（`app.asar`，JS 混淆）→ PyInstaller 冻结的 Python 3.12 后端（`backend.exe` + `_internal`）→ 业务是 **`wxauto4` 的换皮分支**（包名改 `wechatpro`，`ui/main.py.backup` 里明写 `from wxauto4…`）。
> **⭐ 最值钱的借鉴＝微信 4.x 的 UIA 控件通讯录**（wxauto4 现成的类名，抄进 W4/W7 省几天反查）：主窗 `mmui::MainWindow`（`mmui::MainTabBar#main_tabbar`、`mmui::ChatMasterView`、`mmui::ChatMessagePage`→`mmui::XSplitterView`）· 会话列表 `mmui::ChatSessionList`/`mmui::XTableView` · 搜索 `mmui::XSearchField` + 弹层 `mmui::SearchContentPopover` · 聊天区 `mmui::MessageView` + **`mmui::ChatInputField`** + 发送按钮 `Name=发送(S)` · 气泡 `mmui::ChatBubbleItemView`/`ChatTextItemView`/… · **独立子窗 `mmui::FramelessMainWindow`**（wxauto4 原话：通过子窗口发送不切换主窗口）· 聊天信息 AutomationId 路径 `top_content_h_view…current_chat_name_label`。
> **⚠️ 但它那条路在本机当前版本走不通（已实测否定）**：微信 4.1.15.8 上原版 `uiautomation 2.0.29` 遍历主窗只有 **2 个节点**（`Qt51514QWindowIcon` + `MMUIRenderSubWindowHW`），`mmui::ChatInputField`/`发送(S)` 都不存在；**"屏幕阅读器标记能物化 UIA 树"的猜想也被证伪**（`SPI_GETSCREENREADER` 本就是 True，置位后重扫仍是 2 个节点）；它的后端里**没有任何物化 UIA 的代码**（无 `Weixin.dll`/`VirtualProtect`/`WriteProcessMemory`）⇒ 它只在"树本来就在"的微信版本上有效，这正是它反复叮嘱"请勿随意更新微信"的原因。
> **🚩 绝对不抄两件**：①它把用户**微信数据库密钥上传到自己服务器**（`zhangpingguo.com/api/submitWechatSecretKey` + `:wechatpro-secret-salt` + 授权验证）②**机器指纹/虚拟机检测**（`reg query …VirtualMachineId` / `…ProductId` 做授权绑定）。**不抄的还有**：真鼠标真键盘的操作路线（它自己的文档写着"不要动鼠标"，比我们已实测的 L5 落后）、群成员活跃统计（§3.1 红线）、混淆与自保护（我们开源）。
> **值得抄的小件**：启动后端前"检查并清理端口"的流程与话术 · 开放接口用环境变量传 token 且界面可**轮换** token（正对 W3 要修的 `webui.py:450-452` 空 token 放行）· 子窗口思路（将来 UIA 可用时，比投消息更干净的发送路径）。
> **真缺口（顺序）**：①微信自动更新对策（本机当日刚被自动升到 4.1.15.8，库的 UIA 当场退化成 OCR）②UIA 物化机制（本机当前版本无解，需要另找；已否定 SPI 屏幕阅读器标记）③开放接口与本机程序对接。

## 3. 红线

- **显示层自研**（与启动器同口径）：弹窗/图标/文案自己画，不用系统 MessageBox 与系统图标。
- **不动用户鼠标**（按 §2 分层优先：L5/L4/L2）；确需 L0 真实输入时用完恢复光标位置并在日志里留痕。
- **不许从别处外推**：MAA 能后台注入 ≠ 微信能被后台注入（MaaFramework 官方原话 "no universal method"）⇒ 任何"某档可行"的结论必须由本项目 §2.0 那种**最小实验（各 30 次）**产出，不接受引用别人的效果断言。
- **探针/脚本静默**：不许在屏幕上弹不属于产品的窗口；GUI 程序一律经带硬超时的启动器（参考 `~/.dsh/scripts/run-probe.ps1`）。
- 改完必须自测（用户口径：未自测不得声称完成）；打包类改动要**重打包并验证包内容**。

### 3.1 法律与技术红线（2026-09-13 调研；底稿全在 `_scratch\`）

> 底稿：`风险与合规-总报告.md`（合并视图，含"法律建议 vs 用户决定 vs 功能代价"冲突表）· `风险与合规-法律调研.md`（31 条风险表，每条带来源与可信度分级：法条原文/官方解释/判例/官方公告/正规媒体/自媒体/推断）· `风险与合规-技术面.md`（15 条 + F1–F12 带 `file:line`）· `风险闸门-设计.md`（全自动前提下的 L0–L4 闸门）。

- **不做分发包**：《刑法》285 条 3 款 + 两高解释把"情节严重"压到"提供专门工具 **20 人次以上**"或"违法所得 **5000 元以上**"；已有判例打的**全是开发/销售者** ⇒ 分发＝法律高风险，**建议不做**。
- **不做自动群发广告 / 营销群发**（广告法 43 条 + 协议 8.1.2.4 + 反法 12 条三杀，把"封号"升级成"行政+民事"）。
- **群聊不做自动回复、不读群成员列表**（《微信个人账号使用规范》4.1：未经其他用户明确同意不得复制/存储/使用/传输其数据；个保法 13/23 条无合法性基础）⇒ **待用户拍板**。
- **不把 AI 回复伪装成真人**（《深度合成管理规定》17 条(1)："智能对话…模拟自然人…文本生成"须**显著标识**；18 条禁删除/篡改标识）⇒ **待拍板标识方案**（建议在会话固定位置标注，不逐条加）。
- **`Weixin.dll` 写 gate 字节**（UIA 热激活必需）＝协议 8.2.1.5 正面命中，且是崩溃风险最高一环 ⇒ **待拍板**；拍板前凡触及微信进程的步骤一律**默认关**。
- **主号不接入**（协议 7.1.2 账号所有权归腾讯 + 8.5.1 封禁权；官方公示已对上百万账号作限制）+ 关键联系人定期备份。
- **控制台只在回环口 + 必须有口令**：`agent/webui.py:450-452` 现在 token 为空即放行，须修；禁止 `host=0.0.0.0`。
- **出网最小化**：发大模型前本地脱敏（手机号/身份证/银行卡/地址），只送本轮必要片段。
- **停机/拦截的提示绝不往微信侧发**（只在本机与控制台出现）。
- 三条必记：**微源码案 (2017)粤03民初250号**（"外挂由平台规则界定，法院认平台规则"⇒ 不能靠"法无禁止即可为"）· **微信 2024-03-26 / 2019-07-05 公告**（"定时群发、自动回复、自动聊天"明确列为外挂，上百万账号已被限制）· **张尧案 (2016)粤0105刑初1040号**（入罪用 285 条 3 款，门槛在"提供"这个动作）。
- **闸门不是豁免**：它能降"内容伤人"与频率风险，**不能**降协议违约、封号风险、深度合成标识义务。
- 许可证（只在分发时触发）：6 库全部 Apache-2.0/MIT，**无一 GPL/AGPL**；Apache-2.0 §4(a)-(d) 需 LICENSE 副本 + 修改声明 + 保留声明 +（**仅 wechaty 有 NOTICE**）。注意 `wechatauto-replica` 的 LICENSE 版权行是**未填模板**，署名要按"仓库 + commit"写。
- **用户已定：项目一定会开源**（原话："项目本身一定会开源给广大网友来玩的…只需要在技术层面上降低封号风险就行了"）⇒ **开源前置清单 6 条**（详见 `_scratch\开源前置清单.md`）：①**切分公开范围**——公开通用机制（风险闸门/脱敏/UI 自动化框架/OCR 定位/调试包/控制台），**不公开**"扫微信进程内存取密钥 + 往 `Weixin.dll` 写 gate 字节"这类针对微信的能力（这是"通用工具 vs 专门工具"的分界线，也是唯一真正的高危成分）②产品名/图标/文案**去"微信"**（驰名商标判例判赔 1020 万）+ 标注"非官方、与腾讯无关"③**AI 标识**（会话固定位置）④**不做任何"专门规避风控"的功能**（自动换号/代理池/绕过检测/批量养号/群控——这是刑法分界线）⑤**不做一键群发/营销功能与教程**⑥许可证与免责声明齐备。
- **反诈/帮信的分界**：中立工具确有免责空间，但《刑法》287 条之二（帮信罪）+《反电信网络诈骗法》38 条给"提供帮助"设了责任 ⇒ 界限在"**专门性**"与"**规避性**"——把这两样从产品里去掉，就是第 1、4 条的意义。
- **有利抗辩点（写进 README 定位声明）**：读的是**本人账号、本人设备**的库，发的是**本人**的消息，不侵入他人系统、不控制他人账号；产品定位是**聊天互动**，不含营销/群控/加粉功能。

### 3.2 后台操作的风险口径（用户 2026-09-13 定，原话："反正只要我们这个后台点击这个操作，不会给用户带来额外风险即可"）
拆成四条可执行判据，任何输入实现都要照这四条过一遍：
1. **后台注入本身不额外增加风险，反而比"真实键鼠"更难被识别**：`PostMessage`/`SendMessage` 投 `WM_CHAR`/`WM_KEY*` **不携带 `SendInput`/`mouse_event` 的注入签名**（`GetMessageExtraInfo()` 里那一位），真实注入才是可被检测的那一种；后台操作还免掉了"抢前台 + 误点到用户正在用的窗口"这一层次生风险。⇒ **"后台"这个属性不许被当成风险项**（不许因为它后台就额外拦截、也不许因为它后台就要用户确认）。
2. **必须守住"四不"——这才是额外风险的真实来源，一条都不能破**：①**不往微信进程写内存**（`Weixin.dll` 写 gate 字节 / UIA 热激活 ⇒ **默认关**，要用必须显式 opt-in 且控制台可见）②**不改微信文件**（不 patch 客户端、不动 `WeChat Files`）③**不注入 DLL、不 hook**（不 `SetWindowsHookEx`、不 Detours、不替换线程上下文）④**不装驱动、不动系统级安全设置**。
3. **封号风险的主轴是行为特征，不是"动没动鼠标"**：发送频率、时间分布、内容重复度、秒回、24h 在线、新号/异地登录、被举报——这些归 **W3 风险闸门**管；`W3` 的判据里**不许出现"是否后台""是否动鼠标"这两项**。
4. **读取侧的既有事实要写明**：DB 解密密钥来自**读微信进程内存**（`wechatauto-replica` 的能力：只读、不写、不注入代码）——这是当前唯一触及微信进程的地方；按 §3.1 的开源切分原则，**这一块不外发**。
### 3.3 闸门重心：内容与任务层（用户 2026-09-13 定，**改变 W3 的设计**）
原话：「L1 全局节奏…L2 会话节奏没必要限制得这么死，**只要限制发送的内容，或者某些任务不做就行**，就是之前说的那些红线。**只在内容层上做筛选**。」「这个**全局节奏、会话节奏应该可以交给用户自定义**，让他们自己把控账号的风险。」「到时候我会在介绍里面**声明，让他们自己把控账号，账号封了也别怪我**。」
⇒ 落地四条：
1. **频率/节奏类阈值默认全部为 0（＝不限）**：`per_minute / per_hour / per_day / per_chat_per_hour / min_gap_seconds` 默认 0，`quiet_hours` 默认空、`escalate_after` 默认 0（不自动暂停）。它们只作为**用户自控的旋钮**（`config.json` 的 `risk.*`；控制台面板待接）。
2. **闸门默认真正把关的是内容与任务层**：`broadcast_chats`（同一内容 300s 内发给 ≥3 个不同会话 ⇒ 判**群发**，拦）· 同会话重复内容 · 链接堆积（记录）· `block_keywords`（用户按自己的红线填）· 观察词（记录）。
3. **"某些任务不做"落在功能层**：营销群发 / 群控 / 加粉 / 换号绕风控这类能力**我们本来就不提供**，也不写教程（§3.1 第 4、5 条）。
4. **免责口径**：README/介绍里声明「账号风险由使用者自行把控与承担」，产品侧只做内容与任务层把关 + 提供可调的节奏旋钮。
## 4. 与启动器项目的关系

`dsh启动器/AGENTS.md` 里的"窗口自动化"经验（命中码探测、`SetErrorMode` 防崩溃弹窗、探针静默、按消息驱动而不动主光标）**可以直接复用**到这里的微信窗口操作与控制台工程化上。

## 5. 素材与打包（2026-09-13 实测，换图标必看）

**图标是"分层"的，换图只换主体层：**

| 素材 | 是什么 | 约束 |
|---|---|---|
| `assets/icon-whale.png` | **主体层**（256×256 正方形画布，鲸鱼内容宽 240、上下留白对称） | 被 5 处引用：左上徽章（36×36）、导出/迁移按钮内联（14×14）、大图标（52×52）、拖出动画（`.whale-fly` 44×44）、`ICON` 常量。**画布必须是正方形**——CSS 里 `width/height` 都写死同值，非方形图会被拉伸变形 |
| `.whale-badge` 的背景 | `console_html.py:187` 的 `background:#14161a`（近黑圆角方，圆角 13px） | **不动**。鲸鱼层是叠在它上面的独立 `<img>`（`left:8 bottom:6`，`transform-origin:bottom center`） |
| `assets/app-icon.png` | 黑圆角方 + 白鲸（256×256，**圆角半径 68，底色 `#0E1014`**） | 被 `onestart.py:154`、`scripts\installer.ps1`、`scripts\close_all.ps1` 引用（窗口图标 + 快捷方式图片） |
| `assets/app.ico` / `assets/exe.ico` | 多尺寸 ico（**16/24/32/48/64 用 DIB 帧、128/256 用 PNG 帧** —— 项目验证过的配方；DIB 帧的 AND 掩码必须按 alpha 生成，否则老渲染路径会画黑方块） | `app.ico` 被 `scripts\installer.ps1`、`scripts\close_all.ps1` 用（快捷方式图标）；`exe.ico` 由下面的编译命令用 |
| `assets/cursor.png` / `cursor-nod.png` | 默认光标 + 点击点头帧（64×64） | **热点写死在 CSS**：`cursor:url(...) 8 8`（`webui.py:1403/1416`）⇒ 图要"头朝左、主体铺满宽、垂直居中"；两帧画布位置必须一致否则切换会抖。浏览器硬限制 ≤128×128 |
| `assets/custom-cursor*.png` | 用户上传的自定义光标 | **运行时生成，不要手改** |
| `assets/icon.png` | favicon（`console_html.py:17` + `webui.py:256/403`）。**2026-09-13 已换成新鲸鱼**（＝app-icon 同源：黑圆角方 + 白鲸） | ⚠️ `webui.py:254-258` 是**启动时读一次**存内存 ⇒ **换 favicon 后必须重启控制台**才生效（浏览器还要 Ctrl+F5）；`assets/` 里其它素材是每请求读盘，不用重启 |

**一键启动.exe / 一键关闭.exe 是 csc 编译产物（重要，之前没人写下来）：**
- 源码：**`launcher-src\launcher.cs`、`launcher-src\close.cs`**（2026-09-13 W6 从 `_scratch/` 移入版本管理：发布物必须有源）；编译器：`C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`
- W6 起 一键启动.exe **必须引用 WebView2**：`lib\Microsoft.Web.WebView2.Core.dll` + `lib\Microsoft.Web.WebView2.WinForms.dll`，且 **`WebView2Loader.dll` 放在 exe 同目录（根目录）**（程序集解析在 `StyleKit.Prep()` 用 `AssemblyResolve` 挂到 `lib\`）
- 自检入口（W6）：`一键启动.exe --console <url>`（自带 WebView2 窗口开控制台，Python 侧不再开浏览器）· `--shot <dir>`（全部弹窗离屏渲染 PNG 作视觉证据）· `--dlgprobe`（打印弹窗/控件清单，机械核对主题与边框）
- **硬规矩：0 系统 MessageBox**（`grep "MessageBox\.Show" launcher-src\*.cs` 必须 0）；外观只在 `StyleKit` 一处定义，新窗体先 `StyleKit.Apply(this, "标题")`
- 命令（换图标后重新编译，已在 2026-09-13 实测通过）：
  ```powershell
  csc /nologo /target:winexe /optimize+ /win32icon:assets\exe.ico /r:lib\Microsoft.Web.WebView2.Core.dll /r:lib\Microsoft.Web.WebView2.WinForms.dll /out:一键启动.exe launcher-src\launcher.cs
  csc /nologo /target:winexe /optimize+ /win32icon:assets\exe.ico /r:System.Management.dll /out:一键关闭.exe launcher-src\close.cs
  ```
- **验收（比肉眼硬，口径 2026-09-13 W6 修正）**：`[System.Drawing.Icon]::ExtractAssociatedIcon(新exe).ToBitmap()` 应与 **`assets\app.ico` / `assets\exe.ico` 的 32 帧**逐一比对，**最大像素差 = 0**（本轮实测两个 exe、两个 ico 全是 0 ✓）。
  ⚠️ **别拿 `app-icon.png` 缩到 32 来当基准**：PNG→Bitmap 的默认重采样与 ico 内嵌帧不一致，会得到"最大差 580"的**假红**（本轮踩过；两者本就不是同一条渲染路径）。
- 编译只出 3 个"未使用变量"警告（`launcher.cs` 的 `ex`/`done`/`_asking`），正常

**图像处理工具**：`py -3` 可用且带 **PIL 12.3.0**（`python` 命令本身没装，用 `py -3`）；本机 `python` 会落到 Store 别名而报错。
**换素材的取证纪律**：源图要先按 **alpha>16** 裁边（生成图常带一层 `alpha=1` 的不可见雾，按 `>0` 取 bbox 会得到"整张画布"）、旧素材几何要**实测**（别自己发明位置）、换完拼一张"深底/浅底/棋盘"三行核对表看效果再落盘。
