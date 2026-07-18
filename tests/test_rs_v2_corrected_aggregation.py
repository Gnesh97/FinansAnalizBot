from __future__ import annotations

import unittest
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import sys

import pandas as pd

from testdata.vericekici_updated import _aggregate_130min_worker, aggregate_to_130min, v2_offline_audit_guard
from testdata.v2_corrected_aggregation_audit import audit_corrected_v2_aggregation


def _schedule() -> pd.DataFrame:
    sessions = pd.DatetimeIndex(pd.to_datetime(["2024-07-02", "2024-07-03"]))
    opens = [day.tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30) for day in sessions]
    closes = [
        sessions[0].tz_localize("America/New_York") + pd.Timedelta(hours=16),
        sessions[1].tz_localize("America/New_York") + pd.Timedelta(hours=13),
    ]
    return pd.DataFrame({"market_open": opens, "market_close": closes}, index=sessions)


def _raw_for_day(day: str, start: str = "09:25", end: str = "16:05") -> pd.DataFrame:
    timestamps = pd.date_range(f"{day} {start}", f"{day} {end}", freq="5min", tz="America/New_York")
    values = list(range(len(timestamps)))
    return pd.DataFrame({
        "Date": timestamps,
        "Open": values,
        "High": [value + 1 for value in values],
        "Low": [value - 1 for value in values],
        "Close": [value + 0.5 for value in values],
        "Volume": [1] * len(values),
    })


def _failed_run_schedule() -> pd.DataFrame:
    path = Path(__file__).resolve().parent / "fixtures" / "nyse_sessions_2020-07-27_2026-07-07.csv"
    fixture = pd.read_csv(path)
    # Match the repository-approved calendar utility, which treats this future
    # fixture date as a regular session rather than a projected early close.
    fixture.loc[fixture["session"].eq("2026-07-02"), "market_close_time"] = "16:00"
    sessions = pd.DatetimeIndex(pd.to_datetime(fixture["session"]))
    opens = pd.to_datetime(fixture["session"] + " 09:30").dt.tz_localize("America/New_York")
    closes = pd.to_datetime(fixture["session"] + " " + fixture["market_close_time"]).dt.tz_localize("America/New_York")
    return pd.DataFrame({"market_open": list(opens), "market_close": list(closes)}, index=sessions)


