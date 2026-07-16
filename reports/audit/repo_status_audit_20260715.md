# Repository Status Audit — 2026-07-15

Read-only audit. No code, config, or data was modified. HANDOFF.md was located at
`/home/gnesh/Desktop/HANDOFF.md` (not inside this repo — there is no `HANDOFF.md` under
`/home/gnesh/Desktop/BOT - Kopya`) and read in full before this audit began. A second,
much shorter `HANDOFF.md` in `~/Downloads` (1.2KB) was ignored as clearly stale/unrelated.

To run the test suite (Section 5) a scratch virtualenv was created outside the repo
(`/tmp/.../scratchpad/audit_venv`) and `requirements.txt` + `pytest` + `tabulate`
installed into it. Nothing was written inside the repo except this report.

---

## 1. Repository Structure

Actual top-level layout (excluding `.git`, `__pycache__`, `.venv`, `ECC/`, `.agent/`, `.claude/`,
`.understand-anything/`, which are tooling/agent scaffolding, not project code):

```
BOT - Kopya/
├── bot.py                     # Telegram bot: scheduler + notifications (root-level, standalone)
├── universe_scanner.py        # thin root-level shim
├── vericekici_updated.py      # thin root-level shim
├── config.json                # flat config file (symbols, schedule, risk %, telegram flags)
├── requirements.txt
├── AGENTS.md, README.md, PROJECT_STATUS.md, PROJE_SURECI.md
├── docs/
│   ├── PROJECT_STATUS_CURRENT.md, PROJECT_STATUS.md, PROJECT_EVOLUTION_AND_CURRENT_STATUS.md
│   ├── ARCHITECTURE_REVIEW.md, COMMANDS.md, CODEX_TASK_TEMPLATE.md
│   └── testing/ (untracked, 1 file: rs_v2_provider_contract_forensics.tdd.md)
├── hisseler/                  # near-empty top-level data dir (see §2)
│   ├── daily/ (1 file: SPY_1D.csv), raw_5min/ (empty), bars_130min/ (empty), manifest/ (empty)
├── scripts/                   # .bat/.ps1 entry points (Windows-only)
├── testdata/                  # <- THIS is where the real production/research code and data live
│   ├── *.py  (downloader, portfolio_manager.py, risk_gate.py, execution_engine.py,
│   │          broker_*.py, reports.py, runner.py, universe_scanner.py, data_engine.py, ...)
│   ├── strategies/            # strategy engine package
│   ├── hisseler/, hisseler2/, hisseler3/, hisseler_test/, temp_hisseler/  # multiple data snapshots
│   ├── research_data/         # rs_v2_clean_market_data + delisted-securities research
│   ├── delisthisseler/, backups/, baselines/, reports/ (runs + latest), state/, scratch/
├── tests/                     # pytest/unittest suite (flat, ~65 files)
├── quantconnect/              # new, untracked — QuantConnect port scaffold
└── output/, .yfinance_cache/, .vscode/, .env, .env.example
```

### Deviations from HANDOFF's target layout

| HANDOFF target | Actual | Deviation |
|---|---|---|
| `src/` (all production code, layered `data/features/strategy/portfolio/risk/execution/broker/monitoring`) | **Does not exist.** All logic lives flat inside [testdata/](testdata) (a directory literally named "test data") and at repo root ([bot.py](bot.py)) | Production code is not separated from a `src/` tree at all; it sits in a directory whose name signals "not production" |
| `config/` (YAML/TOML/JSON, no business logic) | **Does not exist.** One flat [config.json](config.json) (7 keys) plus a second, separate `testdata/config.py` with Python constants (`DEFAULT_STRATEGY_NAME`, `SHORT_TRADING_ENABLED`, etc.) | Config is split across a JSON file and a Python module; some config is code, violating "no business logic / no code modification" rule |
| `data/` (raw → validated → processed → features, immutable raw) | **Does not exist** as such. Root [hisseler/](hisseler) is nearly empty (1 CSV). Real datasets live under `testdata/hisseler/`, `testdata/hisseler2/`, `testdata/hisseler3/`, `testdata/research_data/rs_v2_clean_market_data/`, `testdata/delisthisseler/` | At least 5 parallel, differently-named data trees under `testdata/`, no single canonical `data/raw` vs `data/processed` split |
| `notebooks/` (research-only) | **Does not exist** | No notebook-based research artifacts found; research is done via one-off `testdata/v2_*.py` / `testdata/*_audit.py` scripts instead |
| `logs/` | **Does not exist** | No dedicated log directory; scripts write directly to `testdata/reports/` |
| `tests/{unit,integration,regression,validation}` | `tests/` exists but is **flat** — ~65 files with no subdirectory split | Matches HANDOFF only partially |
| `reports/` (immutable, at repo root) | Exists at root but is unused (only this new audit dir). The active reports tree is `testdata/reports/latest/` + `testdata/reports/runs/<timestamp>/`, which **does** follow the immutable-runs pattern HANDOFF describes, just under the wrong root | Right pattern, wrong location |
| `pyproject.toml` | **Does not exist** | Only `requirements.txt` |
| `HANDOFF.md` should live in repo (and eventually under `docs/`) | **Not present in this repo at all** — it lives on the Desktop, one level up | Anyone opening this repo without being told where to look will never find it |

