@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 群相灵 · 只读体检（不发消息）
echo ============================================================
echo   只读体检：不发送、不点击、不发表，只收集环境信息
echo ============================================================
echo.
runtime\python\python.exe scripts\collect_report.py --open
echo.
pause
