// 群相 一键启动.exe：图形安装器（C# WinForms，嵌入鲸鱼图标，无控制台）
// 流程：准备 Python → onestart(事件) → 快捷方式询问 → 自动收尾
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace WxLauncher
{
    public class LauncherForm : Form
    {
        string Root;
        // 删掉 `PictureBox pic;` —— CS0169「从不使用字段」。
        //   它是最早版"直接 new PictureBox 摆图标"的残留；图标现在统一走 `StyleKit.MakeIcon`
        //   （返回它自己创建的那个 PictureBox，调用方不需要存句柄）⇒ 本字段从未被赋值/读取。
        Label lblTitle, lblState, lblStepHint;
        StepList stepList; // W6b：4 个裸 Label 换成自绘步骤列表（徽章 + 连接线 + 当前行高亮）
        ProgressBar bar;
        Label lblPct, lblSub;
        // ⚠️ 这里**不能**写 `StyleKit.LogView`：`LogView` 是 `namespace WxLauncher` 下的**顶层类**
        //   （stylekit.cs，与 `StyleKit` 平级、不在它里面）⇒ 带 `StyleKit.` 前缀会报 CS0426
        // 「类型"WxLauncher.StyleKit"中不存在类型名称"LogView"」。
        LogView logView; // 系统 TextBox → 自绘（诊断 §2 原因 4）
        Button btnClose;
        bool done = false;
        bool _asking;
        public bool ProbeMode = false; // 取证探针用：只渲染界面、不跑安装流程
        Form _askHolder;

        /// 每个步骤「在干什么」的**详细说明**。
        /// 与 `Steps` 一一对应；`SetStepDetail` 会在步骤切换时更新 `lblStepHint`。
        /// ⚠️ 文案口径：说**人话**（用户在等什么、这一步大概多久、卡住了看哪里），不写内部文件名。
        internal static readonly string[] Steps = {
            "准备 Python 环境",
            "检查 / 安装依赖",
            "环境自检",
            "启动机器人",
        };
        internal static readonly string[] StepDetail = {
            "在本机找一个能用的 Python（3.10~3.12）。\n"
          + "优先用自带的绿色版，找不到才会去下载 —— 这一步通常几秒，"
          + "首次下载约 1~2 分钟。",
            "逐个核对运行需要的第三方库，缺哪个就补哪个。\n"
          + "已装过的会跳过；需要下载时走国内镜像，进度会显示在下面。",
            "自检本机环境（微信版本、窗口自动化、数据库读取、语音链路等）。\n"
          + "不通过的项目会在控制台的「体检」页给出具体原因和修法。",
            "把机器人拉起来并等控制台就绪。\n"
          + "就绪后会自动打开控制台窗口，这个启动窗会自己关掉。",
        };

        public LauncherForm()
        {
            Root = Path.GetDirectoryName(Application.ExecutablePath);
            BuildUI();
        }

        void BuildUI()
        {
            Icon = null;
            try { if (File.Exists(Path.Combine(Root, "assets", "app.ico"))) { Icon = ExtractIcon(Path.Combine(Root, "assets", "app.ico")); } } catch { }
            Text = "群相 一键启动";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            BackColor = Color.FromArgb(246, 248, 252);
            // 
            //   500×440 → 620×620：留出卡片内边距 + 每个步骤的详细说明 + 更清楚的进度读数。
            // 再修（CLIP=19）：**这里的高度是"自绘标题栏以下"的设计高度** ——
            //   `StyleKit.Apply` 会再 + BarH(38) 让窗口真正长高。原来 Apply 写的是
            //   「客户区不变、内容整体下移 BarH」⇒ 内容被裁，现在方向已纠正。
            ClientSize = new Size(620, 620);

            // ── 头部：统一图标（StyleKit.MakeIcon，56px 圆角）+ 标题 + 状态 ────────
            StyleKit.MakeIcon(this, Root, new Point(StyleKit.Space.x6, StyleKit.Space.x5));
            lblTitle = new Label();
            lblTitle.Text = "群相 一键启动";
            lblTitle.Font = StyleKit.Ui(StyleKit.TextScale.Title, FontStyle.Bold);
            lblTitle.ForeColor = StyleKit.Ink;
            lblTitle.AutoSize = true;
            lblTitle.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4,
                                          StyleKit.Space.x5 + 4);
            Controls.Add(lblTitle);

            lblState = new Label();
            lblState.Text = "准备中…";
            lblState.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            lblState.ForeColor = StyleKit.Accent; // 状态是这一窗最该被先读到的东西 ⇒ 用强调色
            lblState.AutoSize = true;
            // ⛔ #2：
            //   状态行原来手写 `y = x5 + 30` —— 主标题 15pt 在 100% DPI 下行高 ~27px 尚可，
            //   125/150% DPI 下实测行高 34~40px ⇒ 手写偏移把状态行压进标题字里。
            //   修法＝**跟着标题的实测底边走**（AutoSize 标签的 Height 由字体真实量出），
            //   任何 DPI 下都刚好贴着标题下一行，不再叠字。
            lblState.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4,
                                          lblTitle.Bottom + StyleKit.Space.x2);
            Controls.Add(lblState);

            // ── 卡片 1：四个步骤（横向 4 段编号 + 名称，纵向用 StepList 自绘）──────────
            int cardW = 620 - StyleKit.Space.x6 * 2;
            // #12 F1：高度**不再手写**（原来这里抄的是 268）。
            //   `MakeCard` 只给宽度，`SealCard` 排完子控件后按"内容深度 + CardPadB"收口。
            // #13 F3：卡顶原来是 108（另 8 张卡都是 104）⇒ 统一走 `StyleKit.CardTopY`（= 图标底边 76 + 间距 28）。
            CardPanel cardSteps = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, StyleKit.CardTopY), cardW);

            Label t1 = new Label();
            t1.Text = "启动分四步";
            t1.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            t1.ForeColor = StyleKit.Ink;
            t1.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
            t1.AutoSize = true;
            cardSteps.Controls.Add(t1);

            stepList = new StepList();
            stepList.SetSteps(Steps);
            // StepList 自绘行高按 n 均分 ⇒ 给定高度决定行距。
            // ⚠️ （#18 叠字根治）：高度原写死 118（100% 下 4 步 × ~30px）——
            //   150% 下 Body 字高 27px，均分行高只剩 29px ⇒ 相邻步骤文字几乎贴上。
            //   改成"行数 × 当前行高 + 间距"（100% 下仍 ≈118，高 DPI 下自动长高）。
            //   ⚠️ y 同理：原写死 `Space.x4 + 26`，150% 下区头"启动分四步"实高 33px 会压到步骤区
            //   ⇒ 改**跟着 t1 的实测底边走**。
            stepList.Location = new Point(StyleKit.Space.x5, t1.Bottom + StyleKit.Space.x2);
            stepList.Size = new Size(cardW - StyleKit.Space.x5 * 2,
                Math.Max(118, Steps.Length * StyleKit.LineHeight(stepList.Font) + StyleKit.Space.x3));
            cardSteps.Controls.Add(stepList);

            // 步骤详细说明：随步骤切换更新
            lblStepHint = new Label();
            lblStepHint.Text = StepDetail[0];
            lblStepHint.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular); // 步骤说明是正文
            lblStepHint.ForeColor = StyleKit.InkBody;
            // ⚠️ （#18）：y 原来按"手写 26 + 手写 118"拼出来 —— 前两者都实测化后跟着新底边走
            lblStepHint.Location = new Point(StyleKit.Space.x5, stepList.Bottom + StyleKit.Space.x2);
            lblStepHint.Size = new Size(cardW - StyleKit.Space.x5 * 2, 64);
            cardSteps.Controls.Add(lblStepHint);
            StyleKit.FitLabel(lblStepHint);
            StyleKit.SealCard(cardSteps); // #12 F1：高度到这里才定下来（后面两块跟着它的底边走）

            // ── 卡片 2：进度（进度条 + 百分比读数 + 当前动作）────────────────────
            //   原来只有一个无字的圆角进度条，用户看不出"到底走到哪了"。
            // #12 F1：同样不手写高度（原来抄 116）；顶位跟着上一张卡的**实测底边**走（原来抄 388）。
            CardPanel cardProg = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, cardSteps.Bottom + StyleKit.CardGapY), cardW);

            Label t2 = new Label();
            t2.Text = "进度";
            t2.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            t2.ForeColor = StyleKit.Ink;
            t2.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
            t2.AutoSize = true;
            cardProg.Controls.Add(t2);

            bar = new RoundBar(); // W6：自绘圆角进度条（Value/Maximum 语义不变，流程代码无需改）
            // ⚠️ （#18 叠字根治）：y 原写死 `Space.x4 + 28` —— 150% 下 t2 实高 33px 会压到进度条
            //   ⇒ 改**跟着 t2 的实测底边走**。
            bar.Location = new Point(StyleKit.Space.x5, t2.Bottom + StyleKit.Space.x2);
            bar.Size = new Size(cardW - StyleKit.Space.x5 * 2 - 58, 20);
            bar.Style = ProgressBarStyle.Continuous; bar.Maximum = 100;
            cardProg.Controls.Add(bar);

            lblPct = new Label();
            lblPct.Text = "0%";
            lblPct.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            lblPct.ForeColor = StyleKit.Accent;
            lblPct.TextAlign = ContentAlignment.MiddleRight;
            // ⚠️ （#18）：原 `Size(52, 24)` 写死 —— 150% 下 Head 字高 33px 既撑破 24 的高、
            //   "100%" 的实宽也超 52 ⇒ 改 AutoSize（宽高都由字实测），右缘贴卡片右内边距、
            //   垂直方向对进度条居中；Anchor=Right 保证运行时从 "0%" 涨到 "100%" 时向左长、不顶出卡片。
            lblPct.AutoSize = true;
            lblPct.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            cardProg.Controls.Add(lblPct);
            // 垂直对进度条居中；150% 下字高(33) > bar 高(20) 时以"不叠上 t2"为下限（差 2px 呼吸）
            lblPct.Location = new Point(cardW - StyleKit.Space.x5 - lblPct.Width,
                Math.Max(t2.Bottom + 2, bar.Top + (bar.Height - lblPct.Height) / 2));

            lblSub = new Label();
            // ⛔ （可读性）：这里显示的是"当前正在做什么"（如「正在下载依赖…」），
            //   属于**正文**，不是脚注。`Small`+`Sub` 双不达标 ⇒ 提到 `Para`+`InkBody`。
            lblSub.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            lblSub.ForeColor = StyleKit.InkBody;
            // ⚠️ （#18）：y 原写死 `Space.x4 + 56`（按 100% 的 bar 底边配的）⇒ 改跟着 bar 实测底边走
            lblSub.Location = new Point(StyleKit.Space.x5, bar.Bottom + StyleKit.Space.x2);
            lblSub.Size = new Size(cardW - StyleKit.Space.x5 * 2, 34);
            cardProg.Controls.Add(lblSub);
            // ⚠️ 这里**故意不** `FitLabel`：此刻 `lblSub.Text` 还是空的，量出来是 0
            //   ⇒ 卡片会被收口成一张"没有那一行"的高度。它在运行时由 OnProgress 赋字后
            //   自己再 Fit 一次（第 222 行），保留这里的 34 是为了让 `SealCard` 量到真实的一行块。
            StyleKit.SealCard(cardProg);

            // ── 卡片 3：实时日志（自绘 LogView）────────────────────────────────
            // #12 F1：同上，不再手写 78 / 512。
            CardPanel cardLog = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, cardProg.Bottom + StyleKit.CardGapY), cardW);
            Label t3 = new Label();
            t3.Text = "实时日志";
            t3.Font = StyleKit.Ui(StyleKit.TextScale.Small, FontStyle.Bold);
            t3.ForeColor = StyleKit.Ink3ok; // Sub(4.35:1) 不达标；标签也是要读的字
            t3.Location = new Point(StyleKit.Space.x5, 8);
            t3.AutoSize = true;
            cardLog.Controls.Add(t3);

            logView = new LogView();
            // ⚠️ （#18 叠字根治）：y 原写死 26 —— 150% 下 t3 实高 24px（顶 y=8 ⇒ 底 32）
            //   会压进日志区 ⇒ 改**跟着 t3 的实测底边走**。
            logView.Location = new Point(StyleKit.Space.x5, t3.Bottom + StyleKit.Space.x1);
            // 高度同理不写死 44：按"2 行 × 当前行高"给（100% 下 ≈46，与原观感一致）
            logView.Size = new Size(cardW - StyleKit.Space.x5 * 2, Math.Max(44, StyleKit.LineHeight(logView.Font) * 2));
            cardLog.Controls.Add(logView);
            StyleKit.SealCard(cardLog);

            // #13 F2：宽度不再手写 112 —— 由"文字实宽 + 36"决定（见 `StyleKit.MakeButton`）。
            btnClose = StyleKit.MakeButton("关闭");
            // ⚠️ 原来这里 y 抄的是 566，而上一张卡底边是 590 ⇒ **按钮压在日志卡右下角**（开场第一眼就看得出）；
            //   改成"跟着上一块的实测底边走"，抄也抄不错。
            btnClose.Location = new Point(620 - StyleKit.Space.x6 - btnClose.Width, cardLog.Bottom + StyleKit.CardGapY);
            btnClose.FlatStyle = FlatStyle.Flat;
            btnClose.Enabled = false;
            btnClose.Click += (s, e) => Close();
            Controls.Add(btnClose);

            // #12 F1：窗高也不再是"先猜一个数再祈祷内容装得下" —— 由最后一块的底边反推。
            //   （`Apply` 会再 + BarH 给自绘标题栏，所以这里是"标题栏以下"的设计高度。）
            ClientSize = new Size(620, btnClose.Bottom + StyleKit.Space.x6);
            Shown += (s, e) => { if (ProbeMode) return; Thread t = new Thread(StartFlow); t.IsBackground = true; t.Start(); };
            StyleKit.Apply(this, "群相 一键启动");

        }

        static Icon ExtractIcon(string p) { return Icon.ExtractAssociatedIcon(p); }

        // 原来 `logBox` 是系统 TextBox、靠 `logBox.Lines` 截断到 60 行再回填。
        //   换成自绘 `LogView` 后**截断交给控件自己**（它内部按 _maxKeep 滚掉尾部），
        //   这里保持"线程安全 + 追加"两个语义不变，流程代码（Log(...) 的 8 处调用）零改动。
        void Log(string t)
        {
            if (InvokeRequired) { BeginInvoke((Action)(() => Log(t))); return; }
            logView.Append(t);
        }

        /// 取证用：把界面推到"进行到第 stepIdx 步"的样子（只动显示，不碰安装流程）
        public void ProbeState(int stepIdx)
        {
            SetState("环境自检…", 70, stepIdx, "Python 就绪 · 依赖已就绪 · 正在核对本机环境（第 31 项）");
            Log("[取证] 步骤列表渲染到第 " + (stepIdx + 1) + " 步（绿勾/蓝点/灰号三态同框）");
            Log("[取证] 当前动作：正在检测微信版本与窗口可操作性…");
        }

        void SetState(string txt, int pct, int stepIdx, string sub)
        {
            if (InvokeRequired) { BeginInvoke((Action)(() => SetState(txt, pct, stepIdx, sub))); return; }
            lblState.Text = txt;
            bar.Value = Math.Min(100, Math.Max(0, pct));
            lblPct.Text = Math.Min(100, Math.Max(0, pct)) + "%";
            lblSub.Text = sub ?? "";
            StyleKit.FitLabel(lblSub);
            stepList.SetProgress(stepIdx); // 徽章/连接线/高亮全在 StepList 里自绘
            SetStepDetail(stepIdx);
        }

        /// 换步骤时更新"这一步在干什么"的说明
        void SetStepDetail(int stepIdx)
        {
            if (lblStepHint == null) return;
            lblStepHint.Text = (stepIdx >= 0 && stepIdx < StepDetail.Length)
                ? StepDetail[stepIdx]
                : "";
            StyleKit.FitLabel(lblStepHint);
        }

        void ParseLine(string line)
        {
            if (line == null) return;
            if (line.StartsWith("@@PHASE:"))
            {
                string ph = line.Substring(8).Trim();
                if (ph == "deps") SetState("检查 / 安装依赖…", 12, 1, "Python 就绪");
                else if (ph == "selftest") SetState("环境自检…", 70, 2, "");
                else if (ph == "boot") SetState("启动机器人…", 92, 3, "等待控制台就绪");
            }
            else if (line.StartsWith("@@PROG:"))
            {
                string[] ps = line.Substring(7).Split(':');
                if (ps.Length >= 3)
                {
                    int dn = 0, tt = 0;
                    int.TryParse(ps[1], out dn); int.TryParse(ps[2], out tt);
                    double r = tt > 0 ? (double)dn / tt : 0;
                    if (ps[0] == "deps") SetState("检查 / 安装依赖…", 12 + (int)(33 * r), 1, "已检查 " + dn + " / " + tt + " 项");
                    else if (ps[0] == "install")
                        SetState("正在下载依赖…", 45, 1,
                                 "使用国内镜像下载安装 —— 已完成 " + dn + " / " + tt + " 个包"
                                 + (tt > 0 ? ("（" + (int)(100 * r) + "%）") : "") + "，请稍候");
                    else if (ps[0] == "selftest") SetState("环境自检…", 70 + (int)(20 * r), 2, "第 " + dn + " / " + tt + " 项");
                }
            }
            else if (line.StartsWith("@@REQ_SHORTCUT"))
            {
                AskShortcut();
            }
            else if (line.StartsWith("@@DONE"))
            {
                SetState("启动完成 ✓", 100, 4, "控制台已就绪，正在打开控制台窗口…");
                btnClose.Enabled = true;
                try { Process.Start(Application.ExecutablePath, "--ask"); } catch { }
                Thread.Sleep(300);
                BeginInvoke((Action)Close);
                done = true;
            }
            else if (line.StartsWith("@@FAIL"))
            {
                SetState("启动失败，请查看日志", 0, 0, "详见 logs\\onestart.log");
                Log("一键启动失败（onestart 事件）");
                btnClose.Enabled = true;
            }
        }

        void AskShortcut()
        {
            if (InvokeRequired) { BeginInvoke((Action)AskShortcut); return; }
            // 桌面已有快捷方式 → 不再弹询问（按**目标 exe** 判，不按名字）
            try {
                if (Ui.ShortcutOnDesktop()) {
                    Log("桌面快捷方式已存在，跳过询问");
                    return;
                }
            } catch { }
            _asking = true;
            // 这里原来**内联又搭了一遍**和 `AskForm` 几乎逐行相同的窗（诊断 §2 原因 3 的
            //   "六个窗像六个人做的"就包括这一对：图标 66、标题 14、正文 9.5、`ClientSize` 460×256）。
            //   现在直接复用 `AskForm` ⇒ 形态必然一致，改一处两边都变。
            //   差异只在"点创建之后做什么"：AskForm 内部自己建快捷方式，这里要在主窗日志里也记一笔。
            Form q = new AskForm();
            _askHolder = q;
            q.FormClosed += (sender, e2) =>
            {
                if (!Visible) Application.Exit();
            };
            q.Show();
            _asking = false;
        }

        /// 读 logs\python_path.txt 那一行（**936 与 UTF-8 都试、去 BOM、去成对引号**）并**原样返回**。
        /// ⚠️ 返回的可能是**命令**（`py -3`）而不是路径 —— 见 TryPy 的说明。
        internal static string ReadPyRaw(string pth)
        {
            try { if (!File.Exists(pth)) return ""; }
            catch { return ""; }
            foreach (var enc in new[] { Encoding.GetEncoding(936), new UTF8Encoding(false) })
            {
                try
                {
                    string t = File.ReadAllText(pth, enc).Trim().TrimStart('\uFEFF').Trim().Trim('"').Trim();
                    if (t.Length > 0) return t;
                }
                catch { }
            }
            return "";
        }

        /// 命令 →（可执行文件 + 前置参数），并要求**真跑一次**能报出版本号才算数。
        /// ⚠️ **真根因**：
        ///   `setup_python.ps1` 在**有系统 Python 3.10~3.12** 时写进 python_path.txt 的是
        ///   **命令**（`py -3` / `python`），不是路径；而这里原来一律拿 `File.Exists` 判它
        ///   ⇒「py -3」永远不是文件 ⇒ 判否 ⇒ 报"找不到 Python"（而 `.cmd` 入口早就按
        ///   「命令 + 参数」处理了，**两侧必须同源**）。这条与包体无关：`pack_online.py` 的 EXCLUDE
        ///   里有 `runtime/`（绿色 Python 不进包），所以"没有 runtime\python 但有系统 Python"是常态。
        internal static bool TryPy(string raw, out string exe, out string pre, out string ver, out string why)
        {
            exe = ""; pre = ""; ver = ""; why = "";
            string cmd = (raw ?? "").Trim().TrimStart('\uFEFF').Trim();
            if (cmd.Length == 0) { why = "那一行是空的"; return false; }
            // ⛔ （P0）**：**先判"整串是不是一个存在的文件"** ——
            //   安装路径含空格时（默认包顶层就叫 `persona morph`！），按"首个空格切分"会把路径切成
            //   `...\persona` ⇒ 报"路径不存在" ⇒ **全新机器（没装过 Python 的那种）完全起不来**。
            //   先当路径试，能命中就直接用；命不中再走"命令 + 参数"的切分（`py -3` 那种）。
            if (File.Exists(cmd))
            {
                ver = RunPy(cmd, "", "-c \"import sys;print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))\"");
                if (ver.Length == 0) { why = "跑不起来：" + cmd; return false; }
                exe = cmd; pre = "";
                why = "Python " + ver + "（" + cmd + "）";
                return true;
            }
            string first, rest;
            if (cmd[0] == '"')
            {
                int q = cmd.IndexOf('"', 1);
                first = q > 0 ? cmd.Substring(1, q - 1) : cmd.Trim('"');
                rest = q > 0 ? cmd.Substring(q + 1).Trim() : "";
            }
            else
            {
                int sp = cmd.IndexOf(' ');
                first = sp > 0 ? cmd.Substring(0, sp) : cmd;
                rest = sp > 0 ? cmd.Substring(sp + 1).Trim() : "";
            }
            bool looksPath = first.IndexOf('\\') >= 0 || first.IndexOf('/') >= 0 || first.IndexOf(':') >= 0;
            if (looksPath && !File.Exists(first)) { why = "路径不存在：" + first; return false; }
            ver = RunPy(first, rest, "-c \"import sys;print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))\"");
            if (ver.Length == 0) { why = "跑不起来：" + cmd; return false; }
            exe = first; pre = rest;
            why = "Python " + ver + "（" + cmd + "）";
            return true;
        }

        /// 试跑一次 Python（拿版本号用）；跑不起来 / 超时都返回空串，绝不抛。
        static string RunPy(string exe, string preArgs, string tailArgs)
        {
            try
            {
                var psi = new ProcessStartInfo(exe, ((preArgs ?? "") + " " + (tailArgs ?? "")).Trim());
                psi.UseShellExecute = false; psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true; psi.RedirectStandardError = true;
                using (var p = Process.Start(psi))
                {
                    if (!p.WaitForExit(20000)) { try { p.Kill(); } catch { } return ""; }
                    string o = p.StandardOutput.ReadToEnd();
                    return (o ?? "").Trim();
                }
            }
            catch { return ""; }
        }

        /// 跑一次准备脚本，**返回退出码**（-1＝没跑起来）。原来只 `WaitForExit()` 就写"准备 Python 完成"
        /// —— 那句是假的：脚本失败了我们也照样打"完成"。
        int RunSetupPy(string ps1, bool forcePortable)
        {
            try
            {
                var pi = new ProcessStartInfo("powershell.exe",
                    "-NoProfile -ExecutionPolicy Bypass -File \"" + ps1 + "\"");
                pi.UseShellExecute = false; pi.CreateNoWindow = true;
                if (forcePortable) { pi.EnvironmentVariables["WX_FORCE_PORTABLE"] = "1"; }
                using (var p = Process.Start(pi)) { p.WaitForExit(); return p.ExitCode; }
            }
            catch { return -1; }
        }

        static bool VerOk(string ver)
        {
            return ver == "3.10" || ver == "3.11" || ver == "3.12";
        }

        void StartFlow()
        {
            try
            {
                SetState("准备 Python 环境…", 5, 0, "在本机找一个能用的 Python（3.10~3.12），无需你手动安装");
                string ps1 = Path.Combine(Root, "scripts", "setup_python.ps1");
                if (!File.Exists(ps1)) { Fail("缺少 scripts\\setup_python.ps1"); return; }
                int rc = RunSetupPy(ps1, false);
                Log("准备 Python 完成（退出码 " + rc + "）");

                // （真根因见 TryPy 的注释）：先按约定找 runtime\python\python.exe，
                //   再读 logs\python_path.txt —— **两者都可能是"命令 + 参数"**，一律走 TryPy 解析并试跑。
                string pth = Path.Combine(Root, "logs", "python_path.txt");
                string convPy = Path.Combine(Root, "runtime", "python", "python.exe");
                string exe = "", pre = "", ver = "", why = "";
                bool got = TryPy(convPy, out exe, out pre, out ver, out why);
                if (!got) got = TryPy(ReadPyRaw(pth), out exe, out pre, out ver, out why);
                if (!got || !VerOk(ver))
                {
                    // 没找到 / 版本不在支持范围（3.10~3.12）⇒ 重跑一次准备脚本；版本不对就强制用绿色版
                    bool force = got && !VerOk(ver);
                    int rc2 = RunSetupPy(ps1, force);
                    Log("重跑准备 Python（退出码 " + rc2 + (force ? " · 强制绿色版" : "") + "）");
                    got = TryPy(convPy, out exe, out pre, out ver, out why);
                    if (!got) got = TryPy(ReadPyRaw(pth), out exe, out pre, out ver, out why);
                }
                if (!got || !VerOk(ver))
                {
                    // 不许出现「请重新解压完整包」这类话——
                    // 如实说**读到的是什么、为什么不行** + 一个不需要手动解压的下一步。
                    string detail = got ? ("Python 版本 " + ver + " 不在支持范围，需要 3.10~3.12") : why;
                    Fail("Python 环境异常（" + detail + "）——点「关闭」后重开一次；仍不行就在控制台点「检查更新」更新一版（不用自己解压）");
                    return;
                }
                Log("Python 就绪：" + exe + (pre.Length > 0 ? (" " + pre) : "") + "（" + why + "）");


                SetState("检查 / 安装依赖…", 12, 1, "Python 就绪，开始逐个核对依赖库");
                string onestart = Path.Combine(Root, "scripts", "onestart.py");
                var po = new ProcessStartInfo(exe, ((pre.Length > 0 ? (pre + " ") : "") + "-X utf8 \"" + onestart + "\"").Trim());
                po.UseShellExecute = false;
                po.CreateNoWindow = true;
                po.RedirectStandardOutput = true;
                po.RedirectStandardError = true;
                po.EnvironmentVariables["WX_GUI"] = "1";
                using (var p = Process.Start(po))
                {
                    p.OutputDataReceived += (s, e) => { if (e.Data != null && e.Data.StartsWith("@@")) { Log(e.Data); ParseLine(e.Data); } };
                    p.ErrorDataReceived += (s, e) => { };
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();
                    p.WaitForExit();
                }
            }
            catch (Exception ex)
            {
                Fail("启动异常：" + ex.Message);
            }
        }

        void Fail(string msg)
        {
            if (InvokeRequired) { BeginInvoke((Action)(() => Fail(msg))); return; }
            SetState("一键启动失败", 0, 0, "这是一次失败：详见 logs\\onestart.log（下面那行是原因）");
            Log(msg);
            btnClose.Enabled = true;
            btnClose.Focus();
        }
    }

    public static class Ext { public static string[] SubArray(this string[] a, int n) { if (n <= 0) return new string[0]; var r = new string[n]; Array.Copy(a, a.Length - n, r, 0, n); return r; } }




    public class BusyForm : Form
    {
        public BusyForm()
        {
            string root = Path.GetDirectoryName(Application.ExecutablePath);
            Text = "群相 一键启动";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            BackColor = Color.FromArgb(246, 248, 252);
            // 400×222 → 480×322（卡片化 + 补上"你现在可以做什么"的说明）
            //   ⚠️ 这个高度是**标题栏以下**的设计高度（`Apply` 再 + BarH 给窗口长高，见 stylekit.cs）
            ClientSize = new Size(480, 322);
            try { string ico = Path.Combine(root, "assets", "app.ico"); if (File.Exists(ico)) Icon = Icon.ExtractAssociatedIcon(ico); } catch { }

            // 统一图标（56px 圆角）+ 标题（TextScale.Head 12.5 新档）+ 状态
            StyleKit.MakeIcon(this, root, new Point(StyleKit.Space.x6, StyleKit.Space.x5));

            Label t = new Label();
            t.Text = "已经在运行了";
            t.Font = StyleKit.Ui(StyleKit.TextScale.Title, FontStyle.Bold);
            t.ForeColor = StyleKit.Ink;
            t.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4, StyleKit.Space.x5 + 2);
            t.AutoSize = true;
            Controls.Add(t);

            Label s = new Label();
            s.Text = "无需重复启动";
            s.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            s.ForeColor = StyleKit.Ok; // 这不是错误 ⇒ 用"正常"绿，不用红/黄
            // ⚠️ （#18 叠字根治）：y 原写死 `Space.x5 + 30`（100% 的标题字高配的）——
            //   150% 下 Title(15f Bold) 实高 40px ⇒ 副标题叠进标题 12px（用户截图现场）。
            //   改**跟着标题的实测底边走**，任何 DPI 下都刚好在标题下一行。以下五个弹窗同款。
            s.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4,
                                   t.Bottom + StyleKit.Space.x1);
            s.AutoSize = true;
            Controls.Add(s);

            // 卡片：把"发生了什么 / 你可以怎么做"分开写
            int cardW = 480 - StyleKit.Space.x6 * 2;
            // #12 F1 / #13 F3：同上 —— 高度交给 `SealCard` 算，卡顶走 `StyleKit.CardTopY`（原手写 104）。
            CardPanel card = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, StyleKit.CardTopY), cardW);

            Label m1 = new Label();
            m1.Text = "检测到「一键启动」已经在运行中，所以这次不再重复拉起一个。";
            m1.Font = StyleKit.Ui(StyleKit.TextScale.Body, FontStyle.Regular);
            m1.ForeColor = StyleKit.Ink;
            m1.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
            // ⚠️ （#18 叠字根治）：高度原写死 22 —— 150% 下 Body 字高 27px ⇒ m1 自己先被裁。
            //   改 `Size(w,1)` + `FitLabel` 实测落回（本文件 m2/m3 早就是这个口径）。
            m1.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(m1);
            int m1h = StyleKit.FitLabel(m1);

            Label m2 = new Label();
            m2.Text =
                "接下来可以这样做：\n"
              + "  · 看不到启动窗口 —— 它可能被别的程序挡住，稍候几秒通常会自己出来；\n"
              + "  · 想重新走一遍启动流程 —— 先点「一键关闭.exe」把当前这份结束掉，再重新双击「一键启动」。";
            m2.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            m2.ForeColor = StyleKit.InkBody;
            // ⚠️ （#18）：y 原写死 `Space.x4 + 30`（按 m1 手写 22 配的）⇒ 改跟 m1 实测底边走
            m2.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4 + Math.Max(1, m1h) + StyleKit.Space.x2);
            // ⛔ （真 bug：正文被裁）：这里原来是 `new Size(w, 90)` —— 手写 90，
            //   而实测需要 ~152 ⇒ 后两条 bullet 被 CardPanel 裁掉（实拍 shots-20260923d/busy.png）。
            //   根因是当时 `FitLabel` 只长不缩，手写值偏大时不会被修正（现已改双向）。
            //   ⇒ 这里**不再手写高度**（写 1 只为让 MeasureText 有个非零初值），高度一律由 FitLabel 落回实测。
            m2.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(m2);
            StyleKit.FitLabel(m2);
            //   ⇒ 卡片高度到这里收口（原来手抄 152）。
            StyleKit.SealCard(card);

            // #13 F2：宽度不再抄 120 —— `MakeButton` 按文字实宽给。
            Button ok = StyleKit.MakeButton("好的");
            // 按钮顶也不再抄 272：跟着卡片实测底边走。
            ok.Location = new Point(480 - StyleKit.Space.x6 - ok.Width, card.Bottom + StyleKit.CardGapY);
            ok.FlatStyle = FlatStyle.Flat;
            ok.BackColor = Color.FromArgb(64, 140, 255);
            ok.ForeColor = Color.White;
            ok.DialogResult = DialogResult.OK;
            Controls.Add(ok);
            // #12 F1：窗高由最后一块底边反推（原来抄 322）。
            ClientSize = new Size(480, ok.Bottom + StyleKit.Space.x6);
            AcceptButton = ok;
            StyleKit.Apply(this, "群相 正在启动"); // ⚠️ 必须最后调：Apply 之前设 FixedDialog，之后不得再改边框（否则系统标题栏会回来，和自绘标题栏叠成两条）
        }
    }

    /// <summary>
    /// PySide6 首启自动装的**提前告知窗**（BusyForm 同族骨架）。
    /// 有「旧版仍在跑」的窗口期，不能依赖它做安装）；本窗只是把「首启要多下载
    /// 约 100MB 界面组件」提前公告给用户（对齐「任何包换入都必须经过用户可见的
    /// 公告与选择」），自己不装任何东西、不拦启动流程——点「继续」照常走 LauncherForm。
    /// 文案口径与 qt_bootstrap 一致：叫「界面组件」、讲清约 100MB、讲清网页控制台
    /// </summary>
    public class QtBootForm : Form
    {
        /// <summary>缺界面组件时返回弹窗理由；不缺 / 无法判断返回 null（启动器不拦路）。
        /// 版本核对钉在 Python 侧 qt_bootstrap.PYSIDE_PIN（单一来源，绝不两边各写一个版本号）；
        /// 这里只做目录存在性检测——版本不符的场景由 Python 侧幂等重装，启动器不重复判。</summary>
        public static string MissingReason(string root)
        {
            try
            {
                if (!File.Exists(Path.Combine(root, "runtime", "python", "python.exe")))
                    return null; // 没有内置 Python ⇒ Python 侧自举自己处理
                string sp = Path.Combine(root, "runtime", "python", "Lib", "site-packages");
                if (!Directory.Exists(Path.Combine(sp, "PySide6")))
                    return "首次启动需要下载界面组件（约 100MB）";
                return null;
            }
            catch { return null; } // 探测本身出错 ⇒ 不拦路（不是新故障面）
        }

        public QtBootForm(string reason)
        {
            string root = Path.GetDirectoryName(Application.ExecutablePath);
            Text = "群相 一键启动";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            BackColor = Color.FromArgb(246, 248, 252);
            // 设计宽度 480（同 BusyForm）；高度最后由按钮底边反推
            ClientSize = new Size(480, 322);
            try { string ico = Path.Combine(root, "assets", "app.ico"); if (File.Exists(ico)) Icon = Icon.ExtractAssociatedIcon(ico); } catch { }

            // 统一图标 + 标题 + 副标（同 BusyForm 的骨架：图标 56 / 卡片化 / 高度收口交给实测）
            StyleKit.MakeIcon(this, root, new Point(StyleKit.Space.x6, StyleKit.Space.x5));

            Label t = new Label();
            t.Text = "要先把界面组件装好";
            t.Font = StyleKit.Ui(StyleKit.TextScale.Title, FontStyle.Bold);
            t.ForeColor = StyleKit.Ink;
            t.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4, StyleKit.Space.x5 + 2);
            t.AutoSize = true;
            Controls.Add(t);

            Label s = new Label();
            s.Text = "只此一次 · 不影响数据";
            s.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            s.ForeColor = StyleKit.Ok; // 不是错误 ⇒ 用"正常"绿
            // ⚠️ （#18 叠字根治）：跟着标题实测底边走（原写死 +30，150% 下叠 12px）——BusyForm 同款
            s.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4,
                                   t.Bottom + StyleKit.Space.x1);
            s.AutoSize = true;
            Controls.Add(s);

            // 卡片：发生了什么（reason 由 MissingReason 给：首启下载 / 组件缺失）+ 你可以怎么做
            int cardW = 480 - StyleKit.Space.x6 * 2;
            CardPanel card = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, StyleKit.CardTopY), cardW);

            Label m1 = new Label();
            m1.Text = reason + " 这次更新后，控制台界面换成了更稳的原生窗口，"
              + "它会自动从国内下载源装好，装完自己继续，不用你操作。";
            m1.Font = StyleKit.Ui(StyleKit.TextScale.Body, FontStyle.Regular);
            m1.ForeColor = StyleKit.Ink;
            m1.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
            // ⚠️ （#18 叠字根治）：高度原写死 22 ⇒ 改 FitLabel 实测（BusyForm 同款）
            m1.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(m1);
            int m1h = StyleKit.FitLabel(m1);

            Label m2 = new Label();
            m2.Text =
                "接下来可以这样做：\n"
              + "  · 什么都不用做 —— 下载和安装自动进行，大约 1~3 分钟（看网速）；\n"
              + "  · 急着用控制台 —— 网页控制台照常能用，不受这次安装影响；\n"
              + "  · 如果最后提示没装上 —— 检查一下网络，重新打开程序会自动再试。";
            m2.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            m2.ForeColor = StyleKit.InkBody;
            // ⚠️ （#18）：y 改跟 m1 实测底边走（原写死 `Space.x4 + 30`）
            m2.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4 + Math.Max(1, m1h) + StyleKit.Space.x2);
            // 高度不手写（BusyForm 实测教训：手写会被裁），交给 FitLabel 实测落回
            m2.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(m2);
            StyleKit.FitLabel(m2);
            StyleKit.SealCard(card);

            Button ok = StyleKit.MakeButton("知道了，继续");
            ok.Location = new Point(480 - StyleKit.Space.x6 - ok.Width, card.Bottom + StyleKit.CardGapY);
            ok.FlatStyle = FlatStyle.Flat;
            ok.BackColor = Color.FromArgb(64, 140, 255);
            ok.ForeColor = Color.White;
            ok.DialogResult = DialogResult.OK;
            Controls.Add(ok);
            ClientSize = new Size(480, ok.Bottom + StyleKit.Space.x6);
            AcceptButton = ok;
            StyleKit.Apply(this, "群相 界面组件"); // 必须最后调（同 BusyForm 注释）
        }
    }

    internal static class PortHelper
    {
        internal static int ReadPort()
        {
            // ⛔ 
            //   老实现是"**全文找第一个 `"port"`**" —— 在真实 config.json 上它抓到的是 `local_sd` 段的键
            //   （实拍解析出字符串 `"model_dir"`）⇒ `int.TryParse` 失败 ⇒ **恒回落 3210**。
            //   于是"用户把 server.port 改过 / 3210 被别人占着"这两种情形下，启动器预检连的是错的端口。
            //   这跟当年"在 config.json 里瞎找 token、抓到 cloud.token（空）⇒ 401"是同一个病。
            //   现在：①先定位 `"server"` 段再取它的 `port`；②取不到就回读 `logs\console.url` 的端口
            //   （那是"上一次真跑起来的端口"，比配置更接近真相）；③都没有才回落 3210。
            string root = Path.GetDirectoryName(Application.ExecutablePath);
            int p = Ui.ServerPortFromConfig(root);
            if (p > 0) return p;
            int p2 = Ui.PortFromUrlFile(root);
            if (p2 > 0) return p2;
            return 3210;
        }
    }

    public class AskForm : Form
    {
        string Root;
        System.Windows.Forms.Timer watch;
        int failCount = 0;

        public AskForm()
        {
            Root = Path.GetDirectoryName(Application.ExecutablePath);
            Text = "群相 启动完成";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            BackColor = Color.FromArgb(246, 248, 252);
            // 460×256 → 520×368（卡片化 + 把"快捷方式有什么用"讲清楚）
            //   ⚠️ 这个高度是**标题栏以下**的设计高度（`Apply` 再 + BarH 给窗口长高，见 stylekit.cs）
            ClientSize = new Size(520, 368);
            try { string ico = Path.Combine(Root, "assets", "app.ico"); if (File.Exists(ico)) Icon = Icon.ExtractAssociatedIcon(ico); } catch { }

            StyleKit.MakeIcon(this, Root, new Point(StyleKit.Space.x6, StyleKit.Space.x5));

            Label qt = new Label();
            qt.Text = "启动完成";
            qt.Font = StyleKit.Ui(StyleKit.TextScale.Title, FontStyle.Bold);
            qt.ForeColor = StyleKit.Ink;
            qt.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4, StyleKit.Space.x5 + 2);
            qt.AutoSize = true;
            Controls.Add(qt);

            Label qs = new Label();
            qs.Text = "机器人已就绪";
            qs.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            qs.ForeColor = StyleKit.Ok;
            // ⚠️ （#18 叠字根治）：跟着标题实测底边走（原写死 +30，150% 下叠 12px）——BusyForm 同款
            qs.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4,
                                    qt.Bottom + StyleKit.Space.x1);
            qs.AutoSize = true;
            Controls.Add(qs);

            int cardW = 520 - StyleKit.Space.x6 * 2;
            // #12 F1 / #13 F3：高度由 `SealCard` 算，卡顶走 `StyleKit.CardTopY`（原手写 104 / 178）。
            CardPanel card = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, StyleKit.CardTopY), cardW);

            Label qm = new Label();
            qm.Text = "欢迎使用 群相！要不要在桌面放一个「一键启动」的快捷方式？";
            qm.Font = StyleKit.Ui(StyleKit.TextScale.Body, FontStyle.Regular);
            qm.ForeColor = StyleKit.Ink;
            qm.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
            // ⚠️ （#18 叠字根治）：高度原写死 22 ⇒ 改 FitLabel 实测（BusyForm 同款）
            qm.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(qm);
            int qmh = StyleKit.FitLabel(qm);

            Label qm2 = new Label();
            qm2.Text =
                "有它的话：\n"
              + "  · 以后双击桌面上那个图标就能启动，不用再翻安装目录；\n"
              + "  · 图标就是这只鲸鱼，和开始菜单里那个是同一个。\n"
              + "不想要也没关系 —— 随时可以从安装目录里再双击「一键启动.exe」。";
            qm2.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            qm2.ForeColor = StyleKit.InkBody;
            // ⚠️ （#18）：y 改跟 qm 实测底边走（原写死 `Space.x4 + 30`）
            qm2.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4 + Math.Max(1, qmh) + StyleKit.Space.x2);
            // ⛔ （真 bug：正文被裁）：原手写 116，实测需要 ~152 ⇒ 最后一条被裁。
            qm2.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(qm2);
            StyleKit.FitLabel(qm2);
            StyleKit.SealCard(card);

            // #13 F2：两颗按钮的宽度都由文字实宽决定（原来抄 172 / 96），
            //   y 与两颗之间的排布也不再抄 322 —— 跟着卡片底边、彼此的宽度走。
            Button qok = StyleKit.MakeButton("创建桌面快捷方式");
            Button qno = StyleKit.MakeButton("暂不");
            int qy = card.Bottom + StyleKit.CardGapY;
            qok.Location = new Point(520 - StyleKit.Space.x6 - qok.Width, qy);
            qno.Location = new Point(520 - StyleKit.Space.x6 - qok.Width - StyleKit.Space.x2 - qno.Width, qy);
            qok.FlatStyle = FlatStyle.Flat;
            qok.BackColor = Color.FromArgb(64, 140, 255);
            qok.ForeColor = Color.White;
            qok.Click += (s, e) =>
            {
                try
                {
                    Type t = Type.GetTypeFromProgID("WScript.Shell");
                    dynamic ws = Activator.CreateInstance(t);
                    string desk = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                    dynamic sc = ws.CreateShortcut(Path.Combine(desk, "一键启动 群相.lnk"));
                    sc.TargetPath = Path.Combine(Root, "一键启动.exe");
                    sc.WorkingDirectory = Root;
                    sc.IconLocation = Path.Combine(Root, "assets", "app.ico");
                    sc.Save();
                }
                catch (Exception ex) { }
                Close();
            };
            Controls.Add(qok);
            qno.FlatStyle = FlatStyle.Flat;
            qno.Click += (s, e) => Close();
            Controls.Add(qno);
            AcceptButton = qok; CancelButton = qno;

            // 监控控制台（端口从配置/落盘地址现读）：连续 3 次连不上 → 控制台已关闭 → 自动关闭本窗
            watch = new System.Windows.Forms.Timer();
            watch.Interval = 2000;
            watch.Tick += (s, e) =>
            {
                try
                {
                    using (var cc = new System.Net.Sockets.TcpClient()) { cc.Connect("127.0.0.1", PortHelper.ReadPort()); }
                    failCount = 0;
                }
                catch
                {
                    failCount++;
                    if (failCount >= 3) { try { watch.Stop(); Close(); } catch { } }
                }
            };
            watch.Start();
            // #12 F1：窗高由最后一块底边反推（原来抄 368）。
            ClientSize = new Size(520, qok.Bottom + StyleKit.Space.x6);
            FormClosed += (s, e) => { try { watch.Stop(); } catch { } };
            StyleKit.Apply(this, "群相 启动完成"); // 同 BusyForm：Apply 放最后，避免系统标题栏与自绘标题栏叠两条
        }
    }

    /// 全屏时顶栏「贴顶滑出」的**纯决策**（可离线验：`一键启动.exe --peekprobe`，不起任何窗口）。
