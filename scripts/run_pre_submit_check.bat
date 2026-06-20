@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%\testdata"

echo Running pre-submit readiness check...
python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo Pre-submit readiness check completed.
pause
exit /b 0

:failed
echo.
echo FAILED with exit code %EXIT_CODE%
pause
exit /b %EXIT_CODE%
