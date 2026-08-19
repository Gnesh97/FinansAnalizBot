"""Phase 9A -- reproduce the frozen window on the SETTLED dataset.

Read-only. Runs the production harness over the frozen baseline window and
reports headline metrics plus the selection fingerprint, so the re-freeze uses
measured values rather than transcribed ones.

This is the verification Phase 8D 3.3 left open: it could not run while the
nightly refresh was mid-write.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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

    metrics = _run_relative_strength_rotation_backtest(
        asset_data, strategy_params, benchmark_data=benchmark,
    )
    _, selections, _, _ = _relative_strength_selection(
        asset_data, strategy_params, benchmark_data=benchmark,
    )
    digest, _ = selection_fingerprint(selections)

    observed = {
        "net_return_pct": metrics.get("Net Getiri (%)"),
        "trade_count": metrics.get("Islem Sayisi") or metrics.get("Trade Count"),
        "cagr_pct": metrics.get("CAGR (%)"),
        "max_drawdown_pct": metrics.get("Max Drawdown (%)"),
        "sharpe_ratio": metrics.get("Sharpe Ratio"),
        "win_rate_pct": metrics.get("Win Rate (%)"),
    }

    print("=== FROZEN WINDOW ON SETTLED DATASET ===")
    print(f"  symbols          : {len(symbols)}")
    print(f"  window           : {baseline['window']['target_start']} -> {baseline['window']['slice_upper_bound']}")
    print()
    print(f"{'metric':<20} {'baseline':>14} {'observed':>14} {'delta':>12}")
    for key, value in observed.items():
        prior = baseline["metrics"].get(key)
        delta = "" if prior is None or value is None else f"{value - prior:+.4f}"
        print(f"{key:<20} {prior!s:>14} {value!s:>14} {delta:>12}")
    print()
    print(f"  fingerprint baseline : {baseline['selection']['fingerprint_sha256']}")
    print(f"  fingerprint observed : {digest}")
    print(f"  FINGERPRINT MATCHES  : {digest == baseline['selection']['fingerprint_sha256']}")

    out = PROJECT_ROOT / "scratch" / "phase9a_frozen_window_measure.json"
    out.write_text(json.dumps({
        "observed": observed,
        "baseline": baseline["metrics"],
        "fingerprint_observed": digest,
        "fingerprint_baseline": baseline["selection"]["fingerprint_sha256"],
        "fingerprint_matches": digest == baseline["selection"]["fingerprint_sha256"],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwrote {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
