from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

try:
    from .v2_coverage_forensics import (
        _atomic_frame,
        _atomic_text,
        _expected_blocks,
        _local_sessions,
        _monthly_rebalance_dates,
        _normalize_schedule,
        _read_timestamps,
        _stamp,
    )
    from .v2_session_coverage import MARKET_TIMEZONE, approved_nyse_schedule, load_reviewed_coverage_exceptions
    from .vericekici_updated import aggregate_to_130min
except ImportError:
    from v2_coverage_forensics import (
        _atomic_frame,
        _atomic_text,
        _expected_blocks,
        _local_sessions,
        _monthly_rebalance_dates,
        _normalize_schedule,
        _read_timestamps,
        _stamp,
    )
    from v2_session_coverage import MARKET_TIMEZONE, approved_nyse_schedule, load_reviewed_coverage_exceptions
    from vericekici_updated import aggregate_to_130min


CRITICAL_DECISION_STATUSES = {
    "INTERNAL_DAILY_GAP_AFFECTS_FEATURES",
    "TRUE_SIGNAL_BAR_MISSING",
    "TRUE_EXECUTION_OPEN_MISSING",
}


def _read_bars(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
    return frame.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)


def _by_session(timestamps: pd.DatetimeIndex) -> dict[pd.Timestamp, list[pd.Timestamp]]:
    output: dict[pd.Timestamp, list[pd.Timestamp]] = defaultdict(list)
    for timestamp, session in zip(timestamps, _local_sessions(timestamps)):
        output[pd.Timestamp(session)].append(pd.Timestamp(timestamp))
    return output


