import numpy as np
import pandas as pd

try:
    from .rotation_utils import (
        STRATEGY_CACHE_VERSION,
        _atr_from_ohlc,
        _daily_ohlcv_from_frame,
        _normalize_rs_rebalance_frequency as _normalize_rebalance_frequency,
        _relative_strength_metrics_from_equity as _mast_metrics_from_equity,
        _rs_rebalance_mask as _rebalance_mask,
        safe_bool_series,
    )
except ImportError:
    from strategies.rotation_utils import (
        STRATEGY_CACHE_VERSION,
        _atr_from_ohlc,
        _daily_ohlcv_from_frame,
        _normalize_rs_rebalance_frequency as _normalize_rebalance_frequency,
        _relative_strength_metrics_from_equity as _mast_metrics_from_equity,
        _rs_rebalance_mask as _rebalance_mask,
        safe_bool_series,
    )


def _normalize_ma_slope_trend_params(config):
    config = dict(config or {})
    params = {
        "mast_slope_sma_days": int(config.get("mast_slope_sma_days", 100)),
        "mast_slope_lookback_days": int(config.get("mast_slope_lookback_days", 20)),
        "mast_top_n": int(config.get("mast_top_n", 20)),
        "mast_rebalance_frequency": _normalize_rebalance_frequency(
            config.get("mast_rebalance_frequency", "monthly")
        ),
        "mast_score_lookback_days": int(config.get("mast_score_lookback_days", 63)),
        "mast_use_market_regime": bool(config.get("mast_use_market_regime", True)),
        "mast_market_regime_sma_days": int(
            config.get("mast_market_regime_sma_days", 200)
        ),
        "mast_min_price": float(config.get("mast_min_price", 10)),
        "mast_max_atr_pct": float(config.get("mast_max_atr_pct", 12)),
    }
    for key in [
        "mast_slope_sma_days",
        "mast_slope_lookback_days",
        "mast_top_n",
        "mast_score_lookback_days",
        "mast_market_regime_sma_days",
    ]:
        if params[key] < 1:
            raise ValueError(f"{key} en az 1 olmali.")
    if params["mast_min_price"] < 0:
        raise ValueError("mast_min_price negatif olamaz.")
    if params["mast_max_atr_pct"] <= 0:
        raise ValueError("mast_max_atr_pct 0'dan buyuk olmali.")
    return params


def _ma_slope_trend_cache_config(params):
    return {
        "strategy_cache_version": STRATEGY_CACHE_VERSION,
        "strategy_name": "ma_slope_trend",
        "hurst_filter_mode": "off",
        "hurst_threshold": 0.5,
        **_normalize_ma_slope_trend_params(params),
    }


def _ma_slope_features_from_daily(daily, params):
    atr = _atr_from_ohlc(daily, 20)
    atr_pct = 100 * atr / daily["Close"].replace(0, np.nan)
    sma = daily["Close"].rolling(
        params["mast_slope_sma_days"],
        min_periods=params["mast_slope_sma_days"],
    ).mean()
    slope = sma / sma.shift(params["mast_slope_lookback_days"]).replace(0, np.nan) - 1
    momentum = daily["Close"] / daily["Close"].shift(
        params["mast_score_lookback_days"]
    ) - 1
    slope = slope.replace([np.inf, -np.inf], np.nan)
    momentum = momentum.replace([np.inf, -np.inf], np.nan)
    score = (slope + 0.5 * momentum).replace([np.inf, -np.inf], np.nan)
    universe_ok = (
        (daily["Close"] >= params["mast_min_price"])
        & (atr_pct <= params["mast_max_atr_pct"])
        & (daily["Close"] > sma)
    )
    eligible = universe_ok & (slope > 0) & score.notna()
    exit_signal = (slope <= 0) | (daily["Close"] < sma) | (~universe_ok)
    return pd.DataFrame(
        {
            "Open": daily["Open"],
            "Close": daily["Close"],
            "ATR_Pct": atr_pct,
            "MAST_SMA": sma,
            "MAST_Slope": slope,
            "MAST_Momentum": momentum,
            "MAST_Score": score,
            "MAST_Universe_Filter_OK": universe_ok.fillna(False).astype(bool),
            "MAST_Eligible": eligible.fillna(False).astype(bool),
            "MAST_Exit": exit_signal.fillna(False).astype(bool),
        },
        index=daily.index,
    )


