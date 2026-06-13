import pandas as pd
import numpy as np
import warnings
import glob
import os
from scipy.optimize import minimize

warnings.filterwarnings('ignore')

# ==========================================
# 1. KANTİTATİF AĞIRLIKLAR
# ==========================================
QUANT_WEIGHTS = {
    "4H_FVG": 23.0, "4H_CHOCH": 24.0, "4H_BOS": 12.0,
    "1H_FVG": 10.5, "1H_CHOCH": 7.0, "1H_BOS": 4.0,
    "4H_EMA_50_200": 120.0, "MACD_Hist_Delta": 65.5, "ADX_Value": 42.5,
    "1H_EMA_50_200": 31.5, "PLUS_DI": 10.5, "MINUS_DI": 10.5,
    "OBV": 135.5, "MFI": 84.5, "1H_Vol_Spike": 30.0,
    "4H_S_R": 48.5, "BB_Squeeze": 26.5, "BB_Position": 25.5, "1H_S_R": 19.5,
    "RSI": 27.5, "CCI": 22.5, "Stoch_K": 18.5,
    "Relative_Strength": 35.0
}

# ==========================================
# 2. VEKTÖREL İNDİKATÖR VE SMC HESAPLAYICILARI
# ==========================================
def calculate_atr(df, length=14):
    prev_close = df["Close"].shift(1)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - prev_close).abs()
    tr3 = (df["Low"] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/length, adjust=False).mean()

def calculate_smc(df):
    df = df.copy()
    if "ATRr_14" not in df.columns:
        df["ATRr_14"] = calculate_atr(df, 14)

    df['PH_Level'] = np.where((df['High'].shift(2) > df['High'].shift(4)) & (df['High'].shift(2) > df['High'].shift(1)) & (df['High'].shift(2) > df['High']), df['High'].shift(2), np.nan)
    df['PL_Level'] = np.where((df['Low'].shift(2) < df['Low'].shift(4)) & (df['Low'].shift(2) < df['Low'].shift(1)) & (df['Low'].shift(2) < df['Low']), df['Low'].shift(2), np.nan)
    df['Last_PH'] = df['PH_Level'].ffill()
    df['Last_PL'] = df['PL_Level'].ffill()
    df['PH_ID'] = df['PH_Level'].notna().cumsum()
    df['PL_ID'] = df['PL_Level'].notna().cumsum()
    
    df['Break_Up'] = (df['Close'] > df['Last_PH']) & (df['PH_ID'] > 0)
    df['Break_Down'] = (df['Close'] < df['Last_PL']) & (df['PL_ID'] > 0)
    df['First_Break_Up'] = df['Break_Up'] & ~(df.groupby('PH_ID')['Break_Up'].shift(1) == True)
    df['First_Break_Down'] = df['Break_Down'] & ~(df.groupby('PL_ID')['Break_Down'].shift(1) == True)
    
    trend_signals = np.where(df['First_Break_Up'], 1, np.where(df['First_Break_Down'], -1, np.nan))
    df['SMC_Trend'] = pd.Series(trend_signals).ffill().fillna(0)
    prev_trend = df['SMC_Trend'].shift(1).fillna(0)
    
    df['BOS'] = np.where((df['First_Break_Up'] & (prev_trend == 1)), 1, np.where((df['First_Break_Down'] & (prev_trend == -1)), -1, 0))
    df['CHOCH'] = np.where((df['First_Break_Up'] & (prev_trend == -1)), 1, np.where((df['First_Break_Down'] & (prev_trend == 1)), -1, 0))
    
    atr_th = 0.20 * df['ATRr_14']
    bull_fvg_cond = (df['Low'] > df['High'].shift(2)) & ((df['Low'] - df['High'].shift(2)) > atr_th)
    bear_fvg_cond = (df['High'] < df['Low'].shift(2)) & ((df['Low'].shift(2) - df['High']) > atr_th)
    
    df['Active_Bull_FVG_Top'] = np.where(bull_fvg_cond, df['Low'], np.nan)
    df['Active_Bull_FVG_Bot'] = np.where(bull_fvg_cond, df['High'].shift(2), np.nan)
    df['Active_Bull_FVG_Top'] = df['Active_Bull_FVG_Top'].ffill()
    df['Active_Bull_FVG_Bot'] = df['Active_Bull_FVG_Bot'].ffill()
    
    df['Active_Bear_FVG_Top'] = np.where(bear_fvg_cond, df['Low'].shift(2), np.nan).copy()
    df['Active_Bear_FVG_Bot'] = np.where(bear_fvg_cond, df['High'], np.nan).copy()
    df['Active_Bear_FVG_Top'] = pd.Series(df['Active_Bear_FVG_Top']).ffill()
    df['Active_Bear_FVG_Bot'] = pd.Series(df['Active_Bear_FVG_Bot']).ffill()
    
    df['Bull_FVG_Invalid'] = df['Close'] < df['Active_Bull_FVG_Bot']
    df['Bull_FVG_ID'] = bull_fvg_cond.cumsum()
    invalid_bull = df.groupby('Bull_FVG_ID')['Bull_FVG_Invalid'].cummax()
    df['Bull_FVG_Mit'] = (df['Close'] < df['Active_Bull_FVG_Top']) & (df['Close'] >= df['Active_Bull_FVG_Bot']) & (~invalid_bull) & (df['Bull_FVG_ID'] > 0)
    
    df['Bear_FVG_Invalid'] = df['Close'] > df['Active_Bear_FVG_Top']
    df['Bear_FVG_ID'] = bear_fvg_cond.cumsum()
    invalid_bear = df.groupby('Bear_FVG_ID')['Bear_FVG_Invalid'].cummax()
    df['Bear_FVG_Mit'] = (df['Close'] > df['Active_Bear_FVG_Bot']) & (df['Close'] <= df['Active_Bear_FVG_Top']) & (~invalid_bear) & (df['Bear_FVG_ID'] > 0)
    
    df['FVG'] = np.where(df['Bull_FVG_Mit'], 1, np.where(df['Bear_FVG_Mit'], -1, 0))
    return df

