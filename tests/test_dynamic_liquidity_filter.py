import unittest

import numpy as np
import pandas as pd

from testdata.portfolio_manager import PORTFOLIO_CONTEXT_SOURCE_COLUMNS, PortfolioManager


def _asset_frame(avg_dollar_volume):
    dates = pd.date_range("2026-01-01", periods=6, freq="D")
    frame = pd.DataFrame(
        {
            "Open": [10.0, 10.0, 10.0, 11.0, 12.0, 12.0],
            "High": [10.5, 10.5, 11.5, 12.0, 12.5, 12.5],
            "Low": [9.5, 9.5, 9.5, 10.5, 11.5, 11.5],
            "Close": [10.0, 10.0, 11.0, 12.0, 12.0, 12.0],
            "ENTRY_SIGNAL": [False, True, False, False, False, False],
            "EXIT_SIGNAL": [False, False, False, True, False, False],
            "SMA20_Reversion_Exit": [False, False, False, True, False, False],
            "Take_Profit": [False] * 6,
            "Hurst_Exponent": [0.6] * 6,
            "Momentum_Factor": [1.0] * 6,
            "Momentum_Factor_Raw": [1.0] * 6,
            "ATR_Pct": [2.0] * 6,
            "Disaster_Stop": [8.0] * 6,
            "ATR_Take_Profit_Distance": [5.0] * 6,
            "SuperTrend_ATR_22": [1.0] * 6,
            "Risk_Off": [False] * 6,
            "Regime_Ready": [True] * 6,
            "Avg_Dollar_Volume": avg_dollar_volume,
        },
        index=dates,
    )
    for column in PORTFOLIO_CONTEXT_SOURCE_COLUMNS:
        if column not in frame:
            frame[column] = True if column.endswith("_OK") or column in {"Macro_Uptrend"} else np.nan
    frame["Confirmation_Mode"] = "off"
    frame["Trigger"] = True
    frame["Raw_Entry_Signal"] = frame["ENTRY_SIGNAL"]
    frame["Confirmed_Entry_Signal"] = frame["ENTRY_SIGNAL"]
    frame["Entry_Context_Rejected_Reason"] = ""
    return frame


class DynamicLiquidityFilterTest(unittest.TestCase):
    def _run(self, frame, min_dollar_volume=0.0):
        manager = PortfolioManager(
            {"LOW": frame},
            initial_cash=10000.0,
            max_open_positions=1,
            risk_per_trade=0.02,
            ranking_mode="momentum",
            verbose=False,
            fast_backtest=True,
            commission_rate=0.0,
            slippage_mu=0.0,
            min_dollar_volume=min_dollar_volume,
        )
        metrics = manager.run_backtest()
        return manager, metrics

    def test_default_disabled_matches_explicit_zero_filter(self):
        frame = _asset_frame([100000.0] * 6)
        default_manager, default_metrics = self._run(frame)
        zero_manager, zero_metrics = self._run(frame, min_dollar_volume=0.0)

        self.assertEqual(default_metrics["Islem Sayisi"], zero_metrics["Islem Sayisi"])
        self.assertEqual(default_metrics["Bitis Kasa"], zero_metrics["Bitis Kasa"])
        self.assertEqual(default_metrics["Net Getiri (%)"], zero_metrics["Net Getiri (%)"])
        self.assertEqual(len(default_manager.trade_log), len(zero_manager.trade_log))
        for field in ["Symbol", "Entry Time", "Exit Time", "Net PnL", "R Multiple"]:
            self.assertEqual(
                default_manager.trade_log[0][field],
                zero_manager.trade_log[0][field],
            )
        self.assertFalse(default_metrics["Dynamic Liquidity Filter Enabled"])

    def test_enabled_500k_filter_skips_failing_entry_without_cash_use(self):
        frame = _asset_frame([100000.0] * 6)
        manager, metrics = self._run(frame, min_dollar_volume=500000.0)

        self.assertEqual(metrics["Islem Sayisi"], 0)
        self.assertEqual(metrics["Liquidity Skipped Entry Count"], 1)
        self.assertEqual(metrics["Liquidity Skipped Symbols"], "LOW")
        self.assertEqual(metrics["Rejected Liquidity Dollar Volume"], 1)
        self.assertEqual(metrics["Bitis Kasa"], 10000.0)
        self.assertEqual(manager.current_cash, 10000.0)
        self.assertEqual(manager.positions, {})
        self.assertEqual(manager.trade_log, [])

    def test_exit_is_not_blocked_when_entry_liquidity_later_drops(self):
        frame = _asset_frame([1000000.0, 1000000.0, 0.0, 0.0, 0.0, 0.0])
        manager, metrics = self._run(frame, min_dollar_volume=500000.0)

        self.assertEqual(metrics["Islem Sayisi"], 1)
        self.assertEqual(metrics["Liquidity Skipped Entry Count"], 0)
        self.assertEqual(manager.trade_log[0]["Exit Reason"], "sma20_reversion")
        self.assertEqual(manager.positions, {})

    def test_enabled_filter_reports_missing_or_zero_volume_as_skipped(self):
        frame = _asset_frame([np.nan, 0.0, 0.0, 0.0, 0.0, 0.0])
        _, metrics = self._run(frame, min_dollar_volume=500000.0)

        self.assertEqual(metrics["Islem Sayisi"], 0)
        self.assertEqual(metrics["Liquidity Skipped Entry Count"], 1)
        self.assertEqual(metrics["Liquidity Skipped Symbols"], "LOW")


if __name__ == "__main__":
    unittest.main()
