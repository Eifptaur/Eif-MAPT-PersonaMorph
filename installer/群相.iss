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
;    中文目录名会让部分依赖（尤其离线 wheel 解压与便携运行时）踩路径编码的坑；
;    显示名仍用中文「群相」（AppName / 快捷方式 / 卸载项都不变），只有**路径**是英文。
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
; ⭐ **目录页开着**（`DisableDirPage=no`）：**要让用户自己选装到哪**（2026-10-10 用户口径）。
;   理由：装到哪是用户自己的事，由不得我们替他定。
;   ⇒ 为什么开 Inno 这页而不是改启动器 P2：P2 在 `launcher-src/launcher.cs`（C#）里，
;     而**本环境拦 C# 编译器**（实测 csc/dotnet 均不可用）⇒ 改了也编译不出来。
;     Inno 这页由 Inno 自己管路径：快捷方式、卸载项、自检路径全走 `{app}` 变量，
;     改目录**自动跟随**，不会留残留（不像启动器那套要自己同步）。
; ⛔ 目录页之外的页面仍全部关掉（避免冒出系统风格的欢迎/就绪/完成页）。
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
; ⛔ 不显示"选择是否创建快捷方式"那种系统页之外的多余页；快捷方式走 [Tasks]
AllowNoIcons=yes
; ⛔ Inno 自带向导的页面**除目录页外全部关掉** —— 这个 exe 基本不负责"给用户看界面"，
;    它只做"把包铺到用户机器上"这一件事；用户看到的界面是装完后拉起的
;    `群相.exe`（`launcher-src/` 那套自绘四页向导：欢迎 / 安装位置 / 正在安装 / 完成）。
;    ⭐ **例外：目录页 `DisableDirPage=no` 开着**（2026-10-10 用户口径：要让用户选装到哪）。
DisableWelcomePage=yes
DisableDirPage=no
DisableReadyPage=yes
DisableFinishedPage=yes
DisableStartupPrompt=yes

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
; ⛔ 只清程序自己生成的缓存目录。
;    **绝不加** `Type: filesandordirs; Name: "{app}"` —— 那会把用户的
;    data\ logs\ config.json runtime\ 一起删掉（那些是运行时产物，不在安装清单里）。
Type: filesandordirs; Name: "{app}\__pycache__"

[UninstallRun]
; 卸载前先把可能在跑的进程收掉，免得文件占用导致卸载不干净。
; ⚠️ `RunOnceId` 必须给：不给的话 Inno 编译时会告警，且多次卸载场景下这条会重复执行。
Filename: "{cmd}"; Parameters: "/c taskkill /f /im {#AppExe} 2>nul"; Flags: runhidden skipifdoesntexist; RunOnceId: "KillRunningApp"

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
