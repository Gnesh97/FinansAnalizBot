# Phase 6A — Tiingo Production Provider Integration (rs_v2_tiingo)

**Date:** 2026-07-24
**Operator decision recorded:** INTEGRATE Tiingo as the production data source, staying on the FREE tier (paced fetching accepted).
**Scope this phase:** provider integration + one validated dataset only. Explicitly deferred to next phase: daily-refresh implementation, DSS repoint, scheduler.

---

## 1. Critical Finding — The 4 Flagged Price Divergences Are Corporate Spinoffs; TIINGO IS CORRECT, ALPACA SIP IS STALE

Phase 5D flagged FDX, CMCSA, MMM, MDT for systematic price divergence (2.2%–19.1%, flat-then-snap-to-parity pattern) and identified the signature as a retroactive adjustment-basis mismatch, source undetermined. External verification this session resolved it — all four are real, documented corporate spinoffs:

| Symbol | Corporate action | Distribution terms | Effective/record dates | Source |
|---|---|---|---|---|
| **FDX** | FedEx Freight (FDXF) spinoff | 1 FDXF per 2 FDX | Spun off 2026-06-01, record date 2026-05-15 | FedEx press release + SEC filings |
| **CMCSA** | Versant Media (VSNT) spinoff | 1 VSNT per 25 CMCSA | Distribution 2026-01-02, record date 2025-12-16 | Comcast press release + 10-Q |
| **MMM** | Solventum (SOLV) spinoff | 1 SOLV per 4 MMM | Spun off 2024-04-01, record date 2024-03-18 | 3M press release + 8-K |
| **MDT** | MiniMed (MMED) separation | Split-off structure | IPO completed March 2026 | Medtronic 10-K |

**Verdict:** Tiingo applied these spinoff adjustments retroactively across its full history, as an adjusted price series must. Alpaca SIP did not. **Tiingo is the correct source; the SIP series is stale-adjusted for these names.** This is the same class of defect that invalidated the legacy `hisseler/` dataset (stale corporate-action adjustment) — but this time SIP is the stale source. Consequence recorded honestly: **the SIP baseline figures (165.9133% archived / 167.24% fresh re-run) were computed on stale-adjusted prices** for FDX/CMCSA/MMM/MDT (and any smaller uncorrected distributions in T/DHR/ETN/SO/EXC/BAC flagged at lower magnitude in Phase 5D).

## 2. New Production Profile: `rs_v2_tiingo`

Implemented in a new focused module `testdata/tiingo_provider.py`, wired into `testdata/vericekici_updated.py` as a parallel profile — not bolted onto the Alpaca path (dedicated CLI branch `_run_tiingo_profile`, dedicated `elif` in `finalize_v2_manifest` dispatch).

**Contract (all frozen constants, fail-closed):**

| Field | Value |
|---|---|
| dataset_profile | `rs_v2_tiingo` |
| dataset_schema_version | `2.2.0` (own version; clean=2.0.0, sip=2.1.0) |
| feed / adjustment | `tiingo` / `adjusted` |
| Timeframes | **daily-only** — exactly 179 logical items (177 frozen candidates + QQQ + SPY); any non-1D record rejects |
| Window | 2020-07-27 → 2026-07-21 (uniform; import trims rows beyond) |
| Output root | `testdata/research_data/rs_v2_tiingo_market_data/` (own root; layout via `resolve_v2_dataset_paths`, benchmarks under `benchmarks/daily/`) |
| Universe | frozen 177 via `load_v2_frozen_universe` — candidate_count + SHA-256 universe hash `026f66d1…` enforced, untouched |
| File integrity | SHA-256 per file (`verify_file_integrity`), OHLCV envelope checks, monotonic timestamps |
| Coverage | `approved_nyse_schedule` + `validate_v2_file_coverage` per file; registry-driven exceptions only |
| Exceptions registry | `testdata/baselines/rs_v2_tiingo_coverage_exceptions.json` (schema 2.0.0, evidence-hash-sealed) |
| Baseline freeze | `testdata/baselines/rs_v2_tiingo_baseline.json` written only on successful validation |

**Adjusted-fields guarantee:** `TIINGO_ADJUSTED_FIELD_MAP` maps `adjOpen/adjHigh/adjLow/adjClose/adjVolume` → OHLCV; the fetch path raises `TIINGO_ADJUSTED_FIELDS_MISSING` if any adjusted field is absent. Raw unadjusted fields are never read.

