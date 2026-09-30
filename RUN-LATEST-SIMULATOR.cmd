@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-Latest-Simulator.ps1" -OpenBrowser
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo The simulator did not start. Review the error above.
pause
exit /b 1
