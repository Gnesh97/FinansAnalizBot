"""Pass 1 (parallel): scan every monthly HF OHLCV-1m parquet file and build a
compact per-ticker-per-month summary (n_days, first/last date, first/last close).

Read-only against hisseler/87DATA/data/ (local HF dataset mirror). Writes one
small part-file per month to scratch/hf_full_scan/parts/, then concatenates.
Not committed, not production data. Worker count capped below core count to
stay inside available RAM (heavy 2020s months run ~1.5-2GB per worker).
"""
import glob
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd
import pyarrow.parquet as pq

DATA_DIR = "hisseler/87DATA/data"
OUT_DIR = "scratch/hf_full_scan"
PARTS_DIR = os.path.join(OUT_DIR, "parts")
WORKERS = 3


def process_month(fp: str):
    month = os.path.basename(fp).replace("ohlcv_", "").replace(".parquet", "")
    part_path = os.path.join(PARTS_DIR, f"{month}.parquet")
    if os.path.exists(part_path):
        return month, "SKIPPED_EXISTS", 0, 0
    try:
        t = pq.read_table(fp, columns=["timestamp", "ticker", "close"])
    except Exception as exc:
        return month, f"READ_FAILED: {exc}", 0, 0
    df = t.to_pandas()
    if df.empty:
        return month, "EMPTY", 0, 0
    n_rows = len(df)
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
    g.to_parquet(part_path)
    return month, "OK", len(g), n_rows


def main():
    os.makedirs(PARTS_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(DATA_DIR, "ohlcv_*.parquet")))
    print(f"files found: {len(files)}, workers={WORKERS}", flush=True)

    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(process_month, fp): fp for fp in files}
        for fut in as_completed(futures):
            month, status, n_tickers, n_rows = fut.result()
            done += 1
            elapsed = time.time() - t0
            print(f"[{done}/{len(files)}] {month}: {status} tickers={n_tickers} rows={n_rows} elapsed={elapsed:.0f}s", flush=True)

    parts = sorted(glob.glob(os.path.join(PARTS_DIR, "*.parquet")))
    frames = [pd.read_parquet(p) for p in parts]
    full = pd.concat(frames, ignore_index=True)
    out_path = os.path.join(OUT_DIR, "per_ticker_month_summary.parquet")
    full.to_parquet(out_path)
    print(f"DONE. wrote {out_path} rows={len(full)} unique_tickers={full['ticker'].nunique()} total_elapsed={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
