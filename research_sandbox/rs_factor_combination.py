"""Momentum-only vs momentum+low-volatility composite score, for the RS rotation strategy.

Exploratory only -- see research_sandbox/README.md for scope and limitations. Reuses the
production feature computation (_relative_strength_daily_features) read-only for eligibility,
ATR%, and the momentum (return_over_vol) score -- everything else (the composite ranking, the
selection loop, the simulation) is sandbox-only and does not exist in testdata/.

Question: does penalizing high-ATR% (volatility) names on top of the existing momentum score
improve risk-adjusted results, holding lookback (126d), rebalance (monthly), Top-N (30), and
every other production parameter fixed? Composite score = z(RS_Score) - z(ATR_Pct) among
eligible names at each rebalance date (z-scores computed cross-sectionally, per date).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd
import vectorbt as vbt

from testdata.data_engine import read_ohlcv_csv_fast
from testdata.strategies.relative_strength_rotation import (
    _relative_strength_daily_features,
    _rs_rebalance_mask,
)

DAILY_DIR = REPO_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data" / "daily"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
EXCLUDED_SYMBOLS = {"QQQ", "SPY"}

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


def load_universe() -> dict[str, pd.DataFrame]:
    asset_data = {}
    for path in sorted(DAILY_DIR.glob("*_1D.csv")):
        symbol = path.stem.replace("_1D", "")
        if symbol in EXCLUDED_SYMBOLS:
            continue
        frame = read_ohlcv_csv_fast(path)
        frame.attrs["is_daily"] = True
        asset_data[symbol] = frame
    return asset_data


def _zscore(series: pd.Series) -> pd.Series:
    std = series.std()
    if not std or pd.isna(std):
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std


def select_by_score(features: dict[str, pd.DataFrame], rebalance_dates: pd.DatetimeIndex, top_n: int, composite: bool) -> dict:
    selections = {}
    for date in rebalance_dates:
        rows = []
        for symbol, frame in features.items():
            if date not in frame.index:
                continue
            row = frame.loc[date]
            if bool(row.get("RS_Eligible", False)) and pd.notna(row.get("RS_Score")):
                rows.append((symbol, float(row["RS_Score"]), float(row.get("ATR_Pct", np.nan))))
        if not rows:
            selections[date] = set()
            continue
        frame = pd.DataFrame(rows, columns=["symbol", "score", "atr_pct"]).set_index("symbol")
        if composite:
            frame["rank_score"] = _zscore(frame["score"]) - _zscore(frame["atr_pct"])
        else:
            frame["rank_score"] = frame["score"]
        ranked = frame.sort_values("rank_score", ascending=False)
        selections[date] = set(ranked.head(top_n).index)
    return selections


def build_weight_and_price_frames(asset_data, selections, all_dates) -> tuple[pd.DataFrame, pd.DataFrame]:
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


def simulate(asset_data, selections, full_dates) -> dict:
    weights, price = build_weight_and_price_frames(asset_data, selections, full_dates)
    portfolio = vbt.Portfolio.from_orders(
        close=price,
        size=weights,
        size_type="targetpercent",
        group_by=True,
        cash_sharing=True,
        call_seq="auto",
        fees=0.0,
        slippage=0.0,
        init_cash=100_000.0,
        freq="1D",
    )
    stats = portfolio.stats()
    return {
        "net_return_pct": float(stats.get("Total Return [%]", np.nan)),
        "sharpe_ratio": float(stats.get("Sharpe Ratio", np.nan)),
        "max_drawdown_pct": float(stats.get("Max Drawdown [%]", np.nan)),
    }


def run_comparison() -> pd.DataFrame:
    print(f"Loading universe from {DAILY_DIR} ...")
    asset_data = load_universe()
    print(f"Loaded {len(asset_data)} symbols.")

    features = _relative_strength_daily_features(asset_data, PARAMS)
    full_dates = pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in asset_data.values()))))
    rebalance_mask = _rs_rebalance_mask(full_dates, REBALANCE_FREQUENCY)
    rebalance_dates = full_dates[rebalance_mask]

    rows = []
    for label, composite in [("momentum_only (baseline)", False), ("momentum_minus_zscore_atr", True)]:
        started = time.time()
        selections = select_by_score(features, rebalance_dates, PARAMS["rs_top_n"], composite)
        result = simulate(asset_data, selections, full_dates)
        elapsed = time.time() - started
        result["variant"] = label
        result["seconds"] = round(elapsed, 2)
        rows.append(result)
        print(f"  {label:<28} return={result['net_return_pct']:>8.2f}% "
              f"sharpe={result['sharpe_ratio']:>6.2f} maxdd={result['max_drawdown_pct']:>7.2f}%  ({elapsed:.1f}s)")

    frame = pd.DataFrame(rows)[["variant", "net_return_pct", "sharpe_ratio", "max_drawdown_pct", "seconds"]]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "rs_factor_combination.csv"
    frame.to_csv(csv_path, index=False)
    print(f"\nWrote {csv_path}")
    return frame


if __name__ == "__main__":
    run_comparison()
