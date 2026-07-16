"""GET-only, bounded, rigorous SIP full-session completeness probe (Phase 1.6, TASK B).

Unlike the earlier entitlement canary (which used a raw UTC-calendar-day window
and no completeness check), this probe:
  - Uses the project's OWN NYSE-calendar RTH session window
    (testdata/v2_session_coverage.py::approved_nyse_schedule), imported and
    called unmodified.
  - Evaluates daily-bar completeness via
    testdata/v2_session_coverage.py::calculate_v2_session_coverage, imported
    and called unmodified (timeframe="1D").
  - Evaluates 5-minute raw completeness against the exact same 130-minute
    block structure testdata/v2_coverage_forensics.py uses internally
    (imported _expected_blocks, unmodified) -- 3 RTH blocks x 26 five-minute
    bars = 78 expected bars per full session.

Read-only against the repository: writes only a new report file. No write to
hisseler/ or rs_v2_clean_market_data/. Never prints/logs API key or secret
values -- only request parameters and results. Every underlying HTTP request
Alpaca's SDK makes is counted via a thin instrumentation wrapper and logged.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "testdata"))

from vericekici_updated import get_credentials  # noqa: E402
from v2_session_coverage import (  # noqa: E402
    approved_nyse_schedule,
    calculate_v2_session_coverage,
    MARKET_TIMEZONE,
)
from v2_coverage_forensics import _expected_blocks  # noqa: E402

from alpaca.data.enums import Adjustment, DataFeed  # noqa: E402
from alpaca.data.historical import StockHistoricalDataClient  # noqa: E402
from alpaca.data.requests import StockBarsRequest  # noqa: E402
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit  # noqa: E402

OUT_PATH = REPO_ROOT / "reports" / "audit" / "sip_full_session_probe_20260716.json"
MAX_HTTP_REQUESTS = 40

# Same 4 symbol/date pairs as the immutable Phase-1 IEX forensics evidence.
GAP_PROBES = [
    ("ABBV", "2021-04-19"),
    ("ALB", "2021-10-25"),
    ("ABBV", "2022-03-08"),
    ("WBD", "2022-04-06"),
]
# The two PROVIDER_WIDE_PATTERN dates that hit 100% of the universe on IEX,
# probed with SPY/QQQ-adjacent liquid names for a decisive read.
PROVIDER_WIDE_PROBES = [
    ("SPY", "2025-03-10"),
    ("AAPL", "2025-03-10"),
    ("SPY", "2024-12-23"),
    ("AAPL", "2024-12-23"),
]

_http_call_log: list[dict] = []


def _instrument_http_calls(client: StockHistoricalDataClient) -> None:
    """Wrap the low-level HTTP GET method so every underlying request (across
    SDK-internal pagination) is counted and logged without touching the SDK."""
    original_get = client.get

    def wrapped_get(*args, **kwargs):
        _http_call_log.append({
            "n": len(_http_call_log) + 1,
            "path": args[0] if args else kwargs.get("path"),
            "params": {k: v for k, v in (kwargs.get("data") or {}).items() if k not in {"limit"} or True},
        })
        return original_get(*args, **kwargs)

    client.get = wrapped_get


def _daily_bars(client, symbol: str, date_str: str) -> pd.DataFrame:
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = start.replace(hour=23, minute=59, second=59)
    request = StockBarsRequest(
        symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start, end=end,
        adjustment=Adjustment.ALL, feed=DataFeed.SIP,
    )
    response = client.get_stock_bars(request)
    frame = response.df
    return frame.reset_index() if frame is not None and not frame.empty else pd.DataFrame(columns=["timestamp"])


def _rth_5min_bars(client, symbol: str, date_str: str, session_open: pd.Timestamp, session_close: pd.Timestamp) -> pd.DataFrame:
    request = StockBarsRequest(
        symbol_or_symbols=symbol, timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=session_open.tz_convert("UTC").to_pydatetime(),
        end=session_close.tz_convert("UTC").to_pydatetime(),
        adjustment=Adjustment.ALL, feed=DataFeed.SIP,
    )
    response = client.get_stock_bars(request)
    frame = response.df
    return frame.reset_index() if frame is not None and not frame.empty else pd.DataFrame(columns=["timestamp"])


def _daily_completeness(symbol: str, date_str: str, daily_frame: pd.DataFrame, schedule: pd.DataFrame) -> dict:
    if "timestamp" in daily_frame.columns:
        working = daily_frame.rename(columns={"timestamp": "Date"})[["Date"]]
    else:
        working = pd.DataFrame({"Date": []})
    return calculate_v2_session_coverage(
        working, timeframe="1D", symbol=symbol, target_start=date_str, target_end=date_str,
        market_schedule=schedule,
    )


def _intraday_completeness(date_str: str, bars_5min: pd.DataFrame, schedule: pd.DataFrame) -> dict:
    session = pd.Timestamp(date_str)
    blocks = _expected_blocks(schedule).get(session, [])
    if "timestamp" in bars_5min.columns:
        actual_timestamps = pd.DatetimeIndex(pd.to_datetime(bars_5min["timestamp"], utc=True)).sort_values()
    else:
        actual_timestamps = pd.DatetimeIndex([])

    expected_total = 0
    missing_total = 0
    per_block = []
    for block_start, block_end in blocks:
        expected_count = int((block_end - block_start) / pd.Timedelta(minutes=5))
        in_block = actual_timestamps[(actual_timestamps >= block_start) & (actual_timestamps < block_end)]
        actual_count = len(in_block)
        expected_total += expected_count
        missing_total += max(0, expected_count - actual_count)
        per_block.append({
            "block_start_utc": str(block_start), "block_end_utc": str(block_end),
            "expected_bar_count": expected_count, "actual_bar_count": actual_count,
        })

    largest_gap_minutes = 0.0
    if len(actual_timestamps) >= 2:
        diffs = actual_timestamps.to_series().diff().dropna()
        if len(diffs):
            largest_gap_minutes = diffs.max().total_seconds() / 60.0
    elif len(actual_timestamps) == 0 and expected_total > 0:
        largest_gap_minutes = float("inf")

    return {
        "expected_bar_count": expected_total,
        "actual_bar_count": len(actual_timestamps),
        "missing_bar_count": missing_total,
        "first_bar_timestamp": str(actual_timestamps.min()) if len(actual_timestamps) else "",
        "last_bar_timestamp": str(actual_timestamps.max()) if len(actual_timestamps) else "",
        "largest_intra_session_gap_minutes": largest_gap_minutes,
        "per_block": per_block,
        "complete": bool(expected_total > 0 and missing_total == 0),
    }


def probe_gap_date(client, symbol: str, date_str: str) -> dict:
    schedule = approved_nyse_schedule(date_str, date_str)
    session = pd.Timestamp(date_str)
    session_open, session_close = pd.Timestamp(schedule.loc[session, "market_open"]), pd.Timestamp(schedule.loc[session, "market_close"])

    daily_frame = _daily_bars(client, symbol, date_str)
    daily_result = _daily_completeness(symbol, date_str, daily_frame, schedule)

    bars_5min = _rth_5min_bars(client, symbol, date_str, session_open, session_close)
    intraday_result = _intraday_completeness(date_str, bars_5min, schedule)

    return {
        "symbol": symbol, "date": date_str, "feed": "sip",
        "rth_open_utc": str(session_open.tz_convert("UTC")), "rth_close_utc": str(session_close.tz_convert("UTC")),
        "daily_bar_count": len(daily_frame), "daily_coverage_status": daily_result["coverage_status"],
        "daily_coverage_reason": daily_result["coverage_exception_reason"],
        "intraday": intraday_result,
        "intraday_pass": intraday_result["complete"],
    }


def probe_intraday_only(client, symbol: str, date_str: str) -> dict:
    schedule = approved_nyse_schedule(date_str, date_str)
    session = pd.Timestamp(date_str)
    session_open, session_close = pd.Timestamp(schedule.loc[session, "market_open"]), pd.Timestamp(schedule.loc[session, "market_close"])
    bars_5min = _rth_5min_bars(client, symbol, date_str, session_open, session_close)
    intraday_result = _intraday_completeness(date_str, bars_5min, schedule)
    return {
        "symbol": symbol, "date": date_str, "feed": "sip",
        "rth_open_utc": str(session_open.tz_convert("UTC")), "rth_close_utc": str(session_close.tz_convert("UTC")),
        "intraday": intraday_result,
        "intraday_pass": intraday_result["complete"],
    }


def main() -> None:
    api_key, secret_key = get_credentials()  # never printed/logged
    client = StockHistoricalDataClient(api_key, secret_key)
    _instrument_http_calls(client)

    gap_results = []
    for symbol, date_str in GAP_PROBES:
        print(f"probing gap date: {symbol} {date_str} (1Day + 5Min RTH)")
        gap_results.append(probe_gap_date(client, symbol, date_str))

    provider_wide_results = []
    for symbol, date_str in PROVIDER_WIDE_PROBES:
        print(f"probing provider-wide date: {symbol} {date_str} (5Min RTH only)")
        provider_wide_results.append(probe_intraday_only(client, symbol, date_str))

    assert len(_http_call_log) <= MAX_HTTP_REQUESTS, f"HTTP request budget exceeded: {len(_http_call_log)}"

    payload = {
        "probe_run_utc": datetime.now(timezone.utc).isoformat(),
        "total_http_request_count": len(_http_call_log),
        "http_call_log": _http_call_log,
        "gap_date_results": gap_results,
        "provider_wide_date_results": provider_wide_results,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    print("\n--- GAP DATE RESULTS ---")
    for row in gap_results:
        intr = row["intraday"]
        print(f"{row['symbol']} {row['date']}: daily={row['daily_coverage_status']} "
              f"intraday={intr['actual_bar_count']}/{intr['expected_bar_count']} "
              f"missing={intr['missing_bar_count']} gap_max={intr['largest_intra_session_gap_minutes']:.1f}min "
              f"PASS={row['intraday_pass']}")
    print("\n--- PROVIDER-WIDE DATE RESULTS ---")
    for row in provider_wide_results:
        intr = row["intraday"]
        print(f"{row['symbol']} {row['date']}: intraday={intr['actual_bar_count']}/{intr['expected_bar_count']} "
              f"missing={intr['missing_bar_count']} gap_max={intr['largest_intra_session_gap_minutes']:.1f}min "
              f"PASS={row['intraday_pass']}")
    print("\ntotal_http_request_count:", len(_http_call_log))
    print("written:", OUT_PATH)


if __name__ == "__main__":
    main()
