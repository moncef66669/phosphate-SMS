@echo off
setlocal
rem UI design preview (separate app, port 8606). The production simulator on
rem port 8505 is unaffected and can run at the same time.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-UI-Preview.ps1" -OpenBrowser
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo The UI preview did not start. Review the error above.
pause
exit /b 1
