# wx-agent 看门狗（无窗口模式）：机器人崩溃/退出后自动重启
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 解析 python 可执行（无窗口用 pythonw，失败回退 python）
$script:PyExe = $null
$script:PyArgs = @()
foreach ($cand in @('pythonw', 'python')) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) {
        & $cand -c "import sys" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $script:PyExe = $cand; $script:PyArgs = @(); break }
    }
}
if ($null -eq $script:PyExe -and (Get-Command 'py' -ErrorAction SilentlyContinue)) {
    & py -3 -c "import sys" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $script:PyExe = 'py'; $script:PyArgs = @('-3') }
}
if (-not $script:PyExe) {
    exit 1
}

while ($true) {
    $argList = @($script:PyArgs) + @('wx_agent.py')
    $p = Start-Process -FilePath $script:PyExe -ArgumentList $argList -WorkingDirectory $root -PassThru -WindowStyle Hidden
    $p.WaitForExit()
    Start-Sleep -Seconds 5
}
