import unittest
from pathlib import Path
import pandas as pd

from testdata.runner import _load_or_build_prepared_cache, _default_params
from testdata.baseline_parity import _baseline_experiment
from testdata.strategies.relative_strength_rotation import (
    _normalize_relative_strength_params,
    _run_relative_strength_rotation_backtest,
    _relative_strength_selection,
)

class DailyOnlyEquivalenceGuardTest(unittest.TestCase):
    def test_daily_only_pipeline_produces_exact_frozen_baseline(self):
        project_root = Path(__file__).resolve().parents[1]
        data_folder = project_root / "testdata" / "research_data" / "rs_v2_sip_market_data"
        if not data_folder.exists():
            self.skipTest("SIP dataset folder not present in test environment.")
            
        intraday_dir = data_folder / "bars_130min"
        daily_dir = data_folder / "daily"
        
        symbols = sorted([p.name.removesuffix("_1D.csv") for p in daily_dir.glob("*_1D.csv") if p.name.endswith("_1D.csv")])
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
