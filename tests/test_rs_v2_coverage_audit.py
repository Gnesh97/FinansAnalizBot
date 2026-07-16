from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from testdata.v2_coverage_forensics import audit_v2_run
from testdata.vericekici_updated import finalize_v2_with_report, main, v2_offline_audit_guard, write_v2_run_acquisition_report


def _schedule() -> pd.DataFrame:
    sessions = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    opens = [day.tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30) for day in sessions]
    closes = [day.tz_localize("America/New_York") + pd.Timedelta(hours=16) for day in sessions]
    return pd.DataFrame({"market_open": opens, "market_close": closes}, index=sessions)


def _bars(timestamps) -> pd.DataFrame:
    size = len(timestamps)
    return pd.DataFrame({"Date": timestamps, "Open": [10.0] * size, "High": [11.0] * size, "Low": [9.0] * size, "Close": [10.5] * size, "Volume": [100] * size})


def _write_fixture(root: Path) -> tuple[Path, Path]:
    schedule = _schedule()
    records = []
    for symbol, role in (("AAA", "CANDIDATE"), ("QQQ", "PRIMARY_BENCHMARK"), ("SPY", "SECONDARY_BENCHMARK")):
        prefix = Path("benchmarks") if symbol in {"QQQ", "SPY"} else Path()
        daily_path = prefix / "daily" / f"{symbol}_1D.csv"
        bar_path = prefix / "bars_130min" / f"{symbol}_130min.csv"
        raw_path = prefix / "raw_5min" / f"{symbol}_5min.csv"
        for path in (daily_path, bar_path, raw_path):
            (root / path).parent.mkdir(parents=True, exist_ok=True)

        daily_sessions = schedule.index.delete(1) if symbol in {"AAA", "QQQ"} else schedule.index
        daily_times = [day.tz_localize("America/New_York") + pd.Timedelta(hours=16) for day in daily_sessions]
        _bars(daily_times).to_csv(root / daily_path, index=False)

        expected = []
        for day, row in schedule.iterrows():
            for offset in (0, 130, 260):
                expected.append(row["market_open"] + pd.Timedelta(minutes=offset))
        derived = list(expected)
        missing_block = schedule.iloc[1]["market_open"] + pd.Timedelta(minutes=130)
        derived.remove(missing_block)
        if symbol == "QQQ":
            derived.append(schedule.iloc[0]["market_open"] + pd.Timedelta(minutes=5))
        _bars(derived).sort_values("Date").to_csv(root / bar_path, index=False)

        raw = []
        for day, row in schedule.iterrows():
            current = row["market_open"]
            while current < row["market_close"]:
                if not (symbol == "AAA" and missing_block <= current < missing_block + pd.Timedelta(minutes=130)):
                    raw.append(current)
                current += pd.Timedelta(minutes=5)
        _bars(raw).to_csv(root / raw_path, index=False)
        records.extend([
            {"symbol": symbol, "asset_role": role, "timeframe": "1D", "output_relative_path": daily_path.as_posix(), "coverage_exception_reason": ""},
            {"symbol": symbol, "asset_role": role, "timeframe": "130min", "output_relative_path": bar_path.as_posix(), "source_raw_5min_relative_path": raw_path.as_posix(), "coverage_exception_reason": "MISSING_INTRADAY_BAR"},
        ])
    manifest = {
        "acquisition_run_id": "fixture_run", "status": "FAILED_VALIDATION", "dataset_profile": "rs_v2_clean",
        "target_start_date": "2024-01-02", "target_end_date": "2024-01-04", "successful_item_count": 6,
        "failed_item_count": 0, "files": records,
    }
    manifest_path = root / "manifests" / "fixture_run" / "acquisition_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    latest_path = root / "manifests" / "latest_manifest.json"
    latest_path.write_text('{"stable":true}\n', encoding="utf-8")
    return manifest_path, latest_path


def _make_fixture_clean(root: Path) -> None:
    schedule = _schedule()
    expected = [row["market_open"] + pd.Timedelta(minutes=offset) for _, row in schedule.iterrows() for offset in (0, 130, 260)]
    daily_times = [day.tz_localize("America/New_York") + pd.Timedelta(hours=16) for day in schedule.index]
    for symbol in ("AAA", "QQQ", "SPY"):
        prefix = Path("benchmarks") if symbol in {"QQQ", "SPY"} else Path()
        _bars(daily_times).to_csv(root / prefix / "daily" / f"{symbol}_1D.csv", index=False)
        _bars(expected).to_csv(root / prefix / "bars_130min" / f"{symbol}_130min.csv", index=False)


