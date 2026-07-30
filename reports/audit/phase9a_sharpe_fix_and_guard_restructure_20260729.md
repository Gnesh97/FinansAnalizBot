# Phase 9A — Sharpe/Volatility Metric Fix, and the Governing Guard Restructure

**Date:** 2026-07-29
**Scope:** No strategy/ranking/benchmark logic, frozen 177 universe/hash, validator, or DSS freshness check changed. No `--live` fetch. No broker/order calls. The systemd timer, unit, and scheduler state were not touched — tonight's 01:00 run is undisturbed. No credential read, echoed, or logged.

---

## 0. What was already done before this phase started

The instruction described the Sharpe fix as still open. **It was not.** Commit `0b09217` (Phase 8D, 2026-07-28 02:09) already applied it to both metric functions. This phase verified that state rather than re-applying it, and completed the parts Phase 8D explicitly deferred.

Stated plainly so the record is accurate: **Task 1 steps 1 and 5 were pre-existing work; steps 2, 3 and 4 are re-verified here on the settled dataset, and step 4 found two occurrences Phase 8D's table missed.**

---

## TASK 1 — The Sharpe/volatility metric defect

### 1.1 The diff (already committed, shown as instructed)

`testdata/strategies/relative_strength_rotation.py`, and the identical defect in `PortfolioManager.get_metrics` (`testdata/portfolio_manager.py:1482`):

```diff
-    daily_returns = equity.resample("1D").last().pct_change(fill_method=None).dropna()
+    daily_returns = equity.pct_change(fill_method=None).dropna()
     volatility = daily_returns.std()
```

`resample("1D")` expands the equity curve to **calendar** days. A weekend or holiday becomes `NaN`; `pct_change` then yields `NaN` for the **following** session; `dropna()` discards it. Every post-gap session — most Mondays, and every day after a holiday — never reached the calculation.

### 1.2 Proof the fix is reporting-only — re-run on the SETTLED dataset

Phase 8D's isolation proof ran while the nightly refresh was rewriting the dataset, which it disclosed. Re-run now against the settled `COMPLETE_VALIDATED` data (`scratch/phase8d_metric_isolation_proof.py`), it reproduces exactly:

```
  old (resample '1D'): {'observations': 1175, 'annualized_vol_pct': 19.149,  'sharpe': 0.5325}
  new (trading day)  : {'observations': 1502, 'annualized_vol_pct': 18.9029, 'sharpe': 0.9951}
  observations dropped by the old convention: 327 (21.8%)
```

The method is what makes this conclusive: **one** backtest, **one** equity curve, both return series derived from it. Every other field is produced by that same single call and is therefore identical *by construction*, not by comparison — `Net Getiri (%)`, `CAGR (%)`, `Max Drawdown (%)`, `Islem Sayisi`, `Trade Count`, `Win Rate (%)`, `Profit Factor`, R statistics, holding-bar statistics, turnover, position counts and cash figures. Reading the function confirms none of them references `daily_returns`.

**Internal consistency, the point of the exercise:** CAGR 18.47% ÷ vol 18.90% implies a Sharpe near 0.98. The new 0.9951 sits there; the old 0.5325 does not.

### 1.3 A distinction the instruction's own grep forced — and a correction

The instruction said to stop if a decision path consumes volatility. Grepping produced an apparent hit, and it matters that it is reported precisely rather than waved away:

`testdata/quantconnect_port_contract.py:45` documents `score_formula: momentum / annualized_volatility`. **The ranking really does divide by a volatility.**

It is a *different* volatility:

| | Decision-path volatility | Reporting volatility (the fixed line) |
|---|---|---|
| Where | `_relative_strength_daily_features` | `_relative_strength_metrics_from_equity` |
| Input | **per-symbol daily close prices** | **portfolio equity curve** |
| Formula | `Close.pct_change().rolling(60).std() * sqrt(252)` | `equity.pct_change().std()` |
| Consumed by | `score = total_return / annualized_vol` → **ranking** | Sharpe only |
| Touched by this fix | **No** | Yes |

They share a name and nothing else. The fix cannot reach the score path, and the **selection fingerprint matching exactly** is the empirical confirmation.

