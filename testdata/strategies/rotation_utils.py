try:
    from .relative_strength_rotation import (
        STRATEGY_CACHE_VERSION,
        _atr_from_ohlc,
        _daily_ohlcv_from_frame,
        _normalize_relative_strength_params,
        _normalize_rs_rebalance_frequency,
        _relative_strength_metrics_from_equity,
        _rs_rebalance_mask,
        safe_bool_series,
    )
except ImportError:
    from strategies.relative_strength_rotation import (
        STRATEGY_CACHE_VERSION,
        _atr_from_ohlc,
        _daily_ohlcv_from_frame,
        _normalize_relative_strength_params,
        _normalize_rs_rebalance_frequency,
        _relative_strength_metrics_from_equity,
        _rs_rebalance_mask,
        safe_bool_series,
    )


def _normalize_hurst_filter_mode(value):
    mode = str(value).lower()
    if mode not in {"off", "gt", "lt"}:
        raise ValueError("hurst_filter_mode 'off', 'gt' veya 'lt' olmali.")
    return mode


def _normalize_confirmation_mode(value):
    mode = str(value).lower()
    if mode not in {"off", "close_above_signal_high"}:
        raise ValueError(
            "confirmation_mode 'off' veya 'close_above_signal_high' olmali."
        )
    return mode


def _normalize_entry_context_filter_params(
    use_entry_context_filter=False,
    max_entry_gap_pct=None,
    max_entry_bb_distance_atr=None,
    max_entry_signal_range_pct=None,
    min_entry_close_position_in_range=None,
    max_entry_close_position_in_range=None,
):
    params = {
        "use_entry_context_filter": bool(use_entry_context_filter),
        "max_entry_gap_pct": max_entry_gap_pct,
        "max_entry_bb_distance_atr": max_entry_bb_distance_atr,
        "max_entry_signal_range_pct": max_entry_signal_range_pct,
        "min_entry_close_position_in_range": min_entry_close_position_in_range,
        "max_entry_close_position_in_range": max_entry_close_position_in_range,
    }
    for key in [
        "max_entry_gap_pct",
        "max_entry_bb_distance_atr",
        "max_entry_signal_range_pct",
    ]:
        if params[key] is not None:
            params[key] = float(params[key])
            if params[key] < 0:
                raise ValueError(f"{key} negatif olamaz.")
    for key in [
        "min_entry_close_position_in_range",
        "max_entry_close_position_in_range",
    ]:
        if params[key] is not None:
            params[key] = float(params[key])
            if params[key] < 0 or params[key] > 1:
                raise ValueError(f"{key} 0 ile 1 arasinda olmali.")
    min_position = params["min_entry_close_position_in_range"]
    max_position = params["max_entry_close_position_in_range"]
    if (
        min_position is not None
        and max_position is not None
        and min_position > max_position
    ):
        raise ValueError(
            "min_entry_close_position_in_range max_entry_close_position_in_range'dan buyuk olamaz."
        )
    return params


