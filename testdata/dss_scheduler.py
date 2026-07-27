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
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

DATASET_PROFILE = "rs_v2_tiingo"
STATE_PATH = PROJECT_DIR / "reports" / "dss" / "scheduler_state.json"
PENDING_NOTICE_PATH = PROJECT_DIR / "reports" / "dss" / "pending_notices.json"
DSS_FAILURE_MARKER = "DSS did not run"
LATE_NOTICE_MARKER = "LATE NOTICE"

# Minutes after the NYSE close before a session's EOD bars are treated as
# publishable. The 01:00 Europe/Istanbul trigger is 18:00 ET, two hours after
# the 16:00 ET close, so the default leaves ample margin.
DEFAULT_PUBLICATION_LAG_MINUTES = 90
# A notice is retried in-process first (the network may return within seconds),
# then queued for the next run rather than lost.
NOTICE_SEND_ATTEMPTS = 3
NOTICE_RETRY_SLEEP_SECONDS = 20
# Stop retrying a queued notice eventually; a month-old failure is noise.
MAX_QUEUED_NOTICE_ATTEMPTS = 10

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
    """Session the DSS freshness check will demand.

    Deliberately delegates to the provider's latest_completed_session, which is
    the same calendar bar dss_report._latest_expected_session applies. Those two
    must not drift, so this is not reimplemented here.
    """
    return _provider_module().latest_completed_session(as_of)


def publication_lag_minutes() -> int:
    """Configurable via DSS_PUBLICATION_LAG_MINUTES; falls back to the default."""
    raw = os.environ.get("DSS_PUBLICATION_LAG_MINUTES", "").strip()
    if not raw:
        return DEFAULT_PUBLICATION_LAG_MINUTES
    try:
        value = int(raw)
    except ValueError:
        print(f"[dss-scheduler] ignoring non-numeric DSS_PUBLICATION_LAG_MINUTES={raw!r}", flush=True)
        return DEFAULT_PUBLICATION_LAG_MINUTES
    return value if value >= 0 else DEFAULT_PUBLICATION_LAG_MINUTES


def latest_published_session(
    as_of: datetime | None = None,
    lag_minutes: int | None = None,
    lookback_days: int = 15,
) -> str:
    """Latest NYSE session whose close + publication lag has actually passed.

    target_session() answers "which session does the freshness check want"; it
    returns today's session the moment the calendar lists it, hours before the
    16:00 ET close. This answers the different question "which session has
    actually finished and had time to publish", which is what a data refresh
    can really obtain.
    """
    now = as_of or datetime.now(timezone.utc)
    lag = publication_lag_minutes() if lag_minutes is None else lag_minutes
    coverage = _coverage_module()
    window_start = (now - timedelta(days=lookback_days)).date().isoformat()
    schedule = coverage.approved_nyse_schedule(window_start, now.date().isoformat())
    cutoff = now - timedelta(minutes=lag)
    published = [
        session for session, close in zip(schedule.index, schedule["market_close"])
        if close.tz_convert("UTC") <= cutoff
    ]
    if not published:
        raise RuntimeError(
            f"V2_NO_PUBLISHED_SESSION_IN_WINDOW: no NYSE session closed at least {lag} "
            f"minutes before {now.isoformat()} within the trailing {lookback_days} days"
        )
    return published[-1].strftime("%Y-%m-%d")


def session_completion_gate(as_of: datetime | None = None, lag_minutes: int | None = None) -> dict:
    """Is the session the freshness check wants actually finished and published?

    When these disagree the run is too early: the chain would spend hours
    refreshing toward a session that does not exist yet, then fail the freshness
    check. That is precisely what the 2026-07-27 14:24 catch-up run did.
    """
    expected = target_session(as_of)
    published = latest_published_session(as_of, lag_minutes)
    return {"expected_session": expected, "published_session": published, "ready": expected == published}


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


# A real Telegram token is "<bot_id>:<35 chars>". Anything shorter cannot
# authenticate, and blind-replacing it corrupts the log: a one-character value
# turns "Name or service not known" into "Name or service no<redacted> known",
# destroying exactly the diagnostic the notice exists to carry.
MIN_REDACTABLE_TOKEN_LENGTH = 12