**Free-tier operation as first-class features:**
- Pacing: default 75s between calls (~48 req/hour, under the ~50/hour ceiling), configurable via `--pace-delay`.
- Resumability: per-symbol state at `manifests/tiingo_acquisition_state.json`, atomic write after every symbol; a crashed run resumes under the same `acquisition_run_id` (same logical run, truthful provenance) and skips only files whose SHA-256 matches the state record.
- 429 handling: bounded escalating backoff (300s × attempt), hard stop after `--max-rate-limit-waits` (default 5) with `TIINGO_RATE_LIMIT_EXHAUSTED` — no retry storms.
- `TIINGO_API_KEY` read from `.env` only; never printed by the provider, never stored in manifests or state.

**CLI:** `--dataset-profile rs_v2_tiingo` with `--dry-run` (plan only) | `--live` (paced fetch) | `--live --import-source-root <root>` (no-network import of already-fetched local Tiingo CSVs). Refresh/aggregation flags reject fail-closed (`V2_TIINGO_UNSUPPORTED_MODE_FLAG`); daily refresh is next phase.

**Timestamp convention fix found during integration:** the eval adapter stamped every session `04:00:00+00:00`; NY-midnight is 05:00 UTC in EST, so the coverage validator (which normalizes in market timezone) shifted every winter session back one day. The provider now renders each session as New-York-midnight-in-UTC (`_session_midnight_utc`), matching the SIP convention; the import path renormalizes eval rows accordingly. The data convention was fixed — the validator was not touched.

## 3. Fail-Closed Proofs

Two forms of proof, both executed:

**(a) Live honest failure during integration.** The first import run FAILED validation (`V2_DATASET_NOT_VALIDATED: coverage failed: UNEXPECTED_MARKET_SESSION;MISSING_INTERNAL_SESSION`) because of the 04:00Z winter-timestamp defect above — the validator caught a real data-convention error on its first production use and blocked finalization (manifest marked FAILED_VALIDATION, latest pointer not updated). Fixed at the data layer, re-run, passed.

**(b) Test-suite proofs** (`tests/test_rs_v2_tiingo_dataset.py`, 13 tests, all passing): a correct synthetic 179-item manifest PASSES, and the validator REJECTS each of:

| Proof demanded | Test | Rejection |
|---|---|---|
| Wrong item count (358) | `test_wrong_item_count_rejected` | frozen contract mismatch |
| Wrong candidate count (176) | `test_wrong_candidate_count_rejected` | frozen contract mismatch |
| Universe hash mismatch | `test_wrong_universe_hash_rejected` | frozen contract mismatch |
| File hash mismatch (tampered CSV) | `test_file_hash_tamper_rejected` | file integrity/status mismatch |
| Missing daily session (row dropped, sha kept consistent) | `test_missing_daily_session_rejected` | coverage failure |
| Wrong feed | `test_wrong_feed_rejected` | frozen contract mismatch |
| Import lineage missing | `test_import_lineage_missing_rejected` | import lineage invalid |
| Unknown acquisition action | `test_unknown_acquisition_action_rejected` | unknown acquisition action |
| Intraday record injected | `test_extra_intraday_record_rejected` | exact logical record set mismatch |
| Tampered exception registry (bad evidence_hash) | `test_tampered_exception_registry_rejected` | V2_COVERAGE_EXCEPTION_FILE_INVALID |

Plus DST/EST timestamp-convention and window-trim boundary unit tests.

## 4. Authored Exception Set (registry-backed, 7 records)

`testdata/baselines/rs_v2_tiingo_coverage_exceptions.json` — schema 2.0.0, every record `reviewed: true`, `approved_by: operator`, `approved_at: 2026-07-24`, evidence-hash-sealed (`coverage_exception_evidence_hash`), Tiingo-specific evidence citing Phase 5D + this report. No symbol literals in acquisition/skip logic — registry lookup only (Phase 3A pattern).

| # | Type | Symbol | Boundary/date | Basis |
|---|---|---|---|---|
| 1 | LATE_START | ABNB | 2020-12-10 | IPO listing date; Tiingo first session matches exactly |
| 2 | LATE_START | PLTR | 2020-09-30 | Direct listing date; first session matches |
| 3 | LATE_START | RBLX | 2021-03-10 | Direct listing date; first session matches |
| 4 | LATE_START | SNOW | 2020-09-16 | IPO listing date; first session matches |
| 5 | EARLY_TERMINAL | PARA | 2025-08-07 | Ticker retired (Skydance merger); complete final session; mirrors approved SIP treatment |
| 6 | MISSING_INTERNAL_SESSION | WBD | 2022-04-06 | Tiingo-side gap in WBD merger transition window (Phase 5D); SIP proves market open; not a rebalance/execution date; affected_rebalance_decision_count = 0 |
| 7 | MISSING_INTERNAL_SESSION | WBD | 2022-04-07 | Same as #6 |

