// 群相灵 · 统一外观层（StyleKit）+ 自绘控件（RoundBar / RoundButton / StepList）
// 一键启动.exe 与 一键关闭.exe 共用本文件 —— 外观只在这一处定义
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Windows.Forms;

namespace WxLauncher
{
    internal static class StyleKit
    {
        public static readonly Color Bg = Color.FromArgb(247, 249, 252);
        public static readonly Color Card = Color.White;
        public static readonly Color Ink = Color.FromArgb(26, 38, 61);
        public static readonly Color Sub = Color.FromArgb(108, 122, 145);
        public static readonly Color Accent = Color.FromArgb(52, 132, 247);
        public static readonly Color Line = Color.FromArgb(222, 230, 241);
        public static readonly Color ConsoleBg = Color.FromArgb(15, 23, 35);
        public static readonly Color ConsoleInk = Color.FromArgb(206, 220, 236);
        public static readonly Color Ok = Color.FromArgb(52, 150, 90);            // 已完成
        public static readonly Color Muted = Color.FromArgb(150, 158, 172);       // 未开始
        public static readonly Color AccentHi = Color.FromArgb(72, 148, 252);     // 主按钮悬停
        public static readonly Color AccentSoft = Color.FromArgb(232, 240, 254);  // 当前步高亮底

        public static Font Ui(float size, FontStyle fs)
        {
            try { return new Font("Microsoft YaHei UI", size, fs); }
            catch { return new Font(FontFamily.GenericSansSerif, size, fs); }
        }
        public static Font Mono(float size)
        {
            try { return new Font("Consolas", size); }
            catch { return new Font(FontFamily.GenericMonospace, size); }
        }

        [System.Runtime.InteropServices.DllImport("dwmapi.dll")]
        static extern int DwmSetWindowAttribute(IntPtr h, int attr, ref int val, int size);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        static extern bool ReleaseCapture();
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr wp, IntPtr lp);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern int SetThreadDpiAwarenessContext(IntPtr ctx);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern uint SetErrorMode(uint mode);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern IntPtr GetForegroundWindow();
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        static extern int GetWindowLong(IntPtr h, int idx);
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        static extern int SetWindowLong(IntPtr h, int idx, int val);
        const int GWL_EXSTYLE = -20, WS_EX_NOACTIVATE = 0x08000000;

        /// 进程级准备：高 DPI（PerMonitorV2），并关掉系统崩溃/严重错误弹窗（不许弹系统 MessageBox）
        public static void Prep()
        {
            try { SetErrorMode(0x0001 | 0x0002 | 0x8000); } catch { }
            try { SetThreadDpiAwarenessContext((IntPtr)(-4)); } catch { }
            // WebView2 的程序集放在 lib\ 下：.NET 默认不探子目录 ⇒ 自己挂解析（找不到就自然回退浏览器）
            try { AppDomain.CurrentDomain.AssemblyResolve += ResolveFromLib; } catch { }
        }

        static System.Reflection.Assembly ResolveFromLib(object sender, ResolveEventArgs e)
        {
            try
            {
                string dir = Path.GetDirectoryName(Application.ExecutablePath);
                string name = new System.Reflection.AssemblyName(e.Name).Name;
                foreach (string d in new string[] { Path.Combine(dir, "lib"), dir })
                {
                    string p = Path.Combine(d, name + ".dll");
                    if (File.Exists(p)) return System.Reflection.Assembly.LoadFrom(p);
                }
            }
            catch { }
            return null;
        }