**Bottom line:** the actual repo has not adopted the HANDOFF target architecture at all. It's a single flat `testdata/` working directory (this matches what [docs/PROJECT_STATUS_CURRENT.md](docs/PROJECT_STATUS_CURRENT.md) itself says: *"Calisma dizini: `testdata`"*) plus a standalone `bot.py`. This is a pre-migration state relative to HANDOFF, not a partially-migrated one.

---

## 2. Data Layer Status

### What exists under data-like directories

| Directory | Files | What it is |
|---|---|---|
| `hisseler/` (root) | 1 CSV (`SPY_1D.csv`), empty manifest | Vestigial / not in active use |
| `testdata/hisseler/` | 2,829 files — `daily/`, `raw_5min/`, `bars_130min/` per symbol | **The active dataset** used by the daily paper pipeline |
| `testdata/hisseler2/` | 7,190 files, lowercase `.us.csv` tickers (Stooq/Kaggle-style naming, e.g. `aapl.us.csv`) | A separate, older/alternate historical source, not wired into current pipeline calls seen |
| `testdata/hisseler3/` | 103 files, `*_5min.csv` | Small subset intraday sample |
| `testdata/hisseler_test/` | 18 files (4 symbols: AAPL, MSFT, QQQ, SPY) | Test fixture data |
| `testdata/research_data/rs_v2_clean_market_data/` | daily/raw_5min/bars_130min + `benchmarks/` + `manifests/` | **The new "v2 clean" research dataset** (see below) |
| `testdata/delisthisseler/` | 21 files | Delisted-symbol price history, research-stage |

### `testdata/hisseler/` — the active/production dataset

Manifest: [testdata/hisseler/manifest/download_manifest.csv](testdata/hisseler/manifest/download_manifest.csv) (201 rows = 200 symbols + header).

- Symbol count: **200**
- Daily range: **2020-07-27 → 2026-07-07** (SPY starts earlier, 2018-11-01)
- 5-min raw range: 2020-07-27 → 2026-07-07
- 130-min bars: derived from 5-min, same range
- Timeframes present: `1D` (daily), `5min` (raw), `130min` (derived bars), plus a separate `60min` manifest ([download_manifest_60min.csv](testdata/hisseler/manifest/download_manifest_60min.csv))
- Manifest mtime: 2026-07-08 16:03 (last refreshed by the daily pipeline 7 days before this audit)
- Feed: `iex` (per acquisition metadata elsewhere, and per [docs/PROJECT_STATUS_CURRENT.md](docs/PROJECT_STATUS_CURRENT.md) §4: "Symbols: 200, Valid daily files: 199, Empty daily files: 1 → DOW")

### Is the old Alpaca IEX dataset still frozen/immutable, or was a new dataset acquired?

**A new dataset has been acquired since**, and it is still research-stage, not yet the active one:

