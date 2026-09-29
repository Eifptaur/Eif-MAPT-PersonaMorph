@echo off
chcp 65001 >nul
rem Read-only status view. No browser, no WebView2, no GUI.
rem Uses pmctl.py -> local HTTP API only.
cd /d "%~dp0"
set PY=%~dp0runtime\python\python.exe
if not exist "%PY%" set PY=python
"%PY%" -X utf8 "%~dp0scripts\pmctl.py" status %*
echo.
pause
