# Phase 2A — SIP Migration Code Foundations — 2026-07-16

Follow-on to [sip_residual_impact_20260716.md](sip_residual_impact_20260716.md). Operator-authorized scope for this task: (1) extending the coverage-exception schema for internal-session exceptions, (2) creating a new SIP dataset contract/profile. No other validation, strategy, ranking, or benchmark logic was changed. `hisseler/` and `rs_v2_clean_market_data/` remain untouched; `rs_v2_clean`'s frozen baseline, exceptions file, and manifest contract were not edited. **This is code foundations only — no full SIP dataset acquisition was performed.**

---

## TASK A — Exception schema v2

### What changed

[testdata/v2_session_coverage.py](../../testdata/v2_session_coverage.py):

- `load_reviewed_coverage_exceptions` now accepts `schema_version` **`1.0.0` or `2.0.0`**. Under `1.0.0` only `LATE_START`/`EARLY_TERMINAL` are allowed (unchanged). Under `2.0.0`, a new type `MISSING_INTERNAL_SESSION` is additionally allowed, with mandatory fields `exception_type`, `reviewed`, `evidence_hash`, `affected_date`, `affected_symbols` (`"ALL"` or an explicit list), `timeframes_affected`, `rationale`, `evidence_reports`, `residual_impact_statement`, `approved_by`, `approved_at`. Any record missing a mandatory field, unreviewed, or with a tampered `evidence_hash` is rejected — fail-closed, same discipline as the existing `LATE_START`/`EARLY_TERMINAL` types.
- `calculate_v2_session_coverage` now cross-references approved `MISSING_INTERNAL_SESSION` exceptions against the specific `(symbol, date, timeframe)` combination it independently detects as missing. A match suppresses the `MISSING_INTERNAL_SESSION` failure reason **only for that exact combination** — it can never fabricate the absence of a gap that isn't actually there. `coverage_status` becomes `VALID` when that's the only failure. A new reason token `APPROVED_MISSING_INTERNAL_SESSION` is added (consistent with the existing `APPROVED_*` convention already used for `LATE_START`/`EARLY_TERMINAL`), and a new field `approved_internal_session_exception_dates` lists exactly which dates were suppressed. That field is deliberately **not** added to `COVERAGE_FIELDS`, so it never participates in the strict manifest-vs-recomputed equality gate used by `validate_v2_file_coverage`/`validate_v2_manifest_and_files` — it's additional, informational, and cannot be used to smuggle a mismatch past the existing contract check.
- New `write_v2_applied_exceptions_report()` in [testdata/vericekici_updated.py](../../testdata/vericekici_updated.py) scans coverage records for any `APPROVED_*` reason (all three types: `LATE_START`, `EARLY_TERMINAL`, `MISSING_INTERNAL_SESSION`) and writes `rs_v2_applied_coverage_exceptions.{md,csv}` listing every symbol/timeframe/date an approval actually covered, with `exceptions_silently_absorbed: false` stated explicitly. This is new, additive — it does not touch the existing `rs_v2_clean_coverage_review_required.*` report (which lists *unapproved* exceptions needing review; a different, pre-existing concern).

**Byte-for-byte regression guarantee:** `rs_v2_clean`'s exceptions file (`testdata/baselines/rs_v2_clean_coverage_exceptions.json`) is untouched and still declares `schema_version: "1.0.0"` with an empty `exceptions` list. Since no `MISSING_INTERNAL_SESSION` exceptions exist for that file, the new matching logic is a no-op for every existing rs_v2_clean call site — proven by `tests/test_rs_v2_session_coverage.py`'s full 31-test suite passing unchanged (see Test counts below).

### Exception records created

New file [testdata/baselines/rs_v2_sip_coverage_exceptions.json](../../testdata/baselines/rs_v2_sip_coverage_exceptions.json), `schema_version: "2.0.0"`, four records — **intraday (`130min`) only, daily is not excepted**:

| affected_date | affected_symbols | timeframes_affected | approved_by | approved_at |
|---|---|---|---|---|
| 2021-04-19 | ALL | [130min] | operator | 2026-07-16 |
| 2021-10-25 | ALL | [130min] | operator | 2026-07-16 |
| 2022-03-08 | ALL | [130min] | operator | 2026-07-16 |
| 2022-04-06 | ALL | [130min] | operator | 2026-07-16 |

