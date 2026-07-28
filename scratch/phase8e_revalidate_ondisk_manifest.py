"""Phase 8E -- re-validate the 2026-07-27 refresh already on disk. No new fetch.

The run's data was proven sound; only the telemetry CONTRACT rejected it, because
the manifest had no way to declare the two legitimate reasons its provider
attempts exceeded the logical item count. Those reasons are already recorded in
the manifest, per file:

  no_provider_call_item_count <- files with provider_result_status == "SKIPPED"
  reacquisition_item_count    <- len(drift_detected_symbols)

This declares them from that existing evidence, refuses to proceed unless the
arithmetic reconciles EXACTLY, and then runs the production finalize path
(finalize_v2_with_report), which re-runs the full validator chain and repoints
the dataset only if everything passes. Nothing is relaxed or bypassed.

    python3 scratch/phase8e_revalidate_ondisk_manifest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "testdata"))

import vericekici_updated as module  # noqa: E402
import tiingo_provider as tp  # noqa: E402

ROOT = PROJECT_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data"
RUN_ID = "acq_20260727_220002"
MANIFESTS = ROOT / "manifests"
RUN_MANIFEST = MANIFESTS / RUN_ID / "acquisition_manifest.json"


def main() -> None:
    manifest = json.loads(RUN_MANIFEST.read_text(encoding="utf-8"))
    files = manifest["files"]

    skipped = [f["symbol"] for f in files if f.get("provider_result_status") == "SKIPPED"]
    drift = list(manifest.get("drift_detected_symbols") or [])
    base = manifest["logical_acquisition_item_count"]
    provider = manifest["provider_attempt_count"]
    retries = manifest["retry_attempt_count"]

    predicted = (base - len(skipped)) + retries + len(drift)
    print("=== reconciliation from the manifest's own per-file evidence ===")
    print(f"  logical items           : {base}")
    print(f"  no provider call (SKIP) : {len(skipped)} {skipped}")
    print(f"  re-acquisitions (drift) : {len(drift)} {drift}")
    print(f"  retries                 : {retries}")
    print(f"  predicted provider calls: {predicted}")
    print(f"  recorded provider calls : {provider}")
    if predicted != provider:
        raise SystemExit(
            "ABORT: telemetry does not reconcile from recorded evidence. "
            "That would be a genuine telemetry defect, not a schema gap -- do not finalize."
        )
    print("  RECONCILES -- declaring the two counts and re-running the production validator\n")

    repaired = dict(manifest)
    repaired["no_provider_call_item_count"] = len(skipped)
    repaired["reacquisition_item_count"] = len(drift)
    repaired["status"] = "VALIDATION_PENDING"
    for key in ("validation_error_type", "validation_error_message", "validation_failed_at"):
        repaired.pop(key, None)

    report_summary = {
        "acquisition_run_id": RUN_ID,
        "dataset_profile": tp.V2_TIINGO_PROFILE,
        "refresh_mode": "REFRESH_DAILY",
        "symbol_count": len({f["symbol"] for f in files}),
        "requested_start": manifest["target_start_date"],
        "requested_end": manifest["target_end_date"],
    }
    module.finalize_v2_with_report(
        repaired,
        RUN_MANIFEST,
        MANIFESTS / "latest_manifest.json",
        MANIFESTS / RUN_ID,
        PROJECT_ROOT / "testdata" / "reports" / "latest",
        report_summary,
        files,
        profile=tp.V2_TIINGO_PROFILE,
    )

    final = json.loads(RUN_MANIFEST.read_text(encoding="utf-8"))
    pointer = json.loads((MANIFESTS / "latest_manifest.json").read_text(encoding="utf-8"))
    print("=== RESULT ===")
    print(f"  manifest status : {final.get('status')}")
    print(f"  target_end_date : {final.get('target_end_date')}")
    print(f"  latest pointer  : {pointer.get('acquisition_run_id')}")


if __name__ == "__main__":
    main()
