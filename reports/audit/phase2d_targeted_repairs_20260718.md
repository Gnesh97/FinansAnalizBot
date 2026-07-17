# Phase 2D — Targeted Repairs — 2026-07-18

Executes the operator's Phase 2D authorization against `reports/audit/phase2c_residual_gap_characterization_20260718.md`. Two of the authorized repairs were **not implemented** because investigation showed they don't apply — see Task A and Task B below for why. No GO/NO-GO language: the cutover decision returns to the operator.

---

## Task A — 2022-01-24 bounded probe (3 of ~9 requests used)

Live SIP query for ABT, XOM, UNH on 2022-01-24 (5-minute bars, full session, `rth_only=False`):

| Symbol | Rows | First | Last | Rows in 11:40-13:50 ET window |
|---|---:|---|---|---:|
| ABT | 27 | 05:45 ET | 16:00 ET | **0** |
| XOM | 74 | 04:00 ET | 19:00 ET | **0** |
| UNH | 30 | 07:45 ET | 16:00 ET | **0** |

All three symbols have real pre-market and after-hours trades outside normal RTH, but **zero trades in the exact 11:40-13:50 ET window** — the precise window flagged as missing in the archived data. Unanimous across all 3 samples.

**Verdict: provider-side-absent.** Not repairable by re-pull; a fresh request today returns the same gap that exists in the archived data. **Per the fail-closed gate, Task D's Jan-24 repair is skipped.** Root cause remains unknown (not a market-wide event — AAPL and other unaffected symbols have complete data for the same date, confirmed in Phase 2C); recommend the operator pursue this with Alpaca support directly if resolution is wanted, since further investigation is outside what this codebase or a bounded probe can determine.

---

## Task B — Aggregation fix: investigated, not implemented, premise corrected

**This is a significant correction to Phase 2C's own conclusion**, found during investigation of how to implement the authorized fix.

Phase 2C reported the 4 listing-day gaps (ABNB, PLTR, RBLX, SNOW) as a "130-min aggregation labeling artifact" caused by positional-from-first-row bucketing. **This was wrong.** Investigating the actual aggregation code to implement the fix revealed:

1. `_aggregate_130min_worker` (`testdata/vericekici_updated.py`) branches on `payload["dataset_profile"]`: `"rs_v2_clean"` → calls `aggregate_to_130min` (session-schedule-aware); anything else → calls `_aggregate_to_130min_legacy`.
2. The payload-construction code (`_main_impl`, single shared block for all profiles) sets `"dataset_profile": "rs_v2_clean" if is_v2 else "legacy"` — and `is_v2 = is_v2_clean or is_v2_sip`, so **both V2 profiles get `"rs_v2_clean"` in the payload**, meaning `rs_v2_sip` acquisition already uses `aggregate_to_130min`, not the legacy function.
3. `aggregate_to_130min` computes each bucket's label as `market_open + floor((row.Date - market_open) / 130min) * 130min` — genuinely wall-clock-anchored to the session's official open (9:30 ET), which is correct.
4. **Direct proof**: ran `aggregate_to_130min` against ABNB's actual raw 5-minute archive and the real NYSE schedule. The output is **byte-identical** to the archived `ABNB_130min.csv`, for all 4,180 rows, confirmed programmatically.

**What this means**: ABNB's first trade on 2020-12-10 was at 13:35 ET (confirmed live, Phase 2C). Measured from the official 9:30 ET session open, 13:35 ET is 245 minutes elapsed — which correctly falls in the *second* 130-minute bucket (11:40-13:50 ET), not the first. The archived bucket labeled "11:40 ET" is not mislabeled; it accurately represents the only bucket with real data that day. The 9:30-11:40 ET bucket is absent because **no trades occurred in that window** — ABNB hadn't started trading yet. This is true, accurate market history, not a software defect.

**No code change was made.** Implementing the originally-authorized "fix" would have altered already-correct behavior based on a false premise, and risked introducing a real regression into a function used for every symbol in the dataset. The 1-bar gap on each of the 4 listing days is a **genuine, permanent characteristic of that day's trading** — there is nothing to repair via aggregation or re-pull. It remains an unresolved `MISSING_INTRADAY_BAR` failure with no exception type currently modeling it (see Task F).

---

## Task C — BK: reviewed exception + rename metadata

