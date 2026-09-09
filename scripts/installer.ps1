# wx-agent 一键启动安装器窗口：图标 + 步骤进度 + 进度条（无命令行黑窗）
# 由 一键启动.vbs 隐藏启动；依次：准备 Python → onestart(事件解析) → 快捷方式询问 → 完成。
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logFile = Join-Path $root 'logs\onestart.log'

# ── 窗口 ──
$f = New-Object System.Windows.Forms.Form
$f.Text = 'wx-agent 一键启动'
$f.StartPosition = 'CenterScreen'
$f.FormBorderStyle = 'FixedDialog'
$f.MaximizeBox = $false; $f.MinimizeBox = $false
$f.BackColor = [System.Drawing.Color]::FromArgb(246, 248, 252)
$f.ClientSize = New-Object System.Drawing.Size -ArgumentList 500, 420
try { $ico = Join-Path $root 'assets\app.ico'; if (Test-Path $ico) { $f.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($ico) } } catch {}

$pic = New-Object System.Windows.Forms.PictureBox
try { $png = Join-Path $root 'assets\app-icon.png'; if (Test-Path $png) { $pic.Image = [System.Drawing.Image]::FromFile($png) } } catch {}
$pic.SizeMode = 'Zoom'
$pic.Location = New-Object System.Drawing.Point -ArgumentList 24, 20
$pic.Size = New-Object System.Drawing.Size -ArgumentList 64, 64
$f.Controls.Add($pic)

$lTitle = New-Object System.Windows.Forms.Label
$lTitle.Text = 'wx-agent 一键启动'
$lTitle.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', [single]15, [System.Drawing.FontStyle]::Bold)
$lTitle.Location = New-Object System.Drawing.Point -ArgumentList 104, 22
$lTitle.AutoSize = $true
$f.Controls.Add($lTitle)

$lState = New-Object System.Windows.Forms.Label
$lState.Text = '准备中…'
$lState.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 10.5)
$lState.ForeColor = [System.Drawing.Color]::FromArgb(58, 88, 128)
$lState.Location = New-Object System.Drawing.Point -ArgumentList 104, 58
$lState.AutoSize = $true
$f.Controls.Add($lState)

$steps = @('准备 Python 环境', '检查 / 安装依赖', '环境自检（55 项）', '启动机器人（打开控制台）')
$stepLabels = @()
for ($i = 0; $i -lt 4; $i++) {
    $lb = New-Object System.Windows.Forms.Label
    $lb.Text = ('　' + ($i + 1) + '. ' + $steps[$i])
    $lb.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 9.5)
    $lb.Location = New-Object System.Drawing.Point -ArgumentList 48, (112 + $i * 30)
    $lb.AutoSize = $true
    $lb.ForeColor = [System.Drawing.Color]::FromArgb(150, 158, 172)
    $f.Controls.Add($lb)
    $stepLabels += $lb
}

$bar = New-Object System.Windows.Forms.ProgressBar
$bar.Location = New-Object System.Drawing.Point -ArgumentList 48, 250
$bar.Size = New-Object System.Drawing.Size -ArgumentList 404, 20
$bar.Style = 'Continuous'
$bar.Maximum = 100
$bar.Value = 0
$f.Controls.Add($bar)

$lSub = New-Object System.Windows.Forms.Label
$lSub.Text = ''
$lSub.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 8.5)
$lSub.ForeColor = [System.Drawing.Color]::FromArgb(110, 122, 140)
$lSub.Location = New-Object System.Drawing.Point -ArgumentList 48, 276
$lSub.Size = New-Object System.Drawing.Size -ArgumentList 404, 26
$f.Controls.Add($lSub)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.ScrollBars = 'Vertical'
$logBox.WordWrap = $true
$logBox.Font = [System.Drawing.Font]::new('Consolas', 8.5)
$logBox.BackColor = [System.Drawing.Color]::FromArgb(252, 253, 255)
$logBox.Location = New-Object System.Drawing.Point -ArgumentList 48, 306
$logBox.Size = New-Object System.Drawing.Size -ArgumentList 404, 70
$f.Controls.Add($logBox)

$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Text = '关闭'
$btnClose.Size = New-Object System.Drawing.Size -ArgumentList 100, 32
$btnClose.Location = New-Object System.Drawing.Point -ArgumentList 352, 384
$btnClose.FlatStyle = 'Flat'
$btnClose.Enabled = $false
$btnClose.Add_Click({ $f.Close() })
$f.Controls.Add($btnClose)

function Set-State([string]$txt, [int]$pct, [int]$stepIdx, [string]$sub) {
    $lState.Text = $txt
    $bar.Value = [Math]::Min(100, [Math]::Max(0, $pct))
    if ($sub) { $lSub.Text = $sub } else { $lSub.Text = '' }
    for ($i = 0; $i -lt 4; $i++) {
        if ($i -lt $stepIdx) { $stepLabels[$i].Text = '✔ ' + $steps[$i]; $stepLabels[$i].ForeColor = [System.Drawing.Color]::FromArgb(52, 150, 90) }
        elseif ($i -eq $stepIdx) { $stepLabels[$i].Text = '▶ ' + $steps[$i]; $stepLabels[$i].ForeColor = [System.Drawing.Color]::FromArgb(40, 110, 200) }
        else { $stepLabels[$i].Text = '　' + ($i + 1) + '. ' + $steps[$i]; $stepLabels[$i].ForeColor = [System.Drawing.Color]::FromArgb(150, 158, 172) }
    }
    [System.Windows.Forms.Application]::DoEvents()
}