- `testdata/research_data/rs_v2_clean_market_data/manifests/acq_20260712_212749/acquisition_manifest.json` — acquisition run `acq_20260712_212749`, dated 2026-07-12, `"dataset_profile": "rs_v2_clean"`, `"dataset_schema_version": "2.0.0"`, `"feed": "iex"`, 178 candidate symbols + SPY/QQQ benchmarks, range 2020-07-27 → 2026-07-07, downloaded via `testdata/vericekici_updated.py` at commit `f2f9fcb` (the current HEAD).
- This is a materially more rigorous acquisition than the original `hisseler/` dataset: every file carries per-file SHA256, gap/coverage/duplicate telemetry, and explicit `coverage_status` fields — none of which exist in the older `hisseler/manifest/download_manifest.csv`.
- Recent commits confirm this is an active, unfinished migration, not a completed cutover:
  `f2f9fcb checkpoint: close delisted data feasibility research`, `322c4f4 feat: harden research and forward-only paper broker workflow`. Numerous `testdata/reports/latest/rs_v2_*` reports dated 2026-07-12/13 (`rs_v2_night_milestone_report.md`, `rs_v2_major_milestone_report.md`, `rs_v2_clean_coverage_forensics.md`, etc.) document this as an in-progress "v2 clean data" research track, separate from the live daily paper pipeline.
- **The live daily paper pipeline (`scripts/run_daily_paper_pipeline.bat`) still points at `testdata/hisseler/`, not `rs_v2_clean_market_data/`.** So: the old dataset is still what's actually driving reports today; the new one exists but has not been cut over.

### Do the 4 known missing sessions (2021-04-19, 2021-10-25, 2022-03-08, 2022-04-06) still affect the active dataset?

Yes, as of the last write. Evidence:
- `GAP_RECOVERY_APPROVED_DATES = ("2021-04-19", "2021-10-25", "2022-03-08", "2022-04-06")` is defined in [testdata/vericekici_updated.py:102](testdata/vericekici_updated.py) and gap-recovery logic for exactly these 4 dates exists in `testdata/v2_provider_recovery.py`, `testdata/v2_gap_recovery_runner.py`, `testdata/v2_daily_gap_selection_audit.py`, and is exercised by `tests/test_rs_v2_provider_recovery.py`, `tests/test_rs_v2_gap_recovery_probe.py`.
- All of this gap-recovery machinery is scoped to the **`rs_v2_clean`** acquisition path (`--dataset-profile rs_v2_clean`), not the `hisseler/` dataset. There is no evidence these 4 sessions were back-filled into `testdata/hisseler/` — its manifest/CLI has no `--dataset-profile` or gap-recovery flags applied.
- Conclusion: the 4 missing sessions are **still present as gaps in the currently-active `testdata/hisseler/` dataset**; recovery exists only in the new, not-yet-adopted `rs_v2_clean` dataset.

### Provider-independent MarketDataProvider abstraction?

**Does not exist.** `grep -rn "MarketDataProvider\|class.*DataProvider"` across the entire repo returns zero hits. The downloader ([testdata/vericekici_updated.py](testdata/vericekici_updated.py)) is hard-coded to Alpaca: `_load_alpaca()` directly imports `alpaca.data.enums`, `alpaca.data.historical.StockHistoricalDataClient`, `alpaca.data.requests.StockBarsRequest` (lines 438-448). There is no adapter interface — swapping providers would require editing this module directly. `yfinance` is imported only in `bot.py`, for ad-hoc live-price display inside Telegram replies, and is not part of the data pipeline.

The only abstract interface (`ABC`) anywhere in the codebase is on the **broker** side: `class BrokerInterface(ABC)` in [testdata/broker_interface.py](testdata/broker_interface.py), implemented concretely by `AlpacaPaperBroker` in `testdata/alpaca_paper_broker.py`.

### Can the pipeline download FRESH data (yesterday/today) end-to-end?

**Yes** — this already works and is wired as a single command:

- Entry point: [scripts/run_daily_paper_pipeline.bat](scripts/run_daily_paper_pipeline.bat), step [1/4]:
  `python vericekici_updated.py --universe all --feed iex --adjustment all --daily-start 2015-01-01 --intraday-start 2020-07-01 --end today --output-dir hisseler --refresh-latest --parallel --workers auto --download-workers 4 --aggregation-workers auto`
