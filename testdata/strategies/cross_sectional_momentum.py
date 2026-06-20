import pandas as pd
import numpy as np

try:
    from .rotation_utils import (
        _atr_from_ohlc,
        _daily_ohlcv_from_frame,
        _normalize_rs_rebalance_frequency as _normalize_rebalance_frequency,
        _relative_strength_metrics_from_equity as _csm_metrics_from_equity,
        _rs_rebalance_mask as _rebalance_mask,
        safe_bool_series,
        _strategy_cache_config,
    )
except ImportError:
    from strategies.rotation_utils import (
        _atr_from_ohlc,
        _daily_ohlcv_from_frame,
        _normalize_rs_rebalance_frequency as _normalize_rebalance_frequency,
        _relative_strength_metrics_from_equity as _csm_metrics_from_equity,
        _rs_rebalance_mask as _rebalance_mask,
        safe_bool_series,
        _strategy_cache_config,
    )

def _normalize_cross_sectional_momentum_params(config):
    config = dict(config or {})
    params = {
        "csm_lookback_days": int(config.get("csm_lookback_days", 126)),
        "csm_top_n": int(config.get("csm_top_n", 20)),
        "csm_rebalance_frequency": _normalize_rebalance_frequency(
            config.get("csm_rebalance_frequency", "monthly")
        ),
        "csm_trend_sma_days": int(config.get("csm_trend_sma_days", 200)),
        "csm_use_market_regime": bool(config.get("csm_use_market_regime", True)),
        "csm_market_regime_sma_days": int(
            config.get("csm_market_regime_sma_days", 200)
        ),
        "csm_min_price": float(config.get("csm_min_price", 10)),
        "csm_max_atr_pct": float(config.get("csm_max_atr_pct", 12)),
    }
    for key in [
        "csm_lookback_days",
        "csm_top_n",
        "csm_trend_sma_days",
        "csm_market_regime_sma_days",
    ]:
        if params[key] < 1:
            raise ValueError(f"{key} en az 1 olmali.")
    if params["csm_min_price"] < 0:
        raise ValueError("csm_min_price negatif olamaz.")
    if params["csm_max_atr_pct"] <= 0:
        raise ValueError("csm_max_atr_pct 0'dan buyuk olmali.")
    return params

