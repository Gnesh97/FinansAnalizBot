import glob
import hashlib
import importlib
import math
import os
import shutil
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
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

PORTFOLIO_REQUIRED_COLUMNS = [
    "Open",
    "High",
    "Low",
    "Close",
    "ENTRY_SIGNAL",
    "EXIT_SIGNAL",
    "SMA20_Reversion_Exit",
    "Take_Profit",
    "Hurst_Exponent",
    "Momentum_Factor",
    "Momentum_Factor_Raw",
    "ATR_Pct",
    "Disaster_Stop",
    "ATR_Take_Profit_Distance",
    "SuperTrend_ATR_22",
]

CACHE_REQUIRED_COLUMNS = set(
    PORTFOLIO_REQUIRED_COLUMNS
    + [
        "Indicators_Ready",
        "Raw_Entry_Signal",
        "Hurst_Filter_OK",
        "Macro_Filter_OK",
        "Is_Partial_Session_Bar",
        "Take_Profit",
        "Risk_Off",
        "Regime_Ready",
    ]
)

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


def _normalize_ranking_mode(ranking_mode):
    mode = str(ranking_mode).lower()
    if mode not in {"momentum", "hurst", "none"}:
        raise ValueError(
            "ranking_mode 'momentum', 'hurst' veya 'none' degerlerinden biri olmali."
        )
    return mode


def _normalize_execution_mode(execution_mode):
    mode = str(execution_mode).lower()
    if mode not in {"bar_close", "intrabar_1m"}:
        raise ValueError(
            "execution_mode 'bar_close' veya 'intrabar_1m' olmali."
        )
    return mode


def _strategy_cache_config(
    hurst_filter_mode,
    hurst_threshold,
    use_market_calendar=True,
    include_partial_session_bars=False,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    trend_sma_length=50,
    setup_lookback=3,
):
    return {
        "strategy_cache_version": STRATEGY_CACHE_VERSION,
        "hurst_filter_mode": _normalize_hurst_filter_mode(hurst_filter_mode),
        "hurst_threshold": float(hurst_threshold),
        "use_market_calendar": bool(use_market_calendar),
        "include_partial_session_bars": bool(include_partial_session_bars),
        "rsi_length": int(rsi_length),
        "rsi_entry_threshold": float(rsi_entry_threshold),
        "bollinger_length": int(bollinger_length),
        "bollinger_std": float(bollinger_std),
        "trend_sma_length": int(trend_sma_length),
        "setup_lookback": int(setup_lookback),
    }


