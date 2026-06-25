@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%"

echo [1/2] Compiling safety and paper workflow modules...
echo [2/2] Running unittest suite...
python testdata\run_test_suite.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed

echo.
echo Tests passed.
echo Test report: testdata\reports\latest\test_run_report.md
pause
exit /b 0

:failed
echo.
echo FAILED with exit code %EXIT_CODE%
echo Test report: testdata\reports\latest\test_run_report.md
pause
exit /b %EXIT_CODE%
