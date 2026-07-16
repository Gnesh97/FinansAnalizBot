from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from testdata.vericekici_updated import (
    GAP_RECOVERY_APPROVED_DATES,
    GAP_RECOVERY_EXPECTED_PAIR_COUNT,
    build_gap_recovery_targets,
    DownloadBarsResult,
    filter_internal_daily_gaps,
    _request_gap_recovery_bars,
    resolve_gap_recovery_run_paths,
    validate_daily_recovery,
    validate_gap_recovery_invocation,
    validate_raw_recovery,
)


ROOT = Path(__file__).resolve().parents[1]
FROZEN_ROOT = ROOT / "testdata" / "research_data" / "rs_v2_clean_market_data"
AUDIT_RUN = "acq_20260712_212749"
SESSION_FIXTURE = ROOT / "tests" / "fixtures" / "nyse_sessions_2020-07-27_2026-07-07.csv"


def _bars(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "Date": dates,
        "Open": [10.0] * len(dates),
        "High": [11.0] * len(dates),
        "Low": [9.0] * len(dates),
        "Close": [10.5] * len(dates),
        "Volume": [100] * len(dates),
    })


def _approved_schedule() -> pd.DataFrame:
    fixture = pd.read_csv(SESSION_FIXTURE)
    index = pd.DatetimeIndex(pd.to_datetime(fixture["session"]))
    return pd.DataFrame({
        "market_open": pd.to_datetime(fixture["session"] + " 09:30").dt.tz_localize("America/New_York"),
        "market_close": pd.to_datetime(fixture["session"] + " " + fixture["market_close_time"]).dt.tz_localize("America/New_York"),
    }, index=index)