///
/// ⛔ 
///   原来只有"光标贴屏幕顶端 4px 内"**一个**判据 —— 鼠标一往下挪（去点最小化/还原/关闭）就离开那 4px，
///   200ms 后顶栏立刻收起 ⇒ **按钮永远点不到**（三个按钮都在顶栏 y=8~34 那一带里）。
///   ⇒ 拆成三条：①**进入**＝贴到顶端 4px 内（要有意去碰，不能靠"页面顶部路过"就弹出来）；
///   ②**保持**＝光标还在**顶栏自己那块区域**内（顶端 + 顶栏高 + 一点余量）⇒ 从顶端挪到按钮上不会中途收起；
///   ③**离开**后再给 0.7 秒宽限（手抖/斜着挪出去又回来，不会闪掉）。
///   判据（`--peekprobe` 里的四条仿真路径，含"不上顶端就绝不弹出"的反面控制）：
///     A 贴顶→挪到按钮 → 按钮可见；B 贴顶→挪走并等过宽限 → 收起；C 中途抖出去又回来 → 仍可见；D 不碰顶端 → 从不出现。
internal static class BarPeek
{
    public const int EdgeBand = 4; // 贴到屏幕顶端 4px 内＝"我要顶栏"
    public const int KeepSlack = 6; // 顶栏区域再放宽 6px（鼠标斜着挪出去一点不算离开）
    public const int GraceMs = 700; // 离开顶栏区域后再留 0.7 秒
    public const int TickMs = 120; // 定时器间隔（比原来的 200ms 更跟手）

