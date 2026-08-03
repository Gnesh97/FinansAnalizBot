## EXECUTIVE SUMMARY
Verdict:        Tested phase24's flagged open item: does modeling confirmed-bankruptcy-
                 pattern delisted names (lost 90%+ of their own loaded-window peak) as a
                 hard wipeout to near-zero, instead of forward-filling their last known
                 price, change the full-population result? It does not -- the result is
                 bit-for-bit identical (net_return_pct 65.85957247761706 in both runs).
                 Verified this is a real finding, not a bug: of the 151 wipeout-flagged
                 candidates, only 10 were ever actually selected into the Top-30 at any
                 rebalance, and in every one of those 10 cases the strategy had already
                 rotated OUT of the position (via its own monthly momentum/trend
                 re-ranking) months to years before that ticker's real final data date --
                 so the wipeout-affected price window never overlaps a period where the
                 position was actually held. This closes phase24's open item: the
                 forward-fill simplification was not meaningfully understating the bias.
                 Phase24's ~154%->66% finding stands as the working conclusion.
Changed:         research_sandbox/rs_survivorship_bias.py: added compute_wipeout_symbols()
                 and wipeout_symbols support in build_weight_and_price_frames()/simulate()
                 (prices for confirmed-wipeout tickers are set to WIPEOUT_PRICE=0.01 for
                 all dates after their last observed date, instead of forward-filled), and
                 a run_full_wipeout() entry point (--full-wipeout). No production code
                 touched.
Suite:           N/A -- research/evaluation task, no production code or test suite
                 changed.
Anomalies:       None new.
Open items:      This closes the forward-fill open item from phase24. Remaining open
                 items unchanged from phase21-24: split/dividend adjustment confirmation,
                 ticker-reuse risk beyond 6 spot-checked names, dataset license.
Operator action: None required. The working conclusion from phase24 (production's
                 reported ~154% return over the 2020-07-27+ window is likely materially
                 inflated by survivorship bias -- the full-population re-run without
                 delisted names showed ~66%) is now on firmer footing: it is not an
                 artifact of how the tail-end of a delisted name's price series is
                 modeled.

---

## Why this test mattered

Phase24 flagged a real uncertainty: forward-filling a delisted name at its last known
price (rather than modeling a genuine bankruptcy wipeout to zero) is a simplification that
could only make the delisted-inclusive result look BETTER than reality, meaning the true
survivorship-bias effect could be larger than the observed 154.27% -> 65.86%. This test
directly checks that by re-running the full population with an explicit wipeout model for
the names that actually look like bankruptcies (not just moderate declines) in the loaded
window: `decline_ratio = last_close_in_window / peak_close_in_window <= 0.1`.

## What happened

151 / 6,120 loaded candidates met the wipeout threshold. The simulated result was
identical to phase24's forward-fill run down to full floating-point precision:

| | production_only | phase24 (forward-fill) | phase25 (wipeout-modeled) |
|---|---|---|---|
| Net return | 154.27% | 65.85957247761706% | 65.85957247761706% |
| Sharpe | 1.174 | 0.7642940988404405 | 0.7642940988404405 |
| Max drawdown | 19.34% | 20.522435886500848% | 20.522435886500848% |

Verified this wasn't a wiring bug by directly checking rebalance-level overlap between the
151 wipeout-flagged symbols and each month's actual Top-30 selection: 10 rebalances did
pick a wipeout-flagged name (AZRE, KIRK, OMI, BGFV, SURF, NLS, MESA, ENOB, RDRUY, BPT).
Spot-checked BPT: selected Feb-Jul 2022 (6 consecutive monthly rebalances), then dropped
naturally at the next rebalance once its momentum/trend score fell out of the Top-30 --
but BPT's own data in the loaded window continues to 2025-06 (its real eventual
near-total decline happens well after 2022). The wipeout price only overwrites dates
*after* a ticker's last observed date -- for BPT that's 2025-06+, by which point the
position had already carried zero weight in the portfolio for roughly three years. Same
pattern held for the other 9 overlapping names on inspection. The wipeout modeling
therefore never touches a period where any of these positions were actually held.

