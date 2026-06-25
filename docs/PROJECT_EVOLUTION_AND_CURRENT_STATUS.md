# Project Evolution and Current Status

Hazirlanma tarihi: 2026-06-21

Bu dosya, projenin baslangictan bugune gecirdigi ana degisimleri ve su anki durumunu tek yerde anlatmak icin hazirlandi. Kaynak olarak mevcut repo dosyalari, `testdata/reports/latest` altindaki son raporlar, son `download_manifest.csv`, mevcut test sonuclari ve onceki calisma hafizasi kullanildi.

## 1. Executive Summary

Proje su anda canli trading bot degil. Ana islevi, Amerikan borsasi hisseleri icin swing trading stratejilerini test etmek, strateji adaylarini karsilastirmak, veri kalitesini denetlemek, paper-trading sinyali uretmek ve broker submit oncesi guvenlik kontrolleri yapmak.

Son durum:

- Backtest ve raporlama altyapisi calisiyor.
- Ana veri layout'u `daily`, `raw_5min`, `bars_130min` olarak oturdu.
- Son paper run basarili calisti ve `chatgpt_master_report.md`, `run_manifest.json`, `pipeline_health_log.csv`, `data_precheck_report.md`, `pre_submit_readiness_report.md` uretildi.
- Strateji metric degisim bayragi: `False`.
- Benchmark degisim bayragi: `False`.
- Pipeline status: `WARNING`.
- Pre-submit readiness: `BLOCK`.
- Canli emir veya paper broker submit aktif degil.
- Kalan bloklayici veri uyarilari: `early_daily_end`, `psa_zero_volume`.
- Paper tarafinda 5 adet accepted sinyal `PENDING_NEXT_OPEN` durumunda bekliyor.

Net yargi: proje research-grade backtest/paper-readiness yonunde ciddi ilerledi, fakat canli trading icin hazir degil. Kalan en onemli eksikler survivorship-bias-free universe, vendor veri anomalileri, gerçek broker submit entegrasyonu icin explicit onay/gate, fill dogrulamasi, daha genis OOS/walk-forward tekrar validasyonu ve data policy kararlaridir.

## 2. Projenin Amaci Nereye Evrildi?

Baslangictaki genel hedef, Amerikan borsasinda islem yapabilecek bir trading bot olusturmakti. Ancak pratikte proje once backtest ve strateji arastirma motoruna donustu. Bu dogru bir evrim oldu, cunku live trading'den once su temel sorularin cevaplanmasi gerekiyordu:

- Strateji mantigi lookahead yapmadan calisiyor mu?
- Benchmark manipule edilmeden hesaplanabiliyor mu?
- Veri layout'u tutarli mi?
- 130 dakikalik barlar gercek RTH bloklarina uyuyor mu?
- Sonuclar QQQ ve equal-weight universe karsisinda anlamli mi?
- Paper sinyalleri gercek emir gibi izlenebiliyor mu?
- Broker submit acilmadan once guvenlik kapilari var mi?

Su anki proje, bu sorulara cevap ureten bir backtest/report/paper-readiness sistemidir.

## 3. Ana Mimari

### 3.1 Entry Point

Ana CLI:

```text
testdata/universe_scanner.py
```

Bu dosya argparse ile komutlari alir ve runner katmanina yonlendirir. Kritik komut aileleri:

- Basic backtest
- Full diagnostic
- Paper report
- Execution preview
- Pre-submit check
- Alpaca paper check
- Robustness research

### 3.2 Orchestration

Ana orkestrasyon:

```text
testdata/runner.py
```

Runner su sorumluluklari tasiyor:

- Veri klasorlerini cozer.
- Prepared feature cache kullanir.
- Data audit uretir.
- Benchmark audit uretir.
- Strategy experiment batch calistirir.
- Compact report ve performance profile yazar.
- OOS, attribution, cost-stress, liquidity guard raporlarini opsiyonel yazar.
- Paper signal report uretir.
- Pending order state ve paper trade log dosyalarini yonetir.
- Operational health, schema validation, regression guard ve master report uretir.
- Run manifest yazar.

