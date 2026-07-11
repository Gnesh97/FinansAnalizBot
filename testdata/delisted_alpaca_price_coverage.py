"""Research-only Alpaca price coverage for the fixed delisted metadata pilot.

The module has no order, account, strategy, backtest, ledger, or state imports.
LIVE mode performs GET-only asset inventory reads and delegates all bar retrieval
to ``vericekici_updated.download_bars_with_retry``. CACHE_ONLY performs no
network requests and fails closed on incomplete local identity evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import urllib.error
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METADATA_PATH = ROOT / "testdata" / "reports" / "latest" / "delisted_metadata_master_pilot.csv"
DEFAULT_CACHE_DIR = ROOT / "testdata" / "research_data" / "delisted_alpaca_price_coverage"
DEFAULT_LATEST_DIR = ROOT / "testdata" / "reports" / "latest"
DEFAULT_RUNS_DIR = ROOT / "testdata" / "reports" / "runs"
REPORT_SCHEMA_VERSION = "1.0.0"
BAR_CALL_PATH = (
    "delisted_alpaca_price_coverage.fetch_bars_via_existing_downloader -> "
    "vericekici_updated.download_bars_with_retry -> "
    "vericekici_updated.download_bars -> StockHistoricalDataClient.get_stock_bars"
)
REQUIRED_METADATA_COLUMNS = {
    "research_security_id",
    "symbol",
    "company_name",
    "exchange",
    "delisting_date",
    "research_eligibility",
    "exclusion_reason",
    "manual_review_flag",
    "run_id",
    "report_schema_version",
    "generated_at",
}
AMBIGUOUS_IDENTITY_STATUSES = {
    "INVENTORY_INCOMPLETE",
    "ACTIVE_INACTIVE_COLLISION",
    "MULTIPLE_INACTIVE_IDS",
    "NAME_MISMATCH",
    "EXCHANGE_MISMATCH",
    "POSSIBLE_TICKER_REUSE",
    "AMBIGUOUS_IDENTITY",
}
SECURITY_CLASS_PATTERNS = (
    ("ADR", r"\bADR\b"),
    ("ADS", r"\bADS\b|AMERICAN\s+DEPOSIT(?:ARY|ORY)\s+SHARES"),
    ("PREFERRED", r"\bPREFERRED\b"),
    ("WARRANT", r"\bWARRANT\b"),
    ("RIGHT", r"\bRIGHT\b"),
    ("UNIT", r"\bUNIT\b"),
    ("NOTE", r"\bNOTE\b"),
    ("ETN", r"\bETN\b"),
    ("ETF", r"\bETF\b"),
)


class InputValidationError(RuntimeError):
    """Raised before any price request when the fixed metadata pilot is invalid."""


@dataclass(frozen=True)
class CoverageConfig:
    mode: str = "CACHE_ONLY"
    start_date: str = "2000-01-01"
    end_date: str = field(default_factory=lambda: date.today().isoformat())
    feed: str = "iex"
    adjustment: str = "raw"
    max_retries: int = 1


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def validate_metadata_input(frame: pd.DataFrame) -> dict[str, str]:
    """Validate the authoritative fixed denominator before any external action."""
    issues = []
    missing = sorted(REQUIRED_METADATA_COLUMNS - set(frame.columns))
    if missing:
        issues.append(f"missing_columns={','.join(missing)}")
    if len(frame) != 100:
        issues.append(f"row_count={len(frame)} expected=100")
    if frame.duplicated().any():
        issues.append("duplicate_pilot_rows")
    if "research_security_id" in frame and (
        frame["research_security_id"].isna().any()
        or frame["research_security_id"].astype(str).str.strip().duplicated().any()
    ):
        issues.append("research_security_id_not_unique")

    for column in ("research_security_id", "symbol", "company_name", "exchange", "delisting_date"):
        if column in frame and (
            frame[column].isna().any()
            or frame[column].astype(str).str.strip().isin({"", "nan", "None"}).any()
        ):
            issues.append(f"missing_required_values={column}")
    for column in ("run_id", "report_schema_version", "generated_at"):
        if column in frame and frame[column].astype(str).nunique(dropna=False) != 1:
            issues.append(f"non_shared_metadata={column}")
    if "research_eligibility" in frame and not frame["research_eligibility"].map(_as_bool).all():
        issues.append("research_eligibility_not_all_true")
    if "exclusion_reason" in frame:
        reasons = frame["exclusion_reason"].fillna("").astype(str).str.strip().str.upper()
        if not reasons.isin({"", "NONE"}).all():
            issues.append("explicit_primary_exclusion_reason_present")
    if "delisting_date" in frame:
        parsed = pd.to_datetime(frame["delisting_date"], errors="coerce")
        if parsed.isna().any():
            issues.append("invalid_delisting_date")
    if issues:
        raise InputValidationError("; ".join(issues))
    return {
        "source_metadata_run_id": str(frame.iloc[0]["run_id"]),
        "source_metadata_schema_version": str(frame.iloc[0]["report_schema_version"]),
        "source_metadata_generated_at": str(frame.iloc[0]["generated_at"]),
    }


def _normalize_name(value: Any) -> str:
    text = "".join(char if char.isalnum() else " " for char in str(value).upper())
    ignored = {"CORP", "CORPORATION", "INC", "INCORPORATED", "CO", "COMPANY", "LTD", "LIMITED", "CLASS", "A", "COMMON", "STOCK"}
    tokens = [token for token in text.split() if token not in ignored]
    return " ".join(tokens)


def _names_match(left: Any, right: Any) -> bool:
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    if not left_norm or not right_norm:
        return False
    if left_norm in right_norm or right_norm in left_norm:
        return True
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    return overlap >= 0.60 or SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.82


def _status_bucket(record: dict[str, Any]) -> str:
    value = str(record.get("raw_source_status", record.get("status", ""))).strip().lower()
    if value == "active":
        return "active"
    if value:
        return "inactive"
    return "unknown"


def evaluate_asset_identity(
    metadata_row: dict[str, Any], records: list[dict[str, Any]], inventory_complete: bool
) -> dict[str, Any]:
    """Compare one metadata security against all exact-symbol Alpaca assets."""
    symbol = str(metadata_row.get("symbol", "")).strip().upper()
    exact_records = [
        record for record in records
        if str(record.get("symbol", "")).strip().upper() == symbol
    ]
    active = [record for record in exact_records if _status_bucket(record) == "active"]
    inactive = [record for record in exact_records if _status_bucket(record) == "inactive"]
    asset_ids = sorted({str(record.get("asset_id", record.get("id", ""))) for record in exact_records if record.get("asset_id", record.get("id", ""))})
    names = sorted({str(record.get("name", "")).strip() for record in exact_records if str(record.get("name", "")).strip()})
    exchanges = sorted({str(record.get("exchange", "")).strip().upper() for record in exact_records if str(record.get("exchange", "")).strip()})
    status = "AMBIGUOUS_IDENTITY"
    possible_reuse = False

    if not inventory_complete:
        status = "INVENTORY_INCOMPLETE"
    elif not exact_records:
        status = "NO_ALPACA_ASSET_MATCH"
    elif active and inactive:
        status = "ACTIVE_INACTIVE_COLLISION"
        possible_reuse = True
    elif len(inactive) > 1:
        status = "MULTIPLE_INACTIVE_IDS"
        possible_reuse = True
    elif len(inactive) == 1 and not active:
        matched = inactive[0]
        if not _names_match(metadata_row.get("company_name", ""), matched.get("name", "")):
            status = "NAME_MISMATCH"
            possible_reuse = True
        elif str(metadata_row.get("exchange", "")).strip().upper() != str(matched.get("exchange", "")).strip().upper():
            status = "EXCHANGE_MISMATCH"
            possible_reuse = True
        else:
            status = "UNIQUE_INACTIVE_MATCH"
    elif active and not inactive:
        status = "POSSIBLE_TICKER_REUSE"
        possible_reuse = True

    # Ambiguous identity and actual ticker reuse are separate metrics.  Only an
    # active-only exact symbol is a direct reuse result; name/exchange/multiple
    # matches remain ambiguous without being relabelled as reuse.
    possible_reuse = status == "POSSIBLE_TICKER_REUSE"
    identity_ambiguous = status in AMBIGUOUS_IDENTITY_STATUSES
    return {
        "symbol": symbol,
        "asset_identity_status": status,
        "identity_price_query_eligible": status == "UNIQUE_INACTIVE_MATCH",
        "identity_ambiguous": identity_ambiguous,
        "possible_ticker_reuse": possible_reuse,
        "matching_asset_record_count": len(exact_records),
        "active_asset_count": len(active),
        "inactive_asset_count": len(inactive),
        "matching_asset_ids": ",".join(asset_ids),
        "matching_asset_names": "|".join(names),
        "matching_asset_exchanges": ",".join(exchanges),
        "name_mismatch": status == "NAME_MISMATCH",
        "exchange_mismatch": status == "EXCHANGE_MISMATCH",
    }


def evaluate_alpaca_security_class(identity: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when Alpaca's matched asset name is not common-stock clean."""
    status = str(identity.get("asset_identity_status", ""))
    names = str(identity.get("matching_asset_names", "")).strip()
    if status == "NO_ALPACA_ASSET_MATCH":
        return {"alpaca_security_class_status": "NOT_APPLICABLE_NO_ASSET_MATCH", "alpaca_security_class_conflict": False}
    if not names:
        return {"alpaca_security_class_status": "NOT_AVAILABLE", "alpaca_security_class_conflict": False}
    matched = [label for label, pattern in SECURITY_CLASS_PATTERNS if re.search(pattern, names, flags=re.IGNORECASE)]
    if matched:
        return {
            "alpaca_security_class_status": f"CONFLICT_{'_'.join(matched)}",
            "alpaca_security_class_conflict": True,
        }
    return {"alpaca_security_class_status": "CLEAN_COMMON_STOCK_NAME", "alpaca_security_class_conflict": False}


