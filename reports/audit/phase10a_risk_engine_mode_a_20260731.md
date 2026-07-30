## EXECUTIVE SUMMARY
Verdict:        Risk Engine Mode A (advisory-only) implemented via TDD, GREEN, code-reviewed.
Changed:        new testdata/risk_engine.py + tests/test_risk_engine.py; runner.py and
                 universe_scanner.py wired with a new `--risk-engine-dashboard` CLI report
                 (not added to the nightly DSS chain).
Suite:          813 tests, GREEN; reconciled against prior 801 (801 + 12 new = 813).
Anomalies:      Self-review caught a real design gap before it shipped: the kill switch only
                 checked the loss limit, silently dropping the heat-triggered path from the
                 agreed design. Fixed and covered by a new test. See "Code Review" below.
Open items:     Thresholds (heat 6%, loss -3%/-5%) are starting judgment calls, not backtested
                 or operator-calibrated yet. Not wired into the nightly DSS chain (scope was
                 advisory reporting only; operator decides if/when to add it there).
Operator action: Review the four metrics and threshold defaults below; recalibrate if desired
                 once real paper-book history accumulates.

---

## 1. Context

Phase 9A closed out the Sharpe/guard-restructure work and was committed (`f84a526`,
`442737d`). This phase is the next item from the 2026-07-29 handoff: Risk Engine, Mode A —
an advisory layer that reports four risk metrics without touching position sizing, signals,
or orders. Per the handoff's own caveat, two of the four metrics (daily loss limit, kill
switch) can only see the **paper book**, not a real broker account, and had to be labeled as
such.

## 2. Design decision: what "daily loss limit" means here

Before writing tests, I checked what equity/cash history is actually available in the
**live** paper-trading path (not the backtest engine). Finding: none. `testdata/runner.py`'s
paper trading path (`paper_trade_log.csv`, `paper_portfolio_monitor.csv`) tracks only the
current snapshot of open positions and their unrealized P&L — there is no persisted
day-over-day equity/cash ledger. Only the **backtest** engine (`testdata/portfolio_manager.py`
`PortfolioManager.equity_curve`) tracks that, and it is a different code path entirely. An
earlier automated note in this session's memory claimed "equity tracking infrastructure
exists for Risk Engine data" — that was wrong; corrected after reading the actual code
(`testdata/portfolio_manager.py:276` is backtest-only, not reachable from the live paper path).

This meant "daily loss limit" could either be (a) a genuine day-over-day delta reconstructed
from `runs/<timestamp>/` snapshot history, which is fragile (pruning, weekends, retention
assumptions), or (b) the paper book's current unrealized P&L%, honestly labeled as "right
now," not a true same-day delta. Operator chose (b) — asked via AskUserQuestion, answered
"Anlık unrealized P&L% (Önerilen)". Implemented as such; every loss-limit and kill-switch
output field carries a `basis_note` string saying exactly this, tested explicitly
(`test_all_loss_limit_and_kill_switch_fields_carry_paper_book_only_label`).

## 3. The four metrics

| Metric | Source | Notes |
|---|---|---|
| ATR / volatility per position | `_atr_from_ohlc` reused (not reimplemented) from `testdata/strategies/relative_strength_rotation.py`, same 20-day rolling True Range used by the live strategy's own ATR% filter | `atr_pct`, `dollar_atr` per open symbol |
| Portfolio heat | `sum(shares * dollar_atr) / total_open_notional * 100` | Labeled `heat_basis`: this is **% of open notional, not % of total account equity** — no cash/equity ledger exists to compute the latter in the live path |
| Daily loss limit | `total_unrealized_pnl_pct` from `paper_portfolio_monitor.csv`, threshold -3.0% | Paper-book-only, current-snapshot, not a calendar-day delta (see §2) |
| Kill switch (advisory) | Triggers on loss <= -5.0% **or** heat > 6.0% | Advisory only -- reports a flag and a reason string, never blocks orders, never resizes positions |

