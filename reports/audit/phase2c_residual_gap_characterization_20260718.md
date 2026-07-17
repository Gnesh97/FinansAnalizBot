# Phase 2C — Residual Gap Characterization — 2026-07-18

Read-only characterization of the 103 failing records / 102 symbols identified in `reports/audit/phase2b_remediation_20260717.md` §3.2. No code changes, no exception records, no manifest edits, no re-downloads. 8 bounded GET probes to Alpaca SIP were made, all inside Task B (6) and Task C (1) — Task A used zero live requests, offline recomputation only.

**Headline finding: the early-close hypothesis is refuted (0%). The 103-record picture actually decomposes into three completely distinct, independently-diagnosed causes: one dominant, previously-undocumented single-day event (2022-01-24, 202 bars / 101 symbols); a listing-day aggregation-labeling artifact on otherwise-complete data (4 bars / 4 symbols); and BK's already-known post-2026-05-20 gap (93 bars), which live re-query now shows is genuinely absent at the provider, not a transient download failure.**

---

## Task A — the 100-symbol residual gaps

### A.1 Full missing-bar extraction

Recomputed coverage offline against the existing `acq_20260716_135458` manifest data (no re-download), replicating the fixed validator's exact boundary-narrowing and exception-forgiveness logic, but capturing every individual missing 130-min bar timestamp rather than just the aggregate count.

```
Total residual missing bars (130min, post-exception-forgiveness): 299
Symbols affected: 102
Distinct dates affected: 36
```

This reconciles exactly with the remediation report's 103-record / 102-symbol figures (103 records = 102 symbols' 130min files + BK's 1D file, which has no bar-level breakdown).

### A.2 How the code actually handles a half day

- `approved_nyse_schedule()` (`v2_session_coverage.py:115-132`) sources its calendar from `data_engine._build_session_windows`, which — when `use_market_calendar=True` (the default) — calls `pandas_market_calendars.get_calendar("NYSE").schedule(...)` directly. This is the real, authoritative NYSE calendar, and its `market_close` column **does** correctly reflect 13:00 ET closes on early-close days (verified directly, see A.3 table below).
- `_expected_intraday_starts()` (`v2_session_coverage.py:190-197`) walks `current = market_open; while current < close: yield current; current += 130min`. For a normal 6.5-hour session this yields 3 blocks (9:30, 11:40, 13:50 ET). For a 3.5-hour early-close session (9:30-13:00 ET, 210 minutes), the loop yields **exactly 2 blocks** (9:30 and 11:40 ET) — the second block's window (11:40-13:50) extends 50 minutes past the actual 13:00 close, but the loop only checks whether the block's *start* is before close, not whether the full block fits, so it's still counted as expected. This is exactly what the existing test `test_early_close_session_expects_two_start_stamped_bars` (`tests/test_rs_v2_session_coverage.py`) already asserts and locks in.
- **Conclusion: the code's early-close handling is correct and was already working correctly before this investigation.** It is not the cause of any of the 299 residual missing bars.

### A.3 Verdict: early-close hypothesis, with numbers

There are exactly **12** NYSE early-close sessions in the target window (2020-07-27 to 2026-07-07), pulled from the same `pandas_market_calendars` NYSE schedule the codebase uses:

```
2020-11-27, 2020-12-24, 2021-11-26, 2022-11-25, 2023-07-03, 2023-11-24,
2024-07-03, 2024-11-29, 2024-12-24, 2025-07-03, 2025-11-28, 2025-12-24
```

**Overlap between these 12 dates and the 36 residual-gap dates: zero. 0% of the 299 residual missing bars fall on an early-close date.**

The early-close hypothesis is **refuted**. No fix is proposed for calendar/early-close handling — there is nothing to fix.

### A.4 What the 299 bars actually are, itemized

