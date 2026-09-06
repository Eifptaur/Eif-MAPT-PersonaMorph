' wx-agent 无窗口启动器：双击即启动（后台隐藏运行看门狗 + pythonw 机器人）
' 无任何命令行窗口/黑框闪现；停止用 停止机器人.bat 或控制台「停止」
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\看门狗.ps1"""
sh.Run cmd, 0, False