class TestRsV2CorrectedAggregation(unittest.TestCase):
    def test_regular_session_creates_three_start_stamped_blocks(self):
        result = aggregate_to_130min(_raw_for_day("2024-07-02"), _schedule(), dataset_end="2024-07-03")
        starts = result["Date"].dt.tz_convert("America/New_York").dt.strftime("%H:%M").tolist()
        self.assertEqual(starts, ["09:30", "11:40", "13:50"])

    def test_early_close_creates_only_two_blocks(self):
        result = aggregate_to_130min(_raw_for_day("2024-07-03"), _schedule(), dataset_end="2024-07-03")
        starts = result["Date"].dt.tz_convert("America/New_York").dt.strftime("%H:%M").tolist()
        self.assertEqual(starts, ["09:30", "11:40"])

    def test_post_close_rows_cannot_create_a_bar(self):
        raw = _raw_for_day("2024-07-03", start="13:00", end="16:05")
        result = aggregate_to_130min(raw, _schedule(), dataset_end="2024-07-03")
        self.assertTrue(result.empty)

    def test_early_close_partial_final_block_excludes_close_and_later_rows(self):
        raw = _raw_for_day("2024-07-03")
        result = aggregate_to_130min(raw, _schedule(), dataset_end="2024-07-03")
        final = result.iloc[-1]
        eligible = raw[(pd.to_datetime(raw["Date"]) >= pd.Timestamp("2024-07-03 11:40", tz="America/New_York")) & (pd.to_datetime(raw["Date"]) < pd.Timestamp("2024-07-03 13:00", tz="America/New_York"))]
        self.assertEqual(int(final["Volume"]), len(eligible))
        self.assertEqual(float(final["Close"]), float(eligible.iloc[-1]["Close"]))

    def test_historical_aggregation_has_no_wall_clock_dependency(self):
        with patch("pandas.Timestamp.now", side_effect=AssertionError("wall clock used")):
            first = aggregate_to_130min(_raw_for_day("2024-07-03"), _schedule(), dataset_end="2024-07-03")
            second = aggregate_to_130min(_raw_for_day("2024-07-03"), _schedule(), dataset_end="2024-07-03")
        pd.testing.assert_frame_equal(first, second)

    def test_v2_worker_routes_through_required_calendar_schedule(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw_path = root / "raw.csv"
            output_path = root / "bars.csv"
            _raw_for_day("2024-07-03").to_csv(raw_path, index=False)
            result = _aggregate_130min_worker({
                "symbol": "AAA", "raw_path": str(raw_path), "bar_path": str(output_path),
                "overwrite": True, "rebuild": True, "dataset_profile": "rs_v2_clean",
                "market_schedule": _schedule(), "dataset_end": "2024-07-03",
            })
            self.assertEqual(result["bar_130min_status"], "built")
            starts = pd.to_datetime(pd.read_csv(output_path)["Date"], utc=True).dt.tz_convert("America/New_York").dt.strftime("%H:%M").tolist()
            self.assertEqual(starts, ["09:30", "11:40"])

    def test_corrected_audit_flag_without_run_fails_before_any_client_call(self):
        from testdata import vericekici_updated as downloader

        argv = ["vericekici_updated.py", "--corrected-aggregation-audit"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(downloader, "ensure_project_venv"),
            patch.object(downloader, "_load_alpaca") as client,
            self.assertRaisesRegex(ValueError, "requires --audit-run"),
        ):
            downloader.main()
        client.assert_not_called()


class TestCorrectedFailedRunIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1] / "testdata" / "research_data" / "rs_v2_clean_market_data"
        cls.run_id = "acq_20260712_212749"
        cls.manifest_path = cls.root / "manifests" / cls.run_id / "acquisition_manifest.json"
        if not cls.manifest_path.exists():
            raise unittest.SkipTest("frozen failed V2 run is not present")
        manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))
        referenced = {cls.manifest_path}
        for record in manifest["files"]:
            referenced.add(cls.root / record["output_relative_path"])
            if record.get("source_raw_5min_relative_path"):
                referenced.add(cls.root / record["source_raw_5min_relative_path"])
        cls.before_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in referenced}
        # The real dev sandbox may already have a latest_manifest.json pointer from
        # unrelated, legitimate prior acquisition work -- the audit being offline
        # means it must not create OR modify that file, not that it must be absent.
        cls.latest_pointer_path = cls.root / "manifests" / "latest_manifest.json"
        cls.latest_pointer_existed_before = cls.latest_pointer_path.exists()
        cls.latest_pointer_hash_before = (
            hashlib.sha256(cls.latest_pointer_path.read_bytes()).hexdigest() if cls.latest_pointer_existed_before else None
        )
        cls.temp = tempfile.TemporaryDirectory()
        with v2_offline_audit_guard() as telemetry:
            cls.result = audit_corrected_v2_aggregation(
                cls.root,
                cls.run_id,
                Path(cls.temp.name),
                market_schedule=_failed_run_schedule(),
                offline_telemetry=telemetry,
            )
        cls.network_attempt_count = telemetry["network_attempt_count"]
        cls.client_attempt_count = telemetry["client_attempt_count"]

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    def test_all_16_known_early_close_mismatches_disappear(self):
        summary = self.result["summary"]
        self.assertEqual(summary["removed_post_close_bar_count"], 16)
        self.assertEqual(summary["newly_restored_valid_early_close_final_bar_count"], 0)
        self.assertEqual(summary["remaining_invalid_timestamp_count"], 0)

    def test_2025_11_28_corrected_final_signals_are_valid(self):
        symbols = {"AMD", "GOOG", "INTC", "NFLX", "NVDA", "QQQ"}
        decisions = self.result["decision_path_impact"]
        rows = decisions[decisions["rebalance_date"].eq("2025-11-28") & decisions["symbol"].isin(symbols)]
        self.assertEqual(set(rows["symbol"]), symbols)
        self.assertTrue(rows["corrected_final_signal_bar_available"].all())
        self.assertEqual(set(rows["corrected_final_derived_timestamp"]), {"2025-11-28 16:40:00+00:00"})
        self.assertEqual(set(rows["decision_path_status_before"]), {"INVALID_POST_CLOSE_SIGNAL_BAR_SELECTED"})
        self.assertEqual(set(rows["decision_path_status_after"]), {"UNAFFECTED"})

    def test_expected_states_are_not_data_defects(self):
        counts = self.result["status_counts"]
        self.assertEqual(counts["EXPECTED_INITIAL_COLD_START"], 1053)
        self.assertEqual(counts["EXPECTED_POST_LISTING_WARMUP"], 28)
        self.assertEqual(counts["EXPECTED_RIGHT_CENSORED_FINAL_EXECUTION"], 178)
        self.assertEqual(counts.get("TRUE_EXECUTION_OPEN_MISSING", 0), 0)

    def test_internal_daily_feature_gaps_and_raw_provider_gaps_remain(self):
        self.assertEqual(self.result["status_counts"]["INTERNAL_DAILY_GAP_AFFECTS_FEATURES"], 1982)
        markdown = self.result["paths"]["aggregation_markdown"].read_text(encoding="utf-8")
        for date in ("2021-04-19", "2021-10-25", "2022-01-24", "2022-03-08", "2024-12-23", "2025-03-10"):
            self.assertIn(date, markdown)
        self.assertEqual(self.result["summary"]["internal_raw_empty_full_session_classification_count"], 1593)
        self.assertEqual(self.result["summary"]["internal_raw_empty_block_classification_count"], 691)
        self.assertGreater(self.result["summary"]["remaining_missing_block_count"], 0)

    def test_policy_comparison_is_diagnostic_and_exact(self):
        strict = self.result["policy_comparison"]["STRICT_ALL_SESSION_POLICY"]
        strategy = self.result["policy_comparison"]["STRATEGY_DECISION_PATH_POLICY"]
        self.assertEqual((strict["record_pass_count"], strict["record_fail_count"]), (55, 305))
        self.assertEqual((strict["symbol_pass_count"], strict["symbol_fail_count"]), (0, 180))
        self.assertEqual((strict["dataset_global_strict_decision_row_pass_count"], strict["dataset_global_strict_decision_row_fail_count"]), (0, 13140))
        self.assertEqual((strict["per_decision_required_input_strict_row_pass_count"], strict["per_decision_required_input_strict_row_fail_count"]), (11158, 1982))
        self.assertEqual((strategy["record_pass_count"], strategy["record_fail_count"]), (238, 122))
        self.assertEqual((strategy["symbol_pass_count"], strategy["symbol_fail_count"]), (58, 122))
        self.assertEqual((strategy["rebalance_decision_pass_count"], strategy["rebalance_decision_fail_count"]), (11158, 1982))

    def test_audit_preserves_all_referenced_files_and_manifest(self):
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.before_hashes}
        self.assertEqual(after, self.before_hashes)
        if self.latest_pointer_existed_before:
            self.assertEqual(hashlib.sha256(self.latest_pointer_path.read_bytes()).hexdigest(), self.latest_pointer_hash_before)
        else:
            self.assertFalse(self.latest_pointer_path.exists())

    def test_zero_network_or_client_calls(self):
        self.assertEqual((self.network_attempt_count, self.client_attempt_count), (0, 0))


if __name__ == "__main__":
    unittest.main()
