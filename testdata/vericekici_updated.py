from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

try:
    from .v2_session_coverage import (
        COVERAGE_FIELDS,
        _matching_exception,
        approved_nyse_schedule,
        coverage_metadata_for_file,
        load_reviewed_coverage_exceptions,
        validate_v2_file_coverage,
        validate_v2_symbol_session_ranges,
    )
except ImportError:
    from v2_session_coverage import (
        COVERAGE_FIELDS,
        _matching_exception,
        approved_nyse_schedule,
        coverage_metadata_for_file,
        load_reviewed_coverage_exceptions,
        validate_v2_file_coverage,
        validate_v2_symbol_session_ranges,
    )

try:
    from .v2_provider_recovery import (
        PREFLIGHT_OK,
        build_batched_recovery_plan,
        default_dns_probe,
        default_tcp_probe,
        default_tls_probe,
        execute_recovery_batches,
        run_provider_preflight as run_provider_preflight_core,
        select_deterministic_canary,
        write_batched_recovery_plan,
    )
except ImportError:
    from v2_provider_recovery import (
        PREFLIGHT_OK,
        build_batched_recovery_plan,
        default_dns_probe,
        default_tcp_probe,
        default_tls_probe,
        execute_recovery_batches,
        run_provider_preflight as run_provider_preflight_core,
        select_deterministic_canary,
        write_batched_recovery_plan,
    )

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
NEW_YORK = ZoneInfo("America/New_York")

BENCHMARK_ONLY = [
    "SPY", "QQQ", "IWM", "DIA", "RSP", "VTI",
    "XLK", "XLF", "XLY", "XLC", "XLI", "XLV", "XLE", "XLP", "XLU", "XLRE", "XLB",
    "SMH", "SOXX", "XBI", "IBB", "XRT",
]

TRADING_UNIVERSE_V1 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO", "AMD",
    "NFLX", "ADBE", "CRM", "ORCL", "INTU", "NOW", "SHOP", "UBER", "ABNB", "SNOW",
    "PLTR", "CRWD", "PANW", "FTNT", "ZS", "NET", "DDOG", "ANET", "CSCO", "IBM",
    "QCOM", "TXN", "MU", "INTC", "AMAT", "LRCX", "KLAC", "MRVL", "MCHP", "ON",
    "JPM", "BAC", "WFC", "C", "GS", "MS", "BLK", "SCHW", "AXP", "MA", "V", "COF",
    "PNC", "USB", "BK", "SPGI", "ICE", "CME", "MCO", "BX",
    "LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "BMY", "AMGN", "GILD", "REGN",
    "VRTX", "ISRG", "TMO", "DHR", "ABT", "MDT", "CVS", "CI", "HUM", "ELV", "SYK", "BSX",
    "HD", "LOW", "COST", "WMT", "TGT", "TJX", "MCD", "SBUX", "NKE", "LULU",
    "CMG", "YUM", "BKNG", "MAR", "RCL", "CCL", "ROST", "DG", "DLTR",
    "DIS", "CMCSA", "T", "VZ", "TMUS", "EA", "TTWO", "RBLX", "PARA", "WBD",
    "CAT", "DE", "GE", "HON", "ETN", "PH", "UNP", "UPS", "FDX", "RTX", "LMT", "NOC",
    "BA", "GD", "MMM", "EMR", "CARR", "OTIS", "URI",
    "XOM", "CVX", "COP", "EOG", "SLB", "OXY", "MPC", "VLO", "PSX", "HAL",
    "LIN", "APD", "SHW", "ECL", "NEM", "FCX", "NUE", "STLD", "DOW", "DD", "ALB",
    "PG", "KO", "PEP", "PM", "MO", "CL", "KMB", "MDLZ", "GIS", "KHC", "HSY", "KR", "EL",
    "NEE", "DUK", "SO", "AEP", "EXC", "SRE", "XEL", "PEG",
    "AMT", "PLD", "EQIX", "DLR", "O", "PSA",
]

DEFAULT_SYMBOLS = list(dict.fromkeys([*BENCHMARK_ONLY, *TRADING_UNIVERSE_V1]))
V2_UNIVERSE_CONTRACT_MISMATCH = "V2_UNIVERSE_CONTRACT_MISMATCH"
V2_STRATEGY_IDENTIFIER = "rs_m126_top30_sma100_no_regime_v2_clean_stock"
V2_BASELINE_PATH = BASE_DIR / "baselines" / f"{V2_STRATEGY_IDENTIFIER}.json"
V2_COVERAGE_EXCEPTIONS_PATH = BASE_DIR / "baselines" / "rs_v2_clean_coverage_exceptions.json"
V2_BENCHMARK_ROLES = {"QQQ": "PRIMARY_BENCHMARK", "SPY": "SECONDARY_BENCHMARK"}
GAP_RECOVERY_AUDIT_RUN = "acq_20260712_212749"
GAP_RECOVERY_EXPECTED_PAIR_COUNT = 350
GAP_RECOVERY_EXPECTED_SYMBOL_COUNT = 123
GAP_RECOVERY_APPROVED_DATES = ("2021-04-19", "2021-10-25", "2022-03-08", "2022-04-06")
GAP_RECOVERY_REQUIRED_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]
DEFAULT_V2_FROZEN_ROOT = BASE_DIR / "research_data" / "rs_v2_clean_market_data"
DEFAULT_V2_RECOVERY_STAGING_ROOT = BASE_DIR / "research_data" / "rs_v2_clean_gap_recovery"

# --- rs_v2_sip profile: additive, parallel to rs_v2_clean above. Phase 2A scope
# is code foundations only -- mandatory-flag validation and --dry-run wiring are
# implemented; --live acquisition/strict manifest finalization is intentionally
# NOT implemented yet (see V2_SIP_LIVE_ACQUISITION_NOT_YET_IMPLEMENTED_PHASE2A
# below), since no frozen candidate-universe baseline exists for this profile
# until a real acquisition runs.
V2_SIP_DATASET_SCHEMA_VERSION = "2.1.0"
V2_SIP_BASELINE_PATH = BASE_DIR / "baselines" / "rs_v2_sip_baseline.json"
V2_SIP_COVERAGE_EXCEPTIONS_PATH = BASE_DIR / "baselines" / "rs_v2_sip_coverage_exceptions.json"
DEFAULT_V2_SIP_FROZEN_ROOT = BASE_DIR / "research_data" / "rs_v2_sip_market_data"


@dataclass(frozen=True)
class V2DatasetPaths:
    daily: Path
    raw_5min: Path
    bars_130min: Path


@dataclass(frozen=True)
class DownloadBarsResult:
    frame: pd.DataFrame
    provider_attempt_count: int
    retry_attempt_count: int
    failed_provider_attempt_count: int


class DownloadBarsRetryError(RuntimeError):
    def __init__(self, cause: Exception, provider_attempt_count: int, failed_provider_attempt_count: int):
        super().__init__(str(cause))
        self.provider_attempt_count = provider_attempt_count
        self.retry_attempt_count = max(provider_attempt_count - 1, 0)
        self.failed_provider_attempt_count = failed_provider_attempt_count


@dataclass(frozen=True)
class GapRecoveryRunPaths:
    run_root: Path
    daily: Path
    raw_5min: Path
    manifests: Path
    reports: Path


def validate_v2_retry_telemetry(manifest: dict) -> None:
    telemetry_status = manifest.get("retry_telemetry_status")
    telemetry_values = [manifest.get("provider_attempt_count"), manifest.get("retry_attempt_count"), manifest.get("failed_provider_attempt_count")]
    measured_values_valid = all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in telemetry_values)
    if telemetry_status == "MEASURED":
        provider_attempts, retry_attempts, failed_attempts = telemetry_values
        base_operation_count = manifest.get("logical_acquisition_item_count")
        # A daily refresh legitimately departs from "one provider call per
        # logical item" in exactly two ways, and both must be declared:
        #   no_provider_call_item_count -- registry EARLY_TERMINAL skips and
        #       items a resume walks past, which issue NO request
        #   reacquisition_item_count -- drift-triggered full re-acquisitions,
        #       which add a request WITHOUT a failure, so they are not retries
        # Absent on pre-refresh manifests, where both are zero and the identity
        # collapses to the original provider == base + retries. Declared but
        # malformed (None, negative, bool, non-int) still REJECTS, as does any
        # set of counts that fails to reconcile -- a refresh that forgets to
        # record a re-acquisition cannot balance, and is rejected.
        no_provider_call_items = manifest.get("no_provider_call_item_count", 0)
        reacquisition_items = manifest.get("reacquisition_item_count", 0)
        explanatory_valid = all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (no_provider_call_items, reacquisition_items)
        )
        expected_attempts = (
            (base_operation_count - no_provider_call_items) + retry_attempts + reacquisition_items
            if isinstance(base_operation_count, int) and explanatory_valid
            else None
        )
        if (
            not measured_values_valid
            or not isinstance(base_operation_count, int)
            or not explanatory_valid
            or no_provider_call_items > base_operation_count
            or provider_attempts != expected_attempts
            or failed_attempts != retry_attempts
        ):
            raise ValueError("V2_DATASET_NOT_VALIDATED: retry telemetry invalid")
    elif telemetry_status == "UNAVAILABLE":
        if any(value is not None for value in telemetry_values):
            raise ValueError("V2_DATASET_NOT_VALIDATED: retry telemetry invalid")
    else:
        raise ValueError("V2_DATASET_NOT_VALIDATED: retry telemetry missing")


def resolve_v2_dataset_paths(output_root: Path, symbol: str) -> V2DatasetPaths:
    """Pure V2 layout resolver; benchmarks never use candidate directories."""
    root = Path(output_root)
    benchmark_root = root / "benchmarks" if symbol in V2_BENCHMARK_ROLES else root
    return V2DatasetPaths(
        daily=benchmark_root / "daily" / f"{symbol}_1D.csv",
        raw_5min=benchmark_root / "raw_5min" / f"{symbol}_5min.csv",
        bars_130min=benchmark_root / "bars_130min" / f"{symbol}_130min.csv",
    )


def load_v2_frozen_universe(path: Path = V2_BASELINE_PATH) -> tuple[list[str], dict]:
    baseline = json.loads(Path(path).read_text(encoding="utf-8"))
    universe = baseline.get("universe", {})
    symbols = universe.get("candidate_symbols", [])
    excluded = set(universe.get("excluded_symbols", []))
    digest = hashlib.sha256(",".join(symbols).encode("utf-8")).hexdigest()
    valid = (
        baseline.get("strategy_identifier") == V2_STRATEGY_IDENTIFIER
        and len(symbols) == 177
        and len(symbols) == len(set(symbols))
        and symbols == sorted(symbols)
        and not (set(symbols) & excluded)
        and not ({"QQQ", "SPY"} & set(symbols))
        and universe.get("candidate_count") == 177
        and universe.get("candidate_universe_hash") == digest
        and digest == "026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933"
    )
    if not valid:
        raise ValueError(V2_UNIVERSE_CONTRACT_MISMATCH)
    return symbols, baseline


def _refresh_daily_terminated_symbols(symbols: list[str], exceptions: list[dict], as_of_date: str) -> set[str]:
    """Symbols whose acquisition should be skipped during --refresh-daily because a
    reviewed EARLY_TERMINAL exception (registry) shows no data can exist for them on
    or after as_of_date. Registry-driven: no symbol literals here -- the exception
    file is the only source of which symbols this applies to.
    """
    terminated = set()
    for symbol in symbols:
        terminal_exception = _matching_exception(exceptions, symbol, "EARLY_TERMINAL")
        if terminal_exception is not None and str(terminal_exception.get("approved_last_session", "")) <= as_of_date:
            terminated.add(symbol)
    return terminated


def validate_v2_manifest_and_files(dataset_root: Path, manifest: dict, frozen_baseline: dict | None = None) -> None:
    """Shared pure V2 publication/backtest contract validator. Raises on any mismatch."""
    root = Path(dataset_root)
    candidates, baseline = load_v2_frozen_universe(V2_BASELINE_PATH)
    if frozen_baseline is not None and frozen_baseline.get("strategy_identifier") != V2_STRATEGY_IDENTIFIER:
        raise ValueError(V2_UNIVERSE_CONTRACT_MISMATCH)
    item_count = manifest.get("logical_acquisition_item_count", 358)
    if item_count not in (179, 358):
        raise ValueError("V2_DATASET_NOT_VALIDATED: frozen contract mismatch")
    include_intraday = (item_count == 358)
    exact = {
        "dataset_schema_version": manifest.get("dataset_schema_version", "2.0.0"), "dataset_profile": "rs_v2_clean", "strategy_identifier": V2_STRATEGY_IDENTIFIER,
        "target_start_date": "2020-07-27", "target_end_date": "2026-07-07", "feed": "iex", "adjustment": "all",
        "candidate_count": len(candidates), "candidate_universe_hash": baseline["universe"]["candidate_universe_hash"],
        "logical_acquisition_item_count": item_count, "successful_item_count": item_count, "failed_item_count": 0,
    }
    if any(manifest.get(key) != value for key, value in exact.items()) or not manifest.get("acquisition_run_id") or manifest.get("status") not in {"VALIDATION_PENDING", "COMPLETE_VALIDATED"}:
        raise ValueError("V2_DATASET_NOT_VALIDATED: frozen contract mismatch")
    validate_v2_retry_telemetry(manifest)
    expected = {(row["symbol"], row["timeframe"]): row for row in build_v2_acquisition_plan(root, candidates, manifest.get("target_start_date"), manifest.get("target_end_date"), "iex", "all", include_intraday=include_intraday)}
    actual = {}
    for record in manifest.get("files", []):
        key = (record.get("symbol"), record.get("timeframe"))
        if key in actual:
            raise ValueError("V2_DATASET_NOT_VALIDATED: duplicate symbol/timeframe")
        actual[key] = record
    if set(actual) != set(expected):
        raise ValueError("V2_DATASET_NOT_VALIDATED: exact logical record set mismatch")
    exceptions = load_reviewed_coverage_exceptions(V2_COVERAGE_EXCEPTIONS_PATH)
    for key, expected_record in expected.items():
        record = actual[key]
        if (record.get("asset_role") != expected_record["asset_role"] or record.get("output_relative_path") != expected_record["output_relative_path"] or record.get("requested_start") != exact["target_start_date"] or record.get("requested_end") != exact["target_end_date"] or record.get("feed") != "iex" or record.get("adjustment") != "all" or record.get("acquisition_run_id") != manifest["acquisition_run_id"]):
            raise ValueError("V2_DATASET_NOT_VALIDATED: role/path mismatch")
        path = root / record["output_relative_path"]
        integrity = verify_file_integrity(path)
        if record.get("request_status") != "SUCCESS" or integrity["data_status"] != "VALID" or integrity["sha256"] != record.get("sha256"):
            raise ValueError("V2_DATASET_NOT_VALIDATED: file integrity/status mismatch")
        if not COVERAGE_FIELDS.issubset(record):
            raise ValueError("V2_DATASET_NOT_VALIDATED: missing coverage metadata")
        if record["timeframe"] == "1D":
            if record.get("acquisition_action") != "DOWNLOADED_IN_CURRENT_RUN" or record.get("downloaded_in_current_run") is not True or record.get("provider_result_status") != "DOWNLOADED":
                raise ValueError("V2_DATASET_NOT_VALIDATED: stale daily provenance")
        if record["timeframe"] == "130min":
            raw_relative = record.get("source_raw_5min_relative_path", "")
            expected_raw_relative = resolve_v2_dataset_paths(root, record["symbol"]).raw_5min.relative_to(root).as_posix()
            if not raw_relative or Path(raw_relative).is_absolute() or Path(raw_relative).as_posix() != expected_raw_relative:
                raise ValueError("V2_DATASET_NOT_VALIDATED: raw-to-130min lineage path mismatch")
            raw = root / raw_relative
            raw_integrity = verify_file_integrity(raw)
            if raw_integrity["data_status"] != "VALID" or raw_integrity["sha256"] != record.get("source_raw_5min_sha256") or raw_integrity["row_count"] != record.get("source_raw_5min_row_count") or integrity["row_count"] != record.get("derived_row_count") or record.get("derivation_status") != "SUCCESS":
                raise ValueError("V2_DATASET_NOT_VALIDATED: raw-to-130min lineage mismatch")
            if record.get("acquisition_action") != "DERIVED_IN_CURRENT_RUN" or record.get("raw_downloaded_in_current_run") is not True or record.get("aggregation_result_status") != "BUILT":
                raise ValueError("V2_DATASET_NOT_VALIDATED: stale intraday provenance")
    market_schedule = approved_nyse_schedule(exact["target_start_date"], exact["target_end_date"])
    recomputed_by_symbol = {}
    for key in expected:
        record = actual[key]
        path = root / record["output_relative_path"]
        coverage = validate_v2_file_coverage(
            path, record, record["symbol"], record["timeframe"], exact["target_start_date"], exact["target_end_date"],
            reviewed_exceptions=exceptions, market_schedule=market_schedule,
        )
        recomputed_by_symbol.setdefault(record["symbol"], {})[record["timeframe"]] = coverage
    for symbol, coverages in recomputed_by_symbol.items():
        daily = coverages.get("1D", {})
        intraday = coverages.get("130min", {})
        validate_v2_symbol_session_ranges(symbol, daily, intraday)


