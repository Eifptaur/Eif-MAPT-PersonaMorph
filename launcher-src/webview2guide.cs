// webview2guide.cs —— ⑤ WebView2 引导器（2026-09-15）
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
            why = "";
            string exe = BootstrapperPath(dir);
            if (exe == "") { why = "这个安装包没带引导器（assets\\webview2\\MicrosoftEdgeWebview2Setup.exe 不在）"; return false; }
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(exe, "/silent /install");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                using (Process p = Process.Start(psi))
                {
                    if (p == null) { why = "引导器没起来"; return false; }
                    p.WaitForExit(300000);      // 联网下载 ≈2MB，给 5 分钟；超时也往下走，靠回读判断
                }
            }
            catch (Exception ex) { why = "引导器起不来：" + ex.Message; return false; }
            string v = RuntimeVersion();
            if (v == "") { why = "装完注册表里仍然没有（多半是这台机器没网，或被安全软件拦下了）"; return false; }
            why = "已装好运行库 " + v;
            return true;
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

    /// 「本机缺 WebView2 运行库」时给用户看的面板：自绘、按"能做什么"给按钮，**不用系统弹窗**。
    ///
    /// 2026-09-23 并入弹窗族设计系统（用户：「并不是说只处理这一个弹窗，所有的弹窗都要一并处理」）：
    ///   原来它是**第 7 个弹窗、唯一没走 StyleKit 层级层**的一个 —— 没有图标、标题直接用
    ///   `new Font("Microsoft YaHei UI", 12F)`（既不在字号阶梯里，也没走 `Ui()` 那个"字体找不到就回退"的兜底），
    ///   正文是裸 `AutoSize` 标签、按钮手写 `Size(128,34)` 而**没有主次**（四个按钮长得一模一样）。
    ///   现在统一：图标 + 标题/状态分级 + 说明进卡片 + 主按钮（一键安装）用强调色、次按钮描边。
    internal class WebView2MissingForm : Form
    {
        public string Action = "none";     // install / browser / copy / none

        public WebView2MissingForm(string dir, string url)
        {
            bool hasBoot = WebView2Guide.BootstrapperPath(dir) != "";
            bool hasBrowser = WebView2Guide.DefaultBrowserExe() != "";

            Text = "群相 控制台窗口";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false;
            BackColor = Color.FromArgb(246, 248, 252);
            // 452×286 → 540×360（卡片化 + 补图标/状态行）
            //   ⚠️ 这个高度是**标题栏以下**的设计高度（`Apply` 再 + BarH 给窗口长高，见 stylekit.cs）
            ClientSize = new Size(540, 360);

            StyleKit.MakeIcon(this, dir, new Point(StyleKit.Space.x6, StyleKit.Space.x5));

            Label t = new Label();
            t.Text = "本机缺少 WebView2 运行库";
            t.Font = StyleKit.Ui(StyleKit.TextScale.Title, FontStyle.Bold);
            t.ForeColor = StyleKit.Ink;
            t.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4, StyleKit.Space.x5 + 2);
            t.AutoSize = true;
            Controls.Add(t);

            Label s = new Label();
            s.Text = hasBoot ? "可以一键装上" : "这次看不了控制台";
            s.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            s.ForeColor = hasBoot ? StyleKit.Warn : StyleKit.Danger;
            s.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4, StyleKit.Space.x5 + 30);
            s.AutoSize = true;
            Controls.Add(s);

            int cardW = 540 - StyleKit.Space.x6 * 2;
            // 2026-09-23（B 批）：高度**不再手写 168** —— 正文早就是内容驱动的（`FitLabel` 量 what/d，
            //   地址行接在实测底边之后），卡高写死 ⇒ 内容被卡片的裁剪区切掉。
            //   修前实拍：卡内子控件排到 Y=289，而卡高 168 ⇒「怎么办」下半段与「控制台地址」整行都在裁剪区外。
            //   ⇒ 与 launcher.cs 的 7 张卡同构：只给宽度，排完内容 `SealCard` 收口。
            CardPanel card = StyleKit.MakeCard(this, new Point(StyleKit.Space.x6, StyleKit.CardTopY), cardW);

            // 说清楚"这是干什么用的"——原来直接讲"控制台窗口要用它"，用户不知道"它"是什么
            Label what = new Label();
            what.Text = "控制台是一块内嵌的网页窗口，需要系统的 WebView2 运行库来显示。这台机器上没找到它。";
            what.Font = StyleKit.Ui(StyleKit.TextScale.Body, FontStyle.Regular);
            what.ForeColor = StyleKit.Ink;
            what.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
            // ⛔ 2026-09-23：手写 44 → 1（实测值说了算）
            what.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            card.Controls.Add(what);
            int whath = StyleKit.FitLabel(what);

            Label d = new Label();
            // ⛔ 2026-09-23：改成跟着 what 的实测底边走（原写死 +52）
            d.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4 + Math.Max(1, whath) + StyleKit.Space.x3);
            d.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            d.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            d.ForeColor = StyleKit.InkBody;
            if (hasBoot)
                d.Text = hasBrowser
                    ? "怎么办：点「一键安装并打开」联网装它（约 2MB 的小安装器，装完自动打开控制台）；\n"
                    + "不想装也行 —— 点「用浏览器打开」先用浏览器看着。"
                    : "怎么办：本机没检测到可用浏览器，所以**只能**装上它才能看控制台（需要联网）。\n"
                    + "点「一键安装并打开」即可，装完会自动把控制台打开。";
            else
                d.Text = hasBrowser
                    ? "怎么办：这个安装包没带引导器，可以先用浏览器打开；\n"
                    + "想要内嵌窗口的话，去微软官网装一下 WebView2 运行库（免费）。"
                    : "怎么办：这个安装包没带引导器，本机也没有可用浏览器 —— 这次看不了控制台。\n"
                    + "机器人仍会在后台正常运行，不影响它在微信里干活。";
            card.Controls.Add(d);
            int dh = StyleKit.FitLabel(d);

            Label u = new Label();
            u.Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);
            // ⛔ 2026-09-23 修（可达性）：Muted(150,158,172)=2.60:1 远低于 AA ⇒ 改 Ink3ok(5.0:1)。
            //   这一行是用户要照着念/粘贴的控制台地址，属正文。
            u.ForeColor = StyleKit.Ink3ok;
            // ⛔ 2026-09-23：改成跟着 d 的实测底边走（原写死 +122）
            u.Location = new Point(StyleKit.Space.x5,
                StyleKit.Space.x4 + Math.Max(1, whath) + StyleKit.Space.x3 + Math.Max(1, dh) + StyleKit.Space.x3);
            u.Size = new Size(cardW - StyleKit.Space.x5 * 2, 1);
            u.Text = "控制台地址：" + NoticeForm.MaskToken(url);
            card.Controls.Add(u);
            StyleKit.FitLabel(u);
            StyleKit.SealCard(card);     // #12 F1：高度到这里才定下来（按钮行跟着它的实测底边走）

            // 按钮：**有主次 + 分两行**（2026-09-23 B 批修）。
            //   修前：4 颗挤一行，`bx` 从 516 一路左推 ⇒ 主按钮 x=-12（左边 12px 落在客户区外，
            //   圆角与「一」字一起被切）；而 4 颗按合同宽加起来 ≈616 远超 540
            //   ⇒ **一行根本放不下**，这不是"换个算宽函数"能解决的 ⇒ 拆两行。
            //   与 launcher.cs 其余窗体统一口径：宽度一律 `MakeButton`（＝文字实宽 + 36）、
            //   位置从**卡片实测底边**往下排（原来写死 y=316，卡一长高就会叠上）、
            //   主按钮独占最下一行且右下角对齐，次按钮在它上一行同样右对齐。
            RoundButton ins = hasBoot ? StyleKit.MakeButton("一键安装并打开") : null;
            RoundButton br = hasBrowser ? StyleKit.MakeButton("用浏览器打开") : null;
            RoundButton cp = StyleKit.MakeButton("复制网址");
            RoundButton no = StyleKit.MakeButton("知道了");
            RoundButton pri = (ins != null) ? ins : br;      // 主按钮：能装就"一键安装"，否则"用浏览器"
            RoundButton secBr = (pri == br) ? null : br;     // 浏览器当了主按钮，次行不再重复一颗

            int right = 540 - StyleKit.Space.x6;             // 右对齐基准线 = 516
            int secY = card.Bottom + StyleKit.CardGapY;      // 次按钮行：跟着卡片实测底边
            int priY = secY + StyleKit.BtnH + StyleKit.Space.x2;

            int x = right;                                   // 次行从右往左排
            if (secBr != null) { secBr.Location = new Point(x - secBr.Width, secY); x -= secBr.Width + StyleKit.Space.x2; }
            cp.Location = new Point(x - cp.Width, secY); x -= cp.Width + StyleKit.Space.x2;
            no.Location = new Point(x - no.Width, secY); x -= no.Width + StyleKit.Space.x2;

            int bottom = secY + StyleKit.BtnH;               // 没有主按钮时，窗高按次行算
            if (pri != null)
            {
                pri.Location = new Point(right - pri.Width, priY);
                // 走 token 不写字面色：`Restyle` 的主按钮判定是颜色谓词（B>200 && R<140 && G<200），
                //   判完会把底色统一改写成 `Accent` ⇒ 写 `Accent` 与写那个字面量渲染完全一致。
                pri.BackColor = StyleKit.Accent;
                pri.ForeColor = Color.White;
                pri.Click += delegate { Action = (pri == ins) ? "install" : "browser"; Close(); };
                Controls.Add(pri);
                bottom = pri.Bottom;
            }
            if (secBr != null) { secBr.Click += delegate { Action = "browser"; Close(); }; Controls.Add(secBr); }
            cp.Click += delegate { Action = "copy"; Close(); };
            no.Click += delegate { Action = "none"; Close(); };
            Controls.Add(cp);
            Controls.Add(no);

            // #12 F1：窗高由**最后一行的底边**反推（原来写死 360 —— 卡一长高内容就顶出去）
            ClientSize = new Size(540, bottom + StyleKit.Space.x6);
            StyleKit.Apply(this, "群相 控制台窗口");   // ⚠️ 最后一句：Apply 之后不得再改边框
        }

    }
}
