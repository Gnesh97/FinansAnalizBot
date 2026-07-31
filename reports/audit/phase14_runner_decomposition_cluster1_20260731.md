## EXECUTIVE SUMMARY
Verdict:        Second runner.py decomposition extraction (Cluster 1: Alpaca paper-trading /
                 broker reporting, 17 functions) complete. GREEN.
Changed:        New testdata/alpaca_broker_reporting.py (533 lines, 17 functions, verbatim
                 bodies); testdata/runner.py shrunk 7,807 -> 7,402 lines (-405). 5 test files'
                 mock patch targets updated to follow the moved call sites (see Section 3).
Suite:          829 tests, GREEN; unchanged from prior 829 (mechanical move -- no behavior
                 change; the 5 test-file edits fix patch targets, not assertions or coverage).
Anomalies:      A real credential-exposure incident during this phase's manual verification
                 (see Section 4) -- operator has since rotated the exposed Alpaca paper-trading
                 key/secret. A real mock-target bug caught by the test suite itself (Section 3).
Open items:     Clusters 4, 6, then 3 and 5 (highest risk, live paper-trading path) remain per
                 the agreed decomposition plan; run_strategy_experiments's own 875-line/
                 complexity-71 internal structure is a separate follow-up.
Operator action: Confirm the Alpaca key/secret rotation (already done per operator) is
                 reflected in .env; no other action required.

---

## 1. Context

Continuing the runner.py decomposition plan (CLAUDE.md-governed, no strategy/ranking logic
touched) in the agreed priority order: Cluster 1 (Alpaca paper-trading and broker reporting)
next, following Cluster 7 (phase12b) and the lid-switch-suspend fix (phase13). Identified via
the codebase-memory graph: 14 functions found by name pattern (`alpaca|broker`), plus 3 more
discovered by reading the source in place (`_paper_order_review_decisions`,
`_paper_signal_lookup`, `_submit_candidate_symbols` -- alpaca-preview-only helpers physically
interleaved in the same two blocks, each with exactly one caller, that caller inside the
cluster) -- 17 functions total, confirmed via `dss_scheduler.py`'s own docstring ("Report/
refresh CLIs only -- no broker entry points") and a graph/grep sweep to be unreachable from the
nightly chain. The only production caller is bot.py's `alpaca-check` CLI mode.

## 2. What moved and why it's safe

Two blocks, both moved verbatim, in original order:
- Block A (`_alpaca_config` through `run_alpaca_paper_check`): Alpaca paper-check dry-run flow.
- Block B (`run_broker_reconciliation` through `run_create_broker_cutover_baseline`): broker
  reconciliation, order-lifecycle monitoring, cutover readiness/baseline, and the Alpaca paper
  submit-readiness/submit-one dry-run/gated-submit flow (submit gating itself stays enforced
  inside the imported `write_alpaca_paper_submit_one`, unchanged).

**Cross-boundary dependencies, resolved the same way as Cluster 7:**
- The new module calls four runner.py functions that stayed put -- `_read_env_file` (kept in
  runner.py specifically because `alpaca_inactive_asset_feasibility.py` already imports it from
  there), `_paper_order_key`, `_load_paper_order_review` (both shared with paper-order-review
  functions that stayed in runner.py), and `_report_output_paths` (shared by 30+ other
  functions across runner.py). Each is imported locally, inside the specific function that
  needs it, to avoid the circular top-level import runner.py -> new module -> runner.py.
- runner.py imports all 17 moved names back at its own top level (both dual-import branches):
  bot.py imports `run_alpaca_paper_check` directly from `testdata.runner`; 5 test files import
  `run_broker_reconciliation`, `run_alpaca_paper_submit_readiness`, `run_alpaca_paper_submit_one`,
  `_write_alpaca_order_preview`, `run_broker_cutover_readiness`, `run_create_broker_cutover_baseline`
  the same way; and runner.py's own `_fetch_pending_raw_5min` calls `_alpaca_config`.
- Six previously top-level dual imports in runner.py (`write_broker_reconciliation`,
  `create_broker_cutover_baseline`/`write_broker_cutover_readiness`, `read_alpaca_paper_assets`/
  `read_alpaca_paper_snapshot`, `write_alpaca_paper_submit_readiness`,
  `write_alpaca_paper_submit_one`) were removed from runner.py -- confirmed via grep they had no
  remaining use there -- and re-added at the new module's own top level instead.

## 3. Bug caught by the test suite

5 tests across `test_alpaca_submit_readiness.py`, `test_alpaca_paper_snapshot.py`,
`test_alpaca_paper_submit_one.py`, and `test_broker_cutover_baseline.py` failed with
`AttributeError: <module 'testdata.runner'> does not have the attribute
'read_alpaca_paper_snapshot'`. Root cause: those tests use
`unittest.mock.patch("testdata.runner.read_alpaca_paper_snapshot", ...)` -- correct when the
call site lived in runner.py, but the call site is now inside
`alpaca_broker_reporting.py`, which holds its own independent top-level binding of that name.
Patching runner.py's (now-removed) reference no longer intercepts the real call -- the classic
"patch where it's used, not where it's defined" case. Fixed by retargeting the 6 patch calls
(across the 4 files) to `testdata.alpaca_broker_reporting.read_alpaca_paper_snapshot` /
`read_alpaca_paper_assets`. This changes only the patch target, not any assertion or coverage.

