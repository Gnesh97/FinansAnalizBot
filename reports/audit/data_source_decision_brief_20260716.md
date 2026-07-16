# Data Source Decision Brief — Phase 1.5 — 2026-07-16

Follow-on to [cutover_readiness_20260715.md](cutover_readiness_20260715.md) (Phase 1: NO-GO on cutting the daily pipeline over to `rs_v2_clean`, because its strict validator fails). This phase gathers evidence to inform what happens next. No cutover, no strategy/ranking/benchmark/validation code change, no write to `hisseler/` or `rs_v2_clean_market_data/`. All new artifacts are new files; nothing was overwritten.

**Headline finding: the current production dataset (`hisseler/`) has the same coverage failure, at the same severity, as `rs_v2_clean`.** This was not previously measured — nobody had pointed the coverage-forensics tooling at `hisseler` until this task. Separately, a bounded SIP canary shows the Alpaca account already has working entitlement to SIP historical data, and SIP returns non-zero rows for all 4 previously "unrecoverable" IEX gap sessions.

---

## TASK A — hisseler coverage forensics

**Tooling reuse, not modification.** `testdata/v2_coverage_forensics.py::audit_v2_run()` and `testdata/v2_session_coverage.py` turned out to be dataset-agnostic underneath — they only need a manifest shaped like `{target_start_date, target_end_date, files: [{symbol, timeframe, output_relative_path, asset_role, ...}]}` and CSV files with a `Date` column; nothing in the coverage math itself checks `dataset_profile` or a frozen contract hash (that stricter checking lives in a separate function, `validate_v2_manifest_and_files()`, which I did not touch or invoke against hisseler). So the fail-closed "stop TASK A" clause did not trigger — instead I wrote [scripts/hisseler_coverage_forensics_adapter.py](../../scripts/hisseler_coverage_forensics_adapter.py), a new, thin, read-only adapter that:
- Reads `testdata/hisseler/manifest/download_manifest.csv` to enumerate hisseler's 200 symbols.
- Builds an in-memory v2-shaped manifest whose `output_relative_path` entries are **absolute paths** pointing directly at the real files under `testdata/hisseler/daily|raw_5min|bars_130min/` (pathlib resolves `root / <absolute path>` to the absolute path regardless of `root`, so no file under `hisseler/` needed to be touched or copied).
- Writes that synthetic manifest only under a new `reports/audit/hisseler_coverage_forensics_20260716/_adapter_workdir/` directory, then calls the unmodified `audit_v2_run()`.
- Same target window as `rs_v2_clean` (2020-07-27 → 2026-07-07) for apples-to-apples comparison.

Full output: [reports/audit/hisseler_coverage_forensics_20260716/hisseler_coverage_forensics.md](hisseler_coverage_forensics_20260716/hisseler_coverage_forensics.md) (plus the underlying gap-detail/frequency/intraday-block/decision-path/boundary CSVs in the same directory).

### hisseler true coverage status

- `daily_coverage_valid_count`: **71 of 200** (35.5%)
- `intraday_coverage_valid_count`: **0 of 200** (0%) — identical failure rate to `rs_v2_clean`
- `affected_rebalance_decision_count`: **3,445**
- `exception_approved_count`: 0

### The 4 known dates — missing in hisseler too

| Date | hisseler symbols affected | rs_v2_clean symbols affected |
|---|---:|---:|
| 2021-04-19 | 122 | 122 |
| 2021-10-25 | 108 | 108 |
| 2022-03-08 | 119 | 119 |
| 2022-04-06 | 1 | 1 |

