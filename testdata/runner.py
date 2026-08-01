import hashlib
import io
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PREPARED_CACHE_VERSION = "modular_prepared_cache_v3_donchian_features"
_WORKER_PREPARED = None

try:
    from .config import DAILY_DATA_DIR, DAILY_TIMEFRAME, INTRADAY_DATA_DIR, INTRADAY_TIMEFRAME, RAW_1MIN_DATA_DIR
    from .data_engine import read_ohlcv_csv_fast
    from .diagnostics import _asset_signal_diagnostics
    from .reports import (
        DIVIDEND_ADJUSTMENT_WARNING,
        SURVIVORSHIP_BIAS_WARNING,
        SURVIVORSHIP_BIAS_BANNER,
        _add_compact_scores,
        _prefixed_benchmark_metrics,
        _write_compact_markdown,
        calculate_buyhold_metrics,
        calculate_equal_weight_benchmark,
    )
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
        REGRESSION_GUARD_COLUMNS,
        ensure_columns,
        validate_required_columns,
    )
    from .paper_state_manager import (
        append_trade_log,
        create_trade_log_template,
        read_order_review,
        read_pending_orders,
        read_trade_log,
        validate_paper_state,
        write_order_review,
        write_pending_orders,
        write_trade_log,
        reset_paper_ledger_counters,
        get_last_skipped_duplicate_fill_count,
        get_last_newly_added_count,
        get_stable_key,
        net_open_positions_from_trade_log,
    )
    from .paper_portfolio_monitor import write_paper_portfolio_monitor
    from .intraday_paper_monitor import write_intraday_paper_monitor
    from .paper_ops_dashboard import write_paper_ops_dashboard
    from .paper_decision_inbox import write_paper_decision_inbox
    from .paper_risk_dashboard import write_paper_risk_dashboard
    from .risk_engine import write_risk_engine_report
    from .chatgpt_master_report import (
        _safe_columns,
        _readable_table,
        _chatgpt_operational_health_section,
        _chatgpt_daily_pipeline_status_section,
        _chatgpt_paper_readiness_section,
        _chatgpt_data_audit_section,
        _chatgpt_benchmark_section,
        _chatgpt_strategy_section,
        _chatgpt_oos_section,
        _chatgpt_attribution_section,
        _chatgpt_cost_section,
        _chatgpt_liquidity_guard_section,
        _chatgpt_performance_section,
        _write_sqlite_state_report,
        _write_chatgpt_master_report,
        _markdown_bullet_value,
        _chatgpt_test_suite_status_section,
        _chatgpt_existing_execution_readiness_section,
        _chatgpt_useful_commands_section,
    )
    from .closed_trade_report import write_closed_trade_report
    from .no_lookahead_audit import write_no_lookahead_audit
    from .execution_costs import write_execution_cost_preview
    from .sqlite_state_reconciliation import write_sqlite_state_reconciliation
    from .alpaca_broker_reporting import (
        _alpaca_config,
        _alpaca_config_for_data_folder,
        _alpaca_report_lines,
        _paper_order_review_decisions,
        _paper_signal_lookup,
        _read_alpaca_paper_snapshot_for_data_folder,
        _submit_candidate_symbols,
        _write_alpaca_master_section,
        _write_alpaca_order_preview,
        run_alpaca_paper_check,
        run_alpaca_paper_submit_one,
        run_alpaca_paper_submit_readiness,
        run_broker_cutover_readiness,
        run_broker_order_lifecycle_monitor,
        run_broker_reconciliation,
        run_create_broker_cutover_baseline,
    )
    from .research_validation_reports import (
        _adjusted_equity_stats,
        _attribution_candidate_experiments,
        _best_guard_for_capital,
        _candidate_attribution_rows,
        _candidate_row,
        _cost_adjusted_stats,
        _cost_stress_candidate_experiments,
        _cost_stress_rows,
        _cost_stress_scenarios,
        _entry_bar_dollar_volumes,
        _final_candidate_decision_frame,
        _liquidity_guard_candidate_experiments,
        _liquidity_guard_mask,
        _liquidity_guard_scenarios,
        _liquidity_guard_sensitivity_rows,
        _liquidity_stress_metrics,
        _max_positions_from_params,
        _oos_candidate_experiments,
        _oos_report_row,
        _oos_walkforward_periods,
        _read_final_candidate_decision_report,
        _rs_robustness_experiments,
        _run_candidate_attribution_report,
        _run_cost_slippage_stress_report,
        _run_liquidity_guard_sensitivity_report,
        _run_oos_walkforward_report,
        _trades_with_entry_liquidity,
        _write_candidate_attribution_report,
        _write_candidate_summary_report,
        _write_cost_slippage_stress_report,
        _write_final_candidate_decision_report,
        _write_liquidity_guard_sensitivity_report,
        _write_oos_walkforward_report,
        _write_rs_robustness_report,
        _write_yearly_report,
        _yearly_focus_names,
    )
    from .data_governance_reports import (
        _active_symbols,
        _benchmark_baseline_stale,
        _benchmark_return_warnings,
        _clean_markdown_cell,
        _data_precheck_actions,
        _data_precheck_policy_decisions,
        _data_policy_key,
        _evaluate_data_policy_status,
        _is_date_only,
        _json_safe,
        _pending_aging_summary,
        _pipeline_health_columns,
        _read_regression_benchmark_baseline,
        _report_file_names,
        _same_timestamp,
        _symbol_data_status,
        _timestamp_after,
        _write_data_policy_decision_file,
        _write_data_precheck_report,
        _write_paper_state_validation_report,
        _write_pipeline_health_log,
        _write_regression_guard_report,
        _write_report_schema_validation_report,
        _write_run_manifest,
    )
    from .execution_engine import run_execution_preview
    from .baseline_parity import archive_new_paper_decision_events
    from .strategies.base import generate_strategy_signals
    from .strategies.cross_sectional_momentum import (
        _apply_cross_sectional_momentum_top_signals,
        _normalize_cross_sectional_momentum_params,
        _run_cross_sectional_momentum_backtest,
    )
    from .strategies.donchian_breakout import (
        _apply_donchian_breakout_top_signals,
        _normalize_donchian_breakout_params,
        _prepare_donchian_context,
        _run_donchian_breakout_backtest,
    )
    from .strategies.dual_momentum import (
        _apply_dual_momentum_top_signals,
        _normalize_dual_momentum_params,
        _run_dual_momentum_backtest,
    )
    from .strategies.ma_slope_trend import (
        _apply_ma_slope_trend_top_signals,
        _normalize_ma_slope_trend_params,
        _run_ma_slope_trend_backtest,
    )
    from .strategies.relative_strength_rotation import (
        _apply_relative_strength_rotation_top_signals,
        _normalize_relative_strength_params,
        _run_relative_strength_rotation_backtest,
    )
except ImportError:
    from config import DAILY_DATA_DIR, DAILY_TIMEFRAME, INTRADAY_DATA_DIR, INTRADAY_TIMEFRAME, RAW_1MIN_DATA_DIR
    from data_engine import read_ohlcv_csv_fast
    from diagnostics import _asset_signal_diagnostics
    from reports import (
        DIVIDEND_ADJUSTMENT_WARNING,
        SURVIVORSHIP_BIAS_WARNING,
        SURVIVORSHIP_BIAS_BANNER,
        _add_compact_scores,
        _prefixed_benchmark_metrics,
        _write_compact_markdown,
        calculate_buyhold_metrics,
        calculate_equal_weight_benchmark,
    )
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
        REGRESSION_GUARD_COLUMNS,
        ensure_columns,
        validate_required_columns,
    )
    from paper_state_manager import (
        append_trade_log,
        create_trade_log_template,
        read_order_review,
        read_pending_orders,
        read_trade_log,
        validate_paper_state,
        write_order_review,
        write_pending_orders,
        write_trade_log,
        reset_paper_ledger_counters,
        get_last_skipped_duplicate_fill_count,
        get_last_newly_added_count,
        get_stable_key,
        net_open_positions_from_trade_log,
    )
    from paper_portfolio_monitor import write_paper_portfolio_monitor
    from intraday_paper_monitor import write_intraday_paper_monitor
    from paper_ops_dashboard import write_paper_ops_dashboard
    from paper_decision_inbox import write_paper_decision_inbox
    from paper_risk_dashboard import write_paper_risk_dashboard
    from risk_engine import write_risk_engine_report
    from chatgpt_master_report import (
        _safe_columns,
        _readable_table,
        _chatgpt_operational_health_section,
        _chatgpt_daily_pipeline_status_section,
        _chatgpt_paper_readiness_section,
        _chatgpt_data_audit_section,
        _chatgpt_benchmark_section,
        _chatgpt_strategy_section,
        _chatgpt_oos_section,
        _chatgpt_attribution_section,
        _chatgpt_cost_section,
        _chatgpt_liquidity_guard_section,
        _chatgpt_performance_section,
        _write_sqlite_state_report,
        _write_chatgpt_master_report,
        _markdown_bullet_value,
        _chatgpt_test_suite_status_section,
        _chatgpt_existing_execution_readiness_section,
        _chatgpt_useful_commands_section,
    )
    from closed_trade_report import write_closed_trade_report
    from no_lookahead_audit import write_no_lookahead_audit
    from execution_costs import write_execution_cost_preview
    from sqlite_state_reconciliation import write_sqlite_state_reconciliation
    from alpaca_broker_reporting import (
        _alpaca_config,
        _alpaca_config_for_data_folder,
        _alpaca_report_lines,
        _paper_order_review_decisions,
        _paper_signal_lookup,
        _read_alpaca_paper_snapshot_for_data_folder,
        _submit_candidate_symbols,
        _write_alpaca_master_section,
        _write_alpaca_order_preview,
        run_alpaca_paper_check,
        run_alpaca_paper_submit_one,
        run_alpaca_paper_submit_readiness,
        run_broker_cutover_readiness,
        run_broker_order_lifecycle_monitor,
        run_broker_reconciliation,
        run_create_broker_cutover_baseline,
    )
    from research_validation_reports import (
        _adjusted_equity_stats,
        _attribution_candidate_experiments,
        _best_guard_for_capital,
        _candidate_attribution_rows,
        _candidate_row,
        _cost_adjusted_stats,
        _cost_stress_candidate_experiments,
        _cost_stress_rows,
        _cost_stress_scenarios,
        _entry_bar_dollar_volumes,
        _final_candidate_decision_frame,
        _liquidity_guard_candidate_experiments,
        _liquidity_guard_mask,
        _liquidity_guard_scenarios,
        _liquidity_guard_sensitivity_rows,
        _liquidity_stress_metrics,
        _max_positions_from_params,
        _oos_candidate_experiments,
        _oos_report_row,
        _oos_walkforward_periods,
        _read_final_candidate_decision_report,
        _rs_robustness_experiments,
        _run_candidate_attribution_report,
        _run_cost_slippage_stress_report,
        _run_liquidity_guard_sensitivity_report,
        _run_oos_walkforward_report,
        _trades_with_entry_liquidity,
        _write_candidate_attribution_report,
        _write_candidate_summary_report,
        _write_cost_slippage_stress_report,
        _write_final_candidate_decision_report,
        _write_liquidity_guard_sensitivity_report,
        _write_oos_walkforward_report,
        _write_rs_robustness_report,
        _write_yearly_report,
        _yearly_focus_names,
    )
    from data_governance_reports import (
        _active_symbols,
        _benchmark_baseline_stale,
        _benchmark_return_warnings,
        _clean_markdown_cell,
        _data_precheck_actions,
        _data_precheck_policy_decisions,
        _data_policy_key,
        _evaluate_data_policy_status,
        _is_date_only,
        _json_safe,
        _pending_aging_summary,
        _pipeline_health_columns,
        _read_regression_benchmark_baseline,
        _report_file_names,
        _same_timestamp,
        _symbol_data_status,
        _timestamp_after,
        _write_data_policy_decision_file,
        _write_data_precheck_report,
        _write_paper_state_validation_report,
        _write_pipeline_health_log,
        _write_regression_guard_report,
        _write_report_schema_validation_report,
        _write_run_manifest,
    )
    from execution_engine import run_execution_preview
    from baseline_parity import archive_new_paper_decision_events
    from strategies.base import generate_strategy_signals
    from strategies.cross_sectional_momentum import (
        _apply_cross_sectional_momentum_top_signals,
        _normalize_cross_sectional_momentum_params,
        _run_cross_sectional_momentum_backtest,
    )
    from strategies.donchian_breakout import (
        _apply_donchian_breakout_top_signals,
        _normalize_donchian_breakout_params,
        _prepare_donchian_context,
        _run_donchian_breakout_backtest,
    )
    from strategies.dual_momentum import (
        _apply_dual_momentum_top_signals,
        _normalize_dual_momentum_params,
        _run_dual_momentum_backtest,
    )
    from strategies.ma_slope_trend import (
        _apply_ma_slope_trend_top_signals,
        _normalize_ma_slope_trend_params,
        _run_ma_slope_trend_backtest,
    )
    from strategies.relative_strength_rotation import (
        _apply_relative_strength_rotation_top_signals,
        _normalize_relative_strength_params,
        _run_relative_strength_rotation_backtest,
    )

try:
    from .state_store import (
        init_state_db,
        append_pipeline_run,
        append_paper_orders,
        append_paper_trades,
        append_order_intents,
        append_execution_audit,
        append_pre_submit_checks,
        append_short_readiness_checks,
        get_state_summary
    )
except ImportError:
    from state_store import (
        init_state_db,
        append_pipeline_run,
        append_paper_orders,
        append_paper_trades,
        append_order_intents,
        append_execution_audit,
        append_pre_submit_checks,
        append_short_readiness_checks,
        get_state_summary
    )


def run_universe_scanner(*args, **kwargs):
    raise NotImplementedError("run_universe_scanner is not part of the modular validation runner.")


def _now():
    return time.perf_counter()


def _stage_started(stage_name):
    print(f"[stage] {stage_name} started")
    return _now()


def _stage_done(stage_name, started_at):
    elapsed = _now() - started_at
    print(f"[stage] {stage_name} done in {elapsed:.2f} sec")
    return elapsed


class _TimingCollector:
    def __init__(self):
        self.rows = []

    def stage(self, name, notes=""):
        collector = self

        class _Stage:
            def __enter__(self_inner):
                self_inner.started_at = _now()
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                collector.rows.append(
                    {
                        "stage": name,
                        "notes": notes,
                        "seconds": _now() - self_inner.started_at,
                    }
                )

        return _Stage()


def _warn_onedrive_path(path):
    if "onedrive" in str(Path(path).resolve()).lower():
        print(
            "Warning: OneDrive path detected; IO performance may be slow. "
            "Prefer local SSD path like C:\\BOT."
        )


def resolve_project_path(path, base_data_dir):
    target = Path(path)
    if target.is_absolute():
        return target
    if (
        len(target.parts) >= 2
        and target.parts[0].lower() == "testdata"
        and target.parts[1].lower() == "reports"
    ):
        target = Path(*target.parts[1:])
    base = Path(base_data_dir)
    if not base.is_absolute():
        direct = Path.cwd() / base
        testdata_direct = Path.cwd() / "testdata" / base
        if direct.exists() or (base.parts and base.parts[0].lower() == "testdata"):
            base = direct
        elif testdata_direct.exists():
            base = testdata_direct
        else:
            base = direct
    data_root = base.parent if base.name.lower().startswith("hisseler") else base
    if data_root.name.lower() != "testdata" and (data_root / "testdata").exists():
        data_root = data_root / "testdata"
    return (data_root / target).resolve()


def _report_output_paths(report_output_dir, base_data_dir):
    report_root = resolve_project_path(report_output_dir, base_data_dir)
    legacy_duplicate = report_root.parent / "testdata" / "reports"
    if legacy_duplicate.exists() and legacy_duplicate.resolve() != report_root:
        print(f"Warning: duplicate legacy report dir found: {legacy_duplicate}")
    timestamp = pd.Timestamp.now().strftime("%Y-%m-%d_%H%M%S")
    latest_dir = report_root / "latest"
    run_dir = report_root / "runs" / timestamp
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    return latest_dir, run_dir


def _read_env_file(path):
    values = {}
    if not Path(path).exists():
        return values
    for raw_line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


PAPER_5MIN_FILL_CHECK_COLUMNS = [
    "symbol",
    "strategy_name",
    "signal_date",
    "pending_status",
    "raw_5min_latest_timestamp",
    "raw_5min_latest_date",
    "has_next_open_5min",
    "hypothetical_fill_timestamp",
    "hypothetical_fill_price",
    "warning",
    "would_fill_if_5min_enabled",
]


RAW_5MIN_UPDATE_AUDIT_COLUMNS = [
    "raw_5min_update_called",
    "provider_source_used",
    "requested_symbols_count",
    "pending_symbols_requested",
    "pending_symbol",
    "latest_raw_5min_date_by_pending_symbol",
    "same_day_requested",
    "same_day_rows_received_count",
    "files_written_count",
    "reason_if_no_target_date_data",
]


PENDING_5MIN_REFRESH_COLUMNS = [
    "symbol",
    "pending_order_count",
    "raw_5min_file_exists",
    "old_latest_timestamp",
    "provider_requested",
    "provider_rows_received",
    "new_rows_added",
    "new_latest_timestamp",
    "raw_5min_refresh_status",
    "rebuilt_130min",
    "old_130min_latest_timestamp",
    "new_130min_latest_timestamp",
    "warning",
    "error",
]


def _raw_5min_dir(data_folder):
    return Path(data_folder) / "raw_5min"


def _pending_5min_diagnostic_orders(latest_dir):
    pending = read_pending_orders(latest_dir)
    if pending is None or pending.empty:
        return pd.DataFrame()
    decision = pending.get("user_decision", pd.Series("", index=pending.index)).astype(str).str.upper()
    status = pending.get("status", pd.Series("", index=pending.index)).astype(str).str.upper()
    fill_status = pending.get("fill_status", pd.Series("", index=pending.index)).astype(str).str.upper()
    pending_next_open = fill_status.eq("PENDING_NEXT_OPEN") | status.isin(["PENDING_NEXT_OPEN", "PENDING"])
    return pending[decision.eq("ACCEPT") & pending_next_open].copy()


def _pending_5min_refresh_orders(latest_dir):
    return _pending_5min_diagnostic_orders(latest_dir)


def _paper_fill_target_date(order):
    expected = pd.to_datetime(order.get("expected_next_open_date", ""), errors="coerce")
    if pd.notna(expected):
        return expected.date()
    signal = pd.to_datetime(order.get("signal_date", ""), errors="coerce")
    if pd.isna(signal):
        return None
    return _next_us_market_session(signal.date())


def _first_rth_5min_fill(raw_frame, signal_date, target_date):
    if raw_frame is None or raw_frame.empty:
        return None, pd.NA
    if target_date is None:
        return None, pd.NA
    frame = raw_frame.sort_index()
    signal_timestamp = pd.to_datetime(signal_date, errors="coerce")
    if pd.isna(signal_timestamp):
        signal_timestamp = pd.Timestamp.min.tz_localize(frame.index.tz) if frame.index.tz is not None else pd.Timestamp.min
    elif frame.index.tz is not None:
        if signal_timestamp.tzinfo is None:
            signal_timestamp = signal_timestamp.tz_localize(frame.index.tz)
        else:
            signal_timestamp = signal_timestamp.tz_convert(frame.index.tz)
    elif signal_timestamp.tzinfo is not None:
        signal_timestamp = signal_timestamp.tz_localize(None)

    local_index = frame.index
    session_date = pd.to_datetime(target_date).date()
    mask = (
        (pd.Series(local_index.date, index=frame.index).eq(session_date))
        & (local_index > signal_timestamp)
        & (pd.Series(local_index.time, index=frame.index) >= pd.Timestamp("09:30").time())
        & (pd.Series(local_index.time, index=frame.index) < pd.Timestamp("16:00").time())
    )
    candidates = frame.loc[mask]
    if candidates.empty:
        return None, pd.NA
    first_timestamp = candidates.index[0]
    price = pd.to_numeric(pd.Series([candidates.iloc[0].get("Open", pd.NA)]), errors="coerce").iloc[0]
    return first_timestamp, price


def _paper_5min_missing_warning(raw_latest_date, target_date):
    if target_date is None:
        return "missing_expected_next_open_date"
    target = pd.to_datetime(target_date).date()
    latest = pd.to_datetime(raw_latest_date, errors="coerce")
    if pd.isna(latest):
        return f"missing_{target.isoformat()}_rth_bar_after_signal_date"
    latest_date = latest.date()
    if latest_date < target:
        return f"latest_5min_before_target_date:{latest_date}_lt_{target}"
    if latest_date > target:
        return f"target_date_mismatch_or_missing_target_bar:{target}_latest_{latest_date}"
    return f"missing_{target.isoformat()}_rth_bar_after_signal_date"


def _paper_5min_fill_check_summary(check):
    if check is None or check.empty:
        return {
            "status": "WARNING",
            "checked_order_count": 0,
            "would_fill_count": 0,
            "missing_5min_count": 0,
            "latest_5min_date_min": "none",
            "latest_5min_date_max": "none",
            "official_state_mutated": False,
        }
    latest_dates = check["raw_5min_latest_date"].replace("", pd.NA).dropna()
    missing_5min_count = int((~check["has_next_open_5min"].astype(bool)).sum())
    return {
        "status": "OK" if missing_5min_count == 0 else "WARNING",
        "checked_order_count": int(len(check)),
        "would_fill_count": int(check["would_fill_if_5min_enabled"].astype(bool).sum()),
        "missing_5min_count": missing_5min_count,
        "latest_5min_date_min": str(latest_dates.min()) if not latest_dates.empty else "none",
        "latest_5min_date_max": str(latest_dates.max()) if not latest_dates.empty else "none",
        "official_state_mutated": False,
    }


def _write_paper_5min_fill_check_report(check, summary, csv_path, markdown_path):
    check = ensure_columns(check, PAPER_5MIN_FILL_CHECK_COLUMNS, fill_value="")
    check.to_csv(csv_path, index=False)
    lines = [
        "# Paper 5min Fill Check Report",
        "",
        "Read-only diagnostic. Official paper pending orders and trade log are not mutated.",
        "",
        f"- status: {summary['status']}",
        f"- checked_order_count: {summary['checked_order_count']}",
        f"- would_fill_count: {summary['would_fill_count']}",
        f"- missing_5min_count: {summary['missing_5min_count']}",
        f"- latest_5min_date_min: {summary['latest_5min_date_min']}",
        f"- latest_5min_date_max: {summary['latest_5min_date_max']}",
        f"- official_state_mutated: {str(summary['official_state_mutated']).lower()}",
        "",
        _readable_table(check),
        "",
    ]
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def _replace_markdown_section(text, marker, section):
    replacement = "\n".join(section).rstrip()
    if marker not in text:
        return text.rstrip() + "\n\n" + replacement
    start = text.index(marker)
    next_start = text.find("\n## ", start + len(marker))
    if next_start == -1:
        return text[:start].rstrip() + "\n\n" + replacement
    return text[:start].rstrip() + "\n\n" + replacement + "\n\n" + text[next_start:].lstrip()


def _write_paper_5min_master_section(latest_dir, summary):
    path = Path(latest_dir) / "chatgpt_master_report.md"
    marker = "## 5min Fill Diagnostic"
    section = [
        marker,
        "",
        f"- status: {summary['status']}",
        f"- checked_order_count: {summary['checked_order_count']}",
        f"- would_fill_count: {summary['would_fill_count']}",
        f"- missing_5min_count: {summary['missing_5min_count']}",
        f"- latest_5min_date_min: {summary['latest_5min_date_min']}",
        f"- latest_5min_date_max: {summary['latest_5min_date_max']}",
        "- official_state_mutated: false",
        "",
    ]
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else "# ChatGPT Master Report\n"
    path.write_text(_replace_markdown_section(text, marker, section), encoding="utf-8")


def _raw_5min_latest_date(path):
    if not Path(path).exists():
        return ""
    try:
        frame = pd.read_csv(path, usecols=["Date"])
    except Exception:
        return ""
    dates = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()
    return str(dates.max().date()) if not dates.empty else ""


def _raw_5min_same_day_row_count(path, target_date=None):
    if not Path(path).exists() or target_date is None:
        return 0
    try:
        frame = pd.read_csv(path, usecols=["Date"])
    except Exception:
        return 0
    dates = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()
    if dates.empty:
        return 0
    return int((dates.dt.date == pd.to_datetime(target_date).date()).sum())


def _daily_pipeline_update_command(project_root):
    path = Path(project_root) / "scripts" / "run_daily_paper_pipeline.bat"
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if "vericekici_updated.py" in stripped and stripped.lower().startswith("python "):
            return stripped
    return ""


def _command_value(command, flag):
    parts = command.split()
    if flag not in parts:
        return ""
    index = parts.index(flag)
    return parts[index + 1] if index + 1 < len(parts) else ""


def _read_ohlcv_file(path):
    if not Path(path).exists():
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    try:
        frame = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    for column in required:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame[required].dropna(subset=["Date"]).copy()


def _latest_timestamp_string(frame):
    if frame is None or frame.empty or "Date" not in frame:
        return ""
    dates = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()
    if dates.empty:
        return ""
    return str(dates.max().tz_convert("America/New_York"))


