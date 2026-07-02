import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.execution_costs import (
    CommissionModel,
    ExecutionCostConfig,
    SlippageModel,
    apply_execution_cost_to_trade,
    build_execution_cost_preview,
    calculate_execution_cost,
    write_execution_cost_preview,
)
from testdata.execution_engine import build_order_intents
from testdata.paper_state_manager import read_trade_log
from testdata.runner import (
    _apply_previous_pending_next_open_orders,
    _paper_signal_columns,
    _write_paper_pending_orders,
)


FIXTURE_STRATEGY = "fixture_strategy"


def _trade(action="BUY", price=10.0, shares=2.0, symbol="FIXA"):
    return {
        "date": "fixture-fill-date",
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": symbol,
        "action": action,
        "signal_date": "fixture-signal-date",
        "assumed_fill_price": price,
        "fill_status": "PAPER_FILLED",
        "shares": shares,
        "notional": price * shares,
    }


class ExecutionCostsTest(unittest.TestCase):
    def test_zero_cost_config_returns_total_cost_zero(self):
        result = calculate_execution_cost("BUY", 10.0, 2.0)

        self.assertEqual(result.total_cost, 0.0)
        self.assertTrue(result.default_zero_cost)

    def test_zero_cost_config_leaves_adjusted_fill_price_unchanged(self):
        result = calculate_execution_cost("SELL", 10.0, 2.0)

        self.assertEqual(result.adjusted_fill_price, 10.0)

    def test_buy_slippage_bps_increases_adjusted_fill_price(self):
        config = ExecutionCostConfig(slippage=SlippageModel(slippage_bps=50.0))

        result = calculate_execution_cost("BUY", 10.0, 1.0, config)

        self.assertGreater(result.adjusted_fill_price, 10.0)
        self.assertAlmostEqual(result.slippage_cost, 0.05)

    def test_sell_slippage_bps_decreases_adjusted_fill_price(self):
        config = ExecutionCostConfig(slippage=SlippageModel(slippage_bps=50.0))

        result = calculate_execution_cost("SELL", 10.0, 1.0, config)

        self.assertLess(result.adjusted_fill_price, 10.0)
        self.assertAlmostEqual(result.slippage_cost, 0.05)

    def test_commission_per_order_is_included_in_total_cost(self):
        config = ExecutionCostConfig(commission=CommissionModel(commission_per_order=1.25))

        result = calculate_execution_cost("BUY", 10.0, 2.0, config)

        self.assertAlmostEqual(result.commission_cost, 1.25)
        self.assertAlmostEqual(result.total_cost, 1.25)

    def test_commission_bps_is_included_in_total_cost(self):
        config = ExecutionCostConfig(commission=CommissionModel(commission_bps=10.0))

        result = calculate_execution_cost("BUY", 100.0, 2.0, config)

        self.assertAlmostEqual(result.commission_cost, 0.2)
        self.assertAlmostEqual(result.total_cost, 0.2)

    def test_non_zero_cost_reduces_synthetic_realized_pnl(self):
        config = ExecutionCostConfig(
            commission=CommissionModel(commission_per_order=1.0),
            slippage=SlippageModel(slippage_bps=100.0),
        )
        buy = apply_execution_cost_to_trade(_trade("BUY", 10.0, 1.0), config)
        sell = apply_execution_cost_to_trade(_trade("SELL", 12.0, 1.0), config)
        original_pnl = 12.0 - 10.0
        cost_adjusted_pnl = (
            sell["adjusted_fill_price"]
            - buy["adjusted_fill_price"]
            - buy["commission_cost"]
            - sell["commission_cost"]
        )

        self.assertLess(cost_adjusted_pnl, original_pnl)

    def test_preview_zero_default_reads_filled_trades(self):
        result = build_execution_cost_preview(pd.DataFrame([_trade("BUY", 10.0, 2.0)]))

        summary = result["summary"]
        preview = result["preview"]
        self.assertEqual(summary["filled_trade_rows_checked"], 1)
        self.assertEqual(summary["total_execution_cost"], 0.0)
        self.assertTrue(bool(preview.iloc[0]["default_zero_cost"]))

    def test_cost_preview_does_not_mutate_paper_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame([_trade("BUY", 10.0, 2.0)]).to_csv(latest / "paper_trade_log.csv", index=False)
            pd.DataFrame([{"symbol": "FIXP", "status": "PENDING"}]).to_csv(
                latest / "paper_pending_orders.csv",
                index=False,
            )
            pd.DataFrame([{"symbol": "FIXR", "user_decision": "HOLD_REVIEW"}]).to_csv(
                latest / "paper_order_review.csv",
                index=False,
            )
            watched = [latest / "paper_trade_log.csv", latest / "paper_pending_orders.csv", latest / "paper_order_review.csv"]
            before = {path.name: path.read_text(encoding="utf-8") for path in watched}

            write_execution_cost_preview(latest)

            after = {path.name: path.read_text(encoding="utf-8") for path in watched}
            self.assertEqual(before, after)

    def test_existing_duplicate_buy_block_behavior_remains_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame([_trade("BUY", 10.0, 1.0)]).to_csv(latest / "paper_trade_log.csv", index=False)
            report = pd.DataFrame(
                [
                    {
                        "run_datetime": "fixture-run",
                        "signal_date": "2026-01-02 13:50:00-04:00",
                        "strategy_name": FIXTURE_STRATEGY,
                        "symbol": "FIXA",
                        "action": "BUY",
                        "paper_actionable": True,
                        "user_decision": "ACCEPT",
                        "fill_status": "PENDING_NEXT_OPEN",
                        "reason": "fixture_latest_entry_signal",
                        "liquidity_guard_pass": True,
                    }
                ],
                columns=_paper_signal_columns(),
            )

            _write_paper_pending_orders(report, latest / "paper_pending_orders.csv", trade_log_path=latest / "paper_trade_log.csv")

            pending = pd.read_csv(latest / "paper_pending_orders.csv")
            intents, _ = build_order_intents(pending)
            self.assertEqual(pending.iloc[0]["status"], "BLOCKED")
            self.assertIn("buy_already_open", pending.iloc[0]["reason"])
            self.assertTrue(intents.empty)

    def test_existing_sell_quantity_matching_behavior_remains_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame([_trade("BUY", 10.0, 3.0)]).to_csv(latest / "paper_trade_log.csv", index=False)
            pending = pd.DataFrame(
                [
                    {
                        "run_datetime": "fixture-run",
                        "paper_strategy_mode": "main",
                        "paper_portfolio_state": "existing",
                        "strategy_name": FIXTURE_STRATEGY,
                        "signal_date": "2026-01-02 13:50:00-04:00",
                        "symbol": "FIXA",
                        "action": "SELL",
                        "fill_status": "PENDING_NEXT_OPEN",
                        "user_decision": "ACCEPT",
                        "reason": "fixture_exit",
                        "paper_actionable": True,
                        "paper_position_notional": 999.0,
                        "entry_price_reference": 10.0,
                    }
                ]
            )
            asset_data = {
                "FIXA": pd.DataFrame(
                    {"Open": [10.0, 20.0]},
                    index=pd.to_datetime(["2026-01-02 13:50:00-04:00", "2026-01-05 09:30:00-04:00"]),
                )
            }

            _apply_previous_pending_next_open_orders(
                pd.DataFrame(columns=_paper_signal_columns()),
                pending,
                pd.DataFrame(),
                latest / "paper_trade_log.csv",
                asset_data,
            )

            log = read_trade_log(latest)
            sell = log[log["action"].astype(str).str.upper().eq("SELL")].iloc[0]
            self.assertEqual(float(sell["shares"]), 3.0)

    def test_safety_flags_remain_false(self):
        summary = build_execution_cost_preview(pd.DataFrame())["summary"]

        self.assertFalse(summary["state_mutated"])
        self.assertFalse(summary["broker_submit_enabled"])
        self.assertFalse(summary["live_trading_enabled"])
        self.assertFalse(summary["short_trading_enabled"])
        self.assertFalse(summary["submit_order_called"])
        self.assertFalse(summary["post_request_used"])

    def test_no_hardcoded_production_dates_or_symbols_in_cost_module(self):
        source = Path("testdata/execution_costs.py").read_text(encoding="utf-8")

        self.assertNotIn("2026-07", source)
        self.assertNotIn("EQIX", source)
        self.assertNotIn("NUE", source)
        self.assertNotIn("UNH", source)
        self.assertNotIn("CVS", source)


if __name__ == "__main__":
    unittest.main()
