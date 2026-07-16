from __future__ import annotations

import tempfile
import unittest
import hashlib
import json
from pathlib import Path

import pandas as pd

from testdata.v2_daily_gap_selection_audit import (
    BLOCKED_ETFS,
    apply_required_session_mask,
    build_in_memory_repaired_daily,
    calibrate_symbol_equivalence,
    compute_production_features,
    rank_candidate_features,
    reconstruct_daily_bar,
    audit_v2_daily_gap_selection,
    spearman_rank_correlation,
    valid_daily_sessions,
    validate_audit_contract,
)
from testdata.vericekici_updated import v2_offline_audit_guard


def _session(day: str, close_hour: int = 16) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(f"{day} 09:30", tz="America/New_York")
    close = pd.Timestamp(f"{day} {close_hour:02d}:00", tz="America/New_York")
    return start, close


def _raw(start: pd.Timestamp, close: pd.Timestamp, base: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range(start, close - pd.Timedelta(minutes=5), freq="5min")
    values = pd.Series(range(len(dates)), dtype=float)
    return pd.DataFrame({
        "Date": dates,
        "Open": base + values,
        "High": base + values + 2,
        "Low": base + values - 2,
        "Close": base + values + 1,
        "Volume": [10] * len(dates),
    })


def _failed_run_schedule() -> pd.DataFrame:
    path = Path(__file__).resolve().parent / "fixtures" / "nyse_sessions_2020-07-27_2026-07-07.csv"
    fixture = pd.read_csv(path)
    fixture.loc[fixture["session"].eq("2026-07-02"), "market_close_time"] = "16:00"
    sessions = pd.DatetimeIndex(pd.to_datetime(fixture["session"]))
    opens = pd.to_datetime(fixture["session"] + " 09:30").dt.tz_localize("America/New_York")
    closes = pd.to_datetime(fixture["session"] + " " + fixture["market_close_time"]).dt.tz_localize("America/New_York")
    return pd.DataFrame({"market_open": list(opens), "market_close": list(closes)}, index=sessions)


class TestDailyGapRepairability(unittest.TestCase):
    def test_rank_correlation_has_no_scipy_dependency(self):
        self.assertAlmostEqual(spearman_rank_correlation({"A": 1, "B": 2, "C": 3}, {"A": 3, "B": 2, "C": 1}, ["A", "B", "C"]), -1.0)

    def test_frozen_contract_rejects_tampered_candidate_hash_or_roles(self):
        baseline = {"strategy_identifier": "rs_m126_top30_sma100_no_regime_v2_clean_stock", "universe": {"candidate_symbols": ["AAA"], "candidate_count": 1, "candidate_universe_hash": "bad", "excluded_symbols": []}}
        manifest = {"acquisition_run_id": "run", "dataset_profile": "rs_v2_clean", "files": [{"symbol": "AAA", "timeframe": "1D", "asset_role": "CANDIDATE"}]}
        with self.assertRaisesRegex(ValueError, "CONTRACT"):
            validate_audit_contract(manifest, baseline, "run", expected_candidate_count=1, expected_candidate_hash="expected")

    def test_frozen_contract_rejects_intraday_role_or_strategy_parameter_tampering(self):
        digest = hashlib.sha256(b"AAA").hexdigest()
        strategy = {"momentum_lookback_days": 126, "volatility_lookback_days": 60, "sma_filter_days": 100, "top_n": 30, "use_market_regime": False, "score_mode": "return_over_vol", "rebalance_frequency": "monthly", "max_atr_pct": 12}
        baseline = {"strategy_identifier": "rs_m126_top30_sma100_no_regime_v2_clean_stock", "strategy": strategy, "universe": {"candidate_symbols": ["AAA"], "candidate_count": 1, "candidate_universe_hash": digest, "excluded_symbols": sorted(BLOCKED_ETFS), "min_price": 10}}
        files = []
        for symbol, role in (("AAA", "CANDIDATE"), ("QQQ", "PRIMARY_BENCHMARK"), ("SPY", "SECONDARY_BENCHMARK")):
            files.extend([{"symbol": symbol, "timeframe": timeframe, "asset_role": role} for timeframe in ("1D", "130min")])
        manifest = {"acquisition_run_id": "run", "dataset_profile": "rs_v2_clean", "strategy_identifier": baseline["strategy_identifier"], "candidate_count": 1, "candidate_universe_hash": digest, "files": files}
        validate_audit_contract(manifest, baseline, "run", expected_candidate_count=1, expected_candidate_hash=digest)
        manifest["files"][1]["asset_role"] = "PRIMARY_BENCHMARK"
        with self.assertRaisesRegex(ValueError, "UNIVERSE_CONTRACT"):
            validate_audit_contract(manifest, baseline, "run", expected_candidate_count=1, expected_candidate_hash=digest)
        manifest["files"][1]["asset_role"] = "CANDIDATE"
        baseline["strategy"]["top_n"] = 31
        with self.assertRaisesRegex(ValueError, "STRATEGY_CONTRACT"):
            validate_audit_contract(manifest, baseline, "run", expected_candidate_count=1, expected_candidate_hash=digest)
        baseline["strategy"]["top_n"] = 30
        baseline["universe"]["min_price"] = 11
        with self.assertRaisesRegex(ValueError, "UNIVERSE_CONTRACT"):
            validate_audit_contract(manifest, baseline, "run", expected_candidate_count=1, expected_candidate_hash=digest)

    def test_invalid_daily_ohlcv_is_not_available_to_required_session_mask(self):
        daily = pd.DataFrame([
            {"Date": "2024-01-02 21:00:00+00:00", "Open": 10, "High": 11, "Low": 9, "Close": 10.5, "Volume": 100},
            {"Date": "2024-01-03 21:00:00+00:00", "Open": 10, "High": 9, "Low": 11, "Close": 10.5, "Volume": 100},
        ])
        self.assertEqual(valid_daily_sessions(daily), {pd.Timestamp("2024-01-02")})

    def test_complete_regular_raw_session_reconstructs_daily_ohlcv(self):
        market_open, market_close = _session("2024-01-02")
        raw = _raw(market_open, market_close)
        result = reconstruct_daily_bar(raw, market_open, market_close)
        self.assertEqual(result["status"], "COMPLETE_RAW_SESSION")
        self.assertEqual(result["bar"], {
            "Open": float(raw.iloc[0]["Open"]), "High": float(raw["High"].max()),
            "Low": float(raw["Low"].min()), "Close": float(raw.iloc[-1]["Close"]),
            "Volume": float(raw["Volume"].sum()),
        })

    def test_complete_early_close_reconstructs_only_preclose_rows(self):
        market_open, market_close = _session("2024-07-03", 13)
        raw = pd.concat([_raw(market_open, market_close), _raw(market_close, market_close + pd.Timedelta(hours=1), 1000)])
        result = reconstruct_daily_bar(raw, market_open, market_close)
        self.assertEqual((result["status"], result["rth_row_count"]), ("COMPLETE_RAW_SESSION", 42))
        self.assertLess(result["bar"]["Close"], 1000)

    def test_absent_and_incomplete_sessions_are_not_repairable(self):
        market_open, market_close = _session("2024-01-02")
        absent = reconstruct_daily_bar(pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"]), market_open, market_close)
        incomplete = reconstruct_daily_bar(_raw(market_open, market_close).iloc[:-1], market_open, market_close)
        self.assertEqual(absent["status"], "RAW_SESSION_ABSENT")
        self.assertEqual(incomplete["status"], "RAW_SESSION_PRESENT_BUT_INCOMPLETE")

    def test_equivalence_must_be_demonstrated_and_split_discontinuity_rejected(self):
        schedule_rows = []
        daily_rows = []
        raw_frames = []
        for index, day in enumerate(("2024-01-02", "2024-01-03", "2024-01-04")):
            market_open, market_close = _session(day)
            raw = _raw(market_open, market_close, 100 + index)
            reconstructed = reconstruct_daily_bar(raw, market_open, market_close)["bar"]
            schedule_rows.append((pd.Timestamp(day), market_open, market_close))
            daily_rows.append({"Date": market_close, **reconstructed})
            raw_frames.append(raw)
        schedule = pd.DataFrame(
            {"market_open": [row[1] for row in schedule_rows], "market_close": [row[2] for row in schedule_rows]},
            index=pd.DatetimeIndex([row[0] for row in schedule_rows]),
        )
        daily = pd.DataFrame(daily_rows)
        raw = pd.concat(raw_frames, ignore_index=True)
        proven = calibrate_symbol_equivalence(daily, raw, schedule, min_compared_sessions=3)
        self.assertTrue(proven["equivalence_proven"])
        insufficient = calibrate_symbol_equivalence(daily, raw, schedule, min_compared_sessions=4)
        self.assertEqual(insufficient["equivalence_reason"], "INSUFFICIENT_COMPLETE_OVERLAPS")
        duplicated = pd.concat([daily, daily.iloc[[0]]], ignore_index=True)
        invalid_calibration = calibrate_symbol_equivalence(duplicated, raw, schedule, min_compared_sessions=3)
        self.assertFalse(invalid_calibration["equivalence_proven"])
        self.assertEqual(invalid_calibration["equivalence_reason"], "INVALID_DAILY_CALIBRATION_INPUT")
        daily.loc[1, ["Open", "High", "Low", "Close"]] *= 0.5
        rejected = calibrate_symbol_equivalence(daily, raw, schedule, min_compared_sessions=3)
        self.assertFalse(rejected["equivalence_proven"])
        self.assertGreater(rejected["split_adjustment_discontinuity_count"], 0)

    def test_in_memory_repair_does_not_write_csv(self):
        market_open, market_close = _session("2024-01-02")
        daily = pd.DataFrame([{"Date": market_close, "Open": 1, "High": 2, "Low": 0, "Close": 1.5, "Volume": 10}])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repaired = build_in_memory_repaired_daily(daily, {pd.Timestamp("2024-01-03"): {"Open": 2, "High": 3, "Low": 1, "Close": 2.5, "Volume": 20}})
            self.assertEqual(len(repaired), 2)
            self.assertEqual(list(root.glob("*.csv")), [])

    def test_fail_closed_mask_only_applies_when_required_window_contains_gap(self):
        sessions = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]))
        available = set(sessions) - {sessions[1]}
        mask = apply_required_session_mask(available, sessions, [sessions[2], sessions[4]], required_observation_count=2)
        self.assertEqual(mask, {sessions[2]: False, sessions[4]: True})

    def test_candidate_ranking_uses_production_features_and_blocks_all_etfs(self):
        dates = pd.bdate_range("2023-01-02", periods=140)
        candidates = {}
        for offset, symbol in enumerate(("AAA", "BBB")):
            close = pd.Series(range(100 + offset, 240 + offset), index=dates, dtype=float)
            frame = pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1000}, index=dates)
            candidates[symbol] = frame
        params = {"rs_lookback_days": 126, "rs_vol_lookback_days": 60, "rs_trend_sma_days": 100, "rs_top_n": 30, "rs_rebalance_frequency": "monthly", "rs_score_mode": "return_over_vol", "rs_min_price": 10, "rs_max_atr_pct": 12, "rs_use_market_regime": False}
        features = compute_production_features(candidates, params)
        ranking = rank_candidate_features(features, dates[-1], set(candidates), top_n=30)
        self.assertTrue(set(ranking["selected"]).issubset(candidates))
        self.assertTrue(BLOCKED_ETFS.isdisjoint(ranking["selected"]))
        self.assertEqual(len(BLOCKED_ETFS), 22)


class TestFrozenDailyGapSelectionAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1] / "testdata" / "research_data" / "rs_v2_clean_market_data"
        cls.run_id = "acq_20260712_212749"
        cls.manifest_path = cls.root / "manifests" / cls.run_id / "acquisition_manifest.json"
        if not cls.manifest_path.exists():
            raise unittest.SkipTest("frozen failed V2 run is not present")
        manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))
        referenced = {cls.manifest_path, Path(__file__).resolve().parents[1] / "testdata" / "baselines" / "rs_v2_clean_coverage_exceptions.json"}
        for record in manifest["files"]:
            referenced.add(cls.root / record["output_relative_path"])
            if record.get("source_raw_5min_relative_path"):
                referenced.add(cls.root / record["source_raw_5min_relative_path"])
        cls.before_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in referenced}
        cls.temp = tempfile.TemporaryDirectory()
        with v2_offline_audit_guard() as telemetry:
            cls.result = audit_v2_daily_gap_selection(
                cls.root, cls.run_id, Path(cls.temp.name),
                market_schedule=_failed_run_schedule(), offline_telemetry=telemetry,
            )
        cls.telemetry = dict(telemetry)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp"):
            cls.temp.cleanup()

    def test_exact_repairability_counts_keep_boundaries_separate(self):
        counts = self.result["repair_counts"]
        self.assertEqual(len(self.result["repairability"]), 1766)
        self.assertEqual(counts.get("REPAIRABLE_FROM_COMPLETE_RAW_SESSION", 0), 0)
        self.assertEqual(counts["RAW_SESSION_ABSENT"], 350)
        self.assertEqual(counts["BOUNDARY_NOT_APPLICABLE"], 1416)

    def test_scenario_c_equals_fail_closed_when_no_gap_is_repairable(self):
        impact = self.result["selection_impact"]
        self.assertTrue(impact["scenario_b_top30"].eq(impact["scenario_c_top30"]).all())
        self.assertTrue(impact["b_c_selection_status_change_count"].eq(0).all())

    def test_benchmarks_and_all_excluded_etfs_never_enter_ranking(self):
        impact = self.result["selection_impact"]
        self.assertFalse(impact["qqq_or_spy_entered_candidate_ranking"].any())
        self.assertFalse(impact["excluded_etf_entered_candidate_ranking"].any())

    def test_audit_is_offline_and_preserves_frozen_inputs(self):
        self.assertEqual((self.telemetry["network_attempt_count"], self.telemetry["client_attempt_count"]), (0, 0))
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.before_hashes}
        self.assertEqual(after, self.before_hashes)
        self.assertFalse((self.root / "manifests" / "latest_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
