import unittest
import pandas as pd
import numpy as np

from testdata.strategies.cost_model import calculate_transaction_costs, calculate_max_shares
from testdata.strategies.relative_strength_rotation import _run_relative_strength_rotation_backtest
from testdata.strategies.donchian_breakout import _run_donchian_breakout_backtest
from testdata.strategies.dual_momentum import _run_dual_momentum_backtest
from testdata.strategies.ma_slope_trend import _run_ma_slope_trend_backtest

class DynamicTransactionCostTest(unittest.TestCase):
    def test_cost_calculation_zero_params(self):
        params = {}
        costs = calculate_transaction_costs(1000.0, 50.0, params)
        self.assertEqual(costs["commission_cost"], 0.0)
        self.assertEqual(costs["slippage_cost"], 0.0)
        self.assertEqual(costs["total_cost"], 0.0)

    def test_cost_calculation_nonzero_params(self):
        params = {
            "commission_per_share": 0.01,
            "slippage_bps": 10.0,
            "min_commission": 1.0
        }
        # case A: commission > min_commission
        costs = calculate_transaction_costs(1000.0, 200.0, params)
        self.assertEqual(costs["commission_cost"], 2.0)
        self.assertEqual(costs["slippage_cost"], 1.0) # 1000 * 10 / 10000 = 1.0
        self.assertEqual(costs["total_cost"], 3.0)

        # case B: commission < min_commission
        costs = calculate_transaction_costs(100.0, 5.0, params)
        self.assertEqual(costs["commission_cost"], 1.0) # min_comm applies
        self.assertEqual(costs["slippage_cost"], 0.1) # 100 * 10 / 10000 = 0.1
        self.assertEqual(costs["total_cost"], 1.1)

    def test_max_shares_calculation(self):
        params = {
            "commission_per_share": 0.01,
            "slippage_bps": 10.0,
            "min_commission": 2.0
        }
        # With zero cost, should match gross target exactly
        gross_shares = calculate_max_shares(1000.0, 10.0, 1000.0, {})
        self.assertEqual(gross_shares, 100.0)

        # With nonzero cost, max shares should be scaled down to fit cash
        # Available cash = 1000, target_notional = 1000
        # If we buy 100 shares at 10: gross = 1000.
        # Cost: commission = 100 * 0.01 = 1.0 -> min_comm = 2.0. Slippage = 1000 * 10 / 10000 = 1.0. Total cost = 3.0.
        # Total required = 1003.0 > 1000.0.
        # Max shares should be slightly less than 100.
        net_shares = calculate_max_shares(1000.0, 10.0, 1000.0, params)
        self.assertLess(net_shares, 100.0)
        
        # Verify that cash is not exceeded
        gross_notional = net_shares * 10.0
        costs = calculate_transaction_costs(gross_notional, net_shares, params)
        self.assertLessEqual(gross_notional + costs["total_cost"], 1000.0)

    def test_strategy_backtest_with_and_without_costs(self):
        # Create synthetic historical data for 30 days
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        
        # We need two assets to test rotation / selection
        asset_data = {
            "A": pd.DataFrame({
                "Open": [10.0 + 0.5 * i for i in range(30)],
                "High": [10.2 + 0.5 * i for i in range(30)],
                "Low": [9.8 + 0.5 * i for i in range(30)],
                "Close": [10.1 + 0.5 * i for i in range(30)],
                "Volume": [1000]*30
            }, index=dates),
            "B": pd.DataFrame({
                "Open": [10.0 - 0.5 * i for i in range(30)],
                "High": [10.2 - 0.5 * i for i in range(30)],
                "Low": [9.8 - 0.5 * i for i in range(30)],
                "Close": [9.9 - 0.5 * i for i in range(30)],
                "Volume": [1000]*30
            }, index=dates)
        }
        
        # Add metadata attrs
        for df in asset_data.values():
            df.attrs["daily_ohlcv"] = df.copy()

        base_params = {
            "strategy_name": "relative_strength_rotation",
            "rs_lookback_days": 2,
            "rs_vol_lookback_days": 2,
            "rs_trend_sma_days": 2,
            "rs_top_n": 1,
            "rs_rebalance_frequency": "daily",
            "rs_use_market_regime": False,
            "rs_min_price": 1.0,
            "rs_max_atr_pct": 100.0
        }

        # 1) Zero-cost run
        gross_metrics = _run_relative_strength_rotation_backtest(asset_data, base_params)
        self.assertIn("_trade_log", gross_metrics)
        self.assertIn("_equity_curve", gross_metrics)
        
        gross_trades = gross_metrics["_trade_log"]
        self.assertGreater(len(gross_trades), 0)
        
        # Every trade in zero-cost run should have zero transaction cost
        for t in gross_trades:
            self.assertEqual(t.get("transaction_cost", 0.0), 0.0)

        # 2) Costed run
        cost_params = base_params.copy()
        cost_params.update({
            "commission_per_share": 0.05,
            "slippage_bps": 50.0,
            "min_commission": 1.0
        })
        net_metrics = _run_relative_strength_rotation_backtest(asset_data, cost_params)
        net_trades = net_metrics["_trade_log"]
        
        # Trade count should be identical (signal generation is not changed by costs)
        self.assertEqual(len(net_trades), len(gross_trades))
        
        # Verify that nonzero costs were recorded and deducted
        for t in net_trades:
            self.assertGreater(t["transaction_cost"], 0.0)
            self.assertEqual(t["net_cash_effect"], t["Net PnL"])

        # Dynamic equity curves should differ
        gross_final_eq = gross_metrics["_equity_curve"][-1][1]
        net_final_eq = net_metrics["_equity_curve"][-1][1]
        self.assertLess(net_final_eq, gross_final_eq)

if __name__ == "__main__":
    unittest.main()
