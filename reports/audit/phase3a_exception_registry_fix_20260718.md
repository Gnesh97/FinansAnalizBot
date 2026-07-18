# Phase 3A — Blocker Re-Verification and Exception-Registry Fix

**Date:** 2026-07-18
**Operator:** Claude (automated)
**Mode:** (A) read-only re-verification of the Blocker 1/2 claims made in `reports/audit/phase3a_remediation_20260718.md`; (C) surgical fix of the third violation that remediation surfaced (hardcoded boundary-symbol literal in acquisition code).
**Scope constraint:** no strategy/ranking/benchmark logic touched; no acquisition or `--live` run executed; validators kept fail-closed; no new relaxations introduced. The only authorized write in this task is item C.

This report does not conclude GO/NO-GO.

---

## A. Verification — Blocker 1 & 2 claims from the prior remediation

### Blocker 1

**Sub-claim (i): rs_v2_clean validator is identical to baseline `2cb4bc8`.**

```
$ git diff 2cb4bc8 -- testdata/vericekici_updated.py
```

All hunk headers in this diff (as of the commit reviewed here, `e58d8c6`):

```
@@ -279,7 +279,13 @@ def validate_v2_sip_manifest_and_files(dataset_root: Path, manifest: dict, froze
@@ -294,7 +300,7 @@ def validate_v2_sip_manifest_and_files(dataset_root: Path, manifest: dict, froze
@@ -303,8 +309,12 @@ def validate_v2_sip_manifest_and_files(dataset_root: Path, manifest: dict, froze
@@ -314,15 +324,19 @@ def validate_v2_sip_manifest_and_files(dataset_root: Path, manifest: dict, froze
@@ -1463,7 +1477,130 @@ def _download_symbol_worker(payload: dict) -> dict:
@@ -1950,6 +2087,7 @@ def build_parser() -> argparse.ArgumentParser:
@@ -2315,24 +2453,70 @@ def _main_impl() -> None:
... (remaining hunks all >= line 2296 in the baseline file, all inside _main_impl or below)
```

Every hunk header names `validate_v2_sip_manifest_and_files`, `_download_symbol_worker`, `build_parser`, or `_main_impl`. **Zero hunks touch `validate_v2_manifest_and_files`** (the rs_v2_clean gate, baseline lines 205-263 / current lines 205-267). Independently confirmed by extracting both function bodies (baseline commit `2cb4bc8` vs. current working tree, delimited from `def validate_v2_manifest_and_files` to the next `def`) and comparing for exact equality: **byte-identical: True**.

**Verdict: CONFIRMED.** The rs_v2_clean validator is untouched.

**Sub-claim (ii): the SIP validator's `target_end_date` relaxation is reachable only when `manifest.refresh_daily` is truthy, and the non-refresh path fail-closes on exact match / non-string / None.**

Current full gating logic, `validate_v2_sip_manifest_and_files` (lines 282-289):

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

Traced: the `>=` relaxation branch is only entered `if manifest.get("refresh_daily")` — any falsy/absent value takes the `else` branch, which requires exact `== "2026-07-07"`. In the `else` branch, `manifest.get("target_end_date")` compared with `==` against a fixed string correctly evaluates to `False` (not a crash) for `None` or any non-matching type, so the surrounding `if not target_end_date_valid: raise` fires — fail-closed. In the `if manifest.get("refresh_daily")` branch, `isinstance(target_end_date, str)` is checked *before* the `>=` comparison via Python's short-circuit `and`, so `None`/non-string values evaluate the whole expression to `False` without ever reaching a `TypeError`-prone comparison — also fail-closed.

**Verdict: CONFIRMED**, both halves of the sub-claim.

### Blocker 2

Full `if args.refresh_daily:` branch, `_main_impl` (lines 2457-2500 as currently committed):