    /// 推进一帧。返回新的"是否显示"，并把"离开起始时刻"写回 `leaveAtMs`（0＝没在倒计时）。
    /// `nowMs` 用 `Environment.TickCount`；用无符号差值比较，**跨 0 溢出也不会误判**。
    public static bool Next(bool peeked, bool hitEdge, bool overBar, int nowMs, ref int leaveAtMs)
    {
        if (hitEdge) { leaveAtMs = 0; return true; }
        if (!peeked) { return false; }
        if (overBar) { leaveAtMs = 0; return true; }
        if (leaveAtMs == 0) { leaveAtMs = nowMs; return true; }
        if (unchecked(nowMs - leaveAtMs) >= GraceMs) { leaveAtMs = 0; return false; }
        return true;
    }

    /// 离线仿真（纯计算、不打屏、不起窗）：把上面四条路径跑一遍，打印 ASCII 标记给判据解析。
    public static string Probe()
    {
        const int Top = 0; // 屏顶
        const int BarH = 42; // 顶栏高（与 ConsoleForm 里的 42 一致）
        var sb = new StringBuilder();
        int fail = 0;

        // A：并停住
        bool aOver = true;
        {
            int leave = 0; bool peeked = false;
            int[] path = { 1, 1, 8, 14, 20, 20, 20, 20 };
            for (int i = 0; i < path.Length; i++)
            {
                int y = path[i];
                bool hit = y <= Top + EdgeBand;
                bool over = y <= Top + BarH + KeepSlack;
                peeked = Next(peeked, hit, over, i * TickMs, ref leave);
                if (y >= 8 && y <= 34 && !peeked) aOver = false; // 按钮那一带只要有一帧收起就算失败
            }
            if (!peeked) aOver = false;
        }
        sb.AppendLine("PEEK A over_button_visible=" + (aOver ? 1 : 0));
        if (!aOver) fail++;

        // A_OLD：**同一条路径换成老判据**（只有"贴顶 4px"才算显示）跑一遍 —— 必须看到它一挪到按钮就收起。
        //   这是判据的**灵敏度证明**：修好后 A=1 且 A_OLD=0，说明"保持区 + 宽限"正是起作用的那一环；
        //   哪天有人把保持区删回去，A_OLD 会变成 1 ⇒ 这条判据立刻报红。
        bool aOldOver = true;
        {
            int[] path = { 1, 1, 8, 14, 20, 20, 20, 20 };
            for (int i = 0; i < path.Length; i++)
            {
                bool peeked = path[i] <= Top + EdgeBand; // 老判据：只看那 4px，没有保持区、没有宽限
                if (path[i] >= 8 && path[i] <= 34 && !peeked) aOldOver = false;
            }
        }
        sb.AppendLine("PEEK A_OLD over_button_visible=" + (aOldOver ? 1 : 0));
        if (aOldOver) fail++; // 老判据要是也"过"，说明这条判据根本没在测东西

        // B：贴顶端 → 挪走 → 等过宽限 ⇒ 必须收起
        bool bHide;
        {
            int leave = 0; bool peeked = false;
            int[] path = { 1, 1, 300, 300, 300, 300, 300, 300, 300 };
            for (int i = 0; i < path.Length; i++)
            {
                int y = path[i];
                peeked = Next(peeked, y <= Top + EdgeBand, y <= Top + BarH + KeepSlack, i * TickMs, ref leave);
            }
            bHide = !peeked;
        }
        sb.AppendLine("PEEK B leaves_hides=" + (bHide ? 1 : 0));
        if (!bHide) fail++;

        // C：中途抖出去（还没过宽限）又回来 ⇒ 全程可见
        bool cKeep;
        {
            int leave = 0; bool peeked = false;
            int[] path = { 1, 20, 200, 20, 20, 20 }; // 200 那一帧离开，但累计未过 700ms
            bool ever = false;
            for (int i = 0; i < path.Length; i++)
            {
                int y = path[i];
                peeked = Next(peeked, y <= Top + EdgeBand, y <= Top + BarH + KeepSlack, i * TickMs, ref leave);
                if (!peeked) ever = true;
            }
            cKeep = !ever && peeked;
        }
        sb.AppendLine("PEEK C jitter_keeps=" + (cKeep ? 1 : 0));
        if (!cKeep) fail++;

        // D：**反面控制** —— 从不碰顶端，只在页面顶部那 40px 里晃 ⇒ 绝不许弹出（否则正常用页面就被挡）
        bool dNoShow = true;
        {
            int leave = 0; bool peeked = false;
            for (int i = 0; i < 12; i++)
            {
                int y = 20 + (i % 3) * 8;
                peeked = Next(peeked, y <= Top + EdgeBand, y <= Top + BarH + KeepSlack, i * TickMs, ref leave);
                if (peeked) dNoShow = false;
            }
        }
        sb.AppendLine("PEEK D no_edge_no_show=" + (dNoShow ? 1 : 0));
        if (!dNoShow) fail++;

        sb.AppendLine("PEEK GRACE_MS=" + GraceMs);
        sb.AppendLine("PEEK TICK_MS=" + TickMs);
        sb.AppendLine("PEEK BAR_H=" + BarH);
        sb.AppendLine("PEEK RESULT=" + (fail == 0 ? "ok" : ("fail:" + fail)));
        return sb.ToString().TrimEnd();
    }
}

