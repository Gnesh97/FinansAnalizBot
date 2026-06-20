# Architecture Review

## 1. Current Architecture Summary

* CLI entry point: `testdata/universe_scanner.py`. Argparse ile command flags alir, `VALIDATION_EXPERIMENTS` listesini tutar ve `runner.run_strategy_experiments()` veya `runner.run_alpaca_paper_check()` cagrisi yapar.
* Data loading: ana backtest data okuma ve audit akisi `testdata/runner.py` icindedir; OHLCV parse isleri `data_engine.read_ohlcv_csv_fast` uzerinden kullanilir.
* Prepared cache: `runner.py` icinde `_load_or_build_prepared_cache()`, `_prepared_cache_key()` ve `_prepared_cache_path()` ile `testdata/hisseler/.cache/prepared_features` altinda tutulur.
* Strategy runner: `runner.py` icindeki `_run_strategy()`, `_build_experiment_tasks()`, `_run_experiment_batch()` ve worker fonksiyonlari strateji modullerini cagirir.
* Report generation: kompakt benchmark/score raporlari `reports.py`; audit, OOS, attribution, cost, liquidity, paper, health ve master report yazimi buyuk oranda `runner.py`.
* Paper trading state: `testdata/reports/latest` altindaki `paper_signal_report.csv`, `paper_order_review.csv`, `paper_pending_orders.csv`, `paper_trade_log.csv` ve ilgili template/plan dosyalari.
* Alpaca paper check: `runner.py` icinde `run_alpaca_paper_check()` ve yardimci GET-only fonksiyonlari; order submit yok.

## 2. Module Responsibility Map

### `universe_scanner.py`

* Gorevi: CLI argumanlarini tanimlamak, default validation experiment setini tutmak, runner cagrilarini baslatmak.
* Bagimliliklar: `argparse`, `sys`, `Path`, `runner.run_strategy_experiments`, `runner.run_alpaca_paper_check`.
* Ciktilar: dogrudan dosya yazmaz; runner tarafinda raporlar uretir.
* Fazla sorumluluk: medium. CLI ve experiment profile ayni dosyada; davranis kucuk ama config kalabaligi artabilir.

### `runner.py`

* Gorevi: path resolution, report root olusturma, prepared cache, data audit, benchmark audit, experiment worker orchestration, strategy metric rows, optional reports, paper state, operational health, Alpaca paper check.
* Bagimliliklar: `config.py`, `data_engine.py`, `diagnostics.py`, `reports.py`, tum strategy modulleri, pandas, multiprocessing, filesystem.
* Ciktilar: `reports/latest`, `reports/runs`, `reports/history`, paper CSV/MD, benchmark/data/performance/decision/master reports.
* Fazla sorumluluk: high. Orchestration, domain metrics, report writing, paper state mutation ve Alpaca checks ayni dosyada.

### `reports.py`

* Gorevi: benchmark metric hesaplari, equal-weight benchmark, compact score, compact markdown yazimi.
* Bagimliliklar: pandas, numpy, `diagnostics.py` table helpers.
* Ciktilar: compact markdown dosyasi; benchmark/score kolonlari.
* Fazla sorumluluk: low/medium. Benchmark hesaplari ve compact report writing ayni dosyada, ama sinir halen okunabilir.

### `diagnostics.py`

* Gorevi: asset signal diagnostics, calendar summary, zero-trade metric defaults, attribution/context table helpers.
* Bagimliliklar: pandas, numpy.
* Ciktilar: dataframe/dict rows; dogrudan report path yonetmez.
* Fazla sorumluluk: low. Yardimci hesap ve table uretimi icin uygun.

### `vericekici_updated.py`

* Gorevi: Alpaca data downloader; daily, raw 5min, RTH 130min aggregate, manifest ve download summary uretir.
* Bagimliliklar: alpaca-py, pandas, argparse, `.env`, timezone/path helpers.
* Ciktilar: `hisseler/daily`, `hisseler/raw_5min`, `hisseler/bars_130min`, `hisseler/manifest/download_manifest.csv`.
* Fazla sorumluluk: medium. CLI, credential handling, download, aggregation ve manifest ayni dosyada; simdilik izole oldugu icin risk kontrollu.

### `strategies/relative_strength_rotation.py`

