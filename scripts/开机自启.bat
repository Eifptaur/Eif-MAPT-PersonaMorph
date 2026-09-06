@echo off
chcp 65001 >nul
set "BAT=%~dp0启动机器人.bat"
schtasks /create /tn "wx-agent" /tr "\"%BAT%\"" /sc onlogon /rl limited /f
if errorlevel 1 (
  echo 注册开机自启失败（可能需要管理员权限）
) else (
  echo 已注册开机自启：wx-agent
)
pause
