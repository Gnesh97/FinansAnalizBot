"""Pass 3: filter pass1's per-ticker-month summary down to a "real" delisted
candidate list -- i.e. tickers that were genuinely priced/traded like a real
security at some point and then genuinely stopped, with vendor artifacts
(sentinel floor/ceiling placeholder prices, exchange test symbols, isolated
one-print noise) excluded.

Read-only against scratch/hf_full_scan/per_ticker_month_summary.parquet.
Writes scratch/hf_full_scan/real_delisted_candidates.csv. Not committed,
not production data -- exploratory research_sandbox input only.
"""
import re

import pandas as pd

IN_PATH = "scratch/hf_full_scan/per_ticker_month_summary.parquet"
OUT_PATH = "scratch/hf_full_scan/real_delisted_candidates.csv"

FLOOR = 0.0011
CEILING = 199999.0
STOP_BUFFER = 2
MIN_PEAK_CLOSE = 1.0
MIN_VALID_MONTHS = 3

TEST_TICKER_RE = re.compile(
    r"TEST|^ZVZZT$|^ZWZZT$|^ZXZZT$|^ZBZX$|^NTEST", re.IGNORECASE
)

df = pd.read_parquet(IN_PATH)
print(f"loaded rows={len(df)} unique_tickers={df['ticker'].nunique()}", flush=True)

parts = df["month"].str.split("-", expand=True).astype(int)
df["month_ord"] = parts[0] * 12 + parts[1]
DATASET_MAX_ORD = df["month_ord"].max()

# a "valid" print is a month row whose first/last close both sit inside the
# plausible price band -- excludes the two sentinel artifacts found in pass2
df["valid"] = (
    (df["first_close"] > FLOOR) & (df["first_close"] < CEILING)
    & (df["last_close"] > FLOOR) & (df["last_close"] < CEILING)
)
valid = df[df["valid"]].copy()
print(f"rows after sentinel-price filter: {len(valid)} / {len(df)} ({df['ticker'].nunique() - valid['ticker'].nunique()} tickers lost entirely)", flush=True)

# drop official exchange test symbols
is_test = valid["ticker"].str.contains(TEST_TICKER_RE, regex=True, na=False)
print(f"test-symbol rows dropped: {is_test.sum()} (tickers: {sorted(valid.loc[is_test, 'ticker'].unique())[:20]}...)", flush=True)
valid = valid[~is_test]

valid = valid.sort_values(["ticker", "month_ord"])
stats = valid.groupby("ticker").agg(
    n_valid_months=("month_ord", "nunique"),
    first_valid_month_ord=("month_ord", "min"),
    last_valid_month_ord=("month_ord", "max"),
    peak_close=("last_close", "max"),
).reset_index()
peak_first = valid.groupby("ticker")["first_close"].max()
stats["peak_close"] = stats.apply(
    lambda r: max(r["peak_close"], peak_first.get(r["ticker"], 0)), axis=1
)
stats["months_since_stop"] = DATASET_MAX_ORD - stats["last_valid_month_ord"]

last_valid_close = valid.sort_values(["ticker", "month_ord"]).groupby("ticker")["last_close"].last()
stats["last_valid_close"] = stats["ticker"].map(last_valid_close)

delisted = stats[stats["months_since_stop"] > STOP_BUFFER].copy()
print(f"delisted candidates after sentinel/test filter: {len(delisted)} / {len(stats)} tickers", flush=True)

real = delisted[
    (delisted["peak_close"] >= MIN_PEAK_CLOSE)
    & (delisted["n_valid_months"] >= MIN_VALID_MONTHS)
].copy()
print(f"real candidates after peak_close>={MIN_PEAK_CLOSE} and n_valid_months>={MIN_VALID_MONTHS}: {len(real)}", flush=True)


def month_str(ord_val):
    y, m = divmod(int(ord_val) - 1, 12)
    return f"{y:04d}-{m + 1:02d}"


real["first_valid_month"] = real["first_valid_month_ord"].apply(month_str)
real["last_valid_month"] = real["last_valid_month_ord"].apply(month_str)
real = real[[
    "ticker", "first_valid_month", "last_valid_month", "n_valid_months",
    "peak_close", "last_valid_close", "months_since_stop",
]].sort_values("last_valid_month", ascending=False)

real.to_csv(OUT_PATH, index=False)
print(f"wrote {OUT_PATH} rows={len(real)}", flush=True)
print("\nsample (most recently stopped 20):", flush=True)
print(real.head(20).to_string(index=False), flush=True)
print("\nDONE.", flush=True)