def add_entry_indicators(
    df,
    hurst_filter_mode="off",
    hurst_threshold=0.5,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    trend_sma_length=50,
    setup_lookback=3,
):
    hurst_filter_mode = _normalize_hurst_filter_mode(hurst_filter_mode)
    hurst_threshold = float(hurst_threshold)
    rsi_length = int(rsi_length)
    rsi_entry_threshold = float(rsi_entry_threshold)
    bollinger_length = int(bollinger_length)
    bollinger_std = float(bollinger_std)
    trend_sma_length = int(trend_sma_length)
    setup_lookback = int(setup_lookback)
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
    df["Trigger"] = (
        (df["RSI_Value"] < rsi_entry_threshold)
        & (df["Close"] < df["Bollinger_Lower"])
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
    df["ROC_20"] = df["Close"].pct_change(20)
    net_change = df["Close"].diff(20).abs()
    vol_sum = df["Close"].diff().abs().rolling(20, min_periods=20).sum()
    df["ER_20"] = net_change / vol_sum.replace(0, np.nan)
    df["ATR_Pct"] = df["SuperTrend_ATR_22"] / df["Close"].replace(0, np.nan)
    df["Momentum_Factor_Raw"] = (
        df["ROC_20"] / df["ATR_Pct"].replace(0, np.nan)
    ) * df["ER_20"]
    df["Momentum_Factor"] = df["Momentum_Factor_Raw"]
    df["SuperTrend_Lower_22_22"] = lower_band
    df["SuperTrend_22_22"] = supertrend
    df["SuperTrend_Direction"] = direction
    df["ATR_Take_Profit_Distance"] = (
        df["SuperTrend_ATR_22"] * TAKE_PROFIT_ATR_MULTIPLIER
    )
    sma20 = df["Close"].rolling(20).mean()
    df["SMA20_Reversion_Exit"] = df["Close"] > sma20
    df["Take_Profit"] = df["SMA20_Reversion_Exit"]
    df["Disaster_Stop"] = (
        df["Close"] - (df["SuperTrend_ATR_22"] * DISASTER_STOP_ATR_MULTIPLIER)
    )
    df["Indicators_Ready"] &= df[
        [
            "SuperTrend_ATR_22",
            "ATR_Pct",
            "Momentum_Factor_Raw",
            "ATR_Take_Profit_Distance",
            "SMA20_Reversion_Exit",
            "Disaster_Stop",
        ]
    ].notna().all(axis=1)
    df["Risk_Off"] = df["Risk_Off"].fillna(True).astype(bool)
    df["Macro_Filter_OK"] = df["Macro_Uptrend"].fillna(False).astype(bool)
    df["Raw_Entry_Signal"] = (
        df["Trigger"]
        & df["Macro_Filter_OK"]
        & df["Indicators_Ready"]
    )
    df["ENTRY_SIGNAL"] = (
        df["Raw_Entry_Signal"]
        & df["Hurst_Filter_OK"].fillna(False).astype(bool)
        & (~df["Risk_Off"])
    )
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
            trend_sma_length=trend_sma_length,
            setup_lookback=setup_lookback,
        )
    )
    return df


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
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    trend_sma_length=50,
    setup_lookback=3,
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
            hurst_filter_mode=hurst_filter_mode,
            hurst_threshold=hurst_threshold,
            rsi_length=rsi_length,
            rsi_entry_threshold=rsi_entry_threshold,
            bollinger_length=bollinger_length,
            bollinger_std=bollinger_std,
            trend_sma_length=trend_sma_length,
            setup_lookback=setup_lookback,
        )
    df.attrs.update(
        _strategy_cache_config(
            hurst_filter_mode,
            hurst_threshold,
            use_market_calendar=use_market_calendar,
            include_partial_session_bars=include_partial_session_bars,
            rsi_length=rsi_length,
            rsi_entry_threshold=rsi_entry_threshold,
            bollinger_length=bollinger_length,
            bollinger_std=bollinger_std,
            trend_sma_length=trend_sma_length,
            setup_lookback=setup_lookback,
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
    def __init__(self, df, verbose=True):
        self.df = df
        self.verbose = verbose
        self.komisyon_oran = 0.001
        self.slippage_mu = 0.05
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


class PortfolioManager:
    def __init__(
        self,
        asset_data,
        initial_cash=10000.0,
        max_open_positions=5,
        risk_per_trade=0.02,
        ranking_mode="momentum",
        evaluation_start=None,
        evaluation_end=None,
        execution_mode="bar_close",
        intraday_data=None,
        verbose=True,
    ):
        self.asset_data = asset_data
        self.intraday_data = intraday_data or {}
        self.initial_cash = float(initial_cash)
        self.max_open_positions = int(max_open_positions)
        self.risk_per_trade = float(risk_per_trade)
        self.ranking_mode = _normalize_ranking_mode(ranking_mode)
        self.execution_mode = _normalize_execution_mode(execution_mode)
        self.evaluation_start = evaluation_start
        self.evaluation_end = evaluation_end
        self.verbose = verbose
        self.komisyon_oran = 0.001
        self.slippage_mu = 0.05
        self.positions = {}
        self.trade_log = []
        self.equity_curve = []
        self.rejected_signals = 0
        self.macro_rejected_signals = 0
        self.max_open_observed = 0
        self.max_heat_observed = 0.0
        if self.execution_mode == "intrabar_1m":
            missing_intraday = [
                symbol for symbol in self.asset_data if symbol not in self.intraday_data
            ]
            if missing_intraday:
                raise ValueError(
                    "execution_mode='intrabar_1m' icin 1m intraday veri eksik: "
                    + ", ".join(missing_intraday[:10])
                )
        self.market_data = self._build_common_market_data()
        self.market_regime = self._build_market_regime()
        self.evaluation_start = _align_timestamp_to_index(
            self.evaluation_start,
            self.market_data.index,
        )
        self.evaluation_end = _align_timestamp_to_index(
            self.evaluation_end,
            self.market_data.index,
        )

    def _build_common_market_data(self):
        prepared = {}
        required = PORTFOLIO_REQUIRED_COLUMNS

        for symbol, data in self.asset_data.items():
            missing = [column for column in required if column not in data]
            if missing:
                raise ValueError(
                    f"{symbol} eksik portfoy kolonlari: {', '.join(missing)}"
                )

            frame = data[required].copy()
            frame["ENTRY_ORDER"] = frame["ENTRY_SIGNAL"].shift(1).fillna(False)
            frame["EXIT_ORDER"] = frame["EXIT_SIGNAL"].shift(1).fillna(False)
            frame["Signal_Time"] = pd.Series(frame.index, index=frame.index).shift(1)
            frame["Signal_SMA20_Reversion_Exit"] = (
                frame["SMA20_Reversion_Exit"].shift(1).fillna(False)
            )
            frame["Signal_Hurst"] = frame["Hurst_Exponent"].shift(1)
            frame["Signal_Momentum"] = frame["Momentum_Factor"].shift(1)
            frame["Signal_Stop"] = frame["Disaster_Stop"].shift(1)
            frame["Signal_ATR"] = frame["SuperTrend_ATR_22"].shift(1)
            frame["Signal_Take_Profit_Distance"] = (
                frame["ATR_Take_Profit_Distance"].shift(1)
            )
            frame["Signal_Close"] = frame["Close"].shift(1)
            prepared[symbol] = frame[
                [
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "ENTRY_ORDER",
                    "EXIT_ORDER",
                    "Signal_Time",
                    "Signal_SMA20_Reversion_Exit",
                    "Signal_Hurst",
                    "Signal_Momentum",
                    "Signal_Stop",
                    "Signal_ATR",
                    "Signal_Take_Profit_Distance",
                    "Signal_Close",
                    "SuperTrend_ATR_22",
                ]
            ]

        if not prepared:
            return pd.DataFrame()
        return pd.concat(prepared, axis=1, join="outer").sort_index()

    def _build_market_regime(self):
        regime_frames = []
        for symbol, data in self.asset_data.items():
            required = {"Risk_Off", "Regime_Ready"}
            if required.issubset(data.columns):
                regime_frames.append(
                    data[["Risk_Off", "Regime_Ready"]].rename(
                        columns=lambda column: f"{symbol}_{column}"
                    )
                )

        if not regime_frames:
            raise ValueError(
                "Makro rejim verisi bulunamadi. Varliklari benchmark_df ile "
                "build_mega_dataframe uzerinden hazirlayin."
            )

        combined = pd.concat(regime_frames, axis=1, join="outer").sort_index()
        risk_columns = [
            column for column in combined if column.endswith("_Risk_Off")
        ]
        ready_columns = [
            column for column in combined if column.endswith("_Regime_Ready")
        ]
        regime = pd.DataFrame(index=self.market_data.index)
        regime["Risk_Off"] = (
            combined[risk_columns].bfill(axis=1).iloc[:, 0]
        ).reindex(regime.index)
        regime["Regime_Ready"] = (
            combined[ready_columns].bfill(axis=1).iloc[:, 0]
        ).reindex(regime.index)
        return regime

    def _is_risk_off(self, timestamp):
        if timestamp not in self.market_regime.index:
            return True
        ready = self.market_regime.at[timestamp, "Regime_Ready"]
        risk_off = self.market_regime.at[timestamp, "Risk_Off"]
        if pd.isna(ready) or not bool(ready) or pd.isna(risk_off):
            return True
        return bool(risk_off)

    def _in_evaluation_window(self, timestamp):
        if self.evaluation_start is not None and timestamp < self.evaluation_start:
            return False
        if self.evaluation_end is not None and timestamp > self.evaluation_end:
            return False
        return True

    def _calculate_slippage_amount(self, raw_price, atr_value):
        raw_price = self._finite_or_nan(raw_price)
        atr_value = self._finite_or_nan(atr_value)
        if pd.notna(atr_value) and atr_value > 0:
            return atr_value * self.slippage_mu
        if pd.notna(raw_price) and raw_price > 0:
            return raw_price * 0.0005
        return np.nan

    def _value_at(self, timestamp, symbol, column):
        key = (symbol, column)
        if key not in self.market_data.columns:
            return np.nan
        return self.market_data.at[timestamp, key]

    def _finite_or_nan(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return np.nan
        return value if np.isfinite(value) else np.nan

    def _portfolio_equity(self, last_prices):
        holdings_value = sum(
            position["shares"] * last_prices.get(symbol, position["entry_price"])
            for symbol, position in self.positions.items()
        )
        return self.current_cash + holdings_value

    def _update_trade_excursions(self, symbol, timestamp):
        position = self.positions[symbol]
        high_price = self._finite_or_nan(self._value_at(timestamp, symbol, "High"))
        low_price = self._finite_or_nan(self._value_at(timestamp, symbol, "Low"))
        if pd.notna(high_price):
            favorable = (high_price - position["entry_price"]) * position["shares"]
            position["mfe"] = max(position["mfe"], favorable)
        if pd.notna(low_price):
            adverse = (low_price - position["entry_price"]) * position["shares"]
            position["mae"] = min(position["mae"], adverse)

    def _get_intrabar_window(self, symbol, entry_time, next_bar_time):
        if symbol not in self.intraday_data:
            raise ValueError(f"{symbol} icin 1m intraday veri bulunamadi.")
        intraday = self.intraday_data[symbol]
        if intraday.empty:
            return intraday

        start = _align_timestamp_to_index(entry_time, intraday.index)
        end = _align_timestamp_to_index(next_bar_time, intraday.index)
        return intraday.loc[(intraday.index > start) & (intraday.index <= end)]

    def _check_intrabar_exit(self, symbol, position, window):
        if window.empty:
            return None
        for timestamp, row in window.iterrows():
            high_price = self._finite_or_nan(row.get("High"))
            low_price = self._finite_or_nan(row.get("Low"))
            if pd.isna(high_price) or pd.isna(low_price):
                continue

            favorable = (high_price - position["entry_price"]) * position["shares"]
            adverse = (low_price - position["entry_price"]) * position["shares"]
            position["mfe"] = max(position["mfe"], favorable)
            position["mae"] = min(position["mae"], adverse)

            # Conservative long-path assumption: if stop and target are inside
            # the same 1m candle, the stop is considered first.
            if low_price <= position["stop"]:
                return {
                    "timestamp": timestamp,
                    "raw_exit_price": position["stop"],
                    "exit_reason": "disaster_stop",
                }
            if high_price >= position["take_profit_target"]:
                return {
                    "timestamp": timestamp,
                    "raw_exit_price": position["take_profit_target"],
                    "exit_reason": "take_profit",
                }
        return None

    def _process_intrabar_exits(self, bar_timestamp):
        if self.execution_mode != "intrabar_1m":
            return set()

        exited_symbols = set()
        for symbol in list(self.positions):
            position = self.positions[symbol]
            start_time = position.get("last_intrabar_check_time")
            if start_time is None:
                start_time = position["entry_signal_time"]
            window = self._get_intrabar_window(symbol, start_time, bar_timestamp)
            intrabar_exit = self._check_intrabar_exit(symbol, position, window)
            if intrabar_exit is None:
                position["last_intrabar_check_time"] = bar_timestamp
                continue

            atr_value = self._value_at(bar_timestamp, symbol, "Signal_ATR")
            self._close_position(
                symbol,
                intrabar_exit["raw_exit_price"],
                atr_value,
                intrabar_exit["timestamp"],
                intrabar_exit["exit_reason"],
                intrabar_exit["timestamp"],
            )
            exited_symbols.add(symbol)
        return exited_symbols

    def _close_position(
        self,
        symbol,
        raw_exit_price,
        atr_value,
        timestamp,
        exit_reason,
        exit_signal_time,
    ):
        position = self.positions.pop(symbol)
        slippage = self._calculate_slippage_amount(raw_exit_price, atr_value)
        exit_price = max(float(raw_exit_price) - slippage, 0.0)
        proceeds = exit_price * position["shares"]
        exit_commission = proceeds * self.komisyon_oran
        self.current_cash += proceeds - exit_commission

        gross_pnl = proceeds - position["entry_notional"]
        net_pnl = (
            gross_pnl - position["entry_commission"] - exit_commission
        )
        initial_risk = position["initial_risk"]
        r_multiple = net_pnl / initial_risk if initial_risk > 0 else np.nan
        risk_budget = position["portfolio_equity_at_entry"] * self.risk_per_trade
        risk_budget_used = initial_risk / risk_budget if risk_budget > 0 else np.nan
        mfe_r = position["mfe"] / initial_risk if initial_risk > 0 else np.nan
        mae_r = position["mae"] / initial_risk if initial_risk > 0 else np.nan
        self.trade_log.append(
            {
                "Symbol": symbol,
                "Execution Mode": self.execution_mode,
                "Entry Time": position["entry_time"],
                "Exit Time": timestamp,
                "Exit Reason": exit_reason,
                "Entry Signal Time": position["entry_signal_time"],
                "Entry Execution Time": position["entry_time"],
                "Entry Signal Close": position["entry_signal_close"],
                "Raw Entry Open": position["raw_entry_open"],
                "Entry Price": position["entry_price"],
                "Entry Slippage": position["entry_slippage"],
                "Entry Commission": position["entry_commission"],
                "Entry Notional": position["entry_notional"],
                "Exit Signal Time": exit_signal_time,
                "Exit Execution Time": timestamp,
                "Raw Exit Price": raw_exit_price,
                "Exit Price": exit_price,
                "Exit Slippage": slippage,
                "Exit Commission": exit_commission,
                "Shares": position["shares"],
                "Gross PnL": gross_pnl,
                "Initial Risk": position["initial_risk"],
                "Net PnL": net_pnl,
                "Initial Risk Per Share": position["initial_risk_per_share"],
                "R Multiple": r_multiple,
                "Bars Held": position["bars_held"],
                "Portfolio Equity At Entry": position["portfolio_equity_at_entry"],
                "Risk Budget Used": risk_budget_used,
                "MFE": position["mfe"],
                "MAE": position["mae"],
                "MFE_R": mfe_r,
                "MAE_R": mae_r,
                "Ranking Score": position["ranking_score"],
                "Hurst": position["hurst"],
                "Momentum Factor": position["momentum"],
            }
        )

    def _open_position(self, candidate, timestamp, portfolio_equity):
        symbol = candidate["symbol"]
        raw_entry = candidate["open"]
        signal_stop = candidate["stop"]
        atr_value = candidate["atr"]
        slippage = self._calculate_slippage_amount(raw_entry, atr_value)
        entry_price = raw_entry + slippage
        risk_per_share = entry_price - signal_stop
        if (
            not np.isfinite(entry_price)
            or not np.isfinite(signal_stop)
            or not np.isfinite(float(atr_value))
            or not np.isfinite(float(candidate["target_distance"]))
            or risk_per_share <= 0
        ):
            return False

        risk_budget = portfolio_equity * self.risk_per_trade
        risk_sized_shares = risk_budget / risk_per_share
        max_position_notional = portfolio_equity / self.max_open_positions
        allocated_cash = min(self.current_cash, max_position_notional)
        affordable_shares = allocated_cash / (
            entry_price * (1 + self.komisyon_oran)
        )
        shares = min(risk_sized_shares, affordable_shares)
        if not np.isfinite(shares) or shares <= 0:
            return False

        entry_notional = entry_price * shares
        entry_commission = entry_notional * self.komisyon_oran
        total_cost = entry_notional + entry_commission
        if total_cost > self.current_cash + 1e-9:
            return False

        self.current_cash -= total_cost
        self.positions[symbol] = {
            "entry_time": timestamp,
            "entry_signal_time": candidate["signal_time"],
            "entry_signal_close": candidate["signal_close"],
            "raw_entry_open": raw_entry,
            "entry_price": entry_price,
            "entry_slippage": slippage,
            "take_profit_target": entry_price + float(candidate["target_distance"]),
            "last_intrabar_check_time": candidate["signal_time"],
            "entry_notional": entry_notional,
            "entry_commission": entry_commission,
            "shares": shares,
            "stop": signal_stop,
            "bars_held": 0,
            "initial_risk_per_share": risk_per_share,
            "initial_risk": risk_per_share * shares,
            "portfolio_equity_at_entry": portfolio_equity,
            "mfe": 0.0,
            "mae": 0.0,
            "ranking_score": candidate["ranking_score"],
            "hurst": candidate["hurst"],
            "momentum": candidate["momentum"],
        }
        return True

    def run_backtest(self):
        if self.market_data.empty:
            return {}

        self.current_cash = self.initial_cash
        self.positions = {}
        self.trade_log = []
        self.equity_curve = []
        self.rejected_signals = 0
        self.macro_rejected_signals = 0
        self.max_open_observed = 0
        self.max_heat_observed = 0.0
        last_prices = {}

        symbols = sorted(self.asset_data)
        for timestamp in self.market_data.index:
            in_evaluation_window = self._in_evaluation_window(timestamp)
            for symbol in symbols:
                close_price = self._value_at(timestamp, symbol, "Close")
                if pd.notna(close_price):
                    last_prices[symbol] = float(close_price)

            exited_symbols = set()
            for symbol in list(self.positions):
                exit_order = self._value_at(timestamp, symbol, "EXIT_ORDER")
                open_price = self._value_at(timestamp, symbol, "Open")
                signal_close = self._value_at(timestamp, symbol, "Signal_Close")
                signal_time = self._value_at(timestamp, symbol, "Signal_Time")
                signal_sma20_reversion = self._value_at(
                    timestamp,
                    symbol,
                    "Signal_SMA20_Reversion_Exit",
                )
                position = self.positions[symbol]
                take_profit_hit = (
                    pd.notna(signal_close)
                    and float(signal_close) >= position["take_profit_target"]
                )
                sma20_reversion_hit = (
                    (pd.notna(exit_order) and bool(exit_order))
                    or (
                        pd.notna(signal_sma20_reversion)
                        and bool(signal_sma20_reversion)
                    )
                )
                disaster_stop_hit = (
                    pd.notna(signal_close)
                    and float(signal_close) < position["stop"]
                )
                time_stop_hit = position["bars_held"] > TIME_STOP_BARS
                if (
                    (
                        disaster_stop_hit
                        or take_profit_hit
                        or sma20_reversion_hit
                        or time_stop_hit
                    )
                    and pd.notna(open_price)
                ):
                    if disaster_stop_hit:
                        exit_reason = "disaster_stop"
                    elif take_profit_hit:
                        exit_reason = "take_profit"
                    elif sma20_reversion_hit:
                        exit_reason = "sma20_reversion"
                    else:
                        exit_reason = "time_stop"
                    atr_value = self._value_at(timestamp, symbol, "Signal_ATR")
                    self._close_position(
                        symbol,
                        float(open_price),
                        atr_value,
                        timestamp,
                        exit_reason,
                        signal_time,
                    )
                    exited_symbols.add(symbol)

            available_slots = self.max_open_positions - len(self.positions)
            candidates = []
            if in_evaluation_window:
                for symbol in symbols:
                    if symbol in self.positions or symbol in exited_symbols:
                        continue

                    entry_order = self._value_at(timestamp, symbol, "ENTRY_ORDER")
                    open_price = self._value_at(timestamp, symbol, "Open")
                    signal_time = self._value_at(timestamp, symbol, "Signal_Time")
                    signal_close = self._value_at(timestamp, symbol, "Signal_Close")
                    hurst = self._value_at(timestamp, symbol, "Signal_Hurst")
                    momentum = self._value_at(
                        timestamp,
                        symbol,
                        "Signal_Momentum",
                    )
                    stop = self._value_at(timestamp, symbol, "Signal_Stop")
                    atr_value = self._value_at(timestamp, symbol, "Signal_ATR")
                    target_distance = self._value_at(
                        timestamp,
                        symbol,
                        "Signal_Take_Profit_Distance",
                    )
                    hurst_value = self._finite_or_nan(hurst)
                    momentum_value = self._finite_or_nan(momentum)
                    if self.ranking_mode == "momentum":
                        ranking_score = momentum_value
                    elif self.ranking_mode == "hurst":
                        ranking_score = hurst_value
                    else:
                        ranking_score = np.nan
                    ranking_score_ok = (
                        self.ranking_mode == "none"
                        or pd.notna(ranking_score)
                    )
                    if (
                        pd.notna(entry_order)
                        and bool(entry_order)
                        and pd.notna(open_price)
                        and pd.notna(signal_close)
                        and ranking_score_ok
                        and pd.notna(stop)
                        and pd.notna(atr_value)
                        and pd.notna(target_distance)
                    ):
                        candidates.append(
                            {
                                "symbol": symbol,
                                "open": float(open_price),
                                "signal_time": signal_time,
                                "signal_close": float(signal_close),
                                "hurst": hurst_value,
                                "momentum": momentum_value,
                                "ranking_score": ranking_score,
                                "stop": float(stop),
                                "atr": atr_value,
                                "target_distance": float(target_distance),
                            }
                        )

            if self._is_risk_off(timestamp):
                self.macro_rejected_signals += len(candidates)
                candidates = []

            if self.ranking_mode == "none":
                candidates.sort(key=lambda item: item["symbol"])
            else:
                candidates.sort(
                    key=lambda item: item["ranking_score"],
                    reverse=True,
                )
            if len(candidates) > available_slots:
                self.rejected_signals += len(candidates) - max(available_slots, 0)

            portfolio_equity = self._portfolio_equity(last_prices)
            for candidate in candidates[:max(available_slots, 0)]:
                self._open_position(candidate, timestamp, portfolio_equity)

            self._process_intrabar_exits(timestamp)

            if self.execution_mode == "bar_close":
                for symbol in list(self.positions):
                    self._update_trade_excursions(symbol, timestamp)

            for position in self.positions.values():
                position["bars_held"] += 1

            self.max_open_observed = max(
                self.max_open_observed,
                len(self.positions),
            )
            current_equity = self._portfolio_equity(last_prices)
            current_heat = (
                sum(position["initial_risk"] for position in self.positions.values())
                / current_equity
                if current_equity > 0
                else 0.0
            )
            self.max_heat_observed = max(self.max_heat_observed, current_heat)
            if in_evaluation_window:
                self.equity_curve.append((timestamp, current_equity))

        final_timestamp = self.market_data.index[-1]
        for symbol in list(self.positions):
            raw_exit_price = last_prices.get(symbol)
            if raw_exit_price is None:
                continue
            atr_value = self._value_at(
                final_timestamp,
                symbol,
                "SuperTrend_ATR_22",
            )
            self._close_position(
                symbol,
                raw_exit_price,
                atr_value,
                final_timestamp,
                "forced_final_exit",
                final_timestamp,
            )

        if self.equity_curve:
            self.equity_curve[-1] = (final_timestamp, self.current_cash)
        return self.get_metrics()

    def get_metrics(self):
        if not self.equity_curve:
            return {}

        equity = pd.Series(
            [value for _, value in self.equity_curve],
            index=[timestamp for timestamp, _ in self.equity_curve],
            dtype=float,
        )
        total_return = (equity.iloc[-1] / self.initial_cash - 1) * 100
        max_drawdown = ((equity - equity.cummax()) / equity.cummax()).min() * 100
        trades = pd.DataFrame(self.trade_log)
        pnl = (
            pd.to_numeric(trades["Net PnL"], errors="coerce").dropna()
            if not trades.empty and "Net PnL" in trades
            else pd.Series(dtype=float)
        )
        winners = pnl[pnl > 0]
        losers = pnl[pnl < 0]
        trade_count = len(pnl)
        win_rate = len(winners) / trade_count * 100 if trade_count else 0.0
        profit_factor = (
            winners.sum() / abs(losers.sum())
            if len(losers) and abs(losers.sum()) > 0
            else float("inf") if trade_count else 0.0
        )
        r_multiples = (
            pd.to_numeric(trades["R Multiple"], errors="coerce").dropna()
            if not trades.empty and "R Multiple" in trades
            else pd.Series(dtype=float)
        )
        exit_reasons = (
            trades["Exit Reason"]
            if not trades.empty and "Exit Reason" in trades
            else pd.Series(dtype=object)
        )
        exit_reason_counts = exit_reasons.value_counts()
        winning_r = r_multiples[r_multiples > 0]
        losing_r = r_multiples[r_multiples < 0]
        avg_r = r_multiples.mean() if len(r_multiples) else 0.0
        median_r = r_multiples.median() if len(r_multiples) else 0.0
        best_r = r_multiples.max() if len(r_multiples) else 0.0
        worst_r = r_multiples.min() if len(r_multiples) else 0.0
        avg_winner_r = winning_r.mean() if len(winning_r) else 0.0
        avg_loser_r = losing_r.mean() if len(losing_r) else 0.0
        expectancy_r = avg_r
        max_consecutive_losses = 0
        max_consecutive_wins = 0
        current_losses = 0
        current_wins = 0
        for value in pnl:
            if value < 0:
                current_losses += 1
                current_wins = 0
            elif value > 0:
                current_wins += 1
                current_losses = 0
            else:
                current_losses = 0
                current_wins = 0
            max_consecutive_losses = max(max_consecutive_losses, current_losses)
            max_consecutive_wins = max(max_consecutive_wins, current_wins)

        daily_returns = equity.resample("1D").last().pct_change().dropna()
        volatility = daily_returns.std()
        sharpe = (
            daily_returns.mean() / volatility * np.sqrt(252)
            if pd.notna(volatility) and volatility > 0
            else 0.0
        )
        days = max((equity.index[-1] - equity.index[0]).days, 1)
        years = days / 365.25
        cagr = (equity.iloc[-1] / self.initial_cash) ** (1 / years) - 1

        return {
            "Baslangic Kasa": round(self.initial_cash, 2),
            "Bitis Kasa": round(self.current_cash, 2),
            "Ranking Mode": self.ranking_mode,
            "Execution Mode": self.execution_mode,
            "Net Getiri (%)": round(total_return, 2),
            "CAGR (%)": round(cagr * 100, 2),
            "Max Drawdown (%)": round(max_drawdown, 2),
            "Sharpe Ratio": round(sharpe, 2),
            "Islem Sayisi": trade_count,
            "Win Rate (%)": round(win_rate, 1),
            "Profit Factor": round(profit_factor, 2),
            "Avg R": round(avg_r, 4),
            "Median R": round(median_r, 4),
            "Best R": round(best_r, 4),
            "Worst R": round(worst_r, 4),
            "Avg Winner R": round(avg_winner_r, 4),
            "Avg Loser R": round(avg_loser_r, 4),
            "Expectancy R": round(expectancy_r, 4),
            "Max Consecutive Losses": max_consecutive_losses,
            "Max Consecutive Wins": max_consecutive_wins,
            "Stop Exit Count": int(exit_reason_counts.get("disaster_stop", 0)),
            "Take Profit Exit Count": int(exit_reason_counts.get("take_profit", 0)),
            "SMA20 Reversion Exit Count": int(
                exit_reason_counts.get("sma20_reversion", 0)
            ),
            "Time Stop Exit Count": int(exit_reason_counts.get("time_stop", 0)),
            "Forced Final Exit Count": int(
                exit_reason_counts.get("forced_final_exit", 0)
            ),
            "Max Acik Pozisyon": self.max_open_observed,
            "Max Portfolio Heat (%)": round(self.max_heat_observed * 100, 2),
            "Reddedilen Sinyal": self.rejected_signals,
            "Makro Rejimde Reddedilen Sinyal": self.macro_rejected_signals,
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


def _select_universe_files(csv_files, allowed_symbol_set):
    selected_files = []
    excluded_rows = []
    seen_selected_symbols = set()
    for file_path in sorted(csv_files, key=_universe_file_sort_key):
        symbol = _symbol_from_path(file_path)
        excluded_reason = None
        if allowed_symbol_set is not None and symbol not in allowed_symbol_set:
            excluded_reason = "not_in_allowed_universe"
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
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    trend_sma_length=50,
    setup_lookback=3,
):
    stat = Path(file_path).stat()
    config = "|".join(
        [
            STRATEGY_CACHE_VERSION,
            _normalize_hurst_filter_mode(hurst_filter_mode),
            str(float(hurst_threshold)),
            str(bool(use_market_calendar)),
            str(bool(include_partial_session_bars)),
            str(PRIMARY_BAR_MINUTES),
            str(int(rsi_length)),
            str(float(rsi_entry_threshold)),
            str(int(bollinger_length)),
            str(float(bollinger_std)),
            str(int(trend_sma_length)),
            str(int(setup_lookback)),
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
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    trend_sma_length=50,
    setup_lookback=3,
):
    symbol = _symbol_from_path(file_path)
    config_hash = _cache_config_hash(
        file_path,
        hurst_filter_mode,
        hurst_threshold,
        use_market_calendar,
        include_partial_session_bars,
        rsi_length=rsi_length,
        rsi_entry_threshold=rsi_entry_threshold,
        bollinger_length=bollinger_length,
        bollinger_std=bollinger_std,
        trend_sma_length=trend_sma_length,
        setup_lookback=setup_lookback,
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


def calculate_buyhold_metrics(close_series, start, end):
    close = pd.to_numeric(close_series, errors="coerce").dropna().sort_index()
    if close.empty:
        raise ValueError("Benchmark close serisi bos.")

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    if close.index.tz is not None:
        if start.tzinfo is None:
            start = start.tz_localize(close.index.tz)
            end = end.tz_localize(close.index.tz)
        else:
            start = start.tz_convert(close.index.tz)
            end = end.tz_convert(close.index.tz)
    elif close.index.tz is None and start.tzinfo is not None:
        start = start.tz_convert(None)
        end = end.tz_convert(None)

    start_candidates = close.loc[:start]
    if start_candidates.empty:
        raise ValueError("Benchmark icin baslangic fiyatindan once veri yok.")
    start_price = float(start_candidates.iloc[-1])
    if not np.isfinite(start_price) or start_price <= 0:
        raise ValueError("Benchmark baslangic fiyati gecersiz.")

    path = close.loc[(close.index > start) & (close.index <= end)]
    path = pd.concat([pd.Series([start_price], index=[start]), path])
    path = path[~path.index.duplicated(keep="last")].sort_index().ffill().dropna()
    if len(path) < 2:
        raise ValueError("Benchmark icin test araliginda yeterli veri yok.")

    normalized = path / start_price
    total_return = (normalized.iloc[-1] - 1) * 100
    max_drawdown = ((normalized - normalized.cummax()) / normalized.cummax()).min() * 100
    days = max((normalized.index[-1] - normalized.index[0]).days, 1)
    years = days / 365.25
    cagr = (normalized.iloc[-1]) ** (1 / years) - 1
    return {
        "Return (%)": round(total_return, 2),
        "CAGR (%)": round(cagr * 100, 2),
        "MaxDD (%)": round(max_drawdown, 2),
    }


def calculate_equal_weight_benchmark(asset_data, start, end):
    # Survivorship bias: evren bugunun hisselerinden olusuyorsa bu buy-hold
    # benchmark gecmis performansi oldugundan iyi gosterebilir.
    normalized_series = []
    skipped_symbols = []
    for symbol, data in asset_data.items():
        if "Close" not in data:
            skipped_symbols.append(symbol)
            continue
        close = pd.to_numeric(data["Close"], errors="coerce").dropna().sort_index()
        start_candidates = close.loc[:start]
        if start_candidates.empty:
            skipped_symbols.append(symbol)
            continue
        start_price = float(start_candidates.iloc[-1])
        if not np.isfinite(start_price) or start_price <= 0:
            skipped_symbols.append(symbol)
            continue
        path = close.loc[(close.index > start) & (close.index <= end)]
        path = pd.concat([pd.Series([start_price], index=[start]), path])
        path = path[~path.index.duplicated(keep="last")].sort_index().ffill()
        normalized_series.append(path / start_price)

    if not normalized_series:
        raise ValueError("Equal-weight benchmark icin kullanilabilir close serisi yok.")
    if skipped_symbols:
        print(
            "Uyari: Equal-weight benchmark disinda kalan sembol sayisi: "
            f"{len(skipped_symbols)}"
        )

    normalized = pd.concat(normalized_series, axis=1).sort_index().ffill()
    normalized = normalized.loc[(normalized.index >= start) & (normalized.index <= end)]
    equal_weight_close = normalized.mean(axis=1).dropna()
    return calculate_buyhold_metrics(equal_weight_close, start, end)


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


def _prefixed_benchmark_metrics(prefix, metrics):
    return {
        f"{prefix} BuyHold Return (%)": metrics["Return (%)"],
        f"{prefix} BuyHold CAGR (%)": metrics["CAGR (%)"],
        f"{prefix} BuyHold MaxDD (%)": metrics["MaxDD (%)"],
    }


def _normalize_evaluation_mode(evaluation_mode):
    mode = str(evaluation_mode).lower()
    if mode not in {"single", "split", "walk_forward"}:
        raise ValueError(
            "evaluation_mode 'single', 'split' veya 'walk_forward' olmali."
        )
    return mode


def _align_timestamp_to_index(value, index):
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if getattr(index, "tz", None) is not None:
        if timestamp.tzinfo is None:
            return timestamp.tz_localize(index.tz)
        return timestamp.tz_convert(index.tz)
    if timestamp.tzinfo is not None:
        return timestamp.tz_convert(None)
    return timestamp


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
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    trend_sma_length=50,
    setup_lookback=3,
):
    if not isinstance(asset_data, dict) or not asset_data:
        return {"asset_data": ["dict"]}

    expected_config = _strategy_cache_config(
        hurst_filter_mode,
        hurst_threshold,
        use_market_calendar=use_market_calendar,
        include_partial_session_bars=include_partial_session_bars,
        rsi_length=rsi_length,
        rsi_entry_threshold=rsi_entry_threshold,
        bollinger_length=bollinger_length,
        bollinger_std=bollinger_std,
        trend_sma_length=trend_sma_length,
        setup_lookback=setup_lookback,
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
        final_signal = data["ENTRY_SIGNAL"].fillna(False).astype(bool)
        risk_off_rejected = hurst_signal & data["Risk_Off"].fillna(True).astype(bool)
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
                "Raw Giriş Sinyali Sayısı": int(raw_signal.sum()),
                "Hurst Sonrası Sinyal Sayısı": int(hurst_signal.sum()),
                "Final Giriş Sinyali Sayısı": int(final_signal.sum()),
                "Risk Off Reddedilen Sinyal Sayısı": int(
                    risk_off_rejected.sum()
                ),
                "Giris Sinyali Sayisi": int(final_signal.sum()),
            }
        )
    return results


def _resolve_asset_worker_count(max_workers):
    if max_workers is not None:
        return max(1, int(max_workers))
    cpu_count = os.cpu_count() or 1
    if cpu_count <= 1:
        return 1
    return max(1, min(cpu_count - 1, 8))


def _build_asset_worker(task):
    start = time.perf_counter()
    symbol = task["symbol"]
    worker_timing = _WorkerTimingCollector()
    try:
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
            rsi_length=task["rsi_length"],
            rsi_entry_threshold=task["rsi_entry_threshold"],
            bollinger_length=task["bollinger_length"],
            bollinger_std=task["bollinger_std"],
            trend_sma_length=task["trend_sma_length"],
            setup_lookback=task["setup_lookback"],
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
    quick_mode=None,
    write_trade_log=None,
    cache_mode=None,
    output_suffix="",
    output_subdir=None,
    clear_stale_trade_log=True,
    rsi_length=3,
    rsi_entry_threshold=10,
    bollinger_length=20,
    bollinger_std=2.0,
    trend_sma_length=50,
    setup_lookback=3,
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
    rsi_length = int(rsi_length)
    rsi_entry_threshold = float(rsi_entry_threshold)
    bollinger_length = int(bollinger_length)
    bollinger_std = float(bollinger_std)
    trend_sma_length = int(trend_sma_length)
    setup_lookback = int(setup_lookback)

    hurst_filter_mode = _normalize_hurst_filter_mode(hurst_filter_mode)
    hurst_threshold = float(hurst_threshold)
    ranking_mode = _normalize_ranking_mode(ranking_mode)
    execution_mode = _normalize_execution_mode(execution_mode)
    evaluation_mode = _normalize_evaluation_mode(evaluation_mode)
    monte_carlo = bool(monte_carlo)
    use_market_calendar = bool(use_market_calendar)
    include_partial_session_bars = bool(include_partial_session_bars)
    parallel_assets = bool(parallel_assets)
    quick_mode = bool(quick_mode)
    if write_trade_log is None:
        write_trade_log = not quick_mode
    write_trade_log = bool(write_trade_log)
    asset_worker_count = _resolve_asset_worker_count(max_workers)
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
            )
    if universe_filter_error is not None:
        print(f"Evren filtresi okunamadi: {universe_filter_error}")
        timing.finish()
        return
    selected_symbols = [_symbol_from_path(file_path) for file_path in selected_csv_files]
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
    print(f"  Parallel Assets: {parallel_assets}")
    print(f"  Max Workers: {asset_worker_count}")
    print(f"  Cache Mode: {cache_mode}")
    print(
        "  Entry Params: "
        f"RSI({rsi_length})<{rsi_entry_threshold}, "
        f"BB({bollinger_length},{bollinger_std}), "
        f"TrendSMA({trend_sma_length}), setup={setup_lookback}"
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
    load_results = []
    files_to_build = list(selected_csv_files)
    per_symbol_130m_hits = 0
    per_symbol_130m_misses = 0
    per_symbol_1m_hits = 0
    per_symbol_1m_misses = 0
    cache_load_seconds = 0.0
    cache_build_seconds = 0.0

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
                    rsi_length=rsi_length,
                    rsi_entry_threshold=rsi_entry_threshold,
                    bollinger_length=bollinger_length,
                    bollinger_std=bollinger_std,
                    trend_sma_length=trend_sma_length,
                    setup_lookback=setup_lookback,
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
                    rsi_length=rsi_length,
                    rsi_entry_threshold=rsi_entry_threshold,
                    bollinger_length=bollinger_length,
                    bollinger_std=bollinger_std,
                    trend_sma_length=trend_sma_length,
                    setup_lookback=setup_lookback,
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
                    rsi_length=rsi_length,
                    rsi_entry_threshold=rsi_entry_threshold,
                    bollinger_length=bollinger_length,
                    bollinger_std=bollinger_std,
                    trend_sma_length=trend_sma_length,
                    setup_lookback=setup_lookback,
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
                        rsi_length=rsi_length,
                        rsi_entry_threshold=rsi_entry_threshold,
                        bollinger_length=bollinger_length,
                        bollinger_std=bollinger_std,
                        trend_sma_length=trend_sma_length,
                        setup_lookback=setup_lookback,
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
                    "rsi_length": rsi_length,
                    "rsi_entry_threshold": rsi_entry_threshold,
                    "bollinger_length": bollinger_length,
                    "bollinger_std": bollinger_std,
                    "trend_sma_length": trend_sma_length,
                    "setup_lookback": setup_lookback,
                }
                for file_path in files_to_build
            ]
            worker_results = {}
            asset_errors = []
            with ProcessPoolExecutor(max_workers=asset_worker_count) as executor:
                futures = {
                    executor.submit(_build_asset_worker, task): task["symbol"]
                    for task in worker_tasks
                }
                for future in as_completed(futures):
                    future_symbol = futures[future]
                    try:
                        result = future.result()
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
                    timing.add(
                        row["Stage"],
                        seconds=row["Seconds"],
                        notes=f"{ticker}; {row.get('Notes', '')}",
                    )

            elapsed = time.perf_counter() - parallel_start
            successful_count = len(asset_data)
            failed_count = len(asset_errors)
            avg_seconds = (
                sum(result["seconds"] for result in worker_results.values())
                / max(len(worker_results), 1)
            )
            print(
                "Asset preparation tamamlandi: "
                f"basarili={len(worker_results) - failed_count}, hata={failed_count}, "
                f"sure={elapsed:.2f}s"
            )
            if asset_errors:
                print("Asset preparation hatalari:")
                for symbol, error in asset_errors[:20]:
                    print(f"   {symbol}: {error}")
                if len(asset_errors) > 20:
                    print(f"   ... {len(asset_errors) - 20} hata daha")
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
                            rsi_length=rsi_length,
                            rsi_entry_threshold=rsi_entry_threshold,
                            bollinger_length=bollinger_length,
                            bollinger_std=bollinger_std,
                            trend_sma_length=trend_sma_length,
                            setup_lookback=setup_lookback,
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

        cache_build_seconds += time.perf_counter() - build_start
        if asset_data and cache_mode == "global":
            cache_write_start = time.perf_counter()
            pd.to_pickle(asset_data, cache_path)
            print(f"Onbellek kaydedildi: {cache_path}")
            if execution_mode == "intrabar_1m" and intraday_1m_data:
                pd.to_pickle(intraday_1m_data, intraday_cache_path)
                print(f"Intraday onbellek kaydedildi: {intraday_cache_path}")
            cache_build_seconds += time.perf_counter() - cache_write_start
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
                    rsi_length=rsi_length,
                    rsi_entry_threshold=rsi_entry_threshold,
                    bollinger_length=bollinger_length,
                    bollinger_std=bollinger_std,
                    trend_sma_length=trend_sma_length,
                    setup_lookback=setup_lookback,
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
                        rsi_length=rsi_length,
                        rsi_entry_threshold=rsi_entry_threshold,
                        bollinger_length=bollinger_length,
                        bollinger_std=bollinger_std,
                        trend_sma_length=trend_sma_length,
                        setup_lookback=setup_lookback,
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

    if not asset_data:
        print("Portfoy simulasyonu icin kullanilabilir varlik bulunamadi.")
        timing.finish()
        return

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
                        rsi_length=rsi_length,
                        rsi_entry_threshold=rsi_entry_threshold,
                        bollinger_length=bollinger_length,
                        bollinger_std=bollinger_std,
                        trend_sma_length=trend_sma_length,
                        setup_lookback=setup_lookback,
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
                                rsi_length=rsi_length,
                                rsi_entry_threshold=rsi_entry_threshold,
                                bollinger_length=bollinger_length,
                                bollinger_std=bollinger_std,
                                trend_sma_length=trend_sma_length,
                                setup_lookback=setup_lookback,
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
    for result in load_results:
        result.update(universe_summary)
    calendar_summary = _calendar_report_summary(asset_data)

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
        return pd.DataFrame(report_rows)

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
        return pd.DataFrame(fold_rows)

    print(
        f"\n{len(asset_data)} varlik ortak zaman ekseninde birlestiriliyor..."
    )
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
        )
    with timing.stage("PortfolioManager run_backtest", "single"):
        metrics = manager.run_backtest()
    if not metrics:
        print("Portfoy sonucu uretilemedi.")
        timing.finish()
        return
    risk_off_rejected_total = _risk_off_rejected_total(load_results)
    metrics["Makro Rejimde Reddedilen Sinyal"] = risk_off_rejected_total
    metrics.update(calendar_summary)

    strategy_equity = pd.Series(
        [value for _, value in manager.equity_curve],
        index=[timestamp for timestamp, _ in manager.equity_curve],
        dtype=float,
    )
    benchmark_start = strategy_equity.index[0]
    benchmark_end = strategy_equity.index[-1]
    benchmark_summary = {}

    if quick_mode:
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
    else:
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
    return metrics


