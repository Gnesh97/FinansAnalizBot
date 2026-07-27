# Phase 8C — What a Regime Filter Would Actually Have Done

**Date:** 2026-07-27
**Scope:** Read-only measurement. No strategy/ranking/benchmark logic changed, no default changed, nothing committed, no `--live` run. The systemd timer, scheduler and `dss_scheduler.py` were **not touched** — tonight's first real nightly run is undisturbed. The sandbox lives in `scratch/`, is imported by nothing, and is wired into no production path.

**Dataset:** validated `rs_v2_tiingo`, frozen window `2020-07-27 → 2026-07-21`, frozen 177-symbol universe.

---

## 0. Fidelity Gate — passed before any variant was read

```
=== FIDELITY GATE ===
  sandbox : 175.49% / 770 trades / maxDD -18.97% / fp ba697e9653db37bd
  baseline: 175.49% / 770 trades / maxDD -18.97% / fp ba697e9653db37bd
  passed  : True
```

Return, trade count, max drawdown **and** the per-rebalance selection fingerprint all match the governing baseline exactly. The script aborts and writes no variant results otherwise. Ranking, the SMA100 stock filter, the rebalance calendar, next-day-open execution, transaction costs and the liquidity filter are imported unmodified from production; only the regime overlay differs.

### Which index series was used

**SPY**, read directly from `testdata/research_data/rs_v2_tiingo_market_data/benchmarks/daily/SPY_1D.csv` — 1503 sessions, `2020-07-27` → `2026-07-21`, exactly the frozen window.

Worth flagging: the production loader `_load_daily_benchmark` tries `["QQQ", "SPY"]` in that order, so the benchmark the **ranking** engine receives is **QQQ**, not SPY. The regime overlay therefore uses a different index from the one production's own (disabled) regime switch would use. SPY was used because you specified it.

### Signal availability and warm-up

| Signal | First valid |
|---|---|
| SPY SMA100 | 2020-12-15 |
| SPY SMA200 | 2021-05-11 |
| SPY trailing 252-session return | 2021-07-27 |

The strategy first holds positions on **2021-02-01**. A NaN indicator is treated as **risk-on**: refusing to invest because an indicator has not warmed up would be a data artifact, not a market signal. This means variant B is inert between 2021-02-01 and 2021-05-11, and variant C until 2021-07-27. All deployment statistics below are measured from 2021-02-01.

### No look-ahead

The regime signal is read from the SPY **close of the rebalance date**, and the resulting target is executed at the **next session's open** — the same convention production uses for its own regime series. The close of day D is known before the open of D+1.

---

## 1. What Was Implemented

| Variant | Signal | Action |
|---|---|---|
| **A control** | none | none — this is the current system |
| **B clenow_sma200** | SPY < SMA200 | **Blocks NEW entries only.** Names already held keep their normal exit rules (rotation out of the top 30, or SMA100 failure). No forced liquidation. |
| **C antonacci_12m** | SPY trailing 252-session return ≤ 0 | **Fully to cash** at that rebalance; re-enters at the first rebalance where the trailing return is positive. |
| **D index_sma100** | SPY < SMA100 | **Fully to cash.** Chosen to separate the two things B and C vary at once: it holds C's action constant while making the signal faster, and it mirrors the 100-day lookback already used at stock level. |

Variant B could **not** be expressed with production's own `rs_use_market_regime` switch: when risk-off, `_relative_strength_selection` sets `selections[date] = set()`, which force-liquidates. B is implemented in the sandbox instead.

---

## 2. Results

Volatility and Sharpe lead with the **trading-day convention** (`equity.pct_change()`), per Phase 8A. Sharpe uses **no risk-free rate** — it is raw mean return ÷ standard deviation × √252, matching the system's own definition.

| Variant | Return | CAGR | Trades | Vol (trading-day) | Vol (system resample) | Max DD | Sharpe | Mean deployed | % days in cash | Regime events |
|---|---|---|---|---|---|---|---|---|---|---|
| **A control** | **175.49%** | 18.46% | 770 | **18.89%** | 19.13% | **−18.97%** | **0.995** | 99.78% | 0.00% | 0 |
| **B clenow_sma200** | 149.19% | 16.49% | 665 | 17.59% | 17.79% | −17.72% | 0.959 | 87.38% | 0.00% | 13 |
| **C antonacci_12m** | **191.26%** | 19.57% | 651 | 17.41% | 17.63% | −19.36% | **1.118** | 83.32% | 16.68% | 1 |
| **D index_sma100** | 77.28% | 10.04% | 728 | 15.79% | 16.05% | **−26.49%** | 0.688 | 77.12% | 22.87% | 8 |