- `--end` defaults to `datetime.now(timezone.utc).date().isoformat()` ([testdata/vericekici_updated.py:1750](testdata/vericekici_updated.py)) and `--refresh-latest` triggers incremental (not full historical) download.
- Same script continues into `universe_scanner.py --paper-report --make-chatgpt-report` (step 2) and `--execution-preview --pre-submit-check` (step 3) — i.e., fresh-download → report → execution-preview already runs end-to-end in one script.
- Caveat: this was last actually **run** 2026-07-08/09 (manifest mtimes; `chatgpt_master_report.md` last generated 2026-07-09 13:58) — a week before this audit — not because the capability is broken, but because the last 5 days of work (2026-07-10 → 07-13, per commit log and `rs_v2_*` report timestamps) went into the `rs_v2_clean` data-migration research track instead of re-running the daily pipeline. `docs/PROJECT_STATUS_CURRENT.md` itself is stale: it claims *"latest data date: daily=2026-06-18"*, nearly 3 weeks behind the actual manifest date of 2026-07-07 — that doc has not been updated in step with the data.

---

## 3. Pipeline Component Status

All modules below live in `testdata/` (despite the directory's name, this is confirmed to be the real, actively-developed pipeline code — not disposable fixtures — by cross-referencing `docs/PROJECT_STATUS_CURRENT.md`'s own description of it as the working directory).

| Component | Status | Main module(s) | Tests |
|---|---|---|---|
| Downloader | Exists | [testdata/vericekici_updated.py](testdata/vericekici_updated.py) (Alpaca-only, argparse CLI) | Indirect only — no `test_vericekici*.py`; covered piecemeal via `tests/test_downloader_manifest.py`, `tests/test_rs_v2_*` |
| Validator | Exists, partial/dispersed | `testdata/data_policy_review.py`, `data_policy_repair.py`, `data_policy_exceptions.py`, `no_lookahead_audit.py` — no single canonical validator class | `tests/test_data_policy_review.py`, `tests/test_no_lookahead_audit.py`, `tests/test_data_precheck_actions.py` |
| Feature pipeline | Exists | [testdata/indicators.py](testdata/indicators.py) (ATR, Hurst, Supertrend, `add_market_regime`) | **None found** — no `test_indicators.py`, no test references to its functions |
| Strategy engine | Exists, stable per HANDOFF rules | `testdata/strategies/` package: `base.py`, `relative_strength_rotation.py` (main candidate), `donchian_breakout.py`, `dual_momentum.py`, `ma_slope_trend.py`, `cross_sectional_momentum.py`, `liquidity_filter.py`, `cost_model.py` | Partial — `tests/test_strategy_registry.py`, `tests/test_dynamic_liquidity_filter.py`, `tests/test_dynamic_transaction_cost.py`; no direct test of core RS-rotation signal logic |
| Portfolio construction | Exists | [testdata/portfolio_manager.py](testdata/portfolio_manager.py) (`PortfolioManager`, ~62KB) | Weak — referenced only inside `test_dynamic_liquidity_filter.py`, `test_broker_submit_safety.py`; no dedicated `test_portfolio_manager.py` |
| Risk engine | Exists, minimal | [testdata/risk_gate.py](testdata/risk_gate.py) — `evaluate_order_risk()`, order-side normalization, per-order block/warn only | `tests/test_risk_gate.py` — but no portfolio heat, ATR sizing, daily-loss-limit, or kill-switch logic anywhere in the repo (`grep` for these terms returns zero hits), matching HANDOFF's own "Risk Engine: Incomplete" self-assessment |
| Execution | Exists | [testdata/execution_engine.py](testdata/execution_engine.py) (order intents, idempotency, `run_execution_preview`), `execution_costs.py` | `tests/test_execution_engine.py`, `tests/test_execution_costs.py` |
| Broker layer | Exists, best-tested area | [testdata/broker_interface.py](testdata/broker_interface.py) (ABC) → `testdata/alpaca_paper_broker.py`; plus `broker_lifecycle.py`, `broker_ledger.py`, `broker_reconciliation.py`, `broker_submit_recovery.py`, `broker_cutover_baseline.py`. **Order submission is explicitly disabled** (`submit_order` blocked, no live/POST calls) | Well covered: `test_broker_lifecycle.py`, `test_broker_ledger.py`, `test_broker_reconciliation.py`, `test_broker_cutover_baseline.py`, `test_broker_submit_safety.py`, `test_alpaca_paper_submit_one.py`, `test_alpaca_submit_readiness.py` |
| Scheduler | Exists, partial | [bot.py](bot.py) uses the `schedule` library (`schedule.every().monday.at(...)`, polling loop) for a live Telegram cockpit. Separately, `scripts/*.bat` are meant for external OS-level scheduling (no cron/Task-Scheduler config found in-repo) | None for the `schedule` loop; `tests/test_operator_scripts.py` covers the `.bat`/operator scripts, not `bot.py` itself |
| Notifications | Exists, Telegram-only | [bot.py](bot.py): `send_telegram_message`, `send_telegram_document`, `send_master_report_file`, interactive approve/reject callbacks. No email/Discord/Slack | No dedicated test file |
| Reporting | Exists, stable | [testdata/reports.py](testdata/reports.py), `testdata/report_schemas.py`, orchestration in `testdata/runner.py` → `testdata/reports/latest/chatgpt_master_report.md` + ~150 other report artifacts per run | `tests/test_report_schemas.py`, `tests/test_report_paths.py`, `tests/test_master_report_sections.py` |

