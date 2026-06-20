try:
    import argparse
    import sys
    from pathlib import Path

    from .runner import run_alpaca_paper_check, run_execution_preview_check, run_strategy_experiments, run_universe_scanner
except ImportError:
    import argparse
    import sys
    from pathlib import Path

    from runner import run_alpaca_paper_check, run_execution_preview_check, run_strategy_experiments, run_universe_scanner


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
        "--execution-preview",
        action="store_true",
        help="Build local execution preview artifacts from accepted pending orders; never submits orders.",
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
    if args.execution_preview:
        run_execution_preview_check(
            str(data_folder),
            make_chatgpt_report=args.make_chatgpt_report,
        )
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
