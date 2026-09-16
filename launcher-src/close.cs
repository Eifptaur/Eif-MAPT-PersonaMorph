// 群相 一键关闭.exe：结束机器人/看门狗/启动器等全部相关进程 + 清理启动锁（自定义结果窗）
// 外观与 一键启动.exe 完全同一套：编译时把 launcher-src\stylekit.cs 一起编进去（StyleKit + RoundButton）
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
using System.Windows.Forms;
using WxLauncher;

namespace WxCloser
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            StyleKit.Prep();   // 高 DPI（PerMonitorV2）+ 关系统崩溃弹窗

            string root = Path.GetDirectoryName(Application.ExecutablePath);

            // 取证入口：把结果窗离屏渲染成 PNG（不显示、不抢焦点），与被关掉的进程无关
            if (args != null && args.Length > 1 && args[0] == "--shot")
            {
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                Console.WriteLine(Shot(args[1], root));
                return;
            }

            // 只列不动手：排查"为什么关不掉"时用（`一键关闭.exe --probe "%TEMP%\closeprobe.txt"`）
            if (args != null && args.Length > 1 && args[0] == "--probe")
            {
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                string outp = Probe(root);
                try { File.WriteAllText(args[1], outp, System.Text.Encoding.UTF8); } catch { }
                Console.WriteLine(outp);
                return;
            }

            List<string> killed = KillAll(root);
            using (Form f = BuildForm(root, killed))
                f.ShowDialog();
        }

        /// 结束我们自己的全部进程，并清掉启动锁 + 释放端口。
        /// **2026-09-16 重做（用户报「一键关闭又关不掉一键启动了」）**，三处关键改动：
        /// ① **两轮收**：先收"会把别人拉起来的"（看门狗/入口/两个 exe），再收 python 本体 ——
        ///    否则杀掉 `persona_morph.py` 后，`watchdog.py` 会在它自己被杀掉之前把机器人**重新拉起来**
        ///    （用户看到的就是"关不掉"）；
        /// ② **按安装目录收**（ExecutablePath 在本目录下的都算我们的）—— 只按命令行匹配会漏掉
        ///    "命令行读不出来/不含脚本名"的进程；
        /// ③ **失败如实报**（原来 `catch {}` 把 AccessDenied 吞了 ⇒ 界面上写着"没有残留进程"，
        ///    可 `一键启动` 的窗口还在——假成功比报错更糟）。
        static List<string> KillAll(string root)
        {
            var killed = new List<string>();
            var notes = new List<string>();
            string rootLower = root.TrimEnd('\\').ToLowerInvariant();
            // 第一轮：会把别人拉起来的那些（顺序有讲究，见上面的注释 ①）
            string[] first = { "watchdog.py", "onestart.py", "installer.ps1", "setup_python.ps1",
                               "close_all.ps1", "一键启动", "一键关闭" };
            // 第二轮：本体与其余入口
            string[] second = { "persona_morph.py", "wx_agent.py", "stop_bot.py" };
            killed.AddRange(Sweep(rootLower, first, notes, false));
            System.Threading.Thread.Sleep(300);
            killed.AddRange(Sweep(rootLower, second, notes, false));
            try { File.Delete(Path.Combine(root, "logs", "installer.lock")); } catch { }
            // ── 兜底 + 复核（2026-09-14 加）：按控制台端口把"命令行看不出来"的占用者也收掉，
            //    然后**回读端口**确认真关了——结果窗里如实写，不再只报"杀了几条"。
            try
            {
                int port = 3210;
                try
                {
                    string cf = Path.Combine(root, "config.json");
                    if (File.Exists(cf))
                    {
                        string s = File.ReadAllText(cf);
                        int i = s.IndexOf("\"port\"");
                        if (i >= 0)
                        {
                            int k = s.IndexOf(':', i) + 1;
                            while (k > 0 && k < s.Length && !char.IsDigit(s[k])) k++;
                            int e = k;
                            while (e < s.Length && char.IsDigit(s[e])) e++;
                            if (e > k) port = int.Parse(s.Substring(k, e - k));
                        }
                    }
                }
                catch { }
                int pidByPort = PortOwner(port);
                if (pidByPort > 0 && pidByPort != Process.GetCurrentProcess().Id)
                {
                    try
                    {
                        Process p2 = Process.GetProcessById(pidByPort);
                        string n2 = p2.ProcessName;
                        p2.Kill();
                        killed.Add(n2 + ".exe (pid " + pidByPort + " · 占用端口 " + port + ")");
                    }
                    catch { }
                }
                System.Threading.Thread.Sleep(700);
                int left = PortOwner(port);
                killed.Add(left > 0 ? ("端口 " + port + " 仍被 pid " + left + " 占用（未关干净）")
                                    : ("端口 " + port + " 已释放"));
            }
            catch { }
            // ── 复核（2026-09-16 加）：两轮收完之后**再看一眼**还剩下什么，如实写进结果窗 ——
            //    以前只报"成功杀掉的"，一个都杀不掉时界面写着"没有残留进程（早已关闭）"，
            //    可 `一键启动` 的窗口还在（假成功比报错更糟）。
            try
            {
                System.Threading.Thread.Sleep(700);
                var left = Sweep(rootLower, first, notes, true);
                left.AddRange(Sweep(rootLower, second, notes, true));
                if (left.Count > 0)
                {
                    killed.Add("⚠ 还有 " + left.Count + " 个进程没关掉：");
                    for (int i = 0; i < left.Count && i < 3; i++) killed.Add("   " + left[i]);
                }
            }
            catch { }
            killed.AddRange(notes);        // 失败/异常如实列在结果里（不许只报成功项）
            return killed;
        }

        /// 按"**装在我们安装目录里的** 或 **命令行里带着我们目录的**"筛一遍；
        /// `dry=true` 只列不动手（`--probe` 用）。
        /// ⛔ 铁律（2026-09-16 实测过的一版误杀）：**不是我们的目录，一律不碰** ——
        /// 只按"名字/命令行里出现「一键启动」"匹配会连 WebView2 的公用子进程、甚至别人的 node
        /// 一起收掉（那些进程的命令行里会带 `--webview-exe-name=一键启动.exe` 或我们的 user-data-dir）。
        static List<string> Sweep(string rootLower, string[] markers, List<string> notes, bool dry)
        {
            var got = new List<string>();
            // 我们发的脚本文件名（判定"宿主是系统进程、但跑的是我们的脚本"时必须出现其中之一）
            string[] scriptFiles = { "一键启动.vbs", "一键关闭.vbs", "停止机器人.vbs",
                                     "installer.ps1", "setup_python.ps1", "close_all.ps1" };
            try
            {
                var searcher = new ManagementObjectSearcher(
                    "SELECT ProcessId, Name, CommandLine, ExecutablePath FROM Win32_Process");
                foreach (ManagementObject o in searcher.Get())
                {
                    try
                    {
                        uint pid = (uint)o["ProcessId"];
                        if (pid == (uint)Process.GetCurrentProcess().Id) continue;
                        string name = Convert.ToString(o["Name"]) ?? "";
                        string cl = Convert.ToString(o["CommandLine"]) ?? "";
                        string exe = Convert.ToString(o["ExecutablePath"]) ?? "";
                        string nl = name.ToLowerInvariant();
                        string clL = cl.ToLowerInvariant();
                        string exeL = exe.ToLowerInvariant();
                        // ① 装在我们目录里的可执行（runtime\python、两个 exe、我们拉的子进程）
                        bool exeUnderRoot = (!exeL.Equals("") && exeL.StartsWith(rootLower));
                        // ② 我们自己控制台窗口的 WebView2 子进程（宿主名＝一键启动.exe）
                        bool wv2Ours = nl.IndexOf("msedgewebview2") >= 0
                                       && clL.IndexOf("--webview-exe-name=一键启动") >= 0;
                        // ③ 我们发的脚本（vbs/ps1/cmd）：这些宿主是系统进程，但命令行里同时带着
                        //    **我们的目录**与**我们的脚本文件名** —— 两个条件都要，缺一个就可能误杀
                        //    （2026-09-16 实测：只按"命令行里有我们目录"会把正在跑 probe 的 pwsh、
                        //     甚至 DSH 的 node 一起列进来）。
                        bool scriptOurs = (nl == "powershell.exe" || nl == "wscript.exe"
                                           || nl == "cscript.exe" || nl == "cmd.exe")
                                          && clL.IndexOf(rootLower) >= 0;
                        if (scriptOurs)
                        {
                            bool named = false;
                            foreach (string sf in scriptFiles)
                                if (clL.IndexOf(sf.ToLowerInvariant()) >= 0) { named = true; break; }
                            scriptOurs = named;
                        }
                        if (!exeUnderRoot && !wv2Ours && !scriptOurs) continue;   // ← 关键闸门
                        bool hit = exeUnderRoot || wv2Ours || scriptOurs;
                        if (!hit) continue;
                        if (dry)
                        {
                            got.Add("[会结束] " + name + " (pid " + pid + ")");
                            continue;
                        }
                        try
                        {
                            Process p = Process.GetProcessById((int)pid);
                            p.Kill();
                            got.Add(name + " (pid " + pid + ")");
                        }
                        catch (Exception e2)
                        {
                            notes.Add("⚠ 关不掉 " + name + " (pid " + pid + ")：" + e2.GetType().Name);
                        }
                    }
                    catch { }
                }
            }
            catch (Exception e) { notes.Add("⚠ 枚举进程失败：" + e.GetType().Name); }
            return got;
        }

        /// `--probe`：只列会关掉哪些进程，**不动手**（用户/我们排查"为什么关不掉"时用）
        static string Probe(string root)
        {
            var notes = new List<string>();
            string rootLower = root.TrimEnd('\\').ToLowerInvariant();
            string[] first = { "watchdog.py", "onestart.py", "installer.ps1", "setup_python.ps1",
                               "close_all.ps1", "一键启动", "一键关闭" };
            string[] second = { "persona_morph.py", "wx_agent.py", "stop_bot.py" };
            var a = Sweep(rootLower, first, notes, true);
            var b = Sweep(rootLower, second, notes, true);
            var all = new List<string>();
            all.AddRange(a);
            all.AddRange(b);
            all.Add("PROBE 合计 " + all.Count + " 个进程（未动手）");
            all.AddRange(notes);
            return string.Join(Environment.NewLine, all.ToArray());
        }

        /// 谁在监听这个端口（netstat -ano 的最后一段＝pid；查不到返回 0）
        static int PortOwner(int port)
        {
            try
            {
                var psi = new ProcessStartInfo("netstat", "-ano -p tcp");
                psi.UseShellExecute = false; psi.RedirectStandardOutput = true;
                psi.CreateNoWindow = true;
                using (var pr = Process.Start(psi))
                {
                    string outp = pr.StandardOutput.ReadToEnd();
                    pr.WaitForExit(4000);
                    string tag = ":" + port + " ";
                    foreach (string line in outp.Split('\n'))
                    {
                        if (line.IndexOf("LISTENING", StringComparison.OrdinalIgnoreCase) < 0) continue;
                        if (line.IndexOf(tag, StringComparison.Ordinal) < 0) continue;
                        string[] parts = line.Trim().Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                        if (parts.Length == 0) continue;
                        int pid;
                        if (int.TryParse(parts[parts.Length - 1], out pid)) return pid;
                    }
                }
            }
            catch { }
            return 0;
        }

        /// 结果窗：无边框 + 自绘标题栏 + 圆角主按钮（外观全在 StyleKit 一处定义）
        static Form BuildForm(string root, List<string> killed)
        {
            Form f = new Form();
            f.Text = "群相 一键关闭";
            f.StartPosition = FormStartPosition.CenterScreen;
            f.FormBorderStyle = FormBorderStyle.FixedDialog;
            f.MaximizeBox = false; f.MinimizeBox = false;
            f.ClientSize = new Size(400, 210);
            try { string ico = Path.Combine(root, "assets", "app.ico"); if (File.Exists(ico)) f.Icon = Icon.ExtractAssociatedIcon(ico); } catch { }
            PictureBox pic = new PictureBox();
            try { string png = Path.Combine(root, "assets", "app-icon.png"); if (File.Exists(png)) pic.Image = Image.FromFile(png); } catch { }
            pic.SizeMode = PictureBoxSizeMode.Zoom;
            pic.Location = new Point(22, 20); pic.Size = new Size(60, 60);
            f.Controls.Add(pic);
            Label t = new Label();
            t.Text = "群相 一键关闭";
            t.Font = new Font("Microsoft YaHei UI", 13, FontStyle.Bold);
            t.Location = new Point(100, 24); t.AutoSize = true;
            f.Controls.Add(t);
            Label m2 = new Label();
            m2.Text = Environment.NewLine +
                ((killed.Count > 0) ? string.Join(Environment.NewLine, killed) : "没有残留进程（早已关闭）");
            m2.Font = new Font("Microsoft YaHei UI", 9.5f);
            m2.ForeColor = Color.FromArgb(90, 100, 122);
            m2.Location = new Point(100, 60); m2.Size = new Size(270, 92);
            f.Controls.Add(m2);
            RoundButton ok = new RoundButton();
            ok.Text = "好的";
            ok.Size = new Size(120, 34);
            ok.Location = new Point(148, 158);
            ok.BackColor = Color.FromArgb(64, 140, 255);   // 强调色 ⇒ StyleKit 认成主按钮（圆角填充）
            ok.DialogResult = DialogResult.OK;
            f.Controls.Add(ok);
            f.AcceptButton = ok;
            StyleKit.Apply(f, "群相 一键关闭");
            return f;
        }

        /// 离屏渲染取证（不 Show()：Show 会激活窗口抢前台；实现见 StyleKit.CaptureOffscreen）
        static string Shot(string dir, string root)
        {
            var fake = new List<string>(new string[] { "python.exe (pid 4242)", "wx_agent.py (pid 5150)" });
            Form f = BuildForm(root, fake);
            try
            {
                Directory.CreateDirectory(dir);
                return "close " + StyleKit.CaptureOffscreen(f, Path.Combine(dir, "close.png"));
            }
            finally { f.Dispose(); }
        }
    }
}
