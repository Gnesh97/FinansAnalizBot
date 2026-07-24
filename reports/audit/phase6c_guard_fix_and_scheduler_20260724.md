# Phase 6C — Guard Scope Fix, Clean DSS Report, and Nightly Scheduler

**Date:** 2026-07-24
**Scope:** Scope the always-firing daily/130min guard; re-render the DSS report clean; explain the ramp-in cap; commit Phase 6B; wire the nightly scheduler. Completes the DSS chain.

---

## 1. Guard Scope Fix (a correction, not a loosening)

**Problem.** `runner._block_new_signal_generation_on_daily_mismatch` compares the latest session in `daily/` against the latest in `bars_130min/` and blocks new actionable signals when they disagree. Daily-only profiles ship no `bars_130min/` directory at all, so `_latest_csv_data_date` returned `"none"`, which never equals a real date — the guard fired on every run of `rs_v2_tiingo`, permanently tagging every signal `HOLD_REVIEW` / `paper_actionable=False`. An alarm that always fires carries no information and masks the real condition it exists to catch.

**Fix** (`testdata/runner.py`, 6 added lines, guard body otherwise untouched):

```python
if not Path(intraday_dir).exists():
    return paper_report
```

An **absent** intraday directory is the profile's shape, not a staleness signal. A **present** intraday directory is still compared exactly as before — including the present-but-empty case, which still blocks. Nothing was deleted, no threshold moved, no condition relaxed.

**Both directions proven** — `tests/test_daily_only_mismatch_guard_scope.py`, 4 tests, all passing (the guard had no test coverage at all before this):

| Direction | Test | Result |
|---|---|---|
| Daily-only, no `bars_130min` → must NOT fire | `test_absent_intraday_dir_leaves_signals_actionable` | `paper_actionable` stays True, no marker, `user_decision` untouched |
| Intraday present, daily one session ahead → must STILL fire | `test_genuine_daily_130min_mismatch_still_blocks` | `paper_actionable=False`, marker set, `HOLD_REVIEW`, `fill_status=NOT_APPLICABLE` |
| Intraday present but empty → must STILL fire | `test_present_but_empty_intraday_dir_still_blocks` | blocked, marker set |
| Intraday present and in sync → must not fire | `test_matching_feeds_do_not_block` | untouched (pre-existing behavior pinned) |

## 2. Clean DSS Report

Freshness check re-run first, unmodified, to confirm it still fail-louds:

```
$ python3 bot.py --mode dss-report --print-only --dss-data-profile rs_v2_tiingo
==================================================
📋 [MODE] Mode: dss-report
==================================================
DSS did not run today: data is stale: manifest target_end_date='2026-07-21', expected latest completed session='2026-07-24'
```