def _redact(text: str) -> str:
    """Strip the bot token out of anything headed for stdout or a log file."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if len(token) < MIN_REDACTABLE_TOKEN_LENGTH:
        return text
    return text.replace(token, "<redacted>")


def _post_telegram(text: str, token: str, chat_id: str, timeout: int = 30) -> None:
    """Single send attempt. Raises on any transport or HTTP failure."""
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def _undeliverable_banner(text: str, detail: str, queued: bool) -> None:
    """Make an undelivered notice impossible to miss in scheduler.log.

    The original single ERROR line was indistinguishable from ordinary noise, so
    a network outage read the same as a clean weekend skip.
    """
    bar = "!" * 78
    print(
        f"\n{bar}\n"
        f"NOTICE NOT DELIVERED -- the operator was NOT told about this failure.\n"
        f"  message : {text}\n"
        f"  transport error: {detail}\n"
        f"  {'queued for delivery on the next successful run.' if queued else 'NOT queued.'}\n"
        f"{bar}\n",
        flush=True,
    )


def load_pending_notices(path: Path = PENDING_NOTICE_PATH) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def save_pending_notices(notices: list[dict], path: Path = PENDING_NOTICE_PATH) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(notices, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def queue_notice(text: str, reason: str, session: str | None = None,
                 path: Path = PENDING_NOTICE_PATH, as_of: datetime | None = None) -> dict:
    """Persist an undelivered notice so a later run can still report it."""
    now = as_of or datetime.now(timezone.utc)
    entry = {
        "queued_at": now.isoformat().replace("+00:00", "Z"),
        "session": session,
        "text": text,
        "reason": reason,
        "attempts": 0,
    }
    notices = load_pending_notices(path)
    notices.append(entry)
    save_pending_notices(notices, path)
    return entry


def drain_pending_notices(path: Path = PENDING_NOTICE_PATH, as_of: datetime | None = None) -> dict:
    """Send queued notices, clearly marked as late, and drop what is delivered.

    Called once connectivity has demonstrably returned, so the common case is a
    single successful send. An entry that keeps failing is retried up to
    MAX_QUEUED_NOTICE_ATTEMPTS and then dropped with a loud local line, rather
    than accumulating forever.
    """
    notices = load_pending_notices(path)
    if not notices:
        return {"delivered": 0, "remaining": 0, "dropped": 0}

    _load_project_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print(f"[dss-scheduler] {len(notices)} queued notice(s) held: Telegram not configured.", flush=True)
        return {"delivered": 0, "remaining": len(notices), "dropped": 0}

    delivered, dropped, remaining = 0, 0, []
    for entry in notices:
        session_label = entry.get("session") or "unknown session"
        late_text = (f"{LATE_NOTICE_MARKER} (queued {entry.get('queued_at')}, session {session_label}): "
                     f"{entry.get('text', '')}")
        try:
            _post_telegram(late_text, token, chat_id)
            delivered += 1
            print(f"[dss-scheduler] delivered late notice for {session_label} "
                  f"(queued {entry.get('queued_at')}).", flush=True)
        except Exception as exc:
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            if entry["attempts"] >= MAX_QUEUED_NOTICE_ATTEMPTS:
                dropped += 1
                print(f"[dss-scheduler] DROPPING queued notice for {session_label} after "
                      f"{entry['attempts']} attempts: {_redact(str(exc))}", flush=True)
            else:
                remaining.append(entry)

    save_pending_notices(remaining, path)
    return {"delivered": delivered, "remaining": len(remaining), "dropped": dropped}


def send_failure_notice(
    reason: str,
    *,
    session: str | None = None,
    attempts: int = NOTICE_SEND_ATTEMPTS,
    retry_sleep_seconds: float = NOTICE_RETRY_SLEEP_SECONDS,
    queue_path: Path = PENDING_NOTICE_PATH,
    queue_on_failure: bool = True,
    sleep=time.sleep,
) -> dict:
    """Explicit Telegram failure notice, retried and then queued.

    The 2026-07-27 catch-up run proved the original single-shot send shares its
    only transport with the step it is reporting on: the refresh failed on DNS,
    so the notice failed on DNS too and the operator learned nothing. Bounded
    retries cover a transport that returns within seconds; the queue covers one
    that does not.
    """
    _load_project_env()
    text = f"{DSS_FAILURE_MARKER}: {reason}"
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    print(text, flush=True)
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set -- failure notice NOT sent "
              "(fail-closed locally, not sent to a wrong/missing chat).", flush=True)
        return {"sent": False, "reason": "telegram_not_configured", "text": text, "queued": False}

    last_detail = ""
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            _post_telegram(text, token, chat_id)
            return {"sent": True, "text": text, "attempts": attempt, "queued": False}
        except Exception as exc:
            last_detail = _redact(str(exc))
            print(f"[dss-scheduler] notice send attempt {attempt}/{attempts} failed: {last_detail}",
                  flush=True)
            if attempt < max(attempts, 1):
                sleep(retry_sleep_seconds)

    queued = False
    if queue_on_failure:
        queue_notice(text, last_detail, session=session, path=queue_path)
        queued = True
    _undeliverable_banner(text, last_detail, queued)
    return {"sent": False, "reason": last_detail, "text": text, "queued": queued,
            "attempts": max(attempts, 1)}


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
    pending_notice_path: Path = PENDING_NOTICE_PATH,
    publication_lag: int | None = None,
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

    # Flush undelivered notices before any early return. The launcher has
    # already proven DNS resolves, so this is the first moment connectivity is
    # known good -- and the run that most needs to deliver a late notice is
    # exactly the boot-time catch-up that then defers. Draining after the gate
    # would hold the notice for another whole day.
    if send:
        drained = drain_pending_notices(pending_notice_path, now)
        if drained["delivered"] or drained["dropped"]:
            print(f"[dss-scheduler] pending notices: delivered={drained['delivered']} "
                  f"dropped={drained['dropped']} remaining={drained['remaining']}", flush=True)

    session = target_session(now)
    if not force:
        # Only consult the publication gate when it can actually veto. Under
        # --force it must not even be evaluated: latest_published_session fails
        # closed when no session closed inside its lookback window, and an
        # override run must not inherit that failure mode.
        gate = session_completion_gate(now, lag_minutes=publication_lag)
        session = gate["expected_session"]
        if not gate["ready"]:
            # Too early: the freshness check wants a session that has not closed
            # (or has not had time to publish). Running now would burn hours of
            # paced refresh and then fail. Defer -- the next trigger picks it up.
            print(f"[dss-scheduler] session {session} has not closed/published yet "
                  f"(latest published: {gate['published_session']}); deferring.", flush=True)
            return {"status": "DEFERRED_SESSION_NOT_COMPLETE", "target_session": session,
                    "published_session": gate["published_session"]}

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
                notice = send_failure_notice(reason, session=session, queue_path=pending_notice_path)
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
    parser.add_argument("--pending-notice-path", default=str(PENDING_NOTICE_PATH))
    parser.add_argument("--publication-lag-minutes", type=int, default=None,
                        help="Minutes after the NYSE close before a session counts as publishable.")
    parser.add_argument("--queue-network-abort", metavar="DETAIL", default=None,
                        help="Queue a late notice recording that the launcher aborted before the "
                             "chain started (no connectivity), then exit. Used by run_dss_daily.sh.")
    args = parser.parse_args(argv)

    if args.queue_network_abort is not None:
        # The launcher could not reach the network at all, so there is nothing
        # to send now; persist the notice for the next run that can.
        entry = queue_notice(
            f"{DSS_FAILURE_MARKER}: launcher aborted before the chain started -- "
            f"no network connectivity ({args.queue_network_abort})",
            reason="network_unavailable_at_launch",
            session=None,
            path=Path(args.pending_notice_path),
        )
        print(json.dumps({"status": "QUEUED_NETWORK_ABORT", "queued_at": entry["queued_at"]}, indent=2),
              flush=True)
        return 0

    result = run_daily_chain(
        skip_refresh=args.skip_refresh,
        force=args.force,
        send=not args.no_send,
        report_print_only=args.report_print_only,
        state_path=Path(args.state_path),
        pending_notice_path=Path(args.pending_notice_path),
        publication_lag=args.publication_lag_minutes,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "steps"}, indent=2), flush=True)
    return 0 if result["status"] in {
        "SENT", "ALREADY_REPORTED", "SKIPPED_NON_TRADING_DAY", "DEFERRED_SESSION_NOT_COMPLETE",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
