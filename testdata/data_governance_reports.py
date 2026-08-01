"""Data governance reports: symbol data-status checks, data precheck/policy decisions,
regression-guard, pipeline health log, run manifest, and report-schema/paper-state
validation.

Extracted from testdata/runner.py (Phase 17, Cluster 6) to shrink that file. Behavior-
preserving move -- function bodies are unchanged, only their file and imports moved. All 24
functions here are called from exactly one place, run_strategy_experiments (which stays in
runner.py), which is research/validation CLI tooling, not part of the live nightly DSS chain
(confirmed: none of this cluster's write_* functions are referenced anywhere in
dss_scheduler.py, universe_scanner.py, or bot.py).

Two runner.py functions that were not part of this extraction are called here --
_data_policy_decision_columns (a tiny accessor returning DATA_POLICY_DECISION_COLUMNS) and
_latest_csv_data_date -- each given a lazy-import shim, same pattern as Cluster 4's, so every
moved function body stays byte-for-byte identical to its original. _readable_table is not a
runner.py function (it lives in chatgpt_master_report.py, a sibling module already imported at
runner.py's own top level) and is imported directly from there -- no circularity risk, since
chatgpt_master_report.py does not import this module.

Conversely, runner.py imports all 24 names back at its own top level: several tests import
_pipeline_health_columns / _write_run_manifest / _write_data_precheck_report /
_write_regression_guard_report directly from testdata.runner, and chatgpt_master_report.py
already does its own lazy `from .runner import _evaluate_data_policy_status` -- unaffected,
since runner.py's re-export keeps that name resolvable regardless of which file defines it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

try:
    from .report_schemas import (
        ALPACA_ORDER_PREVIEW_COLUMNS,
        DATA_POLICY_DECISION_COLUMNS,
        DATA_POLICY_REVIEW_EVIDENCE_COLUMNS,
        DATA_PRECHECK_COLUMNS,
        PAPER_ORDER_REVIEW_COLUMNS,
        PAPER_PENDING_ORDERS_COLUMNS,
        PAPER_SIGNAL_COLUMNS,
        PAPER_TRADE_LOG_COLUMNS,
        PIPELINE_HEALTH_COLUMNS,
        ensure_columns,
    )
    from .chatgpt_master_report import _readable_table
except ImportError:
    from report_schemas import (
        ALPACA_ORDER_PREVIEW_COLUMNS,
        DATA_POLICY_DECISION_COLUMNS,
        DATA_POLICY_REVIEW_EVIDENCE_COLUMNS,
        DATA_PRECHECK_COLUMNS,
        PAPER_ORDER_REVIEW_COLUMNS,
        PAPER_PENDING_ORDERS_COLUMNS,
        PAPER_SIGNAL_COLUMNS,
        PAPER_TRADE_LOG_COLUMNS,
        PIPELINE_HEALTH_COLUMNS,
        ensure_columns,
    )
    from chatgpt_master_report import _readable_table


def _data_policy_decision_columns(*args, **kwargs):
    try:
        from .runner import _data_policy_decision_columns as _runner_data_policy_decision_columns
    except ImportError:
        from runner import _data_policy_decision_columns as _runner_data_policy_decision_columns
    return _runner_data_policy_decision_columns(*args, **kwargs)


def _latest_csv_data_date(*args, **kwargs):
    try:
        from .runner import _latest_csv_data_date as _runner_latest_csv_data_date
    except ImportError:
        from runner import _latest_csv_data_date as _runner_latest_csv_data_date
    return _runner_latest_csv_data_date(*args, **kwargs)


def _symbol_data_status(symbol, daily_dir, intraday_dir):
    daily_path = Path(daily_dir) / f"{symbol}_1D.csv"
    intraday_path = Path(intraday_dir) / f"{symbol}_130min.csv"

    def inspect(path):
        if not path.exists():
            return {"exists": False, "empty": True, "latest_date": "none", "zero_volume_rows": 0}
        try:
            frame = pd.read_csv(path)
        except Exception:
            return {"exists": True, "empty": True, "latest_date": "none", "zero_volume_rows": 0}
        if frame.empty:
            return {"exists": True, "empty": True, "latest_date": "none", "zero_volume_rows": 0}
        date_column = next(
            (column for column in frame.columns if "date" in str(column).lower() or "time" in str(column).lower()),
            frame.columns[0],
        )
        dates = pd.to_datetime(frame[date_column], errors="coerce", utc=True)
        latest = dates.max()
        volume = pd.to_numeric(frame.get("Volume", pd.Series(dtype=float)), errors="coerce")
        return {
            "exists": True,
            "empty": False,
            "latest_date": str(latest.date()) if pd.notna(latest) else "none",
            "zero_volume_rows": int(volume.fillna(0).le(0).sum()) if not volume.empty else 0,
        }

    return {"daily": inspect(daily_path), "intraday": inspect(intraday_path)}


def _data_precheck_actions(rows, latest_daily, latest_130min):
    actions = []
    for row in rows:
        symbol = str(row.get("symbol", "")).strip().upper()
        warnings = {
            warning
            for warning in str(row.get("warnings", "")).split(";")
            if warning
        }
        if "dow_empty_daily" in warnings:
            actions.append(
                {
                    "symbol": symbol,
                    "warning": "dow_empty_daily",
                    "action": "refresh_or_remove_empty_daily_file_before_paper_submit",
                    "detail": f"{symbol}_1D.csv is empty; rerun downloader for this symbol or exclude it after review.",
                }
            )
        if "early_daily_end" in warnings:
            actions.append(
                {
                    "symbol": symbol,
                    "warning": "early_daily_end",
                    "action": "refresh_stale_symbol_history_before_paper_submit",
                    "detail": f"{symbol}_1D.csv ends before latest_daily_date={latest_daily}.",
                }
            )
        if "psa_zero_volume" in warnings:
            actions.append(
                {
                    "symbol": symbol,
                    "warning": "psa_zero_volume",
                    "action": "inspect_zero_volume_rows_before_paper_submit",
                    "detail": f"{symbol}_1D.csv has zero-volume rows; verify vendor data or repair source.",
                }
            )
        if "candidate_missing_or_empty_data" in warnings:
            actions.append(
                {
                    "symbol": symbol,
                    "warning": "candidate_missing_or_empty_data",
                    "action": "block_candidate_until_daily_and_130min_data_exist",
                    "detail": f"{symbol} is a paper candidate with missing or empty daily/130min data.",
                }
            )
    if latest_daily == "none" or latest_130min == "none" or latest_daily != latest_130min:
        actions.append(
            {
                "symbol": "ALL",
                "warning": "latest_daily_130min_date_mismatch",
                "action": "refresh_daily_and_130min_data_to_same_latest_session",
                "detail": f"latest_daily_date={latest_daily}, latest_130min_date={latest_130min}.",
            }
        )
    return actions


def _data_precheck_policy_decisions(actions):
    decisions = []
    for action in actions:
        warning = str(action.get("warning", "")).strip()
        symbol = str(action.get("symbol", "")).strip().upper()
        if warning == "early_daily_end":
            policy = "BLOCK_UNTIL_POLICY_ACK"
            recommendation = "confirm_delisted_renamed_or_remove_from_trading_universe"
        elif warning == "psa_zero_volume":
            policy = "BLOCK_UNTIL_POLICY_ACK"
            recommendation = "inspect_vendor_row_then_allow_or_repair_source"
        elif warning in {"dow_empty_daily", "candidate_missing_or_empty_data", "latest_daily_130min_date_mismatch"}:
            policy = "BLOCK_UNTIL_DATA_FIXED"
            recommendation = action.get("action", "refresh_data")
        else:
            policy = "REVIEW_REQUIRED"
            recommendation = action.get("action", "review")
        decisions.append(
            {
                "symbol": symbol,
                "warning": warning,
                "policy_status": policy,
                "recommended_policy_action": recommendation,
            }
        )
    return decisions


def _data_policy_key(row):
    return (
        str(row.get("symbol", "")).strip().upper(),
        str(row.get("warning", "")).strip(),
    )


def _write_data_policy_decision_file(policy_decisions, path):
    path = Path(path)
    existing = pd.DataFrame()
    if path.exists():
        try:
            existing = pd.read_csv(path)
        except Exception:
            existing = pd.DataFrame()
    existing_operator = {}
    existing_notes = {}
    for _, row in existing.iterrows():
        key = _data_policy_key(row)
        existing_operator[key] = str(row.get("operator_decision", "REVIEW_REQUIRED")).strip().upper() or "REVIEW_REQUIRED"
        existing_notes[key] = row.get("operator_note", "")
    rows = []
    for decision in policy_decisions:
        key = _data_policy_key(decision)
        output = dict(decision)
        output["operator_decision"] = existing_operator.get(key, "REVIEW_REQUIRED")
        output["operator_note"] = existing_notes.get(key, "")
        rows.append(output)
    frame = ensure_columns(pd.DataFrame(rows), _data_policy_decision_columns())
    frame.to_csv(path, index=False)
    return frame


def _write_data_precheck_report(paper_report, daily_dir, intraday_dir, status, path):
    warnings = []
    critical = []
    rows = []
    latest_daily = status.get("latest_daily_data_date", "none") if status else _latest_csv_data_date(daily_dir)
    latest_130min = status.get("latest_130min_data_date", "none") if status else _latest_csv_data_date(intraday_dir)

    special_symbols = ["DOW", "BK", "PARA", "PSA", "QQQ", "SPY"]
    candidate_symbols = []
    if paper_report is not None and not paper_report.empty and "symbol" in paper_report:
        candidate_symbols = sorted(
            paper_report.loc[
                paper_report.get("paper_actionable", pd.Series(False, index=paper_report.index)).astype(bool),
                "symbol",
            ].dropna().astype(str).str.upper().unique().tolist()
        )
    for symbol in sorted(set(special_symbols + candidate_symbols)):
        info = _symbol_data_status(symbol, daily_dir, intraday_dir)
        daily = info["daily"]
        intraday = info["intraday"]
        row_warnings = []
        if symbol == "DOW" and (not daily["exists"] or daily["empty"]):
            row_warnings.append("dow_empty_daily")
        if symbol in {"BK", "PARA"} and daily["latest_date"] not in {"none", latest_daily}:
            row_warnings.append("early_daily_end")
        if symbol == "PSA" and daily["zero_volume_rows"] > 0:
            row_warnings.append("psa_zero_volume")
        if symbol in {"QQQ", "SPY"}:
            if not daily["exists"] or daily["empty"]:
                critical.append(f"{symbol}_daily_missing_or_empty")
            if not intraday["exists"] or intraday["empty"]:
                critical.append(f"{symbol}_130min_missing_or_empty")
        if symbol in candidate_symbols and (not daily["exists"] or daily["empty"] or not intraday["exists"] or intraday["empty"]):
            row_warnings.append("candidate_missing_or_empty_data")
        warnings.extend(row_warnings)
        rows.append(
            {
                "symbol": symbol,
                "daily_exists": daily["exists"],
                "daily_empty": daily["empty"],
                "daily_latest_date": daily["latest_date"],
                "intraday_exists": intraday["exists"],
                "intraday_empty": intraday["empty"],
                "intraday_latest_date": intraday["latest_date"],
                "zero_volume_rows": daily["zero_volume_rows"],
                "warnings": ";".join(row_warnings),
            }
        )
    if latest_daily == "none" or latest_130min == "none" or latest_daily != latest_130min:
        warnings.append("latest_daily_130min_date_mismatch")
    actions = _data_precheck_actions(rows, latest_daily, latest_130min)
    policy_decisions = _data_precheck_policy_decisions(actions)
    policy_decision_path = Path(path).with_name("data_policy_decisions.csv")
    policy_decision_frame = _write_data_policy_decision_file(policy_decisions, policy_decision_path)
    policy_decision_display_path = (
        "reports/latest/data_policy_decisions.csv"
        if policy_decision_path.parent.name == "latest"
        else str(policy_decision_path)
    )
    result = {
        "status": "CRITICAL" if critical else ("WARNING" if warnings else "OK"),
        "warnings": sorted(set(warnings)),
        "critical": sorted(set(critical)),
        "recommended_actions": actions,
        "policy_decisions": policy_decision_frame.to_dict("records"),
    }
    lines = [
        "# Data Precheck Report",
        "",
        f"- status: {result['status']}",
        f"- latest_daily_date: {latest_daily}",
        f"- latest_130min_date: {latest_130min}",
        f"- critical_warnings: {', '.join(result['critical']) if result['critical'] else 'none'}",
        f"- warnings: {', '.join(result['warnings']) if result['warnings'] else 'none'}",
        f"- recommended_action_count: {len(actions)}",
        f"- policy_decision_count: {len(policy_decision_frame)}",
        f"- data_policy_decisions_path: {policy_decision_display_path}",
        "",
        "## Symbol Checks",
        "",
        _readable_table(ensure_columns(pd.DataFrame(rows), DATA_PRECHECK_COLUMNS)),
        "",
        "## Recommended Actions",
        "",
        _readable_table(pd.DataFrame(actions)) if actions else "none",
        "",
        "## Data Policy Decisions",
        "",
        _readable_table(policy_decision_frame) if not policy_decision_frame.empty else "none",
        "",
        "## Operator Decision Guide",
        "",
        "- Edit operator_decision in data_policy_decisions.csv only after checking the source data issue.",
        "- Resolving values require a non-empty operator_note: ACK, ACKNOWLEDGED, ALLOW, ALLOWED, REPAIRED, REPAIR_DONE, REMOVED, REMOVE_FROM_UNIVERSE.",
        "- Blocking values: blank, REVIEW_REQUIRED, REJECT, REPAIR_REQUIRED, or any unrecognized value.",
        "- operator_note should include the evidence or repair action used for the decision.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return result


REGRESSION_BASELINE_REFRESH_COMMAND = "python testdata\\run_baseline.py"
REGRESSION_BASELINE_UPDATE_REPORT = "regression_baseline_update_report.md"


def _clean_markdown_cell(value):
    return str(value).replace("**", "").strip()


def _is_date_only(value):
    text = str(value).strip()
    return len(text) == 10 and text[4] == "-" and text[7] == "-"


def _read_regression_benchmark_baseline(latest_dir, actual):
    baseline = {
        "qqq_return": pd.NA,
        "equal_weight_return": pd.NA,
        "qqq_start_date": actual.get("qqq_start_date", ""),
        "qqq_end_date": "",
        "equal_weight_start_date": actual.get("equal_weight_start_date", ""),
        "equal_weight_end_date": "",
        "source": str(Path(latest_dir) / REGRESSION_BASELINE_UPDATE_REPORT),
        "refresh_command": REGRESSION_BASELINE_REFRESH_COMMAND,
    }
    path = Path(latest_dir) / REGRESSION_BASELINE_UPDATE_REPORT
    if not path.exists():
        return baseline
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return baseline
    values = {}
    for line in lines:
        if not line.strip().startswith("|") or "---" in line:
            continue
        cells = [_clean_markdown_cell(cell) for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0].lower() == "parameter":
            continue
        values[cells[0]] = cells[2]
    baseline_end = values.get("Baseline End Date", "")
    baseline["qqq_end_date"] = baseline_end
    baseline["equal_weight_end_date"] = baseline_end
    baseline["qqq_return"] = pd.to_numeric(
        pd.Series([values.get("QQQ Expected Return (%)", pd.NA)]),
        errors="coerce",
    ).iloc[0]
    baseline["equal_weight_return"] = pd.to_numeric(
        pd.Series([values.get("EqualWeight Expected Return (%)", pd.NA)]),
        errors="coerce",
    ).iloc[0]
    return baseline


def _same_timestamp(left, right):
    if _is_date_only(left) or _is_date_only(right):
        left_date = pd.to_datetime(left, errors="coerce")
        right_date = pd.to_datetime(right, errors="coerce")
        if pd.isna(left_date) or pd.isna(right_date):
            return str(left).strip() == str(right).strip()
        return bool(left_date.date() == right_date.date())
    left_ts = pd.to_datetime(left, errors="coerce", utc=True)
    right_ts = pd.to_datetime(right, errors="coerce", utc=True)
    if pd.isna(left_ts) or pd.isna(right_ts):
        return str(left).strip() == str(right).strip()
    return bool(left_ts == right_ts)


def _timestamp_after(left, right):
    if _is_date_only(left) or _is_date_only(right):
        left_date = pd.to_datetime(left, errors="coerce")
        right_date = pd.to_datetime(right, errors="coerce")
        if pd.isna(left_date) or pd.isna(right_date):
            return False
        return bool(left_date.date() > right_date.date())
    left_ts = pd.to_datetime(left, errors="coerce", utc=True)
    right_ts = pd.to_datetime(right, errors="coerce", utc=True)
    if pd.isna(left_ts) or pd.isna(right_ts):
        return False
    return bool(left_ts > right_ts)


def _benchmark_baseline_stale(actual, baseline):
    return (
        not _same_timestamp(actual.get("qqq_end_date"), baseline.get("qqq_end_date"))
        and not _same_timestamp(
            actual.get("equal_weight_end_date"),
            baseline.get("equal_weight_end_date"),
        )
        and _timestamp_after(actual.get("qqq_end_date"), baseline.get("qqq_end_date"))
        and _timestamp_after(
            actual.get("equal_weight_end_date"),
            baseline.get("equal_weight_end_date"),
        )
    )


def _benchmark_return_warnings(qqq_return, ew_return, actual, baseline):
    critical = []
    warnings = []
    qqq_expected = baseline.get("qqq_return", pd.NA)
    ew_expected = baseline.get("equal_weight_return", pd.NA)
    if pd.isna(qqq_expected) or pd.isna(ew_expected) or not baseline.get("qqq_end_date"):
        return critical, ["regression_benchmark_baseline_metadata_missing"], "baseline metadata missing; manual baseline refresh required"
    qqq_changed = pd.isna(qqq_return) or abs(float(qqq_return) - qqq_expected) > 0.05
    ew_changed = pd.isna(ew_return) or abs(float(ew_return) - ew_expected) > 0.05
    if not qqq_changed and not ew_changed:
        return critical, warnings, "same benchmark scope"
    if _benchmark_baseline_stale(actual, baseline):
        reason = "benchmark baseline end date is older than actual benchmark end date; manual baseline refresh required"
        if qqq_changed:
            warnings.append(f"qqq_return_baseline_stale_expected_{qqq_expected}_actual_{qqq_return}")
        if ew_changed:
            warnings.append(f"equal_weight_return_baseline_stale_expected_{ew_expected}_actual_{ew_return}")
        return critical, warnings, reason
    reason = "same baseline/date scope or missing date scope; return mismatch is critical"
    if qqq_changed:
        critical.append(f"qqq_return_expected_{qqq_expected}_actual_{qqq_return}")
    if ew_changed:
        critical.append(f"equal_weight_return_expected_{ew_expected}_actual_{ew_return}")
    return critical, warnings, reason


def _write_regression_guard_report(report, decision, latest_dir, path):
    warnings = []
    critical = []
    first = report.iloc[0] if report is not None and not report.empty else pd.Series(dtype=object)
    qqq_return = pd.to_numeric(pd.Series([first.get("QQQ BuyHold Return (%)", pd.NA)]), errors="coerce").iloc[0]
    ew_return = pd.to_numeric(pd.Series([first.get("EqualWeight Universe Return (%)", pd.NA)]), errors="coerce").iloc[0]
    actual_benchmark = {
        "qqq_start_date": first.get("QQQ Benchmark Start", ""),
        "qqq_end_date": first.get("QQQ Benchmark End", ""),
        "equal_weight_start_date": first.get("EqualWeight Benchmark Start", ""),
        "equal_weight_end_date": first.get("EqualWeight Benchmark End", ""),
    }
    baseline = _read_regression_benchmark_baseline(latest_dir, actual_benchmark)
    benchmark_critical, benchmark_warnings, benchmark_scope_reason = _benchmark_return_warnings(
        qqq_return,
        ew_return,
        actual_benchmark,
        baseline,
    )
    critical.extend(benchmark_critical)
    warnings.extend(benchmark_warnings)
    main_role_ok = False
    if decision is not None and not decision.empty:
        main_rows = decision[
            decision["experiment_name"].eq("rs_m126_top30_sma100_no_regime")
            & decision["final_role"].astype(str).str.lower().eq("main")
        ]
        main_role_ok = not main_rows.empty
    if not main_role_ok:
        critical.append("main_candidate_role_changed")
    pending_readable = False
    pending_rows = 0
    try:
        pending = pd.read_csv(Path(latest_dir) / "paper_pending_orders.csv")
        pending_readable = True
        pending_rows = len(pending)
    except Exception:
        critical.append("paper_pending_orders_unreadable")
    status = "CRITICAL" if critical else ("BASELINE_STALE" if warnings else "OK")
    result = {
        "status": status,
        "critical": critical,
        "warnings": warnings,
        "strategy_metrics_changed": False,
        "benchmark_changed": False,
        "paper_pending_readable": pending_readable,
    }
    lines = [
        "# Regression Guard Report",
        "",
        f"- status: {result['status']}",
        f"- expected QQQ benchmark range: {baseline['qqq_start_date']} -> {baseline['qqq_end_date']}",
        f"- actual QQQ benchmark range: {actual_benchmark['qqq_start_date']} -> {actual_benchmark['qqq_end_date']}",
        f"- expected EqualWeight benchmark range: {baseline['equal_weight_start_date']} -> {baseline['equal_weight_end_date']}",
        f"- actual EqualWeight benchmark range: {actual_benchmark['equal_weight_start_date']} -> {actual_benchmark['equal_weight_end_date']}",
        f"- QQQ return expected: {baseline['qqq_return']}",
        f"- QQQ return actual: {qqq_return}",
        f"- EqualWeight return expected: {baseline['equal_weight_return']}",
        f"- EqualWeight return actual: {ew_return}",
        f"- status reason: {benchmark_scope_reason}",
        f"- baseline metadata source: {baseline['source']}",
        f"- manual baseline refresh command: `{baseline['refresh_command']}`",
        "- strategy metrics changed: false",
        "- benchmark changed: false",
        f"- main candidate role: {'rs_m126_top30_sma100_no_regime' if main_role_ok else 'changed_or_missing'}",
        f"- paper pending accepted orders readable: {pending_readable}",
        f"- paper pending rows: {pending_rows}",
        f"- critical_warnings: {', '.join(critical) if critical else 'none'}",
        f"- baseline_warnings: {', '.join(warnings) if warnings else 'none'}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return result


def _pipeline_health_columns():
    return PIPELINE_HEALTH_COLUMNS


def _write_pipeline_health_log(status, data_precheck, regression_guard, latest_dir, history_dir, command, total_runtime_sec, cache_summary):
    warning_parts = []
    for result in [data_precheck, regression_guard]:
        warning_parts.extend(result.get("critical", []))
        warning_parts.extend(result.get("warnings", []))
    if status.get("stale_data_warning"):
        warning_parts.append("stale_data_warning")
    health_status = "CRITICAL" if any(r.get("status") == "CRITICAL" for r in [data_precheck, regression_guard]) else (
        "WARNING" if warning_parts else "OK"
    )
    row = {
        "run_datetime": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "command": command or "unknown",
        "data_update_ran": bool(status.get("data_update_ran", False)),
        "latest_daily_date": status.get("latest_daily_data_date", "none"),
        "latest_130min_date": status.get("latest_130min_data_date", "none"),
        "latest_expected_market_session": status.get("latest_expected_us_market_session", "none"),
        "stale_data_warning": bool(status.get("stale_data_warning", False)),
        "pending_order_count": int(status.get("pending_order_count", 0) or 0),
        "accepted_order_count": int(status.get("accepted_order_count", 0) or 0),
        "pending_next_open_count": int(status.get("pending_next_open_count", 0) or 0),
        "filled_count_this_run": int(status.get("filled_count_this_run", 0) or 0),
        "rejected_count_this_run": int(status.get("rejected_count_this_run", 0) or 0),
        "paper_trade_log_rows": int(status.get("paper_trade_log_rows", 0) or 0),
        "strategy_metrics_changed": False,
        "benchmark_changed": False,
        "total_runtime_sec": round(float(total_runtime_sec), 4),
        "cache_hit_count": int(cache_summary.get("prepared_disk_cache_hits", 0) or 0),
        "cache_miss_count": int(cache_summary.get("prepared_disk_cache_misses", 0) or 0),
        "status": health_status,
        "warning_summary": ";".join(sorted(set(str(w) for w in warning_parts if str(w)))) or "none",
    }
    frame = ensure_columns(pd.DataFrame([row]), _pipeline_health_columns())
    latest_path = Path(latest_dir) / "pipeline_health_log.csv"
    history_path = Path(history_dir) / "pipeline_health_log.csv"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(latest_path, index=False)
    frame.to_csv(history_path, mode="a", header=not history_path.exists(), index=False)
    return row


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (pd.Timestamp,)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _report_file_names(directory):
    path = Path(directory)
    if not path.exists():
        return ["run_manifest.json"]
    files = sorted(item.name for item in path.iterdir() if item.is_file() and item.name != "run_manifest.json")
    return files + ["run_manifest.json"]


def _active_symbols(latest_dir):
    active = set()
    latest_dir = Path(latest_dir)
    # Trade log positions
    trade_log_path = latest_dir / "paper_trade_log.csv"
    if trade_log_path.exists():
        try:
            df = pd.read_csv(trade_log_path)
            if not df.empty:
                filled = df[df["fill_status"].astype(str).str.upper().eq("PAPER_FILLED")]
                active.update(filled["symbol"].dropna().astype(str).str.upper().unique())
        except Exception:
            pass
    # Pending orders
    pending_path = latest_dir / "paper_pending_orders.csv"
    if pending_path.exists():
        try:
            df = pd.read_csv(pending_path)
            if not df.empty:
                active.update(df["symbol"].dropna().astype(str).str.upper().unique())
        except Exception:
            pass
    # Review orders
    review_path = latest_dir / "paper_order_review.csv"
    if review_path.exists():
        try:
            df = pd.read_csv(review_path)
            if not df.empty:
                active.update(df["symbol"].dropna().astype(str).str.upper().unique())
        except Exception:
            pass
    return active


def _evaluate_data_policy_status(policy_decisions, latest_dir, daily_dir):
    try:
        from .data_policy_exceptions import DATA_POLICY_EXCEPTIONS
    except ImportError:
        try:
            from data_policy_exceptions import DATA_POLICY_EXCEPTIONS
        except ImportError:
            DATA_POLICY_EXCEPTIONS = {}

    active_syms = _active_symbols(latest_dir)

    # Check PSA repair status against current daily data end date.
    psa_repaired = False
    latest_daily_date = _latest_csv_data_date(daily_dir)
    psa_daily_file = Path(daily_dir) / "PSA_1D.csv"
    if psa_daily_file.exists():
        try:
            df_psa = pd.read_csv(psa_daily_file)
            psa_latest = pd.to_datetime(
                pd.Series([df_psa["Date"].iloc[-1] if not df_psa.empty else pd.NA]),
                errors="coerce",
            ).iloc[0]
            target_latest = pd.to_datetime(pd.Series([latest_daily_date]), errors="coerce").iloc[0]
            if pd.notna(psa_latest) and pd.notna(target_latest) and psa_latest.date() >= target_latest.date():
                psa_repaired = True
        except Exception:
            pass

    blocking_decisions = []
    active_blocking_symbols = []
    inactive_exception_symbols = []

    safe_exception_count = 0
    redownload_required_count = 0
    manual_review_count = 0
    resolving_values = {
        "ACK",
        "ACKNOWLEDGED",
        "ALLOW",
        "ALLOWED",
        "REPAIRED",
        "REPAIR_DONE",
        "REMOVED",
        "REMOVE_FROM_UNIVERSE",
    }

    for item in policy_decisions:
        symbol = str(item.get("symbol", "")).strip().upper()
        warning = str(item.get("warning", "")).strip()
        status_str = str(item.get("policy_status", "")).upper()
        operator_decision = str(item.get("operator_decision", "")).strip().upper()
        operator_note = str(item.get("operator_note", "")).strip()

        # Determine categories
        if symbol in {"BK", "PARA"}:
            safe_exception_count += 1
        elif symbol == "PSA":
            redownload_required_count += 1
        else:
            if status_str.startswith("BLOCK"):
                manual_review_count += 1

        is_resolved = operator_decision in resolving_values and bool(operator_note)
        is_blocking = status_str.startswith("BLOCK") and not is_resolved
        if is_blocking:
            # Check exceptions
            exception = DATA_POLICY_EXCEPTIONS.get(symbol, {})
            if exception and exception.get("category") == "SAFE_KNOWN_EXCEPTION":
                if symbol not in active_syms:
                    inactive_exception_symbols.append(symbol)
                    continue  # do not block

            if symbol == "PSA":
                repair_report_file = Path(latest_dir) / "data_policy_repair_report.csv"
                repair_attempted = repair_report_file.exists()
                if repair_attempted:
                    if psa_repaired:
                        inactive_exception_symbols.append(symbol)
                        continue  # do not block
                    else:
                        if symbol not in active_syms:
                            inactive_exception_symbols.append(symbol)
                            continue  # do not block
                # If not repair_attempted, it falls through to block

            blocking_decisions.append(item)
            active_blocking_symbols.append(symbol)

    if blocking_decisions:
        data_policy_status = "BLOCK"
    elif policy_decisions:
        data_policy_status = "WARNING"
    else:
        data_policy_status = "OK"

    return {
        "status": data_policy_status,
        "blocking_decisions": blocking_decisions,
        "active_blocking_symbols": sorted(set(active_blocking_symbols)),
        "inactive_exception_symbols": sorted(set(inactive_exception_symbols)),
        "safe_known_exception_count": safe_exception_count,
        "data_redownload_required_count": redownload_required_count,
        "manual_review_required_count": manual_review_count,
    }


def _write_run_manifest(
    latest_dir,
    run_dir,
    command,
    report_mode,
    flags,
    daily_dir,
    intraday_dir,
    cache_path,
    cache_summary,
    total_runtime_sec,
    operational_health=None,
):
    health = (operational_health or {}).get("pipeline_health", {})
    data_precheck = (operational_health or {}).get("data_precheck", {})
    regression = (operational_health or {}).get("regression_guard", {})
    policy_decisions = data_precheck.get("policy_decisions", []) or []

    if not data_precheck:
        data_policy_status = "not_generated"
        blocking_policy_decisions = []
        blocking_symbols = []
    else:
        eval_res = _evaluate_data_policy_status(policy_decisions, latest_dir, daily_dir)
        data_policy_status = eval_res["status"]
        blocking_policy_decisions = eval_res["blocking_decisions"]
        blocking_symbols = eval_res["active_blocking_symbols"]
    manifest = {
        "schema_version": 1,
        "run_datetime": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "command": command or "unknown",
        "report_mode": report_mode,
        "flags": flags or {},
        "latest_dir": str(Path(latest_dir)),
        "run_dir": str(Path(run_dir)),
        "daily_data_dir": str(Path(daily_dir)),
        "intraday_data_dir": str(Path(intraday_dir)),
        "latest_daily_date": _latest_csv_data_date(daily_dir),
        "latest_130min_date": _latest_csv_data_date(intraday_dir),
        "cache_path": str(cache_path) if cache_path else "",
        "cache_hit_count": int(cache_summary.get("prepared_disk_cache_hits", 0) or 0),
        "cache_miss_count": int(cache_summary.get("prepared_disk_cache_misses", 0) or 0),
        "symbols_loaded": int(cache_summary.get("symbols_loaded", 0) or 0),
        "rows_loaded": int(cache_summary.get("rows_loaded", 0) or 0),
        "total_runtime_sec": round(float(total_runtime_sec), 4),
        "pipeline_status": health.get("status", "not_generated"),
        "strategy_metrics_changed": bool(health.get("strategy_metrics_changed", False)),
        "benchmark_changed": bool(health.get("benchmark_changed", False)),
        "warning_summary": health.get("warning_summary", "none"),
        "data_precheck_status": data_precheck.get("status", "not_generated"),
        "data_policy_status": data_policy_status,
        "data_policy_decision_count": len(policy_decisions),
        "data_policy_blocking_count": len(blocking_policy_decisions),
        "data_policy_blocking_symbols": sorted(
            str(item.get("symbol", "")) for item in blocking_policy_decisions if item.get("symbol")
        ),
        "regression_guard_status": regression.get("status", "not_generated"),
        "available_reports_latest": _report_file_names(latest_dir),
        "generated_reports_run": _report_file_names(run_dir),
    }
    for directory in [Path(latest_dir), Path(run_dir)]:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "run_manifest.json").write_text(
            json.dumps(_json_safe(manifest), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return manifest


def _pending_aging_summary(path):
    if not Path(path).exists():
        return {"max_calendar_days": 0, "max_trading_days": 0, "stale_count": 0}
    try:
        pending = pd.read_csv(path)
    except Exception:
        return {"max_calendar_days": 0, "max_trading_days": 0, "stale_count": 0}
    if pending.empty:
        return {"max_calendar_days": 0, "max_trading_days": 0, "stale_count": 0}
    calendar = pd.to_numeric(pending.get("pending_age_calendar_days", pd.Series(dtype=float)), errors="coerce").fillna(0)
    trading = pd.to_numeric(pending.get("pending_age_trading_days", pd.Series(dtype=float)), errors="coerce").fillna(0)
    stale = pending.get("pending_stale_warning", pd.Series(False, index=pending.index)).astype(str).str.lower().isin(["true", "1", "yes"])
    return {
        "max_calendar_days": int(calendar.max()) if not calendar.empty else 0,
        "max_trading_days": int(trading.max()) if not trading.empty else 0,
        "stale_count": int(stale.sum()),
    }


def _write_report_schema_validation_report(latest_dir, path):
    checks = [
        ("paper_signal_report.csv", PAPER_SIGNAL_COLUMNS),
        ("paper_pending_orders.csv", PAPER_PENDING_ORDERS_COLUMNS),
        ("paper_order_review.csv", PAPER_ORDER_REVIEW_COLUMNS),
        ("paper_trade_log.csv", PAPER_TRADE_LOG_COLUMNS),
        ("pipeline_health_log.csv", PIPELINE_HEALTH_COLUMNS),
    ]
    data_policy_decisions = Path(latest_dir) / "data_policy_decisions.csv"
    if data_policy_decisions.exists():
        checks.append(("data_policy_decisions.csv", DATA_POLICY_DECISION_COLUMNS))
    data_policy_evidence = Path(latest_dir) / "data_policy_review_evidence.csv"
    if data_policy_evidence.exists():
        checks.append(("data_policy_review_evidence.csv", DATA_POLICY_REVIEW_EVIDENCE_COLUMNS))
    alpaca_preview = Path(latest_dir) / "alpaca_order_preview.csv"
    if alpaca_preview.exists():
        checks.append(("alpaca_order_preview.csv", ALPACA_ORDER_PREVIEW_COLUMNS))
    rows = []
    for filename, columns in checks:
        file_path = Path(latest_dir) / filename
        if not file_path.exists():
            rows.append(
                {
                    "file": filename,
                    "missing_columns": "file_missing",
                    "extra_columns": "",
                    "status": "WARNING",
                }
            )
            continue
        try:
            frame = pd.read_csv(file_path, nrows=1)
            validation = validate_required_columns(frame, columns)
        except Exception as error:
            rows.append(
                {
                    "file": filename,
                    "missing_columns": f"read_error:{error}",
                    "extra_columns": "",
                    "status": "WARNING",
                }
            )
            continue
        missing = validation["missing_columns"]
        extra = validation["extra_columns"]
        rows.append(
            {
                "file": filename,
                "missing_columns": ",".join(missing),
                "extra_columns": ",".join(extra),
                "status": "WARNING" if missing or extra else "OK",
            }
        )
    status = "WARNING" if any(row["status"] == "WARNING" for row in rows) else "OK"
    lines = [
        "# Report Schema Validation Report",
        "",
        f"- status: {status}",
        "- schema source: report_schemas.py",
        "",
        "## Checked Files",
        "",
        _readable_table(pd.DataFrame(rows)),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": status, "rows": rows, "path": "reports/latest/report_schema_validation_report.md"}


def _write_paper_state_validation_report(reports_dir, path):
    validation = validate_paper_state(reports_dir)
    try:
        validation["open_paper_positions"] = len(net_open_positions_from_trade_log(read_trade_log(reports_dir)))
    except Exception:
        validation["open_paper_positions"] = "not_available"
    lines = [
        "# Paper State Validation Report",
        "",
        f"- pending_orders_exists: {validation['pending_orders_exists']}",
        f"- order_review_exists: {validation['order_review_exists']}",
        f"- trade_log_exists: {validation['trade_log_exists']}",
        f"- pending_orders_rows: {validation['pending_orders_rows']}",
        f"- order_review_rows: {validation['order_review_rows']}",
        f"- trade_log_rows: {validation['trade_log_rows']}",
        f"- open_paper_positions: {validation['open_paper_positions']}",
        f"- schema_status: {validation['schema_status']}",
        f"- status: {validation['status']}",
        "",
        "## Files",
        "",
        _readable_table(pd.DataFrame(validation["rows"])),
        "",
    ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    validation["path"] = "reports/latest/paper_state_validation_report.md"
    return validation
