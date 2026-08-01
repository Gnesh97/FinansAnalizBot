"""Research/validation reports: OOS walk-forward, candidate attribution, cost-slippage
stress, liquidity-guard sensitivity, RS robustness, and the final candidate decision/summary
reports.

Extracted from testdata/runner.py (Phase 16, Cluster 4) to shrink that file. Behavior-
preserving move -- function bodies are unchanged, only their file and imports moved. All 35
functions here are called from exactly one place, run_strategy_experiments (which stays in
runner.py -- it is itself flagged separately for its own internal decomposition), which is
research/validation CLI tooling, not part of the live nightly DSS chain (confirmed: no
reference to run_strategy_experiments anywhere in dss_scheduler.py).

Four runner.py functions that were not part of this extraction are called throughout this
module: _table_for_markdown, _build_experiment_tasks, _run_experiment_batch, _trade_stats.
Rather than repeating a lazy try/except import in every one of the many call sites (they are
called from ~10 different functions), each is given a single same-named shim here that does
the lazy back-import once and delegates -- so every moved function body is untouched,
byte-for-byte identical to its original in runner.py. This avoids a circular top-level import
(runner.py imports this module at load time; this module must not import runner.py at load
time in return).

Conversely, runner.py imports all 35 names back from here: bot.py/tests/quantconnect_port_contract.py/
baseline_parity.py import several of these (_rs_robustness_experiments, _oos_walkforward_periods,
_final_candidate_decision_frame, _read_final_candidate_decision_report) directly from
testdata.runner, and runner.py's own run_strategy_experiments calls all 35.
"""

from __future__ import annotations

import pandas as pd

try:
    from .reports import SURVIVORSHIP_BIAS_BANNER
except ImportError:
    from reports import SURVIVORSHIP_BIAS_BANNER


def _table_for_markdown(frame):
    try:
        from .runner import _table_for_markdown as _runner_table_for_markdown
    except ImportError:
        from runner import _table_for_markdown as _runner_table_for_markdown
    return _runner_table_for_markdown(frame)


def _build_experiment_tasks(*args, **kwargs):
    try:
        from .runner import _build_experiment_tasks as _runner_build_experiment_tasks
    except ImportError:
        from runner import _build_experiment_tasks as _runner_build_experiment_tasks
    return _runner_build_experiment_tasks(*args, **kwargs)


def _run_experiment_batch(*args, **kwargs):
    try:
        from .runner import _run_experiment_batch as _runner_run_experiment_batch
    except ImportError:
        from runner import _run_experiment_batch as _runner_run_experiment_batch
    return _runner_run_experiment_batch(*args, **kwargs)


def _trade_stats(*args, **kwargs):
    try:
        from .runner import _trade_stats as _runner_trade_stats
    except ImportError:
        from runner import _trade_stats as _runner_trade_stats
    return _runner_trade_stats(*args, **kwargs)


def _yearly_focus_names():
    return [
        "rs_m63_sma100",
        "rs_m63_no_regime",
        "dm_m63_top20",
        "rs_m126_top10_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "rs_m126_top30_sma100_no_regime",
        "rs_m126_top30_sma200_no_regime",
        "rs_m84_top30_sma100_no_regime",
        "rs_m63_top20_sma100_regime",
        "rs_m63_top30_sma100_regime",
    ]


def _write_yearly_report(yearly, csv_path, markdown_path):
    yearly.to_csv(csv_path, index=False)
    try:
        table = yearly.to_markdown(index=False)
    except ImportError:
        table = yearly.to_string(index=False)
    markdown_path.write_text(
        "\n".join(["# Yearly Performance Report", "", SURVIVORSHIP_BIAS_BANNER, "", table, ""]),
        encoding="utf-8",
    )


def _rs_robustness_experiments():
    experiments = []
    for lookback in [42, 63, 84, 126]:
        for top_n in [10, 20, 30]:
            for sma_days in [100, 200]:
                for regime in [True, False]:
                    suffix = "regime" if regime else "no_regime"
                    experiments.append(
                        {
                            "name": f"rs_m{lookback}_top{top_n}_sma{sma_days}_{suffix}",
                            "strategy_name": "relative_strength_rotation",
                            "excluded_symbols": ["QQQ", "SPY"],
                            "rs_lookback_days": lookback,
                            "rs_top_n": top_n,
                            "rs_rebalance_frequency": "monthly",
                            "rs_score_mode": "return_over_vol",
                            "rs_vol_lookback_days": 60,
                            "rs_trend_sma_days": sma_days,
                            "rs_use_market_regime": regime,
                            "rs_market_regime_sma_days": 200,
                            "rs_min_price": 10,
                            "rs_max_atr_pct": 12,
                        }
                    )
    return experiments


def _oos_walkforward_periods():
    """OOS walk-forward windows as (name, start, end).

    The bare "YYYY-MM-DD" end bound is INCLUSIVE of that session here, because
    every frame reaching _slice_frame is America/New_York-indexed (see
    data_engine._to_new_york_index) and a daily bar therefore sits at exactly
    00:00 NY -- equal to the bound, kept by `<=`. Phase 7C measured all five
    periods both ways and the row counts are identical. Do not "fix" this
    without re-measuring; see tests/test_slice_frame_end_bound_invariant.py,
    which pins the invariant this relies on.
    """
    return [
        ("split_2020_2022", "2020-07-27", "2022-12-31"),
        ("split_2023_2024", "2023-01-01", "2024-12-31"),
        ("split_2025_2026", "2025-01-01", "2026-06-18"),
        ("anchored_insample_2020_2023", "2020-07-27", "2023-12-31"),
        ("anchored_oos_2024_2026", "2024-01-01", "2026-06-18"),
    ]


def _oos_candidate_experiments(base_experiments):
    candidates = {experiment["name"]: dict(experiment) for experiment in base_experiments}
    for experiment in _rs_robustness_experiments():
        if experiment["name"] in {
            "rs_m126_top30_sma100_no_regime",
            "rs_m126_top20_sma100_no_regime",
            "rs_m126_top10_sma100_no_regime",
            "rs_m126_top30_sma200_no_regime",
        }:
            candidates[experiment["name"]] = experiment
    wanted = [
        "rs_m126_top30_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "rs_m126_top10_sma100_no_regime",
        "rs_m126_top30_sma200_no_regime",
        "donchian_20_10_top20",
        "rs_m63_sma100",
        "dm_m63_top20",
        "mast_sma100_slope20_top20",
    ]
    missing = [name for name in wanted if name not in candidates]
    if missing:
        raise ValueError(f"OOS aday config eksik: {', '.join(missing)}")
    return [candidates[name] for name in wanted]


def _oos_report_row(result, period_name, start_date, end_date):
    row = result["row"]
    net_return = pd.to_numeric(pd.Series([row.get("Net Getiri (%)")]), errors="coerce").iloc[0]
    max_dd = pd.to_numeric(pd.Series([row.get("Max Drawdown (%)")]), errors="coerce").iloc[0]
    qqq_return = pd.to_numeric(pd.Series([row.get("QQQ BuyHold Return (%)")]), errors="coerce").iloc[0]
    ew_return = pd.to_numeric(
        pd.Series([row.get("EqualWeight Universe Return (%)")]),
        errors="coerce",
    ).iloc[0]
    return {
        "experiment_name": row.get("Experiment Name", result["experiment_name"]),
        "period_name": period_name,
        "start_date": start_date,
        "end_date": end_date,
        "net_return_pct": row.get("Net Getiri (%)", pd.NA),
        "max_drawdown_pct": row.get("Max Drawdown (%)", pd.NA),
        "trade_count": row.get("Trade Count", pd.NA),
        "profit_factor": row.get("Profit Factor", pd.NA),
        "expectancy_r": row.get("Expectancy R", pd.NA),
        "win_rate": row.get("Win Rate (%)", pd.NA),
        "avg_holding_bars": row.get("Avg Holding Bars", pd.NA),
        "worst_r": row.get("Worst R", pd.NA),
        "qqq_return_pct": row.get("QQQ BuyHold Return (%)", pd.NA),
        "equal_weight_return_pct": row.get("EqualWeight Universe Return (%)", pd.NA),
        "excess_vs_qqq_pct": row.get("Strategy Excess Return vs QQQ (%)", pd.NA),
        "excess_vs_equal_weight_pct": row.get(
            "Strategy Excess Return vs EqualWeight (%)",
            pd.NA,
        ),
        "return_over_maxdd": (
            round(float(net_return) / abs(float(max_dd)), 4)
            if pd.notna(net_return) and pd.notna(max_dd) and float(max_dd) != 0
            else pd.NA
        ),
        "beats_qqq": bool(pd.notna(net_return) and pd.notna(qqq_return) and net_return > qqq_return),
        "beats_equal_weight": bool(pd.notna(net_return) and pd.notna(ew_return) and net_return > ew_return),
    }


