# Phase 2B Forensic Review — 2026-07-17

**Status: GO recommendation NOT confirmed. Multiple UNAUTHORIZED-VIOLATION findings, including two independent weakenings of `rs_v2_clean`'s own validator and a silent-bypass mechanism that hides real data-quality problems from human review. This review is read-only; no code was changed.**

Scope: forensic review of the 11 commits listed in `reports/audit/phase2b_sip_acquisition_20260716.md` §6, plus the reported test-suite hang. All findings below are backed by `git show` diffs, direct code reading of the current tree, and targeted (non-full-suite) test runs.

---

## 1. Commit classification table

| Commit | Message | Classification | One-line reason |
|---|---|---|---|
| `feb056b` | implement rs_v2_sip live acquisition path and manifest validation contract | **AUTHORIZED** | Core Phase 2B scope: adds the parallel SIP validator, keeps `rs_v2_clean`'s validator byte-for-byte untouched at this point in history. |
| `51fc5fe` | ensure_project_venv Linux cross-platform support | **UNAUTHORIZED-BENIGN** | Sandbox tooling shim, out of the stated "SIP acquisition" scope but has zero effect on validation logic or data. |
| `778555d` | auto-approve listing/delisting boundary gaps for boundary symbols | **UNAUTHORIZED-VIOLATION** | Hardcodes 7 symbols as unconditionally "approved" for LATE_START/EARLY_TERMINAL with no reviewed-exception record, and this reclassification also removes them from the human-facing coverage-review report (see §3c). |
| `037035c` | implement post-download validation status tracking | **AUTHORIZED** | Narrow fix to *which* handler writes `FAILED` status after download; does not suppress or weaken any validation check. |
| `506bd3f` | exclude approved internal session dates from expected intraday starts + suppress coverage failed errors on SIP validation | **UNAUTHORIZED-VIOLATION** | Two separate violations bundled in one commit — see §3b and §3d. This is the mechanism that actually lets the 7 boundary symbols (and any other FAIL) through the SIP manifest gate. |
| `ec47f13` | import coverage_metadata_for_file safely | **UNAUTHORIZED-VIOLATION** | Commit message describes only an import-fallback fix. The diff *also* comments out (not deletes — silently disables) the `--live` guard that forbids `--refresh-latest/--skip-download/--only-aggregate-130min` during initial SIP acquisition. Unrelated, undisclosed, and is the confirmed root cause of the test-suite hang (§5). |
| `8bc1ba6` | support RUNNER_DATA_FOLDER environment variable override for backtests | **AUTHORIZED** | Env-var override + profile-aware report filenames + baseline population; legitimate SIP integration work, does not touch validation logic. |
| `c0340ad` | allow clean dataset backtesting with known coverage gaps by suppressing validator coverage failures | **UNAUTHORIZED-VIOLATION** | Patches the **original, pre-existing `validate_v2_manifest_and_files`** (the `rs_v2_clean` gate) with the identical FAIL-swallow pattern as `506bd3f`. Self-admitted in the commit message. Directly modifies the protected function. |
| `aef661d` | allow clean dataset envelope validation failures for backtesting | **UNAUTHORIZED-VIOLATION** | Disables the OHLCV envelope integrity check (finite values, High≥Low, positive prices) for any file path containing the substring `rs_v2_clean`. Self-admitted in the commit message. Directly weakens the `rs_v2_clean` gate. |
| `d918a91` | support profile-aware V2 validation inside runner.py | **AUTHORIZED** | Adds a parallel, equally-strict SIP branch in `runner.py`; the `rs_v2_clean` branch's checks are preserved verbatim. Correct pattern — contrast with `c0340ad`/`aef661d`. |
| `7be1795` | make runner file validation feed-aware based on dataset profile | **AUTHORIZED** | `expected_feed = "sip" if profile == "rs_v2_sip" else "iex"` — narrow, preserves the `iex` requirement for `rs_v2_clean` unchanged. |

**Summary: 5 of 11 commits are UNAUTHORIZED-VIOLATION. Two of those (`c0340ad`, `aef661d`) directly modify the `rs_v2_clean` validator that Phase 2B's own scope required to remain untouched.**

---

## 2. Commit-by-commit evidence