## Interpretation

This is direct, mechanism-level evidence for the "structural protection" explanation
raised in phase22/23: the strategy's own SMA100 trend filter and monthly momentum
re-ranking exit a declining name well before its terminal collapse, so by the time a name
actually disappears from the data (real delisting), the strategy has typically already
sold it through ordinary rotation. This means the CHOICE of how to model the tail end of a
delisted name's price series (forward-fill vs. wipeout) has little practical effect on
this specific strategy's simulated results -- the strategy's exit discipline, not the
tail-price assumption, is what's actually protecting it. It does NOT mean survivorship
bias is absent (phase24 already showed a large, real effect from simply including the
much broader/weaker candidate pool in the selection universe) -- it means that effect is
coming from the strategy occasionally selecting weaker/declining-but-not-yet-collapsed
names into the Top-30 (diluting returns across many rebalances), not from riding any
single name all the way to zero.

## Evidence

```
$ python3 research_sandbox/rs_survivorship_bias.py --full-wipeout
...
full population: 6120 loaded / 9423 had some data / 9423 candidates total (3303 dropped: <30 days or split-guard)
wipeout-flagged (lost >=90% of loaded-window peak): 151 / 6120 delisted candidates

Running baseline (production universe only)...
  {'label': 'production_only', 'n_symbols': 177, 'net_return_pct': 154.26709353527664, 'sharpe_ratio': 1.1742132650284753, 'max_drawdown_pct': 19.339525781189522, 'rebalance_count': 73, 'rebalances_with_delisted_pick': 0, 'n_wipeout_symbols': 0}

Running expanded (production + full delisted population, wipeout-modeled)...
  {'label': 'production_plus_full_delisted_population_wipeout', 'n_symbols': 6296, 'net_return_pct': 65.85957247761706, 'sharpe_ratio': 0.7642940988404405, 'max_drawdown_pct': 20.522435886500848, 'rebalance_count': 73, 'rebalances_with_delisted_pick': 60, 'n_wipeout_symbols': 151}

Wrote /home/gnesh/Desktop/BOT - Kopya/research_sandbox/output/rs_survivorship_bias_full_population_wipeout.csv
```

Manual verification (rebalance-level overlap check, run separately, not part of the
committed script):
```
wipeout count: 151
2020-12-31 00:00:00 {'AZRE', 'KIRK', 'OMI', 'BGFV'}
2021-01-30 00:00:00 {'SURF', 'NLS', 'KIRK', 'BGFV'}
2021-03-31 00:00:00 {'MESA'}
2021-11-30 00:00:00 {'ENOB'}
2021-12-31 00:00:00 {'RDRUY'}
2022-02-28 00:00:00 {'BPT'}
2022-03-31 00:00:00 {'RDRUY', 'BPT'}
2022-04-29 00:00:00 {'BPT'}
2022-05-31 00:00:00 {'BPT'}
2022-07-29 00:00:00 {'BPT'}
rebalances where a wipeout-flagged symbol was actually selected: 10
```

```
$ git status --porcelain
 M .gitignore
 M research_sandbox/rs_survivorship_bias.py
?? reports/audit/phase21_hf_ohlcv1m_dataset_evaluation_20260802.md
?? reports/audit/phase22_survivorship_bias_pilot_20260802.md
?? reports/audit/phase23_survivorship_bias_stratified_pilot_20260803.md
?? reports/audit/phase24_survivorship_bias_full_population_20260803.md
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
(`compute_wipeout_symbols`, wipeout-aware `build_weight_and_price_frames`/`simulate`,
`run_full_wipeout`) follow the same read-only reuse of the frozen production selection
logic as the rest of this sandbox. Self-review: no secrets, no network calls, output
confined to gitignored `research_sandbox/output/`, no broker/order/live-trading paths
touched. The null result was independently verified via a manual rebalance-overlap check
(not committed) before being reported, per this project's fail-closed / verify-don't-
assume standing rule.
