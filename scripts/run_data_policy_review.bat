@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%\testdata"

echo Reviewing data policy operator decisions...
python data_policy_review.py
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Data policy decisions: reports\latest\data_policy_decisions.csv
echo Data policy review report: reports\latest\data_policy_review_report.md
echo Data policy evidence: reports\latest\data_policy_review_evidence.csv
echo Data precheck report: reports\latest\data_precheck_report.md
echo Pre-submit readiness report: reports\latest\pre_submit_readiness_report.md
pause
exit /b %EXIT_CODE%