def build_mega_dataframe(file_path, benchmark_df=None):
    df = pd.read_csv(file_path, sep=None, engine='python')
    df.columns = df.columns.str.strip().str.lower()
    dt_col = [c for c in df.columns if 'date' in c or 'time' in c][0]
    df[dt_col] = pd.to_datetime(df[dt_col], errors='coerce')
    df = df.dropna(subset=[dt_col]).set_index(dt_col).sort_index()
    
    rename_map = {'open':'Open', 'high':'High', 'low':'Low', 'close':'Close', 'tickvolume':'Volume'}
    if 'tickvolume' not in df.columns and 'volume' in df.columns: rename_map['volume'] = 'Volume'
    df = df.rename(columns=rename_map)[['Open', 'High', 'Low', 'Close', 'Volume']].ffill().dropna()

    df['ATRr_14'] = calculate_atr(df, 14)
    delta = df['Close'].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI_14'] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    macd = df['Close'].ewm(span=12).mean() - df['Close'].ewm(span=26).mean()
    df['MACD_HIST'] = macd - macd.ewm(span=9).mean()
    df['Volume_SMA_20'] = df['Volume'].rolling(20).mean()
    df['OBV'] = np.where(df['Close'] > df['Close'].shift(1), df['Volume'], np.where(df['Close'] < df['Close'].shift(1), -df['Volume'], 0)).cumsum()
    
    df['Low_120'] = df['Low'].rolling(120).min()
    df['High_120'] = df['High'].rolling(120).max()
    df['STOCH_%K'] = 100 * (df["Close"] - df["Low"].rolling(14).min()) / (df["High"].rolling(14).max() - df["Low"].rolling(14).min())
    
    # --- 1. SMC HESAPLAMASI ---
    df = calculate_smc(df)
    
    # ==============================================================
    # YENİ EKLENEN KRİTİK İNDİKATÖRLER (SMC'den Sonra, 4H'den Önce)
    # ==============================================================
    
    # 1. CCI (20 Periyot)
    tp = (df['High'] + df['Low'] + df['Close']) / 3
    tp_sma = tp.rolling(window=20).mean()
    mad = tp.rolling(window=20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df['CCI_20'] = ((tp - tp_sma) / (0.015 * mad)).fillna(0)

    # 2. ADX + PLUS_DI + MINUS_DI (14 Periyot)
    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    atr14 = df['ATRr_14']
    plus_di14 = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr14
    minus_di14 = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr14
    dx = 100 * np.abs(plus_di14 - minus_di14) / (plus_di14 + minus_di14).replace(0, np.nan)
    adx14 = dx.ewm(alpha=1/14, adjust=False).mean()
    
    df['ADX_14'] = adx14.fillna(0)
    df['PLUS_DI_14'] = plus_di14.fillna(0)
    df['MINUS_DI_14'] = minus_di14.fillna(0)

    # 3. MFI (14 Periyot)
    raw_mf = tp * df['Volume']
    pos_flow = pd.Series(np.where(tp > tp.shift(1), raw_mf, 0), index=df.index).rolling(14).sum()
    neg_flow = pd.Series(np.where(tp < tp.shift(1), raw_mf, 0), index=df.index).rolling(14).sum()
    mfi = 100 - (100 / (1 + pos_flow / neg_flow.replace(0, np.nan)))
    df['MFI_14'] = mfi.fillna(50)  # Division by zero durumunda nötr 50

    # 4. Bollinger Bantları (20, 2)
    bb_sma = df['Close'].rolling(window=20).mean()
    bb_std = df['Close'].rolling(window=20).std()
    bb_upper = bb_sma + 2 * bb_std
    bb_lower = bb_sma - 2 * bb_std
    df['BB_Squeeze'] = (bb_upper - bb_lower) / bb_sma
    
    bb_position = (df['Close'] - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)
    df['BB_Position'] = bb_position.clip(0, 1).fillna(0.5)

    if benchmark_df is not None and 'Close' in benchmark_df.columns:
        stock_daily_close = df['Close'].resample('1D').last().dropna()
        benchmark_daily_close = benchmark_df['Close'].resample('1D').last().dropna()
        stock_60d_return = stock_daily_close.pct_change(60)
        benchmark_60d_return = benchmark_daily_close.pct_change(60)
        rs_60d = stock_60d_return - benchmark_60d_return.reindex(stock_60d_return.index).ffill()
        df['RS_60D'] = rs_60d.reindex(df.index, method='ffill').fillna(0)
    else:
        df['RS_60D'] = 0.0
    
    # ==============================================================
    
    df_4h = df.resample('4h').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last', 'Volume':'sum'}).dropna()
    df_4h['EMA_50'] = df_4h['Close'].ewm(span=50, adjust=False).mean()
    df_4h['EMA_200'] = df_4h['Close'].ewm(span=200, adjust=False).mean()
    df_4h['Low_120'] = df_4h['Low'].rolling(120).min()
    df_4h['High_120'] = df_4h['High'].rolling(120).max()
    # --- YENİ EKLENEN: 4H ATR Hesaplaması ---
    df_4h['ATRr_14'] = calculate_atr(df_4h, 14)
    df_4h = calculate_smc(df_4h)
    
    # ATRr_14 de hizalamaya dahil edildi
    cols_4h = ['FVG', 'CHOCH', 'BOS', 'EMA_50', 'EMA_200', 'Low_120', 'High_120', 'Close', 'Active_Bull_FVG_Bot', 'ATRr_14']
    df_4h_renamed = df_4h[cols_4h].add_suffix('_4H')
    
    # 🚨 KRİTİK DEĞİŞİKLİK: Look-ahead bias (geleceği görme) hatasını önleme 🚨
    # 4H barı kapandıktan SONRAKİ 1H barına hizala
    df_4h_shifted = df_4h_renamed.shift(1)  
    combined_idx = df.index.union(df_4h_shifted.index).sort_values()
    df_4h_aligned = df_4h_shifted.reindex(combined_idx).ffill().loc[df.index]
    
    for col in df_4h_aligned.columns:
        df[col] = df_4h_aligned[col]
    
    z = pd.DataFrame(index=df.index)
    c = df['Close']
    z['4H_FVG'] = np.sign(df['FVG_4H'])
    z['4H_CHOCH'] = np.sign(df['CHOCH_4H'])
    z['4H_BOS'] = np.sign(df['BOS_4H'])
    z['1H_FVG'] = np.sign(df['FVG'])
    z['1H_CHOCH'] = np.sign(df['CHOCH'])
    z['1H_BOS'] = np.sign(df['BOS'])
    z['4H_EMA_50_200'] = np.where((df['Close_4H'] > df['EMA_50_4H']) & (df['EMA_50_4H'] > df['EMA_200_4H']), 1, -1)
    z['1H_EMA_50_200'] = np.where((c > df['EMA_50']) & (df['EMA_50'] > df['EMA_200']), 1, -1)
    z['MACD_Hist_Delta'] = np.where(df['MACD_HIST'].diff() > 0, 1, -1)
    z['OBV'] = np.where(df['OBV'].diff(5) > 0, 1, -1)
    z['1H_Vol_Spike'] = np.where((df['Volume'] > df['Volume_SMA_20'] * 1.5) & (c > df['Open']), 1, 0)
    z['RSI'] = np.where(df['RSI_14'] < 30, 1, np.where(df['RSI_14'] > 70, -1, 0.5))
    z['Stoch_K'] = np.where((df['STOCH_%K'].shift(1) <= 20) & (df['STOCH_%K'] > df['STOCH_%K'].shift(1)), 1, 0)
    z['1H_S_R'] = np.where(abs(c - df['Low_120'])/c < 0.015, 1, 0)
    z['4H_S_R'] = np.where(abs(c - df['Low_120_4H'])/c < 0.015, 1, 0)
    
    # --- YENİ EKLENEN SİNYAL VE Z-SCORE AKTARIMLARI ---
    z['CCI'] = np.where(df['CCI_20'] < -100, 1, np.where(df['CCI_20'] > 100, -1, 0))
    z['ADX_Value'] = np.where(df['ADX_14'] > 25, 1, np.where(df['ADX_14'] < 20, -1, 0))
    z['PLUS_DI']  = np.where(df['PLUS_DI_14'] > df['MINUS_DI_14'], 1, -1)
    z['MINUS_DI'] = np.where(df['MINUS_DI_14'] > df['PLUS_DI_14'], -1, 1)
    z['MFI'] = np.where(df['MFI_14'] < 20, 1, np.where(df['MFI_14'] > 80, -1, 0))
    z['BB_Squeeze'] = np.where(df['BB_Squeeze'] < df['BB_Squeeze'].rolling(50).mean(), 1, -1)
    z['BB_Position'] = np.where(df['BB_Position'] < 0.2, 1, np.where(df['BB_Position'] > 0.8, -1, 0))
    z['Relative_Strength'] = np.where(df['RS_60D'] > 0.10, 1, np.where(df['RS_60D'] < -0.10, -1, 0))
    
    # DİKKAT: Hatalı olan OBV doldurma bloğunu SİLDİK!

    for col in z.columns:
        df[col] = z[col]

    df['Z_RAW'] = sum(z[col] * QUANT_WEIGHTS[col] for col in QUANT_WEIGHTS)
    penalty_mask = (z['OBV'] < 0) | (z['4H_CHOCH'] < 0)
    df['Z_FINAL'] = np.where(penalty_mask, df['Z_RAW'] * 0.15, df['Z_RAW'])
    
    return df

# ==========================================
# 3. TOPLU TARAYICI İÇİN SESSİZ MOTOR
# ==========================================
class ScannerBacktester:
    def __init__(self, df, verbose=True, disabled_features=None, weights_override=None):
        self.df = df
        self.verbose = verbose
        self.disabled_features = set() if disabled_features is None else set(disabled_features)
        self.dinamik_agirliklar = QUANT_WEIGHTS.copy() if weights_override is None else weights_override.copy()
        self.wfa_keys = list(self.dinamik_agirliklar.keys())
        self.entry_threshold = 400.0
        self.exit_threshold = 550.0
        
        # Gerçek piyasa maliyetleri (Curve-fitting iptal)
        self.komisyon_oran = 0.001     # %0.1
        self.slippage_oran = 0.0005    # %0.05
        self.trade_log = []            # 🚨 YENİ: Her işlemin PnL'ini tutan liste
        
    def _calibrate_wfa(self, train_df, entry_threshold=400.0):
        if len(train_df) < 500: return self.dinamik_agirliklar
        
        forward_returns = train_df.get('Close', pd.Series(0, index=train_df.index)).pct_change().shift(-1).fillna(0).values
        signals_matrix = np.zeros((len(train_df), len(self.wfa_keys)))
        s0 = pd.Series(0, index=train_df.index)
        
        for i, key in enumerate(self.wfa_keys):
            if key == "4H_FVG":
                signals_matrix[:, i] = np.sign(train_df.get('FVG_4H', s0)).fillna(0).values
            elif key == "4H_CHOCH":
                signals_matrix[:, i] = np.sign(train_df.get('CHOCH_4H', s0)).fillna(0).values
            elif key == "4H_BOS":
                signals_matrix[:, i] = np.sign(train_df.get('BOS_4H', s0)).fillna(0).values
            elif key == "1H_FVG":
                signals_matrix[:, i] = np.sign(train_df.get('FVG', s0)).fillna(0).values
            elif key == "1H_CHOCH":
                signals_matrix[:, i] = np.sign(train_df.get('CHOCH', s0)).fillna(0).values
            elif key == "1H_BOS":
                signals_matrix[:, i] = np.sign(train_df.get('BOS', s0)).fillna(0).values
            elif key == "4H_EMA_50_200":
                signals_matrix[:, i] = np.where((train_df.get('Close_4H', s0) > train_df.get('EMA_50_4H', s0)) & 
                                                (train_df.get('EMA_50_4H', s0) > train_df.get('EMA_200_4H', s0)), 1, -1)
            elif key == "1H_EMA_50_200":
                signals_matrix[:, i] = np.where((train_df.get('Close', s0) > train_df.get('EMA_50', s0)) & 
                                                (train_df.get('EMA_50', s0) > train_df.get('EMA_200', s0)), 1, -1)
            elif key == "MACD_Hist_Delta":
                signals_matrix[:, i] = np.where(train_df.get('MACD_HIST', s0).diff().fillna(0) > 0, 1, -1)
            elif key == "ADX_Value":
                signals_matrix[:, i] = np.where(train_df.get('ADX_14', s0) > 25, 1, 
                                                np.where(train_df.get('ADX_14', s0) < 20, -1, 0))
            elif key == "PLUS_DI":
                signals_matrix[:, i] = np.where(train_df.get('PLUS_DI_14', s0) > train_df.get('MINUS_DI_14', s0), 1, -1)
            elif key == "MINUS_DI":
                signals_matrix[:, i] = np.where(train_df.get('MINUS_DI_14', s0) > train_df.get('PLUS_DI_14', s0), -1, 1)
            elif key == "OBV":
                signals_matrix[:, i] = np.where(train_df.get('OBV', s0).diff(5).fillna(0) > 0, 1, -1)
            elif key == "MFI":
                signals_matrix[:, i] = np.where(train_df.get('MFI_14', s0) < 20, 1, 
                                                np.where(train_df.get('MFI_14', s0) > 80, -1, 0))
            elif key == "1H_Vol_Spike":
                signals_matrix[:, i] = np.where((train_df.get('Volume', s0) > train_df.get('Volume_SMA_20', s0) * 1.5) & 
                                                (train_df.get('Close', s0) > train_df.get('Open', s0)), 1, 0)
            elif key == "Relative_Strength":
                signals_matrix[:, i] = np.where(train_df.get('RS_60D', s0) > 0.10, 1,
                                                np.where(train_df.get('RS_60D', s0) < -0.10, -1, 0))
            elif key == "4H_S_R":
                signals_matrix[:, i] = np.where(abs(train_df.get('Close', s0) - train_df.get('Low_120_4H', s0)) / 
                                                train_df.get('Close', s0).replace(0, np.nan) < 0.015, 1, 0)
            elif key == "BB_Squeeze":
                signals_matrix[:, i] = np.where(train_df.get('BB_Squeeze', s0) < train_df.get('BB_Squeeze', s0).rolling(50).mean(), 1, -1)
            elif key == "BB_Position":
                signals_matrix[:, i] = np.where(train_df.get('BB_Position', s0) < 0.2, 1, 
                                                np.where(train_df.get('BB_Position', s0) > 0.8, -1, 0))
            elif key == "1H_S_R":
                signals_matrix[:, i] = np.where(abs(train_df.get('Close', s0) - train_df.get('Low_120', s0)) / 
                                                train_df.get('Close', s0).replace(0, np.nan) < 0.015, 1, 0)
            elif key == "RSI":
                signals_matrix[:, i] = np.where(train_df.get('RSI_14', s0) < 30, 1, 
                                                np.where(train_df.get('RSI_14', s0) > 70, -1, 0))
            elif key == "CCI":
                signals_matrix[:, i] = np.where(train_df.get('CCI_20', s0) < -100, 1, 
                                                np.where(train_df.get('CCI_20', s0) > 100, -1, 0))
            elif key == "Stoch_K":
                signals_matrix[:, i] = np.where((train_df.get('STOCH_%K', s0).shift(1) <= 20) & 
                                                (train_df.get('STOCH_%K', s0) > train_df.get('STOCH_%K', s0).shift(1)), 1, 0)
                
        obv_s = train_df.get('OBV', pd.Series(1, index=train_df.index))
        penalty_mask = (obv_s.diff(5).fillna(0) < 0).values
        
        raw_scale = np.array(list(self.dinamik_agirliklar.values()) + [entry_threshold], dtype=float)
        scale = np.where(raw_scale == 0, 1.0, raw_scale)

        def objective(weights_normalized):
            weights = weights_normalized * scale
            z_raw = np.dot(signals_matrix, weights[:-1])
            z_final = np.where(penalty_mask, z_raw * 0.15, z_raw)
            trade_signals = np.where(z_final >= weights[-1], 1, 0)
            strategy_returns = trade_signals * forward_returns
            mean_ret, std_ret = np.mean(strategy_returns), np.std(strategy_returns)
            if std_ret == 0: return 1.0
            return -(mean_ret / std_ret) * np.sqrt(252 * 24)
            
        MAX_THRESHOLD = 580.0
        bounds = [(0.0, 200.0 / s) for s in scale[:-1]] + [(200.0 / scale[-1], MAX_THRESHOLD / scale[-1])]
        initial_normalized = np.ones(len(scale), dtype=float)
        initial_obj = objective(initial_normalized)
        result = minimize(objective, x0=initial_normalized, method='SLSQP', bounds=bounds, options={'disp': False, 'maxiter': 500, 'ftol': 1e-7})
        if result.success or result.fun < initial_obj:
            final_weights = result.x * scale
            optimized_weights = final_weights[:-1]
            self.entry_threshold = float(np.clip(final_weights[-1], 300.0, MAX_THRESHOLD))
            return dict(zip(self.wfa_keys, np.round(optimized_weights, 1)))
        return self.dinamik_agirliklar

    def run_backtest(self):
        self.equity_curve = []
        self.trade_log = []
        in_position = False
        entry_price = stop_loss = lot_miktari = entry_atr = 0
        tp1_price = 0
        tp1_hit = False
        entry_time = None
        current_trade_pnl = 0.0
        
        current_kasa = 10000.0
        peak_kasa = 10000.0
        lock_until = None
        
        if self.verbose:
            print("STATIC WEIGHTS MODE ACTIVE") # Konsol bildirimi eklendi
        
        ema50_4h_diff_positive = (self.df['EMA_50_4H'].diff() > 0).astype(int)
        ema50_4h_trend_20 = ema50_4h_diff_positive.rolling(20, min_periods=10).sum()

        for i in range(1, len(self.df)):
            row = self.df.iloc[i]
            current_price = row['Close']
            current_time = row.name
                
            if lock_until is not None:
                if current_time < lock_until:
                    self.equity_curve.append(current_kasa)
                    continue
                else:
                    lock_until = None
                    peak_kasa = current_kasa 
            
            if current_kasa > peak_kasa and not in_position:
                peak_kasa = current_kasa
            
            if in_position:
                unrealized_pnl = (current_price - entry_price) * lot_miktari
                anlik_kasa = current_kasa + unrealized_pnl
                current_dd = (anlik_kasa - peak_kasa) / peak_kasa
                
                # 1. Kill-Switch (DD) Çıkışı
                if current_dd <= -0.06:
                    cikis_fiyat_dd = current_price * (1 - self.slippage_oran)
                    pnl_dd = (cikis_fiyat_dd - entry_price) * lot_miktari
                    komisyon_dd = cikis_fiyat_dd * lot_miktari * self.komisyon_oran
                    net_pnl_dd = pnl_dd - komisyon_dd
                    current_kasa += net_pnl_dd
                    current_trade_pnl += net_pnl_dd
                    self.trade_log.append(current_trade_pnl)
                    current_trade_pnl = 0.0
                    
                    in_position = False
                    lock_until = current_time + pd.Timedelta(hours=48)
                    self.equity_curve.append(current_kasa)
                    continue

                # 2. 7 Günlük Zaman Stopu Çıkışı
                if (current_time - entry_time).days >= 7:
                    cikis_fiyat_7d = current_price * (1 - self.slippage_oran)
                    pnl_7d = (cikis_fiyat_7d - entry_price) * lot_miktari
                    komisyon_7d = cikis_fiyat_7d * lot_miktari * self.komisyon_oran
                    net_pnl_7d = pnl_7d - komisyon_7d
                    current_kasa += net_pnl_7d
                    current_trade_pnl += net_pnl_7d
                    self.trade_log.append(current_trade_pnl)
                    current_trade_pnl = 0.0
                    
                    in_position = False
                    self.equity_curve.append(current_kasa)
                    continue

                # 3. Kademeli Kâr (TP1) Çıkışı
                if not tp1_hit and current_price >= tp1_price:
                    satilacak_lot = lot_miktari / 2
                    cikis_fiyat_tp1 = tp1_price * (1 - self.slippage_oran)
                    pnl_tp1 = (cikis_fiyat_tp1 - entry_price) * satilacak_lot
                    komisyon_tp1 = cikis_fiyat_tp1 * satilacak_lot * self.komisyon_oran
                    net_pnl_tp1 = pnl_tp1 - komisyon_tp1
                    current_kasa += net_pnl_tp1
                    current_trade_pnl += net_pnl_tp1
                    
                    lot_miktari = satilacak_lot 
                    tp1_hit = True
                    # Gürültüden (0.2) kurtarıldı. Minimum 0.5 ATR nefes payı:
                    stop_loss = entry_price + (entry_atr * 0.5)

                if tp1_hit and current_price >= tp1_price:
                    yeni_stop = current_price - (entry_atr * 2.0)
                    if yeni_stop > stop_loss: 
                        stop_loss = yeni_stop

                # 4. Normal Stop Loss & Trailing Stop Çıkışı
                if current_price <= stop_loss:
                    cikis_fiyat_sl = stop_loss * (1 - self.slippage_oran)
                    pnl_sl = (cikis_fiyat_sl - entry_price) * lot_miktari
                    komisyon_sl = cikis_fiyat_sl * lot_miktari * self.komisyon_oran
                    net_pnl_sl = pnl_sl - komisyon_sl
                    current_kasa += net_pnl_sl
                    current_trade_pnl += net_pnl_sl
                    self.trade_log.append(current_trade_pnl)
                    current_trade_pnl = 0.0
                    
                    in_position = False
                    
                self.equity_curve.append(current_kasa)
                continue
            
            active_features = [col for col in self.dinamik_agirliklar.keys() if col not in self.disabled_features]
            z_raw = sum(self.dinamik_agirliklar.get(col, 0) * (1 if row.get(col, 0) > 0 else -1 if row.get(col, 0) < 0 else 0) for col in active_features)
            penalty_mask = (row.get('OBV', 1) < 0) or (row.get('CHOCH_4H', 1) < 0)
            z_final = z_raw * 0.15 if penalty_mask else z_raw
            
            ema50_4h = row.get('EMA_50_4H', np.nan)
            ema200_4h = row.get('EMA_200_4H', np.nan)
            ema50_4h_egim_pozitif = ema50_4h_trend_20.iloc[i] >= 14
            makro_trend_olumlu = (
                pd.notna(ema50_4h) and
                pd.notna(ema200_4h) and
                current_price > ema200_4h and
                ema50_4h > ema200_4h and
                ema50_4h_egim_pozitif
            )
            
            if z_final >= self.entry_threshold and not in_position and lock_until is None and makro_trend_olumlu:
                in_position = True
                tp1_hit = False
                current_trade_pnl = 0.0
                entry_time = current_time
                entry_price = current_price
                entry_atr = row['ATRr_14'] if pd.notna(row['ATRr_14']) else entry_price * 0.015
                
                stop_carpan = 2.5 
                hedef_carpan = 2.5
                
                sl_atr = entry_price - (entry_atr * stop_carpan)
                fvg_bot = row.get('Active_Bull_FVG_Bot_4H', np.nan)
                stop_loss = fvg_bot if pd.notna(fvg_bot) and sl_atr < fvg_bot < entry_price else sl_atr
                
                if (entry_price - stop_loss) < (entry_atr * 1.5):
                    stop_loss = entry_price - (entry_atr * 1.5)
                    
                tp1_price = entry_price + (entry_atr * hedef_carpan) 
                
                risk_sermayesi = current_kasa * 0.02
                h_risk = abs(entry_price - stop_loss)
                lot_miktari = risk_sermayesi / h_risk if h_risk > 0 else 0
                
                gercek_giris = entry_price * (1 + self.slippage_oran)
                entry_price = gercek_giris 
                
                giris_komisyon = entry_price * lot_miktari * self.komisyon_oran
                current_kasa -= giris_komisyon
                current_trade_pnl -= giris_komisyon
                
            self.equity_curve.append(current_kasa)

        return self.get_metrics()

    def get_metrics(self):
        if not self.equity_curve:
            return {}
        
        # Sharpe ve CAGR için Equity Curve'ü zaman endeksiyle (DatetimeIndex) eşleştiriyoruz
        eq = pd.Series(self.equity_curve, index=self.df.index[1:])
        
        total_ret = ((eq.iloc[-1] - eq.iloc[0]) / eq.iloc[0]) * 100
        max_dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
        
        trades = pd.Series(self.trade_log) if self.trade_log else pd.Series([0])
        kazanan = trades[trades > 0]
        kaybeden = trades[trades < 0]
        
        win_rate = len(kazanan) / len(trades) * 100 if len(trades) > 0 else 0
        avg_win = kazanan.mean() if len(kazanan) > 0 else 0
        avg_loss = kaybeden.mean() if len(kaybeden) > 0 else 0
        profit_factor = (kazanan.sum() / abs(kaybeden.sum())) if abs(kaybeden.sum()) > 0 else float('inf')
        
        calmar = (total_ret / abs(max_dd)) if max_dd != 0 else 0
        
        # --- EKSİK OLAN YENİ METRİKLERİN HESAPLAMALARI ---
        
        # 1. CAGR (Bileşik Yıllık Büyüme Oranı)
        days = (eq.index[-1] - eq.index[0]).days
        years = days / 365.25 if days > 0 else 1
        cagr = ((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) * 100
        
        # 2. Sharpe Ratio (Günlük getiriler üzerinden yıllıklandırılmış)
        daily_returns = eq.resample('1D').last().pct_change().dropna()
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() != 0 else 0

        # 3. Sortino Ratio (Sadece negatif günlük getirilerin sapmasıyla yıllıklandırılmış)
        downside_returns = daily_returns[daily_returns < 0]
        sortino = (daily_returns.mean() / downside_returns.std()) * np.sqrt(252) if len(downside_returns) > 1 and downside_returns.std() != 0 else 0
        
        # 4. MAR Ratio (CAGR / Max Drawdown Mutlak Değeri)
        mar = cagr / abs(max_dd) if max_dd != 0 else 0
        
        # 5. Expectancy (Matematiksel Beklenti - Her işlem başına beklenen ortalama kâr/zarar)
        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * abs(avg_loss))
        
        return {
            "net_getiri": round(total_ret, 2),
            "max_drawdown": round(max_dd, 2),
            "toplam_islem": len(trades),
            "win_rate": round(win_rate, 1),
            "ort_kazanc": round(avg_win, 4),
            "ort_kayip": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 2),
            "calmar_orani": round(calmar, 2),
            "cagr": round(cagr, 2),             # EKSİKLİK GİDERİLDİ
            "sharpe_ratio": round(sharpe, 2),   # EKSİKLİK GİDERİLDİ
            "sortino_ratio": round(sortino, 2),
            "mar_ratio": round(mar, 2),         # EKSİKLİK GİDERİLDİ
            "expectancy": round(expectancy, 2)  # EKSİKLİK GİDERİLDİ
        }

