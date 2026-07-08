try:
    import argparse
    import sys
    from pathlib import Path

    from .runner import run_alpaca_paper_check, run_alpaca_paper_submit_one, run_alpaca_paper_submit_readiness, run_apply_paper_order_review_decisions, run_broker_cutover_readiness, run_broker_reconciliation, run_closed_trade_report, run_create_broker_cutover_baseline, run_execution_cost_preview, run_execution_preview_check, run_intraday_paper_monitor, run_no_lookahead_audit, run_paper_5min_fill_check, run_paper_decision_inbox, run_paper_ops_dashboard, run_paper_risk_dashboard, run_pending_5min_refresh, run_sqlite_state_reconciliation, run_strategy_experiments, run_universe_scanner
except ImportError:
    import argparse
    import sys
    from pathlib import Path

    from runner import run_alpaca_paper_check, run_alpaca_paper_submit_one, run_alpaca_paper_submit_readiness, run_apply_paper_order_review_decisions, run_broker_cutover_readiness, run_broker_reconciliation, run_closed_trade_report, run_create_broker_cutover_baseline, run_execution_cost_preview, run_execution_preview_check, run_intraday_paper_monitor, run_no_lookahead_audit, run_paper_5min_fill_check, run_paper_decision_inbox, run_paper_ops_dashboard, run_paper_risk_dashboard, run_pending_5min_refresh, run_sqlite_state_reconciliation, run_strategy_experiments, run_universe_scanner


