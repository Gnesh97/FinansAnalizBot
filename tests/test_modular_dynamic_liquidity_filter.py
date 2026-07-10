import unittest
from unittest.mock import patch

import pandas as pd

from testdata.runner import _build_experiment_tasks
from testdata.strategies.relative_strength_rotation import (
    _run_relative_strength_rotation_backtest,
)


def _rs_feature_frame(signal_adv):
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    return pd.DataFrame(
        {
            "Open": [10.0, 10.0, 10.0, 10.0],
            "Close": [10.0, 10.0, 11.0, 12.0],
            "Signal_Avg_Dollar_Volume": signal_adv,
            "Avg_Dollar_Volume": signal_adv,
        },
        index=dates,
    )


def _patched_rs_selection(signal_adv):
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    features = {"LOW": _rs_feature_frame(signal_adv)}
    selections = {dates[0]: {"LOW"}}
    regime = pd.Series([True, True, True, True], index=dates)
    return features, selections, regime, None


class ModularDynamicLiquidityFilterTest(unittest.TestCase):
    def test_cli_flag_reaches_modular_experiment_params(self):
        tasks = _build_experiment_tasks(
            [{"name": "rs_test", "strategy_name": "relative_strength_rotation"}],
            data_folder="hisseler",
            run_profile="fast",
            quick_mode=True,
            intraday_dir="intraday",
            daily_dir="daily",
            raw_dir="raw",
            intraday_timeframe="130min",
            daily_timeframe="1D",
            cache_rebuilt=False,
            include_raw=False,
            min_dollar_volume=500000.0,
        )

        self.assertEqual(tasks[0]["params"]["min_dollar_volume"], 500000.0)

    def test_default_disabled_matches_explicit_zero_for_modular_engine(self):
        with patch(
            "testdata.strategies.relative_strength_rotation._relative_strength_selection",
            return_value=_patched_rs_selection([100000.0] * 4),
        ):
            default_metrics = _run_relative_strength_rotation_backtest({}, {})
            zero_metrics = _run_relative_strength_rotation_backtest(
                {}, {"min_dollar_volume": 0.0}
            )

        self.assertEqual(default_metrics["Islem Sayisi"], zero_metrics["Islem Sayisi"])
        self.assertEqual(default_metrics["Bitis Kasa"], zero_metrics["Bitis Kasa"])
        self.assertEqual(
            default_metrics["Net Getiri (%)"], zero_metrics["Net Getiri (%)"]
        )
        self.assertEqual(default_metrics["Liquidity Skipped Entry Count"], 0)
        self.assertFalse(default_metrics["Dynamic Liquidity Filter Enabled"])

    def test_enabled_modular_filter_skips_entry_without_consuming_cash(self):
        with patch(
            "testdata.strategies.relative_strength_rotation._relative_strength_selection",
            return_value=_patched_rs_selection([100000.0] * 4),
        ):
            metrics = _run_relative_strength_rotation_backtest(
                {}, {"min_dollar_volume": 500000.0}
            )

        self.assertEqual(metrics["Islem Sayisi"], 0)
        self.assertEqual(metrics["Bitis Kasa"], 10000.0)
        self.assertEqual(metrics["Liquidity Skipped Entry Count"], 1)
        self.assertEqual(metrics["Liquidity Skipped Symbols"], "LOW")
        self.assertEqual(metrics["Liquidity Skipped Reasons"], "below_min_dollar_volume")
        self.assertTrue(metrics["Dynamic Liquidity Filter Enabled"])
        self.assertEqual(metrics["_trade_log"], [])

    def test_modular_exit_not_blocked_when_later_liquidity_drops(self):
        with patch(
            "testdata.strategies.relative_strength_rotation._relative_strength_selection",
            return_value=_patched_rs_selection(
                [1000000.0, 0.0, 0.0, 0.0]
            ),
        ):
            metrics = _run_relative_strength_rotation_backtest(
                {}, {"min_dollar_volume": 500000.0}
            )

        self.assertEqual(metrics["Islem Sayisi"], 1)
        self.assertEqual(metrics["Liquidity Skipped Entry Count"], 0)
        self.assertEqual(metrics["_trade_log"][0]["Exit Reason"], "forced_final_exit")

    def test_modular_filter_rejects_missing_signal_adv(self):
        with patch(
            "testdata.strategies.relative_strength_rotation._relative_strength_selection",
            return_value=_patched_rs_selection([pd.NA, pd.NA, pd.NA, pd.NA]),
        ):
            metrics = _run_relative_strength_rotation_backtest(
                {}, {"min_dollar_volume": 500000.0}
            )

        self.assertEqual(metrics["Islem Sayisi"], 0)
        self.assertEqual(metrics["Liquidity Skipped Entry Count"], 1)
        self.assertEqual(metrics["Liquidity Skipped Reasons"], "missing_dollar_volume")


if __name__ == "__main__":
    unittest.main()