**Correction made:** the code comment left by Phase 8D asserted "no decision path consumes Sharpe or volatility". That claim is over-broad and a future reader could reasonably be misled by it. The comment now states the distinction explicitly. **Comment text only — no executable change.**

### 1.4 Consumer audit — nothing gates, filters, or sizes on either figure

| Consumer | Verdict |
|---|---|
| `reports._add_compact_scores` | Built from expectancy, drawdown, trade count, worst R. Does not reference Sharpe or equity-curve volatility. |
| `relative_strength_rotation.py:473`, `portfolio_manager.py:1490` | `volatility > 0` — divide-by-zero guards, not gates |
| `baseline_parity.py:140-164` | Reporting/parity comparison only |
| `build_tiingo_strategy_baseline.py:124` | Stores the value in the artifact |
| `diagnostics.py:134` | Zero-valued placeholder in an empty-metrics dict |

No `min_sharpe`, no threshold, no filter, no sizing input. **Confirmed: no STOP condition was triggered.**

### 1.5 Every `resample` site — including two Phase 8D missed

| Site | Pattern | Verdict |
|---|---|---|
| `relative_strength_rotation.py:461` | `.last().pct_change().dropna()` | **DEFECT — fixed** (Phase 8D) |
| `portfolio_manager.py:1482` | `.last().pct_change().dropna()` | **DEFECT — fixed** (Phase 8D) |
| `universe_scanner_legacy.py:2297` | `.last().pct_change().dropna()` | **DEFECT — present, NOT fixed. Flagged.** |
| `universe_scanner_legacy.py:4277` | `.last().pct_change().dropna()` | **DEFECT — present, NOT fixed. Flagged.** |
| `baseline_parity.py:107` | `.last().dropna()`, differenced later | Correct — gaps removed *before* differencing |
| `indicators.py:118` | `.last().dropna()` on prices for an SMA regime | Correct — no return series |
| `relative_strength_rotation.py:139` | `.agg()` then `dropna(subset=OHLC)` | Correct — aggregates OHLCV, not returns |
| `universe_scanner_legacy.py:1513, 1792` | as the two above | Correct |

**The two new findings, and why they were flagged rather than fixed.** `universe_scanner_legacy.py` is quarantined dead code. `tests/test_legacy_isolation.py` enforces, by AST walk over every non-legacy file in `testdata/`, that no production module imports it and that no dynamic-execution token (`exec(`, `eval(`, `SourceFileLoader`, `runpy.run_path`) can reach it. Nothing executes these lines.

Fixing them would mean editing a quarantined module to no operational effect, and the surrounding code is strategy logic — which the hard rules forbid touching. **Flagged, not fixed, deliberately.** They are recorded here so the audit trail is complete rather than convenient.

### 1.6 No risk-free rate — documented, not changed

```python
sharpe = daily_returns.mean() / volatility * np.sqrt(252)
```

Raw mean return ÷ standard deviation, annualized by √252. **No risk-free rate is subtracted anywhere.** This is **not a textbook Sharpe ratio**. Over this window it matters: T-bills yielded roughly 0–5% depending on the year, so a true excess-return Sharpe would be materially lower than the 1.0 now reported. Recorded, not changed, per instruction — so the figure is never mistaken for an excess-return Sharpe.

---

## TASK 2 — The governing guard restructure

### 2.1 The measurement the restructure rests on

Frozen window reproduced on the settled dataset (`scratch/phase9a_frozen_window_measure.py`), against the pre-existing baseline:

| Metric | Frozen baseline | Observed | Delta | Relative |
|---|---|---|---|---|
| net_return_pct | 175.49 | 175.70 | +0.21 | 0.12% |
| trade_count | 770 | **768** | −2 | 0.26% |
| cagr_pct | 18.46 | 18.47 | +0.01 | 0.05% |
| max_drawdown_pct | −18.97 | **−19.36** | −0.39 | **2.06%** |
| win_rate_pct | 50.1 | 50.0 | −0.10 | 0.20% |
| sharpe_ratio | 0.53 | **1.00** | +0.47 | 88.68% |
| **Selection fingerprint** | `ba697e96…` | **`ba697e96…`** | — | **EXACT MATCH** |

