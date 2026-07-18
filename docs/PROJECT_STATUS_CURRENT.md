# Project Status Current

## 1. Project Purpose

Bu proje ABD hisseleri icin Python tabanli swing trading / systematic trading botudur.
ChatGPT planlama, quant review ve Codex prompt uretimi yapar.
Codex kodlama, test ve report uretimi yapar.
Su an hedef canli trading degil; local paper trading ve paper execution workflow dogrulamasidir.

## 2. Current Working Directory / Main Files

Calisma dizini:
`C:\Users\polat\OneDrive\Masaüstü\BOT - Kopya\testdata`

Ana dosyalar:

* `universe_scanner.py`
* `runner.py`
* `reports.py`
* `diagnostics.py`
* `strategies/relative_strength_rotation.py`
* `strategies/donchian_breakout.py`
* `strategies/ma_slope_trend.py`
* `strategies/dual_momentum.py`
* `vericekici_updated.py`

Legacy dosya varsa production'a import edilmemeli:

* `universe_scanner_legacy.py`

## 3. Hard Rules

* Trade logic acikca istenmedikce degistirilmeyecek.
* Benchmark logic acikca istenmedikce degistirilmeyecek.
* Yeni strateji acikca istenmedikce eklenmeyecek.
* Buyuk refactor yapilmayacak.
* `universe_scanner_legacy.py` import edilmeyecek.
* Regex/importlib/exec/eval ile legacy extraction yapilmayacak.
* Report path bozulmayacak.
* Strategy result veya benchmark result degisirse acikca raporlanacak.
* Canli emir yok.
* Alpaca live endpoint yok.
* Broker submit/order POST yok.
* ECC ve Understand-Anything sadece tooling icindir; bot runtime veya stratejilere import edilemez.


## 4. Data Status

Active Veri Klasoru:
`testdata/research_data/rs_v2_sip_market_data/`

Legacy (Frozen/Read-Only) Veri Klasoru:
`testdata/hisseler/`

Yapi:

* `daily/`
* `raw_5min/`
* `bars_130min/`
* `manifests/`
* `.cache/prepared_features/`

Backtest:

* Daily data momentum / benchmark / regime icin kullanilir.
* 130min bars intraday signal/execution icin kullanilir.
* raw_5min sadece 130min uretmek icin kaynak; backtest dogrudan raw_5min okumamali.

130min RTH schedule:

* `09:30-11:40`
* `11:40-13:50`
* `13:50-16:00`

Son data audit:

* Symbols: 200
* Daily files: 200
* Valid daily files: 199
* 130min files: 200
* Empty daily files: 1 -> DOW
* Symbols ending before global end date: BK, PARA
* Zero volume warning: PSA
* QQQ daily/130min var
* SPY daily/130min var

## 5. Benchmark Status

Benchmark period:
`2020-07-27 -> 2026-06-18`

Benchmark returns:

* QQQ BuyHold: 191.71%
* EqualWeight Universe: 196.44%

Benchmark notes:

* Same QQQ date range across experiments: True
* Same EqualWeight date range across experiments: True
* EqualWeight benchmark immutable base/prepared OHLCV close data kullaniyor.
* Missing EqualWeight symbols: ABNB, PARA, PLTR, RBLX, SNOW, WBD
* QQQ/SPY trading universe disinda tutuluyor ama benchmark/regime icin kullanilabiliyor.

## 6. Current Final Candidate Decision

Final roles:

* `rs_m126_top30_sma100_no_regime` -> main candidate
* `rs_m126_top20_sma100_no_regime` -> aggressive alternative
* `rs_m126_top10_sma100_no_regime` -> research/aggressive only
* `donchian_20_10_top20` -> diversifier
* `rs_m63_sma100` -> baseline

Main candidate:
`rs_m126_top30_sma100_no_regime`

Key metrics:

* Full period return: 225.72%
* OOS average return: 51.07%
* OOS minimum return: 23.33%
* Top1 contribution: 18.19%
* Top5 contribution: 42.97%
* Max drawdown: -19.71%
* Excess vs QQQ: 34.01%
* Cost survives: True
* Liquidity warning count: 1

Important interpretation:
Top10 has highest return but is too concentrated.
Top30 is the current main candidate because it is more balanced.

Note on Basic/Fast Validation Run vs. Final Candidates:
* The final main candidate is `rs_m126_top30_sma100_no_regime`.
* The basic/fast validation run (`VALIDATION_EXPERIMENTS` in `universe_scanner.py`) intentionally uses a smaller/different experiment set for quick health checks and may not include the M126 final candidate family.
* Therefore, `final_candidate_decision_report.md` and `strategy_compact_report.md` can show different experiment universes in basic mode.
* This is an intentional report-mode separation, not a strategy metric change.

