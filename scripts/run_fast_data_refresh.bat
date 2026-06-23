@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%\testdata"

python vericekici_updated.py --universe all --feed iex --adjustment all --daily-start 2015-01-01 --intraday-start 2020-07-01 --end today --output-dir hisseler --refresh-latest --parallel --download-workers 4 --aggregation-workers auto
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo Data refresh report: reports\latest\data_refresh_report.md
pause
exit /b 0

:failed
echo.
echo FAILED with exit code %EXIT_CODE%
pause
exit /b %EXIT_CODE%
