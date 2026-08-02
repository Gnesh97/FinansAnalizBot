# Project Status

Quick snapshot for a newcomer. For the full standing rules, immutables, and architecture
detail, see [CLAUDE.md](CLAUDE.md) — that file is the authoritative, actively-maintained
source; this one is a shorter pointer to today's headline numbers.

## Current phase

- Python US-equities systematic swing-trading **decision-support** framework — not an
  auto-trading bot. No broker/order/live-trading call exists anywhere in the codebase
  (`broker_submit_enabled`, `live_trading_enabled`, `short_trading_enabled` are hardcoded
  `False` and force-reset on every run). The operator enters all broker orders manually.
- Live strategy: `rs_m126_top30_sma100_no_regime` (momentum-126 score, 60-day volatility
  divisor, SMA100 trend filter, Top-30, monthly rebalance, next-day-open execution). Frozen by
  design — no parameter optimization in this project.
- A nightly systemd user timer (01:00 Europe/Istanbul) runs the DSS chain: paced Tiingo
  refresh -> strict validation -> signal generation -> report -> Telegram, with a non-critical
  Risk Engine (Mode A, advisory-only) step.

## Data & universe

- Live data profile: `rs_v2_tiingo` (Tiingo, daily-only, consolidated multi-venue, ~48
  req/hour paced free tier). `rs_v2_sip`/`rs_v2_clean` (Alpaca IEX/SIP) remain on disk as
  frozen history, not the live path.
- Universe: 177 frozen candidate symbols, hash
  `026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933`. QQQ/SPY are
  benchmark-only, excluded from ranking.
- **Survivorship bias is a known, unresolved limitation.** The universe is current-constituent
  only; delisted/acquired/merged securities are not backfilled. An earlier effort to source
  delisted-company price history via Alpaca was investigated and abandoned after finding real
  data-quality problems (sparse coverage, ticker-reuse identity contamination) — see
  `reports/audit/` for the evidence trail. One already-in-universe symbol, `PARA` (Paramount
  Global), delisted 2025-08-07 (Skydance merger); this is a documented, evidence-backed,
  operator-approved `EARLY_TERMINAL` exception (`testdata/baselines/rs_v2_tiingo_coverage_exceptions.json`),
  not a bug.

## Test suite

- 746 tests, GREEN (`python3 testdata/run_test_suite.py`). Two tests in
  `test_rs_v2_clean_data.py` dominate runtime (~170s/~97.5s combined) by design — they verify
  a 358-file manifest with real disk I/O and SHA256 checks at full production scale; kept
  intentionally, not a suite-hygiene gap.

## Safety

- No live trading, no broker submission, no short trading — all hardcoded `False`, enforced on
  every run regardless of config/env attempts to override.
- Durable broker ledger and paper state never change without explicit human review.

## Repository hygiene

- Report output lives under `testdata/research_data/<profile>_market_data/reports/latest` and
  `.../reports/runs/` (gitignored, regenerable). Audit reports (append-only, one new file per
  task) live under `reports/audit/`.
