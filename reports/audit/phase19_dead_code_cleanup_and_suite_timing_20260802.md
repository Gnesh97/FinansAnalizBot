## EXECUTIVE SUMMARY
Verdict:        Investigated dead/unused code across the whole repo and test-suite runtime.
                 Operator decided: delete bot.py's unused mode handlers + interactive cockpit,
                 delete the orphaned delisted/Alpaca-inactive-asset research island, leave the
                 v2 migration cluster and the two slow tests untouched. Executed and verified.
                 GREEN.
Changed:        bot.py (1,145 -> 215 lines, -81%); deleted run_bot.bat,
                 testdata/delisted_security_metadata.py,
                 testdata/delisted_alpaca_price_coverage.py,
                 testdata/alpaca_inactive_asset_feasibility.py, and their 3 test files;
                 testdata/run_test_suite.py's COMPILE_TARGETS trimmed; a stale docstring
                 reference in alpaca_broker_reporting.py corrected.
Suite:          746 tests, GREEN; reconciled against prior 846 (-100, exactly the 44+16+40
                 tests in the 3 deleted test files -- no other test affected).
Anomalies:      None in this phase's changes. Two pre-existing findings surfaced and
                 deliberately left alone per operator decision (Section 3).
Open items:     v2 gap-recovery/forensics cluster (4,079 lines) and the 14 undocumented
                 universe_scanner.py flags remain, flagged for a possible future decision.
Operator action: None required.

---

## 1. Context

The operator asked for two investigations: (1) what in the repo doesn't actually run when the
bot runs -- "clearly there's old stuff set up that isn't used anymore" -- and (2) why the full
test suite takes so long to run. Both were research-first: an Explore agent traced the dead-code
question from the live nightly-chain source (not documentation, not the graph tool alone) with
every candidate cross-checked by direct grep; the timing question was profiled directly with a
custom per-test timing wrapper around `unittest`.

**A methodological note worth recording:** the codebase-memory graph tool's dead-code detector
(`search_graph(max_degree=0)`) was checked first and produced a small list including
`_table_for_markdown` -- which this session's own Cluster 4/6 work proved has 10+ real callers
via lazy `from .runner import X` imports inside function bodies. The graph's static CALLS-edge
analysis does not track these lazy imports, a blind spot already known from earlier this
session and reconfirmed here. The Explore agent was explicitly briefed on this and told to
verify every candidate by direct grep rather than trust the graph alone -- which is why this
phase's findings differ from (and are more precise than) what the graph alone would suggest.

## 2. Test suite timing -- investigated, no code change (operator decision: leave as-is)

Profiled all 846 tests with a custom `unittest.TextTestResult` timing wrapper. Two tests in
`tests/test_rs_v2_clean_data.py` dominate: `test_validate_v2_clean_dataset_accepts_358_rejects_357_and_359`
(170.3s) and `test_finalized_manifest_publishes_pointer_only_after_immutable_commit` (97.5s) --
together ~30-65% of total suite wall-clock time depending on machine load, against a next-slowest
test at 14.2s.

