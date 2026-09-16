Option Explicit
' Persona Morph 一键关闭：结束机器人 / 看门狗 / 安装器 / 依赖进程等全部残留（无窗口）
Dim fso, sh, root, code
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
code = sh.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\close_all.ps1""", 0, True)
WScript.Quit code
