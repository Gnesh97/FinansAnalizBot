from pathlib import Path

import pandas as pd

try:
    from .paper_state_manager import read_pending_orders
    from .risk_gate import evaluate_order_risk, write_risk_gate_reports
except ImportError:
    from paper_state_manager import read_pending_orders
    from risk_gate import evaluate_order_risk, write_risk_gate_reports


ORDER_INTENT_COLUMNS = [
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
        side = "sell" if action == "SELL" else "buy"
        notional = pd.to_numeric(pd.Series([row.get("paper_position_notional", pd.NA)]), errors="coerce").iloc[0]
        rows.append(
            {
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


def write_execution_preview_reports(latest_dir, order_intents, risk_report, broker_submit_enabled=False):
    latest_path = Path(latest_dir)
    latest_path.mkdir(parents=True, exist_ok=True)
    order_intents.to_csv(latest_path / "order_intents.csv", index=False)
    write_risk_gate_reports(
        risk_report,
        latest_path / "order_risk_check_report.csv",
        latest_path / "order_risk_check_report.md",
    )
    risk_passed = int(risk_report["risk_pass"].astype(bool).sum()) if not risk_report.empty else 0
    blocked = int(len(risk_report) - risk_passed)
    summary = {
        "execution_preview_status": "OK",
        "pending_accepted_orders_count": int(len(order_intents)),
        "order_intents_count": int(len(order_intents)),
        "risk_pass_count": risk_passed,
        "blocked_count": blocked,
        "would_submit_count": 0,
        "broker_submit_enabled": bool(broker_submit_enabled),
        "live_trading_enabled": False,
        "execution_preview_report_path": "reports/latest/execution_preview_report.md",
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
        f"- blocked count: {summary['blocked_count']}",
        f"- would_submit count: {summary['would_submit_count']}",
        "- broker_submit_enabled: false",
        "- live_trading_enabled: false",
        "",
    ]
    (latest_path / "execution_preview_report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def run_execution_preview(latest_dir, max_new_orders_per_run=5):
    pending = read_pending_orders(latest_dir)
    intents, accepted = build_order_intents(pending)
    risk = evaluate_order_risk(intents, accepted, max_new_orders_per_run=max_new_orders_per_run)
    return write_execution_preview_reports(latest_dir, intents, risk, broker_submit_enabled=False)
