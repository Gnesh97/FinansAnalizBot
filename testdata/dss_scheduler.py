"""Nightly DSS chain orchestrator (Phase 6C).

Runs, on US trading days only, the full decision-support chain:

    Tiingo --refresh-daily (paced, 179 items, strict validation on finalize)
      -> paper signal generation
      -> DSS BUY/SELL report
      -> Telegram send

READ-ONLY with respect to trading: this module shells out to the three
existing report CLIs and NOTHING else. It never places, previews, or
prepares broker/order/live-trading calls -- asserted by ORDER_PLACEMENT_ENABLED
below and by the fixed command list in _chain_steps.

Fail-loud: any failing step (refresh, validation, signal generation, report)
sends an explicit "DSS did not run" notice to Telegram. Never a silent
no-send, never a blank BUY list.

Idempotent: a session that has already been reported is not re-run and not
re-sent. Interrupted runs resume through the provider's own per-symbol
resume state; a partially-completed chain simply re-runs on next invocation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

DATASET_PROFILE = "rs_v2_tiingo"
STATE_PATH = PROJECT_DIR / "reports" / "dss" / "scheduler_state.json"
DSS_FAILURE_MARKER = "DSS did not run"

# This scheduler produces and sends reports only. It has no order path.
ORDER_PLACEMENT_ENABLED = False
BROKER_SUBMIT_ENABLED = False


def _coverage_module():
    try:
        from . import v2_session_coverage as module
    except ImportError:
        import v2_session_coverage as module
    return module


def _provider_module():
    try:
        from . import tiingo_provider as module
    except ImportError:
        import tiingo_provider as module
    return module


def _chain_steps(python_executable: str, skip_refresh: bool, report_print_only: bool = False) -> list[dict]:
    """Fixed command list. Report/refresh CLIs only -- no broker entry points."""
    steps = []
    if not skip_refresh:
        steps.append({
            "name": "refresh",
            "description": "paced Tiingo daily refresh + strict validation",
            "command": [python_executable, str(BASE_DIR / "vericekici_updated.py"),
                        "--dataset-profile", DATASET_PROFILE, "--refresh-daily"],
        })
    steps.append({
        "name": "signals",
        "description": "paper signal generation",
        "command": [python_executable, str(BASE_DIR / "universe_scanner.py"),
                    "--data-profile", DATASET_PROFILE, "--paper-report"],
    })
    # --print-only renders the report to stdout/file without dispatching it, so
    # the wiring can be exercised end to end without messaging the operator.
    report_command = [python_executable, str(PROJECT_DIR / "bot.py"),
                      "--mode", "dss-report", "--dss-data-profile", DATASET_PROFILE]
    if report_print_only:
        report_command.append("--print-only")
    steps.append({
        "name": "report",
        "description": "DSS report render + Telegram send" if not report_print_only else "DSS report render (print-only, no send)",
        "command": report_command,
    })
    return steps


def is_trading_day(session_date: str) -> bool:
    """True when session_date is an NYSE session on the approved calendar.

    Queried over a trailing window rather than a single day: approved_nyse_schedule
    fail-closes on an empty range, so a one-day weekend/holiday query would raise
    where the honest answer is simply "not a session". A window always contains
    sessions unless the calendar itself is broken -- and that must still raise.
    """
    coverage = _coverage_module()
    window_start = (datetime.fromisoformat(session_date) - timedelta(days=10)).date().isoformat()
    schedule = coverage.approved_nyse_schedule(window_start, session_date)
    sessions = {stamp.strftime("%Y-%m-%d") for stamp in schedule.index}
    return session_date in sessions


def target_session(as_of: datetime | None = None) -> str:
    """Session this run is responsible for: the latest completed NYSE session."""
    return _provider_module().latest_completed_session(as_of)


def load_state(state_path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict, state_path: Path = STATE_PATH) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def already_reported(session: str, state_path: Path = STATE_PATH) -> bool:
    state = load_state(state_path)
    return state.get("last_target_session") == session and state.get("status") == "SENT"


def _load_project_env() -> None:
    """Populate os.environ from .env. Task Scheduler and cron start with a bare
    environment, so without this the Telegram credentials would be missing and
    every failure notice would silently fail closed -- the opposite of the
    fail-loud contract. Existing environment values win (setdefault)."""
    try:
        from .vericekici_updated import load_env_file
    except ImportError:
        from vericekici_updated import load_env_file
    load_env_file(PROJECT_DIR / ".env")


def _redact(text: str) -> str:
    """Strip the bot token out of anything headed for stdout or a log file."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    return text.replace(token, "<redacted>") if token else text


def send_failure_notice(reason: str) -> dict:
    """Explicit Telegram failure notice. Fails closed locally when unconfigured."""
    _load_project_env()
    text = f"{DSS_FAILURE_MARKER}: {reason}"
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    print(text, flush=True)
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set -- failure notice NOT sent "
              "(fail-closed locally, not sent to a wrong/missing chat).", flush=True)
        return {"sent": False, "reason": "telegram_not_configured", "text": text}
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
        return {"sent": True, "text": text}
    except Exception as exc:
        detail = _redact(str(exc))
        print(f"ERROR: failure notice could not be delivered: {detail}", flush=True)
        return {"sent": False, "reason": detail, "text": text}


