from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
NEW_YORK = ZoneInfo("America/New_York")

BENCHMARK_ONLY = [
    "SPY", "QQQ", "IWM", "DIA", "RSP", "VTI",
    "XLK", "XLF", "XLY", "XLC", "XLI", "XLV", "XLE", "XLP", "XLU", "XLRE", "XLB",
    "SMH", "SOXX", "XBI", "IBB", "XRT",
]

TRADING_UNIVERSE_V1 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO", "AMD",
    "NFLX", "ADBE", "CRM", "ORCL", "INTU", "NOW", "SHOP", "UBER", "ABNB", "SNOW",
    "PLTR", "CRWD", "PANW", "FTNT", "ZS", "NET", "DDOG", "ANET", "CSCO", "IBM",
    "QCOM", "TXN", "MU", "INTC", "AMAT", "LRCX", "KLAC", "MRVL", "MCHP", "ON",
    "JPM", "BAC", "WFC", "C", "GS", "MS", "BLK", "SCHW", "AXP", "MA", "V", "COF",
    "PNC", "USB", "BK", "SPGI", "ICE", "CME", "MCO", "BX",
    "LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "BMY", "AMGN", "GILD", "REGN",
    "VRTX", "ISRG", "TMO", "DHR", "ABT", "MDT", "CVS", "CI", "HUM", "ELV", "SYK", "BSX",
    "HD", "LOW", "COST", "WMT", "TGT", "TJX", "MCD", "SBUX", "NKE", "LULU",
    "CMG", "YUM", "BKNG", "MAR", "RCL", "CCL", "ROST", "DG", "DLTR",
    "DIS", "CMCSA", "T", "VZ", "TMUS", "EA", "TTWO", "RBLX", "PARA", "WBD",
    "CAT", "DE", "GE", "HON", "ETN", "PH", "UNP", "UPS", "FDX", "RTX", "LMT", "NOC",
    "BA", "GD", "MMM", "EMR", "CARR", "OTIS", "URI",
    "XOM", "CVX", "COP", "EOG", "SLB", "OXY", "MPC", "VLO", "PSX", "HAL",
    "LIN", "APD", "SHW", "ECL", "NEM", "FCX", "NUE", "STLD", "DOW", "DD", "ALB",
    "PG", "KO", "PEP", "PM", "MO", "CL", "KMB", "MDLZ", "GIS", "KHC", "HSY", "KR", "EL",
    "NEE", "DUK", "SO", "AEP", "EXC", "SRE", "XEL", "PEG",
    "AMT", "PLD", "EQIX", "DLR", "O", "PSA",
]

DEFAULT_SYMBOLS = list(dict.fromkeys([*BENCHMARK_ONLY, *TRADING_UNIVERSE_V1]))


def ensure_project_venv() -> None:
    venv_python = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        return
    current_python = Path(sys.executable).resolve()
    if current_python == venv_python.resolve():
        return
    command = [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.call(command, cwd=str(Path.cwd())))


ensure_project_venv()

import pandas as pd
try:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
except ModuleNotFoundError as error:
    if error.name != "alpaca":
        raise
    python_exe = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    python_cmd = str(python_exe) if python_exe.exists() else sys.executable
    raise SystemExit(
        "Eksik Python paketi: alpaca-py\n"
        f"Kurulum: \"{python_cmd}\" -m pip install alpaca-py"
    ) from error


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def get_credentials() -> tuple[str, str]:
    load_env_file(PROJECT_DIR / ".env")
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY ve ALPACA_SECRET_KEY .env dosyasinda tanimli olmali.")
    return api_key, secret_key


def resolve_date_end(end_arg: str) -> datetime:
    return parse_utc_date(end_arg)


def resolve_intraday_end(end_arg: str) -> datetime:
    if str(end_arg).strip().lower() == "today":
        return datetime.now(timezone.utc)
    return parse_utc_date(end_arg)


def parse_utc_date(value: str) -> datetime:
    if str(value).strip().lower() == "today":
        return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def unique_symbols(symbols: Iterable[str]) -> list[str]:
    cleaned = []
    for raw in symbols:
        symbol = raw.strip().upper()
        if symbol and symbol not in {"SYMBOL", "TICKER"}:
            cleaned.append(symbol)
    return list(dict.fromkeys(cleaned))


def load_symbols(symbols: list[str] | None, symbols_file: str | None, universe: str | None) -> list[str]:
    result = []
    if universe == "all" or (universe is None and not symbols and not symbols_file):
        result.extend(DEFAULT_SYMBOLS)
    if symbols:
        result.extend(symbols)
    if symbols_file:
        for line in Path(symbols_file).read_text(encoding="utf-8").splitlines():
            result.append(line.strip().split(",")[0])
    return unique_symbols(result)


