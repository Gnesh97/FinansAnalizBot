"""GET-only, bounded SIP entitlement canary (Phase 1.5, TASK B).

Sends a small, fixed probe set (<=12 requests) to Alpaca's historical bars
endpoint with feed=sip, for the 4 known gap sessions (reusing the same
symbol/date pairs as the immutable Phase-1 IEX forensics evidence in
testdata/reports/latest/rs_v2_major_milestone_report.md, for direct
comparability) plus one unaffected control date.

Read-only against the repository: writes only a new report file. Makes no
writes to hisseler/ or rs_v2_clean_market_data/. Never prints or logs the
API key/secret values themselves -- only request parameters and results.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "testdata"))

from vericekici_updated import get_credentials  # noqa: E402

from alpaca.data.enums import Adjustment, DataFeed  # noqa: E402
from alpaca.data.historical import StockHistoricalDataClient  # noqa: E402
from alpaca.data.requests import StockBarsRequest  # noqa: E402
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit  # noqa: E402
from alpaca.common.exceptions import APIError  # noqa: E402

OUT_PATH = REPO_ROOT / "reports" / "audit" / "sip_entitlement_canary_20260716.json"

# Same symbol/date pairs as the immutable Phase-1 IEX forensics matrix
# (rs_v2_major_milestone_report.md) for direct comparability.
GAP_PROBES = [
    ("ABBV", "2021-04-19"),
    ("ALB", "2021-10-25"),
    ("ABBV", "2022-03-08"),
    ("WBD", "2022-04-06"),
]
CONTROL_PROBE = ("SPY", "2023-06-01")  # ordinary trading day, no known issue


def _day_window(date_str: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = start.replace(hour=23, minute=59, second=59)
    return start, end


def _probe(client: StockHistoricalDataClient, symbol: str, date_str: str, timeframe, timeframe_label: str) -> dict:
    start, end = _day_window(date_str)
    record = {
        "symbol": symbol,
        "date": date_str,
        "timeframe": timeframe_label,
        "feed": "sip",
        "adjustment": "all",
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
    }
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        adjustment=Adjustment.ALL,
        feed=DataFeed.SIP,
    )
    try:
        response = client.get_stock_bars(request)
        frame = response.df
        row_count = 0 if frame is None or frame.empty else len(frame)
        record.update({
            "result": "SUCCESS",
            "http_status": 200,
            "error_code": None,
            "error_message": None,
            "row_count": row_count,
        })
    except APIError as exc:
        record.update({
            "result": "API_ERROR",
            "http_status": exc.status_code,
            "error_code": _safe(lambda: exc.code),
            "error_message": _safe(lambda: exc.message) or str(exc),
            "row_count": None,
        })
    except Exception as exc:  # noqa: BLE001 - canary must record any failure mode
        record.update({
            "result": "EXCEPTION",
            "http_status": None,
            "error_code": type(exc).__name__,
            "error_message": str(exc),
            "row_count": None,
        })
    return record


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def main() -> None:
    api_key, secret_key = get_credentials()  # never printed/logged
    client = StockHistoricalDataClient(api_key, secret_key)

    results = []
    request_count = 0
    for symbol, date_str in GAP_PROBES + [CONTROL_PROBE]:
        for timeframe, label in ((TimeFrame.Day, "1Day"), (TimeFrame(5, TimeFrameUnit.Minute), "5Min")):
            request_count += 1
            print(f"[{request_count}] GET feed=sip symbol={symbol} date={date_str} timeframe={label}")
            results.append(_probe(client, symbol, date_str, timeframe, label))

    assert request_count <= 12, f"request budget exceeded: {request_count}"

    payload = {
        "canary_run_utc": datetime.now(timezone.utc).isoformat(),
        "total_request_count": request_count,
        "gap_probe_symbol_date_pairs": GAP_PROBES,
        "control_probe_symbol_date_pair": CONTROL_PROBE,
        "results": results,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    for row in results:
        print(f"{row['symbol']} {row['date']} {row['timeframe']}: result={row['result']} "
              f"status={row['http_status']} code={row['error_code']} rows={row['row_count']}")
    print("total_request_count:", request_count)
    print("written:", OUT_PATH)


if __name__ == "__main__":
    main()
