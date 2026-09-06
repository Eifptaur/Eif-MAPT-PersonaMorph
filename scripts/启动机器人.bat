@echo off
:: 兼容旧习惯：此 bat 会通过 wscript 调起无窗口启动器（会有极小概率闪一下自身），
:: 完全无窗口请双击同目录的「启动机器人.vbs」
start "" wscript "%~dp0启动机器人.vbs"
exit /b 0
