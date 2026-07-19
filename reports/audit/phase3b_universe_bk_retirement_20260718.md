# Phase 3B — BK Universe Retirement Audit Report

**Date:** 2026-07-19
**Operator Authorization:** gnesh, 2026-07-18/19
**Reason:** BK (Bank of New York Mellon) renamed to BNY effective 2025-07-07; CUSIP unchanged; ticker symbol BK retired from exchanges.
**Evidence hash:** e5e3785a (operator-supplied; see impact disclosure)
**Strategy impact disclosure:** [phase3b_bk_retirement_impact.md](file:///home/gnesh/Desktop/BOT%20-%20Kopya/reports/audit/phase3b_bk_retirement_impact.md) — total return delta: **-0.09 percentage points** (approved).

## Universe Transition

| Property | Old (178) | New (177) |
|---|---|---|
| Candidate count | 178 | 177 |
| BK present | Yes | No |
| Candidate universe hash | `0187534f0334d762a99e8d76f4077d44eff833f2d4a782ee8c691734b62c2611` | `026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933` |
| All other symbols | Identical, same ordering | Identical, same ordering |

## Archived Artifacts (Immutable — NOT Overwritten)

| File | Purpose |
|---|---|
| `testdata/baselines/archive/rs_m126_top30_sma100_no_regime_v2_clean_stock.178symbols.20260718.json` | Original 178-symbol main baseline |
| `testdata/baselines/archive/rs_v2_sip_baseline.178symbols.20260718.json` | Original 178-symbol SIP baseline |

## Active Artifacts Updated

| File | Change |
|---|---|
| `testdata/baselines/rs_m126_top30_sma100_no_regime_v2_clean_stock.json` | 178→177, BK removed, hash updated |
| `testdata/baselines/rs_v2_sip_baseline.json` | 178→177, BK removed, hash updated |
| `testdata/runner.py` | SIP+non-SIP validator: hash→`026f66d1...`, count→177 |
| `testdata/v2_daily_gap_selection_audit.py` | EXPECTED_CANDIDATE_COUNT→177, EXPECTED_CANDIDATE_HASH→`026f66d1...` |
| `testdata/vericekici_updated.py` | `load_v2_frozen_universe`: len check 178→177, hash constant updated |
| `testdata/quantconnect_port_contract.py` | `logical_acquisition_item_count`: 360→358 |
| `tests/test_rs_v2_clean_data.py` | EXPECTED_HASH→`026f66d1...`, 3 BK retirement tests added |

## NOT Changed (Immutable Historical Reference)

| File | Reason |
|---|---|
| `testdata/vericekici_updated.py` L978 `candidate_count: 178` | Gap recovery frozen context — references historical `acq_20260712_212749` manifest which was acquired with the 178 universe; changing would break the frozen contract check |
| On-disk SIP manifests (`acq_20260718_*`) | Immutable historical acquisition artifacts; contain `candidate_count: 178` correctly reflecting the universe at acquisition time |

## Fail-Closed Verification

1. **`test_bk_retirement_hash_verification_fails_closed_if_bk_is_reinserted`** — tampers baseline by reinserting BK → `load_v2_frozen_universe` raises `V2_UNIVERSE_CONTRACT_MISMATCH` ✅
2. **`test_frozen_universe_rejects_contract_mismatch`** — reversed symbol order → rejection ✅
3. **On-disk manifest rejection** — existing `acq_20260718_154844` manifest has `candidate_count: 178, hash: 0187534f...`; runner's `validate_v2_clean_dataset` now requires `candidate_count: 177, hash: 026f66d1...` → pipeline will fail-closed until re-acquired with the new 177 baseline ✅

## Regression Tests

| Test | Result |
|---|---|
| `test_bk_retirement_universe_loads_as_177_without_bk` | OK — 177 symbols, BK absent, count=177 |
| `test_bk_retirement_hash_verification_passes_for_current_untampered_list` | OK — recomputed hash matches baseline hash matches EXPECTED_HASH |
| `test_bk_retirement_hash_verification_fails_closed_if_bk_is_reinserted` | OK — tampered list rejected |
| `test_frozen_universe_hash_is_baseline_derived_not_legacy_inventory` | OK — 177 symbols, sorted, no QQQ/SPY/BK |
| `test_plan_is_358_and_unaffected_by_legacy_inventory` | OK — (177+2)×2 = 358 items |
| All 19 test_rs_v2_clean_data tests | OK |

## Safety

| Property | Value |
|---|---|
| submit_order_called | false |
| post_request_used | false |
| cancel_replace_used | false |
| broker_submit_enabled | false |
| live_trading_enabled | false |
| short_trading_enabled | false |
| Trade logic changed | No |
| Benchmark changed | No |
| Strategy logic changed | No |
| Durable ledger changed | No |
| Report paths preserved | Yes |

## On-Disk Manifest State

The current `latest_manifest.json` points to `acq_20260718_154844` which was acquired under the **old 178-universe** contract. The updated validators will reject this manifest at pipeline startup. A fresh `--live` acquisition under the new 177-universe baseline is required before the next pipeline run.

## Full Test Suite

```text
Ran 654 tests in 282.011s
OK
```

Reconciled count:
- Prior baseline: 651 tests
- New tests: +3 BK retirement tests (`test_bk_retirement_universe_loads_as_177_without_bk`, `test_bk_retirement_hash_verification_passes_for_current_untampered_list`, `test_bk_retirement_hash_verification_fails_closed_if_bk_is_reinserted`)
- Total expected: 654 tests (654 passed)