Root cause: both build a full-fidelity synthetic 358-file manifest (`_complete_manifest`,
matching the real 177-symbol x 2-timeframe universe) with real `shutil.copyfile` disk I/O and
real SHA256/coverage verification (`verify_file_integrity`, `coverage_metadata_for_file`) per
file -- the first test calls the shared validator twice, the second calls
`validate_v2_clean_dataset` three times (358/357/359 boundary cases) over the same real files.
This is not accidental bloat: it's the project's own "measure through the production code path,
never a re-implemented harness" discipline applied at full production scale, specifically
regression-testing a real prior bug (Phase 4A's stale 360/356 hardcoded boundary constants).

**Operator decision: leave as-is.** The confidence these two tests buy is judged worth the
wall-clock cost; suite speed is a lower priority than validation fidelity for this dataset-size
=production-size test.

## 3. Dead/unused code -- investigated and acted on per operator decision

Full findings (Tier 1 confirmed-dead, Tier 2 orphaned-islands, Tier 3 ask-the-operator) were
presented to the operator in chat; three decisions were made:

### 3a. Deleted: bot.py's unused mode handlers + interactive Telegram cockpit

Ground truth traced directly from `testdata/dss_scheduler.py:98-99`, which invokes bot.py as
`[python, "bot.py", "--mode", "dss-report", "--dss-data-profile", DATASET_PROFILE]` --
**always** with an explicit `--mode`, never relying on any default. Everything else in bot.py
(`status`, `local-paper`, `execution-preview`, `pre-submit-check`, `alpaca-check`, `backup`,
`interactive`, plus every Telegram-cockpit function reachable only from `interactive`) had zero
real callers anywhere in the repo:
- Not referenced in `README.md` (which documents the operator-facing workflow entirely through
  `universe_scanner.py` flags and `scripts/*.bat`, never through `bot.py`).
- The only thing that ever ran `bot.py` with the now-removed default mode was root-level
  `run_bot.bat`, itself untouched since 2026-06-21 (over a month stale relative to the live
  path) and not called by anything else in the repo.
- The deleted modes' data-folder pointer (`testdata/hisseler`) is itself stale/pre-migration --
  `run_pipeline_worker_thread` still called the pre-Tiingo IEX download interface
  (`vericekici_updated.py --universe all --feed iex ...`), not the live
  `--dataset-profile rs_v2_tiingo --refresh-daily` path.
- Zero test coverage: `tests/test_bot_dss_report.py` (bot.py's only test file, 5 tests) covers
  `run_dss_report` exclusively; nothing tested any of the deleted code.

Removed: `run_status`, `run_local_paper`, `run_execution_preview`, `run_pre_submit_check`,
`run_alpaca_check`, `run_backup`, and the entire "TELEGRAM COCKPIT INTERACTIVE MODE" block
(`build_inline_keyboard`, `create_price_chart`, `update_user_decision`, `get_active_positions`,
`send_review_list`, `send_positions_report`, `send_status_report`, `send_master_report_file`,
`send_help_message`, `run_pipeline_worker_thread`, `run_scheduled_scan`, `setup_scheduler`,
`handle_message`, `handle_callback_query`, `run_interactive_cockpit`) -- plus their
now-unreachable helpers (`send_telegram_document`, `answer_callback_query`,
`edit_message_text`, `PIPELINE_LOCK`) and the imports only they used (`shutil`, `threading`,
`subprocess`, `schedule`, `pandas`, `matplotlib`/`pyplot`, `yfinance`, `time`, `--verbose`).
`root-level run_bot.bat` deleted too -- it existed solely to launch the now-removed default
mode and had no other caller. `--mode`'s `choices` narrowed to `["dss-report"]` (still a real,
required-shaped argparse argument, since the nightly chain always passes it explicitly).
Verified surviving path (`dss-report`) still works: `tests/test_bot_dss_report.py` 5/5 GREEN,
manual smoke run produces the same report output as before.

**bot.py: 1,145 -> 215 lines (-81%).**

### 3b. Deleted: orphaned delisted-security / Alpaca-inactive-asset research island

`testdata/delisted_security_metadata.py` (1,913 lines, self-declared
`"""Research-only delisted-security metadata master..."""`),
`testdata/delisted_alpaca_price_coverage.py` (856 lines, self-declared
`"""Research-only Alpaca price coverage for the fixed delisted metadata pilot..."""`), and
`testdata/alpaca_inactive_asset_feasibility.py` (954 lines, self-declared
`"""GET-only research probe for Alpaca inactive US-equity metadata..."""`) formed a closed
two/three-file island: `alpaca_inactive_asset_feasibility.py` imports from
`delisted_alpaca_price_coverage.py`, and neither of those nor `delisted_security_metadata.py`
had any importer anywhere in the repo besides their own test files -- reconfirmed via a final
grep sweep immediately before deletion (`grep -rln "import <module>" testdata/*.py bot.py
scripts/*.py` returned nothing outside each file's own test). Each module's own docstring
self-identifies as a standalone research probe, consistent with the operator's own earlier
(phase12a) finding of a "prepared, not currently wired into anything live" Alpaca cluster.

Deleted along with their test files: `tests/test_delisted_security_metadata.py` (1,130 lines,
44 tests), `tests/test_delisted_alpaca_price_coverage.py` (334 lines, 16 tests),
`tests/test_alpaca_inactive_asset_feasibility.py` (608 lines, 40 tests). Also removed the two
now-dangling `COMPILE_TARGETS` entries in `testdata/run_test_suite.py`, and corrected a stale
docstring line in `alpaca_broker_reporting.py` that claimed
`alpaca_inactive_asset_feasibility.py` still imports `_read_env_file` from `runner.py` (it no
longer exists to do so -- that back-import's only remaining caller is
`alpaca_broker_reporting.py` itself; left `_read_env_file` in place rather than also moving it,
to keep this phase's scope to deletion, not further refactor).

**Total deleted: 6 files, 5,795 lines (3,723 production + 2,072 test).**

### 3c. Left untouched: v2 gap-recovery/forensics cluster (operator decision)

`v2_provider_contract_forensics.py`, `v2_provider_recovery.py`, `v2_gap_recovery_runner.py`,
`v2_daily_gap_selection_audit.py`, `v2_corrected_aggregation_audit.py`,
`v2_coverage_forensics.py` (4,079 lines combined) are imported by `vericekici_updated.py` (a
genuinely live file) but only behind CLI flags (`--provider-preflight`, `--gap-recovery-*`,
`--corrected-aggregation-audit`, `--daily-gap-selection-audit`) the nightly chain never passes,
and several are hard-pinned to the retired `rs_v2_clean`/`rs_v2_sip` acquisition profiles --
structurally unable to run against the live `rs_v2_tiingo` profile. This is migration-era
tooling from a completed data-source cutover. The operator judged this closer to CLAUDE.md's
"frozen history, do not modify or delete" territory than the bot.py/delisted clusters and asked
to leave it for a more careful, separate future look rather than delete it in this pass.

## 4. Verification evidence

```
$ python3 -m py_compile bot.py testdata/alpaca_broker_reporting.py testdata/run_test_suite.py
COMPILE_OK

$ python3 -c "import testdata.runner"
runner import OK
```
```
$ python3 -m unittest tests.test_bot_dss_report
Ran 5 tests in 0.004s
OK
```
AST-based unused-import sweep on the cleaned `bot.py`: zero remaining unused top-level imports.
```
$ python3 testdata/run_test_suite.py
Ran 746 tests in 402.902s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Reconciliation: 846 before this phase, 746 after (-100). Exact match: 44 + 16 + 40 = 100 tests
in the three deleted test files -- confirms no other test was affected by these deletions.
Suite runtime also dropped materially: ~403s here vs. the ~415s pre-session-load norm this
session established -- the deleted files' own tests were not among the slow ones (Section 2),
so this is mostly noise/reduced-collection-overhead, not the timing fix (there wasn't one --
Section 2's finding was left as-is by operator decision).

## 5. Git state

```
$ git status --porcelain (before this phase's commit)
 M bot.py
 D run_bot.bat
 M testdata/alpaca_broker_reporting.py
 D testdata/alpaca_inactive_asset_feasibility.py
 D testdata/delisted_alpaca_price_coverage.py
 D testdata/delisted_security_metadata.py
 M testdata/run_test_suite.py
 D tests/test_alpaca_inactive_asset_feasibility.py
 D tests/test_delisted_alpaca_price_coverage.py
 D tests/test_delisted_security_metadata.py
```
`testdata/baselines/rs_v2_tiingo_baseline.json` (unrelated live-chain timestamp-only side
effect, currently untracked after the earlier phase-cf9866b/61f3c1c fix) excluded per CLAUDE.md
Section 6.

## 6. Open items

1. v2 gap-recovery/forensics cluster (4,079 lines) -- left untouched per operator decision
   (Section 3c); worth a dedicated, more careful future look given its rs_v2_tiingo
   incompatibility.
2. 14 undocumented `universe_scanner.py` CLI flags (Tier 3 in the original investigation,
   e.g. `--alpaca-paper-submit-one`, `--broker-submit-enabled`) -- not raised for a decision
   this phase; several are safety-relevant, explicit-opt-in submit paths that may be
   deliberately undocumented rather than forgotten.
3. Broker-ledger/reconciliation cluster (`broker_ledger.py`, `broker_reconciliation.py`,
   `broker_lifecycle.py`) -- reachable via `README.md`-documented `--broker-reconciliation`,
   not part of the nightly chain but not undocumented either; not raised for a decision.
4. `runner.py` decomposition backlog (Clusters 3 and 5, highest risk) remains queued,
   unaffected by this phase.
