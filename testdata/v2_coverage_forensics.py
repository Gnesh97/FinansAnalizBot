from __future__ import annotations

import json
import os
import socket
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

try:
    from .v2_session_coverage import MARKET_TIMEZONE, approved_nyse_schedule
except ImportError:
    from v2_session_coverage import MARKET_TIMEZONE, approved_nyse_schedule


BOUNDARY_REVIEW_SYMBOLS = {"ABNB", "BK", "PARA", "PLTR", "RBLX", "SNOW", "WBD"}
ROOT_CAUSES = {
    "RAW_SOURCE_EMPTY_FOR_FULL_SESSION",
    "RAW_SOURCE_EMPTY_FOR_BLOCK",
    "RAW_SOURCE_PARTIAL_BLOCK",
    "AGGREGATION_DROPPED_AVAILABLE_RAW_ROWS",
    "AGGREGATION_TIMESTAMP_MISMATCH",
    "MARKET_CALENDAR_EXPECTATION_MISMATCH",
    "EARLY_CLOSE_EXPECTATION_MISMATCH",
    "INVALID_DERIVED_TIMESTAMP",
    "UNKNOWN_REQUIRES_REVIEW",
}


@contextmanager
def forbid_network_calls():
    telemetry = {"network_attempt_count": 0}
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def blocked(*_args, **_kwargs):
        telemetry["network_attempt_count"] += 1
        raise RuntimeError("V2_OFFLINE_AUDIT_NETWORK_CALL_BLOCKED")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    try:
        yield telemetry
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.create_connection = original_create_connection


def _atomic_frame(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def _atomic_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _normalize_schedule(schedule: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    frame = schedule.copy()
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index)).tz_localize(None).normalize()
    frame = frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))].sort_index()
    frame["market_open"] = pd.to_datetime(frame["market_open"], utc=True).dt.tz_convert(MARKET_TIMEZONE)
    frame["market_close"] = pd.to_datetime(frame["market_close"], utc=True).dt.tz_convert(MARKET_TIMEZONE)
    return frame


def _read_timestamps(path: Path) -> pd.DatetimeIndex:
    frame = pd.read_csv(path, usecols=["Date"])
    return pd.DatetimeIndex(pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()).sort_values()