**Exception record** added to `testdata/baselines/rs_v2_sip_coverage_exceptions.json`:
```
symbol: BK, exception_type: EARLY_TERMINAL, approved_last_session: 2026-05-20
evidence_source: https://www.bny.com/corporate/global/en/about-us/newsroom/press-release/
                 bny-announces-planned-change-of-stock-ticker-symbol-to-bny-130465.html
evidence_date: 2026-05-11, approved_by: operator (phase2d_targeted_repairs authorization)
approved_at: 2026-07-18
```
Verified to load and hash-check via `load_reviewed_coverage_exceptions` (12 exceptions total, up from 11).

**Rename metadata** added to `testdata/research_data/alpha_vantage_delisted.csv`:
```
BK,The Bank of New York Mellon Corporation - ticker changed to BNY (effective
2026-05-21; company/legal name and CUSIP unchanged; not a true delisting),
NYSE,Stock,2007-07-02,2026-05-21,Delisted
```
(`ipoDate` reflects BK's actual 2007-07-02 trading start following the Bank of New York / Mellon Financial merger — a matter of public record, not independently re-verified in this task.)

**BNY was not added to the frozen candidate universe and no BNY data was acquired** — explicitly out of scope, left as an open item for a separate future operator decision.

---

## Task D — Repairs via sanctioned recovery path: none executed

Both conditional repairs resolved to no action:
- **(i) 2022-01-24 5-min recovery for 101 symbols**: not executed — Task A's probe found the gap provider-side-absent, not repairable, and the fail-closed gate explicitly instructs skipping this case.
- **(ii) Re-aggregation of the 4 listing-day files**: not executed — Task B found there is nothing to re-aggregate; the existing files are already correct.

No recovery run was created, no provenance-tracked machinery was invoked, because there was nothing to repair through it.

---

## Task E — Downloader completeness telemetry

Added `write_v2_range_shortfall_report()` (`testdata/vericekici_updated.py`), wired into `_main_impl` alongside the two existing report writers (`write_v2_coverage_review_reports`, `write_v2_applied_exceptions_report`). For every acquired file, compares `requested_end` against the recomputed `last_market_session` (`end_gap_sessions`, already computed by the coverage engine); emits a `RANGE_SHORTFALL` row to `{profile}_range_shortfall_warnings.{csv,md}` whenever the shortfall exceeds `RANGE_SHORTFALL_SESSION_THRESHOLD = 5` sessions and no `APPROVED_EARLY_TERMINAL` exception already explains it.

**Telemetry only** — does not raise, does not touch `validate_v2_manifest_and_files`/`validate_v2_sip_manifest_and_files`, cannot affect `coverage_status` or the manifest's `status` field. This directly targets the root cause found in Phase 2C Task B.1: the original BK acquisition recorded `request_status: SUCCESS` with no completeness check at all against the requested date range, which is why the gap went unnoticed until manual investigation. Had this existed on 2026-07-16, BK would have surfaced a `RANGE_SHORTFALL` warning immediately.

3 new regression tests (`tests/test_v2_internal_session_exception.py::TestRangeShortfallReport`), all passing: unapproved shortfall beyond threshold is flagged; an approved `EARLY_TERMINAL` exception suppresses the warning; a small shortfall within threshold is not flagged.

---

## Task F — Honest re-validation, tests, review, commits

### F.1 True validation outcome: still **FAILED_VALIDATION**, improved

Re-ran full recomputed coverage over all 360 records with the new BK exception applied (offline, no re-download):

```
TOTAL RECORDS: 360
FAIL COUNT: 102   (was 103 on 2026-07-17)
VALID COUNT: 258  (was 257)
Unique failing symbols: 102
```

**Applied-exceptions list now in effect**: 4 internal-session dates (2021-04-19, 2021-10-25, 2022-03-08, 2022-04-06, unchanged) + 6 boundary LATE_START/EARLY_TERMINAL records (ABNB, PLTR, RBLX, SNOW, WBD, PARA-start) + PARA-end + **BK-end (new)** = 8 boundary-type records, 12 exception records total.

**What changed**: BK's `1D` file is now correctly `VALID` (the EARLY_TERMINAL exception applies). BK's `130min` file **still fails** — for an unrelated reason: it also lost its 2022-01-24 bars (part of the systemic event affecting 101 symbols), which the EARLY_TERMINAL exception does not and should not cover. No unexpected or new failure patterns appeared; spot-checked every failing record's reason string against the two known causes (`MISSING_INTRADAY_BAR` from either 2022-01-24 or a listing day) — all 102 match one of the two, none is anomalous.

**Itemized remainder** (102 failing symbols):
- 101 symbols (100 + BK) — `130min`, missing 2022-01-24 bars. Provider-side-absent (Task A). Not fixed.
- 1 symbol (ABNB) — `130min`, missing 1 bar on its own 2020-12-10 listing day. Genuine, accurate absence of trading (Task B). Not fixed. No existing exception type models "a listing day's session was itself partial" the way `MISSING_INTERNAL_SESSION` models "an otherwise-normal internal date had a gap" — this would need a new, narrowly-scoped exception type (not created here; no such authorization was given once the "aggregation bug" premise was corrected).

The manifest (`acq_20260716_135458/acquisition_manifest.json`) has been updated to reflect this: `status` remains `FAILED_VALIDATION` (honestly — it is not valid), with `validation_error_message` itemizing the above and `validation_failed_at` refreshed. The `latest_manifest.json` pointer's `immutable_manifest_sha256` was kept consistent with the updated file.

**No GO/NO-GO recommendation.** The dataset improved by one record; it did not reach a passing state; both remaining causes are genuine data-provider/data-reality issues that this task's authorized tools could not resolve.

### F.2 Test results

- `tests/test_v2_internal_session_exception.py` (targeted, includes the 3 new tests): **20/20 pass**, 0.24s.
- Full suite (`testdata/run_test_suite.py`, 30-minute timeout): **632 tests in 531.3s (~8.85 min)**, well inside the timeout — hang stays fixed, consistent with Phase 2B's 558s/629-test run (+3 tests here for the new telemetry regression tests). **2 failures, both the exact same pre-existing, already-diagnosed-and-flagged failures from Phase 2B** (`test_rs_v2_corrected_aggregation.py::test_audit_preserves_all_referenced_files_and_manifest`, `test_rs_v2_daily_gap_selection_audit.py::test_audit_is_offline_and_preserves_frozen_inputs` — a `rs_v2_clean`-sandbox-state fixture mismatch, unrelated to `rs_v2_sip` or anything touched in this session; see `reports/audit/phase2b_remediation_20260717.md` §6 for the original diagnosis). No new or unexpected failures.

### F.3 Code review

Reviewed directly (95-line diff, 3 files: one data-only JSON addition, one new ~40-line function + 1-line call-site wiring, one test file addition). No CRITICAL/HIGH findings:
- Exception-token matching correctly uses `;`-split exact-match (`"APPROVED_EARLY_TERMINAL" not in reason.split(";")`), avoiding the substring pitfall (`"UNAPPROVED_X"` containing `"APPROVED_X"`) that was caught and fixed in Phase 2B's own test suite.
- Missing/`None` `end_gap_sessions` handled safely, defaults to no-warning rather than crashing.
- No path touches the strict validators; confirmed by inspection and by F.1's re-validation (no behavior change to `coverage_status` computation, only to a new, separate report writer).
- Minor, non-blocking note: the exact threshold boundary (5 vs. 6 sessions) isn't explicitly tested; the `>` comparison is unambiguous by inspection, so this wasn't treated as a gap worth blocking on.

### F.4 Commits

| Hash | Message |
|---|---|
| `7b2fd77` | feat: add downloader completeness telemetry (RANGE_SHORTFALL warnings) |
| `e5bb45d` | feat: add reviewed EARLY_TERMINAL exception for BK (BK->BNY ticker change) |
| `e387ea5` | docs: add Phase 2C residual gap characterization and Phase 2D targeted repairs reports |

All 3 on branch `checkpoint/research-framework-2026-07`, not pushed. Tree clean except the pre-existing untracked `.venv_linux/` and the gitignored data artifacts (`alpha_vantage_delisted.csv`, both manifest files) updated outside git.

---

## Appendix — files touched

- `testdata/vericekici_updated.py` (new `write_v2_range_shortfall_report` function + call site)
- `testdata/baselines/rs_v2_sip_coverage_exceptions.json` (BK exception record)
- `testdata/research_data/alpha_vantage_delisted.csv` (BK rename entry, gitignored data artifact)
- `tests/test_v2_internal_session_exception.py` (3 new regression tests)
- `testdata/research_data/rs_v2_sip_market_data/manifests/acq_20260716_135458/acquisition_manifest.json` (gitignored data artifact — status/metadata fields updated, not a git-tracked change)
- `testdata/research_data/rs_v2_sip_market_data/manifests/latest_manifest.json` (gitignored data artifact, pointer hash kept consistent)
