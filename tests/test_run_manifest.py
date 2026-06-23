import json
import tempfile
import unittest
from pathlib import Path

from testdata.runner import _write_run_manifest


class RunManifestTest(unittest.TestCase):
    def test_write_run_manifest_records_command_data_dates_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest_dir = root / "reports" / "latest"
            run_dir = root / "reports" / "runs" / "2026-01-02_030405"
            daily_dir = root / "hisseler" / "daily"
            intraday_dir = root / "hisseler" / "bars_130min"
            for directory in [latest_dir, run_dir, daily_dir, intraday_dir]:
                directory.mkdir(parents=True)
            (latest_dir / "strategy_compact_report.csv").write_text("a\n1\n", encoding="utf-8")
            (run_dir / "strategy_compact_report.csv").write_text("a\n1\n", encoding="utf-8")
            (daily_dir / "AAPL_1D.csv").write_text("Date,Close\n2026-01-02,100\n", encoding="utf-8")
            (intraday_dir / "AAPL_130min.csv").write_text(
                "Datetime,Close\n2026-01-02 09:30:00-05:00,100\n",
                encoding="utf-8",
            )

            (latest_dir / "paper_pending_orders.csv").write_text("symbol\nBK\nPSA\n", encoding="utf-8")
            manifest = _write_run_manifest(
                latest_dir,
                run_dir,
                "python universe_scanner.py --reports basic",
                "basic",
                {"paper_report": False},
                daily_dir,
                intraday_dir,
                root / "cache.pkl",
                {"prepared_disk_cache_hits": 2, "prepared_disk_cache_misses": 1, "symbols_loaded": 1, "rows_loaded": 2},
                1.23456,
                {
                    "data_precheck": {
                        "status": "WARNING",
                        "policy_decisions": [
                            {"symbol": "BK", "policy_status": "BLOCK_UNTIL_POLICY_ACK"},
                            {"symbol": "PSA", "policy_status": "BLOCK_UNTIL_POLICY_ACK"},
                        ],
                    }
                },
            )

            latest_manifest = json.loads((latest_dir / "run_manifest.json").read_text(encoding="utf-8"))
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["command"], "python universe_scanner.py --reports basic")
            self.assertEqual(latest_manifest["latest_daily_date"], "2026-01-02")
            self.assertEqual(latest_manifest["latest_130min_date"], "2026-01-02")
            self.assertEqual(latest_manifest["cache_hit_count"], 2)
            self.assertIn("strategy_compact_report.csv", latest_manifest["available_reports_latest"])
            self.assertIn("run_manifest.json", latest_manifest["available_reports_latest"])
            self.assertIn("strategy_compact_report.csv", latest_manifest["generated_reports_run"])
            self.assertEqual(run_manifest["run_dir"], str(run_dir))
            self.assertEqual(run_manifest["data_policy_status"], "BLOCK")
            self.assertEqual(run_manifest["data_policy_decision_count"], 2)
            self.assertEqual(run_manifest["data_policy_blocking_count"], 2)
            self.assertEqual(run_manifest["data_policy_blocking_symbols"], ["BK", "PSA"])

    def test_write_run_manifest_marks_missing_data_policy_not_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest_dir = root / "reports" / "latest"
            run_dir = root / "reports" / "runs" / "2026-01-02_030405"
            daily_dir = root / "hisseler" / "daily"
            intraday_dir = root / "hisseler" / "bars_130min"
            for directory in [latest_dir, run_dir, daily_dir, intraday_dir]:
                directory.mkdir(parents=True)

            manifest = _write_run_manifest(
                latest_dir,
                run_dir,
                "python universe_scanner.py --reports basic",
                "basic",
                {},
                daily_dir,
                intraday_dir,
                None,
                {},
                0.0,
            )

            self.assertEqual(manifest["data_precheck_status"], "not_generated")
            self.assertEqual(manifest["data_policy_status"], "not_generated")
            self.assertEqual(manifest["data_policy_decision_count"], 0)
            self.assertEqual(manifest["data_policy_blocking_count"], 0)


if __name__ == "__main__":
    unittest.main()
