## EXECUTIVE SUMMARY
Verdict:        Five graph-confirmed dead functions removed. GREEN.
Changed:        testdata/v2_gap_recovery_runner.py (-_canary_outcome, -_full_outcome),
                 testdata/intraday_paper_monitor.py (-_to_ny_timestamp),
                 testdata/paper_state_manager.py (-build_empty_paper_state),
                 tests/baseline_metric_drift.py (-announced_guards, distinct from the
                 same-named, still-used function in tests/baseline_guard_visibility.py).
Suite:          829 tests, GREEN; unchanged from prior 829 (deletion of truly dead code
                 changes nothing observable -- reconciled by identical count, not addition).
Anomalies:      none.
Open items:     Operator asked not to act on: the frozen 9,385-line
                 testdata/universe_scanner_legacy.py (deliberately isolated, protected by
                 tests/test_legacy_isolation.py -- not touched), the current_pullback stub,
                 config.py's DEFAULT_STRATEGY_NAME, or testdata/backups/.
Operator action: None required.

---

## 1. Method

Scanned the codebase-memory knowledge graph (already indexed at HEAD `6365f8f`) for
Function-level nodes with zero in-degree and zero out-degree, excluding entry points. Method-
level zero-degree results (39) were excluded from the candidate list -- inspection showed
they were entirely `unittest` lifecycle methods (`setUp`/`tearDown`/`__init__`) and duck-typed
fake/mock classes, invoked reflectively or polymorphically in ways the static call graph
cannot trace; none were genuinely dead.

Each of the five Function-level candidates was independently re-verified with `grep -rn` for
the function name across the whole repository (not just the graph's edge data) before
deletion, to catch anything the graph might have missed (e.g. dynamic dispatch). All five had
exactly one match each: their own definition line.

One name collision was caught and handled carefully: `announced_guards` exists in two
different files. `tests/baseline_guard_visibility.py::announced_guards` (line 62) IS called by
`tests/test_baseline_guard_visibility.py`. `tests/baseline_metric_drift.py::announced_guards`
(a separate, differently-scoped function, line 190) had zero callers. Only the latter was
removed.

## 2. Also investigated, per operator question: QuantConnect and Alpaca

Not part of this deletion (nothing was removed), reported for awareness:

- **QuantConnect** (`quantconnect/rs_m126_top30_sma100_no_regime/`): referenced only by
  `tests/test_quantconnect_port.py`, a parity/contract test confirming the QuantConnect port's
  logic matches production. The port itself is not invoked anywhere in this repo's runtime --
  by design, a QuantConnect algorithm runs inside QuantConnect's own cloud sandbox after
  manual upload, not locally. Confirmed: set up, tested for correctness, not currently in use.
- **Alpaca** (6 files: `alpaca_inactive_asset_feasibility.py`, `alpaca_paper_broker.py`,
  `alpaca_paper_snapshot.py`, `alpaca_paper_submit_one.py`, `alpaca_submit_readiness.py`,
  `delisted_alpaca_price_coverage.py`): zero mentions in `testdata/dss_scheduler.py`, the
  actual nightly chain. All are manual CLI-only tooling (broker-cutover-readiness checks, a
  paper-broker interface, submit-one testing) built ahead of need -- the project roadmap
  places broker execution as the last phase, after Risk Engine and a paper-trading observation
  period, neither of which has reached that point yet. Confirmed: prepared, not currently
  wired into anything live. Consistent with CLAUDE.md's standing safety rule (`broker_submit_
  enabled`/`live_trading_enabled` force-`False`).

## 3. Verification evidence

```
$ python3 -m py_compile testdata/v2_gap_recovery_runner.py testdata/intraday_paper_monitor.py \
    testdata/paper_state_manager.py tests/baseline_metric_drift.py
COMPILE_OK

$ python3 testdata/run_test_suite.py
Ran 829 tests in 406.361s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Count unchanged at 829 (no tests referenced the removed functions, so none were lost; nothing
new was added).

## 4. Git state

```
$ git log --oneline -5 (before this phase's commit)
6365f8f feat(scheduler): wire Risk Engine into the nightly chain as a non-critical step
b46d733 feat(scheduler): detect and alert on nights the DSS chain never ran
843d60a fix(risk): calibrate Risk Engine heat threshold against real history, fix script-mode import
9a84e7c feat(risk): add Risk Engine Mode A, an advisory-only paper-book risk report
442737d docs: add standing project rules (CLAUDE.md)
```
`scratch/*` items from earlier phases remain uncommitted (unrelated, predate this session's
scope, per CLAUDE.md Section 6).
