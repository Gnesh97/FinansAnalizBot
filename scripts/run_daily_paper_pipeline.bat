@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
set "SCANNER_PARALLEL_FLAGS=--parallel --workers auto"
set "DATA_REFRESH_FLAGS=--refresh-latest --parallel --download-workers 4 --aggregation-workers auto"
for /f %%A in ('powershell -NoProfile -Command "(Get-Date).ToString('o')"') do set "PIPELINE_START_TS=%%A"
cd /d "%PROJECT_ROOT%\testdata"

echo Scanner parallel flags: %SCANNER_PARALLEL_FLAGS%
echo Data refresh flags: %DATA_REFRESH_FLAGS%
echo.
echo [1/4] Updating market data...
python vericekici_updated.py --universe all --feed iex --adjustment all --daily-start 2015-01-01 --intraday-start 2020-07-01 --end today --output-dir hisseler %DATA_REFRESH_FLAGS%
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed
set "DAILY_DATA_UPDATE_RAN=true"

echo.
echo [2/4] Running paper report and pending fill check...
python universe_scanner.py --reports basic %SCANNER_PARALLEL_FLAGS% --paper-report --make-chatgpt-report
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo [3/4] Running execution preview and pre-submit safety check...
python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report %SCANNER_PARALLEL_FLAGS%
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo [4/4] Done.
for /f %%A in ('powershell -NoProfile -Command "[int]((Get-Date) - [datetime]::Parse($env:PIPELINE_START_TS)).TotalSeconds"') do set "PIPELINE_ELAPSED_SECONDS=%%A"
echo Total elapsed seconds: %PIPELINE_ELAPSED_SECONDS%
echo Master report: reports\latest\chatgpt_master_report.md
echo Data refresh report: reports\latest\data_refresh_report.md
echo Preflight report: reports\latest\daily_preflight_report.md
echo Data precheck report: reports\latest\data_precheck_report.md
echo Regression guard report: reports\latest\regression_guard_report.md
echo Pipeline health log: reports\latest\pipeline_health_log.csv
echo Pre-submit readiness report: reports\latest\pre_submit_readiness_report.md
pause
exit /b 0

:failed
echo.
echo FAILED with exit code %EXIT_CODE%
echo Master report may be stale: reports\latest\chatgpt_master_report.md
pause
exit /b %EXIT_CODE%
