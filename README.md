# FinansAnalizBot

[![tests](https://github.com/Gnesh97/FinansAnalizBot/actions/workflows/tests.yml/badge.svg)](https://github.com/Gnesh97/FinansAnalizBot/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

ABD hisse senetleri için **karar destek (decision-support)** amaçlı, sistematik swing-trading araştırma ve kâğıt üzerinde (paper) takip platformu. Her gece veriyi günceller, sinyal üretir, rapor hazırlar ve Telegram'dan bildirir.

> [!IMPORTANT]
> Bu proje **otomatik işlem botu değildir** ve canlı işlem yazılımı değildir. Kod tabanında broker'a emir gönderen hiçbir çağrı yoktur. Tüm emirleri operatör elle girer.

## Özellikler

- **Aktif strateji:** `rs_m126_top30_sma100_no_regime`
  - 126 günlük momentum skoru, 60 günlük volatiliteye bölünür.
  - SMA100 trend filtresi uygulanır.
  - İlk 30 hisse seçilir ve her ay yeniden dengelenir.
  - Emirler ertesi günün açılış fiyatından hesaplanır.
  - Strateji bilinçli olarak **dondurulmuştur**; bu projede parametre optimizasyonu yapılmaz.
- **Evren:** 177 sembollük sabit aday listesi (hash ile doğrulanır). QQQ ve SPY yalnızca benchmark olarak kullanılır, sıralamaya girmez.
- **Veri:** `rs_v2_tiingo` profili (Tiingo, yalnızca günlük, konsolide veri, ücretsiz katman için saatte ~48 istek ile hız sınırlı). Alpaca IEX/SIP verileri sadece arşiv olarak durur.
- **Gece zinciri (DSS):** Tiingo yenileme → katı doğrulama → sinyal üretimi → rapor → Telegram. Ek olarak tavsiye niteliğinde (Mode A) bir Risk Engine adımı çalışır.
- **Kâğıt işlem ve hazırlık araçları:** yürütme önizlemesi, no-lookahead denetimi, SQLite/CSV mutabakatı, Alpaca paper için yalnızca GET (salt okunur) mutabakat.
- **Araştırma alanı:** `research_sandbox/` altında survivorship bias, rotasyon duyarlılığı, trend gücü filtresi ve piyasa rejimi kapısı deneyleri.

## Güvenlik Varsayılanları

`broker_submit_enabled`, `live_trading_enabled` ve `short_trading_enabled` kodda `False` olarak sabittir ve her çalıştırmada sıfırlanır. Config veya ortam değişkeniyle geçersiz kılınamaz. Kalıcı broker defteri ve paper durumu, insan onayı olmadan değişmez. CSV yerel kaynak-gerçek (source of truth) olarak kalır, SQLite yalnızca ayna/denetim amaçlıdır.

## Proje Durumu

| Alan | Durum |
|---|---|
| Faz | Karar destek sistemi, gece zamanlayıcısı aktif (systemd user timer, 01:00 Europe/Istanbul) |
| Testler | 746 test, hepsi yeşil (`testdata/run_test_suite.py`) |
| CI | GitHub Actions, push ve PR'da çalışır |
| Veri kaynağı | Tiingo (canlı yol) |
| Son araştırma | Piyasa rejimi kapısı denendi, sonuç olumsuz (aşağıya bakın) |

### Bilinen Sınırlamalar

- **Survivorship bias çözülmedi.** Evren yalnızca güncel bileşenlerden oluşur. Delist olmuş veya birleşmiş şirketler geriye dönük eklenmemiştir. Alpaca üzerinden delist veri toplama denemesi, seyrek kapsama ve ticker yeniden kullanımı kaynaklı kimlik kirlenmesi nedeniyle bırakılmıştır. Kanıtlar `reports/audit/` altındadır.
- `PARA` (Paramount Global, 2025-08-07'de delist) belgelenmiş ve operatör onaylı bir `EARLY_TERMINAL` istisnasıdır, hata değildir.
- İki test, 358 dosyalık manifesti gerçek disk I/O ve SHA256 ile doğruladığı için ~4 dakika sürer. Bu bilinçli bir tercihtir.
- CI `windows-latest` üzerinde çalışır, gece zamanlayıcısı ise Linux (systemd) içindir.

### Araştırma Sonuçları (survivorship bias düzeltmeli evren)

| Deney | Sonuç |
|---|---|
| Baz çizgi | %65,86 getiri / 0,76 Sharpe / %20,52 maks. düşüş |
| Piyasa rejimi kapısı (SPY 200 günlük SMA altında nakit) | %19,10 / 0,37 / %19,12, belirgin biçimde kötü |
| Trend gücü filtresi | Net bir iyileşme yok |
| Daha uzun lookback | Tek olumlu (risk ayarlı) sinyal, yalnızca yönlendirici kanıt, öneri değil |

Ayrıntılar: `reports/audit/phase26_*`, `phase27_*`, `phase28_*`.

## Mimari

```
bot.py                  Ana giriş noktası (--mode dss-report vb.)
universe_scanner.py     Evren tarama, raporlar, mutabakat komutları
testdata/               Üretim mantığı, DSS zamanlayıcısı, baseline'lar, test paketi
research_sandbox/       Araştırma deneyleri (üretim koduna dokunmaz)
quantconnect/           Strateji portu / karşılaştırma
scripts/                Gece zinciri ve manuel BAT/PS1/SH scriptleri
reports/audit/          Eklemeli (append-only) faz denetim raporları
docs/                   Mimari inceleme ve komut belgeleri
```

Gece akışı: `scripts/run_dss_daily.sh` → `testdata/dss_scheduler.py` → `bot.py --mode dss-report`.

## Kurulum

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

`.env` içinde yalnızca yerel değerleri doldurun. Alpaca anahtarları paper ve salt okunur kontroller içindir. Telegram bilgileri isteğe bağlıdır. Tiingo erişimi de ayrıca yapılandırılmalıdır.

## Kullanım

```powershell
# Salt okunur raporlar ve denetimler
python universe_scanner.py --closed-trade-report
python universe_scanner.py --no-lookahead-audit
python universe_scanner.py --execution-cost-preview
python universe_scanner.py --sqlite-state-reconciliation
python universe_scanner.py --broker-reconciliation
python universe_scanner.py --broker-reconciliation --alpaca-paper-snapshot-readonly

# Gece zincirini elle çalıştırma
bash scripts/run_dss_daily.sh
```

Manuel Windows akışı için `scripts\run_pending_5min_refresh.bat` ve `scripts\run_daily_paper_pipeline.bat` kullanılabilir. Tüm komutlar `docs/COMMANDS.md` içindedir.

Varsayılan broker mutabakatı Alpaca'yı çağırmaz. Salt okunur bayrak yalnızca GET yapar. Kimlik bilgisi veya ağ yoksa hesabı boş saymaz, WARNING / NOT_AVAILABLE raporlar. Uyarılar operatör için bilgi mesajıdır, emir gönderme izni değildir.

## Raporlar

- Güncel çıktılar: `testdata/reports/latest`
- Zaman damgalı çalıştırmalar: `testdata/reports/runs`
- Ana özet: `testdata/reports/latest/chatgpt_master_report.md`
- Aksiyon özeti: `testdata/reports/latest/chatgpt_action_report.md`

## Testler

```powershell
python testdata\run_test_suite.py
```

Paket yereldir. Geçici dosyalar ve mock kullanır, Alpaca kimlik bilgisi gerektirmez, broker'a emir göndermez.

## Commit Edilmemesi Gerekenler

`.env` ve her türlü gizli bilgi, üretilen rapor çıktıları (bilinçli sürümlenmedikçe), yerel piyasa verisi, durum veritabanları, yedekler, scratch ve cache dosyaları, gerçek broker/hesap kimlikleri ve API anahtarları.

## Katkı ve Çalışma Kuralları

Kod asistanı ve operatör kuralları için [AGENTS.md](AGENTS.md) ve [CLAUDE.md](CLAUDE.md) dosyalarına bakın. Hızlı durum özeti için [PROJECT_STATUS.md](PROJECT_STATUS.md) dosyası kullanılabilir.

## Lisans

Bu proje [MIT Lisansı](LICENSE) altında lisanslanmıştır.

## Sorumluluk Reddi

Bu yazılım yalnızca araştırma ve eğitim amaçlıdır, yatırım tavsiyesi değildir. Geçmiş performans gelecek sonuçların garantisi değildir.