* Gorevi: relative strength signal features, param normalization, selection/backtest helpers.
* Bagimliliklar: pandas, numpy, diagnostics/rotation utilities.
* Ciktilar: enriched OHLCV frames, strategy metrics/selection outputs.
* Fazla sorumluluk: medium. Signal generation ve portfolio/selection helpers ayni aile icinde; trade logic nedeniyle hassas.

### `strategies/donchian_breakout.py`

* Gorevi: Donchian features, regime/rebalance selection, breakout signal and metrics.
* Bagimliliklar: pandas, numpy, `rotation_utils`.
* Ciktilar: enriched OHLCV frames, Donchian strategy metrics.
* Fazla sorumluluk: medium. Feature, selection ve metrics ayni dosyada; davranis korunarak ayrilmali.

### `strategies/ma_slope_trend.py`

* Gorevi: MA slope trend features, regime, selection, strategy metrics.
* Bagimliliklar: pandas, numpy, `rotation_utils`.
* Ciktilar: enriched OHLCV frames, MAST metrics.
* Fazla sorumluluk: medium. Strategy family boundary net, ama feature/selection/backtest bolunebilir.

### `strategies/dual_momentum.py`

* Gorevi: dual momentum features, regime, selection, strategy metrics.
* Bagimliliklar: pandas, numpy, `rotation_utils`.
* Ciktilar: enriched OHLCV frames, DM metrics.
* Fazla sorumluluk: medium. Strategy domain icinde kalmis; refactor once regression guard gerekir.

## 3. Current Strengths

* Report path standardize: `reports/latest`, `reports/runs`, `reports/history` ayrimi var.
* Regression guard var: QQQ/EW baseline, main role ve paper pending okunabilirligi kontrol ediliyor.
* Master report var: ChatGPT handoff icin tek dosya yuzeyi korunuyor.
* Paper workflow stateful: review, pending, trade log ve previous pending next-open akisi mevcut.
* Backup workflow var: latest rapor ve paper state yedeklenebiliyor.
* Strategy/benchmark changed kontrolu var: master/health/guard yuzeyinde false olarak raporlaniyor.
* Parallel runner calisiyor: prepared cache worker init ile experiment batch paralel kosuyor.
* Downloader backtestten ayrilmis: data update `vericekici_updated.py`, backtest/paper `universe_scanner.py` uzerinden gidiyor.

## 4. Architecture Risks

* `runner.py` cok fazla sorumluluk tasiyor. Path, cache, orchestration, metrics, report writing, paper state, Alpaca check ve operational health tek dosyada oldugu icin degisiklik blast radius yuksek.
* `universe_scanner.py` CLI + experiment profile tutuyor. Orchestration az ama command profiles/config buyudukce entry point kalabaliklasabilir.
* Paper trading state `reports/latest` altinda kalinca report snapshot ile mutable state ayni yerde duruyor. Kisa vadede geriye uyumluluk icin kalmali; orta vadede `paper_state/` dusunulebilir ve latest raporlar oradan mirror okuyabilir.
* Optional reports overwrite riski kontrollu ama var. `basic` mod stale optional dosyalari temizliyor; yine de latest altinda report-only output ve state dosyalarinin ayni dizinde olmasi dikkat istiyor.
* Path handling kismen merkezi. `resolve_project_path()` ve `_report_output_paths()` iyi baslangic, ama downloader, scripts ve backup tarafinda path bilgisi daginik.
* Config degerleri hard-coded. `VALIDATION_EXPERIMENTS`, universe sembolleri, baseline constants, paper capital/default max positions ve report names farkli dosyalarda sabit.
* Report generation ve business logic fazla ic ice. Ozellikle paper order application, pending fill, guard report ve master section writing ayni akista.
* Data update / backtest / paper workflow ayrimi CLI seviyesinde net, ama daily BAT hepsini sirali calistirdigi icin run manifest olmadan hangi adimin hangi inputla calistigi sonradan izlenmesi zor.

## 5. Safe Refactor Opportunities

### Config constants merkezi dosya

* Risk seviyesi: low
* Beklenen fayda: baseline constants, report file names, command defaults ve paper defaults tek yerden okunur.
* Degisecek dosyalar: `testdata/config.py`, `testdata/universe_scanner.py`, `testdata/runner.py`
* Validation komutu: `python -m py_compile testdata\config.py testdata\universe_scanner.py testdata\runner.py`