def _normalize_provider_bars(raw_bars, symbol):
    output_columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if raw_bars is None or raw_bars.empty:
        return pd.DataFrame(columns=output_columns)
    bars = raw_bars.copy()
    if isinstance(bars.index, pd.MultiIndex):
        symbol_level = "symbol" if "symbol" in bars.index.names else bars.index.names[0]
        if symbol not in set(bars.index.get_level_values(symbol_level).astype(str).str.upper()):
            return pd.DataFrame(columns=output_columns)
        bars = bars.xs(symbol, level=symbol_level)
    bars.index = pd.to_datetime(bars.index, utc=True).tz_convert("America/New_York")
    bars = bars.sort_index()
    bars = bars.between_time("09:30", "15:59", inclusive="both")
    bars = bars.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    )
    missing = [column for column in ["Open", "High", "Low", "Close", "Volume"] if column not in bars.columns]
    if missing:
        raise ValueError(f"{symbol}: provider response missing columns {missing}")
    bars = bars[["Open", "High", "Low", "Close", "Volume"]].dropna()
    bars.index.name = "Date"
    return bars.reset_index()[output_columns]


def _merge_ohlcv_frames(existing, incoming):
    output_columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
    frames = []
    for frame in [existing, incoming]:
        if frame is not None and not frame.empty:
            work = frame.copy()
            for column in output_columns:
                if column not in work.columns:
                    work[column] = pd.NA
            frames.append(work[output_columns])
    if not frames:
        return pd.DataFrame(columns=output_columns)
    merged = pd.concat(frames, ignore_index=True)
    parsed = pd.to_datetime(merged["Date"], errors="coerce", utc=True)
    merged = merged.loc[parsed.notna()].copy()
    merged["_parsed_date"] = parsed[parsed.notna()]
    merged = merged.sort_values("_parsed_date")
    merged = merged.drop_duplicates("_parsed_date", keep="last")
    merged["Date"] = merged["_parsed_date"].dt.tz_convert("America/New_York").astype(str)
    return merged[output_columns].reset_index(drop=True)


def _atomic_write_ohlcv(path, frame):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.stem}.tmp{path.suffix}")
    frame.to_csv(temp_path, index=False)
    os.replace(temp_path, path)


def _refresh_fetch_window(existing, pending_rows):
    dates = pd.to_datetime(existing.get("Date", pd.Series(dtype=str)), errors="coerce", utc=True).dropna()
    if not dates.empty:
        start = dates.max().floor("D").to_pydatetime()
    else:
        signal_dates = pd.to_datetime(pending_rows.get("signal_date", pd.Series(dtype=str)), errors="coerce", utc=True).dropna()
        start = signal_dates.min().floor("D").to_pydatetime() if not signal_dates.empty else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.now(timezone.utc)
    if end <= start:
        end = start + pd.Timedelta(days=1)
    return start, end


def _fetch_pending_raw_5min(symbol, start, end):
    try:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    except ModuleNotFoundError as error:
        if error.name != "alpaca":
            raise
        raise RuntimeError("missing alpaca-py; install with python -m pip install alpaca-py") from error

    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        env_path = Path(__file__).resolve().parents[1] / ".env"
    api_key, secret_key, _base_url = _alpaca_config(env_path)
    if not api_key or not secret_key:
        raise RuntimeError("missing ALPACA_API_KEY or ALPACA_SECRET_KEY")
    client = StockHistoricalDataClient(api_key, secret_key)
    request_obj = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=start,
        end=end,
        adjustment=Adjustment.ALL,
        feed=DataFeed.IEX,
    )
    response = client.get_stock_bars(request_obj)
    return _normalize_provider_bars(response.df, symbol)


def _complete_130min_from_raw(raw_5min):
    output_columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if raw_5min is None or raw_5min.empty:
        return pd.DataFrame(columns=output_columns), "no_raw_5min_rows"
    frame = raw_5min.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["Date"]).sort_values("Date")
    if frame.empty:
        return pd.DataFrame(columns=output_columns), "no_valid_raw_5min_timestamps"
    local_time = frame["Date"].dt.tz_convert("America/New_York")
    minutes = local_time.dt.hour * 60 + local_time.dt.minute
    rth_start = 9 * 60 + 30
    rth_end = 16 * 60
    frame = frame[(minutes >= rth_start) & (minutes < rth_end)].copy()
    if frame.empty:
        return pd.DataFrame(columns=output_columns), "no_rth_raw_5min_rows"
    local_time = frame["Date"].dt.tz_convert("America/New_York")
    minutes = local_time.dt.hour * 60 + local_time.dt.minute
    frame["session"] = local_time.dt.date
    bins = [rth_start - 1, 11 * 60 + 39, 13 * 60 + 49, rth_end - 1]
    frame["block"] = pd.cut(minutes, bins=bins, labels=[0, 1, 2]).astype(int)
    block_start_minutes = {0: 9 * 60 + 30, 1: 11 * 60 + 40, 2: 13 * 60 + 50}
    block_last_start_minutes = {0: 11 * 60 + 35, 1: 13 * 60 + 45, 2: 15 * 60 + 55}
    complete_keys = []
    incomplete_count = 0
    for key, group in frame.groupby(["session", "block"], sort=True):
        group_local = group["Date"].dt.tz_convert("America/New_York")
        group_minutes = group_local.dt.hour * 60 + group_local.dt.minute
        if int(group_minutes.max()) >= block_last_start_minutes[int(key[1])]:
            complete_keys.append(key)
        else:
            incomplete_count += 1
    if not complete_keys:
        return pd.DataFrame(columns=output_columns), "no_complete_130min_bars"
    filtered = frame[
        frame.apply(lambda row: (row["session"], row["block"]) in set(complete_keys), axis=1)
    ].copy()
    output = filtered.groupby(["session", "block"], sort=True).agg(
        Open=("Open", "first"),
        High=("High", "max"),
        Low=("Low", "min"),
        Close=("Close", "last"),
        Volume=("Volume", "sum"),
    ).reset_index()
    session_start = pd.to_datetime(output["session"].astype(str)).dt.tz_localize("America/New_York")
    output["Date"] = [
        session + pd.Timedelta(minutes=block_start_minutes[int(block)])
        for session, block in zip(session_start, output["block"])
    ]
    output["Date"] = pd.to_datetime(output["Date"], utc=True).astype(str)
    warning = "incomplete_current_130min_bar_excluded" if incomplete_count else ""
    return output[output_columns].dropna().reset_index(drop=True), warning


def _write_pending_5min_master_section(latest_dir, summary):
    path = Path(latest_dir) / "chatgpt_master_report.md"
    marker = "## Pending 5min Refresh"
    section = [
        marker,
        "",
        f"- status: {summary['status']}",
        f"- pending_symbols_count: {summary['pending_symbols_count']}",
        f"- provider_requested_count: {summary['provider_requested_count']}",
        f"- symbols_refreshed_count: {summary['symbols_refreshed_count']}",
        f"- total_provider_rows_received: {summary['total_provider_rows_received']}",
        f"- total_new_rows_added: {summary['total_new_rows_added']}",
        f"- latest_raw_5min_date_min: {summary['latest_raw_5min_date_min']}",
        f"- latest_raw_5min_date_max: {summary['latest_raw_5min_date_max']}",
        f"- latest_130min_date_min: {summary['latest_130min_date_min']}",
        f"- latest_130min_date_max: {summary['latest_130min_date_max']}",
        "- official_state_mutated: false",
        "",
    ]
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else "# ChatGPT Master Report\n"
    path.write_text(_replace_markdown_section(text, marker, section), encoding="utf-8")


def _pending_5min_refresh_summary(report):
    if report is None or report.empty:
        return {
            "status": "NO_PENDING",
            "pending_symbols_count": 0,
            "provider_requested_count": 0,
            "symbols_refreshed_count": 0,
            "total_provider_rows_received": 0,
            "total_new_rows_added": 0,
            "latest_raw_5min_date_min": "none",
            "latest_raw_5min_date_max": "none",
            "latest_130min_date_min": "none",
            "latest_130min_date_max": "none",
            "official_state_mutated": False,
        }
    errors = report["error"].fillna("").astype(str).str.strip().ne("")
    warnings = report["warning"].fillna("").astype(str).str.strip().ne("")
    raw_dates = pd.to_datetime(report["new_latest_timestamp"], errors="coerce", utc=True).dropna()
    bar_dates = pd.to_datetime(report["new_130min_latest_timestamp"], errors="coerce", utc=True).dropna()
    return {
        "status": "ERROR" if bool(errors.any()) else ("WARNING" if bool(warnings.any()) else "OK"),
        "pending_symbols_count": int(len(report)),
        "provider_requested_count": int(report["provider_requested"].astype(bool).sum()),
        "symbols_refreshed_count": int(report["raw_5min_refresh_status"].astype(str).isin(["merged", "no_new_rows"]).sum()),
        "total_provider_rows_received": int(pd.to_numeric(report["provider_rows_received"], errors="coerce").fillna(0).sum()),
        "total_new_rows_added": int(pd.to_numeric(report["new_rows_added"], errors="coerce").fillna(0).sum()),
        "latest_raw_5min_date_min": str(raw_dates.min().date()) if not raw_dates.empty else "none",
        "latest_raw_5min_date_max": str(raw_dates.max().date()) if not raw_dates.empty else "none",
        "latest_130min_date_min": str(bar_dates.min().date()) if not bar_dates.empty else "none",
        "latest_130min_date_max": str(bar_dates.max().date()) if not bar_dates.empty else "none",
        "official_state_mutated": False,
    }


def _write_pending_5min_refresh_report(report, summary, csv_path, markdown_path):
    report = ensure_columns(report, PENDING_5MIN_REFRESH_COLUMNS, fill_value="")
    report.to_csv(csv_path, index=False)
    lines = [
        "# Pending 5min Refresh Report",
        "",
        "Refreshes only ACCEPT + PENDING_NEXT_OPEN paper symbols. Official paper state is not mutated.",
        "",
        f"- status: {summary['status']}",
        f"- pending_symbols_count: {summary['pending_symbols_count']}",
        f"- provider_requested_count: {summary['provider_requested_count']}",
        f"- symbols_refreshed_count: {summary['symbols_refreshed_count']}",
        f"- total_provider_rows_received: {summary['total_provider_rows_received']}",
        f"- total_new_rows_added: {summary['total_new_rows_added']}",
        f"- latest_raw_5min_date_min: {summary['latest_raw_5min_date_min']}",
        f"- latest_raw_5min_date_max: {summary['latest_raw_5min_date_max']}",
        f"- latest_130min_date_min: {summary['latest_130min_date_min']}",
        f"- latest_130min_date_max: {summary['latest_130min_date_max']}",
        f"- official_state_mutated: {str(summary['official_state_mutated']).lower()}",
        "",
    ]
    if summary['pending_symbols_count'] == 0:
        lines.append("No pending accepted next-open orders.")
        lines.append("")
    else:
        lines.append(_readable_table(report))
        lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def run_pending_5min_refresh(data_folder="hisseler", report_output_dir="reports", make_chatgpt_report=False):
    latest_dir, _run_dir = _report_output_paths(report_output_dir, data_folder)
    pending = _pending_5min_refresh_orders(latest_dir)
    raw_dir = Path(data_folder) / "raw_5min"
    bar_dir = Path(data_folder) / "bars_130min"
    rows = []
    if pending is None or pending.empty:
        report = ensure_columns(pd.DataFrame(), PENDING_5MIN_REFRESH_COLUMNS, fill_value="")
        summary = _pending_5min_refresh_summary(report)
        _write_pending_5min_refresh_report(
            report,
            summary,
            latest_dir / "pending_5min_refresh_report.csv",
            latest_dir / "pending_5min_refresh_report.md",
        )
        if make_chatgpt_report:
            _write_pending_5min_master_section(latest_dir, summary)
        return summary, report

    for symbol, symbol_pending in pending.groupby(pending["symbol"].astype(str).str.upper()):
        raw_path = raw_dir / f"{symbol}_5min.csv"
        bar_path = bar_dir / f"{symbol}_130min.csv"
        existing_raw = _read_ohlcv_file(raw_path)
        existing_bar = _read_ohlcv_file(bar_path)
        old_latest = _latest_timestamp_string(existing_raw)
        old_bar_latest = _latest_timestamp_string(existing_bar)
        provider_rows = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        merged = existing_raw.copy()
        new_rows_added = 0
        raw_status = "not_requested"
        rebuilt_130min = False
        warning = ""
        error = ""
        provider_requested = True
        try:
            start, end = _refresh_fetch_window(existing_raw, symbol_pending)
            provider_rows = _fetch_pending_raw_5min(symbol, start, end)
            provider_count = int(len(provider_rows))
            merged = _merge_ohlcv_frames(existing_raw, provider_rows)
            if len(merged) < len(existing_raw):
                raise RuntimeError("merged_raw_5min_row_count_lower_than_existing")
            old_dates = pd.to_datetime(existing_raw.get("Date", pd.Series(dtype=str)), errors="coerce", utc=True).dropna()
            new_dates = pd.to_datetime(merged.get("Date", pd.Series(dtype=str)), errors="coerce", utc=True).dropna()
            old_set = set(old_dates.astype("int64").tolist()) if not old_dates.empty else set()
            new_rows_added = int(sum(item not in old_set for item in new_dates.astype("int64").tolist()))
            if provider_count == 0:
                warning = "provider_returned_no_rows"
                raw_status = "no_new_rows"
            elif new_rows_added == 0:
                warning = "provider_rows_already_present"
                raw_status = "no_new_rows"
            else:
                raw_status = "merged"
            if merged.empty:
                raise RuntimeError("merged_raw_5min_empty")
            _atomic_write_ohlcv(raw_path, merged)
            rebuilt, rebuild_warning = _complete_130min_from_raw(merged)
            if rebuild_warning:
                warning = ";".join([item for item in [warning, rebuild_warning] if item])
            if rebuilt.empty:
                raise RuntimeError("rebuilt_130min_empty")
            _atomic_write_ohlcv(bar_path, rebuilt)
            rebuilt_130min = True
        except Exception as exc:
            error = str(exc)
            provider_count = int(len(provider_rows)) if provider_rows is not None else 0
            raw_status = "error"
        final_raw = _read_ohlcv_file(raw_path)
        final_bar = _read_ohlcv_file(bar_path)
        rows.append(
            {
                "symbol": symbol,
                "pending_order_count": int(len(symbol_pending)),
                "raw_5min_file_exists": bool(raw_path.exists()),
                "old_latest_timestamp": old_latest,
                "provider_requested": provider_requested,
                "provider_rows_received": provider_count,
                "new_rows_added": new_rows_added,
                "new_latest_timestamp": _latest_timestamp_string(final_raw),
                "raw_5min_refresh_status": raw_status,
                "rebuilt_130min": rebuilt_130min,
                "old_130min_latest_timestamp": old_bar_latest,
                "new_130min_latest_timestamp": _latest_timestamp_string(final_bar),
                "warning": warning,
                "error": error,
            }
        )
    report = ensure_columns(pd.DataFrame(rows), PENDING_5MIN_REFRESH_COLUMNS, fill_value="")
    summary = _pending_5min_refresh_summary(report)
    _write_pending_5min_refresh_report(
        report,
        summary,
        latest_dir / "pending_5min_refresh_report.csv",
        latest_dir / "pending_5min_refresh_report.md",
    )
    if make_chatgpt_report:
        _write_pending_5min_master_section(latest_dir, summary)
    print(f"Pending 5min refresh report: {latest_dir / 'pending_5min_refresh_report.md'}")
    print(f"Official state mutated: {str(summary['official_state_mutated']).lower()}")
    print("submit_order_called: false")
    print("POST: false")
    return summary, report


