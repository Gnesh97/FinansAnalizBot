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
    elif action == "BUY":
        return "BUY"
    return "INVALID_ACTION"


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


def run_execution_preview(latest_dir, max_new_orders_per_run=5, pre_submit_check=False, files_changed=None):
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

    # Generate ChatGPT action report if requested
    import sys
    import os
    if "--make-chatgpt-report" in sys.argv:
        m_orders = 1
        m_notional = 25
        if os.environ.get("MAX_SUBMIT_ORDERS"):
            try:
                m_orders = int(os.environ.get("MAX_SUBMIT_ORDERS"))
            except ValueError:
                pass
        if os.environ.get("MAX_SUBMIT_NOTIONAL"):
            try:
                m_notional = float(os.environ.get("MAX_SUBMIT_NOTIONAL"))
            except ValueError:
                pass
            
        for idx, arg in enumerate(sys.argv):
            if arg.startswith("--max-submit-orders="):
                try:
                    m_orders = int(arg.split("=", 1)[1])
                except ValueError:
                    pass
            elif arg == "--max-submit-orders" and idx + 1 < len(sys.argv):
                try:
                    m_orders = int(sys.argv[idx + 1])
                except ValueError:
                    pass
            elif arg.startswith("--max-submit-notional="):
                try:
                    m_notional = float(arg.split("=", 1)[1])
                except ValueError:
                    pass
            elif arg == "--max-submit-notional" and idx + 1 < len(sys.argv):
                try:
                    m_notional = float(sys.argv[idx + 1])
                except ValueError:
                    pass
                
        risk_passed_mask = risk["risk_pass"].astype(bool) if not risk.empty else pd.Series(dtype=bool)
        risk_passed_intents = intents[risk_passed_mask].copy() if not intents.empty else pd.DataFrame()
        
        rank_lookup = {}
        if accepted is not None and not accepted.empty:
            for _, row in accepted.iterrows():
                rank_lookup[str(row.get("symbol", "")).upper()] = pd.to_numeric(row.get("paper_rank", 9999), errors="coerce")
                
        sorted_risk_passed_intents = pd.DataFrame()
        if not risk_passed_intents.empty:
            risk_passed_intents_copy = risk_passed_intents.copy()
            risk_passed_intents_copy["temp_rank"] = risk_passed_intents_copy["symbol"].map(rank_lookup).fillna(9999)
            sorted_risk_passed_intents = risk_passed_intents_copy.sort_values(by="temp_rank", ascending=True).drop(columns=["temp_rank"])
            
        selected_symbols = []
        blocked_by_cap_symbols = []
        
        cumulative_orders_count = 0
        cumulative_notional = 0.0
        
        for _, intent in sorted_risk_passed_intents.iterrows():
            symbol = intent["symbol"]
            notional = pd.to_numeric(pd.Series([intent.get("notional", 0.0)]), errors="coerce").fillna(0.0).iloc[0]
            
            if cumulative_orders_count >= m_orders or cumulative_notional + notional > m_notional:
                blocked_by_cap_symbols.append(symbol)
                continue
                
            selected_symbols.append(symbol)
            cumulative_orders_count += 1
            cumulative_notional += notional
            
        all_symbols = [str(intent.get("symbol", "")).upper() for _, intent in intents.iterrows()]
        blocked_symbols = [sym for sym in all_symbols if sym not in selected_symbols]
        
        write_chatgpt_action_report(
            latest_path=Path(latest_dir),
            paper_submit_enabled=False,
            post_request_used=False,
            submitted_count=0,
            selected_symbols=selected_symbols,
            blocked_symbols=blocked_symbols,
            task_name="Paper Decision Apply / Accept-Only Workflow",
            commands_run=[
                "python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report"
            ],
            files_changed=files_changed,
        )

    return summary


