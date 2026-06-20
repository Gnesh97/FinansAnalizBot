import math
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import pandas_market_calendars as mcal
except ImportError:
    mcal = None

try:
    from .config import DEFAULT_STRATEGY_NAME
    from .indicators import add_market_regime
    from .strategies.base import generate_strategy_signals
    from .strategies.rotation_utils import _strategy_cache_config
except ImportError:
    from config import DEFAULT_STRATEGY_NAME
    from indicators import add_market_regime
    from strategies.base import generate_strategy_signals
    from strategies.rotation_utils import _strategy_cache_config


MARKET_TIMEZONE = "America/New_York"
RTH_OPEN = "09:30"
RTH_CLOSE = "16:00"
PRIMARY_BAR_MINUTES = 130
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
    normalized_to_original = {str(column).strip().lower(): column for column in columns}
    normalized = list(normalized_to_original)
    open_column = next((column for column in normalized if column == "open"), None)
    high_column = next((column for column in normalized if column == "high"), None)
    low_column = next((column for column in normalized if column == "low"), None)
    close_column = next((column for column in normalized if column == "close"), None)
    volume_column = next((column for column in normalized if column in {"volume", "tickvolume"}), None)
    date_columns = [column for column in normalized if "date" in column]
    time_columns = [column for column in normalized if "time" in column]
    datetime_columns = [column for column in normalized if "date" in column or "time" in column]
    date_column = None
    time_column = None
    datetime_column = None
    split_pair = [
        (date_candidate, time_candidate)
        for date_candidate in date_columns
        for time_candidate in time_columns
        if date_candidate != time_candidate
    ]
    if split_pair:
        date_column, time_column = split_pair[0]
    elif datetime_columns:
        datetime_column = datetime_columns[0]
    missing = [
        name
        for name, column in {
            "open": open_column,
            "high": high_column,
            "low": low_column,
            "close": close_column,
            "volume": volume_column,
        }.items()
        if column is None
    ]
    if date_column is None and datetime_column is None:
        missing.append("date/time")
    if missing:
        raise ValueError(f"Eksik CSV sutunlari: {', '.join(missing)}")
    selected = [open_column, high_column, low_column, close_column, volume_column]
    if date_column is not None:
        selected.extend([date_column, time_column])
    else:
        selected.append(datetime_column)
    return {
        "usecols": [normalized_to_original[column] for column in dict.fromkeys(selected)],
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
        if values.str.contains(r"(?:[+-]\d{2}:\d{2}|Z)$", regex=True).any():
            parsed = pd.to_datetime(values, errors="coerce", format="mixed", utc=True)
            return pd.DatetimeIndex(parsed).tz_convert(MARKET_TIMEZONE)
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
        parsed = pd.to_datetime(values, errors="coerce", format=datetime_format, utc=True)
        if parsed.notna().mean() > 0.95:
            return pd.DatetimeIndex(parsed).tz_convert(MARKET_TIMEZONE)
    parsed = pd.to_datetime(values, errors="coerce", dayfirst=True, format="mixed", utc=True)
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
            index = _parse_datetime_fast(df[column_info["date"]], df[column_info["time"]])
        else:
            index = _parse_datetime_fast(df[column_info["datetime"]])
    df = df.rename(
        columns={
            column_info["open"]: "Open",
            column_info["high"]: "High",
            column_info["low"]: "Low",
            column_info["close"]: "Close",
            column_info["volume"]: "Volume",
        }
    )
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
        parsed = pd.to_datetime(values, errors="coerce", dayfirst=True, format="mixed", utc=True)
        index = pd.DatetimeIndex(parsed)
    if index.tz is None:
        return index.tz_localize(MARKET_TIMEZONE, ambiguous="NaT", nonexistent="shift_forward")
    return index.tz_convert(MARKET_TIMEZONE)


def _infer_intraday_interval_minutes(index):
    local_index = index.tz_convert(MARKET_TIMEZONE)
    session_dates = pd.Series(local_index.date, index=local_index)
    deltas = local_index.to_series().diff()[session_dates.eq(session_dates.shift())].dropna()
    if deltas.empty:
        raise ValueError("130m RTH barlari icin intraday veri gerekli; gunluk veri kullanilamaz.")
    minute_deltas = deltas.dt.total_seconds().div(60).round().astype(int)
    minute_deltas = minute_deltas[(minute_deltas > 0) & (minute_deltas < PRIMARY_BAR_MINUTES)]
    if minute_deltas.empty:
        raise ValueError("Intraday kaynak bar araligi belirlenemedi.")
    interval_minutes = math.gcd(*minute_deltas.iloc[:10000].tolist())
    if interval_minutes <= 0 or interval_minutes >= PRIMARY_BAR_MINUTES:
        raise ValueError(f"Kaynak veri araligi yaklasik {interval_minutes} dakika.")
    if PRIMARY_BAR_MINUTES % interval_minutes != 0:
        raise ValueError(f"{interval_minutes} dakikalik kaynak barlar 130 dakikaya tam bolunmuyor.")
    return interval_minutes


def _filter_fixed_rth_fast(df):
    local_index = df.index.tz_convert(MARKET_TIMEZONE)
    minutes = local_index.hour * 60 + local_index.minute
    return df.loc[(minutes >= 570) & (minutes < 960)]


def _aggregate_fixed_rth_130m_fast(df):
    local_index = df.index.tz_convert(MARKET_TIMEZONE)
    normalized = local_index.normalize()
    session_open = normalized + pd.Timedelta(hours=9, minutes=30)
    bucket = ((local_index - session_open) // pd.Timedelta(minutes=PRIMARY_BAR_MINUTES)).astype(int)
    valid = (bucket >= 0) & (bucket <= 2)
    if not valid.any():
        raise ValueError("130m RTH barlari uretilemedi.")
    work = df.loc[valid].copy()
    work["_SessionDate"] = pd.DatetimeIndex(normalized[valid])
    work["_Bucket"] = np.asarray(bucket[valid], dtype=int)
    grouped = work.groupby(["_SessionDate", "_Bucket"], sort=True).agg(OHLCV_AGG)
    if grouped.empty:
        raise ValueError("130m RTH barlari uretilemedi.")
    session_level = pd.DatetimeIndex(grouped.index.get_level_values(0))
    bucket_level = grouped.index.get_level_values(1).astype(int)
    grouped.index = session_level + pd.Timedelta(hours=9, minutes=30) + pd.to_timedelta(
        (bucket_level + 1) * PRIMARY_BAR_MINUTES,
        unit="m",
    )
    grouped["Is_Partial_Session_Bar"] = False
    return grouped.sort_index()


def _fixed_session_windows(session_dates):
    return {
        session_date.date(): (
            session_date + pd.Timedelta(hours=9, minutes=30),
            session_date + pd.Timedelta(hours=16),
        )
        for session_date in session_dates
    }


def _build_session_windows(index, use_market_calendar=True):
    local_index = index.tz_convert(MARKET_TIMEZONE)
    session_dates = pd.DatetimeIndex(pd.Series(local_index.normalize()).unique()).sort_values()
    if session_dates.empty:
        return {}, False
    cache_key = (
        session_dates.min().date(),
        session_dates.max().date(),
        bool(use_market_calendar),
        MARKET_TIMEZONE,
    )
    cached = SESSION_WINDOW_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if use_market_calendar and mcal is not None:
        schedule = mcal.get_calendar("NYSE").schedule(
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
    fallback_dates = pd.date_range(session_dates.min(), session_dates.max(), freq="D")
    result = (_fixed_session_windows(fallback_dates), bool(use_market_calendar))
    SESSION_WINDOW_CACHE[cache_key] = result
    return result


def _filter_rth_with_session_windows(df, session_windows):
    frames = []
    for session_date, session in df.groupby(df.index.date, sort=True):
        if session_date not in session_windows:
            continue
        session_open, session_close = session_windows[session_date]
        frames.append(session.loc[(session.index >= session_open) & (session.index < session_close)])
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
    if not use_market_calendar and bar_minutes == PRIMARY_BAR_MINUTES and not include_partial_session_bars:
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
        bucket = ((session.index - session_open) // pd.Timedelta(minutes=bar_minutes)).astype(int)
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
            if bucket_start >= session_close or (is_partial and not include_partial_session_bars):
                continue
            rows.append(row)
            index.append(bucket_end)
            partial_flags.append(bool(is_partial))
        if rows:
            frame = pd.DataFrame(rows, index=pd.DatetimeIndex(index))
            frame["Is_Partial_Session_Bar"] = partial_flags
            frames.append(frame)
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
        raise ValueError(f"{RTH_OPEN}-{RTH_CLOSE} America/New_York RTH filtresinden sonra veri kalmadi.")
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
    **strategy_params,
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
        config = {
            "strategy_name": strategy_name,
            "hurst_filter_mode": hurst_filter_mode,
            "hurst_threshold": hurst_threshold,
            "use_market_calendar": use_market_calendar,
            "include_partial_session_bars": include_partial_session_bars,
            **strategy_params,
        }
        df = generate_strategy_signals(df, config)
    df.attrs.update(
        _strategy_cache_config(
            hurst_filter_mode,
            hurst_threshold,
            strategy_name=strategy_name,
            use_market_calendar=use_market_calendar,
            include_partial_session_bars=include_partial_session_bars,
            **strategy_params,
        )
    )
    df.attrs["calendar_fallback"] = calendar_fallback
    if return_intraday:
        return df, intraday_df
    return df


def _symbol_from_path(file_path):
    stem = Path(file_path).stem.upper()
    if stem.endswith(".US"):
        stem = stem[:-3]
    for delimiter in ("_", "-", " "):
        if delimiter in stem:
            stem = stem.split(delimiter, 1)[0]
    return stem


def prepare_universe_data(
    data_folder,
    allowed_symbols=None,
    excluded_symbols=None,
    use_market_calendar=True,
    include_partial_session_bars=False,
    strategy_name=DEFAULT_STRATEGY_NAME,
    **strategy_params,
):
    data_folder_path = Path(data_folder)
    allowed = {str(symbol).upper() for symbol in allowed_symbols} if allowed_symbols else None
    excluded = {str(symbol).upper() for symbol in excluded_symbols} if excluded_symbols else set()
    result = {}
    for file_path in sorted(data_folder_path.glob("*.csv")):
        symbol = _symbol_from_path(file_path)
        if allowed is not None and symbol not in allowed:
            continue
        if symbol in excluded:
            continue
        result[symbol] = build_mega_dataframe(
            file_path,
            use_market_calendar=use_market_calendar,
            include_partial_session_bars=include_partial_session_bars,
            strategy_name=strategy_name,
            **strategy_params,
        )
    return result


__all__ = [
    "build_mega_dataframe",
    "load_clean_intraday_dataframe",
    "prepare_rth_130m_bars",
    "prepare_rth_intraday_bars",
    "prepare_universe_data",
    "read_ohlcv_csv_fast",
]