Deltas versus control:

| Variant | Δ return | Δ vol | Δ max DD | Δ Sharpe |
|---|---|---|---|---|
| B clenow_sma200 | **−26.30 pp** | −1.30 pp | +1.25 pp (shallower) | −0.036 |
| C antonacci_12m | **+15.77 pp** | −1.48 pp | −0.39 pp (deeper) | **+0.123** |
| D index_sma100 | **−98.21 pp** | −3.10 pp | **−7.52 pp (deeper)** | −0.307 |

### Five largest drawdowns per variant

**A control**

| Depth | Peak → trough → recovery | Duration | Recovered |
|---|---|---|---|
| −18.97% | 2025-02-19 → 2025-04-08 → 2025-08-06 | 167 d | yes |
| −18.12% | 2022-04-21 → 2022-10-14 → 2023-06-14 | 419 d | yes |
| −16.41% | 2021-11-22 → 2022-01-27 → 2022-04-20 | 148 d | yes |
| −15.40% | 2026-06-23 → 2026-07-20 → — | 28 d | **no, open** |
| −13.32% | 2023-08-01 → 2023-10-27 → 2023-12-26 | 147 d | yes |

**B clenow_sma200**

| Depth | Peak → trough → recovery | Duration | Recovered |
|---|---|---|---|
| −17.72% | 2021-11-22 → **2023-03-17** → 2023-07-18 | **602 d** | yes |
| −15.80% | 2025-02-19 → 2025-04-08 → 2025-09-15 | 207 d | yes |
| −15.73% | 2026-06-23 → 2026-07-20 → — | 28 d | **no, open** |
| −13.36% | 2023-08-01 → 2023-10-27 → 2024-02-02 | 185 d | yes |
| −10.45% | 2026-02-26 → 2026-03-30 → 2026-04-30 | 62 d | yes |

**C antonacci_12m**

| Depth | Peak → trough → recovery | Duration | Recovered |
|---|---|---|---|
| −19.36% | 2025-02-19 → 2025-04-08 → 2025-08-06 | 167 d | yes |
| −16.41% | 2021-11-22 → 2022-01-27 → 2022-04-20 | 148 d | yes |
| −15.38% | 2026-06-23 → 2026-07-20 → — | 28 d | **no, open** |
| −13.29% | 2023-08-01 → 2023-10-27 → 2023-12-26 | 147 d | yes |
| −10.44% | 2026-02-26 → 2026-03-30 → 2026-05-05 | 67 d | yes |

**D index_sma100**

| Depth | Peak → trough → recovery | Duration | Recovered |
|---|---|---|---|
| **−26.49%** | 2021-09-07 → **2023-03-17** → 2024-07-09 | **1036 d** | yes |
| −14.47% | 2025-02-19 → 2025-03-13 → 2025-10-06 | 228 d | yes |
| −12.19% | 2026-06-23 → 2026-07-20 → — | 28 d | **no, open** |
| −10.45% | 2026-02-26 → 2026-03-30 → 2026-06-02 | 95 d | yes |
| −9.21% | 2024-07-11 → 2024-08-05 → 2024-09-23 | 74 d | yes |

### The 2022 bear market (2022-04-21 → 2022-10-14)

| Variant | Mean deployed | Min deployed | Days fully in cash | Mean positions |
|---|---|---|---|---|
| A control | **98.95%** | 86.66% | 0 | 29.59 |
| B clenow_sma200 | 31.57% | 3.40% | 0 | 8.57 |
| C antonacci_12m | 22.76% | 0.00% | **95** | 6.83 |
| D index_sma100 | 24.39% | 0.00% | **93** | 7.32 |

All three filters did exactly what they are advertised to do here: exposure fell from ~99% to 23–32%. Note B reaches 3.4% deployed **without ever forcing an exit** — natural rotation empties the book when nothing can be re-bought.

### The currently-open 2026 drawdown (2026-06-23 → 2026-07-21)

| Variant | Mean deployed | Min deployed | Days fully in cash |
|---|---|---|---|
| A control | 100.00% | 100.00% | 0 |
| B clenow_sma200 | 100.00% | 100.00% | 0 |
| C antonacci_12m | 100.00% | 100.00% | 0 |
| D index_sma100 | 100.00% | 100.00% | 0 |