def validate_v2_sip_manifest_and_files(dataset_root: Path, manifest: dict, frozen_baseline: dict | None = None) -> None:
    """Shared pure V2 SIP publication/backtest contract validator. Raises on any mismatch."""
    root = Path(dataset_root)
    candidates, baseline = load_v2_frozen_universe(V2_BASELINE_PATH)
    if frozen_baseline is not None and frozen_baseline.get("strategy_identifier") != V2_STRATEGY_IDENTIFIER:
        raise ValueError(V2_UNIVERSE_CONTRACT_MISMATCH)
    item_count = manifest.get("logical_acquisition_item_count", 358)
    if item_count not in (179, 358):
        raise ValueError("V2_DATASET_NOT_VALIDATED: frozen contract mismatch")
    include_intraday = (item_count == 358)
    exact = {
        "dataset_schema_version": manifest.get("dataset_schema_version", "2.1.0"), "dataset_profile": "rs_v2_sip", "strategy_identifier": V2_STRATEGY_IDENTIFIER,
        "target_start_date": "2020-07-27", "target_end_date": "2026-07-07", "feed": "sip", "adjustment": "all",
        "candidate_count": len(candidates), "candidate_universe_hash": baseline["universe"]["candidate_universe_hash"],
        "logical_acquisition_item_count": item_count, "successful_item_count": item_count, "failed_item_count": 0,
    }
    other_keys_match = all(manifest.get(key) == value for key, value in exact.items() if key != "target_end_date")
    if manifest.get("refresh_daily"):
        target_end_date = manifest.get("target_end_date")
        target_end_date_valid = isinstance(target_end_date, str) and target_end_date >= exact["target_end_date"]
    else:
        target_end_date_valid = manifest.get("target_end_date") == exact["target_end_date"]
    if not other_keys_match or not target_end_date_valid or not manifest.get("acquisition_run_id") or manifest.get("status") not in {"VALIDATION_PENDING", "COMPLETE_VALIDATED"}:
        raise ValueError("V2_DATASET_NOT_VALIDATED: frozen contract mismatch")
    validate_v2_retry_telemetry(manifest)
    expected = {(row["symbol"], row["timeframe"]): row for row in build_v2_acquisition_plan(root, candidates, manifest.get("target_start_date"), manifest.get("target_end_date"), "sip", "all", include_intraday=include_intraday)}
    actual = {}
    for record in manifest.get("files", []):
        key = (record.get("symbol"), record.get("timeframe"))
        if key in actual:
            raise ValueError("V2_DATASET_NOT_VALIDATED: duplicate symbol/timeframe")
        actual[key] = record
    if set(actual) != set(expected):
        raise ValueError("V2_DATASET_NOT_VALIDATED: exact logical record set mismatch")
    exceptions = load_reviewed_coverage_exceptions(V2_SIP_COVERAGE_EXCEPTIONS_PATH)
    for key, expected_record in expected.items():
        record = actual[key]
        if (record.get("asset_role") != expected_record["asset_role"] or record.get("output_relative_path") != expected_record["output_relative_path"] or record.get("requested_start") != exact["target_start_date"] or record.get("requested_end") != manifest.get("target_end_date") or record.get("feed") != "sip" or record.get("adjustment") != "all" or record.get("acquisition_run_id") != manifest["acquisition_run_id"]):
            raise ValueError("V2_DATASET_NOT_VALIDATED: role/path mismatch")
        path = root / record["output_relative_path"]
        integrity = verify_file_integrity(path)
        if record.get("request_status") != "SUCCESS" or integrity["data_status"] != "VALID" or integrity["sha256"] != record.get("sha256"):
            raise ValueError("V2_DATASET_NOT_VALIDATED: file integrity/status mismatch")
        if not COVERAGE_FIELDS.issubset(record):
            raise ValueError("V2_DATASET_NOT_VALIDATED: missing coverage metadata")
        if record["timeframe"] == "1D":
            if manifest.get("refresh_daily"):
                if record.get("acquisition_action") != "DOWNLOADED_IN_CURRENT_RUN" or record.get("provider_result_status") not in {"DOWNLOADED", "REFRESHED", "SKIPPED"}:
                    raise ValueError("V2_DATASET_NOT_VALIDATED: stale daily provenance")
            else:
                if record.get("acquisition_action") != "DOWNLOADED_IN_CURRENT_RUN" or record.get("downloaded_in_current_run") is not True or record.get("provider_result_status") != "DOWNLOADED":
                    raise ValueError("V2_DATASET_NOT_VALIDATED: stale daily provenance")
        if record["timeframe"] == "130min":
            raw_relative = record.get("source_raw_5min_relative_path", "")
            expected_raw_relative = resolve_v2_dataset_paths(root, record["symbol"]).raw_5min.relative_to(root).as_posix()
            if not raw_relative or Path(raw_relative).is_absolute() or Path(raw_relative).as_posix() != expected_raw_relative:
                raise ValueError("V2_DATASET_NOT_VALIDATED: raw-to-130min lineage path mismatch")
            raw = root / raw_relative
            raw_integrity = verify_file_integrity(raw)
            if raw_integrity["data_status"] != "VALID" or raw_integrity["sha256"] != record.get("source_raw_5min_sha256") or raw_integrity["row_count"] != record.get("source_raw_5min_row_count") or integrity["row_count"] != record.get("derived_row_count") or record.get("derivation_status") != "SUCCESS":
                raise ValueError("V2_DATASET_NOT_VALIDATED: raw-to-130min lineage mismatch")
            if manifest.get("refresh_daily"):
                if record.get("acquisition_action") != "DERIVED_IN_CURRENT_RUN" or record.get("aggregation_result_status") not in {"BUILT", "SKIPPED"}:
                    raise ValueError("V2_DATASET_NOT_VALIDATED: stale intraday provenance")
            else:
                if record.get("acquisition_action") != "DERIVED_IN_CURRENT_RUN" or record.get("raw_downloaded_in_current_run") is not True or record.get("aggregation_result_status") != "BUILT":
                    raise ValueError("V2_DATASET_NOT_VALIDATED: stale intraday provenance")
    market_schedule = approved_nyse_schedule(exact["target_start_date"], manifest.get("target_end_date"))
    recomputed_by_symbol = {}
    for key in expected:
        record = actual[key]
        path = root / record["output_relative_path"]
        coverage = validate_v2_file_coverage(
            path, record, record["symbol"], record["timeframe"], exact["target_start_date"], manifest.get("target_end_date"),
            reviewed_exceptions=exceptions, market_schedule=market_schedule,
        )
        recomputed_by_symbol.setdefault(record["symbol"], {})[record["timeframe"]] = coverage
    for symbol, coverages in recomputed_by_symbol.items():
        daily = coverages.get("1D", {})
        intraday = coverages.get("130min", {})
        validate_v2_symbol_session_ranges(symbol, daily, intraday)


def freeze_v2_sip_baseline() -> None:
    """Populates baselines/rs_v2_sip_baseline.json on successful SIP validation."""
    clean_baseline = json.loads(V2_BASELINE_PATH.read_text(encoding="utf-8"))
    sip_baseline = {
        "baseline_schema_version": "2.1.0",
        "dataset_profile": "rs_v2_sip",
        "strategy_identifier": V2_STRATEGY_IDENTIFIER,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "FROZEN",
        "universe": clean_baseline.get("universe"),
        "benchmark": clean_baseline.get("benchmark"),
        "strategy": clean_baseline.get("strategy"),
        "execution": clean_baseline.get("execution"),
        "sizing_contract": clean_baseline.get("sizing_contract"),
    }
    _atomic_write_json(sip_baseline, V2_SIP_BASELINE_PATH)
    print(f"SIP baseline successfully frozen and written to {V2_SIP_BASELINE_PATH}", flush=True)


def build_v2_acquisition_plan(output_root: Path, candidates: list[str], start: str, end: str, feed: str, adjustment: str, include_intraday: bool = True) -> list[dict]:
    rows = []
    for symbol in [*candidates, "QQQ", "SPY"]:
        paths = resolve_v2_dataset_paths(output_root, symbol)
        role = V2_BENCHMARK_ROLES.get(symbol, "CANDIDATE")
        timeframes = (("1D", paths.daily), ("130min", paths.bars_130min)) if include_intraday else (("1D", paths.daily),)
        for timeframe, path in timeframes:
            rows.append({"symbol": symbol, "timeframe": timeframe, "requested_start": start, "requested_end": end, "feed": feed, "adjustment": adjustment, "output_relative_path": path.relative_to(output_root).as_posix(), "asset_role": role})
    return rows


def write_v2_dry_run_plan(report_dir: Path, plan_rows: list[dict], profile: str = "rs_v2_clean") -> tuple[Path, Path]:
    """Write only explicit dry-run report destination; never acquisition state."""
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    plan_csv = report_dir / f"{profile}_data_acquisition_plan.csv"
    plan_md = report_dir / f"{profile}_data_acquisition_plan.md"
    frame = pd.DataFrame(plan_rows)
    frame.to_csv(plan_csv, index=False)
    title = "V2 SIP Data Acquisition Plan" if profile == "rs_v2_sip" else "V2 Clean Data Acquisition Plan"
    plan_md.write_text("\n".join([f"# {title}", "", "- Status: `DRY_RUN_PLAN_ONLY`", f"- logical_acquisition_item_count: {len(plan_rows)}", "", frame.to_markdown(index=False)]) + "\n", encoding="utf-8")
    return plan_csv, plan_md


def write_v2_coverage_review_reports(file_records: list[dict], latest_dir: Path, profile: str = "rs_v2_clean") -> tuple[Path, Path, int]:
    rows = []
    for record in file_records:
        reason = str(record.get("coverage_exception_reason", ""))
        for exception_type, marker in (("LATE_START", "UNAPPROVED_LATE_START"), ("EARLY_TERMINAL", "UNAPPROVED_EARLY_TERMINAL")):
            if marker in reason:
                rows.append({"symbol": record.get("symbol", ""), "timeframe": record.get("timeframe", ""), "exception_type": exception_type, "reason": reason})
    latest_dir = Path(latest_dir)
    latest_dir.mkdir(parents=True, exist_ok=True)
    csv_path = latest_dir / f"{profile}_coverage_review_required.csv"
    md_path = latest_dir / f"{profile}_coverage_review_required.md"
    columns = ["symbol", "timeframe", "exception_type", "reason"]
    pd.DataFrame(rows, columns=columns).to_csv(csv_path, index=False)
    symbol_count = len({str(row["symbol"]) for row in rows})
    title = "V2 SIP Coverage Review Required" if profile == "rs_v2_sip" else "V2 Coverage Review Required"
    lines = [f"# {title}", "", f"- review_required_symbol_count: {symbol_count}", f"- review_required_record_count: {len(rows)}", "- exceptions_auto_approved: false", ""]
    lines.extend(f"- {row['symbol']} {row['timeframe']} {row['exception_type']}: {row['reason']}" for row in rows)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path, symbol_count


def write_v2_applied_exceptions_report(file_records: list[dict], latest_dir: Path, profile: str = "rs_v2_clean") -> tuple[Path, Path, int]:
    """Surface every APPROVED_* coverage exception actually applied during validation.

    Approved exceptions suppress a validation failure but must never be silently
    absorbed -- each symbol/timeframe/date combination they covered is listed here.
    """
    rows = []
    for record in file_records:
        reason = str(record.get("coverage_exception_reason", ""))
        reason_tokens = set(reason.split(";"))
        symbol = record.get("symbol", "")
        timeframe = record.get("timeframe", "")
        if "APPROVED_LATE_START" in reason_tokens:
            rows.append({"symbol": symbol, "timeframe": timeframe, "exception_type": "LATE_START", "affected_date": record.get("first_market_session", ""), "reason": reason})
        if "APPROVED_EARLY_TERMINAL" in reason_tokens:
            rows.append({"symbol": symbol, "timeframe": timeframe, "exception_type": "EARLY_TERMINAL", "affected_date": record.get("last_market_session", ""), "reason": reason})
        if "APPROVED_MISSING_INTERNAL_SESSION" in reason_tokens:
            for affected_date in record.get("approved_internal_session_exception_dates", []):
                rows.append({"symbol": symbol, "timeframe": timeframe, "exception_type": "MISSING_INTERNAL_SESSION", "affected_date": affected_date, "reason": reason})
        if "APPROVED_PARTIAL_BOUNDARY_SESSION" in reason_tokens:
            for affected_date in record.get("approved_partial_boundary_session_dates", []):
                rows.append({"symbol": symbol, "timeframe": timeframe, "exception_type": "PARTIAL_BOUNDARY_SESSION", "affected_date": affected_date, "reason": reason})
    latest_dir = Path(latest_dir)
    latest_dir.mkdir(parents=True, exist_ok=True)
    if profile == "rs_v2_sip":
        csv_path = latest_dir / "rs_v2_sip_applied_coverage_exceptions.csv"
        md_path = latest_dir / "rs_v2_sip_applied_coverage_exceptions.md"
        title = "V2 SIP Applied Coverage Exceptions"
    else:
        csv_path = latest_dir / "rs_v2_applied_coverage_exceptions.csv"
        md_path = latest_dir / "rs_v2_applied_coverage_exceptions.md"
        title = "V2 Applied Coverage Exceptions"
    columns = ["symbol", "timeframe", "exception_type", "affected_date", "reason"]
    pd.DataFrame(rows, columns=columns).to_csv(csv_path, index=False)
    applied_count = len(rows)
    lines = [f"# {title}", "", f"- applied_exception_count: {applied_count}", "- exceptions_silently_absorbed: false", ""]
    lines.extend(f"- {row['symbol']} {row['timeframe']} {row['exception_type']} {row['affected_date']}: {row['reason']}" for row in rows)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path, applied_count


RANGE_SHORTFALL_SESSION_THRESHOLD = 5


def write_v2_range_shortfall_report(file_records: list[dict], latest_dir: Path, profile: str = "rs_v2_clean") -> tuple[Path, Path, int]:
    """Surface any file whose returned data ends more than RANGE_SHORTFALL_SESSION_THRESHOLD
    sessions short of the requested end date, unless an approved EARLY_TERMINAL
    exception already accounts for the gap (a genuinely delisted/renamed symbol).

    Telemetry only: never raises, never affects validation status. This exists
    because acquisitions previously recorded request_status=SUCCESS with zero
    completeness check against the requested range (see
    reports/audit/phase2c_residual_gap_characterization_20260718.md Task B.1),
    which is how BK's gap went unnoticed until manual investigation.
    """
    rows = []
    for record in file_records:
        reason = str(record.get("coverage_exception_reason", ""))
        end_gap = int(record.get("end_gap_sessions", 0) or 0)
        if end_gap > RANGE_SHORTFALL_SESSION_THRESHOLD and "APPROVED_EARLY_TERMINAL" not in reason.split(";"):
            rows.append({
                "symbol": record.get("symbol", ""),
                "timeframe": record.get("timeframe", ""),
                "requested_start": record.get("requested_start", ""),
                "requested_end": record.get("requested_end", ""),
                "returned_first_session": record.get("first_market_session", ""),
                "returned_last_session": record.get("last_market_session", ""),
                "shortfall_sessions": end_gap,
                "warning_type": "RANGE_SHORTFALL",
            })
    latest_dir = Path(latest_dir)
    latest_dir.mkdir(parents=True, exist_ok=True)
    csv_path = latest_dir / f"{profile}_range_shortfall_warnings.csv"
    md_path = latest_dir / f"{profile}_range_shortfall_warnings.md"
    columns = ["symbol", "timeframe", "requested_start", "requested_end", "returned_first_session", "returned_last_session", "shortfall_sessions", "warning_type"]
    pd.DataFrame(rows, columns=columns).to_csv(csv_path, index=False)
    symbol_count = len({str(row["symbol"]) for row in rows})
    title = "V2 SIP Range Shortfall Warnings" if profile == "rs_v2_sip" else "V2 Range Shortfall Warnings"
    lines = [f"# {title}", "", f"- range_shortfall_symbol_count: {symbol_count}", f"- range_shortfall_record_count: {len(rows)}", f"- shortfall_session_threshold: {RANGE_SHORTFALL_SESSION_THRESHOLD}", "- note: telemetry only, does not affect validation status", ""]
    lines.extend(f"- {row['symbol']} {row['timeframe']}: returned through {row['returned_last_session']}, requested through {row['requested_end']} ({row['shortfall_sessions']} sessions short)" for row in rows)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path, symbol_count


def write_v2_run_acquisition_report(run_dir: Path, latest_dir: Path, summary: dict, records: list[dict], profile: str = "rs_v2_clean") -> tuple[Path, Path]:
    """Write V2-only run diagnostics without touching the legacy refresh report."""
    required = {"acquisition_run_id", "dataset_profile", "refresh_mode", "symbol_count", "requested_start", "requested_end", "validation_status"}
    if not required.issubset(summary):
        raise ValueError("V2_ACQUISITION_REPORT_SUMMARY_INCOMPLETE")
    title = "V2 SIP Acquisition Report" if profile == "rs_v2_sip" else "V2 Clean Acquisition Report"
    lines = [f"# {title}", ""]
    lines.extend(f"- {key}: {summary.get(key, '')}" for key in sorted(summary))
    lines.extend(["", f"Manifest records: {len(records)}", ""])
    text = "\n".join(lines)
    run_dir, latest_dir = Path(run_dir), Path(latest_dir)
    run_md = run_dir / "acquisition_report.md"
    run_csv = run_dir / "acquisition_report.csv"
    latest_md = latest_dir / f"{profile}_acquisition_report.md"
    latest_csv = latest_dir / f"{profile}_acquisition_report.csv"
    run_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    for path in (run_md, latest_md):
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    frame = pd.DataFrame([summary])
    for path in (run_csv, latest_csv):
        temporary = path.with_name(f".{path.name}.tmp")
        frame.to_csv(temporary, index=False)
        os.replace(temporary, path)
    return run_md, latest_md


def finalize_v2_with_report(
    manifest: dict,
    run_manifest_path: Path,
    latest_manifest_path: Path,
    run_report_dir: Path,
    latest_report_dir: Path,
    report_summary: dict,
    records: list[dict],
    profile: str = "rs_v2_clean",
) -> None:
    write_v2_run_acquisition_report(run_report_dir, latest_report_dir, {**report_summary, "validation_status": "VALIDATION_PENDING"}, records, profile=profile)
    try:
        finalize_v2_manifest(manifest, run_manifest_path, latest_manifest_path, profile=profile)
    except BaseException as original_error:
        status = "FAILED_VALIDATION" if isinstance(original_error, Exception) else "INTERRUPTED"
        try:
            write_v2_run_acquisition_report(run_report_dir, latest_report_dir, {**report_summary, "validation_status": status}, records, profile=profile)
        except Exception:
            pass
        raise
    write_v2_run_acquisition_report(run_report_dir, latest_report_dir, {**report_summary, "validation_status": "COMPLETE_VALIDATED"}, records, profile=profile)


@contextmanager
def v2_offline_audit_guard():
    try:
        from .v2_coverage_forensics import forbid_network_calls
    except ImportError:
        from v2_coverage_forensics import forbid_network_calls
    protected_names = ("_load_alpaca", "get_credentials", "download_bars", "download_bars_with_retry")
    originals = {name: globals().get(name) for name in protected_names}
    telemetry = {"client_attempt_count": 0, "network_attempt_count": 0}

    def blocked_client(*_args, **_kwargs):
        telemetry["client_attempt_count"] += 1
        raise RuntimeError("V2_OFFLINE_AUDIT_CLIENT_CALL_BLOCKED")

    for name in protected_names:
        globals()[name] = blocked_client
    try:
        with forbid_network_calls() as network:
            yield telemetry
            telemetry["network_attempt_count"] = network["network_attempt_count"]
    finally:
        for name, value in originals.items():
            globals()[name] = value


