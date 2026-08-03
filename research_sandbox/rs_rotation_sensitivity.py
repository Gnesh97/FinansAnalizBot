"""Momentum lookback / rebalance frequency sensitivity sweep for the RS rotation strategy.

Exploratory only -- see research_sandbox/README.md for scope and limitations. Reuses the
production selection logic (testdata.strategies.relative_strength_rotation) read-only, then
simulates each parameter combination's equity curve with vectorbt (same-day close execution,
no costs -- a simplification, not the production backtest engine).

Base config matches the frozen production baseline (rs_m126_top30_sma100_no_regime) exactly
except for the two dimensions being swept: rs_lookback_days and rs_rebalance_frequency.

--expanded: run the same sweep on the production universe PLUS the full delisted-candidate
population from rs_survivorship_bias.py (see reports/audit/phase24), instead of the
production-only universe -- checks whether the "best" lookback/rebalance combo found on
the survivorship-biased universe still looks best once delisted names are included.
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
from testdata.strategies.relative_strength_rotation import _relative_strength_selection
from rs_survivorship_bias import FULL_POPULATION_LIST_PATH, load_delisted_universe_batched, _normalize_index

DAILY_DIR = REPO_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data" / "daily"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
EXCLUDED_SYMBOLS = {"QQQ", "SPY"}

BASE_PARAMS = {
    "rs_top_n": 30,
    "rs_score_mode": "return_over_vol",
    "rs_vol_lookback_days": 60,
    "rs_trend_sma_days": 100,
    "rs_use_market_regime": False,
    "rs_market_regime_sma_days": 200,
    "rs_min_price": 10,
    "rs_max_atr_pct": 12,
}

LOOKBACK_GRID = [63, 126, 189, 252]
REBALANCE_GRID = ["weekly", "monthly"]


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


def load_expanded_universe() -> dict[str, pd.DataFrame]:
    asset_data = {symbol: _normalize_index(frame) for symbol, frame in load_universe().items()}
    for frame in asset_data.values():
        frame.attrs["is_daily"] = True
    delisted = load_delisted_universe_batched(FULL_POPULATION_LIST_PATH)
    asset_data.update(delisted)
    return asset_data


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


def simulate_combo(asset_data: dict[str, pd.DataFrame], lookback: int, rebalance_frequency: str) -> dict:
    params = dict(BASE_PARAMS, rs_lookback_days=lookback, rs_rebalance_frequency=rebalance_frequency)
    _, selections, _, _ = _relative_strength_selection(asset_data, params, benchmark_data=None)
    full_dates = pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in asset_data.values()))))
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
        "lookback_days": lookback,
        "rebalance_frequency": rebalance_frequency,
        "net_return_pct": float(stats.get("Total Return [%]", np.nan)),
        "sharpe_ratio": float(stats.get("Sharpe Ratio", np.nan)),
        "max_drawdown_pct": float(stats.get("Max Drawdown [%]", np.nan)),
        "rebalance_count": len(selections),
    }


def run_sweep(expanded: bool = False) -> pd.DataFrame:
    if expanded:
        print("Loading EXPANDED universe (production + full delisted population) ...")
        asset_data = load_expanded_universe()
    else:
        print(f"Loading universe from {DAILY_DIR} ...")
        asset_data = load_universe()
    print(f"Loaded {len(asset_data)} symbols.")

    rows = []
    for lookback in LOOKBACK_GRID:
        for rebalance_frequency in REBALANCE_GRID:
            started = time.time()
            result = simulate_combo(asset_data, lookback, rebalance_frequency)
            elapsed = time.time() - started
            result["seconds"] = round(elapsed, 2)
            rows.append(result)
            print(f"  lookback={lookback:>3} rebalance={rebalance_frequency:<8} "
                  f"return={result['net_return_pct']:>8.2f}% sharpe={result['sharpe_ratio']:>6.2f} "
                  f"maxdd={result['max_drawdown_pct']:>7.2f}%  ({elapsed:.1f}s)")

    frame = pd.DataFrame(rows)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_name = "rs_rotation_sensitivity_expanded.csv" if expanded else "rs_rotation_sensitivity.csv"
    csv_path = OUTPUT_DIR / csv_name
    frame.to_csv(csv_path, index=False)
    print(f"\nWrote {csv_path}")
    return frame


if __name__ == "__main__":
    run_sweep(expanded="--expanded" in sys.argv)