Thresholds (`PORTFOLIO_HEAT_WARNING_PCT=6.0`, `DAILY_LOSS_LIMIT_WARNING_PCT=-3.0`,
`KILL_SWITCH_LOSS_LIMIT_PCT=-5.0`) are starting defaults -- the heat ceiling follows Tharp's
aggregate-open-risk guidance referenced in this project's source books; the loss levels are
round-number judgment calls, not fit or backtested (fitting them would violate the
no-parameter-optimization rule if this ever touched strategy decisions -- it doesn't, this is
reporting-only, but the same "don't manufacture false precision" discipline applies, same as
the 10% metric-drift threshold from Phase 9A).

## 4. Implementation

TDD, RED then GREEN:

- RED: wrote `tests/test_risk_engine.py` against a module that didn't exist yet.
  ```
  ModuleNotFoundError: No module named 'testdata.risk_engine'
  ```
- GREEN: implemented `testdata/risk_engine.py` (235 lines) following the existing
  `testdata/paper_risk_dashboard.py` pattern exactly -- `build_*()` pure function,
  `write_*()` produces the same md/csv/json triplet convention, reusing that module's
  private helpers (`_read_csv`, `_monitor_sections`, `_open_positions_from_trade_log`,
  `_number`, `_section_rows`, `_markdown_table`) rather than re-deriving exposure/PnL logic a
  second time -- this project has twice shipped false results from re-implemented harnesses
  (see CLAUDE.md Section 3), so reuse over reimplementation was deliberate.