EXPERIMENT_METRIC_COLUMNS = [
    "Net Getiri (%)",
    "CAGR (%)",
    "Max Drawdown (%)",
    "Sharpe Ratio",
    "Islem Sayisi",
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
    "Forced Final Exit Count",
    "Max Portfolio Heat (%)",
    "Reddedilen Sinyal",
    "Makro Rejimde Reddedilen Sinyal",
]

EXPERIMENT_PARAM_COLUMNS = [
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
    "trend_sma_length",
    "setup_lookback",
    "allowed_symbols_count",
    "allowed_symbols",
]

COMPACT_SCORE_COLUMNS = [
    "Return/DD Score",
    "R Quality Score",
    "Penalty Score",
    "Compact Score",
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


def _numeric_series(df, column):
    return pd.to_numeric(df[column], errors="coerce")


def _add_compact_scores(report):
    net_return = _numeric_series(report, "Net Getiri (%)")
    max_dd = _numeric_series(report, "Max Drawdown (%)")
    expectancy = _numeric_series(report, "Expectancy R")
    avg_winner = _numeric_series(report, "Avg Winner R")
    avg_loser = _numeric_series(report, "Avg Loser R")
    worst_r = _numeric_series(report, "Worst R")
    max_losses = _numeric_series(report, "Max Consecutive Losses")
    trade_count = _numeric_series(report, "Islem Sayisi")

    dd_abs = max_dd.abs()
    report["Return/DD Score"] = net_return / dd_abs.replace(0, np.nan)
    report["R Quality Score"] = expectancy + avg_winner + avg_loser
    report["Penalty Score"] = worst_r.abs() + max_losses * 0.1
    report["Compact Score"] = (
        expectancy
        - dd_abs / 100
        + trade_count.clip(upper=100) / 1000
        - worst_r.abs() / 10
    )
    for column in COMPACT_SCORE_COLUMNS:
        report[column] = report[column].replace([np.inf, -np.inf], np.nan)
    return report


def _write_compact_markdown(report, markdown_path, allowed_symbols, generated_at):
    allowed_symbols = allowed_symbols or []
    symbol_text = ", ".join(map(str, allowed_symbols)) if allowed_symbols else "All / file filter"
    ranked = report.sort_values("Compact Score", ascending=False, na_position="last")
    display_columns = [
        "Experiment Name",
        "Compact Score",
        "Expectancy R",
        "Net Getiri (%)",
        "Max Drawdown (%)",
        "Islem Sayisi",
        "Profit Factor",
        "Worst R",
    ]
    best = ranked.head(5)[display_columns]
    worst = ranked.tail(5)[display_columns]
    def _markdown_table(df):
        try:
            return df.to_markdown(index=False)
        except Exception:
            return df.to_string(index=False)
    warnings = []
    if (_numeric_series(report, "Islem Sayisi") < 30).any():
        warnings.append("- Islem sayisi 30'dan az olan experimentler guvenilir degil.")
    if (_numeric_series(report, "Worst R") < -2).any():
        warnings.append("- Worst R < -2 olan experimentler riskli.")
    if (_numeric_series(report, "Profit Factor") < 1).any():
        warnings.append("- Profit Factor < 1 olan experimentler negatif edge olabilir.")
    if (_numeric_series(report, "Expectancy R") <= 0).any():
        warnings.append("- Expectancy R <= 0 olan experimentler zayif.")
    if not warnings:
        warnings.append("- Otomatik uyari kriterlerine takilan experiment yok.")

    content = [
        "# Strategy Compact Report",
        "",
        f"- Run tarihi/saat: {generated_at}",
        f"- Kullanilan sembol sayisi: {len(allowed_symbols) if allowed_symbols else 'All'}",
        f"- Kullanilan semboller: {symbol_text}",
        f"- Toplam experiment sayisi: {len(report)}",
        "",
        "## En iyi 5 experiment",
        "",
        _markdown_table(best),
        "",
        "## En kotu 5 experiment",
        "",
        _markdown_table(worst),
        "",
        "## Uyarilar",
        "",
        *warnings,
        "",
        "## ChatGPT'ye gonderilecek dosya onerisi",
        "",
        "- strategy_compact_report.csv",
        "- strategy_compact_report.md",
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def run_strategy_experiments(
    data_folder="hisseler",
    allowed_symbols=None,
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
):
    if experiments is None:
        experiments = _default_strategy_experiments()
    if not isinstance(experiments, list):
        raise ValueError("experiments liste olmali.")

    output_dir = Path(data_folder).resolve().parent
    output_path = output_dir / output_name
    compact_csv_path = output_dir / compact_report_name
    compact_markdown_path = output_dir / compact_markdown_name
    latest_root = output_dir / "experiment_reports" / "_latest"
    tmp_root = output_dir / "experiment_reports" / "_tmp_compact"
    if not keep_latest_experiment_files and latest_root.exists():
        shutil.rmtree(latest_root, ignore_errors=True)
    if tmp_root.exists():
        shutil.rmtree(tmp_root, ignore_errors=True)
    rows = []
    for index, experiment in enumerate(experiments, 1):
        if not isinstance(experiment, dict):
            raise ValueError("Her experiment dict olmali.")
        experiment_name = experiment.get("name", f"experiment_{index}")
        print(f"\n[{index}/{len(experiments)}] Experiment calisiyor: {experiment_name}")

        scanner_kwargs = dict(experiment)
        scanner_kwargs.pop("name", None)
        safe_name = _safe_output_name(experiment_name)
        if write_experiment_detail_files:
            output_subdir = (
                f"experiment_reports/{safe_name}"
                if save_individual_reports
                else f"experiment_reports/_latest/{safe_name}"
            )
        else:
            output_subdir = f"experiment_reports/_tmp_compact/{safe_name}"
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
        params = {
            "hurst_filter_mode": "off",
            "hurst_threshold": 0.5,
            "ranking_mode": "momentum",
            "execution_mode": "bar_close",
            "run_profile": run_profile,
            "quick_mode": quick_mode,
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_length": 20,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
            "setup_lookback": 3,
            "allowed_symbols_count": len(allowed_symbols) if allowed_symbols else np.nan,
            "allowed_symbols": ",".join(allowed_symbols) if allowed_symbols else "",
            **scanner_kwargs,
        }
        rows.append(_experiment_metrics_row(experiment_name, metrics, params))
        if not write_experiment_detail_files:
            shutil.rmtree(output_dir / output_subdir, ignore_errors=True)

    report = pd.DataFrame(
        rows,
        columns=(
            ["Experiment Name"]
            + EXPERIMENT_PARAM_COLUMNS
            + EXPERIMENT_METRIC_COLUMNS
        ),
    )
    report = _add_compact_scores(report)
    report = report.sort_values("Compact Score", ascending=False, na_position="last")
    report.to_csv(output_path, index=False)
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

    print(f"\nStrategy experiment raporu: {output_path}")
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
            "name": "base_exit",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_length": 20,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
            "exit_mode": "sma_or_target",
            "take_profit_atr_multiplier": 2.0,
            "disaster_stop_atr_multiplier": 2.0,
            "time_stop_bars": 10,
        },
        {
            "name": "target3_stop2",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_length": 20,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
            "exit_mode": "sma_or_target",
            "take_profit_atr_multiplier": 3.0,
            "disaster_stop_atr_multiplier": 2.0,
            "time_stop_bars": 10,
        },
        {
            "name": "target2_stop15",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_length": 20,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
            "exit_mode": "sma_or_target",
            "take_profit_atr_multiplier": 2.0,
            "disaster_stop_atr_multiplier": 1.5,
            "time_stop_bars": 10,
        },
        {
            "name": "target25_stop15",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_length": 20,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
            "exit_mode": "sma_or_target",
            "take_profit_atr_multiplier": 2.5,
            "disaster_stop_atr_multiplier": 1.5,
            "time_stop_bars": 10,
        },
        {
            "name": "sma_only_stop15",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_length": 20,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
            "exit_mode": "sma_only",
            "disaster_stop_atr_multiplier": 1.5,
            "time_stop_bars": 10,
        },
        {
            "name": "target_only_25_stop15",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_length": 20,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
            "exit_mode": "target_only",
            "take_profit_atr_multiplier": 2.5,
            "disaster_stop_atr_multiplier": 1.5,
            "time_stop_bars": 10,
        },
        {
            "name": "time5_stop15",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_length": 20,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
            "exit_mode": "sma_or_target",
            "take_profit_atr_multiplier": 2.0,
            "disaster_stop_atr_multiplier": 1.5,
            "time_stop_bars": 5,
        },
        {
            "name": "time15_stop2",
            "hurst_filter_mode": "off",
            "rsi_length": 3,
            "rsi_entry_threshold": 10,
            "bollinger_length": 20,
            "bollinger_std": 2.0,
            "trend_sma_length": 50,
            "exit_mode": "sma_or_target",
            "take_profit_atr_multiplier": 2.0,
            "disaster_stop_atr_multiplier": 2.0,
            "time_stop_bars": 15,
        },
    ]
    
    run_strategy_experiments(
        "hisseler",
        allowed_symbols=[
            "AAPL", "MSFT", "NVDA", "AMD", "META",
            "GOOGL", "AMZN", "TSLA", "NFLX", "AVGO",
            "QCOM", "ADBE", "CRM", "INTC", "MU",
            "ORCL", "IBM", "SHOP", "SNOW", "UBER"
        ],
        experiments=experiments,
        run_profile="fast",
        quick_mode=True,
        compact_report=True,
        write_experiment_detail_files=False,
)
