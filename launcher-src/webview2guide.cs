// webview2guide.cs —— ⑤ WebView2 引导器
//
// ⛔⛔⛔ **本文件整份待删， 之后不再需要。**
//     控制台一旦是本地原生控件，就不再经过 WebView2 ⇒
//       · 本文件（引导安装 WebView2）
//       · `launcher.cs` 里的 `WebView2MissingForm`（缺运行库的兜底提示）
//       · `launcher.cs` 里的 `ConsoleForm`（用 WebView2 渲染控制台）
//     三处**一起作废**，届时整块删除、不要再往里投入。
//
// ⚠️ 在 落地之前，本文件**仍在生效**（用户机器可能确实没装 WebView2），
// 所以 的按钮行溢出（主按钮 x=-12 被切）不算白修：过渡期还在用。
//    **但任何"为了更好看/更好用"的新投入，一律不做。** 只允许修阻断性缺陷。
//
// 补的缺口：**本机既没装 WebView2 运行库、又没可用浏览器** ⇒ 控制台根本打不开（旧实现只回退浏览器，
// 回退失败就什么都不发生，用户看到的是"点了按钮没反应"）。
//
// 依据（一手，微软官方《Distribute your app and the WebView2 Runtime》）：
//   · 检测：读注册表 `pv`（64 位在 HKLM\SOFTWARE\WOW6432Node\…\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}，
//     按用户安装的在 HKCU 同路径）；值为空或 0.0.0.0 ＝ 没装。
//   · 在线安装：随机带 Evergreen Bootstrapper，命令 `MicrosoftEdgeWebview2Setup.exe /silent /install`；
//     官方原文允许 "download the bootstrapper and package it with your WebView2 app"。
//   · `WebView2Loader.dll` 必须随包（本仓库在根目录 + lib\ 下都已跟踪）。
//
// 纪律：**不弹系统 MessageBox**（一律 StyleKit 自绘）；失败要**如实说原因**，不许静默什么都不做。
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

namespace WxLauncher
{
    internal static class WebView2Guide
    {
        const string RuntimeKey64 = @"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";
        const string RuntimeKey32 = @"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";

        /// 运行库版本（"" ＝ 没装或只有 0.0.0.0）。
        public static string RuntimeVersion()
        {
            RegistryKey[] roots = new RegistryKey[] { Registry.LocalMachine, Registry.CurrentUser };
            string[] subs = new string[] { RuntimeKey64, RuntimeKey32 };
            foreach (RegistryKey kv in roots)
            {
                foreach (string sub in subs)
                {
                    try
                    {
                        using (RegistryKey k = kv.OpenSubKey(sub))
                        {
                            if (k == null) continue;
                            string pv = k.GetValue("pv") as string;
                            if (!string.IsNullOrEmpty(pv) && pv != "0.0.0.0") return pv;
                        }
                    }
                    catch { }
                }
            }
            return "";
        }

        public static bool HasRuntime() { return RuntimeVersion() != ""; }

        /// 随机带的引导器：优先 assets\webview2\，再退回 exe 同目录。"" ＝ 没带。
        public static string BootstrapperPath(string dir)
        {
            string a = Path.Combine(dir, Path.Combine("assets", "webview2"), "MicrosoftEdgeWebview2Setup.exe");
            if (File.Exists(a)) return a;
            string b = Path.Combine(dir, "MicrosoftEdgeWebview2Setup.exe");
            if (File.Exists(b)) return b;
            return "";
        }

        /// 一键安装：跑 `/silent /install`，再**回读注册表**判定（只认回读，不认退出码）。
        public static bool Install(string dir, out string why)
        {
            return Install(dir, null, out why);
        }