- Wired `run_risk_engine_dashboard()` into `testdata/runner.py` (mirrors
  `run_no_lookahead_audit`'s `data_folder` -> `daily/` resolution pattern) and a new
  `--risk-engine-dashboard` CLI flag into `testdata/universe_scanner.py`. **Not** added to the
  nightly DSS chain -- CLAUDE.md Section 7 prohibits changing scheduler behaviour without an
  explicit task to do so; this is a manually-invoked report for now.

## 5. Code review (ECC, mandatory)

Ran the local-review checklist against the diff (`testdata/risk_engine.py`,
`tests/test_risk_engine.py`, `testdata/runner.py`, `testdata/universe_scanner.py`). Two real
issues found and fixed before this report was filed:

1. **HIGH -- kill switch silently dropped the heat-triggered path.** The plan presented to the
   operator said the kill switch fires "if heat OR loss limit breached," but the first
   implementation only checked the loss limit (`_kill_switch(open_book_unrealized_pnl_pct)`,
   no heat parameter). This is exactly the kind of silent behaviour-narrowing CLAUDE.md
   Section 2.5 forbids. Fixed: `_kill_switch` now takes `heat_pct`/`heat_warning` and triggers
   on either condition, with a `reason` string naming which one(s) fired. Added
   `test_kill_switch_triggers_advisory_flag_on_high_portfolio_heat_alone`, which constructs a
   fixture with wide ATR% (10% High/Low spread) and shallow P&L to prove heat alone triggers
   it, independent of the loss-limit path.
2. **MEDIUM -- inconsistent/redundant CSV row shape.** `write_risk_engine_report` wrapped
   `_section_rows(section, ...)` output in a second `{"section": section, **row}`, which was
   redundant (`_section_rows` already sets `"section"`) and left `position_risk` rows
   untagged, producing a CSV with two incompatible row shapes and no way to filter by section.
   Fixed: `position_risk` rows are now tagged `"section": "position_risk"`, and the redundant
   wrapper on the other three sections was removed.
3. **LOW (noted, not changed) -- `_position_risk_row`'s original length guard** compared
   `len(daily) < ATR_LENGTH` (20) rather than `ATR_LENGTH + 1` (21 rows needed for 20 valid
   True Range values after the first NaN from `shift(1)`). The `pd.isna(atr_value)` fallback
   already caught this safely (never crashed, just correctly fell back to `data_missing`), but
   the guard was tightened to `ATR_LENGTH + 1` anyway for clarity and to avoid a needless ATR
   computation on data that can't produce one.

No CRITICAL or unresolved HIGH issues remain. No hardcoded secrets, no SQL/network calls, no
ticker literals in decision logic (`AAA`/`BBB` are test fixtures, matching this project's
existing fixture convention). All functions stay under 50 lines; the new module is 235 lines,
well under the 800-line ceiling.

## 6. Verification evidence

**Unit tests (module-level, before final full-suite run):**
```
$ python3 -m unittest tests.test_risk_engine tests.test_paper_risk_dashboard -v
...
Ran 20 tests in 0.148s
OK
```

**Real-data smoke test** (read-only against the frozen `testdata/hisseler/daily/` dataset,
synthetic paper positions in a temp dir -- no production report path touched):
```
AAPL: atr_pct=2.93%, dollar_atr=9.09, close=310.69
MSFT: atr_pct=3.06%, dollar_atr=11.89, close=388.74
portfolio_heat: heat_pct=3.76% (below 6.0% warning threshold)
daily_loss_limit: breached=false (-1.25% vs -3.0% threshold)
kill_switch: advisory_triggered=false
```
ATR% in the 2.9-3.1% range on AAPL/MSFT is consistent with this project's own Phase 8A
measurement of ~18.9% annualized portfolio volatility (a few percent daily ATR is the right
order of magnitude) -- sanity-checked, not just executed.

**Full suite, final:**
```
$ python3 testdata/run_test_suite.py
Ran 813 tests in 408.714s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Reconciliation: 801 (prior, Phase 9A) + 12 (new `tests/test_risk_engine.py`) = 813. Exact.

**Compile/import check:**
```
$ python3 -m py_compile testdata/risk_engine.py testdata/runner.py testdata/universe_scanner.py tests/test_risk_engine.py
COMPILE_OK
$ python3 -c "from runner import run_risk_engine_dashboard; print('import OK', run_risk_engine_dashboard)"
import OK <function run_risk_engine_dashboard at 0x...>
```

## 7. Git state

```
$ git status --porcelain
 M scratch/tiingo_fetch_adapter.py
?? scratch/phase5d_analysis_output.json
?? scratch/phase9a_frozen_window_measure.json
?? scratch/phase9a_frozen_window_measure.py
?? scratch/tiingo_full_universe_analysis.py
```
(after this phase's commit -- testdata/risk_engine.py, tests/test_risk_engine.py,
testdata/runner.py, testdata/universe_scanner.py committed; the scratch/ items above predate
this session, dated 2026-07-22/24, unrelated to Risk Engine, and are left uncommitted per
CLAUDE.md Section 6 -- scratch/ run artifacts are not committed)

```
$ git log --oneline -5
<new commit will be prepended here>
442737d docs: add standing project rules (CLAUDE.md)
f84a526 fix(baseline): correct Sharpe metric, restructure guard as fingerprint-hard/metrics-informational
b18c217 docs(audit): file Phase 8E and re-validate the 2026-07-27 dataset on disk
519c763 fix(notices): stop test payloads reaching the operator's Telegram
```

## 8. Open items for the operator

1. **Thresholds are judgment calls, not calibrated.** 6% heat, -3%/-5% loss levels came from
   general risk-management literature (Tharp), not this system's own paper-book history
   (which doesn't exist yet -- the paper book is new). Recalibrate once real data accumulates.
2. **Portfolio heat is % of open notional, not % of account equity**, because no cash/equity
   ledger exists in the live paper path. If a real equity ledger is ever built for the paper
   book, heat should be redefined against it -- this is flagged in `heat_basis` on every
   report so it's never silently misread as a true Tharp-style equity-based heat number.
3. **Not wired into the nightly DSS chain.** Available via
   `python universe_scanner.py --risk-engine-dashboard` manually. Wiring it into the 01:00
   nightly run is a separate, explicit decision the operator should make (CLAUDE.md Section 7).
4. **No historical loss-limit is available** (see Section 2) -- if the operator wants a true
   same-calendar-day equity delta later, that requires either a persisted equity/cash ledger
   in the live paper path or a documented, tested `runs/` snapshot-diffing mechanism; neither
   exists today.
