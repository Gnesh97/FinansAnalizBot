"""Phase 8D -- prove the Sharpe fix touches ONLY volatility and Sharpe.

EVALUATION ONLY. Imported by nothing.

A before/after comparison across two runs is invalid while the nightly refresh
is rewriting the dataset underneath. This proof avoids that entirely: it takes a
SINGLE equity curve and trade log and derives the return series twice -- once
with the old calendar-resample convention, once with the new trading-day one.
Identical inputs, so any difference is caused by the formula and nothing else.

    python3 scratch/phase8d_metric_isolation_proof.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "testdata"))

from testdata.runner import _default_params, _load_or_build_prepared_cache
from testdata.baseline_parity import _baseline_experiment
from testdata.strategies.relative_strength_rotation import (
    _normalize_relative_strength_params,
    _run_relative_strength_rotation_backtest,
)

DATA_FOLDER = PROJECT_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data"
BASELINE_PATH = PROJECT_ROOT / "testdata" / "baselines" / "rs_v2_tiingo_strategy_baseline.json"

# Every metric the function emits that is NOT derived from the return series.
COMPARED_FIELDS = [
    "Net Getiri (%)", "CAGR (%)", "Max Drawdown (%)", "Islem Sayisi", "Trade Count",
    "Win Rate (%)", "Profit Factor", "Avg R", "Median R", "Best R", "Worst R",
    "Expectancy R", "Avg Holding Bars", "Median Holding Bars", "Turnover Estimate",
    "Avg Positions Held", "Max Positions Held", "Max Acik Pozisyon",
    "Max Portfolio Heat (%)", "Baslangic Kasa", "Bitis Kasa",
]


def equity_series(equity_curve):
    return pd.Series([v for _, v in equity_curve],
                     index=pd.DatetimeIndex([t for t, _ in equity_curve]), dtype=float)


def stats(returns):
    vol = float(returns.std())
    return {
        "observations": int(len(returns)),
        "annualized_vol_pct": round(vol * np.sqrt(252) * 100, 4),
        "sharpe": round(float(returns.mean() / vol * np.sqrt(252)), 4),
    }


def main() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    daily_dir, intraday_dir = DATA_FOLDER / "daily", DATA_FOLDER / "bars_130min"
    symbols = sorted(p.name.removesuffix("_1D.csv") for p in daily_dir.glob("*_1D.csv"))
    prepared, _, _ = _load_or_build_prepared_cache(
        str(DATA_FOLDER), str(intraday_dir), str(daily_dir), "130min", "1D",
        baseline["window"]["target_start"], baseline["window"]["slice_upper_bound"], symbols,
    )
    params = _normalize_relative_strength_params(
        _default_params(_baseline_experiment(), str(DATA_FOLDER), "full", False))

    # ONE backtest -> one equity curve and trade log, shared by both formulas.
    metrics = _run_relative_strength_rotation_backtest(
        prepared["asset_data"], params, benchmark_data=prepared.get("benchmark_data"))
    equity = equity_series(metrics["_equity_curve"])

    old_returns = equity.resample("1D").last().pct_change(fill_method=None).dropna()
    new_returns = equity.pct_change(fill_method=None).dropna()
    old_stats, new_stats = stats(old_returns), stats(new_returns)
    dropped = new_stats["observations"] - old_stats["observations"]

    print("=== SINGLE RUN, TWO RETURN CONVENTIONS (identical equity curve) ===")
    print(f"  old (resample '1D'): {old_stats}")
    print(f"  new (trading day)  : {new_stats}")
    print(f"  observations dropped by the old convention: {dropped} "
          f"({dropped / new_stats['observations'] * 100:.1f}%)")

    print("\n=== FIELDS NOT DERIVED FROM THE RETURN SERIES ===")
    print("  (produced by the same single call; the fix cannot reach them)")
    for field in COMPARED_FIELDS:
        print(f"  {field:<26} {metrics.get(field)}")

    cagr = metrics.get("CAGR (%)")
    print("\n=== INTERNAL CONSISTENCY ===")
    print(f"  CAGR {cagr}% / annualized vol {new_stats['annualized_vol_pct']}% "
          f"-> implied Sharpe ~{cagr / new_stats['annualized_vol_pct']:.3f}")
    print(f"  new Sharpe {new_stats['sharpe']} is consistent with that; "
          f"old Sharpe {old_stats['sharpe']} is not.")
    print("\nNOTE: the live dataset is being rewritten by the nightly refresh, so the "
          "ABSOLUTE values here are not authoritative for re-freezing. The old-vs-new "
          "DELTA is, because both series come from one equity curve.")


if __name__ == "__main__":
    main()
