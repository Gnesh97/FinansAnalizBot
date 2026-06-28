import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.paper_portfolio_monitor import build_paper_portfolio_monitor, write_paper_portfolio_monitor
from testdata.paper_state_manager import write_pending_orders, write_trade_log


FIXTURE_START = "2026-01-01"
FIXTURE_MARK = "2026-01-02"


def _trade(symbol, price=10.0, shares=1.0):
    return {
        "date": FIXTURE_START,
        "strategy_name": "fixture_strategy",
        "symbol": symbol,
        "action": "BUY",
        "signal_date": FIXTURE_START,
        "assumed_fill_price": price,
        "fill_status": "PAPER_FILLED",
        "shares": shares,
        "notional": price * shares,
    }


def _pending(symbol, action):
    return {
        "created_at": FIXTURE_START,
        "signal_date": FIXTURE_START,
        "strategy_name": "fixture_strategy",
        "symbol": symbol,
        "action": action,
        "status": "PENDING",
        "user_decision": "ACCEPT",
        "expected_next_open_date": FIXTURE_MARK,
        "liquidity_guard_pass": True,
        "reason": "fixture_pending_order",
    }


class PaperPortfolioMonitorTest(unittest.TestCase):
    def test_monitor_handles_current_scale_filled_trades(self):
        trades = pd.DataFrame([_trade(f"SYM{i:02d}", price=10.0 + i, shares=1.0) for i in range(20)])
        asset_data = {
            f"SYM{i:02d}": pd.DataFrame(
                {"Close": [11.0 + i]},
                index=pd.to_datetime([FIXTURE_MARK]),
            )
            for i in range(20)
        }
        monitor, summary, checks, _, _ = build_paper_portfolio_monitor(
            trades,
            pd.DataFrame(),
            asset_data=asset_data,
        )
        self.assertEqual(summary["total_filled_trades"], 20)
        self.assertEqual(summary["open_paper_positions"], 20)
        self.assertEqual(checks["missing_mark_price_symbols"], "none")
        self.assertFalse(monitor.empty)

    def test_risk_checks_flag_pending_exposure_and_validate_exit(self):
        trades = pd.DataFrame([_trade("AAA"), _trade("BBB")])
        pending = pd.DataFrame([
            _pending("AAA", "BUY"),
            _pending("BBB", "SELL"),
            _pending("ZZZ", "SELL"),
        ])
        asset_data = {
            "AAA": pd.DataFrame({"Close": [12.0]}, index=pd.to_datetime([FIXTURE_MARK])),
        }
        _, summary, checks, _, _ = build_paper_portfolio_monitor(
            trades,
            pending,
            asset_data=asset_data,
        )
        self.assertEqual(summary["pending_buy_count"], 1)
        self.assertEqual(summary["pending_sell_exit_count"], 2)
        self.assertEqual(checks["pending_buy_already_open_symbols"], "AAA")
        self.assertEqual(checks["pending_sell_without_open_position_symbols"], "ZZZ")
        self.assertEqual(checks["sell_exit_symbols_validated"], "BBB")
        self.assertEqual(checks["missing_mark_price_symbols"], "BBB")

    def test_report_writer_creates_csv_and_markdown_without_state_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp)
            write_trade_log(reports_dir, pd.DataFrame([_trade("AAA")]))
            write_pending_orders(reports_dir, pd.DataFrame([_pending("AAA", "BUY")]))
            result = write_paper_portfolio_monitor(
                reports_dir,
                reports_dir / "paper_portfolio_monitor.csv",
                reports_dir / "paper_portfolio_monitor.md",
                asset_data={
                    "AAA": pd.DataFrame({"Close": [12.0]}, index=pd.to_datetime([FIXTURE_MARK])),
                },
            )
            self.assertTrue((reports_dir / "paper_portfolio_monitor.csv").exists())
            self.assertTrue((reports_dir / "paper_portfolio_monitor.md").exists())
            self.assertEqual(result["checks"]["broker_submit_enabled"], False)
            self.assertEqual(result["checks"]["live_trading_enabled"], False)
            self.assertEqual(result["checks"]["post_request_used"], False)


if __name__ == "__main__":
    unittest.main()