def _run_step(step: dict, timeout_seconds: int) -> dict:
    started = datetime.now(timezone.utc)
    print(f"[dss-scheduler] {step['name']}: {step['description']}", flush=True)
    completed = subprocess.run(
        step["command"], cwd=str(PROJECT_DIR), capture_output=True, text=True,
        timeout=timeout_seconds, check=False,
    )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    tail = "\n".join((completed.stdout or "").strip().splitlines()[-15:])
    if completed.stdout:
        print(tail, flush=True)
    if completed.returncode != 0:
        error_tail = "\n".join((completed.stderr or "").strip().splitlines()[-5:])
        return {"name": step["name"], "ok": False, "elapsed_sec": round(elapsed, 1),
                "error": f"{step['name']} step exited {completed.returncode}: {error_tail or tail}"}
    # bot.py reports DSS-level problems on stdout and exits 0 after sending its
    # own failure notice; treat that as a failed chain without double-sending.
    if step["name"] == "report" and DSS_FAILURE_MARKER in (completed.stdout or ""):
        reason = next((line for line in (completed.stdout or "").splitlines() if DSS_FAILURE_MARKER in line), DSS_FAILURE_MARKER)
        return {"name": step["name"], "ok": False, "elapsed_sec": round(elapsed, 1),
                "error": reason.strip(), "notice_already_sent": True}
    return {"name": step["name"], "ok": True, "elapsed_sec": round(elapsed, 1)}


def run_daily_chain(
    *,
    as_of: datetime | None = None,
    skip_refresh: bool = False,
    force: bool = False,
    send: bool = True,
    report_print_only: bool = False,
    state_path: Path = STATE_PATH,
    python_executable: str | None = None,
    step_timeout_seconds: int = 6 * 60 * 60,
) -> dict:
    """Run the nightly chain once. Returns a status dict; never raises for an
    expected condition (non-trading day, already reported, failing step)."""
    now = as_of or datetime.now(timezone.utc)
    today = now.date().isoformat()
    if not force and not is_trading_day(today):
        print(f"[dss-scheduler] {today} is not an NYSE session; nothing to do.", flush=True)
        return {"status": "SKIPPED_NON_TRADING_DAY", "date": today}

    session = target_session(now)
    if not force and already_reported(session, state_path):
        print(f"[dss-scheduler] session {session} already reported and sent; not re-sending.", flush=True)
        return {"status": "ALREADY_REPORTED", "target_session": session}

    steps = _chain_steps(python_executable or sys.executable, skip_refresh, report_print_only)
    outcomes = []
    for step in steps:
        try:
            outcome = _run_step(step, step_timeout_seconds)
        except subprocess.TimeoutExpired:
            outcome = {"name": step["name"], "ok": False,
                       "error": f"{step['name']} step timed out after {step_timeout_seconds}s"}
        outcomes.append(outcome)
        if not outcome["ok"]:
            reason = outcome["error"]
            notice = {"sent": False, "reason": "already_sent_by_report_step"}
            if send and not outcome.get("notice_already_sent"):
                notice = send_failure_notice(reason)
            save_state({
                "last_target_session": session,
                "status": "FAILED",
                "failed_step": outcome["name"],
                "last_error": reason,
                "updated_at": now.isoformat().replace("+00:00", "Z"),
            }, state_path)
            return {"status": "FAILED", "target_session": session, "failed_step": outcome["name"],
                    "error": reason, "notice": notice, "steps": outcomes}

    save_state({
        "last_target_session": session,
        "status": "SENT",
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "last_error": "",
    }, state_path)
    print(f"[dss-scheduler] session {session} complete and sent.", flush=True)
    return {"status": "SENT", "target_session": session, "steps": outcomes}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Nightly DSS chain orchestrator (reports only; never places orders).")
    parser.add_argument("--skip-refresh", action="store_true",
                        help="Run signals + report against data already on disk (verification/backfill).")
    parser.add_argument("--force", action="store_true",
                        help="Ignore the trading-day gate and the already-reported guard.")
    parser.add_argument("--no-send", action="store_true",
                        help="Do not send Telegram failure notices (the report step's own send is governed by bot.py).")
    parser.add_argument("--report-print-only", action="store_true",
                        help="Render the DSS report without dispatching it; for verifying the wiring end to end.")
    parser.add_argument("--state-path", default=str(STATE_PATH))
    args = parser.parse_args(argv)

    result = run_daily_chain(
        skip_refresh=args.skip_refresh,
        force=args.force,
        send=not args.no_send,
        report_print_only=args.report_print_only,
        state_path=Path(args.state_path),
    )
    print(json.dumps({k: v for k, v in result.items() if k != "steps"}, indent=2), flush=True)
    return 0 if result["status"] in {"SENT", "ALREADY_REPORTED", "SKIPPED_NON_TRADING_DAY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
