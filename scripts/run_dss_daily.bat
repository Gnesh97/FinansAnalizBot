@echo off
REM Nightly DSS chain for Windows Task Scheduler (unattended -- no pause).
REM
REM Chain: Tiingo --refresh-daily -> strict validation -> signal generation
REM        -> DSS report -> Telegram send. Reports only; never places orders.
REM
REM Schedule this daily at 23:30 Turkey time (16:30 ET, ~30 min after the US
REM close) so Tiingo has published the completed session. The ~3.8h paced
REM refresh finishes around 03:20 TR, well before the next US open (16:30 TR).
REM The script self-gates on NYSE trading days and on already-reported
REM sessions, so a plain daily trigger is correct -- do not add weekday logic
REM to the Task Scheduler trigger.
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%"

for /f %%A in ('powershell -NoProfile -Command "(Get-Date).ToString(''o'')"') do set "RUN_START_TS=%%A"
echo [dss-daily] start %RUN_START_TS%

python "%PROJECT_ROOT%\testdata\dss_scheduler.py" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo [dss-daily] exit code %EXIT_CODE%
echo [dss-daily] state file: reports\dss\scheduler_state.json
exit /b %EXIT_CODE%
