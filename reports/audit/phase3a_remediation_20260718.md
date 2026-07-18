# Phase 3A — Remediation Report

**Date:** 2026-07-18
**Operator:** Claude (automated)
**Mode:** surgical remediation of 2 fail-closed violations found by the Phase 3A verification pass (see `reports/audit/phase3a_cutover_20260718.md` and the verification findings delivered in-session prior to this report)
**Scope constraint:** no strategy/ranking/benchmark logic touched; no acquisition or `--live` run executed; validators kept fail-closed; no new relaxations introduced.

This report does not conclude GO/NO-GO. It documents what was fixed, with evidence, and flags one pre-existing item that was reviewed but deliberately not touched (out of the authorized scope).

---

## BLOCKER 1 — rs_v2_clean validator weakened (finding #1, commit `1d5933b`)

### 1a. Diff of the relaxation before revert

Commit `1d5933b` had added this to `validate_v2_manifest_and_files` (the rs_v2_clean gate), even though `rs_v2_clean` never uses `--refresh-daily`:

```diff
-    if any(manifest.get(key) != value for key, value in exact.items()) or not manifest.get("acquisition_run_id") or manifest.get("status") not in {"VALIDATION_PENDING", "COMPLETE_VALIDATED"}:
+    other_keys_match = all(manifest.get(key) == value for key, value in exact.items() if key != "target_end_date")
+    target_end_date_valid = (manifest.get("target_end_date") >= "2026-07-07")
+    if not other_keys_match or not target_end_date_valid or not manifest.get("acquisition_run_id") or manifest.get("status") not in {"VALIDATION_PENDING", "COMPLETE_VALIDATED"}:
         raise ValueError("V2_DATASET_NOT_VALIDATED: frozen contract mismatch")
```

Plus matching relaxations further down in the same function for `requested_end`, the 1D/130min provenance branches, and the `market_schedule`/`validate_v2_file_coverage` calls (all switched from the frozen `exact["target_end_date"]` to the mutable `manifest.get("target_end_date")`).

### 1b. Revert — byte-identical proof

`validate_v2_manifest_and_files` was reverted to the unconditional exact-match form (`if any(manifest.get(key) != value for key, value in exact.items()) or ...`), with every downstream reference restored to `exact["target_end_date"]` instead of `manifest.get("target_end_date")`.

Verification performed:
```
$ git diff 2cb4bc8 -- testdata/vericekici_updated.py
```
— the diff against baseline commit `2cb4bc8` (pre-Phase-3A) now shows **zero hunks** inside `validate_v2_manifest_and_files`; all remaining diff lines are inside `validate_v2_sip_manifest_and_files` only. Independently confirmed by extracting both function bodies (baseline commit vs. current working tree) as text and comparing them for exact equality: **byte-identical: True**.

### 1c. SIP path accommodation — gated, fail-closed

`validate_v2_sip_manifest_and_files` keeps a `target_end_date` accommodation (SIP is the only profile with a sanctioned daily-refresh mode), but it is now gated:

```python
other_keys_match = all(manifest.get(key) == value for key, value in exact.items() if key != "target_end_date")
if manifest.get("refresh_daily"):
    target_end_date = manifest.get("target_end_date")
    target_end_date_valid = isinstance(target_end_date, str) and target_end_date >= exact["target_end_date"]
else:
    target_end_date_valid = manifest.get("target_end_date") == exact["target_end_date"]
if not other_keys_match or not target_end_date_valid or not manifest.get("acquisition_run_id") or manifest.get("status") not in {"VALIDATION_PENDING", "COMPLETE_VALIDATED"}:
    raise ValueError("V2_DATASET_NOT_VALIDATED: frozen contract mismatch")
```