Two independent causes, and separating them is the whole point:

- **Five metrics moved because the vendor re-adjusted prices.** The manifest's own evidence: `drift_detected_symbols = ['CVS','PFE','PG','T']`, `reacquisition_item_count = 4`. Execution-level fills shifted because slightly different prices change cash-constrained share counts.
- **Sharpe moved because the formula was corrected** (§1). That is not drift.

**`win_rate_pct` 50.1 → 50.0 is a sixth movement not previously reported** — Phase 8E's table listed four. Recorded here.

### 2.2 What was implemented

**The fingerprint stays exact and hard.** `test_selection_fingerprint_matches` is unchanged. So are the frozen-177 universe assertion, the target-end-session assertion, and both perturbation tests.

**Headline metrics became informational.** `tests/baseline_metric_drift.py` (new) compares observed against baseline and reports. It never raises. The old `test_frozen_headline_metrics` is now `test_headline_metrics_are_recorded_and_reported`.

**What is still asserted hard there:** every pinned metric must still be *produced*, be numeric, and not be `NaN`. A metric that vanishes or turns into `None` is a structural defect in the metrics path, not vendor drift, and still fails.

**Loud when it matters.** Beyond threshold, the module emits `[BASELINE-METRIC-DRIFT] guard=<name> count=<n>` inside a banner. `testdata/run_test_suite.py` parses it — reusing the marker mechanism already built for inactive guards — and prints, *after* the status line:

```
SUITE SUMMARY: N baseline guard(s) reported HEADLINE METRIC DRIFT beyond threshold: <names>
               The suite did not fail on this -- the selection fingerprint is
               asserted exactly and separately. Investigate before re-freezing.
```

It also writes a `## Baseline Metric Drift` section into `testdata/reports/latest/test_run_report.md`. A green run is exactly the case where drift would otherwise go unread, which is why it prints last.

### 2.3 The threshold, and an honest account of its limits

**10% relative, per metric.**

Calibrated against the only re-adjustment event measured end to end (2026-07-27, four symbols). The largest legitimate relative move observed was **max drawdown at 2.06%**; every other metric moved ≤0.26%. 10% sits roughly **5× above the largest observed legitimate move**.

Why relative rather than absolute: the metrics differ in scale by three orders of magnitude (0.53 → 175.49 → 770). One absolute number cannot serve all six without being meaningless for most.

**The honest limitation, stated rather than buried: this is calibrated on ONE event (n = 1).** It is a defensible order-of-magnitude choice, **not** a statistically derived bound, and inventing a tighter figure like "2.5%" would be exactly the false precision the instruction warned against. Every re-freeze artifact records observed deltas, so the evidence base accumulates and the threshold can be revisited on data rather than on one sample.

### 2.4 Why beyond-threshold warns rather than fails

Deliberate, and worth defending. There is no size at which a vendor re-adjustment stops being legitimate — four symbols re-adjusted or forty are both legitimate. Size alone cannot separate "vendor corrected prices" from "strategy regressed"; the **fingerprint** does that, which is why it stayed hard. Making a large-but-legitimate move fail the suite would rebuild the exact behaviour this phase removes.

### 2.5 The re-freeze procedure

`scripts/refreeze_tiingo_strategy_baseline.py` (new). Guarantees:

1. **The prior artifact is never overwritten.** It is copied to `testdata/baselines/history/` keyed by its own `generated_at`, and refuses to clobber an existing history file. Verified not gitignored — provenance that is not committed is not provenance.
2. **Every re-freeze records why:** reason (required; `--reason` is mandatory unless `--dry-run`), the re-acquired symbols and the manifest they came from, prior metric values, prior fingerprint, and per-metric deltas.
3. **A fingerprint change is REFUSED** unless `--allow-fingerprint-change` is passed explicitly. A changed fingerprint means ranking moved — a regression, not a price re-adjustment — and must never be adopted as a side effect of a routine re-freeze.
4. **Refuses any dataset not `COMPLETE_VALIDATED`.** Freezing a governing baseline from unverified data is the failure Phases 7A/8A were convened to correct.
5. `--dry-run` writes nothing.
6. `refreeze_history` accumulates across re-freezes, so the current artifact alone reconstructs every change.