def get_data_feed(feed_name: str):
    return DataFeed.SIP if feed_name.lower() == "sip" else DataFeed.IEX


def get_adjustment(adjustment_name: str):
    name = adjustment_name.lower()
    if name in {"all", "adjusted"}:
        return Adjustment.ALL
    if name in {"raw", "none", "unadjusted"}:
        return Adjustment.RAW
    if name == "split":
        return Adjustment.SPLIT
    if name == "dividend":
        return Adjustment.DIVIDEND
    raise ValueError(f"Unsupported adjustment: {adjustment_name}")


def normalize_bars(raw_bars: pd.DataFrame, symbol: str, rth_only: bool) -> pd.DataFrame:
    if raw_bars is None or raw_bars.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    bars = raw_bars.copy()
    if isinstance(bars.index, pd.MultiIndex):
        symbol_level = "symbol" if "symbol" in bars.index.names else bars.index.names[0]
        if symbol not in set(bars.index.get_level_values(symbol_level)):
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        bars = bars.xs(symbol, level=symbol_level)

    bars.index = pd.to_datetime(bars.index, utc=True).tz_convert(NEW_YORK)
    bars = bars.sort_index()
    if rth_only:
        bars = bars.between_time("09:30", "15:59", inclusive="both")

    bars = bars.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    )
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in bars.columns]
    if missing:
        raise ValueError(f"{symbol}: eksik kolonlar: {missing}")
    bars = bars[required].dropna()
    bars.index.name = "Date"
    return bars.reset_index()


def aggregate_to_130min(raw_5min: pd.DataFrame) -> pd.DataFrame:
    if raw_5min.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    frame = raw_5min.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], utc=True)
    frame = frame.sort_values("Date")
    local_time = frame["Date"].dt.tz_convert(NEW_YORK)
    minutes = local_time.dt.hour * 60 + local_time.dt.minute
    rth_start = 9 * 60 + 30
    rth_end = 16 * 60
    frame = frame[(minutes >= rth_start) & (minutes < rth_end)].copy()
    if frame.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    minutes = minutes.loc[frame.index]
    local_time = local_time.loc[frame.index]
    frame["session"] = local_time.dt.date
    frame["block"] = pd.cut(
        minutes,
        bins=[rth_start - 1, 11 * 60 + 39, 13 * 60 + 49, rth_end - 1],
        labels=[0, 1, 2],
    ).astype(int)
    output = frame.groupby(["session", "block"], sort=True).agg(
        Open=("Open", "first"),
        High=("High", "max"),
        Low=("Low", "min"),
        Close=("Close", "last"),
        Volume=("Volume", "sum"),
    ).reset_index()
    block_start_minutes = {0: 9 * 60 + 30, 1: 11 * 60 + 40, 2: 13 * 60 + 50}
    session_start = pd.to_datetime(output["session"].astype(str)).dt.tz_localize(NEW_YORK)
    output["Date"] = [
        session + pd.Timedelta(minutes=block_start_minutes[int(block)])
        for session, block in zip(session_start, output["block"])
    ]
    output["Date"] = pd.to_datetime(output["Date"], utc=True)
    block_end_minutes = {0: 11 * 60 + 39, 1: 13 * 60 + 49, 2: 15 * 60 + 59}
    now_ny = pd.Timestamp.now(tz=NEW_YORK)
    keep_indices = []
    for idx, row in output.iterrows():
        session_val = row["session"]
        block_val = int(row["block"])
        session_ts = pd.Timestamp(session_val).tz_localize(NEW_YORK)
        block_end = session_ts + pd.Timedelta(minutes=block_end_minutes[block_val])
        if now_ny >= block_end:
            keep_indices.append(idx)
    output = output.loc[keep_indices].reset_index(drop=True)
    return output[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna().reset_index(drop=True)


def download_bars(client, symbol: str, start: datetime, end: datetime, timeframe, feed, adjustment, rth_only: bool) -> pd.DataFrame:
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        adjustment=adjustment,
        feed=feed,
    )
    response = client.get_stock_bars(request)
    return normalize_bars(response.df, symbol, rth_only=rth_only)


def resolve_worker_count(value: str | int, default_auto: int, upper_bound: int | None = None) -> int:
    if str(value).strip().lower() == "auto":
        resolved = default_auto
    else:
        resolved = int(value)
    if upper_bound is not None:
        resolved = min(resolved, upper_bound)
    return max(1, resolved)


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temp_path, index=False)
    os.replace(temp_path, path)


def read_existing_bars(path: Path) -> pd.DataFrame:
    if not path.exists() or row_count(path) == 0:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    frame = pd.read_csv(path)
    if "Date" not in frame.columns:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    return frame


