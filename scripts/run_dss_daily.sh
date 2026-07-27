#!/usr/bin/env bash
# Nightly DSS chain for Linux (cron or systemd user timer).
#
# Chain: Tiingo --refresh-daily -> strict validation -> signal generation
#        -> DSS report -> Telegram send. Reports only; never places orders.
#
# Hardened execution mirroring run_dss_daily.bat: resolves the interpreter from
# .env PYTHON_PATH/PYTHON_EXECUTABLE, then python3/python on PATH, then common
# Linux locations. If none resolves it logs "ERROR 9009" to
# reports/dss/scheduler.log and exits 9 (see the exit-code note below).
#
# Install: prefer the systemd user timer (see reports/audit/phase6f_*).
# cron alternative (system clock on Europe/Istanbul), one line:
#   30 23 * * * /path/to/scripts/run_dss_daily.sh
#
# A plain daily trigger is correct: the scheduler self-gates on NYSE trading
# days and on already-reported sessions.
#
# Trigger must fall between 00:30 and 02:59 Europe/Istanbul.
#
# Upper bound: the trading-day gate resolves the session from UTC, and 03:00 TR
# is 00:00 UTC -- a later trigger rolls onto the next UTC day and skips the
# session it meant to report.
#
# Lower bound (Phase 8B): the session must have closed AND had time to publish.
# The NYSE close is 20:00 UTC and the default publication lag is 90 minutes, so
# nothing before 21:30 UTC (00:30 TR) is eligible. An earlier trigger now exits
# DEFERRED_SESSION_NOT_COMPLETE instead of chasing a session that does not
# exist yet. The installed timer fires at 01:00 TR, inside this window.
# Override the lag with DSS_PUBLICATION_LAG_MINUTES if the window must move.
set -uo pipefail

# Shell exit codes are truncated modulo 256, so the 9009/9010 identifiers used
# in the log text (and in run_dss_daily.bat, where Windows permits them) cannot
# be used as exit statuses: `exit 9009` reaches systemd as 49 and `exit 9010` as
# 50. The log keeps the familiar identifiers; these are what the process
# actually returns.
readonly EXIT_NO_INTERPRETER=9
readonly EXIT_NO_NETWORK=10

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
    exit "$EXIT_NO_INTERPRETER"
fi

log "using python: $RUN_PYTHON"

# 5. Wait for real connectivity before starting the chain.
#
#    systemd's Persistent=true fires a missed timer moments after boot, when
#    DNS is frequently not up yet. On 2026-07-27 that produced
#    "URLError [Errno -2] Name or service not known" on the refresh step -- and
#    the Telegram failure notice died the same way, so the operator was told
#    nothing. Resolution is what actually failed, so resolution is what is
#    tested here, not merely a routable interface.
#
#    User systemd units cannot order against network-online.target: it does not
#    exist in the user manager (LoadState=not-found). This wait is the fix.
NETWORK_HOST="${DSS_NETWORK_PROBE_HOST:-api.tiingo.com}"
NETWORK_WAIT_SECONDS="${DSS_NETWORK_WAIT_SECONDS:-900}"
NETWORK_PROBE_INTERVAL="${DSS_NETWORK_PROBE_INTERVAL:-15}"

probe_network() {
    if command -v getent >/dev/null 2>&1; then
        getent hosts "$NETWORK_HOST" >/dev/null 2>&1 && return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import socket,sys; socket.getaddrinfo(sys.argv[1], 443)" "$NETWORK_HOST" >/dev/null 2>&1 && return 0
    fi
    return 1
}

WAITED=0
until probe_network; do
    if (( WAITED >= NETWORK_WAIT_SECONDS )); then
        log "ERROR 9010: no network connectivity after ${NETWORK_WAIT_SECONDS}s (cannot resolve ${NETWORK_HOST})."
        log "ERROR 9010: chain NOT started. Queuing a late notice for the next run that can send it."
        "$RUN_PYTHON" "$PROJECT_ROOT/testdata/dss_scheduler.py" \
            --queue-network-abort "cannot resolve ${NETWORK_HOST} after ${NETWORK_WAIT_SECONDS}s" \
            2>&1 | tee -a "$LOG_FILE" || true
        exit "$EXIT_NO_NETWORK"
    fi
    if (( WAITED == 0 )); then
        log "waiting for network (resolving ${NETWORK_HOST}, up to ${NETWORK_WAIT_SECONDS}s)..."
    fi
    sleep "$NETWORK_PROBE_INTERVAL"
    WAITED=$(( WAITED + NETWORK_PROBE_INTERVAL ))
done
if (( WAITED > 0 )); then
    log "network became available after ${WAITED}s"
fi

log "starting scheduler chain $*"

"$RUN_PYTHON" "$PROJECT_ROOT/testdata/dss_scheduler.py" "$@" 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE="${PIPESTATUS[0]}"

log "completed with exit code $EXIT_CODE"
log "state file: reports/dss/scheduler_state.json"
exit "$EXIT_CODE"
