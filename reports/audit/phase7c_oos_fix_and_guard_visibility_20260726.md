# Phase 7C — OOS End-Bound Re-measurement and Baseline-Guard Visibility

**Date:** 2026-07-26
**Scope:** Cleanup turn. Item 1 — fix the OOS end-date defect. Item 2 — make the silent baseline-guard skip loud. Item 3 — commit outstanding work.

---

## Headline: Item 1 was a fix for a defect that does not exist

The instruction for Item 1 was premised on Phase 7B §4, which stated that 3 of the 5 OOS
walk-forward periods silently drop their final trading session. **That claim is wrong.**
It was reached by calendar reasoning ("the period end is itself a trading session,
therefore a 00:00Z bound excludes it") without measuring the timezone of the frames the
OOS path actually slices.

I implemented the requested fix first, then measured it. The measurement showed the fix
changed nothing, because there was nothing to change. I reverted it and shipped the
correction instead. Details and evidence below.

---

## 1. Item 1 — OOS End Bound: Before vs After

### 1.1 The measurement that was asked for

Harness replicates the exact OOS slice path (`runner.py:2058`,
`_slice_asset_data_for_period`), loading all 177 Tiingo series through the production
loader and slicing each period two ways: the current bare date, and the proposed
end-of-day bound.

```
period                         end_session  OLD last     NEW last       rows old   rows new   delta
----------------------------------------------------------------------------------------------------
split_2020_2022                2022-12-31   2022-12-30   2022-12-30       108342     108342       0  same
split_2023_2024                2024-12-31   2024-12-31   2024-12-31        88854      88854       0  same
split_2025_2026                2026-06-18   2026-06-18   2026-06-18        64565      64565       0  same
anchored_insample_2020_2023    2023-12-31   2023-12-29   2023-12-29       152592     152592       0  same
anchored_oos_2024_2026         2026-06-18   2026-06-18   2026-06-18       109169     109169       0  same
```

The three periods Phase 7B named as affected — `split_2023_2024`, `split_2025_2026`,
`anchored_oos_2024_2026` — **already consume their final session** (`2024-12-31`,
`2026-06-18`, `2026-06-18`). The two Saturday/Sunday-ended periods correctly stop at the
prior session. Row counts are identical to the unit under both bounds. There is no
session to gain.

### 1.2 Why Phase 7B was wrong

`_slice_frame` resolves its bound in the frame's own timezone:

```python
end = pd.Timestamp(end_date, tz=frame.index.tz)
frame = frame.loc[frame.index <= end]
```

Everything hinges on `frame.index.tz`. Measured:

```
PREPARED frame (what the OOS path slices)
  index tz      : America/New_York
  bars near 2024-12-31: ['2024-12-30 00:00:00-05:00', '2024-12-31 00:00:00-05:00', '2025-01-02 00:00:00-05:00']
  bound Timestamp('2024-12-31', tz=index.tz) = 2024-12-31 00:00:00-05:00
  bare-date slice keeps 2024-12-31? -> 2024-12-31 00:00:00-05:00

RAW on-disk frame (NOT what the OOS path slices)
  index tz      : UTC
  last bar      : 2026-07-21 04:00:00+00:00
  bare-date slice at 2024-12-31 -> 2024-12-30 05:00:00+00:00
```

`read_ohlcv_csv_fast` routes every load through `data_engine._to_new_york_index`
(`data_engine.py:188-197`), which converts to `America/New_York`. A daily bar stored at
NY-midnight-in-UTC therefore lands at **exactly `00:00` local** — equal to a bare-date
bound, and `<=` keeps equality. The truncation is real only on **UTC-indexed** frames,
which is what a plain `pd.read_csv` produces. That is an ad-hoc analysis-harness
artifact, not a production path — the same class of methodology error Phase 7A itself
was convened to root-cause.

Two further facts close the question:

- **No intraday exposure.** An intraday bar (09:30 NY) *would* be cut by a bare-date
  bound. But `legacy_intraday` defaults to `False` (`runner.py:1652`) and
  `grep -rn "legacy_intraday" --include="*.py"` finds **no caller anywhere** that passes
  `True`. The intraday branch at `runner.py:1717` is dead, so prepared frames are always
  daily.
- **`--run-oos` is off by default** and the scheduler chain never passes it, so even had
  the defect been real, no BUY/SELL decision was ever affected. (Unchanged from 7B.)

### 1.3 What was shipped instead

The 4-tuple `(name, start, end_session, end_bound)` change was written, then reverted.
`_oos_walkforward_periods` retains its original three-element entries and identical
values; it gains only a docstring recording the measurement and the invariant it
depends on. `_slice_frame` untouched, as instructed. Phase 7B option 2 remains rejected.

New guard: `tests/test_slice_frame_end_bound_invariant.py` (5 tests, synthetic data only
so it runs on a clean clone) pins all of it —

| Test | Locks |
|---|---|
| `test_ny_indexed_daily_bar_survives_a_bare_date_end_bound` | the invariant production relies on |
| `test_utc_indexed_daily_bar_is_dropped_by_a_bare_date_end_bound` | the real failure mode, so it stays documented |
| `test_intraday_bar_is_dropped_by_a_bare_date_end_bound` | why the invariant is daily-only |
| `test_production_csv_loader_indexes_in_market_timezone` | the loader contract that creates the invariant |
| `test_every_oos_period_retains_its_end_session` | each of the five windows directly |

If the index timezone changes, or `legacy_intraday` is ever switched on, these fail
loudly instead of silently shortening every OOS window by a session.

### 1.4 Instruction item 4 — no OOS figures change

The operator asked that the report note OOS results will now differ from previously
reported figures. **They will not.** `_oos_walkforward_periods` returns byte-identical
values, so any previously published OOS split result stands unchanged. There is no
correction to absorb — only the Phase 7B *narrative* was wrong, not the numbers.

## 2. No Collateral Movement

**Nightly path (passes no bounds).** Unaffected by construction: the only code change to
`runner.py` is a docstring. `git diff` of the whole file is 10 added comment lines and
zero statement changes.

**Phase 7A Tiingo baseline (passes its own explicit end-of-day bound).** Loaded through
the production harness both ways:

```
bare date        | last=2026-07-21 rows=265457 ret=175.4900 trades=770 fp=ba697e9653db37bd
end-of-day bound | last=2026-07-21 rows=265457 ret=175.4900 trades=770 fp=ba697e9653db37bd
```

Bit-identical, and both equal the frozen baseline. This also shows the Phase 7A builder's
`23:59:59` compensation is a no-op on the production loader — harmless, and left in place
because the artifact is frozen and the explicit bound is self-documenting.

**Governing guard (Tiingo, 6 tests):** `Ran 6 tests ... OK` — metrics, selection
fingerprint `ba697e9653db37bd...`, frozen-177 universe and target-session assertions all
pass at their pinned values.

**Historical guard (SIP, 1 test):** passes at its pinned `165.9133 / 762 / 50.50 /
−20.47` on the 178-series on-disk set. No pinned value was touched in either guard.

## 3. Item 2 — Baseline-Guard Visibility

### 3.1 Approach

Skips are **not** converted to failures, per instruction. Instead each guard announces
itself at import time, and the suite reports a count. Skip→fail was rejected because it
breaks clean-clone and developer environments and diverges from the existing SIP
convention.

- `tests/baseline_guard_visibility.py` — `announce_if_inactive()` prints a 78-character
  banner naming the guard, why it is inactive, and how to restore it; emits a stable
  `[BASELINE-GUARD-INACTIVE]` marker; announces once per guard.
- Wired into `tests/test_rs_v2_tiingo_strategy_baseline.py` and
  `tests/test_daily_only_equivalence_guard.py`. The SIP guard's in-method
  `self.skipTest(...)` became a class-level `@unittest.skipUnless` on the same condition,
  so the announcement happens at import.
- `testdata/run_test_suite.py` counts markers into a closing `SUITE SUMMARY` line and a
  new `## Inactive Baseline Guards` section plus `- inactive baseline guards: N` field in
  `testdata/reports/latest/test_run_report.md`.

### 3.2 State A — dataset present (this machine)

```
Ran 743 tests in 1012.024s

OK

SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
```

`grep -cE "skipped|BASELINE-GUARD-INACTIVE"` over the full output returns `0`. Report:

```
- status: OK
- tests ran count: 743
- inactive baseline guards: 0
...
## Inactive Baseline Guards

- none (all baseline guards active)
```

### 3.3 State B — dataset absent (real clean clone)

Demonstrated on an actual `git clone` of this branch, where `research_data/` is simply
absent because it is gitignored — not a simulation.

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
[BASELINE-GUARD-INACTIVE] guard=rs_v2_tiingo_strategy_baseline
  THIS REGRESSION GUARD DID NOT RUN. A green suite does NOT mean this
  baseline was verified.
  reason : missing testdata/research_data/rs_v2_tiingo_market_data
  restore: acquire the rs_v2_tiingo dataset, then rerun scripts/build_tiingo_strategy_baseline.py
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

ssssss
Ran 6 tests in 0.000s
OK (skipped=6)
```

Full suite on the clean clone:

```
Ran 731 tests in 21.989s
FAILED (errors=6, skipped=10)
SUITE SUMMARY: 2 baseline guard(s) INACTIVE and NOT verified by this run: rs_v2_sip_daily_only_equivalence, rs_v2_tiingo_strategy_baseline
```

### 3.4 What a developer or CI actually sees

- **Before:** `OK (skipped=6)` and nothing else. Nothing named the guard, nothing said the
  governing baseline was unverified. A green suite was indistinguishable from a verified one.
- **After:** a banner per inactive guard naming it, the reason, and the fix; and a
  `SUITE SUMMARY` line printed *after* the unittest verdict, so it is the last thing on
  screen. CI that only captures the tail still sees it. The operator-facing
  `test_run_report.md` carries the same count in a dedicated field and section.
- **Exit code is unchanged** — a skipped guard still does not fail the suite, by design.
  Anyone wanting hard enforcement can gate on `- inactive baseline guards: 0`.

### 3.5 Clean-clone errors are pre-existing

The clean clone reports `errors=6`. These are **not** from this turn — they are
data-dependent modules that error instead of skipping when `research_data/` is absent:
`test_rs_v2_clean_data` (×2), `test_rs_v2_gap_recovery_probe` (×2),
`test_delisted_alpaca_price_coverage`, `test_quantconnect_port`. Verified by running the
identical clean-clone discovery at the pre-Phase-7C commit `a44e420`:

```
Ran 719 tests in 21.392s
FAILED (errors=6, skipped=10)
```

Same six test names, same counts. 719 → 731 is exactly the +12 tests added this turn.
Left untouched: converting them to skips is out of scope here and would need its own
review.

## 4. Item 3 — Commit Record

Phase 7A was **already committed** in `a44e420` (builder script, baseline JSON, 6 guard
tests, SIP relabel + scope assertions) — the turn's premise that it was outstanding was
inaccurate. Phase 7B was read-only and left only its report untracked.

```
ea8611c test(guards): keep the wiring failure message readable
3fec024 test(guards): protect the inactive-guard wiring itself
c42d90c test(guards): name only the paths that are actually missing in the inactive-guard notice
5c1eb7b test(guards): correct the OOS end-bound finding and surface inactive baseline guards
51624be docs(audit): file Phase 7B slice_frame production-impact report
a44e420 feat(baseline): freeze rs_v2_tiingo strategy baseline, root-cause the 165.9133 vs 167.24 split
2fc25c1 fix(scheduler): harden Linux launcher interpreter resolution, file Phase 6F install report
67f248e docs(audit): file Phase 6E preflight readiness and review report
```

The last two of those (`3fec024`, `ea8611c`) came out of the code review below.
This report is committed separately, after it.

`scratch/` and `research_data/` excluded as instructed — `scratch/tiingo_fetch_adapter.py`,
`scratch/phase5d_analysis_output.json` and `scratch/tiingo_full_universe_analysis.py`
remain uncommitted.

### The systemd unit is machine-local, not version-controlled

`git ls-files | grep -i "systemd\|dss-daily"` returns nothing. The units live at
`~/.config/systemd/user/dss-daily.{service,timer}` — outside the repository. The Phase 7A
`203/EXEC` repair is therefore **not** captured in git and cannot be; it is host state.
Recorded here so it is reconstructible:

```ini
Type=oneshot
WorkingDirectory=/home/gnesh/Desktop/BOT - Kopya
ExecStart="/home/gnesh/Desktop/BOT - Kopya/scripts/run_dss_daily.sh"
TimeoutStartSec=8h
Documentation=file:///home/gnesh/Desktop/BOT%%20-%%20Kopya/reports/audit/phase6f_scheduler_install_20260724.md
```

The two rules that cost two failed attempts in Phase 7A: quote **`ExecStart` only** (not
`WorkingDirectory`, which takes the path literally and rejects quotes), and escape `%` as
`%%` in `Documentation` or systemd reads it as a specifier.

Timer state, confirmed untouched this turn:

```
NEXT                            LEFT LAST                              PASSED UNIT            ACTIVATES
Mon 2026-07-27 01:00:00 +03 4h 42min Sun 2026-07-26 13:16:05 +03 1h 13min ago dss-daily.timer dss-daily.service
```

**Discrepancy worth noting:** the turn brief says the first real run is Tue 2026-07-28
01:00 TR. The timer says **Mon 2026-07-27 01:00 +03**. The system is authoritative here;
flagging it rather than silently reconciling.

## 5. Test Count Reconciliation

| | Count |
|---|---|
| Pre-Phase-7A suite | 724 |
| Phase 7A Tiingo guard | +6 |
| Phase 7A total (measured then) | 730 |
| Phase 7C `test_slice_frame_end_bound_invariant` | +5 |
| Phase 7C `test_baseline_guard_visibility` | +7 |
| Phase 7C `GuardWiringTest` (added during code review) | +1 |
| **Phase 7C total (measured, dataset present)** | **743** |

`743` matches the final run exactly. Status `OK`, exit code 0. The earlier
742-test run in §3.2 predates the code-review fix; the figure quoted there is the
final one.

### Code review outcome

`/code-review` (ECC, local mode) over `a44e420..HEAD`: zero CRITICAL, zero HIGH,
one MEDIUM found and fixed.

**MEDIUM — the visibility wiring was itself unguarded.** Nothing failed if a guard
module stopped calling `announce_if_inactive`; the skip would silently revert to
invisible — the exact failure Item 2 removes — and all seven visibility tests
would still have passed. `GuardWiringTest` now asserts both guard modules
announce and gate their skip on the returned constant. Demonstrated non-vacuous:
removing the call in a throwaway clone produces
`FAILED (failures=1)` with the intended message. A follow-up commit swapped
`assertIn` for `assertTrue` because `assertIn` dumped the entire 8KB module into
the failure output and buried the explanation.

Accepted without change: the `SUITE SUMMARY` line prints only via
`run_test_suite.py` (the per-guard banner fires on both paths and is the primary
signal); `sys.path.insert(0, tests_dir)` matches the files' existing convention;
the module-global `_announced` registry is deliberate. Pre-existing and out of
scope: `testdata/runner.py` is 8,665 lines against an 800-line guideline.

Security: no credentials, no network, no external input, no SQL or path
construction from untrusted data. `py_compile` passes on all seven files; ruff is
not installed in this environment.

## 6. Hard-Rule Compliance

- Strategy/ranking/benchmark logic, frozen 177 universe/hash, validators and the DSS
  freshness check: unchanged. The only `runner.py` change is a docstring.
- `_slice_frame` untouched. Phase 7B option 2 remains rejected.
- Frozen `rs_v2_sip` / `rs_v2_clean` datasets and archived decision events: untouched.
  No pinned baseline value changed in either guard.
- systemd timer and scheduler state: untouched, next fire unchanged.
- No `--live` acquisition, no broker/order calls, no network calls to any data provider.
- No credential value read, echoed, printed, or logged.
- Side effect disclosed: the measurement harnesses may have written derived
  `.cache/prepared_features/*.pkl` entries. Regenerable cache, not source data.

## 7. Open Items for the Operator

1. **Phase 7B §4 is superseded.** It is committed unaltered as the historical record, with
   the correction noted in its commit message and here. If a stronger marker is wanted,
   an amendment note could be appended to that file — not done, since prior reports are
   immutable by convention.
2. **The Phase 7A `23:59:59` bound** in `scripts/build_tiingo_strategy_baseline.py` is a
   no-op on the production loader. Left as-is: the baseline is frozen and the explicit
   bound documents intent.
3. **Six clean-clone errors** (§3.5) predate this work. Converting those modules to skip
   cleanly would make a clean clone green, but is its own change.
4. **Timer fires Mon 2026-07-27 01:00 +03**, not Tue 07-28 as the brief stated.

---

*No GO/NO-GO — evidence and state only.*
