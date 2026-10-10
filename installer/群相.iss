; =====================================================================
; 群相 —— 安装程序脚本（Inno Setup 6）
;
; 用途：把「含依赖包」（offline/ 里的嵌入式 Python + 199 MB 轮子 + 全部程序文件）
;       封成**一个 setup.exe**：双击 → 铺到用户机器 → 建开始菜单/桌面快捷方式
;       → 注册卸载项 → 装完拉起「群相.exe」（首启准备运行环境）。
;
; ⛔ 三条硬口径（与用户要求一一对应）：
;   ①  一个 exe、双击就装 —— 本脚本产出的就是单文件 setup.exe；
;   ②  全部依赖在 exe 内部 —— [Files] 把整个 payload 目录（含 offline/）全塞进去，
;       运行时**零联网**（不下载任何依赖）；
;   ③  可足够大 —— 不做体积限制；压缩用 lzma2/max。
;
; ⛔ 免 UAC：PrivilegesRequired=lowest + 默认装到 %LOCALAPPDATA%\QunXiang
;      （普通群主一弹 UAC 就走掉一半，所以默认不申请管理员）。
; ⭐ **默认目录名用英文 `QunXiang`**（2026-10-10 用户口径：安装路径要英文）。
;    显示名仍用中文「群相」（AppName / 快捷方式 / 卸载项都不变），只有**默认路径**是英文。
; ⛔ **不许在界面上写"只能选英文路径"**：✅ 中文路径是**支持**的
;    （`scripts/cmd_entry_selftest.py` 有「非 ASCII 路径回归 P8」判据钉死：
;     中文目录下仍走自带 Python、**不许**回落系统 py）。
;    ⇒ 默认给英文是"图省事"，**不是**限制；用户想改到中文目录，随便改。
;
; 编译（由 scripts/make_installer.py 调用，不要手改路径）：
;   iscc installer\群相.iss /DAppVersion=2026.10.09 /DPayloadDir=<解压好的目录> /DOutDir=<出包目录>
; =====================================================================

#define AppName "群相"
#define AppExe "群相.exe"
#define AppId "{{41AE5AB6-CFCF-4F73-8244-9BC5E2BEFB2C}"

; 下面三个由构建器用 /D 传进来；单独点编译时用这里的默认值兜底
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef PayloadDir
  #define PayloadDir "build\payload"
#endif
#ifndef OutDir
  #define OutDir "build"
#endif

