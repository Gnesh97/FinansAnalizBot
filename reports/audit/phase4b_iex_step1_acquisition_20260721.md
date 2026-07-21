# Phase 4B — IEX Step 1: rs_v2_clean Acquisition + Honest Validation (STOPPED)

**Date:** 2026-07-21
**Operator authorization:** --live IEX acquisition sanctioned (Free tier permits IEX). No broker/order calls; `broker_submit_enabled` remained false throughout.
**Outcome in one line:** Acquisition itself succeeded (179/179 symbols, zero rate-limiting), but strict validation fails **beyond** the 6 known IEX gaps — 323 residual missing 130-minute blocks across 65 additional dates and 111 symbols — so, per the task's hard rule, **no exceptions were authored, no manifest was finalized, and Phases 1B/1C were not executed.** Stopped and reported.

No GO/NO-GO conclusion — facts only.

---

## 1. Pre-acquisition finding: rs_v2_clean live acquisition was crashed by a real code regression

The first sanctioned run aborted before any download:

```
File "testdata/vericekici_updated.py", line 2731, in _main_impl
    "drift_check_start": drift_check_start,
UnboundLocalError: cannot access local variable 'drift_check_start' where it is not associated with a value
```

`drift_check_start` was introduced by Phase 3A's SIP `--refresh-daily` work and is only assigned inside the `elif is_v2_sip:` branch. The download-payload construction references it unconditionally, so **every non-SIP live acquisition path (rs_v2_clean and legacy) has been broken since Phase 3A**. The scoping report's "no code-level blocker identified from static analysis" (§6) missed this.

**Fix applied (minimal):** initialize `drift_check_start = None` once before the profile branches. The worker already guards with `is not None` (line ~1519), and the SIP refresh-daily branch still assigns its own value — zero SIP behavior change. No validator logic touched. Aborted run left `manifests/acq_20260721_122657/` with `status: FAILED`.

## 2. Phase 1A acquisition — command, runtime, rate-limiting

Sanctioned command (exactly the profile-enforced window):

```
.venv_linux/bin/python vericekici_updated.py --dataset-profile rs_v2_clean \
  --feed iex --adjustment all --start 2020-07-27 --end 2026-07-07 \
  --output-root research_data/rs_v2_clean_market_data \
  --live --overwrite --parallel --download-workers 4
```

A `--dry-run` first confirmed the plan: `logical_acquisition_item_count: 358` (177 candidates + QQQ + SPY × 2 timeframes).

Live run `acq_20260721_122814`: started 12:28:14Z, ~39 minutes wall-clock, **179/179 symbols downloaded, 0 download errors**.

**Rate-limiting (the flagged unknown): none observed.** Zero occurrences of 429 / "too many requests" / throttling / retry-storm signatures in the full run log at `--download-workers 4`. The Free-tier 200-calls/min ceiling was never hit at this concurrency. This removes the scoping report's untested-throttle risk for Step 2 planning at comparable worker counts.

## 3. Exact initial validation failure

Finalize failed (fail-closed) and the latest pointer was NOT updated:

```
ValueError: V2_DATASET_NOT_VALIDATED: frozen contract mismatch
manifest status: FAILED_VALIDATION
successful_item_count = 344, failed_item_count = 14  (contract requires 358/0)
coverage_valid_item_count = 55, coverage_failed_item_count = 303
```

Two independent failure layers:

### 3a. Mechanical layer — 14 stale 130-minute files kept by the row-count-reduction guard

The 14 failed records are all 130min: AAPL, AMD, AMZN, BA, GOOG, INTC, MSFT, NFLX, NVDA, PFE, PLTR, TSLA, QQQ, SPY — `aggregation SKIPPED` via `aggregated_row_count_lower_than_existing` (e.g. `AAPL old_rows=4463,new_rows=4462`, `QQQ old_rows=4465,new_rows=4462`).

