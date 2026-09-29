@echo off
chcp 65001 >nul
rem Beginner entry: double-click this file. A numbered Chinese menu appears.
rem No browser / WebView2 / GUI is used. Nothing is sent to anyone.
cd /d "%~dp0"
set PY=%~dp0runtime\python\python.exe
if not exist "%PY%" set PY=python
"%PY%" -X utf8 "%~dp0scripts\pmctl.py" --menu
echo.
pause
