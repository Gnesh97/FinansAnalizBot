from pathlib import Path
import hashlib

import pandas as pd

try:
    from .config import SHORT_TRADING_ENABLED
    from .paper_state_manager import read_pending_orders
    from .pre_submit_gate import write_pre_submit_readiness_report
    from .risk_gate import evaluate_order_risk, normalize_order_side, write_risk_gate_reports
except ImportError:
    from config import SHORT_TRADING_ENABLED
    from paper_state_manager import read_pending_orders
    from pre_submit_gate import write_pre_submit_readiness_report
    from risk_gate import evaluate_order_risk, normalize_order_side, write_risk_gate_reports


ORDER_INTENT_COLUMNS = [
    "order_intent_id",
    "idempotency_phase",
    "idempotency_key",
    "execution_idempotency_key",
    "created_at",
    "strategy_name",
    "symbol",
    "side",
    "notional",
    "estimated_qty",
    "order_type",
    "time_in_force",
    "source_signal_date",
    "status",
    "reason",
]

AUDIT_LOG_COLUMNS = [
    "run_datetime",
    "event_type",
    "order_intent_id",
    "idempotency_phase",
    "idempotency_key",
    "execution_idempotency_key",
    "strategy_name",
    "symbol",
    "side",
    "status",
    "risk_pass",
    "block_reason",
    "would_submit",
    "broker_submit_enabled",
    "live_trading_enabled",
    "message",
]