### 2.6 The re-freeze performed

Dry-run first, then executed. `FINGERPRINT UNCHANGED` in both.

```
archived prior -> testdata/baselines/history/rs_v2_tiingo_strategy_baseline_20260726T102328.748473Z.json
wrote          -> testdata/baselines/rs_v2_tiingo_strategy_baseline.json
```

New pinned metrics: `net_return_pct 175.7`, `trade_count 768`, `cagr_pct 18.47`, `max_drawdown_pct -19.36`, `sharpe_ratio 1.0`, `win_rate_pct 50.0`. Fingerprint `ba697e96…` unchanged. Provenance block records all four re-acquired symbols, `acq_20260727_220002`, `manifest_status COMPLETE_VALIDATED`, `validation_completed_at 2026-07-28T16:31:09Z`, the prior values, and all six deltas.

**One interpretation flagged for operator correction.** The instruction said to re-freeze "to the current validated dataset (target_end_date 2026-07-27)". This was read as *re-freeze against the dataset whose manifest target_end_date is 2026-07-27* — **not** as *move the baseline window end from 2026-07-21 to 2026-07-27*. The frozen window remains `2020-07-27 → 2026-07-21`. Moving the window would change what the baseline measures and would invalidate the fingerprint comparison that this entire restructure depends on, so it was not done as a side effect. **If the window move was intended, say so and it can be done deliberately.**

### 2.7 The SIP historical guard — argued, not assumed

**It pins no Sharpe.** `grep -c -i sharpe tests/test_daily_only_equivalence_guard.py` returns **0**.

Its four pins (`tests/test_daily_only_equivalence_guard.py:88-91`) are `total_return 165.9133`, `trade_count 762`, `win_rate 50.50`, `max_dd -20.47` — none derived from the return series, so the Task 1 fix cannot affect it. **No change was made.**

**The case for its pins legitimately staying exact**, as invited:

The argument for relaxing the Tiingo guard was that a live vendor keeps re-adjusting its prices, so exact pins fail routinely for legitimate reasons. **That premise does not hold for rs_v2_sip**, and the difference is structural rather than a judgement call:

- `dss_scheduler.py:39` sets `DATASET_PROFILE = "rs_v2_tiingo"`. The nightly chain does not touch the SIP dataset.
- `tiingo_provider.py` serves the `rs_v2_tiingo` profile only. No refresh path writes to SIP.
- Its manifest has not been modified since 2026-07-20.
- Phase 6A established rs_v2_sip carries *stale* spinoff adjustments (FDX/CMCSA/MMM/MDT) — it is a retired, archival dataset precisely because nobody is re-adjusting it.

A guard over a genuinely static dataset should fail if its numbers move, because the only remaining explanations are code regression or data corruption — both of which are exactly what a guard is for. **Relaxing it would remove signal and add nothing.** Its exact pins stay exact.

---

## 3. Proof the restructured guard still catches real regressions

### 3.1 A selection-altering change still FAILS — hard

Both pre-existing perturbation tests are unchanged and passing:

| Test | Perturbation | Result |
|---|---|---|
| `test_fingerprint_fails_on_price_perturbation` | `AAPL` Close × 1.5 | fingerprint differs → **FAILS as required** |
| `test_fingerprint_fails_on_universe_perturbation` | drop `MPC` | fingerprint differs → **FAILS as required** |

A price change that alters selections breaks the fingerprint, and the fingerprint is a hard assertion. The guard is not vacuous.

Additionally, `refreeze_tiingo_strategy_baseline.py` refuses to adopt a changed fingerprint, so a regression cannot be laundered into the baseline by running the re-freeze.

### 3.2 A legitimate re-adjustment does NOT fail, but IS reported

Proven at two levels.

**Empirically, on the real event.** The 2026-07-27 re-adjustment moved five metrics. Before this phase the suite was RED on `768 != 770`. After it, the suite is GREEN and the movement was reported and recorded in the baseline's provenance block.

**By construction, in `tests/test_baseline_metric_drift.py` (15 tests):**

