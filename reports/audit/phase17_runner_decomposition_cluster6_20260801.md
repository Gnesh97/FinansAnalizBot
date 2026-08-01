## EXECUTIVE SUMMARY
Verdict:        Fourth runner.py decomposition extraction (Cluster 6: data governance
                 reports, 24 functions) complete. GREEN.
Changed:        New testdata/data_governance_reports.py (920 lines, 24 functions, verbatim
                 bodies + 2 lazy-import shims); testdata/runner.py shrunk 5,973 -> 5,182
                 lines (-791).
Suite:          841 tests, GREEN; unchanged from prior 841 (mechanical move).
Anomalies:      Real bug caught before commit: the new module's header was missing `import
                 json` and `from pathlib import Path` -- both used extensively in the moved
                 code (Path 24x, json.dumps 1x) but not part of my speculative header. Caught
                 immediately by the dependent test files, not by inspection (Section 3).
Open items:     Clusters 3 and 5 (highest risk, live paper-trading path) remain per the agreed
                 decomposition plan -- last two, deliberately saved for last;
                 run_strategy_experiments's own 875-line/complexity-71 internal structure and
                 validate_v2_clean_dataset's 181-line/complexity-74 structure are separate
                 follow-ups.
Operator action: None required.

---

## 1. Context

Continuing the runner.py decomposition plan: Cluster 6 (data governance -- symbol data-status
checks, data precheck/policy decisions, regression-guard, pipeline health log, run manifest,
report-schema/paper-state validation) next, following Clusters 7, 1, and 4. Identified by
graph query over the line range plus a direct grep sweep of the extracted source for both
function calls and uppercase constants -- the same two-pass method Cluster 4 required after
its graph-only pass missed real dependencies. Confirmed via grep that none of this cluster's
write_* functions are referenced in dss_scheduler.py, universe_scanner.py, or bot.py -- like
Cluster 4, everything here is called only from `run_strategy_experiments` (stays in runner.py,
not part of the nightly chain).

## 2. What moved and why it's safe

24 functions, one contiguous block (`_symbol_data_status` through
`_write_paper_state_validation_report`), moved verbatim, in original order.

**Cross-boundary dependencies:** two runner.py functions that stayed put --
`_data_policy_decision_columns` (a tiny accessor returning `DATA_POLICY_DECISION_COLUMNS`) and
`_latest_csv_data_date` -- get the same single-shim treatment as Cluster 4's
`_table_for_markdown`. A third dependency, `_readable_table`, is not a runner.py function at
all (it lives in `chatgpt_master_report.py`, Cluster 7's module, already imported by runner.py
at its own top level) and is imported directly from that sibling module -- no circularity risk,
since chatgpt_master_report.py does not import this new module back.

runner.py imports all 24 names back at its own top level (both dual-import branches): four
test files (`test_report_schemas.py`, `test_run_manifest.py`, `test_data_precheck_actions.py`,
`test_regression_guard.py`) import `_pipeline_health_columns` / `_write_run_manifest` /
`_write_data_precheck_report` / `_write_regression_guard_report` directly from
`testdata.runner`; `chatgpt_master_report.py` already does its own lazy
`from .runner import _evaluate_data_policy_status` -- unaffected, since runner.py's re-export
keeps that name resolvable regardless of which file actually defines it now.

Both extraction and deletion used `sed` directly against the file (not retyped), same
transcription-risk-elimination approach as Cluster 4.

## 3. Bug caught before it shipped

The new module's speculative header (`import pandas as pd` only, following the pattern that
happened to be sufficient for Cluster 4) was missing two real dependencies used throughout the
moved code: `Path` (24 uses -- nearly every function in this cluster touches a report path) and
`json` (1 use, `json.dumps` in `_write_run_manifest`). Running the four directly-dependent test
files immediately surfaced `NameError: name 'Path' is not defined` inside
`chatgpt_master_report.py`'s call chain (7 test errors). Traced to the missing imports, added
`import json` and `from pathlib import Path` to the header, re-verified `py_compile` and the
same four test files clean (21/21), then ran the full suite.

This is the same class of gap as Cluster 4's missed cross-boundary function calls: a verbatim
code move still requires actually re-deriving what the moved code depends on, not assuming the
previous cluster's header shape carries over. Caught by the test suite itself in both cases,
not by static inspection alone -- exactly the kind of check this project's "verify, do not
assume" discipline exists to catch.

## 4. Verification evidence

```
$ python3 -m py_compile testdata/runner.py testdata/data_governance_reports.py
COMPILE_OK

$ python3 -c "import testdata.runner"                          # package mode
package-mode import OK

$ cd testdata && python3 -c "sys.path.insert(0,'.'); import runner"   # script mode
script-mode import OK
```
Lazy shim / sibling-module import resolution verified directly:
```
$ python3 -c "from testdata.data_governance_reports import _data_policy_decision_columns, \
    _latest_csv_data_date, _pipeline_health_columns, _readable_table; ..."
data_policy_decision_columns (lazy back-import): ['symbol', 'warning', 'policy_status']
pipeline_health_columns: ['run_datetime', 'command', 'data_update_ran']
readable_table (chatgpt_master_report import): |   a |\n|----:|\n|   1 |
latest_csv_data_date (lazy back-import, empty dir): none
```
```
$ python3 -m unittest tests.test_report_schemas tests.test_run_manifest \
    tests.test_data_precheck_actions tests.test_regression_guard tests.test_master_report_sections
Ran 21 tests in 0.127s
OK
```
```
$ python3 testdata/run_test_suite.py
Ran 841 tests in 916.090s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Reconciliation: 841 before this phase, 841 after -- identity, pure code motion.

Code review: swept every imported name in the new module (report_schemas constants,
`ensure_columns`, `_readable_table`) for actual use (all >=2 occurrences: import line(s) +
real call sites) and every report_schemas constant still imported in runner.py for continued
use elsewhere (all >=2) -- no dead imports left on either side this time.

## 5. Line counts

```
testdata/runner.py:                     5,973 -> 5,182 lines (-791, -13.2%)
testdata/data_governance_reports.py:        0 -> 920 lines (new)
```
Cumulative since Cluster 7 (phase12b baseline 8,687): runner.py 8,687 -> 5,182 lines
(-3,505, -40.3%).

## 6. Git state

```
$ git status --porcelain (before this phase's commit)
 M testdata/runner.py
?? testdata/data_governance_reports.py
```
`scratch/*` (staged by the operator ahead of their own push, not part of this commit) and
`testdata/baselines/rs_v2_tiingo_baseline.json` (unrelated live-chain side effect) excluded
per CLAUDE.md Section 6.

## 7. Open items

1. Remaining clusters per the agreed plan: backtest core (Cluster 3) and the live
   paper-trading signal/order/ledger chain (Cluster 5) -- the two highest-risk, deliberately
   saved for last.
2. `run_strategy_experiments` (875 lines, complexity 71) and `validate_v2_clean_dataset`
   (181 lines, complexity 74) still need their own internal decomposition, independent of
   which file they end up in.
