## EXECUTIVE SUMMARY
Verdict:        Third runner.py decomposition extraction (Cluster 4: research/validation
                 reports, 35 functions) complete. GREEN.
Changed:        New testdata/research_validation_reports.py (1,571 lines, 35 functions,
                 verbatim bodies + 4 lazy-import shims); testdata/runner.py shrunk
                 7,401 -> 5,973 lines (-1,428).
Suite:          841 tests, GREEN; unchanged from prior 841 (mechanical move -- no test file
                 exists specifically for this cluster beyond the one pre-existing OOS-invariant
                 test, which stayed green throughout).
Anomalies:      Two things caught before/during commit (Section 3): four cross-boundary
                 dependencies (_table_for_markdown, _build_experiment_tasks,
                 _run_experiment_batch, _trade_stats) that the codebase-memory graph's
                 CALLS-edge query silently missed, found instead by a direct source grep; and
                 one dead import found by code review after the move.
Open items:     Clusters 6, then 3 and 5 (highest risk, live paper-trading path) remain per
                 the agreed decomposition plan; run_strategy_experiments's own 875-line/
                 complexity-71 internal structure and validate_v2_clean_dataset's 181-line/
                 complexity-74 structure are separate follow-ups, untouched by this phase.
Operator action: None required.

---

## 1. Context

