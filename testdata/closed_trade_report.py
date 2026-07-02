from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


CLOSED_TRADE_COLUMNS = [
    "closed_trade_id",
    "strategy_name",
    "symbol",
    "entry_signal_date",
    "entry_fill_timestamp",
    "entry_price",
    "exit_signal_date",
    "exit_fill_timestamp",
    "exit_price",
    "shares",
    "entry_notional",
    "exit_notional",
    "realized_pnl",
    "realized_pnl_pct",
    "holding_calendar_days",
    "holding_trading_days",
    "entry_trade_key",
    "exit_trade_key",
    "close_reason",
    "lot_match_method",
    "warning",
]


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _number(value, default=0.0) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(parsed) if pd.notna(parsed) else float(default)


def _trade_timestamp(row) -> str:
    return _clean_text(row.get("date", row.get("fill_timestamp", "")))


def _trade_key(row, index: int) -> str:
    parts = [
        _clean_text(row.get("strategy_name", "")),
        _clean_text(row.get("symbol", "")).upper(),
        _clean_text(row.get("action", "")).upper(),
        _clean_text(row.get("signal_date", "")),
        _trade_timestamp(row),
        str(index),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _holding_days(entry_ts: str, exit_ts: str):
    entry = pd.to_datetime(entry_ts, errors="coerce")
    exit_ = pd.to_datetime(exit_ts, errors="coerce")
    if pd.isna(entry) or pd.isna(exit_):
        return pd.NA
    if entry.tzinfo is not None:
        entry = entry.tz_convert("UTC").tz_localize(None)
    if exit_.tzinfo is not None:
        exit_ = exit_.tz_convert("UTC").tz_localize(None)
    return max(int((exit_.normalize() - entry.normalize()).days), 0)


def _closed_trade_id(entry_key: str, exit_key: str, part_index: int) -> str:
    return hashlib.sha1(f"{entry_key}|{exit_key}|{part_index}".encode("utf-8")).hexdigest()[:16]


def _filled_trades(trade_log: pd.DataFrame) -> pd.DataFrame:
    if trade_log.empty:
        return pd.DataFrame()
    status = trade_log.get("fill_status", pd.Series("", index=trade_log.index)).astype(str).str.upper()
    filled = trade_log[status.eq("PAPER_FILLED")].copy()
    if filled.empty:
        return filled
    filled["_original_index"] = filled.index
    try:
        filled["_timestamp"] = pd.to_datetime(
            filled.get("date", pd.Series("", index=filled.index)),
            errors="coerce",
            format="mixed",
        )
    except TypeError:
        filled["_timestamp"] = pd.to_datetime(
            filled.get("date", pd.Series("", index=filled.index)),
            errors="coerce",
        )
    return filled.sort_values(["strategy_name", "symbol", "_timestamp", "_original_index"], kind="stable")


def build_closed_trade_report(trade_log: pd.DataFrame, portfolio_monitor: pd.DataFrame | None = None) -> dict[str, object]:
    filled = _filled_trades(trade_log.copy() if trade_log is not None else pd.DataFrame())
    closed_rows: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    open_lots: list[dict[str, object]] = []
    lots: dict[tuple[str, str], list[dict[str, object]]] = {}
    part_index = 0

    for _, row in filled.iterrows():
        strategy = _clean_text(row.get("strategy_name", ""))
        symbol = _clean_text(row.get("symbol", "")).upper()
        action = _clean_text(row.get("action", "")).upper()
        key = (strategy, symbol)
        trade_key = _trade_key(row, int(row.get("_original_index", 0)))
        price = _number(row.get("assumed_fill_price", row.get("fill_price", pd.NA)))
        shares = _number(row.get("shares", 0.0))
        if shares <= 0 or price <= 0:
            continue
        timestamp = _trade_timestamp(row)
        if action == "BUY":
            lots.setdefault(key, []).append(
                {
                    "strategy_name": strategy,
                    "symbol": symbol,
                    "entry_signal_date": _clean_text(row.get("signal_date", "")),
                    "entry_fill_timestamp": timestamp,
                    "entry_price": price,
                    "remaining_shares": shares,
                    "entry_trade_key": trade_key,
                }
            )
            continue
        if action != "SELL":
            continue
        remaining_sell = shares
        matched_any = False
        while remaining_sell > 1e-9 and lots.get(key):
            lot = lots[key][0]
            close_shares = min(float(lot["remaining_shares"]), remaining_sell)
            part_index += 1
            matched_any = True
            entry_price = float(lot["entry_price"])
            entry_notional = entry_price * close_shares
            exit_notional = price * close_shares
            realized = (price - entry_price) * close_shares
            closed_rows.append(
                {
                    "closed_trade_id": _closed_trade_id(str(lot["entry_trade_key"]), trade_key, part_index),
                    "strategy_name": strategy,
                    "symbol": symbol,
                    "entry_signal_date": lot["entry_signal_date"],
                    "entry_fill_timestamp": lot["entry_fill_timestamp"],
                    "entry_price": entry_price,
                    "exit_signal_date": _clean_text(row.get("signal_date", "")),
                    "exit_fill_timestamp": timestamp,
                    "exit_price": price,
                    "shares": close_shares,
                    "entry_notional": entry_notional,
                    "exit_notional": exit_notional,
                    "realized_pnl": realized,
                    "realized_pnl_pct": (realized / entry_notional * 100.0) if entry_notional else 0.0,
                    "holding_calendar_days": _holding_days(str(lot["entry_fill_timestamp"]), timestamp),
                    "holding_trading_days": "",
                    "entry_trade_key": lot["entry_trade_key"],
                    "exit_trade_key": trade_key,
                    "close_reason": _clean_text(row.get("reason", "")),
                    "lot_match_method": "FIFO",
                    "warning": "",
                }
            )
            lot["remaining_shares"] = float(lot["remaining_shares"]) - close_shares
            remaining_sell -= close_shares
            if float(lot["remaining_shares"]) <= 1e-9:
                lots[key].pop(0)
        if remaining_sell > 1e-9:
            issues.append(
                {
                    "issue_type": "unmatched_sell_issue",
                    "strategy_name": strategy,
                    "symbol": symbol,
                    "shares": remaining_sell,
                    "exit_trade_key": trade_key,
                    "warning": "SELL has no matching open BUY lot; short lot not created.",
                }
            )
        elif not matched_any:
            issues.append(
                {
                    "issue_type": "unmatched_sell_issue",
                    "strategy_name": strategy,
                    "symbol": symbol,
                    "shares": shares,
                    "exit_trade_key": trade_key,
                    "warning": "SELL has no matching open BUY lot; short lot not created.",
                }
            )

    for lot_list in lots.values():
        for lot in lot_list:
            if float(lot["remaining_shares"]) <= 1e-9:
                continue
            open_lots.append(
                {
                    "strategy_name": lot["strategy_name"],
                    "symbol": lot["symbol"],
                    "entry_signal_date": lot["entry_signal_date"],
                    "entry_fill_timestamp": lot["entry_fill_timestamp"],
                    "entry_price": lot["entry_price"],
                    "remaining_shares": lot["remaining_shares"],
                    "entry_trade_key": lot["entry_trade_key"],
                    "lot_match_method": "FIFO",
                }
            )

    closed = pd.DataFrame(closed_rows, columns=CLOSED_TRADE_COLUMNS)
    open_lots_frame = pd.DataFrame(open_lots)
    issues_frame = pd.DataFrame(issues)
    reconciliation = _reconcile(open_lots_frame, portfolio_monitor)
    return {
        "closed_trades": closed,
        "open_lots": open_lots_frame,
        "issues": issues_frame,
        "summary": _summary(closed, open_lots_frame, issues_frame),
        "reconciliation": reconciliation,
        "safety": _safety(),
    }


def _summary(closed: pd.DataFrame, open_lots: pd.DataFrame, issues: pd.DataFrame) -> dict[str, object]:
    pnl = pd.to_numeric(closed.get("realized_pnl", pd.Series(dtype=float)), errors="coerce") if not closed.empty else pd.Series(dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    breakeven = pnl[pnl == 0]
    gross_win = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses.sum())) if not losses.empty else 0.0
    holding = pd.to_numeric(closed.get("holding_calendar_days", pd.Series(dtype=float)), errors="coerce") if not closed.empty else pd.Series(dtype=float)
    return {
        "closed_trade_count": int(len(closed)),
        "winning_trade_count": int(len(wins)),
        "losing_trade_count": int(len(losses)),
        "breakeven_trade_count": int(len(breakeven)),
        "win_rate_pct": float(len(wins) / len(closed) * 100.0) if len(closed) else 0.0,
        "total_realized_pnl": float(pnl.sum()) if not pnl.empty else 0.0,
        "average_realized_pnl": float(pnl.mean()) if not pnl.empty else 0.0,
        "median_realized_pnl": float(pnl.median()) if not pnl.empty else 0.0,
        "average_win": float(wins.mean()) if not wins.empty else 0.0,
        "average_loss": float(losses.mean()) if not losses.empty else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss else (float("inf") if gross_win else 0.0),
        "best_closed_trade_symbol": _best_worst_symbol(closed, pnl, "best"),
        "worst_closed_trade_symbol": _best_worst_symbol(closed, pnl, "worst"),
        "average_holding_calendar_days": float(holding.mean()) if not holding.dropna().empty else 0.0,
        "open_lot_count_after_matching": int(len(open_lots)),
        "unmatched_sell_count": int(len(issues[issues.get("issue_type", pd.Series(dtype=str)).astype(str).eq("unmatched_sell_issue")])) if not issues.empty else 0,
        "state_mutated": False,
        "broker_submit_enabled": False,
        "live_trading_enabled": False,
        "short_trading_enabled": False,
        "submit_order_called": False,
        "post_request_used": False,
    }


