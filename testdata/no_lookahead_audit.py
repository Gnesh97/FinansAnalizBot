from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd


SIGNAL_AUDIT_COLUMNS = [
    "section",
    "strategy_name",
    "symbol",
    "action",
    "signal_date",
    "signal_bar_timestamp",
    "signal_bar_complete_expected",
    "latest_available_data_timestamp_for_signal",
    "lookahead_status",
    "reason",
]

FILL_AUDIT_COLUMNS = [
    "section",
    "strategy_name",
    "symbol",
    "action",
    "signal_date",
    "fill_timestamp",
    "fill_price_source",
    "fill_after_signal",
    "same_bar_fill_detected",
    "expected_next_open_rule_status",
    "reason",
]

BAR_AUDIT_COLUMNS = [
    "section",
    "symbol",
    "latest_raw_5min_timestamp",
    "inferred_130min_block_start",
    "current_block_complete",
    "incomplete_bar_excluded_expected",
    "status",
    "reason",
]

AUDIT_COLUMNS = SIGNAL_AUDIT_COLUMNS + [
    column for column in FILL_AUDIT_COLUMNS + BAR_AUDIT_COLUMNS if column not in SIGNAL_AUDIT_COLUMNS
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


def _boolish(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _timestamp(value):
    text = _clean_text(value)
    if not text:
        return pd.NaT
    try:
        return pd.to_datetime(text, errors="coerce", utc=True)
    except Exception:
        return pd.NaT


def _timestamp_text(value) -> str:
    ts = _timestamp(value)
    return "" if pd.isna(ts) else str(ts)


def _data_timestamp(data_dir: Path | None, symbol: str) -> str:
    if data_dir is None:
        return ""
    path = data_dir / "bars_130min" / f"{symbol.upper()}_130min.csv"
    frame = _read_csv(path)
    if frame.empty or "Date" not in frame:
        return ""
    dates = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()
    return str(dates.max()) if not dates.empty else ""


def _raw_5min_dates(data_dir: Path | None, symbol: str) -> pd.Series:
    if data_dir is None:
        return pd.Series(dtype="datetime64[ns, UTC]")
    path = data_dir / "raw_5min" / f"{symbol.upper()}_5min.csv"
    frame = _read_csv(path)
    if frame.empty or "Date" not in frame:
        return pd.Series(dtype="datetime64[ns, UTC]")
    return pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()


def _required_130min_last(bar_ts) -> object:
    if pd.isna(bar_ts):
        return pd.NaT
    local = pd.Timestamp(bar_ts)
    if local.tzinfo is None:
        local = local.tz_localize("America/New_York")
    else:
        local = local.tz_convert("America/New_York")
    minute = local.hour * 60 + local.minute
    if minute not in {9 * 60 + 30, 11 * 60 + 40, 13 * 60 + 50}:
        return pd.NaT
    return local + pd.Timedelta(minutes=125)


def is_130min_bar_complete(raw_5min_dates: pd.Series, bar_timestamp) -> bool:
    required = _required_130min_last(bar_timestamp)
    if pd.isna(required) or raw_5min_dates.empty:
        return False
    dates = raw_5min_dates
    if getattr(dates.dt, "tz", None) is None:
        dates = dates.dt.tz_localize("UTC")
    local_dates = dates.dt.tz_convert("America/New_York")
    return bool((local_dates >= required).any())


def _extract_note_value(notes: str, key: str) -> str:
    pattern = re.compile(rf"(?:^|;){re.escape(key)}=([^;]+)")
    match = pattern.search(_clean_text(notes))
    return match.group(1).strip() if match else ""


def _signal_rows(signal_report: pd.DataFrame, data_dir: Path | None) -> pd.DataFrame:
    rows = []
    if signal_report.empty:
        return pd.DataFrame(rows, columns=SIGNAL_AUDIT_COLUMNS)
    action = signal_report.get("action", pd.Series("", index=signal_report.index)).astype(str).str.upper()
    actionable = signal_report.get("paper_actionable", pd.Series(False, index=signal_report.index)).map(_boolish)
    subset = signal_report[action.isin(["BUY", "SELL"]) & actionable].copy()
    for _, row in subset.iterrows():
        symbol = _clean_text(row.get("symbol", "")).upper()
        signal_date = _clean_text(row.get("signal_date", ""))
        signal_ts = _timestamp(signal_date)
        latest_data = _data_timestamp(data_dir, symbol)
        latest_ts = _timestamp(latest_data)
        raw_dates = _raw_5min_dates(data_dir, symbol)
        complete = is_130min_bar_complete(raw_dates, signal_ts) if not raw_dates.empty else False
        fill_bar = _clean_text(row.get("fill_bar_timestamp", ""))
        fill_ts = _timestamp(fill_bar)
        if pd.isna(signal_ts) or pd.isna(latest_ts):
            status = "WARNING"
            reason = "missing_signal_or_data_timestamp"
        elif signal_ts > latest_ts:
            status = "FAIL"
            reason = "signal_timestamp_after_latest_available_data"
        elif fill_bar and pd.notna(fill_ts) and fill_ts <= signal_ts:
            status = "FAIL"
            reason = "signal_row_implies_same_bar_or_prior_fill"
        elif not complete:
            status = "WARNING"
            reason = "signal_bar_completeness_not_confirmed_from_raw_5min"
        else:
            status = "PASS"
            reason = "signal_timestamp_within_available_completed_data"
        rows.append(
            {
                "section": "signal_timing",
                "strategy_name": _clean_text(row.get("strategy_name", "")),
                "symbol": symbol,
                "action": _clean_text(row.get("action", "")).upper(),
                "signal_date": signal_date,
                "signal_bar_timestamp": _timestamp_text(signal_date),
                "signal_bar_complete_expected": bool(complete),
                "latest_available_data_timestamp_for_signal": latest_data,
                "lookahead_status": status,
                "reason": reason,
            }
        )
    return pd.DataFrame(rows, columns=SIGNAL_AUDIT_COLUMNS)


def _fill_rows(trade_log: pd.DataFrame, pending_orders: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if not trade_log.empty:
        status = trade_log.get("fill_status", pd.Series("", index=trade_log.index)).astype(str).str.upper()
        filled = trade_log[status.eq("PAPER_FILLED")].copy()
        for _, row in filled.iterrows():
            notes = _clean_text(row.get("notes", ""))
            fill_timestamp = _extract_note_value(notes, "fill_bar_timestamp") or _clean_text(row.get("fill_bar_timestamp", "")) or _clean_text(row.get("date", ""))
            source = _extract_note_value(notes, "fill_price_source") or _clean_text(row.get("fill_price_source", ""))
            signal_ts = _timestamp(row.get("signal_date", ""))
            fill_ts = _timestamp(fill_timestamp)
            fill_after = bool(pd.notna(signal_ts) and pd.notna(fill_ts) and fill_ts > signal_ts)
            same_bar = bool(pd.notna(signal_ts) and pd.notna(fill_ts) and fill_ts <= signal_ts)
            next_open_expected = source == "" or "next" in source.lower()
            if pd.isna(signal_ts) or pd.isna(fill_ts):
                rule_status = "WARNING"
                reason = "missing_signal_or_fill_timestamp"
            elif next_open_expected and same_bar:
                rule_status = "FAIL"
                reason = "next_open_fill_not_strictly_after_signal"
            elif next_open_expected and fill_after:
                rule_status = "PASS"
                reason = "next_open_fill_strictly_after_signal"
            else:
                rule_status = "WARNING"
                reason = "fill_source_not_confirmed_as_next_open"
            rows.append(
                {
                    "section": "fill_timing",
                    "strategy_name": _clean_text(row.get("strategy_name", "")),
                    "symbol": _clean_text(row.get("symbol", "")).upper(),
                    "action": _clean_text(row.get("action", "")).upper(),
                    "signal_date": _clean_text(row.get("signal_date", "")),
                    "fill_timestamp": _timestamp_text(fill_timestamp),
                    "fill_price_source": source,
                    "fill_after_signal": fill_after,
                    "same_bar_fill_detected": same_bar,
                    "expected_next_open_rule_status": rule_status,
                    "reason": reason,
                }
            )
    if not pending_orders.empty:
        status = pending_orders.get("status", pending_orders.get("fill_status", pd.Series("", index=pending_orders.index))).astype(str).str.upper()
        pending = pending_orders[status.isin(["PENDING", "PENDING_NEXT_OPEN", "WAIT_FOR_NEXT_OPEN"])].copy()
        for _, row in pending.iterrows():
            expected_next_open = _clean_text(row.get("expected_next_open_date", ""))
            reason = "waiting_for_next_open" if expected_next_open else "missing_next_open_pending_wait"
            rows.append(
                {
                    "section": "fill_timing_pending",
                    "strategy_name": _clean_text(row.get("strategy_name", "")),
                    "symbol": _clean_text(row.get("symbol", "")).upper(),
                    "action": _clean_text(row.get("action", "")).upper(),
                    "signal_date": _clean_text(row.get("signal_date", "")),
                    "fill_timestamp": "",
                    "fill_price_source": "next_130min_open",
                    "fill_after_signal": "",
                    "same_bar_fill_detected": False,
                    "expected_next_open_rule_status": "WARNING",
                    "reason": reason,
                }
            )
    return pd.DataFrame(rows, columns=FILL_AUDIT_COLUMNS)


def _block_start_for_raw_timestamp(timestamp) -> object:
    if pd.isna(timestamp):
        return pd.NaT
    local = pd.Timestamp(timestamp).tz_convert("America/New_York")
    date = local.date()
    starts = [
        pd.Timestamp.combine(date, pd.Timestamp("09:30").time()).tz_localize("America/New_York"),
        pd.Timestamp.combine(date, pd.Timestamp("11:40").time()).tz_localize("America/New_York"),
        pd.Timestamp.combine(date, pd.Timestamp("13:50").time()).tz_localize("America/New_York"),
    ]
    eligible = [start for start in starts if start <= local]
    return eligible[-1] if eligible else starts[0]


def _bar_rows(data_dir: Path | None, symbols: list[str]) -> pd.DataFrame:
    rows = []
    for symbol in symbols:
        raw_dates = _raw_5min_dates(data_dir, symbol)
        if raw_dates.empty:
            rows.append(
                {
                    "section": "130min_bar_completeness",
                    "symbol": symbol,
                    "latest_raw_5min_timestamp": "",
                    "inferred_130min_block_start": "",
                    "current_block_complete": False,
                    "incomplete_bar_excluded_expected": True,
                    "status": "WARNING",
                    "reason": "raw_5min_missing",
                }
            )
            continue
        latest = raw_dates.max()
        block_start = _block_start_for_raw_timestamp(latest)
        complete = is_130min_bar_complete(raw_dates, block_start)
        rows.append(
            {
                "section": "130min_bar_completeness",
                "symbol": symbol,
                "latest_raw_5min_timestamp": str(latest),
                "inferred_130min_block_start": str(block_start),
                "current_block_complete": bool(complete),
                "incomplete_bar_excluded_expected": bool(not complete),
                "status": "PASS",
                "reason": "current_incomplete_block_excluded" if not complete else "current_block_complete",
            }
        )
    return pd.DataFrame(rows, columns=BAR_AUDIT_COLUMNS)


def _symbols_from_inputs(signal_report: pd.DataFrame, pending_orders: pd.DataFrame, trade_log: pd.DataFrame) -> list[str]:
    symbols = []
    for frame in [signal_report, pending_orders, trade_log]:
        if not frame.empty and "symbol" in frame:
            symbols.extend(frame["symbol"].dropna().astype(str).str.upper().tolist())
    return sorted(set(symbol for symbol in symbols if symbol))


def _count_status(frame: pd.DataFrame, column: str, status: str) -> int:
    if frame.empty or column not in frame:
        return 0
    return int(frame[column].astype(str).str.upper().eq(status).sum())


def _summary(signal_audit: pd.DataFrame, fill_audit: pd.DataFrame, bar_audit: pd.DataFrame) -> dict[str, object]:
    signal_fail = _count_status(signal_audit, "lookahead_status", "FAIL")
    fill_fail = _count_status(fill_audit, "expected_next_open_rule_status", "FAIL")
    bar_fail = _count_status(bar_audit, "status", "FAIL")
    signal_warning = _count_status(signal_audit, "lookahead_status", "WARNING")
    fill_warning = _count_status(fill_audit, "expected_next_open_rule_status", "WARNING")
    audit_status = "FAIL" if signal_fail or fill_fail or bar_fail else ("WARNING" if signal_warning or fill_warning else "PASS")
    return {
        "actionable_signal_rows_checked": int(len(signal_audit)),
        "filled_trade_rows_checked": int(len(fill_audit[fill_audit["section"].eq("fill_timing")])) if not fill_audit.empty else 0,
        "pending_rows_checked": int(len(fill_audit[fill_audit["section"].eq("fill_timing_pending")])) if not fill_audit.empty else 0,
        "signal_timing_pass_count": _count_status(signal_audit, "lookahead_status", "PASS"),
        "signal_timing_warning_count": signal_warning,
        "signal_timing_fail_count": signal_fail,
        "fill_timing_pass_count": _count_status(fill_audit, "expected_next_open_rule_status", "PASS"),
        "fill_timing_warning_count": fill_warning,
        "fill_timing_fail_count": fill_fail,
        "incomplete_bar_checks_count": int(len(bar_audit)),
        "incomplete_bar_fail_count": bar_fail,
        "audit_status": audit_status,
        "state_mutated": False,
        "broker_submit_enabled": False,
        "live_trading_enabled": False,
        "short_trading_enabled": False,
        "submit_order_called": False,
        "post_request_used": False,
    }


def build_no_lookahead_audit(latest_dir: str | Path, data_dir: str | Path | None = None) -> dict[str, object]:
    latest = Path(latest_dir)
    data_path = Path(data_dir) if data_dir is not None else None
    signal_report = _read_csv(latest / "paper_signal_report.csv")
    pending_orders = _read_csv(latest / "paper_pending_orders.csv")
    trade_log = _read_csv(latest / "paper_trade_log.csv")
    signal_audit = _signal_rows(signal_report, data_path)
    fill_audit = _fill_rows(trade_log, pending_orders)
    bar_audit = _bar_rows(data_path, _symbols_from_inputs(signal_report, pending_orders, trade_log))
    return {
        "signal_timing": signal_audit,
        "fill_timing": fill_audit,
        "bar_completeness": bar_audit,
        "summary": _summary(signal_audit, fill_audit, bar_audit),
    }


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    return json.loads(frame.fillna("").to_json(orient="records"))


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "none"
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(_clean_text(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def _metric_table(values: dict[str, object]) -> str:
    return _markdown_table(pd.DataFrame([{"metric": key, "value": value} for key, value in values.items()]))


def write_no_lookahead_audit(latest_dir: str | Path, data_dir: str | Path | None = None) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    result = build_no_lookahead_audit(latest, data_dir)
    combined = pd.concat(
        [
            result["signal_timing"].reindex(columns=AUDIT_COLUMNS),
            result["fill_timing"].reindex(columns=AUDIT_COLUMNS),
            result["bar_completeness"].reindex(columns=AUDIT_COLUMNS),
        ],
        ignore_index=True,
    ).fillna("")
    csv_path = latest / "no_lookahead_audit.csv"
    md_path = latest / "no_lookahead_audit.md"
    json_path = latest / "no_lookahead_audit.json"
    combined.to_csv(csv_path, index=False)
    payload = {
        "summary": result["summary"],
        "signal_timing": _records(result["signal_timing"]),
        "fill_timing": _records(result["fill_timing"]),
        "bar_completeness": _records(result["bar_completeness"]),
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = [
        "# No-Lookahead Audit",
        "",
        "## Summary",
        "",
        _metric_table(result["summary"]),
        "",
        "## Signal Timing Audit",
        "",
        _markdown_table(result["signal_timing"]),
        "",
        "## Fill Timing Audit",
        "",
        _markdown_table(result["fill_timing"]),
        "",
        "## 130min Bar Completeness Audit",
        "",
        _markdown_table(result["bar_completeness"]),
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "summary": result["summary"],
        "csv_path": str(csv_path),
        "md_path": str(md_path),
        "json_path": str(json_path),
    }
