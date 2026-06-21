import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.runner import (
    _chatgpt_existing_execution_readiness_section,
    _chatgpt_paper_readiness_section,
    _chatgpt_test_suite_status_section,
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
                },
            )
            text = (latest / "chatgpt_master_report.md").read_text(encoding="utf-8")
            self.assertIn("- risk_pass_count: 0", text)
            self.assertIn("- submit_candidate_risk_pass_count: 5", text)
            self.assertIn("- would_submit_count: 0", text)
            self.assertIn("- submit_allowed: false", text)

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
            self.assertIn("- submit_order_called: false", text)
            self.assertIn("- post_request_used: false", text)


if __name__ == "__main__":
    unittest.main()
