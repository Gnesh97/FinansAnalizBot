import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

from testdata.vericekici_updated import (  # noqa: E402
    DEFAULT_V2_FROZEN_ROOT,
    DEFAULT_V2_SIP_FROZEN_ROOT,
    V2_COVERAGE_EXCEPTIONS_PATH,
    V2_SIP_COVERAGE_EXCEPTIONS_PATH,
    main,
)


SIP_BASE = [
    "vericekici_updated.py", "--dataset-profile", "rs_v2_sip",
    "--adjustment", "all", "--feed", "sip",
    "--start", "2024-01-02", "--end", "2024-01-05",
    "--symbols", "AAPL", "MSFT",
]


class TestSipProfileMandatoryFlags(unittest.TestCase):
    def _run(self, argv, expected_message):
        with patch("testdata.vericekici_updated.ensure_project_venv"), patch.object(sys, "argv", argv), self.assertRaisesRegex(ValueError, expected_message):
            main()

    def test_requires_explicit_adjustment(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--feed", "sip", "--start", "2024-01-02", "--end", "2024-01-05", "--output-root", "unused", "--symbols", "AAPL", "--dry-run"]
        self._run(argv, "requires explicit --adjustment")

    def test_requires_adjustment_all(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "raw", "--feed", "sip", "--start", "2024-01-02", "--end", "2024-01-05", "--output-root", "unused", "--symbols", "AAPL", "--dry-run"]
        self._run(argv, "requires --adjustment all")

    def test_requires_explicit_feed(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--start", "2024-01-02", "--end", "2024-01-05", "--output-root", "unused", "--symbols", "AAPL", "--dry-run"]
        self._run(argv, "requires explicit --feed")

    def test_feed_iex_rejected_under_rs_v2_sip(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "iex", "--start", "2024-01-02", "--end", "2024-01-05", "--output-root", "unused", "--symbols", "AAPL", "--dry-run"]
        self._run(argv, "requires --feed sip")

    def test_requires_start(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "sip", "--end", "2024-01-05", "--output-root", "unused", "--symbols", "AAPL", "--dry-run"]
        self._run(argv, "requires --start")

    def test_requires_end(self):
        with tempfile.TemporaryDirectory() as temp:
            argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "sip", "--start", "2024-01-02", "--end", "", "--output-root", temp, "--symbols", "AAPL", "--dry-run"]
            self._run(argv, "requires --end")

    def test_requires_output_root(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "sip", "--start", "2024-01-02", "--end", "2024-01-05", "--symbols", "AAPL", "--dry-run"]
        self._run(argv, "requires --output-root")

    def test_requires_dry_run_or_live(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "sip", "--start", "2024-01-02", "--end", "2024-01-05", "--output-root", "unused", "--symbols", "AAPL"]
        self._run(argv, "requires either --dry-run or --live")

    def test_cannot_specify_both_dry_run_and_live(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "sip", "--start", "2024-01-02", "--end", "2024-01-05", "--output-root", "unused", "--symbols", "AAPL", "--dry-run", "--live"]
        self._run(argv, "Cannot specify both")

    def test_requires_symbols_no_frozen_baseline_yet(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "sip", "--start", "2024-01-02", "--end", "2024-01-05", "--output-root", "unused", "--dry-run"]
        self._run(argv, "no frozen baseline established yet")

    def test_live_not_yet_implemented_in_phase2a(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "sip", "--start", "2024-01-02", "--end", "2024-01-05", "--output-root", "unused", "--symbols", "AAPL", "--live"]
        self._run(argv, "V2_SIP_LIVE_ACQUISITION_NOT_YET_IMPLEMENTED_PHASE2A")


class TestFeedRejectionRegression(unittest.TestCase):
    """rs_v2_clean must still reject --feed sip exactly as before TASK B."""

    def test_feed_sip_rejected_under_rs_v2_clean(self):
        argv = [
            "vericekici_updated.py", "--dataset-profile", "rs_v2_clean",
            "--adjustment", "all", "--feed", "sip",
            "--start", "2020-07-27", "--end", "2026-07-07",
            "--output-root", "unused", "--dry-run",
        ]
        with patch("testdata.vericekici_updated.ensure_project_venv"), patch.object(sys, "argv", argv), self.assertRaisesRegex(ValueError, "requires --feed iex"):
            main()


class TestSipDryRun(unittest.TestCase):
    def test_dry_run_produces_plan_and_never_touches_credentials_or_client(self):
        with tempfile.TemporaryDirectory() as temp, patch("testdata.vericekici_updated.ensure_project_venv"), patch("testdata.vericekici_updated.get_credentials") as credentials, patch("testdata.vericekici_updated._load_alpaca") as alpaca:
            argv = [
                "vericekici_updated.py", "--dataset-profile", "rs_v2_sip",
                "--adjustment", "all", "--feed", "sip",
                "--start", "2024-01-02", "--end", "2024-01-05",
                "--output-root", temp, "--symbols", "AAPL", "MSFT",
                "--dry-run",
            ]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as exit_code:
                main()
            self.assertEqual(exit_code.exception.code, 0)
            credentials.assert_not_called()
            alpaca.assert_not_called()
            self.assertFalse((Path(temp) / "manifests").exists())
            plan = pd.read_csv(Path(temp) / "dry_run_reports" / "rs_v2_clean_data_acquisition_plan.csv")
            # 2 requested candidates + QQQ + SPY benchmarks, x 2 timeframes each
            self.assertEqual(len(plan), 8)
            self.assertEqual(sorted(plan["symbol"].unique()), ["AAPL", "MSFT", "QQQ", "SPY"])

    def test_dry_run_deduplicates_benchmark_symbols_passed_explicitly(self):
        with tempfile.TemporaryDirectory() as temp, patch("testdata.vericekici_updated.ensure_project_venv"):
            argv = [
                "vericekici_updated.py", "--dataset-profile", "rs_v2_sip",
                "--adjustment", "all", "--feed", "sip",
                "--start", "2024-01-02", "--end", "2024-01-05",
                "--output-root", temp, "--symbols", "AAPL", "QQQ",
                "--dry-run",
            ]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
                main()
            plan = pd.read_csv(Path(temp) / "dry_run_reports" / "rs_v2_clean_data_acquisition_plan.csv")
            # QQQ must appear exactly once per timeframe, not duplicated because
            # it was also passed explicitly in --symbols.
            self.assertEqual((plan["symbol"] == "QQQ").sum(), 2)
            self.assertEqual(sorted(plan["symbol"].unique()), ["AAPL", "QQQ", "SPY"])
            self.assertEqual(len(plan), 6)


class TestOutputRootIsolation(unittest.TestCase):
    def test_sip_and_clean_have_distinct_default_roots_and_exceptions_files(self):
        self.assertNotEqual(DEFAULT_V2_SIP_FROZEN_ROOT, DEFAULT_V2_FROZEN_ROOT)
        self.assertIn("rs_v2_sip_market_data", str(DEFAULT_V2_SIP_FROZEN_ROOT))
        self.assertIn("rs_v2_clean_market_data", str(DEFAULT_V2_FROZEN_ROOT))
        self.assertNotEqual(V2_SIP_COVERAGE_EXCEPTIONS_PATH, V2_COVERAGE_EXCEPTIONS_PATH)

    def test_sip_dry_run_never_writes_under_clean_default_root(self):
        with tempfile.TemporaryDirectory() as temp, patch("testdata.vericekici_updated.ensure_project_venv"):
            argv = [
                "vericekici_updated.py", "--dataset-profile", "rs_v2_sip",
                "--adjustment", "all", "--feed", "sip",
                "--start", "2024-01-02", "--end", "2024-01-05",
                "--output-root", temp, "--symbols", "AAPL",
                "--dry-run",
            ]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
                main()
            self.assertFalse((DEFAULT_V2_FROZEN_ROOT / "dry_run_reports").exists())


if __name__ == "__main__":
    unittest.main()