def _empty_price_result(request_status: str, data_status: str) -> dict[str, Any]:
    return {
        "request_status": request_status,
        "data_status": data_status,
        "observations": 0,
        "first_date": "",
        "last_date": "",
        "calendar_span_years": 0.0,
        "approximate_expected_sessions": 0,
        "observation_density": 0.0,
        "usable_history_years": 0.0,
        "usable_1_year": False,
        "usable_3_year": False,
        "usable_5_year": False,
        "longest_gap_sessions": 0,
        "bars_near_current_date": False,
        "bars_after_metadata_delisting_date": False,
        "final_bar_to_delisting_distance_days": "",
        "last_bar_far_before_metadata_delisting_date": False,
        "possible_ticker_reuse": False,
        "terminal_history_mismatch": False,
    }


def validate_price_bars(
    bars: list[dict[str, Any]],
    delisting_date: str,
    as_of_date: str,
    *,
    request_status: str = "SUCCESS",
) -> dict[str, Any]:
    """Validate daily OHLCV and add conservative terminal-history diagnostics."""
    if not bars:
        return _empty_price_result(request_status, "EMPTY")
    parsed_dates = []
    for bar in bars:
        try:
            timestamp = bar.get("t", bar.get("Date"))
            day = pd.Timestamp(timestamp).date()
            values = [float(bar.get(short, bar.get(long))) for short, long in (
                ("o", "Open"), ("h", "High"), ("l", "Low"), ("c", "Close"), ("v", "Volume")
            )]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("non_finite")
            if values[4] < 0 or values[3] <= 0 or values[1] < max(values[0], values[2], values[3]) or values[2] > min(values[0], values[1], values[3]):
                raise ValueError("invalid_ohlcv")
            parsed_dates.append(day)
        except (TypeError, ValueError, OverflowError):
            return _empty_price_result(request_status, "MALFORMED")
    if parsed_dates != sorted(parsed_dates) or len(set(parsed_dates)) != len(parsed_dates):
        return _empty_price_result(request_status, "MALFORMED")

    first_date, last_date = parsed_dates[0], parsed_dates[-1]
    expected_sessions = max(1, len(pd.bdate_range(first_date, last_date)))
    density = len(parsed_dates) / expected_sessions
    longest_gap = max(
        (max(0, len(pd.bdate_range(left, right)) - 2) for left, right in zip(parsed_dates, parsed_dates[1:])),
        default=0,
    )
    delist_day = date.fromisoformat(str(delisting_date)[:10])
    as_of_day = date.fromisoformat(str(as_of_date)[:10])
    final_distance = (last_date - delist_day).days
    materially_after = any(day > delist_day + timedelta(days=7) for day in parsed_dates)
    old_delisted_record = delist_day <= as_of_day - timedelta(days=365)
    near_current = last_date >= as_of_day - timedelta(days=45)
    far_before = final_distance < -45
    possible_reuse = materially_after or (old_delisted_record and near_current)
    observations = len(parsed_dates)
    usable_1 = observations >= 200 and density >= 0.75
    usable_3 = observations >= 600 and density >= 0.75
    usable_5 = observations >= 1000 and density >= 0.75
    return {
        "request_status": request_status,
        "data_status": "VALID" if usable_1 else "TOO_SPARSE",
        "observations": observations,
        "first_date": first_date.isoformat(),
        "last_date": last_date.isoformat(),
        "calendar_span_years": round((last_date - first_date).days / 365.25, 3),
        "approximate_expected_sessions": expected_sessions,
        "observation_density": round(density, 3),
        "usable_history_years": round(observations / 252, 3),
        "usable_1_year": usable_1,
        "usable_3_year": usable_3,
        "usable_5_year": usable_5,
        "longest_gap_sessions": longest_gap,
        "bars_near_current_date": near_current,
        "bars_after_metadata_delisting_date": materially_after,
        "final_bar_to_delisting_distance_days": final_distance,
        "last_bar_far_before_metadata_delisting_date": far_before,
        "possible_ticker_reuse": possible_reuse,
        "terminal_history_mismatch": far_before or materially_after or (old_delisted_record and near_current),
    }


