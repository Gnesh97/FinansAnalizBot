"""Tiingo daily EOD provider for the rs_v2_tiingo dataset profile.

Production provider integration (Phase 6A). Parallel to the rs_v2_clean /
rs_v2_sip profiles in vericekici_updated.py -- NOT bolted onto the Alpaca
path. Daily-only contract: 179 logical items (177 frozen candidates + QQQ +
SPY), no intraday timeframes.

Split/dividend safety: only Tiingo ADJUSTED fields (adjOpen/adjHigh/adjLow/
adjClose/adjVolume) are mapped to the standard OHLCV schema. Raw unadjusted
fields are never used.

Free-tier operational constraints are first-class here: paced fetching
(default ~48 requests/hour), per-symbol resumable state on disk, and bounded
429 backoff without retry storms.

TIINGO_API_KEY is read from .env only. It is never printed, logged, or
stored in any manifest or state file.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

V2_TIINGO_DATASET_SCHEMA_VERSION = "2.2.0"
V2_TIINGO_PROFILE = "rs_v2_tiingo"
V2_TIINGO_FEED = "tiingo"
V2_TIINGO_ADJUSTMENT = "adjusted"
V2_TIINGO_TARGET_START = "2020-07-27"
V2_TIINGO_TARGET_END = "2026-07-21"
V2_TIINGO_ITEM_COUNT = 179
V2_TIINGO_COVERAGE_EXCEPTIONS_PATH = BASE_DIR / "baselines" / "rs_v2_tiingo_coverage_exceptions.json"
V2_TIINGO_BASELINE_PATH = BASE_DIR / "baselines" / "rs_v2_tiingo_baseline.json"
DEFAULT_V2_TIINGO_FROZEN_ROOT = BASE_DIR / "research_data" / "rs_v2_tiingo_market_data"

TIINGO_ADJUSTED_FIELD_MAP = {
    "Open": "adjOpen",
    "High": "adjHigh",
    "Low": "adjLow",
    "Close": "adjClose",
    "Volume": "adjVolume",
}
DEFAULT_PACE_DELAY_SECONDS = 75.0  # ~48 requests/hour, under the free-tier ~50/hour ceiling
RATE_LIMIT_BACKOFF_BASE_SECONDS = 300
DEFAULT_MAX_RATE_LIMIT_WAITS = 5
IMPORT_ACTION = "IMPORTED_LOCAL_TIINGO_FETCH"
DOWNLOAD_ACTION = "DOWNLOADED_IN_CURRENT_RUN"


def _vericekici():
    try:
        from . import vericekici_updated as module
    except ImportError:
        import vericekici_updated as module
    return module


def _coverage_module():
    try:
        from . import v2_session_coverage as module
    except ImportError:
        import v2_session_coverage as module
    return module


def get_tiingo_api_key() -> str:
    module = _vericekici()
    module.load_env_file(PROJECT_DIR / ".env")
    api_key = os.getenv("TIINGO_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TIINGO_API_KEY .env dosyasinda tanimli olmali.")
    return api_key


def _session_midnight_utc(session_date: str) -> str:
    """Render a session date as New-York-midnight expressed in UTC.

    Matches the repository daily-bar convention (04:00Z in EDT, 05:00Z in
    EST); the coverage validator normalizes timestamps in market timezone,
    so a fixed 04:00Z offset would shift winter sessions back one day.
    """
    return str(pd.Timestamp(session_date, tz="America/New_York").tz_convert("UTC"))


def fetch_tiingo_daily_bars(symbol: str, api_key: str, start: str, end: str) -> pd.DataFrame:
    """Fetch one symbol's daily EOD series, mapping ADJUSTED fields to OHLCV."""
    url = (
        f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
        f"?startDate={start}&endDate={end}&format=json"
    )
    request = urllib.request.Request(url, headers={
        "Content-Type": "application/json",
        "Authorization": f"Token {api_key}",
    })
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    records = []
    for item in payload:
        if any(item.get(source) is None for source in TIINGO_ADJUSTED_FIELD_MAP.values()):
            raise ValueError(f"TIINGO_ADJUSTED_FIELDS_MISSING: {symbol} {item.get('date')}")
        row = {"Date": _session_midnight_utc(item["date"].split("T")[0])}
        for target, source in TIINGO_ADJUSTED_FIELD_MAP.items():
            row[target] = float(item[source])
        records.append(row)
    frame = pd.DataFrame(records, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    if not frame.empty:
        frame = frame.sort_values("Date").reset_index(drop=True)
    return frame


def _trim_frame_to_window(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    dates = pd.to_datetime(frame["Date"], utc=True).dt.tz_localize(None).dt.normalize()
    mask = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
    return frame.loc[mask].reset_index(drop=True)


def _load_acquisition_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("status") == "IN_PROGRESS" and state.get("acquisition_run_id"):
                return state
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "acquisition_run_id": f"acq_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "status": "IN_PROGRESS",
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "symbols": {},
    }


def _save_acquisition_state(state: dict, state_path: Path) -> None:
    _vericekici()._atomic_write_json(state, state_path)


def _import_symbol_from_local(source_csv: Path, destination: Path, start: str, end: str) -> dict:
    """Import an already-fetched local Tiingo CSV into the production root.

    Trims rows outside [start, end] so the dataset window is uniform across
    all 179 series. Source file is read-only; never modified.
    """
    if not source_csv.exists():
        raise FileNotFoundError(f"TIINGO_IMPORT_SOURCE_MISSING: {source_csv}")
    source_bytes = source_csv.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    frame = pd.read_csv(source_csv)
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if list(frame.columns) != required:
        raise ValueError(f"TIINGO_IMPORT_SOURCE_SCHEMA_INVALID: {source_csv}")
    trimmed = _trim_frame_to_window(frame, start, end)
    if trimmed.empty:
        raise ValueError(f"TIINGO_IMPORT_SOURCE_EMPTY_AFTER_TRIM: {source_csv}")
    # Eval-adapter files carry a fixed 04:00Z stamp; renormalize each session
    # to the repository NY-midnight-in-UTC convention (see _session_midnight_utc).
    session_dates = pd.to_datetime(trimmed["Date"], utc=True).dt.strftime("%Y-%m-%d")
    trimmed = trimmed.assign(Date=[_session_midnight_utc(value) for value in session_dates])
    _vericekici().atomic_write_csv(trimmed, destination)
    return {
        "acquisition_action": IMPORT_ACTION,
        "provider_result_status": "IMPORTED",
        "downloaded_in_current_run": False,
        "import_source_relative_path": source_csv.resolve().relative_to(PROJECT_DIR).as_posix(),
        "import_source_sha256": source_sha256,
        "import_source_row_count": int(len(frame)),
        "import_trimmed_row_count": int(len(trimmed)),
    }


def _fetch_symbol_paced(
    symbol: str,
    api_key: str,
    destination: Path,
    start: str,
    end: str,
    pace_delay_seconds: float,
    max_rate_limit_waits: int,
) -> tuple[dict, int, int]:
    """Fetch one symbol with pacing and bounded 429 backoff.

    Returns (record_fragment, provider_attempt_count, rate_limit_wait_count).
    Raises after max_rate_limit_waits consecutive 429s -- no retry storms.
    """
    attempts = 0
    rate_limit_waits = 0
    while True:
        attempts += 1
        try:
            frame = fetch_tiingo_daily_bars(symbol, api_key, start, end)
            if frame.empty:
                raise ValueError(f"TIINGO_EMPTY_RESPONSE: {symbol}")
            _vericekici().atomic_write_csv(frame, destination)
            time.sleep(pace_delay_seconds)
            return (
                {
                    "acquisition_action": DOWNLOAD_ACTION,
                    "provider_result_status": "DOWNLOADED",
                    "downloaded_in_current_run": True,
                },
                attempts,
                rate_limit_waits,
            )
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            rate_limit_waits += 1
            if rate_limit_waits > max_rate_limit_waits:
                raise RuntimeError(f"TIINGO_RATE_LIMIT_EXHAUSTED: {symbol} after {max_rate_limit_waits} waits") from error
            wait_seconds = RATE_LIMIT_BACKOFF_BASE_SECONDS * rate_limit_waits
            print(f"{symbol}: HTTP 429, waiting {wait_seconds}s for hourly quota reset ({rate_limit_waits}/{max_rate_limit_waits})", flush=True)
            time.sleep(wait_seconds)


def run_v2_tiingo_acquisition(
    output_root: Path,
    *,
    live: bool = False,
    import_source_root: Path | None = None,
    pace_delay_seconds: float = DEFAULT_PACE_DELAY_SECONDS,
    max_rate_limit_waits: int = DEFAULT_MAX_RATE_LIMIT_WAITS,
) -> dict:
    """Acquire (or import) the full 179-item rs_v2_tiingo daily dataset.

    Modes:
    - import_source_root set: no network calls; import + trim already-fetched
      local Tiingo CSVs (flat daily/ layout) into the production layout.
    - live without import_source_root: paced Tiingo fetch with resumable
      per-symbol state at manifests/tiingo_acquisition_state.json.
    - neither: dry-run plan only.
    """
    module = _vericekici()
    coverage_module = _coverage_module()
    output_root = Path(output_root)
    candidates, baseline = module.load_v2_frozen_universe()
    plan = module.build_v2_acquisition_plan(
        output_root, candidates, V2_TIINGO_TARGET_START, V2_TIINGO_TARGET_END,
        V2_TIINGO_FEED, V2_TIINGO_ADJUSTMENT, include_intraday=False,
    )
    if len(plan) != V2_TIINGO_ITEM_COUNT:
        raise ValueError("V2_TIINGO_PLAN_ITEM_COUNT_MISMATCH")

    if not live and import_source_root is None:
        report_dir = BASE_DIR / "reports" / "latest"
        module.write_v2_dry_run_plan(report_dir, plan, profile=V2_TIINGO_PROFILE)
        return {"status": "DRY_RUN_PLAN_ONLY", "logical_acquisition_item_count": len(plan)}

    manifests_dir = output_root / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    state_path = manifests_dir / "tiingo_acquisition_state.json"
    state = _load_acquisition_state(state_path)
    run_id = state["acquisition_run_id"]

    exceptions = coverage_module.load_reviewed_coverage_exceptions(V2_TIINGO_COVERAGE_EXCEPTIONS_PATH)
    market_schedule = coverage_module.approved_nyse_schedule(V2_TIINGO_TARGET_START, V2_TIINGO_TARGET_END)
    api_key = None if import_source_root is not None else get_tiingo_api_key()

    records = []
    provider_attempt_count = 0
    retry_attempt_count = 0
    for row in plan:
        symbol = row["symbol"]
        destination = output_root / row["output_relative_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        state_entry = state["symbols"].get(symbol)

        if import_source_root is not None:
            provenance = _import_symbol_from_local(
                Path(import_source_root) / "daily" / f"{symbol}_1D.csv",
                destination, V2_TIINGO_TARGET_START, V2_TIINGO_TARGET_END,
            )
        elif (
            state_entry is not None
            and state_entry.get("provenance", {}).get("acquisition_action") == DOWNLOAD_ACTION
            and destination.exists()
            and module.verify_file_integrity(destination)["sha256"] == state_entry.get("sha256")
        ):
            # Resume: file already fetched by an earlier invocation of this same
            # logical acquisition run (state carries the run_id); do not refetch.
            provenance = state_entry["provenance"]
            print(f"{symbol}: resume, already fetched in run {run_id}", flush=True)
        else:
            provenance, attempts, _ = _fetch_symbol_paced(
                symbol, api_key, destination, V2_TIINGO_TARGET_START, V2_TIINGO_TARGET_END,
                pace_delay_seconds, max_rate_limit_waits,
            )
            provider_attempt_count += attempts
            retry_attempt_count += attempts - 1

        integrity = module.verify_file_integrity(destination)
        if integrity["data_status"] != "VALID":
            raise ValueError(f"V2_TIINGO_FILE_INVALID: {symbol}: {integrity['error_type']}")
        coverage = coverage_module.calculate_v2_session_coverage(
            pd.read_csv(destination), timeframe="1D", symbol=symbol,
            target_start=V2_TIINGO_TARGET_START, target_end=V2_TIINGO_TARGET_END,
            market_schedule=market_schedule, reviewed_exceptions=exceptions,
        )
        record = {
            **row, **integrity, **coverage, **provenance,
            "request_status": "SUCCESS",
            "request_end_semantics": "INCLUSIVE",
            "acquisition_run_id": run_id,
        }
        records.append(record)
        state["symbols"][symbol] = {"sha256": integrity["sha256"], "provenance": provenance}
        _save_acquisition_state(state, state_path)

    if import_source_root is not None:
        telemetry = {"retry_telemetry_status": "UNAVAILABLE", "provider_attempt_count": None, "retry_attempt_count": None, "failed_provider_attempt_count": None}
    else:
        telemetry = {"retry_telemetry_status": "MEASURED", "provider_attempt_count": provider_attempt_count, "retry_attempt_count": retry_attempt_count, "failed_provider_attempt_count": retry_attempt_count}

    manifest = {
        "dataset_schema_version": V2_TIINGO_DATASET_SCHEMA_VERSION,
        "dataset_profile": V2_TIINGO_PROFILE,
        "strategy_identifier": module.V2_STRATEGY_IDENTIFIER,
        "acquisition_run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "target_start_date": V2_TIINGO_TARGET_START,
        "target_end_date": V2_TIINGO_TARGET_END,
        "feed": V2_TIINGO_FEED,
        "adjustment": V2_TIINGO_ADJUSTMENT,
        "candidate_count": len(candidates),
        "candidate_universe_hash": baseline["universe"]["candidate_universe_hash"],
        "benchmark_symbols": ["QQQ", "SPY"],
        "logical_acquisition_item_count": len(plan),
        "successful_item_count": len(records),
        "failed_item_count": len(plan) - len(records),
        "import_source_root": str(Path(import_source_root).resolve().relative_to(PROJECT_DIR)) if import_source_root is not None else None,
        "downloader_call_path": "testdata/tiingo_provider.py",
        "downloader_source_commit": module._source_commit(),
        "status": "VALIDATION_PENDING",
        **telemetry,
        "files": records,
    }

    run_manifest_path = manifests_dir / run_id / "acquisition_manifest.json"
    latest_manifest_path = manifests_dir / "latest_manifest.json"
    summary = {
        "acquisition_run_id": run_id,
        "dataset_profile": V2_TIINGO_PROFILE,
        "refresh_mode": "IMPORT_LOCAL" if import_source_root is not None else "FULL_FETCH",
        "symbol_count": len({row["symbol"] for row in plan}),
        "requested_start": V2_TIINGO_TARGET_START,
        "requested_end": V2_TIINGO_TARGET_END,
    }
    module.finalize_v2_with_report(
        manifest, run_manifest_path, latest_manifest_path,
        manifests_dir / run_id, BASE_DIR / "reports" / "latest",
        summary, records, profile=V2_TIINGO_PROFILE,
    )
    state["status"] = "FINALIZED"
    _save_acquisition_state(state, state_path)
    return {"status": "COMPLETE_VALIDATED", "acquisition_run_id": run_id, "logical_acquisition_item_count": len(records)}


def validate_v2_tiingo_manifest_and_files(dataset_root: Path, manifest: dict, frozen_baseline: dict | None = None) -> None:
    """Fail-closed rs_v2_tiingo publication contract validator. Raises on any mismatch.

    Daily-only: exactly 179 logical items, no intraday timeframes, adjusted
    Tiingo fields, frozen 177 universe + hash, SHA-256 per file, approved NYSE
    calendar coverage with registry-driven exceptions only.
    """
    module = _vericekici()
    coverage_module = _coverage_module()
    root = Path(dataset_root)
    candidates, baseline = module.load_v2_frozen_universe()
    if frozen_baseline is not None and frozen_baseline.get("strategy_identifier") != module.V2_STRATEGY_IDENTIFIER:
        raise ValueError(module.V2_UNIVERSE_CONTRACT_MISMATCH)
    exact = {
        "dataset_schema_version": V2_TIINGO_DATASET_SCHEMA_VERSION,
        "dataset_profile": V2_TIINGO_PROFILE,
        "strategy_identifier": module.V2_STRATEGY_IDENTIFIER,
        "target_start_date": V2_TIINGO_TARGET_START,
        "target_end_date": V2_TIINGO_TARGET_END,
        "feed": V2_TIINGO_FEED,
        "adjustment": V2_TIINGO_ADJUSTMENT,
        "candidate_count": len(candidates),
        "candidate_universe_hash": baseline["universe"]["candidate_universe_hash"],
        "logical_acquisition_item_count": V2_TIINGO_ITEM_COUNT,
        "successful_item_count": V2_TIINGO_ITEM_COUNT,
        "failed_item_count": 0,
    }
    if any(manifest.get(key) != value for key, value in exact.items()) or not manifest.get("acquisition_run_id") or manifest.get("status") not in {"VALIDATION_PENDING", "COMPLETE_VALIDATED"}:
        raise ValueError("V2_DATASET_NOT_VALIDATED: frozen contract mismatch")
    module.validate_v2_retry_telemetry(manifest)
    expected = {
        (row["symbol"], row["timeframe"]): row
        for row in module.build_v2_acquisition_plan(root, candidates, V2_TIINGO_TARGET_START, V2_TIINGO_TARGET_END, V2_TIINGO_FEED, V2_TIINGO_ADJUSTMENT, include_intraday=False)
    }
    actual = {}
    for record in manifest.get("files", []):
        key = (record.get("symbol"), record.get("timeframe"))
        if key in actual:
            raise ValueError("V2_DATASET_NOT_VALIDATED: duplicate symbol/timeframe")
        actual[key] = record
    if set(actual) != set(expected):
        raise ValueError("V2_DATASET_NOT_VALIDATED: exact logical record set mismatch")
    exceptions = coverage_module.load_reviewed_coverage_exceptions(V2_TIINGO_COVERAGE_EXCEPTIONS_PATH)
    for key, expected_record in expected.items():
        record = actual[key]
        if record.get("timeframe") != "1D":
            raise ValueError("V2_DATASET_NOT_VALIDATED: non-daily timeframe in daily-only profile")
        if (
            record.get("asset_role") != expected_record["asset_role"]
            or record.get("output_relative_path") != expected_record["output_relative_path"]
            or record.get("requested_start") != exact["target_start_date"]
            or record.get("requested_end") != exact["target_end_date"]
            or record.get("feed") != V2_TIINGO_FEED
            or record.get("adjustment") != V2_TIINGO_ADJUSTMENT
            or record.get("acquisition_run_id") != manifest["acquisition_run_id"]
        ):
            raise ValueError("V2_DATASET_NOT_VALIDATED: role/path mismatch")
        path = root / record["output_relative_path"]
        integrity = module.verify_file_integrity(path)
        if record.get("request_status") != "SUCCESS" or integrity["data_status"] != "VALID" or integrity["sha256"] != record.get("sha256"):
            raise ValueError("V2_DATASET_NOT_VALIDATED: file integrity/status mismatch")
        if not coverage_module.COVERAGE_FIELDS.issubset(record):
            raise ValueError("V2_DATASET_NOT_VALIDATED: missing coverage metadata")
        action = record.get("acquisition_action")
        if action == DOWNLOAD_ACTION:
            if record.get("downloaded_in_current_run") is not True or record.get("provider_result_status") != "DOWNLOADED":
                raise ValueError("V2_DATASET_NOT_VALIDATED: stale daily provenance")
        elif action == IMPORT_ACTION:
            source_relative = record.get("import_source_relative_path")
            source_sha256 = record.get("import_source_sha256")
            if (
                record.get("provider_result_status") != "IMPORTED"
                or not isinstance(source_relative, str) or not source_relative
                or Path(source_relative).is_absolute()
                or not isinstance(source_sha256, str) or len(source_sha256) != 64
                or not isinstance(record.get("import_source_row_count"), int)
                or record["import_source_row_count"] < record.get("row_count", 0)
            ):
                raise ValueError("V2_DATASET_NOT_VALIDATED: import lineage invalid")
        else:
            raise ValueError("V2_DATASET_NOT_VALIDATED: unknown acquisition action")
    market_schedule = coverage_module.approved_nyse_schedule(exact["target_start_date"], exact["target_end_date"])
    for key in expected:
        record = actual[key]
        path = root / record["output_relative_path"]
        coverage_module.validate_v2_file_coverage(
            path, record, record["symbol"], "1D", exact["target_start_date"], exact["target_end_date"],
            reviewed_exceptions=exceptions, market_schedule=market_schedule,
        )


def freeze_v2_tiingo_baseline() -> None:
    """Populates baselines/rs_v2_tiingo_baseline.json on successful validation."""
    module = _vericekici()
    clean_baseline = json.loads(module.V2_BASELINE_PATH.read_text(encoding="utf-8"))
    tiingo_baseline = {
        "baseline_schema_version": V2_TIINGO_DATASET_SCHEMA_VERSION,
        "dataset_profile": V2_TIINGO_PROFILE,
        "strategy_identifier": module.V2_STRATEGY_IDENTIFIER,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "FROZEN",
        "universe": clean_baseline.get("universe"),
        "benchmark": clean_baseline.get("benchmark"),
        "strategy": clean_baseline.get("strategy"),
        "execution": clean_baseline.get("execution"),
        "sizing_contract": clean_baseline.get("sizing_contract"),
    }
    module._atomic_write_json(tiingo_baseline, V2_TIINGO_BASELINE_PATH)
    print(f"Tiingo baseline frozen and written to {V2_TIINGO_BASELINE_PATH}", flush=True)