        /// 带进度回调的安装（给「等待」档的双进度条用）。
        ///
        /// ⚠️ 为什么进度要**估**：官方引导器 `MicrosoftEdgeWebview2Setup.exe /silent /install`
        ///   不做任何进度输出（它自己静默下运行库再装，全程无窗口、无 stdout）——
        ///   官方文档也只承诺"以无提示方式安装"，**没有**给进度接口。
        ///   所以我们只能按**外部可观测量**推：进程还活着 ⇒ 在下载（引导器 ≈2MB 秒下完，
        ///   大头是运行库 ≈150MB）；进程退出 ⇒ 进安装阶段（再等注册表出现 `pv`）。
        ///   `Phase` 回调把这个"估计到哪一步了"告诉界面，界面照实显示、不编造精确百分比。
        /// ⚠️ 返回值与回读口径和 `Install(dir, out why)` 完全一致（只认注册表，不认退出码）。
        public static bool Install(string dir, Action<string> phase, out string why)
        {
            why = "";
            string exe = BootstrapperPath(dir);
            if (exe == "") { why = "这个安装包没带引导器（assets\\webview2\\MicrosoftEdgeWebview2Setup.exe 不在）"; return false; }
            if (phase != null) phase("download");
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(exe, "/silent /install");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                using (Process p = Process.Start(psi))
                {
                    if (p == null) { why = "引导器没起来"; return false; }
                    // 轮询：进程还在 ⇒ 继续下载；进程退出 ⇒ 换到"安装"阶段（界面上是第二条进度条）。
                    bool switched = false;
                    var sw = Stopwatch.StartNew();
                    while (!p.HasExited && sw.ElapsedMilliseconds < 300000)
                    {
                        // 12 秒还没退 ⇒ 基本可判定"运行库正在下"（引导器本身体积小、启动快）
                        if (!switched && sw.ElapsedMilliseconds > 12000)
                        {
                            switched = true;
                            if (phase != null) phase("install");
                        }
                        System.Threading.Thread.Sleep(250);
                    }
                    if (!switched && phase != null) phase("install");
                    try { if (!p.HasExited) p.WaitForExit(120000); } catch { }
                }
            }
            catch (Exception ex) { why = "引导器起不来：" + ex.Message; return false; }
            if (phase != null) phase("verify");
            string v = RuntimeVersion();
            if (v == "") { why = "装完注册表里仍然没有（多半是这台机器没网，或被安全软件拦下了）"; return false; }
            why = "已装好运行库 " + v;
            return true;
        }

