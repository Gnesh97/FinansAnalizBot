import time

import numpy as np
import pandas as pd

try:
    from .rotation_utils import (
        STRATEGY_CACHE_VERSION,
        _atr_from_ohlc,
        _daily_ohlcv_from_frame,
        _relative_strength_metrics_from_equity as _donchian_metrics_from_equity,
        _rs_rebalance_mask as _rebalance_mask,
        safe_bool_series,
    )
    from .cost_model import calculate_max_shares, calculate_transaction_costs
    from .liquidity_filter import evaluate_entry_liquidity, liquidity_skip_summary
except ImportError:
    from strategies.rotation_utils import (
        STRATEGY_CACHE_VERSION,
        _atr_from_ohlc,
        _daily_ohlcv_from_frame,
        _relative_strength_metrics_from_equity as _donchian_metrics_from_equity,
        _rs_rebalance_mask as _rebalance_mask,
        safe_bool_series,
    )
    from strategies.cost_model import calculate_max_shares, calculate_transaction_costs
    from strategies.liquidity_filter import evaluate_entry_liquidity, liquidity_skip_summary


def _normalize_donchian_breakout_params(config):
    config = dict(config or {})
    params = {
        "donchian_entry_lookback_days": int(
            config.get("donchian_entry_lookback_days", 20)
        ),
        "donchian_exit_lookback_days": int(
            config.get("donchian_exit_lookback_days", 10)
        ),
        "donchian_top_n": int(config.get("donchian_top_n", 20)),
        "donchian_rebalance_frequency": _rebalance_frequency(
            config.get("donchian_rebalance_frequency", "daily")
        ),
        "donchian_trend_sma_days": int(
            config.get("donchian_trend_sma_days", 200)
        ),
        "donchian_use_market_regime": bool(
            config.get("donchian_use_market_regime", True)
        ),
        "donchian_market_regime_sma_days": int(
            config.get("donchian_market_regime_sma_days", 200)
        ),
        "donchian_min_price": float(config.get("donchian_min_price", 10)),
        "donchian_max_atr_pct": float(config.get("donchian_max_atr_pct", 12)),
        "min_dollar_volume": float(config.get("min_dollar_volume", 0.0) or 0.0),
    }
    for key in [
        "donchian_entry_lookback_days",
        "donchian_exit_lookback_days",
        "donchian_top_n",
        "donchian_trend_sma_days",
        "donchian_market_regime_sma_days",
    ]:
        if params[key] < 1:
            raise ValueError(f"{key} en az 1 olmali.")
    if params["donchian_min_price"] < 0:
        raise ValueError("donchian_min_price negatif olamaz.")
    if params["donchian_max_atr_pct"] <= 0:
        raise ValueError("donchian_max_atr_pct 0'dan buyuk olmali.")
    if params["min_dollar_volume"] < 0:
        raise ValueError("min_dollar_volume negatif olamaz.")
    for key in ["commission_per_share", "slippage_bps", "min_commission"]:
        if key in config:
            params[key] = config[key]
    return params


def _rebalance_frequency(value):
    frequency = str(value).lower()
    if frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError(
            "donchian_rebalance_frequency 'daily', 'weekly' veya 'monthly' olmali."
        )
    return frequency


def _donchian_cache_config(params):
    return {
        "strategy_cache_version": STRATEGY_CACHE_VERSION,
        "strategy_name": "donchian_breakout",
        "hurst_filter_mode": "off",
        "hurst_threshold": 0.5,
        **_normalize_donchian_breakout_params(params),
    }