def _atomic_write_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def finalize_v2_manifest(manifest: dict, run_manifest_path: Path, latest_manifest_path: Path, profile: str = "rs_v2_clean") -> None:
    if manifest.get("status") != "VALIDATION_PENDING":
        raise ValueError("V2_MANIFEST_NOT_VALIDATION_PENDING")
    _atomic_write_json(manifest, run_manifest_path)
    try:
        committed = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        records = committed.get("files", [])
        logical_keys = {(row.get("symbol"), row.get("timeframe"), row.get("output_relative_path")) for row in records}
        expected_count = committed.get("logical_acquisition_item_count", 358)
        if committed.get("status") != "VALIDATION_PENDING" or len(records) != expected_count or len(logical_keys) != expected_count:
            raise ValueError("V2_IMMUTABLE_MANIFEST_COMMIT_FAILED")
        if profile == "rs_v2_sip":
            validate_v2_sip_manifest_and_files(latest_manifest_path.parents[1], committed)
        elif profile == "rs_v2_tiingo":
            _tiingo_provider().validate_v2_tiingo_manifest_and_files(latest_manifest_path.parents[1], committed)
        else:
            validate_v2_manifest_and_files(latest_manifest_path.parents[1], committed)
        committed = {**committed, "status": "COMPLETE_VALIDATED"}
        committed["validation_completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _atomic_write_json(committed, run_manifest_path)
        committed = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        if profile == "rs_v2_sip":
            validate_v2_sip_manifest_and_files(latest_manifest_path.parents[1], committed)
            freeze_v2_sip_baseline()
        elif profile == "rs_v2_tiingo":
            _tiingo_provider().validate_v2_tiingo_manifest_and_files(latest_manifest_path.parents[1], committed)
            _tiingo_provider().freeze_v2_tiingo_baseline()
        else:
            validate_v2_manifest_and_files(latest_manifest_path.parents[1], committed)
    except Exception as exc:
        failed = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        failed = {
            **failed,
            "status": "FAILED_VALIDATION",
            "validation_error_type": type(exc).__name__,
            "validation_error_message": str(exc),
            "validation_failed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        failed.pop("validation_completed_at", None)
        _atomic_write_json(failed, run_manifest_path)
        raise
    pointer = {"acquisition_run_id": committed.get("acquisition_run_id"), "immutable_manifest_relative_path": run_manifest_path.relative_to(latest_manifest_path.parents[1]).as_posix(), "immutable_manifest_sha256": hashlib.sha256(run_manifest_path.read_bytes()).hexdigest()}
    _atomic_write_json(pointer, latest_manifest_path)


def _tiingo_provider():
    """Lazy import: keeps the Tiingo profile parallel to (not entangled with)
    the Alpaca acquisition path and avoids a circular module import."""
    try:
        from . import tiingo_provider as module
    except ImportError:
        import tiingo_provider as module
    return module


def _source_commit() -> str | None:
    try:
        value = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_DIR, text=True, stderr=subprocess.DEVNULL).strip()
        return value or None
    except (OSError, subprocess.CalledProcessError):
        return None


def ensure_project_venv() -> None:
    if sys.platform != "win32":
        linux_venv_python = PROJECT_DIR / ".venv_linux" / "bin" / "python"
        if linux_venv_python.exists():
            current_python = Path(sys.executable).resolve()
            if current_python != linux_venv_python.resolve():
                command = [str(linux_venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]
                raise SystemExit(subprocess.call(command, cwd=str(Path.cwd())))
        return

    venv_python = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        return
    current_python = Path(sys.executable).resolve()
    if current_python == venv_python.resolve():
        return
    command = [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.call(command, cwd=str(Path.cwd())))


import numpy as np
import pandas as pd


def _load_alpaca():
    try:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        return Adjustment, DataFeed, StockHistoricalDataClient, StockBarsRequest, TimeFrame, TimeFrameUnit
    except ModuleNotFoundError as error:
        if error.name != "alpaca":
            raise
        raise RuntimeError("alpaca-py is required only for LIVE acquisition") from error


def __getattr__(name: str):
    """Compatibility attributes stay lazy for non-LIVE callers."""
    names = {"Adjustment": 0, "DataFeed": 1, "StockHistoricalDataClient": 2, "StockBarsRequest": 3, "TimeFrame": 4, "TimeFrameUnit": 5}
    if name in names:
        return _load_alpaca()[names[name]]
    raise AttributeError(name)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def get_credentials() -> tuple[str, str]:
    load_env_file(PROJECT_DIR / ".env")
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY ve ALPACA_SECRET_KEY .env dosyasinda tanimli olmali.")
    return api_key, secret_key


def resolve_date_end(end_arg: str) -> datetime:
    if str(end_arg).strip().lower() == "today":
        return datetime.now(timezone.utc)
    return parse_utc_date(end_arg)


def resolve_intraday_end(end_arg: str) -> datetime:
    if str(end_arg).strip().lower() == "today":
        return datetime.now(timezone.utc)
    return parse_utc_date(end_arg)


def parse_utc_date(value: str) -> datetime:
    if str(value).strip().lower() == "today":
        return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def unique_symbols(symbols: Iterable[str]) -> list[str]:
    cleaned = []
    for raw in symbols:
        symbol = raw.strip().upper()
        if symbol and symbol not in {"SYMBOL", "TICKER"}:
            cleaned.append(symbol)
    return list(dict.fromkeys(cleaned))


def load_symbols(symbols: list[str] | None, symbols_file: str | None, universe: str | None) -> list[str]:
    result = []
    if universe == "all" or (universe is None and not symbols and not symbols_file):
        result.extend(DEFAULT_SYMBOLS)
    if symbols:
        result.extend(symbols)
    if symbols_file:
        for line in Path(symbols_file).read_text(encoding="utf-8").splitlines():
            result.append(line.strip().split(",")[0])
    return unique_symbols(result)


def get_data_feed(feed_name: str):
    _, DataFeed, _, _, _, _ = _load_alpaca()
    return DataFeed.SIP if feed_name.lower() == "sip" else DataFeed.IEX


def get_adjustment(adjustment_name: str):
    Adjustment, _, _, _, _, _ = _load_alpaca()
    name = adjustment_name.lower()
    if name in {"all", "adjusted"}:
        return Adjustment.ALL
    if name in {"raw", "none", "unadjusted"}:
        return Adjustment.RAW
    if name == "split":
        return Adjustment.SPLIT
    if name == "dividend":
        return Adjustment.DIVIDEND
    raise ValueError(f"Unsupported adjustment: {adjustment_name}")


def normalize_bars(raw_bars: pd.DataFrame, symbol: str, rth_only: bool) -> pd.DataFrame:
    if raw_bars is None or raw_bars.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    bars = raw_bars.copy()
    if isinstance(bars.index, pd.MultiIndex):
        symbol_level = "symbol" if "symbol" in bars.index.names else bars.index.names[0]
        if symbol not in set(bars.index.get_level_values(symbol_level)):
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        bars = bars.xs(symbol, level=symbol_level)

    bars.index = pd.to_datetime(bars.index, utc=True).tz_convert(NEW_YORK)
    bars = bars.sort_index()
    if rth_only:
        bars = bars.between_time("09:30", "15:59", inclusive="both")

    bars = bars.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    )
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in bars.columns]
    if missing:
        raise ValueError(f"{symbol}: eksik kolonlar: {missing}")
    bars = bars[required].dropna()
    bars.index.name = "Date"
    return bars.reset_index()


def aggregate_to_130min(
    raw_5min: pd.DataFrame,
    market_schedule: pd.DataFrame,
    *,
    dataset_end: str | pd.Timestamp,
) -> pd.DataFrame:
    """Aggregate V2 raw bars within approved session windows, deterministically."""
    output_columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if raw_5min.empty:
        return pd.DataFrame(columns=output_columns)
    if market_schedule is None or market_schedule.empty:
        raise ValueError("V2_130MIN_MARKET_SCHEDULE_REQUIRED")

    frame = raw_5min.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["Date"]).sort_values("Date")
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"V2_130MIN_MISSING_COLUMNS: {missing}")

    schedule = market_schedule.copy()
    schedule.index = pd.DatetimeIndex(pd.to_datetime(schedule.index)).tz_localize(None).normalize()
    schedule = schedule.loc[schedule.index <= pd.Timestamp(dataset_end).tz_localize(None).normalize()].sort_index()
    schedule["_market_open"] = pd.to_datetime(schedule["market_open"], utc=True)
    schedule["_market_close"] = pd.to_datetime(schedule["market_close"], utc=True)
    windows = schedule[["_market_open", "_market_close"]]

    local_time = frame["Date"].dt.tz_convert(NEW_YORK)
    frame["_session"] = local_time.dt.tz_localize(None).dt.normalize()
    frame = frame.join(windows, on="_session")
    frame = frame[
        frame["_market_open"].notna()
        & frame["Date"].ge(frame["_market_open"])
        & frame["Date"].lt(frame["_market_close"])
    ].copy()
    if frame.empty:
        return pd.DataFrame(columns=output_columns)

    elapsed_minutes = (frame["Date"] - frame["_market_open"]).dt.total_seconds().floordiv(60).astype(int)
    frame["_block"] = elapsed_minutes.floordiv(130)
    frame["_block_start"] = frame["_market_open"] + pd.to_timedelta(frame["_block"] * 130, unit="min")
    frame = frame[frame["_block_start"] < frame["_market_close"]]
    output = frame.groupby("_block_start", sort=True).agg(
        Open=("Open", "first"),
        High=("High", "max"),
        Low=("Low", "min"),
        Close=("Close", "last"),
        Volume=("Volume", "sum"),
    ).reset_index().rename(columns={"_block_start": "Date"})
    return output[output_columns].dropna().reset_index(drop=True)


def _aggregate_to_130min_legacy(raw_5min: pd.DataFrame) -> pd.DataFrame:
    if raw_5min.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    frame = raw_5min.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], utc=True)
    frame = frame.sort_values("Date")
    local_time = frame["Date"].dt.tz_convert(NEW_YORK)
    minutes = local_time.dt.hour * 60 + local_time.dt.minute
    rth_start = 9 * 60 + 30
    rth_end = 16 * 60
    frame = frame[(minutes >= rth_start) & (minutes < rth_end)].copy()
    if frame.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    minutes = minutes.loc[frame.index]
    local_time = local_time.loc[frame.index]
    frame["session"] = local_time.dt.date
    frame["block"] = pd.cut(
        minutes,
        bins=[rth_start - 1, 11 * 60 + 39, 13 * 60 + 49, rth_end - 1],
        labels=[0, 1, 2],
    ).astype(int)
    output = frame.groupby(["session", "block"], sort=True).agg(
        Open=("Open", "first"),
        High=("High", "max"),
        Low=("Low", "min"),
        Close=("Close", "last"),
        Volume=("Volume", "sum"),
    ).reset_index()
    block_start_minutes = {0: 9 * 60 + 30, 1: 11 * 60 + 40, 2: 13 * 60 + 50}
    session_start = pd.to_datetime(output["session"].astype(str)).dt.tz_localize(NEW_YORK)
    output["Date"] = [
        session + pd.Timedelta(minutes=block_start_minutes[int(block)])
        for session, block in zip(session_start, output["block"])
    ]
    output["Date"] = pd.to_datetime(output["Date"], utc=True)
    block_end_minutes = {0: 11 * 60 + 39, 1: 13 * 60 + 49, 2: 15 * 60 + 59}
    now_ny = pd.Timestamp.now(tz=NEW_YORK)
    keep_indices = []
    for idx, row in output.iterrows():
        session_val = row["session"]
        block_val = int(row["block"])
        session_ts = pd.Timestamp(session_val).tz_localize(NEW_YORK)
        block_end = session_ts + pd.Timedelta(minutes=block_end_minutes[block_val])
        if now_ny >= block_end:
            keep_indices.append(idx)
    output = output.loc[keep_indices].reset_index(drop=True)
    return output[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna().reset_index(drop=True)


def download_bars(client, symbol: str, start: datetime, end: datetime, timeframe, feed, adjustment, rth_only: bool) -> pd.DataFrame:
    _, _, _, StockBarsRequest, _, _ = _load_alpaca()
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        adjustment=adjustment,
        feed=feed,
    )
    response = client.get_stock_bars(request)
    return normalize_bars(response.df, symbol, rth_only=rth_only)


def download_bars_batch(
    client,
    symbols: list[str],
    start: datetime,
    end: datetime,
    timeframe,
    feed,
    adjustment,
) -> dict[str, pd.DataFrame]:
    """Fetch a bounded symbol batch in one provider request."""
    if not symbols or len(symbols) > 30 or len(symbols) != len(set(symbols)):
        raise ValueError("GAP_RECOVERY_PROVIDER_BATCH_INVALID")
    _, _, _, StockBarsRequest, _, _ = _load_alpaca()
    request = StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=timeframe,
        start=start,
        end=end,
        adjustment=adjustment,
        feed=feed,
    )
    response = client.get_stock_bars(request)
    return {
        symbol: normalize_bars(response.df, symbol, rth_only=False)
        for symbol in symbols
    }


def _session_index(values) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(values)).tz_localize(None).normalize()


def _daily_sessions(frame: pd.DataFrame) -> pd.DatetimeIndex:
    if frame.empty or "Date" not in frame.columns:
        return pd.DatetimeIndex([])
    dates = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()
    return pd.DatetimeIndex(dates.dt.tz_convert(NEW_YORK).dt.tz_localize(None).dt.normalize().unique())


def filter_internal_daily_gaps(
    present_sessions: set[pd.Timestamp],
    expected_sessions: pd.DatetimeIndex,
    first_session: pd.Timestamp,
    last_session: pd.Timestamp,
) -> pd.DatetimeIndex:
    """Return only listing-interior schedule sessions absent from frozen daily rows."""
    expected = _session_index(expected_sessions)
    present = {_session_index([value])[0] for value in present_sessions}
    return pd.DatetimeIndex([session for session in expected if first_session <= session <= last_session and session not in present])


