// 群相灵 一键关闭.exe：结束机器人/看门狗/启动器等全部相关进程 + 清理启动锁（自定义结果窗）
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace WxCloser
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string root = Path.GetDirectoryName(Application.ExecutablePath);
            var killed = new System.Collections.Generic.List<string>();
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

            // 结果窗（自定义 UI）
            Form f = new Form();
            f.Text = "群相灵 一键关闭";
            f.StartPosition = FormStartPosition.CenterScreen;
            f.FormBorderStyle = FormBorderStyle.FixedDialog;
            f.MaximizeBox = false; f.MinimizeBox = false;
            f.BackColor = Color.FromArgb(246, 248, 252);
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
            Button ok = new Button();
            ok.Text = "好的";
            ok.Size = new Size(120, 34);
            ok.Location = new Point(148, 158);
            ok.FlatStyle = FlatStyle.Flat;
            ok.BackColor = Color.FromArgb(64, 140, 255);
            ok.ForeColor = Color.White;
            ok.DialogResult = DialogResult.OK;
            f.Controls.Add(ok);
            f.AcceptButton = ok;
            f.ShowDialog();
        }
    }
}