Bu dosya halen cok buyuk sorumluluk tasiyor. Kisa vadede kabul edilebilir, ama orta vadede report writers, paper state, manifest ve orchestration parcalarina ayrilmasi faydali olur.

### 3.3 Reporting

Ana rapor dosyalari:

```text
testdata/reports/latest/
testdata/reports/runs/<timestamp>/
testdata/reports/history/
```

Canonical latest raporlar:

- `chatgpt_master_report.md`
- `strategy_compact_report.csv`
- `strategy_compact_report.md`
- `data_audit_report.md`
- `benchmark_audit_report.md`
- `performance_profile_report.md`
- `paper_signal_report.md`
- `paper_pending_orders.csv`
- `paper_order_review.csv`
- `paper_trade_log.csv`
- `daily_preflight_report.md`
- `data_precheck_report.md`
- `pipeline_health_log.csv`
- `regression_guard_report.md`
- `report_schema_validation_report.md`
- `paper_state_validation_report.md`
- `execution_preview_report.md`
- `order_risk_check_report.md`
- `pre_submit_readiness_report.md`
- `short_readiness_report.md`
- `run_manifest.json`

Yanlis duplicate path olan `testdata/testdata/reports` kullanilmamasi icin path guard ve dokumantasyon eklendi.

### 3.4 Data Layout

Guncel veri layout'u:

```text
testdata/hisseler/daily/<SYMBOL>_1D.csv
testdata/hisseler/raw_5min/<SYMBOL>_5min.csv
testdata/hisseler/bars_130min/<SYMBOL>_130min.csv
testdata/hisseler/manifest/download_manifest.csv
```

Onemli karar: backtest normal akista `raw_1min` kullanmiyor. Current default, `daily + raw_5min + bars_130min` akisi.

130 dakika bar bloklari:

- 09:30
- 11:40
- 13:50

Bu bloklar RTH swing workflow icin sabitlendi.

### 3.5 Strategy Modules

Aktif strateji aileleri:

```text
testdata/strategies/relative_strength_rotation.py
testdata/strategies/donchian_breakout.py
testdata/strategies/ma_slope_trend.py
testdata/strategies/dual_momentum.py
```

Karsilastirilan aileler:

- Relative strength / momentum
- Donchian breakout
- MA slope trend
- Dual momentum

Yeni strateji ekleme su asamada yapilmadi. Mevcut strateji mantigi sessiz sekilde degistirilmedi.

## 4. Baslangictan Bugune Ana Degisimler

### 4.1 Legacy ve Modulerlesme

Ilk donemde `universe_scanner.py` ve legacy implementasyon yuzeyi cok fazla sorumluluk tasiyordu. Zamanla strateji mantiklari ayri module'lere tasindi veya ayri module pattern'i guclendirildi.

Yapilanlar:

- Strategy family ayrimi netlestirildi.
- `dual_momentum` ayri strateji module'u olarak eklendi.
- Rotation-style stratejiler icin ortak prepared-data path'i korundu.
- Legacy dosyadan production path'e gereksiz kod import edilmemesi icin testler eklendi.
- Strategy registry / family mapping guard testleri eklendi.

Etkisi:

- Yeni strateji ekleme yuzeyi daha kontrollu hale geldi.
- Eski strateji logic'inin kazara bozulma riski azaldi.
- Legacy isolation testleri ile gizli legacy bagimlilik riski azaldi.

### 4.2 Data Pipeline Evrimi

Data pipeline once daha karmasik raw 1m / 60m / 4H akislariyla ilerliyordu. Kullanici tercihi ve pratik runtime ihtiyaci nedeniyle sade default su hale getirildi:

- Daily data indir.
- Raw 5m data indir.
- Raw 5m'den RTH 130m bar uret.
- Manifest yaz.

Guncel downloader:

```text
testdata/vericekici_updated.py
```

Desteklenen komut yuzeyi:

