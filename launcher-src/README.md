# 启动器源码（一键启动.exe / 一键关闭.exe）

> 2026-09-13（W6）从 `_scratch/` 移到这里：**exe 是随包发布的产物，源码必须在版本管理里**，
> 之前放在 `_scratch/`（被 .gitignore 忽略）等于"发布物没有源"。

## 编译（csc，Windows 自带 .NET Framework 编译器）

```powershell
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

# 一键启动.exe（W6 起需要引用 WebView2 两个程序集；WebView2Loader.dll 必须在 exe 同目录）
& $csc /nologo /target:winexe /optimize+ /win32icon:assets\exe.ico `
  /r:lib\Microsoft.Web.WebView2.Core.dll /r:lib\Microsoft.Web.WebView2.WinForms.dll `
  /out:一键启动.exe launcher-src\launcher.cs

# 一键关闭.exe
& $csc /nologo /target:winexe /optimize+ /win32icon:assets\exe.ico /r:System.Management.dll `
  /out:一键关闭.exe launcher-src\close.cs
```

预期只出 3 个"未使用变量/字段"警告（`ex` / `done` / `_asking`），**0 error**。

## 三个自检入口（W6 新增，都在 一键启动.exe 里）

| 参数 | 作用 | 用途 |
|---|---|---|
| `--console <url>` | 用**我们自己的 WebView2 窗口**打开控制台（无边框 + 自绘标题栏 + 圆角） | Python 侧 `scripts/onestart.py` 调用它，不再开浏览器；WebView2 不可用时自动回退浏览器 |
| `--shot <dir>` | 把全部弹窗**离屏**渲染成 PNG（不出现在屏幕上、不抢焦点） | 视觉验收证据（`_scratch/w6-shots/*.png`） |
| `--dlgprobe` | 打印弹窗清单：窗体名 / 尺寸 / 控件数 / 边框样式 / 底色 / 每个子控件 | 机械判据（可核对主题是否生效、是否还有系统边框） |

## 硬规矩

1. **0 系统 MessageBox**：所有提示都走自绘窗体（`LauncherForm` / `BusyForm` / `AskForm` / `NoticeForm` / `ConsoleForm`）。
   机械判据：`grep -c "MessageBox\.Show" launcher-src\*.cs` 必须为 **0**。
2. **高 DPI 清晰**：`StyleKit.Prep()` 里 `SetThreadDpiAwarenessContext(-4)`（PerMonitorV2）+ `SetErrorMode`（关掉系统崩溃弹窗）。
3. **外观只有一处来源**：颜色/字体/圆角/标题栏都在 `StyleKit`；新增窗体一律先 `StyleKit.Apply(this, "标题")`。
4. **WebView2 程序集**：`lib\Microsoft.Web.WebView2.{Core,WinForms}.dll` + **根目录 `WebView2Loader.dll`（必须与 exe 同级）**；
   程序集解析在 `StyleKit.Prep()` 里挂了 `AssemblyResolve`（.NET 默认不探 `lib\` 子目录）。
