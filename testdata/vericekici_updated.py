from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
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
    return parser


def main() -> None:
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

    api_key, secret_key = get_credentials()
    client = StockHistoricalDataClient(api_key, secret_key)
    feed = get_data_feed(args.feed)
    adjustment = get_adjustment(args.adjustment)
    daily_start = parse_utc_date(args.daily_start)
    intraday_start = parse_utc_date(args.intraday_start)
    end = parse_utc_date(args.end)
    day_tf = TimeFrame.Day if hasattr(TimeFrame, "Day") else TimeFrame(1, TimeFrameUnit.Day)
    min5_tf = TimeFrame(5, TimeFrameUnit.Minute)

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

    for index, symbol in enumerate(symbols, 1):
        daily_path = daily_dir / f"{symbol}_1D.csv"
        raw_path = raw_5min_dir / f"{symbol}_5min.csv"
        bar_path = bars_130min_dir / f"{symbol}_130min.csv"
        daily_status = "skipped"
        raw_status = "skipped"
        bar_status = "skipped"
        error = ""

        try:
            if daily_path.exists() and not args.overwrite:
                summary["daily_skipped"] += 1
            else:
                daily = download_bars(client, symbol, daily_start, end, day_tf, feed, adjustment, rth_only=False)
                daily.to_csv(daily_path, index=False)
                daily_status = "downloaded"
                summary["daily_downloaded"] += 1

            if raw_path.exists() and not args.overwrite:
                summary["raw_5min_skipped"] += 1
            else:
                raw_5min = download_bars(client, symbol, intraday_start, end, min5_tf, feed, adjustment, rth_only=True)
                raw_5min.to_csv(raw_path, index=False)
                raw_status = "downloaded"
                summary["raw_5min_downloaded"] += 1

            if bar_path.exists() and not args.overwrite:
                summary["bars_130min_skipped"] += 1
            else:
                raw_5min = pd.read_csv(raw_path)
                bars_130min = aggregate_to_130min(raw_5min)
                bars_130min.to_csv(bar_path, index=False)
                bar_status = "built"
                summary["bars_130min_built"] += 1
        except Exception as exc:
            error = str(exc)
            summary["errors"] += 1

        print(
            f"[{index}/{len(symbols)}] {symbol} daily {daily_status}, 5min {raw_status}, 130min {bar_status}",
            flush=True,
        )
        if error:
            print(f"  error: {error}", flush=True)

        daily_range = date_range_summary(daily_path)
        raw_range = date_range_summary(raw_path)
        bar_range = date_range_summary(bar_path)
        records.append(
            {
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
                "error": error,
            }
        )

        if error:
            time.sleep(1)

    manifest_path = manifest_dir / "download_manifest.csv"
    pd.DataFrame(records).to_csv(manifest_path, index=False)

    print("Summary:", flush=True)
    for key, value in summary.items():
        print(f"{key}: {value}", flush=True)
    print(f"manifest: {manifest_path}", flush=True)


if __name__ == "__main__":
    main()
