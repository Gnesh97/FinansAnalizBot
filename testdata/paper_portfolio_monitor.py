from pathlib import Path

import pandas as pd

try:
    from .paper_state_manager import read_order_review, read_pending_orders, read_trade_log
except ImportError:
    from paper_state_manager import read_order_review, read_pending_orders, read_trade_log


MONITOR_COLUMNS = [
    "section",
    "metric",
    "symbol",
    "strategy_name",
    "action",
    "user_decision",
    "fill_status",
    "expected_next_open_date",
    "paper_rank",
    "ranking_score",
    "liquidity_guard_pass",
    "reason",
    "entry_signal_date",
    "fill_timestamp",
    "fill_price",
    "shares",
    "notional",
    "latest_price",
    "latest_price_timestamp",
    "unrealized_pnl",
    "unrealized_pnl_pct",
    "holding_calendar_days",
    "holding_trading_days",
    "value",
]


def _clean_text(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _number(value):
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


def _latest_mark(asset_data, symbol):
    frame = (asset_data or {}).get(str(symbol).upper())
    if frame is None or frame.empty or "Close" not in frame:
        return pd.NA, ""
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    if close.empty:
        return pd.NA, ""
    timestamp = close.index[-1]
    return float(close.iloc[-1]), str(timestamp)


def _max_mark_timestamp(asset_data):
    timestamps = []
    for frame in (asset_data or {}).values():
        if frame is not None and not frame.empty and "Close" in frame:
            close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
            if not close.empty:
                timestamps.append(pd.Timestamp(close.index[-1]))
    if not timestamps:
        return pd.NaT
    normalized = []
    for timestamp in timestamps:
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC").tz_localize(None)
        normalized.append(timestamp)
    return max(normalized)


def _action_side(value):
    action = str(value).strip().upper()
    if action == "SELL":
        return -1
    if action == "BUY":
        return 1
    return 0


def _open_position_rows(trade_log, asset_data):
    if trade_log is None or trade_log.empty:
        return pd.DataFrame(columns=MONITOR_COLUMNS)
    filled = trade_log[
        trade_log.get("fill_status", pd.Series("", index=trade_log.index)).astype(str).str.upper().eq("PAPER_FILLED")
    ].copy()
    if filled.empty:
        return pd.DataFrame(columns=MONITOR_COLUMNS)
    rows = []
    for _, row in filled.iterrows():
        symbol = str(row.get("symbol", "")).strip().upper()
        shares = _number(row.get("shares", pd.NA))
        fill_price = _number(row.get("assumed_fill_price", pd.NA))
        side = _action_side(row.get("action", ""))
        if side == 0 or pd.isna(shares) or float(shares) == 0:
            continue
        signed_shares = float(shares) * side
        if side < 0:
            signed_shares = -abs(float(shares))
        latest_price, latest_ts = _latest_mark(asset_data, symbol)
        notional = _number(row.get("notional", pd.NA))
        unrealized_pnl = pd.NA
        unrealized_pct = pd.NA
        if side > 0 and pd.notna(latest_price) and pd.notna(fill_price):
            unrealized_pnl = (float(latest_price) - float(fill_price)) * float(shares)
            if float(fill_price) != 0:
                unrealized_pct = (float(latest_price) / float(fill_price) - 1.0) * 100.0
        fill_ts = _clean_text(row.get("date", ""))
        holding_days = pd.NA
        if latest_ts:
            start = pd.to_datetime(fill_ts, errors="coerce")
            end = pd.to_datetime(latest_ts, errors="coerce")
            if pd.notna(start) and pd.notna(end):
                if start.tzinfo is not None:
                    start = start.tz_convert("UTC").tz_localize(None)
                if end.tzinfo is not None:
                    end = end.tz_convert("UTC").tz_localize(None)
                holding_days = max(int((end.normalize() - start.normalize()).days), 0)
        rows.append(
            {
                "section": "open_position",
                "symbol": symbol,
                "strategy_name": row.get("strategy_name", ""),
                "action": row.get("action", ""),
                "entry_signal_date": row.get("signal_date", ""),
                "fill_timestamp": fill_ts,
                "fill_price": fill_price,
                "shares": signed_shares,
                "notional": notional,
                "latest_price": latest_price,
                "latest_price_timestamp": latest_ts,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": unrealized_pct,
                "holding_calendar_days": holding_days,
                "holding_trading_days": "",
            }
        )
    open_rows = pd.DataFrame(rows)
    if open_rows.empty:
        return pd.DataFrame(columns=MONITOR_COLUMNS)
    grouped = []
    for (symbol, strategy), group in open_rows.groupby(["symbol", "strategy_name"], dropna=False):
        net_shares = group["shares"].sum()
        if net_shares <= 0:
            continue
        buys = group[group["shares"] > 0].copy()
        if buys.empty:
            continue
        total_buy_shares = buys["shares"].sum()
        weighted_fill = (buys["fill_price"] * buys["shares"]).sum() / total_buy_shares if total_buy_shares else pd.NA
        latest_price = buys["latest_price"].dropna().iloc[-1] if buys["latest_price"].notna().any() else pd.NA
        latest_ts = _clean_text(buys["latest_price_timestamp"].dropna().iloc[-1]) if buys["latest_price_timestamp"].notna().any() else ""
        unrealized_pnl = pd.NA
        unrealized_pct = pd.NA
        if pd.notna(latest_price) and pd.notna(weighted_fill):
            unrealized_pnl = (float(latest_price) - float(weighted_fill)) * float(net_shares)
            if float(weighted_fill) != 0:
                unrealized_pct = (float(latest_price) / float(weighted_fill) - 1.0) * 100.0
        grouped.append(
            {
                "section": "open_position",
                "symbol": symbol,
                "strategy_name": strategy,
                "entry_signal_date": buys["entry_signal_date"].iloc[0],
                "fill_timestamp": buys["fill_timestamp"].iloc[0],
                "fill_price": weighted_fill,
                "shares": net_shares,
                "notional": buys["notional"].sum(),
                "latest_price": latest_price,
                "latest_price_timestamp": latest_ts,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": unrealized_pct,
                "holding_calendar_days": buys["holding_calendar_days"].dropna().max() if buys["holding_calendar_days"].notna().any() else pd.NA,
                "holding_trading_days": "",
            }
        )
    return pd.DataFrame(grouped)


def _pending_rows(pending_orders):
    if pending_orders is None or pending_orders.empty:
        return pd.DataFrame(columns=MONITOR_COLUMNS)
    rows = []
    for _, row in pending_orders.iterrows():
        rows.append(
            {
                "section": "pending_order",
                "symbol": str(row.get("symbol", "")).strip().upper(),
                "action": row.get("action", ""),
                "user_decision": row.get("user_decision", ""),
                "fill_status": row.get("fill_status", row.get("status", "")),
                "expected_next_open_date": row.get("expected_next_open_date", ""),
                "paper_rank": row.get("paper_rank", ""),
                "ranking_score": row.get("ranking_score", ""),
                "liquidity_guard_pass": row.get("liquidity_guard_pass", ""),
                "reason": row.get("reason", ""),
            }
        )
    return pd.DataFrame(rows)


def _summary_rows(summary):
    return pd.DataFrame(
        [{"section": "summary", "metric": key, "value": value} for key, value in summary.items()]
    )


def _risk_rows(checks):
    return pd.DataFrame(
        [{"section": "risk_check", "metric": key, "value": value} for key, value in checks.items()]
    )


def _join_symbols(symbols):
    cleaned = sorted(str(symbol).upper() for symbol in symbols if str(symbol).strip())
    return ",".join(cleaned) if cleaned else "none"


def build_paper_portfolio_monitor(trade_log, pending_orders, order_review=None, asset_data=None, safety_flags=None):
    trade_log = trade_log.copy() if trade_log is not None else pd.DataFrame()
    pending_orders = pending_orders.copy() if pending_orders is not None else pd.DataFrame()
    open_positions = _open_position_rows(trade_log, asset_data)
    pending = _pending_rows(pending_orders)
    open_symbols = set(open_positions.get("symbol", pd.Series(dtype=str)).astype(str).str.upper())
    pending_action = pending_orders.get("action", pd.Series("", index=pending_orders.index)).astype(str).str.upper()
    pending_symbol = pending_orders.get("symbol", pd.Series("", index=pending_orders.index)).astype(str).str.upper()
    pending_status = pending_orders.get("status", pd.Series("", index=pending_orders.index)).astype(str).str.upper()
    pending_next = pending_status.isin(["PENDING", "PENDING_NEXT_OPEN"])
    pending_buy_symbols = set(pending_symbol[pending_action.eq("BUY") & pending_next])
    pending_sell_symbols = set(pending_symbol[pending_action.eq("SELL") & pending_next])
    missing_mark = set(open_positions[open_positions.get("latest_price", pd.Series(dtype=object)).isna()].get("symbol", pd.Series(dtype=str)).astype(str))
    max_mark_ts = _max_mark_timestamp(asset_data)
    stale_mark = set()
    if pd.notna(max_mark_ts) and not open_positions.empty:
        for _, row in open_positions.iterrows():
            ts = pd.to_datetime(row.get("latest_price_timestamp", ""), errors="coerce")
            if pd.isna(ts):
                continue
            if ts.tzinfo is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
            if ts < max_mark_ts:
                stale_mark.add(str(row.get("symbol", "")).upper())
    total_unrealized = pd.to_numeric(open_positions.get("unrealized_pnl", pd.Series(dtype=float)), errors="coerce").sum(min_count=1)
    total_open_notional = pd.to_numeric(open_positions.get("notional", pd.Series(dtype=float)), errors="coerce").sum(min_count=1)
    total_unrealized_pct = pd.NA
    if pd.notna(total_unrealized) and pd.notna(total_open_notional) and float(total_open_notional) != 0:
        total_unrealized_pct = float(total_unrealized) / float(total_open_notional) * 100.0
    best_symbol = "none"
    worst_symbol = "none"
    pnl_series = pd.to_numeric(open_positions.get("unrealized_pnl", pd.Series(dtype=float)), errors="coerce")
    if not pnl_series.dropna().empty:
        best_symbol = str(open_positions.loc[pnl_series.idxmax(), "symbol"])
        worst_symbol = str(open_positions.loc[pnl_series.idxmin(), "symbol"])
    duplicate_counts = open_positions.get("symbol", pd.Series(dtype=str)).value_counts()
    duplicate_open_symbols = set(duplicate_counts[duplicate_counts > 1].index.astype(str))
    realized_pnl = pd.NA
    summary = {
        "total_filled_trades": int(
            trade_log.get("fill_status", pd.Series("", index=trade_log.index)).astype(str).str.upper().eq("PAPER_FILLED").sum()
        ) if not trade_log.empty else 0,
        "open_paper_positions": int(len(open_positions)),
        "pending_next_open_count": int(pending_next.sum()) if not pending_orders.empty else 0,
        "pending_buy_count": int((pending_action.eq("BUY") & pending_next).sum()) if not pending_orders.empty else 0,
        "pending_sell_exit_count": int((pending_action.eq("SELL") & pending_next).sum()) if not pending_orders.empty else 0,
        "paper_ledger_status": "OK",
        "total_open_notional": total_open_notional,
        "total_unrealized_pnl": total_unrealized,
        "total_unrealized_pnl_pct": total_unrealized_pct,
        "realized_pnl_total": realized_pnl,
        "best_open_position": best_symbol,
        "worst_open_position": worst_symbol,
    }
    checks = {
        "duplicate_open_symbol_count": int(len(duplicate_open_symbols)),
        "pending_buy_already_open_symbols": _join_symbols(pending_buy_symbols & open_symbols),
        "pending_sell_without_open_position_symbols": _join_symbols(pending_sell_symbols - open_symbols),
        "open_position_without_trade_log_issue": "none",
        "missing_mark_price_symbols": _join_symbols(missing_mark),
        "stale_mark_price_symbols": _join_symbols(stale_mark),
        "sell_exit_symbols_validated": _join_symbols(pending_sell_symbols & open_symbols),
        "short_intents_count": int((pending_action.isin(["SHORT", "SELL_SHORT"]) & pending_next).sum()) if not pending_orders.empty else 0,
        "short_trading_enabled": bool((safety_flags or {}).get("short_trading_enabled", False)),
        "broker_submit_enabled": bool((safety_flags or {}).get("broker_submit_enabled", False)),
        "live_trading_enabled": bool((safety_flags or {}).get("live_trading_enabled", False)),
        "submit_order_called": bool((safety_flags or {}).get("submit_order_called", False)),
        "post_request_used": bool((safety_flags or {}).get("post_request_used", False)),
    }
    frames = [
        frame
        for frame in [_summary_rows(summary), open_positions, pending, _risk_rows(checks)]
        if frame is not None and not frame.empty
    ]
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    for column in MONITOR_COLUMNS:
        if column not in combined:
            combined[column] = ""
    return combined[MONITOR_COLUMNS], summary, checks, open_positions, pending


def _markdown_table(frame):
    if frame is None or frame.empty:
        return "none"
    try:
        return frame.to_markdown(index=False)
    except ImportError:
        return frame.to_string(index=False)


def write_paper_portfolio_monitor(reports_dir, csv_path, md_path, asset_data=None, safety_flags=None):
    reports_dir = Path(reports_dir)
    trade_log = read_trade_log(reports_dir)
    pending_orders = read_pending_orders(reports_dir)
    order_review = read_order_review(reports_dir)
    monitor, summary, checks, open_positions, pending = build_paper_portfolio_monitor(
        trade_log,
        pending_orders,
        order_review,
        asset_data=asset_data,
        safety_flags=safety_flags,
    )
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    monitor.to_csv(csv_path, index=False)
    summary_frame = pd.DataFrame([{"metric": key, "value": value} for key, value in summary.items()])
    checks_frame = pd.DataFrame([{"check": key, "value": value} for key, value in checks.items()])
    md_lines = [
        "# Paper Portfolio Monitor",
        "",
        "## Summary",
        "",
        _markdown_table(summary_frame),
        "",
        "## Open Positions",
        "",
        _markdown_table(open_positions.drop(columns=["section"], errors="ignore")),
        "",
        "## Pending Orders",
        "",
        _markdown_table(pending.drop(columns=["section"], errors="ignore")),
        "",
        "## Risk / Consistency Checks",
        "",
        _markdown_table(checks_frame),
        "",
        "## Safety Flags",
        "",
        _markdown_table(checks_frame[checks_frame["check"].isin([
            "broker_submit_enabled",
            "live_trading_enabled",
            "short_trading_enabled",
            "submit_order_called",
            "post_request_used",
        ])]),
        "",
    ]
    Path(md_path).write_text("\n".join(md_lines), encoding="utf-8")
    return {
        "summary": summary,
        "checks": checks,
        "csv_path": str(csv_path),
        "md_path": str(md_path),
    }