def _metric_delta(base_value, test_value):
    if np.isinf(base_value) and np.isinf(test_value):
        return 0.0
    if np.isinf(base_value) or np.isinf(test_value):
        return np.nan
    return base_value - test_value

def analyze_feature_importance(df_mega):
    baseline_metrics = ScannerBacktester(df_mega).run_backtest()
    rows = []

    for feature in QUANT_WEIGHTS.keys():
        disabled_metrics = ScannerBacktester(df_mega, disabled_features={feature}).run_backtest()
        net_delta = _metric_delta(baseline_metrics["net_getiri"], disabled_metrics["net_getiri"])
        sharpe_delta = _metric_delta(baseline_metrics["sharpe_ratio"], disabled_metrics["sharpe_ratio"])
        pf_delta = _metric_delta(baseline_metrics["profit_factor"], disabled_metrics["profit_factor"])

        rows.append({
            "Feature": feature,
            "Performans Katkısı": "Pozitif" if net_delta > 0 else "Negatif" if net_delta < 0 else "Notr",
            "Net Getiri Katkısı": round(net_delta, 2) if pd.notna(net_delta) else np.nan,
            "Sharpe Katkısı": round(sharpe_delta, 2) if pd.notna(sharpe_delta) else np.nan,
            "Profit Factor Katkısı": round(pf_delta, 2) if pd.notna(pf_delta) else np.nan,
            "Baseline Net Getiri": baseline_metrics["net_getiri"],
            "Feature Kapalı Net Getiri": disabled_metrics["net_getiri"],
            "Baseline Sharpe": baseline_metrics["sharpe_ratio"],
            "Feature Kapalı Sharpe": disabled_metrics["sharpe_ratio"],
            "Baseline Profit Factor": baseline_metrics["profit_factor"],
            "Feature Kapalı Profit Factor": disabled_metrics["profit_factor"]
        })

    importance_df = pd.DataFrame(rows)
    return importance_df.sort_values(
        by=["Net Getiri Katkısı", "Sharpe Katkısı", "Profit Factor Katkısı"],
        ascending=False,
        na_position="last"
    ).reset_index(drop=True)

