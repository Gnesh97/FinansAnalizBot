# Phase 8A — Risk Measurements for the Risk Engine Decision

**Date:** 2026-07-27
**Scope:** Read-only measurement. No strategy/ranking/benchmark logic changed, no default changed, no behavioural commit, no `--live` run, scheduler and timer untouched. Measurement 3 is a sandbox experiment in `scratch/`, wired into nothing.

**Dataset:** validated `rs_v2_tiingo`, frozen baseline window `2020-07-27 → 2026-07-21`, frozen 177-symbol universe.

---

## 0. Baseline Reproduction (sanity check, done first)

Loaded through the production path — `_load_or_build_prepared_cache` → `_run_relative_strength_rotation_backtest` — with the same window bounds the frozen guard uses.

| Quantity | Measured | Frozen baseline | Match |
|---|---|---|---|
| Net return | 175.49% | 175.49% | yes |
| Trade count | 770 | 770 | yes |
| CAGR | 18.46% | 18.46% | yes |
| Max drawdown | −18.97% | −18.97% | yes |
| Sharpe | 0.53 | 0.53 | yes |
| Win rate | 50.1% | 50.1% | yes |
| Selection fingerprint | `ba697e9653db37bd…` | `ba697e9653db37bd…` | yes |
| Symbols | 177 | 177 | yes |

Everything below is measured on this reproduced run.

---

## 1. Measurement 1 — Actual Portfolio Risk

### 1.1 A defect found while measuring: the reported Sharpe is understated

The system reports **Sharpe 0.53**. That figure is not consistent with the system's own other outputs: a CAGR of 18.46% against a volatility of 19.13% cannot produce 0.53.

Cause, at `relative_strength_rotation.py:461`:

```python
daily_returns = equity.resample("1D").last().pct_change(fill_method=None).dropna()
```

`resample("1D")` expands the equity curve to **calendar** days. A weekend or holiday becomes `NaN`, `pct_change` then yields `NaN` for the *following* session, and `dropna()` discards it. Every post-gap session — most Mondays, and every day after a holiday — is silently excluded from both the volatility and the Sharpe.

**327 of 1502 return observations (21.8%) are dropped.**

| Convention | Observations | Annualized vol | Sharpe |
|---|---|---|---|
| System's own (`resample("1D")`) | 1175 | **19.13%** | **0.5342** (reported as 0.53) |
| Trading-day (`equity.pct_change()`) | 1502 | **18.89%** | **0.9952** |

The trading-day figures are internally consistent: annualized arithmetic mean return 18.79% ÷ vol 18.89% = 0.995, and the arithmetic mean sits just above the 18.46% CAGR as it must. The system's own figures are not.

Both are reported throughout. Neither has been "corrected" in code — this is a measurement report.

### 1.2 Realized volatility

- **18.89% annualized** (trading-day, 1502 observations)
- 19.13% annualized by the system's own convention
- Daily volatility 1.2049% (system convention)

### 1.3 Sharpe and its risk-free assumption

**The system uses no risk-free rate at all.** `relative_strength_rotation.py:463-467`:

```python
sharpe = daily_returns.mean() / volatility * np.sqrt(252)
```

This is raw mean return ÷ standard deviation, annualized by √252 — not an excess-return Sharpe. Over this window that matters: T-bills yielded roughly 0–5% depending on the year, so an excess-return Sharpe would be materially lower than either figure above.

### 1.4 Drawdown distribution

88 distinct peak-to-trough episodes. Maximum **−18.97%**. The five largest:

| # | Depth | Peak | Trough | Recovered by | Peak→trough | Total duration | Recovered |
|---|---|---|---|---|---|---|---|
| 1 | **−18.97%** | 2025-02-19 | 2025-04-08 | 2025-08-06 | 47 d | 167 d | yes |
| 2 | −18.12% | 2022-04-21 | 2022-10-14 | 2023-06-14 | 176 d | 419 d | yes |
| 3 | −16.41% | 2021-11-22 | 2022-01-27 | 2022-04-20 | 66 d | 148 d | yes |
| 4 | −15.40% | 2026-06-23 | 2026-07-20 | — | 27 d | 28 d | **no — still open** |
| 5 | −13.32% | 2023-08-01 | 2023-10-27 | 2023-12-26 | 87 d | 147 d | yes |

