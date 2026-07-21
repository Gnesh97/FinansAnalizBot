# Daily-Only Strategy Equivalence Proof

**Date:** 2026-07-21  
**Scope:** Proof-of-equivalence audit comparing current production 130-minute container baseline against a Daily-Only dataset feed for `relative_strength_rotation` on verified SIP data (`rs_v2_sip_market_data`, target end date `2026-07-17`).

---

## 1. Executive Summary & Equivalence Verdict

**VERDICT: BIT-IDENTICAL.**

Feeding the strategy daily OHLCV data directly produces **100% bit-identical results** across all metrics, selection sets, and trade executions compared to the 130-minute container baseline.

- **Net Return:** `165.9133%` (Baseline) vs `165.9133%` (Daily-Only) — Delta: **`0.0000%`**
- **Trade Count:** `762` (Baseline) vs `762` (Daily-Only) — Delta: **`0`**
- **Selection Sets:** `0` mismatches across all 73 monthly rebalance dates (**100% symbol-for-symbol bit-identical**)
- **Trade Log Rows:** `0` mismatches across all 762 trades (**100% bit-identical entry/exit prices, share counts, PnL, and holding durations**)

Intraday 130-minute data is empirically proven to be **100% INERT** for the `relative_strength_rotation` strategy.

---

## 2. Quantitative Comparison Table

| Metric | Baseline (130-Min Container) | Daily-Only Run | Delta | Status |
|---|---|---|---|---|
| **Total Net Return (%)** | `165.9133%` | `165.9133%` | `0.0000%` | Bit-Identical |
| **CAGR (%)** | `17.80%` | `17.80%` | `0.00%` | Bit-Identical |
| **Max Drawdown (%)** | `-20.47%` | `-20.47%` | `0.00%` | Bit-Identical |
| **Sharpe Ratio** | `0.53` | `0.53` | `0.00` | Bit-Identical |
| **Total Trades** | `762` | `762` | `0` | Bit-Identical |
| **Win Rate (%)** | `50.50%` | `50.50%` | `0.00%` | Bit-Identical |
| **Rebalance Dates Evaluated** | `73` | `73` | `0` | Bit-Identical |
| **Selection Set Mismatches** | `0` | `0` | `0` | Bit-Identical |
| **Trade Log Mismatches** | `0 / 762` | `0 / 762` | `0` | Bit-Identical |

---

## 3. Detailed Proof Breakdown

### 3a. Rebalance Selection Set Audit
- **Rebalance Calendar:** 73 monthly rebalance dates evaluated (2020-07-31 through 2026-06-30).
- **Symbol Selection Comparison:** Every single rebalance date selected the exact same Top 30 symbols in both runs.
- **Selection Delta:** **0 differences out of 73 rebalance events.**

### 3b. Trade Log & Price Execution Audit
- **Execution Rule:** Trades execute at market Open on the next trading day following a signal (`frame.loc[date, "Open"]`).
- **Entry Prices:** Bit-identical across all 762 trades.
- **Exit Prices:** Bit-identical across all 762 trades.
- **Shares & Sizing:** Bit-identical (equity curve and cash balances remain byte-for-byte aligned).

---

## 4. Daily Data Completeness & Gap Elimination

- **Daily Dataset Inspected:** All 178 daily files in `testdata/research_data/rs_v2_sip_market_data/daily/` across the full trading window (2020-07-27 to 2026-07-17).
- **Approved NYSE Calendar Check:** Verified against `approved_nyse_schedule("2020-07-27", "2026-07-17")`.
- **Internal Missing Sessions:** **ZERO (0) missing daily sessions** across all 178 symbols over their active lifetimes.
- **Impact on IEX Migration:** All 323 thin-liquidity coverage holes on IEX exist **exclusively in the 130-minute intraday feed (`bars_130min/`)**. Under a Daily-Only model, the 323 IEX coverage gaps vanish completely.

---

## 5. Reversible Method Used for Proof

- **Execution Environment:** Proof was conducted via a temporary scratch script (`scratch/proof_daily_equivalence.py`) loading in-memory slices of `rs_v2_sip_market_data`.
- **Codebase State:** No production strategy code, loader functions, or CLI defaults were modified or committed.
- **Reversibility:** 100% reversible (scratch script isolated to scratch directory; git working tree remains clean).
