@echo off
chcp 65001 >nul
schtasks /delete /tn "wx-agent" /f
echo 已取消开机自启。
pause
