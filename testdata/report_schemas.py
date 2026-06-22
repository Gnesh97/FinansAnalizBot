import pandas as pd


PAPER_SIGNAL_COLUMNS = [
    "signal_scope",
    "run_datetime",
    "paper_strategy_mode",
    "paper_portfolio_state",
    "strategy_name",
    "signal_date",
    "symbol",
    "action",
    "fill_status",
    "user_decision",
    "reason",
    "is_new_signal",
    "already_in_position",
    "paper_actionable",
    "deferred_due_to_ramp_in",
    "paper_rank",
    "paper_max_new_positions_per_day",
    "ranking_score",
    "current_position_status",
    "liquidity_guard_pass",
    "liquidity_warning_reason",
    "warning",
    "estimated_dollar_volume",
    "order_size_pct_of_dollar_volume",
    "paper_capital_assumption",
    "paper_position_notional",
    "entry_price_reference",
    "assumed_fill_rule",
    "next_open_available",
    "fill_bar_timestamp",
    "fill_price_source",
    "max_positions",
]

PAPER_PENDING_ORDERS_COLUMNS = [
    "created_at",
    "signal_date",
    "strategy_name",
    "symbol",
    "action",
    "paper_rank",
    "ranking_score",
    "paper_capital",
    "paper_position_notional",
    "liquidity_guard_pass",
    "estimated_dollar_volume",
    "order_size_pct_of_dollar_volume",
    "status",
    "user_decision",
    "reason",
    "pending_age_calendar_days",
    "pending_age_trading_days",
    "expected_next_open_date",
    "pending_stale_warning",
    "pending_stale_reason",
    "notes",
]

PAPER_ORDER_REVIEW_COLUMNS = ["symbol", "strategy_name", "action", "user_decision", "notes"]

PAPER_TRADE_LOG_COLUMNS = [
    "date",
    "strategy_name",
    "symbol",
    "action",
    "signal_date",
    "signal_price",
    "assumed_fill_price",
    "fill_status",
    "shares",
    "notional",
    "paper_capital",
    "reason",
    "liquidity_guard_pass",
    "notes",
]

PIPELINE_HEALTH_COLUMNS = [
    "run_datetime",
    "command",
    "data_update_ran",
    "latest_daily_date",
    "latest_130min_date",
    "latest_expected_market_session",
    "stale_data_warning",
    "pending_order_count",
    "accepted_order_count",
    "pending_next_open_count",
    "filled_count_this_run",
    "rejected_count_this_run",
    "paper_trade_log_rows",
    "strategy_metrics_changed",
    "benchmark_changed",
    "total_runtime_sec",
    "cache_hit_count",
    "cache_miss_count",
    "status",
    "warning_summary",
]

DATA_PRECHECK_COLUMNS = [
    "symbol",
    "daily_exists",
    "daily_empty",
    "daily_latest_date",
    "intraday_exists",
    "intraday_empty",
    "intraday_latest_date",
    "zero_volume_rows",
    "warnings",
]

DATA_POLICY_DECISION_COLUMNS = [
    "symbol",
    "warning",
    "policy_status",
    "recommended_policy_action",
    "operator_decision",
    "operator_note",
]

DATA_POLICY_REVIEW_EVIDENCE_COLUMNS = [
    "symbol",
    "daily_exists",
    "daily_rows",
    "daily_first_date",
    "daily_last_date",
    "daily_zero_volume_rows",
    "intraday_exists",
    "intraday_rows",
    "intraday_first_datetime",
    "intraday_last_datetime",
    "source_note",
    "recommended_operator_next_action",
]

REGRESSION_GUARD_COLUMNS = [
    "check_name",
    "expected",
    "actual",
    "status",
    "warning",
]

ALPACA_ORDER_PREVIEW_COLUMNS = [
    "symbol",
    "action",
    "status",
    "paper_position_notional",
    "estimated_qty",
    "order_type",
    "time_in_force",
    "would_submit",
    "reason",
]


def ensure_columns(df, columns, fill_value=""):
    output = df.copy() if df is not None else pd.DataFrame()
    for column in columns:
        if column not in output:
            output[column] = fill_value
    extra_columns = [column for column in output.columns if column not in columns]
    return output[list(columns) + extra_columns]


def validate_required_columns(df, columns):
    actual = list(df.columns) if df is not None else []
    missing = [column for column in columns if column not in actual]
    extra = [column for column in actual if column not in columns]
    return {
        "ok": not missing,
        "missing_columns": missing,
        "extra_columns": extra,
        "warning": "" if not missing else "missing_required_columns",
    }