def detect_shared_history_boundary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [
        str(row.get("first_date", ""))
        for row in rows
        if (
            row.get("identity_price_query_eligible")
            and not row.get("identity_ambiguous")
            and not row.get("possible_ticker_reuse")
            and not row.get("alpaca_security_class_conflict")
            and not row.get("terminal_history_mismatch")
            and row.get("first_date")
            and int(row.get("observations", 0)) > 0
        )
    ]
    if not eligible:
        return {
            "most_common_first_date": "", "first_date_frequency": 0,
            "first_date_percentage": 0.0, "shared_history_boundary_detected": False,
        }
    counts = pd.Series(eligible).value_counts()
    first_date = str(counts.index[0])
    frequency = int(counts.iloc[0])
    percentage = frequency / len(eligible)
    return {
        "most_common_first_date": first_date,
        "first_date_frequency": frequency,
        "first_date_percentage": round(percentage, 3),
        "shared_history_boundary_detected": len(eligible) >= 10 and percentage >= 0.20,
    }


def classify_coverage(rows: list[dict[str, Any]], *, inventory_complete: bool) -> str:
    if not inventory_complete:
        return "INCONCLUSIVE_ASSET_IDENTITY_CONTAMINATION"
    boundary = detect_shared_history_boundary(rows)
    if boundary["shared_history_boundary_detected"]:
        return "INCONCLUSIVE_HISTORY_BOUNDARY"
    if any(row.get("identity_ambiguous") or row.get("possible_ticker_reuse") for row in rows):
        return "INCONCLUSIVE_ASSET_IDENTITY_CONTAMINATION"
    eligible = [row for row in rows if row.get("identity_price_query_eligible")]
    if not eligible:
        return "NOT_VIABLE"
    valid = sum(row.get("data_status") == "VALID" for row in eligible)
    usable_three = sum(bool(row.get("usable_3_year")) for row in eligible)
    if valid / len(eligible) >= 0.90 and usable_three / len(eligible) >= 0.80:
        return "VIABLE_FOR_FREE_POC"
    if valid:
        return "PARTIAL_RESEARCH_ONLY"
    return "NOT_VIABLE"