Note #4: the window ends inside an unrecovered 15.4% drawdown. The longest was #2 — **419 days** underwater through the 2022 bear market.

### 1.5 Half-Kelly context (Carver)

Carver's rule of thumb: the full-Kelly annual volatility target, expressed as a percentage, is approximately the realistic Sharpe ratio; Half-Kelly halves it. He is explicit that a backtest Sharpe must be haircut before use.

| Sharpe input | Full-Kelly vol | Half-Kelly vol |
|---|---|---|
| System-reported 0.53 | 53% | **26.5%** |
| System-reported, halved as haircut (0.265) | 26.5% | **13.3%** |
| Trading-day 0.995 | 99.5% | **49.8%** |
| Trading-day, halved as haircut (0.50) | 50% | **24.9%** |

**Measured actual volatility: 18.89%.**

Stated plainly: the system currently runs at ~19% annualized volatility. Half-Kelly on the haircut *trading-day* Sharpe suggests ~25% — the system is running **below** that. Half-Kelly on the haircut *reported* Sharpe suggests ~13% — the system is running **above** that. The two answers disagree in opposite directions, and which one is right depends entirely on the Sharpe defect in §1.1. No change is recommended here; the comparison is the deliverable.

---

## 2. Measurement 2 — Behaviour in Downturns

### 2.1 What the SMA100 filter actually does

Traced through `relative_strength_rotation.py`:

- `RS_Eligible` (line 314-319) requires `Close > SMA100`, plus a minimum price and an ATR ceiling.
- `_relative_strength_selection` (line 403) takes the **top 30 of whatever passes**: `selections[date] = {symbol for symbol, _ in rows[:rs_top_n]}`.
- `rs_use_market_regime` is **False** for this strategy, so `_relative_strength_regime` returns all-`True` (line 341-342) and there is no index-level de-risking switch.
- Sizing (line 634-635) is `target_weight = 1 / rs_top_n`, i.e. a fixed 1/30 of equity per new position.

So cash arises **only** as a residual: if fewer than 30 names pass the SMA100 filter, the shortfall is simply not invested. There is no rule anywhere that says "reduce exposure because the market is falling."

### 2.2 Measured deployment

Position counts were taken from the production `position_count_curve`; exposure was rebuilt from the production trade log and cross-checked against it. **The reconstruction matched production on every date except one — 2026-07-21**, which is the `forced_final_exit` close-out date where production still counts the open book at the close. Expected, and excluded from the statistics below.

Two periods are excluded as artifacts, not behaviour:
- **Warm-up, 2020-07-31 → 2020-12-31** — six rebalances with **0 eligible symbols out of 177**, because SMA100 and the 126-day lookback have no history at the window start. Not a bear-market response.
- **2026-07-21** — the forced final liquidation.

Live period **2021-02-01 → 2026-07-20**, 1372 trading days:

| Statistic | Value |
|---|---|
| Deployed capital, median | **100.00%** |
| Deployed capital, mean | **99.78%** |
| Deployed capital, 5th percentile | 99.64% |
| Deployed capital, minimum | **86.66%** |
| Days below 95% deployed | **21 of 1372** |
| Days below 50% deployed | **0** |
| Days fully in cash | **0** |
| Positions held, median | 29 |
| Positions held, minimum | 25 |

Of 67 live rebalances, exactly **one** produced fewer than 30 selections: **2022-09-30, where only 25 of 177 symbols passed the SMA100 filter** — the trough region of the 2022 bear market. That is the filter's maximum observed defensive effect: a 5-name shortfall, and even then measured deployment on that date was 100% because existing positions had drifted up in weight.

### 2.3 During the five largest drawdowns

| Episode | Depth | Min positions | Mean positions | Min deployed | Mean deployed |
|---|---|---|---|---|---|
| 2025-02-19 → 2025-04-08 | −18.97% | 27 | 28.47 | 100.00% | 100.00% |
| 2022-04-21 → 2022-10-14 | −18.12% | 25 | 29.21 | **86.66%** | 99.07% |
| 2021-11-22 → 2022-01-27 | −16.41% | 29 | 29.62 | 100.00% | 100.00% |
| 2026-06-23 → 2026-07-20 | −15.40% | 0\* | 25.00 | 0.00%\* | 95.00% |
| 2023-08-01 → 2023-10-27 | −13.32% | 28 | 28.74 | 100.00% | 100.00% |

