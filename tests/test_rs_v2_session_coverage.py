from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

from testdata.v2_session_coverage import (  # noqa: E402
    coverage_exception_evidence_hash,
    calculate_v2_session_coverage,
    load_reviewed_coverage_exceptions,
    validate_v2_file_coverage,
    validate_v2_symbol_session_ranges,
)
from testdata.vericekici_updated import (  # noqa: E402
    download_bars_with_retry,
    finalize_v2_manifest,
    validate_v2_retry_telemetry,
    write_v2_coverage_review_reports,
)


def _schedule(count: int = 130) -> pd.DataFrame:
    sessions = list(pd.bdate_range("2024-01-02", periods=count - 1)) + [pd.Timestamp("2024-12-31")]
    opens = [day.tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30) for day in sessions]
    closes = [day.tz_localize("America/New_York") + pd.Timedelta(hours=16) for day in sessions]
    if count > 10:
        closes[10] = sessions[10].tz_localize("America/New_York") + pd.Timedelta(hours=13)
    return pd.DataFrame({"market_open": opens, "market_close": closes}, index=pd.DatetimeIndex(sessions))


def _daily(schedule: pd.DataFrame, sessions=None) -> pd.DataFrame:
    chosen = list(schedule.index if sessions is None else sessions)
    dates = [pd.Timestamp(day).tz_localize("America/New_York") + pd.Timedelta(hours=16) for day in chosen]
    size = len(dates)
    return pd.DataFrame({"Date": dates, "Open": [10.0] * size, "High": [11.0] * size, "Low": [9.0] * size, "Close": [10.5] * size, "Volume": [100] * size})


def _intraday(schedule: pd.DataFrame, sessions=None) -> pd.DataFrame:
    chosen = list(schedule.index if sessions is None else sessions)
    dates = []
    for day in chosen:
        row = schedule.loc[day]
        current = pd.Timestamp(row["market_open"])
        close = pd.Timestamp(row["market_close"])
        while current < close:
            dates.append(current)
            current += pd.Timedelta(minutes=130)
    size = len(dates)
    return pd.DataFrame({"Date": dates, "Open": [10.0] * size, "High": [11.0] * size, "Low": [9.0] * size, "Close": [10.5] * size, "Volume": [100] * size})


def _coverage(frame, timeframe, schedule, exceptions=()):
    return calculate_v2_session_coverage(
        frame,
        timeframe=timeframe,
        symbol="QQQ",
        target_start="2024-01-02",
        target_end="2024-12-31",
        market_schedule=schedule,
        reviewed_exceptions=list(exceptions),
    )


def _exception(exception_type: str, boundary: str) -> dict:
    item = {
        "symbol": "QQQ",
        "exception_type": exception_type,
        "reason": "Independent listing-history review",
        "evidence_source": "reviewed_fixture_registry",
        "evidence_date": "2024-01-01",
        "reviewed": True,
    }
    item["approved_first_session" if exception_type == "LATE_START" else "approved_last_session"] = boundary
    item["evidence_hash"] = coverage_exception_evidence_hash(item)
    return item


