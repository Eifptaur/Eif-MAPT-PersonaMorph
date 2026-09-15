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
    internal class WebView2MissingForm : Form
    {
        public string Action = "none";     // install / browser / copy / none

        public WebView2MissingForm(string dir, string url)
        {
            bool hasBoot = WebView2Guide.BootstrapperPath(dir) != "";
            bool hasBrowser = WebView2Guide.DefaultBrowserExe() != "";

            Text = "群相 控制台窗口";
            ClientSize = new Size(452, 286);

            Label t = new Label();
            t.Text = "本机缺少 WebView2 运行库";
            t.Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold);
            t.Location = new Point(20, 18);
            t.AutoSize = true;
            Controls.Add(t);

            Label d = new Label();
            d.MaximumSize = new Size(412, 0);
            d.AutoSize = true;
            d.Location = new Point(20, 52);
            if (hasBoot)
                d.Text = hasBrowser
                    ? "控制台窗口要用它。可以一键联网安装（约 2MB 的小安装器），也可以先用浏览器打开。"
                    : "控制台窗口要用它，而本机没检测到可用浏览器 —— 只能装上它才能看控制台（需要联网）。";
            else
                d.Text = hasBrowser
                    ? "控制台窗口要用它，但这个安装包没带引导器。可以先用浏览器打开，或去微软官网装 WebView2 运行库。"
                    : "控制台窗口要用它，这个安装包也没带引导器，本机也没检测到可用浏览器 —— 这次看不了控制台。机器人仍会在后台正常运行。";
            Controls.Add(d);

            Label u = new Label();
            u.MaximumSize = new Size(412, 0);
            u.AutoSize = true;
            u.Location = new Point(20, 140);
            u.Text = "控制台地址：" + url;
            Controls.Add(u);

            int x = 20;
            if (hasBoot)
            {
                RoundButton ins = Make("一键安装并打开", x, 210);
                ins.Click += delegate { Action = "install"; Close(); };
                Controls.Add(ins); x += 140;
            }
            if (hasBrowser)
            {
                RoundButton br = Make("用浏览器打开", x, 210);
                br.Click += delegate { Action = "browser"; Close(); };
                Controls.Add(br); x += 140;
            }
            RoundButton cp = Make("复制网址", x, 210);
            cp.Click += delegate { Action = "copy"; Close(); };
            Controls.Add(cp);

            RoundButton no = Make("知道了", 20, 240);
            no.Click += delegate { Action = "none"; Close(); };
            Controls.Add(no);

            StyleKit.Apply(this, "群相 控制台窗口");
        }

        static RoundButton Make(string text, int x, int y)
        {
            RoundButton b = new RoundButton();
            b.Text = text;
            b.Size = new Size(128, 34);
            b.Location = new Point(x, y);
            b.FlatStyle = FlatStyle.Flat;
            return b;
        }
    }
}
