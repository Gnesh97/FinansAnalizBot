# Phase 3B — Frozen Baseline Rebuild (177 @ 2026-07-07) + Daily Refresh to Current

**Date:** 2026-07-20
**Operator authorization:** two-step live acquisition, explicitly authorized this task.
**Outcome: both steps COMPLETE_VALIDATED.** No validator was weakened, no exception was auto-approved, no strategy/ranking/benchmark logic touched, no broker/order calls.

This report does not conclude GO/NO-GO.

---

## STEP 1 — Rebuild frozen baseline at 2026-07-07 (177 universe)

### Command

```
python vericekici_updated.py --dataset-profile rs_v2_sip --adjustment all --feed sip \
  --start 2020-07-27 --end 2026-07-07 \
  --output-root testdata/research_data/rs_v2_sip_market_data \
  --live --overwrite --allow-row-count-reduction \
  --parallel --download-workers 4 --aggregation-workers auto
```

`--allow-row-count-reduction` used here, bound to the exact frozen end date `2026-07-07` — this is the one authorized use for this task (see confirmation at the bottom).

### Result

```
acquisition_run_id: acq_20260719_225502
status: COMPLETE_VALIDATED
candidate_count: 177
candidate_universe_hash: 026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933
logical_acquisition_item_count: 358
successful_item_count: 358
failed_item_count: 0
target_start_date: 2020-07-27
target_end_date: 2026-07-07
```

All 179 symbols (177 candidates + QQQ + SPY) downloaded (179 daily, 179 raw 5-min, 179 built 130-min bars), 0 errors, `validate_v2_sip_manifest_and_files` passed honestly.

### Residual-window check

1D `last_timestamp` across all files now takes exactly two distinct dates: `2026-07-07` (176 candidates + QQQ + SPY) and `2025-08-06` (PARA only — its real, approved `EARLY_TERMINAL` delisting date, unaffected by the frozen-window request). **The 2026-07-08..2026-07-17 residue from the earlier `--refresh-daily` run is gone** — every file now ends exactly where the frozen contract requires.

### Exception enumeration

```
APPROVED_MISSING_INTERNAL_SESSION : 121 records
APPROVED_LATE_START               : 12 records   (6 symbols x 2 timeframes: ABNB, PLTR, RBLX, SNOW, WBD, PARA)
APPROVED_PARTIAL_BOUNDARY_SESSION : 4 records    (ABNB, PLTR, RBLX, SNOW, 130min only)
APPROVED_EARLY_TERMINAL           : 2 records    (PARA, 1D + 130min)
coverage_status: all 358 records VALID
```

Cross-checked against `testdata/baselines/rs_v2_sip_coverage_exceptions.json` (schema `3.0.0`): the registry itself still lists `BK`'s `EARLY_TERMINAL` entry (untouched — it's a historical record, not deleted) alongside `PARA`'s. Since `BK` is no longer in the 177-symbol candidate set, its exception simply never gets a matching record to apply to — the applied set above is **exactly the Phase 2E exception set minus BK's (now-inapplicable) `EARLY_TERMINAL` entry**. No new or unexpected exception type/token appeared anywhere in the 358 records.

### Pointer update

```
Before: acq_20260718_154844
After:  acq_20260719_225502
```

