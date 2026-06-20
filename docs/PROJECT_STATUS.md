# Project Status

## 1. Kısa Özet

Bu proje şu anda US equities swing trading için araştırma ve backtest altyapısıdır. Canlı trading botu değildir; live order routing, broker entegrasyonu, production risk kontrolü ve gerçek zamanlı izleme katmanı bu dosyada tamamlanmış kabul edilmez.

Odak alanı momentum, relative strength, trend-following ve bazı mean reversion fikirlerini aynı veri ve benchmark çerçevesinde test edebilmektir. Mevcut durum umut veren bir research framework aşamasıdır; sonuçlar veri kalitesi, survivorship bias, out-of-sample test ve realistic execution kontrolleri tamamlanmadan karar verici kabul edilmemelidir.

Not: kökte `reports/latest` dizini bu checkout'ta bulunmadı. Güncel aday metrikleri `testdata/testdata/reports/latest/strategy_compact_report.md` ve benchmark özeti `testdata/testdata/reports/latest/benchmark_audit_report.md` içinden alındı. `testdata/reports/latest/data_audit_report.md` ayrıca okundu; performance profile report bulunmadı.

## 2. Mevcut Mimari

- `universe_scanner.py` ana giriş noktası ve uyumluluk yüzeyidir.
- Yeni strateji mantığı `strategies/` altında ayrı modüllerde tutulmalıdır.
- `reports/latest` / `reports/runs` yapısı son run ve tarihli run çıktılarını ayırmak için kullanılır; bu checkout'ta raporlar `testdata/reports/...` ve `testdata/testdata/reports/...` altında görülüyor.
- Prepared feature cache, aynı veri ve config ile feature üretimini tekrar kullanmak için vardır.
- Data loader, daily ve intraday bar kaynaklarını okuyup backtest'e uygun OHLCV/feature seti hazırlar.
- Benchmark audit sistemi QQQ ve EqualWeight Universe benchmark'larının aynı tarih aralığı ve dışlanan sembollerle hesaplanmasını kontrol eder.
- Legacy dosya varsa artık büyütülmemeli; yeni logic legacy içine gömülmemeli, sadece minimum routing/config kalmalıdır.

## 3. Veri Yapısı

- `testdata/hisseler/daily`: günlük OHLCV verisi.
- `testdata/hisseler/raw_1min`: 130 dakikalık bar üretmek için ham 1 dakika kaynak.
- `testdata/hisseler/bars_130min`: intraday swing execution/signal için kullanılacak 130 dakika barlar.
- `testdata/hisseler/manifest`: veri üretimi ve kaynak izleme metadata alanı.

Daily data momentum, relative strength, benchmark ve market regime hesapları için kullanılmalıdır. 130min data intraday swing execution ve sinyal zamanlaması içindir. `raw_1min` sadece 130min üretim kaynağıdır; default backtest loader tarafından doğrudan okunmamalıdır.

`testdata/reports/latest/data_audit_report.md` içindeki son audit 4 sembol, 4 daily dosya, 4 adet 130min dosya ve 4 geçerli 130min schedule row raporladı; bu geniş evren doğrulaması değildir.

## 4. Test Edilmiş Stratejiler

- `relative_strength_rotation`: şu an en umut veren aile; en iyi sonuçlar bu tarafta görünüyor.
- `dual_momentum`: yedek momentum adayı; RS ailesine yakın ama mevcut raporda daha zayıf.
- `cross_sectional_momentum`: momentum araştırma ailesinin parçası; benchmark ve robustness ile ayrıca izlenmeli.
- `donchian_breakout`: çalışıyor ama performans zayıf.
- `ma_slope_trend`: execution bug'ı düzeltildi, fakat son raporda performans zayıf.
- `current_pullback_mean_reversion` / legacy strategy: geniş evrende zayıf; yeni geliştirme yönü olarak öncelikli görünmüyor.

## 5. Güncel En İyi Adaylar

Kaynak: `testdata/testdata/reports/latest/strategy_compact_report.md`, run zamanı `2026-06-19 16:05:20`.

