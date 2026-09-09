# wx-agent 一键启动启动器窗口：图标 + 步骤进度 + 进度条（无命令行黑窗）
# 由 一键启动.vbs 隐藏启动；依次：准备 Python → onestart(事件解析) → 快捷方式询问 → 完成。
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$logFile = Join-Path $root 'logs\onestart.log'
$diag = Join-Path $root 'logs\installer.log'
try { New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs') | Out-Null } catch {}
function Diag([string]$m) { try { [IO.File]::AppendAllText($diag, "[" + (Get-Date -Format 'HH:mm:ss') + "] " + $m + "`r`n") } catch {} }

# ── 单实例锁：重复双击只跑一个安装器（并发会互相抢 get-pip/依赖文件）──
$lkPath = Join-Path $root 'logs\installer.lock'
$locked = $false
try {
    if (Test-Path $lkPath) {
        # 进程检测（不依赖锁文件内容）：有其它 installer.ps1 在跑 → 拦截；否则清理旧锁
        $otherInst = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
            Where-Object { $_.CommandLine -like '*installer.ps1*' -and $_.ProcessId -ne $PID }
        if ($otherInst) {
            Add-Type -AssemblyName System.Windows.Forms
            [System.Windows.Forms.MessageBox]::Show(
                "启动器已在运行中。`r`n若看不到窗口，请稍候或用「一键关闭」结束后重试。",
                'wx-agent 一键启动', [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
            exit 0
        }
        Remove-Item $lkPath -Force -ErrorAction SilentlyContinue
    }
    New-Item $lkPath -Force | Out-Null
    $locked = $true
} catch { }
# 退出时释放（句柄随进程结束自动释放，文件保留供"年龄"判断）

# ── 窗口 ──
$f = New-Object System.Windows.Forms.Form
$f.Text = 'wx-agent 一键启动'
$f.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$f.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
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


function Run-HiddenLogWatch([string]$exe, [string]$argLine, [string]$envName, [string]$envVal) {
    # 不重定向子进程输出（无事件线程 → 无跨线程崩溃）；改为主循环轮询 onestart.log 的 @@ 事件行
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = $argLine
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $false
    $psi.RedirectStandardError = $false
    $psi.CreateNoWindow = $true
    if ($envName) { $psi.EnvironmentVariables[$envName] = $envVal }
    if ($env:WX_ONESTART_CHECK) { $psi.EnvironmentVariables['WX_ONESTART_CHECK'] = $env:WX_ONESTART_CHECK }
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    try { $ok = $p.Start() } catch { Diag ('start fail: exe=[' + $exe + '] arg=[' + $argLine + '] exists=' + (Test-Path $exe)); return @{ code = 1; err = ('start failed: ' + $_.Exception.Message) } }
    if (-not $ok) { return @{ code = 1; err = 'start returned false' } }
    while (-not $p.WaitForExit(100)) {
        try {
            if (Test-Path $logFile) {
                # 按行数增量处理（ReadAllLines 全量读，避免 StreamReader 缓冲丢事件）
                $all = [IO.File]::ReadAllLines($logFile, [Text.Encoding]::UTF8)
                if ($all.Length -gt $script:lineCount) {
                    for ($i = $script:lineCount; $i -lt $all.Length; $i++) {
                        $ln = $all[$i]
                        if ($ln -and $ln.StartsWith('@@')) {
                            try { Parse-Line $ln } catch { Diag ('Parse EX: ' + $_.Exception.Message) }
                        }
                    }
                    $script:lineCount = $all.Length
                }
            }
        } catch { }
        [System.Windows.Forms.Application]::DoEvents()
    }
    return @{ code = $p.ExitCode; err = '' }
}

function Run-Hidden([string]$exe, [string]$argLine, [string]$envName, [string]$envVal) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = $argLine
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    if ($envName) { $psi.EnvironmentVariables[$envName] = $envVal }
    if ($env:WX_ONESTART_CHECK) { $psi.EnvironmentVariables['WX_ONESTART_CHECK'] = $env:WX_ONESTART_CHECK }
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    try { $ok = $p.Start() } catch { Diag ('start fail: exe=[' + $exe + '] arg=[' + $argLine + '] exists=' + (Test-Path $exe)); return @{ code = 1; err = ('start failed: ' + $_.Exception.Message) } }
    if (-not $ok) { return @{ code = 1; err = 'start returned false' } }
    # 异步读输出 → 线程安全队列；主循环在 UI 线程消费（事件线程直接改控件会异常闪退）
    $script:lineQ = New-Object 'System.Collections.Concurrent.ConcurrentQueue[string]'
    $p.add_OutputDataReceived({ param($s, $e) if ($e.Data) { $script:lineQ.Enqueue($e.Data) } })
    $p.add_ErrorDataReceived({ param($s, $e) if ($e.Data) { $script:lineQ.Enqueue(('ERR ' + $e.Data)) } })
    $p.BeginOutputReadLine()
    $p.BeginErrorReadLine()
    try {
        while (-not $p.WaitForExit(100)) {
            $ln = $null
            while ($script:lineQ.TryDequeue([ref]$ln)) {
                try { Parse-Line $ln } catch { Diag ('Parse EX: ' + $_.Exception.Message) }
            }
            [System.Windows.Forms.Application]::DoEvents()
        }
    } catch {
        Diag ('mainloop EX: ' + $_.Exception.Message)
    }
    return @{ code = $p.ExitCode; err = '' }
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
        # 自定义询问窗（图标 + 说明 + 彩色按钮，不用系统 MessageBox）
        $q = New-Object System.Windows.Forms.Form
        $q.Text = 'wx-agent 启动完成'
        $q.StartPosition = 'CenterScreen'
        $q.FormBorderStyle = 'FixedDialog'
        $q.MaximizeBox = $false; $q.MinimizeBox = $false
        $q.BackColor = [System.Drawing.Color]::FromArgb(246, 248, 252)
        $q.ClientSize = New-Object System.Drawing.Size -ArgumentList 450, 208
        try { $ico2 = Join-Path $root 'assets\app.ico'; if (Test-Path $ico2) { $q.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($ico2) } } catch {}
        $pic2 = New-Object System.Windows.Forms.PictureBox
        try { $png2 = Join-Path $root 'assets\app-icon.png'; if (Test-Path $png2) { $pic2.Image = [System.Drawing.Image]::FromFile($png2) } } catch {}
        $pic2.SizeMode = 'Zoom'
        $pic2.Location = New-Object System.Drawing.Point -ArgumentList 24, 26
        $pic2.Size = New-Object System.Drawing.Size -ArgumentList 66, 66
        $q.Controls.Add($pic2)
        $q1 = New-Object System.Windows.Forms.Label
        $q1.Text = 'wx-agent 启动完成'
        $q1.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 14, [System.Drawing.FontStyle]::Bold)
        $q1.Location = New-Object System.Drawing.Point -ArgumentList 108, 26
        $q1.AutoSize = $true
        $q.Controls.Add($q1)
        $qmsg = '欢迎使用 wx-agent！' + [char]10 + [char]10 + '机器人已启动，建议在桌面创建「一键启动」快捷方式。' + [char]10 + '是否现在创建？'
        $q2 = New-Object System.Windows.Forms.Label
        $q2.Text = $qmsg
        $q2.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', 9.5)
        $q2.ForeColor = [System.Drawing.Color]::FromArgb(76, 92, 118)
        $q2.Location = New-Object System.Drawing.Point -ArgumentList 108, 62
        $q2.Size = New-Object System.Drawing.Size -ArgumentList 320, 90
        $q.Controls.Add($q2)
        $qok = New-Object System.Windows.Forms.Button
        $qok.Text = '立即创建'
        $qok.Size = New-Object System.Drawing.Size -ArgumentList 140, 34
        $qok.Location = New-Object System.Drawing.Point -ArgumentList 296, 160
        $qok.FlatStyle = 'Flat'
        $qok.BackColor = [System.Drawing.Color]::FromArgb(64, 140, 255)
        $qok.ForeColor = [System.Drawing.Color]::White
        $qok.DialogResult = 'OK'
        $q.Controls.Add($qok)
        $qno = New-Object System.Windows.Forms.Button
        $qno.Text = '暂不'
        $qno.Size = New-Object System.Drawing.Size -ArgumentList 86, 34
        $qno.Location = New-Object System.Drawing.Point -ArgumentList 196, 160
        $qno.FlatStyle = 'Flat'
        $qno.DialogResult = 'Cancel'
        $q.Controls.Add($qno)
        $q.AcceptButton = $qok
        $q.CancelButton = $qno
        $q.Add_Shown({ $qok.Focus() })
        $m = $q.ShowDialog()
        if ($m -eq [System.Windows.Forms.DialogResult]::OK) {
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
        Set-State '启动完成 ✔' 100 4 ''
        $btnClose.Enabled = $true
        Start-Sleep -Milliseconds 2500
        $f.Close()
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
$script:lineCount = 0
try {
$f.Show()
Diag 'step: window shown'
Set-State '准备 Python 环境…' 5 0 '检测系统/绿色版 Python（无需手动安装）'

# 1) 准备 Python（隐藏执行）
$ps1 = Join-Path $root 'scripts\setup_python.ps1'
$r = Run-HiddenLogWatch 'powershell.exe' ('-NoProfile -ExecutionPolicy Bypass -File "' + $ps1 + '"') '' ''
Diag ('step: setup_python done rc=' + $r.code)
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
    $pyCmd = ([IO.File]::ReadAllText($pth, [Text.Encoding]::GetEncoding(936))).Trim()
}
if (-not $pyCmd) {
    Set-State '未找到可用的 Python' 0 0 '点击「关闭」后重试或检查网络'
    Wait-Close
    return
}
if (-not (Test-Path $pyCmd)) {
    # Python 路径失效（runtime 被删/换目录）：重跑准备脚本再读一次
    Diag ('pyCmd 无效，重跑 setup_python: [' + $pyCmd + ']')
    $r3 = Run-HiddenLogWatch 'powershell.exe' ('-NoProfile -ExecutionPolicy Bypass -File "' + $ps1 + '"') '' ''
    $pyCmd = ''
    if (Test-Path $pth) { $pyCmd = ([IO.File]::ReadAllText($pth, [Text.Encoding]::GetEncoding(936))).Trim() }
    Diag ('重读 pyCmd=[' + $pyCmd + ']')
    if (-not $pyCmd -or -not (Test-Path $pyCmd)) {
        Set-State 'Python 环境异常' 0 0 'runtime\python\python.exe 不存在，请重新解压完整包'
        Wait-Close
        return
    }
}
Set-State '检查 / 安装依赖…' 12 1 'Python 就绪'
Diag ('step: pyCmd=[' + $pyCmd + ']')

# 2) onestart（GUI 事件驱动进度）
$onestart = Join-Path $root 'scripts\onestart.py'
$r2 = Run-HiddenLogWatch $pyCmd ('-X utf8 "' + $onestart + '"') 'WX_GUI' '1'
Diag ('step: onestart done rc=' + $r2.code)
if ($r2.code -ne 0) {
    Set-State '启动失败（依赖/自检/机器人）' 0 0 '详见 logs\onestart.log'
    Add-Log ('onestart 退出码 ' + $r2.code)
    if ($r2.err) { Add-Log ($r2.err.Substring(0, [Math]::Min(300, $r2.err.Length))) }
    Wait-Close
    return
}

Wait-Close
} catch {
    Diag ('EXCEPTION: ' + $_.Exception.Message)
    try { Set-State '运行异常' 0 0 '详见 logs\installer.log' } catch {}
    Wait-Close
}