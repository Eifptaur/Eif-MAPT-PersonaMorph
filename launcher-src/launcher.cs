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
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Console.WriteLine(Ui.DlgProbe());
                return;
            }
            //   --webview2probe  ⑤：打印运行库版本 / 引导器在不在 / 默认浏览器（机械判据只认这三行 ASCII）
            if (args != null && args.Length > 0 && args[0] == "--webview2probe")
            {
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                Console.WriteLine(WebView2Guide.Probe(Path.GetDirectoryName(Application.ExecutablePath)));
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
            string _curl = "";     // 现成的控制台地址（含口令），只从权威来源取
            try
            {
                string _why = "";
                _curl = Ui.ConsoleUrl(root, out _why);
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
                    string url = _curl;
                    if (!url.StartsWith("http"))
                        url = "http://127.0.0.1:" + PortHelper.ReadPort() + "/";
                    Ui.OpenConsole(url);   // W6：自带标题栏的 WebView2 内嵌窗口（不可用时才回退浏览器，原因写 logs\console_open.log）
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

    /// WebView2 内嵌控制台：自带标题栏（无边框 + 圆角 + 可拖动 + **可拉伸**），WebView2 不可用时回退到浏览器
    public class ConsoleForm : Form
    {
        string _url;
        int _ring = 3;                 // 边缘缩放环宽（逻辑 px，OnLoad 里按窗口 DPI 换算）
                                       // 2026-09-15 用户「这个边框太大了」⇒ 6 → 3；最大化时内边距直接归零
        GlyphButton _btnMax;           // 最大化/还原按钮（自绘字形，随状态换 kind）
        public bool ProbeOnly;         // 取证探针用：不初始化 WebView2（免得探针把浏览器弹出来）
        /// 不激活显示：`Show()` 时用 SW_SHOWNOACTIVATE，**不抢用户前台**
        /// （取值探针 --cursorprobe 用；实测：只加 WS_EX_NOACTIVATE 还不够，WinForms 的 Show() 仍会激活）
        public bool NoActivate;
        protected override bool ShowWithoutActivation { get { return NoActivate; } }
        public string MaxGlyph { get { return _btnMax == null ? "" : _btnMax.Text; } }
        /// 取证探针（--cursorprobe）要直接在这个 WebView2 里跑 JS，故开放只读引用
        public Microsoft.Web.WebView2.WinForms.WebView2 ProbeView { get { return _wv; } }
        Microsoft.Web.WebView2.WinForms.WebView2 _wv;

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

        /// 全屏/还原时同步窗口内边距（2026-09-15 用户：「全屏就应该是完全的全屏，这个边框也要消掉的」）：
        /// 无边框窗体的 Maximized 本来就铺满整屏（含任务栏），把内边距归零画面就真顶到边。
        public void ApplyChrome()
        {
            try { Padding = (WindowState == FormWindowState.Maximized) ? new Padding(0) : new Padding(_ring, 0, _ring, _ring); }
            catch { }
        }

        /// 给 ESC 过滤器用（`_btnMax` 是私有字段）
        public GlyphButton MaxButton { get { return _btnMax; } }

        /// ESC 退出全屏（用户 2026-09-15：「退出全屏快捷键，你设置好，最好是ESC」）。
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
            if (m.Msg == 0x0024)   // WM_GETMINMAXINFO
            {
                try
                {
                    MINMAXINFO mmi = (MINMAXINFO)System.Runtime.InteropServices.Marshal.PtrToStructure(m.LParam, typeof(MINMAXINFO));
                    Screen sc = Screen.FromHandle(Handle);
                    Rectangle wa = sc.WorkingArea, mo = sc.Bounds;
                    mmi.ptMaxPosition.x = wa.Left - mo.Left;
                    mmi.ptMaxPosition.y = wa.Top - mo.Top;
                    mmi.ptMaxSize.x = wa.Width;
                    mmi.ptMaxSize.y = wa.Height;
                    System.Runtime.InteropServices.Marshal.StructureToPtr(mmi, m.LParam, false);
                }
                catch { }
                m.Result = IntPtr.Zero; return;
            }
            if (m.Msg == 0x0083)   // WM_NCCALCSIZE：最大化时把系统加的那圈边框收回去
            {
                if (WindowState == FormWindowState.Maximized)
                {
                    try
                    {
                        NCCALCSIZE_PARAMS nc = (NCCALCSIZE_PARAMS)System.Runtime.InteropServices.Marshal.PtrToStructure(m.LParam, typeof(NCCALCSIZE_PARAMS));
                        int bx = FramePx(true), by = FramePx(false);
                        nc.rgrc0.left += bx; nc.rgrc0.top += by;
                        nc.rgrc0.right -= bx; nc.rgrc0.bottom -= by;
                        System.Runtime.InteropServices.Marshal.StructureToPtr(nc, m.LParam, false);
                    }
                    catch { }
                }
                m.Result = IntPtr.Zero; return;
            }
            if (m.Msg == 0x00A3) { ToggleMax(); return; }        // 标题栏双击＝最大化/还原
            if (m.Msg == 0x0084)   // WM_NCHITTEST
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
                ApplyChrome();                                                    // 全屏态内边距为 0
                try { Application.AddMessageFilter(new EscFilter(this)); } catch { }   // ESC 退出全屏
                ClientSize = new Size(w, h);
                MinimumSize = new Size(mw, mh);
            }
            catch { }
        }

        public ConsoleForm(string url)
        {
            _url = url;
            Text = "群相 控制台";
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(1180, 780);   // OnLoad 里按 DPI/工作区重算
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
            // 顶栏三个按钮**自绘**（2026-09-15 用户：「减号、全屏、叉号的样子很奇怪呀，不统一」）：
            // 旧实现用 `—` / `□` / `✕` 三种字形，笔画与基线各不相同 ⇒ 一排看过去必然怪。见 wingliphs.cs。
            GlyphButton min = new GlyphButton();
            min.Kind = GlyphKind.Min; min.Size = new Size(34, 26);
            min.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            min.Location = new Point(bar.Width - 122, 8);
            min.Click += delegate { WindowState = FormWindowState.Minimized; };
            bar.Controls.Add(min);
            // 最大化 / 还原（2026-09-14 用户：「自创原生显示屏是没有全屏键的，顶栏上只有两个按钮。需要一个全屏键」）
            GlyphButton maxb = new GlyphButton();
            maxb.Kind = GlyphKind.Max; maxb.Size = new Size(34, 26);
            maxb.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            maxb.Location = new Point(bar.Width - 82, 8);
            maxb.Click += delegate { ToggleMax(); };
            _btnMax = maxb;
            bar.Controls.Add(maxb);
            GlyphButton cls = new GlyphButton();
            cls.Kind = GlyphKind.Close; cls.Size = new Size(34, 26);
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
                try { _wv.CoreWebView2.Navigate(_url); } catch { Ui.FallbackBrowser(_url); }
            };
            Controls.Add(bar);
            Controls.Add(_wv);
            bar.BringToFront();
            Shown += delegate
            {
                if (ProbeOnly) return;      // 探针模式：只量几何，不起 WebView2
                try { _wv.EnsureCoreWebView2Async(null); }
                catch (Exception ex) { Ui.NoteFallback(Path.GetDirectoryName(Application.ExecutablePath), "EnsureCoreWebView2Async 抛异常：" + ex.Message); Ui.FallbackBrowser(_url); Close(); }
            };
            StyleKit.Apply(this, "群相 控制台");
        }
    }

    internal static class Ui
    {
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
                // ⑤（2026-09-15）：**运行库**不在时不要硬起窗口（初始化必失败、再兜底浏览器，用户看不懂为什么）。
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
                f.NoActivate = true;                       // Show() 不激活（否则会把用户前台抢走）
                f.ShowInTaskbar = false;
                f.StartPosition = FormStartPosition.Manual;
                f.Location = new Point(-4000, -4000);      // 屏外：看得见才怪，但它照样渲染/执行
                // 屏外 + WS_EX_NOACTIVATE：Show() **不激活**，用户的前台窗口不会被抢走
                // （StyleKit.CaptureOffscreen 用的同一招，2026-09-13 实测「前台未变=True」）
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
            string tok = "";
            int qi = u.IndexOf("token=");
            if (qi >= 0) tok = u.Substring(qi + 6);
            sb.AppendLine("console_url_file=" + (File.Exists(Path.Combine(root, "logs", "console.url")) ? "1" : "0"));
            sb.AppendLine("url=" + (tok.Length > 0 ? (u.Replace(tok, tok.Substring(0, Math.Min(3, tok.Length)) + "***")) : u));
            sb.AppendLine("token_len=" + tok.Length);
            sb.AppendLine("token_head=" + (tok.Length > 0 ? tok.Substring(0, Math.Min(3, tok.Length)) : ""));
            sb.AppendLine("fallback_reason=" + why);
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
        public static string ConsoleUrl(string root, out string why)
        {
            why = "";
            try
            {
                string uf = Path.Combine(root, "logs", "console.url");
                if (File.Exists(uf))
                {
                    string u = (File.ReadAllText(uf) ?? "").Trim();
                    if (u.StartsWith("http")) return u;
                    why = "logs\\console.url 内容不是地址";
                }
                else why = "没有 logs\\console.url";
            }
            catch (Exception ex) { why = "读 console.url 失败：" + ex.Message; }
            string port = "3210", tok = "";
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