def _best_worst_symbol(closed: pd.DataFrame, pnl: pd.Series, mode: str) -> str:
    if closed.empty or pnl.dropna().empty:
        return "none"
    index = pnl.idxmax() if mode == "best" else pnl.idxmin()
    return _clean_text(closed.loc[index, "symbol"]).upper()


def _monitor_open_symbols(portfolio_monitor: pd.DataFrame | None) -> set[str]:
    if portfolio_monitor is None or portfolio_monitor.empty or "section" not in portfolio_monitor:
        return set()
    open_rows = portfolio_monitor[portfolio_monitor["section"].astype(str).eq("open_position")]
    if open_rows.empty or "symbol" not in open_rows:
        return set()
    return set(open_rows["symbol"].dropna().astype(str).str.upper().replace("", pd.NA).dropna().tolist())


def _reconcile(open_lots: pd.DataFrame, portfolio_monitor: pd.DataFrame | None) -> dict[str, object]:
    reconstructed = set()
    if not open_lots.empty and "symbol" in open_lots:
        reconstructed = set(open_lots["symbol"].dropna().astype(str).str.upper().replace("", pd.NA).dropna().tolist())
    monitor_symbols = _monitor_open_symbols(portfolio_monitor)
    monitor_missing = portfolio_monitor is None or portfolio_monitor.empty
    return {
        "reconstructed_open_position_count": int(len(reconstructed)),
        "monitor_open_position_count": int(len(monitor_symbols)) if not monitor_missing else "",
        "open_position_count_match": bool(len(reconstructed) == len(monitor_symbols)) if not monitor_missing else False,
        "symbols_only_in_reconstructed": sorted(reconstructed - monitor_symbols),
        "symbols_only_in_monitor": sorted(monitor_symbols - reconstructed),
        "warning": "paper_portfolio_monitor.csv missing" if monitor_missing else "",
    }


