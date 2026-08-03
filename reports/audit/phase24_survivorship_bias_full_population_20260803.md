## EXECUTIVE SUMMARY
Verdict:        CORRECTION to phase22/phase23. Scaling the survivorship-bias test from a
                 150-name pilot to the full ~9,400-candidate delisted population (6,120
                 usable after split-guard, 6,296 total symbols) REVERSES the earlier
                 finding: net return falls from 154.27% (production-only) to 65.86% -- not
                 the 163-205% the two small pilots showed. Sharpe drops from 1.174 to
                 0.764; max drawdown worsens from 19.34% to 20.52%. At full scale, the
                 classic survivorship-bias pattern (today's universe implicitly excludes
                 companies that later failed, inflating backtest returns) DOES appear for
                 this strategy. Both prior pilots (phase22's long-lived-only sample and
                 phase23's crash+stable stratified 150-name sample) were too small and
                 still not representative enough of the true breadth of delisted names
                 -- particularly the short-lived "brief run-up then collapse" pattern that
                 a 150-name sample under-captures regardless of how it's stratified.
Changed:         research_sandbox/rs_survivorship_bias.py: added load_delisted_universe_
                 batched() (reads each monthly HF parquet file once, filtered to the full
                 ~9,400-ticker candidate list, instead of once per ticker -- makes the
                 full population tractable: ~66 file reads instead of ~9,400x66) and a
                 run_full() entry point (python3 research_sandbox/rs_survivorship_bias.py
                 --full). Fixed a real bug found during this run: a small number of
                 tickers had duplicate daily rows after concatenating across months
                 (ValueError: cannot reindex on an axis with duplicate labels) -- root
                 cause is very likely a minor month-boundary overlap in the HF source's
                 monthly file slicing (a few boundary-adjacent rows appearing in both the
                 end of one month's file and the start of the next); fixed defensively by
                 keeping the last value on any duplicate date, which is safe regardless of
                 the exact cause. No production code touched.
Suite:           N/A -- research/evaluation task, no production code or test suite
                 changed.
Anomalies:       Duplicate-date rows across month-boundary files (see Changed) -- minor,
                 handled, not investigated further given time cost. Same known
                 limitations as phase21-23 still apply: forward-fill at last price rather
                 than true wipeout-to-zero (if anything this UNDERSTATES how bad the full-
                 population result should be, since real bankruptcies would be worse than
                 "flat at last price"); no split/dividend adjustment confirmation beyond
                 the coarse split-guard; ticker-reuse risk spot-checked on 6 names only.
Open items:      Given the forward-fill limitation biases in the conservative direction
                 (makes the delisted-inclusive result look BETTER than a true wipeout-
                 model would), the true survivorship-bias effect for this strategy could
                 be even larger than 154.27% -> 65.86% suggests. A wipeout-to-zero model
                 for confirmed-bankruptcy-pattern names (decline_ratio<=0.1, say) would be
                 the next honest refinement if this is worth pursuing further.
Operator action: Treat "the production backtest's reported ~154% return over this window
                 is likely inflated by survivorship bias, materially so" as the working
                 conclusion of this investigation. This does not mean the frozen strategy
                 is wrong or must change (CLAUDE.md's immutables still apply -- no
                 parameter/logic changes here) -- it means the REPORTED HEADLINE NUMBER
                 for this window should be read with that caveat in mind going forward.

---

## Why the pilots were wrong

Phase22's pilot (150 names, sorted by longest trading history) skewed toward mature,
later-acquired companies -- a return-positive delisting pattern. Phase23 corrected that by
stratifying 75 crash-pattern / 75 stable-pattern names, and the result got even BETTER
(204.61%), which in hindsight should have been a stronger warning sign than it was treated
as: even a "fair" 150-name sample is a tiny, non-random slice of a ~9,400-name population,
and 75 crash-pattern names chosen by longest-history-within-the-crash-bucket still isn't
the same as the population's actual mix of mostly short-lived, briefly-promising-then-
collapsed names. The full run's 6,120 loaded candidates include many more names with
under 100 months of history than either 150-name pilot did, and that's exactly the segment
that dragged the result down.

## Method (unchanged strategy logic, new loader only)

Same frozen production selection logic
(`testdata.strategies.relative_strength_rotation._relative_strength_selection`), same
`BASE_PARAMS` as phase22/23, same 2020-07-27+ window, same split-guard (drop any candidate
whose day-over-day close ratio ever falls outside [0.5, 2.0]). The only change is scale:
all 9,423 window-overlapping, sane-priced (see phase21) candidates from
`scratch/hf_full_scan/full_population_candidates.csv`, loaded via a batched-by-month
reader instead of the pilot's one-read-per-ticker-per-month approach (this was also a
meaningful engineering win: 66 file reads total vs. thousands, full load completed in a
few minutes instead of the 8-25 minutes the 150-name pilots took).

6,120 / 9,423 candidates survived (<30 days of data or split-guard trip dropped 3,303).

## Results

| | production_only | phase22 pilot (150, long-lived) | phase23 pilot (150, stratified) | **phase24 (6,120, full population)** |
|---|---|---|---|---|
| Symbols | 177 | 300 | 294 | **6,296** |
| Net return | 154.27% | 163.44% | 204.61% | **65.86%** |
| Sharpe | 1.174 | 1.102 | 1.284 | **0.764** |
| Max drawdown | 19.34% | 18.32% | 17.90% | **20.52%** |
| Rebalances with a delisted pick | 0 | 56/73 | 55/73 | **60/73** |

## Evidence

```
$ python3 research_sandbox/rs_survivorship_bias.py --full
Loading production (frozen, 177-symbol) universe...
  177 symbols loaded.
Loading full delisted candidate population (resampled 1m->daily, split-guarded)...
full population: 9423 candidates, 66 months to scan
  ... [66/66] 2025-12: 569 tickers, 134885 raw rows -> 3455 daily rows
full population: 6120 loaded / 9423 had some data / 9423 candidates total (3303 dropped: <30 days or split-guard)

Running baseline (production universe only)...
  {'label': 'production_only', 'n_symbols': 177, 'net_return_pct': 154.26709353527664, 'sharpe_ratio': 1.1742132650284753, 'max_drawdown_pct': 19.339525781189522, 'rebalance_count': 73, 'rebalances_with_delisted_pick': 0}

Running expanded (production + full delisted population)...
  {'label': 'production_plus_full_delisted_population', 'n_symbols': 6296, 'net_return_pct': 65.85957247761706, 'sharpe_ratio': 0.7642940988404405, 'max_drawdown_pct': 20.522435886500848, 'rebalance_count': 73, 'rebalances_with_delisted_pick': 60}

Wrote /home/gnesh/Desktop/BOT - Kopya/research_sandbox/output/rs_survivorship_bias_full_population.csv
```

First attempt at this run failed with `ValueError: cannot reindex on an axis with
duplicate labels` (duplicate-date rows for a handful of tickers after concatenating
month-boundary data); fixed with `daily = daily[~daily.index.duplicated(keep="last")]`
before the length/split-guard checks, then re-ran successfully.

```
$ git status --porcelain
 M .gitignore
?? reports/audit/phase21_hf_ohlcv1m_dataset_evaluation_20260802.md
?? reports/audit/phase22_survivorship_bias_pilot_20260802.md
?? reports/audit/phase23_survivorship_bias_stratified_pilot_20260803.md
?? research_sandbox/rs_survivorship_bias.py
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

No production code changed. `research_sandbox/rs_survivorship_bias.py` additions
(`load_delisted_universe_batched`, `run_full`) follow the same read-only reuse of the
frozen production selection logic as the rest of this sandbox; the duplicate-date fix is
a narrow, defensive one-liner with no behavioral effect on non-duplicated data. Self-
review: no secrets, no network calls, output confined to gitignored
`research_sandbox/output/`, no broker/order/live-trading paths touched.
