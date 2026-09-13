# 群相灵 一键关闭：结束机器人/看门狗/安装器等全部相关进程（无窗口，完成后弹结果）
$delLock = Join-Path $PSScriptRoot '..\logs\installer.lock'
try { Remove-Item $delLock -Force -ErrorAction SilentlyContinue } catch {}
Add-Type -AssemblyName System.Windows.Forms
$markers = @('onestart.py', 'installer.ps1', 'setup_python.ps1', 'persona_morph.py', 'watchdog.py', 'stop_bot.py', 'close_all.ps1')
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
# 兜底：按 Persona Morph 路径结束残留的 python/powershell（部分命令行不含脚本名时）
try {
    $procs = Get-CimInstance Win32_Process
    foreach ($p in $procs) {
        if ($p.ProcessId -eq $PID) { continue }
        $cl = [string]$p.CommandLine
        if (-not $cl -or $cl -like '*一键关闭*') { continue }
        if ($cl -like '*Desktop*Persona Morph*' -and $cl -like '*python*') {
            try { taskkill /F /T /PID $p.ProcessId 2>$null | Out-Null; $killed += ('python (pid ' + $p.ProcessId + ')') } catch {}
        }
    }
} catch {}
$done = New-Object System.Windows.Forms.Form
$done.Text = '群相灵 一键关闭'
$done.StartPosition = 'CenterScreen'
$done.FormBorderStyle = 'FixedDialog'
$done.MaximizeBox = $false; $done.MinimizeBox = $false
$done.BackColor = [System.Drawing.Color]::FromArgb(246, 248, 252)
$done.ClientSize = New-Object System.Drawing.Size -ArgumentList 400, 200
try { $dico = Join-Path $PSScriptRoot '..\assets\app.ico'; if (Test-Path $dico) { $done.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($dico) } } catch {}
$dpic = New-Object System.Windows.Forms.PictureBox
try { $dpng = Join-Path $PSScriptRoot '..\assets\app-icon.png'; if (Test-Path $dpng) { $dpic.Image = [System.Drawing.Image]::FromFile($dpng) } } catch {}
$dpic.SizeMode = 'Zoom'
$dpic.Location = New-Object System.Drawing.Point -ArgumentList 22, 20
$dpic.Size = New-Object System.Drawing.Size -ArgumentList 60, 60
$done.Controls.Add($dpic)
$dtitle = New-Object System.Windows.Forms.Label
$dtitle.Text = '群相灵 一键关闭'
$dtitle.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 13, [System.Drawing.FontStyle]::Bold)
$dtitle.Location = New-Object System.Drawing.Point -ArgumentList 100, 22
$dtitle.AutoSize = $true
$done.Controls.Add($dtitle)
$dtext = New-Object System.Windows.Forms.Label
$dtext.Text = [char]10 + ($(if ($killed.Count) { $killed -join [char]10 } else { '没有残留进程（早已关闭）' }))
$dtext.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 9.5)
$dtext.ForeColor = [System.Drawing.Color]::FromArgb(90, 100, 122)
$dtext.Location = New-Object System.Drawing.Point -ArgumentList 100, 58
$dtext.Size = New-Object System.Drawing.Size -ArgumentList 270, 90
$done.Controls.Add($dtext)
$dok = New-Object System.Windows.Forms.Button
$dok.Text = '好的'
$dok.Size = New-Object System.Drawing.Size -ArgumentList 120, 34
$dok.Location = New-Object System.Drawing.Point -ArgumentList 148, 152
$dok.FlatStyle = 'Flat'
$dok.BackColor = [System.Drawing.Color]::FromArgb(64, 140, 255)
$dok.ForeColor = [System.Drawing.Color]::White
$dok.Add_Click({ $done.Close() })
$done.Controls.Add($dok)
$done.AcceptButton = $dok
[void]$done.ShowDialog()
exit 0
