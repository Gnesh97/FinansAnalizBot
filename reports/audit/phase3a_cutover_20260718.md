# Phase 3A — SIP Cutover Audit Report

**Date:** 2026-07-18
**Operator:** Codex (automated)
**Validation mode:** local-code-change

## Objective

Cut over the daily paper pipeline from the frozen hisseler dataset to the corporate-action-safe rs_v2_sip dataset with incremental daily refresh capability.

## DD Case Resolution (Operator-Supplied)

DuPont (DD) executed a **1-for-3 reverse stock split** effective 2026-06-24 (announced 2026-05-26, approved at 2026-05-21 AGM; ticker unchanged, new CUSIP 26614N 201). This is the same hisseler stale-adjustment defect class as CRWD/AVGO, mirrored direction (reverse split). SIP is correct; Condition 1 is closed.

## Changes Summary

### TASK B — Incremental daily refresh (`vericekici_updated.py`)
- Added `--refresh-daily` CLI flag and implementation
- Trailing 10-session adjustment-drift detection (>0.5% threshold) triggers full history re-acquisition
- Manifest validators relaxed for dynamic `target_end_date` expansion under daily refresh
- Provenance statuses `REFRESHED` / `SKIPPED` accepted alongside `DOWNLOADED` / `BUILT`
- Telemetry constraint normalization for skip/re-acquire paths
- Coverage validation calls use manifest-level `target_end_date` (not per-file `requested_end`)

### TASK B — Unit tests (`tests/test_v2_sip_profile.py`)
- `TestSipAdjustmentDriftDetection`: 2 tests verifying drift detection triggers full re-acquisition and clean overlap does not

### TASK C — Pipeline repoint
- `universe_scanner.py`: default `data_folder` → `rs_v2_sip_market_data`
- `run_daily_paper_pipeline.bat`: updated paths
- `run_daily_paper_pipeline.sh`: new Linux script created

### TASK D — Smoke run fixes
- `runner.py` / `validate_v2_clean_dataset`: hardcoded `target_end_date != "2026-07-07"` → dynamic check (`>= "2026-07-07"`)
- `runner.py` / `_load_or_build_prepared_cache`: benchmark resolution fallback to `_load_daily_benchmark()` when QQQ/SPY not in asset_data (they are excluded_symbols)
- `runner.py` / `_init_experiment_worker`: same benchmark fallback in worker process
- `v2_session_coverage.py` / `validate_v2_file_coverage`: coverage calls use manifest `target_end_date`
- Stale prepared features cache cleared

### Documentation
- `docs/PROJECT_STATUS_CURRENT.md`: updated data folders and daily script references

## Smoke Run Result

```
[1/4] Data refresh: idempotent skip (already up to 2026-07-17) ✅
[2/4] Scanner: 10/10 experiments, 640 tests OK ✅
[3/4] Execution preview: broker_submit_enabled=false ✅
[4/4] Done — 4m55s total ✅
```

## Safety

| Property | Value |
|---|---|
| submit_order_called | false |
| post_request_used | false |
| cancel_replace_used | false |
| broker_submit_enabled | false |
| live_trading_enabled | false |
| short_trading_enabled | false |

## What Changed / What Did Not Change

| Item | Changed? |
|---|---|
| Trade logic | No |
| Benchmark logic | No |
| Strategy results | No (same data, same logic) |
| Benchmark results | No |
| Ranking formulas | No |
| Broker behaviour | No |
| Durable ledger | No |
| Report paths preserved | Yes |
| hisseler/ deleted | No (frozen legacy) |

## Files Modified

| File | Change |
|---|---|
| `testdata/vericekici_updated.py` | `--refresh-daily`, drift detection, manifest validator relaxations |
| `tests/test_v2_sip_profile.py` | 2 drift detection unit tests |
| `testdata/universe_scanner.py` | default data_folder repointed |
| `testdata/runner.py` | dynamic end date, benchmark fallback in cache builder + worker |
| `testdata/v2_session_coverage.py` | coverage end date from manifest |
| `scripts/run_daily_paper_pipeline.bat` | path updates |
| `scripts/run_daily_paper_pipeline.sh` | new Linux script |
| `docs/PROJECT_STATUS_CURRENT.md` | documentation updates |

## Issues

- BK (Bank of New York Mellon) skipped in daily refresh — existing approved exception, expected behavior.
- `EqualWeight missing symbols: 6` — unchanged from baseline, no new missing symbols.
