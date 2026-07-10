# AGENTS.md

Repository-level instructions for Code Assistant/Codex work in this project.

## Project Identity

- FinansAnalizBot is a US equities research, backtest, local paper-trading, and broker-readiness project.
- It is not production live trading.
- CSV remains the local paper source-of-truth unless explicitly changed.
- SQLite is currently mirror/audit, not source-of-truth.

## Standard ChatGPT / Codex Project Rules

### 1. Default role split

- ChatGPT is planner, quant reviewer, strategy evaluator, and Codex prompt writer.
- Codex is implementer, test runner, and report writer.
- Codex must not silently change project direction.

### 2. Trading/backtest safety

- No live trading.
- No Alpaca POST, submit, cancel, or replace unless prompt explicitly says authorized broker submit.
- Default broker mode is no-broker.
- Broker read-only is allowed only when validation mode says broker-readonly.
- Never mutate durable broker ledger or paper state unless explicitly requested.
- No short trading unless explicitly requested.

### 3. Strategy safety

- Do not change trade logic unless explicitly requested.
- Do not change benchmark logic unless explicitly requested.
- Do not change ranking formulas, signal logic, entry logic, or exit logic unless explicitly requested.
- Do not add new strategies unless explicitly requested.
- Do not optimize parameters unless explicitly requested.
- Prefer small, reversible changes.

### 4. Report/path safety

- Preserve report paths: `testdata/reports/latest` and `testdata/reports/runs`.
- Never write reports under `testdata/testdata/reports`.
- Every ChatGPT-directed task must update `testdata/reports/latest/chatgpt_action_report.md`.
- Action report must state files changed, commands run, test result, whether trade logic, benchmark, strategy metrics, broker behavior, and durable ledger changed, whether report paths were preserved, safety status, and issues.

### 5. Validation modes

- `validation: no-broker`: read-only/local audit/report work; no source code changes unless explicitly allowed; no broker/API calls.
- `validation: local-code-change`: source code changes allowed only within task scope; run tests; no broker/API calls.
- `validation: broker-readonly`: read-only broker/API inspection only; no submit, cancel, replace, or mutation.
- `validation: authorized-broker-submit`: only allowed when prompt explicitly authorizes submit; obey all broker safety checks.

### 6. Testing/performance

- Run `python testdata/run_test_suite.py` when code or report-generation logic changes.
- For report/backtest commands, use maximum safe worker count available on machine.
- Use `--workers 1` only when debugging nondeterminism, cache/race issues, or isolating a failure.
- If results/metrics changed, clearly state why.

### 7. Survivorship / research-only status

- Current universe is static/current-only unless a point-in-time universe is explicitly implemented.
- Current absolute returns, drawdowns, and win rates are research-only.
- Do not present results as live-trading/investable evidence while survivorship bias is unresolved.
- Strategy optimization is locked unless explicitly allowed.

### 8. ECC usage policy

- Do not use all ECC prompts by default.
- For read-only audits: no ECC prompt needed.
- For small code fixes: use `/prompts:python-review` and `/prompts:quality-gate`.
- For test-first/risky accounting/backtest changes: use `/prompts:python-review`, `/prompts:quality-gate`, and `tdd-workflow` skill.
- For roadmap/architecture decisions: use `/prompts:strategic-compact`.
- TDD is invoked as `tdd-workflow` skill, not `/tdd-guide`.

### 9. Legacy code

- Do not import legacy code into production paths unless explicitly requested.
- If touching legacy files, clearly state why.

### 10. If a task is interrupted

- Do not guess completion.
- On resume, first inspect `git status` and latest reports.
- Continue from existing state if safe.
- Do not redo expensive work unnecessarily.

## Compact Prompt Protocol

