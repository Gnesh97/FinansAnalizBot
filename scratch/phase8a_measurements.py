"""Phase 8A Measurements 1 and 2 -- EVALUATION ONLY, not wired into production.

Runs the PRODUCTION path (_load_or_build_prepared_cache ->
_run_relative_strength_rotation_backtest) over the frozen baseline window and
reads the equity curve and trade log the production backtest already returns.
Nothing here reimplements strategy, ranking, sizing or accounting.

Measurement 1: realized annualized volatility, Sharpe (and its risk-free
assumption), drawdown distribution.
Measurement 2: positions held and capital deployed per rebalance, and what
happened during the largest drawdowns.

Read-only. Writes one JSON of results into scratch/.

    python3 scratch/phase8a_measurements.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "testdata"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import testdata.strategies.relative_strength_rotation as rsr
from testdata.runner import _default_params, _load_or_build_prepared_cache
from testdata.baseline_parity import _baseline_experiment
from testdata.strategies.relative_strength_rotation import (
    _normalize_relative_strength_params,
    _relative_strength_selection,
    _run_relative_strength_rotation_backtest,
)
from build_tiingo_strategy_baseline import selection_fingerprint

DATA_FOLDER = PROJECT_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data"
BASELINE_PATH = PROJECT_ROOT / "testdata" / "baselines" / "rs_v2_tiingo_strategy_baseline.json"
OUTPUT_PATH = Path(__file__).resolve().parent / "phase8a_measurements_output.json"

TRADING_DAYS = 252


def load_production_context():
    """Exactly the load the frozen baseline guard performs."""
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    daily_dir = DATA_FOLDER / "daily"
    intraday_dir = DATA_FOLDER / "bars_130min"
    symbols = sorted(p.name.removesuffix("_1D.csv") for p in daily_dir.glob("*_1D.csv"))
    prepared, _, _ = _load_or_build_prepared_cache(
        str(DATA_FOLDER), str(intraday_dir), str(daily_dir), "130min", "1D",
        baseline["window"]["target_start"], baseline["window"]["slice_upper_bound"], symbols,
    )
    params = _normalize_relative_strength_params(
        _default_params(_baseline_experiment(), str(DATA_FOLDER), "full", False)
    )
    return baseline, prepared, params, symbols


def capture_position_counts():
    """Record the production position_count_curve without changing behaviour.

    _run_relative_strength_rotation_backtest computes position_count_curve but
    only surfaces its mean. Wrap the metrics function to record the argument and
    delegate unchanged -- no strategy or accounting logic is altered.
    """
    captured = {}
    original = rsr._relative_strength_metrics_from_equity

    def recording(equity_curve, trade_log, **kwargs):
        captured["position_count_curve"] = list(kwargs.get("position_count_curve") or [])
        captured["equity_curve"] = list(equity_curve)
        return original(equity_curve, trade_log, **kwargs)

    return captured, original, recording


def drawdown_episodes(equity: pd.Series) -> pd.DataFrame:
    """Peak-to-trough episodes: each starts below a running peak, ends on recovery."""
    peak = equity.cummax()
    underwater = equity < peak
    episodes = []
    start = None
    for date, is_under in underwater.items():
        if is_under and start is None:
            start = date
        elif not is_under and start is not None:
            episodes.append((start, date))
            start = None
    if start is not None:
        episodes.append((start, equity.index[-1]))

    rows = []
    for start_date, end_date in episodes:
        window = equity.loc[start_date:end_date]
        peak_value = float(peak.loc[start_date])
        trough_date = window.idxmin()
        trough_value = float(window.min())
        recovered = bool(equity.loc[end_date] >= peak_value)
        rows.append({
            "peak_date": str(pd.Timestamp(start_date).date()),
            "trough_date": str(pd.Timestamp(trough_date).date()),
            "end_date": str(pd.Timestamp(end_date).date()),
            "depth_pct": round((trough_value / peak_value - 1) * 100, 2),
            "peak_to_trough_days": int((pd.Timestamp(trough_date) - pd.Timestamp(start_date)).days),
            "total_duration_days": int((pd.Timestamp(end_date) - pd.Timestamp(start_date)).days),
            "recovered": recovered,
        })
    frame = pd.DataFrame(rows)
    return frame.sort_values("depth_pct").reset_index(drop=True) if not frame.empty else frame


def reconstruct_exposure(trade_log, features, all_dates, production_counts):
    """Rebuild held-share timeline from the production trade log.

    A position is held at the close of date d when Entry Time <= d < Exit Time
    (entries fill at the open of Entry Time, exits at the open of Exit Time).
    Validated against the production position_count_curve before use.
    """
    trades = pd.DataFrame(trade_log)
    counts = pd.Series(0, index=all_dates, dtype=int)
    exposure = pd.Series(0.0, index=all_dates, dtype=float)

    for _, trade in trades.iterrows():
        symbol = trade["Symbol"]
        entry, exit_time = pd.Timestamp(trade["Entry Time"]), pd.Timestamp(trade["Exit Time"])
        shares = float(trade["Shares"])
        frame = features.get(symbol)
        if frame is None:
            continue
        mask = (all_dates >= entry) & (all_dates < exit_time)
        held_dates = all_dates[mask]
        if len(held_dates) == 0:
            continue
        counts.loc[held_dates] += 1
        closes = frame["Close"].reindex(held_dates).ffill()
        exposure.loc[held_dates] += shares * closes.values

    production = pd.Series(production_counts, index=all_dates[: len(production_counts)], dtype=int)
    mismatches = int((counts.loc[production.index] != production).sum())
    return counts, exposure, mismatches


def main() -> None:
    baseline, prepared, params, symbols = load_production_context()
    asset_data = prepared["asset_data"]
    benchmark = prepared.get("benchmark_data")

    captured, original, recording = capture_position_counts()
    rsr._relative_strength_metrics_from_equity = recording
    try:
        metrics = _run_relative_strength_rotation_backtest(asset_data, params, benchmark_data=benchmark)
    finally:
        rsr._relative_strength_metrics_from_equity = original

    features, selections, regime, rebalance = _relative_strength_selection(
        asset_data, params, benchmark_data=benchmark
    )
    digest, _ = selection_fingerprint(selections)

    # --- Step 0: baseline reproduction ---
    expected = baseline["metrics"]
    reproduction = {
        "net_return_pct": metrics.get("Net Getiri (%)"),
        "trade_count": metrics.get("Islem Sayisi"),
        "cagr_pct": metrics.get("CAGR (%)"),
        "max_drawdown_pct": metrics.get("Max Drawdown (%)"),
        "sharpe_ratio": metrics.get("Sharpe Ratio"),
        "win_rate_pct": metrics.get("Win Rate (%)"),
        "fingerprint_sha256": digest,
        "expected_net_return_pct": expected["net_return_pct"],
        "expected_trade_count": expected["trade_count"],
        "expected_fingerprint": baseline["selection"]["fingerprint_sha256"],
        "symbol_count": len(symbols),
    }
    reproduction["matches_baseline"] = bool(
        abs(reproduction["net_return_pct"] - expected["net_return_pct"]) < 0.005
        and reproduction["trade_count"] == expected["trade_count"]
        and digest == baseline["selection"]["fingerprint_sha256"]
    )

    # --- Measurement 1 ---
    equity_curve = metrics["_equity_curve"]
    equity = pd.Series(
        [value for _, value in equity_curve],
        index=pd.DatetimeIndex([timestamp for timestamp, _ in equity_curve]),
        dtype=float,
    )
    # Identical construction to _relative_strength_metrics_from_equity.
    daily_returns = equity.resample("1D").last().pct_change(fill_method=None).dropna()
    daily_vol = float(daily_returns.std())
    annualized_vol_pct = daily_vol * np.sqrt(TRADING_DAYS) * 100
    recomputed_sharpe = float(daily_returns.mean() / daily_vol * np.sqrt(TRADING_DAYS))

    # The resample above expands to CALENDAR days, so a weekend or holiday
    # becomes NaN and pct_change then yields NaN for the following session --
    # which dropna() discards. Every post-gap session (most Mondays) is
    # therefore excluded from the system's own vol and Sharpe. Measure the
    # trading-day series as well; both are reported, neither is "corrected".
    trading_returns = equity.pct_change(fill_method=None).dropna()
    trading_vol = float(trading_returns.std())
    trading_vol_annualized_pct = trading_vol * np.sqrt(TRADING_DAYS) * 100
    trading_sharpe = float(trading_returns.mean() / trading_vol * np.sqrt(TRADING_DAYS))
    annualized_arithmetic_mean_pct = float(trading_returns.mean()) * TRADING_DAYS * 100

    episodes = drawdown_episodes(equity)
    top5 = episodes.head(5).to_dict("records") if not episodes.empty else []

    measurement_1 = {
        "annualized_volatility_pct": round(annualized_vol_pct, 2),
        "daily_volatility_pct": round(daily_vol * 100, 4),
        "sharpe_reported_by_system": metrics.get("Sharpe Ratio"),
        "sharpe_recomputed_unrounded": round(recomputed_sharpe, 4),
        "sharpe_risk_free_assumption": "none -- raw mean daily return / daily std * sqrt(252)",
        "max_drawdown_pct": metrics.get("Max Drawdown (%)"),
        "drawdown_episode_count": int(len(episodes)),
        "top_5_drawdowns": top5,
        "observation_days_system_method": int(len(daily_returns)),
        "trading_day_annualized_volatility_pct": round(trading_vol_annualized_pct, 2),
        "trading_day_sharpe": round(trading_sharpe, 4),
        "trading_day_observations": int(len(trading_returns)),
        "observations_dropped_by_calendar_resample": int(len(trading_returns) - len(daily_returns)),
        "annualized_arithmetic_mean_return_pct": round(annualized_arithmetic_mean_pct, 2),
        "cagr_pct": metrics.get("CAGR (%)"),
    }

    # --- Measurement 2 ---
    all_dates = pd.DatetimeIndex(sorted(set().union(*(f.index for f in features.values()))))
    counts, exposure, mismatches = reconstruct_exposure(
        metrics["_trade_log"], features, all_dates, captured["position_count_curve"]
    )
    equity_aligned = equity.reindex(all_dates).ffill()
    deployed_pct = (exposure / equity_aligned * 100).replace([np.inf, -np.inf], np.nan)

    rebalance_dates = [d for d in all_dates if bool(rebalance.loc[d])]
    rebalance_rows = []
    for date in rebalance_dates:
        selected = selections.get(date, set())
        rebalance_rows.append({
            "date": str(pd.Timestamp(date).date()),
            "selected_count": len(selected),
            "positions_held": int(counts.loc[date]),
            "deployed_pct": round(float(deployed_pct.loc[date]), 2) if pd.notna(deployed_pct.loc[date]) else None,
            "eligible_universe_count": int(sum(
                1 for s, f in features.items()
                if date in f.index and bool(f.loc[date, "RS_Eligible"]) and pd.notna(f.loc[date, "RS_Score"])
            )),
        })

    drawdown_windows = []
    for episode in top5:
        # all_dates is America/New_York-aware; episode bounds are bare date strings.
        peak_ts = pd.Timestamp(episode["peak_date"], tz=all_dates.tz)
        end_ts = pd.Timestamp(episode["end_date"], tz=all_dates.tz) + pd.Timedelta(days=1)
        window_mask = (all_dates >= peak_ts) & (all_dates < end_ts)
        window_dates = all_dates[window_mask]
        if len(window_dates) == 0:
            continue
        drawdown_windows.append({
            "episode": f'{episode["peak_date"]} -> {episode["trough_date"]}',
            "depth_pct": episode["depth_pct"],
            "min_positions_held": int(counts.loc[window_dates].min()),
            "mean_positions_held": round(float(counts.loc[window_dates].mean()), 2),
            "min_deployed_pct": round(float(deployed_pct.loc[window_dates].min()), 2),
            "mean_deployed_pct": round(float(deployed_pct.loc[window_dates].mean()), 2),
        })

    # The window opens with a warm-up: SMA100 and the 126-day lookback have no
    # history yet, so nothing is eligible and the book is empty. The final date
    # is the forced_final_exit close-out. Neither is a regime response, so the
    # "live" deployment statistics exclude both.
    invested = counts[counts > 0]
    live_start = invested.index[0] if len(invested) else all_dates[0]
    live_dates = all_dates[(all_dates >= live_start) & (all_dates < all_dates[-1])]
    live_deployed = deployed_pct.loc[live_dates].dropna()
    live_counts = counts.loc[live_dates]
    mismatch_dates = [
        str(pd.Timestamp(d).date())
        for d in all_dates[: len(captured["position_count_curve"])]
        if int(counts.loc[d]) != int(pd.Series(captured["position_count_curve"],
                                               index=all_dates[: len(captured["position_count_curve"])]).loc[d])
    ]

    measurement_2 = {
        "reconstruction_mismatches_vs_production": mismatches,
        "reconstruction_mismatch_dates": mismatch_dates,
        "warmup_end_date": str(pd.Timestamp(live_start).date()),
        "live_period": f"{pd.Timestamp(live_start).date()} .. {pd.Timestamp(live_dates[-1]).date()}",
        "live_deployed_pct_min": round(float(live_deployed.min()), 2),
        "live_deployed_pct_p05": round(float(live_deployed.quantile(0.05)), 2),
        "live_deployed_pct_median": round(float(live_deployed.median()), 2),
        "live_deployed_pct_mean": round(float(live_deployed.mean()), 2),
        "live_days_total": int(len(live_deployed)),
        "live_days_below_95pct_deployed": int((live_deployed < 95).sum()),
        "live_days_below_50pct_deployed": int((live_deployed < 50).sum()),
        "live_days_fully_in_cash": int((live_deployed == 0).sum()),
        "live_positions_min": int(live_counts.min()),
        "live_positions_median": float(live_counts.median()),
        "production_avg_positions_held": metrics.get("Avg Positions Held"),
        "production_max_positions_held": metrics.get("Max Positions Held"),
        "production_max_portfolio_heat_pct": metrics.get("Max Portfolio Heat (%)"),
        "reconstructed_mean_positions": round(float(counts.mean()), 2),
        "uses_market_regime_filter": bool(params["rs_use_market_regime"]),
        "rs_top_n": int(params["rs_top_n"]),
        "rs_trend_sma_days": int(params["rs_trend_sma_days"]),
        "rebalance_count": len(rebalance_rows),
        "rebalances_below_full_allocation": sum(1 for r in rebalance_rows if r["selected_count"] < params["rs_top_n"]),
        "min_selected_count": min((r["selected_count"] for r in rebalance_rows), default=None),
        "deployed_pct_min": round(float(deployed_pct.min()), 2),
        "deployed_pct_median": round(float(deployed_pct.median()), 2),
        "deployed_pct_max": round(float(deployed_pct.max()), 2),
        "rebalance_series": rebalance_rows,
        "during_largest_drawdowns": drawdown_windows,
    }

    results = {
        "baseline_reproduction": reproduction,
        "measurement_1_risk": measurement_1,
        "measurement_2_exposure": measurement_2,
    }
    OUTPUT_PATH.write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")

    print("=== BASELINE REPRODUCTION ===")
    print(json.dumps(reproduction, indent=2, default=str))
    print("\n=== MEASUREMENT 1: REALIZED RISK ===")
    print(json.dumps({k: v for k, v in measurement_1.items() if k != "top_5_drawdowns"}, indent=2, default=str))
    print("\ntop 5 drawdowns:")
    for row in top5:
        print(f"  {row['depth_pct']:>7.2f}%  peak {row['peak_date']} -> trough {row['trough_date']} "
              f"-> end {row['end_date']}  ptt={row['peak_to_trough_days']}d total={row['total_duration_days']}d "
              f"recovered={row['recovered']}")
    print("\n=== MEASUREMENT 2: EXPOSURE ===")
    print(json.dumps({k: v for k, v in measurement_2.items()
                      if k not in ("rebalance_series", "during_largest_drawdowns")}, indent=2, default=str))
    print("\nduring largest drawdowns:")
    for row in drawdown_windows:
        print(f"  {row['episode']} ({row['depth_pct']}%): positions min={row['min_positions_held']} "
              f"mean={row['mean_positions_held']}, deployed min={row['min_deployed_pct']}% "
              f"mean={row['mean_deployed_pct']}%")
    print(f"\nwrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
