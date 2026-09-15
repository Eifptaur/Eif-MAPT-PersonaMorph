// wingliphs.cs —— 窗口顶栏三个按钮的**自绘字形**（2026-09-15 用户：「减号、全屏、叉号的样子很奇怪呀，不统一」）
//
// 为什么不能继续用文本按钮：`—` / `□` / `❐` / `✕` 来自不同字形簇，笔画粗细、基线、视觉重心都不一样，
// 摆在同一排必然"看着怪"。自研显示层口径也要求图标自己画（不用系统/字体字形）。
//
// 做法：一个 `GlyphButton : Control`，三种字形用**同一支笔**（同粗细、同尺寸、同悬停底色）画出来；
// `Text` 保留成**语义标记**（min/max/restore/close）——它不参与绘制，只给探针（`--dlgprobe`）与判据读。
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace WxLauncher
{
    public enum GlyphKind { Min, Max, Restore, Close }

    public class GlyphButton : Control
    {
        GlyphKind _kind = GlyphKind.Max;
        bool _hover, _down;

        public GlyphButton() { SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.SupportsTransparentBackColor, true); }

        public GlyphKind Kind
        {
            get { return _kind; }
            set { _kind = value; Text = value.ToString().ToLowerInvariant(); Invalidate(); }
        }

        protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { _hover = false; _down = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { _down = true; Invalidate(); base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { _down = false; Invalidate(); base.OnMouseUp(e); }

        /// 自己的圆角矩形（不依赖别的类的内部方法；半径夹在边长一半以内）
        static GraphicsPath RoundedRect(Rectangle r, int radius)
        {
            GraphicsPath p = new GraphicsPath();
            int d = Math.Max(1, Math.Min(radius, Math.Min(r.Width, r.Height) / 2));
            p.AddArc(r.X, r.Y, d * 2, d * 2, 180, 90);
            p.AddArc(r.Right - d * 2, r.Y, d * 2, d * 2, 270, 90);
            p.AddArc(r.Right - d * 2, r.Bottom - d * 2, d * 2, d * 2, 0, 90);
            p.AddArc(r.X, r.Bottom - d * 2, d * 2, d * 2, 90, 90);
            p.CloseFigure();
            return p;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            // 悬停/按下底色：三个按钮**同一套**（圆角 6、同一透明度档），这样一排看过去才一致
            Color bg = _down ? Color.FromArgb(46, 255, 255, 255) : (_hover ? Color.FromArgb(24, 255, 255, 255) : Color.Transparent);
            if (bg != Color.Transparent)
            {
                using (GraphicsPath p = RoundedRect(new Rectangle(0, 0, Width - 1, Height - 1), 6))
                using (SolidBrush b = new SolidBrush(bg)) g.FillPath(b, p);
            }
            // 同一支笔：粗细按控件高矮等比（DPI 无关），颜色取同一档
            float pen = Math.Max(1.2f, Height / 18f);
            Color ink = _kind == GlyphKind.Close ? Color.FromArgb(232, 120, 120) : StyleKit.Sub;
            using (Pen p = new Pen(ink, pen))
            {
                p.StartCap = LineCap.Round; p.EndCap = LineCap.Round;
                // 字形尺寸统一：正方形工作区，边长 ≈ 控件高的一半
                float s = Math.Max(8f, Math.Min(Width, Height) / 2.6f);
                float cx = Width / 2f, cy = Height / 2f;
                float half = s / 2f;
                switch (_kind)
                {
                    case GlyphKind.Min:      // 一条横线（与叉号/方框同宽）
                        g.DrawLine(p, cx - half, cy, cx + half, cy);
                        break;
                    case GlyphKind.Max:      // 一个方框
                        g.DrawRectangle(p, cx - half, cy - half, s, s);
                        break;
                    case GlyphKind.Restore:  // 叠两层方框（还原）
                        g.DrawRectangle(p, cx - half, cy - half + 2, s - 2, s - 2);
                        g.DrawLine(p, cx - half + 2, cy - half, cx + half, cy - half);
                        g.DrawLine(p, cx + half, cy - half, cx + half, cy + half - 2);
                        break;
                    case GlyphKind.Close:    // 一个叉（与方框同边长、同笔宽）
                        g.DrawLine(p, cx - half, cy - half, cx + half, cy + half);
                        g.DrawLine(p, cx + half, cy - half, cx - half, cy + half);
                        break;
                }
            }
        }
    }
}