Diagnosed precisely: the surplus rows in the old July-12 files are bars **starting after the 13:00 ET early close on half-days** (2025-07-03 17:50Z, 2025-11-28 18:50Z, 2025-12-24 18:50Z) — artifacts of the older aggregation code. The fresh schedule-aware `aggregate_to_130min` correctly excludes them, producing 1–3 fewer rows, which trips the fail-closed guard against the contaminated old files. The sanctioned cure on a future re-run is the existing `--allow-row-count-reduction` flag (warning-logged operator override); it was NOT used this run. These 14 also account for all `INVALID_INTRADAY_SCHEDULE` coverage flags (the old files' post-close bars are off-schedule).

### 3b. Coverage layer — what the strict validator found in the fresh IEX data

With the registry empty (as required for the honest first pass), 303 records fail coverage. Recomputed offline against the approved NYSE calendar, per-symbol:

**The 6 known gaps — confirmed, with 2026-vintage IEX specifics:**

| Date | Kind | Affected (177-universe + QQQ/SPY) | vs. prior knowledge |
|---|---|---|---|
| 2021-04-19 | daily session + intraday absent | 121 symbols | was 122 on 178-universe (−BK, consistent) |
| 2021-10-25 | daily session + intraday absent | 107 | was 108 (−BK, consistent) |
| 2022-03-08 | daily session + intraday absent | 118 | was 119 (−BK, consistent) |
| 2022-04-06 | daily session + intraday absent | 1 (WBD) | unchanged |
| 2024-12-23 | intraday only | **1 (PSA)** | was 100% of universe on the July-12 dataset — IEX has since backfilled this date for every other symbol |
| 2025-03-10 | intraday only | 179 (100%) | unchanged |

**Boundary symbols (anticipated; would have been Phase 1B step 5):** LATE_START ABNB 2020-12-10, PLTR 2020-09-30, RBLX 2021-03-10, SNOW 2020-09-16, PARA 2022-02-17, **WBD 2022-04-05 — one session later than SIP's 2022-04-04** (WBD's first IEX-tape session); EARLY_TERMINAL PARA 2025-08-06. BK: none, correctly absent from the 177 universe. Debut-day partials: ABNB/PLTR/RBLX/SNOW first trades 13:35/13:35/13:30/12:35 ET (would need PARTIAL_BOUNDARY_SESSION); PARA boundary days are full 78/78 sessions. New IEX-specific fact: WBD's debut 2022-04-05 has only 5 five-minute bars (09:35–11:40 ET) and is missing its final 130-min block — a gap after the last trade of the day, which PARTIAL_BOUNDARY_SESSION cannot cover by design.

### 3c. THE STOP TRIGGER — residual gaps beyond the known 6

Sweeping every symbol's 130-minute series (fresh aggregation semantics, validator-equivalent expected-starts) after excluding the 6 known dates and debut days:

```
TOTAL residual missing (symbol, 130min-block) pairs: 323
distinct dates: 65        symbols affected: 111
top dates:   2022-01-24 (200 holes)   2024-06-03 (33)   2021-04-13 (15)
             + 62 dates with 1–2 holes each
top symbols: BKNG (41 blocks)   CMG (25)   BLK (6)   CI (5)
```

Character of the residual set:
- **2022-01-24 / 2024-06-03 / 2021-04-13** are provider-wide partial-intraday days. Notably 2022-01-24 already exists in the **SIP** registry as a MISSING_INTERNAL_SESSION (101 symbols) — the same real-world day is partial on IEX too, but it was never in this task's "known 6" list.
- The **62-date long tail** is thin-liquidity reality on IEX's single-venue tape: high-price/low-share-volume names (BKNG, CMG…) have 130-minute windows with zero IEX trades, so no bar exists and none can be acquired. The SIP consolidated tape does not have this failure mode.

**Why this was not previously known:** the July-12 rs_v2_clean dataset's `COMPLETE_VALIDATED` status predates the validator hardening. Git history shows the coverage-failure suppression that was active then (`c0340ad`, `aef661d` "allow clean dataset backtesting … by suppressing validator coverage failures") was later reverted (`fbf0372` "remove coverage-failure swallow from both V2 manifest validators", `aea868a`, `744e33e`). The "6 known gaps" inventory was compiled in that relaxed era and undercounts what today's strict validator enforces.

