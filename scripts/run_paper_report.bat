@echo off
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%\..\testdata"
python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
    echo SUCCESS
    echo Done. Check reports\latest\chatgpt_master_report.md
) else (
    echo FAILED with exit code %EXIT_CODE%
)
pause
exit /b %EXIT_CODE%
