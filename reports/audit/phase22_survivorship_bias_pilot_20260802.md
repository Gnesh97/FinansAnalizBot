## EXECUTIVE SUMMARY
Verdict:        A 150-name pilot (123 usable after split-guard) of historically-delisted
                 symbols, added to the frozen production universe and re-run through the
                 unchanged production selection logic, produced a slightly HIGHER net
                 return (163.44% vs 154.27%) and a slightly lower Sharpe/max-drawdown than
                 the production-only universe -- i.e. this pilot did NOT show the classic
                 "survivorship bias inflates returns" pattern. This result carries a
                 material sampling caveat (see Anomalies) and should be read as a
                 first-pass signal, not a settled answer.
Changed:         New file research_sandbox/rs_survivorship_bias.py (reuses the frozen
                 production selection logic read-only, same pattern as the existing
                 rs_rotation_sensitivity.py / rs_factor_combination.py sandbox scripts).
                 No production code touched. Output written to research_sandbox/output/
                 (gitignored).
Suite:           N/A -- research/evaluation task, no production code or test suite
                 changed.
Anomalies:       The pilot candidate list was sorted by n_valid_months (longest trading
                 history first) to keep the resample workload tractable, which
                 systematically favors long-lived companies that were later acquired at a
                 premium (an often return-POSITIVE event) over short-lived names that went
                 to zero in bankruptcy (the classically return-NEGATIVE case survivorship
                 bias studies worry about). This likely biases this specific pilot's result
                 toward looking better than a properly-stratified sample would. Separately,
                 delisted names are forward-filled at their last known price once they
                 leave the feed rather than modeled as a hard wipeout to zero (documented
                 limitation from phase21), which independently understates the downside of
                 any bankruptcy-type name that IS in the sample.
Open items:      A corrected pilot stratified to include short-lived/bankruptcy-pattern
                 delistings (not just long-lived ones) would be needed to actually settle
                 the direction of the bias for this strategy; the project's own trend/vol
                 filters (SMA100, ATR%<=12, $10 minimum) plausibly limit downside exposure
                 to crashing names structurally, which is itself a real, if untested,
                 explanation for why this pilot didn't show the expected pattern. Split/
                 dividend adjustment, ticker-reuse risk beyond the phase21 spot-checks, and
                 dataset license remain unconfirmed per phase21.
Operator action: Decide whether this pilot-level signal is sufficient to close the
                 survivorship-bias investigation for now, or whether a stratified re-run
                 (weighted toward shorter-lived, bankruptcy-pattern names) is worth the
                 additional resample work before treating "our numbers aren't meaningfully
                 survivorship-biased" as a conclusion.

---

## Method

`research_sandbox/rs_survivorship_bias.py` runs the frozen production selection logic
(`testdata.strategies.relative_strength_rotation._relative_strength_selection`, unchanged,
same params as `rs_m126_top30_sma100_no_regime`) twice, over the same 2020-07-27+ window:

1. **Baseline**: production's 177 frozen symbols only (today's Tiingo daily CSVs).
2. **Expanded**: the same 177 symbols plus a 150-candidate pilot of historically-delisted
   names, sourced from the `mito0o852/OHLCV-1m` HF dataset evaluated in phase21, resampled
   from 1-minute to daily bars and passed through a per-ticker split-guard (drop if any
   day-over-day close ratio falls outside [0.5, 2.0] anywhere in its history -- the source
   has no adjustment info, so an un-caught split would otherwise show as a fake cliff).
   123/150 pilot candidates survived the guard and had usable data; 27 were dropped.

