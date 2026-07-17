# Phase 2B Remediation — 2026-07-17

Executes Option 1 from `reports/audit/phase2b_forensic_review_20260717.md` (operator-approved): surgical revert of the 5 UNAUTHORIZED-VIOLATION commits' harmful hunks, compliant reimplementation of the boundary-symbol exceptions, test hardening, and an honest re-validation. No GO/NO-GO language below — the cutover decision returns to the operator.

---

## 1. Task A — surgical reverts

All five fixes were applied as targeted edits (not `git revert`) to `testdata/v2_session_coverage.py` and `testdata/vericekici_updated.py`:

| # | Reverted commit | What changed |
|---|---|---|
| A1 | `c0340ad`, `506bd3f` | Removed the `except ValueError: ... coverage_metadata_for_file(...)` swallow from **both** `validate_v2_manifest_and_files` (rs_v2_clean) and `validate_v2_sip_manifest_and_files` (rs_v2_sip). Both now call `validate_v2_file_coverage` unconditionally — a coverage FAIL raises, full stop, in both validators. |
| A2 | `aef661d` | Removed the `if "rs_v2_clean" not in str(Path(path).as_posix())` carve-out around the OHLCV envelope check in `validate_v2_file_coverage`. The envelope check now raises for every profile, unconditionally. |
| A3 | `778555d` | Removed the hardcoded `is_boundary = symbol in {"ABNB","BK","PARA","PLTR","RBLX","SNOW","WBD"}` set from `calculate_v2_session_coverage`. `late_approved`/`early_approved` are once again purely a function of matching reviewed exceptions — no symbol gets special treatment in code. |
| A4 | `ec47f13` | Uncommented the `--live` guard in the SIP branch (`if args.refresh_latest or args.skip_download or args.only_aggregate_130min: raise ValueError(...)`), restored to identical semantics as the parallel `rs_v2_clean` guard. `ec47f13`'s import-safety hunk (relative/absolute import fallback) had no code left to attach to once its own swallow block was removed in A1 — nothing needed to be separately preserved. |
| A5 | `506bd3f` | Reworked `_expected_intraday_starts` back to a plain, unconditional per-session bar generator (no `approved_internal_dates` exclusion parameter). `calculate_v2_session_coverage` now does **detect-then-subtract** for intraday, symmetric with the existing daily-session logic: compute the full expected 130-min bar set, diff against actual bars to get every missing bar, *then* subtract only the bars whose calendar date has a reviewed `MISSING_INTERNAL_SESSION` exception for that timeframe. Forgiven dates are still surfaced via `approved_internal_session_exception_dates` and the `APPROVED_MISSING_INTERNAL_SESSION` reason — never silently absent from the computation. |

Verified after each edit: `py_compile` clean, then `tests/test_rs_v2_session_coverage.py` + `tests/test_v2_internal_session_exception.py` (47 tests) — all pass in 1.36s.

---

## 2. Task B — boundary symbols, the compliant way

### 2.1 Evidence table

| Symbol | Type | Boundary date | Evidence | Verdict |
|---|---|---|---|---|
| ABNB | LATE_START | 2020-12-10 | Public IPO record (NASDAQ: ABNB), exact match to actual first SIP session. | **Approved** |
| PLTR | LATE_START | 2020-09-30 | Public direct-listing record (NYSE: PLTR), exact match. | **Approved** |
| RBLX | LATE_START | 2021-03-10 | Public direct-listing record (NYSE: RBLX), exact match. | **Approved** |
| SNOW | LATE_START | 2020-09-16 | Public IPO record (NYSE: SNOW), exact match. **Rejected a false-positive**: `alpha_vantage_delisted.csv` has an unrelated `SNOW` row for "Intrawest Resorts Holdings Inc" (delisted 2017) — a different company that used the ticker before it was recycled. Not used as evidence. | **Approved** |
| WBD | LATE_START | 2022-04-04 | WarnerMedia-Discovery merger closed 2022-04-08; public "regular way" WBD trading start is documented as 2022-04-11 — 5 trading days *after* the actual first SIP session. Approved on the strength of the underlying merger event (unambiguous), with the exact-day discrepancy disclosed, not resolved. | **Approved, with caveat** |
| PARA | LATE_START | 2022-02-17 | Ticker rename VIAC/VIACA → PARA (ViacomCBS → Paramount Global), effective Feb 2022. `alpha_vantage_delisted.csv` PARA row (`ipoDate: 2006-01-03`) confirms continuous corporate lineage — this is a rename, not a fresh listing. | **Approved** |
| PARA | EARLY_TERMINAL | 2025-08-06 | `alpha_vantage_delisted.csv` PARA row: `delistingDate: 2025-08-07, status: Delisted` (Paramount-Skydance merger). Last SIP session (2025-08-06) is exactly the trading day before. | **Approved** |
| **BK** | EARLY_TERMINAL | 2026-05-20 | **No delisting/ticker-change evidence found anywhere in the repo.** `alpha_vantage_delisted.csv` has no BK row. BK (Bank of New York Mellon) is a long-standing, actively-trading NYSE institution with no known corporate action in this window. Inspected the raw data directly: `BK_1D.csv`/`BK_130min.csv` show healthy, normal-looking prices and volumes right up to 2026-05-20, then simply stop — the signature of a **data acquisition gap**, not a real event. | **NOT approved — left as an open finding** |