```text
--symbols
--symbols-file
--universe {all}
--feed {iex,sip}
--adjustment {all,raw,split,dividend}
--daily-start
--intraday-start
--end
--output-dir
--overwrite
```

Son eklenen iyilestirme:

- `download_manifest.csv` artik row count yaninda first/last date kolonlarini da yazar:
  - `daily_first_date`
  - `daily_last_date`
  - `raw_5min_first_date`
  - `raw_5min_last_date`
  - `bar_130min_first_date`
  - `bar_130min_last_date`

Bu sayede `downloaded` yazdigi halde vendor verisi eski biten semboller net gorunuyor.

### 4.3 Backtest Data Layout Entegrasyonu

Backtest tarafi `bars_130min` ve `daily` klasorlerini ana veri kaynagi olarak kullanacak sekilde baglandi.

Yapilanlar:

- `daily` ve `bars_130min` klasorleri audit kapsaminda kontrol ediliyor.
- QQQ/SPY daily ve 130m dosyalari kritik benchmark bagimliligi olarak kontrol ediliyor.
- Prepared cache data root, timeframe, date range ve symbol list bilgisine gore ayriliyor.
- Offset-bearing timestamp parsing icin UTC -> America/New_York donusumu standardize edildi.
- `raw_1min` kaynak-only olarak tutuldu; normal backtest path'i degil.

Etkisi:

- Intraday swing backtest ile daily regime/feature kontrolleri ayrildi.
- Raporlar hem latest hem timestamped run klasorlerine yaziliyor.
- Data audit, veri hazirligi hatalarini kod regresyonundan ayiriyor.

### 4.4 Benchmark ve Report Hygiene

Benchmark tarafinda en kritik riskler:

- Benchmark leakage
- EqualWeight universe hesaplarinda eksik sembol davranisi
- Baslangic fiyatinin future price ile alinmasi
- Benchmark report ile strategy report arasinda sessiz sapma

Yapilanlar:

- QQQ BuyHold ve EqualWeight benchmark audit raporu eklendi.
- EqualWeight benchmark'in base/prepared OHLCV close data kullandigi rapora yazildi.
- Benchmark integrity testleri eklendi:
  - Losing valid symbol benchmark'tan dusurulmesin.
  - BuyHold start veya onceki fiyat kullansin, future fiyat kullanmasin.
  - Start oncesi fiyat yoksa hata versin.
- Regression guard raporu QQQ ve EqualWeight return sabitlerini kontrol ediyor.

Son benchmark durumu:

- QQQ BuyHold Return: `191.71%`
- EqualWeight Universe Return: `196.44%`
- QQQ date range: `2020-07-27 09:30:00-04:00 -> 2026-06-18 00:00:00-04:00`
- EqualWeight date range: `2020-07-27 09:30:00-04:00 -> 2026-06-18 00:00:00-04:00`
- Missing EqualWeight symbols: `ABNB, PARA, PLTR, RBLX, SNOW, WBD`
- Son pipeline bayragi: `benchmark_changed=False`

### 4.5 Compact Report ve Candidate Decisioning

`strategy_compact_report` proje icin ChatGPT/human handoff formatina donustu.

Raporlamada izlenen metrikler:

- Net Getiri
- CAGR
- Max Drawdown
- Sharpe
- Trade Count
- Win Rate
- Profit Factor
- Avg/Median/Best/Worst R
- Expectancy R
- Turnover estimate
- Benchmark-relative excess return
- Compact Score
- Signal diagnostics

Final role karar raporu:

```text
testdata/reports/latest/final_candidate_decision_report.md
```

Son final roles:

- Main: `rs_m126_top30_sma100_no_regime`
- Aggressive: `rs_m126_top20_sma100_no_regime`
- Research/aggressive only: `rs_m126_top10_sma100_no_regime`
- Diversifier: `donchian_20_10_top20`
- Baseline: `rs_m63_sma100`

Not: Son basic paper run, OOS/attribution/cost/liquidity raporlarini yeniden uretmedi. Master report'taki final decision, onceki full diagnostic kaynakli karar tablosunu kullanmaya devam ediyor.

