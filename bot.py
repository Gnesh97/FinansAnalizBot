import contextlib
import csv
import io
import json
import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
import requests
from requests.exceptions import HTTPError, RequestException, Timeout
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
ALPHA_VANTAGE_API_KEY = ""
PREFER_ALPHA_VANTAGE = False
ALPHA_VANTAGE_DISABLED = False


def get_nth_sunday(year, month, n):
    """Belirtilen ayın n. Pazar gününü bulur (DST hesaplaması için)."""
    first_day = datetime(year, month, 1)
    days_to_sunday = (6 - first_day.weekday()) % 7
    first_sunday = first_day + timedelta(days=days_to_sunday)
    return first_sunday + timedelta(days=7 * (n - 1))

def is_us_market_open():
    """ABD piyasasının açık olup olmadığını manuel UTC ve DST hesabı ile kontrol eder."""
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # Hafta sonu kontrolü (5: Cumartesi, 6: Pazar)
    if now_utc.weekday() >= 5:
        return False

    # DST (Yaz Saati) hesaplama: Mart'ın 2. Pazarı başlar, Kasım'ın 1. Pazarı biter
    year = now_utc.year
    dst_start = get_nth_sunday(year, 3, 2)
    dst_end = get_nth_sunday(year, 11, 1)

    is_dst = dst_start <= now_utc < dst_end
    current_time = now_utc.time()

    if is_dst:
        # Yaz saati UTC 13:30 - 20:00
        market_open = datetime.strptime("13:30", "%H:%M").time()
        market_close = datetime.strptime("20:00", "%H:%M").time()
    else:
        # Kış saati UTC 14:30 - 21:00
        market_open = datetime.strptime("14:30", "%H:%M").time()
        market_close = datetime.strptime("21:00", "%H:%M").time()

    return market_open <= current_time < market_close

def load_dotenv(path):
    env_data = {}
    try:
        if path.exists():
            with open(path, encoding="utf-8") as env_file:
                for line in env_file:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"\'')
                    env_data[key] = value
    except (OSError, UnicodeDecodeError, ValueError) as err:
        print(f"[CONFIG] .env yükleme hatası: {err}")
    return env_data


def load_json_config(path):
    try:
        if path.exists():
            with open(path, encoding="utf-8") as config_file:
                return json.load(config_file)
    except (OSError, json.JSONDecodeError) as err:
        print(f"[CONFIG] config.json yükleme hatası: {err}")
    return {}


def get_env(key, default=""):
    return os.getenv(key, _dotenv.get(key, default)).strip()


def get_env_bool(key, default=False):
    value = get_env(key, str(int(default)))
    return value.lower() in ("1", "true", "yes")


def get_env_int(key, default):
    value = get_env(key, str(default))
    try:
        return int(value)
    except ValueError:
        return default


def get_env_float(key, default):
    value = get_env(key, str(default))
    try:
        return float(value)
    except ValueError:
        return default

ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "config.json"
_dotenv = load_dotenv(ENV_PATH)
config = load_json_config(CONFIG_PATH)

ALPHA_VANTAGE_API_KEY = get_env("ALPHA_VANTAGE_API_KEY", config.get("alpha_vantage_api_key", ""))
PREFER_ALPHA_VANTAGE = get_env_bool("PREFER_ALPHA_VANTAGE", config.get("prefer_alpha_vantage", False))
TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN", config.get("telegram_bot_token", ""))
TELEGRAM_CHAT_ID = get_env("TELEGRAM_CHAT_ID", config.get("telegram_chat_id", ""))
try:
    TELEGRAM_CHAT_ID = int(TELEGRAM_CHAT_ID)
except ValueError:
    pass
TELEGRAM_TARAMA_OZETI = get_env_bool("TELEGRAM_TARAMA_OZETI", config.get("telegram_summary", False))
TELEGRAM_KARAR_MESAJLARI = get_env_bool("TELEGRAM_KARAR_MESAJLARI", config.get("telegram_decision_messages", True))
PORTFOY_BUYUKLUGU = get_env_float("PORTFOY_BUYUKLUGU", config.get("portfolio_size", 400))
RISK_YUZDESI = get_env_float("RISK_YUZDESI", config.get("risk_percentage", 10))
TELEGRAM_HAFTALIK_RAPOR = get_env_bool("TELEGRAM_HAFTALIK_RAPOR", config.get("telegram_weekly_report", True))

# --- KANTİTATİF İNDİKATÖR AĞIRLIK MATRİSİ (Z-SCORE İÇİN) ---
QUANT_WEIGHTS = {
    # SMC Grubu
    "4H_FVG": 92.5, "4H_CHOCH": 95.0, "4H_BOS": 47.5, # 4H_CHOCH 95.0'a yükseltildi
    "1H_FVG": 41.5, "1H_CHOCH": 28.5, "1H_BOS": 16.5,
    # Trend Grubu (Line ve Signal Budandı)
    "4H_EMA_50_200": 120.0, "MACD_Hist_Delta": 65.5, "ADX_Value": 42.5, # 4H_EMA ve MACD_Delta güncellendi
    "1H_EMA_50_200": 31.5, "PLUS_DI": 10.5, "MINUS_DI": 10.5,
    # Hacim Grubu
    "OBV": 135.5, "MFI": 84.5, "1H_Vol_Spike": 30.0, # OBV 135.5'e, MFI 84.5'e yükseltildi
    # Yapı Grubu
    "4H_S_R": 48.5, "BB_Squeeze": 26.5, "BB_Position": 25.5, "1H_S_R": 19.5,
    # Momentum Grubu (Stoch_%D Budandı)
    "RSI": 27.5, "CCI": 22.5, "Stoch_K": 18.5 
}

STOCK_SYMBOLS = config.get(
    "stock_symbols",
    ["UFO", "ASTS", "RXT", "RDW", "PL", "RKLB", "LUNR", "ORCL"],
)

SCAN_SCHEDULE = config.get(
    "scan_schedule",
    {
        "monday": ["10:00", "13:00", "16:20", "20:00", "22:45", "23:58"],
        "tuesday": ["10:00", "13:00", "16:20", "20:00", "22:45", "23:58"],
        "wednesday": ["10:00", "13:00", "16:20", "20:00", "22:45", "23:58"],
        "thursday": ["10:00", "13:00", "16:20", "20:00", "22:45", "23:58"],
        "friday": ["10:00", "13:00", "16:31", "20:00", "22:45", "23:58"],
    },
)

# ==========================================
# WALK-FORWARD ANALİZ (WFA) VE AĞIRLIK OPTİMİZASYONU
# ==========================================

# ==========================================
# KANTİTATİF RİSK YÖNETİMİ GRUBU
# ==========================================
MAX_RISK_PERCENT = 0.02   # %2 Mutlak Risk
MAX_DRAWDOWN_LIMIT = 0.06 # %6 Maksimum Portföy Çekilmesi
SYSTEM_LOCK_UNTIL = None

class RiskManager:
    @staticmethod
    def check_and_apply_drawdown_lock():
        """Günlük realize/unrealize Drawdown %6'yı aştıysa sistemi kilitler."""
        global SYSTEM_LOCK_UNTIL
        if SYSTEM_LOCK_UNTIL and datetime.now() < SYSTEM_LOCK_UNTIL:
            return True

        if not CSV_DOSYASI.exists():
            return False

        try:
            df = pd.read_csv(CSV_DOSYASI, encoding="utf-8")
            df['Tarih'] = pd.to_datetime(df['Tarih'], errors='coerce')
            
            # Son 5 gün içindeki işlemleri açık pozisyon olarak simüle et
            df_open = df[(df['Tarih'] >= datetime.now() - timedelta(days=5)) & (df['Aksiyon'].isin(['AL', 'GÜÇLÜ AL']))]
            
            if df_open.empty:
                return False

            total_pnl = 0
            unique_symbols = df_open['Hisse'].unique().tolist()
            
            # Açık pozisyonların anlık güncel fiyatlarını yfinance ile hızlıca çek
            try:
                tickers = yf.Tickers(" ".join(unique_symbols))
                current_prices = {}
                for sym in unique_symbols:
                    hist = tickers.tickers[sym].history(period="1d")
                    if not hist.empty:
                        current_prices[sym] = hist['Close'].iloc[-1]
            except:
                current_prices = {}

            # Portföydeki kâr/zararı hesapla
            for _, row in df_open.iterrows():
                hisse = row['Hisse']
                giris = float(row['Giris'])
                lot = float(row['Lot_Miktari'])
                
                guncel = current_prices.get(hisse, giris)
                
                mevcut_deger = guncel * lot
                yatirilan = giris * lot
                total_pnl += (mevcut_deger - yatirilan)

            # Çekilme Oranı (Drawdown = Zarar / Toplam Kasa)
            drawdown_ratio = abs(total_pnl) / PORTFOY_BUYUKLUGU if total_pnl < 0 else 0.0

            if drawdown_ratio >= MAX_DRAWDOWN_LIMIT:
                SYSTEM_LOCK_UNTIL = datetime.now() + timedelta(hours=48)
                msg = f"🚨 <b>KRİTİK UYARI:</b> Max Drawdown Limiti (%{MAX_DRAWDOWN_LIMIT*100}) aşıldı! (Zarar: ${abs(total_pnl):.2f})\n\nSistem yeni alım sinyallerini mutlak veto edecek, mevcut pozisyonlar tasfiye edildi ve KİLİT moduna geçildi (48 Saat)."
                print(msg)
                send_telegram_message(msg, parse_mode="HTML")
                return True

        except Exception as e:
            print(f"[RİSK] Drawdown hesaplama hatası: {e}")
            
        return False

    @staticmethod
    def calculate_dynamic_position(paket, aksiyon):
        """Asimetrik Stop-Loss ve Volatilite Ayarlı Pozisyon Boyutlandırması"""
        fiyat = paket["Guncel_Fiyat"]
        atr_4h = paket.get("ATR_4H", paket["ATR"]) # 4H ATR bulunamazsa 1H'yi fallback yap
        
        if "AL" in aksiyon:
            sl_atr = fiyat - (2.5 * atr_4h)
            fvg_bot = paket.get("FVG_Bull_Bot_4H")
            
            # FVG alt sınırı fiyata daha yakınsa, giriş noktasına yakın FVG kullan (Sermaye Asimetrisi)
            if pd.notna(fvg_bot) and sl_atr < fvg_bot < fiyat:
                stop_loss = fvg_bot
            else:
                stop_loss = sl_atr
                
            hedef = fiyat + (abs(fiyat - stop_loss) * 3.0)
            
        elif "SAT" in aksiyon:
            sl_atr = fiyat + (2.5 * atr_4h)
            fvg_top = paket.get("FVG_Bear_Top_4H")
            
            if pd.notna(fvg_top) and fiyat < fvg_top < sl_atr:
                stop_loss = fvg_top
            else:
                stop_loss = sl_atr
                
            hedef = fiyat - (abs(stop_loss - fiyat) * 3.0)
        else:
            return "", "", 0

        stop_loss = round(stop_loss, 2)
        hedef = round(hedef, 2)
        
        # Volatiliteye Duyarlı Lot Hesabı (Risk_Sermayesi = Toplam_Kasa * 0.02)
        risk_sermayesi = PORTFOY_BUYUKLUGU * MAX_RISK_PERCENT
        risk_per_share = abs(fiyat - stop_loss)
        
        if risk_per_share == 0:
            lot = 0
        else:
            lot = int(risk_sermayesi / risk_per_share)
            
        return stop_loss, hedef, lot