`affected_symbols: "ALL"` is a deliberate, evidence-bounded choice, not an unqualified claim: the exception mechanism only ever suppresses a gap the coverage calculation independently detects, so it has zero effect on any symbol that turns out to have complete data on these dates. The SIP full-session probe (Phase 1.6) tested one symbol per date and found severe-to-meaningful partial coverage (2/78 to 49/78 expected bars); the original IEX gap-recovery work documented up to 123 affected symbols across these 4 dates. `"ALL"` is the honest, conservative choice given that evidence — it grants no more than what a genuinely-detected gap requires.

Each `rationale` cites `scripts/alpaca_sip_full_session_probe.py` and the two evidentiary reports; each `residual_impact_statement` cites `sip_residual_impact_20260716.md`'s finding — 0 affected rebalance decisions, since none of the 4 dates is a rebalance signal date or next-open execution date, and the ranking engine is code-traced to depend exclusively on daily OHLCV — while also honestly restating the one non-decision-affecting secondary nuance (the same-day mark-to-market backtest-simulation skip in `generate_relative_strength_rotation_signals`).

### Tests

New file [tests/test_v2_internal_session_exception.py](../../tests/test_v2_internal_session_exception.py), 16 tests:
- Schema acceptance (`2.0.0` loads `MISSING_INTERNAL_SESSION`) and rejection (missing each of the 8 mandatory fields individually; type not allowed under `1.0.0`; unreviewed; tampered `evidence_hash`; duplicate record) — all fail-closed.
- Exception application: an approved exception makes `coverage_status` `VALID` and populates `approved_internal_session_exception_dates`; scoping is verified independently on symbol (explicit list vs `"ALL"`, case-insensitive), timeframe (does not leak `130min` approval into `1D`), and date (does not apply to an adjacent date).
- **Regression test** (`test_unapproved_internal_gap_still_fails_exactly_as_before`): with no `MISSING_INTERNAL_SESSION` exception present, `missing_internal_session_count`, `coverage_status`, and `coverage_exception_reason` are identical to the pre-TASK-A contract.
- `write_v2_applied_exceptions_report`: applied exceptions are listed with `exceptions_silently_absorbed: false`; records without an approved reason produce zero rows.

All 16 pass. `tests/test_rs_v2_session_coverage.py`'s existing 31 tests also pass unchanged.

---

## TASK B — SIP dataset profile

### What changed, and what is provably unchanged