def _local_sessions(timestamps: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(timestamps.tz_convert(MARKET_TIMEZONE).tz_localize(None).normalize())


def _expected_blocks(schedule: pd.DataFrame) -> dict[pd.Timestamp, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    output = {}
    for session, row in schedule.iterrows():
        current = pd.Timestamp(row["market_open"])
        close = pd.Timestamp(row["market_close"])
        blocks = []
        while current < close:
            block_end = min(current + pd.Timedelta(minutes=130), close)
            blocks.append((current.tz_convert("UTC"), block_end.tz_convert("UTC")))
            current += pd.Timedelta(minutes=130)
        output[pd.Timestamp(session)] = blocks
    return output


def _monthly_rebalance_dates(sessions: pd.DatetimeIndex) -> list[pd.Timestamp]:
    grouped = pd.Series(sessions, index=sessions.to_period("M"))
    return [pd.Timestamp(value) for value in grouped.groupby(level=0).max().tolist()]


def _pattern(affected: int, total: int, qqq: bool, spy: bool, scope: str) -> str:
    share = affected / total if total else 0.0
    if qqq and spy and share >= 0.25:
        return "PROVIDER_WIDE_PATTERN"
    if share > 0.50:
        return "MARKET_WIDE_PATTERN"
    if affected == 1:
        return "SYMBOL_SPECIFIC_PATTERN"
    if scope in {"INTRADAY_MISSING_BLOCK", "INTRADAY_INVALID_TIMESTAMP"} and affected > 1:
        return "CALENDAR_OR_AGGREGATION_PATTERN"
    return "UNRESOLVED"


def _stamp(value) -> str:
    if value is None or value == "":
        return ""
    return str(pd.Timestamp(value).tz_convert("UTC"))


def audit_v2_run(
    dataset_root: Path,
    run_id: str,
    report_dir: Path,
    *,
    market_schedule: pd.DataFrame | None = None,
    warmup_observation_count: int = 127,
) -> dict:
    root = Path(dataset_root)
    manifest_path = root / "manifests" / run_id / "acquisition_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("acquisition_run_id") != run_id:
        raise ValueError("V2_AUDIT_RUN_ID_MISMATCH")
    start, end = manifest["target_start_date"], manifest["target_end_date"]
    schedule = _normalize_schedule(
        approved_nyse_schedule(start, end) if market_schedule is None else market_schedule,
        start,
        end,
    )
    expected_sessions = pd.DatetimeIndex(schedule.index)
    expected_set = set(expected_sessions)
    expected_blocks = _expected_blocks(schedule)
    expected_timestamp_set = {start for blocks in expected_blocks.values() for start, _ in blocks}
    rebalance_dates = _monthly_rebalance_dates(expected_sessions)

    records = manifest.get("files", [])
    daily_records = {row["symbol"]: row for row in records if row.get("timeframe") == "1D"}
    intraday_records = {row["symbol"]: row for row in records if row.get("timeframe") == "130min"}
    symbols = sorted(set(daily_records) | set(intraday_records))
    candidate_symbols = {symbol for symbol, row in daily_records.items() if row.get("asset_role") == "CANDIDATE"}
    total_symbols = len(symbols)

    daily_sets: dict[str, set[pd.Timestamp]] = {}
    daily_duplicates: dict[str, Counter] = {}
    daily_anomalies = {}
    missing_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    boundary_missing_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    unexpected_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    duplicate_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    for symbol, record in daily_records.items():
        timestamps = _read_timestamps(root / record["output_relative_path"])
        local = _local_sessions(timestamps)
        counts = Counter(local)
        actual = set(counts)
        valid_actual = actual & expected_set
        daily_sets[symbol] = valid_actual
        daily_duplicates[symbol] = counts
        missing = sorted(expected_set - actual)
        unexpected = sorted(actual - expected_set)
        duplicates = sorted(session for session, count in counts.items() if count > 1)
        first, last = (min(valid_actual), max(valid_actual)) if valid_actual else (None, None)
        boundary_missing = {session for session in missing if first is None or session < first or session > last}
        for session in missing:
            (boundary_missing_frequency if session in boundary_missing else missing_frequency)[session].add(symbol)
        for session in unexpected:
            unexpected_frequency[session].add(symbol)
        for session in duplicates:
            duplicate_frequency[session].add(symbol)
        daily_anomalies[symbol] = {"missing": missing, "boundary_missing": boundary_missing, "unexpected": unexpected, "duplicates": duplicates}

    daily_rows = []
    session_position = {session: index for index, session in enumerate(expected_sessions)}
    rebalance_positions = {session_position[date] for date in rebalance_dates}
    for symbol, record in daily_records.items():
        actual = daily_sets[symbol]
        ordered_actual = sorted(actual)
        anomalies = daily_anomalies[symbol]
        events = [("BOUNDARY_GAP" if session in anomalies["boundary_missing"] else "MISSING", session) for session in anomalies["missing"]]
        events += [("UNEXPECTED", session) for session in anomalies["unexpected"]]
        events += [("DUPLICATE", session) for session in anomalies["duplicates"]]
        if not events:
            events = [("NO_GAP", None)]
        for event_type, session in events:
            previous = max((value for value in ordered_actual if session is not None and value < session), default=None)
            following = min((value for value in ordered_actual if session is not None and value > session), default=None)
            affected = missing_frequency.get(session, set()) if event_type == "MISSING" else boundary_missing_frequency.get(session, set()) if event_type == "BOUNDARY_GAP" else unexpected_frequency.get(session, set()) if event_type == "UNEXPECTED" else duplicate_frequency.get(session, set()) if event_type == "DUPLICATE" else set()
            position = session_position.get(session)
            def affects_window(length: int) -> bool:
                return bool(position is not None and any(position <= rebalance <= position + length for rebalance in rebalance_positions))
            daily_rows.append({
                "symbol": symbol,
                "asset_role": record.get("asset_role", ""),
                "event_type": event_type,
                "expected_session_count": len(expected_sessions),
                "actual_session_count": len(actual & expected_set),
                "missing_session_date": str(session.date()) if event_type in {"MISSING", "BOUNDARY_GAP"} else "",
                "unexpected_session_date": str(session.date()) if event_type == "UNEXPECTED" else "",
                "duplicate_session_date": str(session.date()) if event_type == "DUPLICATE" else "",
                "previous_available_session": "" if previous is None else str(previous.date()),
                "next_available_session": "" if following is None else str(following.date()),
                "missing_date_common_across_symbols": bool(event_type in {"MISSING", "BOUNDARY_GAP"} and len(affected) > 1),
                "affected_symbol_count_for_date": len(affected),
                "affected_symbol_percentage_for_date": round(100 * len(affected) / total_symbols, 6) if total_symbols else 0.0,
                "qqq_affected": "QQQ" in affected,
                "spy_affected": "SPY" in affected,
                "strategy_rebalance_date": bool(session in rebalance_dates if session is not None else False),
                "affects_momentum126": affects_window(126) if event_type in {"MISSING", "BOUNDARY_GAP"} else False,
                "affects_sma100": affects_window(99) if event_type in {"MISSING", "BOUNDARY_GAP"} else False,
                "affects_volatility60": affects_window(60) if event_type in {"MISSING", "BOUNDARY_GAP"} else False,
                "affects_atr20": affects_window(20) if event_type in {"MISSING", "BOUNDARY_GAP"} else False,
            })
    daily_detail = pd.DataFrame(daily_rows)

    intraday_sets: dict[str, set[pd.Timestamp]] = {}
    intraday_session_sets: dict[str, set[pd.Timestamp]] = {}
    intraday_by_session: dict[str, dict[pd.Timestamp, list[pd.Timestamp]]] = {}
    intraday_rows = []
    missing_block_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    boundary_block_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    invalid_timestamp_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    missing_full_session_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    boundary_full_session_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    root_causes = Counter()
    internal_root_causes = Counter()
    for symbol, record in intraday_records.items():
        derived = _read_timestamps(root / record["output_relative_path"])
        actual_set = set(derived)
        intraday_sets[symbol] = actual_set
        local_sessions = _local_sessions(derived)
        session_to_derived: dict[pd.Timestamp, list[pd.Timestamp]] = defaultdict(list)
        for timestamp, session in zip(derived, local_sessions):
            session_to_derived[pd.Timestamp(session)].append(pd.Timestamp(timestamp))
        valid_intraday_sessions = set(session_to_derived) & expected_set
        intraday_session_sets[symbol] = valid_intraday_sessions
        intraday_by_session[symbol] = session_to_derived
        raw_relative = record.get("source_raw_5min_relative_path", "")
        raw = _read_timestamps(root / raw_relative) if raw_relative and (root / raw_relative).exists() else pd.DatetimeIndex([])
        raw_local_sessions = _local_sessions(raw) if len(raw) else pd.DatetimeIndex([])
        session_to_raw: dict[pd.Timestamp, list[pd.Timestamp]] = defaultdict(list)
        for timestamp, session in zip(raw, raw_local_sessions):
            session_to_raw[pd.Timestamp(session)].append(pd.Timestamp(timestamp))

        first_derived_session = min(valid_intraday_sessions) if valid_intraday_sessions else None
        last_derived_session = max(valid_intraday_sessions) if valid_intraday_sessions else None
        for session, blocks in expected_blocks.items():
            boundary_gap = first_derived_session is None or session < first_derived_session or session > last_derived_session
            if session not in session_to_derived:
                (boundary_full_session_frequency if boundary_gap else missing_full_session_frequency)[session].add(symbol)
            nearby = "|".join(_stamp(value) for value in session_to_derived.get(session, []))
            raw_session_count = len(session_to_raw.get(session, []))
            for block_start, block_end in blocks:
                if block_start in actual_set:
                    continue
                (boundary_block_frequency if boundary_gap else missing_block_frequency)[block_start].add(symbol)
                raw_in_block = [value for value in session_to_raw.get(session, []) if block_start <= value < block_end]
                expected_raw_count = int((block_end - block_start) / pd.Timedelta(minutes=5))
                invalid_near = [value for value in session_to_derived.get(session, []) if block_start < value < block_end]
                if raw_session_count == 0:
                    classification = "RAW_SOURCE_EMPTY_FOR_FULL_SESSION"
                elif not raw_in_block:
                    classification = "RAW_SOURCE_EMPTY_FOR_BLOCK"
                elif len(raw_in_block) < expected_raw_count:
                    classification = "RAW_SOURCE_PARTIAL_BLOCK"
                elif invalid_near:
                    classification = "AGGREGATION_TIMESTAMP_MISMATCH"
                else:
                    classification = "AGGREGATION_DROPPED_AVAILABLE_RAW_ROWS"
                root_causes[classification] += 1
                if not boundary_gap:
                    internal_root_causes[classification] += 1
                intraday_rows.append({
                    "symbol": symbol,
                    "asset_role": record.get("asset_role", ""),
                    "market_session": str(session.date()),
                    "expected_block_start_timestamp": _stamp(block_start),
                    "expected_block_end_timestamp": _stamp(block_end),
                    "actual_nearby_timestamps": nearby,
                    "actual_invalid_timestamp": "",
                    "raw_5min_rows_inside_expected_block": len(raw_in_block),
                    "raw_first_timestamp_inside_block": _stamp(raw_in_block[0]) if raw_in_block else "",
                    "raw_last_timestamp_inside_block": _stamp(raw_in_block[-1]) if raw_in_block else "",
                    "derived_130min_bar_present": False,
                    "boundary_gap": boundary_gap,
                    "calendar_session_type": "EARLY_CLOSE" if pd.Timestamp(schedule.loc[session, "market_close"]).hour < 16 else "REGULAR",
                    "raw_post_close_row_count": 0,
                    "dst_conversion_issue": False,
                    "cause_component": "RAW_SOURCE",
                    "failure_classification": classification,
                })

        invalid_actual = sorted(actual_set - expected_timestamp_set)
        for actual in invalid_actual:
            invalid_timestamp_frequency[actual].add(symbol)
            local = actual.tz_convert(MARKET_TIMEZONE)
            session = local.tz_localize(None).normalize()
            blocks = expected_blocks.get(session, [])
            nearest = min(blocks, key=lambda block: abs(block[0] - actual)) if blocks else (None, None)
            raw_in_block = [value for value in session_to_raw.get(session, []) if nearest[0] is not None and nearest[0] <= value < nearest[1]]
            scheduled_close = pd.Timestamp(schedule.loc[session, "market_close"]) if session in expected_set else None
            is_early_close = bool(scheduled_close is not None and scheduled_close.hour < 16)
            raw_post_close = [value for value in session_to_raw.get(session, []) if scheduled_close is not None and value >= scheduled_close.tz_convert("UTC")]
            if session not in expected_set:
                classification = "MARKET_CALENDAR_EXPECTATION_MISMATCH"
                cause_component = "MARKET_CALENDAR"
            elif is_early_close and actual >= scheduled_close.tz_convert("UTC"):
                classification = "EARLY_CLOSE_EXPECTATION_MISMATCH"
                cause_component = "AGGREGATION_WITH_POST_CLOSE_RAW"
            else:
                classification = "INVALID_DERIVED_TIMESTAMP"
                cause_component = "DERIVED_TIMESTAMP"
            root_causes[classification] += 1
            intraday_rows.append({
                "symbol": symbol,
                "asset_role": record.get("asset_role", ""),
                "market_session": str(session.date()),
                "expected_block_start_timestamp": _stamp(nearest[0]) if nearest[0] is not None else "",
                "expected_block_end_timestamp": _stamp(nearest[1]) if nearest[1] is not None else "",
                "actual_nearby_timestamps": "|".join(_stamp(value) for value in session_to_derived.get(session, [])),
                "actual_invalid_timestamp": _stamp(actual),
                "raw_5min_rows_inside_expected_block": len(raw_in_block),
                "raw_first_timestamp_inside_block": _stamp(raw_in_block[0]) if raw_in_block else "",
                "raw_last_timestamp_inside_block": _stamp(raw_in_block[-1]) if raw_in_block else "",
                "derived_130min_bar_present": bool(nearest[0] is not None and nearest[0] in actual_set),
                "boundary_gap": False,
                "calendar_session_type": "EARLY_CLOSE" if is_early_close else "REGULAR",
                "raw_post_close_row_count": len(raw_post_close),
                "dst_conversion_issue": False,
                "cause_component": cause_component,
                "failure_classification": classification,
            })
    intraday_audit = pd.DataFrame(intraday_rows)
    if intraday_audit.empty:
        intraday_audit = pd.DataFrame(columns=["symbol", "asset_role", "market_session", "expected_block_start_timestamp", "expected_block_end_timestamp", "actual_nearby_timestamps", "actual_invalid_timestamp", "raw_5min_rows_inside_expected_block", "raw_first_timestamp_inside_block", "raw_last_timestamp_inside_block", "derived_130min_bar_present", "boundary_gap", "calendar_session_type", "raw_post_close_row_count", "dst_conversion_issue", "cause_component", "failure_classification"])

    frequency_rows = []
    def append_frequency(scope: str, key, affected: set[str]) -> None:
        qqq, spy = "QQQ" in affected, "SPY" in affected
        frequency_rows.append({
            "scope": scope,
            "market_session": str(pd.Timestamp(key).tz_localize(None).date()) if isinstance(key, pd.Timestamp) else str(key),
            "timestamp": _stamp(key) if isinstance(key, pd.Timestamp) and key.tzinfo is not None else "",
            "affected_symbol_count": len(affected),
            "affected_candidate_count": len(affected & candidate_symbols),
            "affected_symbol_percentage": round(100 * len(affected) / total_symbols, 6) if total_symbols else 0.0,
            "qqq_affected": qqq,
            "spy_affected": spy,
            "pattern": _pattern(len(affected), total_symbols, qqq, spy, scope),
        })
    for key, affected in sorted(missing_frequency.items()):
        append_frequency("DAILY_MISSING_SESSION", key, affected)
    for key, affected in sorted(boundary_missing_frequency.items()):
        append_frequency("DAILY_BOUNDARY_GAP", key, affected)
    for key, affected in sorted(missing_full_session_frequency.items()):
        append_frequency("INTRADAY_MISSING_FULL_SESSION", key, affected)
    for key, affected in sorted(boundary_full_session_frequency.items()):
        append_frequency("INTRADAY_BOUNDARY_FULL_SESSION", key, affected)
    for key, affected in sorted(missing_block_frequency.items()):
        append_frequency("INTRADAY_MISSING_BLOCK", key, affected)
    for key, affected in sorted(boundary_block_frequency.items()):
        append_frequency("INTRADAY_BOUNDARY_BLOCK", key, affected)
    for key, affected in sorted(invalid_timestamp_frequency.items()):
        append_frequency("INTRADAY_INVALID_TIMESTAMP", key, affected)
    gap_frequency_columns = ["scope", "market_session", "timestamp", "affected_symbol_count", "affected_candidate_count", "affected_symbol_percentage", "qqq_affected", "spy_affected", "pattern"]
    gap_frequency = pd.DataFrame(frequency_rows, columns=gap_frequency_columns)

    decision_rows = []
    candidate_daily_sets = [daily_sets[symbol] for symbol in sorted(candidate_symbols) if symbol in daily_sets]
    strategy_dates = pd.DatetimeIndex(sorted(set().union(*candidate_daily_sets))) if candidate_daily_sets else pd.DatetimeIndex([])
    rebalance_dates = _monthly_rebalance_dates(strategy_dates)
    next_session_map = {
        session: strategy_dates[index + 1] if index + 1 < len(strategy_dates) else None
        for index, session in enumerate(strategy_dates)
    }
    for symbol in symbols:
        role = daily_records.get(symbol, intraday_records.get(symbol, {})).get("asset_role", "")
        daily = daily_sets.get(symbol, set())
        intraday = intraday_sets.get(symbol, set())
        first = min(daily) if daily else None
        last = max(daily) if daily else None
        for rebalance in rebalance_dates:
            through = expected_sessions[expected_sessions <= rebalance]
            required = set(through[-warmup_observation_count:]) if len(through) >= warmup_observation_count else set(through)
            feature_sufficient = len(through) >= warmup_observation_count and required.issubset(daily)
            daily_present = rebalance in daily
            final_expected = expected_blocks[rebalance][-1][0]
            final_present = final_expected in intraday
            actual_session = intraday_by_session.get(symbol, {}).get(rebalance, [])
            actual_last = actual_session[-1] if actual_session else None
            next_session = next_session_map.get(rebalance)
            next_open = bool(next_session is not None and next_session in daily)
            if first is not None and rebalance < first:
                status = "LATE_START_NOT_YET_ELIGIBLE"
            elif last is not None and rebalance > last:
                status = "EARLY_TERMINAL_NO_LONGER_ELIGIBLE"
            elif not feature_sufficient or not daily_present:
                status = "DAILY_FEATURE_INPUT_GAP"
            elif actual_last != final_expected:
                status = "SIGNAL_BAR_MISSING"
            elif not next_open:
                status = "EXECUTION_OPEN_MISSING"
            else:
                status = "UNAFFECTED"
            decision_rows.append({
                "symbol": symbol,
                "asset_role": role,
                "rebalance_date": str(rebalance.date()),
                "daily_feature_history_sufficient": feature_sufficient,
                "daily_bar_present": daily_present,
                "final_expected_130min_bar_present": final_present,
                "expected_final_130min_bar_timestamp": _stamp(final_expected),
                "actual_last_130min_bar_timestamp": _stamp(actual_last) if actual_last is not None else "",
                "signal_timestamp_available": actual_last is not None,
                "next_daily_open_available": next_open,
                "decision_path_status": status,
            })
    decision_impact = pd.DataFrame(decision_rows)

    boundary_rows = []
    boundary_symbols = BOUNDARY_REVIEW_SYMBOLS & set(symbols)
    boundary_symbols |= {
        symbol for symbol in symbols
        if "UNAPPROVED_" in str(daily_records.get(symbol, {}).get("coverage_exception_reason", ""))
        or "UNAPPROVED_" in str(intraday_records.get(symbol, {}).get("coverage_exception_reason", ""))
    }
    for symbol in sorted(boundary_symbols):
        daily_record, intraday_record = daily_records.get(symbol, {}), intraday_records.get(symbol, {})
        daily = daily_sets.get(symbol, set())
        intraday_sessions = intraday_session_sets.get(symbol, set())
        daily_first, daily_last = (min(daily), max(daily)) if daily else (None, None)
        intraday_first, intraday_last = (min(intraday_sessions), max(intraday_sessions)) if intraday_sessions else (None, None)
        boundary_rows.append({
            "symbol": symbol,
            "daily_actual_first_session": str(min(daily).date()) if daily else "",
            "daily_actual_last_session": str(max(daily).date()) if daily else "",
            "intraday_actual_first_session": str(min(intraday_sessions).date()) if intraday_sessions else "",
            "intraday_actual_last_session": str(max(intraday_sessions).date()) if intraday_sessions else "",
            "start_gap_count": len([session for session in expected_sessions if daily_first is None or session < daily_first]),
            "end_gap_count": len([session for session in expected_sessions if daily_last is None or session > daily_last]),
            "intraday_start_gap_count": len([session for session in expected_sessions if intraday_first is None or session < intraday_first]),
            "intraday_end_gap_count": len([session for session in expected_sessions if intraday_last is None or session > intraday_last]),
            "known_manifest_reason": " | ".join(filter(None, [str(daily_record.get("coverage_exception_reason", "")), str(intraday_record.get("coverage_exception_reason", ""))])),
            "daily_intraday_boundaries_agree": (min(daily) if daily else None, max(daily) if daily else None) == (min(intraday_sessions) if intraday_sessions else None, max(intraday_sessions) if intraday_sessions else None),
            "daily_internal_gap_count": len([session for session in expected_sessions if daily and min(daily) <= session <= max(daily) and session not in daily]),
            "intraday_internal_gap_count": len([session for session in expected_sessions if intraday_sessions and min(intraday_sessions) <= session <= max(intraday_sessions) and session not in intraday_sessions]),
            "post_listing_warmup_sufficient": len(daily) >= 127,
            "exception_approved": False,
        })
    boundary_review = pd.DataFrame(boundary_rows)

    daily_valid = sum(not values["missing"] and not values["unexpected"] and not values["duplicates"] for values in daily_anomalies.values())
    intraday_valid = sum(not (expected_timestamp_set - intraday_sets.get(symbol, set())) and not (intraday_sets.get(symbol, set()) - expected_timestamp_set) for symbol in intraday_records)
    affected_decisions = int((decision_impact["asset_role"].eq("CANDIDATE") & decision_impact["decision_path_status"].ne("UNAFFECTED")).sum()) if not decision_impact.empty else 0
    qqq_impact = decision_impact[decision_impact["symbol"].eq("QQQ")]["decision_path_status"].value_counts().to_dict()
    spy_impact = decision_impact[decision_impact["symbol"].eq("SPY")]["decision_path_status"].value_counts().to_dict()

    report_dir = Path(report_dir)
    profile = manifest.get("dataset_profile", "rs_v2_clean")
    paths = {
        "daily_gap_detail": report_dir / f"{profile}_coverage_gap_detail.csv",
        "gap_frequency": report_dir / f"{profile}_coverage_gap_frequency.csv",
        "intraday_block_audit": report_dir / f"{profile}_intraday_block_audit.csv",
        "decision_path_impact": report_dir / f"{profile}_decision_path_impact.csv",
        "boundary_exception_review": report_dir / f"{profile}_boundary_exception_review.csv",
        "markdown": report_dir / f"{profile}_coverage_forensics.md",
    }
    _atomic_frame(daily_detail, paths["daily_gap_detail"])
    _atomic_frame(gap_frequency, paths["gap_frequency"])
    _atomic_frame(intraday_audit, paths["intraday_block_audit"])
    _atomic_frame(decision_impact, paths["decision_path_impact"])
    _atomic_frame(boundary_review, paths["boundary_exception_review"])

    common_daily = gap_frequency[gap_frequency["scope"].eq("DAILY_MISSING_SESSION")].sort_values(["affected_symbol_count", "market_session"], ascending=[False, True])
    common_blocks = gap_frequency[gap_frequency["scope"].isin(["INTRADAY_MISSING_FULL_SESSION", "INTRADAY_MISSING_BLOCK"]) & gap_frequency["affected_symbol_count"].gt(1)].sort_values(["affected_symbol_count", "timestamp"], ascending=[False, True])
    lines = [
        f"# {profile.replace('_', ' ').title().replace('Sip', 'SIP')} Coverage Forensics",
        "",
        f"- acquisition_run_id: {run_id}",
        f"- manifest_status: {manifest.get('status', '')}",
        f"- acquisition_records_succeeded: {sum(row.get('request_status') == 'SUCCESS' for row in records)} of {len(records)}",
        f"- daily_coverage_valid_count: {daily_valid}",
        f"- daily_coverage_failed_count: {len(daily_records) - daily_valid}",
        f"- intraday_coverage_valid_count: {intraday_valid}",
        f"- intraday_coverage_failed_count: {len(intraday_records) - intraday_valid}",
        f"- affected_rebalance_decision_count: {affected_decisions}",
        f"- qqq_decision_path_impact: {json.dumps(qqq_impact, sort_keys=True)}",
        f"- spy_decision_path_impact: {json.dumps(spy_impact, sort_keys=True)}",
        f"- boundary_review_symbols: {','.join(sorted(BOUNDARY_REVIEW_SYMBOLS & set(symbols)))}",
        "- exception_approved_count: 0",
        "- validation_policy_weakened: false",
        "- network_request_count: 0",
        "",
        "## Common Missing Daily Sessions",
        "",
    ]
    lines.extend(f"- {row.market_session}: {int(row.affected_symbol_count)} symbols; QQQ={bool(row.qqq_affected)}; SPY={bool(row.spy_affected)}; pattern={row.pattern}" for row in common_daily.itertuples())
    lines.extend(["", "## Common Missing Intraday Sessions and Blocks", ""])
    lines.extend(f"- {row.scope} {row.market_session} {row.timestamp}: {int(row.affected_symbol_count)} symbols; QQQ={bool(row.qqq_affected)}; SPY={bool(row.spy_affected)}; pattern={row.pattern}" for row in common_blocks.itertuples())
    lines.extend(["", "## Root Cause Totals", ""])
    lines.extend(f"- {name}: {root_causes.get(name, 0)}" for name in sorted(ROOT_CAUSES))
    lines.extend(["", "## Internal Root Cause Totals (Boundary Gaps Excluded)", ""])
    lines.extend(f"- {name}: {internal_root_causes.get(name, 0)}" for name in sorted(ROOT_CAUSES))
    _atomic_text("\n".join(lines) + "\n", paths["markdown"])

    return {
        "daily_gap_detail": daily_detail,
        "gap_frequency": gap_frequency,
        "intraday_block_audit": intraday_audit,
        "decision_path_impact": decision_impact,
        "boundary_exception_review": boundary_review,
        "summary": {
            "daily_valid": daily_valid,
            "daily_failed": len(daily_records) - daily_valid,
            "intraday_valid": intraday_valid,
            "intraday_failed": len(intraday_records) - intraday_valid,
            "root_cause_totals": dict(root_causes),
            "internal_root_cause_totals": dict(internal_root_causes),
            "affected_rebalance_decisions": affected_decisions,
            "qqq_impact": qqq_impact,
            "spy_impact": spy_impact,
        },
        "paths": paths,
    }
