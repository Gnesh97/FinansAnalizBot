from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd


RESOLVING_DATA_POLICY_VALUES = {
    "ACK",
    "ACKNOWLEDGED",
    "ALLOW",
    "ALLOWED",
    "REPAIRED",
    "REPAIR_DONE",
    "REMOVED",
    "REMOVE_FROM_UNIVERSE",
}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _read_key_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^-\s+([^:]+):\s*(.*)$", line.strip())
        if match:
            values[match.group(1).strip()] = match.group(2).strip()
    return values


def _clean_value(value):
    if pd.isna(value):
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() == "true":
            return True
        if stripped.lower() == "false":
            return False
        if stripped.lower() in {"nan", "<na>"}:
            return ""
        try:
            numeric = float(stripped)
        except ValueError:
            return stripped
        if numeric.is_integer():
            return int(numeric)
        return numeric
    return value


def _latest_pipeline_row(latest_dir: Path) -> dict[str, object]:
    frame = _read_csv(latest_dir / "pipeline_health_log.csv")
    if frame.empty:
        return {}
    return frame.iloc[-1].to_dict()


def _monitor_values(latest_dir: Path) -> tuple[dict[str, object], dict[str, object]]:
    monitor = _read_csv(latest_dir / "paper_portfolio_monitor.csv")
    if monitor.empty or "section" not in monitor.columns:
        return {}, {}
    summary_rows = monitor[monitor["section"].astype(str).eq("summary")]
    check_rows = monitor[monitor["section"].astype(str).eq("risk_check")]
    summary = {
        str(row.get("metric", "")): _clean_value(row.get("value", ""))
        for _, row in summary_rows.iterrows()
        if str(row.get("metric", "")).strip()
    }
    checks = {
        str(row.get("metric", "")): _clean_value(row.get("value", ""))
        for _, row in check_rows.iterrows()
        if str(row.get("metric", "")).strip()
    }
    return summary, checks


def _symbols(series: pd.Series) -> list[str]:
    if series.empty:
        return []
    return sorted(series.dropna().astype(str).str.upper().replace("", pd.NA).dropna().unique().tolist())


def _pending_workflow(latest_dir: Path) -> dict[str, object]:
    pending = _read_csv(latest_dir / "paper_pending_orders.csv")
    review = _read_csv(latest_dir / "paper_order_review.csv")
    if pending.empty:
        accepted = pending
        hold_review = pending
    else:
        decision = pending.get("user_decision", pd.Series("", index=pending.index)).fillna("").astype(str).str.upper()
        accepted = pending[decision.eq("ACCEPT")]
        hold_review = pending[decision.isin(["HOLD_REVIEW", "REVIEW_REQUIRED", ""])]
    accepted_action = accepted.get("action", pd.Series("", index=accepted.index)).fillna("").astype(str).str.upper()
    review_decision = (
        review.get("user_decision", pd.Series("", index=review.index)).fillna("").astype(str).str.upper()
        if not review.empty
        else pd.Series(dtype=str)
    )
    review_needs = review[review_decision.isin(["HOLD_REVIEW", "REVIEW_REQUIRED", ""])] if not review.empty else review
    needing_symbols = set(_symbols(hold_review.get("symbol", pd.Series(dtype=str))))
    needing_symbols.update(_symbols(review_needs.get("symbol", pd.Series(dtype=str))))
    reason_text = " ".join(pending.get("reason", pd.Series("", index=pending.index)).fillna("").astype(str).tolist())
    stale_reason_text = " ".join(
        pending.get("pending_stale_reason", pd.Series("", index=pending.index)).fillna("").astype(str).tolist()
    )
    next_open_required = bool(re.search(r"missing_next_open|next_open", reason_text + " " + stale_reason_text, re.I))
    return {
        "accepted_pending_orders_count": int(len(accepted)),
        "accepted_pending_buy_symbols": _symbols(accepted.loc[accepted_action.eq("BUY"), "symbol"] if "symbol" in accepted else pd.Series(dtype=str)),
        "accepted_pending_sell_exit_symbols": _symbols(
            accepted.loc[accepted_action.eq("SELL"), "symbol"] if "symbol" in accepted else pd.Series(dtype=str)
        ),
        "hold_review_count": int(len(needing_symbols)),
        "symbols_needing_operator_decision": sorted(needing_symbols),
        "next_open_data_required": next_open_required,
    }


