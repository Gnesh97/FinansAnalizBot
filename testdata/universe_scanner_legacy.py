import glob
import hashlib
import importlib
import math
import os
import shutil
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

mcal = None
if importlib.util.find_spec("pandas_market_calendars") is not None:
    mcal = importlib.import_module("pandas_market_calendars")

warnings.filterwarnings("ignore")

MARKET_TIMEZONE = "America/New_York"
RTH_OPEN = "09:30"
RTH_CLOSE = "16:00"
PRIMARY_BAR_MINUTES = 130

HURST_LAG_MAX = 20
HURST_WINDOW = 100
HURST_THRESHOLD = 0.5
SUPERTREND_ATR_LENGTH = 22
SUPERTREND_MULTIPLIER = 2.2
DISASTER_STOP_ATR_MULTIPLIER = 2.0
TAKE_PROFIT_ATR_MULTIPLIER = 2.0
TIME_STOP_BARS = 10
STRATEGY_CACHE_VERSION = "pure_mr_rsi10_bblower_sma50_riskoff_atr2_stop2_timestop10_calendar_v6"
DEFAULT_STRATEGY_NAME = "current_pullback_mean_reversion"

STRATEGY_FAMILY_MAP = {
    "current_pullback_mean_reversion": "mean_reversion / pullback_in_uptrend",
    "relative_strength_rotation": "relative_strength / momentum",
    "cross_sectional_momentum": "relative_strength / momentum",
    "dual_momentum": "relative_strength / momentum",
    "donchian_breakout": "trend_following / breakout",
    "ma_slope_trend": "trend_following",
}

CURRENT_PULLBACK_SIGNAL_CONFIG_KEYS = {
    "hurst_filter_mode",
    "hurst_threshold",
    "rsi_length",
    "rsi_entry_threshold",
    "bollinger_length",
    "bollinger_std",
    "bb_entry_tolerance_atr",
    "trend_sma_length",
    "setup_lookback",
    "use_quality_filter",
    "min_price_filter",
    "min_atr_pct_filter",
    "max_atr_pct_filter",
    "min_avg_dollar_volume",
    "avg_volume_lookback",
    "exit_sma_length",
    "take_profit_atr_multiplier",
    "disaster_stop_atr_multiplier",
    "exit_mode",
    "confirmation_mode",
    "confirmation_lookback",
    "confirmation_min_rebound_atr",
    "use_entry_context_filter",
    "max_entry_gap_pct",
    "max_entry_bb_distance_atr",
    "max_entry_signal_range_pct",
    "min_entry_close_position_in_range",
    "max_entry_close_position_in_range",
}

RELATIVE_STRENGTH_SIGNAL_CONFIG_KEYS = {
    "rs_lookback_days",
    "rs_top_n",
    "rs_rebalance_frequency",
    "rs_score_mode",
    "rs_vol_lookback_days",
    "rs_trend_sma_days",
    "rs_use_market_regime",
    "rs_market_regime_sma_days",
    "rs_min_price",
    "rs_max_atr_pct",
}

OHLCV_AGG = {
    "Open": "first",
    "High": "max",
    "Low": "min",
    "Close": "last",
    "Volume": "sum",
}

SESSION_WINDOW_CACHE = {}


@contextmanager
def _timing_stage(timing, name, notes=""):
    if timing is None:
        yield
    else:
        with timing.stage(name, notes):
            yield


def _detect_csv_delimiter(file_path):
    with open(file_path, "rb") as file:
        sample = file.read(8192)
    first_line = sample.splitlines()[0].decode("utf-8-sig", errors="replace")
    candidates = [",", ";", "\t"]
    return max(candidates, key=lambda delimiter: first_line.count(delimiter))


def _select_ohlcv_columns(columns):
    normalized_to_original = {
        str(column).strip().lower(): column for column in columns
    }
    normalized = list(normalized_to_original)

    open_column = next((column for column in normalized if column == "open"), None)
    high_column = next((column for column in normalized if column == "high"), None)
    low_column = next((column for column in normalized if column == "low"), None)
    close_column = next((column for column in normalized if column == "close"), None)
    volume_column = next(
        (column for column in normalized if column in {"volume", "tickvolume"}),
        None,
    )

    date_columns = [column for column in normalized if "date" in column]
    time_columns = [column for column in normalized if "time" in column]
    datetime_columns = [
        column for column in normalized if "date" in column or "time" in column
    ]

    date_column = None
    time_column = None
    datetime_column = None
    split_pair = [
        (date_column_candidate, time_column_candidate)
        for date_column_candidate in date_columns
        for time_column_candidate in time_columns
        if date_column_candidate != time_column_candidate
    ]
    if split_pair:
        date_column, time_column = split_pair[0]
    elif datetime_columns:
        datetime_column = datetime_columns[0]

    required = {
        "open": open_column,
        "high": high_column,
        "low": low_column,
        "close": close_column,
        "volume": volume_column,
    }
    missing = [name for name, column in required.items() if column is None]
    if date_column is None and datetime_column is None:
        missing.append("date/time")
    if missing:
        raise ValueError(f"Eksik CSV sutunlari: {', '.join(missing)}")

    selected = [
        open_column,
        high_column,
        low_column,
        close_column,
        volume_column,
    ]
    if date_column is not None:
        selected.extend([date_column, time_column])
    else:
        selected.append(datetime_column)

    usecols = [normalized_to_original[column] for column in dict.fromkeys(selected)]
    return {
        "usecols": usecols,
        "open": open_column,
        "high": high_column,
        "low": low_column,
        "close": close_column,
        "volume": volume_column,
        "date": date_column,
        "time": time_column,
        "datetime": datetime_column,
    }


def _parse_datetime_fast(date_values, time_values=None):
    if time_values is not None:
        values = date_values.astype(str).str.strip() + " " + time_values.astype(str).str.strip()
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
        ]
    else:
        values = date_values.astype(str).str.strip()
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y",
        ]

    for datetime_format in formats:
        parsed = pd.to_datetime(
            values,
            errors="coerce",
            format=datetime_format,
            utc=True,
        )
        if parsed.notna().mean() > 0.95:
            return pd.DatetimeIndex(parsed).tz_convert(MARKET_TIMEZONE)

    parsed = pd.to_datetime(
        values,
        errors="coerce",
        dayfirst=True,
        format="mixed",
        utc=True,
    )
    return pd.DatetimeIndex(parsed).tz_convert(MARKET_TIMEZONE)


def read_ohlcv_csv_fast(file_path, timing=None):
    delimiter = _detect_csv_delimiter(file_path)
    header = pd.read_csv(file_path, sep=delimiter, engine="c", nrows=0)
    column_info = _select_ohlcv_columns(header.columns)

    with _timing_stage(timing, "CSV read fast", Path(file_path).name):
        df = pd.read_csv(
            file_path,
            sep=delimiter,
            engine="c",
            usecols=column_info["usecols"],
            low_memory=False,
        )
    df.columns = df.columns.str.strip().str.lower()

    with _timing_stage(timing, "Datetime parse", Path(file_path).name):
        if column_info["date"] is not None:
            index = _parse_datetime_fast(
                df[column_info["date"]],
                df[column_info["time"]],
            )
        else:
            index = _parse_datetime_fast(df[column_info["datetime"]])

    rename_map = {
        column_info["open"]: "Open",
        column_info["high"]: "High",
        column_info["low"]: "Low",
        column_info["close"]: "Close",
        column_info["volume"]: "Volume",
    }
    df = df.rename(columns=rename_map)
    df.index = index
    df = df.loc[~df.index.isna()].sort_index()

    required_columns = ["Open", "High", "Low", "Close", "Volume"]
    for column in required_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df[required_columns].ffill().dropna()


def _to_new_york_index(values):
    parsed = pd.to_datetime(values, errors="coerce", dayfirst=True, format="mixed")
    try:
        index = pd.DatetimeIndex(parsed)
    except (TypeError, ValueError):
        parsed = pd.to_datetime(
            values,
            errors="coerce",
            dayfirst=True,
            format="mixed",
            utc=True,
        )
        index = pd.DatetimeIndex(parsed)

    if index.tz is None:
        return index.tz_localize(
            MARKET_TIMEZONE,
            ambiguous="NaT",
            nonexistent="shift_forward",
        )
    return index.tz_convert(MARKET_TIMEZONE)


def _extract_datetime_index(df):
    date_columns = [column for column in df.columns if "date" in column]
    time_columns = [column for column in df.columns if "time" in column]

    if date_columns and time_columns:
        combined = (
            df[date_columns[0]].astype(str).str.strip()
            + " "
            + df[time_columns[0]].astype(str).str.strip()
        )
        # Date/Time split files in this dataset use UTC timestamps.
        parsed = pd.to_datetime(
            combined,
            errors="coerce",
            dayfirst=True,
            format="mixed",
            utc=True,
        )
        return pd.DatetimeIndex(parsed).tz_convert(MARKET_TIMEZONE)

    datetime_columns = [
        column for column in df.columns if "date" in column or "time" in column
    ]
    if not datetime_columns:
        raise ValueError("CSV dosyasinda tarih/zaman sutunu bulunamadi.")
    return _to_new_york_index(df[datetime_columns[0]])


def _infer_intraday_interval_minutes(index):
    local_index = index.tz_convert(MARKET_TIMEZONE)
    session_dates = pd.Series(local_index.date, index=local_index)
    same_session = session_dates.eq(session_dates.shift())
    deltas = local_index.to_series().diff()[same_session].dropna()
    if deltas.empty:
        raise ValueError(
            "130m RTH barlari icin intraday veri gerekli; gunluk veri kullanilamaz."
        )

    sampled_deltas = deltas.iloc[:10000]
    minute_deltas = (
        sampled_deltas.dt.total_seconds()
        .div(60)
        .round()
        .astype(int)
    )
    minute_deltas = minute_deltas[
        (minute_deltas > 0) & (minute_deltas < PRIMARY_BAR_MINUTES)
    ]
    if minute_deltas.empty and len(deltas) > len(sampled_deltas):
        minute_deltas = (
            deltas.dt.total_seconds()
            .div(60)
            .round()
            .astype(int)
        )
        minute_deltas = minute_deltas[
            (minute_deltas > 0) & (minute_deltas < PRIMARY_BAR_MINUTES)
        ]
    if minute_deltas.empty:
        raise ValueError(
            "Intraday kaynak bar araligi belirlenemedi."
        )

    # Missing candles make the median misleading for illiquid symbols.
    # The GCD still recovers the underlying 1m/5m base interval.
    interval_minutes = math.gcd(*minute_deltas.tolist())
    if interval_minutes <= 0 or interval_minutes >= PRIMARY_BAR_MINUTES:
        raise ValueError(
            f"Kaynak veri araligi yaklasik {interval_minutes} dakika. "
            "130m bar uretmek icin daha kisa intraday veri gerekli."
        )
    if PRIMARY_BAR_MINUTES % interval_minutes != 0:
        raise ValueError(
            f"{interval_minutes} dakikalik kaynak barlar 130 dakikaya tam bolunmuyor. "
            "1m, 2m, 5m, 10m, 13m, 26m veya 65m veri kullanin."
        )
    return interval_minutes


def _filter_fixed_rth_fast(df):
    local_index = df.index.tz_convert(MARKET_TIMEZONE)
    minutes = local_index.hour * 60 + local_index.minute
    mask = (minutes >= 570) & (minutes < 960)
    return df.loc[mask]


def _aggregate_fixed_rth_130m_fast(df):
    local_index = df.index.tz_convert(MARKET_TIMEZONE)
    normalized = local_index.normalize()
    session_open = normalized + pd.Timedelta(hours=9, minutes=30)
    bucket = ((local_index - session_open) // pd.Timedelta(minutes=PRIMARY_BAR_MINUTES)).astype(int)
    valid = (bucket >= 0) & (bucket <= 2)
    if not valid.any():
        raise ValueError("130m RTH barlari uretilemedi.")

    work = df.loc[valid].copy()
    bucket = np.asarray(bucket[valid], dtype=int)
    work["_SessionDate"] = pd.DatetimeIndex(normalized[valid])
    work["_Bucket"] = bucket
    grouped = work.groupby(["_SessionDate", "_Bucket"], sort=True).agg(OHLCV_AGG)
    if grouped.empty:
        raise ValueError("130m RTH barlari uretilemedi.")

    session_level = pd.DatetimeIndex(grouped.index.get_level_values(0))
    bucket_level = grouped.index.get_level_values(1).astype(int)
    close_offsets = pd.to_timedelta((bucket_level + 1) * PRIMARY_BAR_MINUTES, unit="m")
    grouped.index = session_level + pd.Timedelta(hours=9, minutes=30) + close_offsets
    grouped["Is_Partial_Session_Bar"] = False
    return grouped.sort_index()


def _fixed_session_windows(session_dates):
    windows = {}
    for session_date in session_dates:
        session_open = session_date + pd.Timedelta(hours=9, minutes=30)
        session_close = session_date + pd.Timedelta(hours=16)
        windows[session_date.date()] = (session_open, session_close)
    return windows


def _session_window_cache_key(start_date, end_date, use_market_calendar):
    return (
        pd.Timestamp(start_date).date(),
        pd.Timestamp(end_date).date(),
        bool(use_market_calendar),
        MARKET_TIMEZONE,
    )


def _build_session_windows(index, use_market_calendar=True):
    local_index = index.tz_convert(MARKET_TIMEZONE)
    session_dates = pd.DatetimeIndex(pd.Series(local_index.normalize()).unique()).sort_values()
    if session_dates.empty:
        return {}, False

    cache_key = _session_window_cache_key(
        session_dates.min().date(),
        session_dates.max().date(),
        use_market_calendar,
    )
    cached = SESSION_WINDOW_CACHE.get(cache_key)
    if cached is not None:
        windows, calendar_fallback = cached
        return windows, calendar_fallback

    if use_market_calendar and mcal is not None:
        calendar = mcal.get_calendar("NYSE")
        schedule = calendar.schedule(
            start_date=session_dates.min().date(),
            end_date=session_dates.max().date(),
        )
        windows = {}
        for _, row in schedule.iterrows():
            market_open = pd.Timestamp(row["market_open"]).tz_convert(MARKET_TIMEZONE)
            market_close = pd.Timestamp(row["market_close"]).tz_convert(MARKET_TIMEZONE)
            windows[market_open.normalize().date()] = (market_open, market_close)
        SESSION_WINDOW_CACHE[cache_key] = (windows, False)
        return windows, False

    if use_market_calendar and mcal is None:
        print(
            "Uyari: pandas_market_calendars bulunamadi; sabit "
            "09:30-16:00 RTH fallback kullaniliyor."
        )

    fallback_dates = pd.date_range(
        session_dates.min(),
        session_dates.max(),
        freq="D",
    )
    windows = _fixed_session_windows(fallback_dates)
    calendar_fallback = bool(use_market_calendar)
    SESSION_WINDOW_CACHE[cache_key] = (windows, calendar_fallback)
    return windows, calendar_fallback


def _filter_rth_with_session_windows(df, session_windows):
    frames = []
    for session_date, session in df.groupby(df.index.date, sort=True):
        if session_date not in session_windows:
            continue
        session_open, session_close = session_windows[session_date]
        frames.append(
            session.loc[
                (session.index >= session_open)
                & (session.index < session_close)
            ]
        )
    if not frames:
        return df.iloc[0:0].copy()
    return pd.concat(frames).sort_index()


def _aggregate_each_rth_session(
    df,
    bar_minutes,
    session_windows=None,
    include_partial_session_bars=False,
    use_market_calendar=True,
):
    if (
        not use_market_calendar
        and bar_minutes == PRIMARY_BAR_MINUTES
        and not include_partial_session_bars
    ):
        return _aggregate_fixed_rth_130m_fast(df)

    frames = []
    for _, session in df.groupby(df.index.date, sort=True):
        if session.empty:
            continue

        session_date = session.index[0].date()
        if session_windows and session_date in session_windows:
            session_open, session_close = session_windows[session_date]
        else:
            session_open = session.index[0].normalize() + pd.Timedelta(hours=9, minutes=30)
            session_close = session.index[0].normalize() + pd.Timedelta(hours=16)
        bucket = (
            (session.index - session_open) // pd.Timedelta(minutes=bar_minutes)
        ).astype(int)
        aggregated = session.groupby(bucket).agg(OHLCV_AGG)
        aggregated = aggregated.loc[aggregated.index >= 0]
        rows = []
        index = []
        partial_flags = []
        for bucket_id, row in aggregated.iterrows():
            bucket_id = int(bucket_id)
            bucket_start = session_open + pd.Timedelta(minutes=bar_minutes * bucket_id)
            expected_end = session_open + pd.Timedelta(minutes=bar_minutes * (bucket_id + 1))
            bucket_end = min(expected_end, session_close)
            is_partial = bucket_end < expected_end
            if bucket_start >= session_close:
                continue
            if is_partial and not include_partial_session_bars:
                continue
            rows.append(row)
            index.append(bucket_end)
            partial_flags.append(bool(is_partial))
        if not rows:
            continue
        aggregated = pd.DataFrame(rows, index=pd.DatetimeIndex(index))
        aggregated["Is_Partial_Session_Bar"] = partial_flags
        frames.append(aggregated)

    if not frames:
        raise ValueError("RTH filtresinden sonra kullanilabilir seans verisi kalmadi.")
    return pd.concat(frames).sort_index()


def prepare_rth_intraday_bars(
    df,
    use_market_calendar=True,
    session_windows=None,
    calendar_fallback=None,
    timing=None,
):
    df = df.copy()
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df.index = df.index.tz_convert(MARKET_TIMEZONE)
    else:
        df.index = _to_new_york_index(df.index)
    df = df.loc[~df.index.isna()].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    _infer_intraday_interval_minutes(df.index)

    with _timing_stage(timing, "RTH filter", f"use_market_calendar={use_market_calendar}"):
        if not use_market_calendar:
            df = _filter_fixed_rth_fast(df)
            session_windows = None
            calendar_fallback = False
        else:
            if session_windows is None:
                session_windows, calendar_fallback = _build_session_windows(
                    df.index,
                    use_market_calendar=use_market_calendar,
                )
            elif calendar_fallback is None:
                calendar_fallback = bool(use_market_calendar and mcal is None)
            df = _filter_rth_with_session_windows(df, session_windows)
    if df.empty:
        raise ValueError(
            f"{RTH_OPEN}-{RTH_CLOSE} America/New_York RTH filtresinden sonra veri kalmadi."
        )
    df.attrs["calendar_fallback"] = calendar_fallback
    df.attrs["session_windows"] = session_windows
    df.attrs["use_market_calendar"] = bool(use_market_calendar)
    return df


def prepare_rth_130m_bars(
    df,
    use_market_calendar=True,
    include_partial_session_bars=False,
    session_windows=None,
    calendar_fallback=None,
    timing=None,
):
    df = prepare_rth_intraday_bars(
        df,
        use_market_calendar=use_market_calendar,
        session_windows=session_windows,
        calendar_fallback=calendar_fallback,
        timing=timing,
    )
    session_windows = df.attrs.get("session_windows")
    calendar_fallback = bool(df.attrs.get("calendar_fallback", False))

    with _timing_stage(timing, "130m aggregation", f"use_market_calendar={use_market_calendar}"):
        bars = _aggregate_each_rth_session(
            df,
            PRIMARY_BAR_MINUTES,
            session_windows=session_windows,
            include_partial_session_bars=include_partial_session_bars,
            use_market_calendar=use_market_calendar,
        )
    if bars.empty:
        raise ValueError("130m RTH barlari uretilemedi.")
    bars.attrs["calendar_fallback"] = calendar_fallback
    bars.attrs["include_partial_session_bars"] = include_partial_session_bars
    return bars


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


def _normalize_hurst_filter_mode(hurst_filter_mode):
    mode = str(hurst_filter_mode).lower()
    if mode not in {"off", "gt", "lt"}:
        raise ValueError(
            "hurst_filter_mode 'off', 'gt' veya 'lt' degerlerinden biri olmali."
        )
    return mode


def _normalize_confirmation_mode(confirmation_mode):
    mode = str(confirmation_mode).lower()
    if mode not in {
        "off",
        "close_above_prior_close",
        "close_above_signal_high",
        "rebound_atr",
    }:
        raise ValueError(
            "confirmation_mode 'off', 'close_above_prior_close', "
            "'close_above_signal_high' veya 'rebound_atr' olmali."
        )
    return mode


def _calculate_confirmed_entry_signal(
    df,
    confirmation_mode="off",
    confirmation_lookback=1,
    confirmation_min_rebound_atr=0.0,
):
    confirmation_mode = _normalize_confirmation_mode(confirmation_mode)
    confirmation_lookback = int(confirmation_lookback)
    confirmation_min_rebound_atr = float(confirmation_min_rebound_atr)
    if confirmation_lookback < 1:
        raise ValueError("confirmation_lookback en az 1 olmali.")
    if confirmation_min_rebound_atr < 0:
        raise ValueError("confirmation_min_rebound_atr negatif olamaz.")

    raw_signal = df["Raw_Entry_Signal"].fillna(False).astype(bool)
    if confirmation_mode == "off":
        confirmation_ok = pd.Series(True, index=df.index)
        confirmed_entry = raw_signal
    else:
        confirmed_values = np.zeros(len(df), dtype=bool)
        raw_values = raw_signal.to_numpy(dtype=bool)
        close_values = pd.to_numeric(df["Close"], errors="coerce").to_numpy()
        high_values = pd.to_numeric(df["High"], errors="coerce").to_numpy()
        atr_values = pd.to_numeric(
            df["SuperTrend_ATR_22"],
            errors="coerce",
        ).to_numpy()
        pending = []
        for position in range(len(df)):
            kept_pending = []
            for signal_position in pending:
                age = position - signal_position
                if age < 1:
                    kept_pending.append(signal_position)
                    continue
                if age > confirmation_lookback:
                    continue

                if confirmation_mode == "close_above_prior_close":
                    confirmed = (
                        position > 0
                        and close_values[position] > close_values[position - 1]
                    )
                elif confirmation_mode == "close_above_signal_high":
                    confirmed = close_values[position] > high_values[signal_position]
                else:
                    rebound_level = (
                        close_values[signal_position]
                        + atr_values[signal_position] * confirmation_min_rebound_atr
                    )
                    confirmed = close_values[position] >= rebound_level

                if confirmed:
                    confirmed_values[position] = True
                else:
                    kept_pending.append(signal_position)

            if raw_values[position]:
                kept_pending.append(position)
            pending = kept_pending
        confirmation_ok = pd.Series(confirmed_values, index=df.index)
        confirmed_entry = confirmation_ok

    return confirmation_ok.fillna(False).astype(bool), confirmed_entry.fillna(False).astype(bool)


def _optional_nonnegative_float(value, name):
    if value is None:
        return None
    value = float(value)
    if value < 0:
        raise ValueError(f"{name} None veya >= 0 olmali.")
    return value


def _optional_unit_float(value, name):
    if value is None:
        return None
    value = float(value)
    if value < 0 or value > 1:
        raise ValueError(f"{name} None veya 0 ile 1 arasinda olmali.")
    return value


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
        "max_entry_gap_pct": _optional_nonnegative_float(
            max_entry_gap_pct,
            "max_entry_gap_pct",
        ),
        "max_entry_bb_distance_atr": _optional_nonnegative_float(
            max_entry_bb_distance_atr,
            "max_entry_bb_distance_atr",
        ),
        "max_entry_signal_range_pct": _optional_nonnegative_float(
            max_entry_signal_range_pct,
            "max_entry_signal_range_pct",
        ),
        "min_entry_close_position_in_range": _optional_unit_float(
            min_entry_close_position_in_range,
            "min_entry_close_position_in_range",
        ),
        "max_entry_close_position_in_range": _optional_unit_float(
            max_entry_close_position_in_range,
            "max_entry_close_position_in_range",
        ),
    }
    min_position = params["min_entry_close_position_in_range"]
    max_position = params["max_entry_close_position_in_range"]
    if (
        min_position is not None
        and max_position is not None
        and min_position > max_position
    ):
        raise ValueError(
            "min_entry_close_position_in_range <= "
            "max_entry_close_position_in_range olmali."
        )
    return params


def _calculate_entry_context_filter(
    df,
    use_entry_context_filter=False,
    max_entry_gap_pct=None,
    max_entry_bb_distance_atr=None,
    max_entry_signal_range_pct=None,
    min_entry_close_position_in_range=None,
    max_entry_close_position_in_range=None,
):
    params = _normalize_entry_context_filter_params(
        use_entry_context_filter=use_entry_context_filter,
        max_entry_gap_pct=max_entry_gap_pct,
        max_entry_bb_distance_atr=max_entry_bb_distance_atr,
        max_entry_signal_range_pct=max_entry_signal_range_pct,
        min_entry_close_position_in_range=min_entry_close_position_in_range,
        max_entry_close_position_in_range=max_entry_close_position_in_range,
    )
    previous_close = df["Close"].shift(1)
    entry_gap_pct = 100 * (df["Open"] - previous_close) / previous_close.replace(
        0,
        np.nan,
    )
    entry_bb_distance_atr = (
        (df["Close"] - df["Bollinger_Lower"])
        / df["SuperTrend_ATR_22"].replace(0, np.nan)
    )
    entry_signal_range_pct = (
        100 * (df["High"] - df["Low"]) / df["Close"].replace(0, np.nan)
    )
    signal_range = df["High"] - df["Low"]
    entry_close_position = (df["Close"] - df["Low"]) / signal_range.replace(
        0,
        np.nan,
    )

    filter_ok = pd.Series(True, index=df.index)
    rejected_reason = pd.Series("", index=df.index, dtype=object)
    if params["use_entry_context_filter"]:
        checks = [
            (
                "max_entry_gap_pct",
                params["max_entry_gap_pct"],
                entry_gap_pct,
                lambda series, limit: series <= limit,
            ),
            (
                "max_entry_bb_distance_atr",
                params["max_entry_bb_distance_atr"],
                entry_bb_distance_atr,
                lambda series, limit: series <= limit,
            ),
            (
                "max_entry_signal_range_pct",
                params["max_entry_signal_range_pct"],
                entry_signal_range_pct,
                lambda series, limit: series <= limit,
            ),
            (
                "min_entry_close_position_in_range",
                params["min_entry_close_position_in_range"],
                entry_close_position,
                lambda series, limit: series >= limit,
            ),
            (
                "max_entry_close_position_in_range",
                params["max_entry_close_position_in_range"],
                entry_close_position,
                lambda series, limit: series <= limit,
            ),
        ]
        for reason, limit, series, predicate in checks:
            if limit is None:
                continue
            check_ok = predicate(series, limit).fillna(False).astype(bool)
            newly_rejected = filter_ok & (~check_ok)
            rejected_reason.loc[newly_rejected] = reason
            filter_ok &= check_ok

    return {
        "Entry_Context_Filter_OK": filter_ok.fillna(False).astype(bool),
        "Entry_Context_Rejected_Reason": rejected_reason,
        "Entry_Gap_Pct": entry_gap_pct,
        "Entry_BB_Distance_ATR": entry_bb_distance_atr,
        "Entry_Signal_Range_Pct": entry_signal_range_pct,
        "Entry_Signal_Close_Position_In_Range": entry_close_position,
    }


def validate_strategy_name(strategy_name):
    strategy_name = DEFAULT_STRATEGY_NAME if strategy_name is None else str(strategy_name)
    if strategy_name not in STRATEGY_FAMILY_MAP:
        raise ValueError(f"Unknown strategy_name: {strategy_name}")
    return strategy_name


def get_strategy_family(strategy_name):
    return STRATEGY_FAMILY_MAP[validate_strategy_name(strategy_name)]


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
    return params