Both runs use vectorbt for the equity-curve simulation (same-day close, no transaction
costs -- this sandbox's existing, documented simplification, not specific to this script).

## Results

| | production_only | production_plus_delisted_pilot |
|---|---|---|
| Symbols | 177 | 300 |
| Net return | 154.27% | 163.44% |
| Sharpe | 1.174 | 1.102 |
| Max drawdown | 19.34% | 18.32% |
| Rebalances | 73 | 73 |
| Rebalances that picked a delisted name | 0 (n/a) | 56 / 73 |

The delisted pilot names were actually selected into the Top-30 at 56 of 73 monthly
rebalances -- they were not a rarely-touched tail case, they materially participated in
the simulated portfolio.

## Interpretation

The naive expectation going in was that adding delisted names back would drag returns
down (the standard survivorship-bias story: today's universe implicitly excludes the
companies that failed). That did not happen here. Two candidate explanations, not yet
distinguished:

1. **Sampling artifact (likely, at least partially)**: this pilot was built by sorting
   confirmed-delisted candidates by longest trading history to keep the resample workload
   small (see phase21). That selection mechanically favors mature companies that got
   acquired -- GCI, IPG, IGT, and others in the pilot are 2025 M&A/restructuring cases,
   not bankruptcies -- and under-samples the short-lived, crashed-to-zero names that would
   be expected to hurt returns most. A stratified re-run including more bankruptcy-pattern
   short-lived names would be a real test of whether this holds.
2. **Structural (plausible, untested)**: the production strategy already carries an
   SMA100 trend filter and a 12% ATR% volatility cap on top of the $10 minimum price. A
   stock actively crashing toward delisting typically breaks its trend filter and/or
   exceeds the volatility cap well before the delisting itself, so the strategy's existing
   risk controls may already exclude most of the "still holding it into the crash" failure
   mode that classic survivorship-bias studies assume. If true, this would mean the
   project's specific strategy design is less exposed to survivorship bias than a generic
   buy-and-hold or unfiltered momentum strategy would be -- but this has not been isolated
   from explanation (1) in this pilot.

Given the operator's stated preference to keep this investigation lean, this report treats
the result as a directional first pass rather than pushing immediately into a stratified
second pilot. The most honest summary: **no evidence this session that the project's
frozen backtest numbers are meaningfully survivorship-biased upward, but the sample used
to check that is itself biased toward the type of delisting least likely to show the
effect, so this is not a strong disconfirmation either.**

## Evidence

```
$ python3 research_sandbox/rs_survivorship_bias.py
Loading production (frozen, 177-symbol) universe...
  177 symbols loaded.
Loading delisted pilot candidates (resampled 1m->daily, split-guarded)...
delisted pilot: 123 loaded / 150 attempted (27 dropped: no usable data or split-guard tripped)

Running baseline (production universe only)...
  {'label': 'production_only', 'n_symbols': 177, 'net_return_pct': 154.26709353527664, 'sharpe_ratio': 1.1742132650284753, 'max_drawdown_pct': 19.339525781189522, 'rebalance_count': 73, 'rebalances_with_delisted_pick': 0}

Running expanded (production + delisted pilot)...
  {'label': 'production_plus_delisted_pilot', 'n_symbols': 300, 'net_return_pct': 163.44054750544856, 'sharpe_ratio': 1.1020405737360521, 'max_drawdown_pct': 18.319547640372903, 'rebalance_count': 73, 'rebalances_with_delisted_pick': 56}

Wrote /home/gnesh/Desktop/BOT - Kopya/research_sandbox/output/rs_survivorship_bias.csv
```

```
$ git status --porcelain
 M .gitignore
?? reports/audit/phase21_hf_ohlcv1m_dataset_evaluation_20260802.md
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

No production code changed. `research_sandbox/rs_survivorship_bias.py` follows the exact
reuse pattern already established and reviewed for `rs_rotation_sensitivity.py` /
`rs_factor_combination.py` (frozen selection logic imported read-only, no parameter
changes, output confined to gitignored `research_sandbox/output/`). Self-review: no
secrets, no network calls, no writes outside `research_sandbox/output/`, no broker/order/
live-trading paths touched, matches `research_sandbox/README.md`'s stated scope (not
audit-grade, not a parameter-optimization pipeline).
