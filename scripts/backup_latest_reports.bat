@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%\testdata"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "STAMP=%%I"
set "BACKUP_DIR=backups\%STAMP%"
mkdir "%BACKUP_DIR%" >nul 2>nul

echo Backup target: %CD%\%BACKUP_DIR%
for %%F in (
    "reports\latest\chatgpt_master_report.md"
    "reports\latest\paper_signal_report.md"
    "reports\latest\paper_signal_report.csv"
    "reports\latest\paper_pending_orders.csv"
    "reports\latest\paper_order_review.csv"
    "reports\latest\paper_trade_log.csv"
    "reports\latest\paper_trading_plan.md"
    "reports\latest\order_intents.csv"
    "reports\latest\order_risk_check_report.csv"
    "reports\latest\order_risk_check_report.md"
    "reports\latest\execution_preview_report.csv"
    "reports\latest\execution_preview_report.md"
    "reports\latest\execution_audit_log.csv"
    "reports\latest\pre_submit_readiness_report.csv"
    "reports\latest\pre_submit_readiness_report.md"
    "reports\latest\short_readiness_report.csv"
    "reports\latest\short_readiness_report.md"
    "reports\latest\daily_preflight_report.md"
    "reports\latest\data_precheck_report.md"
    "reports\latest\regression_guard_report.md"
    "reports\latest\pipeline_health_log.csv"
    "reports\latest\run_manifest.json"
    "reports\latest\test_run_report.md"
    "reports\latest\paper_state_validation_report.md"
    "reports\latest\report_schema_validation_report.md"
    "reports\latest\project_status_detailed_report.md"
    "reports\latest\performance_profile_report.md"
    "reports\latest\strategy_compact_report.md"
    "reports\latest\strategy_compact_report.csv"
    "reports\latest\benchmark_audit_report.md"
    "reports\latest\data_audit_report.md"
    "reports\latest\data_audit_report.csv"
    "state\trading_state.sqlite3"
) do (
    if exist "%%~F" (
        copy /Y "%%~F" "%BACKUP_DIR%\" >nul
        echo copied: %%~F
    ) else (
        echo warning: missing %%~F
    )
)

echo.
echo Backup complete: %CD%\%BACKUP_DIR%
pause
exit /b 0