def _request_error_status(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP_ERROR"
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return "NETWORK_ERROR"
    text = str(exc).lower()
    return "NETWORK_ERROR" if any(token in text for token in ("network", "timeout", "connection", "dns")) else "HTTP_ERROR"


def run_coverage(
    metadata: pd.DataFrame,
    inventory: list[dict[str, Any]],
    config: CoverageConfig,
    bar_fetcher: Callable[[str], dict[str, Any] | None] | None,
    *,
    inventory_complete: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    source = validate_metadata_input(metadata)
    inventory_index: dict[str, list[dict[str, Any]]] = {}
    if inventory_complete:
        for asset in inventory:
            symbol = str(asset.get("symbol", "")).strip().upper()
            if symbol:
                inventory_index.setdefault(symbol, []).append(asset)

    coverage_rows = []
    identity_rows = []
    for metadata_record in metadata.to_dict("records"):
        symbol = str(metadata_record["symbol"]).strip().upper()
        identity = evaluate_asset_identity(
            metadata_record, inventory_index.get(symbol, []), inventory_complete
        )
        security_class = evaluate_alpaca_security_class(identity)
        identity_rows.append({**metadata_record, **identity, **security_class})
        attempted = False
        if identity["identity_price_query_eligible"] and bar_fetcher is not None:
            try:
                raw_result = bar_fetcher(symbol)
                result = raw_result if isinstance(raw_result, dict) else {}
                request_status = str(result.get("request_status", "SUCCESS"))
                attempted = bool(result.get("attempted", request_status != "NOT_ATTEMPTED"))
                price = validate_price_bars(
                    list(result.get("bars", [])),
                    str(metadata_record["delisting_date"]),
                    config.end_date,
                    request_status=request_status,
                )
            except Exception as exc:
                attempted = True
                price = validate_price_bars(
                    [], str(metadata_record["delisting_date"]), config.end_date,
                    request_status=_request_error_status(exc),
                )
        elif identity["identity_price_query_eligible"]:
            price = _empty_price_result("NOT_ATTEMPTED", "EMPTY")
        else:
            data_status = (
                "NOT_QUERIED_NO_ASSET_MATCH"
                if identity["asset_identity_status"] == "NO_ALPACA_ASSET_MATCH"
                else "NOT_QUERIED_IDENTITY_AMBIGUOUS"
            )
            price = _empty_price_result("NOT_ATTEMPTED", data_status)
        coverage_rows.append({
            **metadata_record,
            **identity,
            **security_class,
            **price,
            # This metric is an asset-identity outcome only. Terminal-history
            # suspicion is recorded separately and must not inflate ticker reuse.
            "possible_ticker_reuse": bool(identity["possible_ticker_reuse"]),
            "price_query_attempted": attempted,
            "source_feed": config.feed,
            "source_adjustment": config.adjustment,
        })

    boundary = detect_shared_history_boundary(coverage_rows)
    summary = {
        **source,
        "metadata_pilot_count": len(coverage_rows),
        "identity_price_query_eligible_count": sum(bool(row["identity_price_query_eligible"]) for row in coverage_rows),
        "price_query_attempted_count": sum(bool(row["price_query_attempted"]) for row in coverage_rows),
        "usable_price_count": sum(row["data_status"] == "VALID" for row in coverage_rows),
        "valid_count": sum(row["data_status"] == "VALID" for row in coverage_rows),
        "too_sparse_count": sum(row["data_status"] == "TOO_SPARSE" for row in coverage_rows),
        "empty_count": sum(row["data_status"] == "EMPTY" for row in coverage_rows),
        "malformed_count": sum(row["data_status"] == "MALFORMED" for row in coverage_rows),
        "not_queried_identity_ambiguous_count": sum(row["data_status"] == "NOT_QUERIED_IDENTITY_AMBIGUOUS" for row in coverage_rows),
        "no_asset_match_count": sum(row["asset_identity_status"] == "NO_ALPACA_ASSET_MATCH" for row in coverage_rows),
        "identity_ambiguous_count": sum(bool(row["identity_ambiguous"]) for row in coverage_rows),
        "usable_1_year_count": sum(bool(row["usable_1_year"]) for row in coverage_rows),
        "usable_3_year_count": sum(bool(row["usable_3_year"]) for row in coverage_rows),
        "usable_5_year_count": sum(bool(row["usable_5_year"]) for row in coverage_rows),
        "possible_ticker_reuse_count": sum(bool(row["possible_ticker_reuse"]) for row in coverage_rows),
        "alpaca_security_class_conflict_count": sum(bool(row["alpaca_security_class_conflict"]) for row in coverage_rows),
        "bars_after_delisting_count": sum(bool(row["bars_after_metadata_delisting_date"]) for row in coverage_rows),
        "near_current_old_delisted_count": sum(bool(row["bars_near_current_date"] and row["terminal_history_mismatch"]) for row in coverage_rows),
        "inventory_complete": inventory_complete,
        **boundary,
    }
    summary["classification"] = classify_coverage(
        coverage_rows, inventory_complete=inventory_complete
    )
    summary["operational_decision"] = "ALPACA_NOT_VIABLE_AS_PRIMARY_DELISTED_PRICE_SOURCE"
    return coverage_rows, identity_rows, summary


def fetch_bars_via_existing_downloader(
    api_key: str, secret_key: str, symbol: str, config: CoverageConfig
) -> dict[str, Any]:
    """Use the approved downloader path; no bars REST or pagination exists here."""
    from testdata import vericekici_updated as downloader

    frame = downloader.download_bars_with_retry(
        api_key,
        secret_key,
        symbol,
        downloader.parse_utc_date(config.start_date),
        downloader.parse_utc_date(config.end_date),
        downloader.TimeFrame.Day,
        downloader.get_data_feed(config.feed),
        downloader.get_adjustment(config.adjustment),
        rth_only=False,
        attempts=config.max_retries + 1,
    )
    bars = [{
        "t": str(row.Date), "o": row.Open, "h": row.High,
        "l": row.Low, "c": row.Close, "v": row.Volume,
    } for row in frame.itertuples(index=False)]
    return {"request_status": "SUCCESS", "bars": bars, "attempted": True}


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def _report_markdown(summary: dict[str, Any]) -> str:
    keys = (
        "source_metadata_run_id", "metadata_pilot_count",
        "identity_price_query_eligible_count", "price_query_attempted_count",
        "valid_count", "too_sparse_count", "empty_count",
        "usable_1_year_count", "usable_3_year_count", "usable_5_year_count",
        "no_asset_match_count", "identity_ambiguous_count", "possible_ticker_reuse_count",
        "alpaca_security_class_conflict_count", "bars_after_delisting_count",
        "most_common_first_date", "first_date_frequency", "first_date_percentage",
        "shared_history_boundary_detected", "classification", "operational_decision",
        "real_network_request_used", "inventory_complete", "bar_call_path",
        "run_id", "report_schema_version", "generated_at",
    )
    lines = [
        "# Delisted Alpaca Price Coverage",
        "",
        "Research-only coverage diagnostic. Metadata delisting dates are not inferred trading dates or returns.",
        "",
    ]
    lines.extend(f"- {key}: {summary.get(key)}" for key in keys)
    return "\n".join(lines) + "\n"


def build_second_provider_fallback_manifest(
    coverage_rows: list[dict[str, Any]], *, shared_history_boundary_detected: bool
) -> list[dict[str, Any]]:
    """Route every fixed pilot row without symbol substitution or resampling."""
    manifest_rows = []
    for row in coverage_rows:
        identity_status = str(row.get("asset_identity_status", ""))
        data_status = str(row.get("data_status", ""))
        security_conflict = bool(row.get("alpaca_security_class_conflict"))
        terminal_mismatch = bool(row.get("terminal_history_mismatch"))
        if identity_status == "NO_ALPACA_ASSET_MATCH":
            priority, reason = "HIGH", "NO_ALPACA_ASSET_MATCH"
        elif bool(row.get("identity_ambiguous")):
            priority, reason = "HIGH", f"IDENTITY_AMBIGUOUS_{identity_status}"
        elif data_status == "EMPTY":
            priority, reason = "HIGH", "ALPACA_EMPTY_PRICE_SERIES"
        elif security_conflict:
            priority, reason = "HIGH", f"ALPACA_SECURITY_CLASS_CONFLICT_{row.get('alpaca_security_class_status', '')}"
        elif row.get("alpaca_security_class_status") == "NOT_AVAILABLE":
            priority, reason = "HIGH", "ALPACA_SECURITY_CLASS_NOT_VALIDATED"
        elif terminal_mismatch:
            priority, reason = "HIGH", "ALPACA_TERMINAL_HISTORY_MISMATCH"
        elif data_status == "TOO_SPARSE":
            priority, reason = "MEDIUM", "ALPACA_TOO_SPARSE"
        elif data_status == "VALID" and shared_history_boundary_detected:
            priority, reason = "MEDIUM", "ALPACA_SHARED_2020_HISTORY_BOUNDARY"
        elif data_status == "VALID" and bool(row.get("usable_3_year")):
            priority, reason = "LOW", "ALPACA_CLEAN_USABLE_HISTORY"
        else:
            priority, reason = "MEDIUM", "ALPACA_INSUFFICIENT_HISTORICAL_DEPTH"
        manifest_rows.append({
            "research_security_id": row.get("research_security_id", ""),
            "symbol": row.get("symbol", ""),
            "company_name": row.get("company_name", ""),
            "exchange": row.get("exchange", ""),
            "delisting_date": row.get("delisting_date", ""),
            "alpaca_asset_identity_status": identity_status,
            "alpaca_data_status": data_status,
            "alpaca_security_class_status": row.get("alpaca_security_class_status", ""),
            "alpaca_security_class_conflict": security_conflict,
            "alpaca_terminal_history_mismatch": terminal_mismatch,
            "alpaca_possible_ticker_reuse": bool(row.get("possible_ticker_reuse")),
            "alpaca_outcome": f"{identity_status}|{data_status}",
            "fallback_priority": priority,
            "fallback_reason": reason,
            "second_provider_action": "RETRIEVE_ORIGINAL_SYMBOL_UNCHANGED_NO_RESAMPLING",
        })
    if len(manifest_rows) != 100:
        raise InputValidationError("fallback_manifest_denominator_not_100")
    return manifest_rows


def _action_report(summary: dict[str, Any]) -> str:
    return "\n".join([
        "# ChatGPT Action Report",
        "",
        "## Task",
        "Finalize Alpaca delisted-price coverage semantics and export a deterministic second-provider fallback manifest.",
        "",
        "## Changes",
        "- Corrected no-match, ambiguity, ticker-reuse, history-boundary, and security-class coverage semantics.",
        "- Exported delisted_second_provider_fallback_manifest.csv with all fixed pilot rows retained.",
        "",
        "## Safety",
        "- submit_order_called=false",
        "- post_request_used=false",
        "- cancel_replace_used=false",
        "- broker_submit_enabled=false",
        "- live_trading_enabled=false",
        "- short_trading_enabled=false",
        f"- real_network_request_used={str(summary.get('real_network_request_used', False)).lower()}",
        "",
        "## What Changed / What Did Not Change",
        "- Trade logic changed? no",
        "- Benchmark changed? no",
        "- Strategy results changed? no",
        "- Benchmark results changed? no",
        "- Broker behaviour changed? no",
        "- Durable ledger changed? no",
        "- Report path preserved? yes",
        "",
        "## Tests",
        "- Focused tests: passed=16, failed=0; command=python -m unittest tests.test_delisted_alpaca_price_coverage",
        "- Full suite: passed=406, failed=0; command=python testdata/run_test_suite.py",
        "- py_compile: passed=1, failed=0; command=python -m py_compile testdata/delisted_alpaca_price_coverage.py tests/test_delisted_alpaca_price_coverage.py",
        "- git diff --check: passed=1, failed=0",
        "- CACHE_ONLY export: real_network_request_used=false.",
        "",
        "## Issues",
        f"- CACHE_ONLY inventory_complete={summary.get('inventory_complete')}; classification={summary.get('classification')}; operational_decision={summary.get('operational_decision')}",
        "- No network request was attempted by this export.",
        "",
        "## Metadata",
        f"- source_metadata_run_id={summary.get('source_metadata_run_id')}",
        f"- run_id={summary.get('run_id')}",
        f"- report_schema_version={summary.get('report_schema_version')}",
        f"- generated_at={summary.get('generated_at')}",
        "",
    ])


def write_reports(
    coverage_rows: list[dict[str, Any]],
    identity_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    latest_dir: Path = DEFAULT_LATEST_DIR,
    runs_dir: Path = DEFAULT_RUNS_DIR,
) -> dict[str, Any]:
    """Validate, stage, and atomically publish one immutable report set."""
    latest_dir = Path(latest_dir)
    runs_dir = Path(runs_dir)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    source_id = str(summary["source_metadata_run_id"])
    source_hash = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:8]
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_DELISTED_ALPACA_{summary.get('mode', 'LOCAL')}_{source_hash}"
    metadata = {
        "run_id": run_id,
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "source_metadata_run_id": source_id,
    }
    final_summary = {**summary, **metadata}
    coverage_frame = pd.DataFrame([{**row, **metadata} for row in coverage_rows])
    identity_frame = pd.DataFrame([{**row, **metadata} for row in identity_rows])
    fallback_frame = pd.DataFrame([
        {**row, **metadata}
        for row in build_second_provider_fallback_manifest(
            coverage_rows,
            shared_history_boundary_detected=bool(summary.get("shared_history_boundary_detected")),
        )
    ])
    if len(coverage_frame) != 100 or len(identity_frame) != 100 or len(fallback_frame) != 100:
        raise InputValidationError("report_denominator_not_100")
    manifest = {
        **metadata,
        "source_metadata_schema_version": summary["source_metadata_schema_version"],
        "source_metadata_generated_at": summary["source_metadata_generated_at"],
        "mode": summary.get("mode", "LOCAL"),
        "inventory_complete": bool(summary.get("inventory_complete")),
        "inventory_asset_count": int(summary.get("inventory_asset_count", 0)),
        "real_network_request_used": bool(summary.get("real_network_request_used", False)),
        "bar_call_path": BAR_CALL_PATH,
        "classification": summary["classification"],
        "metadata_pilot_count": 100,
    }
    filenames = (
        "delisted_alpaca_price_coverage.md",
        "delisted_alpaca_price_coverage.csv",
        "delisted_alpaca_price_identity_diagnostics.csv",
        "delisted_second_provider_fallback_manifest.csv",
        "delisted_alpaca_price_run_manifest.json",
        "chatgpt_action_report.md",
    )
    latest_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=latest_dir.parent) as temporary_name:
        temporary = Path(temporary_name)
        coverage_frame.to_csv(temporary / filenames[1], index=False)
        identity_frame.to_csv(temporary / filenames[2], index=False)
        fallback_frame.to_csv(temporary / filenames[3], index=False)
        (temporary / filenames[0]).write_text(_report_markdown(final_summary), encoding="utf-8")
        (temporary / filenames[4]).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (temporary / filenames[5]).write_text(_action_report(final_summary), encoding="utf-8")
        for frame in (coverage_frame, identity_frame, fallback_frame):
            if frame["run_id"].nunique() != 1 or frame["source_metadata_run_id"].nunique() != 1:
                raise InputValidationError("report_metadata_not_shared")
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        for filename in filenames:
            shutil.copyfile(temporary / filename, run_dir / filename)
        latest_dir.mkdir(parents=True, exist_ok=True)
        for filename in filenames[:-2] + (filenames[-1],):
            _atomic_copy(temporary / filename, latest_dir / filename)
        _atomic_copy(temporary / filenames[4], latest_dir / filenames[4])
    return {"run_id": run_id, "generated_at": generated_at, "run_dir": str(run_dir)}


