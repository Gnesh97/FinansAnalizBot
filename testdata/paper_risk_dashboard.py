from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


MAX_SINGLE_POSITION_PCT_WARNING = 8.0
MAX_TOP5_CONCENTRATION_PCT_WARNING = 35.0
MAX_PENDING_BUY_COUNT_WARNING = 3
DEFAULT_MAX_OPEN_POSITIONS = 30


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _number(value, default=0.0) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(parsed) if pd.notna(parsed) else float(default)


def _bool_false(value) -> bool:
    return str(value).strip().lower() == "false" or value is False


def _symbols(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "symbol" not in frame:
        return []
    return sorted(frame["symbol"].dropna().astype(str).str.upper().replace("", pd.NA).dropna().unique().tolist())


def _monitor_sections(monitor: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object], dict[str, object]]:
    if monitor.empty or "section" not in monitor:
        return pd.DataFrame(), {}, {}
    open_positions = monitor[monitor["section"].astype(str).eq("open_position")].copy()
    summary_rows = monitor[monitor["section"].astype(str).eq("summary")]
    risk_rows = monitor[monitor["section"].astype(str).eq("risk_check")]
    summary = {
        str(row.get("metric", "")): row.get("value", "")
        for _, row in summary_rows.iterrows()
        if str(row.get("metric", "")).strip()
    }
    risk = {
        str(row.get("metric", "")): row.get("value", "")
        for _, row in risk_rows.iterrows()
        if str(row.get("metric", "")).strip()
    }
    return open_positions, summary, risk


def _open_positions_from_trade_log(trade_log: pd.DataFrame) -> pd.DataFrame:
    if trade_log.empty:
        return pd.DataFrame()
    filled = trade_log[
        trade_log.get("fill_status", pd.Series("", index=trade_log.index)).astype(str).str.upper().eq("PAPER_FILLED")
    ].copy()
    if filled.empty:
        return pd.DataFrame()
    filled["side"] = filled.get("action", pd.Series("", index=filled.index)).astype(str).str.upper().map(
        {"BUY": 1, "SELL": -1}
    ).fillna(0)
    filled["signed_shares"] = pd.to_numeric(filled.get("shares", 0), errors="coerce").fillna(0.0) * filled["side"]
    rows = []
    for (symbol, strategy), group in filled.groupby(
        [filled.get("symbol", pd.Series("", index=filled.index)).astype(str).str.upper(), "strategy_name"],
        dropna=False,
    ):
        shares = float(group["signed_shares"].sum())
        if shares <= 0:
            continue
        buys = group[group["side"].eq(1)]
        rows.append(
            {
                "symbol": symbol,
                "strategy_name": strategy,
                "notional": pd.to_numeric(buys.get("notional", pd.Series(dtype=float)), errors="coerce").sum(),
            }
        )
    return pd.DataFrame(rows)


def _derive_max_open_positions(signals: pd.DataFrame, current_open: int, pending_buy: int) -> tuple[int, str]:
    if not signals.empty and "max_positions" in signals:
        values = pd.to_numeric(signals["max_positions"], errors="coerce").dropna()
        if not values.empty:
            return int(values.max()), "paper_signal_report.max_positions"
    derived = max(DEFAULT_MAX_OPEN_POSITIONS, current_open + pending_buy)
    return int(derived), "default_or_current_projection"


def _derive_max_new_accepted(signals: pd.DataFrame) -> tuple[int, str]:
    if not signals.empty and "paper_max_new_positions_per_day" in signals:
        values = pd.to_numeric(signals["paper_max_new_positions_per_day"], errors="coerce").dropna()
        if not values.empty:
            return int(values.max()), "paper_signal_report.paper_max_new_positions_per_day"
    return MAX_PENDING_BUY_COUNT_WARNING, "named_default"


