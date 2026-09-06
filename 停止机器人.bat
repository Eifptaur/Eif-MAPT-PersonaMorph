@echo off
:: 兼容旧习惯：此 bat 通过 wscript 调起「停止机器人.vbs」（隐藏、无黑框）
start "" wscript "%~dp0停止机器人.vbs"
exit /b 0