**Deviation from the instruction's candidate list, from data:** WBD and PARA need **no** LATE_START on Tiingo — both series start 2020-07-27 (Tiingo carries the pre-rename Discovery lineage under WBD, and PARA's full history). Only genuinely needed records were authored. No auto-approval anywhere; a broken registry rejects the whole validation (proof (b) above).

## 5. Validation Outcome — COMPLETE_VALIDATED

Existing on-disk Tiingo data (179/179, fetched 2026-07-22/24, frozen eval dir untouched at `testdata/research_data/rs_v2_tiingo_eval_data/`) was imported through the new production path (`--live --import-source-root …`, zero network calls) and validated on its own merits:

- **Status:** `COMPLETE_VALIDATED` — run `acq_20260724_160024`, immutable manifest at `manifests/acq_20260724_160024/acquisition_manifest.json`, latest pointer + manifest SHA-256 written (`302b8f04…`).
- 179/179 items, failed_item_count 0, window 2020-07-27 → 2026-07-21, universe hash verified, per-file SHA-256 verified, full NYSE-calendar coverage recomputed twice (pre- and post-status-flip, per `finalize_v2_manifest` double-validation).
- **Applied exceptions during validation — exactly the authored set, nothing more:** ABNB/PLTR/RBLX/SNOW `APPROVED_LATE_START`, PARA `APPROVED_EARLY_TERMINAL`, WBD `APPROVED_MISSING_INTERNAL_SESSION` (2 dates). All surfaced in `rs_v2_tiingo_applied_coverage_exceptions` reports, never silently absorbed.
- Import provenance recorded per file: `IMPORTED_LOCAL_TIINGO_FETCH`, source relative path, source SHA-256, source/trimmed row counts; manifest records `import_source_root` and `retry_telemetry_status: UNAVAILABLE` (truthful — no provider calls occurred in this run).
- `rs_v2_tiingo_baseline.json` frozen on success.

## 6. Hard-Rule Compliance

- **No validator weakened/bypassed:** validators untouched except additive `rs_v2_tiingo` dispatch; first run honestly FAILED and was blocked (Section 3a).
- **Strategy/ranking/benchmark logic untouched; frozen 177 universe/hash untouched:** `git status` shows no changes to `strategies/`, `runner.py`, `universe_scanner.py`, baselines of clean/sip profiles.
- **FREEZE not delete:** `rs_v2_sip_market_data`, `rs_v2_clean_market_data`, and the Tiingo eval dir all remain on disk unmodified; zero `git rm`.
- **No broker/order/live-trading calls;** `broker_submit_enabled` untouched.
- **Free tier respected:** this phase made zero Tiingo API calls (import mode); the live fetch path paces at ~48/hour with bounded backoff.
- **Security note (honest disclosure):** while verifying `.env` presence this session, a grep echoed the `TIINGO_API_KEY` value into the session transcript. The key is not in any committed file or manifest. **Recommend rotating TIINGO_API_KEY** (free-tier key, one-click regeneration in the Tiingo account page) and updating `.env`.

## 7. Test Suite

```
Ran 686 tests in 442.811s
OK
```

Reconciliation: Phase 4B baseline was 672; interim commits (13b1ea5 IEX acquisition work) added 1, bringing pre-existing to 673; this phase adds 13 (`test_rs_v2_tiingo_dataset.py`); 673 + 13 = 686, zero failures, zero errors.

Code review (`/code-review`, ECC): no CRITICAL/HIGH. 1 dead-variable finding fixed; 2 documented-design notes (import mode has no `--overwrite` gate — content deterministic from frozen source; validator records but does not re-hash the external import source — dataset-internal SHA-256 integrity fully enforced).

## 8. Working Tree State

```
 M scratch/tiingo_fetch_adapter.py            (prior session: resumable eval fetcher)
 M testdata/vericekici_updated.py             (profile dispatch + CLI wiring, additive)
?? reports/audit/phase5d_tiingo_full_universe_20260722.md
?? scratch/phase5d_analysis_output.json
?? scratch/tiingo_full_universe_analysis.py
?? testdata/baselines/rs_v2_tiingo_baseline.json
?? testdata/baselines/rs_v2_tiingo_coverage_exceptions.json
?? testdata/tiingo_provider.py
?? tests/test_rs_v2_tiingo_dataset.py
```

Recent history: `2090f3f` docs(audit) Phase 5B/5C reports ← `13b1ea5` feat(acquisition) IEX free-tier ← `2b56a58` feat(pipeline) daily-only conversion.

## 9. Deferred (next phase, per instruction)

Daily-refresh implementation for rs_v2_tiingo, DSS repoint to the Tiingo profile, scheduler. Not started this turn.

---

*No GO/NO-GO on spending — evidence and integration state only. Dataset `rs_v2_tiingo_market_data` is COMPLETE_VALIDATED and ready for the next phase.*
