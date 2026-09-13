@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 群相灵 · 只读体检（不发消息）
echo ============================================================
echo   只读体检：不发送、不点击、不发表，只收集环境信息
echo   第一次运行会联网准备 Python 与依赖（约 1~3 分钟），之后很快
echo ============================================================
echo.
set "PYCMD=runtime\python\python.exe"
set "PYARG="
if exist "%PYCMD%" goto run
echo   [1/2] 正在准备 Python 运行时（需要联网，请稍候）…
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup_python.ps1"
if exist "logs\python_path.txt" for /f "usebackq delims=" %%i in ("logs\python_path.txt") do set "PYCMD=%%i"
if exist "%PYCMD%" goto run
where py >nul 2>nul
if not errorlevel 1 set "PYCMD=py" & set "PYARG=-3" & goto run
echo.
echo   [X] 没能准备好 Python。请确认这台电脑能上网，或手动装 Python 3.10+ 后重跑本文件。
pause
exit /b 1
:run
echo   [2/2] 正在收集环境信息（只读，不发任何消息）…
if defined PYARG (
  %PYCMD% %PYARG% scripts\collect_report.py --open
) else (
  "%PYCMD%" scripts\collect_report.py --open
)
echo.
pause
