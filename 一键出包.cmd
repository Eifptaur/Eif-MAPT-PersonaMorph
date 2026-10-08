@echo off
rem 群相 release packaging entry. Kept pure-ASCII on purpose:
rem cmd.exe reads .cmd as GBK, so any UTF-8 Chinese here turns into mojibake.
rem Default form is the "with dependencies" package: offline wheels + runtime zip
rem are bundled so the user's first install needs no network at all.
rem --slim additionally drops packages the user never uses (see BLOAT in pack_online.py).
cd /d "%~dp0"
if not exist "%~dp0runtime\python\python.exe" goto NOPY

"%~dp0runtime\python\python.exe" -X utf8 "%~dp0scripts\pack_online.py" --slim
echo.
echo ==========================================
echo  Result: 0 = OK, 3 = PII hit, 4 = bad state,
echo          5 = .cmd line endings, 6 = fingerprint,
echo          7 = bloated package, 2 = bad flags
echo  Full log is above. Package lands in the parent folder.
echo ==========================================
echo.
pause
exit /b 0

:NOPY
echo Bundled runtime\python\python.exe not found next to this file.
pause
exit /b 1