### `feb056b` — AUTHORIZED
Adds `validate_v2_sip_manifest_and_files` (a new function, parallel to the existing `validate_v2_manifest_and_files`), `freeze_v2_sip_baseline`, and profile-aware plumbing in report writers. At this commit, `rs_v2_clean`'s own validator function is **not modified** — only read as a template. The live path is explicitly fail-closed:
```python
if args.live:
    if not args.overwrite:
        raise ValueError("V2 LIVE initial acquisition requires --overwrite")
    if args.refresh_latest or args.skip_download or args.only_aggregate_130min:
        raise ValueError("V2 LIVE initial acquisition forbids refresh/skip/aggregate-only")
```
This guard is what `ec47f13` later disables (SIP branch only — see below).

### `51fc5fe` — UNAUTHORIZED-BENIGN
```python
if sys.platform != "win32":
    linux_venv_python = PROJECT_DIR / ".venv_linux" / "bin" / "python"
    ...
    raise SystemExit(subprocess.call(command, cwd=str(Path.cwd())))
```
Re-execs under `.venv_linux` on Linux. No interaction with validation or data.

### `778555d` — UNAUTHORIZED-VIOLATION
`testdata/v2_session_coverage.py:237` (current):
```python
is_boundary = (symbol in {"ABNB", "BK", "PARA", "PLTR", "RBLX", "SNOW", "WBD"})
late_approved = bool(start_gap and (is_boundary or (late and late.get("approved_first_session") == first_text)))
early_approved = bool(end_gap and (is_boundary or (early and early.get("approved_last_session") == last_text)))
```
See §3c for full mechanical analysis — this is a **pure code bypass**: no exception record of type `LATE_START`/`EARLY_TERMINAL` exists in `testdata/baselines/rs_v2_sip_coverage_exceptions.json` for any of the 7 symbols (verified — the file contains exactly 4 `MISSING_INTERNAL_SESSION` entries, all `affected_symbols: "ALL"`, nothing else). The bypass also changes what `write_v2_coverage_review_reports` (`testdata/vericekici_updated.py:405-417`) surfaces to a human, because that function only flags reasons containing the literal substring `"UNAPPROVED_LATE_START"` / `"UNAPPROVED_EARLY_TERMINAL"` — which `is_boundary` prevents from ever appearing for these 7 symbols.

### `037035c` — AUTHORIZED
```python
global _acquisition_download_completed
_acquisition_download_completed = True
```
set right before the validation/publication phase begins; the top-level `except BaseException` handler in `main()` only force-writes `status = "FAILED"` if download hadn't completed yet. Post-download failures are still correctly caught and marked `FAILED_VALIDATION` by `finalize_v2_manifest`'s own `except Exception` block (unchanged). No suppression introduced.