VALIDATION_EXPERIMENTS = [
    {
        "name": "rs_m63_sma100",
        "strategy_name": "relative_strength_rotation",
        "excluded_symbols": ["QQQ", "SPY"],
        "rs_lookback_days": 63,
        "rs_top_n": 20,
        "rs_rebalance_frequency": "monthly",
        "rs_score_mode": "return_over_vol",
        "rs_vol_lookback_days": 60,
        "rs_trend_sma_days": 100,
        "rs_use_market_regime": True,
        "rs_market_regime_sma_days": 200,
        "rs_min_price": 10,
        "rs_max_atr_pct": 12,
    },
    {
        "name": "rs_m63_no_regime",
        "strategy_name": "relative_strength_rotation",
        "excluded_symbols": ["QQQ", "SPY"],
        "rs_lookback_days": 63,
        "rs_top_n": 20,
        "rs_rebalance_frequency": "monthly",
        "rs_score_mode": "return_over_vol",
        "rs_vol_lookback_days": 60,
        "rs_trend_sma_days": 200,
        "rs_use_market_regime": False,
        "rs_market_regime_sma_days": 200,
        "rs_min_price": 10,
        "rs_max_atr_pct": 12,
    },
    {
        "name": "dm_m63_top20",
        "strategy_name": "dual_momentum",
        "excluded_symbols": ["QQQ", "SPY"],
        "dm_lookback_days": 63,
        "dm_top_n": 20,
        "dm_rebalance_frequency": "monthly",
        "dm_absolute_momentum_threshold": 0.0,
        "dm_trend_sma_days": 200,
        "dm_use_market_regime": True,
        "dm_market_regime_sma_days": 200,
        "dm_min_price": 10,
        "dm_max_atr_pct": 12,
    },
    {
        "name": "donchian_20_10_top20",
        "strategy_name": "donchian_breakout",
        "excluded_symbols": ["QQQ", "SPY"],
        "donchian_entry_lookback_days": 20,
        "donchian_exit_lookback_days": 10,
        "donchian_top_n": 20,
        "donchian_rebalance_frequency": "daily",
        "donchian_trend_sma_days": 200,
        "donchian_use_market_regime": True,
        "donchian_market_regime_sma_days": 200,
        "donchian_min_price": 10,
        "donchian_max_atr_pct": 12,
    },
    {
        "name": "donchian_55_20_top20",
        "strategy_name": "donchian_breakout",
        "excluded_symbols": ["QQQ", "SPY"],
        "donchian_entry_lookback_days": 55,
        "donchian_exit_lookback_days": 20,
        "donchian_top_n": 20,
        "donchian_rebalance_frequency": "daily",
        "donchian_trend_sma_days": 200,
        "donchian_use_market_regime": True,
        "donchian_market_regime_sma_days": 200,
        "donchian_min_price": 10,
        "donchian_max_atr_pct": 12,
    },
    {
        "name": "donchian_20_10_top10",
        "strategy_name": "donchian_breakout",
        "excluded_symbols": ["QQQ", "SPY"],
        "donchian_entry_lookback_days": 20,
        "donchian_exit_lookback_days": 10,
        "donchian_top_n": 10,
        "donchian_rebalance_frequency": "daily",
        "donchian_trend_sma_days": 200,
        "donchian_use_market_regime": True,
        "donchian_market_regime_sma_days": 200,
        "donchian_min_price": 10,
        "donchian_max_atr_pct": 12,
    },
    {
        "name": "donchian_55_20_sma100",
        "strategy_name": "donchian_breakout",
        "excluded_symbols": ["QQQ", "SPY"],
        "donchian_entry_lookback_days": 55,
        "donchian_exit_lookback_days": 20,
        "donchian_top_n": 20,
        "donchian_rebalance_frequency": "daily",
        "donchian_trend_sma_days": 100,
        "donchian_use_market_regime": True,
        "donchian_market_regime_sma_days": 200,
        "donchian_min_price": 10,
        "donchian_max_atr_pct": 12,
    },
    {
        "name": "mast_sma100_slope20_top20",
        "strategy_name": "ma_slope_trend",
        "excluded_symbols": ["QQQ", "SPY"],
        "mast_slope_sma_days": 100,
        "mast_slope_lookback_days": 20,
        "mast_top_n": 20,
        "mast_rebalance_frequency": "monthly",
        "mast_score_lookback_days": 63,
        "mast_use_market_regime": True,
        "mast_market_regime_sma_days": 200,
        "mast_min_price": 10,
        "mast_max_atr_pct": 12,
    },
    {
        "name": "mast_sma200_slope20_top20",
        "strategy_name": "ma_slope_trend",
        "excluded_symbols": ["QQQ", "SPY"],
        "mast_slope_sma_days": 200,
        "mast_slope_lookback_days": 20,
        "mast_top_n": 20,
        "mast_rebalance_frequency": "monthly",
        "mast_score_lookback_days": 63,
        "mast_use_market_regime": True,
        "mast_market_regime_sma_days": 200,
        "mast_min_price": 10,
        "mast_max_atr_pct": 12,
    },
    {
        "name": "mast_sma100_slope20_top10",
        "strategy_name": "ma_slope_trend",
        "excluded_symbols": ["QQQ", "SPY"],
        "mast_slope_sma_days": 100,
        "mast_slope_lookback_days": 20,
        "mast_top_n": 10,
        "mast_rebalance_frequency": "monthly",
        "mast_score_lookback_days": 63,
        "mast_use_market_regime": True,
        "mast_market_regime_sma_days": 200,
        "mast_min_price": 10,
        "mast_max_atr_pct": 12,
    },
]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Universe scanner backtest, diagnostics, local paper reports, and safe Alpaca paper readiness checks."
    )
    parser.add_argument(
        "--reports",
        choices=["basic", "full"],
        default="basic",
        help="basic writes compact/audit/performance reports; full also writes yearly/candidate reports.",
    )
    parser.add_argument("--parallel", action="store_true", help="Run strategy experiments in parallel for faster report generation.")
    parser.add_argument("--no-parallel", action="store_true", help="Force sequential experiments.")
    parser.add_argument("--workers", default="auto", help="Worker count for --parallel; use 'auto' or an integer.")
    parser.add_argument(
        "--run-robustness",
        action="store_true",
        help="Run slow RS robustness research grid; not for normal daily use.",
    )
    parser.add_argument(
        "--run-oos",
        action="store_true",
        help="Run fixed-candidate out-of-sample/walk-forward diagnostics.",
    )
    parser.add_argument(
        "--run-attribution",
        action="store_true",
        help="Run fixed-candidate attribution and concentration report.",
    )
    parser.add_argument(
        "--run-cost-stress",
        action="store_true",
        help="Run fixed-candidate transaction cost, slippage, and liquidity stress report.",
    )
    parser.add_argument(
        "--run-liquidity-guard-sensitivity",
        action="store_true",
        help="Run report-level liquidity guard sensitivity; does not change trade logic.",
    )
    parser.add_argument(
        "--make-chatgpt-report",
        action="store_true",
        help="Write reports/latest/chatgpt_master_report.md for a single-file ChatGPT handoff.",
    )
    parser.add_argument(
        "--paper-report",
        action="store_true",
        help="Write local paper signal, pending-order, review, and ledger files; no broker orders.",
    )
    parser.add_argument(
        "--paper-strategy",
        choices=["main", "all"],
        default="main",
        help="Paper actionable strategy scope; default main only.",
    )
    parser.add_argument(
        "--paper-include-watchlist",
        action="store_true",
        help="Include comparison strategies as watch-only rows.",
    )
    parser.add_argument(
        "--paper-max-new-positions-per-day",
        type=int,
        default=5,
        help="Maximum actionable new BUY rows per day for ramp-in.",
    )
    parser.add_argument(
        "--alpaca-paper-check",
        action="store_true",
        help="GET-only Alpaca paper account/positions/orders check plus dry-run preview; never submits orders.",
    )
    parser.add_argument(
        "--alpaca-paper-submit-readiness",
        action="store_true",
        help="Write doc-gated Alpaca paper submit readiness report; GET-only and never submits orders.",
    )
    parser.add_argument(
        "--alpaca-paper-submit-one",
        action="store_true",
        help="Run one-order gated Alpaca paper submit path; blocks by default unless explicit enable and confirm token pass.",
    )
    parser.add_argument(
        "--alpaca-paper-submit-confirm-token",
        default="",
        help="Required token for --alpaca-paper-submit-one. Do not use unless intentionally submitting one paper order.",
    )
    parser.add_argument(
        "--broker-submit-enabled",
        action="store_true",
        help="Explicitly enable broker submit gate for --alpaca-paper-submit-one; default remains disabled.",
    )
    parser.add_argument(
        "--execution-preview",
        action="store_true",
        help="Build local execution preview artifacts from accepted pending orders; never submits orders.",
    )
    parser.add_argument(
        "--pre-submit-check",
        action="store_true",
        help="Add pre-submit readiness checklist to execution preview; never submits orders.",
    )
    parser.add_argument(
        "--paper-5min-fill-check",
        action="store_true",
        help="Read-only raw_5min diagnostic for pending paper orders; never mutates official paper state.",
    )
    parser.add_argument(
        "--refresh-pending-5min",
        action="store_true",
        help="Refresh raw_5min and rebuild 130min only for accepted pending paper symbols; never mutates paper state.",
    )
    parser.add_argument(
        "--paper-ops-dashboard",
        action="store_true",
        help="Write report-only local paper operations dashboard from existing latest reports; no data refresh or state mutation.",
    )
    parser.add_argument(
        "--intraday-paper-monitor",
        action="store_true",
        help="Write report-only intraday local paper monitor from existing raw_5min data; no state mutation.",
    )
    parser.add_argument(
        "--paper-decision-inbox",
        action="store_true",
        help="Write report-only local paper decision inbox from existing latest reports; no state mutation.",
    )
    parser.add_argument(
        "--apply-paper-order-review-decisions",
        action="store_true",
        help="Apply paper_order_review.csv decisions into paper_pending_orders.csv; no fills or broker orders.",
    )
    parser.add_argument(
        "--paper-risk-dashboard",
        action="store_true",
        help="Write report-only local paper portfolio risk dashboard from existing latest reports; no state mutation.",
    )
    parser.add_argument(
        "--closed-trade-report",
        action="store_true",
        help="Write report-only local paper closed trade and realized PnL report; no state mutation.",
    )
    parser.add_argument(
        "--no-lookahead-audit",
        action="store_true",
        help="Write report-only no-lookahead and fill timing audit; no state mutation.",
    )
    parser.add_argument(
        "--execution-cost-preview",
        action="store_true",
        help="Write report-only zero-cost execution cost preview from paper trade log; no state mutation.",
    )
    parser.add_argument(
        "--sqlite-state-reconciliation",
        action="store_true",
        help="Write report-only CSV-vs-SQLite state reconciliation; CSV remains source of truth.",
    )
    parser.add_argument(
        "--broker-reconciliation",
        action="store_true",
        help="Write report-only local-vs-broker reconciliation skeleton; no broker submit or POST.",
    )
    parser.add_argument(
        "--broker-cutover-readiness",
        action="store_true",
        help="Write forward-only Alpaca paper cutover readiness report; no broker submit or POST.",
    )
    parser.add_argument(
        "--create-broker-cutover-baseline",
        action="store_true",
        help="Explicitly create forward-only Alpaca paper cutover baseline JSON after GET-only snapshot PASS.",
    )
    parser.add_argument(
        "--alpaca-paper-snapshot-readonly",
        action="store_true",
        help="Use explicit GET-only Alpaca paper snapshot for broker reconciliation; disabled by default.",
    )
    args = parser.parse_args()
    parallel = bool(args.parallel and not args.no_parallel)
    data_folder = Path(__file__).resolve().parent / "hisseler"
    if args.alpaca_paper_check:
        run_alpaca_paper_check(
            str(data_folder),
            make_chatgpt_report=args.make_chatgpt_report,
        )
        raise SystemExit(0)
    if args.alpaca_paper_submit_readiness:
        run_alpaca_paper_submit_readiness(str(data_folder))
        raise SystemExit(0)
    if args.alpaca_paper_submit_one:
        run_alpaca_paper_submit_one(
            str(data_folder),
            confirm_token=args.alpaca_paper_submit_confirm_token,
            broker_submit_enabled=args.broker_submit_enabled,
        )
        raise SystemExit(0)
    if args.execution_preview or args.pre_submit_check:
        run_execution_preview_check(
            str(data_folder),
            make_chatgpt_report=args.make_chatgpt_report,
            pre_submit_check=args.pre_submit_check,
        )
        raise SystemExit(0)
    if args.refresh_pending_5min:
        run_pending_5min_refresh(
            str(data_folder),
            make_chatgpt_report=args.make_chatgpt_report,
        )
        if not args.paper_5min_fill_check:
            raise SystemExit(0)
    if args.paper_5min_fill_check:
        run_paper_5min_fill_check(
            str(data_folder),
            make_chatgpt_report=args.make_chatgpt_report,
        )
        raise SystemExit(0)
    if args.paper_ops_dashboard:
        run_paper_ops_dashboard(str(data_folder))
        raise SystemExit(0)
    if args.intraday_paper_monitor:
        run_intraday_paper_monitor(str(data_folder))
        raise SystemExit(0)
    if args.paper_decision_inbox:
        run_paper_decision_inbox(str(data_folder))
        raise SystemExit(0)
    if args.apply_paper_order_review_decisions:
        run_apply_paper_order_review_decisions(str(data_folder))
        raise SystemExit(0)
    if args.paper_risk_dashboard:
        run_paper_risk_dashboard(str(data_folder))
        raise SystemExit(0)
    if args.closed_trade_report:
        run_closed_trade_report(str(data_folder))
        raise SystemExit(0)
    if args.no_lookahead_audit:
        run_no_lookahead_audit(str(data_folder))
        raise SystemExit(0)
    if args.execution_cost_preview:
        run_execution_cost_preview(str(data_folder))
        raise SystemExit(0)
    if args.sqlite_state_reconciliation:
        run_sqlite_state_reconciliation(str(data_folder))
        raise SystemExit(0)
    if args.broker_reconciliation:
        run_broker_reconciliation(str(data_folder), alpaca_paper_snapshot_readonly=args.alpaca_paper_snapshot_readonly)
        raise SystemExit(0)
    if args.broker_cutover_readiness:
        run_broker_cutover_readiness(str(data_folder))
        raise SystemExit(0)
    if args.create_broker_cutover_baseline:
        run_create_broker_cutover_baseline(str(data_folder))
        raise SystemExit(0)
    run_strategy_experiments(
        str(data_folder),
        experiments=VALIDATION_EXPERIMENTS,
        run_profile="fast",
        quick_mode=True,
        compact_report=True,
        write_experiment_detail_files=False,
        reports=args.reports,
        parallel=parallel,
        workers=args.workers,
        run_robustness=args.run_robustness,
        run_oos=args.run_oos,
        run_attribution=args.run_attribution,
        run_cost_stress=args.run_cost_stress,
        run_liquidity_guard_sensitivity=args.run_liquidity_guard_sensitivity,
        make_chatgpt_report=args.make_chatgpt_report,
        paper_report=args.paper_report,
        paper_strategy=args.paper_strategy,
        paper_include_watchlist=args.paper_include_watchlist,
        paper_max_new_positions_per_day=args.paper_max_new_positions_per_day,
        command="python " + " ".join(sys.argv),
    )