def _current_exposure(open_positions: pd.DataFrame, summary: dict[str, object]) -> dict[str, object]:
    notional = pd.to_numeric(open_positions.get("notional", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    total_open_notional = _number(summary.get("total_open_notional", notional.sum()), notional.sum())
    open_count = int(_number(summary.get("open_paper_positions", len(open_positions)), len(open_positions)))
    largest = float(notional.max()) if notional.size else 0.0
    largest_pct = (largest / total_open_notional * 100.0) if total_open_notional else 0.0
    top5 = float(notional.sort_values(ascending=False).head(5).sum()) if notional.size else 0.0
    top5_pct = (top5 / total_open_notional * 100.0) if total_open_notional else 0.0
    average = (total_open_notional / open_count) if open_count else 0.0
    return {
        "open_paper_positions": open_count,
        "total_open_notional": total_open_notional,
        "total_unrealized_pnl": _number(summary.get("total_unrealized_pnl", 0.0)),
        "total_unrealized_pnl_pct": _number(summary.get("total_unrealized_pnl_pct", 0.0)),
        "gross_long_exposure": total_open_notional,
        "net_exposure": total_open_notional,
        "average_position_notional": average,
        "largest_position_notional": largest,
        "largest_position_pct_of_total": largest_pct,
        "top5_position_pct_of_total": top5_pct,
    }


def _pending_impact(pending: pd.DataFrame, inbox: pd.DataFrame, open_count: int, total_notional: float) -> dict[str, object]:
    if pending.empty:
        accepted = pending
        hold = pending
        blocked_duplicate_buy = pending
    else:
        decision = pending.get("user_decision", pd.Series("", index=pending.index)).fillna("").astype(str).str.upper()
        status = pending.get("status", pending.get("fill_status", pd.Series("", index=pending.index))).fillna("").astype(str).str.upper()
        reason = pending.get("reason", pd.Series("", index=pending.index)).fillna("").astype(str)
        active = status.isin(["PENDING", "PENDING_NEXT_OPEN", ""])
        accepted = pending[decision.eq("ACCEPT") & active].copy()
        hold = pending[decision.isin(["HOLD_REVIEW", "REVIEW_REQUIRED", ""]) & active].copy()
        blocked_duplicate_buy = pending[
            status.eq("BLOCKED")
            & pending.get("action", pd.Series("", index=pending.index)).fillna("").astype(str).str.upper().eq("BUY")
            & reason.str.contains("buy_already_open|already_open_position", case=False, na=False)
        ].copy()
    accepted_action = accepted.get("action", pd.Series("", index=accepted.index)).fillna("").astype(str).str.upper()
    hold_action = hold.get("action", pd.Series("", index=hold.index)).fillna("").astype(str).str.upper()
    accepted_buy = accepted[accepted_action.eq("BUY")]
    accepted_sell = accepted[accepted_action.eq("SELL")]
    hold_buy = hold[hold_action.eq("BUY")]
    hold_sell = hold[hold_action.eq("SELL")]
    if not inbox.empty:
        inbox_decision = inbox.get("user_decision", pd.Series("", index=inbox.index)).fillna("").astype(str).str.upper()
        inbox_action = inbox.get("action", pd.Series("", index=inbox.index)).fillna("").astype(str).str.upper()
        review = inbox_decision.isin(["HOLD_REVIEW", "REVIEW_REQUIRED", ""])
        hold_buy_count = max(len(hold_buy), int((review & inbox_action.eq("BUY")).sum()))
        hold_sell_count = max(len(hold_sell), int((review & inbox_action.eq("SELL")).sum()))
    else:
        hold_buy_count = len(hold_buy)
        hold_sell_count = len(hold_sell)
    buy_notional = pd.to_numeric(accepted_buy.get("paper_position_notional", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()
    count_change = int(len(accepted_buy) - len(accepted_sell))
    return {
        "accepted_pending_buy_count": int(len(accepted_buy)),
        "accepted_pending_sell_exit_count": int(len(accepted_sell)),
        "hold_review_buy_count": int(hold_buy_count),
        "hold_review_sell_count": int(hold_sell_count),
        "projected_open_positions_after_pending_fills": int(open_count + count_change),
        "projected_notional_after_pending_buys": float(total_notional + buy_notional),
        "projected_position_count_change": count_change,
        "pending_buy_symbols": _symbols(accepted_buy),
        "pending_sell_exit_symbols": _symbols(accepted_sell),
        "blocked_duplicate_buy_count": int(len(blocked_duplicate_buy)),
        "blocked_duplicate_buy_symbols": _symbols(blocked_duplicate_buy),
    }


def _risk_limits(
    exposure: dict[str, object],
    pending: dict[str, object],
    signals: pd.DataFrame,
    monitor_risk: dict[str, object],
) -> dict[str, object]:
    max_open, max_open_source = _derive_max_open_positions(
        signals,
        int(exposure["open_paper_positions"]),
        int(pending["accepted_pending_buy_count"]),
    )
    max_new, max_new_source = _derive_max_new_accepted(signals)
    missing_mark = _clean_text(monitor_risk.get("missing_mark_price_symbols", "none")).lower() not in {"", "none"}
    stale_mark = _clean_text(monitor_risk.get("stale_mark_price_symbols", "none")).lower() not in {"", "none"}
    projected_exceeds = int(pending["projected_open_positions_after_pending_fills"]) > max_open
    pending_buy_at_limit = int(pending["accepted_pending_buy_count"]) >= max_new
    concentration_warning = (
        float(exposure["largest_position_pct_of_total"]) > MAX_SINGLE_POSITION_PCT_WARNING
        or float(exposure["top5_position_pct_of_total"]) > MAX_TOP5_CONCENTRATION_PCT_WARNING
    )
    if projected_exceeds or missing_mark or pending_buy_at_limit:
        status = "BLOCK_NEW_BUY_REVIEW_REQUIRED"
    elif concentration_warning or stale_mark:
        status = "WARNING"
    else:
        status = "OK"
    return {
        "risk_status": status,
        "max_open_positions": max_open,
        "max_open_positions_source": max_open_source,
        "max_new_accepted_orders_per_cycle": max_new,
        "max_new_accepted_orders_source": max_new_source,
        "max_single_position_pct_warning": MAX_SINGLE_POSITION_PCT_WARNING,
        "max_top5_concentration_pct_warning": MAX_TOP5_CONCENTRATION_PCT_WARNING,
        "max_pending_buy_count_warning": MAX_PENDING_BUY_COUNT_WARNING,
        "projected_open_positions_exceeds_limit": projected_exceeds,
        "pending_buy_count_at_or_above_cycle_limit": pending_buy_at_limit,
        "single_position_concentration_warning": float(exposure["largest_position_pct_of_total"]) > MAX_SINGLE_POSITION_PCT_WARNING,
        "top5_concentration_warning": float(exposure["top5_position_pct_of_total"]) > MAX_TOP5_CONCENTRATION_PCT_WARNING,
        "missing_mark_warning": missing_mark,
        "stale_mark_warning": stale_mark,
    }


def _decision_guidance(pending: dict[str, object], risk: dict[str, object]) -> str:
    if risk["missing_mark_warning"] or risk["stale_mark_warning"]:
        return "fix data/monitor first"
    if int(pending["accepted_pending_sell_exit_count"]) > 0:
        return "prioritize exits"
    if risk["projected_open_positions_exceeds_limit"]:
        return "do not accept new BUY"
    if int(pending["accepted_pending_buy_count"]) > 0:
        return "wait for fills before more BUY"
    if risk["risk_status"] == "OK":
        return "review decision inbox"
    return "do not accept new BUY"


def _safety(ops: dict[str, object]) -> dict[str, object]:
    safety = ops.get("safety_gates", {}) if isinstance(ops, dict) else {}
    return {
        "broker_submit_enabled": _bool_false(safety.get("broker_submit_enabled", "false")) is False,
        "live_trading_enabled": _bool_false(safety.get("live_trading_enabled", "false")) is False,
        "short_trading_enabled": _bool_false(safety.get("short_trading_enabled", "false")) is False,
        "submit_order_called": _bool_false(safety.get("submit_order_called", "false")) is False,
        "post_request_used": _bool_false(safety.get("post_request_used", "false")) is False,
        "state_mutated": False,
    }


def _section_rows(section: str, values: dict[str, object]) -> list[dict[str, object]]:
    return [{"section": section, "metric": key, "value": value} for key, value in values.items()]


def _markdown_table(values: dict[str, object]) -> str:
    lines = ["| metric | value |", "| --- | --- |"]
    for key, value in values.items():
        if isinstance(value, list):
            value = ", ".join(value) if value else "none"
        lines.append(f"| {key} | {value} |")
    return "\n".join(lines)


def build_paper_risk_dashboard(latest_dir: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    trade_log = _read_csv(latest / "paper_trade_log.csv")
    pending_orders = _read_csv(latest / "paper_pending_orders.csv")
    portfolio_monitor = _read_csv(latest / "paper_portfolio_monitor.csv")
    intraday_monitor = _read_csv(latest / "intraday_paper_monitor.csv")
    decision_inbox = _read_csv(latest / "paper_decision_inbox.csv")
    ops = _read_json(latest / "paper_ops_dashboard.json")
    signals = _read_csv(latest / "paper_signal_report.csv")
    open_positions, summary, monitor_risk = _monitor_sections(portfolio_monitor)
    if open_positions.empty:
        open_positions = _open_positions_from_trade_log(trade_log)
    if intraday_monitor.empty:
        monitor_risk = {**monitor_risk, "intraday_monitor_available": False}
    else:
        monitor_risk = {**monitor_risk, "intraday_monitor_available": True}
    exposure = _current_exposure(open_positions, summary)
    pending = _pending_impact(
        pending_orders,
        decision_inbox,
        int(exposure["open_paper_positions"]),
        float(exposure["total_open_notional"]),
    )
    limits = _risk_limits(exposure, pending, signals, monitor_risk)
    guidance = {"decision_guidance": _decision_guidance(pending, limits)}
    safety = _safety(ops)
    return {
        "current_exposure": exposure,
        "pending_impact": pending,
        "risk_limits": limits,
        "decision_guidance": guidance,
        "safety": safety,
    }


def write_paper_risk_dashboard(latest_dir: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    dashboard = build_paper_risk_dashboard(latest)
    rows = []
    for section in ["current_exposure", "pending_impact", "risk_limits", "decision_guidance", "safety"]:
        rows.extend(_section_rows(section, dashboard[section]))
    csv_path = latest / "paper_risk_dashboard.csv"
    md_path = latest / "paper_risk_dashboard.md"
    json_path = latest / "paper_risk_dashboard.json"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(dashboard, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = [
        "# Paper Risk Dashboard",
        "",
        "## Current Exposure",
        "",
        _markdown_table(dashboard["current_exposure"]),
        "",
        "## Pending Impact",
        "",
        _markdown_table(dashboard["pending_impact"]),
        "",
        "## Risk Limits",
        "",
        _markdown_table(dashboard["risk_limits"]),
        "",
        "## Decision Guidance",
        "",
        _markdown_table(dashboard["decision_guidance"]),
        "",
        "## Safety",
        "",
        _markdown_table(dashboard["safety"]),
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "dashboard": dashboard,
        "csv_path": str(csv_path),
        "md_path": str(md_path),
        "json_path": str(json_path),
    }