```python
if args.refresh_daily:
    if args.refresh_latest:
        raise ValueError("Cannot specify both --refresh-daily and --refresh-latest")
    if args.skip_download or args.only_aggregate_130min:
        raise ValueError("V2 SIP daily refresh forbids --skip-download and --only-aggregate-130min")
    if args.dry_run:
        raise ValueError("V2 SIP daily refresh forbids --dry-run (refresh-daily is always a live acquisition)")
    if not args.feed:
        args.feed = "sip"
    if args.feed != "sip":
        raise ValueError("V2 SIP profile requires --feed sip")
    if not args.adjustment:
        args.adjustment = "all"
    if args.adjustment != "all":
        raise ValueError("V2 SIP profile requires --adjustment all")
    if not args.output_root:
        args.output_root = str(DEFAULT_V2_SIP_FROZEN_ROOT)
    if not args.start:
        args.start = "2020-07-27"

    output_dir = Path(args.output_root)
    latest_pointer_path = output_dir / "manifests" / "latest_manifest.json"
    if not latest_pointer_path.exists():
        raise FileNotFoundError("latest_manifest.json not found under output_root. Daily refresh requires an existing validated SIP dataset.")

    latest_pointer = json.loads(latest_pointer_path.read_text(encoding="utf-8"))
    run_manifest_path = output_dir / latest_pointer["immutable_manifest_relative_path"]
    latest_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))

    today_str = args.end or datetime.now(timezone.utc).date().isoformat()
    market_schedule = approved_nyse_schedule("2020-07-27", today_str)
    latest_session_date_str = market_schedule.index[-1].strftime("%Y-%m-%d")

    if latest_manifest.get("target_end_date") == latest_session_date_str:
        print(f"Idempotent: daily refresh already run up to {latest_session_date_str}. No-op.")
        sys.exit(0)

    args.end = latest_session_date_str
    args.live = True

    if len(market_schedule) >= 10:
        drift_check_start = market_schedule.index[-10].to_pydatetime()
    else:
        drift_check_start = parse_utc_date("2020-07-27")
```

The three guard rejections (`refresh_latest`, `skip_download`/`only_aggregate_130min`, `dry_run`) are the first three statements in the branch — they execute before `args.output_root`/`args.start` defaulting, before the `latest_manifest.json` read, and, critically, before `args.live = True` is even assigned (that assignment is 30+ lines further down, after the guards). No credential loading (`get_credentials`), client construction (`_load_alpaca`), or download call (`download_bars`/`download_bars_with_retry`) appears anywhere in this branch or is reachable before these three `raise` statements — those calls only happen later, inside the per-symbol worker dispatch, which this branch cannot reach if any of the three flags is set.

The 4 regression tests (`TestRefreshDailyGuardEnforcement` in `tests/test_v2_sip_profile.py`) each patch `get_credentials`, `_load_alpaca`, and `download_bars`, run `main()` under the offending flag combination, assert the expected `ValueError` fires, and then assert all three mocks were **never called**:

```python
def _run(self, argv, expected_message):
    with patch("testdata.vericekici_updated.ensure_project_venv"), \
         patch("testdata.vericekici_updated.get_credentials") as credentials, \
         patch("testdata.vericekici_updated._load_alpaca") as alpaca, \
         patch("testdata.vericekici_updated.download_bars") as download, \
         patch.object(sys, "argv", argv), \
         self.assertRaisesRegex(ValueError, expected_message):
        main()
    credentials.assert_not_called()
    alpaca.assert_not_called()
    download.assert_not_called()
```

**Verdict: CONFIRMED.** No path from `--refresh-daily` reaches live acquisition while carrying `--skip-download`, `--only-aggregate-130min`, or `--dry-run`.

---

## C. The third violation — hardcoded boundary symbol in acquisition code

### The violation

```python
"skip_download": args.skip_download or args.only_aggregate_130min or (args.refresh_daily and symbol == "BK"),
```

This hardcoded `"BK"` directly into the per-symbol acquisition payload construction. Functionally it correctly honored BK's approved `EARLY_TERMINAL` exception (BK→BNY ticker change), and it was disclosed in both the Phase 3A cutover report and the prior remediation report — but the *mechanism* was a literal ticker check in code, not a registry lookup. This is the same violation class Phase 2B was caught for: a boundary condition that should be entirely data-driven (reviewed exception file) was instead baked into acquisition logic. If a second symbol were ever terminated, or BK's exception were revoked, the code would not reflect that without another manual edit — exactly the failure mode a reviewed-exception registry exists to prevent.

### How EARLY_TERMINAL exceptions are recorded