static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            // ⚠️ （接手方回执，
            //   出现在开跑 0.4 秒内），而 stdout 与 stderr 都是空的 ⇒ 它抛在**没有被 try/catch 罩住的地方**」）：
            //
            //   为什么"弹系统窗 + stdout 全空"会同时出现（本条的根因，不是猜测）：
            //   · 这是 **winexe**（`/target:winexe`）⇒ 进程**没有控制台**。任何"写 stdout"的尝试都拿不到句柄；
            //   · 异常抛在 `Main` 之外（或 `Main` 里还没进 try 的地方）⇒ CLR 走**默认**未处理异常路径：
            //     winexe 下这一步是弹 **WER 系统对话框**（`.NET` 的 `ThreadException` 只在有消息循环时接管，
            //     而 `Main` 里我们**没有** `Application.Run`）。那个对话框是**系统级的**，不是 WinForms 的
            //     MessageBox —— 所以它既不受 `StyleKit.Prep()` 里 `SetErrorMode` 的约束，也不会往
            //     stdout/stderr 写一个字。这就完整解释了"弹窗 + 两边都空"。
            //   · 更要紧的：`SetErrorMode` 那句在 `StyleKit.Prep()` 里，而 `Prep()` 是 `Main` 的第一句 ——
            //     也就是说**在它执行之前，进程是完全裸奔的**。0.4 秒这个时间点，正好落在"Prep 之后、
            //     ShotProbe 的 try 之前"（`EnableVisualStyles` / 窗体构造 都在这一段）。
            //
            //   修法（把"裸奔区"压到零）：**在 `Main` 的第一条语句**就把两个钩子挂上，并且
            //   在进入任何分支之前，就把"异常落盘到哪"确定下来。这样即使 `Prep()` 自己炸，
            //   也能留下证据（`--shot <dir>` 传了目录就写那儿，否则写 exe 旁的 `_scratch\launcher.err`）。
            string bootErr = null;
            try
            {
                bootErr = (args != null && args.Length > 1 && args[0] == "--shot")
                    ? Path.Combine(args[1], "shot.err")
                    : Path.Combine(Path.GetDirectoryName(Application.ExecutablePath) ?? ".", "_scratch", "launcher.err");
            }
            catch { }
            Action<string> bootLog = delegate (string s)
            {
                if (bootErr == null) return;
                try
                {
                    string d = Path.GetDirectoryName(bootErr);
                    if (!string.IsNullOrEmpty(d)) Directory.CreateDirectory(d);
                    File.AppendAllText(bootErr, s + Environment.NewLine, new UTF8Encoding(false));
                }
                catch { }
            };
            AppDomain.CurrentDomain.UnhandledException += delegate (object s, UnhandledExceptionEventArgs e)
            {
                bootLog("[Boot/UnhandledException] " + (e.ExceptionObject == null ? "(null)" : e.ExceptionObject.ToString()));
            };
            try { Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException); } catch { }
            Application.ThreadException += delegate (object s, System.Threading.ThreadExceptionEventArgs e)
            {
                bootLog("[Boot/ThreadException] " + e.Exception.ToString());
            };

            StyleKit.Prep(); // W6：高 DPI（PerMonitorV2）+ 关掉系统崩溃弹窗（产品不许弹系统 MessageBox）
            // W6 取证/集成入口：
            //   --console <url>  用自带标题栏的 WebView2 窗口打开控制台（Python 侧不再开浏览器）
            //   --shot <dir>     把每个弹窗离屏渲染成 PNG（不出现在屏幕上、不抢焦点）
            //   --dlgprobe       打印弹窗清单与控件（机械判据用）
            // --console-reuse <url> **复用分支也强制重新导航**
            //       由来：`agent/notify_ui.py` 的复用分支只 flash/抬起、**从不导航** ⇒ 窗口里若是一张
            // ERR_CONNECTION_REFUSED 的旧页，用户怎么点都是那一屏死页。
            //       这里让新起的这个进程把地址递给**已经开着的**那个窗，由它重新导航 + 抬起，
            //       然后本进程退出（不攒第二个窗）。找不到开着的窗就照旧新开（等价 `--console`）。
            if (args != null && args.Length > 1 &&
                (args[0] == "--console" || args[0] == "--console-reuse"))
            {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                if (args[0] == "--console-reuse" && Ui.HandOffToConsole(args[1])) return;
                Application.Run(new ConsoleForm(args[1]));
                return;
            }
            if (args != null && args.Length > 1 && args[0] == "--shot")
            {
                // ⚠️ `--shot` 跑起来会弹 1 个系统错误窗「参数无效。」，
                //   出现在开跑 0.4 秒内，而 stdout 与 stderr **都是空的**）：
                //   说明它抛在**没有被 try/catch 罩住的地方** —— 就是在 `Ui.ShotProbe()` 里。
                //   老实现只在 `TryShot` 捕获"构造窗体"那一层；而 `CaptureOffscreen` 里的
                //   `ShowWindow` / `DrawToBitmap` / `Bitmap.Save` 都在它的下游，任一处抛都会冒到 Main。
                //   修法（三件，缺一不可）：
                //     ①把**每一步**各自包起来（见 `Shot` 里分段 try/catch）；
                //     ②两个全局钩子兜底 —— winexe 下 `AppDomain.UnhandledException` **不会**弹 CLR 窗，
                //     ③所有异常 `ex.ToString()` **append 到同一个文件**（不是覆盖），下次可直接给行号。
                //
                // 落盘路径：`--shot <目录>` 的目录里放 `shot.err`（和 PNG 在一起，好找）。
                // （全局钩子已在 `Main` 第一句挂好，这里不重复注册 —— 两处都注册会让同一条异常写两遍。）
                string shotDir = args[1];
                string errPath = Path.Combine(shotDir, "shot.err");
                Action<string> log = delegate (string s)
                {
                    try { Directory.CreateDirectory(shotDir); File.AppendAllText(errPath, s + Environment.NewLine, new UTF8Encoding(false)); } catch { }
                };
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                try { Application.EnableVisualStyles(); } catch (Exception ex) { log("[EnableVisualStyles] " + ex.ToString()); }
                try { Application.SetCompatibleTextRenderingDefault(false); } catch (Exception ex) { log("[SetCompatibleTextRenderingDefault] " + ex.ToString()); }
                string shotOut = "";
                try { shotOut = Ui.ShotProbe(shotDir); }
                catch (Exception ex)
                {
                    shotOut = "SHOTPROBE ABORTED " + ex.GetType().Name + ": " + ex.Message;
                    log("[ShotProbe] " + ex.ToString());
                }
                // ⚠️ **正常路也落一份**（接手方："正常路也落一份更好，落盘比控制台稳"）。
                //   同 `--dlgprobe` 的处理 ⇒ 两个探针的行为一致：文件在不在本身就是一条判据。
                try { log("[ShotProbe/OK] exit=0" + Environment.NewLine + shotOut); } catch { }
                try { Console.WriteLine(shotOut); } catch { }
                return;
            }
            if (args != null && args.Length > 0 && args[0] == "--winprobe")
            {
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Console.WriteLine(Ui.WinProbe());
                return;
            }
            if (args != null && args.Length > 0 && args[0] == "--urlprobe")
            {
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                Console.WriteLine(Ui.UrlProbe());
                return;
            }
            if (args != null && args.Length > 0 && args[0] == "--peekprobe")
            {
                // 全屏顶栏"贴顶滑出"的**离线仿真**：纯计算、不打屏、不起任何窗口（不进 Application.Run）。
                // 由这里的 A 条守着。
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                Console.WriteLine(BarPeek.Probe());
                return;
            }
            if (args != null && args.Length > 1 && args[0] == "--cursorprobe")
            {
                // 光标点头取证：在**我们自己的 WebView2 窗口**里（屏外、不激活）真跑一遍
                // 「鲸鱼光标 + 点击换歪头帧」这条链路，量出来给判据用（不靠肉眼、不截屏）。
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Console.WriteLine(Ui.CursorProbe(args[1]));
                return;
            }
            if (args != null && args.Length > 0 && args[0] == "--dlgprobe")
            {
                // ⚠️ winexe 下 CLR 弹系统窗「参数无效。」，进程停住不返回，跑 3 次弹 3 次）：
                //   控制台宿主（/main:ProbeHarness + try/catch 直接调 `Ui.DlgProbe()`）**是好的**（返回 5018 字符）
                //   ⇒ 异常在 winexe 子系统或外层，不在 `DlgProbe()` 里。而在 `winexe` 下：
                //     · `Console.OutputEncoding = ...` 与 `Console.WriteLine(...)` 在**没有控制台**时，
                //       .NET 会去拿 `GetStdHandle` 的句柄，拿不到就可能在这里抛「参数无效。」
                //       （这正是"弹系统窗"的来源 —— 违反硬规矩 ①「0 系统 MessageBox」）；
                //     · 所以这里**三层全包**：编码设置 / 探测体 / 输出，并把 `ex.ToString()` 落盘。
                //   落盘优先走 argv[1]（用户给的那个参数），否则默认 `_scratch\dlgprobe.err`。
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                try
                {
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                }
                catch { }
                // ⚠️ 补（接手方回执
                // 正常路也落一份更好」）：
                //   原来只在 catch 里写文件 ⇒ 正常跑完一个字都不落。现在**两条路都落**：
                // · 正常路写 `<路径>`；
                //     · 异常路写 `<路径>.err`（原始 ex.ToString()），并在 `<路径>` 里留一行指向它。
                //   这样接手方复跑时"先看文件在不在"，在不在本身就是一种判据。
                string outPath = (args.Length > 1 && !string.IsNullOrEmpty(args[1]))
                    ? args[1]
                    : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "_scratch", "dlgprobe.out");
                string errPath = outPath + ".err";
                Action<string, string> dump = delegate (string p, string body)
                {
                    try
                    {
                        string d = Path.GetDirectoryName(p);
                        if (!string.IsNullOrEmpty(d)) Directory.CreateDirectory(d);
                        File.WriteAllText(p, body, new UTF8Encoding(false));
                    }
                    catch { }
                };
                try
                {
                    string probe = Ui.DlgProbe();
                    dump(outPath, probe); // ← 正常路也落盘
                    try { Console.WriteLine(probe); } catch { }
                }
                catch (Exception ex)
                {
                    // 落盘（不弹系统窗 —— 硬规矩 ①）；控制台输出也要包，它自己也可能抛
                    dump(errPath, ex.ToString());
                    dump(outPath, "dlgprobe ERROR -> " + errPath + Environment.NewLine
                                  + ex.GetType().Name + ": " + ex.Message + Environment.NewLine);
                    try { Console.WriteLine("dlgprobe ERROR -> " + errPath); } catch { }
                    try { Console.WriteLine(ex.GetType().Name + ": " + ex.Message); } catch { }
                }
                return;
            }
            //   --webview2probe  ⑤：打印运行库版本 / 引导器在不在 / 默认浏览器（机械判据只认这三行 ASCII）
            if (args != null && args.Length > 0 && args[0] == "--webview2probe")
            {
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                Console.WriteLine(WebView2Guide.Probe(Path.GetDirectoryName(Application.ExecutablePath)));
                return;
            }
            if (args != null && args.Length > 0 && args[0] == "--shortcutprobe")
            {
                // 机械判据用：`--shortcutprobe [目录]` ⇒ 打印 shortcut=yes/no（不碰用户桌面）
                string sdir = args.Length > 1 ? args[1] : "";
                bool yes = sdir.Length > 0 ? Ui.ShortcutInDir(sdir) : Ui.ShortcutOnDesktop();
                Console.WriteLine("shortcut=" + (yes ? "yes" : "no"));
                return;
            }
            if (args != null && args.Length > 0 && args[0] == "--ask")
            {
                // 快捷方式询问窗（独立进程）：监控 3210 控制台，控制台关闭时自动关闭
                //   这个分支原来**无条件弹询问窗**，而 AskShortcut 那边的"已存在就跳过"只认两个写死的名字
                //   （安装器建的「一键启动 Persona Morph.lnk」它不认）⇒ 每次启动都再问一遍。
                //   现在统一按「桌面上有没有指向本 exe 的 .lnk」判断，有就一句话都不问。
                if (Ui.ShortcutOnDesktop())
                {
                    return; // 桌面已有指向本 exe 的快捷方式 ⇒ 一个字都不问
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new AskForm());
                return;
            }
            // 前置检测：控制台已在运行（3210 有服务）。
            // ⛔ 
            //   原来这里**一律只弹提示窗、绝不开窗**（理由是"保持唯一"）。但 3210 有服务意味着
            //   机器人**早就在跑**，它只在启动那一次开窗、此刻不会再开 ⇒ 结果是"谁都不开"，
            //   用户点了启动却什么都看不到。⇒ 改成：**没有控制台窗口就由启动器开**，
            //   只有确实已经开着一个「群相 控制台」窗口时才只提示、不重复开。
            try
            {
                using (var c = new System.Net.Sockets.TcpClient())
                {
                    c.Connect("127.0.0.1", PortHelper.ReadPort());
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                if (!Ui.ConsoleWindowAlive())
                {
                    string dir0 = Path.GetDirectoryName(Application.ExecutablePath);
                    string why0 = "";
                    string url0 = Ui.ConsoleUrl(dir0, out why0);
                    // ⛔ **只有真有人在听才开窗** —— 地址文件是"上一次"跑控制台时写下的，
                    //   机器人停掉之后它仍在；老写法无条件拿配置端口兜底，而那个端口多半也没人听
                    //   ⇒ 用户看到一屏 ERR_CONNECTION_REFUSED（＝网友报的「打不开控制台」）。
                    //   现在两个候选地址都**探活**，都不通就不开窗，让下面的正常启动流程把控制台拉起来。
                    if (url0 == null || !url0.StartsWith("http"))
                    {
                        string urlCfg = "http://127.0.0.1:" + PortHelper.ReadPort() + "/";
                        url0 = Ui.PortAlive(urlCfg) ? urlCfg : null;
                    }
                    if (url0 != null && url0.StartsWith("http")) Ui.OpenConsole(url0);
                    else Ui.NoteFallback(dir0, (why0 == "" ? "没有可用地址" : why0)
                        + "；两个候选地址都没人应答 ⇒ 这次**不开空窗**（避免一屏 ERR_CONNECTION_REFUSED），"
                        + "由下面的正常启动流程把控制台拉起来");
                }
                else
                {
                    Application.Run(new NoticeForm());
                }
                return;
            }
            catch
            {
                // 探测失败 = 控制台未在运行（正常首次启动），静默继续安装流程，不弹窗
            }
            bool created;
            Mutex m = new Mutex(true, "Global\\WxAgentLauncher", out created);
            if (!created)
            {
                // 隐形残留启动器（无窗口但占着互斥锁）→ 自动杀同路径旧进程后重试
                try
                {
                    var self = Process.GetCurrentProcess();
                    foreach (var pr in Process.GetProcesses())
                    {
                        try
                        {
                            if (pr.Id != self.Id && pr.MainModule != null &&
                                string.Equals(pr.MainModule.FileName, self.MainModule.FileName,
                                              StringComparison.OrdinalIgnoreCase))
                                pr.Kill();
                        }
                        catch { }
                    }
                    System.Threading.Thread.Sleep(400);
                    Mutex m2 = new Mutex(true, "Global\\WxAgentLauncher", out created);
                    if (created) { m = m2; }
                }
                catch { }
                if (!created)
                {
                    Application.EnableVisualStyles();
                    Application.SetCompatibleTextRenderingDefault(false);
                    Application.Run(new BusyForm());
                    return;
                }
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            // ── PySide6 首启自动装的「提前介入」告知 ──
            //   launcher.exe 有「旧版仍在跑」的窗口期，不能依赖它做安装）；这里只是
            //   在启动器开跑之前，把「首启要多下载约 100MB 界面组件」公告给用户
            //   （对齐「任何包换入都必须经过用户可见的公告与选择」）。
            //   检测不到（没有 runtime / 目录判断出错）就一言不发地跳过——拦路反而不是优化；
            //   走到这里说明控制台没在跑、互斥锁也拿到了 ⇒ 是一次真正的全新启动，才值得公告。
            string qtBootWhy = QtBootForm.MissingReason(Path.GetDirectoryName(Application.ExecutablePath));
            if (qtBootWhy != null)
            {
                Application.Run(new QtBootForm(qtBootWhy));
            }
            Application.Run(new LauncherForm());
        }
    }

    public class NoticeForm : Form
    {
        public NoticeForm()
        {
            string root = Path.GetDirectoryName(Application.ExecutablePath);
            Text = "群相 一键启动";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            BackColor = Color.FromArgb(246, 248, 252);
            // 440×254 → 520×338（卡片化 + 把"控制台在哪、为什么不再开一个"讲清楚）
            //   ⚠️ 这个高度是**标题栏以下**的设计高度（`Apply` 再 + BarH 给窗口长高，见 stylekit.cs）
            ClientSize = new Size(520, 338);
            try { string ico = Path.Combine(root, "assets", "app.ico"); if (File.Exists(ico)) Icon = Icon.ExtractAssociatedIcon(ico); } catch { }

            string _curl = ""; // 现成的控制台地址（含口令），只从权威来源取
            try
            {
                string _why = "";
                _curl = Ui.ConsoleUrl(root, out _why);
            }
            catch { }

            StyleKit.MakeIcon(this, root, new Point(StyleKit.Space.x6, StyleKit.Space.x5));

            Label t = new Label();
            t.Text = "控制台已经在运行";
            t.Font = StyleKit.Ui(StyleKit.TextScale.Title, FontStyle.Bold);
            t.ForeColor = StyleKit.Ink;
            t.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4, StyleKit.Space.x5 + 2);
            t.AutoSize = true;
            Controls.Add(t);

            Label s = new Label();
            s.Text = "无需重复打开";
            s.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            s.ForeColor = StyleKit.Ok;
            // ⚠️ （#18 叠字根治）：跟着标题实测底边走（原写死 +30，150% 下叠 12px）——BusyForm 同款
            s.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4,
                                   t.Bottom + StyleKit.Space.x1);
            s.AutoSize = true;
            Controls.Add(s);

            int cardW = 520 - StyleKit.Space.x6 * 2;
            // #12 F1 / #13 F3：同上（原手写卡顶 104 / 卡高 152，现由 `SealCard` 算）。
            CardPanel card = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, StyleKit.CardTopY), cardW);

            Label m = new Label();
            m.Text = "群相 控制台已经在运行了，所以这次不再重复打开一个窗口（重复打开会出现两个控制台，容易看乱）。";
            m.Font = StyleKit.Ui(StyleKit.TextScale.Body, FontStyle.Regular);
            m.ForeColor = StyleKit.Ink;
            m.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
            // ⛔ 原来是 `new Size(w, 44)` 手写 + FitLabel ⇒ 偏大时不被修正。
            //   这句话实测 2 行（~33）< 44 ⇒ 这一处是"多留白"而不是被裁，但同样不该手写高度。
            m.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(m);
            int mh = StyleKit.FitLabel(m);

            Label m2 = new Label();
            m2.Text = "想接着用它 —— 点右下角「打开控制台」，会在我们自己的窗口里打开（不再借浏览器）。";
            m2.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            m2.ForeColor = StyleKit.InkBody;
            // 跟着 m 的实测底边走（原来写死 +52，是按"m 手写 44"配出来的）
            m2.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4 + Math.Max(1, mh) + StyleKit.Space.x3);
            m2.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(m2);
            int m2h = StyleKit.FitLabel(m2);

            // 把地址显示出来（诊断：用户经常想知道"它到底在哪一个端口上"）
            Label m3 = new Label();
            m3.Text = string.IsNullOrEmpty(_curl)
                ? "地址：读不到（可能还没落到 logs\\console.url）"
                : ("地址：" + MaskToken(_curl));
            m3.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            // ⛔ （可达性）：Muted(150,158,172)=2.60:1 远低于 AA ⇒ 改 Ink3ok(5.0:1)。
            m3.ForeColor = StyleKit.Ink3ok;
            m3.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4 + Math.Max(1, mh) + StyleKit.Space.x3 + Math.Max(1, m2h) + StyleKit.Space.x3);
            m3.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(m3);
            StyleKit.FitLabel(m3);
            StyleKit.SealCard(card);

            // #13 F2：宽度由文字实宽决定（原来抄 148 / 100），y 跟着卡片实测底边走（原来抄 292）。
            Button ok = StyleKit.MakeButton("打开控制台");
            Button no = StyleKit.MakeButton("知道了");
            int ny = card.Bottom + StyleKit.CardGapY;
            ok.Location = new Point(520 - StyleKit.Space.x6 - ok.Width, ny);
            ok.FlatStyle = FlatStyle.Flat;
            ok.BackColor = Color.FromArgb(64, 140, 255);
            ok.ForeColor = Color.White;
            ok.Click += (ss, ee) =>
            {
                try
                {
                    string url = _curl;
                    if (!url.StartsWith("http"))
                        url = "http://127.0.0.1:" + PortHelper.ReadPort() + "/";
                    Ui.OpenConsole(url); // W6：自带标题栏的 WebView2 内嵌窗口（不可用时才回退浏览器，原因写 logs\console_open.log）
                }
                catch { }
                Close();
            };
            Controls.Add(ok);
            no.Location = new Point(520 - StyleKit.Space.x6 - ok.Width - StyleKit.Space.x2 - no.Width, ny);
            no.FlatStyle = FlatStyle.Flat;
            no.Click += (ss, ee) => Close();
            Controls.Add(no);
            // #12 F1：窗高由最后一块底边反推（原来抄 338）。
            ClientSize = new Size(520, ok.Bottom + StyleKit.Space.x6);
            AcceptButton = ok; CancelButton = no;
            StyleKit.Apply(this, "群相 已就绪");
        }

        /// 把地址里的 token 打码（地址要给人看，但口令不该整串摊在屏幕上）
        internal static string MaskToken(string url)
        {
            try
            {
                int i = url.IndexOf("token=", StringComparison.OrdinalIgnoreCase);
                if (i < 0) return url;
                int s = i + 6;
                int e = url.IndexOf('&', s);
                if (e < 0) e = url.Length;
                if (e - s <= 4) return url.Substring(0, s) + "****";
                return url.Substring(0, s) + url.Substring(s, 2) + "****" + url.Substring(e - 2);
            }
            catch { return url; }
        }
    }

    // ================= W6：统一外观（StyleKit）=================

    /// WebView2 内嵌控制台：自带标题栏（无边框 + 圆角 + 可拖动 + **可拉伸**），WebView2 不可用时回退到浏览器
    ///
    /// ⛔⛔⛔ **`ConsoleForm` 待删， 之后不再需要。**
    ///     控制台一旦是本地原生控件，就不再经过 WebView2 ⇒ 本窗体与 `webview2guide.cs` 整份一起作废。
    /// ⚠️ 落地前它**仍在生效**，但**任何"为了更好看/更好用"的新投入一律不做**，只允许修阻断性缺陷。
    /// （本窗体在 里承担的健康度判定 `HealHealth` 是另一回事 —— 那段逻辑 的 `heal.py` 会接手，
    ///       不是随窗体一起删，删窗前先确认 `HealHealth` / `ResolveLiveUrl` 已被 Qt 侧接管。）
    /// 后台健康度的判定 —— **移植** `_scratch/qt_proto/heal.py` 的
    /// `Health`（那个原型是真跑过的），这里不另发明状态机，只把六态搬成 C# 枚举。
    internal enum HealHealth
    {
        Ok = 0, // 连上了，后端在跑
        Starting = 1, // 连不上，但我们知道它正在起来（刚点过「拉起来」）
        Dead = 2, // 连不上，且没有进程在撑 ⇒ 可以自愈
        Hijacked = 3, // 连上了，但对面不是我们的服务（代理 / 端口被别的程序占了）
        Refused = 4, // 连不上，无从判断原因
        Unknown = 5,
    }

    public class ConsoleForm : Form
    {
        string _url;
        int _ring = 3; // 边缘缩放环宽（逻辑 px，OnLoad 里按窗口 DPI 换算）
                                       // ⇒ 6 → 3；最大化时内边距直接归零
        GlyphButton _btnMax; // 最大化/还原按钮（自绘字形，随状态换 kind）
        Panel _bar; // 自绘顶栏（真全屏时要收起来，见 SyncBarVisible）
        bool _barPeek; // 全屏时鼠标是否贴在屏幕顶端（贴顶才把顶栏滑出来）
        int _barLeaveAt; // 光标离开"顶栏区域"的时刻（Environment.TickCount；0＝没在倒计时）
        public bool ProbeOnly; // 取证探针用：不初始化 WebView2（免得探针把浏览器弹出来）
        /// 不激活显示：`Show()` 时用 SW_SHOWNOACTIVATE，**不抢用户前台**
        /// （取值探针 --cursorprobe 用；实测：只加 WS_EX_NOACTIVATE 还不够，WinForms 的 Show() 仍会激活）
        public bool NoActivate;
        protected override bool ShowWithoutActivation { get { return NoActivate; } }
        public string MaxGlyph { get { return _btnMax == null ? "" : _btnMax.Text; } }
        /// 取证探针（--cursorprobe）要直接在这个 WebView2 里跑 JS，故开放只读引用
        public Microsoft.Web.WebView2.WinForms.WebView2 ProbeView { get { return _wv; } }
        Microsoft.Web.WebView2.WinForms.WebView2 _wv;

        System.Windows.Forms.Timer _heal; // 周期性探活（服务死了要**自己**发现）
        bool _healBusy; // 探测跑在线程池里，跑完才放下一轮进来
        bool _showingDown; // 当前是不是我们自己那张"没起来"的页
        bool _navDown; // 最近一次导航的目标是自绘页（用来区分"导航成功"的两种含义）
        bool _downStarting; // 自绘页现在显示的是"正在起来"还是"没起来"
        string _healWhy = ""; // 探活失败的原因（进日志，也进自绘页）
        string _healFail = ""; // b：最近一次「拉起」的结论（超时 / 起不来），失败时必须给人看
        DateTime _startAt = DateTime.MinValue; // 最近一次「拉起来」的时刻（HealStartTimeoutSec 秒内按"正在起来"讲）
        /// b：「正在拉起」的**硬超时**（秒）。
        /// 过了这个点**必须**离开「正在拉起」态。原实现把 90 秒散着写了两处，而且出口只能靠探活那一轮
        /// 顺带带出来 —— 探活一旦卡在线程池里（`_healBusy` 留在 true），页面就**永久转圈**。
        /// ⇒ 收成一个常量，并且配一条**只认时间、不看探活**的独立看门狗，保证任何路径都有出口。
        internal const int HealStartTimeoutSec = 30;
        /// c：探活**周期**（毫秒）。
        /// 原来写 2000 —— 服务挂掉后窗口要**平均 1 秒、最坏 2 秒**才自己发现，实测体感是"死页停了 2~5 秒"，
        /// 用户明确要求更快。探活只是本机回环上的 `TcpClient.Connect` + 一次 HEAD，开销是**微秒级**，
        /// 800ms 一轮对 CPU 完全无感，却是人眼分不出"卡顿 / 立刻"的那条线 ⇒ 取 800。
        /// ⚠️ 提速只动**间隔**，不动 `Ui.ResolveLiveUrl` 里每个候选口的 300ms 单口超时：
        ///    那个值要扛冷启动和弱网，改小会把"起得慢"误判成"口死了"。
        internal const int HealProbeIntervalMs = 800;
        /// c：硬超时看门狗的**周期**（毫秒）。它只比较时刻、不碰网络，
        /// 1 秒一跳够了 —— 超时判据本身就是 30 秒量级，快那么几百毫秒没意义。
        /// 抽常量的理由：整片代码里不该再出现裸的定时器毫秒数字。
        internal const int HealWatchIntervalMs = 1000;
        System.Windows.Forms.Timer _healWatch; // 硬超时看门狗：不依赖探活线程，只读表

        /// 最大化 ↔ 还原（标题栏双击与「□」按钮走同一处）
        public void ToggleMax()
        {
            try
            {
                WindowState = (WindowState == FormWindowState.Maximized)
                    ? FormWindowState.Normal : FormWindowState.Maximized;
                ApplyChrome();
                GlyphButton gm = _btnMax as GlyphButton;
                if (gm != null) gm.Kind = (WindowState == FormWindowState.Maximized) ? GlyphKind.Restore : GlyphKind.Max;
            }
            catch { }
        }

        /// 全屏/还原时同步窗口内边距：
        /// 无边框窗体的 Maximized 本来就铺满整屏（含任务栏），把内边距归零画面就真顶到边。
        public void ApplyChrome()
        {
            try
            {
                // 只在"真的要变"时才赋值：Padding 变化会触发 OnResize，而 OnResize 又会调回来
                //（下面新增的那个）⇒ 不加这道判断就会无限递归。
                Padding want = (WindowState == FormWindowState.Maximized)
                    ? new Padding(0) : new Padding(_ring, 0, _ring, _ring);
                if (Padding != want) Padding = want;
                SyncBarVisible();
            }
            catch { }
        }

        /// 真全屏时把自绘顶栏收起来，让页面内容顶到屏幕最上边。
        /// 鼠标贴到屏幕顶端 4px 内再把顶栏滑出来 —— 否则全屏时就没有「还原」按钮可点了，
        /// 用户会被困在全屏里（ESC 也能退，见 EscFilter，但不能只靠它）。
        void SyncBarVisible()
        {
            if (_bar == null) return;
            bool full = (WindowState == FormWindowState.Maximized);
            bool show = !full || _barPeek;
            if (_bar.Visible != show)
            {
                _bar.Visible = show;
                if (_wv != null) _wv.Dock = DockStyle.Fill;
            }
        }

        /// ⛔ 
        ///   `ToggleMax()` 里虽然调了 `ApplyChrome()`，但那是**设完 WindowState 立刻读**——
        ///   最大化/还原是**异步**的（系统随后才发 WM_SIZE），那一刻读到的可能还是旧状态
        ///   ⇒ 全屏之后内边距仍留着那 3px 的环，看起来就是"全屏了还有一圈边框"。
        ///   ⇒ 在 OnResize 里再同步一次：这时候 WindowState 一定已经更新。
        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            ApplyChrome();
        }

        /// 给 ESC 过滤器用（`_btnMax` 是私有字段）
        public GlyphButton MaxButton { get { return _btnMax; } }

        /// ESC 退出全屏。
        /// 为什么用 `IMessageFilter` 而不是 KeyPreview/ProcessCmdKey：WebView2 是**原生子窗口**，
        /// 键盘消息直接投给它，WinForms 的 KeyPreview/ProcessCmdKey 收不到；消息过滤器挂在应用消息泵上，
        /// 能看见发给子窗的 WM_KEYDOWN，所以这条路才有效。
        internal class EscFilter : IMessageFilter
        {
            readonly ConsoleForm _f;
            public EscFilter(ConsoleForm f) { _f = f; }
            public bool PreFilterMessage(ref Message m)
            {
                try
                {
                    if (m.Msg == 0x0100 && (int)m.WParam == 0x1B && _f != null
                        && _f.WindowState == FormWindowState.Maximized)
                    {
                        _f.WindowState = FormWindowState.Normal;
                        _f.ApplyChrome();
                        GlyphButton gm = _f.MaxButton as GlyphButton;
                        if (gm != null) gm.Kind = GlyphKind.Max;
                        return true;
                    }
                }
                catch { }
                return false;
            }
        }
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        static extern uint GetDpiForWindow(IntPtr h);

        /// 无边框窗口的鼠标缩放：自绘 WM_NCHITTEST 回八向命中码（10..17），系统即给出缩放光标并接管拖拽。
        /// 前提＝窗体**自己留一圈内边距**（环内像素归父窗）——否则整块客户区被 WebView2 子窗盖住，
        /// 命中测试全被它吃掉，父窗的 WndProc 根本收不到 ⇒ 表现成"窗口拉伸不动"（另一台机器实测）。
        /// 顶部不留环：标题带要能拖（与 dsh启动器 FrmDshWindow 同口径）。
        int EdgeCode(int x, int y)
        {
            if (WindowState != FormWindowState.Normal) return 0;
            int w = ClientSize.Width, h = ClientSize.Height;
            bool L = x < _ring, R = x >= w - _ring, B = y >= h - _ring;
            if (!(L || R || B)) return 0;
            if (L && B) return 16;
            if (R && B) return 17;
            if (L) return 10;
            if (R) return 11;
            return 15;
        }

        protected override void WndProc(ref Message m)
        {
            // 最大化：无边框窗口必须自己处理这两条消息，否则最大化的窗会盖住任务栏
            // （照抄 dsh启动器 主窗/DSH 窗已验证的做法）
            if (m.Msg == 0x0024) // WM_GETMINMAXINFO
            {
                try
                {
                    MINMAXINFO mmi = (MINMAXINFO)System.Runtime.InteropServices.Marshal.PtrToStructure(m.LParam, typeof(MINMAXINFO));
                    Screen sc = Screen.FromHandle(Handle);
                    Rectangle mo = sc.Bounds; // 整个屏幕（不只工作区）
                    // ⛔ 
                    //   原来这里用的是 `WorkingArea`（不含任务栏）⇒ 最大化后窗口只到工作区。
                    //   ⇒ 这里必须用 `Bounds`，与下面 WM_NCCALCSIZE 的目标矩形保持一致。
                    mmi.ptMaxPosition.x = 0;
                    mmi.ptMaxPosition.y = 0;
                    mmi.ptMaxSize.x = mo.Width;
                    mmi.ptMaxSize.y = mo.Height;
                    System.Runtime.InteropServices.Marshal.StructureToPtr(mmi, m.LParam, false);
                }
                catch { }
                m.Result = IntPtr.Zero; return;
            }
            // 另一个进程（`--console-reuse`）把"现在该连的地址"递过来了 ⇒ 重新导航 + 抬起。
            //   治的就是 `agent/notify_ui.py` 里那条"复用分支只 flash/抬起、从不导航"的老路 ——
            //   它抬起来的还是那一屏 ERR_CONNECTION_REFUSED。
            if (m.Msg == WM_COPYDATA)
            {
                try { HealHandOff(m.LParam); m.Result = (IntPtr)1; } catch { }
                return;
            }
            if (m.Msg == 0x0083) // WM_NCCALCSIZE：把客户区**精确钉死到屏幕矩形**
            {
                // ⛔ 三轮才对（用户三次实测反馈）：
                //   ① 原版：最大化时把 rgrc0 **内缩** SM_CXSIZEFRAME(4)+SM_CXPADDEDBORDER(4)=8px
                // ⇒ 客户区比窗口小 8px ⇒ **四周露出一圈窗体底色**。
                // ② 我干脆不内缩 ⇒ 但系统会把最大化窗口**撑到屏幕之外**（顶层窗带
                //      WS_THICKFRAME 时系统按边框额度外扩）⇒ 客户区跟着超出屏幕 ⇒
                // **顶栏被切掉一截**。
                //   ③ 正解：两种"猜"都不要——**直接把客户区设成屏幕矩形本身**（屏幕坐标），
                //      于是"系统撑大了多少"根本不需要知道：客户区永远精确等于整块屏幕。
                try
                {
                    if (m.WParam != IntPtr.Zero && WindowState == FormWindowState.Maximized)
                    {
                        NCCALCSIZE_PARAMS nc = (NCCALCSIZE_PARAMS)System.Runtime.InteropServices.Marshal.PtrToStructure(m.LParam, typeof(NCCALCSIZE_PARAMS));
                        Rectangle mo = Screen.FromHandle(Handle).Bounds; // 与 WM_GETMINMAXINFO 同一目标
                        nc.rgrc0.left = mo.Left; nc.rgrc0.top = mo.Top;
                        nc.rgrc0.right = mo.Right; nc.rgrc0.bottom = mo.Bottom;
                        System.Runtime.InteropServices.Marshal.StructureToPtr(nc, m.LParam, false);
                    }
                }
                catch { }
                m.Result = IntPtr.Zero; return; // 没有非客户区
            }
            if (m.Msg == 0x00A3) { ToggleMax(); return; } // 标题栏双击＝最大化/还原
            if (m.Msg == 0x0084) // WM_NCHITTEST
            {
                try
                {
                    int lp = m.LParam.ToInt32();
                    Point cp = PointToClient(new Point((short)(lp & 0xFFFF), (short)((lp >> 16) & 0xFFFF)));
                    int code = EdgeCode(cp.X, cp.Y);
                    if (code != 0) { m.Result = (IntPtr)code; return; }
                }
                catch { }
            }
            base.WndProc(ref m);
        }

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        static extern int GetSystemMetrics(int idx);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern IntPtr GetForegroundWindow();
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern bool SetForegroundWindow(IntPtr h);

        internal const int GWL_EXSTYLE = -20;
        internal const int WS_EX_NOACTIVATE = 0x08000000;
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern int GetWindowLong(IntPtr h, int idx);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern int SetWindowLong(IntPtr h, int idx, int val);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        static extern bool IsZoomed(IntPtr h);

        // ---- 跨进程递地址（复用分支要"抬起 + 重新导航"，就得能跟那个已经开着的窗说话）----
        internal const int WM_COPYDATA = 0x004A;
        internal const int SW_RESTORE = 9;

        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        internal struct COPYDATASTRUCT
        {
            public IntPtr dwData;
            public int cbData;
            public IntPtr lpData;
        }

        [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
        internal static extern IntPtr FindWindow(string cls, string title);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern bool IsIconic(IntPtr h);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern bool ShowWindow(IntPtr h, int cmd);
        [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "SendMessage")]
        internal static extern IntPtr SendMessageCd(IntPtr h, int msg, IntPtr w, ref COPYDATASTRUCT l);

        /// 无边框窗最大化时系统补的那圈边框宽度（不补回来任务栏会被盖住）
        static int FramePx(bool horiz)
        {
            try { return GetSystemMetrics(horiz ? 32 : 33) + GetSystemMetrics(92); } catch { return 0; }
        }

        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        struct POINT { public int x, y; }
        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        struct RECTS { public int left, top, right, bottom; }
        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        struct MINMAXINFO
        {
            public POINT ptReserved, ptMaxSize, ptMaxPosition, ptMinTrackSize, ptMaxTrackSize;
        }
        [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
        struct NCCALCSIZE_PARAMS
        {
            public RECTS rgrc0, rgrc1, rgrc2;
            public IntPtr lppos;
        }

        // ── 控制台窗口几何持久化──
        //    真因：`OnLoad → ApplyGeometry()` 每次都按"工作区 + DPI"**现算尺寸**并 `CenterScreen`，
        //    没有任何持久化 ⇒ 用户调好的大小/位置，一「停止」一「打开」就被覆盖回默认。
        //    修法：关窗时把「普通态的位置与大小 + 是否最大化」落盘，下次开窗优先恢复；
        //    只有"没有存档 / 解析失败 / 那块矩形已不在任何屏幕工作区里（换显示器、改分辨率）"
        //    才回落到原来的现算 + 居中。**探针模式（ProbeOnly）不读存档** —— 带 `--shot`／几何
        //    探针的判据要的是"按工作区算出来的那个尺寸"，读了存档就不再确定。
        static string GeoFile()
        {
            try { return Path.Combine(Path.GetDirectoryName(Application.ExecutablePath), "data", "console_window.txt"); }
            catch { return null; }
        }

        /// 读存档；不可用（没有/坏了/已不在屏幕上）一律返回 false，由调用方回落现算值
        static bool LoadGeo(out Rectangle rect, out bool maxed)
        {
            rect = Rectangle.Empty; maxed = false;
            try
            {
                string p = GeoFile();
                if (p == null || !File.Exists(p)) return false;
                int x = 0, y = 0, w = 0, h = 0, mx = 0;
                foreach (string ln in File.ReadAllLines(p))
                {
                    int i = ln.IndexOf('=');
                    if (i <= 0) continue;
                    string kk = ln.Substring(0, i).Trim();
                    int vv;
                    if (!int.TryParse(ln.Substring(i + 1).Trim(), out vv)) continue;
                    if (kk == "x") x = vv; else if (kk == "y") y = vv;
                    else if (kk == "w") w = vv; else if (kk == "h") h = vv;
                    else if (kk == "max") mx = vv;
                }
                if (w < 200 || h < 150) return false;
                Rectangle r = new Rectangle(x, y, w, h);
                bool onScreen = false;
                foreach (Screen s in Screen.AllScreens)
                {
                    Rectangle it = Rectangle.Intersect(r, s.WorkingArea);
                    if (it.Width >= 200 && it.Height >= 150) { onScreen = true; break; }
                }
                if (!onScreen) return false;
                rect = r; maxed = (mx != 0);
                return true;
            }
            catch { return false; }
        }

        void SaveGeo()
        {
            try
            {
                string p = GeoFile();
                if (p == null) return;
                Directory.CreateDirectory(Path.GetDirectoryName(p));
                // 最大化时记的是**还原后的**位置与大小，这样"取消最大化"回到用户喜欢的那一版
                Rectangle b = (WindowState == FormWindowState.Normal) ? Bounds : RestoreBounds;
                if (b.Width < 200 || b.Height < 150) return;
                File.WriteAllText(p, string.Format("x={0}\ny={1}\nw={2}\nh={3}\nmax={4}\n",
                    b.X, b.Y, b.Width, b.Height,
                    WindowState == FormWindowState.Maximized ? 1 : 0));
            }
            catch { }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            SaveGeo();
            base.OnFormClosing(e);
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            ApplyGeometry();
        }

        /// 纯函数：按工作区与缩放算客户区尺寸/最小尺寸/缩放环宽（无副作用 ⇒ 判据可以精确断言）
        public static void ComputeGeometry(int waW, int waH, float k,
                                           out int w, out int h, out int minW, out int minH, out int ring)
        {
            if (k < 0.5f || k > 4f) k = 1f;
            ring = Math.Max(5, (int)Math.Round(6 * k));
            w = (int)Math.Min(waW * 0.92, 1320 * k);
            h = (int)Math.Min(waH * 0.92, 880 * k);
            w = Math.Max(w, (int)(980 * k));
            h = Math.Max(h, (int)(640 * k));
            minW = (int)(880 * k);
            minH = (int)(580 * k);
        }

        /// 初始尺寸按"工作区比例 + 窗口 DPI"现算（原来写死 1180×780 物理像素）：
        /// 125% 缩放的机器上那是 944×624 逻辑像素，比设计意图小一圈、内容自然挤在一起。
        public void ApplyGeometry()
        {
            try
            {
                float k = 1f;
                try { uint dpi = GetDpiForWindow(Handle); if (dpi >= 96) k = dpi / 96f; } catch { }
                if (k < 0.5f || k > 4f) k = 1f;
                Rectangle wa = Screen.FromPoint(Cursor.Position).WorkingArea;
                int w, h, mw, mh, r;
                ComputeGeometry(wa.Width, wa.Height, k, out w, out h, out mw, out mh, out r);
                _ring = r;
                ApplyChrome(); // 全屏态内边距为 0
                try { Application.AddMessageFilter(new EscFilter(this)); } catch { } // ESC 退出全屏
                ClientSize = new Size(w, h);
                MinimumSize = new Size(mw, mh);
                // 有存过的几何就优先用（探针模式跳过，保证几何判据的确定性）
                if (!ProbeOnly)
                {
                    Rectangle g; bool maxed;
                    if (LoadGeo(out g, out maxed))
                    {
                        StartPosition = FormStartPosition.Manual;
                        Bounds = g;
                        if (maxed) WindowState = FormWindowState.Maximized;
                    }
                }
            }
            catch { }
        }

        public ConsoleForm(string url)
        {
            _url = url;
            Text = "群相 控制台";
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(1180, 780); // OnLoad 里按 DPI/工作区重算
            // ⛔ （用户截图）：
            //   这里原来用 `StyleKit.Bg`（**浅色** 247,249,252）⇒ 非全屏时那圈 `Padding(_ring)`
            //   露出的就是浅色底，等于给深蓝控制台套了个白框；顶栏也是浅色，与页面割裂。
            //   `StyleKit.ConsoleBg`（15,23,35）本来就是为控制台准备的深底 ⇒ 改用它，环就隐进页面里。
            // 这个底色有两个用途，所以必须**与页面背景完全同色**：
            //   ① 顶栏（42px 自绘栏）的底；② 非全屏时那圈 `Padding(_ring)` 露出来的"拉伸环"。
            // 环是拉伸必需的（WebView2 会吃掉命中测试，必须留一圈给父窗），那就让它**看不出来**——
            // 页面深色主题的 `--bg` 是 #0E1420 = (14,20,32)，这里就照它取；差一点点（比如原来的
            // ConsoleBg=(15,23,35)）在深色渐变边上就会显出一条线。
            BackColor = Color.FromArgb(14, 20, 32);
            Panel bar = new Panel();
            bar.Height = 42; bar.Dock = DockStyle.Top; bar.BackColor = Color.FromArgb(14, 20, 32);
            bar.MouseDown += delegate { StyleKit.Drag(Handle); };
            Label t = new Label();
            t.Text = "群相 控制台";
            t.Font = StyleKit.Ui(10.5f, FontStyle.Bold);
            t.ForeColor = StyleKit.ConsoleInk; // 深底（ConsoleBg）上原来的 Ink/Sub 都太暗
            t.AutoSize = true; t.Location = new Point(14, 12);
            t.MouseDown += delegate { StyleKit.Drag(Handle); };
            bar.Controls.Add(t);
            // 顶栏三个按钮**自绘**：
            // 旧实现用 `—` / `□` / `✕` 三种字形，笔画与基线各不相同 ⇒ 一排看过去必然怪。见 wingliphs.cs。
            //
            // ⚠️ 
            //   原来高度写 26，而字形需要的 `need` 是 27（Min/Max/Close 分别为 48×27 / 52×27 / 60×27）
            //   ⇒ `h=26->26 CLIP`（差值 1px，但判据是"任一 CLIP 都不许有"）。
            //   修法：**高度 26 → 27**（跟其它窗体标题栏按钮对齐）。
            //   ⚠️ 不能只加宽度：宽度是 34 已经够（48/52/60 是**含内边距的量测值**，
            //      而按钮是 `Anchor=Top|Right` + 固定 34 宽 + 字形居中绘制 ⇒ 宽度不参与截断判断，
            //      真正差的是**高度**那一格）。
            //   尺寸账：`BarH = 38`，Location.Y = 8 ⇒ 8 + 27 = 35 ≤ 38，仍在标题栏内（不会顶出去）。
            //   高度值统一走 `StyleKit.TitleBarBtnH`（与 `BuildTitleBar` 那颗 RoundButton 同源）。
            const int GlyphH = StyleKit.TitleBarBtnH;
            GlyphButton min = new GlyphButton();
            //   但"尺寸不许手写"这条一样成立 ⇒ 宽走 `StyleKit.TitleBarBtnW`、高走 `StyleKit.TitleBarBtnH`。
            min.Kind = GlyphKind.Min; min.Size = new Size(StyleKit.TitleBarBtnW, GlyphH);
            min.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            min.Location = new Point(bar.Width - 122, 8);
            min.Click += delegate { WindowState = FormWindowState.Minimized; };
            bar.Controls.Add(min);
            // 最大化 / 还原
            GlyphButton maxb = new GlyphButton();
            maxb.Kind = GlyphKind.Max; maxb.Size = new Size(StyleKit.TitleBarBtnW, GlyphH);
            maxb.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            maxb.Location = new Point(bar.Width - 82, 8);
            maxb.Click += delegate { ToggleMax(); };
            _btnMax = maxb;
            bar.Controls.Add(maxb);
            GlyphButton cls = new GlyphButton();
            cls.Kind = GlyphKind.Close; cls.Size = new Size(StyleKit.TitleBarBtnW, GlyphH);
            cls.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            cls.Location = new Point(bar.Width - 42, 8);
            cls.Click += delegate { Close(); };
            bar.Controls.Add(cls);
            // 图标随状态换（最大化时显示"还原"框）
            Resize += delegate
            {
                try { if (_btnMax != null) _btnMax.Kind = (WindowState == FormWindowState.Maximized) ? GlyphKind.Restore : GlyphKind.Max; }
                catch { }
            };
            _wv = new Microsoft.Web.WebView2.WinForms.WebView2();
            _wv.Dock = DockStyle.Fill;
            try
            {
                var cp = new Microsoft.Web.WebView2.WinForms.CoreWebView2CreationProperties();
                cp.UserDataFolder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PersonaMorph", "WebView2");
                _wv.CreationProperties = cp;
            }
            catch { }
            _wv.CoreWebView2InitializationCompleted += delegate(object s, Microsoft.Web.WebView2.Core.CoreWebView2InitializationCompletedEventArgs e)
            {
                if (!e.IsSuccess) { Ui.NoteFallback(Path.GetDirectoryName(Application.ExecutablePath), "WebView2 初始化失败（多半是系统没装 WebView2 运行时）"); Ui.FallbackBrowser(_url); Close(); return; }
                // ⛔ 
                //   页面在"用户点停止 + 后端已关"之后会调 `window.close()`，但 WebView2 默认**忽略**它，
                //   必须宿主自己响应 `WindowCloseRequested` 才真的关窗（否则用户点完停止，窗口一直杵在那）。
                //   注意这不等于"网页能随便关窗"——只有我们自己的页面在自己进程里能触发它。
                try { _wv.CoreWebView2.WindowCloseRequested += delegate { try { Close(); } catch { } }; } catch { }
                // ⛔ （用户实测「ESC退出不了全屏」）：
                //   `EscFilter`（IMessageFilter）在 WebView2 是**原生子窗口**时收不到按键 ⇒ 全屏按 ESC 没反应，
                //   而全屏又收起了自绘顶栏 ⇒ **用户会被困在全屏里**。页面自己一定能收到 keydown，
                //   所以由页面 postMessage 上来，宿主这边还原窗口。
                try
                {
                    _wv.CoreWebView2.WebMessageReceived += delegate(object s2, Microsoft.Web.WebView2.Core.CoreWebView2WebMessageReceivedEventArgs e2)
                    {
                        try
                        {
                            string msg = e2.TryGetWebMessageAsString();
                            // 页面请求关窗（用户点「确认停止」之后）——
                            // 用户实测「其他都行了，只有点停止关窗不行」⇒ 单靠页面 `window.close()`
                            // 在这台机器上没触发宿主关窗，而这条 postMessage 通道已被 ESC 验证可用。
                            if (msg == "pm-close-window") { try { Close(); } catch { } return; }
                            // ⛔ 我们自己那张"控制台正在启动"的重试页要求再试一次
                            //   （页面 postMessage 比 JS 直接 location.replace 稳：宿主这边一定能拿到）。
                            if (msg == "pm-retry") { try { _wv.CoreWebView2.Navigate(_url); } catch { } return; }
                            // 自绘那张页的两个动作 —— 立刻再探一次 / 现在就把后台拉起来
                            if (msg == "pm-heal") { HealTick(); return; }
                            if (msg == "pm-heal-start") { StartBackend(); return; }
                            if (msg == "pm-esc-exit-fullscreen" && WindowState == FormWindowState.Maximized)
                            {
                                WindowState = FormWindowState.Normal;
                                ApplyChrome();
                                GlyphButton gm = _btnMax as GlyphButton;
                                if (gm != null) gm.Kind = GlyphKind.Max;
                            }
                        }
                        catch { }
                    };
                }
                catch { }
                // ⛔ （**B站网友报「打不开控制台 / 无法访问此页面」的那个入口**）：
                //   机器人没在跑/还没就绪时，这里的 `Navigate(_url)` 会**成功返回**、随后把
                //   WebView2 的默认错误页（一屏 ERR_CONNECTION_REFUSED）显示给用户 —— 用户当然以为
                //   产品坏了。⇒ 导航失败一律换成**我们自己的**页。
                // ⛔ 那张页现在带「现在就拉起来」，并且**换口也能接上**
                //   （老版只会死盯着最初那个 `_url` 重试，端口一顺延就永远连不上）。
                try
                {
                    _wv.CoreWebView2.NavigationCompleted += delegate(object s3, Microsoft.Web.WebView2.Core.CoreWebView2NavigationCompletedEventArgs e3)
                    {
                        // 成功有两种含义：连上了真页面 / 刚把自绘页写进去 —— 用 `_navDown` 分开。
                        if (e3.IsSuccess) { _showingDown = _navDown; return; }
                        if (_navDown) return; // 已经是自绘页，别自己追自己
                        GoDown(StartingWindowOpen);
                    };
                }
                catch { }
                // ⛔ **开窗前自己探活，不信任 `logs\console.url`** —— 服务关了那个文件还在
                //   （里面是没人听的口），端口被占时后台又会静默顺延。探到活口就导航过去，
                //   一个活口都没有就直接上我们自己的页，绝不把死地址丢给 WebView2。
                try
                {
                    if (HealEnabled) { StartHeal(); HealTick(); }
                    else { _wv.CoreWebView2.Navigate(_url); }
                }
                catch { Ui.FallbackBrowser(_url); }
            };
            Controls.Add(bar);
            Controls.Add(_wv);
            bar.BringToFront();
            _bar = bar;
            Shown += delegate
            {
                if (ProbeOnly) return; // 探针模式：只量几何，不起 WebView2
                try { _wv.EnsureCoreWebView2Async(null); }
                catch (Exception ex) { Ui.NoteFallback(Path.GetDirectoryName(Application.ExecutablePath), "EnsureCoreWebView2Async 抛异常：" + ex.Message); Ui.FallbackBrowser(_url); Close(); }
            };
            StyleKit.Apply(this, "群相 控制台");
            // 全屏时"鼠标贴顶端才滑出顶栏"：WebView2 是**原生子窗口**、会吃掉鼠标消息，
            // 父窗收不到 MouseMove ⇒ 只能用定时器读全局光标位置（**只读，不动鼠标**）。
            // ⛔ 判据从"只有顶端 4px"改成 `BarPeek`（进入/保持/宽限三段，见那个类的注释）
            // —— 上边栏碰触之后维持的时间太短，导致点不到最小化」就是原来那一条判据
            //   造成的：鼠标一往按钮上挪就离开 4px ⇒ 顶栏立刻收起。离线仿真见 `--peekprobe`。
            try
            {
                System.Windows.Forms.Timer peek = new System.Windows.Forms.Timer();
                peek.Interval = BarPeek.TickMs;
                peek.Tick += delegate
                {
                    try
                    {
                        if (WindowState != FormWindowState.Maximized)
                        {
                            if (_barPeek) { _barPeek = false; _barLeaveAt = 0; SyncBarVisible(); }
                            return;
                        }
                        Rectangle mo = Screen.FromHandle(Handle).Bounds;
                        Point c = Cursor.Position;
                        bool inX = (c.X >= mo.Left) && (c.X < mo.Right);
                        bool hitEdge = inX && (c.Y <= mo.Top + BarPeek.EdgeBand);
                        // "还在顶栏那块区域内"＝顶端 + 顶栏高 + 余量（这样从顶端往下挪到按钮上不会中途收起）
                        bool overBar = inX && (c.Y <= mo.Top + (_bar != null ? _bar.Height : 42) + BarPeek.KeepSlack);
                        bool want = BarPeek.Next(_barPeek, hitEdge, overBar, Environment.TickCount, ref _barLeaveAt);
                        if (want != _barPeek) { _barPeek = want; SyncBarVisible(); }
                    }
                    catch { }
                };
                peek.Start();
            }
            catch { }
            // ⛔ 本窗原来**没有设 Icon** ⇒ 任务栏/Alt+Tab 用的是进程默认图标。
            //   统一成 `assets\app.ico`（与其它弹出窗一致）。放在 `StyleKit.Apply` **之后**，
            //   免得被它的主题化逻辑覆盖。
            try
            {
                string ico0 = Path.Combine(Path.GetDirectoryName(Application.ExecutablePath), "assets", "app.ico");
                if (File.Exists(ico0)) Icon = Icon.ExtractAssociatedIcon(ico0);
            }
            catch { }
        }

        // =====================================================================
        // 探活 / 自愈（治"窗口不知道服务死活"这件事）
        // =====================================================================

        /// 只有**真连后台**的窗口才探活。`--shot` / 各类探针建的是 `about:blank` 的窗，
        /// 它们绝不能起定时器（否则取证跑一次就多出一堆无谓的连接）。
        bool HealEnabled
        {
            get { return !ProbeOnly && !string.IsNullOrEmpty(_url) && _url.StartsWith("http"); }
        }

        public void StartHeal()
        {
            if (!HealEnabled) return;
            try
            {
                _heal = new System.Windows.Forms.Timer();
                _heal.Interval = HealProbeIntervalMs; // 服务死了 ⇒ 最多 HealProbeIntervalMs 毫秒内自己发现
                _heal.Tick += delegate { HealTick(); };
                _heal.Start();
            }
            catch { }
            StartHealWatch();
            // 窗口被抬起来/激活时**立刻**探一次：用户点「打开控制台」后不该先看到一屏死页再等一整轮。
            Activated += delegate { HealTick(); };
        }

        void HealTick()
        {
            if (!HealEnabled || _healBusy) return;
            _healBusy = true;
            string cur = _url;
            bool starting = StartingWindowOpen;
            ThreadPool.QueueUserWorkItem(delegate
            {
                int health; string why;
                string live = Ui.ResolveLiveUrl(cur, out health, out why);
                try { BeginInvoke((Action)delegate { HealApply(live, health, why, starting); }); }
                catch { _healBusy = false; }
            });
        }

        void HealApply(string live, int health, string why, bool starting)
        {
            _healBusy = false;
            try
            {
                if (IsDisposed || _wv == null || _wv.CoreWebView2 == null) return;
                if (!string.IsNullOrEmpty(live))
                {
                    // 起来了 —— **可能在别的口上**。判据是"探活成功"，不是文件里写的那个端口号。
                    if (_showingDown || !string.Equals(live, _url, StringComparison.Ordinal)) GoLive(live);
                    return;
                }
                _healWhy = why;
                // 没起来 ⇒ 我们自己的页（不是 WebView2 那张 ERR_CONNECTION_REFUSED）。
                if (!_showingDown || starting != _downStarting) GoDown(starting);
            }
            catch { }
        }

        /// 导航到活地址，并记下"现在不是自绘页"。
        void GoLive(string u)
        {
            _url = u; _navDown = false; _showingDown = false;
            _startAt = DateTime.MinValue; _healFail = ""; // 已经接上 ⇒ 拉起这件事到此结束（含失败结论）
            try { _wv.CoreWebView2.Navigate(u); } catch { }
        }

        /// 切到我们自绘的"没起来 / 正在起来"页，并记下"现在是自绘页"。
        void GoDown(bool starting)
        {
            _navDown = true; _showingDown = true; _downStarting = starting;
            try { _wv.CoreWebView2.NavigateToString(Ui.DownPage(_url, _healWhy, starting, _healFail)); } catch { }
        }

        // ---- b：「正在拉起」的硬超时与失败出口 ----

        /// 「正在拉起」这个说法**还成立**吗（点了拉起 + 没过硬超时）。
        /// 只有这一个判据，别再散着写秒数。
        bool StartingWindowOpen
        {
            get { return _startAt != DateTime.MinValue && (DateTime.Now - _startAt).TotalSeconds < HealStartTimeoutSec; }
        }

        /// 硬超时看门狗：**1 秒一跳，只认时间**。
        /// 为什么不挂在探活那一轮上：探活要联网（连不上时会耗掉整轮），而且 `_healBusy` 一旦留在 true，
        /// `HealTick` 就再也不会进入 —— 那正是"永久停在正在拉起"的成因。看门狗不碰网络，只比较时刻。
        void StartHealWatch()
        {
            try
            {
                if (_healWatch != null) return;
                _healWatch = new System.Windows.Forms.Timer();
                _healWatch.Interval = HealWatchIntervalMs;
                _healWatch.Tick += delegate { HealWatchTick(); };
                _healWatch.Start();
            }
            catch { }
        }

        void HealWatchTick()
        {
            try
            {
                if (_startAt == DateTime.MinValue) return; // 没点过拉起 ⇒ 不关它的事
                if (StartingWindowOpen) return; // 还在硬超时窗口内 ⇒ 继续等
                if (!_downStarting) return; // 已经离开「正在拉起」了 ⇒ 收工
                HealFail("已尝试 " + HealStartTimeoutSec + " 秒，控制台服务仍没探到（" + Ui.ShortUrl(_url) + " 及候选口都没应答）"
                    + HealLogTail(), Path.GetDirectoryName(Application.ExecutablePath));
            }
            catch { }
        }

        /// 失败时的**可诊断信息**：把 `logs\console_heal.log` 的最后几行带上，别让人对着空白猜。
        string HealLogTail()
        {
            string tail = Ui.LastHealLog(Path.GetDirectoryName(Application.ExecutablePath), 5);
            return string.IsNullOrEmpty(tail) ? "" : ("；最后几条自愈日志：" + tail);
        }

        /// 「拉起」的**统一失败出口**：记日志、清掉起始时刻、把自绘页换成「没起来 + 原因」。
        /// ⛔ 任何一条走不通的路都必须调它 —— 否则页面（或页面里那颗按钮）就停在"正在拉起…"上出不来。
        void HealFail(string reason, string root)
        {
            try
            {
                _startAt = DateTime.MinValue; // 关掉"正在拉起"这个说法 ⇒ 看门狗不会再重复打
                _healFail = reason; // 进自绘页，用户看得见
                _healBusy = false; // 探活万一卡住（标志留在 true 就再也不探了），这里放它出来
                Ui.NoteHeal(root, reason);
                if (IsDisposed || _wv == null || _wv.CoreWebView2 == null) return;
                GoDown(false); // ⛔ 必须是 false：超时后就**不许**再显示"正在拉起"
            }
            catch { }
        }

        /// 跨进程递来的新地址：**先探活再导航**（治"抬起来的还是死页"）。
        void HealHandOff(IntPtr lParam)
        {
            COPYDATASTRUCT cd = (COPYDATASTRUCT)
                System.Runtime.InteropServices.Marshal.PtrToStructure(lParam, typeof(COPYDATASTRUCT));
            if (cd.cbData <= 0 || cd.lpData == IntPtr.Zero) return;
            string u = System.Runtime.InteropServices.Marshal.PtrToStringUni(cd.lpData, cd.cbData / 2);
            if (string.IsNullOrEmpty(u)) return;
            u = u.Trim('\0');
            if (!u.StartsWith("http")) return;
            _url = u; // 口令跟着这个地址走，换口不掉口令
            RaiseSelf();
            HealTick();
        }

        void RaiseSelf()
        {
            try
            {
                if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
                Show(); Activate(); SetForegroundWindow(Handle);
            }
            catch { }
        }

        /// 「现在就拉起来」：走**现有启动流程**（`python -X utf8 scripts\onestart.py`），
        /// 与「一键启动.exe」用的是同一份 Python 解析、同一个脚本 —— 不另起一套。
        /// ⛔ 不等它退出：`onestart.py` 会一直守到控制台就绪（可能一两分钟）。
        void StartBackend()
        {
            string root = Path.GetDirectoryName(Application.ExecutablePath);
            try
            {
                string exe = "", pre = "", ver = "", why = "";
                bool got = LauncherForm.TryPy(Path.Combine(root, "runtime", "python", "python.exe"), out exe, out pre, out ver, out why);
                if (!got) got = LauncherForm.TryPy(LauncherForm.ReadPyRaw(Path.Combine(root, "logs", "python_path.txt")), out exe, out pre, out ver, out why);
                // ⛔ b：这两条"根本没 spawn 出去"的失败**原来只写日志、页面不动** ⇒ 页面里那颗按钮
                //   已经被 JS 置成 disabled + "正在拉起…"，永远回不来（又一处"进去出不来"）。
                //   ⇒ 走 HealFail：写日志 + 把原因贴到自绘页 + 重新画出可点的按钮。
                if (!got) { HealFail("拉起失败：找不到可用的 Python（" + why + "）", root); return; }
                string onestart = Path.Combine(root, "scripts", "onestart.py");
                if (!File.Exists(onestart)) { HealFail("拉起失败：缺 scripts\\onestart.py", root); return; }
                var po = new ProcessStartInfo(exe, ((pre.Length > 0 ? (pre + " ") : "") + "-X utf8 \"" + onestart + "\"").Trim());
                po.UseShellExecute = false;
                po.CreateNoWindow = true;
                po.WorkingDirectory = root;
                po.EnvironmentVariables["WX_GUI"] = "1";
                Process.Start(po);
                _startAt = DateTime.Now;
                _healFail = ""; // 上一次的失败结论到此为止，别跟着新一轮显示
                StartHealWatch(); // 兜底：探活定时器没起来时，硬超时仍然有人管
                Ui.NoteHeal(root, "已拉起后台：" + exe + " " + po.Arguments);
                GoDown(true); // 立刻换成"正在起来"那张页，别让用户对着旧按钮
                HealTick();
            }
            catch (Exception ex) { HealFail("拉起异常：" + ex.Message, root); }
        }
    }

    internal static class Ui
    {
        /// 指定目录里有没有指向本 exe 的快捷方式（判据可指向临时目录，不碰用户桌面）。
        public static bool ShortcutInDir(string dir)
        {
            try
            {
                if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) return false;
                string exe = Path.Combine(Path.GetDirectoryName(Application.ExecutablePath), "一键启动.exe");
                foreach (string f in Directory.GetFiles(dir, "*.lnk"))
                {
                    try
                    {
                        Type t = Type.GetTypeFromProgID("WScript.Shell");
                        dynamic ws = Activator.CreateInstance(t);
                        dynamic sc = ws.CreateShortcut(f);
                        string tp = (string)sc.TargetPath;
                        if (!string.IsNullOrEmpty(tp) && string.Equals(tp, exe, StringComparison.OrdinalIgnoreCase))
                            return true;
                    }
                    catch { }
                }
            }
            catch { }
            return false;
        }

        /// 桌面上（含公共桌面）是否已经有指向本 exe 的快捷方式 —— 判定按**目标路径**，不按名字。
        public static bool ShortcutOnDesktop()
        {
            try
            {
                if (ShortcutInDir(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory))) return true;
                if (ShortcutInDir(Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory))) return true;
            }
            catch { }
            return false;
        }

        /// 是否**已经开着一个**「群相 控制台」窗口（按主窗口标题判，零依赖、零打扰）。
        /// 用途：3210 有服务时决定"由启动器开窗"还是"只提示不重复开"。
        public static bool ConsoleWindowAlive()
        {
            try
            {
                foreach (var pr in Process.GetProcesses())
                {
                    try { if (pr.MainWindowTitle == "群相 控制台") return true; } catch { }
                }
            }
            catch { }
            return false;
        }

        /// 打开控制台：优先用我们自己的 WebView2 内嵌窗口；DLL 缺失/初始化失败则回退系统浏览器。
        /// 每次回退都往 `logs\console_open.log` 记一行**原因**——"点打开控制台却弹出浏览器"这类
        /// 现场只有那台机器有，没这行日志就只能靠猜。
        public static void OpenConsole(string url)
        {
            string dir = Path.GetDirectoryName(Application.ExecutablePath);
            try
            {
                bool has = File.Exists(Path.Combine(dir, "lib", "Microsoft.Web.WebView2.WinForms.dll"))
                        || File.Exists(Path.Combine(dir, "Microsoft.Web.WebView2.WinForms.dll"));
                if (!has) { NoteFallback(dir, "缺 lib\\Microsoft.Web.WebView2.WinForms.dll"); FallbackBrowser(url); return; }
                // ⑤：**运行库**不在时不要硬起窗口（初始化必失败、再兜底浏览器，用户看不懂为什么）。
                // 先给自绘面板：一键装（随机带的官方引导器）｜用浏览器打开（仅当本机真有浏览器）｜复制网址；
                // 一个都做不了时**如实说清"这次看不了控制台，但机器人在后台照常跑"**，并写日志留现场。
                if (!WebView2Guide.HasRuntime())
                {
                    using (WebView2MissingForm f = new WebView2MissingForm(dir, url))
                    {
                        f.ShowDialog();
                        if (f.Action == "install")
                        {
                            string why;
                            bool done = WebView2Guide.Install(dir, out why);
                            NoteFallback(dir, "缺 WebView2 运行库 ⇒ 一键安装：" + why);
                            if (done) { Application.Run(new ConsoleForm(url)); }
                            return;
                        }
                        if (f.Action == "browser")
                        {
                            NoteFallback(dir, "缺 WebView2 运行库 ⇒ 用户选了用浏览器打开");
                            FallbackBrowser(url);
                            return;
                        }
                        if (f.Action == "copy")
                        {
                            try { Clipboard.SetText(url); } catch { }
                            NoteFallback(dir, "缺 WebView2 运行库 ⇒ 用户复制了网址（本机看不了控制台）");
                            return;
                        }
                        NoteFallback(dir, "缺 WebView2 运行库 ⇒ 用户点了知道了（未打开控制台）");
                        return;
                    }
                }
                Application.Run(new ConsoleForm(url));
            }
            catch (Exception ex) { NoteFallback(dir, "自家窗口启动异常：" + ex.Message); FallbackBrowser(url); }
        }

        /// 取证探针：**我们自己的 WebView2 窗口**里的光标链路（鲸鱼光标 + 点击歪头帧）。
        ///
        /// 为什么必须在这扇窗里量：浏览器里好不代表自家窗里好——WebView2 的宿主是 WinForms 控件，
        /// 光标由"网页请求 → WebView2 → 宿主"这条链传下去，任何一环没接上，用户看到的就只是普通箭头。
        /// 做法：把控制台窗口开到**屏幕外**、`WS_EX_NOACTIVATE` 显示（不抢前台、不出现在屏幕上），
        /// 等页面就绪后在页面里真跑一遍：读 cursor 计算值 → 派发 mousedown → 读歪头帧 → 等 400ms 读回默认帧
        /// → 同步 XHR 确认两张图都能取到。全部结果打成 ASCII 标记给判据脚本解析。
        public static string CursorProbe(string url)
        {
            var sb = new StringBuilder();
            string dir = Path.GetDirectoryName(Application.ExecutablePath);
            bool hasDll = File.Exists(Path.Combine(dir, "lib", "Microsoft.Web.WebView2.WinForms.dll"))
                       || File.Exists(Path.Combine(dir, "Microsoft.Web.WebView2.WinForms.dll"));
            sb.AppendLine("dll_present=" + hasDll);
            if (!hasDll) { sb.AppendLine("cursorprobe=skip_no_dll"); return sb.ToString(); }
            IntPtr fg0 = ConsoleForm.GetForegroundWindow();
            ConsoleForm f = null;
            try
            {
                f = new ConsoleForm(url);
                f.ProbeOnly = false;
                f.NoActivate = true; // Show() 不激活（否则会把用户前台抢走）
                f.ShowInTaskbar = false;
                f.StartPosition = FormStartPosition.Manual;
                f.Location = new Point(-4000, -4000); // 屏外：看得见才怪，但它照样渲染/执行
                // 屏外 + WS_EX_NOACTIVATE：Show() **不激活**，用户的前台窗口不会被抢走
                // 
                try
                {
                    IntPtr h0 = f.Handle;
                    ConsoleForm.SetWindowLong(h0, ConsoleForm.GWL_EXSTYLE, ConsoleForm.GetWindowLong(h0, ConsoleForm.GWL_EXSTYLE) | ConsoleForm.WS_EX_NOACTIVATE);
                }
                catch { }
                var wv = f.ProbeView;
                f.Show();
                // 等 WebView2 就绪（ConsoleForm 的 Shown 里已经在 Initialize）
                var sw = System.Diagnostics.Stopwatch.StartNew();
                while (wv.CoreWebView2 == null && sw.ElapsedMilliseconds < 25000)
                { Application.DoEvents(); System.Threading.Thread.Sleep(60); }
                sb.AppendLine("webview_ready=" + (wv.CoreWebView2 != null));
                if (wv.CoreWebView2 == null) { sb.AppendLine("cursorprobe=fail_no_webview"); return sb.ToString(); }
                // 等页面就绪（readyState + 光标脚本已跑）
                string ready = "";
                sw.Restart();
                while (sw.ElapsedMilliseconds < 25000)
                {
                    ready = RunJs(wv, "(function(){try{return document.readyState+'|'+(document.getElementById('whaleCursorStyle')?'style':'nostyle')}catch(e){return 'err'}})()");
                    if (ready.IndexOf("complete|style") >= 0) break;
                    Application.DoEvents(); System.Threading.Thread.Sleep(120);
                }
                sb.AppendLine("page=" + ready.Replace("\"", "").Replace("|", " "));
                // 资源可达（同步 XHR，同源）
                sb.AppendLine(Stat(wv, "/assets/cursor.png"));
                sb.AppendLine(Stat(wv, "/assets/cursor-nod.png"));
                // 光标样式现状
                string s1 = Js(wv, "(function(){try{var st=document.getElementById('whaleCursorStyle');return (st?st.textContent:'')}catch(e){return 'err'}})()");
                sb.AppendLine("style_default=" + Brief(s1));
                sb.AppendLine("has_cursor_png=" + (s1.IndexOf("cursor.png") >= 0));
                sb.AppendLine("has_whale_class=" + (Js(wv, "document.documentElement.className").IndexOf("whale-cursor") >= 0));
                // 点一下：应换成歪头帧
                string s2 = Js(wv, "(function(){try{document.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));var st=document.getElementById('whaleCursorStyle');return (st?st.textContent:'')}catch(e){return 'err'}})()");
                sb.AppendLine("style_after_mousedown=" + Brief(s2));
                sb.AppendLine("nod_applied=" + (s2.IndexOf("cursor-nod.png") >= 0));
                // 180ms 后应换回默认帧
                System.Threading.Thread.Sleep(500);
                string s3 = Js(wv, "(function(){try{var st=document.getElementById('whaleCursorStyle');return (st?st.textContent:'')}catch(e){return 'err'}})()");
                sb.AppendLine("style_after_400ms=" + Brief(s3));
                sb.AppendLine("nod_reverted=" + (s3.IndexOf("cursor-nod.png") < 0 && s3.IndexOf("cursor.png") >= 0));
                // 中键（滚轮键）特效：鲸鱼原地转一圈 360°（12 帧 canvas 预转的 dataURL 光标）
                // 先等帧备好（帧是 img.onload 里异步造的：图没 load 完就按会退回"点头"，判据会假红）
                sw.Restart();
                while (sw.ElapsedMilliseconds < 6000)
                {
                    string rdy = Js(wv, "(function(){try{return String(WHALE_CURSOR.framesReady())}catch(e){return 'err'}})()");
                    if (rdy.IndexOf("true") >= 0) break;
                    Application.DoEvents(); System.Threading.Thread.Sleep(100);
                }
                sb.AppendLine("spin_frames_ready=" + (Js(wv, "(function(){try{return String(WHALE_CURSOR.framesReady())}catch(e){return 'err'}})()").IndexOf("true") >= 0));
                string s4 = Js(wv, "(function(){try{document.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,button:1}));var st=document.getElementById('whaleCursorStyle');return (st?st.textContent:'')}catch(e){return 'err'}})()");
                sb.AppendLine("style_after_middle=" + Brief(s4));
                sb.AppendLine("spin_applied=" + (s4.IndexOf("data:image/png") >= 0));
                System.Threading.Thread.Sleep(900); // 12 × 44ms ≈ 530ms ⇒ 900ms 后必须回到默认帧
                string s5 = Js(wv, "(function(){try{var st=document.getElementById('whaleCursorStyle');return (st?st.textContent:'')}catch(e){return 'err'}})()");
                sb.AppendLine("style_after_spin=" + Brief(s5));
                sb.AppendLine("spin_reverted=" + (s5.IndexOf("data:image/png") < 0 && s5.IndexOf("cursor.png") >= 0));
                // 自研「滚轮模式」：中键按下后必须**真的在滚**（均匀往下）+ 那只鱼的徽标在转 + 左键退出。
                // 先把上一步 spin 那次 button:1 可能带起的滚轮模式按掉（否则这里的第一次中键会变成"退出"）
                Js(wv, "(function(){try{document.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,button:0}));return 'ok'}catch(e){return 'err'}})()");
                System.Threading.Thread.Sleep(200);
                Js(wv, "(function(){try{document.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,button:1,clientX:520,clientY:420}));return 'ok'}catch(e){return 'err'}})()");
                sb.AppendLine("wheel_t0=" + Js(wv, "(function(){try{return String(PM_WHEEL.top())}catch(e){return 'err'}})()").Replace("\"", ""));
                System.Threading.Thread.Sleep(700);
                sb.AppendLine("wheel_1=" + Js(wv, "(function(){try{return PM_WHEEL.active()+'|'+PM_WHEEL.top()+'|'+PM_WHEEL.hasPuck()+'|'+WHALE_CURSOR.frameIdx()+'|'+(document.querySelector('#pmWheel img')?'yes':'no')}catch(e){return 'err'}})()").Replace("\"", ""));
                System.Threading.Thread.Sleep(300);
                sb.AppendLine("wheel_2=" + Js(wv, "(function(){try{return PM_WHEEL.active()+'|'+PM_WHEEL.top()+'|'+PM_WHEEL.hasPuck()+'|'+WHALE_CURSOR.frameIdx()+'|'+(document.querySelector('#pmWheel img')?'yes':'no')}catch(e){return 'err'}})()").Replace("\"", ""));
                Js(wv, "(function(){try{document.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,button:0,clientX:520,clientY:420}));return 'ok'}catch(e){return 'err'}})()");
                System.Threading.Thread.Sleep(250);
                sb.AppendLine("wheel_3=" + Js(wv, "(function(){try{return PM_WHEEL.active()+'|'+PM_WHEEL.top()+'|'+PM_WHEEL.hasPuck()}catch(e){return 'err'}})()").Replace("\"", ""));
                System.Threading.Thread.Sleep(350);
                sb.AppendLine("wheel_4=" + Js(wv, "(function(){try{return PM_WHEEL.active()+'|'+PM_WHEEL.top()+'|'+PM_WHEEL.hasPuck()}catch(e){return 'err'}})()").Replace("\"", ""));
                // ① 左导航跟着指示条走
                //    注意：探针窗里整页常常不可滚（数据是空的、页面短），所以**不靠 window 滚动**来造场景：
                //    直接把左栏滚到底（激活项被滚出去）+ 抛一个 scroll 事件触发 sync()，看左栏会不会自己跟回来。
                Js(wv, "(function(){try{var n=document.getElementById('nav');n.scrollTop=n.scrollHeight;window.dispatchEvent(new Event('scroll'));return 'ok'}catch(e){return 'err'}})()");
                System.Threading.Thread.Sleep(1000);
                sb.AppendLine("nav_1=" + Js(wv, "(function(){try{var n=document.getElementById('nav');var a=n?n.querySelector('a.on'):null;if(!n||!a)return 'noactive';var t=a.offsetTop,h=a.offsetHeight,st=n.scrollTop,vh=n.clientHeight,mx=n.scrollHeight-vh;return (t>=st+1&&t+h<=st+vh-1?'visible':'hidden')+'|scrollTop='+Math.round(st)+'|item='+t+'|h='+h+'|vh='+vh+'|max='+Math.round(mx)}catch(e){return 'err'}})()").Replace("\"", ""));
                // ② 鱼的转速跟着滚动速度（同一套锚点下：基础速度取样 → 鼠标挪到锚点下方很远（更快）再取样）
                Js(wv, "(function(){try{document.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,button:1,clientX:520,clientY:300}));return 'ok'}catch(e){return 'err'}})()");
                System.Threading.Thread.Sleep(250);
                sb.AppendLine("spinp_a1=" + Js(wv, "(function(){try{return PM_WHEEL.active()+'|'+WHALE_CURSOR.frameIdx()+'|'+PM_WHEEL.spinDeg()}catch(e){return 'err'}})()").Replace("\"", ""));
                System.Threading.Thread.Sleep(300);
                sb.AppendLine("spinp_a2=" + Js(wv, "(function(){try{return PM_WHEEL.active()+'|'+WHALE_CURSOR.frameIdx()+'|'+PM_WHEEL.spinDeg()}catch(e){return 'err'}})()").Replace("\"", ""));
                Js(wv, "(function(){try{document.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,cancelable:true,clientX:520,clientY:950}));return 'ok'}catch(e){return 'err'}})()");
                System.Threading.Thread.Sleep(250);
                sb.AppendLine("spinp_b1=" + Js(wv, "(function(){try{return PM_WHEEL.active()+'|'+WHALE_CURSOR.frameIdx()+'|'+PM_WHEEL.spinDeg()}catch(e){return 'err'}})()").Replace("\"", ""));
                System.Threading.Thread.Sleep(300);
                sb.AppendLine("spinp_b2=" + Js(wv, "(function(){try{return PM_WHEEL.active()+'|'+WHALE_CURSOR.frameIdx()+'|'+PM_WHEEL.spinDeg()}catch(e){return 'err'}})()").Replace("\"", ""));
                Js(wv, "(function(){try{document.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,button:0}));return 'ok'}catch(e){return 'err'}})()");
                IntPtr fg1 = ConsoleForm.GetForegroundWindow();
                sb.AppendLine("fg_before=" + fg0.ToInt64());
                sb.AppendLine("fg_after=" + fg1.ToInt64());
                sb.AppendLine("probe_hwnd=" + f.Handle.ToInt64());
                sb.AppendLine("fg_is_probe=" + (fg1 == f.Handle ? "True" : "False"));
                sb.AppendLine("foreground_unchanged=" + (fg1 == fg0 ? "True" : "False"));
                // 实测：**WebView2 初始化那一瞬间会把激活抢过来**（WS_EX_NOACTIVATE + ShowWithoutActivation
                // 都挡不住它内部的 SetFocus）⇒ 探针收尾**把前台还给原来的窗口**，不留给用户一个被换掉的前台。
                // 这是"用完还回去"的老规矩（同光标还原），并且如实把"抢过"记进 fg_is_probe。
                try { ConsoleForm.SetForegroundWindow(fg0); System.Threading.Thread.Sleep(120); } catch { }
                IntPtr fg2 = ConsoleForm.GetForegroundWindow();
                sb.AppendLine("fg_restored=" + (fg2 == fg0 ? "True" : "False"));
                sb.AppendLine("cursorprobe=ok");
            }
            catch (Exception ex) { sb.AppendLine("cursorprobe=error:" + ex.Message); }
            finally
            {
                try { if (f != null) { f.Close(); f.Dispose(); } } catch { }
            }
            return sb.ToString();
        }

        /// 跑一段 JS 并等结果（WebView2 的 ExecuteScriptAsync 是异步的，这里用消息泵同步等）
        static string RunJs(Microsoft.Web.WebView2.WinForms.WebView2 wv, string js)
        {
            string res = null; bool done = false;
            try
            {
                wv.CoreWebView2.ExecuteScriptAsync(js).ContinueWith(delegate(System.Threading.Tasks.Task<string> t)
                {
                    try { res = t.Result; } catch { res = null; }
                    done = true;
                });
            }
            catch { return ""; }
            var sw = System.Diagnostics.Stopwatch.StartNew();
            while (!done && sw.ElapsedMilliseconds < 12000) { Application.DoEvents(); System.Threading.Thread.Sleep(20); }
            return res == null ? "" : res.Trim('"');
        }

        static string Js(Microsoft.Web.WebView2.WinForms.WebView2 wv, string body) { return RunJs(wv, body); }

        static string Brief(string s)
        {
            s = (s ?? "").Replace("\r", " ").Replace("\n", " ");
            return s.Length > 150 ? s.Substring(0, 150) + "…" : s;
        }

        /// 同步 XHR 取一个资源，输出 `asset:<path>=<status>`
        static string Stat(Microsoft.Web.WebView2.WinForms.WebView2 wv, string path)
        {
            string r = RunJs(wv, "(function(){try{var x=new XMLHttpRequest();x.open('GET','" + path + "?probe=1',false);x.send();return 'asset:" + path + "='+x.status}catch(e){return 'asset:" + path + "=ERR'}})()");
            return string.IsNullOrEmpty(r) ? ("asset:" + path + "=TIMEOUT") : r;
        }

        /// 取证探针：控制台窗口的几何/缩放命中码（**全程不 Show、不初始化 WebView2** ⇒ 屏幕上不出现任何窗口）
        public static string WinProbe()
        {
            var sb = new StringBuilder();
            // ① 纯函数几何：不依赖本机工作区 ⇒ 判据可精确断言
            sb.AppendLine("== 几何（工作区 1920×1040，按缩放算客户区）==");
            foreach (float k in new float[] { 1f, 1.25f, 1.5f, 2f })
            {
                int w, h, mw, mh, r;
                ConsoleForm.ComputeGeometry(1920, 1040, k, out w, out h, out mw, out mh, out r);
                sb.AppendLine("geom k=" + k.ToString("0.##") + " client=" + w + "x" + h
                              + " min=" + mw + "x" + mh + " ring=" + r);
            }
            // ② 活窗口（只 CreateControl + ApplyGeometry，不 Show）
            try
            {
                ConsoleForm f = new ConsoleForm("about:blank");
                f.StartPosition = FormStartPosition.Manual;
                f.Location = new Point(240, 160);
                f.CreateControl();
                f.ApplyGeometry();
                sb.AppendLine("== 活窗口（未 Show）==");
                sb.AppendLine("live client=" + f.ClientSize.Width + "x" + f.ClientSize.Height
                              + " pad=" + f.Padding.Left + "," + f.Padding.Top + "," + f.Padding.Right + "," + f.Padding.Bottom
                              + " min=" + f.MinimumSize.Width + "x" + f.MinimumSize.Height
                              + " border=" + f.FormBorderStyle);
                foreach (Control c in f.Controls)
                    sb.AppendLine("  ctrl " + c.GetType().Name + " bounds=" + c.Bounds + " dock=" + c.Dock);
                int W = f.ClientSize.Width, H = f.ClientSize.Height;
                int[][] pts = new int[][] {
                    new int[] { 2, 2 }, new int[] { W - 2, 2 }, new int[] { 2, H - 2 }, new int[] { W - 2, H - 2 },
                    new int[] { 2, H / 2 }, new int[] { W - 2, H / 2 }, new int[] { W / 2, H - 2 },
                    new int[] { W / 2, 5 }, new int[] { W / 2, H / 2 }
                };
                foreach (int[] p in pts)
                {
                    Point sp = f.PointToScreen(new Point(p[0], p[1]));
                    sb.AppendLine("  hit(" + p[0] + "," + p[1] + ")=" + HitTest(f.Handle, sp.X, sp.Y));
                }
                f.Dispose();
            }
            catch (Exception ex) { sb.AppendLine("live 探针失败：" + ex.Message); }
            // ③ 最大化：真开一次窗（屏外 + 不激活，前台不变），量"最大化后是否正好等于工作区"——
            //    无边框窗不做 WM_GETMINMAXINFO/WM_NCCALCSIZE 的话会连任务栏一起盖住。
            try
            {
                ConsoleForm g = new ConsoleForm("about:blank");
                g.ProbeOnly = true;
                string shot = StyleKit.CaptureOffscreen(g, System.IO.Path.Combine(System.IO.Path.GetTempPath(), "qm-console-maxprobe.png"));
                Rectangle wa2 = Screen.FromHandle(g.Handle).WorkingArea;
                g.ToggleMax();
                for (int i = 0; i < 10; i++) { Application.DoEvents(); System.Threading.Thread.Sleep(30); }
                sb.AppendLine("max state=" + g.WindowState + " bounds=" + g.Bounds + " workarea=" + wa2
                              + " 正好等于工作区=" + (g.Bounds == wa2) + " 图标=" + g.MaxGlyph
                              + " eq_workarea=" + (g.Bounds == wa2 ? "True" : "False"));
                g.ToggleMax();
                for (int i = 0; i < 8; i++) { Application.DoEvents(); System.Threading.Thread.Sleep(20); }
                sb.AppendLine("max 还原后 state=" + g.WindowState + " 图标=" + g.MaxGlyph
                              + " restored_state=" + g.WindowState);
                g.Close(); g.Dispose();
                sb.AppendLine("maxprobe 截图=" + shot);
            }
            catch (Exception ex) { sb.AppendLine("maxprobe 失败：" + ex.Message); }
            return sb.ToString();
        }

        /// 取证探针：控制台地址取法（口令只打印掩码与前 3 位 + 长度，判据拿长度与 config 对账）
        public static string UrlProbe()
        {
            var sb = new StringBuilder();
            string root = Path.GetDirectoryName(Application.ExecutablePath);
            string why = "";
            string u = ConsoleUrl(root, out why);
            if (string.IsNullOrEmpty(u)) u = ""; // 死链时 ConsoleUrl 会回 null，探针不许崩
            string tok = "";
            int qi = u.IndexOf("token=");
            if (qi >= 0) tok = u.Substring(qi + 6);
            sb.AppendLine("console_url_file=" + (File.Exists(Path.Combine(root, "logs", "console.url")) ? "1" : "0"));
            sb.AppendLine("url=" + (tok.Length > 0 ? (u.Replace(tok, tok.Substring(0, Math.Min(3, tok.Length)) + "***")) : u));
            sb.AppendLine("token_len=" + tok.Length);
            sb.AppendLine("token_head=" + (tok.Length > 0 ? tok.Substring(0, Math.Min(3, tok.Length)) : ""));
            sb.AppendLine("fallback_reason=" + why);
            // ⛔ **ASCII 分类标记** —— 中文经 OEM 代码页重定向会变乱码，判据没法断言；
            //   与 `--winprobe` 的 `eq_workarea=True`/`restored_state=Normal` 同一套做法。
            string kind = "";
            if (why.Contains("死链") || why.Contains("没人应答")) kind = "dead_link";
            else if (why.Contains("没有 logs")) kind = "no_file";
            else if (why.Contains("不是地址")) kind = "not_url";
            else if (why.Contains("失败")) kind = "read_fail";
            sb.AppendLine("fallback_kind=" + kind);
            return sb.ToString();
        }

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);

        /// 向窗体真发一次 WM_NCHITTEST（lParam＝屏幕坐标），拿系统会用的命中码
        static int HitTest(IntPtr h, int x, int y)
        {
            try { return (int)SendMessage(h, 0x0084, IntPtr.Zero, (IntPtr)((y << 16) | (x & 0xFFFF))); }
            catch { return -999; }
        }

        /// ⛔ **"控制台还没起来"要用我们自己的页说**（显示层自研 + 不许给用户一屏 Edge 错误）。
        ///
        /// 现场（B站网友截图）：地址是 `127.0.0.1` 但机器人没在跑/还在起 ⇒ WebView2 直接显示
        /// `ERR_CONNECTION_REFUSED` 的浏览器错误页，用户只看到"无法访问此页面"，以为产品坏了。
        /// ⇒ 导航失败时换成这张自绘页：**自动重试**（端口一起来就接上）+ 手动重试 + 如实说明去哪看日志。
        public static string EscapeHtml(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;")
                    .Replace("\"", "&quot;").Replace("'", "&#39;");
        }

        // =====================================================================
        //
        // 要治的是"**窗口不知道服务死活**"这件事：让窗口自己探活、自己重连、自己把服务拉起来，
        // 并且**不再信任那个可能是死的地址文件**。判定逻辑是 `_scratch/qt_proto/heal.py`
        // 那个真跑过的 Python 原型的移植（五/六态），不另发明。
        // =====================================================================

        /// **我们自己的**"后台没起来"页 —— 顶掉 WebView2 那张 ERR_CONNECTION_REFUSED。
        ///
        /// 为什么必须自绘：那张浏览器错误页信息量 = 0（分不清"服务死了 / 端口被占 / 代理拦了"），
        /// 用户看到只能刷新，而刷不刷得好纯靠运气。这里换成能讲人话 + 能给动作的页。
        public static string DownPage(string url, string why, bool starting, string fail)
        {
            string shown = EscapeHtml(ShortUrl(url));
            var sb = new StringBuilder();
            sb.Append("<!doctype html><html><head><meta charset=\"utf-8\"><title>群相 控制台</title><style>");
            sb.Append("html,body{margin:0;height:100%;background:#0E1420;color:#E7ECF5;");
            sb.Append("font-family:'Microsoft YaHei UI','Microsoft YaHei',sans-serif}");
            sb.Append(".w{max-width:560px;margin:0 auto;padding:96px 28px 0}");
            sb.Append("h1{font-size:20px;font-weight:600;margin:0 0 14px}");
            sb.Append("p{font-size:13.5px;line-height:1.9;color:#9FB0C9;margin:0 0 10px}");
            sb.Append("code{font-size:12.5px;color:#7FB0FF;background:#141C2B;padding:2px 6px;border-radius:4px}");
            sb.Append("b{color:#E7ECF5;font-weight:600}");
            sb.Append("button{margin-top:14px;background:#2E6BE6;color:#fff;border:0;border-radius:8px;");
            sb.Append("padding:9px 18px;font-size:13px;cursor:pointer}");
            sb.Append("button:disabled{background:#2A3446;color:#7A8798;cursor:default}");
            sb.Append("</style></head><body><div class=\"w\">");
            if (starting)
            {
                // ②"重启窗口期那 2~5 秒真空" ⇒ **不许**把它讲成失败（老实现正是把这几秒
                //   当成失败，于是永久停在一张红字错误页上）。
                sb.Append("<h1>后台正在起来…</h1>");
                sb.Append("<p>刚点了拉起，正在准备 Python 环境、装依赖并连微信，<b>第一次可能要一两分钟</b>。</p>");
                sb.Append("<p>这个窗口会<b>自己接上</b>，不用刷新，也不用管它。</p>");
                // b：把"最多等多久"讲在前面 —— 超时的那一刻页面一定会变，不会一直转圈。
                sb.Append("<p>这里最多等 <b>" + ConsoleForm.HealStartTimeoutSec + " 秒</b>；"
                    + "到点还没探到服务，我就把<b>原因</b>和<b>日志最后几行</b>写在这，并把按钮还给你。</p>");
            }
            else
            {
                sb.Append("<h1>控制台还没连上</h1>");
                sb.Append("<p>本机上没找到在跑的控制台服务 —— 这不是页面坏了，是<b>后台没起来</b>。</p>");
                sb.Append("<p>点下面这个按钮，我按「一键启动」的同一套流程把它拉起来；起来之后这个窗口<b>自动接上</b>，不用你刷新。</p>");
                sb.Append("<button id=\"b\" onclick=\"go()\">现在就拉起来</button>");
            }
            sb.Append("<p>刚才试的是 <code>" + shown + "</code> —— 探下来没有活着的控制台服务。</p>");
            sb.Append("<p>想看细节就翻 <code>logs\\console_heal.log</code>（本次探测）、");
            sb.Append("<code>logs\\persona_morph.log</code> 与 <code>logs\\onestart.log</code>（机器人那边的日志）。</p>");
            if (!string.IsNullOrEmpty(why))
                sb.Append("<p>这次探到的情况：" + EscapeHtml(why) + "</p>");
            // b：失败结论（超时 / spawn 不出去）**必须可见** —— 空白或永久转圈都不算数。
            if (!string.IsNullOrEmpty(fail))
                sb.Append("<p>上次拉起的结果：" + EscapeHtml(fail).Replace("\n", "<br>") + "</p>");
            sb.Append("</div><script>");
            sb.Append("function post(m){try{if(window.chrome&&chrome.webview){chrome.webview.postMessage(m);return true;}}catch(e){}return false;}");
            sb.Append("function go(){var b=document.getElementById('b');if(b){b.disabled=true;b.textContent='正在拉起…';}post('pm-heal-start');}");
            sb.Append("post('pm-heal');"); // 页面一进来就让宿主立刻再探一次（不用干等定时器）
            sb.Append("</script></body></html>");
            return sb.ToString();
        }

        /// 自愈日志（与"回退浏览器"那条分开记，别混在一个文件里看）。
        public static void NoteHeal(string root, string what)
        {
            try
            {
                string d = Path.Combine(root, "logs");
                Directory.CreateDirectory(d);
                File.AppendAllText(Path.Combine(d, "console_heal.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + what + Environment.NewLine);
            }
            catch { }
        }

        /// `logs\console_heal.log` 的最后 n 行（给失败页用：让人不用自己去翻文件）。
        /// 读不出来就返回空 —— 这条只是"更好看清"，读不到不该影响页面本身。
        public static string LastHealLog(string root, int lines)
        {
            try
            {
                if (string.IsNullOrEmpty(root) || lines <= 0) return "";
                string f = Path.Combine(root, "logs", "console_heal.log");
                if (!File.Exists(f)) return "";
                string[] all = File.ReadAllLines(f);
                int take = Math.Min(lines, all.Length);
                var sb = new StringBuilder();
                for (int i = all.Length - take; i < all.Length; i++)
                {
                    string t = all[i];
                    if (string.IsNullOrEmpty(t)) continue;
                    if (sb.Length > 0) sb.Append(" ｜ ");
                    sb.Append(t.Trim());
                }
                return sb.ToString();
            }
            catch { return ""; }
        }

        public static string ShortUrl(string url)
        {
            if (string.IsNullOrEmpty(url)) return "";
            int q = url.IndexOf('?');
            return q > 0 ? url.Substring(0, q) : url;
        }

        /// 地址里的端口号（取不到给 0）。
        public static int PortOf(string url)
        {
            try
            {
                if (string.IsNullOrEmpty(url) || !url.StartsWith("http")) return 0;
                string rest = url.Substring(url.IndexOf("://") + 3);
                int slash = rest.IndexOf('/');
                if (slash >= 0) rest = rest.Substring(0, slash);
                int colon = rest.LastIndexOf(':');
                if (colon <= 0) return 0;
                int v = 0;
                if (int.TryParse(rest.Substring(colon + 1), out v) && v > 0) return v;
            }
            catch { }
            return 0;
        }

        /// 地址里的口令（拿不到给空串）。
        public static string TokenOf(string url)
        {
            try
            {
                if (string.IsNullOrEmpty(url)) return "";
                int q = url.IndexOf('?');
                if (q <= 0) return "";
                foreach (string kv in url.Substring(q + 1).Split('&'))
                {
                    int e = kv.IndexOf('=');
                    if (e > 0 && kv.Substring(0, e) == "token") return kv.Substring(e + 1);
                }
            }
            catch { }
            return "";
        }

        /// 用指定的口重拼一个地址，**口令跟着走**（换口不能把口令换丢，否则 401）。
        public static string WithPort(string url, int port)
        {
            string root = Path.GetDirectoryName(Application.ExecutablePath);
            string tok = TokenOf(url);
            if (tok == "")
            {
                // 传进来的地址没带口令 ⇒ 去地址文件里找（那是**口令的拥有者**写下来的）
                try
                {
                    string uf = Path.Combine(root, "logs", "console.url");
                    if (File.Exists(uf)) tok = TokenOf(File.ReadAllText(uf));
                }
                catch { }
            }
            if (tok == "")
            {
                try { string w; tok = TokenOf(ConsoleUrl(root, out w)); } catch { }
            }
            return "http://127.0.0.1:" + port + "/" + (tok != "" ? ("?token=" + tok) : "");
        }

        /// 该试哪些口（按可信度排序、去重）。
        ///
        /// ① 当前地址里的口（调用方刚给的）② `logs\console.url` 里的口（"上一次真跑起来的那一个"）
        /// ③ `config.json` 的 `server.port` ④ 顺延的几个口（端口被占时后台会**静默往后挪**）。
        public static int[] CandidatePorts(string url)
        {
            var set = new System.Collections.Generic.List<int>();
            string root = Path.GetDirectoryName(Application.ExecutablePath);
            int p0 = PortOf(url);
            if (p0 > 0) set.Add(p0);
            int pf = PortFromUrlFile(root);
            if (pf > 0 && !set.Contains(pf)) set.Add(pf);
            int baseP = ServerPortFromConfig(root);
            if (baseP <= 0) baseP = PortHelper.ReadPort();
            if (baseP > 0 && !set.Contains(baseP)) set.Add(baseP);
            for (int k = 1; k <= 8; k++)
            {
                int q = baseP + k;
                if (q > 0 && q < 65536 && !set.Contains(q)) set.Add(q);
            }
            return set.ToArray();
        }

        /// 打一次 HTTP（**必须绕代理**，见 `Identify` 的说明）。拿不到 HTTP 应答就给 false。
        static bool HttpProbe(int port, string path, out int code, out string raw, out string body)
        {
            code = 0; raw = ""; body = "";
            System.Net.HttpWebResponse rs = null;
            try
            {
                var rq = (System.Net.HttpWebRequest)System.Net.WebRequest.Create("http://127.0.0.1:" + port + path);
                rq.Proxy = null;
                rq.Timeout = 800;
                rq.ReadWriteTimeout = 800;
                rq.AllowAutoRedirect = false;
                rq.Method = "GET";
                rq.UserAgent = "PersonaMorph-Heal";
                rs = (System.Net.HttpWebResponse)rq.GetResponse();
                code = (int)rs.StatusCode; raw = "HTTP " + code;
                body = ReadBody(rs);
                return true;
            }
            catch (System.Net.WebException we)
            {
                System.Net.HttpWebResponse er = we.Response as System.Net.HttpWebResponse;
                if (er != null) { code = (int)er.StatusCode; raw = "HTTP " + code; body = ReadBody(er); return true; }
                raw = we.Status.ToString(); return false;
            }
            catch (Exception e) { raw = e.Message; return false; }
            finally { try { if (rs != null) rs.Close(); } catch { } }
        }

        /// 只读一小段正文（够认人就行，全页几百 KB 没必要读完）。
        static string ReadBody(System.Net.HttpWebResponse rs)
        {
            try
            {
                using (System.IO.Stream st = rs.GetResponseStream())
                {
                    if (st == null) return "";
                    byte[] buf = new byte[8192];
                    int got = st.Read(buf, 0, buf.Length);
                    if (got <= 0) return "";
                    return Encoding.UTF8.GetString(buf, 0, got);
                }
            }
            catch { return ""; }
        }

        /// 这段正文**像不像**我们的控制台页（`agent/console_html.py` 里带「群相」字样与 `<title>群相 控制台</title>`）。
        /// 判不了（空正文）就放行 —— 宁可错认，不可错杀：把真在跑的服务判成"不是"会直接害死正常路径。
        static bool LooksLikeOurs(string body)
        {
            if (string.IsNullOrEmpty(body)) return true;
            return body.IndexOf("群相") >= 0
                || body.IndexOf("PersonaMorph", StringComparison.OrdinalIgnoreCase) >= 0
                || body.IndexOf("persona_morph", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        /// 这个口上**是不是我们的控制台**（两步：先 TCP 快探，再 HTTP 认人）。
        ///
        /// ⛔ 必须两步：E4（端口顺延）现场里 3210 被别的程序占着 —— 只做 TCP 的话它也"活着"，
        ///    我们就会连到一个不是我们的东西上。HTTP 这一步负责认人。
        /// ⛔ 必须绕代理（`Proxy = null`）：本机代理把 127.0.0.1 也代理走时，应答会是 502，
        ///    不绕开就会把"代理劫持"误判成"服务挂了"（`_scratch/qt_proto/heal.py` 已实测过这个坑）。
        public static HealHealth Identify(int port, out string raw)
        {
            raw = "";
            if (port <= 0 || port >= 65536) return HealHealth.Refused;
            if (!PortAlive("http://127.0.0.1:" + port + "/", 300)) { raw = "没人接这个口"; return HealHealth.Refused; }
            int code; string r1, b1;
            if (!HttpProbe(port, "/api/status", out code, out r1, out b1))
            {
                raw = r1;
                // 有东西在听，但不是 HTTP —— 多半是别的程序占了这个口（**不是**我们）。
                return HealHealth.Unknown;
            }
            if (code == 502 || code == 503 || code == 504) { raw = r1; return HealHealth.Hijacked; }
            if (code != 404) { raw = r1; return HealHealth.Ok; } // 200 / 401 / 403 …… 都是"我们在应答"
            // `/api/status` 不存在 ⇒ 再问一次首页，并且**认正文**：别把别的 HTTP 服务当成我们
            // （E4 现场里占着 3210 的若是另一个 HTTP 服务，光看状态码是分不出来的）。
            int c2; string r2, b2;
            if (!HttpProbe(port, "/", out c2, out r2, out b2)) { raw = r2; return HealHealth.Unknown; }
            if (c2 == 502 || c2 == 503 || c2 == 504) { raw = r2; return HealHealth.Hijacked; }
            raw = r2;
            if (c2 != 200 && c2 != 401 && c2 != 403) return HealHealth.Unknown;
            return LooksLikeOurs(b2) ? HealHealth.Ok : HealHealth.Unknown;
        }

        /// 探一圈，返回**现在真能用的**控制台地址；一个活口都没有就返回 null。
        ///
        /// ⛔ 这是 的核心：**以"探活成功"为准，不以 `logs\console.url` 里的端口号为准**。
        ///   服务关了那个文件还在（里面是没人听的口），端口被占时后台又会静默顺延 ——
        ///   两种情况都只有"自己探"才测得出来。
        public static string ResolveLiveUrl(string url, out int health, out string why)
        {
            health = (int)HealHealth.Refused;
            why = "";
            try
            {
                foreach (int p in CandidatePorts(url))
                {
                    string raw;
                    HealHealth s = Identify(p, out raw);
                    if (s == HealHealth.Ok) { health = (int)HealHealth.Ok; return WithPort(url, p); }
                    if (s == HealHealth.Hijacked) why += p + " 上有别的东西在应答（" + raw + "）；";
                    else if (s == HealHealth.Unknown) why += p + " 上不像我们的服务（" + raw + "）；";
                }
            }
            catch { }
            return null;
        }

        /// **把地址递给已经开着的那个控制台窗**，让它重新导航 + 抬起。
        /// 找到窗并递成功 ⇒ 返回 true（调用方随即退出，不攒第二个窗）。
        ///
        /// 治的是入口 C：复用分支原来只 flash/抬起、**从不导航**，于是抬起来的还是那一屏死页。
        public static bool HandOffToConsole(string url)
        {
            try
            {
                if (string.IsNullOrEmpty(url) || !url.StartsWith("http")) return false;
                IntPtr h = ConsoleForm.FindWindow(null, "群相 控制台");
                if (h == IntPtr.Zero) return false;
                byte[] bytes = Encoding.Unicode.GetBytes(url);
                ConsoleForm.COPYDATASTRUCT cd = new ConsoleForm.COPYDATASTRUCT();
                cd.dwData = (IntPtr)0x504D; // 'PM'
                cd.cbData = bytes.Length + 2;
                IntPtr buf = System.Runtime.InteropServices.Marshal.AllocHGlobal(bytes.Length + 2);
                try
                {
                    System.Runtime.InteropServices.Marshal.Copy(bytes, 0, buf, bytes.Length);
                    System.Runtime.InteropServices.Marshal.WriteInt16(buf, bytes.Length, 0); // 结尾补 \0
                    cd.lpData = buf;
                    ConsoleForm.SendMessageCd(h, ConsoleForm.WM_COPYDATA, IntPtr.Zero, ref cd);
                }
                finally { System.Runtime.InteropServices.Marshal.FreeHGlobal(buf); }
                try { if (ConsoleForm.IsIconic(h)) ConsoleForm.ShowWindow(h, ConsoleForm.SW_RESTORE); } catch { }
                try { ConsoleForm.SetForegroundWindow(h); } catch { }
                return true;
            }
            catch { return false; }
        }

        /// 回退原因落盘（尽力而为，失败不影响开窗）
        public static void NoteFallback(string root, string why)
        {
            try
            {
                string d = Path.Combine(root, "logs");
                Directory.CreateDirectory(d);
                File.AppendAllText(Path.Combine(d, "console_open.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  自家控制台窗口不可用 ⇒ 回退浏览器：" + why + Environment.NewLine);
            }
            catch { }
        }

        /// 取"可直接使用的控制台地址"（含口令）。
        /// ① 优先 `logs\console.url`——**token 的拥有者写出来的现成地址**（webui 启动时落盘），别人只读；
        /// ② 兜底＝在 config.json 里**先定位 server 段**再取 token/port。
        /// ⛔ 绝不再全文找第一个 "token"：config.json 里排在前面的 `cloud.token` 是空串，
        ///    抓错就会打开一个 `/?token=` 的地址 ⇒ 控制台回 `{"error":"unauthorized"}`（另一台机器实测）。
        /// 从 config.json 的 **server 段**取 port。
        public static int ServerPortFromConfig(string root)
        {
            try
            {
                string cf = Path.Combine(root, "config.json");
                if (!File.Exists(cf)) return 0;
                string s = File.ReadAllText(cf);
                int i = s.IndexOf("\"server\"");
                if (i < 0) return 0;
                int b = s.IndexOf('{', i);
                if (b < 0) return 0;
                int depth = 0, j = b;
                for (; j < s.Length; j++)
                {
                    if (s[j] == '{') depth++;
                    else if (s[j] == '}') { depth--; if (depth == 0) break; }
                }
                string seg = s.Substring(b, Math.Min(s.Length, j + 1) - b);
                string p = JsonValue(seg, "port");
                int v = 0;
                if (p != "" && int.TryParse(p, out v) && v > 0) return v;
            }
            catch { }
            return 0;
        }

        /// 从 `logs\console.url` 里取端口。
        public static int PortFromUrlFile(string root)
        {
            try
            {
                string uf = Path.Combine(root, "logs", "console.url");
                if (!File.Exists(uf)) return 0;
                string u = (File.ReadAllText(uf) ?? "").Trim();
                int c = u.IndexOf("://");
                if (c < 0) return 0;
                string rest = u.Substring(c + 3);
                int slash = rest.IndexOf('/');
                if (slash >= 0) rest = rest.Substring(0, slash);
                int colon = rest.LastIndexOf(':');
                if (colon <= 0) return 0;
                int v = 0;
                if (int.TryParse(rest.Substring(colon + 1), out v) && v > 0) return v;
            }
            catch { }
            return 0;
        }

        /// 这个地址指向的端口**真有人应答吗**。
        ///
        /// 为什么必须有：`logs\console.url` 是**上一次**跑控制台时写下的地址，机器人停掉之后它仍在。
        /// 老实现只判"以 http 开头"就照它开窗 ⇒ 一屏 `ERR_CONNECTION_REFUSED` ＝ 用户口中的
        /// 「打不开控制台」。这里做一次 400ms 的 TCP 连接探测：连不上就**当作没有这个地址**，
        /// 让调用方走"重新拉起控制台"的正路（而不是开一个死窗）。
        /// 纯探测：不写任何文件、不发任何数据、立即关闭。
        public static bool PortAlive(string url, int timeoutMs = 400)
        {
            try
            {
                if (string.IsNullOrEmpty(url) || !url.StartsWith("http")) return false;
                string rest = url.Substring(url.IndexOf("://") + 3);
                int slash = rest.IndexOf('/');
                if (slash >= 0) rest = rest.Substring(0, slash);
                int colon = rest.LastIndexOf(':');
                if (colon <= 0) return false;
                string host = rest.Substring(0, colon);
                int port = 0;
                if (!int.TryParse(rest.Substring(colon + 1), out port) || port <= 0) return false;
                var c = new TcpClient();
                try
                {
                    var ar = c.BeginConnect(host, port, null, null);
                    if (!ar.AsyncWaitHandle.WaitOne(timeoutMs)) return false;
                    c.EndConnect(ar);
                    return c.Connected;
                }
                finally { try { c.Close(); } catch { } }
            }
            catch { return false; }
        }

        public static string ConsoleUrl(string root, out string why)
        {
            why = "";
            try
            {
                string uf = Path.Combine(root, "logs", "console.url");
                if (File.Exists(uf))
                {
                    string u = (File.ReadAllText(uf) ?? "").Trim();
                    // ⛔ **先探活再用** —— 死链（机器人早停了、或端口顺延时写错了端口）
                    //   直接当"没有地址"，落到下面的配置回退；否则用户点「一键启动」只会得到
                    //   一个 ERR_CONNECTION_REFUSED 的空窗。
                    if (u.StartsWith("http"))
                    {
                        if (PortAlive(u)) return u;
                        why = "logs\\console.url 指向的端口没人应答（死链，按没有地址处理）";
                    }
                    else why = "logs\\console.url 内容不是地址";
                }
                else why = "没有 logs\\console.url";
            }
            catch (Exception ex) { why = "读 console.url 失败：" + ex.Message; }
            string port = PortHelper.ReadPort().ToString(), tok = ""; // 默认值也走同一份来源（不再写死 3210）
            try
            {
                string cf = Path.Combine(root, "config.json");
                if (!File.Exists(cf)) why += "；没有 config.json";
                else
                {
                    string s = File.ReadAllText(cf);
                    int i = s.IndexOf("\"server\"");
                    if (i < 0) why += "；config.json 里没有 server 段";
                    else
                    {
                        int b = s.IndexOf('{', i);
                        if (b < 0) why += "；server 段找不到左花括号";
                        else
                        {
                            int depth = 0, j = b;
                            for (; j < s.Length; j++)
                            {
                                if (s[j] == '{') depth++;
                                else if (s[j] == '}') { depth--; if (depth == 0) break; }
                            }
                            string seg = s.Substring(b, Math.Min(s.Length, j + 1) - b);
                            tok = JsonValue(seg, "token");
                            string p = JsonValue(seg, "port");
                            if (p != "") port = p;
                        }
                    }
                }
            }
            catch (Exception ex) { why += "；解析 config.json 失败：" + ex.Message; }
            return "http://127.0.0.1:" + port + "/" + (tok != "" ? ("?token=" + tok) : "");
        }

        /// 极简取值：在 JSON 片段里取 `"key": 值`（字符串去引号、数字/布尔原样）。
        /// 只用来读**我们自己的** config.json 片段，不做完整解析。
        static string JsonValue(string seg, string key)
        {
            try
            {
                int i = seg.IndexOf("\"" + key + "\"");
                if (i < 0) return "";
                int c = seg.IndexOf(':', i);
                if (c < 0) return "";
                int k = c + 1;
                while (k < seg.Length && char.IsWhiteSpace(seg[k])) k++;
                if (k >= seg.Length) return "";
                if (seg[k] == '"')
                {
                    int q2 = seg.IndexOf('"', k + 1);
                    return (q2 > k) ? seg.Substring(k + 1, q2 - k - 1) : "";
                }
                int e = k;
                while (e < seg.Length && ",}\r\n \t".IndexOf(seg[e]) < 0) e++;
                return seg.Substring(k, e - k).Trim();
            }
            catch { return ""; }
        }
        public static void FallbackBrowser(string url)
        {
            string dir = Path.GetDirectoryName(Application.ExecutablePath);
            // ⛔ **浏览器兜底也要探活**。
            //   这条路上没有 WebView2 可以画重试页 ⇒ 死链就是死链（用户截图正是 Edge 上的
            //   「无法访问此页面 / 127.0.0.1 拒绝连接」）⇒ 不再开它，改用**我们自己的**浮窗把话说清楚。
            if (string.IsNullOrEmpty(url) || !url.StartsWith("http"))
            {
                NoteFallback(dir, "没有可用地址 ⇒ 不开浏览器");
                return;
            }
            if (!PortAlive(url))
            {
                NoteFallback(dir, "地址端口没人应答（机器人没在跑/还没就绪）⇒ 不开死页，改弹自家提示窗");
                try { using (DeadLinkForm f = new DeadLinkForm(url)) { f.ShowDialog(); } } catch { }
                return;
            }
            try { System.Diagnostics.Process.Start(url); } catch { }
        }

        /// 自家提示窗：**"控制台还没就绪"**（死链时用它顶替一屏 ERR_CONNECTION_REFUSED）。
        /// 零系统 MessageBox（红线），按钮＝重试 / 关闭；重试就再探一次活、活着才开窗。
        public class DeadLinkForm : Form
        {
            public DeadLinkForm(string url)
            {
                string root = Path.GetDirectoryName(Application.ExecutablePath);
                Text = "群相 控制台";
                StartPosition = FormStartPosition.CenterScreen;
                // 原来**没设**边框 ⇒ 默认 `Sizable`，而 `StyleKit.Apply` 对
                //   `Sizable` 与 `FixedDialog` **都会**换自绘标题栏、都会把客户区定成"设计高度"。
                //   写出来是为了让"这一窗的高度账跟其它窗走同一条规则"这件事在代码里可见。
                FormBorderStyle = FormBorderStyle.FixedDialog;
                MaximizeBox = false; MinimizeBox = false;
                // 470×250 → 540×368（卡片化 + 明确的"为什么打不开 / 你该怎么办"两段）
                //   ⚠️ 这个高度是**标题栏以下**的设计高度（`Apply` 再 + BarH 给窗口长高，见 stylekit.cs）
                // ⛔ 第二次调（真 bug：正文被裁）：368 → 420。
                //   这一窗有**三段**卡内文字（m 一句 + m2 四条 bullet + m3 地址行），是三张卡里最挤的：
                //     16(上) + m17 + 14(间隔) + m2实测108 + 12 + m3实测14 + 16(下) ≈ 197
                //   而 368 高下卡片最多只能到 198（按钮 322 − 卡顶 104 − 20 间隔）⇒ 零余量。
                //   放开到 420 后卡片可以给到 250，余量 53px。
                ClientSize = new Size(540, 420);

                StyleKit.MakeIcon(this, root, new Point(StyleKit.Space.x6, StyleKit.Space.x5));

                Label t = new Label();
                t.Text = "控制台还没就绪";
                t.Font = StyleKit.Ui(StyleKit.TextScale.Title, FontStyle.Bold);
                t.ForeColor = StyleKit.Ink;
                t.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4, StyleKit.Space.x5 + 2);
                t.AutoSize = true;
                Controls.Add(t);

                Label s = new Label();
                s.Text = "现在打开只会是一屏错误页";
                s.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
                s.ForeColor = StyleKit.Warn; // 提醒（不是错误、也不是正常）⇒ 用中间那一档
                // ⚠️ （#18 叠字根治）：跟着标题实测底边走（原写死 +30，150% 下叠 12px）——BusyForm 同款
                s.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4,
                                       t.Bottom + StyleKit.Space.x1);
                s.AutoSize = true;
                Controls.Add(s);

                int cardW = 540 - StyleKit.Space.x6 * 2;
                // #12 F1 / #13 F3：卡顶走 `StyleKit.CardTopY`（原手写 104），
                //   高度不再手抄（这条一路抄过 172 → 250，每抄一次就漏一段正文 ⇒ 交给 `SealCard` 算）。
                CardPanel card = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, StyleKit.CardTopY), cardW);

                Label m = new Label();
                m.Text = "机器人还没起来（或刚刚被关掉），所以这个控制台地址现在没人应答。";
                m.Font = StyleKit.Ui(StyleKit.TextScale.Body, FontStyle.Regular);
                m.ForeColor = StyleKit.Ink;
                m.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
                // ⚠️ （#18 叠字根治）：高度原写死 22 ⇒ 改 FitLabel 实测（BusyForm 同款）
                m.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
                card.Controls.Add(m);
                int mh = StyleKit.FitLabel(m);

                Label m2 = new Label();
                m2.Text =
                    "可以这样做：\n"
                  + "  · 点「重试」—— 如果机器人刚好起来了，这里会直接接上去；\n"
                  + "  · 还没起来就双击「一键启动.exe」把控制台拉起来，再回来点「重试」；\n"
                  + "  · 反复不行的话，看 logs\\persona_morph.log 与 logs\\onestart.log 里的最后几行。";
                m2.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
                m2.ForeColor = StyleKit.InkBody;
                // ⚠️ （#18）：y 改跟 m 实测底边走（原写死 `Space.x4 + 30`）
                m2.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4 + Math.Max(1, mh) + StyleKit.Space.x2);
                // ⛔ （真 bug：正文被裁）：原手写 110，实测需要 ~152 ⇒ 第三条被切一半。
                m2.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
                card.Controls.Add(m2);
                StyleKit.FitLabel(m2); // #18：m3 直接跟 m2.Bottom ⇒ 这里不再需要接住返回值

                Label m3 = new Label();
                m3.Text = string.IsNullOrEmpty(url) ? "地址：（空）" : ("尝试的地址：" + NoticeForm.MaskToken(url));
                m3.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
                // ⛔ （可达性）：原来是 `StyleKit.Muted`(150,158,172) = 对比度 2.60:1，
                //   远低于 WCAG AA 的 4.5:1 ⇒ 这一行"地址"等于故意做成看不清。
                m3.ForeColor = StyleKit.Ink3ok;
                // ⛔ （被裁的第二层）：原来 m3 写死在 `Space.x4 + 146` —— 那是按
                //   "m2 手写 110"配出来的。m2 改成实测后 146 就不再成立 ⇒ 改成**跟着 m2 的实测底边走**，
                //   这样"m2 长高 ⇒ m3 自动下移"，两者永远不会叠在一起。
                //   （#18 再修：m2 自己的 y 也改实测推进了 ⇒ 这里直接用 m2.Bottom，不再手工拼链。）
                m3.Location = new Point(StyleKit.Space.x5, m2.Bottom + StyleKit.Space.x3);
                m3.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
                card.Controls.Add(m3);
                StyleKit.FitLabel(m3);
                StyleKit.SealCard(card);

                // #13 F2：宽度由文字实宽决定（原来抄 108 / 96），y 跟着卡片实测底边走
                //   （原来抄 `420 - x6 - 38` —— 那是把"窗高"当成了已知数，改窗高就会漂）。
                Button retry = StyleKit.MakeButton("重试");
                Button no = StyleKit.MakeButton("关闭");
                int dy = card.Bottom + StyleKit.CardGapY;
                retry.Location = new Point(540 - StyleKit.Space.x6 - retry.Width, dy);
                retry.FlatStyle = FlatStyle.Flat;
                retry.BackColor = Color.FromArgb(64, 140, 255);
                retry.ForeColor = Color.White;
                retry.Click += delegate
                {
                    try
                    {
                        string _why2 = "";
                        string u = Ui.ConsoleUrl(root, out _why2);
                        if (!string.IsNullOrEmpty(u) && Ui.PortAlive(u)) { Ui.OpenConsole(u); }
                    }
                    catch { }
                    Close();
                };
                Controls.Add(retry);
                no.Location = new Point(540 - StyleKit.Space.x6 - retry.Width - StyleKit.Space.x2 - no.Width, dy);
                no.FlatStyle = FlatStyle.Flat;
                no.Click += delegate { Close(); };
                Controls.Add(no);
                // #12 F1：窗高由最后一块底边反推（原来抄 420）。
                ClientSize = new Size(540, retry.Bottom + StyleKit.Space.x6);
                AcceptButton = retry; CancelButton = no;
                try { string ico = Path.Combine(root, "assets", "app.ico"); if (File.Exists(ico)) Icon = Icon.ExtractAssociatedIcon(ico); } catch { }
                StyleKit.Apply(this, "群相 控制台");
            }
        }

        /// 取证探针：把每个弹窗**离屏**渲染成 PNG（不显示、不抢焦点），逐张人工核对外观
        public static string ShotProbe(string dir)
        {
            var sb = new StringBuilder();
            try { Directory.CreateDirectory(dir); } catch { }
            TryShot(dir, sb, "launcher", delegate { return new LauncherForm() { ProbeMode = true }; });
            TryShot(dir, sb, "launcher-step3", delegate { LauncherForm lf = new LauncherForm() { ProbeMode = true }; lf.ProbeState(2); return lf; });
            TryShot(dir, sb, "busy", delegate { return new BusyForm(); });
            TryShot(dir, sb, "ask", delegate { return new AskForm(); });
            TryShot(dir, sb, "notice", delegate { return new NoticeForm(); });
            // 这两个原来**没有截图入口**（诊断 §1 表里的第 5、6 个弹窗），
            //   于是"所有弹窗一并处理"就少了取证；现在补齐，加上关闭器的结果窗（在 close.cs 里另跑）。
            TryShot(dir, sb, "deadlink", delegate { return new DeadLinkForm("http://127.0.0.1:3210/?token=abcdef123456"); });
            TryShot(dir, sb, "console", delegate { return new ConsoleForm("about:blank"); });
            // （B 批）：`WebView2MissingForm` 原来**零覆盖** —— 既不在 `--shot` 也不在
            //   `--dlgprobe`，于是它的按钮行溢出（4 颗挤一行 ⇒ 主按钮 x=-12、左边 12px 落在客户区外）
            //   一直没有任何机械判据看得见。
            //   本机实测：`hasBoot`=true（`assets\webview2\MicrosoftEdgeWebview2Setup.exe` 在）+
            //   `hasBrowser`=true（默认浏览器 ChromeHTML）⇒ 这一张**天然就是 4 颗按钮的溢出场景**。
            TryShot(dir, sb, "webview2missing", delegate {
                return new WebView2MissingForm(AppDomain.CurrentDomain.BaseDirectory,
                                               "http://127.0.0.1:3210/?token=abcdef123456"); });
            // 变体：换一个"没有引导器"的 root ⇒ `hasBoot`=false，覆盖「只有浏览器」那条分支
            //   （3 颗按钮、主按钮是"用浏览器打开"）。临时 root 里补一份 app-icon.png，
            //   免得这张图的图标缺失被误读成缺陷（`MakeIcon` 找不到图会静默返回 null）。
            TryShot(dir, sb, "webview2missing-noboot", delegate {
                string bare = Path.Combine(dir, "_noboot");
                try
                {
                    Directory.CreateDirectory(Path.Combine(bare, "assets"));
                    File.Copy(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "assets", "app-icon.png"),
                              Path.Combine(bare, "assets", "app-icon.png"), true);
                }
                catch { }
                return new WebView2MissingForm(bare, "http://127.0.0.1:3210/?token=abcdef123456");
            });
            return sb.ToString();
        }
        delegate Form FormMaker();
        static void TryShot(string dir, StringBuilder sb, string name, FormMaker mk)
        {
            // ⚠️ **构造**这一步也单独罩 + 落盘（接手方要求"构造 → 显示/复核 → DrawToBitmap
            //   各自 try/catch"）。构造失败和出图失败是两种完全不同的 bug，混在一个 catch 里等于没报。
            string errPath = Path.Combine(dir, "shot.err");
            Action<string> log = delegate (string s)
            {
                try { Directory.CreateDirectory(dir); File.AppendAllText(errPath, s + Environment.NewLine, new UTF8Encoding(false)); } catch { }
            };
            Form f = null;
            try { f = mk(); }
            catch (Exception ex)
            {
                sb.AppendLine(name + " FAIL[Construct] " + ex.GetType().Name + ": " + ex.Message);
                log("[TryShot/" + name + "/Construct] " + ex.ToString());
            }
            if (f == null) return;
            Shot(dir, sb, name, f);
        }

        static void Shot(string dir, StringBuilder sb, string name, Form f)
        {
            // ⚠️ **每一步各自 try/catch**（原来只有一个大 try，抛在哪一步分不出来）。
            //   接手方要的是"下次直接给行号"，所以这里把阶段名写进异常文本里。
            //   阶段划分与接手方逐字对齐：构造（在 TryShot 里）→ 显示/复核 → DrawToBitmap。
            string errPath = Path.Combine(dir, "shot.err");
            Action<string> log = delegate (string s)
            {
                try { Directory.CreateDirectory(dir); File.AppendAllText(errPath, s + Environment.NewLine, new UTF8Encoding(false)); } catch { }
            };
            string p = Path.Combine(dir, name + ".png");
            // 阶段 A：显示 + 复核 + DrawToBitmap + 存盘（`CaptureOffscreen` 内部再细分，见 StyleKit）
            try
            {
                string r = StyleKit.CaptureOffscreen(f, p); // 离屏 + 不抢前台（实现见 StyleKit）
                sb.AppendLine(name + " " + r);
                log("[Shot/" + name + "/OK] " + r);
            }
            catch (Exception ex)
            {
                sb.AppendLine(name + " FAIL[CaptureOffscreen] " + ex.GetType().Name + ": " + ex.Message);
                log("[Shot/" + name + "/CaptureOffscreen] " + ex.ToString());
            }
            // 阶段 B：关窗（关不掉会把下一个窗体顶到前台 ⇒ 单独报）
            try { f.Close(); }
            catch (Exception ex)
            {
                sb.AppendLine(name + " WARN[Close] " + ex.GetType().Name + ": " + ex.Message);
                log("[Shot/" + name + "/Close] " + ex.ToString());
            }
        }

        /// 弹窗清单（机械判据用）：窗体名 + 控件数 + 是否用了系统 MessageBox
        public static string DlgProbe()
        {
            var sb = new StringBuilder();
            var list = new System.Collections.Generic.List<Form>();
            try { list.Add(new LauncherForm() { ProbeMode = true }); } catch (Exception ex) { sb.AppendLine("LauncherForm 不可用: " + ex.Message); }
            try { list.Add(new BusyForm()); } catch (Exception ex) { sb.AppendLine("BusyForm 不可用: " + ex.Message); }
            try { list.Add(new AskForm()); } catch (Exception ex) { sb.AppendLine("AskForm 不可用: " + ex.Message); }
            try { list.Add(new NoticeForm()); } catch (Exception ex) { sb.AppendLine("NoticeForm 不可用: " + ex.Message); }
            // DeadLinkForm 原来不在清单里 ⇒ "所有弹窗"这条没有机械判据守着
            try { list.Add(new DeadLinkForm("http://127.0.0.1:3210/?token=abcdef123456")); } catch (Exception ex) { sb.AppendLine("DeadLinkForm 不可用: " + ex.Message); }
            // （B 批）：同 `ShotProbe` —— 这个窗体原来也不在清单里，
            //   于是"每颗按钮 x>=24 且 x+w<=516"这条几何判据**根本没有读数来源**。
            try { list.Add(new WebView2MissingForm(AppDomain.CurrentDomain.BaseDirectory, "http://127.0.0.1:3210/?token=abcdef123456")); } catch (Exception ex) { sb.AppendLine("WebView2MissingForm 不可用: " + ex.Message); }
            try { list.Add(new ConsoleForm("about:blank")); } catch (Exception ex) { sb.AppendLine("ConsoleForm 不可用（WebView2 未就绪）: " + ex.Message); }
            Form[] fs = list.ToArray();
            foreach (Form f in fs)
            {
                // 先离屏 Show 一次再读：构造函数里设的颜色会被 Load 时的统一主题覆盖，不 Show 就读到旧值
                //
                // ⚠️ （接手方回执指出 `--shot` 的 6 张图全「前台未变=False」，
                //   根因是"六个窗体里只有 `ConsoleForm` 覆写了 `ShowWithoutActivation`，
                //   其余五个在 `Show()` 那一刻必然抢前台"）：**这里原来也写的是 `f.Show()`** ——
                //   同一个根因的第二处。`--dlgprobe` 只是"读一遍控件树"，更没有任何理由抢前台。
                //   统一改走 `StyleKit.ShowNoActivate`（`ShowWindow(h, SW_SHOWNOACTIVATE)`），
                //   并把"这个探针自己有没有抢前台"量出来（`前台未变=`），免得下次再靠人肉猜。
                IntPtr fg0 = IntPtr.Zero;
                try { fg0 = StyleKit.GetForegroundWindow(); } catch { }
                try
                {
                    f.StartPosition = FormStartPosition.Manual;
                    f.Location = new Point(-4000, -4000);
                    f.ShowInTaskbar = false;
                    if (f.AcceptButton != null) f.AcceptButton = null; // 对话框键处理会尝试激活
                    if (f.CancelButton != null) f.CancelButton = null;
                    StyleKit.MarkNoActivate(f);
                    StyleKit.ShowNoActivate(f);
                    StyleKit.MarkNoActivate(f); // 显示过程可能重建句柄
                    for (int i = 0; i < 10; i++) { Application.DoEvents(); System.Threading.Thread.Sleep(15); }
                }
                catch (Exception ex) { sb.AppendLine(f.GetType().Name + " 显示失败: " + ex.GetType().Name + ": " + ex.Message); }
                sb.AppendLine(f.GetType().Name + " client=" + f.ClientSize.Width + "x" + f.ClientSize.Height + " controls=" + f.Controls.Count + " border=" + f.FormBorderStyle + " back=" + f.BackColor
                              + (fg0 != IntPtr.Zero ? (" 前台未变=" + (StyleKit.GetForegroundWindow() == fg0)) : ""));
                // `need` 判据**递归**走一遍 —— 卡片化之后说明文字都搬进了卡片
                //   （`StyleKit.CardPanel`，基类是 `System.Windows.Forms.Panel`）里，
                //   原来只扫 `f.Controls` 第一层 ⇒ 那些文字反而"逃出判据视线"（这正是 C1 那类截断的守备）
                AppendProbe(sb, f, "   ");
                f.Dispose();
            }
            return sb.ToString();
        }

        /// 递归列控件 + 量文字是否截断（`need.Height > lb.Height` ⇒ 打 `CLIP`）
        static void AppendProbe(StringBuilder sb, Control parent, string indent)
        {
            foreach (Control c in parent.Controls)
            {
                string extra = "";
                string label = c.Text ?? "";
                string fontDesc = "";
                try { if (c.Font != null) fontDesc = c.Font.Name + "/" + c.Font.SizeInPoints.ToString("0.#"); }
                catch { }
                // ⚠️ 补（接手方回执问
                //   `text=?`，确认一下是否有意为之」）：
                //   **根因是编码，不是控件**。`RoundButton.Text` 是**有值的**（`BuildTitleBar` 里
                //   `cls.Text = "✕"`，`OnPaint` 就是拿这个 `Text` 画的）；它显示成 `?` 是因为
                //   `--dlgprobe` 的输出走**系统 ANSI 代码页**（`docs/AGENTS.md` 写死的口径），
                //   `✕`(U+2715) 在这套代码页里没有对应字符 ⇒ 被替换成 `?`。
                //   所以：**不是有意留空**，是"原样输出在控制台里必然降级"。
                //
                //   为了让接手方**不看源码、不管代码页**都能判，这里给每个有文字的控件追加一份
                //   **纯 ASCII 证据** `cp=`（逐字符码位，空格分隔，十六进制）。`cp=2715` 就等价于
                //   "这里确实有一个 U+2715 的 ✕"，任何终端都能读。非 ASCII 才打（ASCII 文字自明）。
                string cp = "";
                bool hasNonAscii = false;
                for (int i = 0; i < label.Length; i++) { if (label[i] > 0x7E || label[i] < 0x20) { hasNonAscii = true; break; } }
                if (hasNonAscii && label.Length > 0)
                {
                    var cb = new StringBuilder();
                    for (int i = 0; i < label.Length && i < 12; i++)
                    {
                        if (i > 0) cb.Append(' ');
                        cb.Append(((int)label[i]).ToString("X4"));
                    }
                    cp = " cp=" + cb;
                }
                bool measurable = label.Length > 0 && c.Font != null && c.Width > 0;
                if (measurable)
                {
                    // （原来是 `System.Drawing.Size` 全名：本方法是 static，不继承 `Control.Size`，
                    //   而当时 `StyleKit` 里有个同名嵌套类 `Size` 会在名字查找里赢；该嵌套类已改名
                    //   `TextScale` ⇒ 遮蔽源消除，回退裸名。守备见 `_cs_precompile.py` 判据 5。）
                    Size need = TextRenderer.MeasureText(label, c.Font,
                        new Size(Math.Max(8, c.Width), int.MaxValue),
                        TextFormatFlags.WordBreak);
                    // （CLIP=19 那一轮）：判据从"只报不修"改成"报 + **当场按证据撑开**"。
                    //   理由：`need` 是唯一的真相来源，而 `c.Height` 可能来自构造时的固定值
                    //   （`AutoSize=false` + 手写 Size），或来自某次 `FitLabel` 后又换了字号。
                    //   探测的语义是"给我看真实控件树"，那就该把**同一把尺子**的结果落回控件上，
                    //   否则报出来的数字和用户看到的画面不是一回事。撑开后重读，`CLIP` 只会出现在
                    //   "连 `need` 都塞不进父容器"的情况（那是真错误，需要人看；继续报出来）。
                    //   ⚠️ 只对 `Label` 撑：按钮的尺寸是设计定死的（撑开它会破坏标题栏排版），
                    //      按钮只"报不修"。
                    int h0 = c.Height;
                    if (c is Label && need.Height > h0) c.Height = need.Height;
                    extra = " need=" + need.Width + "x" + need.Height
                          + " h=" + h0 + "->" + c.Height
                          + ((need.Height > c.Height) ? " CLIP" : "");
                }
                sb.AppendLine(indent + "- " + c.GetType().Name + " " + c.Bounds
                              + " text=" + label
                              + cp
                              + (fontDesc.Length > 0 ? " font=" + fontDesc : "")
                              + extra);
                if (c.HasChildren) AppendProbe(sb, c, indent + "  ");
            }
        }
    }
}