def _write_oos_walkforward_report(oos, csv_path, markdown_path):
    output_columns = [
        "experiment_name",
        "period_name",
        "start_date",
        "end_date",
        "net_return_pct",
        "max_drawdown_pct",
        "trade_count",
        "profit_factor",
        "expectancy_r",
        "win_rate",
        "avg_holding_bars",
        "worst_r",
        "qqq_return_pct",
        "equal_weight_return_pct",
        "excess_vs_qqq_pct",
        "excess_vs_equal_weight_pct",
        "return_over_maxdd",
    ]
    output = oos[output_columns].copy()
    output.to_csv(csv_path, index=False)
    ranked_parts = []
    for period_name, group in oos.groupby("period_name", sort=False):
        top = group.sort_values(
            ["return_over_maxdd", "net_return_pct"],
            ascending=[False, False],
            na_position="last",
        ).head(5)
        ranked_parts.append(f"### {period_name}")
        ranked_parts.append("")
        ranked_parts.append(_table_for_markdown(top[output_columns]))
        ranked_parts.append("")
    numeric = oos.copy()
    for column in ["net_return_pct", "max_drawdown_pct", "return_over_maxdd"]:
        numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
    stability = numeric.groupby("experiment_name").agg(
        periods=("period_name", "count"),
        avg_return_pct=("net_return_pct", "mean"),
        min_return_pct=("net_return_pct", "min"),
        return_std_pct=("net_return_pct", "std"),
        worst_drawdown_pct=("max_drawdown_pct", "min"),
        qqq_wins=("beats_qqq", "sum"),
        equal_weight_wins=("beats_equal_weight", "sum"),
    ).round(4).reset_index()
    topn = numeric[numeric["experiment_name"].str.contains("top", na=False)].copy()
    topn["top_n"] = topn["experiment_name"].str.extract(r"top(\d+)").astype(float)
    topn_summary = topn.groupby("top_n").agg(
        avg_return_pct=("net_return_pct", "mean"),
        min_return_pct=("net_return_pct", "min"),
        avg_return_over_maxdd=("return_over_maxdd", "mean"),
    ).round(4).reset_index()
    momentum_period = numeric[numeric["period_name"].eq("split_2023_2024")][
        [
            "experiment_name",
            "net_return_pct",
            "qqq_return_pct",
            "excess_vs_qqq_pct",
            "beats_qqq",
        ]
    ].sort_values("excess_vs_qqq_pct", ascending=False, na_position="last")
    content = [
        "# OOS Walkforward Report",
        "",
        SURVIVORSHIP_BIAS_BANNER,
        "",
        "## Best 5 By Period",
        "",
        *ranked_parts,
        "## Stability",
        "",
        _table_for_markdown(stability),
        "",
        "## Top10 / Top20 / Top30",
        "",
        _table_for_markdown(topn_summary),
        "",
        "## 2023-2024 Momentum Period vs QQQ",
        "",
        _table_for_markdown(momentum_period),
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _attribution_candidate_experiments(base_experiments):
    candidates = {experiment["name"]: dict(experiment) for experiment in base_experiments}
    for experiment in _rs_robustness_experiments():
        if experiment["name"] in {
            "rs_m126_top10_sma100_no_regime",
            "rs_m126_top20_sma100_no_regime",
            "rs_m126_top30_sma100_no_regime",
        }:
            candidates[experiment["name"]] = experiment
    wanted = [
        "rs_m126_top10_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "rs_m126_top30_sma100_no_regime",
        "donchian_20_10_top20",
        "rs_m63_sma100",
    ]
    missing = [name for name in wanted if name not in candidates]
    if missing:
        raise ValueError(f"Attribution aday config eksik: {', '.join(missing)}")
    return [candidates[name] for name in wanted]


def _candidate_attribution_rows(attribution_results):
    rows = []
    summaries = []
    for result in attribution_results:
        experiment_name = result["experiment_name"]
        metrics = (result.get("raw") or {}).get("metrics", {}) or {}
        trades = pd.DataFrame(metrics.get("_trade_log", []))
        if trades.empty or "Symbol" not in trades:
            summaries.append(
                {
                    "experiment_name": experiment_name,
                    "top_1_symbol_contribution_pct": 0.0,
                    "top_3_symbols_contribution_pct": 0.0,
                    "top_5_symbols_contribution_pct": 0.0,
                    "top_10_symbols_contribution_pct": 0.0,
                    "profitable_symbol_count": 0,
                    "losing_symbol_count": 0,
                    "total_traded_symbol_count": 0,
                    "largest_losing_symbol": "",
                    "largest_winning_symbol": "",
                }
            )
            continue
        trades["Net PnL"] = pd.to_numeric(trades.get("Net PnL"), errors="coerce").fillna(0.0)
        trades["R Multiple"] = pd.to_numeric(trades.get("R Multiple"), errors="coerce")
        trades["Bars Held"] = pd.to_numeric(trades.get("Bars Held"), errors="coerce")
        total_pnl = float(trades["Net PnL"].sum())
        grouped_rows = []
        for symbol, group in trades.groupby("Symbol", sort=True):
            pnl = group["Net PnL"]
            r_values = group["R Multiple"].dropna()
            bars = group["Bars Held"].dropna()
            symbol_pnl = float(pnl.sum())
            grouped_rows.append(
                {
                    "experiment_name": experiment_name,
                    "symbol": symbol,
                    "trade_count": int(len(group)),
                    "win_rate": round(float((pnl > 0).mean() * 100), 2) if len(pnl) else 0.0,
                    "total_pnl": round(symbol_pnl, 2),
                    "total_r": round(float(r_values.sum()), 4) if not r_values.empty else 0.0,
                    "avg_r": round(float(r_values.mean()), 4) if not r_values.empty else 0.0,
                    "median_r": round(float(r_values.median()), 4) if not r_values.empty else 0.0,
                    "best_r": round(float(r_values.max()), 4) if not r_values.empty else 0.0,
                    "worst_r": round(float(r_values.min()), 4) if not r_values.empty else 0.0,
                    "contribution_pct_of_total_pnl": (
                        round(symbol_pnl / total_pnl * 100, 2) if total_pnl else 0.0
                    ),
                    "avg_holding_bars": round(float(bars.mean()), 2) if not bars.empty else 0.0,
                }
            )
        grouped_rows.sort(key=lambda item: item["total_pnl"], reverse=True)
        cumulative = 0
        for item in grouped_rows:
            cumulative += 1
            item["cumulative_contribution_rank"] = cumulative
        rows.extend(grouped_rows)
        positive = [item for item in grouped_rows if item["total_pnl"] > 0]
        negative = [item for item in grouped_rows if item["total_pnl"] < 0]

        def top_pct(count):
            if not total_pnl:
                return 0.0
            return round(sum(item["total_pnl"] for item in grouped_rows[:count]) / total_pnl * 100, 2)

        summaries.append(
            {
                "experiment_name": experiment_name,
                "top_1_symbol_contribution_pct": top_pct(1),
                "top_3_symbols_contribution_pct": top_pct(3),
                "top_5_symbols_contribution_pct": top_pct(5),
                "top_10_symbols_contribution_pct": top_pct(10),
                "profitable_symbol_count": len(positive),
                "losing_symbol_count": len(negative),
                "total_traded_symbol_count": len(grouped_rows),
                "largest_losing_symbol": negative[-1]["symbol"] if negative else "",
                "largest_winning_symbol": positive[0]["symbol"] if positive else "",
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(summaries)


def _write_candidate_attribution_report(attribution, summary, csv_path, markdown_path):
    columns = [
        "experiment_name",
        "symbol",
        "trade_count",
        "win_rate",
        "total_pnl",
        "total_r",
        "avg_r",
        "median_r",
        "best_r",
        "worst_r",
        "contribution_pct_of_total_pnl",
        "cumulative_contribution_rank",
        "avg_holding_bars",
    ]
    output = attribution[columns].copy() if not attribution.empty else pd.DataFrame(columns=columns)
    output.to_csv(csv_path, index=False)
    warnings = []
    concentration_by_name = {}
    summary_by_name = (
        summary.set_index("experiment_name").to_dict("index")
        if isinstance(summary, pd.DataFrame)
        and not summary.empty
        and "experiment_name" in summary
        else {}
    )
    for name, group in output.groupby("experiment_name", sort=False):
        ranked = group.sort_values("total_pnl", ascending=False)
        total_pnl = float(pd.to_numeric(ranked["total_pnl"], errors="coerce").fillna(0.0).sum())
        top1 = float(pd.to_numeric(ranked.head(1)["total_pnl"], errors="coerce").fillna(0.0).sum())
        top5 = float(pd.to_numeric(ranked.head(5)["total_pnl"], errors="coerce").fillna(0.0).sum())
        top1_pct = round(top1 / total_pnl * 100, 2) if total_pnl else 0.0
        top5_pct = round(top5 / total_pnl * 100, 2) if total_pnl else 0.0
        concentration_by_name[name] = {"top1": top1_pct, "top5": top5_pct}
        if top5_pct > 50:
            warnings.append(f"- Warning: {name} top 5 symbols contribute >50% of total PnL.")
        if top1_pct > 25:
            warnings.append(f"- Warning: {name} top 1 symbol contributes >25% of total PnL.")
        if len(group) < 20:
            warnings.append(f"- Warning: {name} traded symbol count is low.")
    top10_concentration = concentration_by_name.get("rs_m126_top10_sma100_no_regime", {})
    if float(top10_concentration.get("top5", 0.0)) > 50:
        warnings.append("- Warning: Top10 strategy is highly concentrated.")
    if not warnings:
        warnings = ["- No concentration warning triggered."]
    rs_compare = []
    for name in [
        "rs_m126_top10_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "rs_m126_top30_sma100_no_regime",
    ]:
        item = summary_by_name.get(name, {})
        rs_compare.append(
            f"- {name}: top1={item.get('top_1_symbol_contribution_pct', 0.0)}%, "
            f"top5={item.get('top_5_symbols_contribution_pct', 0.0)}%, "
            f"traded_symbols={item.get('total_traded_symbol_count', 0)}."
        )
    donchian_summary = summary_by_name.get("donchian_20_10_top20", {})
    top_rows = output.sort_values(
        ["experiment_name", "cumulative_contribution_rank"],
        ascending=[True, True],
    ).groupby("experiment_name", sort=False).head(10)
    content = [
        "# Candidate Attribution Report",
        "",
        SURVIVORSHIP_BIAS_BANNER,
        "",
        "## Concentration Summary",
        "",
        _table_for_markdown(summary),
        "",
        "## Risk Warnings",
        "",
        *warnings,
        "",
        "## Balance And Concentration Read",
        "",
        *rs_compare,
        "- Top10 is the aggressive/high-concentration RS candidate when top1/top5 contribution is materially above Top20/Top30.",
        "- Top20 is the middle RS balance point; Top30 is the broader RS candidate when concentration reduction is preferred.",
        (
            "- Donchian is a different strategy-family backup; "
            f"top5={donchian_summary.get('top_5_symbols_contribution_pct', 0.0)}%, "
            f"traded_symbols={donchian_summary.get('total_traded_symbol_count', 0)}."
        ),
        "",
        "## Top Symbols By Candidate",
        "",
        _table_for_markdown(top_rows),
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _run_candidate_attribution_report(
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
):
    tasks = _build_experiment_tasks(
        _attribution_candidate_experiments(experiments),
        data_folder,
        run_profile,
        quick_mode,
        intraday_dir,
        daily_dir,
        raw_dir,
        intraday_timeframe,
        daily_timeframe,
        cache_rebuilt,
        include_raw=True,
    )
    results, batch_sec = _run_experiment_batch(
        tasks,
        asset_data,
        benchmark_data,
        cache_path,
        parallel_enabled=parallel_enabled,
        workers=worker_count,
    )
    attribution, summary = _candidate_attribution_rows(results)
    return attribution, summary, batch_sec


def _cost_stress_candidate_experiments(base_experiments):
    return _attribution_candidate_experiments(base_experiments)


def _cost_stress_scenarios():
    return [
        ("zero_cost_baseline", 0.0, 0.0),
        ("commission_0_slippage_5bps_per_side", 5.0, 0.0),
        ("commission_0_slippage_10bps_per_side", 10.0, 0.0),
        ("commission_0_slippage_25bps_per_side", 25.0, 0.0),
        ("commission_0_slippage_50bps_per_side", 50.0, 0.0),
        ("fixed_fee_1usd_plus_10bps_per_side", 10.0, 1.0),
    ]


def _max_positions_from_params(params):
    if params.get("strategy_name") == "donchian_breakout":
        return max(1, int(params.get("donchian_top_n", 20)))
    if params.get("strategy_name") == "relative_strength_rotation":
        return max(1, int(params.get("rs_top_n", 20)))
    return 20


def _entry_bar_dollar_volumes(trades, asset_data):
    values = []
    for _, trade in trades.iterrows():
        symbol = str(trade.get("Symbol", "")).upper()
        frame = asset_data.get(symbol)
        if frame is None or frame.empty or "Volume" not in frame or "Close" not in frame:
            continue
        entry_time = pd.Timestamp(trade.get("Entry Time"))
        index = frame.index
        if index.tz is not None and entry_time.tzinfo is None:
            entry_time = entry_time.tz_localize(index.tz)
        elif index.tz is None and entry_time.tzinfo is not None:
            entry_time = entry_time.tz_convert(None)
        pos = index.searchsorted(entry_time, side="right") - 1
        if pos < 0:
            continue
        row = frame.iloc[pos]
        close = pd.to_numeric(pd.Series([row.get("Close")]), errors="coerce").iloc[0]
        volume = pd.to_numeric(pd.Series([row.get("Volume")]), errors="coerce").iloc[0]
        if pd.notna(close) and pd.notna(volume) and close > 0 and volume > 0:
            values.append(float(close) * float(volume))
    return pd.Series(values, dtype=float)


def _trades_with_entry_liquidity(trades, asset_data):
    trades = trades.copy()
    if trades.empty:
        trades["entry_bar_dollar_volume"] = pd.Series(dtype=float)
        return trades
    values = []
    for _, trade in trades.iterrows():
        symbol = str(trade.get("Symbol", "")).upper()
        frame = asset_data.get(symbol)
        value = pd.NA
        if frame is not None and not frame.empty and "Volume" in frame and "Close" in frame:
            entry_time = pd.Timestamp(trade.get("Entry Time"))
            index = frame.index
            if index.tz is not None and entry_time.tzinfo is None:
                entry_time = entry_time.tz_localize(index.tz)
            elif index.tz is None and entry_time.tzinfo is not None:
                entry_time = entry_time.tz_convert(None)
            pos = index.searchsorted(entry_time, side="right") - 1
            if pos >= 0:
                row = frame.iloc[pos]
                close = pd.to_numeric(pd.Series([row.get("Close")]), errors="coerce").iloc[0]
                volume = pd.to_numeric(pd.Series([row.get("Volume")]), errors="coerce").iloc[0]
                if pd.notna(close) and pd.notna(volume) and close > 0 and volume > 0:
                    value = float(close) * float(volume)
        values.append(value)
    trades["entry_bar_dollar_volume"] = pd.to_numeric(
        pd.Series(values),
        errors="coerce",
    ).to_numpy()
    return trades


def _cost_adjusted_stats(trades, net_return, slippage_bps, fixed_fee):
    if trades.empty:
        return {
            "net_return_pct": round(float(net_return), 2),
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "avg_trade_return_pct": 0.0,
            "degradation_vs_zero_cost_pct": 0.0,
        }
    entry_notional = pd.to_numeric(trades.get("Entry Notional"), errors="coerce").fillna(0.0)
    shares = pd.to_numeric(trades.get("Shares"), errors="coerce").fillna(0.0)
    exit_price = pd.to_numeric(trades.get("Exit Price"), errors="coerce").fillna(0.0)
    exit_notional = shares * exit_price
    pnl = pd.to_numeric(trades.get("Net PnL"), errors="coerce").fillna(0.0)
    side_cost = float(slippage_bps) / 10000.0
    total_cost = side_cost * (entry_notional + exit_notional) + (float(fixed_fee) * 2.0)
    adjusted_pnl = pnl - total_cost
    gross_profit = adjusted_pnl[adjusted_pnl > 0].sum()
    gross_loss = adjusted_pnl[adjusted_pnl < 0].sum()
    profit_factor = (
        gross_profit / abs(gross_loss)
        if gross_loss < 0
        else (gross_profit if gross_profit > 0 else 0.0)
    )
    adjusted_net_return = float(net_return) - float(total_cost.sum()) / 10000.0 * 100.0
    adjusted_r = adjusted_pnl / entry_notional.replace(0, pd.NA)
    return {
        "net_return_pct": round(float(adjusted_net_return), 2),
        "profit_factor": round(float(profit_factor), 2),
        "expectancy_r": round(float(adjusted_r.dropna().mean()), 4)
        if not adjusted_r.dropna().empty
        else 0.0,
        "avg_trade_return_pct": round(float(adjusted_r.dropna().mean()) * 100, 4)
        if not adjusted_r.dropna().empty
        else 0.0,
        "degradation_vs_zero_cost_pct": round(float(net_return) - float(adjusted_net_return), 2),
    }


def _liquidity_stress_metrics(dollar_volumes, per_position):
    if dollar_volumes.empty:
        return {
            "median_order_pct_of_entry_bar_dollar_volume": pd.NA,
            "p10_order_pct_of_entry_bar_dollar_volume": pd.NA,
            "min_order_pct_of_entry_bar_dollar_volume": pd.NA,
            "liquidity_warning_count": 1,
            "entries_below_1m_entry_bar_dollar_volume": 0,
            "entries_below_500k_entry_bar_dollar_volume": 0,
            "entries_below_100k_entry_bar_dollar_volume": 0,
        }
    median_dv = float(dollar_volumes.median())
    p10_dv = float(dollar_volumes.quantile(0.10))
    min_dv = float(dollar_volumes.min())
    median_pct = round(float(per_position) / median_dv * 100, 6) if median_dv > 0 else pd.NA
    p10_pct = round(float(per_position) / p10_dv * 100, 6) if p10_dv > 0 else pd.NA
    min_pct = round(float(per_position) / min_dv * 100, 6) if min_dv > 0 else pd.NA
    below_1m = int((dollar_volumes < 1_000_000).sum())
    below_500k = int((dollar_volumes < 500_000).sum())
    below_100k = int((dollar_volumes < 100_000).sum())
    warning_count = int(
        (pd.notna(p10_pct) and float(p10_pct) > 1.0)
        + (pd.notna(min_pct) and float(min_pct) > 5.0)
        + (below_100k > 0)
    )
    return {
        "median_order_pct_of_entry_bar_dollar_volume": median_pct,
        "p10_order_pct_of_entry_bar_dollar_volume": p10_pct,
        "min_order_pct_of_entry_bar_dollar_volume": min_pct,
        "liquidity_warning_count": warning_count,
        "entries_below_1m_entry_bar_dollar_volume": below_1m,
        "entries_below_500k_entry_bar_dollar_volume": below_500k,
        "entries_below_100k_entry_bar_dollar_volume": below_100k,
    }


def _cost_stress_rows(cost_results, asset_data):
    rows = []
    for result in cost_results:
        row = result["row"]
        raw = result.get("raw") or {}
        metrics = raw.get("metrics", {}) or {}
        params = raw.get("params", {}) or {}
        trades = pd.DataFrame(metrics.get("_trade_log", []))
        net_return = pd.to_numeric(pd.Series([row.get("Net Getiri (%)")]), errors="coerce").iloc[0]
        max_dd = row.get("Max Drawdown (%)", pd.NA)
        qqq = row.get("QQQ BuyHold Return (%)", pd.NA)
        ew = row.get("EqualWeight Universe Return (%)", pd.NA)
        max_positions = _max_positions_from_params(params)
        dollar_volumes = _entry_bar_dollar_volumes(trades, asset_data)
        median_dv = round(float(dollar_volumes.median()), 2) if not dollar_volumes.empty else pd.NA
        p10_dv = round(float(dollar_volumes.quantile(0.10)), 2) if not dollar_volumes.empty else pd.NA
        min_dv = round(float(dollar_volumes.min()), 2) if not dollar_volumes.empty else pd.NA
        for scenario, slippage_bps, fixed_fee in _cost_stress_scenarios():
            stats = _cost_adjusted_stats(trades, net_return, slippage_bps, fixed_fee)
            if scenario == "zero_cost_baseline":
                avg_r = pd.to_numeric(pd.Series([row.get("Avg R", pd.NA)]), errors="coerce").iloc[0]
                stats.update(
                    {
                        "net_return_pct": row.get("Net Getiri (%)", pd.NA),
                        "profit_factor": row.get("Profit Factor", pd.NA),
                        "expectancy_r": row.get("Expectancy R", pd.NA),
                        "avg_trade_return_pct": round(float(avg_r) * 100, 4)
                        if pd.notna(avg_r)
                        else pd.NA,
                        "degradation_vs_zero_cost_pct": 0.0,
                    }
                )
            excess_qqq = (
                round(float(stats["net_return_pct"]) - float(qqq), 2)
                if pd.notna(stats["net_return_pct"]) and pd.notna(qqq)
                else pd.NA
            )
            excess_ew = (
                round(float(stats["net_return_pct"]) - float(ew), 2)
                if pd.notna(stats["net_return_pct"]) and pd.notna(ew)
                else pd.NA
            )
            for capital in [10000, 25000, 50000, 100000]:
                per_position = float(capital) / max_positions
                liquidity = _liquidity_stress_metrics(dollar_volumes, per_position)
                rows.append(
                    {
                        "experiment_name": row.get("Experiment Name", result["experiment_name"]),
                        "scenario": scenario,
                        "capital_assumption_usd": capital,
                        "max_positions": max_positions,
                        "per_position_notional": round(per_position, 2),
                        "net_return_pct": stats["net_return_pct"],
                        "max_drawdown_pct": max_dd,
                        "trade_count": row.get("Trade Count", pd.NA),
                        "profit_factor": stats["profit_factor"],
                        "expectancy_r": stats["expectancy_r"],
                        "avg_trade_return_pct": stats["avg_trade_return_pct"],
                        "avg_holding_bars": row.get("Avg Holding Bars", pd.NA),
                        "turnover_estimate": row.get("Turnover Estimate", pd.NA),
                        "qqq_return_pct": qqq,
                        "equal_weight_return_pct": ew,
                        "excess_vs_qqq_pct": excess_qqq,
                        "excess_vs_equal_weight_pct": excess_ew,
                        "degradation_vs_zero_cost_pct": stats["degradation_vs_zero_cost_pct"],
                        "survives_cost_test": bool(
                            pd.notna(stats["net_return_pct"])
                            and float(stats["net_return_pct"]) > 0
                            and pd.notna(stats["profit_factor"])
                            and float(stats["profit_factor"]) > 1.0
                        ),
                        "median_entry_bar_dollar_volume": median_dv,
                        "p10_entry_bar_dollar_volume": p10_dv,
                        "min_entry_bar_dollar_volume": min_dv,
                        "estimated_order_size_pct_of_entry_bar_dollar_volume": liquidity[
                            "median_order_pct_of_entry_bar_dollar_volume"
                        ],
                        **liquidity,
                    }
                )
    return pd.DataFrame(rows)


def _write_cost_slippage_stress_report(stress, csv_path, markdown_path):
    stress.to_csv(csv_path, index=False)
    base_capital = stress[stress["capital_assumption_usd"].eq(10000)].copy()
    scenario_summary = base_capital.pivot_table(
        index="experiment_name",
        columns="scenario",
        values="net_return_pct",
        aggfunc="first",
    ).reset_index()
    survivors = base_capital.groupby(["scenario", "experiment_name"])["survives_cost_test"].first().reset_index()
    ten_bps = survivors[
        survivors["scenario"].isin(
            ["commission_0_slippage_10bps_per_side", "fixed_fee_1usd_plus_10bps_per_side"]
        )
        & survivors["survives_cost_test"]
    ]["experiment_name"].drop_duplicates().tolist()
    twenty_five = survivors[
        survivors["scenario"].eq("commission_0_slippage_25bps_per_side")
        & survivors["survives_cost_test"]
    ]["experiment_name"].drop_duplicates().tolist()
    zero_rows = stress[stress["scenario"].eq("zero_cost_baseline")].copy()
    safe_by_capital = {}
    for capital, group in zero_rows.groupby("capital_assumption_usd"):
        safe_by_capital[int(capital)] = sorted(
            group.loc[
                pd.to_numeric(group["liquidity_warning_count"], errors="coerce").fillna(1).eq(0),
                "experiment_name",
            ].unique()
        )
    liquidity_summary = zero_rows[
        [
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
    ].sort_values(["capital_assumption_usd", "experiment_name"])
    warnings = []
    donchian = base_capital[base_capital["experiment_name"].eq("donchian_20_10_top20")]
    if not donchian.empty:
        turnover = pd.to_numeric(donchian["turnover_estimate"], errors="coerce").max()
        if pd.notna(turnover):
            warnings.append(f"- Donchian turnover estimate: {round(float(turnover), 4)}; compare degradation vs RS candidates.")
    top10 = base_capital[base_capital["experiment_name"].eq("rs_m126_top10_sma100_no_regime")]
    if not top10.empty:
        warnings.append("- Top10 has high return, but attribution concentration plus cost stress remains a risk.")
    top30 = base_capital[base_capital["experiment_name"].eq("rs_m126_top30_sma100_no_regime")]
    if not top30.empty:
        warnings.append("- Top30 remains the main candidate if it survives 10/25bps with lower concentration.")
        top30_liquidity = zero_rows[
            zero_rows["experiment_name"].eq("rs_m126_top30_sma100_no_regime")
        ]
        if not top30_liquidity.empty:
            max_warning = pd.to_numeric(
                top30_liquidity["liquidity_warning_count"],
                errors="coerce",
            ).fillna(0).max()
            warnings.append(
                f"- Top30 liquidity warning max across capital assumptions: {int(max_warning)}."
            )
    if not zero_rows.empty:
        median_ok = pd.to_numeric(
            zero_rows["median_order_pct_of_entry_bar_dollar_volume"],
            errors="coerce",
        ).dropna().le(1).all()
        p10_ok = pd.to_numeric(
            zero_rows["p10_order_pct_of_entry_bar_dollar_volume"],
            errors="coerce",
        ).dropna().le(1).all()
        min_problem = pd.to_numeric(
            zero_rows["min_order_pct_of_entry_bar_dollar_volume"],
            errors="coerce",
        ).dropna().gt(5).any()
        warnings.extend(
            [
                f"- Median liquidity ok across assumptions: {bool(median_ok)}.",
                f"- P10 liquidity ok across assumptions: {bool(p10_ok)}.",
                f"- Min liquidity has problem cases: {bool(min_problem)}.",
            ]
        )
    content = [
        "# Cost Slippage Stress Report",
        "",
        SURVIVORSHIP_BIAS_BANNER,
        "",
        "## Net Return By Scenario (capital 10000)",
        "",
        _table_for_markdown(scenario_summary),
        "",
        "## Summary",
        "",
        f"- 10bps survivors: {', '.join(ten_bps) if ten_bps else 'none'}",
        f"- 25bps survivors: {', '.join(twenty_five) if twenty_five else 'none'}",
        *[
            f"- Liquidity safe at ${capital}: {', '.join(names) if names else 'none'}"
            for capital, names in sorted(safe_by_capital.items())
        ],
        *warnings,
        "",
        "## Liquidity Stress",
        "",
        _table_for_markdown(liquidity_summary),
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _write_final_candidate_decision_report(report, oos, attribution_summary, cost_stress, markdown_path):
    decision = _final_candidate_decision_frame(report, oos, attribution_summary, cost_stress)

    oos_status_note = ""
    if "oos_avg_return" in decision and decision["oos_avg_return"].isna().any():
        oos_status_note = (
            "> [!CAUTION]\n"
            "> **CRITICAL WARNING: MISSING OUT-OF-SAMPLE (OOS) METRICS**\n"
            "> - **OOS metrics require the `--run-oos` flag** to be generated (e.g., `python universe_scanner.py --run-oos --run-attribution --run-cost-stress`).\n"
            "> - **Final decision analysis is incomplete** without OOS / walk-forward performance validation.\n"
            "> - **Survivorship bias remains unresolved**; absolute returns shown below are research-only and not investable evidence."
        )
    else:
        oos_status_note = (
            "> [!NOTE]\n"
            "> **Out-of-Sample (OOS) Walk-Forward Metrics Loaded**\n"
            "> - OOS metrics generated via `--run-oos` are included in the table below.\n"
            "> - **Survivorship bias remains unresolved**; absolute returns shown below are research-only and not investable evidence."
        )

    content = [
        "# Final Candidate Decision Report",
        "",
        SURVIVORSHIP_BIAS_BANNER,
        "",
        oos_status_note,
        "",
        "## Decision Table",
        "",
        _table_for_markdown(decision),
        "",
        "## Notes",
        "",
        "- Top30 is the main candidate: balanced OOS, lower concentration than Top10, and survives cost stress.",
        "- Top20 is an aggressive alternative with stronger concentration than Top30.",
        "- Donchian is a diversifier/back-up because it is a different family and attribution is more distributed.",
        "- Top10 is research/aggressive only because concentration risk remains high despite strong return and cost survival.",
        "- m63 remains the baseline control.",
        "- dm_m63_top20 and mast_sma100_slope20_top20 are included as family diversifier baselines.",
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _final_candidate_decision_frame(report, oos, attribution_summary, cost_stress):
    candidates = [
        "rs_m126_top30_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "rs_m126_top10_sma100_no_regime",
        "donchian_20_10_top20",
        "rs_m63_sma100",
        "dm_m63_top20",
        "mast_sma100_slope20_top20",
    ]
    by_name = report.set_index("Experiment Name") if "Experiment Name" in report else pd.DataFrame()
    oos_numeric = oos.copy() if isinstance(oos, pd.DataFrame) else pd.DataFrame()
    if not oos_numeric.empty:
        oos_numeric["net_return_pct"] = pd.to_numeric(oos_numeric["net_return_pct"], errors="coerce")
    attr_by_name = (
        attribution_summary.set_index("experiment_name")
        if isinstance(attribution_summary, pd.DataFrame)
        and not attribution_summary.empty
        and "experiment_name" in attribution_summary
        else pd.DataFrame()
    )
    zero_cost = (
        cost_stress[
            cost_stress["scenario"].eq("zero_cost_baseline")
            & cost_stress["capital_assumption_usd"].eq(10000)
        ].set_index("experiment_name")
        if isinstance(cost_stress, pd.DataFrame)
        and not cost_stress.empty
        and "experiment_name" in cost_stress
        else pd.DataFrame()
    )
    roles = {
        "rs_m126_top30_sma100_no_regime": "main",
        "rs_m126_top20_sma100_no_regime": "aggressive",
        "rs_m126_top10_sma100_no_regime": "research/aggressive only",
        "donchian_20_10_top20": "diversifier",
        "rs_m63_sma100": "baseline",
        "dm_m63_top20": "dual_momentum_diversifier",
        "mast_sma100_slope20_top20": "ma_slope_diversifier",
    }
    rows = []
    for name in candidates:
        base = by_name.loc[name] if name in by_name.index else {}
        oos_group = oos_numeric[oos_numeric["experiment_name"].eq(name)] if not oos_numeric.empty else pd.DataFrame()
        attr = attr_by_name.loc[name] if name in attr_by_name.index else {}
        cost = zero_cost.loc[name] if name in zero_cost.index else {}
        rows.append(
            {
                "experiment_name": name,
                "full_period_return": base.get(
                    "Net Getiri (%)",
                    cost.get("net_return_pct", pd.NA),
                ),
                "oos_avg_return": round(float(oos_group["net_return_pct"].mean()), 2)
                if not oos_group.empty
                else pd.NA,
                "oos_min_return": round(float(oos_group["net_return_pct"].min()), 2)
                if not oos_group.empty
                else pd.NA,
                "top1_contribution_pct": attr.get("top_1_symbol_contribution_pct", pd.NA),
                "top5_contribution_pct": attr.get("top_5_symbols_contribution_pct", pd.NA),
                "cost_survives": cost.get("survives_cost_test", pd.NA),
                "liquidity_warning_count": cost.get("liquidity_warning_count", pd.NA),
                "max_drawdown_pct": base.get(
                    "Max Drawdown (%)",
                    cost.get("max_drawdown_pct", pd.NA),
                ),
                "excess_vs_qqq_pct": base.get(
                    "Strategy Excess Return vs QQQ (%)",
                    cost.get("excess_vs_qqq_pct", pd.NA),
                ),
                "excess_vs_equal_weight_pct": base.get(
                    "Strategy Excess Return vs EqualWeight (%)",
                    cost.get("excess_vs_equal_weight_pct", pd.NA),
                ),
                "final_role": roles.get(name, "review"),
            }
        )
    return pd.DataFrame(rows)


def _read_final_candidate_decision_report(path):
    if path is None or not Path(path).exists():
        return None
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = lines.index("## Decision Table") + 2
    except ValueError:
        return None
    table_lines = []
    for line in lines[start:]:
        if not line.strip():
            break
        table_lines.append(line)
    if not table_lines:
        return None
    try:
        return pd.read_fwf(io.StringIO("\n".join(table_lines)))
    except Exception:
        return None


def _liquidity_guard_candidate_experiments(base_experiments):
    return _attribution_candidate_experiments(base_experiments)


def _liquidity_guard_scenarios():
    return [
        ("no_liquidity_guard", None, None),
        ("skip_entry_bar_dollar_volume_below_100k", "min_entry_bar_dollar_volume", 100_000.0),
        ("skip_entry_bar_dollar_volume_below_500k", "min_entry_bar_dollar_volume", 500_000.0),
        ("skip_entry_bar_dollar_volume_below_1m", "min_entry_bar_dollar_volume", 1_000_000.0),
        ("skip_order_pct_of_entry_bar_dollar_volume_above_1pct", "max_order_pct", 1.0),
        ("skip_order_pct_of_entry_bar_dollar_volume_above_5pct", "max_order_pct", 5.0),
    ]


def _adjusted_equity_stats(equity_curve, skipped_trades):
    equity = pd.DataFrame(equity_curve, columns=["date", "equity"])
    if equity.empty:
        return pd.NA, pd.NA
    equity["date"] = pd.to_datetime(equity["date"])
    equity["equity"] = pd.to_numeric(equity["equity"], errors="coerce")
    equity = equity.dropna(subset=["equity"]).sort_values("date")
    if equity.empty:
        return pd.NA, pd.NA
    adjusted = equity["equity"].copy()
    if not skipped_trades.empty:
        exits = skipped_trades.copy()
        exits["Exit Time"] = pd.to_datetime(exits["Exit Time"])
        exits["Net PnL"] = pd.to_numeric(exits["Net PnL"], errors="coerce").fillna(0.0)
        skipped_by_exit = exits.groupby("Exit Time")["Net PnL"].sum().sort_index()
        cumulative = []
        running = 0.0
        items = list(skipped_by_exit.items())
        item_index = 0
        for date in equity["date"]:
            while item_index < len(items) and items[item_index][0] <= date:
                running += float(items[item_index][1])
                item_index += 1
            cumulative.append(running)
        adjusted = adjusted - pd.Series(cumulative, index=equity.index)
    start_equity = float(adjusted.iloc[0])
    end_equity = float(adjusted.iloc[-1])
    net_return = (end_equity / start_equity - 1.0) * 100.0 if start_equity > 0 else pd.NA
    drawdown = ((adjusted - adjusted.cummax()) / adjusted.cummax()).min() * 100.0
    return round(float(net_return), 2), round(float(drawdown), 2)


def _liquidity_guard_mask(trades, scenario_kind, scenario_value, per_position):
    dollar_volume = pd.to_numeric(trades.get("entry_bar_dollar_volume"), errors="coerce")
    if scenario_kind is None:
        return pd.Series(False, index=trades.index)
    if scenario_kind == "min_entry_bar_dollar_volume":
        return dollar_volume.lt(float(scenario_value)).fillna(False)
    if scenario_kind == "max_order_pct":
        order_pct = (float(per_position) / dollar_volume.replace(0, pd.NA)) * 100.0
        return order_pct.gt(float(scenario_value)).fillna(False)
    raise ValueError(f"Unknown liquidity guard scenario: {scenario_kind}")


def _liquidity_guard_sensitivity_rows(results, asset_data):
    rows = []
    for result in results:
        row = result["row"]
        raw = result.get("raw") or {}
        metrics = raw.get("metrics", {}) or {}
        params = raw.get("params", {}) or {}
        trades = _trades_with_entry_liquidity(
            pd.DataFrame(metrics.get("_trade_log", [])),
            asset_data,
        )
        max_positions = _max_positions_from_params(params)
        no_guard_net = pd.to_numeric(
            pd.Series([row.get("Net Getiri (%)")]),
            errors="coerce",
        ).iloc[0]
        qqq = row.get("QQQ BuyHold Return (%)", pd.NA)
        ew = row.get("EqualWeight Universe Return (%)", pd.NA)
        for capital in [10000, 25000, 50000, 100000]:
            per_position = float(capital) / max_positions
            for scenario, scenario_kind, scenario_value in _liquidity_guard_scenarios():
                skip_mask = _liquidity_guard_mask(
                    trades,
                    scenario_kind,
                    scenario_value,
                    per_position,
                )
                skipped = trades[skip_mask].copy()
                kept = trades[~skip_mask].copy()
                if scenario == "no_liquidity_guard":
                    net_return = row.get("Net Getiri (%)", pd.NA)
                    max_dd = row.get("Max Drawdown (%)", pd.NA)
                    stats = {
                        "trade_count": row.get("Trade Count", pd.NA),
                        "profit_factor": row.get("Profit Factor", pd.NA),
                        "expectancy_r": row.get("Expectancy R", pd.NA),
                        "avg_holding_bars": row.get("Avg Holding Bars", pd.NA),
                    }
                else:
                    net_return, max_dd = _adjusted_equity_stats(
                        metrics.get("_equity_curve", []),
                        skipped,
                    )
                    stats = _trade_stats(kept)
                trade_count = int(len(trades))
                skipped_count = int(len(skipped))
                skipped_pct = round(skipped_count / trade_count * 100.0, 2) if trade_count else 0.0
                excess_qqq = (
                    round(float(net_return) - float(qqq), 2)
                    if pd.notna(net_return) and pd.notna(qqq)
                    else pd.NA
                )
                excess_ew = (
                    round(float(net_return) - float(ew), 2)
                    if pd.notna(net_return) and pd.notna(ew)
                    else pd.NA
                )
                degradation = (
                    round(float(no_guard_net) - float(net_return), 2)
                    if pd.notna(no_guard_net) and pd.notna(net_return)
                    else pd.NA
                )
                pass_metric = (
                    pd.notna(net_return)
                    and float(net_return) > 0.0
                    and pd.notna(stats["profit_factor"])
                    and float(stats["profit_factor"]) > 1.0
                )
                rows.append(
                    {
                        "experiment_name": row.get("Experiment Name", result["experiment_name"]),
                        "guard_scenario": scenario,
                        "capital_assumption_usd": capital,
                        "net_return_pct": net_return,
                        "max_drawdown_pct": max_dd,
                        "trade_count": stats["trade_count"],
                        "skipped_trade_count": skipped_count,
                        "skipped_trade_pct": skipped_pct,
                        "profit_factor": stats["profit_factor"],
                        "expectancy_r": stats["expectancy_r"],
                        "avg_holding_bars": stats["avg_holding_bars"],
                        "qqq_return_pct": qqq,
                        "equal_weight_return_pct": ew,
                        "excess_vs_qqq_pct": excess_qqq,
                        "excess_vs_equal_weight_pct": excess_ew,
                        "degradation_vs_no_guard_pct": degradation,
                        "liquidity_guard_pass": bool(pass_metric),
                    }
                )
    columns = [
        "experiment_name",
        "guard_scenario",
        "capital_assumption_usd",
        "net_return_pct",
        "max_drawdown_pct",
        "trade_count",
        "skipped_trade_count",
        "skipped_trade_pct",
        "profit_factor",
        "expectancy_r",
        "avg_holding_bars",
        "qqq_return_pct",
        "equal_weight_return_pct",
        "excess_vs_qqq_pct",
        "excess_vs_equal_weight_pct",
        "degradation_vs_no_guard_pct",
        "liquidity_guard_pass",
    ]
    return pd.DataFrame(rows, columns=columns)


def _best_guard_for_capital(frame, capital, experiment_name):
    priority = [
        "skip_entry_bar_dollar_volume_below_100k",
        "skip_entry_bar_dollar_volume_below_500k",
        "skip_entry_bar_dollar_volume_below_1m",
        "skip_order_pct_of_entry_bar_dollar_volume_above_5pct",
        "skip_order_pct_of_entry_bar_dollar_volume_above_1pct",
    ]
    subset = frame[
        frame["capital_assumption_usd"].eq(capital)
        & frame["experiment_name"].eq(experiment_name)
    ]
    for scenario in priority:
        row = subset[subset["guard_scenario"].eq(scenario)]
        if not row.empty and bool(row["liquidity_guard_pass"].iloc[0]):
            return scenario
    return "no passing guard"


def _write_liquidity_guard_sensitivity_report(report, csv_path, markdown_path):
    report.to_csv(csv_path, index=False)
    base_capital = report[report["capital_assumption_usd"].eq(10000)].copy()
    focus_columns = [
        "experiment_name",
        "guard_scenario",
        "capital_assumption_usd",
        "net_return_pct",
        "trade_count",
        "skipped_trade_count",
        "skipped_trade_pct",
        "profit_factor",
        "excess_vs_qqq_pct",
        "degradation_vs_no_guard_pct",
        "liquidity_guard_pass",
    ]
    summary = base_capital[focus_columns].sort_values(
        ["experiment_name", "guard_scenario"]
    )
    def _scenario_row(name, scenario, capital=10000):
        subset = report[
            report["experiment_name"].eq(name)
            & report["guard_scenario"].eq(scenario)
            & report["capital_assumption_usd"].eq(capital)
        ]
        return subset.iloc[0] if not subset.empty else pd.Series(dtype=object)

    top30_500k = _scenario_row("rs_m126_top30_sma100_no_regime", "skip_entry_bar_dollar_volume_below_500k")
    top20_500k = _scenario_row("rs_m126_top20_sma100_no_regime", "skip_entry_bar_dollar_volume_below_500k")
    top10_500k = _scenario_row("rs_m126_top10_sma100_no_regime", "skip_entry_bar_dollar_volume_below_500k")
    donchian_500k = _scenario_row("donchian_20_10_top20", "skip_entry_bar_dollar_volume_below_500k")
    top30_main = bool(top30_500k.get("liquidity_guard_pass", False)) and pd.to_numeric(
        pd.Series([top30_500k.get("excess_vs_qqq_pct", pd.NA)]),
        errors="coerce",
    ).iloc[0] > 0
    top20_aggressive = bool(top20_500k.get("liquidity_guard_pass", False))
    top10_research = True
    donchian_low_impact = pd.to_numeric(
        pd.Series([donchian_500k.get("degradation_vs_no_guard_pct", pd.NA)]),
        errors="coerce",
    ).iloc[0]
    capital_rows = [
        {
            "capital_assumption_usd": capital,
            "recommended_minimum_guard": _best_guard_for_capital(
                report,
                capital,
                "rs_m126_top30_sma100_no_regime",
            ),
        }
        for capital in [10000, 25000, 50000, 100000]
    ]
    content = [
        "# Liquidity Guard Sensitivity Report",
        "",
        SURVIVORSHIP_BIAS_BANNER,
        "",
        "> [!IMPORTANT]",
        "> **Report Clarification & Scope Guidance**",
        "> - **Definition**: This report uses **post-hoc entry-bar dollar volume** (calculated as `entry_bar_Close * entry_bar_Volume`) to model hypothetical post-backtest trade execution exclusions.",
        "> - **Not Signal-Date ADV**: This is strictly entry-bar volume from actual trade execution time, *not* signal-date Average Daily Volume (ADV) metrics.",
        "> - **Not In-Simulation Eligibility**: These guard scenarios are post-hoc calculations evaluated from final trade logs. They do *not* represent in-simulation entry eligibility, which is handled dynamically by the strategy-level liquidity filters.",
        "",
        "## Decision Summary",
        "",
        f"- Top30 main after 500k guard: {top30_main}; skipped trades: {top30_500k.get('skipped_trade_count', pd.NA)}.",
        f"- Top20 aggressive alternative after 500k guard: {top20_aggressive}; skipped trades: {top20_500k.get('skipped_trade_count', pd.NA)}.",
        f"- Top10 remains research/aggressive only: {top10_research}; skipped trades at 500k: {top10_500k.get('skipped_trade_count', pd.NA)}.",
        f"- Donchian low impact at 500k guard: {bool(pd.notna(donchian_low_impact) and abs(float(donchian_low_impact)) <= 1.0)}; degradation pct: {donchian_500k.get('degradation_vs_no_guard_pct', pd.NA)}.",
        "",
        "## Recommended Minimum Guard By Capital",
        "",
        _table_for_markdown(pd.DataFrame(capital_rows)),
        "",
        "## Capital 10000 Scenario Detail",
        "",
        _table_for_markdown(summary),
        "",
        "Note: guard scenarios are report-level sensitivity calculations from executed trade logs; strategy trade logic is not changed.",
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _run_liquidity_guard_sensitivity_report(
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
):
    tasks = _build_experiment_tasks(
        _liquidity_guard_candidate_experiments(experiments),
        data_folder,
        run_profile,
        quick_mode,
        intraday_dir,
        daily_dir,
        raw_dir,
        intraday_timeframe,
        daily_timeframe,
        cache_rebuilt,
        include_raw=True,
    )
    results, batch_sec = _run_experiment_batch(
        tasks,
        asset_data,
        benchmark_data,
        cache_path,
        parallel_enabled=parallel_enabled,
        workers=worker_count,
    )
    return _liquidity_guard_sensitivity_rows(results, asset_data), batch_sec


def _run_cost_slippage_stress_report(
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
):
    tasks = _build_experiment_tasks(
        _cost_stress_candidate_experiments(experiments),
        data_folder,
        run_profile,
        quick_mode,
        intraday_dir,
        daily_dir,
        raw_dir,
        intraday_timeframe,
        daily_timeframe,
        cache_rebuilt,
        include_raw=True,
    )
    results, batch_sec = _run_experiment_batch(
        tasks,
        asset_data,
        benchmark_data,
        cache_path,
        parallel_enabled=parallel_enabled,
        workers=worker_count,
    )
    return _cost_stress_rows(results, asset_data), batch_sec


def _run_oos_walkforward_report(
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
):
    rows = []
    total_batch_sec = 0.0
    candidates = _oos_candidate_experiments(experiments)
    for period_name, start_date, end_date in _oos_walkforward_periods():
        period_tasks = _build_experiment_tasks(
            candidates,
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
        for task in period_tasks:
            task["period_name"] = period_name
            task["period_start"] = start_date
            task["period_end"] = end_date
        print(f"[stage] oos period {period_name} started")
        period_results, period_sec = _run_experiment_batch(
            period_tasks,
            asset_data,
            benchmark_data,
            cache_path,
            parallel_enabled=parallel_enabled,
            workers=worker_count,
        )
        total_batch_sec += period_sec
        for result in period_results:
            rows.append(_oos_report_row(result, period_name, start_date, end_date))
        print(f"[stage] oos period {period_name} done in {period_sec:.2f} sec")
    return pd.DataFrame(rows), total_batch_sec


def _write_rs_robustness_report(report, csv_path, markdown_path):
    columns = {
        "Experiment Name": "experiment_name",
        "rs_lookback_days": "rs_lookback_days",
        "rs_top_n": "rs_top_n",
        "rs_trend_sma_days": "rs_trend_sma_days",
        "rs_use_market_regime": "rs_use_market_regime",
        "Net Getiri (%)": "net_return_pct",
        "Max Drawdown (%)": "max_drawdown_pct",
        "Trade Count": "trade_count",
        "Profit Factor": "profit_factor",
        "Expectancy R": "expectancy_r",
        "Worst R": "worst_r",
        "QQQ BuyHold Return (%)": "qqq_return_pct",
        "EqualWeight Universe Return (%)": "equal_weight_return_pct",
        "Strategy Excess Return vs QQQ (%)": "excess_vs_qqq_pct",
        "Strategy Excess Return vs EqualWeight (%)": "excess_vs_equal_weight_pct",
        "Compact Score": "compact_score",
    }
    output = report[list(columns)].rename(columns=columns)
    output.to_csv(csv_path, index=False)
    best = output.sort_values("compact_score", ascending=False).head(10)
    worst = output.sort_values("compact_score", ascending=True).head(10)
    grouped = {
        "lookback": output.groupby("rs_lookback_days")["compact_score"].mean().round(4),
        "top_n": output.groupby("rs_top_n")["compact_score"].mean().round(4),
        "sma": output.groupby("rs_trend_sma_days")["compact_score"].mean().round(4),
        "regime": output.groupby("rs_use_market_regime")["compact_score"].mean().round(4),
    }
    try:
        best_table = best.to_markdown(index=False)
        worst_table = worst.to_markdown(index=False)
    except ImportError:
        best_table = best.to_string(index=False)
        worst_table = worst.to_string(index=False)
    content = [
        "# RS Robustness Report",
        "",
        "## En iyi 10 kombinasyon",
        "",
        best_table,
        "",
        "## En kotu 10 kombinasyon",
        "",
        worst_table,
        "",
        "## Kisa ozet",
        "",
        f"- Lookback compact score ortalamalari: {grouped['lookback'].to_dict()}",
        f"- TopN compact score ortalamalari: {grouped['top_n'].to_dict()}",
        f"- SMA compact score ortalamalari: {grouped['sma'].to_dict()}",
        f"- Regime compact score ortalamalari: {grouped['regime'].to_dict()}",
        "- QQQ ve EqualWeight farklari CSV'deki excess kolonlarinda yer alir.",
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _candidate_row(label, row, source):
    return {
        "candidate": label,
        "source": source,
        "net_return_pct": row.get("Net Getiri (%)", ""),
        "max_drawdown_pct": row.get("Max Drawdown (%)", ""),
        "trade_count": row.get("Trade Count", ""),
        "profit_factor": row.get("Profit Factor", ""),
        "expectancy_r": row.get("Expectancy R", ""),
        "compact_score": row.get("Compact Score", ""),
        "qqq_return_pct": row.get("QQQ BuyHold Return (%)", ""),
        "equal_weight_return_pct": row.get("EqualWeight Universe Return (%)", ""),
    }


def _write_candidate_summary_report(report, robustness, markdown_path):
    rows = []
    by_name = report.set_index("Experiment Name", drop=False)
    for name in ["rs_m63_sma100", "rs_m63_no_regime"]:
        if name in by_name.index:
            rows.append(_candidate_row(name, by_name.loc[name], "baseline"))
    donchian = report[report["Strategy Name"].eq("donchian_breakout")]
    if not donchian.empty:
        best = donchian.sort_values("Compact Score", ascending=False).iloc[0]
        rows.append(_candidate_row(best["Experiment Name"], best, "best_donchian"))
    if robustness is not None and not robustness.empty:
        robust_by_name = robustness.set_index("Experiment Name", drop=False)
        for name in [
            "rs_m126_top10_sma100_no_regime",
            "rs_m126_top20_sma100_no_regime",
            "rs_m126_top30_sma100_no_regime",
            "rs_m126_top30_sma200_no_regime",
        ]:
            if name in robust_by_name.index:
                rows.append(_candidate_row(name, robust_by_name.loc[name], "rs_m126"))
    first = report.iloc[0] if not report.empty else {}
    rows.append(
        {
            "candidate": "QQQ",
            "source": "benchmark",
            "net_return_pct": first.get("QQQ BuyHold Return (%)", ""),
            "max_drawdown_pct": first.get("QQQ BuyHold MaxDD (%)", ""),
            "trade_count": "",
            "profit_factor": "",
            "expectancy_r": "",
            "compact_score": "",
            "qqq_return_pct": first.get("QQQ BuyHold Return (%)", ""),
            "equal_weight_return_pct": "",
        }
    )
    rows.append(
        {
            "candidate": "EqualWeight",
            "source": "benchmark",
            "net_return_pct": first.get("EqualWeight Universe Return (%)", ""),
            "max_drawdown_pct": first.get("EqualWeight Universe BuyHold MaxDD (%)", ""),
            "trade_count": "",
            "profit_factor": "",
            "expectancy_r": "",
            "compact_score": "",
            "qqq_return_pct": "",
            "equal_weight_return_pct": first.get("EqualWeight Universe Return (%)", ""),
        }
    )
    summary = pd.DataFrame(rows)
    try:
        table = summary.to_markdown(index=False)
    except ImportError:
        table = summary.to_string(index=False)
    markdown_path.write_text(
        "\n".join(["# Candidate Summary Report", "", table, ""]),
        encoding="utf-8",
    )