- **(i) Gated**: the `>=` relaxation only applies when `manifest.refresh_daily` is truthy. Any manifest without that flag is held to the original exact `"2026-07-07"` match — including the `isinstance` check, which fail-closes (rejects) on `None`/non-string `target_end_date` instead of raising an uncaught `TypeError` as the pre-remediation code would have.
- **(ii) Fail-closed direction confirmed**: outside the sanctioned refresh window (i.e. `refresh_daily` not set, or `target_end_date` doesn't match/exceed the frozen date), the function **rejects** — it never silently accepts a mismatch.

---

## BLOCKER 2 — `--live` guard unreachable on `--refresh-daily` path (finding #6)

### 2a. The unreachable path

`_main_impl` (~line 2455 onward) branches:

```python
elif is_v2_sip:
    drift_check_start = None
    if args.refresh_daily:
        ...
        args.live = True          # <- forces live mode
        ...
    else:
        ...

    ...
    if args.refresh_daily:
        candidates, v2_baseline = load_v2_frozen_universe()
        symbols = [*candidates, "QQQ", "SPY"]
    elif args.live:               # <- guard block lives here
        if not args.overwrite:
            raise ValueError(...)
        if args.refresh_latest or args.skip_download or args.only_aggregate_130min:
            raise ValueError("V2 LIVE initial acquisition forbids refresh/skip/aggregate-only")
        ...
```

Because `if args.refresh_daily:` is checked first and unconditionally sets `symbols` for that branch, the `elif args.live:` guard block — which contains the `refresh_latest`/`skip_download`/`only_aggregate_130min` rejection — is structurally never reached when `--refresh-daily` is set, even though `args.live` was just forced `True` above. This is exactly the bug class from the original `ec47f13` incident (guard present in source but not on the execution path).

### 2b. Fix and control-flow proof

An explicit fail-closed rejection was added at the very top of the `if args.refresh_daily:` branch (before any acquisition setup):

```python
if args.refresh_daily:
    if args.refresh_latest:
        raise ValueError("Cannot specify both --refresh-daily and --refresh-latest")
    if args.skip_download or args.only_aggregate_130min:
        raise ValueError("V2 SIP daily refresh forbids --skip-download and --only-aggregate-130min")
    if args.dry_run:
        raise ValueError("V2 SIP daily refresh forbids --dry-run (refresh-daily is always a live acquisition)")
    ...
```

Control-flow proof: every `--refresh-daily` invocation now hits this block first, before `args.live` is even set to `True` (that assignment happens further down in the same branch, after these checks). There is no code path from CLI argument parsing to `_download_symbol_worker`/`download_bars_with_retry` for `--refresh-daily` that can carry `--skip-download`, `--only-aggregate-130min`, or `--dry-run` — all three raise `ValueError` immediately, before credentials are loaded, before `_load_alpaca` is called, before any network I/O. `--refresh-latest` was already rejected in combination with `--refresh-daily` prior to this fix and remains rejected.

### 2c. Regression test

Added `TestRefreshDailyGuardEnforcement` in `tests/test_v2_sip_profile.py`:
- `test_refresh_daily_forbids_skip_download`
- `test_refresh_daily_forbids_only_aggregate_130min`
- `test_refresh_daily_forbids_dry_run`
- `test_refresh_daily_forbids_refresh_latest`

Each mocks `get_credentials`, `_load_alpaca`, and `download_bars`, asserts they are **never called**, and asserts the invocation raises `ValueError` with the expected message — proving fail-closed-before-network, not just "raises an error somewhere."

---

## ALSO FIXED

### Finding #2 — drift-detection boundary test

Added two tests to `TestSipAdjustmentDriftDetection`:
- `test_drift_below_threshold_does_not_trigger_full_reacquisition`: existing Close `99.51` vs. new Close `100.00` → relative diff `0.49%` (`0.0049`, not `> 0.005`) → asserts incremental path (`daily_status == "refreshed"`, only the new date appended, old row preserved).
- `test_drift_above_threshold_triggers_full_reacquisition`: existing Close `99.49` vs. new Close `100.00` → relative diff `0.51%` (`0.0051`, `> 0.005`) → asserts full re-acquisition path (`daily_status == "downloaded"`, both rows come from the fresh download, old row's price is overwritten to the new value).

Both assertions pass; the boundary sits exactly where the code's `rel_diff > 0.005` check draws the line.

### Finding #7 — undisclosed debug prints

Removed the 9-line `print("ROLE/PATH MISMATCH DETAIL:")` block that commit `1d5933b` had added inside `validate_v2_sip_manifest_and_files`'s role/path-mismatch branch, undisclosed in that commit's message. The `raise ValueError("V2_DATASET_NOT_VALIDATED: role/path mismatch")` behavior is unchanged; only the prints were removed.

---

## EVIDENCE

### Full test suite

```
Ran 646 tests in 290.265s
OK
```
Log: `scratchpad/phase3a_remediation_suite.log` (full run, this session).

**Reconciliation**: Phase 3A verification found the cutover report's claim of "640 tests" matched the actual run (640, OK) but did not reconcile against the 637-baseline + 2-added-tests arithmetic (637+2=639≠640; a 1-test discrepancy was left explicitly unresolved/flagged SUSPICIOUS in that verification, not chased further at the time). This remediation adds exactly 6 new tests (2 drift-boundary + 4 refresh-daily-guard). 640 (actual, pre-remediation) + 6 (this remediation) = **646**, which matches the actual run exactly. The prior +1 discrepancy from Phase 3A itself remains unresolved and is restated here rather than re-investigated, since re-deriving it was outside this task's two-blocker scope.

No FAIL/ERROR lines in the run.

### Benchmark no-change proof

Ran `testdata/universe_scanner.py --reports basic` (offline — reads only cached local CSVs and the existing prepared-features pickle cache; no network, no `--live`, no acquisition) both before touching any code (captured to `scratchpad/benchmark_audit_report_BEFORE.md`) and again after all remediation edits and the full suite run.

```
$ diff scratchpad/benchmark_audit_report_BEFORE.md \
       testdata/research_data/rs_v2_sip_market_data/reports/latest/benchmark_audit_report.md
(no output — files identical)
```

QQQ BuyHold Return: `177.51` (unchanged). EqualWeight Universe Return: `210.03` (unchanged). All 10 experiments' benchmark rows identical, byte-for-byte, before and after remediation. Benchmark logic was not touched by this task and this proves the validator/guard fixes have no side effect on it.

### git status / git log

```
$ git status --porcelain
 M .gitignore                                          <- pre-existing, unrelated to this task, not touched
?? reports/audit/phase2f_parity_deep_trace_20260718.md  <- pre-existing untracked Phase 2F artifact, not touched
?? scripts/phase2f_selection_snapshots.py               <- pre-existing untracked Phase 2F artifact, not touched
```
(This report itself, `reports/audit/phase3a_remediation_20260718.md`, is untracked until a follow-up commit.) No other working-tree changes remain — the two files this task modified (`testdata/vericekici_updated.py`, `tests/test_v2_sip_profile.py`) are committed.

```
$ git log --oneline -3
744e33e fix: revert rs_v2_clean validator relaxation and close --refresh-daily live-guard bypass
7f71a0f docs: Phase 3A cutover audit report and documentation updates
2f42f9f fix: dynamic target_end_date validation and benchmark fallback for SIP daily refresh
```

---

## Reviewed but NOT fixed (out of authorized scope — flagged for operator decision)

An independent code-review pass (run against this diff before commit) surfaced one additional item, pre-existing and untouched by this remediation:

```python
"skip_download": args.skip_download or args.only_aggregate_130min or (args.refresh_daily and symbol == "BK"),
```
(`testdata/vericekici_updated.py`, inside the per-symbol payload construction, introduced by commit `1d5933b`.)

Under `--refresh-daily`, this line forces `skip_download=True` specifically for `BK`, causing `_download_symbol_worker` to skip that symbol's daily/raw-5min acquisition entirely with no CLI flag required. The reviewer initially flagged this CRITICAL as a possible bypass of the guard fixed in Blocker 2. On inspection this appears to be the **already-disclosed** BK→BNY ticker-rename accommodation: BK has an operator-approved `EARLY_TERMINAL` reviewed exception from Phase 2D (ticker changed to BNY 2026-05-21), and the Phase 3A cutover report itself lists "BK skipped in daily refresh — existing approved exception, expected behavior" under its Issues section — i.e. it was known and disclosed, not silently introduced.

This was **not** one of the two enumerated blockers or the two "ALSO FIX" items in this task's authorization, and touching it would mean making a judgment call on strategy-adjacent acquisition behavior beyond the surgical scope given ("Do NOT touch strategy/ranking/benchmark logic"). Left unchanged. Flagging here rather than silently fixing or silently ignoring it, consistent with this engagement's practice of full disclosure over assumed authorization.

---

## Stop

Per task instructions: no GO/NO-GO conclusion is offered here. Stopping after reporting.
