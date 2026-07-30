"""Fail-closed behaviour of the baseline re-freeze procedure (Phase 9A).

The re-freeze script writes the GOVERNING baseline. Its refusals are the safety
property, so they are tested rather than trusted:

  - a changed selection fingerprint is refused unless explicitly forced
  - a dataset that is not COMPLETE_VALIDATED is refused
  - an existing history artifact is never clobbered
  - --reason is mandatory for a real run

These tests patch `build()` and `_load_manifest()` so no backtest runs and no
production baseline, manifest or dataset is touched.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import refreeze_tiingo_strategy_baseline as refreeze  # noqa: E402

FINGERPRINT_A = "a" * 64
FINGERPRINT_B = "b" * 64

BASELINE = {
    "generated_at": "2026-01-01T00:00:00.000000Z",
    "metrics": {"net_return_pct": 100.0, "trade_count": 700},
    "selection": {"fingerprint_sha256": FINGERPRINT_A},
}

MANIFEST = {
    "acquisition_run_id": "acq_20260727_220002",
    "status": "COMPLETE_VALIDATED",
    "target_end_date": "2026-07-27",
    "validation_completed_at": "2026-07-28T16:31:09.260471Z",
    "drift_detected_symbols": ["CVS", "PFE", "PG", "T"],
    "reacquisition_item_count": 4,
    "candidate_universe_hash": "0" * 64,
    "_pointer": {"immutable_manifest_sha256": "f" * 64},
}


def _fresh(fingerprint=FINGERPRINT_A):
    return {
        "generated_at": "2026-07-29T00:00:00.000000Z",
        "metrics": {"net_return_pct": 101.0, "trade_count": 698},
        "selection": {"fingerprint_sha256": fingerprint},
    }


class DatasetProvenanceTest(unittest.TestCase):
    def test_records_the_reacquired_symbols_and_manifest_identity(self):
        provenance = refreeze._dataset_provenance(MANIFEST)
        self.assertEqual(provenance["reacquired_symbols"], ["CVS", "PFE", "PG", "T"])
        self.assertEqual(provenance["acquisition_run_id"], "acq_20260727_220002")
        self.assertEqual(provenance["manifest_status"], "COMPLETE_VALIDATED")
        self.assertEqual(provenance["manifest_sha256"], "f" * 64)

    def test_absent_drift_symbols_become_an_empty_list_not_none(self):
        provenance = refreeze._dataset_provenance({**MANIFEST, "drift_detected_symbols": None})
        self.assertEqual(provenance["reacquired_symbols"], [])


class ArchiveNeverClobbersTest(unittest.TestCase):
    def test_refuses_to_overwrite_an_existing_history_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "history"
            baseline = Path(tmp) / "baseline.json"
            baseline.write_text(json.dumps(BASELINE), encoding="utf-8")
            with mock.patch.object(refreeze, "HISTORY_DIR", history), \
                 mock.patch.object(refreeze, "BASELINE_PATH", baseline):
                first = refreeze._archive_prior(BASELINE)
                self.assertTrue(first.exists())
                with self.assertRaises(SystemExit) as caught:
                    refreeze._archive_prior(BASELINE)
                self.assertIn("refusing to overwrite", str(caught.exception))


class RefusalTest(unittest.TestCase):
    """The refusals that stop a regression being laundered into the baseline."""

    def _run(self, argv, fresh, manifest=MANIFEST):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            baseline.write_text(json.dumps(BASELINE), encoding="utf-8")
            with mock.patch.object(sys, "argv", ["refreeze", *argv]), \
                 mock.patch.object(refreeze, "BASELINE_PATH", baseline), \
                 mock.patch.object(refreeze, "HISTORY_DIR", Path(tmp) / "history"), \
                 mock.patch.object(refreeze, "_load_manifest", return_value=manifest), \
                 mock.patch.object(refreeze, "build", return_value=fresh):
                return refreeze.main(), json.loads(baseline.read_text(encoding="utf-8"))

    def test_changed_fingerprint_is_refused_by_default(self):
        with self.assertRaises(SystemExit) as caught:
            self._run(["--reason", "x"], _fresh(FINGERPRINT_B))
        self.assertIn("REFUSING TO RE-FREEZE", str(caught.exception))

    def test_changed_fingerprint_can_be_forced_and_is_recorded_as_forced(self):
        code, written = self._run(
            ["--reason", "investigated", "--allow-fingerprint-change"], _fresh(FINGERPRINT_B),
        )
        self.assertEqual(code, 0)
        self.assertTrue(written["provenance"]["fingerprint_changed"])
        self.assertTrue(written["provenance"]["fingerprint_change_forced"])

    def test_unvalidated_dataset_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self._run(["--reason", "x"], _fresh(), manifest={**MANIFEST, "status": "PARTIAL"})
        self.assertIn("COMPLETE_VALIDATED", str(caught.exception))

    def test_reason_is_mandatory_for_a_real_run(self):
        with self.assertRaises(SystemExit):
            self._run([], _fresh())

    def test_dry_run_writes_nothing(self):
        code, written = self._run(["--dry-run"], _fresh())
        self.assertEqual(code, 0)
        self.assertEqual(written, BASELINE, "dry-run must leave the baseline byte-identical")


class ProvenanceContentTest(unittest.TestCase):
    def _refreeze(self, prior, argv=("--reason", "vendor re-adjusted CVS")):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            baseline.write_text(json.dumps(prior), encoding="utf-8")
            with mock.patch.object(sys, "argv", ["refreeze", *argv]), \
                 mock.patch.object(refreeze, "BASELINE_PATH", baseline), \
                 mock.patch.object(refreeze, "HISTORY_DIR", Path(tmp) / "history"), \
                 mock.patch.object(refreeze, "_load_manifest", return_value=MANIFEST), \
                 mock.patch.object(refreeze, "build", return_value=_fresh()):
                refreeze.main()
            return json.loads(baseline.read_text(encoding="utf-8"))

    def test_successful_refreeze_records_prior_values_and_deltas(self):
        written = self._refreeze(BASELINE)
        provenance = written["provenance"]
        self.assertEqual(provenance["refreeze_reason"], "vendor re-adjusted CVS")
        self.assertEqual(provenance["previous_metrics"], BASELINE["metrics"])
        self.assertEqual(provenance["previous_fingerprint_sha256"], FINGERPRINT_A)
        self.assertEqual(provenance["dataset"]["reacquired_symbols"], ["CVS", "PFE", "PG", "T"])
        self.assertFalse(provenance["fingerprint_changed"])

        deltas = {d["metric"]: d for d in provenance["metric_deltas"]}
        self.assertEqual(deltas["trade_count"]["previous"], 700)
        self.assertEqual(deltas["trade_count"]["current"], 698)

        self.assertEqual(len(written["refreeze_history"]), 1)
        self.assertEqual(written["refreeze_history"][0]["reacquired_symbols"], ["CVS", "PFE", "PG", "T"])

    def test_refreeze_history_accumulates_across_refreezes(self):
        prior_with_history = {**BASELINE, "refreeze_history": [{"reason": "earlier"}]}
        written = self._refreeze(prior_with_history, argv=("--reason", "later"))
        self.assertEqual(len(written["refreeze_history"]), 2)
        self.assertEqual(written["refreeze_history"][0]["reason"], "earlier")
        self.assertEqual(written["refreeze_history"][1]["reason"], "later")


if __name__ == "__main__":
    unittest.main()
