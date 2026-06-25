@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%\testdata"

echo =================================================================
echo [Lightweight Workflow] Applying Paper Decisions / Risk Preview
echo =================================================================
echo.
python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report

echo.
echo Decision application completed.
pause