### 4.6 OOS, Attribution, Cost Stress, Liquidity Guard

Projede strateji secimi sadece CAGR'a gore yapilmadi. Ek raporlar eklendi:

- OOS / walk-forward
- Candidate attribution
- Cost / slippage stress
- Liquidity guard sensitivity
- Final candidate role decision

Bu raporlar full diagnostic komutuyla uretiliyor:

```text
python universe_scanner.py --reports basic --parallel --workers auto --run-oos --run-attribution --run-cost-stress --run-liquidity-guard-sensitivity --make-chatgpt-report
```

Guncel paper run'da bu opsiyonlar kapaliydi:

- `run_oos=False`
- `run_attribution=False`
- `run_cost_stress=False`
- `run_liquidity_guard_sensitivity=False`

Bu nedenle su an paper report hizli ve operasyonel; final strategy confidence icin full diagnostic tekrar calistirilmelidir.

### 4.7 Paper Trading Workflow

Paper workflow ciddi sekilde gelisti.

Ana dosyalar:

```text
paper_signal_report.csv/md
paper_pending_orders.csv
paper_order_review.csv
paper_trade_log.csv
paper_trade_log_template.csv
paper_trading_plan.md
daily_preflight_report.md
```

Yapilanlar:

- Paper sinyalleri sadece local report olarak uretiliyor.
- Broker order submit yok.
- Accepted order state `paper_pending_orders.csv` icinde tutuluyor.
- Next-open data yoksa emirler `PENDING_NEXT_OPEN` olarak kalmaya devam ediyor.
- Onceki pending next-open emirleri sonraki run'da tasiniyor.
- User review karari `REJECT` ise pending fill uygulanmiyor.
- `paper_trade_log` bos concat warning'i giderildi.
- Master report paper readiness bolumu current state'ten uretiliyor.
- Paper state validation raporu eklendi.

Son paper durumu:

- Selected main candidate: `rs_m126_top30_sma100_no_regime`
- Latest signal date: `2026-06-18`
- Actionable BUY count: `5`
- Actionable SELL count: `0`
- Accepted order count: `5`
- Pending next open count: `5`
- Filled count this run: `0`
- Rejected count this run: `0`
- Stale data warning: `False`

Actionable symbols:

```text
AMAT
EQIX
INTC
MRVL
MU
```

Hepsi:

```text
fill_status=PENDING_NEXT_OPEN
user_decision=ACCEPT
reason=initial_paper_state;latest_entry_signal;missing_next_open
```

### 4.8 Execution Preview ve Pre-submit Safety

Execution layer canli emir gondermiyor. Sadece dry-run / readiness raporu uretiyor.

Ana dosyalar:

```text
order_intents.csv
order_risk_check_report.csv/md
execution_preview_report.csv/md
execution_audit_log.csv
pre_submit_readiness_report.csv/md
short_readiness_report.csv/md
```

Yapilanlar:

- `broker_submit_enabled=false`
- `live_trading_enabled=false`
- `submit_order_called=false`
- `post_request_used=false`
- Order intents uretildi.
- Risk pass count ile future submit candidate count ayrildi.
- Preview duplicate namespace ile future submit namespace ayrildi.
- Pre-submit readiness eklendi.
- Alpaca paper connection / positions / orders check rapordan okunuyor.
- Data precheck status `OK` degilse pre-submit readiness artik `BLOCK`.
- Short readiness ayri rapora alindi ve short submit disabled kaldi.

Son execution readiness:

- Execution preview status: `OK`
- Order intents count: `5`
- Risk pass count: `5`
- Submit candidate risk pass count: `5`
- Blocked count: `0`
- Duplicate count: `0`
- Would submit count: `0`
- Pre-submit readiness: `BLOCK`
- Blocking checks count: `5`
- Submit allowed: `False`

Bloklayan ana nedenler:

- Broker submit disabled
- Live trading disabled
- Pending next-open orders var
- Data precheck `WARNING`
- Short submit disabled

