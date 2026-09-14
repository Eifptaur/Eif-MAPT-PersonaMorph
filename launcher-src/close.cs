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

            List<string> killed = KillAll(root);
            using (Form f = BuildForm(root, killed))
                f.ShowDialog();
        }

        /// 结束匹配 markers 的 python/powershell/wscript/一键启动/一键关闭 进程，并清掉启动锁 + 释放端口
        static List<string> KillAll(string root)
        {
            var killed = new List<string>();
            // ⛔ 入口脚本改名记录：老入口 `wx_agent.py` 已不存在（只剩 .pyc 残骸），
            //    现行主流程是 `scripts\persona_morph.py`、由 `scripts\watchdog.py` 拉起。
            //    2026-09-14 另一台机器实测：markers 里还写着 wx_agent.py ⇒ 看门狗被杀、**子进程 persona_morph 活着**
            //    ⇒ 控制台（3210）照旧在跑，再点一键启动就弹「为保持唯一…本次不再重复打开」。
            string[] markers = { "onestart.py", "installer.ps1", "setup_python.ps1",
                                 "persona_morph.py", "wx_agent.py", "watchdog.py", "stop_bot.py", "close_all.ps1",
                                 "一键关闭", "一键启动" };
            try
            {
                var searcher = new ManagementObjectSearcher(
                    "SELECT ProcessId, Name, CommandLine FROM Win32_Process");
                foreach (ManagementObject o in searcher.Get())
                {
                    try
                    {
                        uint pid = (uint)o["ProcessId"];
                        if (pid == (uint)Process.GetCurrentProcess().Id) continue;
                        string name = Convert.ToString(o["Name"]);
                        string cl = Convert.ToString(o["CommandLine"]);
                        if (string.IsNullOrEmpty(cl)) continue;
                        if (name.IndexOf("python", StringComparison.OrdinalIgnoreCase) < 0 &&
                            name.IndexOf("powershell", StringComparison.OrdinalIgnoreCase) < 0 &&
                            name.IndexOf("wscript", StringComparison.OrdinalIgnoreCase) < 0 &&
                            name.IndexOf("一键启动", StringComparison.OrdinalIgnoreCase) < 0 &&
                            name.IndexOf("一键关闭", StringComparison.OrdinalIgnoreCase) < 0) continue;
                        bool hit = false;
                        foreach (string m in markers)
                            if (cl.IndexOf(m, StringComparison.OrdinalIgnoreCase) >= 0) { hit = true; break; }
                        if (!hit) continue;
                        try
                        {
                            Process p = Process.GetProcessById((int)pid);
                            p.Kill();
                            killed.Add(name + " (pid " + pid + ")");
                        }
                        catch { }
                    }
                    catch { }
                }
            }
            catch { }
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
            return killed;
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