[Setup]
; AppId 固定不变 ⇒ 覆盖安装能识别成"升级"、不会装出两份
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\QunXiang
DefaultGroupName=群相
; ⛔ 不装到 Program Files、不申请管理员 ⇒ 全程无 UAC 弹窗
PrivilegesRequired=lowest
; ⛔ **不要**写 `PrivilegesRequiredOverridesAllowed=dialog`：那个选项会让安装程序
;    启动时**弹一个"为所有用户装 / 只为我装"的选择框** —— 实测把 /VERYSILENT 都顶掉了，
;    静默装变成弹窗（2026-10-09 踩过）。我们的口径本来就是"装到当前用户目录、免 UAC"，
;    不需要让用户选模式。
; ⛔ **目录页必须关掉**（`DisableDirPage=yes`）—— 2026-10-10 踩过的**硬 bug**：
;    下面 `[Code]` 里 `WizardForm.Visible := False` 把 Inno 窗口**整个藏了**
;    （`InitializeWizard` + `CurPageChanged` 双重隐藏）。开着目录页 ⇒ 页面出来了
;    但**用户看不见、点不到「下一步」** ⇒ 安装永远卡在那一步。
;    而且用户点名：**看到的必须是启动器那套自绘四页**（`launcher-src/`，含「安装位置」页），
;    「那个窗口是 inno 的默认的，不是我们设计的」⇒ 本 exe 只管**静默铺文件**，
;    铺完拉起 `群相.exe`，界面交给自绘向导。
; ⛔ Inno 自带向导的页面**全部关掉**（含目录页）：欢迎/就绪/完成页一个都不许冒出来。
DisableProgramGroupPage=yes
OutputDir={#OutDir}
OutputBaseFilename=群相-Setup-{#AppVersion}
; 塞 280 MB 级依赖。⚠️ 不用 ultra64/max：包内大头是 `offline\wheels\*.whl`，
;   而 whl 本身就是 zip（已压缩）⇒ 再压几乎零收益、却要多花十几分钟（实测过）。
;   normal 在"体积几乎一样"的前提下快得多。
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupLogging=yes
; ⛔ **装之前自动把正在跑的群相关掉**，而不是弹一句"请先关闭再安装"让用户自己动手。
;   `CloseApplications=yes`（默认值，这里显式写出来是为了记下意图）走 Windows 的
;   Restart Manager：谁占着 `{app}` 下要替换的文件，就关掉谁 —— 启动器与它拉起的
;   `runtime\python\python.exe` 子进程都占着东西，所以两个都会被收掉。
; ⛔ `RestartApplications` **必须显式设 no**：它默认是 yes，会让 Inno 把"它自己关掉的"
;   程序再拉起来；而我们在 `[Run]` 里本来就要启动一次 ⇒ 会同时冒出两个群相。
; ⛔ 别加 `AppMutex`：那东西只在安装/卸载启动时**检测**并弹"请先关闭应用"，用户得手动关；
;   而我们要的是**自动关**（见上面 CloseApplications 与 [UninstallRun]），加它反而添麻烦。
CloseApplications=yes
RestartApplications=no
; ⛔ 不显示"选择是否创建快捷方式"那种系统页之外的多余页；快捷方式走 [Tasks]
AllowNoIcons=yes
; ⛔ Inno 自带向导的页面**全部关掉** —— 这个 exe 不负责"给用户看界面"，
;    它只做"把包铺到用户机器上"这一件事；用户看到的界面是装完后拉起的
;    `群相.exe`（`launcher-src/` 那套自绘四页向导：欢迎 / 安装位置 / 正在安装 / 完成）。
;    关页 + 下面的 [Code] 隐藏窗口，两者一起才能做到"双击后看不到系统风格窗口"。
DisableWelcomePage=yes
DisableDirPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
DisableStartupPrompt=yes
; ⭐ **图标必须是我们的**（2026-10-10 用户点名："setup 的图标不是我们的图标"）。
;    与启动器同源：`launcher.cs` 多处 `assets\app.ico`（`Icon.ExtractAssociatedIcon`）。
SetupIconFile=..\assets\app.ico

[Languages]
; ChineseSimplified.isl 由构建器保证存在（放在本文件同目录）——
; Inno Setup 6 官方安装包**不自带**中文，缺了会直接编译失败
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
; ⛔ 整个 payload 全进去（含 offline\python 与 offline\wheels ⇒ 运行时不用联网）
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\群相"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\群相"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; 装完拉起启动器（它负责"准备运行环境 → 自检"那四步）
Filename: "{app}\{#AppExe}"; Description: "立即启动群相（首次会准备运行环境）"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; ⛔ 只删**程序自己生成/解出来的东西**（白名单式逐条列），
;    **绝不加** `Type: filesandordirs; Name: "{app}"` 全删 —— 那会把用户的
;    data\（人设与记忆） config.json（配置） logs\ wechatauto_logs\ 报告\
;    一起删掉。那些是**用户的东西**，卸载也不该丢；卸载完文件夹还在，
;    用户想彻底清理就自己删（见 [UninstallRun] 末尾那条说明）。
;
; 为什么这几样必须显式列：Inno 卸载时**只删它自己在 [Files] 里装进去的文件**，
; 之后还要把"空了"的目录删掉。运行期生成的东西不在安装清单里 ⇒ 一个都不删，
; 而且它们的父目录因此非空 ⇒ 整棵程序目录树也留着。
Type: filesandordirs; Name: "{app}\runtime"
; ↑ 程序按 `offline\` 里的运行时 zip 解出来的依赖（几百 MB）—— 不删就是最大的一块残留。
;   删了下次重装重解一遍（约 30 秒，用包内本地轮子，不联网）。
Type: filesandordirs; Name: "{app}\releases"
; ↑ 自更新在 `versioned` 布局下的版本目录（见 `agent/update_apply.py::releases_root`）。
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\agent\__pycache__"
Type: filesandordirs; Name: "{app}\scripts\__pycache__"
Type: filesandordirs; Name: "{app}\ui_qt\__pycache__"
; ↑ 运行期生成的字节码缓存，散在这几个子目录里（[Files] 里没有它们）。

[UninstallRun]
; 卸载前先收进程（**顺序不能反**）：
; ① 首选走我们自己的「一键关闭」无界面模式 —— 它的判据是
;    "可执行文件在**本安装目录下**"，所以能连启动器拉起的
;    `runtime\python\python.exe` 子进程一起收掉（那些子进程占着 `runtime\` 与 `*.pyc`，
;    不收掉 Inno 就删不动、卸载留下整棵树）。
;    ⛔ 绝不能改成 `taskkill /f /im python.exe`：那会把用户**自己跑的** python 一起杀。
Filename: "{app}\一键关闭.exe"; Parameters: "--silent"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "CloseOursFirst"
; ② 兜底（老版本装的包可能没有 ①，或它已被用户删掉）：仍按名字收掉启动器，
;    `/t` ＝连带**子进程树**，把它的 python 子进程一并带走。
;    ⚠️ `RunOnceId` 必须给：不给的话 Inno 编译时会告警，且多次卸载场景下这条会重复执行。
Filename: "{cmd}"; Parameters: "/c taskkill /f /im {#AppExe} /t"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "KillRunningApp"
;
; ⛔ 卸载**不删**用户数据：data\（人设与记忆） config.json（配置） logs\ wechatauto_logs\ 报告\
;    一律留在 `{app}` 里。想彻底清理的用户删掉那个文件夹即可 —— 宁可留一份让人自己删，
;    也不替人删掉不可恢复的东西。

[Code]
// ⛔ 把 Inno 自带的向导窗口**藏起来**。
//
//   为什么必须这么做：这个 exe 的**唯一职责是"把文件铺到用户机器上"**；
//   用户看到的界面是装完后拉起的 `群相.exe` —— 那是 `launcher-src/` 里自绘的四页向导
//   （欢迎 / 安装位置 / 正在安装 / 完成，自绘页签与勾选框，不用系统 TabControl/CheckBox）。
//   若 Inno 的窗口露出来，用户看到的就是**两个风格完全不同的安装流程**。
//
//   上面那几个 `Disable*Page` 只是**去掉页面**，窗口本体还在（会留下一个空框），
//   所以这里必须再显式隐藏一次；`CurPageChanged` 里再兜一次，是因为 Inno 会在
//   某些流程节点重新显示窗口。
procedure InitializeWizard();
begin
  WizardForm.Visible := False;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  // 未使用的入参：Inno 的 Pascal 编译器不会因此报错，留着是为了符合回调签名
  WizardForm.Visible := False;
end;
