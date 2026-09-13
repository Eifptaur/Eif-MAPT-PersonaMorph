# 启动器源码（一键启动.exe / 一键关闭.exe）

> 2026-09-13（W6）从 `_scratch/` 移到这里：**exe 是随包发布的产物，源码必须在版本管理里**，
> 之前放在 `_scratch/`（被 .gitignore 忽略）等于"发布物没有源"。

## 编译（csc，Windows 自带 .NET Framework 编译器）

```powershell
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

# 一键启动.exe（W6 起需要引用 WebView2 两个程序集；WebView2Loader.dll 必须在 exe 同目录）
# W6b 起 stylekit.cs 是**两个 exe 共用**的（StyleKit + RoundBar + RoundButton + StepList），两个编译命令都要带上它
& $csc /nologo /target:winexe /optimize+ /win32icon:assets\exe.ico `
  /r:lib\Microsoft.Web.WebView2.Core.dll /r:lib\Microsoft.Web.WebView2.WinForms.dll `
  /out:一键启动.exe launcher-src\launcher.cs launcher-src\stylekit.cs

# 一键关闭.exe
& $csc /nologo /target:winexe /optimize+ /win32icon:assets\exe.ico /r:System.Management.dll `
  /out:一键关闭.exe launcher-src\close.cs launcher-src\stylekit.cs
```

预期只出 3 个"未使用变量/字段"警告（`ex` / `done` / `_asking`），**0 error**。

## 三个自检入口（W6 新增，都在 一键启动.exe 里）

| 参数 | 作用 | 用途 |
|---|---|---|
| `--console <url>` | 用**我们自己的 WebView2 窗口**打开控制台（无边框 + 自绘标题栏 + 圆角） | Python 侧 `scripts/onestart.py` 调用它，不再开浏览器；WebView2 不可用时自动回退浏览器 |
| `--shot <dir>` | 把全部弹窗**离屏**渲染成 PNG（不出现在屏幕上、不抢焦点） | 视觉验收证据（`_scratch/w6b-shots/*.png`；每行带「前台未变」与**像素色数**，≤2 即空白图）。⚠️ `console.png` 在探针里必然是空的（WebView2 内容要真初始化） |
| `--dlgprobe` | 打印弹窗清单：窗体名 / 尺寸 / 控件数 / 边框样式 / 底色 / 每个子控件 + **每个 Label 的 `need=WxH`**（超过控件高度打 `CLIP`） | 机械判据（主题是否生效、还有没有系统边框、**文字有没有被截断**） |
| `一键关闭.exe --shot <dir>` | 关闭器结果窗离屏出图（`close.png`） | 关闭器外观证据 |

## 硬规矩

1. **0 系统 MessageBox**：所有提示都走自绘窗体（`LauncherForm` / `BusyForm` / `AskForm` / `NoticeForm` / `ConsoleForm`）。
   机械判据：`grep -c "MessageBox\.Show" launcher-src\*.cs` 必须为 **0**。
2. **高 DPI 清晰**：`StyleKit.Prep()` 里 `SetThreadDpiAwarenessContext(-4)`（PerMonitorV2）+ `SetErrorMode`（关掉系统崩溃弹窗）。
3. **外观只有一处来源**：颜色/字体/圆角/标题栏都在 `StyleKit`；新增窗体一律先 `StyleKit.Apply(this, "标题")`。
4. **WebView2 程序集**：`lib\Microsoft.Web.WebView2.{Core,WinForms}.dll` + **根目录 `WebView2Loader.dll`（必须与 exe 同级）**；
   程序集解析在 `StyleKit.Prep()` 里挂了 `AssemblyResolve`（.NET 默认不探 `lib\` 子目录）。
5. **`StyleKit.Apply(this, "标题")` 必须是构造函数里最后一句**：它会把 `FixedDialog` 改成无边框 + 加自绘标题栏 + 把子控件整体下移 38px。
   在它**之后**再写 `FormBorderStyle = ...` 会让系统标题栏回来，和自绘标题栏叠成两条（W6 遗留，W6b 修）。
6. **出图不许用 `CreateControl()` + `SetWindowPos(SWP_SHOWWINDOW)`**：WinForms 不认为这种窗口 `Visible` ⇒ 子控件全不画，
   PNG 只有底色（W6 那 5 张"渲染成功"的截图其实全是空白）。用 `StyleKit.CaptureOffscreen()`（`WS_EX_NOACTIVATE` + `Show()` + `DrawToBitmap`）。
7. **文字截断是判据、不是观感**：改完任何窗体跑 `--dlgprobe`，`CLIP` 计数必须是 **0**。
