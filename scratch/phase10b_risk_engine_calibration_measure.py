"""Phase 10B -- calibrate Risk Engine (Mode A) thresholds against real history.

Read-only. Reproduces the frozen baseline window (fidelity gate first, per the
hard-won "measure through production code path" lesson) and derives two
empirical distributions the Risk Engine's thresholds were only guessed at in
Phase 10A:

1. Historical portfolio heat -- under equal weight (target_weight = 1/rs_top_n,
   ~constant per position), heat_pct as defined in testdata/risk_engine.py
   (sum(shares * dollar_atr) / total_open_notional * 100) reduces to
   approximately the mean ATR_Pct of the held Top-30 at each rebalance date
   (see derivation in the Phase 10B report). Computed directly from the
   production ATR_Pct feature, not the approximation, to avoid compounding
   errors.
2. Daily equity-curve return distribution, from the production backtest's own
   _equity_curve, to sanity-check the -3%/-5% loss-limit levels against what
   this strategy's real daily moves look like.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "testdata"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import runner as runner_module  # noqa: E402
from baseline_parity import _baseline_experiment  # noqa: E402
from strategies.relative_strength_rotation import (  # noqa: E402
    _normalize_relative_strength_params,
    _relative_strength_selection,
    _run_relative_strength_rotation_backtest,
)
from build_tiingo_strategy_baseline import selection_fingerprint  # noqa: E402

DATA_FOLDER = PROJECT_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data"
BASELINE_PATH = PROJECT_ROOT / "testdata" / "baselines" / "rs_v2_tiingo_strategy_baseline.json"


def main() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    daily_dir = DATA_FOLDER / "daily"
    intraday_dir = DATA_FOLDER / "bars_130min"
    symbols = sorted(p.name.removesuffix("_1D.csv") for p in daily_dir.glob("*_1D.csv"))

    prepared, _, _ = runner_module._load_or_build_prepared_cache(
        str(DATA_FOLDER), str(intraday_dir), str(daily_dir), "130min", "1D",
        baseline["window"]["target_start"], baseline["window"]["slice_upper_bound"], symbols,
    )
    asset_data = prepared["asset_data"]
    benchmark = prepared.get("benchmark_data")

    experiment = _baseline_experiment()
    params = runner_module._default_params(experiment, str(DATA_FOLDER), "full", False)
    strategy_params = _normalize_relative_strength_params(params)

    # --- Fidelity gate: must reproduce the frozen baseline before trusting
    # anything derived from this run. ---
    metrics = _run_relative_strength_rotation_backtest(
        asset_data, strategy_params, benchmark_data=benchmark,
    )
    features, selections, regime, rebalance = _relative_strength_selection(
        asset_data, strategy_params, benchmark_data=benchmark,
    )
    digest, _ = selection_fingerprint(selections)
    fingerprint_matches = digest == baseline["selection"]["fingerprint_sha256"]
    print("=== FIDELITY GATE ===")
    print(f"  fingerprint baseline : {baseline['selection']['fingerprint_sha256']}")
    print(f"  fingerprint observed : {digest}")
    print(f"  MATCHES              : {fingerprint_matches}")
    if not fingerprint_matches:
        print("\nABORT: fingerprint mismatch, derived numbers below would be untrustworthy.")
        raise SystemExit(1)

    # --- Measurement 1: historical portfolio heat under equal weight ---
    heat_by_date = {}
    for date, held in selections.items():
        if not held:
            continue
        atr_values = []
        for symbol in held:
            frame = features.get(symbol)
            if frame is None or date not in frame.index:
                continue
            atr_pct = frame.loc[date, "ATR_Pct"]
            if pd.notna(atr_pct):
                atr_values.append(float(atr_pct))
        if atr_values:
            heat_by_date[str(date.date())] = {
                "mean_atr_pct_of_held": float(np.mean(atr_values)),
                "max_atr_pct_of_held": float(np.max(atr_values)),
                "held_count": len(atr_values),
            }
    heat_series = pd.Series({k: v["mean_atr_pct_of_held"] for k, v in heat_by_date.items()})
    heat_percentiles = {
        "min": float(heat_series.min()),
        "p05": float(heat_series.quantile(0.05)),
        "p50": float(heat_series.quantile(0.50)),
        "p95": float(heat_series.quantile(0.95)),
        "p99": float(heat_series.quantile(0.99)),
        "max": float(heat_series.max()),
        "n_rebalances": int(heat_series.size),
    }

    # --- Measurement 2: daily equity-curve return distribution ---
    equity_curve = metrics.get("_equity_curve", [])
    equity = pd.Series(
        [value for _, value in equity_curve],
        index=pd.to_datetime([ts for ts, _ in equity_curve]),
    ).sort_index()
    daily_returns_pct = equity.pct_change(fill_method=None).dropna() * 100
    return_percentiles = {
        "min": float(daily_returns_pct.min()),
        "p01": float(daily_returns_pct.quantile(0.01)),
        "p05": float(daily_returns_pct.quantile(0.05)),
        "p50": float(daily_returns_pct.quantile(0.50)),
        "p95": float(daily_returns_pct.quantile(0.95)),
        "p99": float(daily_returns_pct.quantile(0.99)),
        "max": float(daily_returns_pct.max()),
        "n_days": int(daily_returns_pct.size),
        "n_days_below_neg3pct": int((daily_returns_pct <= -3.0).sum()),
        "n_days_below_neg5pct": int((daily_returns_pct <= -5.0).sum()),
    }

    print("\n=== HISTORICAL PORTFOLIO HEAT (mean ATR_Pct of held Top-30, per rebalance) ===")
    for k, v in heat_percentiles.items():
        print(f"  {k:<15}: {v}")

    print("\n=== DAILY EQUITY-CURVE RETURN DISTRIBUTION (%) ===")
    for k, v in return_percentiles.items():
        print(f"  {k:<22}: {v}")

    out = PROJECT_ROOT / "scratch" / "phase10b_risk_engine_calibration_measure.json"
    out.write_text(
        json.dumps(
            {
                "fingerprint_matches": fingerprint_matches,
                "heat_percentiles": heat_percentiles,
                "return_percentiles": return_percentiles,
                "heat_by_date_sample": dict(list(heat_by_date.items())[:5]),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
