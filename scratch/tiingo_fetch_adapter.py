"""Tiingo Daily Data Evaluation Adapter (Sandbox Only).

READ-ONLY evaluation tool to test Tiingo EOD consolidated daily data as a potential free source.
NOT wired into production pipeline. No DSS repointing. No broker calls.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from testdata.vericekici_updated import load_v2_frozen_universe


def get_tiingo_api_key() -> str:
    env_path = Path(".env")
    if not env_path.exists():
        raise RuntimeError("TIINGO_API_KEY_ERROR: .env file missing!")
    key = None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("TIINGO_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("\"'")
    if not key or len(key) < 10:
        raise RuntimeError("TIINGO_API_KEY_ERROR: TIINGO_API_KEY missing or invalid in .env!")
    return key


def fetch_tiingo_daily_symbol(symbol: str, api_key: str, start_date: str = "2020-07-27") -> pd.DataFrame:
    ticker = symbol.lower()
    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate={start_date}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Token {api_key}",
    }
    req = urllib.request.Request(url, headers=headers)
    
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    
    if not payload:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    
    records = []
    for item in payload:
        # Critical split/dividend safety: map adjusted fields to standard OHLCV
        dt_str = item["date"].split("T")[0] + " 04:00:00+00:00"
        records.append({
            "Date": dt_str,
            "Open": float(item["adjOpen"]),
            "High": float(item["adjHigh"]),
            "Low": float(item["adjLow"]),
            "Close": float(item["adjClose"]),
            "Volume": float(item["adjVolume"]),
        })
    
    df = pd.DataFrame(records)
    df["Date"] = pd.to_datetime(df["Date"], utc=True)
    df = df.sort_values("Date").drop_duplicates(subset=["Date"]).reset_index(drop=True)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d %H:%M:%S+00:00")
    return df


def run_tiingo_acquisition(output_dir: Path) -> dict:
    api_key = get_tiingo_api_key()
    candidates, _ = load_v2_frozen_universe()
    symbols = sorted(set(candidates) | {"QQQ", "SPY"})
    
    output_dir.mkdir(parents=True, exist_ok=True)
    daily_dir = output_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    
    results = []
    print(f"Starting Tiingo EOD fetch for {len(symbols)} symbols...")
    start_time = time.time()
    
    for idx, sym in enumerate(symbols, 1):
        try:
            df = fetch_tiingo_daily_symbol(sym, api_key)
            out_path = daily_dir / f"{sym}_1D.csv"
            df.to_csv(out_path, index=False)
            
            latest_date = df["Date"].iloc[-1].split()[0] if not df.empty else "NONE"
            row_cnt = len(df)
            results.append({"symbol": sym, "status": "SUCCESS", "rows": row_cnt, "latest_date": latest_date, "error": ""})
            print(f"[{idx}/{len(symbols)}] {sym}: {row_cnt} rows, latest {latest_date}")
            time.sleep(0.15)  # Gentle rate control
        except Exception as exc:
            results.append({"symbol": sym, "status": "FAILED", "rows": 0, "latest_date": "NONE", "error": str(exc)})
            print(f"[{idx}/{len(symbols)}] {sym}: FAILED ({exc})")
    
    elapsed = time.time() - start_time
    summary = {
        "total_symbols": len(symbols),
        "success_count": sum(1 for r in results if r["status"] == "SUCCESS"),
        "failed_count": sum(1 for r in results if r["status"] == "FAILED"),
        "elapsed_seconds": round(elapsed, 2),
        "results": results,
    }
    return summary


if __name__ == "__main__":
    out_path = Path("testdata/research_data/rs_v2_tiingo_eval_data")
    summary = run_tiingo_acquisition(out_path)
    print("\nSUMMARY:")
    print(f"Success: {summary['success_count']} / {summary['total_symbols']} in {summary['elapsed_seconds']}s")
