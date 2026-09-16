Option Explicit
' Persona Morph 一键启动（图形安装器窗口，无命令行黑窗）：
'  1) scripts\installer.ps1：显示安装进度窗口（图标+步骤+进度条），内部隐藏执行
'     setup_python.ps1（准备 Python）与 onestart.py（依赖/自检/启动机器人/打开控制台）
'  2) 全部完成后询问是否创建桌面快捷方式，窗口自动关闭；失败时窗口内显示原因。
Dim fso, sh, root, code
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
code = sh.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\installer.ps1""", 0, True)
If code <> 0 Then
  Dim diag
  diag = ""
  If fso.FileExists(root & "\logs\installer.log") Then
    Dim f, all, lines, k, cnt
    Set f = fso.OpenTextFile(root & "\logs\installer.log", 1, 0)
    all = f.ReadAll()
    f.Close
    lines = Split(all, vbCrLf)
    cnt = UBound(lines)
    If cnt >= 8 Then k = cnt - 8 Else k = 0
    Do While k <= cnt
      If Trim(lines(k)) <> "" Then diag = diag & lines(k) & vbCrLf
      k = k + 1
    Loop
  End If
  MsgBox "一键启动未完成，请查看：\logs\onestart.log" & vbCrLf & vbCrLf & "—— 最近诊断（logs\installer.log）——" & vbCrLf & diag, 48, "Persona Morph"
  WScript.Quit 1
End If
WScript.Quit 0
