# Project Status

## Current phase

- Python US-equities swing trading research/backtest framework.
- Not live-trading ready.
- Current results are research-only.

## Completed reliability work

- Fill-time transaction-cost accounting implemented and validated.
- Opt-in signal-date dynamic ADV liquidity filtering implemented for active modular engines.
- Liquidity reporting terminology clarified.
- OOS/walk-forward reporting integrated into full reports.
- Missing Dual Momentum and MA Slope candidates added to OOS coverage.
- Survivorship/research-only warning banners added to performance reports.
- Full suite last verified at 275 passing tests.

## Current strategy assessment

- No strategy is currently considered investable.
- `rs_m126_top30_sma100_no_regime` remains conservative baseline for framework validation.
- `rs_m126_top20_sma100_no_regime` currently looks stronger on return/benchmark comparison, but is not investable evidence.
- Strategy optimization is locked while survivorship bias remains unresolved.

## Critical unresolved issue

- Universe is a static/current-only set of approximately 200 active stocks and ETFs.
- Delisted, bankrupt, acquired, merged, and historically removed securities are missing.
- Equal-weight benchmark is also exposed to survivorship bias.
- Current local data cannot fully solve this issue.
- Point-in-time membership alone is not enough without inactive/delisted price and terminal-event data.

## Data-cost policy

- Minimize project spending.
- Do not purchase Norgate, CRSP, Polygon/Massive, or another paid dataset without separate explicit decision.
- Paid intraday data is not currently justified.
- Continue using current dataset only as research-only baseline.
- Prefer free or low-cost proof-of-concept work before any purchase.

## Safety/status

- No live trading.
- Broker submission remains disabled by default.
- No short trading.
- Durable broker ledger and paper state must not change without explicit authorization.

## Recommended next decision

Choose one:

- A. Keep project research-only with current free data.
- B. Build small free/manual point-in-time proof of concept.
- C. Temporarily validate strategies on ETF-only universe.
- D. Defer survivorship correction until affordable data becomes available.

## Repository hygiene

- Working tree may contain pre-existing unrelated changes.
- Reports remain under `testdata/reports/latest` and `testdata/reports/runs`.