### 4.9 Operational Health ve Run Manifest

Yeni eklenen `run_manifest.json`, her run icin su bilgileri tek yerde topluyor:

- Command
- Report mode
- Flags
- Latest/run dir
- Daily/intraday data dir
- Latest daily / 130min date
- Cache hit/miss
- Loaded symbols / rows
- Pipeline status
- Strategy metrics changed
- Benchmark changed
- Warning summary
- Generated run reports
- Available latest reports

Son manifest:

```text
command: python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report
report_mode: basic
paper_report: true
run_datetime: 2026-06-21 14:38:31
latest_daily_date: 2026-06-18
latest_130min_date: 2026-06-18
pipeline_status: WARNING
warning_summary: early_daily_end;psa_zero_volume
strategy_metrics_changed: false
benchmark_changed: false
symbols_loaded: 199
rows_loaded: 875825
cache_hit_count: 1
cache_miss_count: 0
```

Bu manifest ozellikle daily pipeline, paper report ve pre-submit arasinda neyin hangi data state ile uretildigini izlemek icin onemli.

### 4.10 Windows Operator Workflow

Proje Windows/Powershell/BAT-first is akisi icin sadeleştirildi.

Ana BAT dosyalari:

```text
scripts/run_basic.bat
scripts/run_paper_report.bat
scripts/run_daily_paper_pipeline.bat
scripts/run_data_warning_refresh.bat
scripts/run_execution_preview.bat
scripts/run_pre_submit_check.bat
scripts/run_alpaca_check.bat
scripts/run_full_diagnostic.bat
scripts/run_robustness.bat
scripts/run_tests.bat
scripts/backup_latest_reports.bat
```

Yapilanlar:

- BAT dosyalari repo root'tan calisip `testdata` icine geciyor.
- Paper report BAT artik paper report + execution preview + pre-submit check calistiriyor.
- Daily pipeline data update + paper report + execution preview + pre-submit check calistiriyor.
- Backup script latest raporlari, paper state'i, execution safety raporlarini, schema validation'i ve run manifest'i yedekliyor.
- `run_tests.bat` pytest yerine unittest suite calistiriyor.
- `run_data_warning_refresh.bat` DOW/BK/PARA/PSA icin hedefli data refresh yapiyor.

### 4.11 Test Suite

Test suite giderek safety/regression guard agina donustu.

Guncel test sonucu:

```text
scripts/run_tests.bat passed
Ran 66 tests
OK
```

Eklenen/aktif test alanlari:

- Benchmark integrity
- Broker submit safety
- Data precheck actions
- Downloader manifest columns
- Legacy isolation
- Master report sections
- Operator scripts
- Paper pending fill
- Pre-submit gate
- Report paths
- Report schemas
- Run manifest
- Strategy registry
- Risk gate
- Execution engine
- Paper state manager

Bu testler strateji logic'ini optimize etmekten cok, proje guvenilirligini ve operasyonel hatalari yakalamayi hedefliyor.

## 5. Guncel Strateji Sonuclari

Son `strategy_compact_report.csv` basic run tablosundan ozet:

```text
Experiment Name               Strategy Name                Net Getiri   CAGR    MaxDD    Sharpe  Trades  Win%  PF    ExpectancyR  Compact   Excess vs QQQ
donchian_20_10_top20          donchian_breakout              132.70    10.51   -15.14    0.85     844   45.4  2.17     0.0234    -0.04922     -59.01
rs_m63_sma100                 relative_strength_rotation     112.63     9.33   -14.88    0.63     577   51.1  2.09     0.0294    -0.05152     -79.08
donchian_20_10_top10          donchian_breakout              142.54    11.05   -15.94    0.73     433   47.6  2.20     0.0239    -0.05672     -49.17
rs_m63_no_regime              relative_strength_rotation     133.56    10.55   -17.99    0.80     717   49.8  1.98     0.0267    -0.08532     -58.15
donchian_55_20_sma100         donchian_breakout              128.04    10.24   -19.31    0.76     548   44.9  2.50     0.0373    -0.14557     -63.67
dm_m63_top20                  dual_momentum                  122.94     9.95   -20.39    0.39     501   50.1  2.11     0.0377    -0.15646     -68.77
donchian_55_20_top20          donchian_breakout              129.88    10.35   -20.45    0.78     540   45.0  2.62     0.0374    -0.15687     -61.83
mast_sma200_slope20_top20     ma_slope_trend                 114.33     9.44   -27.59    0.31     452   49.1  2.21     0.0394    -0.22582     -77.38
mast_sma100_slope20_top10     ma_slope_trend                 133.23    10.54   -32.70    0.33     251   46.6  2.16     0.0432    -0.27312     -58.48
mast_sma100_slope20_top20      ma_slope_trend                 81.05     7.27   -31.89    0.27     468   45.7  1.82     0.0338    -0.27442    -110.66
```

Onemli yorum:

- Basic run'daki en iyi compact score `donchian_20_10_top20` gorunuyor.
- Final decision report ise daha genis OOS/attribution/cost/liquidity degerlendirmesine dayanarak `rs_m126_top30_sma100_no_regime` stratejisini main aday olarak tutuyor.
- Bu fark normaldir: basic run sadece hizli compact karsilastirma; final decision daha genis aday validasyonlarina dayaniyor.
- Son basic run'da `rs_m126_*` adaylari compact report'taki 10 experiment listesinde degil, fakat paper/final candidate workflow'ta korunuyor.

## 6. Guncel Data Durumu

Son data audit:

- Symbols: `200`
- Daily files: `200`
- Valid daily files: `200`
- 130min files: `200`
- Empty daily files: `0`
- Short-history symbols: `0`
- Symbols ending before global end date: `2`
- Zero volume warnings: `1`
- QQQ daily/130min mevcut: `True`
- SPY daily/130min mevcut: `True`

Problem semboller:

```text
BK   daily_last_date=2026-05-20, 130min_last_date=2026-05-20
PARA daily_last_date=2025-08-06, 130min_last_date=2025-08-06
PSA  daily_last_date=2026-06-18, zero_volume_rows=1
```

Data warning refresh sonucu:

```text
symbol daily_status daily_rows daily_first_date daily_last_date raw_5min_last_date bar_130min_last_date
DOW    downloaded   1479       2020-07-27       2026-06-18      2026-06-18         2026-06-18
BK     downloaded   1459       2020-07-27       2026-05-20      2026-05-20         2026-05-20
PARA   downloaded    869       2022-02-17       2025-08-06      2025-08-06         2025-08-06
PSA    downloaded   1479       2020-07-27       2026-06-18      2026-06-18         2026-06-18
```

Yorum:

- DOW sorunu cozuldu; artik empty daily degil.
- BK ve PARA vendor'dan indirildigi halde eski tarihte bitiyor. Bu kod hatasi gibi degil, vendor/universe/sembol yasam dongusu konusu gibi gorunuyor.
- PSA icin veri guncel, fakat 1 adet zero-volume satiri var.
- Pipeline status bu yuzden hala `WARNING`.

## 7. Guncel Pre-submit Durumu

Pre-submit readiness tablosundaki kritik satirlar:

```text
broker_submit_enabled              BLOCK
live_trading_enabled               BLOCK
pending_next_open_count            BLOCK, value=5
data_precheck_status               BLOCK, value=WARNING
short_submit_allowed               BLOCK
submit_order_called                PASS, value=False
post_request_used                  PASS, value=False
alpaca_paper_connection_ok         PASS
alpaca_positions_orders_ok         PASS
local_state_execution_preview_match PASS
```

Yorum:

- Sistem broker submit'e bilerek kapali.
- Submit guvenli degil diye degil, submit yetkisi bilincli olarak aktif edilmedigi icin kapali.
- Data precheck `OK` degilse pre-submit artik blokluyor.
- Pending next-open emirler gercek next-open data gelmeden submit edilemez.
- Short taraf tamamen kapali.