\* the 0 values are the 2026-07-21 forced final exit falling inside this window, not a de-risking event.

Through the deepest and longest drawdown (2022, 419 days underwater) the portfolio averaged **99.07% invested** and never fell below 86.66%.

### 2.4 Plain answer

**There is no mechanism by which this system reduces equity exposure in a sustained bear market. It is structurally ~100% long.**

The SMA100 filter changes *which* stocks are held, not *how much* is held. It can only produce cash when fewer than 30 names in the entire 177-symbol universe are above their 100-day average, which happened at exactly one rebalance in five and a half years, and even then reduced deployment by roughly 13 percentage points at its worst. The name "no_regime" is accurate: `rs_use_market_regime = False`, and no other de-risking path exists.

---

## 3. Measurement 3 — ATR / Risk-Parity Sizing (sandbox experiment)

### 3.1 Fidelity gate — run before trusting any result

Position sizing lives inside `_run_relative_strength_rotation_backtest`, so the sandbox is a copy of that loop with a sizing switch. A copy that has silently drifted would produce meaningless numbers, so the experiment **aborts unless its equal-weight mode reproduces the frozen baseline exactly**:

```
=== FIDELITY GATE (equal-weight replica vs frozen baseline) ===
  replica : 175.49% / 770 trades / maxDD -18.97%
  baseline: 175.49% / 770 trades / maxDD -18.97%
  faithful: True
```

Ranking, SMA100 filter, monthly rebalance calendar, next-day-open execution, transaction costs, liquidity filter and universe are all imported unmodified from production. Only sizing differs.

### 3.2 Convention used, and why it matters

**The experiment keeps the production Top-30 cap.** Position sizes come from `shares = equity × risk_factor / ATR20` (ATR taken from the production `ATR_Pct` feature at the signal date), and names are entered in **rank order**, highest RS_Score first, so that when capital runs out the *lowest-ranked* names go unfilled. Rank order is inert under equal weight — which is why production can sort alphabetically — but decisive here.

This is not identical to Clenow. Clenow lets position count emerge by continuing down the ranking past rank 30 until capital is exhausted. Keeping the Top-30 cap was the only way to satisfy "keep EVERYTHING else identical — same ranking, same filter, same universe; only position sizing changes."

**Where the cap binds:** at `rf=0.0005` the book is only 72.87% deployed with **zero** unfilled entries — capital was left over at rank 30 and Clenow's method would have kept buying ranks 31, 32, … So **the `rf=0.0005` result understates Clenow's method**; its shortfall is mostly idle cash, not sizing. At `rf=0.001` and `rf=0.002` capital *is* exhausted inside the top 30 (473 and 1128 unfilled entry attempts), so the cap does not bind and those two are faithful to Clenow's construction.

### 3.3 Results

| Variant | Return | Trades | Vol | Max DD | Sharpe (trading-day) | Avg positions | Deployed (mean) |
|---|---|---|---|---|---|---|---|
| **equal_weight (= production)** | **175.49%** | **770** | **18.89%** | **−18.97%** | **0.995** | 28.95 | 99.78% |
| atr rf=0.0005 | 86.36% | 792 | 11.47% | −13.19% | 0.968 | 29.92 | **72.87%** |
| atr rf=0.001 (Clenow default) | 166.30% | 547 | 17.27% | −17.42% | 1.038 | 22.75 | 99.38% |
| atr rf=0.002 | 207.56% | 257 | 19.93% | −19.32% | 1.046 | 12.82 | 100.00% |

Deltas versus the equal-weight control:

| Variant | Δ return | Δ trades | Δ vol | Δ max DD | CAGR |
|---|---|---|---|---|---|
| atr rf=0.0005 | **−89.13 pp** | +22 | −7.42 pp | +5.78 pp (shallower) | 10.97% |
| atr rf=0.001 | −9.19 pp | −223 | −1.62 pp | +1.55 pp (shallower) | 17.79% |
| atr rf=0.002 | **+32.07 pp** | −513 | +1.04 pp | −0.35 pp (deeper) | 20.66% |

Position weight distribution at entry:

| Variant | Min | Median | Max | Entries | Unfilled attempts |
|---|---|---|---|---|---|
| equal_weight | 0.00%\* | 3.33% | 3.33% | 770 | 65 |
| atr rf=0.0005 | 0.78% | 2.17% | **12.56%** | 792 | 0 |
| atr rf=0.001 | 0.00%\* | 3.86% | **21.84%** | 547 | 473 |
| atr rf=0.002 | 0.00%\* | 6.98% | **19.54%** | 257 | 1128 |

\* a 0.00% minimum is a residual fill when cash was nearly exhausted, not a sizing outcome. Equal weight is otherwise exactly 3.33% (= 1/30) by construction.

### 3.4 What the numbers show

- The **concentration/diversification trade-off is monotonic**: raising `risk_factor` cuts the number of positions (28.95 → 22.75 → 12.82), raises the median weight (3.33% → 3.86% → 6.98%), and raises both return and volatility.
- ATR sizing produces **markedly less uniform portfolios**: a single position reached 21.84% of equity at `rf=0.001`, versus a hard 3.33% under equal weight.
- **Trade count falls sharply** as `risk_factor` rises (770 → 547 → 257), because fewer positions are opened per rebalance.
- Risk-adjusted return is **not dramatically different** in any variant: trading-day Sharpe spans 0.968 to 1.046 against the control's 0.995.
- The `rf=0.0005` row is substantially a de-levered portfolio (72.87% invested), which is why its return falls 89 pp while its Sharpe barely moves.

No choice is recommended.

---

## 4. Analysis Code and Review

Two evaluation-only scripts, both in `scratch/`, neither imported by anything, no production default changed:

- `scratch/phase8a_measurements.py` (349 lines) — Measurements 1 and 2.
- `scratch/phase8a_atr_sizing_sandbox.py` (360 lines) — Measurement 3.

`/code-review` (ECC, local mode) findings:

- **MEDIUM — the sandbox duplicates ~200 lines of the production backtest loop.** If production sizing or accounting changes, the copy drifts silently. Mitigated by the fidelity gate, which aborts the run unless the equal-weight replica reproduces the frozen baseline exactly; that gate is the protection and it is checked on every run.
- **LOW — `phase8a_measurements.py` monkeypatches `_relative_strength_metrics_from_equity`** to capture the `position_count_curve` the production function computes but does not return. The wrapper records its argument and delegates unchanged, and is restored in a `finally`. No strategy or accounting behaviour is altered.
- **LOW — the "minimum entry weight" statistic is uninformative** (0.00% rows) because it is dominated by residual fills when cash is nearly exhausted, not by the sizing rule. Median and max are the meaningful columns.
- **Security:** no credentials, no network, no external input, no path construction from untrusted data. `grep` for `token|api_key|password|secret|TIINGO` over both files returns nothing. `py_compile` passes on both.
- **No lookahead introduced:** ATR and the liquidity row are taken from the *signal* date; execution price is the *next* session's open, exactly as production does.

---

## 5. Hard-Rule Compliance

- Strategy, ranking, benchmark logic, universe and validators: unchanged. `git status` over `testdata/`, `tests/` and `scripts/` is empty.
- No default changed. Measurement 3 is sandbox-only and wired into nothing.
- No `--live` run, no broker or order calls, no network calls to any data provider.
- systemd timer and scheduler state untouched.
- No credential value read, echoed, printed, or logged.
- Side effect disclosed: the runs may have written derived `.cache/prepared_features/*.pkl` entries. Regenerable cache, not source data.

## 6. Items Surfaced for the Operator

1. **The reported Sharpe (0.53) is understated by the calendar-resample defect in §1.1** — 327 of 1502 return observations are dropped. This is a production metric defect, not a measurement artifact, and it feeds directly into any Carver-style volatility target. Not fixed here (read-only turn).
2. **There is no bear-market exposure reduction** (§2.4). Whatever the Risk Engine becomes, this is the current starting point.
3. **The window ends inside an open 15.40% drawdown** (peak 2026-06-23, trough 2026-07-20, unrecovered).
4. **The `rf=0.0005` sandbox row understates Clenow's method** because the Top-30 cap binds there (§3.2).

---

*No GO/NO-GO, no design recommendation — evidence only.*