def recommend_simplified_model(importance_df, target_features=10, low_feature_count=5):
    contribution_cols = ["Net Getiri Katkısı", "Sharpe Katkısı", "Profit Factor Katkısı"]
    scored_df = importance_df.copy()
    for col in contribution_cols:
        scored_df[col] = pd.to_numeric(scored_df[col], errors="coerce").fillna(0)

    scored_df["Katkı Skoru"] = (
        scored_df["Net Getiri Katkısı"].rank(pct=True) +
        scored_df["Sharpe Katkısı"].rank(pct=True) +
        scored_df["Profit Factor Katkısı"].rank(pct=True)
    ) / len(contribution_cols)

    low_features = scored_df.sort_values(
        by=["Katkı Skoru", "Net Getiri Katkısı", "Sharpe Katkısı", "Profit Factor Katkısı"],
        ascending=True
    ).head(low_feature_count).copy()

    low_features["Kaldırma Etkisi"] = np.where(
        low_features["Net Getiri Katkısı"] < 0,
        "Kaldırılırsa performans artabilir",
        np.where(
            low_features["Net Getiri Katkısı"] > 0,
            "Kaldırılırsa performans düşebilir",
            "Kaldırma etkisi nötr"
        )
    )

    keep_features = scored_df.sort_values(
        by=["Katkı Skoru", "Net Getiri Katkısı", "Sharpe Katkısı", "Profit Factor Katkısı"],
        ascending=False
    ).head(target_features)["Feature"].tolist()

    simplified_weights = {
        feature: QUANT_WEIGHTS[feature]
        for feature in keep_features
        if feature in QUANT_WEIGHTS
    }

    return {
        "low_features": low_features[[
            "Feature",
            "Kaldırma Etkisi",
            "Net Getiri Katkısı",
            "Sharpe Katkısı",
            "Profit Factor Katkısı",
            "Katkı Skoru"
        ]].reset_index(drop=True),
        "keep_features": keep_features,
        "simplified_weights": simplified_weights
    }

