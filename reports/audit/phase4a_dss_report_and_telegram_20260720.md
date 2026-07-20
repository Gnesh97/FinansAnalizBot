# Phase 4A — Daily DSS BUY/SELL Report + Telegram (manual-trigger only)

**Date:** 2026-07-20
**Scope:** report-generation + Telegram send only. No scheduler/cron/Task Scheduler wiring (explicitly a separate later task). Manual-trigger only, so the format can be verified before automating.

This report does not conclude GO/NO-GO.

---

## What was built

### `testdata/dss_report.py` (new)

A read-only decision-support report generator. It never recomputes ranking/selection — it reads the strategy output the existing, validated pipeline already produces (`paper_signal_report.csv`, written by `universe_scanner.py --paper-report`) and formats it.

- `generate_dss_report(data_folder, as_of=None) -> DSSReport` — the entry point.
- `render_dss_report_text(report) -> str` — plain-text rendering (header banner, BUY list, SELL list, no-change-day note, off-universe anomaly note).
- `DSSDataNotReadyError` — the fail-loud exception type; every failure path raises this, nothing else.

### `bot.py` (modified)

- New `--mode dss-report` and `--print-only` CLI flags.
- New `run_dss_report(args)`: calls `generate_dss_report`, renders the text, writes it to a new timestamped file under `reports/dss/` (never overwritten — each run gets its own `dss_report_<UTC timestamp>.md`), then either prints it (`--print-only`) or sends it to Telegram.
- New `send_dss_report_message(text, chat_id)`: a thin, intentionally minimal wrapper around the pre-existing `send_telegram_message` — kept small specifically so tests can mock it and assert exactly what it was called with.
- A module-level safety assertion block right above this new code:
  ```python
  assert BROKER_SUBMIT_ENABLED is False
  assert LIVE_TRADING_ENABLED is False
  assert SHORT_TRADING_ENABLED is False
  ```
  These flags are also unconditionally forced to `False` in the pre-existing `load_config()` regardless of what `config.json`/env vars try to set (confirmed during code review — a real, non-bypassable guard, not just a docstring claim).

No broker/order/live-trading call exists anywhere in this new code — confirmed by grep and by the code review (see below).

## Fail-loud paths

`generate_dss_report` raises `DSSDataNotReadyError` (never returns a partial/blank report) when:

1. The dataset fails strict validation — reuses `runner.validate_v2_clean_dataset` unmodified (the same validator the rest of the pipeline already trusts), not reimplemented.
2. The dataset is stale — `manifest.target_end_date` doesn't match the latest NYSE session that should already be complete (computed via the existing `approved_nyse_schedule`, the same freshness bar `--refresh-daily` itself uses).
3. The manifest/pointer files can't be read or parsed.
4. `paper_signal_report.csv` is missing, empty, unparseable, or missing required columns.
5. There are no rows for the main strategy (`rs_m126_top30_sma100_no_regime`) — meaning the strategy step likely errored.
6. The frozen universe file itself can't be loaded.

`bot.py`'s `run_dss_report` catches any exception from report generation and turns it into `"DSS did not run today: <reason>"`, written to the artifact file and (unless `--print-only`) sent to Telegram — never a silent no-send.

Symbols in the signal file that fall outside the frozen 177-symbol universe (plus QQQ/SPY, which are benchmark-only and explicitly kept) are excluded from the BUY/SELL lists but never silently dropped without a trace — `off_universe_dropped` is always populated and always rendered as an explicit NOTE line in the report text.

## Credential handling