class WalkForwardOptimizer:
    def __init__(self, quant_weights_dict):
        self.keys = list(quant_weights_dict.keys())
        self.initial_weights = np.array(list(quant_weights_dict.values()))
        self.bounds = [(0.0, 200.0) for _ in self.keys]

    def _objective_function(self, weights, signals_matrix, forward_returns, penalty_vector):
        """Sharpe Oranı Hesabı - Tamamen Vektörel (C-Hızında)"""
        z_raw = np.dot(signals_matrix, weights)
        
        # Ceza vektörü (penalty_vector) önceden dinamik olarak matrise işlendi (1.0 veya 0.15-0.65 arası)
        z_final = z_raw * penalty_vector
        
        trade_signals = np.where(z_final >= 550, 1, np.where(z_final <= -750, -1, 0))
        strategy_returns = trade_signals * forward_returns
        
        mean_ret = np.mean(strategy_returns)
        std_ret = np.std(strategy_returns)
        
        if std_ret == 0:
            return 1.0  
            
        sharpe_ratio = (mean_ret / std_ret) * np.sqrt(252 * 7)
        return -sharpe_ratio 

    def calibrate_weights(self, df_1h, df_4h):
        print("[WFA] Son 6 aylık veriler üzerinde Tam Vektörel Sharpe Optimizasyonu başlatılıyor...")
        
        cutoff_date = df_1h.index[-1] - pd.DateOffset(months=6)
        train_1h = df_1h[df_1h.index >= cutoff_date].copy()
        train_4h = df_4h[df_4h.index >= cutoff_date].copy()
        
        if len(train_1h) < 500:
            print("[WFA] Eğitim için yeterli veri yok, mevcut ağırlıklar korunuyor.")
            return dict(zip(self.keys, self.initial_weights))

        # --- ZAMAN SERİSİ HİZALAMASI (4H Veriyi 1H Index'ine Uydurma) ---
        train_4h_aligned = train_4h.reindex(train_1h.index, method='ffill')
        
        forward_returns = train_1h['Close'].pct_change().shift(-1).fillna(0).values
        
        N = len(train_1h)
        signals_matrix = np.zeros((N, len(self.keys)))
        
        # ========================================================
        # RASTGELELİK (RANDOMNESS) KALDIRILDI: GERÇEK VERİ MATRİSİ
        # ========================================================
        
        # 1. SMC Grubu
        idx = self.keys.index("4H_FVG"); signals_matrix[:, idx] = np.sign(train_4h_aligned.get('FVG', pd.Series(0, index=train_1h.index)))
        idx = self.keys.index("4H_CHOCH"); signals_matrix[:, idx] = np.sign(train_4h_aligned.get('CHOCH', pd.Series(0, index=train_1h.index)))
        idx = self.keys.index("4H_BOS"); signals_matrix[:, idx] = np.sign(train_4h_aligned.get('BOS', pd.Series(0, index=train_1h.index)))
        idx = self.keys.index("1H_FVG"); signals_matrix[:, idx] = np.sign(train_1h.get('FVG', pd.Series(0, index=train_1h.index)))
        idx = self.keys.index("1H_CHOCH"); signals_matrix[:, idx] = np.sign(train_1h.get('CHOCH', pd.Series(0, index=train_1h.index)))
        idx = self.keys.index("1H_BOS"); signals_matrix[:, idx] = np.sign(train_1h.get('BOS', pd.Series(0, index=train_1h.index)))

        # 2. Trend Grubu
        c_4h = train_4h_aligned['Close']; e50_4h = train_4h_aligned['EMA_50']; e200_4h = train_4h_aligned['EMA_200']
        idx = self.keys.index("4H_EMA_50_200")
        signals_matrix[:, idx] = np.where((c_4h > e50_4h) & (e50_4h > e200_4h), 1, np.where((c_4h < e50_4h) & (e50_4h < e200_4h), -1, 0))

        c_1h = train_1h['Close']; e50_1h = train_1h['EMA_50']; e200_1h = train_1h['EMA_200']
        idx = self.keys.index("1H_EMA_50_200")
        signals_matrix[:, idx] = np.where((c_1h > e50_1h) & (e50_1h > e200_1h), 1, np.where((c_1h < e50_1h) & (e50_1h < e200_1h), -1, 0))

        idx = self.keys.index("MACD_Hist_Delta")
        macd_delta = train_1h['MACD_HIST'].diff()
        signals_matrix[:, idx] = np.where(macd_delta > 0, 1, -1)

        adx = train_1h.get('ADX_14', pd.Series(0, index=train_1h.index))
        p_di = train_1h.get('PLUS_DI', pd.Series(0, index=train_1h.index))
        m_di = train_1h.get('MINUS_DI', pd.Series(0, index=train_1h.index))
        idx = self.keys.index("ADX_Value")
        signals_matrix[:, idx] = np.where((adx > 25) & (p_di > m_di), 1, np.where((adx > 25) & (m_di > p_di), -1, 0))
        idx = self.keys.index("PLUS_DI"); signals_matrix[:, idx] = np.where(p_di > 25, 1, 0)
        idx = self.keys.index("MINUS_DI"); signals_matrix[:, idx] = np.where(m_di > 25, -1, 0)

        # 3. Hacim Grubu
        obv_4h = train_4h_aligned['OBV']
        obv_slope = obv_4h - obv_4h.shift(20) # 1H üzerinde 20 mum geriye gitmek = 4H üzerinde 5 mum (Inertia Hesabı)
        idx = self.keys.index("OBV"); signals_matrix[:, idx] = np.where(obv_slope > 0, 1, -1)

        idx = self.keys.index("MFI")
        mfi = train_1h.get('MFI_14', pd.Series(50, index=train_1h.index))
        signals_matrix[:, idx] = np.where(mfi < 20, 1, np.where(mfi > 80, -1, 0))

        idx = self.keys.index("1H_Vol_Spike")
        vol = train_1h['Volume']; vol_sma = train_1h['Volume_SMA_20']
        signals_matrix[:, idx] = np.where((vol > vol_sma * 1.5) & (c_1h > train_1h['Open']), 1,
                                 np.where((vol > vol_sma * 1.5) & (c_1h < train_1h['Open']), -1, 0))

        # 4. Yapı Grubu
        roll_low_1h = train_1h['Low'].rolling(120).min()
        roll_high_1h = train_1h['High'].rolling(120).max()
        idx = self.keys.index("1H_S_R")
        signals_matrix[:, idx] = np.where(abs(c_1h - roll_low_1h)/c_1h < 0.015, 1, np.where(abs(roll_high_1h - c_1h)/c_1h < 0.015, -1, 0))
        
        roll_low_4h = train_4h_aligned['Low'].rolling(120).min()
        roll_high_4h = train_4h_aligned['High'].rolling(120).max()
        idx = self.keys.index("4H_S_R")
        signals_matrix[:, idx] = np.where(abs(c_1h - roll_low_4h)/c_1h < 0.015, 1, np.where(abs(roll_high_4h - c_1h)/c_1h < 0.015, -1, 0))

        idx = self.keys.index("BB_Squeeze"); signals_matrix[:, idx] = np.where(train_1h.get('BB_SQUEEZE', False), 0.5, 0)
        idx = self.keys.index("BB_Position"); signals_matrix[:, idx] = np.where(c_1h < train_1h.get('BB_LOW', 0), 1, np.where(c_1h > train_1h.get('BB_UP', 0), -1, 0))

        # 5. Momentum Grubu
        idx = self.keys.index("RSI")
        rsi = train_1h.get('RSI_14', pd.Series(50, index=train_1h.index))
        signals_matrix[:, idx] = np.where(rsi < 30, 1, np.where(rsi > 70, -1, np.where(rsi > 50, 0.5, -0.5)))

        idx = self.keys.index("CCI")
        cci = train_1h.get('CCI_20', pd.Series(0, index=train_1h.index))
        signals_matrix[:, idx] = np.where(cci < -100, 1, np.where(cci > 100, -1, 0))

        idx = self.keys.index("Stoch_K")
        stoch_k = train_1h.get('STOCH_%K', pd.Series(50, index=train_1h.index))
        stoch_k_prev = stoch_k.shift(1).fillna(50)
        signals_matrix[:, idx] = np.where((stoch_k_prev <= 20) & (stoch_k > stoch_k_prev), 1, 
                                 np.where((stoch_k_prev >= 80) & (stoch_k < stoch_k_prev), -1, 0))

        # ========================================================
        # DİNAMİK CEZA ÇARPANININ (PENALTY DECAY) VEKTÖRLEŞTİRİLMESİ
        # ========================================================
        # Hangi mumlarda 4H_CHOCH veya OBV negatif? (Matristen çek)
        penalty_mask = (signals_matrix[:, self.keys.index("OBV")] < 0) | (signals_matrix[:, self.keys.index("4H_CHOCH")] < 0)
        
        # OBV İvmesini tüm zaman serisi için C hızında hesapla
        obv_mean_10 = train_1h['OBV'].rolling(10).mean()
        obv_std_10 = train_1h['OBV'].rolling(10).std().replace(0, np.nan)
        obv_inertia = ((train_1h['OBV'] - obv_mean_10) / obv_std_10).fillna(0).values
        
        # Asimetrik ceza oranını (0.15 - 0.65 arası) hesapla
        penalty_decay = 0.15 + (0.50 * (np.clip(obv_inertia, None, 4.0) - 2.0) / 2.0)
        penalty_decay = np.where(obv_inertia > 2.0, penalty_decay, 0.15)
        
        # Maskeye uyan (makro trendi veya hacmi bozulan) yerlere dinamik cezayı bas, uymayanlara 1.0 (Normal) bas
        penalty_vector = np.where(penalty_mask, penalty_decay, 1.0)

        # --- 3. SLSQP Algoritması ile Non-Linear Optimizasyon ---
        result = minimize(
            self._objective_function,
            x0=self.initial_weights,
            args=(signals_matrix, forward_returns, penalty_vector), # Maske yerine, dinamik vektör gönderildi
            method='SLSQP',
            bounds=self.bounds,
            options={'disp': False, 'maxiter': 150}
        )

        if result.success:
            optimized_weights = np.round(result.x, 1)
            print(f"[WFA] ✅ Optimizasyon Başarılı! Yeni Sharpe Oranı: {abs(result.fun):.2f}")
            return dict(zip(self.keys, optimized_weights))
        else:
            print("[WFA] ⚠️ Optimizasyon yakınsamadı, orijinal ağırlıklara dönülüyor.")
            return dict(zip(self.keys, self.initial_weights))


def schedule_scan(job_fn, schedule_config):
    for day_name, times in schedule_config.items():
        day_name = day_name.lower()
        if not hasattr(schedule.every(), day_name):
            print(f"[SCHEDULE] Geçersiz gün: {day_name}")
            continue
        if isinstance(times, str):
            times = [times]
        for scheduled_time in times:
            try:
                getattr(schedule.every(), day_name).at(scheduled_time).do(job_fn)
            except (AttributeError, TypeError, ValueError) as err:
                print(f"[SCHEDULE] {day_name} {scheduled_time} hatası: {err}")


def format_schedule(schedule_config):
    lines = []
    for day_name in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
        times = schedule_config.get(day_name, [])
        if times:
            times_text = ", ".join(times)
            lines.append(f"{day_name.capitalize()}: {times_text}")
    return " | ".join(lines)

print(f"[CONFIG] loaded from {CONFIG_PATH.name} and {ENV_PATH.name} if present")


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
        except (OSError, csv.Error, ValueError):
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

CSV_LOCK = threading.Lock()
PRICE_HISTORY_CACHE = {}
CACHE_LOCK = threading.Lock()
CACHE_TTL_SECONDS = 15 * 60

def is_duplicate_signal(hisse, aksiyon):
    """
    Belirtilen hisse için bugün aynı YÖNDE (örn: AL veya GÜÇLÜ AL fark etmeksizin) 
    daha önce işlem loglanmış mı kontrol eder.
    """
    # Gelen aksiyonu boşluklardan arındırıp büyük harfe çeviriyoruz
    aksiyon_upper = aksiyon.strip().upper()
    
    if aksiyon_upper == "BEKLE":
        return False

    if not os.path.exists(CSV_DOSYASI):
        return False

    # Bugünün tarihini al (Örn: 2024-05-31)
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    # İşlemin temel ANA YÖNÜNÜ belirle ("GÜÇLÜ AL" gelse de "AL" olarak baz alır)
    hedef_yon = "AL" if "AL" in aksiyon_upper else "SAT" if "SAT" in aksiyon_upper else aksiyon_upper

    try:
        with CSV_LOCK:
            with open(CSV_DOSYASI, mode='r', encoding='utf-8') as file:
                reader = csv.reader(file)
                for row in reader:
                    if len(row) > 3:
                        # Satırdaki verileri temizleyerek (strip) al
                        row_date = str(row[0]).strip()
                        row_hisse = str(row[1]).strip().upper()
                        row_aksiyon = str(row[3]).strip().upper()

                        # CSV'deki kaydın da temel ANA YÖNÜNÜ bul
                        kayitli_yon = "AL" if "AL" in row_aksiyon else "SAT" if "SAT" in row_aksiyon else row_aksiyon

                        # 1. Tarih bugün mü?
                        # 2. Hisse aynı hisse mi?
                        # 3. YÖN (AL/SAT) aynı mı?
                        if row_date.startswith(today_str) and row_hisse == hisse.strip().upper() and kayitli_yon == hedef_yon:
                            return True
                            
    except Exception as e:
        print(f"[CSV] Duplicate kontrolü sırasında hata: {e}")

    return False


def log_to_csv(veri_listesi):
    try:
        with CSV_LOCK:
            with open(CSV_DOSYASI, mode='a', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow(veri_listesi)
        return True
    except OSError as err:
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
    except RequestException as err:
        print(f"Telegram gönderim hatası: {err}")
        return False


def send_telegram_photo(photo_path, caption=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {}
    data = {"chat_id": TELEGRAM_CHAT_ID}
    if caption:
        data["caption"] = caption
    try:
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            response = requests.post(url, data=data, files=files, timeout=20)
            response.raise_for_status()
        return True
    except RequestException as err:
        print(f"Telegram fotoğraf gönderim hatası: {err}")
        return False
    except OSError as err:
        print(f"Fotoğraf dosyası açılamadı: {err}")
        return False


def create_candlestick_chart(paket, tail=40):
    """Create candlestick PNG for paket['DF_HOURLY'] showing EMA20, EMA50, and S/R levels.
    Returns file path or None on failure.
    """
    df = paket.get("DF_HOURLY")
    if df is None or df.empty:
        return None

    try:
        import tempfile
        
        # --- KRİTİK DÜZELTME: Tkinter GUI Çökmesini Engeller ---
        import matplotlib
        matplotlib.use('Agg') # Matplotlib'i penceresiz (headless) sunucu modunda çalışmaya zorlar
        # --------------------------------------------------------

        plot_df = df.copy()
        if "Close" not in plot_df.columns:
            return None
        plot_df = plot_df.tail(tail)

        # Destek, Direnç ve Güncel Fiyat seviyelerini al
        destek = paket.get("Destek_1H")
        direnc = paket.get("Direnc_1H")
        guncel_fiyat = plot_df["Close"].iloc[-1]
        hisse_ismi = paket.get("Hisse", "Bilinmiyor")

        # compute EMAs if missing
        if "EMA_20" not in plot_df.columns or plot_df["EMA_20"].isna().all():
            plot_df["EMA_20"] = plot_df["Close"].ewm(span=20, adjust=False, min_periods=1).mean()
        if "EMA_50" not in plot_df.columns or plot_df["EMA_50"].isna().all():
            plot_df["EMA_50"] = plot_df["Close"].ewm(span=50, adjust=False, min_periods=1).mean()

        # Try mplfinance first
        try:
            import mplfinance as mpf
            import matplotlib.ticker as ticker
            import matplotlib.pyplot as plt

            add_plots = [
                mpf.make_addplot(plot_df["EMA_20"], color="#ff9900"), 
                mpf.make_addplot(plot_df["EMA_50"], color="#3399ff")
            ]
            
            # mplfinance için Yatay Çizgiler (Destek, Direnç ve Güncel Fiyat)
            hlines_levels = []
            hlines_colors = []
            if destek is not None:
                hlines_levels.append(destek)
                hlines_colors.append("blue") # Destek Mavi
            if direnc is not None:
                hlines_levels.append(direnc)
                hlines_colors.append("red") # Direnç Kırmızı
                
            # Güncel fiyatı gri/siyah kesik çizgi olarak ekleyelim
            hlines_levels.append(guncel_fiyat)
            hlines_colors.append("gray")

            mpf_kwargs = dict(
                type="candle",
                style="yahoo",
                addplot=add_plots,
                datetime_format="%m-%d %H:%M",
                title=f"\n{hisse_ismi} | Guncel: {guncel_fiyat:.3f}",
                tight_layout=True,
                volume=True,
                # Grafiği daha da genişletip detayları açıyoruz
                figratio=(14, 7), 
                figscale=1.2
            )
            
            if hlines_levels:
                mpf_kwargs["hlines"] = dict(hlines=hlines_levels, colors=hlines_colors, linestyle='dashed', linewidths=1.5)

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            tmp_path = tmp.name
            tmp.close()
            
            fig, axlist = mpf.plot(plot_df, returnfig=True, **mpf_kwargs)
            
            # --- Y EKSENİ (FİYAT) DÜZELTMELERİ ---
            axlist[0].tick_params(labelleft=True, labelright=True, labelsize=9)
            axlist[0].yaxis.set_major_locator(ticker.MaxNLocator(nbins=12))
            axlist[0].yaxis.set_major_formatter(ticker.FormatStrFormatter('%.2f'))
            
            fig.savefig(tmp_path)
            plt.close(fig)
            
            return tmp_path
        except Exception as e_mpf:
            print(f"[CHART] mplfinance kullanılamıyor, fallback: {e_mpf}")

        # Fallback: use matplotlib to draw a simple candlestick chart
        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            import matplotlib.ticker as ticker

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            tmp_path = tmp.name
            tmp.close()

            fig, ax = plt.subplots(figsize=(14, 7))
            dates = mdates.date2num(plot_df.index.to_pydatetime())

            width = 0.02 * (dates[-1] - dates[0]) if len(dates) > 1 else 0.01
            for idx, (dt, row) in enumerate(plot_df.iterrows()):
                d = dates[idx]
                openp = float(row["Open"])
                highp = float(row["High"])
                lowp = float(row["Low"])
                closep = float(row["Close"])
                color = "#26a69a" if closep >= openp else "#ef5350"
                # wick
                ax.plot([d, d], [lowp, highp], color="black", linewidth=0.6)
                # body
                rect = plt.Rectangle((d - width / 2, min(openp, closep)), width, abs(closep - openp), color=color, alpha=0.8)
                ax.add_patch(rect)

            # plot EMAs
            ax.plot(dates, plot_df["EMA_20"].to_numpy(), color="#ff9900", label="EMA20")
            ax.plot(dates, plot_df["EMA_50"].to_numpy(), color="#3399ff", label="EMA50")
            
            # plot Support / Resistance with labels
            if destek is not None:
                ax.axhline(y=destek, color="blue", linestyle="--", linewidth=1.5, label=f"Destek ({destek:.3f})")
            if direnc is not None:
                ax.axhline(y=direnc, color="red", linestyle="--", linewidth=1.5, label=f"Direnç ({direnc:.3f})")
                
            ax.axhline(y=guncel_fiyat, color="gray", linestyle="--", linewidth=1.5, label=f"Güncel ({guncel_fiyat:.3f})")

            ax.xaxis_date()
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
            
            # Fallback Y Ekseni Düzeltmeleri
            ax.tick_params(labelleft=True, labelright=True, labelsize=9)
            ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=12))
            ax.yaxis.set_major_formatter(ticker.FormatStrFormatter('%.2f'))
            
            plt.xticks(rotation=45)
            plt.title(f"{hisse_ismi} | Guncel Fiyat: {guncel_fiyat:.3f}")
            plt.legend()
            plt.tight_layout()
            fig.savefig(tmp_path)
            plt.close(fig)
            return tmp_path
        except Exception as e_mat:
            print(f"[CHART] Matplotlib fallback başarısız: {e_mat}")
            return None
    except Exception as err:
        print(f"[CHART] Grafik oluşturulamadı: {err}")
        return None


