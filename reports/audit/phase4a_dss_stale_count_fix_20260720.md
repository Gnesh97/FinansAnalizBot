# Phase 4A — DSS Stale File-Count Constant Fix

**Date:** 2026-07-20/21
**Trigger:** a `dss-report --print-only` run correctly failed fail-loud with `"dataset failed strict validation: V2_DATASET_NOT_VALIDATED: expected 360 files in manifest, got 358"`.
**Diagnosis confirmed:** this was a stale-constant bug, not a data problem. The on-disk 358-file dataset (177 candidates, correctly retired-BK universe) was being checked against a leftover pre-retirement expectation of 360 files.

This report does not conclude GO/NO-GO.

---

## 1. Root cause trace

The failure originates in `testdata/runner.py`'s `validate_v2_clean_dataset` — the shared manifest-integrity gate used by both `rs_v2_sip` and `rs_v2_clean` profiles, called directly on the DSS path: `dss_report.py::_assert_dataset_ready` → `runner.validate_v2_clean_dataset`.

An earlier commit (`472951d`, during Phase 3B) had already corrected this same function's `candidate_universe_hash`/`candidate_count` checks (a few lines above the failing code) to the new 177-symbol baseline. It missed three further-down literals in the same function:

```python
# BEFORE (stale)
files = manifest.get("files", [])
if len(files) != 360 or manifest.get("logical_acquisition_item_count") != 360:
    raise ValueError(f"V2_DATASET_NOT_VALIDATED: expected 360 files in manifest, got {len(files)}")
if manifest.get("successful_item_count") != 360 or manifest.get("failed_item_count") != 0:
    raise ValueError("V2_DATASET_NOT_VALIDATED: manifest count reconciliation failed")
...
if role_counts != {"CANDIDATE": 356, "PRIMARY_BENCHMARK": 2, "SECONDARY_BENCHMARK": 2} or symbol_counts != {"QQQ": 2, "SPY": 2}:
    raise ValueError("V2_DATASET_NOT_VALIDATED: role count mismatch")
```

`360 = (178+2)*2` (178 candidates + QQQ + SPY, 2 timeframes) and `356 = 178*2` — both pre-BK-retirement values. The real, current, honestly-acquired dataset has 358 items (`(177+2)*2`) with 354 CANDIDATE-role records (`177*2`), and was being rejected by these leftover literals, not by any real data defect.

## 2. Every 178/360/180 occurrence, classified

Grepped `testdata/`, `scripts/`, `bot.py` for `\b360\b|\b356\b|\b180\b|\b178\b`.

### (a) Historical — correctly left unchanged, not touched this task

| Location | Why it must stay |
|---|---|
| `testdata/vericekici_updated.py:980` — `"candidate_count": 178` inside `_load_gap_recovery_frozen_context` | Pinned to `GAP_RECOVERY_AUDIT_RUN`, a specific, immutable, already-frozen Phase 2D acquisition that genuinely has 178 files and will never change. |
| `testdata/v2_daily_gap_selection_audit.py:42` — `EXPECTED_CANDIDATE_COUNT = 178` | Its caller (`audit_v2_daily_gap_selection`) explicitly loads `testdata/baselines/archive/rs_m126_top30_sma100_no_regime_v2_clean_stock.178symbols.20260718.json` — the archived pre-retirement snapshot — not the current mutable baseline. 178 is correct for what it audits. |
| `scripts/phase3b_bk_retirement_impact.py` (all "178" references) | Intentional — the entire point of this script is comparing the old 178-universe run against the new 177-universe run. Not stale. |
| `testdata/delisted_security_metadata.py` (`<= 180` day-window checks) | Unrelated — these are filing/event-alignment day-count thresholds, not universe symbol counts. |

### (b) Live — fixed this task

| Location | What it was | What it is now |
|---|---|---|
| `testdata/runner.py:7665` | `len(files) != 360` | `len(files) != 358` |
| `testdata/runner.py:7665` | `logical_acquisition_item_count) != 360` | `!= 358` |
| `testdata/runner.py:7666` | error text `"expected 360 files"` | `"expected 358 files"` |
| `testdata/runner.py:7667` | `successful_item_count) != 360` | `!= 358` |
| `testdata/runner.py:7723` | `{"CANDIDATE": 356, ...}` | `{"CANDIDATE": 354, ...}` |

### Noted, but out of scope (not touched — not causing the DSS failure)

`testdata/quantconnect_port_contract.py` contains descriptive text strings ("178 clean stock symbols; 22 ETFs excluded", "no reproducible historical membership rule for current local 178-symbol universe") inside a generated contract's narrative `contract_status` block — not a functional count check. In that same file, `candidate_count`/`candidate_universe_hash` are already pulled dynamically from the live baseline file (auto-correct), and `logical_acquisition_item_count` was already fixed to `358` in an earlier commit. The lingering "178" text is stale documentation, not a live validator, and not on the DSS failure path — left untouched per instruction to fix only category (b).

## 3. The fix

