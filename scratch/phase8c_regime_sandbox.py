"""Phase 8C -- regime-filter measurement SANDBOX. EVALUATION ONLY.

NOT wired into production. Changes no default. Imported by nothing.

Only the regime overlay differs from production. Ranking, the SMA100 stock
eligibility filter, the monthly rebalance calendar, next-day-open execution,
transaction costs, the liquidity filter and the universe all come from the
unmodified production modules.

Variants:
  A control          -- no regime filter (must reproduce the frozen baseline)
  B clenow_sma200    -- SPY < SMA200: block NEW entries only; held names keep
                        their normal exit rules (rotation / SMA100 failure)
  C antonacci_12m    -- SPY trailing 252-session return <= 0: go fully to cash
  D index_sma100     -- SPY < SMA100: go fully to cash

Production's own regime switch (rs_use_market_regime) cannot express variant B:
_relative_strength_selection sets selections[date] = set() when risk-off, which
force-liquidates. B is implemented here instead.

    python3 scratch/phase8c_regime_sandbox.py
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

from testdata.runner import _default_params, _load_or_build_prepared_cache, _normalize_frame
from testdata.baseline_parity import _baseline_experiment
from testdata.strategies.cost_model import calculate_max_shares, calculate_transaction_costs
from testdata.strategies.liquidity_filter import evaluate_entry_liquidity, liquidity_skip_summary
from testdata.strategies.relative_strength_rotation import (
    _normalize_relative_strength_params,
    _relative_strength_metrics_from_equity,
    _relative_strength_selection,
)
from build_tiingo_strategy_baseline import selection_fingerprint

DATA_FOLDER = PROJECT_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data"
BASELINE_PATH = PROJECT_ROOT / "testdata" / "baselines" / "rs_v2_tiingo_strategy_baseline.json"
SPY_PATH = DATA_FOLDER / "benchmarks" / "daily" / "SPY_1D.csv"
OUTPUT_PATH = Path(__file__).resolve().parent / "phase8c_regime_output.json"

TRADING_DAYS = 252
INITIAL_CASH = 10000.0

BEAR_2022 = ("2022-04-21", "2022-10-14")
OPEN_2026 = ("2026-06-23", "2026-07-21")


def build_regime_signals(index_close: pd.Series) -> pd.DataFrame:
    """Risk-on booleans per variant, plus the raw indicators.

    A NaN indicator (insufficient history at the window start) is treated as
    RISK-ON: refusing to invest merely because an indicator has not warmed up
    would be a data artifact, not a market signal. The first date each signal
    becomes available is reported alongside the results.
    """
    sma200 = index_close.rolling(200, min_periods=200).mean()
    sma100 = index_close.rolling(100, min_periods=100).mean()
    trailing_12m = index_close / index_close.shift(TRADING_DAYS) - 1
    return pd.DataFrame({
        "close": index_close,
        "sma200": sma200,
        "sma100": sma100,
        "trailing_12m": trailing_12m,
        "risk_on_sma200": (index_close > sma200).where(sma200.notna(), True),
        "risk_on_sma100": (index_close > sma100).where(sma100.notna(), True),
        "risk_on_12m": (trailing_12m > 0).where(trailing_12m.notna(), True),
    })


VARIANT_SIGNAL = {
    "A_control": None,
    "B_clenow_sma200": "risk_on_sma200",
    "C_antonacci_12m": "risk_on_12m",
    "D_index_sma100": "risk_on_sma100",
}
# B blocks new entries only; C and D liquidate to cash.
VARIANT_ACTION = {
    "A_control": None,
    "B_clenow_sma200": "block_entries",
    "C_antonacci_12m": "to_cash",
    "D_index_sma100": "to_cash",
}


def run_backtest(asset_data, params, benchmark_data, regime, variant):
    """Production backtest loop with a regime overlay at target-selection time."""
    rs_params = _normalize_relative_strength_params(params)
    features, selections, production_regime, _ = _relative_strength_selection(
        asset_data, rs_params, benchmark_data=benchmark_data,
    )
    all_dates = pd.DatetimeIndex(sorted(set().union(*(f.index for f in features.values()))))
    signal_column = VARIANT_SIGNAL[variant]
    action = VARIANT_ACTION[variant]

    cash = INITIAL_CASH
    positions, trade_log, equity_curve = {}, [], []
    position_count_curve, deployed_curve = [], []
    pending_target = None
    pending_target_signal_date = None
    liquidity_skips = []
    max_open_observed, max_heat_observed = 0, 0.0
    regime_events = []

    def _equity_at(date, price_column):
        total = cash
        for symbol, position in positions.items():
            frame = features.get(symbol)
            if frame is None or date not in frame.index:
                total += position["shares"] * position.get("last_price", position["entry_price"])
                continue
            price = frame.loc[date, price_column]
            if pd.notna(price) and price > 0:
                position["last_price"] = float(price)
                total += position["shares"] * float(price)
        return total

    def _close(symbol, date, price, reason):
        nonlocal cash
        position = positions.pop(symbol)
        gross_proceeds = position["shares"] * price
        costs = calculate_transaction_costs(gross_proceeds, position["shares"], rs_params)
        exit_cost = costs["total_cost"]
        proceeds = gross_proceeds - exit_cost
        cash += proceeds
        pnl = proceeds - (position["entry_notional"] + position["entry_cost"])
        r_multiple = pnl / position["entry_notional"] if position["entry_notional"] > 0 else np.nan
        trade_log.append({
            "Symbol": symbol, "Entry Time": position["entry_time"], "Exit Time": date,
            "Exit Reason": reason, "Net PnL": round(float(pnl), 2),
            "R Multiple": round(float(r_multiple), 4) if pd.notna(r_multiple) else np.nan,
            "Bars Held": int(position["bars_held"]), "Entry Price": position["entry_price"],
            "Exit Price": price, "Shares": position["shares"],
            "Entry Notional": position["entry_notional"],
        })

    for date in all_dates:
        if pending_target is not None:
            target_symbols = set(pending_target)
            open_equity = _equity_at(date, "Open")
            for symbol in list(positions):
                if symbol not in target_symbols:
                    frame = features.get(symbol)
                    if frame is not None and date in frame.index:
                        price = frame.loc[date, "Open"]
                        if pd.notna(price) and price > 0:
                            _close(symbol, date, float(price), "rs_rotation_exit")
            target_weight = 1 / max(rs_params["rs_top_n"], 1)
            target_notional = open_equity * target_weight
            for symbol in sorted(target_symbols):
                if symbol in positions:
                    continue
                frame = features.get(symbol)
                if frame is None or date not in frame.index:
                    continue
                signal_date = pending_target_signal_date
                signal_row = (frame.loc[signal_date]
                              if signal_date is not None and signal_date in frame.index else None)
                liquidity_result = evaluate_entry_liquidity(
                    symbol, signal_date, signal_row, rs_params["min_dollar_volume"])
                if not liquidity_result["passed"]:
                    liquidity_skips.append(liquidity_result)
                    continue
                price = frame.loc[date, "Open"]
                if pd.isna(price) or price <= 0:
                    continue
                shares = calculate_max_shares(target_notional, price, cash, rs_params)
                if shares <= 0:
                    continue
                gross_entry_notional = shares * price
                entry_costs = calculate_transaction_costs(gross_entry_notional, shares, rs_params)
                cash -= (gross_entry_notional + entry_costs["total_cost"])
                positions[symbol] = {
                    "entry_time": date, "entry_price": float(price),
                    "entry_notional": gross_entry_notional, "shares": shares, "bars_held": 0,
                    "last_price": float(price),
                    "entry_commission": entry_costs["commission_cost"],
                    "entry_slippage": entry_costs["slippage_cost"],
                    "entry_cost": entry_costs["total_cost"],
                }
            pending_target = None
            pending_target_signal_date = None

        close_equity = _equity_at(date, "Close")
        for position in positions.values():
            position["bars_held"] += 1
        max_open_observed = max(max_open_observed, len(positions))
        exposure = sum(p["shares"] * p.get("last_price", p["entry_price"]) for p in positions.values())
        max_heat_observed = max(max_heat_observed, exposure / close_equity if close_equity > 0 else 0.0)
        position_count_curve.append(len(positions))
        deployed_curve.append(exposure / close_equity if close_equity > 0 else 0.0)
        equity_curve.append((date, close_equity))

        if date in selections:
            base_target = selections[date] if bool(production_regime.loc[date]) else set()
            if signal_column is None:
                pending_target = base_target
            else:
                risk_on = bool(regime.loc[date, signal_column]) if date in regime.index else True
                if risk_on:
                    pending_target = base_target
                elif action == "block_entries":
                    # Clenow: no NEW buys while the index is below its long-term
                    # average; names already held keep their normal exit rules.
                    held = set(positions)
                    pending_target = base_target & held
                    blocked = sorted(base_target - held)
                    if blocked:
                        regime_events.append({"date": str(date.date()), "action": "blocked_entries",
                                              "blocked_count": len(blocked), "held_count": len(held)})
                else:
                    pending_target = set()
                    if positions:
                        regime_events.append({"date": str(date.date()), "action": "to_cash",
                                              "liquidated_count": len(positions)})
            pending_target_signal_date = date

    final_date = all_dates[-1]
    for symbol in list(positions):
        frame = features.get(symbol)
        if frame is None or final_date not in frame.index:
            continue
        price = frame.loc[final_date, "Close"]
        if pd.notna(price) and price > 0:
            _close(symbol, final_date, float(price), "forced_final_exit")
    if equity_curve:
        equity_curve[-1] = (final_date, cash)

    metrics = _relative_strength_metrics_from_equity(
        equity_curve, trade_log, initial_cash=INITIAL_CASH,
        max_open_observed=max_open_observed, max_heat_observed=max_heat_observed,
        position_count_curve=position_count_curve,
    )
    metrics.update(liquidity_skip_summary(liquidity_skips, rs_params["min_dollar_volume"]))
    metrics["_equity_curve"] = equity_curve
    metrics["_deployed_curve"] = deployed_curve
    metrics["_position_count_curve"] = position_count_curve
    metrics["_regime_events"] = regime_events
    metrics["_all_dates"] = all_dates
    metrics["_selections"] = selections
    return metrics


def drawdown_episodes(equity: pd.Series) -> pd.DataFrame:
    peak = equity.cummax()
    underwater = equity < peak
    episodes, start = [], None
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
        rows.append({
            "peak_date": str(pd.Timestamp(start_date).date()),
            "trough_date": str(pd.Timestamp(trough_date).date()),
            "end_date": str(pd.Timestamp(end_date).date()),
            "depth_pct": round((float(window.min()) / peak_value - 1) * 100, 2),
            "peak_to_trough_days": int((pd.Timestamp(trough_date) - pd.Timestamp(start_date)).days),
            "total_duration_days": int((pd.Timestamp(end_date) - pd.Timestamp(start_date)).days),
            "recovered": bool(equity.loc[end_date] >= peak_value),
        })
    frame = pd.DataFrame(rows)
    return frame.sort_values("depth_pct").reset_index(drop=True) if not frame.empty else frame


def window_stats(dates, deployed, counts, start, end):
    tz = dates.tz
    lo = pd.Timestamp(start, tz=tz)
    hi = pd.Timestamp(end, tz=tz) + pd.Timedelta(days=1)
    mask = (dates >= lo) & (dates < hi)
    if not mask.any():
        return None
    return {
        "mean_deployed_pct": round(float(deployed[mask].mean()) * 100, 2),
        "min_deployed_pct": round(float(deployed[mask].min()) * 100, 2),
        "days_fully_cash": int((deployed[mask] == 0).sum()),
        "mean_positions": round(float(counts[mask].mean()), 2),
    }


def summarize(label, metrics, live_from):
    equity = pd.Series([v for _, v in metrics["_equity_curve"]],
                       index=pd.DatetimeIndex([t for t, _ in metrics["_equity_curve"]]), dtype=float)
    trading_returns = equity.pct_change(fill_method=None).dropna()
    trading_vol = float(trading_returns.std()) * np.sqrt(TRADING_DAYS) * 100
    trading_sharpe = float(trading_returns.mean() / trading_returns.std() * np.sqrt(TRADING_DAYS))
    resampled = equity.resample("1D").last().pct_change(fill_method=None).dropna()
    system_vol = float(resampled.std()) * np.sqrt(TRADING_DAYS) * 100

    dates = metrics["_all_dates"]
    deployed = pd.Series(metrics["_deployed_curve"], index=dates, dtype=float)
    counts = pd.Series(metrics["_position_count_curve"], index=dates, dtype=float)
    live_mask = dates >= pd.Timestamp(live_from, tz=dates.tz)
    live_deployed = deployed[live_mask]
    episodes = drawdown_episodes(equity)

    return {
        "variant": label,
        "net_return_pct": metrics.get("Net Getiri (%)"),
        "cagr_pct": metrics.get("CAGR (%)"),
        "trade_count": metrics.get("Islem Sayisi"),
        "annualized_vol_trading_day_pct": round(trading_vol, 2),
        "annualized_vol_system_resample_pct": round(system_vol, 2),
        "sharpe_trading_day": round(trading_sharpe, 4),
        "sharpe_system_reported": metrics.get("Sharpe Ratio"),
        "max_drawdown_pct": metrics.get("Max Drawdown (%)"),
        "top_5_drawdowns": episodes.head(5).to_dict("records") if not episodes.empty else [],
        "drawdown_episode_count": int(len(episodes)),
        "mean_deployed_pct_live": round(float(live_deployed.mean()) * 100, 2),
        "median_deployed_pct_live": round(float(live_deployed.median()) * 100, 2),
        "pct_of_live_days_fully_in_cash": round(float((live_deployed == 0).mean()) * 100, 2),
        "live_days": int(live_mask.sum()),
        "bear_2022": window_stats(dates, deployed, counts, *BEAR_2022),
        "open_2026": window_stats(dates, deployed, counts, *OPEN_2026),
        "regime_event_count": len(metrics["_regime_events"]),
        "regime_events": metrics["_regime_events"],
    }


def filter_wrongness(regime_frame, selections, index_close, signal_column, action):
    """How often the filter fired and the index then ROSE (the cost of insurance).

    Forward return is measured from the rebalance date to the next rebalance,
    which is the horizon over which the block or liquidation actually applied.
    """
    if signal_column is None:
        return None
    rebalance_dates = sorted(selections)
    rows = []
    for current, following in zip(rebalance_dates, rebalance_dates[1:]):
        if current not in regime_frame.index:
            continue
        if bool(regime_frame.loc[current, signal_column]):
            continue
        try:
            start_price = float(index_close.loc[current])
            end_price = float(index_close.loc[following])
        except KeyError:
            continue
        rows.append({"date": str(current.date()),
                     "forward_return_pct": round((end_price / start_price - 1) * 100, 2)})
    if not rows:
        return {"triggered_rebalances": 0, "wrong_count": 0, "wrong_pct": None,
                "mean_forward_return_pct": None, "detail": []}
    forward = pd.Series([r["forward_return_pct"] for r in rows])
    wrong = int((forward > 0).sum())
    return {
        "triggered_rebalances": len(rows),
        "wrong_count": wrong,
        "wrong_pct": round(wrong / len(rows) * 100, 1),
        "mean_forward_return_pct": round(float(forward.mean()), 2),
        "median_forward_return_pct": round(float(forward.median()), 2),
        "action": action,
        "detail": rows,
    }


def main() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    daily_dir, intraday_dir = DATA_FOLDER / "daily", DATA_FOLDER / "bars_130min"
    symbols = sorted(p.name.removesuffix("_1D.csv") for p in daily_dir.glob("*_1D.csv"))
    prepared, _, _ = _load_or_build_prepared_cache(
        str(DATA_FOLDER), str(intraday_dir), str(daily_dir), "130min", "1D",
        baseline["window"]["target_start"], baseline["window"]["slice_upper_bound"], symbols,
    )
    asset_data = prepared["asset_data"]
    benchmark = prepared.get("benchmark_data")
    params = _default_params(_baseline_experiment(), str(DATA_FOLDER), "full", False)

    benchmark_symbol = benchmark.attrs.get("benchmark_symbol") if benchmark is not None else None
    spy = _normalize_frame(SPY_PATH)
    spy_close = pd.to_numeric(spy["Close"], errors="coerce").dropna()
    regime = build_regime_signals(spy_close)
    signal_availability = {
        "sma200_first_valid": str(regime["sma200"].first_valid_index().date()),
        "sma100_first_valid": str(regime["sma100"].first_valid_index().date()),
        "trailing_12m_first_valid": str(regime["trailing_12m"].first_valid_index().date()),
        "spy_first_session": str(spy_close.index.min().date()),
        "spy_last_session": str(spy_close.index.max().date()),
        "spy_sessions": int(len(spy_close)),
        "prepared_benchmark_symbol_used_by_ranking": benchmark_symbol,
    }

    # --- Fidelity gate ---
    control = run_backtest(asset_data, params, benchmark, regime, "A_control")
    digest, _ = selection_fingerprint(control["_selections"])
    expected = baseline["metrics"]
    gate = {
        "net_return_pct": control.get("Net Getiri (%)"),
        "trade_count": control.get("Islem Sayisi"),
        "max_drawdown_pct": control.get("Max Drawdown (%)"),
        "fingerprint_sha256": digest,
        "expected": expected,
        "expected_fingerprint": baseline["selection"]["fingerprint_sha256"],
    }
    gate["passed"] = bool(
        abs(gate["net_return_pct"] - expected["net_return_pct"]) < 0.005
        and gate["trade_count"] == expected["trade_count"]
        and abs(gate["max_drawdown_pct"] - expected["max_drawdown_pct"]) < 0.005
        and digest == baseline["selection"]["fingerprint_sha256"]
    )
    print("=== FIDELITY GATE ===")
    print(f"  sandbox : {gate['net_return_pct']}% / {gate['trade_count']} trades / "
          f"maxDD {gate['max_drawdown_pct']}% / fp {digest[:16]}")
    print(f"  baseline: {expected['net_return_pct']}% / {expected['trade_count']} trades / "
          f"maxDD {expected['max_drawdown_pct']}% / fp {baseline['selection']['fingerprint_sha256'][:16]}")
    print(f"  passed  : {gate['passed']}")
    if not gate["passed"]:
        OUTPUT_PATH.write_text(json.dumps({"fidelity_gate": gate}, indent=2, default=str) + "\n",
                               encoding="utf-8")
        raise SystemExit("ABORT: sandbox does not reproduce the governing baseline.")

    # First date the strategy actually holds anything (warm-up excluded).
    counts = pd.Series(control["_position_count_curve"], index=control["_all_dates"])
    live_from = str(counts[counts > 0].index[0].date())

    results = [summarize("A_control", control, live_from)]
    wrongness = {}
    for variant in ("B_clenow_sma200", "C_antonacci_12m", "D_index_sma100"):
        metrics = run_backtest(asset_data, params, benchmark, regime, variant)
        results.append(summarize(variant, metrics, live_from))
        wrongness[variant] = filter_wrongness(
            regime, metrics["_selections"], spy_close,
            VARIANT_SIGNAL[variant], VARIANT_ACTION[variant])

    control_row = results[0]
    for row in results:
        row["delta_return_pp"] = round(row["net_return_pct"] - control_row["net_return_pct"], 2)
        row["delta_vol_pp"] = round(row["annualized_vol_trading_day_pct"]
                                    - control_row["annualized_vol_trading_day_pct"], 2)
        row["delta_maxdd_pp"] = round(row["max_drawdown_pct"] - control_row["max_drawdown_pct"], 2)
        row["delta_sharpe"] = round(row["sharpe_trading_day"] - control_row["sharpe_trading_day"], 4)

    OUTPUT_PATH.write_text(json.dumps({
        "fidelity_gate": gate, "signal_availability": signal_availability,
        "live_from": live_from, "variants": results, "wrongness": wrongness,
    }, indent=2, default=str) + "\n", encoding="utf-8")

    print("\n=== SIGNAL AVAILABILITY ===")
    print(json.dumps(signal_availability, indent=2))
    print(f"\nlive from (first date with positions): {live_from}")
    header = (f"{'variant':<20}{'return%':>9}{'CAGR%':>8}{'trades':>8}{'vol%':>7}{'maxDD%':>9}"
              f"{'Sharpe':>8}{'deploy%':>9}{'cash%':>7}{'events':>8}")
    print("\n=== VARIANTS ===")
    print(header)
    print("-" * len(header))
    for row in results:
        print(f"{row['variant']:<20}{row['net_return_pct']:>9.2f}{row['cagr_pct']:>8.2f}"
              f"{row['trade_count']:>8}{row['annualized_vol_trading_day_pct']:>7.2f}"
              f"{row['max_drawdown_pct']:>9.2f}{row['sharpe_trading_day']:>8.3f}"
              f"{row['mean_deployed_pct_live']:>9.2f}{row['pct_of_live_days_fully_in_cash']:>7.2f}"
              f"{row['regime_event_count']:>8}")

    print("\n=== 2022 BEAR (2022-04-21 -> 2022-10-14) ===")
    for row in results:
        print(f"  {row['variant']:<20} {row['bear_2022']}")
    print("\n=== OPEN 2026 DRAWDOWN (2026-06-23 -> 2026-07-21) ===")
    for row in results:
        print(f"  {row['variant']:<20} {row['open_2026']}")

    print("\n=== FILTER WRONGNESS (fired, then index rose) ===")
    for variant, data in wrongness.items():
        if data:
            print(f"  {variant:<20} triggered={data['triggered_rebalances']:>2} "
                  f"wrong={data['wrong_count']:>2} ({data['wrong_pct']}%) "
                  f"mean fwd={data['mean_forward_return_pct']}%")

    print("\n=== TOP 5 DRAWDOWNS PER VARIANT ===")
    for row in results:
        print(f"  {row['variant']}:")
        for episode in row["top_5_drawdowns"]:
            print(f"    {episode['depth_pct']:>7.2f}%  {episode['peak_date']} -> "
                  f"{episode['trough_date']} -> {episode['end_date']}  "
                  f"dur={episode['total_duration_days']}d recovered={episode['recovered']}")
    print(f"\nwrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