class TestV2SessionCoverage(unittest.TestCase):
    @staticmethod
    def _pending_manifest():
        return {
            "status": "VALIDATION_PENDING",
            "files": [
                {"symbol": f"S{index}", "timeframe": "1D", "output_relative_path": f"f{index}.csv"}
                for index in range(360)
            ],
        }

    def test_one_day_sixty_row_pseudo_dataset_fails(self):
        schedule = _schedule()
        frame = pd.concat([_intraday(schedule.iloc[:1])] * 20, ignore_index=True)
        result = _coverage(frame, "130min", schedule)
        self.assertEqual(result["coverage_status"], "FAIL")
        self.assertEqual(result["intraday_session_count"], 1)
        self.assertEqual(result["intraday_bar_count"], 60)

    def test_missing_coverage_fields_fail(self):
        schedule = _schedule()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "QQQ_1D.csv"
            _daily(schedule).to_csv(path, index=False)
            record = _coverage(_daily(schedule), "1D", schedule)
            record.pop("missing_internal_session_count")
            with self.assertRaisesRegex(ValueError, "coverage metadata"):
                validate_v2_file_coverage(path, record, "QQQ", "1D", "2024-01-02", "2024-12-31", market_schedule=schedule)

    def test_manifest_coverage_metadata_cannot_override_file(self):
        schedule = _schedule()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "QQQ_1D.csv"
            _daily(schedule.iloc[:1]).to_csv(path, index=False)
            forged = _coverage(_daily(schedule), "1D", schedule)
            with self.assertRaisesRegex(ValueError, "coverage"):
                validate_v2_file_coverage(path, forged, "QQQ", "1D", "2024-01-02", "2024-12-31", market_schedule=schedule)

    def test_truncated_qqq_daily_fails(self):
        result = _coverage(_daily(_schedule().iloc[:60]), "1D", _schedule())
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_truncated_qqq_130min_fails(self):
        result = _coverage(_intraday(_schedule().iloc[:60]), "130min", _schedule())
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_missing_internal_daily_session_fails(self):
        schedule = _schedule()
        frame = _daily(schedule, schedule.index.delete(50))
        result = _coverage(frame, "1D", schedule)
        self.assertEqual(result["missing_internal_session_count"], 1)
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_missing_internal_intraday_session_fails(self):
        schedule = _schedule()
        frame = _intraday(schedule, schedule.index.delete(50))
        result = _coverage(frame, "130min", schedule)
        self.assertEqual(result["missing_internal_session_count"], 1)
        self.assertGreaterEqual(result["missing_intraday_bar_count"], 3)

    def test_invalid_130min_schedule_fails(self):
        schedule = _schedule()
        frame = _intraday(schedule)
        frame.loc[10, "Date"] = pd.Timestamp(frame.loc[10, "Date"]) + pd.Timedelta(minutes=5)
        result = _coverage(frame, "130min", schedule)
        self.assertEqual(result["invalid_intraday_schedule_count"], 1)
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_early_close_session_expects_two_start_stamped_bars(self):
        schedule = _schedule()
        result = _coverage(_intraday(schedule), "130min", schedule)
        self.assertEqual(result["expected_intraday_bar_count"], (len(schedule) * 3) - 1)
        self.assertEqual(result["coverage_status"], "VALID")

    def test_duplicate_daily_session_fails(self):
        schedule = _schedule()
        frame = pd.concat([_daily(schedule), _daily(schedule.iloc[:1])], ignore_index=True)
        result = _coverage(frame, "1D", schedule)
        self.assertIn("DUPLICATE_DAILY_SESSION", result["coverage_exception_reason"])
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_weekend_session_fails(self):
        schedule = _schedule()
        frame = _daily(schedule)
        weekend = frame.iloc[[0]].copy()
        weekend["Date"] = pd.Timestamp("2024-01-06 16:00", tz="America/New_York")
        result = _coverage(pd.concat([frame, weekend], ignore_index=True), "1D", schedule)
        self.assertEqual(result["unexpected_session_count"], 1)
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_unapproved_late_start_fails(self):
        schedule = _schedule()
        result = _coverage(_daily(schedule.iloc[1:]), "1D", schedule)
        self.assertIn("UNAPPROVED_LATE_START", result["coverage_exception_reason"])
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_former_boundary_symbol_without_reviewed_exception_still_fails(self):
        # Regression coverage for the 778555d incident: ABNB was one of 7 symbols
        # hardcoded as unconditionally "approved" for LATE_START/EARLY_TERMINAL with
        # no reviewed-exception record backing it. This asserts that with zero
        # exceptions present, a symbol from that former hardcoded set gets no special
        # treatment and fails exactly like any other symbol would.
        schedule = _schedule()
        frame = _daily(schedule.iloc[1:])
        result = calculate_v2_session_coverage(
            frame, timeframe="1D", symbol="ABNB",
            target_start="2024-01-02", target_end="2024-12-31",
            market_schedule=schedule, reviewed_exceptions=[],
        )
        reasons = result["coverage_exception_reason"].split(";")
        self.assertIn("UNAPPROVED_LATE_START", reasons)
        self.assertNotIn("APPROVED_LATE_START", reasons)
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_approved_late_start_relaxes_only_start_boundary(self):
        schedule = _schedule()
        first = str(schedule.index[1].date())
        result = _coverage(_daily(schedule.iloc[1:]), "1D", schedule, [_exception("LATE_START", first)])
        self.assertEqual(result["start_gap_sessions"], 1)
        self.assertEqual(result["coverage_status"], "VALID")
        broken = _daily(schedule.iloc[1:], schedule.index[1:].delete(50))
        self.assertEqual(_coverage(broken, "1D", schedule, [_exception("LATE_START", first)])["coverage_status"], "FAIL")

    def test_unapproved_early_terminal_fails(self):
        schedule = _schedule()
        result = _coverage(_daily(schedule.iloc[:-1]), "1D", schedule)
        self.assertIn("UNAPPROVED_EARLY_TERMINAL", result["coverage_exception_reason"])
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_approved_early_terminal_relaxes_only_end_boundary(self):
        schedule = _schedule()
        last = str(schedule.index[-2].date())
        result = _coverage(_daily(schedule.iloc[:-1]), "1D", schedule, [_exception("EARLY_TERMINAL", last)])
        self.assertEqual(result["end_gap_sessions"], 1)
        self.assertEqual(result["coverage_status"], "VALID")
        broken = _daily(schedule.iloc[:-1], schedule.index[:-1].delete(50))
        self.assertEqual(_coverage(broken, "1D", schedule, [_exception("EARLY_TERMINAL", last)])["coverage_status"], "FAIL")

    def test_insufficient_warmup_fails(self):
        schedule = _schedule(100)
        result = _coverage(_daily(schedule), "1D", schedule)
        self.assertFalse(result["warmup_history_sufficient"])
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_failed_validation_writes_failed_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "manifests" / "r1" / "acquisition_manifest.json"
            latest = root / "manifests" / "latest_manifest.json"
            manifest = self._pending_manifest()
            with patch("testdata.vericekici_updated.validate_v2_manifest_and_files", side_effect=ValueError("coverage bad")), self.assertRaisesRegex(ValueError, "coverage bad"):
                finalize_v2_manifest(manifest, run, latest)
            failed = json.loads(run.read_text(encoding="utf-8"))
            self.assertEqual(failed["status"], "FAILED_VALIDATION")
            self.assertEqual(failed["validation_error_type"], "ValueError")
            self.assertEqual(failed["validation_error_message"], "coverage bad")
            self.assertTrue(failed["validation_failed_at"].endswith("Z"))

    def test_failed_validation_preserves_previous_latest_pointer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "manifests" / "r1" / "acquisition_manifest.json"
            latest = root / "manifests" / "latest_manifest.json"
            latest.parent.mkdir(parents=True)
            latest.write_text('{"stable": true}\n', encoding="utf-8")
            before = latest.read_bytes()
            manifest = self._pending_manifest()
            with patch("testdata.vericekici_updated.validate_v2_manifest_and_files", side_effect=ValueError("coverage bad")), self.assertRaises(ValueError):
                finalize_v2_manifest(manifest, run, latest)
            self.assertEqual(latest.read_bytes(), before)

    def test_valid_session_complete_fixture_passes(self):
        schedule = _schedule()
        daily = _coverage(_daily(schedule), "1D", schedule)
        intraday = _coverage(_intraday(schedule), "130min", schedule)
        self.assertEqual(daily["coverage_status"], "VALID")
        self.assertEqual(intraday["coverage_status"], "VALID")
        self.assertEqual(intraday["intraday_bar_count"], intraday["expected_intraday_bar_count"])

    def test_retry_telemetry_is_measured_or_marked_unavailable(self):
        frame = pd.DataFrame({"Date": []})
        with patch("testdata.vericekici_updated._load_alpaca", return_value=(None, None, lambda *_: object(), None, None, None)), patch("testdata.vericekici_updated.download_bars", side_effect=[RuntimeError("one"), frame]), patch("testdata.vericekici_updated.time.sleep"):
            result = download_bars_with_retry("k", "s", "QQQ", None, None, None, None, None, False)
        self.assertEqual(result.provider_attempt_count, 2)
        self.assertEqual(result.retry_attempt_count, 1)
        self.assertEqual(result.failed_provider_attempt_count, 1)
        self.assertIs(result.frame, frame)

    def test_missing_regular_session_block_fails(self):
        schedule = _schedule()
        frame = _intraday(schedule).drop(index=20).reset_index(drop=True)
        result = _coverage(frame, "130min", schedule)
        self.assertEqual(result["missing_intraday_bar_count"], 1)
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_unexpected_intraday_block_fails(self):
        schedule = _schedule()
        frame = _intraday(schedule)
        extra = frame.iloc[[0]].copy()
        extra["Date"] = pd.Timestamp(extra.iloc[0]["Date"]) + pd.Timedelta(minutes=5)
        result = _coverage(pd.concat([frame, extra], ignore_index=True), "130min", schedule)
        self.assertEqual(result["invalid_intraday_schedule_count"], 1)
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_invalid_early_close_extra_block_fails(self):
        schedule = _schedule()
        frame = _intraday(schedule)
        early_day = schedule.index[10].tz_localize("America/New_York")
        extra = frame.iloc[[0]].copy()
        extra["Date"] = early_day + pd.Timedelta(hours=13, minutes=50)
        result = _coverage(pd.concat([frame, extra], ignore_index=True), "130min", schedule)
        self.assertEqual(result["invalid_intraday_schedule_count"], 1)

    def test_duplicate_intraday_timestamp_fails(self):
        schedule = _schedule()
        frame = _intraday(schedule)
        result = _coverage(pd.concat([frame, frame.iloc[[0]]], ignore_index=True), "130min", schedule)
        self.assertEqual(result["duplicate_intraday_timestamp_count"], 1)
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_daily_intraday_range_mismatch_fails(self):
        schedule = _schedule()
        daily = _coverage(_daily(schedule), "1D", schedule)
        intraday = _coverage(_intraday(schedule.iloc[1:]), "130min", schedule, [_exception("LATE_START", str(schedule.index[1].date()))])
        with self.assertRaisesRegex(ValueError, "range mismatch"):
            validate_v2_symbol_session_ranges("QQQ", daily, intraday)

    def test_exception_reviewed_false_fails(self):
        schedule = _schedule()
        item = _exception("LATE_START", str(schedule.index[1].date()))
        item["reviewed"] = False
        item["evidence_hash"] = coverage_exception_evidence_hash(item)
        result = _coverage(_daily(schedule.iloc[1:]), "1D", schedule, [item])
        self.assertEqual(result["coverage_status"], "FAIL")

    def test_invalid_exception_evidence_hash_fails(self):
        schedule = _schedule()
        item = _exception("LATE_START", str(schedule.index[1].date()))
        item["evidence_hash"] = "bad"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exceptions.json"
            path.write_text(json.dumps({"schema_version": "1.0.0", "exceptions": [item]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "EXCEPTION_FILE_INVALID"):
                load_reviewed_coverage_exceptions(path)

    def test_reviewed_exception_cannot_bypass_ohlcv_integrity(self):
        schedule = _schedule()
        first = str(schedule.index[1].date())
        frame = _daily(schedule.iloc[1:])
        frame.loc[0, "High"] = 1.0
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "QQQ_1D.csv"
            frame.to_csv(path, index=False)
            record = _coverage(frame, "1D", schedule, [_exception("LATE_START", first)])
            with self.assertRaisesRegex(ValueError, "OHLCV integrity"):
                validate_v2_file_coverage(path, record, "QQQ", "1D", "2024-01-02", "2024-12-31", reviewed_exceptions=[_exception("LATE_START", first)], market_schedule=schedule)

    def test_envelope_broken_rs_v2_clean_path_still_raises(self):
        # Regression coverage for the aef661d incident: a broken OHLCV envelope
        # (here, High < Low) was silently allowed through for any file path
        # containing the substring "rs_v2_clean". This asserts the check applies
        # regardless of path.
        schedule = _schedule()
        first = str(schedule.index[1].date())
        frame = _daily(schedule.iloc[1:])
        frame.loc[0, "High"] = 1.0
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rs_v2_clean_market_data" / "daily" / "QQQ_1D.csv"
            path.parent.mkdir(parents=True)
            frame.to_csv(path, index=False)
            record = _coverage(frame, "1D", schedule, [_exception("LATE_START", first)])
            with self.assertRaisesRegex(ValueError, "OHLCV integrity"):
                validate_v2_file_coverage(path, record, "QQQ", "1D", "2024-01-02", "2024-12-31", reviewed_exceptions=[_exception("LATE_START", first)], market_schedule=schedule)

    def test_coverage_fail_rs_v2_clean_path_still_raises(self):
        # Regression coverage for the c0340ad/506bd3f incident: a coverage_status of
        # FAIL was silently swallowed by the manifest validators (not this function),
        # but the swallow was keyed off the exact string this function raises. This
        # locks in that validate_v2_file_coverage itself always raises on FAIL,
        # including at a path containing "rs_v2_clean", regardless of what any caller
        # does with the exception.
        schedule = _schedule()
        frame = _daily(schedule.iloc[1:])  # unapproved late start -> coverage FAIL
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rs_v2_clean_market_data" / "daily" / "QQQ_1D.csv"
            path.parent.mkdir(parents=True)
            frame.to_csv(path, index=False)
            record = _coverage(frame, "1D", schedule)
            with self.assertRaisesRegex(ValueError, "coverage failed"):
                validate_v2_file_coverage(path, record, "QQQ", "1D", "2024-01-02", "2024-12-31", market_schedule=schedule)

    def test_retry_rejects_zero_attempts(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            download_bars_with_retry("k", "s", "QQQ", None, None, None, None, None, False, attempts=0)

    def test_manifest_retry_telemetry_missing_or_forged_fails(self):
        with self.assertRaisesRegex(ValueError, "telemetry missing"):
            validate_v2_retry_telemetry({})
        with self.assertRaisesRegex(ValueError, "telemetry invalid"):
            validate_v2_retry_telemetry({"logical_acquisition_item_count": 360, "retry_telemetry_status": "MEASURED", "provider_attempt_count": 1, "retry_attempt_count": 2, "failed_provider_attempt_count": 0})
        validate_v2_retry_telemetry({"logical_acquisition_item_count": 360, "retry_telemetry_status": "MEASURED", "provider_attempt_count": 362, "retry_attempt_count": 2, "failed_provider_attempt_count": 2})
        validate_v2_retry_telemetry({"retry_telemetry_status": "UNAVAILABLE", "provider_attempt_count": None, "retry_attempt_count": None, "failed_provider_attempt_count": None})

    def test_review_report_lists_unapproved_symbols_without_approval(self):
        records = [{"symbol": "QQQ", "timeframe": "1D", "coverage_exception_reason": "UNAPPROVED_LATE_START"}]
        with tempfile.TemporaryDirectory() as temp:
            md_path, csv_path, count = write_v2_coverage_review_reports(records, Path(temp))
            self.assertEqual(count, 1)
            self.assertIn("exceptions_auto_approved: false", md_path.read_text(encoding="utf-8"))
            self.assertEqual(pd.read_csv(csv_path).iloc[0]["exception_type"], "LATE_START")


if __name__ == "__main__":
    unittest.main()
