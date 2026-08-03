"""Stricter trend-strength filter: does requiring SMA50 > SMA100 (trend actually
accelerating, not just "above its 100-day average") on top of the existing eligibility
filter improve results on the survivorship-bias-corrected universe?

Exploratory only -- see research_sandbox/README.md for scope and limitations. Reuses the
production feature computation (_relative_strength_daily_features) read-only for
eligibility, score, and the existing SMA100 trend filter -- everything else (the extra
SMA50 condition, the selection loop, the simulation) is sandbox-only, does not exist in
testdata/. Runs on the survivorship-bias-corrected universe (production + full delisted
population from phase24/rs_survivorship_bias.py), not production-only, since that's the
more realistic picture established earlier this session (see reports/audit/phase24-26).

Motivation (phase25/26): names that are about to collapse can still show a brief
short-term momentum spike right before the fall, fooling the existing filters into
selecting them. SMA50 > SMA100 requires the trend to be genuinely accelerating (not just
"above average"), which should reject more of those spike-then-collapse cases.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
import vectorbt as vbt

from testdata.strategies.relative_strength_rotation import (
    _relative_strength_daily_features,
    _rs_rebalance_mask,
)
from rs_rotation_sensitivity import load_expanded_universe

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

PARAMS = {
    "rs_lookback_days": 126,
    "rs_top_n": 30,
    "rs_score_mode": "return_over_vol",
    "rs_vol_lookback_days": 60,
    "rs_trend_sma_days": 100,
    "rs_min_price": 10,
    "rs_max_atr_pct": 12,
}
REBALANCE_FREQUENCY = "monthly"
FAST_SMA_DAYS = 50


def select_by_trend_strength(asset_data: dict[str, pd.DataFrame], features: dict[str, pd.DataFrame], rebalance_dates: pd.DatetimeIndex, top_n: int, require_accelerating_trend: bool) -> dict:
    fast_sma = {
        symbol: frame["Close"].rolling(FAST_SMA_DAYS, min_periods=FAST_SMA_DAYS).mean()
        for symbol, frame in asset_data.items()
    }
    selections = {}
    for date in rebalance_dates:
        rows = []
        for symbol, frame in features.items():
            if date not in frame.index:
                continue
            row = frame.loc[date]
            if not (bool(row.get("RS_Eligible", False)) and pd.notna(row.get("RS_Score"))):
                continue
            if require_accelerating_trend:
                sma50 = fast_sma[symbol].get(date, np.nan)
                sma100 = row.get("RS_Trend_SMA", np.nan)
                if pd.isna(sma50) or pd.isna(sma100) or sma50 <= sma100:
                    continue
            rows.append((symbol, float(row["RS_Score"])))
        if not rows:
            selections[date] = set()
            continue
        frame = pd.DataFrame(rows, columns=["symbol", "score"]).set_index("symbol")
        ranked = frame.sort_values("score", ascending=False)
        selections[date] = set(ranked.head(top_n).index)
    return selections


def build_weight_and_price_frames(asset_data: dict[str, pd.DataFrame], selections: dict, all_dates: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    symbols = sorted(asset_data.keys())
    price = pd.DataFrame(index=all_dates, columns=symbols, dtype=float)
    for symbol, frame in asset_data.items():
        price[symbol] = frame["Close"].reindex(all_dates)
    price = price.ffill()

    weights = pd.DataFrame(np.nan, index=all_dates, columns=symbols, dtype=float)
    for date, selected in selections.items():
        row = pd.Series(0.0, index=symbols)
        if selected:
            row.loc[sorted(selected)] = 1.0 / len(selected)
        weights.loc[date] = row
    weights = weights.ffill().fillna(0.0)
    return weights, price


def simulate(asset_data: dict[str, pd.DataFrame], selections: dict, full_dates: pd.DatetimeIndex) -> dict:
    weights, price = build_weight_and_price_frames(asset_data, selections, full_dates)
    portfolio = vbt.Portfolio.from_orders(
        close=price, size=weights, size_type="targetpercent", group_by=True,
        cash_sharing=True, call_seq="auto", fees=0.0, slippage=0.0,
        init_cash=100_000.0, freq="1D",
    )
    stats = portfolio.stats()
    return {
        "net_return_pct": float(stats.get("Total Return [%]", np.nan)),
        "sharpe_ratio": float(stats.get("Sharpe Ratio", np.nan)),
        "max_drawdown_pct": float(stats.get("Max Drawdown [%]", np.nan)),
    }


def run_comparison() -> pd.DataFrame:
    print("Loading EXPANDED universe (production + full delisted population) ...")
    asset_data = load_expanded_universe()
    print(f"Loaded {len(asset_data)} symbols.")

    features = _relative_strength_daily_features(asset_data, PARAMS)
    full_dates = pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in asset_data.values()))))
    rebalance_mask = _rs_rebalance_mask(full_dates, REBALANCE_FREQUENCY)
    rebalance_dates = full_dates[rebalance_mask]

    rows = []
    for label, require_accelerating_trend in [("baseline (existing SMA100 filter)", False), ("plus_sma50_gt_sma100", True)]:
        started = time.time()
        selections = select_by_trend_strength(asset_data, features, rebalance_dates, PARAMS["rs_top_n"], require_accelerating_trend)
        result = simulate(asset_data, selections, full_dates)
        elapsed = time.time() - started
        result["variant"] = label
        result["rebalance_count"] = len(selections)
        result["seconds"] = round(elapsed, 2)
        rows.append(result)
        print(f"  {label:<32} return={result['net_return_pct']:>8.2f}% "
              f"sharpe={result['sharpe_ratio']:>6.2f} maxdd={result['max_drawdown_pct']:>7.2f}%  ({elapsed:.1f}s)")

    frame = pd.DataFrame(rows)[["variant", "net_return_pct", "sharpe_ratio", "max_drawdown_pct", "rebalance_count", "seconds"]]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "rs_trend_strength_filter.csv"
    frame.to_csv(csv_path, index=False)
    print(f"\nWrote {csv_path}")
    return frame


if __name__ == "__main__":
    run_comparison()
