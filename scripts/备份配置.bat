@echo off
chcp 65001 >nul
cd /d "%~dp0.."
setlocal
set STAMP=%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%%time:~6,2%
set STAMP=%STAMP: =0%
set BACKUP=backups\backup-%STAMP%
mkdir "%BACKUP%" 2>nul
copy /y config.json "%BACKUP%\" >nul
if exist data xcopy /e /i /y data "%BACKUP%\data" >nul 2>nul
echo 已备份配置与存档到 %BACKUP%
pause