        /// 统一外观：圆角（DWM）+ 标题栏配色 + 字体/底色，并把子控件按同一套语言重刷一遍
        public static void Apply(Form f, string title)
        {
            try
            {
                f.Text = title;
                f.BackColor = Bg;
                f.Font = Ui(9.5f, FontStyle.Regular);
                if (f.FormBorderStyle == FormBorderStyle.FixedDialog || f.FormBorderStyle == FormBorderStyle.Sizable)
                {
                    // 去系统标题栏，换成自绘标题栏；既有子控件整体下移 BarH，绝对坐标布局不受影响
                    f.FormBorderStyle = FormBorderStyle.None;
                    f.MinimizeBox = false; f.MaximizeBox = false;
                    f.Padding = new Padding(1);
                    Panel bar = BuildTitleBar(f, title);
                    f.Controls.Add(bar);
                    bar.BringToFront();
                    foreach (Control c in f.Controls)
                    {
                        if (c != bar) c.Top += BarH;
                    }
                    f.ClientSize = new Size(f.ClientSize.Width, f.ClientSize.Height + BarH);
                }
                f.HandleCreated += delegate { Decorate(f); };
                if (f.IsHandleCreated) Decorate(f);
                Restyle(f);
                // 有些窗体在构造函数里靠后还会再设一遍颜色/字体 ⇒ Load 时统一再落一次（幂等）
                f.Load += delegate
                {
                    try
                    {
                        f.BackColor = Bg;
                        f.Font = Ui(9.5f, FontStyle.Regular);
                        Restyle(f);
                        Decorate(f);
                    }
                    catch { }
                };
            }
            catch { }
        }

        static void Decorate(Form f)
        {
            int v;
            try { v = 2; DwmSetWindowAttribute(f.Handle, 33, ref v, 4); } catch { }   // 圆角：DWMWCP_ROUND
            try { v = ColorTranslator.ToWin32(Bg); DwmSetWindowAttribute(f.Handle, 35, ref v, 4); } catch { }  // 标题栏底色
            try { v = ColorTranslator.ToWin32(Ink); DwmSetWindowAttribute(f.Handle, 36, ref v, 4); } catch { }  // 标题文字色
            if (f.FormBorderStyle == FormBorderStyle.None)
            {
                try
                {
                    using (var p = RoundBar.RoundRect(new Rectangle(0, 0, f.Width, f.Height), 12))
                        f.Region = new Region(p);
                }
                catch { }
            }
        }

        internal const int BarH = 38;

        /// 自绘标题栏：标题文字 + 最小化/关闭（拖动靠 Drag）
        static Panel BuildTitleBar(Form f, string title)
        {
            Panel bar = new Panel();
            bar.Height = BarH; bar.Dock = DockStyle.Top; bar.BackColor = Bg;
            bar.MouseDown += delegate { Drag(f.Handle); };
            Label t = new Label();
            t.Text = title;
            t.Font = Ui(9.5f, FontStyle.Bold);
            t.ForeColor = Ink;
            t.AutoSize = true;
            t.Location = new Point(14, 11);
            t.MouseDown += delegate { Drag(f.Handle); };
            bar.Controls.Add(t);
            Button cls = new RoundButton();
            cls.Text = "✕"; cls.Size = new Size(34, 26); cls.FlatStyle = FlatStyle.Flat;
            cls.FlatAppearance.BorderSize = 0; cls.BackColor = Bg; cls.ForeColor = Sub;
            cls.Dock = DockStyle.Right;   // 用 Dock 而不是 Anchor：Anchor 在 Dock 重排后会二次位移
            cls.Click += delegate { f.Close(); };
            bar.Controls.Add(cls);
            return bar;
        }

        /// 拖动无边框窗口（自绘标题栏用）
        public static void Drag(IntPtr h)
        {
            try { ReleaseCapture(); SendMessage(h, 0xA1, (IntPtr)2, IntPtr.Zero); } catch { }
        }

