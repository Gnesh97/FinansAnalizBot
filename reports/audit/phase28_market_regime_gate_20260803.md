## EXECUTIVE SUMMARY
Verdict:        Tested idea #3 from this session's brainstorm: does turning on the
                 existing-but-unused rs_use_market_regime flag (go to cash when SPY is
                 below its 200-day SMA) improve results on the survivorship-bias-
                 corrected universe? No -- clearly worse. Baseline: 65.86% return / 0.76
                 Sharpe / 20.52% max drawdown. With the regime gate on: 19.10% / 0.37 /
                 19.12% -- return collapses to less than a third of baseline and Sharpe
                 roughly halves, for only a marginal 1.4-point drawdown improvement. The
                 strategy sat out of the market for 23 of 73 monthly rebalances (~31%)
                 under the gate. This idea is clearly not worth pursuing further.
Changed:         New file research_sandbox/rs_market_regime_gate.py (reuses the frozen
                 production selection logic's own existing, already-implemented regime
                 gate read-only and unmodified -- the flag already exists in production
                 code and is simply False in the frozen baseline). No production code
                 touched.
Suite:           N/A -- research/evaluation task, no production code or test suite
                 changed.
Anomalies:       None.
Open items:      All three ideas from this session's brainstorm (longer lookback --
                 phase26, stricter trend filter -- phase27, market regime gate -- this
                 report) are now tested. None produced a clear, unambiguous improvement
                 worth pursuing as a production change; phase26's longer-lookback finding
                 is the only one with a genuinely positive (risk-adjusted) signal, and
                 even that is directional evidence, not a recommendation.
Operator action: None required. This closes out the three-idea brainstorm from earlier in
                 this session.

---

## Method

Reused `_relative_strength_selection`'s own built-in, already-implemented regime gate
(`_relative_strength_regime`, unchanged production code) -- when `rs_use_market_regime`
is True, the strategy holds 0% equity at any rebalance where the benchmark's close is
below its `rs_market_regime_sma_days`-day (200) SMA. Loaded SPY as `benchmark_data` from
`testdata/research_data/rs_v2_tiingo_market_data/benchmarks/daily/SPY_1D.csv` (the
project's own production benchmark path). Same survivorship-bias-corrected universe and
frozen 126-day lookback / monthly rebalance as phase24-27.

## Results

| variant | net return | Sharpe | max drawdown | months out of market (of 73) |
|---|---|---|---|---|
| baseline (no regime gate) | 65.86% | 0.76 | 20.52% | 0 |
| plus market regime gate | 19.10% | 0.37 | 19.12% | 23 |

Baseline again matches phase24/25/26/27 exactly (65.86%), confirming setup consistency.

## Interpretation

The regime gate's lagging 200-day SMA signal exits after a decline has already happened
and re-enters after a recovery is already underway -- over 2020-07-27+ (a period that
includes the sharp 2022 bear market followed by a strong 2023-2025 recovery), this pattern
plausibly means sitting out much of the recovery's strongest days while still having been
exposed to a meaningful chunk of the initial decline (drawdown only improved 1.4 points,
nowhere near proportional to the return given up). This is the well-known cost of trend-
following regime filters in a V-shaped-recovery-heavy period -- not a bug, just a bad fit
for this particular window and this specific 200-day threshold. A different SMA length or
a faster regime signal might behave differently, but that would be new parameter search,
not the "turn on the existing flag" test that was actually asked for; not pursued further
given the clarity of this result.

## Evidence

```
$ python3 research_sandbox/rs_market_regime_gate.py
Loading EXPANDED universe (production + full delisted population) ...
...
full population: 6120 loaded / 9423 had some data / 9423 candidates total (3303 dropped: <30 days or split-guard)
Loaded 6296 symbols.
  baseline (no regime gate)    return=   65.86% sharpe=  0.76 maxdd=  20.52% months_out_of_market=  0  (24.7s)
  plus_market_regime_gate      return=   19.10% sharpe=  0.37 maxdd=  19.12% months_out_of_market= 23  (21.2s)

Wrote /home/gnesh/Desktop/BOT - Kopya/research_sandbox/output/rs_market_regime_gate.csv
```

## `/code-review` (ECC)

No production code changed. `research_sandbox/rs_market_regime_gate.py` reuses
`_relative_strength_selection`'s existing regime-gate logic completely unmodified --
this script only supplies params and a benchmark, it does not implement any new
selection/filter logic itself. Self-review: no secrets, no network calls, output confined
to gitignored `research_sandbox/output/`, no broker/order/live-trading paths touched.
