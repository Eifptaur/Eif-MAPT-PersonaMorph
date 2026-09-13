Option Explicit
' Persona Morph 开机自启用启动器（无窗口）：转调主目录「一键启动.vbs」完整流程
' （自动保障 Python → 装依赖 → 自检 → 启动）。
Dim fso, sh, root, root2
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)     ' scripts\
root2 = fso.GetParentFolderName(root)                       ' 程序主目录
sh.Run "wscript.exe """ & root2 & "\一键启动.vbs""", 0, False
