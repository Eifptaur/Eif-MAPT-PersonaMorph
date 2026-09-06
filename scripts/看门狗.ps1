# wx-agent 看门狗：机器人崩溃/退出后自动重启
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 解析 python 可执行（优先 python，其次 py -3）
$script:PyExe = $null
$script:PyArgs = @()
foreach ($cand in @('python', 'py')) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) {
        if ($cand -eq 'py') {
            & py -3 -c "import sys" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $script:PyExe = 'py'; $script:PyArgs = @('-3'); break }
        } else {
            & python -c "import sys" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $script:PyExe = 'python'; $script:PyArgs = @(); break }
        }
    }
}
if (-not $script:PyExe) {
    Write-Host '[错误] 未找到可用的 Python，请安装 Python 3.10+（64 位）'
    exit 1
}

while ($true) {
    $argList = @($script:PyArgs) + @('wx_agent.py')
    $p = Start-Process -FilePath $script:PyExe -ArgumentList $argList -WorkingDirectory $root -PassThru -NoNewWindow
    $p.WaitForExit()
    Write-Host ""
    Write-Host ("机器人已退出 (exit code {0})，5 秒后自动重启… (Ctrl+C 退出看门狗)" -f $p.ExitCode)
    Start-Sleep -Seconds 5
}