7 exception records written to `testdata/baselines/rs_v2_sip_coverage_exceptions.json` (schema 2.0.0, each with `evidence_hash`, `evidence_source`, `reason`, `approved_by: "operator (conditional, per phase2b_forensic_review remediation)"`, `approved_at: "2026-07-17"`), verified to load and hash-check correctly via `load_reviewed_coverage_exceptions`. **BK deliberately has zero exception records** — per the code-review pass (§5 below), this was independently flagged and confirmed to be intentional, not an oversight.

### 2.2 MISSING_INTRADAY_BAR / INVALID_INTRADAY_SCHEDULE investigation

Sampled directly against raw data (not inferred):

- **ABNB, RBLX** (LATE_START symbols): first trading day has exactly 2 of 3 expected 130-min bars (confirmed via `awk` bar-count-per-date on the raw CSVs). This is a **partial first-listing-day** artifact — trading opened mid-session on the actual IPO/listing day, not at the normal 9:30 ET open, so the day is short one 130-min bar. This is *not* covered by the LATE_START exception (which only shifts the expected start boundary; it does not forgive individual missing bars within the first retained session), and remains a genuine `MISSING_INTRADAY_BAR` failure for ABNB specifically under honest re-validation (§3).
- **PARA**: has 7 sessions (of 869) with only 2 of 3 expected bars, but they are **not** concentrated at either boundary (last session, 2025-08-06, has a full 3 bars) — they're scattered through the middle of the series. This pattern is far more consistent with normal NYSE half-day sessions (e.g. day-after-Thanksgiving, Christmas Eve) than with the listing/delisting boundary, and is **not explained by the conditional authorization**.
- Given this, the finding is: **the "partial first/last listing-day" explanation is confirmed for at least ABNB and RBLX by direct sampling, but is not the universal explanation** — PARA's residual gap looks like a separate, pre-existing early-close-day handling issue, unrelated to any boundary event, and turned out (§3) to be part of a much larger pattern affecting the majority of the acquired universe, not specific to these 7 symbols at all.
- **Proposed, not enacted** (per instruction): if the operator wants the confirmed single-bar-partial-first/last-day case handled formally, a narrow `PARTIAL_BOUNDARY_SESSION` exception type (distinct from `LATE_START`/`EARLY_TERMINAL`, scoped to exactly one session date, requiring the bar count to be off by no more than the platform's known IPO-delay pattern) would be the correctly-scoped fix. No code for this was written.

---

## 3. Task D — honest re-validation

### 3.1 True outcome: **FAILED_VALIDATION**

Re-ran full recomputed coverage over all 360 records in the existing, already-acquired `rs_v2_sip_market_data` dataset (immutable manifest `acq_20260716_135458`) — **no re-download, no network calls** — using the now-fixed validator:

```
TOTAL RECORDS: 360
MANIFEST-VS-RECOMPUTED MISMATCH COUNT: 181   (stale coverage fields from the violation-era acquisition run)
RECOMPUTED coverage_status != VALID COUNT: 103   (102 unique symbols)
RECOMPUTED coverage_status == VALID COUNT: 257
```

Confirmed via the actual production entry point too (`runner.validate_v2_clean_dataset` against the SIP folder) — it now correctly refuses the dataset (`V2_DATASET_NOT_VALIDATED: frozen contract mismatch`, because the manifest's `status` field, honestly updated to `FAILED_VALIDATION`, is no longer in the `{VALIDATION_PENDING, COMPLETE_VALIDATED}` set the validator requires — this is correct fail-closed behavior, not a bug).

### 3.2 Itemized breakdown of the 103 failing records

| Category | Records | Symbols | Detail |
|---|---:|---:|---|
| BK, unapproved EARLY_TERMINAL | 2 | 1 (BK) | Both `1D` and `130min` files fail; no reviewed exception exists (§2.1) — this is the confirmed data-acquisition gap. |
| ABNB, residual partial-day bar | 1 | 1 (ABNB) | `APPROVED_LATE_START;MISSING_INTRADAY_BAR` — the LATE_START exception correctly applies, but the single missing bar *within* the retained first session (§2.2) is not forgiven by it. |
| Residual `MISSING_INTRADAY_BAR` on dates *not* covered by any of the 4 reviewed `MISSING_INTERNAL_SESSION` exceptions | 100 | 100 | `APPROVED_MISSING_INTERNAL_SESSION;MISSING_INTRADAY_BAR` — for these symbols, the 4 approved exception dates are correctly matched and forgiven (visible via `approved_internal_session_exception_dates`), but there is at least one *additional*, separate, unapproved missing-bar gap elsewhere in the series. Magnitude sampled directly: **ABT** — 2 of 4467 expected bars missing (0.04%); **AAPL** — 0 missing (clean). Not all 178 symbols were individually sampled for exact magnitude, but the pattern (small residual counts, `invalid_intraday_schedule_count: 0`) suggests isolated, scattered gaps rather than large missing blocks — this was not fully characterized and is reported as an open finding, not resolved. |

**This directly contradicts a specific, quantified claim in the original audit report** (`reports/audit/phase2b_sip_acquisition_20260716.md` §3): *"Residual middle-of-backtest gaps for standard symbols: **Exactly 0!** This confirms perfect data continuity."* That claim was only true because `506bd3f`'s unconditional swallow (§Task A1) silently absorbed every one of these 103 coverage failures, across roughly the majority of the acquired 130min universe — not because the data was actually complete.

### 3.3 Manifest status corrected

The immutable manifest (`testdata/research_data/rs_v2_sip_market_data/manifests/acq_20260716_135458/acquisition_manifest.json`) was `COMPLETE_VALIDATED`. Updated, using the codebase's own `_atomic_write_json` and mirroring exactly the field set `finalize_v2_manifest`'s own failure path already uses (`status`, `validation_error_type`, `validation_error_message`, `validation_failed_at`, with `validation_completed_at` removed):

- `status`: `COMPLETE_VALIDATED` → **`FAILED_VALIDATION`**
- `validation_error_message`: itemized summary pointing to this report

The `latest_manifest.json` pointer's `immutable_manifest_sha256` was also updated to match the corrected file's new content hash, to keep the pointer internally consistent (otherwise the next read would fail on a spurious hash-mismatch instead of the true reason). No market-data files (CSV) were touched, re-downloaded, or modified — only the manifest's own status/metadata fields.

**No GO/NO-GO recommendation is made here.** This is a finding: the SIP dataset, as currently acquired, does not pass the corrected validator. Whether to accept the residual gaps, investigate further, re-acquire the affected symbols/dates, or add further reviewed exceptions is an operator decision.

---

## 4. Task C — test hardening

1. `tests/test_v2_sip_profile.py::test_live_forbids_refresh_skip_aggregate_only` now hard-mocks `get_credentials`, `_load_alpaca`, and `download_bars`, and uses a `tempfile.TemporaryDirectory()` instead of a literal `"unused"` path, so a future regression of the guard fails on an `AssertionError`/mock-not-called check in under a second instead of ever reaching real network I/O again. Verified: 0.004s, no network activity.
2. Four regression tests added, all passing:
   - `tests/test_rs_v2_session_coverage.py::test_former_boundary_symbol_without_reviewed_exception_still_fails` — ABNB with zero exceptions present gets no special treatment (guards `778555d`).
   - `tests/test_rs_v2_session_coverage.py::test_envelope_broken_rs_v2_clean_path_still_raises` — broken OHLCV envelope at a path containing `"rs_v2_clean"` still raises (guards `aef661d`).
   - `tests/test_rs_v2_session_coverage.py::test_coverage_fail_rs_v2_clean_path_still_raises` — `coverage_status=FAIL` at a path containing `"rs_v2_clean"` still raises (guards `c0340ad`/`506bd3f`'s exact mechanism).
   - `tests/test_v2_internal_session_exception.py::test_approved_internal_gap_partial_intraday_session_is_detected_then_forgiven_not_excluded` — a partial (not fully missing) approved-date session is detected in full, only the approved bars are forgiven, and the forgiveness stays visible (`APPROVED_MISSING_INTERNAL_SESSION` in the reason, date listed in `approved_internal_session_exception_dates`) — guards `506bd3f`'s exclude-from-expectation bug specifically.
3. Deleted the untracked `unused/` directory (~150MB) — confirmed via `git ls-files unused` (empty) to be entirely untracked, and confirmed (in the prior forensic review) to be partial live-download output from the `ec47f13` bug, including two more partial-download directories dated 2026-07-17 from that review's own diagnostic run.

---

## 5. Task E — review

Ran a two-angle code review (correctness; reuse/simplification/efficiency/altitude/conventions) against the full diff via independent agents.

**Correctness pass**: no bugs found in the reworked logic (timezone handling, detect-then-subtract set operations, both swallow-removal call sites, the guard restoration). One data-completeness observation was raised — BK has zero exception records — which is **intentional** (§2.1: no evidence exists, and inventing one would violate the conditional authorization), not a defect. All three hardened/new test files (69 tests) were independently re-run by the reviewer and passed.

**Cleanup pass**: two low-severity findings, both left as-is with rationale:
1. `tests/test_v2_internal_session_exception.py` adds a third near-duplicate copy of an `_intraday()` test helper (already duplicated between other test files pre-existing this diff). Not fixed: extracting a shared test-helpers module would touch multiple files beyond this remediation's surgical scope, and no such shared-fixtures convention exists elsewhere in this test suite.
2. The new regression test in the same file re-inlines the bar-stepping loop a third time. Same rationale — left as-is.

No CLAUDE.md exists in this repository to check conventions against (only user-global workflow rules, not specific enough to cite a broken line).

---

## 6. Test results

- Targeted, hard-timeout per file: `test_rs_v2_clean_data` (16 tests, 218.7s), `test_rs_v2_session_coverage` (34 tests, 1.15s), `test_v2_sip_profile` (18 tests, 0.06s — down from a confirmed infinite-hang before hardening), `test_v2_internal_session_exception` (17 tests, 0.22s). **All 85 pass.**
- Full suite (`testdata/run_test_suite.py`, 30-minute global timeout): **completed in 558.0s (~9.3 minutes)**, well inside the timeout, no hang, no killed process — direct confirmation the `ec47f13` guard restoration (Task A4 / Task C.1) fully resolves the reported 1.5-2h hang. **Ran 629 tests, 2 failures:**
  - `test_rs_v2_corrected_aggregation.TestCorrectedFailedRunIntegration.test_audit_preserves_all_referenced_files_and_manifest`
  - `test_rs_v2_daily_gap_selection_audit.TestFrozenDailyGapSelectionAudit.test_audit_is_offline_and_preserves_frozen_inputs`

  Both assert `(self.root / "manifests" / "latest_manifest.json").exists()` is `False`, where `self.root` is `testdata/research_data/rs_v2_clean_market_data` — **the `rs_v2_clean` profile, not `rs_v2_sip`**, and a file this remediation never touched. Confirmed pre-existing and unrelated to this diff by three independent checks: (1) neither test file imports any function this diff modified (`test_rs_v2_corrected_aggregation.py` imports only `_aggregate_130min_worker`/`aggregate_to_130min`/`v2_offline_audit_guard`; `test_rs_v2_daily_gap_selection_audit.py` imports only `v2_daily_gap_selection_audit` functions and `v2_offline_audit_guard` — none of the two validator functions or `_expected_intraday_starts` touched in Task A); (2) the offending file's mtime is `2026-07-16 18:59:04`, a full day before this remediation session started; (3) both tests' fixtures assume a pristine sandbox with no `rs_v2_clean_market_data/manifests/latest_manifest.json` present, but this dev environment has real, previously-acquired clean-profile data with that pointer file already in place — a sandbox/fixture mismatch, not a regression. Not fixed here (out of scope for this remediation; flagged for separate follow-up).

---

## 7. Commits

| Hash | Message |
|---|---|
| `fbf0372` | fix: remove coverage-failure swallow from both V2 manifest validators; restore SIP --live guard |
| `aea868a` | fix: remove rs_v2_clean envelope bypass and hardcoded boundary-symbol approval; rework intraday coverage to detect-then-subtract |
| `2a72752` | feat: add reviewed boundary-symbol exceptions for rs_v2_sip (ABNB, PLTR, RBLX, SNOW, WBD, PARA) |
| `878b61f` | test: harden live-guard test against network; add 4 regression tests for the reverted violations |
| `0e6e786` | docs: add Phase 2B forensic review and remediation reports |

All 5 on branch `checkpoint/research-framework-2026-07`, not pushed. Tree is clean (`git status --short` shows only the pre-existing untracked `.venv_linux/`).

---

## Appendix — files touched

- `testdata/v2_session_coverage.py`
- `testdata/vericekici_updated.py`
- `testdata/baselines/rs_v2_sip_coverage_exceptions.json`
- `tests/test_rs_v2_session_coverage.py`
- `tests/test_v2_internal_session_exception.py`
- `tests/test_v2_sip_profile.py`
- `testdata/research_data/rs_v2_sip_market_data/manifests/acq_20260716_135458/acquisition_manifest.json` (gitignored data artifact, not a git-tracked change)
- `testdata/research_data/rs_v2_sip_market_data/manifests/latest_manifest.json` (gitignored data artifact, not a git-tracked change)
- Deleted: `unused/` (untracked)
