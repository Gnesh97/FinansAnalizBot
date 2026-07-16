import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.baseline_parity import (
    BASELINE_NAME,
    append_decision_event,
    archive_new_paper_decision_events,
    build_baseline_specification,
    build_decision_event,
    build_fingerprint,
    compare_fingerprint,
    compare_parity_events,
    evaluate_return_integrity,
    build_common_window_comparison,
    validate_comparison_scope,
    run_read_only_parity_audit,
)


class GoldenBaselineParityTest(unittest.TestCase):
    def _event(self, **overrides):
        event = {
            "signal_timestamp": "2026-01-31T13:50:00-05:00",
            "effective_price_timestamp": "2026-02-01T09:30:00-05:00",
            "eligible_universe": ["AAPL", "MSFT"],
            "ranked_symbols": ["AAPL", "MSFT"],
            "selected_symbols": ["AAPL"],
            "target_weights": {"AAPL": 1.0},
            "order_intents": [{"symbol": "AAPL", "side": "BUY", "notional": 100.0}],
        }
        event.update(overrides)
        return event

    def test_specification_exactly_reflects_production_baseline(self):
        spec = build_baseline_specification(source_commit="test")
        self.assertEqual(spec["strategy_identifier"], BASELINE_NAME)
        self.assertEqual(spec["strategy"]["momentum_lookback_days"], 126)
        self.assertEqual(spec["strategy"]["top_n"], 30)
        self.assertEqual(spec["strategy"]["sma_filter_days"], 100)
        self.assertFalse(spec["strategy"]["use_market_regime"])
        self.assertEqual(spec["strategy"]["rebalance_frequency"], "monthly")
        self.assertEqual(spec["execution"]["short_trading_enabled"], False)

    def test_unintended_config_change_fails_baseline_regression(self):
        fingerprint = build_fingerprint({"Baslangic Kasa": 10000, "Bitis Kasa": 11000}, "abc", "def", "ghi")
        changed = dict(fingerprint)
        changed["config_hashes"] = dict(fingerprint["config_hashes"])
        changed["config_hashes"]["strategy_config_hash"] = "changed"
        result = compare_fingerprint(fingerprint, changed)
        self.assertFalse(result["matches"])
        self.assertIn("strategy_config_hash", result["mismatches"])

    def test_selection_mismatch_detected(self):
        rows = compare_parity_events([self._event()], [self._event(selected_symbols=["MSFT"])])
        self.assertEqual(rows[0]["classification"], "SELECTION_MISMATCH")

    def test_timestamp_mismatch_not_mislabeled_as_ranking(self):
        rows = compare_parity_events(
            [self._event()],
            [self._event(signal_timestamp="2026-02-01T13:50:00-05:00", ranked_symbols=["MSFT", "AAPL"])],
        )
        self.assertEqual(rows[0]["classification"], "DATA_TIMESTAMP_MISMATCH")

    def test_missing_paper_data_is_separate_from_strategy_failure(self):
        rows = compare_parity_events([self._event()], [])
        self.assertEqual(rows[0]["classification"], "EXPECTED_LIVE_DATA_NOT_AVAILABLE")
        self.assertFalse(rows[0]["strategy_failure"])

    def test_comparison_is_deterministic_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame(
                [{"strategy_name": BASELINE_NAME, "signal_date": "2026-01-31T13:50:00-05:00", "symbol": "AAPL", "action": "BUY", "paper_rank": 1, "ranking_score": 1.0, "paper_position_notional": 100.0}]
            ).to_csv(latest / "paper_signal_report.csv", index=False)
            pd.DataFrame(columns=["strategy_name", "signal_date", "symbol", "action"]).to_csv(latest / "paper_pending_orders.csv", index=False)
            before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in latest.iterdir()}
            first = run_read_only_parity_audit(latest)
            second = run_read_only_parity_audit(latest)
            after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in latest.iterdir()}
            self.assertEqual(first, second)
            self.assertEqual(before, after)

    def test_audit_has_no_broker_or_ledger_mutation_path(self):
        source = Path(__file__).resolve().parents[1] / "testdata" / "baseline_parity.py"
        text = source.read_text(encoding="utf-8")
        for forbidden in ("submit_order(", "cancel_order(", "replace_order(", "requests.post(", "broker_order_ledger"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("SHORT_SELL", text)

    def test_event_files_are_append_only_and_same_content_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = build_decision_event(self._event(), "PAPER_DECISION", source_commit="test")
            created = append_decision_event(tmp, event)
            repeated = append_decision_event(tmp, event)
            self.assertTrue(created["created"])
            self.assertFalse(repeated["created"])
            self.assertEqual(len(list(Path(tmp).glob("*.json"))), 1)

    def test_conflicting_duplicate_event_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = build_decision_event(self._event(), "PAPER_DECISION", source_commit="test")
            append_decision_event(tmp, event)
            conflict = dict(event)
            conflict["target_weights"] = {"AAPL": 0.5}
            with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
                append_decision_event(tmp, conflict)

    def test_audit_does_not_backfill_historical_paper_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            pd.DataFrame([{"strategy_name": BASELINE_NAME, "signal_date": "2026-01-31", "symbol": "AAPL", "action": "BUY"}]).to_csv(latest / "paper_signal_report.csv", index=False)
            result = run_read_only_parity_audit(latest, Path(tmp) / "events")
            self.assertEqual(result["archived_paper_decision_events"], 0)
            self.assertGreater(result["scheduled_backtest_rebalance_events"], 1)
            self.assertEqual(result["paper_observation_missing_events"], result["archived_backtest_decision_events"])

    def test_detailed_weight_mismatch_and_timestamp_mismatch_are_separate(self):
        weight_rows = compare_parity_events([self._event()], [self._event(target_weights={"AAPL": 0.5})])
        time_rows = compare_parity_events([self._event()], [self._event(effective_price_timestamp="2026-02-02T09:30:00-05:00", ranked_symbols=["MSFT", "AAPL"])])
        self.assertEqual(weight_rows[0]["classification"], "TARGET_WEIGHT_MISMATCH")
        self.assertEqual(time_rows[0]["classification"], "DATA_TIMESTAMP_MISMATCH")
        self.assertFalse(time_rows[0]["ranking_match"])

    def test_missing_archived_paper_event_is_not_strategy_failure(self):
        rows = compare_parity_events([self._event()], [])
        self.assertFalse(rows[0]["strategy_failure"])
        self.assertEqual(rows[0]["classification"], "EXPECTED_LIVE_DATA_NOT_AVAILABLE")

    def test_current_paper_decision_archives_distinct_current_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = pd.DataFrame(
                [{"strategy_name": BASELINE_NAME, "signal_date": "2026-01-31", "symbol": "AAPL", "action": "BUY", "paper_rank": 1, "ranking_score": 2.0, "paper_position_notional": 100.0, "paper_capital_assumption": 1000.0, "warning": ""}]
            )
            archive_new_paper_decision_events(report, {"AAPL": pd.DataFrame()}, tmp)
            types = {json.loads(path.read_text(encoding="utf-8"))["event_type"] for path in Path(tmp).glob("*.json")}
            self.assertEqual(types, {"BACKTEST_DECISION", "PAPER_DECISION"})


class GoldenBaselineMetricIntegrityTest(unittest.TestCase):
    def test_baseline_total_return_and_cagr_reconcile_with_strategy_period(self):
        result = evaluate_return_integrity(
            10000.0, 28652.58, "2018-01-02T00:00:00-05:00", "2026-07-07T00:00:00-04:00", 186.53, 13.17
        )
        self.assertEqual(result["metric_integrity_status"], "PASS")
        self.assertAlmostEqual(result["independently_recomputed_total_return_pct"], 186.5258, places=4)

    def test_recorded_benchmark_window_produces_approximately_1938_cagr(self):
        result = evaluate_return_integrity(
            10000.0, 28652.58, "2020-07-27T09:30:00-04:00", "2026-07-07T00:00:00-04:00", 186.53, 13.17
        )
        self.assertEqual(result["metric_integrity_status"], "FAIL")
        self.assertAlmostEqual(result["independently_recomputed_cagr_pct"], 19.3784, places=4)

    def test_qqq_total_return_and_cagr_reconcile(self):
        result = evaluate_return_integrity(
            100.0, 292.11, "2020-07-27T09:30:00-04:00", "2026-07-07T00:00:00-04:00", 192.11, 19.77
        )
        self.assertEqual(result["metric_integrity_status"], "PASS")

    def test_wrong_cagr_and_source_column_mapping_fail(self):
        wrong_cagr = evaluate_return_integrity(100.0, 200.0, "2020-01-01", "2021-01-01", 100.0, 25.0)
        wrong_total = evaluate_return_integrity(100.0, 200.0, "2020-01-01", "2021-01-01", 50.0, 100.0)
        self.assertEqual(wrong_cagr["metric_integrity_status"], "FAIL")
        self.assertEqual(wrong_total["metric_integrity_status"], "FAIL")

    def test_external_cash_flows_fail_closed_for_simple_cagr(self):
        with self.assertRaisesRegex(ValueError, "external cash flows"):
            evaluate_return_integrity(100.0, 200.0, "2020-01-01", "2021-01-01", 100.0, 100.0, external_cash_flows=[10.0])

    def test_metric_metadata_does_not_change_baseline_config_hashes(self):
        spec = build_baseline_specification(source_commit="test")
        original = (spec["strategy_config_hash"], spec["benchmark_config_hash"], spec["execution_config_hash"])
        metadata = {"metric_schema_version": "2.0.0", "return_measure": "time_weighted_no_cash_flows"}
        self.assertEqual(original, (spec["strategy_config_hash"], spec["benchmark_config_hash"], spec["execution_config_hash"]))
        self.assertEqual(metadata["metric_schema_version"], "2.0.0")

    def test_metric_correction_does_not_change_equity_or_trade_values(self):
        frozen_trade_and_equity = {"initial_equity": 10000.0, "final_equity": 28652.58, "trade_count": 790}
        integrity = evaluate_return_integrity(10000.0, 28652.58, "2018-01-02T00:00:00-05:00", "2026-07-07T00:00:00-04:00", 186.53, 13.17)
        self.assertEqual(frozen_trade_and_equity, {"initial_equity": 10000.0, "final_equity": 28652.58, "trade_count": 790})
        self.assertEqual(integrity["metric_integrity_status"], "PASS")


class GoldenBaselineComparabilityTest(unittest.TestCase):
    def _series(self):
        index = pd.date_range("2021-01-01", periods=4, freq="D", tz="UTC")
        return pd.Series([100.0, 105.0, 110.0, 121.0], index=index), pd.Series([100.0, 102.0, 104.0, 108.0], index=index)

    def test_different_periods_and_old_excess_fail(self):
        result = validate_comparison_scope("2020-01-01", "2021-01-01", "2020-02-01", "2021-01-01")
        self.assertEqual(result["comparison_integrity_status"], "FAIL")
        self.assertIn("start_dates_differ", result["comparison_failure_reasons"])
        self.assertFalse(result["explicit_excess_total_return_valid"])

    def test_common_window_rebases_and_reconciles_returns(self):
        strategy, benchmark = self._series()
        result = build_common_window_comparison(strategy, benchmark)
        self.assertEqual(result["comparison_integrity_status"], "PASS")
        self.assertEqual(result["strategy_rebased"].iloc[0], 1.0)
        self.assertEqual(result["benchmark_rebased"].iloc[0], 1.0)
        self.assertAlmostEqual(result["strategy_common_window_total_return_pct"], 21.0)

    def test_common_window_cagrs_share_identical_dates(self):
        strategy, benchmark = self._series()
        result = build_common_window_comparison(strategy, benchmark)
        self.assertEqual(result["strategy_common_window_start"], result["benchmark_common_window_start"])
        self.assertEqual(result["strategy_common_window_end"], result["benchmark_common_window_end"])

    def test_synthetic_flat_and_missing_observations_fail_closed(self):
        strategy, benchmark = self._series()
        flat = pd.Series([100.0, 100.0, 100.0, 121.0], index=strategy.index)
        missing = benchmark.drop(benchmark.index[2])
        self.assertIn("synthetic_flat_pre_data_period", build_common_window_comparison(flat, benchmark)["comparison_failure_reasons"])
        self.assertEqual(build_common_window_comparison(strategy, missing)["comparison_integrity_status"], "FAIL")

    def test_first_trade_is_not_equity_curve_start(self):
        self.assertNotEqual(pd.Timestamp("2018-01-02"), pd.Timestamp("2021-02-01"))


if __name__ == "__main__":
    unittest.main()