function Add-Log([string]$t) {
    if ($logBox.Lines.Count -gt 60) {
        $tmp = $logBox.Lines; $logBox.Lines = $tmp[($tmp.Count - 50)..($tmp.Count - 1)]
    }
    $logBox.AppendText($t + "`r`n")
    $logBox.SelectionStart = $logBox.TextLength
    $logBox.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

function Run-Hidden([string]$exe, [string]$args, [string]$envName, [string]$envVal) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = $args
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    if ($envName) { $psi.EnvironmentVariables[$envName] = $envVal }
    if ($env:WX_ONESTART_CHECK) { $psi.EnvironmentVariables['WX_ONESTART_CHECK'] = $env:WX_ONESTART_CHECK }
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    $p.Start() | Out-Null
    while (-not $p.StandardOutput.EndOfStream) {
        $line = $p.StandardOutput.ReadLine()
        Parse-Line $line
    }
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    return @{ code = $p.ExitCode; err = $err }
}

function Parse-Line([string]$line) {
    if (-not $line) { return }
    if ($line.StartsWith('@@PHASE:')) {
        $ph = $line.Substring(8)
        if ($ph -eq 'deps') { Set-State '检查 / 安装依赖…' 12 1 '准备 Python 完成' }
        elseif ($ph -eq 'selftest') { Set-State '环境自检（55 项）…' 70 2 '' }
        elseif ($ph -eq 'boot') { Set-State '启动机器人…' 92 3 '等待 Web 控制台就绪（自动打开浏览器）' }
    }
    elseif ($line.StartsWith('@@PROG:')) {
        $parts = $line.Substring(7).Split(':')
        if ($parts.Length -ge 3) {
            $key = $parts[0]; $done = [int]$parts[1]; $total = [int]$parts[2]
            $ratio = if ($total -gt 0) { $done / $total } else { 0 }
            if ($key -eq 'deps') { Set-State '检查 / 安装依赖…' (12 + [int](33 * $ratio)) 1 ('已检查 ' + $done + ' / ' + $total + ' 项') }
            elseif ($key -eq 'install') { Set-State '正在安装依赖…' 45 1 '使用国内镜像下载安装（请稍候）' }
            elseif ($key -eq 'selftest') { Set-State '环境自检（55 项）…' (70 + [int](20 * $ratio)) 2 ('第 ' + $done + ' / ' + $total + ' 项') }
        }
    }
    elseif ($line.StartsWith('@@REQ_SHORTCUT')) {
        $m = [System.Windows.Forms.MessageBox]::Show(
            '欢迎使用 wx-agent！`r`n`r`n机器人已启动，建议在桌面创建「一键启动」快捷方式，以后双击即可。`r`n`r`n是否创建？',
            'wx-agent 一键启动', [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($m -eq [System.Windows.Forms.DialogResult]::Yes) {
            try {
                $ws = New-Object -ComObject WScript.Shell
                $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\一键启动 wx-agent.lnk')
                $s.TargetPath = (Join-Path $root '一键启动.vbs')
                $s.WorkingDirectory = $root
                $s.IconLocation = (Join-Path $root 'assets\app.ico')
                $s.Save()
                Add-Log '桌面快捷方式已创建（一键启动 wx-agent）'
            } catch { Add-Log '快捷方式创建失败' }
        }
    }
    elseif ($line.StartsWith('@@DONE')) {
        Set-State '安装完成 ✔' 100 4 ''
        if ($env:WX_ONESTART_CHECK) { Start-Sleep -Milliseconds 1500; $f.Close() }
        else { $btnClose.Enabled = $true; $btnClose.Focus() }
    }
    elseif ($line.StartsWith('@@FAIL')) {
        Set-State '启动失败，请查看日志' 0 0 '详见 logs\onestart.log'
        Add-Log '一键启动失败（onestart 事件）'
        $btnClose.Enabled = $true
        $btnClose.Focus()
    }
}

function Wait-Close {
    $btnClose.Enabled = $true
    $btnClose.Focus()
    while ($f.Visible) {
        Start-Sleep -Milliseconds 200
        [System.Windows.Forms.Application]::DoEvents()
    }
}

# ── 主流程 ──
$f.Show()
Set-State '准备 Python 环境…' 5 0 '检测系统/绿色版 Python（无需手动安装）'

# 1) 准备 Python（隐藏执行）
$ps1 = Join-Path $root 'scripts\setup_python.ps1'
$r = Run-Hidden 'powershell.exe' ('-NoProfile -ExecutionPolicy Bypass -File "' + $ps1 + '"') '' ''
if ($r.code -ne 0) {
    Set-State 'Python 准备失败，请查看日志' 0 0 ('checks logs\onestart.log')
    Add-Log ('setup_python 退出码 ' + $r.code)
    Wait-Close
    return
}

# 读取 python 命令
$pyCmd = ''
$pth = Join-Path $root 'logs\python_path.txt'
if (Test-Path $pth) {
    $pyCmd = (Get-Content $pth -Raw).Trim()
}
if (-not $pyCmd) {
    Set-State '未找到可用的 Python' 0 0 '点击「关闭」后重试或检查网络'
    Wait-Close
    return
}
Set-State '检查 / 安装依赖…' 12 1 'Python 就绪'

# 2) onestart（GUI 事件驱动进度）
$onestart = Join-Path $root 'scripts\onestart.py'
$r2 = Run-Hidden $pyCmd ('-X utf8 "' + $onestart + '"') 'WX_GUI' '1'
if ($r2.code -ne 0) {
    Set-State '启动失败（依赖/自检/机器人）' 0 0 '详见 logs\onestart.log'
    Add-Log ('onestart 退出码 ' + $r2.code)
    if ($r2.err) { Add-Log ($r2.err.Substring(0, [Math]::Min(300, $r2.err.Length))) }
    Wait-Close
    return
}

Wait-Close
