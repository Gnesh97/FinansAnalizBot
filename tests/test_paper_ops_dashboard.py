import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.paper_ops_dashboard import build_paper_ops_dashboard, write_paper_ops_dashboard


def _write_common_reports(latest: Path) -> None:
    pd.DataFrame(
        [
            {
                "latest_daily_date": "fixture-daily-date",
                "latest_130min_date": "fixture-130min-date",
                "stale_data_warning": False,
                "status": "WARNING",
                "pending_next_open_count": 1,
            }
        ]
    ).to_csv(latest / "pipeline_health_log.csv", index=False)
    (latest / "regression_guard_report.md").write_text("- status: BASELINE_STALE\n", encoding="utf-8")
    (latest / "test_run_report.md").write_text("- status: OK\n", encoding="utf-8")
    (latest / "paper_ledger_audit_report.md").write_text(
        "- paper_ledger_status: OK\n- current_open_position_count: 1\n",
        encoding="utf-8",
    )
    (latest / "sqlite_state_report.md").write_text("- paper_open_positions_count: 1\n", encoding="utf-8")
    (latest / "execution_preview_report.md").write_text(
        "\n".join(
            [
                "- pending accepted orders count: 1",
                "- would_submit count: 0",
                "- broker_submit_enabled: false",
                "- live_trading_enabled: false",
                "- short_trading_enabled: false",
            ]
        ),
        encoding="utf-8",
    )
    (latest / "pre_submit_readiness_report.md").write_text("- pre_submit_readiness_status: BLOCK\n", encoding="utf-8")
    (latest / "chatgpt_master_report.md").write_text(
        "\n".join(
            [
                "- open_paper_positions: 1",
                "- submit_order_called: false",
                "- post_request_used: false",
            ]
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "symbol": "FIX",
                "warning": "fixture_warning",
                "policy_status": "BLOCK_UNTIL_POLICY_ACK",
                "operator_decision": "ACK",
                "operator_note": "fixture acknowledgement",
            }
        ]
    ).to_csv(latest / "data_policy_decisions.csv", index=False)
    (latest / "data_policy_review_report.md").write_text(
        "- status: OK\n- unresolved_count: 0\n",
        encoding="utf-8",
    )


def _write_monitor(latest: Path, pending: bool = True) -> None:
    rows = [
        {"section": "summary", "metric": "total_filled_trades", "value": 1},
        {"section": "summary", "metric": "open_paper_positions", "value": 1},
        {"section": "summary", "metric": "pending_next_open_count", "value": 1 if pending else 0},
        {"section": "summary", "metric": "paper_ledger_status", "value": "OK"},
        {"section": "summary", "metric": "total_open_notional", "value": 100.0},
        {"section": "summary", "metric": "total_unrealized_pnl", "value": 5.0},
        {"section": "summary", "metric": "total_unrealized_pnl_pct", "value": 5.0},
        {"section": "summary", "metric": "best_open_position", "value": "FIX"},
        {"section": "summary", "metric": "worst_open_position", "value": "FIX"},
        {"section": "risk_check", "metric": "duplicate_open_symbol_count", "value": 0},
        {"section": "risk_check", "metric": "pending_buy_already_open_symbols", "value": "none"},
        {"section": "risk_check", "metric": "pending_sell_without_open_position_symbols", "value": "none"},
        {"section": "risk_check", "metric": "missing_mark_price_symbols", "value": "none"},
        {"section": "risk_check", "metric": "stale_mark_price_symbols", "value": "none"},
        {"section": "risk_check", "metric": "broker_submit_enabled", "value": False},
        {"section": "risk_check", "metric": "live_trading_enabled", "value": False},
        {"section": "risk_check", "metric": "short_trading_enabled", "value": False},
        {"section": "risk_check", "metric": "submit_order_called", "value": False},
        {"section": "risk_check", "metric": "post_request_used", "value": False},
    ]
    pd.DataFrame(rows).to_csv(latest / "paper_portfolio_monitor.csv", index=False)


