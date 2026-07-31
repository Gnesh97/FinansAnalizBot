## EXECUTIVE SUMMARY
Verdict:        Risk Engine Mode A wired into the nightly DSS chain as a non-critical,
                 advisory-only step. GREEN.
Changed:        testdata/dss_scheduler.py (+"risk_engine" step in _chain_steps, +"critical"
                 flag on all four steps, +non-critical-failure handling in run_daily_chain),
                 tests/test_dss_scheduler.py (+6 tests).
Suite:          829 tests, GREEN; reconciled against prior 823 (823 + 6 new = 829).
Anomalies:      none.
Open items:     Heat/loss thresholds still calibrated against one frozen backtest window
                 (Phase 10B); real paper-book history is the next independent check.
Operator action: None required. The risk_engine step runs after the report step every night;
                 its failure is logged but never blocks or delays the BUY/SELL Telegram send,
                 and never triggers a "DSS did not run" notice.

---

## 1. Context and timing

This session's live nightly chain (`dss-daily.service`, catch-up-triggered at 02:34
Europe/Istanbul today after the machine came back online -- see the missed-session
investigation earlier this session) was still mid-refresh when this change was made. Verified
before editing: `_chain_steps()` is called once, near the top of `run_daily_chain`, and its
return value is captured into a local `steps` list for the remainder of that single
invocation; the three subprocess steps (`vericekici_updated.py`, `universe_scanner.py`,
`bot.py`) are independent script invocations that do not re-import `dss_scheduler.py`. Editing
the module on disk while that invocation is running therefore has no effect on it -- it
finished as a normal 3-step run using the code it had already loaded at 02:34:39. This new
4th step only takes effect on the next invocation.

## 2. Why the operator asked for this

Phase 10A/10B built and calibrated Risk Engine Mode A, but it was reachable only via a manual
CLI flag (`universe_scanner.py --risk-engine-dashboard`) -- invisible in the actual nightly
operation the operator relies on. The paper-trading observation phase that comes next in the
project roadmap has no risk visibility without this wiring.

## 3. Design

`_chain_steps()` gained a fourth step, `risk_engine`, appended **after** `report`:

```python
steps.append({
    "name": "risk_engine",
    "description": "advisory Risk Engine Mode A report (non-critical)",
    "command": [python_executable, str(BASE_DIR / "universe_scanner.py"),
                "--data-profile", DATASET_PROFILE, "--risk-engine-dashboard"],
    "critical": False,
})
```

Placed last, not before `report`, so a slow or failing advisory step can never delay the one
notice the operator actually acts on (the BUY/SELL report). It reuses `universe_scanner.py` --
the same binary the `signals` step already invokes -- so the set of distinct scripts the chain
runs is unchanged (still `vericekici_updated.py`, `universe_scanner.py`, `bot.py`; tested
explicitly).

All four steps now carry an explicit `"critical"` flag (`True` for refresh/signals/report,
`False` for risk_engine) rather than relying on an implicit default, per the "no hidden
behaviour" standing rule -- a reader of `_chain_steps()` should not have to know what happens
when the key is absent.

`run_daily_chain`'s step loop gained one branch before the existing failure handling:

```python
if not outcome["ok"] and not step.get("critical", True):
    print(f"[dss-scheduler] non-critical step {step['name']} failed, continuing: "
          f"{outcome['error']}", flush=True)
    continue
if not outcome["ok"]:
    ... existing critical-failure path: send_failure_notice, save FAILED state, return ...
```

A non-critical failure is logged (locally, and captured in the returned `outcomes` list for
anyone inspecting the result) but does **not**: mark the chain FAILED, save `status: FAILED`
to the state file, or call `send_failure_notice` -- so it can never produce a misleading "DSS
did not run" notice for a chain that, in every way that matters to the operator, actually did.

## 4. TDD evidence

RED:
```
$ python3 -m unittest tests.test_dss_scheduler.TestRiskEngineChainStep -v
...
AssertionError: 'risk_engine' not found in ['refresh', 'signals', 'report']
ValueError: 'risk_engine' is not in list
KeyError: 'risk_engine'
Ran 6 tests in 0.402s
FAILED (failures=1, errors=2)
```

GREEN:
```
$ python3 -m unittest tests.test_dss_scheduler -v
...
Ran 56 tests in 1.378s
OK
```
(50 prior + 6 new.)

Six new tests: the step exists and calls `--risk-engine-dashboard` via `universe_scanner.py`;
it runs after `report`; it alone is marked non-critical while the other three remain critical;
the set of distinct invoked scripts is unchanged; a risk_engine-only failure leaves the chain
`SENT` with no failure notice; a `report` failure still fails the chain exactly as before, even
with the new step present (guards against the non-critical branch accidentally swallowing a
real failure).

Also spot-checked argparse acceptance of the new flag combination directly (no data touched):
```
$ python3 testdata/universe_scanner.py --data-profile rs_v2_tiingo --risk-engine-dashboard --help
usage: universe_scanner.py [-h] [--reports {basic,full}] ...
```

## 5. Verification evidence

```
$ python3 testdata/run_test_suite.py
Ran 829 tests in 416.935s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Reconciliation: 823 (prior, Phase 11A) + 6 (new risk-engine-chain-step tests) = 829. Exact.

## 6. Git state

```
$ git status --porcelain (before this phase's commit)
 M scratch/tiingo_fetch_adapter.py
 M testdata/dss_scheduler.py
 M tests/test_dss_scheduler.py
?? scratch/phase10b_risk_engine_calibration_measure.json
?? scratch/phase5d_analysis_output.json
?? scratch/phase9a_frozen_window_measure.json
?? scratch/phase9a_frozen_window_measure.py
?? scratch/tiingo_full_universe_analysis.py
```
`scratch/*` items are unrelated pre-existing files (dated 2026-07-22/24) and a prior phase's
run artifact, left uncommitted per CLAUDE.md Section 6.

```
$ git log --oneline -5 (before this phase's commit)
b46d733 feat(scheduler): detect and alert on nights the DSS chain never ran
843d60a fix(risk): calibrate Risk Engine heat threshold against real history, fix script-mode import
9a84e7c feat(risk): add Risk Engine Mode A, an advisory-only paper-book risk report
442737d docs: add standing project rules (CLAUDE.md)
f84a526 fix(baseline): correct Sharpe metric, restructure guard as fingerprint-hard/metrics-informational
```

## 7. Open items

1. Not yet observed running for real inside the actual nightly chain end to end (the live run
   in progress during this session started before the change and will not pick it up; the
   next nightly trigger will be the first real exercise of this step). Worth checking
   `reports/dss/scheduler.log` after the next run for a `risk_engine_report.*` write and a
   clean (or cleanly-logged-and-ignored) outcome.
2. Standing items from Phase 10B remain: thresholds calibrated against one backtest window,
   heat is % of open notional not account equity, no true same-calendar-day loss delta.