[testdata/vericekici_updated.py](../../testdata/vericekici_updated.py) `main()`: `is_v2` (previously `True` only for `rs_v2_clean`) is now `is_v2_clean or is_v2_sip`. The former `if is_v2:` mandatory-flag block was renamed to `if is_v2_clean:` **with its body untouched** — this rename was structurally required (otherwise a `rs_v2_sip` invocation would incorrectly fall into rs_v2_clean's exact-date/frozen-universe checks), not a behavior change. A new `elif is_v2_sip:` branch was added alongside it. The only other change inside the shared/reused code path is the dry-run item-count print, which switched from a hardcoded `"360"` to `len(plan_rows)` — for `rs_v2_clean` this is always 360 (178 fixed candidates + QQQ + SPY, × 2 timeframes), so the printed output is byte-identical; confirmed by the existing `test_dry_run_uses_temp_output_and_never_reads_credentials_or_constructs_client` test, unmodified, still passing.

New profile surface, additive only:

| | rs_v2_clean | rs_v2_sip |
|---|---|---|
| `--feed` | `iex` (required) | `sip` (required) |
| dataset_schema_version | `2.0.0` (unchanged) | `2.1.0` (`V2_SIP_DATASET_SCHEMA_VERSION`) |
| output root | `research_data/rs_v2_clean_market_data/` | `research_data/rs_v2_sip_market_data/` (`DEFAULT_V2_SIP_FROZEN_ROOT`) |
| frozen baseline | `baselines/rs_m126_top30_sma100_no_regime_v2_clean_stock.json` (real, 178-symbol, hash-pinned) | `baselines/rs_v2_sip_baseline.json` — **placeholder**, `status: "NOT_YET_ACQUIRED"`, `universe: null`; populated by a real acquisition in a later phase |
| coverage exceptions | `baselines/rs_v2_clean_coverage_exceptions.json` (schema 1.0.0, empty) | `baselines/rs_v2_sip_coverage_exceptions.json` (schema 2.0.0, the 4 records above) |
| target dates | fixed: `2020-07-27` → `2026-07-07` | not fixed — no frozen universe exists yet to pin them to |
| candidate universe | `load_v2_frozen_universe()` (hash-verified, 178 symbols) | `--symbols` (explicit, required) — bootstrap mode |
| `--live` | fully implemented | **rejected**: `V2_SIP_LIVE_ACQUISITION_NOT_YET_IMPLEMENTED_PHASE2A` |

Reused **unmodified**: `build_v2_acquisition_plan`, `write_v2_dry_run_plan`, `resolve_v2_dataset_paths`, `verify_file_integrity` (SHA256), all of `v2_coverage_forensics.py` and `v2_session_coverage.py`'s coverage machinery, and the gap-recovery module — none of these were touched, imported differently, or forked. `validate_v2_manifest_and_files` (rs_v2_clean's strict manifest contract: frozen 178-hash, exact 360-record set, exact target dates) was **not** parameterized or reused for SIP — reusing it would have required parameterizing hardcoded rs_v2_clean-specific constants, and no real SIP acquisition exists yet to validate against. Per the fail-closed instruction for this task, rather than force that reuse, `--live` is explicitly blocked with a clear, distinct error instead of silently falling through to rs_v2_clean's contract (which would either crash confusingly or, worse, spend real download effort before failing).

Recovery/audit-only modes (`--provider-preflight`, `--gap-recovery-plan`, `--gap-recovery-canary`, `--gap-recovery-full`, `--audit-run`) remain gated to `is_v2_clean` specifically — their internals (`GAP_RECOVERY_AUDIT_RUN`, the frozen 178-symbol/350-pair contract) are rs_v2_clean-specific and were not requested or built for SIP in this task.

### Regression evidence

- `tests/test_rs_v2_clean_data.py`'s full 16-test suite passes unchanged, including `test_live_v2_requires_overwrite_and_forbids_skip` and the dry-run test (still asserts exactly 360 plan rows).
- New regression test `test_feed_sip_rejected_under_rs_v2_clean` confirms `--dataset-profile rs_v2_clean --feed sip` still raises `"V2 profile requires --feed iex"` — unchanged from before this task.

### Tests

New file [tests/test_v2_sip_profile.py](../../tests/test_v2_sip_profile.py), 16 tests:
- Mandatory-flag `ValueError`s: missing/wrong `--adjustment`, missing `--feed`, `--feed iex` rejected, missing `--start`/`--end`/`--output-root`, missing both/both-of `--dry-run`/`--live`, missing `--symbols`.
- `--live` raises `V2_SIP_LIVE_ACQUISITION_NOT_YET_IMPLEMENTED_PHASE2A`.
- `--feed sip` rejected under `rs_v2_clean` (regression, above).
- Dry-run: produces the expected plan (candidates × 2 + QQQ/SPY × 2 timeframes), never touches credentials or the Alpaca client, doesn't create a `manifests/` directory; explicit benchmark symbols passed via `--symbols` are deduplicated rather than double-counted.
- Output-root isolation: SIP and clean default roots and exceptions-file paths are distinct constants; a SIP dry run never writes under the clean profile's default root.

All 16 pass.

---

## TASK C — Dry-run proof

Ran (bypassing only the pre-existing Windows-venv re-exec shim in `ensure_project_venv()`, which is an environment artifact unrelated to this feature — every CLI invocation on this Linux sandbox needs the same bypass, including for `rs_v2_clean`):

```
--dataset-profile rs_v2_sip --adjustment all --feed sip
--start 2025-03-10 --end 2025-03-11
--output-root /tmp/sip_dry_run_probe --symbols AAPL MSFT NVDA --dry-run
```

