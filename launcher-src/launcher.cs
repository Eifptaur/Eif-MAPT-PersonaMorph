// 群相 一键启动.exe：图形安装器（C# WinForms，嵌入鲸鱼图标，无控制台）
// 流程：准备 Python → onestart(事件) → 快捷方式询问 → 自动收尾
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace WxLauncher
{
    public class LauncherForm : Form
    {
        string Root;
        PictureBox pic;
        Label lblTitle, lblState;
        StepList stepList;   // W6b：4 个裸 Label 换成自绘步骤列表（徽章 + 连接线 + 当前行高亮）
        ProgressBar bar;
        Label lblSub;
        TextBox logBox;
        Button btnClose;
        bool done = false;
        bool _asking;
        public bool ProbeMode = false;   // 取证探针用：只渲染界面、不跑安装流程
        Form _askHolder;

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
            ClientSize = new Size(500, 440);

            pic = new PictureBox();
            try { if (File.Exists(Path.Combine(Root, "assets", "app-icon.png"))) { pic.Image = Image.FromFile(Path.Combine(Root, "assets", "app-icon.png")); } } catch { }
            pic.SizeMode = PictureBoxSizeMode.Zoom;
            pic.Location = new Point(24, 20); pic.Size = new Size(66, 66);
            Controls.Add(pic);

            lblTitle = new Label();
            lblTitle.Text = "群相 一键启动";
            lblTitle.Font = new Font("Microsoft YaHei UI", 15, FontStyle.Bold);
            lblTitle.Location = new Point(106, 22); lblTitle.AutoSize = true;
            Controls.Add(lblTitle);

            lblState = new Label();
            lblState.Text = "准备中…";
            lblState.Font = new Font("Microsoft YaHei UI", 10.5f);
            lblState.ForeColor = Color.FromArgb(58, 88, 128);
            lblState.Location = new Point(106, 60); lblState.AutoSize = true;
            Controls.Add(lblState);

            stepList = new StepList();
            stepList.SetSteps(new string[] { "准备 Python 环境", "检查 / 安装依赖", "环境自检（55 项）", "启动机器人（打开控制台）" });
            stepList.Location = new Point(50, 116); stepList.Size = new Size(400, 124);
            Controls.Add(stepList);

            bar = new RoundBar();   // W6：自绘圆角进度条（Value/Maximum 语义不变，流程代码无需改）
            bar.Location = new Point(50, 256); bar.Size = new Size(400, 20);
            bar.Style = ProgressBarStyle.Continuous; bar.Maximum = 100;
            Controls.Add(bar);

            lblSub = new Label();
            lblSub.Font = new Font("Microsoft YaHei UI", 8.5f);
            lblSub.ForeColor = Color.FromArgb(110, 122, 140);
            lblSub.Location = new Point(50, 282); lblSub.Size = new Size(400, 26);
            Controls.Add(lblSub);

            logBox = new TextBox();
            logBox.Multiline = true; logBox.ReadOnly = true; logBox.ScrollBars = ScrollBars.Vertical;
            logBox.WordWrap = true; logBox.Font = new Font("Consolas", 8.5f);
            logBox.BackColor = Color.FromArgb(252, 253, 255);
            logBox.Location = new Point(50, 312); logBox.Size = new Size(400, 74);
            Controls.Add(logBox);

            btnClose = new RoundButton();
            btnClose.Text = "关闭";
            btnClose.Size = new Size(104, 34);
            btnClose.Location = new Point(346, 394);
            btnClose.FlatStyle = FlatStyle.Flat;
            btnClose.Enabled = false;
            btnClose.Click += (s, e) => Close();
            Controls.Add(btnClose);

            Shown += (s, e) => { if (ProbeMode) return; Thread t = new Thread(StartFlow); t.IsBackground = true; t.Start(); };
            StyleKit.Apply(this, "群相 一键启动");

        }

        static Icon ExtractIcon(string p) { return Icon.ExtractAssociatedIcon(p); }
        void Log(string t) { if (InvokeRequired) { BeginInvoke((Action)(() => Log(t))); return; }
            if (logBox.Lines.Length > 60) { var t2 = logBox.Lines; logBox.Lines = t2.SubArray(t2.Length - 50); }
            logBox.AppendText(t + "\r\n"); }

        /// 取证用：把界面推到"进行到第 stepIdx 步"的样子（只动显示，不碰安装流程）
        public void ProbeState(int stepIdx)
        {
            SetState("环境自检（55 项）…", 70, stepIdx, "Python 就绪 · 依赖已就绪");
            Log("[取证] 步骤列表渲染到第 " + (stepIdx + 1) + " 步（绿勾/蓝点/灰号三态同框）");
        }

        void SetState(string txt, int pct, int stepIdx, string sub)
        {
            if (InvokeRequired) { BeginInvoke((Action)(() => SetState(txt, pct, stepIdx, sub))); return; }
            lblState.Text = txt;
            bar.Value = Math.Min(100, Math.Max(0, pct));
            lblSub.Text = sub ?? "";
            stepList.SetProgress(stepIdx);   // 徽章/连接线/高亮全在 StepList 里自绘
        }

        void ParseLine(string line)
        {
            if (line == null) return;
            if (line.StartsWith("@@PHASE:"))
            {
                string ph = line.Substring(8).Trim();
                if (ph == "deps") SetState("检查 / 安装依赖…", 12, 1, "Python 就绪");
                else if (ph == "selftest") SetState("环境自检（55 项）…", 70, 2, "");
                else if (ph == "boot") SetState("启动机器人…", 92, 3, "等待 Web 控制台就绪（自动打开浏览器）");
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
                    else if (ps[0] == "install") SetState("正在安装依赖…", 45, 1, "使用国内镜像下载安装（请稍候）");
                    else if (ps[0] == "selftest") SetState("环境自检（55 项）…", 70 + (int)(20 * r), 2, "第 " + dn + " / " + tt + " 项");
                }
            }
            else if (line.StartsWith("@@REQ_SHORTCUT"))
            {
                AskShortcut();
            }
            else if (line.StartsWith("@@DONE"))
            {
                SetState("启动完成 ✔", 100, 4, "");
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
            // 桌面已有快捷方式 → 不再弹询问
            try {
                string desk = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                if (File.Exists(Path.Combine(desk, "一键启动 群相.lnk")) || File.Exists(Path.Combine(desk, "一键启动 wx-agent.lnk"))) {   // 兼容旧名
                    Log("桌面快捷方式已存在，跳过询问");
                    return;
                }
            } catch { }
            _asking = true;
            Form q = new Form();
            _askHolder = q;
            q.Text = "群相 启动完成";
            q.StartPosition = FormStartPosition.CenterScreen;
            q.FormBorderStyle = FormBorderStyle.FixedDialog;
            q.MaximizeBox = false; q.MinimizeBox = false;
            q.BackColor = Color.FromArgb(246, 248, 252);
            q.ClientSize = new Size(460, 256);
            try { if (File.Exists(Path.Combine(Root, "assets", "app.ico"))) q.Icon = ExtractIcon(Path.Combine(Root, "assets", "app.ico")); } catch { }
            PictureBox qp = new PictureBox();
            try { if (File.Exists(Path.Combine(Root, "assets", "app-icon.png"))) qp.Image = Image.FromFile(Path.Combine(Root, "assets", "app-icon.png")); } catch { }
            qp.SizeMode = PictureBoxSizeMode.Zoom;
            qp.Location = new Point(24, 28); qp.Size = new Size(66, 66);
            q.Controls.Add(qp);
            Label qt = new Label();
            qt.Text = "群相 启动完成";
            qt.Font = new Font("Microsoft YaHei UI", 14, FontStyle.Bold);
            qt.Location = new Point(108, 28); qt.AutoSize = true;
            q.Controls.Add(qt);
            Label qm = new Label();
            qm.Text = "欢迎使用 群相！\r\n\r\n机器人已启动，建议在桌面创建「一键启动」快捷方式。\r\n是否现在创建？";
            qm.Font = new Font("Microsoft YaHei UI", 9.5f);
            qm.ForeColor = Color.FromArgb(76, 92, 118);
            qm.Location = new Point(108, 66); qm.Size = new Size(330, 128);   // 高 92 放不下三行（实测 need=125）
            q.Controls.Add(qm);
            Button qok = new RoundButton();
            qok.Text = "立即创建";
            qok.Size = new Size(146, 36);
            qok.Location = new Point(300, 204);
            qok.FlatStyle = FlatStyle.Flat;
            qok.BackColor = Color.FromArgb(64, 140, 255);
            qok.ForeColor = Color.White;
            qok.DialogResult = DialogResult.OK;
            q.Controls.Add(qok);
            Button qno = new RoundButton();
            qno.Text = "暂不";
            qno.Size = new Size(88, 36);
            qno.Location = new Point(200, 204);
            qno.FlatStyle = FlatStyle.Flat;
            qno.DialogResult = DialogResult.Cancel;
            q.Controls.Add(qno);
            q.AcceptButton = qok; q.CancelButton = qno;
            // 非模态：主窗自动关闭时不影响本询问窗
            qok.Click += (sender, e2) =>
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
                    Log("桌面快捷方式已创建（一键启动 群相）");
                }
                catch (Exception ex) { Log("快捷方式创建失败：" + ex.Message); }
                q.Close();
            };
            qno.Click += (sender, e2) => q.Close();
            q.FormClosed += (sender, e2) =>
            {
                if (!Visible) Application.Exit();
            };
            StyleKit.Apply(q, "群相 启动完成");   // 与 AskForm 同一套外观（此前这个内联窗还是系统标题栏）
            q.Show();
            _asking = false;
        }

        void StartFlow()
        {
            try
            {
                SetState("准备 Python 环境…", 5, 0, "检测系统/绿色版 Python（无需手动安装）");
                string ps1 = Path.Combine(Root, "scripts", "setup_python.ps1");
                if (!File.Exists(ps1)) { Fail("缺少 scripts\\setup_python.ps1"); return; }
                var pi = new ProcessStartInfo("powershell.exe",
                    "-NoProfile -ExecutionPolicy Bypass -File \"" + ps1 + "\"");
                pi.UseShellExecute = false; pi.CreateNoWindow = true;
                using (var p = Process.Start(pi)) { p.WaitForExit(); }
                Log("准备 Python 完成");

                string pyCmd = "";
                string pth = Path.Combine(Root, "logs", "python_path.txt");
                if (File.Exists(pth))
                {
                    try { pyCmd = File.ReadAllText(pth, Encoding.GetEncoding(936)).Trim(); }
                    catch { pyCmd = File.ReadAllText(pth).Trim(); }
                }
                if (string.IsNullOrEmpty(pyCmd) || !File.Exists(pyCmd))
                {
                    // 重跑一次准备再读
                    var pi2 = new ProcessStartInfo("powershell.exe",
                        "-NoProfile -ExecutionPolicy Bypass -File \"" + ps1 + "\"");
                    pi2.UseShellExecute = false; pi2.CreateNoWindow = true;
                    using (var p2 = Process.Start(pi2)) { p2.WaitForExit(); }
                    if (File.Exists(pth))
                    {
                        try { pyCmd = File.ReadAllText(pth, Encoding.GetEncoding(936)).Trim(); }
                        catch { pyCmd = File.ReadAllText(pth).Trim(); }
                    }
                }
                if (string.IsNullOrEmpty(pyCmd) || !File.Exists(pyCmd)) { Fail("Python 环境异常（runtime\\python\\python.exe 不存在）"); return; }

                SetState("检查 / 安装依赖…", 12, 1, "Python 就绪");
                string onestart = Path.Combine(Root, "scripts", "onestart.py");
                var po = new ProcessStartInfo(pyCmd, "-X utf8 \"" + onestart + "\"");
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
            SetState("一键启动失败", 0, 0, "详见 logs\\onestart.log");
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
            ClientSize = new Size(400, 222);
            try { string ico = Path.Combine(root, "assets", "app.ico"); if (File.Exists(ico)) Icon = Icon.ExtractAssociatedIcon(ico); } catch { }
            PictureBox pic = new PictureBox();
            try { string png = Path.Combine(root, "assets", "app-icon.png"); if (File.Exists(png)) pic.Image = Image.FromFile(png); } catch { }
            pic.SizeMode = PictureBoxSizeMode.Zoom;
            pic.Location = new Point(22, 22); pic.Size = new Size(58, 58);
            Controls.Add(pic);
            Label t = new Label();
            t.Text = "一键启动已在运行";
            t.Font = new Font("Microsoft YaHei UI", 13, FontStyle.Bold);
            t.Location = new Point(98, 24); t.AutoSize = true;
            Controls.Add(t);
            Label m = new Label();
            m.Text = "检测到一键启动已在运行。" + Environment.NewLine + "若看不到窗口，请稍候，" + Environment.NewLine + "或先点「一键关闭.exe」结束后重试。";
            m.Font = new Font("Microsoft YaHei UI", 9.5f);
            m.ForeColor = Color.FromArgb(76, 92, 118);
            m.Location = new Point(98, 60); m.Size = new Size(280, 104);   // 高 80 会把最后一行截掉（--dlgprobe 的 need 判据实测 100）
            Controls.Add(m);
            Button ok = new RoundButton();
            ok.Text = "好的";
            ok.Size = new Size(110, 34);
            ok.Location = new Point(158, 174);
            ok.FlatStyle = FlatStyle.Flat;
            ok.BackColor = Color.FromArgb(64, 140, 255);
            ok.ForeColor = Color.White;
            ok.DialogResult = DialogResult.OK;
            Controls.Add(ok);
            AcceptButton = ok;
            StyleKit.Apply(this, "群相 正在启动");   // ⚠️ 必须最后调：Apply 之前设 FixedDialog，之后不得再改边框（否则系统标题栏会回来，和自绘标题栏叠成两条）
        }
    }

    internal static class PortHelper
    {
        internal static int ReadPort()
        {
            try
            {
                string root = Path.GetDirectoryName(Application.ExecutablePath);
                string cf = Path.Combine(root, "config.json");
                if (File.Exists(cf))
                {
                    string t = File.ReadAllText(cf);
                    int i = t.IndexOf("\"port\"");
                    if (i >= 0)
                    {
                        int j = t.IndexOf(":", i + 6);
                        int k = t.IndexOf("\"", j + 1);
                        int end = t.IndexOf("\"", k + 1);
                        string v = t.Substring(k + 1, end - k - 1).Trim();
                        int pr = 0;
                        if (int.TryParse(v, out pr) && pr > 0) return pr;
                    }
                }
            }
            catch { }
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
            ClientSize = new Size(460, 256);
            try { string ico = Path.Combine(Root, "assets", "app.ico"); if (File.Exists(ico)) Icon = Icon.ExtractAssociatedIcon(ico); } catch { }
            PictureBox qp = new PictureBox();
            try { string png = Path.Combine(Root, "assets", "app-icon.png"); if (File.Exists(png)) qp.Image = Image.FromFile(png); } catch { }
            qp.SizeMode = PictureBoxSizeMode.Zoom;
            qp.Location = new Point(24, 28); qp.Size = new Size(66, 66);
            Controls.Add(qp);
            Label qt = new Label();
            qt.Text = "群相 启动完成";
            qt.Font = new Font("Microsoft YaHei UI", 14, FontStyle.Bold);
            qt.Location = new Point(108, 28); qt.AutoSize = true;
            Controls.Add(qt);
            Label qm = new Label();
            qm.Text = "欢迎使用 群相！\r\n\r\n机器人已启动，建议在桌面创建「一键启动」快捷方式。\r\n是否现在创建？";
            qm.Font = new Font("Microsoft YaHei UI", 9.5f);
            qm.ForeColor = Color.FromArgb(76, 92, 118);
            qm.Location = new Point(108, 66); qm.Size = new Size(330, 128);   // 高 92 放不下三行（实测 need=125）
            Controls.Add(qm);
            Button qok = new RoundButton();
            qok.Text = "立即创建";
            qok.Size = new Size(146, 36);
            qok.Location = new Point(300, 204);
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
            Button qno = new RoundButton();
            qno.Text = "暂不";
            qno.Size = new Size(88, 36);
            qno.Location = new Point(200, 204);
            qno.FlatStyle = FlatStyle.Flat;
            qno.Click += (s, e) => Close();
            Controls.Add(qno);
            AcceptButton = qok; CancelButton = qno;

            // 监控控制台（3210）：连续 3 次连不上 → 控制台已关闭 → 自动关闭本窗
            watch = new System.Windows.Forms.Timer();
            watch.Interval = 2000;
            watch.Tick += (s, e) =>
            {
                try
                {
                    using (var cc = new System.Net.Sockets.TcpClient()) { cc.Connect("127.0.0.1", 3210); }
                    failCount = 0;
                }
                catch
                {
                    failCount++;
                    if (failCount >= 3) { try { watch.Stop(); Close(); } catch { } }
                }
            };
            watch.Start();
            FormClosed += (s, e) => { try { watch.Stop(); } catch { } };
            StyleKit.Apply(this, "群相 启动完成");   // 同 BusyForm：Apply 放最后，避免系统标题栏与自绘标题栏叠两条
        }
    }

    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            StyleKit.Prep();   // W6：高 DPI（PerMonitorV2）+ 关掉系统崩溃弹窗（产品不许弹系统 MessageBox）
            // W6 取证/集成入口：
            //   --console <url>  用自带标题栏的 WebView2 窗口打开控制台（Python 侧不再开浏览器）
            //   --shot <dir>     把每个弹窗离屏渲染成 PNG（不出现在屏幕上、不抢焦点）
            //   --dlgprobe       打印弹窗清单与控件（机械判据用）
            if (args != null && args.Length > 1 && args[0] == "--console")
            {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new ConsoleForm(args[1]));
                return;
            }
            if (args != null && args.Length > 1 && args[0] == "--shot")
            {
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Console.WriteLine(Ui.ShotProbe(args[1]));
                return;
            }
            if (args != null && args.Length > 0 && args[0] == "--dlgprobe")
            {
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Console.WriteLine(Ui.DlgProbe());
                return;
            }
            if (args != null && args.Length > 0 && args[0] == "--ask")
            {
                // 快捷方式询问窗（独立进程）：监控 3210 控制台，控制台关闭时自动关闭
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new AskForm());
                return;
            }
            // 前置检测：控制台已在运行（3210 有服务）→ 弹自定义提示窗，绝不重复打开/重复安装
            try
            {
                using (var c = new System.Net.Sockets.TcpClient())
                {
                    c.Connect("127.0.0.1", PortHelper.ReadPort());
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new NoticeForm());
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
            ClientSize = new Size(440, 254);
            try { string ico = Path.Combine(root, "assets", "app.ico"); if (File.Exists(ico)) Icon = Icon.ExtractAssociatedIcon(ico); } catch { }
            PictureBox pic = new PictureBox();
            try { string png = Path.Combine(root, "assets", "app-icon.png"); if (File.Exists(png)) pic.Image = Image.FromFile(png); } catch { }
            pic.SizeMode = PictureBoxSizeMode.Zoom;
            pic.Location = new Point(22, 24); pic.Size = new Size(64, 64);
            Controls.Add(pic);
            Label t = new Label();
            t.Text = "控制台已在运行";
            t.Font = new Font("Microsoft YaHei UI", 14, FontStyle.Bold);
            t.Location = new Point(104, 26); t.AutoSize = true;
            Controls.Add(t);
            string tok = "";
            try
            {
                string cf = Path.Combine(root, "config.json");
                if (File.Exists(cf))
                {
                    string cfText = File.ReadAllText(cf);
                    int ci = cfText.IndexOf("\"token\"");
                    if (ci >= 0)
                    {
                        int cj = cfText.IndexOf("\"", ci + 8);
                        int ck = cfText.IndexOf("\"", cj + 1);
                        if (cj >= 0 && ck > cj) tok = cfText.Substring(cj + 1, ck - cj - 1);
                    }
                }
            }
            catch { }
            Label m = new Label();
            m.Text = "检测到 群相 控制台已在运行。" + Environment.NewLine + Environment.NewLine +
                "为保持唯一，本次不再重复打开浏览器窗口。" + Environment.NewLine + "需要打开控制台请点下方按钮。";
            m.Font = new Font("Microsoft YaHei UI", 9.5f);
            m.ForeColor = Color.FromArgb(76, 92, 118);
            m.Location = new Point(104, 64); m.Size = new Size(315, 128);   // 高 92 放不下四行（实测 need=125）
            Controls.Add(m);
            Button ok = new RoundButton();
            ok.Text = "打开控制台";
            ok.Size = new Size(136, 36);
            ok.Location = new Point(290, 202);
            ok.FlatStyle = FlatStyle.Flat;
            ok.BackColor = Color.FromArgb(64, 140, 255);
            ok.ForeColor = Color.White;
            ok.Click += (ss, ee) =>
            {
                try
                {
                    string url = "http://127.0.0.1:" + PortHelper.ReadPort() + "/?token=" + tok;
                    Ui.OpenConsole(url);   // W6：改用自带标题栏的 WebView2 内嵌窗口（不可用时自动回退浏览器）
                }
                catch { }
                Close();
            };
            Controls.Add(ok);
            Button no = new RoundButton();
            no.Text = "知道了";
            no.Size = new Size(92, 36);
            no.Location = new Point(188, 202);
            no.FlatStyle = FlatStyle.Flat;
            no.Click += (ss, ee) => Close();
            Controls.Add(no);
            AcceptButton = ok; CancelButton = no;
            StyleKit.Apply(this, "群相 已就绪");
        }
    }

    // ================= W6：统一外观（StyleKit）=================

    /// WebView2 内嵌控制台：自带标题栏（无边框 + 圆角 + 可拖动），WebView2 不可用时回退到浏览器
    public class ConsoleForm : Form
    {
        string _url;
        Microsoft.Web.WebView2.WinForms.WebView2 _wv;
        public ConsoleForm(string url)
        {
            _url = url;
            Text = "群相 控制台";
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(1180, 780);
            BackColor = StyleKit.Bg;
            Panel bar = new Panel();
            bar.Height = 42; bar.Dock = DockStyle.Top; bar.BackColor = StyleKit.Bg;
            bar.MouseDown += delegate { StyleKit.Drag(Handle); };
            Label t = new Label();
            t.Text = "群相 控制台";
            t.Font = StyleKit.Ui(10.5f, FontStyle.Bold);
            t.ForeColor = StyleKit.Ink;
            t.AutoSize = true; t.Location = new Point(14, 12);
            t.MouseDown += delegate { StyleKit.Drag(Handle); };
            bar.Controls.Add(t);
            Button min = new RoundButton();
            min.Text = "—"; min.Size = new Size(34, 26); min.FlatStyle = FlatStyle.Flat;
            min.FlatAppearance.BorderSize = 0; min.BackColor = StyleKit.Bg; min.ForeColor = StyleKit.Sub;
            min.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            min.Location = new Point(bar.Width - 82, 8);
            min.Click += delegate { WindowState = FormWindowState.Minimized; };
            bar.Controls.Add(min);
            Button cls = new RoundButton();
            cls.Text = "✕"; cls.Size = new Size(34, 26); cls.FlatStyle = FlatStyle.Flat;
            cls.FlatAppearance.BorderSize = 0; cls.BackColor = StyleKit.Bg; cls.ForeColor = StyleKit.Sub;
            cls.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            cls.Location = new Point(bar.Width - 42, 8);
            cls.Click += delegate { Close(); };
            bar.Controls.Add(cls);
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
                if (!e.IsSuccess) { Ui.FallbackBrowser(_url); Close(); return; }
                try { _wv.CoreWebView2.Navigate(_url); } catch { Ui.FallbackBrowser(_url); }
            };
            Controls.Add(bar);
            Controls.Add(_wv);
            bar.BringToFront();
            Shown += delegate
            {
                try { _wv.EnsureCoreWebView2Async(null); }
                catch { Ui.FallbackBrowser(_url); Close(); }
            };
            StyleKit.Apply(this, "群相 控制台");
        }
    }

    internal static class Ui
    {
        /// 打开控制台：优先用我们自己的 WebView2 内嵌窗口；DLL 缺失/初始化失败则回退系统浏览器
        public static void OpenConsole(string url)
        {
            try
            {
                string dir = Path.GetDirectoryName(Application.ExecutablePath);
                bool has = File.Exists(Path.Combine(dir, "lib", "Microsoft.Web.WebView2.WinForms.dll"))
                        || File.Exists(Path.Combine(dir, "Microsoft.Web.WebView2.WinForms.dll"));
                if (!has) { FallbackBrowser(url); return; }
                Application.Run(new ConsoleForm(url));
            }
            catch { FallbackBrowser(url); }
        }
        public static void FallbackBrowser(string url)
        {
            try { System.Diagnostics.Process.Start(url); } catch { }
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
            TryShot(dir, sb, "console", delegate { return new ConsoleForm("about:blank"); });
            return sb.ToString();
        }
        delegate Form FormMaker();
        static void TryShot(string dir, StringBuilder sb, string name, FormMaker mk)
        {
            try { Shot(dir, sb, name, mk()); }
            catch (Exception ex) { sb.AppendLine(name + " UNAVAILABLE " + ex.GetType().Name + ": " + ex.Message); }
        }

        static void Shot(string dir, StringBuilder sb, string name, Form f)
        {
            try
            {
                string p = Path.Combine(dir, name + ".png");
                sb.AppendLine(name + " " + StyleKit.CaptureOffscreen(f, p));   // 离屏 + 不抢前台（实现见 StyleKit）
                f.Close();
            }
            catch (Exception ex) { sb.AppendLine(name + " FAIL " + ex.Message); }
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
            try { list.Add(new ConsoleForm("about:blank")); } catch (Exception ex) { sb.AppendLine("ConsoleForm 不可用（WebView2 未就绪）: " + ex.Message); }
            Form[] fs = list.ToArray();
            foreach (Form f in fs)
            {
                // 先离屏 Show 一次再读：构造函数里设的颜色会被 Load 时的统一主题覆盖，不 Show 就读到旧值
                try
                {
                    f.StartPosition = FormStartPosition.Manual;
                    f.Location = new Point(-4000, -4000);
                    f.ShowInTaskbar = false;
                    f.Show();
                    for (int i = 0; i < 8; i++) { Application.DoEvents(); System.Threading.Thread.Sleep(15); }
                }
                catch { }
                sb.AppendLine(f.GetType().Name + " client=" + f.ClientSize.Width + "x" + f.ClientSize.Height + " controls=" + f.Controls.Count + " border=" + f.FormBorderStyle + " back=" + f.BackColor);
                foreach (Control c in f.Controls)
                {
                    // 机械判据：文字需要的高度 > 控件高度 ⇒ 截断（此前只能靠肉眼看图，本轮改成可判定的读数）
                    string extra = "";
                    Label lb = c as Label;
                    if (lb != null && !string.IsNullOrEmpty(lb.Text))
                    {
                        Size need = TextRenderer.MeasureText(lb.Text, lb.Font, new Size(Math.Max(8, lb.Width), int.MaxValue), TextFormatFlags.WordBreak);
                        extra = " need=" + need.Width + "x" + need.Height + ((need.Height > lb.Height) ? " CLIP" : "");
                    }
                    sb.AppendLine("   - " + c.GetType().Name + " " + c.Bounds + " text=" + (c.Text ?? "") + extra);
                }
                f.Dispose();
            }
            return sb.ToString();
        }
    }
}