`finalize_v2_manifest` published this atomically on successful validation (the tool's own sanctioned commit path — not manually edited).

---

## STEP 2 — Bring data current via daily refresh

### Command

```
python vericekici_updated.py --dataset-profile rs_v2_sip --refresh-daily \
  --parallel --download-workers 4 --aggregation-workers auto \
  --output-root testdata/research_data/rs_v2_sip_market_data
```

No `--allow-row-count-reduction` (confirmed absent from this command).

### Result

```
acquisition_run_id: acq_20260719_235853
status: COMPLETE_VALIDATED
candidate_count: 177
candidate_universe_hash: 026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933
logical_acquisition_item_count: 358
successful_item_count: 358
failed_item_count: 0
target_start_date: 2020-07-27
target_end_date: 2026-07-17
refresh_daily: true
```

178 symbols refreshed incrementally, 1 skipped (PARA — delisted, correctly has nothing new to fetch), 0 errors.

### Drift-detection outcome

The trailing-10-session adjustment-drift check ran for every symbol with existing data. It triggered a full-history re-acquisition for exactly one symbol:

```
[ADJUSTMENT_DRIFT] PNC drift detected! Performing full history re-acquisition.
```

Investigated: the finalized `PNC_1D.csv` shows no split-like price discontinuity across the affected window (prices move smoothly from ~$231 to ~$253 through late June/early July 2026, no jump). This looks like a routine SIP feed price-revision (a small vendor-side adjustment restatement crossing the 0.5% threshold), not a real corporate action. The exact drift percentage that triggered it is not retrievable after the fact — the check compares the pre-refresh on-disk values against a fresh trailing-window fetch in memory, and both were superseded in the same run by the full re-acquisition it triggered — but the trigger and its full-history-reacquisition consequence are visible and logged as shown above, and the resulting data is internally consistent (no anomaly, validates cleanly). No other symbol triggered the drift path.

### Fail-closed guard confirmation

`--refresh-daily` was run alone, with none of `--skip-download`, `--only-aggregate-130min`, `--dry-run`, or `--refresh-latest` — all four of which are explicitly rejected in combination with `--refresh-daily` per the Phase 3A remediation fix (`testdata/vericekici_updated.py`, the `if args.refresh_daily:` guard block). The run reached `args.live = True` only after those guards, exactly as designed; no bypass was exercised or needed.

### Re-validation and exception enumeration

`validate_v2_sip_manifest_and_files` passed honestly (`status: COMPLETE_VALIDATED`, 358/358, 0 failed). Exception token counts are **unchanged** from Step 1 (`APPROVED_MISSING_INTERNAL_SESSION`: 121, `APPROVED_LATE_START`: 12, `APPROVED_PARTIAL_BOUNDARY_SESSION`: 4, `APPROVED_EARLY_TERMINAL`: 2) — the extra 10 days of fresh data introduced no new coverage gaps or exception needs.

### Pointer update

```
Before: acq_20260719_225502
After:  acq_20260719_235853
```

---

## `--allow-row-count-reduction` confirmation (hard rule)

- Used exactly **once**: Step 1's command only, bound to the exact frozen end date `2026-07-07`.
- **Not** used in Step 2's command (shown above — absent).
- **Not** persisted anywhere:
  ```
  $ grep -rn "allow-row-count-reduction" scripts/ tests/*.bat tests/*.sh 2>/dev/null
  (no output)
  $ grep -rln "allow-row-count-reduction" --include="*.bat" --include="*.sh" --include="*.json" --include="*.ps1" .
  (no output, excluding .venv)
  ```
  It exists only as a CLI flag definition in `testdata/vericekici_updated.py` (pre-existing, part of the sanctioned tool) and was never written into any `.bat`/`.sh`/config file or made a default.

---

## EVIDENCE

### Full test suite

```
Ran 654 tests in 308.825s
OK
```
Log: `scratchpad/phase3b_final_suite.log`. Reconciles exactly against the established 654-test baseline — no new tests were added by this task (no code changes were made; this was a data-acquisition task only).

### git status / git log

```
$ git status --porcelain
 M testdata/baselines/rs_v2_sip_baseline.json   <- auto-updated generated_at timestamp only (freeze_v2_sip_baseline(), tool's own sanctioned write on successful finalize); content otherwise unchanged
?? reports/audit/phase3b_correction_20260719.md
?? reports/audit/phase3b_reacquisition_177_20260719.md
```
(This report itself is untracked until a follow-up commit.) No source-code changes this task — commits since the last code change remain:
```
$ git log --oneline -6
1bd58e6 docs: Phase 3B BK universe retirement audit report and impact script
472951d feat: retire BK from frozen universe (178 -> 177) and update hash verification
b27ccb6 docs: Phase 3A exception-registry-fix verification report
e58d8c6 fix: replace hardcoded BK ticker check with exception-registry lookup
f6805b6 docs: Phase 3A remediation report
744e33e fix: revert rs_v2_clean validator relaxation and close --refresh-daily live-guard bypass
```

---

## Stop

Per task instructions: no GO/NO-GO conclusion is offered here. Stopping after reporting.
