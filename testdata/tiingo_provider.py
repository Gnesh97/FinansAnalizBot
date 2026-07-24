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
ADJUSTMENT_DRIFT_THRESHOLD = 0.005  # >0.5% relative OHLC difference on a common session
DRIFT_CHECK_TRAILING_SESSIONS = 10


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


def measure_adjustment_drift(existing: pd.DataFrame, refreshed: pd.DataFrame) -> dict:
    """Max relative OHLC difference across sessions common to both frames.

    Returns full telemetry -- the triggering percentage and before/after
    values are always retrievable afterwards (fixes the SIP-era gap where the
    drift magnitude could not be reconstructed post hoc).
    """
    result = {
        "max_drift_ratio": 0.0,
        "drift_session": "",
        "drift_column": "",
        "drift_value_existing": None,
        "drift_value_refreshed": None,
        "common_session_count": 0,
    }
    if existing.empty or refreshed.empty:
        return result
    existing_by_date = existing.set_index(pd.to_datetime(existing["Date"], utc=True).dt.strftime("%Y-%m-%d"))
    refreshed_by_date = refreshed.set_index(pd.to_datetime(refreshed["Date"], utc=True).dt.strftime("%Y-%m-%d"))
    common = sorted(set(existing_by_date.index) & set(refreshed_by_date.index))
    result["common_session_count"] = len(common)
    for session in common:
        row_existing = existing_by_date.loc[session]
        row_refreshed = refreshed_by_date.loc[session]
        for column in ("Open", "High", "Low", "Close"):
            value_existing = float(row_existing[column])
            value_refreshed = float(row_refreshed[column])
            if value_refreshed <= 0:
                continue
            ratio = abs(value_existing - value_refreshed) / value_refreshed
            if ratio > result["max_drift_ratio"]:
                result.update({
                    "max_drift_ratio": ratio,
                    "drift_session": session,
                    "drift_column": column,
                    "drift_value_existing": value_existing,
                    "drift_value_refreshed": value_refreshed,
                })
    return result


def is_adjustment_drift(drift: dict, threshold: float = ADJUSTMENT_DRIFT_THRESHOLD) -> bool:
    return drift["max_drift_ratio"] > threshold


def _merge_new_sessions(existing: pd.DataFrame, refreshed: pd.DataFrame) -> pd.DataFrame:
    """Append refreshed rows for sessions strictly after the existing last session."""
    existing_last = pd.to_datetime(existing["Date"], utc=True).max()
    refreshed_dates = pd.to_datetime(refreshed["Date"], utc=True)
    new_rows = refreshed.loc[refreshed_dates > existing_last]
    if new_rows.empty:
        return existing
    return pd.concat([existing, new_rows], ignore_index=True)


def _terminal_symbols(symbols: list[str], exceptions: list[dict], as_of_date: str) -> set[str]:
    """Registry-driven EARLY_TERMINAL skip set -- no symbol literals here."""
    coverage_module = _coverage_module()
    terminated = set()
    for symbol in symbols:
        row = coverage_module._matching_exception(exceptions, symbol, "EARLY_TERMINAL")
        if row is not None and str(row.get("approved_last_session", "")) <= as_of_date:
            terminated.add(symbol)
    return terminated


def latest_completed_session(as_of: datetime | None = None) -> str:
    """Most recent NYSE session per the approved calendar, matching the
    freshness bar dss_report._latest_expected_session uses."""
    coverage_module = _coverage_module()
    today_text = (as_of or datetime.now(timezone.utc)).date().isoformat()
    schedule = coverage_module.approved_nyse_schedule(V2_TIINGO_TARGET_START, today_text)
    return schedule.index[-1].strftime("%Y-%m-%d")


