# Phase 5A — Daily-Only Pipeline Conversion & Equivalence Guard

**Date:** 2026-07-21  
**Scope:** Architectural conversion of the dataset pipeline to Daily-Only OHLCV execution for `relative_strength_rotation`, freezing legacy 130-minute intraday code/data, and verifying post-conversion bit-identical strategy equivalence.

---

## 1. Summary of Architectural Conversion

The pipeline has been converted to feed daily OHLCV data directly into `asset_data` and stamp entry signals on the daily date timestamp. Legacy 130-minute intraday acquisition and container loading are preserved in code as a frozen fallback path (`legacy_intraday=True`) and on disk (`bars_130min/`).

---

## 2. Summary of Changes Made

### Files Modified

1. **`testdata/runner.py` (`_load_or_build_prepared_cache` & `_prepared_cache_key`)**
   - **Change:** Defaults to loading daily OHLCV files (`*_1D.csv`) directly into `asset_data[symbol]`, eliminating the requirement to wrap inside `bars_130min` DataFrames.
   - **Fallback:** Setting `legacy_intraday=True` preserves the 130-minute container wrapping path. Cache keys incorporate `legacy_intraday` to prevent cache collisions.

2. **`testdata/strategies/relative_strength_rotation.py` (`_daily_ohlcv_from_frame`)**
   - **Change:** Recognizes direct daily DataFrames via `df.attrs["is_daily"]` or `daily_timeframe == "1D"`, returning `df.copy()` without redundant resampling or circular `attrs` references.

3. **`testdata/vericekici_updated.py` (`build_v2_acquisition_plan`, `validate_v2_sip_manifest_and_files`, `validate_v2_manifest_and_files`)**
   - **Change:** Manifest validators dynamically support 179-item daily-only manifests (`logical_acquisition_item_count: 179`) alongside legacy 358-item dual manifests.
   - **Validation Contract:** For daily-only manifests, all 179 daily series (177 candidates + QQQ + SPY) are strictly checked for file integrity, SHA-256 hashes, approved NYSE calendar coverage, and exception registry rules. Validation remains strictly **FAIL-CLOSED**.

4. **`tests/test_daily_only_equivalence_guard.py` [NEW]**
   - **Change:** Permanent regression test locking daily-only execution output against the frozen baseline numbers (165.9133% Net Return, 762 trades, 50.5% Win Rate, -20.47% Max DD).

---

## 3. Post-Conversion Equivalence Proof

The converted production pipeline was executed directly on the verified SIP dataset (`rs_v2_sip_market_data` through 2026-07-17):

| Metric | Frozen Baseline | Converted Production Pipeline | Delta | Status |
|---|---|---|---|---|
| **Total Net Return (%)** | `165.9133%` | `165.9133%` | `0.0000%` | Bit-Identical |
| **CAGR (%)** | `17.80%` | `17.80%` | `0.00%` | Bit-Identical |
| **Max Drawdown (%)** | `-20.47%` | `-20.47%` | `0.00%` | Bit-Identical |
| **Sharpe Ratio** | `0.53` | `0.53` | `0.00` | Bit-Identical |
| **Total Trades** | `762` | `762` | `0` | Bit-Identical |
| **Win Rate (%)** | `50.50%` | `50.50%` | `0.00%` | Bit-Identical |
| **Selection Set Mismatches** | `0 / 73` | `0 / 73` | `0` | Bit-Identical |
| **Trade Log Mismatches** | `0 / 762` | `0 / 762` | `0` | Bit-Identical |

---

## 4. Frozen Assets & Reversibility

- **Intraday Data (`bars_130min/`):** Retained on disk under `testdata/research_data/*/bars_130min/`, untouched and frozen.
- **Acquisition Code:** 130-minute aggregation (`aggregate_to_130min`) and raw 5-minute derivation remain intact in `vericekici_updated.py`.
- **Reversibility:** Passing `legacy_intraday=True` to `_load_or_build_prepared_cache` or specifying `logical_acquisition_item_count: 358` in manifests instantly activates the legacy intraday container path.

---

## 5. Test Suite Verification

- **Full Suite Run:** `Ran 673 tests in 428.86s — OK`.
- **Reconciliation:** 672 baseline tests + 1 new equivalence guard test = **673 total tests passing**.
- **Code Review (ECC):** Reviewed via subagent; identified cache key parameterization resolved.
