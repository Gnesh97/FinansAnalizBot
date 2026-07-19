import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

from testdata.vericekici_updated import (
    V2_UNIVERSE_CONTRACT_MISMATCH,
    build_v2_acquisition_plan,
    load_v2_frozen_universe,
    resolve_v2_dataset_paths,
    _aggregate_130min_worker,
    finalize_v2_manifest,
    validate_v2_manifest_and_files,
    verify_file_integrity,
)
from testdata.v2_session_coverage import coverage_metadata_for_file
from testdata.runner import validate_v2_clean_dataset


BASELINE = Path(__file__).resolve().parent.parent / "testdata" / "baselines" / "rs_m126_top30_sma100_no_regime_v2_clean_stock.json"
EXPECTED_HASH = "026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933"
SESSION_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nyse_sessions_2020-07-27_2026-07-07.csv"


def _sample_bars():
    dates = pd.date_range("2024-01-02 14:30:00+00:00", periods=60, freq="5min")
    return pd.DataFrame({"Date": dates.astype(str), "Open": range(1, 61), "High": range(2, 62), "Low": range(0, 60), "Close": range(1, 61), "Volume": [100] * 60})


def _validation_schedule():
    fixture = pd.read_csv(SESSION_FIXTURE)
    sessions = pd.DatetimeIndex(pd.to_datetime(fixture["session"]))
    opens = pd.to_datetime(fixture["session"] + " 09:30").dt.tz_localize("America/New_York")
    closes = pd.to_datetime(fixture["session"] + " " + fixture["market_close_time"]).dt.tz_localize("America/New_York")
    return pd.DataFrame({"market_open": list(opens), "market_close": list(closes)}, index=sessions)


def _session_fixture_frames(schedule):
    daily_dates = list(schedule["market_close"])
    intraday_dates = []
    for _, row in schedule.iterrows():
        current = row["market_open"]
        while current < row["market_close"]:
            intraday_dates.append(current)
            current += pd.Timedelta(minutes=130)
    def frame(dates):
        size = len(dates)
        return pd.DataFrame({"Date": dates, "Open": [10.0] * size, "High": [11.0] * size, "Low": [9.0] * size, "Close": [10.5] * size, "Volume": [100] * size})
    return frame(daily_dates), frame(intraday_dates)


