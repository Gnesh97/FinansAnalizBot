"""Frozen strategy baseline guard for the rs_v2_tiingo production dataset (Phase 7A).

This is THE governing regression guard for the live system.

It supersedes the SIP-era numbers (165.9133%/762): rs_v2_sip carries stale
spinoff adjustments for FDX/CMCSA/MMM/MDT (verified in Phase 6A), so its
returns are computed on prices that no longer reflect those corporate actions.

STRUCTURE (Phase 9A -- operator decision, recorded in
reports/audit/phase9a_sharpe_fix_and_guard_restructure_20260729.md)

  HARD, EXACT assertions -- these fail the suite:
    - the per-rebalance selection fingerprint
    - the frozen 177-symbol universe
    - the retained target end session
    - structural presence of every pinned headline metric

  INFORMATIONAL -- recorded and reported, never fails the suite:
    - the headline metric VALUES (return, trades, CAGR, max drawdown, Sharpe,
      win rate)

Why the split: Tiingo retroactively re-adjusts prices for corporate actions, and
the nightly refresh re-acquires those symbols' full history. On 2026-07-27 that
happened for CVS, PFE, PG and T; the headline metrics moved while the selection
fingerprint matched exactly. Failing the suite for that trains the operator to
ignore the governing guard -- the same failure mode as the Phase 8E notice spam.

The fingerprint is the real invariant: it proves ranking/selection is unchanged
regardless of vendor price adjustments. The perturbation tests below prove it
still fails on drift rather than passing vacuously.

When metrics move legitimately, re-freeze with provenance:
    python3 scripts/refreeze_tiingo_strategy_baseline.py --help
"""

import io
import json
import math
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

from testdata.runner import _default_params, _load_or_build_prepared_cache
from testdata.baseline_parity import _baseline_experiment
from testdata.strategies.relative_strength_rotation import (
    _normalize_relative_strength_params,
    _relative_strength_selection,
    _run_relative_strength_rotation_backtest,
)
from testdata.vericekici_updated import load_v2_frozen_universe

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FOLDER = PROJECT_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data"
BASELINE_PATH = PROJECT_ROOT / "testdata" / "baselines" / "rs_v2_tiingo_strategy_baseline.json"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_tiingo_strategy_baseline import selection_fingerprint  # noqa: E402
from baseline_guard_visibility import announce_if_inactive  # noqa: E402
from baseline_metric_drift import MARKER_PREFIX, report_metric_drift  # noqa: E402

# Phase 7C: this dataset is gitignored, so absent it every assertion below is
# skipped and the suite still prints OK. Announce that loudly instead of letting
# a green run imply the governing baseline was verified.
_MISSING = [
    str(path.relative_to(PROJECT_ROOT))
    for path in (DATA_FOLDER, BASELINE_PATH)
    if not path.exists()
]
GUARD_ACTIVE = announce_if_inactive(
    "rs_v2_tiingo_strategy_baseline",
    not _MISSING,
    reason=f"missing {', '.join(_MISSING)}" if _MISSING else "",
    restore_hint="acquire the rs_v2_tiingo dataset, then rerun scripts/build_tiingo_strategy_baseline.py",
)


def _load_context():
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    daily_dir = DATA_FOLDER / "daily"
    intraday_dir = DATA_FOLDER / "bars_130min"
    symbols = sorted(p.name.removesuffix("_1D.csv") for p in daily_dir.glob("*_1D.csv"))
    prepared, _, _ = _load_or_build_prepared_cache(
        str(DATA_FOLDER), str(intraday_dir), str(daily_dir), "130min", "1D",
        baseline["window"]["target_start"], baseline["window"]["slice_upper_bound"], symbols,
    )
    experiment = _baseline_experiment()
    params = _default_params(experiment, str(DATA_FOLDER), "full", False)
    strategy_params = _normalize_relative_strength_params(params)
    return baseline, prepared, strategy_params, symbols


@unittest.skipUnless(GUARD_ACTIVE,
                     "rs_v2_tiingo dataset or frozen strategy baseline not present")
class TiingoStrategyBaselineGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline, cls.prepared, cls.strategy_params, cls.symbols = _load_context()
        cls.asset_data = cls.prepared["asset_data"]
        cls.benchmark = cls.prepared.get("benchmark_data")

    def _fingerprint(self, asset_data):
        _, selections, _, _ = _relative_strength_selection(
            asset_data, self.strategy_params, benchmark_data=self.benchmark,
        )
        digest, _ = selection_fingerprint(selections)
        return digest

    def test_universe_is_exactly_the_frozen_177(self):
        # Phase 7A: the SIP guard silently ran on 178 symbols because retired BK
        # was still on disk, which alone moved trade count by 9.
        frozen, _ = load_v2_frozen_universe()
        self.assertEqual(sorted(self.symbols), sorted(frozen))
        self.assertEqual(len(self.symbols), self.baseline["universe"]["candidate_count"])

    def test_window_retains_the_target_end_session(self):
        # A bare "YYYY-MM-DD" upper bound resolves to 00:00Z and would drop the
        # 04:00Z bar of that session -- the Phase 5D defect. Pin it.
        target = self.baseline["window"]["target_end_session"]
        last_sessions = {str(f.index.max().date()) for f in self.asset_data.values()}
        self.assertIn(target, last_sessions)

    def test_headline_metrics_are_recorded_and_reported(self):
        """INFORMATIONAL by operator decision (Phase 9A). Does not fail on value drift.

        Tiingo re-adjusts prices retroactively for corporate actions, which moves
        these figures without any change to strategy logic. Failing here for that
        reason would train the operator to ignore the governing guard, so the
        values are reported instead and the fingerprint carries the invariant.

        What IS asserted hard: every pinned metric must still be PRODUCED, and be
        a real number. A metric that vanishes or turns into None/NaN is a
        structural defect in the metrics path, not vendor drift.
        """
        metrics = _run_relative_strength_rotation_backtest(
            self.asset_data, self.strategy_params, benchmark_data=self.benchmark,
        )
        observed = {
            "net_return_pct": metrics.get("Net Getiri (%)"),
            "trade_count": metrics.get("Islem Sayisi"),
            "cagr_pct": metrics.get("CAGR (%)"),
            "max_drawdown_pct": metrics.get("Max Drawdown (%)"),
            "sharpe_ratio": metrics.get("Sharpe Ratio"),
            "win_rate_pct": metrics.get("Win Rate (%)"),
        }
        expected = self.baseline["metrics"]

        for key in expected:
            self.assertIn(key, observed, f"baseline pins {key} but the guard does not read it")
            value = observed[key]
            self.assertIsNotNone(value, f"{key} was not produced by the backtest")
            self.assertIsInstance(value, (int, float), f"{key} is not numeric: {value!r}")
            self.assertFalse(math.isnan(float(value)), f"{key} is NaN")

        report_metric_drift("rs_v2_tiingo_strategy_baseline", expected, observed)

    def test_selection_fingerprint_matches(self):
        self.assertEqual(self._fingerprint(self.asset_data),
                         self.baseline["selection"]["fingerprint_sha256"])

    def test_fingerprint_fails_on_price_perturbation(self):
        """Proof the guard is not vacuous: nudging one symbol's prices must break it."""
        frozen_digest = self.baseline["selection"]["fingerprint_sha256"]
        perturbed = dict(self.asset_data)
        victim = "AAPL"
        frame = self.asset_data[victim].copy(deep=True)
        frame.attrs = dict(self.asset_data[victim].attrs)
        frame["Close"] = frame["Close"] * 1.5
        perturbed[victim] = frame
        self.assertNotEqual(self._fingerprint(perturbed), frozen_digest)

    def test_fingerprint_fails_on_universe_perturbation(self):
        """A single dropped candidate must also break the fingerprint."""
        frozen_digest = self.baseline["selection"]["fingerprint_sha256"]
        perturbed = {s: f for s, f in self.asset_data.items() if s != "MPC"}
        self.assertNotEqual(self._fingerprint(perturbed), frozen_digest)

    def test_metric_movement_alone_cannot_fail_this_guard(self):
        """The other half of the Phase 9A split, asserted at guard level.

        A vendor re-adjustment moves metrics without moving selections. Pinning
        metrics exactly made that fail the suite, which is what trained the
        operator to ignore the guard. Prove the informational path cannot raise,
        even for a move far larger than any re-adjustment would produce.
        """
        metrics = _run_relative_strength_rotation_backtest(
            self.asset_data, self.strategy_params, benchmark_data=self.benchmark,
        )
        observed = {
            "net_return_pct": metrics.get("Net Getiri (%)"),
            "trade_count": metrics.get("Islem Sayisi"),
            "cagr_pct": metrics.get("CAGR (%)"),
            "max_drawdown_pct": metrics.get("Max Drawdown (%)"),
            "sharpe_ratio": metrics.get("Sharpe Ratio"),
            "win_rate_pct": metrics.get("Win Rate (%)"),
        }
        absurd = {key: value * 10 for key, value in self.baseline["metrics"].items()}
        # Capture stdout: this probe deliberately trips the marker, and an
        # uncaptured marker would be lifted into the suite-level SUITE SUMMARY
        # on every run. That would train the operator to ignore a drift warning
        # -- precisely the failure this restructure exists to prevent.
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            records = report_metric_drift("phase9a_metric_movement_probe", absurd, observed)
        self.assertTrue(any(r["beyond_threshold"] for r in records),
                        "a 10x baseline shift must register as beyond threshold")
        self.assertIn(MARKER_PREFIX, buffer.getvalue())
        # Reaching here at all is the assertion: reporting did not raise. The
        # fingerprint, asserted exactly above, is what carries the invariant.
        self.assertEqual(self._fingerprint(self.asset_data),
                         self.baseline["selection"]["fingerprint_sha256"])


if __name__ == "__main__":
    unittest.main()