def run_walk_forward_analysis(df_mega, train_years=3, test_years=1, start_year=None, end_year=None):
    if df_mega.empty:
        return pd.DataFrame(), {}

    first_year = int(df_mega.index.min().year)
    last_year = int(df_mega.index.max().year)
    start_year = start_year or first_year
    end_year = end_year or last_year

    rows = []
    max_train_start = end_year - train_years - test_years + 1

    for train_start in range(start_year, max_train_start + 1):
        train_end = train_start + train_years - 1
        test_start = train_end + 1
        test_end = test_start + test_years - 1

        train_df = df_mega[(df_mega.index.year >= train_start) & (df_mega.index.year <= train_end)]
        test_df = df_mega[(df_mega.index.year >= test_start) & (df_mega.index.year <= test_end)]

        if len(train_df) < 500 or len(test_df) < 100:
            continue

        trainer = ScannerBacktester(train_df, verbose=False)
        trained_weights = trainer._calibrate_wfa(train_df)
        tester = ScannerBacktester(test_df, weights_override=trained_weights, verbose=False)
        metrics = tester.run_backtest()

        if not metrics:
            continue

        rows.append({
            "Train Dönemi": f"{train_start}-{train_end}",
            "Test Yılı": test_start if test_years == 1 else f"{test_start}-{test_end}",
            "CAGR (%)": metrics["cagr"],
            "Sharpe": metrics["sharpe_ratio"],
            "Max Drawdown (%)": metrics["max_drawdown"],
            "Profit Factor": metrics["profit_factor"],
            "Win Rate (%)": metrics["win_rate"],
            "İşlem Sayısı": metrics["toplam_islem"]
        })

    report_df = pd.DataFrame(rows)
    if report_df.empty:
        return report_df, {}

    average_results = {
        "Ortalama CAGR (%)": round(report_df["CAGR (%)"].mean(), 2),
        "Ortalama Sharpe": round(report_df["Sharpe"].mean(), 2),
        "Ortalama Max Drawdown (%)": round(report_df["Max Drawdown (%)"].mean(), 2),
        "Ortalama Profit Factor": round(report_df["Profit Factor"].replace([np.inf, -np.inf], np.nan).mean(), 2),
        "Ortalama Win Rate (%)": round(report_df["Win Rate (%)"].mean(), 1),
        "En Kötü Yıl": report_df.loc[report_df["CAGR (%)"].idxmin(), "Test Yılı"],
        "En Kötü Yıl CAGR (%)": report_df["CAGR (%)"].min(),
        "En İyi Yıl": report_df.loc[report_df["CAGR (%)"].idxmax(), "Test Yılı"],
        "En İyi Yıl CAGR (%)": report_df["CAGR (%)"].max()
    }

    return report_df, average_results

