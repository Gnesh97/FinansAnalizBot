import sys
import unittest
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from baseline_guard_visibility import announce_if_inactive

from testdata.runner import _load_or_build_prepared_cache, _default_params
from testdata.baseline_parity import _baseline_experiment
from testdata.strategies.relative_strength_rotation import (
    _normalize_relative_strength_params,
    _run_relative_strength_rotation_backtest,
    _relative_strength_selection,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIP_DATA_FOLDER = PROJECT_ROOT / "testdata" / "research_data" / "rs_v2_sip_market_data"

# Phase 7C: research_data/ is gitignored, so this guard silently skipped in any
# environment lacking the SIP dataset. Same treatment as the Tiingo guard.
SIP_GUARD_ACTIVE = announce_if_inactive(
    "rs_v2_sip_daily_only_equivalence",
    SIP_DATA_FOLDER.exists(),
    reason=f"missing {SIP_DATA_FOLDER.relative_to(PROJECT_ROOT)}",
    restore_hint="restore the frozen rs_v2_sip dataset (historical control, not the governing baseline)",
)


@unittest.skipUnless(SIP_GUARD_ACTIVE, "SIP dataset folder not present in test environment.")
class DailyOnlyEquivalenceGuardTest(unittest.TestCase):
    """HISTORICAL guard for the Phase 5A daily-only conversion, on frozen SIP data.

    This is NOT the governing baseline for the live system any more -- that is
    tests/test_rs_v2_tiingo_strategy_baseline.py. rs_v2_sip carries stale
    spinoff adjustments (FDX/CMCSA/MMM/MDT, Phase 6A), so these returns are
    computed on prices that no longer reflect those corporate actions.

    It is deliberately kept, because it still tests something real: that the
    daily-only execution path reproduces the pre-conversion result bit for bit
    on unchanged inputs. Phase 7A used exactly that property as the control
    that ruled out a strategy-logic behaviour change.

    Scope note (Phase 7A): this intentionally runs on the SIP *file set on
    disk* (178 series, including retired BK), NOT the frozen 177 universe.
    That is where 762 trades comes from -- restricting to the frozen 177 gives
    771. The symbol set is asserted below so this can never again be mistaken
    for a frozen-universe measurement.
    """

    def test_daily_only_pipeline_produces_exact_frozen_baseline(self):
        data_folder = SIP_DATA_FOLDER
        intraday_dir = data_folder / "bars_130min"
        daily_dir = data_folder / "daily"
        
        symbols = sorted([p.name.removesuffix("_1D.csv") for p in daily_dir.glob("*_1D.csv") if p.name.endswith("_1D.csv")])
        # Phase 7A: make the measured scope explicit. The frozen numbers below
        # are only meaningful for this exact 178-series on-disk set.
        self.assertEqual(len(symbols), 178, "SIP on-disk file set changed; frozen numbers below no longer apply")
        self.assertIn("BK", symbols, "retired BK absent; this is no longer the historical 178-series set")
        prepared, _, _ = _load_or_build_prepared_cache(
            str(data_folder),
            str(intraday_dir),
            str(daily_dir),
            "130min",
            "1D",
            None,
            None,
            symbols,
        )
        
        exp = _baseline_experiment()
        params = _default_params(exp, str(data_folder), "full", False)
        strategy_params = _normalize_relative_strength_params(params)
        
        metrics = _run_relative_strength_rotation_backtest(
            prepared["asset_data"],
            strategy_params,
            benchmark_data=prepared.get("benchmark_data"),
        )
        
        total_return = metrics.get("Net Getiri (%)") or metrics.get("Total Return (%)")
        trade_count = metrics.get("Islem Sayisi") or metrics.get("Trade Count")
        win_rate = metrics.get("Kazanan Islem Orani (%)") or metrics.get("Win Rate (%)")
        max_dd = metrics.get("Max Drawdown (%)")
        
        # Frozen Baseline Assertions
        self.assertAlmostEqual(total_return, 165.9133, places=2)
        self.assertEqual(trade_count, 762)
        self.assertAlmostEqual(win_rate, 50.50, places=1)
        self.assertAlmostEqual(max_dd, -20.47, places=2)

if __name__ == "__main__":
    unittest.main()
