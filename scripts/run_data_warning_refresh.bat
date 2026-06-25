@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%\testdata"

echo [1/3] Refreshing data-warning symbols: DOW BK PARA PSA...
python vericekici_updated.py --symbols DOW BK PARA PSA --feed iex --adjustment all --daily-start 2015-01-01 --intraday-start 2020-07-01 --end today --output-dir hisseler --overwrite
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed
set "DAILY_DATA_UPDATE_RAN=true"

echo.
echo [2/3] Rebuilding paper report and data precheck...
python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo [3/3] Rebuilding execution preview and pre-submit safety check...
python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo SUCCESS
echo Data precheck report: reports\latest\data_precheck_report.md
echo Pipeline health log: reports\latest\pipeline_health_log.csv
echo Pre-submit readiness report: reports\latest\pre_submit_readiness_report.md
pause
exit /b 0

:failed
echo.
echo FAILED with exit code %EXIT_CODE%
echo Check downloader output and reports\latest\data_precheck_report.md
pause
exit /b %EXIT_CODE%
