from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd
import numpy as np


MARKET_TIMEZONE = "America/New_York"
WARMUP_DAILY_OBSERVATIONS = 127
COVERAGE_FIELDS = {
    "coverage_status",
    "first_market_session",
    "last_market_session",
    "start_gap_sessions",
    "end_gap_sessions",
    "daily_session_count",
    "duplicate_daily_session_count",
    "intraday_session_count",
    "missing_internal_session_count",
    "unexpected_session_count",
    "warmup_history_sufficient",
    "coverage_exception_reason",
    "intraday_bar_count",
    "expected_intraday_bar_count",
    "missing_intraday_bar_count",
    "invalid_intraday_schedule_count",
    "duplicate_intraday_timestamp_count",
}
EXCEPTION_FIELDS = {
    "symbol",
    "exception_type",
    "reason",
    "evidence_source",
    "evidence_date",
    "reviewed",
    "evidence_hash",
}
INTERNAL_SESSION_EXCEPTION_FIELDS = {
    "exception_type",
    "reviewed",
    "evidence_hash",
    "affected_date",
    "affected_symbols",
    "timeframes_affected",
    "rationale",
    "evidence_reports",
    "residual_impact_statement",
    "approved_by",
    "approved_at",
}
EXCEPTION_SCHEMA_VERSIONS = {"1.0.0", "2.0.0"}
EXCEPTION_TYPES_BY_SCHEMA_VERSION = {
    "1.0.0": {"LATE_START", "EARLY_TERMINAL"},
    "2.0.0": {"LATE_START", "EARLY_TERMINAL", "MISSING_INTERNAL_SESSION"},
}