def generate_donchian_breakout_signals(df, config):
    params = _normalize_donchian_breakout_params(config)
    frame = df.copy()
    if "Risk_Off" not in frame:
        frame["Risk_Off"] = False
    if "Regime_Ready" not in frame:
        frame["Regime_Ready"] = True

    daily = _daily_ohlcv_from_frame(frame)
    if daily.empty:
        raise ValueError("donchian_breakout icin gunluk veri uretilemedi.")

    daily_features = _donchian_features_from_daily(daily, params)
    aligned = daily_features.reindex(frame.index.normalize()).set_index(frame.index)
    for column in aligned:
        frame[column] = aligned[column]

    frame["SuperTrend_ATR_22"] = frame["Close"] * frame["ATR_Pct"] / 100
    frame["SuperTrend_ATR_22"] = frame["SuperTrend_ATR_22"].replace(
        [np.inf, -np.inf],
        np.nan,
    )
    frame["Hurst_Exponent"] = np.nan
    frame["Hurst_Filter_OK"] = True
    frame["Hurst_Entry_OK"] = True
    frame["Macro_Uptrend"] = frame["Donchian_Universe_Filter_OK"].fillna(False).astype(bool)
    frame["Macro_Filter_OK"] = frame["Macro_Uptrend"]
    frame["Quality_Filter_OK"] = frame["Donchian_Universe_Filter_OK"].fillna(False).astype(bool)
    frame["Trigger"] = frame["Donchian_Breakout"].fillna(False).astype(bool)
    frame["Raw_Entry_Signal"] = frame["Trigger"]
    frame["Confirmation_OK"] = frame["Trigger"]
    frame["Confirmed_Entry_Signal"] = frame["Trigger"]
    frame["Entry_Context_Filter_OK"] = True
    frame["Entry_Context_Rejected_Reason"] = ""
    frame["Entry_Gap_Pct"] = np.nan
    frame["Entry_BB_Distance_ATR"] = np.nan
    frame["Entry_Signal_Range_Pct"] = (
        100 * (frame["High"] - frame["Low"]) / frame["Close"].replace(0, np.nan)
    )
    frame["Entry_Signal_Close_Position_In_Range"] = (
        (frame["Close"] - frame["Low"])
        / (frame["High"] - frame["Low"]).replace(0, np.nan)
    )
    frame["Confirmation_Mode"] = "donchian_breakout"
    frame["Momentum_Factor_Raw"] = frame["Donchian_Breakout_Strength"]
    frame["Momentum_Factor"] = frame["Donchian_Breakout_Strength"]
    frame["ATR_Take_Profit_Distance"] = frame["SuperTrend_ATR_22"]
    frame["Disaster_Stop"] = frame["Close"] - frame["SuperTrend_ATR_22"]
    frame["SMA20_Reversion_Exit"] = frame["Donchian_Exit"].fillna(False).astype(bool)
    frame["Take_Profit"] = False
    frame["ENTRY_SIGNAL"] = False
    frame["EXIT_SIGNAL"] = False
    frame["Indicators_Ready"] = (
        frame["Donchian_Universe_Filter_OK"].notna()
        & frame["Donchian_Breakout_Strength"].replace([np.inf, -np.inf], np.nan).notna()
        & frame["ATR_Pct"].notna()
    )
    for column in [
        "Indicators_Ready",
        "Quality_Filter_OK",
        "Trigger",
        "Raw_Entry_Signal",
        "Confirmation_OK",
        "Confirmed_Entry_Signal",
        "Entry_Context_Filter_OK",
        "ENTRY_SIGNAL",
        "EXIT_SIGNAL",
    ]:
        frame[column] = frame[column].fillna(False).astype(bool)
    frame.attrs.update(_donchian_cache_config(params))
    return frame


