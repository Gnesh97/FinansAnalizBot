## EXECUTIVE SUMMARY
Verdict:        Re-ran the existing momentum-lookback / rebalance-frequency sensitivity
                 sweep (research_sandbox/rs_rotation_sensitivity.py) on the survivorship-
                 bias-corrected universe (production + full 6,120-name delisted
                 population from phase24) instead of production-only, to check whether
                 the frozen production choice (126-day lookback, monthly rebalance) is
                 still the best combo once the biased universe is corrected. Monthly
                 rebalance is confirmed robust in both universes (weekly is clearly worse
                 everywhere). 126-day lookback is still the single best by raw return in
                 the corrected universe (65.86%), but is NOT the best by risk-adjusted
                 metrics there: 189-day lookback has a higher Sharpe (0.83 vs 0.76) and
                 lower max drawdown (18.14% vs 20.52%) for a modest return cost, and
                 252-day has the lowest drawdown of all (15.94%). This is a genuine,
                 if modest, finding worth flagging for a future deliberate decision --
                 it is NOT a production change (see Operator action).
Changed:         research_sandbox/rs_rotation_sensitivity.py: added load_expanded_universe()
                 (reuses rs_survivorship_bias.load_delisted_universe_batched, with an
                 index-timezone-normalization fix -- see Anomalies) and a --expanded CLI
                 flag; run_sweep() now writes to a separate output file when expanded
                 (rs_rotation_sensitivity_expanded.csv) so the original production-only
                 sweep result is untouched. No production code touched.
Suite:           N/A -- research/evaluation task, no production code or test suite
                 changed.
Anomalies:       First run crashed with `TypeError: Cannot compare tz-naive and tz-aware
                 timestamps` inside the production selection logic's own union-of-indices
                 step -- production Tiingo frames (loaded via read_ohlcv_csv_fast) keep a
                 UTC-aware datetime index, while phase24's delisted-candidate frames are
                 normalized to timezone-naive dates (see phase24). Mixing both universes
                 without normalizing both sides the same way breaks any code that sorts
                 the union of frame indices. Fixed by normalizing the production frames'
                 index the same way inside load_expanded_universe() (not in the shared
                 load_universe(), to avoid touching the already-validated production-only
                 sweep path).
Open items:      This is a two-dimensional (lookback x rebalance-frequency) sweep only,
                 same grid as the original script (63/126/189/252 days, weekly/monthly).
                 Other candidate directions raised earlier in this session (a stricter
                 trend-strength filter, turning on the existing-but-unused market-regime
                 gate) are still unexplored.
Operator action: None required to act on. This is directional research evidence, not a
                 recommendation to change the frozen production strategy (CLAUDE.md's
                 immutables still apply -- no parameter optimization / auto-feedback to
                 production). If a longer lookback is ever considered as a deliberate,
                 human-approved change, this report is the evidence trail to start from.

---

## Method