class PaperOpsDashboardTest(unittest.TestCase):
    def test_dashboard_reports_open_positions_and_accepted_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_common_reports(latest)
            _write_monitor(latest)
            pending = pd.DataFrame(
                [
                    {
                        "symbol": "FIXA",
                        "action": "BUY",
                        "status": "PENDING",
                        "user_decision": "ACCEPT",
                        "reason": "fixture_missing_next_open",
                    }
                ]
            )
            pending.to_csv(latest / "paper_pending_orders.csv", index=False)
            pd.DataFrame(columns=["symbol", "action", "user_decision"]).to_csv(latest / "paper_order_review.csv", index=False)

            dashboard = write_paper_ops_dashboard(latest)

            self.assertEqual(dashboard["portfolio_summary"]["open_paper_positions"], 1)
            self.assertEqual(dashboard["pending_workflow"]["accepted_pending_orders_count"], 1)
            self.assertEqual(dashboard["pending_workflow"]["accepted_pending_buy_symbols"], ["FIXA"])
            self.assertEqual(dashboard["next_action"], "wait for next open data, then run pending refresh + daily pipeline")
            self.assertTrue((latest / "paper_ops_dashboard.md").exists())
            self.assertTrue((latest / "paper_ops_dashboard.json").exists())

    def test_dashboard_handles_no_pending_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_common_reports(latest)
            _write_monitor(latest, pending=False)
            pd.DataFrame(columns=["symbol", "action", "status", "user_decision"]).to_csv(
                latest / "paper_pending_orders.csv",
                index=False,
            )
            pd.DataFrame(columns=["symbol", "action", "user_decision"]).to_csv(latest / "paper_order_review.csv", index=False)

            dashboard = build_paper_ops_dashboard(latest)

            self.assertEqual(dashboard["pending_workflow"]["accepted_pending_orders_count"], 0)
            self.assertEqual(dashboard["next_action"], "monitor positions")

    def test_dashboard_detects_hold_review_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_common_reports(latest)
            _write_monitor(latest, pending=False)
            pd.DataFrame(
                [
                    {
                        "symbol": "FIXH",
                        "action": "BUY",
                        "status": "PENDING",
                        "user_decision": "REVIEW_REQUIRED",
                    }
                ]
            ).to_csv(latest / "paper_pending_orders.csv", index=False)
            pd.DataFrame(
                [{"symbol": "FIXH", "action": "BUY", "user_decision": "HOLD_REVIEW"}]
            ).to_csv(latest / "paper_order_review.csv", index=False)

            dashboard = build_paper_ops_dashboard(latest)

            self.assertGreater(dashboard["pending_workflow"]["hold_review_count"], 0)
            self.assertEqual(dashboard["pending_workflow"]["symbols_needing_operator_decision"], ["FIXH"])
            self.assertEqual(dashboard["next_action"], "review paper decisions")

    def test_dashboard_surfaces_blocked_duplicate_buy(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_common_reports(latest)
            _write_monitor(latest, pending=False)
            pd.DataFrame(
                [
                    {
                        "symbol": "FIXD",
                        "action": "BUY",
                        "status": "BLOCKED",
                        "user_decision": "REVIEW_REQUIRED",
                        "reason": "fixture_reason;buy_already_open",
                    }
                ]
            ).to_csv(latest / "paper_pending_orders.csv", index=False)
            pd.DataFrame(columns=["symbol", "action", "user_decision"]).to_csv(latest / "paper_order_review.csv", index=False)

            dashboard = build_paper_ops_dashboard(latest)

            self.assertEqual(dashboard["pending_workflow"]["blocked_duplicate_buy_symbols"], ["FIXD"])
            self.assertEqual(dashboard["risk_consistency"]["blocked_duplicate_buy_symbols"], ["FIXD"])

    def test_dashboard_reports_safety_false_and_does_not_mutate_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_common_reports(latest)
            _write_monitor(latest)
            pending = pd.DataFrame(
                [{"symbol": "FIXA", "action": "BUY", "status": "PENDING", "user_decision": "ACCEPT"}]
            )
            pending_path = latest / "paper_pending_orders.csv"
            pending.to_csv(pending_path, index=False)
            pd.DataFrame(columns=["symbol", "action", "user_decision"]).to_csv(latest / "paper_order_review.csv", index=False)
            before = pending_path.read_text(encoding="utf-8")

            write_paper_ops_dashboard(latest)
            payload = json.loads((latest / "paper_ops_dashboard.json").read_text(encoding="utf-8"))

            self.assertEqual(pending_path.read_text(encoding="utf-8"), before)
            self.assertEqual(str(payload["safety_gates"]["broker_submit_enabled"]).lower(), "false")
            self.assertEqual(str(payload["safety_gates"]["live_trading_enabled"]).lower(), "false")
            self.assertEqual(str(payload["safety_gates"]["short_trading_enabled"]).lower(), "false")


if __name__ == "__main__":
    unittest.main()
