// 群相灵 一键关闭.exe：结束机器人/看门狗/启动器等全部相关进程 + 清理启动锁（自定义结果窗）
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

        /// 结束匹配 markers 的 python/powershell/wscript/一键启动/一键关闭 进程，并清掉启动锁
        static List<string> KillAll(string root)
        {
            var killed = new List<string>();
            string[] markers = { "onestart.py", "installer.ps1", "setup_python.ps1",
                                 "wx_agent.py", "watchdog.py", "stop_bot.py", "close_all.ps1",
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
            return killed;
        }

        /// 结果窗：无边框 + 自绘标题栏 + 圆角主按钮（外观全在 StyleKit 一处定义）
        static Form BuildForm(string root, List<string> killed)
        {
            Form f = new Form();
            f.Text = "群相灵 一键关闭";
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
            t.Text = "群相灵 一键关闭";
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
            StyleKit.Apply(f, "群相灵 一键关闭");
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
