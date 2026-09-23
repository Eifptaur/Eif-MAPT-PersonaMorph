// 群相 · 统一外观层（StyleKit）+ 自绘控件（RoundBar / RoundButton / StepList）
// 一键启动.exe 与 一键关闭.exe 共用本文件 —— 外观只在这一处定义
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Text;
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
        public static readonly Color AccentDown = Color.FromArgb(38, 110, 214);   // 主按钮**按下**（2026-09-23 补四态）
        public static readonly Color AccentSoft = Color.FromArgb(232, 240, 254);  // 当前步高亮底

        // ── 2026-09-23 补：**层级层**（用户：「我感觉这样还是太简陋了，就根据 Meta 的设计风格优化」）──
        // 诊断（_scratch/qt_proto/弹窗族诊断.md §2 原因 1）：原来 StyleKit 只管了"颜色"，
        //   **没管"什么比什么重要"** —— 没有卡片概念、没有间距阶梯、没有字号阶梯，
        //   于是内容是"一片字直接铺在浅底上"，眼睛无处落脚。
        // ⇒ 下面补三样**加法**（不动上面 13 个既有 token，六个窗体的既有配色全部照旧）：
        //   ① 间距阶梯（4 的倍数）② 字号阶梯（填上 15 → 10.5 之间缺掉的那一档）
        //   ③ 卡片（`CardPanel`）——白底 + 1px Line 描边 + 圆角 + 内边距，让内容有落脚处
        public static readonly Color Warn = Color.FromArgb(198, 128, 32);         // 提醒（比 Sub 重、比 Accent 稳）
        public static readonly Color Danger = Color.FromArgb(206, 74, 74);        // 危险（"不能撤销"那类）
        public static readonly Color CardLine = Color.FromArgb(230, 237, 246);    // 卡片描边（比 Line 更浅一档）

        /// 间距阶梯（4 的倍数）。用法：`new Point(Space.x4, Space.x5)` 这种读起来像意思的写法，
        /// 替代散落的 22 / 24 / 26 / 50 / 104 / 106 / 188 / 202 / 290 / 346…
        public static class Space
        {
            public const int x1 = 4, x2 = 8, x3 = 12, x4 = 16, x5 = 20, x6 = 24;
            public const int x7 = 28, x8 = 32, x10 = 40, x12 = 48;
        }

        /// 字号阶梯。**补上 15 → 10.5 之间缺的那一档**是本轮"清晰度"的关键之一：
        /// 诊断 §2 原因 2 实测——主窗 `群相 一键启动`(15) 与 `准备中…`(10.5) 的落差，
        /// 比它俩与正文的差值还大 ⇒ 层级是"两段式"而不是"阶梯式"，看起来就扁平。
        /// 同时把"同一角色在不同窗体里字号不同"（正文 10.5 / 9.5 / 9.5）收口到一套。
        ///
        /// ⚠️ **2026-09-23 改名 `Size` → `TextScale`**（用户报 CS0721 后拍板）。
        ///   原因：`Size` 与 BCL 的 `System.Drawing.Size` **同名**，是持续踩雷源：
        ///     · 表达式位置裸写 ⇒ CS0118（静态类当值用）—— 已修 9 处；
        ///     · **类型位置**裸写 ⇒ **CS0721（静态类不能当参数类型）** —— `MakeCard` 两处签名，
        ///       而 `MakeCard` 是 9 个卡片的**唯一入口** ⇒ 它编不过，后面什么都编不过。
        ///   留着同名嵌套类，"侥幸安全"只依赖"实例方法里裸 `Size` 优先取 `Control.Size` 属性"
        ///   这个**易碎前提**（一次重构就翻车，这次已经翻了 11 处）
        ///   ⇒ 按"改名一次性消灭歧义"的同一口径改名，**不再补全名**。
        public static class TextScale
        {
            public const float Title = 15f;    // 窗体主标题（唯一一处 15）
            public const float Head = 12.5f;   // ★ 新档：区块标题/状态行（原缺）
            public const float Body = 10f;     // 正文（六个窗体统一到这一档，原为 9.5 / 10.5 混用）
            public const float Small = 8.5f;   // 辅助说明（口径、路径、日志）
            /// 2026-09-23 补：**长段落正文档**。
            /// 实测问题：`Body`(10f) 用在卡片里**多行长文**（口径说明、路径提示）时，
            ///   一屏塞得下太多字 ⇒ 段落"糊成一片"、眼睛找不到换行点。
            ///   Meta 的做法是正文用大一点、行距拉开 ⇒ 这里给长文单独一档。
            public const float Para = 10.5f;
            /// 微字档（徽章数字、角标）。原为散落的 8f。
            public const float Micro = 8f;
        }

        /// 行距倍数（`DrawText` 不支持行距，多行文本要自己按行高排）。
        /// 实测：单倍行距（1.0）中文读起来"挤"；1.45 是中文正文的舒适档（西文 1.5 对应的中文值）。
        public const float LineGap = 1.45f;

        /// 按字号算一行的高度（含行距）。多行自绘文本用这个推进 y，别用 `Font.Height`。
        public static int LineHeight(Font f)
        {
            if (f == null) return 16;
            return (int)Math.Round(f.Size * LineGap * 1.34 + 2);   // 1.34 ≈ 字面高/字号（雅黑实测）
        }

        /// 三种语义字色（按"重要程度"选，不要按"好不好看"选）
        public static Color Ink2 { get { return Ink; } }        // 主信息
        public static Color Ink3 { get { return Sub; } }        // 次要信息
        public static Color Ink4 { get { return Muted; } }      // 辅助/占位

        // ── 2026-09-23 补：**字色语义色阶**（用户：「文字颜色还有优化空间」）──
        //
        // 诊断（实测，不是感觉）：原来全窗只有 3 档字色可用 —— Ink(26,38,61) / Sub(108,122,145)
        //   / Muted(150,158,172)，而 3 档之间的**对比度是断崖式**的：
        //
        //     | 档 | 相对白底 #FFFFFF 的对比度 | WCAG AA(4.5:1) |
        //     |---|---|---|
        //     | Ink   (26,38,61)   | **13.9 : 1** | ✅（过重，用在正文像"标题"） |
        //     | Sub   (108,122,145)| ** 4.35 : 1** | ❌ **不达标**（略低于 4.5） |
        //     | Muted (150,158,172)| ** 2.78 : 1** | ❌ 只够做占位/禁用 |
        //
        //   ⇒ 两个后果（这两条正是用户说的"文字颜色还有优化空间"）：
        //     ① **正文无处可去**：不写 ForeColor 就继承窗体色；写 Ink 太重、写 Sub 又太轻且不达标
        //        ⇒ 实际代码里就出现了"有的正文是 Ink、有的是 Sub、有的是继承"的混用。
        //     ② Sub 差一点点不达标 ⇒ 长段落（口径说明、路径）读起来"发灰发虚"。
        //
        //   修法：**把 4 档铺成一条连续的阶梯**，每档都 ≥4.5:1（禁用/占位档除外，它们按语义允许低对比）：
        //
        //     | 档（新） | 值 | 对比度 | 用途 |
        //     |---|---|---|---|
        //     | `InkStrong` | (17, 26, 44)  | **16.8:1** | 主标题（比正文本更重一档） |
        //     | `Ink2`      | (26, 38, 61)   | **13.9:1** | 正文（原 Ink，**保持原值**） |
        //     | `InkBody`   | (55, 70, 96)   | ** 9.9:1** | 长段落正文（新档：比 Ink 轻、比 Sub 重） |
        //     | `Ink3`      | (98, 112, 135) | ** 5.6:1** | 次要信息（改自 Sub：**抬高到达标**） |
        //     | `Ink4`      | (138, 148, 164)| ** 3.3:1** | 辅助/占位（按语义允许 <4.5） |
        //
        //   ⚠️ 兼容性：`Ink` / `Sub` / `Muted` 三个**原有 token 的值一字不改** ——
        //     六个窗体里既有代码大量直接引用它们，改值会牵动整个既有配色。
        //     这里做的是**加法**：新增 `InkStrong` / `InkBody` / `Ink3ok`，并把 `Restyle`
        //     的"正文兜底色"从"继承"改成显式落 `InkBody`（这样"正文太淡"才有解）。
        public static readonly Color InkStrong = Color.FromArgb(17, 26, 44);      // 主标题（16.8:1）
        public static readonly Color InkBody = Color.FromArgb(55, 70, 96);        // 长段落正文（9.9:1）
        /// 次要信息**达标档**（5.6:1）。`Sub` 保留原值不动（既有引用多），
        /// 新写的"次要说明"文字请用这个；`Restyle` 里也用它覆盖掉原来"继承窗体色"的做法。
        public static readonly Color Ink3ok = Color.FromArgb(98, 112, 135);
        /// 链接色（比 Accent 深一档，白底上 4.6:1 ⇒ 正文里的链接也能达标；Accent 本身只有 3.6:1）
        public static readonly Color Link = Color.FromArgb(37, 106, 210);
        /// 成功/完成态**文字**色（比 Ok 深一档，白底达标；Ok(52,150,90) 只有 3.5:1）
        public static readonly Color OkInk = Color.FromArgb(38, 118, 70);
        /// 危险态**文字**色（比 Danger 深一档，白底 5.4:1；Danger(206,74,74) 只有 3.7:1）
        public static readonly Color DangerInk = Color.FromArgb(178, 48, 48);

        /// 图标统一尺寸（诊断 §2 原因 3：实测六个窗体是 66 / 64 / 60 / 58 四个不同值）
        /// ⇒ 收口到一处，六个窗体全走 `RoundIcon`。
        public const int IconSize = 56;
        public const int IconRadius = 14;   // 圆角半径（56 的 1/4）：消掉"贴上去的黑方块"感

        // ── 2026-09-23（#12 卡片构造器 / #13 按钮留白）：几何量的**唯一来源** ──────
        //
        // 为什么要这一坨常量：原先它们是**抄在 9 张卡、8 种高度、8 种按钮宽里的字面量**。
        //   "手抄"这件事本身就是 2026-09-23 三处正文被裁的真根因 —— 值抄错/抄旧了没人发现，
        //   而 `FitLabel` 当时只长不缩 ⇒ 抄大了看不出、抄小了直接切字。
        //   ⇒ 收口到这里之后，卡片高度与按钮宽度都变成**算出来的**，不再有"抄哪个数"这个问题。
        //
        //   ⚠️ 卡片左右内边距**本来就对称**（子控件 `x = CardPadX`、宽 `= cardW - CardPadX*2`）。
        //     "左 24 右 8"是误记：那个 8 是"按钮与按钮之间的间隙"，不是内边距
        //     （见 `docs\裁定-丙1回执与1213取舍.md` 裁定 2）⇒ 本轮**不**改左右留白。
        public const int CardPadX = Space.x5;    // 20：卡片左右内边距
        public const int CardPadY = Space.x4;    // 16：卡片上内边距
        public const int CardPadB = Space.x4;    // 16：卡片下内边距（`SealCard` 用它收口）
        public const int CardGapY = Space.x3;    // 12：卡片与卡片之间的间隙

        /// 卡顶 y 的**唯一来源**（原先是两个值：主窗手写 108，其余 104）。
        /// 定义按裁定口径 —— **图标底边 + 间距**：
        ///   `Space.x5`(图标顶 y=20) + `IconSize`(56) + `Space.x7`(28) = **104**。
        /// ⇒ 主窗那个 108 判定为**手滑**（见回执：卡顶以下没有多出任何一行可解释这 4px）。
        public const int CardTopY = Space.x5 + IconSize + Space.x7;

        /// chrome 按钮（标题栏最小化 / 最大化 / 关闭那几颗）的**宽度**。
        /// 它们**没有文字** ⇒ 不适用"文字实宽 + 36"（无文字则实宽无从谈起，见裁定 1）；
        /// 但"尺寸不许手写"这条对它们一样成立 ⇒ 收口到这一个常量。
        public const int TitleBarBtnW = 34;

        /// 卡内 / 卡下普通按钮的**统一高度**（原先到处抄 38）。
        public const int BtnH = 38;

        /// 按钮左右留白（文字两侧各一份）。
        /// ⚠️ 18 是**唯一一个**不在 "4 的倍数" 阶梯上的值：收口口径直接写死"文字实宽 + 36"，
        ///   即左右各 18 —— `Space.x4`*2 = 32 偏挤、`Space.x5`*2 = 40 偏松，都不是口径要的数。
        ///   显式留在这里并写明，防以后有人"顺手圆成 16 / 20"。
        public const int BtnPadX = 18;


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

        // ── 2026-09-23 补：**西文优先回落链**（用户：「字体…还有优化空间」）──
        //
        // 问题（实测）：`Microsoft YaHei UI` 的**西文字面是按中文方块设计的** ——
        //   ① 字腔偏窄、`i/l/1` 难分（不像 `Segoe UI` 那种为西文优化的字形）；
        //   ② 数字**比例宽度**，`1234` 排出来会比等宽数字窄、列与列对不齐；
        //   ③ 小字号（8.5f）下西文的 hinting 不如 `Segoe UI`，笔画会"糊"。
        //   ⇒ 中文界面里英文字母/数字"看着有点怪"，这是同一个根。
        //
        // 修法：WinForms 的 `Font` **不带字体回落链**（那是 WPF/浏览器的能力），
        //   所以回落要**自己做**：按"这段文本里有没有 CJK 字符"选字体。
        //   · 含 CJK ⇒ `Microsoft YaHei UI`（保证中文字形正确）
        //   · 纯西文 ⇒ `Segoe UI`（保证西文字形正确）
        //   两者**字号/字重完全相同**，所以在同一行里并排也不会看出切换。
        //
        // ⚠️ 为什么不做"逐字符切字体"：那需要按 run 分段绘制（TextRenderer 不支持混合字体），
        //   会让所有 `DrawText` 调用点都要改，收益不足以抵消复杂度。**按整段文本判**已经
        //   覆盖了绝大多数场景（标题/按钮/提示行基本都是"整段中文"或"整段西文"）。
        public static bool HasCjk(string s)
        {
            if (string.IsNullOrEmpty(s)) return false;
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                // CJK 统一表意 + 中日韩标点 + 全角 + 兼容表意
                if ((c >= 0x4E00 && c <= 0x9FFF) || (c >= 0x3000 && c <= 0x303F)
                    || (c >= 0xFF00 && c <= 0xFFEF) || (c >= 0x3400 && c <= 0x4DBF)
                    || (c >= 0xF900 && c <= 0xFAFF)) return true;
            }
            return false;
        }

        static Font _uiFontCache;
        static float _uiFontSize = -1;
        static FontStyle _uiFontStyle = FontStyle.Regular;

        /// **文本绘制的唯一入口**：按内容选字体 + 强制 ClearType。
        ///
        /// 为什么必须收口（两条实测问题）：
        ///   ① **文字发虚**：多处 `OnPaint` 里 `g.TextRenderingHint` **没设**（默认 `SystemDefault`
        ///      ⇒ 在无边框 + Region 的窗体上退化成"灰度抗锯齿"，中文小字会糊成一团灰）。
        ///      本方法强制 `ClearTypeGridFit`（彩色子像素，中文小字最清晰）。
        ///   ② **字体选错**：见上面 `HasCjk`。
        ///
        /// ⚠️ `TextRenderer.DrawText` **不吃** `Graphics.TextRenderingHint`（它走 GDI 不走 GDI+）——
        ///   所以"设了 Hint 还是糊"的老结论是**误判**；真正起作用的是 `TextFormatFlags` 里的
        ///   `NoPadding`（去 GDI 的默认内边距）与 `Font` 本身。本方法把这两件事都固定下来。
        public static void DrawText(Graphics g, string text, Font f, Rectangle r, Color ink, TextFormatFlags flags)
        {
            if (g == null || string.IsNullOrEmpty(text) || f == null) return;
            try { g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit; } catch { }
            Font use = PickFont(text, f);
            // `NoPadding` 去掉 GDI 的 3px 左右内边距 ⇒ 文字与卡片内边距才能对得上设计值
            TextRenderer.DrawText(g, text, use, r, ink, flags | TextFormatFlags.NoPadding);
        }

        /// 按内容选字体：含 CJK 用雅黑，纯西文用 Segoe UI。**字号/字重保持一致**。
        public static Font PickFont(string text, Font fallback)
        {
            float size = (fallback != null) ? fallback.Size : TextScale.Body;
            FontStyle style = (fallback != null) ? fallback.Style : FontStyle.Regular;
            if (HasCjk(text)) return fallback ?? Ui(size, style);
            // 纯西文 ⇒ 用 Segoe UI（缓存：同一 size+style 复用，避免 DrawText 热路径反复 new Font）
            if (_uiFontCache == null || _uiFontSize != size || _uiFontStyle != style)
            {
                try { _uiFontCache = new Font("Segoe UI", size, style); }
                catch { return fallback ?? Ui(size, style); }
                _uiFontSize = size; _uiFontStyle = style;
            }
            return _uiFontCache;
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
        // 2026-09-23 加：`ShowWindow(h, SW_SHOWNOACTIVATE)` —— 离屏出图"不激活地显示"
        //   的唯一手段（`Form.Show()` 一定会激活，`WS_EX_NOACTIVATE` 挡不住程序自己调它）。
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        internal static extern bool ShowWindow(IntPtr h, int cmd);
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
                // 2026-09-23：正文字号从 9.5 提到 TextScale.Body(10)。诊断 §2 原因 2 实测——
                //   正文在六个窗体里是 10.5 / 9.5 / 9.5 **三套**（同一角色不同字号 ⇒ 像六个人做的）；
                //   且在浅底上 9.5 + Regular 偏"淡"，正是用户说的"清晰度"那一层观感来源（C5）。
                f.Font = Ui(TextScale.Body, FontStyle.Regular);
                if (f.FormBorderStyle == FormBorderStyle.FixedDialog || f.FormBorderStyle == FormBorderStyle.Sizable)
                {
                    // 去系统标题栏，换成自绘标题栏；既有子控件整体下移 BarH，绝对坐标布局不受影响
                    f.FormBorderStyle = FormBorderStyle.None;
                    f.MinimizeBox = false; f.MaximizeBox = false;
                    f.Padding = new Padding(1);
                    CardPanel bar = BuildTitleBar(f, title);
                    f.Controls.Add(bar);
                    bar.BringToFront();
                    foreach (Control c in f.Controls)
                    {
                        if (c != bar) c.Top += BarH;
                    }
                    // (2026-09-23：原来这里是 `System.Drawing.Size` 全名，因 StyleKit 内有同名嵌套类
                    //  `Size`；该嵌套类已改名 `TextScale` ⇒ 遮蔽源消除，回退裸名。)
                    //
                    // ⚠️ 2026-09-23 修（用户报「--dlgprobe 的 CLIP 计数 = 19，且弹窗内容被裁」）：
                    //   **这里原来写 `+ BarH`，是错的方向。**
                    //
                    //   错在哪：各窗体是在 `Apply` **之前**设的 `ClientSize`（那是"加自绘标题栏
                    //   **之前**"的整窗高度 = 设计高度）。`+ BarH` 等于把**同一片高度**切成两半：
                    //   标题栏吃掉 38px，其余子控件整体下移 38px ⇒
                    //     ① 相对窗体定位的按钮/卡片被推到客户区之外（视觉上「对话界面被裁」）；
                    //     ② `Restyle` 的 `b.Height = Math.Max(b.Height, 34)` 把按钮压扁 ⇒ 文字被裁；
                    //     ③ `Label.Height` 仍是构造时的固定值，而放大字号后 need 变大 ⇒
                    //        「Label 高度只有 need 的 ~1/2」⇒ CLIP=19（4 个窗体里都有）。
                    //
                    //   正解：**窗口要长高 BarH**，让"设计高度"= **标题栏以下**的客户区高度。
                    //   `Apply` 是构造函数的最后一句（硬规矩 ⑤）⇒ 此刻 `ClientSize` 必是设计值；
                    //   重复调用也安全（二次调用时边框已是 None，根本不进这个分支）。
                    f.ClientSize = new Size(f.ClientSize.Width, f.ClientSize.Height + BarH);
                }
                f.HandleCreated += delegate { Decorate(f); };
                if (f.IsHandleCreated) Decorate(f);
                f.Resize += delegate { Reclip(f); };   // 圆角裁剪随尺寸重算（可拉伸的窗口必须挂这一条）
                Restyle(f);
                // 有些窗体在构造函数里靠后还会再设一遍颜色/字体 ⇒ Load 时统一再落一次（幂等）
                f.Load += delegate
                {
                    try
                    {
                        f.BackColor = Bg;
                        f.Font = Ui(TextScale.Body, FontStyle.Regular);
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
                Reclip(f);
            }
        }

        /// 无边框窗的圆角裁剪：**必须随尺寸重算**——Region 只在构造时算一次的话，
        /// 用户把窗口拉大之后，超出的那块既画不出来、鼠标也点不进去（实测：拉完按钮点不动）。
        static void Reclip(Form f)
        {
            if (f.FormBorderStyle != FormBorderStyle.None) return;
            try
            {
                using (var p = RoundBar.RoundRect(new Rectangle(0, 0, f.Width, f.Height), 12))
                    f.Region = new Region(p);
            }
            catch { }
        }

        internal const int BarH = 38;

        /// 标题栏按钮的**统一高度**。
        ///
        /// ⚠️ 2026-09-23 加（接手方第五轮回执：CLIP 回归 = 3，全在 `ConsoleForm` 顶栏那三颗
        ///   `GlyphButton` 上，`need` 分别是 48×27 / 52×27 / 60×27 而高度写的是 26
        ///   ⇒ `h=26->26 CLIP`，差 1px）。
        ///
        /// 为什么必须 ≥27：字形（减号/方框/叉）在**同一支笔**下要撑满 27px 才不被截，
        ///   而按钮高度是**设计定死的**（`AppendProbe` 对按钮"只报不撑"）。
        ///   ⇒ 这个数就是"标题栏按钮高度"的唯一来源，四处（三颗 Glyph + 一颗 RoundButton）共用。
        ///   尺寸账：`Location.Y = 8` + 27 = 35 ≤ `BarH`(38) ⇒ 仍在标题栏内，不会顶出去。
        internal const int TitleBarBtnH = 27;

        /// 自绘标题栏：标题文字 + 最小化/关闭（拖动靠 Drag）
        static CardPanel BuildTitleBar(Form f, string title)
        {
            CardPanel bar = new CardPanel();
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
            // ⚠️ 2026-09-23：高度原写 26 —— 这里 `Dock = Right` 会让高度被 Dock 覆盖成整条 `BarH`(38)，
            //   所以 26 是个**从来没生效**的值（看起来像"设计高度"，实则误导）。改成"标题栏按钮的统一高度"
            //   `TitleBarBtnH`，与 `GlyphButton` 那三颗对齐（接手方第五轮：那三颗 need=27 ⇒ 高度必须 ≥27）。
            //   注意：Dock 生效时这句话仍不改变外观，它的作用是把**意图**写对，防以后有人把 Dock 去掉。
            //   ⇒ 宽 / 高两个字面量都不许留 —— 走 `TitleBarBtnW` / `TitleBarBtnH` 两个常量（#13 收口）。
            cls.Text = "✕"; cls.Size = new Size(TitleBarBtnW, TitleBarBtnH); cls.FlatStyle = FlatStyle.Flat;
            cls.FlatAppearance.BorderSize = 0; cls.BackColor = Bg; cls.ForeColor = Ink3ok;   // 2026-09-23: Sub(4.35:1)→Ink3ok(5.02:1)，✕ 是要认的符号
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

        // ── 2026-09-23 加：卡片与图标的**唯一构造入口** ────────────────────────
        /// 造一张卡片并挂到窗体上（位置/尺寸按窗体坐标给）。六个弹窗全走这里 ⇒ 形态必然一致。
        /// 参数里的 `System.Drawing.Size` 写全名（**类型位置**，不能省）：嵌套类已改名 `TextScale`，
        /// 但这里保持显式，防以后有人再塞一个同名嵌套类进来。
        public static CardPanel MakeCard(Form f, Point at, System.Drawing.Size size)
        {
            return MakeCard(f, at, size, false, false);
        }
        public static CardPanel MakeCard(Form f, Point at, System.Drawing.Size size, bool emphasis, bool danger)
        {
            CardPanel p = new CardPanel();
            p.Location = at;
            p.Size = size;
            p.Emphasis = emphasis;
            p.Danger = danger;
            f.Controls.Add(p);
            return p;
        }

        // ── 2026-09-23（#12 F1）：**高度由内容算出**，不再是手抄的固定值 ──────────
        /// 先造一张"只给了宽度、等收口"的卡片（高度暂按上内边距占位，看不出来）。
        ///
        /// 为什么要分两步而不是一步传高度：卡里的子控件多半是**按上一块的实测高度往下排**的
        ///   （例：`NoticeForm` 的 `m3.Location` 依赖 `m2h`，`DeadLinkForm` 同理）
        ///   ⇒ 必须**先有 card 容器**才排得下去，排完才知道内容有多深。
        ///   ⇒ 谁都不必再猜"这张卡该多高"：排完 `SealCard(card)` 一收口就对了。
        public static CardPanel MakeCard(Form f, Point at, int width)
        {
            return MakeCard(f, at, new System.Drawing.Size(width, CardPadY));
        }

        /// **卡片高度收口**：`高度 = 最深子控件的底边 + CardPadB`。
        /// 空卡退化成 `CardPadY + CardPadB`。返回最终高度 ⇒ 调用方可以拿它排下一块（卡片 / 按钮 / 窗高）。
        ///
        /// ⚠️ 为什么用 `c.Top + c.Height` 而不是 `c.Bottom`：`Bottom` 是同一件事，
        ///    这里展开写是为了让人一眼看出"高度就是从**内容占到的地方**算出来的"。
        public static int SealCard(CardPanel p)
        {
            int deepest = CardPadY;
            foreach (System.Windows.Forms.Control c in p.Controls)
            {
                int edge = c.Top + c.Height;
                if (edge > deepest) deepest = edge;
            }
            p.Height = deepest + CardPadB;
            return p.Height;
        }

        // ── 2026-09-23（#13 F2）：按钮宽度 = **文字实际渲染宽度 + 36** ─────────────
        /// 只负责"尺寸"，**不碰配色**（调用方照旧自己上 `BackColor` / `ForeColor`）
        ///   ⇒ 主按钮 / 次按钮 / 禁用态各写各的，不因为收口宽度而互相牵连。
        ///
        /// ⚠️ `NoPadding` 是刻意的：它量的是"文字自己占多宽"，不含 GDI 给文字留的那圈边距；
        ///    再各加 `BtnPadX`(18) ⇒ 正好是口径里的"实宽 + 36"。
        /// ⚠️ 字体取按钮**自己的**（`RoundButton` 构造里定为 `TextScale.Body`），不抄常量
        ///    ⇒ 以后动字号，宽度自己跟着走，不需要有人回来改这里的数。
        /// ⚠️ chrome 按钮（min / max / close）**不走这里** —— 它们没有文字，
        ///    尺寸走 `TitleBarBtnW` / `TitleBarBtnH`（裁定 1）。
        public static RoundButton MakeButton(string text)
        {
            RoundButton b = new RoundButton();
            b.Text = text;
            Size words = TextRenderer.MeasureText(text, b.Font,
                new Size(int.MaxValue, int.MaxValue), TextFormatFlags.NoPadding);
            b.Size = new Size(words.Width + BtnPadX * 2, BtnH);
            return b;
        }

        /// **统一图标**：缩放到 `size` 并**画圆角遮罩**。
        ///
        /// 为什么必须走它（诊断 §2 原因 3，实测六处不一致）：
        ///   `app-icon.png` 是一张 256×256 的深色方图，直接贴上去在极浅底上**对比度全窗最高**
        ///   ⇒ 视线先被装饰性的黑方块抓住（视觉层级倒置）；而且圆角是**它自己图里没有的**
        ///   ⇒ 看起来像"贴上去的图"而不是"设计中的一个元素"。
        ///   同时六个窗体原来各写各的尺寸（66 / 64 / 60 / 58）⇒ 统一收口到这里。
        ///
        /// ⚠️ 源图 256×256、最大槽位 56（200% DPI 下 112 < 256）⇒ **缩放不会糊**（诊断已实测排除 C3）。
        public static Image RoundIcon(string path, int size)
        {
            return RoundIcon(path, size, IconRadius);
        }
        public static Image RoundIcon(string path, int size, int radius)
        {
            // ⚠️ 2026-09-23 修（接手方第五轮回执，真根因）：
            //   原来这里是
            //       using (Image src = Image.FromFile(path))
            //       using (Bitmap bmp = new Bitmap(size, size))
            //       { ...; return bmp; }        // ← bmp 在 return 时被 using **先 Dispose 掉**
            //   ⇒ 调用方（`MakeIcon`）拿到的是一个**已释放**的 Image；它被塞进 `PictureBox.Image` 后，
            //     在 paint / show 那一刻才炸 ——
            //     栈：`PictureBox.Animate` → `ImageAnimator.CanAnimate` → `get_FrameDimensionsList`
            //     报文：**「参数无效。」**（就是第四轮那条"跑 0.4 秒弹一次"的系统错误窗的真身；
            //     第四轮我只是把异常**接住并落盘**了，缺陷本身还在 ⇒ 每张图 2 条 `[Boot/ThreadException]`）。
            //
            //   为什么"接住"不等于"修好"：全局钩子只能把栈写下来，用户看到的仍是"图标没画出来"。
            //   ⇒ 本条的判据从"不许弹系统窗"升级为"**不许抛异常**"（接手方：改完这条 14 条日志应一起消失）。
            //
            //   修法：`bmp` **不进 using**，成败都在这里显式处置：
            //     · 成功 ⇒ 直接 `return bmp`（所有权转移给调用方）；
            //     · 失败 ⇒ 自己 `Dispose` 掉再 `return null`（不留半张图、不漏资源）。
            //   `src`（读进来的原图）仍用 using —— 它的像素已被 `TextureBrush` 拷进 `bmp`，本就该释放。
            Bitmap bmp = null;
            try
            {
                if (string.IsNullOrEmpty(path) || !File.Exists(path)) return null;
                using (Image src = Image.FromFile(path))
                {
                    bmp = new Bitmap(size, size);
                    using (Graphics g = Graphics.FromImage(bmp))
                    {
                        g.SmoothingMode = SmoothingMode.AntiAlias;
                        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                        g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                        g.Clear(Color.Transparent);
                        using (var pth = RoundBar.RoundRect(new Rectangle(0, 0, size, size), radius))
                        using (var tb = new TextureBrush(src, System.Drawing.Drawing2D.WrapMode.Clamp))
                            g.FillPath(tb, pth);
                    }
                }
                return bmp;   // ⚠️ 刻意不回填原文件、不返回 src：调用方持有的是独立位图（且**必须活着**）
            }
            catch
            {
                // 画失败 ⇒ 自己收拾干净，绝不把半张/已废的图交出去
                try { if (bmp != null) bmp.Dispose(); } catch { }
                return null;
            }
        }

        /// 造一个统一尺寸的图标控件并挂到窗体上。找不到图就返回 null（调用方自行决定留不留白）。
        public static PictureBox MakeIcon(Form f, string root, Point at)
        {
            return MakeIcon(f, root, at, IconSize);
        }
        public static PictureBox MakeIcon(Form f, string root, Point at, int size)
        {
            Image img = RoundIcon(Path.Combine(root, "assets", "app-icon.png"), size);
            if (img == null) return null;
            PictureBox pic = new PictureBox();
            pic.Image = img;
            pic.SizeMode = PictureBoxSizeMode.Zoom;
            pic.Location = at;
            pic.Size = new Size(size, size);
            pic.BackColor = Color.Transparent;
            f.Controls.Add(pic);
            return pic;
        }

        /// 统一正文标签（省得每个窗体各写各的 Font/ForeColor）。
        /// ⚠️ 2026-09-23：`ink` 参数允许传 `Color.Empty` ⇒ 落 `InkBody`（9.9:1 的正文档）。
        ///   这样调用方不必在每个 Label 上都写一遍 `StyleKit.InkBody`（写漏了就退化成"继承"）。
        public static Label MakeLabel(string text, float size, Color ink, int width)
        {
            Label lb = new Label();
            lb.Text = text;
            lb.Font = Ui(size, FontStyle.Regular);
            lb.ForeColor = (ink == Color.Empty) ? InkBody : ink;
            lb.AutoSize = false;
            lb.Width = width;
            lb.Height = MeasureFitHeight(text, lb.Font, width);
            return lb;
        }

        /// 把一个标签的高度**按实测文字高度**撑开（而不是硬编码），并返回需要的高度。
        /// 诊断 §4 P1-6：三处 `Size` 里的高度原来是**手写的魔法数 + 注释里记着 need 值**，
        /// 那是"注释里记着"而不是"结构上不会发生"。这里改成当场量。
        ///
        /// ⚠️ 2026-09-23 加固（CLIP=19 那一轮）：原来只撑 `Height`，漏了两件事——
        ///   ①`lb.Width` 可能是 0 或未被设过（`MeasureText` 的宽度参数会退化）⇒ 按 `MinimumSize`/父宽兜底；
        ///   ②多行文字实测出来的是**宽 × 高**的组合，只改高不改宽时换行点会变 ⇒ 两轴都按证据落回。
        ///   `AutoSize=true` 的标签不吃这套（它会自己长），直接跳过。
        ///
        /// ⛔ 2026-09-23 **第三次修（真 bug：三处正文正在被裁）**
        ///   老实现是 `lb.Height = Math.Max(lb.Height, h);` —— **只长不缩**。
        ///   当时的理由是「缩会把调用方算好的间距弄乱」。但那个理由只对**一半**的情形成立：
        ///     · 手写高度**偏小**时，`Math.Max` 会把它撑到实测值 —— 这半边是работ的；
        ///     · 手写高度**偏大**时，`Math.Max` 原样保留那个偏大的值 —— 这半边**从不工作**。
        ///   而后者带来的不是"多留白"，是**文字被 `CardPanel` 的裁剪区切掉**：
        ///     `BusyForm/m2`(手写 90)、`DeadLinkForm/m2`(110)、`AskForm/qm2`(116)
        ///     实测都需要 ~152 ⇒ 三条正文各自被切掉了最后一行。
        ///   （实拍：`_scratch/shots-20260923d/busy.png`）
        ///
        ///   ⇒ 证据（`MeasureFitHeight`）说了算，**双向**：
        ///     `lb.Height` 一律落回实测值。调用方若想表达"这里要多留白"，
        ///     请用 `Margin`/`Padding`/显式的间隔控件 —— **不要编码进 Height**，
        ///     因为 Height 是"文字需要多少"的语义，混进"我想留多少空"就再也分不清了。
        public static int FitLabel(Label lb)
        {
            if (lb == null) return 0;
            if (lb.AutoSize) return lb.Height;          // 自适应的不用我们管
            int w = lb.Width;
            if (w <= 0 && lb.Parent != null) w = Math.Max(8, lb.Parent.ClientSize.Width - lb.Left);
            w = Math.Max(8, w);
            int h = MeasureFitHeight(lb.Text, lb.Font, w);
            if (h > 0) lb.Height = h;                    // ★ 双向：实测值说了算（h<=0 时不动，免得把控件抹成 0 高）
            return h;
        }

        /// 量"这段文字在给定宽度下需要多高"（供 `FitLabel` 与 `--dlgprobe` 用同一个尺子）
        public static int MeasureFitHeight(string text, Font f, int width)
        {
            if (string.IsNullOrEmpty(text) || f == null) return 0;
            // 2026-09-23：这里原来是 `System.Drawing.Size` 全名 —— 因为当时 StyleKit 里有个
            //   **同名嵌套类** `Size`（字号阶梯），按 C# 名字查找"就近优先"，裸写 `Size` 会解析到
            //   那个 class ⇒ CS0118。**嵌套类已改名 `TextScale`**，遮蔽源消除 ⇒ 已回退为裸 `Size`。
            //   （守备：`_scratch/_cs_precompile.py` 判据 5 + `_cs_shadow_audit.py` 判据 B）
            Size s = TextRenderer.MeasureText(text, f,
                new Size(Math.Max(8, width), int.MaxValue),
                TextFormatFlags.WordBreak);
            return s.Height;
        }

        /// 离屏取证：把窗体真实画面写成 PNG —— 显示但**抢不到前台**、且不像素判据自欺。
        /// 姿势（2026-09-13 四组对照实测得出）：
        ///   ①先 `CreateControl()` 建句柄 → ②给窗口加 `WS_EX_NOACTIVATE` → ③`ShowWithCtx`（不激活地显示）
        ///   → ④`DrawToBitmap`（**只有它在无边框+Region 的窗体上出得来像素**）。
        /// ⚠️ 两条被实测证否的老路：`CreateControl`+`SWP_SHOWWINDOW`（WinForms 不认为窗口 Visible ⇒ 子控件不画，全白图，
        ///    6 张"渲染成功"的截图其实是空白）；`PrintWindow(PW_RENDERFULLCONTENT)` 在这类窗体上也只有底色。
        ///
        /// ⚠️ 2026-09-23 第二次修（接手方报：`--shot` 的 6 张图在真 exe 下 **0/6 True**，
        ///   而在控制台宿主里是 **5/6 True**，且只有 `DeadLinkForm` 两种宿主下都 False）。
        ///   这条线索一步锁定了根因 —— **问题不是"标记丢了"，而是"标记本来就没生效"**：
        ///
        ///   1) `WS_EX_NOACTIVATE` 只管"**鼠标点击**不激活"。它**挡不住程序自己调 `Show()`** ——
        ///      `Show()` 会走 `SetActiveWindow/SetForegroundWindow`，于是前台照样被抢走。
        ///      ⇒ 只有在 **`ShowWithoutActivation` 覆写为 true**（WinForms 内部改用
        ///        `SW_SHOWNOACTIVATE`）时，`Show()` 才真的不抢。这正是 `ConsoleForm` 里那条
        ///        老注释说的「实测：只加 `WS_EX_NOACTIVATE` 还不够，WinForms 的 `Show()` 仍会激活」。
        ///   2) 而 `ShowWithoutActivation` 是 Form 的 protected 虚属性 ⇒ **只能在子类里覆写**。
        ///      六个窗体里**只有 `ConsoleForm` 覆写了**（`public bool NoActivate` 那条），
        ///      其余五个（LauncherForm/BusyForm/AskForm/NoticeForm/DeadLinkForm）都没有。
        ///      ⇒ **它们在 `Show()` 那一刻必然抢前台**，`WS_EX_NOACTIVATE` 管不着。
        ///   3) 为什么"控制台宿主 5/6 True、真 exe 0/6 True"？因为宿主进程**没有前台窗口可抢**
        ///      （或者抢了也不变，`GetForegroundWindow()` 比较的还是同一个），所以看起来"通过"——
        ///      **那个 5/6 是假绿**。这也解释了为什么 W6b 基线当时能全 True：当时测的是宿主体。
        ///   4) `DeadLinkForm` 两种宿主下都 False，是**第二个独立原因**，见下面 `PrepareForShot`。
        ///
        ///   修法：**不碰 Form 的 `Show()`**，改用 Win32 直接把窗口显示出来（`ShowWindow` + `SW_SHOWNOACTIVATE`
        ///   → 原子地把"显示"和"不激活"绑在一起，不经过 WinForms 的激活逻辑）。
        ///   判据口径没变：仍是 `GetForegroundWindow() == fg0`。
        public static string CaptureOffscreen(Form f, string path)
        {
            IntPtr fg0 = GetForegroundWindow();
            string prep = PrepareForShot(f);
            MarkNoActivate(f);
            ShowNoActivate(f);
            MarkNoActivate(f);                       // 显示过程可能重建句柄 ⇒ 复核一遍（幂等）
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
                   + prep
                   + " -> " + path;
        }

        /// 出图前的**兜底**：把"必抢前台"的三件事按需卸掉，并把做了什么写进返回串（便于定位）。
        ///
        /// 为什么 `DeadLinkForm` 在两种宿主下都 False（接手方给的突破口）：
        ///   它的构造函数里有 `AcceptButton = retry; CancelButton = no;`。
        ///   而在 `FormBorderStyle.None` 的无边框窗上，WinForms 仍会为 `AcceptButton/CancelButton`
        ///   准备对话框键处理 ⇒ 窗口一旦显示就会尝试把焦点/激活拿过去（对话框语义）。
        ///   这里在**出图前**把三者归位：`ShowInTaskbar=false`（本来就在做）、
        ///   `StartPosition=Manual` + 屏外坐标、以及 `AcceptButton/CancelButton` 临时置空
        ///   （只影响"回车/ESC 触发哪个按钮"，取证本来就不按键，**不改变产品行为**）。
        static string PrepareForShot(Form f)
        {
            var notes = new StringBuilder();
            try
            {
                f.StartPosition = FormStartPosition.Manual;
                f.Location = new Point(-4000, -4000);   // 屏外：用户看不到窗口开关
                if (f.ShowInTaskbar) { f.ShowInTaskbar = false; }
                if (f.AcceptButton != null) { f.AcceptButton = null; notes.Append(" 卸Accept"); }
                if (f.CancelButton != null) { f.CancelButton = null; notes.Append(" 卸Cancel"); }
            }
            catch { }
            return notes.ToString();
        }

        /// **不激活地把无边框窗显示出来**（`CaptureOffscreen` 的核心，替代 `Form.Show()`）。
        ///
        /// 为什么不能再用 `Show()`：见 `CaptureOffscreen` 顶部第 1)2) 条 ——
        /// `WS_EX_NOACTIVATE` 挡不住程序自己调 `Show()`，而六个窗体里只有 `ConsoleForm`
        /// 覆写了 `ShowWithoutActivation`。所以这里**绕开 WinForms 的显示路径**，
        /// 直接用 Win32：`ShowWindow(h, SW_SHOWNOACTIVATE)` 一步到位 ——
        /// "显示"和"不激活"由同一个调用保证，不存在中间被激活的窗口。
        ///
        /// ⚠️ 显示之前必须 `f.CreateControl()`：`DrawToBitmap` 要的是"WinForms 认为控件已创建"，
        ///   否则子控件一个都不画（全白图，2026-09-13 已实测踩过）。
        ///
        /// ⚠️ 2026-09-23：改成 `public` —— `--dlgprobe`（`Ui.DlgProbe`）里也有一句 `f.Show()`，
        ///   是**同一个抢前台的根因**（那里也只是"读一遍控件树"的取证动作，没有任何理由抢前台）。
        ///   两处共用这一份实现 ⇒ 以后不会再各修各的。
        public static void ShowNoActivate(Form f)
        {
            try
            {
                f.CreateControl();                     // 建句柄 + 让 WinForms 认可"已创建"
                IntPtr h = f.Handle;
                // SW_SHOWNOACTIVATE(4)：显示窗口但**不激活**它（哪怕它当前未激活）
                ShowWindow(h, 4);
                // SWP_NOACTIVATE(0x10) | SWP_SHOWWINDOW(0x40) | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                // 再补一次，覆盖"ShowWindow 之后被别的东西改了 Z 序/激活"的情况
                SetWindowPos(h, IntPtr.Zero, 0, 0, 0, 0, 0x10 | 0x40 | 0x2 | 0x1 | 0x4);
            }
            catch { }
        }

        /// 给窗口加 `WS_EX_NOACTIVATE` 并**立刻生效**（`CaptureOffscreen` 的判据靠它成立）。
        ///
        /// 为什么不能只 `SetWindowLong` 一句：
        ///   `SetWindowLong` 只写风格整数位，不加 `SWP_FRAMECHANGED` 的话，窗口管理器可能仍按旧风格
        ///   处理激活；而且 WinForms 在 `Show()` / 改 `ShowInTaskbar` / 改边框时会**重建句柄**，
        ///   新句柄不带这个属性。所以这里：读 → 若已置位则直接返回 → 否则置位 + `SWP_FRAMECHANGED`。
        /// 幂等：重复调用只在"确实没置位"时才动 `SetWindowLong`。
        ///
        /// ⚠️ 但它**只管鼠标点击**，挡不住 `Show()` 自带的激活 —— 那件事由 `ShowNoActivate` 解决。
        /// ⚠️ 2026-09-23：改成 `public`（同 `ShowNoActivate`，`--dlgprobe` 复用）。
        public static void MarkNoActivate(Form f)
        {
            try
            {
                IntPtr h = f.Handle;                   // 可能触发建句柄，正是我们要的
                int lex = GetWindowLong(h, GWL_EXSTYLE);
                if ((lex & WS_EX_NOACTIVATE) != 0) return;   // 已生效 ⇒ 什么都不做
                SetWindowLong(h, GWL_EXSTYLE, lex | WS_EX_NOACTIVATE);
                // SWP_FRAMECHANGED(0x20) | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                SetWindowPos(h, IntPtr.Zero, 0, 0, 0, 0, 0x20 | 0x2 | 0x1 | 0x4 | 0x10);
            }
            catch { }
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
                // ⚠️ 2026-09-23：**卡片自己管自己的外观，这里不许覆盖它** ——
                //   CardPanel 是自绘的（白底 + 描边 + 圆角），原来 Restyle 会把 BackColor 改回 Bg
                //   ⇒ 卡片和窗体底色糊在一起，"有落脚处"这件事就白做了。
                if (c is CardPanel) { if (c.HasChildren) Restyle(c); continue; }
                Button b = c as Button;
                if (b != null)
                {
                    // ⚠️ 主按钮判定必须在覆盖 BackColor **之前**做：老写法先写 CardPanel 再判 CardPanel 的 R/B ⇒ 恒为 false
                    //    （结果是所有按钮都成了白卡片，强调色主按钮从来没生效过）
                    bool accentSet = (b.BackColor.B > 200 && b.BackColor.R < 140 && b.BackColor.G < 200);
                    // ⚠️ 这里判的是"按钮是否长在**自绘标题栏**上"（标题栏按钮要透明底、只留字），
                    //   而标题栏本身是 `BuildTitleBar` 造的自绘卡片 ⇒ 必须判 `CardPanel`。
                    //
                    //   2026-09-23 修（编译报错 `CS0146 循环基类依赖`带出来的第二处隐患）：
                    //   老写法裸写 `Panel`，原本解析到 `System.Windows.Forms.Panel`（所有控件都满足），
                    //   一旦新类叫 `Panel` 就会被**静默改绑**到自绘卡片上 ⇒ 判据含义整个变掉。
                    //   现在新类统一叫 `CardPanel` 且基类写全名，这里显式写 `CardPanel` ⇒ 语义明确、不会再被遮蔽。
                    bool titlebar = (b.Parent is CardPanel) && (((CardPanel)b.Parent).Dock == DockStyle.Top);
                    b.FlatStyle = FlatStyle.Flat;
                    b.FlatAppearance.BorderSize = 1;
                    b.FlatAppearance.BorderColor = Line;
                    b.BackColor = Card;
                    b.ForeColor = Ink;
                    // 2026-09-23：按钮字号跟正文同档（原来写死 9.5，与正文 10.5/9.5 三套并存）
                    b.Font = Ui(TextScale.Body, accentSet ? FontStyle.Bold : FontStyle.Regular);
                    RoundButton rb = b as RoundButton;
                    if (rb != null) { rb.Primary = accentSet && !titlebar; rb.FlatAppearance.BorderSize = 0; }
                    if (accentSet && !titlebar) { b.BackColor = Accent; b.ForeColor = Color.White; b.FlatAppearance.BorderColor = Accent; }
                    if (titlebar)
                    {
                        // 标题栏上的最小化/关闭：无边框、跟标题栏同底色，悬停由 RoundButton 自己画
                        b.FlatAppearance.BorderSize = 0; b.BackColor = Bg; b.ForeColor = Ink3ok;   // 2026-09-23: 与下面 OnPaint 的 ink 同档，防"设了不生效"
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
                            // 2026-09-23：9 → 9.5（诊断 §3 C6：8.5f 等宽在小字号下"挤"），
                            //   且**日志框的字体由调用方决定**——自绘日志改用 UI 字体（见 LogView）
                            tb.Font = Mono(9.5f);
                            // 2026-09-14 用户："这个日志栏怎么是黑的？好丑，你换成浅蓝的、白的都行"
                            // ⇒ 启动器的日志区**跟窗体同色系**（浅底深字），深色控制台底色只留给真正的终端
                            tb.BackColor = Color.FromArgb(244, 248, 254);
                            tb.ForeColor = Ink;
                        }
                    }
                    else
                    {
                        Label lb = c as Label;
                        if (lb != null && lb.Font != null)
                        {
                            // 原来只在 >= 13f 时才设 Ink，于是 12.5 的区块标题会保持窗体继承色（偏浅）
                            // ⇒ 现在按**语义档位**给色：标题类用 Ink，正文交给调用方（它才知道自己是主还是次）
                            if (lb.Font.Size >= TextScale.Head - 0.6f) lb.ForeColor = Ink;
                            // ⚠️ 2026-09-23 补（用户：「文字颜色还有优化空间」）：
                            //   原来大于正文档位的**什么都不做** ⇒ 正文"继承窗体默认色"。
                            //   实测后果：窗体色一路继承下去，遇到深底/卡片底就会"淡到看不清"，
                            //   而调用方又没法区分"我是没设"还是"我刻意设成这个色"。
                            //   ⇒ 这里给**未显式着色**的正文落一个明确档位（`InkBody` 9.9:1）。
                            //   判据：`ForeColor` 还等于窗体继承色（`Control.DefaultForeColor` 或继承值）
                            //   才落 —— 调用方显式设过的**一律不覆盖**。
                            else if (lb.ForeColor == SystemColors.ControlText
                                     || lb.ForeColor == (lb.Parent != null ? lb.Parent.ForeColor : SystemColors.ControlText))
                            {
                                lb.ForeColor = InkBody;
                            }
                        }
                    }
                }
                if (c.HasChildren) Restyle(c);
            }
        }
    }

    /// **卡片**：白底 + 1px 描边 + 圆角 10 + 内边距 —— 弹窗族"内容有落脚处"的那一层。
    ///
    /// 为什么要有它（2026-09-23 加，用户：「我感觉这样还是太简陋了，就根据 Meta 的设计风格优化」）：
    ///   诊断 `弹窗族诊断.md` §2 原因 1 —— 六个弹窗的内容原来都是**直接铺在 `#F7F9FC` 底上的一片字**，
    ///   与底色只差一点点，眼睛无处落脚 ⇒ 再好的文案也显得"没做完"。
    ///   加一层白卡是**加法**：不动流程、不动既有子控件的绝对坐标语义（往里加的子控件坐标相对卡片算）。
    ///
    /// 用法（注意坐标是**相对卡片**的，所以要比原来减去卡片在窗体上的 Location）：
    ///   CardPanel card = StyleKit.MakeCard(this, new Point(20, 96), new Size(460, 190));
    ///   card.Controls.Add(new Label { Location = new Point(20, 18), ... });
    public class CardPanel : System.Windows.Forms.Panel
    {
        public int Radius = 10;
        /// 强调描边（用于"危险区"这类需要区别对待的卡片）；默认 false＝普通 `CardLine` 描边
        public bool Emphasis = false;
        /// 「危险」语义：描边转红、底色转极浅红（危险操作卡专用）
        public bool Danger = false;

        public CardPanel()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint
                     | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            BackColor = StyleKit.Card;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            // 先铺窗体底色，再把卡片圆角画上去 —— 否则四个角会露出父控件的方形底
            Color outer = (Parent != null) ? Parent.BackColor : StyleKit.Bg;
            using (var ob = new SolidBrush(outer)) g.FillRectangle(ob, ClientRectangle);
            Rectangle r = new Rectangle(0, 0, Width - 1, Height - 1);
            Color fill = Danger ? Color.FromArgb(253, 246, 246) : StyleKit.Card;
            Color edge = Danger ? Color.FromArgb(240, 208, 208) : StyleKit.CardLine;
            // ── 2026-09-23 补**升起**（用户：「按钮的质感…还有优化空间」的同一诉求）──
            // 上一版卡片只有"填充 + 描边"，与背景的差只有那 1px 描边 ⇒ 卡片像"画上去的框"
            //   而不是"浮在底上的一片纸"。Meta 的卡片有极淡的一层 `0 1px 2px` 投影。
            // 这里用与按钮同一套手法：描边近似软阴影（1px 偏移 + α=16 的黑），仅在**非危险**卡上画
            //   —— 危险卡是"警告"语义，越平越对（不该看起来可以"拿起来"）。
            if (!Danger)
            {
                using (var sp = new Pen(Color.FromArgb(16, 0, 0, 0), 1f))
                using (var pth = RoundBar.RoundRect(new Rectangle(0, 1, Width - 1, Height - 1), Radius))
                    g.DrawPath(sp, pth);
            }
            using (var pth = RoundBar.RoundRect(r, Radius))
            using (var fb = new SolidBrush(fill)) g.FillPath(fb, pth);
            using (var pth = RoundBar.RoundRect(r, Radius))
            using (var pb = new Pen(edge, Emphasis || Danger ? 1.4f : 1f)) g.DrawPath(pb, pth);
        }
    }

    /// **自绘日志区**（替代 `TextBox` + `ScrollBars.Vertical`）。
    ///
    /// 为什么要换掉系统 TextBox（诊断 §2 原因 4，实测）：
    ///   它是 `LauncherForm` 里**面积最大的装饰性元素**，却完全没有设计 ——
    ///   ① 突然冒出一条系统滚动条（截图里那个灰箭头条），与其余自绘控件不是一套语言；
    ///   ② 里面是 `Consolas` 等宽字，**和全窗的中文 UI 字体是两套语言**，中英混排基线不齐（C6）；
    ///   ③ 底色 `#FCFDFF` 浅得发白，与卡片/背景几乎同色，边界"看不见"。
    ///   ⇒ 单这一块就占了"简陋"感的一半。
    ///
    /// 对外仍暴露 `Lines` / `BeginUpdate` / `EndUpdate` 之外的最小面：
    ///   `Append(string)` / `Clear()` / `LineCount`。**保留自动滚到底**（原来 TextBox 用 AppendText 就有）。
    ///
    /// ── 2026-09-23 重写滚动条（用户：「滚动条…还有优化空间」）──
    /// 上一版的三个问题（自评，截图比对得出）：
    ///   ① **没有轨道**：只有一根 3px 的竖条"贴"在右边，看不出"这里可以拖/可以点"⇒ 不像控件、像装饰线；
    ///   ② **恒定 3px、无 hover / 按下反馈**：鼠标移上去毫无变化 ⇒ 光标到位了也不知道点得中；
    ///   ③ **不可拖动、点轨道无反应**：只能滚轮 ⇒ 鼠标用户的第一直觉（拖滑块）落空。
    ///
    /// 新版按 **Meta 的滚动条语言**做（克制、细、悬停才"醒过来"）：
    ///   · **轨道**：进控件即有一条极淡的半透明轨（`#E6ECF5` 60%），只在**需要滚动**时出现；
    ///   · **滑块**：常态 6px 宽 / 圆角 3 / 透明度中；`hover` 时**加宽到 10px 并变深**（经典"醒过来"）；
    ///   · **可拖**：`MouseDown` 命中滑块 → 捕获鼠标 → `MouseMove` 换算成行偏移（1:1 跟手）；
    ///   · **点轨道翻页**：命中轨道空白 → 翻一屏（不是 1 行，那是老式 Windows 的行为）；
    ///   · **滚轮**：保留（且**只在内容超出时**才吃掉滚轮，否则让事件冒泡给父容器）。
    public class LogView : Control
    {
        readonly System.Collections.Generic.List<string> _lines =
            new System.Collections.Generic.List<string>();
        int _maxKeep = 400;          // 只留尾部若干行（原来窗体侧截到 50，这里放宽、由窗体决定）
        int _top;                    // 第一行可见索引（由滚动条控制）
        int _rowH = 17;
        bool _stick = true;          // 是否"粘在底部"（用户在底部时新行自动滚出来）

        // ── 滚动条状态（2026-09-23 加：可悬停、可拖动） ──
        bool _sbHover;               // 鼠标进到滚动条热区（右侧 14px 竖条）
        bool _sbDrag;                // 正在拖滑块
        int _sbGrabY;                // 按下时鼠标相对滑块顶部的偏移（保证"跟手"、不跳）
        bool _overSb;                // 鼠标是否落在**滑块**上（与"落在热区"不同：热区含轨道）

        static readonly Color SbTrack = Color.FromArgb(150, 226, 236, 245);   // 轨道（半透明，压在浅底上）
        static readonly Color SbThumb = Color.FromArgb(255, 199, 212, 228);   // 滑块常态
        static readonly Color SbThumbHi = Color.FromArgb(255, 166, 184, 206); // 滑块悬停/按下（变深）

        public LogView()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint
                     | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            BackColor = StyleKit.Card;
            Font = StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Regular);   // 2026-09-23: 日志是要读的内容，不是脚注
            // 自绘控件要自己接滚轮。**只在内容超出时才处理**：否则把事件交回父容器，
            // 免得"日志没几行却把整窗的滚动吃掉了"。
            MouseWheel += delegate(object s, MouseEventArgs e)
            {
                if (!NeedScroll) return;
                ScrollBy(-e.Delta / 120 * 3);
            };
            // 进热区 ⇒ 滑块"醒过来"（加宽 + 变深），这是"可点得到"的唯一视觉线索
            MouseMove += OnLogMouseMove;
            MouseDown += OnLogMouseDown;
            MouseUp += OnLogMouseUp;
            MouseLeave += delegate { _sbHover = false; _sbDrag = false; Invalidate(); };
        }

        /// 内容是否超出（决定滚动条画不画、滚轮吃不吃）
        bool NeedScroll { get { return _lines.Count > VisibleRows; } }

        // ── 滚动条几何（一处算、三处用：画、命中、拖动） ──
        /// 滚动条**热区**：右侧 14px 竖条（比视觉宽度宽，方便命中 —— 视觉 6px、热区 14px 是常规做法）
        const int SbHot = 14;
        /// 滑块视觉宽度（常态 / 悬停）。悬停时从 6 变 10，靠**右对齐**生长（左边不越过内容区）
        const int SbW = 6, SbWHi = 10;
        int SbRight { get { return Width - 6; } }                       // 滑块右边缘距控件右边 6px
        int TrackTop { get { return Pad; } }
        int TrackH { get { return Math.Max(1, Height - 2 * Pad); } }
        int ThumbH
        {
            get
            {
                int rows = VisibleRows;
                // 最小 28px：太短的滑块在日志很多时"点不着"
                return Math.Max(28, TrackH * rows / Math.Max(1, _lines.Count));
            }
        }
        int MaxTop { get { return Math.Max(0, _lines.Count - VisibleRows); } }
        int ThumbY
        {
            get
            {
                int mt = MaxTop;
                int span = TrackH - ThumbH;
                return TrackTop + (mt == 0 ? 0 : span * _top / mt);
            }
        }

        bool InSbHot(MouseEventArgs e) { return e.X >= Width - SbHot; }
        bool OnThumb(MouseEventArgs e)
        {
            if (!NeedScroll || !InSbHot(e)) return false;
            int ty = ThumbY;
            return e.Y >= ty && e.Y <= ty + ThumbH;
        }

        void OnLogMouseMove(object s, MouseEventArgs e)
        {
            bool hot = NeedScroll && InSbHot(e);
            bool over = hot && OnThumb(e);
            if (_sbDrag && NeedScroll)
            {
                // 拖动：把"鼠标位移"换算成"行位移"。按轨道可走距离/可走行数 求比例 ——
                // 这样无论日志多少行，滑块都**1:1 跟手**（不是"拖一格跳N行"）。
                int span = Math.Max(1, TrackH - ThumbH);
                int y = e.Y - _sbGrabY - TrackTop;
                int want = (int)Math.Round((double)y / span * MaxTop);
                SetTop(want);
                return;
            }
            if (hot != _sbHover || over != _overSb)
            {
                _sbHover = hot; _overSb = over;
                Cursor = over ? Cursors.Hand : Cursors.Default;
                Invalidate();
            }
        }

        void OnLogMouseDown(object s, MouseEventArgs e)
        {
            if (!NeedScroll || e.Button != MouseButtons.Left) return;
            if (!InSbHot(e)) return;
            if (OnThumb(e))
            {
                // 命中滑块 ⇒ 进入拖动（记录抓取点偏移，避免"一点滑块就跳到鼠标位置"）
                _sbDrag = true; _sbGrabY = e.Y - ThumbY;
                Capture = true;
            }
            else
            {
                // 命中轨道空白 ⇒ **翻一屏**（老式 Windows 的"翻 1 行"反直觉；现代语言都是翻一屏）
                bool below = e.Y > ThumbY + ThumbH;
                ScrollBy(below ? VisibleRows : -VisibleRows);
            }
            Invalidate();
        }

        void OnLogMouseUp(object s, MouseEventArgs e)
        {
            if (!_sbDrag) return;
            _sbDrag = false; Capture = false;
            _sbHover = NeedScroll && InSbHot(e);
            _overSb = _sbHover && OnThumb(e);
            Invalidate();
        }

        /// 统一的"设到某一行"（拖动/滚轮/翻页都走这里）
        void SetTop(int t)
        {
            int nt = Math.Min(MaxTop, Math.Max(0, t));
            if (nt == _top) return;
            _top = nt; _stick = (nt >= MaxTop);
            Invalidate();
        }

        public int LineCount { get { return _lines.Count; } }
        public void Clear() { _lines.Clear(); _top = 0; _stick = true; Invalidate(); }

        /// 追加一行（自动换行、自动滚到底）。**线程安全**：需要时自行 Invoke。
        public void Append(string line)
        {
            if (InvokeRequired) { try { BeginInvoke((Action)(() => Append(line))); } catch { } return; }
            if (line == null) return;
            foreach (string seg in Wrap(line, Width))
                _lines.Add(seg);
            while (_lines.Count > _maxKeep) _lines.RemoveAt(0);
            if (_stick) _top = Math.Max(0, _lines.Count - VisibleRows);
            Invalidate();
        }

        /// 直接替换全部内容（窗体侧如果做了 `Lines = xxx`，走这里）
        public void SetLines(string[] ls)
        {
            if (InvokeRequired) { try { BeginInvoke((Action)(() => SetLines(ls))); } catch { } return; }
            _lines.Clear();
            foreach (string s in (ls ?? new string[0]))
                foreach (string seg in Wrap(s, Width)) _lines.Add(seg);
            _top = Math.Max(0, _lines.Count - VisibleRows);
            Invalidate();
        }

        /// 供窗体侧读回（`--dlgprobe` 的机械判据要用到行数/末行）
        public string Tail() { return _lines.Count == 0 ? "" : _lines[_lines.Count - 1]; }

        /// 可见行数。⚠️ 2026-09-23：行高改为走 `StyleKit.LineHeight`（与绘制时同一个尺子）——
        ///   原来这里用常量 `_rowH = 17`，而绘制时也用 17，看着一致；
        ///   但**换过字号之后**（比如 `Small` 8.5 → `Para` 10.5）两者会不一致 ⇒ 底部空一行或半行被切。
        ///   改成"同一个函数算"就不会再出现这种偏差。
        int VisibleRows
        {
            get
            {
                int rh = Math.Max(1, Math.Max(_rowH, StyleKit.LineHeight(Font)));
                return Math.Max(1, (Height - 2 * Pad) / rh);
            }
        }
        const int Pad = 8;

        void ScrollBy(int lines)
        {
            SetTop(Math.Max(0, _top + lines));
        }

        /// 按像素宽度折行（中文≈字号宽、ASCII≈0.55 倍）。自绘控件没有 WordWrap，得自己算。
        /// ⚠️ 2026-09-23：右侧要扣掉**滚动条热区**（`SbHot`）—— 否则折行按满宽算，
        ///   画的时候又被滚动条压掉右边 ⇒ 每行末尾被切（上一版就少扣了那 14px）。
        System.Collections.Generic.IEnumerable<string> Wrap(string s, int width)
        {
            if (s == null) yield break;
            if (s.Length == 0) { yield return ""; yield break; }
            int px = Math.Max(40, width - 2 * Pad - 4 - SbHot);
            var sb = new StringBuilder();
            float acc = 0;
            foreach (char ch in s)
            {
                // 中文按字号宽、ASCII 按 0.55 倍（经验值，与雅黑的字宽比例吻合）
                float w = (ch < 128) ? Font.Size * 0.55f : Font.Size;
                if (acc + w > px && sb.Length > 0) { yield return sb.ToString(); sb.Length = 0; acc = 0; }
                sb.Append(ch); acc += w;
            }
            if (sb.Length > 0) yield return sb.ToString();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            Rectangle r = new Rectangle(0, 0, Width - 1, Height - 1);
            // 底色比白卡再浅一档的"内嵌槽"，让日志区在卡片里也有自己的边界（原来与背景几乎同色 ⇒ 看不见边界）
            using (var pth = RoundBar.RoundRect(r, 8))
            using (var bb = new SolidBrush(Color.FromArgb(247, 250, 254))) g.FillPath(bb, pth);
            using (var pth = RoundBar.RoundRect(r, 8))
            using (var pb = new Pen(StyleKit.CardLine)) g.DrawPath(pb, pth);

            int rows = VisibleRows;
            int maxTop = Math.Max(0, _lines.Count - rows);
            if (_top > maxTop) _top = maxTop;
            // ⚠️ 2026-09-23：右边留 14px 给滚动条（原来是 2px）——
            //   否则日志文字会**压在滑块底下**（上一版 3px 滑块压在字的右边就是这个问题）。
            int textRight = Width - SbHot;
            int y = Pad;
            // 多行自绘文本要自己按行高排；`LineHeight` 是统一口径（含 1.45 行距）
            int rowH = Math.Max(_rowH, StyleKit.LineHeight(Font));
            for (int i = _top; i < _lines.Count && i < _top + rows; i++)
            {
                // 末行（最新一行）用主色加重，其余用**达标**的次要色 ⇒ 一眼看到"刚发生了什么"
                // （2026-09-23：原来用 `Sub`(4.35:1) 不达标 ⇒ 换成 `Ink3ok`(5.6:1)）
                bool latest = (i == _lines.Count - 1);
                Color ink = latest ? StyleKit.Ink : StyleKit.Ink3ok;
                StyleKit.DrawText(g, _lines[i],
                    latest ? StyleKit.Ui(StyleKit.TextScale.Para, FontStyle.Bold) : Font,
                    new Rectangle(Pad, y, Math.Max(20, textRight - Pad), rowH), ink,
                    TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
                y += rowH;
            }
            // ── 自绘滚动条（2026-09-23 重写）──
            // 只在内容超出时出现（原来系统滚动条是**常驻**的，空的时候也杵在那儿）。
            // 三层结构：轨道（淡）→ 滑块（常态 6px / 悬停 10px）→ 拖动时再深一档。
            if (NeedScroll)
            {
                int tbH = ThumbH, tbY = ThumbY;
                int w = (_sbHover || _sbDrag) ? SbWHi : SbW;
                int x = SbRight - w;                 // 右对齐生长（向左变宽，不越过内容区）

                // ① 轨道：整条（比滑块更淡、更宽一点点的"槽"，暗示"这里可以拖")
                using (var tk = new SolidBrush(SbTrack))
                using (var pth = RoundBar.RoundRect(new Rectangle(x, TrackTop, w, TrackH), w / 2))
                    g.FillPath(tk, pth);

                // ② 滑块：圆角矩形。悬停/按下变深（`SbThumbHi`），常态 `SbThumb`。
                //    圆角取 w/2 ⇒ 永远是"胶囊"形（w 变化时形状语言不变，只变粗细）。
                Color tc = (_sbHover || _sbDrag) ? SbThumbHi : SbThumb;
                using (var sb = new SolidBrush(tc))
                using (var pth = RoundBar.RoundRect(new Rectangle(x, tbY, w, tbH), w / 2))
                    g.FillPath(sb, pth);
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
    ///
    /// 2026-09-23 补**按下态**（用户：「你能不能做按钮的点击特效」）：
    ///   四态齐全 = 常态 / 悬停 / **按下** / 禁用。按下时下沉 1px（`_press` 影响整个绘制矩形 Y+1）
    ///   且底色压暗一档（主按钮 `AccentDown`、次按钮淡灰底、标题栏更深的灰）。
    ///   与网页侧 `button:active{transform:translateY(var(--btn-lift)) scale(...)}` 是**同一套语言**
    ///   —— 网页那边靠 CSS transform，这边靠 OnPaint 里挪矩形，视觉结果对齐。
    ///   另加**键盘可达的按下反馈**：空格/回车按住时 Button 会置 `Capture`+`_press` 由 OnMouseDown 覆盖，
    ///   键盘路径见 `OnKeyDown/OnKeyUp`（否则纯键盘用户永远看不到按下态）。
    ///
    /// ── 2026-09-23 第二轮补**质感**（用户：「按钮的质感…还有优化空间」）──
    /// 上一版的三个"平"（自评，与 Meta 的按钮逐项对比得出）：
    ///   ① **圆角 8 偏方**：Meta 按钮的圆角≈高度的 **1/3 ~ 1/2**（34 高 ⇒ 10~12）。
    ///      8px 在 34 高的按钮上只占 24%，看起来还是"圆角矩形"而不是"胶囊"。
    ///   ② **没有高光**：纯色填充 ⇒ 像一张色纸。Meta 的实心按钮顶部有一条**极淡的白色内高光**
    ///      （≈8% 白）—— 它给"这是个可按的凸起"提供了唯一的立体线索（阴影之外）。
    ///   ③ **没有阴影**：`Primary` 只是"另一个颜色的方块"，与次按钮**浮起层级相同**。
    ///      Meta 的主按钮有极克制的 `0 1px 2px rgba(0,0,0,.08)` ⇒ 轻轻离开纸面。
    ///
    /// 本轮修法（三条，都是**加法**，不改任何调用点，也不动既有配色 token）：
    ///   · `Radius` 默认 8 → **10**（可用 `Radius` 字段或 `PillRadius` 覆写）；
    ///   · **内高光**：仅在 `Primary` 且非按下时画，顶部 50% 高度、白色 22 α 的渐变 ——
    ///     ⚠️ 只在**按下为假**时画：按下时高光消失（真实世界"压进去就没有顶光了"）；
    ///   · **阴影**：在按钮矩形**外面**下移 1px 画一层半透明黑（用 `PathGradientBrush` 太重，
    ///     这里用"逐层描边"最省：1px 偏移 + α=14 的黑描边，视觉上就是 1px 软阴影）。
    ///     只在 `Primary` 上画 —— 次按钮保持"平躺在卡片上"的观感，层级才有区分。
    public class RoundButton : Button
    {
        public bool Primary = false;
        /// 圆角半径。默认 10（Meta 观感）；`PillRadius` 为 true 时自动取高度一半（胶囊）。
        public int Radius = 10;
        /// 胶囊按钮（圆角 = 高度一半）。用于"确定/取消"这类短文字按钮。
        public bool PillRadius = false;
        bool _hover;
        bool _press;
        public RoundButton()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            BackColor = StyleKit.Bg;
            Font = StyleKit.Ui(StyleKit.TextScale.Body, FontStyle.Regular);
            Cursor = Cursors.Hand;
        }
        bool InTitleBar { get { return (Parent is CardPanel) && (((CardPanel)Parent).Dock == DockStyle.Top); } }
        int UseRadius
        {
            get
            {
                int h = Math.Max(2, Height - 1);
                return PillRadius ? h / 2 : Math.Min(Radius, h / 2);
            }
        }
        protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { _hover = false; _press = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { if (e.Button == MouseButtons.Left) { _press = true; Invalidate(); } base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { _press = false; Invalidate(); base.OnMouseUp(e); }
        // 键盘路径（空格 / 回车按住）：不补的话纯键盘用户看不到任何按下反馈
        protected override void OnKeyDown(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Space || e.KeyCode == Keys.Enter) { _press = true; Invalidate(); }
            base.OnKeyDown(e);
        }
        protected override void OnKeyUp(KeyEventArgs e) { _press = false; Invalidate(); base.OnKeyUp(e); }
        protected override void OnLostFocus(EventArgs e) { _press = false; _hover = false; Invalidate(); base.OnLostFocus(e); }
        protected override void OnEnabledChanged(EventArgs e) { _press = false; Invalidate(); base.OnEnabledChanged(e); }
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            Color back = (Parent != null) ? Parent.BackColor : StyleKit.Bg;
            using (var bb = new SolidBrush(back)) g.FillRectangle(bb, ClientRectangle);
            bool tb = InTitleBar;
            // 按下态只对**可用**按钮生效；禁用时忽略（防"看起来能按"）
            bool down = _press && Enabled;
            Color fill, ink, border;
            if (!Enabled)
            {
                // 禁用：去饱和 + 去掉一切凸起线索（高光/阴影都不画）⇒ 一眼看出"按不动"
                fill = Color.FromArgb(238, 242, 248); ink = StyleKit.Muted; border = StyleKit.Line;
            }
            else if (tb) { fill = down ? Color.FromArgb(219, 226, 236) : (_hover ? Color.FromArgb(230, 235, 243) : back); ink = (_hover || down) ? StyleKit.Ink : StyleKit.Ink3ok; border = fill; }
            else if (Primary) { fill = down ? StyleKit.AccentDown : (_hover ? StyleKit.AccentHi : StyleKit.Accent); ink = Color.White; border = fill; }
            else { fill = down ? Color.FromArgb(228, 236, 250) : (_hover ? Color.FromArgb(240, 245, 253) : StyleKit.Card); ink = StyleKit.Ink; border = down ? StyleKit.AccentSoft : (_hover ? StyleKit.AccentSoft : StyleKit.Line); }
            // 按下时整体下沉 1px（矩形下移，按钮高度不变 ⇒ 视觉上像被按进纸面）
            int dy = down ? 1 : 0;
            int rad = UseRadius;
            Rectangle r = new Rectangle(0, dy, Width - 1, Height - 1 - dy);

            // ── ① 阴影（仅主按钮、仅不按下时）：在按钮矩形下方 1px 处描一圈极淡的黑。
            //    用"描边"而不是"模糊"是刻意的：无 GDI+ 模糊的廉价近似，
            //    而 1px 硬阴影在 96dpi 下**恰好**呈现为"1px 软边"（人眼分辨不出它与真模糊的差别）。
            if (Enabled && Primary && !down)
            {
                using (var sp = new Pen(Color.FromArgb(28, 0, 0, 0), 1f))
                using (var pth = RoundBar.RoundRect(new Rectangle(0, dy + 1, Width - 1, Height - 1 - dy), rad))
                    g.DrawPath(sp, pth);
            }

            using (var pth = RoundBar.RoundRect(r, rad))
            using (var fb = new SolidBrush(fill)) g.FillPath(fb, pth);

            // ── ② 内高光（仅主按钮、仅不按下时）：顶部 45% 高度的一条白色极淡渐变。
            //    做法：把高光区裁成"圆角矩形的上半 + 与圆角同形"，再用 LinearGradientBrush 上白下透。
            //    ⚠️ 必须 `SetClip` 到按钮外轮廓，否则高光会溢到圆角外面（看起来像"按钮漏光"）。
            if (Enabled && Primary && !down)
            {
                Region old = g.Clip;
                try
                {
                    using (var cp = RoundBar.RoundRect(r, rad)) g.SetClip(cp);
                    Rectangle hi = new Rectangle(r.X, r.Y, r.Width, Math.Max(2, r.Height * 45 / 100));
                    using (var lg = new LinearGradientBrush(hi,
                        Color.FromArgb(56, 255, 255, 255), Color.FromArgb(0, 255, 255, 255),
                        LinearGradientMode.Vertical))
                        g.FillRectangle(lg, hi);
                }
                catch { }
                finally { g.Clip = old; }
            }

            if (!tb)
                using (var pth = RoundBar.RoundRect(r, rad))
                using (var pb = new Pen(border)) g.DrawPath(pb, pth);

            // 文本走统一入口（自动 ClearType + 中英混排选字体）
            StyleKit.DrawText(g, Text, Font, r, ink,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
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
                        StyleKit.DrawText(g, (i + 1).ToString(), StyleKit.Ui(StyleKit.TextScale.Micro, FontStyle.Bold), box,
                            cur ? Color.White : StyleKit.Ink3ok,
                            TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
                    }
                    // 语义字色（2026-09-23 修）：原来"已完成"用 `Ink`、"进行中"用 `Accent`(3.6:1 不达标)、
                    //   "待办"用 `Muted`(2.78:1)。三档里有两档不达标 —— 而步骤文字恰恰是**最需要看清**的。
                    //   ⇒ 已完成 `Ink`(13.9) · 进行中 `Link`(4.6 达标且仍是蓝色语义) · 待办 `Ink3ok`(5.6 达标)。
                    //     待办不再用 Muted：那是"占位"的档位，步骤名不是占位信息，只是"还没轮到"。
                    Color ink = done ? StyleKit.Ink : (cur ? StyleKit.Link : StyleKit.Ink3ok);
                    Font f = cur ? StyleKit.Ui(StyleKit.TextScale.Body, FontStyle.Bold) : Font;
                    StyleKit.DrawText(g, _steps[i], f,
                        new Rectangle(cx + 20, cy - rowH / 2, Math.Max(10, Width - cx - 22), rowH), ink,
                        TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
                }
            }
        }
    }
}
