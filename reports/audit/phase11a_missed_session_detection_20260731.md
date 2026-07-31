## EXECUTIVE SUMMARY
Verdict:        Missed-session detection implemented via TDD. GREEN.
Changed:        testdata/dss_scheduler.py (+detect_missed_sessions, +send_missed_session_notice,
                 +wiring in run_daily_chain, +MISSED_SESSION_MARKER), tests/test_dss_scheduler.py
                 (+9 tests).
Suite:          823 tests, GREEN; reconciled against prior 814 (814 + 9 new = 823).
Anomalies:      none.
Open items:     This closes the "no alert on a skipped night" gap, not the "machine must be
                 awake" gap (handoff open item #2) -- systemd still cannot wake a sleeping
                 machine; this only makes a skip loud the next time it does wake.
Operator action: None required. The next time the machine misses one or more nights, the
                 following successful run will print and (if Telegram is configured) send a
                 "DSS MISSED SESSION" notice listing the exact dates before proceeding.

---

## 1. The gap this closes

From the 2026-07-29 handoff, open item #1: "Kaçırılan seans tespiti yok. Makine gece kapalıysa
o seansın raporu üretilmez ve hiçbir uyarı çıkmaz -- durum dosyası sadece son hedefi tutuyor,
24'ten 30'a atlama görünmez." (No missed-session detection. If the machine is off overnight,
that session's report is never produced and no warning fires -- the state file only holds the
last target, a jump from one session to a much later one is invisible.)

Traced through `testdata/dss_scheduler.py`: `run_daily_chain` is only ever invoked by the
systemd timer firing. If the machine is off, the timer simply does not fire -- nothing runs,
nothing is written. `load_state`/`save_state` persist exactly one thing:
`last_target_session` + `status`. So after N missed nights, the next real run computes
`session = target_session(now)` (the newest session), finds it does not match
`last_target_session`, and proceeds normally -- correctly reporting the newest session, but
with zero record that N sessions in between were silently skipped. This is exactly the kind of
silent failure CLAUDE.md's own hard-won lessons warn about repeatedly.

## 2. Design

Two new functions plus one wiring point in `run_daily_chain`:

- **`detect_missed_sessions(last_reported_session, current_session)`** -- walks the approved
  NYSE calendar (`v2_session_coverage.approved_nyse_schedule`, the same calendar
  `is_trading_day` already uses) strictly between the two dates and returns any trading
  sessions that fall in the gap. A first-ever run (no prior state) returns `[]` -- there is
  nothing to have missed yet, not "all history missing."
- **`send_missed_session_notice(missed_sessions, ...)`** -- same retry-then-queue mechanism as
  the existing `send_failure_notice` (bounded retries, then durable queueing via the
  Phase 8E-hardened `queue_notice`/`drain_pending_notices` path), but its own
  `MISSED_SESSION_MARKER = "DSS MISSED SESSION"`, kept deliberately distinct from
  `DSS_FAILURE_MARKER = "DSS did not run"` so the two alert types are never conflated by the
  operator or by `_run_step`'s own `DSS_FAILURE_MARKER in stdout` check (tested explicitly:
  `test_missed_session_notice_lists_the_dates_and_uses_its_own_marker` asserts the failure
  marker text does NOT appear in a missed-session notice).
- **Wiring**: in `run_daily_chain`, right after the publication-completion gate resolves the
  actual session to process (and before the idempotency check), the state file's
  `last_target_session` -- regardless of whether that last run's status was SENT or FAILED,
  both mean the scheduler *did* run that night, only a frozen state means it did not -- is
  compared against the session about to be processed. Any gap is printed locally always, and
  sent to Telegram when `send=True`.

This does **not** touch the systemd timer, unit, or schedule (CLAUDE.md Section 7) -- it is
pure application logic inside the chain the timer already invokes.

## 3. Why it does not become noisy

The "a warning that fires constantly trains the operator to ignore it" lesson (CLAUDE.md
Section 3) was a live design constraint here: the notice can only fire on a run that actually
happens, and a run only happens when the timer fires, which only happens once the machine is
back on. So by construction it cannot fire on a night nothing runs -- it fires at most once per
actual invocation, and only carries content when there truly was a gap. If the gap persists
across several real invocations (e.g. the same failure keeps recurring), it will keep
re-notifying, which is the same "keep reminding rather than go silent" behavior the existing
`send_failure_notice` path already has for a stuck FAILED state.

## 4. TDD evidence

RED:
```
$ python3 -m unittest tests.test_dss_scheduler.TestMissedSessionDetection -v
...
AttributeError: module 'testdata.dss_scheduler' has no attribute 'detect_missed_sessions'
AttributeError: module 'testdata.dss_scheduler' has no attribute 'send_missed_session_notice'
Ran 9 tests in 0.009s
FAILED (errors=9)
```

GREEN, after implementation:
```
$ python3 -m unittest tests.test_dss_scheduler -v
...
Ran 50 tests in 1.454s
OK
```
(41 prior `test_dss_scheduler.py` tests + 9 new = 50, all green, including
`TestProductionQueueIsNeverTouchedByTests` -- confirms the new code path never touches the
real production notice queue during tests.)

Nine new tests cover: no gap on consecutive sessions, no gap on the same session, no gap on a
first-ever run (`None` or `""` prior state), a real weekend-plus-machine-gap scenario (missed
Thu/Fri, weekend itself correctly excluded), notice text contains the dates and its own marker
and does not contain the failure marker, notice retry-then-queue behavior, the chain calling
the notice function with the right arguments when a gap exists, the chain NOT calling it when
there is no gap, and the chain still detecting (and returning) the gap locally even when
`send=False`.

## 5. Verification evidence

```
$ python3 testdata/run_test_suite.py
Ran 823 tests in 409.096s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Reconciliation: 814 (prior, Phase 10B) + 9 (new missed-session tests) = 823. Exact.

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
`scratch/*` items are unrelated pre-existing files (dated 2026-07-22/24) and the Phase 10B run
artifact, left uncommitted per CLAUDE.md Section 6.

```
$ git log --oneline -5 (before this phase's commit)
843d60a fix(risk): calibrate Risk Engine heat threshold against real history, fix script-mode import
9a84e7c feat(risk): add Risk Engine Mode A, an advisory-only paper-book risk report
442737d docs: add standing project rules (CLAUDE.md)
f84a526 fix(baseline): correct Sharpe metric, restructure guard as fingerprint-hard/metrics-informational
b18c217 docs(audit): file Phase 8E and re-validate the 2026-07-27 dataset on disk
```

## 7. Open items

1. **Not the same as "machine must be awake."** This makes a skip loud after the fact; it does
   not make the machine wake up (handoff open item #2, still open, unrelated fix -- systemd
   cannot wake a sleeping machine, only prevent sleep during an active run, which is already in
   place from Phase 8E).
2. **Untested against the real systemd timer end-to-end** (simulating an actual multi-day
   machine-off gap and confirming the real Telegram message arrives) -- covered here at the
   unit level with mocked Telegram/state, consistent with how `send_failure_notice` itself was
   verified in earlier phases; live end-to-end confirmation would require actually letting the
   machine miss a night, which is not something to manufacture on purpose.
