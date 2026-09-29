@echo off
chcp 65001 >nul
rem Low-spec mode: turn off CPU-hungry visual effects / animations / faster polling.
rem It only touches whitelisted keys; safety switches are never included.
rem Undo: scripts\pmctl.py lowspec off
cd /d "%~dp0"
set PY=%~dp0runtime\python\python.exe
if not exist "%PY%" set PY=python
"%PY%" -X utf8 "%~dp0scripts\pmctl.py" lowspec on %*
echo.
pause
