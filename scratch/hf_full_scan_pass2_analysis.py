"""Pass 2: analyze the per-ticker-month summary from pass1 to characterize
dataset health -- delisted candidates, internal coverage gaps, and
gap-boundary price jumps (possible ticker-reuse candidates).

Read-only against scratch/hf_full_scan/per_ticker_month_summary.parquet.
Writes CSV reports to scratch/hf_full_scan/. Not committed, not production.
"""
import pandas as pd

IN_PATH = "scratch/hf_full_scan/per_ticker_month_summary.parquet"
OUT_DIR = "scratch/hf_full_scan"

df = pd.read_parquet(IN_PATH)
print(f"loaded rows={len(df)} unique_tickers={df['ticker'].nunique()}", flush=True)

parts = df["month"].str.split("-", expand=True).astype(int)
df["month_ord"] = parts[0] * 12 + parts[1]
df = df.sort_values(["ticker", "month_ord"]).reset_index(drop=True)

DATASET_MAX_ORD = df["month_ord"].max()
DATASET_MIN_ORD = df["month_ord"].min()
print(f"dataset month range ord: {DATASET_MIN_ORD} -> {DATASET_MAX_ORD}", flush=True)

# ---- Delisted candidates: last-seen month is meaningfully before dataset end ----
per_ticker_last = df.groupby("ticker")["month_ord"].max().rename("last_month_ord")
per_ticker_first = df.groupby("ticker")["month_ord"].min().rename("first_month_ord")
per_ticker_nmonths = df.groupby("ticker")["month_ord"].nunique().rename("n_months_present")
last_close = df.sort_values(["ticker", "month_ord"]).groupby("ticker")["last_close"].last().rename("final_close")

STOP_BUFFER = 2  # months of slack at the very end (still-active names near dataset edge)
ticker_stats = pd.concat([per_ticker_first, per_ticker_last, per_ticker_nmonths, last_close], axis=1).reset_index()
ticker_stats["months_since_stop"] = DATASET_MAX_ORD - ticker_stats["last_month_ord"]
delisted = ticker_stats[ticker_stats["months_since_stop"] > STOP_BUFFER].copy()
delisted = delisted.sort_values("last_month_ord", ascending=False)
delisted.to_csv(f"{OUT_DIR}/delisted_candidates.csv", index=False)
print(f"delisted candidates (last_month_ord < max-{STOP_BUFFER}): {len(delisted)} / {len(ticker_stats)}", flush=True)

# ---- Internal gaps: consecutive present-months per ticker, find skips ----
df["prev_month_ord"] = df.groupby("ticker")["month_ord"].shift(1)
df["prev_last_close"] = df.groupby("ticker")["last_close"].shift(1)
df["gap_months"] = df["month_ord"] - df["prev_month_ord"] - 1

gaps = df[df["gap_months"] > 0].copy()
gaps["ratio"] = df["first_close"] / df["prev_last_close"]
gaps = gaps.rename(columns={"month": "resume_month", "first_close": "close_after", "prev_last_close": "close_before"})
gaps["gap_months"] = gaps["gap_months"].astype(int)

FLAG_RATIO_HIGH = 3.0
FLAG_RATIO_LOW = 1.0 / 3.0
gaps["price_flag"] = (gaps["ratio"] > FLAG_RATIO_HIGH) | (gaps["ratio"] < FLAG_RATIO_LOW)

gap_cols = ["ticker", "resume_month", "gap_months", "close_before", "close_after", "ratio", "price_flag"]
gaps_out = gaps[gap_cols].sort_values(["price_flag", "gap_months"], ascending=[False, False])
gaps_out.to_csv(f"{OUT_DIR}/gap_events.csv", index=False)
print(f"internal gap events: {len(gaps_out)} across {gaps_out['ticker'].nunique()} tickers", flush=True)
print(f"gap events with price_flag (ratio>{FLAG_RATIO_HIGH} or <{FLAG_RATIO_LOW:.2f}): {gaps_out['price_flag'].sum()}", flush=True)

# ---- distribution summaries for a quick health read ----
print("\ngap_months distribution (all gap events):", flush=True)
print(gaps_out["gap_months"].describe(), flush=True)

flagged = gaps_out[gaps_out["price_flag"]]
print(f"\nflagged events by gap length bucket:", flush=True)
bucket = pd.cut(flagged["gap_months"], bins=[0, 1, 3, 6, 12, 24, 10_000], labels=["1", "2-3", "4-6", "7-12", "13-24", "25+"])
print(bucket.value_counts().sort_index(), flush=True)

print("\nTop 40 flagged gap events by |log ratio| (most extreme first):", flush=True)
import numpy as np
flagged = flagged.copy()
flagged["abs_log_ratio"] = flagged["ratio"].apply(lambda r: abs(np.log(r)) if r > 0 else float("inf"))
top = flagged.sort_values("abs_log_ratio", ascending=False).head(40)
print(top[gap_cols].to_string(index=False), flush=True)

print("\nDONE.", flush=True)
