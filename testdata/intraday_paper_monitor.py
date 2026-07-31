from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

try:
    from .data_engine import MARKET_TIMEZONE, PRIMARY_BAR_MINUTES, RTH_CLOSE, RTH_OPEN
    from .paper_state_manager import read_order_review, read_pending_orders, read_trade_log
except ImportError:
    from data_engine import MARKET_TIMEZONE, PRIMARY_BAR_MINUTES, RTH_CLOSE, RTH_OPEN
    from paper_state_manager import read_order_review, read_pending_orders, read_trade_log


MONITOR_COLUMNS = [
    "section",
    "metric",
    "symbol",
    "strategy_name",
    "action",
    "signal_date",
    "expected_next_open_date",
    "shares",
    "fill_price",
    "fill_timestamp",
    "latest_5min_price",
    "latest_5min_timestamp",
    "intraday_unrealized_pnl",
    "intraday_unrealized_pnl_pct",
    "mark_status",
    "raw_5min_latest_timestamp",
    "has_target_session_5min_rows",
    "would_fill_if_5min_execution_enabled",
    "hypothetical_5min_fill_timestamp",
    "hypothetical_5min_fill_price",
    "latest_raw_5min_timestamp",
    "inferred_session_date",
    "current_130min_block_start",
    "inferred_block_start",
    "expected_block_last_5min_timestamp",
    "current_130min_bar_complete",
    "current_block_complete",
    "provisional_130min_open",
    "provisional_130min_high",
    "provisional_130min_low",
    "provisional_130min_close",
    "provisional_130min_volume",
    "warning",
    "value",
]

SAFETY_FLAGS = {
    "state_mutated": False,
    "broker_submit_enabled": False,
    "live_trading_enabled": False,
    "short_trading_enabled": False,
    "submit_order_called": False,
    "post_request_used": False,
}

ACCEPTED_PENDING_STATUSES = {"PENDING", "PENDING_NEXT_OPEN", "WAIT_FOR_NEXT_OPEN"}


def _clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _number(value):
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


def _session_open_delta() -> pd.Timedelta:
    parsed = pd.Timestamp(RTH_OPEN)
    return pd.Timedelta(hours=parsed.hour, minutes=parsed.minute)


def _load_raw_5min(raw_5min_dir: Path, symbol: str) -> pd.DataFrame:
    path = raw_5min_dir / f"{str(symbol).upper()}_5min.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    if frame.empty:
        return pd.DataFrame()
    date_col = "Date" if "Date" in frame.columns else "Datetime" if "Datetime" in frame.columns else None
    if date_col is None:
        return pd.DataFrame()
    frame = frame.copy()
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
    frame = frame.dropna(subset=[date_col]).sort_values(date_col)
    if frame.empty:
        return pd.DataFrame()
    frame.index = pd.DatetimeIndex(frame[date_col])
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    return frame


