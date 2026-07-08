# AGENTS.md

Repository-level instructions for Code Assistant/Codex work in this project.

## Project Identity

- FinansAnalizBot is a US equities research, backtest, local paper-trading, and broker-readiness project.
- It is not production live trading.
- CSV remains the local paper source-of-truth unless explicitly changed.
- SQLite is currently mirror/audit, not source-of-truth.

## Non-Negotiable Safety Rules

- Do not enable broker submit.
- Do not enable live trading.
- Do not enable short trading.
- Do not call Alpaca POST.
- Do not submit, cancel, replace, or modify broker orders.
- Alpaca snapshot adapter must remain GET-only and disabled by default.
- Execution preview is not order submission.

## Alpaca Paper Submit Rules

- Every broker-submitted order must use deterministic `client_order_id`.
- On submit timeout or unknown result, never resend blindly; first perform broker order readback.
- Broker submit must be max 1 order until explicitly expanded.
- Extended hours must remain false unless explicitly approved.
- Do not use both `qty` and `notional` in one order.
- Check broker account, positions, open orders, asset tradability/fractionability, and buying power before submit.
- Check broker open-order conflicts and possible wash-trade conflicts before submit.

## Trading Logic Protection

- Do not change strategy logic unless explicitly requested.
- Do not change benchmark logic unless explicitly requested.
- Do not change ranking logic unless explicitly requested.
- Do not change entry/exit rules unless explicitly requested.
- Do not change official local paper fill logic unless explicitly requested.

## Paper Workflow Rules

- Do not mutate `paper_trade_log.csv` unless the task explicitly involves official fill workflow.
- Do not rewrite `paper_pending_orders.csv` or `paper_order_review.csv` unless the task explicitly requires regeneration.
- Report-only commands must not mutate state.
- Do not bulk-mirror or backfill historical local CSV paper positions into Alpaca paper.
- Alpaca paper transition must be forward-only from an explicit cutover point.
- Broker reconciliation before cutover may show local/broker position mismatch; in cutover readiness mode this is expected and must be reported as `PRE_CUTOVER` / `UNSYNCED_LOCAL_ONLY`, not treated as a submit blocker by itself.
- BUY for an already-open strategy/symbol must be blocked/non-executable.
- SELL exit must match an existing open long and close existing open shares.
- `REVIEW_REQUIRED`, `HOLD_REVIEW`, and `BLOCKED` rows are not executable accepted pending orders.

## Anti-Hardcoding Rules

- Do not hardcode production dates, sessions, tickers, returns, benchmark values, run IDs, paths, broker IDs, account IDs, signal dates, fill dates, or one-off values.
- Derive values from config, report metadata, market/session helpers, current CSV/SQLite state, or CLI arguments.
- Test fixtures may hardcode small synthetic values only if clearly labeled as fixtures.

## Report Path Rules

- Preserve existing report paths.
- Main reports live under `testdata/reports/latest` and `testdata/reports/runs`.
- Do not create `testdata/testdata/reports`.
- Do not commit generated reports unless explicitly requested.

## Validation Rules

Default validation for code changes:

```powershell
python testdata/run_test_suite.py
```

For paper/report changes, also run the relevant commands:

```powershell
python universe_scanner.py --paper-decision-inbox
python universe_scanner.py --paper-risk-dashboard
python universe_scanner.py --paper-ops-dashboard
python universe_scanner.py --intraday-paper-monitor
python universe_scanner.py --closed-trade-report
python universe_scanner.py --no-lookahead-audit
python universe_scanner.py --execution-cost-preview
python universe_scanner.py --sqlite-state-reconciliation
python universe_scanner.py --broker-reconciliation
python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report
```

## Required Final Report

Every task must write:

```text
testdata/reports/latest/chatgpt_action_report.md
```

Report sections:

```text
Task:
Changed:
Tests:
Safety:
State Mutation Check:
Metrics:
Issues:
Next:
```

## Current Operational Notes

- Pre-submit BLOCK is expected while broker submit is disabled.
- Broker reconciliation WARNING is expected if no broker snapshot is configured.
- Alpaca GET-only snapshot NOT_AVAILABLE/WARNING is expected without usable paper credentials/network.
- No-lookahead WARNING is acceptable for waiting_for_next_open pending rows.
- Historical closed-trade `unmatched_sell_count` may remain from old rows, but future SELL exits must not increase it.

## Communication Rules

- Default mode: compact.
- Normal task replies should be at most 12 lines unless the user asks for a detailed report or validation fails.
- Report changed files, commands run, safety result, metric changes, and next action.
- Do not repeat the prompt or write long background explanations unless needed for a critical risk.