| Test | Pins |
|---|---|
| `test_reporting_never_raises` | A 100× move still returns records rather than raising |
| `test_small_move_is_recorded_but_stays_quiet` | A routine re-adjustment emits no marker but is printed |
| `test_large_move_emits_the_marker` | Beyond threshold emits `[BASELINE-METRIC-DRIFT]` |
| `test_runner_extracts_the_guard_name_from_real_output` | The suite runner parses the real banner |
| `test_negative_baseline_uses_magnitude` | Deeper max drawdown reads as positive drift |
| `test_threshold_boundary_is_exclusive` | Exactly at 10% does not trip |
| `test_zero_baseline_yields_no_relative_delta` | No divide-by-zero |
| `test_missing_observed_metric_is_not_silently_scored` | A vanished metric is not scored as zero drift |
| `test_inactive_marker_parsing_is_unchanged` | The extracted helper did not alter existing behaviour |

Plus `test_metric_movement_alone_cannot_fail_this_guard` at guard level, on the real dataset.

### 3.3 A leak this work introduced, and closed

The guard-level probe deliberately trips the marker. As first written it printed to real stdout, so `SUITE SUMMARY` would have reported drift on **every** run — training the operator to ignore the warning, which is the precise failure mode this phase exists to remove, reintroduced by its own test. It now captures stdout and asserts on the captured buffer.

This is the same class of defect as the Phase 8E test-payload leak: a verification path inheriting a production-visible default. Recorded rather than quietly fixed.

---

## 4. Verification

### 4.1 Full suite — GREEN

```
Ran 801 tests in 463.415s
OK

[baseline-metrics] rs_v2_tiingo_strategy_baseline: all 6 headline metrics match the frozen baseline exactly.
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```

**801 confirmed GREEN, zero failures.** The count reconciles exactly against the Phase 8E total of 775:

| Source | Tests |
|---|---|
| Phase 8E baseline | 775 |
| `tests/test_baseline_metric_drift.py` (new) | +15 |
| `tests/test_refreeze_baseline_procedure.py` (new) | +10 |
| `test_metric_movement_alone_cannot_fail_this_guard` (new) | +1 |
| **Total** | **801** |

`test_frozen_headline_metrics` did not disappear — it was renamed to `test_headline_metrics_are_recorded_and_reported`, so the count is unaffected by the restructure itself.

Both SUITE SUMMARY lines are clean, which is the meaningful result: the guard ran (not skipped), and it reported no drift because the re-freeze adopted the corrected values. The `[baseline-metrics] … match … exactly` line confirms the informational path executed rather than being silently inert.

### 4.2 Code review (ECC, local mode)

**Decision: APPROVE.** No CRITICAL or HIGH findings. Three MEDIUM findings were raised and **fixed during review**, not deferred:

| Severity | Finding | Resolution |
|---|---|---|
| — | Secrets scan (`api_key`/`token`/`password`/`secret`/`bearer`) across all new files | **Clean** — no matches |
| — | Debug statements (`breakpoint`, `pdb`, `TODO`, `FIXME`) | **Clean** — none |
| MEDIUM | `refreeze…main()` was **112 lines**, over the 50-line limit | Extracted `_print_delta_table`, `_build_provenance`, `_parse_args`, `_write_refreeze`. **No function now exceeds 50 lines.** |
| MEDIUM | `archived.relative_to(PROJECT_ROOT)` raises for any path outside the repo — and it raises *after* `_archive_prior` has already copied the file, leaving an orphaned archive and **no new baseline written** | Added `_display_path()`, which falls back to the absolute path. Provenance *formatting* must never be what fails a write. Found by the new tests, not by inspection. |
| MEDIUM | `scripts/refreeze_tiingo_strategy_baseline.py` — the script that writes the **governing** baseline — had **no test coverage** | Added `tests/test_refreeze_baseline_procedure.py` (10 tests) covering every refusal path |

Structural checks: all files ≤ 223 lines (limit 800); no nesting deeper than 4; all touched files compile.

