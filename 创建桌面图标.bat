@echo off
chcp 936 >nul
echo 正在创建桌面快捷方式（带鲸鱼图标）...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell;$s=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\一键启动 wx-agent.lnk');$s.TargetPath='%~dp0一键启动.vbs';$s.WorkingDirectory='%~dp0';$s.IconLocation='%~dp0assets\app.ico,0';$s.Save()"
echo 完成：桌面已生成「一键启动 wx-agent」快捷方式
pause