**No filter was risk-off during the open drawdown.** SPY was above every threshold and its trailing 12-month return was positive throughout. None of these overlays would have provided any protection against the decline currently in progress.

### Regime events — when each filter actually fired

**B (13 blocking events, no forced exits).** 2022-02-28 (12 blocked / 29 held), 2022-04-29 (10/29), 2022-05-31 (17/20), 2022-06-30 (27/13), 2022-07-29 (28/3), 2022-08-31 (28/2), 2022-09-30 (24/2), 2022-10-31 (29/1), 2022-12-30 (14/30), 2023-10-31 (19/28), 2025-03-31 (16/29), 2025-04-30 (20/14), 2026-03-31 (13/27).

**C (1 liquidation).** 2022-05-31, liquidating all 30 positions. It was risk-off at 11 rebalances in total, but the book was already empty at the other 10, so no further liquidation was needed — the filter simply kept it in cash through 2023-03-31.

**D (8 liquidations).** 2021-09-30 (29 positions), 2022-01-31 (30), 2022-04-29 (30), 2022-08-31 (30), 2022-12-30 (30), 2023-09-29 (28), 2025-03-31 (29), 2026-03-31 (27). Risk-off at 15 rebalances. **This is the whipsaw pattern** — eight full round-trips, each paying costs and frequently re-entering higher.

---

## 3. The Cost Side of the Insurance — how often each filter was wrong

"Wrong" = the filter was risk-off at a rebalance and SPY then **rose** over the following month (the horizon the block or liquidation actually applied to).

| Variant | Risk-off rebalances | Wrong | Wrong % | Mean forward return | Median forward return |
|---|---|---|---|---|---|
| B clenow_sma200 | 13 | 9 | **69.2%** | +2.82% | +5.56% |
| C antonacci_12m | 11 | 6 | **54.5%** | +0.42% | +1.60% |
| D index_sma100 | 15 | 10 | **66.7%** | +2.84% | +5.56% |

Per-trigger forward returns:

- **B:** 2022-02-28 (+3.8), 2022-04-29 (+0.2), 2022-05-31 (−8.2), 2022-06-30 (+9.2), 2022-07-29 (−4.1), 2022-08-31 (−9.2), 2022-09-30 (+8.1), 2022-10-31 (+5.6), 2022-12-30 (+6.3), 2023-10-31 (+9.1), 2025-03-31 (−0.9), 2025-04-30 (+6.3), 2026-03-31 (+10.5)
- **C:** 2022-05-31 (−8.2), 2022-06-30 (+9.2), 2022-07-29 (−4.1), 2022-08-31 (−9.2), 2022-09-30 (+8.1), 2022-10-31 (+5.6), 2022-11-30 (−5.8), 2022-12-30 (+6.3), 2023-01-31 (−2.5), 2023-02-28 (+3.7), 2023-03-31 (+1.6)
- **D:** 2021-09-30 (+7.0), 2022-01-31 (−3.0), 2022-02-28 (+3.8), 2022-04-29 (+0.2), 2022-05-31 (−8.2), 2022-06-30 (+9.2), 2022-08-31 (−9.2), 2022-09-30 (+8.1), 2022-10-31 (+5.6), 2022-12-30 (+6.3), 2023-09-29 (−2.2), 2023-10-31 (+9.1), 2025-03-31 (−0.9), 2025-04-30 (+6.3), 2026-03-31 (+10.5)

Every filter was wrong more often than right on this window, and B and D were wrong by a wide margin (mean +2.8% forgone per trigger). C's misses were close to break-even (+0.42% mean), which is the arithmetic behind its better headline result.

---

## 4. Plain Answers

### 4.1 Does a regime filter improve risk-adjusted return over this window, or just cost return?

**It depends entirely on which filter — the three variants do not point the same way, and two of the three cost return.**

- **C (Antonacci 12-month) improved both:** +15.77 pp return and Sharpe 1.118 vs 0.995, at 1.48 pp lower volatility. It is the only variant that beat the control on return.
- **B (Clenow SMA200) cost return** (−26.30 pp) for a marginal volatility reduction and a slightly *worse* Sharpe (0.959 vs 0.995).
- **D (SMA100 to cash) was severely destructive:** −98.21 pp of return, Sharpe 0.688, and a max drawdown 7.52 pp **deeper** than doing nothing.

Every filter reduced volatility. Only one improved risk-adjusted return. A faster signal on the same action (D vs C) was catastrophic, which says the result is driven by signal choice, not by "having a regime filter".