Rendered at the as-of date the validated data supports (same route as Phase 6B — the module's own public `as_of` parameter, check unmodified):

```
DECISION SUPPORT -- enter orders manually at next open
As-of data date: 2026-07-21
Universe: 177 symbols (hash 026f66d1...)
Strategy: rs_m126_top30_sma100_no_regime_v2_clean_stock
Generated at: 2026-07-24T17:31:24.469527Z

BUY (5):
  AMD: price=544.43, target_weight=3.33%, rank=5, score=1.5375256922745022, reason=initial_paper_state;latest_entry_signal;missing_next_open
  FTNT: price=158.10, target_weight=3.33%, rank=2, score=1.9595193328054803, reason=initial_paper_state;latest_entry_signal;missing_next_open
  MPC: price=319.76, target_weight=3.33%, rank=1, score=2.3708165727497987, reason=initial_paper_state;latest_entry_signal;missing_next_open
  PSX: price=212.27, target_weight=3.33%, rank=4, score=1.6582056440123645, reason=initial_paper_state;latest_entry_signal;missing_next_open
  VLO: price=314.80, target_weight=3.33%, rank=3, score=1.8746857525177387, reason=initial_paper_state;latest_entry_signal;missing_next_open

SELL: none today.
```

**Confirmed identical to Phase 6B apart from the removed token.** Same 5 symbols, same prices to the cent, same ranks, same scores to the last digit, same 3.33% weights, same empty SELL list. The only delta is the removal of `;new_signal_generation_blocked_by_daily_130min_mismatch` from all five reason strings. Underlying CSV now shows `paper_actionable` True on all 5 BUY rows (was False) and the marker absent everywhere.

`user_decision` remains `HOLD_REVIEW`, which is **not** guard residue — it is the constructor default at `runner.py:3872`, the normal "operator has not decided yet" state for decision support.

## 3. Ramp-In Cap — Explanation Only (nothing changed)

**Where configured:** `universe_scanner.py` `--paper-max-new-positions-per-day`, `default=5`, passed through `run_strategy_experiments` into `runner._run_paper_signal_report`. Applied at `runner.py:3968-3976`.

**What it does:** among main-strategy BUY rows only, sorts by `ranking_score` descending; rank ≤ 5 stays `BUY` with `paper_actionable=True`; rank > 5 is rewritten to `action="DEFER"` with `deferred_due_to_ramp_in=True` and `;ramp_in_limit` appended to the reason. SELL rows are never capped — exits always stay actionable.

**Deliberate design, not a bootstrap artifact.** It is an explicit CLI parameter with a documented default, and it caps *entries only* while leaving exits unthrottled — a risk-control shape, not an initialization side effect. What made today's report look extreme is the interaction with a cold start: the paper ledger holds no positions, so all 30 top-ranked names are "new", and 25 of them defer at once. That 5/25 split is the cold-start consequence of a standing rule, not a separate bootstrap rule.

**Subsequent days:** each run re-reads the paper ledger. Symbols already held are not re-emitted as BUY (`already_in_position` short-circuits at `runner.py:3829`), so the next run's top-ranked *unheld* names fill the next 5 slots. A 30-name book therefore fills over roughly 6 trading days, assuming the top-30 set is stable; churn in the ranking extends it.

**Backtest baseline does NOT use it.** The cap lives entirely in `_run_paper_signal_report` (the paper/DSS path). The frozen backtest runs through `_run_strategy` / `relative_strength_rotation`, which takes `rs_top_n=30` and has no per-day entry limit — it enters the full top-30 at each monthly rebalance. So the 165.9133%-lineage baseline figures are unaffected by this cap; it is **DSS/paper-only**.

**Consequence worth the operator's attention:** because the backtest fills the book instantly and the paper/DSS path fills it over ~6 days, live paper results will diverge from the backtest during any ramp-in period. Not changed, per instruction — flagged for your decision.

## 4. Nightly Scheduler

New module `testdata/dss_scheduler.py` plus launchers `scripts/run_dss_daily.bat` (Windows) and `scripts/run_dss_daily.sh` (cron).

**Chain:** paced Tiingo `--refresh-daily` (179 items; strict validation runs inside finalize) → paper signal generation → DSS report → Telegram send. Implemented as subprocess calls to the three existing CLIs, so there is exactly one source of truth for each step.

**No order path — structurally, not just by convention.** `_chain_steps` is a fixed list containing only `vericekici_updated.py`, `universe_scanner.py`, and `bot.py --mode dss-report`. `ORDER_PLACEMENT_ENABLED` and `BROKER_SUBMIT_ENABLED` are module constants pinned False. Two tests assert the invoked script set exactly, and that no argument anywhere in the chain contains `submit`, `order`, `broker`, `live`, `execute`, `buy`, or `sell`.

**Timing.** Trigger daily at **23:30 Turkey time** = 16:30 ET, 30 minutes after the US close, giving Tiingo time to publish the completed session (the Phase 6B finding: a refresh started before publication fails coverage validation on the not-yet-published session — correctly, but pointlessly). The ~3.8h paced refresh finishes around **03:20 TR**, roughly 13 hours before the next US open at 16:30 TR.

**Trading days from the approved calendar, never a hardcoded list.** `is_trading_day` queries `approved_nyse_schedule` over a trailing 10-day window and tests membership. The window matters: `approved_nyse_schedule` fail-closes with `V2_MARKET_CALENDAR_EMPTY` on an empty range, so a single-day weekend query would *raise* where the honest answer is "not a session". Querying a window means a genuinely broken calendar still raises loudly instead of being swallowed. Weekends and holidays are both covered by test (`2026-07-25` Saturday, `2026-01-01` New Year).

**Fail-loud on every step.** Any non-zero exit, timeout, or DSS-level failure writes `status: FAILED` to the state file and sends `DSS did not run: <reason>` to Telegram. Never a silent no-send, never a blank BUY list. Two subtleties handled:
- `bot.py` reports DSS problems on stdout and exits 0 after sending its *own* notice. The scheduler detects the `DSS did not run` marker in stdout, records the failure, and suppresses its own send — so a stale-data night produces exactly one message, not two.
- If Telegram is unconfigured, the notice fails **closed locally** (printed, not sent to a wrong/missing chat) rather than pretending success.

**Idempotent and resumable.** State lives in `reports/dss/scheduler_state.json` (`last_target_session`, `status`, `completed_at`, `failed_step`, `last_error`). A session already recorded `SENT` is neither re-run nor re-sent. A session recorded `FAILED` is retried on the next invocation. Corrupt or missing state degrades to "not yet reported" rather than crashing. Mid-refresh interruption is absorbed one layer down by the provider's own per-symbol resume state, so a resumed run re-fetches only what it must.

### What was verified live vs. what remains for the first real night

**Verified live this session** (`--skip-refresh --no-send --report-print-only`, scratch state path, zero Telegram traffic, zero API calls):
- Real subprocess invocation of the signal-generation step.
- A real step failure was detected, propagated, and written to state as `FAILED` with `failed_step: "signals"` and the full error text — the fail-loud path firing on a genuine, unplanned failure rather than a synthetic one.
- Trading-day gating, idempotent skip, state round-trip, no-double-send, and token redaction are covered by 17 unit tests.

**The failure it caught, explained honestly.** The signals step failed with `conflicting duplicate event id: 1bfb61a4…` from `baseline_parity.append_decision_event`. Root cause, established from the stored artifact rather than inferred: the archived event's content hash includes `source_commit_hash`, and the stored record carries `a49e1f96…` (Phase 6A HEAD) because it was written before this turn's Phase 6B commit `8687580`. Re-running the same session's chain after committing code produces an identical *decision identity* with a different *content hash*, and the archive fail-closes on exactly that. This is the archive working as designed — it refuses to let two different code versions silently claim the same decision. It is not a scheduler defect, not caused by the guard fix, and was deliberately **not** worked around: the archived events were left untouched. On a normal nightly cadence (one run per session, no mid-session commits) it does not arise.

**Remains to be verified on the first real nightly run:** the full 179-symbol paced refresh under the scheduler's 6h step timeout; a real Telegram delivery (success and failure); and end-to-end behavior on a session whose decisions have not already been archived.

### What the operator must do to enable it

**Windows (Task Scheduler):**
1. Open Task Scheduler → **Create Task** (not "Basic Task").
2. *General*: give it a name; select **Run whether user is logged on or not**; check **Run with highest privileges** only if your Python install requires it.
3. *Triggers*: **New → Daily**, start time **23:30**, recur every 1 day. Add no weekday filtering — the script self-gates on NYSE sessions, and a weekday trigger would only add a second, drift-prone source of truth.
4. *Actions*: **Start a program** → Program: `C:\...\BOT - Kopya\scripts\run_dss_daily.bat` → Start in: `C:\...\BOT - Kopya`.
5. *Conditions*: uncheck **Start the task only if the computer is on AC power** if this runs on a laptop; check **Wake the computer to run this task**.
6. *Settings*: check **Run task as soon as possible after a scheduled start is missed** (a missed night then runs on wake; the state file prevents a double-send). Set **Stop the task if it runs longer than** to at least **6 hours**.
7. **Confirm `python` resolves for the account the task runs as.** A service account often has a different PATH; if `run_dss_daily.bat` exits with code 9009, edit the `python` invocation in that file to the absolute interpreter path. This is the one failure mode that occurs *before* the scheduler starts, so it produces no Telegram notice — verify it once by running the .bat manually from a plain `cmd` window.
8. Verify `.env` contains `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (the scheduler loads `.env` itself; a bare Task Scheduler environment is expected and handled).

**Linux (cron)**, system clock on Europe/Istanbul:
```
30 23 * * * /path/to/BOT\ -\ Kopya/scripts/run_dss_daily.sh >> /path/to/BOT\ -\ Kopya/reports/dss/scheduler.log 2>&1
```

**Manual invocations:**
```
python3 testdata/dss_scheduler.py                        # full nightly chain
python3 testdata/dss_scheduler.py --skip-refresh         # signals + report against data on disk
python3 testdata/dss_scheduler.py --report-print-only    # render without dispatching (verification)
python3 testdata/dss_scheduler.py --force                # ignore trading-day and already-reported gates
```

## 5. Commits

`8687580` — `feat(data): rs_v2_tiingo daily refresh with drift telemetry, DSS wiring, daily-only guard scope` (Phase 6B refresh implementation, guards, tests, DSS profile wiring, this turn's guard fix + its tests, Phase 6B report). `scratch/` and `research_data/` excluded.

A second commit carries the scheduler module, both launchers, the scheduler tests, and this report.

## 6. Test Suite & Review

```
Ran 721 tests in 468.396s
OK
```
Reconciliation: 702 (Phase 6B final) + 4 (guard scope) + 15 (scheduler, at suite time) = 721, zero failures. Two further scheduler tests were added after the suite run in response to code review (env loading, token redaction), bringing the module to 17 and the true total to **723**; module re-run passes.

**Code review** (`/code-review`, ECC) — one CONFIRMED defect, fixed:
- **CONFIRMED, fixed:** `send_failure_notice` read Telegram credentials from `os.environ`, but they live in `.env`. Task Scheduler and cron start with a bare environment, so *every* nightly failure notice would have fail-closed locally and never reached the operator — silently defeating the entire fail-loud requirement. The scheduler now loads `.env` itself (`_load_project_env`, `setdefault` semantics so a real environment still wins). Pinned by `test_failure_notice_loads_env_so_cron_can_deliver`.
- **PLAUSIBLE, fixed:** delivery-error text was printed unsanitized and could echo the bot token into `scheduler.log`. Now redacted; pinned by `test_delivery_error_text_never_leaks_the_bot_token`. Given this project already had to rotate one leaked credential, hardening here was warranted rather than deferred.
- **Noted, not code-fixed:** `run_dss_daily.bat` invokes bare `python`, which may not resolve under a Task Scheduler service account. Addressed in the install steps above (step 7) rather than by hardcoding an interpreter path.

## 7. Hard-Rule Compliance

- No validator or freshness check weakened. The guard was **scoped**, and both directions are test-pinned; the freshness check was re-run unmodified and still fail-louds (Section 2).
- Strategy/ranking/benchmark logic and the frozen 177 universe/hash untouched.
- `rs_v2_sip` / `rs_v2_clean` datasets and profiles untouched.
- `TIINGO_API_KEY`: env-only; never echoed, printed, or logged. Credential presence was checked without printing values.
- Free tier: zero Tiingo API calls this session (no refresh was run).
- No broker/order/live-trading calls; `broker_submit_enabled` false. The scheduler is structurally incapable of ordering (Section 4).
- The decision-event archive conflict was reported, not worked around; no archived events were deleted or modified.

## 8. Open Items for the Operator

1. **Ramp-in cap** (Section 3): keep 5/day, or raise it so the paper book tracks the backtest more closely? Unchanged pending your call.
2. **First nightly run**: the items in Section 4 that only a real night can exercise.
3. **Decision-event archive**: session 2026-07-24 already holds events written under commit `a49e1f9`. Re-running that specific session's chain will keep conflicting until it rolls to a new session — expected, and left for you to decide whether anything should be done about the stale records.

---

*No GO/NO-GO — evidence and state only.*