def _load_gap_recovery_frozen_context(frozen_root: Path, audit_run: str) -> tuple[dict, dict[str, dict], list[str]]:
    frozen_root = Path(frozen_root)
    manifest_path = frozen_root / "manifests" / audit_run / "acquisition_manifest.json"
    if not manifest_path.exists():
        raise ValueError("GAP_RECOVERY_FROZEN_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline_path = BASE_DIR / "baselines" / "archive" / "rs_m126_top30_sma100_no_regime_v2_clean_stock.178symbols.20260718.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidates = baseline["universe"]["candidate_symbols"]
    required = {
        "acquisition_run_id": GAP_RECOVERY_AUDIT_RUN,
        "dataset_profile": "rs_v2_clean",
        "feed": "iex",
        "adjustment": "all",
        "candidate_count": 178,
        "candidate_universe_hash": baseline["universe"]["candidate_universe_hash"],
    }
    if audit_run != GAP_RECOVERY_AUDIT_RUN or any(manifest.get(key) != value for key, value in required.items()):
        raise ValueError("GAP_RECOVERY_FROZEN_CONTRACT_MISMATCH")
    records = {row.get("symbol"): row for row in manifest.get("files", []) if row.get("timeframe") == "1D"}
    if set(records) != set(candidates) | {"QQQ", "SPY"}:
        raise ValueError("GAP_RECOVERY_FROZEN_DAILY_RECORDS_MISMATCH")
    return manifest, records, candidates


def build_gap_recovery_targets(frozen_root: Path, audit_run: str, market_schedule: pd.DataFrame | None = None) -> tuple[list[dict], dict]:
    """Derive exact missing candidate/session pairs from frozen daily files and NYSE schedule."""
    frozen_root = Path(frozen_root)
    manifest, records, candidates = _load_gap_recovery_frozen_context(frozen_root, audit_run)
    schedule = approved_nyse_schedule(manifest["target_start_date"], manifest["target_end_date"]) if market_schedule is None else market_schedule
    expected = _session_index(schedule.index)
    targets = []
    for symbol in candidates:
        record = records[symbol]
        daily_path = frozen_root / record["output_relative_path"]
        present = set(_daily_sessions(pd.read_csv(daily_path)))
        first = pd.Timestamp(record.get("first_market_session", ""))
        last = pd.Timestamp(record.get("last_market_session", ""))
        if pd.isna(first) or pd.isna(last) or first > last:
            raise ValueError("GAP_RECOVERY_INVALID_LISTING_BOUNDARY")
        gaps = filter_internal_daily_gaps(present, expected, first.normalize(), last.normalize())
        for session in gaps:
            targets.append({
                "symbol": symbol,
                "target_session": str(session.date()),
                "asset_role": "CANDIDATE",
                "frozen_daily_relative_path": record["output_relative_path"],
            })
    targets.sort(key=lambda row: (row["target_session"], row["symbol"]))
    target_dates = {row["target_session"] for row in targets}
    target_symbols = {row["symbol"] for row in targets}
    pairs = {(row["symbol"], row["target_session"]) for row in targets}
    if (
        len(targets) != GAP_RECOVERY_EXPECTED_PAIR_COUNT
        or len(pairs) != GAP_RECOVERY_EXPECTED_PAIR_COUNT
        or len(target_symbols) != GAP_RECOVERY_EXPECTED_SYMBOL_COUNT
        or target_dates != set(GAP_RECOVERY_APPROVED_DATES)
        or {"QQQ", "SPY"} & target_symbols
    ):
        raise ValueError("GAP_RECOVERY_TARGET_CONTRACT_MISMATCH")
    return targets, {"manifest": manifest, "schedule": schedule, "candidates": candidates}


def resolve_gap_recovery_run_paths(frozen_root: Path, staging_root: Path, recovery_run_id: str) -> GapRecoveryRunPaths:
    frozen = Path(frozen_root).resolve()
    staging = Path(staging_root).resolve()
    try:
        staging.relative_to(frozen)
    except ValueError:
        pass
    else:
        raise ValueError("GAP_RECOVERY_STAGING_OVERLAPS_FROZEN")
    if not recovery_run_id.startswith("gaprec_"):
        raise ValueError("GAP_RECOVERY_RUN_ID_INVALID")
    run_root = staging / "runs" / recovery_run_id
    return GapRecoveryRunPaths(run_root, run_root / "daily", run_root / "raw_5min", run_root / "manifests", run_root / "reports")


def validate_gap_recovery_invocation(
    *,
    dataset_profile: str | None,
    feed: str | None,
    adjustment: str | None,
    audit_run: str | None,
    frozen_root: Path,
    staging_root: Path,
    targets: list[dict],
) -> None:
    if dataset_profile != "rs_v2_clean" or feed != "iex" or adjustment != "all" or audit_run != GAP_RECOVERY_AUDIT_RUN:
        raise ValueError("GAP_RECOVERY_INVOCATION_CONTRACT_MISMATCH")
    resolve_gap_recovery_run_paths(frozen_root, staging_root, "gaprec_contract")
    pairs = {(row.get("symbol"), row.get("target_session")) for row in targets}
    if (
        len(targets) != GAP_RECOVERY_EXPECTED_PAIR_COUNT
        or len(pairs) != GAP_RECOVERY_EXPECTED_PAIR_COUNT
        or {row.get("target_session") for row in targets} != set(GAP_RECOVERY_APPROVED_DATES)
        or {"QQQ", "SPY"} & {row.get("symbol") for row in targets}
    ):
        raise ValueError("GAP_RECOVERY_TARGET_CONTRACT_MISMATCH")


def _valid_ohlcv(frame: pd.DataFrame) -> bool:
    if frame.empty or not set(GAP_RECOVERY_REQUIRED_COLUMNS).issubset(frame.columns):
        return False
    numeric = frame[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    prices = numeric[["Open", "High", "Low", "Close"]]
    return bool(
        np.isfinite(numeric.to_numpy(dtype=float)).all()
        and (prices > 0).all().all()
        and (numeric["Volume"] >= 0).all()
        and (numeric["High"] >= numeric[["Open", "Close"]].max(axis=1)).all()
        and (numeric["Low"] <= numeric[["Open", "Close"]].min(axis=1)).all()
        and (numeric["High"] >= numeric["Low"]).all()
    )


def validate_daily_recovery(provider_frame: pd.DataFrame, target_session: str) -> tuple[str, pd.DataFrame]:
    if provider_frame.empty:
        return "DAILY_EMPTY_RESPONSE", pd.DataFrame(columns=GAP_RECOVERY_REQUIRED_COLUMNS)
    frame = provider_frame.copy()
    dates = pd.to_datetime(frame.get("Date"), errors="coerce", utc=True)
    # Alpaca daily bars identify the market session by their UTC calendar date.
    # Normalization may display UTC midnight as the prior New York evening, so
    # converting that display value back to UTC is required before date matching.
    sessions = dates.dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    target = pd.Timestamp(target_session).normalize()
    staged = frame.loc[sessions.eq(target)].copy()
    if staged.empty:
        return "DAILY_WRONG_SESSION", pd.DataFrame(columns=GAP_RECOVERY_REQUIRED_COLUMNS)
    if len(staged) != 1:
        return "DAILY_DUPLICATE_SESSION", pd.DataFrame(columns=GAP_RECOVERY_REQUIRED_COLUMNS)
    if pd.isna(dates.loc[staged.index[0]]) or not _valid_ohlcv(staged):
        return "DAILY_INVALID_OHLCV", staged[GAP_RECOVERY_REQUIRED_COLUMNS]
    return "DAILY_RECOVERED_VALID", staged[GAP_RECOVERY_REQUIRED_COLUMNS]


def validate_raw_recovery(
    provider_frame: pd.DataFrame,
    target_session: str,
    market_open: pd.Timestamp,
    market_close: pd.Timestamp,
) -> tuple[str, pd.DataFrame]:
    if provider_frame.empty:
        return "RAW_EMPTY_RESPONSE", pd.DataFrame(columns=GAP_RECOVERY_REQUIRED_COLUMNS)
    frame = provider_frame.copy()
    dates = pd.to_datetime(frame.get("Date"), errors="coerce", utc=True)
    valid_dates = dates.notna()
    frame = frame.loc[valid_dates].copy()
    frame["Date"] = dates.loc[valid_dates]
    target = pd.Timestamp(target_session).normalize()
    local_sessions = frame["Date"].dt.tz_convert(NEW_YORK).dt.tz_localize(None).dt.normalize()
    staged = frame.loc[local_sessions.eq(target) & frame["Date"].ge(pd.Timestamp(market_open).tz_convert("UTC")) & frame["Date"].lt(pd.Timestamp(market_close).tz_convert("UTC"))].copy()
    staged = staged.sort_values("Date")
    if staged.empty:
        return "RAW_EMPTY_RESPONSE", pd.DataFrame(columns=GAP_RECOVERY_REQUIRED_COLUMNS)
    if staged["Date"].duplicated().any() or not staged["Date"].is_monotonic_increasing:
        return "RAW_INVALID_TIMESTAMPS", staged[GAP_RECOVERY_REQUIRED_COLUMNS]
    if not _valid_ohlcv(staged):
        return "RAW_INVALID_OHLCV", staged[GAP_RECOVERY_REQUIRED_COLUMNS]
    # Completeness policy: at least one valid, unique RTH trade bar; no fabricated empty intervals required.
    return "RAW_RECOVERED_COMPLETE", staged[GAP_RECOVERY_REQUIRED_COLUMNS]


def _gap_recovery_windows(session: str, schedule: pd.DataFrame) -> dict:
    session_key = pd.Timestamp(session).normalize()
    row = schedule.loc[session_key]
    if isinstance(row, pd.DataFrame):
        raise ValueError("GAP_RECOVERY_DUPLICATE_SCHEDULE_SESSION")
    local_midnight = session_key.tz_localize(NEW_YORK)
    return {
        "daily_start": local_midnight - pd.Timedelta(days=1),
        "daily_end": local_midnight + pd.Timedelta(days=2),
        "intraday_start": pd.Timestamp(row["market_open"]).tz_convert(NEW_YORK),
        "intraday_end": pd.Timestamp(row["market_close"]).tz_convert(NEW_YORK),
        "market_open": pd.Timestamp(row["market_open"]).tz_convert(NEW_YORK),
        "market_close": pd.Timestamp(row["market_close"]).tz_convert(NEW_YORK),
    }


def _relative_difference(left: float, right: float) -> float | None:
    if not np.isfinite(left) or not np.isfinite(right) or left == 0:
        return None
    return abs(right - left) / abs(left)


def _daily_raw_comparison(daily: pd.DataFrame, raw: pd.DataFrame) -> dict:
    empty = {f"{field.lower()}_relative_difference": None for field in ["Open", "High", "Low", "Close", "Volume"]}
    if len(daily) != 1 or raw.empty:
        return {**empty, "adjustment_equivalence_status": "NOT_COMPARABLE"}
    derived = {
        "Open": float(raw.iloc[0]["Open"]), "High": float(raw["High"].max()), "Low": float(raw["Low"].min()),
        "Close": float(raw.iloc[-1]["Close"]), "Volume": float(raw["Volume"].sum()),
    }
    diffs = {f"{field.lower()}_relative_difference": _relative_difference(float(daily.iloc[0][field]), derived[field]) for field in derived}
    return {**diffs, "adjustment_equivalence_status": "MATCH" if all(value == 0 for value in diffs.values()) else "DIFFERENT"}


def _frozen_hashes(frozen_root: Path) -> dict[str, str]:
    root = Path(frozen_root)
    paths = [path for path in root.rglob("*") if path.is_file()]
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def frozen_snapshot_digest(hashes: dict[str, str]) -> str:
    payload = "\n".join(
        f"{path}|{hashes[path]}" for path in sorted(hashes)
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _outcome_class(daily_status: str, raw_status: str) -> str:
    if daily_status == "DAILY_REQUEST_FAILED" or raw_status == "RAW_REQUEST_FAILED":
        return "REQUEST_FAILED_REQUIRES_RETRY"
    invalid = {"DAILY_WRONG_SESSION", "DAILY_DUPLICATE_SESSION", "DAILY_INVALID_OHLCV", "RAW_INVALID_TIMESTAMPS", "RAW_INVALID_OHLCV"}
    if daily_status in invalid or raw_status in invalid:
        return "INVALID_RECOVERY_EVIDENCE"
    daily_ok = daily_status == "DAILY_RECOVERED_VALID"
    raw_ok = raw_status == "RAW_RECOVERED_COMPLETE"
    if daily_ok and raw_ok:
        return "RECOVERED_DAILY_AND_RAW"
    if daily_ok:
        return "RECOVERED_DAILY_ONLY"
    if raw_ok:
        return "RECOVERED_RAW_ONLY"
    return "RECOVERED_NEITHER"


def resolve_worker_count(value: str | int, default_auto: int, upper_bound: int | None = None) -> int:
    if str(value).strip().lower() == "auto":
        resolved = default_auto
    else:
        resolved = int(value)
    if upper_bound is not None:
        resolved = min(resolved, upper_bound)
    return max(1, resolved)


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temp_path, index=False)
    os.replace(temp_path, path)


def read_existing_bars(path: Path) -> pd.DataFrame:
    if not path.exists() or row_count(path) == 0:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    frame = pd.read_csv(path)
    if "Date" not in frame.columns:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    return frame


def latest_timestamp(frame: pd.DataFrame) -> pd.Timestamp | None:
    if frame.empty or "Date" not in frame.columns:
        return None
    dates = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()
    if dates.empty:
        return None
    return dates.max()


def merge_bars(existing: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
    frames = [frame for frame in [existing, new_rows] if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame(columns=columns)
    merged = pd.concat(frames, ignore_index=True)
    for column in columns:
        if column not in merged.columns:
            merged[column] = pd.NA
    merged["Date"] = pd.to_datetime(merged["Date"], errors="coerce", utc=True)
    merged = merged.dropna(subset=["Date"])
    merged = merged.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
    return merged[columns].reset_index(drop=True)


def download_bars_with_retry(
    api_key: str,
    secret_key: str,
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe,
    feed,
    adjustment,
    rth_only: bool,
    attempts: int = 3,
) -> DownloadBarsResult:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last_error = None
    failed_attempts = 0
    for attempt in range(1, attempts + 1):
        try:
            _, _, StockHistoricalDataClient, _, _, _ = _load_alpaca()
            client = StockHistoricalDataClient(api_key, secret_key)
            frame = download_bars(client, symbol, start, end, timeframe, feed, adjustment, rth_only=rth_only)
            return DownloadBarsResult(frame, attempt, attempt - 1, failed_attempts)
        except Exception as exc:
            last_error = exc
            failed_attempts += 1
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise DownloadBarsRetryError(last_error, attempts, failed_attempts) from last_error


def _write_gap_recovery_reports(detail_rows: list[dict], latest_dir: Path, run_reports_dir: Path, summary: dict) -> dict[str, Path]:
    columns = [
        "recovery_run_id", "symbol", "target_session", "asset_role", "daily_request_start", "daily_request_end",
        "intraday_request_start", "intraday_request_end", "feed", "adjustment", "daily_provider_row_count",
        "exact_target_daily_row_count", "raw_provider_row_count", "exact_target_rth_raw_row_count",
        "first_target_raw_timestamp", "last_target_raw_timestamp", "expected_market_open", "expected_market_close",
        "daily_recovered", "raw_session_recovered", "raw_session_complete", "daily_ohlcv_integrity_status",
        "raw_ohlcv_integrity_status", "provider_attempt_count", "retry_attempt_count", "error_type", "error_message",
        "outcome_class", "open_relative_difference", "high_relative_difference", "low_relative_difference",
        "close_relative_difference", "volume_relative_difference", "adjustment_equivalence_status",
    ]
    detail = pd.DataFrame(detail_rows, columns=columns)
    by_date = detail.groupby(["target_session", "outcome_class"], dropna=False).size().reset_index(name="pair_count") if not detail.empty else pd.DataFrame(columns=["target_session", "outcome_class", "pair_count"])
    by_symbol = detail.groupby(["symbol", "outcome_class"], dropna=False).size().reset_index(name="pair_count") if not detail.empty else pd.DataFrame(columns=["symbol", "outcome_class", "pair_count"])
    latest_dir = Path(latest_dir)
    run_reports_dir = Path(run_reports_dir)
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_reports_dir.mkdir(parents=True, exist_ok=True)
    names = {
        "detail": "rs_v2_clean_gap_recovery_probe_detail.csv",
        "by_date": "rs_v2_clean_gap_recovery_probe_by_date.csv",
        "by_symbol": "rs_v2_clean_gap_recovery_probe_by_symbol.csv",
        "summary": "rs_v2_clean_gap_recovery_probe_summary.md",
    }
    for folder in (latest_dir, run_reports_dir):
        atomic_write_csv(detail, folder / names["detail"])
        atomic_write_csv(by_date, folder / names["by_date"])
        atomic_write_csv(by_symbol, folder / names["by_symbol"])
    lines = ["# RS V2 Clean Gap Recovery Probe", ""]
    lines.extend(f"- {key}: {value}" for key, value in summary.items())
    lines.extend([
        "- raw_session_completeness_rule: at least one valid, unique, monotonic RTH bar in [market_open, market_close); no theoretical 5-minute empty intervals required.",
        "- production_merge_performed: false",
        "- strategy_metrics_calculated: false",
        "",
    ])
    text = "\n".join(lines)
    for folder in (latest_dir, run_reports_dir):
        path = folder / names["summary"]
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    return {key: latest_dir / value for key, value in names.items()}


def _request_gap_recovery_bars(api_key: str, secret_key: str, symbol: str, start: datetime, end: datetime, timeframe, feed, adjustment) -> tuple[pd.DataFrame, int, int, str, str]:
    try:
        result = download_bars_with_retry(api_key, secret_key, symbol, start, end, timeframe, feed, adjustment, False)
        return result.frame, result.provider_attempt_count, result.retry_attempt_count, "", ""
    except DownloadBarsRetryError as exc:
        return pd.DataFrame(columns=GAP_RECOVERY_REQUIRED_COLUMNS), exc.provider_attempt_count, exc.retry_attempt_count, type(exc.__cause__).__name__, str(exc)
    except Exception as exc:
        return pd.DataFrame(columns=GAP_RECOVERY_REQUIRED_COLUMNS), 1, 0, type(exc).__name__, str(exc)


def run_gap_recovery_probe(
    *,
    frozen_root: Path,
    staging_root: Path,
    audit_run: str,
    dataset_profile: str,
    feed_name: str,
    adjustment_name: str,
    latest_report_dir: Path,
) -> dict:
    """Read-only IEX recoverability probe. Never writes frozen dataset paths or latest pointer."""
    targets, context = build_gap_recovery_targets(frozen_root, audit_run)
    validate_gap_recovery_invocation(
        dataset_profile=dataset_profile, feed=feed_name, adjustment=adjustment_name, audit_run=audit_run,
        frozen_root=frozen_root, staging_root=staging_root, targets=targets,
    )
    run_id = f"gaprec_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    paths = resolve_gap_recovery_run_paths(frozen_root, staging_root, run_id)
    for directory in (paths.daily, paths.raw_5min, paths.manifests, paths.reports):
        directory.mkdir(parents=True, exist_ok=True)
    frozen_before = _frozen_hashes(frozen_root)
    immutable_path = Path(frozen_root) / "manifests" / audit_run / "acquisition_manifest.json"
    latest_pointer = Path(frozen_root) / "manifests" / "latest_manifest.json"
    immutable_before = hashlib.sha256(immutable_path.read_bytes()).hexdigest()
    latest_before = hashlib.sha256(latest_pointer.read_bytes()).hexdigest() if latest_pointer.exists() else "MISSING"
    manifest_path = paths.manifests / "recovery_manifest.json"
    manifest = {
        "recovery_run_id": run_id, "status": "IN_PROGRESS", "dataset_profile": dataset_profile,
        "frozen_audit_run": audit_run, "feed": feed_name, "adjustment": adjustment_name,
        "target_pair_count": len(targets), "target_symbol_count": len({row["symbol"] for row in targets}),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _atomic_write_json(manifest, manifest_path)
    try:
        api_key, secret_key = get_credentials()
    except Exception as exc:
        summary = {
            "probe_status": "BLOCKED_CREDENTIALS", "exact_target_pair_count": len(targets), "unique_target_symbol_count": len({row["symbol"] for row in targets}),
            "target_dates": ",".join(GAP_RECOVERY_APPROVED_DATES), "daily_valid_recovery_count": 0, "raw_complete_recovery_count": 0,
            "daily_and_raw_recovery_count": 0, "daily_only_count": 0, "raw_only_count": 0, "neither_count": 0,
            "request_failed_count": 0, "invalid_evidence_count": 0, "provider_attempt_count": 0, "retry_attempt_count": 0,
            "network_request_count": 0, "frozen_dataset_mutation_count": 0, "immutable_manifest_changed": "false",
            "latest_pointer_changed": "false", "approved_exception_count": 0, "error_type": type(exc).__name__,
        }
        _write_gap_recovery_reports([], latest_report_dir, paths.reports, summary)
        _atomic_write_json({**manifest, "status": "FAILED_PROBE", "failure_type": "BLOCKED_CREDENTIALS", "failure_message": str(exc)}, manifest_path)
        return {"status": "BLOCKED_CREDENTIALS", "summary": summary, "paths": paths}

    _, _, _, _, TimeFrame, TimeFrameUnit = _load_alpaca()
    feed = get_data_feed(feed_name)
    adjustment = get_adjustment(adjustment_name)
    day_tf = TimeFrame.Day if hasattr(TimeFrame, "Day") else TimeFrame(1, TimeFrameUnit.Day)
    min5_tf = TimeFrame(5, TimeFrameUnit.Minute)
    detail_rows = []
    provider_attempts = retries = 0
    for target in targets:
        windows = _gap_recovery_windows(target["target_session"], context["schedule"])
        daily, daily_attempts, daily_retries, daily_error_type, daily_error_message = _request_gap_recovery_bars(
            api_key, secret_key, target["symbol"], windows["daily_start"].to_pydatetime(), windows["daily_end"].to_pydatetime(), day_tf, feed, adjustment,
        )
        raw, raw_attempts, raw_retries, raw_error_type, raw_error_message = _request_gap_recovery_bars(
            api_key, secret_key, target["symbol"], windows["intraday_start"].to_pydatetime(), windows["intraday_end"].to_pydatetime(), min5_tf, feed, adjustment,
        )
        provider_attempts += daily_attempts + raw_attempts
        retries += daily_retries + raw_retries
        daily_status, staged_daily = ("DAILY_REQUEST_FAILED", pd.DataFrame(columns=GAP_RECOVERY_REQUIRED_COLUMNS)) if daily_error_type else validate_daily_recovery(daily, target["target_session"])
        raw_status, staged_raw = ("RAW_REQUEST_FAILED", pd.DataFrame(columns=GAP_RECOVERY_REQUIRED_COLUMNS)) if raw_error_type else validate_raw_recovery(raw, target["target_session"], windows["market_open"], windows["market_close"])
        if not staged_daily.empty:
            atomic_write_csv(staged_daily, paths.daily / f"{target['symbol']}_{target['target_session']}_1D.csv")
        if not staged_raw.empty:
            atomic_write_csv(staged_raw, paths.raw_5min / f"{target['symbol']}_{target['target_session']}_5min.csv")
        comparison = _daily_raw_comparison(staged_daily if daily_status == "DAILY_RECOVERED_VALID" else pd.DataFrame(), staged_raw if raw_status == "RAW_RECOVERED_COMPLETE" else pd.DataFrame())
        error_type = ";".join(value for value in (daily_error_type, raw_error_type) if value)
        error_message = "; ".join(value for value in (daily_error_message, raw_error_message) if value)
        detail_rows.append({
            "recovery_run_id": run_id, **target,
            "daily_request_start": windows["daily_start"].isoformat(), "daily_request_end": windows["daily_end"].isoformat(),
            "intraday_request_start": windows["intraday_start"].isoformat(), "intraday_request_end": windows["intraday_end"].isoformat(),
            "feed": feed_name, "adjustment": adjustment_name, "daily_provider_row_count": len(daily),
            "exact_target_daily_row_count": len(staged_daily), "raw_provider_row_count": len(raw), "exact_target_rth_raw_row_count": len(staged_raw),
            "first_target_raw_timestamp": "" if staged_raw.empty else str(staged_raw.iloc[0]["Date"]),
            "last_target_raw_timestamp": "" if staged_raw.empty else str(staged_raw.iloc[-1]["Date"]),
            "expected_market_open": windows["market_open"].isoformat(), "expected_market_close": windows["market_close"].isoformat(),
            "daily_recovered": daily_status == "DAILY_RECOVERED_VALID", "raw_session_recovered": raw_status in {"RAW_RECOVERED_COMPLETE", "RAW_RECOVERED_PARTIAL"},
            "raw_session_complete": raw_status == "RAW_RECOVERED_COMPLETE", "daily_ohlcv_integrity_status": daily_status,
            "raw_ohlcv_integrity_status": raw_status, "provider_attempt_count": daily_attempts + raw_attempts,
            "retry_attempt_count": daily_retries + raw_retries, "error_type": error_type, "error_message": error_message,
            "outcome_class": _outcome_class(daily_status, raw_status), **comparison,
        })
    outcomes = pd.Series([row["outcome_class"] for row in detail_rows]).value_counts().to_dict()
    frozen_after = _frozen_hashes(frozen_root)
    immutable_after = hashlib.sha256(immutable_path.read_bytes()).hexdigest()
    latest_after = hashlib.sha256(latest_pointer.read_bytes()).hexdigest() if latest_pointer.exists() else "MISSING"
    summary = {
        "probe_status": "COMPLETE_PROBE", "exact_target_pair_count": len(targets), "unique_target_symbol_count": len({row["symbol"] for row in targets}),
        "target_dates": ",".join(GAP_RECOVERY_APPROVED_DATES), "daily_valid_recovery_count": sum(row["daily_recovered"] for row in detail_rows),
        "raw_complete_recovery_count": sum(row["raw_session_complete"] for row in detail_rows),
        "daily_and_raw_recovery_count": outcomes.get("RECOVERED_DAILY_AND_RAW", 0), "daily_only_count": outcomes.get("RECOVERED_DAILY_ONLY", 0),
        "raw_only_count": outcomes.get("RECOVERED_RAW_ONLY", 0), "neither_count": outcomes.get("RECOVERED_NEITHER", 0),
        "request_failed_count": outcomes.get("REQUEST_FAILED_REQUIRES_RETRY", 0), "invalid_evidence_count": outcomes.get("INVALID_RECOVERY_EVIDENCE", 0),
        "provider_attempt_count": provider_attempts, "retry_attempt_count": retries, "network_request_count": provider_attempts,
        "frozen_dataset_mutation_count": sum(frozen_before.get(key) != frozen_after.get(key) for key in set(frozen_before) | set(frozen_after)),
        "immutable_manifest_changed": str(immutable_before != immutable_after).lower(), "latest_pointer_changed": str(latest_before != latest_after).lower(),
        "approved_exception_count": 0,
    }
    report_paths = _write_gap_recovery_reports(detail_rows, latest_report_dir, paths.reports, summary)
    _atomic_write_json({**manifest, "status": "COMPLETE_PROBE", "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "summary": summary, "records": detail_rows}, manifest_path)
    return {"status": "COMPLETE_PROBE", "summary": summary, "paths": paths, "report_paths": report_paths}


def refresh_file(
    path: Path,
    new_rows: pd.DataFrame,
    refresh_latest: bool,
    overwrite: bool,
    symbol: str,
    file_type: str,
    allow_row_count_reduction: bool = False,
) -> tuple[str, int, int, str]:
    old_rows = row_count(path)
    existing = read_existing_bars(path) if path.exists() and not overwrite else pd.DataFrame()
    if overwrite:
        final = merge_bars(pd.DataFrame(), new_rows)
        status = "downloaded"
    elif refresh_latest:
        final = merge_bars(existing, new_rows)
        status = "refreshed"
    else:
        final = merge_bars(pd.DataFrame(), new_rows)
        status = "downloaded"
    new_rows_added = max(len(final) - old_rows, 0)
    warning = ""
    if old_rows and len(final) < old_rows:
        action = "overwrite" if allow_row_count_reduction else "keep_old_file"
        reason = "merged_row_count_lower_than_existing"
        warning = f"symbol={symbol},file_type={file_type},old_rows={old_rows},new_rows={len(final)},action={action},reason={reason}"
        if not allow_row_count_reduction:
            return "skipped", len(new_rows), 0, warning
    atomic_write_csv(final, path)
    return status, len(new_rows), new_rows_added, warning


def _download_symbol_worker(payload: dict) -> dict:
    symbol = payload["symbol"]
    daily_path = Path(payload["daily_path"])
    raw_path = Path(payload["raw_path"])
    refresh_latest = payload["refresh_latest"]
    overwrite = payload["overwrite"]
    skip_download = payload["skip_download"]
    allow_row_count_reduction = payload.get("allow_row_count_reduction", False)
    start_time = time.perf_counter()
    result = {
        "symbol": symbol,
        "daily_status": "skipped",
        "raw_5min_status": "skipped",
        "daily_provider_rows": 0,
        "raw_provider_rows": 0,
        "new_rows_added": 0,
        "current_session_received": 0,
        "current_session_added": 0,
        "provider_attempt_count": 0,
        "retry_attempt_count": 0,
        "failed_provider_attempt_count": 0,
        "retry_telemetry_status": "MEASURED",
        "warnings": [],
        "error": "",
        "runtime_sec": 0.0,
    }
    try:
        if skip_download:
            if payload.get("refresh_daily"):
                result["provider_attempt_count"] = 2
            return result

        if payload.get("refresh_daily"):
            api_key = payload["api_key"]
            secret_key = payload["secret_key"]
            feed = payload["feed"]
            adjustment = payload["adjustment"]
            day_tf = payload["day_tf"]
            min5_tf = payload["min5_tf"]
            daily_end = payload["daily_end"]
            intraday_end = payload["intraday_end"]
            drift_check_start = payload["drift_check_start"]
            daily_start = payload["daily_start"]
            intraday_start = payload["intraday_start"]
            
            existing_daily = read_existing_bars(daily_path) if daily_path.exists() else pd.DataFrame()
            existing_raw = read_existing_bars(raw_path) if raw_path.exists() else pd.DataFrame()
            
            drift_detected = False
            if not existing_daily.empty and drift_check_start is not None:
                drift_result = download_bars_with_retry(api_key, secret_key, symbol, drift_check_start, daily_end, day_tf, feed, adjustment, False)
                result["provider_attempt_count"] += drift_result.provider_attempt_count
                result["retry_attempt_count"] += drift_result.retry_attempt_count
                result["failed_provider_attempt_count"] += drift_result.failed_provider_attempt_count
                
                existing_dates = pd.to_datetime(existing_daily["Date"], utc=True)
                new_dates = pd.to_datetime(drift_result.frame["Date"], utc=True)
                common_dates = set(existing_dates).intersection(set(new_dates))
                
                for dt in common_dates:
                    row_existing = existing_daily[existing_dates == dt].iloc[0]
                    row_new = drift_result.frame[new_dates == dt].iloc[0]
                    for col in ["Open", "High", "Low", "Close"]:
                        val_existing = float(row_existing[col])
                        val_new = float(row_new[col])
                        if val_new > 0:
                            rel_diff = abs(val_existing - val_new) / val_new
                            if rel_diff > 0.005:
                                drift_detected = True
                                break
                    if drift_detected:
                        break
            
            if drift_detected or existing_daily.empty:
                if drift_detected:
                    print(f"[ADJUSTMENT_DRIFT] {symbol} drift detected! Performing full history re-acquisition.", flush=True)
                daily_result = download_bars_with_retry(api_key, secret_key, symbol, daily_start, daily_end, day_tf, feed, adjustment, False)
                result["provider_attempt_count"] += daily_result.provider_attempt_count
                result["retry_attempt_count"] += daily_result.retry_attempt_count
                result["failed_provider_attempt_count"] += daily_result.failed_provider_attempt_count
                daily_frame = daily_result.frame
                status_d, prov_rows_d, added_d, warning_d = refresh_file(daily_path, daily_frame, False, True, symbol, "daily", allow_row_count_reduction)
                
                raw_result = download_bars_with_retry(api_key, secret_key, symbol, intraday_start, intraday_end, min5_tf, feed, adjustment, True)
                result["provider_attempt_count"] += raw_result.provider_attempt_count
                result["retry_attempt_count"] += raw_result.retry_attempt_count
                result["failed_provider_attempt_count"] += raw_result.failed_provider_attempt_count
                raw_frame = raw_result.frame
                status_r, prov_rows_r, added_r, warning_r = refresh_file(raw_path, raw_frame, False, True, symbol, "raw_5min", allow_row_count_reduction)
            else:
                latest_daily_ts = latest_timestamp(existing_daily)
                if latest_daily_ts is not None:
                    daily_query_start = (latest_daily_ts + pd.Timedelta(days=1)).to_pydatetime()
                else:
                    daily_query_start = daily_start
                
                if pd.Timestamp(daily_query_start).tz_localize(None).normalize() <= pd.Timestamp(daily_end).tz_localize(None).normalize():
                    daily_result = download_bars_with_retry(api_key, secret_key, symbol, daily_query_start, daily_end, day_tf, feed, adjustment, False)
                    result["provider_attempt_count"] += daily_result.provider_attempt_count
                    result["retry_attempt_count"] += daily_result.retry_attempt_count
                    result["failed_provider_attempt_count"] += daily_result.failed_provider_attempt_count
                    daily_frame = daily_result.frame
                else:
                    daily_frame = pd.DataFrame()
                
                status_d, prov_rows_d, added_d, warning_d = refresh_file(daily_path, daily_frame, True, False, symbol, "daily", allow_row_count_reduction)
                
                latest_raw_ts = latest_timestamp(existing_raw)
                if latest_raw_ts is not None:
                    intraday_query_start = (latest_raw_ts + pd.Timedelta(minutes=1)).to_pydatetime()
                else:
                    intraday_query_start = intraday_start
                
                if intraday_query_start <= intraday_end:
                    raw_result = download_bars_with_retry(api_key, secret_key, symbol, intraday_query_start, intraday_end, min5_tf, feed, adjustment, True)
                    result["provider_attempt_count"] += raw_result.provider_attempt_count
                    result["retry_attempt_count"] += raw_result.retry_attempt_count
                    result["failed_provider_attempt_count"] += raw_result.failed_provider_attempt_count
                    raw_frame = raw_result.frame
                else:
                    raw_frame = pd.DataFrame()
                
                status_r, prov_rows_r, added_r, warning_r = refresh_file(raw_path, raw_frame, True, False, symbol, "raw_5min", allow_row_count_reduction)
            
            result["daily_status"] = status_d
            result["daily_provider_rows"] = prov_rows_d
            result["new_rows_added"] += added_d
            if warning_d:
                result["warnings"].append(warning_d)
                
            result["raw_5min_status"] = status_r
            result["raw_provider_rows"] = prov_rows_r
            result["new_rows_added"] += added_r
            if warning_r:
                result["warnings"].append(warning_r)
                
            target_session = pd.Timestamp(intraday_end).date().isoformat()
            if not raw_frame.empty:
                dates_str = raw_frame["Date"].astype(str)
                result["current_session_received"] = int(dates_str.str.contains(target_session).sum())
            else:
                result["current_session_received"] = 0
            
            final_saved = read_existing_bars(raw_path)
            if not final_saved.empty:
                dates_str = final_saved["Date"].astype(str)
                result["current_session_added"] = int(dates_str.str.contains(target_session).sum())
            else:
                result["current_session_added"] = 0
                
            return result

        api_key = payload["api_key"]
        secret_key = payload["secret_key"]
        feed = payload["feed"]
        adjustment = payload["adjustment"]
        day_tf = payload["day_tf"]
        min5_tf = payload["min5_tf"]
        daily_end = payload["daily_end"]
        intraday_end = payload["intraday_end"]

        if daily_path.exists() and not overwrite and not refresh_latest:
            result["daily_status"] = "skipped"
        else:
            existing = read_existing_bars(daily_path)
            latest = latest_timestamp(existing)
            daily_start = payload["daily_start"]
            if refresh_latest and latest is not None and not overwrite:
                daily_start = max(payload["daily_start"], (latest - pd.Timedelta(days=10)).to_pydatetime())
            daily_result = download_bars_with_retry(api_key, secret_key, symbol, daily_start, daily_end, day_tf, feed, adjustment, False)
            result["provider_attempt_count"] += daily_result.provider_attempt_count
            result["retry_attempt_count"] += daily_result.retry_attempt_count
            result["failed_provider_attempt_count"] += daily_result.failed_provider_attempt_count
            daily = daily_result.frame
            status, provider_rows, added, warning = refresh_file(daily_path, daily, refresh_latest, overwrite, symbol, "daily", allow_row_count_reduction)
            result["daily_status"] = status
            result["daily_provider_rows"] = provider_rows
            result["new_rows_added"] += added
            if warning:
                result["warnings"].append(warning)

        if payload.get("skip_raw_5min") or (raw_path.exists() and not overwrite and not refresh_latest):
            result["raw_5min_status"] = "skipped"
        else:
            existing = read_existing_bars(raw_path)
            latest = latest_timestamp(existing)
            intraday_start = payload["intraday_start"]
            if refresh_latest and latest is not None and not overwrite:
                intraday_start = max(payload["intraday_start"], (latest - pd.Timedelta(days=5)).to_pydatetime())
            raw_result = download_bars_with_retry(api_key, secret_key, symbol, intraday_start, intraday_end, min5_tf, feed, adjustment, True)
            result["provider_attempt_count"] += raw_result.provider_attempt_count
            result["retry_attempt_count"] += raw_result.retry_attempt_count
            result["failed_provider_attempt_count"] += raw_result.failed_provider_attempt_count
            raw_5min = raw_result.frame
            status, provider_rows, added, warning = refresh_file(raw_path, raw_5min, refresh_latest, overwrite, symbol, "raw_5min", allow_row_count_reduction)
            result["raw_5min_status"] = status
            result["raw_provider_rows"] = provider_rows
            result["new_rows_added"] += added
            target_session = pd.Timestamp(intraday_end).date().isoformat()

            # Calculate current session stats
            if not raw_5min.empty:
                dates_str = raw_5min["Date"].astype(str)
                result["current_session_received"] = int(dates_str.str.contains(target_session).sum())
            else:
                result["current_session_received"] = 0

            # Check how many are in the final saved file
            final_saved = read_existing_bars(raw_path)
            if not final_saved.empty:
                dates_str = final_saved["Date"].astype(str)
                result["current_session_added"] = int(dates_str.str.contains(target_session).sum())
            else:
                result["current_session_added"] = 0

            if warning:
                result["warnings"].append(warning)
    except DownloadBarsRetryError as exc:
        result["provider_attempt_count"] += exc.provider_attempt_count
        result["retry_attempt_count"] += exc.retry_attempt_count
        result["failed_provider_attempt_count"] += exc.failed_provider_attempt_count
        result["error"] = str(exc)
    except Exception as exc:
        result["error"] = str(exc)
        result["retry_telemetry_status"] = "UNAVAILABLE"
    finally:
        result["runtime_sec"] = round(time.perf_counter() - start_time, 4)
    return result


def _aggregate_130min_worker(payload: dict) -> dict:
    symbol = payload["symbol"]
    raw_path = Path(payload["raw_path"])
    bar_path = Path(payload["bar_path"])
    allow_row_count_reduction = payload.get("allow_row_count_reduction", False)
    start_time = time.perf_counter()
    result = {
        "symbol": symbol,
        "bar_130min_status": "skipped",
        "bar_130min_rows": row_count(bar_path),
        "warning": "",
        "error": "",
        "runtime_sec": 0.0,
    }
    try:
        if not raw_path.exists():
            result["error"] = "missing_raw_5min"
            return result
        if bar_path.exists() and not payload["overwrite"] and not payload["rebuild"]:
            return result
        old_rows = row_count(bar_path)
        raw_5min = pd.read_csv(raw_path)
        if payload.get("dataset_profile") == "rs_v2_clean":
            bars_130min = aggregate_to_130min(
                raw_5min,
                payload.get("market_schedule"),
                dataset_end=payload.get("dataset_end"),
            )
        else:
            bars_130min = _aggregate_to_130min_legacy(raw_5min)
        if old_rows and len(bars_130min) < old_rows:
            action = "overwrite" if allow_row_count_reduction else "keep_old_file"
            reason = "aggregated_row_count_lower_than_existing"
            result["warning"] = f"symbol={symbol},file_type=130min,old_rows={old_rows},new_rows={len(bars_130min)},action={action},reason={reason}"
            if not allow_row_count_reduction:
                return result
        atomic_write_csv(bars_130min, bar_path)
        result["bar_130min_status"] = "built"
        result["bar_130min_rows"] = len(bars_130min)
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        result["runtime_sec"] = round(time.perf_counter() - start_time, 4)
    return result


def row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return max(sum(1 for _ in path.open("r", encoding="utf-8")) - 1, 0)
    except UnicodeDecodeError:
        return max(sum(1 for _ in path.open("r")) - 1, 0)


def date_range_summary(path: Path) -> dict[str, str]:
    empty = {"first_date": "", "last_date": ""}
    if not path.exists() or row_count(path) == 0:
        return empty
    try:
        frame = pd.read_csv(path, usecols=["Date"])
    except Exception:
        return empty
    dates = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()
    if dates.empty:
        return empty
    return {
        "first_date": str(dates.min().date()),
        "last_date": str(dates.max().date()),
    }


def v2_coverage_metadata(path: Path, timeframe: str, symbol: str, target_start: str, target_end: str, *, market_schedule=None, reviewed_exceptions=()) -> dict:
    """Load file, then apply shared pure session-based coverage policy."""
    return coverage_metadata_for_file(
        path, timeframe=timeframe, symbol=symbol, target_start=target_start, target_end=target_end,
        market_schedule=market_schedule, reviewed_exceptions=reviewed_exceptions,
    )


def latest_date_in_dir(directory: Path, pattern: str) -> str:
    latest = None
    for path in directory.glob(pattern):
        summary = date_range_summary(path)
        if not summary["last_date"]:
            continue
        candidate = pd.to_datetime(summary["last_date"], errors="coerce")
        if pd.isna(candidate):
            continue
        if latest is None or candidate > latest:
            latest = candidate
    return "" if latest is None else str(latest.date())


def write_data_refresh_reports(
    latest_dir: Path,
    summary: dict,
    records: list[dict],
    timing_rows: list[dict],
) -> None:
    latest_dir.mkdir(parents=True, exist_ok=True)
    csv_path = latest_dir / "data_refresh_report.csv"
    md_path = latest_dir / "data_refresh_report.md"
    timing_path = latest_dir / "data_refresh_timing_report.csv"
    pd.DataFrame([summary]).to_csv(csv_path, index=False)
    pd.DataFrame(timing_rows).to_csv(timing_path, index=False)

    reduction_warnings = []
    for r in records:
        w_str = r.get("warning", "")
        if w_str:
            for w in w_str.split("|"):
                if "old_rows=" in w:
                    parts = {}
                    for p in w.split(","):
                        if "=" in p:
                            k, v = p.split("=", 1)
                            parts[k.strip()] = v.strip()
                    reduction_warnings.append(parts)

    lines = [
        "# Data Refresh Report",
        "",
        f"- refresh_mode: {summary.get('refresh_mode', '')}",
        f"- parallel_enabled: {summary.get('parallel_enabled', False)}",
        f"- workers_resolved: {summary.get('workers_resolved', 1)}",
        f"- download_workers_resolved: {summary.get('download_workers_resolved', 1)}",
        f"- aggregation_workers_resolved: {summary.get('aggregation_workers_resolved', 1)}",
        f"- daily_requested_count: {summary.get('daily_requested_count', 0)}",
        f"- daily_files_updated: {summary.get('daily_files_updated', 0)}",
        f"- raw_5min_requested_count: {summary.get('raw_5min_requested_count', 0)}",
        f"- raw_5min_files_updated: {summary.get('raw_5min_files_updated', 0)}",
        f"- bars_130min_rebuilt_count: {summary.get('bars_130min_rebuilt_count', 0)}",
        f"- bars_130min_failed_count: {summary.get('bars_130min_failed_count', 0)}",
        f"- provider_rows_received: {summary.get('provider_rows_received', 0)}",
        f"- new_rows_added: {summary.get('new_rows_added', 0)}",
        f"- old_latest_daily_date: {summary.get('old_latest_daily_date', '')}",
        f"- new_latest_daily_date: {summary.get('new_latest_daily_date', '')}",
        f"- old_latest_130min_date: {summary.get('old_latest_130min_date', '')}",
        f"- new_latest_130min_date: {summary.get('new_latest_130min_date', '')}",
        f"- cache_invalidated: {summary.get('cache_invalidated', False)}",
        f"- daily_requested_end: {summary.get('daily_requested_end', '')}",
        f"- intraday_requested_end: {summary.get('intraday_requested_end', '')}",
        f"- intraday_end_mode: {summary.get('intraday_end_mode', '')}",
        f"- current_session_rows_received: {summary.get('current_session_rows_received', 0)}",
        f"- current_session_new_rows_added: {summary.get('current_session_new_rows_added', 0)}",
        f"- pending_symbols_current_session_rows: {summary.get('pending_symbols_current_session_rows', '')}",
        f"- total_runtime_sec: {summary.get('total_runtime_sec', 0)}",
        f"- download_runtime_sec: {summary.get('download_runtime_sec', 0)}",
        f"- aggregation_130min_runtime_sec: {summary.get('aggregation_130min_runtime_sec', 0)}",
        f"- slowest_symbols_top10: {summary.get('slowest_symbols_top10', '')}",
        f"- warnings: {summary.get('warnings', '')}",
        f"- errors: {summary.get('errors', '')}",
        "",
        f"Manifest rows: {len(records)}",
    ]

    if reduction_warnings:
        lines.append("")
        lines.append("## Row Count Reduction Warnings")
        lines.append("")
        lines.append("| symbol | file_type | old_rows | new_rows | action | reason |")
        lines.append("|---|---|---|---|---|---|")
        for w in reduction_warnings:
            lines.append(
                f"| {w.get('symbol', '')} | {w.get('file_type', '')} | "
                f"{w.get('old_rows', '')} | {w.get('new_rows', '')} | "
                f"{w.get('action', '')} | {w.get('reason', '')} |"
            )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _provider_preflight_authenticated_request(
    credentials: tuple[str, str],
    symbol: str,
    request_start: str,
    request_end: str,
) -> pd.DataFrame:
    """Perform the one authenticated IEX daily-bars request for preflight."""
    api_key, secret_key = credentials
    _, _, StockHistoricalDataClient, _, TimeFrame, TimeFrameUnit = _load_alpaca()
    client = StockHistoricalDataClient(api_key, secret_key)
    day_timeframe = (
        TimeFrame.Day
        if hasattr(TimeFrame, "Day")
        else TimeFrame(1, TimeFrameUnit.Day)
    )
    return download_bars(
        client,
        symbol,
        parse_utc_date(request_start),
        parse_utc_date(request_end),
        day_timeframe,
        get_data_feed("iex"),
        get_adjustment("all"),
        rth_only=False,
    )


def run_v2_provider_preflight(
    *,
    report_dir: Path,
    frozen_root: Path = DEFAULT_V2_FROZEN_ROOT,
    max_retries: int = 0,
    dns_probe=default_dns_probe,
    tcp_probe=default_tcp_probe,
    tls_probe=default_tls_probe,
    credential_loader=get_credentials,
    authenticated_request=_provider_preflight_authenticated_request,
) -> dict:
    """Derive a narrow request window from the frozen contract and preflight once."""
    manifest_path = (
        Path(frozen_root)
        / "manifests"
        / GAP_RECOVERY_AUDIT_RUN
        / "acquisition_manifest.json"
    )
    if not manifest_path.is_file():
        raise ValueError("PREFLIGHT_FROZEN_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("dataset_profile") != "rs_v2_clean"
        or manifest.get("feed") != "iex"
        or manifest.get("adjustment") != "all"
    ):
        raise ValueError("PREFLIGHT_FROZEN_CONTRACT_MISMATCH")
    target_end = datetime.fromisoformat(str(manifest["target_end_date"])).date()
    request_start = (target_end - timedelta(days=3)).isoformat()
    request_end = (target_end + timedelta(days=1)).isoformat()
    return run_provider_preflight_core(
        dns_probe=dns_probe,
        tcp_probe=tcp_probe,
        tls_probe=tls_probe,
        credential_loader=credential_loader,
        authenticated_request=authenticated_request,
        request_start=request_start,
        request_end=request_end,
        symbol="QQQ",
        max_retries=max_retries,
        report_dir=Path(report_dir),
    )


def load_provider_preflight_evidence(
    path: Path,
    *,
    max_age_minutes: int = 30,
) -> dict:
    """Load a recent successful preflight without repeating provider work."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("PREFLIGHT_EVIDENCE_MISSING")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("status") != PREFLIGHT_OK:
        raise ValueError("PREFLIGHT_EVIDENCE_NOT_OK")
    required = {
        "dns_status": "OK",
        "tcp_status": "OK",
        "tls_status": "OK",
        "credential_status": "AVAILABLE",
        "authenticated_request_status": "OK",
    }
    if any(evidence.get(key) != value for key, value in required.items()):
        raise ValueError("PREFLIGHT_EVIDENCE_CONTRACT_MISMATCH")
    try:
        generated_at = datetime.fromisoformat(
            str(evidence["generated_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("PREFLIGHT_EVIDENCE_TIMESTAMP_INVALID") from error
    age = datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc)
    if age < -timedelta(minutes=5) or age > timedelta(minutes=max_age_minutes):
        raise ValueError("PREFLIGHT_EVIDENCE_STALE")
    request_count = evidence.get("provider_request_count")
    retry_count = evidence.get("retry_count")
    if (
        not isinstance(request_count, int)
        or request_count not in {1, 2}
        or retry_count not in {0, 1}
        or request_count != retry_count + 1
    ):
        raise ValueError("PREFLIGHT_EVIDENCE_TELEMETRY_INVALID")
    return evidence


def load_canary_recovery_evidence(
    path: Path,
    frozen_root: Path,
    *,
    preflight_report_path: Path | None = None,
    max_age_minutes: int = 60,
    expected_request_contract: str | None = None,
) -> dict:
    """Validate that full recovery is bound to a recent, safe canary."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("GAP_RECOVERY_CANARY_EVIDENCE_MISSING")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    manifest_path = (
        Path(frozen_root)
        / "manifests"
        / GAP_RECOVERY_AUDIT_RUN
        / "acquisition_manifest.json"
    )
    manifest_hash = (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if manifest_path.is_file()
        else ""
    )
    current_frozen_digest = frozen_snapshot_digest(_frozen_hashes(frozen_root))
    required_contract = (
        evidence.get("decision_status") == "CANARY_PASSED"
        and evidence.get("mode") == "canary"
        and evidence.get("exact_target_pair_count") == 4
        and evidence.get("daily_recovery_count") == 4
        and evidence.get("raw_recovery_count") == 4
        and evidence.get("complete_provider_evidence_count") == 8
        and evidence.get("planned_batch_count") == 8
        and evidence.get("terminal_batch_count") == 8
        and evidence.get("interrupted_batch_count") == 0
        and isinstance(evidence.get("network_request_count"), int)
        and 0 < evidence["network_request_count"] <= 20
        and isinstance(evidence.get("retry_count"), int)
        and 0 <= evidence["retry_count"] <= evidence["network_request_count"]
        and evidence.get("systemic_circuit_breaker_fired") is False
        and evidence.get("frozen_dataset_mutation_count") == 0
        and evidence.get("preflight_status") == PREFLIGHT_OK
        and evidence.get("frozen_manifest_sha256") == manifest_hash
        and evidence.get("frozen_snapshot_digest") == current_frozen_digest
        and isinstance(evidence.get("preflight_generated_at"), str)
        and isinstance(evidence.get("request_window_contract"), str)
        and bool(evidence.get("request_window_contract"))
        and (
            expected_request_contract is None
            or evidence.get("request_window_contract")
            == expected_request_contract
        )
    )
    if not required_contract:
        raise ValueError("GAP_RECOVERY_CANARY_EVIDENCE_CONTRACT_MISMATCH")
    try:
        generated_at = datetime.fromisoformat(
            str(evidence["generated_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("GAP_RECOVERY_CANARY_EVIDENCE_CONTRACT_MISMATCH") from error
    age = datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc)
    if age < -timedelta(minutes=5) or age > timedelta(minutes=max_age_minutes):
        raise ValueError("GAP_RECOVERY_CANARY_EVIDENCE_STALE")
    if preflight_report_path is not None:
        preflight = load_provider_preflight_evidence(preflight_report_path)
        if evidence["preflight_generated_at"] != preflight.get("generated_at"):
            raise ValueError("GAP_RECOVERY_CANARY_PREFLIGHT_BINDING_MISMATCH")
    return evidence


def create_gap_recovery_plan(
    *,
    frozen_root: Path,
    audit_run: str,
    report_dir: Path,
    staging_root: Path,
    batch_size: int = 20,
    retry_limit: int = 0,
    run_id: str | None = None,
) -> dict:
    """Create a zero-network, strict-contract batched recovery plan."""
    targets, _ = build_gap_recovery_targets(frozen_root, audit_run)
    validate_gap_recovery_invocation(
        dataset_profile="rs_v2_clean",
        feed="iex",
        adjustment="all",
        audit_run=audit_run,
        frozen_root=frozen_root,
        staging_root=staging_root,
        targets=targets,
    )
    resolved_run_id = run_id or (
        f"gaprec_plan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    plan = build_batched_recovery_plan(
        targets,
        batch_size=batch_size,
        retry_limit=retry_limit,
        run_id=resolved_run_id,
    )
    report_paths = write_batched_recovery_plan(
        plan,
        Path(report_dir),
        Path(staging_root),
    )
    return {"plan": plan, "report_paths": report_paths}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily + raw 5min + RTH 130min data indirir.")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--symbols-file")
    parser.add_argument("--universe", choices=["all"], default=None)
    parser.add_argument("--feed", choices=["iex", "sip", "tiingo"], default=None)
    parser.add_argument("--adjustment", choices=["all", "raw", "split", "dividend"], default=None)
    parser.add_argument("--daily-start", default="2015-01-01")
    parser.add_argument("--intraday-start", default="2020-01-01")
    parser.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--output-dir", default="hisseler")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--refresh-latest", action="store_true")
    parser.add_argument("--refresh-daily", action="store_true")
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--workers", default="auto")
    parser.add_argument("--download-workers", default="4")
    parser.add_argument("--aggregation-workers", default="auto")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--only-aggregate-130min", action="store_true")
    parser.add_argument("--allow-row-count-reduction", action="store_true")

    # V2 profile options
    parser.add_argument("--dataset-profile", choices=["rs_v2_clean", "rs_v2_sip", "rs_v2_tiingo"], default=None)
    parser.add_argument("--daily-only", action="store_true")
    parser.add_argument("--import-source-root", default=None)
    parser.add_argument("--pace-delay", type=float, default=None)
    parser.add_argument("--max-rate-limit-waits", type=int, default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--audit-run")
    parser.add_argument("--corrected-aggregation-audit", action="store_true")
    parser.add_argument("--daily-gap-selection-audit", action="store_true")
    parser.add_argument("--gap-recovery-probe", action="store_true")
    parser.add_argument("--provider-preflight", action="store_true")
    parser.add_argument("--gap-recovery-plan", action="store_true")
    parser.add_argument("--gap-recovery-canary", action="store_true")
    parser.add_argument("--gap-recovery-full", action="store_true")
    parser.add_argument("--use-preflight-report", action="store_true")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument(
        "--recovery-retry-limit",
        type=int,
        choices=[0, 1],
        default=0,
    )
    parser.add_argument("--recovery-workers", type=int, default=2)
    parser.add_argument("--request-budget", type=int, default=None)
    parser.add_argument("--resume-run-id", default=None)
    parser.add_argument("--allow-staging-overwrite", action="store_true")
    parser.add_argument("--staging-root", default=None)
    parser.add_argument("--audit-output-dir", default=str(BASE_DIR / "reports" / "latest"))
    return parser


def verify_file_integrity(file_path: Path) -> dict:
    import hashlib
    if not file_path.exists():
        return {
            "sha256": "",
            "file_size_bytes": 0,
            "row_count": 0,
            "first_timestamp": "",
            "last_timestamp": "",
            "timezone": "",
            "missing_required_column_count": 6,
            "duplicate_timestamp_count": 0,
            "null_ohlcv_count": 0,
            "monotonic_timestamp_status": "INVALID",
            "data_status": "INVALID",
            "error_type": "FileNotFoundError",
            "error_message": f"File {file_path} not found"
        }
    try:
        content = file_path.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        file_size_bytes = len(content)
        df = pd.read_csv(file_path)
        row_count = len(df)

        required_cols = {"Date", "Open", "High", "Low", "Close", "Volume"}
        missing_cols = required_cols - set(df.columns)
        missing_count = len(missing_cols)

        null_count = int(df[["Open", "High", "Low", "Close", "Volume"]].isna().sum().sum()) if missing_count == 0 else 0

        if "Date" in df.columns and row_count > 0:
            dates = pd.to_datetime(df["Date"], utc=True)
            first_ts = str(df["Date"].iloc[0])
            last_ts = str(df["Date"].iloc[-1])
            timezone_str = str(dates.dt.tz) if dates.dt.tz is not None else "America/New_York"
            is_monotonic = dates.is_monotonic_increasing
            monotonic_status = "MONOTONIC_INCREASING" if is_monotonic else "INVALID"
            dup_count = int(dates.duplicated().sum())
        else:
            first_ts = ""
            last_ts = ""
            timezone_str = ""
            monotonic_status = "INVALID"
            dup_count = 0

        data_status = "VALID" if (missing_count == 0 and null_count == 0 and dup_count == 0 and monotonic_status == "MONOTONIC_INCREASING") else "INVALID"

        return {
            "sha256": sha256,
            "file_size_bytes": file_size_bytes,
            "row_count": row_count,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "timezone": timezone_str,
            "missing_required_column_count": missing_count,
            "duplicate_timestamp_count": dup_count,
            "null_ohlcv_count": null_count,
            "monotonic_timestamp_status": monotonic_status,
            "data_status": data_status,
            "error_type": "",
            "error_message": ""
        }
    except Exception as e:
        return {
            "sha256": "",
            "file_size_bytes": 0,
            "row_count": 0,
            "first_timestamp": "",
            "last_timestamp": "",
            "timezone": "",
            "missing_required_column_count": 6,
            "duplicate_timestamp_count": 0,
            "null_ohlcv_count": 0,
            "monotonic_timestamp_status": "INVALID",
            "data_status": "INVALID",
            "error_type": type(e).__name__,
            "error_message": str(e)
        }


_current_run_manifest_path: Path | None = None
_current_manifest_data: dict | None = None
_acquisition_download_completed: bool = False


def main() -> None:
    try:
        _main_impl()
    except BaseException as exc:
        global _current_run_manifest_path, _current_manifest_data, _acquisition_download_completed
        if not _acquisition_download_completed and _current_run_manifest_path is not None and _current_manifest_data is not None:
            try:
                _current_manifest_data["status"] = "FAILED"
                _current_manifest_data["failed_reason"] = f"{type(exc).__name__}: {str(exc)}"
                _atomic_write_json(_current_manifest_data, _current_run_manifest_path)
            except Exception:
                pass
        raise exc


def _run_tiingo_profile(args) -> None:
    """rs_v2_tiingo CLI entry: parallel to the Alpaca profiles, never mixed.

    Modes: --dry-run (plan only), --live (paced Tiingo fetch, resumable),
    --live --import-source-root <root> (no network; import already-fetched
    local Tiingo CSVs). Daily-only; refresh flags are rejected fail-closed.
    """
    provider = _tiingo_provider()
    if args.feed not in (None, "tiingo"):
        raise ValueError("V2_TIINGO_REQUIRES_FEED_TIINGO")
    if args.refresh_latest or args.only_aggregate_130min or args.skip_download:
        raise ValueError("V2_TIINGO_UNSUPPORTED_MODE_FLAG")
    output_root = Path(args.output_root) if args.output_root else provider.DEFAULT_V2_TIINGO_FROZEN_ROOT
    kwargs = {}
    if args.pace_delay is not None:
        if args.pace_delay <= 0:
            raise ValueError("V2_TIINGO_PACE_DELAY_MUST_BE_POSITIVE")
        kwargs["pace_delay_seconds"] = args.pace_delay
    if args.max_rate_limit_waits is not None:
        if args.max_rate_limit_waits < 1:
            raise ValueError("V2_TIINGO_MAX_RATE_LIMIT_WAITS_MUST_BE_POSITIVE")
        kwargs["max_rate_limit_waits"] = args.max_rate_limit_waits
    if args.refresh_daily:
        # Refresh is always a live acquisition; plan/import flags reject fail-closed.
        if args.dry_run or args.import_source_root:
            raise ValueError("V2_TIINGO_REFRESH_FORBIDS_DRY_RUN_AND_IMPORT")
        # Subset refresh never finalizes a manifest, so running it against the
        # default production root would leave files newer than the validated
        # manifest (loud downstream validation failure). Require an explicit,
        # non-default root for subset verification runs.
        if args.symbols and (args.output_root is None or Path(args.output_root).resolve() == provider.DEFAULT_V2_TIINGO_FROZEN_ROOT.resolve()):
            raise ValueError("V2_TIINGO_SUBSET_REFRESH_REQUIRES_NON_DEFAULT_OUTPUT_ROOT")
        result = provider.run_v2_tiingo_daily_refresh(
            output_root,
            symbols_subset=list(args.symbols) if args.symbols else None,
            **kwargs,
        )
        print(f"rs_v2_tiingo refresh status: {result['status']} (end {result['refresh_end']}, {result['symbols_processed']} symbols, drift: {result['drift_detected_symbols'] or 'none'})", flush=True)
        return
    if args.dry_run == args.live:
        raise ValueError("V2_TIINGO_REQUIRES_EXACTLY_ONE_OF_DRY_RUN_OR_LIVE")
    result = provider.run_v2_tiingo_acquisition(
        output_root,
        live=args.live,
        import_source_root=Path(args.import_source_root) if args.import_source_root else None,
        **kwargs,
    )
    print(f"rs_v2_tiingo status: {result['status']} ({result['logical_acquisition_item_count']} items)", flush=True)


def _main_impl() -> None:
    ensure_project_venv()
    total_start = time.perf_counter()
    args = build_parser().parse_args()

    is_v2_clean = (args.dataset_profile == "rs_v2_clean")
    is_v2_sip = (args.dataset_profile == "rs_v2_sip")
    is_v2 = is_v2_clean or is_v2_sip

    if args.dataset_profile == "rs_v2_tiingo":
        _run_tiingo_profile(args)
        return

    selected_recovery_modes = sum(
        bool(value)
        for value in (
            args.provider_preflight,
            args.gap_recovery_plan,
            args.gap_recovery_canary,
            args.gap_recovery_full,
            args.gap_recovery_probe,
        )
    )
    if selected_recovery_modes > 1:
        raise ValueError("SELECT_EXACTLY_ONE_V2_RECOVERY_MODE")

    if args.provider_preflight:
        feed_passed = any(arg.startswith("--feed") for arg in sys.argv)
        adjustment_passed = any(arg.startswith("--adjustment") for arg in sys.argv)
        if (
            not is_v2_clean
            or args.feed != "iex"
            or args.adjustment != "all"
            or not feed_passed
            or not adjustment_passed
        ):
            raise ValueError("PREFLIGHT_INVOCATION_CONTRACT_MISMATCH")
        result = run_v2_provider_preflight(
            report_dir=Path(args.audit_output_dir),
            frozen_root=Path(args.output_root or DEFAULT_V2_FROZEN_ROOT),
            max_retries=args.recovery_retry_limit,
        )
        print(f"provider_preflight_status: {result['status']}")
        for key in (
            "dns_status",
            "tcp_status",
            "tls_status",
            "credential_status",
            "authenticated_request_status",
            "provider_request_count",
            "retry_count",
        ):
            print(f"{key}: {result[key]}")
        return

    if args.gap_recovery_plan:
        if not is_v2_clean or args.feed != "iex" or args.adjustment != "all":
            raise ValueError("GAP_RECOVERY_PLAN_INVOCATION_CONTRACT_MISMATCH")
        result = create_gap_recovery_plan(
            frozen_root=Path(args.output_root or DEFAULT_V2_FROZEN_ROOT),
            audit_run=args.audit_run or GAP_RECOVERY_AUDIT_RUN,
            report_dir=Path(args.audit_output_dir),
            staging_root=Path(
                args.staging_root or DEFAULT_V2_RECOVERY_STAGING_ROOT
            ),
            batch_size=args.batch_size,
            retry_limit=args.recovery_retry_limit,
        )
        print("gap_recovery_plan_status: COMPLETE")
        for key, value in result["plan"]["summary"].items():
            print(f"{key}: {value}")
        return

    if args.gap_recovery_canary or args.gap_recovery_full:
        if (
            not is_v2_clean
            or args.feed != "iex"
            or args.adjustment != "all"
            or not args.use_preflight_report
        ):
            raise ValueError("GAP_RECOVERY_EXECUTION_CONTRACT_MISMATCH")
        report_dir = Path(args.audit_output_dir)
        provider_contract_path = (
            report_dir / "rs_v2_provider_contract_forensics_status.json"
        )
        preflight_path = report_dir / "rs_v2_provider_preflight.json"
        try:
            from .v2_provider_contract_forensics import (
                load_resolved_provider_contract,
            )
        except ImportError:
            from v2_provider_contract_forensics import (
                load_resolved_provider_contract,
            )
        current_preflight = load_provider_preflight_evidence(preflight_path)
        provider_contract = load_resolved_provider_contract(
            provider_contract_path,
            preflight_generated_at=str(current_preflight["generated_at"]),
        )
        request_contract = str(
            provider_contract["selected_request_window_contract"]
        )
        if args.gap_recovery_full:
            canary_status_path = (
                report_dir / "rs_v2_gap_recovery_canary_status.json"
            )
            load_canary_recovery_evidence(
                canary_status_path,
                Path(args.output_root or DEFAULT_V2_FROZEN_ROOT),
                preflight_report_path=(
                    preflight_path
                ),
                expected_request_contract=request_contract,
            )
        try:
            from .v2_gap_recovery_runner import run_gap_recovery_mode
        except ImportError:
            from v2_gap_recovery_runner import run_gap_recovery_mode
        result = run_gap_recovery_mode(
            mode="full" if args.gap_recovery_full else "canary",
            frozen_root=Path(args.output_root or DEFAULT_V2_FROZEN_ROOT),
            staging_root=Path(
                args.staging_root or DEFAULT_V2_RECOVERY_STAGING_ROOT
            ),
            report_dir=report_dir,
            preflight_report_path=preflight_path,
            provider_contract_report_path=provider_contract_path,
            audit_run=args.audit_run or GAP_RECOVERY_AUDIT_RUN,
            batch_size=args.batch_size,
            retry_limit=args.recovery_retry_limit,
            workers=args.recovery_workers,
            request_budget=args.request_budget,
            resume_run_id=args.resume_run_id,
            allow_staging_overwrite=args.allow_staging_overwrite,
        )
        print(f"gap_recovery_status: {result['status']}")
        for key, value in result["summary"].items():
            print(f"{key}: {value}")
        return

    if args.gap_recovery_probe:
        raise ValueError(
            "GAP_RECOVERY_LEGACY_PROBE_DISABLED_USE_BATCHED_RECOVERY"
        )

    if (args.corrected_aggregation_audit or args.daily_gap_selection_audit) and not args.audit_run:
        raise ValueError("offline V2 audit mode requires --audit-run")
    if args.corrected_aggregation_audit and args.daily_gap_selection_audit:
        raise ValueError("select exactly one offline V2 audit mode")

    if args.audit_run:
        if not is_v2 or not args.output_root:
            raise ValueError("V2 audit requires --dataset-profile rs_v2_clean/rs_v2_sip and --output-root")
        if args.live or args.dry_run:
            raise ValueError("V2 audit forbids --live and --dry-run")
        with v2_offline_audit_guard() as offline_telemetry:
            if args.daily_gap_selection_audit:
                try:
                    from .v2_daily_gap_selection_audit import audit_v2_daily_gap_selection
                except ImportError:
                    from v2_daily_gap_selection_audit import audit_v2_daily_gap_selection
                result = audit_v2_daily_gap_selection(
                    Path(args.output_root), args.audit_run, Path(args.audit_output_dir), offline_telemetry=offline_telemetry,
                )
                report_path = result["paths"]["repair_summary"]
            elif args.corrected_aggregation_audit:
                try:
                    from .v2_corrected_aggregation_audit import audit_corrected_v2_aggregation
                except ImportError:
                    from v2_corrected_aggregation_audit import audit_corrected_v2_aggregation
                result = audit_corrected_v2_aggregation(
                    Path(args.output_root),
                    args.audit_run,
                    Path(args.audit_output_dir),
                    offline_telemetry=offline_telemetry,
                )
                report_path = result["paths"]["aggregation_markdown"]
            else:
                try:
                    from .v2_coverage_forensics import audit_v2_run
                except ImportError:
                    from v2_coverage_forensics import audit_v2_run
                result = audit_v2_run(Path(args.output_root), args.audit_run, Path(args.audit_output_dir))
                report_path = result["paths"]["markdown"]
        print(f"V2 offline coverage audit complete: {report_path}")
        print(f"network_request_count: {offline_telemetry['network_attempt_count']}")
        print(f"client_attempt_count: {offline_telemetry['client_attempt_count']}")
        return

    adjustment_passed = any(arg.startswith("--adjustment") for arg in sys.argv)
    feed_passed = any(arg.startswith("--feed") for arg in sys.argv)

    # Drift detection only applies to SIP --refresh-daily; every other
    # acquisition path must carry an explicit None in its download payloads.
    drift_check_start = None

    if is_v2_clean:
        if not adjustment_passed:
            raise ValueError("V2 profile requires explicit --adjustment parameter")
        if args.adjustment != "all":
            raise ValueError("V2 profile requires --adjustment all")
        if not feed_passed:
            raise ValueError("V2 profile requires explicit --feed parameter")
        if args.feed != "iex":
            raise ValueError("V2 profile requires --feed iex")
        if not args.start:
            raise ValueError("V2 profile requires --start")
        if not args.end:
            raise ValueError("V2 profile requires --end")
        if not args.output_root:
            raise ValueError("V2 profile requires --output-root")
        if not (args.dry_run or args.live):
            raise ValueError("V2 profile requires either --dry-run or --live")
        if args.dry_run and args.live:
            raise ValueError("Cannot specify both --dry-run and --live")
        if args.live and not args.refresh_daily and not args.overwrite:
            raise ValueError("V2 LIVE initial acquisition requires --overwrite")
        if args.live and (args.refresh_latest or args.skip_download or args.only_aggregate_130min):
            raise ValueError("V2 LIVE initial acquisition forbids refresh/skip/aggregate-only")

        # V2 target dates enforcement
        if args.refresh_daily:
            today_str = args.end if (args.end and args.end != "2026-07-07") else datetime.now(timezone.utc).date().isoformat()
            market_schedule = approved_nyse_schedule("2020-07-27", today_str)
            args.end = market_schedule.index[-1].strftime("%Y-%m-%d")
        elif args.start != "2020-07-27" or args.end != "2026-07-07":
            raise ValueError("V2 profile target dates must be start=2020-07-27, end=2026-07-07")

        output_dir = Path(args.output_root)
        daily_start = parse_utc_date(args.start)
        intraday_start = parse_utc_date(args.start)
        daily_end = parse_utc_date(args.end).replace(hour=23, minute=59, second=59)
        intraday_end = parse_utc_date(args.end).replace(hour=23, minute=59, second=59)

        candidates, v2_baseline = load_v2_frozen_universe()
        symbols = [*candidates, "QQQ", "SPY"]
    elif is_v2_sip:
        drift_check_start = None
        if args.refresh_daily:
            if args.refresh_latest:
                raise ValueError("Cannot specify both --refresh-daily and --refresh-latest")
            if args.skip_download or args.only_aggregate_130min:
                raise ValueError("V2 SIP daily refresh forbids --skip-download and --only-aggregate-130min")
            if args.dry_run:
                raise ValueError("V2 SIP daily refresh forbids --dry-run (refresh-daily is always a live acquisition)")
            if not args.feed:
                args.feed = "sip"
            if args.feed != "sip":
                raise ValueError("V2 SIP profile requires --feed sip")
            if not args.adjustment:
                args.adjustment = "all"
            if args.adjustment != "all":
                raise ValueError("V2 SIP profile requires --adjustment all")
            if not args.output_root:
                args.output_root = str(DEFAULT_V2_SIP_FROZEN_ROOT)
            if not args.start:
                args.start = "2020-07-27"

            output_dir = Path(args.output_root)
            latest_pointer_path = output_dir / "manifests" / "latest_manifest.json"
            if not latest_pointer_path.exists():
                raise FileNotFoundError("latest_manifest.json not found under output_root. Daily refresh requires an existing validated SIP dataset.")

            latest_pointer = json.loads(latest_pointer_path.read_text(encoding="utf-8"))
            run_manifest_path = output_dir / latest_pointer["immutable_manifest_relative_path"]
            latest_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))

            today_str = args.end or datetime.now(timezone.utc).date().isoformat()
            market_schedule = approved_nyse_schedule("2020-07-27", today_str)
            latest_session_date_str = market_schedule.index[-1].strftime("%Y-%m-%d")

            if latest_manifest.get("target_end_date") == latest_session_date_str:
                print(f"Idempotent: daily refresh already run up to {latest_session_date_str}. No-op.")
                sys.exit(0)

            args.end = latest_session_date_str
            args.live = True

            if len(market_schedule) >= 10:
                drift_check_start = market_schedule.index[-10].to_pydatetime()
            else:
                drift_check_start = parse_utc_date("2020-07-27")
        else:
            if not adjustment_passed:
                raise ValueError("V2 SIP profile requires explicit --adjustment parameter")
            if args.adjustment != "all":
                raise ValueError("V2 SIP profile requires --adjustment all")
            if not feed_passed:
                raise ValueError("V2 SIP profile requires explicit --feed parameter")
            if args.feed != "sip":
                raise ValueError("V2 SIP profile requires --feed sip")
            if not args.start:
                raise ValueError("V2 SIP profile requires --start")
            if not args.end:
                raise ValueError("V2 SIP profile requires --end")
            if not args.output_root:
                raise ValueError("V2 SIP profile requires --output-root")
            if not (args.dry_run or args.live):
                raise ValueError("V2 SIP profile requires either --dry-run or --live")
            if args.dry_run and args.live:
                raise ValueError("Cannot specify both --dry-run and --live")

        output_dir = Path(args.output_root)
        daily_start = parse_utc_date(args.start)
        intraday_start = parse_utc_date(args.start)
        daily_end = parse_utc_date(args.end).replace(hour=23, minute=59, second=59)
        intraday_end = parse_utc_date(args.end).replace(hour=23, minute=59, second=59)

        if args.refresh_daily:
            candidates, v2_baseline = load_v2_frozen_universe()
            symbols = [*candidates, "QQQ", "SPY"]
        elif args.live:
            if not args.overwrite:
                raise ValueError("V2 LIVE initial acquisition requires --overwrite")
            if args.refresh_latest or args.skip_download or args.only_aggregate_130min:
                raise ValueError("V2 LIVE initial acquisition forbids refresh/skip/aggregate-only")
            if args.start != "2020-07-27" or args.end != "2026-07-07":
                raise ValueError("V2 SIP profile target dates must be start=2020-07-27, end=2026-07-07")
            candidates, v2_baseline = load_v2_frozen_universe()
            symbols = [*candidates, "QQQ", "SPY"]
        else:
            if not args.symbols:
                raise ValueError("V2 SIP profile requires --symbols (no frozen baseline established yet)")
            requested_symbols = unique_symbols(args.symbols)
            candidates = [symbol for symbol in requested_symbols if symbol not in V2_BENCHMARK_ROLES]
            symbols = [*candidates, "QQQ", "SPY"]
    else:
        if args.feed is None:
            args.feed = "iex"
        if args.adjustment is None:
            args.adjustment = "all"

        if args.start:
            daily_start = parse_utc_date(args.start)
            intraday_start = parse_utc_date(args.start)
        else:
            daily_start = parse_utc_date(args.daily_start)
            intraday_start = parse_utc_date(args.intraday_start)
        daily_end = resolve_date_end(args.end)
        intraday_end = resolve_intraday_end(args.end)
        output_dir = Path(args.output_dir)
        symbols = load_symbols(args.symbols, args.symbols_file, args.universe)

    if not symbols:
        raise SystemExit("Indirilecek sembol bulunamadi.")

    if is_v2 and args.dry_run:
        plan_rows = build_v2_acquisition_plan(output_dir, candidates, args.start, args.end, args.feed, args.adjustment)
        report_dir = Path(args.output_root) / "dry_run_reports"
        plan_csv, plan_md = write_v2_dry_run_plan(report_dir, plan_rows)

        print(f"DRY RUN SUCCESSFUL. Plan generated at {plan_csv} and {plan_md}.")
        print(f"logical_acquisition_item_count: {len(plan_rows)}")
        print("Status: DRY_RUN_PLAN_ONLY")
        sys.exit(0)

    # V2 folder directory structure
    if is_v2:
        daily_dir = output_dir / "daily"
        raw_5min_dir = output_dir / "raw_5min"
        bars_130min_dir = output_dir / "bars_130min"
        manifest_dir = output_dir / "manifests"
        (output_dir / "benchmarks" / "daily").mkdir(parents=True, exist_ok=True)
        (output_dir / "benchmarks" / "raw_5min").mkdir(parents=True, exist_ok=True)
        (output_dir / "benchmarks" / "bars_130min").mkdir(parents=True, exist_ok=True)
    else:
        daily_dir = output_dir / "daily"
        raw_5min_dir = output_dir / "raw_5min"
        bars_130min_dir = output_dir / "bars_130min"
        manifest_dir = output_dir / "manifest"

    for directory in (daily_dir, raw_5min_dir, bars_130min_dir, manifest_dir):
        directory.mkdir(parents=True, exist_ok=True)

    _, _, _, _, TimeFrame, TimeFrameUnit = _load_alpaca()
    api_key = ""
    secret_key = ""
    if not args.skip_download and not args.only_aggregate_130min:
        api_key, secret_key = get_credentials()
    feed = get_data_feed(args.feed)
    adjustment = get_adjustment(args.adjustment)
    day_tf = TimeFrame.Day if hasattr(TimeFrame, "Day") else TimeFrame(1, TimeFrameUnit.Day)
    min5_tf = TimeFrame(5, TimeFrameUnit.Minute)
    cpu_count = os.cpu_count() or 2
    workers_resolved = resolve_worker_count(args.workers, min(max(cpu_count - 1, 1), 8), 16)
    download_workers_resolved = resolve_worker_count(args.download_workers, 4, 8)
    aggregation_workers_resolved = resolve_worker_count(args.aggregation_workers, min(max(cpu_count - 1, 1), 8), 16)
    if args.parallel:
        download_workers_resolved = min(download_workers_resolved, workers_resolved)
        aggregation_workers_resolved = min(aggregation_workers_resolved, workers_resolved)
    else:
        download_workers_resolved = 1
        aggregation_workers_resolved = 1

    old_latest_daily_date = latest_date_in_dir(daily_dir, "*_1D.csv")
    old_latest_130min_date = latest_date_in_dir(bars_130min_dir, "*_130min.csv")
    refresh_mode = "refresh-latest" if args.refresh_latest else ("overwrite" if args.overwrite else "skip-existing")
    if args.only_aggregate_130min:
        refresh_mode = "only-aggregate-130min"
    elif args.skip_download:
        refresh_mode = "skip-download"

    summary = {
        "daily_downloaded": 0,
        "daily_skipped": 0,
        "raw_5min_downloaded": 0,
        "raw_5min_skipped": 0,
        "bars_130min_built": 0,
        "bars_130min_skipped": 0,
        "errors": 0,
    }
    records = []
    timing_rows = []
    download_results = {}
    aggregation_results = {}

    print(
        f"refresh_mode: {refresh_mode}; parallel: {args.parallel}; "
        f"download_workers: {download_workers_resolved}; aggregation_workers: {aggregation_workers_resolved}",
        flush=True,
    )

    if is_v2 and args.live:
        run_id = f"acq_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        run_manifest_dir = output_dir / "manifests" / run_id
        run_manifest_dir.mkdir(parents=True, exist_ok=True)
        run_manifest_path = run_manifest_dir / "acquisition_manifest.json"
        latest_manifest_path = output_dir / "manifests" / "latest_manifest.json"
        download_report_csv_path = manifest_dir / "download_manifest.csv"
        source_commit = _source_commit()
        manifest_data = {
            "dataset_schema_version": "2.1.0" if is_v2_sip else "2.0.0",
            "acquisition_run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "dataset_profile": args.dataset_profile,
            "strategy_identifier": "rs_m126_top30_sma100_no_regime_v2_clean_stock",
            "target_start_date": args.start,
            "target_end_date": args.end,
            "feed": args.feed,
            "adjustment": args.adjustment,
            "logical_acquisition_item_count": 179 if args.daily_only else 358,
            "successful_item_count": 0,
            "failed_item_count": 0,
            "status": "IN_PROGRESS",
            "candidate_universe_hash": "026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933",
            "candidate_count": 177,
            "benchmark_symbols": ["QQQ", "SPY"],
            "downloader_source_commit": source_commit,
            "downloader_source_commit_status": "AVAILABLE" if source_commit else "UNAVAILABLE",
            "provider_attempt_count": None,
            "retry_attempt_count": None,
            "failed_provider_attempt_count": None,
            "retry_telemetry_status": "PENDING",
            "pagination_request_count": None,
            "pagination_request_count_status": "UNAVAILABLE_FROM_CLIENT_API",
            "downloader_call_path": "testdata/vericekici_updated.py",
            "refresh_daily": args.refresh_daily,
            "files": []
        }
        global _current_run_manifest_path, _current_manifest_data
        _current_run_manifest_path = run_manifest_path
        _current_manifest_data = manifest_data
        _atomic_write_json(manifest_data, run_manifest_path)

    refresh_daily_terminated_symbols = set()
    if args.refresh_daily:
        refresh_daily_exceptions = load_reviewed_coverage_exceptions(V2_SIP_COVERAGE_EXCEPTIONS_PATH)
        refresh_daily_terminated_symbols = _refresh_daily_terminated_symbols(symbols, refresh_daily_exceptions, args.end)

    download_payloads = []
    for symbol in symbols:
        if is_v2:
            paths = resolve_v2_dataset_paths(output_dir, symbol)
            daily_p, raw_p = str(paths.daily), str(paths.raw_5min)
        else:
            daily_p = str(daily_dir / f"{symbol}_1D.csv")
            raw_p = str(raw_5min_dir / f"{symbol}_5min.csv")

        download_payloads.append({
            "symbol": symbol,
            "daily_path": daily_p,
            "raw_path": raw_p,
            "api_key": api_key,
            "secret_key": secret_key,
            "feed": feed,
            "adjustment": adjustment,
            "day_tf": day_tf,
            "min5_tf": min5_tf,
            "daily_start": daily_start,
            "intraday_start": intraday_start,
            "daily_end": daily_end,
            "intraday_end": intraday_end,
            "refresh_latest": args.refresh_latest,
            "refresh_daily": args.refresh_daily,
            "drift_check_start": drift_check_start,
            "overwrite": args.overwrite,
            "skip_download": args.skip_download or args.only_aggregate_130min or (symbol in refresh_daily_terminated_symbols),
            "skip_raw_5min": args.daily_only,
            "allow_row_count_reduction": args.allow_row_count_reduction,
        })

    download_start = time.perf_counter()
    if args.parallel and download_workers_resolved > 1 and not args.skip_download and not args.only_aggregate_130min:
        with ThreadPoolExecutor(max_workers=download_workers_resolved) as executor:
            futures = {executor.submit(_download_symbol_worker, payload): payload["symbol"] for payload in download_payloads}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                result = future.result()
                download_results[result["symbol"]] = result
                print(
                    f"[download {completed}/{len(symbols)}] {result['symbol']} "
                    f"daily {result['daily_status']}, 5min {result['raw_5min_status']}",
                    flush=True,
                )
                if result["error"]:
                    print(f"  error: {result['error']}", flush=True)
    else:
        for index, payload in enumerate(download_payloads, 1):
            result = _download_symbol_worker(payload)
            download_results[result["symbol"]] = result
            print(
                f"[download {index}/{len(symbols)}] {result['symbol']} "
                f"daily {result['daily_status']}, 5min {result['raw_5min_status']}",
                flush=True,
            )
            if result["error"]:
                print(f"  error: {result['error']}", flush=True)
                time.sleep(1)
    download_runtime_sec = round(time.perf_counter() - download_start, 4)

    v2_market_schedule = approved_nyse_schedule(args.start, args.end) if is_v2 else None
    aggregation_payloads = []
    for symbol in symbols:
        download_result = download_results.get(symbol, {})
        raw_status = download_result.get("raw_5min_status", "skipped")
        rebuild = bool(args.overwrite or args.refresh_latest or args.skip_download or args.only_aggregate_130min or raw_status in {"downloaded", "refreshed"})
        v2_paths = resolve_v2_dataset_paths(output_dir, symbol) if is_v2 else None
        aggregation_payloads.append(
            {
                "symbol": symbol,
                "raw_path": str(v2_paths.raw_5min if v2_paths else raw_5min_dir / f"{symbol}_5min.csv"),
                "bar_path": str(v2_paths.bars_130min if v2_paths else bars_130min_dir / f"{symbol}_130min.csv"),
                "overwrite": args.overwrite,
                "rebuild": rebuild,
                "allow_row_count_reduction": args.allow_row_count_reduction,
                "dataset_profile": "rs_v2_clean" if is_v2 else "legacy",
                "market_schedule": v2_market_schedule,
                "dataset_end": args.end,
            }
        )

    aggregation_start = time.perf_counter()
    if args.parallel and aggregation_workers_resolved > 1:
        with ThreadPoolExecutor(max_workers=aggregation_workers_resolved) as executor:
            futures = {executor.submit(_aggregate_130min_worker, payload): payload["symbol"] for payload in aggregation_payloads}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                result = future.result()
                aggregation_results[result["symbol"]] = result
                print(
                    f"[aggregate {completed}/{len(symbols)}] {result['symbol']} 130min {result['bar_130min_status']}",
                    flush=True,
                )
                if result["error"]:
                    print(f"  error: {result['error']}", flush=True)
    else:
        for index, payload in enumerate(aggregation_payloads, 1):
            result = _aggregate_130min_worker(payload)
            aggregation_results[result["symbol"]] = result
            print(f"[aggregate {index}/{len(symbols)}] {result['symbol']} 130min {result['bar_130min_status']}", flush=True)
            if result["error"]:
                print(f"  error: {result['error']}", flush=True)
    aggregation_runtime_sec = round(time.perf_counter() - aggregation_start, 4)

    all_warnings = []
    all_errors = []
    for index, symbol in enumerate(symbols, 1):
        v2_paths = resolve_v2_dataset_paths(output_dir, symbol) if is_v2 else None
        daily_path = v2_paths.daily if v2_paths else daily_dir / f"{symbol}_1D.csv"
        raw_path = v2_paths.raw_5min if v2_paths else raw_5min_dir / f"{symbol}_5min.csv"
        bar_path = v2_paths.bars_130min if v2_paths else bars_130min_dir / f"{symbol}_130min.csv"
        download_result = download_results.get(symbol, {})
        aggregation_result = aggregation_results.get(symbol, {})
        daily_status = download_result.get("daily_status", "skipped")
        raw_status = download_result.get("raw_5min_status", "skipped")
        bar_status = aggregation_result.get("bar_130min_status", "skipped")
        error_parts = [value for value in [download_result.get("error", ""), aggregation_result.get("error", "")] if value]
        warning_parts = list(download_result.get("warnings", []))
        if aggregation_result.get("warning"):
            warning_parts.append(aggregation_result["warning"])
        if error_parts:
            summary["errors"] += 1
            all_errors.append(f"{symbol}:{'|'.join(error_parts)}")
        if warning_parts:
            all_warnings.append(f"{symbol}:{'|'.join(warning_parts)}")
        summary["daily_downloaded"] += int(daily_status in {"downloaded", "refreshed"})
        summary["daily_skipped"] += int(daily_status == "skipped")
        summary["raw_5min_downloaded"] += int(raw_status in {"downloaded", "refreshed"})
        summary["raw_5min_skipped"] += int(raw_status == "skipped")
        summary["bars_130min_built"] += int(bar_status == "built")
        summary["bars_130min_skipped"] += int(bar_status == "skipped")

        daily_range = date_range_summary(daily_path)
        raw_range = date_range_summary(raw_path)
        bar_range = date_range_summary(bar_path)
        record = {
            "symbol": symbol,
            "daily_status": daily_status,
            "raw_5min_status": raw_status,
            "bar_130min_status": bar_status,
            "daily_rows": row_count(daily_path),
            "raw_5min_rows": row_count(raw_path),
            "bar_130min_rows": row_count(bar_path),
            "daily_first_date": daily_range["first_date"],
            "daily_last_date": daily_range["last_date"],
            "raw_5min_first_date": raw_range["first_date"],
            "raw_5min_last_date": raw_range["last_date"],
            "bar_130min_first_date": bar_range["first_date"],
            "bar_130min_last_date": bar_range["last_date"],
            "daily_path": str(daily_path),
            "raw_5min_path": str(raw_path),
            "bar_130min_path": str(bar_path),
            "download_runtime_sec": download_result.get("runtime_sec", 0.0),
            "aggregation_runtime_sec": aggregation_result.get("runtime_sec", 0.0),
            "provider_rows_received": int(download_result.get("daily_provider_rows", 0)) + int(download_result.get("raw_provider_rows", 0)),
            "new_rows_added": int(download_result.get("new_rows_added", 0)),
            "warning": "|".join(warning_parts),
            "error": "|".join(error_parts),
        }
        records.append(record)
        timing_rows.append(
            {
                "symbol": symbol,
                "download_runtime_sec": record["download_runtime_sec"],
                "aggregation_runtime_sec": record["aggregation_runtime_sec"],
                "total_symbol_runtime_sec": round(record["download_runtime_sec"] + record["aggregation_runtime_sec"], 4),
            }
        )
        print(
            f"[{index}/{len(symbols)}] {symbol} daily {daily_status}, 5min {raw_status}, 130min {bar_status}",
            flush=True,
        )

    download_report_csv_path = manifest_dir / "download_manifest.csv"
    pd.DataFrame(records).to_csv(download_report_csv_path, index=False)

    any_updated = (summary["daily_downloaded"] > 0 or
                   summary["raw_5min_downloaded"] > 0 or
                   summary["bars_130min_built"] > 0)
    cache_invalidated = False
    if any_updated:
        cache_dir = daily_dir.parent / ".cache" / "prepared_features"
        if cache_dir.exists():
            for item in cache_dir.iterdir():
                if item.is_file():
                    try:
                        item.unlink()
                    except Exception as e:
                        print(f"Warning: failed to delete cache file {item}: {e}")
        cache_invalidated = True

    new_latest_daily_date = latest_date_in_dir(daily_dir, "*_1D.csv")
    new_latest_130min_date = latest_date_in_dir(bars_130min_dir, "*_130min.csv")
    total_runtime_sec = round(time.perf_counter() - total_start, 4)
    slowest = sorted(timing_rows, key=lambda item: item["total_symbol_runtime_sec"], reverse=True)[:10]
    slowest_symbols_top10 = ";".join(f"{item['symbol']}:{item['total_symbol_runtime_sec']}" for item in slowest)
    current_session_rows_received = sum(row.get("current_session_received", 0) for row in download_results.values())
    current_session_new_rows_added = sum(row.get("current_session_added", 0) for row in download_results.values())

    pending_symbols = {"AMD", "KLAC", "LRCX", "SMH", "SOXX"}
    pending_parts = []
    for row in download_results.values():
        sym = row["symbol"]
        if sym in pending_symbols:
            pending_parts.append(f"{sym}:{row.get('current_session_added', 0)}")
    pending_symbols_current_session_rows_str = ", ".join(pending_parts)

    refresh_summary = {
        "refresh_mode": refresh_mode,
        "parallel_enabled": bool(args.parallel),
        "workers_resolved": workers_resolved,
        "download_workers_resolved": download_workers_resolved,
        "aggregation_workers_resolved": aggregation_workers_resolved,
        "daily_requested_count": 0 if args.skip_download or args.only_aggregate_130min else len(symbols),
        "daily_files_updated": summary["daily_downloaded"],
        "raw_5min_requested_count": 0 if args.skip_download or args.only_aggregate_130min else len(symbols),
        "raw_5min_files_updated": summary["raw_5min_downloaded"],
        "bars_130min_rebuilt_count": summary["bars_130min_built"],
        "bars_130min_failed_count": sum(
            1
            for row in records
            if row["error"] and ("missing_raw_5min" in row["error"] or row["bar_130min_status"] != "built")
        ),
        "provider_rows_received": sum(row["provider_rows_received"] for row in records),
        "new_rows_added": sum(row["new_rows_added"] for row in records),
        "old_latest_daily_date": old_latest_daily_date,
        "new_latest_daily_date": new_latest_daily_date,
        "old_latest_130min_date": old_latest_130min_date,
        "new_latest_130min_date": new_latest_130min_date,
        "cache_invalidated": cache_invalidated,
        "total_runtime_sec": total_runtime_sec,
        "download_runtime_sec": download_runtime_sec,
        "aggregation_130min_runtime_sec": aggregation_runtime_sec,
        "slowest_symbols_top10": slowest_symbols_top10,
        "warnings": ";".join(all_warnings),
        "errors": ";".join(all_errors),
        "daily_requested_end": daily_end.isoformat(),
        "intraday_requested_end": intraday_end.isoformat(),
        "intraday_end_mode": "now" if args.end == "today" else "current_timestamp",
        "current_session_rows_received": current_session_rows_received,
        "current_session_new_rows_added": current_session_new_rows_added,
        "pending_symbols_current_session_rows": pending_symbols_current_session_rows_str,
    }

    global _acquisition_download_completed
    _acquisition_download_completed = True
    if is_v2 and args.live:
        successful_count = 0
        failed_count = 0
        file_records = []
        reviewed_exceptions = load_reviewed_coverage_exceptions(V2_SIP_COVERAGE_EXCEPTIONS_PATH if is_v2_sip else V2_COVERAGE_EXCEPTIONS_PATH)
        market_schedule = approved_nyse_schedule(args.start, args.end)

        for symbol in symbols:
            role = "PRIMARY_BENCHMARK" if symbol == "QQQ" else "SECONDARY_BENCHMARK" if symbol == "SPY" else "CANDIDATE"
            download_result = download_results.get(symbol, {})
            daily_status = download_result.get("daily_status", "FAILED")
            raw_status = download_result.get("raw_5min_status", "FAILED")

            # Verify daily file
            paths = resolve_v2_dataset_paths(output_dir, symbol)
            daily_path = paths.daily
            daily_integrity = verify_file_integrity(daily_path)
            daily_coverage = v2_coverage_metadata(
                daily_path, "1D", symbol, args.start, args.end,
                market_schedule=market_schedule, reviewed_exceptions=reviewed_exceptions,
            )

            daily_record = {
                "symbol": symbol,
                "asset_role": role,
                "timeframe": "1D",
                "requested_start": args.start,
                "requested_end": args.end,
                "request_end_semantics": "INCLUSIVE",
                "feed": args.feed,
                "adjustment": args.adjustment,
                "output_relative_path": str(daily_path.relative_to(output_dir)).replace("\\", "/"),
                "acquisition_run_id": run_id,
                "acquisition_action": "DOWNLOADED_IN_CURRENT_RUN",
                "downloaded_in_current_run": daily_status in {"downloaded", "refreshed"},
                "provider_result_status": daily_status.upper(),
                **daily_coverage,
                **daily_integrity,
                "request_status": "SUCCESS" if daily_integrity["data_status"] == "VALID" and (daily_status == "downloaded" or (args.refresh_daily and daily_status in {"refreshed", "skipped"})) else "FAILED"
            }
            file_records.append(daily_record)
            if daily_record["request_status"] == "SUCCESS":
                successful_count += 1
            else:
                failed_count += 1

            if not args.daily_only:
                # Verify intraday file
                intraday_path = paths.bars_130min
                raw_path = paths.raw_5min
                intraday_integrity = verify_file_integrity(intraday_path)
                raw_integrity = verify_file_integrity(raw_path)
                intraday_coverage = v2_coverage_metadata(
                    intraday_path, "130min", symbol, args.start, args.end,
                    market_schedule=market_schedule, reviewed_exceptions=reviewed_exceptions,
                )
                intraday_success = (
                    intraday_integrity["data_status"] == "VALID"
                    and raw_integrity["data_status"] == "VALID"
                    and intraday_integrity["row_count"] > 0
                    and raw_integrity["row_count"] > 0
                )

                intraday_record = {
                    "symbol": symbol,
                    "asset_role": role,
                    "timeframe": "130min",
                    "requested_start": args.start,
                    "requested_end": args.end,
                    "request_end_semantics": "INCLUSIVE",
                    "feed": args.feed,
                    "adjustment": args.adjustment,
                    "output_relative_path": str(intraday_path.relative_to(output_dir)).replace("\\", "/"),
                    "acquisition_run_id": run_id,
                    "acquisition_action": "DERIVED_IN_CURRENT_RUN",
                    "downloaded_in_current_run": raw_status == "downloaded",
                    "provider_result_status": raw_status.upper(),
                    "raw_downloaded_in_current_run": raw_status == "downloaded",
                    "aggregation_result_status": aggregation_results.get(symbol, {}).get("bar_130min_status", "FAILED").upper(),
                    **intraday_coverage,
                    "derivation_type": "AGGREGATED_FROM_RAW_5MIN",
                    "source_raw_5min_relative_path": str(raw_path.relative_to(output_dir)).replace("\\", "/"),
                    "source_raw_5min_sha256": raw_integrity["sha256"],
                    "source_raw_5min_row_count": raw_integrity["row_count"],
                    "aggregation_function": "aggregate_to_130min",
                    "aggregation_schema_version": "1.0.0",
                    "aggregation_timezone": "America/New_York",
                    "derived_row_count": intraday_integrity["row_count"],
                    "derivation_status": "SUCCESS" if intraday_integrity["data_status"] == "VALID" and raw_integrity["data_status"] == "VALID" else "FAILED",
                    **intraday_integrity,
                    "request_status": "SUCCESS" if intraday_success and (raw_status == "downloaded" or (args.refresh_daily and raw_status in {"refreshed", "skipped"})) and (aggregation_results.get(symbol, {}).get("bar_130min_status") in {"built", "skipped"} if args.refresh_daily else aggregation_results.get(symbol, {}).get("bar_130min_status") == "built") else "FAILED"
                }
                file_records.append(intraday_record)
                if intraday_record["request_status"] == "SUCCESS":
                    successful_count += 1
                else:
                    failed_count += 1

        manifest_data["files"] = file_records
        manifest_data["successful_item_count"] = successful_count
        manifest_data["failed_item_count"] = failed_count
        manifest_data["acquisition_successful_item_count"] = successful_count
        manifest_data["acquisition_failed_item_count"] = failed_count
        manifest_data["coverage_valid_item_count"] = sum(row.get("coverage_status") == "VALID" for row in file_records)
        manifest_data["coverage_failed_item_count"] = sum(row.get("coverage_status") != "VALID" for row in file_records)
        manifest_data["daily_coverage_valid_count"] = sum(row.get("timeframe") == "1D" and row.get("coverage_status") == "VALID" for row in file_records)
        manifest_data["daily_coverage_failed_count"] = sum(row.get("timeframe") == "1D" and row.get("coverage_status") != "VALID" for row in file_records)
        manifest_data["intraday_coverage_valid_count"] = sum(row.get("timeframe") == "130min" and row.get("coverage_status") == "VALID" for row in file_records)
        manifest_data["intraday_coverage_failed_count"] = sum(row.get("timeframe") == "130min" and row.get("coverage_status") != "VALID" for row in file_records)
        telemetry_measured = all(item.get("retry_telemetry_status") == "MEASURED" for item in download_results.values())
        manifest_data["retry_telemetry_status"] = "MEASURED" if telemetry_measured else "UNAVAILABLE"
        if args.refresh_daily:
            manifest_data["retry_attempt_count"] = sum(item["retry_attempt_count"] for item in download_results.values()) if telemetry_measured else None
            manifest_data["provider_attempt_count"] = 358 + (manifest_data["retry_attempt_count"] or 0) if telemetry_measured else None
            manifest_data["failed_provider_attempt_count"] = manifest_data["retry_attempt_count"] if telemetry_measured else None
        else:
            manifest_data["provider_attempt_count"] = sum(item["provider_attempt_count"] for item in download_results.values()) if telemetry_measured else None
            manifest_data["retry_attempt_count"] = sum(item["retry_attempt_count"] for item in download_results.values()) if telemetry_measured else None
            manifest_data["failed_provider_attempt_count"] = sum(item["failed_provider_attempt_count"] for item in download_results.values()) if telemetry_measured else None

        write_v2_coverage_review_reports(file_records, BASE_DIR / "reports" / "latest", profile=args.dataset_profile)
        write_v2_applied_exceptions_report(file_records, BASE_DIR / "reports" / "latest", profile=args.dataset_profile)
        write_v2_range_shortfall_report(file_records, BASE_DIR / "reports" / "latest", profile=args.dataset_profile)

        manifest_data["status"] = "VALIDATION_PENDING"
        acquisition_report = {
            "acquisition_run_id": run_id,
            "dataset_profile": args.dataset_profile,
            "refresh_mode": refresh_mode,
            "symbol_count": len(symbols),
            "requested_start": args.start,
            "requested_end": args.end,
            "validation_status": "VALIDATION_PENDING",
            "acquisition_successful_item_count": successful_count,
            "acquisition_failed_item_count": failed_count,
            "coverage_valid_item_count": manifest_data["coverage_valid_item_count"],
            "coverage_failed_item_count": manifest_data["coverage_failed_item_count"],
        }
        finalize_v2_with_report(
            manifest_data,
            run_manifest_path,
            latest_manifest_path,
            run_manifest_dir,
            BASE_DIR / "reports" / "latest",
            acquisition_report,
            file_records,
            profile=args.dataset_profile,
        )
        print(f"Acquisition successfully validated. Published atomic latest manifest to {latest_manifest_path}")

    write_data_refresh_reports(BASE_DIR / "reports" / "latest", refresh_summary, records, timing_rows)

    print("Summary:", flush=True)
    for key, value in summary.items():
        print(f"{key}: {value}", flush=True)
    print(f"download_report_csv: {download_report_csv_path}", flush=True)
    print(f"data_refresh_report: {BASE_DIR / 'reports' / 'latest' / 'data_refresh_report.md'}", flush=True)
    if summary["errors"] >= len(symbols):
        raise SystemExit("All symbols failed; data refresh did not complete.")


if __name__ == "__main__":
    main()
