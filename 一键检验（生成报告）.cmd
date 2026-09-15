@echo off
rem  规则（2026-09-15 两轮跨机实测定的，改前先读）： 
rem   ① 本文件必须 CRLF + UTF-8 无 BOM，且**每一行都以 ASCII 字节结尾**（LF 会让 cmd 一行都跑不动； 
rem      多字节字符紧贴 CR 还有解析事故的风险）。 
rem   ② **不许读 logs\python_path.txt**：那个文件是 ANSI 写的（一键启动.exe 按 936 读它）， 
rem      在本窗口 chcp 65001 下读会变乱码 ⇒ if exist 判否 ⇒ 静默掉到系统 Python 3.14 去编译源码。 
rem      正解＝先直接试包内 runtime\python\python.exe，试不到才退系统 py。 
rem   ③ 用 %PYCMD% 之前先确认它真的存在（见 :nopy），查不到就给人话，不抛 cmd 天书报错。 
chcp 65001 >nul
cd /d "%~dp0"
title 群相 · 环境检验 
echo ============================================================
echo   群相 · 环境检验（全程只读为主，约 1 分钟） 
echo   第一次运行会联网准备 Python 与依赖（约 1~3 分钟），之后很快 
echo   会做一次「投递发送」实测：给【文件传输助手】发一条测试消息 
echo   （那条消息只有你自己能看到，可以随手删掉） 
echo   实测会临时放行「版本门」（只对本次运行有效，重启后恢复拦截） 
echo ============================================================
echo.
set "PYCMD=runtime\python\python.exe"
set "PYARG="
if exist "%PYCMD%" goto deps
echo   [1/3] 正在准备 Python 运行时（需要联网，请稍候）… 
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup_python.ps1"
if exist "runtime\python\python.exe" (
  set "PYCMD=runtime\python\python.exe"
  set "PYARG="
  goto deps
)
where py >nul 2>nul
if not errorlevel 1 (
  set "PYCMD=py"
  set "PYARG=-3"
  echo   [注意] 没找到本包自带的 Python，下面会用系统 Python。本产品只支持 3.10~3.12， 
  echo          若接下来出现 cmake / ninja / Building wheel，请停下：装一个 3.10~3.12 再重跑。 
  goto deps
)
echo.
echo   [X] 没能准备好 Python。请确认这台电脑能上网，或手动装 Python 3.10+ 后重跑本文件。 
echo       也可以把本文件与「报告」文件夹的名称、以及这一屏报错拍照发回来。 
pause
exit /b 1
:deps
if not defined PYARG if not exist "%PYCMD%" goto nopy
echo   [2/3] 正在检查依赖（第一次会联网安装，约 1~3 分钟）… 
if defined PYARG (
  %PYCMD% %PYARG% -X utf8 scripts\setup_deps.py
) else (
  "%PYCMD%" -X utf8 scripts\setup_deps.py
)
echo   [3/3] 正在收集环境信息… 
if defined PYARG (
  %PYCMD% %PYARG% scripts\collect_report.py --send-test --allow-send --open
) else (
  "%PYCMD%" scripts\collect_report.py --send-test --allow-send --open
)
echo.
echo ============================================================
echo   报告已生成在「报告」文件夹里。 
echo   遇到任何问题，把最新那个「检验报告-*.txt」发回来即可。 
echo ============================================================
pause
exit /b 0
:nopy
echo   [X] 没能找到可用的 Python（%PYCMD% 不存在）。 
echo       请确认这台电脑能上网后重跑本文件，或手动装 Python 3.10~3.12 再试。 
pause
exit /b 1
