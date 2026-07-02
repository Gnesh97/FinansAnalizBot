import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.paper_state_manager import read_trade_log
from testdata.execution_engine import build_order_intents
from testdata.runner import (
    _apply_paper_order_review,
    _apply_previous_pending_next_open_orders,
    _next_open_for_signal,
    _paper_signal_columns,
    _write_paper_pending_orders,
)


def _previous_pending_order():
    return pd.DataFrame(
        [
            {
                "run_datetime": "2026-06-19 16:10:00",
                "paper_strategy_mode": "main",
                "paper_portfolio_state": "existing",
                "strategy_name": "rs_m126_top30_sma100_no_regime",
                "signal_date": "2026-06-19",
                "symbol": "AAPL",
                "action": "BUY",
                "fill_status": "PENDING_NEXT_OPEN",
                "user_decision": "ACCEPT",
                "reason": "missing_next_open",
                "paper_actionable": True,
                "paper_position_notional": 1000.0,
                "paper_capital_assumption": 25000.0,
                "entry_price_reference": 100.0,
                "liquidity_guard_pass": True,
            }
        ]
    )


def _previous_pending_sell_order():
    order = _previous_pending_order()
    order.loc[0, "action"] = "SELL"
    order.loc[0, "paper_position_notional"] = 1000.0
    return order


def _asset_data_with_next_open():
    return {
        "AAPL": pd.DataFrame(
            {"Open": [100.0, 125.0]},
            index=pd.to_datetime(["2026-06-19", "2026-06-22"]),
        )
    }