def _complete_manifest(root: Path):
    candidates, _ = load_v2_frozen_universe(BASELINE)
    schedule = _validation_schedule()
    daily_frame, intraday_frame = _session_fixture_frames(schedule)
    templates = root / "fixture_templates"
    templates.mkdir(parents=True, exist_ok=True)
    daily_template = templates / "daily.csv"
    intraday_template = templates / "intraday.csv"
    daily_frame.to_csv(daily_template, index=False)
    intraday_frame.to_csv(intraday_template, index=False)
    records = []
    for row in build_v2_acquisition_plan(root, candidates, "2020-07-27", "2026-07-07", "iex", "all"):
        path = root / row["output_relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(daily_template if row["timeframe"] == "1D" else intraday_template, path)
        coverage = coverage_metadata_for_file(path, timeframe=row["timeframe"], symbol=row["symbol"], target_start="2020-07-27", target_end="2026-07-07", market_schedule=schedule)
        record = {**row, **verify_file_integrity(path), **coverage, "request_status": "SUCCESS", "acquisition_run_id": "fixture", "acquisition_action": "DOWNLOADED_IN_CURRENT_RUN" if row["timeframe"] == "1D" else "DERIVED_IN_CURRENT_RUN", "downloaded_in_current_run": row["timeframe"] == "1D", "provider_result_status": "DOWNLOADED"}
        if row["timeframe"] == "130min":
            paths = resolve_v2_dataset_paths(root, row["symbol"])
            paths.raw_5min.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(intraday_template, paths.raw_5min)
            raw_integrity = verify_file_integrity(paths.raw_5min)
            record.update({"derivation_type": "AGGREGATED_FROM_RAW_5MIN", "source_raw_5min_relative_path": paths.raw_5min.relative_to(root).as_posix(), "source_raw_5min_sha256": raw_integrity["sha256"], "source_raw_5min_row_count": raw_integrity["row_count"], "aggregation_function": "aggregate_to_130min", "aggregation_schema_version": "1.0.0", "aggregation_timezone": "America/New_York", "derived_row_count": record["row_count"], "derivation_status": "SUCCESS", "raw_downloaded_in_current_run": True, "aggregation_result_status": "BUILT"})
        records.append(record)
    return {"dataset_schema_version": "2.0.0", "dataset_profile": "rs_v2_clean", "acquisition_run_id": "fixture", "strategy_identifier": "rs_m126_top30_sma100_no_regime_v2_clean_stock", "target_start_date": "2020-07-27", "target_end_date": "2026-07-07", "feed": "iex", "adjustment": "all", "logical_acquisition_item_count": 358, "successful_item_count": 358, "failed_item_count": 0, "status": "VALIDATION_PENDING", "candidate_universe_hash": EXPECTED_HASH, "candidate_count": 177, "retry_telemetry_status": "MEASURED", "provider_attempt_count": 358, "retry_attempt_count": 0, "failed_provider_attempt_count": 0, "files": records}


def _invalid_structural_manifest(root: Path):
    candidates, _ = load_v2_frozen_universe(BASELINE)
    template = root / "invalid_fixture_template.csv"
    _sample_bars().to_csv(template, index=False)
    records = []
    fake_coverage = {"coverage_status": "VALID", "first_market_session": "2024-01-02", "last_market_session": "2024-01-02", "start_gap_sessions": 0, "end_gap_sessions": 0, "daily_session_count": 60, "duplicate_daily_session_count": 0, "intraday_session_count": 0, "missing_internal_session_count": 0, "unexpected_session_count": 0, "warmup_history_sufficient": True, "coverage_exception_reason": "", "intraday_bar_count": 0, "expected_intraday_bar_count": 0, "missing_intraday_bar_count": 0, "invalid_intraday_schedule_count": 0, "duplicate_intraday_timestamp_count": 0}
    for row in build_v2_acquisition_plan(root, candidates, "2020-07-27", "2026-07-07", "iex", "all"):
        path = root / row["output_relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, path)
        coverage = dict(fake_coverage)
        if row["timeframe"] == "130min":
            coverage.update({"daily_session_count": 0, "intraday_session_count": 1, "intraday_bar_count": 60, "expected_intraday_bar_count": 60})
        record = {**row, **verify_file_integrity(path), **coverage, "request_status": "SUCCESS", "acquisition_run_id": "fixture", "acquisition_action": "DOWNLOADED_IN_CURRENT_RUN" if row["timeframe"] == "1D" else "DERIVED_IN_CURRENT_RUN", "downloaded_in_current_run": row["timeframe"] == "1D", "provider_result_status": "DOWNLOADED"}
        if row["timeframe"] == "130min":
            raw = resolve_v2_dataset_paths(root, row["symbol"]).raw_5min
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template, raw)
            raw_integrity = verify_file_integrity(raw)
            record.update({"source_raw_5min_relative_path": raw.relative_to(root).as_posix(), "source_raw_5min_sha256": raw_integrity["sha256"], "source_raw_5min_row_count": raw_integrity["row_count"], "derived_row_count": record["row_count"], "derivation_status": "SUCCESS", "raw_downloaded_in_current_run": True, "aggregation_result_status": "BUILT"})
        records.append(record)
    return {"dataset_schema_version": "2.0.0", "dataset_profile": "rs_v2_clean", "acquisition_run_id": "fixture", "strategy_identifier": "rs_m126_top30_sma100_no_regime_v2_clean_stock", "target_start_date": "2020-07-27", "target_end_date": "2026-07-07", "feed": "iex", "adjustment": "all", "logical_acquisition_item_count": 358, "successful_item_count": 358, "failed_item_count": 0, "status": "VALIDATION_PENDING", "candidate_universe_hash": EXPECTED_HASH, "candidate_count": 177, "retry_telemetry_status": "MEASURED", "provider_attempt_count": 358, "retry_attempt_count": 0, "failed_provider_attempt_count": 0, "files": records}


class TestRsV2CleanData(unittest.TestCase):
    def test_frozen_universe_hash_is_baseline_derived_not_legacy_inventory(self):
        symbols, baseline = load_v2_frozen_universe(BASELINE)
        self.assertEqual(len(symbols), 177)
        self.assertEqual(symbols, sorted(symbols))
        self.assertEqual(len(symbols), len(set(symbols)))
        self.assertNotIn("QQQ", symbols)
        self.assertNotIn("SPY", symbols)
        self.assertNotIn("BK", symbols)
        self.assertEqual(hashlib.sha256(",".join(symbols).encode()).hexdigest(), EXPECTED_HASH)
        self.assertEqual(baseline["universe"]["candidate_universe_hash"], EXPECTED_HASH)

    def test_frozen_universe_rejects_contract_mismatch(self):
        bad = json.loads(BASELINE.read_text(encoding="utf-8"))
        bad["universe"]["candidate_symbols"] = list(reversed(bad["universe"]["candidate_symbols"]))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, V2_UNIVERSE_CONTRACT_MISMATCH):
                load_v2_frozen_universe(path)

    def test_legacy_baseline_cannot_be_used_as_v2_contract(self):
        legacy = BASELINE.with_name("rs_m126_top30_sma100_no_regime.json")
        with self.assertRaisesRegex(ValueError, V2_UNIVERSE_CONTRACT_MISMATCH):
            load_v2_frozen_universe(legacy)

    def test_bk_retirement_universe_loads_as_177_without_bk(self):
        # Phase 3B: BK retired from the frozen universe (178 -> 177). The active
        # baseline file must load as exactly 177 symbols with BK absent.
        symbols, baseline = load_v2_frozen_universe(BASELINE)
        self.assertEqual(len(symbols), 177)
        self.assertNotIn("BK", symbols)
        self.assertEqual(baseline["universe"]["candidate_count"], 177)

    def test_bk_retirement_hash_verification_passes_for_current_untampered_list(self):
        symbols, baseline = load_v2_frozen_universe(BASELINE)
        recomputed = hashlib.sha256(",".join(symbols).encode("utf-8")).hexdigest()
        self.assertEqual(recomputed, baseline["universe"]["candidate_universe_hash"])
        self.assertEqual(recomputed, EXPECTED_HASH)

    def test_bk_retirement_hash_verification_fails_closed_if_bk_is_reinserted(self):
        # Tamper: re-insert BK (restoring the old 178-symbol list) without updating
        # candidate_count/candidate_universe_hash to match -- must be rejected, not
        # silently accepted as if BK were still approved.
        tampered = json.loads(BASELINE.read_text(encoding="utf-8"))
        symbols = tampered["universe"]["candidate_symbols"]
        insert_at = next(i for i, s in enumerate(symbols) if s > "BK")
        symbols.insert(insert_at, "BK")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tampered_with_bk.json"
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, V2_UNIVERSE_CONTRACT_MISMATCH):
                load_v2_frozen_universe(path)

    def test_plan_is_358_and_unaffected_by_legacy_inventory(self):
        symbols, _ = load_v2_frozen_universe(BASELINE)
        plan = build_v2_acquisition_plan(Path("unused"), symbols, "2020-07-27", "2026-07-07", "iex", "all")
        self.assertEqual(len(plan), 358)
        self.assertEqual(sum(row["asset_role"] == "CANDIDATE" for row in plan), 354)
        self.assertEqual(sum(row["symbol"] == "QQQ" for row in plan), 2)
        self.assertEqual(sum(row["symbol"] == "SPY" for row in plan), 2)

    def test_benchmark_paths_never_share_candidate_paths(self):
        root = Path("root")
        qqq = resolve_v2_dataset_paths(root, "QQQ")
        aapl = resolve_v2_dataset_paths(root, "AAPL")
        self.assertEqual(qqq.daily.relative_to(root).as_posix(), "benchmarks/daily/QQQ_1D.csv")
        self.assertEqual(qqq.raw_5min.relative_to(root).as_posix(), "benchmarks/raw_5min/QQQ_5min.csv")
        self.assertEqual(qqq.bars_130min.relative_to(root).as_posix(), "benchmarks/bars_130min/QQQ_130min.csv")
        self.assertFalse(str(aapl.daily).replace("\\", "/").startswith("root/benchmarks/"))

    def test_benchmark_aggregation_uses_benchmark_raw_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for symbol in ("QQQ", "SPY"):
                paths = resolve_v2_dataset_paths(root, symbol)
                paths.raw_5min.parent.mkdir(parents=True, exist_ok=True)
                _sample_bars().to_csv(paths.raw_5min, index=False)
                result = _aggregate_130min_worker({"symbol": symbol, "raw_path": str(paths.raw_5min), "bar_path": str(paths.bars_130min), "overwrite": True, "rebuild": True})
                self.assertEqual(result["bar_130min_status"], "built")
                self.assertTrue(paths.bars_130min.exists())
                self.assertFalse((root / "raw_5min" / f"{symbol}_5min.csv").exists())

    def test_finalized_manifest_publishes_pointer_only_after_immutable_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_path = root / "manifests" / "run1" / "acquisition_manifest.json"
            latest_path = root / "manifests" / "latest_manifest.json"
            manifest = _complete_manifest(root)
            manifest["acquisition_run_id"] = "run1"
            for record in manifest["files"]:
                record["acquisition_run_id"] = "run1"
            with patch("testdata.vericekici_updated.approved_nyse_schedule", return_value=_validation_schedule()), patch("testdata.vericekici_updated.validate_v2_manifest_and_files", wraps=validate_v2_manifest_and_files) as shared_validator:
                finalize_v2_manifest(manifest, run_path, latest_path)
            self.assertEqual(shared_validator.call_count, 2)
            self.assertTrue(run_path.exists())
            self.assertTrue(latest_path.exists())
            pointer = json.loads(latest_path.read_text(encoding="utf-8"))
            self.assertEqual(pointer["immutable_manifest_relative_path"], "manifests/run1/acquisition_manifest.json")
            self.assertEqual(pointer["immutable_manifest_sha256"], hashlib.sha256(run_path.read_bytes()).hexdigest())
            self.assertNotEqual(run_path, latest_path)
            committed = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertEqual(committed["status"], "COMPLETE_VALIDATED")
            self.assertTrue(committed["validation_completed_at"].endswith("Z"))

    def test_incomplete_manifest_never_replaces_latest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            latest = root / "manifests" / "latest_manifest.json"
            latest.parent.mkdir(parents=True)
            latest.write_text('{"stable": true}', encoding="utf-8")
            with self.assertRaises(ValueError):
                finalize_v2_manifest({"acquisition_run_id": "bad", "status": "INCOMPLETE"}, root / "manifests" / "bad" / "acquisition_manifest.json", latest)
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8")), {"stable": True})

    def test_runner_rejects_orphaned_latest_pointer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            latest = root / "manifests" / "latest_manifest.json"
            latest.parent.mkdir(parents=True)
            latest.write_text(json.dumps({"acquisition_run_id": "gone", "immutable_manifest_relative_path": "manifests/gone/acquisition_manifest.json", "immutable_manifest_sha256": "x"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "V2_DATASET_NOT_VALIDATED"):
                validate_v2_clean_dataset(root)

    def test_runner_calls_the_downloader_shared_validator(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "manifests" / "fixture" / "acquisition_manifest.json"
            latest = root / "manifests" / "latest_manifest.json"
            run.parent.mkdir(parents=True)
            run.write_text(json.dumps({"acquisition_run_id": "fixture"}), encoding="utf-8")
            latest.write_text(json.dumps({"acquisition_run_id": "fixture", "immutable_manifest_relative_path": "manifests/fixture/acquisition_manifest.json", "immutable_manifest_sha256": hashlib.sha256(run.read_bytes()).hexdigest()}), encoding="utf-8")
            with patch("testdata.vericekici_updated.validate_v2_manifest_and_files", side_effect=ValueError("shared sentinel")) as shared, self.assertRaisesRegex(ValueError, "shared sentinel"):
                validate_v2_clean_dataset(root)
            shared.assert_called_once()

    def test_runner_rejects_wrong_benchmark_role_and_raw_lineage_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = _invalid_structural_manifest(root)
            manifest["files"][354]["asset_role"] = "CANDIDATE"
            run, latest = root / "manifests" / "fixture" / "acquisition_manifest.json", root / "manifests" / "latest_manifest.json"
            with self.assertRaisesRegex(ValueError, "V2_DATASET_NOT_VALIDATED"):
                finalize_v2_manifest(manifest, run, latest)
            manifest = _invalid_structural_manifest(root)
            next(item for item in manifest["files"] if item["timeframe"] == "130min")["source_raw_5min_sha256"] = "bad"
            with self.assertRaisesRegex(ValueError, "V2_DATASET_NOT_VALIDATED"):
                finalize_v2_manifest(manifest, run, latest)

    def test_dry_run_uses_temp_output_and_never_reads_credentials_or_constructs_client(self):
        from testdata.vericekici_updated import main
        with tempfile.TemporaryDirectory() as temp, patch("testdata.vericekici_updated.ensure_project_venv"), patch("testdata.vericekici_updated.get_credentials") as credentials, patch("testdata.vericekici_updated._load_alpaca") as alpaca:
            stale = Path(temp) / "dry_run_reports" / "rs_v2_clean_data_acquisition_plan.csv"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale\n", encoding="utf-8")
            argv = ["vericekici_updated.py", "--dataset-profile", "rs_v2_clean", "--adjustment", "all", "--feed", "iex", "--start", "2020-07-27", "--end", "2026-07-07", "--output-root", temp, "--dry-run"]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as exit_code:
                main()
            self.assertEqual(exit_code.exception.code, 0)
            credentials.assert_not_called()
            alpaca.assert_not_called()
            self.assertFalse((Path(temp) / "manifests").exists())
            self.assertEqual(len(pd.read_csv(Path(temp) / "dry_run_reports" / "rs_v2_clean_data_acquisition_plan.csv")), 358)

    def test_live_v2_requires_overwrite_and_forbids_skip(self):
        from testdata.vericekici_updated import main
        base = ["vericekici_updated.py", "--dataset-profile", "rs_v2_clean", "--adjustment", "all", "--feed", "iex", "--start", "2020-07-27", "--end", "2026-07-07", "--output-root", "unused", "--live"]
        with patch("testdata.vericekici_updated.ensure_project_venv"), patch.object(sys, "argv", base), self.assertRaisesRegex(ValueError, "requires --overwrite"):
            main()
        with patch("testdata.vericekici_updated.ensure_project_venv"), patch.object(sys, "argv", [*base, "--overwrite", "--skip-download"]), self.assertRaisesRegex(ValueError, "forbids"):
            main()

    def test_missing_coverage_and_stale_current_run_provenance_fail_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = _invalid_structural_manifest(root)
            manifest["files"][0].pop("coverage_status")
            with self.assertRaisesRegex(ValueError, "coverage"):
                finalize_v2_manifest(manifest, root / "manifests" / "x" / "acquisition_manifest.json", root / "manifests" / "latest_manifest.json")
            manifest = _invalid_structural_manifest(root)
            manifest["files"][0]["downloaded_in_current_run"] = False
            with self.assertRaisesRegex(ValueError, "stale daily provenance"):
                finalize_v2_manifest(manifest, root / "manifests" / "x" / "acquisition_manifest.json", root / "manifests" / "latest_manifest.json")

    def test_wrong_record_run_id_and_per_record_policy_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = _invalid_structural_manifest(root)
            manifest["files"][0]["acquisition_run_id"] = "old"
            with self.assertRaisesRegex(ValueError, "role/path"):
                finalize_v2_manifest(manifest, root / "manifests" / "x" / "acquisition_manifest.json", root / "manifests" / "latest_manifest.json")
            manifest = _invalid_structural_manifest(root)
            manifest["files"][0]["feed"] = "sip"
            with self.assertRaisesRegex(ValueError, "role/path"):
                finalize_v2_manifest(manifest, root / "manifests" / "x" / "acquisition_manifest.json", root / "manifests" / "latest_manifest.json")

    def test_etf_fail_closed_guard_is_scoped_to_v2_identifier(self):
        from strategies.relative_strength_rotation import _normalize_relative_strength_params, _relative_strength_selection
        index = pd.date_range("2023-01-01", periods=220, freq="D")
        frame = pd.DataFrame({"Open": range(20, 240), "High": range(21, 241), "Low": range(19, 239), "Close": range(20, 240), "Volume": [1000] * 220}, index=index)
        legacy = _normalize_relative_strength_params({"rs_use_market_regime": False, "rs_rebalance_frequency": "monthly"})
        _relative_strength_selection({"QQQ": frame}, legacy)
        v2 = dict(legacy, strategy_identifier="rs_m126_top30_sma100_no_regime_v2_clean_stock")
        with self.assertRaisesRegex(ValueError, "Blocked ETF"):
            _relative_strength_selection({"QQQ": frame}, v2)


if __name__ == "__main__":
    unittest.main()