def get_telegram_updates(offset=None, timeout=10):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    try:
        response = requests.get(url, params=params, timeout=timeout + 5)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise ValueError(f"Telegram getUpdates hatası: {payload}")
        return payload.get("result", [])
    except (RequestException, ValueError, TypeError, json.JSONDecodeError) as err:
        print(f"Telegram güncelleme kontrol hatası: {err}")
        return []


def poll_telegram_messages(last_update_id=None):
    updates = get_telegram_updates(offset=last_update_id, timeout=5)
    new_messages = []
    next_update_id = last_update_id

    for update in updates:
        update_id = update.get("update_id")
        if update_id is None:
            continue
        if next_update_id is None or update_id >= next_update_id:
            next_update_id = update_id + 1
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        chat_id = message.get("chat", {}).get("id")
        if chat_id is None:
            continue
        if str(chat_id) != str(TELEGRAM_CHAT_ID):
            continue
        new_messages.append(message)

    return next_update_id, new_messages


def get_last_scan_csv_row():
    if not CSV_DOSYASI.exists():
        return None
    try:
        with open(CSV_DOSYASI, mode="r", newline="", encoding="utf-8") as file:
            reader = csv.reader(file)
            last_row = None
            for row in reader:
                last_row = row
    except (OSError, csv.Error) as err:
        print(f"[CSV] Son tarama okunamadı: {err}")
        return None

    if not last_row or last_row == CSV_BASLIKLARI:
        return None
    return dict(zip(CSV_BASLIKLARI, last_row))


def build_last_scan_message(csv_row):
    if not csv_row:
        return "Henüz tarama sonucu kaydedilmedi veya CSV boş."

    return (
        f"Son tarama sonucu:\n"
        f"Hisse: {csv_row.get('Hisse', '-') }\n"
        f"Aksiyon: {csv_row.get('Aksiyon', '-') }\n"
        f"Fiyat: {csv_row.get('Fiyat', '-') }\n"
        f"RSI: {csv_row.get('RSI', '-') } | ATR: {csv_row.get('ATR', '-') }\n"
        f"Hedef: {csv_row.get('Hedef', '-') } | Stop: {csv_row.get('Stop', '-') }\n"
        f"Tarih: {csv_row.get('Tarih', '-') }\n"
        f"Not: {csv_row.get('Gerekce', '-') }"
    )


def is_telegram_scan_command(message_text):
    if not isinstance(message_text, str):
        return False
    normalized = message_text.strip().lower()
    return normalized in {
        "/tarama",
        "/analiz",
        "/scan",
        "tarama",
        "analiz",
    }

def is_telegram_karne_command(message_text):
    if not isinstance(message_text, str):
        return False
    normalized = message_text.strip().lower()
    return normalized in {"/karne", "karne", "/rapor", "rapor"}