def _safety() -> dict[str, object]:
    return {
        "state_mutated": False,
        "broker_submit_enabled": False,
        "live_trading_enabled": False,
        "short_trading_enabled": False,
        "submit_order_called": False,
        "post_request_used": False,
    }


def _frame_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    return json.loads(frame.fillna("").to_json(orient="records"))


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "none"
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(_clean_text(row.get(col, "")) for col in columns) + " |")
    return "\n".join(lines)


def _metric_table(values: dict[str, object]) -> str:
    rows = []
    for key, value in values.items():
        if isinstance(value, list):
            value = ", ".join(value) if value else "none"
        rows.append({"metric": key, "value": value})
    return _markdown_table(pd.DataFrame(rows))


def write_closed_trade_report(latest_dir: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    result = build_closed_trade_report(
        _read_csv(latest / "paper_trade_log.csv"),
        _read_csv(latest / "paper_portfolio_monitor.csv"),
    )
    csv_path = latest / "closed_trade_report.csv"
    md_path = latest / "closed_trade_report.md"
    json_path = latest / "closed_trade_report.json"
    result["closed_trades"].to_csv(csv_path, index=False)
    payload = {
        "summary": result["summary"],
        "closed_trades": _frame_records(result["closed_trades"]),
        "open_lots": _frame_records(result["open_lots"]),
        "issues": _frame_records(result["issues"]),
        "reconciliation": result["reconciliation"],
        "safety": result["safety"],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = [
        "# Closed Trade Report",
        "",
        "## Summary",
        "",
        _metric_table(result["summary"]),
        "",
        "## Closed Trades",
        "",
        _markdown_table(result["closed_trades"]),
        "",
        "## Open Lots After FIFO Matching",
        "",
        _markdown_table(result["open_lots"]),
        "",
        "## Issues / Warnings",
        "",
        _markdown_table(result["issues"]),
        "",
        "## Reconciliation",
        "",
        _metric_table(result["reconciliation"]),
        "",
        "## Safety Footer",
        "",
        _metric_table(result["safety"]),
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "summary": result["summary"],
        "reconciliation": result["reconciliation"],
        "csv_path": str(csv_path),
        "md_path": str(md_path),
        "json_path": str(json_path),
    }
