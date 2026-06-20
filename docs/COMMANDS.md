# Command Guide

En kolay gunluk kullanim: `scripts/run_daily_paper_pipeline.bat` dosyasina cift tikla.

- Bu script veri gunceller.
- Paper report uretir.
- Pending accepted emirlerde next open fill kontrol eder.
- Operational health, data precheck ve regression guard raporlarini uretir.
- ChatGPT'ye gonderilecek tek dosya: `testdata/reports/latest/chatgpt_master_report.md`
- Canli emir gondermez.

Backup: `scripts/backup_latest_reports.bat`

- Raporlari ve paper state'i yedekler.
- Trade logic calistirmaz.
- Emir gondermez.
- Guvenlidir.

En kolay kullanim: Windows'ta `scripts/*.bat` dosyalarina cift tikla.

## En sik kullanacagin 3 komut

1. Gunluk kontrol: `scripts/run_basic.bat`
2. Paper trading: `scripts/run_paper_report.bat`
3. ChatGPT'ye rapor: `testdata/reports/latest/chatgpt_master_report.md`

Not: `.bat` ve PowerShell scriptleri repo kokunden calistirilir. Dogrudan `python universe_scanner.py ...` komutlari `testdata` dizini icinden calistirilir.

## Double-click BAT Files

- `scripts/run_basic.bat`: gunluk hizli kontrol.
- `scripts/run_paper_report.bat`: paper trading + ChatGPT master report.
- `scripts/backup_latest_reports.bat`: latest raporlarini ve paper state dosyalarini yedekler.
- `scripts/run_alpaca_check.bat`: Alpaca paper baglanti kontrolu, emir gondermez.
- `scripts/run_execution_preview.bat`: execution preview dry-run; duplicate/idempotency kontrol eder, emir gondermez.
- `scripts/run_pre_submit_check.bat`: pre-submit safety checklist uretir, emir gondermez.
- `scripts/run_full_diagnostic.bat`: agir detayli kontrol.
- `scripts/run_robustness.bat`: yavas research testi.
- `scripts/run_tests.bat`: schema ve paper state smoke testleri.

## Basic Backtest

- Command name: Basic backtest
- Full command: `python universe_scanner.py --reports basic --parallel --workers auto`
- Ne ise yarar?: Temel backtest, compact strategy report, audit ve performance raporlarini uretir.
- Ne zaman kullanilir?: Gunluk hizli kontrol ve temel metrik dogrulama icin.
- Guvenli mi?: Evet, local backtest/report-only.
- Emir gonderir mi?: Hayir.
- Hangi raporlari uretir?: `strategy_compact_report`, `data_audit_report`, `benchmark_audit_report`, `performance_profile_report`, final candidate raporlari.
- Ortalama runtime: Cache durumuna ve makineye bagli; genelde en hizli ana komuttur.
- ChatGPT'ye hangi dosya atilir?: `testdata/reports/latest/chatgpt_master_report.md` sadece `--make-chatgpt-report` ile uretilir; aksi halde compact MD/CSV paylasilir.

## Full Diagnostic

- Command name: Full diagnostic
- Full command: `python universe_scanner.py --reports basic --parallel --workers auto --run-oos --run-attribution --run-cost-stress --run-liquidity-guard-sensitivity --make-chatgpt-report`
- Ne ise yarar?: Final adaylar icin OOS, attribution, cost stress, liquidity guard sensitivity ve master rapor uretir.
- Ne zaman kullanilir?: Paper trading oncesi veya final karar tekrar dogrulamasinda.
- Guvenli mi?: Evet, report-only.
- Emir gonderir mi?: Hayir.
- Hangi raporlari uretir?: Basic raporlar, `oos_walkforward_report`, `candidate_attribution_report`, `cost_slippage_stress_report`, `liquidity_guard_sensitivity_report`, `chatgpt_master_report`.
- Ortalama runtime: Basic'ten belirgin uzun; aday diagnostikleri ve cache durumuna bagli.
- ChatGPT'ye hangi dosya atilir?: `testdata/reports/latest/chatgpt_master_report.md`.

## Local Paper Report

- Command name: Local paper report
- Full command: `python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report`
- Ne ise yarar?: Son bar icin local paper sinyalleri, pending orders, review dosyasi ve ledger dosyalarini uretir.
- Ne zaman kullanilir?: Gunluk paper trading review akisi icin.
- Guvenli mi?: Evet, tamamen local report-only.
- Emir gonderir mi?: Hayir. `--paper-report` broker'a emir gondermez.
- Hangi raporlari uretir?: `paper_signal_report`, `paper_pending_orders`, `paper_order_review`, `paper_trade_log`, `paper_trading_plan`, `chatgpt_master_report`.
- Operational health raporlari: `pipeline_health_log.csv` son run saglik satiridir; `reports/history/pipeline_health_log.csv` tarihsel append logudur; `data_precheck_report.md` data integrity uyarilarini yazar; `regression_guard_report.md` baseline metrik sapmalarini kontrol eder.
- Ortalama runtime: Basic backtest seviyesine yakin; paper dosyalari eklenir.
- ChatGPT'ye hangi dosya atilir?: `testdata/reports/latest/chatgpt_master_report.md`.