Continuing the runner.py decomposition plan in the agreed priority order: Cluster 4
(research/validation reports) next, following Cluster 7 (phase12b), the lid-switch-suspend
fix (phase13), Cluster 1 (phase14), the strategy dashboard (phase15), and a research_sandbox
vectorbt sweep (this session, not audit-tracked since it's explicitly non-production tooling).
Identified via the codebase-memory graph (function name/line/complexity query over two line
ranges) plus a direct grep sweep of the actual source, which is what caught the four
cross-boundary dependencies the graph's CALLS-edge query silently missed (Section 2) --
consistent with this project's "verify, do not assume" discipline: the graph is a fast lookup
tool, not the final authority.

## 2. What moved and why it's safe

35 functions across two blocks in the original file (`_yearly_focus_names` through
`_write_liquidity_guard_sensitivity_report`, and `_run_liquidity_guard_sensitivity_report`
through `_write_candidate_summary_report`), moved verbatim, in original order. All 35 are
called from exactly one place: `run_strategy_experiments` (stays in runner.py, itself flagged
separately for its own future internal decomposition -- 875 lines, complexity 71). Confirmed
`run_strategy_experiments` is not reachable from the nightly DSS chain (`grep
run_strategy_experiments testdata/dss_scheduler.py` -- zero matches).

**Cross-boundary dependencies, handled differently from Clusters 1/7:** this cluster calls
four runner.py functions that stayed put -- `_table_for_markdown`, `_build_experiment_tasks`,
`_run_experiment_batch`, `_trade_stats` -- but unlike the earlier clusters (which added a
per-function lazy `try/except` import at every call site), `_table_for_markdown` alone has 10
call sites across ~8 different functions in this cluster. Repeating the lazy-import boilerplate
10 times would be real duplication for no benefit, so each of the four gets a single
same-named module-level shim at the top of the new file (e.g. `def _table_for_markdown(frame):
... return _runner_table_for_markdown(frame)`) that does the lazy back-import once and
delegates. Every one of the 35 moved function bodies is therefore byte-for-byte identical to
its original in runner.py -- verified by extracting both blocks with `sed` directly from the
file (not retyped), eliminating transcription risk entirely for a move this size.

runner.py imports all 35 names back at its own top level (both dual-import branches): bot.py
and three production files (`baseline_parity.py`, `quantconnect_port_contract.py`) access
`_rs_robustness_experiments` via `runner._rs_robustness_experiments()` (module-attribute
style, not a named import -- confirmed this still resolves correctly since the name is present
in runner's namespace either way); `tests/test_slice_frame_end_bound_invariant.py` imports
`_oos_walkforward_periods` directly; `chatgpt_master_report.py` (Cluster 7) already does its
own lazy `from .runner import _final_candidate_decision_frame,
_read_final_candidate_decision_report` -- unaffected, since runner.py's re-export keeps those
names resolvable from `runner` regardless of which file actually defines them now.

## 3. Findings caught before/during commit

- **Four missed cross-boundary dependencies** (Section 2): the codebase-memory graph's CALLS
  query returned zero cross-boundary edges for this cluster (misleadingly suggesting full
  self-containment). A direct `grep -oE "\b_[a-zA-Z_]+\("` sweep of the actual extracted
  source caught `_table_for_markdown`, `_build_experiment_tasks`, `_run_experiment_batch`,
  `_trade_stats` -- all real, all needed. Root cause of the graph miss not investigated
  (out of scope for this phase); the practical lesson is the same one already written into
  this project's culture: cross-check the fast tool against the actual source before trusting
  it for something this consequential.
- **Dead import** (code review): `import numpy as np` in the new module was unused --
  `.to_numpy()` in the moved code is a pandas Series method, not the numpy module. Removed;
  re-verified `py_compile` and import clean, then reran the full suite.

No CRITICAL or HIGH findings. No hardcoded secrets. No new external dependencies. Function
count and file size (1,571 lines) within project norms for this file-split pattern.

## 4. Verification evidence

```
$ python3 -m py_compile testdata/runner.py testdata/research_validation_reports.py
COMPILE_OK

$ python3 -c "import testdata.runner"                          # package mode
package-mode import OK

$ cd testdata && python3 -c "sys.path.insert(0,'.'); import runner"   # script mode
script-mode import OK
```
Lazy shim resolution verified directly (not just import-clean):
```
$ python3 -c "from testdata.research_validation_reports import _table_for_markdown, \
    _rs_robustness_experiments, _oos_walkforward_periods, _trade_stats; ..."
table_for_markdown: |   a |\n|----:|\n|   1 |\n|   2 |
rs_robustness_experiments count: 48
oos_walkforward_periods: [('split_2020_2022', '2020-07-27', '2022-12-31'), ...]
trade_stats empty: {'trade_count': 0, 'profit_factor': 0.0, ...}
```
```
$ python3 -m unittest tests.test_slice_frame_end_bound_invariant -v
Ran 5 tests in 0.014s
OK
```
```
$ python3 testdata/run_test_suite.py
Ran 841 tests in 910.809s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Reconciliation: 841 before this phase, 841 after -- identity, as expected for a pure code-
motion change with no tests added or removed. The frozen baseline's selection-fingerprint
guard staying green confirms `run_strategy_experiments`'s behavior (which calls into all 35
moved functions) is unaffected. Suite runtime (~910s vs. the ~415s norm) reflects sustained
background load across this session's many parallel diagnostic runs, not a regression --
consistent with the same elevated runtime observed in phase14/phase15's later suite runs.

## 5. Line counts

```
testdata/runner.py:                       7,401 -> 5,973 lines (-1,428, -19.3%)
testdata/research_validation_reports.py:      0 -> 1,571 lines (new)
```
Cumulative since Cluster 7 (phase12b baseline 8,687): runner.py 8,687 -> 5,973 lines
(-2,714, -31.2%).

## 6. Git state

```
$ git status --porcelain (before this phase's commit)
 M testdata/runner.py
?? testdata/research_validation_reports.py
```
`scratch/*` and `testdata/baselines/rs_v2_tiingo_baseline.json` (unrelated live-chain
timestamp-only side effect) remain uncommitted, per CLAUDE.md Section 6.

## 7. Open items

1. Remaining clusters per the agreed plan: data governance (Cluster 6), then the two
   highest-risk clusters last -- backtest core (Cluster 3) and the live paper-trading
   signal/order/ledger chain (Cluster 5).
2. `run_strategy_experiments` (875 lines, complexity 71) and `validate_v2_clean_dataset`
   (181 lines, complexity 74) still need their own internal decomposition, independent of
   which file they end up in.
3. Per operator instruction, this session's commits are being pushed to
   `origin/checkpoint/research-framework-2026-07` immediately following this phase --
   authorship on all commits this session was already Gnesh97 <polatefed@gmail.com> only
   (no Co-Authored-By trailer; attribution is disabled globally per the operator's existing
   git-workflow settings), so no rewrite was needed before pushing.