def _donchian_features_from_daily(daily, params):
    trend_length = params["donchian_trend_sma_days"]
    entry_length = params["donchian_entry_lookback_days"]
    exit_length = params["donchian_exit_lookback_days"]
    atr_pct = daily.get("ATR_Pct_20")
    if atr_pct is None:
        atr = _atr_from_ohlc(daily, 20)
        atr_pct = 100 * atr / daily["Close"].replace(0, np.nan)
    trend_sma = daily.get(f"Donchian_Trend_SMA_{trend_length}")
    if trend_sma is None:
        trend_sma = daily["Close"].rolling(
            trend_length,
            min_periods=trend_length,
        ).mean()
    entry_high = daily.get(f"Donchian_Entry_High_{entry_length}")
    if entry_high is None:
        entry_high = daily["High"].rolling(
            entry_length,
            min_periods=entry_length,
        ).max().shift(1)
    exit_low = daily.get(f"Donchian_Exit_Low_{exit_length}")
    if exit_low is None:
        exit_low = daily["Low"].rolling(
            exit_length,
            min_periods=exit_length,
        ).min().shift(1)
    strength = daily["Close"] / entry_high.replace(0, np.nan) - 1
    eligible = (
        (daily["Close"] >= params["donchian_min_price"])
        & (atr_pct <= params["donchian_max_atr_pct"])
        & (daily["Close"] > trend_sma)
    )
    breakout = (
        eligible
        & (daily["Close"] > entry_high)
        & strength.replace([np.inf, -np.inf], np.nan).notna()
    )
    exit_signal = (daily["Close"] < exit_low) | (daily["Close"] < trend_sma)
    dollar_volume = daily["Close"] * daily.get("Volume", pd.Series(index=daily.index, dtype=float))
    return pd.DataFrame(
        {
            "Open": daily["Open"],
            "Close": daily["Close"],
            "Avg_Dollar_Volume": dollar_volume,
            "Signal_Avg_Dollar_Volume": dollar_volume.shift(1),
            "ATR_Pct": atr_pct,
            "Donchian_Trend_SMA": trend_sma,
            "Donchian_Entry_High": entry_high,
            "Donchian_Exit_Low": exit_low,
            "Donchian_Breakout_Strength": strength.replace([np.inf, -np.inf], np.nan),
            "Donchian_Universe_Filter_OK": eligible.fillna(False).astype(bool),
            "Donchian_Breakout": breakout.fillna(False).astype(bool),
            "Donchian_Exit": exit_signal.fillna(False).astype(bool),
        },
        index=daily.index,
    )


def _donchian_daily_features(asset_data, params):
    features = {}
    for symbol, df in asset_data.items():
        if df is None or df.empty:
            continue
        daily = _daily_ohlcv_from_frame(df)
        if daily.empty:
            continue
        features[symbol] = _donchian_features_from_daily(daily, params)
    if not features:
        raise ValueError("donchian_breakout icin kullanilabilir varlik yok.")
    return features


def _profiler_add(profiler, key, started_at):
    if profiler is not None:
        profiler[key] = profiler.get(key, 0.0) + (time.perf_counter() - started_at)


def _prepare_donchian_context(asset_data, params, benchmark_data=None, profiler=None):
    started = time.perf_counter()
    features = _donchian_daily_features(asset_data, params)
    _profiler_add(profiler, "rolling_high_low_sec", started)
    all_dates = pd.DatetimeIndex(
        sorted(set().union(*(frame.index for frame in features.values())))
    )
    if all_dates.empty:
        raise ValueError("donchian_breakout icin tarih indeksi bos.")
    regime = _donchian_regime(asset_data, benchmark_data, params, all_dates)
    rebalance = _rebalance_mask(all_dates, params["donchian_rebalance_frequency"])
    started = time.perf_counter()
    rank_frames = []
    symbol_order = {symbol: order for order, symbol in enumerate(features)}
    allowed_dates = rebalance & regime
    for symbol, frame in features.items():
        mask = frame["Donchian_Breakout"].fillna(False).astype(bool)
        scores = frame.loc[mask, "Donchian_Breakout_Strength"].replace(
            [np.inf, -np.inf],
            np.nan,
        ).dropna()
        if scores.empty:
            continue
        local = pd.DataFrame(
            {
                "date": scores.index,
                "symbol": symbol,
                "strength": scores.to_numpy(dtype=float),
                "symbol_order": symbol_order[symbol],
            }
        )
        rank_frames.append(local)
    selections = {}
    if rank_frames:
        ranking = pd.concat(rank_frames, ignore_index=True)
        valid_dates = set(allowed_dates.index[allowed_dates])
        ranking = ranking[ranking["date"].isin(valid_dates)]
        if not ranking.empty:
            ranking = ranking.sort_values(
                ["date", "strength", "symbol_order"],
                ascending=[True, False, True],
                kind="mergesort",
            )
            ranking = ranking.groupby("date", sort=False).head(params["donchian_top_n"])
            for date, group in ranking.groupby("date", sort=False):
                selections[pd.Timestamp(date)] = list(
                    zip(group["symbol"], group["strength"].astype(float))
                )
    _profiler_add(profiler, "ranking_sec", started)
    return {
        "features": features,
        "selections": selections,
        "regime": regime,
        "rebalance": rebalance,
        "all_dates": all_dates,
    }


