import os
import pandas as pd
import yfinance as yf
from pathlib import Path

# Çıktıların düzgün görünmesi için terminal ayarları
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def main():
    print("📊 Karne hesaplanıyor, yfinance üzerinden güncel fiyatlar çekiliyor...\n")
    
    # CSV dosyasının yolu (bot.py ile aynı klasörde olduğu varsayılır)
    BASE_DIR = Path(__file__).resolve().parent
    csv_path = BASE_DIR / "trade_loglari.csv"

    if not csv_path.exists():
        print(f"❌ Hata: '{csv_path.name}' dosyası bulunamadı. Lütfen botun en az 1 kere çalışıp log üretmesini bekleyin.")
        return

    # Log dosyasını oku
    try:
        df = pd.read_csv(csv_path, encoding="utf-8")
    except Exception as e:
        print(f"❌ CSV okuma hatası: {e}")
        return

    # Sadece 'AL' veya 'GÜÇLÜ AL' sinyallerini filtrele
    hedef_aksiyonlar = ['AL', 'GÜÇLÜ AL']
    # Sütun adları boşluklu falan gelme ihtimaline karşı strip() yapıyoruz
    df.columns = df.columns.str.strip() 
    
    if 'Aksiyon' not in df.columns:
        print("❌ Hata: CSV dosyasında 'Aksiyon' sütunu bulunamadı.")
        return

    df_filtered = df[df['Aksiyon'].isin(hedef_aksiyonlar)].copy()

    if df_filtered.empty:
        print("⚠️ Henüz kayıtlarda 'AL' veya 'GÜÇLÜ AL' işlemi bulunmuyor.")
        return

    # Veri tiplerini temizle
    df_filtered['Giris'] = pd.to_numeric(df_filtered['Giris'], errors='coerce')
    df_filtered = df_filtered.dropna(subset=['Giris'])

    # Yfinance API'sini yormamak için listedeki tekil (unique) hisselerin güncel fiyatlarını bir kere çekiyoruz
    unique_tickers = df_filtered['Hisse'].unique()
    current_prices = {}

    for ticker in unique_tickers:
        try:
            stock = yf.Ticker(ticker)
            # Son 1 günlük kapanış verisini al
            hist = stock.history(period="1d")
            if not hist.empty:
                current_prices[ticker] = hist['Close'].iloc[-1]
            else:
                current_prices[ticker] = None
        except Exception as e:
            print(f"⚠️ {ticker} güncel fiyatı çekilemedi: {e}")
            current_prices[ticker] = None

    # Çekilen güncel fiyatları tabloya ekle
    df_filtered['Guncel_Fiyat'] = df_filtered['Hisse'].map(current_prices)
    
    # Güncel fiyatı çekilemeyen satırları atla
    df_filtered = df_filtered.dropna(subset=['Guncel_Fiyat'])

    if df_filtered.empty:
        print("❌ Hesaplama yapılabilecek geçerli hisse verisi kalmadı (API yanıt vermemiş olabilir).")
        return

    # ---------------- MATEMATİK VE İSTATİSTİK ----------------

    # Fark ve Yüzdelik Kâr/Zarar Hesaplaması
    df_filtered['Kâr/Zarar ($)'] = df_filtered['Guncel_Fiyat'] - df_filtered['Giris']
    df_filtered['Kâr/Zarar (%)'] = (df_filtered['Kâr/Zarar ($)'] / df_filtered['Giris']) * 100

    # Çıktı tablosunu şıklaştırma
    sonuc_df = df_filtered[['Tarih', 'Hisse', 'Aksiyon', 'Giris', 'Guncel_Fiyat', 'Kâr/Zarar ($)', 'Kâr/Zarar (%)']].copy()
    
    # Sayıları yuvarlama
    sonuc_df['Giris'] = sonuc_df['Giris'].round(2)
    sonuc_df['Guncel_Fiyat'] = sonuc_df['Guncel_Fiyat'].round(2)
    sonuc_df['Kâr/Zarar ($)'] = sonuc_df['Kâr/Zarar ($)'].round(2)
    sonuc_df['Kâr/Zarar (%)'] = sonuc_df['Kâr/Zarar (%)'].round(2)

    # Durum simgesi ekleme
    sonuc_df['Durum'] = sonuc_df['Kâr/Zarar (%)'].apply(
        lambda x: '🟢 KÂR' if x > 0 else ('🔴 ZARAR' if x < 0 else '⚪ NÖTR')
    )

    # Pandasta satır ve sütunların kesilmemesi için görüntüleme ayarları
    pd.set_option('display.max_rows', None)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 1000)

    # Tabloyu Ekrana Yazdır
    print("\n" + "="*85)
    print(" "*30 + "🤖 BOT KARNESİ")
    print("="*85)
    print(sonuc_df.to_string(index=False))
    print("="*85)

    # Genel Başarı İstatistikleri
    toplam_islem = len(sonuc_df)
    karda_olan_islemler = len(sonuc_df[sonuc_df['Kâr/Zarar (%)'] > 0])
    zararda_olan_islemler = len(sonuc_df[sonuc_df['Kâr/Zarar (%)'] < 0])
    notr_islemler = len(sonuc_df[sonuc_df['Kâr/Zarar (%)'] == 0])

    basari_orani = (karda_olan_islemler / toplam_islem) * 100 if toplam_islem > 0 else 0
    ortalama_getiri = sonuc_df['Kâr/Zarar (%)'].mean()

    print(f"\n📌 İSTATİSTİKLER:")
    print(f"Toplam 'AL' Sinyali: {toplam_islem}")
    print(f"Kârdaki İşlemler:    {karda_olan_islemler}")
    print(f"Zarardaki İşlemler:  {zararda_olan_islemler}")
    print(f"Nötr İşlemler:       {notr_islemler}")
    print("-" * 30)
    print(f"Ortalama Getiri:     %{ortalama_getiri:.2f}")
    
    # Başarı oranına göre renk/ikon
    if basari_orani >= 60:
        icon = "🏆"
    elif basari_orani >= 40:
        icon = "⚖️"
    else:
        icon = "🚨"

    print(f"Botun Genel Başarı Oranı: {icon} %{basari_orani:.2f}\n")

if __name__ == "__main__":
    main()