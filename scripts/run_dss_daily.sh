#!/usr/bin/env bash
# Nightly DSS chain for Linux (cron or systemd user timer).
#
# Chain: Tiingo --refresh-daily -> strict validation -> signal generation
#        -> DSS report -> Telegram send. Reports only; never places orders.
#
# Hardened execution mirroring run_dss_daily.bat: resolves the interpreter from
# .env PYTHON_PATH/PYTHON_EXECUTABLE, then python3/python on PATH, then common
# Linux locations. If none resolves it logs a clear error to
# reports/dss/scheduler.log and exits 9009.
#
# Install: prefer the systemd user timer (see reports/audit/phase6f_*).
# cron alternative (system clock on Europe/Istanbul), one line:
#   30 23 * * * /path/to/scripts/run_dss_daily.sh
#
# A plain daily trigger is correct: the scheduler self-gates on NYSE trading
# days and on already-reported sessions.
#
# Trigger must fall between 23:30 and 02:59 Europe/Istanbul. The trading-day
# gate resolves the session from UTC, and 03:00 TR is 00:00 UTC -- a later
# trigger would roll onto the next UTC day and skip the session it meant to
# report.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT" || exit 1

mkdir -p "$PROJECT_ROOT/reports/dss"
LOG_FILE="$PROJECT_ROOT/reports/dss/scheduler.log"

log() {
    printf '[%s] [dss-daily] %s\n' "$(date --iso-8601=seconds)" "$1" | tee -a "$LOG_FILE"
}

# 1. Interpreter from .env, if configured there. Read the value without
#    sourcing the file, so no other secret in .env is ever expanded here.
RUN_PYTHON=""
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    for key in PYTHON_PATH PYTHON_EXECUTABLE; do
        candidate="$(grep -E "^${key}=" "$PROJECT_ROOT/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d "\"'" | xargs || true)"
        if [[ -n "$candidate" ]] && "$candidate" --version >/dev/null 2>&1; then
            RUN_PYTHON="$candidate"
            break
        fi
    done
fi

# 2. python3 / python on PATH.
if [[ -z "$RUN_PYTHON" ]]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
            RUN_PYTHON="$(command -v "$candidate")"
            break
        fi
    done
fi

# 3. Common absolute locations, for a bare cron/systemd PATH.
if [[ -z "$RUN_PYTHON" ]]; then
    for candidate in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
        if [[ -x "$candidate" ]] && "$candidate" --version >/dev/null 2>&1; then
            RUN_PYTHON="$candidate"
            break
        fi
    done
fi

# 4. Fail loud locally. This failure happens before the scheduler starts, so
#    it cannot send a Telegram notice -- the log file is the only channel.
if [[ -z "$RUN_PYTHON" ]]; then
    log "ERROR 9009: no working Python interpreter found."
    log "ERROR 9009: set PYTHON_PATH=/path/to/python3 in .env"
    exit 9009
fi

log "using python: $RUN_PYTHON"
log "starting scheduler chain $*"

"$RUN_PYTHON" "$PROJECT_ROOT/testdata/dss_scheduler.py" "$@" 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE="${PIPESTATUS[0]}"

log "completed with exit code $EXIT_CODE"
log "state file: reports/dss/scheduler_state.json"
exit "$EXIT_CODE"