Identical counts (hisseler's 22 extra candidate symbols beyond `rs_v2_clean`'s 178 don't touch these 4 dates any differently). Classification: **EXPECTED / shared-provider** — both datasets pull from the same Alpaca IEX feed and inherit the same provider-level hole.

### PROVIDER_WIDE_PATTERN dates — missing in hisseler too

- **2025-03-10**: full intraday session missing for 200/200 hisseler symbols (100%), including QQQ and SPY — identical in kind to `rs_v2_clean`'s 180/180 (100%).
- **2024-12-23**: two intraday blocks missing for 200/200 hisseler symbols, including QQQ and SPY — identical in kind to `rs_v2_clean`'s 180/180.

Classification: **EXPECTED / shared-provider**, same as above.

### Direct coverage comparison table (completes deferred Phase-1 TASK C, coverage level)

| Metric | hisseler (200 symbols) | rs_v2_clean (178 candidates + 2 benchmarks) | Classification |
|---|---:|---:|---|
| Daily coverage valid | 71 (35.5%) | 55 (30.6%) | EXPECTED — hisseler slightly better, larger universe |
| Daily coverage failed | 129 | 125 | EXPECTED |
| Intraday coverage valid | 0 (0%) | 0 (0%) | EXPECTED — identical |
| Intraday coverage failed | 200 | 180 | EXPECTED |
| Affected rebalance decisions | 3,445 | 3,304 | EXPECTED — hisseler slightly higher, larger universe |
| QQQ decision-path impact | `{DAILY_FEATURE_INPUT_GAP:6, EXECUTION_OPEN_MISSING:1, SIGNAL_BAR_MISSING:1, UNAFFECTED:65}` | same shape `{...UNAFFECTED:65}` | EXPECTED — identical |
| SPY decision-path impact | `{DAILY_FEATURE_INPUT_GAP:6, EXECUTION_OPEN_MISSING:1, UNAFFECTED:66}` | same shape `{...UNAFFECTED:66}` | EXPECTED — identical |
| 2021-04-19 / 10-25 / 03-08 / 04-06 missing-symbol counts | 122/108/119/1 | 122/108/119/1 | EXPECTED — identical |
| 2025-03-10, 2024-12-23 provider-wide gaps | 200/200 symbols | 180/180 symbols | EXPECTED — identical (100% either way) |

**No REGRESSION and no UNEXPLAINED discrepancy found.** Every measured difference is fully explained by hisseler's slightly larger candidate universe (200 vs 178). The two datasets are, for practical purposes, coverage-equivalent — both fail the same strict bar, for the same underlying provider reason.

---

## TASK B — SIP entitlement canary

Script: [scripts/alpaca_sip_entitlement_canary.py](../../scripts/alpaca_sip_entitlement_canary.py). 10 GET-only requests (within the ≤12 budget), reusing the project's own `get_credentials()` loader (key/secret never printed or logged — only request parameters and results were). Same symbol/date pairs as the immutable Phase-1 IEX forensics evidence (`rs_v2_major_milestone_report.md`) for direct comparability, plus one control date. Raw results: [reports/audit/sip_entitlement_canary_20260716.json](sip_entitlement_canary_20260716.json).

| # | Symbol | Date | Timeframe | HTTP status | Error | Rows returned |
|---|---|---|---|---:|---|---:|
| 1 | ABBV | 2021-04-19 | 1Day | 200 | — | 1 |
| 2 | ABBV | 2021-04-19 | 5Min | 200 | — | 2 |
| 3 | ALB | 2021-10-25 | 1Day | 200 | — | 1 |
| 4 | ALB | 2021-10-25 | 5Min | 200 | — | 5 |
| 5 | ABBV | 2022-03-08 | 1Day | 200 | — | 1 |
| 6 | ABBV | 2022-03-08 | 5Min | 200 | — | 8 |
| 7 | WBD | 2022-04-06 | 1Day | 200 | — | 1 |
| 8 | WBD | 2022-04-06 | 5Min | 200 | — | 50 |
| 9 (control) | SPY | 2023-06-01 | 1Day | 200 | — | 1 |
| 10 (control) | SPY | 2023-06-01 | 5Min | 200 | — | 190 |

**Interpretation: case (a) — SIP historical is entitled and returns data for all 4 previously-empty target sessions.** Every request succeeded (HTTP 200, no `APIError`, no entitlement-denial text of any kind — there is no case-(c) verbatim error to quote because none occurred). This directly contradicts nothing from Phase 1: the IEX forensics found *IEX* returns zero rows for these exact sessions; this canary shows *SIP*, on the same account, does not.

**Caveat — the returned SIP coverage is much sparser than normal for 3 of the 4 dates.** The control day (SPY, an ordinary session) returned 190 5-minute rows. The 4 gap-date probes returned only 2, 5, 8, and 50 rows respectively (WBD's 50 is closest to normal; ABBV's 2-row and ALB's 5-row results are not what a full regular session looks like for large/mid-cap, actively-traded names). This is **not** the zero-row provider hole IEX showed, but it is also not yet demonstrated to be a *complete* session. Before treating this as "gap resolved," a full-session pull (not just this bounded canary) would need to confirm whether these row counts reflect the entire trading session or only a partial one.

---

## Is the CURRENT daily pipeline (on hisseler) affected by the same gaps? — Honest assessment

**Yes, quantifiably, using the exact selection-impact methodology already in the repo** (`testdata/v2_daily_gap_selection_audit.py`'s decision-path classification, reused unmodified in TASK A's own output):

- 3,445 of the candidate×rebalance-date decision points show a non-`UNAFFECTED` status on `hisseler` right now (`DAILY_FEATURE_INPUT_GAP`, `SIGNAL_BAR_MISSING`, or `EXECUTION_OPEN_MISSING`) — this is the same live dataset `scripts/run_daily_paper_pipeline.bat` refreshes and feeds into `universe_scanner.py` every day.
- The benchmark symbols the strategy is evaluated against are themselves affected: **QQQ is non-`UNAFFECTED` on 8 of 73 monthly rebalance dates (11.0%)**, **SPY on 7 of 73 (9.6%)** — `DAILY_FEATURE_INPUT_GAP` (6 dates each) plus one `SIGNAL_BAR_MISSING` (QQQ) / `EXECUTION_OPEN_MISSING` (both).
- This was true before Phase 1.5 started — the gaps are not new, and are not specific to the `rs_v2_clean` migration effort. They simply had not been measured against `hisseler` with this tooling until this task. The daily pipeline has been producing decisions with this exposure the whole time; Phase 1's NO-GO was correct on `rs_v2_clean`'s own merits, but it should not be read as "hisseler is the safe, clean baseline" — it has the identical problem.

---

## Options matrix (facts only — no cost/purchasing advice)

### Option 1 — Use Alpaca SIP for the affected sessions
- **Entitlement status**: already confirmed working on the current account/credentials (TASK B, 10/10 requests succeeded).
- **What it requires technically**: `vericekici_updated.py` already supports `--feed sip` as a CLI choice (`parser.add_argument("--feed", choices=["iex", "sip"], ...)`) — no new provider integration needed. Backfilling the 4 known dates (and investigating 2025-03-10 / 2024-12-23) would mean re-running the existing gap-recovery path (`testdata/v2_provider_recovery.py`, `GAP_RECOVERY_APPROVED_DATES`) with `feed=sip` instead of `feed=iex` for just the affected symbol/date pairs, then re-validating.
- **Open question**: whether SIP returns a *complete* session for these dates or only partial data (see caveat above) — needs a full, non-bounded pull to confirm before relying on it.
- **Schema gap**: none of this changes `validate_v2_manifest_and_files()`'s hardcoded `"feed": "iex"` exact-match requirement (line ~202 of `vericekici_updated.py`) — using SIP data would currently fail that check by design, and would need an explicit, deliberate contract change (not attempted here — out of scope, flagged for a future authorized task).

### Option 2 — Alternative data provider
- Per `testdata/reports/latest/rs_v2_data_source_migration_initial_state.md` (2026-07-13 inventory, still current): Polygon, Databento, Finnhub, Tiingo, Twelve Data, EODHD credentials are **NOT_PRESENT** in `.env`. Alpha Vantage credential **is present**, and has been used for delisted-symbol metadata research (`testdata/research_data/alpha_vantage_delisted.csv`, `alpha_vantage_metadata.json`) — but not proven as a full OHLCV backfill substitute, and the `alpha_vantage` Python library is **not installed**.
- **What it requires technically**: since no `MarketDataProvider` abstraction exists in the codebase (confirmed in the prior repo-status audit — the downloader is hard-coded to Alpaca via `_load_alpaca()`), integrating any alternative provider means writing new adapter code, not a config flag. Larger scope than Option 1.

### Option 3 — Formal coverage exceptions
- `testdata/baselines/rs_v2_clean_coverage_exceptions.json`'s schema (`v2_session_coverage.py::load_reviewed_coverage_exceptions`) only supports `exception_type` values `"LATE_START"` and `"EARLY_TERMINAL"` — **boundary** gaps only.
- The 4 known dates and the 2025-03-10 / 2024-12-23 provider-wide gaps are all classified `MISSING_INTERNAL_SESSION` — **internal**, mid-range gaps, which the exception schema has no mechanism to approve at all, even if a human reviewer wanted to accept them as-is.
- **What it requires technically**: extending the exception schema/validator to support an internal-gap exception type — a validation-logic code change, explicitly out of scope for this task and any prior one.

---

## Summary for the next decision

- `rs_v2_clean` is not uniquely broken — `hisseler` has the same coverage failure, now measured for the first time.
- The specific blocker from Phase 1 (IEX returning zero rows for the 4 known dates) has at least a partial technical path forward: SIP is already entitled and returns non-zero data, though completeness is unconfirmed.
- None of the three options are "free" — Option 1 needs a full-session SIP re-pull plus a deliberate contract-schema decision; Option 2 needs new adapter code; Option 3 needs a validation-schema extension. All are implementation tasks, correctly deferred pending an explicit decision on which path to pursue.
