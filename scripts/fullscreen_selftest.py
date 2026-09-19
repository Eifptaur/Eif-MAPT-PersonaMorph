# -*- coding: utf-8 -*-
"""全屏无边框的**源码级判据**（不需要 Windows、不需要起窗口，纯文本断言）。

为什么要有它：`WM_NCCALCSIZE` / `WM_GETMINMAXINFO` 这两处的写法**改错一次就是用户可见的
缺陷**，而且改错之后"编译过、别的判据全绿"——2026-09-16 我连错两轮（先内缩 8px ⇒ 四周露
一圈底色；后干脆不缩 ⇒ 顶栏被切）。所以把"必须怎么算"钉死在判据里，谁改回去就红。

判据（全部读 `launcher-src\\launcher.cs` 源码）：
  1. 有 WM_NCCALCSIZE(0x0083) 分支
  2. 该分支把 rgrc0 设成 **屏幕矩形**（`Bounds`）
  3. **不许**再出现内缩写法（`rgrc0.left +=` / `rgrc0.top +=` —— 那是"露一圈底色"的旧错误）
  4. WM_GETMINMAXINFO(0x0024) 用 **Bounds** 尺寸，**不许**再用工作区（`wa.Width`）
  5. WebView2 的 WindowCloseRequested 被宿主接管（否则点停止窗口永不关）
  6. ConsoleForm 给窗体设了 Icon（否则任务栏用进程默认图标）

跑法：`runtime\\python\\python.exe -X utf8 -u scripts\\fullscreen_selftest.py`
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "launcher-src", "launcher.cs")

passed = 0
failed = 0


def ok(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  OK   %s%s" % (name, ("  [" + detail + "]") if detail else ""))
    else:
        failed += 1
        print("  FAIL %s%s" % (name, ("  [" + detail + "]") if detail else ""))


print("全屏无边框（源码级）判据")
print("")

if not os.path.isfile(SRC):
    print("  FAIL 找不到 launcher-src\\launcher.cs")
    sys.exit(1)

src = io.open(SRC, encoding="utf-8").read()

# 只看无边框窗那一段（ConsoleForm 的 WndProc），避免别处的同名调用误命中
start = src.find("protected override void WndProc(ref Message m)")
end = src.find("protected override void OnLoad", start if start > 0 else 0)
seg = src[start:end] if (start > 0 and end > start) else src

print("── A. WM_NCCALCSIZE：客户区必须钉死到屏幕矩形 ──")
ok("有无边框窗的 WndProc", start > 0)
ok("处理 WM_NCCALCSIZE(0x0083)", "0x0083" in seg)
ok("rgrc0 被设成屏幕矩形（用了 Bounds）", "Bounds" in seg,
   "Screen.FromHandle(Handle).Bounds")
ok("rgrc0 的四个边都取了屏幕矩形", "nc.rgrc0.left = mo.Left" in seg.replace(" ", "")
   or "rgrc0.left=mo.Left" in seg.replace(" ", ""))
ok("**不再内缩**（没有 rgrc0.left += bx 那种旧写法）",
   ("rgrc0.left +=" not in seg) and ("rgrc0.top +=" not in seg),
   "内缩写法＝四周露一圈窗体底色，用户报过的那个边框")

print("")
print("── B. WM_GETMINMAXINFO：最大化尺寸用整屏（真全屏，含任务栏）──")
ok("处理 WM_GETMINMAXINFO(0x0024)", "0x0024" in seg)
ok("ptMaxSize 用屏幕(Bounds)尺寸，不是工作区", "mo.Width" in seg and "wa.Width" not in seg,
   "用工作区＝任务栏还在，与「真全屏」口径不一致")

print("")
print("── C. 关联的两处用户可见行为 ──")
ok("接管 WebView2 的 WindowCloseRequested（点停止才会关窗）",
   "WindowCloseRequested" in src)
ok("ConsoleForm 给窗体设了 Icon（任务栏图标才是我们的）",
   "assets\", \"app.ico\"" in src or 'assets", "app.ico"' in src)

print("")
print("── D. 真全屏：自绘顶栏收起来，鼠标贴屏幕顶端再滑出 ──")
ok("有 SyncBarVisible（全屏时收起顶栏）", "void SyncBarVisible()" in src)
ok("顶栏显隐由 SyncBarVisible 决定", "_bar.Visible = show" in src)
ok("有贴顶检测（只读光标位置，不动鼠标）",
   "Cursor.Position" in src and "hitEdge" in src and "BarPeek.Next" in src,
   "WebView2 会吃掉 MouseMove，只能用定时器读全局光标")
# ⛔ 2026-09-20 修（作者报「最大化后上边栏维持时间太短、点不到最小化」）：显隐判据从"只有贴顶 4px"
#   升级成 `BarPeek`（进入 / 保持 / 宽限三段）。**行为**由 `scripts/peek_probe_selftest.py` 守着
#   （跑 `一键启动.exe --peekprobe` 的四条仿真路径 + 老判据灵敏度对照）；这里只钉住"接线对不对"。
ok("顶栏显隐走 BarPeek 三段判据（进入 / 保持 / 宽限），不是只看顶端那几个像素",
   "BarPeek.Next(_barPeek" in src and "_bar.Height" in src and "BarPeek.EdgeBand" in src,
   "见 launcher-src/launcher.cs 的 BarPeek")
ok("老那一条「只看顶端 4px」的写法已经清掉（它正是「点不到最小化」的成因）",
   "bool atTop = (c.Y <= mo.Top + 4)" not in src)

print("")
print("全屏判据：%d 通过 / %d 失败" % (passed, failed))
sys.exit(1 if failed else 0)