Reuses the existing `.env`-based mechanism already in `bot.py` — `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, loaded once at module import via the pre-existing `load_dotenv(ENV_PATH)`. No new config file was introduced, so no new `.gitignore` entry was needed (`.env` and `.env.*` were already gitignored before this task).

If either credential is empty, `run_dss_report` checks `not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID` on **both** the success and failure branches before ever calling `send_dss_report_message` — prints a clear local error and returns, never sends, never crashes. `send_telegram_message` itself also independently returns `False` if `target_chat_id` is falsy, as a second layer.

## Code review

Ran the mandatory review (this is a live-output component). Findings and what was done:

- **MEDIUM** — `_assert_dataset_ready`'s manifest/pointer read and `generate_dss_report`'s `load_v2_frozen_universe()` call had no local error handling of their own; the fail-loud contract was only enforced because `bot.py`'s caller happens to wrap everything in a broad `except Exception`. **Fixed**: both are now wrapped in their own `try/except`, raising `DSSDataNotReadyError` directly, so the module enforces its own documented contract regardless of caller behavior.
- **MEDIUM** — test coverage gaps: no test for CSV-parse failure, missing-columns, unreadable-manifest-pointer, or the QQQ/SPY off-universe carve-out surviving the filter. **Fixed**: added `test_missing_columns_raises_not_ready`, `test_unparseable_csv_raises_not_ready`, `test_unreadable_manifest_pointer_raises_not_ready`, `test_qqq_and_spy_survive_the_off_universe_filter`.
- **LOW** — `_to_position`'s weight-percent guard (`capital not in (None, 0)`) didn't exclude `NaN`, which compares unequal to both `None` and `0`, so a `NaN` capital could silently render as `target_weight=nan%`. **Fixed**: added an explicit `pd.notna(capital)` check.
- Confirmed correct, no changes needed: broker/trading safety (no such calls anywhere, flags force-False and non-bypassable), credential fail-closed path (can't crash, can't send to a wrong/undefined chat), off-universe filtering (never silently swallowed), reuse discipline (`validate_v2_clean_dataset`, `approved_nyse_schedule`, `load_v2_frozen_universe` all reused as-is, not reimplemented).

## Tests

17 new tests, all mocking network/Telegram calls — **no real Telegram message was sent at any point during development or the test suite**.

`tests/test_dss_report.py` (12 tests):
- Normal rendering: BUY/SELL/reasons render correctly, no-change-day is explicit, off-universe symbols are excluded-and-flagged, QQQ/SPY survive the filter.
- Fail-loud: stale data, failed strict validation, missing/empty/unparseable/missing-columns signal file, no main-strategy rows, unreadable manifest pointer — each raises `DSSDataNotReadyError` with a matching message.

`tests/test_bot_dss_report.py` (5 tests):
- Send function called with exactly `(text, chat_id)` and no broker/order/live-trading kwargs.
- `--print-only` never calls the send function and writes the artifact file.
- Stale/failed data sends the failure notice text (`"DSS did not run today"`), not a normal BUY/SELL report.
- Missing credentials fail closed with no send attempt, on both the success and failure branches.

### Full suite

```
Ran 671 tests in 301.751s
OK
```
Reconciles exactly: 654 (established baseline) + 17 (new DSS tests) = 671.

### git status / git log

```
$ git status --porcelain
(clean)

$ git log --oneline -3
6484a9b feat: add daily DSS BUY/SELL report generator and Telegram send (manual-trigger only)
e1131de docs: Phase 3B reacquisition + baseline rebuild reports
1bd58e6 docs: Phase 3B BK universe retirement audit report and impact script
```

## Manual commands for the operator

**(a) Print-only preview** (safe, no Telegram, no credentials required) — run this first to eyeball the format:
```
python bot.py --mode dss-report --print-only
```
This renders the report to stdout and writes it to `reports/dss/dss_report_<UTC timestamp>.md`. Nothing is sent anywhere.

**(b) Real test send to Telegram** (requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` set in `.env`) — the operator should run this themselves once ready to verify delivery:
```
python bot.py --mode dss-report
```
This was **not** run as part of this task (per instruction) — no real Telegram message was sent by me at any point.

## Not done (explicitly out of scope, per instruction)

- No scheduler/cron/Task Scheduler wiring — a separate later task.
- No `--live` acquisition was run for this task; it reads whatever dataset is already on disk/validated.
- No strategy/ranking/benchmark logic or the frozen 177 universe/hash was touched.

---

## Stop

Per task instructions: no GO/NO-GO conclusion is offered here. Stopping after reporting.
