@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%\testdata"

echo =================================================================
echo [Lightweight Workflow] Applying Paper Decisions / Risk Preview
echo =================================================================
echo.
python universe_scanner.py --apply-paper-order-review-decisions
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo Decision application completed.
pause
exit /b 0

:failed
echo.
echo FAILED with exit code %EXIT_CODE%
pause
exit /b %EXIT_CODE%
