@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Stop-Simulator.ps1"
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo The simulator did not stop cleanly. Review the error above.
pause
exit /b 1
