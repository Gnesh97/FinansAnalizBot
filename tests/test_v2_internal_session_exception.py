from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

from testdata.v2_session_coverage import (  # noqa: E402
    coverage_exception_evidence_hash,
    calculate_v2_session_coverage,
    load_reviewed_coverage_exceptions,
)
from testdata.vericekici_updated import write_v2_applied_exceptions_report  # noqa: E402


def _schedule(count: int = 130) -> pd.DataFrame:
    sessions = list(pd.bdate_range("2024-01-02", periods=count - 1)) + [pd.Timestamp("2024-12-31")]
    opens = [day.tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30) for day in sessions]
    closes = [day.tz_localize("America/New_York") + pd.Timedelta(hours=16) for day in sessions]
    return pd.DataFrame({"market_open": opens, "market_close": closes}, index=pd.DatetimeIndex(sessions))


def _daily(schedule: pd.DataFrame, sessions=None) -> pd.DataFrame:
    chosen = list(schedule.index if sessions is None else sessions)
    dates = [pd.Timestamp(day).tz_localize("America/New_York") + pd.Timedelta(hours=16) for day in chosen]
    size = len(dates)
    return pd.DataFrame({"Date": dates, "Open": [10.0] * size, "High": [11.0] * size, "Low": [9.0] * size, "Close": [10.5] * size, "Volume": [100] * size})


def _coverage(frame, timeframe, schedule, exceptions=(), symbol="QQQ"):
    return calculate_v2_session_coverage(
        frame,
        timeframe=timeframe,
        symbol=symbol,
        target_start="2024-01-02",
        target_end="2024-12-31",
        market_schedule=schedule,
        reviewed_exceptions=list(exceptions),
    )


def _internal_session_exception(*, affected_date: str, affected_symbols, timeframes_affected: list[str]) -> dict:
    item = {
        "exception_type": "MISSING_INTERNAL_SESSION",
        "reviewed": True,
        "affected_date": affected_date,
        "affected_symbols": affected_symbols,
        "timeframes_affected": timeframes_affected,
        "rationale": "SIP full-session probe showed severe intraday gaps on this legacy date.",
        "evidence_reports": ["reports/audit/sip_completeness_and_fetch_history_20260716.md"],
        "residual_impact_statement": "0 affected rebalance decisions per sip_residual_impact_20260716.md.",
        "approved_by": "operator",
        "approved_at": "2026-07-16",
    }
    item["evidence_hash"] = coverage_exception_evidence_hash(item)
    return item


