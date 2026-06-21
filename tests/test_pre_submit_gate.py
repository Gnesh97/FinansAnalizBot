import unittest
import tempfile
from pathlib import Path

from testdata.pre_submit_gate import (
    build_summary_from_latest,
    build_pre_submit_checks,
    build_short_readiness_checks,
    readiness_summary,
    short_readiness_summary,
)


class PreSubmitGateTest(unittest.TestCase):
    def _summary(self, **overrides):
        base = {
            "broker_submit_enabled": False,
            "live_trading_enabled": False,
            "submit_order_called": False,
            "post_request_used": False,
            "pending_accepted_orders_count": 5,
            "order_intents_count": 5,
            "risk_pass_count": 5,
            "blocked_count": 0,
            "preview_duplicate_count": 0,
            "pending_next_open_count": 0,
            "next_open_data_available": True,
            "market_open_closed_info_available": True,
            "alpaca_paper_connection_ok": True,
            "alpaca_positions_orders_ok": True,
            "local_state_execution_preview_match": True,
            "data_precheck_status": "OK",
            "data_precheck_warning_summary": "none",
            "short_trading_enabled": False,
            "short_intents_count": 0,
            "short_blocked_count": 0,
            "borrow_check_available": False,
        }
        base.update(overrides)
        return base

    def test_broker_submit_disabled_blocks(self):
        checks = build_pre_submit_checks(self._summary(broker_submit_enabled=False))
        row = checks[checks["check_name"].eq("broker_submit_enabled")].iloc[0]
        self.assertEqual(row["status"], "BLOCK")

    def test_live_trading_disabled_blocks(self):
        checks = build_pre_submit_checks(self._summary(live_trading_enabled=False))
        row = checks[checks["check_name"].eq("live_trading_enabled")].iloc[0]
        self.assertEqual(row["status"], "BLOCK")

    def test_submit_and_post_false_pass(self):
        checks = build_pre_submit_checks(self._summary())
        self.assertEqual(checks[checks["check_name"].eq("submit_order_called")].iloc[0]["status"], "PASS")
        self.assertEqual(checks[checks["check_name"].eq("post_request_used")].iloc[0]["status"], "PASS")

    def test_pending_next_open_blocks(self):
        checks = build_pre_submit_checks(self._summary(pending_next_open_count=5))
        row = checks[checks["check_name"].eq("pending_next_open_count")].iloc[0]
        self.assertEqual(row["status"], "BLOCK")

    def test_valid_risk_pass_but_submit_allowed_false(self):
        checks = build_pre_submit_checks(self._summary(risk_pass_count=5))
        self.assertEqual(checks[checks["check_name"].eq("submit_candidate_risk_pass_count")].iloc[0]["status"], "PASS")
        self.assertFalse(readiness_summary(checks)["submit_allowed"])

    def test_preview_duplicate_does_not_zero_submit_candidate_risk(self):
        checks = build_pre_submit_checks(
            self._summary(
                risk_pass_count=0,
                submit_candidate_risk_pass_count=5,
                blocked_count=5,
                preview_duplicate_count=5,
            )
        )
        row = checks[checks["check_name"].eq("submit_candidate_risk_pass_count")].iloc[0]
        self.assertEqual(row["status"], "PASS")

    def test_short_readiness_blocks_when_short_disabled(self):
        checks = build_short_readiness_checks(self._summary(short_trading_enabled=False))
        result = short_readiness_summary(checks)
        self.assertEqual(result["short_readiness_status"], "BLOCK")
        self.assertFalse(result["short_submit_allowed"])

    def test_alpaca_report_underscore_connection_ok_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            (latest / "alpaca_paper_check_report.md").write_text(
                "\n".join(
                    [
                        "- connection_status: ok",
                        "- open_positions_count: 0",
                        "- open_orders_count: 0",
                    ]
                ),
                encoding="utf-8",
            )
            summary = build_summary_from_latest(latest, self._summary())
            self.assertTrue(summary["alpaca_paper_connection_ok"])
            self.assertTrue(summary["alpaca_positions_orders_ok"])

    def test_alpaca_positions_orders_do_not_pass_without_connection_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            (latest / "alpaca_paper_check_report.md").write_text(
                "\n".join(
                    [
                        "- connection_status: missing_credentials",
                        "- open_positions_count: 0",
                        "- open_orders_count: 0",
                    ]
                ),
                encoding="utf-8",
            )
            summary = build_summary_from_latest(latest, self._summary())
            self.assertFalse(summary["alpaca_paper_connection_ok"])
            self.assertFalse(summary["alpaca_positions_orders_ok"])

    def test_data_precheck_warning_blocks_submit_readiness(self):
        checks = build_pre_submit_checks(
            self._summary(
                data_precheck_status="WARNING",
                data_precheck_warning_summary="dow_empty_daily",
            )
        )
        row = checks[checks["check_name"].eq("data_precheck_status")].iloc[0]
        self.assertEqual(row["status"], "BLOCK")
        self.assertIn("dow_empty_daily", row["message"])

    def test_summary_reads_data_precheck_status_from_run_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            (latest / "run_manifest.json").write_text(
                '{"data_precheck_status": "WARNING", "warning_summary": "psa_zero_volume"}',
                encoding="utf-8",
            )
            summary = build_summary_from_latest(latest, self._summary())
            self.assertEqual(summary["data_precheck_status"], "WARNING")
            self.assertEqual(summary["data_precheck_warning_summary"], "psa_zero_volume")


if __name__ == "__main__":
    unittest.main()
