@echo off
chcp 65001 >nul
echo 正在停止 wx-agent（机器人 + 看门狗）…
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { (($_.Name -eq 'python.exe' -and $_.CommandLine -like '*wx_agent.py*') -or ($_.Name -eq 'powershell.exe' -and $_.CommandLine -like '*看门狗.ps1*' -and $_.CommandLine -notlike '*Get-CimInstance*')) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('已停止 PID ' + $_.ProcessId) }"
echo.
echo 机器人进程已被终止，控制台页面将自动显示「机器人已停止」。
echo.
echo 按任意键关闭本窗口…
pause >nul