def _data_policy(latest_dir: Path) -> dict[str, object]:
    decisions = _read_csv(latest_dir / "data_policy_decisions.csv")
    review_values = _read_key_values(latest_dir / "data_policy_review_report.md")
    if decisions.empty:
        blocking_symbols: list[str] = []
    else:
        policy_status = decisions.get("policy_status", pd.Series("", index=decisions.index)).fillna("").astype(str).str.upper()
        operator_decision = decisions.get("operator_decision", pd.Series("", index=decisions.index)).fillna("").astype(str).str.upper()
        operator_note = decisions.get("operator_note", pd.Series("", index=decisions.index)).fillna("").astype(str).str.strip()
        unresolved = policy_status.str.startswith("BLOCK") & ~(
            operator_decision.isin(RESOLVING_DATA_POLICY_VALUES) & operator_note.ne("")
        )
        blocking_symbols = _symbols(decisions.loc[unresolved, "symbol"] if "symbol" in decisions else pd.Series(dtype=str))
    return {
        "data_policy_status": review_values.get("status", "not_generated" if decisions.empty else "UNKNOWN"),
        "data_policy_blocking_symbols": blocking_symbols,
    }


def _safety(latest_dir: Path, monitor_checks: dict[str, object]) -> dict[str, object]:
    execution = _read_key_values(latest_dir / "execution_preview_report.md")
    pre_submit = _read_key_values(latest_dir / "pre_submit_readiness_report.md")
    master = _read_key_values(latest_dir / "chatgpt_master_report.md")
    return {
        "broker_submit_enabled": execution.get("broker_submit_enabled", monitor_checks.get("broker_submit_enabled", master.get("broker_submit_enabled", "unknown"))),
        "live_trading_enabled": execution.get("live_trading_enabled", monitor_checks.get("live_trading_enabled", master.get("live_trading_enabled", "unknown"))),
        "short_trading_enabled": execution.get("short_trading_enabled", monitor_checks.get("short_trading_enabled", master.get("short_trading_enabled", "unknown"))),
        "submit_order_called": master.get("submit_order_called", monitor_checks.get("submit_order_called", "unknown")),
        "post_request_used": master.get("post_request_used", monitor_checks.get("post_request_used", "unknown")),
        "would_submit_count": execution.get("would_submit count", master.get("would_submit_count", "unknown")),
        "pre_submit_readiness_status": pre_submit.get("pre_submit_readiness_status", master.get("pre_submit_readiness_status", "unknown")),
    }


def _count_consistency(latest_dir: Path, monitor_summary: dict[str, object]) -> dict[str, object]:
    master = _read_key_values(latest_dir / "chatgpt_master_report.md")
    ledger = _read_key_values(latest_dir / "paper_ledger_audit_report.md")
    sqlite = _read_key_values(latest_dir / "sqlite_state_report.md")
    counts = {
        "master": master.get("open_paper_positions", ""),
        "monitor": str(monitor_summary.get("open_paper_positions", "")),
        "ledger": ledger.get("current_open_position_count", ""),
        "sqlite": sqlite.get("paper_open_positions_count", ""),
    }
    present = [str(value) for value in counts.values() if str(value).strip()]
    return {
        "open_position_counts": counts,
        "open_position_count_consistent": len(set(present)) <= 1 if present else False,
    }


def _next_action(pending: dict[str, object], risk: dict[str, object], data_policy: dict[str, object]) -> str:
    if data_policy["data_policy_blocking_symbols"]:
        return "resolve data policy"
    if pending["accepted_pending_sell_exit_symbols"]:
        return "review exits first"
    if pending["accepted_pending_orders_count"] and pending["next_open_data_required"]:
        return "wait for next open data, then run pending refresh + daily pipeline"
    if pending["accepted_pending_orders_count"]:
        return "run fill check"
    if pending["hold_review_count"]:
        return "review paper decisions"
    if str(risk.get("pending_sell_without_open_position_symbols", "none")).lower() not in {"none", "", "nan"}:
        return "review exits first"
    return "monitor positions"