def _strategy_cache_config(
    hurst_filter_mode,
    hurst_threshold,
    strategy_name=DEFAULT_STRATEGY_NAME,
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
    csm_params = {
        "csm_lookback_days": csm_lookback_days,
        "csm_top_n": csm_top_n,
        "csm_rebalance_frequency": csm_rebalance_frequency,
        "csm_trend_sma_days": csm_trend_sma_days,
        "csm_use_market_regime": csm_use_market_regime,
        "csm_market_regime_sma_days": csm_market_regime_sma_days,
        "csm_min_price": csm_min_price,
        "csm_max_atr_pct": csm_max_atr_pct,
    }
    dm_params = _normalize_dual_momentum_params(
        {
            "dm_lookback_days": dm_lookback_days,
            "dm_top_n": dm_top_n,
            "dm_rebalance_frequency": dm_rebalance_frequency,
            "dm_absolute_momentum_threshold": dm_absolute_momentum_threshold,
            "dm_trend_sma_days": dm_trend_sma_days,
            "dm_use_market_regime": dm_use_market_regime,
            "dm_market_regime_sma_days": dm_market_regime_sma_days,
            "dm_min_price": dm_min_price,
            "dm_max_atr_pct": dm_max_atr_pct,
        }
    )
    donchian_params = _normalize_donchian_breakout_params(
        {
            "donchian_entry_lookback_days": donchian_entry_lookback_days,
            "donchian_exit_lookback_days": donchian_exit_lookback_days,
            "donchian_top_n": donchian_top_n,
            "donchian_rebalance_frequency": donchian_rebalance_frequency,
            "donchian_trend_sma_days": donchian_trend_sma_days,
            "donchian_use_market_regime": donchian_use_market_regime,
            "donchian_market_regime_sma_days": donchian_market_regime_sma_days,
            "donchian_min_price": donchian_min_price,
            "donchian_max_atr_pct": donchian_max_atr_pct,
        }
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
        "strategy_name": validate_strategy_name(strategy_name),
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


def _generate_current_pullback_mean_reversion_signals(
    df,
    hurst_filter_mode="off",
    hurst_threshold=0.5,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    bb_entry_tolerance_atr=0.0,
    trend_sma_length=50,
    setup_lookback=3,
    use_quality_filter=True,
    min_price_filter=10.0,
    min_atr_pct_filter=0.2,
    max_atr_pct_filter=12.0,
    min_avg_dollar_volume=20_000_000,
    avg_volume_lookback=20,
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
    hurst_filter_mode = _normalize_hurst_filter_mode(hurst_filter_mode)
    hurst_threshold = float(hurst_threshold)
    confirmation_mode = _normalize_confirmation_mode(confirmation_mode)
    confirmation_lookback = int(confirmation_lookback)
    confirmation_min_rebound_atr = float(confirmation_min_rebound_atr)
    context_filter_params = _normalize_entry_context_filter_params(
        use_entry_context_filter=use_entry_context_filter,
        max_entry_gap_pct=max_entry_gap_pct,
        max_entry_bb_distance_atr=max_entry_bb_distance_atr,
        max_entry_signal_range_pct=max_entry_signal_range_pct,
        min_entry_close_position_in_range=min_entry_close_position_in_range,
        max_entry_close_position_in_range=max_entry_close_position_in_range,
    )
    rsi_length = int(rsi_length)
    rsi_entry_threshold = float(rsi_entry_threshold)
    bollinger_length = int(bollinger_length)
    bollinger_std = float(bollinger_std)
    bb_entry_tolerance_atr = float(bb_entry_tolerance_atr)
    if bb_entry_tolerance_atr < 0:
        raise ValueError("bb_entry_tolerance_atr negatif olamaz.")
    use_quality_filter = bool(use_quality_filter)
    min_price_filter = float(min_price_filter)
    min_atr_pct_filter = float(min_atr_pct_filter)
    max_atr_pct_filter = float(max_atr_pct_filter)
    min_avg_dollar_volume = float(min_avg_dollar_volume)
    avg_volume_lookback = int(avg_volume_lookback)
    if min_price_filter < 0:
        raise ValueError("min_price_filter negatif olamaz.")
    if min_atr_pct_filter < 0:
        raise ValueError("min_atr_pct_filter negatif olamaz.")
    if max_atr_pct_filter <= min_atr_pct_filter:
        raise ValueError("max_atr_pct_filter min_atr_pct_filter'dan buyuk olmali.")
    if min_avg_dollar_volume < 0:
        raise ValueError("min_avg_dollar_volume negatif olamaz.")
    if avg_volume_lookback < 1:
        raise ValueError("avg_volume_lookback en az 1 olmali.")
    trend_sma_length = int(trend_sma_length)
    setup_lookback = int(setup_lookback)
    exit_sma_length = int(exit_sma_length)
    take_profit_atr_multiplier = float(take_profit_atr_multiplier)
    disaster_stop_atr_multiplier = float(disaster_stop_atr_multiplier)
    df = df.copy()
    if "Risk_Off" not in df:
        df["Risk_Off"] = False

    df["SMA_10"] = df["Close"].rolling(10).mean()
    df["Trend_SMA"] = df["Close"].rolling(trend_sma_length).mean()
    df["Macro_Uptrend"] = df["Close"] > df["Trend_SMA"]
    df["Pullback_Condition"] = df["Close"] < df["SMA_10"]
    df["Setup_OK"] = df["Pullback_Condition"].rolling(setup_lookback).max() > 0
    rsi_delta = df["Close"].diff()
    rsi_gain = rsi_delta.clip(lower=0).rolling(rsi_length).mean()
    rsi_loss = (-rsi_delta.clip(upper=0)).rolling(rsi_length).mean()
    df["RSI_Value"] = 100 - (100 / (1 + rsi_gain / rsi_loss.replace(0, np.nan)))
    df["Bollinger_Lower"] = df["Close"].rolling(bollinger_length).mean() - (
        df["Close"].rolling(bollinger_length).std() * bollinger_std
    )
    df["Hurst_Exponent"] = calculate_hurst_exponent(df, HURST_LAG_MAX)
    df["Hurst_Entry_OK"] = df["Hurst_Exponent"] > hurst_threshold
    if hurst_filter_mode == "off":
        df["Hurst_Filter_OK"] = True
    elif hurst_filter_mode == "gt":
        df["Hurst_Filter_OK"] = df["Hurst_Exponent"] > hurst_threshold
    else:
        df["Hurst_Filter_OK"] = df["Hurst_Exponent"] < hurst_threshold

    df["Indicators_Ready"] = df[
        [
            "Trend_SMA",
            "RSI_Value",
            "Bollinger_Lower",
        ]
    ].notna().all(axis=1)

    supertrend, direction, lower_band, atr = calculate_supertrend(df)
    df["SuperTrend_ATR_22"] = atr
    df["Bollinger_Entry_Level"] = (
        df["Bollinger_Lower"]
        + df["SuperTrend_ATR_22"] * bb_entry_tolerance_atr
    )
    if bb_entry_tolerance_atr == 0:
        bollinger_entry_ok = df["Close"] < df["Bollinger_Lower"]
    else:
        bollinger_entry_ok = df["Close"] <= df["Bollinger_Entry_Level"]
    df["Trigger"] = (
        (df["RSI_Value"] < rsi_entry_threshold)
        & bollinger_entry_ok
    )
    df["ROC_20"] = df["Close"].pct_change(20)
    net_change = df["Close"].diff(20).abs()
    vol_sum = df["Close"].diff().abs().rolling(20, min_periods=20).sum()
    df["ER_20"] = net_change / vol_sum.replace(0, np.nan)
    df["ATR_Pct"] = 100 * df["SuperTrend_ATR_22"] / df["Close"].replace(0, np.nan)
    df["Avg_Dollar_Volume"] = np.nan
    df.attrs["quality_filter_warning"] = ""
    if "Volume" in df.columns:
        df["Avg_Dollar_Volume"] = (
            df["Close"] * df["Volume"]
        ).rolling(avg_volume_lookback).mean()
        volume_filter_ok = df["Avg_Dollar_Volume"] >= min_avg_dollar_volume
    else:
        volume_filter_ok = pd.Series(True, index=df.index)
        if use_quality_filter:
            df.attrs["quality_filter_warning"] = (
                "Volume column not available; dollar volume filter skipped."
            )
    if use_quality_filter:
        df["Quality_Filter_OK"] = (
            (df["Close"] >= min_price_filter)
            & (df["ATR_Pct"] >= min_atr_pct_filter)
            & (df["ATR_Pct"] <= max_atr_pct_filter)
            & volume_filter_ok.fillna(False).astype(bool)
        )
    else:
        df["Quality_Filter_OK"] = True
    df["Quality_Filter_OK"] = df["Quality_Filter_OK"].fillna(False).astype(bool)
    df["Momentum_Factor_Raw"] = (
        df["ROC_20"] / df["ATR_Pct"].replace(0, np.nan)
    ) * df["ER_20"]
    df["Momentum_Factor"] = df["Momentum_Factor_Raw"]
    df["SuperTrend_Lower_22_22"] = lower_band
    df["SuperTrend_22_22"] = supertrend
    df["SuperTrend_Direction"] = direction
    df["ATR_Take_Profit_Distance"] = (
        df["SuperTrend_ATR_22"] * take_profit_atr_multiplier
    )
    sma20 = df["Close"].rolling(exit_sma_length).mean()
    df["SMA20_Reversion_Exit"] = df["Close"] > sma20
    df["Take_Profit"] = df["SMA20_Reversion_Exit"]
    df["Disaster_Stop"] = (
        df["Close"] - (df["SuperTrend_ATR_22"] * disaster_stop_atr_multiplier)
    )
    df["Indicators_Ready"] &= df[
        [
            "SuperTrend_ATR_22",
            "ATR_Pct",
            "Momentum_Factor_Raw",
            "ATR_Take_Profit_Distance",
            "SMA20_Reversion_Exit",
            "Disaster_Stop",
            "Quality_Filter_OK",
        ]
    ].notna().all(axis=1)
    df["Risk_Off"] = df["Risk_Off"].fillna(True).astype(bool)
    df["Macro_Filter_OK"] = df["Macro_Uptrend"].fillna(False).astype(bool)
    df["Raw_Entry_Signal"] = (
        df["Trigger"]
        & df["Macro_Filter_OK"]
        & df["Indicators_Ready"]
        & df["Quality_Filter_OK"].fillna(False).astype(bool)
    )
    df["Raw_Entry_Signal"] = df["Raw_Entry_Signal"].fillna(False).astype(bool)
    if not use_quality_filter:
        raw_without_quality = (
            df["Trigger"]
            & df["Macro_Filter_OK"]
            & df["Indicators_Ready"]
        ).fillna(False).astype(bool)
        if not df["Raw_Entry_Signal"].equals(raw_without_quality):
            raise ValueError(
                "use_quality_filter=False iken Quality_Filter_OK entry sinyalini "
                "degistirmemeli."
            )
    df["Confirmation_OK"], df["Confirmed_Entry_Signal"] = (
        _calculate_confirmed_entry_signal(
            df,
            confirmation_mode=confirmation_mode,
            confirmation_lookback=confirmation_lookback,
            confirmation_min_rebound_atr=confirmation_min_rebound_atr,
        )
    )
    df["Confirmation_Mode"] = confirmation_mode
    context_filter = _calculate_entry_context_filter(df, **context_filter_params)
    for column, values in context_filter.items():
        df[column] = values
    df["ENTRY_SIGNAL"] = (
        df["Confirmed_Entry_Signal"]
        & df["Hurst_Filter_OK"].fillna(False).astype(bool)
        & (~df["Risk_Off"])
        & df["Entry_Context_Filter_OK"].fillna(False).astype(bool)
    )
    for signal_column in [
        "Confirmation_OK",
        "Confirmed_Entry_Signal",
        "Entry_Context_Filter_OK",
        "ENTRY_SIGNAL",
    ]:
        df[signal_column] = df[signal_column].fillna(False).astype(bool)
    # Exit is confirmed only after the candle closes. Execution is deferred
    # to the next candle open in ScannerBacktester.
    df["EXIT_SIGNAL"] = df["SMA20_Reversion_Exit"]
    df.attrs.update(
        _strategy_cache_config(
            hurst_filter_mode,
            hurst_threshold,
            rsi_length=rsi_length,
            rsi_entry_threshold=rsi_entry_threshold,
            bollinger_length=bollinger_length,
            bollinger_std=bollinger_std,
            bb_entry_tolerance_atr=bb_entry_tolerance_atr,
            use_quality_filter=use_quality_filter,
            min_price_filter=min_price_filter,
            min_atr_pct_filter=min_atr_pct_filter,
            max_atr_pct_filter=max_atr_pct_filter,
            min_avg_dollar_volume=min_avg_dollar_volume,
            avg_volume_lookback=avg_volume_lookback,
            trend_sma_length=trend_sma_length,
            setup_lookback=setup_lookback,
            exit_sma_length=exit_sma_length,
            take_profit_atr_multiplier=take_profit_atr_multiplier,
            disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
            exit_mode=exit_mode,
            confirmation_mode=confirmation_mode,
            confirmation_lookback=confirmation_lookback,
            confirmation_min_rebound_atr=confirmation_min_rebound_atr,
            **context_filter_params,
        )
    )
    return df


def generate_current_pullback_mean_reversion_signals(df, config):
    config = dict(config or {})
    config.pop("strategy_name", None)
    signal_config = {
        key: value
        for key, value in config.items()
        if key in CURRENT_PULLBACK_SIGNAL_CONFIG_KEYS
    }
    return _generate_current_pullback_mean_reversion_signals(df, **signal_config)


def _daily_ohlcv_from_frame(df):
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("relative_strength_rotation icin DatetimeIndex gerekli.")
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
    daily_return = daily["Close"].pct_change()
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
    frame.attrs.update(_strategy_cache_config("off", 0.5, strategy_name="relative_strength_rotation", **params))
    return frame


def generate_strategy_signals(df, config):
    config = dict(config or {})
    strategy_name = validate_strategy_name(config.get("strategy_name"))
    if strategy_name == "current_pullback_mean_reversion":
        return generate_current_pullback_mean_reversion_signals(df, config)
    if strategy_name == "relative_strength_rotation":
        return generate_relative_strength_rotation_signals(df, config)
    elif strategy_name == "dual_momentum":
        return generate_dual_momentum_signals(df, config)
    if strategy_name == "cross_sectional_momentum":
        return generate_cross_sectional_momentum_signals(df, config)
    if strategy_name == "donchian_breakout":
        return generate_donchian_breakout_signals(df, config)
    raise NotImplementedError(f"strategy_name not implemented yet: {strategy_name}")


def add_entry_indicators(
    df,
    strategy_name=DEFAULT_STRATEGY_NAME,
    hurst_filter_mode="off",
    hurst_threshold=0.5,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    bb_entry_tolerance_atr=0.0,
    trend_sma_length=50,
    setup_lookback=3,
    use_quality_filter=True,
    min_price_filter=10.0,
    min_atr_pct_filter=0.2,
    max_atr_pct_filter=12.0,
    min_avg_dollar_volume=20_000_000,
    avg_volume_lookback=20,
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
    return generate_strategy_signals(
        df,
        {
            "strategy_name": strategy_name,
            "hurst_filter_mode": hurst_filter_mode,
            "hurst_threshold": hurst_threshold,
            "rsi_length": rsi_length,
            "rsi_entry_threshold": rsi_entry_threshold,
            "bollinger_length": bollinger_length,
            "bollinger_std": bollinger_std,
            "bb_entry_tolerance_atr": bb_entry_tolerance_atr,
            "trend_sma_length": trend_sma_length,
            "setup_lookback": setup_lookback,
            "use_quality_filter": use_quality_filter,
            "min_price_filter": min_price_filter,
            "min_atr_pct_filter": min_atr_pct_filter,
            "max_atr_pct_filter": max_atr_pct_filter,
            "min_avg_dollar_volume": min_avg_dollar_volume,
            "avg_volume_lookback": avg_volume_lookback,
            "exit_sma_length": exit_sma_length,
            "take_profit_atr_multiplier": take_profit_atr_multiplier,
            "disaster_stop_atr_multiplier": disaster_stop_atr_multiplier,
            "exit_mode": exit_mode,
            "confirmation_mode": confirmation_mode,
            "confirmation_lookback": confirmation_lookback,
            "confirmation_min_rebound_atr": confirmation_min_rebound_atr,
            "use_entry_context_filter": use_entry_context_filter,
            "max_entry_gap_pct": max_entry_gap_pct,
            "max_entry_bb_distance_atr": max_entry_bb_distance_atr,
            "max_entry_signal_range_pct": max_entry_signal_range_pct,
            "min_entry_close_position_in_range": min_entry_close_position_in_range,
            "max_entry_close_position_in_range": max_entry_close_position_in_range,
            "csm_lookback_days": csm_lookback_days,
            "csm_top_n": csm_top_n,
            "csm_rebalance_frequency": csm_rebalance_frequency,
            "csm_trend_sma_days": csm_trend_sma_days,
            "csm_use_market_regime": csm_use_market_regime,
            "csm_market_regime_sma_days": csm_market_regime_sma_days,
            "csm_min_price": csm_min_price,
            "csm_max_atr_pct": csm_max_atr_pct,
            "dm_lookback_days": dm_lookback_days,
            "dm_top_n": dm_top_n,
            "dm_rebalance_frequency": dm_rebalance_frequency,
            "dm_absolute_momentum_threshold": dm_absolute_momentum_threshold,
            "dm_trend_sma_days": dm_trend_sma_days,
            "dm_use_market_regime": dm_use_market_regime,
            "dm_market_regime_sma_days": dm_market_regime_sma_days,
            "dm_min_price": dm_min_price,
            "dm_max_atr_pct": dm_max_atr_pct,
            "donchian_entry_lookback_days": donchian_entry_lookback_days,
            "donchian_exit_lookback_days": donchian_exit_lookback_days,
            "donchian_top_n": donchian_top_n,
            "donchian_rebalance_frequency": donchian_rebalance_frequency,
            "donchian_trend_sma_days": donchian_trend_sma_days,
            "donchian_use_market_regime": donchian_use_market_regime,
            "donchian_market_regime_sma_days": donchian_market_regime_sma_days,
            "donchian_min_price": donchian_min_price,
            "donchian_max_atr_pct": donchian_max_atr_pct,
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
        },
    )


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


def apply_exit_parameters_to_asset_data(
    asset_data,
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
):
    _normalize_exit_mode(exit_mode)
    exit_sma_length = int(exit_sma_length)
    take_profit_atr_multiplier = float(take_profit_atr_multiplier)
    disaster_stop_atr_multiplier = float(disaster_stop_atr_multiplier)
    updated = {}
    for symbol, data in asset_data.items():
        frame = data.copy(deep=False)
        frame["ATR_Take_Profit_Distance"] = (
            frame["SuperTrend_ATR_22"] * take_profit_atr_multiplier
        )
        frame["Disaster_Stop"] = (
            frame["Close"]
            - (frame["SuperTrend_ATR_22"] * disaster_stop_atr_multiplier)
        )
        sma_exit = frame["Close"].rolling(exit_sma_length).mean()
        frame["SMA20_Reversion_Exit"] = frame["Close"] > sma_exit
        frame["Take_Profit"] = frame["SMA20_Reversion_Exit"]
        frame["EXIT_SIGNAL"] = frame["SMA20_Reversion_Exit"]
        frame.attrs.update(data.attrs)
        frame.attrs.update(
            {
                "exit_sma_length": exit_sma_length,
                "take_profit_atr_multiplier": take_profit_atr_multiplier,
                "disaster_stop_atr_multiplier": disaster_stop_atr_multiplier,
                "exit_mode": str(exit_mode),
            }
        )
        updated[symbol] = frame
    return updated


def apply_entry_filters_to_asset_data(
    asset_data,
    hurst_filter_mode="off",
    hurst_threshold=0.5,
):
    hurst_filter_mode = _normalize_hurst_filter_mode(hurst_filter_mode)
    hurst_threshold = float(hurst_threshold)
    updated = {}
    for symbol, data in asset_data.items():
        frame = data.copy(deep=False)
        if hurst_filter_mode == "off":
            frame["Hurst_Filter_OK"] = True
        elif hurst_filter_mode == "gt":
            frame["Hurst_Filter_OK"] = frame["Hurst_Exponent"] > hurst_threshold
        else:
            frame["Hurst_Filter_OK"] = frame["Hurst_Exponent"] < hurst_threshold
        confirmed_entry = frame.get("Confirmed_Entry_Signal", frame["Raw_Entry_Signal"])
        frame["ENTRY_SIGNAL"] = (
            confirmed_entry.fillna(False).astype(bool)
            & frame["Hurst_Filter_OK"].fillna(False).astype(bool)
            & (~frame["Risk_Off"].fillna(True).astype(bool))
            & frame.get(
                "Entry_Context_Filter_OK",
                pd.Series(True, index=frame.index),
            ).fillna(False).astype(bool)
        )
        frame.attrs.update(data.attrs)
        frame.attrs.update(
            {
                "hurst_filter_mode": hurst_filter_mode,
                "hurst_threshold": hurst_threshold,
            }
        )
        updated[symbol] = frame
    return updated


def build_mega_dataframe(
    file_path,
    benchmark_df=None,
    hurst_filter_mode="off",
    hurst_threshold=0.5,
    return_intraday=False,
    use_market_calendar=True,
    include_partial_session_bars=False,
    session_windows=None,
    calendar_fallback=None,
    timing=None,
    strategy_name=DEFAULT_STRATEGY_NAME,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    bb_entry_tolerance_atr=0.0,
    trend_sma_length=50,
    setup_lookback=3,
    use_quality_filter=True,
    min_price_filter=10.0,
    min_atr_pct_filter=0.2,
    max_atr_pct_filter=12.0,
    min_avg_dollar_volume=20_000_000,
    avg_volume_lookback=20,
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
    df = read_ohlcv_csv_fast(file_path, timing=timing)
    intraday_df = prepare_rth_intraday_bars(
        df,
        use_market_calendar=use_market_calendar,
        session_windows=session_windows,
        calendar_fallback=calendar_fallback,
        timing=timing,
    )
    session_windows = intraday_df.attrs.get("session_windows")
    calendar_fallback = bool(intraday_df.attrs.get("calendar_fallback", False))
    with _timing_stage(timing, "130m aggregation", Path(file_path).name):
        df = _aggregate_each_rth_session(
            intraday_df,
            PRIMARY_BAR_MINUTES,
            session_windows=session_windows,
            include_partial_session_bars=include_partial_session_bars,
            use_market_calendar=use_market_calendar,
        )
    df.attrs["calendar_fallback"] = calendar_fallback
    df.attrs["include_partial_session_bars"] = include_partial_session_bars
    with _timing_stage(timing, "Indicator calculation", Path(file_path).name):
        if benchmark_df is not None:
            df = add_market_regime(df, benchmark_df)
        df = add_entry_indicators(
            df,
            strategy_name=strategy_name,
            hurst_filter_mode=hurst_filter_mode,
            hurst_threshold=hurst_threshold,
            rsi_length=rsi_length,
            rsi_entry_threshold=rsi_entry_threshold,
            bollinger_length=bollinger_length,
            bollinger_std=bollinger_std,
            bb_entry_tolerance_atr=bb_entry_tolerance_atr,
            use_quality_filter=use_quality_filter,
            min_price_filter=min_price_filter,
            min_atr_pct_filter=min_atr_pct_filter,
            max_atr_pct_filter=max_atr_pct_filter,
            min_avg_dollar_volume=min_avg_dollar_volume,
            avg_volume_lookback=avg_volume_lookback,
            trend_sma_length=trend_sma_length,
            setup_lookback=setup_lookback,
            exit_sma_length=exit_sma_length,
            take_profit_atr_multiplier=take_profit_atr_multiplier,
            disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
            exit_mode=exit_mode,
            confirmation_mode=confirmation_mode,
            confirmation_lookback=confirmation_lookback,
            confirmation_min_rebound_atr=confirmation_min_rebound_atr,
            donchian_entry_lookback_days=donchian_entry_lookback_days,
            donchian_exit_lookback_days=donchian_exit_lookback_days,
            donchian_top_n=donchian_top_n,
            donchian_rebalance_frequency=donchian_rebalance_frequency,
            donchian_trend_sma_days=donchian_trend_sma_days,
            donchian_use_market_regime=donchian_use_market_regime,
            donchian_market_regime_sma_days=donchian_market_regime_sma_days,
            donchian_min_price=donchian_min_price,
            donchian_max_atr_pct=donchian_max_atr_pct,
            use_entry_context_filter=use_entry_context_filter,
            max_entry_gap_pct=max_entry_gap_pct,
            max_entry_bb_distance_atr=max_entry_bb_distance_atr,
            max_entry_signal_range_pct=max_entry_signal_range_pct,
            min_entry_close_position_in_range=min_entry_close_position_in_range,
            max_entry_close_position_in_range=max_entry_close_position_in_range,
            rs_lookback_days=rs_lookback_days,
            rs_top_n=rs_top_n,
            rs_rebalance_frequency=rs_rebalance_frequency,
            rs_score_mode=rs_score_mode,
            rs_vol_lookback_days=rs_vol_lookback_days,
            rs_trend_sma_days=rs_trend_sma_days,
            rs_use_market_regime=rs_use_market_regime,
            rs_market_regime_sma_days=rs_market_regime_sma_days,
            rs_min_price=rs_min_price,
            rs_max_atr_pct=rs_max_atr_pct,
        )
    df.attrs.update(
        _strategy_cache_config(
            hurst_filter_mode,
            hurst_threshold,
            strategy_name=strategy_name,
            use_market_calendar=use_market_calendar,
            include_partial_session_bars=include_partial_session_bars,
            rsi_length=rsi_length,
            rsi_entry_threshold=rsi_entry_threshold,
            bollinger_length=bollinger_length,
            bollinger_std=bollinger_std,
            bb_entry_tolerance_atr=bb_entry_tolerance_atr,
            use_quality_filter=use_quality_filter,
            min_price_filter=min_price_filter,
            min_atr_pct_filter=min_atr_pct_filter,
            max_atr_pct_filter=max_atr_pct_filter,
            min_avg_dollar_volume=min_avg_dollar_volume,
            avg_volume_lookback=avg_volume_lookback,
            trend_sma_length=trend_sma_length,
            setup_lookback=setup_lookback,
            exit_sma_length=exit_sma_length,
            take_profit_atr_multiplier=take_profit_atr_multiplier,
            disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
            exit_mode=exit_mode,
            confirmation_mode=confirmation_mode,
            confirmation_lookback=confirmation_lookback,
            confirmation_min_rebound_atr=confirmation_min_rebound_atr,
            use_entry_context_filter=use_entry_context_filter,
            max_entry_gap_pct=max_entry_gap_pct,
            max_entry_bb_distance_atr=max_entry_bb_distance_atr,
            max_entry_signal_range_pct=max_entry_signal_range_pct,
            min_entry_close_position_in_range=min_entry_close_position_in_range,
            max_entry_close_position_in_range=max_entry_close_position_in_range,
        )
    )
    df.attrs["calendar_fallback"] = calendar_fallback
    if return_intraday:
        return df, intraday_df
    return df


def load_clean_intraday_dataframe(
    file_path,
    use_market_calendar=True,
    session_windows=None,
    calendar_fallback=None,
    timing=None,
):
    df = read_ohlcv_csv_fast(file_path, timing=timing)
    return prepare_rth_intraday_bars(
        df,
        use_market_calendar=use_market_calendar,
        session_windows=session_windows,
        calendar_fallback=calendar_fallback,
        timing=timing,
    )


class ScannerBacktester:
    def __init__(
        self,
        df,
        verbose=True,
        commission_rate=0.001,
        slippage_mu=0.05,
    ):
        self.df = df
        self.verbose = verbose
        self.komisyon_oran = float(commission_rate)
        self.slippage_mu = float(slippage_mu)
        if self.komisyon_oran < 0:
            raise ValueError("commission_rate negatif olamaz.")
        if self.slippage_mu < 0:
            raise ValueError("slippage_mu negatif olamaz.")
        self.trade_log = []
        self.equity_curve = []
        self.entry_count = 0

    def _calculate_slippage_amount(self, raw_price, atr_value):
        if pd.notna(atr_value) and float(atr_value) > 0:
            return float(atr_value) * self.slippage_mu
        return float(raw_price) * 0.0005

    def run_backtest(self):
        self.equity_curve = []
        self.trade_log = []
        self.entry_count = 0

        in_position = False
        entry_price = 0.0
        disaster_stop = np.nan
        take_profit_target = np.nan
        bars_held = 0
        lot_size = 0.0
        current_trade_pnl = 0.0

        current_cash = 10000.0

        if self.verbose:
            print(
                "ENTRY: RSI(3)<10 exhaustion + Close > SMA(200) macro uptrend "
                "(on bar close)"
            )
            print(
                "EXIT: Close > SMA(20), 2 ATR target, static 2 ATR disaster stop, "
                "or 10 bar time stop; "
                "market order at next bar Open"
            )

        for i in range(1, len(self.df)):
            row = self.df.iloc[i]
            signal_row = self.df.iloc[i - 1]
            current_price = float(row["Close"])

            # Exit is confirmed at candle i-1 close and executed at candle i
            # open. No intra-bar High/Low ordering is assumed.
            take_profit_hit = bool(signal_row["Take_Profit"]) or (
                pd.notna(take_profit_target)
                and float(signal_row["Close"]) >= take_profit_target
            )
            disaster_stop_hit = (
                pd.notna(disaster_stop)
                and float(signal_row["Close"]) < disaster_stop
            )
            time_stop_hit = (
                not take_profit_hit
                and not disaster_stop_hit
                and bars_held > TIME_STOP_BARS
            )
            if in_position and (take_profit_hit or disaster_stop_hit or time_stop_hit):
                raw_exit_price = float(row["Open"])
                exit_atr = signal_row["SuperTrend_ATR_22"]
                slippage_amount = self._calculate_slippage_amount(
                    raw_exit_price,
                    exit_atr,
                )
                exit_price = raw_exit_price - slippage_amount
                gross_pnl = (exit_price - entry_price) * lot_size
                exit_commission = exit_price * lot_size * self.komisyon_oran
                net_pnl = gross_pnl - exit_commission
                current_cash += net_pnl
                current_trade_pnl += net_pnl
                self.trade_log.append(current_trade_pnl)
                in_position = False
                disaster_stop = np.nan
                take_profit_target = np.nan
                bars_held = 0
                current_trade_pnl = 0.0

            # The signal is confirmed at candle i-1 close. Execution happens
            # only at candle i open, so the signal candle cannot fill itself.
            if not in_position and bool(signal_row["ENTRY_SIGNAL"]):
                signal_stop = signal_row["Disaster_Stop"]
                raw_entry = float(row["Open"])
                entry_atr = signal_row["SuperTrend_ATR_22"]
                risk_per_share = raw_entry - signal_stop
                risk_capital = current_cash * 0.02
                lot_size = (
                    risk_capital / risk_per_share
                    if pd.notna(signal_stop) and risk_per_share > 0
                    else 0
                )

                if lot_size > 0:
                    slippage_amount = self._calculate_slippage_amount(
                        raw_entry,
                        entry_atr,
                    )
                    entry_price = raw_entry + slippage_amount
                    entry_commission = (
                        entry_price * lot_size * self.komisyon_oran
                    )
                    current_cash -= entry_commission
                    current_trade_pnl = -entry_commission
                    disaster_stop = float(signal_stop)
                    take_profit_target = (
                        entry_price + float(signal_row["ATR_Take_Profit_Distance"])
                    )
                    bars_held = 0
                    in_position = True
                    self.entry_count += 1

            if in_position:
                unrealized_pnl = (current_price - entry_price) * lot_size
                self.equity_curve.append(current_cash + unrealized_pnl)
                bars_held += 1
            else:
                self.equity_curve.append(current_cash)

        # Close any still-open position at the test boundary. Without this,
        # unrealized PnL changes Net Return while trade_count remains zero.
        if in_position and self.equity_curve:
            raw_exit_price = float(self.df["Close"].iloc[-1])
            exit_atr = self.df["SuperTrend_ATR_22"].iloc[-1]
            slippage_amount = self._calculate_slippage_amount(
                raw_exit_price,
                exit_atr,
            )
            exit_price = raw_exit_price - slippage_amount
            gross_pnl = (exit_price - entry_price) * lot_size
            exit_commission = exit_price * lot_size * self.komisyon_oran
            net_pnl = gross_pnl - exit_commission
            current_cash += net_pnl
            current_trade_pnl += net_pnl
            self.trade_log.append(current_trade_pnl)
            self.equity_curve[-1] = current_cash

        return self.get_metrics()

    def get_metrics(self):
        if not self.equity_curve:
            return {}

        equity = pd.Series(self.equity_curve, index=self.df.index[1:])
        total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
        max_drawdown = ((equity - equity.cummax()) / equity.cummax()).min() * 100

        trades = pd.Series(self.trade_log, dtype=float)
        winners = trades[trades > 0]
        losers = trades[trades < 0]
        trade_count = self.entry_count
        win_rate = len(winners) / trade_count * 100 if trade_count else 0.0
        average_win = winners.mean() if len(winners) else 0.0
        average_loss = losers.mean() if len(losers) else 0.0
        if trade_count == 0:
            profit_factor = 0.0
        elif len(losers) and abs(losers.sum()) > 0:
            profit_factor = winners.sum() / abs(losers.sum())
        else:
            profit_factor = float("inf")
        expectancy = (
            (win_rate / 100) * average_win
            + (1 - win_rate / 100) * average_loss
            if trade_count
            else 0.0
        )

        days = max((equity.index[-1] - equity.index[0]).days, 1)
        years = days / 365.25
        cagr = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100

        daily_returns = equity.resample("1D").last().pct_change().dropna()
        volatility = daily_returns.std()
        sharpe = (
            daily_returns.mean() / volatility * np.sqrt(252)
            if pd.notna(volatility) and volatility > 0
            else 0.0
        )
        downside = daily_returns[daily_returns < 0].std()
        sortino = (
            daily_returns.mean() / downside * np.sqrt(252)
            if pd.notna(downside) and downside > 0
            else 0.0
        )
        calmar = total_return / abs(max_drawdown) if max_drawdown else 0.0
        mar = cagr / abs(max_drawdown) if max_drawdown else 0.0

        return {
            "net_getiri": round(total_return, 2),
            "max_drawdown": round(max_drawdown, 2),
            "toplam_islem": trade_count,
            "win_rate": round(win_rate, 1),
            "ort_kazanc": round(average_win, 4),
            "ort_kayip": round(average_loss, 4),
            "profit_factor": round(profit_factor, 2),
            "calmar_orani": round(calmar, 2),
            "cagr": round(cagr, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "mar_ratio": round(mar, 2),
            "expectancy": round(expectancy, 2),
        }


def _find_benchmark_file(csv_files, benchmark_symbol):
    symbol = _normalize_symbol(benchmark_symbol)
    for file_path in csv_files:
        if _symbol_from_path(file_path) == symbol:
            return file_path
    return None


def _normalize_symbol(symbol):
    normalized = str(symbol).strip().upper()
    if normalized.endswith(".US"):
        normalized = normalized[:-3]
    for delimiter in ("_", "-", " "):
        if delimiter in normalized:
            normalized = normalized.split(delimiter, 1)[0]
    return normalized


def _symbol_from_path(file_path):
    return _normalize_symbol(Path(file_path).stem)


def _read_universe_symbols_file(file_path):
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Evren sembol dosyasi bulunamadi: {file_path}")

    if path.suffix.lower() == ".csv":
        symbols_df = pd.read_csv(path)
        symbol_column = next(
            (
                column
                for column in symbols_df.columns
                if str(column).strip().lower() == "symbol"
            ),
            None,
        )
        if symbol_column is None:
            if symbols_df.empty or len(symbols_df.columns) == 0:
                return set()
            symbol_column = symbols_df.columns[0]
        values = symbols_df[symbol_column].dropna().tolist()
    else:
        values = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return {_normalize_symbol(value) for value in values if str(value).strip()}


def _build_allowed_symbol_set(universe_symbols_file, allowed_symbols):
    allowed_sets = []
    if universe_symbols_file is not None:
        allowed_sets.append(_read_universe_symbols_file(universe_symbols_file))
    if allowed_symbols is not None:
        allowed_sets.append(
            {_normalize_symbol(symbol) for symbol in allowed_symbols if str(symbol).strip()}
        )
    if not allowed_sets:
        return None
    return set.intersection(*allowed_sets) if allowed_sets else None


def _build_excluded_symbol_set(excluded_symbols):
    if excluded_symbols is None:
        return set()
    return {
        _normalize_symbol(symbol)
        for symbol in excluded_symbols
        if str(symbol).strip()
    }


def _select_universe_files(csv_files, allowed_symbol_set, excluded_symbol_set=None):
    excluded_symbol_set = excluded_symbol_set or set()
    selected_files = []
    excluded_rows = []
    seen_selected_symbols = set()
    for file_path in sorted(csv_files, key=_universe_file_sort_key):
        symbol = _symbol_from_path(file_path)
        excluded_reason = None
        if allowed_symbol_set is not None and symbol not in allowed_symbol_set:
            excluded_reason = "not_in_allowed_universe"
        elif symbol in excluded_symbol_set:
            excluded_reason = "excluded_symbols"
        elif symbol in seen_selected_symbols:
            excluded_reason = "duplicate_symbol"

        if excluded_reason is None:
            selected_files.append(file_path)
            seen_selected_symbols.add(symbol)
        else:
            excluded_rows.append(
                {
                    "Symbol": symbol,
                    "File": str(file_path),
                    "Reason": excluded_reason,
                }
            )
    return selected_files, excluded_rows


def _universe_file_sort_key(file_path):
    symbol = _symbol_from_path(file_path)
    stem = Path(file_path).stem.upper()
    if stem == symbol:
        priority = 0
    elif stem == f"{symbol}.US":
        priority = 1
    else:
        priority = 2
    return (symbol, priority, str(file_path).upper())


def _universe_cache_digest(selected_symbols, use_market_calendar):
    digest = hashlib.sha1(
        (
            "|".join(sorted(selected_symbols))
            + f"|calendar={bool(use_market_calendar)}"
        ).encode("utf-8")
    ).hexdigest()[:12]
    return digest


def _cache_path_for_universe(
    data_folder_path,
    selected_symbols,
    filtered,
    use_market_calendar=True,
):
    digest = _universe_cache_digest(selected_symbols, use_market_calendar)
    return data_folder_path / f"compiled_universe_data_{digest}.pkl"


def _intraday_cache_path_for_universe(
    data_folder_path,
    selected_symbols,
    use_market_calendar=True,
):
    digest = _universe_cache_digest(selected_symbols, use_market_calendar)
    return data_folder_path / f"compiled_intraday_data_{digest}.pkl"


def _cache_config_hash(
    file_path,
    hurst_filter_mode,
    hurst_threshold,
    use_market_calendar,
    include_partial_session_bars,
    strategy_name=DEFAULT_STRATEGY_NAME,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    bb_entry_tolerance_atr=0.0,
    trend_sma_length=50,
    setup_lookback=3,
    use_quality_filter=True,
    min_price_filter=10.0,
    min_atr_pct_filter=0.2,
    max_atr_pct_filter=12.0,
    min_avg_dollar_volume=20_000_000,
    avg_volume_lookback=20,
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
    stat = Path(file_path).stat()
    csm_params = {
        "csm_lookback_days": csm_lookback_days,
        "csm_top_n": csm_top_n,
        "csm_rebalance_frequency": csm_rebalance_frequency,
        "csm_trend_sma_days": csm_trend_sma_days,
        "csm_use_market_regime": csm_use_market_regime,
        "csm_market_regime_sma_days": csm_market_regime_sma_days,
        "csm_min_price": csm_min_price,
        "csm_max_atr_pct": csm_max_atr_pct,
    }
    dm_params = _normalize_dual_momentum_params(
        {
            "dm_lookback_days": dm_lookback_days,
            "dm_top_n": dm_top_n,
            "dm_rebalance_frequency": dm_rebalance_frequency,
            "dm_absolute_momentum_threshold": dm_absolute_momentum_threshold,
            "dm_trend_sma_days": dm_trend_sma_days,
            "dm_use_market_regime": dm_use_market_regime,
            "dm_market_regime_sma_days": dm_market_regime_sma_days,
            "dm_min_price": dm_min_price,
            "dm_max_atr_pct": dm_max_atr_pct,
        }
    )
    donchian_params = _normalize_donchian_breakout_params(
        {
            "donchian_entry_lookback_days": donchian_entry_lookback_days,
            "donchian_exit_lookback_days": donchian_exit_lookback_days,
            "donchian_top_n": donchian_top_n,
            "donchian_rebalance_frequency": donchian_rebalance_frequency,
            "donchian_trend_sma_days": donchian_trend_sma_days,
            "donchian_use_market_regime": donchian_use_market_regime,
            "donchian_market_regime_sma_days": donchian_market_regime_sma_days,
            "donchian_min_price": donchian_min_price,
            "donchian_max_atr_pct": donchian_max_atr_pct,
        }
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
    config = "|".join(
        [
            STRATEGY_CACHE_VERSION,
            validate_strategy_name(strategy_name),
            _normalize_hurst_filter_mode(hurst_filter_mode),
            str(float(hurst_threshold)),
            str(bool(use_market_calendar)),
            str(bool(include_partial_session_bars)),
            str(PRIMARY_BAR_MINUTES),
            str(int(rsi_length)),
            str(float(rsi_entry_threshold)),
            str(int(bollinger_length)),
            str(float(bollinger_std)),
            str(float(bb_entry_tolerance_atr)),
            str(int(trend_sma_length)),
            str(int(setup_lookback)),
            str(bool(use_quality_filter)),
            str(float(min_price_filter)),
            str(float(min_atr_pct_filter)),
            str(float(max_atr_pct_filter)),
            str(float(min_avg_dollar_volume)),
            str(int(avg_volume_lookback)),
            str(int(exit_sma_length)),
            str(float(take_profit_atr_multiplier)),
            str(float(disaster_stop_atr_multiplier)),
            str(exit_mode),
            _normalize_confirmation_mode(confirmation_mode),
            str(int(confirmation_lookback)),
            str(float(confirmation_min_rebound_atr)),
            str(context_params["use_entry_context_filter"]),
            str(context_params["max_entry_gap_pct"]),
            str(context_params["max_entry_bb_distance_atr"]),
            str(context_params["max_entry_signal_range_pct"]),
            str(context_params["min_entry_close_position_in_range"]),
            str(context_params["max_entry_close_position_in_range"]),
            str(rs_params["rs_lookback_days"]),
            str(rs_params["rs_top_n"]),
            str(rs_params["rs_rebalance_frequency"]),
            str(rs_params["rs_score_mode"]),
            str(rs_params["rs_vol_lookback_days"]),
            str(rs_params["rs_trend_sma_days"]),
            str(rs_params["rs_use_market_regime"]),
            str(rs_params["rs_market_regime_sma_days"]),
            str(rs_params["rs_min_price"]),
            str(rs_params["rs_max_atr_pct"]),
            str(dm_params["dm_lookback_days"]),
            str(dm_params["dm_top_n"]),
            str(dm_params["dm_rebalance_frequency"]),
            str(dm_params["dm_absolute_momentum_threshold"]),
            str(dm_params["dm_trend_sma_days"]),
            str(dm_params["dm_use_market_regime"]),
            str(dm_params["dm_market_regime_sma_days"]),
            str(dm_params["dm_min_price"]),
            str(dm_params["dm_max_atr_pct"]),
            str(donchian_entry_lookback_days),
            str(donchian_exit_lookback_days),
            str(donchian_top_n),
            str(donchian_rebalance_frequency),
            str(donchian_trend_sma_days),
            str(donchian_use_market_regime),
            str(donchian_market_regime_sma_days),
            str(donchian_min_price),
            str(donchian_max_atr_pct),
            str(int(stat.st_mtime_ns)),
            str(int(stat.st_size)),
        ]
    )
    return hashlib.sha1(config.encode("utf-8")).hexdigest()[:16]


def _safe_cache_symbol(symbol):
    return "".join(
        char if char.isalnum() or char in {"_", "-"} else "_"
        for char in _normalize_symbol(symbol)
    )


def _symbol_cache_paths(
    data_folder_path,
    symbol,
    timeframe,
    config_hash,
):
    cache_dir = data_folder_path / ".cache"
    stem = f"{_safe_cache_symbol(symbol)}_{timeframe}_{config_hash}"
    return {
        "dir": cache_dir,
        "parquet": cache_dir / f"{stem}.parquet",
        "pickle": cache_dir / f"{stem}.pkl",
        "meta": cache_dir / f"{stem}.meta.pkl",
    }


def _write_dataframe_cache(df, paths):
    paths["dir"].mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(paths["parquet"])
        pd.to_pickle(dict(df.attrs), paths["meta"])
        return paths["parquet"]
    except Exception:
        if paths["parquet"].exists():
            try:
                paths["parquet"].unlink()
            except OSError:
                pass
        if paths["meta"].exists():
            try:
                paths["meta"].unlink()
            except OSError:
                pass
        pd.to_pickle({"data": df, "attrs": dict(df.attrs)}, paths["pickle"])
        return paths["pickle"]


def _read_dataframe_cache(paths):
    if paths["parquet"].exists():
        df = pd.read_parquet(paths["parquet"])
        if paths["meta"].exists():
            attrs = pd.read_pickle(paths["meta"])
            if isinstance(attrs, dict):
                df.attrs.update(attrs)
        return df, paths["parquet"]
    if paths["pickle"].exists():
        payload = pd.read_pickle(paths["pickle"])
        if isinstance(payload, dict) and "data" in payload:
            df = payload["data"]
            attrs = payload.get("attrs", {})
            if isinstance(attrs, dict):
                df.attrs.update(attrs)
            return df, paths["pickle"]
        return payload, paths["pickle"]
    return None, None


def _per_symbol_cache_paths_for_file(
    data_folder_path,
    file_path,
    timeframe,
    hurst_filter_mode,
    hurst_threshold,
    use_market_calendar,
    include_partial_session_bars,
    strategy_name=DEFAULT_STRATEGY_NAME,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    bb_entry_tolerance_atr=0.0,
    trend_sma_length=50,
    setup_lookback=3,
    use_quality_filter=True,
    min_price_filter=10.0,
    min_atr_pct_filter=0.2,
    max_atr_pct_filter=12.0,
    min_avg_dollar_volume=20_000_000,
    avg_volume_lookback=20,
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
    symbol = _symbol_from_path(file_path)
    config_hash = _cache_config_hash(
        file_path,
        hurst_filter_mode,
        hurst_threshold,
        use_market_calendar,
        include_partial_session_bars,
        strategy_name=strategy_name,
        rsi_length=rsi_length,
        rsi_entry_threshold=rsi_entry_threshold,
        bollinger_length=bollinger_length,
        bollinger_std=bollinger_std,
        bb_entry_tolerance_atr=bb_entry_tolerance_atr,
        use_quality_filter=use_quality_filter,
        min_price_filter=min_price_filter,
        min_atr_pct_filter=min_atr_pct_filter,
        max_atr_pct_filter=max_atr_pct_filter,
        min_avg_dollar_volume=min_avg_dollar_volume,
        avg_volume_lookback=avg_volume_lookback,
        trend_sma_length=trend_sma_length,
        setup_lookback=setup_lookback,
        exit_sma_length=exit_sma_length,
        take_profit_atr_multiplier=take_profit_atr_multiplier,
        disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
        exit_mode=exit_mode,
        confirmation_mode=confirmation_mode,
        confirmation_lookback=confirmation_lookback,
        confirmation_min_rebound_atr=confirmation_min_rebound_atr,
        use_entry_context_filter=use_entry_context_filter,
        max_entry_gap_pct=max_entry_gap_pct,
        max_entry_bb_distance_atr=max_entry_bb_distance_atr,
        max_entry_signal_range_pct=max_entry_signal_range_pct,
        min_entry_close_position_in_range=min_entry_close_position_in_range,
        max_entry_close_position_in_range=max_entry_close_position_in_range,
        rs_lookback_days=rs_lookback_days,
        rs_top_n=rs_top_n,
        rs_rebalance_frequency=rs_rebalance_frequency,
        rs_score_mode=rs_score_mode,
        rs_vol_lookback_days=rs_vol_lookback_days,
        rs_trend_sma_days=rs_trend_sma_days,
        rs_use_market_regime=rs_use_market_regime,
        rs_market_regime_sma_days=rs_market_regime_sma_days,
        rs_min_price=rs_min_price,
        rs_max_atr_pct=rs_max_atr_pct,
        donchian_entry_lookback_days=donchian_entry_lookback_days,
        donchian_exit_lookback_days=donchian_exit_lookback_days,
        donchian_top_n=donchian_top_n,
        donchian_rebalance_frequency=donchian_rebalance_frequency,
        donchian_trend_sma_days=donchian_trend_sma_days,
        donchian_use_market_regime=donchian_use_market_regime,
        donchian_market_regime_sma_days=donchian_market_regime_sma_days,
        donchian_min_price=donchian_min_price,
        donchian_max_atr_pct=donchian_max_atr_pct,
    )
    return symbol, _symbol_cache_paths(data_folder_path, symbol, timeframe, config_hash)


def _datetime_bounds_for_csv_file(file_path):
    delimiter = _detect_csv_delimiter(file_path)
    header = pd.read_csv(file_path, sep=delimiter, engine="c", nrows=0)
    original_by_normalized = {
        str(column).strip().lower(): column for column in header.columns
    }
    columns = list(original_by_normalized)
    date_columns = [column for column in columns if "date" in column]
    time_columns = [column for column in columns if "time" in column]
    datetime_columns = [
        column for column in columns if "date" in column or "time" in column
    ]
    split_pair = [
        (date_column, time_column)
        for date_column in date_columns
        for time_column in time_columns
        if date_column != time_column
    ]
    if split_pair:
        date_column, time_column = split_pair[0]
        usecols = [
            original_by_normalized[date_column],
            original_by_normalized[time_column],
        ]
    elif datetime_columns:
        date_column = None
        time_column = None
        usecols = [original_by_normalized[datetime_columns[0]]]
    else:
        raise ValueError("CSV dosyasinda tarih/zaman sutunu bulunamadi.")

    date_frame = pd.read_csv(
        file_path,
        sep=delimiter,
        engine="c",
        usecols=usecols,
    )
    date_frame.columns = date_frame.columns.str.strip().str.lower()
    if split_pair:
        index = _parse_datetime_fast(
            date_frame[date_column],
            date_frame[time_column],
        )
    else:
        index = _parse_datetime_fast(date_frame[datetime_columns[0]])
    index = index[~index.isna()]
    if index.empty:
        raise ValueError("Tarih/zaman sutununda gecerli deger bulunamadi.")
    local_index = index.tz_convert(MARKET_TIMEZONE)
    return local_index.min(), local_index.max()


def _build_run_session_windows_from_files(selected_csv_files, use_market_calendar=True):
    if not use_market_calendar:
        return None, None
    if not selected_csv_files:
        return None, None

    bounds = []
    errors = []
    for file_path in selected_csv_files:
        try:
            bounds.append(_datetime_bounds_for_csv_file(file_path))
        except Exception as error:
            errors.append(f"{Path(file_path).name}: {error}")

    if not bounds:
        if errors:
            print(
                "Calendar cache tarih araligi okunamadi; sembol bazli fallback "
                f"kullanilacak. Ornek: {errors[0]}"
            )
        return None, None

    start = min(bound[0] for bound in bounds)
    end = max(bound[1] for bound in bounds)
    index = pd.DatetimeIndex([start, end])
    windows, calendar_fallback = _build_session_windows(
        index,
        use_market_calendar=use_market_calendar,
    )
    if errors:
        print(
            "Calendar cache bazi dosyalari okuyamadi; mevcut aralik kullaniliyor. "
            f"Ornek: {errors[0]}"
        )
    return windows, calendar_fallback


def _empty_buyhold_metrics():
    return {
        "Return (%)": np.nan,
        "CAGR (%)": np.nan,
        "MaxDD (%)": np.nan,
    }


def _benchmark_summary_for_equity_curve(
    asset_data,
    equity_curve,
    strategy_return,
    benchmark_data=None,
    benchmark_symbol="QQQ",
):
    summary = {}
    if not equity_curve:
        summary.update(_prefixed_benchmark_metrics("QQQ", _empty_buyhold_metrics()))
        summary.update(_prefixed_benchmark_metrics("SPY", _empty_buyhold_metrics()))
        summary.update(
            {
                "EqualWeight Universe Return (%)": np.nan,
                "EqualWeight Universe CAGR (%)": np.nan,
                "EqualWeight Universe MaxDD (%)": np.nan,
                "Strategy Excess Return vs QQQ (%)": np.nan,
                "Strategy Excess Return vs EqualWeight (%)": np.nan,
                "Benchmark Comparison": "skipped_empty_equity_curve",
            }
        )
        return summary

    strategy_equity = pd.Series(
        [value for _, value in equity_curve],
        index=[timestamp for timestamp, _ in equity_curve],
        dtype=float,
    )
    benchmark_start = strategy_equity.index[0]
    benchmark_end = strategy_equity.index[-1]
    if (
        isinstance(benchmark_start, pd.Timestamp)
        and benchmark_start == benchmark_start.normalize()
    ):
        benchmark_start = benchmark_start + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    if (
        isinstance(benchmark_end, pd.Timestamp)
        and benchmark_end == benchmark_end.normalize()
    ):
        benchmark_end = benchmark_end + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    for symbol in ["QQQ", "SPY"]:
        close_series = None
        asset = _find_asset_data(asset_data, symbol)
        if asset is not None and "Close" in asset:
            close_series = asset["Close"]
        elif (
            benchmark_data is not None
            and "Close" in benchmark_data
            and _normalize_symbol(symbol) == _normalize_symbol(benchmark_symbol)
        ):
            close_series = benchmark_data["Close"]
        if close_series is None:
            buyhold_metrics = _empty_buyhold_metrics()
        else:
            try:
                buyhold_metrics = calculate_buyhold_metrics(
                    close_series,
                    benchmark_start,
                    benchmark_end,
                )
            except Exception:
                buyhold_metrics = _empty_buyhold_metrics()
        summary.update(_prefixed_benchmark_metrics(symbol, buyhold_metrics))

    try:
        equal_weight_metrics = calculate_equal_weight_benchmark(
            asset_data,
            benchmark_start,
            benchmark_end,
        )
    except Exception:
        equal_weight_metrics = _empty_buyhold_metrics()
    summary.update(
        {
            "EqualWeight Universe Return (%)": equal_weight_metrics["Return (%)"],
            "EqualWeight Universe CAGR (%)": equal_weight_metrics["CAGR (%)"],
            "EqualWeight Universe MaxDD (%)": equal_weight_metrics["MaxDD (%)"],
        }
    )
    summary["Strategy Excess Return vs QQQ (%)"] = (
        strategy_return - summary["QQQ BuyHold Return (%)"]
        if pd.notna(summary["QQQ BuyHold Return (%)"])
        else np.nan
    )
    summary["Strategy Excess Return vs EqualWeight (%)"] = (
        strategy_return - summary["EqualWeight Universe Return (%)"]
        if pd.notna(summary["EqualWeight Universe Return (%)"])
        else np.nan
    )
    summary["Benchmark Comparison"] = "computed"
    return summary


def _find_asset_data(asset_data, benchmark_symbol):
    symbol = _normalize_symbol(benchmark_symbol)
    for asset_symbol, data in asset_data.items():
        stem = _normalize_symbol(asset_symbol)
        if stem == symbol:
            return data
    return None


def _load_benchmark_close(
    asset_data,
    csv_files,
    benchmark_symbol,
    hurst_filter_mode,
    hurst_threshold,
    use_market_calendar=True,
    include_partial_session_bars=False,
    strategy_name=DEFAULT_STRATEGY_NAME,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    bb_entry_tolerance_atr=0.0,
    trend_sma_length=50,
    setup_lookback=3,
    use_quality_filter=True,
    min_price_filter=10.0,
    min_atr_pct_filter=0.2,
    max_atr_pct_filter=12.0,
    min_avg_dollar_volume=20_000_000,
    avg_volume_lookback=20,
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
):
    asset = _find_asset_data(asset_data, benchmark_symbol)
    if asset is not None and "Close" in asset:
        return asset["Close"]

    file_path = _find_benchmark_file(csv_files, benchmark_symbol)
    if file_path is None:
        print(f"Uyari: {benchmark_symbol} benchmark dosyasi bulunamadi.")
        return None

    try:
        data = build_mega_dataframe(
            file_path,
            hurst_filter_mode=hurst_filter_mode,
            hurst_threshold=hurst_threshold,
            use_market_calendar=use_market_calendar,
            include_partial_session_bars=include_partial_session_bars,
            strategy_name=strategy_name,
            rsi_length=rsi_length,
            rsi_entry_threshold=rsi_entry_threshold,
            bollinger_length=bollinger_length,
            bollinger_std=bollinger_std,
            bb_entry_tolerance_atr=bb_entry_tolerance_atr,
            use_quality_filter=use_quality_filter,
            min_price_filter=min_price_filter,
            min_atr_pct_filter=min_atr_pct_filter,
            max_atr_pct_filter=max_atr_pct_filter,
            min_avg_dollar_volume=min_avg_dollar_volume,
            avg_volume_lookback=avg_volume_lookback,
            trend_sma_length=trend_sma_length,
            setup_lookback=setup_lookback,
            exit_sma_length=exit_sma_length,
            take_profit_atr_multiplier=take_profit_atr_multiplier,
            disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
            exit_mode=exit_mode,
            confirmation_mode=confirmation_mode,
            confirmation_lookback=confirmation_lookback,
            confirmation_min_rebound_atr=confirmation_min_rebound_atr,
        )
        return data["Close"]
    except Exception as error:
        print(f"Uyari: {benchmark_symbol} benchmark hazirlanamadi: {error}")
        return None


def _load_intraday_data_for_files(
    selected_csv_files,
    use_market_calendar=True,
    session_windows=None,
    calendar_fallback=None,
    timing=None,
):
    intraday_data = {}
    errors = []
    for file_path in selected_csv_files:
        symbol = _symbol_from_path(file_path)
        try:
            intraday_data[symbol] = load_clean_intraday_dataframe(
                file_path,
                use_market_calendar=use_market_calendar,
                session_windows=session_windows,
                calendar_fallback=calendar_fallback,
                timing=timing,
            )
        except Exception as error:
            errors.append(f"{symbol}: {error}")
    if errors:
        raise ValueError(
            "Intrabar execution icin 1m veri hazirlanamadi. "
            + " | ".join(errors[:5])
        )
    return intraday_data


def _normalize_evaluation_mode(evaluation_mode):
    mode = str(evaluation_mode).lower()
    if mode not in {"single", "split", "walk_forward"}:
        raise ValueError(
            "evaluation_mode 'single', 'split' veya 'walk_forward' olmali."
        )
    return mode


def _slice_asset_data_for_period(asset_data, start, end, warmup_bars=300):
    sliced = {}
    for symbol, data in asset_data.items():
        if data.empty:
            continue
        start_ts = _align_timestamp_to_index(start, data.index)
        end_ts = _align_timestamp_to_index(end, data.index)
        start_pos = 0
        if start_ts is not None:
            start_pos = int(data.index.searchsorted(start_ts, side="left"))
        warmup_start_pos = max(0, start_pos - int(warmup_bars))
        period = data.iloc[warmup_start_pos:].copy()
        if end_ts is not None:
            period = period.loc[period.index <= end_ts]
        if not period.empty:
            sliced[symbol] = period
    return sliced


def _prefix_metrics(metrics, prefix):
    return {f"{prefix} {key}": value for key, value in metrics.items()}


def _run_portfolio_period(
    asset_data,
    ranking_mode,
    period_name,
    start=None,
    end=None,
    warmup_bars=300,
    execution_mode="bar_close",
    intraday_data=None,
    timing=None,
    exit_mode="sma_or_target",
    time_stop_bars=10,
    fast_backtest=True,
    commission_rate=0.001,
    slippage_mu=0.05,
    risk_off_exit_mode="hold_existing",
):
    period_data = _slice_asset_data_for_period(
        asset_data,
        start,
        end,
        warmup_bars=warmup_bars,
    )
    if not period_data:
        print(f"Uyari: {period_name} donemi icin kullanilabilir veri yok.")
        return None, {}
    if timing is None:
        manager = PortfolioManager(
            asset_data=period_data,
            initial_cash=10000.0,
            max_open_positions=5,
            risk_per_trade=0.02,
            ranking_mode=ranking_mode,
            evaluation_start=start,
            evaluation_end=end,
            execution_mode=execution_mode,
            intraday_data=intraday_data,
            verbose=False,
            exit_mode=exit_mode,
            time_stop_bars=time_stop_bars,
            fast_backtest=fast_backtest,
            commission_rate=commission_rate,
            slippage_mu=slippage_mu,
            risk_off_exit_mode=risk_off_exit_mode,
        )
        metrics = manager.run_backtest()
    else:
        with timing.stage("PortfolioManager init", period_name):
            manager = PortfolioManager(
                asset_data=period_data,
                initial_cash=10000.0,
                max_open_positions=5,
                risk_per_trade=0.02,
                ranking_mode=ranking_mode,
                evaluation_start=start,
                evaluation_end=end,
                execution_mode=execution_mode,
                intraday_data=intraday_data,
                verbose=False,
                exit_mode=exit_mode,
                time_stop_bars=time_stop_bars,
                fast_backtest=fast_backtest,
                commission_rate=commission_rate,
                slippage_mu=slippage_mu,
                risk_off_exit_mode=risk_off_exit_mode,
            )
        with timing.stage("PortfolioManager run_backtest", period_name):
            metrics = manager.run_backtest()
    if not metrics:
        print(
            f"Uyari: {period_name} donemi icin sonuc uretilemedi. "
            "Warmup barlari korunur, fakat trade/equity sadece degerlendirme "
            "araliginda raporlanir."
        )
        return manager, {}
    metrics = {
        "Period": period_name,
        "Evaluation Start": start,
        "Evaluation End": end,
        "Warmup Bars Preserved": warmup_bars,
        **metrics,
    }
    return manager, metrics


def _common_asset_index(asset_data):
    indexes = [data.index for data in asset_data.values() if not data.empty]
    if not indexes:
        return pd.DatetimeIndex([])
    common_index = indexes[0]
    for index in indexes[1:]:
        common_index = common_index.union(index)
    return common_index.sort_values()


def _build_walk_forward_folds(asset_data, train_months, test_months):
    index = _common_asset_index(asset_data)
    if index.empty:
        return []
    cursor = index.min()
    last_timestamp = index.max()
    folds = []
    fold_no = 1
    while True:
        train_start = cursor
        train_end = train_start + pd.DateOffset(months=int(train_months))
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=int(test_months))
        if test_start > last_timestamp:
            break
        if test_end > last_timestamp:
            test_end = last_timestamp
        folds.append(
            {
                "Fold No": fold_no,
                "Train Start": train_start,
                "Train End": train_end,
                "Test Start": test_start,
                "Test End": test_end,
            }
        )
        fold_no += 1
        cursor = test_start
        if test_end >= last_timestamp:
            break
    return folds


def run_monte_carlo(
    trades,
    initial_cash,
    simulations=5000,
    seed=42,
    mode="r",
):
    trades = pd.DataFrame(trades)
    if trades.empty:
        raise ValueError("Monte Carlo icin trade bulunamadi.")

    warnings_list = []
    mode = str(mode).lower()
    if mode not in {"r", "pnl"}:
        raise ValueError("Monte Carlo mode 'r' veya 'pnl' olmali.")

    if mode == "r":
        if "R Multiple" not in trades:
            warnings_list.append("R Multiple missing; falling back to Net PnL mode")
            mode = "pnl"
        else:
            samples = pd.to_numeric(trades["R Multiple"], errors="coerce").dropna()
            if samples.empty:
                warnings_list.append("R Multiple invalid; falling back to Net PnL mode")
                mode = "pnl"

    if mode == "pnl":
        if "Net PnL" not in trades:
            raise ValueError("Monte Carlo icin Net PnL kolonu bulunamadi.")
        samples = pd.to_numeric(trades["Net PnL"], errors="coerce").dropna()
        if samples.empty:
            raise ValueError("Monte Carlo icin gecerli Net PnL degeri yok.")

    samples = samples[np.isfinite(samples)]
    if samples.empty:
        raise ValueError("Monte Carlo icin finite trade sonucu yok.")

    trade_count = len(samples)
    if trade_count < 30:
        warnings_list.append("Trade count too low for reliable Monte Carlo")

    rng = np.random.default_rng(seed)
    sample_values = samples.to_numpy(dtype=float)
    risk_amount = float(initial_cash) * 0.02
    path_rows = []

    for simulation_id in range(1, int(simulations) + 1):
        shuffled = rng.choice(sample_values, size=trade_count, replace=True)
        if mode == "r":
            pnl_path = shuffled * risk_amount
        else:
            pnl_path = shuffled

        equity = float(initial_cash) + np.cumsum(pnl_path)
        equity = np.insert(equity, 0, float(initial_cash))
        running_max = np.maximum.accumulate(equity)
        drawdowns = np.divide(
            equity - running_max,
            running_max,
            out=np.zeros_like(equity, dtype=float),
            where=running_max != 0,
        ) * 100
        path_rows.append(
            {
                "simulation_id": simulation_id,
                "final_equity": equity[-1],
                "max_drawdown": drawdowns.min(),
                "min_equity": equity.min(),
                "max_equity": equity.max(),
            }
        )

    paths_summary = pd.DataFrame(path_rows)
    summary = {
        "Simülasyon Sayısı": int(simulations),
        "Monte Carlo Mode": mode,
        "Trade Count": trade_count,
        "Warning": "; ".join(warnings_list),
        "Median Final Equity": paths_summary["final_equity"].median(),
        "5th Percentile Final Equity": paths_summary["final_equity"].quantile(0.05),
        "95th Percentile Final Equity": paths_summary["final_equity"].quantile(0.95),
        "Median MaxDD": paths_summary["max_drawdown"].median(),
        "95th Percentile MaxDD": paths_summary["max_drawdown"].quantile(0.05),
        "Worst MaxDD": paths_summary["max_drawdown"].min(),
        "Probability of Loss": (
            paths_summary["final_equity"].lt(float(initial_cash)).mean() * 100
        ),
        "Probability of MaxDD > 20%": (
            paths_summary["max_drawdown"].le(-20).mean() * 100
        ),
        "Probability of MaxDD > 30%": (
            paths_summary["max_drawdown"].le(-30).mean() * 100
        ),
        "Probability of Ruin": (
            paths_summary["min_equity"].le(float(initial_cash) * 0.5).mean() * 100
        ),
    }
    return pd.DataFrame([summary]), paths_summary


def _output_path(output_dir, stem, output_suffix="", extension=".csv"):
    return Path(output_dir) / f"{stem}{output_suffix}{extension}"


def _write_skipped_trade_log(path, clear_stale_trade_log=True):
    if not clear_stale_trade_log:
        return
    pd.DataFrame(
        [{"Info": "Trade log skipped because quick_mode=True and write_trade_log=False"}]
    ).to_csv(path, index=False)


def _write_monte_carlo_reports(
    trades,
    output_dir,
    initial_cash,
    simulations,
    seed=42,
    output_suffix="",
):
    monte_carlo_report_path = _output_path(
        output_dir,
        "monte_carlo_raporu",
        output_suffix,
    )
    monte_carlo_paths_path = _output_path(
        output_dir,
        "monte_carlo_paths_summary",
        output_suffix,
    )
    try:
        summary, paths_summary = run_monte_carlo(
            trades,
            initial_cash,
            simulations=simulations,
            seed=seed,
            mode="r",
        )
        warning = summary.at[0, "Warning"]
        if isinstance(warning, str) and warning:
            print(f"Monte Carlo uyarisi: {warning}")
        summary.to_csv(monte_carlo_report_path, index=False)
        paths_summary.to_csv(monte_carlo_paths_path, index=False)
        print(f"Monte Carlo raporu: {monte_carlo_report_path}")
        print(f"Monte Carlo path ozeti: {monte_carlo_paths_path}")
        return summary
    except Exception as error:
        print(f"Uyari: Monte Carlo raporu uretilemedi: {error}")
        pd.DataFrame(
            [
                {
                    "Simülasyon Sayısı": int(simulations),
                    "Warning": f"Monte Carlo failed: {error}",
                }
            ]
        ).to_csv(monte_carlo_report_path, index=False)
        pd.DataFrame().to_csv(monte_carlo_paths_path, index=False)
        return pd.DataFrame()


def _cache_missing_columns(
    asset_data,
    hurst_filter_mode="off",
    hurst_threshold=0.5,
    expected_symbols=None,
    use_market_calendar=True,
    include_partial_session_bars=False,
    strategy_name=DEFAULT_STRATEGY_NAME,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    bb_entry_tolerance_atr=0.0,
    trend_sma_length=50,
    setup_lookback=3,
    use_quality_filter=True,
    min_price_filter=10.0,
    min_atr_pct_filter=0.2,
    max_atr_pct_filter=12.0,
    min_avg_dollar_volume=20_000_000,
    avg_volume_lookback=20,
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
    if not isinstance(asset_data, dict) or not asset_data:
        return {"asset_data": ["dict"]}

    expected_config = _strategy_cache_config(
        hurst_filter_mode,
        hurst_threshold,
        strategy_name=strategy_name,
        use_market_calendar=use_market_calendar,
        include_partial_session_bars=include_partial_session_bars,
        rsi_length=rsi_length,
        rsi_entry_threshold=rsi_entry_threshold,
        bollinger_length=bollinger_length,
        bollinger_std=bollinger_std,
        bb_entry_tolerance_atr=bb_entry_tolerance_atr,
        use_quality_filter=use_quality_filter,
        min_price_filter=min_price_filter,
        min_atr_pct_filter=min_atr_pct_filter,
        max_atr_pct_filter=max_atr_pct_filter,
        min_avg_dollar_volume=min_avg_dollar_volume,
        avg_volume_lookback=avg_volume_lookback,
        trend_sma_length=trend_sma_length,
        setup_lookback=setup_lookback,
        exit_sma_length=exit_sma_length,
        take_profit_atr_multiplier=take_profit_atr_multiplier,
        disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
        exit_mode=exit_mode,
        confirmation_mode=confirmation_mode,
        confirmation_lookback=confirmation_lookback,
        confirmation_min_rebound_atr=confirmation_min_rebound_atr,
        use_entry_context_filter=use_entry_context_filter,
        max_entry_gap_pct=max_entry_gap_pct,
        max_entry_bb_distance_atr=max_entry_bb_distance_atr,
        max_entry_signal_range_pct=max_entry_signal_range_pct,
        min_entry_close_position_in_range=min_entry_close_position_in_range,
        max_entry_close_position_in_range=max_entry_close_position_in_range,
        rs_lookback_days=rs_lookback_days,
        rs_top_n=rs_top_n,
        rs_rebalance_frequency=rs_rebalance_frequency,
        rs_score_mode=rs_score_mode,
        rs_vol_lookback_days=rs_vol_lookback_days,
        rs_trend_sma_days=rs_trend_sma_days,
        rs_use_market_regime=rs_use_market_regime,
        rs_market_regime_sma_days=rs_market_regime_sma_days,
        rs_min_price=rs_min_price,
        rs_max_atr_pct=rs_max_atr_pct,
    )
    if expected_symbols is not None:
        cached_symbols = {_normalize_symbol(symbol) for symbol in asset_data}
        if cached_symbols != set(expected_symbols):
            return {"asset_data": ["symbol_set"]}
    missing_by_symbol = {}
    for symbol, data in asset_data.items():
        missing = [
            column for column in CACHE_REQUIRED_COLUMNS
            if column not in data
        ]
        for key, expected_value in expected_config.items():
            actual_value = data.attrs.get(key)
            if key in {
                "hurst_threshold",
                "rsi_entry_threshold",
                "bollinger_std",
                "bb_entry_tolerance_atr",
                "min_price_filter",
                "min_atr_pct_filter",
                "max_atr_pct_filter",
                "min_avg_dollar_volume",
                "take_profit_atr_multiplier",
                "disaster_stop_atr_multiplier",
                "confirmation_min_rebound_atr",
                "rs_min_price",
                "rs_max_atr_pct",
            }:
                try:
                    matches = (
                        actual_value is not None
                        and math.isclose(float(actual_value), expected_value)
                    )
                except (TypeError, ValueError):
                    matches = False
            else:
                matches = actual_value == expected_value
            if not matches:
                missing.append(key)
        if missing:
            missing_by_symbol[symbol] = missing
    return missing_by_symbol


def _intraday_cache_missing(
    intraday_data,
    expected_symbols=None,
    use_market_calendar=True,
):
    if not isinstance(intraday_data, dict) or not intraday_data:
        return {"intraday_data": ["dict"]}
    if expected_symbols is not None:
        cached_symbols = {_normalize_symbol(symbol) for symbol in intraday_data}
        if cached_symbols != set(expected_symbols):
            return {"intraday_data": ["symbol_set"]}

    missing_by_symbol = {}
    required_columns = ["Open", "High", "Low", "Close", "Volume"]
    for symbol, data in intraday_data.items():
        missing = [column for column in required_columns if column not in data]
        if data.attrs.get("use_market_calendar") != bool(use_market_calendar):
            missing.append("use_market_calendar")
        if missing:
            missing_by_symbol[symbol] = missing
    return missing_by_symbol


def _build_load_results(asset_data):
    results = []
    for ticker, data in asset_data.items():
        raw_signal = data["Raw_Entry_Signal"].fillna(False).astype(bool)
        hurst_signal = raw_signal & data["Hurst_Filter_OK"].fillna(False).astype(bool)
        context_ok = data.get(
            "Entry_Context_Filter_OK",
            pd.Series(True, index=data.index),
        ).fillna(False).astype(bool)
        final_signal = data["ENTRY_SIGNAL"].fillna(False).astype(bool)
        risk_off_rejected = hurst_signal & data["Risk_Off"].fillna(True).astype(bool)
        context_rejected = (
            hurst_signal
            & (~data["Risk_Off"].fillna(True).astype(bool))
            & (~context_ok)
        )
        results.append(
            {
                "Hisse / Varlik": ticker,
                "Bar Sayisi": len(data),
                "Partial Session Bar Sayısı": int(
                    data.get(
                        "Is_Partial_Session_Bar",
                        pd.Series(False, index=data.index),
                    ).fillna(False).sum()
                ),
                "Hazir Bar Sayisi": int(data["Indicators_Ready"].sum()),
                "calendar_fallback": bool(data.attrs.get("calendar_fallback", False)),
                "Quality Filter Uyarisi": data.attrs.get("quality_filter_warning", ""),
                "Quality Filter OK Sayisi": int(
                    data.get(
                        "Quality_Filter_OK",
                        pd.Series(True, index=data.index),
                    ).fillna(False).astype(bool).sum()
                ),
                "Raw Giriş Sinyali Sayısı": int(raw_signal.sum()),
                "Hurst Sonrası Sinyal Sayısı": int(hurst_signal.sum()),
                "Final Giriş Sinyali Sayısı": int(final_signal.sum()),
                "Risk Off Reddedilen Sinyal Sayısı": int(
                    risk_off_rejected.sum()
                ),
                "Entry Context Filter OK Count": int(context_ok.sum()),
                "Entry Context Rejected Signal Count": int(context_rejected.sum()),
                "Giris Sinyali Sayisi": int(final_signal.sum()),
            }
        )
    return results


def _resolve_asset_worker_count(
    max_workers,
    symbol_count=None,
    worker_utilization_target=0.85,
):
    if max_workers is not None:
        return max(1, int(max_workers))
    cpu_count = os.cpu_count() or 1
    if cpu_count <= 1:
        return 1
    target = float(worker_utilization_target)
    if not np.isfinite(target) or target <= 0:
        target = 0.85
    target = min(target, 0.95)
    cpu_limited = max(1, min(cpu_count - 1, int(np.floor(cpu_count * target))))
    if symbol_count is None:
        return cpu_limited
    return max(1, min(cpu_limited, int(symbol_count)))


def _resolve_parallel_chunk_size(parallel_chunk_size, symbol_count, worker_count):
    if parallel_chunk_size is not None:
        return max(1, int(parallel_chunk_size))
    symbol_count = max(0, int(symbol_count or 0))
    worker_count = max(1, int(worker_count or 1))
    return max(1, symbol_count // (worker_count * 3))


def _build_asset_worker(task):
    start = time.perf_counter()
    required_parameters = [
        "symbol",
        "file_path",
        "benchmark_data",
        "hurst_filter_mode",
        "hurst_threshold",
        "return_intraday",
        "use_market_calendar",
        "include_partial_session_bars",
        "session_windows",
        "calendar_fallback",
        "strategy_name",
        "rsi_length",
        "rsi_entry_threshold",
        "bollinger_length",
        "bollinger_std",
        "bb_entry_tolerance_atr",
        "use_quality_filter",
        "min_price_filter",
        "min_atr_pct_filter",
        "max_atr_pct_filter",
        "min_avg_dollar_volume",
        "avg_volume_lookback",
        "trend_sma_length",
        "setup_lookback",
        "exit_sma_length",
        "take_profit_atr_multiplier",
        "disaster_stop_atr_multiplier",
        "exit_mode",
        "confirmation_mode",
        "confirmation_lookback",
        "confirmation_min_rebound_atr",
        "use_entry_context_filter",
        "max_entry_gap_pct",
        "max_entry_bb_distance_atr",
        "max_entry_signal_range_pct",
        "min_entry_close_position_in_range",
        "max_entry_close_position_in_range",
        "csm_lookback_days",
        "csm_top_n",
        "csm_rebalance_frequency",
        "csm_trend_sma_days",
        "csm_use_market_regime",
        "csm_market_regime_sma_days",
        "csm_min_price",
        "csm_max_atr_pct",
        "dm_lookback_days",
        "dm_top_n",
        "dm_rebalance_frequency",
        "dm_absolute_momentum_threshold",
        "dm_trend_sma_days",
        "dm_use_market_regime",
        "dm_market_regime_sma_days",
        "dm_min_price",
        "dm_max_atr_pct",
        "donchian_entry_lookback_days",
        "donchian_exit_lookback_days",
        "donchian_top_n",
        "donchian_rebalance_frequency",
        "donchian_trend_sma_days",
        "donchian_use_market_regime",
        "donchian_market_regime_sma_days",
        "donchian_min_price",
        "donchian_max_atr_pct",
        "rs_lookback_days",
        "rs_top_n",
        "rs_rebalance_frequency",
        "rs_score_mode",
        "rs_vol_lookback_days",
        "rs_trend_sma_days",
        "rs_use_market_regime",
        "rs_market_regime_sma_days",
        "rs_min_price",
        "rs_max_atr_pct",
    ]
    symbol = task.get("symbol", "UNKNOWN")
    worker_timing = _WorkerTimingCollector()
    try:
        for parameter in required_parameters:
            if parameter not in task:
                raise ValueError(
                    f"Worker task missing required parameter: {parameter}"
                )
        symbol = task["symbol"]
        build_result = build_mega_dataframe(
            task["file_path"],
            benchmark_df=task["benchmark_data"],
            hurst_filter_mode=task["hurst_filter_mode"],
            hurst_threshold=task["hurst_threshold"],
            return_intraday=task["return_intraday"],
            use_market_calendar=task["use_market_calendar"],
            include_partial_session_bars=task["include_partial_session_bars"],
            session_windows=task["session_windows"],
            calendar_fallback=task["calendar_fallback"],
            timing=worker_timing,
            strategy_name=task["strategy_name"],
            rsi_length=task["rsi_length"],
            rsi_entry_threshold=task["rsi_entry_threshold"],
            bollinger_length=task["bollinger_length"],
            bollinger_std=task["bollinger_std"],
            bb_entry_tolerance_atr=task["bb_entry_tolerance_atr"],
            trend_sma_length=task["trend_sma_length"],
            setup_lookback=task["setup_lookback"],
            use_quality_filter=task["use_quality_filter"],
            min_price_filter=task["min_price_filter"],
            min_atr_pct_filter=task["min_atr_pct_filter"],
            max_atr_pct_filter=task["max_atr_pct_filter"],
            min_avg_dollar_volume=task["min_avg_dollar_volume"],
            avg_volume_lookback=task["avg_volume_lookback"],
            exit_sma_length=task["exit_sma_length"],
            take_profit_atr_multiplier=task["take_profit_atr_multiplier"],
            disaster_stop_atr_multiplier=task["disaster_stop_atr_multiplier"],
            exit_mode=task["exit_mode"],
            confirmation_mode=task["confirmation_mode"],
            confirmation_lookback=task["confirmation_lookback"],
            confirmation_min_rebound_atr=task["confirmation_min_rebound_atr"],
            use_entry_context_filter=task["use_entry_context_filter"],
            max_entry_gap_pct=task["max_entry_gap_pct"],
            max_entry_bb_distance_atr=task["max_entry_bb_distance_atr"],
            max_entry_signal_range_pct=task["max_entry_signal_range_pct"],
            min_entry_close_position_in_range=task[
                "min_entry_close_position_in_range"
            ],
            max_entry_close_position_in_range=task[
                "max_entry_close_position_in_range"
            ],
            csm_lookback_days=task["csm_lookback_days"],
            csm_top_n=task["csm_top_n"],
            csm_rebalance_frequency=task["csm_rebalance_frequency"],
            csm_trend_sma_days=task["csm_trend_sma_days"],
            csm_use_market_regime=task["csm_use_market_regime"],
            csm_market_regime_sma_days=task["csm_market_regime_sma_days"],
            csm_min_price=task["csm_min_price"],
            csm_max_atr_pct=task["csm_max_atr_pct"],
            dm_lookback_days=task["dm_lookback_days"],
            dm_top_n=task["dm_top_n"],
            dm_rebalance_frequency=task["dm_rebalance_frequency"],
            dm_absolute_momentum_threshold=task["dm_absolute_momentum_threshold"],
            dm_trend_sma_days=task["dm_trend_sma_days"],
            dm_use_market_regime=task["dm_use_market_regime"],
            dm_market_regime_sma_days=task["dm_market_regime_sma_days"],
            dm_min_price=task["dm_min_price"],
            dm_max_atr_pct=task["dm_max_atr_pct"],
            donchian_entry_lookback_days=task["donchian_entry_lookback_days"],
            donchian_exit_lookback_days=task["donchian_exit_lookback_days"],
            donchian_top_n=task["donchian_top_n"],
            donchian_rebalance_frequency=task["donchian_rebalance_frequency"],
            donchian_trend_sma_days=task["donchian_trend_sma_days"],
            donchian_use_market_regime=task["donchian_use_market_regime"],
            donchian_market_regime_sma_days=task["donchian_market_regime_sma_days"],
            donchian_min_price=task["donchian_min_price"],
            donchian_max_atr_pct=task["donchian_max_atr_pct"],
            rs_lookback_days=task["rs_lookback_days"],
            rs_top_n=task["rs_top_n"],
            rs_rebalance_frequency=task["rs_rebalance_frequency"],
            rs_score_mode=task["rs_score_mode"],
            rs_vol_lookback_days=task["rs_vol_lookback_days"],
            rs_trend_sma_days=task["rs_trend_sma_days"],
            rs_use_market_regime=task["rs_use_market_regime"],
            rs_market_regime_sma_days=task["rs_market_regime_sma_days"],
            rs_min_price=task["rs_min_price"],
            rs_max_atr_pct=task["rs_max_atr_pct"],
        )
        intraday_df = None
        if task["return_intraday"]:
            data, intraday_df = build_result
        else:
            data = build_result
        load_result = _build_load_results({symbol: data})[0]
        return {
            "symbol": symbol,
            "success": True,
            "data": data,
            "intraday_df": intraday_df,
            "error": "",
            "load_result": load_result,
            "seconds": time.perf_counter() - start,
            "timings": worker_timing.rows,
        }
    except Exception as error:
        return {
            "symbol": symbol,
            "success": False,
            "data": None,
            "intraday_df": None,
            "error": str(error),
            "load_result": None,
            "seconds": time.perf_counter() - start,
            "timings": worker_timing.rows,
        }


def _risk_off_rejected_total(load_results):
    total = 0
    for result in load_results:
        for key, value in result.items():
            if str(key).startswith("Risk Off Reddedilen"):
                total += int(value)
                break
    return total


def _validate_asset_signal_columns(asset_data):
    required_columns = [
        "Quality_Filter_OK",
        "Raw_Entry_Signal",
        "Confirmed_Entry_Signal",
        "ENTRY_SIGNAL",
    ]
    for symbol, data in asset_data.items():
        missing = [column for column in required_columns if column not in data]
        if missing:
            raise ValueError(
                f"{symbol} icin sinyal kolonu eksik: {', '.join(missing)}"
            )
        for column in required_columns:
            if data[column].isna().any():
                raise ValueError(f"{symbol} icin {column} NaN iceriyor.")
            if not pd.api.types.is_bool_dtype(data[column]):
                data[column] = data[column].fillna(False).astype(bool)


# Signal diagnostics helper.
def _asset_signal_diagnostics(asset_data):
    if not isinstance(asset_data, dict):
        asset_data = {}
    symbols_loaded = len(asset_data)
    total_bars = 0
    indicators_ready_count = 0
    raw_trigger_count = 0
    raw_entry_signal_count = 0
    quality_filter_ok_count = 0
    confirmed_entry_signal_count = 0
    hurst_filter_ok_count = 0
    final_entry_signal_count = 0
    risk_off_rejected_signal_count = 0
    entry_context_filter_ok_count = 0
    entry_context_rejected_signal_count = 0
    for data in asset_data.values():
        total_bars += len(data)
        indicators_ready = data.get(
            "Indicators_Ready",
            pd.Series(False, index=data.index),
        ).fillna(False).astype(bool)
        raw_trigger = data.get("Trigger", pd.Series(False, index=data.index))
        quality_ok = data.get("Quality_Filter_OK", pd.Series(False, index=data.index))
        raw_signal = data.get("Raw_Entry_Signal", pd.Series(False, index=data.index))
        confirmed_signal = data.get(
            "Confirmed_Entry_Signal",
            pd.Series(False, index=data.index),
        )
        hurst_ok = data.get("Hurst_Filter_OK", pd.Series(False, index=data.index))
        risk_off = data.get("Risk_Off", pd.Series(True, index=data.index))
        context_ok = data.get(
            "Entry_Context_Filter_OK",
            pd.Series(True, index=data.index),
        )
        final_signal = data.get("ENTRY_SIGNAL", pd.Series(False, index=data.index))
        raw_trigger = raw_trigger.fillna(False).astype(bool)
        quality_ok = quality_ok.fillna(False).astype(bool)
        raw_signal = raw_signal.fillna(False).astype(bool)
        confirmed_signal = confirmed_signal.fillna(False).astype(bool)
        hurst_ok = hurst_ok.fillna(False).astype(bool)
        risk_off = risk_off.fillna(True).astype(bool)
        context_ok = context_ok.fillna(False).astype(bool)
        final_signal = final_signal.fillna(False).astype(bool)
        post_hurst_signal = confirmed_signal & hurst_ok

        indicators_ready_count += int(indicators_ready.sum())
        raw_trigger_count += int(raw_trigger.sum())
        raw_entry_signal_count += int(raw_signal.sum())
        quality_filter_ok_count += int(quality_ok.sum())
        confirmed_entry_signal_count += int(confirmed_signal.sum())
        hurst_filter_ok_count += int(post_hurst_signal.sum())
        final_entry_signal_count += int(final_signal.sum())
        risk_off_rejected_signal_count += int((post_hurst_signal & risk_off).sum())
        entry_context_filter_ok_count += int(context_ok.sum())
        entry_context_rejected_signal_count += int(
            (post_hurst_signal & (~risk_off) & (~context_ok)).sum()
        )
    return {
        "Symbols Loaded": symbols_loaded,
        "Total Bars": total_bars,
        "Indicators Ready Count": indicators_ready_count,
        "Raw Trigger Count": raw_trigger_count,
        "Raw Entry Signal Count": raw_entry_signal_count,
        "Quality Filter OK Count": quality_filter_ok_count,
        "Confirmed Entry Signal Count": confirmed_entry_signal_count,
        "Hurst Filter OK Count": hurst_filter_ok_count,
        "Final Entry Signal Count": final_entry_signal_count,
        "Risk Off Rejected Signal Count": risk_off_rejected_signal_count,
        "Entry Context Filter OK Count": entry_context_filter_ok_count,
        "Entry Context Rejected Signal Count": entry_context_rejected_signal_count,
        "symbols_loaded": symbols_loaded,
        "quality_filter_active_symbols": sum(
            1
            for data in asset_data.values()
            if data.get(
                "Quality_Filter_OK",
                pd.Series(False, index=data.index),
            ).fillna(False).astype(bool).any()
        ),
        "total_raw_entry_signals": raw_entry_signal_count,
        "total_confirmed_entry_signals": confirmed_entry_signal_count,
        "total_final_entry_signals": final_entry_signal_count,
    }


def _zero_trade_metrics(**extra):
    metrics = {
        "Baslangic Kasa": 10000.0,
        "Bitis Kasa": 10000.0,
        "Net Getiri (%)": 0.0,
        "CAGR (%)": 0.0,
        "Max Drawdown (%)": 0.0,
        "Sharpe Ratio": 0.0,
        "Islem Sayisi": 0,
        "Trade Count": 0,
        "Win Rate (%)": 0.0,
        "Profit Factor": 0.0,
        "Avg R": 0.0,
        "Median R": 0.0,
        "Best R": 0.0,
        "Worst R": 0.0,
        "Avg Winner R": 0.0,
        "Avg Loser R": 0.0,
        "Expectancy R": 0.0,
        "Max Consecutive Losses": 0,
        "Max Consecutive Wins": 0,
        "Stop Exit Count": 0,
        "Take Profit Exit Count": 0,
        "SMA20 Reversion Exit Count": 0,
        "Time Stop Exit Count": 0,
        "Risk Off Exit Count": 0,
        "Forced Final Exit Count": 0,
        "Max Acik Pozisyon": 0,
        "Max Portfolio Heat (%)": 0.0,
        "Avg Holding Bars": 0.0,
        "Median Holding Bars": 0.0,
        "Turnover Estimate": 0.0,
        "Avg Positions Held": 0.0,
        "Max Positions Held": 0,
        "Reddedilen Sinyal": 0,
        "Makro Rejimde Reddedilen Sinyal": 0,
    }
    metrics.update({column: 0 for column in PORTFOLIO_REJECTION_COLUMNS})
    metrics.update(extra)
    return metrics


def _rs_rebalance_mask(index, frequency):
    frequency = _normalize_rs_rebalance_frequency(frequency)
    if frequency == "daily":
        return pd.Series(True, index=index)
    if frequency == "weekly":
        periods = index.to_period("W-FRI")
    else:
        periods = index.to_period("M")
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
        annualized_vol = daily["Close"].pct_change().rolling(
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


def _safe_bool_series(series, index):
    aligned = series.reindex(index)
    aligned = aligned.infer_objects(copy=False)
    aligned = aligned.astype("boolean")
    return aligned.ffill().fillna(False).astype(bool)


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
    return _safe_bool_series(regime, index)


def _relative_strength_selection(asset_data, params, benchmark_data=None):
    features = _relative_strength_daily_features(asset_data, params)
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
    daily_returns = equity.resample("1D").last().pct_change().dropna()
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
    metrics = _zero_trade_metrics(
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
    return metrics


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
                            _close(symbol, date, float(price), "rs_rotation_exit")
            target_weight = 1 / max(rs_params["rs_top_n"], 1)
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
                    "entry_notional": shares * float(price),
                    "shares": shares,
                    "bars_held": 0,
                    "last_price": float(price),
                }
            pending_target = None

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
    metrics["_trade_log"] = trade_log
    metrics["_equity_curve"] = equity_curve
    return metrics


class _SimpleBacktestResult:
    def __init__(self, trade_log=None, equity_curve=None, initial_cash=10000.0):
        self.trade_log = list(trade_log or [])
        self.equity_curve = list(equity_curve or [])
        self.initial_cash = float(initial_cash)


def _calendar_report_summary(asset_data):
    return {
        "calendar_fallback": any(
            bool(data.attrs.get("calendar_fallback", False))
            for data in asset_data.values()
        ),
        "Partial Session Bar Sayısı": sum(
            int(
                data.get(
                    "Is_Partial_Session_Bar",
                    pd.Series(False, index=data.index),
                ).fillna(False).sum()
            )
            for data in asset_data.values()
        ),
    }


class _TimingRecorder:
    def __init__(self, enabled=True, report_path=None):
        self.enabled = bool(enabled)
        self.report_path = Path(report_path) if report_path is not None else None
        self.rows = []
        self.total_start = time.perf_counter()
        self._finished = False

    @contextmanager
    def stage(self, name, notes=""):
        if not self.enabled:
            yield
            return

        start = time.perf_counter()
        try:
            yield
        finally:
            self.rows.append(
                {
                    "Stage": name,
                    "Seconds": round(time.perf_counter() - start, 6),
                    "Notes": notes,
                }
            )

    def finish(self):
        if not self.enabled or self._finished:
            return
        self._finished = True
        self.rows.append(
            {
                "Stage": "Total runtime",
                "Seconds": round(time.perf_counter() - self.total_start, 6),
                "Notes": "",
            }
        )
        self._print_summary()
        self._write_report()

    def add(self, name, seconds=0.0, notes=""):
        if not self.enabled:
            return
        self.rows.append(
            {
                "Stage": name,
                "Seconds": round(float(seconds), 6),
                "Notes": notes,
            }
        )

    def _print_summary(self):
        stage_rows = [row for row in self.rows if row["Stage"] != "Total runtime"]
        slowest_rows = sorted(
            stage_rows,
            key=lambda row: row["Seconds"],
            reverse=True,
        )[:5]
        if not slowest_rows:
            return
        print("\nTiming ozeti - en yavas 5 asama:")
        for row in slowest_rows:
            print(f"  {row['Stage']}: {row['Seconds']:.3f}s")

    def _write_report(self):
        if self.report_path is None:
            return
        try:
            pd.DataFrame(self.rows, columns=["Stage", "Seconds", "Notes"]).to_csv(
                self.report_path,
                index=False,
            )
            print(f"Timing raporu: {self.report_path}")
        except Exception as error:
            print(f"Timing raporu yazilamadi: {error}")


class _WorkerTimingCollector:
    def __init__(self):
        self.rows = []

    @contextmanager
    def stage(self, name, notes=""):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.rows.append(
                {
                    "Stage": name,
                    "Seconds": round(time.perf_counter() - start, 6),
                    "Notes": notes,
                }
            )

    def add(self, name, seconds=0.0, notes=""):
        self.rows.append(
            {
                "Stage": name,
                "Seconds": round(float(seconds), 6),
                "Notes": notes,
            }
        )



try:
    from .portfolio_manager import (
        PORTFOLIO_REQUIRED_COLUMNS,
        PORTFOLIO_CORE_REQUIRED_COLUMNS,
        PORTFOLIO_CONTEXT_SOURCE_COLUMNS,
        PORTFOLIO_CONTEXT_SIGNAL_COLUMNS,
        TRADE_CONTEXT_REPORT_COLUMNS,
        PORTFOLIO_REJECTION_COLUMNS,
        SYMBOL_ATTRIBUTION_COLUMNS,
        CACHE_REQUIRED_COLUMNS,
        _normalize_ranking_mode,
        _normalize_execution_mode,
        _normalize_exit_mode,
        _normalize_risk_off_exit_mode,
        _align_timestamp_to_index,
        PortfolioManager,
    )
except ImportError:
    from portfolio_manager import (
        PORTFOLIO_REQUIRED_COLUMNS,
        PORTFOLIO_CORE_REQUIRED_COLUMNS,
        PORTFOLIO_CONTEXT_SOURCE_COLUMNS,
        PORTFOLIO_CONTEXT_SIGNAL_COLUMNS,
        TRADE_CONTEXT_REPORT_COLUMNS,
        PORTFOLIO_REJECTION_COLUMNS,
        SYMBOL_ATTRIBUTION_COLUMNS,
        CACHE_REQUIRED_COLUMNS,
        _normalize_ranking_mode,
        _normalize_execution_mode,
        _normalize_exit_mode,
        _normalize_risk_off_exit_mode,
        _align_timestamp_to_index,
        PortfolioManager,
    )

def run_universe_scanner(
    data_folder="hisseler",
    benchmark_file=None,
    benchmark_symbol="QQQ",
    hurst_filter_mode="off",
    hurst_threshold=0.5,
    ranking_mode="momentum",
    run_profile="fast",
    execution_mode=None,
    universe_symbols_file=None,
    allowed_symbols=None,
    excluded_symbols=None,
    evaluation_mode=None,
    train_start=None,
    train_end=None,
    test_start=None,
    test_end=None,
    walk_train_months=18,
    walk_test_months=6,
    monte_carlo=None,
    monte_carlo_simulations=5000,
    use_market_calendar=None,
    include_partial_session_bars=None,
    profile_timing=True,
    parallel_assets=True,
    max_workers=None,
    worker_utilization_target=0.85,
    parallel_chunk_size=None,
    quick_mode=None,
    write_trade_log=None,
    cache_mode=None,
    output_suffix="",
    output_subdir=None,
    clear_stale_trade_log=True,
    strategy_name=DEFAULT_STRATEGY_NAME,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    bb_entry_tolerance_atr=0.0,
    trend_sma_length=50,
    setup_lookback=3,
    use_quality_filter=True,
    min_price_filter=10.0,
    min_atr_pct_filter=0.2,
    max_atr_pct_filter=12.0,
    min_avg_dollar_volume=20_000_000,
    avg_volume_lookback=20,
    exit_sma_length=20,
    take_profit_atr_multiplier=2.0,
    disaster_stop_atr_multiplier=2.0,
    time_stop_bars=10,
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
    return_prepared_data=False,
    prepared_data_only=False,
    fast_backtest=True,
    commission_rate=0.001,
    slippage_mu=0.05,
    risk_off_exit_mode="hold_existing",
):
    run_profile = str(run_profile).lower()
    if run_profile not in {"fast", "full"}:
        raise ValueError("run_profile 'fast' veya 'full' olmali.")

    if run_profile == "fast":
        if quick_mode is None:
            quick_mode = True
        if monte_carlo is None:
            monte_carlo = False
        if use_market_calendar is None:
            use_market_calendar = False
        if execution_mode is None:
            execution_mode = "bar_close"
        if evaluation_mode is None:
            evaluation_mode = "single"
        if include_partial_session_bars is None:
            include_partial_session_bars = False
    else:
        if quick_mode is None:
            quick_mode = False
        if monte_carlo is None:
            monte_carlo = True
        if use_market_calendar is None:
            use_market_calendar = True
        if execution_mode is None:
            execution_mode = "bar_close"
        if evaluation_mode is None:
            evaluation_mode = "single"
        if include_partial_session_bars is None:
            include_partial_session_bars = False
    if cache_mode is None:
        cache_mode = "per_symbol"
    cache_mode = str(cache_mode).lower()
    if cache_mode not in {"per_symbol", "global", "off"}:
        raise ValueError("cache_mode 'per_symbol', 'global' veya 'off' olmali.")
    strategy_name = validate_strategy_name(strategy_name)
    csm_params = {
        "csm_lookback_days": csm_lookback_days,
        "csm_top_n": csm_top_n,
        "csm_rebalance_frequency": csm_rebalance_frequency,
        "csm_trend_sma_days": csm_trend_sma_days,
        "csm_use_market_regime": csm_use_market_regime,
        "csm_market_regime_sma_days": csm_market_regime_sma_days,
        "csm_min_price": csm_min_price,
        "csm_max_atr_pct": csm_max_atr_pct,
    }
    dm_params = _normalize_dual_momentum_params(
        {
            "dm_lookback_days": dm_lookback_days,
            "dm_top_n": dm_top_n,
            "dm_rebalance_frequency": dm_rebalance_frequency,
            "dm_absolute_momentum_threshold": dm_absolute_momentum_threshold,
            "dm_trend_sma_days": dm_trend_sma_days,
            "dm_use_market_regime": dm_use_market_regime,
            "dm_market_regime_sma_days": dm_market_regime_sma_days,
            "dm_min_price": dm_min_price,
            "dm_max_atr_pct": dm_max_atr_pct,
        }
    )
    donchian_params = _normalize_donchian_breakout_params(
        {
            "donchian_entry_lookback_days": donchian_entry_lookback_days,
            "donchian_exit_lookback_days": donchian_exit_lookback_days,
            "donchian_top_n": donchian_top_n,
            "donchian_rebalance_frequency": donchian_rebalance_frequency,
            "donchian_trend_sma_days": donchian_trend_sma_days,
            "donchian_use_market_regime": donchian_use_market_regime,
            "donchian_market_regime_sma_days": donchian_market_regime_sma_days,
            "donchian_min_price": donchian_min_price,
            "donchian_max_atr_pct": donchian_max_atr_pct,
        }
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
    rs_lookback_days = rs_params["rs_lookback_days"]
    rs_top_n = rs_params["rs_top_n"]
    rs_rebalance_frequency = rs_params["rs_rebalance_frequency"]
    rs_score_mode = rs_params["rs_score_mode"]
    rs_vol_lookback_days = rs_params["rs_vol_lookback_days"]
    rs_trend_sma_days = rs_params["rs_trend_sma_days"]
    rs_use_market_regime = rs_params["rs_use_market_regime"]
    rs_market_regime_sma_days = rs_params["rs_market_regime_sma_days"]
    rs_min_price = rs_params["rs_min_price"]
    rs_max_atr_pct = rs_params["rs_max_atr_pct"]
    donchian_entry_lookback_days = donchian_params["donchian_entry_lookback_days"]
    donchian_exit_lookback_days = donchian_params["donchian_exit_lookback_days"]
    donchian_top_n = donchian_params["donchian_top_n"]
    donchian_rebalance_frequency = donchian_params["donchian_rebalance_frequency"]
    donchian_trend_sma_days = donchian_params["donchian_trend_sma_days"]
    donchian_use_market_regime = donchian_params["donchian_use_market_regime"]
    donchian_market_regime_sma_days = donchian_params["donchian_market_regime_sma_days"]
    donchian_min_price = donchian_params["donchian_min_price"]
    donchian_max_atr_pct = donchian_params["donchian_max_atr_pct"]
    rsi_length = int(rsi_length)
    rsi_entry_threshold = float(rsi_entry_threshold)
    bollinger_length = int(bollinger_length)
    bollinger_std = float(bollinger_std)
    bb_entry_tolerance_atr = float(bb_entry_tolerance_atr)
    if bb_entry_tolerance_atr < 0:
        raise ValueError("bb_entry_tolerance_atr negatif olamaz.")
    use_quality_filter = bool(use_quality_filter)
    min_price_filter = float(min_price_filter)
    min_atr_pct_filter = float(min_atr_pct_filter)
    max_atr_pct_filter = float(max_atr_pct_filter)
    min_avg_dollar_volume = float(min_avg_dollar_volume)
    avg_volume_lookback = int(avg_volume_lookback)
    if min_price_filter < 0:
        raise ValueError("min_price_filter negatif olamaz.")
    if min_atr_pct_filter < 0:
        raise ValueError("min_atr_pct_filter negatif olamaz.")
    if max_atr_pct_filter <= min_atr_pct_filter:
        raise ValueError("max_atr_pct_filter min_atr_pct_filter'dan buyuk olmali.")
    if min_avg_dollar_volume < 0:
        raise ValueError("min_avg_dollar_volume negatif olamaz.")
    if avg_volume_lookback < 1:
        raise ValueError("avg_volume_lookback en az 1 olmali.")
    trend_sma_length = int(trend_sma_length)
    setup_lookback = int(setup_lookback)
    exit_sma_length = int(exit_sma_length)
    take_profit_atr_multiplier = float(take_profit_atr_multiplier)
    disaster_stop_atr_multiplier = float(disaster_stop_atr_multiplier)
    time_stop_bars = int(time_stop_bars)

    hurst_filter_mode = _normalize_hurst_filter_mode(hurst_filter_mode)
    hurst_threshold = float(hurst_threshold)
    confirmation_mode = _normalize_confirmation_mode(confirmation_mode)
    confirmation_lookback = int(confirmation_lookback)
    confirmation_min_rebound_atr = float(confirmation_min_rebound_atr)
    context_filter_params = _normalize_entry_context_filter_params(
        use_entry_context_filter=use_entry_context_filter,
        max_entry_gap_pct=max_entry_gap_pct,
        max_entry_bb_distance_atr=max_entry_bb_distance_atr,
        max_entry_signal_range_pct=max_entry_signal_range_pct,
        min_entry_close_position_in_range=min_entry_close_position_in_range,
        max_entry_close_position_in_range=max_entry_close_position_in_range,
    )
    if confirmation_lookback < 1:
        raise ValueError("confirmation_lookback en az 1 olmali.")
    if confirmation_min_rebound_atr < 0:
        raise ValueError("confirmation_min_rebound_atr negatif olamaz.")
    commission_rate = float(commission_rate)
    slippage_mu = float(slippage_mu)
    if commission_rate < 0:
        raise ValueError("commission_rate negatif olamaz.")
    if slippage_mu < 0:
        raise ValueError("slippage_mu negatif olamaz.")
    risk_off_exit_mode = _normalize_risk_off_exit_mode(risk_off_exit_mode)
    ranking_mode = _normalize_ranking_mode(ranking_mode)
    execution_mode = _normalize_execution_mode(execution_mode)
    exit_mode = _normalize_exit_mode(exit_mode)
    evaluation_mode = _normalize_evaluation_mode(evaluation_mode)
    monte_carlo = bool(monte_carlo)
    use_market_calendar = bool(use_market_calendar)
    include_partial_session_bars = bool(include_partial_session_bars)
    parallel_assets = bool(parallel_assets)
    quick_mode = bool(quick_mode)
    if write_trade_log is None:
        write_trade_log = not quick_mode
    write_trade_log = bool(write_trade_log)
    asset_worker_count = _resolve_asset_worker_count(
        max_workers,
        worker_utilization_target=worker_utilization_target,
    )
    data_folder_path = Path(data_folder)
    output_dir = Path(data_folder).resolve().parent
    if output_subdir is not None:
        output_dir = output_dir / output_subdir
    output_dir.mkdir(parents=True, exist_ok=True)
    timing = _TimingRecorder(
        enabled=profile_timing,
        report_path=_output_path(output_dir, "timing_raporu", output_suffix),
    )

    print(f"'{data_folder}' klasorundeki hisseler taraniyor...\n")
    allowed_symbol_set = None
    excluded_symbol_set = _build_excluded_symbol_set(excluded_symbols)
    selected_csv_files = []
    excluded_rows = []
    universe_filter_error = None
    with timing.stage(
        "CSV file discovery / universe filtering",
        f"folder={data_folder}",
    ):
        csv_files = glob.glob(os.path.join(data_folder, "*.csv"))
        try:
            allowed_symbol_set = _build_allowed_symbol_set(
                universe_symbols_file,
                allowed_symbols,
            )
        except Exception as error:
            universe_filter_error = error

        if universe_filter_error is None:
            selected_csv_files, excluded_rows = _select_universe_files(
                csv_files,
                allowed_symbol_set,
                excluded_symbol_set,
            )
    if universe_filter_error is not None:
        print(f"Evren filtresi okunamadi: {universe_filter_error}")
        timing.finish()
        return
    selected_symbols = [_symbol_from_path(file_path) for file_path in selected_csv_files]
    asset_worker_count = _resolve_asset_worker_count(
        max_workers,
        symbol_count=len(selected_symbols),
        worker_utilization_target=worker_utilization_target,
    )
    parallel_chunk_size_value = _resolve_parallel_chunk_size(
        parallel_chunk_size,
        len(selected_symbols),
        asset_worker_count,
    )
    filtered_universe = allowed_symbol_set is not None
    cache_path = _cache_path_for_universe(
        data_folder_path,
        selected_symbols,
        filtered_universe,
        use_market_calendar=use_market_calendar,
    )
    intraday_cache_path = _intraday_cache_path_for_universe(
        data_folder_path,
        selected_symbols,
        use_market_calendar=use_market_calendar,
    )
    print("Run ayarlari:")
    print(f"  Strategy Name: {strategy_name}")
    print(f"  Strategy Family: {get_strategy_family(strategy_name)}")
    if strategy_name == "relative_strength_rotation":
        print(
            "  RS Params: "
            f"lookback={rs_lookback_days}, top_n={rs_top_n}, "
            f"rebalance={rs_rebalance_frequency}, score={rs_score_mode}, "
            f"vol_lookback={rs_vol_lookback_days}, trend_sma={rs_trend_sma_days}, "
            f"market_regime={rs_use_market_regime}, "
            f"market_sma={rs_market_regime_sma_days}, "
            f"min_price={rs_min_price}, max_atr_pct={rs_max_atr_pct}"
        )
    elif strategy_name == "dual_momentum":
        print(
            "  DM Params: "
            f"lookback={dm_params.get('dm_lookback_days')}, top_n={dm_params.get('dm_top_n')}, "
            f"rebalance={dm_params.get('dm_rebalance_frequency')}, "
            f"abs_threshold={dm_params.get('dm_absolute_momentum_threshold')}, "
            f"trend_sma={dm_params.get('dm_trend_sma_days')}, "
            f"market_regime={dm_params.get('dm_use_market_regime')}, "
            f"market_sma={dm_params.get('dm_market_regime_sma_days')}, "
            f"min_price={dm_params.get('dm_min_price')}, max_atr_pct={dm_params.get('dm_max_atr_pct')}"
        )
        if evaluation_mode != "single":
            raise ValueError(
                "dual_momentum su anda evaluation_mode='single' "
                "ile destekleniyor."
            )
        if dm_params.get("dm_use_market_regime") and benchmark_data is None:
            benchmark_path = benchmark_file
            if benchmark_path is None:
                benchmark_path = _find_benchmark_file(csv_files, benchmark_symbol)
            if benchmark_path is None and benchmark_symbol.upper() != "SPY":
                benchmark_path = _find_benchmark_file(csv_files, "SPY")
            if benchmark_path is None:
                raise ValueError(
                    "dual_momentum market regime icin QQQ veya SPY "
                    "benchmark verisi gerekli."
                )
            benchmark_data = build_mega_dataframe(
                benchmark_path,
                hurst_filter_mode=hurst_filter_mode,
                hurst_threshold=hurst_threshold,
                use_market_calendar=use_market_calendar,
                include_partial_session_bars=include_partial_session_bars,
                strategy_name=strategy_name,
                rsi_length=rsi_length,
                rsi_entry_threshold=rsi_entry_threshold,
                bollinger_length=bollinger_length,
                bollinger_std=bollinger_std,
                bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                use_quality_filter=use_quality_filter,
                min_price_filter=min_price_filter,
                min_atr_pct_filter=min_atr_pct_filter,
                max_atr_pct_filter=max_atr_pct_filter,
                min_avg_dollar_volume=min_avg_dollar_volume,
                avg_volume_lookback=avg_volume_lookback,
                trend_sma_length=trend_sma_length,
                setup_lookback=setup_lookback,
                exit_sma_length=exit_sma_length,
                take_profit_atr_multiplier=take_profit_atr_multiplier,
                disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                exit_mode=exit_mode,
                confirmation_mode=confirmation_mode,
                confirmation_lookback=confirmation_lookback,
                confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                **dm_params,
                **context_filter_params,
            )
        asset_data = _apply_dual_momentum_top_signals(
            asset_data,
            dm_params,
            benchmark_data=benchmark_data,
        )
        load_results = _build_load_results(asset_data)
    elif strategy_name == "donchian_breakout":
        print(
            "  Donchian Params: "
            f"entry={donchian_params.get('donchian_entry_lookback_days')}, "
            f"exit={donchian_params.get('donchian_exit_lookback_days')}, "
            f"top_n={donchian_params.get('donchian_top_n')}, "
            f"rebalance={donchian_params.get('donchian_rebalance_frequency')}, "
            f"trend_sma={donchian_params.get('donchian_trend_sma_days')}, "
            f"market_regime={donchian_params.get('donchian_use_market_regime')}, "
            f"market_sma={donchian_params.get('donchian_market_regime_sma_days')}, "
            f"min_price={donchian_params.get('donchian_min_price')}, "
            f"max_atr_pct={donchian_params.get('donchian_max_atr_pct')}"
        )
        if evaluation_mode != "single":
            raise ValueError(
                "donchian_breakout su anda evaluation_mode='single' "
                "ile destekleniyor."
            )
        if donchian_params.get("donchian_use_market_regime") and benchmark_data is None:
            benchmark_path = benchmark_file
            if benchmark_path is None:
                benchmark_path = _find_benchmark_file(csv_files, benchmark_symbol)
            if benchmark_path is None and benchmark_symbol.upper() != "SPY":
                benchmark_path = _find_benchmark_file(csv_files, "SPY")
            if benchmark_path is None:
                raise ValueError(
                    "donchian_breakout market regime icin QQQ veya SPY "
                    "benchmark verisi gerekli."
                )
            benchmark_data = build_mega_dataframe(
                benchmark_path,
                hurst_filter_mode=hurst_filter_mode,
                hurst_threshold=hurst_threshold,
                use_market_calendar=use_market_calendar,
                include_partial_session_bars=include_partial_session_bars,
                strategy_name=strategy_name,
                rsi_length=rsi_length,
                rsi_entry_threshold=rsi_entry_threshold,
                bollinger_length=bollinger_length,
                bollinger_std=bollinger_std,
                bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                use_quality_filter=use_quality_filter,
                min_price_filter=min_price_filter,
                min_atr_pct_filter=min_atr_pct_filter,
                max_atr_pct_filter=max_atr_pct_filter,
                min_avg_dollar_volume=min_avg_dollar_volume,
                avg_volume_lookback=avg_volume_lookback,
                trend_sma_length=trend_sma_length,
                setup_lookback=setup_lookback,
                exit_sma_length=exit_sma_length,
                take_profit_atr_multiplier=take_profit_atr_multiplier,
                disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                exit_mode=exit_mode,
                confirmation_mode=confirmation_mode,
                confirmation_lookback=confirmation_lookback,
                confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                **donchian_params,
                **context_filter_params,
            )
        asset_data = _apply_donchian_breakout_top_signals(
            asset_data,
            donchian_params,
            benchmark_data=benchmark_data,
        )
        load_results = _build_load_results(asset_data)
    elif strategy_name == "cross_sectional_momentum":
        print(
            "  CSM Params: "
            f"lookback={csm_lookback_days}, top_n={csm_top_n}, "
            f"rebalance={csm_rebalance_frequency}, trend_sma={csm_trend_sma_days}, "
            f"market_regime={csm_use_market_regime}, "
            f"market_sma={csm_market_regime_sma_days}, "
            f"min_price={csm_min_price}, max_atr_pct={csm_max_atr_pct}"
        )
    print(f"  Run Profile: {run_profile}")
    print(f"  Evaluation Mode: {evaluation_mode}")
    print(f"  Execution Mode: {execution_mode}")
    print(f"  Monte Carlo: {monte_carlo}")
    print(f"  Market Calendar: {use_market_calendar}")
    print(f"  Quick Mode: {quick_mode}")
    print(
        "  Benchmark comparison "
        f"{'skipped' if quick_mode else 'enabled'}"
    )
    print(
        "  Trade log writing "
        f"{'enabled' if write_trade_log else 'skipped'}"
    )
    print(f"  Secilen sembol sayisi: {len(selected_symbols)}")
    if excluded_symbol_set:
        print(
            "  Excluded symbols: "
            f"{', '.join(sorted(excluded_symbol_set))}"
        )
    print(f"  Parallel Assets: {parallel_assets}")
    print(f"  Max Workers: {asset_worker_count}")
    print(f"  Parallel Chunk Size: {parallel_chunk_size_value}")
    print(f"  Cache Mode: {cache_mode}")
    print(
        "  Entry Params: "
        f"RSI({rsi_length})<{rsi_entry_threshold}, "
        f"BB({bollinger_length},{bollinger_std}, tol_atr={bb_entry_tolerance_atr}), "
        f"TrendSMA({trend_sma_length}), setup={setup_lookback}, "
        f"quality={use_quality_filter}, "
        f"confirmation={confirmation_mode}/{confirmation_lookback}/"
        f"{confirmation_min_rebound_atr}, "
        f"context_filter={context_filter_params['use_entry_context_filter']}"
    )
    print(f"  Output Dir: {output_dir}")
    if output_suffix:
        print(f"  Output Suffix: {output_suffix}")
    print(f"  Cache path: {cache_path}")
    if execution_mode == "intrabar_1m":
        print(f"  Intraday cache path: {intraday_cache_path}")
    print()
    universe_summary = {
        "Toplam CSV dosyası": len(csv_files),
        "Dahil edilen sembol sayısı": len(selected_symbols),
        "Hariç bırakılan sembol sayısı": len(excluded_rows),
    }
    excluded_symbols_path = _output_path(output_dir, "excluded_symbols", output_suffix)
    asset_data = {}
    intraday_1m_data = {}
    intraday_built_with_assets = False
    run_session_windows = None
    run_calendar_fallback = None
    benchmark_data = None
    load_results = []
    files_to_build = list(selected_csv_files)
    per_symbol_130m_hits = 0
    per_symbol_130m_misses = 0
    per_symbol_1m_hits = 0
    per_symbol_1m_misses = 0
    cache_load_seconds = 0.0
    cache_build_seconds = 0.0
    cache_write_seconds = 0.0
    worker_timing_rows = []
    asset_parallel_backend = "none"
    sequential_fallback = False
    asset_build_elapsed = 0.0
    avg_worker_seconds = 0.0
    max_worker_seconds = 0.0
    parallel_overhead_estimate_sec = 0.0

    def _cache_summary_dict():
        return {
            "per_symbol_130m_hits": per_symbol_130m_hits,
            "per_symbol_130m_misses": per_symbol_130m_misses,
            "per_symbol_1m_hits": per_symbol_1m_hits,
            "per_symbol_1m_misses": per_symbol_1m_misses,
            "cache_load_seconds": cache_load_seconds,
            "cache_build_seconds": cache_build_seconds,
            "cache_write_seconds": cache_write_seconds,
            "asset_build_seconds": asset_build_elapsed,
            "avg_worker_seconds": avg_worker_seconds,
            "max_worker_seconds": max_worker_seconds,
            "parallel_overhead_estimate_sec": parallel_overhead_estimate_sec,
            "worker_timing_rows": list(worker_timing_rows),
            "worker_count": asset_worker_count,
            "parallel_backend": asset_parallel_backend,
            "chunk_size": parallel_chunk_size_value,
            "batch_size": parallel_chunk_size_value,
            "sequential_fallback": sequential_fallback,
            "os_cpu_count": os.cpu_count() or 1,
            "worker_utilization_target": worker_utilization_target,
        }

    if not csv_files:
        print(f"'{data_folder}' klasorunde CSV dosyasi bulunamadi.")
        timing.finish()
        return
    if not selected_csv_files:
        with timing.stage("CSV writing", "excluded symbols only"):
            pd.DataFrame(excluded_rows).to_csv(excluded_symbols_path, index=False)
        print(
            "Evren filtresinden sonra backtest icin sembol kalmadi. "
            f"Haric birakilan semboller: {excluded_symbols_path}"
        )
        timing.finish()
        return
    if cache_mode == "per_symbol":
        (data_folder_path / ".cache").mkdir(parents=True, exist_ok=True)

    with timing.stage("calendar cache build/load", f"files={len(selected_csv_files)}"):
        run_session_windows, run_calendar_fallback = _build_run_session_windows_from_files(
            selected_csv_files,
            use_market_calendar=use_market_calendar,
        )

    cache_load_start = time.perf_counter()
    with timing.stage("asset 130m cache load", f"mode={cache_mode}"):
        if cache_mode == "global" and cache_path.exists():
            print(f"Onbellek bulundu: {cache_path}")
            try:
                cached_asset_data = pd.read_pickle(cache_path)
                missing_by_symbol = _cache_missing_columns(
                    cached_asset_data,
                    hurst_filter_mode=hurst_filter_mode,
                    hurst_threshold=hurst_threshold,
                    expected_symbols=selected_symbols,
                    use_market_calendar=use_market_calendar,
                    include_partial_session_bars=include_partial_session_bars,
                    strategy_name=strategy_name,
                    rsi_length=rsi_length,
                    rsi_entry_threshold=rsi_entry_threshold,
                    bollinger_length=bollinger_length,
                    bollinger_std=bollinger_std,
                    bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                    use_quality_filter=use_quality_filter,
                    min_price_filter=min_price_filter,
                    min_atr_pct_filter=min_atr_pct_filter,
                    max_atr_pct_filter=max_atr_pct_filter,
                    min_avg_dollar_volume=min_avg_dollar_volume,
                    avg_volume_lookback=avg_volume_lookback,
                    trend_sma_length=trend_sma_length,
                    setup_lookback=setup_lookback,
                    exit_sma_length=exit_sma_length,
                    take_profit_atr_multiplier=take_profit_atr_multiplier,
                    disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                    exit_mode=exit_mode,
                    confirmation_mode=confirmation_mode,
                    confirmation_lookback=confirmation_lookback,
                    confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                    **rs_params,
                    **context_filter_params,
                )
                if missing_by_symbol:
                    first_symbol = next(iter(missing_by_symbol))
                    missing = ", ".join(missing_by_symbol[first_symbol])
                    print(
                        "Onbellek guncel kodla uyumsuz; CSV'lerden yeniden "
                        f"derlenecek. Ornek: {first_symbol} eksik kolonlar: {missing}"
                    )
                else:
                    asset_data = cached_asset_data
                    load_results = _build_load_results(asset_data)
                    files_to_build = []
            except Exception as error:
                print(
                    "Onbellek okunamadi; CSV'lerden yeniden derlenecek. "
                    f"Hata: {error}"
                )
        elif cache_mode == "per_symbol":
            files_to_build = []
            for file_path in selected_csv_files:
                symbol, paths = _per_symbol_cache_paths_for_file(
                    data_folder_path,
                    file_path,
                    "130m",
                    hurst_filter_mode,
                    hurst_threshold,
                    use_market_calendar,
                    include_partial_session_bars,
                    strategy_name=strategy_name,
                    rsi_length=rsi_length,
                    rsi_entry_threshold=rsi_entry_threshold,
                    bollinger_length=bollinger_length,
                    bollinger_std=bollinger_std,
                    bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                    use_quality_filter=use_quality_filter,
                    min_price_filter=min_price_filter,
                    min_atr_pct_filter=min_atr_pct_filter,
                    max_atr_pct_filter=max_atr_pct_filter,
                    min_avg_dollar_volume=min_avg_dollar_volume,
                    avg_volume_lookback=avg_volume_lookback,
                    trend_sma_length=trend_sma_length,
                    setup_lookback=setup_lookback,
                    exit_sma_length=exit_sma_length,
                    take_profit_atr_multiplier=take_profit_atr_multiplier,
                    disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                    exit_mode=exit_mode,
                    confirmation_mode=confirmation_mode,
                    confirmation_lookback=confirmation_lookback,
                    confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                    **rs_params,
                    **context_filter_params,
                )
                try:
                    data, cache_file = _read_dataframe_cache(paths)
                except Exception as error:
                    print(
                        f"{symbol} 130m cache okunamadi; yeniden hazirlanacak: {error}"
                    )
                    data = None
                    cache_file = None
                if data is None:
                    per_symbol_130m_misses += 1
                    files_to_build.append(file_path)
                    continue
                missing = _cache_missing_columns(
                    {symbol: data},
                    hurst_filter_mode=hurst_filter_mode,
                    hurst_threshold=hurst_threshold,
                    expected_symbols=[symbol],
                    use_market_calendar=use_market_calendar,
                    include_partial_session_bars=include_partial_session_bars,
                    strategy_name=strategy_name,
                    rsi_length=rsi_length,
                    rsi_entry_threshold=rsi_entry_threshold,
                    bollinger_length=bollinger_length,
                    bollinger_std=bollinger_std,
                    bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                    use_quality_filter=use_quality_filter,
                    min_price_filter=min_price_filter,
                    min_atr_pct_filter=min_atr_pct_filter,
                    max_atr_pct_filter=max_atr_pct_filter,
                    min_avg_dollar_volume=min_avg_dollar_volume,
                    avg_volume_lookback=avg_volume_lookback,
                    trend_sma_length=trend_sma_length,
                    setup_lookback=setup_lookback,
                    exit_sma_length=exit_sma_length,
                    take_profit_atr_multiplier=take_profit_atr_multiplier,
                    disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                    exit_mode=exit_mode,
                    confirmation_mode=confirmation_mode,
                    confirmation_lookback=confirmation_lookback,
                    confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                    **rs_params,
                    **context_filter_params,
                )
                if missing:
                    per_symbol_130m_misses += 1
                    files_to_build.append(file_path)
                    print(
                        f"{symbol} 130m cache uyumsuz; yeniden hazirlanacak: "
                        f"{', '.join(next(iter(missing.values())))}"
                    )
                    continue
                per_symbol_130m_hits += 1
                asset_data[symbol] = data
                load_results.append(_build_load_results({symbol: data})[0])
        elif cache_mode == "off":
            files_to_build = list(selected_csv_files)
    cache_load_seconds += time.perf_counter() - cache_load_start

    if cache_mode == "per_symbol":
        print(f"130m cache hit: {per_symbol_130m_hits}")
        print(f"130m cache miss/rebuild: {per_symbol_130m_misses}")
        timing.add(
            "per-symbol cache hits",
            seconds=per_symbol_130m_hits,
            notes="130m",
        )
        timing.add(
            "per-symbol cache misses",
            seconds=per_symbol_130m_misses,
            notes="130m",
        )
        timing.add("cache load seconds", seconds=cache_load_seconds, notes="130m")

    if asset_data and not files_to_build:
        timing.add("Benchmark preparation", notes="skipped; cache hit")
        timing.add("asset 130m build", notes="skipped; cache hit")

    if files_to_build:
        benchmark_error = None
        benchmark_missing = False
        benchmark_not_ready = False
        ready_days = 0
        with timing.stage("Benchmark preparation", str(benchmark_file or benchmark_symbol)):
            if benchmark_file is None:
                benchmark_file = _find_benchmark_file(csv_files, benchmark_symbol)
            if benchmark_file is None and benchmark_symbol.upper() != "SPY":
                benchmark_file = _find_benchmark_file(csv_files, "SPY")
            if benchmark_file is None:
                benchmark_missing = True
            else:
                try:
                    benchmark_data = build_mega_dataframe(
                        benchmark_file,
                        hurst_filter_mode=hurst_filter_mode,
                        hurst_threshold=hurst_threshold,
                        use_market_calendar=use_market_calendar,
                        include_partial_session_bars=include_partial_session_bars,
                        timing=timing,
                        strategy_name=strategy_name,
                        rsi_length=rsi_length,
                        rsi_entry_threshold=rsi_entry_threshold,
                        bollinger_length=bollinger_length,
                        bollinger_std=bollinger_std,
                        bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                        use_quality_filter=use_quality_filter,
                        min_price_filter=min_price_filter,
                        min_atr_pct_filter=min_atr_pct_filter,
                        max_atr_pct_filter=max_atr_pct_filter,
                        min_avg_dollar_volume=min_avg_dollar_volume,
                        avg_volume_lookback=avg_volume_lookback,
                        trend_sma_length=trend_sma_length,
                        setup_lookback=setup_lookback,
                        exit_sma_length=exit_sma_length,
                        take_profit_atr_multiplier=take_profit_atr_multiplier,
                        disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                        exit_mode=exit_mode,
                        confirmation_mode=confirmation_mode,
                        confirmation_lookback=confirmation_lookback,
                        confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                        **donchian_params,
                        **rs_params,
                        **context_filter_params,
                    )
                    regime = calculate_market_regime(benchmark_data)
                    ready_days = int(regime["Regime_Ready"].sum())
                    if ready_days == 0:
                        benchmark_not_ready = True
                    else:
                        print(
                            f"Benchmark: {Path(benchmark_file).name} "
                            f"({ready_days} rejim gunu hazir)\n"
                        )
                except Exception as error:
                    benchmark_error = error
        if benchmark_missing:
            print(
                "Makro rejim filtresi icin QQQ veya SPY CSV dosyasi bulunamadi. "
                "benchmark_file parametresiyle dosya yolunu belirtin."
            )
            timing.finish()
            return
        if benchmark_not_ready:
            print(
                "Benchmark verisi SMA_50/SMA_200 icin yetersiz. En az 201 islem "
                "gunluk intraday benchmark verisi gerekli."
            )
            timing.finish()
            return
        if benchmark_error is not None:
            print(f"Benchmark verisi hazirlanamadi: {benchmark_error}")
            timing.finish()
            return

        build_start = time.perf_counter()
        if parallel_assets and asset_worker_count > 1 and len(files_to_build) > 1:
            asset_parallel_backend = "ProcessPoolExecutor"
            print(
                "Asset preparation paralel calisiyor: "
                f"{len(files_to_build)} sembol, max_workers={asset_worker_count}"
            )
            parallel_start = time.perf_counter()
            worker_tasks = [
                {
                    "symbol": _symbol_from_path(file_path),
                    "file_path": file_path,
                    "benchmark_data": benchmark_data,
                    "hurst_filter_mode": hurst_filter_mode,
                    "hurst_threshold": hurst_threshold,
                    "return_intraday": execution_mode == "intrabar_1m",
                    "use_market_calendar": use_market_calendar,
                    "include_partial_session_bars": include_partial_session_bars,
                    "session_windows": run_session_windows,
                    "calendar_fallback": run_calendar_fallback,
                    "strategy_name": strategy_name,
                    "rsi_length": rsi_length,
                    "rsi_entry_threshold": rsi_entry_threshold,
                    "bollinger_length": bollinger_length,
                    "bollinger_std": bollinger_std,
                    "bb_entry_tolerance_atr": bb_entry_tolerance_atr,
                    "use_quality_filter": use_quality_filter,
                    "min_price_filter": min_price_filter,
                    "min_atr_pct_filter": min_atr_pct_filter,
                    "max_atr_pct_filter": max_atr_pct_filter,
                    "min_avg_dollar_volume": min_avg_dollar_volume,
                    "avg_volume_lookback": avg_volume_lookback,
                    "trend_sma_length": trend_sma_length,
                    "setup_lookback": setup_lookback,
                    "exit_sma_length": exit_sma_length,
                    "take_profit_atr_multiplier": take_profit_atr_multiplier,
                    "disaster_stop_atr_multiplier": disaster_stop_atr_multiplier,
                    "exit_mode": exit_mode,
                    "confirmation_mode": confirmation_mode,
                    "confirmation_lookback": confirmation_lookback,
                    "confirmation_min_rebound_atr": confirmation_min_rebound_atr,
                    "csm_lookback_days": csm_lookback_days,
                    "csm_top_n": csm_top_n,
                    "csm_rebalance_frequency": csm_rebalance_frequency,
                    "csm_trend_sma_days": csm_trend_sma_days,
                    "csm_use_market_regime": csm_use_market_regime,
                    "csm_market_regime_sma_days": csm_market_regime_sma_days,
                    "csm_min_price": csm_min_price,
                    "csm_max_atr_pct": csm_max_atr_pct,
                    **dm_params,
                    **donchian_params,
                    **rs_params,
                    **context_filter_params,
                }
                for file_path in files_to_build
            ]
            worker_results = {}
            asset_errors = []
            with ProcessPoolExecutor(max_workers=asset_worker_count) as executor:
                result_iterator = executor.map(
                    _build_asset_worker,
                    worker_tasks,
                    chunksize=parallel_chunk_size_value,
                )
                for task, future_result in zip(worker_tasks, result_iterator):
                    future_symbol = task["symbol"]
                    try:
                        result = future_result
                    except Exception as error:
                        result = {
                            "symbol": future_symbol,
                            "success": False,
                            "data": None,
                            "intraday_df": None,
                            "error": str(error),
                            "load_result": None,
                            "seconds": 0.0,
                            "timings": [],
                        }
                    worker_results[result["symbol"]] = result
                    if not result["success"]:
                        asset_errors.append((result["symbol"], result["error"]))

            for file_path in files_to_build:
                ticker = _symbol_from_path(file_path)
                result = worker_results.get(ticker)
                if result is None or not result["success"]:
                    continue
                asset_data[ticker] = result["data"]
                load_results.append(result["load_result"])
                if execution_mode == "intrabar_1m":
                    intraday_1m_data[ticker] = result["intraday_df"]
                for row in result.get("timings", []):
                    worker_timing_rows.append(row)
                    timing.add(
                        row["Stage"],
                        seconds=row["Seconds"],
                        notes=f"{ticker}; {row.get('Notes', '')}",
                    )

            elapsed = time.perf_counter() - parallel_start
            asset_build_elapsed = elapsed
            successful_count = len(asset_data)
            failed_count = len(asset_errors)
            avg_seconds = (
                sum(result["seconds"] for result in worker_results.values())
                / max(len(worker_results), 1)
            )
            avg_worker_seconds = avg_seconds
            max_worker_seconds = max(
                (float(result.get("seconds", 0.0) or 0.0) for result in worker_results.values()),
                default=0.0,
            )
            parallel_overhead_estimate_sec = max(0.0, elapsed - max_worker_seconds)
            print(
                "Asset preparation tamamlandi: "
                f"basarili={len(worker_results) - failed_count}, hata={failed_count}, "
                f"sure={elapsed:.2f}s"
            )
            if asset_errors:
                print("Asset preparation hatalari:")
                for symbol, error in asset_errors[:20]:
                    print(f"   {symbol}: {error}")
                    excluded_rows.append(
                        {
                            "Symbol": symbol,
                            "Reason": "asset_preparation_error",
                            "Error": error,
                        }
                    )
                if len(asset_errors) > 20:
                    print(f"   ... {len(asset_errors) - 20} hata daha")
                for symbol, error in asset_errors[20:]:
                    excluded_rows.append(
                        {
                            "Symbol": symbol,
                            "Reason": "asset_preparation_error",
                            "Error": error,
                        }
                    )
            timing.add(
                "asset 130m build parallel",
                seconds=elapsed,
                notes=(
                    f"avg seconds per symbol={avg_seconds:.3f}; "
                    f"failed symbols count={failed_count}; "
                    f"max_workers={asset_worker_count}"
                ),
            )
        else:
            with timing.stage(
                "asset 130m build",
                f"symbols={len(files_to_build)}; parallel=False",
            ):
                asset_parallel_backend = "sequential"
                sequential_fallback = bool(files_to_build)
                sequential_start = time.perf_counter()
                total = len(files_to_build)
                for index, file_path in enumerate(files_to_build, 1):
                    ticker = _symbol_from_path(file_path)
                    print(f"[{index}/{total}] {ticker} verisi hazirlaniyor...")

                    try:
                        build_result = build_mega_dataframe(
                            file_path,
                            benchmark_df=benchmark_data,
                            hurst_filter_mode=hurst_filter_mode,
                            hurst_threshold=hurst_threshold,
                            return_intraday=execution_mode == "intrabar_1m",
                            use_market_calendar=use_market_calendar,
                            include_partial_session_bars=include_partial_session_bars,
                            session_windows=run_session_windows,
                            calendar_fallback=run_calendar_fallback,
                            timing=timing,
                            strategy_name=strategy_name,
                            rsi_length=rsi_length,
                            rsi_entry_threshold=rsi_entry_threshold,
                            bollinger_length=bollinger_length,
                            bollinger_std=bollinger_std,
                            bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                            use_quality_filter=use_quality_filter,
                            min_price_filter=min_price_filter,
                            min_atr_pct_filter=min_atr_pct_filter,
                            max_atr_pct_filter=max_atr_pct_filter,
                            min_avg_dollar_volume=min_avg_dollar_volume,
                            avg_volume_lookback=avg_volume_lookback,
                            trend_sma_length=trend_sma_length,
                            setup_lookback=setup_lookback,
                            exit_sma_length=exit_sma_length,
                            take_profit_atr_multiplier=take_profit_atr_multiplier,
                            disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                            exit_mode=exit_mode,
                            confirmation_mode=confirmation_mode,
                            confirmation_lookback=confirmation_lookback,
                            confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                            **donchian_params,
                            **rs_params,
                            **context_filter_params,
                        )
                        if execution_mode == "intrabar_1m":
                            data, intraday_df = build_result
                            intraday_1m_data[ticker] = intraday_df
                        else:
                            data = build_result
                        asset_data[ticker] = data
                        load_results.append(_build_load_results({ticker: data})[0])
                    except Exception as error:
                        print(f"   {ticker} dosyasinda hata: {error}")
                        excluded_rows.append(
                            {
                                "Symbol": ticker,
                                "Reason": "asset_preparation_error",
                                "Error": str(error),
                            }
                        )

                asset_build_elapsed = time.perf_counter() - sequential_start

        cache_build_seconds += time.perf_counter() - build_start
        if asset_data and cache_mode == "global":
            cache_write_start = time.perf_counter()
            pd.to_pickle(asset_data, cache_path)
            print(f"Onbellek kaydedildi: {cache_path}")
            if execution_mode == "intrabar_1m" and intraday_1m_data:
                pd.to_pickle(intraday_1m_data, intraday_cache_path)
                print(f"Intraday onbellek kaydedildi: {intraday_cache_path}")
            cache_write_elapsed = time.perf_counter() - cache_write_start
            cache_write_seconds += cache_write_elapsed
            cache_build_seconds += cache_write_elapsed
            timing.add("cache build seconds", seconds=cache_build_seconds, notes="global")
        elif asset_data and cache_mode == "per_symbol":
            cache_write_start = time.perf_counter()
            for file_path in files_to_build:
                symbol = _symbol_from_path(file_path)
                data = asset_data.get(symbol)
                if data is None:
                    continue
                _, paths = _per_symbol_cache_paths_for_file(
                    data_folder_path,
                    file_path,
                    "130m",
                    hurst_filter_mode,
                    hurst_threshold,
                    use_market_calendar,
                    include_partial_session_bars,
                    strategy_name=strategy_name,
                    rsi_length=rsi_length,
                    rsi_entry_threshold=rsi_entry_threshold,
                    bollinger_length=bollinger_length,
                    bollinger_std=bollinger_std,
                    bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                    use_quality_filter=use_quality_filter,
                    min_price_filter=min_price_filter,
                    min_atr_pct_filter=min_atr_pct_filter,
                    max_atr_pct_filter=max_atr_pct_filter,
                    min_avg_dollar_volume=min_avg_dollar_volume,
                    avg_volume_lookback=avg_volume_lookback,
                    trend_sma_length=trend_sma_length,
                    setup_lookback=setup_lookback,
                    exit_sma_length=exit_sma_length,
                    take_profit_atr_multiplier=take_profit_atr_multiplier,
                    disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                    exit_mode=exit_mode,
                    confirmation_mode=confirmation_mode,
                    confirmation_lookback=confirmation_lookback,
                    confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                    **rs_params,
                    **context_filter_params,
                )
                _write_dataframe_cache(data, paths)
                if execution_mode == "intrabar_1m" and symbol in intraday_1m_data:
                    _, intraday_paths = _per_symbol_cache_paths_for_file(
                        data_folder_path,
                        file_path,
                        "1m",
                        hurst_filter_mode,
                        hurst_threshold,
                        use_market_calendar,
                        include_partial_session_bars,
                        strategy_name=strategy_name,
                        rsi_length=rsi_length,
                        rsi_entry_threshold=rsi_entry_threshold,
                        bollinger_length=bollinger_length,
                        bollinger_std=bollinger_std,
                        bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                        use_quality_filter=use_quality_filter,
                        min_price_filter=min_price_filter,
                        min_atr_pct_filter=min_atr_pct_filter,
                        max_atr_pct_filter=max_atr_pct_filter,
                        min_avg_dollar_volume=min_avg_dollar_volume,
                        avg_volume_lookback=avg_volume_lookback,
                        trend_sma_length=trend_sma_length,
                        setup_lookback=setup_lookback,
                        exit_sma_length=exit_sma_length,
                        take_profit_atr_multiplier=take_profit_atr_multiplier,
                        disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                        exit_mode=exit_mode,
                        confirmation_mode=confirmation_mode,
                        confirmation_lookback=confirmation_lookback,
                        confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                        **rs_params,
                        **context_filter_params,
                    )
                    _write_dataframe_cache(intraday_1m_data[symbol], intraday_paths)
            cache_build_seconds += time.perf_counter() - cache_write_start
            timing.add("cache build seconds", seconds=cache_build_seconds, notes="130m")
            if execution_mode == "intrabar_1m" and intraday_1m_data:
                intraday_built_with_assets = bool(intraday_1m_data)
                timing.add(
                    "intraday 1m build",
                    notes="built during asset 130m build from same CSV reads",
                )
            cache_write_elapsed = time.perf_counter() - cache_write_start
            cache_write_seconds += cache_write_elapsed
            cache_build_seconds += 0.0

    if not asset_data:
        pd.DataFrame(excluded_rows).to_csv(excluded_symbols_path, index=False)
        timing.finish()
        raise RuntimeError(
            "No assets were successfully prepared; check asset preparation errors."
        )

    if execution_mode == "intrabar_1m":
        intraday_files = [
            file_path
            for file_path in selected_csv_files
            if _symbol_from_path(file_path) in asset_data
        ]
        intraday_error = None
        intraday_files_to_build = [
            file_path
            for file_path in intraday_files
            if _symbol_from_path(file_path) not in intraday_1m_data
        ]
        if cache_mode == "per_symbol":
            intraday_load_start = time.perf_counter()
            with timing.stage("intraday 1m cache load", "mode=per_symbol"):
                remaining = []
                for file_path in intraday_files_to_build:
                    symbol, paths = _per_symbol_cache_paths_for_file(
                        data_folder_path,
                        file_path,
                        "1m",
                        hurst_filter_mode,
                        hurst_threshold,
                        use_market_calendar,
                        include_partial_session_bars,
                        strategy_name=strategy_name,
                        rsi_length=rsi_length,
                        rsi_entry_threshold=rsi_entry_threshold,
                        bollinger_length=bollinger_length,
                        bollinger_std=bollinger_std,
                        bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                        use_quality_filter=use_quality_filter,
                        min_price_filter=min_price_filter,
                        min_atr_pct_filter=min_atr_pct_filter,
                        max_atr_pct_filter=max_atr_pct_filter,
                        min_avg_dollar_volume=min_avg_dollar_volume,
                        avg_volume_lookback=avg_volume_lookback,
                        trend_sma_length=trend_sma_length,
                        setup_lookback=setup_lookback,
                        exit_sma_length=exit_sma_length,
                        take_profit_atr_multiplier=take_profit_atr_multiplier,
                        disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                        exit_mode=exit_mode,
                        confirmation_mode=confirmation_mode,
                        confirmation_lookback=confirmation_lookback,
                        confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                        **rs_params,
                        **context_filter_params,
                    )
                    try:
                        data, cache_file = _read_dataframe_cache(paths)
                    except Exception as error:
                        print(
                            f"{symbol} 1m cache okunamadi; yeniden hazirlanacak: {error}"
                        )
                        data = None
                    if data is None:
                        per_symbol_1m_misses += 1
                        remaining.append(file_path)
                        continue
                    missing = _intraday_cache_missing(
                        {symbol: data},
                        expected_symbols=[symbol],
                        use_market_calendar=use_market_calendar,
                    )
                    if missing:
                        per_symbol_1m_misses += 1
                        remaining.append(file_path)
                        print(
                            f"{symbol} 1m cache uyumsuz; yeniden hazirlanacak: "
                            f"{', '.join(next(iter(missing.values())))}"
                        )
                        continue
                    per_symbol_1m_hits += 1
                    intraday_1m_data[symbol] = data
                intraday_files_to_build = remaining
            cache_load_seconds += time.perf_counter() - intraday_load_start
            print(f"intraday cache hit: {per_symbol_1m_hits}")
            print(f"intraday cache miss/rebuild: {per_symbol_1m_misses}")
            timing.add(
                "per-symbol cache hits",
                seconds=per_symbol_1m_hits,
                notes="1m",
            )
            timing.add(
                "per-symbol cache misses",
                seconds=per_symbol_1m_misses,
                notes="1m",
            )
            timing.add("cache load seconds", seconds=cache_load_seconds, notes="1m")
        elif cache_mode == "global" and intraday_files_to_build:
            with timing.stage("intraday 1m cache load", str(intraday_cache_path)):
                if intraday_cache_path.exists():
                    print(f"Intraday onbellek bulundu: {intraday_cache_path}")
                    try:
                        cached_intraday_data = pd.read_pickle(intraday_cache_path)
                        missing_by_symbol = _intraday_cache_missing(
                            cached_intraday_data,
                            expected_symbols=[
                                _symbol_from_path(file_path)
                                for file_path in intraday_files
                            ],
                            use_market_calendar=use_market_calendar,
                        )
                        if missing_by_symbol:
                            first_symbol = next(iter(missing_by_symbol))
                            missing = ", ".join(missing_by_symbol[first_symbol])
                            print(
                                "Intraday onbellek uyumsuz; 1m veri yeniden "
                                f"hazirlanacak. Ornek: {first_symbol}: {missing}"
                            )
                        else:
                            intraday_1m_data = cached_intraday_data
                    except Exception as error:
                        print(
                            "Intraday onbellek okunamadi; 1m veri yeniden "
                            f"hazirlanacak. Hata: {error}"
                        )
            intraday_files_to_build = [
                file_path
                for file_path in intraday_files
                if _symbol_from_path(file_path) not in intraday_1m_data
            ]
        elif not intraday_files_to_build:
            timing.add(
                "intraday 1m cache load",
                notes="skipped; built during asset 130m build",
            )

        if intraday_files_to_build:
            with timing.stage("intraday 1m build", f"files={len(intraday_files_to_build)}"):
                try:
                    built_intraday_data = _load_intraday_data_for_files(
                        intraday_files_to_build,
                        use_market_calendar=use_market_calendar,
                        session_windows=run_session_windows,
                        calendar_fallback=run_calendar_fallback,
                        timing=timing,
                    )
                    intraday_1m_data.update(built_intraday_data)
                    if cache_mode == "global":
                        pd.to_pickle(intraday_1m_data, intraday_cache_path)
                        print(f"Intraday onbellek kaydedildi: {intraday_cache_path}")
                    elif cache_mode == "per_symbol":
                        cache_write_start = time.perf_counter()
                        for file_path in intraday_files_to_build:
                            symbol = _symbol_from_path(file_path)
                            data = built_intraday_data.get(symbol)
                            if data is None:
                                continue
                            _, paths = _per_symbol_cache_paths_for_file(
                                data_folder_path,
                                file_path,
                                "1m",
                                hurst_filter_mode,
                                hurst_threshold,
                                use_market_calendar,
                                include_partial_session_bars,
                                strategy_name=strategy_name,
                                rsi_length=rsi_length,
                                rsi_entry_threshold=rsi_entry_threshold,
                                bollinger_length=bollinger_length,
                                bollinger_std=bollinger_std,
                                bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                                use_quality_filter=use_quality_filter,
                                min_price_filter=min_price_filter,
                                min_atr_pct_filter=min_atr_pct_filter,
                                max_atr_pct_filter=max_atr_pct_filter,
                                min_avg_dollar_volume=min_avg_dollar_volume,
                                avg_volume_lookback=avg_volume_lookback,
                                trend_sma_length=trend_sma_length,
                                setup_lookback=setup_lookback,
                                exit_sma_length=exit_sma_length,
                                take_profit_atr_multiplier=take_profit_atr_multiplier,
                                disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                                exit_mode=exit_mode,
                                confirmation_mode=confirmation_mode,
                                confirmation_lookback=confirmation_lookback,
                                confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                                **rs_params,
                                **context_filter_params,
                            )
                            _write_dataframe_cache(data, paths)
                        cache_build_seconds += time.perf_counter() - cache_write_start
                        timing.add("cache build seconds", seconds=cache_build_seconds, notes="1m")
                except Exception as error:
                    intraday_error = error
        elif not intraday_built_with_assets:
            timing.add("intraday 1m build", notes="skipped; cache hit")
        if intraday_error is not None:
            print(f"Intrabar execution hazirlanamadi: {intraday_error}")
            timing.finish()
            return
    else:
        timing.add(
            "intraday 1m cache load",
            notes=f"skipped; execution_mode={execution_mode}",
        )
        timing.add(
            "intraday 1m build",
            notes=f"skipped; execution_mode={execution_mode}",
        )

    portfolio_report_path = _output_path(output_dir, "portfoy_raporu", output_suffix)
    trade_log_path = _output_path(output_dir, "portfoy_islemleri", output_suffix)
    universe_report_path = _output_path(output_dir, "evren_sinyal_raporu", output_suffix)
    benchmark_report_path = _output_path(output_dir, "benchmark_raporu", output_suffix)
    split_report_path = _output_path(output_dir, "split_raporu", output_suffix)
    fold_report_path = _output_path(output_dir, "fold_raporu", output_suffix)
    with timing.stage("CSV writing", "excluded symbols"):
        pd.DataFrame(excluded_rows).to_csv(excluded_symbols_path, index=False)
    if strategy_name == "relative_strength_rotation":
        if evaluation_mode != "single":
            raise ValueError(
                "relative_strength_rotation su anda evaluation_mode='single' "
                "ile destekleniyor."
            )
        if rs_use_market_regime and benchmark_data is None:
            benchmark_path = benchmark_file
            if benchmark_path is None:
                benchmark_path = _find_benchmark_file(csv_files, benchmark_symbol)
            if benchmark_path is None and benchmark_symbol.upper() != "SPY":
                benchmark_path = _find_benchmark_file(csv_files, "SPY")
            if benchmark_path is None:
                raise ValueError(
                    "relative_strength_rotation market regime icin QQQ veya SPY "
                    "benchmark verisi gerekli."
                )
            benchmark_data = build_mega_dataframe(
                benchmark_path,
                hurst_filter_mode=hurst_filter_mode,
                hurst_threshold=hurst_threshold,
                use_market_calendar=use_market_calendar,
                include_partial_session_bars=include_partial_session_bars,
                strategy_name=strategy_name,
                rsi_length=rsi_length,
                rsi_entry_threshold=rsi_entry_threshold,
                bollinger_length=bollinger_length,
                bollinger_std=bollinger_std,
                bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                use_quality_filter=use_quality_filter,
                min_price_filter=min_price_filter,
                min_atr_pct_filter=min_atr_pct_filter,
                max_atr_pct_filter=max_atr_pct_filter,
                min_avg_dollar_volume=min_avg_dollar_volume,
                avg_volume_lookback=avg_volume_lookback,
                trend_sma_length=trend_sma_length,
                setup_lookback=setup_lookback,
                exit_sma_length=exit_sma_length,
                take_profit_atr_multiplier=take_profit_atr_multiplier,
                disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                exit_mode=exit_mode,
                confirmation_mode=confirmation_mode,
                confirmation_lookback=confirmation_lookback,
                confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                **rs_params,
                **context_filter_params,
            )
        asset_data = _apply_relative_strength_rotation_top_signals(
            asset_data,
            rs_params,
            benchmark_data=benchmark_data,
        )
        load_results = _build_load_results(asset_data)
    elif strategy_name == "cross_sectional_momentum":
        if evaluation_mode != "single":
            raise ValueError(
                "cross_sectional_momentum su anda evaluation_mode='single' "
                "ile destekleniyor."
            )
        if csm_use_market_regime and benchmark_data is None:
            benchmark_path = benchmark_file
            if benchmark_path is None:
                benchmark_path = _find_benchmark_file(csv_files, benchmark_symbol)
            if benchmark_path is None and benchmark_symbol.upper() != "SPY":
                benchmark_path = _find_benchmark_file(csv_files, "SPY")
            if benchmark_path is None:
                raise ValueError(
                    "cross_sectional_momentum market regime icin QQQ veya SPY "
                    "benchmark verisi gerekli."
                )
            benchmark_data = build_mega_dataframe(
                benchmark_path,
                hurst_filter_mode=hurst_filter_mode,
                hurst_threshold=hurst_threshold,
                use_market_calendar=use_market_calendar,
                include_partial_session_bars=include_partial_session_bars,
                strategy_name=strategy_name,
                rsi_length=rsi_length,
                rsi_entry_threshold=rsi_entry_threshold,
                bollinger_length=bollinger_length,
                bollinger_std=bollinger_std,
                bb_entry_tolerance_atr=bb_entry_tolerance_atr,
                use_quality_filter=use_quality_filter,
                min_price_filter=min_price_filter,
                min_atr_pct_filter=min_atr_pct_filter,
                max_atr_pct_filter=max_atr_pct_filter,
                min_avg_dollar_volume=min_avg_dollar_volume,
                avg_volume_lookback=avg_volume_lookback,
                trend_sma_length=trend_sma_length,
                setup_lookback=setup_lookback,
                exit_sma_length=exit_sma_length,
                take_profit_atr_multiplier=take_profit_atr_multiplier,
                disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                exit_mode=exit_mode,
                confirmation_mode=confirmation_mode,
                confirmation_lookback=confirmation_lookback,
                confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                **csm_params,
                **context_filter_params,
            )
        asset_data = _apply_cross_sectional_momentum_top_signals(
            asset_data,
            csm_params,
            benchmark_data=benchmark_data,
        )
        load_results = _build_load_results(asset_data)
    _validate_asset_signal_columns(asset_data)
    signal_diagnostics = _asset_signal_diagnostics(asset_data)
    for result in load_results:
        result.update(universe_summary)
    calendar_summary = _calendar_report_summary(asset_data)
    if return_prepared_data and prepared_data_only:
        timing.finish()
        return {
            "metrics": {},
            "asset_data": asset_data,
            "intraday_data": intraday_1m_data,
            "benchmark_data": benchmark_data,
            "load_results": load_results,
            "calendar_summary": calendar_summary,
            "signal_diagnostics": signal_diagnostics,
            "selected_symbols": selected_symbols,
            "csv_files": csv_files,
            "cache_summary": _cache_summary_dict(),
        }

    if evaluation_mode == "split":
        print("Split evaluation modu calisiyor...")
        timing.add("Benchmark comparison", notes="skipped; evaluation_mode=split")
        train_manager, train_metrics = _run_portfolio_period(
            asset_data,
            ranking_mode,
            "Train",
            start=train_start,
            end=train_end,
            execution_mode=execution_mode,
            intraday_data=intraday_1m_data,
            timing=timing,
            exit_mode=exit_mode,
            time_stop_bars=time_stop_bars,
            fast_backtest=fast_backtest,
            commission_rate=commission_rate,
            slippage_mu=slippage_mu,
            risk_off_exit_mode=risk_off_exit_mode,
        )
        test_manager, test_metrics = _run_portfolio_period(
            asset_data,
            ranking_mode,
            "Test",
            start=test_start,
            end=test_end,
            execution_mode=execution_mode,
            intraday_data=intraday_1m_data,
            timing=timing,
            exit_mode=exit_mode,
            time_stop_bars=time_stop_bars,
            fast_backtest=fast_backtest,
            commission_rate=commission_rate,
            slippage_mu=slippage_mu,
            risk_off_exit_mode=risk_off_exit_mode,
        )
        report_rows = [metrics for metrics in [train_metrics, test_metrics] if metrics]
        if not report_rows:
            print("Split raporu uretilemedi; train/test donemlerinde sonuc yok.")
            timing.finish()
            return
        for row in report_rows:
            row["Makro Rejimde Reddedilen Sinyal"] = _risk_off_rejected_total(
                load_results
            )
            row.update(calendar_summary)
        trade_logs = []
        for period_name, manager in [("Train", train_manager), ("Test", test_manager)]:
            if manager is not None and manager.trade_log:
                period_trades = pd.DataFrame(manager.trade_log)
                period_trades.insert(0, "Period", period_name)
                trade_logs.append(period_trades)
        with timing.stage("CSV writing", "split portfolio reports"):
            pd.DataFrame(report_rows).to_csv(portfolio_report_path, index=False)
            pd.DataFrame(report_rows).to_csv(split_report_path, index=False)
        if trade_logs:
            combined_trades = pd.concat(trade_logs, ignore_index=True)
            if write_trade_log:
                with timing.stage("CSV writing", "split trade log"):
                    combined_trades.to_csv(trade_log_path, index=False)
            else:
                _write_skipped_trade_log(trade_log_path, clear_stale_trade_log)
                timing.add("CSV writing", notes="split trade log skipped; write_trade_log=False")
            if monte_carlo:
                with timing.stage("Monte Carlo", "split trades"):
                    _write_monte_carlo_reports(
                        combined_trades,
                        output_dir,
                        10000.0,
                        monte_carlo_simulations,
                        output_suffix=output_suffix,
                    )
            else:
                timing.add("Monte Carlo", notes="skipped; disabled")
        else:
            if write_trade_log:
                with timing.stage("CSV writing", "empty split trade log"):
                    pd.DataFrame().to_csv(trade_log_path, index=False)
            else:
                _write_skipped_trade_log(trade_log_path, clear_stale_trade_log)
                timing.add("CSV writing", notes="empty split trade log skipped; write_trade_log=False")
            if monte_carlo:
                with timing.stage("Monte Carlo", "empty split trades"):
                    _write_monte_carlo_reports(
                        pd.DataFrame(),
                        output_dir,
                        10000.0,
                        monte_carlo_simulations,
                        output_suffix=output_suffix,
                    )
            else:
                timing.add("Monte Carlo", notes="skipped; disabled")
        with timing.stage("CSV writing", "split universe report"):
            pd.DataFrame(load_results).sort_values(
                "Giris Sinyali Sayisi",
                ascending=False,
            ).to_csv(universe_report_path, index=False)
        print(f"Split raporu: {split_report_path}")
        print(f"Portfoy raporu: {portfolio_report_path}")
        timing.finish()
        split_result = pd.DataFrame(report_rows)
        if return_prepared_data:
            return {
                "metrics": split_result,
                "asset_data": asset_data,
                "intraday_data": intraday_1m_data,
                "load_results": load_results,
                "calendar_summary": calendar_summary,
                "selected_symbols": selected_symbols,
                "csv_files": csv_files,
                "cache_summary": _cache_summary_dict(),
            }
        return split_result

    if evaluation_mode == "walk_forward":
        print("Walk-forward evaluation modu calisiyor...")
        timing.add("Benchmark comparison", notes="skipped; evaluation_mode=walk_forward")
        folds = _build_walk_forward_folds(
            asset_data,
            walk_train_months,
            walk_test_months,
        )
        if not folds:
            print("Walk-forward fold uretilemedi; veri araligi yetersiz.")
            timing.finish()
            return
        fold_rows = []
        trade_logs = []
        for fold in folds:
            # Future optimizer hook: train window is defined per fold, but this
            # implementation intentionally reuses the current fixed parameters.
            test_manager, test_metrics = _run_portfolio_period(
                asset_data,
                ranking_mode,
                f"Fold {fold['Fold No']} Test",
                start=fold["Test Start"],
                end=fold["Test End"],
                execution_mode=execution_mode,
                intraday_data=intraday_1m_data,
                timing=timing,
                exit_mode=exit_mode,
                time_stop_bars=time_stop_bars,
                fast_backtest=fast_backtest,
                commission_rate=commission_rate,
                slippage_mu=slippage_mu,
                risk_off_exit_mode=risk_off_exit_mode,
            )
            if not test_metrics:
                continue
            fold_rows.append(
                {
                    "Fold No": fold["Fold No"],
                    "Train Start": fold["Train Start"],
                    "Train End": fold["Train End"],
                    "Test Start": fold["Test Start"],
                    "Test End": fold["Test End"],
                    "Test Net Return": test_metrics.get("Net Getiri (%)", np.nan),
                    "Test CAGR": test_metrics.get("CAGR (%)", np.nan),
                    "Test MaxDD": test_metrics.get("Max Drawdown (%)", np.nan),
                    "Test Sharpe": test_metrics.get("Sharpe Ratio", np.nan),
                    "Test Trade Count": test_metrics.get("Islem Sayisi", np.nan),
                    "Test Profit Factor": test_metrics.get("Profit Factor", np.nan),
                    "Test Expectancy R": test_metrics.get("Expectancy R", np.nan),
                    **calendar_summary,
                }
            )
            if test_manager is not None and test_manager.trade_log:
                period_trades = pd.DataFrame(test_manager.trade_log)
                period_trades.insert(0, "Fold No", fold["Fold No"])
                trade_logs.append(period_trades)
        if not fold_rows:
            print("Walk-forward raporu uretilemedi; fold testlerinde sonuc yok.")
            timing.finish()
            return
        with timing.stage("CSV writing", "walk-forward reports"):
            pd.DataFrame(fold_rows).to_csv(fold_report_path, index=False)
            pd.DataFrame(fold_rows).to_csv(portfolio_report_path, index=False)
        if trade_logs:
            combined_trades = pd.concat(trade_logs, ignore_index=True)
            if write_trade_log:
                with timing.stage("CSV writing", "walk-forward trade log"):
                    combined_trades.to_csv(trade_log_path, index=False)
            else:
                _write_skipped_trade_log(trade_log_path, clear_stale_trade_log)
                timing.add("CSV writing", notes="walk-forward trade log skipped; write_trade_log=False")
            if monte_carlo:
                with timing.stage("Monte Carlo", "walk-forward trades"):
                    _write_monte_carlo_reports(
                        combined_trades,
                        output_dir,
                        10000.0,
                        monte_carlo_simulations,
                        output_suffix=output_suffix,
                    )
            else:
                timing.add("Monte Carlo", notes="skipped; disabled")
        else:
            if write_trade_log:
                with timing.stage("CSV writing", "empty walk-forward trade log"):
                    pd.DataFrame().to_csv(trade_log_path, index=False)
            else:
                _write_skipped_trade_log(trade_log_path, clear_stale_trade_log)
                timing.add("CSV writing", notes="empty walk-forward trade log skipped; write_trade_log=False")
            if monte_carlo:
                with timing.stage("Monte Carlo", "empty walk-forward trades"):
                    _write_monte_carlo_reports(
                        pd.DataFrame(),
                        output_dir,
                        10000.0,
                        monte_carlo_simulations,
                        output_suffix=output_suffix,
                    )
            else:
                timing.add("Monte Carlo", notes="skipped; disabled")
        with timing.stage("CSV writing", "walk-forward universe report"):
            pd.DataFrame(load_results).sort_values(
                "Giris Sinyali Sayisi",
                ascending=False,
            ).to_csv(universe_report_path, index=False)
        print(f"Fold raporu: {fold_report_path}")
        print(f"Portfoy raporu: {portfolio_report_path}")
        timing.finish()
        fold_result = pd.DataFrame(fold_rows)
        if return_prepared_data:
            return {
                "metrics": fold_result,
                "asset_data": asset_data,
                "intraday_data": intraday_1m_data,
                "load_results": load_results,
                "calendar_summary": calendar_summary,
                "selected_symbols": selected_symbols,
                "csv_files": csv_files,
                "cache_summary": _cache_summary_dict(),
            }
        return fold_result

    print(
        f"\n{len(asset_data)} varlik ortak zaman ekseninde birlestiriliyor..."
    )
    if strategy_name == "relative_strength_rotation":
        with timing.stage("RelativeStrengthRotation run_backtest", "single"):
            metrics = _run_relative_strength_rotation_backtest(
                asset_data,
                rs_params,
                benchmark_data=benchmark_data,
            )
    elif strategy_name == "dual_momentum":
        with timing.stage("DualMomentum run_backtest", "single"):
            metrics = _run_dual_momentum_backtest(
                asset_data,
                dm_params,
                benchmark_data=benchmark_data,
            )
        rs_trade_log = metrics.pop("_trade_log", [])
        rs_equity_curve = metrics.pop("_equity_curve", [])
        manager = _SimpleBacktestResult(
            trade_log=rs_trade_log,
            equity_curve=rs_equity_curve,
            initial_cash=10000.0,
        )
        timing.add("portfolio fast arrays build seconds", notes="not applicable; dm")
        timing.add("portfolio loop seconds", notes="not applicable; dm")
    elif strategy_name == "donchian_breakout":
        with timing.stage("DonchianBreakout run_backtest", "single"):
            metrics = _run_donchian_breakout_backtest(
                asset_data,
                donchian_params,
                benchmark_data=benchmark_data,
            )
        rs_trade_log = metrics.pop("_trade_log", [])
        rs_equity_curve = metrics.pop("_equity_curve", [])
        manager = _SimpleBacktestResult(
            trade_log=rs_trade_log,
            equity_curve=rs_equity_curve,
            initial_cash=10000.0,
        )
        timing.add("portfolio fast arrays build seconds", notes="not applicable; donchian")
        timing.add("portfolio loop seconds", notes="not applicable; donchian")
    elif strategy_name == "cross_sectional_momentum":
        with timing.stage("CrossSectionalMomentum run_backtest", "single"):
            metrics = _run_cross_sectional_momentum_backtest(
                asset_data,
                csm_params,
                benchmark_data=benchmark_data,
            )
        rs_trade_log = metrics.pop("_trade_log", [])
        rs_equity_curve = metrics.pop("_equity_curve", [])
        manager = _SimpleBacktestResult(
            trade_log=rs_trade_log,
            equity_curve=rs_equity_curve,
            initial_cash=10000.0,
        )
        timing.add("portfolio fast arrays build seconds", notes="not applicable; rs")
        timing.add("portfolio loop seconds", notes="not applicable; rs")
    else:
        with timing.stage("PortfolioManager init", "single"):
            manager = PortfolioManager(
                asset_data=asset_data,
                initial_cash=10000.0,
                max_open_positions=5,
                risk_per_trade=0.02,
                ranking_mode=ranking_mode,
                execution_mode=execution_mode,
                intraday_data=intraday_1m_data,
                verbose=False,
                exit_mode=exit_mode,
                time_stop_bars=time_stop_bars,
                fast_backtest=fast_backtest,
                commission_rate=commission_rate,
                slippage_mu=slippage_mu,
                risk_off_exit_mode=risk_off_exit_mode,
            )
        with timing.stage("PortfolioManager run_backtest", "single"):
            metrics = manager.run_backtest()
        timing.add(
            "portfolio fast arrays build seconds",
            seconds=manager.portfolio_fast_arrays_build_seconds,
            notes=f"fast_backtest={manager.fast_backtest}",
        )
        timing.add(
            "portfolio loop seconds",
            seconds=manager.portfolio_loop_seconds,
            notes=f"fast_backtest={manager.fast_backtest}",
        )
    if not metrics:
        print("Portfoy sonucu uretilemedi; 0 islem metrikleri yaziliyor.")
        metrics = _zero_trade_metrics()
    risk_off_rejected_total = _risk_off_rejected_total(load_results)
    metrics["Makro Rejimde Reddedilen Sinyal"] = risk_off_rejected_total
    metrics.update(calendar_summary)
    metrics.update(signal_diagnostics)
    metrics["total_trades"] = int(metrics.get("Islem Sayisi", 0) or 0)
    metrics["Trade Count"] = metrics["total_trades"]
    print(
        "Experiment diagnostics: "
        f"symbols_loaded={metrics['symbols_loaded']}, "
        f"quality_filter_active_symbols={metrics['quality_filter_active_symbols']}, "
        f"total_raw_entry_signals={metrics['total_raw_entry_signals']}, "
        f"total_confirmed_entry_signals={metrics['total_confirmed_entry_signals']}, "
        f"total_final_entry_signals={metrics['total_final_entry_signals']}, "
        f"total_trades={metrics['total_trades']}"
    )

    benchmark_summary = {}

    if strategy_name == "relative_strength_rotation":
        benchmark_summary.update(
            _benchmark_summary_for_equity_curve(
                asset_data,
                manager.equity_curve,
                metrics.get("Net Getiri (%)", np.nan),
                benchmark_data=benchmark_data,
                benchmark_symbol=benchmark_symbol,
            )
        )
    elif not manager.equity_curve:
        print("Benchmark comparison skipped; equity curve empty")
        timing.add("Benchmark comparison", notes="skipped; equity curve empty")
        benchmark_summary.update(_prefixed_benchmark_metrics("QQQ", _empty_buyhold_metrics()))
        benchmark_summary.update(_prefixed_benchmark_metrics("SPY", _empty_buyhold_metrics()))
        benchmark_summary.update(
            {
                "EqualWeight Universe Return (%)": np.nan,
                "EqualWeight Universe CAGR (%)": np.nan,
                "EqualWeight Universe MaxDD (%)": np.nan,
                "Strategy Excess Return vs QQQ (%)": np.nan,
                "Strategy Excess Return vs EqualWeight (%)": np.nan,
                "Benchmark Comparison": "skipped_empty_equity_curve",
            }
        )
    else:
        strategy_equity = pd.Series(
            [value for _, value in manager.equity_curve],
            index=[timestamp for timestamp, _ in manager.equity_curve],
            dtype=float,
        )
        benchmark_start = strategy_equity.index[0]
        benchmark_end = strategy_equity.index[-1]

    if strategy_name == "relative_strength_rotation":
        timing.add("Benchmark comparison", notes="computed; relative_strength_rotation")
    elif strategy_name == "dual_momentum":
        timing.add("Benchmark comparison", notes="computed; dual_momentum")
    elif strategy_name == "cross_sectional_momentum":
        timing.add("Benchmark comparison", notes="computed; cross_sectional_momentum")
    elif quick_mode and manager.equity_curve:
        print("Benchmark comparison skipped")
        timing.add("Benchmark comparison", notes="skipped; quick_mode=True")
        benchmark_summary.update(_prefixed_benchmark_metrics("QQQ", _empty_buyhold_metrics()))
        benchmark_summary.update(_prefixed_benchmark_metrics("SPY", _empty_buyhold_metrics()))
        benchmark_summary.update(
            {
                "EqualWeight Universe Return (%)": np.nan,
                "EqualWeight Universe CAGR (%)": np.nan,
                "EqualWeight Universe MaxDD (%)": np.nan,
                "Strategy Excess Return vs QQQ (%)": np.nan,
                "Strategy Excess Return vs EqualWeight (%)": np.nan,
                "Benchmark Comparison": "skipped_quick_mode",
            }
        )
    elif manager.equity_curve:
        with timing.stage("Benchmark comparison", "QQQ/SPY/equal-weight"):
            for symbol in ["QQQ", "SPY"]:
                close_series = _load_benchmark_close(
                    asset_data,
                    csv_files,
                    symbol,
                    hurst_filter_mode,
                    hurst_threshold,
                    use_market_calendar,
                    include_partial_session_bars,
                    strategy_name=strategy_name,
                    exit_sma_length=exit_sma_length,
                    take_profit_atr_multiplier=take_profit_atr_multiplier,
                    disaster_stop_atr_multiplier=disaster_stop_atr_multiplier,
                    exit_mode=exit_mode,
                    confirmation_mode=confirmation_mode,
                    confirmation_lookback=confirmation_lookback,
                    confirmation_min_rebound_atr=confirmation_min_rebound_atr,
                )
                if close_series is None:
                    buyhold_metrics = _empty_buyhold_metrics()
                else:
                    try:
                        buyhold_metrics = calculate_buyhold_metrics(
                            close_series,
                            benchmark_start,
                            benchmark_end,
                        )
                    except Exception as error:
                        print(f"Uyari: {symbol} buy-hold hesaplanamadi: {error}")
                        buyhold_metrics = _empty_buyhold_metrics()
                benchmark_summary.update(_prefixed_benchmark_metrics(symbol, buyhold_metrics))

            try:
                equal_weight_metrics = calculate_equal_weight_benchmark(
                    asset_data,
                    benchmark_start,
                    benchmark_end,
                )
            except Exception as error:
                print(f"Uyari: Equal-weight benchmark hesaplanamadi: {error}")
                equal_weight_metrics = _empty_buyhold_metrics()

            benchmark_summary.update(
                {
                    "EqualWeight Universe Return (%)": equal_weight_metrics["Return (%)"],
                    "EqualWeight Universe CAGR (%)": equal_weight_metrics["CAGR (%)"],
                    "EqualWeight Universe MaxDD (%)": equal_weight_metrics["MaxDD (%)"],
                }
            )
            benchmark_summary["Strategy Excess Return vs QQQ (%)"] = (
                metrics["Net Getiri (%)"] - benchmark_summary["QQQ BuyHold Return (%)"]
                if pd.notna(benchmark_summary["QQQ BuyHold Return (%)"])
                else np.nan
            )
            benchmark_summary["Strategy Excess Return vs EqualWeight (%)"] = (
                metrics["Net Getiri (%)"]
                - benchmark_summary["EqualWeight Universe Return (%)"]
                if pd.notna(benchmark_summary["EqualWeight Universe Return (%)"])
                else np.nan
            )
    metrics.update(benchmark_summary)

    with timing.stage("CSV writing", "single reports"):
        pd.DataFrame([metrics]).to_csv(portfolio_report_path, index=False)
        if quick_mode:
            pd.DataFrame(
                [
                    {
                        "Benchmark Comparison": "skipped_quick_mode",
                        "Reason": "quick_mode=True",
                    }
                ]
            ).to_csv(benchmark_report_path, index=False)
        else:
            pd.DataFrame([benchmark_summary]).to_csv(benchmark_report_path, index=False)
        if write_trade_log:
            pd.DataFrame(manager.trade_log).to_csv(trade_log_path, index=False)
        else:
            _write_skipped_trade_log(trade_log_path, clear_stale_trade_log)
            timing.add("CSV writing", notes="trade log skipped; write_trade_log=False")
    if monte_carlo:
        with timing.stage("Monte Carlo", "single trades"):
            _write_monte_carlo_reports(
                manager.trade_log,
                output_dir,
                manager.initial_cash,
                monte_carlo_simulations,
                output_suffix=output_suffix,
            )
    else:
        timing.add("Monte Carlo", notes="skipped; disabled")
    with timing.stage("CSV writing", "single universe report"):
        pd.DataFrame(load_results).sort_values(
            "Giris Sinyali Sayisi",
            ascending=False,
        ).to_csv(universe_report_path, index=False)

    print("\nPORTFOY BACKTEST TAMAMLANDI")
    print(pd.DataFrame([metrics]).to_string(index=False))
    print(f"Portfoy raporu: {portfolio_report_path}")
    print(f"Benchmark raporu: {benchmark_report_path}")
    if write_trade_log:
        print(f"Islem gunlugu: {trade_log_path}")
    else:
        print("Islem gunlugu yazimi atlandi.")
    print(f"Evren sinyal raporu: {universe_report_path}")
    timing.finish()
    metrics["_trade_log"] = list(manager.trade_log)
    if return_prepared_data:
        return {
            "metrics": metrics,
            "asset_data": asset_data,
            "intraday_data": intraday_1m_data,
            "benchmark_data": benchmark_data,
            "load_results": load_results,
            "calendar_summary": calendar_summary,
            "signal_diagnostics": signal_diagnostics,
            "selected_symbols": selected_symbols,
            "csv_files": csv_files,
            "cache_summary": _cache_summary_dict(),
        }
    return metrics


def prepare_universe_data(
    data_folder,
    allowed_symbols=None,
    universe_symbols_file=None,
    run_profile="fast",
    execution_mode="bar_close",
    use_market_calendar=False,
    include_partial_session_bars=False,
    hurst_filter_mode="off",
    hurst_threshold=0.5,
    strategy_name=DEFAULT_STRATEGY_NAME,
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
    confirmation_mode="off",
    confirmation_lookback=1,
    confirmation_min_rebound_atr=0.0,
    excluded_symbols=None,
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
    profile_timing=False,
    max_workers=None,
    worker_utilization_target=0.85,
    parallel_chunk_size=None,
    use_prepared_feature_disk_cache=True,
    prepared_feature_cache_dir=".cache/prepared_features",
    force_rebuild_prepared_cache=False,
):
    prepared = run_universe_scanner(
        data_folder=data_folder,
        allowed_symbols=allowed_symbols,
        excluded_symbols=excluded_symbols,
        universe_symbols_file=universe_symbols_file,
        run_profile=run_profile,
        execution_mode=execution_mode,
        use_market_calendar=use_market_calendar,
        include_partial_session_bars=include_partial_session_bars,
        hurst_filter_mode=hurst_filter_mode,
        hurst_threshold=hurst_threshold,
        strategy_name=strategy_name,
        rsi_length=rsi_length,
        rsi_entry_threshold=rsi_entry_threshold,
        bollinger_length=bollinger_length,
        bollinger_std=bollinger_std,
        bb_entry_tolerance_atr=bb_entry_tolerance_atr,
        use_quality_filter=use_quality_filter,
        min_price_filter=min_price_filter,
        min_atr_pct_filter=min_atr_pct_filter,
        max_atr_pct_filter=max_atr_pct_filter,
        min_avg_dollar_volume=min_avg_dollar_volume,
        avg_volume_lookback=avg_volume_lookback,
        trend_sma_length=trend_sma_length,
        setup_lookback=setup_lookback,
        confirmation_mode=confirmation_mode,
        confirmation_lookback=confirmation_lookback,
        confirmation_min_rebound_atr=confirmation_min_rebound_atr,
        use_entry_context_filter=use_entry_context_filter,
        max_entry_gap_pct=max_entry_gap_pct,
        max_entry_bb_distance_atr=max_entry_bb_distance_atr,
        max_entry_signal_range_pct=max_entry_signal_range_pct,
        min_entry_close_position_in_range=min_entry_close_position_in_range,
        max_entry_close_position_in_range=max_entry_close_position_in_range,
        csm_lookback_days=csm_lookback_days,
        csm_top_n=csm_top_n,
        csm_rebalance_frequency=csm_rebalance_frequency,
        csm_trend_sma_days=csm_trend_sma_days,
        csm_use_market_regime=csm_use_market_regime,
        csm_market_regime_sma_days=csm_market_regime_sma_days,
        csm_min_price=csm_min_price,
        csm_max_atr_pct=csm_max_atr_pct,
        donchian_entry_lookback_days=donchian_entry_lookback_days,
        donchian_exit_lookback_days=donchian_exit_lookback_days,
        donchian_top_n=donchian_top_n,
        donchian_rebalance_frequency=donchian_rebalance_frequency,
        donchian_trend_sma_days=donchian_trend_sma_days,
        donchian_use_market_regime=donchian_use_market_regime,
        donchian_market_regime_sma_days=donchian_market_regime_sma_days,
        donchian_min_price=donchian_min_price,
        donchian_max_atr_pct=donchian_max_atr_pct,
        rs_lookback_days=rs_lookback_days,
        rs_top_n=rs_top_n,
        rs_rebalance_frequency=rs_rebalance_frequency,
        rs_score_mode=rs_score_mode,
        rs_vol_lookback_days=rs_vol_lookback_days,
        rs_trend_sma_days=rs_trend_sma_days,
        rs_use_market_regime=rs_use_market_regime,
        rs_market_regime_sma_days=rs_market_regime_sma_days,
        rs_min_price=rs_min_price,
        rs_max_atr_pct=rs_max_atr_pct,
        profile_timing=profile_timing,
        max_workers=max_workers,
        worker_utilization_target=worker_utilization_target,
        parallel_chunk_size=parallel_chunk_size,
        quick_mode=True if run_profile == "fast" else None,
        return_prepared_data=True,
        prepared_data_only=True,
    )
    if not isinstance(prepared, dict):
        return {}
    asset_data = prepared.get("asset_data", {})
    _validate_asset_signal_columns(asset_data)
    cache_summary = prepared.get("cache_summary", {})
    return {
        "asset_data": asset_data,
        "intraday_data": prepared.get("intraday_data", {}),
        "benchmark_data": prepared.get("benchmark_data"),
        "load_results": prepared.get("load_results", []),
        "symbols": prepared.get("selected_symbols", []),
        "signal_diagnostics": _asset_signal_diagnostics(asset_data),
        "timing": {
            "cache_summary": cache_summary,
            "asset_preparation_seconds": (
                float(cache_summary.get("cache_load_seconds", 0.0))
                + float(cache_summary.get("cache_build_seconds", 0.0))
            ),
        },
        "calendar_summary": prepared.get("calendar_summary", {}),
        "cache_summary": cache_summary,
    }


EXPERIMENT_METRIC_COLUMNS = [
    "Net Getiri (%)",
    "CAGR (%)",
    "Max Drawdown (%)",
    "Sharpe Ratio",
    "Islem Sayisi",
    "Trade Count",
    "Win Rate (%)",
    "Profit Factor",
    "Avg R",
    "Median R",
    "Best R",
    "Worst R",
    "Avg Winner R",
    "Avg Loser R",
    "Expectancy R",
    "Max Consecutive Losses",
    "Stop Exit Count",
    "Take Profit Exit Count",
    "SMA20 Reversion Exit Count",
    "Time Stop Exit Count",
    "Risk Off Exit Count",
    "Forced Final Exit Count",
    "Max Portfolio Heat (%)",
    "Avg Holding Bars",
    "Median Holding Bars",
    "Turnover Estimate",
    "Avg Positions Held",
    "Max Positions Held",
    "Reddedilen Sinyal",
    "Makro Rejimde Reddedilen Sinyal",
    "QQQ BuyHold Return (%)",
    "SPY BuyHold Return (%)",
    "EqualWeight Universe Return (%)",
    "Strategy Excess Return vs QQQ (%)",
    "Strategy Excess Return vs EqualWeight (%)",
    "Benchmark Comparison",
    "Symbols Loaded",
    "Total Bars",
    "Indicators Ready Count",
    "Raw Trigger Count",
    "Raw Entry Signal Count",
    "Quality Filter OK Count",
    "Confirmed Entry Signal Count",
    "Hurst Filter OK Count",
    "Final Entry Signal Count",
    "Risk Off Rejected Signal Count",
    "Entry Context Filter OK Count",
    "Entry Context Rejected Signal Count",
    *PORTFOLIO_REJECTION_COLUMNS,
    "symbols_loaded",
    "quality_filter_active_symbols",
    "total_raw_entry_signals",
    "total_confirmed_entry_signals",
    "total_final_entry_signals",
    "total_trades",
]

EXPERIMENT_PARAM_COLUMNS = [
    "strategy_name",
    "Strategy Name",
    "Strategy Family",
    "hurst_filter_mode",
    "hurst_threshold",
    "ranking_mode",
    "execution_mode",
    "run_profile",
    "quick_mode",
    "rsi_length",
    "rsi_entry_threshold",
    "bollinger_length",
    "bollinger_std",
    "bb_entry_tolerance_atr",
    "use_quality_filter",
    "min_price_filter",
    "min_atr_pct_filter",
    "max_atr_pct_filter",
    "min_avg_dollar_volume",
    "avg_volume_lookback",
    "trend_sma_length",
    "setup_lookback",
    "confirmation_mode",
    "confirmation_lookback",
    "confirmation_min_rebound_atr",
    "exit_mode",
    "exit_sma_length",
    "take_profit_atr_multiplier",
    "disaster_stop_atr_multiplier",
    "time_stop_bars",
    "commission_rate",
    "slippage_mu",
    "risk_off_exit_mode",
    "use_entry_context_filter",
    "max_entry_gap_pct",
    "max_entry_bb_distance_atr",
    "max_entry_signal_range_pct",
    "min_entry_close_position_in_range",
    "max_entry_close_position_in_range",
    "csm_lookback_days",
    "csm_top_n",
    "csm_rebalance_frequency",
    "csm_trend_sma_days",
    "csm_use_market_regime",
    "csm_market_regime_sma_days",
    "csm_min_price",
    "csm_max_atr_pct",
    "dm_lookback_days",
    "dm_top_n",
    "dm_rebalance_frequency",
    "dm_absolute_momentum_threshold",
    "dm_trend_sma_days",
    "dm_use_market_regime",
    "dm_market_regime_sma_days",
    "dm_min_price",
    "dm_max_atr_pct",
    "donchian_entry_lookback_days",
    "donchian_exit_lookback_days",
    "donchian_top_n",
    "donchian_rebalance_frequency",
    "donchian_trend_sma_days",
    "donchian_use_market_regime",
    "donchian_market_regime_sma_days",
    "donchian_min_price",
    "donchian_max_atr_pct",
    "rs_lookback_days",
    "rs_top_n",
    "rs_rebalance_frequency",
    "rs_score_mode",
    "rs_vol_lookback_days",
    "rs_trend_sma_days",
    "rs_use_market_regime",
    "rs_market_regime_sma_days",
    "rs_min_price",
    "rs_max_atr_pct",
    "allowed_symbols_count",
    "allowed_symbols",
    "excluded_symbols_count",
    "excluded_symbols",
]

def _default_strategy_experiments():
    return [
        {
            "name": "base",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
        },
        {
            "name": "rsi15",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 15,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
        },
        {
            "name": "bb25",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_std": 2.5,
            "trend_sma_length": 50,
        },
        {
            "name": "trend100",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_std": 2.0,
            "trend_sma_length": 100,
        },
        {
            "name": "rsi15_trend100",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 15,
            "bollinger_std": 2.0,
            "trend_sma_length": 100,
        },
        {
            "name": "rsi15_bb25",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 15,
            "bollinger_std": 2.5,
            "trend_sma_length": 50,
        },
    ]


def _safe_output_name(value):
    return "".join(
        char if char.isalnum() or char in {"_", "-"} else "_"
        for char in str(value)
    ).strip("_") or "experiment"


def _experiment_metrics_row(experiment_name, metrics, params):
    row = {"Experiment Name": experiment_name}
    for column in EXPERIMENT_PARAM_COLUMNS:
        row[column] = params.get(column, np.nan)
    if isinstance(metrics, pd.DataFrame):
        if metrics.empty:
            metrics = {}
        else:
            metrics = metrics.iloc[-1].to_dict()
    if not isinstance(metrics, dict):
        metrics = {}
    for column in EXPERIMENT_METRIC_COLUMNS:
        row[column] = metrics.get(column, np.nan)
    return row


ENTRY_SIGNATURE_KEYS = [
    "strategy_name",
    "data_folder",
    "allowed_symbols",
    "excluded_symbols",
    "universe_symbols_file",
    "rsi_length",
    "rsi_entry_threshold",
    "bollinger_length",
    "bollinger_std",
    "bb_entry_tolerance_atr",
    "use_quality_filter",
    "min_price_filter",
    "min_atr_pct_filter",
    "max_atr_pct_filter",
    "min_avg_dollar_volume",
    "avg_volume_lookback",
    "trend_sma_length",
    "setup_lookback",
    "confirmation_mode",
    "confirmation_lookback",
    "confirmation_min_rebound_atr",
    "use_entry_context_filter",
    "max_entry_gap_pct",
    "max_entry_bb_distance_atr",
    "max_entry_signal_range_pct",
    "min_entry_close_position_in_range",
    "max_entry_close_position_in_range",
    "rs_lookback_days",
    "rs_top_n",
    "rs_rebalance_frequency",
    "rs_score_mode",
    "rs_vol_lookback_days",
    "rs_trend_sma_days",
    "rs_use_market_regime",
    "rs_market_regime_sma_days",
    "rs_min_price",
    "rs_max_atr_pct",
    "donchian_entry_lookback_days",
    "donchian_exit_lookback_days",
    "donchian_top_n",
    "donchian_rebalance_frequency",
    "donchian_trend_sma_days",
    "donchian_use_market_regime",
    "donchian_market_regime_sma_days",
    "donchian_min_price",
    "donchian_max_atr_pct",
    "use_market_calendar",
    "include_partial_session_bars",
    "execution_mode",
    "run_profile",
]

DATA_SIGNATURE_EXCLUDED_KEYS = {
    "hurst_filter_mode",
    "hurst_threshold",
}

EXIT_SIGNATURE_KEYS = [
    "exit_mode",
    "exit_sma_length",
    "take_profit_atr_multiplier",
    "disaster_stop_atr_multiplier",
    "time_stop_bars",
]


def _signature_value(value):
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    return value


def _experiment_effective_params(
    experiment,
    data_folder,
    allowed_symbols,
    excluded_symbols,
    universe_symbols_file,
    run_profile,
    quick_mode,
    commission_rate=0.001,
    slippage_mu=0.05,
    risk_off_exit_mode="hold_existing",
    max_workers=None,
    worker_utilization_target=0.85,
    parallel_chunk_size=None,
    use_prepared_feature_disk_cache=True,
    prepared_feature_cache_dir=".cache/prepared_features",
    force_rebuild_prepared_cache=False,
):
    run_profile = str(run_profile).lower()
    params = {
        "strategy_name": DEFAULT_STRATEGY_NAME,
        "hurst_filter_mode": "off",
        "hurst_threshold": 0.5,
        "ranking_mode": "momentum",
        "run_profile": run_profile,
        "quick_mode": quick_mode,
        "rsi_length": 3,
        "rsi_entry_threshold": 10,
        "bollinger_length": 20,
        "bollinger_std": 2.0,
        "bb_entry_tolerance_atr": 0.0,
        "use_quality_filter": True,
        "min_price_filter": 10.0,
        "min_atr_pct_filter": 0.2,
        "max_atr_pct_filter": 12.0,
        "min_avg_dollar_volume": 20_000_000,
        "avg_volume_lookback": 20,
        "trend_sma_length": 50,
        "setup_lookback": 3,
        "confirmation_mode": "off",
        "confirmation_lookback": 1,
        "confirmation_min_rebound_atr": 0.0,
        "use_entry_context_filter": False,
        "max_entry_gap_pct": None,
        "max_entry_bb_distance_atr": None,
        "max_entry_signal_range_pct": None,
        "min_entry_close_position_in_range": None,
        "max_entry_close_position_in_range": None,
        "dm_lookback_days": 126,
        "dm_top_n": 20,
        "dm_rebalance_frequency": "monthly",
        "dm_absolute_momentum_threshold": 0.0,
        "dm_trend_sma_days": 200,
        "dm_use_market_regime": True,
        "dm_market_regime_sma_days": 200,
        "dm_min_price": 10,
        "dm_max_atr_pct": 12,
        "donchian_entry_lookback_days": 20,
        "donchian_exit_lookback_days": 10,
        "donchian_top_n": 20,
        "donchian_rebalance_frequency": "daily",
        "donchian_trend_sma_days": 200,
        "donchian_use_market_regime": True,
        "donchian_market_regime_sma_days": 200,
        "donchian_min_price": 10,
        "donchian_max_atr_pct": 12,
        "csm_lookback_days": 126,
        "csm_top_n": 20,
        "csm_rebalance_frequency": "monthly",
        "csm_trend_sma_days": 200,
        "csm_use_market_regime": True,
        "csm_market_regime_sma_days": 200,
        "csm_min_price": 10,
        "csm_max_atr_pct": 12,
        "rs_lookback_days": 126,
        "rs_top_n": 20,
        "rs_rebalance_frequency": "weekly",
        "rs_score_mode": "return_over_vol",
        "rs_vol_lookback_days": 60,
        "rs_trend_sma_days": 200,
        "rs_use_market_regime": True,
        "rs_market_regime_sma_days": 200,
        "rs_min_price": 10,
        "rs_max_atr_pct": 12,
        "exit_mode": "sma_or_target",
        "exit_sma_length": 20,
        "take_profit_atr_multiplier": 2.0,
        "disaster_stop_atr_multiplier": 2.0,
        "time_stop_bars": 10,
        "commission_rate": commission_rate,
        "slippage_mu": slippage_mu,
        "risk_off_exit_mode": risk_off_exit_mode,
        "fast_backtest": True,
        "execution_mode": "bar_close",
        "evaluation_mode": "single",
        "train_start": None,
        "train_end": None,
        "test_start": None,
        "test_end": None,
        "walk_train_months": 18,
        "walk_test_months": 6,
        "data_folder": data_folder,
        "allowed_symbols": tuple(allowed_symbols) if allowed_symbols else tuple(),
        "excluded_symbols": (
            tuple(_normalize_symbol(symbol) for symbol in excluded_symbols)
            if excluded_symbols
            else tuple()
        ),
        "universe_symbols_file": universe_symbols_file,
        "benchmark_file": None,
        "benchmark_symbol": "QQQ",
        "cache_mode": None,
        "max_workers": max_workers,
        "worker_utilization_target": worker_utilization_target,
        "parallel_chunk_size": parallel_chunk_size,
        "use_prepared_feature_disk_cache": bool(use_prepared_feature_disk_cache),
        "prepared_feature_cache_dir": prepared_feature_cache_dir,
        "force_rebuild_prepared_cache": bool(force_rebuild_prepared_cache),
    }
    if run_profile == "fast":
        params.update(
            {
                "use_market_calendar": False,
                "include_partial_session_bars": False,
            }
        )
    else:
        params.update(
            {
                "use_market_calendar": True,
                "include_partial_session_bars": False,
            }
        )
    params.update(experiment)
    params.pop("name", None)
    params["strategy_name"] = validate_strategy_name(params.get("strategy_name"))
    params["Strategy Name"] = params["strategy_name"]
    params["Strategy Family"] = get_strategy_family(params["strategy_name"])
    rs_params = _normalize_relative_strength_params(params)
    params.update(rs_params)
    if params["strategy_name"] == "dual_momentum":
        dm_params = _normalize_dual_momentum_params(params)
        params.update(dm_params)
    if params["strategy_name"] == "donchian_breakout":
        donchian_params = _normalize_donchian_breakout_params(params)
        params.update(donchian_params)
    if params["strategy_name"] == "cross_sectional_momentum":
        csm_params = _normalize_cross_sectional_momentum_params(params)
        params.update(csm_params)
    params["bb_entry_tolerance_atr"] = float(params["bb_entry_tolerance_atr"])
    if params["bb_entry_tolerance_atr"] < 0:
        raise ValueError("bb_entry_tolerance_atr negatif olamaz.")
    params["use_quality_filter"] = bool(params["use_quality_filter"])
    params["min_price_filter"] = float(params["min_price_filter"])
    params["min_atr_pct_filter"] = float(params["min_atr_pct_filter"])
    params["max_atr_pct_filter"] = float(params["max_atr_pct_filter"])
    params["min_avg_dollar_volume"] = float(params["min_avg_dollar_volume"])
    params["avg_volume_lookback"] = int(params["avg_volume_lookback"])
    if params["min_price_filter"] < 0:
        raise ValueError("min_price_filter negatif olamaz.")
    if params["min_atr_pct_filter"] < 0:
        raise ValueError("min_atr_pct_filter negatif olamaz.")
    if params["max_atr_pct_filter"] <= params["min_atr_pct_filter"]:
        raise ValueError("max_atr_pct_filter min_atr_pct_filter'dan buyuk olmali.")
    if params["min_avg_dollar_volume"] < 0:
        raise ValueError("min_avg_dollar_volume negatif olamaz.")
    if params["avg_volume_lookback"] < 1:
        raise ValueError("avg_volume_lookback en az 1 olmali.")
    params["confirmation_mode"] = _normalize_confirmation_mode(
        params["confirmation_mode"]
    )
    params["confirmation_lookback"] = int(params["confirmation_lookback"])
    params["confirmation_min_rebound_atr"] = float(
        params["confirmation_min_rebound_atr"]
    )
    if params["confirmation_lookback"] < 1:
        raise ValueError("confirmation_lookback en az 1 olmali.")
    if params["confirmation_min_rebound_atr"] < 0:
        raise ValueError("confirmation_min_rebound_atr negatif olamaz.")
    context_filter_params = _normalize_entry_context_filter_params(
        use_entry_context_filter=params["use_entry_context_filter"],
        max_entry_gap_pct=params["max_entry_gap_pct"],
        max_entry_bb_distance_atr=params["max_entry_bb_distance_atr"],
        max_entry_signal_range_pct=params["max_entry_signal_range_pct"],
        min_entry_close_position_in_range=params[
            "min_entry_close_position_in_range"
        ],
        max_entry_close_position_in_range=params[
            "max_entry_close_position_in_range"
        ],
    )
    params.update(context_filter_params)
    params["execution_mode"] = _normalize_execution_mode(params["execution_mode"])
    params["evaluation_mode"] = _normalize_evaluation_mode(params["evaluation_mode"])
    params["exit_mode"] = _normalize_exit_mode(params["exit_mode"])
    params["commission_rate"] = float(params["commission_rate"])
    params["slippage_mu"] = float(params["slippage_mu"])
    if params["commission_rate"] < 0:
        raise ValueError("commission_rate negatif olamaz.")
    if params["slippage_mu"] < 0:
        raise ValueError("slippage_mu negatif olamaz.")
    params["risk_off_exit_mode"] = _normalize_risk_off_exit_mode(
        params["risk_off_exit_mode"]
    )
    return params


def _experiment_signature(params, keys):
    return tuple(
        (key, _signature_value(params.get(key)))
        for key in keys
        if key not in DATA_SIGNATURE_EXCLUDED_KEYS
    )


ROTATION_RUN_LEVEL_REUSE_STRATEGIES = {
    "relative_strength_rotation",
    "cross_sectional_momentum",
    "dual_momentum",
    "donchian_breakout",
}

ROTATION_BASE_FEATURE_SET = (
    "raw_ohlcv",
    "cleaned_ohlcv",
    "daily_resample",
    "atr20",
    "atr_pct",
    "sma50",
    "sma100",
    "sma200",
    "momentum_close_series",
    "rolling_volatility",
    "benchmark_regime",
)

PREPARED_FEATURE_CACHE_VERSION = "prepared_feature_cache_v1"


def _data_folder_mtime_fingerprint(data_folder, allowed_symbols=None, excluded_symbols=None):
    allowed = (
        {_normalize_symbol(symbol) for symbol in allowed_symbols}
        if allowed_symbols
        else None
    )
    excluded = (
        {_normalize_symbol(symbol) for symbol in excluded_symbols}
        if excluded_symbols
        else set()
    )
    fingerprint = []
    for file_path in sorted(Path(data_folder).glob("*.csv")):
        symbol = _normalize_symbol(file_path.stem)
        if allowed is not None and symbol not in allowed:
            continue
        if symbol in excluded:
            continue
        try:
            stat = file_path.stat()
        except OSError:
            continue
        fingerprint.append((symbol, str(file_path.resolve()), stat.st_mtime_ns, stat.st_size))
    return tuple(fingerprint)


def _rotation_run_level_cache_key(params):
    return (
        ("data_folder", str(Path(params.get("data_folder", "")).resolve())),
        ("allowed_symbols", _signature_value(params.get("allowed_symbols", tuple()))),
        ("excluded_symbols", _signature_value(params.get("excluded_symbols", tuple()))),
        ("universe_symbols_file", _signature_value(params.get("universe_symbols_file"))),
        ("benchmark_file", _signature_value(params.get("benchmark_file"))),
        ("benchmark_symbol", params.get("benchmark_symbol", "QQQ")),
        ("run_profile", params.get("run_profile")),
        ("execution_mode", params.get("execution_mode")),
        ("use_market_calendar", bool(params.get("use_market_calendar", False))),
        (
            "include_partial_session_bars",
            bool(params.get("include_partial_session_bars", False)),
        ),
        ("cache_mode", params.get("cache_mode")),
        ("feature_set", ROTATION_BASE_FEATURE_SET),
        (
            "data_fingerprint",
            _data_folder_mtime_fingerprint(
                params.get("data_folder", ""),
                params.get("allowed_symbols", tuple()),
                params.get("excluded_symbols", tuple()),
            ),
        ),
    )


def _rotation_prepare_config_key(params):
    return (
        "rotation-prepared-base",
        _rotation_run_level_cache_key(params),
    )


def _prepared_feature_cache_dir_path(data_folder, cache_dir):
    cache_path = Path(cache_dir)
    if cache_path.is_absolute():
        return cache_path
    return Path(data_folder) / cache_path


def _prepared_feature_disk_cache_path(params):
    cache_key = (
        PREPARED_FEATURE_CACHE_VERSION,
        _rotation_run_level_cache_key(params),
        ("adjusted", bool(params.get("adjusted", False))),
        ("format", "pickle"),
    )
    digest = hashlib.sha256(repr(cache_key).encode("utf-8")).hexdigest()[:24]
    cache_dir = _prepared_feature_cache_dir_path(
        params.get("data_folder", ""),
        params.get("prepared_feature_cache_dir", ".cache/prepared_features"),
    )
    return cache_dir / f"prepared_features_{digest}.pkl", cache_key


def _empty_prepared_disk_cache_stats():
    return {
        "prepared_disk_cache_hits": 0,
        "prepared_disk_cache_misses": 0,
        "prepared_disk_cache_write_sec": 0.0,
        "prepared_disk_cache_read_sec": 0.0,
        "prepared_disk_cache_size_mb": 0.0,
    }


def _read_prepared_feature_disk_cache(params):
    stats = _empty_prepared_disk_cache_stats()
    if not params.get("use_prepared_feature_disk_cache", True):
        return None, stats
    if params.get("force_rebuild_prepared_cache", False):
        stats["prepared_disk_cache_misses"] = 1
        return None, stats
    cache_path, cache_key = _prepared_feature_disk_cache_path(params)
    if not cache_path.exists():
        stats["prepared_disk_cache_misses"] = 1
        return None, stats
    read_start = time.perf_counter()
    try:
        payload = pd.read_pickle(cache_path)
    except Exception as error:
        stats["prepared_disk_cache_misses"] = 1
        stats["prepared_disk_cache_read_sec"] = time.perf_counter() - read_start
        print(f"Prepared feature disk cache okunamadi; rebuild edilecek: {error}")
        return None, stats
    stats["prepared_disk_cache_read_sec"] = time.perf_counter() - read_start
    try:
        if payload.get("cache_key") != cache_key:
            raise ValueError("cache key mismatch")
        if payload.get("version") != PREPARED_FEATURE_CACHE_VERSION:
            raise ValueError("cache version mismatch")
        prepared = payload.get("prepared")
        if not isinstance(prepared, dict) or not prepared.get("asset_data"):
            raise ValueError("prepared payload missing asset_data")
        _validate_asset_signal_columns(prepared.get("asset_data", {}))
    except Exception as error:
        stats["prepared_disk_cache_misses"] = 1
        print(f"Prepared feature disk cache uyumsuz; rebuild edilecek: {error}")
        return None, stats
    stats["prepared_disk_cache_hits"] = 1
    try:
        stats["prepared_disk_cache_size_mb"] = cache_path.stat().st_size / (1024 * 1024)
    except OSError:
        stats["prepared_disk_cache_size_mb"] = 0.0
    return prepared, stats


def _write_prepared_feature_disk_cache(params, prepared):
    stats = _empty_prepared_disk_cache_stats()
    if not params.get("use_prepared_feature_disk_cache", True):
        return stats
    cache_path, cache_key = _prepared_feature_disk_cache_path(params)
    write_start = time.perf_counter()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": PREPARED_FEATURE_CACHE_VERSION,
            "cache_format": "pickle",
            "cache_key": cache_key,
            "prepared": prepared,
        }
        pd.to_pickle(payload, cache_path)
    except Exception as error:
        stats["prepared_disk_cache_write_sec"] = time.perf_counter() - write_start
        print(f"Prepared feature disk cache yazilamadi; devam ediliyor: {error}")
        return stats
    stats["prepared_disk_cache_write_sec"] = time.perf_counter() - write_start
    try:
        stats["prepared_disk_cache_size_mb"] = cache_path.stat().st_size / (1024 * 1024)
    except OSError:
        stats["prepared_disk_cache_size_mb"] = 0.0
    return stats


def _derive_rotation_prepared_bundle(base_prepared, params):
    derive_start = time.perf_counter()
    asset_data = {}
    for symbol, frame in base_prepared.get("asset_data", {}).items():
        asset_data[symbol] = generate_strategy_signals(frame, params)
    _validate_asset_signal_columns(asset_data)

    base_cache_summary = dict(base_prepared.get("cache_summary", {}) or {})
    derived_seconds = time.perf_counter() - derive_start
    symbols_count = len(asset_data)
    base_cache_summary.update(
        {
            "run_level_prepared_cache_hits": int(
                base_cache_summary.get("run_level_prepared_cache_hits", 0) or 0
            )
            + 1,
            "run_level_prepared_cache_misses": int(
                base_cache_summary.get("run_level_prepared_cache_misses", 0) or 0
            ),
            "feature_cache_hits": int(
                base_cache_summary.get("feature_cache_hits", 0) or 0
            )
            + symbols_count,
            "feature_cache_misses": int(
                base_cache_summary.get("feature_cache_misses", 0) or 0
            ),
            "benchmark_cache_reused": bool(base_prepared.get("benchmark_data") is not None),
            "cache_load_seconds": 0.0,
            "cache_build_seconds": derived_seconds,
            "cache_write_seconds": 0.0,
            "per_symbol_130m_hits": symbols_count,
            "per_symbol_130m_misses": 0,
            "per_symbol_1m_hits": 0,
            "per_symbol_1m_misses": 0,
        }
    )
    return {
        "asset_data": asset_data,
        "intraday_data": base_prepared.get("intraday_data", {}),
        "benchmark_data": base_prepared.get("benchmark_data"),
        "load_results": _build_load_results(asset_data),
        "symbols": list(base_prepared.get("symbols", list(asset_data.keys()))),
        "signal_diagnostics": _asset_signal_diagnostics(asset_data),
        "timing": {
            "cache_summary": base_cache_summary,
            "asset_preparation_seconds": derived_seconds,
        },
        "calendar_summary": _calendar_report_summary(asset_data),
        "cache_summary": base_cache_summary,
        "asset_preparation_seconds": derived_seconds,
    }


def _experiment_report_params(params, allowed_symbols):
    report_params = dict(params)
    report_params["allowed_symbols_count"] = (
        len(allowed_symbols) if allowed_symbols else np.nan
    )
    report_params["allowed_symbols"] = (
        ",".join(allowed_symbols) if allowed_symbols else ""
    )
    excluded_symbols = params.get("excluded_symbols", tuple())
    excluded_symbol_list = [
        _normalize_symbol(symbol)
        for symbol in excluded_symbols
        if str(symbol).strip()
    ]
    report_params["excluded_symbols_count"] = (
        len(excluded_symbol_list) if excluded_symbol_list else np.nan
    )
    report_params["excluded_symbols"] = ",".join(excluded_symbol_list)
    return report_params


def _metrics_as_dict(metrics):
    if isinstance(metrics, pd.DataFrame):
        if metrics.empty:
            return {}
        return metrics.iloc[-1].to_dict()
    if isinstance(metrics, dict):
        return metrics
    return {}


def _sum_timing_stage_seconds(rows, keywords):
    total = 0.0
    keywords = tuple(str(keyword).lower() for keyword in keywords)
    for row in rows or []:
        stage = str(row.get("Stage", "")).lower()
        if any(keyword in stage for keyword in keywords):
            total += float(row.get("Seconds", 0.0) or 0.0)
    return total


def _performance_profile_row(
    experiment_name,
    params,
    metrics,
    cache_summary,
    experiment_runtime,
    preparation_runtime,
    portfolio_runtime,
    report_generation_runtime,
    symbols_processed,
    worker_count,
    parallel_backend,
    sequential_fallback,
    prepared_data_reused,
    experiment_count,
):
    cache_summary = cache_summary or {}
    worker_timing_rows = cache_summary.get("worker_timing_rows", [])
    cache_hit_count = int(cache_summary.get("per_symbol_130m_hits", 0) or 0)
    cache_miss_count = int(cache_summary.get("per_symbol_130m_misses", 0) or 0)
    cache_hit_count += int(cache_summary.get("per_symbol_1m_hits", 0) or 0)
    cache_miss_count += int(cache_summary.get("per_symbol_1m_misses", 0) or 0)
    worker_count = int(worker_count or 1)
    symbols_processed = int(symbols_processed or 0)
    return {
        "Experiment Name": experiment_name,
        "Strategy Name": params.get("strategy_name"),
        "Runtime Sec": round(float(experiment_runtime), 6),
        "Symbols Processed": symbols_processed,
        "Trades": int(metrics.get("Trade Count", metrics.get("Islem Sayisi", 0)) or 0),
        "Cache Hit Count": cache_hit_count,
        "Cache Miss Count": cache_miss_count,
        "total_runtime_sec": np.nan,
        "data_loading_sec": round(float(preparation_runtime), 6),
        "cache_read_sec": round(float(cache_summary.get("cache_load_seconds", 0.0) or 0.0), 6),
        "cache_write_sec": round(float(cache_summary.get("cache_write_seconds", 0.0) or 0.0), 6),
        "indicator_calculation_sec": round(
            _sum_timing_stage_seconds(worker_timing_rows, ["indicator calculation"]),
            6,
        ),
        "daily_resample_sec": round(
            _sum_timing_stage_seconds(worker_timing_rows, ["daily resample"]),
            6,
        ),
        "strategy_signal_generation_sec": round(
            _sum_timing_stage_seconds(
                worker_timing_rows,
                ["strategy signal generation", "indicator calculation"],
            ),
            6,
        ),
        "portfolio_backtest_sec": round(float(portfolio_runtime), 6),
        "report_generation_sec": round(float(report_generation_runtime), 6),
        "per_experiment_runtime_sec": round(float(experiment_runtime), 6),
        "per_symbol_preparation_sec": round(
            float(preparation_runtime) / max(symbols_processed, 1),
            6,
        ),
        "per_symbol_backtest_sec": round(
            float(portfolio_runtime) / max(symbols_processed, 1),
            6,
        ),
        "os_cpu_count": os.cpu_count() or 1,
        "worker_count": worker_count,
        "parallel_backend": parallel_backend,
        "chunk_size": cache_summary.get("chunk_size", 1),
        "batch_size": cache_summary.get("batch_size", 1),
        "avg_symbols_per_worker": round(symbols_processed / max(worker_count, 1), 4),
        "sequential_fallback": bool(sequential_fallback),
        "prepared_data_reused": bool(prepared_data_reused),
        "worker_utilization_target": params.get("worker_utilization_target", np.nan),
        "run_level_prepared_cache_hits": int(
            cache_summary.get("run_level_prepared_cache_hits", 0) or 0
        ),
        "run_level_prepared_cache_misses": int(
            cache_summary.get("run_level_prepared_cache_misses", 0) or 0
        ),
        "feature_cache_hits": int(cache_summary.get("feature_cache_hits", 0) or 0),
        "feature_cache_misses": int(cache_summary.get("feature_cache_misses", 0) or 0),
        "benchmark_cache_reused": bool(cache_summary.get("benchmark_cache_reused", False)),
        "avg_worker_runtime_sec": round(
            float(cache_summary.get("avg_worker_seconds", 0.0) or 0.0),
            6,
        ),
        "max_worker_runtime_sec": round(
            float(cache_summary.get("max_worker_seconds", 0.0) or 0.0),
            6,
        ),
        "parallel_overhead_estimate_sec": round(
            float(cache_summary.get("parallel_overhead_estimate_sec", 0.0) or 0.0),
            6,
        ),
        "prepared_disk_cache_hits": int(
            cache_summary.get("prepared_disk_cache_hits", 0) or 0
        ),
        "prepared_disk_cache_misses": int(
            cache_summary.get("prepared_disk_cache_misses", 0) or 0
        ),
        "prepared_disk_cache_write_sec": round(
            float(cache_summary.get("prepared_disk_cache_write_sec", 0.0) or 0.0),
            6,
        ),
        "prepared_disk_cache_read_sec": round(
            float(cache_summary.get("prepared_disk_cache_read_sec", 0.0) or 0.0),
            6,
        ),
        "prepared_disk_cache_size_mb": round(
            float(cache_summary.get("prepared_disk_cache_size_mb", 0.0) or 0.0),
            6,
        ),
        "experiment_count": int(experiment_count),
    }


def _print_performance_summary(profile_rows):
    if not profile_rows:
        return
    total_row = profile_rows[-1] if profile_rows[-1].get("Experiment Name") == "TOTAL" else {}
    total_runtime = float(total_row.get("total_runtime_sec", 0.0) or 0.0)
    print(f"\nTotal runtime: {total_runtime:.2f}s")
    stage_columns = [
        "data_loading_sec",
        "cache_read_sec",
        "cache_write_sec",
        "indicator_calculation_sec",
        "daily_resample_sec",
        "strategy_signal_generation_sec",
        "portfolio_backtest_sec",
        "report_generation_sec",
    ]
    slowest = sorted(
        (
            (column, float(total_row.get(column, 0.0) or 0.0))
            for column in stage_columns
        ),
        key=lambda item: item[1],
        reverse=True,
    )[:5]
    print("En yavas 5 asama:")
    for name, seconds in slowest:
        print(f"  {name}: {seconds:.2f}s")
    print(
        "Cache hit/miss: "
        f"{int(total_row.get('Cache Hit Count', 0) or 0)}/"
        f"{int(total_row.get('Cache Miss Count', 0) or 0)}"
    )
    print(f"Worker count: {int(total_row.get('worker_count', 0) or 0)}")
    avg_runtime = float(total_row.get("per_experiment_runtime_sec", 0.0) or 0.0)
    print(f"Ortalama per-experiment runtime: {avg_runtime:.2f}s")


def _metric_values_match(left, right, tolerance=1e-6):
    left_missing = pd.isna(left)
    right_missing = pd.isna(right)
    if left_missing or right_missing:
        return bool(left_missing and right_missing)
    try:
        left_float = float(left)
        right_float = float(right)
    except (TypeError, ValueError):
        return left == right
    if math.isinf(left_float) or math.isinf(right_float):
        return left_float == right_float
    return abs(left_float - right_float) <= tolerance


def validate_fast_backtest_equivalence(
    data_folder,
    allowed_symbols,
    experiment,
    run_profile="fast",
    quick_mode=True,
):
    if not isinstance(experiment, dict):
        raise ValueError("experiment dict olmali.")

    def _run_validation_case(fast_backtest):
        scanner_kwargs = dict(experiment)
        for key in [
            "name",
            "data_folder",
            "allowed_symbols",
            "run_profile",
            "quick_mode",
            "profile_timing",
            "output_subdir",
            "clear_stale_trade_log",
            "write_trade_log",
        ]:
            scanner_kwargs.pop(key, None)
        scanner_kwargs["fast_backtest"] = bool(fast_backtest)
        return _metrics_as_dict(
            run_universe_scanner(
                data_folder=data_folder,
                allowed_symbols=allowed_symbols,
                run_profile=run_profile,
                quick_mode=quick_mode,
                profile_timing=False,
                output_subdir=(
                    "validation_fast_backtest_fast"
                    if fast_backtest
                    else "validation_fast_backtest_slow"
                ),
                clear_stale_trade_log=True,
                write_trade_log=False,
                **scanner_kwargs,
            )
        )

    slow_metrics = _run_validation_case(False)
    fast_metrics = _run_validation_case(True)
    if not slow_metrics or not fast_metrics:
        print("FAIL: validation metrics could not be produced")
        return False

    metric_checks = [
        ("net_getiri", "Net Getiri (%)", "float"),
        ("max_drawdown", "Max Drawdown (%)", "float"),
        ("toplam_islem", "Islem Sayisi", "int"),
        ("profit_factor", "Profit Factor", "float"),
        ("expectancy", "Expectancy R", "float"),
        ("avg_r", "Avg R", "float"),
        ("worst_r", "Worst R", "float"),
        ("risk_off_exit_count", "Risk Off Exit Count", "int"),
    ]
    failures = []
    for label, key, value_type in metric_checks:
        slow_value = slow_metrics.get(key, np.nan)
        fast_value = fast_metrics.get(key, np.nan)
        if value_type == "int":
            values_match = slow_value == fast_value
        else:
            values_match = _metric_values_match(
                slow_value,
                fast_value,
                tolerance=1e-6,
            )
        if not values_match:
            failures.append(
                f"{label}: slow={slow_value!r}, fast={fast_value!r}"
            )

    if failures:
        print("FAIL: fast_backtest differs from slow_backtest")
        for failure in failures:
            print(f"- {failure}")
        return False

    print("PASS: fast_backtest matches slow_backtest")
    return True


def _run_prepared_portfolio_experiment(
    prepared_bundle,
    params,
    timing=None,
):
    if params.get("strategy_name") == "relative_strength_rotation":
        if params.get("evaluation_mode", "single") != "single":
            raise ValueError(
                "relative_strength_rotation su anda evaluation_mode='single' "
                "ile destekleniyor."
            )
        rs_params = _normalize_relative_strength_params(params)
        asset_data = _apply_relative_strength_rotation_top_signals(
            prepared_bundle["asset_data"],
            rs_params,
            benchmark_data=prepared_bundle.get("benchmark_data"),
        )
        _validate_asset_signal_columns(asset_data)
        signal_diagnostics = _asset_signal_diagnostics(asset_data)
        calendar_summary = _calendar_report_summary(asset_data)
        if prepared_bundle.get("calendar_summary"):
            calendar_summary.update(prepared_bundle["calendar_summary"])
        metrics = _run_relative_strength_rotation_backtest(
            asset_data,
            rs_params,
            benchmark_data=prepared_bundle.get("benchmark_data"),
        )
        if not metrics:
            metrics = _zero_trade_metrics()
        metrics.update(calendar_summary)
        metrics.update(signal_diagnostics)
        for column in PORTFOLIO_REJECTION_COLUMNS:
            metrics.setdefault(column, 0)
        metrics["Makro Rejimde Reddedilen Sinyal"] = metrics.get(
            "Risk Off Rejected Signal Count",
            0,
        )
        metrics["total_trades"] = int(metrics.get("Islem Sayisi", 0) or 0)
        metrics["Trade Count"] = metrics["total_trades"]
        metrics.setdefault("_fast_backtest", False)
        metrics.setdefault("_portfolio_fast_arrays_build_seconds", 0.0)
        metrics.setdefault("_portfolio_loop_seconds", 0.0)
        metrics.update(
            _benchmark_summary_for_equity_curve(
                asset_data,
                metrics.get("_equity_curve", []),
                metrics.get("Net Getiri (%)", np.nan),
                benchmark_data=prepared_bundle.get("benchmark_data"),
                benchmark_symbol=params.get("benchmark_symbol", "QQQ"),
            )
        )
        return metrics

    if params.get("strategy_name") == "dual_momentum":
        if params.get("evaluation_mode", "single") != "single":
            raise ValueError(
                "dual_momentum su anda evaluation_mode='single' "
                "ile destekleniyor."
            )
        dm_params = _normalize_dual_momentum_params(params)
        asset_data = _apply_dual_momentum_top_signals(
            prepared_bundle["asset_data"],
            dm_params,
            benchmark_data=prepared_bundle.get("benchmark_data"),
        )
        _validate_asset_signal_columns(asset_data)
        signal_diagnostics = _asset_signal_diagnostics(asset_data)
        calendar_summary = _calendar_report_summary(asset_data)
        if prepared_bundle.get("calendar_summary"):
            calendar_summary.update(prepared_bundle["calendar_summary"])
        metrics = _run_dual_momentum_backtest(
            asset_data,
            dm_params,
            benchmark_data=prepared_bundle.get("benchmark_data"),
        )
        if not metrics:
            metrics = _zero_trade_metrics()
        metrics.update(calendar_summary)
        metrics.update(signal_diagnostics)
        for column in PORTFOLIO_REJECTION_COLUMNS:
            metrics.setdefault(column, 0)
        metrics["Makro Rejimde Reddedilen Sinyal"] = metrics.get(
            "Risk Off Rejected Signal Count",
            0,
        )
        metrics["total_trades"] = int(metrics.get("Islem Sayisi", 0) or 0)
        metrics["Trade Count"] = metrics["total_trades"]
        metrics.setdefault("_fast_backtest", False)
        metrics.setdefault("_portfolio_fast_arrays_build_seconds", 0.0)
        metrics.setdefault("_portfolio_loop_seconds", 0.0)
        metrics.update(
            _benchmark_summary_for_equity_curve(
                asset_data,
                metrics.get("_equity_curve", []),
                metrics.get("Net Getiri (%)", float("nan")),
                benchmark_data=prepared_bundle.get("benchmark_data"),
                benchmark_symbol=params.get("benchmark_symbol", "QQQ"),
            )
        )
        return metrics

    if params.get("strategy_name") == "donchian_breakout":
        if params.get("evaluation_mode", "single") != "single":
            raise ValueError(
                "donchian_breakout su anda evaluation_mode='single' "
                "ile destekleniyor."
            )
        donchian_params = _normalize_donchian_breakout_params(params)
        asset_data = _apply_donchian_breakout_top_signals(
            prepared_bundle["asset_data"],
            donchian_params,
            benchmark_data=prepared_bundle.get("benchmark_data"),
        )
        _validate_asset_signal_columns(asset_data)
        signal_diagnostics = _asset_signal_diagnostics(asset_data)
        calendar_summary = _calendar_report_summary(asset_data)
        if prepared_bundle.get("calendar_summary"):
            calendar_summary.update(prepared_bundle["calendar_summary"])
        metrics = _run_donchian_breakout_backtest(
            asset_data,
            donchian_params,
            benchmark_data=prepared_bundle.get("benchmark_data"),
        )
        if not metrics:
            metrics = _zero_trade_metrics()
        metrics.update(calendar_summary)
        metrics.update(signal_diagnostics)
        for column in PORTFOLIO_REJECTION_COLUMNS:
            metrics.setdefault(column, 0)
        metrics["Makro Rejimde Reddedilen Sinyal"] = metrics.get(
            "Risk Off Rejected Signal Count",
            0,
        )
        metrics["total_trades"] = int(metrics.get("Islem Sayisi", 0) or 0)
        metrics["Trade Count"] = metrics["total_trades"]
        metrics.setdefault("_fast_backtest", False)
        metrics.setdefault("_portfolio_fast_arrays_build_seconds", 0.0)
        metrics.setdefault("_portfolio_loop_seconds", 0.0)
        metrics.update(
            _benchmark_summary_for_equity_curve(
                asset_data,
                metrics.get("_equity_curve", []),
                metrics.get("Net Getiri (%)", np.nan),
                benchmark_data=prepared_bundle.get("benchmark_data"),
                benchmark_symbol=params.get("benchmark_symbol", "QQQ"),
            )
        )
        return metrics

    if params.get("strategy_name") == "cross_sectional_momentum":
        if params.get("evaluation_mode", "single") != "single":
            raise ValueError(
                "cross_sectional_momentum su anda evaluation_mode='single' "
                "ile destekleniyor."
            )
        csm_params = _normalize_cross_sectional_momentum_params(params)
        asset_data = _apply_cross_sectional_momentum_top_signals(
            prepared_bundle["asset_data"],
            csm_params,
            benchmark_data=prepared_bundle.get("benchmark_data"),
        )
        _validate_asset_signal_columns(asset_data)
        signal_diagnostics = _asset_signal_diagnostics(asset_data)
        calendar_summary = _calendar_report_summary(asset_data)
        if prepared_bundle.get("calendar_summary"):
            calendar_summary.update(prepared_bundle["calendar_summary"])
        metrics = _run_cross_sectional_momentum_backtest(
            asset_data,
            csm_params,
            benchmark_data=prepared_bundle.get("benchmark_data"),
        )
        if not metrics:
            metrics = _zero_trade_metrics()
        metrics.update(calendar_summary)
        metrics.update(signal_diagnostics)
        for column in PORTFOLIO_REJECTION_COLUMNS:
            metrics.setdefault(column, 0)
        metrics["Makro Rejimde Reddedilen Sinyal"] = metrics.get(
            "Risk Off Rejected Signal Count",
            0,
        )
        metrics["total_trades"] = int(metrics.get("Islem Sayisi", 0) or 0)
        metrics["Trade Count"] = metrics["total_trades"]
        metrics.setdefault("_fast_backtest", False)
        metrics.setdefault("_portfolio_fast_arrays_build_seconds", 0.0)
        metrics.setdefault("_portfolio_loop_seconds", 0.0)
        metrics.update(
            _benchmark_summary_for_equity_curve(
                asset_data,
                metrics.get("_equity_curve", []),
                metrics.get("Net Getiri (%)", np.nan),
                benchmark_data=prepared_bundle.get("benchmark_data"),
                benchmark_symbol=params.get("benchmark_symbol", "QQQ"),
            )
        )
        return metrics

    asset_data = apply_entry_filters_to_asset_data(
        prepared_bundle["asset_data"],
        hurst_filter_mode=params["hurst_filter_mode"],
        hurst_threshold=params["hurst_threshold"],
    )
    _validate_asset_signal_columns(asset_data)
    signal_diagnostics = _asset_signal_diagnostics(asset_data)
    asset_data = apply_exit_parameters_to_asset_data(
        asset_data,
        exit_sma_length=params["exit_sma_length"],
        take_profit_atr_multiplier=params["take_profit_atr_multiplier"],
        disaster_stop_atr_multiplier=params["disaster_stop_atr_multiplier"],
        exit_mode=params["exit_mode"],
    )
    intraday_data = prepared_bundle.get("intraday_data") or {}
    load_results = prepared_bundle.get("load_results") or []
    calendar_summary = _calendar_report_summary(asset_data)
    if prepared_bundle.get("calendar_summary"):
        calendar_summary.update(prepared_bundle["calendar_summary"])

    evaluation_mode = params["evaluation_mode"]
    ranking_mode = params["ranking_mode"]
    execution_mode = params["execution_mode"]
    exit_mode = params["exit_mode"]
    time_stop_bars = params["time_stop_bars"]
    fast_backtest = bool(params.get("fast_backtest", True))
    commission_rate = params.get("commission_rate", 0.001)
    slippage_mu = params.get("slippage_mu", 0.05)
    risk_off_exit_mode = params.get("risk_off_exit_mode", "hold_existing")

    if evaluation_mode == "split":
        rows = []
        for period_name, start, end in [
            ("Train", params.get("train_start"), params.get("train_end")),
            ("Test", params.get("test_start"), params.get("test_end")),
        ]:
            _, metrics = _run_portfolio_period(
                asset_data,
                ranking_mode,
                period_name,
                start=start,
                end=end,
                execution_mode=execution_mode,
                intraday_data=intraday_data,
                timing=timing,
                exit_mode=exit_mode,
                time_stop_bars=time_stop_bars,
                fast_backtest=fast_backtest,
                commission_rate=commission_rate,
                slippage_mu=slippage_mu,
                risk_off_exit_mode=risk_off_exit_mode,
            )
            if metrics:
                metrics["Makro Rejimde Reddedilen Sinyal"] = (
                    _risk_off_rejected_total(load_results)
                )
                metrics.update(calendar_summary)
                rows.append(metrics)
        return pd.DataFrame(rows) if rows else {}

    if evaluation_mode == "walk_forward":
        rows = []
        folds = _build_walk_forward_folds(
            asset_data,
            params["walk_train_months"],
            params["walk_test_months"],
        )
        for fold in folds:
            _, metrics = _run_portfolio_period(
                asset_data,
                ranking_mode,
                f"Fold {fold['Fold No']} Test",
                start=fold["Test Start"],
                end=fold["Test End"],
                execution_mode=execution_mode,
                intraday_data=intraday_data,
                timing=timing,
                exit_mode=exit_mode,
                time_stop_bars=time_stop_bars,
                fast_backtest=fast_backtest,
                commission_rate=commission_rate,
                slippage_mu=slippage_mu,
                risk_off_exit_mode=risk_off_exit_mode,
            )
            if metrics:
                rows.append(
                    {
                        "Fold No": fold["Fold No"],
                        "Train Start": fold["Train Start"],
                        "Train End": fold["Train End"],
                        "Test Start": fold["Test Start"],
                        "Test End": fold["Test End"],
                        "Test Net Return": metrics.get("Net Getiri (%)", np.nan),
                        "Test CAGR": metrics.get("CAGR (%)", np.nan),
                        "Test MaxDD": metrics.get("Max Drawdown (%)", np.nan),
                        "Test Sharpe": metrics.get("Sharpe Ratio", np.nan),
                        "Test Trade Count": metrics.get("Islem Sayisi", np.nan),
                        "Test Profit Factor": metrics.get("Profit Factor", np.nan),
                        "Test Expectancy R": metrics.get("Expectancy R", np.nan),
                        **calendar_summary,
                    }
                )
        return pd.DataFrame(rows) if rows else {}

    manager = PortfolioManager(
        asset_data=asset_data,
        initial_cash=10000.0,
        max_open_positions=5,
        risk_per_trade=0.02,
        ranking_mode=ranking_mode,
        execution_mode=execution_mode,
        intraday_data=intraday_data,
        verbose=False,
        exit_mode=exit_mode,
        time_stop_bars=time_stop_bars,
        fast_backtest=fast_backtest,
        commission_rate=commission_rate,
        slippage_mu=slippage_mu,
        risk_off_exit_mode=risk_off_exit_mode,
    )
    metrics = manager.run_backtest()
    if not metrics:
        metrics = _zero_trade_metrics()
    metrics["_fast_backtest"] = manager.fast_backtest
    metrics["_portfolio_fast_arrays_build_seconds"] = (
        manager.portfolio_fast_arrays_build_seconds
    )
    metrics["_portfolio_loop_seconds"] = manager.portfolio_loop_seconds
    metrics["Makro Rejimde Reddedilen Sinyal"] = _risk_off_rejected_total(load_results)
    metrics.update(calendar_summary)
    metrics.update(signal_diagnostics)
    for column in PORTFOLIO_REJECTION_COLUMNS:
        metrics.setdefault(column, 0)
    metrics["total_trades"] = int(metrics.get("Islem Sayisi", 0) or 0)
    metrics["Trade Count"] = metrics["total_trades"]
    metrics["_trade_log"] = list(manager.trade_log)
    if params.get("quick_mode", True):
        metrics.update(_prefixed_benchmark_metrics("QQQ", _empty_buyhold_metrics()))
        metrics.update(_prefixed_benchmark_metrics("SPY", _empty_buyhold_metrics()))
        metrics.update(
            {
                "EqualWeight Universe Return (%)": np.nan,
                "EqualWeight Universe CAGR (%)": np.nan,
                "EqualWeight Universe MaxDD (%)": np.nan,
                "Strategy Excess Return vs QQQ (%)": np.nan,
                "Strategy Excess Return vs EqualWeight (%)": np.nan,
                "Benchmark Comparison": "skipped_quick_mode",
            }
        )
    return metrics



try:
    from .reports import (
        COMPACT_SCORE_COLUMNS,
        SURVIVORSHIP_BIAS_WARNING,
        DIVIDEND_ADJUSTMENT_WARNING,
        calculate_buyhold_metrics,
        calculate_equal_weight_benchmark,
        _prefixed_benchmark_metrics,
        _add_compact_scores,
        _write_compact_markdown,
    )
    from .diagnostics import (
        _symbol_attribution_rows,
        _trade_context_rows,
    )
except ImportError:
    from reports import (
        COMPACT_SCORE_COLUMNS,
        SURVIVORSHIP_BIAS_WARNING,
        DIVIDEND_ADJUSTMENT_WARNING,
        calculate_buyhold_metrics,
        calculate_equal_weight_benchmark,
        _prefixed_benchmark_metrics,
        _add_compact_scores,
        _write_compact_markdown,
    )
    from diagnostics import (
        _symbol_attribution_rows,
        _trade_context_rows,
    )

def run_strategy_experiments(
    data_folder="hisseler",
    allowed_symbols=None,
    excluded_symbols=None,
    universe_symbols_file=None,
    experiments=None,
    run_profile="fast",
    quick_mode=True,
    profile_timing=False,
    output_name="strategy_experiment_raporu.csv",
    save_individual_reports=False,
    compact_report=True,
    compact_report_name="strategy_compact_report.csv",
    compact_markdown_name="strategy_compact_report.md",
    write_experiment_detail_files=False,
    keep_latest_experiment_files=False,
    reuse_prepared_data=True,
    commission_rate=0.001,
    slippage_mu=0.05,
    risk_off_exit_mode="hold_existing",
    max_workers=None,
    worker_utilization_target=0.85,
    parallel_chunk_size=None,
    use_prepared_feature_disk_cache=True,
    prepared_feature_cache_dir=".cache/prepared_features",
    force_rebuild_prepared_cache=False,
):
    if experiments is None:
        experiments = _default_strategy_experiments()
    if not isinstance(experiments, list):
        raise ValueError("experiments liste olmali.")
    if len(experiments) == 0:
        experiments = _default_strategy_experiments()

    output_dir = Path(data_folder).resolve().parent
    output_path = output_dir / output_name
    compact_csv_path = output_dir / compact_report_name
    compact_markdown_path = output_dir / compact_markdown_name
    symbol_attribution_path = output_dir / "strategy_symbol_attribution_report.csv"
    trade_context_path = output_dir / "strategy_trade_context_report.csv"
    latest_root = output_dir / "experiment_reports" / "_latest"
    tmp_root = output_dir / "experiment_reports" / "_tmp_compact"
    if not keep_latest_experiment_files and latest_root.exists():
        shutil.rmtree(latest_root, ignore_errors=True)
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    rows = []
    symbol_attribution_rows = []
    trade_context_rows = []
    performance_profile_rows = []
    total_runtime_start = time.perf_counter()

    prepared_groups = {}
    experiment_items = []
    for index, experiment in enumerate(experiments, 1):
        if not isinstance(experiment, dict):
            raise ValueError("Her experiment dict olmali.")
        effective_params = _experiment_effective_params(
            experiment,
            data_folder,
            allowed_symbols,
            excluded_symbols,
            universe_symbols_file,
            run_profile,
            quick_mode,
            commission_rate=commission_rate,
            slippage_mu=slippage_mu,
            risk_off_exit_mode=risk_off_exit_mode,
            max_workers=max_workers,
            worker_utilization_target=worker_utilization_target,
            parallel_chunk_size=parallel_chunk_size,
            use_prepared_feature_disk_cache=use_prepared_feature_disk_cache,
            prepared_feature_cache_dir=prepared_feature_cache_dir,
            force_rebuild_prepared_cache=force_rebuild_prepared_cache,
        )
        data_signature = _experiment_signature(effective_params, ENTRY_SIGNATURE_KEYS)
        exit_signature = _experiment_signature(effective_params, EXIT_SIGNATURE_KEYS)
        item = {
            "index": index,
            "experiment": experiment,
            "params": effective_params,
            "data_signature": data_signature,
            "exit_signature": exit_signature,
        }
        experiment_items.append(item)
        prepared_groups.setdefault(data_signature, []).append(item)
    data_signature_group_numbers = {
        data_signature: group_number
        for group_number, data_signature in enumerate(prepared_groups, 1)
    }

    timing_rows = []
    total_portfolio_seconds = 0.0
    total_fast_arrays_build_seconds = 0.0
    total_portfolio_loop_seconds = 0.0
    portfolio_runs = 0
    prepared_data_cache = {}
    prepared_data_cache_hits = 0
    prepared_data_cache_misses = 0
    run_level_prepared_cache = {}
    run_level_prepared_cache_hits = 0
    run_level_prepared_cache_misses = 0
    feature_cache_hits = 0
    feature_cache_misses = 0
    benchmark_cache_reused_count = 0
    asset_preparation_reused_count = 0
    effective_reuse_prepared_data = (
        bool(reuse_prepared_data) and not write_experiment_detail_files
    )
    print(f"Experiment groups count: {len(prepared_groups)}")
    print(f"Prepared data reused: {effective_reuse_prepared_data}")

    def _output_subdir_for(experiment_name):
        safe_name = _safe_output_name(experiment_name)
        if write_experiment_detail_files:
            return (
                f"experiment_reports/{safe_name}"
                if save_individual_reports
                else f"experiment_reports/_latest/{safe_name}"
            )
        return f"experiment_reports/_tmp_compact/{safe_name}"

    def _run_old_path(item):
        experiment_start = time.perf_counter()
        experiment = item["experiment"]
        params = item["params"]
        experiment_name = experiment.get("name", f"experiment_{item['index']}")
        print(
            f"\n[{item['index']}/{len(experiments)}] "
            f"Experiment calisiyor: {experiment_name}"
        )

        scanner_kwargs = dict(experiment)
        scanner_kwargs.pop("name", None)
        scanner_kwargs.setdefault("strategy_name", params["strategy_name"])
        scanner_kwargs.setdefault("commission_rate", params["commission_rate"])
        scanner_kwargs.setdefault("slippage_mu", params["slippage_mu"])
        scanner_kwargs.setdefault("risk_off_exit_mode", params["risk_off_exit_mode"])
        scanner_kwargs.setdefault("max_workers", params["max_workers"])
        scanner_kwargs.setdefault(
            "worker_utilization_target",
            params["worker_utilization_target"],
        )
        scanner_kwargs.setdefault("parallel_chunk_size", params["parallel_chunk_size"])
        scanner_kwargs.setdefault("excluded_symbols", params["excluded_symbols"])
        scanner_kwargs.setdefault(
            "use_entry_context_filter",
            params["use_entry_context_filter"],
        )
        scanner_kwargs.setdefault("max_entry_gap_pct", params["max_entry_gap_pct"])
        scanner_kwargs.setdefault(
            "max_entry_bb_distance_atr",
            params["max_entry_bb_distance_atr"],
        )
        scanner_kwargs.setdefault(
            "max_entry_signal_range_pct",
            params["max_entry_signal_range_pct"],
        )
        scanner_kwargs.setdefault(
            "min_entry_close_position_in_range",
            params["min_entry_close_position_in_range"],
        )
        scanner_kwargs.setdefault(
            "max_entry_close_position_in_range",
            params["max_entry_close_position_in_range"],
        )
        output_subdir = _output_subdir_for(experiment_name)
        metrics = run_universe_scanner(
            data_folder=data_folder,
            allowed_symbols=allowed_symbols,
            universe_symbols_file=universe_symbols_file,
            run_profile=run_profile,
            quick_mode=quick_mode,
            profile_timing=profile_timing,
            output_subdir=output_subdir,
            clear_stale_trade_log=True,
            **scanner_kwargs,
        )
        experiment_runtime = time.perf_counter() - experiment_start
        metrics_dict = _metrics_as_dict(metrics)
        symbol_attribution_rows.extend(
            _symbol_attribution_rows(
                experiment_name,
                metrics_dict.get("_trade_log", []),
            )
        )
        trade_context_rows.extend(
            _trade_context_rows(
                experiment_name,
                metrics_dict.get("_trade_log", []),
            )
        )
        print(
            "Experiment diagnostics: "
            f"symbols_loaded={metrics_dict.get('symbols_loaded', 0)}, "
            f"quality_filter_active_symbols="
            f"{metrics_dict.get('quality_filter_active_symbols', 0)}, "
            f"total_raw_entry_signals="
            f"{metrics_dict.get('total_raw_entry_signals', 0)}, "
            f"total_confirmed_entry_signals="
            f"{metrics_dict.get('total_confirmed_entry_signals', 0)}, "
            f"total_final_entry_signals="
            f"{metrics_dict.get('total_final_entry_signals', 0)}, "
            f"total_trades={metrics_dict.get('total_trades', metrics_dict.get('Islem Sayisi', 0))}"
        )
        rows.append(
            _experiment_metrics_row(
                experiment_name,
                metrics,
                _experiment_report_params(params, allowed_symbols),
            )
        )
        performance_profile_rows.append(
            _performance_profile_row(
                experiment_name,
                params,
                metrics_dict,
                {},
                experiment_runtime=experiment_runtime,
                preparation_runtime=0.0,
                portfolio_runtime=experiment_runtime,
                report_generation_runtime=0.0,
                symbols_processed=metrics_dict.get("symbols_loaded", 0),
                worker_count=params.get("max_workers") or 1,
                parallel_backend="run_universe_scanner",
                sequential_fallback=False,
                prepared_data_reused=False,
                experiment_count=len(experiments),
            )
        )
        if not write_experiment_detail_files:
            shutil.rmtree(output_dir / output_subdir, ignore_errors=True)

    if not effective_reuse_prepared_data:
        for item in experiment_items:
            _run_old_path(item)
    else:
        for item in experiment_items:
            experiment = item["experiment"]
            params = item["params"]
            data_key = item["data_signature"]
            group_number = data_signature_group_numbers[data_key]
            experiment_name = experiment.get("name", f"experiment_{item['index']}")
            experiment_start = time.perf_counter()
            preparation_seconds_for_experiment = 0.0
            prepared_data_reused_for_experiment = False
            rotation_cache_key = (
                _rotation_run_level_cache_key(params)
                if params.get("strategy_name") in ROTATION_RUN_LEVEL_REUSE_STRATEGIES
                else None
            )
            print(
                f"\n[{item['index']}/{len(experiments)}] "
                f"Experiment calisiyor: {experiment_name}"
            )
            if data_key in prepared_data_cache:
                prepared_data_cache_hits += 1
                asset_preparation_reused_count += 1
                prepared_result = prepared_data_cache[data_key]
                prepared_data_reused_for_experiment = True
                print(f"Prepared data reused for experiment {experiment_name}")
                print("Asset preparation skipped for reused experiment")
            elif rotation_cache_key is not None and rotation_cache_key in run_level_prepared_cache:
                prepared_data_cache_hits += 1
                run_level_prepared_cache_hits += 1
                asset_preparation_reused_count += 1
                derive_start = time.perf_counter()
                prepared_result = _derive_rotation_prepared_bundle(
                    run_level_prepared_cache[rotation_cache_key],
                    params,
                )
                prepare_elapsed = time.perf_counter() - derive_start
                preparation_seconds_for_experiment = prepare_elapsed
                prepared_data_reused_for_experiment = True
                prepared_data_cache[data_key] = prepared_result
                symbol_count = len(prepared_result.get("asset_data", {}))
                feature_cache_hits += symbol_count
                if prepared_result.get("benchmark_data") is not None:
                    benchmark_cache_reused_count += 1
                cache_summary = prepared_result.get("cache_summary", {})
                cache_summary.update(
                    {
                        "run_level_prepared_cache_hits": 1,
                        "run_level_prepared_cache_misses": 0,
                        "feature_cache_hits": symbol_count,
                        "feature_cache_misses": 0,
                        "benchmark_cache_reused": bool(
                            prepared_result.get("benchmark_data") is not None
                        ),
                    }
                )
                print(f"Run-level prepared data reused for experiment {experiment_name}")
                print(
                    "Asset preparation skipped; strategy signals regenerated in memory"
                )
            elif (
                rotation_cache_key is not None
                and (
                    disk_cache_entry := _read_prepared_feature_disk_cache(params)
                )[0]
                is not None
            ):
                prepared_data_cache_hits += 1
                asset_preparation_reused_count += 1
                disk_base_prepared, disk_cache_stats = disk_cache_entry
                run_level_prepared_cache[rotation_cache_key] = disk_base_prepared
                derive_start = time.perf_counter()
                prepared_result = _derive_rotation_prepared_bundle(
                    disk_base_prepared,
                    params,
                )
                prepare_elapsed = time.perf_counter() - derive_start
                preparation_seconds_for_experiment = (
                    prepare_elapsed
                    + float(
                        disk_cache_stats.get(
                            "prepared_disk_cache_read_sec",
                            0.0,
                        )
                        or 0.0
                    )
                )
                prepared_data_reused_for_experiment = True
                prepared_data_cache[data_key] = prepared_result
                symbol_count = len(prepared_result.get("asset_data", {}))
                feature_cache_hits += symbol_count
                if prepared_result.get("benchmark_data") is not None:
                    benchmark_cache_reused_count += 1
                cache_summary = prepared_result.get("cache_summary", {})
                cache_summary.update(
                    {
                        "run_level_prepared_cache_hits": 0,
                        "run_level_prepared_cache_misses": 0,
                        "feature_cache_hits": symbol_count,
                        "feature_cache_misses": 0,
                        "benchmark_cache_reused": bool(
                            prepared_result.get("benchmark_data") is not None
                        ),
                        **disk_cache_stats,
                    }
                )
                print(f"Prepared feature disk cache hit for experiment {experiment_name}")
                print(
                    "Asset preparation skipped; strategy signals regenerated from disk cache"
                )
            else:
                prepared_data_cache_misses += 1
                if rotation_cache_key is not None:
                    run_level_prepared_cache_misses += 1
                    _, disk_cache_stats = _read_prepared_feature_disk_cache(params)
                else:
                    disk_cache_stats = _empty_prepared_disk_cache_stats()
                print(f"Prepared data built once for group {group_number}")
                prepare_start = time.perf_counter()
                prepared_result = prepare_universe_data(
                    data_folder=data_folder,
                    allowed_symbols=allowed_symbols,
                    excluded_symbols=params["excluded_symbols"],
                    universe_symbols_file=universe_symbols_file,
                    run_profile=params["run_profile"],
                    execution_mode=params["execution_mode"],
                    use_market_calendar=params["use_market_calendar"],
                    include_partial_session_bars=params[
                        "include_partial_session_bars"
                    ],
                    hurst_filter_mode="off",
                    hurst_threshold=0.5,
                    strategy_name=params["strategy_name"],
                    rsi_length=params["rsi_length"],
                    rsi_entry_threshold=params["rsi_entry_threshold"],
                    bollinger_length=params["bollinger_length"],
                    bollinger_std=params["bollinger_std"],
                    bb_entry_tolerance_atr=params["bb_entry_tolerance_atr"],
                    use_quality_filter=params["use_quality_filter"],
                    min_price_filter=params["min_price_filter"],
                    min_atr_pct_filter=params["min_atr_pct_filter"],
                    max_atr_pct_filter=params["max_atr_pct_filter"],
                    min_avg_dollar_volume=params["min_avg_dollar_volume"],
                    avg_volume_lookback=params["avg_volume_lookback"],
                    trend_sma_length=params["trend_sma_length"],
                    setup_lookback=params["setup_lookback"],
                    confirmation_mode=params["confirmation_mode"],
                    confirmation_lookback=params["confirmation_lookback"],
                    confirmation_min_rebound_atr=params[
                        "confirmation_min_rebound_atr"
                    ],
                    use_entry_context_filter=params["use_entry_context_filter"],
                    max_entry_gap_pct=params["max_entry_gap_pct"],
                    max_entry_bb_distance_atr=params["max_entry_bb_distance_atr"],
                    max_entry_signal_range_pct=params[
                        "max_entry_signal_range_pct"
                    ],
                    min_entry_close_position_in_range=params[
                        "min_entry_close_position_in_range"
                    ],
                    max_entry_close_position_in_range=params[
                        "max_entry_close_position_in_range"
                    ],
                    csm_lookback_days=params["csm_lookback_days"],
                    csm_top_n=params["csm_top_n"],
                    csm_rebalance_frequency=params["csm_rebalance_frequency"],
                    csm_trend_sma_days=params["csm_trend_sma_days"],
                    csm_use_market_regime=params["csm_use_market_regime"],
                    csm_market_regime_sma_days=params["csm_market_regime_sma_days"],
                    csm_min_price=params["csm_min_price"],
                    csm_max_atr_pct=params["csm_max_atr_pct"],
                    donchian_entry_lookback_days=params["donchian_entry_lookback_days"],
                    donchian_exit_lookback_days=params["donchian_exit_lookback_days"],
                    donchian_top_n=params["donchian_top_n"],
                    donchian_rebalance_frequency=params["donchian_rebalance_frequency"],
                    donchian_trend_sma_days=params["donchian_trend_sma_days"],
                    donchian_use_market_regime=params["donchian_use_market_regime"],
                    donchian_market_regime_sma_days=params[
                        "donchian_market_regime_sma_days"
                    ],
                    donchian_min_price=params["donchian_min_price"],
                    donchian_max_atr_pct=params["donchian_max_atr_pct"],
                    rs_lookback_days=params["rs_lookback_days"],
                    rs_top_n=params["rs_top_n"],
                    rs_rebalance_frequency=params["rs_rebalance_frequency"],
                    rs_score_mode=params["rs_score_mode"],
                    rs_vol_lookback_days=params["rs_vol_lookback_days"],
                    rs_trend_sma_days=params["rs_trend_sma_days"],
                    rs_use_market_regime=params["rs_use_market_regime"],
                    rs_market_regime_sma_days=params["rs_market_regime_sma_days"],
                    rs_min_price=params["rs_min_price"],
                    rs_max_atr_pct=params["rs_max_atr_pct"],
                    profile_timing=profile_timing,
                    max_workers=params["max_workers"],
                    worker_utilization_target=params["worker_utilization_target"],
                    parallel_chunk_size=params["parallel_chunk_size"],
                )
                prepare_elapsed = time.perf_counter() - prepare_start
                if not (
                    isinstance(prepared_result, dict)
                    and "asset_data" in prepared_result
                    and prepared_result["asset_data"]
                ):
                    _run_old_path(item)
                    continue
                prepared_result["asset_preparation_seconds"] = prepare_elapsed
                preparation_seconds_for_experiment = prepare_elapsed
                if rotation_cache_key is not None:
                    run_level_prepared_cache[rotation_cache_key] = prepared_result
                    feature_cache_misses += len(prepared_result.get("asset_data", {}))
                    disk_write_stats = _write_prepared_feature_disk_cache(
                        params,
                        prepared_result,
                    )
                    cache_summary = prepared_result.get("cache_summary", {})
                    cache_summary.update(
                        {
                            "run_level_prepared_cache_hits": 0,
                            "run_level_prepared_cache_misses": 1,
                            "feature_cache_hits": 0,
                            "feature_cache_misses": len(prepared_result.get("asset_data", {})),
                            "benchmark_cache_reused": False,
                            **disk_cache_stats,
                            **disk_write_stats,
                        }
                    )
                prepared_data_cache[data_key] = prepared_result

            portfolio_start = time.perf_counter()
            metrics = _run_prepared_portfolio_experiment(
                prepared_result,
                params,
                timing=None,
            )
            if isinstance(metrics, dict):
                print(
                    "Experiment diagnostics: "
                    f"symbols_loaded={metrics.get('symbols_loaded', 0)}, "
                    f"quality_filter_active_symbols="
                    f"{metrics.get('quality_filter_active_symbols', 0)}, "
                    f"total_raw_entry_signals="
                    f"{metrics.get('total_raw_entry_signals', 0)}, "
                    f"total_confirmed_entry_signals="
                    f"{metrics.get('total_confirmed_entry_signals', 0)}, "
                    f"total_final_entry_signals="
                    f"{metrics.get('total_final_entry_signals', 0)}, "
                    f"total_trades={metrics.get('total_trades', metrics.get('Islem Sayisi', 0))}"
                )
                symbol_attribution_rows.extend(
                    _symbol_attribution_rows(
                        experiment_name,
                        metrics.get("_trade_log", []),
                    )
                )
                trade_context_rows.extend(
                    _trade_context_rows(
                        experiment_name,
                        metrics.get("_trade_log", []),
                        TRADE_CONTEXT_REPORT_COLUMNS,
                    )
                )
            portfolio_seconds = time.perf_counter() - portfolio_start
            experiment_runtime = time.perf_counter() - experiment_start
            total_portfolio_seconds += portfolio_seconds
            if isinstance(metrics, dict):
                total_fast_arrays_build_seconds += float(
                    metrics.get("_portfolio_fast_arrays_build_seconds", 0.0)
                )
                total_portfolio_loop_seconds += float(
                    metrics.get("_portfolio_loop_seconds", 0.0)
                )
            portfolio_runs += 1
            rows.append(
                _experiment_metrics_row(
                    experiment_name,
                    metrics,
                    _experiment_report_params(params, allowed_symbols),
                )
            )
            if isinstance(metrics, dict):
                cache_summary = prepared_result.get("cache_summary", {})
                performance_profile_rows.append(
                    _performance_profile_row(
                        experiment_name,
                        params,
                        metrics,
                        cache_summary,
                        experiment_runtime=experiment_runtime,
                        preparation_runtime=preparation_seconds_for_experiment,
                        portfolio_runtime=portfolio_seconds,
                        report_generation_runtime=0.0,
                        symbols_processed=metrics.get(
                            "symbols_loaded",
                            len(prepared_result.get("asset_data", {})),
                        ),
                        worker_count=cache_summary.get("worker_count", 1),
                        parallel_backend=cache_summary.get(
                            "parallel_backend",
                            "unknown",
                        ),
                        sequential_fallback=cache_summary.get(
                            "sequential_fallback",
                            False,
                        ),
                        prepared_data_reused=prepared_data_reused_for_experiment,
                        experiment_count=len(experiments),
                    )
                )

        for cache_index, prepared_result in enumerate(
            prepared_data_cache.values(),
            1,
        ):
            cache_summary = prepared_result.get("cache_summary", {})
            asset_preparation_seconds = (
                float(cache_summary.get("cache_load_seconds", 0.0))
                + float(cache_summary.get("cache_build_seconds", 0.0))
            )
            if asset_preparation_seconds <= 0:
                asset_preparation_seconds = float(
                    prepared_result.get("asset_preparation_seconds", 0.0)
                )
            timing_rows.append(
                {
                    "Group": cache_index,
                    "Experiment Groups Count": len(prepared_data_cache),
                    "data_signature_group_count": len(prepared_data_cache),
                    "Experiment Count": len(experiments),
                    "reuse_prepared_data": effective_reuse_prepared_data,
                    "fast_backtest": bool(
                        experiment_items[0]["params"].get("fast_backtest", True)
                    ) if experiment_items else True,
                    "prepared_data_cache_hits": prepared_data_cache_hits,
                    "prepared_data_cache_misses": prepared_data_cache_misses,
                    "asset_preparation_reused_count": asset_preparation_reused_count,
                    "Prepared Data Reused": True,
                    "asset_preparation_seconds": round(
                        asset_preparation_seconds,
                        6,
                    ),
                    "portfolio_backtest_seconds_total": round(
                        total_portfolio_seconds,
                        6,
                    ),
                    "portfolio_backtest_seconds_avg_per_experiment": round(
                        total_portfolio_seconds / max(portfolio_runs, 1),
                        6,
                    ),
                    "portfolio_fast_arrays_build_seconds": round(
                        total_fast_arrays_build_seconds,
                        6,
                    ),
                    "portfolio_loop_seconds": round(
                        total_portfolio_loop_seconds,
                        6,
                    ),
                    "Asset Preparation Seconds": round(asset_preparation_seconds, 6),
                    "Total PortfolioManager Seconds": round(
                        total_portfolio_seconds,
                        6,
                    ),
                    "Avg PortfolioManager Seconds Per Experiment": round(
                        total_portfolio_seconds / max(portfolio_runs, 1),
                        6,
                    ),
                    "130m Cache Hits": cache_summary.get("per_symbol_130m_hits", 0),
                    "130m Cache Misses": cache_summary.get(
                        "per_symbol_130m_misses",
                        0,
                    ),
                    "1m Cache Hits": cache_summary.get("per_symbol_1m_hits", 0),
                    "1m Cache Misses": cache_summary.get("per_symbol_1m_misses", 0),
                }
            )

    if timing_rows:
        timing_path = output_dir / "strategy_experiment_timing_raporu.csv"
        timing_rows.append(
            {
                "Group": "TOTAL",
                "Experiment Groups Count": len(prepared_groups),
                "data_signature_group_count": len(prepared_groups),
                "Experiment Count": len(experiments),
                "reuse_prepared_data": effective_reuse_prepared_data,
                "fast_backtest": bool(
                    experiment_items[0]["params"].get("fast_backtest", True)
                ) if experiment_items else True,
                "prepared_data_cache_hits": prepared_data_cache_hits,
                "prepared_data_cache_misses": prepared_data_cache_misses,
                "asset_preparation_reused_count": asset_preparation_reused_count,
                "Prepared Data Reused": effective_reuse_prepared_data,
                "asset_preparation_seconds": round(
                    sum(row["Asset Preparation Seconds"] for row in timing_rows),
                    6,
                ),
                "portfolio_backtest_seconds_total": round(
                    total_portfolio_seconds,
                    6,
                ),
                "portfolio_backtest_seconds_avg_per_experiment": round(
                    total_portfolio_seconds / max(portfolio_runs, 1),
                    6,
                ),
                "portfolio_fast_arrays_build_seconds": round(
                    total_fast_arrays_build_seconds,
                    6,
                ),
                "portfolio_loop_seconds": round(total_portfolio_loop_seconds, 6),
                "Asset Preparation Seconds": round(
                    sum(row["Asset Preparation Seconds"] for row in timing_rows),
                    6,
                ),
                "Total PortfolioManager Seconds": round(total_portfolio_seconds, 6),
                "Avg PortfolioManager Seconds Per Experiment": round(
                    total_portfolio_seconds / max(portfolio_runs, 1),
                    6,
                ),
                "130m Cache Hits": sum(row["130m Cache Hits"] for row in timing_rows),
                "130m Cache Misses": sum(
                    row["130m Cache Misses"] for row in timing_rows
                ),
                "1m Cache Hits": sum(row["1m Cache Hits"] for row in timing_rows),
                "1m Cache Misses": sum(row["1m Cache Misses"] for row in timing_rows),
            }
        )
        pd.DataFrame(timing_rows).to_csv(timing_path, index=False)

    report_generation_start = time.perf_counter()
    report = pd.DataFrame(
        rows,
        columns=(
            ["Experiment Name"]
            + EXPERIMENT_PARAM_COLUMNS
            + EXPERIMENT_METRIC_COLUMNS
        ),
    )
    report = _add_compact_scores(report)
    report["Survivorship Bias Warning"] = SURVIVORSHIP_BIAS_WARNING
    report["Dividend Adjustment Warning"] = DIVIDEND_ADJUSTMENT_WARNING
    report = report.sort_values("Compact Score", ascending=False, na_position="last")
    report.to_csv(output_path, index=False)
    pd.DataFrame(
        symbol_attribution_rows,
        columns=SYMBOL_ATTRIBUTION_COLUMNS,
    ).to_csv(symbol_attribution_path, index=False)
    pd.DataFrame(
        trade_context_rows,
        columns=TRADE_CONTEXT_REPORT_COLUMNS,
    ).to_csv(trade_context_path, index=False)
    if compact_report:
        report.to_csv(compact_csv_path, index=False)
        generated_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        _write_compact_markdown(
            report,
            compact_markdown_path,
            allowed_symbols,
            generated_at,
        )
    if not write_experiment_detail_files and tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    report_generation_seconds = time.perf_counter() - report_generation_start
    if performance_profile_rows:
        per_experiment_report_seconds = report_generation_seconds / max(
            len(performance_profile_rows),
            1,
        )
        for profile_row in performance_profile_rows:
            profile_row["report_generation_sec"] = round(
                per_experiment_report_seconds,
                6,
            )
        total_runtime_seconds = time.perf_counter() - total_runtime_start
        total_profile_row = {
            "Experiment Name": "TOTAL",
            "Strategy Name": "ALL",
            "Runtime Sec": round(total_runtime_seconds, 6),
            "Symbols Processed": sum(
                int(row.get("Symbols Processed", 0) or 0)
                for row in performance_profile_rows
            ),
            "Trades": sum(
                int(row.get("Trades", 0) or 0)
                for row in performance_profile_rows
            ),
            "Cache Hit Count": sum(
                int(row.get("Cache Hit Count", 0) or 0)
                for row in performance_profile_rows
            ),
            "Cache Miss Count": sum(
                int(row.get("Cache Miss Count", 0) or 0)
                for row in performance_profile_rows
            ),
            "total_runtime_sec": round(total_runtime_seconds, 6),
            "data_loading_sec": round(
                sum(float(row.get("data_loading_sec", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "cache_read_sec": round(
                sum(float(row.get("cache_read_sec", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "cache_write_sec": round(
                sum(float(row.get("cache_write_sec", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "indicator_calculation_sec": round(
                sum(float(row.get("indicator_calculation_sec", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "daily_resample_sec": round(
                sum(float(row.get("daily_resample_sec", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "strategy_signal_generation_sec": round(
                sum(float(row.get("strategy_signal_generation_sec", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "portfolio_backtest_sec": round(
                sum(float(row.get("portfolio_backtest_sec", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "report_generation_sec": round(report_generation_seconds, 6),
            "per_experiment_runtime_sec": round(
                sum(float(row.get("Runtime Sec", 0.0) or 0.0) for row in performance_profile_rows)
                / max(len(performance_profile_rows), 1),
                6,
            ),
            "per_symbol_preparation_sec": round(
                sum(float(row.get("data_loading_sec", 0.0) or 0.0) for row in performance_profile_rows)
                / max(sum(int(row.get("Symbols Processed", 0) or 0) for row in performance_profile_rows), 1),
                6,
            ),
            "per_symbol_backtest_sec": round(
                sum(float(row.get("portfolio_backtest_sec", 0.0) or 0.0) for row in performance_profile_rows)
                / max(sum(int(row.get("Symbols Processed", 0) or 0) for row in performance_profile_rows), 1),
                6,
            ),
            "os_cpu_count": os.cpu_count() or 1,
            "worker_count": max(
                int(row.get("worker_count", 1) or 1)
                for row in performance_profile_rows
            ),
            "parallel_backend": "mixed",
            "chunk_size": max(
                int(row.get("chunk_size", 1) or 1)
                for row in performance_profile_rows
            ),
            "batch_size": max(
                int(row.get("batch_size", 1) or 1)
                for row in performance_profile_rows
            ),
            "avg_symbols_per_worker": round(
                sum(float(row.get("avg_symbols_per_worker", 0.0) or 0.0) for row in performance_profile_rows)
                / max(len(performance_profile_rows), 1),
                4,
            ),
            "sequential_fallback": any(
                bool(row.get("sequential_fallback", False))
                for row in performance_profile_rows
            ),
            "prepared_data_reused": effective_reuse_prepared_data,
            "worker_utilization_target": worker_utilization_target,
            "run_level_prepared_cache_hits": sum(
                int(row.get("run_level_prepared_cache_hits", 0) or 0)
                for row in performance_profile_rows
            ),
            "run_level_prepared_cache_misses": sum(
                int(row.get("run_level_prepared_cache_misses", 0) or 0)
                for row in performance_profile_rows
            ),
            "feature_cache_hits": sum(
                int(row.get("feature_cache_hits", 0) or 0)
                for row in performance_profile_rows
            ),
            "feature_cache_misses": sum(
                int(row.get("feature_cache_misses", 0) or 0)
                for row in performance_profile_rows
            ),
            "benchmark_cache_reused": any(
                bool(row.get("benchmark_cache_reused", False))
                for row in performance_profile_rows
            ),
            "avg_worker_runtime_sec": round(
                sum(float(row.get("avg_worker_runtime_sec", 0.0) or 0.0) for row in performance_profile_rows)
                / max(len(performance_profile_rows), 1),
                6,
            ),
            "max_worker_runtime_sec": max(
                float(row.get("max_worker_runtime_sec", 0.0) or 0.0)
                for row in performance_profile_rows
            ),
            "parallel_overhead_estimate_sec": round(
                sum(float(row.get("parallel_overhead_estimate_sec", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "prepared_disk_cache_hits": sum(
                int(row.get("prepared_disk_cache_hits", 0) or 0)
                for row in performance_profile_rows
            ),
            "prepared_disk_cache_misses": sum(
                int(row.get("prepared_disk_cache_misses", 0) or 0)
                for row in performance_profile_rows
            ),
            "prepared_disk_cache_write_sec": round(
                sum(float(row.get("prepared_disk_cache_write_sec", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "prepared_disk_cache_read_sec": round(
                sum(float(row.get("prepared_disk_cache_read_sec", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "prepared_disk_cache_size_mb": round(
                sum(float(row.get("prepared_disk_cache_size_mb", 0.0) or 0.0) for row in performance_profile_rows),
                6,
            ),
            "experiment_count": len(experiments),
        }
        performance_profile_rows.append(total_profile_row)
        performance_profile_path = output_dir / "performance_profile_report.csv"
        pd.DataFrame(performance_profile_rows).to_csv(
            performance_profile_path,
            index=False,
        )
        _print_performance_summary(performance_profile_rows)

    print(f"\nStrategy experiment raporu: {output_path}")
    print(f"Symbol attribution report: {symbol_attribution_path}")
    print(f"Trade context report: {trade_context_path}")
    if performance_profile_rows:
        print(f"Performance profile report: {output_dir / 'performance_profile_report.csv'}")
    if compact_report:
        print(f"Compact CSV path: {compact_csv_path}")
        print(f"Compact Markdown path: {compact_markdown_path}")
        print("ChatGPT'ye analiz icin bu iki dosyayi gondermen yeterli.")
    if not report.empty:
        ranked = report.copy()
        ranked = ranked.sort_values("Compact Score", ascending=False, na_position="last")
        print("\nCompact Score'a gore ilk 10 experiment:")
        print(ranked.head(10).to_string(index=False))
    return report

try:
    from .diagnostics import (
        _asset_signal_diagnostics,
        _calendar_report_summary,
        _validate_asset_signal_columns,
        _zero_trade_metrics,
    )
    from .indicators import (
        add_market_regime,
        calculate_atr,
        calculate_hurst_exponent,
        calculate_market_regime,
        calculate_supertrend,
    )
    from .strategies.dual_momentum import (
        _normalize_dual_momentum_params,
        generate_dual_momentum_signals,
    )
    from .strategies.dual_momentum import (
        _apply_dual_momentum_top_signals,
        _run_dual_momentum_backtest,
        generate_dual_momentum_signals,
        _normalize_dual_momentum_params,
    )
    from .strategies.cross_sectional_momentum import (
        _apply_cross_sectional_momentum_top_signals,
        _normalize_cross_sectional_momentum_params,
        _run_cross_sectional_momentum_backtest,
        generate_cross_sectional_momentum_signals,
    )
    from .strategies.donchian_breakout import (
        _apply_donchian_breakout_top_signals,
        _normalize_donchian_breakout_params,
        _run_donchian_breakout_backtest,
        generate_donchian_breakout_signals,
    )
    from .strategies.relative_strength_rotation import (
        _SimpleBacktestResult,
        _apply_relative_strength_rotation_top_signals,
        _normalize_relative_strength_params,
        _normalize_rs_rebalance_frequency,
        _normalize_rs_score_mode,
        _run_relative_strength_rotation_backtest,
        generate_relative_strength_rotation_signals,
    )
except ImportError:
    from diagnostics import (
        _asset_signal_diagnostics,
        _calendar_report_summary,
        _validate_asset_signal_columns,
        _zero_trade_metrics,
    )
    from indicators import (
        add_market_regime,
        calculate_atr,
        calculate_hurst_exponent,
        calculate_market_regime,
        calculate_supertrend,
    )
    from strategies.dual_momentum import (
        _normalize_dual_momentum_params,
        generate_dual_momentum_signals,
    )
    from strategies.dual_momentum import (
        _apply_dual_momentum_top_signals,
        _run_dual_momentum_backtest,
        generate_dual_momentum_signals,
        _normalize_dual_momentum_params,
    )
    from strategies.cross_sectional_momentum import (
        _apply_cross_sectional_momentum_top_signals,
        _normalize_cross_sectional_momentum_params,
        _run_cross_sectional_momentum_backtest,
        generate_cross_sectional_momentum_signals,
    )
    from strategies.donchian_breakout import (
        _apply_donchian_breakout_top_signals,
        _normalize_donchian_breakout_params,
        _run_donchian_breakout_backtest,
        generate_donchian_breakout_signals,
    )
    from strategies.relative_strength_rotation import (
        _SimpleBacktestResult,
        _apply_relative_strength_rotation_top_signals,
        _normalize_relative_strength_params,
        _normalize_rs_rebalance_frequency,
        _normalize_rs_score_mode,
        _run_relative_strength_rotation_backtest,
        generate_relative_strength_rotation_signals,
    )


if __name__ == "__main__":
    # run_universe_scanner("hisseler", hurst_filter_mode="off")
    # run_universe_scanner("hisseler", hurst_filter_mode="gt", hurst_threshold=0.5)
    # run_universe_scanner("hisseler", hurst_filter_mode="lt", hurst_threshold=0.5)
    # run_universe_scanner("hisseler", universe_symbols_file="tech_universe.csv")
    # run_universe_scanner("hisseler", allowed_symbols=["AAPL", "MSFT", "NVDA", "AMD"])
    # run_universe_scanner("hisseler", evaluation_mode="split", train_start="2021-01-01", train_end="2022-12-31", test_start="2023-01-01", test_end="2023-12-31")
    # run_universe_scanner("hisseler", evaluation_mode="walk_forward", walk_train_months=18, walk_test_months=6)
    # run_universe_scanner("hisseler", use_market_calendar=True, include_partial_session_bars=False)
    # run_universe_scanner("hisseler", run_profile="fast")
    # run_universe_scanner("hisseler", run_profile="full", monte_carlo=True, use_market_calendar=True)
    # run_universe_scanner("hisseler", run_profile="full", execution_mode="intrabar_1m", monte_carlo=True)
    #run_universe_scanner("hisseler", run_profile="fast")
    # run_strategy_experiments(
    #     "hisseler",
    #     allowed_symbols=["AAPL", "MSFT", "NVDA", "AMD", "META"],
    #     run_profile="fast",
    #     quick_mode=True,
    #     compact_report=True,
    #     write_experiment_detail_files=False,
    # )
    # run_strategy_experiments(
    #     "hisseler",
    #     allowed_symbols=["AAPL", "MSFT", "NVDA", "AMD", "META"],
    #     run_profile="fast",
    #     quick_mode=False,
    #     compact_report=True,
    #     write_experiment_detail_files=True,
    #     save_individual_reports=True,
    # )
    experiments = [
    {
        "name": "compat_current_strategy",
        "strategy_name": "current_pullback_mean_reversion",
        "excluded_symbols": ["QQQ", "SPY"],
        "rsi_entry_threshold": 20,
        "bb_entry_tolerance_atr": 0.0,
        "use_quality_filter": False,
        "confirmation_mode": "close_above_signal_high",
        "confirmation_lookback": 1,
        "hurst_filter_mode": "gt",
        "hurst_threshold": 0.5,
        "exit_mode": "sma_or_target",
        "take_profit_atr_multiplier": 2.0,
        "disaster_stop_atr_multiplier": 1.5,
        "time_stop_bars": 10,
        "risk_off_exit_mode": "hold_existing",
    },
    {
        "name": "rs_m63_sma100",
        "strategy_name": "relative_strength_rotation",
        "excluded_symbols": ["QQQ", "SPY"],
        "rs_lookback_days": 63,
        "rs_top_n": 20,
        "rs_rebalance_frequency": "monthly",
        "rs_score_mode": "return_over_vol",
        "rs_vol_lookback_days": 60,
        "rs_trend_sma_days": 100,
        "rs_use_market_regime": True,
        "rs_market_regime_sma_days": 200,
        "rs_min_price": 10,
        "rs_max_atr_pct": 12,
    },
    {
        "name": "rs_m63_no_regime",
        "strategy_name": "relative_strength_rotation",
        "excluded_symbols": ["QQQ", "SPY"],
        "rs_lookback_days": 63,
        "rs_top_n": 20,
        "rs_rebalance_frequency": "monthly",
        "rs_score_mode": "return_over_vol",
        "rs_vol_lookback_days": 60,
        "rs_trend_sma_days": 200,
        "rs_use_market_regime": False,
        "rs_market_regime_sma_days": 200,
        "rs_min_price": 10,
        "rs_max_atr_pct": 12,
    },
]
    
    run_strategy_experiments(
        "hisseler",
        #allowed_symbols=[
         #   "AAPL", "MSFT", "NVDA", "AMD", "META",
          #  "GOOGL", "AMZN", "TSLA", "NFLX", "AVGO",
           # "QCOM", "INTC", "MU", "ORCL", "IBM",
            #"MA", "V", "CMCSA", "SPOT", "SNAP",
            #"AMAT", "TXN", "HON", "GE", "RTX",
            #"CAT", "BA", "UPS", "FDX", "DE",
        #    "JPM", "BAC", "WFC", "C", "GS",
         #   "MS", "SCHW", "BLK", "AXP", "COF",
          #  "UNH", "LLY", "JNJ", "MRK", "PFE",
           # "BMY", "ABBV", "ABT", "AMGN", "TMO",
        #],
        experiments=experiments,
        run_profile="fast",
        quick_mode=True,
        compact_report=True,
        write_experiment_detail_files=False,
        profile_timing=True,
        reuse_prepared_data=True,
    )