| Experiment | Net Getiri (%) | Max Drawdown (%) | Profit Factor | Trade Count | QQQ excess (%) | EqualWeight karşısındaki durum |
|---|---:|---:|---:|---:|---:|---|
| `rs_m63_sma100` | 26.13 | -11.06 | 2.13 | 140 | -9.47 | EqualWeight Universe Return 58.13; altında |
| `rs_m63_no_regime` | 46.80 | -17.52 | 2.10 | 228 | 11.20 | EqualWeight Universe Return 58.13; altında |
| `dm_m63_top20` | 20.20 | -12.92 | 1.82 | 141 | -15.40 | EqualWeight Universe Return 58.13; altında |

Bu tabloda QQQ BuyHold Return raporda 35.6 olarak geçiyor. EqualWeight farkı raporda ayrı excess kolonu olarak verilmediği için sadece rapordaki EqualWeight Universe Return değeriyle karşılaştırma yönü yazıldı.

## 6. Benchmark Durumu

Kaynak: `testdata/testdata/reports/latest/benchmark_audit_report.md`.

- Benchmark sembolü: QQQ.
- EqualWeight Universe benchmark immutable base/prepared OHLCV close data kullanıyor.
- Tüm experiment'lerde aynı date range kullanılıyor: True.
- QQQ BuyHold Return unique values: `[35.6]`.
- EqualWeight Universe Return unique values: `[58.13]`.
- EqualWeight benchmark için eksik semboller hâlâ `PARA,WBD`.
- Universe count used: 99.
- QQQ ve SPY trade universe dışında uygulanmış görünüyor: `excluded_symbols_applied` değeri `QQQ,SPY`.
- Benchmark deterministic kabulü için audit çıktısı tutarlı görünüyor; yine de veri kaynağı değiştiğinde yeniden audit gerekir.

## 7. Bilinen Riskler

- Survivorship bias hâlâ çözülmedi.
- Adjusted data kontrolü kritik; fiyat datası adjusted değilse uzun dönem sonuçlar sapabilir.
- Delisted/historical universe yoksa sonuçlar iyimser olabilir.
- EqualWeight benchmark'i geçmek zor; son adayların üçü de EqualWeight 58.13 altında.
- Parametre overfit riski var.
- Test dönemi sınırlı olabilir.
- İşlem maliyeti ve slippage canlı koşullarda sonucu bozabilir.
- Son data audit geniş evreni değil, yalnızca 4 sembollük seti doğruluyor.

## 8. Tamamlanan İşler

- Çoklu strateji altyapısı.
- Benchmark audit düzeltmesi.
- Report klasör yapısı.
- Prepared feature cache.
- Strategy compact report.
- Portfolio rejection diagnostics.
- 1m -> 130m data üretim planı.
- Data klasör ayrımı.

## 9. Sıradaki Plan

1. `data_audit_report` temizliği.
2. Yeni data ile baseline run.
3. RS robustness grid.
4. Yearly performance report.
5. Walk-forward / out-of-sample test.
6. Transaction cost + slippage.
7. Survivorship/adjusted data iyileştirmesi.
8. En iyi stratejilerden portfolio blend testi.

## 10. Codex İçin Çalışma Kuralları

- Yeni strateji eklemeden önce validation yap.
- Report metriklerini uydurma.
- Legacy dosyayı büyütme.
- `universe_scanner_legacy.py` içinden `import`/`open`/`read_text`/`re`/`exec`/`eval` ile logic çekme.
- Her değişiklikten sonra mevcut ana stratejilerin sonuçlarını karşılaştır.
- Büyük cevap verme; normal görevlerde maksimum 12 satır özet dön.
- Trade logic ve benchmark logic ayrı tutulmalı.

## Current honest status

Bu proje umut veren bir research framework aşamasındadır; henüz live trading için yeterli değildir. En güçlü yön momentum/relative strength tarafıdır. Asıl eksikler veri kalitesi, robustness, OOS ve realistic execution testleridir.
