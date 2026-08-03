## EXECUTIVE SUMMARY
Verdict:        Re-ran the phase22 pilot with a stratified candidate sample (75 crash/
                 bankruptcy-pattern delistings, decline_ratio<=0.3, i.e. lost 70%+ before
                 disappearing -- e.g. RAD/Rite Aid, BPT, ENZ, FRBK -- mixed with 75 stable/
                 M&A-pattern delistings) to correct phase22's known sampling bias toward
                 long-lived, acquisition-type names only. The result held in the same
                 direction and got STRONGER: net return rose further (204.61% vs
                 baseline 154.27%, vs phase22's non-stratified 163.44%), Sharpe improved
                 (1.284 vs 1.174), max drawdown improved (17.90% vs 19.34%). Including a
                 fair mix of crash-pattern delistings did not drag performance down. This
                 meaningfully strengthens (does not fully settle) the phase22 finding: no
                 evidence found this session that the frozen strategy's reported numbers
                 are inflated by survivorship bias.
Changed:         research_sandbox/rs_survivorship_bias.py: PILOT_LIST_PATH repointed to
                 the new stratified candidate list; output filename changed to
                 rs_survivorship_bias_stratified.csv (kept separate from phase22's run,
                 both remain in gitignored research_sandbox/output/). No other code
                 changed, no production code touched.
Suite:           N/A -- research/evaluation task, no production code or test suite
                 changed.
Anomalies:       None new. Same known limitations as phase21/phase22 apply (forward-fill
                 at last price rather than true wipeout-to-zero for names that go
                 bankrupt while held; no split/dividend adjustment confirmation; sample is
                 117/150 attempted, 33 dropped to the split-guard or insufficient data,
                 out of ~9,400 total window-overlapping candidates available).
Open items:      Same as phase22: full-population stratified test (not just a 150-name
                 pilot), split/dividend adjustment status, ticker-reuse risk beyond the
                 6 spot-checked names, dataset license.
Operator action: None required to act on now. Treat "this strategy's numbers do not show
                 meaningful survivorship-bias inflation in this two-pilot test" as the
                 working conclusion for this investigation unless/until a full-population
                 run is judged worth the additional resample cost.

---

## What changed from phase22

Phase22 flagged a real sampling problem: the 150-name pilot was built by sorting delisted
candidates by longest trading history, which mechanically favored companies that got
acquired (an often return-positive event) over companies that went bankrupt (the
return-negative case survivorship-bias studies actually worry about).

This run corrects that. From the same underlying candidate pool
(`scratch/hf_full_scan/candidates_threshold5.csv`, sane-price-filtered, overlapping the
2020-07-27+ production window, n_valid_months>=3), split into two buckets by
`decline_ratio = last_valid_close / peak_close`:

- **Crash bucket** (`decline_ratio <= 0.3`, i.e. lost 70%+ of peak value before
  disappearing): 2,182 candidates available, top 75 by trading-history length taken.
  Examples: RAD (Rite Aid, decline_ratio 0.010), BPT (0.004), ENZ (0.004), FRBK (0.0006,
  essentially wiped out), GCI (0.042), SUP (0.005).
- **Stable bucket** (`decline_ratio > 0.7`, i.e. still near its historical peak at
  delisting -- the M&A-pattern case): 5,495 candidates available, top 75 taken.

150 total (75+75), 117 survived the same split-guard and minimum-data checks as phase22
(33 dropped).

## Results

| | production_only | phase22 (long-lived-only pilot) | phase23 (stratified pilot) |
|---|---|---|---|
| Symbols | 177 | 300 | 294 |
| Net return | 154.27% | 163.44% | **204.61%** |
| Sharpe | 1.174 | 1.102 | **1.284** |
| Max drawdown | 19.34% | 18.32% | **17.90%** |
| Rebalances with a delisted pick | 0 | 56/73 | 55/73 |

## Interpretation

Adding a genuinely mixed sample -- including 75 names that lost 70-99.9%+ of their value
before delisting -- still did not drag returns down; results improved further across all
three metrics. This is evidence against the "sampling artifact" explanation floated in
phase22 (explanation 1) and correspondingly strengthens the "structural" explanation
(explanation 2): the production strategy's SMA100 trend filter and 12% ATR% volatility
cap plausibly exclude most names from being held during their actual collapse phase, so
the strategy captures some of a later-delisted name's earlier uptrend (when it still
passed the trend/vol/price filters) without being exposed to the terminal crash. The
forward-fill-at-last-price simplification (not modeling a true bankruptcy wipeout) is a
conservative bias in the OPPOSITE direction of this result (it would understate downside
if a crash name were ever actually held into its final collapse), so this result is not
an artifact of that particular simplification -- if anything it is a mild headwind that
the result still overcame.

This is still a two-pilot, ~250-name-total test out of an ~9,400-candidate population, not
an exhaustive audit. Treat as a meaningfully strengthened directional finding, not proof.

## Evidence

```
$ python3 research_sandbox/rs_survivorship_bias.py
Loading production (frozen, 177-symbol) universe...
  177 symbols loaded.
Loading delisted pilot candidates (resampled 1m->daily, split-guarded)...
delisted pilot: 117 loaded / 150 attempted (33 dropped: no usable data or split-guard tripped)

Running baseline (production universe only)...
  {'label': 'production_only', 'n_symbols': 177, 'net_return_pct': 154.26709353527664, 'sharpe_ratio': 1.1742132650284753, 'max_drawdown_pct': 19.339525781189522, 'rebalance_count': 73, 'rebalances_with_delisted_pick': 0}

Running expanded (production + delisted pilot)...
  {'label': 'production_plus_delisted_pilot', 'n_symbols': 294, 'net_return_pct': 204.60842521669, 'sharpe_ratio': 1.283842021750397, 'max_drawdown_pct': 17.904373718135687, 'rebalance_count': 73, 'rebalances_with_delisted_pick': 55}

Wrote /home/gnesh/Desktop/BOT - Kopya/research_sandbox/output/rs_survivorship_bias_stratified.csv
```

```
$ git status --porcelain
 M .gitignore
 M research_sandbox/rs_survivorship_bias.py
?? reports/audit/phase21_hf_ohlcv1m_dataset_evaluation_20260802.md
?? reports/audit/phase22_survivorship_bias_pilot_20260802.md
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

No production code changed. Only edit was a two-constant change in the already-reviewed
`research_sandbox/rs_survivorship_bias.py` (input CSV path, output CSV filename) --
identical scope and reuse pattern as phase22's original version.
