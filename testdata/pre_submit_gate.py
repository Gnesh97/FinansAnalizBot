from pathlib import Path
import json

import pandas as pd

try:
    from .config import SHORT_TRADING_ENABLED
except ImportError:
    from config import SHORT_TRADING_ENABLED


PRE_SUBMIT_COLUMNS = ["check_name", "status", "value", "required_value", "message"]
SHORT_READINESS_COLUMNS = PRE_SUBMIT_COLUMNS


def _bool_value(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _row(check_name, status, value, required_value, message):
    return {
        "check_name": check_name,
        "status": status,
        "value": value,
        "required_value": required_value,
        "message": message,
    }


def build_pre_submit_checks(summary):
    risk_pass_count = int(summary.get("risk_pass_count", 0) or 0)
    submit_candidate_risk_pass_count = int(
        summary.get("submit_candidate_risk_pass_count", risk_pass_count) or 0
    )
    blocked_count = int(summary.get("blocked_count", 0) or 0)
    duplicate_count = int(summary.get("preview_duplicate_count", summary.get("duplicate_intent_count", 0)) or 0)
    pending_next_open_count = int(summary.get("pending_next_open_count", 0) or 0)
    broker_submit_enabled = _bool_value(summary.get("broker_submit_enabled", False))
    live_trading_enabled = _bool_value(summary.get("live_trading_enabled", False))
    submit_order_called = _bool_value(summary.get("submit_order_called", False))
    post_request_used = _bool_value(summary.get("post_request_used", False))
    short_trading_enabled = _bool_value(summary.get("short_trading_enabled", SHORT_TRADING_ENABLED))
    alpaca_connection_ok = _bool_value(summary.get("alpaca_paper_connection_ok", False))
    alpaca_positions_orders_ok = _bool_value(summary.get("alpaca_positions_orders_ok", False))
    data_precheck_status = str(summary.get("data_precheck_status", "UNKNOWN")).strip().upper()
    data_precheck_warning = str(summary.get("data_precheck_warning_summary", "none")).strip()

    rows = [
        _row(
            "broker_submit_enabled",
            "PASS" if broker_submit_enabled else "BLOCK",
            broker_submit_enabled,
            True,
            "Submit is intentionally disabled in current build.",
        ),
        _row(
            "live_trading_enabled",
            "PASS" if live_trading_enabled else "BLOCK",
            live_trading_enabled,
            True,
            "Live trading is intentionally disabled.",
        ),
        _row(
            "submit_order_called",
            "BLOCK" if submit_order_called else "PASS",
            submit_order_called,
            False,
            "submit_order must remain uncalled.",
        ),
        _row(
            "post_request_used",
            "BLOCK" if post_request_used else "PASS",
            post_request_used,
            False,
            "POST requests must remain unused.",
        ),
        _row(
            "pending_accepted_orders",
            "PASS" if int(summary.get("pending_accepted_orders_count", 0) or 0) > 0 else "WARNING",
            int(summary.get("pending_accepted_orders_count", 0) or 0),
            ">0",
            "Accepted pending orders available for readiness review.",
        ),
        _row(
            "order_intents",
            "PASS" if int(summary.get("order_intents_count", 0) or 0) > 0 else "BLOCK",
            int(summary.get("order_intents_count", 0) or 0),
            ">0",
            "Order intents must exist before submit readiness can be evaluated.",
        ),
        _row(
            "submit_candidate_risk_pass_count",
            "PASS" if submit_candidate_risk_pass_count > 0 else "BLOCK",
            submit_candidate_risk_pass_count,
            ">0",
            "At least one intent must pass non-preview-duplicate risk for a future submit candidate.",
        ),
        _row("blocked_count", "WARNING" if blocked_count > 0 else "PASS", blocked_count, "0", "Blocked intents require review."),
        _row("duplicate_count", "WARNING" if duplicate_count > 0 else "PASS", duplicate_count, "0", "Preview duplicates do not block future submit namespace."),
        _row(
            "pending_next_open_count",
            "BLOCK" if pending_next_open_count > 0 else "PASS",
            pending_next_open_count,
            "0",
            "Orders remain pending next open; submit is not allowed now.",
        ),
        _row(
            "next_open_data_available",
            "PASS" if _bool_value(summary.get("next_open_data_available", False)) else "WARNING",
            _bool_value(summary.get("next_open_data_available", False)),
            True,
            "Next open data availability must be explicit before submit.",
        ),
        _row(
            "market_open_closed_info_available",
            "PASS" if _bool_value(summary.get("market_open_closed_info_available", False)) else "WARNING",
            _bool_value(summary.get("market_open_closed_info_available", False)),
            True,
            "Market session open/closed state must be available before submit.",
        ),
        _row(
            "alpaca_paper_connection_ok",
            "PASS" if alpaca_connection_ok else "WARNING",
            alpaca_connection_ok,
            True,
            "Alpaca paper connection confirmed by latest report."
            if alpaca_connection_ok
            else "Alpaca paper connection was not confirmed by this check.",
        ),
        _row(
            "alpaca_positions_orders_ok",
            "PASS" if alpaca_positions_orders_ok else "WARNING",
            alpaca_positions_orders_ok,
            True,
            "Alpaca open positions/orders check confirmed by latest report."
            if alpaca_positions_orders_ok
            else "Alpaca open positions/orders status was not confirmed by this check.",
        ),
        _row(
            "local_state_execution_preview_match",
            "PASS" if _bool_value(summary.get("local_state_execution_preview_match", False)) else "BLOCK",
            _bool_value(summary.get("local_state_execution_preview_match", False)),
            True,
            "Local paper state and execution preview counts must match.",
        ),
        _row(
            "data_precheck_status",
            "PASS" if data_precheck_status == "OK" else "BLOCK",
            data_precheck_status,
            "OK",
            "Data precheck must be OK before submit readiness; warnings: "
            + (data_precheck_warning or "none"),
        ),
        _row(
            "short_submit_allowed",
            "BLOCK" if not short_trading_enabled else "WARNING",
            False,
            False,
            "Short submit must remain disabled until short readiness is explicitly enabled.",
        ),
    ]
    return pd.DataFrame(rows, columns=PRE_SUBMIT_COLUMNS)


def build_short_readiness_checks(summary):
    short_trading_enabled = _bool_value(summary.get("short_trading_enabled", SHORT_TRADING_ENABLED))
    short_intents_count = int(summary.get("short_intents_count", 0) or 0)
    short_blocked_count = int(summary.get("short_blocked_count", 0) or 0)
    borrow_check_available = _bool_value(summary.get("borrow_check_available", False))
    rows = [
        _row("short_trading_enabled", "PASS" if short_trading_enabled else "BLOCK", short_trading_enabled, True, "Short trading is disabled by config."),
        _row("short_strategy_enabled", "BLOCK", False, False, "No short strategy is enabled or generated."),
        _row("short_intents_count", "PASS" if short_intents_count == 0 else "BLOCK", short_intents_count, 0, "Short order intents must not be produced in the current build."),
        _row("short_blocked_count", "PASS" if short_intents_count == 0 or short_blocked_count == short_intents_count else "BLOCK", short_blocked_count, short_intents_count, "Any short intent must be blocked while shorts are disabled."),
        _row("borrow_check_available", "BLOCK" if not borrow_check_available else "PASS", borrow_check_available, True, "Borrow availability API is not wired in this readiness-only phase."),
        _row("borrow_fee_known", "BLOCK", False, True, "Borrow fee is unknown; no borrow API call is made."),
        _row("hard_to_borrow_known", "BLOCK", False, True, "Hard-to-borrow status is unknown; no borrow API call is made."),
        _row("max_short_notional", "BLOCK", "placeholder", "configured", "Max short notional is a placeholder and not enabled."),
        _row("short_position_exists", "BLOCK", "placeholder", "verified", "Short position state is a placeholder and no short state exists."),
        _row("short_submit_allowed", "BLOCK", False, True, "Short submit is not allowed."),
    ]
    return pd.DataFrame(rows, columns=SHORT_READINESS_COLUMNS)


def short_readiness_summary(checks):
    blocking = int(checks["status"].eq("BLOCK").sum()) if checks is not None and not checks.empty else 1
    warnings = int(checks["status"].eq("WARNING").sum()) if checks is not None and not checks.empty else 0
    return {
        "short_readiness_status": "BLOCK" if blocking > 0 else ("WARNING" if warnings > 0 else "PASS"),
        "short_blocking_checks_count": blocking,
        "short_warning_checks_count": warnings,
        "short_trading_enabled": bool(SHORT_TRADING_ENABLED),
        "short_submit_allowed": False,
        "short_strategy_enabled": False,
        "borrow_check_available": False,
        "short_readiness_report_path": "reports/latest/short_readiness_report.md",
    }


def readiness_summary(checks):
    blocking = int(checks["status"].eq("BLOCK").sum()) if checks is not None and not checks.empty else 1
    warnings = int(checks["status"].eq("WARNING").sum()) if checks is not None and not checks.empty else 0
    return {
        "pre_submit_readiness_status": "BLOCK" if blocking > 0 else ("WARNING" if warnings > 0 else "PASS"),
        "blocking_checks_count": blocking,
        "warning_checks_count": warnings,
        "submit_allowed": False,
        "reason_submit_not_allowed": "Submit intentionally disabled; pre-submit readiness has blocking safety checks.",
        "pre_submit_readiness_report_path": "reports/latest/pre_submit_readiness_report.md",
    }


def _read_count(path):
    if not Path(path).exists():
        return 0
    try:
        return len(pd.read_csv(path))
    except Exception:
        return 0


def _file_has_text(path, text):
    if not Path(path).exists():
        return False
    return text.lower() in Path(path).read_text(encoding="utf-8", errors="replace").lower()


def _alpaca_report_connection_ok(path):
    return _file_has_text(path, "connection_status: ok") or _file_has_text(path, "connection status: ok")


def _alpaca_report_positions_orders_checked(path):
    if not _alpaca_report_connection_ok(path):
        return False
    positions_checked = _file_has_text(path, "open_positions_count:") or _file_has_text(path, "open positions count:")
    orders_checked = _file_has_text(path, "open_orders_count:") or _file_has_text(path, "open orders count:")
    return positions_checked and orders_checked


def _latest_data_precheck_status(latest_path):
    manifest_path = Path(latest_path) / "run_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            status = str(manifest.get("data_precheck_status", "")).strip().upper()
            warning = str(manifest.get("warning_summary", "none")).strip()
            if status:
                return status, warning or "none"
        except Exception:
            pass
    health_path = Path(latest_path) / "pipeline_health_log.csv"
    if health_path.exists():
        try:
            health = pd.read_csv(health_path)
            if not health.empty:
                last = health.iloc[-1]
                status = str(last.get("status", "")).strip().upper()
                warning = str(last.get("warning_summary", "none")).strip()
                return status or "UNKNOWN", warning or "none"
        except Exception:
            pass
    return "UNKNOWN", "data_precheck_status_missing"


def build_summary_from_latest(latest_dir, preview_summary):
    latest_path = Path(latest_dir)
    summary = dict(preview_summary or {})
    pending_path = latest_path / "paper_pending_orders.csv"
    if pending_path.exists():
        try:
            pending = pd.read_csv(pending_path)
        except Exception:
            pending = pd.DataFrame()
        decision = pending.get("user_decision", pd.Series("", index=pending.index)).astype(str).str.upper()
        status = pending.get("status", pd.Series("", index=pending.index)).astype(str).str.upper()
        fill_status = pending.get("fill_status", pd.Series("", index=pending.index)).astype(str).str.upper()
        accepted = decision.eq("ACCEPT")
        pending_next_open = fill_status.eq("PENDING_NEXT_OPEN") | status.isin(["PENDING_NEXT_OPEN", "PENDING"])
        summary["pending_accepted_orders_count"] = int((accepted & pending_next_open).sum())
        summary["pending_next_open_count"] = int((accepted & pending_next_open).sum())
    summary["order_intents_count"] = int(summary.get("order_intents_count", _read_count(latest_path / "order_intents.csv")) or 0)
    summary["local_state_execution_preview_match"] = int(summary.get("pending_accepted_orders_count", 0) or 0) == int(summary.get("order_intents_count", 0) or 0)
    summary["market_open_closed_info_available"] = (latest_path / "daily_preflight_report.md").exists()
    summary["next_open_data_available"] = _file_has_text(latest_path / "paper_signal_report.csv", "next_open_available")
    alpaca_report = latest_path / "alpaca_paper_check_report.md"
    summary["alpaca_paper_connection_ok"] = _alpaca_report_connection_ok(alpaca_report)
    summary["alpaca_positions_orders_ok"] = _alpaca_report_positions_orders_checked(alpaca_report)
    data_precheck_status, data_precheck_warning = _latest_data_precheck_status(latest_path)
    summary["data_precheck_status"] = data_precheck_status
    summary["data_precheck_warning_summary"] = data_precheck_warning
    return summary


def write_pre_submit_readiness_report(latest_dir, preview_summary):
    latest_path = Path(latest_dir)
    latest_path.mkdir(parents=True, exist_ok=True)
    summary = build_summary_from_latest(latest_path, preview_summary)
    checks = build_pre_submit_checks(summary)
    result = readiness_summary(checks)
    short_checks = build_short_readiness_checks({**summary, **result})
    short_result = short_readiness_summary(short_checks)
    checks.to_csv(latest_path / "pre_submit_readiness_report.csv", index=False)
    short_checks.to_csv(latest_path / "short_readiness_report.csv", index=False)
    lines = [
        "# Pre-submit Readiness Report",
        "",
        f"- pre_submit_readiness_status: {result['pre_submit_readiness_status']}",
        f"- submit_allowed: {result['submit_allowed']}",
        f"- blocking_checks_count: {result['blocking_checks_count']}",
        f"- warning_checks_count: {result['warning_checks_count']}",
        f"- reason submit not allowed: {result['reason_submit_not_allowed']}",
        "",
        "| " + " | ".join(PRE_SUBMIT_COLUMNS) + " |",
        "| " + " | ".join(["---"] * len(PRE_SUBMIT_COLUMNS)) + " |",
    ]
    for _, row in checks.iterrows():
        lines.append("| " + " | ".join(str(row.get(column, "")).replace("\n", " ") for column in PRE_SUBMIT_COLUMNS) + " |")
    lines.append("")
    (latest_path / "pre_submit_readiness_report.md").write_text("\n".join(lines), encoding="utf-8")
    short_lines = [
        "# Short Readiness Report",
        "",
        f"- short_readiness_status: {short_result['short_readiness_status']}",
        f"- short_trading_enabled: {short_result['short_trading_enabled']}",
        f"- short_submit_allowed: {short_result['short_submit_allowed']}",
        f"- borrow_check_available: {short_result['borrow_check_available']}",
        "",
        "| " + " | ".join(SHORT_READINESS_COLUMNS) + " |",
        "| " + " | ".join(["---"] * len(SHORT_READINESS_COLUMNS)) + " |",
    ]
    for _, row in short_checks.iterrows():
        short_lines.append("| " + " | ".join(str(row.get(column, "")).replace("\n", " ") for column in SHORT_READINESS_COLUMNS) + " |")
    short_lines.append("")
    (latest_path / "short_readiness_report.md").write_text("\n".join(short_lines), encoding="utf-8")
    return {**result, **short_result, "pre_submit_checks": checks, "short_readiness_checks": short_checks}
