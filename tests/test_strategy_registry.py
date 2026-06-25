import unittest

from testdata.universe_scanner import VALIDATION_EXPERIMENTS


class StrategyRegistryTest(unittest.TestCase):
    def test_default_validation_experiment_names_are_explicit(self):
        expected_names = [
            "rs_m63_sma100",
            "rs_m63_no_regime",
            "dm_m63_top20",
            "donchian_20_10_top20",
            "donchian_55_20_top20",
            "donchian_20_10_top10",
            "donchian_55_20_sma100",
            "mast_sma100_slope20_top20",
            "mast_sma200_slope20_top20",
            "mast_sma100_slope20_top10",
        ]
        self.assertEqual([item["name"] for item in VALIDATION_EXPERIMENTS], expected_names)

    def test_default_validation_strategy_families_are_explicit(self):
        expected_families = {
            "relative_strength_rotation",
            "dual_momentum",
            "donchian_breakout",
            "ma_slope_trend",
        }
        actual_families = {item["strategy_name"] for item in VALIDATION_EXPERIMENTS}
        self.assertEqual(actual_families, expected_families)

    def test_default_validation_excludes_benchmarks_from_trading_universe(self):
        for experiment in VALIDATION_EXPERIMENTS:
            self.assertEqual(experiment.get("excluded_symbols"), ["QQQ", "SPY"])


if __name__ == "__main__":
    unittest.main()