class TestRsV2CoverageAudit(unittest.TestCase):
    def test_audit_reports_exact_daily_dates_and_common_frequency(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_fixture(root)
            result = audit_v2_run(root, "fixture_run", root / "reports", market_schedule=_schedule())
            gaps = result["daily_gap_detail"]
            missing = gaps[gaps["missing_session_date"].eq("2024-01-03")]
            self.assertEqual(set(missing["symbol"]), {"AAA", "QQQ"})
            self.assertEqual(set(missing["affected_symbol_count_for_date"]), {2})
            frequency = result["gap_frequency"]
            row = frequency[(frequency["scope"] == "DAILY_MISSING_SESSION") & frequency["market_session"].eq("2024-01-03")].iloc[0]
            self.assertEqual((row["affected_symbol_count"], bool(row["qqq_affected"]), bool(row["spy_affected"])), (2, True, False))

    def test_audit_distinguishes_raw_empty_and_aggregation_drop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_fixture(root)
            result = audit_v2_run(root, "fixture_run", root / "reports", market_schedule=_schedule())
            blocks = result["intraday_block_audit"]
            target = blocks[blocks["expected_block_start_timestamp"].str.contains("2024-01-03 16:40:00")]
            classes = dict(zip(target["symbol"], target["failure_classification"]))
            self.assertEqual(classes["AAA"], "RAW_SOURCE_EMPTY_FOR_BLOCK")
            self.assertEqual(classes["SPY"], "AGGREGATION_DROPPED_AVAILABLE_RAW_ROWS")

    def test_exact_intraday_frequency_counts_common_missing_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_fixture(root)
            result = audit_v2_run(root, "fixture_run", root / "reports", market_schedule=_schedule())
            frequency = result["gap_frequency"]
            row = frequency[(frequency["scope"] == "INTRADAY_MISSING_BLOCK") & frequency["timestamp"].str.contains("2024-01-03 16:40:00")].iloc[0]
            self.assertEqual((int(row["affected_symbol_count"]), bool(row["qqq_affected"]), bool(row["spy_affected"])), (3, True, True))

    def test_invalid_timestamp_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_fixture(root)
            result = audit_v2_run(root, "fixture_run", root / "reports", market_schedule=_schedule())
            invalid = result["intraday_block_audit"]
            invalid = invalid[(invalid["symbol"] == "QQQ") & invalid["actual_invalid_timestamp"].str.contains("2024-01-02 14:35:00")]
            self.assertEqual(len(invalid), 1)
            self.assertEqual(invalid.iloc[0]["failure_classification"], "INVALID_DERIVED_TIMESTAMP")

    def test_decision_path_and_boundary_review_are_generated_without_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = _write_fixture(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["files"][0]["coverage_exception_reason"] = "UNAPPROVED_LATE_START"
            payload["files"][0]["start_gap_sessions"] = 999
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = audit_v2_run(root, "fixture_run", root / "reports", market_schedule=_schedule())
            decisions = result["decision_path_impact"]
            self.assertEqual(set(decisions["decision_path_status"]), {"DAILY_FEATURE_INPUT_GAP"})
            boundary = result["boundary_exception_review"]
            self.assertTrue(boundary["known_manifest_reason"].str.contains("UNAPPROVED").any())
            self.assertFalse(boundary["exception_approved"].any())
            self.assertEqual(int(boundary.iloc[0]["start_gap_count"]), 0)

    def test_strategy_rebalance_uses_actual_last_bar_and_flags_invalid_final_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_fixture(root)
            path = root / "benchmarks" / "bars_130min" / "QQQ_130min.csv"
            frame = pd.read_csv(path)
            extra = _bars([pd.Timestamp("2024-01-04 13:55", tz="America/New_York")])
            extra["Date"] = extra["Date"].astype(str)
            pd.concat([frame, extra], ignore_index=True).sort_values("Date").to_csv(path, index=False)
            result = audit_v2_run(root, "fixture_run", root / "reports", market_schedule=_schedule(), warmup_observation_count=1)
            row = result["decision_path_impact"][(result["decision_path_impact"]["symbol"] == "QQQ") & result["decision_path_impact"]["rebalance_date"].eq("2024-01-04")].iloc[0]
            self.assertEqual(row["decision_path_status"], "SIGNAL_BAR_MISSING")
            self.assertIn("18:55:00", row["actual_last_130min_bar_timestamp"])

    def test_strategy_dates_are_derived_from_candidates_not_benchmarks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_fixture(root)
            candidate_path = root / "daily" / "AAA_1D.csv"
            candidate = pd.read_csv(candidate_path).iloc[:1]
            candidate.to_csv(candidate_path, index=False)
            result = audit_v2_run(root, "fixture_run", root / "reports", market_schedule=_schedule(), warmup_observation_count=1)
            candidate_decisions = result["decision_path_impact"][result["decision_path_impact"]["symbol"].eq("AAA")]
            self.assertEqual(set(candidate_decisions["rebalance_date"]), {"2024-01-02"})

    def test_unexpected_sessions_do_not_change_recomputed_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = _write_fixture(root)
            candidate_path = root / "daily" / "AAA_1D.csv"
            middle = _bars([pd.Timestamp("2024-01-03 16:00", tz="America/New_York")])
            outside = _bars([
                pd.Timestamp("2023-12-29 16:00", tz="America/New_York"),
                pd.Timestamp("2024-01-05 16:00", tz="America/New_York"),
            ])
            outside["Date"] = outside["Date"].astype(str)
            pd.concat([middle, outside], ignore_index=True).to_csv(candidate_path, index=False)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["files"][0]["coverage_exception_reason"] = "UNAPPROVED_BOUNDARY"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = audit_v2_run(root, "fixture_run", root / "reports", market_schedule=_schedule())
            row = result["boundary_exception_review"].set_index("symbol").loc["AAA"]
            self.assertEqual((int(row["start_gap_count"]), int(row["end_gap_count"])), (1, 1))
            self.assertEqual((row["daily_actual_first_session"], row["daily_actual_last_session"]), ("2024-01-03", "2024-01-03"))

    def test_clean_audit_has_fixed_empty_frequency_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_fixture(root)
            _make_fixture_clean(root)
            result = audit_v2_run(root, "fixture_run", root / "reports", market_schedule=_schedule())
            self.assertTrue(result["gap_frequency"].empty)
            self.assertEqual(list(result["gap_frequency"].columns), ["scope", "market_session", "timestamp", "affected_symbol_count", "affected_candidate_count", "affected_symbol_percentage", "qqq_affected", "spy_affected", "pattern"])

    def test_cli_audit_is_offline_and_preserves_manifest_data_and_latest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, latest = _write_fixture(root)
            data_path = root / "daily" / "AAA_1D.csv"
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}
            argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_clean", "--audit-run", "fixture_run", "--output-root", str(root), "--audit-output-dir", str(root / "audit_reports")]
            with patch("testdata.vericekici_updated.ensure_project_venv"), patch("testdata.v2_coverage_forensics.approved_nyse_schedule", return_value=_schedule()), patch("testdata.vericekici_updated._load_alpaca") as alpaca, patch("testdata.vericekici_updated.get_credentials") as credentials, patch.object(__import__("sys"), "argv", argv):
                main()
            alpaca.assert_not_called()
            credentials.assert_not_called()
            after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
            self.assertEqual(after, before)

    def test_offline_guard_blocks_client_and_socket_attempts(self):
        from testdata import vericekici_updated as downloader
        with v2_offline_audit_guard() as telemetry:
            with self.assertRaisesRegex(RuntimeError, "CLIENT_CALL_BLOCKED"):
                downloader._load_alpaca()
            with self.assertRaisesRegex(RuntimeError, "NETWORK_CALL_BLOCKED"):
                socket.create_connection(("example.invalid", 443))
        self.assertEqual((telemetry["client_attempt_count"], telemetry["network_attempt_count"]), (1, 1))

    def test_exception_registry_is_not_modified_by_audit(self):
        exception_path = Path(__file__).resolve().parents[1] / "testdata" / "baselines" / "rs_v2_clean_coverage_exceptions.json"
        before = hashlib.sha256(exception_path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_fixture(root)
            audit_v2_run(root, "fixture_run", root / "reports", market_schedule=_schedule())
        self.assertEqual(hashlib.sha256(exception_path.read_bytes()).hexdigest(), before)

    def test_failed_run_report_is_run_specific_and_cannot_masquerade_as_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "latest" / "data_refresh_report.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy-refresh\n", encoding="utf-8")
            summary = {"acquisition_run_id": "run_x", "dataset_profile": "rs_v2_clean", "refresh_mode": "overwrite", "symbol_count": 180, "requested_start": "2020-07-27", "requested_end": "2026-07-07", "validation_status": "FAILED_VALIDATION"}
            run_md, latest_md = write_v2_run_acquisition_report(root / "runs" / "run_x", root / "latest", summary, [])
            self.assertIn("run_x", run_md.read_text(encoding="utf-8"))
            self.assertIn("FAILED_VALIDATION", latest_md.read_text(encoding="utf-8"))
            self.assertEqual(legacy.read_text(encoding="utf-8"), "legacy-refresh\n")

    def test_finalize_failure_publishes_pending_then_failed_run_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary = {"acquisition_run_id": "run_y", "dataset_profile": "rs_v2_clean", "refresh_mode": "overwrite", "symbol_count": 180, "requested_start": "2020-07-27", "requested_end": "2026-07-07"}
            with patch("testdata.vericekici_updated.finalize_v2_manifest", side_effect=ValueError("validation boom")), self.assertRaisesRegex(ValueError, "validation boom"):
                finalize_v2_with_report({"status": "VALIDATION_PENDING"}, root / "manifest.json", root / "latest.json", root / "run", root / "reports", summary, [])
            report = (root / "run" / "acquisition_report.md").read_text(encoding="utf-8")
            self.assertIn("validation_status: FAILED_VALIDATION", report)
            self.assertFalse((root / "latest.json").exists())


if __name__ == "__main__":
    unittest.main()