## Alpaca Paper Check

- Command name: Alpaca paper check
- Full command: `python universe_scanner.py --alpaca-paper-check --make-chatgpt-report`
- Ne ise yarar?: Alpaca paper endpoint, account, positions, open orders ve dry-run order preview kontrolu yapar.
- Ne zaman kullanilir?: Local paper pending orderlari broker paper hesabiyla karsilastirmadan once.
- Guvenli mi?: Evet, sadece GET-only kontrol yapar.
- Emir gonderir mi?: Hayir. Alpaca submit/order POST yoktur.
- Hangi raporlari uretir?: `alpaca_paper_check_report`, `alpaca_order_preview`, `chatgpt_master_report` icinde Alpaca Paper Readiness.
- Ortalama runtime: Ag baglantisina bagli; backtest calistirmadigi icin genelde kisa.
- ChatGPT'ye hangi dosya atilir?: `testdata/reports/latest/chatgpt_master_report.md`.

## Execution Preview Dry-run

- Command name: Execution preview dry-run
- Full command: `python universe_scanner.py --execution-preview --make-chatgpt-report`
- Ne ise yarar?: Accepted pending orderlardan order intent, risk check, duplicate/idempotency kontrolu ve execution audit log uretir.
- Ne zaman kullanilir?: Broker submit acilmadan once execution bot hazirligini dry-run olarak kontrol etmek icin.
- Guvenli mi?: Evet, dry-run/audit-only.
- Emir gonderir mi?: Hayir. `submit_order` cagrilmaz, POST yoktur.
- Hangi raporlari uretir?: `order_intents.csv`, `order_risk_check_report`, `execution_preview_report`, `execution_audit_log.csv`, `chatgpt_master_report` icinde Execution Bot Readiness.
- ChatGPT'ye hangi dosya atilir?: `testdata/reports/latest/chatgpt_master_report.md`.

## Pre-submit Check

- Command name: Pre-submit check
- Full command: `python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report`
- Ne ise yarar?: Submit acilmadan once safety checklist, readiness BLOCK/WARNING/PASS satirlari ve master report ozeti uretir.
- Ne zaman kullanilir?: Alpaca paper submit eklenmeden once hangi kosullarin eksik oldugunu gormek icin.
- Guvenli mi?: Evet, report-only.
- Emir gonderir mi?: Hayir. `submit_order` cagrilmaz, POST yoktur.
- Hangi raporlari uretir?: Execution preview raporlari ve `pre_submit_readiness_report.md/csv`.
- ChatGPT'ye hangi dosya atilir?: `testdata/reports/latest/chatgpt_master_report.md`.

## Robustness Research

- Command name: Robustness research
- Full command: `python universe_scanner.py --reports full --parallel --workers auto --run-robustness --make-chatgpt-report`
- Ne ise yarar?: RS robustness grid ve full raporlama uretir.
- Ne zaman kullanilir?: Arastirma/dogrulama icin; normal gunluk kullanimda calistirilmaz.
- Guvenli mi?: Evet, report-only ama yavas.
- Emir gonderir mi?: Hayir.
- Hangi raporlari uretir?: Full raporlar, robustness raporlari, `chatgpt_master_report`.
- Ortalama runtime: Yavas; makine, cache ve worker sayisina ciddi baglidir.
- ChatGPT'ye hangi dosya atilir?: `testdata/reports/latest/chatgpt_master_report.md`.

## Test Suite

- Command name: Lightweight tests
- Full command: `scripts/run_tests.bat`
- Ne ise yarar?: `report_schemas.py` ve `paper_state_manager.py` icin smoke/regression testleri calistirir.
- Ne zaman kullanilir?: Schema veya paper state read/write helper degisikliklerinden sonra.
- Guvenli mi?: Evet, tempfile kullanir; strategy logic calistirmaz.
- Emir gonderir mi?: Hayir.

## Guvenlik notlari

- Canli trading yok.
- Alpaca live endpoint destegi yok.
- `--paper-report` broker'a emir gondermez.
- `--alpaca-paper-check` sadece GET-only kontrol yapar; order gondermez.
- `--execution-preview` dry-run/audit-only calisir; duplicate/idempotency kontrol eder, emir gondermez.
- `--pre-submit-check` submit acilmadan once guvenlik checklist'i uretir; emir gondermez.
- Alpaca submit/order POST yok.
- `.bat` dosyalarinin hicbiri canli emir gondermez.
- `run_alpaca_check.bat` sadece GET-only kontrol yapar.
- `--run-robustness` yavas arastirma komutudur, normal gunluk kullanimda calistirilmaz.
