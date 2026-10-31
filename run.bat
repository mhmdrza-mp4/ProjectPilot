@echo off
if not exist "%~dp0run.ps1" goto :missing
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
set "rc=%errorlevel%"
if "%rc%"=="0" exit /b 0
if "%rc%"=="2" exit /b 2
if "%rc%"=="3" exit /b 3
if "%rc%"=="10" exit /b 10
if "%rc%"=="99" exit /b 99
echo.
echo Launcher stopped unexpectedly (code %rc%).
echo Log folder: %LOCALAPPDATA%\ProjectPilot\logs
echo If Windows says running scripts is disabled, ask IT to allow run.ps1.
echo %* | find /i "NoPause" >nul || pause
exit /b %rc%

:missing
echo run.ps1 was not found next to run.bat.
echo Extract the whole ZIP (do not run from inside it) and keep both files together.
pause
exit /b 1