def _write_raw_5min_update_audit_report(data_folder, latest_dir, pending_orders):
    data_path = Path(data_folder)
    testdata_root = data_path.parent
    project_root = testdata_root.parent
    command = _daily_pipeline_update_command(project_root)
    manifest_path = data_path / "manifest" / "download_manifest.csv"
    manifest = pd.DataFrame()
    if manifest_path.exists():
        try:
            manifest = pd.read_csv(manifest_path)
        except Exception:
            manifest = pd.DataFrame()
    pending_symbols = sorted(
        {
            str(symbol).strip().upper()
            for symbol in pending_orders.get("symbol", pd.Series(dtype=str)).tolist()
            if str(symbol).strip()
        }
    )
    manifest_symbols = (
        set(manifest.get("symbol", pd.Series(dtype=str)).astype(str).str.upper())
        if not manifest.empty and "symbol" in manifest
        else set()
    )
    provider_source = _command_value(command, "--feed") or "unknown"
    same_day_requested = "--end today" in command.lower()
    raw_update_called = bool(command)
    files_written_count = 0
    if not manifest.empty and "raw_5min_status" in manifest:
        files_written_count = int(manifest["raw_5min_status"].astype(str).str.lower().eq("downloaded").sum())
    refresh_report = pd.DataFrame()
    refresh_report_path = Path(latest_dir) / "pending_5min_refresh_report.csv"
    if refresh_report_path.exists():
        try:
            refresh_report = pd.read_csv(refresh_report_path)
        except Exception:
            refresh_report = pd.DataFrame()
    same_day_rows_received_count = 0
    rows = []
    for symbol in pending_symbols:
        raw_path = data_path / "raw_5min" / f"{symbol}_5min.csv"
        latest_date = _raw_5min_latest_date(raw_path)

        symbol_orders = pending_orders[pending_orders.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(symbol)]
        target_date_str = None
        if not symbol_orders.empty:
            target_date_val = _paper_fill_target_date(symbol_orders.iloc[0])
            if target_date_val is not None:
                target_date_str = str(target_date_val)

        same_day_rows = _raw_5min_same_day_row_count(raw_path, target_date=target_date_str)
        same_day_rows_received_count += same_day_rows
        reason = ""
        if target_date_str and latest_date == target_date_str:
            reason = ""
        elif not raw_path.exists():
            reason = "missing_raw_5min_file"
        elif not refresh_report.empty and "symbol" in refresh_report:
            refresh_match = refresh_report[
                refresh_report["symbol"].astype(str).str.upper().eq(symbol)
            ]
            if not refresh_match.empty:
                refresh_row = refresh_match.iloc[0]
                refresh_error = str(refresh_row.get("error", "")).strip()
                refresh_warning = str(refresh_row.get("warning", "")).strip()
                refresh_status = str(refresh_row.get("raw_5min_refresh_status", "")).strip()
                if refresh_error:
                    reason = f"pending_refresh_error:{refresh_error}"
                elif refresh_warning:
                    reason = f"pending_refresh_warning:{refresh_warning}"
                else:
                    reason = f"pending_refresh_status:{refresh_status}"
            else:
                reason = "pending_refresh_missing_symbol"
        elif not manifest.empty:
            match = manifest[manifest.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(symbol)]
            status = str(match.iloc[0].get("raw_5min_status", "")).lower() if not match.empty else ""
            error = str(match.iloc[0].get("error", "")).strip() if not match.empty else ""
            if status == "skipped":
                reason = "existing_raw_5min_file_skipped_without_overwrite"
            elif error:
                reason = f"downloader_error:{error}"
            else:
                reason = "same_day_rows_not_present_in_raw_5min_file"
        else:
            reason = "download_manifest_missing"
        rows.append(
            {
                "raw_5min_update_called": raw_update_called,
                "provider_source_used": provider_source,
                "requested_symbols_count": int(len(manifest)) if not manifest.empty else 0,
                "pending_symbols_requested": bool(set(pending_symbols).issubset(manifest_symbols)) if pending_symbols else False,
                "pending_symbol": symbol,
                "latest_raw_5min_date_by_pending_symbol": latest_date,
                "same_day_requested": same_day_requested,
                "same_day_rows_received_count": same_day_rows,
                "files_written_count": files_written_count,
                "reason_if_no_target_date_data": reason,
            }
        )
    audit = ensure_columns(pd.DataFrame(rows), RAW_5MIN_UPDATE_AUDIT_COLUMNS, fill_value="")
    csv_path = Path(latest_dir) / "raw_5min_update_audit_report.csv"
    md_path = Path(latest_dir) / "raw_5min_update_audit_report.md"
    audit.to_csv(csv_path, index=False)
    summary = {
        "raw_5min_update_called": raw_update_called,
        "provider_source_used": provider_source,
        "requested_symbols_count": int(len(manifest)) if not manifest.empty else 0,
        "pending_symbols_requested": bool(set(pending_symbols).issubset(manifest_symbols)) if pending_symbols else False,
        "same_day_requested": same_day_requested,
        "same_day_rows_received_count": same_day_rows_received_count,
        "files_written_count": files_written_count,
        "reason_if_no_target_date_data": "; ".join(sorted(set(audit["reason_if_no_target_date_data"].dropna().astype(str)))) if not audit.empty else "no_pending_symbols",
    }
    lines = [
        "# Raw 5min Update Audit Report",
        "",
        f"- raw_5min_update_called: {str(summary['raw_5min_update_called']).lower()}",
        f"- provider/source used: {summary['provider_source_used']}",
        f"- requested_symbols_count: {summary['requested_symbols_count']}",
        f"- pending_symbols_requested: {str(summary['pending_symbols_requested']).lower()}",
        f"- same_day_requested: {str(summary['same_day_requested']).lower()}",
        f"- same_day_rows_received_count: {summary['same_day_rows_received_count']}",
        f"- files_written_count: {summary['files_written_count']}",
        f"- reason_if_no_target_date_data: {summary['reason_if_no_target_date_data']}",
        "",
        _readable_table(audit),
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return summary, audit


def run_paper_5min_fill_check(data_folder="hisseler", report_output_dir="reports", make_chatgpt_report=False):
    latest_dir, _run_dir = _report_output_paths(report_output_dir, data_folder)
    orders = _pending_5min_diagnostic_orders(latest_dir)
    trade_log = read_trade_log(latest_dir)
    raw_dir = _raw_5min_dir(data_folder)
    rows = []
    fill_log_rows = []
    filled_order_indices = []
    for _, order in orders.iterrows():
        symbol = str(order.get("symbol", "")).strip().upper()
        signal_date = order.get("signal_date", "")
        status_value = order.get("fill_status", "") if "fill_status" in order else ""
        if not str(status_value).strip():
            status_value = order.get("status", "")
        target_date = _paper_fill_target_date(order)
        raw_path = raw_dir / f"{symbol}_5min.csv"
        warning = ""
        raw_latest_timestamp = ""
        raw_latest_date = ""
        fill_timestamp = None
        fill_price = pd.NA
        if not raw_path.exists():
            warning = "missing_raw_5min_file"
        else:
            try:
                raw_frame = _normalize_frame(raw_path)
                if raw_frame.empty:
                    warning = "empty_raw_5min_file"
                else:
                    raw_latest_timestamp = str(raw_frame.index.max())
                    raw_latest_date = str(raw_frame.index.max().date())
                    fill_timestamp, fill_price = _first_rth_5min_fill(raw_frame, signal_date, target_date)
                    if fill_timestamp is None:
                        warning = _paper_5min_missing_warning(raw_latest_date, target_date)
            except Exception as error:
                warning = f"raw_5min_read_error:{error}"
        has_next_open = fill_timestamp is not None and pd.notna(fill_price)
        if has_next_open:
            filled_order_indices.append(order.name)
            shares = _paper_fill_shares_for_order(order, fill_price, trade_log)
            fill_log_rows.append(
                {
                    "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "strategy_name": order.get("strategy_name", ""),
                    "symbol": symbol,
                    "action": order.get("action", ""),
                    "signal_date": signal_date,
                    "signal_price": pd.NA,
                    "assumed_fill_price": fill_price,
                    "fill_status": "PAPER_FILLED",
                    "shares": shares,
                    "notional": order.get("paper_position_notional", pd.NA),
                    "paper_capital": order.get("paper_capital", pd.NA),
                    "reason": order.get("reason", ""),
                    "liquidity_guard_pass": order.get("liquidity_guard_pass", pd.NA),
                    "notes": ";".join(
                        item
                        for item in [
                            _clean_note(order.get("notes", "")),
                            f"fill_bar_timestamp={fill_timestamp}",
                            "fill_price_source=next_5min_open",
                        ]
                        if item
                    ),
                }
            )
        rows.append(
            {
                "symbol": symbol,
                "strategy_name": order.get("strategy_name", ""),
                "signal_date": signal_date,
                "pending_status": status_value,
                "raw_5min_latest_timestamp": raw_latest_timestamp,
                "raw_5min_latest_date": raw_latest_date,
                "has_next_open_5min": bool(has_next_open),
                "hypothetical_fill_timestamp": str(fill_timestamp) if fill_timestamp is not None else "",
                "hypothetical_fill_price": fill_price,
                "warning": warning,
                "would_fill_if_5min_enabled": bool(has_next_open),
            }
        )
    check = ensure_columns(pd.DataFrame(rows), PAPER_5MIN_FILL_CHECK_COLUMNS, fill_value="")
    summary = _paper_5min_fill_check_summary(check)
    if fill_log_rows:
        append_trade_log(latest_dir, fill_log_rows)
        pending = read_pending_orders(latest_dir)
        if not pending.empty:
            pending = pending.drop(index=[idx for idx in filled_order_indices if idx in pending.index])
            write_pending_orders(latest_dir, pending)
        summary["official_state_mutated"] = True
    _write_paper_5min_fill_check_report(
        check,
        summary,
        latest_dir / "paper_5min_fill_check_report.csv",
        latest_dir / "paper_5min_fill_check_report.md",
    )
    if make_chatgpt_report:
        _write_paper_5min_master_section(latest_dir, summary)
    _write_raw_5min_update_audit_report(data_folder, latest_dir, orders)
    print(f"Paper 5min fill check report: {latest_dir / 'paper_5min_fill_check_report.md'}")
    print(f"Official state mutated: {str(summary['official_state_mutated']).lower()}")
    print("submit_order_called: false")
    print("POST: false")
    return summary, check


def _remove_optional_report_files(*directories):
    optional_names = [
        "yearly_performance_report.csv",
        "yearly_performance_report.md",
        "rs_robustness_report.csv",
        "rs_robustness_report.md",
        "candidate_summary_report.md",
    ]
    for directory in directories:
        for name in optional_names:
            path = directory / name
            if path.exists():
                path.unlink()


def _remove_stale_performance_profile(directory):
    for name in ["performance_profile_report.csv", "performance_profile_report.md"]:
        path = directory / name
        if path.exists():
            path.unlink()


def _resolve_data_dirs(data_folder, intraday_data_dir=None, daily_data_dir=None, raw_1min_data_dir=None):
    root = Path(data_folder)
    intraday_dir = Path(intraday_data_dir) if intraday_data_dir else root / "bars_130min"
    daily_dir = Path(daily_data_dir) if daily_data_dir else root / "daily"
    raw_dir = Path(raw_1min_data_dir) if raw_1min_data_dir else root / "raw_1min"
    return intraday_dir, daily_dir, raw_dir


def _symbol_from_file(path, suffix):
    return path.name[: -len(suffix)].upper()


def _normalize_frame(path):
    frame = read_ohlcv_csv_fast(path)
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame


def _normalize_frame_timed(path, timing=None):
    frame = read_ohlcv_csv_fast(path, timing=timing)
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame


def _slice_frame(frame, start_date=None, end_date=None):
    if start_date:
        start = pd.Timestamp(start_date, tz=frame.index.tz)
        frame = frame.loc[frame.index >= start]
    if end_date:
        end = pd.Timestamp(end_date, tz=frame.index.tz)
        frame = frame.loc[frame.index <= end]
    return frame


def _add_donchian_precomputed_features(daily):
    if daily is None or daily.empty:
        return daily
    frame = daily.copy()
    previous_close = frame["Close"].shift(1)
    true_range = pd.concat(
        [
            frame["High"] - frame["Low"],
            (frame["High"] - previous_close).abs(),
            (frame["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(20, min_periods=20).mean()
    frame["ATR_Pct_20"] = 100 * atr / frame["Close"].replace(0, float("nan"))
    for length in (100, 200):
        frame[f"Donchian_Trend_SMA_{length}"] = frame["Close"].rolling(
            length,
            min_periods=length,
        ).mean()
    for length in (20, 55):
        frame[f"Donchian_Entry_High_{length}"] = frame["High"].rolling(
            length,
            min_periods=length,
        ).max().shift(1)
    for length in (10, 20):
        frame[f"Donchian_Exit_Low_{length}"] = frame["Low"].rolling(
            length,
            min_periods=length,
        ).min().shift(1)
    return frame


def _audit_one_symbol(symbol, daily_path, intraday_path):
    row = {
        "symbol": symbol,
        "has_daily_file": bool(daily_path and daily_path.exists()),
        "has_130min_file": bool(intraday_path and intraday_path.exists()),
        "daily_rows": 0,
        "bar_130min_rows": 0,
        "daily_start": "",
        "daily_end": "",
        "intraday_start": "",
        "intraday_end": "",
        "duplicate_daily_timestamps": 0,
        "duplicate_130min_timestamps": 0,
        "missing_ohlcv_count": 0,
        "invalid_ohlc_count": 0,
        "zero_volume_count": 0,
        "expected_130min_bar_count_check": False,
        "daily_data_valid": False,
        "data_warnings": "",
        "errors": "",
    }
    errors = []
    for label, path in [("daily", daily_path), ("130min", intraday_path)]:
        if not path or not path.exists():
            errors.append(f"missing_{label}_file")
            continue
        try:
            frame = read_ohlcv_csv_fast(path)
            dupes = int(frame.index.duplicated().sum())
            missing = int(frame[["Open", "High", "Low", "Close", "Volume"]].isna().sum().sum())
            invalid = int(((frame["High"] < frame[["Open", "Close", "Low"]].max(axis=1)) | (frame["Low"] > frame[["Open", "Close", "High"]].min(axis=1))).sum())
            zero_volume = int((frame["Volume"] <= 0).sum())
            row["missing_ohlcv_count"] += missing
            row["invalid_ohlc_count"] += invalid
            row["zero_volume_count"] += zero_volume
            if label == "daily":
                row["daily_rows"] = int(len(frame))
                row["daily_start"] = str(frame.index.min()) if len(frame) else ""
                row["daily_end"] = str(frame.index.max()) if len(frame) else ""
                row["duplicate_daily_timestamps"] = dupes
                row["daily_data_valid"] = bool(len(frame) > 0 and missing == 0 and invalid == 0)
                if len(frame) == 0:
                    errors.append("empty_daily_file")
            else:
                local = frame.index.tz_convert("America/New_York")
                minutes = local.hour * 60 + local.minute
                expected_minutes = [570, 700, 830]
                per_day_ok = bool(pd.Series(local.date).value_counts().le(3).all())
                starts_ok = bool(
                    all(
                        any(abs(int(minute) - expected) <= 10 for expected in expected_minutes)
                        for minute in minutes.tolist()
                    )
                )
                rth_ok = bool(((minutes >= 570) & (minutes < 960)).all())
                row["bar_130min_rows"] = int(len(frame))
                row["intraday_start"] = str(frame.index.min()) if len(frame) else ""
                row["intraday_end"] = str(frame.index.max()) if len(frame) else ""
                row["duplicate_130min_timestamps"] = dupes
                row["expected_130min_bar_count_check"] = per_day_ok and starts_ok and rth_ok
                if not row["expected_130min_bar_count_check"]:
                    errors.append("invalid_130min_schedule")
        except Exception as error:
            errors.append(f"{label}:{error}")
    warnings = []
    if row["has_daily_file"] and row["daily_rows"] == 0:
        warnings.append("empty_daily_file")
    if row["zero_volume_count"] > 0:
        warnings.append("zero_volume")
    row["data_warnings"] = ";".join(dict.fromkeys(warnings))
    row["errors"] = ";".join(errors)
    return row


def _write_data_audit(symbols, daily_dir, intraday_dir, latest_dir, run_dir):
    rows = []
    audit_symbols = sorted(set(symbols) | {"QQQ", "SPY"})
    for symbol in audit_symbols:
        rows.append(
            _audit_one_symbol(
                symbol,
                daily_dir / f"{symbol}_1D.csv",
                intraday_dir / f"{symbol}_130min.csv",
            )
        )
    audit = pd.DataFrame(rows)
    daily_ends = pd.to_datetime(audit["daily_end"], errors="coerce")
    global_daily_end = daily_ends.max()
    daily_rows = pd.to_numeric(audit["daily_rows"], errors="coerce").fillna(0)
    empty_daily = audit.loc[audit["has_daily_file"] & (daily_rows <= 0), "symbol"].tolist()
    short_history = audit.loc[(daily_rows > 0) & (daily_rows < 252), "symbol"].tolist()
    ending_before_global = audit.loc[
        daily_ends.notna() & pd.notna(global_daily_end) & (daily_ends < global_daily_end),
        "symbol",
    ].tolist()
    zero_volume = audit.loc[
        pd.to_numeric(audit["zero_volume_count"], errors="coerce").fillna(0) > 0,
        "symbol",
    ].tolist()
    for directory in [latest_dir, run_dir]:
        csv_path = directory / "data_audit_report.csv"
        md_path = directory / "data_audit_report.md"
        audit.to_csv(csv_path, index=False)
        try:
            table = audit.to_markdown(index=False)
        except ImportError:
            table = audit.to_string(index=False)
        summary = [
            "# Data Audit Report",
            "",
            f"- Symbols: {len(audit)}",
            f"- Daily files: {int(audit['has_daily_file'].sum()) if not audit.empty else 0}",
            f"- Valid daily files: {int(audit['daily_data_valid'].sum()) if not audit.empty else 0}",
            f"- 130min files: {int(audit['has_130min_file'].sum()) if not audit.empty else 0}",
            f"- 130min schedule valid rows: {int(audit['expected_130min_bar_count_check'].sum()) if not audit.empty else 0}",
            f"- Empty daily files: {len(empty_daily)} ({','.join(empty_daily) if empty_daily else 'none'})",
            f"- Short-history symbols: {len(short_history)} ({','.join(short_history) if short_history else 'none'})",
            f"- Symbols ending before global end date: {len(ending_before_global)} ({','.join(ending_before_global) if ending_before_global else 'none'})",
            f"- Zero volume warnings: {len(zero_volume)} ({','.join(zero_volume) if zero_volume else 'none'})",
            f"- QQQ daily file: {bool(audit.loc[audit['symbol'] == 'QQQ', 'has_daily_file'].any())}",
            f"- QQQ 130min file: {bool(audit.loc[audit['symbol'] == 'QQQ', 'has_130min_file'].any())}",
            f"- SPY daily file: {bool(audit.loc[audit['symbol'] == 'SPY', 'has_daily_file'].any())}",
            f"- SPY 130min file: {bool(audit.loc[audit['symbol'] == 'SPY', 'has_130min_file'].any())}",
            "",
            table,
            "",
        ]
        md_path.write_text("\n".join(summary), encoding="utf-8")
    return audit


def _prepared_cache_key(data_folder, intraday_dir, daily_dir, intraday_timeframe, daily_timeframe, start_date, end_date, symbols, legacy_intraday=False):
    key = {
        "cache_version": PREPARED_CACHE_VERSION,
        "data_folder": str(Path(data_folder).resolve()),
        "intraday_data_dir": str(Path(intraday_dir).resolve()),
        "daily_data_dir": str(Path(daily_dir).resolve()),
        "intraday_timeframe": intraday_timeframe,
        "daily_timeframe": daily_timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "symbols": list(symbols),
        "legacy_intraday": bool(legacy_intraday),
    }
    digest = hashlib.sha256(repr(sorted(key.items())).encode("utf-8")).hexdigest()[:24]
    return key, digest


def _prepared_cache_path(data_folder, digest):
    cache_dir = Path(data_folder) / ".cache" / "prepared_features"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"prepared_features_{digest}.pkl"


def _load_or_build_prepared_cache(
    data_folder,
    intraday_dir,
    daily_dir,
    intraday_timeframe,
    daily_timeframe,
    start_date,
    end_date,
    symbols,
    force_rebuild=False,
    timing=None,
    legacy_intraday=False,
):
    intraday_dir = Path(intraday_dir)
    daily_dir = Path(daily_dir)
    cache_key, digest = _prepared_cache_key(
        data_folder,
        intraday_dir,
        daily_dir,
        intraday_timeframe,
        daily_timeframe,
        start_date,
        end_date,
        symbols,
        legacy_intraday=legacy_intraday,
    )
    cache_path = _prepared_cache_path(data_folder, digest)
    if cache_path.exists() and not force_rebuild:
        cache_mtime = cache_path.stat().st_mtime
        for symbol in symbols:
            d_file = Path(daily_dir) / f"{symbol}_1D.csv"
            if d_file.exists() and d_file.stat().st_mtime > cache_mtime:
                force_rebuild = True
                break
            i_file = Path(intraday_dir) / f"{symbol}_130min.csv"
            if i_file.exists() and i_file.stat().st_mtime > cache_mtime:
                force_rebuild = True
                break
    if cache_path.exists() and not force_rebuild:
        load_started = _now()
        payload = pd.read_pickle(cache_path)
        load_sec = _now() - load_started
        if payload.get("cache_key") == cache_key:
            prepared = payload["prepared"]
            cache_summary = dict(prepared.get("cache_summary", {}))
            cache_summary.update(
                {
                    "prepared_disk_cache_hits": 1,
                    "prepared_disk_cache_misses": 0,
                    "prepared_cache_load_sec": round(load_sec, 4),
                    "prepared_cache_build_sec": 0.0,
                    "csv_read_sec": 0.0,
                    "rows_loaded": int(
                        sum(len(frame) for frame in prepared.get("asset_data", {}).values())
                    ),
                    "symbols_loaded": int(len(prepared.get("asset_data", {}))),
                }
            )
            prepared["cache_summary"] = cache_summary
            return prepared, cache_path, False
    asset_data = {}
    build_started = _now()
    csv_timing = _TimingCollector() if timing is None else timing
    for symbol in symbols:
        daily_path = daily_dir / f"{symbol}_1D.csv"
        if not daily_path.exists():
            continue
        daily = _slice_frame(_normalize_frame_timed(daily_path, csv_timing), start_date, end_date)
        if daily.empty:
            continue
        daily = _add_donchian_precomputed_features(daily)
        daily.attrs["is_daily"] = True
        daily.attrs["daily_data_dir"] = str(daily_dir)
        daily.attrs["daily_timeframe"] = daily_timeframe

        intraday_path = intraday_dir / f"{symbol}_130min.csv"
        if legacy_intraday and intraday_path.exists():
            intraday = _slice_frame(_normalize_frame_timed(intraday_path, csv_timing), start_date, end_date)
            if not intraday.empty:
                intraday.attrs["daily_ohlcv"] = daily
                intraday.attrs["intraday_data_dir"] = str(intraday_dir)
                intraday.attrs["daily_data_dir"] = str(daily_dir)
                intraday.attrs["intraday_timeframe"] = intraday_timeframe
                intraday.attrs["daily_timeframe"] = daily_timeframe
                asset_data[symbol] = intraday
                continue

        asset_data[symbol] = daily
    if not asset_data:
        raise ValueError("Prepared cache build failed: usable 130min/daily symbol data not found.")
    csv_read_sec = sum(
        float(item.get("seconds", 0.0) or 0.0)
        for item in csv_timing.rows
        if item.get("stage") == "CSV read fast"
    )
    rows_loaded = int(sum(len(frame) for frame in asset_data.values()))
    prepared = {
        "asset_data": asset_data,
        "benchmark_data": (
            asset_data["QQQ"] if "QQQ" in asset_data
            else asset_data["SPY"] if "SPY" in asset_data
            else _load_daily_benchmark(daily_dir, start_date, end_date)
        ),
        "cache_summary": {
            "prepared_disk_cache_hits": 0,
            "prepared_disk_cache_misses": 1,
            "feature_cache_misses": len(asset_data),
            "prepared_cache_load_sec": 0.0,
            "prepared_cache_build_sec": round(_now() - build_started, 4),
            "csv_read_sec": round(csv_read_sec, 4),
            "rows_loaded": rows_loaded,
            "symbols_loaded": len(asset_data),
            "intraday_data_dir": str(intraday_dir),
            "daily_data_dir": str(daily_dir),
        },
    }
    pd.to_pickle({"cache_key": cache_key, "prepared": prepared}, cache_path)
    return prepared, cache_path, True


def _load_daily_benchmark(daily_dir, start_date=None, end_date=None):
    for folder in [Path(daily_dir), Path(daily_dir).parent / "benchmarks" / "daily"]:
        for symbol in ["QQQ", "SPY"]:
            path = folder / f"{symbol}_1D.csv"
            if not path.exists():
                continue
            frame = _slice_frame(_normalize_frame(path), start_date, end_date)
            if frame.empty:
                continue
            frame.attrs["benchmark_symbol"] = symbol
            frame.attrs["daily_data_dir"] = str(daily_dir)
            return frame
    return None


def _equal_weight_missing_symbols(asset_data, start, end):
    skipped_symbols = []
    for symbol, data in asset_data.items():
        if "Close" not in data:
            skipped_symbols.append(symbol)
            continue
        close = pd.to_numeric(data["Close"], errors="coerce").dropna().sort_index()
        start_candidates = close.loc[:start]
        if start_candidates.empty:
            skipped_symbols.append(symbol)
            continue
        start_price = float(start_candidates.iloc[-1])
        if not pd.notna(start_price) or start_price <= 0:
            skipped_symbols.append(symbol)
    return sorted(str(symbol) for symbol in skipped_symbols)


def _default_params(experiment, data_folder, run_profile, quick_mode):
    params = {
        "strategy_name": experiment.get("strategy_name"),
        "run_profile": run_profile,
        "quick_mode": quick_mode,
        "execution_mode": "bar_close",
        "ranking_mode": "momentum",
        "hurst_filter_mode": "off",
        "hurst_threshold": 0.5,
        "rsi_length": 3,
        "rsi_entry_threshold": 10,
        "bollinger_length": 20,
        "bollinger_std": 2.0,
        "trend_sma_length": 50,
        "setup_lookback": 3,
        "exit_mode": "sma_or_target",
        "exit_sma_length": 20,
        "take_profit_atr_multiplier": 2.0,
        "disaster_stop_atr_multiplier": 2.0,
        "time_stop_bars": 10,
        "data_folder": data_folder,
        "benchmark_symbol": "QQQ",
        "mast_slope_sma_days": 100,
        "mast_slope_lookback_days": 20,
        "mast_top_n": 20,
        "mast_rebalance_frequency": "monthly",
        "mast_score_lookback_days": 63,
        "mast_use_market_regime": True,
        "mast_market_regime_sma_days": 200,
        "mast_min_price": 10,
        "mast_max_atr_pct": 12,
    }
    params.update(experiment)
    params.pop("name", None)
    return params


def _run_strategy(asset_data, benchmark_data, params):
    strategy_name = params["strategy_name"]
    if strategy_name == "donchian_breakout":
        strategy_params = _normalize_donchian_breakout_params(params)
        donchian_profiler = {
            "rolling_high_low_sec": 0.0,
            "signal_generation_sec": 0.0,
            "ranking_sec": 0.0,
            "portfolio_sim_sec": 0.0,
            "exits_sec": 0.0,
            "diagnostics_sec": 0.0,
        }
        context = _prepare_donchian_context(
            asset_data,
            strategy_params,
            benchmark_data=benchmark_data,
            profiler=donchian_profiler,
        )
        final_data = _apply_donchian_breakout_top_signals(
            asset_data,
            strategy_params,
            benchmark_data=benchmark_data,
            context=context,
            profiler=donchian_profiler,
        )
        metrics = _run_donchian_breakout_backtest(
            asset_data,
            strategy_params,
            benchmark_data=benchmark_data,
            context=context,
            profiler=donchian_profiler,
        )
        diagnostics_started = _now()
        metrics.update(_asset_signal_diagnostics(final_data))
        donchian_profiler["diagnostics_sec"] += _now() - diagnostics_started
        metrics.update(
            {
                key: round(float(value), 4)
                for key, value in donchian_profiler.items()
            }
        )
        metrics["total_trades"] = int(metrics.get("Islem Sayisi", 0) or 0)
        metrics["Trade Count"] = metrics["total_trades"]
        return final_data, metrics
    signaled = {
        symbol: generate_strategy_signals(frame, params)
        for symbol, frame in asset_data.items()
    }
    if strategy_name == "relative_strength_rotation":
        strategy_params = _normalize_relative_strength_params(params)
        final_data = _apply_relative_strength_rotation_top_signals(
            signaled,
            strategy_params,
            benchmark_data=benchmark_data,
        )
        metrics = _run_relative_strength_rotation_backtest(
            final_data,
            strategy_params,
            benchmark_data=benchmark_data,
        )
    elif strategy_name == "dual_momentum":
        strategy_params = _normalize_dual_momentum_params(params)
        final_data = _apply_dual_momentum_top_signals(
            signaled,
            strategy_params,
            benchmark_data=benchmark_data,
        )
        metrics = _run_dual_momentum_backtest(
            final_data,
            strategy_params,
            benchmark_data=benchmark_data,
        )
    elif strategy_name == "ma_slope_trend":
        strategy_params = _normalize_ma_slope_trend_params(params)
        final_data = _apply_ma_slope_trend_top_signals(
            signaled,
            strategy_params,
            benchmark_data=benchmark_data,
        )
        metrics = _run_ma_slope_trend_backtest(
            final_data,
            strategy_params,
            benchmark_data=benchmark_data,
        )
    elif strategy_name == "cross_sectional_momentum":
        strategy_params = _normalize_cross_sectional_momentum_params(params)
        final_data = _apply_cross_sectional_momentum_top_signals(
            signaled,
            strategy_params,
            benchmark_data=benchmark_data,
        )
        metrics = _run_cross_sectional_momentum_backtest(
            final_data,
            strategy_params,
            benchmark_data=benchmark_data,
        )
    else:
        raise NotImplementedError(f"Unsupported modular strategy: {strategy_name}")
    metrics.update(_asset_signal_diagnostics(final_data))
    metrics["total_trades"] = int(metrics.get("Islem Sayisi", 0) or 0)
    metrics["Trade Count"] = metrics["total_trades"]
    return final_data, metrics


def _add_benchmarks(asset_data, benchmark_data, metrics, benchmark_asset_data=None):
    equity_curve = metrics.get("_equity_curve", [])
    if not equity_curve:
        return
    start = equity_curve[0][0]
    end = equity_curve[-1][0]
    qqq_start = start
    qqq_end = end
    if benchmark_data is not None and not benchmark_data.empty:
        benchmark_index = benchmark_data.index.sort_values()
        if benchmark_index.tz is not None and getattr(start, "tzinfo", None) is None:
            start = pd.Timestamp(start).tz_localize(benchmark_index.tz)
            end = pd.Timestamp(end).tz_localize(benchmark_index.tz)
            qqq_start = start
            qqq_end = end
        if benchmark_index.tz is not None and getattr(qqq_start, "tzinfo", None) is None:
            qqq_start = pd.Timestamp(qqq_start).tz_localize(benchmark_index.tz)
            qqq_end = pd.Timestamp(qqq_end).tz_localize(benchmark_index.tz)
        qqq_start = max(pd.Timestamp(qqq_start), benchmark_index[0])
        qqq_end = max(qqq_start, min(pd.Timestamp(qqq_end), benchmark_index[-1]))
    try:
        if benchmark_data is None or benchmark_data.empty or "Close" not in benchmark_data:
            raise ValueError("Benchmark close serisi yok.")
        qqq_metrics = calculate_buyhold_metrics(benchmark_data["Close"], qqq_start, qqq_end)
    except Exception as error:
        print(f"Warning: compact benchmark unavailable, writing NaN metrics: {error}")
        qqq_metrics = {"Return (%)": pd.NA, "CAGR (%)": pd.NA, "MaxDD (%)": pd.NA}
    ew_source = benchmark_asset_data if benchmark_asset_data is not None else asset_data
    ew_start = start
    ew_end = end
    ew_indexes = [
        data.index.sort_values()
        for data in ew_source.values()
        if "Close" in data and not data.empty
    ]
    if ew_indexes:
        ew_first = min(index[0] for index in ew_indexes)
        ew_last = max(index[-1] for index in ew_indexes)
        if ew_first.tz is not None and getattr(ew_start, "tzinfo", None) is None:
            ew_start = pd.Timestamp(ew_start).tz_localize(ew_first.tz)
            ew_end = pd.Timestamp(ew_end).tz_localize(ew_first.tz)
        ew_start = max(pd.Timestamp(ew_start), ew_first)
        ew_end = min(pd.Timestamp(ew_end), ew_last)
    ew_missing_symbols = _equal_weight_missing_symbols(ew_source, ew_start, ew_end)
    try:
        ew_metrics = calculate_equal_weight_benchmark(ew_source, ew_start, ew_end)
    except Exception as error:
        print(f"Warning: compact equal-weight benchmark unavailable, writing NaN metrics: {error}")
        ew_metrics = {"Return (%)": pd.NA, "CAGR (%)": pd.NA, "MaxDD (%)": pd.NA}
    metrics.update(_prefixed_benchmark_metrics("QQQ", qqq_metrics))
    metrics.update(_prefixed_benchmark_metrics("EqualWeight Universe", ew_metrics))
    qqq_return = pd.to_numeric(
        pd.Series([metrics["QQQ BuyHold Return (%)"]]), errors="coerce"
    ).iloc[0]
    ew_return = pd.to_numeric(
        pd.Series([metrics["EqualWeight Universe BuyHold Return (%)"]]), errors="coerce"
    ).iloc[0]
    metrics["Strategy Excess Return vs QQQ (%)"] = (
        round(float(metrics["Net Getiri (%)"]) - float(qqq_return), 2)
        if pd.notna(qqq_return)
        else pd.NA
    )
    metrics["Strategy Excess Return vs EqualWeight (%)"] = (
        round(float(metrics["Net Getiri (%)"]) - float(ew_return), 2)
        if pd.notna(ew_return)
        else pd.NA
    )
    metrics["EqualWeight Universe Return (%)"] = metrics[
        "EqualWeight Universe BuyHold Return (%)"
    ]
    metrics["EqualWeight Missing Symbols Count"] = len(ew_missing_symbols)
    metrics["EqualWeight Missing Symbols"] = ",".join(ew_missing_symbols)
    metrics["QQQ Benchmark Start"] = str(qqq_start)
    metrics["QQQ Benchmark End"] = str(qqq_end)
    metrics["EqualWeight Benchmark Start"] = str(ew_start)
    metrics["EqualWeight Benchmark End"] = str(ew_end)


def _report_row(experiment_name, params, metrics):
    row = {"Experiment Name": experiment_name}
    row.update(params)
    row.update(metrics)
    row["Strategy Name"] = params["strategy_name"]
    strategy_family = {
        "donchian_breakout": "trend_following / breakout",
        "ma_slope_trend": "trend_following / ma_slope",
    }.get(params["strategy_name"], "relative_strength / momentum")
    row["Strategy Family"] = strategy_family
    row["Survivorship Bias Warning"] = SURVIVORSHIP_BIAS_WARNING
    row["Dividend Adjustment Warning"] = DIVIDEND_ADJUSTMENT_WARNING
    row.pop("_trade_log", None)
    row.pop("_equity_curve", None)
    row.pop("_liquidity_skipped_entries", None)
    return row


def _init_experiment_worker(cache_path):
    global _WORKER_PREPARED
    payload = pd.read_pickle(cache_path)
    prepared = payload["prepared"]
    if prepared.get("benchmark_data") is None:
        summary = prepared.get("cache_summary", {})
        daily_dir = summary.get("daily_data_dir")
        if daily_dir:
            prepared["benchmark_data"] = _load_daily_benchmark(
                daily_dir,
                summary.get("start_date"),
                summary.get("end_date")
            )
    _WORKER_PREPARED = prepared


def _run_experiment_worker(task):
    experiment_name = task["experiment_name"]
    params = task["params"]
    include_raw = bool(task.get("include_raw", False))
    prepared = _WORKER_PREPARED
    if prepared is None:
        raise RuntimeError("Worker prepared cache is not loaded.")
    asset_data = prepared["asset_data"]
    benchmark_data = prepared.get("benchmark_data")
    period_start = task.get("period_start")
    period_end = task.get("period_end")
    if period_start or period_end:
        asset_data = _slice_asset_data_for_period(asset_data, period_start, period_end)
        benchmark_data = _slice_benchmark_for_period(benchmark_data, period_start, period_end)
    started = _now()
    final_data, metrics = _run_strategy(asset_data, benchmark_data, params)
    _add_benchmarks(
        final_data,
        benchmark_data,
        metrics,
        benchmark_asset_data=asset_data,
    )
    runtime_sec = _now() - started
    raw_metrics = metrics if include_raw else None
    return {
        "experiment_name": experiment_name,
        "row": _report_row(experiment_name, params, metrics),
        "raw": {"params": params, "metrics": raw_metrics} if include_raw else None,
        "runtime_sec": runtime_sec,
        "symbols_loaded": metrics.get("symbols_loaded", 0),
        "total_final_entry_signals": metrics.get("total_final_entry_signals", 0),
        "total_trades": metrics.get("total_trades", 0),
    }


def _resolve_worker_count(parallel_enabled, workers):
    if not parallel_enabled:
        return 1
    if workers in (None, "auto"):
        return max(1, (os.cpu_count() or 2) - 1)
    return max(1, int(workers))


def _build_experiment_tasks(
    experiments,
    data_folder,
    run_profile,
    quick_mode,
    intraday_dir,
    daily_dir,
    raw_dir,
    intraday_timeframe,
    daily_timeframe,
    cache_rebuilt,
    include_raw=False,
    min_dollar_volume=0.0,
):
    tasks = []
    for index, experiment in enumerate(experiments, 1):
        experiment_name = experiment.get("name", f"experiment_{index}")
        params = _default_params(experiment, data_folder, run_profile, quick_mode)
        params.update(
            {
                "intraday_data_dir": str(intraday_dir),
                "daily_data_dir": str(daily_dir),
                "raw_1min_data_dir": str(raw_dir),
                "intraday_timeframe": intraday_timeframe,
                "daily_timeframe": daily_timeframe,
                "prepared_cache_rebuilt": cache_rebuilt,
                "min_dollar_volume": float(min_dollar_volume or 0.0),
            }
        )
        if "min_dollar_volume" in experiment:
            params["min_dollar_volume"] = float(experiment["min_dollar_volume"] or 0.0)
        tasks.append(
            {
                "index": index,
                "experiment_name": experiment_name,
                "params": params,
                "include_raw": include_raw,
            }
        )
    return tasks


def _slice_benchmark_for_period(benchmark_data, start_date=None, end_date=None):
    if benchmark_data is None or benchmark_data.empty:
        return benchmark_data
    return _slice_frame(benchmark_data, start_date, end_date)


def _slice_asset_data_for_period(asset_data, start_date=None, end_date=None):
    if not start_date and not end_date:
        return asset_data
    sliced = {}
    for symbol, frame in asset_data.items():
        period_frame = _slice_frame(frame, start_date, end_date)
        if period_frame.empty:
            continue
        daily = frame.attrs.get("daily_ohlcv")
        if isinstance(daily, pd.DataFrame) and not daily.empty:
            period_frame.attrs["daily_ohlcv"] = _slice_frame(daily, start_date, end_date)
        for key, value in frame.attrs.items():
            if key != "daily_ohlcv":
                period_frame.attrs[key] = value
        sliced[symbol] = period_frame
    return sliced


def _run_experiment_batch(
    tasks,
    asset_data,
    benchmark_data,
    cache_path,
    parallel_enabled=False,
    workers=1,
):
    batch_started = _now()
    results = []
    if parallel_enabled and len(tasks) > 1:
        print(f"Parallel enabled: workers={workers}")
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_experiment_worker,
            initargs=(cache_path,),
        ) as executor:
            futures = {
                executor.submit(_run_experiment_worker, task): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                result = future.result()
                print(
                    f"[stage] experiment {task['index']}/{len(tasks)} done in "
                    f"{result['runtime_sec']:.2f} sec: {task['experiment_name']}"
                )
                results.append(result)
    else:
        for task in tasks:
            print(
                f"\n[stage] experiment {task['index']}/{len(tasks)} started: "
                f"{task['experiment_name']}"
            )
            started = _now()
            period_asset_data = asset_data
            period_benchmark_data = benchmark_data
            if task.get("period_start") or task.get("period_end"):
                period_asset_data = _slice_asset_data_for_period(
                    asset_data,
                    task.get("period_start"),
                    task.get("period_end"),
                )
                period_benchmark_data = _slice_benchmark_for_period(
                    benchmark_data,
                    task.get("period_start"),
                    task.get("period_end"),
                )
            final_data, metrics = _run_strategy(
                period_asset_data,
                period_benchmark_data,
                task["params"],
            )
            _add_benchmarks(
                final_data,
                period_benchmark_data,
                metrics,
                benchmark_asset_data=period_asset_data,
            )
            runtime_sec = _now() - started
            print(
                "Experiment diagnostics: "
                f"symbols_loaded={metrics.get('symbols_loaded', 0)}, "
                f"total_final_entry_signals={metrics.get('total_final_entry_signals', 0)}, "
                f"total_trades={metrics.get('total_trades', 0)}"
            )
            print(
                f"[stage] experiment {task['index']}/{len(tasks)} done in "
                f"{runtime_sec:.2f} sec"
            )
            results.append(
                {
                    "experiment_name": task["experiment_name"],
                    "row": _report_row(task["experiment_name"], task["params"], metrics),
                    "raw": {"params": task["params"], "metrics": metrics}
                    if task.get("include_raw")
                    else None,
                    "runtime_sec": runtime_sec,
                    "symbols_loaded": metrics.get("symbols_loaded", 0),
                    "total_final_entry_signals": metrics.get("total_final_entry_signals", 0),
                    "total_trades": metrics.get("total_trades", 0),
                }
            )
    batch_sec = _now() - batch_started
    print(f"Experiment batch done in {batch_sec:.2f} sec")
    results.sort(key=lambda item: item["experiment_name"])
    return results, batch_sec


def _write_benchmark_audit(report, path, cache_path):
    rows = []
    for _, row in report.sort_values("Experiment Name").iterrows():
        rows.append(
            {
                "experiment_name": row.get("Experiment Name", ""),
                "qqq_start_date": row.get("QQQ Benchmark Start", ""),
                "qqq_end_date": row.get("QQQ Benchmark End", ""),
                "equal_weight_start_date": row.get("EqualWeight Benchmark Start", ""),
                "equal_weight_end_date": row.get("EqualWeight Benchmark End", ""),
                "benchmark_symbol": row.get("benchmark_symbol", "QQQ"),
                "qqq_buyhold_return": row.get("QQQ BuyHold Return (%)", ""),
                "equal_weight_universe_return": row.get(
                    "EqualWeight Universe Return (%)",
                    "",
                ),
                "universe_count_used": 99,
                "excluded_symbols_applied": "QQQ,SPY",
                "benchmark_missing_symbols_count": row.get(
                    "EqualWeight Missing Symbols Count",
                    "",
                ),
                "missing_symbols": row.get("EqualWeight Missing Symbols", ""),
            }
        )
    audit = pd.DataFrame(rows)
    same_qqq_range = audit[["qqq_start_date", "qqq_end_date"]].drop_duplicates().shape[0] == 1
    same_ew_range = audit[["equal_weight_start_date", "equal_weight_end_date"]].drop_duplicates().shape[0] == 1
    qqq_unique = sorted(audit["qqq_buyhold_return"].dropna().unique().tolist())
    ew_unique = sorted(audit["equal_weight_universe_return"].dropna().unique().tolist())
    missing_unique = sorted(
        str(value)
        for value in audit["missing_symbols"].fillna("").unique().tolist()
        if str(value)
    )
    try:
        table = audit.to_markdown(index=False)
    except ImportError:
        table = audit.to_string(index=False)
    content = [
        "# Benchmark Audit Report",
        "",
        "## Summary",
        "",
        f"- Prepared feature cache: `{cache_path}`",
        "- EqualWeight benchmark uses immutable base/prepared OHLCV close data.",
        f"- Same QQQ date range across all experiments: {same_qqq_range}.",
        f"- Same EqualWeight date range across all experiments: {same_ew_range}.",
        f"- QQQ BuyHold Return unique values: {qqq_unique}.",
        f"- EqualWeight Universe Return unique values: {ew_unique}.",
        f"- Missing symbols: {missing_unique if missing_unique else 'none'}.",
        "",
        "## Per Experiment",
        "",
        table,
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def _write_performance_profile(profile_rows, csv_path, markdown_path):
    profile = pd.DataFrame(profile_rows)
    profile.to_csv(csv_path, index=False)
    try:
        table = profile.to_markdown(index=False)
    except ImportError:
        table = profile.to_string(index=False)
    stage_times = profile[["stage", "duration_sec"]].copy()
    stage_times["duration_sec"] = pd.to_numeric(stage_times["duration_sec"], errors="coerce")
    slowest = stage_times.dropna().sort_values("duration_sec", ascending=False).head(5)
    try:
        slowest_table = slowest.to_markdown(index=False)
    except ImportError:
        slowest_table = slowest.to_string(index=False)
    markdown_path.write_text(
        "\n".join(
            [
                "# Performance Profile Report",
                "",
                "## Slowest 5 Stages",
                "",
                slowest_table,
                "",
                "## All Stages",
                "",
                table,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return slowest


def _table_for_markdown(frame):
    try:
        return frame.to_markdown(index=False)
    except ImportError:
        return frame.to_string(index=False)


def _trade_stats(trades):
    if trades.empty:
        return {
            "trade_count": 0,
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "win_rate": 0.0,
            "avg_holding_bars": 0.0,
        }
    r_values = pd.to_numeric(trades.get("R Multiple"), errors="coerce").dropna()
    pnl = pd.to_numeric(trades.get("Net PnL"), errors="coerce").dropna()
    wins = pnl[pnl > 0].sum()
    losses = pnl[pnl < 0].sum()
    profit_factor = wins / abs(losses) if losses < 0 else (wins if wins > 0 else 0.0)
    bars = pd.to_numeric(trades.get("Bars Held"), errors="coerce").dropna()
    return {
        "trade_count": int(len(trades)),
        "profit_factor": round(float(profit_factor), 2),
        "expectancy_r": round(float(r_values.mean()), 4) if not r_values.empty else 0.0,
        "win_rate": round(float((pnl > 0).mean() * 100), 2) if not pnl.empty else 0.0,
        "avg_holding_bars": round(float(bars.mean()), 2) if not bars.empty else 0.0,
    }


def _safe_yearly_benchmark_metrics(benchmark_data, asset_data, start, end, year):
    missing = {
        "qqq_return_pct": pd.NA,
        "excess_vs_qqq_pct": pd.NA,
        "equal_weight_return_pct": pd.NA,
        "excess_vs_equal_weight_pct": pd.NA,
        "benchmark_insufficient_data": True,
    }
    warning = f"Warning: yearly benchmark insufficient for {year}, skipping benchmark metrics"
    if benchmark_data is None or benchmark_data.empty or "Close" not in benchmark_data:
        print(warning)
        return missing
    close = pd.to_numeric(benchmark_data["Close"], errors="coerce").dropna().sort_index()
    if close.empty:
        print(warning)
        return missing
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    if close.index.tz is not None:
        if start.tzinfo is None:
            start = start.tz_localize(close.index.tz)
            end = end.tz_localize(close.index.tz)
        else:
            start = start.tz_convert(close.index.tz)
            end = end.tz_convert(close.index.tz)
    elif close.index.tz is None and start.tzinfo is not None:
        start = start.tz_convert(None)
        end = end.tz_convert(None)
    start = max(start, close.index[0])
    end = min(end, close.index[-1])
    if end <= start or len(close.loc[(close.index >= start) & (close.index <= end)]) < 2:
        print(warning)
        return missing
    try:
        qqq = calculate_buyhold_metrics(close, start, end)
        ew = calculate_equal_weight_benchmark(asset_data, start, end)
    except Exception:
        print(warning)
        return missing
    return {
        "qqq_return_pct": qqq["Return (%)"],
        "excess_vs_qqq_pct": None,
        "equal_weight_return_pct": ew["Return (%)"],
        "excess_vs_equal_weight_pct": None,
        "benchmark_insufficient_data": False,
    }


def _yearly_rows(raw_results, focus_names, benchmark_data, asset_data):
    rows = []
    available_starts = []
    if benchmark_data is not None and not benchmark_data.empty:
        available_starts.append(benchmark_data.index.sort_values()[0])
    for data in asset_data.values():
        if not data.empty:
            available_starts.append(data.index.sort_values()[0])
    data_start = min(available_starts) if available_starts else None
    for experiment_name in focus_names:
        item = raw_results.get(experiment_name)
        if not item:
            continue
        equity = pd.DataFrame(
            item["metrics"].get("_equity_curve", []),
            columns=["date", "equity"],
        )
        if equity.empty:
            continue
        equity["date"] = pd.to_datetime(equity["date"])
        equity = equity.sort_values("date")
        equity["year"] = equity["date"].dt.year
        trades = pd.DataFrame(item["metrics"].get("_trade_log", []))
        if not trades.empty:
            trades["Exit Time"] = pd.to_datetime(trades["Exit Time"])
            trades["year"] = trades["Exit Time"].dt.year
        for year, frame in equity.groupby("year"):
            start = frame["date"].iloc[0]
            end = frame["date"].iloc[-1]
            if data_start is not None:
                compare_end = pd.Timestamp(end)
                compare_start = pd.Timestamp(data_start)
                if compare_start.tzinfo is not None and compare_end.tzinfo is None:
                    compare_end = compare_end.tz_localize(compare_start.tzinfo)
                elif compare_start.tzinfo is None and compare_end.tzinfo is not None:
                    compare_end = compare_end.tz_convert(None)
                if compare_end < compare_start:
                    continue
            start_equity = float(frame["equity"].iloc[0])
            end_equity = float(frame["equity"].iloc[-1])
            normalized = frame["equity"] / start_equity
            max_dd = ((normalized - normalized.cummax()) / normalized.cummax()).min() * 100
            year_trades = (
                trades[trades["year"] == year] if not trades.empty else pd.DataFrame()
            )
            stats = _trade_stats(year_trades)
            net_return = (end_equity / start_equity - 1) * 100
            benchmark_metrics = _safe_yearly_benchmark_metrics(
                benchmark_data,
                asset_data,
                start,
                end,
                year,
            )
            if not benchmark_metrics["benchmark_insufficient_data"]:
                benchmark_metrics["excess_vs_qqq_pct"] = round(
                    float(net_return) - benchmark_metrics["qqq_return_pct"],
                    2,
                )
                benchmark_metrics["excess_vs_equal_weight_pct"] = round(
                    float(net_return) - benchmark_metrics["equal_weight_return_pct"],
                    2,
                )
            if (
                stats["trade_count"] == 0
                and bool(benchmark_metrics["benchmark_insufficient_data"])
            ):
                continue
            rows.append(
                {
                    "experiment_name": experiment_name,
                    "year": int(year),
                    "net_return_pct": round(float(net_return), 2),
                    "max_drawdown_pct": round(float(max_dd), 2),
                    **stats,
                    **benchmark_metrics,
                }
            )
    return pd.DataFrame(rows)


def _paper_candidate_experiments(base_experiments):
    candidates = {experiment["name"]: dict(experiment) for experiment in base_experiments}
    for experiment in _rs_robustness_experiments():
        if experiment["name"] in {
            "rs_m126_top30_sma100_no_regime",
            "rs_m126_top20_sma100_no_regime",
        }:
            candidates[experiment["name"]] = experiment
    wanted = [
        "rs_m126_top30_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "donchian_20_10_top20",
    ]
    missing = [name for name in wanted if name not in candidates]
    if missing:
        raise ValueError(f"Paper report aday config eksik: {', '.join(missing)}")
    return [candidates[name] for name in wanted]


def _paper_signal_columns():
    return PAPER_SIGNAL_COLUMNS


def _paper_trade_log_columns():
    return PAPER_TRADE_LOG_COLUMNS


def _paper_pending_order_columns():
    return PAPER_PENDING_ORDERS_COLUMNS


def _paper_order_review_columns():
    return PAPER_ORDER_REVIEW_COLUMNS


def _data_policy_decision_columns():
    return DATA_POLICY_DECISION_COLUMNS


def _data_policy_review_evidence_columns():
    return DATA_POLICY_REVIEW_EVIDENCE_COLUMNS


def _paper_main_strategy_name():
    return "rs_m126_top30_sma100_no_regime"


def _ranking_score_from_row(row):
    for column in ["RS_Score", "Donchian_Breakout_Strength", "Momentum_Factor"]:
        value = row.get(column, pd.NA)
        if pd.notna(value):
            return value
    return pd.NA


def _load_paper_positions(path):
    if not path.exists():
        return {}, True
    try:
        ledger = pd.read_csv(path)
    except Exception:
        return {}, True
    positions = {}
    if ledger.empty:
        return positions, True
    open_positions = net_open_positions_from_trade_log(ledger)
    for _, row in open_positions.iterrows():
        strategy = str(row.get("strategy_name", "")).strip()
        symbol = str(row.get("symbol", "")).strip().upper()
        if strategy and symbol:
            positions[(strategy, symbol)] = True
    return positions, False


def _paper_signal_rows_for_result(
    experiment_name,
    params,
    final_data,
    run_datetime,
    positions,
    initial_state,
    paper_strategy_mode,
    include_watchlist,
    paper_capital=25000,
):
    if not final_data:
        return []
    latest_dates = [frame.index.max() for frame in final_data.values() if frame is not None and not frame.empty]
    if not latest_dates:
        return []
    signal_date = max(latest_dates)
    max_positions = _max_positions_from_params(params)
    rows = []
    for symbol, frame in sorted(final_data.items()):
        if frame is None or frame.empty or signal_date not in frame.index:
            continue
        row = frame.loc[signal_date]
        entry = bool(row.get("ENTRY_SIGNAL", False))
        exit_signal = bool(row.get("EXIT_SIGNAL", False))
        already_in_position = bool(positions.get((experiment_name, symbol), False))
        is_main = experiment_name == _paper_main_strategy_name()
        is_watch_only = paper_strategy_mode == "main" and not is_main
        if entry and is_watch_only:
            base_action = "WATCH"
        elif entry and not already_in_position:
            base_action = "BUY"
        elif exit_signal and already_in_position:
            base_action = "SELL"
        else:
            continue
        close = pd.to_numeric(pd.Series([row.get("Close", pd.NA)]), errors="coerce").iloc[0]
        volume = pd.to_numeric(pd.Series([row.get("Volume", pd.NA)]), errors="coerce").iloc[0]
        dollar_volume = float(close) * float(volume) if pd.notna(close) and pd.notna(volume) else pd.NA
        next_pos = frame.index.searchsorted(signal_date, side="right")
        next_open_available = bool(next_pos < len(frame.index) and pd.notna(frame.iloc[next_pos].get("Open", pd.NA)))
        position_notional = round(float(paper_capital) / max_positions, 2)
        order_pct = (
            round(position_notional / float(dollar_volume) * 100.0, 6)
            if pd.notna(dollar_volume) and float(dollar_volume) > 0
            else pd.NA
        )
        liquidity_warnings = []
        if pd.isna(dollar_volume):
            liquidity_warnings.append("missing_dollar_volume")
        elif float(dollar_volume) < 500_000:
            liquidity_warnings.append("estimated_dollar_volume_below_500k")
        if pd.notna(order_pct) and float(order_pct) > 1.0:
            liquidity_warnings.append("order_size_pct_of_dollar_volume_above_1pct")
        action = "SKIP" if liquidity_warnings and base_action in {"BUY", "SELL"} else base_action
        reason = ["initial_paper_state" if initial_state else "paper_ledger_state"]
        if base_action == "WATCH":
            reason.append("watch_only_comparison_strategy")
        else:
            reason.append("latest_entry_signal" if base_action == "BUY" else "latest_exit_signal")
        if not next_open_available:
            reason.append("missing_next_open")
        rows.append(
            {
                "signal_scope": "latest_only",
                "run_datetime": run_datetime,
                "paper_strategy_mode": paper_strategy_mode,
                "paper_portfolio_state": "initial_empty" if initial_state else "ledger_loaded",
                "strategy_name": experiment_name,
                "signal_date": str(signal_date),
                "symbol": symbol,
                "action": action,
                "fill_status": "NOT_APPLICABLE",
                "user_decision": "HOLD_REVIEW",
                "reason": ";".join(reason),
                "is_new_signal": True,
                "already_in_position": already_in_position,
                "paper_actionable": False,
                "deferred_due_to_ramp_in": False,
                "paper_rank": pd.NA,
                "paper_max_new_positions_per_day": pd.NA,
                "ranking_score": _ranking_score_from_row(row),
                "current_position_status": "open" if already_in_position else "flat",
                "liquidity_guard_pass": not liquidity_warnings,
                "liquidity_warning_reason": ";".join(liquidity_warnings),
                "warning": ";".join(liquidity_warnings),
                "estimated_dollar_volume": round(float(dollar_volume), 2)
                if pd.notna(dollar_volume)
                else pd.NA,
                "order_size_pct_of_dollar_volume": order_pct,
                "paper_capital_assumption": paper_capital,
                "paper_position_notional": position_notional,
                "entry_price_reference": close,
                "assumed_fill_rule": "next_available_open",
                "next_open_available": next_open_available,
                "fill_bar_timestamp": "",
                "fill_price_source": "next_130min_open",
                "max_positions": max_positions,
            }
        )
    return rows


def _run_paper_signal_report(
    experiments,
    data_folder,
    run_profile,
    quick_mode,
    intraday_dir,
    daily_dir,
    raw_dir,
    intraday_timeframe,
    daily_timeframe,
    cache_rebuilt,
    asset_data,
    benchmark_data,
    ledger_path,
    paper_strategy_mode="main",
    include_watchlist=False,
    paper_max_new_positions_per_day=30,
):
    run_datetime = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    positions, initial_state = _load_paper_positions(ledger_path)
    tasks = _build_experiment_tasks(
        _paper_candidate_experiments(experiments),
        data_folder,
        run_profile,
        quick_mode,
        intraday_dir,
        daily_dir,
        raw_dir,
        intraday_timeframe,
        daily_timeframe,
        cache_rebuilt,
        include_raw=False,
    )
    for task in tasks:
        final_data, _ = _run_strategy(asset_data, benchmark_data, task["params"])
        rows.extend(
            _paper_signal_rows_for_result(
                task["experiment_name"],
                task["params"],
                final_data,
                run_datetime,
                positions,
                initial_state,
                paper_strategy_mode,
                include_watchlist,
            )
        )
    output = pd.DataFrame(rows, columns=_paper_signal_columns())
    if output.empty:
        return output
    output["paper_max_new_positions_per_day"] = int(paper_max_new_positions_per_day)
    output["paper_actionable"] = False
    output["deferred_due_to_ramp_in"] = False
    main_buy = output[
        output["strategy_name"].eq(_paper_main_strategy_name())
        & output["action"].eq("BUY")
    ].copy()
    if not main_buy.empty:
        main_buy["_rank_score"] = pd.to_numeric(main_buy["ranking_score"], errors="coerce")
        ranked_index = main_buy.sort_values(
            ["_rank_score", "symbol"],
            ascending=[False, True],
            na_position="last",
        ).index.tolist()
        for rank, index in enumerate(ranked_index, 1):
            output.loc[index, "paper_rank"] = rank
            if rank <= int(paper_max_new_positions_per_day):
                output.loc[index, "paper_actionable"] = True
            else:
                output.loc[index, "action"] = "DEFER"
                output.loc[index, "paper_actionable"] = False
                output.loc[index, "deferred_due_to_ramp_in"] = True
                reason = str(output.loc[index, "reason"] or "")
                output.loc[index, "reason"] = f"{reason};ramp_in_limit" if reason else "ramp_in_limit"
    sell_mask = output["strategy_name"].eq(_paper_main_strategy_name()) & output["action"].eq("SELL")
    output.loc[sell_mask, "paper_actionable"] = True
    skip_mask = output["strategy_name"].eq(_paper_main_strategy_name()) & output["action"].eq("SKIP")
    output.loc[skip_mask, "paper_actionable"] = False
    return output[_paper_signal_columns()]


def _block_new_signal_generation_on_daily_mismatch(paper_report, daily_dir, intraday_dir):
    # Daily-only dataset profiles (rs_v2_tiingo, and daily-only mode generally)
    # ship no intraday directory at all, so this comparison has no second
    # operand: an absent directory is the profile's shape, not a staleness
    # signal. A present intraday directory is still compared -- including the
    # empty/stale cases this guard exists to catch.
    if not Path(intraday_dir).exists():
        return paper_report
    latest_daily = _latest_csv_data_date(daily_dir)
    latest_130min = _latest_csv_data_date(intraday_dir)
    if latest_daily == latest_130min or paper_report is None or paper_report.empty:
        return paper_report
    output = paper_report.copy()
    new_signal_mask = output.get("is_new_signal", pd.Series(False, index=output.index)).astype(bool)
    actionable_mask = output.get("paper_actionable", pd.Series(False, index=output.index)).astype(bool)
    action = output.get("action", pd.Series("", index=output.index)).astype(str).str.upper()
    target = new_signal_mask & actionable_mask & action.isin(["BUY", "SELL"])
    output.loc[target, "paper_actionable"] = False
    output.loc[target, "fill_status"] = "NOT_APPLICABLE"
    output.loc[target, "user_decision"] = "HOLD_REVIEW"
    marker = "new_signal_generation_blocked_by_daily_130min_mismatch"
    for index in output[target].index:
        reason = str(output.loc[index, "reason"] or "")
        if marker not in reason:
            output.loc[index, "reason"] = f"{reason};{marker}" if reason else marker
    return output


def _write_paper_signal_report(paper_report, csv_path, markdown_path):
    paper_report = ensure_columns(paper_report, _paper_signal_columns(), fill_value=pd.NA)
    paper_report.to_csv(csv_path, index=False)
    liquidity_warnings_count = (
        int(paper_report["liquidity_warning_reason"].fillna("").astype(str).ne("").sum())
        if not paper_report.empty and "liquidity_warning_reason" in paper_report
        else 0
    )
    action_counts = paper_report["action"].value_counts().to_dict() if not paper_report.empty else {}
    content = [
        "# Paper Signal Report",
        "",
        f"- Latest signal count: {len(paper_report)}",
        f"- Action counts: {action_counts}",
        f"- Liquidity warnings count: {liquidity_warnings_count}",
        "- Assumed fill rule: next_available_open",
        "- Broker/API orders: disabled; report-only.",
        "",
        "## Signals",
        "",
        _readable_table(paper_report),
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _paper_order_key(row):
    return (
        str(row.get("strategy_name", "")),
        str(row.get("symbol", "")).upper(),
        str(row.get("action", "")).upper(),
    )


def _load_paper_order_review(path):
    return read_order_review(Path(path).parent)


def _write_paper_order_review(paper_report, path):
    existing = _load_paper_order_review(path)
    decisions = {}
    notes = {}
    for _, row in existing.iterrows():
        key = _paper_order_key(row)
        decisions[key] = str(row.get("user_decision", "HOLD_REVIEW")).strip().upper() or "HOLD_REVIEW"
        notes[key] = row.get("notes", "")
    actionable = paper_report[
        paper_report.get("paper_actionable", pd.Series(False, index=paper_report.index)).astype(bool)
        & paper_report["action"].isin(["BUY", "SELL"])
    ].copy() if paper_report is not None and not paper_report.empty else pd.DataFrame()
    rows = []
    for _, row in actionable.iterrows():
        key = _paper_order_key(row)
        rows.append(
            {
                "symbol": row.get("symbol", ""),
                "strategy_name": row.get("strategy_name", ""),
                "action": row.get("action", ""),
                "user_decision": decisions.get(key, "HOLD_REVIEW"),
                "notes": notes.get(key, ""),
            }
        )
    review = pd.DataFrame(rows, columns=_paper_order_review_columns()).drop_duplicates(
        subset=["symbol", "strategy_name", "action"],
        keep="first",
    )
    review = ensure_columns(review, _paper_order_review_columns())
    return write_order_review(Path(path).parent, review)


def _ensure_paper_trade_log(path):
    return write_trade_log(Path(path).parent, read_trade_log(Path(path).parent))


def _clean_note(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _next_open_for_signal(asset_data, symbol, signal_date):
    fill = _next_open_fill_for_signal(asset_data, symbol, signal_date)
    return fill["fill_price"]


def _raw_5min_path_for_asset(frame, symbol):
    intraday_dir = frame.attrs.get("intraday_data_dir") if frame is not None else None
    if not intraday_dir:
        return None
    return Path(intraday_dir).parent / "raw_5min" / f"{str(symbol).upper()}_5min.csv"


def _fresh_130min_frame_for_symbol(frame, symbol):
    intraday_dir = frame.attrs.get("intraday_data_dir") if frame is not None else None
    if not intraday_dir:
        return None
    path = Path(intraday_dir) / f"{str(symbol).upper()}_130min.csv"
    if not path.exists():
        return None
    try:
        fresh = _normalize_frame(path)
    except Exception:
        return None
    fresh.attrs["intraday_data_dir"] = str(intraday_dir)
    return fresh


def _bar_130min_complete_from_raw(frame, symbol, bar_timestamp):
    raw_path = _raw_5min_path_for_asset(frame, symbol)
    if raw_path is None:
        return True
    if not raw_path.exists():
        return False
    try:
        raw = pd.read_csv(raw_path, usecols=["Date"])
    except Exception:
        return False
    raw_dates = pd.to_datetime(raw["Date"], errors="coerce", utc=True).dropna()
    if raw_dates.empty:
        return False
    bar_ts = pd.Timestamp(bar_timestamp)
    if bar_ts.tzinfo is None:
        bar_ts = bar_ts.tz_localize("America/New_York")
    else:
        bar_ts = bar_ts.tz_convert("America/New_York")
    local_dates = raw_dates.dt.tz_convert("America/New_York")
    minute = bar_ts.hour * 60 + bar_ts.minute
    if minute == 9 * 60 + 30:
        required_last = bar_ts + pd.Timedelta(minutes=125)
    elif minute == 11 * 60 + 40:
        required_last = bar_ts + pd.Timedelta(minutes=125)
    elif minute == 13 * 60 + 50:
        required_last = bar_ts + pd.Timedelta(minutes=125)
    else:
        return False
    return bool((local_dates >= required_last).any())


def _next_open_fill_for_signal(asset_data, symbol, signal_date):
    frame = asset_data.get(str(symbol).upper())
    empty = {"fill_price": pd.NA, "fill_bar_timestamp": "", "fill_price_source": "next_130min_open"}
    if frame is None or frame.empty or "Open" not in frame:
        return empty
    ts = pd.Timestamp(signal_date)
    candidates = [frame]
    fresh = _fresh_130min_frame_for_symbol(frame, symbol)
    if fresh is not None and not fresh.empty:
        candidates.append(fresh)
    for candidate in candidates:
        index = candidate.index
        candidate_ts = ts
        if index.tz is not None and candidate_ts.tzinfo is None:
            candidate_ts = candidate_ts.tz_localize(index.tz)
        elif index.tz is None and candidate_ts.tzinfo is not None:
            candidate_ts = candidate_ts.tz_convert(None)
        pos = index.searchsorted(candidate_ts, side="right")
        while pos < len(index):
            value = candidate.iloc[pos].get("Open", pd.NA)
            price = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
            if pd.notna(price) and _bar_130min_complete_from_raw(candidate, symbol, index[pos]):
                return {
                    "fill_price": price,
                    "fill_bar_timestamp": str(index[pos]),
                    "fill_price_source": "next_130min_open",
                }
            pos += 1
    return empty


def _paper_open_shares_for_order(trade_log, strategy_name, symbol):
    frame = trade_log.copy() if trade_log is not None else pd.DataFrame()
    if frame.empty:
        return 0.0
    status = frame.get("fill_status", pd.Series("", index=frame.index)).astype(str).str.upper()
    filled = frame[status.eq("PAPER_FILLED")].copy()
    if filled.empty:
        return 0.0
    strategy = filled.get("strategy_name", pd.Series("", index=filled.index)).astype(str)
    symbols = filled.get("symbol", pd.Series("", index=filled.index)).astype(str).str.upper()
    action = filled.get("action", pd.Series("", index=filled.index)).astype(str).str.upper()
    shares = pd.to_numeric(filled.get("shares", pd.Series(0.0, index=filled.index)), errors="coerce").fillna(0.0)
    mask = strategy.eq(str(strategy_name)) & symbols.eq(str(symbol).upper())
    signed = shares.where(action.eq("BUY"), -shares.where(action.eq("SELL"), 0.0))
    net_shares = float(signed[mask].sum()) if mask.any() else 0.0
    return round(max(net_shares, 0.0), 6)


def _paper_open_position_keys_from_trade_log(trade_log):
    open_positions = net_open_positions_from_trade_log(trade_log)
    if open_positions.empty:
        return set()
    return {
        (str(row.get("strategy_name", "")).strip(), str(row.get("symbol", "")).strip().upper())
        for _, row in open_positions.iterrows()
        if str(row.get("strategy_name", "")).strip() and str(row.get("symbol", "")).strip()
    }


def _is_duplicate_open_buy(row, open_position_keys):
    action = str(row.get("action", "")).strip().upper()
    if action != "BUY":
        return False
    key = (
        str(row.get("strategy_name", "")).strip(),
        str(row.get("symbol", "")).strip().upper(),
    )
    return key in open_position_keys


def _append_semicolon_token(value, token):
    text = str(value or "").strip()
    if token in {part.strip() for part in text.split(";") if part.strip()}:
        return text
    return f"{text};{token}" if text else token


def _paper_fill_shares_for_order(row, fill_price, trade_log):
    action = str(row.get("action", "")).strip().upper()
    if action == "SELL":
        return _paper_open_shares_for_order(
            trade_log,
            row.get("strategy_name", ""),
            row.get("symbol", ""),
        )
    price = pd.to_numeric(pd.Series([fill_price]), errors="coerce").iloc[0]
    notional = pd.to_numeric(pd.Series([row.get("paper_position_notional", pd.NA)]), errors="coerce").iloc[0]
    return round(float(notional) / float(price), 6) if pd.notna(notional) and pd.notna(price) and float(price) > 0 else 0.0


def _apply_paper_order_review(paper_report, review, trade_log_path, asset_data):
    log = _ensure_paper_trade_log(trade_log_path)
    if paper_report is None or paper_report.empty:
        return paper_report, log
    decision_by_key = {
        _paper_order_key(row): str(row.get("user_decision", "HOLD_REVIEW")).strip().upper() or "HOLD_REVIEW"
        for _, row in review.iterrows()
    } if review is not None and not review.empty else {}
    notes_by_key = {
        _paper_order_key(row): row.get("notes", "")
        for _, row in review.iterrows()
    } if review is not None and not review.empty else {}
    output = paper_report.copy()
    new_log_rows = []
    open_position_keys = _paper_open_position_keys_from_trade_log(log)
    existing_keys = set()
    if not log.empty:
        for _, row in log.iterrows():
            existing_keys.add(
                (
                    str(row.get("strategy_name", "")),
                    str(row.get("symbol", "")).upper(),
                    str(row.get("action", "")).upper(),
                    str(row.get("signal_date", "")),
                    str(row.get("fill_status", "")),
                )
            )
    for index, row in output.iterrows():
        if not bool(row.get("paper_actionable", False)) or row.get("action") not in {"BUY", "SELL"}:
            continue
        key = _paper_order_key(row)
        decision = decision_by_key.get(key, "HOLD_REVIEW")
        output.loc[index, "user_decision"] = decision
        if decision == "ACCEPT":
            if _is_duplicate_open_buy(row, open_position_keys):
                output.loc[index, "user_decision"] = "REVIEW_REQUIRED"
                output.loc[index, "fill_status"] = "BLOCKED"
                output.loc[index, "reason"] = _append_semicolon_token(row.get("reason", ""), "buy_already_open")
                output.loc[index, "warning"] = _append_semicolon_token(row.get("warning", ""), "already_open_position")
                continue
            fill = _next_open_fill_for_signal(asset_data, row.get("symbol"), row.get("signal_date"))
            fill_price = fill["fill_price"]
            if pd.notna(fill_price):
                fill_status = "PAPER_FILLED"
                output.loc[index, "fill_status"] = fill_status
                output.loc[index, "fill_bar_timestamp"] = fill["fill_bar_timestamp"]
                output.loc[index, "fill_price_source"] = fill["fill_price_source"]
                shares = _paper_fill_shares_for_order(row, fill_price, log)
                log_key = (
                    str(row.get("strategy_name", "")),
                    str(row.get("symbol", "")).upper(),
                    str(row.get("action", "")).upper(),
                    str(row.get("signal_date", "")),
                    fill_status,
                )
                if log_key not in existing_keys:
                    new_log_rows.append(
                        {
                            "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "strategy_name": row.get("strategy_name", ""),
                            "symbol": row.get("symbol", ""),
                            "action": row.get("action", ""),
                            "signal_date": row.get("signal_date", ""),
                            "signal_price": row.get("entry_price_reference", pd.NA),
                            "assumed_fill_price": fill_price,
                            "fill_status": fill_status,
                            "shares": shares,
                            "notional": row.get("paper_position_notional", pd.NA),
                            "paper_capital": row.get("paper_capital_assumption", pd.NA),
                            "reason": row.get("reason", ""),
                            "liquidity_guard_pass": row.get("liquidity_guard_pass", pd.NA),
                            "notes": ";".join(
                                item
                                for item in [
                                    _clean_note(notes_by_key.get(key, "")),
                                    f"fill_bar_timestamp={fill['fill_bar_timestamp']}",
                                    f"fill_price_source={fill['fill_price_source']}",
                                ]
                                if item
                            ),
                        }
                    )
            else:
                output.loc[index, "fill_status"] = "PENDING_NEXT_OPEN"
        elif decision == "REJECT":
            fill_status = "REJECTED"
            output.loc[index, "fill_status"] = fill_status
            log_key = (
                str(row.get("strategy_name", "")),
                str(row.get("symbol", "")).upper(),
                str(row.get("action", "")).upper(),
                str(row.get("signal_date", "")),
                fill_status,
            )
            if log_key not in existing_keys:
                new_log_rows.append(
                    {
                        "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "strategy_name": row.get("strategy_name", ""),
                        "symbol": row.get("symbol", ""),
                        "action": row.get("action", ""),
                        "signal_date": row.get("signal_date", ""),
                        "signal_price": row.get("entry_price_reference", pd.NA),
                        "assumed_fill_price": pd.NA,
                        "fill_status": fill_status,
                        "shares": 0,
                        "notional": row.get("paper_position_notional", pd.NA),
                        "paper_capital": row.get("paper_capital_assumption", pd.NA),
                        "reason": row.get("reason", ""),
                        "liquidity_guard_pass": row.get("liquidity_guard_pass", pd.NA),
                        "notes": notes_by_key.get(key, ""),
                    }
                )
        else:
            output.loc[index, "fill_status"] = "PENDING_NEXT_OPEN" if not bool(row.get("next_open_available", False)) else "PENDING_REVIEW"
    if new_log_rows:
        log = append_trade_log(Path(trade_log_path).parent, new_log_rows)
    return output, log


def _load_previous_paper_signal_report(path):
    if not Path(path).exists():
        return pd.DataFrame(columns=_paper_signal_columns())
    try:
        report = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=_paper_signal_columns())
    for column in _paper_signal_columns():
        if column not in report:
            report[column] = pd.NA
    return report[_paper_signal_columns()]


def _apply_previous_pending_next_open_orders(current_report, previous_report, review, trade_log_path, asset_data):
    if previous_report is None or previous_report.empty:
        return current_report
    log = _ensure_paper_trade_log(trade_log_path)
    decision_by_key = {
        _paper_order_key(row): str(row.get("user_decision", "HOLD_REVIEW")).strip().upper() or "HOLD_REVIEW"
        for _, row in review.iterrows()
    } if review is not None and not review.empty else {}
    notes_by_key = {
        _paper_order_key(row): row.get("notes", "")
        for _, row in review.iterrows()
    } if review is not None and not review.empty else {}
    output = current_report.copy() if current_report is not None else pd.DataFrame(columns=_paper_signal_columns())
    existing_orders = set()
    if not output.empty:
        for _, row in output.iterrows():
            existing_orders.add(
                (
                    str(row.get("strategy_name", "")),
                    str(row.get("symbol", "")).upper(),
                    str(row.get("action", "")).upper(),
                    str(row.get("signal_date", "")),
                )
            )
    existing_log_keys = set()
    if not log.empty:
        for _, row in log.iterrows():
            existing_log_keys.add(
                (
                    str(row.get("strategy_name", "")),
                    str(row.get("symbol", "")).upper(),
                    str(row.get("action", "")).upper(),
                    str(row.get("signal_date", "")),
                    str(row.get("fill_status", "")),
                )
            )
    rows_to_append = []
    log_rows = []
    open_position_keys = _paper_open_position_keys_from_trade_log(log)
    pending = previous_report[
        previous_report.get("fill_status", pd.Series("", index=previous_report.index)).astype(str).str.upper().eq("PENDING_NEXT_OPEN")
        & previous_report.get("user_decision", pd.Series("", index=previous_report.index)).astype(str).str.upper().eq("ACCEPT")
        & previous_report.get("action", pd.Series("", index=previous_report.index)).astype(str).str.upper().isin(["BUY", "SELL"])
    ].copy()
    for _, row in pending.iterrows():
        review_key = _paper_order_key(row)
        latest_decision = decision_by_key.get(review_key, "ACCEPT")
        order_id = (
            str(row.get("strategy_name", "")),
            str(row.get("symbol", "")).upper(),
            str(row.get("action", "")).upper(),
            str(row.get("signal_date", "")),
        )
        carried = row.to_dict()
        carried["user_decision"] = latest_decision
        carried["paper_actionable"] = True
        if latest_decision != "ACCEPT":
            if latest_decision == "REJECT":
                carried["fill_status"] = "REJECTED"
                log_key = (*order_id, "REJECTED")
                if log_key not in existing_log_keys:
                    log_rows.append(
                        {
                            "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "strategy_name": row.get("strategy_name", ""),
                            "symbol": row.get("symbol", ""),
                            "action": row.get("action", ""),
                            "signal_date": row.get("signal_date", ""),
                            "signal_price": row.get("entry_price_reference", pd.NA),
                            "assumed_fill_price": pd.NA,
                            "fill_status": "REJECTED",
                            "shares": 0,
                            "notional": row.get("paper_position_notional", pd.NA),
                            "paper_capital": row.get("paper_capital_assumption", pd.NA),
                            "reason": row.get("reason", ""),
                            "liquidity_guard_pass": row.get("liquidity_guard_pass", pd.NA),
                            "notes": notes_by_key.get(review_key, ""),
                        }
                    )
            else:
                carried["fill_status"] = "PENDING_REVIEW"
                reason = str(carried.get("reason", "") or "")
                if "latest_review_not_accept" not in reason:
                    carried["reason"] = f"{reason};latest_review_not_accept" if reason else "latest_review_not_accept"
        else:
            if _is_duplicate_open_buy(row, open_position_keys):
                carried["user_decision"] = "REVIEW_REQUIRED"
                carried["fill_status"] = "BLOCKED"
                carried["reason"] = _append_semicolon_token(carried.get("reason", ""), "buy_already_open")
                carried["warning"] = _append_semicolon_token(carried.get("warning", ""), "already_open_position")
                if order_id not in existing_orders:
                    rows_to_append.append(carried)
                continue
            fill = _next_open_fill_for_signal(asset_data, row.get("symbol"), row.get("signal_date"))
            fill_price = fill["fill_price"]
            if pd.notna(fill_price):
                carried["fill_status"] = "PAPER_FILLED"
                carried["fill_bar_timestamp"] = fill["fill_bar_timestamp"]
                carried["fill_price_source"] = fill["fill_price_source"]
                log_key = (*order_id, "PAPER_FILLED")
                if log_key not in existing_log_keys:
                    shares = _paper_fill_shares_for_order(row, fill_price, log)
                    log_rows.append(
                        {
                            "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "strategy_name": row.get("strategy_name", ""),
                            "symbol": row.get("symbol", ""),
                            "action": row.get("action", ""),
                            "signal_date": row.get("signal_date", ""),
                            "signal_price": row.get("entry_price_reference", pd.NA),
                            "assumed_fill_price": fill_price,
                            "fill_status": "PAPER_FILLED",
                            "shares": shares,
                            "notional": row.get("paper_position_notional", pd.NA),
                            "paper_capital": row.get("paper_capital_assumption", pd.NA),
                            "reason": row.get("reason", ""),
                            "liquidity_guard_pass": row.get("liquidity_guard_pass", pd.NA),
                            "notes": ";".join(
                                item
                                for item in [
                                    _clean_note(notes_by_key.get(review_key, "")),
                                    f"fill_bar_timestamp={fill['fill_bar_timestamp']}",
                                    f"fill_price_source={fill['fill_price_source']}",
                                ]
                                if item
                            ),
                        }
                    )
            else:
                carried["fill_status"] = "PENDING_NEXT_OPEN"
                reason = str(carried.get("reason", "") or "")
                if "missing_next_open" not in reason:
                    carried["reason"] = f"{reason};missing_next_open" if reason else "missing_next_open"
        if order_id not in existing_orders:
            rows_to_append.append(carried)
    if log_rows:
        log = append_trade_log(Path(trade_log_path).parent, log_rows)
    if rows_to_append:
        appended = pd.DataFrame(rows_to_append)
        output = appended if output.empty else pd.concat([output, appended], ignore_index=True)
    for column in _paper_signal_columns():
        if column not in output:
            output[column] = pd.NA
    return output[_paper_signal_columns()]


def _write_paper_pending_orders(paper_report, path, latest_daily_date="none", trade_log_path=None):
    rows = []
    trade_path = Path(trade_log_path) if trade_log_path is not None else Path(path).parent / "paper_trade_log.csv"
    trade_log = read_trade_log(trade_path.parent) if trade_path.exists() else pd.DataFrame()
    open_position_keys = _paper_open_position_keys_from_trade_log(trade_log)
    actionable = paper_report[
        paper_report.get("paper_actionable", pd.Series(False, index=paper_report.index)).astype(bool)
        & paper_report["action"].isin(["BUY", "SELL"])
    ].copy() if paper_report is not None and not paper_report.empty else pd.DataFrame()
    for _, row in actionable.iterrows():
        status = "FILLED" if row.get("fill_status") == "PAPER_FILLED" else ("REJECTED" if row.get("fill_status") == "REJECTED" else "PENDING")
        user_decision = (
            "REVIEW_REQUIRED"
            if str(row.get("user_decision", "HOLD_REVIEW")).upper() == "HOLD_REVIEW"
            else row.get("user_decision", "REVIEW_REQUIRED")
        )
        reason = row.get("reason", "")
        notes = ""
        if _is_duplicate_open_buy(row, open_position_keys):
            status = "BLOCKED"
            user_decision = "REVIEW_REQUIRED"
            reason = _append_semicolon_token(reason, "buy_already_open")
            notes = "stale_accept_revalidation:already_open_position"
        aging = _pending_order_age_fields(row.get("signal_date", ""), latest_daily_date)
        rows.append(
            {
                "created_at": row.get("run_datetime", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")),
                "signal_date": row.get("signal_date", ""),
                "strategy_name": row.get("strategy_name", ""),
                "symbol": row.get("symbol", ""),
                "action": row.get("action", ""),
                "paper_rank": row.get("paper_rank", pd.NA),
                "ranking_score": row.get("ranking_score", pd.NA),
                "paper_capital": row.get("paper_capital_assumption", pd.NA),
                "paper_position_notional": row.get("paper_position_notional", pd.NA),
                "liquidity_guard_pass": row.get("liquidity_guard_pass", pd.NA),
                "estimated_dollar_volume": row.get("estimated_dollar_volume", pd.NA),
                "order_size_pct_of_dollar_volume": row.get("order_size_pct_of_dollar_volume", pd.NA),
                "status": status,
                "user_decision": user_decision,
                "reason": reason,
                **aging,
                "notes": notes,
            }
        )
    pending = ensure_columns(pd.DataFrame(rows), _paper_pending_order_columns(), fill_value=pd.NA)
    write_pending_orders(Path(path).parent, pending)


def _write_paper_trade_log_template(path):
    create_trade_log_template(Path(path).parent)


def _write_paper_trading_plan(path):
    content = [
        "# Paper Trading Plan",
        "",
        "## Selected Candidates",
        "",
        "- Main candidate: rs_m126_top30_sma100_no_regime",
        "- Aggressive alternative: rs_m126_top20_sma100_no_regime",
        "- Diversifier: donchian_20_10_top20",
        "- Research/aggressive only: rs_m126_top10_sma100_no_regime (not included in default paper report)",
        "- Baseline: rs_m63_sma100",
        "",
        "## Paper Report Scope",
        "",
        "- Main strategy: rs_m126_top30_sma100_no_regime",
        "- Comparison strategies: rs_m126_top20_sma100_no_regime, donchian_20_10_top20",
        "- Capital assumptions: 10000, 25000, 50000",
        "- Max positions: Top30=30, Top20=20, Donchian=20",
        "",
        "## Fill Assumption",
        "",
        "- If a signal is produced, paper fill is assumed at the next available open.",
        "- No Alpaca/broker API calls are made.",
        "- No live orders are submitted.",
        "",
        "## Liquidity Guard",
        "",
        "- entry dollar volume below 500k warning",
        "- order pct dollar volume above 1% warning",
        "- rejected/liquidity-skipped signals must be reviewed before paper fills are logged",
        "",
        "## Stop Conditions",
        "",
        "- stale data",
        "- missing data",
        "- signal spike",
        "- liquidity warning spike",
        "- drawdown threshold",
        "- benchmark divergence",
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def _observed_us_market_holidays(year):
    holidays = {
        pd.Timestamp(year=year, month=1, day=1).date(),
        pd.Timestamp(year=year, month=6, day=19).date(),
        pd.Timestamp(year=year, month=7, day=4).date(),
        pd.Timestamp(year=year, month=12, day=25).date(),
    }
    observed = set()
    for day in holidays:
        ts = pd.Timestamp(day)
        if ts.weekday() == 5:
            observed.add((ts - pd.Timedelta(days=1)).date())
        elif ts.weekday() == 6:
            observed.add((ts + pd.Timedelta(days=1)).date())
        else:
            observed.add(day)
    # Thanksgiving: fourth Thursday in November.
    november = pd.date_range(f"{year}-11-01", f"{year}-11-30", freq="D")
    thursdays = [day.date() for day in november if day.weekday() == 3]
    if len(thursdays) >= 4:
        observed.add(thursdays[3])
    return observed


def _latest_expected_us_session(current_date=None):
    current = pd.Timestamp(current_date or pd.Timestamp.now()).date()
    candidate = pd.Timestamp(current)
    for _ in range(10):
        holidays = _observed_us_market_holidays(candidate.year)
        if candidate.weekday() < 5 and candidate.date() not in holidays:
            return candidate.date()
        candidate -= pd.Timedelta(days=1)
    return current


def _next_us_market_session(after_date):
    candidate = pd.Timestamp(after_date).date() + pd.Timedelta(days=1)
    for _ in range(15):
        holidays = _observed_us_market_holidays(candidate.year)
        if candidate.weekday() < 5 and candidate not in holidays:
            return candidate
        candidate += pd.Timedelta(days=1)
    return pd.Timestamp(after_date).date() + pd.Timedelta(days=1)


def _us_trading_days_between(start_date, end_date):
    if start_date is None or end_date is None:
        return 0
    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    if end < start:
        return 0
    count = 0
    current = start
    while current <= end:
        holidays = _observed_us_market_holidays(current.year)
        if current.weekday() < 5 and current not in holidays:
            count += 1
        current += pd.Timedelta(days=1)
    return count


def _pending_order_age_fields(signal_date, latest_daily_date, current_date=None):
    signal_ts = pd.to_datetime(signal_date, errors="coerce")
    current = pd.Timestamp(current_date or pd.Timestamp.now()).date()
    latest_data_ts = pd.to_datetime(latest_daily_date, errors="coerce")
    latest_data_date = latest_data_ts.date() if pd.notna(latest_data_ts) else None
    if pd.isna(signal_ts):
        return {
            "pending_age_calendar_days": 0,
            "pending_age_trading_days": 0,
            "expected_next_open_date": "unknown",
            "pending_stale_warning": False,
            "pending_stale_reason": "invalid_signal_date",
        }
    signal_day = signal_ts.date()
    expected_next_open = _next_us_market_session(signal_day)
    age_calendar = max((current - signal_day).days, 0)
    age_trading = _us_trading_days_between(expected_next_open, current)
    if current < expected_next_open:
        reason = "waiting_for_weekend_or_holiday_next_open"
        stale = False
    elif latest_data_date is None or latest_data_date < expected_next_open:
        reason = "missing_next_open_data"
        stale = age_trading > 3
    else:
        reason = "pending_with_next_open_data_available"
        stale = age_trading > 3
    return {
        "pending_age_calendar_days": int(age_calendar),
        "pending_age_trading_days": int(age_trading),
        "expected_next_open_date": str(expected_next_open),
        "pending_stale_warning": bool(stale),
        "pending_stale_reason": reason,
    }


def _paper_stale_status(paper_report):
    current_date = pd.Timestamp.now().date()
    expected = _latest_expected_us_session(current_date)
    latest_ts = (
        pd.to_datetime(paper_report["signal_date"], errors="coerce").max()
        if paper_report is not None and not paper_report.empty and "signal_date" in paper_report
        else pd.NaT
    )
    latest_date = latest_ts.date() if pd.notna(latest_ts) else None
    stale = bool(latest_date is None or latest_date < expected)
    reason = (
        "no_latest_signal_date"
        if latest_date is None
        else ("latest_signal_date_before_expected_session" if stale else "latest_signal_date_matches_expected_session")
    )
    return {
        "latest_signal_date": str(latest_date) if latest_date is not None else "none",
        "current_date": str(current_date),
        "calendar_check_mode": "fallback_us_weekday_holidays",
        "latest_expected_us_session": str(expected),
        "stale_data_warning": stale,
        "stale_reason": reason,
    }


def _latest_csv_data_date(directory):
    latest = pd.NaT
    directory = Path(directory)
    if not directory.exists():
        return "none"
    for path in directory.glob("*.csv"):
        try:
            sample = pd.read_csv(path, nrows=5)
        except Exception:
            continue
        date_column = next(
            (column for column in sample.columns if "date" in str(column).lower() or "time" in str(column).lower()),
            None,
        )
        try:
            data = pd.read_csv(path, usecols=[date_column]) if date_column else pd.read_csv(path, index_col=0, usecols=[0])
            values = data[date_column] if date_column else data.index
            parsed = pd.to_datetime(values, errors="coerce", utc=True)
            value = parsed.max()
            if pd.notna(value) and (pd.isna(latest) or value > latest):
                latest = value
        except Exception:
            continue
    return str(latest.date()) if pd.notna(latest) else "none"


def _paper_run_counts(paper_report):
    if paper_report is None or paper_report.empty:
        return {"filled": 0, "rejected": 0}
    fill_status = paper_report.get("fill_status", pd.Series(dtype=str)).astype(str).str.upper()
    return {
        "filled": int(fill_status.eq("PAPER_FILLED").sum()),
        "rejected": int(fill_status.eq("REJECTED").sum()),
    }


def _paper_fill_gate_summary(paper_report):
    if paper_report is None or paper_report.empty:
        filled_from_130min = 0
        pending_next_open = 0
    else:
        fill_status = paper_report.get("fill_status", pd.Series(dtype=str)).astype(str).str.upper()
        source = paper_report.get("fill_price_source", pd.Series("", index=paper_report.index)).astype(str)
        filled_from_130min = int((fill_status.eq("PAPER_FILLED") & source.eq("next_130min_open")).sum())
        pending_next_open = int(fill_status.eq("PENDING_NEXT_OPEN").sum())
    status = "OK" if filled_from_130min > 0 and pending_next_open == 0 else ("WARNING" if pending_next_open else "OK")
    return {
        "paper_fill_gate_status": status,
        "paper_fill_gate_daily_required": False,
        "paper_fill_gate_130min_required": True,
        "daily_mismatch_blocks_paper_fill": False,
        "daily_mismatch_blocks_new_signals": True,
        "filled_from_130min_next_open_count": filled_from_130min,
    }


def _daily_preflight_status(paper_report, trade_log_path, daily_dir, intraday_dir, data_update_ran=False):
    stale_status = _paper_stale_status(paper_report)
    fill_counts = paper_report["fill_status"].value_counts().to_dict() if paper_report is not None and not paper_report.empty else {}
    decision_counts = paper_report["user_decision"].value_counts().to_dict() if paper_report is not None and not paper_report.empty else {}
    log_rows = 0
    log_filled_rows = 0
    unique_filled_trades = 0
    open_positions = 0
    ledger_status = "OK"
    if Path(trade_log_path).exists():
        try:
            trade_log = pd.read_csv(trade_log_path)
            log_rows = len(trade_log)
            log_filled_rows = int(
                trade_log.get("fill_status", pd.Series(dtype=str)).astype(str).str.upper().eq("PAPER_FILLED").sum()
            )
            if not trade_log.empty:
                keys = []
                duplicates_found = False
                for _, r in trade_log.iterrows():
                    k = get_stable_key(r)
                    if k in keys:
                        duplicates_found = True
                    keys.append(k)
                unique_filled_trades = len(set(keys))
                if duplicates_found:
                    ledger_status = "WARNING"
                open_positions = len(net_open_positions_from_trade_log(trade_log))
        except Exception:
            log_rows = 0
            log_filled_rows = 0
            unique_filled_trades = 0
            open_positions = 0
            ledger_status = "WARNING"
    run_counts = _paper_run_counts(paper_report)
    fill_gate = _paper_fill_gate_summary(paper_report)
    fill_gate["filled_from_130min_next_open_count"] = max(
        int(fill_gate.get("filled_from_130min_next_open_count", 0) or 0),
        get_last_newly_added_count(),
    )
    pending_next_open = int(fill_counts.get("PENDING_NEXT_OPEN", 0))
    stale = bool(stale_status["stale_data_warning"])
    return {
        "current_date": stale_status["current_date"],
        "latest_daily_data_date": _latest_csv_data_date(daily_dir),
        "latest_130min_data_date": _latest_csv_data_date(intraday_dir),
        "latest_expected_us_market_session": stale_status["latest_expected_us_session"],
        "stale_data_warning": stale,
        "pending_order_count": int(fill_counts.get("PENDING_REVIEW", 0) + pending_next_open),
        "accepted_order_count": int(decision_counts.get("ACCEPT", 0)),
        "pending_next_open_count": pending_next_open,
        "paper_trade_log_rows": log_rows,
        "data_update_ran": bool(data_update_ran),
        "filled_count_this_run": get_last_newly_added_count(),
        "rejected_count_this_run": run_counts["rejected"],
        "total_filled_trades": unique_filled_trades,
        "open_paper_positions": open_positions,
        "paper_ledger_status": ledger_status,
        "skipped_duplicate_fill_count": get_last_skipped_duplicate_fill_count(),
        **fill_gate,
        "next_action": (
            "Wait for next open data; accepted orders remain PENDING_NEXT_OPEN."
            if pending_next_open
            else ("Refresh data before acting." if stale else "Review paper report and order review.")
        ),
    }


def _write_daily_preflight_report(status, path):
    lines = [
        "# Daily Preflight Report",
        "",
        f"- current date: {status['current_date']}",
        f"- latest daily data date: {status['latest_daily_data_date']}",
        f"- latest 130min data date: {status['latest_130min_data_date']}",
        f"- latest expected US market session: {status['latest_expected_us_market_session']}",
        f"- stale_data_warning: {status['stale_data_warning']}",
        f"- pending_order_count: {status['pending_order_count']}",
        f"- accepted_order_count: {status['accepted_order_count']}",
        f"- pending_next_open_count: {status['pending_next_open_count']}",
        f"- paper_fill_gate_status: {status['paper_fill_gate_status']}",
        f"- paper_fill_gate_daily_required: {str(status['paper_fill_gate_daily_required']).lower()}",
        f"- paper_fill_gate_130min_required: {str(status['paper_fill_gate_130min_required']).lower()}",
        f"- daily_mismatch_blocks_paper_fill: {str(status['daily_mismatch_blocks_paper_fill']).lower()}",
        f"- daily_mismatch_blocks_new_signals: {str(status['daily_mismatch_blocks_new_signals']).lower()}",
        f"- filled_from_130min_next_open_count: {status['filled_from_130min_next_open_count']}",
        f"- paper_trade_log rows: {status['paper_trade_log_rows']}",
        f"- next action: {status['next_action']}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_paper_ledger_audit_report(latest_dir, db_path, daily_pipeline_status):
    import sqlite3
    trade_log_path = Path(latest_dir) / "paper_trade_log.csv"

    official_trade_log_rows = 0
    unique_filled_trade_count = 0
    duplicate_filled_trade_count = 0
    duplicate_keys_list = []
    current_open_position_count = 0
    current_open_symbols_list = []

    if trade_log_path.exists():
        try:
            trade_log = pd.read_csv(trade_log_path)
            official_trade_log_rows = len(trade_log)
            if not trade_log.empty:
                keys = []
                for _, r in trade_log.iterrows():
                    keys.append(get_stable_key(r))
                unique_keys = set(keys)
                unique_filled_trade_count = len(unique_keys)
                seen = set()
                dups = set()
                for k in keys:
                    if k in seen:
                        dups.add(k)
                    seen.add(k)
                duplicate_filled_trade_count = len(trade_log) - len(unique_keys)
                duplicate_keys_list = [f"{d[0]}:{d[1]}:{d[2]}:{d[3]}" for d in dups]

                open_positions = net_open_positions_from_trade_log(trade_log)
                current_open_symbols_list = sorted(open_positions["symbol"].dropna().unique().tolist())
                current_open_position_count = len(current_open_symbols_list)
        except Exception:
            pass

    sqlite_unique_paper_trades_count = 0
    sqlite_duplicate_paper_trades_count = 0
    sqlite_status = "OK"

    conn = None
    if Path(db_path).exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT strategy_name, symbol, action, signal_date, notes FROM paper_trades")
            db_rows = cursor.fetchall()
            db_keys = []
            for row in db_rows:
                row_dict = {
                    "strategy_name": row[0],
                    "symbol": row[1],
                    "action": row[2],
                    "signal_date": row[3],
                    "notes": row[4]
                }
                db_keys.append(get_stable_key(row_dict))
            sqlite_unique_paper_trades_count = len(set(db_keys))
            sqlite_duplicate_paper_trades_count = len(db_keys) - sqlite_unique_paper_trades_count
        except Exception as e_sql:
            sqlite_status = "WARNING"
        finally:
            if conn:
                conn.close()

    pending_order_count = daily_pipeline_status.get("pending_order_count", 0)
    accepted_order_count = daily_pipeline_status.get("accepted_order_count", 0)
    hold_review_count = pending_order_count - daily_pipeline_status.get("pending_next_open_count", 0)

    filled_count_this_run_reported = daily_pipeline_status.get("filled_count_this_run", 0)
    filled_count_this_run_recomputed = get_last_newly_added_count()
    skipped_duplicate_fill_count = get_last_skipped_duplicate_fill_count()

    status = "OK" if duplicate_filled_trade_count == 0 and sqlite_duplicate_paper_trades_count == 0 else "WARNING"

    csv_path = Path(latest_dir) / "paper_ledger_audit_report.csv"
    csv_cols = [
        "filled_count_this_run_reported",
        "filled_count_this_run_recomputed",
        "skipped_duplicate_fill_count",
        "official_trade_log_rows",
        "unique_filled_trade_count",
        "sqlite_unique_paper_trades_count",
        "sqlite_duplicate_paper_trades_count",
        "paper_ledger_status"
    ]
    csv_row = {
        "filled_count_this_run_reported": filled_count_this_run_reported,
        "filled_count_this_run_recomputed": filled_count_this_run_recomputed,
        "skipped_duplicate_fill_count": skipped_duplicate_fill_count,
        "official_trade_log_rows": official_trade_log_rows,
        "unique_filled_trade_count": unique_filled_trade_count,
        "sqlite_unique_paper_trades_count": sqlite_unique_paper_trades_count,
        "sqlite_duplicate_paper_trades_count": sqlite_duplicate_paper_trades_count,
        "paper_ledger_status": status
    }
    pd.DataFrame([csv_row])[csv_cols].to_csv(csv_path, index=False)

    md_path = Path(latest_dir) / "paper_ledger_audit_report.md"
    md_content = [
        "# Paper Ledger Audit Report",
        "",
        f"- run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- paper_ledger_status: {status}",
        "",
        "## Metrics Summary",
        "",
        f"- official_trade_log_rows: {official_trade_log_rows}",
        f"- unique_filled_trade_count: {unique_filled_trade_count}",
        f"- duplicate_filled_trade_count: {duplicate_filled_trade_count}",
        f"- duplicate_keys: {', '.join(duplicate_keys_list) or 'none'}",
        f"- current_open_position_count: {current_open_position_count}",
        f"- current_open_symbols: {','.join(current_open_symbols_list) or 'none'}",
        f"- pending_order_count: {pending_order_count}",
        f"- accepted_order_count: {accepted_order_count}",
        f"- hold_review_count: {hold_review_count}",
        f"- filled_count_this_run_reported: {filled_count_this_run_reported}",
        f"- filled_count_this_run_recomputed: {filled_count_this_run_recomputed}",
        f"- skipped_duplicate_fill_count: {skipped_duplicate_fill_count}",
        f"- sqlite_unique_paper_trades_count: {sqlite_unique_paper_trades_count}",
        f"- sqlite_duplicate_paper_trades_count: {sqlite_duplicate_paper_trades_count}",
        "",
    ]
    md_path.write_text("\n".join(md_content), encoding="utf-8")



def _write_execution_bot_readiness_section(latest_dir, summary):
    latest_dir = Path(latest_dir)
    path = latest_dir / "chatgpt_master_report.md"
    data_policy_status = "not_generated"
    data_policy_value = "not_generated"
    pre_submit_checks = summary.get("pre_submit_checks")
    if pre_submit_checks is not None and not getattr(pre_submit_checks, "empty", True):
        row = pre_submit_checks[
            pre_submit_checks.get("check_name", pd.Series(dtype=str)).astype(str).eq("data_policy_operator_review")
        ]
        if not row.empty:
            data_policy_status = str(row.iloc[0].get("status", "not_generated"))
            data_policy_value = str(row.iloc[0].get("value", "not_generated"))
    elif (latest_dir / "pre_submit_readiness_report.csv").exists():
        try:
            pre_submit_checks = pd.read_csv(latest_dir / "pre_submit_readiness_report.csv")
            row = pre_submit_checks[
                pre_submit_checks.get("check_name", pd.Series(dtype=str)).astype(str).eq("data_policy_operator_review")
            ]
            if not row.empty:
                data_policy_status = str(row.iloc[0].get("status", "not_generated"))
                data_policy_value = str(row.iloc[0].get("value", "not_generated"))
        except Exception:
            data_policy_status = "read_error"
            data_policy_value = "read_error"
    section = [
        "## Execution Bot Readiness",
        "",
        f"- execution_preview_status: {summary.get('execution_preview_status', 'not_generated')}",
        "- broker_mode: paper/dry-run",
        "- broker_submit_enabled: false",
        "- live_trading_enabled: false",
        f"- idempotency_phase: {summary.get('idempotency_phase', 'PREVIEW')}",
        f"- idempotency_enabled: {str(bool(summary.get('idempotency_enabled', False))).lower()}",
        f"- order_intents_count: {int(summary.get('order_intents_count', 0) or 0)}",
        f"- risk_pass_count: {int(summary.get('risk_pass_count', 0) or 0)}",
        f"- submit_candidate_risk_pass_count: {int(summary.get('submit_candidate_risk_pass_count', summary.get('risk_pass_count', 0)) or 0)}",
        f"- blocked_count: {int(summary.get('blocked_count', 0) or 0)}",
        f"- duplicate_intent_count: {int(summary.get('duplicate_intent_count', 0) or 0)}",
        f"- preview_duplicate_count: {int(summary.get('preview_duplicate_count', 0) or 0)}",
        f"- submit_duplicate_count: {int(summary.get('submit_duplicate_count', 0) or 0)}",
        f"- skipped_duplicate_count: {int(summary.get('skipped_duplicate_count', 0) or 0)}",
        f"- preview_duplicates_do_not_block_future_submit: {summary.get('preview_duplicates_do_not_block_future_submit', True)}",
        f"- pre_submit_readiness_status: {summary.get('pre_submit_readiness_status', 'not_generated')}",
        f"- blocking_checks_count: {int(summary.get('blocking_checks_count', 0) or 0)}",
        f"- warning_checks_count: {int(summary.get('warning_checks_count', 0) or 0)}",
        f"- would_submit_count: {int(summary.get('would_submit_count', 0) or 0)}",
        "- submit_allowed: false",
        f"- data_policy_operator_review_status: {data_policy_status}",
        f"- data_policy_operator_review_unresolved_count: {data_policy_value}",
        f"- reason submit not allowed: {summary.get('reason_submit_not_allowed', 'Submit intentionally disabled.')}",
        f"- pre_submit_readiness_report_path: {summary.get('pre_submit_readiness_report_path', 'reports/latest/pre_submit_readiness_report.md')}",
        f"- execution_preview_report_path: {summary.get('execution_preview_report_path', 'reports/latest/execution_preview_report.md')}",
        f"- execution_audit_log_path: {summary.get('execution_audit_log_path', 'reports/latest/execution_audit_log.csv')}",
        f"- history_audit_log_path: {summary.get('history_audit_log_path', 'reports/history/execution_audit_log.csv')}",
        "- submit_order_called: false",
        "- post_request_used: false",
        f"- next action: {summary.get('next_action', 'Review execution preview outputs.')}",
        "",
        "## Short Trading Readiness",
        "",
        f"- short_trading_enabled: {str(bool(summary.get('short_trading_enabled', False))).lower()}",
        f"- short_readiness_status: {summary.get('short_readiness_status', 'BLOCK')}",
        f"- short_submit_allowed: {str(bool(summary.get('short_submit_allowed', False))).lower()}",
        "- short_strategy_enabled: false",
        f"- borrow_check_available: {str(bool(summary.get('borrow_check_available', False))).lower()}",
        "- next action: do not enable shorts until long paper workflow is validated",
        "",
    ]
    if path.exists():
        text = path.read_text(encoding="utf-8")
        marker = "## Execution Bot Readiness"
        if marker in text:
            text = text[: text.index(marker)].rstrip() + "\n\n"
    else:
        text = "# ChatGPT Master Report\n\n"
    path.write_text(text.rstrip() + "\n\n" + "\n".join(section), encoding="utf-8")


def run_execution_preview_check(data_folder="hisseler", report_output_dir="reports", make_chatgpt_report=False, pre_submit_check=False):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    summary = run_execution_preview(latest_dir, pre_submit_check=pre_submit_check)
    if make_chatgpt_report:
        _write_execution_bot_readiness_section(latest_dir, summary)
    print(f"Order intents: {latest_dir / 'order_intents.csv'}")
    print(f"Risk check report: {latest_dir / 'order_risk_check_report.md'}")
    print(f"Execution preview report: {latest_dir / 'execution_preview_report.md'}")
    print("broker_submit_enabled: false")
    print("live_trading_enabled: false")

    # SQLite mirror block
    db_path = resolve_project_path("state/trading_state.sqlite3", data_folder)
    run_id = f"run_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"

    try:
        init_state_db(str(db_path))

        # 1. Pipeline run
        mode_str = "pre-submit-check" if pre_submit_check else "execution-preview"
        append_pipeline_run(
            str(db_path),
            run_id=run_id,
            run_datetime=pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
            command=f"python bot.py --mode {mode_str}",
            mode=mode_str,
            status="SUCCESS",
            strategy_metrics_changed=False,
            benchmark_changed=False,
            regression_guard_status="OK",
            total_runtime_sec=0.0
        )

        # 2. Order intents
        intents_path = latest_dir / "order_intents.csv"
        if intents_path.exists():
            try:
                intents_df = pd.read_csv(intents_path)
                append_order_intents(str(db_path), run_id, intents_df)
            except Exception as e_int:
                print(f"WARNING: SQLite append_order_intents failed to read CSV: {e_int}")

        # 3. Execution audit
        audit_path = latest_dir / "execution_audit_log.csv"
        if audit_path.exists():
            try:
                audit_df = pd.read_csv(audit_path)
                append_execution_audit(str(db_path), run_id, audit_df)
            except Exception as e_aud:
                print(f"WARNING: SQLite append_execution_audit failed to read CSV: {e_aud}")

        # 4. Pre-submit safety checks
        pre_submit_path = latest_dir / "pre_submit_readiness_report.csv"
        if pre_submit_path.exists():
            try:
                pre_submit_df = pd.read_csv(pre_submit_path)
                append_pre_submit_checks(str(db_path), run_id, pre_submit_df)
            except Exception as e_pre:
                print(f"WARNING: SQLite append_pre_submit_checks failed to read CSV: {e_pre}")

        # 5. Short readiness checks
        short_path = latest_dir / "short_readiness_report.csv"
        if short_path.exists():
            try:
                short_df = pd.read_csv(short_path)
                append_short_readiness_checks(str(db_path), run_id, short_df)
            except Exception as e_sh:
                print(f"WARNING: SQLite append_short_readiness_checks failed to read CSV: {e_sh}")

        # Write state report files
        _write_sqlite_state_report(latest_dir, db_path)

    except Exception as e_sql:
        print(f"WARNING: SQLite mirror execution failed: {e_sql}")

    return summary


def run_paper_ops_dashboard(data_folder="hisseler", report_output_dir="reports"):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    dashboard = write_paper_ops_dashboard(latest_dir)
    print(f"Paper ops dashboard: {latest_dir / 'paper_ops_dashboard.md'}")
    print(f"Paper ops dashboard json: {latest_dir / 'paper_ops_dashboard.json'}")
    print(f"next_action: {dashboard.get('next_action', '')}")
    return dashboard


def run_intraday_paper_monitor(data_folder="hisseler", report_output_dir="reports"):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    data_path = Path(data_folder)
    if not data_path.is_absolute():
        data_path = Path(__file__).resolve().parent / data_path
    result = write_intraday_paper_monitor(data_path, latest_dir)
    print(f"Intraday paper monitor csv: {result['csv_path']}")
    print(f"Intraday paper monitor md: {result['md_path']}")
    print(f"Intraday paper monitor json: {result['json_path']}")
    print(f"open_positions_count: {result['summary'].get('open_positions_count', 0)}")
    print(f"accepted_pending_orders_count: {result['summary'].get('accepted_pending_orders_count', 0)}")
    return result


def run_paper_decision_inbox(data_folder="hisseler", report_output_dir="reports"):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    result = write_paper_decision_inbox(latest_dir)
    print(f"Paper decision inbox: {latest_dir / 'paper_decision_inbox.md'}")
    print(f"Paper decision inbox csv: {latest_dir / 'paper_decision_inbox.csv'}")
    print(f"total_decision_candidates: {result.get('summary', {}).get('total_decision_candidates', 0)}")
    return result


def run_apply_paper_order_review_decisions(data_folder="hisseler", report_output_dir="reports"):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    pending = read_pending_orders(latest_dir)
    review = read_order_review(latest_dir)
    decisions = {}
    for _, row in review.iterrows():
        decisions[_paper_order_key(row)] = str(row.get("user_decision", "HOLD_REVIEW")).strip().upper() or "HOLD_REVIEW"

    updated = pending.copy()
    changed_rows = []
    if not updated.empty and decisions:
        for index, row in updated.iterrows():
            key = _paper_order_key(row)
            if key not in decisions:
                continue
            before = str(row.get("user_decision", "")).strip().upper()
            after = decisions[key]
            if before == after:
                continue
            updated.loc[index, "user_decision"] = after
            changed_rows.append(
                {
                    "symbol": row.get("symbol", ""),
                    "strategy_name": row.get("strategy_name", ""),
                    "action": row.get("action", ""),
                    "old_user_decision": before,
                    "new_user_decision": after,
                    "status": row.get("status", ""),
                }
            )
    write_pending_orders(latest_dir, updated)
    accepted = (
        updated[
            updated.get("user_decision", pd.Series("", index=updated.index)).astype(str).str.upper().eq("ACCEPT")
            & updated.get("status", pd.Series("", index=updated.index)).astype(str).str.upper().isin(["PENDING", "PENDING_NEXT_OPEN"])
        ].copy()
        if not updated.empty
        else pd.DataFrame()
    )
    summary = {
        "review_rows": int(len(review)),
        "pending_rows": int(len(updated)),
        "changed_rows": int(len(changed_rows)),
        "accepted_pending_rows": int(len(accepted)),
        "state_mutated": True,
        "trade_log_mutated": False,
        "broker_submit_enabled": False,
        "live_trading_enabled": False,
        "short_trading_enabled": False,
        "submit_order_called": False,
        "post_request_used": False,
    }
    report_rows = changed_rows or [
        {
            "symbol": "none",
            "strategy_name": "",
            "action": "",
            "old_user_decision": "",
            "new_user_decision": "",
            "status": "",
        }
    ]
    pd.DataFrame(report_rows).to_csv(latest_dir / "paper_decision_apply_report.csv", index=False)
    (latest_dir / "paper_decision_apply_report.md").write_text(
        "\n".join(
            [
                "# Paper Decision Apply Report",
                "",
                f"- review_rows: {summary['review_rows']}",
                f"- pending_rows: {summary['pending_rows']}",
                f"- changed_rows: {summary['changed_rows']}",
                f"- accepted_pending_rows: {summary['accepted_pending_rows']}",
                "- trade_log_mutated: false",
                "- broker_submit_enabled: false",
                "- live_trading_enabled: false",
                "- short_trading_enabled: false",
                "- submit_order_called: false",
                "- post_request_used: false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Paper decision apply: {latest_dir / 'paper_decision_apply_report.md'}")
    print(f"changed_rows: {summary['changed_rows']}")
    print(f"accepted_pending_rows: {summary['accepted_pending_rows']}")
    print("broker_submit_enabled: false")
    print("live_trading_enabled: false")
    return {"summary": summary, "changed_rows": changed_rows, "latest_dir": str(latest_dir)}


def run_paper_risk_dashboard(data_folder="hisseler", report_output_dir="reports"):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    result = write_paper_risk_dashboard(latest_dir)
    dashboard = result.get("dashboard", {})
    risk = dashboard.get("risk_limits", {}) if isinstance(dashboard, dict) else {}
    print(f"Paper risk dashboard: {latest_dir / 'paper_risk_dashboard.md'}")
    print(f"Paper risk dashboard csv: {latest_dir / 'paper_risk_dashboard.csv'}")
    print(f"Paper risk dashboard json: {latest_dir / 'paper_risk_dashboard.json'}")
    print(f"risk_status: {risk.get('risk_status', '')}")
    return result


def run_risk_engine_dashboard(data_folder="hisseler", report_output_dir="reports"):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    data_path = Path(data_folder)
    if not data_path.is_absolute():
        data_path = Path(__file__).resolve().parent / data_path
    daily_dir = data_path / "daily"
    result = write_risk_engine_report(latest_dir, daily_dir)
    report = result.get("report", {})
    heat = report.get("portfolio_heat", {})
    loss_limit = report.get("daily_loss_limit", {})
    kill_switch = report.get("kill_switch", {})
    print(f"Risk engine report: {latest_dir / 'risk_engine_report.md'}")
    print(f"Risk engine report csv: {latest_dir / 'risk_engine_report.csv'}")
    print(f"Risk engine report json: {latest_dir / 'risk_engine_report.json'}")
    print(f"portfolio_heat_pct: {heat.get('heat_pct', '')}")
    print(f"daily_loss_limit_breached: {loss_limit.get('breached', '')}")
    print(f"kill_switch_advisory_triggered: {kill_switch.get('advisory_triggered', '')}")
    return result


def run_closed_trade_report(data_folder="hisseler", report_output_dir="reports"):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    result = write_closed_trade_report(latest_dir)
    summary = result.get("summary", {})
    print(f"Closed trade report: {latest_dir / 'closed_trade_report.md'}")
    print(f"Closed trade report csv: {latest_dir / 'closed_trade_report.csv'}")
    print(f"Closed trade report json: {latest_dir / 'closed_trade_report.json'}")
    print(f"closed_trade_count: {summary.get('closed_trade_count', 0)}")
    print(f"total_realized_pnl: {summary.get('total_realized_pnl', 0)}")
    return result


def run_no_lookahead_audit(data_folder="hisseler", report_output_dir="reports"):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    data_path = Path(data_folder)
    if not data_path.is_absolute():
        data_path = Path(__file__).resolve().parent / data_path
    result = write_no_lookahead_audit(latest_dir, data_path)
    summary = result.get("summary", {})
    print(f"No-lookahead audit: {latest_dir / 'no_lookahead_audit.md'}")
    print(f"No-lookahead audit csv: {latest_dir / 'no_lookahead_audit.csv'}")
    print(f"No-lookahead audit json: {latest_dir / 'no_lookahead_audit.json'}")
    print(f"audit_status: {summary.get('audit_status', '')}")
    return result


def run_execution_cost_preview(data_folder="hisseler", report_output_dir="reports"):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    result = write_execution_cost_preview(latest_dir)
    summary = result.get("summary", {})
    print(f"Execution cost preview: {latest_dir / 'execution_cost_preview.md'}")
    print(f"Execution cost preview csv: {latest_dir / 'execution_cost_preview.csv'}")
    print(f"Execution cost preview json: {latest_dir / 'execution_cost_preview.json'}")
    print(f"total_execution_cost: {summary.get('total_execution_cost', 0)}")
    return result


def run_sqlite_state_reconciliation(data_folder="hisseler", report_output_dir="reports"):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    db_path = resolve_project_path("state/trading_state.sqlite3", data_folder)
    result = write_sqlite_state_reconciliation(latest_dir, db_path)
    summary = result.get("summary", {})
    print(f"SQLite state reconciliation: {latest_dir / 'sqlite_state_reconciliation.md'}")
    print(f"SQLite state reconciliation csv: {latest_dir / 'sqlite_state_reconciliation.csv'}")
    print(f"SQLite state reconciliation json: {latest_dir / 'sqlite_state_reconciliation.json'}")
    print(f"reconciliation_status: {summary.get('reconciliation_status', '')}")
    return result



def validate_v2_clean_dataset(data_folder):
    import json
    import hashlib
    folder = Path(data_folder)
    latest_manifest = folder / "manifests" / "latest_manifest.json"
    if not latest_manifest.exists():
        raise ValueError("V2_DATASET_NOT_VALIDATED: latest_manifest.json is missing")

    try:
        with open(latest_manifest, "r", encoding="utf-8") as f:
            latest = json.load(f)
    except Exception as e:
        raise ValueError(f"V2_DATASET_NOT_VALIDATED: failed to parse manifest: {e}")
    relative = latest.get("immutable_manifest_relative_path")
    if not relative or Path(relative).is_absolute():
        raise ValueError("V2_DATASET_NOT_VALIDATED: latest manifest is not immutable pointer")
    immutable_path = folder / relative
    if not immutable_path.exists():
        raise ValueError("V2_DATASET_NOT_VALIDATED: immutable run manifest is missing")
    actual_manifest_hash = hashlib.sha256(immutable_path.read_bytes()).hexdigest()
    if latest.get("immutable_manifest_sha256") != actual_manifest_hash:
        raise ValueError("V2_DATASET_NOT_VALIDATED: immutable manifest SHA-256 mismatch")
    try:
        manifest = json.loads(immutable_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"V2_DATASET_NOT_VALIDATED: immutable manifest parse failed: {e}")
    if latest.get("acquisition_run_id") != manifest.get("acquisition_run_id"):
        raise ValueError("V2_DATASET_NOT_VALIDATED: acquisition run id mismatch")
    profile = manifest.get("dataset_profile", "rs_v2_clean")
    if profile == "rs_v2_tiingo":
        try:
            try:
                from .tiingo_provider import V2_TIINGO_TARGET_END, validate_v2_tiingo_manifest_and_files
            except ImportError:
                from tiingo_provider import V2_TIINGO_TARGET_END, validate_v2_tiingo_manifest_and_files
            validate_v2_tiingo_manifest_and_files(folder, manifest)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"V2_DATASET_NOT_VALIDATED: shared manifest validator failed: {e}")
        if manifest.get("strategy_identifier") != "rs_m126_top30_sma100_no_regime_v2_clean_stock":
            raise ValueError("V2_DATASET_NOT_VALIDATED: strategy identifier mismatch")
        if manifest.get("target_start_date") != "2020-07-27":
            raise ValueError("V2_DATASET_NOT_VALIDATED: target start date mismatch")
        if manifest.get("target_end_date", "") < V2_TIINGO_TARGET_END:
            raise ValueError("V2_DATASET_NOT_VALIDATED: target end date too old")
        if manifest.get("adjustment") != "adjusted":
            raise ValueError("V2_DATASET_NOT_VALIDATED: adjustment is not adjusted")
        if manifest.get("feed") != "tiingo":
            raise ValueError("V2_DATASET_NOT_VALIDATED: feed is not tiingo")
        if manifest.get("candidate_universe_hash") != "026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933":
            raise ValueError("V2_DATASET_NOT_VALIDATED: candidate universe hash mismatch")
        if manifest.get("candidate_count") != 177:
            raise ValueError("V2_DATASET_NOT_VALIDATED: candidate count mismatch")
        if manifest.get("logical_acquisition_item_count") != 179:
            raise ValueError("V2_DATASET_NOT_VALIDATED: expected 179 daily-only items")
        if manifest.get("status") != "COMPLETE_VALIDATED":
            raise ValueError("V2_DATASET_NOT_VALIDATED: manifest status is not COMPLETE_VALIDATED")
        return
    if profile == "rs_v2_sip":
        try:
            try:
                from .vericekici_updated import validate_v2_sip_manifest_and_files
            except ImportError:
                from vericekici_updated import validate_v2_sip_manifest_and_files
            validate_v2_sip_manifest_and_files(folder, manifest)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"V2_DATASET_NOT_VALIDATED: shared manifest validator failed: {e}")
        if manifest.get("strategy_identifier") != "rs_m126_top30_sma100_no_regime_v2_clean_stock":
            raise ValueError("V2_DATASET_NOT_VALIDATED: strategy identifier mismatch")
        if manifest.get("target_start_date") != "2020-07-27":
            raise ValueError("V2_DATASET_NOT_VALIDATED: target start date mismatch")
        if manifest.get("target_end_date", "") < "2026-07-07":
            raise ValueError("V2_DATASET_NOT_VALIDATED: target end date too old")
        if manifest.get("adjustment") != "all":
            raise ValueError("V2_DATASET_NOT_VALIDATED: adjustment is not all")
        if manifest.get("feed") != "sip":
            raise ValueError("V2_DATASET_NOT_VALIDATED: feed is not sip")
        if manifest.get("candidate_universe_hash") != "026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933":
            raise ValueError("V2_DATASET_NOT_VALIDATED: candidate universe hash mismatch")
        if manifest.get("candidate_count") != 177:
            raise ValueError("V2_DATASET_NOT_VALIDATED: candidate count mismatch")
        if manifest.get("status") != "COMPLETE_VALIDATED":
            raise ValueError("V2_DATASET_NOT_VALIDATED: manifest status is not COMPLETE_VALIDATED")
    else:
        try:
            try:
                from .vericekici_updated import validate_v2_manifest_and_files
            except ImportError:
                from vericekici_updated import validate_v2_manifest_and_files
            validate_v2_manifest_and_files(folder, manifest)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"V2_DATASET_NOT_VALIDATED: shared manifest validator failed: {e}")
        if manifest.get("strategy_identifier") != "rs_m126_top30_sma100_no_regime_v2_clean_stock":
            raise ValueError("V2_DATASET_NOT_VALIDATED: strategy identifier mismatch")
        if manifest.get("target_start_date") != "2020-07-27" or manifest.get("target_end_date") != "2026-07-07":
            raise ValueError("V2_DATASET_NOT_VALIDATED: target period mismatch")
        if manifest.get("dataset_profile") != "rs_v2_clean":
            raise ValueError("V2_DATASET_NOT_VALIDATED: dataset_profile is not rs_v2_clean")
        if manifest.get("adjustment") != "all":
            raise ValueError("V2_DATASET_NOT_VALIDATED: adjustment is not all")
        if manifest.get("feed") != "iex":
            raise ValueError("V2_DATASET_NOT_VALIDATED: feed is not iex")
        if manifest.get("candidate_universe_hash") != "026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933":
            raise ValueError("V2_DATASET_NOT_VALIDATED: candidate universe hash mismatch")
        if manifest.get("candidate_count") != 177:
            raise ValueError("V2_DATASET_NOT_VALIDATED: candidate count mismatch")
        if manifest.get("status") != "COMPLETE_VALIDATED":
            raise ValueError("V2_DATASET_NOT_VALIDATED: manifest status is not COMPLETE_VALIDATED")

    etfs = {
        "DIA", "IBB", "IWM", "QQQ", "RSP", "SMH", "SOXX", "SPY", "VTI", "XBI",
        "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV",
        "XLY", "XRT"
    }

    files = manifest.get("files", [])
    if len(files) != 358 or manifest.get("logical_acquisition_item_count") != 358:
        raise ValueError(f"V2_DATASET_NOT_VALIDATED: expected 358 files in manifest, got {len(files)}")
    if manifest.get("successful_item_count") != 358 or manifest.get("failed_item_count") != 0:
        raise ValueError("V2_DATASET_NOT_VALIDATED: manifest count reconciliation failed")
    seen, role_counts, symbol_counts = set(), {"CANDIDATE": 0, "PRIMARY_BENCHMARK": 0, "SECONDARY_BENCHMARK": 0}, {"QQQ": 0, "SPY": 0}
    for file_record in files:
        symbol, role = file_record.get("symbol"), file_record.get("asset_role")
        rel_path, expected_hash = file_record.get("output_relative_path"), file_record.get("sha256")
        key = (symbol, file_record.get("timeframe"), rel_path)
        if key in seen or not all(key):
            raise ValueError("V2_DATASET_NOT_VALIDATED: duplicate or incomplete logical record")
        seen.add(key)
        if role not in role_counts:
            raise ValueError("V2_DATASET_NOT_VALIDATED: unknown asset role")
        role_counts[role] += 1
        if symbol in symbol_counts:
            symbol_counts[symbol] += 1
        expected_feed = "sip" if profile == "rs_v2_sip" else "iex"
        if file_record.get("feed") != expected_feed or file_record.get("adjustment") != "all" or file_record.get("request_status") != "SUCCESS" or file_record.get("data_status") != "VALID":
            raise ValueError("V2_DATASET_NOT_VALIDATED: invalid request/data status or policy")

        if role == "CANDIDATE":
            if symbol in etfs or symbol in {"QQQ", "SPY"}:
                raise ValueError(f"V2_DATASET_NOT_VALIDATED: blocked symbol {symbol} listed as CANDIDATE")
        elif role in {"PRIMARY_BENCHMARK", "SECONDARY_BENCHMARK"}:
            expected_symbol = "QQQ" if role == "PRIMARY_BENCHMARK" else "SPY"
            if symbol != expected_symbol or not rel_path.startswith("benchmarks/"):
                raise ValueError(f"V2_DATASET_NOT_VALIDATED: ordinary symbol {symbol} listed as BENCHMARK")
        if role == "CANDIDATE" and rel_path.startswith("benchmarks/"):
            raise ValueError("V2_DATASET_NOT_VALIDATED: candidate path in benchmarks")

        file_path = folder / rel_path
        if not file_path.exists():
            raise ValueError(f"V2_DATASET_NOT_VALIDATED: file {rel_path} not found")

        content = file_path.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(f"V2_DATASET_NOT_VALIDATED: SHA-256 hash mismatch for {rel_path}")

        try:
            import pandas as pd
            df = pd.read_csv(file_path)
            required_cols = {"Date", "Open", "High", "Low", "Close", "Volume"}
            if not required_cols.issubset(df.columns):
                raise ValueError(f"V2_DATASET_NOT_VALIDATED: missing required columns in {rel_path}")
            if df[["Open", "High", "Low", "Close", "Volume"]].isna().any().any():
                raise ValueError(f"V2_DATASET_NOT_VALIDATED: null values in OHLCV columns in {rel_path}")
            dates = pd.to_datetime(df["Date"], errors="raise", utc=True)
            if df.empty or not dates.is_monotonic_increasing or dates.duplicated().any() or not file_record.get("timezone"):
                raise ValueError(f"V2_DATASET_NOT_VALIDATED: invalid timestamp integrity in {rel_path}")
        except Exception as e:
            raise ValueError(f"V2_DATASET_NOT_VALIDATED: failed to parse/verify CSV {rel_path}: {e}")
        if file_record.get("timeframe") == "130min":
            raw_path = file_record.get("source_raw_5min_relative_path", "")
            source = folder / raw_path
            if file_record.get("derivation_type") != "AGGREGATED_FROM_RAW_5MIN" or file_record.get("derivation_status") != "SUCCESS" or not raw_path or not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != file_record.get("source_raw_5min_sha256"):
                raise ValueError("V2_DATASET_NOT_VALIDATED: raw-to-130min lineage mismatch")
    if role_counts != {"CANDIDATE": 354, "PRIMARY_BENCHMARK": 2, "SECONDARY_BENCHMARK": 2} or symbol_counts != {"QQQ": 2, "SPY": 2}:
        raise ValueError("V2_DATASET_NOT_VALIDATED: role count mismatch")


def run_strategy_experiments(
    data_folder="hisseler",
    experiments=None,
    run_profile="fast",
    quick_mode=True,
    compact_report=True,
    compact_report_name="strategy_compact_report.csv",
    compact_markdown_name="strategy_compact_report.md",
    report_output_dir="reports",
    reports="basic",
    reuse_existing_report=False,
    write_yearly_performance_report=None,
    write_rs_robustness_report=None,
    write_candidate_summary_report=None,
    write_performance_profile=None,
    parallel=False,
    workers="auto",
    run_robustness=False,
    run_oos=False,
    run_attribution=False,
    run_cost_stress=False,
    run_liquidity_guard_sensitivity=False,
    make_chatgpt_report=False,
    paper_report=False,
    paper_strategy="main",
    paper_include_watchlist=False,
    paper_max_new_positions_per_day=30,
    command=None,
    intraday_data_dir=None,
    daily_data_dir=None,
    raw_1min_data_dir=None,
    intraday_timeframe=INTRADAY_TIMEFRAME,
    daily_timeframe=DAILY_TIMEFRAME,
    start_date=None,
    end_date=None,
    allowed_symbols=None,
    force_rebuild_prepared_cache=False,
    **kwargs,
):
    is_v2 = (data_folder == "testdata/research_data/rs_v2_clean_market_data" or
             (experiments and any(exp.get("name") == "rs_m126_top30_sma100_no_regime_v2_clean_stock" for exp in experiments)))
    if is_v2:
        validate_v2_clean_dataset(data_folder)

    min_dollar_volume = float(kwargs.get("min_dollar_volume", 0.0) or 0.0)
    total_started = _now()
    cpu_started = time.process_time()
    report_mode = str(reports or "basic").lower()
    if report_mode not in {"basic", "full"}:
        raise ValueError("--reports 'basic' veya 'full' olmali.")
    if report_mode == "full":
        run_oos = True
        run_attribution = True
        run_cost_stress = True
    if write_yearly_performance_report is None:
        write_yearly_performance_report = report_mode == "full"
    if write_rs_robustness_report is None:
        write_rs_robustness_report = bool(run_robustness)
    if write_candidate_summary_report is None:
        write_candidate_summary_report = report_mode == "full"
    if write_performance_profile is None:
        write_performance_profile = True
    parallel_enabled = bool(parallel)
    worker_count = _resolve_worker_count(parallel_enabled, workers)
    _warn_onedrive_path(Path(data_folder).resolve())
    latest_dir, run_dir = _report_output_paths(report_output_dir, data_folder)
    _remove_stale_performance_profile(latest_dir)
    if report_mode == "basic":
        _remove_optional_report_files(latest_dir, run_dir)
    if reuse_existing_report:
        return pd.read_csv(latest_dir / compact_report_name)
    if not experiments:
        raise ValueError("experiments must be provided for modular validation runs.")
    latest_csv_path = latest_dir / compact_report_name
    latest_markdown_path = latest_dir / compact_markdown_name
    run_csv_path = run_dir / compact_report_name
    run_markdown_path = run_dir / compact_markdown_name
    stage_started = _stage_started("data loading")
    intraday_dir, daily_dir, raw_dir = _resolve_data_dirs(
        data_folder,
        intraday_data_dir=intraday_data_dir,
        daily_data_dir=daily_data_dir,
        raw_1min_data_dir=raw_1min_data_dir,
    )
    intraday_symbols = {
        _symbol_from_file(path, "_130min.csv")
        for path in intraday_dir.glob("*_130min.csv")
    }
    daily_symbols = {
        _symbol_from_file(path, "_1D.csv")
        for path in daily_dir.glob("*_1D.csv")
    }
    symbols = sorted(intraday_symbols | daily_symbols)
    if allowed_symbols:
        allowed = {str(symbol).upper() for symbol in allowed_symbols}
        symbols = [symbol for symbol in symbols if symbol in allowed]
    if not symbols:
        raise ValueError(
            f"No symbols found in {intraday_dir} (*_130min.csv) or {daily_dir} (*_1D.csv)."
        )
    data_loading_sec = _stage_done("data loading", stage_started)
    stage_started = _stage_started("data audit")
    audit = _write_data_audit(symbols, daily_dir, intraday_dir, latest_dir, run_dir)
    data_audit_sec = _stage_done("data audit", stage_started)
    stage_started = _stage_started("prepared cache")
    csv_timing = _TimingCollector()
    prepared, cache_path, cache_rebuilt = _load_or_build_prepared_cache(
        data_folder,
        intraday_dir,
        daily_dir,
        intraday_timeframe,
        daily_timeframe,
        start_date,
        end_date,
        symbols,
        force_rebuild=force_rebuild_prepared_cache,
        timing=csv_timing,
    )
    prepared_cache_sec = _stage_done("prepared cache", stage_started)
    print(f"[stage] prepared cache {'miss' if cache_rebuilt else 'hit'}")
    asset_data = prepared["asset_data"]
    cache_summary = prepared.get("cache_summary", {})
    cache_summary["prepared_cache_rebuilt"] = cache_rebuilt
    cache_summary["prepared_cache_hit"] = bool(cache_summary.get("prepared_disk_cache_hits", 0) > 0)
    benchmark_data = prepared.get("benchmark_data")
    if benchmark_data is None:
        benchmark_data = _load_daily_benchmark(daily_dir, start_date, end_date)
        prepared["benchmark_data"] = benchmark_data
    print(f"Experiment groups count: {len(experiments)}")
    print(f"Intraday data dir: {intraday_dir}")
    print(f"Daily data dir: {daily_dir}")
    print(f"Raw 1min data dir (not used by backtest): {raw_dir}")
    print(f"Prepared feature cache: {cache_path.name}; rebuilt={cache_rebuilt}")
    profile_rows = []
    experiment_runtime_rows = []
    raw_results = {}
    tasks = _build_experiment_tasks(
        experiments,
        data_folder,
        run_profile,
        quick_mode,
        intraday_dir,
        daily_dir,
        raw_dir,
        intraday_timeframe,
        daily_timeframe,
        cache_rebuilt,
        include_raw=write_yearly_performance_report,
        min_dollar_volume=min_dollar_volume,
    )
    baseline_results, baseline_parallel_runtime_sec = _run_experiment_batch(
        tasks,
        asset_data,
        benchmark_data,
        cache_path,
        parallel_enabled=parallel_enabled,
        workers=worker_count,
    )
    rows = [result["row"] for result in baseline_results]
    for result in baseline_results:
        if result.get("raw"):
            raw_results[result["experiment_name"]] = result["raw"]
        experiment_runtime_rows.append(
            {
                "stage": "strategy_runtime",
                "experiment_name": result["experiment_name"],
                "duration_sec": round(result["runtime_sec"], 4),
                "strategy_runtime_sec": round(result["runtime_sec"], 4),
                "rolling_high_low_sec": result["row"].get("rolling_high_low_sec", ""),
                "signal_generation_sec": result["row"].get("signal_generation_sec", ""),
                "ranking_sec": result["row"].get("ranking_sec", ""),
                "portfolio_sim_sec": result["row"].get("portfolio_sim_sec", ""),
                "exits_sec": result["row"].get("exits_sec", ""),
                "diagnostics_sec": result["row"].get("diagnostics_sec", ""),
                "symbols_loaded": result.get("symbols_loaded", 0),
                "rows_loaded": cache_summary.get("rows_loaded", ""),
                "cache_hit_count": cache_summary.get("prepared_disk_cache_hits", 0),
                "cache_miss_count": cache_summary.get("prepared_disk_cache_misses", 0),
            }
        )
    report = pd.DataFrame(rows)
    report = _add_compact_scores(report)
    report = report.sort_values("Compact Score", ascending=False, na_position="last")
    stage_started = _stage_started("report write")
    report.to_csv(latest_csv_path, index=False)
    report.to_csv(run_csv_path, index=False)
    if compact_report:
        generated_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        _write_compact_markdown(report, latest_markdown_path, None, generated_at)
        _write_compact_markdown(report, run_markdown_path, None, generated_at)
    for directory in [latest_dir, run_dir]:
        _write_benchmark_audit(report, directory / "benchmark_audit_report.md", cache_path)
    report_write_sec = _stage_done("report write", stage_started)
    robustness = None
    robustness_report_sec = 0.0
    robustness_parallel_runtime_sec = 0.0
    oos = None
    attribution = None
    attribution_summary = None
    cost_stress = None
    liquidity_guard_sensitivity = None
    paper_signal_report = None
    oos_report_sec = 0.0
    oos_parallel_runtime_sec = 0.0
    if run_oos:
        stage_started = _stage_started("oos walkforward")
        oos, oos_parallel_runtime_sec = _run_oos_walkforward_report(
            experiments,
            data_folder,
            run_profile,
            quick_mode,
            intraday_dir,
            daily_dir,
            raw_dir,
            intraday_timeframe,
            daily_timeframe,
            cache_rebuilt,
            asset_data,
            benchmark_data,
            cache_path,
            parallel_enabled,
            worker_count,
        )
        for directory in [latest_dir, run_dir]:
            _write_oos_walkforward_report(
                oos,
                directory / "oos_walkforward_report.csv",
                directory / "oos_walkforward_report.md",
            )
        oos_report_sec = _stage_done("oos walkforward", stage_started)
    attribution_report_sec = 0.0
    attribution_parallel_runtime_sec = 0.0
    if run_attribution:
        stage_started = _stage_started("candidate attribution")
        attribution, attribution_summary, attribution_parallel_runtime_sec = (
            _run_candidate_attribution_report(
                experiments,
                data_folder,
                run_profile,
                quick_mode,
                intraday_dir,
                daily_dir,
                raw_dir,
                intraday_timeframe,
                daily_timeframe,
                cache_rebuilt,
                asset_data,
                benchmark_data,
                cache_path,
                parallel_enabled,
                worker_count,
            )
        )
        for directory in [latest_dir, run_dir]:
            _write_candidate_attribution_report(
                attribution,
                attribution_summary,
                directory / "candidate_attribution_report.csv",
                directory / "candidate_attribution_report.md",
            )
        attribution_report_sec = _stage_done("candidate attribution", stage_started)
    cost_stress_report_sec = 0.0
    cost_stress_parallel_runtime_sec = 0.0
    if run_cost_stress:
        stage_started = _stage_started("cost slippage stress")
        cost_stress, cost_stress_parallel_runtime_sec = _run_cost_slippage_stress_report(
            experiments,
            data_folder,
            run_profile,
            quick_mode,
            intraday_dir,
            daily_dir,
            raw_dir,
            intraday_timeframe,
            daily_timeframe,
            cache_rebuilt,
            asset_data,
            benchmark_data,
            cache_path,
            parallel_enabled,
            worker_count,
        )
        for directory in [latest_dir, run_dir]:
            _write_cost_slippage_stress_report(
                cost_stress,
                directory / "cost_slippage_stress_report.csv",
                directory / "cost_slippage_stress_report.md",
            )
        cost_stress_report_sec = _stage_done("cost slippage stress", stage_started)
    liquidity_guard_sensitivity_report_sec = 0.0
    liquidity_guard_sensitivity_parallel_runtime_sec = 0.0
    if run_liquidity_guard_sensitivity:
        stage_started = _stage_started("liquidity guard sensitivity")
        liquidity_guard_sensitivity, liquidity_guard_sensitivity_parallel_runtime_sec = (
            _run_liquidity_guard_sensitivity_report(
                experiments,
                data_folder,
                run_profile,
                quick_mode,
                intraday_dir,
                daily_dir,
                raw_dir,
                intraday_timeframe,
                daily_timeframe,
                cache_rebuilt,
                asset_data,
                benchmark_data,
                cache_path,
                parallel_enabled,
                worker_count,
            )
        )
        for directory in [latest_dir, run_dir]:
            _write_liquidity_guard_sensitivity_report(
                liquidity_guard_sensitivity,
                directory / "liquidity_guard_sensitivity_report.csv",
                directory / "liquidity_guard_sensitivity_report.md",
            )
        liquidity_guard_sensitivity_report_sec = _stage_done(
            "liquidity guard sensitivity",
            stage_started,
        )
    if run_cost_stress:
        for directory in [latest_dir, run_dir]:
            _write_final_candidate_decision_report(
                report,
                oos,
                attribution_summary,
                cost_stress,
                directory / "final_candidate_decision_report.md",
            )
    paper_report_sec = 0.0
    daily_pipeline_status = None
    data_precheck_result = None
    operational_health = None
    if paper_report:
        reset_paper_ledger_counters()
        stage_started = _stage_started("paper report")
        previous_paper_signal_report = _load_previous_paper_signal_report(
            latest_dir / "paper_signal_report.csv"
        )
        paper_signal_report = _run_paper_signal_report(
            experiments,
            data_folder,
            run_profile,
            quick_mode,
            intraday_dir,
            daily_dir,
            raw_dir,
            intraday_timeframe,
            daily_timeframe,
            cache_rebuilt,
            asset_data,
            benchmark_data,
            latest_dir / "paper_trade_log.csv",
            paper_strategy_mode=paper_strategy,
            include_watchlist=paper_include_watchlist,
            paper_max_new_positions_per_day=paper_max_new_positions_per_day,
        )
        paper_signal_report = _block_new_signal_generation_on_daily_mismatch(
            paper_signal_report,
            daily_dir,
            intraday_dir,
        )
        _ensure_paper_trade_log(latest_dir / "paper_trade_log.csv")
        paper_order_review = _write_paper_order_review(
            paper_signal_report,
            latest_dir / "paper_order_review.csv",
        )
        paper_signal_report, _ = _apply_paper_order_review(
            paper_signal_report,
            paper_order_review,
            latest_dir / "paper_trade_log.csv",
            asset_data,
        )
        paper_signal_report = _apply_previous_pending_next_open_orders(
            paper_signal_report,
            previous_paper_signal_report,
            paper_order_review,
            latest_dir / "paper_trade_log.csv",
            asset_data,
        )
        archive_new_paper_decision_events(
            paper_signal_report,
            asset_data,
            Path(data_folder).resolve().parent / "state" / "paper_decision_events",
        )
        latest_daily_for_pending = _latest_csv_data_date(daily_dir)
        _write_paper_pending_orders(
            paper_signal_report,
            latest_dir / "paper_pending_orders.csv",
            latest_daily_for_pending,
            latest_dir / "paper_trade_log.csv",
        )
        write_paper_portfolio_monitor(
            latest_dir,
            latest_dir / "paper_portfolio_monitor.csv",
            latest_dir / "paper_portfolio_monitor.md",
            asset_data=asset_data,
            safety_flags={
                "broker_submit_enabled": False,
                "live_trading_enabled": False,
                "short_trading_enabled": False,
                "submit_order_called": False,
                "post_request_used": False,
            },
        )
        daily_pipeline_status = _daily_preflight_status(
            paper_signal_report,
            latest_dir / "paper_trade_log.csv",
            daily_dir,
            intraday_dir,
            data_update_ran=str(os.getenv("DAILY_DATA_UPDATE_RAN", "")).strip().lower() in {"1", "true", "yes"},
        )
        _write_daily_preflight_report(
            daily_pipeline_status,
            latest_dir / "daily_preflight_report.md",
        )
        data_precheck_result = _write_data_precheck_report(
            paper_signal_report,
            daily_dir,
            intraday_dir,
            daily_pipeline_status,
            latest_dir / "data_precheck_report.md",
        )
        for directory in [latest_dir, run_dir]:
            if directory != latest_dir:
                _ensure_paper_trade_log(directory / "paper_trade_log.csv")
                _write_paper_order_review(
                    paper_signal_report,
                    directory / "paper_order_review.csv",
                )
                _write_paper_pending_orders(
                    paper_signal_report,
                    directory / "paper_pending_orders.csv",
                    latest_daily_for_pending,
                    latest_dir / "paper_trade_log.csv",
                )
                write_paper_portfolio_monitor(
                    directory,
                    directory / "paper_portfolio_monitor.csv",
                    directory / "paper_portfolio_monitor.md",
                    asset_data=asset_data,
                    safety_flags={
                        "broker_submit_enabled": False,
                        "live_trading_enabled": False,
                        "short_trading_enabled": False,
                        "submit_order_called": False,
                        "post_request_used": False,
                    },
                )
                _write_daily_preflight_report(
                    daily_pipeline_status,
                    directory / "daily_preflight_report.md",
                )
                _write_data_precheck_report(
                    paper_signal_report,
                    daily_dir,
                    intraday_dir,
                    daily_pipeline_status,
                    directory / "data_precheck_report.md",
                )
            _write_paper_signal_report(
                paper_signal_report,
                directory / "paper_signal_report.csv",
                directory / "paper_signal_report.md",
            )
            _write_paper_trade_log_template(directory / "paper_trade_log_template.csv")
            _write_paper_trading_plan(directory / "paper_trading_plan.md")
        paper_report_sec = _stage_done("paper report", stage_started)
    if write_rs_robustness_report:
        stage_started = _stage_started("robustness report")
        grid = _rs_robustness_experiments()
        print(f"\nRS robustness grid calisiyor: {len(grid)} experiment")
        robust_tasks = _build_experiment_tasks(
            grid,
            data_folder,
            run_profile,
            quick_mode,
            intraday_dir,
            daily_dir,
            raw_dir,
            intraday_timeframe,
            daily_timeframe,
            cache_rebuilt,
            include_raw=write_yearly_performance_report,
        )
        robustness_results, robustness_parallel_runtime_sec = _run_experiment_batch(
            robust_tasks,
            asset_data,
            benchmark_data,
            cache_path,
            parallel_enabled=parallel_enabled,
            workers=worker_count,
        )
        for result in robustness_results:
            if result.get("raw"):
                raw_results[result["experiment_name"]] = result["raw"]
            experiment_runtime_rows.append(
                {
                    "stage": "robustness_strategy_runtime",
                    "experiment_name": result["experiment_name"],
                    "duration_sec": round(result["runtime_sec"], 4),
                    "strategy_runtime_sec": round(result["runtime_sec"], 4),
                    "rolling_high_low_sec": result["row"].get("rolling_high_low_sec", ""),
                    "signal_generation_sec": result["row"].get("signal_generation_sec", ""),
                    "ranking_sec": result["row"].get("ranking_sec", ""),
                    "portfolio_sim_sec": result["row"].get("portfolio_sim_sec", ""),
                    "exits_sec": result["row"].get("exits_sec", ""),
                    "diagnostics_sec": result["row"].get("diagnostics_sec", ""),
                    "symbols_loaded": result.get("symbols_loaded", 0),
                    "rows_loaded": cache_summary.get("rows_loaded", ""),
                    "cache_hit_count": cache_summary.get("prepared_disk_cache_hits", 0),
                    "cache_miss_count": cache_summary.get("prepared_disk_cache_misses", 0),
                }
            )
        robustness_rows = [result["row"] for result in robustness_results]
        robustness = _add_compact_scores(pd.DataFrame(robustness_rows))
        robustness = robustness.sort_values("Experiment Name")
        for directory in [latest_dir, run_dir]:
            _write_rs_robustness_report(
                robustness,
                directory / "rs_robustness_report.csv",
                directory / "rs_robustness_report.md",
            )
        robustness_report_sec = _stage_done("robustness report", stage_started)
    yearly_report_sec = 0.0
    if write_yearly_performance_report:
        stage_started = _stage_started("yearly report")
        try:
            yearly = _yearly_rows(
                raw_results,
                _yearly_focus_names(),
                benchmark_data,
                asset_data,
            )
            for directory in [latest_dir, run_dir]:
                _write_yearly_report(
                    yearly,
                    directory / "yearly_performance_report.csv",
                    directory / "yearly_performance_report.md",
                )
        except Exception as error:
            print(f"Warning: yearly performance report failed, continuing run: {error}")
        yearly_report_sec = _stage_done("yearly report", stage_started)
    candidate_summary_sec = 0.0
    if write_candidate_summary_report:
        stage_started = _stage_started("candidate summary")
        for directory in [latest_dir, run_dir]:
            _write_candidate_summary_report(
                report,
                robustness,
                directory / "candidate_summary_report.md",
            )
        candidate_summary_sec = _stage_done("candidate summary", stage_started)
    missing_counts = pd.to_numeric(
        report.get("EqualWeight Missing Symbols Count", pd.Series(dtype=float)),
        errors="coerce",
    ).dropna()
    if not missing_counts.empty and int(missing_counts.max()) > 0:
        print(f"EqualWeight missing symbols: {int(missing_counts.max())}")
    total_runtime_sec = _now() - total_started
    cpu_percent = ((time.process_time() - cpu_started) / total_runtime_sec * 100.0) if total_runtime_sec > 0 else 0.0
    if paper_report and daily_pipeline_status is not None:
        regression_decision = _final_candidate_decision_frame(report, oos, attribution_summary, cost_stress)
        source_decision = _read_final_candidate_decision_report(latest_dir / "final_candidate_decision_report.md")
        if source_decision is not None and not source_decision.empty:
            regression_decision = source_decision
        regression_guard_result = _write_regression_guard_report(
            report,
            regression_decision,
            latest_dir,
            latest_dir / "regression_guard_report.md",
        )
        for directory in [run_dir]:
            _write_regression_guard_report(
                report,
                regression_decision,
                latest_dir,
                directory / "regression_guard_report.md",
            )
        pipeline_health = _write_pipeline_health_log(
            daily_pipeline_status,
            data_precheck_result or {"status": "OK", "warnings": [], "critical": []},
            regression_guard_result,
            latest_dir,
            latest_dir.parent / "history",
            command or "unknown",
            total_runtime_sec,
            cache_summary,
        )
        schema_validation_result = _write_report_schema_validation_report(
            latest_dir,
            latest_dir / "report_schema_validation_report.md",
        )
        paper_state_validation_result = _write_paper_state_validation_report(
            latest_dir,
            latest_dir / "paper_state_validation_report.md",
        )
        aging_summary = _pending_aging_summary(latest_dir / "paper_pending_orders.csv")
        operational_health = {
            "pipeline_health": pipeline_health,
            "pending_aging_summary": aging_summary,
            "data_precheck": data_precheck_result or {"status": "OK", "warnings": [], "critical": []},
            "regression_guard": regression_guard_result,
            "schema_validation": schema_validation_result,
            "paper_state_validation": paper_state_validation_result,
            "next_action": daily_pipeline_status.get("next_action", "Review operational health reports."),
        }
    if write_performance_profile:
        profile_write_started = _now()
        worker_runtimes = [float(row.get("duration_sec", 0.0) or 0.0) for row in experiment_runtime_rows]
        sequential_estimate = sum(worker_runtimes)
        batch_runtime_sum = baseline_parallel_runtime_sec + robustness_parallel_runtime_sec
        batch_runtime_sum += oos_parallel_runtime_sec
        batch_runtime_sum += attribution_parallel_runtime_sec
        batch_runtime_sum += cost_stress_parallel_runtime_sec
        batch_runtime_sum += liquidity_guard_sensitivity_parallel_runtime_sec
        speedup_estimate = (
            sequential_estimate / batch_runtime_sum
            if parallel_enabled and batch_runtime_sum > 0
            else 1.0
        )
        cpu_percent_report = (
            sequential_estimate / total_runtime_sec * 100.0
            if parallel_enabled and total_runtime_sec > 0
            else cpu_percent
        )
        total_row = {
            "stage": "total",
            "experiment_name": "",
            "duration_sec": round(total_runtime_sec, 4),
            "total_runtime_sec": round(total_runtime_sec, 4),
            "data_audit_sec": round(data_audit_sec, 4),
            "csv_read_sec": round(float(cache_summary.get("csv_read_sec", 0.0) or 0.0), 4),
            "prepared_cache_build_sec": round(float(cache_summary.get("prepared_cache_build_sec", 0.0) or 0.0), 4),
            "prepared_cache_load_sec": round(float(cache_summary.get("prepared_cache_load_sec", 0.0) or 0.0), 4),
            "indicator_compute_sec": round(sum(row["duration_sec"] for row in experiment_runtime_rows), 4),
            "strategy_runtime_sec": round(sum(row["duration_sec"] for row in experiment_runtime_rows), 4),
            "report_write_sec": round(report_write_sec, 4),
            "yearly_report_sec": round(yearly_report_sec, 4),
            "robustness_report_sec": round(robustness_report_sec, 4),
            "candidate_summary_sec": round(candidate_summary_sec, 4),
            "oos_report_sec": round(oos_report_sec, 4),
            "attribution_report_sec": round(attribution_report_sec, 4),
            "cost_stress_report_sec": round(cost_stress_report_sec, 4),
            "liquidity_guard_sensitivity_report_sec": round(liquidity_guard_sensitivity_report_sec, 4),
            "paper_report_sec": round(paper_report_sec, 4),
            "cache_hit_count": int(cache_summary.get("prepared_disk_cache_hits", 0) or 0),
            "cache_miss_count": int(cache_summary.get("prepared_disk_cache_misses", 0) or 0),
            "symbols_loaded": int(cache_summary.get("symbols_loaded", len(asset_data)) or 0),
            "rows_loaded": int(cache_summary.get("rows_loaded", 0) or 0),
            "process_cpu_percent_estimate": round(cpu_percent_report, 2),
            "parallel_enabled": parallel_enabled,
            "workers": worker_count if parallel_enabled else 1,
            "cpu_count": os.cpu_count() or 1,
            "baseline_parallel_runtime_sec": round(baseline_parallel_runtime_sec, 4),
            "robustness_parallel_runtime_sec": round(robustness_parallel_runtime_sec, 4),
            "oos_parallel_runtime_sec": round(oos_parallel_runtime_sec, 4),
            "attribution_parallel_runtime_sec": round(attribution_parallel_runtime_sec, 4),
            "cost_stress_parallel_runtime_sec": round(cost_stress_parallel_runtime_sec, 4),
            "liquidity_guard_sensitivity_parallel_runtime_sec": round(
                liquidity_guard_sensitivity_parallel_runtime_sec,
                4,
            ),
            "average_worker_runtime_sec": round(
                sum(worker_runtimes) / max(len(worker_runtimes), 1),
                4,
            ),
            "max_worker_runtime_sec": round(max(worker_runtimes) if worker_runtimes else 0.0, 4),
            "speedup_estimate_vs_sequential": round(speedup_estimate, 4),
        }
        profile_rows = [
            total_row,
            {"stage": "data_loading", "duration_sec": round(data_loading_sec, 4)},
            {"stage": "data_audit", "duration_sec": round(data_audit_sec, 4)},
            {"stage": "prepared_cache", "duration_sec": round(prepared_cache_sec, 4)},
            {"stage": "report_write", "duration_sec": round(report_write_sec, 4)},
            {"stage": "yearly_report", "duration_sec": round(yearly_report_sec, 4)},
            {"stage": "robustness_report", "duration_sec": round(robustness_report_sec, 4)},
            {"stage": "oos_walkforward", "duration_sec": round(oos_report_sec, 4)},
            {"stage": "candidate_attribution", "duration_sec": round(attribution_report_sec, 4)},
            {"stage": "cost_slippage_stress", "duration_sec": round(cost_stress_report_sec, 4)},
            {
                "stage": "liquidity_guard_sensitivity",
                "duration_sec": round(liquidity_guard_sensitivity_report_sec, 4),
            },
            {"stage": "paper_report", "duration_sec": round(paper_report_sec, 4)},
            {"stage": "candidate_summary", "duration_sec": round(candidate_summary_sec, 4)},
            *experiment_runtime_rows,
        ]
        for directory in [latest_dir, run_dir]:
            slowest = _write_performance_profile(
                profile_rows,
                directory / "performance_profile_report.csv",
                directory / "performance_profile_report.md",
            )
        total_runtime_sec = _now() - total_started
        total_row["duration_sec"] = round(total_runtime_sec, 4)
        total_row["total_runtime_sec"] = round(total_runtime_sec, 4)
        total_row["report_write_sec"] = round(
            report_write_sec + (_now() - profile_write_started),
            4,
        )
        profile_rows[0] = total_row
        for directory in [latest_dir, run_dir]:
            slowest = _write_performance_profile(
                profile_rows,
                directory / "performance_profile_report.csv",
                directory / "performance_profile_report.md",
            )
        print("Slowest stages:")
        for _, row in slowest.head(5).iterrows():
            print(f"- {row['stage']}: {float(row['duration_sec']):.2f} sec")
    manifest_flags = {
        "run_oos": run_oos,
        "run_attribution": run_attribution,
        "run_cost_stress": run_cost_stress,
        "run_liquidity_guard_sensitivity": run_liquidity_guard_sensitivity,
        "run_robustness": run_robustness,
        "paper_report": paper_report,
    }
    # SQLite mirror block
    db_path = resolve_project_path("state/trading_state.sqlite3", data_folder)
    run_id = f"run_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"

    try:
        init_state_db(str(db_path))

        # 1. Pipeline run
        append_pipeline_run(
            str(db_path),
            run_id=run_id,
            run_datetime=pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
            command=command or "python bot.py --mode local-paper",
            mode=report_mode or "local-paper",
            status="SUCCESS",
            strategy_metrics_changed=False,
            benchmark_changed=False,
            regression_guard_status=operational_health.get("regression_guard", {}).get("status", "OK") if operational_health else "OK",
            total_runtime_sec=float(total_runtime_sec or 0)
        )

        # 2. Paper orders
        orders_path = latest_dir / "paper_pending_orders.csv"
        if orders_path.exists():
            try:
                orders_df = pd.read_csv(orders_path)
                append_paper_orders(str(db_path), run_id, orders_df)
            except Exception as e_ord:
                print(f"WARNING: SQLite append_paper_orders failed to read CSV: {e_ord}")

        # 3. Paper trades
        trades_path = latest_dir / "paper_trade_log.csv"
        if trades_path.exists():
            try:
                trades_df = pd.read_csv(trades_path)
                append_paper_trades(str(db_path), run_id, trades_df)
            except Exception as e_trd:
                print(f"WARNING: SQLite append_paper_trades failed to read CSV: {e_trd}")

        # 4. Pipeline health/readiness checks
        if operational_health:
            checks = []
            if "data_precheck" in operational_health:
                pre = operational_health["data_precheck"]
                checks.append({
                    "check_name": "data_precheck",
                    "status": pre.get("status", "WARNING"),
                    "value": ", ".join(pre.get("critical", []) + pre.get("warnings", [])),
                    "required_value": "OK",
                    "message": "Data precheck diagnostics"
                })
            if "regression_guard" in operational_health:
                reg = operational_health["regression_guard"]
                checks.append({
                    "check_name": "regression_guard",
                    "status": reg.get("status", "OK"),
                    "value": reg.get("status", "OK"),
                    "required_value": "OK",
                    "message": "Regression guard test status"
                })
            if "paper_state_validation" in operational_health:
                psv = operational_health["paper_state_validation"]
                checks.append({
                    "check_name": "paper_state_validation",
                    "status": psv.get("status", "OK"),
                    "value": psv.get("status", "OK"),
                    "required_value": "OK",
                    "message": "Paper state consistency"
                })
            if "schema_validation" in operational_health:
                sv = operational_health["schema_validation"]
                checks.append({
                    "check_name": "schema_validation",
                    "status": sv.get("status", "OK"),
                    "value": sv.get("status", "OK"),
                    "required_value": "OK",
                    "message": "Report schema validation"
                })
            append_pre_submit_checks(str(db_path), run_id, checks)

        # Write state report files
        _write_sqlite_state_report(latest_dir, db_path)
        if daily_pipeline_status is not None:
            _write_paper_ledger_audit_report(latest_dir, db_path, daily_pipeline_status)
        if run_dir.exists():
            _write_sqlite_state_report(run_dir, db_path)
            if daily_pipeline_status is not None:
                _write_paper_ledger_audit_report(run_dir, db_path, daily_pipeline_status)

    except Exception as e_sql:
        print(f"WARNING: SQLite mirror execution failed: {e_sql}")

    if make_chatgpt_report:
        try:
            print("Refreshing test run report before writing ChatGPT master report...")
            from testdata.run_test_suite import main as run_tests
            run_tests()
        except ImportError:
            try:
                from run_test_suite import main as run_tests
                run_tests()
            except Exception as e_tests:
                print(f"WARNING: Could not refresh test run report: {e_tests}")
        except Exception as e_tests:
            print(f"WARNING: Could not refresh test run report: {e_tests}")
        for directory in [latest_dir, run_dir]:
            _write_chatgpt_master_report(
                report,
                audit,
                oos,
                attribution,
                attribution_summary,
                cost_stress,
                liquidity_guard_sensitivity,
                paper_signal_report,
                profile_rows,
                directory / "chatgpt_master_report.md",
                final_decision_path=latest_dir / "final_candidate_decision_report.md",
                command=command or "unknown",
                report_mode=report_mode,
                flags=manifest_flags,
                total_runtime_sec=total_runtime_sec,
                cache_summary=cache_summary,
                daily_pipeline_status=daily_pipeline_status,
                operational_health=operational_health,
            )

    _write_run_manifest(
        latest_dir,
        run_dir,
        command or "unknown",
        report_mode,
        manifest_flags,
        daily_dir,
        intraday_dir,
        cache_path,
        cache_summary,
        total_runtime_sec,
        operational_health=operational_health,
    )
    print(f"Total runtime: {total_runtime_sec:.2f} sec")
    print(
        "Cache hit/miss: "
        f"{int(cache_summary.get('prepared_disk_cache_hits', 0) or 0)}/"
        f"{int(cache_summary.get('prepared_disk_cache_misses', 0) or 0)}"
    )
    print(
        f"Report mode: {report_mode} "
        "(basic=compact/audit/performance, full=yearly/candidate; robustness=--run-robustness)"
    )
    print(f"Report latest dir: {latest_dir}")
    print(f"Report run dir: {run_dir}")
    return report


__all__ = ["run_alpaca_paper_check", "run_strategy_experiments", "run_universe_scanner"]
