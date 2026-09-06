' wx-agent 无窗口停止器：双击隐藏窗口停止（机器人 + 看门狗），无任何 cmd 黑框
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell -NoProfile -Command ""Get-CimInstance Win32_Process | Where-Object { (($_.Name -eq 'python.exe' -and $_.CommandLine -like '*wx_agent.py*') -or ($_.Name -eq 'powershell.exe' -and $_.CommandLine -like '*看门狗.ps1*' -and $_.CommandLine -notlike '*Get-CimInstance*')) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"""
sh.Run cmd, 0, True
MsgBox "wx-agent 已停止（机器人 + 看门狗）。", 64, "wx-agent"