**Verified the restructure did not hollow out the guard.** The only remaining `assertEqual`/`assertAlmostEqual` calls in the guard are the frozen-177 universe (×2) and the selection fingerprint (×2). No headline metric *value* assertion survives — which is the intent — while the fingerprint assertion is untouched and exact.

### 4.3 Repository state

**Nothing committed.** All Phase 9A work is left in the working tree for operator review, per "report and stop".

```
$ git status --short
 M scratch/tiingo_fetch_adapter.py                          <- pre-existing, not this phase
 M testdata/baselines/rs_v2_tiingo_strategy_baseline.json   <- re-frozen, with provenance
 M testdata/run_test_suite.py                               <- drift marker -> SUITE SUMMARY
 M testdata/strategies/relative_strength_rotation.py        <- comment text only
 M tests/test_rs_v2_tiingo_strategy_baseline.py             <- guard restructure
?? reports/audit/phase9a_sharpe_fix_and_guard_restructure_20260729.md
?? scratch/phase5d_analysis_output.json                     <- pre-existing
?? scratch/phase9a_frozen_window_measure.json
?? scratch/phase9a_frozen_window_measure.py
?? scratch/tiingo_full_universe_analysis.py                 <- pre-existing
?? scripts/refreeze_tiingo_strategy_baseline.py
?? testdata/baselines/history/                              <- versioned prior baseline
?? tests/baseline_metric_drift.py
?? tests/test_baseline_metric_drift.py
?? tests/test_refreeze_baseline_procedure.py

$ git log --oneline -6
b18c217 docs(audit): file Phase 8E and re-validate the 2026-07-27 dataset on disk
519c763 fix(notices): stop test payloads reaching the operator's Telegram
c8d9f69 fix(scheduler): inhibit sleep for the duration of the nightly chain
5ed6401 fix(telemetry): let the retry contract express skips and drift re-acquisitions
bfb9d48 docs(audit): file Phase 8D metric fix and record the no-regime-filter decision
0b09217 fix(metrics): compute Sharpe and volatility from trading-day returns
```

Three entries (`scratch/tiingo_fetch_adapter.py`, `scratch/phase5d_analysis_output.json`, `scratch/tiingo_full_universe_analysis.py`) pre-date this phase and were untouched by it.

`testdata/baselines/history/` is confirmed **not gitignored** (`git check-ignore` returns non-zero), so the versioned prior artifact will be tracked. Provenance that is not committed is not provenance.

---

### 4.4 The dataset was settled throughout — checked, not assumed

Phase 8D drew a wrong conclusion because it measured against a dataset the nightly refresh was actively rewriting. Since this phase **re-froze the governing baseline**, that trap had to be excluded explicitly rather than hoped past.

The timer did activate `dss-daily.service` during this session, so the question was real:

```
ExecMainStartTimestamp = Wed 2026-07-29 19:38:45
ExecMainExitTimestamp  = Wed 2026-07-29 19:38:46     <- one second
Result                 = success
```

A one-second run is the session gate declining, not a refresh. Corroborated on the data itself:

```
daily CSVs modified today : 0
newest daily CSV mtime    : 2026-07-28 17:29:27
manifest pointer          : acq_20260727_220002   (unchanged throughout)
```

**No file under `daily/` was touched on 2026-07-29.** Every measurement and the re-freeze ran against a fully settled `COMPLETE_VALIDATED` dataset.

---

## 5. Hard-Rule Compliance

- **Strategy/ranking/benchmark logic:** unchanged. The only edit to a strategy module is comment text (§1.3). The selection fingerprint matching exactly is the empirical proof.
- **Frozen 177 universe/hash:** unchanged — `026f66d1…`, re-verified in the new baseline artifact.
- **Validators and the DSS freshness check:** untouched.
- **systemd timer, unit, scheduler state:** not touched. No file under `~/.config/systemd/` was read or written; tonight's 01:00 run is undisturbed.
- **No `--live` fetch.** All work ran against data already on disk.
- **No broker/order calls.**
- **Credentials:** none read, echoed, printed, or logged.
- **Side effect disclosed:** the measurement runs may have written derived `.cache/prepared_features/*.pkl` entries. Regenerable cache, not source data.

---

*No GO/NO-GO — evidence and state only.*
