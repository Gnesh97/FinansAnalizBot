import tempfile
import unittest
from pathlib import Path

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

            result = _write_data_precheck_report(
                None,
                daily,
                intraday,
                {"latest_daily_data_date": "2026-06-18", "latest_130min_data_date": "2026-06-18"},
                root / "data_precheck_report.md",
            )

            actions = result["recommended_actions"]
            warnings = {(item["symbol"], item["warning"]) for item in actions}
            self.assertIn(("DOW", "dow_empty_daily"), warnings)
            self.assertIn(("BK", "early_daily_end"), warnings)
            self.assertIn(("PSA", "psa_zero_volume"), warnings)
            report_text = (root / "data_precheck_report.md").read_text(encoding="utf-8")
            self.assertIn("## Recommended Actions", report_text)
            self.assertIn("recommended_action_count: 3", report_text)


if __name__ == "__main__":
    unittest.main()
