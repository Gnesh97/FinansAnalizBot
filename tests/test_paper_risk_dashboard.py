import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.paper_risk_dashboard import build_paper_risk_dashboard, write_paper_risk_dashboard


def _write_fixture_reports(latest: Path, *, include_intraday: bool = True, max_positions: int = 5) -> None:
    pd.DataFrame(
        [
            {
                "date": "fixture-fill-date-1",
                "strategy_name": "fixture_strategy",
                "symbol": "AAA",
                "action": "BUY",
                "signal_date": "fixture-signal-date-1",
                "assumed_fill_price": 10,
                "fill_status": "PAPER_FILLED",
                "shares": 10,
                "notional": 100,
            },
            {
                "date": "fixture-fill-date-2",
                "strategy_name": "fixture_strategy",
                "symbol": "BBB",
                "action": "BUY",
                "signal_date": "fixture-signal-date-2",
                "assumed_fill_price": 20,
                "fill_status": "PAPER_FILLED",
                "shares": 5,
                "notional": 100,
            },
        ]
    ).to_csv(latest / "paper_trade_log.csv", index=False)
    monitor_rows = [
        {"section": "summary", "metric": "open_paper_positions", "value": 2},
        {"section": "summary", "metric": "total_open_notional", "value": 200.0},
        {"section": "summary", "metric": "total_unrealized_pnl", "value": 10.0},
        {"section": "summary", "metric": "total_unrealized_pnl_pct", "value": 5.0},
        {"section": "open_position", "symbol": "AAA", "strategy_name": "fixture_strategy", "notional": 100.0},
        {"section": "open_position", "symbol": "BBB", "strategy_name": "fixture_strategy", "notional": 100.0},
        {"section": "risk_check", "metric": "missing_mark_price_symbols", "value": "none"},
        {"section": "risk_check", "metric": "stale_mark_price_symbols", "value": "none"},
    ]
    pd.DataFrame(monitor_rows).to_csv(latest / "paper_portfolio_monitor.csv", index=False)
    pd.DataFrame(
        [
            {
                "symbol": "CCC",
                "action": "BUY",
                "status": "PENDING",
                "user_decision": "ACCEPT",
                "paper_position_notional": 100.0,
            },
            {
                "symbol": "AAA",
                "action": "SELL",
                "status": "PENDING",
                "user_decision": "ACCEPT",
                "paper_position_notional": 100.0,
            },
            {
                "symbol": "DDD",
                "action": "BUY",
                "status": "PENDING",
                "user_decision": "HOLD_REVIEW",
                "paper_position_notional": 100.0,
            },
            {
                "symbol": "AAA",
                "action": "BUY",
                "status": "BLOCKED",
                "user_decision": "REVIEW_REQUIRED",
                "paper_position_notional": 100.0,
                "reason": "fixture_reason;buy_already_open",
            },
        ]
    ).to_csv(latest / "paper_pending_orders.csv", index=False)
    pd.DataFrame(
        [{"symbol": "DDD", "action": "BUY", "user_decision": "HOLD_REVIEW"}]
    ).to_csv(latest / "paper_decision_inbox.csv", index=False)
    pd.DataFrame(
        [{"max_positions": max_positions, "paper_max_new_positions_per_day": 3}]
    ).to_csv(latest / "paper_signal_report.csv", index=False)
    (latest / "paper_ops_dashboard.json").write_text(
        json.dumps(
            {
                "safety_gates": {
                    "broker_submit_enabled": "false",
                    "live_trading_enabled": "false",
                    "short_trading_enabled": "false",
                    "submit_order_called": "false",
                    "post_request_used": "false",
                }
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame(columns=["symbol", "action", "user_decision"]).to_csv(latest / "paper_order_review.csv", index=False)
    if include_intraday:
        pd.DataFrame([{"section": "summary", "metric": "state_mutated", "value": False}]).to_csv(
            latest / "intraday_paper_monitor.csv",
            index=False,
        )


class PaperRiskDashboardTest(unittest.TestCase):
    def test_calculates_current_open_exposure_from_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_fixture_reports(latest)

            dashboard = build_paper_risk_dashboard(latest)

            exposure = dashboard["current_exposure"]
            self.assertEqual(exposure["open_paper_positions"], 2)
            self.assertEqual(exposure["total_open_notional"], 200.0)
            self.assertEqual(exposure["gross_long_exposure"], 200.0)
            self.assertEqual(exposure["average_position_notional"], 100.0)

    def test_nets_buy_and_sell_pending_into_projected_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_fixture_reports(latest)

            pending = build_paper_risk_dashboard(latest)["pending_impact"]

            self.assertEqual(pending["accepted_pending_buy_count"], 1)
            self.assertEqual(pending["accepted_pending_sell_exit_count"], 1)
            self.assertEqual(pending["projected_position_count_change"], 0)
            self.assertEqual(pending["projected_open_positions_after_pending_fills"], 2)
            self.assertEqual(pending["blocked_duplicate_buy_symbols"], ["AAA"])

    def test_blocks_new_buy_when_projected_positions_exceed_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_fixture_reports(latest, max_positions=1)

            risk = build_paper_risk_dashboard(latest)["risk_limits"]

            self.assertEqual(risk["risk_status"], "BLOCK_NEW_BUY_REVIEW_REQUIRED")
            self.assertTrue(risk["projected_open_positions_exceeds_limit"])

    def test_flags_concentration_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_fixture_reports(latest)
            monitor = pd.read_csv(latest / "paper_portfolio_monitor.csv")
            monitor.loc[monitor["symbol"].eq("AAA"), "notional"] = 190.0
            monitor.loc[monitor["symbol"].eq("BBB"), "notional"] = 10.0
            monitor.to_csv(latest / "paper_portfolio_monitor.csv", index=False)

            risk = build_paper_risk_dashboard(latest)["risk_limits"]

            self.assertEqual(risk["risk_status"], "WARNING")
            self.assertTrue(risk["single_position_concentration_warning"])

    def test_handles_missing_intraday_monitor_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_fixture_reports(latest, include_intraday=False)

            result = write_paper_risk_dashboard(latest)

            self.assertTrue(Path(result["csv_path"]).exists())
            self.assertTrue(Path(result["md_path"]).exists())
            self.assertTrue(Path(result["json_path"]).exists())

    def test_does_not_mutate_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_fixture_reports(latest)
            watched = [
                latest / "paper_trade_log.csv",
                latest / "paper_pending_orders.csv",
                latest / "paper_order_review.csv",
            ]
            before = {path: path.read_text(encoding="utf-8") for path in watched}

            write_paper_risk_dashboard(latest)

            after = {path: path.read_text(encoding="utf-8") for path in watched}
            self.assertEqual(before, after)

    def test_safety_flags_remain_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_fixture_reports(latest)

            safety = build_paper_risk_dashboard(latest)["safety"]

            self.assertFalse(safety["broker_submit_enabled"])
            self.assertFalse(safety["live_trading_enabled"])
            self.assertFalse(safety["short_trading_enabled"])
            self.assertFalse(safety["submit_order_called"])
            self.assertFalse(safety["post_request_used"])
            self.assertFalse(safety["state_mutated"])

    def test_fixture_uses_marked_fixture_dates_and_symbols_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_fixture_reports(latest)

            text = (latest / "paper_trade_log.csv").read_text(encoding="utf-8")

            self.assertIn("fixture-fill-date", text)
            self.assertIn("fixture-signal-date", text)


if __name__ == "__main__":
    unittest.main()