Schema (`testdata/v2_session_coverage.py`, `EXCEPTION_FIELDS` + `_required_exception_fields`): every `EARLY_TERMINAL` row must carry `symbol`, `exception_type`, `approved_last_session`, `reason`, `evidence_source`, `evidence_date`, `reviewed: true`, `approved_by`, `approved_at`, and `evidence_hash` (a SHA-256 over the canonical JSON of the record minus the hash field itself — tamper-evident). BK's actual record, `testdata/baselines/rs_v2_sip_coverage_exceptions.json`:

```json
{
  "symbol": "BK",
  "exception_type": "EARLY_TERMINAL",
  "approved_last_session": "2026-05-20",
  "reason": "BK ... changed its NYSE ticker symbol from BK to BNY, effective 2026-05-21 ...",
  "evidence_source": "https://www.bny.com/corporate/global/en/about-us/newsroom/press-release/bny-announces-planned-change-of-stock-ticker-symbol-to-bny-130465.html",
  "evidence_date": "2026-05-11",
  "reviewed": true,
  "approved_by": "operator (phase2d_targeted_repairs authorization)",
  "approved_at": "2026-07-18",
  "evidence_hash": "e5e3785a8537696db4f2b9e3d12ab6df5684fe9e1adb55a9ef59e974cf2a17a2"
}
```

`load_reviewed_coverage_exceptions()` validates this shape (required fields present, `reviewed is True`, `evidence_hash` matches a fresh recomputation) and raises `V2_COVERAGE_EXCEPTION_FILE_INVALID` on any tamper or malformed row. `_matching_exception(exceptions, symbol, "EARLY_TERMINAL")` (already existed in `v2_session_coverage.py`, used by `calculate_v2_session_coverage` for the same purpose in the coverage-audit path) returns the validated row for a given symbol, or `None` if no valid match exists — this is the registry-query primitive the fix reuses.

### The fix

Added a pure function, `_refresh_daily_terminated_symbols` (`testdata/vericekici_updated.py`, inserted after `load_v2_frozen_universe`):

```python
def _refresh_daily_terminated_symbols(symbols: list[str], exceptions: list[dict], as_of_date: str) -> set[str]:
    """Symbols whose acquisition should be skipped during --refresh-daily because a
    reviewed EARLY_TERMINAL exception (registry) shows no data can exist for them on
    or after as_of_date. Registry-driven: no symbol literals here -- the exception
    file is the only source of which symbols this applies to.
    """
    terminated = set()
    for symbol in symbols:
        terminal_exception = _matching_exception(exceptions, symbol, "EARLY_TERMINAL")
        if terminal_exception is not None and str(terminal_exception.get("approved_last_session", "")) <= as_of_date:
            terminated.add(symbol)
    return terminated
```

Wired in before the per-symbol payload loop:

```python
refresh_daily_terminated_symbols = set()
if args.refresh_daily:
    refresh_daily_exceptions = load_reviewed_coverage_exceptions(V2_SIP_COVERAGE_EXCEPTIONS_PATH)
    refresh_daily_terminated_symbols = _refresh_daily_terminated_symbols(symbols, refresh_daily_exceptions, args.end)
```

And the payload line:

```python
"skip_download": args.skip_download or args.only_aggregate_130min or (symbol in refresh_daily_terminated_symbols),
```

`_matching_exception` already enforces `reviewed is True` and `evidence_hash` correctness before returning a row (returns `None` otherwise), so a tampered or unreviewed record is silently *not honored* — fail-closed toward "don't skip" (i.e. attempt the download, which will legitimately return nothing for a truly terminated symbol; this is a wasted API call, not a safety violation). The `approved_last_session <= as_of_date` check additionally guards against skipping a symbol whose termination date is somehow still within or after the refresh window.

`symbols == 178 candidates + QQQ + SPY` for the refresh-daily path (unchanged from before this fix; not touched).

### Regression tests

`TestRefreshDailyTerminatedSymbolsRegistryDriven` in `tests/test_v2_sip_profile.py`, calling `_refresh_daily_terminated_symbols` directly with synthetic exception rows (correct `evidence_hash` computed via the real `coverage_exception_evidence_hash`):

