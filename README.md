# BOT - Kopya

Research, backtest, local paper-trading cockpit, and broker-readiness tooling for a CSV-first trading workflow. This project is not production live-trading software.

## Current Project Status

Research-only; not live-trading ready. Current strategy results are not investable evidence while survivorship bias remains unresolved. See [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Safety Defaults

- Live trading is disabled.
- Broker submit is disabled.
- Short trading is disabled.
- Execution preview is not order submission.
- Alpaca snapshot reconciliation is GET-only and disabled by default.
- Do not enable broker submit, live trading, or short trading without explicit approval and a separate safety review.

## Windows Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy the example environment file and fill only local placeholders:

```powershell
copy .env.example .env
```

Alpaca values are for paper-only GET/readiness checks unless a separate approved submit workflow is used. Telegram values are optional notification placeholders.

## Daily Local Paper Workflow

Use the BAT files from the repository root:

```bat
scripts\run_pending_5min_refresh.bat
scripts\run_daily_paper_pipeline.bat
```

Useful report-only operator views:

```powershell
python universe_scanner.py --paper-decision-inbox
python universe_scanner.py --paper-risk-dashboard
python universe_scanner.py --paper-ops-dashboard
python universe_scanner.py --intraday-paper-monitor
python universe_scanner.py --execution-preview --pre-submit-check --make-chatgpt-report
```

## Roadmap Reports

```powershell
python universe_scanner.py --closed-trade-report
python universe_scanner.py --no-lookahead-audit
python universe_scanner.py --execution-cost-preview
python universe_scanner.py --sqlite-state-reconciliation
python universe_scanner.py --broker-reconciliation
python universe_scanner.py --broker-reconciliation --alpaca-paper-snapshot-readonly
```

Default broker reconciliation does not call Alpaca. The explicit Alpaca snapshot flag is GET-only; if credentials or network are unavailable it reports WARNING/NOT_AVAILABLE rather than treating the broker account as empty.

## Expected Warnings

- Broker snapshot unavailable: expected when no broker snapshot is configured or Alpaca credentials/network are unavailable.
- Pre-submit BLOCK: expected until every local safety gate is deliberately cleared.
- SQLite paper_order_review mirror warning: CSV remains source of truth.
- No-lookahead pending next-open warning: expected for pending rows waiting on next valid open data.

Warnings are operator prompts, not automatic permission to submit orders.

## Report Paths

- Latest handoff artifacts: `testdata/reports/latest`
- Timestamped run artifacts: `testdata/reports/runs`
- Main ChatGPT handoff: `testdata/reports/latest/chatgpt_master_report.md`
- Current action summary: `testdata/reports/latest/chatgpt_action_report.md`

## Do Not Commit

- `.env` or any secrets.
- Generated report output files unless intentionally versioned.
- Local market data, state databases, backups, scratch files, or cache files.
- Real broker IDs, account IDs, tokens, API keys, or one-off production values.

## Tests

```powershell
python testdata\run_test_suite.py
```

The test suite is local and uses temp files/mocks for broker safety coverage. It should not require Alpaca credentials and should not submit broker orders.
