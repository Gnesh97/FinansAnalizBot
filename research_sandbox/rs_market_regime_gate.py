"""Market-regime gate: does turning on the existing-but-unused rs_use_market_regime
flag (go to cash when SPY < its 200-day SMA) improve results on the survivorship-bias-
corrected universe?

Exploratory only -- see research_sandbox/README.md for scope and limitations. Reuses the
production selection logic (testdata.strategies.relative_strength_rotation, including its
built-in regime gate) read-only and unmodified -- the flag already exists in production
code, it is simply False in the frozen baseline (rs_m126_top30_sma100_no_regime, "no
regime" is literally in the name). Runs on the survivorship-bias-corrected universe
(production + full delisted population from phase24), same as phase26/27.
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

from testdata.data_engine import read_ohlcv_csv_fast
from testdata.strategies.relative_strength_rotation import _relative_strength_selection
from rs_rotation_sensitivity import load_expanded_universe
from rs_survivorship_bias import _normalize_index

BENCHMARK_PATH = REPO_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data" / "benchmarks" / "daily" / "SPY_1D.csv"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

BASE_PARAMS = {
    "rs_lookback_days": 126,
    "rs_top_n": 30,
    "rs_score_mode": "return_over_vol",
    "rs_vol_lookback_days": 60,
    "rs_trend_sma_days": 100,
    "rs_market_regime_sma_days": 200,
    "rs_min_price": 10,
    "rs_max_atr_pct": 12,
    "rs_rebalance_frequency": "monthly",
}


def load_benchmark() -> pd.DataFrame:
    frame = _normalize_index(read_ohlcv_csv_fast(BENCHMARK_PATH))
    frame.attrs["is_daily"] = True
    return frame


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


def simulate(asset_data: dict[str, pd.DataFrame], params: dict, benchmark_data: pd.DataFrame, label: str) -> dict:
    _, selections, regime, _ = _relative_strength_selection(asset_data, params, benchmark_data=benchmark_data)
    full_dates = pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in asset_data.values()))))
    weights, price = build_weight_and_price_frames(asset_data, selections, full_dates)

    portfolio = vbt.Portfolio.from_orders(
        close=price, size=weights, size_type="targetpercent", group_by=True,
        cash_sharing=True, call_seq="auto", fees=0.0, slippage=0.0,
        init_cash=100_000.0, freq="1D",
    )
    stats = portfolio.stats()
    months_out_of_market = sum(1 for date in selections if not bool(regime.loc[date]))
    return {
        "label": label,
        "net_return_pct": float(stats.get("Total Return [%]", np.nan)),
        "sharpe_ratio": float(stats.get("Sharpe Ratio", np.nan)),
        "max_drawdown_pct": float(stats.get("Max Drawdown [%]", np.nan)),
        "rebalance_count": len(selections),
        "months_out_of_market": months_out_of_market,
    }


def run_comparison() -> pd.DataFrame:
    print("Loading EXPANDED universe (production + full delisted population) ...")
    asset_data = load_expanded_universe()
    print(f"Loaded {len(asset_data)} symbols.")
    benchmark = load_benchmark()

    rows = []
    for label, use_regime in [("baseline (no regime gate)", False), ("plus_market_regime_gate", True)]:
        params = dict(BASE_PARAMS, rs_use_market_regime=use_regime)
        started = time.time()
        result = simulate(asset_data, params, benchmark, label)
        elapsed = time.time() - started
        result["seconds"] = round(elapsed, 2)
        rows.append(result)
        print(f"  {label:<28} return={result['net_return_pct']:>8.2f}% "
              f"sharpe={result['sharpe_ratio']:>6.2f} maxdd={result['max_drawdown_pct']:>7.2f}% "
              f"months_out_of_market={result['months_out_of_market']:>3}  ({elapsed:.1f}s)")

    frame = pd.DataFrame(rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "rs_market_regime_gate.csv"
    frame.to_csv(csv_path, index=False)
    print(f"\nWrote {csv_path}")
    return frame


if __name__ == "__main__":
    run_comparison()