        /// 离屏取证：把窗体真实画面写成 PNG —— 显示但**抢不到前台**、且不像素判据自欺。
        /// 姿势（2026-09-13 四组对照实测得出）：
        ///   ①先 `CreateControl()` 建句柄 → ②给窗口加 `WS_EX_NOACTIVATE` → ③`Show()`（会激活但风格档住了 ⇒ 前台不变）
        ///   → ④`DrawToBitmap`（**只有它在无边框+Region 的窗体上出得来像素**）。
        /// ⚠️ 两条被实测证否的老路：`CreateControl`+`SWP_SHOWWINDOW`（WinForms 不认为窗口 Visible ⇒ 子控件不画，全白图，
        ///    6 张"渲染成功"的截图其实是空白）；`PrintWindow(PW_RENDERFULLCONTENT)` 在这类窗体上也只有底色。
        public static string CaptureOffscreen(Form f, string path)
        {
            IntPtr fg0 = GetForegroundWindow();
            f.StartPosition = FormStartPosition.Manual;
            f.Location = new Point(-4000, -4000);   // 屏外：用户看不到窗口开关
            f.ShowInTaskbar = false;
            f.CreateControl();
            try { SetWindowLong(f.Handle, GWL_EXSTYLE, GetWindowLong(f.Handle, GWL_EXSTYLE) | WS_EX_NOACTIVATE); } catch { }
            f.Show();
            for (int i = 0; i < 16; i++) { Application.DoEvents(); System.Threading.Thread.Sleep(20); }
            int colors;
            using (Bitmap bmp = new Bitmap(Math.Max(1, f.Width), Math.Max(1, f.Height)))
            {
                try { f.DrawToBitmap(bmp, new Rectangle(0, 0, bmp.Width, bmp.Height)); }
                catch { try { using (Graphics g = Graphics.FromImage(bmp)) { IntPtr hdc = g.GetHdc(); PrintWindow(f.Handle, hdc, 2); g.ReleaseHdc(hdc); } } catch { } }
                bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png);
                colors = CountColors(bmp);
            }
            return f.Width + "x" + f.Height + " controls=" + f.Controls.Count
                   + " 前台未变=" + (GetForegroundWindow() == fg0)
                   + " 像素色数=" + colors + (colors <= 2 ? " ⚠️空白图" : "")
                   + " -> " + path;
        }

        /// 抽点统计不同颜色数：≤2 就说明是空白图（出图取证不许"文件存在=成功"）
        static int CountColors(Bitmap b)
        {
            var seen = new System.Collections.Generic.HashSet<int>();
            for (int y = 0; y < b.Height; y += 4)
                for (int x = 0; x < b.Width; x += 4)
                {
                    seen.Add(b.GetPixel(x, y).ToArgb());
                    if (seen.Count > 8) return seen.Count;
                }
            return seen.Count;
        }

        /// 把一棵控件树刷成同一套语言：主按钮用强调色、次按钮描边、日志/文本框用卡片色
        public static void Restyle(Control root)
        {
            if (root == null) return;
            foreach (Control c in root.Controls)
            {
                Button b = c as Button;
                if (b != null)
                {
                    // ⚠️ 主按钮判定必须在覆盖 BackColor **之前**做：老写法先写 Card 再判 Card 的 R/B ⇒ 恒为 false
                    //    （结果是所有按钮都成了白卡片，强调色主按钮从来没生效过）
                    bool accentSet = (b.BackColor.B > 200 && b.BackColor.R < 140 && b.BackColor.G < 200);
                    bool titlebar = (b.Parent is Panel) && (((Panel)b.Parent).Dock == DockStyle.Top);
                    b.FlatStyle = FlatStyle.Flat;
                    b.FlatAppearance.BorderSize = 1;
                    b.FlatAppearance.BorderColor = Line;
                    b.BackColor = Card;
                    b.ForeColor = Ink;
                    b.Font = Ui(9.5f, accentSet ? FontStyle.Bold : FontStyle.Regular);
                    RoundButton rb = b as RoundButton;
                    if (rb != null) { rb.Primary = accentSet && !titlebar; rb.FlatAppearance.BorderSize = 0; }
                    if (accentSet && !titlebar) { b.BackColor = Accent; b.ForeColor = Color.White; b.FlatAppearance.BorderColor = Accent; }
                    if (titlebar)
                    {
                        // 标题栏上的最小化/关闭：无边框、跟标题栏同底色，悬停由 RoundButton 自己画
                        b.FlatAppearance.BorderSize = 0; b.BackColor = Bg; b.ForeColor = Sub;
                        if (rb != null) rb.Primary = false;
                    }
                    else b.Height = Math.Max(b.Height, 34);
                }
                else
                {
                    TextBox tb = c as TextBox;
                    if (tb != null)
                    {
                        tb.BackColor = Card; tb.ForeColor = Ink;
                        if (tb.Multiline)
                        {
                            tb.BorderStyle = BorderStyle.FixedSingle;
                            tb.Font = Mono(9f);
                            tb.BackColor = ConsoleBg;    // 深底浅字：日志区像控制台，字更清楚
                            tb.ForeColor = ConsoleInk;
                        }
                    }
                    else
                    {
                        Label lb = c as Label;
                        if (lb != null && lb.Font != null && lb.Font.Size >= 13f) { lb.ForeColor = Ink; }
                    }
                }
                if (c.HasChildren) Restyle(c);
            }
        }
    }

    /// 自绘圆角进度条：保留 ProgressBar 的 Value/Maximum API，流程代码零改动
    public class RoundBar : ProgressBar
    {
        public RoundBar() { SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true); }
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            Rectangle r = new Rectangle(0, 0, Width - 1, Height - 1);
            using (var bk = new SolidBrush(Color.FromArgb(232, 238, 247)))
            using (var pth = RoundRect(r, Height / 2))
                g.FillPath(bk, pth);
            int max = Maximum > 0 ? Maximum : 100;
            int w = (int)Math.Round((Width - 2) * (Math.Min(Value, max) / (double)max));
            if (w > 2)
            {
                using (var fg = new SolidBrush(StyleKit.Accent))
                using (var pth = RoundRect(new Rectangle(1, 1, Math.Max(2, w - 2), Height - 3), (Height - 3) / 2))
                    g.FillPath(fg, pth);
            }
        }
        internal static System.Drawing.Drawing2D.GraphicsPath RoundRect(Rectangle r, int radius)
        {
            var p = new System.Drawing.Drawing2D.GraphicsPath();
            int d = Math.Max(2, radius * 2);
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }
    }

    /// 自绘圆角按钮：主按钮＝强调色填充（悬停变亮）· 次按钮＝卡片底+描边 · 标题栏按钮＝无边框（悬停淡灰）
    /// ⚠️ 主按钮身份由 StyleKit.Restyle 在覆盖底色之前判定并写进 Primary，别在窗体里手设颜色来"表示主按钮"
    public class RoundButton : Button
    {
        public bool Primary = false;
        public int Radius = 8;
        bool _hover;
        public RoundButton()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            BackColor = StyleKit.Bg;
            Font = StyleKit.Ui(9.5f, FontStyle.Regular);
            Cursor = Cursors.Hand;
        }
        bool InTitleBar { get { return (Parent is Panel) && (((Panel)Parent).Dock == DockStyle.Top); } }
        protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { _hover = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnEnabledChanged(EventArgs e) { Invalidate(); base.OnEnabledChanged(e); }
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            Color back = (Parent != null) ? Parent.BackColor : StyleKit.Bg;
            using (var bb = new SolidBrush(back)) g.FillRectangle(bb, ClientRectangle);
            bool tb = InTitleBar;
            Color fill, ink, border;
            if (!Enabled) { fill = Color.FromArgb(238, 242, 248); ink = StyleKit.Muted; border = StyleKit.Line; }
            else if (tb) { fill = _hover ? Color.FromArgb(230, 235, 243) : back; ink = _hover ? StyleKit.Ink : StyleKit.Sub; border = fill; }
            else if (Primary) { fill = _hover ? StyleKit.AccentHi : StyleKit.Accent; ink = Color.White; border = fill; }
            else { fill = _hover ? Color.FromArgb(240, 245, 253) : StyleKit.Card; ink = StyleKit.Ink; border = _hover ? StyleKit.AccentSoft : StyleKit.Line; }
            Rectangle r = new Rectangle(0, 0, Width - 1, Height - 1);
            using (var pth = RoundBar.RoundRect(r, Radius))
            using (var fb = new SolidBrush(fill)) g.FillPath(fb, pth);
            if (!tb)
                using (var pth = RoundBar.RoundRect(r, Radius))
                using (var pb = new Pen(border)) g.DrawPath(pb, pth);
            TextRenderer.DrawText(g, Text, Font, r, ink,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding);
        }
    }

    /// 自绘步骤列表（替代 4 个裸 Label）：编号徽章（待办灰/进行蓝/完成绿勾）+ 连接线 + 当前行高亮底
    /// 对外只暴露 SetSteps / SetProgress，流程代码的"第几步"语义不变
    public class StepList : Control
    {
        string[] _steps = new string[0];
        int _idx = -1;   // -1 = 还没开始
        public StepList()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            BackColor = StyleKit.Bg;
            Font = StyleKit.Ui(9.5f, FontStyle.Regular);
        }
        public void SetSteps(string[] steps) { _steps = (steps == null) ? new string[0] : steps; Invalidate(); }
        public void SetProgress(int stepIdx) { _idx = stepIdx; Invalidate(); }
        /// 覆盖 Text：自绘控件默认 Text 是空串，--dlgprobe 这类机械判据要靠它读到"当前第几步"
        public override string Text
        {
            get
            {
                if (_steps.Length == 0) return "步骤列表 空";
                string cur = (_idx >= 0 && _idx < _steps.Length) ? _steps[_idx] : "未开始";
                return "步骤列表 " + (_idx + 1) + "/" + _steps.Length + "：" + cur;
            }
            set { }
        }
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            int n = _steps.Length;
            if (n == 0 || Height < 8) return;
            int rowH = Math.Max(24, Height / n);
            int cx = 15;
            using (var linePen = new Pen(StyleKit.Line, 2f))
            using (var okBrush = new SolidBrush(StyleKit.Ok))
            using (var acBrush = new SolidBrush(StyleKit.Accent))
            using (var cardBrush = new SolidBrush(StyleKit.Card))
            using (var softBrush = new SolidBrush(StyleKit.AccentSoft))
            using (var softPen = new Pen(StyleKit.Line, 1.5f))
            using (var tickPen = new Pen(Color.White, 2f))
            {
                for (int i = 0; i < n; i++)
                {
                    int cy = i * rowH + rowH / 2;
                    bool done = (i < _idx), cur = (i == _idx);
                    if (i < n - 1) g.DrawLine(linePen, cx, cy + 11, cx, cy + rowH - 11);
                    if (cur)
                        using (var pill = RoundBar.RoundRect(new Rectangle(2, cy - rowH / 2 + 2, Width - 4, rowH - 4), 8))
                            g.FillPath(softBrush, pill);
                    Rectangle box = new Rectangle(cx - 10, cy - 10, 20, 20);
                    if (done)
                    {
                        g.FillEllipse(okBrush, box);
                        g.DrawLines(tickPen, new Point[] { new Point(cx - 5, cy), new Point(cx - 1, cy + 4), new Point(cx + 5, cy - 4) });
                    }
                    else
                    {
                        if (cur) g.FillEllipse(acBrush, box);
                        else { g.FillEllipse(cardBrush, box); g.DrawEllipse(softPen, box); }
                        TextRenderer.DrawText(g, (i + 1).ToString(), StyleKit.Ui(8f, FontStyle.Bold), box,
                            cur ? Color.White : StyleKit.Muted,
                            TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding);
                    }
                    Color ink = done ? StyleKit.Ink : (cur ? StyleKit.Accent : StyleKit.Muted);
                    Font f = cur ? StyleKit.Ui(9.5f, FontStyle.Bold) : Font;
                    TextRenderer.DrawText(g, _steps[i], f,
                        new Rectangle(cx + 20, cy - rowH / 2, Math.Max(10, Width - cx - 22), rowH), ink,
                        TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding | TextFormatFlags.EndEllipsis);
                }
            }
        }
    }
}