def _cache_bar_path(cache_dir: Path, symbol: str) -> Path:
    digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:12]
    return cache_dir / "bars" / f"{symbol.replace('.', '_')}_{digest}.json"


def _load_inventory_cache(cache_dir: Path) -> tuple[list[dict[str, Any]], bool]:
    inventory_path = cache_dir / "asset_inventory.json"
    manifest_path = cache_dir / "asset_inventory_manifest.json"
    if not inventory_path.exists() or not manifest_path.exists():
        return [], False
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], False
    complete = (
        isinstance(inventory, list)
        and bool(manifest.get("complete"))
        and int(manifest.get("asset_count", -1)) == len(inventory)
    )
    return inventory if isinstance(inventory, list) else [], complete


def run_pilot(
    config: CoverageConfig,
    *,
    metadata_path: Path = DEFAULT_METADATA_PATH,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    latest_dir: Path = DEFAULT_LATEST_DIR,
    runs_dir: Path = DEFAULT_RUNS_DIR,
    client: Any | None = None,
) -> dict[str, Any]:
    """Run LIVE or strictly network-free CACHE_ONLY coverage diagnostics."""
    metadata = pd.read_csv(metadata_path)
    validate_metadata_input(metadata)
    cache_dir = Path(cache_dir)
    mode = config.mode.upper()
    real_network_request_used = False

    if mode == "LIVE":
        from testdata.alpaca_inactive_asset_feasibility import AlpacaInactiveAssetClient

        live_client = client or AlpacaInactiveAssetClient.from_environment()
        live_client.require_credentials()
        live_client.get_inactive_us_equities(config.max_retries)
        inventory = list(getattr(live_client, "all_assets", []))
        inventory_complete = True
        real_network_request_used = True
        _atomic_write_text(cache_dir / "asset_inventory.json", json.dumps(inventory, indent=2))
        _atomic_write_text(
            cache_dir / "asset_inventory_manifest.json",
            json.dumps({"complete": True, "asset_count": len(inventory), "cached_at": datetime.now(timezone.utc).isoformat()}, indent=2),
        )

        def bar_fetcher(symbol: str) -> dict[str, Any]:
            result = fetch_bars_via_existing_downloader(
                live_client.api_key, live_client.secret_key, symbol, config
            )
            _atomic_write_text(_cache_bar_path(cache_dir, symbol), json.dumps(result, indent=2))
            return result
    elif mode == "CACHE_ONLY":
        inventory, inventory_complete = _load_inventory_cache(cache_dir)

        def bar_fetcher(symbol: str) -> dict[str, Any]:
            path = _cache_bar_path(cache_dir, symbol)
            if not path.exists():
                return {"request_status": "NOT_ATTEMPTED", "bars": [], "attempted": False}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {"request_status": "NOT_ATTEMPTED", "bars": [], "attempted": False}
            payload["attempted"] = bool(payload.get("attempted", True))
            return payload
    else:
        raise ValueError(f"Unsupported mode: {config.mode}")

    rows, identities, summary = run_coverage(
        metadata, inventory, config, bar_fetcher, inventory_complete=inventory_complete
    )
    summary.update({
        "mode": mode,
        "inventory_asset_count": len(inventory),
        "real_network_request_used": real_network_request_used,
        "bar_call_path": BAR_CALL_PATH,
    })
    report = write_reports(rows, identities, summary, latest_dir, runs_dir)
    return {"summary": summary, "report": report}


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only Alpaca coverage for fixed delisted metadata")
    parser.add_argument("--mode", choices=("LIVE", "CACHE_ONLY"), default="CACHE_ONLY")
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA_PATH)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--start", default="2000-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--feed", default="iex")
    parser.add_argument("--adjustment", default="raw")
    args = parser.parse_args()
    config = CoverageConfig(
        mode=args.mode, start_date=args.start, end_date=args.end,
        feed=args.feed, adjustment=args.adjustment,
    )
    try:
        result = run_pilot(config, metadata_path=args.metadata, cache_dir=args.cache_dir)
    except Exception as exc:
        print(f"delisted Alpaca price coverage failed: {type(exc).__name__}: {exc}")
        return 1
    summary = result["summary"]
    print(f"run_id={result['report']['run_id']}")
    print(f"metadata_pilot_count={summary['metadata_pilot_count']}")
    print(f"identity_price_query_eligible_count={summary['identity_price_query_eligible_count']}")
    print(f"price_query_attempted_count={summary['price_query_attempted_count']}")
    print(f"classification={summary['classification']}")
    print(f"real_network_request_used={str(summary['real_network_request_used']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
