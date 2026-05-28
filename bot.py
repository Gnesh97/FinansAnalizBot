import contextlib
import csv
import io
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import schedule
import time
import yfinance as yf
from yfinance import set_tz_cache_location

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
YFINANCE_CACHE_DIR = BASE_DIR / ".yfinance_cache"
YFINANCE_CACHE_DIR.mkdir(exist_ok=True)
set_tz_cache_location(str(YFINANCE_CACHE_DIR))
YAHOO_CHART_HOSTS = (
    "https://query2.finance.yahoo.com",
    "https://query1.finance.yahoo.com",
)
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}
NASDAQ_HEADERS = {
    **HTTP_HEADERS,
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
PREFER_ALPHA_VANTAGE = os.getenv("PREFER_ALPHA_VANTAGE", "").strip() == "1"
ALPHA_VANTAGE_DISABLED = False

with contextlib.redirect_stdout(io.StringIO()):
    from smartmoneyconcepts import smc


TELEGRAM_BOT_TOKEN = "8980730872:AAGLtRvzNQKpWu67Wxey9fZ2KzJQpb1Un5U"
TELEGRAM_CHAT_ID = "753110584"
TELEGRAM_TARAMA_OZETI = os.getenv("TELEGRAM_TARAMA_OZETI", "0").strip() == "1"
TELEGRAM_KARAR_MESAJLARI = os.getenv("TELEGRAM_KARAR_MESAJLARI", "1").strip() != "0"

PORTFOY_BUYUKLUGU = 10000 
RİSK_YUZDESI = 0.01       


CSV_DOSYASI = BASE_DIR / "trade_loglari.csv"
CSV_BASLIKLARI = [
    "Tarih",
    "Hisse",
    "Fiyat",
    "Aksiyon",
    "Giris",
    "Hedef",
    "Stop",
    "Lot_Miktari",
    "Risk_Maliyeti",
    "RSI",
    "ATR",
    "Analiz_1H",
    "Analiz_4H",
    "Destek_Direnc_1H",
    "Destek_Direnc_4H",
    "FVG_Yon",
    "FVG_Yon_4H",
    "BOS_Yon",
    "BOS_Yon_4H",
    "CHOCH_Yon",
    "CHOCH_Yon_4H",
    "Veri_Kaynagi",
    "Veri_Periyodu",
    "Gerekce",
]

def ensure_csv_file():
    if os.path.exists(CSV_DOSYASI):
        try:
            with open(CSV_DOSYASI, mode='r', newline='', encoding='utf-8') as file:
                reader = csv.reader(file)
                mevcut_baslik = next(reader, [])
            if mevcut_baslik == CSV_BASLIKLARI:
                return
        except Exception:
            pass

        yedek_dosya = CSV_DOSYASI.with_name(
            f"{CSV_DOSYASI.stem}_eski_{datetime.now().strftime('%Y%m%d_%H%M%S')}{CSV_DOSYASI.suffix}"
        )
        os.replace(CSV_DOSYASI, yedek_dosya)
        print(f"CSV başlığı güncellendi. Eski dosya yedeklendi: {yedek_dosya.name}")

    with open(CSV_DOSYASI, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(CSV_BASLIKLARI)

ensure_csv_file()

def log_to_csv(veri_listesi):
    try:
        with open(CSV_DOSYASI, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(veri_listesi)
        return True
    except Exception as err:
        print(f"CSV yazma hatası: {err}")
        return False

def send_telegram_message(message, parse_mode=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as err:
        print(f"Telegram gönderim hatası: {err}")
        return False

def brute_force_cleaner(df):
    """yfinance verilerini zorla OHLCV formatına çevirir."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(col[0]).lower().strip() for col in df.columns]
    else:
        df.columns = [str(col).lower().strip() for col in df.columns]
    
    mevcut_sutunlar = df.columns.tolist()
    gerekli_sutunlar = ['open', 'high', 'low', 'close', 'volume']
    
    for sutun in gerekli_sutunlar:
        if sutun not in mevcut_sutunlar:
            raise ValueError(f"Eksik Sütun: '{sutun}'")
            
    df = df[gerekli_sutunlar].copy()
    df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
    
    if isinstance(df.index, pd.DatetimeIndex):
        df.index = df.index.tz_localize(None)
        
    return df

def source_df(df, source):
    df.attrs["source"] = source
    return df

def period_to_days(period):
    amount = int("".join(ch for ch in period if ch.isdigit()) or "1")
    if period.endswith("d"):
        return amount
    if period.endswith("mo"):
        return amount * 40
    if period.endswith("y"):
        return amount * 370
    return 120

def parse_market_number(value):
    if value is None:
        return np.nan
    return pd.to_numeric(str(value).replace("$", "").replace(",", "").strip(), errors="coerce")

def short_provider_message(error):
    message = str(error).replace("\n", " ").strip()
    if "demo" in message and "API key" in message:
        return "demo API key sadece örnek isteklerde çalışıyor"
    if "premium feature" in message:
        return "ücretsiz planda bu parametre desteklenmiyor"
    if "rate limit" in message or "25 requests per day" in message:
        return "ücretsiz API limiti doldu"
    if "1 request per second" in message:
        return "saniyelik API limiti"
    if len(message) > 140:
        return message[:137] + "..."
    return message

def should_disable_alpha_vantage(error):
    message = str(error).lower()
    return any(
        phrase in message
        for phrase in (
            "premium",
            "rate limit",
            "25 requests per day",
            "demo",
            "thank you for using alpha vantage",
        )
    )

def disable_alpha_vantage_if_needed(error):
    global ALPHA_VANTAGE_DISABLED
    if should_disable_alpha_vantage(error):
        ALPHA_VANTAGE_DISABLED = True

def alpha_vantage_history(ticker_symbol, period, interval):
    if not ALPHA_VANTAGE_API_KEY:
        raise ValueError("ALPHA_VANTAGE_API_KEY tanımlı değil.")

    params = {"symbol": ticker_symbol, "apikey": ALPHA_VANTAGE_API_KEY}
    if interval in ("1h", "60m", "60min"):
        params.update(
            {
                "function": "TIME_SERIES_INTRADAY",
                "interval": "60min",
                "outputsize": "compact",
                "adjusted": "false",
                "extended_hours": "false",
            }
        )
    elif interval in ("1d", "1day"):
        params.update({"function": "TIME_SERIES_DAILY", "outputsize": "compact"})
    else:
        raise ValueError(f"Alpha Vantage desteklenmeyen interval: {interval}")

    response = requests.get(
        "https://www.alphavantage.co/query",
        params=params,
        headers=HTTP_HEADERS,
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()

    for key in ("Error Message", "Note", "Information"):
        if key in payload:
            raise ValueError(short_provider_message(payload[key]))

    series_key = next((key for key in payload if key.startswith("Time Series")), None)
    if not series_key:
        raise ValueError("Alpha Vantage time series alanı bulunamadı.")

    df = pd.DataFrame.from_dict(payload[series_key], orient="index")
    rename_map = {
        "1. open": "Open",
        "2. high": "High",
        "3. low": "Low",
        "4. close": "Close",
        "5. volume": "Volume",
    }
    df = df.rename(columns=rename_map)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    for column in ["Open", "High", "Low", "Close", "Volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    start_date = datetime.now() - timedelta(days=period_to_days(period))
    df = df.loc[df.index >= start_date, ["Open", "High", "Low", "Close", "Volume"]]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if df.empty:
        raise ValueError("Alpha Vantage verisi temizlendikten sonra boş kaldı.")

    return source_df(df, "Alpha Vantage")

def yahoo_chart_history(ticker_symbol, period, interval):
    """Yahoo chart API'den OHLCV verisini yfinance cache/cookie katmanına girmeden çeker."""
    params = {
        "range": period,
        "interval": interval,
        "events": "history",
        "includeAdjustedClose": "true",
    }
    last_error = None

    for host in YAHOO_CHART_HOSTS:
        try:
            response = requests.get(
                f"{host}/v8/finance/chart/{ticker_symbol}",
                params=params,
                headers=HTTP_HEADERS,
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            chart = payload.get("chart", {})

            if chart.get("error"):
                raise ValueError(chart["error"])

            results = chart.get("result") or []
            if not results:
                raise ValueError("Yahoo chart sonucu boş döndü.")

            result = results[0]
            timestamps = result.get("timestamp") or []
            quote_items = result.get("indicators", {}).get("quote") or []
            if not timestamps or not quote_items:
                raise ValueError("Yahoo chart OHLCV alanları eksik döndü.")

            quote = quote_items[0]
            df = pd.DataFrame(
                {
                    "Open": quote.get("open"),
                    "High": quote.get("high"),
                    "Low": quote.get("low"),
                    "Close": quote.get("close"),
                    "Volume": quote.get("volume"),
                }
            )
            timezone = result.get("meta", {}).get("exchangeTimezoneName", "UTC")
            df.index = pd.to_datetime(timestamps, unit="s", utc=True)
            df.index = df.index.tz_convert(timezone).tz_localize(None)

            for column in ["Open", "High", "Low", "Close", "Volume"]:
                df[column] = pd.to_numeric(df[column], errors="coerce")

            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if df.empty:
                raise ValueError("Yahoo chart verisi temizlendikten sonra boş kaldı.")

            return source_df(df, "Yahoo Chart")
        except Exception as err:
            last_error = err

    raise ValueError(f"Yahoo chart verisi alınamadı: {last_error}")

def nasdaq_daily_history(ticker_symbol, period):
    today = datetime.now().date()
    start_date = today - timedelta(days=period_to_days(period))
    response = requests.get(
        f"https://api.nasdaq.com/api/quote/{ticker_symbol}/historical",
        params={
            "assetclass": "stocks",
            "fromdate": start_date.isoformat(),
            "todate": today.isoformat(),
            "limit": "9999",
        },
        headers=NASDAQ_HEADERS,
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    rows = (
        payload.get("data", {})
        .get("tradesTable", {})
        .get("rows", [])
    )
    if not rows:
        raise ValueError("Nasdaq günlük veri boş döndü.")

    records = []
    for row in rows:
        records.append(
            {
                "Date": pd.to_datetime(row.get("date")),
                "Open": parse_market_number(row.get("open")),
                "High": parse_market_number(row.get("high")),
                "Low": parse_market_number(row.get("low")),
                "Close": parse_market_number(row.get("close")),
                "Volume": parse_market_number(row.get("volume")),
            }
        )

    df = pd.DataFrame(records).set_index("Date").sort_index()
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if df.empty:
        raise ValueError("Nasdaq günlük veri temizlendikten sonra boş kaldı.")

    return source_df(df, "Nasdaq Daily")

def nasdaq_intraday_chart(ticker_symbol, interval):
    response = requests.get(
        f"https://api.nasdaq.com/api/quote/{ticker_symbol}/chart",
        params={"assetclass": "stocks"},
        headers=NASDAQ_HEADERS,
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("data", {}).get("chart", [])
    if not chart:
        raise ValueError("Nasdaq intraday chart boş döndü.")

    df = pd.DataFrame(
        {
            "Date": [pd.to_datetime(item.get("x"), unit="ms", errors="coerce") for item in chart],
            "Close": [parse_market_number(item.get("y")) for item in chart],
        }
    ).dropna()
    if df.empty:
        raise ValueError("Nasdaq intraday chart temizlendikten sonra boş kaldı.")

    df = df.set_index("Date").sort_index()
    rule = "1h" if interval in ("1h", "60m", "60min") else interval
    ohlc = df["Close"].resample(rule).ohlc().dropna()
    ohlc.columns = ["Open", "High", "Low", "Close"]
    ohlc["Volume"] = 0
    if ohlc.empty:
        raise ValueError("Nasdaq intraday chart OHLC üretilemedi.")

    return source_df(ohlc, "Nasdaq Intraday")

def get_price_history(ticker_symbol, period, interval):
    if PREFER_ALPHA_VANTAGE and not ALPHA_VANTAGE_DISABLED:
        try:
            return alpha_vantage_history(ticker_symbol, period, interval)
        except Exception as alpha_err:
            disable_alpha_vantage_if_needed(alpha_err)
            if ALPHA_VANTAGE_API_KEY:
                print(f"[{ticker_symbol}] Alpha Vantage atlandı: {short_provider_message(alpha_err)}")

    try:
        return yahoo_chart_history(ticker_symbol, period, interval)
    except Exception as chart_err:
        print(f"[{ticker_symbol}] Yahoo chart uyarı: {chart_err}")

    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=False)
        if df.empty:
            raise ValueError(f"{period}/{interval} veri boş döndü.")
        return source_df(brute_force_cleaner(df), "yfinance")
    except Exception as yf_err:
        print(f"[{ticker_symbol}] yfinance uyarı: {yf_err}")

    if interval in ("1d", "1day"):
        return nasdaq_daily_history(ticker_symbol, period)

    try:
        return nasdaq_intraday_chart(ticker_symbol, interval)
    except Exception as nasdaq_err:
        print(f"[{ticker_symbol}] Nasdaq intraday uyarı: {short_provider_message(nasdaq_err)}")

    if ALPHA_VANTAGE_API_KEY and not PREFER_ALPHA_VANTAGE and not ALPHA_VANTAGE_DISABLED:
        try:
            return alpha_vantage_history(ticker_symbol, period, interval)
        except Exception as alpha_err:
            disable_alpha_vantage_if_needed(alpha_err)
            print(f"[{ticker_symbol}] Alpha Vantage atlandı: {short_provider_message(alpha_err)}")

    raise ValueError(f"{period}/{interval} için hiçbir veri kaynağı çalışmadı.")

def calculate_rsi(close_series, length=14):
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_atr(df, length=14):
    prev_close = df["Close"].shift(1)
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - prev_close).abs()
    low_close = (df["Low"] - prev_close).abs()

    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    return atr

def analyze_ema(fiyat, ema_20, ema_50, ema_200):
    if pd.isna(ema_20) or pd.isna(ema_50) or pd.isna(ema_200):
        return "EMA verisi yetersiz"
    if fiyat > ema_20 > ema_50 > ema_200:
        return "Güçlü yükseliş dizilimi"
    if fiyat < ema_20 < ema_50 < ema_200:
        return "Güçlü düşüş dizilimi"
    if fiyat > ema_200 and ema_20 > ema_50:
        return "Yükseliş eğilimi"
    if fiyat < ema_200 and ema_20 < ema_50:
        return "Düşüş eğilimi"
    return "Karışık/kararsız"

def analyze_volume(volume, volume_sma_20):
    if pd.isna(volume) or pd.isna(volume_sma_20) or volume_sma_20 == 0:
        return "Hacim verisi yetersiz"
    ratio = volume / volume_sma_20
    if ratio >= 1.5:
        return f"Yüksek hacim ({ratio:.2f}x)"
    if ratio <= 0.7:
        return f"Düşük hacim ({ratio:.2f}x)"
    return f"Normal hacim ({ratio:.2f}x)"

def resample_ohlcv(df, rule):
    ohlc = df.resample(rule).agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
    )
    return ohlc.dropna(subset=["Open", "High", "Low", "Close"])

def add_timeframe_indicators(df):
    df = df.copy()
    df["RSI_14"] = calculate_rsi(df["Close"], length=14)
    df["ATRr_14"] = calculate_atr(df, length=14)
    df["EMA_20"] = df["Close"].ewm(span=20, adjust=False, min_periods=20).mean()
    df["EMA_50"] = df["Close"].ewm(span=50, adjust=False, min_periods=50).mean()
    df["Volume_SMA_20"] = df["Volume"].rolling(20).mean()
    return df

def timeframe_analysis(df, ema_200, fallback_volume=None, fallback_volume_sma=None):
    son_mum = df.dropna(subset=["RSI_14", "ATRr_14"]).iloc[-1]
    volume = son_mum["Volume"]
    volume_sma_20 = son_mum["Volume_SMA_20"]
    if pd.isna(volume) or volume == 0 or pd.isna(volume_sma_20) or volume_sma_20 == 0:
        volume = fallback_volume
        volume_sma_20 = fallback_volume_sma

    ema_analiz = analyze_ema(float(son_mum["Close"]), son_mum["EMA_20"], son_mum["EMA_50"], ema_200)
    hacim_analiz = analyze_volume(volume, volume_sma_20)
    return {
        "RSI": float(round(son_mum["RSI_14"], 2)),
        "ATR": float(round(son_mum["ATRr_14"], 2)),
        "EMA_Analiz": ema_analiz,
        "Hacim_Analiz": hacim_analiz,
        "Analiz": f"{ema_analiz} | {hacim_analiz}",
    }

def calculate_support_resistance(df, current_price, lookback=120, pivot_window=2):
    recent = df.tail(lookback).copy()
    if len(recent) < (pivot_window * 2 + 1):
        return {
            "Destek": None,
            "Direnc": None,
            "Analiz": "Destek/direnç verisi yetersiz",
        }

    lows = recent["Low"]
    highs = recent["High"]
    pivot_lows = []
    pivot_highs = []

    for i in range(pivot_window, len(recent) - pivot_window):
        low = lows.iloc[i]
        high = highs.iloc[i]
        if low == lows.iloc[i - pivot_window:i + pivot_window + 1].min():
            pivot_lows.append(float(low))
        if high == highs.iloc[i - pivot_window:i + pivot_window + 1].max():
            pivot_highs.append(float(high))

    destekler = [level for level in pivot_lows if level < current_price]
    direncler = [level for level in pivot_highs if level > current_price]
    destek = max(destekler) if destekler else None
    direnc = min(direncler) if direncler else None

    if destek is None and direnc is None:
        analiz = "Yakın destek/direnç bulunamadı"
    elif destek is None:
        direnc_uzaklik = ((direnc - current_price) / current_price) * 100
        analiz = f"Destek yok | Direnç ${direnc:.2f} (+{direnc_uzaklik:.2f}%)"
    elif direnc is None:
        destek_uzaklik = ((current_price - destek) / current_price) * 100
        analiz = f"Destek ${destek:.2f} (-{destek_uzaklik:.2f}%) | Direnç yok"
    else:
        destek_uzaklik = ((current_price - destek) / current_price) * 100
        direnc_uzaklik = ((direnc - current_price) / current_price) * 100
        analiz = f"Destek ${destek:.2f} (-{destek_uzaklik:.2f}%) | Direnç ${direnc:.2f} (+{direnc_uzaklik:.2f}%)"

    return {
        "Destek": None if destek is None else round(destek, 2),
        "Direnc": None if direnc is None else round(direnc, 2),
        "Analiz": analiz,
    }

def trend_is_bullish(analysis_text):
    return "yükseliş" in analysis_text.lower()

def trend_is_bearish(analysis_text):
    return "düşüş" in analysis_text.lower()

def indicator_signals(paket):
    fiyat = paket["Guncel_Fiyat"]
    rsi = paket["RSI"]
    fvg_yon = paket["FVG_Yon"]
    fvg_yon_4h = paket.get("FVG_Yon_4H", 0)
    bos_yon = paket.get("BOS_Yon", 0)
    bos_yon_4h = paket.get("BOS_Yon_4H", 0)
    choch_yon = paket.get("CHOCH_Yon", 0)
    choch_yon_4h = paket.get("CHOCH_Yon_4H", 0)
    destek_1h = paket.get("Destek_1H")
    direnc_1h = paket.get("Direnc_1H")
    destek_4h = paket.get("Destek_4H")
    direnc_4h = paket.get("Direnc_4H")

    sinyaller = {
        "EMA 1H": "AL" if trend_is_bullish(paket["EMA_Analiz"]) else "SAT" if trend_is_bearish(paket["EMA_Analiz"]) else "BEKLE",
        "EMA 4H": "AL" if trend_is_bullish(paket["EMA_Analiz_4H"]) else "SAT" if trend_is_bearish(paket["EMA_Analiz_4H"]) else "BEKLE",
        "RSI": "SAT" if rsi >= 75 else "AL" if rsi <= 30 else "BEKLE",
        "Hacim 1H": "AL" if "Yüksek hacim" in paket["Hacim_Analiz"] and trend_is_bullish(paket["EMA_Analiz"]) else "SAT" if "Yüksek hacim" in paket["Hacim_Analiz"] and trend_is_bearish(paket["EMA_Analiz"]) else "BEKLE",
        "FVG 1H": "AL" if fvg_yon == 1 else "SAT" if fvg_yon == -1 else "BEKLE",
        "FVG 4H": "AL" if fvg_yon_4h == 1 else "SAT" if fvg_yon_4h == -1 else "BEKLE",
        "BOS 1H": "AL" if bos_yon == 1 else "SAT" if bos_yon == -1 else "BEKLE",
        "BOS 4H": "AL" if bos_yon_4h == 1 else "SAT" if bos_yon_4h == -1 else "BEKLE",
        "CHOCH 1H": "AL" if choch_yon == 1 else "SAT" if choch_yon == -1 else "BEKLE",
        "CHOCH 4H": "AL" if choch_yon_4h == 1 else "SAT" if choch_yon_4h == -1 else "BEKLE",
    }

    def support_resistance_signal(destek, direnc):
        destek_yakin = destek is not None and fiyat > 0 and ((fiyat - destek) / fiyat) <= 0.01
        direnc_yakin = direnc is not None and fiyat > 0 and ((direnc - fiyat) / fiyat) <= 0.01
        if destek_yakin and not direnc_yakin:
            return "AL"
        if direnc_yakin and not destek_yakin:
            return "SAT"
        return "BEKLE"

    sinyaller["Destek/Direnç 1H"] = support_resistance_signal(destek_1h, direnc_1h)
    sinyaller["Destek/Direnç 4H"] = support_resistance_signal(destek_4h, direnc_4h)
    return sinyaller

def get_market_data(ticker_symbol, max_deneme=3):
    """Piyasa verilerini çeker ve indikatörleri hesaplar."""
    for deneme in range(max_deneme):
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {ticker_symbol} taranıyor (Deneme {deneme+1})...")
            
            # Günlük Veri
            df_daily = get_price_history(ticker_symbol, period="1y", interval="1d")
                
            guncel_fiyat = float(df_daily['Close'].iloc[-1])
            sma_50 = float(df_daily['Close'].rolling(50).mean().iloc[-1])
            makro_trend = "Yukselis" if guncel_fiyat > sma_50 else "Dusus"
            df_daily["EMA_20"] = df_daily["Close"].ewm(span=20, adjust=False, min_periods=20).mean()
            df_daily["EMA_50"] = df_daily["Close"].ewm(span=50, adjust=False, min_periods=50).mean()
            df_daily["EMA_200"] = df_daily["Close"].ewm(span=200, adjust=False, min_periods=200).mean()
            df_daily["Volume_SMA_20"] = df_daily["Volume"].rolling(20).mean()
            
            # Saatlik Veri
            try:
                df_hourly = get_price_history(ticker_symbol, period="3mo", interval="1h")
                veri_periyodu = "1h"
            except Exception as hourly_err:
                print(f"[{ticker_symbol}] Saatlik veri uyarı: {hourly_err}. Günlük veriyle devam ediliyor.")
                df_hourly = df_daily.copy()
                df_hourly.attrs["source"] = f"{df_daily.attrs.get('source', 'Daily')} / daily fallback"
                veri_periyodu = "1d"

            if len(df_hourly) < 20 and veri_periyodu != "1d":
                print(f"[{ticker_symbol}] Saatlik veri yetersiz ({len(df_hourly)} mum). Günlük veriyle devam ediliyor.")
                df_hourly = df_daily.copy()
                df_hourly.attrs["source"] = f"{df_daily.attrs.get('source', 'Daily')} / daily fallback"
                veri_periyodu = "1d"
            
            df_hourly = add_timeframe_indicators(df_hourly)
            df_4h = add_timeframe_indicators(resample_ohlcv(df_hourly[["Open", "High", "Low", "Close", "Volume"]], "4h"))
            
            # SMC Entegrasyonu
            try:
                ohlc = df_hourly[['Open', 'High', 'Low', 'Close']].reset_index(drop=True)
                fvg_df = smc.fvg(ohlc, join_consecutive=True)
                if isinstance(fvg_df, pd.DataFrame) and 'FVG' in fvg_df.columns:
                    df_hourly['FVG'] = fvg_df['FVG'].to_numpy()
                elif isinstance(fvg_df, pd.Series):
                    df_hourly['FVG'] = np.asarray(fvg_df)
                else:
                    df_hourly['FVG'] = 0
            except Exception as smc_err:
                print(f"[{ticker_symbol}] SMC FVG uyarı: {smc_err}")
                df_hourly['FVG'] = 0
            
            try:
                ohlc = df_hourly[['Open', 'High', 'Low', 'Close']].reset_index(drop=True)
                bos_df = smc.bos(ohlc)
                if isinstance(bos_df, pd.DataFrame) and 'BOS' in bos_df.columns:
                    df_hourly['BOS'] = bos_df['BOS'].to_numpy()
                elif isinstance(bos_df, pd.Series):
                    df_hourly['BOS'] = np.asarray(bos_df)
                else:
                    df_hourly['BOS'] = 0
            except Exception as bos_err:
                print(f"[{ticker_symbol}] SMC BOS uyarı: {bos_err}")
                df_hourly['BOS'] = 0
            
            try:
                ohlc = df_hourly[['Open', 'High', 'Low', 'Close']].reset_index(drop=True)
                choch_df = smc.choch(ohlc)
                if isinstance(choch_df, pd.DataFrame) and 'CHOCH' in choch_df.columns:
                    df_hourly['CHOCH'] = choch_df['CHOCH'].to_numpy()
                elif isinstance(choch_df, pd.Series):
                    df_hourly['CHOCH'] = np.asarray(choch_df)
                else:
                    df_hourly['CHOCH'] = 0
            except Exception as choch_err:
                print(f"[{ticker_symbol}] SMC CHOCH uyarı: {choch_err}")
                df_hourly['CHOCH'] = 0

            # İlk indikatör satırlarını burada ayıkla; SMC hesaplaması artık doğru hizalanmış durumda.
            df_hourly = df_hourly.dropna(subset=['RSI_14', 'ATRr_14'])

            # KRİTİK ÇÖZÜM: SMC boşluklarını silmek yerine 0 yaz (Veri kaybını engeller)
            df_hourly['FVG'] = df_hourly['FVG'].fillna(0)
            df_hourly['BOS'] = df_hourly['BOS'].fillna(0)
            df_hourly['CHOCH'] = df_hourly['CHOCH'].fillna(0)
            
            # 4H için SMC Entegrasyonu
            try:
                ohlc_4h = df_4h[['Open', 'High', 'Low', 'Close']].reset_index(drop=True)
                fvg_4h = smc.fvg(ohlc_4h, join_consecutive=True)
                if isinstance(fvg_4h, pd.DataFrame) and 'FVG' in fvg_4h.columns:
                    df_4h['FVG'] = fvg_4h['FVG'].to_numpy()
                elif isinstance(fvg_4h, pd.Series):
                    df_4h['FVG'] = np.asarray(fvg_4h)
                else:
                    df_4h['FVG'] = 0
            except Exception:
                df_4h['FVG'] = 0
            
            try:
                ohlc_4h = df_4h[['Open', 'High', 'Low', 'Close']].reset_index(drop=True)
                bos_4h = smc.bos(ohlc_4h)
                if isinstance(bos_4h, pd.DataFrame) and 'BOS' in bos_4h.columns:
                    df_4h['BOS'] = bos_4h['BOS'].to_numpy()
                elif isinstance(bos_4h, pd.Series):
                    df_4h['BOS'] = np.asarray(bos_4h)
                else:
                    df_4h['BOS'] = 0
            except Exception:
                df_4h['BOS'] = 0
            
            try:
                ohlc_4h = df_4h[['Open', 'High', 'Low', 'Close']].reset_index(drop=True)
                choch_4h = smc.choch(ohlc_4h)
                if isinstance(choch_4h, pd.DataFrame) and 'CHOCH' in choch_4h.columns:
                    df_4h['CHOCH'] = choch_4h['CHOCH'].to_numpy()
                elif isinstance(choch_4h, pd.Series):
                    df_4h['CHOCH'] = np.asarray(choch_4h)
                else:
                    df_4h['CHOCH'] = 0
            except Exception:
                df_4h['CHOCH'] = 0
            
            df_4h['FVG'] = df_4h['FVG'].fillna(0)
            df_4h['BOS'] = df_4h['BOS'].fillna(0)
            df_4h['CHOCH'] = df_4h['CHOCH'].fillna(0)
            
            # Tabloda veri kaldığından emin ol
            if len(df_hourly) == 0:
                raise ValueError("Hesaplamalardan sonra tablo tamamen boşaldı!")
                
            son_mum = df_hourly.iloc[-1]
            son_mum_4h = df_4h.iloc[-1]
            son_gun = df_daily.iloc[-1]
            ema_200 = son_gun["EMA_200"]
            if pd.isna(ema_200):
                ema_200 = son_gun["EMA_50"]

            analiz_1h = timeframe_analysis(
                df_hourly,
                ema_200,
                fallback_volume=son_gun["Volume"],
                fallback_volume_sma=son_gun["Volume_SMA_20"],
            )
            analiz_4h = timeframe_analysis(
                df_4h,
                ema_200,
                fallback_volume=son_gun["Volume"],
                fallback_volume_sma=son_gun["Volume_SMA_20"],
            )
            destek_direnc_1h = calculate_support_resistance(df_hourly, guncel_fiyat)
            destek_direnc_4h = calculate_support_resistance(df_4h, guncel_fiyat, lookback=80)

            return {
                "Hisse": ticker_symbol,
                "Guncel_Fiyat": round(guncel_fiyat, 2),
                "Makro_Trend": makro_trend,
                "RSI": analiz_1h["RSI"],
                "ATR": analiz_1h["ATR"],
                "Analiz_1H": analiz_1h["Analiz"],
                "Analiz_4H": analiz_4h["Analiz"],
                "EMA_Analiz": analiz_1h["EMA_Analiz"],
                "EMA_Analiz_4H": analiz_4h["EMA_Analiz"],
                "Hacim_Analiz": analiz_1h["Hacim_Analiz"],
                "Hacim_Analiz_4H": analiz_4h["Hacim_Analiz"],
                "Destek_Direnc_1H": destek_direnc_1h["Analiz"],
                "Destek_Direnc_4H": destek_direnc_4h["Analiz"],
                "Destek_1H": destek_direnc_1h["Destek"],
                "Direnc_1H": destek_direnc_1h["Direnc"],
                "Destek_4H": destek_direnc_4h["Destek"],
                "Direnc_4H": destek_direnc_4h["Direnc"],
                "FVG_Yon": int(0 if pd.isna(son_mum.get('FVG', 0)) else son_mum.get('FVG', 0)),
                "FVG_Yon_4H": int(0 if pd.isna(son_mum_4h.get('FVG', 0)) else son_mum_4h.get('FVG', 0)),
                "BOS_Yon": int(0 if pd.isna(son_mum.get('BOS', 0)) else son_mum.get('BOS', 0)),
                "BOS_Yon_4H": int(0 if pd.isna(son_mum_4h.get('BOS', 0)) else son_mum_4h.get('BOS', 0)),
                "CHOCH_Yon": int(0 if pd.isna(son_mum.get('CHOCH', 0)) else son_mum.get('CHOCH', 0)),
                "CHOCH_Yon_4H": int(0 if pd.isna(son_mum_4h.get('CHOCH', 0)) else son_mum_4h.get('CHOCH', 0)),
                "Veri_Kaynagi": df_hourly.attrs.get("source", "Bilinmiyor"),
                "Veri_Periyodu": veri_periyodu,
            }
            
        except Exception as e:
            print(f"❌ [{ticker_symbol}] HATA DETAYI: {repr(e)}")
            time.sleep(3)
            
    print(f"⚠️ {ticker_symbol} verisi alınamadı, pas geçiliyor.\n")
    return None

def rule_based_engine(paket):
    trend = paket["Makro_Trend"]
    fiyat = paket["Guncel_Fiyat"]
    rsi = paket["RSI"]
    atr = paket["ATR"]
    fvg_yon = paket["FVG_Yon"]
    ema_analiz = paket["EMA_Analiz"]
    ema_analiz_4h = paket["EMA_Analiz_4H"]
    analiz_1h = paket["Analiz_1H"]
    analiz_4h = paket["Analiz_4H"]
    destek_direnc_1h = paket["Destek_Direnc_1H"]
    destek_direnc_4h = paket["Destek_Direnc_4H"]
    bullish_1h = trend_is_bullish(ema_analiz)
    bullish_4h = trend_is_bullish(ema_analiz_4h)
    bearish_1h = trend_is_bearish(ema_analiz)
    bearish_4h = trend_is_bearish(ema_analiz_4h)
    
    # RASYONEL KURAL MOTORU
    if trend == "Yukselis" and bullish_1h and bullish_4h and rsi < 75:
        return {
            "aksiyon": "AL",
            "gerekce": f"Trend UP + 1H/4H EMA uyumu + RSI({rsi}) | 1H: {analiz_1h} | 4H: {analiz_4h} | S/R 1H: {destek_direnc_1h} | S/R 4H: {destek_direnc_4h} | FVG: {fvg_yon}",
            "stop_loss": round(fiyat - (atr * 1.5), 2),
            "hedef_fiyat": round(fiyat + (atr * 3.0), 2)
        }
    elif trend == "Dusus" and bearish_1h and bearish_4h and rsi > 25:
        return {
            "aksiyon": "SAT",
            "gerekce": f"Trend DOWN + 1H/4H EMA uyumu + RSI({rsi}) | 1H: {analiz_1h} | 4H: {analiz_4h} | S/R 1H: {destek_direnc_1h} | S/R 4H: {destek_direnc_4h} | FVG: {fvg_yon}",
            "stop_loss": round(fiyat + (atr * 1.5), 2),
            "hedef_fiyat": round(fiyat - (atr * 3.0), 2)
        }
    return {"aksiyon": "BEKLE"}

def calculate_position_size(fiyat, stop_loss):
    if stop_loss <= 0 or fiyat <= 0 or fiyat == stop_loss: return 0
    risk_edilen_tutar = PORTFOY_BUYUKLUGU * RİSK_YUZDESI
    hisse_basina_risk = abs(fiyat - stop_loss)
    return int(risk_edilen_tutar / hisse_basina_risk)

def build_decision_message(paket, karar, lot, gerekce):
    aksiyon = karar["aksiyon"]
    sinyaller = indicator_signals(paket)
    
    satirlar = [
        f"{paket['Hisse']} KARAR: {aksiyon}",
        f"Fiyat: ${paket['Guncel_Fiyat']}",
        f"RSI: {paket['RSI']} | ATR: {paket['ATR']}",
        f"1H: {paket['Analiz_1H']}",
        f"4H: {paket['Analiz_4H']}",
        f"Destek/Direnç 1H: {paket['Destek_Direnc_1H']}",
        f"Destek/Direnç 4H: {paket['Destek_Direnc_4H']}",
        f"Kaynak: {paket['Veri_Kaynagi']} / {paket['Veri_Periyodu']}",
        "İndikatör Sinyalleri:",
        f"  EMA: 1H {sinyaller['EMA 1H']} | 4H {sinyaller['EMA 4H']}",
        f"  RSI: {sinyaller['RSI']} | Hacim 1H: {sinyaller['Hacim 1H']}",
        f"  FVG: 1H {sinyaller['FVG 1H']} | 4H {sinyaller['FVG 4H']}",
        f"  BOS: 1H {sinyaller['BOS 1H']} | 4H {sinyaller['BOS 4H']}",
        f"  CHOCH: 1H {sinyaller['CHOCH 1H']} | 4H {sinyaller['CHOCH 4H']}",
        f"  S/R: 1H {sinyaller['Destek/Direnç 1H']} | 4H {sinyaller['Destek/Direnç 4H']}",
    ]

    if aksiyon != "BEKLE":
        satirlar.extend(
            [
                f"Hedef: ${karar['hedef_fiyat']} | Stop: ${karar['stop_loss']}",
                f"Lot: {lot} adet | Risk: ${PORTFOY_BUYUKLUGU * RİSK_YUZDESI}",
            ]
        )

    return "\n".join(satirlar)

def log_decision(paket, karar):
    zaman = datetime.now().strftime('%Y-%m-%d %H:%M')
    aksiyon = karar["aksiyon"]
    fiyat = paket["Guncel_Fiyat"]
    hedef = karar.get("hedef_fiyat", "")
    stop_loss = karar.get("stop_loss", "")
    gerekce = karar.get(
        "gerekce",
        (
            f"BEKLE | Trend: {paket['Makro_Trend']} | RSI: {paket['RSI']} | "
            f"1H: {paket['Analiz_1H']} | 4H: {paket['Analiz_4H']} | "
            f"S/R 1H: {paket['Destek_Direnc_1H']} | S/R 4H: {paket['Destek_Direnc_4H']} | "
            f"ATR: {paket['ATR']} | FVG: {paket['FVG_Yon']} | "
            f"Kaynak: {paket['Veri_Kaynagi']} | Periyot: {paket['Veri_Periyodu']}"
        ),
    )
    lot = calculate_position_size(fiyat, stop_loss) if stop_loss else 0
    risk = PORTFOY_BUYUKLUGU * RİSK_YUZDESI if stop_loss else 0

    log_to_csv([
        zaman,
        paket["Hisse"],
        fiyat,
        aksiyon,
        fiyat,
        hedef,
        stop_loss,
        lot,
        risk,
        paket["RSI"],
        paket["ATR"],
        paket["Analiz_1H"],
        paket["Analiz_4H"],
        paket["Destek_Direnc_1H"],
        paket["Destek_Direnc_4H"],
        paket["FVG_Yon"],
        paket.get("FVG_Yon_4H", ""),
        paket.get("BOS_Yon", ""),
        paket.get("BOS_Yon_4H", ""),
        paket.get("CHOCH_Yon", ""),
        paket.get("CHOCH_Yon_4H", ""),
        paket["Veri_Kaynagi"],
        paket["Veri_Periyodu"],
        gerekce,
    ])
    return lot, gerekce

def job():
    print(f"\n🔄 Tarama Başladı ({datetime.now().strftime('%H:%M')})")
    hisseler = ["UFO", "AMD", "ASTS", "RXT", "ORCL"] 
    ozet_satirlari = [f"Tarama Özeti ({datetime.now().strftime('%Y-%m-%d %H:%M')})"]
    
    for hisse in hisseler:
        paket = get_market_data(hisse)
        
        if paket:
            print(
                f"✅ Veri OK: {hisse} | Kaynak: {paket['Veri_Kaynagi']} | "
                f"Periyot: {paket['Veri_Periyodu']} | Fiyat: ${paket['Guncel_Fiyat']} | "
                f"RSI: {paket['RSI']} | ATR: {paket['ATR']} | "
                f"1H: {paket['Analiz_1H']} | 4H: {paket['Analiz_4H']} | "
                f"S/R 1H: {paket['Destek_Direnc_1H']} | S/R 4H: {paket['Destek_Direnc_4H']} | "
                f"FVG: {paket['FVG_Yon']}"
            )

            karar = rule_based_engine(paket)
            aksiyon = karar["aksiyon"]
            lot, gerekce = log_decision(paket, karar)
            if TELEGRAM_KARAR_MESAJLARI:
                if send_telegram_message(build_decision_message(paket, karar, lot, gerekce)):
                    print(f"✅ {hisse} karar mesajı Telegram'a gönderildi.")

            ozet_satirlari.append(
                f"{hisse}: {aksiyon} | ${paket['Guncel_Fiyat']} | RSI {paket['RSI']} | "
                f"1H: {paket['Analiz_1H']} | 4H: {paket['Analiz_4H']} | "
                f"S/R 1H: {paket['Destek_Direnc_1H']} | S/R 4H: {paket['Destek_Direnc_4H']} | "
                f"FVG {paket['FVG_Yon']} | {paket['Veri_Kaynagi']}/{paket['Veri_Periyodu']}"
            )
            
            if aksiyon != "BEKLE":
                fiyat = paket["Guncel_Fiyat"]
                stop_loss = karar["stop_loss"]
                hedef = karar["hedef_fiyat"]
                
                print(f"✅ {hisse} sinyali üretildi. Hedef: ${hedef} | Stop: ${stop_loss} | Lot: {lot}")
            else:
                print(f"ℹ️ {hisse} için işlem şartları oluşmadı (BEKLE). CSV'ye kaydedildi.")
        else:
            ozet_satirlari.append(f"{hisse}: VERI ALINAMADI")
                
        time.sleep(2)

    if TELEGRAM_TARAMA_OZETI:
        if send_telegram_message("\n".join(ozet_satirlari)):
            print("✅ Telegram tarama özeti gönderildi.")

if __name__ == "__main__":
    schedule.every().monday.at("10:00").do(job)
    schedule.every().monday.at("13:00").do(job)
    schedule.every().monday.at("16:20").do(job)
    schedule.every().monday.at("20:00").do(job)
    schedule.every().monday.at("22:45").do(job)
    schedule.every().monday.at("23:58").do(job)
    
    schedule.every().tuesday.at("10:00").do(job)
    schedule.every().tuesday.at("13:00").do(job)
    schedule.every().tuesday.at("16:20").do(job)
    schedule.every().tuesday.at("20:00").do(job)
    schedule.every().tuesday.at("22:45").do(job)
    schedule.every().tuesday.at("23:58").do(job)
    
    schedule.every().wednesday.at("10:00").do(job)
    schedule.every().wednesday.at("13:00").do(job)
    schedule.every().wednesday.at("16:20").do(job)
    schedule.every().wednesday.at("20:00").do(job)
    schedule.every().wednesday.at("22:45").do(job)
    schedule.every().wednesday.at("23:58").do(job)
    
    schedule.every().thursday.at("10:00").do(job)
    schedule.every().thursday.at("13:00").do(job)
    schedule.every().thursday.at("16:20").do(job)
    schedule.every().thursday.at("20:00").do(job)
    schedule.every().thursday.at("22:45").do(job)
    schedule.every().thursday.at("23:58").do(job)
    
    schedule.every().friday.at("10:00").do(job)
    schedule.every().friday.at("13:00").do(job)
    schedule.every().friday.at("16:20").do(job)
    schedule.every().friday.at("20:00").do(job)
    schedule.every().friday.at("22:45").do(job)
    schedule.every().friday.at("23:58").do(job)

    print("🤖 Kuantitatif Bot Aktif")
    print("📅 Hafta içi (Pazartesi-Cuma) belirtilen saatlerde analiz yapılacak")
    print("⏰ Saatler: 10:00, 13:00, 16:20, 20:00, 22:45, 23:58")
    print("⏳ Sonraki zamanlama bekleniyor...")

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except Exception as e:
            print(f"❌ Döngü Hatası: {e}")
            time.sleep(60)
