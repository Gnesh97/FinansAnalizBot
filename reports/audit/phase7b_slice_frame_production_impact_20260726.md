# Phase 7B — Does the `_slice_frame` End-Date Trap Reach Production?

**Date:** 2026-07-26
**Scope:** Read-only. Trace every `_slice_frame` caller on the nightly DSS path and determine empirically whether the operator's BUY/SELL report ranks on data missing the most recent session. No code changed, nothing committed, timer and scheduler state untouched.

---

## Verdict: **NOT AFFECTED**

The nightly DSS chain never passes an end bound at all, so the trap cannot fire on it. The ranking window includes the most recent session, proven from the data the strategy actually consumed. One **adjacent** path — the optional OOS walk-forward diagnostics — *is* affected, but it is off the nightly path and does not touch the BUY/SELL report.

---

## 1. Every `_slice_frame` Call Site, and What It Passes

`testdata/runner.py:1444`:

```python
def _slice_frame(frame, start_date=None, end_date=None):
    if start_date:
        start = pd.Timestamp(start_date, tz=frame.index.tz)
        frame = frame.loc[frame.index >= start]
    if end_date:
        end = pd.Timestamp(end_date, tz=frame.index.tz)
        frame = frame.loc[frame.index <= end]     # <-- bare "YYYY-MM-DD" resolves to 00:00Z
    return frame
```

Note the guards: **both bounds are skipped entirely when falsy.** `None` means no filtering, so an unbounded call cannot drop anything.

| # | Call site | Caller | Bound passed | Trap? |
|---|---|---|---|---|
| 1 | `runner.py:1708` daily frames | `_load_or_build_prepared_cache` | forwarded from caller | depends on caller |
| 2 | `runner.py:1718` intraday frames | `_load_or_build_prepared_cache` | forwarded from caller | depends on caller |
| 3 | `runner.py:1767` benchmark | `_load_daily_benchmark` | forwarded from caller | depends on caller |
| 4 | `runner.py:2134` | `_slice_benchmark_for_period` | OOS period end, **bare date** | **yes** |
| 5 | `runner.py:2142` | `_slice_asset_data_for_period` | OOS period end, **bare date** | **yes** |
| 6 | `runner.py:2147` | `_slice_asset_data_for_period` (`daily_ohlcv` attr) | OOS period end, **bare date** | **yes** |

Sites 1–3 are the ones the nightly chain executes. They inherit whatever `run_strategy_experiments` was given.

### The nightly path, end to end

`dss_scheduler.py` → `universe_scanner.py --data-profile rs_v2_tiingo --paper-report` → `run_strategy_experiments(...)` → `_load_or_build_prepared_cache(...)` → `_slice_frame`.

`run_strategy_experiments` declares (`runner.py:7811-7812`):

```python
    start_date=None,
    end_date=None,
```

and `universe_scanner.py:479-501` calls it **without either argument** — `grep -n "start_date\|end_date" testdata/universe_scanner.py` returns nothing at all. The scheduler's chain (`dss_scheduler._chain_steps`) invokes exactly that command with no extra flags.

So the nightly call is `_slice_frame(frame, None, None)` → both `if` guards false → **frame returned untouched, no slicing performed**.

`dss_report.py` slices nothing: `_load_signal_rows` reads `paper_signal_report.csv` and filters by `strategy_name` only. The refresh step (`vericekici_updated --refresh-daily`) does not use `_slice_frame`; its window handling goes through `approved_nyse_schedule` / `calculate_v2_session_coverage`, which normalise in market timezone.

## 2. Empirical Proof the Latest Session Was Ranked

Loading through the exact production path the paper-report step uses (`start_date=None, end_date=None`, all 177 Tiingo series):

```
dataset AAPL rows on disk : 1503  last bar: 2026-07-21 04:00:00+00:00
consumed AAPL rows        : 1503  last bar: 2026-07-21 00:00:00-04:00
distinct last sessions consumed across 177: ['2025-08-07', '2026-07-21']
```

**1503 on disk, 1503 consumed** — no row lost. The `2026-07-21` bar is inside the ranking window. (`2025-08-07` is PARA, the approved `EARLY_TERMINAL` exception, so its earlier terminal session is expected.)

Corroborated by the artifact the Phase 6D report was rendered from:

```
signal_date(s): ['2026-07-21 00:00:00-04:00']
main-strategy rows: 30 | actions: {'BUY': 30}
```

The signal date **is** the dataset's final session. Had the trap fired, signals would have been stamped `2026-07-20` and one session of returns would be missing from every ranking score. It was not.

This also matches Phase 7A's contrast: the truncation there produced 1500 rows for a 1501-row SIP series. Production shows no such shortfall.

## 3. Freshness Check vs Ranking Window — They Agree

| Quantity | Source | Value |
|---|---|---|
| Manifest `target_end_date` | validated Tiingo manifest | `2026-07-21` |
| Last session the ranking consumed | production loader (§2) | `2026-07-21` |
| `dss_report._latest_expected_session()` (run today) | approved NYSE calendar | `2026-07-24` |

The two that must agree — **the freshness check's subject and the ranking window** — do agree: the freshness check compares the manifest's `target_end_date` against the latest completed session, and the ranking consumes the dataset through exactly that same `target_end_date` (unbounded load of a dataset validated to that end). There is no configuration in which the check demands session N while the ranking silently uses N−1, because the ranking's end is *defined* by the manifest end the check inspects.

The `2026-07-24` value is simply today's staleness — the dataset has not been refreshed since 2026-07-21, so the check correctly fail-louds. That is the known, intended behaviour, not a slicing artifact.

## 4. Secondary Finding — OOS Walk-Forward Diagnostics *Are* Affected

Off the nightly path, but the same defect class, so it is named here rather than left for someone to rediscover.

`_oos_walkforward_periods()` returns bare date strings, which flow to `_slice_asset_data_for_period` / `_slice_benchmark_for_period` and hence to `_slice_frame`. The trap only bites when the period's end date **is itself a trading session** (otherwise the last real bar already precedes the 00:00Z bound). Checked against the approved calendar:

| Period | End | End is a session? | Final session lost |
|---|---|---|---|
| `split_2020_2022` | 2022-12-31 | no (Saturday) | no |
| `split_2023_2024` | 2024-12-31 | **yes** | **YES** |
| `split_2025_2026` | 2026-06-18 | **yes** | **YES** |
| `anchored_insample_2020_2023` | 2023-12-31 | no (Sunday) | no |
| `anchored_oos_2024_2026` | 2026-06-18 | **yes** | **YES** |

**3 of 5 OOS periods silently drop their final session.** Impact is confined to OOS diagnostic reporting: one missing session per affected split slightly shifts that split's return and can move a boundary trade. `--run-oos` is `action="store_true"`, default off, and the scheduler's fixed chain never passes it — so no BUY/SELL decision has ever been affected by this.

## 5. Why No Fix Was Made

Per instruction, and because it is the right call regardless: `_slice_frame` is shared strategy-path code. Making the end bound inclusive would change every OOS split result and would invalidate any baseline computed through a bounded path — including the Phase 7A Tiingo baseline, which deliberately compensates by passing an explicit `2026-07-21 23:59:59` bound. Changing the helper and leaving that compensation in place would double-correct.

Options for the operator, not actioned:
1. **Leave as-is**, treating "end bound is exclusive of that session's bar" as the documented contract, with callers passing end-of-day bounds (what the Phase 7A builder now does).
2. **Normalise inside `_slice_frame`** (e.g. treat a date-only string as end-of-day) — fixes all callers at once, but shifts 3 OOS split results and requires re-freezing anything measured through a bounded path.
3. **Fix only `_oos_walkforward_periods`** by giving those five entries end-of-day bounds — narrowest blast radius, corrects the one place demonstrably wrong, leaves `_slice_frame` semantics untouched.

## 6. Hard-Rule Compliance

- Read-only: no source file modified, nothing committed, no `--live` acquisition, zero API calls.
- Systemd timer and scheduler state untouched; next fire remains Mon 2026-07-27 01:00 +03.
- Strategy/ranking/benchmark logic, frozen 177 universe/hash, validators and the freshness check all unchanged.
- rs_v2_sip / rs_v2_clean datasets and archived decision events untouched.
- No credential value read, echoed, or logged. No broker/order/live-trading calls.
- One side effect disclosed: loading through the production path may write a derived `.cache/prepared_features/*.pkl` entry for the Tiingo dataset. That is regenerable cache, not source data.

---

*No GO/NO-GO — evidence and state only.*