class TestRsV2GapRecoveryProbe(unittest.TestCase):
    def test_frozen_targets_are_exact_and_exclude_benchmarks(self):
        targets, _ = build_gap_recovery_targets(FROZEN_ROOT, AUDIT_RUN, _approved_schedule())
        self.assertEqual(len(targets), GAP_RECOVERY_EXPECTED_PAIR_COUNT)
        self.assertEqual(len({row["symbol"] for row in targets}), 123)
        self.assertEqual({row["target_session"] for row in targets}, set(GAP_RECOVERY_APPROVED_DATES))
        self.assertFalse({"QQQ", "SPY"} & {row["symbol"] for row in targets})

    def test_only_approved_dates_survive_target_filter(self):
        schedule = pd.DatetimeIndex(pd.to_datetime(["2021-04-19", "2021-10-25", "2022-03-08", "2022-04-06", "2022-05-05"]))
        present = {pd.Timestamp("2022-05-05")}
        gaps = filter_internal_daily_gaps(present, schedule, pd.Timestamp("2021-04-19"), pd.Timestamp("2022-05-05"))
        self.assertEqual({str(value.date()) for value in gaps}, set(GAP_RECOVERY_APPROVED_DATES))

    def test_listing_boundary_and_present_sessions_are_excluded(self):
        schedule = pd.DatetimeIndex(pd.to_datetime(["2021-04-16", "2021-04-19", "2021-04-20", "2021-04-21"]))
        gaps = filter_internal_daily_gaps({pd.Timestamp("2021-04-20")}, schedule, pd.Timestamp("2021-04-19"), pd.Timestamp("2021-04-20"))
        self.assertEqual([str(value.date()) for value in gaps], ["2021-04-19"])

    def test_invocation_aborts_on_wrong_target_count_before_network(self):
        with self.assertRaisesRegex(ValueError, "GAP_RECOVERY_TARGET_CONTRACT_MISMATCH"):
            validate_gap_recovery_invocation(
                dataset_profile="rs_v2_clean", feed="iex", adjustment="all", audit_run=AUDIT_RUN,
                frozen_root=FROZEN_ROOT, staging_root=FROZEN_ROOT.parent / "staging", targets=[{}],
            )

    def test_staging_root_cannot_be_inside_frozen_dataset(self):
        with self.assertRaisesRegex(ValueError, "GAP_RECOVERY_STAGING_OVERLAPS_FROZEN"):
            resolve_gap_recovery_run_paths(FROZEN_ROOT, FROZEN_ROOT / "staging", "gaprec_test")

    def test_daily_empty_and_wrong_session_are_not_recovered(self):
        status, staged = validate_daily_recovery(pd.DataFrame(), "2021-04-19")
        self.assertEqual(status, "DAILY_EMPTY_RESPONSE")
        self.assertTrue(staged.empty)
        status, staged = validate_daily_recovery(_bars(["2021-04-20 04:00:00+00:00"]), "2021-04-19")
        self.assertEqual(status, "DAILY_WRONG_SESSION")
        self.assertTrue(staged.empty)

    def test_daily_duplicate_and_invalid_ohlcv_are_rejected(self):
        status, _ = validate_daily_recovery(_bars(["2021-04-19 04:00:00+00:00", "2021-04-19 20:00:00+00:00"]), "2021-04-19")
        self.assertEqual(status, "DAILY_DUPLICATE_SESSION")
        invalid = _bars(["2021-04-19 04:00:00+00:00"])
        invalid.loc[0, "Low"] = 12.0
        status, _ = validate_daily_recovery(invalid, "2021-04-19")
        self.assertEqual(status, "DAILY_INVALID_OHLCV")

    def test_raw_empty_duplicate_and_premarket_are_not_success(self):
        market_open = pd.Timestamp("2021-04-19 09:30", tz="America/New_York")
        market_close = pd.Timestamp("2021-04-19 16:00", tz="America/New_York")
        status, staged = validate_raw_recovery(pd.DataFrame(), "2021-04-19", market_open, market_close)
        self.assertEqual(status, "RAW_EMPTY_RESPONSE")
        self.assertTrue(staged.empty)
        premarket = _bars(["2021-04-19 13:00:00+00:00"])
        status, staged = validate_raw_recovery(premarket, "2021-04-19", market_open, market_close)
        self.assertEqual(status, "RAW_EMPTY_RESPONSE")
        self.assertTrue(staged.empty)
        duplicate = _bars(["2021-04-19 13:30:00+00:00", "2021-04-19 13:30:00+00:00"])
        status, _ = validate_raw_recovery(duplicate, "2021-04-19", market_open, market_close)
        self.assertEqual(status, "RAW_INVALID_TIMESTAMPS")

    def test_early_close_rth_is_valid_without_theoretical_bar_requirement(self):
        market_open = pd.Timestamp("2021-11-26 09:30", tz="America/New_York")
        market_close = pd.Timestamp("2021-11-26 13:00", tz="America/New_York")
        raw = _bars(["2021-11-26 14:30:00+00:00", "2021-11-26 17:55:00+00:00", "2021-11-26 18:00:00+00:00"])
        status, staged = validate_raw_recovery(raw, "2021-11-26", market_open, market_close)
        self.assertEqual(status, "RAW_RECOVERED_COMPLETE")
        self.assertEqual(len(staged), 2)

    def test_frozen_manifest_and_production_daily_remain_unchanged_during_derivation(self):
        manifest = FROZEN_ROOT / "manifests" / AUDIT_RUN / "acquisition_manifest.json"
        daily = FROZEN_ROOT / "daily" / "AAPL_1D.csv"
        latest = FROZEN_ROOT / "manifests" / "latest_manifest.json"
        before = (hashlib.sha256(manifest.read_bytes()).hexdigest(), hashlib.sha256(daily.read_bytes()).hexdigest(), hashlib.sha256(latest.read_bytes()).hexdigest() if latest.exists() else "MISSING")
        build_gap_recovery_targets(FROZEN_ROOT, AUDIT_RUN, _approved_schedule())
        after = (hashlib.sha256(manifest.read_bytes()).hexdigest(), hashlib.sha256(daily.read_bytes()).hexdigest(), hashlib.sha256(latest.read_bytes()).hexdigest() if latest.exists() else "MISSING")
        self.assertEqual(before, after)

    def test_retry_telemetry_uses_provider_attempts(self):
        frame = _bars(["2021-04-19 04:00:00+00:00"])
        with patch("testdata.vericekici_updated.download_bars_with_retry", return_value=DownloadBarsResult(frame, 2, 1, 1)):
            _, attempts, retries, error_type, _ = _request_gap_recovery_bars("key", "secret", "AAPL", pd.Timestamp("2021-04-18", tz="UTC").to_pydatetime(), pd.Timestamp("2021-04-21", tz="UTC").to_pydatetime(), object(), object(), object())
        self.assertEqual((attempts, retries, error_type), (2, 1, ""))


if __name__ == "__main__":
    unittest.main()
