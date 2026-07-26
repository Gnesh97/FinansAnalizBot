"""Frozen strategy baseline guard for the rs_v2_tiingo production dataset (Phase 7A).

This is THE governing regression guard for the live system. It pins both the
headline metrics and a per-rebalance selection fingerprint, so drift is caught
at selection level and not only when it happens to move the headline return.

It supersedes the SIP-era numbers (165.9133%/762): rs_v2_sip carries stale
spinoff adjustments for FDX/CMCSA/MMM/MDT (verified in Phase 6A), so its
returns are computed on prices that no longer reflect those corporate actions.

Tolerances are deliberately tight -- metrics to the stored precision, trade
count and fingerprint exact. The perturbation tests below prove the guard
actually fails on drift rather than passing vacuously.
"""

import json
import sys
import unittest
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

# Phase 7C: this dataset is gitignored, so absent it every assertion below is
# skipped and the suite still prints OK. Announce that loudly instead of letting
# a green run imply the governing baseline was verified.
GUARD_ACTIVE = announce_if_inactive(
    "rs_v2_tiingo_strategy_baseline",
    DATA_FOLDER.exists() and BASELINE_PATH.exists(),
    reason=f"missing {DATA_FOLDER.relative_to(PROJECT_ROOT)} or {BASELINE_PATH.relative_to(PROJECT_ROOT)}",
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

    def test_frozen_headline_metrics(self):
        metrics = _run_relative_strength_rotation_backtest(
            self.asset_data, self.strategy_params, benchmark_data=self.benchmark,
        )
        expected = self.baseline["metrics"]
        self.assertEqual(metrics.get("Islem Sayisi"), expected["trade_count"])
        self.assertAlmostEqual(metrics.get("Net Getiri (%)"), expected["net_return_pct"], places=2)
        self.assertAlmostEqual(metrics.get("CAGR (%)"), expected["cagr_pct"], places=2)
        self.assertAlmostEqual(metrics.get("Max Drawdown (%)"), expected["max_drawdown_pct"], places=2)
        self.assertAlmostEqual(metrics.get("Sharpe Ratio"), expected["sharpe_ratio"], places=2)
        self.assertAlmostEqual(metrics.get("Win Rate (%)"), expected["win_rate_pct"], places=1)

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


if __name__ == "__main__":
    unittest.main()
