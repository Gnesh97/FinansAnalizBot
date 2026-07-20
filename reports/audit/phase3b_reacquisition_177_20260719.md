# Phase 3B — 177-Universe SIP Re-Acquisition: Honest Failure Report

**Date:** 2026-07-19/20
**Operator authorization:** live acquisition explicitly authorized by operator, this task.
**Outcome: FAILED_VALIDATION.** Reported honestly, not bypassed. No validator was weakened, no exception was auto-approved, no data was silently modified. `latest_manifest.json` was NOT updated (correctly — `finalize_v2_manifest` only commits the pointer on `COMPLETE_VALIDATED`, and that was never reached).

This report does not conclude GO/NO-GO.

---

## 1. Acquisition command run

```
python vericekici_updated.py \
  --dataset-profile rs_v2_sip --adjustment all --feed sip \
  --start 2020-07-27 --end 2026-07-07 \
  --output-root testdata/research_data/rs_v2_sip_market_data \
  --live --overwrite --parallel --download-workers 4 --aggregation-workers auto
```

This is the sanctioned full (non-`--refresh-daily`) SIP acquisition path — `feed=sip`, `adjustment=all`, exact frozen target window `2020-07-27`..`2026-07-07`, `--live --overwrite` (required for a full initial/re-acquisition; `--refresh-daily` was deliberately NOT used, per instruction, since this is a full re-acquisition under a new baseline, not an incremental refresh). It ran to completion — processed all 179 symbols (177 candidates + QQQ + SPY), no crash, no network error, no credential problem.

## 2. Honest validation result

```
acquisition_run_id: acq_20260719_205948
status: FAILED_VALIDATION
candidate_count: 177
candidate_universe_hash: 026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933
logical_acquisition_item_count: 358
successful_item_count: 2
failed_item_count: 356
```

`finalize_v2_manifest` → `validate_v2_sip_manifest_and_files` raised:

```
ValueError: V2_DATASET_NOT_VALIDATED: frozen contract mismatch
```

This is the genuine, unmodified strict validator — no except-swallow, no bypass, exactly the code path audited in the Phase 3A verification and Phase 3A remediation work. It failed on its own merits. The manifest itself independently records `status: FAILED_VALIDATION` (not `COMPLETE_VALIDATED`), confirming the failure is real and was not routed around.

## 3. Root cause (traced, not guessed)

**The on-disk SIP dataset was already extended past the frozen window by a prior, separately-authorized `--refresh-daily` run** (the Phase 3A cutover, commit `1d5933b`/`96915b2`, whose own smoke-run log recorded "already up to 2026-07-17"). That prior run legitimately advanced almost every symbol's daily/raw files from the frozen end date (`2026-07-07`) out to `2026-07-17` (10 extra trading sessions).

This new acquisition requested the **exact frozen window** (`2020-07-27` to `2026-07-07`, per the strict `rs_v2_sip` contract for a non-`refresh_daily` run). For any symbol whose on-disk file already had data through `2026-07-17`, the freshly-downloaded frozen-window response (naturally **fewer rows** than the extended on-disk file) tripped `refresh_file()`'s built-in shrink-protection guard:

```python
# testdata/vericekici_updated.py, refresh_file()
if old_rows and len(final) < old_rows:
    ...
    if not allow_row_count_reduction:
        return "skipped", len(new_rows), 0, warning
```

`--allow-row-count-reduction` was **not** passed (correctly — it wasn't part of the authorized command), so the function refused to overwrite and reported `"skipped"` instead of `"downloaded"`. That in turn fails the strict non-`refresh_daily` provenance check (`provider_result_status != "DOWNLOADED"` → `request_status = FAILED`), and separately, the file's now-stale coverage was flagged `UNEXPECTED_MARKET_SESSION` by the coverage validator for the 8-10 sessions beyond the requested end date that are still sitting in the file.

**Quantified, exact evidence:**

```
provider_result_status counts across all 358 items: {'SKIPPED': 356, 'DOWNLOADED': 2}
1D last_timestamp date distribution: {'2026-07-17': 178 symbols, '2025-08-06': 1 symbol (PARA)}
succeeded (request_status == SUCCESS): only PARA (1D, 130min)
```

`PARA` succeeded because it's the one symbol whose on-disk file already ends earlier (`2025-08-06`, PARA's real delisting date, per its approved `EARLY_TERMINAL` exception) — its fresh frozen-window download naturally has *more* rows than what was on disk, so the shrink guard never triggered and it downloaded/validated normally. Every other symbol — 178 of 179 — was blocked by the exact same guard for the exact same reason: real, extra, already-downloaded data sitting past the frozen window.