def print_walk_forward_report(report_df, average_results):
    if report_df.empty:
        print("Walk-forward raporu üretilemedi: yeterli train/test verisi yok.")
        return

    print("\nWALK-FORWARD TEST RAPORU")
    print(report_df.to_string(index=False))
    print("\nÖZET")
    for key, value in average_results.items():
        print(f"{key}: {value}")

# ==========================================
# 4. EVREN TARAMA MOTORU (UNIVERSE SCANNER)
# ==========================================
def run_universe_scanner(data_folder="hisseler"):
    print(f"🔍 '{data_folder}' klasöründeki hisseler taranıyor...\n")
    
    csv_files = glob.glob(f"{data_folder}/*.csv")
    
    if not csv_files:
        print(f"❌ '{data_folder}' klasöründe hiç CSV dosyası bulunamadı!")
        return

    results = []
    toplam_hisse = len(csv_files)
    spy_file = next((f for f in csv_files if os.path.basename(f).replace(".csv", "").upper() == "SPY"), None)
    spy_benchmark_df = None
    if spy_file:
        spy_benchmark_df = build_mega_dataframe(spy_file)
    else:
        print("SPY benchmark dosyasi bulunamadi; Relative Strength sinyali notr kalacak.")
    
    for idx, file in enumerate(csv_files, 1):
        ticker = os.path.basename(file).replace(".csv", "").upper()
        print(f"[{idx}/{toplam_hisse}] ⏳ {ticker} analiz ediliyor... (WFA Öğrenme devrede)")
        
        try:
            df_mega = build_mega_dataframe(file, spy_benchmark_df)

            train_df = df_mega[df_mega.index.year >= (df_mega.index.year.max() - 2)]
            if len(train_df) >= 500:
                calibrator = ScannerBacktester(train_df, verbose=False)
                trained_weights = calibrator._calibrate_wfa(train_df)
                trained_entry_threshold = calibrator.entry_threshold
                trained_exit_threshold = calibrator.exit_threshold
                z_values = df_mega['Z_FINAL'].dropna()
                print(
                    f"   [WFA] entry_threshold={trained_entry_threshold:.0f} | "
                    f"Z_FINAL p75={z_values.quantile(0.75):.0f} p90={z_values.quantile(0.90):.0f} max={z_values.max():.0f}"
                )
            else:
                trained_weights = None
                trained_entry_threshold = 400.0
                trained_exit_threshold = 550.0
                print("   [WFA] yetersiz train verisi; sabit ağırlık kullanılıyor.")

            tester = ScannerBacktester(df_mega, verbose=False, weights_override=trained_weights)
            tester.entry_threshold = trained_entry_threshold
            tester.exit_threshold = trained_exit_threshold
            
            metriks = tester.run_backtest()
            
            if not metriks:
                continue

            results.append({
                "Hisse / Varlık": ticker,
                "Net Getiri (%)": metriks["net_getiri"],
                "Max Drawdown (%)": metriks["max_drawdown"],
                "CAGR (%)": metriks.get("cagr", 0),
                "Sharpe Ratio": metriks.get("sharpe_ratio", 0),
                "Sortino Ratio": metriks.get("sortino_ratio", 0),
                "MAR Ratio": metriks.get("mar_ratio", 0),
                "İşlem Sayısı": metriks["toplam_islem"],
                "Win Rate (%)": metriks["win_rate"],
                "Expectancy": metriks.get("expectancy", 0),
                "Profit Factor": metriks["profit_factor"],
                "Calmar Oranı": metriks["calmar_orani"],
                "Kantitatif Skor": round(metriks["net_getiri"] / max(abs(metriks["max_drawdown"]), 1), 2)
            })
            
            # 🚨 YENİ: Profesyonel Konsol Çıktısı
            print(f"   ✅ {ticker} → Getiri: %{metriks['net_getiri']:.1f} | "
                  f"DD: %{metriks['max_drawdown']:.1f} | "
                  f"CAGR: %{metriks.get('cagr', 0):.1f} | "
                  f"Sharpe: {metriks.get('sharpe_ratio', 0):.2f} | "
                  f"MAR: {metriks.get('mar_ratio', 0):.2f} | "
                  f"Exp: {metriks.get('expectancy', 0):.2f} | "
                  f"WR: %{metriks['win_rate']:.0f} | "
                  f"PF: {metriks['profit_factor']:.2f} | "
                  f"İşlem: {metriks['toplam_islem']}")
            
        except Exception as e:
            print(f"   ❌ {ticker} dosyasında hata oluştu: {e}")

    if results:
        rapor_df = pd.DataFrame(results)
        rapor_df = rapor_df.sort_values(by="Kantitatif Skor", ascending=False).reset_index(drop=True)
        
        rapor_df.to_csv("portfoy_raporu.csv", index=False)
        print("\n" + "="*70)
        print("🏆 TARAMA TAMAMLANDI! (portfoy_raporu.csv oluşturuldu)")
        print("="*70)
        print("İşte Kantitatif Skoru En Yüksek İlk 5 Hisse (Altın Liste):")
        print(rapor_df.head(5).to_string())
        print("="*70)

if __name__ == "__main__":
    run_universe_scanner("hisseler")
