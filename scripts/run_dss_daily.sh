#!/usr/bin/env bash
# Nightly DSS chain for cron (Linux equivalent of run_dss_daily.bat).
#
# Chain: Tiingo --refresh-daily -> strict validation -> signal generation
#        -> DSS report -> Telegram send. Reports only; never places orders.
#
# Install (Europe/Istanbul system clock), runs 23:30 daily:
#   30 23 * * * /path/to/scripts/run_dss_daily.sh >> /path/to/reports/dss/scheduler.log 2>&1
#
# A plain daily entry is correct: the scheduler self-gates on NYSE trading
# days and on already-reported sessions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "[dss-daily] start $(date --iso-8601=seconds)"
set +e
python3 "$PROJECT_ROOT/testdata/dss_scheduler.py" "$@"
EXIT_CODE=$?
set -e
echo "[dss-daily] exit code $EXIT_CODE"
echo "[dss-daily] state file: reports/dss/scheduler_state.json"
exit "$EXIT_CODE"
