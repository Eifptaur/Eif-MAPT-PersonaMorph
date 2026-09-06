@echo off
:: 兼容旧习惯：此 bat 通过 wscript 调起根目录的「启动机器人.vbs」（隐藏、无黑框）
start "" wscript "%~dp0..\启动机器人.vbs"
exit /b 0