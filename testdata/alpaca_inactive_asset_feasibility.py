"""GET-only research probe for Alpaca inactive US-equity metadata and daily bars.

This module is intentionally isolated from order, account, paper-state, and strategy code.
It measures API coverage; it never infers a delisting date or delisting return.
"""
from __future__ import annotations

import argparse
import csv
import errno
import hashlib
import json
import math
import os
import re
import socket
import ssl
import statistics
import time
import urllib.error
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import parse, request


TRADING_BASE_URL = "https://paper-api.alpaca.markets"
LOCAL_RESEARCH_DATA_DIR = Path(__file__).resolve().parent / "research_data" / "alpaca_inactive_feasibility"
REPORT_DIR = Path(__file__).resolve().parent / "reports" / "latest"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SEED = "alpaca-inactive-asset-feasibility-v1"
CLASSIFICATIONS = (
    "ticker_like_common_stock", "identifier_like", "contingent_value_right", "etf_fund",
    "preferred", "warrant", "unit", "right", "adr_foreign",
    "otc_unsupported_exchange", "unknown", "etn", "leveraged_inverse",
    "notes", "bonds", "depositary_shares", "spac_acquisition",
    "when_issued", "paired_shares",
)


def _read_project_env() -> dict[str, str]:
    """Use project's existing read-only root `.env` parser without mutating os.environ."""
    try:
        from .runner import _read_env_file
    except ImportError:
        from runner import _read_env_file
    return _read_env_file(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class FeasibilityConfig:
    start_date: str = "2000-01-01"
    end_date: str = "2026-07-10"
    feed: str = "iex"
    adjustment: str = "raw"
    sample_size: int = 100
    batch_size: int = 20
    max_retries: int = 1


class RequestStageError(RuntimeError):
    def __init__(self, stage: str, cause: Exception):
        super().__init__(stage)
        self.stage = stage
        self.cause = cause


class SampleConstructionError(RuntimeError):
    def __init__(self, issues: list[str]):
        super().__init__("INCONCLUSIVE_SAMPLE_CONSTRUCTION_DEFECT")
        self.issues = issues


class AlpacaInactiveAssetClient:
    """Small HTTP client that constructs GET requests only."""

    def __init__(self, api_key: str = "", secret_key: str = "", *, credential_variables_used: tuple[str, str] = (), urlopen: Callable[..., Any] | None = None):
        self.api_key = api_key or ""
        self.secret_key = secret_key or ""
        self.credential_variables_used = credential_variables_used
        self.urlopen = urlopen or request.urlopen
        self.methods_used: list[str] = []
        self.inventory_status_counts: dict[str, int] = {}
        self.inventory_asset_count = 0
        self.inactive_asset_count = 0
        self.bar_download_invocations = 0

    @classmethod
    def from_environment(cls, *, urlopen: Callable[..., Any] | None = None) -> "AlpacaInactiveAssetClient":
        values = _read_project_env()
        api_key = os.getenv("ALPACA_API_KEY") or values.get("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY") or values.get("ALPACA_SECRET_KEY", "")
        if api_key and secret_key:
            return cls(api_key, secret_key, credential_variables_used=("ALPACA_API_KEY", "ALPACA_SECRET_KEY"), urlopen=urlopen)
        return cls(
            os.getenv("APCA_API_KEY_ID") or values.get("APCA_API_KEY_ID", ""),
            os.getenv("APCA_API_SECRET_KEY") or values.get("APCA_API_SECRET_KEY", ""),
            credential_variables_used=("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"),
            urlopen=urlopen,
        )

    def require_credentials(self) -> None:
        if not self.api_key or not self.secret_key:
            raise RuntimeError("Missing Alpaca credentials: set ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables.")

    def _get_json(self, base_url: str, path: str, query: dict[str, Any]) -> Any:
        self.require_credentials()
        url = f"{base_url}{path}?{parse.urlencode(query, doseq=True)}"
        req = request.Request(url, headers={"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.secret_key, "Accept": "application/json"}, method="GET")
        self.methods_used.append("GET")
        with self.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def _retry_get(self, base_url: str, path: str, query: dict[str, Any], max_retries: int) -> Any:
        for attempt in range(max_retries + 1):
            try:
                return self._get_json(base_url, path, query)
            except urllib.error.HTTPError as exc:
                exc.sanitized_detail = sanitize_http_error(exc)
                exc.sanitized_request_context = sanitize_request_context(base_url, path, query)
                if exc.code in {400, 401, 403, 404, 429} or attempt >= max_retries:
                    raise
            except (urllib.error.URLError, TimeoutError) as exc:
                exc.sanitized_request_context = sanitize_request_context(base_url, path, query)
                if attempt >= max_retries:
                    raise
            time.sleep(0.25 * (attempt + 1))
        raise RuntimeError("unreachable")

    def get_inactive_us_equities(self, max_retries: int = 1) -> list[dict[str, Any]]:
        payload = self._retry_get(TRADING_BASE_URL, "/v2/assets", {"asset_class": "us_equity"}, max_retries)
        if not isinstance(payload, list):
            raise ValueError("Inactive asset response must be a JSON list.")
        records = [_asset_record(row) for row in payload if isinstance(row, dict)]
        counts = {"active": 0, "inactive": 0, "non_active": 0, "unknown": 0}
        inactive = []
        for row in records:
            bucket = _asset_status_bucket(row["raw_source_status"])
            counts[bucket] += 1
            if bucket in {"inactive", "non_active"}:
                inactive.append(row)
        self.inventory_status_counts = {name: count for name, count in counts.items() if count}
        self.inventory_asset_count = len(records)
        self.all_assets = records
        self.inactive_asset_count = len(inactive)
        return sorted(inactive, key=lambda row: (row["symbol"], row["asset_id"]))

    def get_daily_bars(self, symbols: list[str], start_date: str, end_date: str, feed: str, adjustment: str, max_retries: int = 1) -> dict[str, list[dict[str, Any]]]:
        self.bar_download_invocations += 1
        try:
            return _download_daily_bars_via_existing_downloader(self.api_key, self.secret_key, symbols, start_date, end_date, feed, adjustment, max_retries)
        except urllib.error.HTTPError as exc:
            exc.sanitized_detail = sanitize_http_error(exc)
            exc.sanitized_request_context = {"endpoint_hostname": "data.alpaca.markets", "endpoint_path": "SDK StockBarsRequest", "query_parameter_names": ["symbol_or_symbols", "timeframe", "start", "end", "feed", "adjustment"], "query_values": {}}
            raise


def _download_daily_bars_via_existing_downloader(api_key: str, secret_key: str, symbols: list[str], start_date: str, end_date: str, feed: str, adjustment: str, max_retries: int) -> dict[str, list[dict[str, Any]]]:
    """Lazy reuse of the production downloader; importing it is now import-safe."""
    try:
        try:
            from . import vericekici_updated as downloader
        except ImportError:
            import vericekici_updated as downloader
    except ImportError as e:
        raise RuntimeError(f"Could not import vericekici_updated: {e}") from e

    start = downloader.parse_utc_date(start_date)
    end = downloader.parse_utc_date(end_date)
    timeframe = downloader.TimeFrame.Day
    data_feed = downloader.get_data_feed(feed)
    data_adjustment = downloader.get_adjustment(adjustment)
    result: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        download_result = downloader.download_bars_with_retry(
            api_key, secret_key, symbol, start, end, timeframe, data_feed, data_adjustment,
            rth_only=False, attempts=max_retries + 1
        )
        frame = getattr(download_result, "frame", download_result)
        result[symbol] = [
            {"t": str(row.Date), "o": row.Open, "h": row.High, "l": row.Low, "c": row.Close, "v": row.Volume}
            for row in frame.itertuples(index=False)
        ]
    return result


def _asset_record(row: dict[str, Any]) -> dict[str, Any]:
    result = {
        "asset_id": str(row.get("id", "")), "symbol": str(row.get("symbol", "")).upper(), "name": str(row.get("name", "")),
        "exchange": str(row.get("exchange", "")), "status": str(row.get("status", "")), "asset_class": str(row.get("class", row.get("asset_class", ""))),
        "tradable": bool(row.get("tradable", False)), "marginable": bool(row.get("marginable", False)), "shortable": bool(row.get("shortable", False)),
        "fractionable": bool(row.get("fractionable", False)), "easy_to_borrow": bool(row.get("easy_to_borrow", False)),
        "maintenance_margin_requirement": row.get("maintenance_margin_requirement", ""), "raw_source_status": str(row.get("status", "")),
        "created_at": str(row.get("created_at", "")),
    }
    classification, reason, inst_type, eligibility, excl_reason = classify_asset_with_details(result)
    result["classification"] = classification
    result["classification_reason"] = reason
    result["instrument_type"] = inst_type
    result["research_eligibility"] = eligibility
    result["exclusion_reason"] = excl_reason
    return result


def _asset_status_bucket(status: object) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "active":
        return "active"
    if normalized == "inactive":
        return "inactive"
    if normalized:
        return "non_active"
    return "unknown"


def sanitize_http_error(exc: urllib.error.HTTPError, body: bytes | None = None) -> dict[str, object]:
    """Keep small API diagnostics while never preserving headers or credentials."""
    raw = body
    if raw is None:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
    payload: dict[str, object] = {}
    try:
        decoded = json.loads((raw or b"").decode("utf-8", errors="replace"))
        payload = decoded if isinstance(decoded, dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    code = payload.get("code", payload.get("error_code", ""))
    message = _sanitize_api_message(payload.get("message", payload.get("error", "")))
    return {"http_status": exc.code, "error_code": str(code)[:40], "message": message}


def sanitize_request_context(base_url: str, path: str, query: dict[str, Any]) -> dict[str, object]:
    hostname = parse.urlparse(base_url).hostname or ""
    values = {}
    for key, value in sorted(query.items()):
        key_text = str(key)
        values[key_text] = "redacted" if re.search(r"(?i)(key|secret|token|authorization)", key_text) else _sanitize_api_message(value)
    return {"endpoint_hostname": hostname, "endpoint_path": path, "query_parameter_names": sorted(values), "query_values": values}


def _sanitize_api_message(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if re.search(r"(?i)(authorization|bearer|api[ _-]?key|secret|token)", text):
        return "redacted"
    return text[:160]


def normalize_company_name(name: str) -> str:
    name = str(name).upper().strip()
    name = re.sub(r"[.,\-/\(\)]", " ", name)
    tokens = name.split()
    designators = {
        "CORP", "CORPORATION", "INC", "INCORPORATED", "LTD", "LIMITED",
        "CO", "COMPANY", "PLC", "SA", "LLC", "CLASS", "A", "B", "CL", "COMMON", "STOCK", "SHARES", "SHS", "ORD"
    }
    clean_tokens = [t for t in tokens if t not in designators]
    if not clean_tokens:
        clean_tokens = tokens
    return " ".join(clean_tokens)


def check_name_conflict(names: list[str]) -> bool:
    if len(names) <= 1:
        return False
    normalized = [normalize_company_name(n) for n in names]
    unique_normalized = set(normalized)
    return len(unique_normalized) > 1


def classify_symbol_identity(symbol: str, records: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    raw_count = len(records)
    active_records = []
    inactive_records = []
    for r in records:
        status_raw = str(r.get("raw_source_status", r.get("status", "")))
        status_bucket = _asset_status_bucket(status_raw)
        if status_bucket == "active":
            active_records.append(r)
        elif status_bucket in ("inactive", "non_active"):
            inactive_records.append(r)
    active_count = len(active_records)
    inactive_count = len(inactive_records)
    
    asset_ids = sorted(list({str(r.get("asset_id", r.get("id", ""))) for r in records if r.get("asset_id", r.get("id", ""))}))
    names = sorted(list({str(r.get("name", "")).strip() for r in records if r.get("name", "")}))
    exchanges = sorted(list({str(r.get("exchange", "")).strip() for r in records if r.get("exchange", "")}))
    
    conflict_names = check_name_conflict(names)
    conflict_exchanges = len(exchanges) > 1
    
    status = "IDENTITY_UNKNOWN"
    possible_ticker_reuse = False
    
    if raw_count == 0:
        status = "IDENTITY_UNKNOWN"
    elif active_count > 0 and inactive_count > 0:
        status = "ACTIVE_INACTIVE_COLLISION"
        possible_ticker_reuse = True
    elif conflict_names:
        status = "NAME_MISMATCH"
        possible_ticker_reuse = True
    elif conflict_exchanges:
        status = "EXCHANGE_MISMATCH"
        possible_ticker_reuse = True
    elif inactive_count > 1:
        status = "MULTIPLE_INACTIVE_ASSET_IDS"
        possible_ticker_reuse = True
    elif inactive_count == 1 and active_count == 0:
        status = "UNIQUE_INACTIVE_SYMBOL"
        
    details = {
        "asset_identity_status": status,
        "matching_asset_record_count": raw_count,
        "active_record_count": active_count,
        "inactive_record_count": inactive_count,
        "conflicting_asset_ids": ",".join(asset_ids),
        "conflicting_names": "|".join(names),
        "conflicting_exchanges": ",".join(exchanges),
        "possible_ticker_reuse": possible_ticker_reuse,
    }
    return status, details


def classify_asset(row: dict[str, Any]) -> str:
    return classify_asset_with_reason(row)[0]


def classify_asset_with_reason(row: dict[str, Any]) -> tuple[str, str]:
    res = classify_asset_with_details(row)
    return res[0], res[1]


def classify_asset_with_details(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    name = str(row.get("name", "")).upper().strip()
    symbol = str(row.get("symbol", "")).upper().strip()
    exchange = str(row.get("exchange", "")).upper().strip()

    # OTC exchange
    if exchange == "OTC":
        return "otc_unsupported_exchange", "otc_exchange", "common_stock", "ineligible", "otc_unsupported"

    # CVR / Contra / Escrow / Tender
    if "CVR" in symbol or "CVR" in name or "CONTINGENT" in name or "VALUE RIGHT" in name:
        return "contingent_value_right", "cvr_metadata", "cvr", "ineligible", "cvr_contra_escrow_tender"
    if "CONTRA" in name or "CONTRA" in symbol:
        return "contingent_value_right", "contra_metadata", "contra", "ineligible", "cvr_contra_escrow_tender"
    if "ESCROW" in name or "ESCROW" in symbol or "ESC" in symbol:
        return "contingent_value_right", "escrow_metadata", "escrow", "ineligible", "cvr_contra_escrow_tender"
    if "TENDER" in name or "TENDER" in symbol:
        return "contingent_value_right", "tender_metadata", "tender", "ineligible", "cvr_contra_escrow_tender"

    # ETN
    if "ETN" in symbol or "ETN" in name or "EXCHANGE TRADED NOTE" in name or "EXCHANGE-TRADED NOTE" in name:
        return "etn", "etn_keywords", "etn", "ineligible", "etn"

    # Leveraged / Inverse
    if "LEVERAGED" in name or "INVERSE" in name or "BULL" in name or "BEAR" in name or "ULTRA" in name or re.search(r"\b[23]X\b", name) or re.search(r"\b-[123]X\b", name):
        return "leveraged_inverse", "leveraged_inverse_keywords", "etf_fund", "ineligible", "leveraged_inverse"

    # Notes
    if "NOTE" in name or "NOTES" in name or "DEBENTURE" in name or "DEBENTURES" in name:
        return "notes", "note_keywords", "note", "ineligible", "notes"

    # Bonds
    if "BOND" in name or "BONDS" in name or "DEBT" in name or "OBLIGATION" in name or "OBLIGATIONS" in name:
        return "bonds", "bond_keywords", "bond", "ineligible", "bonds"

    # Depositary Shares
    if "DEPOSITARY" in name or "DEPOSITORY" in name or "DEP SHS" in name or "DEP SHARES" in name:
        return "depositary_shares", "depositary_shares_keywords", "depositary_shares", "ineligible", "depositary_shares"

    # ADR / Foreign
    if "ADR" in symbol or "ADR" in name or "ADS" in symbol or "ADS" in name or any(token in name for token in ("ADR", "ADS", "FOREIGN", "ORD SHS", "ORDINARY SHARES", "PLC", "S.A.", "S.A.B.", "LTD", "LIMITED")):
        return "adr_foreign", "adr_foreign_keywords", "adr", "ineligible", "adr_foreign"

    # Preferred
    if "PREFERRED" in name or " PFD" in name or "PREFERRED" in symbol or symbol.endswith(".P") or symbol.endswith("/P") or symbol.endswith("PR") or symbol.endswith(".PR") or "-P" in symbol or "_P" in symbol:
        return "preferred", "preferred_keywords_or_suffix", "preferred", "ineligible", "preferred"

    # Warrant
    if "WARRANT" in name or "WARRANTS" in name or "WTS" in name or symbol.endswith(".W") or symbol.endswith("/W") or symbol.endswith("WS") or symbol.endswith(".WS") or "-W" in symbol or "_W" in symbol:
        return "warrant", "warrant_keywords_or_suffix", "warrant", "ineligible", "warrant"

    # Right
    if "RIGHT" in name or "RIGHTS" in name or "RTS" in name or symbol.endswith(".R") or symbol.endswith("/R") or symbol.endswith("RT") or symbol.endswith(".RT") or "-R" in symbol or "_R" in symbol:
        return "right", "right_keywords_or_suffix", "right", "ineligible", "right"

    # Unit
    if "UNIT" in name or "UNITS" in name or symbol.endswith(".U") or symbol.endswith("/U") or symbol.endswith("UT") or symbol.endswith(".UT") or "-U" in symbol or "_U" in symbol:
        return "unit", "unit_keywords_or_suffix", "unit", "ineligible", "unit"

    # When Issued
    if "WHEN ISSUED" in name or "WHEN-ISSUED" in name or "EX-DISTRIBUTION" in name or "EX DIST" in name or symbol.endswith(".WI") or symbol.endswith("WI") or "-WI" in symbol or "_WI" in symbol:
        return "when_issued", "when_issued_keywords_or_suffix", "common_stock", "ineligible", "when_issued"

    # SPAC / Acquisition
    if "SPAC" in name or "ACQUISITION" in name or "BLANK CHECK" in name or "SHELL" in name:
        return "spac_acquisition", "spac_keywords", "common_stock", "ineligible", "spac_acquisition"

    # Paired Shares
    if "PAIRED" in name or "PAIR" in name:
        return "paired_shares", "paired_shares_keywords", "common_stock", "ineligible", "paired_shares"

    # ETF/Fund
    if any(token in name for token in ("ETF", "FUND", "TRUST", "INDEX")) or any(token in symbol for token in ("ETF", "FUND", "TRUST", "INDEX")):
        return "etf_fund", "fund_keywords", "etf_fund", "ineligible", "etf_fund"

    # Digit-heavy / CUSIP-like
    digit_count = sum(char.isdigit() for char in symbol)
    if (len(symbol) == 9 and symbol.isalnum() and digit_count >= 3) or (symbol and digit_count * 2 >= len(symbol)):
        return "identifier_like", "cusip_or_digit_heavy_symbol", "unknown", "ineligible", "digit_heavy_symbol"

    # Ticker shape check
    if not re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", symbol):
        return "identifier_like", "unsupported_equity_ticker_shape", "unknown", "ineligible", "unsupported_ticker_shape"

    # Valid Common Stock check
    if name and exchange in {"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "NYSEARCA"}:
        return "ticker_like_common_stock", "supported_ticker_and_exchange", "common_stock", "eligible", "none"

    return "unknown", "insufficient_metadata", "unknown", "ineligible", "insufficient_metadata"


def select_sample(assets: list[dict[str, Any]], sample_size: int) -> list[str]:
    candidates = []
    seen = set()
    for row in assets:
        symbol = str(row.get("symbol", "")).upper().strip()
        if symbol and symbol not in seen and classify_asset(row) == "ticker_like_common_stock" and _supported_ticker(symbol):
            seen.add(symbol)
            stable_key = hashlib.sha256(f"{SAMPLE_SEED}|{symbol}|{row.get('asset_id', row.get('id', ''))}".encode("utf-8")).hexdigest()
            candidates.append((stable_key, symbol))
    return [symbol for _key, symbol in sorted(candidates)[:max(0, sample_size)]]


def _supported_ticker(symbol: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", str(symbol or "").upper().strip()))


def _sample_preflight(assets: list[dict[str, Any]], sample: list[str]) -> tuple[bool, list[str]]:
    by_symbol = {str(row.get("symbol", "")).upper().strip(): row for row in assets}
    issues = []
    if not sample:
        issues.append("empty_sample")
    ticker_like_sample_count = 0
    identifier_like_count_in_sample = 0
    for symbol in sample:
        row = by_symbol.get(symbol, {})
        classification = classify_asset(row)
        if classification == "ticker_like_common_stock":
            ticker_like_sample_count += 1
        elif classification == "identifier_like":
            identifier_like_count_in_sample += 1
        if classification != "ticker_like_common_stock":
            issues.append(f"invalid_sample_member_classification:{symbol}:{classification}")
        if not _supported_ticker(symbol):
            issues.append(f"invalid_ticker_syntax:{symbol}")
    if ticker_like_sample_count != len(sample):
        issues.append(f"sample_count_mismatch:ticker_like={ticker_like_sample_count},total={len(sample)}")
    if identifier_like_count_in_sample > 0:
        issues.append(f"identifier_like_in_sample_count:{identifier_like_count_in_sample}")
    return not issues, issues


def _invalid_symbol_from_error(exc: urllib.error.HTTPError, symbols: list[str]) -> str | None:
    message = str(getattr(exc, "sanitized_detail", {}).get("message", "")).upper()
    match = re.search(r"INVALID\s+SYMBOL\s*[:=]?\s*([A-Z0-9.]+)", message)
    candidate = match.group(1) if match else ""
    return candidate if candidate in set(symbols) else None


def retrieve_bars_fault_tolerant(client: Any, symbols: list[str], config: FeasibilityConfig) -> tuple[dict[str, dict[str, Any]], int]:
    """Isolate invalid-symbol 400s without replacing original-sample members."""
    results = {symbol: {"bar_query_attempted": False, "bar_query_result": "NOT_ATTEMPTED", "api_error": "", "bars": []} for symbol in symbols}
    isolated_400 = 0
    budget = max(8, len(symbols) * 4)

    def attempt(batch: list[str]) -> None:
        nonlocal isolated_400, budget
        if not batch or budget <= 0:
            for symbol in batch:
                results[symbol].update({"bar_query_attempted": True, "bar_query_result": "BATCH_ISOLATION_LIMIT", "api_error": "isolation_limit"})
            return
        budget -= 1
        for symbol in batch: results[symbol]["bar_query_attempted"] = True
        try:
            payload = client.get_daily_bars(batch, config.start_date, config.end_date, config.feed, config.adjustment, config.max_retries)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise
            if exc.code != 400:
                raise
            isolated_400 += 1
            invalid = _invalid_symbol_from_error(exc, batch)
            if invalid:
                results[invalid].update({"bar_query_result": "INVALID_SYMBOL", "api_error": "invalid_symbol"})
                attempt([symbol for symbol in batch if symbol != invalid])
            elif len(batch) == 1:
                results[batch[0]].update({"bar_query_result": "INVALID_SYMBOL", "api_error": "unparseable_http_400"})
            else:
                midpoint = len(batch) // 2
                attempt(batch[:midpoint]); attempt(batch[midpoint:])
            return
        for symbol in batch:
            results[symbol].update({"bar_query_result": "OK", "bars": list(payload.get(symbol, []))})

    attempt(list(symbols))
    return results, isolated_400


def load_kaggle_symbols(path: Path | None) -> set[str]:
    if path is None or not path.exists(): return set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {str(row.get("symbol", "")).strip().upper() for row in csv.DictReader(handle) if str(row.get("symbol", "")).strip()}


def validate_bars(symbol: str, bars: list[dict[str, Any]], requested_end: str = "") -> dict[str, Any]:
    result = {
        "symbol": symbol,
        "status": "VALID",
        "issues": [],
        "observations": len(bars),
        "first_date": "",
        "last_date": "",
        "history_years": 0.0,
        "long_gap_count": 0,
        "recent_final_date_coverage": False,
        "last_30_trading_day_coverage": False,
        "suspicious_symbol_reuse_or_discontinuity": False,
        "abrupt_history_termination": False,
        "official_delist_date": "UNKNOWN",
        "request_status": "SUCCESS",
        "data_status": "VALID",
        "calendar_span_years": 0.0,
        "approximate_expected_sessions": 0,
        "observation_density": 0.0,
        "usable_history_years": 0.0,
        "usable_1_year": False,
        "usable_3_year": False,
        "usable_5_year": False,
    }
    if not bars:
        result["status"] = "EMPTY"
        result["data_status"] = "EMPTY"
        return result
    parsed: list[date] = []
    for bar in bars:
        try:
            timestamp = str(bar["t"]).replace("Z", "+00:00")
            day = datetime.fromisoformat(timestamp).date()
            values = [float(bar[key]) for key in ("o", "h", "l", "c", "v")]
            if not all(math.isfinite(value) for value in values) or values[4] < 0 or values[3] <= 0 or values[1] < max(values[0], values[2], values[3]) or values[2] > min(values[0], values[1], values[3]):
                raise ValueError("ohlcv")
            parsed.append(day)
        except (KeyError, TypeError, ValueError, OverflowError):
            result["issues"].append("invalid_ohlcv_or_date")
    if len(parsed) != len(bars):
        result["status"] = "MALFORMED"
        result["data_status"] = "MALFORMED"
        return result
    if parsed != sorted(parsed):
        result["issues"].append("unsorted_dates")
    if len(set(parsed)) != len(parsed):
        result["issues"].append("duplicate_dates")
    if result["issues"]:
        result["status"] = "MALFORMED"
        result["data_status"] = "MALFORMED"
        return result
    first_date, last_date = parsed[0], parsed[-1]
    result["first_date"], result["last_date"] = first_date.isoformat(), last_date.isoformat()
    result["history_years"] = round((last_date - first_date).days / 365.25, 3)
    gaps = [(right - left).days for left, right in zip(parsed, parsed[1:])]
    result["long_gap_count"] = sum(gap > 14 for gap in gaps)
    if requested_end:
        end = date.fromisoformat(requested_end[:10])
        trailing_days = (end - last_date).days
        result["recent_final_date_coverage"] = trailing_days <= 45
        result["last_30_trading_day_coverage"] = trailing_days <= 45
        result["abrupt_history_termination"] = 45 < trailing_days <= 365 and result["long_gap_count"] == 0
    result["suspicious_symbol_reuse_or_discontinuity"] = result["long_gap_count"] > 0

    # Usable history metrics
    calendar_span_days = (last_date - first_date).days
    result["calendar_span_years"] = round(calendar_span_days / 365.25, 3)
    result["approximate_expected_sessions"] = max(1, int(round(calendar_span_days * 5 / 7)))
    result["observation_density"] = round(len(parsed) / result["approximate_expected_sessions"], 3)
    result["usable_history_years"] = round(len(parsed) / 252, 3)

    result["usable_1_year"] = bool(len(parsed) >= 200 and result["observation_density"] >= 0.75)
    result["usable_3_year"] = bool(len(parsed) >= 600 and result["observation_density"] >= 0.75)
    result["usable_5_year"] = bool(len(parsed) >= 1000 and result["observation_density"] >= 0.75)

    if result["usable_1_year"]:
        result["data_status"] = "VALID"
    else:
        result["data_status"] = "TOO_SPARSE"
    return result


def compute_coverage(rows: list[dict[str, Any]], *, total_inactive_assets: int, kaggle_symbols: set[str] | None = None, asset_inventory: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    for row in rows:
        if "identity_eligible" not in row:
            row["identity_eligible"] = True
    inventory = asset_inventory if asset_inventory is not None else rows
    candidates = [row for row in inventory if row.get("classification") == "ticker_like_common_stock"]
    
    metadata_eligible_sample = len(rows)
    identity_verified_sample = sum(1 for row in rows if row.get("identity_eligible"))
    
    verified_rows = [row for row in rows if row.get("identity_eligible")]
    valid_verified = [row for row in verified_rows if row.get("data_status") == "VALID" or (row.get("data_status") is None and row.get("status") == "VALID")]
    observations_verified = [int(row.get("observations", 0)) for row in valid_verified]
    
    kaggle = kaggle_symbols or set()
    sample_symbols = {str(row.get("symbol", "")) for row in rows}
    
    excluded = {kind: sum(row.get("classification") == kind for row in inventory) for kind in CLASSIFICATIONS if kind not in {"ticker_like_common_stock", "identifier_like"}}
    excluded = {kind: count for kind, count in excluded.items() if count}
    
    feeds = sorted({str(row.get("source_feed", "unknown")) or "unknown" for row in rows})
    
    feed_rates = {}
    for feed in feeds:
        verified_feed_rows = [r for r in verified_rows if (str(r.get("source_feed", "unknown")) or "unknown") == feed]
        if verified_feed_rows:
            success_count = sum(1 for r in verified_feed_rows if r.get("data_status") == "VALID" or (r.get("data_status") is None and r.get("status") == "VALID"))
            feed_rates[feed] = success_count / len(verified_feed_rows)
        else:
            feed_rates[feed] = 0.0
            
    eligible_series = [row for row in verified_rows if row.get("first_date")]
    shared_history_boundary = False
    most_common_first_date = ""
    first_date_frequency = 0
    first_date_percentage = 0.0
    
    if len(eligible_series) >= 10:
        first_dates = [row.get("first_date") for row in eligible_series]
        from collections import Counter
        counts = Counter(first_dates)
        most_common_first_date, first_date_frequency = counts.most_common(1)[0]
        first_date_percentage = round(first_date_frequency / len(eligible_series), 3)
        if first_date_percentage >= 0.20:
            shared_history_boundary = True
            
    for row in rows:
        if shared_history_boundary and row.get("first_date") == most_common_first_date:
            row["shared_history_boundary"] = True
        else:
            row["shared_history_boundary"] = False
            
    collisions_count = sum(1 for row in rows if row.get("possible_ticker_reuse") or row.get("asset_identity_status") in ("ACTIVE_INACTIVE_COLLISION", "MULTIPLE_INACTIVE_ASSET_IDS"))
    
    min_representative_sample_size = 10
    
    if collisions_count > 0:
        classification = "INCONCLUSIVE_ASSET_IDENTITY_CONTAMINATION"
    elif shared_history_boundary:
        classification = "INCONCLUSIVE_HISTORY_BOUNDARY"
    else:
        valid_rate = len(valid_verified) / identity_verified_sample if identity_verified_sample else 0.0
        three_rate = sum(1 for row in verified_rows if row.get("usable_3_year")) / identity_verified_sample if identity_verified_sample else 0.0
        if valid_rate >= 0.90 and three_rate >= 0.80:
            classification = "VIABLE_FOR_FREE_POC"
        elif valid_verified:
            classification = "PARTIAL_RESEARCH_ONLY"
        else:
            classification = "NOT_VIABLE"
            
        if classification == "NOT_VIABLE" and identity_verified_sample < min_representative_sample_size:
            classification = "INCONCLUSIVE_ASSET_IDENTITY_CONTAMINATION"
            
    return {
        "total_inactive_assets": total_inactive_assets,
        "probable_common_stock_candidates": len(candidates),
        "eligible_ticker_like_pool": len(candidates),
        "deterministic_sample_size": len(rows),
        "original_sample_size": len(rows),
        "metadata_eligible_sample": metadata_eligible_sample,
        "identity_verified_sample": identity_verified_sample,
        "ticker_like_sample_count": sum(row.get("classification") == "ticker_like_common_stock" for row in rows),
        "identifier_like_count": sum(row.get("classification") == "identifier_like" for row in rows),
        "invalid_symbol_count": sum(row.get("bar_query_result") in {"INVALID_SYMBOL", "IDENTIFIER_NOT_BAR_QUERYABLE"} for row in rows),
        "bar_query_attempted_count": sum(bool(row.get("bar_query_attempted")) for row in rows),
        "symbols_with_valid_bars": len(valid_verified),
        "valid_bar_count": len(valid_verified),
        "valid_bar_percentage": round((len(valid_verified) / identity_verified_sample * 100), 3) if identity_verified_sample else 0.0,
        "symbols_at_least_1_year": sum(1 for row in verified_rows if row.get("usable_1_year")),
        "symbols_at_least_3_years": sum(1 for row in verified_rows if row.get("usable_3_year")),
        "symbols_at_least_5_years": sum(1 for row in verified_rows if row.get("usable_5_year")),
        "empty_responses": sum(row.get("data_status") == "EMPTY" for row in rows),
        "malformed_responses": sum(row.get("data_status") == "MALFORMED" for row in rows),
        "recent_final_date_coverage": sum(bool(row.get("recent_final_date_coverage")) for row in valid_verified),
        "median_observations": statistics.median(observations_verified) if observations_verified else 0,
        "median_history_years": statistics.median([float(row.get("usable_history_years", 0)) for row in valid_verified]) if valid_verified else 0.0,
        "source_feed_success_rates": feed_rates,
        "excluded_asset_type_counts": excluded,
        "kaggle_validation_count": len(kaggle),
        "kaggle_match_rate": (len(kaggle & sample_symbols) / len(kaggle)) if kaggle else None,
        "classification": classification,
        "shared_history_boundary_detected": shared_history_boundary,
        "most_common_first_date": most_common_first_date,
        "first_date_frequency": first_date_frequency,
        "first_date_percentage": first_date_percentage,
        "collisions_count": collisions_count,
        "min_representative_sample_size": min_representative_sample_size,
    }


def run_feasibility(config: FeasibilityConfig, kaggle_csv: Path | None = None, *, client: AlpacaInactiveAssetClient | None = None) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    client = client or AlpacaInactiveAssetClient.from_environment()
    client.require_credentials()
    try:
        assets = client.get_inactive_us_equities(config.max_retries)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
        raise RequestStageError("ASSET_INVENTORY_REQUEST_FAILED", exc) from exc
        
    all_inventory = getattr(client, "all_assets", assets)
    identity_index = {}
    for r in all_inventory:
        sym = str(r.get("symbol", "")).upper().strip()
        if sym:
            identity_index.setdefault(sym, []).append(r)
            
    sample = select_sample(assets, config.sample_size)
    preflight_ok, preflight_issues = _sample_preflight(assets, sample)
    if not preflight_ok:
        raise SampleConstructionError(preflight_issues)
        
    raw_dir = LOCAL_RESEARCH_DATA_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "inactive_assets.json").write_text(json.dumps(assets, indent=2), encoding="utf-8")
    
    rows = []
    isolated_400_count = 0
    
    symbol_identities = {}
    for symbol in sample:
        records = identity_index.get(symbol, [])
        status, details = classify_symbol_identity(symbol, records)
        symbol_identities[symbol] = (status == "UNIQUE_INACTIVE_SYMBOL", status, details)
        
    for index in range(0, len(sample), max(1, config.batch_size)):
        batch = sample[index:index + max(1, config.batch_size)]
        eligible_batch = [s for s in batch if symbol_identities[s][0]]
        
        batch_results = {}
        isolated = 0
        if eligible_batch:
            try:
                batch_results, isolated = retrieve_bars_fault_tolerant(client, eligible_batch, config)
                isolated_400_count += isolated
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
                raise RequestStageError("BAR_REQUEST_FAILED", exc) from exc
                
        for symbol in batch:
            source = next(row for row in assets if row["symbol"] == symbol)
            is_eligible, identity_status, details = symbol_identities[symbol]
            
            created_date = None
            if source.get("created_at"):
                try:
                    created_date = datetime.fromisoformat(source["created_at"].replace("Z", "+00:00")).date()
                except Exception:
                    pass
            
            if is_eligible:
                result = batch_results[symbol]
                if result["bar_query_result"] == "OK":
                    validation = validate_bars(symbol, result["bars"], config.end_date)
                    first_bar_str = validation.get("first_date", "")
                    bars_start_after_creation = False
                    if created_date and first_bar_str:
                        first_bar_date = date.fromisoformat(first_bar_str)
                        if first_bar_date > created_date:
                            bars_start_after_creation = True
                    validation["bars_start_after_creation"] = bars_start_after_creation
                    
                    last_bar_str = validation.get("last_date", "")
                    near_current_date_bars = False
                    if last_bar_str:
                        last_bar_date = date.fromisoformat(last_bar_str)
                        if last_bar_date >= date(2026, 6, 10):
                            near_current_date_bars = True
                    validation["near_current_date_bars"] = near_current_date_bars
                else:
                    req_status = "NETWORK_ERROR" if any(kw in result["api_error"].lower() for kw in ("dns", "network", "timeout", "connection")) else "HTTP_ERROR"
                    validation = {
                        "symbol": symbol,
                        "status": result["bar_query_result"],
                        "issues": [result["api_error"]] if result["api_error"] else [],
                        "observations": 0,
                        "first_date": "",
                        "last_date": "",
                        "history_years": 0.0,
                        "request_status": req_status,
                        "data_status": "EMPTY",
                        "calendar_span_years": 0.0,
                        "approximate_expected_sessions": 0,
                        "observation_density": 0.0,
                        "usable_history_years": 0.0,
                        "usable_1_year": False,
                        "usable_3_year": False,
                        "usable_5_year": False,
                        "bars_start_after_creation": False,
                        "near_current_date_bars": False,
                    }
                bar_attempted = result["bar_query_attempted"]
                bar_result = result["bar_query_result"]
                api_err = result["api_error"]
            else:
                validation = {
                    "symbol": symbol,
                    "status": "NOT_QUERIED_IDENTITY_AMBIGUOUS",
                    "issues": [],
                    "observations": 0,
                    "first_date": "",
                    "last_date": "",
                    "history_years": 0.0,
                    "request_status": "SUCCESS",
                    "data_status": "NOT_QUERIED_IDENTITY_AMBIGUOUS",
                    "calendar_span_years": 0.0,
                    "approximate_expected_sessions": 0,
                    "observation_density": 0.0,
                    "usable_history_years": 0.0,
                    "usable_1_year": False,
                    "usable_3_year": False,
                    "usable_5_year": False,
                    "bars_start_after_creation": False,
                    "near_current_date_bars": False,
                }
                bar_attempted = False
                bar_result = "NOT_QUERIED_IDENTITY_AMBIGUOUS"
                api_err = ""
                
            row_data = {
                **source,
                **validation,
                "bar_query_attempted": bar_attempted,
                "bar_query_result": bar_result,
                "api_error": api_err,
                "source_feed": config.feed,
                "normalized_symbol": symbol,
                "identity_eligible": is_eligible,
                **details
            }
            rows.append(row_data)
            
    summary = compute_coverage(rows, total_inactive_assets=len(assets), kaggle_symbols=load_kaggle_symbols(kaggle_csv), asset_inventory=assets)
    summary.update({
        "inventory_asset_count": client.inventory_asset_count,
        "inactive_asset_count": client.inactive_asset_count,
        "isolated_400_count": isolated_400_count,
        "completed_sample": all(row.get("bar_query_result") != "BATCH_ISOLATION_LIMIT" for row in rows),
        "request_methods_used": sorted(set(client.methods_used)),
        "http_request_count": len(client.methods_used),
        "asset_inventory_http_requests": len(client.methods_used),
        "bar_download_invocations": client.bar_download_invocations,
        "raw_http_request_count": None,
        "raw_http_request_count_available": False,
        "inventory_status_counts": client.inventory_status_counts,
        "credential_variables_used": ",".join(client.credential_variables_used),
        "feed": config.feed,
        "adjustment": config.adjustment
    })
    return rows, summary, True


def write_reports(rows: list[dict[str, Any]], summary: dict[str, Any], *, real_run: bool) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row} | {"source_feed"})
    with (REPORT_DIR / "alpaca_inactive_asset_coverage.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    lines = ["# Alpaca Inactive Asset Feasibility", "", f"- real_get_only_run: {'yes' if real_run else 'no'}"]
    lines.extend(f"- {key}: {value}" for key, value in summary.items())
    lines += ["", "## Limitations", "", "- Inactive status is not an official delisting label or date.", "- Final bar is not a delisting return; missing bars do not imply bankruptcy or zero value.", "- Passing coverage thresholds does not prove survivorship-free, point-in-time universe coverage.", "- Symbol changes/reuse and corporate actions need separate point-in-time validation."]
    (REPORT_DIR / "alpaca_inactive_asset_feasibility.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="GET-only Alpaca inactive-asset research feasibility probe")
    parser.add_argument("--sample-size", type=int, default=100); parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--start-date", default="2000-01-01"); parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--feed", default="iex"); parser.add_argument("--adjustment", default="raw"); parser.add_argument("--kaggle-csv", type=Path)
    args = parser.parse_args(); config = FeasibilityConfig(args.start_date, args.end_date, args.feed, args.adjustment, args.sample_size, args.batch_size)
    client = AlpacaInactiveAssetClient.from_environment()
    try:
        rows, summary, real_run = run_feasibility(config, args.kaggle_csv, client=client)
    except (SampleConstructionError, RequestStageError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
        rows, real_run = [], bool(client.methods_used)
        summary = compute_coverage([], total_inactive_assets=0)
        cause = exc.cause if isinstance(exc, RequestStageError) else exc
        diagnostic = classify_diagnostic_exception(cause)
        detail = getattr(cause, "sanitized_detail", {})
        context = getattr(cause, "sanitized_request_context", {})
        classification = "INCONCLUSIVE_SAMPLE_CONSTRUCTION_DEFECT" if isinstance(exc, SampleConstructionError) else coverage_failure_classification(len(client.methods_used))
        summary.update({
            "classification": classification,
            "request_stage": exc.stage if isinstance(exc, RequestStageError) else "SAMPLE_PREFLIGHT" if isinstance(exc, SampleConstructionError) else "UNKNOWN",
            "sample_preflight_issues": exc.issues if isinstance(exc, SampleConstructionError) else [],
            "api_error": diagnostic["classification"],
            "api_exception_type": diagnostic["exception_type"],
            "api_errno": diagnostic["errno"],
            "api_error_message": detail.get("message", diagnostic["message"]),
            "api_error_code": detail.get("error_code", ""),
            "request_context": context,
            "http_request_count": len(client.methods_used),
            "asset_inventory_http_requests": len(client.methods_used),
            "bar_download_invocations": client.bar_download_invocations,
            "raw_http_request_count": None,
            "raw_http_request_count_available": False,
            "inventory_status_counts": client.inventory_status_counts,
            "credential_variables_used": ",".join(client.credential_variables_used),
            "feed": config.feed,
            "adjustment": config.adjustment
        })
    write_reports(rows, summary, real_run=real_run)
    print(summary["classification"]); return 0


def _safe_api_error(exc: Exception) -> str:
    return classify_diagnostic_exception(exc)["classification"]


def coverage_failure_classification(request_count: int) -> str:
    return "INCONCLUSIVE_API_BLOCKED" if request_count else "INCONCLUSIVE_NOT_RUN"


def classify_diagnostic_exception(exc: Exception) -> dict[str, object]:
    """Classify transport failures without retaining server text, URLs, or secrets."""
    if isinstance(exc, urllib.error.HTTPError):
        labels = {401: "HTTP_AUTH_FAILURE", 403: "HTTP_PERMISSION_FAILURE", 429: "HTTP_RATE_LIMITED"}
        classification = labels.get(exc.code, "HTTP_SERVER_FAILURE" if exc.code >= 500 else "ENDPOINT_OR_REQUEST_DEFECT")
        return {"classification": classification, "exception_type": "HTTPError", "errno": None, "message": f"http_status_{exc.code}"}
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        return {"classification": "NETWORK_DNS_FAILURE", "exception_type": type(reason).__name__, "errno": reason.errno, "message": "dns_resolution_failed"}
    if isinstance(reason, ssl.SSLError):
        return {"classification": "NETWORK_TLS_FAILURE", "exception_type": type(reason).__name__, "errno": getattr(reason, "errno", None), "message": "tls_validation_failed"}
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return {"classification": "NETWORK_TIMEOUT", "exception_type": type(reason).__name__, "errno": None, "message": "connection_timed_out"}
    if isinstance(reason, OSError):
        blocked = {errno.EACCES, errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH, 13, 10061, 10051, 10065}
        classification = "NETWORK_CONNECTION_BLOCKED" if reason.errno in blocked else "INCONCLUSIVE"
        return {"classification": classification, "exception_type": type(reason).__name__, "errno": reason.errno, "message": "tcp_connection_failed"}
    return {"classification": "INCONCLUSIVE", "exception_type": type(reason).__name__, "errno": None, "message": "network_request_failed"}


if __name__ == "__main__":
    raise SystemExit(main())
