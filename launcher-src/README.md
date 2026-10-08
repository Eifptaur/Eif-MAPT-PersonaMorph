# 启动器源码（群相.exe / 一键关闭.exe）

> 2026-09-13（W6）移到这里：**exe 是随包发布的产物，源码必须在版本管理里**，
> 之前放在一个被 .gitignore 忽略的开发目录里，等于"发布物没有源"。
>
> ⚠️ 启动器已改名 **群相.exe**（旧名 `一键启动.exe` 仅用于识别老安装，别再新建该名字的产物）。

## 安装向导（四页）

`launcher.cs` 里的 `LauncherForm` 就是**安装器本体**（`群相.exe` 双击即安装向导）：

| 页 | 名称 | 内容 | 主按钮 |
|---|---|---|---|
| P1 | 欢迎 | 接下来会做什么（准备运行环境 → 取运行库 → 自检） | 下一步 |
| P2 | 安装位置 | 显示 `Root` 路径 ＋ "便携版"说明（不提供改目录） | 上一步 / 下一步 |
| P3 | 正在安装 | 原四步进度（`Steps` / `StepDetail` / `LogView`） | （安装中「关闭」置灰） |
| P4 | 完成 | 说明 ＋ 勾选「在桌面创建快捷方式」 | 立即启动 / 上一步 |

- **页签**走自绘 `WizardTabs`（`stylekit.cs`）——⛔ 不许用系统 `TabControl`（会被判据 ①c 扫红）。
- **勾选框**走自绘 `CardCheck`——同理不许用系统 `CheckBox`。
- 切页只有一处入口：`GoPage(int)`；`--shot` 逐页出图靠 `ProbePage(int)`。
- **静默档（`FastPathReady` 三条件全满足）直接进 P3**，不许停在欢迎页（否则后台线程等不到 `Shown`，死锁）。
- **"一点就能直接打开"** 有两处落点：P4 的默认勾选建桌面快捷方式 ＋ 静默档跳过整个向导。

## 编译（csc，Windows 自带 .NET Framework 编译器）

## 编译（csc，Windows 自带 .NET Framework 编译器）

```powershell
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

# 群相.exe（W6 起需要引用 WebView2 两个程序集；WebView2Loader.dll 必须在 exe 同目录）
# W6b 起 stylekit.cs 是**两个 exe 共用**的（StyleKit + RoundBar + RoundButton + StepList + WizardTabs + CardCheck），两个编译命令都要带上它
# ⛔ 2026-10-02 修（B 赛道 §G.1 N51-5）：`launcher.cs` **还引用了另外两个文件里的类型**，
#   漏掉就会 **CS0246（找不到类型或命名空间）**、照本文档编译**编不出来**：
#     · `wingliphs.cs`  → `GlyphButton`（自绘字形按钮，launcher.cs 里用了 8 处）
#     · `webview2guide.cs` → `WebView2Guide` / `WebView2MissingForm`（launcher.cs 里用了 3 处）
#   ⚠️ `close.cs` **不用**这两个（实测引用数 = 0）⇒ 关闭器那条命令保持原样，别顺手加上去。
& $csc /nologo /target:winexe /optimize+ /win32icon:assets\exe.ico `
  /r:lib\Microsoft.Web.WebView2.Core.dll /r:lib\Microsoft.Web.WebView2.WinForms.dll `
  /out:群相.exe launcher-src\launcher.cs launcher-src\stylekit.cs `
  launcher-src\wingliphs.cs launcher-src\webview2guide.cs

# 一键关闭.exe（只用 StyleKit，**不带** wingliphs / webview2guide）
& $csc /nologo /target:winexe /optimize+ /win32icon:assets\exe.ico /r:System.Management.dll `
  /out:一键关闭.exe launcher-src\close.cs launcher-src\stylekit.cs
```

预期只出 3 个"未使用变量/字段"警告（`ex` / `done` / `_asking`），**0 error**。
其他警告（如 CS0414「已赋值但从未使用」）出现即为真问题，**别拿"预期"当挡箭牌**。

> **怎么一眼自查"该带哪几个 .cs"**（改完 launcher.cs 后跑一遍，防再漏）：
> `grep -o "GlyphButton\|WebView2Guide\|WebView2MissingForm\|StyleKit" launcher-src\launcher.cs | sort -u`
> —— 出现的每一个类型，都要能在**这条编译命令列出的 .cs** 里找到定义。