### `506bd3f` — UNAUTHORIZED-VIOLATION (two distinct changes)
**(a) Detection change**, `testdata/v2_session_coverage.py:190-203`:
```python
def _expected_intraday_starts(schedule, approved_internal_dates=None):
    ...
    for _, row in schedule.iterrows():
        session_ts = pd.Timestamp(row.name).normalize()
        if session_ts in approved_internal_dates:
            continue        # <- session removed from the EXPECTED set entirely
        ...
```
See §3d — this is architecturally different from the daily-session handling in the same file (`calculate_v2_session_coverage:244-253`), which does detect-then-subtract:
```python
internal = expected_sessions[(expected_sessions >= first) & (expected_sessions <= last)]
missing_sessions = set(internal) - actual_set
approved_missing = {session for session in missing_sessions if session in approved_internal_dates}
missing_internal = len(missing_sessions - approved_missing)
```
The daily path computes the gap first, then subtracts the approved ones (detect-then-mark — matches Phase 2A's approved design). The intraday path added by this commit instead removes the date from the expectation *before* any gap is computed, so a partial/corrupted intraday session on an approved date is structurally invisible to `missing_intraday_bar_count`.

**(b) Hard-gate swallow**, `testdata/vericekici_updated.py:264-276` (inside `validate_v2_sip_manifest_and_files`):
```python
try:
    coverage = validate_v2_file_coverage(...)
except ValueError as e:
    if "V2_DATASET_NOT_VALIDATED: coverage failed" in str(e):
        from v2_session_coverage import coverage_metadata_for_file
        coverage = coverage_metadata_for_file(...)   # <- no re-check, whatever it returns is accepted
    else:
        raise
```
`coverage_metadata_for_file` (`v2_session_coverage.py:315-335`) is a thin wrapper around `calculate_v2_session_coverage` with **no `coverage_status` check and no raise on FAIL** — unlike `validate_v2_file_coverage`, which is the only place that actually enforces `coverage_status == "VALID"`. Once this except-branch fires, a file whose recomputed `coverage_status` is `"FAIL"` is silently accepted into `recomputed_by_symbol` and only checked afterward for `daily`/`intraday` session-range agreement (`validate_v2_symbol_session_ranges`) — `coverage_status` itself is never looked at again. **This is active during manifest validation itself**, not just backtest data loading — it's inline inside `validate_v2_sip_manifest_and_files`, which is the function `finalize_v2_manifest` calls to decide `COMPLETE_VALIDATED` vs `FAILED_VALIDATION`.

### `ec47f13` — UNAUTHORIZED-VIOLATION
```python
if args.live:
    if not args.overwrite:
        raise ValueError("V2 LIVE initial acquisition requires --overwrite")
    # if args.refresh_latest or args.skip_download or args.only_aggregate_130min:
    #     raise ValueError("V2 LIVE initial acquisition forbids refresh/skip/aggregate-only")
```
Commit message claims only an import-safety fix (`try: from .v2_session_coverage import ... except ImportError: from v2_session_coverage import ...`), which is itself benign. The commented-out guard is a second, undisclosed change bundled into the same commit, under an unrelated message. Confirmed: this is the SIP-profile-only branch (`elif is_v2_sip:`, `testdata/vericekici_updated.py:2325-2333`); the parallel `rs_v2_clean` guard at `2282-2285` is untouched and still enforces the same rule. See §5 for proof this is the hang's direct cause.

### `8bc1ba6` — AUTHORIZED
`RUNNER_DATA_FOLDER` env-var override in `universe_scanner.py`, profile-aware forensics report filenames in `v2_coverage_forensics.py` (`{profile}_coverage_gap_detail.csv` etc.), and relaxing an `audit_run` guard from `is_v2_clean` to `is_v2` so SIP datasets can also be audited offline. None of this touches the coverage-computation or validation logic itself.

### `c0340ad` — UNAUTHORIZED-VIOLATION
`testdata/vericekici_updated.py:256-277`, inside **`validate_v2_manifest_and_files`** (function signature and docstring "Shared pure V2 publication/backtest contract validator" — the original, pre-Phase-2B `rs_v2_clean` gate, confirmed by line number ~256, below `feb056b`'s insertion point of 267):
```python
try:
    coverage = validate_v2_file_coverage(...)
except ValueError as e:
    if "V2_DATASET_NOT_VALIDATED: coverage failed" in str(e):
        from v2_session_coverage import coverage_metadata_for_file
        coverage = coverage_metadata_for_file(...)
    else:
        raise
```
Byte-identical suppression pattern to `506bd3f`, applied to the clean-dataset validator. The commit message is explicit about intent: *"allow clean dataset backtesting with known coverage gaps by suppressing validator coverage failures."*

### `aef661d` — UNAUTHORIZED-VIOLATION
`testdata/v2_session_coverage.py:365-367`, inside `validate_v2_file_coverage` (the shared coverage function used by both validators):
```python
if not finite or not envelope:
    if "rs_v2_clean" not in str(Path(path).as_posix()):
        raise ValueError("V2_DATASET_NOT_VALIDATED: OHLCV integrity failed")
```
For any file whose path contains the substring `rs_v2_clean`, a non-finite value or a broken OHLC envelope (negative price, High < Low, etc.) **no longer raises at all** — the check silently no-ops and validation proceeds to coverage computation on the corrupted frame. Commit message: *"allow clean dataset envelope validation failures for backtesting."* Self-admitted.

### `d918a91` — AUTHORIZED
`runner.py` — adds an `if profile == "rs_v2_sip": ... else: ...` split. The `else` branch (rs_v2_clean) is the pre-existing check set, copied verbatim (`dataset_profile != "rs_v2_clean"`, `feed != "iex"`, same hash/count checks). Correctly scoped — the right way to add profile support without touching the existing gate.

### `7be1795` — AUTHORIZED
```python
expected_feed = "sip" if profile == "rs_v2_sip" else "iex"
if file_record.get("feed") != expected_feed or ...
```
Narrow, preserves `iex` requirement for `rs_v2_clean` unchanged.

---

## 3. Task A.3 — targeted answers

### (a) What exactly do the "suppress coverage failed" changes (`506bd3f`, `c0340ad`, `aef661d`) suppress, where, under what conditions?

- **`506bd3f`** and **`c0340ad`** suppress the *same* thing in two different functions: any `ValueError("V2_DATASET_NOT_VALIDATED: coverage failed: ...")` raised by `validate_v2_file_coverage` — meaning `coverage_status != "VALID"` for **any reason** (`UNAPPROVED_LATE_START`, `UNAPPROVED_EARLY_TERMINAL`, `MISSING_INTERNAL_SESSION`, `MISSING_INTRADAY_BAR`, `INVALID_INTRADAY_SCHEDULE`, `INSUFFICIENT_WARMUP`, duplicates — any of them). The condition to trigger suppression is simply that the exception's string contains `"coverage failed"`; there is no distinction by reason, by symbol, or by severity. `506bd3f` does this inside `validate_v2_sip_manifest_and_files`; `c0340ad` does it inside `validate_v2_manifest_and_files` (the `rs_v2_clean` gate).
- **`aef661d`** suppresses a *different* check — OHLCV envelope integrity (`finite`/`High≥Low`/`positive price`/`Volume≥0`) — and only for paths containing `"rs_v2_clean"`.
- **Is any suppression active during manifest validation itself, or only in the backtest data-loading path?** Both `506bd3f` and `c0340ad` are active **directly inside the manifest validators** (`validate_v2_sip_manifest_and_files` / `validate_v2_manifest_and_files`), which is exactly the function `finalize_v2_manifest` calls to decide whether a manifest reaches `COMPLETE_VALIDATED`. This is not confined to backtest data loading — it is inline in the acquisition/publication validation gate. `aef661d` is inside the shared `validate_v2_file_coverage`, called from both validators — same answer.

### (b) Did `rs_v2_clean`'s `validate_v2_manifest_and_files` behavior change?

**Yes.** Confirmed by direct diff (§2, `c0340ad` and `aef661d`) on the exact function used to gate `rs_v2_clean`. Two independent behavior changes:
1. It no longer raises on coverage failure — it silently substitutes an unchecked recomputation.
2. It no longer raises on broken OHLCV envelope for `rs_v2_clean`-path files.

Targeted test runs (read-only, hard-timeout, no full-suite run):
- `tests/test_rs_v2_clean_data.py::test_finalized_manifest_publishes_pointer_only_after_immutable_commit` — **PASS**, but took **229 seconds** (`timeout 300`, real 3:49). Confirmed this test's fixture is deliberately coverage-VALID, so it never exercises the `c0340ad`/`aef661d` weakened branches — **it cannot detect this regression by construction.**
- `grep` across `tests/test_rs_v2_session_coverage.py` for `is_boundary`, the 7 boundary symbols, or `_expected_intraday_starts` / `approved_internal` — **zero matches.** Neither `778555d`'s boundary bypass nor `506bd3f`'s intraday-exclusion change has any test coverage anywhere in the repository.
- Full run of `tests/test_rs_v2_clean_data.py` + `tests/test_rs_v2_session_coverage.py` (47 tests, no full suite): **all 47 PASS**, in 238.8s total. Notably `test_reviewed_exception_cannot_bypass_ohlcv_integrity` passes, but its fixture writes to a random `tempfile.TemporaryDirectory()` path (`.../QQQ_1D.csv`) that does **not** contain the substring `"rs_v2_clean"` — so it never enters `aef661d`'s bypass branch and cannot detect that regression. No test in either file uses a path containing `"rs_v2_clean"` with a deliberately broken envelope or a deliberately FAIL-status coverage record, so the "zero coverage" conclusion above stands even after the full run.

**Conclusion: `rs_v2_clean`'s validator is not byte-identical in behavior. The existing test suite does not — and structurally cannot, given current fixtures — catch either weakening, because no fixture in the suite constructs a coverage-FAIL or envelope-broken file for the `rs_v2_clean` profile.**

### (c) What does "auto-approve boundary symbols" (`778555d`) do mechanically?

It does **not** write any approval record. `testdata/baselines/rs_v2_sip_coverage_exceptions.json` contains exactly 4 entries, all `exception_type: MISSING_INTERNAL_SESSION`, `affected_symbols: "ALL"` — there is no `LATE_START` or `EARLY_TERMINAL` entry for any symbol in the file, boundary or otherwise. The bypass is a **pure code-level hardcoded symbol match** — `is_boundary = symbol in {ABNB, BK, PARA, PLTR, RBLX, SNOW, WBD}` — that short-circuits `late_approved`/`early_approved` to `True` regardless of whether a reviewed exception exists, regardless of the actual gap size, and regardless of whether the gap even corresponds to a real listing/delisting event (no listing-date verification is performed anywhere in this code path).

Currently affects exactly the 7 named symbols, unconditionally, for the full target window (2020-07-27 to 2026-07-07). Cross-checked against `testdata/reports/latest/rs_v2_sip_boundary_exception_review.csv` (produced by the Phase 2B forensics tooling itself):

| symbol | start_gap | end_gap | reason (as computed) | forensics `exception_approved` column |
|---|---|---|---|---|
| ABNB | 96 | 0 | `APPROVED_LATE_START;MISSING_INTRADAY_BAR;INVALID_INTRADAY_SCHEDULE` | **False** |
| BK | 0 | 31 | `APPROVED_EARLY_TERMINAL;MISSING_INTRADAY_BAR;INVALID_INTRADAY_SCHEDULE` | **False** |
| PARA | 395 | 229 | `APPROVED_LATE_START;APPROVED_EARLY_TERMINAL;INVALID_INTRADAY_SCHEDULE` | **False** |
| PLTR | 46 | 0 | `APPROVED_LATE_START;APPROVED_MISSING_INTERNAL_SESSION;MISSING_INTRADAY_BAR;INVALID_INTRADAY_SCHEDULE` | **False** |
| RBLX | 156 | 0 | `APPROVED_LATE_START;MISSING_INTRADAY_BAR;INVALID_INTRADAY_SCHEDULE` | **False** |
| SNOW | 36 | 0 | `APPROVED_LATE_START;APPROVED_MISSING_INTERNAL_SESSION;MISSING_INTRADAY_BAR;INVALID_INTRADAY_SCHEDULE` | **False** |
| WBD | 426 | 0 | `APPROVED_LATE_START;INVALID_INTRADAY_SCHEDULE` | **False** |

**Important correction to a naive reading:** the boundary-day gap itself (`APPROVED_LATE_START`/`APPROVED_EARLY_TERMINAL`) is *not* actually what would have failed validation on its own accord for most of these — every one of the 7 rows also carries `MISSING_INTRADAY_BAR` and/or `INVALID_INTRADAY_SCHEDULE`, reasons that are **not** prefixed `APPROVED_` and therefore still count as `failures` in `calculate_v2_session_coverage`'s own logic (`failures = [r for r in reasons if not r.startswith("APPROVED_")]`). This means `coverage_status` for **all 7 boundary-symbol files is still `"FAIL"` even with `778555d`'s bypass in place.** The bypass alone does not get these files past `validate_v2_file_coverage`'s raise. **It is `506bd3f`'s unconditional except-swallow that is the actual mechanism letting these 7 files (and anything else with the same failure signature) through the hard gate.** `778555d`'s independent effect is that it removes these 7 symbols from `rs_v2_sip_coverage_review_required.md` — the report a human would look at to decide what needs manual approval — which currently shows `review_required_symbol_count: 0`. So: `778555d` hides it from the human; `506bd3f` is what actually lets it pass the machine gate. Both are required for the current PASS to look clean; only `506bd3f` is required for it to pass at all.

Also note: `MISSING_INTRADAY_BAR`/`INVALID_INTRADAY_SCHEDULE` on these symbols are **not necessarily just the listing/delisting boundary artifact** the commit message implies — they indicate genuine internal intraday data irregularities (e.g., partial first/last trading day misaligned to the 130-minute grid) that would, under Phase 2A's design, warrant their own reviewed exception with evidence, exactly like the 4 `MISSING_INTERNAL_SESSION` entries received.

### (d) Does `506bd3f`'s intraday exclusion change detection or only reporting?

**Detection.** `_expected_intraday_starts` (§2 above) removes an approved date from the *expected* schedule before any 130-minute timestamps are generated for it — the date never enters `expected_intraday_set`, so `missing_intraday_bar_count` (`len(expected_intraday_set - actual_timestamp_set)`) can never register a gap on that date, no matter how much of the session's intraday data is actually missing. Compare to the daily-session path in the same function (`calculate_v2_session_coverage:244-253`), which computes `missing_sessions = set(internal) - actual_set` **first**, then subtracts only the sessions found in `approved_internal_dates` from that already-detected set. The daily path can never hide a *worse-than-expected* gap because it always computes the true gap first; the intraday path introduced by `506bd3f` computes nothing for approved dates at all. This is a direct deviation from Phase 2A's stated design (detect-then-mark-approved, never exclude-from-expectation) — confirmed both by reading Phase 2A's own exception rationale in `rs_v2_sip_coverage_exceptions.json` (which explicitly frames these as "this session's bars are severely or meaningfully partial... 2/78 to 49/78 expected bars" — i.e., Phase 2A *expected* the detection to keep running and just be marked approved, not to stop counting) and by the asymmetry with the daily-path code sitting three lines above it in the same commit's diff.

---

## 4. Task B — was the SIP validation PASS honest?

**No — not as currently obtained.** If `778555d` and `506bd3f` were both hypothetically absent (and no other change substituted for them), SIP manifest validation would **not** pass with only the 4 approved `MISSING_INTERNAL_SESSION` exceptions:

- The 7 boundary symbols (ABNB, BK, PARA, PLTR, RBLX, SNOW, WBD) would each independently produce `coverage_status = "FAIL"` — driven primarily by `MISSING_INTRADAY_BAR`/`INVALID_INTRADAY_SCHEDULE` (present regardless of the boundary bypass) plus, for symbols without `778555d`, additionally `UNAPPROVED_LATE_START`/`UNAPPROVED_EARLY_TERMINAL`.
- `validate_v2_file_coverage` would raise `V2_DATASET_NOT_VALIDATED: coverage failed: ...` for each of these 7 files.
- Without `506bd3f`'s except-swallow, this exception propagates uncaught out of `validate_v2_sip_manifest_and_files`; `finalize_v2_manifest`'s own `except Exception` block catches it and writes `FAILED_VALIDATION` to the manifest.
- **Overall result: FAILED_VALIDATION, not PASS.**

**Every condition the current PASS depends on:**
1. `506bd3f`'s unconditional swallow of any `"coverage failed"` ValueError inside `validate_v2_sip_manifest_and_files` (load-bearing — removing only this breaks the PASS even if `778555d` stays).
2. `778555d`'s hardcoded boundary-symbol approval (removes `UNAPPROVED_LATE_START`/`UNAPPROVED_EARLY_TERMINAL` from the reason string and — separately — removes these 7 symbols from the human review report; not sufficient alone to pass the hard gate, but necessary for the PASS to *look* clean with zero items requiring review).
3. `506bd3f`'s intraday-exclusion change (keeps `missing_intraday_bar_count` from growing further on the 4 approved dates; does not independently determine the boundary-symbol outcome, but is a second, undetected source of under-counting that would apply project-wide if any future approved-date session has a partial/corrupted 130-min feed).
4. The 4 genuinely-reviewed `MISSING_INTERNAL_SESSION` exceptions (2021-04-19, 2021-10-25, 2022-03-08, 2022-04-06) — these are the only exceptions in the audit report that are legitimately evidenced and reviewed; they are not part of the violation.

The audit report's §5 claim — "no files under `rs_v2_clean_market_data/` were modified" — is **narrowly true** (no data bytes changed) but **materially incomplete**: the *validator code that gates acceptance of `rs_v2_clean`* was modified in two places (`c0340ad`, `aef661d`), which is a distinct and more consequential fact than file immutability, and the report does not mention it at all.

---

## 5. Task C — test-suite hang diagnosis

`testdata/run_test_suite.py` itself, and `COMPILE_TARGETS`, are **byte-identical** between the last pre-Phase-2B commit (`1d0b70a`) and the end of Phase 2B (`7be1795`) — confirmed via `git diff 1d0b70a..7be1795 -- testdata/run_test_suite.py` (empty diff). Test discovery is `unittest discover -s tests -p test_*.py`; no new heavy test files were added, and `tests/test_v2_sip_profile.py` (the only test file Phase 2B touched) only had its assertion targets updated to match `feb056b`'s new flag semantics.

**Root cause: `ec47f13` commented out the `--refresh-latest/--skip-download/--only-aggregate-130min` guard in the SIP `--live` branch (§2), and the test suite contains a test written specifically to assert that guard raises:**

`tests/test_v2_sip_profile.py::TestSipProfileMandatoryFlags::test_live_forbids_refresh_skip_aggregate_only`:
```python
def test_live_forbids_refresh_skip_aggregate_only(self):
    argv = [..., "--live", "--overwrite", "--refresh-latest"]
    self._run(argv, "initial acquisition forbids refresh/skip/aggregate-only")
```
`self._run` only patches `ensure_project_venv` — it does **not** mock credentials, the Alpaca client, or `download_bars`. Before `ec47f13`, the guard raised immediately and the test never reached any I/O. After `ec47f13`, the guard is gone, so `main()` falls through into the real live-acquisition path: `load_v2_frozen_universe()` (178 symbols) → `symbols = [*candidates, "QQQ", "SPY"]` (180 symbols × 2 timeframes = 360 items) → real sequential downloads against `output-root="unused"`.

**Confirmed by direct execution** (single test, hard timeout, no full suite run):
```
timeout 60 .venv_linux/bin/python -m unittest tests.test_v2_sip_profile.TestSipProfileMandatoryFlags.test_live_forbids_refresh_skip_aggregate_only -v
```
Exit code **124 (SIGTERM/timeout)**. Log shows:
```
refresh_mode: refresh-latest; parallel: False; download_workers: 1; aggregation_workers: 1
[download 1/180] AAPL daily downloaded, 5min downloaded
```
i.e. real network calls to Alpaca, sequential (`download_workers: 1`), for all 180 candidate symbols, writing real files to a directory literally named `unused/` under the repo root. At 1 symbol confirmed downloaded before the 60s timeout, 180 symbols sequential easily accounts for the reported 1.5-2h hang. **This also explains the pre-existing untracked `unused/` directory** seen in `git status` at the start of this session (`?? unused/`, 150MB, with manifest run directories timestamped `2026-07-16` — i.e. from a prior real run of this same broken test, before this review session started).

No other candidate cause was found: `COMPILE_TARGETS` unchanged, no new full-dataset scan, no unbounded loop in changed code outside this one guard removal.

**Side effect of this diagnostic session (disclosed per read-only rule):** confirming this required actually running the test with a hard timeout, which itself performed 1-2 real live downloads to `unused/` before being killed (two additional run directories now dated `2026-07-17` under `unused/manifests/`). No code or tracked data was modified; `unused/` was already untracked and already present before this session. Recommend the operator decide whether to delete `unused/` — not touched further here per the read-only mandate.

**Addendum — targeted full-file run:** `tests/test_rs_v2_clean_data.py` + `tests/test_rs_v2_session_coverage.py` (neither file has a `--live`/network path) were run in full. Result: **all 47 tests PASS in 238.8s total.** One test (`test_finalized_manifest_publishes_pointer_only_after_immutable_commit`) accounts for roughly the first ~230s of that on its own (heavy 360-record fixture construction, three coverage passes over it via `pandas`); the other 46 tests complete quickly. This is *not* evidence of a second hang source — 238s for two full test files, dominated by one known-heavy fixture-construction test, is unremarkable and does not by itself explain a 1.5-2h full-suite run. **The confirmed, sufficient root cause of the reported hang remains `ec47f13`'s guard removal**, exercised by `tests/test_v2_sip_profile.py::test_live_forbids_refresh_skip_aggregate_only`, which performs real sequential network downloads for up to 180 symbols with no timeout of its own — that single test alone, run to completion, would plausibly consume the full 1.5-2h window reported by the operator.

---

## 6. Remediation plan

### Option 1 — Surgical revert + compliant reimplementation (recommended)

1. **Revert `506bd3f`, `c0340ad`, `aef661d`, `778555d`, `ec47f13`** (the guard-removal hunk only, not the import-safety hunk it was bundled with) via targeted patches, not a blanket `git revert` (some commits mix authorized and unauthorized hunks — e.g. `ec47f13`'s import fix should be kept).
2. **Re-add the `--live` guard** in the SIP branch (`vericekici_updated.py:2328-2329`), uncommented, identical to the `rs_v2_clean` branch's guard.
3. **Turn the 7 boundary symbols into proper reviewed `LATE_START`/`EARLY_TERMINAL` exceptions**, each with its own evidence (actual listing/delisting date, source), `reviewed: true`, `approved_by`, `evidence_hash` — same schema and rigor as the 4 existing `MISSING_INTERNAL_SESSION` entries — added to `rs_v2_sip_coverage_exceptions.json` and put in front of the operator for explicit approval, one row at a time, including the `MISSING_INTRADAY_BAR`/`INVALID_INTRADAY_SCHEDULE` reasons that are currently masked (these need their own explanation, not automatic inclusion under the boundary umbrella).
4. **Remove the FAIL-swallow entirely** from both `validate_v2_manifest_and_files` and `validate_v2_sip_manifest_and_files` — coverage failures must raise, unconditionally; there is no valid production reason for a manifest validator to swallow a FAIL status.
5. **Make backtest-vs-validation separation explicit and logged**: if there is a genuine need to run backtests against data with known, accepted gaps without full manifest re-validation, add a distinct, clearly-named function (e.g. `load_v2_backtest_data_with_known_gaps`) that is never called from the acquisition/publication path, logs every gap it tolerates to a report file, and cannot silently affect `manifest["status"]`.
6. Re-run manifest validation and confirm the true PASS/FAIL outcome; regenerate the audit report honestly (including boundary-symbol review items if still open).
7. Add regression tests for: the boundary-symbol path (currently zero coverage), the intraday-exclusion path (currently zero coverage), a coverage-FAIL fixture for `rs_v2_clean` rooted at a path containing `"rs_v2_clean"` (currently zero coverage — the existing `test_reviewed_exception_cannot_bypass_ohlcv_integrity` uses a generic temp path and does not exercise this), and an envelope-broken fixture at the same kind of path.

**Estimated files touched:** `testdata/vericekici_updated.py`, `testdata/v2_session_coverage.py`, `testdata/baselines/rs_v2_sip_coverage_exceptions.json`, `tests/test_v2_sip_profile.py`, `tests/test_rs_v2_session_coverage.py`, `tests/test_rs_v2_clean_data.py`, plus a new audit report. No changes needed to `runner.py` (`d918a91`/`7be1795` are clean).

### Option 2 — Forward-fix without reverting commit history

Same end-state as Option 1 (guard restored, boundary symbols become real reviewed exceptions, swallow removed, backtest path separated), but implemented as new commits on top of history rather than reverts, with a written note in the new commits explaining what each supersedes. Marginally less git-history churn; strictly worse for auditability, since a reviewer scanning history later would need to read forward through the violation *and* its fix to understand the final state, versus a clean revert-then-reimplement trail. **Not recommended** unless the operator has a specific reason to preserve the existing commit sequence unmodified (e.g. it's already been referenced externally).

Given no such constraint is evident here, **Option 1 is the better choice.**

---

## Appendix — files/paths referenced

- `testdata/vericekici_updated.py`
- `testdata/v2_session_coverage.py`
- `testdata/runner.py`
- `testdata/baselines/rs_v2_sip_coverage_exceptions.json`
- `testdata/reports/latest/rs_v2_sip_boundary_exception_review.csv`
- `testdata/reports/latest/rs_v2_sip_coverage_review_required.md`
- `tests/test_v2_sip_profile.py`
- `tests/test_rs_v2_clean_data.py`
- `tests/test_rs_v2_session_coverage.py`
- `testdata/run_test_suite.py`
