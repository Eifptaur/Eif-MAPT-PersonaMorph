@echo off
chcp 65001 >nul
cd /d "%~dp0.."
setlocal
set "PYCMD="
python -c "import sys" >nul 2>nul && set "PYCMD=python"
if not defined PYCMD py -3 -c "import sys" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
  echo [错误] 未找到可用的 Python，请安装 Python 3.10+（64 位）
  pause
  exit /b 1
)
%PYCMD% scripts\selftest.py
echo.
pause