def _strategy_cache_config(
    hurst_filter_mode,
    hurst_threshold,
    strategy_name="current_pullback_mean_reversion",
    use_market_calendar=True,
    include_partial_session_bars=False,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    bb_entry_tolerance_atr=0.0,
    use_quality_filter=True,
    min_price_filter=10.0,
    min_atr_pct_filter=0.2,
    max_atr_pct_filter=12.0,
    min_avg_dollar_volume=20_000_000,
    avg_volume_lookback=20,
    trend_sma_length=50,
    setup_lookback=3,
    exit_sma_length=20,
    take_profit_atr_multiplier=2.0,
    disaster_stop_atr_multiplier=2.0,
    exit_mode="sma_or_target",
    confirmation_mode="off",
    confirmation_lookback=1,
    confirmation_min_rebound_atr=0.0,
    use_entry_context_filter=False,
    max_entry_gap_pct=None,
    max_entry_bb_distance_atr=None,
    max_entry_signal_range_pct=None,
    min_entry_close_position_in_range=None,
    max_entry_close_position_in_range=None,
    dm_lookback_days=126,
    dm_top_n=20,
    dm_rebalance_frequency="monthly",
    dm_absolute_momentum_threshold=0.0,
    dm_trend_sma_days=200,
    dm_use_market_regime=True,
    dm_market_regime_sma_days=200,
    dm_min_price=10,
    dm_max_atr_pct=12,
    donchian_entry_lookback_days=20,
    donchian_exit_lookback_days=10,
    donchian_top_n=20,
    donchian_rebalance_frequency="daily",
    donchian_trend_sma_days=200,
    donchian_use_market_regime=True,
    donchian_market_regime_sma_days=200,
    donchian_min_price=10,
    donchian_max_atr_pct=12,
    csm_lookback_days=126,
    csm_top_n=20,
    csm_rebalance_frequency="monthly",
    csm_trend_sma_days=200,
    csm_use_market_regime=True,
    csm_market_regime_sma_days=200,
    csm_min_price=10,
    csm_max_atr_pct=12,
    rs_lookback_days=126,
    rs_top_n=20,
    rs_rebalance_frequency="weekly",
    rs_score_mode="return_over_vol",
    rs_vol_lookback_days=60,
    rs_trend_sma_days=200,
    rs_use_market_regime=True,
    rs_market_regime_sma_days=200,
    rs_min_price=10,
    rs_max_atr_pct=12,
):
    context_params = _normalize_entry_context_filter_params(
        use_entry_context_filter=use_entry_context_filter,
        max_entry_gap_pct=max_entry_gap_pct,
        max_entry_bb_distance_atr=max_entry_bb_distance_atr,
        max_entry_signal_range_pct=max_entry_signal_range_pct,
        min_entry_close_position_in_range=min_entry_close_position_in_range,
        max_entry_close_position_in_range=max_entry_close_position_in_range,
    )
    rs_params = _normalize_relative_strength_params(
        {
            "rs_lookback_days": rs_lookback_days,
            "rs_top_n": rs_top_n,
            "rs_rebalance_frequency": rs_rebalance_frequency,
            "rs_score_mode": rs_score_mode,
            "rs_vol_lookback_days": rs_vol_lookback_days,
            "rs_trend_sma_days": rs_trend_sma_days,
            "rs_use_market_regime": rs_use_market_regime,
            "rs_market_regime_sma_days": rs_market_regime_sma_days,
            "rs_min_price": rs_min_price,
            "rs_max_atr_pct": rs_max_atr_pct,
        }
    )
    return {
        "strategy_cache_version": STRATEGY_CACHE_VERSION,
        "strategy_name": str(strategy_name),
        "hurst_filter_mode": _normalize_hurst_filter_mode(hurst_filter_mode),
        "hurst_threshold": float(hurst_threshold),
        "use_market_calendar": bool(use_market_calendar),
        "include_partial_session_bars": bool(include_partial_session_bars),
        "rsi_length": int(rsi_length),
        "rsi_entry_threshold": float(rsi_entry_threshold),
        "bollinger_length": int(bollinger_length),
        "bollinger_std": float(bollinger_std),
        "bb_entry_tolerance_atr": float(bb_entry_tolerance_atr),
        "trend_sma_length": int(trend_sma_length),
        "setup_lookback": int(setup_lookback),
        "use_quality_filter": bool(use_quality_filter),
        "min_price_filter": float(min_price_filter),
        "min_atr_pct_filter": float(min_atr_pct_filter),
        "max_atr_pct_filter": float(max_atr_pct_filter),
        "min_avg_dollar_volume": float(min_avg_dollar_volume),
        "avg_volume_lookback": int(avg_volume_lookback),
        "exit_sma_length": int(exit_sma_length),
        "take_profit_atr_multiplier": float(take_profit_atr_multiplier),
        "disaster_stop_atr_multiplier": float(disaster_stop_atr_multiplier),
        "exit_mode": str(exit_mode),
        "confirmation_mode": _normalize_confirmation_mode(confirmation_mode),
        "confirmation_lookback": int(confirmation_lookback),
        "confirmation_min_rebound_atr": float(confirmation_min_rebound_atr),
        **rs_params,
        **context_params,
    }


__all__ = [
    "STRATEGY_CACHE_VERSION",
    "_atr_from_ohlc",
    "_daily_ohlcv_from_frame",
    "_normalize_rs_rebalance_frequency",
    "_relative_strength_metrics_from_equity",
    "_rs_rebalance_mask",
    "safe_bool_series",
    "_strategy_cache_config",
]