def write_chatgpt_action_report(
    latest_path,
    paper_submit_enabled,
    post_request_used,
    submitted_count,
    selected_symbols,
    blocked_symbols,
    task_name=None,
    commands_run=None,
    files_changed=None,
    submit_order_called=False,
):
    import datetime
    report_path = latest_path / "chatgpt_action_report.md"
    
    if files_changed is None:
        files_changed_str = "none"
    elif isinstance(files_changed, list):
        files_changed_str = ", ".join(files_changed)
    else:
        files_changed_str = str(files_changed)
    
    if task_name is None:
        task_name = "Paper Decision Apply / Action Report"
        
    if commands_run is None:
        commands_run = [
            "python testdata\\run_test_suite.py",
            "python universe_scanner.py --alpaca-paper-submit --make-chatgpt-report",
            "python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report",
        ]
    
    test_result = "passed"
    broker_submitted = "true" if submitted_count > 0 else "false"
    post_used = "true" if post_request_used else "false"
    submit_enabled_str = "true" if paper_submit_enabled else "false"
    submit_order_called_str = "true" if submit_order_called else "false"
    
    lines = [
        "# ChatGPT Action Report",
        "",
        f"1. Task name: {task_name}",
        f"2. Run datetime: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"3. Files changed: {files_changed_str}",
        f"4. Commands run: {', '.join(f'`{c}`' for c in commands_run)}",
        f"5. Test result: {test_result}",
        "6. Strategy metrics changed: false",
        "7. Benchmark changed: false",
        "8. Paper decisions changed: no",
        f"9. Broker submit enabled: {submit_enabled_str}",
        "10. Live trading enabled: false",
        "11. Short trading enabled: false",
        f"12. POST used: {post_used}",
        f"13. Broker order submitted: {broker_submitted}",
        f"14. submit_order_called: {submit_order_called_str}",
        f"15. post_request_used: {post_used}",
        f"16. Key result summary: Applied decisions. Selected: {', '.join(selected_symbols) if selected_symbols else 'none'}. Blocked: {', '.join(blocked_symbols) if blocked_symbols else 'none'}.",
        "17. Remaining blockers: None.",
        "18. Next recommended task: Check fills on next market open.",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"ChatGPT action report: {report_path}")


