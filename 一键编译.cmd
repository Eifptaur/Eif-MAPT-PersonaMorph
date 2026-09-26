@echo off
rem 群相 launcher build entry. Kept pure-ASCII on purpose:
rem cmd.exe reads .cmd as GBK, so any UTF-8 Chinese here turns into mojibake
rem (that is why the real work lives in build.ps1, which PowerShell reads as UTF-8).
cd /d "%~dp0"
if not exist "%~dp0build.ps1" goto NOPS1

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
echo.
echo ==========================================
echo  Result: 0 = OK, 2 = csc not found, 3 = missing source/lib
echo  Full log: logs\build-latest.log
echo ==========================================
echo.
pause
exit /b 0

:NOPS1
echo build.ps1 not found next to this file.
pause
exit /b 1
