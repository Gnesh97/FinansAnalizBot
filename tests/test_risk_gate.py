import unittest

import pandas as pd

from testdata.risk_gate import evaluate_order_risk


class RiskGateTest(unittest.TestCase):
    def test_duplicate_symbol_blocks(self):
        intents = pd.DataFrame(
            [
                {"symbol": "AAPL", "strategy_name": "s", "side": "buy", "notional": 100, "liquidity_guard_pass": True},
                {"symbol": "AAPL", "strategy_name": "s", "side": "buy", "notional": 100, "liquidity_guard_pass": True},
            ]
        )
        report = evaluate_order_risk(intents)
        self.assertIn("duplicate_symbol_order", report.iloc[1]["block_reason"])

    def test_liquidity_fail_blocks(self):
        intents = pd.DataFrame(
            [{"symbol": "AAPL", "strategy_name": "s", "side": "buy", "notional": 100, "liquidity_guard_pass": False}]
        )
        report = evaluate_order_risk(intents)
        self.assertFalse(bool(report.iloc[0]["risk_pass"]))
        self.assertIn("liquidity_guard_failed", report.iloc[0]["block_reason"])

    def test_valid_order_passes(self):
        intents = pd.DataFrame(
            [{"symbol": "AAPL", "strategy_name": "s", "side": "buy", "notional": 100, "liquidity_guard_pass": True}]
        )
        report = evaluate_order_risk(intents)
        self.assertTrue(bool(report.iloc[0]["risk_pass"]))

    def test_max_new_orders_per_run_blocks_over_limit(self):
        intents = pd.DataFrame(
            [
                {"symbol": f"SYM{i}", "strategy_name": "s", "side": "buy", "notional": 100, "liquidity_guard_pass": True}
                for i in range(6)
            ]
        )
        report = evaluate_order_risk(intents, max_new_orders_per_run=5)
        self.assertIn("max_new_orders_per_run_exceeded", report.iloc[5]["block_reason"])


if __name__ == "__main__":
    unittest.main()
