# wx-agent 自动保障 Python（无需用户安装）：
#  1) 系统已有 Python 3.10+（py / python） → 直接用
#  2) 否则用绿色版：解压 offline\python\python-3.10.11-embed-amd64.zip（或联网下载）
#  3) 为绿色版引导 pip（优先离线 wheels 里的 pip 轮子，其次 get-pip.py）
# 结果写入 logs\python_path.txt（ASCII）。退出码 0=成功。
$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'onestart.log'
$pathTxt = Join-Path $logDir 'python_path.txt'

function Log($m) {
    try { Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding UTF8 } catch {}
}

function Set-PyPath($cmd) {
    try { Set-Content -Path $pathTxt -Value $cmd -Encoding ascii } catch {}
}

# --- 1) 系统 Python ---
if (-not $env:WX_FORCE_PORTABLE) {
    foreach ($c in @('py -3', 'py', 'python')) {
        $ok = $false
        try {
            & cmd /c "$c -c `"import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)`" 2>nul" | Out-Null
            if ($LASTEXITCODE -eq 0) { $ok = $true }
        } catch {}
        if ($ok) {
            Log "使用系统 Python：$c"
            Set-PyPath $c
            exit 0
        }
    }
    Log "未找到系统 Python（>=3.10），改用绿色版（无需安装）…"
} else {
    Log "WX_FORCE_PORTABLE=1：跳过系统 Python 检测（测试模式）"
}

# --- 2) 绿色版 Python ---
$runtime = Join-Path $root 'runtime\python'
$zipDir  = Join-Path $root 'offline\python'
$zip     = Join-Path $zipDir 'python-3.10.11-embed-amd64.zip'
$pyExe   = Join-Path $runtime 'python.exe'
$urls = @(
    'https://mirrors.huaweicloud.com/python/3.10.11/python-3.10.11-embed-amd64.zip',
    'https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip'
)

function Download-Win($url, $dst) {
    # 优先 curl.exe（自带进度条，可见百分比）；旧系统无 curl 则退回 Invoke-WebRequest
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -L --fail --progress-bar --ssl-no-revoke --connect-timeout 15 --retry 2 -o $dst $url
        return ($LASTEXITCODE -eq 0 -and (Test-Path $dst))
    }
    Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing -TimeoutSec 120
    return $true
}

function Ensure-Runtime {
    if (Test-Path $pyExe) { return $true }
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    # 源码包可能没有 offline\python 目录：联网下载时把 zip 放到 runtime 下
    $zipUse = $zip
    if (-not (Test-Path $zipUse)) {
        $zipUse = Join-Path $runtime 'python-3.10.11-embed-amd64.zip'
    }
    if (-not (Test-Path $zipUse)) {
        Log "需要下载绿色版 Python（约 8MB）…"
        $down = $false
        foreach ($u in $urls) {
            try {
                if (Download-Win $u $zipUse) {
                    Log "下载成功：$u"
                    $down = $true
                    break
                }
                Log "下载失败：$u"
            } catch {
                Log "下载失败：$u（$($_.Exception.Message)）"
            }
        }
        if (-not $down) { return $false }
    }
    Log "解压绿色版 Python 到 runtime\python …"
    Expand-Archive -Path $zipUse -DestinationPath $runtime -Force
    if ($zipUse -ne $zip) { Remove-Item $zipUse -Force -ErrorAction SilentlyContinue }
    if (-not (Test-Path $pyExe)) { return $false }
    $pth = Get-ChildItem $runtime -Filter 'python*._pth' | Select-Object -First 1
    if ($pth) {
        $c = Get-Content $pth.FullName -Raw
        $c = $c -replace '#\s*import site', 'import site'
        Set-Content -Path $pth.FullName -Value $c -Encoding ascii
        Log "已开启 site（pip 支持）：$($pth.Name)"
    }
    return $true
}

if (-not (Ensure-Runtime)) {
    Log "[失败] 没有 Python 且自动下载失败（需要联网，或离线包里有 offline\python）。"
    exit 1
}

# --- 3) 引导 pip（只要 pip 可用就行；离线优先，其次 get-pip）---
$pipOk = $false
try {
    $pv = & $pyExe -m pip --version 2>&1
    if ($LASTEXITCODE -eq 0 -and ("$pv" -match 'pip \d')) { $pipOk = $true }
} catch {}
if (-not $pipOk) {
    Log "绿色版缺少 pip，开始引导…"
    $sitePkgs = Join-Path $runtime 'Lib\site-packages'
    $pipWheel = Get-ChildItem (Join-Path $root 'offline\wheels') -Filter 'pip-*-py3-none-any.whl' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pipWheel) {
        Log "从离线 wheels 解包 pip：$($pipWheel.Name)"
        try {
            New-Item -ItemType Directory -Force -Path $sitePkgs | Out-Null
            # PS5.1 的 Expand-Archive 只认 .zip 扩展名，.whl 必须用 .NET ZipFile 解
            Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
            [System.IO.Compression.ZipFile]::ExtractToDirectory($pipWheel.FullName, $sitePkgs)
            $setWheel = Get-ChildItem (Join-Path $root 'offline\wheels') -Filter 'setuptools-*-py3-none-any.whl' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($setWheel) { [System.IO.Compression.ZipFile]::ExtractToDirectory($setWheel.FullName, $sitePkgs) }
            $pv = & $pyExe -m pip --version 2>&1
            if ($LASTEXITCODE -eq 0 -and ("$pv" -match 'pip \d')) { $pipOk = $true }
        } catch {
            Log "pip wheel 解包失败：$($_.Exception.Message)"
        }
    }
    if (-not $pipOk) {
        Log "尝试联网 get-pip.py…"
        $gp = Join-Path $logDir 'get-pip.py'
        try {
            Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $gp -UseBasicParsing -TimeoutSec 90
            & $pyExe $gp --no-warn-script-location 2>$null | Out-Null
            $pv = & $pyExe -m pip --version 2>&1
            if ($LASTEXITCODE -eq 0 -and ("$pv" -match 'pip \d')) { $pipOk = $true }
        } catch {
            Log "get-pip 失败：$($_.Exception.Message)"
        }
    }
    if (-not $pipOk) {
        Log "[失败] pip 引导失败（离线 wheels 里没有 pip 且无网络）。"
        exit 1
    }
    Log "pip 就绪"
}

Log "Python 就绪：$pyExe"
Set-PyPath $pyExe
exit 0
