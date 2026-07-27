# Phase 8D — Sharpe/Volatility Metric Fix, and the Recorded Regime-Filter Decision

**Date:** 2026-07-27 (work completed early 2026-07-28 TR)
**Scope:** Reporting-metric fix only. No strategy/ranking/benchmark logic, frozen 177 universe/hash, validator or DSS freshness check changed. No `--live` run, no broker/order calls. The systemd timer and scheduler scheduling were not touched.

---

## 0. OPERATOR DECISION RECORDED — No Regime Filter

**Decision: no regime filter will be added. The system stays structurally ~100% long. The operator accepts full equity exposure knowingly.**

Rationale, recorded here so it is not relitigated:

- **(a)** Of three variants measured in Phase 8C, two cost return and only one improved risk-adjusted return.
- **(b)** That one variant's entire result rests on a **single liquidation event (2022-05-31)** — far too small a sample to conclude an edge.
- **(c)** Selecting the winner of a 4-way variant search on one bull-heavy window would be **overfitting**, contrary to the strategy's standing "no parameter optimization" principle.
- **(d)** The window contains **no catastrophic decline**, so the insurance value remains **unmeasured, not disproven**.

**This decision was made with measurement in hand, not by omission.** Phase 8A quantified the exposure profile (median 100% deployed, minimum 86.66%, zero days below 50%, 99.07% invested through the 419-day 2022 drawdown) and Phase 8C quantified what each candidate overlay would have done. The absence of a regime filter is now a deliberate, evidenced choice.

---

## 1. The Defect

`relative_strength_rotation.py` derived its return series as:

```python
daily_returns = equity.resample("1D").last().pct_change(fill_method=None).dropna()
```

`resample("1D")` expands the equity curve to **calendar** days. A weekend or holiday becomes `NaN`; `pct_change` then yields `NaN` for the **following** session; `dropna()` discards it. Every post-gap session — most Mondays, and every day after a holiday — never reached the calculation.

That is why the reported Sharpe of **0.53** could not be reconciled with the same function's CAGR of **18.46%** and volatility of **19.13%**.

## 2. The Fix

```diff
-    daily_returns = equity.resample("1D").last().pct_change(fill_method=None).dropna()
+    # Trading-day returns, not calendar-resampled ones. resample("1D") expands
+    # the curve to CALENDAR days, so a weekend or holiday becomes NaN and
+    # pct_change then yields NaN for the FOLLOWING session, which dropna()
+    # discarded -- silently removing 327 of 1502 observations (21.8%), mostly
+    # Mondays. That made the reported Sharpe arithmetically inconsistent with
+    # this function's own CAGR and volatility (Phase 8A). Reporting only: no
+    # decision path consumes Sharpe or volatility (Phase 8D verified that the
+    # compact score, gates and filters do not reference either).
+    daily_returns = equity.pct_change(fill_method=None).dropna()
     volatility = daily_returns.std()
```

The identical defect in `PortfolioManager.get_metrics` (`portfolio_manager.py:1482`) was fixed the same way.

## 3. Proof the Fix Touches Only Reporting

### 3.1 A complication that changed the method — and it matters

The instructed proof was a before/after comparison against the frozen baseline. **That comparison was not valid at the time this ran, and running it anyway would have produced a false result.**

Tonight's first real nightly run began at 01:00:01 TR and was still executing. Its paced Tiingo refresh was **actively rewriting the dataset**: 50 of 177 daily CSVs had been modified within three hours, progressing alphabetically. The governing guard, run against that half-written dataset, reported:

```
AssertionError: 768 != 770
```

Trade count cannot be affected by a change to `daily_returns`. The cause is a **torn dataset**, not the metric fix — corroborated by `test_selection_fingerprint_matches` still **passing**, i.e. the ranking was unchanged. Per the standing instruction to stop if anything else moves, work stopped here and the method changed.

### 3.2 The proof actually used — stronger, and immune to the moving dataset

`scratch/phase8d_metric_isolation_proof.py` runs **one** backtest and derives both return series from that **single** equity curve. Identical inputs, so any difference is attributable to the formula and nothing else:

```
=== SINGLE RUN, TWO RETURN CONVENTIONS (identical equity curve) ===
  old (resample '1D'): {'observations': 1175, 'annualized_vol_pct': 19.149,  'sharpe': 0.5325}
  new (trading day)  : {'observations': 1502, 'annualized_vol_pct': 18.9029, 'sharpe': 0.9951}
  observations dropped by the old convention: 327 (21.8%)
```

- **327 of 1502 observations (21.8%)** — exactly the figure Phase 8A derived independently.
- Sharpe **0.5325 → 0.9951**; volatility **19.149% → 18.903%**.
- Every other field is produced by the same single call and is therefore identical **by construction**, not by comparison: `Net Getiri (%)`, `CAGR (%)`, `Max Drawdown (%)`, `Islem Sayisi`, `Trade Count`, `Win Rate (%)`, `Profit Factor`, `Avg/Median/Best/Worst R`, `Expectancy R`, holding-bar statistics, turnover, position counts, `Max Portfolio Heat (%)` and the cash figures. Reading the function confirms none of them references `daily_returns`.

**Internal consistency, the point of the exercise:** CAGR 18.47% ÷ volatility 18.90% implies a Sharpe near **0.98**. The new figure of 0.9951 sits there; the old 0.5325 does not. (The small residual is the arithmetic-vs-geometric mean difference, as expected.)

### 3.3 What still needs doing on the frozen dataset

Once the refresh completes and the dataset validates, the frozen-window reproduction must be re-run to confirm `175.49% / 770 / max DD −18.97% / fingerprint ba697e96…` are unchanged. If they are not, that is a **data** finding requiring operator attention, entirely separate from this metric fix.

