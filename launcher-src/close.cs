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

            // 取证入口：把结果窗离屏渲染成 PNG（不显示、不抢焦点），与被关掉的进程无关。
            // ⚠️ 2026-09-23（#18）：可选第三参 = 字号倍率（等效 DPI 模拟，见 StyleKit.FontScale）——
            //   本机系统 DPI 恒 144（150%），`--shot <dir> 0.667` 出等效 100% 图、`0.833` 出 125% 图、
            //   省略时 = 本机原生 150%。
            if (args != null && args.Length > 1 && args[0] == "--shot")
            {
                try { Console.OutputEncoding = System.Text.Encoding.UTF8; } catch { }
                float scale = 1f;
                if (args.Length > 2)
                {
                    try { scale = float.Parse(args[2], System.Globalization.CultureInfo.InvariantCulture); } catch { }
                    if (scale <= 0.1f) scale = 1f;
                }
                // ⛔ 2026-09-23（#18）：失败要**如实说原因**（项目纪律）——原来这里任何异常都静默死掉
                //   （winexe 没控制台，Console.WriteLine 也看不到），取证的人只看到"没图"。
                //   异常全文落盘 <dir>\close-shot.err，成功时把返回串也存 <dir>\close-shot.out。
                try
                {
                    string ret = Shot(args[1], root, scale);
                    try { File.WriteAllText(Path.Combine(args[1], "close-shot.out"), ret + Environment.NewLine, System.Text.Encoding.UTF8); } catch { }
                    Console.WriteLine(ret);
                }
                catch (Exception ex)
                {
                    string dump = "--shot FAILED: " + ex.GetType().Name + ": " + ex.Message + Environment.NewLine
                                + (ex.StackTrace ?? "");
                    try { Directory.CreateDirectory(args[1]); File.WriteAllText(Path.Combine(args[1], "close-shot.err"), dump, System.Text.Encoding.UTF8); } catch { }
                    Console.WriteLine(dump);
                }
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
            // 2026-09-23：400×210 → 520×392（卡片化 + 把"关掉了什么"分区列清楚）
            //   ⚠️ 这个高度是**标题栏以下**的设计高度（`Apply` 再 + BarH 给窗口长高，见 stylekit.cs）
            f.ClientSize = new Size(520, 392);
            try { string ico = Path.Combine(root, "assets", "app.ico"); if (File.Exists(ico)) f.Icon = Icon.ExtractAssociatedIcon(ico); } catch { }

            StyleKit.MakeIcon(f, root, new Point(StyleKit.Space.x6, StyleKit.Space.x5));

            Label t = new Label();
            t.Text = "群相 一键关闭";
            t.Font = StyleKit.Ui(StyleKit.TextScale.Title, FontStyle.Bold);
            t.ForeColor = StyleKit.Ink;
            t.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4, StyleKit.Space.x5 + 2);
            t.AutoSize = true;
            f.Controls.Add(t);

            // 状态：没有任何"⚠"开头 / "没关掉"的行 ⇒ 才算干净
            bool clean = true;
            foreach (string s in killed)
            {
                if (s != null && (s.StartsWith("⚠") || s.IndexOf("没关掉") >= 0 || s.IndexOf("未关干净") >= 0))
                {
                    clean = false; break;
                }
            }
            Label st = new Label();
            st.Text = clean ? "已全部结束" : "有项目没关干净";
            st.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            st.ForeColor = clean ? StyleKit.Ok : StyleKit.Warn;
            // ⚠️ 2026-09-23（#18 叠字根治）：副标题 y 原写死 `Space.x5 + 30` —— 那是按 100% DPI 的
            //   标题字高（27px）配的；150% 下 Title(15f) 实高 40px ⇒ 副标题叠进标题里
            //   （用户截图「其他弹窗全是这样」的现场）。改成**跟着标题的实测底边走**
            //   （AutoSize 标签的 Height 由字体真实量出），任何 DPI 下都刚好在标题下一行。
            st.Location = new Point(StyleKit.Space.x6 + StyleKit.IconSize + StyleKit.Space.x4,
                                    t.Bottom + StyleKit.Space.x1);
            st.AutoSize = true;
            f.Controls.Add(st);

            int cardW = 520 - StyleKit.Space.x6 * 2;
            // #12 F1：高度不再手写 226 —— 150% 下卡内「结束明细」标题、日志区都按大字体排，
            //   写死的卡高会把日志区下半截切掉。只给宽度，排完 `SealCard` 收口（launcher.cs 同构）。
            CardPanel card = StyleKit.MakeCard(f, new Point(StyleKit.Space.x6, StyleKit.CardTopY), cardW);

            Label cap = new Label();
            cap.Text = "结束明细";
            cap.Font = StyleKit.Ui(StyleKit.TextScale.Head, FontStyle.Bold);
            cap.ForeColor = StyleKit.Ink;
            cap.Location = new Point(StyleKit.Space.x5, StyleKit.Space.x4);
            cap.AutoSize = true;
            card.Controls.Add(cap);

            // 自绘日志（原来这里是系统 TextBox：等宽字 + 常驻滚动条，与全窗两套语言）
            // `LogView` 是顶层类（stylekit.cs），不带 `StyleKit.` 前缀。2026-09-23 修 CS0426。
            LogView lv = new LogView();
            // ⚠️ 2026-09-23（#18 叠字根治）：y 原写死 `Space.x4 + 28`（100% 的标题高 22 + 6 间距）——
            //   150% 下 cap 实高 33px ⇒ 日志区顶边压进标题。改**跟着 cap 的实测底边走**。
            //   高度也不再写死 178：按"8 行 × 当前行高 + 上下内边距"算（等效 100% 下的原设计 178），
            //   高 DPI 下日志可见行数不缩水。
            int lvH = Math.Max(178, StyleKit.LineHeight(lv.Font) * 8 + 16);
            lv.Location = new Point(StyleKit.Space.x5, cap.Bottom + StyleKit.Space.x2);
            lv.Size = new Size(cardW - StyleKit.Space.x5 * 2, lvH);
            card.Controls.Add(lv);
            lv.SetLines(killed.Count > 0
                ? killed.ToArray()
                : new string[] { "没有残留进程 —— 群相 早已关闭，不用再关一次。" });
            StyleKit.SealCard(card);   // #18：卡高到这里才定（日志区、标题都按实测）

            // #13 F2：宽度改走 `MakeButton`（＝文字实宽 + 36，原来手写 132），
            //   y 跟着卡片实测底边走（原来写死 346 —— 卡一长高按钮就压在卡上）。
            RoundButton ok = StyleKit.MakeButton("好的");
            ok.Location = new Point(520 - StyleKit.Space.x6 - ok.Width, card.Bottom + StyleKit.CardGapY);
            ok.BackColor = Color.FromArgb(64, 140, 255);   // 强调色 ⇒ StyleKit 认成主按钮（圆角填充）
            ok.ForeColor = Color.White;
            ok.DialogResult = DialogResult.OK;
            f.Controls.Add(ok);
            f.AcceptButton = ok;
            // #18：窗高由最后一行的底边反推（原来写死 392 —— 内容按实测排下来 100% 下正好也是这个数，
            //   高 DPI 下内容变高 ⇒ 窗口跟着长高，不再切字）。
            f.ClientSize = new Size(520, ok.Bottom + StyleKit.Space.x6);
            StyleKit.Apply(f, "群相 一键关闭");
            return f;
        }

        /// 离屏渲染取证（不 Show()：Show 会激活窗口抢前台；实现见 StyleKit.CaptureOffscreen）
        /// ⚠️ 2026-09-23（#18）：加 scale 参数 —— 等效 DPI 模拟（见 StyleKit.FontScale 与 Main 的 --shot 注释）。
        static string Shot(string dir, string root, float scale)
        {
            var fake = new List<string>(new string[] { "python.exe (pid 4242)", "wx_agent.py (pid 5150)" });
            float saved = StyleKit.FontScale;
            try
            {
                StyleKit.FontScale = scale;
                Form f = BuildForm(root, fake);
                try
                {
                    Directory.CreateDirectory(dir);
                    return "close " + StyleKit.CaptureOffscreen(f, Path.Combine(dir, "close.png"));
                }
                finally { f.Dispose(); }
            }
            finally { StyleKit.FontScale = saved; }
        }
    }
}
