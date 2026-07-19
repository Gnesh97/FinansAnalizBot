from __future__ import annotations

import json
import hashlib
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from .strategies.relative_strength_rotation import (
        _normalize_relative_strength_params,
        _relative_strength_daily_features,
        _rs_rebalance_mask,
    )
    from .v2_coverage_forensics import _atomic_frame, _atomic_text, _normalize_schedule
    from .v2_session_coverage import MARKET_TIMEZONE, approved_nyse_schedule, load_reviewed_coverage_exceptions
except ImportError:
    from strategies.relative_strength_rotation import (
        _normalize_relative_strength_params,
        _relative_strength_daily_features,
        _rs_rebalance_mask,
    )
    from v2_coverage_forensics import _atomic_frame, _atomic_text, _normalize_schedule
    from v2_session_coverage import MARKET_TIMEZONE, approved_nyse_schedule, load_reviewed_coverage_exceptions


BLOCKED_ETFS = {
    "DIA", "IBB", "IWM", "QQQ", "RSP", "SMH", "SOXX", "SPY", "VTI", "XBI",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY", "XRT",
}
PRICE_RELATIVE_TOLERANCE = 0.0025
PRICE_ABSOLUTE_TOLERANCE = 0.01
VOLUME_RELATIVE_TOLERANCE = 0.25
PRICE_HARD_CAP = 0.01
VOLUME_HARD_CAP = 0.50
MIN_CALIBRATION_SESSIONS = 252
MIN_TOLERANCE_PASS_RATE = 0.99
SPLIT_SCALE_THRESHOLD = 0.05
EXPECTED_STRATEGY_IDENTIFIER = "rs_m126_top30_sma100_no_regime_v2_clean_stock"
EXPECTED_CANDIDATE_COUNT = 178
EXPECTED_CANDIDATE_HASH = "0187534f0334d762a99e8d76f4077d44eff833f2d4a782ee8c691734b62c2611"
EXPECTED_STRATEGY = {
    "momentum_lookback_days": 126,
    "volatility_lookback_days": 60,
    "sma_filter_days": 100,
    "top_n": 30,
    "use_market_regime": False,
    "score_mode": "return_over_vol",
    "rebalance_frequency": "monthly",
    "max_atr_pct": 12,
}


def validate_audit_contract(
    manifest: dict,
    baseline: dict,
    run_id: str,
    *,
    expected_candidate_count: int = EXPECTED_CANDIDATE_COUNT,
    expected_candidate_hash: str = EXPECTED_CANDIDATE_HASH,
) -> set[str]:
    universe = baseline.get("universe", {})
    candidates = list(universe.get("candidate_symbols", []))
    digest = hashlib.sha256(",".join(candidates).encode("utf-8")).hexdigest()
    expected_roles = {symbol: "CANDIDATE" for symbol in candidates}
    expected_roles.update({"QQQ": "PRIMARY_BENCHMARK", "SPY": "SECONDARY_BENCHMARK"})
    records = manifest.get("files", [])
    actual_roles = {
        timeframe: {row.get("symbol"): row.get("asset_role") for row in records if row.get("timeframe") == timeframe}
        for timeframe in ("1D", "130min")
    }
    strategy_valid = all(baseline.get("strategy", {}).get(key) == value for key, value in EXPECTED_STRATEGY.items())
    valid = (
        manifest.get("acquisition_run_id") == run_id
        and manifest.get("dataset_profile") == "rs_v2_clean"
        and manifest.get("strategy_identifier") == EXPECTED_STRATEGY_IDENTIFIER
        and manifest.get("candidate_count") == expected_candidate_count
        and manifest.get("candidate_universe_hash") == expected_candidate_hash
        and baseline.get("strategy_identifier") == EXPECTED_STRATEGY_IDENTIFIER
        and strategy_valid
        and universe.get("candidate_count") == expected_candidate_count
        and universe.get("candidate_universe_hash") == expected_candidate_hash
        and universe.get("min_price") == 10
        and len(candidates) == expected_candidate_count
        and len(candidates) == len(set(candidates))
        and digest == expected_candidate_hash
        and set(universe.get("excluded_symbols", [])) == BLOCKED_ETFS
        and actual_roles["1D"] == expected_roles
        and actual_roles["130min"] == expected_roles
        and len(records) == 2 * len(expected_roles)
        and not (set(candidates) & BLOCKED_ETFS)
    )
    if not valid:
        error = "V2_SELECTION_AUDIT_STRATEGY_CONTRACT_MISMATCH" if not strategy_valid else "V2_SELECTION_AUDIT_UNIVERSE_CONTRACT_MISMATCH"
        raise ValueError(error)
    return set(candidates)