        /// 后台安装：**不等**它跑完，起一个子进程就返回（给「后台下载」那一档用）。
        /// ⛔ 静默到底：`UseShellExecute=false`（不弹 UAC、不走 shell）+ `CreateNoWindow`（无窗口）。
        ///   用户什么都不会看到，装完下次打开控制台就能用。
        public static bool InstallDetached(string dir, out string why)
        {
            why = "";
            string exe = BootstrapperPath(dir);
            if (exe == "") { why = "这个安装包没带引导器（assets\\webview2\\MicrosoftEdgeWebview2Setup.exe 不在）"; return false; }
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(exe, "/silent /install");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                Process p = Process.Start(psi);
                if (p == null) { why = "引导器没起来"; return false; }
                why = "已在后台开始安装";
                return true;
            }
            catch (Exception ex) { why = "引导器起不来：" + ex.Message; return false; }
        }

        /// 默认浏览器的可执行文件（"" ＝ 这台机器没有可用浏览器）。
        public static string DefaultBrowserExe()
        {
            try
            {
                using (RegistryKey k = Registry.CurrentUser.OpenSubKey(
                    @"SOFTWARE\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"))
                {
                    string progId = k == null ? null : k.GetValue("ProgId") as string;
                    if (string.IsNullOrEmpty(progId)) return "";
                    using (RegistryKey c = Registry.ClassesRoot.OpenSubKey(progId + @"\shell\open\command"))
                    {
                        string cmd = c == null ? null : c.GetValue(null) as string;
                        if (string.IsNullOrEmpty(cmd)) return "";
                        int q1 = cmd.IndexOf('"');
                        if (q1 >= 0)
                        {
                            int q2 = cmd.IndexOf('"', q1 + 1);
                            if (q2 > q1) return cmd.Substring(q1 + 1, q2 - q1 - 1);
                        }
                        int sp = cmd.IndexOf(' ');
                        string f = (sp > 0 ? cmd.Substring(0, sp) : cmd).Trim('"');
                        return File.Exists(f) ? f : "";
                    }
                }
            }
            catch { return ""; }
        }

        /// 机械判据用：**只认这三行 ASCII 标记**（判据脚本按前缀解析，别改格式）。
        public static string Probe(string dir)
        {
            StringBuilder sb = new StringBuilder();
            sb.AppendLine("wv2_runtime=" + (HasRuntime() ? RuntimeVersion() : "none"));
            string boot = BootstrapperPath(dir);
            sb.AppendLine("wv2_bootstrapper=" + (boot == "" ? "missing" : "present"));
            string br = DefaultBrowserExe();
            sb.AppendLine("wv2_browser=" + (br == "" ? "none" : br));
            return sb.ToString();
        }
    }

    /// 「本机缺 WebView2 运行库」时给用户看的面板：自绘、**三段式**、**不用系统弹窗**、
    /// **不教用户做事**（用户口径："不要教用户做事，要帮用户把事办好"）。
    ///
    /// 三段式（全部在**同一个窗**里，不连开三个窗追着用户跑）：
    ///   ① **初始**：一句话说清"少个组件"，两个按钮 ——「忽略」「下载」。
    ///      点「忽略」⇒ 关窗、什么都不做（机器人在后台照常跑）；点「下载」⇒ 进 ②。
    ///   ② **下载方式**：两个按钮 ——「后台下载」「等待」。
    ///      ·「后台下载」⇒ 起一个静默子进程就关窗，用户继续干别的，装完下次打开就能用；
    ///      ·「等待」⇒ 进 ③，当场看进度。
    ///   ③ **进度**：下载进度条 + 安装进度条（两条），装完自动切到「已完成」那一句。
    ///
    /// ⛔ 全文不许出现"怎么办：点……"这类教程式文案（旧版就是那么写的，用户点过）。
    /// ⛔ 不许出现 Python / 运行库 / bootstrapper 这类内部词；也不许提"看网速"。
    internal class WebView2MissingForm : Form
    {
        public string Action = "none"; // ignore / download-bg / install-now / none

        // 三段式的界面元素（一次建好、按状态显隐；不重建控件，免得闪烁）
        Label s, d;                 // ① 的说明行
        RoundButton btnIgnore, btnDownload;     // ① 的两颗
        RoundButton btnBg, btnWait;             // ② 的两颗
        CardPanel cardProg;         // ③ 的进度卡
        RoundBar barDl, barIn;      // ③ 的两条进度条
        Label lblDl, lblIn, lblProgSub;         // ③ 的读数与当前动作
        RoundButton btnCloseX;       // ③ 阶段的"在后台继续"（＝放它自己跑）

        readonly string _dir;
        readonly int _cardW;
        int _stage = 1;             // 1=初始 2=选下载方式 3=看进度 4=已完成

        public WebView2MissingForm(string dir, string url)
        {
            _dir = dir;
            bool hasBoot = WebView2Guide.BootstrapperPath(dir) != "";

            Text = "群相 控制台窗口";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            BackColor = Color.FromArgb(246, 248, 252);
            ClientSize = new Size(540, 320);

            StyleKit.MakeIcon(this, dir, new Point(StyleKit.Space.x6, StyleKit.Space.x5));

            Label t = new Label();
            t.Text = "打开控制台还需要一个组件";
            t.Font = StyleKit.Ui(StyleKit.TextScale.Title, FontStyle.Bold);
            t.ForeColor = StyleKit.Ink;
            t.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4, StyleKit.Space.x5 + 2);
            t.AutoSize = true;
            Controls.Add(t);

            s = new Label();
            // ⛔ 不写"怎么办：点……"这类教程；只陈述事实（少什么 + 忽略会怎样 + 装它会怎样）。
            s.Text = hasBoot ? "这台电脑上少了一个用来显示网页的小组件。" : "这次暂时看不了控制台。";
            s.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            s.ForeColor = hasBoot ? StyleKit.Warn : StyleKit.Danger;
            s.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4,
                                   t.Bottom + StyleKit.Space.x1);
            s.AutoSize = true;
            Controls.Add(s);

            _cardW = 540 - StyleKit.Space.x6 * 2;
            CardPanel card = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, StyleKit.CardTopY), _cardW);

            d = new Label();
            d.Font = StyleKit.Ui(StyleKit.TextScale.Body, FontStyle.Regular);
            d.ForeColor = StyleKit.Ink;
            d.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
            d.Size = new Size(_cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(d);
            StyleKit.FitLabel(d);

            Label u = new Label();
            u.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            u.ForeColor = StyleKit.Ink3ok;
            u.Location = new Point(StyleKit.Space.x5, d.Bottom + StyleKit.Space.x3);
            u.Size = new Size(_cardW - StyleKit.Space.x5 * 2, 1);
            u.Text = "控制台地址：" + NoticeForm.MaskToken(url);
            card.Controls.Add(u);
            StyleKit.FitLabel(u);

            // ③ 的两条进度条（同一张卡里，先建好、默认隐藏）
            cardProg = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, card.Bottom + StyleKit.CardGapY), _cardW);
            Label tp = new Label();
            tp.Text = "正在处理";
            tp.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            tp.ForeColor = StyleKit.Ink;
            tp.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
            tp.AutoSize = true;
            cardProg.Controls.Add(tp);

            lblDl = new Label();
            lblDl.Text = "下载";
            lblDl.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            lblDl.ForeColor = StyleKit.InkBody;
            lblDl.Location = new Point(StyleKit.Space.x5, tp.Bottom + StyleKit.Space.x3);
            lblDl.AutoSize = true;
            cardProg.Controls.Add(lblDl);

            barDl = new RoundBar();
            barDl.Location = new Point(StyleKit.Space.x5, lblDl.Bottom + StyleKit.Space.x2);
            barDl.Size = new Size(_cardW - StyleKit.Space.x5 * 2, 18);
            barDl.Style = ProgressBarStyle.Continuous; barDl.Maximum = 100;
            cardProg.Controls.Add(barDl);

            lblIn = new Label();
            lblIn.Text = "安装";
            lblIn.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            lblIn.ForeColor = StyleKit.InkBody;
            lblIn.Location = new Point(StyleKit.Space.x5, barDl.Bottom + StyleKit.Space.x3);
            lblIn.AutoSize = true;
            cardProg.Controls.Add(lblIn);

            barIn = new RoundBar();
            barIn.Location = new Point(StyleKit.Space.x5, lblIn.Bottom + StyleKit.Space.x2);
            barIn.Size = new Size(_cardW - StyleKit.Space.x5 * 2, 18);
            barIn.Style = ProgressBarStyle.Continuous; barIn.Maximum = 100;
            cardProg.Controls.Add(barIn);

            lblProgSub = new Label();
            lblProgSub.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            lblProgSub.ForeColor = StyleKit.InkBody;
            lblProgSub.Location = new Point(StyleKit.Space.x5, barIn.Bottom + StyleKit.Space.x3);
            lblProgSub.Size = new Size(_cardW - StyleKit.Space.x5 * 2, 1);
            cardProg.Controls.Add(lblProgSub);
            StyleKit.FitLabel(lblProgSub);
            StyleKit.SealCard(cardProg);
            cardProg.Visible = false;

            // ── 按钮（三组，按状态显隐）──────────────────────────────────
            btnIgnore = StyleKit.MakeButton("忽略");
            btnDownload = StyleKit.MakeButton("下载");
            btnBg = StyleKit.MakeButton("后台下载");
            btnWait = StyleKit.MakeButton("等待");
            btnCloseX = StyleKit.MakeButton("在后台继续");

            int right = 540 - StyleKit.Space.x6;
            int by = card.Bottom + StyleKit.CardGapY;
            // ①「忽略」在左、「下载」在右（主按钮占右下，是全项目一致口径）
            btnIgnore.Location = new Point(StyleKit.Space.x6, by);
            btnDownload.Location = new Point(right - btnDownload.Width, by);
            // ②「后台下载」在左、「等待」在右
            btnBg.Location = new Point(StyleKit.Space.x6, by);
            btnWait.Location = new Point(right - btnWait.Width, by);
            // ③「在后台继续」占右下
            btnCloseX.Location = new Point(right - btnCloseX.Width, by);

            btnDownload.BackColor = StyleKit.Accent; btnDownload.ForeColor = Color.White;
            btnWait.BackColor = StyleKit.Accent; btnWait.ForeColor = Color.White;

            btnIgnore.Click += delegate { Action = "ignore"; Close(); };
            btnDownload.Click += delegate { SetStage(2, card); };
            btnBg.Click += delegate
            {
                string why;
                bool ok2 = WebView2Guide.InstallDetached(_dir, out why);
                Action = ok2 ? "download-bg" : "none";
                Close();
            };
            btnWait.Click += delegate { SetStage(3, card); StartWatch(); };
            btnCloseX.Click += delegate { Action = "download-bg"; Close(); };

            foreach (RoundButton b in new RoundButton[] { btnIgnore, btnDownload, btnBg, btnWait, btnCloseX })
                Controls.Add(b);
            btnBg.Visible = btnWait.Visible = btnCloseX.Visible = false;

            // 没带引导器 ⇒ 只剩"忽略"这一个诚实选项（不许给死按钮）
            if (!hasBoot)
            {
                btnDownload.Visible = false;
                btnIgnore.Text = "知道了";
                btnIgnore.Location = new Point(right - btnIgnore.Width, by);
            }

            SetStage(1, card);
            ClientSize = new Size(540, by + StyleKit.BtnH + StyleKit.Space.x6);
            StyleKit.Apply(this, "群相 控制台窗口"); // ⚠️ 最后一句：Apply 之后不得再改边框
        }

        /// 切段：只改文字与显隐，**不动几何**（窗高在构造期按最高那段定死）。
        void SetStage(int st, CardPanel card)
        {
            _stage = st;
            if (st == 1)
            {
                d.Text = "这个小组件是系统里用来显示网页的，装上以后就能在这个窗口里看控制台。\n"
                       + "不装也行 —— 机器人照样在后台干活，只是控制台要用浏览器看。";
                StyleKit.FitLabel(d);
                btnIgnore.Visible = true; btnDownload.Visible = (WebView2Guide.BootstrapperPath(_dir) != "");
                btnBg.Visible = btnWait.Visible = btnCloseX.Visible = false;
                cardProg.Visible = false;
            }
            else if (st == 2)
            {
                d.Text = "要现在装，还是让它自己在后台装？\n"
                       + "选「后台下载」你不用等，装完下次打开就能直接用。";
                StyleKit.FitLabel(d);
                btnIgnore.Visible = btnDownload.Visible = false;
                btnBg.Visible = btnWait.Visible = true; btnCloseX.Visible = false;
                cardProg.Visible = false;
            }
            else if (st == 3)
            {
                d.Text = "正在处理，你可以先去忙别的；这个窗口关掉也不影响它在后台继续。";
                StyleKit.FitLabel(d);
                btnIgnore.Visible = btnDownload.Visible = btnBg.Visible = btnWait.Visible = false;
                btnCloseX.Visible = true;
                cardProg.Visible = true;
                barDl.Value = 0; barIn.Value = 0;
                lblProgSub.Text = "正在下载…";
                StyleKit.FitLabel(lblProgSub);
            }
            else // 4
            {
                d.Text = "安装完成，您可以正常使用。";
                StyleKit.FitLabel(d);
                btnIgnore.Visible = btnDownload.Visible = btnBg.Visible = btnWait.Visible = false;
                btnCloseX.Visible = true;
                btnCloseX.Text = "打开控制台";
                cardProg.Visible = true;
                barDl.Value = 100; barIn.Value = 100;
                lblProgSub.Text = "已经装好了。";
                StyleKit.FitLabel(lblProgSub);
            }
        }

        /// ③ 的进度观察：另起线程跑带回调的安装，回调里 `BeginInvoke` 回 UI 线程刷新。
        void StartWatch()
        {
            System.Threading.Thread t = new System.Threading.Thread(delegate ()
            {
                string why;
                bool ok2 = WebView2Guide.Install(_dir, delegate (string ph)
                {
                    try
                    {
                        if (!IsHandleCreated || IsDisposed) return;
                        BeginInvoke((Action)delegate ()
                        {
                            if (IsDisposed) return;
                            if (ph == "download")
                            {
                                // 引导器端无进度接口 ⇒ 用"时间驱动"的**估计**填前一条，到点就换到下一条。
                                // ⚠️ 是估计不是实测：不许显示成精确数字来源的口气（文案讲"正在下载…"）。
                                barDl.Value = Math.Min(95, barDl.Value + 12);
                                lblProgSub.Text = "正在下载…";
                            }
                            else if (ph == "install")
                            {
                                barDl.Value = 100;
                                lblProgSub.Text = "正在安装…";
                            }
                            else if (ph == "verify")
                            {
                                barIn.Value = 92;
                                lblProgSub.Text = "正在确认…";
                            }
                            StyleKit.FitLabel(lblProgSub);
                        });
                    }
                    catch { }
                }, out why);
                try
                {
                    if (!IsHandleCreated || IsDisposed) return;
                    BeginInvoke((Action)delegate ()
                    {
                        if (IsDisposed) return;
                        if (ok2)
                        {
                            barIn.Value = 100;
                            SetStage(4, null);
                            Action = "install-now";
                        }
                        else
                        {
                            barIn.Value = 100;
                            d.Text = "这次没装上（" + why + "）。\n机器人仍然在后台正常运行，不影响它在微信里干活。";
                            StyleKit.FitLabel(d);
                            btnCloseX.Text = "知道了";
                            btnCloseX.Click += delegate { Action = "none"; };
                        }
                    });
                }
                catch { }
            });
            t.IsBackground = true;
            t.Start();
        }
    }
}
