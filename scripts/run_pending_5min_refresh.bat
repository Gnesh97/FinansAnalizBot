@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%\testdata"

echo Refreshing pending raw_5min data and running diagnostic...
python universe_scanner.py --refresh-pending-5min --paper-5min-fill-check --make-chatgpt-report
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo SUCCESS
echo Refresh report: reports\latest\pending_5min_refresh_report.md
echo 5min fill check: reports\latest\paper_5min_fill_check_report.md
echo Master report: reports\latest\chatgpt_master_report.md
exit /b 0

:failed
echo.
echo FAILED with exit code %EXIT_CODE%
echo Refresh report may be stale: reports\latest\pending_5min_refresh_report.md
exit /b %EXIT_CODE%
