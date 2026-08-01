"""Tiingo Daily Data Evaluation Adapter (Sandbox - Resumable & Paced).

READ-ONLY evaluation tool to fetch the complete 179 symbol universe via Tiingo free tier.
Paced to respect free tier's ~50 requests/hour limit (~75s delay per call or hourly wait on 429).
NOT wired into production pipeline. No DSS repointing. No broker calls.
"""

from __future__ import annotations

import json
import time
import urllib.error
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


def run_resumable_tiingo_acquisition(
    output_dir: Path,
    pace_delay_sec: float = 2.0,
    max_429_waits: int = 5,
) -> dict:
    api_key = get_tiingo_api_key()
    candidates, _ = load_v2_frozen_universe()
    symbols = sorted(set(candidates) | {"QQQ", "SPY"})
    
    output_dir.mkdir(parents=True, exist_ok=True)
    daily_dir = output_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    progress_file = output_dir / "tiingo_fetch_progress.json"
    
    progress = {}
    if progress_file.exists():
        try:
            progress = json.loads(progress_file.read_text(encoding="utf-8"))
        except Exception:
            progress = {}
            
    total_429s = 0
    start_time = time.time()
    results = []
    
    print(f"Starting/Resuming Tiingo EOD fetch for {len(symbols)} symbols...")
    print(f"Pacing: {pace_delay_sec}s delay between calls. Resumable state file: {progress_file}")
    
    for idx, sym in enumerate(symbols, 1):
        out_csv = daily_dir / f"{sym}_1D.csv"
        
        # RESUMABILITY CHECK: If valid file exists (>100 bytes), skip fetch!
        if out_csv.exists() and out_csv.stat().st_size > 100:
            try:
                existing = pd.read_csv(out_csv)
                if not existing.empty:
                    latest = existing["Date"].iloc[-1].split()[0]
                    row_cnt = len(existing)
                    results.append({"symbol": sym, "status": "SKIPPED_EXISTING", "rows": row_cnt, "latest_date": latest, "error": ""})
                    print(f"[{idx}/{len(symbols)}] {sym}: ALREADY EXISTS on disk ({row_cnt} rows, latest {latest})")
                    continue
            except Exception:
                pass
                
        # Fetch symbol with gentle backoff on 429
        attempt_429_count = 0
        success = False
        while not success and attempt_429_count <= max_429_waits:
            try:
                df = fetch_tiingo_daily_symbol(sym, api_key)
                df.to_csv(out_csv, index=False)
                
                latest_date = df["Date"].iloc[-1].split()[0] if not df.empty else "NONE"
                row_cnt = len(df)
                results.append({"symbol": sym, "status": "SUCCESS", "rows": row_cnt, "latest_date": latest_date, "error": ""})
                progress[sym] = {"status": "SUCCESS", "rows": row_cnt, "latest_date": latest_date}
                print(f"[{idx}/{len(symbols)}] {sym}: FETCHED ({row_cnt} rows, latest {latest_date})")
                success = True
                time.sleep(pace_delay_sec)
            except urllib.error.HTTPError as err:
                if err.code == 429:
                    total_429s += 1
                    attempt_429_count += 1
                    wait_time = 300 * attempt_429_count  # 5m, 10m, 15m backoff for hourly reset
                    print(f"[{idx}/{len(symbols)}] {sym}: HTTP 429 Hourly Limit (attempt {attempt_429_count}/{max_429_waits}). Waiting {wait_time}s for quota reset...")
                    time.sleep(wait_time)
                else:
                    results.append({"symbol": sym, "status": "FAILED", "rows": 0, "latest_date": "NONE", "error": str(err)})
                    progress[sym] = {"status": "FAILED", "error": str(err)}
                    print(f"[{idx}/{len(symbols)}] {sym}: FAILED ({err})")
                    break
            except Exception as exc:
                results.append({"symbol": sym, "status": "FAILED", "rows": 0, "latest_date": "NONE", "error": str(exc)})
                progress[sym] = {"status": "FAILED", "error": str(exc)}
                print(f"[{idx}/{len(symbols)}] {sym}: FAILED ({exc})")
                break
                
        # Save progress after each symbol
        progress_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")
        
    elapsed = time.time() - start_time
    
    # Calculate final status across all daily CSVs
    final_files = list(daily_dir.glob("*_1D.csv"))
    valid_files = [p for p in final_files if p.stat().st_size > 100]
    
    summary = {
        "total_symbols": len(symbols),
        "valid_on_disk_count": len(valid_files),
        "total_429s_encountered": total_429s,
        "elapsed_seconds": round(elapsed, 2),
    }
    print(f"\nFETCH COMPLETE: {len(valid_files)} / {len(symbols)} valid files on disk. Total 429s: {total_429s}. Elapsed: {round(elapsed, 2)}s")
    return summary


if __name__ == "__main__":
    out_path = Path("testdata/research_data/rs_v2_tiingo_eval_data")
    summary = run_resumable_tiingo_acquisition(out_path, pace_delay_sec=2.0)