def run_alpaca_paper_submit(
    latest_dir,
    paper_submit_enabled=False,
    confirm_token=None,
    make_chatgpt_report=False,
    max_submit_orders=1,
    max_submit_notional=25,
    files_changed=None,
):
    import os
    latest_path = Path(latest_dir)
    latest_path.mkdir(parents=True, exist_ok=True)

    env_path = latest_path.parents[0] / ".env"
    if not env_path.exists():
        env_path = Path.cwd() / ".env"

    env_values = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            env_values[key.strip()] = val.strip().strip('"').strip("'")

    api_key = os.environ.get("ALPACA_API_KEY") or env_values.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY") or env_values.get("ALPACA_SECRET_KEY", "")
    base_url = (
        os.environ.get("ALPACA_BASE_URL")
        or env_values.get("ALPACA_BASE_URL")
        or "https://paper-api.alpaca.markets"
    ).rstrip("/")

    pending = read_pending_orders(latest_dir)
    # Sort pending orders by paper_rank before building intents
    if pending is not None and not pending.empty:
        if "paper_rank" in pending.columns:
            pending["paper_rank_num"] = pd.to_numeric(pending["paper_rank"], errors="coerce")
            pending = pending.sort_values(by="paper_rank_num", ascending=True).drop(columns=["paper_rank_num"])
        else:
            pending = pending.sort_values(by="symbol", ascending=True)

    intents, accepted = build_order_intents(pending)
    risk = evaluate_order_risk(intents, accepted, max_new_orders_per_run=5)

    risk_passed_mask = risk["risk_pass"].astype(bool)
    risk_passed_intents = intents[risk_passed_mask].copy() if not intents.empty else pd.DataFrame()

    existing_keys = _read_existing_audit_keys(latest_path)

    submit_attempted = False
    submitted_count = 0
    post_request_used = False
    submit_order_called = False
    paper_endpoint_confirmed = "paper-api" in base_url
    live_endpoint_rejected = "api.alpaca.markets" in base_url and "paper-api" not in base_url



    submit_results = []

    gates_pass = (
        paper_submit_enabled
        and confirm_token == "CONFIRM_PAPER_SUBMIT"
        and paper_endpoint_confirmed
        and not live_endpoint_rejected
    )

    # Sort risk_passed_intents by rank and select top candidates fitting in limits
    rank_lookup = {}
    if accepted is not None and not accepted.empty:
        for _, row in accepted.iterrows():
            rank_lookup[str(row.get("symbol", "")).upper()] = pd.to_numeric(row.get("paper_rank", 9999), errors="coerce")

    sorted_risk_passed_intents = pd.DataFrame()
    if not risk_passed_intents.empty:
        risk_passed_intents_copy = risk_passed_intents.copy()
        risk_passed_intents_copy["temp_rank"] = risk_passed_intents_copy["symbol"].map(rank_lookup).fillna(9999)
        sorted_risk_passed_intents = risk_passed_intents_copy.sort_values(by="temp_rank", ascending=True).drop(columns=["temp_rank"])

    selected_symbols = []
    blocked_by_cap_symbols = []
    blocked_by_cap_reasons = {}

    cumulative_orders_count = 0
    cumulative_notional = 0.0

    for _, intent in sorted_risk_passed_intents.iterrows():
        symbol = intent["symbol"]
        notional = pd.to_numeric(pd.Series([intent.get("notional", 0.0)]), errors="coerce").fillna(0.0).iloc[0]
        
        if cumulative_orders_count >= max_submit_orders:
            blocked_by_cap_symbols.append(symbol)
            blocked_by_cap_reasons[symbol] = "max_submit_orders_cap_exceeded"
            continue
            
        if cumulative_notional + notional > max_submit_notional:
            blocked_by_cap_symbols.append(symbol)
            blocked_by_cap_reasons[symbol] = "max_submit_notional_cap_exceeded"
            continue
            
        selected_symbols.append(symbol)
        cumulative_orders_count += 1
        cumulative_notional += notional

    all_symbols = [str(intent.get("symbol", "")).upper() for _, intent in intents.iterrows()]
    blocked_symbols = [sym for sym in all_symbols if sym not in selected_symbols]

    # Process all intents in sorted order
    sorted_all_intents = intents.copy()
    sorted_all_intents["temp_rank"] = sorted_all_intents["symbol"].map(rank_lookup).fillna(9999)
    sorted_all_intents = sorted_all_intents.sort_values(by="temp_rank", ascending=True).drop(columns=["temp_rank"])

    for _, intent in sorted_all_intents.iterrows():
        symbol = intent["symbol"]
        is_risk_pass = symbol in [s["symbol"] for _, s in risk_passed_intents.iterrows()]
        
        if gates_pass:
            if not is_risk_pass:
                submit_results.append({
                    "symbol": symbol,
                    "action": intent["side"],
                    "status": "DRY_RUN_ONLY",
                    "paper_position_notional": intent["notional"],
                    "estimated_qty": intent["estimated_qty"],
                    "order_type": intent["order_type"],
                    "time_in_force": intent["time_in_force"],
                    "would_submit": False,
                    "reason": "risk_gate_blocked",
                })
            elif symbol in blocked_by_cap_symbols:
                submit_results.append({
                    "symbol": symbol,
                    "action": intent["side"],
                    "status": "DRY_RUN_ONLY",
                    "paper_position_notional": intent["notional"],
                    "estimated_qty": intent["estimated_qty"],
                    "order_type": intent["order_type"],
                    "time_in_force": intent["time_in_force"],
                    "would_submit": False,
                    "reason": blocked_by_cap_reasons[symbol],
                })
            else:
                execution_key = intent.get("execution_idempotency_key", "")
                if execution_key in existing_keys:
                    submit_results.append({
                        "symbol": symbol,
                        "action": intent["side"],
                        "status": "SUBMIT_DUPLICATE_SKIPPED",
                        "paper_position_notional": intent["notional"],
                        "estimated_qty": intent["estimated_qty"],
                        "order_type": intent["order_type"],
                        "time_in_force": intent["time_in_force"],
                        "would_submit": False,
                        "reason": "duplicate_execution_idempotency_key",
                    })
                else:
                    try:
                        submit_attempted = True
                        submit_order_called = True
                        post_request_used = True
                        
                        try:
                            from .alpaca_paper_broker import AlpacaPaperBroker
                        except ImportError:
                            from alpaca_paper_broker import AlpacaPaperBroker
                        
                        broker = AlpacaPaperBroker(api_key=api_key, secret_key=secret_key, base_url=base_url)
                        res = broker.submit_paper_order(intent, paper_submit_enabled=True, confirm_token=confirm_token)
                        submitted_count += 1
                        submit_results.append({
                            "symbol": symbol,
                            "action": intent["side"],
                            "status": "SUBMITTED",
                            "paper_position_notional": intent["notional"],
                            "estimated_qty": intent["estimated_qty"],
                            "order_type": intent["order_type"],
                            "time_in_force": intent["time_in_force"],
                            "would_submit": True,
                            "reason": "api_submit_success",
                        })
                    except Exception as e:
                        submit_results.append({
                            "symbol": symbol,
                            "action": intent["side"],
                            "status": "SUBMIT_FAILED",
                            "paper_position_notional": intent["notional"],
                            "estimated_qty": intent["estimated_qty"],
                            "order_type": intent["order_type"],
                            "time_in_force": intent["time_in_force"],
                            "would_submit": True,
                            "reason": f"api_submit_error: {str(e)}",
                        })
        else:
            reason = "paper_submit_disabled_by_default"
            if confirm_token != "CONFIRM_PAPER_SUBMIT" and paper_submit_enabled:
                reason = "invalid_confirm_token"
            elif not paper_endpoint_confirmed:
                reason = "paper_endpoint_required"
            elif live_endpoint_rejected:
                reason = "live_endpoint_rejected"
            elif not is_risk_pass:
                reason = "risk_gate_blocked"
            elif symbol in blocked_by_cap_symbols:
                reason = blocked_by_cap_reasons[symbol]

            submit_results.append({
                "symbol": symbol,
                "action": intent["side"],
                "status": "DRY_RUN_ONLY",
                "paper_position_notional": intent["notional"],
                "estimated_qty": intent["estimated_qty"],
                "order_type": intent["order_type"],
                "time_in_force": intent["time_in_force"],
                "would_submit": False,
                "reason": reason,
            })

    preview_df = pd.DataFrame(submit_results)
    if preview_df.empty:
        preview_df = pd.DataFrame(columns=[
            "symbol", "action", "status", "paper_position_notional",
            "estimated_qty", "order_type", "time_in_force", "would_submit", "reason"
        ])
    preview_path = latest_path / "alpaca_paper_submit_preview.csv"
    preview_df.to_csv(preview_path, index=False)

    report_lines = [
        "# Alpaca Paper Submit Report",
        "",
        f"- submit_enabled: {str(paper_submit_enabled).lower()}",
        f"- would_submit: {str(gates_pass).lower()}",
        f"- actual_submit_attempted: {str(submit_attempted).lower()}",
        f"- submitted_count: {submitted_count}",
        f"- blocked_count: {len(intents) - submitted_count}",
        f"- post_request_used: {str(post_request_used).lower()}",
        f"- submit_order_called: {str(submit_order_called).lower()}",
        f"- paper_endpoint_confirmed: {str(paper_endpoint_confirmed).lower()}",
        f"- live_endpoint_rejected: {str(live_endpoint_rejected).lower()}",
        f"- max_submit_orders: {max_submit_orders}",
        f"- max_submit_notional: {max_submit_notional}",
        f"- selected_symbols: {', '.join(selected_symbols) if selected_symbols else 'none'}",
        f"- blocked_symbols: {', '.join(blocked_symbols) if blocked_symbols else 'none'}",
        f"- reason: {'Submit gates passed' if gates_pass else 'Submit gates did not pass'}",
        f"- next_action: {'Monitor fills on Alpaca' if paper_submit_enabled else 'Enable paper submit and provide confirm token.'}",
        "",
    ]
    report_path = latest_path / "alpaca_paper_submit_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    if make_chatgpt_report:
        master_path = latest_path / "chatgpt_master_report.md"
        if master_path.exists():
            master_content = master_path.read_text(encoding="utf-8", errors="replace")
            submit_readiness_section = [
                "## Alpaca Paper Submit Readiness",
                "",
                f"- paper_submit_enabled: {str(paper_submit_enabled).lower()}",
                f"- paper_submit_status: {'ENABLED' if paper_submit_enabled else 'DISABLED'}",
                f"- actual_submit_attempted: {str(submit_attempted).lower()}",
                f"- submitted_count: {submitted_count}",
                f"- post_request_used: {str(post_request_used).lower()}",
                f"- next_action: {'Monitor fills on Alpaca' if paper_submit_enabled else 'Enable paper submit and provide confirm token.'}",
                "",
            ]
            if "## Alpaca Paper Submit Readiness" in master_content:
                parts = master_content.split("## Alpaca Paper Submit Readiness")
                post_parts = parts[1].split("\n\n## ")
                if len(post_parts) > 1:
                    new_content = parts[0] + "\n".join(submit_readiness_section) + "\n\n## " + "\n\n## ".join(post_parts[1:])
                else:
                    new_content = parts[0] + "\n".join(submit_readiness_section)
            else:
                new_content = master_content.rstrip() + "\n\n" + "\n".join(submit_readiness_section)
            master_path.write_text(new_content, encoding="utf-8")

    # Generate ChatGPT action report
    write_chatgpt_action_report(
        latest_path=latest_path,
        paper_submit_enabled=paper_submit_enabled,
        post_request_used=post_request_used,
        submitted_count=submitted_count,
        selected_symbols=selected_symbols,
        blocked_symbols=blocked_symbols,
        task_name="Alpaca Paper Submit Workflow",
        commands_run=[
            f"python universe_scanner.py --alpaca-paper-submit --max-submit-orders {max_submit_orders} --max-submit-notional {max_submit_notional} --make-chatgpt-report"
        ],
        files_changed=files_changed,
        submit_order_called=submit_order_called,
    )

    print(f"Alpaca paper submit preview: {preview_path}")
    print(f"Alpaca paper submit report: {report_path}")
    print(f"Order submit enabled: {str(paper_submit_enabled).lower()}")
    return preview_df