def _find_asset_data(asset_data, benchmark_symbol):
    symbol = str(benchmark_symbol).strip().upper()
    for asset_symbol, data in asset_data.items():
        stem = str(asset_symbol).strip().upper()
        if stem.endswith(".US"):
            stem = stem[:-3]
        for delimiter in ("_", "-", " "):
            if delimiter in stem:
                stem = stem.split(delimiter, 1)[0]
        if stem == symbol:
            return data
    return None


def _donchian_regime(asset_data, benchmark_data, params, index):
    if not params["donchian_use_market_regime"]:
        return pd.Series(True, index=index)
    source = benchmark_data
    if source is None:
        source = _find_asset_data(asset_data, "SPY")
        if source is None:
            source = _find_asset_data(asset_data, "QQQ")
    if source is None or "Close" not in source:
        raise ValueError(
            "donchian_breakout icin market regime aktifken SPY/QQQ "
            "benchmark verisi gerekli."
        )
    daily = _daily_ohlcv_from_frame(source)
    close = daily["Close"].dropna()
    sma = close.rolling(
        params["donchian_market_regime_sma_days"],
        min_periods=params["donchian_market_regime_sma_days"],
    ).mean()
    regime = (close > sma).fillna(False)
    return safe_bool_series(regime, index)


def _donchian_selection(asset_data, params, benchmark_data=None):
    context = _prepare_donchian_context(asset_data, params, benchmark_data=benchmark_data)
    return (
        context["features"],
        context["selections"],
        context["regime"],
        context["rebalance"],
    )


