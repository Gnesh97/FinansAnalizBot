"""Freeze the rs_v2_tiingo strategy baseline (Phase 7A).

Computes the frozen production strategy result on the validated rs_v2_tiingo
dataset over a fixed, reproducible window and writes a versioned baseline
artifact consumed by tests/test_rs_v2_tiingo_strategy_baseline.py.

Uses the PRODUCTION harness -- runner._load_or_build_prepared_cache followed by
_run_relative_strength_rotation_backtest -- because Phase 7A forensics proved
that hand-built frames and ad-hoc slicing produce different numbers. See the
END_DATE note below.

Read-only with respect to market data. Writes exactly one artifact, and never
overwrites the SIP-era baselines.

Regenerate with:
    python3 scripts/build_tiingo_strategy_baseline.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "testdata"))

import runner as runner_module  # noqa: E402
from baseline_parity import _baseline_experiment  # noqa: E402
from strategies.relative_strength_rotation import (  # noqa: E402
    _normalize_relative_strength_params,
    _relative_strength_selection,
    _run_relative_strength_rotation_backtest,
)
from vericekici_updated import load_v2_frozen_universe  # noqa: E402

DATA_FOLDER = PROJECT_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data"
BASELINE_PATH = PROJECT_ROOT / "testdata" / "baselines" / "rs_v2_tiingo_strategy_baseline.json"

BASELINE_SCHEMA_VERSION = "1.0.0"
TARGET_START = "2020-07-27"
TARGET_END_SESSION = "2026-07-21"
# Bars are stamped at NY-midnight-in-UTC (04:00Z EDT / 05:00Z EST), and
# runner._slice_frame compares with `index <= Timestamp(end_date)`. A bare
# "2026-07-21" resolves to 00:00Z and would silently drop that entire session --
# the exact defect Phase 7A root-caused in the Phase 5D comparison. Pass an
# end-of-day bound instead, and assert the retained last session below.
TARGET_END = f"{TARGET_END_SESSION} 23:59:59"


def selection_fingerprint(selections: dict) -> tuple[str, list]:
    """SHA-256 over the ordered per-rebalance Top-N selection sets.

    Symbols are sorted within each date and dates are sorted ascending, so the
    digest is stable across dict ordering but changes if any single symbol on
    any single rebalance date changes.
    """
    ordered = [
        [str(date.date()), sorted(str(s) for s in symbols)]
        for date, symbols in sorted(selections.items())
        if symbols
    ]
    canonical = json.dumps(ordered, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), ordered


def build() -> dict:
    daily_dir = DATA_FOLDER / "daily"
    intraday_dir = DATA_FOLDER / "bars_130min"

    symbols = sorted(p.name.removesuffix("_1D.csv") for p in daily_dir.glob("*_1D.csv"))
    frozen, frozen_baseline = load_v2_frozen_universe()
    extra = sorted(set(symbols) - set(frozen))
    missing = sorted(set(frozen) - set(symbols))
    if extra or missing:
        # Phase 7A: the SIP guard silently ran on 178 symbols because retired BK
        # was still on disk. Fail loudly rather than freezing a contaminated set.
        raise SystemExit(f"universe mismatch -- extra={extra} missing={missing}")

    prepared, _, _ = runner_module._load_or_build_prepared_cache(
        str(DATA_FOLDER), str(intraday_dir), str(daily_dir),
        "130min", "1D", TARGET_START, TARGET_END, symbols,
    )
    asset_data = prepared["asset_data"]

    last_sessions = {str(frame.index.max().date()) for frame in asset_data.values()}
    if TARGET_END_SESSION not in last_sessions:
        raise SystemExit(f"window bound dropped the target session; last sessions seen: {sorted(last_sessions)[-3:]}")

    experiment = _baseline_experiment()
    params = runner_module._default_params(experiment, str(DATA_FOLDER), "full", False)
    strategy_params = _normalize_relative_strength_params(params)

    metrics = _run_relative_strength_rotation_backtest(
        asset_data, strategy_params, benchmark_data=prepared.get("benchmark_data"),
    )
    _, selections, _, _ = _relative_strength_selection(
        asset_data, strategy_params, benchmark_data=prepared.get("benchmark_data"),
    )
    digest, ordered = selection_fingerprint(selections)

    return {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "dataset_profile": "rs_v2_tiingo",
        "strategy_identifier": experiment["name"],
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "FROZEN",
        "window": {
            "target_start": TARGET_START,
            "target_end_session": TARGET_END_SESSION,
            "slice_upper_bound": TARGET_END,
        },
        "universe": {
            "candidate_count": len(symbols),
            "candidate_universe_hash": frozen_baseline["universe"]["candidate_universe_hash"],
        },
        "metrics": {
            "net_return_pct": metrics.get("Net Getiri (%)"),
            "trade_count": metrics.get("Islem Sayisi") or metrics.get("Trade Count"),
            "cagr_pct": metrics.get("CAGR (%)"),
            "max_drawdown_pct": metrics.get("Max Drawdown (%)"),
            "sharpe_ratio": metrics.get("Sharpe Ratio"),
            "win_rate_pct": metrics.get("Win Rate (%)"),
        },
        "selection": {
            "rebalance_date_count": len(ordered),
            "first_rebalance_date": ordered[0][0] if ordered else None,
            "last_rebalance_date": ordered[-1][0] if ordered else None,
            "top_n": strategy_params["rs_top_n"],
            "fingerprint_sha256": digest,
        },
    }


def main() -> None:
    baseline = build()
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(baseline, indent=2, sort_keys=True))
    print(f"\nwrote {BASELINE_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
