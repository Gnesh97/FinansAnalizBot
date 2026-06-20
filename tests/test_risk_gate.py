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
        self.assertIn("duplicate_symbol_order_same_run", report.iloc[1]["warning"])

    def test_preview_duplicate_preview_blocks(self):
        intents = pd.DataFrame(
            [
                {
                    "order_intent_id": "id1",
                    "idempotency_phase": "PREVIEW",
                    "idempotency_key": "same",
                    "execution_idempotency_key": "PREVIEW:same",
                    "symbol": "AAPL",
                    "strategy_name": "s",
                    "side": "buy",
                    "notional": 100,
                    "liquidity_guard_pass": True,
                },
                {
                    "order_intent_id": "id2",
                    "idempotency_phase": "PREVIEW",
                    "idempotency_key": "same",
                    "execution_idempotency_key": "PREVIEW:same",
                    "symbol": "MSFT",
                    "strategy_name": "s",
                    "side": "buy",
                    "notional": 100,
                    "liquidity_guard_pass": True,
                },
            ]
        )
        report = evaluate_order_risk(intents)
        self.assertIn("duplicate_execution_idempotency_key", report.iloc[1]["block_reason"])

    def test_preview_duplicate_future_submit_not_duplicate(self):
        intents = pd.DataFrame(
            [
                {
                    "order_intent_id": "id1",
                    "idempotency_phase": "PREVIEW",
                    "idempotency_key": "same",
                    "execution_idempotency_key": "PREVIEW:same",
                    "symbol": "AAPL",
                    "strategy_name": "s",
                    "side": "buy",
                    "notional": 100,
                    "liquidity_guard_pass": True,
                },
                {
                    "order_intent_id": "id1",
                    "idempotency_phase": "SUBMIT",
                    "idempotency_key": "same",
                    "execution_idempotency_key": "SUBMIT:same",
                    "symbol": "MSFT",
                    "strategy_name": "s",
                    "side": "buy",
                    "notional": 100,
                    "liquidity_guard_pass": True,
                },
            ]
        )
        report = evaluate_order_risk(intents)
        self.assertTrue(bool(report.iloc[1]["risk_pass"]))

    def test_liquidity_fail_blocks(self):
        intents = pd.DataFrame(
            [{"symbol": "AAPL", "strategy_name": "s", "side": "buy", "notional": 100, "liquidity_guard_pass": False}]
        )
        report = evaluate_order_risk(intents)
        self.assertFalse(bool(report.iloc[0]["risk_pass"]))
        self.assertIn("liquidity_guard_failed", report.iloc[0]["block_reason"])

    def test_valid_order_passes(self):
        intents = pd.DataFrame(
            [
                {
                    "order_intent_id": "id1",
                    "idempotency_phase": "PREVIEW",
                    "idempotency_key": "unique",
                    "execution_idempotency_key": "PREVIEW:unique",
                    "symbol": "AAPL",
                    "strategy_name": "s",
                    "side": "buy",
                    "notional": 100,
                    "liquidity_guard_pass": True,
                }
            ]
        )
        report = evaluate_order_risk(intents)
        self.assertTrue(bool(report.iloc[0]["risk_pass"]))
        self.assertEqual(report.iloc[0]["side"], "BUY")

    def test_short_sell_blocks_when_short_disabled(self):
        intents = pd.DataFrame(
            [{"symbol": "AAPL", "strategy_name": "s", "side": "SHORT_SELL", "notional": 100, "liquidity_guard_pass": True}]
        )
        report = evaluate_order_risk(intents)
        self.assertFalse(bool(report.iloc[0]["risk_pass"]))
        self.assertIn("short_trading_disabled", report.iloc[0]["block_reason"])

    def test_buy_to_cover_without_short_position_blocks(self):
        intents = pd.DataFrame(
            [{"symbol": "AAPL", "strategy_name": "s", "side": "BUY_TO_COVER", "notional": 100, "liquidity_guard_pass": True}]
        )
        report = evaluate_order_risk(intents, short_trading_enabled=True)
        self.assertFalse(bool(report.iloc[0]["risk_pass"]))
        self.assertIn("buy_to_cover_without_short_position", report.iloc[0]["block_reason"])

    def test_sell_long_exit_passes(self):
        intents = pd.DataFrame(
            [{"symbol": "AAPL", "strategy_name": "s", "side": "SELL", "notional": 100, "liquidity_guard_pass": True}]
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