- `test_symbol_with_approved_exception_is_skipped` — `BK` with a valid exception → `{"BK"}`.
- `test_symbol_without_exception_is_not_skipped` — `AAPL`/`MSFT` with no matching exception → `set()`.
- `test_altering_the_exception_record_changes_the_behavior` — identical call, only the `exceptions` list differs (with vs. without the BK record) → result flips between `{"BK"}` and `set()`, proving the decision is registry-content-driven, not hardcoded.
- `test_unreviewed_or_tampered_exception_is_not_honored` — a tampered `evidence_hash` causes `_matching_exception` to reject the row → `set()` (fail-closed, doesn't skip).
- `test_terminal_date_after_refresh_window_is_not_skipped` — an exception whose `approved_last_session` is after the refresh window's end date → `set()` (guards the date-ordering edge case).

Full file run: **31 tests, OK** (26 pre-existing + 5 new).

### Grep proof — zero remaining hardcoded ticker literals in skip logic

```
$ grep -n 'skip_download' testdata/vericekici_updated.py
1474:    skip_download = payload["skip_download"]
1495:        if skip_download:
2456:        if args.live and (args.refresh_latest or args.skip_download or args.only_aggregate_130min):
2476:            if args.skip_download or args.only_aggregate_130min:
2549:            if args.refresh_latest or args.skip_download or args.only_aggregate_130min:
2612:    if not args.skip_download and not args.only_aggregate_130min:
2634:    elif args.skip_download:
2731:            "skip_download": args.skip_download or args.only_aggregate_130min or (symbol in refresh_daily_terminated_symbols),
2736:    if args.parallel and download_workers_resolved > 1 and not args.skip_download and not args.only_aggregate_130min:
2770:        rebuild = bool(args.overwrite or args.refresh_latest or args.skip_download or args.only_aggregate_130min or raw_status in {"downloaded", "refreshed"})
2919:        "daily_requested_count": 0 if args.skip_download or args.only_aggregate_130min else len(symbols),
2921:        "raw_5min_requested_count": 0 if args.skip_download or args.only_aggregate_130min else len(symbols),
```

Every remaining reference is either the CLI-level `args.skip_download`/`args.only_aggregate_130min` flags (user-controlled, already guarded) or the new registry-driven `refresh_daily_terminated_symbols` set at line 2731. No symbol literal remains.

```
$ grep -n 'symbol == "' testdata/vericekici_updated.py
2949:            role = "PRIMARY_BENCHMARK" if symbol == "QQQ" else "SECONDARY_BENCHMARK" if symbol == "SPY" else "CANDIDATE"
```

The one remaining `symbol == "..."` comparison is unrelated to skip/acquisition logic — it tags `QQQ`/`SPY` as benchmark roles for manifest reporting (an always-true structural fact about the frozen universe, not a data-availability exception), untouched by this fix and out of scope.

---

## EVIDENCE

### Full test suite

```
Ran 651 tests in 280.560s
OK
```
Log: `scratchpad/phase3a_exception_registry_suite.log`.

**Reconciliation:** prior remediation (`phase3a_remediation_20260718.md`) established 646 tests (640 Phase-3A actual + 6 tests from that remediation). This task adds exactly 5 new tests (`TestRefreshDailyTerminatedSymbolsRegistryDriven`). 646 + 5 = **651**, matching the actual run exactly. No new discrepancy introduced.

The Phase-3A-original +1 test-count discrepancy (637 baseline + 2 claimed-added ≠ 640 actual) remains **open, not explained** by this task — it predates both this fix and the prior remediation, and nothing in this task's diff touches the commit (`1d5933b`) or files where that arithmetic originated. Restated here rather than papered over.

### git status / git log

```
$ git status --porcelain
 M .gitignore                                          <- pre-existing, unrelated, not touched
?? reports/audit/phase2f_parity_deep_trace_20260718.md  <- pre-existing untracked Phase 2F artifact, not touched
?? scripts/phase2f_selection_snapshots.py               <- pre-existing untracked Phase 2F artifact, not touched
```
(This report itself is untracked until its own follow-up commit.) Both files this task modified are committed; no other working-tree changes.

```
$ git log --oneline -3
e58d8c6 fix: replace hardcoded BK ticker check with exception-registry lookup
f6805b6 docs: Phase 3A remediation report
744e33e fix: revert rs_v2_clean validator relaxation and close --refresh-daily live-guard bypass
```

---

## Stop

Per task instructions: no GO/NO-GO conclusion is offered here. Stopping after reporting.
