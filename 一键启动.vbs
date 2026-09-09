Option Explicit
' wx-agent 一键启动（图形安装器窗口，无命令行黑窗）：
'  1) scripts\installer.ps1：显示安装进度窗口（图标+步骤+进度条），内部隐藏执行
'     setup_python.ps1（准备 Python）与 onestart.py（依赖/自检/启动机器人/打开控制台）
'  2) 全部完成后询问是否创建桌面快捷方式，窗口自动关闭；失败时窗口内显示原因。
Dim fso, sh, root, code
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
code = sh.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\installer.ps1""", 0, True)
If code <> 0 Then
  MsgBox "一键启动未完成，请查看：\logs\onestart.log", 48, "wx-agent"
  WScript.Quit 1
End If
WScript.Quit 0