def _apply_donchian_breakout_top_signals(
    asset_data,
    params,
    benchmark_data=None,
    context=None,
    profiler=None,
):
    started = time.perf_counter()
    if context is None:
        context = _prepare_donchian_context(
            asset_data,
            params,
            benchmark_data=benchmark_data,
            profiler=profiler,
        )
    features = context["features"]
    selections = context["selections"]
    regime = context["regime"]
    selected_by_symbol = {}
    for date, selected_rows in selections.items():
        for symbol, _ in selected_rows:
            selected_by_symbol.setdefault(symbol, []).append(date)
    updated = {}
    for symbol, data in asset_data.items():
        frame = data.copy(deep=False)
        signal_dates = pd.Series(False, index=frame.index)
        exit_dates = pd.Series(False, index=frame.index)
        normalized_dates = frame.index.normalize()
        last_bars = pd.Series(frame.index, index=frame.index).groupby(normalized_dates).last()
        selected_dates = selected_by_symbol.get(symbol, [])
        if selected_dates:
            entry_bars = last_bars.reindex(selected_dates).dropna()
            signal_dates.loc[entry_bars.to_numpy()] = True
        feature = features.get(symbol)
        if feature is not None:
            exit_feature = feature["Donchian_Exit"].reindex(last_bars.index).fillna(False)
            regime_exit = ~regime.reindex(last_bars.index).fillna(False).astype(bool)
            exit_days = (exit_feature.astype(bool) | regime_exit).index[
                (exit_feature.astype(bool) | regime_exit)
            ]
            exit_bars = last_bars.reindex(exit_days).dropna()
            if len(exit_bars):
                exit_dates.loc[exit_bars.to_numpy()] = True
            aligned = feature.reindex(normalized_dates).set_index(frame.index)
            frame["ATR_Pct"] = aligned["ATR_Pct"]
            frame["Donchian_Breakout_Strength"] = aligned["Donchian_Breakout_Strength"]
            frame["Donchian_Universe_Filter_OK"] = aligned[
                "Donchian_Universe_Filter_OK"
            ].fillna(False).astype(bool)
            frame["Donchian_Breakout"] = aligned["Donchian_Breakout"].fillna(False).astype(bool)
            frame["Donchian_Exit"] = aligned["Donchian_Exit"].fillna(False).astype(bool)
        frame["ENTRY_SIGNAL"] = signal_dates.fillna(False).astype(bool)
        frame["EXIT_SIGNAL"] = exit_dates.fillna(False).astype(bool)
        if "Risk_Off" not in frame:
            frame["Risk_Off"] = False
        if "Regime_Ready" not in frame:
            frame["Regime_Ready"] = True
        frame["Hurst_Filter_OK"] = True
        frame["Entry_Context_Filter_OK"] = True
        frame["Quality_Filter_OK"] = frame.get(
            "Donchian_Universe_Filter_OK",
            pd.Series(False, index=frame.index),
        ).fillna(False).astype(bool)
        frame["Confirmed_Entry_Signal"] = frame["ENTRY_SIGNAL"]
        frame["Trigger"] = frame.get(
            "Donchian_Breakout",
            frame["ENTRY_SIGNAL"],
        ).fillna(False).astype(bool)
        frame["Raw_Entry_Signal"] = frame["Trigger"]
        frame["Indicators_Ready"] = (
            frame.get("Donchian_Universe_Filter_OK", pd.Series(False, index=frame.index))
            .notna()
            & frame.get(
                "Donchian_Breakout_Strength",
                pd.Series(np.nan, index=frame.index),
            )
            .replace([np.inf, -np.inf], np.nan)
            .notna()
            & frame.get("ATR_Pct", pd.Series(np.nan, index=frame.index)).notna()
        )
        updated[symbol] = frame
    _profiler_add(profiler, "signal_generation_sec", started)
    return updated