def update_config_symbols(action, symbol=None):
    global STOCK_SYMBOLS
    
    # config.json dosyasını oku veya yoksa oluştur
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                current_config = json.load(f)
        else:
            current_config = {}
    except Exception as e:
        print(f"[CONFIG] Okuma hatası: {e}")
        current_config = {}

    # config içinde stock_symbols listesi yoksa RAM'deki mevcut listeyi baz al
    if "stock_symbols" not in current_config:
        current_config["stock_symbols"] = STOCK_SYMBOLS.copy()
    
    config_symbols = current_config["stock_symbols"]

    # --- LİSTELEME İŞLEMİ ---
    if action == "liste":
        liste_str = ", ".join(STOCK_SYMBOLS)
        mesaj = f"📋 <b>Güncel Takip Listesi ({len(STOCK_SYMBOLS)} Hisse):</b>\n{liste_str}"
        return mesaj

    # --- EKLEME İŞLEMİ ---
    elif action == "ekle" and symbol:
        symbol = symbol.upper()
        if symbol not in config_symbols:
            config_symbols.append(symbol)
            if symbol not in STOCK_SYMBOLS:
                STOCK_SYMBOLS.append(symbol) # RAM'i anında güncelle
            mesaj = f"✅ <b>{symbol}</b> kalıcı takip listesine eklendi."
        else:
            mesaj = f"ℹ️ <b>{symbol}</b> zaten listede mevcut."
    
    # --- ÇIKARMA İŞLEMİ ---
    elif action == "cikar" and symbol:
        symbol = symbol.upper()
        if symbol in config_symbols:
            config_symbols.remove(symbol)
            if symbol in STOCK_SYMBOLS:
                STOCK_SYMBOLS.remove(symbol) # RAM'i anında güncelle
            mesaj = f"✅ <b>{symbol}</b> kalıcı listeden çıkarıldı."
        else:
            mesaj = f"⚠️ <b>{symbol}</b> listede bulunamadı."
    
    else:
        return "❌ Geçersiz komut yapısı."

    # Değişiklikleri config.json'a güvenli bir şekilde kaydet
    current_config["stock_symbols"] = config_symbols
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(current_config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[CONFIG] Yazma hatası: {e}")
        mesaj += f"\n❌ <i>Ancak config.json dosyasına kaydedilemedi: {e}</i>"
    
    return mesaj

def generate_karne_summary():
    if not CSV_DOSYASI.exists():
        return "❌ Henüz işlem logu bulunmuyor (CSV dosyası yok)."
    
    try:
        df = pd.read_csv(CSV_DOSYASI, encoding="utf-8")
    except Exception as e:
        return f"❌ CSV okuma hatası: {e}"

    df.columns = df.columns.str.strip()
    if 'Aksiyon' not in df.columns:
        return "❌ Hata: CSV dosyasında 'Aksiyon' sütunu bulunamadı."

    hedef_aksiyonlar = ['AL', 'GÜÇLÜ AL']
    df_filtered = df[df['Aksiyon'].isin(hedef_aksiyonlar)].copy()

    if df_filtered.empty:
        return "⚠️ Henüz kayıtlarda 'AL' veya 'GÜÇLÜ AL' işlemi bulunmuyor."

    df_filtered['Giris'] = pd.to_numeric(df_filtered['Giris'], errors='coerce')
    df_filtered = df_filtered.dropna(subset=['Giris'])

    unique_tickers = df_filtered['Hisse'].unique()
    current_prices = {}

    for ticker in unique_tickers:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                current_prices[ticker] = hist['Close'].iloc[-1]
            else:
                current_prices[ticker] = None
        except Exception:
            current_prices[ticker] = None

    df_filtered['Guncel_Fiyat'] = df_filtered['Hisse'].map(current_prices)
    df_filtered = df_filtered.dropna(subset=['Guncel_Fiyat'])

    if df_filtered.empty:
        return "❌ Hesaplama yapılabilecek geçerli hisse verisi kalmadı."

    df_filtered['Kâr/Zarar (%)'] = ((df_filtered['Guncel_Fiyat'] - df_filtered['Giris']) / df_filtered['Giris']) * 100

    toplam_islem = len(df_filtered)
    karda = len(df_filtered[df_filtered['Kâr/Zarar (%)'] > 0])
    zararda = len(df_filtered[df_filtered['Kâr/Zarar (%)'] < 0])
    notr = len(df_filtered[df_filtered['Kâr/Zarar (%)'] == 0])

    basari_orani = (karda / toplam_islem) * 100 if toplam_islem > 0 else 0
    ortalama_getiri = df_filtered['Kâr/Zarar (%)'].mean()

    icon = "🏆" if basari_orani >= 60 else "⚖️" if basari_orani >= 40 else "🚨"

    # --- İŞLEM DETAYLARI OLUŞTURMA KISMI ---
    # Çok fazla veri varsa mesajın patlamaması için son 20 işlemi alıyoruz
    son_islemler = df_filtered.tail(20)
    detaylar = []
    
    for _, row in son_islemler.iterrows():
        pnl = row['Kâr/Zarar (%)']
        ikon_detay = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        hisse = row['Hisse']
        giris = row['Giris']
        guncel = row['Guncel_Fiyat']
        
        detaylar.append(f"{ikon_detay} <b>{hisse}</b> | {giris:.2f} ➔ {guncel:.2f} <b>(%{pnl:.2f})</b>")
    
    detay_metni = "\n".join(detaylar)
    
    # 20'den fazla işlem varsa alta küçük bir not düş
    if toplam_islem > 20:
        detay_metni += f"\n\n<i>...ve {toplam_islem - 20} eski işlem daha.</i>"

    msg = (
        f"📊 <b>BOT KARNESİ ÖZETİ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Toplam 'AL' Sinyali: {toplam_islem}\n"
        f"🟢 Kârdaki İşlemler: {karda}\n"
        f"🔴 Zarardaki İşlemler: {zararda}\n"
        f"⚪ Nötr İşlemler: {notr}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Ortalama Getiri: %{ortalama_getiri:.2f}\n"
        f"Genel Başarı Oranı: {icon} %{basari_orani:.2f}\n\n"
        f"📋 <b>İŞLEM DETAYLARI (Son {len(son_islemler)})</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{detay_metni}"
    )
    return msg

def generate_weekly_report():
    if not CSV_DOSYASI.exists():
        return "❌ Henüz işlem logu bulunmuyor (CSV dosyası yok)."

    try:
        df = pd.read_csv(CSV_DOSYASI, encoding="utf-8")
    except Exception as e:
        return f"❌ Haftalık rapor için CSV okuma hatası: {e}"

    df.columns = df.columns.str.strip()
    
    # Tarih sütununu datetime formatına çevir
    try:
        df['Tarih'] = pd.to_datetime(df['Tarih'], format='%Y-%m-%d %H:%M', errors='coerce')
    except Exception:
        df['Tarih'] = pd.to_datetime(df['Tarih'], errors='coerce')
        
    df = df.dropna(subset=['Tarih'])
    
    now = datetime.now()
    last_7_days_start = now - timedelta(days=7)
    prev_7_days_start = now - timedelta(days=14)
    
    # Zaman aralıklarını filtrele
    df_this_week = df[(df['Tarih'] >= last_7_days_start) & (df['Tarih'] <= now)]
    df_prev_week = df[(df['Tarih'] >= prev_7_days_start) & (df['Tarih'] < last_7_days_start)]
    
    al_aksiyonlar = ['AL', 'GÜÇLÜ AL']
    sat_aksiyonlar = ['SAT', 'GÜÇLÜ SAT']
    
    # Sinyal Sayıları (Bu Hafta)
    al_count_this = len(df_this_week[df_this_week['Aksiyon'].isin(al_aksiyonlar)])
    sat_count_this = len(df_this_week[df_this_week['Aksiyon'].isin(sat_aksiyonlar)])
    toplam_sinyal_this = al_count_this + sat_count_this
    
    # Sinyal Sayıları (Geçen Hafta)
    al_count_prev = len(df_prev_week[df_prev_week['Aksiyon'].isin(al_aksiyonlar)])
    sat_count_prev = len(df_prev_week[df_prev_week['Aksiyon'].isin(sat_aksiyonlar)])
    toplam_sinyal_prev = al_count_prev + sat_count_prev
    
    # En çok sinyal alan ilk 3 hisse
    top_3_hisse = "  <i>Sinyal yok</i>"
    if not df_this_week.empty:
        valid_signals = df_this_week[df_this_week['Aksiyon'].isin(al_aksiyonlar + sat_aksiyonlar)]
        if not valid_signals.empty:
            top_3 = valid_signals['Hisse'].value_counts().head(3)
            top_3_list = [f"<b>{k}</b> ({v} sinyal)" for k, v in top_3.items()]
            top_3_hisse = "\n".join([f"  {i+1}. {x}" for i, x in enumerate(top_3_list)])
        
    # AL sinyallerinin ortalama RSI değeri
    df_al_this = df_this_week[df_this_week['Aksiyon'].isin(al_aksiyonlar)].copy()
    df_al_this['RSI'] = pd.to_numeric(df_al_this['RSI'], errors='coerce')
    avg_rsi = df_al_this['RSI'].mean()
    avg_rsi_str = f"{avg_rsi:.2f}" if not pd.isna(avg_rsi) else "Veri Yok"
    
    # Haftalık Kıyaslama
    diff = toplam_sinyal_this - toplam_sinyal_prev
    if diff > 0:
        kiyaslama = f"Geçen haftaya göre <b>{diff} daha fazla</b> sinyal üretildi. 📈"
    elif diff < 0:
        kiyaslama = f"Geçen haftaya göre <b>{abs(diff)} daha az</b> sinyal üretildi. 📉"
    else:
        kiyaslama = "Geçen haftayla <b>aynı</b> sayıda sinyal üretildi. ⚖️"
        
    msg = (
        f"📅 <b>HAFTALIK BOT RAPORU</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Sinyal Özeti (Son 7 Gün):</b>\n"
        f"🟢 AL Sinyali: <b>{al_count_this}</b>\n"
        f"🔴 SAT Sinyali: <b>{sat_count_this}</b>\n"
        f"📉 AL Sinyalleri Ort. RSI: <b>{avg_rsi_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔥 <b>En Çok Sinyal Alanlar (Top 3):</b>\n{top_3_hisse}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Kıyaslama:</b>\n{kiyaslama}"
    )
    return msg

def weekly_report_job():
    if not TELEGRAM_HAFTALIK_RAPOR:
        return
    print("[REPORT] Haftalık rapor hazırlanıyor...")
    try:
        rapor_mesaji = generate_weekly_report()
        if send_telegram_message(rapor_mesaji, parse_mode="HTML"):
            print("[REPORT] ✅ Haftalık rapor Telegram'a gönderildi.")
        else:
            print("[REPORT] ❌ Haftalık rapor gönderilemedi.")
    except Exception as e:
        print(f"[REPORT] ❌ Haftalık rapor oluşturulurken hata: {e}")


def handle_telegram_polling(new_messages):
    if not new_messages:
        return

    for message in new_messages:
        text = message.get("text", "").strip()
        
        # 1. KARNE (Rapor) Komutu Kontrolü
        if is_telegram_karne_command(text):
            print(f"📩 Karne komutu alındı: {text}. Hesaplanıyor...")
            send_telegram_message("⏳ Karne hesaplanıyor, güncel fiyatlar çekiliyor. Lütfen bekleyin...")
            
            karne_mesaji = generate_karne_summary()
            send_telegram_message(karne_mesaji, parse_mode="HTML")
            print("✅ Telegram'a karne özeti gönderildi.")
            return

        # Komutu parçalara ayır
        parts = text.split()
        if not parts:
            continue
            
        komut = parts[0].lower()

        # 2. HELP (Yardım) Komutu
        if komut in {"/help", "help", "/yardim", "/yardım", "yardim", "yardım"}:
            print("📩 Help komutu alındı.")
            help_text = (
                "🤖 <b>KUANTİTATİF BOT KOMUTLARI</b>\n\n"
                "🔍 <b>Tarama ve Analiz</b>\n"
                "<code>/tarama</code> : Sabit listedeki hisseleri anında tarar.\n"
                "<code>/tarama TSLA AAPL</code> : Sadece yazdığın özel hisseleri tarar.\n\n"
                "📊 <b>Performans</b>\n"
                "<code>/karne</code> : Botun geçmiş AL işlemlerinin kâr/zarar analizini çıkarır.\n\n"
                "📋 <b>Liste Yönetimi</b>\n"
                "<code>/liste</code> : Güncel takip listesini gösterir.\n"
                "<code>/ekle NVDA</code> : Kendi kalıcı takip listene yeni hisse ekler.\n"
                "<code>/cikar ASTS</code> : İlgili hisseyi kalıcı takip listenden siler.\n\n"
                "ℹ️ <b>Bilgi</b>\n"
                "<code>/help</code> : Bu yardım menüsünü gösterir."
            )
            send_telegram_message(help_text, parse_mode="HTML")
            return

        # 3. LİSTE Komutu (Güncel hisseleri gösterir)
        if komut in {"/liste", "liste"}:
            print("📩 Liste komutu alındı.")
            yanit = update_config_symbols("liste")
            send_telegram_message(yanit, parse_mode="HTML")
            return

        # 4. EKLE Komutu (Kalıcı listeye hisse ekler)
        if komut in {"/ekle", "ekle"}:
            if len(parts) > 1:
                symbol = parts[1].upper()
                print(f"📩 Ekle komutu alındı: {symbol}")
                yanit = update_config_symbols("ekle", symbol)
                send_telegram_message(yanit, parse_mode="HTML")
            else:
                send_telegram_message("⚠️ Lütfen eklenecek hisseyi belirtin.\nÖrnek kullanım: <code>/ekle TSLA</code>", parse_mode="HTML")
            return

        # 5. ÇIKAR Komutu (Kalıcı listeden hisse siler)
        if komut in {"/cikar", "/çıkar", "cikar", "çıkar"}:
            if len(parts) > 1:
                symbol = parts[1].upper()
                print(f"📩 Çıkar komutu alındı: {symbol}")
                yanit = update_config_symbols("cikar", symbol)
                send_telegram_message(yanit, parse_mode="HTML")
            else:
                send_telegram_message("⚠️ Lütfen çıkarılacak hisseyi belirtin.\nÖrnek kullanım: <code>/cikar UFO</code>", parse_mode="HTML")
            return

        # 6. TARAMA Komutu ve Özel Hisse Kontrolü
        if komut in {"/tarama", "/analiz", "/scan", "tarama", "analiz"}:
            custom_symbols = [s.upper() for s in parts[1:]]
            
            mesaj_bilgisi = f"📩 Özel tarama komutu alındı. "
            if custom_symbols:
                mesaj_bilgisi += f"Taranacak Özel Hisseler: {', '.join(custom_symbols)}"
            else:
                mesaj_bilgisi += "Standart liste taranıyor."
            
            print(mesaj_bilgisi)
            
            try:
                job(command_triggered=True, custom_symbols=custom_symbols)
                print("✅ Özel Telegram taraması tamamlandı.")
            except Exception as err:
                print(f"❌ Özel Telegram taraması sırasında hata: {err}")
                send_telegram_message(f"Özel tarama sırasında hata oluştu: {err}")
            return

    # Hiçbir komut eşleşmezse (Düz mesaj atılmışsa son tarama özetini dön)
    print(f"📩 {len(new_messages)} yeni Telegram mesajı alındı. Tarama başlatılmıyor, son tarama özeti gönderiliyor.")
    last_row = get_last_scan_csv_row()
    summary_message = build_last_scan_message(last_row)
    if send_telegram_message(summary_message):
        print("✅ Telegram'a son tarama özeti gönderildi.")


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


def normalize_ohlc_columns(df):
    return df.rename(columns={str(col): str(col).lower() for col in df.columns})

def calculate_smc(df):
    """
    Lookahead bias barındırmayan, tamamen vektörel, katı matematiksel SMC Hesaplayıcısı.
    """
    df = df.copy()

    # Eğer ATR henüz hesaplanmadıysa hesapla (FVG kuralı için şart)
    if "ATRr_14" not in df.columns:
        df["ATRr_14"] = calculate_atr(df, length=14)

    # 1. PİVOT NOKTALARI (Fractal Highs & Lows - Pencereli Tarama: t-4, t-3, t-2, t-1, t)
    # t-2 mumu merkez kabul edilir. Sadece t mumu kapandığında t-2'nin pivot olduğu kesinleşir (Lookahead engeli).
    df['PH_Level'] = np.where((df['High'].shift(2) > df['High'].shift(4)) & 
                              (df['High'].shift(2) > df['High'].shift(3)) & 
                              (df['High'].shift(2) > df['High'].shift(1)) & 
                              (df['High'].shift(2) > df['High']), 
                              df['High'].shift(2), np.nan)
                              
    df['PL_Level'] = np.where((df['Low'].shift(2) < df['Low'].shift(4)) & 
                              (df['Low'].shift(2) < df['Low'].shift(3)) & 
                              (df['Low'].shift(2) < df['Low'].shift(1)) & 
                              (df['Low'].shift(2) < df['Low']), 
                              df['Low'].shift(2), np.nan)
                              
    # Son teyit edilmiş majör tepeleri ve dipleri ileriye doğru taşı (forward fill)
    df['Last_PH'] = df['PH_Level'].ffill()
    df['Last_PL'] = df['PL_Level'].ffill()
    
    # 2. BOS & CHOCH (Sadece Gövdeli Kırılım Kontrolü - Close Fiyatı ile)
    df['PH_ID'] = df['PH_Level'].notna().cumsum()
    df['PL_ID'] = df['PL_Level'].notna().cumsum()
    
    # Yukarı ve aşağı yönlü mutlak gövde kırılımları
    df['Break_Up'] = (df['Close'] > df['Last_PH']) & (df['PH_ID'] > 0)
    df['Break_Down'] = (df['Close'] < df['Last_PL']) & (df['PL_ID'] > 0)
    
    # Aynı tepe/dip seviyesinin birden fazla kez ihlal edilmesini engellemek için sadece İLK kırılım anını al
    df['First_Break_Up'] = df['Break_Up'] & ~(df.groupby('PH_ID')['Break_Up'].shift(1) == True)
    df['First_Break_Down'] = df['Break_Down'] & ~(df.groupby('PL_ID')['Break_Down'].shift(1) == True)
    
    # Trend Yönü: +1 (Uptrend), -1 (Downtrend)
    trend_signals = np.where(df['First_Break_Up'], 1, np.where(df['First_Break_Down'], -1, np.nan))
    df['SMC_Trend'] = pd.Series(trend_signals).ffill().fillna(0)
    
    # Kırılım anından bir önceki trend yönü (BOS ve CHOCH matematiksel ayrımı için)
    prev_trend = df['SMC_Trend'].shift(1).fillna(0)
    
    # BOS: Trend yönündeki devamlılık kırılımı
    df['BOS'] = np.where((df['First_Break_Up'] & (prev_trend == 1)), 1, 
                np.where((df['First_Break_Down'] & (prev_trend == -1)), -1, 0))
                
    # CHOCH: Trendin tersi yönündeki karakter değişimi (Kurumsal Trend Dönüş Onayı)
    df['CHOCH'] = np.where((df['First_Break_Up'] & (prev_trend == -1)), 1, 
                  np.where((df['First_Break_Down'] & (prev_trend == 1)), -1, 0))
                  
    # 3. FAIR VALUE GAP (FVG) - ATR Filtreli Katı Sınırlar
    atr_threshold = 0.20 * df['ATRr_14']
    bull_fvg_cond = (df['Low'] > df['High'].shift(2)) & ((df['Low'] - df['High'].shift(2)) > atr_threshold)
    bear_fvg_cond = (df['High'] < df['Low'].shift(2)) & ((df['Low'].shift(2) - df['High']) > atr_threshold)
    
    # Bullish FVG Takibi
    df['Bull_FVG_Top'] = np.where(bull_fvg_cond, df['Low'], np.nan)
    df['Bull_FVG_Bot'] = np.where(bull_fvg_cond, df['High'].shift(2), np.nan)
    df['Active_Bull_FVG_Top'] = df['Bull_FVG_Top'].ffill()
    df['Active_Bull_FVG_Bot'] = df['Bull_FVG_Bot'].ffill()
    
    # Fiyat FVG'nin altına gövdeyle sarkarsa bölge "invalid" (geçersiz) olur.
    df['Bull_FVG_Invalid'] = df['Close'] < df['Active_Bull_FVG_Bot']
    df['Bull_FVG_ID'] = bull_fvg_cond.cumsum()
    invalid_mask_bull = df.groupby('Bull_FVG_ID')['Bull_FVG_Invalid'].cummax()
    
    # Mitigation: Fiyat henüz ihlal edilmemiş (geçerli) bir FVG bölgesinin içine girdiyse (AL)
    df['Bull_FVG_Mitigated'] = (df['Close'] < df['Active_Bull_FVG_Top']) & (df['Close'] >= df['Active_Bull_FVG_Bot']) & (~invalid_mask_bull) & (df['Bull_FVG_ID'] > 0)
    
    # Bearish FVG Takibi
    df['Bear_FVG_Top'] = np.where(bear_fvg_cond, df['Low'].shift(2), np.nan)
    df['Bear_FVG_Bot'] = np.where(bear_fvg_cond, df['High'], np.nan)
    df['Active_Bear_FVG_Top'] = df['Bear_FVG_Top'].ffill()
    df['Active_Bear_FVG_Bot'] = df['Bear_FVG_Bot'].ffill()
    
    df['Bear_FVG_Invalid'] = df['Close'] > df['Active_Bear_FVG_Top']
    df['Bear_FVG_ID'] = bear_fvg_cond.cumsum()
    invalid_mask_bear = df.groupby('Bear_FVG_ID')['Bear_FVG_Invalid'].cummax()
    
    # Mitigation: Fiyat henüz ihlal edilmemiş bir FVG bölgesinin içine girdiyse (SAT)
    df['Bear_FVG_Mitigated'] = (df['Close'] > df['Active_Bear_FVG_Bot']) & (df['Close'] <= df['Active_Bear_FVG_Top']) & (~invalid_mask_bear) & (df['Bear_FVG_ID'] > 0)
    
    # Nihai FVG Sinyali Sentezi
    df['FVG'] = np.where(df['Bull_FVG_Mitigated'], 1, np.where(df['Bear_FVG_Mitigated'], -1, 0))
    
    # Algoritmanın belleği şişirmemesi için geçici hesaplama kolonlarını temizle
    cols_to_drop = [
        'PH_Level', 'PL_Level', 'Last_PH', 'Last_PL', 'PH_ID', 'PL_ID', 'Break_Up', 'Break_Down',
        'First_Break_Up', 'First_Break_Down', 'SMC_Trend', 'Bull_FVG_Top', 'Bull_FVG_Bot',
        'Active_Bull_FVG_Top', 'Bull_FVG_Invalid', 'Bull_FVG_ID',
        'Bull_FVG_Mitigated', 'Bear_FVG_Top', 'Bear_FVG_Bot', 
        'Active_Bear_FVG_Bot', 'Bear_FVG_Invalid', 'Bear_FVG_ID', 'Bear_FVG_Mitigated'
    ]
    df.drop(columns=[c for c in cols_to_drop if c in df.columns], inplace=True)
    
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
        except (ValueError, KeyError, TypeError, IndexError, RequestException) as err:
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

def get_cached_price_history(ticker_symbol, period, interval):
    key = (ticker_symbol.upper(), period, interval)
    now = time.monotonic()
    with CACHE_LOCK:
        entry = PRICE_HISTORY_CACHE.get(key)
        if entry:
            timestamp, cached_df = entry
            if now - timestamp < CACHE_TTL_SECONDS:
                cached_copy = cached_df.copy(deep=True)
                cached_copy.attrs = dict(cached_df.attrs)
                return source_df(cached_copy, cached_copy.attrs.get("source", "cache"))
            del PRICE_HISTORY_CACHE[key]
    return None


def cache_price_history(ticker_symbol, period, interval, df):
    key = (ticker_symbol.upper(), period, interval)
    cached_df = df.copy(deep=True)
    cached_df.attrs = dict(getattr(df, "attrs", {}))
    with CACHE_LOCK:
        PRICE_HISTORY_CACHE[key] = (time.monotonic(), cached_df)


def get_price_history(ticker_symbol, period, interval, use_cache=True):
    if use_cache:
        cached = get_cached_price_history(ticker_symbol, period, interval)
        if cached is not None:
            print(f"[CACHE] {ticker_symbol} {period}/{interval} cache kullanıldı.")
            return cached
    else:
        key = (ticker_symbol.upper(), period, interval)
        with CACHE_LOCK:
            PRICE_HISTORY_CACHE.pop(key, None)

    if PREFER_ALPHA_VANTAGE and not ALPHA_VANTAGE_DISABLED:
        try:
            result = alpha_vantage_history(ticker_symbol, period, interval)
            cache_price_history(ticker_symbol, period, interval, result)
            return result
        except (ValueError, KeyError, RequestException, Timeout, HTTPError) as alpha_err:
            disable_alpha_vantage_if_needed(alpha_err)
            if ALPHA_VANTAGE_API_KEY:
                print(f"[{ticker_symbol}] Alpha Vantage atlandı: {short_provider_message(alpha_err)}")

    try:
        result = yahoo_chart_history(ticker_symbol, period, interval)
        cache_price_history(ticker_symbol, period, interval, result)
        return result
    except (ValueError, RequestException, Timeout, HTTPError) as chart_err:
        print(f"[{ticker_symbol}] Yahoo chart uyarı: {chart_err}")

    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=False)
        if df.empty:
            raise ValueError(f"{period}/{interval} veri boş döndü.")
        result = source_df(brute_force_cleaner(df), "yfinance")
        cache_price_history(ticker_symbol, period, interval, result)
        return result
    except (ValueError, KeyError, OSError, RequestException) as yf_err:
        print(f"[{ticker_symbol}] yfinance uyarı: {yf_err}")

    if interval in ("1d", "1day"):
        result = nasdaq_daily_history(ticker_symbol, period)
        cache_price_history(ticker_symbol, period, interval, result)
        return result

    try:
        result = nasdaq_intraday_chart(ticker_symbol, interval)
        cache_price_history(ticker_symbol, period, interval, result)
        return result
    except (ValueError, RequestException, Timeout, HTTPError) as nasdaq_err:
        print(f"[{ticker_symbol}] Nasdaq intraday uyarı: {short_provider_message(nasdaq_err)}")

    if ALPHA_VANTAGE_API_KEY and not PREFER_ALPHA_VANTAGE and not ALPHA_VANTAGE_DISABLED:
        try:
            result = alpha_vantage_history(ticker_symbol, period, interval)
            cache_price_history(ticker_symbol, period, interval, result)
            return result
        except (ValueError, KeyError, RequestException, Timeout, HTTPError) as alpha_err:
            disable_alpha_vantage_if_needed(alpha_err)
            print(f"[{ticker_symbol}] Alpha Vantage atlandı: {short_provider_message(alpha_err)}")

    raise ValueError(f"{period}/{interval} için hiçbir veri kaynağı çalışmadı.")