def latest_timestamp(frame: pd.DataFrame) -> pd.Timestamp | None:
    if frame.empty or "Date" not in frame.columns:
        return None
    dates = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()
    if dates.empty:
        return None
    return dates.max()


def merge_bars(existing: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
    frames = [frame for frame in [existing, new_rows] if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame(columns=columns)
    merged = pd.concat(frames, ignore_index=True)
    for column in columns:
        if column not in merged.columns:
            merged[column] = pd.NA
    merged["Date"] = pd.to_datetime(merged["Date"], errors="coerce", utc=True)
    merged = merged.dropna(subset=["Date"])
    merged = merged.sort_values("Date").drop_duplicates(subset=["Date"], keep="last")
    return merged[columns].reset_index(drop=True)


def download_bars_with_retry(
    api_key: str,
    secret_key: str,
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe,
    feed,
    adjustment,
    rth_only: bool,
    attempts: int = 3,
) -> pd.DataFrame:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            client = StockHistoricalDataClient(api_key, secret_key)
            return download_bars(client, symbol, start, end, timeframe, feed, adjustment, rth_only=rth_only)
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise last_error


def refresh_file(
    path: Path,
    new_rows: pd.DataFrame,
    refresh_latest: bool,
    overwrite: bool,
    symbol: str,
    file_type: str,
    allow_row_count_reduction: bool = False,
) -> tuple[str, int, int, str]:
    old_rows = row_count(path)
    existing = read_existing_bars(path) if path.exists() and not overwrite else pd.DataFrame()
    if overwrite:
        final = merge_bars(pd.DataFrame(), new_rows)
        status = "downloaded"
    elif refresh_latest:
        final = merge_bars(existing, new_rows)
        status = "refreshed"
    else:
        final = merge_bars(pd.DataFrame(), new_rows)
        status = "downloaded"
    new_rows_added = max(len(final) - old_rows, 0)
    warning = ""
    if old_rows and len(final) < old_rows:
        action = "overwrite" if allow_row_count_reduction else "keep_old_file"
        reason = "merged_row_count_lower_than_existing"
        warning = f"symbol={symbol},file_type={file_type},old_rows={old_rows},new_rows={len(final)},action={action},reason={reason}"
        if not allow_row_count_reduction:
            return "skipped", len(new_rows), 0, warning
    atomic_write_csv(final, path)
    return status, len(new_rows), new_rows_added, warning


def _download_symbol_worker(payload: dict) -> dict:
    symbol = payload["symbol"]
    daily_path = Path(payload["daily_path"])
    raw_path = Path(payload["raw_path"])
    refresh_latest = payload["refresh_latest"]
    overwrite = payload["overwrite"]
    skip_download = payload["skip_download"]
    allow_row_count_reduction = payload.get("allow_row_count_reduction", False)
    start_time = time.perf_counter()
    result = {
        "symbol": symbol,
        "daily_status": "skipped",
        "raw_5min_status": "skipped",
        "daily_provider_rows": 0,
        "raw_provider_rows": 0,
        "new_rows_added": 0,
        "current_session_received": 0,
        "current_session_added": 0,
        "warnings": [],
        "error": "",
        "runtime_sec": 0.0,
    }
    try:
        if skip_download:
            return result
        api_key = payload["api_key"]
        secret_key = payload["secret_key"]
        feed = payload["feed"]
        adjustment = payload["adjustment"]
        day_tf = payload["day_tf"]
        min5_tf = payload["min5_tf"]
        daily_end = payload["daily_end"]
        intraday_end = payload["intraday_end"]

        if daily_path.exists() and not overwrite and not refresh_latest:
            result["daily_status"] = "skipped"
        else:
            existing = read_existing_bars(daily_path)
            latest = latest_timestamp(existing)
            daily_start = payload["daily_start"]
            if refresh_latest and latest is not None and not overwrite:
                daily_start = max(payload["daily_start"], (latest - pd.Timedelta(days=10)).to_pydatetime())
            daily = download_bars_with_retry(api_key, secret_key, symbol, daily_start, daily_end, day_tf, feed, adjustment, False)
            status, provider_rows, added, warning = refresh_file(daily_path, daily, refresh_latest, overwrite, symbol, "daily", allow_row_count_reduction)
            result["daily_status"] = status
            result["daily_provider_rows"] = provider_rows
            result["new_rows_added"] += added
            if warning:
                result["warnings"].append(warning)

        if raw_path.exists() and not overwrite and not refresh_latest:
            result["raw_5min_status"] = "skipped"
        else:
            existing = read_existing_bars(raw_path)
            latest = latest_timestamp(existing)
            intraday_start = payload["intraday_start"]
            if refresh_latest and latest is not None and not overwrite:
                intraday_start = max(payload["intraday_start"], (latest - pd.Timedelta(days=5)).to_pydatetime())
            raw_5min = download_bars_with_retry(api_key, secret_key, symbol, intraday_start, intraday_end, min5_tf, feed, adjustment, True)
            status, provider_rows, added, warning = refresh_file(raw_path, raw_5min, refresh_latest, overwrite, symbol, "raw_5min", allow_row_count_reduction)
            result["raw_5min_status"] = status
            result["raw_provider_rows"] = provider_rows
            result["new_rows_added"] += added
            
            # Calculate current session stats
            if not raw_5min.empty:
                dates_str = raw_5min["Date"].astype(str)
                result["current_session_received"] = int(dates_str.str.contains("2026-06-23").sum())
            else:
                result["current_session_received"] = 0
                
            # Check how many are in the final saved file
            final_saved = read_existing_bars(raw_path)
            if not final_saved.empty:
                dates_str = final_saved["Date"].astype(str)
                result["current_session_added"] = int(dates_str.str.contains("2026-06-23").sum())
            else:
                result["current_session_added"] = 0
                
            if warning:
                result["warnings"].append(warning)
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        result["runtime_sec"] = round(time.perf_counter() - start_time, 4)
    return result


def _aggregate_130min_worker(payload: dict) -> dict:
    symbol = payload["symbol"]
    raw_path = Path(payload["raw_path"])
    bar_path = Path(payload["bar_path"])
    allow_row_count_reduction = payload.get("allow_row_count_reduction", False)
    start_time = time.perf_counter()
    result = {
        "symbol": symbol,
        "bar_130min_status": "skipped",
        "bar_130min_rows": row_count(bar_path),
        "warning": "",
        "error": "",
        "runtime_sec": 0.0,
    }
    try:
        if not raw_path.exists():
            result["error"] = "missing_raw_5min"
            return result
        if bar_path.exists() and not payload["overwrite"] and not payload["rebuild"]:
            return result
        old_rows = row_count(bar_path)
        raw_5min = pd.read_csv(raw_path)
        bars_130min = aggregate_to_130min(raw_5min)
        if old_rows and len(bars_130min) < old_rows:
            action = "overwrite" if allow_row_count_reduction else "keep_old_file"
            reason = "aggregated_row_count_lower_than_existing"
            result["warning"] = f"symbol={symbol},file_type=130min,old_rows={old_rows},new_rows={len(bars_130min)},action={action},reason={reason}"
            if not allow_row_count_reduction:
                return result
        atomic_write_csv(bars_130min, bar_path)
        result["bar_130min_status"] = "built"
        result["bar_130min_rows"] = len(bars_130min)
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        result["runtime_sec"] = round(time.perf_counter() - start_time, 4)
    return result


def row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return max(sum(1 for _ in path.open("r", encoding="utf-8")) - 1, 0)
    except UnicodeDecodeError:
        return max(sum(1 for _ in path.open("r")) - 1, 0)


def date_range_summary(path: Path) -> dict[str, str]:
    empty = {"first_date": "", "last_date": ""}
    if not path.exists() or row_count(path) == 0:
        return empty
    try:
        frame = pd.read_csv(path, usecols=["Date"])
    except Exception:
        return empty
    dates = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()
    if dates.empty:
        return empty
    return {
        "first_date": str(dates.min().date()),
        "last_date": str(dates.max().date()),
    }


def latest_date_in_dir(directory: Path, pattern: str) -> str:
    latest = None
    for path in directory.glob(pattern):
        summary = date_range_summary(path)
        if not summary["last_date"]:
            continue
        candidate = pd.to_datetime(summary["last_date"], errors="coerce")
        if pd.isna(candidate):
            continue
        if latest is None or candidate > latest:
            latest = candidate
    return "" if latest is None else str(latest.date())


def write_data_refresh_reports(
    latest_dir: Path,
    summary: dict,
    records: list[dict],
    timing_rows: list[dict],
) -> None:
    latest_dir.mkdir(parents=True, exist_ok=True)
    csv_path = latest_dir / "data_refresh_report.csv"
    md_path = latest_dir / "data_refresh_report.md"
    timing_path = latest_dir / "data_refresh_timing_report.csv"
    pd.DataFrame([summary]).to_csv(csv_path, index=False)
    pd.DataFrame(timing_rows).to_csv(timing_path, index=False)

    reduction_warnings = []
    for r in records:
        w_str = r.get("warning", "")
        if w_str:
            for w in w_str.split("|"):
                if "old_rows=" in w:
                    parts = {}
                    for p in w.split(","):
                        if "=" in p:
                            k, v = p.split("=", 1)
                            parts[k.strip()] = v.strip()
                    reduction_warnings.append(parts)

    lines = [
        "# Data Refresh Report",
        "",
        f"- refresh_mode: {summary.get('refresh_mode', '')}",
        f"- parallel_enabled: {summary.get('parallel_enabled', False)}",
        f"- workers_resolved: {summary.get('workers_resolved', 1)}",
        f"- download_workers_resolved: {summary.get('download_workers_resolved', 1)}",
        f"- aggregation_workers_resolved: {summary.get('aggregation_workers_resolved', 1)}",
        f"- daily_requested_count: {summary.get('daily_requested_count', 0)}",
        f"- daily_files_updated: {summary.get('daily_files_updated', 0)}",
        f"- raw_5min_requested_count: {summary.get('raw_5min_requested_count', 0)}",
        f"- raw_5min_files_updated: {summary.get('raw_5min_files_updated', 0)}",
        f"- bars_130min_rebuilt_count: {summary.get('bars_130min_rebuilt_count', 0)}",
        f"- bars_130min_failed_count: {summary.get('bars_130min_failed_count', 0)}",
        f"- provider_rows_received: {summary.get('provider_rows_received', 0)}",
        f"- new_rows_added: {summary.get('new_rows_added', 0)}",
        f"- old_latest_daily_date: {summary.get('old_latest_daily_date', '')}",
        f"- new_latest_daily_date: {summary.get('new_latest_daily_date', '')}",
        f"- old_latest_130min_date: {summary.get('old_latest_130min_date', '')}",
        f"- new_latest_130min_date: {summary.get('new_latest_130min_date', '')}",
        f"- cache_invalidated: {summary.get('cache_invalidated', False)}",
        f"- daily_requested_end: {summary.get('daily_requested_end', '')}",
        f"- intraday_requested_end: {summary.get('intraday_requested_end', '')}",
        f"- intraday_end_mode: {summary.get('intraday_end_mode', '')}",
        f"- current_session_rows_received: {summary.get('current_session_rows_received', 0)}",
        f"- current_session_new_rows_added: {summary.get('current_session_new_rows_added', 0)}",
        f"- pending_symbols_current_session_rows: {summary.get('pending_symbols_current_session_rows', '')}",
        f"- total_runtime_sec: {summary.get('total_runtime_sec', 0)}",
        f"- download_runtime_sec: {summary.get('download_runtime_sec', 0)}",
        f"- aggregation_130min_runtime_sec: {summary.get('aggregation_130min_runtime_sec', 0)}",
        f"- slowest_symbols_top10: {summary.get('slowest_symbols_top10', '')}",
        f"- warnings: {summary.get('warnings', '')}",
        f"- errors: {summary.get('errors', '')}",
        "",
        f"Manifest rows: {len(records)}",
    ]

    if reduction_warnings:
        lines.append("")
        lines.append("## Row Count Reduction Warnings")
        lines.append("")
        lines.append("| symbol | file_type | old_rows | new_rows | action | reason |")
        lines.append("|---|---|---|---|---|---|")
        for w in reduction_warnings:
            lines.append(
                f"| {w.get('symbol', '')} | {w.get('file_type', '')} | "
                f"{w.get('old_rows', '')} | {w.get('new_rows', '')} | "
                f"{w.get('action', '')} | {w.get('reason', '')} |"
            )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily + raw 5min + RTH 130min data indirir.")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--symbols-file")
    parser.add_argument("--universe", choices=["all"], default=None)
    parser.add_argument("--feed", choices=["iex", "sip"], default="iex")
    parser.add_argument("--adjustment", choices=["all", "raw", "split", "dividend"], default="all")
    parser.add_argument("--daily-start", default="2015-01-01")
    parser.add_argument("--intraday-start", default="2020-01-01")
    parser.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--output-dir", default="hisseler")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--refresh-latest", action="store_true")
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--workers", default="auto")
    parser.add_argument("--download-workers", default="4")
    parser.add_argument("--aggregation-workers", default="auto")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--only-aggregate-130min", action="store_true")
    parser.add_argument("--allow-row-count-reduction", action="store_true")
    return parser