Per the task's hard rule — *"If ANYTHING fails beyond those 6 known gaps … STOP and report — do not proceed to exception authoring"* — **exception authoring (Phase 1B) and DSS repoint execution (Phase 1C) were not performed.** `rs_v2_clean_coverage_exceptions.json` remains `{"exceptions": [], "schema_version": "1.0.0"}`, untouched.

## 4. Rebalance/execution-date cross-check (performed early, offline — reported as instructed)

Reusing `v2_coverage_forensics._monthly_rebalance_dates()` on the approved calendar (73 rebalance dates, matching Phase 1.7's count exactly):

```
2021-04-19: rebalance_date=False  next_open_execution_date=False
2021-10-25: rebalance_date=False  next_open_execution_date=False
2022-03-08: rebalance_date=False  next_open_execution_date=False
2022-04-06: rebalance_date=False  next_open_execution_date=False
2024-12-23: rebalance_date=False  next_open_execution_date=False
2025-03-10: rebalance_date=False  next_open_execution_date=False
```

Phase 1.7's finding **holds for the 177 universe**: none of the 6 known gap dates is a strategy decision or execution date. (The 65 residual dates were NOT cross-checked — that analysis belongs to whatever follow-up decides how to handle them.)

## 5. State after stopping

- `latest_manifest.json` still points to `acq_20260712_212749` — the failed runs never touched the pointer (fail-closed behavior verified again).
- **Caveat:** the on-disk data files under `rs_v2_clean_market_data/` were overwritten by the sanctioned `--overwrite` run, so the July-12 manifest's file hashes no longer match disk. That dataset already failed strict validation (178-universe contract) before this task; it is now additionally hash-stale. Any rs_v2_clean consumer fails loud either way — nothing silently reads it.
- The 2 failed-run manifest directories (`acq_20260721_122657` crash, `acq_20260721_122814` FAILED_VALIDATION) are immutable audit artifacts on disk.
- IEX evidence-probe script was prepared (scratchpad, GET-only, request-capped) but **not run** — probes are only needed if/when exception authoring is re-authorized.

## 6. Code changes made (working tree; none touch validators/strategy/universe/SIP data)

1. `testdata/vericekici_updated.py` — `drift_check_start = None` initialization (regression fix, §1).
2. `bot.py` — new `--dss-data-profile {rs_v2_sip,rs_v2_clean}` flag, default `rs_v2_sip`; `run_dss_report` derives `data_folder` from it. Explicit opt-in; default behavior unchanged.
3. `testdata/universe_scanner.py` — new `--data-profile {rs_v2_sip,rs_v2_clean}` flag, default `rs_v2_sip`, replacing the silent hardcoded default the scoping report warned about. All existing invocations (including `run_daily_paper_pipeline.sh`) keep SIP.

Items 2–3 were prepared for Phase 1C before the STOP trigger surfaced; they are inert (defaults unchanged) and were left in place as reviewed groundwork.

## 7. What Phase 1B would have needed (recorded for the operator's decision, not performed)

An honestly-validated 177-universe IEX dataset requires exceptions for substantially more than 6 records: the 6 known gaps (per-date symbol lists now measured exactly), 6 LATE_START + 1 EARLY_TERMINAL + 4 PARTIAL_BOUNDARY_SESSION boundary records, a WBD 2022-04-05 partial-debut record, **plus** coverage for 65 residual dates / 323 thin-liquidity holes — either as ~65 additional per-date MISSING_INTERNAL_SESSION records (each needing IEX-specific evidence) or a policy decision on whether thin-tape IEX blocks are acceptable for the strategy. The long tail is structural to the IEX feed, not a one-off outage: it would grow as new thin days occur. That trade-off is an operator decision and out of scope here.

## 8. Test suite / review

Full unittest suite run after the three code changes (baseline 672) and ECC code review executed at end of session — results recorded in the session log and commit metadata (suite output appended after this report was filed; this file is immutable and intentionally does not claim numbers produced after its writing).

---

## Stop

Stopped after Phase 1A per the beyond-known-gaps rule. No exceptions authored, no finalize, no DSS run against IEX data, no `--print-only` outcome to report (Phase 1C unreached). Awaiting operator decision on the residual-gap policy before any Step-1 resumption.
