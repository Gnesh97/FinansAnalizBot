import unittest

import pandas as pd

from quantconnect.rs_m126_top30_sma100_no_regime.port_helpers import (
    STATIC_PARITY,
    build_static_universe,
    can_publish_executable,
    compute_return_over_vol,
    ensure_long_only_targets,
    rank_symbols,
    static_universe_hash,
    validate_universe_mode,
)


class QuantConnectPortTest(unittest.TestCase):
    def test_momentum_volatility_and_return_over_vol_match_formula(self):
        close = pd.Series([100.0 + index for index in range(200)])
        result = compute_return_over_vol(close, momentum_lookback_days=126, volatility_lookback_days=60)
        self.assertAlmostEqual(result["momentum"].iloc[-1], close.iloc[-1] / close.iloc[-127] - 1)
        self.assertAlmostEqual(result["score"].iloc[-1], result["momentum"].iloc[-1] / result["annualized_volatility"].iloc[-1])

    def test_ranking_tie_breaking_selection_and_weights(self):
        # QQQ is now allowed as candidate, so it ranks highest
        ranked = rank_symbols({"MSFT": 1.0, "AAPL": 1.0, "QQQ": 9.0}, top_n=2)
        self.assertEqual(ranked, ["QQQ", "AAPL"])
        self.assertEqual(ensure_long_only_targets(ranked), {"AAPL": 0.5, "QQQ": 0.5})

    def test_cold_start_and_short_generation_are_blocked(self):
        close = pd.Series([100.0] * 125)
        result = compute_return_over_vol(close, momentum_lookback_days=126, volatility_lookback_days=60)
        self.assertTrue(result["score"].isna().all())
        with self.assertRaisesRegex(ValueError, "long-only"):
            ensure_long_only_targets({"AAPL": -0.1})

    def test_static_universe_hash_is_deterministic_and_qqq_is_included(self):
        # QQQ/SPY are now included in build_static_universe to match baseline
        universe = build_static_universe(["MSFT", "QQQ", "AAPL"])
        self.assertEqual(universe, ["AAPL", "MSFT", "QQQ"])
        self.assertEqual(static_universe_hash(universe), static_universe_hash(list(reversed(universe))))

    def test_pit_cannot_use_static_list_and_no_optimization_path_exists(self):
        self.assertEqual(validate_universe_mode(STATIC_PARITY, ["AAPL"]), STATIC_PARITY)
        with self.assertRaisesRegex(ValueError, "PIT_VALIDATION"):
            validate_universe_mode("PIT_VALIDATION", ["AAPL"])

    def test_unresolved_blocking_contract_prevents_executable_publication(self):
        self.assertFalse(can_publish_executable([{"blocker_scope": "STATIC_BLOCKING"}]))
        self.assertFalse(can_publish_executable([{"blocker_scope": "BOTH"}]))
        self.assertTrue(can_publish_executable([{"blocker_scope": "PIT_BLOCKING"}]))
        self.assertTrue(can_publish_executable([{"blocker_scope": "NONE"}]))

    def test_reference_events_file_integrity(self):
        # Read reference events and verify counts
        import csv
        events_file = "testdata/reports/latest/quantconnect_local_reference_events.csv"
        rows = []
        with open(events_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        
        self.assertEqual(len(rows), 73)
        
        import json
        warmup = 0
        nonempty = 0
        for row in rows:
            selected = json.loads(row["selected_top_30"])
            if len(selected) == 0:
                warmup += 1
            else:
                nonempty += 1
                
        self.assertEqual(warmup, 6)
        self.assertEqual(nonempty, 67)


if __name__ == "__main__":
    unittest.main()