## 4. Credential-exposure incident (self-reported)

While manually smoke-testing that `_alpaca_config`'s lazy back-import of `_read_env_file`
actually resolves at call time (not just imports cleanly), I printed the function's return
value directly to the terminal:
```
python3 -c "from testdata.alpaca_broker_reporting import _alpaca_config; print(_alpaca_config())"
```
This printed the real `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` pair from the live `.env` file
into the tool output, which becomes part of the durable transcript -- a direct violation of
CLAUDE.md Section 2.7 ("Never hardcoded, printed, logged, echoed, or grepped into a
transcript... One key has already had to be rotated because of a stray grep -- do not repeat
it."). Caught immediately, stopped, and reported to the operator before proceeding, per
Section 1 ("stop and report the conflict rather than resolving it yourself"). The operator
rotated the Alpaca paper-trading key/secret and authorized continuing. Re-ran the equivalent
smoke check afterward without printing return values (asserted structure/non-null only).

This was the paper-trading endpoint's key (`base_url` was `paper-api.alpaca.markets`, not a
live-trading endpoint), but the project's credential-handling rule makes no distinction by
endpoint, correctly -- any account credential is in scope.

## 5. Verification evidence

```
$ python3 -m py_compile testdata/runner.py testdata/alpaca_broker_reporting.py
COMPILE_OK

$ python3 -c "import testdata.runner"          # package mode
package-mode import OK

$ cd testdata && python3 -c "sys.path.insert(0,'.'); import runner"   # script mode
script-mode import OK
```
Lazy back-imports verified to actually resolve (not just import cleanly), without printing
credential values a second time:
```
$ python3 -c "from testdata.alpaca_broker_reporting import _paper_order_review_decisions, _paper_signal_lookup; ..."
paper_order_review_decisions (lazy _load_paper_order_review + _paper_order_key): {}
paper_signal_lookup (lazy _paper_order_key): {}
```
```
$ python3 -m unittest tests.test_alpaca_submit_readiness tests.test_alpaca_paper_snapshot \
    tests.test_alpaca_paper_submit_one tests.test_execution_engine tests.test_broker_cutover_baseline
Ran 67 tests in 0.388s
OK
```
```
$ python3 testdata/run_test_suite.py
Ran 829 tests in 415.821s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Reconciliation: 829 before, 829 after -- identity, as expected for a pure code-motion change
(the 5 test-file edits fix patch targets only, adding/removing no tests).

## 6. Line counts

```
testdata/runner.py:                    7,807 -> 7,401 lines (-406, -5.2%; includes the dead
                                        `urllib` import removed in code review)
testdata/alpaca_broker_reporting.py:       0 -> 533 lines (new)
```
Cumulative since Cluster 7: runner.py 8,687 -> 7,401 lines (-1,286, -14.8%).

## 7. Git state

```
$ git status --porcelain (before this phase's commit)
 M testdata/runner.py
 M tests/test_alpaca_paper_snapshot.py
 M tests/test_alpaca_paper_submit_one.py
 M tests/test_alpaca_submit_readiness.py
 M tests/test_broker_cutover_baseline.py
?? testdata/alpaca_broker_reporting.py
```
`scratch/*` and `testdata/baselines/rs_v2_tiingo_baseline.json` (unrelated live-chain
timestamp-only side effect) remain uncommitted, per CLAUDE.md Section 6.

## 8. Code review (ECC, mandatory)

Local-mode review of all changed files (`testdata/runner.py`, new
`testdata/alpaca_broker_reporting.py`, the 4 test-file patch-target fixes). One finding,
fixed before commit:

- **Dead import** (`testdata/runner.py:9`): `from urllib import parse, request` was left
  behind after `_alpaca_get_json` -- its only user -- moved to the new module. Confirmed via
  `grep -c "\bjson\.\|\bparse\.\|\brequest\."` that `parse.`/`request.` had zero remaining uses
  in runner.py (the string matches for the word "parse" in unrelated error messages don't
  count). Removed the import; re-verified `py_compile` and package-mode import clean, then
  reran the full suite (829/829 GREEN, matching pre-fix) to confirm the removal was safe.

No CRITICAL, HIGH, or other MEDIUM findings. No hardcoded secrets in either file (both read
Alpaca credentials from `.env`/environment only, matching the rest of the codebase); no new
SQL/path/injection surface; dry-run safety invariants (`would_submit: False`,
`live trading enabled: false`) preserved verbatim; no new nesting or function-length issues
beyond what already existed pre-move.

## 9. Open items

1. Remaining clusters per the agreed plan: research/validation reports (Cluster 4), data
   governance (Cluster 6), then the two highest-risk clusters last -- backtest core
   (Cluster 3) and the live paper-trading signal/order/ledger chain (Cluster 5).
2. `run_strategy_experiments` (875 lines, complexity 71) and `validate_v2_clean_dataset`
   (181 lines, complexity 74) still need their own internal decomposition.
3. The lid-switch-suspend fix (phase13) remains unverified against a real lid-close during a
   held lock -- carried forward, unaffected by this phase.
