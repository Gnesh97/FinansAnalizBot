"""ChatGPT master report: the consolidated research/validation summary.

Extracted from testdata/runner.py (Phase 12B) to shrink that 8,687-line
file. Behavior-preserving move -- function bodies are unchanged, only
their file and a handful of now-necessary imports moved. Called from
exactly one site, inside run_strategy_experiments's call to
_write_chatgpt_master_report(...): this is research/validation tooling,
not part of the live nightly DSS chain (which only invokes --paper-report
and --dss-report).

A few helpers here (_readable_table, _write_sqlite_state_report) are also
called from report-writing functions that stayed in runner.py, so those
are imported back there. Conversely, this module calls a handful of
runner.py functions that were not part of this extraction
(_evaluate_data_policy_status, _paper_stale_status,
_paper_fill_gate_summary, _final_candidate_decision_frame,
_read_final_candidate_decision_report, _table_for_markdown) -- those are
imported locally, inside the functions that need them, specifically to
avoid a circular top-level import (runner.py imports this module at load
time; this module must not import runner.py at load time in return).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

try:
    from .paper_state_manager import net_open_positions_from_trade_log
    from .state_store import get_state_summary
except ImportError:
    from paper_state_manager import net_open_positions_from_trade_log
    from state_store import get_state_summary


def _safe_columns(frame, columns):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    output = frame.copy()
    for column in columns:
        if column not in output:
            output[column] = pd.NA
    return output[columns]


def _readable_table(frame):
    try:
        from .runner import _table_for_markdown
    except ImportError:
        from runner import _table_for_markdown
    if frame is None or frame.empty:
        return "No rows."
    return _table_for_markdown(frame)


def _chatgpt_operational_health_section(operational_health):
    try:
        from .runner import _evaluate_data_policy_status
    except ImportError:
        from runner import _evaluate_data_policy_status
    if not operational_health:
        return []
    aging = operational_health.get("pending_aging_summary", {})
    data_precheck = operational_health.get("data_precheck", {})
    regression = operational_health.get("regression_guard", {})
    schema_validation = operational_health.get("schema_validation", {})
    paper_state = operational_health.get("paper_state_validation", {})
    health = operational_health.get("pipeline_health", {})
    policy_decisions = data_precheck.get("policy_decisions", []) or []

    daily_dir = health.get("daily_data_dir", "hisseler/daily")
    latest_dir = health.get("latest_dir", "reports/latest")
    if not data_precheck:
        data_policy_status = "not_generated"
        safe_known_exception_count = 0
        data_redownload_required_count = 0
        manual_review_required_count = 0
        active_blocking_symbols = []
        inactive_exception_symbols = []
        blocking_policy_decisions = []
    else:
        eval_res = _evaluate_data_policy_status(policy_decisions, latest_dir, daily_dir)

        data_policy_status = eval_res["status"]
        safe_known_exception_count = eval_res["safe_known_exception_count"]
        data_redownload_required_count = eval_res["data_redownload_required_count"]
        manual_review_required_count = eval_res["manual_review_required_count"]
        active_blocking_symbols = eval_res["active_blocking_symbols"]
        inactive_exception_symbols = eval_res["inactive_exception_symbols"]
        blocking_policy_decisions = eval_res["blocking_decisions"]

    ret = [
        "## Operational Health",
        "",
        f"- pipeline status: {health.get('status', 'not_generated')}",
        f"- latest data dates: daily={health.get('latest_daily_date', 'none')}, 130min={health.get('latest_130min_date', 'none')}",
        f"- pending order aging: max_calendar_days={aging.get('max_calendar_days', 0)}, max_trading_days={aging.get('max_trading_days', 0)}, stale_count={aging.get('stale_count', 0)}",
        f"- data precheck warnings: {', '.join(data_precheck.get('critical', []) + data_precheck.get('warnings', [])) or 'none'}",
        f"- data_policy_status: {data_policy_status}",
        f"- data_policy_decision_count: {len(policy_decisions)}",
        f"- data_policy_blocking_count: {len(blocking_policy_decisions)}",
        f"- data_policy_blocking_symbols: {', '.join(active_blocking_symbols) or 'none'}",
        f"- safe_known_exception_count: {safe_known_exception_count}",
        f"- data_redownload_required_count: {data_redownload_required_count}",
        f"- manual_review_required_count: {manual_review_required_count}",
        f"- active_blocking_symbols: {', '.join(active_blocking_symbols) or 'none'}",
        f"- inactive_exception_symbols: {', '.join(inactive_exception_symbols) or 'none'}",
        "- data_policy_decisions_path: reports/latest/data_policy_decisions.csv",
        "- data_policy_review_report_path: reports/latest/data_policy_review_report.md",
        "- data_policy_review_evidence_path: reports/latest/data_policy_review_evidence.csv",
        f"- regression guard status: {regression.get('status', 'not_generated')}",
        f"- report_schema_validation_status: {schema_validation.get('status', 'not_generated')}",
        f"- report_schema_validation_report_path: {schema_validation.get('path', 'reports/latest/report_schema_validation_report.md')}",
        f"- paper_state_validation_status: {paper_state.get('status', 'not_generated')}",
        f"- paper_state_validation_report_path: {paper_state.get('path', 'reports/latest/paper_state_validation_report.md')}",
    ]

    sqlite = operational_health.get("sqlite", {})
    if sqlite:
        db_rel_path = "testdata/state/trading_state.sqlite3"
        report_rel_path = "reports/latest/sqlite_state_report.md"
        ret.extend([
            f"- sqlite_state_status: {sqlite.get('status', 'WARNING')}",
            f"- sqlite_db_path: {db_rel_path}",
            f"- sqlite_state_report_path: {report_rel_path}",
            f"- latest_run_id: {sqlite.get('latest_run_id') or 'none'}",
            f"- paper_orders_count: {sqlite.get('paper_orders_count', 0)}",
            f"- paper_trades_count: {sqlite.get('paper_trades_count', 0)}",
            f"- execution_audit_count: {sqlite.get('execution_audit_count', 0)}"
        ])

    ret.extend([
        f"- next action: {operational_health.get('next_action', 'Review reports/latest/pipeline_health_log.csv.')}",
        "",
    ])
    return ret


def _chatgpt_daily_pipeline_status_section(daily_pipeline_status):
    if not daily_pipeline_status:
        return []
    return [
        "## Daily Pipeline Status",
        "",
        "- daily_pipeline_script: scripts/run_daily_paper_pipeline.bat",
        f"- data update ran: {daily_pipeline_status['data_update_ran']}",
        "- preflight report path: reports/latest/daily_preflight_report.md",
        "- chatgpt_upload_file: testdata/reports/latest/chatgpt_master_report.md",
        f"- latest data date: daily={daily_pipeline_status['latest_daily_data_date']}, 130min={daily_pipeline_status['latest_130min_data_date']}",
        f"- stale warning: {daily_pipeline_status['stale_data_warning']}",
        f"- pending next open count: {daily_pipeline_status['pending_next_open_count']}",
        f"- filled_count_this_run: {daily_pipeline_status['filled_count_this_run']}",
        f"- total_filled_trades: {daily_pipeline_status.get('total_filled_trades', 0)}",
        f"- open_paper_positions: {daily_pipeline_status.get('open_paper_positions', 0)}",
        f"- paper_ledger_status: {daily_pipeline_status.get('paper_ledger_status', 'OK')}",
        f"- skipped_duplicate_fill_count: {daily_pipeline_status.get('skipped_duplicate_fill_count', 0)}",
        f"- paper_fill_gate_status: {daily_pipeline_status['paper_fill_gate_status']}",
        f"- paper_fill_gate_daily_required: {str(daily_pipeline_status['paper_fill_gate_daily_required']).lower()}",
        f"- paper_fill_gate_130min_required: {str(daily_pipeline_status['paper_fill_gate_130min_required']).lower()}",
        f"- daily_mismatch_blocks_paper_fill: {str(daily_pipeline_status['daily_mismatch_blocks_paper_fill']).lower()}",
        f"- daily_mismatch_blocks_new_signals: {str(daily_pipeline_status['daily_mismatch_blocks_new_signals']).lower()}",
        f"- filled_from_130min_next_open_count: {daily_pipeline_status['filled_from_130min_next_open_count']}",
        f"- rejected count this run: {daily_pipeline_status['rejected_count_this_run']}",
        f"- next action: {daily_pipeline_status['next_action']}",
        "",
    ]


def _chatgpt_paper_readiness_section(paper_report):
    try:
        from .runner import _paper_stale_status, _paper_fill_gate_summary
    except ImportError:
        from runner import _paper_stale_status, _paper_fill_gate_summary
    if paper_report is None:
        return [
            "## Paper Trading Readiness",
            "",
            "- selected main candidate: rs_m126_top30_sma100_no_regime",
            "- paper signal report path: reports/latest/paper_signal_report.md",
            "- latest_signal_date: not generated",
            "- actionable_buy_count: not generated",
            "- actionable_sell_count: not generated",
            "- hold_count: not generated",
            "- skip_count: not generated",
            "- liquidity_warning_count: not generated",
            "- stale_data_warning: not generated",
            "- next action: run --paper-report.",
            "",
        ]
    liquidity_warnings_count = (
        int(paper_report["liquidity_warning_reason"].fillna("").astype(str).ne("").sum())
        if not paper_report.empty and "liquidity_warning_reason" in paper_report
        else 0
    )
    action_counts = paper_report["action"].value_counts().to_dict() if not paper_report.empty else {}
    fill_counts = paper_report["fill_status"].value_counts().to_dict() if not paper_report.empty else {}
    decision_counts = paper_report["user_decision"].value_counts().to_dict() if not paper_report.empty else {}
    stale_status = _paper_stale_status(paper_report)
    actionable = paper_report[paper_report["paper_actionable"].astype(bool)].copy() if not paper_report.empty else pd.DataFrame()
    pending_next_open_count = int(fill_counts.get("PENDING_NEXT_OPEN", 0))
    accepted_order_count = int(decision_counts.get("ACCEPT", 0))
    if pending_next_open_count > 0:
        next_action = "Wait for next valid market open data, then run scripts/run_daily_paper_pipeline.bat again."
    elif accepted_order_count > 0:
        next_action = "Run scripts/run_daily_paper_pipeline.bat after the next data update to refresh paper state."
    else:
        next_action = "Review paper signals and update paper_order_review.csv decisions if needed."
    table_columns = [
        "strategy_name",
        "signal_date",
        "symbol",
        "action",
        "fill_status",
        "user_decision",
        "fill_bar_timestamp",
        "fill_price_source",
        "paper_actionable",
        "paper_rank",
        "ranking_score",
        "liquidity_guard_pass",
        "warning",
        "reason",
    ]
    return [
        "## Paper Trading Readiness",
        "",
        "- selected main candidate: rs_m126_top30_sma100_no_regime",
        "- paper_signal_report_path: reports/latest/paper_signal_report.md",
        f"- latest_signal_date: {stale_status['latest_signal_date']}",
        f"- current_date: {stale_status['current_date']}",
        f"- calendar_check_mode: {stale_status['calendar_check_mode']}",
        f"- stale_reason: {stale_status['stale_reason']}",
        f"- actionable_buy_count: {int(action_counts.get('BUY', 0))}",
        f"- actionable_sell_count: {int(action_counts.get('SELL', 0))}",
        f"- hold_count: {int(action_counts.get('HOLD', 0))}",
        f"- skip_count: {int(action_counts.get('SKIP', 0))}",
        f"- liquidity_warning_count: {liquidity_warnings_count}",
        f"- stale_data_warning: {stale_status['stale_data_warning']}",
        f"- pending_order_count: {int(fill_counts.get('PENDING_REVIEW', 0) + fill_counts.get('PENDING_NEXT_OPEN', 0))}",
        f"- accepted_order_count: {int(decision_counts.get('ACCEPT', 0))}",
        f"- rejected_order_count: {int(decision_counts.get('REJECT', 0))}",
        f"- hold_review_count: {int(decision_counts.get('HOLD_REVIEW', 0))}",
        f"- pending_next_open_count: {pending_next_open_count}",
        f"- filled_from_130min_next_open_count: {_paper_fill_gate_summary(paper_report)['filled_from_130min_next_open_count']}",
        f"- paper_fill_gate_status: {_paper_fill_gate_summary(paper_report)['paper_fill_gate_status']}",
        "- paper_fill_gate_daily_required: false",
        "- paper_fill_gate_130min_required: true",
        "- daily_mismatch_blocks_paper_fill: false",
        "- daily_mismatch_blocks_new_signals: true",
        "- paper_pending_orders_path: reports/latest/paper_pending_orders.csv",
        "- paper_order_review_path: reports/latest/paper_order_review.csv",
        "- paper_trade_log_path: reports/latest/paper_trade_log.csv",
        f"- next action: {next_action}",
        "",
        "### Actionable Signals",
        "",
        _readable_table(_safe_columns(actionable.head(20), table_columns)),
        "",
    ]


def _chatgpt_data_audit_section(audit):
    if audit is None or audit.empty:
        return ["## Data Audit Summary", "", "Data audit is unavailable.", ""]
    daily_rows = pd.to_numeric(audit["daily_rows"], errors="coerce").fillna(0)
    daily_ends = pd.to_datetime(audit["daily_end"], errors="coerce")
    global_daily_end = daily_ends.max()
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
    watched = {"DOW", "BK", "PARA", "PSA"}
    problem_mask = (
        audit["symbol"].isin(watched)
        | audit["data_warnings"].fillna("").astype(str).ne("")
        | audit["errors"].fillna("").astype(str).ne("")
    )
    problem_columns = [
        "symbol",
        "has_daily_file",
        "has_130min_file",
        "daily_rows",
        "bar_130min_rows",
        "daily_end",
        "zero_volume_count",
        "data_warnings",
        "errors",
    ]
    problem_rows = _safe_columns(audit.loc[problem_mask], problem_columns)
    return [
        "## Data Audit Summary",
        "",
        f"- Symbols: {len(audit)}",
        f"- Daily files: {int(audit['has_daily_file'].sum())}",
        f"- Valid daily files: {int(audit['daily_data_valid'].sum())}",
        f"- 130min files: {int(audit['has_130min_file'].sum())}",
        f"- Empty daily files: {len(empty_daily)} ({','.join(empty_daily) if empty_daily else 'none'})",
        f"- Short-history symbols: {len(short_history)} ({','.join(short_history) if short_history else 'none'})",
        f"- Symbols ending before global end date: {len(ending_before_global)} ({','.join(ending_before_global) if ending_before_global else 'none'})",
        f"- Zero volume warnings: {len(zero_volume)} ({','.join(zero_volume) if zero_volume else 'none'})",
        f"- QQQ daily file: {bool(audit.loc[audit['symbol'] == 'QQQ', 'has_daily_file'].any())}",
        f"- QQQ 130min file: {bool(audit.loc[audit['symbol'] == 'QQQ', 'has_130min_file'].any())}",
        f"- SPY daily file: {bool(audit.loc[audit['symbol'] == 'SPY', 'has_daily_file'].any())}",
        f"- SPY 130min file: {bool(audit.loc[audit['symbol'] == 'SPY', 'has_130min_file'].any())}",
        "",
        "### Problem Symbols",
        "",
        _readable_table(problem_rows),
        "",
    ]


def _chatgpt_benchmark_section(report):
    if report is None or report.empty:
        return ["## Benchmark Audit Summary", "", "Benchmark audit is unavailable.", ""]
    first = report.iloc[0]
    qqq_unique = sorted(report["QQQ BuyHold Return (%)"].dropna().unique().tolist())
    ew_unique = sorted(report["EqualWeight Universe Return (%)"].dropna().unique().tolist())
    same_qqq = report[["QQQ Benchmark Start", "QQQ Benchmark End"]].drop_duplicates().shape[0] == 1
    same_ew = report[["EqualWeight Benchmark Start", "EqualWeight Benchmark End"]].drop_duplicates().shape[0] == 1
    missing = sorted(
        str(value)
        for value in report["EqualWeight Missing Symbols"].fillna("").unique().tolist()
        if str(value)
    )
    compact = _safe_columns(
        report.sort_values("Experiment Name").head(5),
        [
            "Experiment Name",
            "QQQ Benchmark Start",
            "QQQ Benchmark End",
            "EqualWeight Benchmark Start",
            "EqualWeight Benchmark End",
            "QQQ BuyHold Return (%)",
            "EqualWeight Universe Return (%)",
            "EqualWeight Missing Symbols",
        ],
    )
    return [
        "## Benchmark Audit Summary",
        "",
        f"- QQQ return unique values: {qqq_unique}",
        f"- EqualWeight return unique values: {ew_unique}",
        f"- QQQ date range: {first.get('QQQ Benchmark Start', '')} -> {first.get('QQQ Benchmark End', '')}",
        f"- EqualWeight date range: {first.get('EqualWeight Benchmark Start', '')} -> {first.get('EqualWeight Benchmark End', '')}",
        f"- Same QQQ date range: {same_qqq}",
        f"- Same EqualWeight date range: {same_ew}",
        f"- Missing symbols: {missing if missing else 'none'}",
        "- Excluded symbols: QQQ,SPY",
        "- Benchmark consistency note: EqualWeight benchmark uses immutable base/prepared OHLCV close data.",
        "",
        "### Per Experiment Sample",
        "",
        _readable_table(compact),
        "",
    ]


def _chatgpt_strategy_section(report):
    columns = [
        "Experiment Name",
        "Strategy Name",
        "Strategy Family",
        "Compact Score",
        "Expectancy R",
        "Net Getiri (%)",
        "Max Drawdown (%)",
        "Trade Count",
        "Profit Factor",
        "Worst R",
        "Avg Holding Bars",
        "QQQ BuyHold Return (%)",
        "EqualWeight Universe Return (%)",
        "Strategy Excess Return vs QQQ (%)",
    ]
    signal_columns = [
        "Experiment Name",
        "Strategy Name",
        "Raw Trigger Count",
        "Raw Entry Signal Count",
        "Confirmed Entry Signal Count",
        "Final Entry Signal Count",
        "Trade Count",
        "Risk Off Rejected Signal Count",
    ]
    ranked = report.sort_values("Compact Score", ascending=False, na_position="last")
    warnings = ["- Otomatik uyari kriterlerine takilan experiment yok."]
    if pd.to_numeric(report["Trade Count"], errors="coerce").lt(30).any():
        warnings.append("- Trade count < 30 olan experimentler guvenilir degil.")
    return [
        "## Strategy Compact Summary",
        "",
        "### Best 5 Experiments",
        "",
        _readable_table(_safe_columns(ranked.head(5), columns)),
        "",
        "### Worst 5 Experiments",
        "",
        _readable_table(_safe_columns(ranked.tail(5), columns)),
        "",
        "### Signal Diagnostics",
        "",
        _readable_table(_safe_columns(ranked, signal_columns)),
        "",
        "### Warnings",
        "",
        *warnings,
        "",
    ]


def _chatgpt_oos_section(oos):
    if oos is None or oos.empty:
        return ["## OOS / Walkforward Summary", "", "OOS report was not generated in this run.", ""]
    columns = [
        "experiment_name",
        "period_name",
        "start_date",
        "end_date",
        "net_return_pct",
        "max_drawdown_pct",
        "trade_count",
        "profit_factor",
        "expectancy_r",
        "qqq_return_pct",
        "equal_weight_return_pct",
        "excess_vs_qqq_pct",
        "return_over_maxdd",
    ]
    lines = ["## OOS / Walkforward Summary", "", "### Best 5 By Period", ""]
    for period_name, group in oos.groupby("period_name", sort=False):
        best = group.sort_values(
            ["return_over_maxdd", "net_return_pct"],
            ascending=[False, False],
            na_position="last",
        ).head(5)
        lines.extend([f"#### {period_name}", "", _readable_table(_safe_columns(best, columns)), ""])
    numeric = oos.copy()
    for column in ["net_return_pct", "max_drawdown_pct", "return_over_maxdd"]:
        numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
    stability = numeric.groupby("experiment_name").agg(
        periods=("period_name", "count"),
        avg_return_pct=("net_return_pct", "mean"),
        min_return_pct=("net_return_pct", "min"),
        return_std_pct=("net_return_pct", "std"),
        worst_drawdown_pct=("max_drawdown_pct", "min"),
    ).round(4).reset_index()
    topn = numeric[numeric["experiment_name"].str.contains("top", na=False)].copy()
    topn["top_n"] = topn["experiment_name"].str.extract(r"top(\d+)").astype(float)
    topn_summary = topn.groupby("top_n").agg(
        avg_return_pct=("net_return_pct", "mean"),
        min_return_pct=("net_return_pct", "min"),
        avg_return_over_maxdd=("return_over_maxdd", "mean"),
    ).round(4).reset_index()
    momentum = numeric[numeric["period_name"].eq("split_2023_2024")][
        ["experiment_name", "net_return_pct", "qqq_return_pct", "excess_vs_qqq_pct"]
    ].copy()
    momentum["beats_qqq"] = pd.to_numeric(
        momentum["excess_vs_qqq_pct"],
        errors="coerce",
    ).gt(0)
    lines.extend(
        [
            "### Stability",
            "",
            _readable_table(stability),
            "",
            "### 2023-2024 Momentum Period vs QQQ",
            "",
            _readable_table(momentum.sort_values("net_return_pct", ascending=False)),
            "",
            "### Top10 / Top20 / Top30 Summary",
            "",
            _readable_table(topn_summary),
            "",
        ]
    )
    return lines


def _chatgpt_attribution_section(attribution, attribution_summary):
    if attribution is None or attribution.empty:
        return ["## Candidate Attribution", "", "Candidate attribution was not generated in this run.", ""]
    lines = [
        "## Candidate Attribution",
        "",
        "### Concentration Summary",
        "",
        _readable_table(attribution_summary),
        "",
        "### Risk Warnings",
        "",
    ]
    if attribution_summary is not None and not attribution_summary.empty:
        top10 = attribution_summary[
            pd.to_numeric(
                attribution_summary.get("top_5_symbols_contribution_pct", pd.Series(dtype=float)),
                errors="coerce",
            ).gt(50)
        ]
        if top10.empty:
            lines.append("- No candidate has top 5 contribution above 50%.")
        else:
            for _, row in top10.iterrows():
                lines.append(f"- Warning: {row['experiment_name']} top 5 symbols contribute >50% of total PnL.")
    lines.extend(["", "### Top 10 Symbols By Candidate", ""])
    columns = [
        "experiment_name",
        "symbol",
        "trade_count",
        "win_rate",
        "total_pnl",
        "total_r",
        "avg_r",
        "contribution_pct_of_total_pnl",
        "cumulative_contribution_rank",
        "avg_holding_bars",
    ]
    top_symbols = attribution.sort_values(
        ["experiment_name", "cumulative_contribution_rank"],
        ascending=[True, True],
    ).groupby("experiment_name", group_keys=False).head(10)
    lines.extend([_readable_table(_safe_columns(top_symbols, columns)), ""])
    return lines


def _chatgpt_cost_section(cost_stress):
    if cost_stress is None or cost_stress.empty:
        return ["## Cost / Slippage / Liquidity Stress", "", "Cost stress report was not generated in this run.", ""]
    base = cost_stress[cost_stress["capital_assumption_usd"].eq(10000)].copy()
    scenario_summary = base.pivot_table(
        index="experiment_name",
        columns="scenario",
        values="net_return_pct",
        aggfunc="first",
    ).reset_index()
    survivors = base[
        base["scenario"].isin([
            "commission_0_slippage_10bps_per_side",
            "commission_0_slippage_25bps_per_side",
        ])
        & base["survives_cost_test"].astype(bool)
    ].groupby("scenario")["experiment_name"].apply(lambda values: ", ".join(sorted(values))).reset_index()
    liquidity = cost_stress[cost_stress["scenario"].eq("zero_cost_baseline")]
    liquidity_columns = [
        "experiment_name",
        "capital_assumption_usd",
        "max_positions",
        "per_position_notional",
        "median_order_pct_of_entry_bar_dollar_volume",
        "p10_order_pct_of_entry_bar_dollar_volume",
        "min_order_pct_of_entry_bar_dollar_volume",
        "liquidity_warning_count",
        "entries_below_1m_entry_bar_dollar_volume",
        "entries_below_500k_entry_bar_dollar_volume",
        "entries_below_100k_entry_bar_dollar_volume",
    ]
    return [
        "## Cost / Slippage / Liquidity Stress",
        "",
        "### Net Return By Scenario (capital 10000)",
        "",
        _readable_table(scenario_summary),
        "",
        "### Summary",
        "",
        _readable_table(survivors),
        "",
        "### Liquidity Stress",
        "",
        _readable_table(_safe_columns(liquidity, liquidity_columns)),
        "",
    ]


def _chatgpt_liquidity_guard_section(liquidity_guard_sensitivity):
    lines = ["## Liquidity Guard Sensitivity", ""]
    if liquidity_guard_sensitivity is None or liquidity_guard_sensitivity.empty:
        return lines + ["liquidity_guard_sensitivity_report.md not generated in this run.", ""]
    focus = liquidity_guard_sensitivity[
        liquidity_guard_sensitivity["capital_assumption_usd"].eq(10000)
    ].copy()
    columns = [
        "experiment_name",
        "guard_scenario",
        "capital_assumption_usd",
        "net_return_pct",
        "trade_count",
        "skipped_trade_count",
        "skipped_trade_pct",
        "degradation_vs_no_guard_pct",
        "liquidity_guard_pass",
    ]
    top30 = liquidity_guard_sensitivity[
        liquidity_guard_sensitivity["experiment_name"].eq("rs_m126_top30_sma100_no_regime")
        & liquidity_guard_sensitivity["guard_scenario"].eq("skip_entry_bar_dollar_volume_below_500k")
    ]
    top30_main = bool(
        not top30.empty
        and top30["liquidity_guard_pass"].astype(bool).all()
        and pd.to_numeric(top30["excess_vs_qqq_pct"], errors="coerce").gt(0).all()
    )
    return lines + [
        f"- Top30 guard sonrasi main kaliyor mu: {top30_main}",
        f"- Top30 500k skipped trade count: {int(top30['skipped_trade_count'].iloc[0]) if not top30.empty else 'n/a'}",
        f"- Top30 500k degradation_vs_no_guard_pct: {top30['degradation_vs_no_guard_pct'].iloc[0] if not top30.empty else 'n/a'}",
        f"- Top30 500k liquidity_guard_pass: {bool(top30['liquidity_guard_pass'].iloc[0]) if not top30.empty else 'n/a'}",
        "",
        "### Guard Scenario Summary (capital 10000)",
        "",
        _readable_table(_safe_columns(focus, columns)),
        "",
    ]


def _chatgpt_performance_section(profile_rows):
    profile = pd.DataFrame(profile_rows)
    if profile.empty:
        return ["## Performance Profile", "", "Performance profile is unavailable.", ""]
    stage_times = _safe_columns(profile, ["stage", "experiment_name", "duration_sec"])
    stage_times["duration_sec"] = pd.to_numeric(stage_times["duration_sec"], errors="coerce")
    slowest = stage_times.dropna(subset=["duration_sec"]).sort_values("duration_sec", ascending=False).head(5)
    total = profile[profile["stage"].eq("total")].head(1)
    strategy = profile[profile["stage"].eq("strategy_runtime")].copy()
    strategy["duration_sec"] = pd.to_numeric(strategy["duration_sec"], errors="coerce")
    stage_summary = profile[
        profile["stage"].isin([
            "oos_walkforward",
            "candidate_attribution",
            "cost_slippage_stress",
            "liquidity_guard_sensitivity",
        ])
    ][["stage", "duration_sec"]]
    return [
        "## Performance Profile",
        "",
        "### Slowest 5 Stages",
        "",
        _readable_table(slowest),
        "",
        "### Total Row",
        "",
        _readable_table(total),
        "",
        "### Strategy Runtime Top 10",
        "",
        _readable_table(strategy.sort_values("duration_sec", ascending=False).head(10)[["stage", "experiment_name", "duration_sec"]]),
        "",
        "### OOS / Attribution / Cost / Liquidity Guard Durations",
        "",
        _readable_table(stage_summary),
        "",
    ]


def _write_sqlite_state_report(latest_dir, db_path):
    try:
        import sqlite3
        summary = get_state_summary(str(db_path))
        summary["paper_open_positions_count"] = "not_available"
        if Path(db_path).exists():
            conn = None
            try:
                conn = sqlite3.connect(str(db_path))
                trades = pd.read_sql_query("SELECT * FROM paper_trades", conn)
                summary["paper_open_positions_count"] = len(net_open_positions_from_trade_log(trades))
            except Exception:
                summary["paper_open_positions_count"] = "not_available"
            finally:
                if conn:
                    conn.close()

        # Write CSV
        csv_path = latest_dir / "sqlite_state_report.csv"
        csv_lines = ["metric,value"]
        for k, v in summary.items():
            if k != "error_msg":
                csv_lines.append(f"{k},{v}")
        csv_path.write_text("\n".join(csv_lines), encoding="utf-8")

        # Write MD
        md_path = latest_dir / "sqlite_state_report.md"
        md_lines = [
            "# SQLite State Report",
            "",
            f"- db_path: {summary.get('db_path')}",
            f"- db_exists: {summary.get('db_exists')}",
            f"- table_count: {summary.get('table_count')}",
            f"- pipeline_runs_count: {summary.get('pipeline_runs_count')}",
            f"- paper_orders_count: {summary.get('paper_orders_count')}",
            f"- paper_trades_count: {summary.get('paper_trades_count')}",
            f"- paper_open_positions_count: {summary.get('paper_open_positions_count')}",
            f"- order_intents_count: {summary.get('order_intents_count')}",
            f"- execution_audit_count: {summary.get('execution_audit_count')}",
            f"- readiness_checks_count: {summary.get('readiness_checks_count')}",
            f"- latest_run_id: {summary.get('latest_run_id')}",
            f"- status: {summary.get('status')}",
        ]
        if summary.get("error_msg"):
            md_lines.extend(["", f"Warning: {summary.get('error_msg')}"])
        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        return summary
    except Exception as e:
        print(f"WARNING: _write_sqlite_state_report failed: {e}")
        return {"status": "WARNING", "error_msg": str(e)}


def _write_chatgpt_master_report(
    report,
    audit,
    oos,
    attribution,
    attribution_summary,
    cost_stress,
    liquidity_guard_sensitivity,
    paper_report,
    profile_rows,
    output_path,
    *,
    final_decision_path=None,
    command,
    report_mode,
    flags,
    total_runtime_sec,
    cache_summary,
    daily_pipeline_status=None,
    operational_health=None,
):
    try:
        from .runner import _final_candidate_decision_frame, _read_final_candidate_decision_report
    except ImportError:
        from runner import _final_candidate_decision_frame, _read_final_candidate_decision_report
    latest_report_dir = Path(final_decision_path).parent if final_decision_path else Path(output_path).parent
    sqlite_db_path = latest_report_dir.parent.parent / "state" / "trading_state.sqlite3"
    sqlite_summary = get_state_summary(str(sqlite_db_path))
    if operational_health is None:
        operational_health = {}
    operational_health["sqlite"] = sqlite_summary

    decision = _final_candidate_decision_frame(report, oos, attribution_summary, cost_stress)
    source_decision = None
    if oos is None or attribution_summary is None or cost_stress is None:
        source_decision = _read_final_candidate_decision_report(final_decision_path)
    if source_decision is not None and not source_decision.empty:
        decision = source_decision
    included = [
        "final_candidate_decision_report.md",
        "data_audit_report.md",
        "benchmark_audit_report.md",
        "strategy_compact_report.md",
        "oos_walkforward_report.md" if oos is not None else "oos_walkforward_report.md (not generated)",
        "candidate_attribution_report.md" if attribution is not None else "candidate_attribution_report.md (not generated)",
        "cost_slippage_stress_report.md" if cost_stress is not None else "cost_slippage_stress_report.md (not generated)",
        "liquidity_guard_sensitivity_report.md" if liquidity_guard_sensitivity is not None else "liquidity_guard_sensitivity_report.md (not generated)",
        "paper_signal_report.md" if paper_report is not None else "paper_signal_report.md (not generated)",
        "paper_trading_plan.md" if paper_report is not None else "paper_trading_plan.md (not generated)",
        "performance_profile_report.md",
    ]
    top30_row = decision[decision["experiment_name"].eq("rs_m126_top30_sma100_no_regime")]
    top30_main = bool(
        not top30_row.empty
        and str(top30_row.iloc[0].get("final_role", "")).lower() == "main"
        and pd.to_numeric(
            pd.Series([top30_row.iloc[0].get("excess_vs_qqq_pct", pd.NA)]),
            errors="coerce",
        ).iloc[0] > 0
    )
    next_step = "Next step: Review latest paper signals and run scripts/run_daily_paper_pipeline.bat for the next daily cycle."
    if daily_pipeline_status:
        pending_next_open = int(daily_pipeline_status.get("pending_next_open_count", 0) or 0)
        filled_this_run = int(daily_pipeline_status.get("filled_count_this_run", 0) or 0)
        if pending_next_open > 0 and filled_this_run == 0:
            next_step = "Wait for next valid market open data, then run scripts/run_daily_paper_pipeline.bat again."
        elif filled_this_run > 0:
            next_step = "Review filled paper trades and compare fills vs expected next open."
    if paper_report is None:
        next_step = (
        "Next step: paper trading checklist / execution plan."
        if liquidity_guard_sensitivity is not None and top30_main
        else "Next step: liquidity guard sensitivity."
        )
    lines = [
        "# ChatGPT Master Report",
        "",
        "## Run Summary",
        "",
        f"- run datetime: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- command: `{command}`",
        f"- reports mode: {report_mode}",
        f"- run_oos: {bool(flags.get('run_oos'))}",
        f"- run_attribution: {bool(flags.get('run_attribution'))}",
        f"- run_cost_stress: {bool(flags.get('run_cost_stress'))}",
        f"- run_liquidity_guard_sensitivity: {bool(flags.get('run_liquidity_guard_sensitivity'))}",
        f"- run_robustness: {bool(flags.get('run_robustness'))}",
        f"- paper_report: {bool(flags.get('paper_report'))}",
        f"- total_runtime_sec: {round(float(total_runtime_sec), 4)}",
        f"- prepared_cache_rebuilt: {cache_summary.get('prepared_cache_rebuilt', False)}",
        f"- prepared_cache_hit: {cache_summary.get('prepared_cache_hit', False)}",
        "- strategy metrics changed: false",
        "- benchmark changed: false",
        f"- included reports: {', '.join(included)}",
        "- excluded large tables: full data audit per-symbol table; full candidate attribution beyond top 10 per candidate; full performance all-stages table.",
        "",
        "## Current Final Decision",
        "",
        _readable_table(_safe_columns(
            decision,
            [
                "experiment_name",
                "full_period_return",
                "oos_avg_return",
                "oos_min_return",
                "top1_contribution_pct",
                "top5_contribution_pct",
                "cost_survives",
                "liquidity_warning_count",
                "max_drawdown_pct",
                "excess_vs_qqq_pct",
                "final_role",
            ],
        )),
        "",
    ]
    lines.extend(_chatgpt_data_audit_section(audit))
    lines.extend(_chatgpt_benchmark_section(report))
    lines.extend(_chatgpt_strategy_section(report))
    lines.extend(_chatgpt_oos_section(oos))
    lines.extend(_chatgpt_attribution_section(attribution, attribution_summary))
    lines.extend(_chatgpt_cost_section(cost_stress))
    lines.extend(_chatgpt_liquidity_guard_section(liquidity_guard_sensitivity))
    lines.extend(_chatgpt_daily_pipeline_status_section(daily_pipeline_status))
    lines.extend(_chatgpt_operational_health_section(operational_health))
    lines.extend(_chatgpt_paper_readiness_section(paper_report))
    lines.extend(_chatgpt_performance_section(profile_rows))
    latest_report_dir = Path(final_decision_path).parent if final_decision_path else Path(output_path).parent
    lines.extend(_chatgpt_test_suite_status_section(latest_report_dir))
    lines.extend(_chatgpt_existing_execution_readiness_section(latest_report_dir))
    lines.extend(_chatgpt_useful_commands_section())
    lines.extend(
        [
            "## Warnings / Open Issues",
            "",
            "- Backtest survivorship-bias-free degildir.",
            "- Adjusted data dogrulanmali.",
            "- Transaction cost/slippage varsayimsaldir.",
            "- Liquidity proxy dollar volume bazlidir; real spread/order book icermez.",
            "- Paper trading oncesi execution plan gerekir.",
            "- Canli trading hazir denmemeli.",
            "",
            "## Next Recommended Step",
            "",
            next_step,
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _markdown_bullet_value(path, key, default="not_generated"):
    if not Path(path).exists():
        return default
    prefix = f"- {key}:"
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().lower().startswith(prefix.lower()):
            return line.split(":", 1)[1].strip()
    return default


def _chatgpt_test_suite_status_section(latest_dir):
    path = Path(latest_dir) / "test_run_report.md"
    if not path.exists():
        return [
            "## Test Suite Status",
            "",
            "- status: not_generated",
            "- tests ran count: not_generated",
            "- last run datetime: not_generated",
            "- path: reports/latest/test_run_report.md",
            "",
        ]
    return [
        "## Test Suite Status",
        "",
        f"- status: {_markdown_bullet_value(path, 'status')}",
        f"- tests ran count: {_markdown_bullet_value(path, 'tests ran count')}",
        f"- last run datetime: {_markdown_bullet_value(path, 'run datetime')}",
        "- path: reports/latest/test_run_report.md",
        "",
    ]


def _chatgpt_existing_execution_readiness_section(latest_dir):
    latest_dir = Path(latest_dir)
    preview_path = latest_dir / "execution_preview_report.md"
    pre_submit_path = latest_dir / "pre_submit_readiness_report.md"
    pre_submit_csv_path = latest_dir / "pre_submit_readiness_report.csv"
    audit_path = latest_dir / "execution_audit_log.csv"
    if not (preview_path.exists() and pre_submit_path.exists() and audit_path.exists()):
        return []
    audit = pd.DataFrame()
    try:
        audit = pd.read_csv(audit_path)
    except Exception:
        audit = pd.DataFrame()
    submit_order_called = False
    post_request_used = False
    if not audit.empty and "event_type" in audit:
        submit_order_called = audit["event_type"].astype(str).str.contains("SUBMIT_ORDER", case=False, na=False).any()
        post_request_used = audit["event_type"].astype(str).str.contains("POST", case=False, na=False).any()
    data_policy_status = "not_generated"
    data_policy_value = "not_generated"
    if pre_submit_csv_path.exists():
        try:
            pre_submit = pd.read_csv(pre_submit_csv_path)
            row = pre_submit[pre_submit.get("check_name", pd.Series(dtype=str)).astype(str).eq("data_policy_operator_review")]
            if not row.empty:
                data_policy_status = str(row.iloc[0].get("status", "not_generated"))
                data_policy_value = str(row.iloc[0].get("value", "not_generated"))
        except Exception:
            data_policy_status = "read_error"
            data_policy_value = "read_error"
    return [
        "## Execution Bot Readiness",
        "",
        "- execution_preview_status: OK",
        "- broker_mode: paper/dry-run",
        f"- broker_submit_enabled: {_markdown_bullet_value(preview_path, 'broker_submit_enabled', 'false')}",
        f"- live_trading_enabled: {_markdown_bullet_value(preview_path, 'live_trading_enabled', 'false')}",
        f"- order_intents_count: {_markdown_bullet_value(preview_path, 'order intents count', '0')}",
        f"- risk_pass_count: {_markdown_bullet_value(preview_path, 'risk passed count', '0')}",
        f"- would_submit_count: {_markdown_bullet_value(preview_path, 'would_submit count', '0')}",
        f"- submit_allowed: {_markdown_bullet_value(pre_submit_path, 'submit_allowed', 'False')}",
        f"- data_policy_operator_review_status: {data_policy_status}",
        f"- data_policy_operator_review_unresolved_count: {data_policy_value}",
        f"- submit_order_called: {str(bool(submit_order_called)).lower()}",
        f"- post_request_used: {str(bool(post_request_used)).lower()}",
        "- next action: Review pre-submit blocks and keep broker_submit_enabled false until explicit approval.",
        "",
    ]


def _chatgpt_useful_commands_section():
    return [
        "## Useful Commands",
        "",
        "### Double-click BAT",
        "",
        "- Daily paper pipeline: `scripts/run_daily_paper_pipeline.bat`",
        "- Test suite: `scripts/run_tests.bat`",
        "- Paper report only: `scripts/run_paper_report.bat`",
        "- Data policy review: `scripts/run_data_policy_review.bat`",
        "- Basic backtest: `scripts/run_basic.bat`",
        "- Alpaca paper check: `scripts/run_alpaca_check.bat`",
        "- Full diagnostic: `scripts/run_full_diagnostic.bat`",
        "- Robustness research: `scripts/run_robustness.bat`",
        "",
        "### Advanced / PowerShell Alternative",
        "",
        "- Paper report only: `scripts/run_paper_report.ps1`",
        "- Basic backtest: `scripts/run_basic.ps1`",
        "- Alpaca paper check: `scripts/run_alpaca_check.ps1`",
        "- Full diagnostic: `scripts/run_full_diagnostic.ps1`",
        "- Robustness research: `scripts/run_robustness.ps1`",
        "",
    ]
