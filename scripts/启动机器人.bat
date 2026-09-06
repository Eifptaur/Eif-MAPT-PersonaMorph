@echo off
chcp 65001 >nul
cd /d "%~dp0.."
setlocal
set "PYCMD="
python -c "import sys" >nul 2>nul && set "PYCMD=python"
if not defined PYCMD py -3 -c "import sys" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
  echo [错误] 未找到可用的 Python，请安装 Python 3.10+（64 位）并勾选 "Add to PATH"
  pause
  exit /b 1
)
echo 正在启动 wx-agent（带看门狗自动重启）…
echo 关闭此窗口、或运行 scripts\停止机器人.bat 可退出
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\看门狗.ps1"
pause