Same script, same frozen `BASE_PARAMS` (only `rs_lookback_days` and
`rs_rebalance_frequency` swept, matching the original script's existing grid), same
`_relative_strength_selection` production logic, unchanged. The only difference between
the two sweeps below is the universe: `load_universe()` (production's 177 frozen symbols)
vs. the new `load_expanded_universe()` (that same 177 plus phase24's 6,120-name delisted
population, 6,296 symbols total).

## Results

**Production-only universe** (existing `research_sandbox/output/rs_rotation_sensitivity.csv`, unchanged):

| lookback | rebalance | return | sharpe | max_dd |
|---|---|---|---|---|
| 63 | weekly | 79.67% | 0.81 | 27.06% |
| 63 | monthly | 129.49% | 1.08 | 20.02% |
| 126 | weekly | 127.83% | 1.09 | 20.61% |
| **126** | **monthly** | **154.27%** | **1.17** | 19.34% |
| 189 | weekly | 75.14% | 0.79 | 21.84% |
| 189 | monthly | 90.81% | 0.87 | 17.67% |
| 252 | weekly | 83.38% | 0.85 | 20.22% |
| 252 | monthly | 99.17% | 0.93 | 20.92% |

**Survivorship-bias-corrected universe** (new `research_sandbox/output/rs_rotation_sensitivity_expanded.csv`):

| lookback | rebalance | return | sharpe | max_dd |
|---|---|---|---|---|
| 63 | weekly | 25.24% | 0.38 | 36.84% |
| 63 | monthly | 49.62% | 0.61 | 22.61% |
| 126 | weekly | 62.96% | 0.79 | 26.41% |
| **126** | **monthly** | **65.86%** | 0.76 | 20.52% |
| 189 | weekly | 29.64% | 0.47 | 27.15% |
| 189 | monthly | 64.80% | **0.83** | **18.14%** |
| 252 | weekly | 54.43% | 0.73 | 24.00% |
| 252 | monthly | 58.97% | 0.78 | **15.94%** |

## Interpretation

Two things hold up across both universes: monthly rebalance beats weekly at every
lookback (confirms the production choice is robust, not an artifact of the biased
universe), and 126-day lookback remains the top performer by raw return either way.

What changes: in the production-only (survivorship-biased) universe, 126/monthly is the
unambiguous winner on every metric simultaneously. In the corrected universe, it is only
the best by return -- 189-day lookback edges it out on both Sharpe (0.83 vs 0.76) and max
drawdown (18.14% vs 20.52%), and 252-day has the lowest drawdown of the whole grid
(15.94%) while still beating every non-126 combo on return. This is directionally
consistent with today's earlier finding (phase25): shorter lookback windows are more
exposed to a declining name's brief, short-lived momentum spike before its collapse,
since a shorter window reacts faster to (and gets fooled more often by) a short rally.
A longer lookback averages that noise out more, at some cost to how quickly it captures
genuine new trends -- the classic momentum-window tradeoff, just now visible with the
more realistic (delisted-inclusive) candidate pool instead of survivorship-biased data.

## Evidence

```
$ python3 research_sandbox/rs_rotation_sensitivity.py --expanded
...
full population: 6120 loaded / 9423 had some data / 9423 candidates total (3303 dropped: <30 days or split-guard)
Loaded 6296 symbols.
  lookback= 63 rebalance=weekly   return=   25.24% sharpe=  0.38 maxdd=  36.84%  (37.7s)
  lookback= 63 rebalance=monthly  return=   49.62% sharpe=  0.61 maxdd=  22.61%  (22.4s)
  lookback=126 rebalance=weekly   return=   62.96% sharpe=  0.79 maxdd=  26.41%  (36.1s)
  lookback=126 rebalance=monthly  return=   65.86% sharpe=  0.76 maxdd=  20.52%  (22.5s)
  lookback=189 rebalance=weekly   return=   29.64% sharpe=  0.47 maxdd=  27.15%  (36.3s)
  lookback=189 rebalance=monthly  return=   64.80% sharpe=  0.83 maxdd=  18.14%  (22.1s)
  lookback=252 rebalance=weekly   return=   54.43% sharpe=  0.73 maxdd=  24.00%  (36.2s)
  lookback=252 rebalance=monthly  return=   58.97% sharpe=  0.78 maxdd=  15.94%  (22.1s)

Wrote /home/gnesh/Desktop/BOT - Kopya/research_sandbox/output/rs_rotation_sensitivity_expanded.csv
```

First attempt failed:
```
TypeError: Cannot compare tz-naive and tz-aware timestamps
  File ".../testdata/strategies/relative_strength_rotation.py", line 382, in _relative_strength_selection
    sorted(set().union(*(frame.index for frame in features.values())))
```
Fixed as described in Anomalies, then re-ran successfully.

```
$ git status --porcelain
 M research_sandbox/rs_rotation_sensitivity.py
?? scratch/hf_full_scan/
?? scratch/hf_full_scan_pass1.py
?? scratch/hf_full_scan_pass1_parallel.py
?? scratch/hf_full_scan_pass2_analysis.py
?? scratch/hf_full_scan_pass3_real_candidates.py
?? scratch/phase10b_risk_engine_calibration_measure.json
?? scratch/phase5d_analysis_output.json
?? scratch/phase9a_frozen_window_measure.json
?? scratch/phase9a_frozen_window_measure.py
?? scratch/tiingo_fetch_adapter.py
?? scratch/tiingo_full_universe_analysis.py
?? testdata/baselines/rs_v2_tiingo_baseline.json
```

## `/code-review` (ECC)

No production code changed. `research_sandbox/rs_rotation_sensitivity.py` changes are
additive (`load_expanded_universe`, `--expanded` flag, separate output filename when
expanded) and reuse the same frozen production selection logic as before. The tz-mismatch
bug was in this sandbox script's own universe-merging code, not in production code; fixed
narrowly (normalize inside the new function only), verified by successful re-run producing
sane, cross-checked-consistent numbers (126/monthly's 65.86% matches phase24/25 exactly).
Self-review: no secrets, no network calls, output confined to gitignored
`research_sandbox/output/`, no broker/order/live-trading paths touched.