def run_v2_tiingo_daily_refresh(
    output_root: Path,
    *,
    pace_delay_seconds: float = DEFAULT_PACE_DELAY_SECONDS,
    max_rate_limit_waits: int = DEFAULT_MAX_RATE_LIMIT_WAITS,
    symbols_subset: list[str] | None = None,
    target_end: str | None = None,
) -> dict:
    """Paced, resumable daily refresh for rs_v2_tiingo.

    Per symbol: one trailing-window request (last DRIFT_CHECK_TRAILING_SESSIONS
    sessions through target_end) doubles as adjustment-drift probe AND source
    of the new bars. Drift > threshold triggers a clean full-history
    re-acquisition for that symbol (Tiingo retroactively re-adjusts history on
    corporate actions -- spinoffs/splits/dividends legitimately fire this).
    Registry-driven EARLY_TERMINAL symbols are skipped without a request.

    symbols_subset: verification mode -- refreshes only those symbols and
    NEVER finalizes a manifest (a partial refresh must not repoint the
    dataset's validated state). Full refresh (no subset) finalizes fail-closed.
    """
    module = _vericekici()
    coverage_module = _coverage_module()
    output_root = Path(output_root)
    candidates, baseline = module.load_v2_frozen_universe()
    all_symbols = [*candidates, "QQQ", "SPY"]
    refresh_end = target_end or latest_completed_session()
    if refresh_end < V2_TIINGO_TARGET_END:
        raise ValueError("V2_TIINGO_REFRESH_END_BEFORE_FROZEN_WINDOW")

    exceptions = coverage_module.load_reviewed_coverage_exceptions(V2_TIINGO_COVERAGE_EXCEPTIONS_PATH)
    terminal = _terminal_symbols(all_symbols, exceptions, refresh_end)
    selected = [s for s in all_symbols if symbols_subset is None or s in set(symbols_subset)]
    if not selected:
        raise ValueError("V2_TIINGO_REFRESH_EMPTY_SYMBOL_SELECTION")

    manifests_dir = output_root / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    state_path = manifests_dir / "tiingo_refresh_state.json"
    state = _load_acquisition_state(state_path)
    run_id = state["acquisition_run_id"]
    api_key = get_tiingo_api_key()

    provider_attempt_count = 0
    retry_attempt_count = 0
    refreshed_records = {}
    timing = []
    for symbol in selected:
        destination = output_root / module.resolve_v2_dataset_paths(output_root, symbol).daily.relative_to(output_root)
        symbol_started = time.time()
        if symbol in terminal:
            print(f"{symbol}: SKIPPED (registry EARLY_TERMINAL, no data can exist after approved_last_session)", flush=True)
            refreshed_records[symbol] = {"provider_result_status": "SKIPPED", "adjustment_drift_detected": False}
            state["symbols"][symbol] = {"status": "SKIPPED", "refresh_end": refresh_end}
            _save_acquisition_state(state, state_path)
            continue
        state_entry = state["symbols"].get(symbol)
        if (
            state_entry is not None
            and state_entry.get("refresh_end") == refresh_end
            and destination.exists()
            and module.verify_file_integrity(destination)["sha256"] == state_entry.get("sha256")
        ):
            print(f"{symbol}: resume, already refreshed in run {run_id}", flush=True)
            refreshed_records[symbol] = {
                "provider_result_status": state_entry.get("provider_result_status", "REFRESHED"),
                "adjustment_drift_detected": state_entry.get("adjustment_drift_detected", False),
                **{k: state_entry[k] for k in ("max_drift_pct", "drift_session", "drift_column") if k in state_entry},
            }
            continue
        if not destination.exists():
            raise ValueError(f"V2_TIINGO_REFRESH_MISSING_BASE_FILE: {symbol}")
        existing = pd.read_csv(destination)
        existing_sessions = pd.to_datetime(existing["Date"], utc=True).dt.strftime("%Y-%m-%d").tolist()
        drift_check_start = existing_sessions[-DRIFT_CHECK_TRAILING_SESSIONS] if len(existing_sessions) >= DRIFT_CHECK_TRAILING_SESSIONS else existing_sessions[0]

        window, attempts, _ = _fetch_window_paced(symbol, api_key, drift_check_start, refresh_end, pace_delay_seconds, max_rate_limit_waits)
        provider_attempt_count += attempts
        retry_attempt_count += attempts - 1
        drift = measure_adjustment_drift(existing, window)
        drift_detected = is_adjustment_drift(drift)
        drift_pct = round(drift["max_drift_ratio"] * 100.0, 6)
        if drift_detected:
            print(
                f"[ADJUSTMENT_DRIFT] {symbol}: max_drift={drift_pct}% on {drift['drift_session']} {drift['drift_column']} "
                f"(existing={drift['drift_value_existing']} refreshed={drift['drift_value_refreshed']}) "
                f"-- full history re-acquisition",
                flush=True,
            )
            full, attempts_full, _ = _fetch_window_paced(symbol, api_key, V2_TIINGO_TARGET_START, refresh_end, pace_delay_seconds, max_rate_limit_waits)
            provider_attempt_count += attempts_full
            retry_attempt_count += attempts_full - 1
            module.atomic_write_csv(full, destination)
            status = "DOWNLOADED"
        else:
            merged = _merge_new_sessions(existing, window)
            module.atomic_write_csv(merged, destination)
            status = "REFRESHED"
        integrity = module.verify_file_integrity(destination)
        if integrity["data_status"] != "VALID":
            raise ValueError(f"V2_TIINGO_REFRESH_FILE_INVALID: {symbol}: {integrity['error_type']}")
        record = {
            "provider_result_status": status,
            "adjustment_drift_detected": drift_detected,
            "max_drift_pct": drift_pct,
            "drift_session": drift["drift_session"],
            "drift_column": drift["drift_column"],
            "drift_value_existing": drift["drift_value_existing"],
            "drift_value_refreshed": drift["drift_value_refreshed"],
        }
        refreshed_records[symbol] = record
        state["symbols"][symbol] = {**record, "sha256": integrity["sha256"], "refresh_end": refresh_end, "status": status}
        _save_acquisition_state(state, state_path)
        timing.append({"symbol": symbol, "seconds": round(time.time() - symbol_started, 2), "status": status})

    summary = {
        "acquisition_run_id": run_id,
        "refresh_end": refresh_end,
        "symbols_processed": len(selected),
        "terminal_skipped": sorted(set(selected) & terminal),
        "drift_detected_symbols": sorted(s for s, r in refreshed_records.items() if r.get("adjustment_drift_detected")),
        "provider_attempt_count": provider_attempt_count,
        "retry_attempt_count": retry_attempt_count,
        "per_symbol_timing": timing,
    }
    if symbols_subset is not None:
        print(f"PARTIAL_VERIFICATION_NO_FINALIZE: refreshed {len(selected)} symbol(s); manifest NOT finalized (subset refresh can never repoint validated state)", flush=True)
        return {"status": "PARTIAL_VERIFICATION_NO_FINALIZE", **summary}

    market_schedule = coverage_module.approved_nyse_schedule(V2_TIINGO_TARGET_START, refresh_end)
    plan = module.build_v2_acquisition_plan(output_root, candidates, V2_TIINGO_TARGET_START, refresh_end, V2_TIINGO_FEED, V2_TIINGO_ADJUSTMENT, include_intraday=False)
    records = []
    for row in plan:
        symbol = row["symbol"]
        destination = output_root / row["output_relative_path"]
        integrity = module.verify_file_integrity(destination)
        coverage = coverage_module.calculate_v2_session_coverage(
            pd.read_csv(destination), timeframe="1D", symbol=symbol,
            target_start=V2_TIINGO_TARGET_START, target_end=refresh_end,
            market_schedule=market_schedule, reviewed_exceptions=exceptions,
        )
        refresh_info = refreshed_records.get(symbol, {})
        records.append({
            **row, **integrity, **coverage,
            "request_status": "SUCCESS",
            "request_end_semantics": "INCLUSIVE",
            "acquisition_run_id": run_id,
            "acquisition_action": DOWNLOAD_ACTION,
            "downloaded_in_current_run": refresh_info.get("provider_result_status") in {"DOWNLOADED", "REFRESHED"},
            **refresh_info,
        })
    manifest = {
        "dataset_schema_version": V2_TIINGO_DATASET_SCHEMA_VERSION,
        "dataset_profile": V2_TIINGO_PROFILE,
        "strategy_identifier": module.V2_STRATEGY_IDENTIFIER,
        "acquisition_run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "target_start_date": V2_TIINGO_TARGET_START,
        "target_end_date": refresh_end,
        "feed": V2_TIINGO_FEED,
        "adjustment": V2_TIINGO_ADJUSTMENT,
        "candidate_count": len(candidates),
        "candidate_universe_hash": baseline["universe"]["candidate_universe_hash"],
        "benchmark_symbols": ["QQQ", "SPY"],
        "logical_acquisition_item_count": len(plan),
        "successful_item_count": len(records),
        "failed_item_count": len(plan) - len(records),
        "refresh_daily": True,
        "drift_detected_symbols": summary["drift_detected_symbols"],
        "import_source_root": None,
        "downloader_call_path": "testdata/tiingo_provider.py",
        "downloader_source_commit": module._source_commit(),
        "status": "VALIDATION_PENDING",
        "retry_telemetry_status": "MEASURED",
        "provider_attempt_count": provider_attempt_count,
        "retry_attempt_count": retry_attempt_count,
        "failed_provider_attempt_count": retry_attempt_count,
        "files": records,
    }
    run_manifest_path = manifests_dir / run_id / "acquisition_manifest.json"
    latest_manifest_path = manifests_dir / "latest_manifest.json"
    report_summary = {
        "acquisition_run_id": run_id,
        "dataset_profile": V2_TIINGO_PROFILE,
        "refresh_mode": "REFRESH_DAILY",
        "symbol_count": len({row["symbol"] for row in plan}),
        "requested_start": V2_TIINGO_TARGET_START,
        "requested_end": refresh_end,
    }
    module.finalize_v2_with_report(
        manifest, run_manifest_path, latest_manifest_path,
        manifests_dir / run_id, BASE_DIR / "reports" / "latest",
        report_summary, records, profile=V2_TIINGO_PROFILE,
    )
    state["status"] = "FINALIZED"
    _save_acquisition_state(state, state_path)
    return {"status": "COMPLETE_VALIDATED", **summary}