| Cause | Bars | Symbols | Dates |
|---|---:|---:|---|
| Single-day event, 2022-01-24 | 202 | 101 (see Appendix) | 1 |
| Listing-day partial-session labeling artifact | 4 | 4 (ABNB, PLTR, RBLX, SNOW) | 4 (each symbol's own listing day) |
| BK post-2026-05-20 gap | 93 | 1 (BK) | 31 |

**2022-01-24 in detail** (the dominant cause, 67.6% of all residual bars): every one of the 101 affected symbols is missing exactly the same two block labels — `16:40:00+00:00` and `18:50:00+00:00` UTC (11:40 ET and 13:50 ET) — while the `14:30:00+00:00` UTC (9:30 ET, market-open) block is present. Verified directly against raw files: `AAPL_130min.csv` has all 3 blocks that day; `ABT_130min.csv` (one of the 101 affected) has only the 9:30 ET block, then nothing until the next day. This is a **hard cutoff after the first block of the day**, uniform across 101 of 178 candidate symbols (57%), with the remaining ~77 symbols (including AAPL, and notably every megacap/high-liquidity name spot-checked) completely unaffected. Not a market-wide feed outage (AAPL and others are clean); not early-close (Jan 24, 2022 was a normal Monday — in fact one of the most volatile single trading days of that period, with the S&P 500 swinging roughly 4% intraday before closing positive). No further root-cause determination was made within this task's scope — see the repair-plan matrix (Task D) for the recommended next step.

**Listing-day artifact in detail**: see Task C below — this is not a SIP data gap at all.

**BK in detail**: see Task B below.

---

## Task B — BK acquisition-gap diagnosis

### B.1 Manifest/log trail

BK's manifest records (both `1D` and `130min`) show `request_status: SUCCESS`, `provider_result_status: DOWNLOADED`, `error_message: ""`, `error_type: ""` — **no error of any kind was recorded**. Run-level telemetry for the entire 360-item acquisition: `retry_attempt_count: 0`, `failed_provider_attempt_count: 0`. No BK-specific mention exists in `acquisition_report.md`/`.csv`, and no separate log file exists for this run beyond the manifest and report. Row counts: 1,462 daily rows (2020-07-27 to 2026-05-20), 4,366 intraday rows. **The acquisition simply accepted whatever SIP returned as a success, with no completeness check against the requested date range** — this is a distinct, secondary finding: the downloader has no mechanism to detect or flag "returned data doesn't cover the full requested range" as anything other than success.

### B.2 Bounded SIP probe (6 of 6 requests used)

All via the codebase's own `download_bars()`/Alpaca client, live-queried today:

| # | Request | Result |
|---|---|---|
| 1 | BK daily, 2026-05-15 → 2026-07-07 | 4 rows: 05-15, 05-18, 05-19, **05-20** (last) |
| 2 | BK 5min, 2026-05-26 → 2026-05-27 | 0 rows |
| 3 | BK 5min, 2026-06-15 → 2026-06-16 | 0 rows |
| 4 | BK 5min, 2026-07-06 → 2026-07-07 | 0 rows |
| 5 | AAPL daily, 2026-07-01 → 2026-07-07 (sanity check) | 3 rows (07-01, 07-02, 07-06) — proves the request mechanism and credentials work correctly for recent dates |
| 6 | BK daily, 2026-05-18 → 2026-06-01 (pinpoint) | 3 rows: 05-18, 05-19, **05-20** (last), zero afterward |

### B.3 Conclusion

**Not a transient download failure.** A live query against Alpaca SIP today (2026-07-18) — nearly two months after the acquisition run — returns exactly zero BK data for any date after 2026-05-20, for both daily and intraday timeframes. The request mechanism itself is confirmed working (AAPL sanity check succeeds for the same and later period). A targeted re-pull would return the identical empty result; **this is not repairable via re-download**.

Root cause is genuinely unresolved: the raw archived data (up to 2026-05-20) shows nothing abnormal — realistic prices, realistic volumes, no error markers — and there is no delisting/rename evidence for BK anywhere in the repo (confirmed in the prior remediation's evidence table). Two explanations remain open and **could not be distinguished within this task's scope**: (a) a genuine BK-specific event or trading/reporting change after 2026-05-20 that this repository has no record of and that this review has no independent means to verify (the system date, 2026-07-18, is beyond general knowledge available to this review), or (b) a SIP/Alpaca-side technical or entitlement issue specific to this symbol. **This finding revises the prior remediation report's characterization** ("the signature of a data acquisition gap") — the live evidence gathered here shows it is specifically *not* an acquisition-side (download/retry) failure; it is a genuine provider-side data absence, confirmed as of today, cause unknown.

---

## Task C — ABNB cross-check (1 of 2 requests used)

Probed SIP live for ABNB's full IPO-day session, 2020-12-10 5-minute bars: **66 rows returned, first-trade timestamp `2020-12-10 13:35:00-05:00`** (13:35 ET) — matches the locally-archived `raw_5min/ABNB_5min.csv` exactly, row for row (verified against the first several rows: identical OHLCV).

**This is not a missing-data gap.** SIP's own history confirms trading genuinely did not begin until 13:35 ET (roughly 4 hours after the normal 9:30 open) — the raw 5-minute data was completely and correctly acquired; there is nothing to re-pull.

**However, the archived 130-minute file mislabels the session.** The first archived `ABNB_130min.csv` bucket for 2020-12-10 is labeled `16:40:00+00:00` (11:40 ET) with `Open: 146.0` — which is exactly the open of the raw 13:35 ET bar. The second archived bucket is labeled `18:50:00+00:00` (13:50 ET). This means the 130-min aggregation assigned session-start-anchored positional labels (the labels a *normal*, 9:30-opening day would carry) to buckets built from whatever rows actually exist, rather than computing labels from the true wall-clock time each bucket of rows covers. The validator (`_expected_intraday_starts`, which walks strictly from `market_open = 9:30 ET`) correctly expects a block labeled 9:30 ET that the archived, mislabeled data does not have — hence the "1 missing bar."

**Checked locally (no additional live probes) whether this generalizes**: yes, identically, for the other three listing-day symbols with the same failure signature:

| Symbol | Listing date | Raw first-trade time (local, ET) | Archived first 130min bucket label |
|---|---|---|---|
| ABNB | 2020-12-10 | 13:35 | 11:40 ET (mislabeled) |
| PLTR | 2020-09-30 | 13:35 | 10:40 ET (mislabeled) |
| RBLX | 2021-03-10 | 14:30 | 11:40 ET (mislabeled) |
| SNOW | 2020-09-16 | 12:35 | 10:40 ET (mislabeled) |

**Verdict: the gap is not real data absence and is not repairable by re-pull (the raw data is already complete). It is a 130-min aggregation labeling artifact.** This changes the recommendation from the prior remediation report's proposal (a `PARTIAL_BOUNDARY_SESSION` exception treating the symptom) to fixing the actual root cause in the aggregation step — see the repair-plan matrix below.

---

## Repair-plan matrix

| Portion | Bars | Fix category | Detail |
|---|---:|---|---|
| Early-close handling | 0 | — | Nothing to fix; confirmed already correct. |
| 2022-01-24 event | 202 | **(unresolved — needs its own bounded probe)** | Root cause not determined within this task's scope (Task A had no live-probe budget). Recommended next step: a bounded SIP probe (same pattern as Task B/C) against 2-3 of the 101 affected symbols for 2022-01-24 intraday data, to determine whether this is repairable via **(b) targeted SIP re-pull** or reflects a genuine provider-side gap on that date, before any further action. Full symbol/date list for the re-pull, if authorized, is in the Appendix (101 symbols × 2022-01-24). |
| ABNB/PLTR/RBLX/SNOW listing-day artifact | 4 | **(a) calendar/aggregation-handling correction** — not (b), not (c) | Root cause is confirmed: the 130-min aggregation function (`aggregate_to_130min`/its fast path, `testdata/vericekici_updated.py`) labels buckets by position from session start rather than by the wall-clock time each bucket of rows actually covers, for sessions where real data starts significantly after 9:30 ET. **Proposed fix (not implemented — requires operator approval, since it touches the aggregation path used for every symbol, not just these 4):** derive each 130-min bucket's label from `floor((row_time - market_open) / 130min) * 130min + market_open` computed per-row, rather than from positional/sequential grouping of whatever rows exist that session — i.e. wall-clock-anchor the bucket index instead of assuming row 0 of the day is always bucket 0's window. Functions likely affected: `aggregate_to_130min`, `_aggregate_130min_worker`, and whichever fast path produced these files (needs confirming against `_aggregate_fixed_rth_130m_fast` in `testdata/data_engine.py`, not read in full during this read-only task). Regression tests that would prove it: a fixture with a session whose real data starts mid-session (e.g. 13:35 ET) and asserts the resulting 130-min bucket labels reflect the *actual* wall-clock window the data falls in (11:40-13:50 ET label for data at 13:35), not the session-start-relative position; plus a regression fixture confirming normal, full 9:30-start sessions are unaffected (byte-identical output) by the change. **This is a validation/aggregation-logic change requiring explicit operator approval before any implementation** — not enacted here. |
| BK post-2026-05-20 | 93 (+2 shared with the Jan-24 event) | **(c) remains an open, unapproved finding — not a reviewed exception candidate** | Confirmed not repairable by re-pull (Task B). No delisting/rename evidence exists to support a reviewed `EARLY_TERMINAL` exception under the existing conditional-authorization criteria (provable listing/delisting/ticker-change event). Recommend the operator investigate BK's status through a source independent of this codebase (e.g. Alpaca support/status page, or an authoritative market-data source) before deciding whether this becomes a reviewed exception, a data-provider support ticket, or stays an open, permanently-unapproved gap. |

**No portion of the 299 residual bars is currently ready for immediate remediation without further operator-authorized work**: the Jan-24 event needs its own bounded probe to even determine which repair category it belongs to; the listing-day artifact needs an approved code change to the aggregation path; BK needs external verification this review cannot perform.

---

## Appendix

**101 symbols affected by the 2022-01-24 event** (missing the 11:40 ET and 13:50 ET 130-min blocks; 9:30 ET block present):

ABBV, ABT, ALB, AMT, ANET, APD, AXP, BA, BAC, BK, BLK, BMY, BSX, BX, C, CARR, CAT, CCL, CI, CL, CMG, COF, COP, CRM, CVS, CVX, DD, DE, DG, DHR, DIS, DLR, DOW, DUK, ELV, KMB, KO, KR, LIN, LLY, LMT, LOW, MA, MCD, MCO, MDT, MMM, MO, MPC, MRK, MS, NEE, NEM, NET, NKE, NOC, NOW, NUE, O, ORCL, OTIS, OXY, PEG, PFE, PG, PH, PLD, PLTR, PM, PNC, PSA, PSX, RBLX, RCL, RTX, SCHW, SHOP, SHW, SLB, SNOW, SO, SPGI, SRE, SYK, T, TGT, TJX, TMO, UBER, UNH, UNP, UPS, URI, USB, V, VLO, VZ, WFC, WMT, XOM, YUM

**32 dates BK is affected** (1 shared with the Jan-24 event, 31 unique to the post-2026-05-20 gap): 2022-01-24, 2026-05-21, 2026-05-22, 2026-05-26 through 2026-06-30 (all business days), 2026-07-01, 2026-07-02, 2026-07-06, 2026-07-07.

**Files/paths referenced**: `testdata/v2_session_coverage.py`, `testdata/data_engine.py`, `testdata/vericekici_updated.py`, `testdata/research_data/rs_v2_sip_market_data/manifests/acq_20260716_135458/acquisition_manifest.json`, `testdata/research_data/rs_v2_sip_market_data/{daily,bars_130min,raw_5min}/*.csv`, `reports/audit/phase2b_remediation_20260717.md`.
