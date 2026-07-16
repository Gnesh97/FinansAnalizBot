"""Read-only adapter: runs the existing rs_v2 coverage-forensics tooling
(testdata/v2_coverage_forensics.py::audit_v2_run, testdata/v2_session_coverage.py)
against testdata/hisseler/ without modifying that tooling or any hisseler file.

Neither hisseler/ nor rs_v2_clean_market_data/ is written to. A synthetic
in-memory manifest (v2-schema shaped) is built from hisseler's own
manifest/download_manifest.csv and written only under a new adapter workdir,
using absolute paths for every "output_relative_path" entry so the audit
tooling reads the real hisseler CSVs directly (Path(root) / <absolute path>
resolves to the absolute path regardless of root, per pathlib semantics).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HISSELER_ROOT = REPO_ROOT / "testdata" / "hisseler"
MANIFEST_CSV = HISSELER_ROOT / "manifest" / "download_manifest.csv"

OUT_DIR = REPO_ROOT / "reports" / "audit" / "hisseler_coverage_forensics_20260716"
ADAPTER_DATASET_ROOT = OUT_DIR / "_adapter_workdir"
RUN_ID = "hisseler_baseline_forensics"

TARGET_START = "2020-07-27"
TARGET_END = "2026-07-07"
BENCHMARK_SYMBOLS = {"QQQ", "SPY"}


def build_synthetic_manifest() -> dict:
    files = []
    skipped = []
    with MANIFEST_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            symbol = row["symbol"].strip()
            daily_path = HISSELER_ROOT / "daily" / f"{symbol}_1D.csv"
            raw5_path = HISSELER_ROOT / "raw_5min" / f"{symbol}_5min.csv"
            bars130_path = HISSELER_ROOT / "bars_130min" / f"{symbol}_130min.csv"
            role = "BENCHMARK" if symbol in BENCHMARK_SYMBOLS else "CANDIDATE"

            if not daily_path.exists() or not bars130_path.exists():
                skipped.append(symbol)
                continue

            files.append({
                "symbol": symbol,
                "timeframe": "1D",
                "output_relative_path": str(daily_path),
                "asset_role": role,
                "coverage_exception_reason": "",
                "request_status": "SUCCESS" if row.get("daily_status") not in ("", None, "error") else "FAILED",
            })
            files.append({
                "symbol": symbol,
                "timeframe": "130min",
                "output_relative_path": str(bars130_path),
                "source_raw_5min_relative_path": str(raw5_path) if raw5_path.exists() else "",
                "asset_role": role,
                "coverage_exception_reason": "",
                "request_status": "SUCCESS" if row.get("bar_130min_status") not in ("", None, "error") else "FAILED",
            })

    manifest = {
        "acquisition_run_id": RUN_ID,
        "dataset_profile": "hisseler_baseline",
        "target_start_date": TARGET_START,
        "target_end_date": TARGET_END,
        "status": "ADAPTER_SYNTHETIC_FOR_FORENSICS_ONLY",
        "files": files,
    }
    return manifest, skipped


def main() -> None:
    manifest, skipped = build_synthetic_manifest()

    manifest_path = ADAPTER_DATASET_ROOT / "manifests" / RUN_ID / "acquisition_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    try:
        from testdata.v2_coverage_forensics import audit_v2_run
    except ImportError:
        import sys
        sys.path.insert(0, str(REPO_ROOT / "testdata"))
        from v2_coverage_forensics import audit_v2_run

    result = audit_v2_run(
        dataset_root=ADAPTER_DATASET_ROOT,
        run_id=RUN_ID,
        report_dir=OUT_DIR,
    )

    # The unmodified tool hardcodes "rs_v2_clean_*" output filenames regardless
    # of dataset_root. Copy (not move, not rewrite) to hisseler-named siblings
    # in the same new directory for clarity; originals are left untouched.
    rename_map = {
        "rs_v2_clean_coverage_gap_detail.csv": "hisseler_coverage_gap_detail.csv",
        "rs_v2_clean_coverage_gap_frequency.csv": "hisseler_coverage_gap_frequency.csv",
        "rs_v2_clean_intraday_block_audit.csv": "hisseler_intraday_block_audit.csv",
        "rs_v2_clean_decision_path_impact.csv": "hisseler_decision_path_impact.csv",
        "rs_v2_clean_boundary_exception_review.csv": "hisseler_boundary_exception_review.csv",
        "rs_v2_clean_coverage_forensics.md": "hisseler_coverage_forensics.md",
    }
    for original_name, new_name in rename_map.items():
        original = OUT_DIR / original_name
        if original.exists():
            (OUT_DIR / new_name).write_bytes(original.read_bytes())

    summary = result["summary"]
    print("symbols_included:", len({f["symbol"] for f in manifest["files"]}))
    print("symbols_skipped_missing_files:", skipped)
    print("daily_valid:", summary["daily_valid"], "daily_failed:", summary["daily_failed"])
    print("intraday_valid:", summary["intraday_valid"], "intraday_failed:", summary["intraday_failed"])
    print("affected_rebalance_decisions:", summary["affected_rebalance_decisions"])
    print("qqq_impact:", summary["qqq_impact"])
    print("spy_impact:", summary["spy_impact"])
    print("report_dir:", OUT_DIR)


if __name__ == "__main__":
    main()
