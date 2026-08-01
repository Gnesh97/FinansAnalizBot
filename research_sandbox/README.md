# Research Sandbox

Exploratory, non-authoritative research tooling. Separate on purpose from `testdata/`
(the production decision-support path) and from `scratch/` (throwaway, gitignored).

## What this is

A place to explore "what if" questions about the strategy's own parameters --
momentum lookback, rebalance frequency, alternative factors -- **without** touching the
frozen production strategy (`rs_m126_top30_sma100_no_regime`) or its selection-fingerprint
invariant. Nothing here is imported by `testdata/` or the nightly DSS chain, and nothing
here can change what the live paper book does.

## What this is not

- Not a parameter-optimization pipeline for the live strategy. The project's standing rule
  ("no parameter optimization, by design") applies to the *shipped* strategy; this sandbox
  exists to study alternatives, not to feed a winner back into production automatically.
  Any production change stays a deliberate, human-approved, separately-reviewed act.
- Not audit-grade. Results here use simplified execution (same-day close, no transaction
  costs, no liquidity filter, no next-day-open lag) for speed. They are directional evidence
  for research conversations, not a substitute for the production backtest engine's
  next-day-open, no-lookahead, cost-modeled simulation.
- Not covered by the project's fidelity-gate discipline (reproduce-the-frozen-baseline-first).
  It deliberately reuses the production strategy's own scoring/selection functions
  (`testdata.strategies.relative_strength_rotation`) read-only, for signal fidelity, but the
  portfolio simulation layer is its own (vectorbt), not the production backtest engine.

## Dependencies

`vectorbt` (added to `requirements.txt`, used only here -- not imported by any `testdata/`
module). Reuses the project's existing Tiingo daily data
(`testdata/research_data/rs_v2_tiingo_market_data/daily/`) read-only via
`testdata.data_engine.read_ohlcv_csv_fast`, the same CSV parser production uses.

## Contents

- `rs_rotation_sensitivity.py` -- sweeps momentum lookback and rebalance frequency for the
  relative-strength rotation strategy, reusing the real selection logic, simulating each
  combination's equity curve with vectorbt, and writing a comparison table + heatmap to
  `output/`.

## Output

`output/` is gitignored -- regenerable, not archived history.