def _rth_rows(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    local_index = raw.index.tz_convert(MARKET_TIMEZONE) if raw.index.tz is not None else raw.index.tz_localize(MARKET_TIMEZONE)
    frame = raw.copy()
    frame.index = local_index
    return frame.between_time(RTH_OPEN, RTH_CLOSE, inclusive="left").sort_index()


def _latest_close(rth: pd.DataFrame):
    if rth.empty or "Close" not in rth.columns:
        return pd.NA, ""
    close = pd.to_numeric(rth["Close"], errors="coerce").dropna()
    if close.empty:
        return pd.NA, ""
    timestamp = close.index[-1]
    return float(close.iloc[-1]), str(timestamp)


def _infer_interval_minutes(rth: pd.DataFrame) -> int:
    if rth is None or len(rth.index) < 2:
        return 5
    deltas = rth.index.to_series().diff().dropna()
    if deltas.empty:
        return 5
    minutes = int(round(deltas.median().total_seconds() / 60.0))
    return minutes if minutes > 0 else 5


def _block_info(rth: pd.DataFrame) -> dict[str, object]:
    empty = {
        "latest_raw_5min_timestamp": "",
        "inferred_session_date": "",
        "inferred_block_start": "",
        "expected_block_last_5min_timestamp": "",
        "current_block_complete": False,
        "provisional_130min_open": pd.NA,
        "provisional_130min_high": pd.NA,
        "provisional_130min_low": pd.NA,
        "provisional_130min_close": pd.NA,
        "provisional_130min_volume": pd.NA,
    }
    if rth.empty:
        return empty
    latest_ts = rth.index[-1]
    session_open = latest_ts.normalize() + _session_open_delta()
    minutes_from_open = max(int((latest_ts - session_open).total_seconds() // 60), 0)
    block_start = session_open + pd.Timedelta(minutes=(minutes_from_open // PRIMARY_BAR_MINUTES) * PRIMARY_BAR_MINUTES)
    interval_minutes = _infer_interval_minutes(rth)
    expected_last = block_start + pd.Timedelta(minutes=PRIMARY_BAR_MINUTES - interval_minutes)
    block = rth[(rth.index >= block_start) & (rth.index <= expected_last)]
    if block.empty:
        return empty
    return {
        "latest_raw_5min_timestamp": str(latest_ts),
        "inferred_session_date": str(latest_ts.date()),
        "inferred_block_start": str(block_start),
        "expected_block_last_5min_timestamp": str(expected_last),
        "current_block_complete": bool(latest_ts >= expected_last),
        "provisional_130min_open": _number(block["Open"].iloc[0]) if "Open" in block else pd.NA,
        "provisional_130min_high": pd.to_numeric(block.get("High", pd.Series(dtype=float)), errors="coerce").max(),
        "provisional_130min_low": pd.to_numeric(block.get("Low", pd.Series(dtype=float)), errors="coerce").min(),
        "provisional_130min_close": _number(block["Close"].iloc[-1]) if "Close" in block else pd.NA,
        "provisional_130min_volume": pd.to_numeric(block.get("Volume", pd.Series(dtype=float)), errors="coerce").sum(min_count=1),
    }


def _symbol_universe(trade_log: pd.DataFrame, pending: pd.DataFrame, review: pd.DataFrame) -> list[str]:
    symbols = []
    for frame in [trade_log, pending, review]:
        if frame is not None and not frame.empty and "symbol" in frame:
            symbols.extend(frame["symbol"].dropna().astype(str).str.upper().str.strip().tolist())
    return sorted(symbol for symbol in set(symbols) if symbol)


def _raw_context(data_folder: str | Path, symbols: list[str]) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], pd.Timestamp]:
    raw_dir = Path(data_folder) / "raw_5min"
    raw = {symbol: _load_raw_5min(raw_dir, symbol) for symbol in symbols}
    rth = {symbol: _rth_rows(frame) for symbol, frame in raw.items()}
    latest_dates = [
        frame.index[-1].date()
        for frame in rth.values()
        if frame is not None and not frame.empty
    ]
    max_session = max(latest_dates) if latest_dates else None
    return raw, rth, max_session


def _open_positions(
    trade_log: pd.DataFrame,
    raw_by_symbol: dict[str, pd.DataFrame],
    rth_by_symbol: dict[str, pd.DataFrame],
    max_session,
) -> pd.DataFrame:
    if trade_log is None or trade_log.empty:
        return pd.DataFrame()
    filled = trade_log[
        trade_log.get("fill_status", pd.Series("", index=trade_log.index)).astype(str).str.upper().eq("PAPER_FILLED")
    ].copy()
    if filled.empty:
        return pd.DataFrame()
    filled["symbol"] = filled.get("symbol", pd.Series("", index=filled.index)).astype(str).str.upper().str.strip()
    filled["strategy_name"] = filled.get("strategy_name", pd.Series("", index=filled.index)).astype(str)
    action = filled.get("action", pd.Series("", index=filled.index)).astype(str).str.upper()
    shares = pd.to_numeric(filled.get("shares", pd.Series(0.0, index=filled.index)), errors="coerce").fillna(0.0)
    filled["signed_shares"] = shares.where(action.eq("BUY"), -shares.where(action.eq("SELL"), 0.0))
    rows = []
    for (strategy, symbol), group in filled.groupby(["strategy_name", "symbol"], dropna=False):
        net_shares = float(group["signed_shares"].sum())
        if net_shares <= 0:
            continue
        buys = group[group["signed_shares"] > 0].copy()
        if buys.empty:
            continue
        buy_shares = pd.to_numeric(buys["signed_shares"], errors="coerce").fillna(0.0)
        fill_prices = pd.to_numeric(buys.get("assumed_fill_price", pd.Series(pd.NA, index=buys.index)), errors="coerce")
        total_buy_shares = float(buy_shares.sum())
        fill_price = (fill_prices * buy_shares).sum() / total_buy_shares if total_buy_shares else pd.NA
        rth = rth_by_symbol.get(symbol, pd.DataFrame())
        latest_price, latest_ts = _latest_close(rth)
        block = _block_info(rth)
        raw = raw_by_symbol.get(symbol, pd.DataFrame())
        if raw.empty:
            raw_status = "MISSING_RAW_5MIN"
        elif rth.empty or "Close" not in rth.columns:
            raw_status = "NO_RTH_ROWS"
        elif max_session is not None and rth.index[-1].date() < max_session:
            raw_status = "STALE_RAW_5MIN"
        else:
            raw_status = "OK"
        pnl = pd.NA
        pnl_pct = pd.NA
        if pd.notna(latest_price) and pd.notna(fill_price):
            pnl = (float(latest_price) - float(fill_price)) * net_shares
            if float(fill_price) != 0:
                pnl_pct = (float(latest_price) / float(fill_price) - 1.0) * 100.0
        rows.append(
            {
                "section": "open_position",
                "symbol": symbol,
                "strategy_name": strategy,
                "shares": net_shares,
                "fill_price": fill_price,
                "fill_timestamp": _clean_text(buys.iloc[0].get("date", "")),
                "latest_5min_price": latest_price,
                "latest_5min_timestamp": latest_ts,
                "intraday_unrealized_pnl": pnl,
                "intraday_unrealized_pnl_pct": pnl_pct,
                "mark_status": raw_status,
                "current_130min_block_start": block["inferred_block_start"],
                "current_130min_bar_complete": block["current_block_complete"],
                "provisional_130min_open": block["provisional_130min_open"],
                "provisional_130min_high": block["provisional_130min_high"],
                "provisional_130min_low": block["provisional_130min_low"],
                "provisional_130min_close": block["provisional_130min_close"],
                "provisional_130min_volume": block["provisional_130min_volume"],
                "warning": "" if raw_status == "OK" else raw_status,
            }
        )
    return pd.DataFrame(rows)


def _accepted_pending(pending: pd.DataFrame, rth_by_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if pending is None or pending.empty:
        return pd.DataFrame()
    status = pending.get("status", pd.Series("", index=pending.index)).astype(str).str.upper()
    fill_status = pending.get("fill_status", pd.Series("", index=pending.index)).astype(str).str.upper()
    decision = pending.get("user_decision", pd.Series("", index=pending.index)).astype(str).str.upper()
    pending_status = status.isin(ACCEPTED_PENDING_STATUSES) | fill_status.isin(ACCEPTED_PENDING_STATUSES)
    accepted = pending[decision.eq("ACCEPT") & pending_status].copy()
    rows = []
    for _, row in accepted.iterrows():
        symbol = str(row.get("symbol", "")).upper().strip()
        target = pd.to_datetime(row.get("expected_next_open_date", ""), errors="coerce")
        rth = rth_by_symbol.get(symbol, pd.DataFrame())
        target_rows = pd.DataFrame()
        if pd.notna(target) and not rth.empty:
            target_rows = rth[rth.index.date == target.date()]
        has_target = not target_rows.empty
        fill_ts = str(target_rows.index[0]) if has_target else ""
        fill_price = _number(target_rows.iloc[0].get("Open", pd.NA)) if has_target else pd.NA
        raw_latest = str(rth.index[-1]) if not rth.empty else ""
        warning = "" if has_target else "MISSING_TARGET_SESSION_5MIN_ROWS"
        rows.append(
            {
                "section": "pending_5min_readiness",
                "symbol": symbol,
                "action": row.get("action", ""),
                "strategy_name": row.get("strategy_name", ""),
                "signal_date": row.get("signal_date", ""),
                "expected_next_open_date": row.get("expected_next_open_date", ""),
                "raw_5min_latest_timestamp": raw_latest,
                "has_target_session_5min_rows": bool(has_target),
                "would_fill_if_5min_execution_enabled": bool(has_target),
                "hypothetical_5min_fill_timestamp": fill_ts,
                "hypothetical_5min_fill_price": fill_price,
                "warning": warning,
            }
        )
    return pd.DataFrame(rows)


def _bar_rows(symbols: list[str], rth_by_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for symbol in symbols:
        block = _block_info(rth_by_symbol.get(symbol, pd.DataFrame()))
        rows.append(
            {
                "section": "current_130min_bar",
                "symbol": symbol,
                "latest_raw_5min_timestamp": block["latest_raw_5min_timestamp"],
                "inferred_session_date": block["inferred_session_date"],
                "inferred_block_start": block["inferred_block_start"],
                "expected_block_last_5min_timestamp": block["expected_block_last_5min_timestamp"],
                "current_block_complete": block["current_block_complete"],
                "provisional_130min_open": block["provisional_130min_open"],
                "provisional_130min_high": block["provisional_130min_high"],
                "provisional_130min_low": block["provisional_130min_low"],
                "provisional_130min_close": block["provisional_130min_close"],
                "provisional_130min_volume": block["provisional_130min_volume"],
                "warning": "" if block["latest_raw_5min_timestamp"] else "MISSING_RAW_5MIN",
            }
        )
    return pd.DataFrame(rows)


def _summary(open_rows: pd.DataFrame, pending_rows: pd.DataFrame, bar_rows: pd.DataFrame) -> dict[str, object]:
    pnl = pd.to_numeric(open_rows.get("intraday_unrealized_pnl", pd.Series(dtype=float)), errors="coerce")
    notional = (
        pd.to_numeric(open_rows.get("fill_price", pd.Series(dtype=float)), errors="coerce")
        * pd.to_numeric(open_rows.get("shares", pd.Series(dtype=float)), errors="coerce")
    )
    total_pnl = pnl.sum(min_count=1)
    total_notional = notional.sum(min_count=1)
    total_pct = pd.NA
    if pd.notna(total_pnl) and pd.notna(total_notional) and float(total_notional) != 0:
        total_pct = float(total_pnl) / float(total_notional) * 100.0
    best = "none"
    worst = "none"
    if not pnl.dropna().empty:
        best = str(open_rows.loc[pnl.idxmax(), "symbol"])
        worst = str(open_rows.loc[pnl.idxmin(), "symbol"])
    mark_status = open_rows.get("mark_status", pd.Series(dtype=str)).astype(str)
    current_complete = bar_rows.get("current_block_complete", pd.Series(dtype=bool)).astype(bool)
    return {
        "open_positions_count": int(len(open_rows)),
        "positions_with_intraday_mark": int(mark_status.eq("OK").sum()),
        "missing_raw_5min_count": int(mark_status.eq("MISSING_RAW_5MIN").sum()),
        "stale_raw_5min_count": int(mark_status.eq("STALE_RAW_5MIN").sum()),
        "accepted_pending_orders_count": int(len(pending_rows)),
        "pending_orders_with_5min_open_available": int(pending_rows.get("has_target_session_5min_rows", pd.Series(dtype=bool)).astype(bool).sum()),
        "current_130min_complete_count": int(current_complete.sum()),
        "current_130min_incomplete_count": int((~current_complete).sum()) if len(current_complete) else 0,
        "total_intraday_unrealized_pnl": total_pnl,
        "total_intraday_unrealized_pnl_pct": total_pct,
        "best_intraday_position": best,
        "worst_intraday_position": worst,
        **SAFETY_FLAGS,
    }


def build_intraday_paper_monitor(data_folder: str | Path, latest_dir: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    trade_log = read_trade_log(latest)
    pending = read_pending_orders(latest)
    review = read_order_review(latest)
    symbols = _symbol_universe(trade_log, pending, review)
    raw_by_symbol, rth_by_symbol, max_session = _raw_context(data_folder, symbols)
    open_rows = _open_positions(trade_log, raw_by_symbol, rth_by_symbol, max_session)
    pending_rows = _accepted_pending(pending, rth_by_symbol)
    monitored_symbols = sorted(
        set(open_rows.get("symbol", pd.Series(dtype=str)).astype(str))
        | set(pending_rows.get("symbol", pd.Series(dtype=str)).astype(str))
    )
    bars = _bar_rows(monitored_symbols, rth_by_symbol)
    summary = _summary(open_rows, pending_rows, bars)
    frames = [
        pd.DataFrame([{"section": "summary", "metric": key, "value": value} for key, value in summary.items()]),
        open_rows,
        pending_rows,
        bars,
    ]
    combined = pd.concat([frame for frame in frames if frame is not None and not frame.empty], ignore_index=True, sort=False)
    for column in MONITOR_COLUMNS:
        if column not in combined:
            combined[column] = ""
    return {
        "summary": summary,
        "open_positions": open_rows,
        "pending_readiness": pending_rows,
        "current_130min_bars": bars,
        "monitor": combined[MONITOR_COLUMNS],
    }


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "none"
    try:
        return frame.to_markdown(index=False)
    except ImportError:
        return frame.to_string(index=False)


def _json_default(value):
    if pd.isna(value):
        return None
    return str(value)


def write_intraday_paper_monitor(data_folder: str | Path, latest_dir: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    result = build_intraday_paper_monitor(data_folder, latest)
    csv_path = latest / "intraday_paper_monitor.csv"
    md_path = latest / "intraday_paper_monitor.md"
    json_path = latest / "intraday_paper_monitor.json"
    result["monitor"].to_csv(csv_path, index=False)
    json_path.write_text(
        json.dumps(
            {
                "summary": result["summary"],
                "open_positions": result["open_positions"].to_dict(orient="records"),
                "pending_readiness": result["pending_readiness"].to_dict(orient="records"),
                "current_130min_bars": result["current_130min_bars"].to_dict(orient="records"),
            },
            indent=2,
            sort_keys=True,
            default=_json_default,
        ),
        encoding="utf-8",
    )
    warnings = result["monitor"][result["monitor"].get("warning", pd.Series(dtype=str)).astype(str).str.len() > 0]
    safety = pd.DataFrame([{"flag": key, "value": value} for key, value in SAFETY_FLAGS.items()])
    summary_frame = pd.DataFrame([{"metric": key, "value": value} for key, value in result["summary"].items()])
    md_lines = [
        "# Intraday Paper Monitor",
        "",
        "## Summary",
        "",
        _markdown_table(summary_frame),
        "",
        "## Open Positions Intraday PnL",
        "",
        _markdown_table(result["open_positions"]),
        "",
        "## Pending 5min Fill Readiness",
        "",
        _markdown_table(result["pending_readiness"]),
        "",
        "## Current / Provisional 130min Bars",
        "",
        _markdown_table(result["current_130min_bars"]),
        "",
        "## Warnings",
        "",
        _markdown_table(warnings[["section", "symbol", "warning"]].drop_duplicates() if not warnings.empty else warnings),
        "",
        "## Safety Footer",
        "",
        _markdown_table(safety),
        "",
    ]
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return {
        "summary": result["summary"],
        "csv_path": str(csv_path),
        "md_path": str(md_path),
        "json_path": str(json_path),
    }
