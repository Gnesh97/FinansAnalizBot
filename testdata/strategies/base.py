try:
    from ..config import validate_strategy_name
    from .cross_sectional_momentum import generate_cross_sectional_momentum_signals
    from .donchian_breakout import generate_donchian_breakout_signals
    from .dual_momentum import generate_dual_momentum_signals
    from .ma_slope_trend import generate_ma_slope_trend_signals
    from .relative_strength_rotation import generate_relative_strength_rotation_signals
except ImportError:
    from config import validate_strategy_name
    from strategies.cross_sectional_momentum import generate_cross_sectional_momentum_signals
    from strategies.donchian_breakout import generate_donchian_breakout_signals
    from strategies.dual_momentum import generate_dual_momentum_signals
    from strategies.ma_slope_trend import generate_ma_slope_trend_signals
    from strategies.relative_strength_rotation import generate_relative_strength_rotation_signals


def generate_strategy_signals(df, config):
    config = dict(config or {})
    strategy_name = validate_strategy_name(config.get("strategy_name"))
    if strategy_name == "relative_strength_rotation":
        return generate_relative_strength_rotation_signals(df, config)
    if strategy_name == "cross_sectional_momentum":
        return generate_cross_sectional_momentum_signals(df, config)
    if strategy_name == "dual_momentum":
        return generate_dual_momentum_signals(df, config)
    if strategy_name == "donchian_breakout":
        return generate_donchian_breakout_signals(df, config)
    if strategy_name == "ma_slope_trend":
        return generate_ma_slope_trend_signals(df, config)
    raise NotImplementedError(f"strategy_name not implemented in modular strategies: {strategy_name}")


__all__ = ["generate_strategy_signals"]
