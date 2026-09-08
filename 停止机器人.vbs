Option Explicit
' wx-agent 停止器（无窗口）：先确保 Python（与一键启动同一套自动配置），再按 PID 文件静默停止机器人+看门狗。
Dim fso, sh, root, pyCmd, pyPath, script, cmd, code
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pyPath = root & "\logs\python_path.txt"

' 1) Python 环境（与启动器同一套；机器上有 Python 时秒完成）
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & root & "\scripts\setup_python.ps1""", 0, True
pyCmd = ""
If fso.FileExists(pyPath) Then
  pyCmd = Trim(fso.OpenTextFile(pyPath, 1, 0).ReadAll())
  pyCmd = Replace(pyCmd, vbCrLf, "")
  pyCmd = Replace(pyCmd, vbCr, "")
  pyCmd = Replace(pyCmd, vbLf, "")
End If
script = root & "\scripts\stop_bot.py"
If pyCmd = "" Then
  ' 极端情况：无 Python 且下载失败 —— 直接按 PID 文件强杀兜底
  On Error Resume Next
  Dim pidFile, pid
  pidFile = root & "\data\bot.pid"
  If fso.FileExists(pidFile) Then
    pid = Trim(fso.OpenTextFile(pidFile, 1, 0).ReadAll())
    pid = Replace(pid, vbCrLf, "")
    pid = Replace(pid, vbCr, "")
    pid = Replace(pid, vbLf, "")
    If pid <> "" Then
      sh.Run "taskkill /F /PID " & pid & " /T", 0, True
    End If
  End If
  On Error GoTo 0
  fso.DeleteFile pidFile, True
  MsgBox "wx-agent：没有找到 Python，已按 PID 文件强制结束进程。" & vbCrLf & _
         "请安装 Python 或联网后再用「停止机器人」做平滑停止。其它已经停止。", 48, "wx-agent"
  WScript.Quit 1
End If
If InStr(pyCmd, " ") > 0 Then
  cmd = pyCmd & " """ & script & """"
Else
  cmd = """" & pyCmd & """ """ & script & """"
End If
On Error Resume Next
code = sh.Run(cmd, 0, True)
If Err.Number <> 0 Then
  Err.Clear
  code = sh.Run("cmd /c " & cmd, 0, True)
End If
On Error GoTo 0
Dim OK_MSG, FAIL_MSG
OK_MSG = "wx-agent 已停止" & vbCrLf & vbCrLf & _
         "停止成功（微信保持打开，不会退出）：" & vbCrLf & _
         "（再次启动请双击「一键启动.vbs」；或在控制台点「恢复」）" & vbCrLf & vbCrLf & _
         "完成后建议关闭 Excel 等残留程序，防止干扰鼠标。"
FAIL_MSG = "wx-agent 停止失败（可能已不在运行）。" & vbCrLf & "请再双击「停止机器人.vbs」重试。"
If code = 0 Then
  MsgBox OK_MSG, 64, "wx-agent"
Else
  MsgBox FAIL_MSG, 48, "wx-agent"
End If