## 三个自检入口（W6 新增，都在 一键启动.exe 里）

| 参数 | 作用 | 用途 |
|---|---|---|
| `--console <url>` | 用**我们自己的 WebView2 窗口**打开控制台（无边框 + 自绘标题栏 + 圆角） | Python 侧 `scripts/onestart.py` 调用它，不再开浏览器；WebView2 不可用时自动回退浏览器 |
| `--shot <dir>` | 把全部弹窗**离屏**渲染成 PNG（不显示、不抢焦点） | 视觉验收证据（出到指定目录；每行带「前台未变」与**像素色数**，≤2 即空白图）。⚠️ `console.png` 在探针里必然是空的（WebView2 内容要真初始化） |
| `--dlgprobe` | 打印弹窗清单：窗体名 / 尺寸 / 控件数 / 边框样式 / 底色 / 每个子控件 + **每个 Label 的 `need=WxH`**（超过控件高度打 `CLIP`） | 机械自检（主题是否生效、还有没有系统边框、**文字有没有被截断**） |
| `一键关闭.exe --shot <dir>` | 关闭器结果窗离屏出图（`close.png`） | 关闭器外观证据 |

### `--shot` 的 launcher 出图清单（共 5 张）

| 文件名 | 对应页 |
|---|---|
| `launcher.png` | P3「正在安装」（默认首页） |
| `launcher-welcome.png` | P1「欢迎」 |
| `launcher-location.png` | P2「安装位置」 |
| `launcher-step3.png` | P3「正在安装」带步骤态 |
| `launcher-done.png` | P4「完成」 |

> 出图靠 `ProbePage(int)` 逐页切；出完必须回到 P3，否则 `--dlgprobe` 的 `CLIP` 计数会读到非预期页面。

## 硬规矩

1. **0 系统 MessageBox**：所有提示都走自绘窗体（`LauncherForm` / `BusyForm` / `AskForm` / `NoticeForm` / `ConsoleForm`）。
   机械自检：`grep -c "MessageBox\.Show" launcher-src\*.cs` 必须为 **0**。
2. **高 DPI 清晰**：`StyleKit.Prep()` 里 `SetThreadDpiAwarenessContext(-4)`（PerMonitorV2）+ `SetErrorMode`（关掉系统崩溃弹窗）。
3. **外观只有一处来源**：颜色/字体/圆角/标题栏都在 `StyleKit`；新增窗体一律先 `StyleKit.Apply(this, "标题")`。
4. **WebView2 程序集**：`lib\Microsoft.Web.WebView2.{Core,WinForms}.dll` + **根目录 `WebView2Loader.dll`（必须与 exe 同级）**；
   程序集解析在 `StyleKit.Prep()` 里挂了 `AssemblyResolve`（.NET 默认不探 `lib\` 子目录）。
5. **`StyleKit.Apply(this, "标题")` 必须是构造函数里最后一句**：它会把 `FixedDialog` 改成无边框 + 加自绘标题栏 + 把子控件整体下移 38px。
   在它**之后**再写 `FormBorderStyle = ...` 会让系统标题栏回来，和自绘标题栏叠成两条（W6 遗留，W6b 修）。
6. **出图不许用 `CreateControl()` + `SetWindowPos(SWP_SHOWWINDOW)`**：WinForms 不认为这种窗口 `Visible` ⇒ 子控件全不画，
   PNG 只有底色（W6 那 5 张"渲染成功"的截图其实全是空白）。用 `StyleKit.CaptureOffscreen()`（`WS_EX_NOACTIVATE` + `Show()` + `DrawToBitmap`）。
7. **文字截断是自检、不是观感**：改完任何窗体跑 `--dlgprobe`，`CLIP` 计数必须是 **0**。
8. **改改名的东西，主判据要用运行时真值**：例如"桌面有没有快捷方式"以 `Application.ExecutablePath` 为主判据，写死的旧文件名只能作**候选兜底**——
   `Ui.ShortcutInDir` 曾写死 `一键启动.exe`，改名后永远找不到 ⇒ 每次启动都重复提问/重复建 lnk。（见 判据 `launcher_wizard_selftest.py` ⑥）