class PaperPendingFillTest(unittest.TestCase):
    def test_next_open_for_signal_skips_signal_bar_open(self):
        asset_data = {
            "AAPL": pd.DataFrame(
                {"Open": [100.0, 125.0, 130.0]},
                index=pd.to_datetime(["2026-06-19", "2026-06-22", "2026-06-23"]),
            )
        }
        self.assertEqual(float(_next_open_for_signal(asset_data, "AAPL", "2026-06-19")), 125.0)

    def test_next_open_for_signal_returns_na_when_no_future_bar_exists(self):
        asset_data = {
            "AAPL": pd.DataFrame(
                {"Open": [100.0]},
                index=pd.to_datetime(["2026-06-19"]),
            )
        }
        self.assertTrue(pd.isna(_next_open_for_signal(asset_data, "AAPL", "2026-06-19")))

    def test_latest_reject_prevents_previous_pending_next_open_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = pd.DataFrame(
                [
                    {
                        "symbol": "AAPL",
                        "strategy_name": "rs_m126_top30_sma100_no_regime",
                        "action": "BUY",
                        "user_decision": "REJECT",
                        "notes": "cancel before next open",
                    }
                ]
            )
            output = _apply_previous_pending_next_open_orders(
                pd.DataFrame(columns=_paper_signal_columns()),
                _previous_pending_order(),
                review,
                Path(tmp) / "paper_trade_log.csv",
                _asset_data_with_next_open(),
            )
            self.assertEqual(output.iloc[0]["user_decision"], "REJECT")
            self.assertEqual(output.iloc[0]["fill_status"], "REJECTED")
            log = read_trade_log(Path(tmp))
            self.assertEqual(len(log), 1)
            self.assertEqual(log.iloc[0]["fill_status"], "REJECTED")
            self.assertNotIn("PAPER_FILLED", set(log["fill_status"].astype(str)))

    def test_latest_accept_fills_previous_pending_next_open_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = _apply_previous_pending_next_open_orders(
                pd.DataFrame(columns=_paper_signal_columns()),
                _previous_pending_order(),
                pd.DataFrame(),
                Path(tmp) / "paper_trade_log.csv",
                _asset_data_with_next_open(),
            )
            self.assertEqual(output.iloc[0]["fill_status"], "PAPER_FILLED")
            log = read_trade_log(Path(tmp))
            self.assertEqual(len(log), 1)
            self.assertEqual(log.iloc[0]["fill_status"], "PAPER_FILLED")
            self.assertEqual(float(log.iloc[0]["assumed_fill_price"]), 125.0)

    def test_pending_sell_fill_closes_existing_open_shares_not_notional_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            pd.DataFrame(
                [
                    {
                        "date": "fixture-date-1",
                        "strategy_name": "rs_m126_top30_sma100_no_regime",
                        "symbol": "AAPL",
                        "action": "BUY",
                        "signal_date": "fixture-signal-date-1",
                        "assumed_fill_price": 100.0,
                        "fill_status": "PAPER_FILLED",
                        "shares": 10.0,
                        "notional": 1000.0,
                    }
                ]
            ).to_csv(Path(tmp) / "paper_trade_log.csv", index=False)

            _apply_previous_pending_next_open_orders(
                pd.DataFrame(columns=_paper_signal_columns()),
                _previous_pending_sell_order(),
                pd.DataFrame(),
                Path(tmp) / "paper_trade_log.csv",
                _asset_data_with_next_open(),
            )

            log = read_trade_log(Path(tmp))
            sell = log[log["action"].astype(str).str.upper().eq("SELL")].iloc[0]
            self.assertEqual(float(sell["assumed_fill_price"]), 125.0)
            self.assertEqual(float(sell["shares"]), 10.0)

    def test_new_accepted_sell_fill_closes_existing_open_shares_not_notional_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            pd.DataFrame(
                [
                    {
                        "date": "fixture-date-1",
                        "strategy_name": "rs_m126_top30_sma100_no_regime",
                        "symbol": "AAPL",
                        "action": "BUY",
                        "signal_date": "fixture-signal-date-1",
                        "assumed_fill_price": 100.0,
                        "fill_status": "PAPER_FILLED",
                        "shares": 10.0,
                        "notional": 1000.0,
                    }
                ]
            ).to_csv(Path(tmp) / "paper_trade_log.csv", index=False)
            paper_report = _previous_pending_sell_order()
            paper_report.loc[0, "fill_status"] = "NOT_APPLICABLE"
            review = pd.DataFrame(
                [
                    {
                        "strategy_name": "rs_m126_top30_sma100_no_regime",
                        "symbol": "AAPL",
                        "action": "SELL",
                        "user_decision": "ACCEPT",
                    }
                ]
            )

            _apply_paper_order_review(
                paper_report,
                review,
                Path(tmp) / "paper_trade_log.csv",
                _asset_data_with_next_open(),
            )

            log = read_trade_log(Path(tmp))
            sell = log[log["action"].astype(str).str.upper().eq("SELL")].iloc[0]
            self.assertEqual(float(sell["shares"]), 10.0)

    def test_pending_writer_blocks_accepted_duplicate_buy_already_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame(
                [
                    {
                        "date": "fixture-date-1",
                        "strategy_name": "fixture_strategy",
                        "symbol": "FIXA",
                        "action": "BUY",
                        "signal_date": "fixture-signal-date-1",
                        "assumed_fill_price": 10.0,
                        "fill_status": "PAPER_FILLED",
                        "shares": 10.0,
                    }
                ]
            ).to_csv(latest / "paper_trade_log.csv", index=False)
            report = pd.DataFrame(
                [
                    {
                        "run_datetime": "fixture-run",
                        "signal_date": "fixture-signal-date-2",
                        "strategy_name": "fixture_strategy",
                        "symbol": "FIXA",
                        "action": "BUY",
                        "paper_actionable": True,
                        "user_decision": "ACCEPT",
                        "fill_status": "PENDING_NEXT_OPEN",
                        "reason": "fixture_latest_entry_signal",
                        "liquidity_guard_pass": True,
                    },
                    {
                        "run_datetime": "fixture-run",
                        "signal_date": "fixture-signal-date-2",
                        "strategy_name": "fixture_strategy",
                        "symbol": "FIXH",
                        "action": "BUY",
                        "paper_actionable": True,
                        "user_decision": "HOLD_REVIEW",
                        "fill_status": "PENDING_NEXT_OPEN",
                        "reason": "fixture_latest_entry_signal",
                        "liquidity_guard_pass": True,
                    },
                ],
                columns=_paper_signal_columns(),
            )

            _write_paper_pending_orders(report, latest / "paper_pending_orders.csv", trade_log_path=latest / "paper_trade_log.csv")

            pending = pd.read_csv(latest / "paper_pending_orders.csv")
            duplicate = pending[pending["symbol"].astype(str).eq("FIXA")].iloc[0]
            hold = pending[pending["symbol"].astype(str).eq("FIXH")].iloc[0]
            intents, _ = build_order_intents(pending)
            self.assertEqual(duplicate["status"], "BLOCKED")
            self.assertEqual(duplicate["user_decision"], "REVIEW_REQUIRED")
            self.assertIn("buy_already_open", duplicate["reason"])
            self.assertEqual(hold["user_decision"], "REVIEW_REQUIRED")
            self.assertTrue(intents.empty)

    def test_sell_exit_for_open_long_remains_executable_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame(
                [
                    {
                        "date": "fixture-date-1",
                        "strategy_name": "fixture_strategy",
                        "symbol": "FIXA",
                        "action": "BUY",
                        "signal_date": "fixture-signal-date-1",
                        "assumed_fill_price": 10.0,
                        "fill_status": "PAPER_FILLED",
                        "shares": 10.0,
                    }
                ]
            ).to_csv(latest / "paper_trade_log.csv", index=False)
            report = pd.DataFrame(
                [
                    {
                        "run_datetime": "fixture-run",
                        "signal_date": "fixture-signal-date-2",
                        "strategy_name": "fixture_strategy",
                        "symbol": "FIXA",
                        "action": "SELL",
                        "paper_actionable": True,
                        "user_decision": "ACCEPT",
                        "fill_status": "PENDING_NEXT_OPEN",
                        "reason": "fixture_latest_exit_signal",
                        "liquidity_guard_pass": True,
                    }
                ],
                columns=_paper_signal_columns(),
            )

            _write_paper_pending_orders(report, latest / "paper_pending_orders.csv", trade_log_path=latest / "paper_trade_log.csv")

            pending = pd.read_csv(latest / "paper_pending_orders.csv")
            intents, _ = build_order_intents(pending)
            self.assertEqual(pending.iloc[0]["status"], "PENDING")
            self.assertEqual(pending.iloc[0]["user_decision"], "ACCEPT")
            self.assertEqual(len(intents), 1)
            self.assertEqual(intents.iloc[0]["side"], "SELL")


if __name__ == "__main__":
    unittest.main()