def main() -> None:
    total_start = time.perf_counter()
    args = build_parser().parse_args()
    symbols = load_symbols(args.symbols, args.symbols_file, args.universe)
    if not symbols:
        raise SystemExit("Indirilecek sembol bulunamadi.")

    output_dir = Path(args.output_dir)
    daily_dir = output_dir / "daily"
    raw_5min_dir = output_dir / "raw_5min"
    bars_130min_dir = output_dir / "bars_130min"
    manifest_dir = output_dir / "manifest"
    for directory in (daily_dir, raw_5min_dir, bars_130min_dir, manifest_dir):
        directory.mkdir(parents=True, exist_ok=True)

    api_key = ""
    secret_key = ""
    if not args.skip_download and not args.only_aggregate_130min:
        api_key, secret_key = get_credentials()
    feed = get_data_feed(args.feed)
    adjustment = get_adjustment(args.adjustment)
    daily_start = parse_utc_date(args.daily_start)
    intraday_start = parse_utc_date(args.intraday_start)
    daily_end = resolve_date_end(args.end)
    intraday_end = resolve_intraday_end(args.end)
    day_tf = TimeFrame.Day if hasattr(TimeFrame, "Day") else TimeFrame(1, TimeFrameUnit.Day)
    min5_tf = TimeFrame(5, TimeFrameUnit.Minute)
    cpu_count = os.cpu_count() or 2
    workers_resolved = resolve_worker_count(args.workers, min(max(cpu_count - 1, 1), 8), 16)
    download_workers_resolved = resolve_worker_count(args.download_workers, 4, 8)
    aggregation_workers_resolved = resolve_worker_count(args.aggregation_workers, min(max(cpu_count - 1, 1), 8), 16)
    if args.parallel:
        download_workers_resolved = min(download_workers_resolved, workers_resolved)
        aggregation_workers_resolved = min(aggregation_workers_resolved, workers_resolved)
    else:
        download_workers_resolved = 1
        aggregation_workers_resolved = 1

    old_latest_daily_date = latest_date_in_dir(daily_dir, "*_1D.csv")
    old_latest_130min_date = latest_date_in_dir(bars_130min_dir, "*_130min.csv")
    refresh_mode = "refresh-latest" if args.refresh_latest else ("overwrite" if args.overwrite else "skip-existing")
    if args.only_aggregate_130min:
        refresh_mode = "only-aggregate-130min"
    elif args.skip_download:
        refresh_mode = "skip-download"

    summary = {
        "daily_downloaded": 0,
        "daily_skipped": 0,
        "raw_5min_downloaded": 0,
        "raw_5min_skipped": 0,
        "bars_130min_built": 0,
        "bars_130min_skipped": 0,
        "errors": 0,
    }
    records = []
    timing_rows = []
    download_results = {}
    aggregation_results = {}

    print(
        f"refresh_mode: {refresh_mode}; parallel: {args.parallel}; "
        f"download_workers: {download_workers_resolved}; aggregation_workers: {aggregation_workers_resolved}",
        flush=True,
    )

    download_payloads = [
        {
            "symbol": symbol,
            "daily_path": str(daily_dir / f"{symbol}_1D.csv"),
            "raw_path": str(raw_5min_dir / f"{symbol}_5min.csv"),
            "api_key": api_key,
            "secret_key": secret_key,
            "feed": feed,
            "adjustment": adjustment,
            "day_tf": day_tf,
            "min5_tf": min5_tf,
            "daily_start": daily_start,
            "intraday_start": intraday_start,
            "daily_end": daily_end,
            "intraday_end": intraday_end,
            "refresh_latest": args.refresh_latest,
            "overwrite": args.overwrite,
            "skip_download": args.skip_download or args.only_aggregate_130min,
            "allow_row_count_reduction": args.allow_row_count_reduction,
        }
        for symbol in symbols
    ]

    download_start = time.perf_counter()
    if args.parallel and download_workers_resolved > 1 and not args.skip_download and not args.only_aggregate_130min:
        with ThreadPoolExecutor(max_workers=download_workers_resolved) as executor:
            futures = {executor.submit(_download_symbol_worker, payload): payload["symbol"] for payload in download_payloads}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                result = future.result()
                download_results[result["symbol"]] = result
                print(
                    f"[download {completed}/{len(symbols)}] {result['symbol']} "
                    f"daily {result['daily_status']}, 5min {result['raw_5min_status']}",
                    flush=True,
                )
                if result["error"]:
                    print(f"  error: {result['error']}", flush=True)
    else:
        for index, payload in enumerate(download_payloads, 1):
            result = _download_symbol_worker(payload)
            download_results[result["symbol"]] = result
            print(
                f"[download {index}/{len(symbols)}] {result['symbol']} "
                f"daily {result['daily_status']}, 5min {result['raw_5min_status']}",
                flush=True,
            )
            if result["error"]:
                print(f"  error: {result['error']}", flush=True)
                time.sleep(1)
    download_runtime_sec = round(time.perf_counter() - download_start, 4)

    aggregation_payloads = []
    for symbol in symbols:
        download_result = download_results.get(symbol, {})
        raw_status = download_result.get("raw_5min_status", "skipped")
        rebuild = bool(args.overwrite or args.refresh_latest or args.skip_download or args.only_aggregate_130min or raw_status in {"downloaded", "refreshed"})
        aggregation_payloads.append(
            {
                "symbol": symbol,
                "raw_path": str(raw_5min_dir / f"{symbol}_5min.csv"),
                "bar_path": str(bars_130min_dir / f"{symbol}_130min.csv"),
                "overwrite": args.overwrite,
                "rebuild": rebuild,
                "allow_row_count_reduction": args.allow_row_count_reduction,
            }
        )

    aggregation_start = time.perf_counter()
    if args.parallel and aggregation_workers_resolved > 1:
        with ThreadPoolExecutor(max_workers=aggregation_workers_resolved) as executor:
            futures = {executor.submit(_aggregate_130min_worker, payload): payload["symbol"] for payload in aggregation_payloads}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                result = future.result()
                aggregation_results[result["symbol"]] = result
                print(
                    f"[aggregate {completed}/{len(symbols)}] {result['symbol']} 130min {result['bar_130min_status']}",
                    flush=True,
                )
                if result["error"]:
                    print(f"  error: {result['error']}", flush=True)
    else:
        for index, payload in enumerate(aggregation_payloads, 1):
            result = _aggregate_130min_worker(payload)
            aggregation_results[result["symbol"]] = result
            print(f"[aggregate {index}/{len(symbols)}] {result['symbol']} 130min {result['bar_130min_status']}", flush=True)
            if result["error"]:
                print(f"  error: {result['error']}", flush=True)
    aggregation_runtime_sec = round(time.perf_counter() - aggregation_start, 4)

    all_warnings = []
    all_errors = []
    for index, symbol in enumerate(symbols, 1):
        daily_path = daily_dir / f"{symbol}_1D.csv"
        raw_path = raw_5min_dir / f"{symbol}_5min.csv"
        bar_path = bars_130min_dir / f"{symbol}_130min.csv"
        download_result = download_results.get(symbol, {})
        aggregation_result = aggregation_results.get(symbol, {})
        daily_status = download_result.get("daily_status", "skipped")
        raw_status = download_result.get("raw_5min_status", "skipped")
        bar_status = aggregation_result.get("bar_130min_status", "skipped")
        error_parts = [value for value in [download_result.get("error", ""), aggregation_result.get("error", "")] if value]
        warning_parts = list(download_result.get("warnings", []))
        if aggregation_result.get("warning"):
            warning_parts.append(aggregation_result["warning"])
        if error_parts:
            summary["errors"] += 1
            all_errors.append(f"{symbol}:{'|'.join(error_parts)}")
        if warning_parts:
            all_warnings.append(f"{symbol}:{'|'.join(warning_parts)}")
        summary["daily_downloaded"] += int(daily_status in {"downloaded", "refreshed"})
        summary["daily_skipped"] += int(daily_status == "skipped")
        summary["raw_5min_downloaded"] += int(raw_status in {"downloaded", "refreshed"})
        summary["raw_5min_skipped"] += int(raw_status == "skipped")
        summary["bars_130min_built"] += int(bar_status == "built")
        summary["bars_130min_skipped"] += int(bar_status == "skipped")

        daily_range = date_range_summary(daily_path)
        raw_range = date_range_summary(raw_path)
        bar_range = date_range_summary(bar_path)
        record = {
            "symbol": symbol,
            "daily_status": daily_status,
            "raw_5min_status": raw_status,
            "bar_130min_status": bar_status,
            "daily_rows": row_count(daily_path),
            "raw_5min_rows": row_count(raw_path),
            "bar_130min_rows": row_count(bar_path),
            "daily_first_date": daily_range["first_date"],
            "daily_last_date": daily_range["last_date"],
            "raw_5min_first_date": raw_range["first_date"],
            "raw_5min_last_date": raw_range["last_date"],
            "bar_130min_first_date": bar_range["first_date"],
            "bar_130min_last_date": bar_range["last_date"],
            "daily_path": str(daily_path),
            "raw_5min_path": str(raw_path),
            "bar_130min_path": str(bar_path),
            "download_runtime_sec": download_result.get("runtime_sec", 0.0),
            "aggregation_runtime_sec": aggregation_result.get("runtime_sec", 0.0),
            "provider_rows_received": int(download_result.get("daily_provider_rows", 0)) + int(download_result.get("raw_provider_rows", 0)),
            "new_rows_added": int(download_result.get("new_rows_added", 0)),
            "warning": "|".join(warning_parts),
            "error": "|".join(error_parts),
        }
        records.append(record)
        timing_rows.append(
            {
                "symbol": symbol,
                "download_runtime_sec": record["download_runtime_sec"],
                "aggregation_runtime_sec": record["aggregation_runtime_sec"],
                "total_symbol_runtime_sec": round(record["download_runtime_sec"] + record["aggregation_runtime_sec"], 4),
            }
        )
        print(
            f"[{index}/{len(symbols)}] {symbol} daily {daily_status}, 5min {raw_status}, 130min {bar_status}",
            flush=True,
        )

    manifest_path = manifest_dir / "download_manifest.csv"
    pd.DataFrame(records).to_csv(manifest_path, index=False)

    any_updated = (summary["daily_downloaded"] > 0 or 
                   summary["raw_5min_downloaded"] > 0 or 
                   summary["bars_130min_built"] > 0)
    cache_invalidated = False
    if any_updated:
        cache_dir = daily_dir.parent / ".cache" / "prepared_features"
        if cache_dir.exists():
            for item in cache_dir.iterdir():
                if item.is_file():
                    try:
                        item.unlink()
                    except Exception as e:
                        print(f"Warning: failed to delete cache file {item}: {e}")
        cache_invalidated = True

    new_latest_daily_date = latest_date_in_dir(daily_dir, "*_1D.csv")
    new_latest_130min_date = latest_date_in_dir(bars_130min_dir, "*_130min.csv")
    total_runtime_sec = round(time.perf_counter() - total_start, 4)
    slowest = sorted(timing_rows, key=lambda item: item["total_symbol_runtime_sec"], reverse=True)[:10]
    slowest_symbols_top10 = ";".join(f"{item['symbol']}:{item['total_symbol_runtime_sec']}" for item in slowest)
    current_session_rows_received = sum(row.get("current_session_received", 0) for row in download_results.values())
    current_session_new_rows_added = sum(row.get("current_session_added", 0) for row in download_results.values())
    
    pending_symbols = {"AMD", "KLAC", "LRCX", "SMH", "SOXX"}
    pending_parts = []
    for row in download_results.values():
        sym = row["symbol"]
        if sym in pending_symbols:
            pending_parts.append(f"{sym}:{row.get('current_session_added', 0)}")
    pending_symbols_current_session_rows_str = ", ".join(pending_parts)
    
    refresh_summary = {
        "refresh_mode": refresh_mode,
        "parallel_enabled": bool(args.parallel),
        "workers_resolved": workers_resolved,
        "download_workers_resolved": download_workers_resolved,
        "aggregation_workers_resolved": aggregation_workers_resolved,
        "daily_requested_count": 0 if args.skip_download or args.only_aggregate_130min else len(symbols),
        "daily_files_updated": summary["daily_downloaded"],
        "raw_5min_requested_count": 0 if args.skip_download or args.only_aggregate_130min else len(symbols),
        "raw_5min_files_updated": summary["raw_5min_downloaded"],
        "bars_130min_rebuilt_count": summary["bars_130min_built"],
        "bars_130min_failed_count": sum(
            1
            for row in records
            if row["error"] and ("missing_raw_5min" in row["error"] or row["bar_130min_status"] != "built")
        ),
        "provider_rows_received": sum(row["provider_rows_received"] for row in records),
        "new_rows_added": sum(row["new_rows_added"] for row in records),
        "old_latest_daily_date": old_latest_daily_date,
        "new_latest_daily_date": new_latest_daily_date,
        "old_latest_130min_date": old_latest_130min_date,
        "new_latest_130min_date": new_latest_130min_date,
        "cache_invalidated": cache_invalidated,
        "total_runtime_sec": total_runtime_sec,
        "download_runtime_sec": download_runtime_sec,
        "aggregation_130min_runtime_sec": aggregation_runtime_sec,
        "slowest_symbols_top10": slowest_symbols_top10,
        "warnings": ";".join(all_warnings),
        "errors": ";".join(all_errors),
        "daily_requested_end": daily_end.isoformat(),
        "intraday_requested_end": intraday_end.isoformat(),
        "intraday_end_mode": "now" if args.end == "today" else "current_timestamp",
        "current_session_rows_received": current_session_rows_received,
        "current_session_new_rows_added": current_session_new_rows_added,
        "pending_symbols_current_session_rows": pending_symbols_current_session_rows_str,
    }
    write_data_refresh_reports(BASE_DIR / "reports" / "latest", refresh_summary, records, timing_rows)

    print("Summary:", flush=True)
    for key, value in summary.items():
        print(f"{key}: {value}", flush=True)
    print(f"manifest: {manifest_path}", flush=True)
    print(f"data_refresh_report: {BASE_DIR / 'reports' / 'latest' / 'data_refresh_report.md'}", flush=True)
    if summary["errors"] >= len(symbols):
        raise SystemExit("All symbols failed; data refresh did not complete.")


if __name__ == "__main__":
    main()