def _run_donchian_breakout_backtest(
    asset_data,
    params,
    benchmark_data=None,
    context=None,
    profiler=None,
):
    donchian_params = _normalize_donchian_breakout_params(params)
    if context is None:
        context = _prepare_donchian_context(
            asset_data,
            donchian_params,
            benchmark_data=benchmark_data,
            profiler=profiler,
        )
    features = context["features"]
    selections = context["selections"]
    regime = context["regime"]
    all_dates = context["all_dates"]
    cash = 10000.0
    positions = {}
    trade_log = []
    equity_curve = []
    position_count_curve = []
    pending_entries = []
    pending_exits = set()
    liquidity_skips = []
    max_open_observed = 0
    max_heat_observed = 0.0

    def _equity_at(date, price_column):
        total = cash
        for symbol, position in positions.items():
            frame = features.get(symbol)
            if frame is None or date not in frame.index:
                total += position["shares"] * position.get(
                    "last_price",
                    position["entry_price"],
                )
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
        costs = calculate_transaction_costs(gross_proceeds, position["shares"], donchian_params)
        exit_cost = costs["total_cost"]
        proceeds = gross_proceeds - exit_cost
        cash += proceeds
        pnl = proceeds - (position["entry_notional"] + position["entry_cost"])
        r_multiple = (
            pnl / position["entry_notional"]
            if position["entry_notional"] > 0
            else np.nan
        )
        total_trade_costs = position["entry_cost"] + exit_cost
        trade_log.append(
            {
                "Symbol": symbol,
                "Entry Time": position["entry_time"],
                "Exit Time": date,
                "Exit Reason": reason,
                "Net PnL": round(float(pnl), 2),
                "R Multiple": round(float(r_multiple), 4)
                if pd.notna(r_multiple)
                else np.nan,
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
            }
        )

    portfolio_started = time.perf_counter()
    exits_sec = 0.0
    for date in all_dates:
        open_equity = _equity_at(date, "Open")
        exit_started = time.perf_counter()
        for symbol in sorted(pending_exits):
            if symbol not in positions:
                continue
            frame = features.get(symbol)
            if frame is None or date not in frame.index:
                continue
            price = frame.loc[date, "Open"]
            if pd.notna(price) and price > 0:
                _close(symbol, date, float(price), "donchian_exit")
        pending_exits = set()
        exits_sec += time.perf_counter() - exit_started

        target_weight = 1 / max(donchian_params["donchian_top_n"], 1)
        target_notional = open_equity * target_weight
        for pending_entry in pending_entries:
            symbol = pending_entry[0]
            signal_date = pending_entry[2] if len(pending_entry) > 2 else None
            if len(positions) >= donchian_params["donchian_top_n"]:
                break
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
                symbol,
                signal_date,
                signal_row,
                donchian_params["min_dollar_volume"],
            )
            if not liquidity_result["passed"]:
                liquidity_skips.append(liquidity_result)
                continue
            price = frame.loc[date, "Open"]
            if pd.isna(price) or price <= 0:
                continue
            shares = calculate_max_shares(target_notional, price, cash, donchian_params)
            if shares <= 0:
                continue
            gross_entry_notional = shares * price
            entry_costs = calculate_transaction_costs(gross_entry_notional, shares, donchian_params)
            total_entry_cost = entry_costs["total_cost"]
            cash -= (gross_entry_notional + total_entry_cost)
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
        pending_entries = []

        close_equity = _equity_at(date, "Close")
        for position in positions.values():
            position["bars_held"] += 1
        max_open_observed = max(max_open_observed, len(positions))
        exposure = sum(
            position["shares"] * position.get("last_price", position["entry_price"])
            for position in positions.values()
        )
        max_heat_observed = max(
            max_heat_observed,
            exposure / close_equity if close_equity > 0 else 0.0,
        )
        position_count_curve.append(len(positions))
        equity_curve.append((date, close_equity))

        exit_started = time.perf_counter()
        pending_exits = {
            symbol
            for symbol, frame in features.items()
            if symbol in positions
            and date in frame.index
            and (
                bool(frame.loc[date, "Donchian_Exit"])
                or not bool(regime.loc[date])
            )
        }
        exits_sec += time.perf_counter() - exit_started
        held_after_exits = set(positions) - pending_exits
        capacity = max(donchian_params["donchian_top_n"] - len(held_after_exits), 0)
        if capacity and date in selections and bool(regime.loc[date]):
            pending_entries = [
                (item[0], item[1], date)
                for item in selections[date]
                if item[0] not in positions and item[0] not in pending_exits
            ][:capacity]

    final_date = all_dates[-1] if len(all_dates) else None
    if final_date is not None:
        for symbol in list(positions):
            frame = features.get(symbol)
            if frame is None or final_date not in frame.index:
                continue
            price = frame.loc[final_date, "Close"]
            if pd.notna(price) and price > 0:
                _close(symbol, final_date, float(price), "forced_final_exit")
        if equity_curve:
            equity_curve[-1] = (final_date, cash)

    metrics = _donchian_metrics_from_equity(
        equity_curve,
        trade_log,
        initial_cash=10000.0,
        max_open_observed=max_open_observed,
        max_heat_observed=max_heat_observed,
        position_count_curve=position_count_curve,
    )
    metrics.update(
        liquidity_skip_summary(liquidity_skips, donchian_params["min_dollar_volume"])
    )
    metrics["_trade_log"] = trade_log
    metrics["_equity_curve"] = equity_curve
    metrics["_liquidity_skipped_entries"] = liquidity_skips
    _profiler_add(profiler, "portfolio_sim_sec", portfolio_started)
    if profiler is not None:
        profiler["exits_sec"] = profiler.get("exits_sec", 0.0) + exits_sec
    return metrics


__all__ = [
    "_apply_donchian_breakout_top_signals",
    "_normalize_donchian_breakout_params",
    "_prepare_donchian_context",
    "_run_donchian_breakout_backtest",
    "generate_donchian_breakout_signals",
]