## 4. Consumer Audit — does anything decide on Sharpe or volatility?

**No.** Verified before editing:

| Consumer | Verdict |
|---|---|
| `reports._add_compact_scores` | Built from expectancy, drawdown, trade count and worst R. **Does not reference Sharpe or volatility.** |
| `relative_strength_rotation.py:465`, `portfolio_manager.py:1486` | `volatility > 0` — divide-by-zero guards, not gates. |
| `baseline_parity.py` | Reporting/parity comparison only. |
| `build_tiingo_strategy_baseline.py` | Stores the value in the baseline artifact. |
| `tests/test_rs_v2_tiingo_strategy_baseline.py` | Pins the value (see §6). |

No `min_sharpe`, no threshold, no filter, no sizing input. Nothing gates on either figure.

## 5. Every Other `resample` Site

| Site | Pattern | Verdict |
|---|---|---|
| `relative_strength_rotation.py:461` | `.last().pct_change().dropna()` | **DEFECT — fixed** |
| `portfolio_manager.py:1482` | `.last().pct_change().dropna()` | **DEFECT — fixed** |
| `baseline_parity.py:107` | `.last().dropna()`, differenced later | **Correct** — gaps removed *before* differencing. Left alone. |
| `indicators.py:118` | `.last().dropna()` on prices for an SMA regime | **Correct** — no return series at all. Left alone (and it is strategy logic). |
| `relative_strength_rotation.py:139` | `.agg()` then `dropna(subset=OHLC)` | **Correct** — aggregates OHLCV bars, not returns; also returns early for daily frames. Left alone (strategy logic). |

The distinction that matters: `resample().last().dropna()` **then** `pct_change()` is sound; `resample().last().pct_change().dropna()` is the defect.

## 6. Baseline Re-freeze and Guard Pin — DEFERRED, with reasons

**Not done this turn, deliberately.**

`testdata/baselines/rs_v2_tiingo_strategy_baseline.json` stores `metrics.sharpe_ratio: 0.53` and **no volatility field**, so `sharpe_ratio` is the only field the fix affects. But its correct value depends on the equity curve, and the equity curve could not be reproduced against the frozen window while the refresh was mid-write. Freezing a governing baseline from an unverifiable dataset is precisely the failure class Phases 7A and 8A were convened to correct, so it was not done.

**Consequence, stated plainly: `test_frozen_headline_metrics` in the governing guard FAILS until this is completed.** The failure is a stale pin, not a defect. It is loud and documented rather than silent.

**Procedure once the refresh has completed and validated:**

1. Re-run the guard. Confirm `Net Getiri`, `Islem Sayisi`, `CAGR`, `Max Drawdown` and the fingerprint still match `175.49 / 770 / 18.46 / −18.97 / ba697e96…`. If any of those moved, stop — that is a data change, not a metric change.
2. Re-run `python3 scripts/build_tiingo_strategy_baseline.py`, then revert every field except `metrics.sharpe_ratio` so only that one moves.
3. Update the pinned Sharpe in `tests/test_rs_v2_tiingo_strategy_baseline.py` with a comment recording that the change is the Phase 8D convention fix, not a strategy change.

Expected new value ≈ **1.0** (Phase 8A measured 0.9952 on the intact dataset; the artifact stores `round(sharpe, 2)`).

**Suite result with the pin stale:**

```
Ran 762 tests in 635.949s
FAILED (failures=1)
FAIL: test_frozen_headline_metrics (TiingoStrategyBaselineGuardTest)
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
```

762 reconciles exactly against the Phase 8C total — this phase added no tests. The single failure is `test_frozen_headline_metrics`, which trips on **two** documented causes: the stale Sharpe pin (§6) and the torn dataset's trade count (§3.1). Both resolve when the procedure above is run against a settled dataset.

### The SIP historical guard

**It pins no Sharpe at all** — `grep -c -i sharpe tests/test_daily_only_equivalence_guard.py` returns `0`. Its four pinned values (`165.9133`, `762` trades, `50.50%` win rate, `−20.47%` max drawdown) are all independent of the return series, so the fix cannot affect it and **no change was made**.

## 7. No Risk-Free Rate — documented, not changed

The calculation is:

```python
sharpe = daily_returns.mean() / volatility * np.sqrt(252)
```

Raw mean return ÷ standard deviation. **No risk-free rate is subtracted anywhere.** This is not a textbook Sharpe ratio, and over this window it matters — T-bills yielded roughly 0–5% depending on the year, so a true excess-return Sharpe would be materially lower than the figure reported. Left as-is per instruction; recorded so the number is never mistaken for an excess-return Sharpe.

## 8. Disclosure — the metric fix and tonight's live run

The nightly chain was mid-flight when these edits landed. The refresh step had already started; the signals and report steps had not. They will therefore import the **fixed** module.

The practical effect: tonight's artifacts will carry the corrected Sharpe and volatility. **No BUY/SELL decision is affected** — §4 establishes that nothing gates on either figure, and the ranking, filters and execution paths are untouched. This is disclosed rather than assumed harmless.

## 9. Hard-Rule Compliance

- Strategy/ranking/benchmark **logic**, frozen 177 universe/hash, validators and the DSS freshness check: unchanged. The only edits are two return-series lines and their comments.
- systemd timer, scheduler scheduling and scheduler state: untouched. Phase 8B code was committed as already written; its behaviour was not changed.
- No `--live` run initiated by this session, no broker/order calls, no network calls to any provider.
- No credential value read, echoed, printed, or logged.
- Side effect disclosed: the isolation proof may have written derived `.cache/prepared_features/*.pkl` entries. Regenerable cache, not source data.

---

*No GO/NO-GO — evidence and state only.*