### 4.2 Does it reduce the depth and duration of the worst drawdowns?

**Depth: barely, and not reliably. Duration: it made them markedly worse.**

- Only B reduced max drawdown (−17.72% vs −18.97%, a 1.25 pp improvement). C was 0.39 pp **deeper**; D was 7.52 pp **deeper**.
- **Duration went the wrong way.** The control's worst episode lasted 419 days. B's worst lasted **602 days**; D's lasted **1036 days**. Both stretched because being out of the market during the recovery delays the return to the prior peak — the equity high of 2021-11-22 was not regained until 2023-07-18 (B) or 2024-07-09 (D).
- This is the counter-intuitive core of the measurement: the filters genuinely cut 2022 exposure from ~99% to 23–32%, and still did not produce shallower or shorter worst-case drawdowns, because they also missed the rebound.

### 4.3 CRITICAL CAVEAT — what this window can and cannot tell us

**The test window contains no catastrophic decline, and a regime filter's entire purpose is insurance against exactly that event.**

`2020-07-27 → 2026-07-21` begins *after* the February–March 2020 COVID crash. It contains one moderate bear market (2022, −18% at strategy level) and otherwise a strongly rising market. It contains nothing resembling 2008 (−50%+), 2000–2002, or the 2020 crash itself.

Consequently:

- **What this measurement can tell us:** what each filter costs in a bull-heavy regime; how often it whipsaws; how it behaves through a moderate, orderly decline; and that signal choice dominates the outcome.
- **What it cannot tell us:** whether any of these filters would protect capital in a 40–50% systemic decline — the scenario they exist for. That event is absent from the sample, so its benefit is **unmeasured, not disproven**.
- **A weak or negative result here is not evidence that regime filtering is useless.** Insurance reliably shows a negative expected return in every period where the insured event does not occur. The 2022 exposure figures (99% → 23%) show the mechanisms *do* engage; this window simply never tested whether that engagement pays off catastrophically.
- Equally, C's outperformance here is **one favourable episode**, not a validated edge. It rests on a single liquidation event (2022-05-31) and 11 risk-off rebalances. That is an extremely small sample from which to conclude anything about future behaviour.

### 4.4 A further limitation worth stating

None of the four variants was risk-off during the **currently-open 2026-06-23 drawdown**. Whatever is happening now, none of these overlays would be reducing exposure today.

---

## 5. Analysis Code and Review

`scratch/phase8c_regime_sandbox.py` (499 lines), evaluation-only, imported by nothing, no production default changed.

`/code-review` (ECC, local mode):

- **MEDIUM — the sandbox duplicates the production backtest loop** (as in Phase 8A). Divergence risk is contained by the fidelity gate, which aborts the run unless the control reproduces the frozen baseline exactly — return, trade count, max drawdown *and* selection fingerprint. That gate ran and passed before any variant was read.
- **LOW — NaN regime indicators are treated as risk-on.** A defensible choice (see §0), but it means B and C are inert for their warm-up periods; both first-valid dates are reported so the reader can discount accordingly.
- **LOW — the "regime event" count undercounts risk-off periods for C and D**, because a liquidation is only logged when the book is non-empty. Risk-off rebalance counts are reported separately in §3 (11 for C, 15 for D) to close that gap.
- **Security:** no credentials, no network, no external input, no path construction from untrusted data; `grep` for `token|api_key|password|secret|TIINGO` returns nothing. `py_compile` passes.
- **No look-ahead:** signal from the rebalance-date close, execution at the next open — verified against production's own convention.
- **Production untouched:** `git status --porcelain` over `testdata/`, `tests/`, `scripts/` shows only the three files carrying the **previous** turn's uncommitted Phase 8B scheduler work. This phase added exactly one file, in `scratch/`.

---

## 6. Hard-Rule Compliance

- Strategy, ranking, benchmark logic, frozen 177 universe/hash, validators and the DSS freshness check: unchanged.
- No default changed. Sandbox wired into nothing.
- Nothing committed. No `--live` run. No broker/order calls. No network calls to any provider.
- **systemd timer, scheduler and `dss_scheduler.py` untouched this phase** — tonight's first real nightly run is undisturbed.
- No credential value read, echoed, printed, or logged.
- Side effect disclosed: the runs may have written derived `.cache/prepared_features/*.pkl` entries. Regenerable cache, not source data.

---

*No GO/NO-GO, no recommendation — numbers and framing only.*