**Notable gap not called out by HANDOFF explicitly:** `bot.py` (scheduler + notifications) is a standalone script that is **not** invoked by `scripts/run_daily_paper_pipeline.bat` and is not listed as a core file in `docs/PROJECT_STATUS_CURRENT.md`'s own "Ana dosyalar" (main files) list. Scheduling and notification exist as code, but are not wired into the actual daily pipeline entry point.

---

## 4. Reproducibility Check

Most recent daily-pipeline report manifest: [testdata/reports/latest/run_manifest.json](testdata/reports/latest/run_manifest.json), `run_datetime: 2026-07-09 17:08:13`.

Fields present:
- `command`: `python .../testdata/universe_scanner.py --reports basic --workers 1 --run-cost-stress`
- `daily_data_dir` / `intraday_data_dir`: point at `testdata/hisseler/daily` and `testdata/hisseler/bars_130min` (the old/active dataset, confirming §2)
- `latest_daily_date` / `latest_130min_date`: both `2026-07-07`
- `schema_version`: `1`
- `symbols_loaded`: 200, `rows_loaded`: 886,776
- `strategy_metrics_changed: False`, `benchmark_changed: False` (regression-guard fields)
- `pipeline_status`, `regression_guard_status`, `data_policy_status`, `data_precheck_status`: all `not_generated` for this particular run (those sub-checks weren't invoked with this command)

**What's missing from this manifest, relative to what HANDOFF requires ("dataset ID, feature version, strategy version"):** there is no explicit `dataset_id`, no `feature_version`, and no `strategy_version` field. Traceability instead relies on: the data directory path + `latest_daily_date`/`latest_130min_date` (implicit dataset identity), and separately the fact that `strategy_metrics_changed`/`benchmark_changed` are boolean regression flags rather than version numbers. Contrast this with the newer `rs_v2_clean` acquisition manifest (`acquisition_manifest.json`), which *does* carry `dataset_schema_version: "2.0.0"`, `downloader_source_commit`, and per-file SHA256 — i.e., the new research track has better provenance discipline than the manifest the live daily pipeline actually produces.

On paper, the chain is: `testdata/hisseler/` (dated 2026-07-07) → `universe_scanner.py --reports basic` (2026-07-09 17:08) → `chatgpt_master_report.md`. This is traceable back to a specific data snapshot and command, but **not** to an explicit strategy-code version or feature-pipeline version identifier — reproducing this exact report months from now would require knowing which git commit was checked out on 2026-07-09, which is not recorded in the manifest itself (only in `git log`).

No re-run was performed — this section is a provenance-chain read only, per instructions.

---

## 5. Test Suite

Run via the project's own runner: `python testdata/run_test_suite.py` (as invoked by [scripts/run_tests.bat](scripts/run_tests.bat)), inside a scratch venv with `requirements.txt` + `pytest` + `tabulate` installed (none of this touched the repo itself).

**Result: 591 tests, all PASS.**

```
Ran 591 tests in 247.551s
OK
```

One dependency gap surfaced on the first run (before `tabulate` was installed): `tests/test_rs_v2_clean_data.py::test_dry_run_uses_temp_output_and_never_reads_credentials_or_constructs_client` failed with `ModuleNotFoundError: No module named 'tabulate'`, because `testdata/vericekici_updated.py`'s `write_v2_dry_run_plan()` calls `pandas.DataFrame.to_markdown()`, which requires the optional `tabulate` package. **`tabulate` is not listed in [requirements.txt](requirements.txt)** — this is a real, minor environment-setup gap (not a code bug): a clean install per `requirements.txt` alone would fail this one test. After adding `tabulate`, the full suite is green.

All 591 tests were run within a single `unittest` process using temp directories for report output (visible in the log as `/tmp/tmpXXXXXX/...` paths) — confirmed no writes landed inside the actual repo (`git status` file count unchanged before/after).

---

## 6. Gaps for Decision Support System

Based on the above, what's missing to run **fresh data → validation → features → strategy → portfolio → risk → BUY/SELL report → notification** automatically each trading day:

1. **Dataset cutover decision.** Two datasets exist in parallel: the active `testdata/hisseler/` (has the 4 known session gaps) and the new `rs_v2_clean` (gap-recovered, better provenance, not yet wired into the daily pipeline or into `strategy`/`portfolio` code paths). Nothing downstream currently reads from `rs_v2_clean`. This needs an explicit decision + cutover, not silent dual-maintenance.
2. **No scheduler triggers the daily pipeline automatically.** `scripts/run_daily_paper_pipeline.bat` exists and works end-to-end (§2), but nothing in the repo invokes it on a schedule — no cron, no Windows Task Scheduler config, no `schedule`-library wiring to it. `bot.py`'s `schedule` loop exists but drives a separate Telegram cockpit, not this pipeline.
3. **Notifications aren't wired to the daily pipeline.** `bot.py` can send Telegram messages, but `run_daily_paper_pipeline.bat` doesn't call it — a human currently has to remember to open Telegram/pull `chatgpt_master_report.md` manually. There's no "pipeline finished → push summary" hook.
4. **Risk Engine is order-level only.** `testdata/risk_gate.py` blocks/warns individual orders; there is no portfolio heat, ATR-based position sizing, daily/weekly loss limits, or kill switch anywhere in the codebase (confirmed via repo-wide grep) — exactly the gap HANDOFF itself flags as "Very High" priority.
5. **Manifest/report provenance lacks explicit strategy & feature version fields** (§4) — a prerequisite for HANDOFF's "every report must be traceable" principle to actually hold once daily automation starts producing dozens of reports a week unattended.
6. **`requirements.txt` is missing `tabulate`**, a hard dependency of the downloader's dry-run report path — trivial to fix, but would break a clean automated setup today.
7. **Operational staleness has no alarm.** `docs/PROJECT_STATUS_CURRENT.md` was ~3 weeks stale relative to the actual data (§2) with nothing to flag that drift; an automated daily system needs a freshness/staleness check (e.g., "data is N days old, pipeline hasn't run since X") independent of manually maintained status docs.
8. **Feature pipeline (`testdata/indicators.py`) has zero test coverage** — risky to lean on inside an unattended daily loop without regression protection.
9. **No single validator entry point.** Validation logic for "fail-closed" is spread across `data_policy_review.py` / `data_policy_repair.py` / `no_lookahead_audit.py` / the `v2_*_audit.py` scripts, with different scripts for the old vs. new dataset. A daily automated chain needs one validation gate that both datasets converge on.
10. **Everything is Windows-shaped.** All entry points are `.bat`/`.ps1` (`scripts/*.bat`), hardcoded Windows paths appear inside data (e.g. `C:\Users\polat\...` in manifest CSVs and `run_manifest.json`). Running this unattended (e.g., on a server/cron) will require either a Linux/cross-platform entry point or a scheduled Windows task — currently neither exists.

None of the above required or involved touching strategy logic, ranking, or benchmark code, per HANDOFF's "DO NOT" rules.
