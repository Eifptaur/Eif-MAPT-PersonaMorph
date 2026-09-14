@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 群相 · 环境检验
echo ============================================================
echo   群相 · 环境检验（全程只读为主，约 1 分钟）
echo   第一次运行会联网准备 Python 与依赖（约 1~3 分钟），之后很快
echo   会做一次"投递发送"实测：给【文件传输助手】发一条测试消息
echo   （那条消息只有你自己能看到，可以随手删掉）
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
echo       也可以把本文件与「报告」文件夹的名称、以及这一屏报错拍照发回来。
pause
exit /b 1
:run
echo   [2/2] 正在收集环境信息…
if defined PYARG (
  %PYCMD% %PYARG% scripts\collect_report.py --send-test --open
) else (
  "%PYCMD%" scripts\collect_report.py --send-test --open
)
echo.
echo ============================================================
echo   报告已生成在「报告」文件夹里。
echo   遇到任何问题，把最新那个「检验报告-*.txt」发回来即可。
echo ============================================================
pause
