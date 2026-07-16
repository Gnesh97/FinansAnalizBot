"""Bounded Alpaca request-window forensics for the frozen RS V2 recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Iterable

import pandas as pd


CURRENT_CONTRACT = "CURRENT_CONTRACT"
NYSE_SESSION_UTC_WINDOW = "NYSE_SESSION_UTC_WINDOW"
DATE_END_EXCLUSIVE_WINDOW = "DATE_END_EXCLUSIVE_WINDOW"
PROVIDER_CONTRACT_VARIANTS = (
    CURRENT_CONTRACT,
    NYSE_SESSION_UTC_WINDOW,
    DATE_END_EXCLUSIVE_WINDOW,
)
PROVIDER_FORENSICS_MAX_REQUESTS = 24
NEW_YORK = "America/New_York"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_RUN = "acq_20260712_212749"


@dataclass(frozen=True)
class RequestWindow:
    """One explicit provider request interval and its documented semantics."""

    variant: str
    endpoint: str
    start: pd.Timestamp
    end: pd.Timestamp
    documented_end_semantics: str = "INCLUSIVE"
    variant_end_semantics: str = "INCLUSIVE"


@dataclass(frozen=True)
class ForensicsRunSpec:
    """Immutable inputs for one bounded provider-contract investigation."""

    run_id: str
    targets: list[dict]
    schedule: pd.DataFrame
    run_root: Path
    report_dir: Path
    frozen_root: Path
    request_budget: int = PROVIDER_FORENSICS_MAX_REQUESTS
    preflight_status: str = ""
    preflight_generated_at: str = ""


def _session_schedule_row(
    target_session: str, schedule: pd.DataFrame
) -> tuple[pd.Timestamp, pd.Timestamp]:
    session = pd.Timestamp(target_session).tz_localize(None).normalize()
    row = schedule.loc[session]
    if isinstance(row, pd.DataFrame):
        raise ValueError("PROVIDER_FORENSICS_DUPLICATE_SCHEDULE_SESSION")
    market_open = pd.Timestamp(row["market_open"])
    market_close = pd.Timestamp(row["market_close"])
    if market_open.tzinfo is None or market_close.tzinfo is None:
        raise ValueError("PROVIDER_FORENSICS_SCHEDULE_TIMEZONE_REQUIRED")
    return market_open, market_close


def build_request_window(
    variant: str,
    endpoint: str,
    target_session: str,
    schedule: pd.DataFrame,
) -> RequestWindow:
    """Build one of the three approved, deterministic request conventions."""

    if variant not in PROVIDER_CONTRACT_VARIANTS:
        raise ValueError("PROVIDER_FORENSICS_VARIANT_INVALID")
    if endpoint not in {"daily", "raw_5min"}:
        raise ValueError("PROVIDER_FORENSICS_ENDPOINT_INVALID")
    market_open, market_close = _session_schedule_row(target_session, schedule)
    session = pd.Timestamp(target_session).tz_localize(None).normalize()

    if variant == CURRENT_CONTRACT:
        local_midnight = session.tz_localize(NEW_YORK)
        if endpoint == "daily":
            start = local_midnight - pd.Timedelta(days=1)
            end = local_midnight + pd.Timedelta(days=2)
        else:
            start = market_open.tz_convert(NEW_YORK)
            end = market_close.tz_convert(NEW_YORK)
        variant_end_semantics = "INCLUSIVE"
    elif variant == NYSE_SESSION_UTC_WINDOW:
        start = market_open.tz_convert(timezone.utc)
        end = market_close.tz_convert(timezone.utc)
        variant_end_semantics = "INCLUSIVE"
    else:
        start = session.tz_localize(timezone.utc)
        end = start + pd.Timedelta(days=1)
        variant_end_semantics = "EXCLUSIVE_HYPOTHESIS"

    return RequestWindow(
        variant=variant,
        endpoint=endpoint,
        start=start,
        end=end,
        documented_end_semantics="INCLUSIVE",
        variant_end_semantics=variant_end_semantics,
    )


def planned_forensic_request_count(target_count: int) -> int:
    """Return exact three-variant, two-endpoint request count, capped at 24."""

    count = int(target_count) * len(PROVIDER_CONTRACT_VARIANTS) * 2
    if target_count < 1 or count > PROVIDER_FORENSICS_MAX_REQUESTS:
        raise ValueError("PROVIDER_FORENSICS_REQUEST_BUDGET_EXCEEDED")
    return count


def request_object_evidence(request: object) -> dict[str, str]:
    """Serialize the exact SDK request object without credentials."""

    to_fields = getattr(request, "to_request_fields", None)
    fields = to_fields() if callable(to_fields) else {}
    return {
        "sdk_request_repr": repr(request),
        "sdk_request_fields_json": json.dumps(
            fields, sort_keys=True, separators=(",", ":"), default=str
        ),
    }


def make_stock_request_builder(
    stock_bars_request_type,
    *,
    day_timeframe,
    raw_timeframe,
    feed,
    adjustment,
):
    """Bind SDK types once while preserving each exact request window."""

    def build(_variant: str, target: dict, endpoint: str, window: RequestWindow):
        return stock_bars_request_type(
            symbol_or_symbols=str(target["symbol"]),
            timeframe=day_timeframe if endpoint == "daily" else raw_timeframe,
            start=window.start.to_pydatetime(),
            end=window.end.to_pydatetime(),
            adjustment=adjustment,
            feed=feed,
        )

    return build


def _index_metadata(frame: pd.DataFrame) -> tuple[list[str | None], list[int]]:
    if isinstance(frame.index, pd.MultiIndex):
        return list(frame.index.names), [len(level) for level in frame.index.levels]
    return [frame.index.name], [int(frame.index.nunique())]


def _filter_symbol(
    frame: pd.DataFrame, symbol: str
) -> tuple[pd.DataFrame, str]:
    if not isinstance(frame.index, pd.MultiIndex):
        return frame.copy(), "SYMBOL_LEVEL_ABSENT"
    names = list(frame.index.names)
    level = "symbol" if "symbol" in names else names[0]
    values = set(str(value) for value in frame.index.get_level_values(level))
    if symbol not in values:
        return frame.iloc[0:0].copy(), "SYMBOL_NOT_FOUND"
    return frame.xs(symbol, level=level, drop_level=True).copy(), "SYMBOL_FOUND"


def _frame_timestamps(frame: pd.DataFrame) -> pd.DatetimeIndex:
    if frame.empty:
        return pd.DatetimeIndex([])
    if isinstance(frame.index, pd.MultiIndex):
        names = list(frame.index.names)
        level = "timestamp" if "timestamp" in names else names[-1]
        values = frame.index.get_level_values(level)
    elif isinstance(frame.index, pd.DatetimeIndex):
        values = frame.index
    elif "timestamp" in frame.columns:
        values = frame["timestamp"]
    elif "Date" in frame.columns:
        values = frame["Date"]
    else:
        return pd.DatetimeIndex([])
    timestamps = pd.to_datetime(values, errors="coerce", utc=True)
    return pd.DatetimeIndex(timestamps[~pd.isna(timestamps)])


def classify_session_shift(
    mapped_sessions: Iterable[str], target_session: str
) -> str:
    """Classify returned market sessions without treating adjacency as success."""

    sessions = sorted({str(value) for value in mapped_sessions if str(value)})
    target = str(pd.Timestamp(target_session).date())
    if not sessions:
        return "EMPTY"
    if target in sessions:
        return "EXACT_TARGET_ONLY" if sessions == [target] else "TARGET_WITH_ADJACENT"
    if all(value < target for value in sessions):
        return "SHIFTED_BACKWARD"
    if all(value > target for value in sessions):
        return "SHIFTED_FORWARD"
    return "TARGET_MISSING_MIXED_ADJACENT"


def response_forensics(
    raw_frame: pd.DataFrame,
    *,
    symbol: str,
    endpoint: str,
    target_session: str,
    market_open: pd.Timestamp,
    market_close: pd.Timestamp,
) -> dict[str, object]:
    """Describe raw provider rows before normalization or target filtering."""

    index_names, index_levels = _index_metadata(raw_frame)
    symbol_frame, symbol_status = _filter_symbol(raw_frame, symbol)
    timestamps = _frame_timestamps(symbol_frame)
    utc_values = [value.isoformat() for value in timestamps]
    ny_values = [value.tz_convert(NEW_YORK).isoformat() for value in timestamps]
    if endpoint == "daily":
        mapped_sessions = [str(value.date()) for value in timestamps]
        target_mask = pd.Series(mapped_sessions).eq(str(pd.Timestamp(target_session).date()))
    elif endpoint == "raw_5min":
        mapped_sessions = [str(value.tz_convert(NEW_YORK).date()) for value in timestamps]
        open_utc = pd.Timestamp(market_open).tz_convert(timezone.utc)
        close_utc = pd.Timestamp(market_close).tz_convert(timezone.utc)
        target = str(pd.Timestamp(target_session).date())
        target_mask = pd.Series(mapped_sessions).eq(target)
        if len(timestamps):
            target_mask &= pd.Series(timestamps >= open_utc)
            target_mask &= pd.Series(timestamps < close_utc)
    else:
        raise ValueError("PROVIDER_FORENSICS_ENDPOINT_INVALID")

    session_counts = pd.Series(mapped_sessions, dtype="object").value_counts().to_dict()
    target_key = str(pd.Timestamp(target_session).date())
    adjacent = {
        str(key): int(value)
        for key, value in sorted(session_counts.items())
        if str(key) != target_key
    }
    schema = {str(column): str(dtype) for column, dtype in raw_frame.dtypes.items()}
    return {
        "returned_row_count_before_filtering": int(len(raw_frame)),
        "symbol_filtered_row_count": int(len(symbol_frame)),
        "returned_timestamp_count": int(len(timestamps)),
        "returned_timestamps_utc_json": json.dumps(utc_values, separators=(",", ":")),
        "first_returned_timestamp_utc": utc_values[0] if utc_values else "",
        "last_returned_timestamp_utc": utc_values[-1] if utc_values else "",
        "returned_timestamps_new_york_json": json.dumps(
            ny_values, separators=(",", ":")
        ),
        "mapped_nyse_sessions_json": json.dumps(
            mapped_sessions, separators=(",", ":")
        ),
        "target_session_row_count": int(target_mask.sum()),
        "adjacent_session_row_counts_json": json.dumps(
            adjacent, sort_keys=True, separators=(",", ":")
        ),
        "session_shift_class": classify_session_shift(
            mapped_sessions, target_session
        ),
        "response_schema_json": json.dumps(
            schema, sort_keys=True, separators=(",", ":")
        ),
        "response_index_names_json": json.dumps(index_names, separators=(",", ":")),
        "response_index_levels_json": json.dumps(
            index_levels, separators=(",", ":")
        ),
        "symbol_filter_status": symbol_status,
    }


def persist_provider_response_evidence(
    raw_frame: pd.DataFrame,
    path: Path,
    *,
    allow_overwrite: bool = False,
) -> str:
    """Atomically persist every response, including an empty response marker."""

    path = Path(path)
    if path.exists() and not allow_overwrite:
        raise FileExistsError("PROVIDER_EVIDENCE_OVERWRITE_BLOCKED")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = raw_frame.copy()
    if frame.empty and not len(frame.columns):
        frame = pd.DataFrame(columns=["_empty_provider_response"])
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary, index=True)
    os.replace(temporary, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contract_rows_are_exact(rows: list[dict]) -> bool:
    pairs = {(str(row.get("pair_id")), str(row.get("endpoint"))) for row in rows}
    pair_ids = {pair_id for pair_id, _ in pairs}
    if not pair_ids or pairs != {
        (pair_id, endpoint)
        for pair_id in pair_ids
        for endpoint in ("daily", "raw_5min")
    }:
        return False
    return all(
        not row.get("provider_error_class")
        and int(row.get("target_session_row_count", 0)) > 0
        and row.get("session_shift_class")
        in {"EXACT_TARGET_ONLY", "TARGET_WITH_ADJACENT"}
        for row in rows
    )


def select_provider_contract(rows: list[dict]) -> str | None:
    """Select the first proven variant in fixed order; row volume is irrelevant."""

    for variant in PROVIDER_CONTRACT_VARIANTS:
        variant_rows = [row for row in rows if row.get("variant") == variant]
        if _contract_rows_are_exact(variant_rows):
            return variant
    return None


def _select_endpoint_contract(rows: list[dict], endpoint: str) -> str | None:
    expected_pairs = {str(row.get("pair_id")) for row in rows}
    for variant in PROVIDER_CONTRACT_VARIANTS:
        endpoint_rows = [
            row
            for row in rows
            if row.get("variant") == variant and row.get("endpoint") == endpoint
        ]
        if (
            {str(row.get("pair_id")) for row in endpoint_rows} == expected_pairs
            and endpoint_rows
            and all(
                not row.get("provider_error_class")
                and int(row.get("target_session_row_count", 0)) > 0
                and row.get("session_shift_class")
                in {"EXACT_TARGET_ONLY", "TARGET_WITH_ADJACENT"}
                for row in endpoint_rows
            )
        ):
            return variant
    return None


def _hash_tree(root: Path) -> dict[str, str]:
    root = Path(root)
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _tree_digest(hashes: dict[str, str]) -> str:
    payload = "\n".join(
        f"{path}|{hashes[path]}" for path in sorted(hashes)
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_write_text(text: str, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_write_json(payload: dict, path: Path) -> None:
    _atomic_write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", path
    )


def _atomic_write_dataframe(frame: pd.DataFrame, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def _request_time_evidence(window: RequestWindow) -> dict[str, str]:
    return {
        "request_start_exact": window.start.isoformat(),
        "request_end_exact": window.end.isoformat(),
        "request_start_utc": window.start.tz_convert(timezone.utc).isoformat(),
        "request_end_utc": window.end.tz_convert(timezone.utc).isoformat(),
        "request_start_new_york": window.start.tz_convert(NEW_YORK).isoformat(),
        "request_end_new_york": window.end.tz_convert(NEW_YORK).isoformat(),
        "documented_end_semantics": window.documented_end_semantics,
        "variant_end_semantics": window.variant_end_semantics,
    }


def _contract_root_cause(
    selected: str | None,
    daily_contract: str | None,
    raw_contract: str | None,
    rows: list[dict],
) -> str:
    if selected == CURRENT_CONTRACT:
        return "UTC_DATE_SESSION_MAPPING_ERROR"
    if selected == NYSE_SESSION_UTC_WINDOW:
        return "SDK_TIMEZONE_SERIALIZATION_ERROR"
    if selected == DATE_END_EXCLUSIVE_WINDOW:
        return "END_EXCLUSIVE_CONTRACT_ERROR"
    raw_rows = [row for row in rows if row.get("endpoint") == "raw_5min"]
    if daily_contract and not raw_contract and raw_rows and all(
        not row.get("provider_error_class")
        and int(row.get("returned_row_count_before_filtering", 0)) == 0
        for row in raw_rows
    ):
        return "PROVIDER_TARGET_SESSION_EMPTY"
    return "UNKNOWN_CONTRACT_DEFECT"


def _provider_target_empty_proven(rows: list[dict]) -> bool:
    pairs = {str(row.get("pair_id")) for row in rows}
    if (
        len(pairs) != 4
        or len(rows) != PROVIDER_FORENSICS_MAX_REQUESTS
        or {str(row.get("variant")) for row in rows}
        != set(PROVIDER_CONTRACT_VARIANTS)
        or any(row.get("provider_error_class") for row in rows)
        or any(int(row.get("target_session_row_count", 0)) > 0 for row in rows)
    ):
        return False
    evidence = {
        (
            str(row.get("pair_id")),
            str(row.get("variant")),
            str(row.get("endpoint")),
        ): row
        for row in rows
    }
    for pair_id in pairs:
        current_daily = evidence.get((pair_id, CURRENT_CONTRACT, "daily"), {})
        current_raw = evidence.get((pair_id, CURRENT_CONTRACT, "raw_5min"), {})
        date_daily = evidence.get(
            (pair_id, DATE_END_EXCLUSIVE_WINDOW, "daily"), {}
        )
        date_raw = evidence.get(
            (pair_id, DATE_END_EXCLUSIVE_WINDOW, "raw_5min"), {}
        )
        if (
            int(current_daily.get("returned_row_count_before_filtering", 0)) < 1
            or current_daily.get("session_shift_class")
            not in {
                "SHIFTED_BACKWARD",
                "SHIFTED_FORWARD",
                "TARGET_MISSING_MIXED_ADJACENT",
            }
            or int(current_raw.get("returned_row_count_before_filtering", 0)) != 0
            or int(date_daily.get("returned_row_count_before_filtering", 0)) != 0
            or int(date_raw.get("returned_row_count_before_filtering", 0)) != 0
        ):
            return False
    return True


def classify_forensics_outcome(
    rows: list[dict], *, circuit_breaker_fired: bool
) -> dict[str, str | None]:
    """Resolve contract, provider insufficiency, or implementation failure."""

    selected = select_provider_contract(rows)
    daily_contract = _select_endpoint_contract(rows, "daily")
    raw_contract = _select_endpoint_contract(rows, "raw_5min")
    if circuit_breaker_fired:
        decision = "CONTRACT_SYSTEMIC_FAILURE"
        root_cause = "UNKNOWN_CONTRACT_DEFECT"
    elif selected:
        decision = "PROVIDER_CONTRACT_RESOLVED"
        root_cause = _contract_root_cause(
            selected, daily_contract, raw_contract, rows
        )
    elif _provider_target_empty_proven(rows):
        decision = "PROVIDER_DATA_INSUFFICIENT"
        root_cause = "PROVIDER_TARGET_SESSION_EMPTY"
    elif (
        (daily_contract or raw_contract)
        and len(rows) == PROVIDER_FORENSICS_MAX_REQUESTS
        and not any(row.get("provider_error_class") for row in rows)
    ):
        decision = "PROVIDER_DATA_INSUFFICIENT"
        root_cause = _contract_root_cause(
            selected, daily_contract, raw_contract, rows
        )
    else:
        decision = "CONTRACT_UNRESOLVED"
        root_cause = "UNKNOWN_CONTRACT_DEFECT"
    return {
        "decision_status": decision,
        "root_cause": root_cause,
        "selected_request_window_contract": selected,
        "selected_daily_contract": daily_contract,
        "selected_raw_contract": raw_contract,
    }


def _is_sha256(value: object) -> bool:
    text = str(value)
    return len(text) == 64 and all(
        character in "0123456789abcdef" for character in text.lower()
    )


def _provider_evidence_digest(rows: list[dict]) -> str:
    entries = sorted(
        f"{row.get('evidence_csv_relative_path', '')}|{row.get('evidence_csv_sha256', '')}"
        for row in rows
    )
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _bound_forensics_status(
    status: dict,
    detail_rows: list[dict],
    detail_path: Path,
    run_root: Path,
) -> dict:
    return {
        **status,
        "detail_row_count": len(detail_rows),
        "detail_csv_sha256": hashlib.sha256(
            Path(detail_path).read_bytes()
        ).hexdigest(),
        "provider_evidence_digest": _provider_evidence_digest(detail_rows),
        "staging_run_root": str(Path(run_root).resolve()),
    }


def _fresh_generated_at(value: object, max_age_minutes: int) -> bool:
    try:
        generated_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if generated_at.tzinfo is None:
        return False
    now = datetime.now(timezone.utc)
    return (
        generated_at <= now + timedelta(minutes=5)
        and now - generated_at <= timedelta(minutes=max_age_minutes)
    )


def _detail_evidence_is_valid(payload: dict, status_path: Path) -> bool:
    detail_path = status_path.parent / "rs_v2_provider_contract_forensics_detail.csv"
    run_root = Path(str(payload.get("staging_run_root", "")))
    if not detail_path.is_file() or not run_root.is_dir():
        return False
    if hashlib.sha256(detail_path.read_bytes()).hexdigest() != payload.get(
        "detail_csv_sha256"
    ):
        return False
    try:
        rows = pd.read_csv(detail_path, keep_default_na=False).to_dict("records")
    except (OSError, pd.errors.ParserError, UnicodeError):
        return False
    if len(rows) != PROVIDER_FORENSICS_MAX_REQUESTS:
        return False
    pair_ids = {str(row.get("pair_id")) for row in rows}
    matrix = {
        (
            str(row.get("variant")),
            str(row.get("pair_id")),
            str(row.get("endpoint")),
        )
        for row in rows
    }
    expected_matrix = {
        (variant, pair_id, endpoint)
        for variant in PROVIDER_CONTRACT_VARIANTS
        for pair_id in pair_ids
        for endpoint in ("daily", "raw_5min")
    }
    if len(pair_ids) != 4 or matrix != expected_matrix:
        return False
    classification = classify_forensics_outcome(
        rows,
        circuit_breaker_fired=False,
    )
    for key in (
        "decision_status",
        "root_cause",
        "selected_request_window_contract",
        "selected_daily_contract",
        "selected_raw_contract",
    ):
        if classification.get(key) != payload.get(key):
            return False
    root = run_root.resolve()
    seen_paths: set[str] = set()
    for row in rows:
        relative_text = str(row.get("evidence_csv_relative_path", ""))
        expected_hash = row.get("evidence_csv_sha256")
        relative_path = Path(relative_text)
        evidence_path = (root / relative_path).resolve()
        if (
            relative_path.is_absolute()
            or not evidence_path.is_relative_to(root)
            or not evidence_path.is_file()
            or relative_text in seen_paths
            or not _is_sha256(expected_hash)
            or hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            != expected_hash
        ):
            return False
        seen_paths.add(relative_text)
    return _provider_evidence_digest(rows) == payload.get(
        "provider_evidence_digest"
    )


def load_resolved_provider_contract(
    path: Path,
    *,
    preflight_generated_at: str,
    max_age_minutes: int = 60,
) -> dict:
    """Load a safe Phase 2 result and bind it to the current preflight."""

    path = Path(path)
    if not path.is_file():
        raise ValueError("PROVIDER_CONTRACT_EVIDENCE_MISSING")
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected = payload.get("selected_request_window_contract")
    if (
        payload.get("decision_status") != "PROVIDER_CONTRACT_RESOLVED"
        or selected not in PROVIDER_CONTRACT_VARIANTS
    ):
        raise ValueError("PROVIDER_CONTRACT_NOT_RESOLVED")
    request_count = payload.get("provider_request_count")
    retry_count = payload.get("retry_count")
    if (
        payload.get("preflight_generated_at") != preflight_generated_at
        or payload.get("selected_daily_contract") != selected
        or payload.get("selected_raw_contract") != selected
        or payload.get("planned_request_count")
        != PROVIDER_FORENSICS_MAX_REQUESTS
        or request_count != PROVIDER_FORENSICS_MAX_REQUESTS
        or retry_count != 0
        or payload.get("detail_row_count") != PROVIDER_FORENSICS_MAX_REQUESTS
        or not _is_sha256(payload.get("detail_csv_sha256"))
        or not _is_sha256(payload.get("provider_evidence_digest"))
        or not _fresh_generated_at(payload.get("generated_at"), max_age_minutes)
        or payload.get("frozen_dataset_mutation_count") != 0
        or payload.get("systemic_circuit_breaker_fired") is not False
        or not _detail_evidence_is_valid(payload, path)
    ):
        raise ValueError("PROVIDER_CONTRACT_EVIDENCE_INVALID")
    return payload


def _write_forensics_reports(
    spec: ForensicsRunSpec,
    detail_rows: list[dict],
    status: dict,
) -> tuple[dict[str, Path], dict]:
    detail_path = Path(spec.report_dir) / "rs_v2_provider_contract_forensics_detail.csv"
    summary_path = Path(spec.report_dir) / "rs_v2_provider_contract_forensics.md"
    status_path = (
        Path(spec.report_dir) / "rs_v2_provider_contract_forensics_status.json"
    )
    _atomic_write_dataframe(pd.DataFrame(detail_rows), detail_path)
    bound_status = _bound_forensics_status(
        status,
        detail_rows,
        detail_path,
        spec.run_root,
    )
    summary_text = _forensics_summary_text(bound_status)
    _atomic_write_text(summary_text, summary_path)
    _atomic_write_json(bound_status, status_path)
    run_reports = Path(spec.run_root) / "reports"
    _atomic_write_dataframe(
        pd.DataFrame(detail_rows),
        run_reports / detail_path.name,
    )
    _atomic_write_text(summary_text, run_reports / summary_path.name)
    _atomic_write_json(bound_status, run_reports / status_path.name)
    return (
        {"detail": detail_path, "summary": summary_path, "status": status_path},
        bound_status,
    )


def _forensics_summary_text(status: dict) -> str:
    lines = [
        "# RS V2 Provider Contract Forensics",
        "",
        f"- run_id: `{status.get('run_id', 'UNKNOWN')}`",
        f"- decision_status: `{status['decision_status']}`",
        f"- root_cause: `{status['root_cause']}`",
        f"- selected_request_window_contract: `{status.get('selected_request_window_contract') or 'NONE'}`",
        f"- selected_daily_contract: `{status.get('selected_daily_contract') or 'NONE'}`",
        f"- selected_raw_contract: `{status.get('selected_raw_contract') or 'NONE'}`",
        f"- provider_request_count: `{status['provider_request_count']}`",
        f"- retry_count: `{status['retry_count']}`",
        f"- systemic_circuit_breaker_fired: `{str(status['systemic_circuit_breaker_fired']).lower()}`",
        f"- frozen_dataset_mutation_count: `{status['frozen_dataset_mutation_count']}`",
        "",
        "## Fixed Variants",
        "",
        "- `CURRENT_CONTRACT`: current production semantics unchanged",
        "- `NYSE_SESSION_UTC_WINDOW`: exact approved NYSE open/close in UTC; no undocumented buffer",
        "- `DATE_END_EXCLUSIVE_WINDOW`: target UTC date to next UTC date as the bounded exclusivity hypothesis",
        "",
        "Alpaca's historical-bars reference documents both `start` and `end` as inclusive. The exclusive-end variant is retained only as the mandated deterministic hypothesis; selection depends on exact target-session evidence, never row volume.",
    ]
    if status.get("root_cause") == "PROVIDER_TARGET_SESSION_EMPTY":
        lines.extend(
            [
                "",
                "## Provider Insufficiency Evidence",
                "",
                "`CURRENT_CONTRACT` returned valid adjacent daily sessions around each requested gap while omitting every exact target session. The exact target-date window returned zero daily rows, and all raw variants returned zero rows. No provider error, symbol-filter loss, or frozen mutation occurred.",
            ]
        )
    return "\n".join(lines) + "\n"


def reclassify_forensics_reports(
    report_dir: Path, *, run_root: Path | None = None
) -> dict:
    """Reclassify persisted evidence without issuing another provider call."""

    report_dir = Path(report_dir)
    detail_path = report_dir / "rs_v2_provider_contract_forensics_detail.csv"
    status_path = report_dir / "rs_v2_provider_contract_forensics_status.json"
    summary_path = report_dir / "rs_v2_provider_contract_forensics.md"
    rows = pd.read_csv(detail_path, keep_default_na=False).to_dict("records")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    classification = classify_forensics_outcome(
        rows,
        circuit_breaker_fired=bool(
            status.get("systemic_circuit_breaker_fired", False)
        ),
    )
    updated = {**status, **classification}
    resolved_run_root = run_root or status.get("staging_run_root")
    if resolved_run_root is not None:
        updated = _bound_forensics_status(
            updated,
            rows,
            detail_path,
            Path(resolved_run_root),
        )
    summary_text = _forensics_summary_text(updated)
    _atomic_write_json(updated, status_path)
    _atomic_write_text(summary_text, summary_path)
    if run_root is not None:
        run_reports = Path(run_root) / "reports"
        _atomic_write_json(updated, run_reports / status_path.name)
        _atomic_write_text(summary_text, run_reports / summary_path.name)
    return updated


def execute_provider_contract_forensics(
    spec: ForensicsRunSpec,
    *,
    request_builder,
    request_executor,
) -> dict[str, object]:
    """Execute exactly the bounded three-variant matrix with zero retries."""

    if Path(spec.run_root).exists():
        raise FileExistsError("PROVIDER_FORENSICS_RUN_ROOT_EXISTS")
    planned_requests = planned_forensic_request_count(len(spec.targets))
    if spec.request_budget != planned_requests:
        raise ValueError("PROVIDER_FORENSICS_REQUEST_BUDGET_MISMATCH")
    frozen_before = _hash_tree(spec.frozen_root)
    detail_rows: list[dict] = []
    request_count = 0
    error_counts: dict[str, int] = {}
    circuit_breaker_fired = False
    ordered_targets = sorted(
        spec.targets,
        key=lambda row: (str(row["target_session"]), str(row["symbol"])),
    )

    for variant in PROVIDER_CONTRACT_VARIANTS:
        if circuit_breaker_fired:
            break
        for target in ordered_targets:
            if circuit_breaker_fired:
                break
            pair_id = f"{target['symbol']}|{target['target_session']}"
            market_open, market_close = _session_schedule_row(
                str(target["target_session"]), spec.schedule
            )
            for endpoint in ("daily", "raw_5min"):
                window = build_request_window(
                    variant,
                    endpoint,
                    str(target["target_session"]),
                    spec.schedule,
                )
                request = request_builder(variant, target, endpoint, window)
                evidence_path = (
                    Path(spec.run_root)
                    / "provider_responses"
                    / variant
                    / endpoint
                    / f"{target['symbol']}_{target['target_session']}.csv"
                )
                request_count += 1
                provider_error_class = ""
                provider_error_message = ""
                try:
                    raw_frame = request_executor(request)
                    if raw_frame is None:
                        raw_frame = pd.DataFrame()
                    if not isinstance(raw_frame, pd.DataFrame):
                        raise TypeError("PROVIDER_FORENSICS_RESPONSE_NOT_DATAFRAME")
                except Exception as error:
                    raw_frame = pd.DataFrame()
                    provider_error_class = type(error).__name__
                    provider_error_message = str(error)[:500]
                    signature = provider_error_class
                    error_counts = {
                        **error_counts,
                        signature: error_counts.get(signature, 0) + 1,
                    }
                    circuit_breaker_fired = error_counts[signature] >= 3

                evidence_hash = persist_provider_response_evidence(
                    raw_frame, evidence_path
                )
                forensic = response_forensics(
                    raw_frame,
                    symbol=str(target["symbol"]),
                    endpoint=endpoint,
                    target_session=str(target["target_session"]),
                    market_open=market_open,
                    market_close=market_close,
                )
                detail_rows.append(
                    {
                        "run_id": spec.run_id,
                        "variant": variant,
                        "pair_id": pair_id,
                        "symbol": str(target["symbol"]),
                        "asset_role": target.get("asset_role", "CANDIDATE"),
                        "endpoint": endpoint,
                        "timeframe": "1Day" if endpoint == "daily" else "5Min",
                        "target_nyse_session": str(target["target_session"]),
                        **_request_time_evidence(window),
                        **request_object_evidence(request),
                        **forensic,
                        "provider_error_class": provider_error_class,
                        "provider_error_message": provider_error_message,
                        "evidence_csv_relative_path": evidence_path.relative_to(
                            spec.run_root
                        ).as_posix(),
                        "evidence_csv_sha256": evidence_hash,
                    }
                )
                if circuit_breaker_fired:
                    break

    frozen_after = _hash_tree(spec.frozen_root)
    mutation_count = sum(
        frozen_before.get(path) != frozen_after.get(path)
        for path in set(frozen_before) | set(frozen_after)
    )
    classification = classify_forensics_outcome(
        detail_rows, circuit_breaker_fired=circuit_breaker_fired
    )
    status = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "run_id": spec.run_id,
        "preflight_status": spec.preflight_status,
        "preflight_generated_at": spec.preflight_generated_at,
        **classification,
        "provider_request_count": request_count,
        "retry_count": 0,
        "planned_request_count": planned_requests,
        "request_budget": spec.request_budget,
        "systemic_circuit_breaker_fired": circuit_breaker_fired,
        "frozen_dataset_mutation_count": mutation_count,
        "frozen_snapshot_digest_before": _tree_digest(frozen_before),
        "frozen_snapshot_digest_after": _tree_digest(frozen_after),
        "detail_row_count": len(detail_rows),
    }
    report_paths, bound_status = _write_forensics_reports(
        spec, detail_rows, status
    )
    return {
        **bound_status,
        "report_paths": report_paths,
        "detail_rows": detail_rows,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run bounded GET-only Alpaca request-window forensics."
    )
    parser.add_argument(
        "--frozen-root",
        type=Path,
        default=PROJECT_ROOT
        / "testdata"
        / "research_data"
        / "rs_v2_clean_market_data",
    )
    parser.add_argument(
        "--staging-root",
        type=Path,
        default=PROJECT_ROOT
        / "testdata"
        / "research_data"
        / "rs_v2_clean_gap_recovery"
        / "runs",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=PROJECT_ROOT / "testdata" / "reports" / "latest",
    )
    parser.add_argument(
        "--preflight-report",
        type=Path,
        default=PROJECT_ROOT
        / "testdata"
        / "reports"
        / "latest"
        / "rs_v2_provider_preflight.json",
    )
    parser.add_argument("--audit-run", default=DEFAULT_AUDIT_RUN)
    parser.add_argument(
        "--request-budget",
        type=int,
        default=PROVIDER_FORENSICS_MAX_REQUESTS,
    )
    return parser


def run_live_provider_contract_forensics(
    *,
    frozen_root: Path,
    staging_root: Path,
    report_dir: Path,
    preflight_report: Path,
    audit_run: str = DEFAULT_AUDIT_RUN,
    request_budget: int = PROVIDER_FORENSICS_MAX_REQUESTS,
) -> dict[str, object]:
    """Run the approved market-data GET matrix; no trading client is loaded."""

    try:
        from .v2_provider_recovery import select_deterministic_canary
        from .vericekici_updated import (
            _load_alpaca,
            build_gap_recovery_targets,
            get_adjustment,
            get_credentials,
            get_data_feed,
            load_provider_preflight_evidence,
            validate_gap_recovery_invocation,
        )
    except ImportError:
        from v2_provider_recovery import select_deterministic_canary
        from vericekici_updated import (
            _load_alpaca,
            build_gap_recovery_targets,
            get_adjustment,
            get_credentials,
            get_data_feed,
            load_provider_preflight_evidence,
            validate_gap_recovery_invocation,
        )

    preflight = load_provider_preflight_evidence(Path(preflight_report))
    all_targets, context = build_gap_recovery_targets(Path(frozen_root), audit_run)
    validate_gap_recovery_invocation(
        dataset_profile="rs_v2_clean",
        feed="iex",
        adjustment="all",
        audit_run=audit_run,
        frozen_root=Path(frozen_root),
        staging_root=Path(staging_root),
        targets=all_targets,
    )
    targets = select_deterministic_canary(all_targets)
    if planned_forensic_request_count(len(targets)) != request_budget:
        raise ValueError("PROVIDER_FORENSICS_REQUEST_BUDGET_MISMATCH")

    api_key, secret_key = get_credentials()
    _, _, client_type, request_type, timeframe_type, timeframe_unit = _load_alpaca()
    client = client_type(api_key, secret_key)
    day_timeframe = (
        timeframe_type.Day
        if hasattr(timeframe_type, "Day")
        else timeframe_type(1, timeframe_unit.Day)
    )
    raw_timeframe = timeframe_type(5, timeframe_unit.Minute)
    request_builder = make_stock_request_builder(
        request_type,
        day_timeframe=day_timeframe,
        raw_timeframe=raw_timeframe,
        feed=get_data_feed("iex"),
        adjustment=get_adjustment("all"),
    )

    def request_executor(request) -> pd.DataFrame:
        response = client.get_stock_bars(request)
        raw_frame = getattr(response, "df", None)
        return raw_frame.copy() if isinstance(raw_frame, pd.DataFrame) else pd.DataFrame()

    run_id = f"provider_contract_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    spec = ForensicsRunSpec(
        run_id=run_id,
        targets=targets,
        schedule=context["schedule"],
        run_root=Path(staging_root) / run_id,
        report_dir=Path(report_dir),
        frozen_root=Path(frozen_root),
        request_budget=request_budget,
        preflight_status=str(preflight.get("status", "")),
        preflight_generated_at=str(preflight.get("generated_at", "")),
    )
    return execute_provider_contract_forensics(
        spec,
        request_builder=request_builder,
        request_executor=request_executor,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_live_provider_contract_forensics(
        frozen_root=args.frozen_root,
        staging_root=args.staging_root,
        report_dir=args.report_dir,
        preflight_report=args.preflight_report,
        audit_run=args.audit_run,
        request_budget=args.request_budget,
    )
    for key in (
        "decision_status",
        "root_cause",
        "selected_request_window_contract",
        "provider_request_count",
        "retry_count",
        "frozen_dataset_mutation_count",
    ):
        print(f"{key}: {result.get(key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