def _markdown_table(rows: list[tuple[str, object]]) -> str:
    lines = ["| metric | value |", "| --- | --- |"]
    for key, value in rows:
        if isinstance(value, list):
            value = ", ".join(value) if value else "none"
        elif isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in value.items())
        text = str(value)
        lines.append(f"| {key} | {text} |")
    return "\n".join(lines)


def build_paper_ops_dashboard(latest_dir: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    pipeline = _latest_pipeline_row(latest)
    monitor_summary, monitor_checks = _monitor_values(latest)
    pending = _pending_workflow(latest)
    data_policy = _data_policy(latest)
    consistency = _count_consistency(latest, monitor_summary)
    regression = _read_key_values(latest / "regression_guard_report.md")
    tests = _read_key_values(latest / "test_run_report.md")
    ledger = _read_key_values(latest / "paper_ledger_audit_report.md")
    safety = _safety(latest, monitor_checks)
    risk = {
        "duplicate_open_symbols": monitor_checks.get("duplicate_open_symbol_count", "unknown"),
        "pending_buy_already_open_symbols": monitor_checks.get("pending_buy_already_open_symbols", "unknown"),
        "pending_sell_without_open_position_symbols": monitor_checks.get("pending_sell_without_open_position_symbols", "unknown"),
        "data_policy_blocking_symbols": data_policy["data_policy_blocking_symbols"],
        "missing_mark_price_symbols": monitor_checks.get("missing_mark_price_symbols", "unknown"),
        "stale_mark_price_symbols": monitor_checks.get("stale_mark_price_symbols", "unknown"),
        **consistency,
    }
    status = {
        "latest_daily_date": pipeline.get("latest_daily_date", ""),
        "latest_130min_date": pipeline.get("latest_130min_date", ""),
        "stale_data_warning": pipeline.get("stale_data_warning", ""),
        "pipeline_status": pipeline.get("status", ""),
        "regression_guard_status": regression.get("status", ""),
        "data_policy_status": data_policy["data_policy_status"],
        "paper_ledger_status": monitor_summary.get("paper_ledger_status", ledger.get("paper_ledger_status", "")),
        "test_status": tests.get("status", ""),
    }
    portfolio = {
        "total_filled_trades": monitor_summary.get("total_filled_trades", ""),
        "open_paper_positions": monitor_summary.get("open_paper_positions", ""),
        "pending_next_open_count": monitor_summary.get("pending_next_open_count", pipeline.get("pending_next_open_count", "")),
        "total_open_notional": monitor_summary.get("total_open_notional", ""),
        "unrealized_pnl": monitor_summary.get("total_unrealized_pnl", ""),
        "unrealized_pnl_pct": monitor_summary.get("total_unrealized_pnl_pct", ""),
        "best_open_position": monitor_summary.get("best_open_position", ""),
        "worst_open_position": monitor_summary.get("worst_open_position", ""),
    }
    dashboard = {
        "status_summary": status,
        "portfolio_summary": portfolio,
        "pending_workflow": pending,
        "risk_consistency": risk,
        "safety_gates": safety,
        "next_action": _next_action(pending, risk, data_policy),
    }
    return dashboard


def write_paper_ops_dashboard(latest_dir: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    dashboard = build_paper_ops_dashboard(latest)
    (latest / "paper_ops_dashboard.json").write_text(json.dumps(dashboard, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = [
        "# Paper Ops Dashboard",
        "",
        "## Status Summary",
        "",
        _markdown_table(list(dashboard["status_summary"].items())),
        "",
        "## Portfolio Summary",
        "",
        _markdown_table(list(dashboard["portfolio_summary"].items())),
        "",
        "## Pending Workflow",
        "",
        _markdown_table(list(dashboard["pending_workflow"].items())),
        "",
        "## Risk / Consistency",
        "",
        _markdown_table(list(dashboard["risk_consistency"].items())),
        "",
        "## Safety Gates",
        "",
        _markdown_table(list(dashboard["safety_gates"].items())),
        "",
        "## Next Action",
        "",
        str(dashboard["next_action"]),
        "",
    ]
    (latest / "paper_ops_dashboard.md").write_text("\n".join(lines), encoding="utf-8")
    return dashboard