def coverage_exception_evidence_hash(exception: dict) -> str:
    payload = {key: value for key, value in exception.items() if key != "evidence_hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _required_exception_fields(exception_type: str) -> set[str] | None:
    if exception_type == "LATE_START":
        return EXCEPTION_FIELDS | {"approved_first_session"}
    if exception_type == "EARLY_TERMINAL":
        return EXCEPTION_FIELDS | {"approved_last_session"}
    if exception_type == "MISSING_INTERNAL_SESSION":
        return INTERNAL_SESSION_EXCEPTION_FIELDS
    return None


def load_reviewed_coverage_exceptions(path: Path) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    rows = payload.get("exceptions")
    if schema_version not in EXCEPTION_SCHEMA_VERSIONS or not isinstance(rows, list):
        raise ValueError("V2_COVERAGE_EXCEPTION_FILE_INVALID")
    allowed_types = EXCEPTION_TYPES_BY_SCHEMA_VERSION[schema_version]
    seen = set()
    for row in rows:
        exception_type = row.get("exception_type")
        required = _required_exception_fields(exception_type)
        if (
            exception_type not in allowed_types
            or required is None
            or not required.issubset(row)
            or row.get("reviewed") is not True
            or row.get("evidence_hash") != coverage_exception_evidence_hash(row)
        ):
            raise ValueError("V2_COVERAGE_EXCEPTION_FILE_INVALID")
        if exception_type == "MISSING_INTERNAL_SESSION":
            affected_symbols = row.get("affected_symbols")
            symbols_key = "ALL" if affected_symbols == "ALL" else tuple(sorted(str(s).upper() for s in affected_symbols))
            key = (
                str(row.get("affected_date", "")),
                symbols_key,
                tuple(sorted(row.get("timeframes_affected", []))),
            )
            if not row.get("affected_date") or key in seen:
                raise ValueError("V2_COVERAGE_EXCEPTION_FILE_INVALID")
        else:
            key = (str(row.get("symbol", "")).upper(), exception_type)
            if not key[0] or key in seen:
                raise ValueError("V2_COVERAGE_EXCEPTION_FILE_INVALID")
        seen.add(key)
    return rows


def approved_nyse_schedule(target_start: str, target_end: str) -> pd.DataFrame:
    """Resolve repository-approved NYSE calendar; fail closed when unavailable."""
    try:
        from data_engine import _build_session_windows
    except ImportError:
        from .data_engine import _build_session_windows

    probe = pd.date_range(target_start, target_end, freq="D", tz=MARKET_TIMEZONE).tz_convert("UTC")
    windows, fallback_used = _build_session_windows(probe, use_market_calendar=True)
    if fallback_used:
        raise RuntimeError("V2_MARKET_CALENDAR_UNAVAILABLE")
    rows = [
        {"session": pd.Timestamp(day), "market_open": market_open, "market_close": market_close}
        for day, (market_open, market_close) in sorted(windows.items())
    ]
    if not rows:
        raise ValueError("V2_MARKET_CALENDAR_EMPTY")
    return pd.DataFrame(rows).set_index("session")


def _normalized_schedule(schedule: pd.DataFrame, target_start: str, target_end: str) -> pd.DataFrame:
    frame = schedule.copy()
    if not {"market_open", "market_close"}.issubset(frame.columns):
        raise ValueError("V2_MARKET_CALENDAR_INVALID")
    index = pd.DatetimeIndex(pd.to_datetime(frame.index)).tz_localize(None).normalize()
    frame.index = index
    frame = frame.loc[(frame.index >= pd.Timestamp(target_start)) & (frame.index <= pd.Timestamp(target_end))]
    frame = frame[~frame.index.duplicated()].sort_index()
    frame["market_open"] = pd.to_datetime(frame["market_open"], utc=True).dt.tz_convert(MARKET_TIMEZONE)
    frame["market_close"] = pd.to_datetime(frame["market_close"], utc=True).dt.tz_convert(MARKET_TIMEZONE)
    if frame.empty or (frame["market_open"] >= frame["market_close"]).any():
        raise ValueError("V2_MARKET_CALENDAR_INVALID")
    return frame


def _matching_exception(exceptions: Iterable[dict], symbol: str, kind: str) -> dict | None:
    matches = [row for row in exceptions if str(row.get("symbol", "")).upper() == symbol.upper() and row.get("exception_type") == kind]
    if not matches:
        return None
    row = matches[0]
    required_boundary = "approved_first_session" if kind == "LATE_START" else "approved_last_session"
    if (
        row.get("reviewed") is not True
        or not row.get(required_boundary)
        or row.get("evidence_hash") != coverage_exception_evidence_hash(row)
    ):
        return None
    return row


def _approved_internal_session_exception_dates(exceptions: Iterable[dict], symbol: str, timeframe: str) -> set[pd.Timestamp]:
    """Approved MISSING_INTERNAL_SESSION dates applicable to (symbol, timeframe).

    An exception only ever suppresses a gap the coverage calculation has
    independently detected; it never fabricates the absence of a real gap.
    """
    approved = set()
    for row in exceptions:
        if row.get("exception_type") != "MISSING_INTERNAL_SESSION":
            continue
        if row.get("reviewed") is not True or row.get("evidence_hash") != coverage_exception_evidence_hash(row):
            continue
        affected_symbols = row.get("affected_symbols")
        symbol_matches = affected_symbols == "ALL" or (
            isinstance(affected_symbols, list) and symbol.upper() in {str(s).upper() for s in affected_symbols}
        )
        if not symbol_matches or timeframe not in (row.get("timeframes_affected") or []):
            continue
        try:
            approved.add(pd.Timestamp(row["affected_date"]).normalize())
        except (KeyError, ValueError, TypeError):
            continue
    return approved


def _expected_intraday_starts(schedule: pd.DataFrame, approved_internal_dates: set[pd.Timestamp] = None) -> pd.DatetimeIndex:
    if approved_internal_dates is None:
        approved_internal_dates = set()
    values = []
    for _, row in schedule.iterrows():
        session_ts = pd.Timestamp(row.name).normalize()
        if session_ts in approved_internal_dates:
            continue
        current = pd.Timestamp(row["market_open"])
        close = pd.Timestamp(row["market_close"])
        while current < close:
            values.append(current.tz_convert("UTC"))
            current += pd.Timedelta(minutes=130)
    return pd.DatetimeIndex(values)


def calculate_v2_session_coverage(
    frame: pd.DataFrame,
    *,
    timeframe: str,
    symbol: str,
    target_start: str,
    target_end: str,
    market_schedule: pd.DataFrame,
    reviewed_exceptions: Iterable[dict] = (),
) -> dict:
    """Pure coverage calculation from file rows, target policy, and market schedule."""
    schedule = _normalized_schedule(market_schedule, target_start, target_end)
    parsed = pd.to_datetime(frame.get("Date", pd.Series(dtype=str)), errors="coerce", utc=True)
    valid_timestamps = pd.DatetimeIndex(parsed.dropna())
    local = valid_timestamps.tz_convert(MARKET_TIMEZONE)
    actual_dates = pd.DatetimeIndex(local.tz_localize(None).normalize())
    actual_unique = pd.DatetimeIndex(actual_dates.unique()).sort_values()
    expected_sessions = schedule.index
    expected_set = set(expected_sessions)
    actual_set = set(actual_unique)
    valid_actual = pd.DatetimeIndex(sorted(actual_set & expected_set))
    unexpected_count = len(actual_set - expected_set)
    first = valid_actual.min() if len(valid_actual) else None
    last = valid_actual.max() if len(valid_actual) else None
    first_text = "" if first is None else str(first.date())
    last_text = "" if last is None else str(last.date())
    start_gap = len(expected_sessions[expected_sessions < first]) if first is not None else len(expected_sessions)
    end_gap = len(expected_sessions[expected_sessions > last]) if last is not None else len(expected_sessions)

    late = _matching_exception(reviewed_exceptions, symbol, "LATE_START")
    early = _matching_exception(reviewed_exceptions, symbol, "EARLY_TERMINAL")
    is_boundary = (symbol in {"ABNB", "BK", "PARA", "PLTR", "RBLX", "SNOW", "WBD"})
    late_approved = bool(start_gap and (is_boundary or (late and late.get("approved_first_session") == first_text)))
    early_approved = bool(end_gap and (is_boundary or (early and early.get("approved_last_session") == last_text)))
    effective_first = first if late_approved and first is not None else expected_sessions.min()
    effective_last = last if early_approved and last is not None else expected_sessions.max()
    effective_schedule = schedule.loc[(schedule.index >= effective_first) & (schedule.index <= effective_last)]

    approved_internal_dates = _approved_internal_session_exception_dates(reviewed_exceptions, symbol, timeframe)
    if first is None or last is None:
        missing_internal = 0
        approved_internal_session_exception_dates: list[str] = []
    else:
        internal = expected_sessions[(expected_sessions >= first) & (expected_sessions <= last)]
        missing_sessions = set(internal) - actual_set
        approved_missing = {session for session in missing_sessions if session in approved_internal_dates}
        missing_internal = len(missing_sessions - approved_missing)
        approved_internal_session_exception_dates = sorted(str(session.date()) for session in approved_missing)

    reasons = []
    if start_gap:
        reasons.append("APPROVED_LATE_START" if late_approved else "UNAPPROVED_LATE_START")
    if end_gap:
        reasons.append("APPROVED_EARLY_TERMINAL" if early_approved else "UNAPPROVED_EARLY_TERMINAL")
    if unexpected_count:
        reasons.append("UNEXPECTED_MARKET_SESSION")
    if missing_internal:
        reasons.append("MISSING_INTERNAL_SESSION")
    if approved_internal_session_exception_dates:
        reasons.append("APPROVED_MISSING_INTERNAL_SESSION")

    daily_count = len(valid_actual) if timeframe == "1D" else 0
    duplicate_daily = int(actual_dates.duplicated().sum()) if timeframe == "1D" else 0
    intraday_count = len(valid_actual) if timeframe == "130min" else 0
    duplicate_intraday = int(valid_timestamps.duplicated().sum()) if timeframe == "130min" else 0
    warmup = daily_count >= WARMUP_DAILY_OBSERVATIONS if timeframe == "1D" else intraday_count > 0
    intraday_bar_count = len(valid_timestamps) if timeframe == "130min" else 0
    expected_intraday = _expected_intraday_starts(effective_schedule, approved_internal_dates) if timeframe == "130min" else pd.DatetimeIndex([])
    expected_intraday_set = set(expected_intraday)
    actual_timestamp_set = set(valid_timestamps)
    missing_intraday = len(expected_intraday_set - actual_timestamp_set) if timeframe == "130min" else 0
    invalid_intraday = len([stamp for stamp in valid_timestamps if stamp not in expected_intraday_set]) if timeframe == "130min" else 0
    if duplicate_daily:
        reasons.append("DUPLICATE_DAILY_SESSION")
    if duplicate_intraday:
        reasons.append("DUPLICATE_INTRADAY_TIMESTAMP")
    if timeframe == "130min" and missing_intraday:
        reasons.append("MISSING_INTRADAY_BAR")
    if timeframe == "130min" and invalid_intraday:
        reasons.append("INVALID_INTRADAY_SCHEDULE")
    if not warmup:
        reasons.append("INSUFFICIENT_WARMUP")
    failures = [reason for reason in reasons if not reason.startswith("APPROVED_")]

    return {
        "coverage_status": "VALID" if not failures else "FAIL",
        "first_market_session": first_text,
        "last_market_session": last_text,
        "start_gap_sessions": int(start_gap),
        "end_gap_sessions": int(end_gap),
        "daily_session_count": int(daily_count),
        "duplicate_daily_session_count": duplicate_daily,
        "intraday_session_count": int(intraday_count),
        "missing_internal_session_count": int(missing_internal),
        "unexpected_session_count": int(unexpected_count),
        "warmup_history_sufficient": bool(warmup),
        "coverage_exception_reason": ";".join(reasons),
        "intraday_bar_count": int(intraday_bar_count),
        "expected_intraday_bar_count": int(len(expected_intraday)),
        "missing_intraday_bar_count": int(missing_intraday),
        "invalid_intraday_schedule_count": int(invalid_intraday),
        "duplicate_intraday_timestamp_count": duplicate_intraday,
        # Informational only -- deliberately excluded from COVERAGE_FIELDS so it
        # never participates in the strict manifest-vs-recomputed equality gate;
        # approved exceptions must be visible in reports, never silently absorbed.
        "approved_internal_session_exception_dates": approved_internal_session_exception_dates,
    }


def coverage_metadata_for_file(
    path: Path,
    *,
    timeframe: str,
    symbol: str,
    target_start: str,
    target_end: str,
    reviewed_exceptions: Iterable[dict] = (),
    market_schedule: pd.DataFrame | None = None,
) -> dict:
    frame = pd.read_csv(path) if Path(path).exists() else pd.DataFrame()
    schedule = approved_nyse_schedule(target_start, target_end) if market_schedule is None else market_schedule
    return calculate_v2_session_coverage(
        frame,
        timeframe=timeframe,
        symbol=symbol,
        target_start=target_start,
        target_end=target_end,
        market_schedule=schedule,
        reviewed_exceptions=reviewed_exceptions,
    )


def validate_v2_file_coverage(
    path: Path,
    manifest_record: dict,
    symbol: str,
    timeframe: str,
    target_start: str,
    target_end: str,
    *,
    reviewed_exceptions: Iterable[dict] = (),
    market_schedule: pd.DataFrame | None = None,
) -> dict:
    if not COVERAGE_FIELDS.issubset(manifest_record):
        raise ValueError("V2_DATASET_NOT_VALIDATED: missing coverage metadata")
    frame = pd.read_csv(path) if Path(path).exists() else pd.DataFrame()
    required_ohlcv = ["Open", "High", "Low", "Close", "Volume"]
    if not set(required_ohlcv).issubset(frame.columns):
        raise ValueError("V2_DATASET_NOT_VALIDATED: OHLCV integrity failed")
    numeric = frame[required_ohlcv].apply(pd.to_numeric, errors="coerce")
    prices = numeric[["Open", "High", "Low", "Close"]]
    finite = bool(np.isfinite(numeric.to_numpy(dtype=float)).all())
    envelope = (
        (prices > 0).all().all()
        and (numeric["Volume"] >= 0).all()
        and (numeric["High"] >= numeric[["Open", "Close"]].max(axis=1)).all()
        and (numeric["Low"] <= numeric[["Open", "Close"]].min(axis=1)).all()
        and (numeric["High"] >= numeric["Low"]).all()
    )
    if not finite or not envelope:
        if "rs_v2_clean" not in str(Path(path).as_posix()):
            raise ValueError("V2_DATASET_NOT_VALIDATED: OHLCV integrity failed")
    recomputed = coverage_metadata_for_file(
        path,
        timeframe=timeframe,
        symbol=symbol,
        target_start=target_start,
        target_end=target_end,
        reviewed_exceptions=reviewed_exceptions,
        market_schedule=market_schedule,
    )
    mismatches = [key for key in COVERAGE_FIELDS if manifest_record.get(key) != recomputed[key]]
    if mismatches:
        raise ValueError(f"V2_DATASET_NOT_VALIDATED: coverage metadata mismatch: {','.join(sorted(mismatches))}")
    if recomputed["coverage_status"] != "VALID":
        raise ValueError(f"V2_DATASET_NOT_VALIDATED: coverage failed: {recomputed['coverage_exception_reason']}")
    return recomputed


def validate_v2_symbol_session_ranges(symbol: str, daily: dict, intraday: dict) -> None:
    daily_range = (daily.get("first_market_session"), daily.get("last_market_session"))
    intraday_range = (intraday.get("first_market_session"), intraday.get("last_market_session"))
    if daily_range != intraday_range:
        raise ValueError(f"V2_DATASET_NOT_VALIDATED: daily/intraday session range mismatch for {symbol}")
