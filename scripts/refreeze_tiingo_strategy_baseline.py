"""Re-freeze the rs_v2_tiingo strategy baseline, with provenance (Phase 9A).

WHY THIS EXISTS
---------------
Tiingo retroactively re-adjusts prices when a corporate action is processed. The
nightly refresh detects the adjustment drift and re-acquires that symbol's ENTIRE
history, so the frozen baseline's headline metrics stop reproducing -- through no
change to strategy logic. On 2026-07-27 this happened for CVS, PFE, PG and T.

This will recur. The operator therefore needs a re-freeze that is routine,
scripted, and self-documenting, rather than a hand edit of a JSON file that
leaves no trace of WHY the numbers changed.

WHAT THIS GUARANTEES
--------------------
1. The prior artifact is NEVER overwritten. It is copied to
   testdata/baselines/history/ under its own generated_at timestamp first, and
   the new artifact names that file.
2. Every re-freeze records provenance: the reason, which symbols the vendor
   re-acquired and when, the dataset manifest it was computed against, the prior
   metric values, and the per-metric deltas.
3. A re-freeze that would change the SELECTION FINGERPRINT is REFUSED unless
   explicitly forced. A fingerprint change means ranking/selection moved, which
   is a real regression, not a price re-adjustment -- adopting it silently is
   exactly what this guard exists to prevent.
4. It refuses to run against a dataset whose manifest is not COMPLETE_VALIDATED.

USAGE
-----
    # See what would change; writes nothing.
    python3 scripts/refreeze_tiingo_strategy_baseline.py --dry-run

    # Re-freeze, recording why.
    python3 scripts/refreeze_tiingo_strategy_baseline.py \
        --reason "Tiingo re-adjusted CVS, PFE, PG, T on the 2026-07-27 refresh"

The window itself (2020-07-27 -> 2026-07-21) is NOT moved by this script. It
re-computes the frozen window against the CURRENT validated dataset. Changing
the window is a different, deliberate act.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from build_tiingo_strategy_baseline import (  # noqa: E402
    BASELINE_PATH,
    DATA_FOLDER,
    build,
)
from baseline_metric_drift import compare_metrics  # noqa: E402

HISTORY_DIR = PROJECT_ROOT / "testdata" / "baselines" / "history"
MANIFEST_POINTER = DATA_FOLDER / "manifests" / "latest_manifest.json"


def _display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise.

    `relative_to` raises for any path outside the repo. That would abort the
    re-freeze AFTER the prior artifact had already been copied, leaving an
    archived file and no new baseline. Provenance formatting must never be the
    thing that fails a write.
    """
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _load_manifest() -> dict:
    """The immutable manifest the latest pointer resolves to."""
    pointer = json.loads(MANIFEST_POINTER.read_text(encoding="utf-8"))
    manifest_path = DATA_FOLDER / pointer["immutable_manifest_relative_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["_pointer"] = pointer
    return manifest


def _dataset_provenance(manifest: dict) -> dict:
    return {
        "acquisition_run_id": manifest.get("acquisition_run_id"),
        "manifest_status": manifest.get("status"),
        "manifest_sha256": manifest["_pointer"].get("immutable_manifest_sha256"),
        "target_end_date": manifest.get("target_end_date"),
        "validation_completed_at": manifest.get("validation_completed_at"),
        "reacquired_symbols": manifest.get("drift_detected_symbols") or [],
        "reacquisition_item_count": manifest.get("reacquisition_item_count"),
        "candidate_universe_hash": manifest.get("candidate_universe_hash"),
    }


def _archive_prior(prior: dict) -> Path:
    """Copy the prior artifact into history/ keyed by its own generated_at."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = str(prior.get("generated_at", "unknown")).replace(":", "").replace("-", "")
    target = HISTORY_DIR / f"rs_v2_tiingo_strategy_baseline_{stamp}.json"
    if target.exists():
        raise SystemExit(
            f"refusing to overwrite an existing history artifact: {target}\n"
            f"the prior baseline's generated_at has already been archived."
        )
    shutil.copy2(BASELINE_PATH, target)
    return target


def _print_delta_table(deltas: list[dict]) -> None:
    print(f"{'metric':<20} {'prior':>14} {'new':>14} {'delta':>12} {'rel':>9}")
    for record in deltas:
        absolute = record["absolute_delta"]
        relative = record["relative_delta"]
        absolute_text = "n/a" if absolute is None else f"{absolute:+.4f}"
        relative_text = "n/a" if relative is None else f"{relative * 100:.2f}%"
        print(
            f"{record['metric']:<20} {str(record['expected']):>14} {str(record['observed']):>14} "
            f"{absolute_text:>12} {relative_text:>9}"
        )


def _build_provenance(
    prior: dict, deltas: list[dict], dataset: dict, archived: Path,
    reason: str, fingerprint_changed: bool, forced: bool,
) -> dict:
    return {
        "refreeze_reason": reason,
        "refrozen_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "previous_artifact": _display_path(archived),
        "previous_generated_at": prior.get("generated_at"),
        "previous_metrics": prior["metrics"],
        "previous_fingerprint_sha256": prior["selection"]["fingerprint_sha256"],
        "fingerprint_changed": fingerprint_changed,
        "fingerprint_change_forced": bool(fingerprint_changed and forced),
        "metric_deltas": [
            {
                "metric": record["metric"],
                "previous": record["expected"],
                "current": record["observed"],
                "absolute_delta": record["absolute_delta"],
                "relative_delta": record["relative_delta"],
            }
            for record in deltas
        ],
        "dataset": dataset,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-freeze the rs_v2_tiingo strategy baseline with recorded provenance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--reason", help="Why this re-freeze is being performed. Required unless --dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change; write nothing.")
    parser.add_argument(
        "--allow-fingerprint-change", action="store_true",
        help="Adopt a CHANGED selection fingerprint. Refused by default: a changed "
             "fingerprint means ranking/selection moved, which is a regression, not "
             "a vendor price re-adjustment.",
    )
    args = parser.parse_args()
    if not args.dry_run and not args.reason:
        parser.error("--reason is required (or use --dry-run)")
    return args


def main() -> int:
    args = _parse_args()

    if not BASELINE_PATH.exists():
        raise SystemExit(f"no existing baseline at {BASELINE_PATH}; use build_tiingo_strategy_baseline.py to create one")

    prior = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    manifest = _load_manifest()
    dataset = _dataset_provenance(manifest)

    if dataset["manifest_status"] != "COMPLETE_VALIDATED":
        raise SystemExit(
            f"refusing to re-freeze against a dataset that is not COMPLETE_VALIDATED "
            f"(status={dataset['manifest_status']!r}). Freezing a governing baseline "
            f"from an unverified dataset is the failure this guard exists to prevent."
        )

    print("=== RECOMPUTING THE FROZEN WINDOW ON THE CURRENT VALIDATED DATASET ===")
    print(f"  dataset run   : {dataset['acquisition_run_id']} ({dataset['manifest_status']})")
    print(f"  target_end    : {dataset['target_end_date']}")
    print(f"  re-acquired   : {dataset['reacquired_symbols'] or 'none'}")
    print()

    fresh = build()

    prior_fingerprint = prior["selection"]["fingerprint_sha256"]
    fresh_fingerprint = fresh["selection"]["fingerprint_sha256"]
    fingerprint_changed = prior_fingerprint != fresh_fingerprint

    deltas = compare_metrics(prior["metrics"], fresh["metrics"])
    _print_delta_table(deltas)
    print()
    print(f"  fingerprint prior : {prior_fingerprint}")
    print(f"  fingerprint new   : {fresh_fingerprint}")
    print(f"  FINGERPRINT {'CHANGED' if fingerprint_changed else 'UNCHANGED'}")

    if fingerprint_changed and not args.allow_fingerprint_change:
        raise SystemExit(
            "\nREFUSING TO RE-FREEZE: the selection fingerprint changed.\n"
            "  A vendor price re-adjustment moves headline metrics but NOT the\n"
            "  ranking. A changed fingerprint means selection logic or inputs\n"
            "  changed materially -- investigate before adopting it.\n"
            "  Override with --allow-fingerprint-change once understood."
        )

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    _write_refreeze(fresh, prior, deltas, dataset, args, fingerprint_changed)
    return 0


def _write_refreeze(
    fresh: dict, prior: dict, deltas: list[dict], dataset: dict,
    args: argparse.Namespace, fingerprint_changed: bool,
) -> None:
    """Archive the prior artifact, attach provenance, and write the new baseline."""
    archived = _archive_prior(prior)

    fresh["provenance"] = _build_provenance(
        prior, deltas, dataset, archived, args.reason,
        fingerprint_changed, args.allow_fingerprint_change,
    )
    # Carry the whole re-freeze history forward so a reader can reconstruct every
    # change from the current artifact alone, not just the most recent one.
    prior_history = prior.get("refreeze_history", [])
    fresh["refreeze_history"] = prior_history + [{
        "refrozen_at": fresh["provenance"]["refrozen_at"],
        "reason": args.reason,
        "artifact": _display_path(archived),
        "acquisition_run_id": dataset["acquisition_run_id"],
        "reacquired_symbols": dataset["reacquired_symbols"],
    }]

    BASELINE_PATH.write_text(json.dumps(fresh, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\narchived prior -> {_display_path(archived)}")
    print(f"wrote          -> {_display_path(BASELINE_PATH)}")
    print(f"reason         : {args.reason}")


if __name__ == "__main__":
    raise SystemExit(main())
