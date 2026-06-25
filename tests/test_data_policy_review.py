import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from testdata.data_policy_review import main


class DataPolicyReviewTest(unittest.TestCase):
    def test_main_writes_review_report_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            daily = root / "hisseler" / "daily"
            intraday = root / "hisseler" / "bars_130min"
            latest.mkdir(parents=True)
            daily.mkdir(parents=True)
            intraday.mkdir(parents=True)
            (latest / "paper_pending_orders.csv").write_text(
                "symbol,quantity,side\nBK,10,BUY\nPARA,10,BUY\n", encoding="utf-8"
            )
            (latest / "data_policy_decisions.csv").write_text(
                "\n".join(
                    [
                        "symbol,warning,policy_status,recommended_policy_action,operator_decision,operator_note",
                        "BK,early_daily_end,BLOCK_UNTIL_POLICY_ACK,confirm,REVIEW_REQUIRED,",
                        "PSA,psa_zero_volume,BLOCK_UNTIL_POLICY_ACK,inspect,ACKNOWLEDGED,ok",
                        "PARA,early_daily_end,BLOCK_UNTIL_POLICY_ACK,confirm,ACKNOWLEDGED,",
                    ]
                ),
                encoding="utf-8",
            )
            (daily / "BK_1D.csv").write_text("Date,Open,High,Low,Close,Volume\n2026-05-20,1,1,1,1,100\n", encoding="utf-8")
            (intraday / "BK_130min.csv").write_text(
                "Date,Open,High,Low,Close,Volume\n2026-05-20 09:30:00-04:00,1,1,1,1,100\n",
                encoding="utf-8",
            )
            (daily / "PSA_1D.csv").write_text("Date,Open,High,Low,Close,Volume\n2026-06-18,1,1,1,1,0\n", encoding="utf-8")
            (intraday / "PSA_130min.csv").write_text(
                "Datetime,Open,High,Low,Close,Volume\n2026-06-18 09:30:00-04:00,1,1,1,1,100\n",
                encoding="utf-8",
            )

            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(), 0)
            finally:
                os.chdir(old_cwd)

            report_text = (latest / "data_policy_review_report.md").read_text(encoding="utf-8")
            evidence_text = (latest / "data_policy_review_evidence.csv").read_text(encoding="utf-8")
            self.assertIn("- status: BLOCK", report_text)
            self.assertIn("- unresolved_count: 2", report_text)
            self.assertIn("operator_note is non-empty", report_text)
            self.assertIn("local CSV dates/rows/zero-volume only", report_text)
            self.assertIn("2026-05-20 09:30:00-04:00", report_text)
            self.assertNotIn("nan", report_text)
            self.assertIn("BK", evidence_text)
            self.assertIn("daily_zero_volume_rows", evidence_text)
            self.assertIn("recommended_operator_next_action", evidence_text)
            self.assertIn("verify_corporate_action_or_vendor_stale_history_before_ack", evidence_text)
            self.assertIn("inspect_zero_volume_vendor_row_or_repair_source_before_ack", evidence_text)


if __name__ == "__main__":
    unittest.main()
