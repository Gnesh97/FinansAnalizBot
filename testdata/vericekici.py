import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
NEW_YORK = ZoneInfo("America/New_York")


def ensure_project_venv():
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
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit


def load_env_file(path):
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def get_credentials():
    load_env_file(PROJECT_DIR / ".env")
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        raise RuntimeError(
            "ALPACA_API_KEY ve ALPACA_SECRET_KEY .env dosyasinda tanimli olmali."
        )
    return api_key, secret_key


def parse_utc_date(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_symbols(symbols, symbols_file):
    result = []
    if symbols:
        result.extend(symbols)

    if symbols_file:
        path = Path(symbols_file)
        for line in path.read_text(encoding="utf-8").splitlines():
            symbol = line.strip().split(",")[0].upper()
            if symbol and symbol not in {"SYMBOL", "TICKER"}:
                result.append(symbol)

    return list(dict.fromkeys(symbol.upper() for symbol in result if symbol.strip()))


def normalize_bars(raw_bars, symbol, rth_only=True):
    if raw_bars.empty:
        return pd.DataFrame()

    bars = raw_bars.copy()
    if isinstance(bars.index, pd.MultiIndex):
        symbol_level = "symbol" if "symbol" in bars.index.names else bars.index.names[0]
        bars = bars.xs(symbol, level=symbol_level)

    bars.index = pd.to_datetime(bars.index, utc=True)
    bars.index = bars.index.tz_convert(NEW_YORK)
    bars = bars.sort_index()

    if rth_only:
        bars = bars.between_time("09:30", "15:59", inclusive="both")

    bars = bars.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    required = ["Open", "High", "Low", "Close", "Volume"]
    bars = bars[required].dropna()
    bars.index.name = "Date"
    return bars.reset_index()


def download_symbol(
    client,
    symbol,
    start,
    end,
    minutes,
    feed,
    output_dir,
    rth_only=True,
):
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(minutes, TimeFrameUnit.Minute),
        start=start,
        end=end,
        adjustment=Adjustment.ALL,
        feed=feed,
    )
    response = client.get_stock_bars(request)
    bars = normalize_bars(response.df, symbol, rth_only=rth_only)
    if bars.empty:
        return None, 0

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{symbol}_{minutes}min.csv"
    bars.to_csv(output_path, index=False)
    return output_path, len(bars)


def download_history(
    symbols,
    start,
    end,
    minutes=1,
    feed_name="iex",
    output_dir=BASE_DIR / "hisseler",
    rth_only=True,
    retries=3,
):
    api_key, secret_key = get_credentials()
    client = StockHistoricalDataClient(api_key, secret_key)
    feed = DataFeed.SIP if feed_name.lower() == "sip" else DataFeed.IEX

    for position, symbol in enumerate(symbols, 1):
        print(f"[{position}/{len(symbols)}] {symbol}: {minutes}m veri indiriliyor...")
        for attempt in range(1, retries + 1):
            try:
                path, row_count = download_symbol(
                    client=client,
                    symbol=symbol,
                    start=start,
                    end=end,
                    minutes=minutes,
                    feed=feed,
                    output_dir=Path(output_dir),
                    rth_only=rth_only,
                )
                if path is None:
                    print("  Veri bulunamadi.")
                else:
                    print(f"  {row_count:,} bar kaydedildi: {path}")
                break
            except Exception as error:
                if attempt == retries:
                    print(f"  Hata: {error}")
                    break
                wait_seconds = attempt * 2
                print(f"  Deneme {attempt} basarisiz: {error}; {wait_seconds}s bekleniyor.")
                time.sleep(wait_seconds)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Alpaca Market Data API ile backtest OHLCV verisi indirir."
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=["QQQ","SPY"]
        #default=["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "ORCL", "IBM", "INTC", "AMD", "QCOM", "AVGO", "MU", "AMAT", "JPM", "BAC", "GS", "MS", "WFC", "C", "V", "MA", "AXP", "BLK", "SCHW", "PNC", "USB", "COF", "TFC", "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "TMO", "ABT", "DHR", "AMGN", "GILD", "BIIB", "REGN", "VRTX", "BMY", "WMT", "HD", "COST", "NKE", "MCD", "SBUX", "TGT", "LOW", "TJX", "BKNG", "CMG", "YUM", "DG", "DLTR", "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "HAL", "BKR", "DVN", "FANG", "HES", "MRO", "CAT", "HON", "UPS", "RTX", "GE", "LMT", "DE", "BA", "MMM", "FDX", "EMR", "ETN", "PH", "ROK", "ITW", "NFLX", "DIS", "CMCSA", "T", "VZ", "CHTR", "PARA", "WBD", "LUMN", "SNAP", "PINS", "SPOT"],
    )
    parser.add_argument("--symbols-file")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2024-01-01")
    parser.add_argument("--minutes", type=int, choices=[1, 2, 5, 10], default=1)
    parser.add_argument("--feed", choices=["iex", "sip"], default="iex")
    parser.add_argument("--output-dir", default=str(BASE_DIR / "hisseler"))
    parser.add_argument(
        "--include-extended-hours",
        action="store_true",
        help="Pre-market ve after-hours verilerini de kaydeder.",
    )
    return parser


def main():
    args = build_parser().parse_args()
    symbols = load_symbols(args.symbols, args.symbols_file)
    if not symbols:
        raise SystemExit("Indirilecek sembol bulunamadi.")

    download_history(
        symbols=symbols,
        start=parse_utc_date(args.start),
        end=parse_utc_date(args.end),
        minutes=args.minutes,
        feed_name=args.feed,
        output_dir=Path(args.output_dir),
        rth_only=not args.include_extended_hours,
    )


if __name__ == "__main__":
    main()
