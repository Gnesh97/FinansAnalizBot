import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.closed_trade_report import build_closed_trade_report, write_closed_trade_report


def _trade(
    symbol,
    action,
    shares,
    price,
    date,
    *,
    strategy="fixture_strategy",
    signal_date="fixture-signal-date",
):
    return {
        "date": date,
        "strategy_name": strategy,
        "symbol": symbol,
        "action": action,
        "signal_date": signal_date,
        "assumed_fill_price": price,
        "fill_status": "PAPER_FILLED",
        "shares": shares,
        "notional": shares * price,
        "reason": "fixture_reason",
    }


def _monitor(symbols):
    rows = [
        {"section": "open_position", "symbol": symbol, "strategy_name": "fixture_strategy"}
        for symbol in symbols
    ]
    return pd.DataFrame(rows)


class ClosedTradeReportTest(unittest.TestCase):
    def test_single_buy_then_sell_creates_closed_trade_with_realized_pnl(self):
        trade_log = pd.DataFrame(
            [
                _trade("FIXA", "BUY", 2, 10, "fixture-date-1"),
                _trade("FIXA", "SELL", 2, 15, "fixture-date-2"),
            ]
        )

        result = build_closed_trade_report(trade_log, _monitor([]))
        closed = result["closed_trades"]

        self.assertEqual(len(closed), 1)
        self.assertEqual(closed.iloc[0]["realized_pnl"], 10)
        self.assertEqual(closed.iloc[0]["realized_pnl_pct"], 50)
        self.assertEqual(result["summary"]["closed_trade_count"], 1)

    def test_buy_remains_open_without_sell(self):
        trade_log = pd.DataFrame([_trade("FIXA", "BUY", 2, 10, "fixture-date-1")])

        result = build_closed_trade_report(trade_log, _monitor(["FIXA"]))

        self.assertTrue(result["closed_trades"].empty)
        self.assertEqual(len(result["open_lots"]), 1)
        self.assertEqual(result["reconciliation"]["open_position_count_match"], True)

    def test_multiple_buy_lots_then_one_sell_closes_fifo(self):
        trade_log = pd.DataFrame(
            [
                _trade("FIXA", "BUY", 1, 10, "fixture-date-1"),
                _trade("FIXA", "BUY", 1, 20, "fixture-date-2"),
                _trade("FIXA", "SELL", 2, 30, "fixture-date-3"),
            ]
        )

        closed = build_closed_trade_report(trade_log, _monitor([]))["closed_trades"]

        self.assertEqual(len(closed), 2)
        self.assertEqual(list(closed["entry_price"]), [10, 20])
        self.assertEqual(list(closed["realized_pnl"]), [20, 10])

    def test_partial_sell_closes_only_part_of_position(self):
        trade_log = pd.DataFrame(
            [
                _trade("FIXA", "BUY", 3, 10, "fixture-date-1"),
                _trade("FIXA", "SELL", 1, 12, "fixture-date-2"),
            ]
        )

        result = build_closed_trade_report(trade_log, _monitor(["FIXA"]))

        self.assertEqual(len(result["closed_trades"]), 1)
        self.assertEqual(result["closed_trades"].iloc[0]["shares"], 1)
        self.assertEqual(result["open_lots"].iloc[0]["remaining_shares"], 2)

    def test_sell_without_prior_buy_flags_unmatched_sell_no_short_created(self):
        trade_log = pd.DataFrame([_trade("FIXA", "SELL", 1, 12, "fixture-date-1")])

        result = build_closed_trade_report(trade_log, _monitor([]))

        self.assertTrue(result["closed_trades"].empty)
        self.assertTrue(result["open_lots"].empty)
        self.assertEqual(result["summary"]["unmatched_sell_count"], 1)
        self.assertEqual(result["issues"].iloc[0]["issue_type"], "unmatched_sell_issue")

    def test_tiny_floating_sell_residual_is_not_counted_unmatched(self):
        trade_log = pd.DataFrame(
            [
                _trade("FIXA", "BUY", 10, 10, "fixture-date-1"),
                _trade("FIXA", "SELL", 10 + 5e-10, 12, "fixture-date-2"),
            ]
        )

        result = build_closed_trade_report(trade_log, _monitor([]))

        self.assertEqual(len(result["closed_trades"]), 1)
        self.assertEqual(result["summary"]["unmatched_sell_count"], 0)
        self.assertTrue(result["issues"].empty)

    def test_sell_above_open_quantity_flags_residual_without_short_lot(self):
        trade_log = pd.DataFrame(
            [
                _trade("FIXA", "BUY", 10, 10, "fixture-date-1"),
                _trade("FIXA", "SELL", 11, 12, "fixture-date-2"),
            ]
        )

        result = build_closed_trade_report(trade_log, _monitor([]))

        self.assertEqual(len(result["closed_trades"]), 1)
        self.assertEqual(result["closed_trades"].iloc[0]["shares"], 10)
        self.assertEqual(result["summary"]["unmatched_sell_count"], 1)
        self.assertTrue(result["open_lots"].empty)

    def test_report_does_not_mutate_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame(
                [
                    _trade("FIXA", "BUY", 1, 10, "fixture-date-1"),
                    _trade("FIXA", "SELL", 1, 12, "fixture-date-2"),
                ]
            ).to_csv(latest / "paper_trade_log.csv", index=False)
            pd.DataFrame([{"symbol": "FIXP", "user_decision": "ACCEPT"}]).to_csv(
                latest / "paper_pending_orders.csv",
                index=False,
            )
            pd.DataFrame([{"symbol": "FIXR", "user_decision": "HOLD_REVIEW"}]).to_csv(
                latest / "paper_order_review.csv",
                index=False,
            )
            pd.DataFrame(columns=["section", "symbol"]).to_csv(latest / "paper_portfolio_monitor.csv", index=False)
            watched = [
                latest / "paper_trade_log.csv",
                latest / "paper_pending_orders.csv",
                latest / "paper_order_review.csv",
            ]
            before = {path: path.read_text(encoding="utf-8") for path in watched}

            write_closed_trade_report(latest)

            after = {path: path.read_text(encoding="utf-8") for path in watched}
            self.assertEqual(before, after)

    def test_reconciliation_detects_match_and_mismatch(self):
        trade_log = pd.DataFrame([_trade("FIXA", "BUY", 1, 10, "fixture-date-1")])

        match = build_closed_trade_report(trade_log, _monitor(["FIXA"]))["reconciliation"]
        mismatch = build_closed_trade_report(trade_log, _monitor(["FIXB", "FIXC"]))["reconciliation"]

        self.assertTrue(match["open_position_count_match"])
        self.assertFalse(mismatch["open_position_count_match"])
        self.assertEqual(mismatch["symbols_only_in_reconstructed"], ["FIXA"])
        self.assertEqual(mismatch["symbols_only_in_monitor"], ["FIXB", "FIXC"])

    def test_safety_flags_remain_false(self):
        result = build_closed_trade_report(pd.DataFrame(), pd.DataFrame())

        safety = result["safety"]
        self.assertFalse(safety["state_mutated"])
        self.assertFalse(safety["broker_submit_enabled"])
        self.assertFalse(safety["live_trading_enabled"])
        self.assertFalse(safety["short_trading_enabled"])
        self.assertFalse(safety["submit_order_called"])
        self.assertFalse(safety["post_request_used"])

    def test_fixture_dates_and_symbols_are_marked(self):
        trade_log = pd.DataFrame([_trade("FIXA", "BUY", 1, 10, "fixture-date-1")])

        text = trade_log.to_csv(index=False)

        self.assertIn("fixture-date", text)
        self.assertIn("FIXA", text)


if __name__ == "__main__":
    unittest.main()