Sample record (`AAPL`, `1D`):
```
provider_result_status: SKIPPED
request_status: FAILED
coverage_status: FAIL
coverage_exception_reason: UNEXPECTED_MARKET_SESSION
unexpected_session_count: 8
row_count: 1501 (on disk)  vs.  requested_end: 2026-07-07, but last_timestamp: 2026-07-17
```

## 4. What this is NOT

- **Not a validator bug.** The shrink-protection guard in `refresh_file()` and the coverage validator's `UNEXPECTED_MARKET_SESSION` check are both working exactly as designed — refusing to silently discard 10 real trading sessions' worth of already-downloaded data without an explicit, visible flag.
- **Not a credentials/network/entitlement problem.** The acquisition ran to completion; PARA proves live SIP access and the full pipeline work correctly end-to-end.
- **Not touched or worked around.** No flag was added, no check was loosened, no exception was fabricated. `--allow-row-count-reduction` — the one flag that would resolve this — was deliberately **not** used, since applying it wasn't part of what was explicitly authorized for this run and would truncate real, already-acquired data (2026-07-08 through 2026-07-17) back to the frozen window as a consequence. That is a decision for the operator, not something to apply unilaterally mid-task.

## 5. Exception/universe checks that DID pass, for the record

Even though the overall manifest failed, the parts of validation that ran before the final gate confirm the universe-side of the retirement is correct:
- `candidate_count: 177`, `candidate_universe_hash: 026f66d1...` — both match the new frozen baseline exactly (visible in the manifest header above).
- `logical_acquisition_item_count: 358` = (177 + 2) × 2, correct.
- Approved-exception reason tokens that DID surface in the (failed) coverage output include `APPROVED_LATE_START`, `APPROVED_MISSING_INTERNAL_SESSION`, `APPROVED_PARTIAL_BOUNDARY_SESSION`, `APPROVED_EARLY_TERMINAL` — all pre-existing, registry-backed reason tokens, nothing new or auto-approved. No `BK`-related exception appears anywhere (BK is not in the 177-symbol candidate set, so no BK record exists to except).
- No new/unexpected exception type surfaced. `UNEXPECTED_MARKET_SESSION` is not an "exception" in the reviewed-registry sense — it's a raw coverage-status FAIL reason indicating unapproved data outside the requested window, exactly the failure being reported here.

## 6. Not done, and why

Per instruction ("If validation fails at any point, STOP and report the real failure rather than working around it"), the following steps were **not** performed:
- `latest_manifest.json` was **not** updated — correctly left pointing at the prior `acq_20260718_154844` run, since `finalize_v2_manifest` never reaches `COMPLETE_VALIDATED`.
- Full exception enumeration against "Phase 2E baseline minus BK" was not completed as a clean pass/fail table, since the dataset itself did not validate.
- Full test suite / git commit for this specific reacquisition attempt were not run, since there is no new validated state to commit (the working tree's Phase 3B universe-retirement code changes from the prior task remain as they were).

## 7. What would resolve this (not applied — operator decision)

The acquisition tool already has the sanctioned mechanism for this exact situation: re-run with `--allow-row-count-reduction` added to the same command. That would let `refresh_file()` truncate each symbol's file back down to the exact frozen window, discarding the 2026-07-08–2026-07-17 residue from the earlier refresh-daily run, and allow the strict validator to re-check coverage against a file that actually matches the requested window. This is a visible, explicit, already-existing flag — not a new mechanism — but applying it means intentionally discarding 10 already-acquired days of real data, which is a call for the operator to make, not something to decide unilaterally mid-acquisition.

---

## Stop

Per task instructions: no GO/NO-GO conclusion is offered here. Stopping after reporting the real, honest failure.
