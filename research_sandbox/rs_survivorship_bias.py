"""Survivorship-bias pilot: does adding historically-delisted names back into the
universe change the frozen production strategy's results?

Exploratory only -- see research_sandbox/README.md for scope and limitations. Reuses the
production selection logic (testdata.strategies.relative_strength_rotation) read-only,
with the exact frozen baseline params (rs_m126_top30_sma100_no_regime) unchanged. The
only thing that differs between the two runs below is the universe: production's 177
frozen symbols, vs. that same universe plus a pilot sample of delisted names sourced from
a third-party HF OHLCV-1m dataset (see reports/audit/phase21_hf_ohlcv1m_dataset_evaluation_20260802.md).

Known simplifications (consistent with this sandbox's existing non-audit-grade scope):
- Delisted-candidate daily bars are resampled from 1-minute data (open=first, high=max,
  low=min, close=last, volume=sum) and a per-ticker split-guard drops any candidate whose
  day-over-day close ratio ever falls outside [0.5, 2.0] anywhere in its history -- the HF
  source has no split/dividend-adjustment info, so a real split would otherwise show as a
  fake price cliff. This is coarser than true adjustment but avoids sourcing an external
  split calendar for a 150-name pilot.
- All frame indices (both the production Tiingo loader's and this script's resampled HF
  frames) are normalized to a timezone-naive calendar date before merging -- production's
  own convention keeps a UTC time-of-day component (NY midnight in UTC) that isn't needed
  for this daily-bar comparison and would otherwise silently break the date alignment.
- Same as the rest of this sandbox: same-day close execution, no transaction costs, and a
  forward-filled last price after a symbol disappears from the feed (i.e. delisting is NOT
  modeled as a hard wipeout to zero here) -- this likely UNDERSTATES true survivorship bias
  for outright-bankruptcy cases (e.g. FRC in the phase21 report), since a name that goes to
  zero is instead held flat at its last known price if still in the selected set.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import vectorbt as vbt

from testdata.data_engine import read_ohlcv_csv_fast
from testdata.strategies.relative_strength_rotation import _relative_strength_selection

DAILY_DIR = REPO_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data" / "daily"
HF_DATA_DIR = REPO_ROOT / "hisseler" / "87DATA" / "data"
PILOT_LIST_PATH = REPO_ROOT / "scratch" / "hf_full_scan" / "pilot_candidates_stratified_150.csv"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
EXCLUDED_SYMBOLS = {"QQQ", "SPY"}
PRODUCTION_WINDOW_START_MONTH = "2020-07"

BASE_PARAMS = {
    "rs_lookback_days": 126,
    "rs_top_n": 30,
    "rs_score_mode": "return_over_vol",
    "rs_vol_lookback_days": 60,
    "rs_trend_sma_days": 100,
    "rs_use_market_regime": False,
    "rs_market_regime_sma_days": 200,
    "rs_min_price": 10,
    "rs_max_atr_pct": 12,
    "rs_rebalance_frequency": "monthly",
}


def _normalize_index(frame: pd.DataFrame) -> pd.DataFrame:
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    frame = frame.copy()
    frame.index = idx.normalize()
    return frame


def load_production_universe() -> dict[str, pd.DataFrame]:
    asset_data = {}
    for path in sorted(DAILY_DIR.glob("*_1D.csv")):
        symbol = path.stem.replace("_1D", "")
        if symbol in EXCLUDED_SYMBOLS:
            continue
        frame = _normalize_index(read_ohlcv_csv_fast(path))
        frame.attrs["is_daily"] = True
        asset_data[symbol] = frame
    return asset_data


def _month_range(first_month: str, last_month: str) -> list[str]:
    y1, m1 = (int(x) for x in first_month.split("-"))
    y2, m2 = (int(x) for x in last_month.split("-"))
    months = []
    y, m = y1, m1
    while (y, m) <= (y2, m2):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def load_delisted_daily(ticker: str, first_month: str, last_month: str) -> pd.DataFrame | None:
    clipped_first = max(first_month, PRODUCTION_WINDOW_START_MONTH)
    months = _month_range(clipped_first, last_month)
    frames = []
    for month in months:
        path = HF_DATA_DIR / f"ohlcv_{month}.parquet"
        if not path.exists():
            continue
        try:
            t = pq.read_table(
                path,
                columns=["timestamp", "open", "high", "low", "close", "volume", "ticker"],
                filters=[("ticker", "=", ticker)],
            )
        except FileNotFoundError:
            continue
        df = t.to_pandas()
        if not df.empty:
            frames.append(df)
    if not frames:
        return None

    raw = pd.concat(frames, ignore_index=True).sort_values("timestamp")
    raw["date"] = raw["timestamp"].dt.floor("D")
    daily = raw.groupby("date").agg(
        Open=("open", "first"), High=("high", "max"), Low=("low", "min"),
        Close=("close", "last"), Volume=("volume", "sum"),
    )
    if len(daily) < 30:
        return None

    ratios = daily["Close"] / daily["Close"].shift(1)
    if ((ratios < 0.5) | (ratios > 2.0)).any():
        return None

    daily = _normalize_index(daily)
    daily.attrs["is_daily"] = True
    return daily


FULL_POPULATION_LIST_PATH = REPO_ROOT / "scratch" / "hf_full_scan" / "full_population_candidates.csv"


def load_delisted_universe_batched(candidate_list_path: Path) -> dict[str, pd.DataFrame]:
    """Same resample + split-guard contract as load_delisted_daily, but reads each
    monthly parquet file exactly once (filtered to the full candidate ticker list) instead
    of once per ticker -- makes a several-thousand-candidate population tractable."""
    candidates = pd.read_csv(candidate_list_path)
    ticker_bounds = {
        row["ticker"]: (max(row["first_valid_month"], PRODUCTION_WINDOW_START_MONTH), row["last_valid_month"])
        for _, row in candidates.iterrows()
    }
    all_tickers = list(ticker_bounds.keys())
    all_months = sorted({m for first, last in ticker_bounds.values() for m in _month_range(first, last)})
    print(f"full population: {len(all_tickers)} candidates, {len(all_months)} months to scan")

    monthly_daily_frames = []
    for i, month in enumerate(all_months, 1):
        path = HF_DATA_DIR / f"ohlcv_{month}.parquet"
        if not path.exists():
            continue
        t = pq.read_table(
            path,
            columns=["timestamp", "open", "high", "low", "close", "volume", "ticker"],
            filters=[("ticker", "in", all_tickers)],
        )
        df = t.to_pandas()
        if df.empty:
            continue
        df["date"] = df["timestamp"].dt.floor("D")
        daily = df.groupby(["ticker", "date"]).agg(
            Open=("open", "first"), High=("high", "max"), Low=("low", "min"),
            Close=("close", "last"), Volume=("volume", "sum"),
        ).reset_index()
        monthly_daily_frames.append(daily)
        print(f"  [{i}/{len(all_months)}] {month}: {daily['ticker'].nunique()} tickers, "
              f"{len(df)} raw rows -> {len(daily)} daily rows", flush=True)

    combined = pd.concat(monthly_daily_frames, ignore_index=True)
    asset_data = {}
    dropped = 0
    for ticker, group in combined.groupby("ticker"):
        daily = group.set_index("date")[["Open", "High", "Low", "Close", "Volume"]].sort_index()
        daily = daily[~daily.index.duplicated(keep="last")]
        if len(daily) < 30:
            dropped += 1
            continue
        ratios = daily["Close"] / daily["Close"].shift(1)
        if ((ratios < 0.5) | (ratios > 2.0)).any():
            dropped += 1
            continue
        daily = _normalize_index(daily)
        daily.attrs["is_daily"] = True
        asset_data[ticker] = daily

    print(f"full population: {len(asset_data)} loaded / {combined['ticker'].nunique()} had some data / "
          f"{len(all_tickers)} candidates total ({dropped} dropped: <30 days or split-guard)")
    return asset_data


def load_delisted_pilot() -> dict[str, pd.DataFrame]:
    pilot = pd.read_csv(PILOT_LIST_PATH)
    asset_data = {}
    dropped_no_data = 0
    for _, row in pilot.iterrows():
        ticker = row["ticker"]
        daily = load_delisted_daily(ticker, row["first_valid_month"], row["last_valid_month"])
        if daily is None:
            dropped_no_data += 1
            continue
        asset_data[ticker] = daily
    print(f"delisted pilot: {len(asset_data)} loaded / {len(pilot)} attempted "
          f"({dropped_no_data} dropped: no usable data or split-guard tripped)")
    return asset_data


WIPEOUT_DECLINE_RATIO = 0.1  # lost 90%+ of its own loaded-window peak before disappearing
WIPEOUT_PRICE = 0.01  # near-zero, not exactly 0.0 (avoids div/log issues in the simulator)


def compute_wipeout_symbols(asset_data: dict[str, pd.DataFrame]) -> set[str]:
    """Confirmed-bankruptcy-pattern names: lost WIPEOUT_DECLINE_RATIO+ of their own
    loaded-window peak close before their last observed date. Computed from the loaded
    window itself (not the full historical peak in the candidate CSV) so this lines up
    exactly with what this run actually simulated."""
    wipeouts = set()
    for symbol, frame in asset_data.items():
        peak = frame["Close"].max()
        last = frame["Close"].iloc[-1]
        if peak > 0 and (last / peak) <= WIPEOUT_DECLINE_RATIO:
            wipeouts.add(symbol)
    return wipeouts


def build_weight_and_price_frames(asset_data: dict[str, pd.DataFrame], selections: dict, all_dates: pd.DatetimeIndex, wipeout_symbols: frozenset[str] = frozenset()) -> tuple[pd.DataFrame, pd.DataFrame]:
    symbols = sorted(asset_data.keys())
    price = pd.DataFrame(index=all_dates, columns=symbols, dtype=float)
    last_real_date = {}
    for symbol, frame in asset_data.items():
        price[symbol] = frame["Close"].reindex(all_dates)
        last_real_date[symbol] = frame.index.max()
    price = price.ffill()

    for symbol in wipeout_symbols:
        if symbol in price.columns:
            price.loc[price.index > last_real_date[symbol], symbol] = WIPEOUT_PRICE

    weights = pd.DataFrame(np.nan, index=all_dates, columns=symbols, dtype=float)
    for date, selected in selections.items():
        row = pd.Series(0.0, index=symbols)
        if selected:
            row.loc[sorted(set(selected) & set(symbols))] = 1.0 / len(selected)
        weights.loc[date] = row
    weights = weights.ffill().fillna(0.0)
    return weights, price


def simulate(asset_data: dict[str, pd.DataFrame], label: str, delisted_symbols: set[str], wipeout_symbols: frozenset[str] = frozenset()) -> dict:
    _, selections, _, _ = _relative_strength_selection(asset_data, BASE_PARAMS, benchmark_data=None)
    full_dates = pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in asset_data.values()))))
    weights, price = build_weight_and_price_frames(asset_data, selections, full_dates, wipeout_symbols)

    portfolio = vbt.Portfolio.from_orders(
        close=price, size=weights, size_type="targetpercent", group_by=True,
        cash_sharing=True, call_seq="auto", fees=0.0, slippage=0.0,
        init_cash=100_000.0, freq="1D",
    )
    stats = portfolio.stats()

    delisted_selections = sum(
        1 for selected in selections.values() if delisted_symbols & set(selected)
    )

    return {
        "label": label,
        "n_symbols": len(asset_data),
        "net_return_pct": float(stats.get("Total Return [%]", np.nan)),
        "sharpe_ratio": float(stats.get("Sharpe Ratio", np.nan)),
        "max_drawdown_pct": float(stats.get("Max Drawdown [%]", np.nan)),
        "rebalance_count": len(selections),
        "rebalances_with_delisted_pick": delisted_selections,
        "n_wipeout_symbols": len(wipeout_symbols),
    }


def run() -> pd.DataFrame:
    print("Loading production (frozen, 177-symbol) universe...")
    production = load_production_universe()
    print(f"  {len(production)} symbols loaded.")

    print("Loading delisted pilot candidates (resampled 1m->daily, split-guarded)...")
    delisted = load_delisted_pilot()

    expanded = dict(production)
    expanded.update(delisted)

    print("\nRunning baseline (production universe only)...")
    baseline_result = simulate(production, "production_only", set())
    print(f"  {baseline_result}")

    print("\nRunning expanded (production + delisted pilot)...")
    expanded_result = simulate(expanded, "production_plus_delisted_pilot", set(delisted.keys()))
    print(f"  {expanded_result}")

    frame = pd.DataFrame([baseline_result, expanded_result])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "rs_survivorship_bias_stratified.csv"
    frame.to_csv(csv_path, index=False)
    print(f"\nWrote {csv_path}")
    return frame


def run_full() -> pd.DataFrame:
    print("Loading production (frozen, 177-symbol) universe...")
    production = load_production_universe()
    print(f"  {len(production)} symbols loaded.")

    print("Loading full delisted candidate population (resampled 1m->daily, split-guarded)...")
    delisted = load_delisted_universe_batched(FULL_POPULATION_LIST_PATH)

    expanded = dict(production)
    expanded.update(delisted)

    print("\nRunning baseline (production universe only)...")
    baseline_result = simulate(production, "production_only", set())
    print(f"  {baseline_result}")

    print("\nRunning expanded (production + full delisted population)...")
    expanded_result = simulate(expanded, "production_plus_full_delisted_population", set(delisted.keys()))
    print(f"  {expanded_result}")

    frame = pd.DataFrame([baseline_result, expanded_result])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "rs_survivorship_bias_full_population.csv"
    frame.to_csv(csv_path, index=False)
    print(f"\nWrote {csv_path}")
    return frame


def run_full_wipeout() -> pd.DataFrame:
    """Same as run_full(), except confirmed-bankruptcy-pattern names (lost 90%+ of their
    own loaded-window peak) are modeled as a hard wipeout to near-zero after their last
    observed date, instead of forward-filled at their last known price. This directly
    tests phase24's flagged conservative bias: forward-fill likely understates how bad
    the full-population result should be."""
    print("Loading production (frozen, 177-symbol) universe...")
    production = load_production_universe()
    print(f"  {len(production)} symbols loaded.")

    print("Loading full delisted candidate population (resampled 1m->daily, split-guarded)...")
    delisted = load_delisted_universe_batched(FULL_POPULATION_LIST_PATH)

    wipeouts = compute_wipeout_symbols(delisted)
    print(f"wipeout-flagged (lost >={1 - WIPEOUT_DECLINE_RATIO:.0%} of loaded-window peak): "
          f"{len(wipeouts)} / {len(delisted)} delisted candidates")

    expanded = dict(production)
    expanded.update(delisted)

    print("\nRunning baseline (production universe only)...")
    baseline_result = simulate(production, "production_only", set())
    print(f"  {baseline_result}")

    print("\nRunning expanded (production + full delisted population, wipeout-modeled)...")
    expanded_result = simulate(
        expanded, "production_plus_full_delisted_population_wipeout",
        set(delisted.keys()), frozenset(wipeouts),
    )
    print(f"  {expanded_result}")

    frame = pd.DataFrame([baseline_result, expanded_result])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "rs_survivorship_bias_full_population_wipeout.csv"
    frame.to_csv(csv_path, index=False)
    print(f"\nWrote {csv_path}")
    return frame


if __name__ == "__main__":
    if "--full-wipeout" in sys.argv:
        run_full_wipeout()
    elif "--full" in sys.argv:
        run_full()
    else:
        run()