## 8. Guncel Performance Durumu

Son run:

- Total runtime: `38.8558 sec`
- Cache hit/miss: `1/0`
- Symbols loaded: `199`
- Rows loaded: `875825`
- Parallel enabled: `True`
- Workers: `15`
- CPU count: `16`
- Slowest stage: `paper_report`, `20.6079 sec`
- Slowest strategy runtime: `rs_m63_no_regime`, `11.1319 sec`

Yorum:

- Prepared cache isliyor.
- OneDrive path performans uyarisi devam ediyor.
- Paper report su an en pahali bolum.
- Local SSD path'e tasima runtime'i iyilestirebilir.

## 9. Guvenlik ve Live Trading Durumu

Canli trading durumu:

```text
live_trading_enabled=false
broker_submit_enabled=false
submit_order_called=false
post_request_used=false
would_submit_count=0
submit_allowed=false
```

Bu iyi bir durumdur. Proje henuz live-ready olmadigi icin emir gondermemesi dogrudur.

Live/paper submit'e gecmeden once gerekenler:

- Data precheck `OK` olmali veya BK/PARA/PSA icin explicit policy yazilmali.
- Pending next-open emirler fill/reject olarak dogrulanmali.
- Broker submit fonksiyonu explicit approval gate ile acilmali.
- Idempotency key namespace production submit icin ayrica test edilmeli.
- Account buying power, open positions, open orders, duplicate orders broker state'i ile her gun karsilastirilmali.
- OOS/attribution/cost/liquidity raporlari son data ile tekrar calistirilmali.
- Survivorship bias ve adjusted data konusu cozulmeli.

## 10. Bilinen Riskler

### 10.1 Survivorship Bias

Evren bugunku mevcut hisselerden olusuyorsa gecmis performans oldugundan iyi gorunebilir. Bu, projedeki en buyuk research risklerinden biridir.

Gereken:

- Historical constituents
- Delisted symbols
- Point-in-time universe
- Corporate action aware data

### 10.2 Vendor Data / Symbol Lifecycle

BK ve PARA indirildigi halde eski tarihte bitiyor:

- BK: `2026-05-20`
- PARA: `2025-08-06`

Bu semboller icin karar gerekli:

- Universe'den cikarilacak mi?
- Stale symbol exception policy yazilacak mi?
- Baska feed/SIP ile tekrar denenecek mi?
- Symbol rename/delisting/corporate action kontrolu yapilacak mi?

### 10.3 PSA Zero Volume

PSA guncel ama 1 zero-volume satiri var. Tek satir oldugu icin kritik olmayabilir, fakat paper submit gate su an bunu warning olarak blokluyor.

Gereken:

- Satir detayini bul.
- Vendor hatasi mi, market holiday/partial data mi, gercek zero volume mu kontrol et.
- Policy yaz: tekil zero-volume non-candidate ise warning mi, block mu?

### 10.4 Adjusted Data

Raporlarda dividend adjustment warning var. Uzun donem backtestlerde adjusted/unadjusted OHLC ayrimi sonucu etkileyebilir.

### 10.5 Transaction Cost ve Slippage

Cost/slippage stress raporu var, fakat son run'da yeniden uretilmedi. Gercek live/paper readiness icin son data ile tekrar kosulmali.

### 10.6 Execution Reality

Backtest fill varsayimlari ve paper pending next-open davranisi kodda daha iyi izleniyor, fakat gercek broker order fill simulasyonu hala sinirli.

## 11. Basarili Olanlar

Bu noktaya kadar basarili sayilabilecek ana kazanclar:

- Strategy logic buyuk refactor yapmadan korundu.
- Benchmark logic daha izlenebilir hale geldi.
- Data layout netlesti.
- Downloader sade ve operasyonel hale geldi.
- 130min RTH aggregation standardize edildi.
- Reports canonical path altinda toplandi.
- Paper state dosyalari olustu.
- Pre-submit safety gate eklendi.
- Broker submit kapali ve testlerle korunuyor.
- Run manifest eklendi.
- Schema validation eklendi.
- Regression guard eklendi.
- Operator BAT workflow olustu.
- Backup workflow genisletildi.
- Test suite 66 teste cikti.

