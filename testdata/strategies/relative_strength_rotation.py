import numpy as np
import pandas as pd

try:
    from ..diagnostics import _zero_trade_metrics
    from .cost_model import calculate_max_shares, calculate_transaction_costs
    from .liquidity_filter import evaluate_entry_liquidity, liquidity_skip_summary
except ImportError:
    from diagnostics import _zero_trade_metrics
    from strategies.cost_model import calculate_max_shares, calculate_transaction_costs
    from strategies.liquidity_filter import evaluate_entry_liquidity, liquidity_skip_summary


STRATEGY_CACHE_VERSION = "pure_mr_rsi10_bblower_sma50_riskoff_atr2_stop2_timestop10_calendar_v6"


def safe_bool_series(series, index):
    aligned = series.reindex(index)
    aligned = aligned.infer_objects(copy=False)
    aligned = aligned.astype("boolean")
    return aligned.ffill().fillna(False).astype(bool)


def _normalize_rs_rebalance_frequency(value):
    frequency = str(value).lower()
    if frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError(
            "rs_rebalance_frequency 'daily', 'weekly' veya 'monthly' olmali."
        )
    return frequency


def _normalize_rs_score_mode(value):
    mode = str(value).lower()
    if mode not in {"total_return", "return_over_vol"}:
        raise ValueError(
            "rs_score_mode 'total_return' veya 'return_over_vol' olmali."
        )
    return mode


def _normalize_relative_strength_params(config):
    config = dict(config or {})
    params = {
        "rs_lookback_days": int(config.get("rs_lookback_days", 126)),
        "rs_top_n": int(config.get("rs_top_n", 20)),
        "rs_rebalance_frequency": _normalize_rs_rebalance_frequency(
            config.get("rs_rebalance_frequency", "weekly")
        ),
        "rs_score_mode": _normalize_rs_score_mode(
            config.get("rs_score_mode", "return_over_vol")
        ),
        "rs_vol_lookback_days": int(config.get("rs_vol_lookback_days", 60)),
        "rs_trend_sma_days": int(config.get("rs_trend_sma_days", 200)),
        "rs_use_market_regime": bool(config.get("rs_use_market_regime", True)),
        "rs_market_regime_sma_days": int(
            config.get("rs_market_regime_sma_days", 200)
        ),
        "rs_min_price": float(config.get("rs_min_price", 10)),
        "rs_max_atr_pct": float(config.get("rs_max_atr_pct", 12)),
        "min_dollar_volume": float(config.get("min_dollar_volume", 0.0) or 0.0),
    }
    for key in [
        "rs_lookback_days",
        "rs_top_n",
        "rs_vol_lookback_days",
        "rs_trend_sma_days",
        "rs_market_regime_sma_days",
    ]:
        if params[key] < 1:
            raise ValueError(f"{key} en az 1 olmali.")
    if params["rs_min_price"] < 0:
        raise ValueError("rs_min_price negatif olamaz.")
    if params["rs_max_atr_pct"] <= 0:
        raise ValueError("rs_max_atr_pct 0'dan buyuk olmali.")
    if params["min_dollar_volume"] < 0:
        raise ValueError("min_dollar_volume negatif olamaz.")
    for key in ["commission_per_share", "slippage_bps", "min_commission"]:
        if key in config:
            params[key] = config[key]
    return params


def _relative_strength_cache_config(params):
    params = _normalize_relative_strength_params(params)
    return {
        "strategy_cache_version": STRATEGY_CACHE_VERSION,
        "strategy_name": "relative_strength_rotation",
        "hurst_filter_mode": "off",
        "hurst_threshold": 0.5,
        "use_market_calendar": True,
        "include_partial_session_bars": False,
        "rsi_length": 3,
        "rsi_entry_threshold": 10.0,
        "bollinger_length": 20,
        "bollinger_std": 2.0,
        "bb_entry_tolerance_atr": 0.0,
        "trend_sma_length": 50,
        "setup_lookback": 3,
        "use_quality_filter": True,
        "min_price_filter": 10.0,
        "min_atr_pct_filter": 0.2,
        "max_atr_pct_filter": 12.0,
        "min_avg_dollar_volume": 20_000_000.0,
        "avg_volume_lookback": 20,
        "exit_sma_length": 20,
        "take_profit_atr_multiplier": 2.0,
        "disaster_stop_atr_multiplier": 2.0,
        "exit_mode": "sma_or_target",
        "confirmation_mode": "off",
        "confirmation_lookback": 1,
        "confirmation_min_rebound_atr": 0.0,
        **params,
        "use_entry_context_filter": False,
        "max_entry_gap_pct": None,
        "max_entry_bb_distance_atr": None,
        "max_entry_signal_range_pct": None,
        "min_entry_close_position_in_range": None,
        "max_entry_close_position_in_range": None,
    }


