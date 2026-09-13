@echo off
rem Persona Morph 开机自启注册（登录后自动走一键启动：自动保障 Python → 装依赖 → 自检 → 启动）
set "VBS=%~dp0..\一键启动.vbs"
schtasks /create /tn "Persona Morph" /tr "\"wscript.exe\" \"%VBS%\"" /sc onlogon /rl limited /f
if errorlevel 1 (
  echo 注册开机自启失败，请右键「以管理员身份运行」本文件。
) else (
  echo 已注册开机自启任务（Persona Morph，登录时自动启动）。
)
pause