Output:
```
DRY RUN SUCCESSFUL. Plan generated at /tmp/sip_dry_run_probe/dry_run_reports/rs_v2_clean_data_acquisition_plan.csv and .../rs_v2_clean_data_acquisition_plan.md.
logical_acquisition_item_count: 10
Status: DRY_RUN_PLAN_ONLY
[process exited with code 0]
```

The generated plan (`rs_v2_clean_data_acquisition_plan.csv` — filename comes from the shared, unmodified `write_v2_dry_run_plan` helper) correctly threads `feed=sip` through every row:

```
symbol,timeframe,requested_start,requested_end,feed,adjustment,output_relative_path,asset_role
AAPL,1D,2025-03-10,2025-03-11,sip,all,daily/AAPL_1D.csv,CANDIDATE
AAPL,130min,2025-03-10,2025-03-11,sip,all,bars_130min/AAPL_130min.csv,CANDIDATE
MSFT,1D,2025-03-10,2025-03-11,sip,all,daily/MSFT_1D.csv,CANDIDATE
MSFT,130min,2025-03-10,2025-03-11,sip,all,bars_130min/MSFT_130min.csv,CANDIDATE
NVDA,1D,2025-03-10,2025-03-11,sip,all,daily/NVDA_1D.csv,CANDIDATE
NVDA,130min,2025-03-10,2025-03-11,sip,all,bars_130min/NVDA_130min.csv,CANDIDATE
QQQ,1D,2025-03-10,2025-03-11,sip,all,benchmarks/daily/QQQ_1D.csv,PRIMARY_BENCHMARK
QQQ,130min,2025-03-10,2025-03-11,sip,all,benchmarks/bars_130min/QQQ_130min.csv,PRIMARY_BENCHMARK
SPY,1D,2025-03-10,2025-03-11,sip,all,benchmarks/daily/SPY_1D.csv,SECONDARY_BENCHMARK
SPY,130min,2025-03-10,2025-03-11,sip,all,benchmarks/bars_130min/SPY_130min.csv,SECONDARY_BENCHMARK
```

Only `dry_run_reports/` was created under the output root — no `manifests/` directory, no credentials read, no Alpaca client constructed. This proves the wiring end-to-end without any acquisition.

---

## TASK D — Verification & commits

### Test counts

Ran both the direct `unittest discover` and the repo's own `testdata/run_test_suite.py` (compile-check of every `COMPILE_TARGETS` file, including the two changed/new-registered ones, followed by the full suite):

| | Before this task | After this task |
|---|---:|---:|
| Total tests | 591 | **623** |
| New tests added | — | 32 (16 exception-schema + 16 SIP-profile) |
| Failures | 0 | **0** |
| Compile status | OK | OK |

`testdata/reports/latest/test_run_report.md` (repo's own report artifact) confirms: `status: OK`, `tests ran count: 623`, `Failed Tests: none`.

### Commits

| Commit | Message |
|---|---|
| `cdd2e81` | `docs: secure sip residual impact report from prior task` |
| `78e6f9d` | `feat: extend V2 coverage exception schema for MISSING_INTERNAL_SESSION` |
| `ae14070` | `feat: add rs_v2_sip dataset profile foundations` |

`git status --porcelain` is empty — working tree clean. No secrets: pattern-scanned every changed/new file for API-key/secret/password/token/private-key markers before committing; only pre-existing variable-name references (`api_key`, `secret_key` as parameter names, never values) were found, consistent with the rest of the codebase's `get_credentials()` convention.

---

## Fail-closed disclosure

Reusing the SIP profile's machinery required **no modification** of any rs_v2_clean-specific logic — the one genuine conflict considered (whether to reuse `validate_v2_manifest_and_files` for SIP's eventual `--live` path) was resolved by *not* forcing that reuse: `--live` is explicitly rejected for `rs_v2_sip` in this phase rather than parameterizing a strict, hash-pinned contract that has nothing real to validate against yet. No STOP was required — the additive path stayed available throughout, and every rs_v2_clean regression (31 + 16 = 47 targeted tests, plus the full 591 pre-existing suite) confirms zero behavioral change to the existing profile.