def _daily_ohlcv_from_frame(df):
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("relative_strength_rotation icin DatetimeIndex gerekli.")
    daily_source = df.attrs.get("daily_ohlcv")
    if isinstance(daily_source, pd.DataFrame) and not daily_source.empty:
        return daily_source.copy()
    if df.attrs.get("is_daily", False) or df.attrs.get("daily_timeframe") == "1D":
        return df.copy()
    aggregations = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
    }
    if "Volume" in df:
        aggregations["Volume"] = "sum"
    daily = df.resample("1D").agg(aggregations)
    return daily.dropna(subset=["Open", "High", "Low", "Close"])


def _atr_from_ohlc(daily, length=20):
    previous_close = daily["Close"].shift(1)
    true_range = pd.concat(
        [
            daily["High"] - daily["Low"],
            (daily["High"] - previous_close).abs(),
            (daily["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(int(length), min_periods=int(length)).mean()


def generate_relative_strength_rotation_signals(df, config):
    params = _normalize_relative_strength_params(config)
    frame = df.copy()
    if "Risk_Off" not in frame:
        frame["Risk_Off"] = False
    if "Regime_Ready" not in frame:
        frame["Regime_Ready"] = True

    daily = _daily_ohlcv_from_frame(frame)
    if daily.empty:
        raise ValueError("relative_strength_rotation icin gunluk veri uretilemedi.")

    atr = _atr_from_ohlc(daily, 20)
    atr_pct = 100 * atr / daily["Close"].replace(0, np.nan)
    trend_sma = daily["Close"].rolling(
        params["rs_trend_sma_days"],
        min_periods=params["rs_trend_sma_days"],
    ).mean()
    total_return = daily["Close"] / daily["Close"].shift(
        params["rs_lookback_days"]
    ) - 1
    daily_return = daily["Close"].pct_change(fill_method=None)
    annualized_vol = daily_return.rolling(
        params["rs_vol_lookback_days"],
        min_periods=params["rs_vol_lookback_days"],
    ).std() * np.sqrt(252)
    if params["rs_score_mode"] == "total_return":
        score = total_return
    else:
        score = total_return / annualized_vol.replace(0, np.nan)

    eligible = (
        (daily["Close"] >= params["rs_min_price"])
        & (atr_pct <= params["rs_max_atr_pct"])
        & (daily["Close"] > trend_sma)
        & score.replace([np.inf, -np.inf], np.nan).notna()
    )
    daily_features = pd.DataFrame(index=daily.index)
    daily_features["RS_Total_Return"] = total_return
    daily_features["RS_Annualized_Vol"] = annualized_vol
    daily_features["RS_Score"] = score.replace([np.inf, -np.inf], np.nan)
    daily_features["RS_Trend_SMA"] = trend_sma
    daily_features["ATR_Pct"] = atr_pct
    daily_features["RS_Universe_Filter_OK"] = eligible.fillna(False).astype(bool)

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
    frame["Macro_Uptrend"] = frame["RS_Universe_Filter_OK"].fillna(False).astype(bool)
    frame["Macro_Filter_OK"] = frame["Macro_Uptrend"]
    frame["Quality_Filter_OK"] = frame["RS_Universe_Filter_OK"].fillna(False).astype(bool)
    frame["Trigger"] = frame["RS_Universe_Filter_OK"].fillna(False).astype(bool)
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
    frame["Confirmation_Mode"] = "relative_strength_rotation"
    frame["Momentum_Factor_Raw"] = frame["RS_Total_Return"]
    frame["Momentum_Factor"] = frame["RS_Score"]
    frame["ATR_Take_Profit_Distance"] = frame["SuperTrend_ATR_22"]
    frame["Disaster_Stop"] = frame["Close"] - frame["SuperTrend_ATR_22"]
    frame["SMA20_Reversion_Exit"] = ~frame["RS_Universe_Filter_OK"].fillna(False).astype(bool)
    frame["Take_Profit"] = False
    frame["ENTRY_SIGNAL"] = False
    frame["EXIT_SIGNAL"] = False
    frame["Indicators_Ready"] = (
        frame["RS_Universe_Filter_OK"].notna()
        & frame["RS_Score"].replace([np.inf, -np.inf], np.nan).notna()
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
    frame.attrs.update(_relative_strength_cache_config(params))
    return frame


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


def _rs_rebalance_mask(index, frequency):
    frequency = _normalize_rs_rebalance_frequency(frequency)
    if frequency == "daily":
        return pd.Series(True, index=index)
    period_index = index
    if getattr(period_index, "tz", None) is not None:
        period_index = period_index.tz_convert(None)
    if frequency == "weekly":
        periods = period_index.to_period("W-FRI")
    else:
        periods = period_index.to_period("M")
    values = np.ones(len(index), dtype=bool)
    if len(index) > 1:
        values[:-1] = periods[:-1] != periods[1:]
    return pd.Series(values, index=index)


def _relative_strength_daily_features(asset_data, params):
    features = {}
    for symbol, data in asset_data.items():
        daily = _daily_ohlcv_from_frame(data)
        if daily.empty:
            continue
        atr = _atr_from_ohlc(daily, 20)
        atr_pct = 100 * atr / daily["Close"].replace(0, np.nan)
        trend_sma = daily["Close"].rolling(
            params["rs_trend_sma_days"],
            min_periods=params["rs_trend_sma_days"],
        ).mean()
        total_return = daily["Close"] / daily["Close"].shift(
            params["rs_lookback_days"]
        ) - 1
        annualized_vol = daily["Close"].pct_change(fill_method=None).rolling(
            params["rs_vol_lookback_days"],
            min_periods=params["rs_vol_lookback_days"],
        ).std() * np.sqrt(252)
        if params["rs_score_mode"] == "total_return":
            score = total_return
        else:
            score = total_return / annualized_vol.replace(0, np.nan)
        eligible = (
            (daily["Close"] >= params["rs_min_price"])
            & (atr_pct <= params["rs_max_atr_pct"])
            & (daily["Close"] > trend_sma)
            & score.replace([np.inf, -np.inf], np.nan).notna()
        )
        features[symbol] = pd.DataFrame(
            {
                "Open": daily["Open"],
                "Close": daily["Close"],
                "Avg_Dollar_Volume": daily["Close"] * daily.get("Volume", pd.Series(index=daily.index, dtype=float)),
                "Signal_Avg_Dollar_Volume": (
                    daily["Close"] * daily.get("Volume", pd.Series(index=daily.index, dtype=float))
                ).shift(1),
                "ATR_Pct": atr_pct,
                "RS_Trend_SMA": trend_sma,
                "RS_Score": score.replace([np.inf, -np.inf], np.nan),
                "RS_Eligible": eligible.fillna(False).astype(bool),
            },
            index=daily.index,
        )
    if not features:
        raise ValueError("relative_strength_rotation icin kullanilabilir varlik yok.")
    return features


def _relative_strength_regime(asset_data, benchmark_data, params, index):
    if not params["rs_use_market_regime"]:
        return pd.Series(True, index=index)
    source = benchmark_data
    if source is None:
        source = _find_asset_data(asset_data, "SPY")
        if source is None:
            source = _find_asset_data(asset_data, "QQQ")
    if source is None or "Close" not in source:
        raise ValueError(
            "relative_strength_rotation icin market regime aktifken SPY/QQQ "
            "benchmark verisi gerekli."
        )
    daily = _daily_ohlcv_from_frame(source)
    close = daily["Close"].dropna()
    sma = close.rolling(
        params["rs_market_regime_sma_days"],
        min_periods=params["rs_market_regime_sma_days"],
    ).mean()
    regime = (close > sma).fillna(False)
    return safe_bool_series(regime, index)


def _relative_strength_selection(asset_data, params, benchmark_data=None):
    is_v2 = (params.get("strategy_identifier") == "rs_m126_top30_sma100_no_regime_v2_clean_stock")
    blocked_etfs = {
        "DIA", "IBB", "IWM", "QQQ", "RSP", "SMH", "SOXX", "SPY", "VTI", "XBI",
        "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV",
        "XLY", "XRT"
    }
    if is_v2:
        for symbol in asset_data:
            if symbol in blocked_etfs:
                raise ValueError(f"Blocked ETF {symbol} found in candidate universe for V2 clean stock!")

    features = _relative_strength_daily_features(asset_data, params)
    if is_v2:
        for symbol in features:
            if symbol in blocked_etfs:
                raise ValueError(f"Blocked ETF {symbol} found in features universe for V2 clean stock!")

    all_dates = pd.DatetimeIndex(
        sorted(set().union(*(frame.index for frame in features.values())))
    )
    if all_dates.empty:
        raise ValueError("relative_strength_rotation icin tarih indeksi bos.")
    regime = _relative_strength_regime(asset_data, benchmark_data, params, all_dates)
    rebalance = _rs_rebalance_mask(all_dates, params["rs_rebalance_frequency"])
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
            if bool(row.get("RS_Eligible", False)) and pd.notna(row.get("RS_Score")):
                rows.append((symbol, float(row["RS_Score"])))
        rows.sort(key=lambda item: item[1], reverse=True)
        selections[date] = {symbol for symbol, _ in rows[: params["rs_top_n"]]}
        if is_v2:
            for s in selections[date]:
                if s in blocked_etfs:
                    raise ValueError(f"Blocked ETF {s} found in V2 selection on {date}!")
    return features, selections, regime, rebalance


def _apply_relative_strength_rotation_top_signals(asset_data, params, benchmark_data=None):
    features, selections, regime, rebalance = _relative_strength_selection(
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
                and features[symbol].loc[date, "RS_Eligible"]
            )
            exit_dates.loc[last_bar] = (not in_top) or (not trend_ok) or (not bool(regime.loc[date]))
        frame["ENTRY_SIGNAL"] = signal_dates.fillna(False).astype(bool)
        frame["EXIT_SIGNAL"] = exit_dates.fillna(False).astype(bool)
        frame["Confirmed_Entry_Signal"] = frame["ENTRY_SIGNAL"]
        frame["Trigger"] = frame.get("RS_Universe_Filter_OK", frame["ENTRY_SIGNAL"]).fillna(False).astype(bool)
        updated[symbol] = frame
    return updated


def _relative_strength_metrics_from_equity(
    equity_curve,
    trade_log,
    initial_cash=10000.0,
    max_open_observed=0,
    max_heat_observed=0.0,
    position_count_curve=None,
):
    if not equity_curve:
        return _zero_trade_metrics()
    equity = pd.Series(
        [value for _, value in equity_curve],
        index=[timestamp for timestamp, _ in equity_curve],
        dtype=float,
    )
    total_return = (equity.iloc[-1] / initial_cash - 1) * 100
    max_drawdown = ((equity - equity.cummax()) / equity.cummax()).min() * 100
    daily_returns = equity.resample("1D").last().pct_change(fill_method=None).dropna()
    volatility = daily_returns.std()
    sharpe = (
        daily_returns.mean() / volatility * np.sqrt(252)
        if pd.notna(volatility) and volatility > 0
        else 0.0
    )
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    years = days / 365.25
    cagr = (equity.iloc[-1] / initial_cash) ** (1 / years) - 1
    trades = pd.DataFrame(trade_log)
    pnl = (
        pd.to_numeric(trades["Net PnL"], errors="coerce").dropna()
        if not trades.empty and "Net PnL" in trades
        else pd.Series(dtype=float)
    )
    r_multiples = (
        pd.to_numeric(trades["R Multiple"], errors="coerce").dropna()
        if not trades.empty and "R Multiple" in trades
        else pd.Series(dtype=float)
    )
    winners = pnl[pnl > 0]
    losers = pnl[pnl < 0]
    trade_count = len(pnl)
    profit_factor = (
        winners.sum() / abs(losers.sum())
        if len(losers) and abs(losers.sum()) > 0
        else float("inf") if trade_count else 0.0
    )
    holding_bars = (
        pd.to_numeric(trades["Bars Held"], errors="coerce").dropna()
        if not trades.empty and "Bars Held" in trades
        else pd.Series(dtype=float)
    )
    position_counts = pd.Series(position_count_curve or [], dtype=float)
    avg_positions_held = (
        float(position_counts.mean()) if len(position_counts) else 0.0
    )
    turnover_estimate = (
        float(trade_count) / avg_positions_held
        if avg_positions_held > 0
        else 0.0
    )
    return _zero_trade_metrics(
        **{
            "Baslangic Kasa": round(initial_cash, 2),
            "Bitis Kasa": round(float(equity.iloc[-1]), 2),
            "Net Getiri (%)": round(float(total_return), 2),
            "CAGR (%)": round(float(cagr * 100), 2),
            "Max Drawdown (%)": round(float(max_drawdown), 2),
            "Sharpe Ratio": round(float(sharpe), 2),
            "Islem Sayisi": int(trade_count),
            "Trade Count": int(trade_count),
            "Win Rate (%)": round(len(winners) / trade_count * 100, 1)
            if trade_count
            else 0.0,
            "Profit Factor": round(float(profit_factor), 2)
            if np.isfinite(profit_factor)
            else float("inf"),
            "Avg R": round(float(r_multiples.mean()), 4) if len(r_multiples) else 0.0,
            "Median R": round(float(r_multiples.median()), 4)
            if len(r_multiples)
            else 0.0,
            "Best R": round(float(r_multiples.max()), 4) if len(r_multiples) else 0.0,
            "Worst R": round(float(r_multiples.min()), 4) if len(r_multiples) else 0.0,
            "Avg Winner R": round(float(r_multiples[r_multiples > 0].mean()), 4)
            if len(r_multiples[r_multiples > 0])
            else 0.0,
            "Avg Loser R": round(float(r_multiples[r_multiples < 0].mean()), 4)
            if len(r_multiples[r_multiples < 0])
            else 0.0,
            "Expectancy R": round(float(r_multiples.mean()), 4)
            if len(r_multiples)
            else 0.0,
            "Forced Final Exit Count": int(
                trades["Exit Reason"].eq("forced_final_exit").sum()
            )
            if not trades.empty and "Exit Reason" in trades
            else 0,
            "Max Acik Pozisyon": int(max_open_observed),
            "Max Portfolio Heat (%)": round(float(max_heat_observed * 100), 2),
            "Avg Holding Bars": round(float(holding_bars.mean()), 2)
            if len(holding_bars)
            else 0.0,
            "Median Holding Bars": round(float(holding_bars.median()), 2)
            if len(holding_bars)
            else 0.0,
            "Turnover Estimate": round(turnover_estimate, 4),
            "Avg Positions Held": round(avg_positions_held, 2),
            "Max Positions Held": int(max_open_observed),
        }
    )


def _run_relative_strength_rotation_backtest(asset_data, params, benchmark_data=None):
    rs_params = _normalize_relative_strength_params(params)
    features, selections, regime, _ = _relative_strength_selection(
        asset_data,
        rs_params,
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
    pending_target_signal_date = None
    liquidity_skips = []
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
        gross_proceeds = position["shares"] * price
        costs = calculate_transaction_costs(gross_proceeds, position["shares"], rs_params)
        exit_cost = costs["total_cost"]
        proceeds = gross_proceeds - exit_cost
        cash += proceeds
        pnl = proceeds - (position["entry_notional"] + position["entry_cost"])
        r_multiple = pnl / position["entry_notional"] if position["entry_notional"] > 0 else np.nan
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
                signal_row = (
                    frame.loc[signal_date]
                    if signal_date is not None and signal_date in frame.index
                    else None
                )
                liquidity_result = evaluate_entry_liquidity(
                    symbol,
                    signal_date,
                    signal_row,
                    rs_params["min_dollar_volume"],
                )
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
        max_heat_observed = max(
            max_heat_observed,
            exposure / close_equity if close_equity > 0 else 0.0,
        )
        position_count_curve.append(len(positions))
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
        equity_curve,
        trade_log,
        initial_cash=10000.0,
        max_open_observed=max_open_observed,
        max_heat_observed=max_heat_observed,
        position_count_curve=position_count_curve,
    )
    metrics.update(liquidity_skip_summary(liquidity_skips, rs_params["min_dollar_volume"]))
    metrics["_trade_log"] = trade_log
    metrics["_equity_curve"] = equity_curve
    metrics["_liquidity_skipped_entries"] = liquidity_skips
    return metrics


class _SimpleBacktestResult:
    def __init__(self, trade_log=None, equity_curve=None, initial_cash=10000.0):
        self.trade_log = list(trade_log or [])
        self.equity_curve = list(equity_curve or [])
        self.initial_cash = float(initial_cash)


__all__ = [
    "_SimpleBacktestResult",
    "_apply_relative_strength_rotation_top_signals",
    "_normalize_relative_strength_params",
    "_normalize_rs_rebalance_frequency",
    "_normalize_rs_score_mode",
    "_run_relative_strength_rotation_backtest",
    "generate_relative_strength_rotation_signals",
    "safe_bool_series",
]
