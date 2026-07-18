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
    DownloadBarsResult,
    _download_symbol_worker,
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

    def test_live_requires_overwrite(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "sip", "--start", "2020-07-27", "--end", "2026-07-07", "--output-root", "unused", "--live"]
        self._run(argv, "initial acquisition requires --overwrite")

    def test_live_forbids_refresh_skip_aggregate_only(self):
        # Regression coverage for the ec47f13 incident: this guard was once silently
        # commented out under an unrelated commit message, and a test that only
        # asserted the ValueError (without blocking the network) let main() fall
        # through into a real ~180-symbol live download that hung the test suite for
        # 1.5-2h. Network/credentials are hard-mocked here so a guard regression fails
        # fast with an assertion error instead of ever reaching real I/O again.
        with tempfile.TemporaryDirectory() as temp:
            argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "sip", "--start", "2020-07-27", "--end", "2026-07-07", "--output-root", temp, "--live", "--overwrite", "--refresh-latest"]
            with patch("testdata.vericekici_updated.ensure_project_venv"), \
                 patch("testdata.vericekici_updated.get_credentials") as credentials, \
                 patch("testdata.vericekici_updated._load_alpaca") as alpaca, \
                 patch("testdata.vericekici_updated.download_bars") as download, \
                 patch.object(sys, "argv", argv), \
                 self.assertRaisesRegex(ValueError, "initial acquisition forbids refresh/skip/aggregate-only"):
                main()
            credentials.assert_not_called()
            alpaca.assert_not_called()
            download.assert_not_called()

    def test_live_requires_target_dates(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--adjustment", "all", "--feed", "sip", "--start", "2024-01-02", "--end", "2024-01-05", "--output-root", "unused", "--live", "--overwrite"]
        self._run(argv, "target dates must be start=2020-07-27, end=2026-07-07")


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



class TestSipAdjustmentDriftDetection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp_dir.name)
        
    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("testdata.vericekici_updated.download_bars_with_retry")
    def test_drift_detection_triggers_full_history_reacquisition(self, mock_download):
        symbol = "AAPL"
        daily_path = self.output_root / "daily" / f"{symbol}_1D.csv"
        raw_path = self.output_root / "raw_5min" / f"{symbol}_5min.csv"
        daily_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        
        df_old = pd.DataFrame([
            {"Date": "2026-06-11 04:00:00+00:00", "Open": 46.0, "High": 47.0, "Low": 45.0, "Close": 46.84, "Volume": 10000}
        ])
        df_old.to_csv(daily_path, index=False)
        pd.DataFrame([
            {"Date": "2026-06-11 13:30:00+00:00", "Open": 46.0, "High": 47.0, "Low": 45.0, "Close": 46.84, "Volume": 10000}
        ]).to_csv(raw_path, index=False)
        
        df_new_trailing = pd.DataFrame([
            {"Date": "2026-06-11 04:00:00+00:00", "Open": 140.0, "High": 141.0, "Low": 139.0, "Close": 140.52, "Volume": 10000}
        ])
        
        df_new_full_daily = pd.DataFrame([
            {"Date": "2026-06-11 04:00:00+00:00", "Open": 140.0, "High": 141.0, "Low": 139.0, "Close": 140.52, "Volume": 10000},
            {"Date": "2026-06-12 04:00:00+00:00", "Open": 141.0, "High": 142.0, "Low": 140.0, "Close": 141.50, "Volume": 12000}
        ])
        
        df_new_full_raw = pd.DataFrame([
            {"Date": "2026-06-11 13:30:00+00:00", "Open": 140.0, "High": 141.0, "Low": 139.0, "Close": 140.52, "Volume": 10000},
            {"Date": "2026-06-12 13:30:00+00:00", "Open": 141.0, "High": 142.0, "Low": 140.0, "Close": 141.50, "Volume": 12000}
        ])
        
        mock_download.side_effect = [
            DownloadBarsResult(df_new_trailing, 1, 0, 0),
            DownloadBarsResult(df_new_full_daily, 1, 0, 0),
            DownloadBarsResult(df_new_full_raw, 1, 0, 0)
        ]
        
        payload = {
            "symbol": symbol,
            "daily_path": str(daily_path),
            "raw_path": str(raw_path),
            "api_key": "dummy",
            "secret_key": "dummy",
            "feed": "sip",
            "adjustment": "all",
            "day_tf": "1D",
            "min5_tf": "5min",
            "daily_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "intraday_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "daily_end": pd.Timestamp("2026-06-12", tz="UTC").to_pydatetime(),
            "intraday_end": pd.Timestamp("2026-06-12", tz="UTC").to_pydatetime(),
            "refresh_latest": False,
            "refresh_daily": True,
            "drift_check_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "overwrite": False,
            "skip_download": False,
            "allow_row_count_reduction": True
        }
        
        result = _download_symbol_worker(payload)
        
        self.assertEqual(mock_download.call_count, 3)
        self.assertEqual(result["daily_status"], "downloaded")
        self.assertEqual(result["raw_5min_status"], "downloaded")
        
        updated_daily = pd.read_csv(daily_path)
        self.assertEqual(len(updated_daily), 2)
        self.assertEqual(float(updated_daily.iloc[0]["Close"]), 140.52)
        
    @patch("testdata.vericekici_updated.download_bars_with_retry")
    def test_clean_overlap_does_not_trigger_full_history_reacquisition(self, mock_download):
        symbol = "AAPL"
        daily_path = self.output_root / "daily" / f"{symbol}_1D.csv"
        raw_path = self.output_root / "raw_5min" / f"{symbol}_5min.csv"
        daily_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        
        df_old = pd.DataFrame([
            {"Date": "2026-06-11 04:00:00+00:00", "Open": 140.0, "High": 141.0, "Low": 139.0, "Close": 140.52, "Volume": 10000}
        ])
        df_old.to_csv(daily_path, index=False)
        pd.DataFrame([
            {"Date": "2026-06-11 13:30:00+00:00", "Open": 140.0, "High": 141.0, "Low": 139.0, "Close": 140.52, "Volume": 10000}
        ]).to_csv(raw_path, index=False)
        
        df_new_trailing = pd.DataFrame([
            {"Date": "2026-06-11 04:00:00+00:00", "Open": 140.0, "High": 141.0, "Low": 139.0, "Close": 140.52, "Volume": 10000}
        ])
        
        df_incremental_daily = pd.DataFrame([
            {"Date": "2026-06-12 04:00:00+00:00", "Open": 141.0, "High": 142.0, "Low": 140.0, "Close": 141.50, "Volume": 12000}
        ])
        
        df_incremental_raw = pd.DataFrame([
            {"Date": "2026-06-12 13:30:00+00:00", "Open": 141.0, "High": 142.0, "Low": 140.0, "Close": 141.50, "Volume": 12000}
        ])
        
        mock_download.side_effect = [
            DownloadBarsResult(df_new_trailing, 1, 0, 0),
            DownloadBarsResult(df_incremental_daily, 1, 0, 0),
            DownloadBarsResult(df_incremental_raw, 1, 0, 0)
        ]
        
        payload = {
            "symbol": symbol,
            "daily_path": str(daily_path),
            "raw_path": str(raw_path),
            "api_key": "dummy",
            "secret_key": "dummy",
            "feed": "sip",
            "adjustment": "all",
            "day_tf": "1D",
            "min5_tf": "5min",
            "daily_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "intraday_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "daily_end": pd.Timestamp("2026-06-12", tz="UTC").to_pydatetime(),
            "intraday_end": pd.Timestamp("2026-06-12", tz="UTC").to_pydatetime(),
            "refresh_latest": False,
            "refresh_daily": True,
            "drift_check_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "overwrite": False,
            "skip_download": False,
            "allow_row_count_reduction": True
        }
        
        result = _download_symbol_worker(payload)
        
        self.assertEqual(mock_download.call_count, 3)
        self.assertEqual(result["daily_status"], "refreshed")
        self.assertEqual(result["raw_5min_status"], "refreshed")
        
        updated_daily = pd.read_csv(daily_path)
        self.assertEqual(len(updated_daily), 2)
        self.assertEqual(float(updated_daily.iloc[0]["Close"]), 140.52)
        self.assertEqual(float(updated_daily.iloc[1]["Close"]), 141.50)

    @patch("testdata.vericekici_updated.download_bars_with_retry")
    def test_drift_below_threshold_does_not_trigger_full_reacquisition(self, mock_download):
        # Boundary check: 0.49% relative diff must NOT trigger (threshold is > 0.5%).
        symbol = "MSFT"
        daily_path = self.output_root / "daily" / f"{symbol}_1D.csv"
        raw_path = self.output_root / "raw_5min" / f"{symbol}_5min.csv"
        daily_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.parent.mkdir(parents=True, exist_ok=True)

        pd.DataFrame([
            {"Date": "2026-06-11 04:00:00+00:00", "Open": 99.51, "High": 99.51, "Low": 99.51, "Close": 99.51, "Volume": 10000}
        ]).to_csv(daily_path, index=False)
        pd.DataFrame([
            {"Date": "2026-06-11 13:30:00+00:00", "Open": 99.51, "High": 99.51, "Low": 99.51, "Close": 99.51, "Volume": 10000}
        ]).to_csv(raw_path, index=False)

        df_new_trailing = pd.DataFrame([
            {"Date": "2026-06-11 04:00:00+00:00", "Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 10000}
        ])
        df_incremental_daily = pd.DataFrame([
            {"Date": "2026-06-12 04:00:00+00:00", "Open": 101.0, "High": 101.0, "Low": 101.0, "Close": 101.0, "Volume": 12000}
        ])
        df_incremental_raw = pd.DataFrame([
            {"Date": "2026-06-12 13:30:00+00:00", "Open": 101.0, "High": 101.0, "Low": 101.0, "Close": 101.0, "Volume": 12000}
        ])

        mock_download.side_effect = [
            DownloadBarsResult(df_new_trailing, 1, 0, 0),
            DownloadBarsResult(df_incremental_daily, 1, 0, 0),
            DownloadBarsResult(df_incremental_raw, 1, 0, 0),
        ]

        payload = {
            "symbol": symbol,
            "daily_path": str(daily_path),
            "raw_path": str(raw_path),
            "api_key": "dummy",
            "secret_key": "dummy",
            "feed": "sip",
            "adjustment": "all",
            "day_tf": "1D",
            "min5_tf": "5min",
            "daily_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "intraday_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "daily_end": pd.Timestamp("2026-06-12", tz="UTC").to_pydatetime(),
            "intraday_end": pd.Timestamp("2026-06-12", tz="UTC").to_pydatetime(),
            "refresh_latest": False,
            "refresh_daily": True,
            "drift_check_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "overwrite": False,
            "skip_download": False,
            "allow_row_count_reduction": True,
        }

        result = _download_symbol_worker(payload)

        self.assertEqual(mock_download.call_count, 3)
        self.assertEqual(result["daily_status"], "refreshed")
        updated_daily = pd.read_csv(daily_path)
        self.assertEqual(len(updated_daily), 2)
        self.assertEqual(float(updated_daily.iloc[0]["Close"]), 99.51)

    @patch("testdata.vericekici_updated.download_bars_with_retry")
    def test_drift_above_threshold_triggers_full_reacquisition(self, mock_download):
        # Boundary check: 0.51% relative diff MUST trigger full history re-acquisition.
        symbol = "MSFT"
        daily_path = self.output_root / "daily" / f"{symbol}_1D.csv"
        raw_path = self.output_root / "raw_5min" / f"{symbol}_5min.csv"
        daily_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.parent.mkdir(parents=True, exist_ok=True)

        pd.DataFrame([
            {"Date": "2026-06-11 04:00:00+00:00", "Open": 99.49, "High": 99.49, "Low": 99.49, "Close": 99.49, "Volume": 10000}
        ]).to_csv(daily_path, index=False)
        pd.DataFrame([
            {"Date": "2026-06-11 13:30:00+00:00", "Open": 99.49, "High": 99.49, "Low": 99.49, "Close": 99.49, "Volume": 10000}
        ]).to_csv(raw_path, index=False)

        df_new_trailing = pd.DataFrame([
            {"Date": "2026-06-11 04:00:00+00:00", "Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 10000}
        ])
        df_new_full_daily = pd.DataFrame([
            {"Date": "2026-06-11 04:00:00+00:00", "Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 10000},
            {"Date": "2026-06-12 04:00:00+00:00", "Open": 101.0, "High": 101.0, "Low": 101.0, "Close": 101.0, "Volume": 12000},
        ])
        df_new_full_raw = pd.DataFrame([
            {"Date": "2026-06-11 13:30:00+00:00", "Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 10000},
            {"Date": "2026-06-12 13:30:00+00:00", "Open": 101.0, "High": 101.0, "Low": 101.0, "Close": 101.0, "Volume": 12000},
        ])

        mock_download.side_effect = [
            DownloadBarsResult(df_new_trailing, 1, 0, 0),
            DownloadBarsResult(df_new_full_daily, 1, 0, 0),
            DownloadBarsResult(df_new_full_raw, 1, 0, 0),
        ]

        payload = {
            "symbol": symbol,
            "daily_path": str(daily_path),
            "raw_path": str(raw_path),
            "api_key": "dummy",
            "secret_key": "dummy",
            "feed": "sip",
            "adjustment": "all",
            "day_tf": "1D",
            "min5_tf": "5min",
            "daily_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "intraday_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "daily_end": pd.Timestamp("2026-06-12", tz="UTC").to_pydatetime(),
            "intraday_end": pd.Timestamp("2026-06-12", tz="UTC").to_pydatetime(),
            "refresh_latest": False,
            "refresh_daily": True,
            "drift_check_start": pd.Timestamp("2026-06-11", tz="UTC").to_pydatetime(),
            "overwrite": False,
            "skip_download": False,
            "allow_row_count_reduction": True,
        }

        result = _download_symbol_worker(payload)

        self.assertEqual(mock_download.call_count, 3)
        self.assertEqual(result["daily_status"], "downloaded")
        updated_daily = pd.read_csv(daily_path)
        self.assertEqual(len(updated_daily), 2)
        self.assertEqual(float(updated_daily.iloc[0]["Close"]), 100.0)


class TestRefreshDailyGuardEnforcement(unittest.TestCase):
    # Regression coverage for Phase 3A finding #6: --refresh-daily forced args.live = True
    # via a structurally separate branch that never reached the --live safety guard
    # (refresh_latest/skip_download/only_aggregate_130min rejection). Any --refresh-daily
    # invocation combined with those flags, or with --dry-run, must STOP fail-closed
    # before any network/credentials access.
    def _run(self, argv, expected_message):
        with patch("testdata.vericekici_updated.ensure_project_venv"), \
             patch("testdata.vericekici_updated.get_credentials") as credentials, \
             patch("testdata.vericekici_updated._load_alpaca") as alpaca, \
             patch("testdata.vericekici_updated.download_bars") as download, \
             patch.object(sys, "argv", argv), \
             self.assertRaisesRegex(ValueError, expected_message):
            main()
        credentials.assert_not_called()
        alpaca.assert_not_called()
        download.assert_not_called()

    def test_refresh_daily_forbids_skip_download(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--refresh-daily", "--skip-download"]
        self._run(argv, "forbids --skip-download and --only-aggregate-130min")

    def test_refresh_daily_forbids_only_aggregate_130min(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--refresh-daily", "--only-aggregate-130min"]
        self._run(argv, "forbids --skip-download and --only-aggregate-130min")

    def test_refresh_daily_forbids_dry_run(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--refresh-daily", "--dry-run"]
        self._run(argv, "forbids --dry-run")

    def test_refresh_daily_forbids_refresh_latest(self):
        argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_sip", "--refresh-daily", "--refresh-latest"]
        self._run(argv, "Cannot specify both --refresh-daily and --refresh-latest")


if __name__ == "__main__":
    unittest.main()