def generate_ma_slope_trend_signals(df, config):
    params = _normalize_ma_slope_trend_params(config)
    frame = df.copy()
    if "Risk_Off" not in frame:
        frame["Risk_Off"] = False
    if "Regime_Ready" not in frame:
        frame["Regime_Ready"] = True
    daily = _daily_ohlcv_from_frame(frame)
    if daily.empty:
        raise ValueError("ma_slope_trend icin gunluk veri uretilemedi.")

    daily_features = _ma_slope_features_from_daily(daily, params).drop(
        columns=["Open", "Close"]
    )
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
    frame["Macro_Uptrend"] = frame["MAST_Universe_Filter_OK"].fillna(False).astype(bool)
    frame["Macro_Filter_OK"] = frame["Macro_Uptrend"]
    frame["Quality_Filter_OK"] = frame["MAST_Universe_Filter_OK"].fillna(False).astype(bool)
    frame["Trigger"] = frame["MAST_Eligible"].fillna(False).astype(bool)
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
    frame["Confirmation_Mode"] = "ma_slope_trend"
    frame["Momentum_Factor_Raw"] = frame["MAST_Score"]
    frame["Momentum_Factor"] = frame["MAST_Score"]
    frame["ATR_Take_Profit_Distance"] = frame["SuperTrend_ATR_22"]
    frame["Disaster_Stop"] = frame["Close"] - frame["SuperTrend_ATR_22"]
    frame["SMA20_Reversion_Exit"] = frame["MAST_Exit"].fillna(False).astype(bool)
    frame["Take_Profit"] = False
    frame["ENTRY_SIGNAL"] = False
    frame["EXIT_SIGNAL"] = False
    frame["Indicators_Ready"] = (
        frame["MAST_Score"].replace([np.inf, -np.inf], np.nan).notna()
        & frame["MAST_Slope"].replace([np.inf, -np.inf], np.nan).notna()
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
    frame.attrs.update(_ma_slope_trend_cache_config(params))
    return frame


def _ma_slope_daily_features(asset_data, params):
    features = {}
    for symbol, df in asset_data.items():
        if df is None or df.empty:
            continue
        daily = _daily_ohlcv_from_frame(df)
        if daily.empty:
            continue
        features[symbol] = _ma_slope_features_from_daily(daily, params)
    if not features:
        raise ValueError("ma_slope_trend icin kullanilabilir varlik yok.")
    return features


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


def _ma_slope_regime(asset_data, benchmark_data, params, index):
    if not params["mast_use_market_regime"]:
        return pd.Series(True, index=index)
    source = benchmark_data
    if source is None:
        source = _find_asset_data(asset_data, "SPY")
        if source is None:
            source = _find_asset_data(asset_data, "QQQ")
    if source is None or "Close" not in source:
        raise ValueError(
            "ma_slope_trend icin market regime aktifken SPY/QQQ "
            "benchmark verisi gerekli."
        )
    daily = _daily_ohlcv_from_frame(source)
    close = daily["Close"].dropna()
    sma = close.rolling(
        params["mast_market_regime_sma_days"],
        min_periods=params["mast_market_regime_sma_days"],
    ).mean()
    regime = (close > sma).fillna(False)
    return safe_bool_series(regime, index)


def _ma_slope_selection(asset_data, params, benchmark_data=None):
    features = _ma_slope_daily_features(asset_data, params)
    all_dates = pd.DatetimeIndex(
        sorted(set().union(*(frame.index for frame in features.values())))
    )
    if all_dates.empty:
        raise ValueError("ma_slope_trend icin tarih indeksi bos.")
    regime = _ma_slope_regime(asset_data, benchmark_data, params, all_dates)
    rebalance = _rebalance_mask(all_dates, params["mast_rebalance_frequency"])
    selections = {}
    for date in all_dates:
        if not bool(rebalance.loc[date]):
            continue
        if not bool(regime.loc[date]):
            selections[date] = []
            continue
        rows = []
        for symbol, frame in features.items():
            if date not in frame.index:
                continue
            row = frame.loc[date]
            if bool(row.get("MAST_Eligible", False)) and pd.notna(
                row.get("MAST_Score")
            ):
                rows.append((symbol, float(row["MAST_Score"])))
        rows.sort(key=lambda item: item[1], reverse=True)
        selections[date] = rows[: params["mast_top_n"]]
    return features, selections, regime, rebalance


def _apply_ma_slope_trend_top_signals(asset_data, params, benchmark_data=None):
    features, selections, regime, _ = _ma_slope_selection(
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
        for date, selected_rows in selections.items():
            date_mask = normalized_dates == date
            if not date_mask.any():
                continue
            last_bar = frame.index[date_mask][-1]
            selected = {row_symbol for row_symbol, _ in selected_rows}
            signal_dates.loc[last_bar] = symbol in selected
            feature = features.get(symbol)
            feature_exit = bool(
                feature is not None
                and date in feature.index
                and feature.loc[date, "MAST_Exit"]
            )
            exit_dates.loc[last_bar] = (
                feature_exit or symbol not in selected or not bool(regime.loc[date])
            )
        frame["ENTRY_SIGNAL"] = signal_dates.fillna(False).astype(bool)
        frame["EXIT_SIGNAL"] = exit_dates.fillna(False).astype(bool)
        frame["Confirmed_Entry_Signal"] = frame["ENTRY_SIGNAL"]
        frame["Trigger"] = frame.get("MAST_Eligible", frame["ENTRY_SIGNAL"]).fillna(False).astype(bool)
        updated[symbol] = frame
    return updated


def _run_ma_slope_trend_backtest(asset_data, params, benchmark_data=None):
    mast_params = _normalize_ma_slope_trend_params(params)
    features, selections, regime, _ = _ma_slope_selection(
        asset_data,
        mast_params,
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
    pending_entries = []
    pending_exits = set()
    current_target = set()
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
        proceeds = position["shares"] * price
        cash += proceeds
        pnl = proceeds - position["entry_notional"]
        r_multiple = (
            pnl / position["entry_notional"]
            if position["entry_notional"] > 0
            else np.nan
        )
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
        open_equity = _equity_at(date, "Open")
        for symbol in sorted(pending_exits):
            if symbol not in positions:
                continue
            frame = features.get(symbol)
            if frame is None or date not in frame.index:
                continue
            price = frame.loc[date, "Open"]
            if pd.notna(price) and price > 0:
                _close(symbol, date, float(price), "ma_slope_exit")
        pending_exits = set()

        target_weight = 1 / max(mast_params["mast_top_n"], 1)
        target_notional = open_equity * target_weight
        for symbol, _ in pending_entries:
            if len(positions) >= mast_params["mast_top_n"]:
                break
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
                "entry_notional": shares * float(price),
                "shares": shares,
                "bars_held": 0,
                "last_price": float(price),
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

        if date in selections:
            current_target = {symbol for symbol, _ in selections[date]}
        pending_exits = {
            symbol
            for symbol, frame in features.items()
            if symbol in positions
            and date in frame.index
            and (
                bool(frame.loc[date, "MAST_Exit"])
                or symbol not in current_target
                or not bool(regime.loc[date])
            )
        }
        held_after_exits = set(positions) - pending_exits
        capacity = max(mast_params["mast_top_n"] - len(held_after_exits), 0)
        if capacity and date in selections and bool(regime.loc[date]):
            pending_entries = [
                item
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

    metrics = _mast_metrics_from_equity(
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


__all__ = [
    "_apply_ma_slope_trend_top_signals",
    "_normalize_ma_slope_trend_params",
    "_run_ma_slope_trend_backtest",
    "generate_ma_slope_trend_signals",
]
