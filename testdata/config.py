DEFAULT_STRATEGY_NAME = "current_pullback_mean_reversion"
STRATEGY_CACHE_VERSION = "pure_mr_rsi10_bblower_sma50_riskoff_atr2_stop2_timestop10_calendar_v6"
INTRADAY_DATA_DIR = "testdata/hisseler/bars_130min"
DAILY_DATA_DIR = "testdata/hisseler/daily"
RAW_1MIN_DATA_DIR = "testdata/hisseler/raw_1min"
INTRADAY_TIMEFRAME = "130min"
DAILY_TIMEFRAME = "1D"
SHORT_TRADING_ENABLED = False

STRATEGY_FAMILY_MAP = {
    "current_pullback_mean_reversion": "mean_reversion / pullback_in_uptrend",
    "relative_strength_rotation": "relative_strength / momentum",
    "cross_sectional_momentum": "relative_strength / momentum",
    "dual_momentum": "relative_strength / momentum",
    "donchian_breakout": "trend_following / breakout",
    "ma_slope_trend": "trend_following / ma_slope",
}


def validate_strategy_name(strategy_name):
    strategy_name = DEFAULT_STRATEGY_NAME if strategy_name is None else str(strategy_name)
    if strategy_name not in STRATEGY_FAMILY_MAP:
        raise ValueError(f"Unknown strategy_name: {strategy_name}")
    return strategy_name


def get_strategy_family(strategy_name):
    return STRATEGY_FAMILY_MAP[validate_strategy_name(strategy_name)]


__all__ = [
    "DEFAULT_STRATEGY_NAME",
    "DAILY_DATA_DIR",
    "DAILY_TIMEFRAME",
    "INTRADAY_DATA_DIR",
    "INTRADAY_TIMEFRAME",
    "RAW_1MIN_DATA_DIR",
    "SHORT_TRADING_ENABLED",
    "STRATEGY_CACHE_VERSION",
    "STRATEGY_FAMILY_MAP",
    "get_strategy_family",
    "validate_strategy_name",
]