## 12. Henuz Tamamlanmayanlar

Henuz tamamlanmayan veya karar bekleyen konular:

- Canli trading hazirligi tamamlanmadi.
- Paper order next-open fill henuz gercek next-open data ile kapanmadi.
- BK/PARA/PSA data policy karari verilmedi.
- Full diagnostic son data ile tekrar calistirilmadi.
- Survivorship-bias-free universe yok.
- Historical delisted data yok.
- Realistic spread/order book/slippage entegrasyonu yok.
- Broker submit explicit approval gate ile bile henuz acik degil.
- Runner hala cok buyuk; orta vadede modulerlestirilmeli.

## 13. Onerilen Sonraki Is Sirasi

### 13.1 Kisa Vadeli

1. BK/PARA/PSA icin data policy karari ver:
   - Exclude stale symbols
   - Exception allowlist
   - SIP feed refresh
   - Universe lifecycle metadata

2. `data_precheck_status=OK` veya bilincli accepted-warning policy elde et.

3. Next valid market open data geldikten sonra:

```text
scripts/run_daily_paper_pipeline.bat
```

4. Pending next-open emirlerin fill/reject davranisini dogrula.

5. Full diagnostic tekrar calistir:

```text
scripts/run_full_diagnostic.bat
```

### 13.2 Orta Vadeli

1. Data policy module'u ekle:
   - stale symbol policy
   - zero-volume policy
   - candidate-critical data policy

2. Runner'i parcalara ayir:
   - report writers
   - paper state
   - operational health
   - run manifest

3. Broker paper submit'i sadece explicit approval ve PASS gate ile tasarla.

4. Historical universe / delisted data planini baslat.

5. Slippage/spread modelini daha gercekci hale getir.

### 13.3 Uzun Vadeli

1. Point-in-time universe.
2. Corporate action aware data.
3. Broker reconciliation.
4. Real order lifecycle state machine.
5. Paper trading burn-in period.
6. Live trading sadece tum gate'ler PASS olduktan sonra.

## 14. Son Teknik Kanitlar

Son dogrulamalar:

```text
scripts/run_data_warning_refresh.bat passed
python -m unittest discover -s tests -p "test_*.py" passed, 66 tests
scripts/run_tests.bat passed, 66 tests
```

Son health:

```text
run_datetime: 2026-06-21 14:38:30
status: WARNING
warning_summary: early_daily_end;psa_zero_volume
strategy_metrics_changed: False
benchmark_changed: False
pending_next_open_count: 5
filled_count_this_run: 0
latest_daily_date: 2026-06-18
latest_130min_date: 2026-06-18
```

Son data refresh backup:

```text
testdata/backups/data_warning_refresh_before_2026-06-21_143218
```

## 15. Final Degerlendirme

Proje artik sadece "backtest scriptleri" degil; veri denetimi, benchmark denetimi, strategy compact report, final candidate decisioning, paper state, execution preview, pre-submit safety ve run manifest iceren bir research/paper-readiness framework haline geldi.

En iyi haliyle bugunku durum:

- Research ve paper-readiness icin kullanilabilir.
- Live trading icin hazir degil.
- Ana strateji adayi `rs_m126_top30_sma100_no_regime`.
- Aggressive aday `rs_m126_top20_sma100_no_regime`.
- Diversifier `donchian_20_10_top20`.
- Basic compact run'da Donchian iyi gorunse de final aday secimi daha genis OOS/attribution/cost/liquidity degerlendirmesine dayaniyor.
- Kalan en buyuk teknik blokaj data policy ve survivorship-bias-free validation.

Bu dosya, mevcut durum snapshot'idir. Yeni data, yeni full diagnostic run veya strategy logic degisikligi yapildiginda tekrar guncellenmelidir.