def _fetch_window_paced(
    symbol: str,
    api_key: str,
    start: str,
    end: str,
    pace_delay_seconds: float,
    max_rate_limit_waits: int,
) -> tuple[pd.DataFrame, int, int]:
    """Paced window fetch with bounded 429 backoff; returns (frame, attempts, waits)."""
    attempts = 0
    rate_limit_waits = 0
    while True:
        attempts += 1
        try:
            frame = fetch_tiingo_daily_bars(symbol, api_key, start, end)
            if frame.empty:
                raise ValueError(f"TIINGO_EMPTY_RESPONSE: {symbol}")
            time.sleep(pace_delay_seconds)
            return frame, attempts, rate_limit_waits
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
    is_refresh = bool(manifest.get("refresh_daily"))
    if is_refresh:
        target_end_date = manifest.get("target_end_date")
        target_end_valid = isinstance(target_end_date, str) and target_end_date >= V2_TIINGO_TARGET_END
        other_keys_match = all(manifest.get(key) == value for key, value in exact.items() if key != "target_end_date")
    else:
        target_end_valid = manifest.get("target_end_date") == V2_TIINGO_TARGET_END
        other_keys_match = all(manifest.get(key) == value for key, value in exact.items() if key != "target_end_date")
    if not other_keys_match or not target_end_valid or not manifest.get("acquisition_run_id") or manifest.get("status") not in {"VALIDATION_PENDING", "COMPLETE_VALIDATED"}:
        raise ValueError("V2_DATASET_NOT_VALIDATED: frozen contract mismatch")
    module.validate_v2_retry_telemetry(manifest)
    effective_end = manifest.get("target_end_date")
    expected = {
        (row["symbol"], row["timeframe"]): row
        for row in module.build_v2_acquisition_plan(root, candidates, V2_TIINGO_TARGET_START, effective_end, V2_TIINGO_FEED, V2_TIINGO_ADJUSTMENT, include_intraday=False)
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
            or record.get("requested_end") != effective_end
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
            if is_refresh:
                if record.get("provider_result_status") not in {"DOWNLOADED", "REFRESHED", "SKIPPED"}:
                    raise ValueError("V2_DATASET_NOT_VALIDATED: stale daily provenance")
            elif record.get("downloaded_in_current_run") is not True or record.get("provider_result_status") != "DOWNLOADED":
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
    market_schedule = coverage_module.approved_nyse_schedule(exact["target_start_date"], effective_end)
    for key in expected:
        record = actual[key]
        path = root / record["output_relative_path"]
        coverage_module.validate_v2_file_coverage(
            path, record, record["symbol"], "1D", exact["target_start_date"], effective_end,
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