def audit_corrected_v2_aggregation(
    dataset_root: Path,
    run_id: str,
    report_dir: Path,
    *,
    market_schedule: pd.DataFrame | None = None,
    warmup_observation_count: int = 127,
    offline_telemetry: dict | None = None,
) -> dict:
    """Audit corrected V2 aggregation in memory without mutating frozen inputs."""
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
    expected_timestamps = {block_start for blocks in expected_blocks.values() for block_start, _ in blocks}

    records = manifest.get("files", [])
    daily_records = {row["symbol"]: row for row in records if row.get("timeframe") == "1D"}
    intraday_records = {row["symbol"]: row for row in records if row.get("timeframe") == "130min"}
    symbols = sorted(set(daily_records) | set(intraday_records))
    candidates = {symbol for symbol, row in daily_records.items() if row.get("asset_role") == "CANDIDATE"}

    daily_sets: dict[str, set[pd.Timestamp]] = {}
    daily_strict: dict[str, bool] = {}
    for symbol, record in daily_records.items():
        sessions = _local_sessions(_read_timestamps(root / record["output_relative_path"]))
        counts = Counter(sessions)
        actual = set(counts)
        daily_sets[symbol] = actual & expected_set
        daily_strict[symbol] = actual == expected_set and all(count == 1 for count in counts.values())

    old_sets: dict[str, set[pd.Timestamp]] = {}
    corrected_sets: dict[str, set[pd.Timestamp]] = {}
    old_sessions_by_symbol = {}
    corrected_sessions_by_symbol = {}
    raw_sessions_by_symbol = {}
    intraday_strict: dict[str, bool] = {}
    missing_session_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    missing_block_frequency: dict[pd.Timestamp, set[str]] = defaultdict(set)
    internal_raw_empty_full_session_count = 0
    internal_raw_empty_block_count = 0
    block_ends = {block_start: block_end for blocks in expected_blocks.values() for block_start, block_end in blocks}
    delta_rows = []

    for symbol, record in intraday_records.items():
        old = _read_bars(root / record["output_relative_path"])
        raw = _read_bars(root / record["source_raw_5min_relative_path"])
        corrected = aggregate_to_130min(raw, schedule, dataset_end=end)
        old_set = set(pd.DatetimeIndex(old["Date"]))
        corrected_set = set(pd.DatetimeIndex(corrected["Date"]))
        old_sets[symbol] = old_set
        corrected_sets[symbol] = corrected_set
        old_by_session = _by_session(pd.DatetimeIndex(old["Date"]))
        corrected_by_session = _by_session(pd.DatetimeIndex(corrected["Date"]))
        raw_by_session = _by_session(pd.DatetimeIndex(raw["Date"]))
        old_sessions_by_symbol[symbol] = old_by_session
        corrected_sessions_by_symbol[symbol] = corrected_by_session
        raw_sessions_by_symbol[symbol] = raw_by_session

        removed_post_close = []
        for timestamp in sorted(old_set - corrected_set):
            session = timestamp.tz_convert(MARKET_TIMEZONE).tz_localize(None).normalize()
            if session in expected_set and timestamp >= pd.Timestamp(schedule.loc[session, "market_close"]).tz_convert("UTC"):
                removed_post_close.append(timestamp)
        restored_final = []
        for session, blocks in expected_blocks.items():
            close = pd.Timestamp(schedule.loc[session, "market_close"])
            final_start = blocks[-1][0]
            if close.hour < 16 and final_start in corrected_set and final_start not in old_set:
                restored_final.append(final_start)
        missing_blocks = expected_timestamps - corrected_set
        missing_sessions = expected_set - (set(corrected_by_session) & expected_set)
        invalid = corrected_set - expected_timestamps
        valid_sessions = set(corrected_by_session) & expected_set
        first_session = min(valid_sessions) if valid_sessions else None
        last_session = max(valid_sessions) if valid_sessions else None
        for session in missing_sessions:
            if first_session is not None and first_session <= session <= last_session:
                missing_session_frequency[session].add(symbol)
        for timestamp in missing_blocks:
            session = timestamp.tz_convert(MARKET_TIMEZONE).tz_localize(None).normalize()
            if first_session is not None and first_session <= session <= last_session:
                missing_block_frequency[timestamp].add(symbol)
                market_open = pd.Timestamp(schedule.loc[session, "market_open"]).tz_convert("UTC")
                market_close = pd.Timestamp(schedule.loc[session, "market_close"]).tz_convert("UTC")
                raw_session = [value for value in raw_by_session.get(session, []) if market_open <= value < market_close]
                raw_in_block = [value for value in raw_session if timestamp <= value < block_ends[timestamp]]
                if not raw_session:
                    internal_raw_empty_full_session_count += 1
                elif not raw_in_block:
                    internal_raw_empty_block_count += 1
        affected_sessions = sorted({timestamp.tz_convert(MARKET_TIMEZONE).date().isoformat() for timestamp in removed_post_close + restored_final})
        delta_rows.append({
            "symbol": symbol,
            "asset_role": record.get("asset_role", ""),
            "old_derived_bar_count": len(old),
            "corrected_derived_bar_count": len(corrected),
            "removed_post_close_bar_count": len(removed_post_close),
            "newly_restored_valid_early_close_final_bar_count": len(restored_final),
            "remaining_missing_session_count": len(missing_sessions),
            "remaining_missing_block_count": len(missing_blocks),
            "remaining_invalid_timestamp_count": len(invalid),
            "affected_market_sessions": "|".join(affected_sessions),
            "qqq_impact": symbol == "QQQ" and bool(affected_sessions),
            "spy_impact": symbol == "SPY" and bool(affected_sessions),
        })
        intraday_strict[symbol] = not missing_blocks and not invalid and len(corrected_set) == len(expected_timestamps)

    delta = pd.DataFrame(delta_rows)
    candidate_daily_sets = [daily_sets[symbol] for symbol in sorted(candidates)]
    strategy_dates = pd.DatetimeIndex(sorted(set().union(*candidate_daily_sets)))
    rebalance_dates = _monthly_rebalance_dates(strategy_dates)
    session_positions = {session: position for position, session in enumerate(expected_sessions)}
    decision_rows = []

    for symbol in symbols:
        role = daily_records.get(symbol, intraday_records.get(symbol, {})).get("asset_role", "")
        daily = daily_sets.get(symbol, set())
        first, last = (min(daily), max(daily)) if daily else (None, None)
        corrected = corrected_sets.get(symbol, set())
        old = old_sets.get(symbol, set())
        for rebalance in rebalance_dates:
            position = session_positions[rebalance]
            through = expected_sessions[: position + 1]
            next_session = expected_sessions[position + 1] if position + 1 < len(expected_sessions) else None
            required = set(through[-warmup_observation_count:]) if len(through) >= warmup_observation_count else set(through)
            since_listing = expected_sessions[(expected_sessions >= first) & (expected_sessions <= rebalance)] if first is not None else pd.DatetimeIndex([])
            missing_required = sorted(required - daily)
            final_expected = expected_blocks[rebalance][-1][0]
            signal_available = final_expected in corrected
            if first is None or rebalance < first:
                status = "LATE_START_NOT_YET_ELIGIBLE"
            elif last is not None and rebalance > last:
                status = "EARLY_TERMINAL_NO_LONGER_ELIGIBLE"
            elif len(through) < warmup_observation_count:
                status = "EXPECTED_INITIAL_COLD_START"
            elif first > expected_sessions[0] and len(since_listing) < warmup_observation_count:
                status = "EXPECTED_POST_LISTING_WARMUP"
            elif missing_required:
                status = "INTERNAL_DAILY_GAP_AFFECTS_FEATURES"
            elif not signal_available:
                status = "TRUE_SIGNAL_BAR_MISSING"
            elif next_session is None:
                status = "EXPECTED_RIGHT_CENSORED_FINAL_EXECUTION"
            elif next_session not in daily:
                status = "TRUE_EXECUTION_OPEN_MISSING"
            else:
                status = "UNAFFECTED"

            raw_session = raw_sessions_by_symbol.get(symbol, {}).get(rebalance, [])
            old_session = old_sessions_by_symbol.get(symbol, {}).get(rebalance, [])
            corrected_session = corrected_sessions_by_symbol.get(symbol, {}).get(rebalance, [])
            official_close = pd.Timestamp(schedule.loc[rebalance, "market_close"]).tz_convert("UTC")
            pre_close = [timestamp for timestamp in raw_session if timestamp < official_close]
            post_close = [timestamp for timestamp in raw_session if timestamp >= official_close]
            old_last = max(old_session) if old_session else None
            corrected_last = max(corrected_session) if corrected_session else None
            if old_last is not None and old_last >= official_close and final_expected in old:
                prior_status = "INVALID_POST_CLOSE_SIGNAL_BAR_SELECTED"
            elif final_expected not in old:
                prior_status = "TRUE_SIGNAL_BAR_MISSING"
            else:
                prior_status = "SIGNAL_BAR_AVAILABLE"
            decision_rows.append({
                "symbol": symbol,
                "asset_role": role,
                "rebalance_date": str(rebalance.date()),
                "eligibility_status": status if status in {"LATE_START_NOT_YET_ELIGIBLE", "EARLY_TERMINAL_NO_LONGER_ELIGIBLE"} else "ELIGIBLE",
                "daily_feature_status": status if status in {"EXPECTED_INITIAL_COLD_START", "EXPECTED_POST_LISTING_WARMUP", "INTERNAL_DAILY_GAP_AFFECTS_FEATURES"} else "FEATURES_AVAILABLE",
                "signal_bar_status": "SIGNAL_BAR_AVAILABLE" if signal_available else "TRUE_SIGNAL_BAR_MISSING",
                "execution_status": status if status in {"EXPECTED_RIGHT_CENSORED_FINAL_EXECUTION", "TRUE_EXECUTION_OPEN_MISSING"} else "EXECUTION_OPEN_AVAILABLE",
                "decision_path_status": status,
                "missing_required_daily_session_count": len(missing_required),
                "expected_final_130min_bar_timestamp": _stamp(final_expected),
                "corrected_final_signal_bar_available": signal_available,
                "next_expected_market_session": "" if next_session is None else str(next_session.date()),
                "next_daily_open_available": bool(next_session is not None and next_session in daily),
                "official_market_close": _stamp(official_close),
                "raw_last_pre_close_timestamp": _stamp(max(pre_close)) if pre_close else "",
                "raw_post_close_row_count": len(post_close),
                "old_final_derived_timestamp": _stamp(old_last) if old_last is not None else "",
                "corrected_final_derived_timestamp": _stamp(corrected_last) if corrected_last is not None else "",
                "decision_path_status_before": prior_status,
                "decision_path_status_after": status,
            })

    decisions = pd.DataFrame(decision_rows)
    decision_fail = decisions["decision_path_status"].isin(CRITICAL_DECISION_STATUSES)
    candidate_decisions = decisions[decisions["symbol"].isin(candidates)]
    candidate_decision_fail = candidate_decisions["decision_path_status"].isin(CRITICAL_DECISION_STATUSES)
    daily_fail_symbols = set(decisions.loc[decisions["decision_path_status"].isin({"INTERNAL_DAILY_GAP_AFFECTS_FEATURES", "TRUE_EXECUTION_OPEN_MISSING"}), "symbol"])
    intraday_fail_symbols = set(decisions.loc[decisions["decision_path_status"].eq("TRUE_SIGNAL_BAR_MISSING"), "symbol"])
    strict_record_pass = sum(daily_strict.values()) + sum(intraday_strict.values())
    strict_symbol_pass = sum(daily_strict.get(symbol, False) and intraday_strict.get(symbol, False) for symbol in symbols)
    decision_record_pass = sum(symbol not in daily_fail_symbols for symbol in daily_records) + sum(symbol not in intraday_fail_symbols for symbol in intraday_records)
    decision_symbol_pass = sum(symbol not in (daily_fail_symbols | intraday_fail_symbols) for symbol in symbols)
    strict_decision_pass = sum(daily_strict.get(row.symbol, False) and intraday_strict.get(row.symbol, False) for row in decisions.itertuples())
    date_has_failure = decisions.groupby("rebalance_date")["decision_path_status"].apply(lambda values: values.isin(CRITICAL_DECISION_STATUSES).any())
    policy = {
        "STRICT_ALL_SESSION_POLICY": {
            "record_pass_count": strict_record_pass,
            "record_fail_count": len(records) - strict_record_pass,
            "symbol_pass_count": strict_symbol_pass,
            "symbol_fail_count": len(symbols) - strict_symbol_pass,
            "dataset_global_strict_decision_row_pass_count": strict_decision_pass,
            "dataset_global_strict_decision_row_fail_count": len(decisions) - strict_decision_pass,
            "per_decision_required_input_strict_row_pass_count": int((~decision_fail).sum()),
            "per_decision_required_input_strict_row_fail_count": int(decision_fail.sum()),
        },
        "STRATEGY_DECISION_PATH_POLICY": {
            "scope": "ALL_180_ACQUIRED_SYMBOLS_WITH_QQQ_SPY_DIAGNOSTIC_ROWS",
            "record_pass_count": decision_record_pass,
            "record_fail_count": len(records) - decision_record_pass,
            "symbol_pass_count": decision_symbol_pass,
            "symbol_fail_count": len(symbols) - decision_symbol_pass,
            "rebalance_decision_pass_count": int((~decision_fail).sum()),
            "rebalance_decision_fail_count": int(decision_fail.sum()),
            "rebalance_date_pass_count": int((~date_has_failure).sum()),
            "rebalance_date_fail_count": int(date_has_failure.sum()),
            "candidate_record_pass_count": 2 * len(candidates) - len(daily_fail_symbols & candidates) - len(intraday_fail_symbols & candidates),
            "candidate_record_fail_count": len(daily_fail_symbols & candidates) + len(intraday_fail_symbols & candidates),
            "candidate_symbol_pass_count": len(candidates - daily_fail_symbols - intraday_fail_symbols),
            "candidate_symbol_fail_count": len(candidates & (daily_fail_symbols | intraday_fail_symbols)),
            "candidate_rebalance_decision_pass_count": int((~candidate_decision_fail).sum()),
            "candidate_rebalance_decision_fail_count": int(candidate_decision_fail.sum()),
        },
    }
    status_counts = decisions["decision_path_status"].value_counts().to_dict()

    report_dir = Path(report_dir)
    paths = {
        "aggregation_delta": report_dir / "rs_v2_clean_corrected_aggregation_delta.csv",
        "aggregation_markdown": report_dir / "rs_v2_clean_corrected_aggregation_forensics.md",
        "decision_path_impact": report_dir / "rs_v2_clean_corrected_decision_path_impact.csv",
        "decision_markdown": report_dir / "rs_v2_clean_corrected_decision_path_summary.md",
    }
    _atomic_frame(delta, paths["aggregation_delta"])
    _atomic_frame(decisions, paths["decision_path_impact"])

    totals = {
        "old_derived_bar_count": int(delta["old_derived_bar_count"].sum()),
        "corrected_derived_bar_count": int(delta["corrected_derived_bar_count"].sum()),
        "removed_post_close_bar_count": int(delta["removed_post_close_bar_count"].sum()),
        "newly_restored_valid_early_close_final_bar_count": int(delta["newly_restored_valid_early_close_final_bar_count"].sum()),
        "remaining_missing_session_count": int(delta["remaining_missing_session_count"].sum()),
        "remaining_missing_block_count": int(delta["remaining_missing_block_count"].sum()),
        "remaining_invalid_timestamp_count": int(delta["remaining_invalid_timestamp_count"].sum()),
        "internal_raw_empty_full_session_classification_count": internal_raw_empty_full_session_count,
        "internal_raw_empty_block_classification_count": internal_raw_empty_block_count,
    }
    affected_symbols = delta.loc[delta["removed_post_close_bar_count"].gt(0) | delta["newly_restored_valid_early_close_final_bar_count"].gt(0), "symbol"].tolist()
    affected_sessions = sorted({value for values in delta["affected_market_sessions"] for value in str(values).split("|") if value})
    totals.update({"affected_symbols": affected_symbols, "affected_market_sessions": affected_sessions})
    exception_path = Path(__file__).resolve().parent / "baselines" / "rs_v2_clean_coverage_exceptions.json"
    approved_exception_count = len(load_reviewed_coverage_exceptions(exception_path))
    measured_network_count = offline_telemetry.get("network_attempt_count") if offline_telemetry is not None else "UNAVAILABLE_NOT_GUESSED"
    measured_client_count = offline_telemetry.get("client_attempt_count") if offline_telemetry is not None else "UNAVAILABLE_NOT_GUESSED"

    aggregation_lines = ["# V2 Corrected Aggregation Forensics", "", f"- acquisition_run_id: {run_id}"]
    aggregation_lines.extend(f"- {key}: {','.join(value) if isinstance(value, list) else value}" for key, value in totals.items())
    aggregation_lines.extend([
        f"- early_close_mismatch_count_before: {totals['removed_post_close_bar_count']}",
        f"- early_close_mismatch_count_after: {totals['remaining_invalid_timestamp_count']}",
        f"- qqq_removed_post_close_bar_count: {int(delta.loc[delta.symbol.eq('QQQ'), 'removed_post_close_bar_count'].sum())}",
        f"- spy_removed_post_close_bar_count: {int(delta.loc[delta.symbol.eq('SPY'), 'removed_post_close_bar_count'].sum())}",
        f"- network_request_count: {measured_network_count}",
        f"- client_attempt_count: {measured_client_count}",
        "", "## Common Remaining Internal Missing Sessions", "",
    ])
    for session, affected in sorted(missing_session_frequency.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(affected) > 1:
            aggregation_lines.append(f"- {session.date()}: {len(affected)} symbols")
    aggregation_lines.extend(["", "## Common Remaining Internal Missing Blocks", ""])
    for timestamp, affected in sorted(missing_block_frequency.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(affected) > 1:
            aggregation_lines.append(f"- {_stamp(timestamp)}: {len(affected)} symbols")
    _atomic_text("\n".join(aggregation_lines) + "\n", paths["aggregation_markdown"])

    decision_lines = ["# V2 Corrected Decision Path Summary", ""]
    labels = [
        "EXPECTED_INITIAL_COLD_START", "EXPECTED_POST_LISTING_WARMUP", "INTERNAL_DAILY_GAP_AFFECTS_FEATURES",
        "TRUE_SIGNAL_BAR_MISSING", "EXPECTED_RIGHT_CENSORED_FINAL_EXECUTION", "TRUE_EXECUTION_OPEN_MISSING",
        "LATE_START_NOT_YET_ELIGIBLE", "EARLY_TERMINAL_NO_LONGER_ELIGIBLE", "UNAFFECTED",
    ]
    decision_lines.extend(f"- {label.lower()}_rows: {status_counts.get(label, 0)}" for label in labels)
    decision_lines.extend(["", "## Policy Comparison (Diagnostic Only)", ""])
    for policy_name, values in policy.items():
        decision_lines.extend([f"### {policy_name}", ""])
        decision_lines.extend(f"- {key}: {value}" for key, value in values.items())
        decision_lines.append("")
    decision_lines.extend([
        "- acceptance_policy_changed: false",
        f"- approved_exception_count: {approved_exception_count}",
        f"- network_request_count: {measured_network_count}",
        f"- client_attempt_count: {measured_client_count}",
    ])
    _atomic_text("\n".join(decision_lines) + "\n", paths["decision_markdown"])
    return {"aggregation_delta": delta, "decision_path_impact": decisions, "status_counts": status_counts, "policy_comparison": policy, "summary": totals, "paths": paths}
