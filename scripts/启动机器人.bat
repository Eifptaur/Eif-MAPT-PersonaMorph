@echo off
cd /d "%~dp0.."
setlocal
:: 无窗口启动：看门狗以隐藏窗口方式运行，机器人用 pythonw 后台跑；双击后界面立即结束，不再有黑色 cmd 窗口
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "scripts\看门狗.ps1"
exit /b 0
