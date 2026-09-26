# 一键编译（群相）：编 一键启动.exe / 一键关闭.exe
# 命令以 AGENTS.md:224-225 为准 —— 必须带 WebView2 两个 /r，源文件是 4 个
# （wingliphs.cs 顶栏自绘字形 + webview2guide.cs 引导器，AGENTS.md:222 明写"两者都漏写过一次"）
# 文件名用 ASCII：cmd 按 GBK 读批处理，中文文件名/参数会变乱码（一键编译.cmd 里零中文就是为这个）

$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
Set-Location $root

# ⛔ 日志目录**必须是产品自己的**：以前指到本地的草稿目录（不入库、不进包的那一个）⇒ 编译脚本
#    每跑一次就在产品根里把那个目录重新建出来，用户/开发者一编译就冒出一个开发目录。
#    改到 `logs/`（产品自己的运行日志目录，同样不入库、不进包），编译日志与运行日志放一起也更好找。
$logDir = Join-Path $root 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir 'build-latest.log'
"===== BUILD $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====" | Out-File -FilePath $log -Encoding utf8

# 1) 定位 csc
$csc = $null
foreach ($c in @('C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe',
                 'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe')) {
    if (Test-Path $c) { $csc = $c; break }
}
if (-not $csc) {
    'CSC_NOT_FOUND' | Out-File -FilePath $log -Encoding utf8 -Append
    Write-Output "CSC_NOT_FOUND (see $log)"
    exit 2
}
"csc = $csc" | Out-File -FilePath $log -Encoding utf8 -Append

# 2) 前置检查：源文件 / 图标 / WebView2 程序集缺一个就别编（否则得到"编出来了其实是旧的"）
$need = @('launcher-src\launcher.cs', 'launcher-src\close.cs', 'launcher-src\stylekit.cs',
          'launcher-src\wingliphs.cs', 'launcher-src\webview2guide.cs', 'assets\exe.ico',
          'lib\Microsoft.Web.WebView2.Core.dll', 'lib\Microsoft.Web.WebView2.WinForms.dll')
$missing = @()
foreach ($n in $need) {
    if (-not (Test-Path (Join-Path $root $n))) { $missing += $n }
}
if ($missing.Count -gt 0) {
    ('MISSING: ' + ($missing -join '; ')) | Out-File -FilePath $log -Encoding utf8 -Append
    Write-Output ("MISSING_FILES=" + $missing.Count)
    exit 3
}

# 3) 一键启动.exe（4 源 + WebView2）
$o1 = & $csc /nologo /target:winexe /optimize+ "/win32icon:assets\exe.ico" `
    "/r:lib\Microsoft.Web.WebView2.Core.dll" "/r:lib\Microsoft.Web.WebView2.WinForms.dll" `
    "/out:一键启动.exe" "launcher-src\launcher.cs" "launcher-src\stylekit.cs" `
    "launcher-src\wingliphs.cs" "launcher-src\webview2guide.cs" 2>&1 | Out-String
$e1 = $LASTEXITCODE
"--- 一键启动.exe exit=$e1 ---" | Out-File -FilePath $log -Encoding utf8 -Append
$o1 | Out-File -FilePath $log -Encoding utf8 -Append

# 4) 一键关闭.exe（2 源，无 WebView2）
$o2 = & $csc /nologo /target:winexe /optimize+ "/win32icon:assets\exe.ico" `
    /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Management.dll `
    "/out:一键关闭.exe" "launcher-src\close.cs" "launcher-src\stylekit.cs" 2>&1 | Out-String
$e2 = $LASTEXITCODE
"--- 一键关闭.exe exit=$e2 ---" | Out-File -FilePath $log -Encoding utf8 -Append
$o2 | Out-File -FilePath $log -Encoding utf8 -Append

# 5) 产物清单（字节数 + 时间戳是"exe 是否落后于源码"的机械判据）
$art = Get-Item '一键启动.exe', '一键关闭.exe' -ErrorAction SilentlyContinue
foreach ($a in $art) {
    "ARTIFACT $($a.Name) $($a.Length) bytes $($a.LastWriteTime)" | Out-File -FilePath $log -Encoding utf8 -Append
}

Write-Output "START_EXE_EXIT=$e1"
Write-Output "CLOSE_EXE_EXIT=$e2"
foreach ($a in $art) {
    Write-Output ("ARTIFACT {0} {1} bytes {2}" -f $a.Name, $a.Length, $a.LastWriteTime)
}
Write-Output "LOG=$log"