def generate_cross_sectional_momentum_signals(df, config):
    params = _normalize_cross_sectional_momentum_params(config)
    frame = df.copy()
    if "Risk_Off" not in frame:
        frame["Risk_Off"] = False
    if "Regime_Ready" not in frame:
        frame["Regime_Ready"] = True

    daily = _daily_ohlcv_from_frame(frame)
    if daily.empty:
        raise ValueError("cross_sectional_momentum icin gunluk veri uretilemedi.")

    atr = _atr_from_ohlc(daily, 20)
    atr_pct = 100 * atr / daily["Close"].replace(0, np.nan)
    trend_sma = daily["Close"].rolling(
        params["csm_trend_sma_days"],
        min_periods=params["csm_trend_sma_days"],
    ).mean()
    
    score = daily["Close"] / daily["Close"].shift(params["csm_lookback_days"]) - 1

    eligible = (
        (daily["Close"] >= params["csm_min_price"])
        & (atr_pct <= params["csm_max_atr_pct"])
        & (daily["Close"] > trend_sma)
        & score.replace([np.inf, -np.inf], np.nan).notna()
    )
    daily_features = pd.DataFrame(index=daily.index)
    daily_features["CSM_Score"] = score.replace([np.inf, -np.inf], np.nan)
    daily_features["CSM_Trend_SMA"] = trend_sma
    daily_features["ATR_Pct"] = atr_pct
    daily_features["CSM_Universe_Filter_OK"] = eligible.fillna(False).astype(bool)

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
    frame["Macro_Uptrend"] = frame["CSM_Universe_Filter_OK"].fillna(False).astype(bool)
    frame["Macro_Filter_OK"] = frame["Macro_Uptrend"]
    frame["Quality_Filter_OK"] = frame["CSM_Universe_Filter_OK"].fillna(False).astype(bool)
    frame["Trigger"] = frame["CSM_Universe_Filter_OK"].fillna(False).astype(bool)
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
        (frame["Close"] - frame["Low"]) / (frame["High"] - frame["Low"]).replace(0, np.nan)
    )
    frame["Confirmation_Mode"] = "cross_sectional_momentum"
    frame["Momentum_Factor_Raw"] = frame["CSM_Score"]
    frame["Momentum_Factor"] = frame["CSM_Score"]
    frame["ATR_Take_Profit_Distance"] = frame["SuperTrend_ATR_22"]
    frame["Disaster_Stop"] = frame["Close"] - frame["SuperTrend_ATR_22"]
    frame["SMA20_Reversion_Exit"] = ~frame["CSM_Universe_Filter_OK"].fillna(False).astype(bool)
    frame["Take_Profit"] = False
    frame["ENTRY_SIGNAL"] = False
    frame["EXIT_SIGNAL"] = False
    frame["Indicators_Ready"] = (
        frame["CSM_Universe_Filter_OK"].notna()
        & frame["CSM_Score"].replace([np.inf, -np.inf], np.nan).notna()
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
    frame.attrs.update(_strategy_cache_config("off", 0.5, strategy_name="cross_sectional_momentum", **params))
    return frame

def _cross_sectional_momentum_daily_features(asset_data, params):
    features = {}
    for symbol, df in asset_data.items():
        if df is None or df.empty:
            continue
        daily = _daily_ohlcv_from_frame(df)
        if daily.empty:
            continue
        
        atr = _atr_from_ohlc(daily, 20)
        atr_pct = 100 * atr / daily["Close"].replace(0, np.nan)
        trend_sma = daily["Close"].rolling(
            params["csm_trend_sma_days"],
            min_periods=params["csm_trend_sma_days"],
        ).mean()
        
        score = daily["Close"] / daily["Close"].shift(params["csm_lookback_days"]) - 1

        eligible = (
            (daily["Close"] >= params["csm_min_price"])
            & (atr_pct <= params["csm_max_atr_pct"])
            & (daily["Close"] > trend_sma)
            & score.replace([np.inf, -np.inf], np.nan).notna()
        )
        
        daily_features = pd.DataFrame(index=daily.index)
        daily_features["Open"] = daily["Open"]
        daily_features["Close"] = daily["Close"]
        daily_features["CSM_Score"] = score.replace([np.inf, -np.inf], np.nan)
        daily_features["CSM_Eligible"] = eligible.fillna(False).astype(bool)
        features[symbol] = daily_features
    return features

def _cross_sectional_momentum_regime(asset_data, benchmark_data, params, index):
    if not params["csm_use_market_regime"]:
        return pd.Series(True, index=index)
    if benchmark_data is not None and not benchmark_data.empty:
        daily_bench = _daily_ohlcv_from_frame(benchmark_data)
        sma = daily_bench["Close"].rolling(params["csm_market_regime_sma_days"]).mean()
        regime = (daily_bench["Close"] > sma).fillna(False)
        return safe_bool_series(regime, index)
    
    spy_data = asset_data.get("SPY")
    qqq_data = asset_data.get("QQQ")
    ref_data = spy_data if spy_data is not None else qqq_data
    if ref_data is not None and not ref_data.empty:
        daily_ref = _daily_ohlcv_from_frame(ref_data)
        sma = daily_ref["Close"].rolling(params["csm_market_regime_sma_days"]).mean()
        regime = (daily_ref["Close"] > sma).fillna(False)
        return safe_bool_series(regime, index)
    
    raise ValueError("cross_sectional_momentum icin market regime aktifken SPY/QQQ veya benchmark verisi gereklidir.")

def _cross_sectional_momentum_selection(asset_data, params, benchmark_data=None):
    features = _cross_sectional_momentum_daily_features(asset_data, params)
    all_dates = pd.DatetimeIndex(
        sorted(set().union(*(frame.index for frame in features.values())))
    )
    if all_dates.empty:
        raise ValueError("cross_sectional_momentum icin tarih indeksi bos.")
    regime = _cross_sectional_momentum_regime(asset_data, benchmark_data, params, all_dates)
    rebalance = _rebalance_mask(all_dates, params["csm_rebalance_frequency"])
    selections = {}
    for date in all_dates:
        if not bool(rebalance.loc[date]):
            continue
        if not bool(regime.loc[date]):
            selections[date] = set()
            continue
        rows = []
        for symbol, frame in features.items():
            if date not in frame.index:
                continue
            row = frame.loc[date]
            if bool(row.get("CSM_Eligible", False)) and pd.notna(row.get("CSM_Score")):
                rows.append((symbol, float(row["CSM_Score"])))
        rows.sort(key=lambda item: item[1], reverse=True)
        selections[date] = {symbol for symbol, _ in rows[: params["csm_top_n"]]}
    return features, selections, regime, rebalance

def _apply_cross_sectional_momentum_top_signals(asset_data, params, benchmark_data=None):
    features, selections, regime, rebalance = _cross_sectional_momentum_selection(
        asset_data,
        params,
        benchmark_data=benchmark_data,
    )
    updated = {}
    for symbol, data in asset_data.items():
        frame = data.copy(deep=False)
        signal_dates = pd.Series(False, index=frame.index)
        exit_dates = pd.Series(False, index=frame.index)
        normalized_dates = frame.index.normalize()
        for date, selected in selections.items():
            date_mask = normalized_dates == date
            if not date_mask.any():
                continue
            last_bar = frame.index[date_mask][-1]
            in_top = symbol in selected
            signal_dates.loc[last_bar] = in_top
            trend_ok = bool(
                symbol in features
                and date in features[symbol].index
                and features[symbol].loc[date, "CSM_Eligible"]
            )
            exit_dates.loc[last_bar] = (not in_top) or (not trend_ok) or (not bool(regime.loc[date]))
        frame["ENTRY_SIGNAL"] = signal_dates.fillna(False).astype(bool)
        frame["EXIT_SIGNAL"] = exit_dates.fillna(False).astype(bool)
        frame["Confirmed_Entry_Signal"] = frame["ENTRY_SIGNAL"]
        frame["Trigger"] = frame.get("CSM_Universe_Filter_OK", frame["ENTRY_SIGNAL"]).fillna(False).astype(bool)
        updated[symbol] = frame
    return updated

def _run_cross_sectional_momentum_backtest(asset_data, params, benchmark_data=None):
    csm_params = _normalize_cross_sectional_momentum_params(params)
    features, selections, regime, _ = _cross_sectional_momentum_selection(
        asset_data,
        csm_params,
        benchmark_data=benchmark_data,
    )
    all_dates = pd.DatetimeIndex(
        sorted(set().union(*(frame.index for frame in features.values())))
    )
    cash = 10000.0
    positions = {}
    trade_log = []
    equity_curve = []
    position_count_curve = []
    pending_target = None
    max_open_observed = 0
    max_heat_observed = 0.0

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
        proceeds = position["shares"] * price
        cash += proceeds
        pnl = proceeds - position["entry_notional"]
        r_multiple = pnl / position["entry_notional"] if position["entry_notional"] > 0 else np.nan
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
            }
        )

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
                            _close(symbol, date, float(price), "csm_rotation_exit")
            target_weight = 1 / max(csm_params["csm_top_n"], 1)
            target_notional = open_equity * target_weight
            for symbol in sorted(target_symbols):
                if symbol in positions:
                    continue
                frame = features.get(symbol)
                if frame is None or date not in frame.index:
                    continue
                price = frame.loc[date, "Open"]
                if pd.isna(price) or price <= 0:
                    continue
                notional = min(cash, target_notional)
                if notional <= 0:
                    continue
                shares = notional / float(price)
                cash -= shares * float(price)
                positions[symbol] = {
                    "entry_time": date,
                    "entry_price": float(price),
                    "shares": shares,
                    "entry_notional": notional,
                    "bars_held": 0,
                    "last_price": float(price),
                }
            pending_target = None

        for symbol in list(positions):
            frame = features.get(symbol)
            if frame is not None and date in frame.index:
                price = frame.loc[date, "Close"]
                if pd.notna(price) and price > 0:
                    positions[symbol]["last_price"] = float(price)
                    positions[symbol]["bars_held"] += 1

        close_equity = _equity_at(date, "Close")
        equity_curve.append((date, close_equity))
        open_positions = len(positions)
        position_count_curve.append(open_positions)
        if open_positions > max_open_observed:
            max_open_observed = open_positions
        if close_equity > 0:
            heat = sum(pos["entry_notional"] for pos in positions.values()) / close_equity
            if heat > max_heat_observed:
                max_heat_observed = heat

        if date in selections:
            selected = selections[date]
            current_symbols = set(positions.keys())
            if selected != current_symbols:
                pending_target = selected

    last_date = all_dates[-1] if not all_dates.empty else None
    if last_date:
        for symbol in list(positions):
            frame = features.get(symbol)
            if frame is not None and last_date in frame.index:
                price = frame.loc[last_date, "Close"]
                if pd.notna(price) and price > 0:
                    _close(symbol, last_date, float(price), "forced_final_exit")

    metrics = _csm_metrics_from_equity(
        equity_curve,
        trade_log,
        initial_cash=10000.0,
        max_open_observed=max_open_observed,
        max_heat_observed=max_heat_observed,
        position_count_curve=position_count_curve,
    )
    metrics["_trade_log"] = trade_log
    metrics["_equity_curve"] = equity_curve
    return metrics