def _empty_bar_result(status: str, **extra) -> dict:
    return {"status": status, "bar": None, "rth_row_count": 0, "expected_row_count": 0, "excluded_outside_rth_row_count": 0, **extra}


def reconstruct_daily_bar(raw: pd.DataFrame, market_open: pd.Timestamp, market_close: pd.Timestamp) -> dict:
    """Pure close-exclusive RTH daily reconstruction from one raw session."""
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if raw is None or raw.empty:
        return _empty_bar_result("RAW_SESSION_ABSENT")
    if any(column not in raw.columns for column in required):
        return _empty_bar_result("RAW_SESSION_INVALID")
    frame = raw[required].copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
    open_utc = pd.Timestamp(market_open).tz_convert("UTC")
    close_utc = pd.Timestamp(market_close).tz_convert("UTC")
    outside = int((frame["Date"].lt(open_utc) | frame["Date"].ge(close_utc)).fillna(True).sum())
    frame = frame[frame["Date"].ge(open_utc) & frame["Date"].lt(close_utc)].sort_values("Date")
    expected = pd.date_range(open_utc, close_utc - pd.Timedelta(minutes=5), freq="5min")
    if frame.empty:
        return _empty_bar_result("RAW_SESSION_ABSENT", expected_row_count=len(expected), excluded_outside_rth_row_count=outside)
    numeric = frame[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    integrity = (
        numeric.notna().all(axis=1)
        & np.isfinite(numeric).all(axis=1)
        & numeric["Volume"].ge(0)
        & numeric["High"].ge(numeric[["Open", "Close"]].max(axis=1))
        & numeric["Low"].le(numeric[["Open", "Close"]].min(axis=1))
    )
    timestamps = pd.DatetimeIndex(frame["Date"])
    if not integrity.all() or timestamps.has_duplicates or not timestamps.is_monotonic_increasing:
        return _empty_bar_result("RAW_SESSION_INVALID", rth_row_count=len(frame), expected_row_count=len(expected), excluded_outside_rth_row_count=outside)
    if not timestamps.equals(expected):
        return _empty_bar_result("RAW_SESSION_PRESENT_BUT_INCOMPLETE", rth_row_count=len(frame), expected_row_count=len(expected), excluded_outside_rth_row_count=outside)
    bar = {
        "Open": float(numeric.iloc[0]["Open"]),
        "High": float(numeric["High"].max()),
        "Low": float(numeric["Low"].min()),
        "Close": float(numeric.iloc[-1]["Close"]),
        "Volume": float(numeric["Volume"].sum()),
    }
    return {"status": "COMPLETE_RAW_SESSION", "bar": bar, "rth_row_count": len(frame), "expected_row_count": len(expected), "excluded_outside_rth_row_count": outside}


def _aggregate_raw_sessions(raw: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    columns = ["Open", "High", "Low", "Close", "Volume", "raw_session_status", "rth_row_count", "expected_row_count"]
    if raw.empty:
        return pd.DataFrame(columns=columns)
    frame = raw.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["Date"]).sort_values("Date")
    local = frame["Date"].dt.tz_convert(MARKET_TIMEZONE)
    frame["session"] = local.dt.tz_localize(None).dt.normalize()
    windows = schedule[["market_open", "market_close"]].copy()
    windows["market_open"] = pd.to_datetime(windows["market_open"], utc=True)
    windows["market_close"] = pd.to_datetime(windows["market_close"], utc=True)
    frame = frame.join(windows, on="session")
    frame = frame[frame["market_open"].notna() & frame["Date"].ge(frame["market_open"]) & frame["Date"].lt(frame["market_close"])].copy()
    if frame.empty:
        return pd.DataFrame(columns=columns)
    numeric_columns = ["Open", "High", "Low", "Close", "Volume"]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    frame["row_valid"] = (
        frame[numeric_columns].notna().all(axis=1)
        & np.isfinite(frame[numeric_columns]).all(axis=1)
        & frame["Volume"].ge(0)
        & frame["High"].ge(frame[["Open", "Close"]].max(axis=1))
        & frame["Low"].le(frame[["Open", "Close"]].min(axis=1))
    )
    elapsed = (frame["Date"] - frame["market_open"]).dt.total_seconds()
    frame["on_grid"] = elapsed.mod(300).eq(0)
    grouped = frame.groupby("session", sort=True)
    result = grouped.agg(
        Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"), Close=("Close", "last"), Volume=("Volume", "sum"),
        rth_row_count=("Date", "size"), unique_timestamp_count=("Date", "nunique"), valid_row_count=("row_valid", "sum"), on_grid_count=("on_grid", "sum"),
        market_open=("market_open", "first"), market_close=("market_close", "first"),
    )
    result["expected_row_count"] = ((result["market_close"] - result["market_open"]).dt.total_seconds() / 300).astype(int)
    invalid = (result["unique_timestamp_count"] != result["rth_row_count"]) | (result["valid_row_count"] != result["rth_row_count"]) | (result["on_grid_count"] != result["rth_row_count"])
    incomplete = result["rth_row_count"] != result["expected_row_count"]
    result["raw_session_status"] = np.where(invalid, "RAW_SESSION_INVALID", np.where(incomplete, "RAW_SESSION_PRESENT_BUT_INCOMPLETE", "COMPLETE_RAW_SESSION"))
    return result[columns]


def _daily_by_session(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.copy()
    if "Date" not in frame.columns and isinstance(frame.index, pd.DatetimeIndex):
        frame = frame.reset_index().rename(columns={frame.index.name or "index": "Date"})
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["Date"]).sort_values("Date").drop_duplicates("Date", keep="last")
    frame.index = frame["Date"].dt.tz_convert(MARKET_TIMEZONE).dt.tz_localize(None).dt.normalize()
    return frame[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")


def valid_daily_sessions(daily: pd.DataFrame) -> set[pd.Timestamp]:
    frame = daily.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["Date"])
    frame["session"] = frame["Date"].dt.tz_convert(MARKET_TIMEZONE).dt.tz_localize(None).dt.normalize()
    numeric = frame[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    valid = (
        numeric.notna().all(axis=1)
        & np.isfinite(numeric).all(axis=1)
        & numeric["Volume"].ge(0)
        & numeric["High"].ge(numeric[["Open", "Close"]].max(axis=1))
        & numeric["Low"].le(numeric[["Open", "Close"]].min(axis=1))
    )
    counts = frame.groupby("session")["Date"].size()
    valid_counts = frame.loc[valid].groupby("session")["Date"].size()
    return {pd.Timestamp(session) for session in counts.index if counts.loc[session] == 1 and valid_counts.get(session, 0) == 1}


def calibrate_symbol_equivalence(
    daily: pd.DataFrame,
    raw: pd.DataFrame,
    schedule: pd.DataFrame,
    *,
    min_compared_sessions: int = MIN_CALIBRATION_SESSIONS,
    raw_aggregates: pd.DataFrame | None = None,
) -> dict:
    raw_daily = _aggregate_raw_sessions(raw, schedule) if raw_aggregates is None else raw_aggregates
    valid_sessions = valid_daily_sessions(daily)
    parsed_dates = pd.to_datetime(daily.get("Date", pd.Series(dtype=str)), errors="coerce", utc=True).dropna()
    parsed_sessions = parsed_dates.dt.tz_convert(MARKET_TIMEZONE).dt.tz_localize(None).dt.normalize()
    invalid_daily_input = len(parsed_dates) != len(daily) or len(valid_sessions) != len(parsed_dates) or parsed_sessions.duplicated().any()
    daily_sessions = _daily_by_session(daily)
    daily_sessions = daily_sessions[daily_sessions.index.isin(valid_sessions)]
    comparison = daily_sessions.join(raw_daily, how="inner", lsuffix="_daily", rsuffix="_raw")
    comparison = comparison[comparison["raw_session_status"].eq("COMPLETE_RAW_SESSION")].copy()
    fields = ["Open", "High", "Low", "Close", "Volume"]
    metrics = {"compared_session_count": len(comparison), "exact_match_count": 0, "price_exact_match_count": 0, "pass_with_tolerance_count": 0}
    if comparison.empty:
        for field in fields:
            metrics[f"{field.lower()}_relative_error"] = np.nan
            metrics[f"{field.lower()}_p99_relative_error"] = np.nan
        metrics.update({"split_adjustment_discontinuity_count": 0, "equivalence_proven": False, "equivalence_reason": "INSUFFICIENT_COMPLETE_OVERLAPS"})
        return metrics
    tolerance_pass = pd.Series(True, index=comparison.index)
    hard_cap_pass = pd.Series(True, index=comparison.index)
    exact_fields = []
    for field in fields:
        daily_values = comparison[f"{field}_daily"].astype(float)
        raw_values = comparison[f"{field}_raw"].astype(float)
        relative_error = (raw_values - daily_values).abs() / daily_values.abs().replace(0, np.nan)
        metrics[f"{field.lower()}_relative_error"] = float(relative_error.max())
        metrics[f"{field.lower()}_p99_relative_error"] = float(relative_error.quantile(0.99))
        exact_fields.append(raw_values.eq(daily_values))
        if field == "Volume":
            tolerance_pass &= relative_error.le(VOLUME_RELATIVE_TOLERANCE).fillna(False)
            hard_cap_pass &= relative_error.le(VOLUME_HARD_CAP).fillna(False)
        else:
            absolute_error = (raw_values - daily_values).abs()
            tolerance_pass &= (absolute_error.le(PRICE_ABSOLUTE_TOLERANCE) | relative_error.le(PRICE_RELATIVE_TOLERANCE)).fillna(False)
            hard_cap_pass &= relative_error.le(PRICE_HARD_CAP).fillna(False)
    price_exact = pd.concat(exact_fields[:4], axis=1).all(axis=1)
    all_exact = pd.concat(exact_fields, axis=1).all(axis=1)
    metrics["price_exact_match_count"] = int(price_exact.sum())
    metrics["exact_match_count"] = int(all_exact.sum())
    metrics["pass_with_tolerance_count"] = int(tolerance_pass.sum())
    price_log_ratios = []
    for field in fields[:4]:
        ratio = comparison[f"{field}_daily"].astype(float) / comparison[f"{field}_raw"].astype(float).replace(0, np.nan)
        price_log_ratios.append(np.log(ratio.where(ratio > 0)))
    log_ratios = pd.concat(price_log_ratios, axis=1)
    median_scale = log_ratios.median(axis=1)
    coherent = log_ratios.max(axis=1).sub(log_ratios.min(axis=1)).abs().le(np.log1p(0.005))
    scale_regime = coherent & median_scale.abs().ge(np.log1p(SPLIT_SCALE_THRESHOLD))
    scale_change = median_scale.diff().abs().ge(np.log1p(SPLIT_SCALE_THRESHOLD))
    split_count = int((scale_regime | scale_change).sum())
    metrics["split_adjustment_discontinuity_count"] = split_count
    pass_rate = float(tolerance_pass.mean())
    p99_pass = all(metrics[f"{field.lower()}_p99_relative_error"] <= (VOLUME_RELATIVE_TOLERANCE if field == "Volume" else PRICE_RELATIVE_TOLERANCE) for field in fields)
    proven = not invalid_daily_input and len(comparison) >= min_compared_sessions and pass_rate >= MIN_TOLERANCE_PASS_RATE and bool(hard_cap_pass.all()) and p99_pass and split_count == 0
    metrics["equivalence_proven"] = bool(proven)
    if proven:
        reason = "PROVEN_WITH_CALIBRATED_TOLERANCE"
    elif invalid_daily_input:
        reason = "INVALID_DAILY_CALIBRATION_INPUT"
    elif len(comparison) < min_compared_sessions:
        reason = "INSUFFICIENT_COMPLETE_OVERLAPS"
    elif split_count:
        reason = "SPLIT_ADJUSTMENT_DISCONTINUITY"
    else:
        reason = "CALIBRATION_TOLERANCE_NOT_MET"
    metrics["equivalence_reason"] = reason
    return metrics


def build_in_memory_repaired_daily(daily: pd.DataFrame, repairs: dict[pd.Timestamp, dict]) -> pd.DataFrame:
    frame = daily.copy(deep=True)
    additions = [{"Date": pd.Timestamp(session).tz_localize(MARKET_TIMEZONE) + pd.Timedelta(hours=16), **bar} for session, bar in repairs.items()]
    if additions:
        frame = pd.concat([frame, pd.DataFrame(additions)], ignore_index=True)
    frame["Date"] = pd.to_datetime(frame["Date"], utc=True)
    return frame.sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)


def apply_required_session_mask(
    available_sessions: set[pd.Timestamp],
    expected_sessions: pd.DatetimeIndex,
    rebalance_dates,
    *,
    required_observation_count: int = 127,
) -> dict[pd.Timestamp, bool]:
    positions = {session: index for index, session in enumerate(expected_sessions)}
    output = {}
    for date in rebalance_dates:
        position = positions[pd.Timestamp(date)]
        required = set(expected_sessions[max(0, position - required_observation_count + 1): position + 1])
        output[pd.Timestamp(date)] = len(required) == required_observation_count and required.issubset(available_sessions)
    return output


def _prepare_asset_frame(daily: pd.DataFrame) -> pd.DataFrame:
    source = _daily_by_session(daily)
    source.index = pd.DatetimeIndex(source.index)
    frame = source.copy()
    frame.attrs["daily_ohlcv"] = source.copy()
    return frame


def compute_production_features(candidate_daily: dict[str, pd.DataFrame], params: dict) -> dict[str, pd.DataFrame]:
    normalized = _normalize_relative_strength_params(params)
    assets = {symbol: _prepare_asset_frame(candidate_daily[symbol]) for symbol in sorted(candidate_daily)}
    return _relative_strength_daily_features(assets, normalized)


def rank_candidate_features(
    features: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    candidate_symbols: set[str],
    *,
    top_n: int,
    required_masks: dict[str, dict[pd.Timestamp, bool]] | None = None,
) -> dict:
    if candidate_symbols & BLOCKED_ETFS:
        raise ValueError("V2_SELECTION_AUDIT_BLOCKED_ETF_IN_CANDIDATES")
    rows = []
    for symbol in sorted(candidate_symbols):
        frame = features.get(symbol)
        if frame is None or date not in frame.index:
            continue
        if required_masks is not None and not required_masks.get(symbol, {}).get(pd.Timestamp(date), False):
            continue
        row = frame.loc[date]
        if bool(row.get("RS_Eligible", False)) and pd.notna(row.get("RS_Score")):
            rows.append((symbol, float(row["RS_Score"])))
    rows.sort(key=lambda item: item[1], reverse=True)
    ranks = {symbol: index + 1 for index, (symbol, _) in enumerate(rows)}
    scores = dict(rows)
    return {"eligible": [symbol for symbol, _ in rows], "selected": [symbol for symbol, _ in rows[:top_n]], "ranks": ranks, "scores": scores}


def spearman_rank_correlation(ranks_a: dict[str, int], ranks_b: dict[str, int], common_symbols) -> float:
    """Spearman correlation for existing ordinal ranks without SciPy."""
    common = list(common_symbols)
    if len(common) < 2:
        return float("nan")
    values_a = np.asarray([ranks_a[symbol] for symbol in common], dtype=float)
    values_b = np.asarray([ranks_b[symbol] for symbol in common], dtype=float)
    if values_a.std() == 0 or values_b.std() == 0:
        return float("nan")
    return float(np.corrcoef(values_a, values_b)[0, 1])


def _strategy_params(baseline: dict) -> dict:
    strategy = baseline["strategy"]
    return {
        "rs_lookback_days": strategy["momentum_lookback_days"],
        "rs_top_n": strategy["top_n"],
        "rs_rebalance_frequency": strategy["rebalance_frequency"],
        "rs_score_mode": strategy["score_mode"],
        "rs_vol_lookback_days": strategy["volatility_lookback_days"],
        "rs_trend_sma_days": strategy["sma_filter_days"],
        "rs_use_market_regime": strategy["use_market_regime"],
        "rs_market_regime_sma_days": strategy["market_regime_sma_days"],
        "rs_min_price": baseline["universe"]["min_price"],
        "rs_max_atr_pct": strategy["max_atr_pct"],
    }


def audit_v2_daily_gap_selection(
    dataset_root: Path,
    run_id: str,
    report_dir: Path,
    *,
    market_schedule: pd.DataFrame | None = None,
    offline_telemetry: dict | None = None,
) -> dict:
    root = Path(dataset_root)
    manifest_path = root / "manifests" / run_id / "acquisition_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline_path = Path(__file__).resolve().parent / "baselines" / "archive" / "rs_m126_top30_sma100_no_regime_v2_clean_stock.178symbols.20260718.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    start, end = manifest["target_start_date"], manifest["target_end_date"]
    schedule = _normalize_schedule(approved_nyse_schedule(start, end) if market_schedule is None else market_schedule, start, end)
    expected_sessions = pd.DatetimeIndex(schedule.index)
    expected_set = set(expected_sessions)
    records = manifest["files"]
    daily_records = {row["symbol"]: row for row in records if row.get("timeframe") == "1D"}
    intraday_records = {row["symbol"]: row for row in records if row.get("timeframe") == "130min"}
    candidates = validate_audit_contract(manifest, baseline, run_id)

    daily_frames = {}
    repaired_frames = {}
    available_sets = {}
    repaired_sets = {}
    equivalence_rows = []
    repair_rows = []
    repairs_by_symbol = {}
    for symbol in sorted(daily_records):
        daily = pd.read_csv(root / daily_records[symbol]["output_relative_path"])
        raw = pd.read_csv(root / intraday_records[symbol]["source_raw_5min_relative_path"])
        daily_frames[symbol] = daily
        daily_sessions = _daily_by_session(daily)
        actual = valid_daily_sessions(daily) & expected_set
        available_sets[symbol] = actual
        first, last = (min(actual), max(actual)) if actual else (None, None)
        raw_aggregates = _aggregate_raw_sessions(raw, schedule)
        calibration = calibrate_symbol_equivalence(daily, raw, schedule, raw_aggregates=raw_aggregates)
        equivalence_rows.append({"symbol": symbol, "asset_role": daily_records[symbol].get("asset_role", ""), **calibration})
        repairs = {}
        for session in sorted(expected_set - actual):
            if first is None or session < first or session > last:
                classification = "BOUNDARY_NOT_APPLICABLE"
                raw_status = "NOT_INSPECTED_BOUNDARY"
            elif session not in raw_aggregates.index:
                classification = "RAW_SESSION_ABSENT"
                raw_status = "RAW_SESSION_ABSENT"
            else:
                raw_status = str(raw_aggregates.loc[session, "raw_session_status"])
                if raw_status == "RAW_SESSION_INVALID":
                    classification = "RAW_SESSION_INVALID"
                elif raw_status == "RAW_SESSION_PRESENT_BUT_INCOMPLETE":
                    classification = "RAW_SESSION_PRESENT_BUT_INCOMPLETE"
                elif not calibration["equivalence_proven"]:
                    classification = "ADJUSTMENT_EQUIVALENCE_NOT_PROVEN"
                else:
                    classification = "REPAIRABLE_FROM_COMPLETE_RAW_SESSION"
                    repairs[session] = {field: float(raw_aggregates.loc[session, field]) for field in ["Open", "High", "Low", "Close", "Volume"]}
            repair_rows.append({
                "symbol": symbol, "asset_role": daily_records[symbol].get("asset_role", ""), "market_session": str(session.date()),
                "classification": classification, "raw_session_status": raw_status,
                "equivalence_proven": bool(calibration["equivalence_proven"]), "boundary_gap": classification == "BOUNDARY_NOT_APPLICABLE",
                "qqq_impact": symbol == "QQQ", "spy_impact": symbol == "SPY",
            })
        repairs_by_symbol[symbol] = repairs
        repaired_frames[symbol] = build_in_memory_repaired_daily(daily, repairs)
        repaired_sets[symbol] = actual | set(repairs)

    repairability = pd.DataFrame(repair_rows)
    equivalence = pd.DataFrame(equivalence_rows)
    params = _strategy_params(baseline)
    candidate_daily_a = {symbol: daily_frames[symbol] for symbol in sorted(candidates)}
    candidate_daily_c = {symbol: repaired_frames[symbol] for symbol in sorted(candidates)}
    features_a = compute_production_features(candidate_daily_a, params)
    features_c = compute_production_features(candidate_daily_c, params)
    all_dates = pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in features_a.values()))))
    rebalance_mask = _rs_rebalance_mask(all_dates, params["rs_rebalance_frequency"])
    rebalance_dates = [date for date in all_dates if bool(rebalance_mask.loc[date])]
    masks_b = {symbol: apply_required_session_mask(available_sets[symbol], expected_sessions, rebalance_dates) for symbol in candidates}
    masks_c = {symbol: apply_required_session_mask(repaired_sets[symbol], expected_sessions, rebalance_dates) for symbol in candidates}

    impact_rows = []
    membership_rows = []
    added_b = Counter()
    removed_b = Counter()
    restored_c = Counter()
    for date in rebalance_dates:
        scenario_a = rank_candidate_features(features_a, date, candidates, top_n=params["rs_top_n"])
        scenario_b = rank_candidate_features(features_a, date, candidates, top_n=params["rs_top_n"], required_masks=masks_b)
        scenario_c = rank_candidate_features(features_c, date, candidates, top_n=params["rs_top_n"], required_masks=masks_c)
        selected_a, selected_b, selected_c = map(set, (scenario_a["selected"], scenario_b["selected"], scenario_c["selected"]))
        additions_b, removals_b = selected_b - selected_a, selected_a - selected_b
        additions_c, removals_c = selected_c - selected_b, selected_b - selected_c
        added_b.update(additions_b)
        removed_b.update(removals_b)
        restored_c.update(additions_c)
        common = sorted(set(scenario_a["ranks"]) & set(scenario_b["ranks"]))
        rank_correlation = spearman_rank_correlation(scenario_a["ranks"], scenario_b["ranks"], common)
        max_rank_change = max((abs(scenario_a["ranks"][symbol] - scenario_b["ranks"][symbol]) for symbol in common), default=0)
        ranked_symbols = set(scenario_a["eligible"]) | set(scenario_b["eligible"]) | set(scenario_c["eligible"])
        impact_rows.append({
            "rebalance_date": str(date.date()),
            "scenario_a_eligible_count": len(scenario_a["eligible"]), "scenario_b_eligible_count": len(scenario_b["eligible"]), "scenario_c_eligible_count": len(scenario_c["eligible"]),
            "scenario_a_top30": "|".join(scenario_a["selected"]), "scenario_b_top30": "|".join(scenario_b["selected"]), "scenario_c_top30": "|".join(scenario_c["selected"]),
            "a_b_top30_overlap_count": len(selected_a & selected_b), "a_b_top30_additions": "|".join(sorted(additions_b)), "a_b_top30_removals": "|".join(sorted(removals_b)),
            "b_c_top30_overlap_count": len(selected_b & selected_c), "b_c_top30_additions": "|".join(sorted(additions_c)), "b_c_top30_removals": "|".join(sorted(removals_c)),
            "a_b_rank_correlation": rank_correlation, "a_b_max_absolute_rank_change": max_rank_change,
            "a_b_selection_status_change_count": len(selected_a ^ selected_b), "b_c_selection_status_change_count": len(selected_b ^ selected_c),
            "qqq_or_spy_entered_candidate_ranking": bool(ranked_symbols & {"QQQ", "SPY"}),
            "excluded_etf_entered_candidate_ranking": bool(ranked_symbols & BLOCKED_ETFS),
        })
        for symbol in sorted(candidates):
            membership_rows.append({
                "rebalance_date": str(date.date()), "symbol": symbol,
                "scenario_a_rank": scenario_a["ranks"].get(symbol, ""), "scenario_b_rank": scenario_b["ranks"].get(symbol, ""), "scenario_c_rank": scenario_c["ranks"].get(symbol, ""),
                "scenario_a_selected": symbol in selected_a, "scenario_b_selected": symbol in selected_b, "scenario_c_selected": symbol in selected_c,
                "removed_by_fail_closed": symbol in removals_b, "added_by_fail_closed": symbol in additions_b, "restored_by_in_memory_repair": symbol in additions_c,
            })
    impact = pd.DataFrame(impact_rows)
    membership = pd.DataFrame(membership_rows)

    report_dir = Path(report_dir)
    paths = {
        "repairability": report_dir / "rs_v2_clean_daily_gap_repairability.csv",
        "equivalence": report_dir / "rs_v2_clean_raw_daily_equivalence.csv",
        "repair_summary": report_dir / "rs_v2_clean_daily_gap_repairability_summary.md",
        "selection_impact": report_dir / "rs_v2_clean_selection_impact_by_rebalance.csv",
        "membership": report_dir / "rs_v2_clean_selection_membership_detail.csv",
        "selection_summary": report_dir / "rs_v2_clean_selection_impact_summary.md",
    }
    _atomic_frame(repairability, paths["repairability"])
    _atomic_frame(equivalence, paths["equivalence"])
    _atomic_frame(impact, paths["selection_impact"])
    _atomic_frame(membership, paths["membership"])
    classification_counts = repairability["classification"].value_counts().to_dict()
    internal = repairability[~repairability["classification"].eq("BOUNDARY_NOT_APPLICABLE")]
    candidate_repairability = repairability[repairability["asset_role"].eq("CANDIDATE")]
    candidate_classification_counts = candidate_repairability["classification"].value_counts().to_dict()
    internal_symbols = sorted(internal["symbol"].unique())
    internal_sessions = sorted(internal["market_session"].unique())
    network_count = offline_telemetry.get("network_attempt_count") if offline_telemetry is not None else "UNAVAILABLE_NOT_GUESSED"
    client_count = offline_telemetry.get("client_attempt_count") if offline_telemetry is not None else "UNAVAILABLE_NOT_GUESSED"
    exception_count = len(load_reviewed_coverage_exceptions(Path(__file__).resolve().parent / "baselines" / "rs_v2_clean_coverage_exceptions.json"))
    repair_lines = [
        "# V2 Daily Gap Repairability Summary", "",
        f"- missing_daily_session_count: {len(repairability)}",
        f"- candidate_missing_daily_session_count: {int(repairability.asset_role.eq('CANDIDATE').sum())}",
        f"- candidate_repairable_count: {candidate_classification_counts.get('REPAIRABLE_FROM_COMPLETE_RAW_SESSION', 0)}",
        f"- candidate_raw_present_incomplete_count: {candidate_classification_counts.get('RAW_SESSION_PRESENT_BUT_INCOMPLETE', 0)}",
        f"- candidate_raw_absent_count: {candidate_classification_counts.get('RAW_SESSION_ABSENT', 0)}",
        f"- candidate_raw_invalid_count: {candidate_classification_counts.get('RAW_SESSION_INVALID', 0)}",
        f"- candidate_adjustment_equivalence_unproven_count: {candidate_classification_counts.get('ADJUSTMENT_EQUIVALENCE_NOT_PROVEN', 0)}",
        f"- candidate_boundary_not_applicable_count: {candidate_classification_counts.get('BOUNDARY_NOT_APPLICABLE', 0)}",
        f"- repairable_count: {classification_counts.get('REPAIRABLE_FROM_COMPLETE_RAW_SESSION', 0)}",
        f"- unrecoverable_internal_count: {len(internal) - classification_counts.get('REPAIRABLE_FROM_COMPLETE_RAW_SESSION', 0)}",
        f"- raw_present_incomplete_count: {classification_counts.get('RAW_SESSION_PRESENT_BUT_INCOMPLETE', 0)}",
        f"- raw_absent_count: {classification_counts.get('RAW_SESSION_ABSENT', 0)}",
        f"- raw_invalid_count: {classification_counts.get('RAW_SESSION_INVALID', 0)}",
        f"- adjustment_equivalence_unproven_count: {classification_counts.get('ADJUSTMENT_EQUIVALENCE_NOT_PROVEN', 0)}",
        f"- boundary_not_applicable_count: {classification_counts.get('BOUNDARY_NOT_APPLICABLE', 0)}",
        f"- internal_affected_symbol_count: {len(internal_symbols)}",
        f"- internal_affected_symbols: {','.join(internal_symbols)}",
        f"- internal_affected_market_sessions: {','.join(internal_sessions)}",
        f"- symbols_with_equivalence_proven: {int(equivalence.equivalence_proven.sum())}",
        f"- qqq_missing_gap_count: {int(repairability.symbol.eq('QQQ').sum())}",
        f"- spy_missing_gap_count: {int(repairability.symbol.eq('SPY').sum())}",
        f"- approved_exception_count: {exception_count}", f"- network_request_count: {network_count}", f"- client_attempt_count: {client_count}",
    ]
    _atomic_text("\n".join(repair_lines) + "\n", paths["repair_summary"])

    changes = impact["a_b_selection_status_change_count"]
    summary = {
        "rebalance_date_count": len(impact),
        "affected_rebalance_date_count": int(changes.gt(0).sum()),
        "identical_top30_date_count": int(changes.eq(0).sum()),
        "one_to_three_change_date_count": int(changes.between(1, 3).sum()),
        "four_to_ten_change_date_count": int(changes.between(4, 10).sum()),
        "more_than_ten_change_date_count": int(changes.gt(10).sum()),
        "minimum_eligible_candidate_count": int(impact["scenario_b_eligible_count"].min()),
        "median_eligible_candidate_count": float(impact["scenario_b_eligible_count"].median()),
        "maximum_eligible_candidate_count": int(impact["scenario_b_eligible_count"].max()),
        "total_membership_status_changes": int(changes.sum()),
        "scenario_b_c_identical_date_count": int(impact["b_c_selection_status_change_count"].eq(0).sum()),
    }
    largest_distortion = impact.sort_values(["a_b_max_absolute_rank_change", "rebalance_date"], ascending=[False, True]).head(10)
    selection_lines = ["# V2 Selection Impact Summary", ""]
    selection_lines.extend(f"- {key}: {value}" for key, value in summary.items())
    qqq_spy_entered = bool(impact["qqq_or_spy_entered_candidate_ranking"].any())
    excluded_etf_entered = bool(impact["excluded_etf_entered_candidate_ranking"].any())
    if qqq_spy_entered or excluded_etf_entered:
        raise ValueError("V2_SELECTION_AUDIT_BLOCKED_ETF_ENTERED_RANKING")
    selection_lines.extend([
        f"- most_frequently_added_by_fail_closed: {json.dumps(added_b.most_common(20))}",
        f"- most_frequently_removed_by_fail_closed: {json.dumps(removed_b.most_common(20))}",
        f"- most_frequently_restored_by_in_memory_repair: {json.dumps(restored_c.most_common(20))}",
        f"- dates_with_largest_rank_distortion: {json.dumps([[row.rebalance_date, int(row.a_b_max_absolute_rank_change)] for row in largest_distortion.itertuples()])}",
        f"- qqq_or_spy_entered_candidate_ranking: {str(qqq_spy_entered).lower()}",
        f"- excluded_etf_entered_candidate_ranking: {str(excluded_etf_entered).lower()}",
        "- performance_metrics_calculated: false", f"- network_request_count: {network_count}", f"- client_attempt_count: {client_count}",
    ])
    _atomic_text("\n".join(selection_lines) + "\n", paths["selection_summary"])
    return {"repairability": repairability, "equivalence": equivalence, "selection_impact": impact, "membership": membership, "repair_counts": classification_counts, "selection_summary": summary, "paths": paths}
