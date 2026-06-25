@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%\..\testdata"

echo [1/2] Running paper report and pending fill check...
python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo [2/2] Running execution preview and pre-submit safety check...
python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo SUCCESS
echo Done. Check reports\latest\chatgpt_master_report.md
echo Pre-submit readiness report: reports\latest\pre_submit_readiness_report.md
pause
exit /b 0

:failed
echo.
echo FAILED with exit code %EXIT_CODE%
pause
exit /b %EXIT_CODE%
