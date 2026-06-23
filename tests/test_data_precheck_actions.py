import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.runner import _write_data_precheck_report


def _write_daily(path, date="2026-06-18", volume=1000):
    path.write_text(
        "\n".join(
            [
                "Date,Open,High,Low,Close,Volume",
                f"{date},10,11,9,10,{volume}",
            ]
        ),
        encoding="utf-8",
    )


def _write_intraday(path, date="2026-06-18", volume=1000):
    path.write_text(
        "\n".join(
            [
                "Datetime,Open,High,Low,Close,Volume",
                f"{date} 09:30:00-05:00,10,11,9,10,{volume}",
            ]
        ),
        encoding="utf-8",
    )


class DataPrecheckActionsTest(unittest.TestCase):
    def test_warning_symbols_emit_recommended_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily = root / "daily"
            intraday = root / "bars_130min"
            daily.mkdir()
            intraday.mkdir()
            for symbol in ["BK", "DOW", "PARA", "PSA", "QQQ", "SPY"]:
                _write_intraday(intraday / f"{symbol}_130min.csv")
            (daily / "DOW_1D.csv").write_text("Date,Open,High,Low,Close,Volume\n", encoding="utf-8")
            _write_daily(daily / "BK_1D.csv", date="2026-05-20")
            _write_daily(daily / "PARA_1D.csv", date="2026-06-18")
            _write_daily(daily / "PSA_1D.csv", volume=0)
            _write_daily(daily / "QQQ_1D.csv")
            _write_daily(daily / "SPY_1D.csv")
            pd.DataFrame(
                [
                    {
                        "symbol": "BK",
                        "warning": "early_daily_end",
                        "policy_status": "BLOCK_UNTIL_POLICY_ACK",
                        "recommended_policy_action": "confirm_delisted_renamed_or_remove_from_trading_universe",
                        "operator_decision": "ACKNOWLEDGED",
                        "operator_note": "known pending corporate-action review",
                    }
                ]
            ).to_csv(root / "data_policy_decisions.csv", index=False)

            result = _write_data_precheck_report(
                None,
                daily,
                intraday,
                {"latest_daily_data_date": "2026-06-18", "latest_130min_data_date": "2026-06-18"},
                root / "data_precheck_report.md",
            )

            actions = result["recommended_actions"]
            policy_decisions = result["policy_decisions"]
            warnings = {(item["symbol"], item["warning"]) for item in actions}
            self.assertIn(("DOW", "dow_empty_daily"), warnings)
            self.assertIn(("BK", "early_daily_end"), warnings)
            self.assertIn(("PSA", "psa_zero_volume"), warnings)
            policies = {(item["symbol"], item["policy_status"]) for item in policy_decisions}
            self.assertIn(("BK", "BLOCK_UNTIL_POLICY_ACK"), policies)
            self.assertIn(("PSA", "BLOCK_UNTIL_POLICY_ACK"), policies)
            self.assertIn(("DOW", "BLOCK_UNTIL_DATA_FIXED"), policies)
            report_text = (root / "data_precheck_report.md").read_text(encoding="utf-8")
            self.assertIn("## Recommended Actions", report_text)
            self.assertIn("## Data Policy Decisions", report_text)
            self.assertIn("recommended_action_count: 3", report_text)
            self.assertIn("policy_decision_count: 3", report_text)
            self.assertIn("data_policy_decisions_path:", report_text)
            self.assertIn("## Operator Decision Guide", report_text)
            self.assertIn("Resolving values require a non-empty operator_note", report_text)
            policy_file = pd.read_csv(root / "data_policy_decisions.csv")
            self.assertEqual(len(policy_file), 3)
            bk = policy_file[policy_file["symbol"].eq("BK")].iloc[0]
            self.assertEqual(bk["operator_decision"], "ACKNOWLEDGED")
            self.assertEqual(bk["operator_note"], "known pending corporate-action review")
            self.assertIn("operator_decision", policy_file.columns)


if __name__ == "__main__":
    unittest.main()