def _stable_order_intent_id(row, side):
    values = [
        row.get("strategy_name", ""),
        row.get("symbol", ""),
        side,
        row.get("signal_date", ""),
        row.get("created_at", row.get("signal_date", "")),
        row.get("action", ""),
    ]
    raw = "|".join(str(value).strip().upper() for value in values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def execution_idempotency_key(idempotency_key, phase="PREVIEW"):
    return f"{str(phase).strip().upper()}:{str(idempotency_key).strip()}"


def action_to_order_side(action):
    action = str(action).strip().upper()
    if action == "SELL":
        return "SELL"
    return "BUY"


def _accepted_pending_next_open_orders(pending_orders):
    frame = pending_orders.copy() if pending_orders is not None else pd.DataFrame()
    if frame.empty:
        return frame
    decision = frame.get("user_decision", pd.Series("", index=frame.index)).astype(str).str.upper()
    status = frame.get("status", pd.Series("", index=frame.index)).astype(str).str.upper()
    fill_status = frame.get("fill_status", pd.Series("", index=frame.index)).astype(str).str.upper()
    pending_next_open = fill_status.eq("PENDING_NEXT_OPEN") | status.isin(["PENDING_NEXT_OPEN", "PENDING"])
    return frame[decision.eq("ACCEPT") & pending_next_open].copy()


def build_order_intents(pending_orders):
    accepted = _accepted_pending_next_open_orders(pending_orders)
    rows = []
    for _, row in accepted.iterrows():
        action = str(row.get("action", "BUY")).strip().upper()
        side = action_to_order_side(action)
        notional = pd.to_numeric(pd.Series([row.get("paper_position_notional", pd.NA)]), errors="coerce").iloc[0]
        order_intent_id = _stable_order_intent_id(row, side)
        idempotency_phase = "PREVIEW"
        base_key = order_intent_id
        rows.append(
            {
                "order_intent_id": order_intent_id,
                "idempotency_phase": idempotency_phase,
                "idempotency_key": base_key,
                "execution_idempotency_key": execution_idempotency_key(base_key, idempotency_phase),
                "created_at": row.get("created_at", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")),
                "strategy_name": row.get("strategy_name", ""),
                "symbol": str(row.get("symbol", "")).strip().upper(),
                "side": side,
                "notional": notional,
                "estimated_qty": pd.NA,
                "order_type": "market",
                "time_in_force": "day",
                "source_signal_date": row.get("signal_date", ""),
                "status": "PREVIEW_ONLY",
                "reason": row.get("reason", "accepted_local_pending_order_pending_next_open"),
            }
        )
    return pd.DataFrame(rows, columns=ORDER_INTENT_COLUMNS), accepted


def _read_existing_audit_keys(latest_path):
    keys = set()
    for path in [
        latest_path / "execution_audit_log.csv",
        latest_path.parent / "history" / "execution_audit_log.csv",
    ]:
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path)
        except Exception:
            continue
        if "execution_idempotency_key" in frame:
            keys.update(frame["execution_idempotency_key"].dropna().astype(str).str.strip())
        elif "idempotency_key" in frame:
            keys.update("PREVIEW:" + frame["idempotency_key"].dropna().astype(str).str.strip())
    keys.discard("")
    return keys


def _apply_idempotency(order_intents, risk_report, existing_keys):
    intents = order_intents.copy()
    risk = risk_report.copy()
    if intents.empty or "execution_idempotency_key" not in intents:
        return intents, risk, 0
    duplicate_mask = intents["execution_idempotency_key"].astype(str).isin(existing_keys)
    duplicate_keys = set(intents.loc[duplicate_mask, "execution_idempotency_key"].astype(str))
    if duplicate_keys:
        intents.loc[duplicate_mask, "status"] = "PREVIEW_DUPLICATE_SKIPPED"
        if not risk.empty and "execution_idempotency_key" in risk:
            risk_mask = risk["execution_idempotency_key"].astype(str).isin(duplicate_keys)
            risk.loc[risk_mask, "risk_pass"] = False
            risk.loc[risk_mask, "block_reason"] = risk.loc[risk_mask, "block_reason"].apply(
                lambda value: "duplicate_preview_execution_idempotency_key"
                if not str(value).strip()
                else str(value) + ";duplicate_preview_execution_idempotency_key"
            )
    return intents, risk, int(duplicate_mask.sum())


def _audit_rows(order_intents, risk_report, run_datetime, broker_submit_enabled=False):
    risk_by_key = {}
    if risk_report is not None and not risk_report.empty:
        for _, row in risk_report.iterrows():
            risk_by_key[str(row.get("execution_idempotency_key", ""))] = row
    rows = []
    for _, intent in order_intents.iterrows():
        execution_key = str(intent.get("execution_idempotency_key", ""))
        risk = risk_by_key.get(execution_key, {})
        risk_pass = bool(risk.get("risk_pass", False))
        block_reason = str(risk.get("block_reason", ""))
        status = str(intent.get("status", "PREVIEW_ONLY"))
        base = {
            "run_datetime": run_datetime,
            "order_intent_id": intent.get("order_intent_id", ""),
            "idempotency_phase": intent.get("idempotency_phase", "PREVIEW"),
            "idempotency_key": intent.get("idempotency_key", ""),
            "execution_idempotency_key": execution_key,
            "strategy_name": intent.get("strategy_name", ""),
            "symbol": intent.get("symbol", ""),
            "side": intent.get("side", ""),
            "status": status,
            "risk_pass": risk_pass,
            "block_reason": block_reason,
            "would_submit": False,
            "broker_submit_enabled": bool(broker_submit_enabled),
            "live_trading_enabled": False,
        }
        events = [("PREVIEW_INTENT_CREATED", "preview order intent created")]
        if status == "PREVIEW_DUPLICATE_SKIPPED":
            events.append(
                (
                    "PREVIEW_DUPLICATE_SKIPPED",
                    "Preview duplicate does not imply submit duplicate.",
                )
            )
        elif risk_pass:
            events.extend(
                [
                    ("PREVIEW_RISK_PASSED", "preview risk gate passed"),
                ]
            )
        else:
            events.append(("PREVIEW_RISK_BLOCKED", block_reason or "preview risk gate blocked"))
        events.append(("SUBMIT_DISABLED", "Order submission disabled in current build."))
        for event_type, message in events:
            row = dict(base)
            row["event_type"] = event_type
            row["message"] = message
            rows.append(row)
    return pd.DataFrame(rows, columns=AUDIT_LOG_COLUMNS)


def _write_audit_logs(latest_path, audit):
    latest_audit_path = latest_path / "execution_audit_log.csv"
    history_dir = latest_path.parent / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_audit_path = history_dir / "execution_audit_log.csv"
    audit.to_csv(latest_audit_path, index=False)
    if history_audit_path.exists():
        try:
            previous = pd.read_csv(history_audit_path)
        except Exception:
            previous = pd.DataFrame(columns=AUDIT_LOG_COLUMNS)
        pd.concat([previous, audit], ignore_index=True).to_csv(history_audit_path, index=False)
    else:
        audit.to_csv(history_audit_path, index=False)
    return latest_audit_path, history_audit_path


def _submit_candidate_risk_pass_count(risk_report):
    if risk_report is None or risk_report.empty:
        return 0
    count = 0
    for _, row in risk_report.iterrows():
        if bool(row.get("risk_pass", False)):
            count += 1
            continue
        reasons = [
            reason.strip()
            for reason in str(row.get("block_reason", "")).split(";")
            if reason.strip()
        ]
        non_preview_reasons = [
            reason
            for reason in reasons
            if reason != "duplicate_preview_execution_idempotency_key"
        ]
        if reasons and not non_preview_reasons:
            count += 1
    return count


def write_execution_preview_reports(latest_dir, order_intents, risk_report, broker_submit_enabled=False):
    latest_path = Path(latest_dir)
    latest_path.mkdir(parents=True, exist_ok=True)
    run_datetime = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    existing_keys = _read_existing_audit_keys(latest_path)
    order_intents, risk_report, skipped_duplicates = _apply_idempotency(
        order_intents,
        risk_report,
        existing_keys,
    )
    order_intents.to_csv(latest_path / "order_intents.csv", index=False)
    write_risk_gate_reports(
        risk_report,
        latest_path / "order_risk_check_report.csv",
        latest_path / "order_risk_check_report.md",
    )
    risk_passed = int(risk_report["risk_pass"].astype(bool).sum()) if not risk_report.empty else 0
    blocked = int(len(risk_report) - risk_passed)
    duplicate_intent_count = int(
        risk_report.get("block_reason", pd.Series(dtype=str))
        .astype(str)
        .str.contains("duplicate_preview_execution_idempotency_key|duplicate_execution_idempotency_key")
        .sum()
    ) if not risk_report.empty else 0
    if not order_intents.empty and "side" in order_intents:
        side_series = order_intents["side"].map(normalize_order_side)
    else:
        side_series = pd.Series(dtype=str)
    short_mask = side_series.isin(["SHORT_SELL", "BUY_TO_COVER"])
    short_intents_count = int(short_mask.sum())
    short_keys = (
        set(order_intents.loc[short_mask, "execution_idempotency_key"].astype(str))
        if short_intents_count and "execution_idempotency_key" in order_intents
        else set()
    )
    if short_keys and not risk_report.empty:
        short_risk = risk_report[risk_report["execution_idempotency_key"].astype(str).isin(short_keys)]
        short_blocked_count = int(len(short_risk) - short_risk["risk_pass"].astype(bool).sum())
    else:
        short_blocked_count = 0
    audit = _audit_rows(order_intents, risk_report, run_datetime, broker_submit_enabled=broker_submit_enabled)
    latest_audit_path, history_audit_path = _write_audit_logs(latest_path, audit)
    summary = {
        "execution_preview_status": "OK",
        "pending_accepted_orders_count": int(len(order_intents)),
        "order_intents_count": int(len(order_intents)),
        "risk_pass_count": risk_passed,
        "submit_candidate_risk_pass_count": _submit_candidate_risk_pass_count(risk_report),
        "blocked_count": blocked,
        "duplicate_intent_count": duplicate_intent_count,
        "preview_duplicate_count": max(duplicate_intent_count, skipped_duplicates),
        "submit_duplicate_count": 0,
        "audit_log_rows": int(len(audit)),
        "skipped_duplicate_count": skipped_duplicates,
        "idempotency_enabled": True,
        "preview_idempotency_enabled": True,
        "submit_idempotency_enabled": False,
        "preview_duplicates_do_not_block_future_submit": True,
        "idempotency_phase": "PREVIEW",
        "would_submit_count": 0,
        "broker_submit_enabled": bool(broker_submit_enabled),
        "live_trading_enabled": False,
        "execution_preview_report_path": "reports/latest/execution_preview_report.md",
        "execution_audit_log_path": "reports/latest/execution_audit_log.csv",
        "history_audit_log_path": "reports/history/execution_audit_log.csv",
        "submit_order_called": False,
        "post_request_used": False,
        "short_trading_enabled": bool(SHORT_TRADING_ENABLED),
        "short_strategy_enabled": False,
        "short_intents_count": short_intents_count,
        "short_blocked_count": short_blocked_count,
        "short_submit_allowed": False,
        "short_readiness_status": "BLOCK",
        "borrow_check_available": False,
        "next_action": "Review risk blocks and keep broker_submit_enabled false until explicit approval.",
    }
    preview = pd.DataFrame([summary])
    preview.to_csv(latest_path / "execution_preview_report.csv", index=False)
    lines = [
        "# Execution Preview Report",
        "",
        f"- pending accepted orders count: {summary['pending_accepted_orders_count']}",
        f"- order intents count: {summary['order_intents_count']}",
        f"- risk passed count: {summary['risk_pass_count']}",
        f"- submit candidate risk passed count: {summary['submit_candidate_risk_pass_count']}",
        f"- blocked count: {summary['blocked_count']}",
        f"- duplicate_intent_count: {summary['duplicate_intent_count']}",
        f"- preview_duplicate_count: {summary['preview_duplicate_count']}",
        f"- submit_duplicate_count: {summary['submit_duplicate_count']}",
        f"- audit_log_rows: {summary['audit_log_rows']}",
        f"- skipped_duplicate_count: {summary['skipped_duplicate_count']}",
        f"- short_intents_count: {summary.get('short_intents_count', 0)}",
        f"- short_blocked_count: {summary.get('short_blocked_count', 0)}",
        "- short_trading_enabled: false",
        "- short_submit_allowed: false",
        "- idempotency_enabled: true",
        "- preview_idempotency_enabled: true",
        "- submit_idempotency_enabled: false",
        "- preview_duplicates_do_not_block_future_submit: True",
        "- message: Preview duplicate does not imply submit duplicate.",
        f"- would_submit count: {summary['would_submit_count']}",
        "- broker_submit_enabled: false",
        "- live_trading_enabled: false",
        f"- execution_audit_log_path: {latest_audit_path}",
        f"- history_audit_log_path: {history_audit_path}",
        "",
    ]
    (latest_path / "execution_preview_report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def _rewrite_execution_preview_summary(latest_path, summary):
    preview = pd.DataFrame([summary])
    preview.to_csv(latest_path / "execution_preview_report.csv", index=False)
    lines = [
        "# Execution Preview Report",
        "",
        f"- pending accepted orders count: {summary['pending_accepted_orders_count']}",
        f"- order intents count: {summary['order_intents_count']}",
        f"- risk passed count: {summary['risk_pass_count']}",
        f"- submit candidate risk passed count: {summary.get('submit_candidate_risk_pass_count', summary['risk_pass_count'])}",
        f"- blocked count: {summary['blocked_count']}",
        f"- duplicate_intent_count: {summary['duplicate_intent_count']}",
        f"- preview_duplicate_count: {summary['preview_duplicate_count']}",
        f"- submit_duplicate_count: {summary['submit_duplicate_count']}",
        f"- audit_log_rows: {summary['audit_log_rows']}",
        f"- skipped_duplicate_count: {summary['skipped_duplicate_count']}",
        f"- short_intents_count: {summary['short_intents_count']}",
        f"- short_blocked_count: {summary['short_blocked_count']}",
        "- short_trading_enabled: false",
        "- short_submit_allowed: false",
        "- idempotency_enabled: true",
        "- preview_idempotency_enabled: true",
        "- submit_idempotency_enabled: false",
        "- preview_duplicates_do_not_block_future_submit: True",
        "- message: Preview duplicate does not imply submit duplicate.",
        f"- pre_submit_readiness_status: {summary.get('pre_submit_readiness_status', 'not_generated')}",
        f"- blocking_checks_count: {summary.get('blocking_checks_count', 0)}",
        f"- warning_checks_count: {summary.get('warning_checks_count', 0)}",
        f"- pre_submit_readiness_report_path: {summary.get('pre_submit_readiness_report_path', 'reports/latest/pre_submit_readiness_report.md')}",
        f"- would_submit count: {summary['would_submit_count']}",
        "- broker_submit_enabled: false",
        "- live_trading_enabled: false",
        f"- execution_audit_log_path: {latest_path / 'execution_audit_log.csv'}",
        f"- history_audit_log_path: {latest_path.parent / 'history' / 'execution_audit_log.csv'}",
        "",
    ]
    (latest_path / "execution_preview_report.md").write_text("\n".join(lines), encoding="utf-8")


def run_execution_preview(latest_dir, max_new_orders_per_run=5, pre_submit_check=False):
    pending = read_pending_orders(latest_dir)
    intents, accepted = build_order_intents(pending)
    risk = evaluate_order_risk(intents, accepted, max_new_orders_per_run=max_new_orders_per_run)
    summary = write_execution_preview_reports(latest_dir, intents, risk, broker_submit_enabled=False)
    if pre_submit_check:
        readiness = write_pre_submit_readiness_report(latest_dir, summary)
        summary.update(
            {
                key: value
                for key, value in readiness.items()
                if key not in {"pre_submit_checks", "short_readiness_checks"}
            }
        )
        _rewrite_execution_preview_summary(Path(latest_dir), summary)
    return summary
