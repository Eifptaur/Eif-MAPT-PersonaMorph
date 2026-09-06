@echo off
chcp 65001 >nul
set "VBS=%~dp0..\启动机器人.vbs"
schtasks /create /tn "wx-agent" /tr "\"wscript.exe\" \"%VBS%\"" /sc onlogon /rl limited /f
if errorlevel 1 (
  echo 注册开机自启失败（可能需要管理员权限）
) else (
  echo 已注册开机自启：wx-agent（wscript 无窗口方式）
)
pause
