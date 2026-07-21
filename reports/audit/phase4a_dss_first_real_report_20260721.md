# Phase 4A — First Real DSS Report Attempt: Honest Failure (External SIP Entitlement Limit)

**Date:** 2026-07-20/21
**Outcome: the code is sound; the on-disk data still could not be refreshed, for a reason outside this codebase's control.** Nothing was weakened, bypassed, or worked around. No GO/NO-GO conclusion.

---

## Step 0 — outstanding reports committed

Committed the two untracked Phase 4A reports plus the `.gitignore` addition for `reports/dss/` (operational run artifacts, kept out of the tree):

```
$ git add .gitignore reports/audit/phase4a_dss_report_and_telegram_20260720.md reports/audit/phase4a_dss_stale_count_fix_20260720.md
$ git commit -m "docs: Phase 4A DSS report+telegram and stale-count-fix reports"
[checkpoint/research-framework-2026-07 aec37a5] docs: Phase 4A DSS report+telegram and stale-count-fix reports
 3 files changed, 264 insertions(+)
```

`.gitignore` diff (only line added, no other pre-existing entries touched):
```diff
 testdata/hisseler*/
 testdata/reports/
+reports/dss/
 testdata/backups/
```
`reports/dss/` was never tracked to begin with (confirmed `??` untracked before this change), so no `git rm --cached` was needed — the addition simply prevents it from being picked up by any future broad `git add`.

## Step 1 — `--refresh-daily` run: honest failure

### Command

```
python vericekici_updated.py --dataset-profile rs_v2_sip --refresh-daily \
  --parallel --download-workers 4 --aggregation-workers auto \
  --output-root testdata/research_data/rs_v2_sip_market_data
```

No `--allow-row-count-reduction`, `--skip-download`, `--only-aggregate-130min`, or `--dry-run` — all forbidden per the hard rules (and all fail-closed-rejected by the guard added in the earlier Phase 3A remediation regardless).

### What happened

Every one of the 179 symbols hit the same provider-side error:

```
error: {"message":"subscription does not permit querying recent SIP data"}
```

Confirmed via the log: **178 occurrences** of this exact message, one per symbol attempted (each symbol logs the error once per failed daily+intraday query pair in this run's code path). This is a real Alpaca API response — the credentials in `.env` authenticate successfully (no auth error), but the current SIP subscription tier does not permit querying *recent* SIP data (this is a known Alpaca SIP-feed restriction pattern: many SIP subscription tiers only permit historical/delayed data, not near-real-time queries). **This is an external account/subscription limitation, not a code bug.**

### Drift detection

```
$ grep -c "ADJUSTMENT_DRIFT" scratchpad/dss_refresh.log
0
```

Zero drift triggers — expected and correct: the drift check compares a freshly-fetched trailing window against existing on-disk data, and since every fetch failed with the subscription error, there was never any new data to compare against the old data. Nothing to report per-symbol; no false trigger, no false suppression.

### Honest validation outcome

```
acquisition_run_id: acq_20260720_221523
status: FAILED_VALIDATION
target_end_date: 2026-07-20
successful_item_count: 358
failed_item_count: 0
```

```
Traceback (most recent call last):
  ...
  File "testdata/v2_session_coverage.py", line 456, in validate_v2_file_coverage
    raise ValueError(f"V2_DATASET_NOT_VALIDATED: coverage failed: {recomputed['coverage_exception_reason']}")
ValueError: V2_DATASET_NOT_VALIDATED: coverage failed: UNAPPROVED_EARLY_TERMINAL
```

Why this specific error: since no new data landed for any symbol, the on-disk files still end at `2026-07-17`, but the manifest's `target_end_date` (the requested window) advanced to `2026-07-20` (today's expected latest completed session). The coverage validator recomputed coverage against that 3-day gap and correctly flagged it as `UNAPPROVED_EARLY_TERMINAL` — the coverage engine has no way to distinguish "provider subscription blocked the query" from "the security genuinely stopped trading" at this layer; both present identically as "no data available after date X, and no reviewed exception covers it." **This is the fail-closed system working exactly as designed** — it refused to silently accept a 3-day data gap as valid, and it did not auto-approve any exception to paper over it (no exception was added to the registry).

`successful_item_count: 358, failed_item_count: 0` reflects that every file's per-file provenance check still passed (each file was correctly marked with `SKIPPED` provenance, which the `refresh_daily` branch's relaxed provenance check accepts) — the rejection came from the deeper coverage-gap check, not from provenance/hash/role integrity, all of which remain sound.

### Pointer — confirmed untouched

```
$ cat testdata/research_data/rs_v2_sip_market_data/manifests/latest_manifest.json
{
  "acquisition_run_id": "acq_20260719_235853",
  ...
}
```

Still points at the last genuinely validated run from Phase 3B (`acq_20260719_235853`) — `finalize_v2_manifest` only commits the pointer on `COMPLETE_VALIDATED`, and that was never reached here. No manual edit, no bypass.

## Step 2 — DSS `--print-only` re-confirmation

```
$ python bot.py --mode dss-report --print-only
==================================================
📋 [MODE] Mode: dss-report
==================================================
DSS did not run today: data is stale: manifest target_end_date='2026-07-17', expected latest completed session='2026-07-20'

Saved: reports/dss/dss_report_20260720T222337Z.md

--print-only: not sending to Telegram.
```

Correct and expected: since the refresh attempt produced no validated new data, the DSS report generator still reads the last genuinely validated manifest (`target_end_date=2026-07-17`) and correctly fails loud for the same staleness reason as before — this time for the right underlying cause (external provider limitation), not the stale-constant bug fixed earlier in this session.

## What this is NOT

- **Not a code bug.** The stale-360-constant bug (fixed earlier this session) is confirmed gone — this run failed at a completely different, later stage (coverage/gap validation), for a completely different reason (no data arrived at all).
- **Not worked around.** No validator was weakened. No `--allow-row-count-reduction` or other bypass flag was used. No exception was added to the registry to paper over the gap.
- **Not something fixable from inside this codebase.** This requires operator action outside the code: verifying/upgrading the Alpaca account's SIP data subscription tier to permit querying recent (near-real-time) SIP data, or confirming whether this is a temporary account-side issue.

## Evidence

### Full test suite

```
Ran 672 tests in 523.361s
OK
```
No code changes were made this task (this was a data-acquisition attempt only), so the count is unchanged from the prior Phase 4A baseline (672).

### git status / git log

```
$ git status --porcelain
(clean)

$ git log --oneline -4
aec37a5 docs: Phase 4A DSS report+telegram and stale-count-fix reports
d0dc4c5 fix: correct stale 360/356 file-count literals in validate_v2_clean_dataset to 358/354 post-BK-retirement
6484a9b feat: add daily DSS BUY/SELL report generator and Telegram send (manual-trigger only)
e1131de docs: Phase 3B reacquisition + baseline rebuild reports
```

---

## Stop

Per task instructions: no GO/NO-GO conclusion is offered here. This is a real, honest failure requiring operator action on the Alpaca SIP subscription entitlement, not a code fix. Stopping after reporting.
