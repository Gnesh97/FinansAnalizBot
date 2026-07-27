"""Phase 8A Measurement 3 -- ATR risk-parity sizing SANDBOX. EVALUATION ONLY.

NOT wired into production. Changes no default. Imported by nothing. This file
exists solely to answer "what would Clenow-style sizing have done?" on the
frozen baseline window.

Position sizing is the ONLY thing that differs from production. The ranking,
the SMA100 eligibility filter, the monthly rebalance calendar, next-day-open
execution, transaction costs, the liquidity filter and the universe all come
from the unmodified production modules.

Because sizing lives inside _run_relative_strength_rotation_backtest, the loop
below is a faithful copy of that function with a `sizing` switch spliced into
the entry block. To prove the copy is faithful rather than merely claimed, the
`equal_weight` mode must reproduce the frozen baseline EXACTLY (175.49 / 770)
before any ATR result is reported. If it does not, the run aborts.

    python3 scratch/phase8a_atr_sizing_sandbox.py
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

from testdata.runner import _default_params, _load_or_build_prepared_cache
from testdata.baseline_parity import _baseline_experiment
from testdata.strategies.cost_model import calculate_max_shares, calculate_transaction_costs
from testdata.strategies.liquidity_filter import evaluate_entry_liquidity, liquidity_skip_summary
from testdata.strategies.relative_strength_rotation import (
    _normalize_relative_strength_params,
    _relative_strength_metrics_from_equity,
    _relative_strength_selection,
)

DATA_FOLDER = PROJECT_ROOT / "testdata" / "research_data" / "rs_v2_tiingo_market_data"
BASELINE_PATH = PROJECT_ROOT / "testdata" / "baselines" / "rs_v2_tiingo_strategy_baseline.json"
OUTPUT_PATH = Path(__file__).resolve().parent / "phase8a_atr_sizing_output.json"

TRADING_DAYS = 252
INITIAL_CASH = 10000.0


def run_backtest(asset_data, params, benchmark_data=None, sizing="equal_weight", risk_factor=0.001):
    """Faithful copy of _run_relative_strength_rotation_backtest with a sizing switch.

    sizing="equal_weight": target_notional = open_equity / rs_top_n, symbols
        entered in alphabetical order -- byte-identical to production.
    sizing="atr_risk_parity": shares = open_equity * risk_factor / ATR20, symbols
        entered in RANK order (highest RS_Score first) so that when capital runs
        out it is the lowest-ranked names that go unfilled. Rank order is
        required by "buy down the ranking until capital is exhausted"; it is
        inert under equal weight, which is why production can sort alphabetically.
    """
    rs_params = _normalize_relative_strength_params(params)
    features, selections, regime, _ = _relative_strength_selection(
        asset_data, rs_params, benchmark_data=benchmark_data,
    )
    all_dates = pd.DatetimeIndex(
        sorted(set().union(*(frame.index for frame in features.values())))
    )
    cash = INITIAL_CASH
    positions = {}
    trade_log = []
    equity_curve = []
    position_count_curve = []
    pending_target = None
    pending_target_signal_date = None
    liquidity_skips = []
    max_open_observed = 0
    max_heat_observed = 0.0
    entry_weights = []
    deployed_curve = []
    unfilled_events = 0

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
        total_trade_costs = position["entry_cost"] + exit_cost
        trade_log.append({
            "Symbol": symbol,
            "Entry Time": position["entry_time"],
            "Exit Time": date,
            "Exit Reason": reason,
            "Net PnL": round(float(pnl), 2),
            "R Multiple": round(float(r_multiple), 4) if pd.notna(r_multiple) else np.nan,
            "Bars Held": int(position["bars_held"]),
            "Entry Price": position["entry_price"],
            "Exit Price": price,
            "Shares": position["shares"],
            "Entry Notional": position["entry_notional"],
            "gross_notional": round(position["entry_notional"] + gross_proceeds, 2),
            "transaction_cost": round(total_trade_costs, 4),
            "slippage_cost": round(position["entry_slippage"] + costs["slippage_cost"], 4),
            "commission_cost": round(position["entry_commission"] + costs["commission_cost"], 4),
            "net_cash_effect": round(pnl, 2),
        })

    def _entry_order(target_symbols, signal_date):
        if sizing == "equal_weight":
            return sorted(target_symbols)
        scored = []
        for symbol in target_symbols:
            frame = features.get(symbol)
            score = (
                float(frame.loc[signal_date, "RS_Score"])
                if frame is not None and signal_date in frame.index
                and pd.notna(frame.loc[signal_date, "RS_Score"])
                else float("-inf")
            )
            scored.append((score, symbol))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [symbol for _, symbol in scored]

    def _target_notional(symbol, open_equity, price, signal_date):
        if sizing == "equal_weight":
            return open_equity * (1 / max(rs_params["rs_top_n"], 1))
        frame = features.get(symbol)
        if frame is None or signal_date is None or signal_date not in frame.index:
            return 0.0
        # Reuse the production ATR: features carries ATR_Pct = 100 * ATR20 / Close.
        atr_pct = frame.loc[signal_date, "ATR_Pct"]
        close = frame.loc[signal_date, "Close"]
        if pd.isna(atr_pct) or pd.isna(close) or atr_pct <= 0 or close <= 0:
            return 0.0
        atr = float(atr_pct) / 100.0 * float(close)
        if atr <= 0:
            return 0.0
        shares_target = open_equity * risk_factor / atr
        return shares_target * price

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
            signal_date = pending_target_signal_date
            for symbol in _entry_order(target_symbols, signal_date):
                if symbol in positions:
                    continue
                frame = features.get(symbol)
                if frame is None or date not in frame.index:
                    continue
                signal_row = (
                    frame.loc[signal_date]
                    if signal_date is not None and signal_date in frame.index
                    else None
                )
                liquidity_result = evaluate_entry_liquidity(
                    symbol, signal_date, signal_row, rs_params["min_dollar_volume"],
                )
                if not liquidity_result["passed"]:
                    liquidity_skips.append(liquidity_result)
                    continue
                price = frame.loc[date, "Open"]
                if pd.isna(price) or price <= 0:
                    continue
                target_notional = _target_notional(symbol, open_equity, float(price), signal_date)
                shares = calculate_max_shares(target_notional, price, cash, rs_params)
                if shares <= 0:
                    unfilled_events += 1
                    continue
                gross_entry_notional = shares * price
                entry_costs = calculate_transaction_costs(gross_entry_notional, shares, rs_params)
                total_entry_cost = entry_costs["total_cost"]
                cash -= (gross_entry_notional + total_entry_cost)
                if open_equity > 0:
                    entry_weights.append(gross_entry_notional / open_equity)
                positions[symbol] = {
                    "entry_time": date,
                    "entry_price": float(price),
                    "entry_notional": gross_entry_notional,
                    "shares": shares,
                    "bars_held": 0,
                    "last_price": float(price),
                    "entry_commission": entry_costs["commission_cost"],
                    "entry_slippage": entry_costs["slippage_cost"],
                    "entry_cost": total_entry_cost,
                }
            pending_target = None
            pending_target_signal_date = None

        close_equity = _equity_at(date, "Close")
        for position in positions.values():
            position["bars_held"] += 1
        max_open_observed = max(max_open_observed, len(positions))
        exposure = sum(
            position["shares"] * position.get("last_price", position["entry_price"])
            for position in positions.values()
        )
        max_heat_observed = max(max_heat_observed, exposure / close_equity if close_equity > 0 else 0.0)
        position_count_curve.append(len(positions))
        deployed_curve.append(exposure / close_equity if close_equity > 0 else 0.0)
        equity_curve.append((date, close_equity))
        if date in selections:
            pending_target = selections[date] if bool(regime.loc[date]) else set()
            pending_target_signal_date = date

    final_date = all_dates[-1]
    for symbol in list(positions):
        frame = features.get(symbol)
        if frame is None or final_date not in frame.index:
            continue
        price = frame.loc[final_date, "Close"]
        if pd.notna(price) and price > 0:
            _close(symbol, final_date, float(price), "forced_final_exit")
    final_equity = cash
    if equity_curve:
        equity_curve[-1] = (final_date, final_equity)

    metrics = _relative_strength_metrics_from_equity(
        equity_curve, trade_log,
        initial_cash=INITIAL_CASH,
        max_open_observed=max_open_observed,
        max_heat_observed=max_heat_observed,
        position_count_curve=position_count_curve,
    )
    metrics.update(liquidity_skip_summary(liquidity_skips, rs_params["min_dollar_volume"]))
    metrics["_equity_curve"] = equity_curve
    metrics["_trade_log"] = trade_log
    metrics["_entry_weights"] = entry_weights
    metrics["_position_count_curve"] = position_count_curve
    metrics["_deployed_curve"] = deployed_curve
    metrics["_unfilled_events"] = unfilled_events
    return metrics


def summarize(label, metrics):
    equity = pd.Series(
        [value for _, value in metrics["_equity_curve"]],
        index=pd.DatetimeIndex([ts for ts, _ in metrics["_equity_curve"]]),
        dtype=float,
    )
    trading_returns = equity.pct_change(fill_method=None).dropna()
    trading_vol = float(trading_returns.std()) * np.sqrt(TRADING_DAYS) * 100
    trading_sharpe = float(trading_returns.mean() / trading_returns.std() * np.sqrt(TRADING_DAYS))
    weights = pd.Series(metrics["_entry_weights"], dtype=float)
    counts = pd.Series(metrics["_position_count_curve"], dtype=float)
    live_counts = counts[counts > 0]
    # Deployment separates "sizing changed the bets" from "sizing left cash idle".
    deployed = pd.Series(metrics["_deployed_curve"], dtype=float) * 100
    live_deployed = deployed[counts > 0]
    return {
        "label": label,
        "net_return_pct": metrics.get("Net Getiri (%)"),
        "trade_count": metrics.get("Islem Sayisi"),
        "cagr_pct": metrics.get("CAGR (%)"),
        "max_drawdown_pct": metrics.get("Max Drawdown (%)"),
        "sharpe_system_method": metrics.get("Sharpe Ratio"),
        "sharpe_trading_day": round(trading_sharpe, 4),
        "annualized_vol_trading_day_pct": round(trading_vol, 2),
        "avg_positions_held": metrics.get("Avg Positions Held"),
        "max_positions_held": metrics.get("Max Positions Held"),
        "avg_positions_while_invested": round(float(live_counts.mean()), 2) if len(live_counts) else 0.0,
        "entry_weight_min_pct": round(float(weights.min()) * 100, 3) if len(weights) else None,
        "entry_weight_median_pct": round(float(weights.median()) * 100, 3) if len(weights) else None,
        "entry_weight_max_pct": round(float(weights.max()) * 100, 3) if len(weights) else None,
        "entry_count": int(len(weights)),
        "unfilled_entry_events": metrics["_unfilled_events"],
        "max_portfolio_heat_pct": metrics.get("Max Portfolio Heat (%)"),
        "mean_deployed_pct_while_invested": round(float(live_deployed.mean()), 2) if len(live_deployed) else 0.0,
        "median_deployed_pct_while_invested": round(float(live_deployed.median()), 2) if len(live_deployed) else 0.0,
    }


def main() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    daily_dir = DATA_FOLDER / "daily"
    intraday_dir = DATA_FOLDER / "bars_130min"
    symbols = sorted(p.name.removesuffix("_1D.csv") for p in daily_dir.glob("*_1D.csv"))
    prepared, _, _ = _load_or_build_prepared_cache(
        str(DATA_FOLDER), str(intraday_dir), str(daily_dir), "130min", "1D",
        baseline["window"]["target_start"], baseline["window"]["slice_upper_bound"], symbols,
    )
    asset_data = prepared["asset_data"]
    benchmark = prepared.get("benchmark_data")
    params = _default_params(_baseline_experiment(), str(DATA_FOLDER), "full", False)

    # --- Fidelity gate: the copied loop must reproduce production exactly ---
    control = run_backtest(asset_data, params, benchmark, sizing="equal_weight")
    expected = baseline["metrics"]
    control_ok = (
        abs(control.get("Net Getiri (%)") - expected["net_return_pct"]) < 0.005
        and control.get("Islem Sayisi") == expected["trade_count"]
        and abs(control.get("Max Drawdown (%)") - expected["max_drawdown_pct"]) < 0.005
    )
    print("=== FIDELITY GATE (equal-weight replica vs frozen baseline) ===")
    print(f"  replica : {control.get('Net Getiri (%)')}% / {control.get('Islem Sayisi')} trades "
          f"/ maxDD {control.get('Max Drawdown (%)')}%")
    print(f"  baseline: {expected['net_return_pct']}% / {expected['trade_count']} trades "
          f"/ maxDD {expected['max_drawdown_pct']}%")
    print(f"  faithful: {control_ok}")
    if not control_ok:
        raise SystemExit("ABORT: sandbox loop does not reproduce production; ATR results would be meaningless.")

    runs = [summarize("equal_weight (control = production)", control)]
    for risk_factor in (0.0005, 0.001, 0.002):
        metrics = run_backtest(asset_data, params, benchmark, sizing="atr_risk_parity", risk_factor=risk_factor)
        runs.append(summarize(f"atr_risk_parity rf={risk_factor}", metrics))

    baseline_row = runs[0]
    for row in runs:
        row["delta_net_return_pct"] = round(row["net_return_pct"] - baseline_row["net_return_pct"], 2)
        row["delta_trade_count"] = row["trade_count"] - baseline_row["trade_count"]
        row["delta_max_drawdown_pct"] = round(row["max_drawdown_pct"] - baseline_row["max_drawdown_pct"], 2)
        row["delta_vol_pct"] = round(row["annualized_vol_trading_day_pct"] - baseline_row["annualized_vol_trading_day_pct"], 2)

    OUTPUT_PATH.write_text(json.dumps({"fidelity_gate_passed": control_ok, "runs": runs}, indent=2) + "\n",
                           encoding="utf-8")

    print("\n=== MEASUREMENT 3: SIZING COMPARISON ===")
    header = (f"{'variant':<38}{'return%':>9}{'trades':>8}{'vol%':>8}{'maxDD%':>9}"
              f"{'Sharpe':>8}{'avgPos':>8}{'wMin%':>8}{'wMed%':>8}{'wMax%':>8}")
    print(header)
    print("-" * len(header))
    for row in runs:
        print(f"{row['label']:<38}{row['net_return_pct']:>9.2f}{row['trade_count']:>8}"
              f"{row['annualized_vol_trading_day_pct']:>8.2f}{row['max_drawdown_pct']:>9.2f}"
              f"{row['sharpe_trading_day']:>8.3f}{row['avg_positions_while_invested']:>8.2f}"
              f"{row['entry_weight_min_pct']:>8.2f}{row['entry_weight_median_pct']:>8.2f}"
              f"{row['entry_weight_max_pct']:>8.2f}")
    print(f"\nwrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