### Path resolver/helper

* Risk seviyesi: low
* Beklenen fayda: `reports/latest`, `reports/runs`, `reports/history`, data dirs ve backup paths icin tek sozlesme.
* Degisecek dosyalar: yeni `testdata/paths.py`, `testdata/runner.py`, opsiyonel scripts docs.
* Validation komutu: `python -m py_compile testdata\paths.py testdata\runner.py`

### Report writer helper

* Risk seviyesi: medium
* Beklenen fayda: markdown/csv writing, latest+run mirror yazimi, history append ve schema kolon sirasi tekrarini azaltir.
* Degisecek dosyalar: yeni `testdata/report_writers.py`, `testdata/runner.py`
* Validation komutu: `python -m py_compile testdata\report_writers.py testdata\runner.py`

### Paper state manager

* Risk seviyesi: medium
* Beklenen fayda: pending/review/trade log okuma-yazma ve order aging tek modulde toplanir; state/report ayrimi netlesir.
* Degisecek dosyalar: yeni `testdata/paper_state.py`, `testdata/runner.py`
* Validation komutu: `python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report`

### Command profiles

* Risk seviyesi: low
* Beklenen fayda: daily/basic/full/robustness/alpaca command setleri dokumante ve test edilebilir hale gelir.
* Degisecek dosyalar: yeni `testdata/command_profiles.py`, `testdata/universe_scanner.py`, `docs/COMMANDS.md`
* Validation komutu: `python universe_scanner.py --help`

### Schema validation for CSV reports

* Risk seviyesi: low/medium
* Beklenen fayda: `paper_pending_orders.csv`, `pipeline_health_log.csv`, `strategy_compact_report.csv` kolon driftleri erken yakalanir.
* Degisecek dosyalar: yeni `testdata/report_schemas.py`, `testdata/runner.py`
* Validation komutu: `python -m py_compile testdata\report_schemas.py testdata\runner.py`

### Pytest regression tests

* Risk seviyesi: low
* Beklenen fayda: benchmark constants, master report section, pending schema ve no-live-order safety otomatik dogrulanir.
* Degisecek dosyalar: yeni `tests/` veya `testdata/tests/`
* Validation komutu: `pytest`

### Run manifest

* Risk seviyesi: low
* Beklenen fayda: her run icin command, input data dates, cache digest, reports generated ve warnings tek JSON/CSV kayda girer.
* Degisecek dosyalar: `testdata/runner.py`, opsiyonel yeni `testdata/run_manifest.py`
* Validation komutu: `python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report`

## 6. Do Not Touch Areas

* Strategy signal logic
* Benchmark calculation
* Trade simulation/fill assumptions
* Report metric formulas
* Historical result calculations
* Existing final candidate decision formulas
* Existing paper fill semantics and next-open assumptions

## 7. Recommended Refactor Sequence

1. Dusuk riskli dokumantasyon/config/path cleanup: constants ve path helper sozlesmesini yaz, davranis parity check ile dogrula.
2. Paper state manager: mevcut `reports/latest` dosyalarini bozmadan read/write helper katmani cikar.
3. Report generator modularization: once pure writer helpers, sonra master report section builders.
4. Pytest regression guard: mevcut guard beklentilerini test fixtures ile otomatiklestir.
5. Data access/cache boundary: prepared cache key/path/load/build islerini ayri module tasimadan once exact output parity al.
6. Only then larger cleanup: `runner.py` orchestration katmanini incelt, strategy ailelerine dokunmadan ilerle.

## 8. First Suggested Implementation Task

Ilk guvenli Codex task'i: `testdata/report_schemas.py` adli kucuk bir schema constants dosyasi ekle ve yalnizca `paper_pending_orders.csv`, `pipeline_health_log.csv`, `paper_trade_log.csv` kolon listelerini oraya tasimayi planla.

* Kucuk kapsam: sadece kolon sabitleri ve import degisimi.
* Test edilebilirlik: `python -m py_compile testdata\report_schemas.py testdata\runner.py` ve `python universe_scanner.py --reports basic --parallel --workers auto --paper-report --make-chatgpt-report`.
* Strategy/benchmark sonucu degismez: sadece CSV kolon sozlesmesi merkezi hale gelir.
