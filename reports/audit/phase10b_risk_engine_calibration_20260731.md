## EXECUTIVE SUMMARY
Verdict:        Risk Engine Mode A recalibrated against real history; a real script-mode
                 import bug found and fixed in the same pass. GREEN.
Changed:        testdata/risk_engine.py (import fix + PORTFOLIO_HEAT_WARNING_PCT 6.0 -> 5.0),
                 tests/test_risk_engine.py (+1 regression test for the import bug),
                 scratch/phase10b_risk_engine_calibration_measure.py (new, read-only).
Suite:          814 tests, GREEN; reconciled against prior 813 (813 + 1 new = 814).
Anomalies:      A real production bug: testdata/risk_engine.py's internal imports used the
                 package-absolute form unconditionally, which breaks when runner.py /
                 universe_scanner.py run as scripts (the actual nightly/CLI invocation style).
                 Every existing test happened to run in package mode, so nothing caught it
                 until this phase's measurement script exercised the script-mode path. Fixed;
                 a dedicated regression test now exercises script-mode import directly.
Open items:     Loss-limit thresholds (-3.0%/-5.0%) were checked against history and left
                 unchanged. Heat threshold recalibrated to 5.0% (was 6.0%, which the data
                 showed would never have fired). All calibration is against n=67 rebalances /
                 n=1502 days from one frozen window -- a judgment call with evidence behind
                 it, not a statistically robust fit.
Operator action: None required to proceed; recalibration already applied per your answers.

---

## 1. Why this phase exists

Phase 10A (`9a84e7c`) shipped Risk Engine Mode A with three threshold constants
(`PORTFOLIO_HEAT_WARNING_PCT=6.0`, `DAILY_LOSS_LIMIT_WARNING_PCT=-3.0`,
`KILL_SWITCH_LOSS_LIMIT_PCT=-5.0`) that were explicitly documented as "starting defaults,
not fit or optimized against backtest data" -- literature references (Tharp) and round
numbers, not measurement. You asked to calibrate them before moving on. This phase measures
what the thresholds *should* be against the one real history this project has: the frozen
`rs_v2_tiingo` backtest window.

## 2. Method

Wrote `scratch/phase10b_risk_engine_calibration_measure.py`, following the exact pattern of
the Phase 9A frozen-window reproduction script: **fidelity gate first** -- the script aborts
unless it reproduces the frozen baseline's selection fingerprint exactly, so nothing derived
from it is trusted on a drifted harness (the hard lesson from the 167.24% phantom return and
the phantom OOS truncation, both caused by re-implemented harnesses that silently diverged
from production).

```
=== FIDELITY GATE ===
  fingerprint baseline : ba697e9653db37bdf348af1abc50de3366fe2cfbd26a271498cb31359fcada73
  fingerprint observed : ba697e9653db37bdf348af1abc50de3366fe2cfbd26a271498cb31359fcada73
  MATCHES              : True
```

Two measurements, both read-only, no state written outside `scratch/`:

1. **Historical portfolio heat.** Under equal weight (`target_weight = 1/30`, ~constant per
   position by construction), `risk_engine.py`'s heat formula (`sum(shares * dollar_atr) /
   total_open_notional * 100`) reduces algebraically to approximately the **mean ATR_Pct of
   the held Top-30** at each rebalance date (shares and notional both scale with position
   count and cancel; see the derivation note in the script's docstring). Computed directly
   from the production `ATR_Pct` feature at each of the 67 live rebalance dates, not the
   approximation, to avoid compounding two levels of estimation error.
2. **Daily equity-curve return distribution.** From the production backtest's own
   `_equity_curve` (day-over-day % change), to check whether -3%/-5% single-day moves are
   ordinary or rare for this strategy.

## 3. Results

**Historical portfolio heat** (mean ATR_Pct of held Top-30, 67 live rebalances):

| Statistic | Value |
|---|---|
| min | 1.60% |
| p05 | 1.84% |
| p50 (median) | 2.52% |
| p95 | 3.68% |
| p99 | 4.26% |
| max | 4.29% |

The old default of 6.0% is **above the observed maximum across the entire frozen window** --
it would never have fired once in 67 rebalances. That is not a working warning threshold, it
is dead weight (and, per the Phase 10A design, it was also one of the two conditions that can
trip the advisory kill switch -- so the kill switch's heat-triggered path was effectively
unreachable under normal history too).

**Daily equity-curve returns** (1502 trading days):

| Statistic | Value |
|---|---|
| min | -6.94% |
| p01 | -3.30% |
| p05 | -1.90% |
| p50 | 0.00% |
| p95 | 1.90% |
| p99 | 2.91% |
| max | 6.33% |
| days <= -3.0% | 21 / 1502 (1.40%) |
| days <= -5.0% | 3 / 1502 (0.20%) |

-3.0% sits almost exactly at the empirical 1st percentile; -5.0% catches only the genuine
tail (3 days across the whole window). Both were left as-is -- the "a warning that fires
constantly trains the operator to ignore it" lesson (CLAUDE.md Section 3) argues against
tightening further, and there was no evidence they were too loose either.

## 4. Decision, recorded

Presented via AskUserQuestion. Operator chose:
- **Portfolio heat: 6.0% -> 5.0%.** A round number with margin above the observed max
  (4.29%), not pinned to it -- avoids overfitting the threshold to a single 67-point sample
  while still being a real, reachable ceiling instead of the old dead one.
- **Loss limit (-3.0%) and kill-switch loss (-5.0%): unchanged.** Evidence already supported
  them.

Applied in `testdata/risk_engine.py`; the module-level comment above the constants now cites
this phase's numbers directly instead of an unattributed literature reference.

## 5. Bug found and fixed: script-mode import failure

Running the calibration script (which imports `runner` the same way the real nightly/CLI
entry points do -- `sys.path.insert(0, PROJECT_ROOT / "testdata")` then bare `import runner`)
surfaced a real production bug:

```
ModuleNotFoundError: No module named 'testdata'
  File ".../testdata/risk_engine.py", line 21, in <module>
    from testdata.paper_risk_dashboard import (
```

`testdata/risk_engine.py` (Phase 10A) imported its sibling modules with the package-absolute
form (`from testdata.paper_risk_dashboard import ...`), which only resolves when the project
root is on `sys.path` (how `tests/test_risk_engine.py` loads it, via `unittest discover` from
the repo root). Every other module in `testdata/` that has cross-module dependencies
(`runner.py`, `universe_scanner.py`) uses a try/except dual-import pattern -- relative
(`.paper_risk_dashboard`) when loaded as part of the `testdata` package, bare
(`paper_risk_dashboard`) when loaded as a script with `testdata/` itself on `sys.path`. This
is exactly how the real nightly DSS chain and `universe_scanner.py --risk-engine-dashboard`
invoke it. `risk_engine.py` was the first new module in this project's history to have
cross-module imports without following that pattern, and none of the 12 Phase 10A tests
caught it because `unittest discover` always runs in package mode.

**This would have broken `--risk-engine-dashboard` the first time it was actually run from
the command line**, exactly the kind of thing CLAUDE.md's "verify, do not assume" lesson
warns about -- the module compiled, imported fine in tests, and looked done, but had never
been exercised through the real invocation path.

Fixed: `risk_engine.py` now uses the same try/except relative/bare import pattern as every
other cross-importing module in `testdata/`. Added
`test_imports_cleanly_in_script_mode_like_the_nightly_cli_does`, which subprocess-invokes
`python3 -c "sys.path.insert(0, 'testdata'); import risk_engine"` and asserts a clean exit --
this is the first test in the Risk Engine suite that actually exercises the script-mode path,
so this class of bug can't silently recur.

## 6. Verification evidence

```
$ python3 -m unittest tests.test_risk_engine -v
...
Ran 13 tests in 0.370s
OK
```

```
$ python3 testdata/run_test_suite.py
Ran 814 tests in 413.798s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Reconciliation: 813 (prior, Phase 10A) + 1 (new script-mode regression test) = 814. Exact.

## 7. Git state

```
$ git status --porcelain (before this phase's commit)
 M scratch/tiingo_fetch_adapter.py
 M testdata/risk_engine.py
 M tests/test_risk_engine.py
?? scratch/phase10b_risk_engine_calibration_measure.json
?? scratch/phase10b_risk_engine_calibration_measure.py
?? scratch/phase5d_analysis_output.json
?? scratch/phase9a_frozen_window_measure.json
?? scratch/phase9a_frozen_window_measure.py
?? scratch/tiingo_full_universe_analysis.py
```
`scratch/phase10b_risk_engine_calibration_measure.py` is committed (it is this phase's
evidence-producing tool, same as `scratch/phase9a_frozen_window_measure.py` was for Phase
9A). Its `.json` output is a run artifact and is not committed, per CLAUDE.md Section 6. The
other `scratch/*` items predate this session (dated 2026-07-22/24), are unrelated to Risk
Engine, and remain uncommitted.

```
$ git log --oneline -5 (before this phase's commit)
9a84e7c feat(risk): add Risk Engine Mode A, an advisory-only paper-book risk report
442737d docs: add standing project rules (CLAUDE.md)
f84a526 fix(baseline): correct Sharpe metric, restructure guard as fingerprint-hard/metrics-informational
b18c217 docs(audit): file Phase 8E and re-validate the 2026-07-27 dataset on disk
519c763 fix(notices): stop test payloads reaching the operator's Telegram
```

## 8. Open items

1. Calibration is against **one frozen window** (2020-07 to 2026-07, n=67 rebalances / n=1502
   days). Real paper-book history, once it accumulates, is a second independent check --
   revisit if it disagrees with this backtest-derived calibration.
2. Heat threshold (5.0%) is a judgment call with a documented margin, not a statistically
   derived confidence bound -- flagged as such in the module comment so a future reader
   doesn't mistake it for more precise than it is.
3. Same standing items from Phase 10A remain: not wired into the nightly DSS chain; heat is
   % of open notional, not % of account equity (no cash/equity ledger in the live paper
   path); no true same-calendar-day loss delta is available.
