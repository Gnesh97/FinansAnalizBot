"""Fail-closed proofs for the rs_v2_tiingo dataset profile (Phase 6A).

Proves the validator REJECTS: wrong item count, wrong candidate count,
universe hash mismatch, file hash mismatch (tamper), missing daily session,
invalid import lineage, unknown acquisition action, non-daily records, and a
tampered exception registry -- and that a correct manifest PASSES (so the
rejections are meaningful).
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

from testdata.tiingo_provider import (
    DOWNLOAD_ACTION,
    IMPORT_ACTION,
    V2_TIINGO_DATASET_SCHEMA_VERSION,
    V2_TIINGO_ITEM_COUNT,
    V2_TIINGO_TARGET_END,
    V2_TIINGO_TARGET_START,
    _session_midnight_utc,
    _trim_frame_to_window,
    validate_v2_tiingo_manifest_and_files,
)
from testdata.vericekici_updated import (
    build_v2_acquisition_plan,
    load_v2_frozen_universe,
    verify_file_integrity,
)
from testdata.v2_session_coverage import (
    approved_nyse_schedule,
    calculate_v2_session_coverage,
)

EXPECTED_HASH = "026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933"


def _build_fixture(root: Path, schedule) -> dict:
    candidates, _ = load_v2_frozen_universe()
    dates = [_session_midnight_utc(str(session.date())) for session in schedule.index]
    size = len(dates)
    frame = pd.DataFrame({
        "Date": dates,
        "Open": [10.0] * size,
        "High": [11.0] * size,
        "Low": [9.0] * size,
        "Close": [10.5] * size,
        "Volume": [100.0] * size,
    })
    template = root / "fixture_template.csv"
    frame.to_csv(template, index=False)
    records = []
    plan = build_v2_acquisition_plan(root, candidates, V2_TIINGO_TARGET_START, V2_TIINGO_TARGET_END, "tiingo", "adjusted", include_intraday=False)
    for row in plan:
        path = root / row["output_relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, path)
        coverage = calculate_v2_session_coverage(
            pd.read_csv(path), timeframe="1D", symbol=row["symbol"],
            target_start=V2_TIINGO_TARGET_START, target_end=V2_TIINGO_TARGET_END,
            market_schedule=schedule, reviewed_exceptions=(),
        )
        records.append({
            **row, **verify_file_integrity(path), **coverage,
            "request_status": "SUCCESS",
            "acquisition_run_id": "fixture",
            "acquisition_action": DOWNLOAD_ACTION,
            "downloaded_in_current_run": True,
            "provider_result_status": "DOWNLOADED",
        })
    return {
        "dataset_schema_version": V2_TIINGO_DATASET_SCHEMA_VERSION,
        "dataset_profile": "rs_v2_tiingo",
        "acquisition_run_id": "fixture",
        "strategy_identifier": "rs_m126_top30_sma100_no_regime_v2_clean_stock",
        "target_start_date": V2_TIINGO_TARGET_START,
        "target_end_date": V2_TIINGO_TARGET_END,
        "feed": "tiingo",
        "adjustment": "adjusted",
        "logical_acquisition_item_count": V2_TIINGO_ITEM_COUNT,
        "successful_item_count": V2_TIINGO_ITEM_COUNT,
        "failed_item_count": 0,
        "status": "VALIDATION_PENDING",
        "candidate_universe_hash": EXPECTED_HASH,
        "candidate_count": 177,
        "retry_telemetry_status": "MEASURED",
        "provider_attempt_count": V2_TIINGO_ITEM_COUNT,
        "retry_attempt_count": 0,
        "failed_provider_attempt_count": 0,
        "files": records,
    }


class TestRsV2TiingoDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temp.name)
        cls.schedule = approved_nyse_schedule(V2_TIINGO_TARGET_START, V2_TIINGO_TARGET_END)
        cls.manifest = _build_fixture(cls.root, cls.schedule)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def _copy(self):
        manifest = dict(self.manifest)
        manifest["files"] = [dict(record) for record in self.manifest["files"]]
        return manifest

    def test_valid_manifest_passes(self):
        validate_v2_tiingo_manifest_and_files(self.root, self._copy())

    def test_wrong_item_count_rejected(self):
        manifest = self._copy()
        manifest["logical_acquisition_item_count"] = 358
        with self.assertRaisesRegex(ValueError, "frozen contract mismatch"):
            validate_v2_tiingo_manifest_and_files(self.root, manifest)

    def test_wrong_candidate_count_rejected(self):
        manifest = self._copy()
        manifest["candidate_count"] = 176
        with self.assertRaisesRegex(ValueError, "frozen contract mismatch"):
            validate_v2_tiingo_manifest_and_files(self.root, manifest)

    def test_wrong_universe_hash_rejected(self):
        manifest = self._copy()
        manifest["candidate_universe_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "frozen contract mismatch"):
            validate_v2_tiingo_manifest_and_files(self.root, manifest)

    def test_wrong_feed_rejected(self):
        manifest = self._copy()
        manifest["feed"] = "iex"
        with self.assertRaisesRegex(ValueError, "frozen contract mismatch"):
            validate_v2_tiingo_manifest_and_files(self.root, manifest)

    def test_file_hash_tamper_rejected(self):
        manifest = self._copy()
        record = manifest["files"][0]
        path = self.root / record["output_relative_path"]
        original = path.read_bytes()
        try:
            tampered = pd.read_csv(path)
            tampered.loc[0, "Close"] = 999.75
            tampered.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "file integrity/status mismatch"):
                validate_v2_tiingo_manifest_and_files(self.root, manifest)
        finally:
            path.write_bytes(original)

    def test_missing_daily_session_rejected(self):
        manifest = self._copy()
        record = manifest["files"][0]
        path = self.root / record["output_relative_path"]
        original = path.read_bytes()
        try:
            frame = pd.read_csv(path)
            frame.drop(index=100).to_csv(path, index=False)
            # Keep integrity consistent so the failure is attributable to the
            # coverage gap itself, not the tamper-detection hash check.
            record.update(verify_file_integrity(path))
            with self.assertRaisesRegex(ValueError, "coverage"):
                validate_v2_tiingo_manifest_and_files(self.root, manifest)
        finally:
            path.write_bytes(original)

    def test_import_lineage_missing_rejected(self):
        manifest = self._copy()
        record = manifest["files"][0]
        record["acquisition_action"] = IMPORT_ACTION
        record["provider_result_status"] = "IMPORTED"
        record["downloaded_in_current_run"] = False
        with self.assertRaisesRegex(ValueError, "import lineage invalid"):
            validate_v2_tiingo_manifest_and_files(self.root, manifest)

    def test_unknown_acquisition_action_rejected(self):
        manifest = self._copy()
        manifest["files"][0]["acquisition_action"] = "COPIED_FROM_SOMEWHERE"
        with self.assertRaisesRegex(ValueError, "unknown acquisition action"):
            validate_v2_tiingo_manifest_and_files(self.root, manifest)

    def test_extra_intraday_record_rejected(self):
        manifest = self._copy()
        extra = dict(manifest["files"][0])
        extra["timeframe"] = "130min"
        manifest["files"].append(extra)
        with self.assertRaisesRegex(ValueError, "exact logical record set mismatch"):
            validate_v2_tiingo_manifest_and_files(self.root, manifest)

    def test_tampered_exception_registry_rejected(self):
        registry = {
            "schema_version": "2.0.0",
            "exceptions": [{
                "symbol": "ABNB",
                "exception_type": "LATE_START",
                "reason": "tampered",
                "evidence_source": "tampered",
                "evidence_date": "2026-07-24",
                "reviewed": True,
                "approved_first_session": "2020-12-10",
                "evidence_hash": "deadbeef" * 8,
            }],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(registry, handle)
            bad_path = Path(handle.name)
        try:
            with patch("testdata.tiingo_provider.V2_TIINGO_COVERAGE_EXCEPTIONS_PATH", bad_path):
                with self.assertRaisesRegex(ValueError, "V2_COVERAGE_EXCEPTION_FILE_INVALID"):
                    validate_v2_tiingo_manifest_and_files(self.root, self._copy())
        finally:
            bad_path.unlink()

    def test_session_midnight_utc_handles_dst_and_est(self):
        self.assertEqual(_session_midnight_utc("2023-07-03"), "2023-07-03 04:00:00+00:00")
        self.assertEqual(_session_midnight_utc("2023-01-03"), "2023-01-03 05:00:00+00:00")

    def test_trim_frame_to_window_bounds_inclusive(self):
        frame = pd.DataFrame({
            "Date": ["2020-07-24 04:00:00+00:00", "2020-07-27 04:00:00+00:00", "2026-07-21 04:00:00+00:00", "2026-07-22 04:00:00+00:00"],
            "Open": [1.0] * 4, "High": [1.0] * 4, "Low": [1.0] * 4, "Close": [1.0] * 4, "Volume": [1.0] * 4,
        })
        trimmed = _trim_frame_to_window(frame, V2_TIINGO_TARGET_START, V2_TIINGO_TARGET_END)
        self.assertEqual(list(trimmed["Date"]), ["2020-07-27 04:00:00+00:00", "2026-07-21 04:00:00+00:00"])


if __name__ == "__main__":
    unittest.main()
