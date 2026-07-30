"""Tests for the informational metric-drift mechanism (Phase 9A).

These pin the CONTRACT the operator decision rests on:
  - drift is reported, never raised
  - a move beyond threshold emits the marker the suite runner lifts into
    SUITE SUMMARY
  - a move within threshold stays quiet
  - the suite runner actually parses that marker

The companion proof -- that the selection fingerprint still fails on a real
regression -- lives in tests/test_rs_v2_tiingo_strategy_baseline.py, because it
needs the dataset.
"""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "testdata"))

from baseline_metric_drift import (  # noqa: E402
    DRIFT_RELATIVE_THRESHOLD,
    MARKER_PREFIX,
    compare_metrics,
    report_metric_drift,
    reset_for_testing,
)
from run_test_suite import _inactive_baseline_guards, _metric_drift_guards  # noqa: E402

# Shaped like the real artifact, with round synthetic values.
BASE = {
    "net_return_pct": 100.0,
    "trade_count": 700,
    "cagr_pct": 10.0,
    "max_drawdown_pct": -10.0,
    "sharpe_ratio": 1.0,
    "win_rate_pct": 50.0,
}


def _capture(guard_name, expected, observed):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        records = report_metric_drift(guard_name, expected, observed)
    return records, buffer.getvalue()


class CompareMetricsTest(unittest.TestCase):
    def test_identical_metrics_produce_zero_deltas(self):
        records = compare_metrics(BASE, dict(BASE))
        self.assertTrue(all(r["absolute_delta"] == 0.0 for r in records))
        self.assertFalse(any(r["beyond_threshold"] for r in records))

    def test_relative_delta_is_scale_free(self):
        observed = dict(BASE, net_return_pct=110.0)
        record = next(r for r in compare_metrics(BASE, observed) if r["metric"] == "net_return_pct")
        self.assertAlmostEqual(record["relative_delta"], 0.10)

    def test_negative_baseline_uses_magnitude(self):
        """max_drawdown is negative; a deeper drawdown must read as positive drift."""
        observed = dict(BASE, max_drawdown_pct=-12.0)
        record = next(r for r in compare_metrics(BASE, observed) if r["metric"] == "max_drawdown_pct")
        self.assertAlmostEqual(record["relative_delta"], 0.20)
        self.assertTrue(record["beyond_threshold"])

    def test_zero_baseline_yields_no_relative_delta(self):
        record = next(
            r for r in compare_metrics({"sharpe_ratio": 0.0}, {"sharpe_ratio": 0.4})
            if r["metric"] == "sharpe_ratio"
        )
        self.assertIsNone(record["relative_delta"])
        self.assertFalse(record["beyond_threshold"])

    def test_missing_observed_metric_is_not_silently_scored(self):
        record = next(r for r in compare_metrics(BASE, {}) if r["metric"] == "cagr_pct")
        self.assertIsNone(record["observed"])
        self.assertIsNone(record["absolute_delta"])
        self.assertFalse(record["beyond_threshold"])

    def test_threshold_boundary_is_exclusive(self):
        exactly_at = dict(BASE, cagr_pct=10.0 * (1 + DRIFT_RELATIVE_THRESHOLD))
        record = next(r for r in compare_metrics(BASE, exactly_at) if r["metric"] == "cagr_pct")
        self.assertFalse(record["beyond_threshold"], "a move exactly at the threshold must not trip it")


class ReportMetricDriftTest(unittest.TestCase):
    def setUp(self):
        reset_for_testing()
        self.addCleanup(reset_for_testing)

    def test_reporting_never_raises(self):
        """The whole point: drift is informational and must not fail the suite."""
        wild = {key: (value * 100 if isinstance(value, (int, float)) else value) for key, value in BASE.items()}
        records, _ = _capture("guard_a", BASE, wild)
        self.assertEqual(len(records), len(BASE))

    def test_exact_match_reports_no_drift_and_no_marker(self):
        _, output = _capture("guard_a", BASE, dict(BASE))
        self.assertIn("match the frozen baseline exactly", output)
        self.assertNotIn(MARKER_PREFIX, output)

    def test_small_move_is_recorded_but_stays_quiet(self):
        """A routine vendor re-adjustment must NOT emit the loud marker."""
        observed = dict(BASE, max_drawdown_pct=-10.21, trade_count=698)
        _, output = _capture("guard_a", BASE, observed)
        self.assertNotIn(MARKER_PREFIX, output)
        self.assertIn("within the 10% informational threshold", output)
        self.assertIn("max_drawdown_pct", output)

    def test_large_move_emits_the_marker(self):
        observed = dict(BASE, net_return_pct=50.0)
        _, output = _capture("guard_a", BASE, observed)
        self.assertIn(MARKER_PREFIX, output)
        self.assertIn("guard=guard_a", output)
        self.assertIn("BEYOND THRESHOLD", output)

    def test_marker_is_announced_once_per_guard(self):
        observed = dict(BASE, net_return_pct=50.0)
        _, first = _capture("guard_a", BASE, observed)
        _, second = _capture("guard_a", BASE, observed)
        self.assertIn(MARKER_PREFIX, first)
        self.assertNotIn(MARKER_PREFIX, second)


class SuiteRunnerParsesTheMarkerTest(unittest.TestCase):
    """The marker is worthless if the suite runner does not lift it into SUITE SUMMARY."""

    def setUp(self):
        reset_for_testing()
        self.addCleanup(reset_for_testing)

    def test_runner_extracts_the_guard_name_from_real_output(self):
        _, output = _capture("rs_v2_tiingo_strategy_baseline", BASE, dict(BASE, net_return_pct=50.0))
        self.assertEqual(_metric_drift_guards(output), ["rs_v2_tiingo_strategy_baseline"])

    def test_drift_and_inactive_markers_do_not_bleed_into_each_other(self):
        _, output = _capture("guard_a", BASE, dict(BASE, net_return_pct=50.0))
        self.assertEqual(_inactive_baseline_guards(output), [])

    def test_clean_output_yields_no_drift_guards(self):
        self.assertEqual(_metric_drift_guards("Ran 10 tests\n\nOK\n"), [])

    def test_inactive_marker_parsing_is_unchanged(self):
        """Regression: the extracted helper must not alter existing behaviour."""
        output = "[BASELINE-GUARD-INACTIVE] guard=rs_v2_sip_daily_only\n"
        self.assertEqual(_inactive_baseline_guards(output), ["rs_v2_sip_daily_only"])


if __name__ == "__main__":
    unittest.main()
