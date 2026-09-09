# wx-agent 一键关闭：结束机器人/看门狗/安装器等全部相关进程（无窗口，完成后弹结果）
Add-Type -AssemblyName System.Windows.Forms
$markers = @('onestart.py', 'installer.ps1', 'setup_python.ps1', 'wx_agent.py', 'watchdog.py', 'stop_bot.py', 'close_all.ps1')
$killed = @()
$names = @()
try {
    $procs = Get-CimInstance Win32_Process
    foreach ($p in $procs) {
        if ($p.ProcessId -eq $PID) { continue }
        $n = [string]$p.Name
        $cl = [string]$p.CommandLine
        if (-not $cl) { continue }
        if ($cl -like '*一键关闭*') { continue }          # 不动自己所在的 vbs/脚本
        if ($n -notmatch 'python|powershell|wscript|cscript') { continue }
        $hit = $false
        foreach ($m in $markers) { if ($cl -like ('*' + $m + '*')) { $hit = $true; break } }
        if (-not $hit) { continue }
        try {
            taskkill /F /T /PID $p.ProcessId 2>$null | Out-Null
            $killed += ($n + ' (pid ' + $p.ProcessId + ')')
        } catch {}
    }
} catch {}
# 兜底：按 wx-agent 路径结束残留的 python/powershell（部分命令行不含脚本名时）
try {
    $procs = Get-CimInstance Win32_Process
    foreach ($p in $procs) {
        if ($p.ProcessId -eq $PID) { continue }
        $cl = [string]$p.CommandLine
        if (-not $cl -or $cl -like '*一键关闭*') { continue }
        if ($cl -like '*Desktop*wx-agent*' -and $cl -like '*python*') {
            try { taskkill /F /T /PID $p.ProcessId 2>$null | Out-Null; $killed += ('python (pid ' + $p.ProcessId + ')') } catch {}
        }
    }
} catch {}
[System.Windows.Forms.MessageBox]::Show(
    'wx-agent 已全部关闭。' + [Environment]::NewLine + [Environment]::NewLine +
    ($(if ($killed.Count) { $killed -join ', ' } else { '没有残留进程（早已关闭）' })),
    'wx-agent 一键关闭', [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
exit 0