- **Implicit Execution**: When a prompt states "standard project rules apply", it implies all safety, workflow, path, validation, and reporting rules in this file are in effect. Meticulous repetition of safety rules is unnecessary.
- **Ambiguity Rule**: If the task scope or a parameter is ambiguous, default to the safest, read-only, or report-only execution path.

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
- Broker order ledger source-of-truth must live under testdata/state (e.g., `testdata/state/broker_order_ledger.csv`).
- reports/latest may contain only synced report copies of the broker order ledger.
- Forward-only broker reconciliation must use the durable state ledger.
- Broker reconciliation before cutover may show local/broker position mismatch; in cutover readiness mode this is expected and must be reported as `PRE_CUTOVER` / `UNSYNCED_LOCAL_ONLY`, not treated as a submit blocker by itself.
- BUY for an already-open strategy/symbol must be blocked/non-executable.
- SELL exit must match an existing open long and close existing open shares.
- `REVIEW_REQUIRED`, `HOLD_REVIEW`, and `BLOCKED` rows are not executable accepted pending orders.

## Broker-Forward Paper Protocol

- **Durable Ledger SOT**: The durable broker ledger source of truth resides at `testdata/state/broker_order_ledger.csv`. Files under `testdata/reports/latest/` are secondary report copies.
- **Broker-Forward Reconciliation**: Must use the durable state ledger. Full reconciliation may fail due to historical local-only paper positions (this is expected pre-cutover behavior and must be reported separately from forward-only check results).
- **Separation of States**: Do not backfill or mirror local historical paper positions to Alpaca. Keep local paper state and broker paper state segregated.
- **Machine-Generated SOT**: All report fields (`client_order_id`, `broker_order_id`, `filled_qty`, `status`) must be loaded from machine sources (durable ledger, broker GET response, or generated JSON artifacts).
- **Hallucination Protection**: Manual prose reports must never override machine artifacts. Any mismatch between prose reports and machine state files must be reported as an Issue.

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

## Validation Profiles

When validation profiles are referenced, execute exactly the specified actions:

- **`validation: local-code-change`**
  Run the test suite: `python testdata/run_test_suite.py`. Note: Documentation-only changes do not require running the test suite unless code is modified.
- **`validation: report-only`**
  Run only the safe local report commands relevant to the task (e.g., ops/risk dashboards, closed trade reports).
- **`validation: broker-readonly`**
  GET-only broker reads are allowed if credentials/network are configured. No POST, no order submits, no cancellations, no replacements.
- **`validation: no-broker`**
  Strict local-only execution; do not contact the Alpaca broker under any circumstances.

## Required Final Report

Every task must generate or update the following report copy:

```text
testdata/reports/latest/chatgpt_action_report.md
```

Report structure:

```text
# ChatGPT Action Report

## Task
[Task name/purpose]

## Changes
[List modified files or explain "No source code changes."]

## Safety
[Confirm observed safety properties. Enumerate specific flags:
- submit_order_called=false/true
- post_request_used=false/true
- cancel_replace_used=false/true
- broker_submit_enabled=false/true
- live_trading_enabled=false/true
- short_trading_enabled=false/true
If any safety value is unknown, write UNKNOWN and list under Issues.]

## What Changed / What Did Not Change
[Explicitly specify whether the following were modified (yes/no):
- Trade logic changed?
- Benchmark changed?
- Strategy results changed?
- Benchmark results changed?
- Broker behaviour changed?
- Durable ledger changed?
- Report path preserved?]

## Tests
[Commands run and exact results, including pass count and failure count.]

## Issues
[Detail any issues, mismatches, or state discrepancies. Do not write "None" unless fully verified.]
```

## Research Source Usage

- **Conceptual References**: Systematic/quantitative trading reference books uploaded to the session may be used as conceptual context for system design, planning, and code review.
- **Passage Restrictions**: Do not copy long passages or templates verbatim from books.
- **Scope**: Apply reference books to analyze systematic trading principles such as transaction costs, slippage, survivorship bias, walk-forward diagnostics, Monte Carlo simulations, robustness, position sizing, execution monitoring, and risk gates.
- **Advisory Status**: Book-derived ideas are strictly advisory. Do not implement strategy logic changes or new features based on them unless explicitly requested.
- **Test-First**: Always construct validations and unit tests prior to adopting or implementing new strategy patterns.

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
