# Phase 5B — Free-Tier IEX Daily Gap Materiality & Viability Audit

**Date:** 2026-07-21  
**Scope:** Rigorous READ-ONLY investigation into the 350 internal daily session gaps discovered during free-tier IEX daily acquisition (`rs_v2_clean_market_data`), their exact overlap with strategy decision windows, decomposition of the 11.11-point backtest return delta, and an honest viability verdict.

---

## 1. Precise Enumeration of IEX Daily Gaps

- **Total Internal Missing Daily Sessions:** `350`
- **Symbols Affected:** `123` out of 179 series (121 candidates + QQQ + SPY)
- **Distinct Gap Dates:** `4` specific dates across the 6-year window

### Date Distribution of Gaps

| Missing Date | Affected Symbols | Category | Mechanism |
|---|---|---|---|
| **`2021-04-19`** | **122** | Cluster Venue Gap | Zero printed trades on single-venue IEX exchange |
| **`2022-03-08`** | **119** | Cluster Venue Gap | Zero printed trades on single-venue IEX exchange |
| **`2021-10-25`** | **108** | Cluster Venue Gap | Zero printed trades on single-venue IEX exchange |
| **`2022-04-06`** | **1** | Single-Symbol Gap | Zero printed trades on IEX for BK |
| **Total** | **350** | | |

*Note: Listing-day boundaries (e.g. ABNB, RBLX, PLTR, SNOW, WBD prior to IPO date) and delisting boundaries (PARA post-acquisition) are excluded from the 350 count above; the 350 count represents active-span internal session missing bars.*

---

## 2. Cross-Check Against Strategy Decision Windows

Across the 73 monthly rebalance dates and 73 execution dates spanning `2020-07-27` to `2026-07-07`:

| Decision Window Check | Gap Count | Status |
|---|---|---|
| **Gaps Directly ON a Monthly Rebalance Date** | **`0 / 350`** | Clean |
| **Gaps Directly ON a Next-Open Execution Date** | **`0 / 350`** | Clean |
| **Gaps INSIDE Trailing 126-Day Ranking Lookback Windows** | **`350 / 350`** | **CORRUPTED** |

### Mechanism of Trailing Window Corruption

While no gap lands directly on a rebalance date, **100% of the 350 gaps land inside trailing 126-day lookback windows** for subsequent rebalance dates:
- **`2021-04-19` (122 symbols)** corrupts ranking lookback windows for 6 rebalance dates: `2021-04-30`, `2021-05-28`, `2021-06-30`, `2021-07-30`, `2021-08-31`, `2021-09-30`.
- **`2021-10-25` (108 symbols)** corrupts ranking lookback windows for 6 rebalance dates: `2021-10-29`, `2021-11-30`, `2021-12-31`, `2022-01-31`, `2022-02-28`, `2022-03-31`.
- **`2022-03-08` (119 symbols)** corrupts ranking lookback windows for 6 rebalance dates: `2022-03-31`, `2022-04-29`, `2022-05-31`, `2022-06-30`, `2022-07-29`, `2022-08-31`.

When a daily session is missing from pandas DataFrame `rolling(126)`, the 126-period lookback shifts back by 1 calendar day (`t-127` instead of `t-126`) for affected symbols, altering 126-day momentum scores and 60-day volatility calculations.

---

## 3. Honest Decomposition of the 11.11-Point Net Return Delta

The backtest on unvalidated IEX daily data produced `177.02%` net return vs the `165.9133%` SIP baseline (a **+11.11%** delta). The previous characterization of this gap as "small feed variance ~3bps" is **INCORRECT**.

### Empirical Breakdown

1. **Genuine Price Feed Variance (Same Day, Same Symbol):**
   - **Median relative close price difference:** **2.24 bps** (`0.0224%`)
   - **Mean relative close price difference:** **3.85 bps** (`0.0385%`)
   - On common trading sessions, IEX daily closes match SIP consolidated closes within 2–4 bps. Price feed variance alone accounts for <0.5% of total return delta.

2. **Selection Set Corruption (Different Symbols Selected):**
   - **17 out of 72 rebalance dates (23.6%)** suffered Top-30 portfolio selection differences between SIP and IEX daily datasets.
   - On rebalance dates following gap clusters (e.g. `2021-04-30`, `2021-11-30`, `2022-05-31`), up to **4 symbols swapped in/out** of the Top 30 (e.g., IEX selected `AMZN`, `IBM`, `MPC`, `NEE` instead of `TGT`, `COST`, `ANET`, `VLO`).
   - Trade count dropped from 762 to 757 (-5 trades).

3. **Trustworthiness:**
   - The `177.02%` backtest result is derived from an **unvalidated dataset** (`FAILED_VALIDATION` with 350 missing internal sessions). It is **UNTRUSTWORTHY AND INVALID**. It cannot be presented as meaningful research evidence.

---

## 4. Unambiguous Viability Verdict

- **Finding:** Free-tier IEX daily data suffers from 350 venue-level trading gaps across 123 tickers due to zero-print sessions on the single IEX venue.
- **Impact:** All 350 gaps land inside trailing 126-day ranking windows, corrupting momentum/volatility rankings and changing Top-30 selections on 23.6% of all rebalance dates.
- **VERDICT:** Single-venue free-tier IEX daily data **MATERIALLY CORRUPTS RANKINGS AND IS NOT VIABLE FOR RESEARCH-GRADE SYSTEMATIC TRADING**.
- **Recommendation:** Consolidated multi-venue SIP data ($99/mo) or another consolidated data feed is required to maintain research-grade dataset integrity.
