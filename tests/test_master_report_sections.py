import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.runner import (
    _chatgpt_operational_health_section,
    _chatgpt_existing_execution_readiness_section,
    _chatgpt_paper_readiness_section,
    _chatgpt_test_suite_status_section,
    _chatgpt_useful_commands_section,
    _write_execution_bot_readiness_section,
)


class MasterReportSectionsTest(unittest.TestCase):
    def test_execution_readiness_includes_submit_candidate_and_would_submit_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            (latest / "chatgpt_master_report.md").write_text("# ChatGPT Master Report\n", encoding="utf-8")
            _write_execution_bot_readiness_section(
                latest,
                {
                    "execution_preview_status": "OK",
                    "order_intents_count": 5,
                    "risk_pass_count": 0,
                    "submit_candidate_risk_pass_count": 5,
                    "blocked_count": 5,
                    "would_submit_count": 0,
                    "pre_submit_readiness_status": "BLOCK",
                    "pre_submit_checks": pd.DataFrame(
                        [
                            {
                                "check_name": "data_policy_operator_review",
                                "status": "BLOCK",
                                "value": 3,
                            }
                        ]
                    ),
                },
            )
            text = (latest / "chatgpt_master_report.md").read_text(encoding="utf-8")
            self.assertIn("- risk_pass_count: 0", text)
            self.assertIn("- submit_candidate_risk_pass_count: 5", text)
            self.assertIn("- would_submit_count: 0", text)
            self.assertIn("- submit_allowed: false", text)
            self.assertIn("- data_policy_operator_review_status: BLOCK", text)
            self.assertIn("- data_policy_operator_review_unresolved_count: 3", text)

    def test_execution_readiness_falls_back_to_pre_submit_csv_for_data_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            (latest / "chatgpt_master_report.md").write_text("# ChatGPT Master Report\n", encoding="utf-8")
            pd.DataFrame(
                [
                    {
                        "check_name": "data_policy_operator_review",
                        "status": "BLOCK",
                        "value": 3,
                    }
                ]
            ).to_csv(latest / "pre_submit_readiness_report.csv", index=False)
            _write_execution_bot_readiness_section(
                latest,
                {
                    "execution_preview_status": "OK",
                    "order_intents_count": 5,
                    "pre_submit_readiness_status": "BLOCK",
                },
            )
            text = (latest / "chatgpt_master_report.md").read_text(encoding="utf-8")
            self.assertIn("- data_policy_operator_review_status: BLOCK", text)
            self.assertIn("- data_policy_operator_review_unresolved_count: 3", text)

    def test_paper_readiness_next_action_uses_daily_pipeline_for_pending_next_open(self):
        section = _chatgpt_paper_readiness_section(
            pd.DataFrame(
                [
                    {
                        "strategy_name": "rs_m126_top30_sma100_no_regime",
                        "signal_date": "2026-06-18",
                        "symbol": "AAPL",
                        "action": "BUY",
                        "fill_status": "PENDING_NEXT_OPEN",
                        "user_decision": "ACCEPT",
                        "paper_actionable": True,
                        "liquidity_warning_reason": "",
                    }
                ]
            )
        )
        text = "\n".join(section)
        self.assertIn(
            "- next action: Wait for next valid market open data, then run scripts/run_daily_paper_pipeline.bat again.",
            text,
        )
        self.assertNotIn("manually record accepted paper fills", text)

    def test_test_suite_status_reads_latest_test_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            (latest / "test_run_report.md").write_text(
                "\n".join(
                    [
                        "# Test Run Report",
                        "",
                        "- run datetime: 2026-06-21 15:00:00",
                        "- status: OK",
                        "- tests ran count: 66",
                    ]
                ),
                encoding="utf-8",
            )
            text = "\n".join(_chatgpt_test_suite_status_section(latest))
            self.assertIn("## Test Suite Status", text)
            self.assertIn("- status: OK", text)
            self.assertIn("- tests ran count: 66", text)
            self.assertIn("- path: reports/latest/test_run_report.md", text)

    def test_useful_commands_includes_data_policy_review_helper(self):
        text = "\n".join(_chatgpt_useful_commands_section())
        self.assertIn("- Data policy review: `scripts/run_data_policy_review.bat`", text)

    def test_operational_health_includes_data_policy_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "paper_pending_orders.csv").write_text("symbol\nBK\nPSA\n", encoding="utf-8")
            section = _chatgpt_operational_health_section(
                {
                    "data_precheck": {
                        "status": "WARNING",
                        "warnings": ["early_daily_end"],
                        "critical": [],
                        "policy_decisions": [
                            {"symbol": "BK", "policy_status": "BLOCK_UNTIL_POLICY_ACK"},
                            {"symbol": "PSA", "policy_status": "BLOCK_UNTIL_POLICY_ACK"},
                        ],
                    },
                    "pipeline_health": {
                        "status": "WARNING",
                        "latest_dir": str(tmp_path),
                    },
                }
            )
        text = "\n".join(section)
        self.assertIn("- data_policy_status: BLOCK", text)
        self.assertIn("- safe_known_exception_count: 1", text)
        self.assertIn("- data_redownload_required_count: 1", text)
        self.assertIn("- manual_review_required_count: 0", text)
        self.assertIn("- active_blocking_symbols: BK, PSA", text)
        self.assertIn("- inactive_exception_symbols: none", text)
        self.assertIn("- data_policy_decisions_path: reports/latest/data_policy_decisions.csv", text)
        self.assertIn("- data_policy_review_report_path: reports/latest/data_policy_review_report.md", text)
        self.assertIn("- data_policy_review_evidence_path: reports/latest/data_policy_review_evidence.csv", text)

    def test_operational_health_marks_missing_data_policy_not_generated(self):
        text = "\n".join(_chatgpt_operational_health_section({"pipeline_health": {"status": "OK"}}))
        self.assertIn("- data_policy_status: not_generated", text)
        self.assertIn("- data_policy_decision_count: 0", text)
        self.assertIn("- data_policy_blocking_count: 0", text)
        self.assertIn("- data_policy_blocking_symbols: none", text)

    def test_existing_execution_readiness_section_reads_latest_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            (latest / "execution_preview_report.md").write_text(
                "\n".join(
                    [
                        "# Execution Preview Report",
                        "",
                        "- order intents count: 5",
                        "- risk passed count: 5",
                        "- would_submit count: 0",
                        "- broker_submit_enabled: false",
                        "- live_trading_enabled: false",
                    ]
                ),
                encoding="utf-8",
            )
            (latest / "pre_submit_readiness_report.md").write_text(
                "\n".join(["# Pre-submit Readiness Report", "", "- submit_allowed: False"]),
                encoding="utf-8",
            )
            pd.DataFrame(
                [
                    {
                        "check_name": "data_policy_operator_review",
                        "status": "BLOCK",
                        "value": 3,
                        "required_value": 0,
                        "message": "policy decisions require review",
                    }
                ]
            ).to_csv(latest / "pre_submit_readiness_report.csv", index=False)
            pd.DataFrame(
                [
                    {
                        "event_type": "SUBMIT_DISABLED",
                        "broker_submit_enabled": False,
                        "live_trading_enabled": False,
                    }
                ]
            ).to_csv(latest / "execution_audit_log.csv", index=False)
            text = "\n".join(_chatgpt_existing_execution_readiness_section(latest))
            self.assertIn("## Execution Bot Readiness", text)
            self.assertIn("- execution_preview_status: OK", text)
            self.assertIn("- order_intents_count: 5", text)
            self.assertIn("- risk_pass_count: 5", text)
            self.assertIn("- would_submit_count: 0", text)
            self.assertIn("- broker_submit_enabled: false", text)
            self.assertIn("- live_trading_enabled: false", text)
            self.assertIn("- data_policy_operator_review_status: BLOCK", text)
            self.assertIn("- data_policy_operator_review_unresolved_count: 3", text)
            self.assertIn("- submit_order_called: false", text)
            self.assertIn("- post_request_used: false", text)


if __name__ == "__main__":
    unittest.main()
