"""Pass 1: scan every monthly HF OHLCV-1m parquet file and build a compact
per-ticker-per-month summary (n_days, first/last date, first/last close).

Read-only against hisseler/87DATA/data/ (local HF dataset mirror). Writes
only to scratch/hf_full_scan/ (not committed, not production data).
"""
import glob
import os
import sys
import time

import pandas as pd
import pyarrow.parquet as pq

DATA_DIR = "hisseler/87DATA/data"
OUT_DIR = "scratch/hf_full_scan"
os.makedirs(OUT_DIR, exist_ok=True)

files = sorted(glob.glob(os.path.join(DATA_DIR, "ohlcv_*.parquet")))
print(f"files found: {len(files)}", flush=True)

summary_parts = []
t0 = time.time()
for i, fp in enumerate(files, 1):
    month = os.path.basename(fp).replace("ohlcv_", "").replace(".parquet", "")
    try:
        t = pq.read_table(fp, columns=["timestamp", "ticker", "close"])
    except Exception as exc:
        print(f"[{i}/{len(files)}] {month}: READ FAILED: {exc}", flush=True)
        continue
    df = t.to_pandas()
    if df.empty:
        print(f"[{i}/{len(files)}] {month}: EMPTY", flush=True)
        continue
    df["date"] = df["timestamp"].dt.date
    df = df.sort_values("timestamp")
    daily = df.groupby(["ticker", "date"], as_index=False).agg(close=("close", "last"))
    g = daily.groupby("ticker").agg(
        n_days=("date", "nunique"),
        first_date=("date", "min"),
        last_date=("date", "max"),
        first_close=("close", "first"),
        last_close=("close", "last"),
    ).reset_index()
    g["month"] = month
    summary_parts.append(g)
    elapsed = time.time() - t0
    print(f"[{i}/{len(files)}] {month}: tickers={len(g)} rows={len(df)} elapsed={elapsed:.0f}s", flush=True)

full = pd.concat(summary_parts, ignore_index=True)
out_path = os.path.join(OUT_DIR, "per_ticker_month_summary.parquet")
full.to_parquet(out_path)
print(f"DONE. wrote {out_path} rows={len(full)} unique_tickers={full['ticker'].nunique()} total_elapsed={time.time()-t0:.0f}s", flush=True)