## 7. Paper Trading State

Local paper trading aktif.
Broker/Alpaca order submit yok.

Current accepted pending orders:

* AMAT
* EQIX
* INTC
* MRVL
* MU

Current status:

* accepted_order_count: 5
* pending_next_open_count: 5
* filled_count_this_run: 0
* rejected_count_this_run: 0
* fill_status: PENDING_NEXT_OPEN

Reason:
Next open data is missing, so fills are correctly not written yet.

Expected behavior:

* Next valid market open data arrives.
* Run daily paper pipeline.
* Accepted pending orders should fill if next open data exists.
* If next open data still missing, orders remain PENDING_NEXT_OPEN.

## 8. Paper Trading Files

Important files:

* `reports/latest/paper_signal_report.md`
* `reports/latest/paper_signal_report.csv`
* `reports/latest/paper_pending_orders.csv`
* `reports/latest/paper_order_review.csv`
* `reports/latest/paper_trade_log.csv`
* `reports/latest/paper_trading_plan.md`
* `reports/latest/chatgpt_master_report.md`

Manual review file:
`reports/latest/paper_order_review.csv`

Allowed decisions:

* ACCEPT
* REJECT
* HOLD_REVIEW

## 9. Daily Pipeline

Main daily scripts:
* `scripts/run_daily_paper_pipeline.bat` (Windows)
* `scripts/run_daily_paper_pipeline.sh` (Linux)

This script:

* updates data
* updates 130min bars
* runs paper report
* checks pending accepted orders for next open fills
* creates `chatgpt_master_report.md`

ChatGPT upload file:
`testdata/reports/latest/chatgpt_master_report.md`

Current daily pipeline status:

* data update ran: True
* latest data date: daily=2026-06-18, 130min=2026-06-18
* stale warning: False
* pending next open count: 5
* filled count this run: 0
* next action: wait for next valid market open data, then run daily pipeline again.

## 10. Useful BAT Commands

Main daily usage:
`scripts/run_daily_paper_pipeline.bat`

Paper report only:
`scripts/run_paper_report.bat`

Basic backtest:
`scripts/run_basic.bat`

Alpaca paper check:
`scripts/run_alpaca_check.bat`

Full diagnostic:
`scripts/run_full_diagnostic.bat`

Robustness research:
`scripts/run_robustness.bat`

Backup latest reports:
`scripts/backup_latest_reports.bat`

## 11. Alpaca Paper Status

Alpaca paper check has been added and tested as GET-only.

It checks:

* account
* positions
* open orders
* dry-run preview

Safety:

* `submit_order` not used
* POST/order submit not used
* live trading disabled
* paper endpoint required

Current Alpaca check result:

* connection_status: ok
* account_status: ACTIVE
* open_positions: 0
* open_orders: 0
* dry-run preview rows: 5
* would_submit: False

## 12. Performance Status

Basic / paper workflow is fast enough.

Recent paper run:

* total_runtime_sec around 45-50 sec
* cache hit/miss: 1/0
* parallel enabled
* workers auto / 15
* strategy metrics changed: false
* benchmark changed: false

No need for further performance optimization right now.

## 13. Backup / Stable Checkpoint

Stable checkpoint was committed and pushed.

Stable checkpoint meaning:

* local paper workflow exists
* daily pipeline exists
* BAT commands exist
* master report exists
* backup script exists
* Alpaca paper GET-only check exists
* trade/benchmark metrics unchanged

Backup script:
`scripts/backup_latest_reports.bat`

Backup output:
`testdata/backups/YYYY-MM-DD_HHMMSS/`

## 14. Known Limitations / Warnings

* Backtest survivorship-bias-free degildir.
* Current universe may include survivorship bias.
* Adjusted data dogrulanmali.
* Transaction cost/slippage assumptions are still simplified.
* Liquidity proxy uses dollar volume only; real spread/order book not modeled.
* Alpaca paper fill is not equal to real live execution.
* No live trading readiness claim should be made yet.
* Paper trading must run for at least several weeks before live consideration.

## 15. Next Action

Do not add new features unless necessary.

Next operational step:
Wait for next valid US market open data.
Then run:
`scripts/run_daily_paper_pipeline.bat`

After run, send this file to ChatGPT:
`testdata/reports/latest/chatgpt_master_report.md`

Expected check:

* pending_next_open_count should decrease if next open data exists.
* paper_trade_log.csv should start recording PAPER_FILLED rows.
* If not, investigate next-open detection or data update.
