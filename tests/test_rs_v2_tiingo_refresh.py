"""Tests for the rs_v2_tiingo --refresh-daily path (Phase 6B).

Covers: adjustment-drift boundary (0.49% must NOT trigger, 0.51% must
trigger with full telemetry), CLI guard rejections, resumability (no
refetch of already-refreshed symbols), registry-driven terminal skip, and
subset-mode never finalizing a manifest.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

from testdata.tiingo_provider import (
    ADJUSTMENT_DRIFT_THRESHOLD,
    _merge_new_sessions,
    _session_midnight_utc,
    _terminal_symbols,
    is_adjustment_drift,
    measure_adjustment_drift,
)
from testdata.vericekici_updated import _run_tiingo_profile, build_parser
from testdata.v2_session_coverage import load_reviewed_coverage_exceptions


def _frame(prices: dict[str, float]) -> pd.DataFrame:
    dates = [_session_midnight_utc(day) for day in prices]
    values = list(prices.values())
    return pd.DataFrame({
        "Date": dates,
        "Open": values,
        "High": [v * 1.01 for v in values],
        "Low": [v * 0.99 for v in values],
        "Close": values,
        "Volume": [100.0] * len(values),
    })


class TestAdjustmentDriftBoundary(unittest.TestCase):
    def test_drift_at_0_49_pct_does_not_trigger(self):
        existing = _frame({"2026-07-20": 100.0, "2026-07-21": 100.0})
        refreshed = _frame({"2026-07-20": 100.0, "2026-07-21": 100.0})
        # 0.49% relative to the refreshed value.
        existing.loc[1, "Close"] = 100.49
        refreshed.loc[1, "Close"] = 100.0
        drift = measure_adjustment_drift(existing, refreshed)
        self.assertAlmostEqual(drift["max_drift_ratio"], 0.0049, places=6)
        self.assertLess(drift["max_drift_ratio"], ADJUSTMENT_DRIFT_THRESHOLD)
        self.assertFalse(is_adjustment_drift(drift))

    def test_drift_at_0_51_pct_triggers_with_full_telemetry(self):
        existing = _frame({"2026-07-20": 100.0, "2026-07-21": 100.0})
        refreshed = _frame({"2026-07-20": 100.0, "2026-07-21": 100.0})
        # 0.51% relative to the refreshed value.
        existing.loc[1, "Close"] = 100.51
        refreshed.loc[1, "Close"] = 100.0
        drift = measure_adjustment_drift(existing, refreshed)
        self.assertAlmostEqual(drift["max_drift_ratio"], 0.0051, places=6)
        self.assertTrue(is_adjustment_drift(drift))
        # Telemetry gap fix: triggering percentage and before/after values retrievable.
        self.assertEqual(drift["drift_session"], "2026-07-21")
        self.assertEqual(drift["drift_column"], "Close")
        self.assertEqual(drift["drift_value_existing"], 100.51)
        self.assertEqual(drift["drift_value_refreshed"], 100.0)
        self.assertEqual(drift["common_session_count"], 2)

    def test_no_common_sessions_reports_zero_drift(self):
        existing = _frame({"2026-07-20": 100.0})
        refreshed = _frame({"2026-07-22": 100.0})
        drift = measure_adjustment_drift(existing, refreshed)
        self.assertEqual(drift["max_drift_ratio"], 0.0)
        self.assertFalse(is_adjustment_drift(drift))


class TestMergeNewSessions(unittest.TestCase):
    def test_appends_only_sessions_after_existing_last(self):
        existing = _frame({"2026-07-20": 100.0, "2026-07-21": 101.0})
        window = _frame({"2026-07-20": 100.0, "2026-07-21": 101.0, "2026-07-22": 102.0, "2026-07-23": 103.0})
        merged = _merge_new_sessions(existing, window)
        self.assertEqual(len(merged), 4)
        self.assertEqual(
            pd.to_datetime(merged["Date"], utc=True).dt.strftime("%Y-%m-%d").tolist(),
            ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"],
        )

    def test_no_new_sessions_returns_existing_unchanged(self):
        existing = _frame({"2026-07-20": 100.0, "2026-07-21": 101.0})
        window = _frame({"2026-07-20": 100.0, "2026-07-21": 101.0})
        merged = _merge_new_sessions(existing, window)
        self.assertEqual(len(merged), 2)


class TestTerminalSkipIsRegistryDriven(unittest.TestCase):
    def test_terminal_set_comes_from_registry_not_literals(self):
        registry_path = Path(__file__).resolve().parent.parent / "testdata" / "baselines" / "rs_v2_tiingo_coverage_exceptions.json"
        exceptions = load_reviewed_coverage_exceptions(registry_path)
        terminal = _terminal_symbols(["PARA", "AAPL", "WBD"], exceptions, "2026-07-23")
        self.assertEqual(terminal, {"PARA"})

    def test_no_exceptions_means_no_skips(self):
        self.assertEqual(_terminal_symbols(["PARA", "AAPL"], [], "2026-07-23"), set())


class TestRefreshGuards(unittest.TestCase):
    def _args(self, extra):
        parser = build_parser()
        return parser.parse_args(["--dataset-profile", "rs_v2_tiingo", *extra])

    def test_refresh_forbids_dry_run(self):
        with self.assertRaisesRegex(ValueError, "V2_TIINGO_REFRESH_FORBIDS_DRY_RUN_AND_IMPORT"):
            _run_tiingo_profile(self._args(["--refresh-daily", "--dry-run"]))

    def test_refresh_forbids_import_source(self):
        with self.assertRaisesRegex(ValueError, "V2_TIINGO_REFRESH_FORBIDS_DRY_RUN_AND_IMPORT"):
            _run_tiingo_profile(self._args(["--refresh-daily", "--import-source-root", "somewhere"]))

    def test_refresh_forbids_refresh_latest(self):
        with self.assertRaisesRegex(ValueError, "V2_TIINGO_UNSUPPORTED_MODE_FLAG"):
            _run_tiingo_profile(self._args(["--refresh-daily", "--refresh-latest"]))

    def test_refresh_forbids_skip_download(self):
        with self.assertRaisesRegex(ValueError, "V2_TIINGO_UNSUPPORTED_MODE_FLAG"):
            _run_tiingo_profile(self._args(["--refresh-daily", "--skip-download"]))

    def test_refresh_forbids_only_aggregate(self):
        with self.assertRaisesRegex(ValueError, "V2_TIINGO_UNSUPPORTED_MODE_FLAG"):
            _run_tiingo_profile(self._args(["--refresh-daily", "--only-aggregate-130min"]))

    def test_wrong_feed_rejected(self):
        with self.assertRaisesRegex(ValueError, "V2_TIINGO_REQUIRES_FEED_TIINGO"):
            _run_tiingo_profile(self._args(["--refresh-daily", "--feed", "sip"]))

    def test_subset_refresh_rejects_default_production_root(self):
        with self.assertRaisesRegex(ValueError, "V2_TIINGO_SUBSET_REFRESH_REQUIRES_NON_DEFAULT_OUTPUT_ROOT"):
            _run_tiingo_profile(self._args(["--refresh-daily", "--symbols", "AAPL"]))


class TestRefreshResumability(unittest.TestCase):
    def test_already_refreshed_symbol_is_not_refetched(self):
        from testdata import tiingo_provider

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            existing = _frame({"2026-07-20": 100.0, "2026-07-21": 101.0, "2026-07-22": 102.0, "2026-07-23": 103.0})
            daily_dir = root / "daily"
            daily_dir.mkdir(parents=True)
            destination = daily_dir / "AAPL_1D.csv"
            existing.to_csv(destination, index=False)
            from testdata.vericekici_updated import verify_file_integrity
            sha = verify_file_integrity(destination)["sha256"]
            manifests = root / "manifests"
            manifests.mkdir(parents=True)
            state = {
                "acquisition_run_id": "acq_test",
                "status": "IN_PROGRESS",
                "started_at": "2026-07-24T00:00:00Z",
                "symbols": {"AAPL": {"sha256": sha, "refresh_end": "2026-07-23", "provider_result_status": "REFRESHED", "adjustment_drift_detected": False, "status": "REFRESHED"}},
            }
            (manifests / "tiingo_refresh_state.json").write_text(json.dumps(state), encoding="utf-8")

            def _must_not_fetch(*args, **kwargs):
                raise AssertionError("refetch attempted for an already-refreshed symbol")

            with patch.object(tiingo_provider, "fetch_tiingo_daily_bars", _must_not_fetch), \
                 patch.object(tiingo_provider, "get_tiingo_api_key", lambda: "test-key"):
                result = tiingo_provider.run_v2_tiingo_daily_refresh(
                    root, symbols_subset=["AAPL"], target_end="2026-07-23", pace_delay_seconds=0.0,
                )
            self.assertEqual(result["status"], "PARTIAL_VERIFICATION_NO_FINALIZE")
            self.assertEqual(result["provider_attempt_count"], 0)

    def test_subset_mode_never_finalizes(self):
        from testdata import tiingo_provider

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            existing = _frame({"2026-07-20": 100.0, "2026-07-21": 101.0})
            daily_dir = root / "daily"
            daily_dir.mkdir(parents=True)
            existing.to_csv(daily_dir / "AAPL_1D.csv", index=False)
            window = _frame({"2026-07-20": 100.0, "2026-07-21": 101.0, "2026-07-22": 102.0, "2026-07-23": 103.0})

            with patch.object(tiingo_provider, "fetch_tiingo_daily_bars", lambda *a, **k: window.copy()), \
                 patch.object(tiingo_provider, "get_tiingo_api_key", lambda: "test-key"):
                result = tiingo_provider.run_v2_tiingo_daily_refresh(
                    root, symbols_subset=["AAPL"], target_end="2026-07-23", pace_delay_seconds=0.0,
                )
            self.assertEqual(result["status"], "PARTIAL_VERIFICATION_NO_FINALIZE")
            self.assertFalse((root / "manifests" / "latest_manifest.json").exists())
            refreshed = pd.read_csv(daily_dir / "AAPL_1D.csv")
            self.assertEqual(len(refreshed), 4)


if __name__ == "__main__":
    unittest.main()