def get_fear_greed_index():
    """Fetch daily Fear & Greed index (0-100) from CNN for the Stock Market.
    Returns int value or the string 'Bilinmiyor' on failure.
    """
    # CNN Fear & Greed Index (Hisse Senedi Piyasası) için özel JSON endpointi
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    
    try:
        # CNN'in anti-bot filtrelerine takılmamak için botta zaten tanımlı olan HTTP_HEADERS'ı gönderiyoruz
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        
        # CNN'in döndürdüğü JSON yapısından anlık skoru çek:
        # Beklenen yapı: {'fear_and_greed': {'score': 60.5, 'rating': 'greed', ...}}
        score = payload.get("fear_and_greed", {}).get("score")
        
        if score is None:
            return "Bilinmiyor"
            
        return int(score)
    except Exception as err:
        print(f"[SENTIMENT] CNN Hisse Senedi Fear & Greed alınamadı: {err}")
        return "Bilinmiyor"

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
    df["EMA_100"] = df["Close"].ewm(span=100, adjust=False, min_periods=100).mean()
    df["EMA_200"] = df["Close"].ewm(span=200, adjust=False, min_periods=200).mean()
    df["Volume_SMA_20"] = df["Volume"].rolling(20).mean()
    # STOCHASTIC (14,3)
    low_min = df["Low"].rolling(14).min()
    high_max = df["High"].rolling(14).max()
    df["STOCH_%K"] = 100 * (df["Close"] - low_min) / (high_max - low_min)
    df["STOCH_%D"] = df["STOCH_%K"].rolling(3).mean()

    # MACD (12,26,9)
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD_LINE"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD_LINE"].ewm(span=9, adjust=False).mean()
    df["MACD_HIST"] = df["MACD_LINE"] - df["MACD_SIGNAL"]

    # Bollinger Bands (20,2)
    mb = df["Close"].rolling(20).mean()
    mb_std = df["Close"].rolling(20).std()
    df["BB_MID"] = mb
    df["BB_UP"] = mb + 2 * mb_std
    df["BB_LOW"] = mb - 2 * mb_std
    df["BB_WIDTH"] = (df["BB_UP"] - df["BB_LOW"]) / df["BB_MID"].replace(0, np.nan)
    df["BB_SQUEEZE"] = df["BB_WIDTH"] < df["BB_WIDTH"].rolling(100).quantile(0.2)

    # OBV
    obv = [0]
    for i in range(1, len(df)):
        if df["Close"].iat[i] > df["Close"].iat[i - 1]:
            obv.append(obv[-1] + df["Volume"].iat[i])
        elif df["Close"].iat[i] < df["Close"].iat[i - 1]:
            obv.append(obv[-1] - df["Volume"].iat[i])
        else:
            obv.append(obv[-1])
    df["OBV"] = pd.Series(obv, index=df.index)

    # CCI (20)
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    ma_tp = tp.rolling(20).mean()
    mad = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    df["CCI_20"] = (tp - ma_tp) / (0.015 * mad)

    # MFI (14)
    typical = tp
    money_flow = typical * df["Volume"]
    positive_flow = []
    negative_flow = []
    for i in range(1, len(typical)):
        if typical.iat[i] > typical.iat[i - 1]:
            positive_flow.append(money_flow.iat[i])
            negative_flow.append(0)
        else:
            positive_flow.append(0)
            negative_flow.append(money_flow.iat[i])
    positive_flow = pd.Series([0] + positive_flow, index=df.index)
    negative_flow = pd.Series([0] + negative_flow, index=df.index)
    mf_ratio = positive_flow.rolling(14).sum() / negative_flow.rolling(14).sum().replace(0, np.nan)
    df["MFI_14"] = 100 - (100 / (1 + mf_ratio))

    # ADX (14)
    up_move = df["High"].diff()
    down_move = -df["Low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - df["Close"].shift(1)).abs()
    tr3 = (df["Low"] - df["Close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr14 = tr.ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * (pd.Series(plus_dm).ewm(alpha=1/14, adjust=False).mean() / atr14)
    minus_di = 100 * (pd.Series(minus_dm).ewm(alpha=1/14, adjust=False).mean() / atr14)
    dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di)).replace([np.inf, -np.inf], np.nan) * 100
    adx = dx.ewm(alpha=1/14, adjust=False).mean()
    df["PLUS_DI"] = plus_di
    df["MINUS_DI"] = minus_di
    df["ADX_14"] = adx
    return df

def timeframe_analysis(df, ema_200, fallback_volume=None, fallback_volume_sma=None):
    clean = df.dropna(subset=["RSI_14", "ATRr_14"])
    if clean.empty:
        raise ValueError("Timeframe analiz için yeterli veri yok")
    son_mum = clean.iloc[-1]
    prev = clean.iloc[-2] if len(clean) > 1 else son_mum
    volume = son_mum["Volume"]
    volume_sma_20 = son_mum["Volume_SMA_20"]
    if pd.isna(volume) or volume == 0 or pd.isna(volume_sma_20) or volume_sma_20 == 0:
        volume = fallback_volume
        volume_sma_20 = fallback_volume_sma

    ema_analiz = analyze_ema(float(son_mum["Close"]), son_mum["EMA_20"], son_mum["EMA_50"], ema_200)
    hacim_analiz = analyze_volume(volume, volume_sma_20)
    # Stochastic cross
    stoch_signal = "BEKLE"
    try:
        k_prev = prev.get("STOCH_%K", np.nan)
        d_prev = prev.get("STOCH_%D", np.nan)
        k_last = son_mum.get("STOCH_%K", np.nan)
        d_last = son_mum.get("STOCH_%D", np.nan)
        if not np.isnan(k_prev) and not np.isnan(d_prev) and not np.isnan(k_last) and not np.isnan(d_last):
            if k_prev < d_prev and k_last > d_last:
                stoch_signal = "AL"
            elif k_prev > d_prev and k_last < d_last:
                stoch_signal = "SAT"
    except (KeyError, TypeError, ValueError, IndexError):
        stoch_signal = "BEKLE"

    # MACD cross
    macd_signal = "BEKLE"
    try:
        m_prev = prev.get("MACD_LINE", np.nan)
        s_prev = prev.get("MACD_SIGNAL", np.nan)
        m_last = son_mum.get("MACD_LINE", np.nan)
        s_last = son_mum.get("MACD_SIGNAL", np.nan)
        if not np.isnan(m_prev) and not np.isnan(s_prev) and not np.isnan(m_last) and not np.isnan(s_last):
            if m_prev < s_prev and m_last > s_last:
                macd_signal = "AL"
            elif m_prev > s_prev and m_last < s_last:
                macd_signal = "SAT"
    except (KeyError, TypeError, ValueError, IndexError):
        macd_signal = "BEKLE"

    # Bollinger band touch
    bb_signal = "BEKLE"
    try:
        if son_mum.get("Close", 0) >= son_mum.get("BB_UP", np.inf):
            bb_signal = "SAT"
        elif son_mum.get("Close", 0) <= son_mum.get("BB_LOW", -np.inf):
            bb_signal = "AL"
    except (KeyError, TypeError, ValueError):
        bb_signal = "BEKLE"

    # OBV trend signal (compare to 10 bars ago)
    obv_signal = "BEKLE"
    try:
        if len(clean) >= 11:
            obv_now = son_mum.get("OBV", 0)
            obv_10 = clean.iloc[-11].get("OBV", 0)
            price_now = son_mum.get("Close", 0)
            price_10 = clean.iloc[-11].get("Close", 0)
            price_change = (price_now - price_10) / price_10 if price_10 else 0
            obv_change = obv_now - obv_10
            if price_change <= 0 and obv_change > 0:
                obv_signal = "GÜÇLÜ AL"
            elif price_change > 0 and obv_change < 0:
                obv_signal = "GÜÇLÜ SAT"
    except (KeyError, TypeError, ValueError, IndexError):
        obv_signal = "BEKLE"

    # RSI divergence (simple check over last 10)
    rsi_div = "NONE"
    try:
        window = clean.tail(10)
        if len(window) >= 3:
            lows_idx = window["Close"].nsmallest(2).index
            if len(lows_idx) >= 2:
                p1 = window.loc[lows_idx[0], "Close"]
                p2 = window.loc[lows_idx[1], "Close"]
                r1 = window.loc[lows_idx[0], "RSI_14"]
                r2 = window.loc[lows_idx[1], "RSI_14"]
                if p2 < p1 and r2 > r1:
                    rsi_div = "POSITIF"
            highs_idx = window["Close"].nlargest(2).index
            if len(highs_idx) >= 2:
                p1 = window.loc[highs_idx[0], "Close"]
                p2 = window.loc[highs_idx[1], "Close"]
                r1 = window.loc[highs_idx[0], "RSI_14"]
                r2 = window.loc[highs_idx[1], "RSI_14"]
                if p2 > p1 and r2 < r1:
                    rsi_div = "NEGATIF"
    except (KeyError, TypeError, ValueError, IndexError):
        rsi_div = "NONE"

    return {
        "RSI": float(round(son_mum["RSI_14"], 2)),
        "ATR": float(round(son_mum["ATRr_14"], 2)),
        "EMA_Analiz": ema_analiz,
        "Hacim_Analiz": hacim_analiz,
        "Analiz": f"{ema_analiz} | {hacim_analiz}",
        "STOCH_Signal": stoch_signal,
        "MACD_Signal": macd_signal,
        "BB_Signal": bb_signal,
        "OBV_Signal": obv_signal,
        "RSI_Divergence": rsi_div,
        "ADX": float(round(son_mum.get("ADX_14", np.nan), 2)) if not pd.isna(son_mum.get("ADX_14", np.nan)) else None,
        "PLUS_DI": float(round(son_mum.get("PLUS_DI", np.nan), 2)) if not pd.isna(son_mum.get("PLUS_DI", np.nan)) else None,
        "MINUS_DI": float(round(son_mum.get("MINUS_DI", np.nan), 2)) if not pd.isna(son_mum.get("MINUS_DI", np.nan)) else None,
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

    def strong_signal(base, strong_condition):
        if base == "AL" and strong_condition:
            return "GÜÇLÜ AL"
        if base == "SAT" and strong_condition:
            return "GÜÇLÜ SAT"
        return base

    def same_direction(a, b):
        return a != 0 and a == b

    ema_1h = "AL" if trend_is_bullish(paket["EMA_Analiz"]) else "SAT" if trend_is_bearish(paket["EMA_Analiz"]) else "BEKLE"
    ema_4h = "AL" if trend_is_bullish(paket["EMA_Analiz_4H"]) else "SAT" if trend_is_bearish(paket["EMA_Analiz_4H"]) else "BEKLE"
    # RSI rules per spec
    if rsi is None:
        rsi_signal = "BEKLE"
    else:
        rsi_signal = "SAT" if rsi >= 70 else "AL" if rsi <= 30 else ("AL" if rsi > 50 else "SAT" if rsi < 50 else "BEKLE")
    hacim_signal = (
        "AL"
        if "Yüksek hacim" in paket["Hacim_Analiz"] and trend_is_bullish(paket["EMA_Analiz"])
        else "SAT"
        if "Yüksek hacim" in paket["Hacim_Analiz"] and trend_is_bearish(paket["EMA_Analiz"])
        else "BEKLE"
    )

    # Derived indicator signals
    stoch_base = paket.get("STOCH_Signal", "BEKLE")
    macd_base = paket.get("MACD_Signal", "BEKLE")
    bb_base = paket.get("BB_Signal", "BEKLE")
    obv_base = paket.get("OBV_Signal", "BEKLE")

    # Strength conditions
    rsi_div = paket.get("RSI_Divergence", "NONE")
    macd_line = paket.get("MACD_LINE")
    stoch_k = paket.get("STOCH_K")

    sinyaller = {
        "EMA 1H": strong_signal(ema_1h, "Güçlü yükseliş" in paket["EMA_Analiz"] or "Güçlü düşüş" in paket["EMA_Analiz"]),
        "EMA 4H": strong_signal(ema_4h, "Güçlü yükseliş" in paket["EMA_Analiz_4H"] or "Güçlü düşüş" in paket["EMA_Analiz_4H"]),
        "RSI": ("GÜÇLÜ SAT" if rsi_div == "NEGATIF" else ("GÜÇLÜ AL" if rsi_div == "POSITIF" else strong_signal(rsi_signal, rsi <= 20 or rsi >= 80))),
        "Hacim 1H": strong_signal(hacim_signal, "Yüksek hacim" in paket["Hacim_Analiz"] and ("Güçlü yükseliş" in paket["EMA_Analiz"] or "Güçlü düşüş" in paket["EMA_Analiz"])),
        "Stochastic": ("GÜÇLÜ AL" if stoch_base == "AL" and stoch_k is not None and stoch_k <= 20 else ("GÜÇLÜ SAT" if stoch_base == "SAT" and stoch_k is not None and stoch_k >= 80 else stoch_base)),
        "MACD": ("GÜÇLÜ AL" if macd_base == "AL" and macd_line is not None and macd_line > 0 else ("GÜÇLÜ SAT" if macd_base == "SAT" and macd_line is not None and macd_line < 0 else macd_base)),
        "Bollinger": bb_base,
        "OBV": obv_base,
        "FVG 1H": strong_signal("AL" if fvg_yon == 1 else "SAT" if fvg_yon == -1 else "BEKLE", same_direction(fvg_yon, fvg_yon_4h)),
        "FVG 4H": strong_signal("AL" if fvg_yon_4h == 1 else "SAT" if fvg_yon_4h == -1 else "BEKLE", same_direction(fvg_yon, fvg_yon_4h)),
        "BOS 1H": strong_signal("AL" if bos_yon == 1 else "SAT" if bos_yon == -1 else "BEKLE", same_direction(bos_yon, bos_yon_4h)),
        "BOS 4H": strong_signal("AL" if bos_yon_4h == 1 else "SAT" if bos_yon_4h == -1 else "BEKLE", same_direction(bos_yon, bos_yon_4h)),
        "CHOCH 1H": strong_signal("AL" if choch_yon == 1 else "SAT" if choch_yon == -1 else "BEKLE", same_direction(choch_yon, choch_yon_4h)),
        "CHOCH 4H": strong_signal("AL" if choch_yon_4h == 1 else "SAT" if choch_yon_4h == -1 else "BEKLE", same_direction(choch_yon, choch_yon_4h)),
        "ADX": "DUSUK" if (paket.get("ADX") is not None and paket.get("ADX") < 20) else ("GUC" if paket.get("ADX") is not None and paket.get("ADX") >= 25 else "ORTA"),
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


def get_earnings_date(ticker_symbol):
    """Hissenin bir sonraki bilanço (Earnings) açıklama tarihini yfinance üzerinden çeker."""
    try:
        ticker = yf.Ticker(ticker_symbol)
        cal = ticker.calendar
        # yfinance sürümüne göre takvim dict veya DataFrame dönebilir
        if isinstance(cal, dict) and 'Earnings Date' in cal:
            dates = cal['Earnings Date']
            if dates and len(dates) > 0:
                # tz_naive'e çeviriyoruz ki datetime ile kıyaslanabilsin
                return pd.to_datetime(dates[0]).replace(tzinfo=None)
        elif isinstance(cal, pd.DataFrame) and 'Earnings Date' in cal.index:
            return pd.to_datetime(cal.loc['Earnings Date'].iloc[0]).replace(tzinfo=None)
    except Exception as e:
        print(f"[{ticker_symbol}] Earnings tarihi alınamadı: {e}")
    return None

def get_recent_news(ticker_symbol, max_items=3):
    """Hissenin son 24 saat içindeki haberlerini çeker."""
    try:
        ticker = yf.Ticker(ticker_symbol)
        news_data = ticker.news
        if not news_data:
            return []

        recent_news = []
        now_ts = time.time()
        for item in news_data:
            # Haberlerin yayınlanma zamanını (Unix timestamp) al
            pub_ts = item.get("providerPublishTime", 0)
            
            # Son 24 saat (86400 saniye) filtresi
            if now_ts - pub_ts <= 86400:
                dt_str = datetime.fromtimestamp(pub_ts).strftime("%H:%M")
                recent_news.append({
                    "baslik": item.get("title", "Başlık Yok"),
                    "kaynak": item.get("publisher", "Bilinmeyen Kaynak"),
                    "tarih": dt_str
                })
            if len(recent_news) >= max_items:
                break
        return recent_news
    except Exception as e:
        print(f"[{ticker_symbol}] Haberler alınamadı: {e}")
        return []

def earnings_risk_flag(earnings_date):
    """Earnings tarihine kaç gün kaldığını hesaplayıp risk etiketi döndürür."""
    if not earnings_date:
        return None
    
    # Sadece gün farkına bakıyoruz
    days_diff = (earnings_date.date() - datetime.now().date()).days
    
    if 0 <= days_diff <= 3:
        return f"YÜKSEK RİSK — Kazanç açıklaması {earnings_date.strftime('%Y-%m-%d')}"
    elif 3 < days_diff <= 7:
        return f"ORTA RİSK — Kazanç açıklaması {earnings_date.strftime('%Y-%m-%d')}"
    
    return None


def get_market_data(ticker_symbol, max_deneme=3, use_cache=True):
    """Piyasa verilerini çeker ve indikatörleri hesaplar."""
    for deneme in range(max_deneme):
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {ticker_symbol} taranıyor (Deneme {deneme+1})...")
            
            # Günlük Veri
            df_daily = get_price_history(ticker_symbol, period="1y", interval="1d", use_cache=use_cache)
                
            guncel_fiyat = float(df_daily['Close'].iloc[-1])
            sma_50 = float(df_daily['Close'].rolling(50).mean().iloc[-1])
            makro_trend = "Yukselis" if guncel_fiyat > sma_50 else "Dusus"
            df_daily["EMA_20"] = df_daily["Close"].ewm(span=20, adjust=False, min_periods=20).mean()
            df_daily["EMA_50"] = df_daily["Close"].ewm(span=50, adjust=False, min_periods=50).mean()
            df_daily["EMA_200"] = df_daily["Close"].ewm(span=200, adjust=False, min_periods=200).mean()
            df_daily["Volume_SMA_20"] = df_daily["Volume"].rolling(20).mean()
            
            # Saatlik Veri
            try:
                df_hourly = get_price_history(ticker_symbol, period="3mo", interval="1h", use_cache=use_cache)
                veri_periyodu = "1h"
            except (RequestException, ValueError, KeyError, IndexError, OSError) as hourly_err:
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
            
            # ========================================================
            # YENİ VEKTÖREL SMC ENTEGRASYONU (ESKİ TRY-EXCEPT'LER SİLİNDİ)
            # ========================================================
            df_hourly = calculate_smc(df_hourly)
            df_4h = calculate_smc(df_4h)

            df_hourly = df_hourly.dropna(subset=['RSI_14', 'ATRr_14'])
            if len(df_hourly) == 0:
                raise ValueError("Hesaplamalardan sonra tablo tamamen boşaldı!")
            
            # Na Değerleri güvenlik amaçlı 0'a çekiyoruz
            df_hourly[['FVG', 'BOS', 'CHOCH']] = df_hourly.get(['FVG', 'BOS', 'CHOCH'], 0).fillna(0)
            df_4h[['FVG', 'BOS', 'CHOCH']] = df_4h.get(['FVG', 'BOS', 'CHOCH'], 0).fillna(0)
            # ========================================================
                
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

            # attach fear/greed index (try fresh fetch; non-fatal)
            fgi = get_fear_greed_index()
            
            # Temel Analiz Verilerini ve Sektörü Çek
            forward_pe = None
            price_to_book = None
            sektor = "Bilinmiyor"
            try:
                ticker_obj = yf.Ticker(ticker_symbol)
                info = ticker_obj.info
                
                forward_pe = info.get('forwardPE')
                if forward_pe is None:
                    forward_pe = info.get('trailingPE')
                price_to_book = info.get('priceToBook')
                sektor = info.get('sector', "Bilinmiyor")
                
                if forward_pe is not None:
                    forward_pe = float(forward_pe)
                if price_to_book is not None:
                    price_to_book = float(price_to_book)
                    
                print(f"[{ticker_symbol}] Temel Analiz: F/K={forward_pe}, PD/DD={price_to_book}, Sektör={sektor}")
            except Exception as fundamental_err:
                print(f"[{ticker_symbol}] Temel analiz verisi alınamadı (non-fatal): {fundamental_err}")
                forward_pe = None
                price_to_book = None
                sektor = "Bilinmiyor"
            
            earnings_dt = get_earnings_date(ticker_symbol)
            earnings_risk = earnings_risk_flag(earnings_dt)
            recent_news = get_recent_news(ticker_symbol)

            paket = {
                "Hisse": ticker_symbol,
                "Guncel_Fiyat": round(guncel_fiyat, 2),
                "Makro_Trend": makro_trend,
                "ForwardPE": forward_pe,
                "PriceToBook": price_to_book,
                "Sektor": sektor,
                "Earnings_Tarihi": earnings_dt.strftime('%Y-%m-%d') if earnings_dt else "Bilinmiyor",
                "Earnings_Riski": earnings_risk,
                "Son_Haberler": recent_news,
                "RSI": analiz_1h["RSI"],
                "ATR_4H": float(round(son_mum_4h.get("ATRr_14", analiz_1h["ATR"]), 2)),
                "FVG_Bull_Bot_4H": float(son_mum_4h.get("Active_Bull_FVG_Bot", np.nan)),
                "FVG_Bear_Top_4H": float(son_mum_4h.get("Active_Bear_FVG_Top", np.nan)),
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
                "FVG_Yon": int(0 if pd.isna(son_mum.get('FVG', np.nan)) else son_mum.get('FVG', 0)),
                "FVG_Yon_4H": int(0 if pd.isna(son_mum_4h.get('FVG', np.nan)) else son_mum_4h.get('FVG', 0)),
                "BOS_Yon": int(0 if pd.isna(son_mum.get('BOS', np.nan)) else son_mum.get('BOS', 0)),
                "BOS_Yon_4H": int(0 if pd.isna(son_mum_4h.get('BOS', np.nan)) else son_mum_4h.get('BOS', 0)),
                "CHOCH_Yon": int(0 if pd.isna(son_mum.get('CHOCH', np.nan)) else son_mum.get('CHOCH', 0)),
                "CHOCH_Yon_4H": int(0 if pd.isna(son_mum_4h.get('CHOCH', np.nan)) else son_mum_4h.get('CHOCH', 0)),
                "STOCH_Signal": analiz_1h.get("STOCH_Signal", "BEKLE"),
                "MACD_Signal": analiz_1h.get("MACD_Signal", "BEKLE"),
                "BB_Signal": analiz_1h.get("BB_Signal", "BEKLE"),
                "OBV_Signal": analiz_1h.get("OBV_Signal", "BEKLE"),
                "RSI_Divergence": analiz_1h.get("RSI_Divergence", "NONE"),
                "ADX": analiz_1h.get("ADX"),
                "PLUS_DI": analiz_1h.get("PLUS_DI"),
                "MINUS_DI": analiz_1h.get("MINUS_DI"),
                "STOCH_K": float(round(son_mum.get("STOCH_%K", np.nan), 2)) if not pd.isna(son_mum.get("STOCH_%K", np.nan)) else None,
                "STOCH_D": float(round(son_mum.get("STOCH_%D", np.nan), 2)) if not pd.isna(son_mum.get("STOCH_%D", np.nan)) else None,
                "MACD_LINE": float(round(son_mum.get("MACD_LINE", np.nan), 4)) if not pd.isna(son_mum.get("MACD_LINE", np.nan)) else None,
                "MACD_SIGNAL": float(round(son_mum.get("MACD_SIGNAL", np.nan), 4)) if not pd.isna(son_mum.get("MACD_SIGNAL", np.nan)) else None,
                "MACD_HIST": float(round(son_mum.get("MACD_HIST", np.nan), 4)) if not pd.isna(son_mum.get("MACD_HIST", np.nan)) else None,
                "BB_UP": float(round(son_mum.get("BB_UP", np.nan), 4)) if not pd.isna(son_mum.get("BB_UP", np.nan)) else None,
                "BB_LOW": float(round(son_mum.get("BB_LOW", np.nan), 4)) if not pd.isna(son_mum.get("BB_LOW", np.nan)) else None,
                "OBV": float(round(son_mum.get("OBV", np.nan), 2)) if not pd.isna(son_mum.get("OBV", np.nan)) else None,
                "CCI_20": float(round(son_mum.get("CCI_20", np.nan), 2)) if not pd.isna(son_mum.get("CCI_20", np.nan)) else None,
                "MFI_14": float(round(son_mum.get("MFI_14", np.nan), 2)) if not pd.isna(son_mum.get("MFI_14", np.nan)) else None,
                "Veri_Kaynagi": df_hourly.attrs.get("source", "Bilinmiyor"),
                "Veri_Periyodu": veri_periyodu,
                "DF_HOURLY": df_hourly.copy(deep=True),
                "DF_4H": df_4h.copy(deep=True),
            }
            paket["Fear_Greed_Index"] = fgi
            return paket
            
        except Exception as e:
            print(f"❌ [{ticker_symbol}] HATA DETAYI: {repr(e)}")
            time.sleep(3)
            
    print(f"⚠️ {ticker_symbol} verisi alınamadı, pas geçiliyor.\n")
    return None

def rule_based_engine(paket):
    df_1h = paket["DF_HOURLY"]
    df_4h = paket["DF_4H"]
    last_1h = df_1h.iloc[-1]
    last_4h = df_4h.iloc[-1]
    fiyat = paket["Guncel_Fiyat"]
    atr = paket["ATR"]

    # NaN (Boş) veri koruması
    def val(x, default=0):
        return default if pd.isna(x) else x

    signals = {}
    
    # -- 1. SMC SİNYALLERİ --
    signals["4H_FVG"] = 1 if paket.get("FVG_Yon_4H") == 1 else (-1 if paket.get("FVG_Yon_4H") == -1 else 0)
    signals["4H_CHOCH"] = 1 if paket.get("CHOCH_Yon_4H") == 1 else (-1 if paket.get("CHOCH_Yon_4H") == -1 else 0)
    signals["4H_BOS"] = 1 if paket.get("BOS_Yon_4H") == 1 else (-1 if paket.get("BOS_Yon_4H") == -1 else 0)
    signals["1H_FVG"] = 1 if paket.get("FVG_Yon") == 1 else (-1 if paket.get("FVG_Yon") == -1 else 0)
    signals["1H_CHOCH"] = 1 if paket.get("CHOCH_Yon") == 1 else (-1 if paket.get("CHOCH_Yon") == -1 else 0)
    signals["1H_BOS"] = 1 if paket.get("BOS_Yon") == 1 else (-1 if paket.get("BOS_Yon") == -1 else 0)

    # -- 2. TREND SİNYALLERİ --
    e4_50, e4_200 = val(last_4h.get("EMA_50")), val(last_4h.get("EMA_200"))
    signals["4H_EMA_50_200"] = 1 if (last_4h["Close"] > e4_50 > e4_200) else (-1 if (last_4h["Close"] < e4_50 < e4_200) else 0)
    
    e1_50, e1_200 = val(last_1h.get("EMA_50")), val(last_1h.get("EMA_200"))
    signals["1H_EMA_50_200"] = 1 if (last_1h["Close"] > e1_50 > e1_200) else (-1 if (last_1h["Close"] < e1_50 < e1_200) else 0)

    # YENİ MACD DELTA MANTIĞI (İvme / 1. Türev)
    hist_1h = val(last_1h.get("MACD_HIST"))
    hist_1h_prev = val(df_1h.iloc[-2].get("MACD_HIST")) if len(df_1h) > 1 else 0
    macd_delta = hist_1h - hist_1h_prev
    signals["MACD_Hist_Delta"] = 1 if macd_delta > 0 else -1
    # MACD Line ve Signal overfitting engellemek için silindi.

    adx_val, p_di, m_di = val(last_1h.get("ADX_14")), val(last_1h.get("PLUS_DI")), val(last_1h.get("MINUS_DI"))
    signals["ADX_Value"] = 1 if (adx_val > 25 and p_di > m_di) else (-1 if (adx_val > 25 and m_di > p_di) else 0)
    signals["PLUS_DI"] = 1 if p_di > 25 else 0
    signals["MINUS_DI"] = -1 if m_di > 25 else 0

    # -- 3. HACİM SİNYALLERİ --
    obv_slope = 0
    if len(df_4h) >= 5:
        obv_slope = val(last_4h.get("OBV")) - val(df_4h.iloc[-5].get("OBV"))
    signals["OBV"] = 1 if obv_slope > 0 else -1

    mfi_val = val(last_1h.get("MFI_14"), 50)
    signals["MFI"] = 1 if mfi_val < 20 else (-1 if mfi_val > 80 else 0)
    
    vol_sma = val(last_1h.get("Volume_SMA_20"), 1)
    vol_sma = vol_sma if vol_sma > 0 else 1
    is_bull_candle = last_1h["Close"] > last_1h["Open"]
    signals["1H_Vol_Spike"] = 1 if (last_1h["Volume"] > vol_sma * 1.5 and is_bull_candle) else (-1 if (last_1h["Volume"] > vol_sma * 1.5 and not is_bull_candle) else 0)

    # -- 4. YAPI SİNYALLERİ --
    destek_1h, direnc_1h = paket.get("Destek_1H"), paket.get("Direnc_1H")
    signals["1H_S_R"] = 1 if (destek_1h and abs(fiyat - destek_1h)/fiyat < 0.015) else (-1 if (direnc_1h and abs(direnc_1h - fiyat)/fiyat < 0.015) else 0)
    
    destek_4h, direnc_4h = paket.get("Destek_4H"), paket.get("Direnc_4H")
    signals["4H_S_R"] = 1 if (destek_4h and abs(fiyat - destek_4h)/fiyat < 0.015) else (-1 if (direnc_4h and abs(direnc_4h - fiyat)/fiyat < 0.015) else 0)

    bb_squeeze = last_1h.get("BB_SQUEEZE", False)
    signals["BB_Squeeze"] = 0.5 if bb_squeeze else 0
    bb_low, bb_up = val(last_1h.get("BB_LOW")), val(last_1h.get("BB_UP"))
    signals["BB_Position"] = 1 if (bb_low and fiyat < bb_low) else (-1 if (bb_up and fiyat > bb_up) else 0)

    # -- 5. MOMENTUM SİNYALLERİ --
    rsi_val = val(last_1h.get("RSI_14"), 50)
    signals["RSI"] = 1 if rsi_val < 30 else (-1 if rsi_val > 70 else (0.5 if rsi_val > 50 else -0.5))

    cci_val = val(last_1h.get("CCI_20"), 0)
    signals["CCI"] = 1 if cci_val < -100 else (-1 if cci_val > 100 else 0)

    # YENİ STOCHASTIC MANTIĞI (Sadece %K ve Kırılım Kontrolü)
    stoch_k = val(last_1h.get("STOCH_%K"), 50)
    stoch_k_prev = val(df_1h.iloc[-2].get("STOCH_%K"), 50) if len(df_1h) > 1 else 50
    
    # Kural: %K yalnızca aşırı bölgeden (<=20 veya >=80) kendi yönünde ivme kazandığında (yukarı/aşağı kestiğinde) puan üretir
    if stoch_k_prev <= 20 and stoch_k > stoch_k_prev:
        signals["Stoch_K"] = 1
    elif stoch_k_prev >= 80 and stoch_k < stoch_k_prev:
        signals["Stoch_K"] = -1
    else:
        signals["Stoch_K"] = 0

    # ===============================================
    # SKORLAMA VE DİNAMİK ÇÖKÜŞ (PENALTY DECAY) MATEMATİĞİ
    # ===============================================
    z_raw = sum(QUANT_WEIGHTS[key] * float(signals[key]) for key in QUANT_WEIGHTS)

    penalty_multiplier = 1.0
    obv_inertia = 0.0

    if signals["4H_CHOCH"] < 0 or signals["OBV"] < 0:
        obv_series = df_1h["OBV"].dropna()
        if len(obv_series) >= 10:
            obv_current = float(obv_series.iloc[-1])
            obv_10_mean = float(obv_series.tail(10).mean())
            obv_10_std = float(obv_series.tail(10).std())
            
            if pd.isna(obv_10_std) or obv_10_std == 0:
                obv_inertia = 0.0
            else:
                obv_inertia = (obv_current - obv_10_mean) / obv_10_std

        if obv_inertia > 2.0:
            penalty_multiplier = 0.15 + (0.50 * (min(obv_inertia, 4.0) - 2.0) / 2.0)
        else:
            penalty_multiplier = 0.15

    z_final = z_raw * penalty_multiplier

    # KARAR EŞİKLERİ
    if z_final >= 800:
        aksiyon = "GÜÇLÜ AL"
    elif z_final >= 550:
        aksiyon = "AL"
    elif z_final > -350:
        aksiyon = "BEKLE"
    elif z_final >= -750:
        aksiyon = "SAT"
    else:
        aksiyon = "GÜÇLÜ SAT"

    # Temel Analiz Koruması
    pe_val, pb_val = paket.get("ForwardPE"), paket.get("PriceToBook")
    pe_ok = (pe_val is None) or (pe_val < 30)
    pb_ok = (pb_val is None) or (pb_val < 10)
    
    if "AL" in aksiyon and not (pe_ok and pb_ok):
        return {
            "aksiyon": "BEKLE",
            "gerekce": f"Skor {z_final:.1f} (AL) verdi ancak Temel Analizi Kötü! (F/K: {pe_val}, PD/DD: {pb_val})",
            "z_final": z_final,
            "z_raw": z_raw
        }
    
    risk_durumu = paket.get("Earnings_Riski")
    if "AL" in aksiyon and risk_durumu and risk_durumu.startswith("YÜKSEK RİSK"):
        return {
            "aksiyon": "BEKLE",
            "gerekce": f"Skor {z_final:.1f} (AL) verdi ancak {risk_durumu}",
            "z_final": z_final,
            "z_raw": z_raw
        }

    gerekce = (
        f"Z-Skor: {z_final:.1f} (Ham: {z_raw:.1f}) | Ceza Modu: {'AKTİF (x' + str(round(penalty_multiplier, 2)) + ')' if penalty_multiplier < 1 else 'YOK'} | "
        f"OBV İvmesi: {obv_inertia:.2f}z | CHOCH 4H: {signals['4H_CHOCH']} | Trend: {signals['4H_EMA_50_200']}"
    )
    
    # === PORTFÖY KİLİDİ KONTROLÜ (DRAWDOWN VETO) ===
    global SYSTEM_LOCK_UNTIL
    if SYSTEM_LOCK_UNTIL and datetime.now() < SYSTEM_LOCK_UNTIL:
        return {
            "aksiyon": "BEKLE",
            "gerekce": f"SİSTEM KİLİTLİ: Max Drawdown (%6) limiti sebebiyle sistem VETO uyguladı. Açılış: {SYSTEM_LOCK_UNTIL.strftime('%d %b %H:%M')}",
            "z_final": z_final,
            "z_raw": z_raw
        }

    # === DİNAMİK STOP VE LOT HESAPLAMASI ===
    if "AL" in aksiyon or "SAT" in aksiyon:
        stop, hedef, lot = RiskManager.calculate_dynamic_position(paket, aksiyon)
    else:
        stop, hedef, lot = "", "", 0

    return {
        "aksiyon": aksiyon,
        "gerekce": gerekce,
        "stop_loss": stop,
        "hedef_fiyat": hedef,
        "lot": lot,
        "z_final": z_final,
        "z_raw": z_raw
    }


def build_decision_message(paket, karar, lot, gerekce):
    aksiyon = karar["aksiyon"]
    sinyaller = indicator_signals(paket)
    
    # Piyasa Durumu
    piyasa_durumu_mesaji = "⚠️ Piyasa Kapalı (Pre/After-Market)\n" if not is_us_market_open() else ""
    
    satirlar = [
        f"{piyasa_durumu_mesaji}{paket['Hisse']} KARAR: {aksiyon}",
        f"Fiyat: ${paket['Guncel_Fiyat']}",
        f"F/K (PE): {paket.get('ForwardPE', 'Bilinmiyor')} | PD/DD (PB): {paket.get('PriceToBook', 'Bilinmiyor')}",
    ]

    # Earnings (Kazanç) Bilgisi
    if paket.get('Earnings_Tarihi') and paket.get('Earnings_Tarihi') != "Bilinmiyor":
        risk_str = paket.get('Earnings_Riski') or "Düşük Risk / Uzak Tarih"
        satirlar.append(f"📅 Earnings: {paket['Earnings_Tarihi']} ({risk_str})")

    # Temel Teknik Veriler
    satirlar.extend([
        f"RSI: {paket['RSI']} | ATR: {paket['ATR']}",
        f"1H: {paket['Analiz_1H']}",
        f"4H: {paket['Analiz_4H']}",
        f"Destek/Direnç 1H: {paket['Destek_Direnc_1H']}",
        f"Destek/Direnç 4H: {paket['Destek_Direnc_4H']}",
        f"Kaynak: {paket['Veri_Kaynagi']} / {paket['Veri_Periyodu']}",
        f"Fear/Greed: {paket.get('Fear_Greed_Index', 'Bilinmiyor')}",
    ])

    # Kantitatif Z-Score
    try:
        z_final = karar.get("z_final", 0)
        z_raw = karar.get("z_raw", 0)
        satirlar.append(f"🧮 Kantitatif Z-Score: {z_final:.1f} (Ham: {z_raw:.1f})")
    except Exception:
        pass

    # Sinyal Dağılımları
    satirlar.extend([
        "İndikatör Sinyalleri:",
        f"  EMA: 1H {sinyaller.get('EMA 1H', 'BEKLE')} | 4H {sinyaller.get('EMA 4H', 'BEKLE')}",
        f"  RSI: {sinyaller.get('RSI', 'BEKLE')} | Hacim 1H: {sinyaller.get('Hacim 1H', 'BEKLE')}",
        f"  FVG: 1H {sinyaller.get('FVG 1H', 'BEKLE')} | 4H {sinyaller.get('FVG 4H', 'BEKLE')}",
        f"  BOS: 1H {sinyaller.get('BOS 1H', 'BEKLE')} | 4H {sinyaller.get('BOS 4H', 'BEKLE')}",
        f"  CHOCH: 1H {sinyaller.get('CHOCH 1H', 'BEKLE')} | 4H {sinyaller.get('CHOCH 4H', 'BEKLE')}",
        f"  S/R: 1H {sinyaller.get('Destek/Direnç 1H', 'BEKLE')} | 4H {sinyaller.get('Destek/Direnç 4H', 'BEKLE')}",
    ])

    # Ek İndikatörler ve Sayısal Değerler
    try:
        satirlar.extend([
            f"  Stochastic: %K {paket.get('STOCH_K')} | %D {paket.get('STOCH_D')}",
            f"  MACD: {sinyaller.get('MACD')} | Line {paket.get('MACD_LINE')} | Signal {paket.get('MACD_SIGNAL')} | Hist {paket.get('MACD_HIST')}",
            f"  Bollinger: {sinyaller.get('Bollinger')} | BB_UP {paket.get('BB_UP')} | BB_LOW {paket.get('BB_LOW')}",
            f"  OBV: {sinyaller.get('OBV')} ({paket.get('OBV')})",
            f"  ADX: {sinyaller.get('ADX')} | +DI {paket.get('PLUS_DI')} | -DI {paket.get('MINUS_DI')}",
            f"  CCI: {paket.get('CCI_20')} | MFI: {paket.get('MFI_14')}",
        ])
    except Exception:
        pass

    # Son Haberler
    haberler = paket.get('Son_Haberler', [])
    if haberler:
        satirlar.append("📰 Son Haberler (24 Saat):")
        # Maksimum 2 haber göster ve başlıkları 60 karakterle sınırla
        for h in haberler[:2]:
            kisa_baslik = h['baslik'][:60] + "..." if len(h['baslik']) > 60 else h['baslik']
            satirlar.append(f"  ▪️ {kisa_baslik} ({h['kaynak']})")

    # Risk ve Hedef Bilgileri (Eğer İşlem Varsa)
    if aksiyon != "BEKLE":
        satirlar.extend(
            [
                f"Hedef: ${karar.get('hedef_fiyat', '-')} | Stop: ${karar.get('stop_loss', '-')}",
                f"Lot: {lot} adet | Risk: ${PORTFOY_BUYUKLUGU * (RISK_YUZDESI / 100):.2f}",
            ]
        )

    # Gerekçe
    satirlar.append(f"\n📝 Gerekçe: {gerekce}")

    return "\n".join(satirlar)

def log_decision(paket, karar):
    zaman = datetime.now().strftime('%Y-%m-%d %H:%M')
    aksiyon = karar["aksiyon"]
    fiyat = paket["Guncel_Fiyat"]
    hisse = paket["Hisse"]
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
    lot = karar.get("lot", 0)
    risk = PORTFOY_BUYUKLUGU * MAX_RISK_PERCENT if stop_loss else 0

    # --- BEKLE VE DUPLICATE FİLTRESİ ---
    if aksiyon.strip().upper() == "BEKLE":
        # BEKLE kararlarını CSV'ye yazarak dosyayı şişirme, sessizce geç
        pass
    elif is_duplicate_signal(hisse, aksiyon):
        print(f"[DUPLICATE] {hisse} için bugün zaten {aksiyon} sinyali mevcut, tekrar loglanmıyor.")
    else:
        # Geçerli ve yepyeni bir sinyal ise CSV'ye kaydet
        log_to_csv([
            zaman,
            hisse,
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


def process_symbol(hisse, use_cache=True):
    paket = get_market_data(hisse, use_cache=use_cache)
    if not paket:
        return {
            "symbol": hisse,
            "status": "missing",
            "result_line": f"{hisse}: VERI ALINAMADI",
            "decision_message": None,
        }

    karar = rule_based_engine(paket)
    lot, gerekce = log_decision(paket, karar)
    aksiyon = karar["aksiyon"]

    result_line = (
        f"{hisse}: {aksiyon} | ${paket['Guncel_Fiyat']} | RSI {paket['RSI']} | "
        f"1H: {paket['Analiz_1H']} | 4H: {paket['Analiz_4H']} | "
        f"S/R 1H: {paket['Destek_Direnc_1H']} | S/R 4H: {paket['Destek_Direnc_4H']} | "
        f"FVG {paket['FVG_Yon']} | {paket['Veri_Kaynagi']}/{paket['Veri_Periyodu']}"
    )

    decision_message = None
    if TELEGRAM_KARAR_MESAJLARI:
        decision_message = build_decision_message(paket, karar, lot, gerekce)

    return {
        "symbol": hisse,
        "status": "ok",
        "paket": paket,
        "karar": karar,
        "lot": lot,
        "gerekce": gerekce,
        "result_line": result_line,
        "decision_message": decision_message,
        "Piyasa_Durumu": "Acik" if is_us_market_open() else "Kapali",
    }


def build_telegram_decision_summary(results):
    signal_lines = []
    status_lines = []
    
    # Sektörel risk analizi için takip değişkenleri
    al_sektorleri = {}
    toplam_al_sayisi = 0

    for item in results:
        status = item.get("status")
        if status == "ok":
            aksiyon = item["karar"].get("aksiyon", "BEKLE")
            sektor = item['paket'].get('Sektor', 'Bilinmiyor')
            
            if aksiyon != "BEKLE":
                # AL veya SAT veren hisselerin özetinde Sektör, F/K ve PD/DD değerlerini göster
                signal_lines.append(
                    f"{item['symbol']}: {aksiyon} | ${item['paket']['Guncel_Fiyat']} | Sektör: {sektor} | F/K: {item['paket'].get('ForwardPE', '-')} | PD/DD: {item['paket'].get('PriceToBook', '-')}"
                )
                
                # Sektörel yığılmayı hesapla (Sadece AL verenleri dahil et)
                if "AL" in aksiyon:
                    toplam_al_sayisi += 1
                    if sektor != "Bilinmiyor":
                        al_sektorleri[sektor] = al_sektorleri.get(sektor, 0) + 1
                        
            else:
                # Eğer hisse BEKLE aldıysa, bunun sebebi temel analizin kötü olması mı diye kontrol et
                gerekce = item.get("gerekce", "")
                if "Temel Analiz kötü" in gerekce:
                    status_lines.append(f"{item['symbol']}: BEKLE (İptal: Temel Analiz Kötü/Hisse Pahalı)")
                else:
                    status_lines.append(f"{item['symbol']}: BEKLE")
        elif status == "missing":
            status_lines.append(f"{item['symbol']}: VERI ALINAMADI")
        elif status == "error":
            status_lines.append(f"{item['symbol']}: TARAYICI HATASI")

    if signal_lines:
        message = "📊 İşlem Sinyalleri:\n" + "\n".join(signal_lines)
        if status_lines:
            message += "\n\nDiğer sonuçlar:\n" + "\n".join(status_lines)
            
        # Sektörel yığılma uyarı motoru
        if toplam_al_sayisi > 0 and al_sektorleri:
            en_cok_tekrar_eden_sektor = max(al_sektorleri, key=al_sektorleri.get)
            tekrar_sayisi = al_sektorleri[en_cok_tekrar_eden_sektor]
            
            # Eğer AL sinyallerinin %50'sinden fazlası (veya sayıca 2'den fazlası) aynı sektördeyse uyar
            if tekrar_sayisi > (toplam_al_sayisi / 2) or tekrar_sayisi > 2:
                message += f"\n\n⚠️ <b>DİKKAT:</b> AL sinyallerinin çoğu <b>{en_cok_tekrar_eden_sektor}</b> sektöründe birikti. Sektörel çöküş riskine karşı portföy çeşitliliğine dikkat et!"
                
        return message

    if status_lines:
        return "📌 Hiç işlem sinyali yok. Tarama sonuçları:\n" + "\n".join(status_lines)

    return "📌 Hiç işlem sinyali yok."


def send_result_with_chart(item):
    """Send decision message + chart (if AL/SAT) for a single scan result."""
    message = None
    if item["status"] == "ok":
        message = item.get("decision_message")
        if not message and item.get("paket") and item.get("karar"):
            message = build_decision_message(item["paket"], item["karar"], item.get("lot", 0), item.get("gerekce", ""))
    if not message:
        message = item["result_line"]

    aksiyon = None
    hisse_sembolu = item.get('symbol', 'Bilinmiyor')
    if item.get("karar"):
        aksiyon = item["karar"].get("aksiyon")

    try:
        # Sadece AL/SAT durumlarında grafik oluştur
        if isinstance(aksiyon, str) and ("AL" in aksiyon or "SAT" in aksiyon):
            print(f"[SEND] {hisse_sembolu} için grafik oluşturuluyor (aksiyon: {aksiyon})...")
            foto = create_candlestick_chart(item.get("paket") or {})
            
            if foto:
                print(f"[SEND] Grafik oluşturuldu: {foto}. Telegram'a gönderiliyor...")
                
                # KRİTİK DÜZELTME: Telegram Caption Limiti 1024 Karakterdir.
                # Bu yüzden fotoğrafa sadece kısa bir başlık ekliyoruz.
                kisa_baslik = f"📊 {hisse_sembolu} - KARAR: {aksiyon}"
                
                sent = send_telegram_photo(foto, caption=kisa_baslik)
                
                # Fotoğraf başarıyla gittiyse, uzun analiz mesajını hemen peşinden at
                if sent:
                    send_telegram_message(message)
                else:
                    # Fotoğraf gidemezse (API hatası vs), veriyi kaybetmemek için yine metni at
                    print(f"[SEND] Fotoğraf gönderilemedi, sadece metin iletiliyor.")
                    send_telegram_message(message)
                    
                # Çöp dosya temizliği
                try:
                    os.remove(foto)
                    print(f"[SEND] Geçici dosya silindi: {foto}")
                except Exception as e_rm:
                    print(f"[SEND] Geçici dosya silinemedi: {e_rm}")
            else:
                print(f"[SEND] Grafik oluşturulamadı for {hisse_sembolu}. Sadece metin gönderiliyor.")
                send_telegram_message(message)
        else:
            # AL veya SAT değilse (yani BEKLE ise) sadece metni gönder
            send_telegram_message(message)
            
    except Exception as e:
        print(f"[SEND] Grafik gönderilirken genel hata: {e}")
        send_telegram_message(message)

    time.sleep(0.5)


def send_telegram_scan_notifications(results, command_triggered=False):
    if not results:
        return

    if command_triggered:
        for item in results:
            send_result_with_chart(item)

        summary_msg = build_telegram_decision_summary(results)
        if send_telegram_message(summary_msg, parse_mode="HTML"):
            print("✅ Telegram karar özeti ve detayları gönderildi.")
        return

    if TELEGRAM_KARAR_MESAJLARI:
        for item in results:
            send_result_with_chart(item)

        summary_msg = build_telegram_decision_summary(results)
        if send_telegram_message(summary_msg, parse_mode="HTML"):
            print("✅ Telegram karar özeti ve detayları gönderildi.")


def job(command_triggered=False, custom_symbols=None):
    print(f"\n🔄 Tarama Başladı ({datetime.now().strftime('%H:%M')})")
    # Tüm işlemlerden önce Max Drawdown kilit kontrolünü çalıştır:
    RiskManager.check_and_apply_drawdown_lock()

    if not is_us_market_open():
        print("[MARKET] ABD piyasası şu an kapalı. Tarama bilgi amaçlı devam ediyor.")
    # Eğer Telegram'dan özel hisseler gönderildiyse onları kullan, yoksa config'i kullan
    if custom_symbols:
        hisseler = [sym.upper() for sym in custom_symbols]
        print(f"📌 Özel hisse listesi taranıyor: {', '.join(hisseler)}")
    else:
        hisseler = STOCK_SYMBOLS
        
    ozet_satirlari = [f"Tarama Özeti ({datetime.now().strftime('%Y-%m-%d %H:%M')})"]
    results = []

    with ThreadPoolExecutor(max_workers=min(8, len(hisseler))) as executor:
        # Future'ları ait oldukları hisse sembolleriyle eşleştirerek (tuple olarak) saklıyoruz
        futures = [(hisse, executor.submit(process_symbol, hisse, use_cache=not command_triggered)) for hisse in hisseler]
        
        for hisse, future in futures:
            try:
                result = future.result()
            except Exception as err:
                print(f"❌ {hisse} tarayıcı hatası: {err}")
                result = {
                    "symbol": hisse,
                    "status": "error",
                    "result_line": f"{hisse}: TARAYICI HATASI",
                    "decision_message": None,
                }
            results.append(result)

    decision_messages = []
    for result in results:
        print(result["result_line"])
        ozet_satirlari.append(result["result_line"])

        if result["status"] == "ok" and result.get("decision_message"):
            decision_messages.append(result["decision_message"])
        elif result["status"] == "missing":
            print(f"ℹ️ {result['symbol']} için veri alınamadı.")
        elif result["status"] == "error":
            print(f"❌ {result['symbol']} işlenirken hata oluştu.")

    if command_triggered:
        # Telegram özel istek taraması (Zaten doğruydu)
        send_telegram_scan_notifications(results, command_triggered=True)
    else:
        # OTOMATİK ZAMANLANMIŞ TARAMALAR (DÜZELTİLEN KISIM)
        # Artık zamanlanmış taramalar da yukarıdaki bildirim/grafik motorundan geçecek
        send_telegram_scan_notifications(results, command_triggered=False)
        
        # Ek olarak, eski usül kısa text özeti isteyenler için:
        if TELEGRAM_TARAMA_OZETI:
            if send_telegram_message("\n".join(ozet_satirlari)):
                print("✅ Telegram tarama özeti gönderildi.")

if __name__ == "__main__":
    schedule_scan(job, SCAN_SCHEDULE)
    schedule.every().sunday.at("20:00").do(weekly_report_job)

    print("🤖 Kuantitatif Bot Aktif")
    print("📅 Hafta içi (Pazartesi-Cuma) belirtilen saatlerde analiz yapılacak")
    
    # ===============================================
    # İLK ÇALIŞTIRMADA WALK-FORWARD KALİBRASYONU (WFA)
    # ===============================================
    try:
        print("\n⚙️ Walk-Forward Kalibrasyonu (WFA) başlatılıyor...")
        # Örnekleme için ana endeks veya ilk hisse üzerinden piyasa rejimini analiz et (Örn: QQQ veya listedeki ilk hisse)
        ornek_hisse = STOCK_SYMBOLS[0]
        paket = get_market_data(ornek_hisse, use_cache=False)
        
        if paket:
            wfa_engine = WalkForwardOptimizer(QUANT_WEIGHTS)
            yeni_agirliklar = wfa_engine.calibrate_weights(paket["DF_HOURLY"], paket["DF_4H"])
            
            # Global değişkeni dinamik olarak güncelle
            QUANT_WEIGHTS.update(yeni_agirliklar)
            print("🚀 Ağırlıklar güncel piyasa rejimine (Son 6 Ay) göre optimize edildi!")
    except Exception as e:
        print(f"⚠️ WFA Kalibrasyonu başarısız, statik varsayılan ağırlıklar kullanılacak. Hata: {e}")
    # ===============================================

    # Uygulama başladığında hemen ilk tarama yapılır
    try:
        job()
    except Exception as e:
        print(f"❌ İlk tarama sırasında hata: {e}")

    last_telegram_update_id, _ = poll_telegram_messages(None)

    while True:
        try:
            schedule.run_pending()
            last_telegram_update_id, new_messages = poll_telegram_messages(last_telegram_update_id)
            if new_messages:
                handle_telegram_polling(new_messages)
            time.sleep(10)
        except Exception as e:
            print(f"❌ Döngü Hatası: {e}")
            time.sleep(60)
