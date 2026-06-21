import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.paper_state_manager import read_trade_log
from testdata.runner import _apply_previous_pending_next_open_orders, _next_open_for_signal, _paper_signal_columns


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


if __name__ == "__main__":
    unittest.main()