class TestExceptionSchemaV2(unittest.TestCase):
    def test_schema_v2_accepts_missing_internal_session_type(self):
        item = _internal_session_exception(affected_date="2021-04-19", affected_symbols="ALL", timeframes_affected=["130min"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exceptions.json"
            path.write_text(json.dumps({"schema_version": "2.0.0", "exceptions": [item]}), encoding="utf-8")
            loaded = load_reviewed_coverage_exceptions(path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["exception_type"], "MISSING_INTERNAL_SESSION")

    def test_schema_v1_still_loads_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exceptions.json"
            path.write_text(json.dumps({"schema_version": "1.0.0", "exceptions": []}), encoding="utf-8")
            loaded = load_reviewed_coverage_exceptions(path)
        self.assertEqual(loaded, [])

    def test_schema_v1_rejects_missing_internal_session_type(self):
        item = _internal_session_exception(affected_date="2021-04-19", affected_symbols="ALL", timeframes_affected=["130min"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exceptions.json"
            path.write_text(json.dumps({"schema_version": "1.0.0", "exceptions": [item]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "EXCEPTION_FILE_INVALID"):
                load_reviewed_coverage_exceptions(path)

    def test_missing_mandatory_field_rejected_fail_closed(self):
        item = _internal_session_exception(affected_date="2021-04-19", affected_symbols="ALL", timeframes_affected=["130min"])
        for field in ("affected_date", "affected_symbols", "timeframes_affected", "rationale", "evidence_reports", "residual_impact_statement", "approved_by", "approved_at"):
            broken = dict(item)
            broken.pop(field)
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "exceptions.json"
                path.write_text(json.dumps({"schema_version": "2.0.0", "exceptions": [broken]}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "EXCEPTION_FILE_INVALID", msg=f"field={field}"):
                    load_reviewed_coverage_exceptions(path)

    def test_unreviewed_internal_session_exception_rejected(self):
        item = _internal_session_exception(affected_date="2021-04-19", affected_symbols="ALL", timeframes_affected=["130min"])
        item["reviewed"] = False
        item["evidence_hash"] = coverage_exception_evidence_hash(item)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exceptions.json"
            path.write_text(json.dumps({"schema_version": "2.0.0", "exceptions": [item]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "EXCEPTION_FILE_INVALID"):
                load_reviewed_coverage_exceptions(path)

    def test_tampered_evidence_hash_rejected(self):
        item = _internal_session_exception(affected_date="2021-04-19", affected_symbols="ALL", timeframes_affected=["130min"])
        item["rationale"] = "tampered after hashing"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exceptions.json"
            path.write_text(json.dumps({"schema_version": "2.0.0", "exceptions": [item]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "EXCEPTION_FILE_INVALID"):
                load_reviewed_coverage_exceptions(path)

    def test_duplicate_internal_session_exception_rejected(self):
        item = _internal_session_exception(affected_date="2021-04-19", affected_symbols="ALL", timeframes_affected=["130min"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exceptions.json"
            path.write_text(json.dumps({"schema_version": "2.0.0", "exceptions": [item, item]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "EXCEPTION_FILE_INVALID"):
                load_reviewed_coverage_exceptions(path)


class TestInternalSessionExceptionApplication(unittest.TestCase):
    def test_unapproved_internal_gap_still_fails_exactly_as_before(self):
        """Regression: with no MISSING_INTERNAL_SESSION exception present, behavior
        must be byte-for-byte identical to the pre-TASK-A contract."""
        schedule = _schedule()
        frame = _daily(schedule, schedule.index.delete(50))
        result = _coverage(frame, "1D", schedule)
        self.assertEqual(result["missing_internal_session_count"], 1)
        self.assertEqual(result["coverage_status"], "FAIL")
        self.assertIn("MISSING_INTERNAL_SESSION", result["coverage_exception_reason"])
        self.assertNotIn("APPROVED_MISSING_INTERNAL_SESSION", result["coverage_exception_reason"])
        self.assertEqual(result["approved_internal_session_exception_dates"], [])

    def test_approved_internal_gap_passes_and_is_listed_not_absorbed(self):
        schedule = _schedule()
        missing_session = schedule.index[50]
        frame = _daily(schedule, schedule.index.delete(50))
        exception = _internal_session_exception(
            affected_date=str(missing_session.date()), affected_symbols="ALL", timeframes_affected=["1D"]
        )
        result = _coverage(frame, "1D", schedule, [exception])
        self.assertEqual(result["coverage_status"], "VALID")
        self.assertEqual(result["missing_internal_session_count"], 0)
        self.assertIn("APPROVED_MISSING_INTERNAL_SESSION", result["coverage_exception_reason"])
        self.assertEqual(result["approved_internal_session_exception_dates"], [str(missing_session.date())])

    def test_exception_scoped_to_explicit_symbol_list_does_not_apply_to_other_symbols(self):
        schedule = _schedule()
        missing_session = schedule.index[50]
        frame = _daily(schedule, schedule.index.delete(50))
        exception = _internal_session_exception(
            affected_date=str(missing_session.date()), affected_symbols=["ABBV"], timeframes_affected=["1D"]
        )
        result = _coverage(frame, "1D", schedule, [exception], symbol="QQQ")
        self.assertEqual(result["coverage_status"], "FAIL")
        self.assertEqual(result["approved_internal_session_exception_dates"], [])

    def test_exception_scoped_to_explicit_symbol_list_applies_to_matching_symbol(self):
        schedule = _schedule()
        missing_session = schedule.index[50]
        frame = _daily(schedule, schedule.index.delete(50))
        exception = _internal_session_exception(
            affected_date=str(missing_session.date()), affected_symbols=["ABBV", "ALB"], timeframes_affected=["1D"]
        )
        result = _coverage(frame, "1D", schedule, [exception], symbol="abbv")
        self.assertEqual(result["coverage_status"], "VALID")

    def test_exception_scoped_to_timeframe_does_not_leak_across_timeframes(self):
        schedule = _schedule()
        missing_session = schedule.index[50]
        frame = _daily(schedule, schedule.index.delete(50))
        exception = _internal_session_exception(
            affected_date=str(missing_session.date()), affected_symbols="ALL", timeframes_affected=["130min"]
        )
        result = _coverage(frame, "1D", schedule, [exception])
        self.assertEqual(result["coverage_status"], "FAIL")
        self.assertEqual(result["approved_internal_session_exception_dates"], [])

    def test_exception_does_not_apply_to_a_different_date(self):
        schedule = _schedule()
        missing_session = schedule.index[50]
        other_session = schedule.index[51]
        frame = _daily(schedule, schedule.index.delete(50))
        exception = _internal_session_exception(
            affected_date=str(other_session.date()), affected_symbols="ALL", timeframes_affected=["1D"]
        )
        result = _coverage(frame, "1D", schedule, [exception])
        self.assertEqual(result["coverage_status"], "FAIL")
        self.assertNotEqual(str(missing_session.date()), str(other_session.date()))

    def test_unreviewed_or_tampered_exception_never_applied_at_runtime(self):
        schedule = _schedule()
        missing_session = schedule.index[50]
        frame = _daily(schedule, schedule.index.delete(50))
        exception = _internal_session_exception(
            affected_date=str(missing_session.date()), affected_symbols="ALL", timeframes_affected=["1D"]
        )
        tampered = {**exception, "rationale": "tampered"}
        result = _coverage(frame, "1D", schedule, [tampered])
        self.assertEqual(result["coverage_status"], "FAIL")


class TestAppliedExceptionsReport(unittest.TestCase):
    def test_applied_exceptions_are_listed_not_silently_absorbed(self):
        record = {
            "symbol": "ABBV",
            "timeframe": "130min",
            "coverage_exception_reason": "APPROVED_MISSING_INTERNAL_SESSION",
            "approved_internal_session_exception_dates": ["2021-04-19"],
        }
        with tempfile.TemporaryDirectory() as temp:
            md_path, csv_path, count = write_v2_applied_exceptions_report([record], Path(temp))
            self.assertEqual(count, 1)
            text = md_path.read_text(encoding="utf-8")
            self.assertIn("exceptions_silently_absorbed: false", text)
            self.assertIn("ABBV 130min MISSING_INTERNAL_SESSION 2021-04-19", text)
            self.assertEqual(pd.read_csv(csv_path).iloc[0]["affected_date"], "2021-04-19")

    def test_records_without_approved_reason_produce_no_rows(self):
        record = {"symbol": "QQQ", "timeframe": "1D", "coverage_exception_reason": "UNAPPROVED_LATE_START"}
        with tempfile.TemporaryDirectory() as temp:
            _, _, count = write_v2_applied_exceptions_report([record], Path(temp))
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
