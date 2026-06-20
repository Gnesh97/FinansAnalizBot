import numpy as np
import pandas as pd


HURST_LAG_MAX = 20
HURST_WINDOW = 100
SUPERTREND_ATR_LENGTH = 22
SUPERTREND_MULTIPLIER = 2.2


def calculate_atr(df, length=14):
    previous_close = df["Close"].shift(1)
    true_range = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - previous_close).abs(),
            (df["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def calculate_hurst_exponent(df, lag_max=HURST_LAG_MAX):
    if lag_max < 3:
        raise ValueError("lag_max en az 3 olmali.")

    log_price = np.log(df["Close"].where(df["Close"] > 0))
    lags = np.arange(2, lag_max + 1, dtype=float)
    rolling_variances = pd.concat(
        [
            log_price.diff(int(lag))
            .rolling(HURST_WINDOW, min_periods=HURST_WINDOW)
            .var()
            .rename(int(lag))
            for lag in lags
        ],
        axis=1,
    )

    log_lags = np.log(lags)
    centered_lags = log_lags - log_lags.mean()
    denominator = np.square(centered_lags).sum()
    log_variances = np.log(rolling_variances.where(rolling_variances > 0))
    row_means = log_variances.mean(axis=1)
    slope = log_variances.sub(row_means, axis=0).mul(
        centered_lags,
        axis=1,
    ).sum(axis=1) / denominator

    valid_lag_count = log_variances.notna().sum(axis=1)
    hurst = (slope / 2).where(valid_lag_count == len(lags))
    return hurst.clip(lower=0, upper=1)


def calculate_supertrend(
    df,
    atr_length=SUPERTREND_ATR_LENGTH,
    multiplier=SUPERTREND_MULTIPLIER,
):
    atr = calculate_atr(df, atr_length)
    midpoint = (df["High"] + df["Low"]) / 2
    basic_upper = midpoint + multiplier * atr
    basic_lower = midpoint - multiplier * atr

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    direction = pd.Series(0, index=df.index, dtype=int)
    supertrend = pd.Series(np.nan, index=df.index, dtype=float)

    for i in range(1, len(df)):
        if pd.isna(atr.iloc[i]):
            continue

        if pd.isna(final_upper.iloc[i - 1]):
            final_upper.iloc[i] = basic_upper.iloc[i]
        elif (
            basic_upper.iloc[i] < final_upper.iloc[i - 1]
            or df["Close"].iloc[i - 1] > final_upper.iloc[i - 1]
        ):
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]

        if pd.isna(final_lower.iloc[i - 1]):
            final_lower.iloc[i] = basic_lower.iloc[i]
        elif (
            basic_lower.iloc[i] > final_lower.iloc[i - 1]
            or df["Close"].iloc[i - 1] < final_lower.iloc[i - 1]
        ):
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

        previous_direction = direction.iloc[i - 1]
        if previous_direction == 0:
            previous_direction = 1

        if previous_direction > 0:
            direction.iloc[i] = (
                -1 if df["Close"].iloc[i] < final_lower.iloc[i] else 1
            )
        else:
            direction.iloc[i] = (
                1 if df["Close"].iloc[i] > final_upper.iloc[i] else -1
            )

        supertrend.iloc[i] = (
            final_lower.iloc[i]
            if direction.iloc[i] > 0
            else final_upper.iloc[i]
        )

    return supertrend, direction, final_lower, atr


def calculate_market_regime(benchmark_df):
    daily_close = benchmark_df["Close"].resample("1D").last().dropna()
    daily_sma_50 = daily_close.rolling(50, min_periods=50).mean()
    daily_sma_200 = daily_close.rolling(200, min_periods=200).mean()

    regime = pd.DataFrame(index=daily_close.index)
    regime["Benchmark_SMA50_Prev"] = daily_sma_50.shift(1)
    regime["Benchmark_SMA200_Prev"] = daily_sma_200.shift(1)
    regime["Regime_Ready"] = (
        regime["Benchmark_SMA50_Prev"].notna()
        & regime["Benchmark_SMA200_Prev"].notna()
    )
    regime["Risk_Off"] = (
        regime["Benchmark_SMA50_Prev"] < regime["Benchmark_SMA200_Prev"]
    ).where(regime["Regime_Ready"])
    return regime


def add_market_regime(df, benchmark_df):
    regime = calculate_market_regime(benchmark_df)
    session_dates = pd.Series(pd.DatetimeIndex(df.index.normalize()), index=df.index)
    unique_session_dates = pd.DatetimeIndex(session_dates.unique()).sort_values()
    lookup_index = regime.index.union(unique_session_dates)
    aligned_by_day = regime.reindex(lookup_index).ffill().reindex(unique_session_dates)

    result = df.copy()
    for column in regime.columns:
        result[column] = session_dates.map(aligned_by_day[column])
    return result


__all__ = [
    "HURST_LAG_MAX",
    "HURST_WINDOW",
    "SUPERTREND_ATR_LENGTH",
    "SUPERTREND_MULTIPLIER",
    "add_market_regime",
    "calculate_atr",
    "calculate_hurst_exponent",
    "calculate_market_regime",
    "calculate_supertrend",
]