```diff
- if len(files) != 360 or manifest.get("logical_acquisition_item_count") != 360:
-     raise ValueError(f"V2_DATASET_NOT_VALIDATED: expected 360 files in manifest, got {len(files)}")
- if manifest.get("successful_item_count") != 360 or manifest.get("failed_item_count") != 0:
+ if len(files) != 358 or manifest.get("logical_acquisition_item_count") != 358:
+     raise ValueError(f"V2_DATASET_NOT_VALIDATED: expected 358 files in manifest, got {len(files)}")
+ if manifest.get("successful_item_count") != 358 or manifest.get("failed_item_count") != 0:
      raise ValueError("V2_DATASET_NOT_VALIDATED: manifest count reconciliation failed")
```
```diff
- if role_counts != {"CANDIDATE": 356, "PRIMARY_BENCHMARK": 2, "SECONDARY_BENCHMARK": 2} or symbol_counts != {"QQQ": 2, "SPY": 2}:
+ if role_counts != {"CANDIDATE": 354, "PRIMARY_BENCHMARK": 2, "SECONDARY_BENCHMARK": 2} or symbol_counts != {"QQQ": 2, "SPY": 2}:
```

Four numeric literals changed. No comparison operator, key name, or surrounding logic touched. No check was loosened — the same "must equal exactly" semantics apply, just against the current correct number.

## 4. Fail-closed boundary proof

New regression test `test_validate_v2_clean_dataset_accepts_358_rejects_357_and_359` (`tests/test_rs_v2_clean_data.py`), built on the existing `_complete_manifest`/`finalize_v2_manifest` fixture machinery (real synthetic CSVs, real SHA-256 hashes, a genuine `COMPLETE_VALIDATED` manifest):

1. Builds and finalizes a real 358-record manifest → `validate_v2_clean_dataset` **accepts** it (no exception).
2. Removes one record (357), rewrites the immutable manifest file, recomputes and rewrites its SHA-256 in the pointer (so only the file count changes — the tamper-detection hash check stays consistent, isolating the count check specifically) → `validate_v2_clean_dataset` **rejects** it (`ValueError: V2_DATASET_NOT_VALIDATED`).
3. Adds one synthetic extra record (359), same resync technique → **rejected**.
4. Restores exactly 358 → **accepted** again.

```
test_validate_v2_clean_dataset_accepts_358_rejects_357_and_359 ... ok
```

## 5. Code review

Ran the mandatory review on `git diff HEAD -- testdata/runner.py tests/test_rs_v2_clean_data.py`. Verdict: **APPROVE**, 0 critical/high/medium findings.

- Arithmetic independently re-verified by the reviewer: 177 candidates + QQQ + SPY = 179 symbols × 2 timeframes = 358; 177 × 2 = 354 CANDIDATE records. Confirmed correct.
- Confirmed the diff is surgical — exactly 4 numeric literal changes, nothing else touched.
- Confirmed the new test's manifest-corruption technique correctly isolates the file-count check (verified `validate_v2_clean_dataset` has no other integrity signature besides the one SHA-256 the test already resyncs).
- Confirmed no other stale `360`/`356`/`178` literal was missed in this diff's blast radius; the remaining `178` references elsewhere are the historical/intentional ones listed in §2(a), unrelated to this diff.
- One LOW note (informational only, not a defect in this diff): the reviewer flagged that `v2_daily_gap_selection_audit.py`/`vericekici_updated.py`'s `178` references are worth a human sanity-check "if that gap-recovery audit is ever expected to run against the current 177-symbol universe" — it explicitly is not (it audits a named historical run), so no action taken.

## 6. `--print-only` run against the real current dataset

```
$ python bot.py --mode dss-report --print-only
==================================================
📋 [MODE] Mode: dss-report
==================================================
DSS did not run today: data is stale: manifest target_end_date='2026-07-17', expected latest completed session='2026-07-20'

Saved: reports/dss/dss_report_20260720T212737Z.md

--print-only: not sending to Telegram.
```

**The stale-count bug is gone** — `validate_v2_clean_dataset` no longer rejects the real 358-file dataset for the wrong count. It now correctly fails loud for a **different, legitimate** reason: the on-disk data genuinely hasn't been refreshed since Phase 3B Step 2 (`--refresh-daily`, which last brought it to `2026-07-17`), and today's expected latest completed session is `2026-07-20`. This is the freshness check working exactly as designed — not a bug, and per this task's explicit instruction not to run `--live` acquisition, it was left as-is rather than "fixed" by refreshing the data.

## Full suite

```
Ran 672 tests in 733.871s
OK
```
Reconciles exactly: 671 (Phase 4A baseline) + 1 (new boundary test) = 672.

## git status / git log

```
$ git status --porcelain
?? reports/audit/phase4a_dss_report_and_telegram_20260720.md   <- prior Phase 4A report, never committed, untouched by this task
?? reports/dss/                                                 <- DSS run artifacts (this task's --print-only runs + one prior failed run), untouched/uncommitted by design

$ git log --oneline -3
d0dc4c5 fix: correct stale 360/356 file-count literals in validate_v2_clean_dataset to 358/354 post-BK-retirement
6484a9b feat: add daily DSS BUY/SELL report generator and Telegram send (manual-trigger only)
e1131de docs: Phase 3B reacquisition + baseline rebuild reports
```

---

## Stop

Per task instructions: no GO/NO-GO conclusion is offered here. Stopping after reporting.
