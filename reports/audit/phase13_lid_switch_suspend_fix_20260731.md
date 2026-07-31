## EXECUTIVE SUMMARY
Verdict:        Diagnosed and fixed the root cause of the 2026-07-31 stale-data chain
                 failure: the sleep inhibitor added after the 2026-07-27 incident does not
                 cover lid-switch-triggered suspend. Fixed. GREEN.
Changed:        scripts/run_dss_daily.sh -- inhibitor `--what=sleep:idle` extended to
                 `sleep:idle:handle-lid-switch`.
Suite:          829 tests, GREEN; unchanged from prior 829 (change is bash-only, outside the
                 Python test tree; full suite run anyway as a regression check).
Anomalies:      A second, distinct suspend-related incident (07-27, then 07-31) from the same
                 root cause class -- the first fix addressed idle/explicit suspend but missed
                 lid-switch suspend, a separate logind inhibit category.
Open items:     Screen-blanking-on-lid-close behaviour while the lock is held is expected to
                 keep working (separate mechanism, observed reacting independently of the
                 suspend action in the 07-31 journal) but has not been verified against a real
                 lid-close during a held lock -- can only be confirmed on the next real run.
Operator action: None required. Worth watching tonight's/next run's wall-clock duration and,
                 if convenient, noting whether the screen still blanks when the lid is closed
                 mid-run.

---

## 1. Context

Following up on the 2026-07-31 nightly chain FAILED run (report step: "data is stale:
manifest target_end_date='2026-07-30', expected latest completed session='2026-07-31'"),
flagged as a separate operational issue in phase12b. The operator asked to investigate the
root cause (not to touch the refactor work), then, once the cause was found, specified the
desired end-state precisely: lid can close, screen can go dark, but processing must continue,
and normal suspend-on-lid-close must resume once the chain finishes.

## 2. Root cause (evidence-backed)

Built a timeline of the 07-31 refresh from `daily/*.csv` mtimes (176 files, 02:35:56 -
17:20:00). All but one consecutive pair fell in a tight 75-111s band, matching the documented
free-tier pacing. One gap stood out: `JNJ_1D.csv` (04:16:37) -> `JPM_1D.csv` (15:20:57), a
39,860s (~11h) hole -- almost the entire excess over the documented ~3.7h runtime.

`journalctl` for that exact window:
```
04:16:57  Lid closed. -> Suspending... -> kernel: PM: suspend entry (s2idle)
04:17:24  kernel: PM: suspend exit           (brief wake, ~27s)
04:17:35  Lid closed. (again)
04:17:53  Suspending... -> kernel: PM: suspend entry (s2idle)
15:20:34  kernel: PM: suspend exit           (~11h later)
```
The chain's own log confirms the inhibitor *was* taken beforehand: `02:34:39 [dss-daily]
sleep/idle inhibited for the duration of the chain`. No inhibit-denial or delay message
appears anywhere in the journal around the suspend -- the lock was granted but simply didn't
cover this trigger.

`man systemd-inhibit` lists `handle-lid-switch` as an inhibit-what category **distinct** from
`sleep` and `idle`. `/etc/systemd/logind.conf` confirms the host default is
`HandleLidSwitch=suspend` (commented = default). `scripts/run_dss_daily.sh` requested
`--what=sleep:idle` (added in the 2026-07-27 post-incident fix) -- which blocks idle-triggered
and explicit/programmatic suspend, but not logind's own lid-switch handler. Closing the lid
therefore suspended the machine regardless of the held lock, for the full ~11h until the lid
was reopened.

This is the same failure class as the 07-27 incident (unbounded suspend mid-refresh
ballooning a ~3.7h run to ~13-15h), recurring via a different logind trigger than the one the
first fix covered.

## 3. Fix

`scripts/run_dss_daily.sh`, inhibitor construction (step 6): `--what=sleep:idle` ->
`--what=sleep:idle:handle-lid-switch`, `--mode=block` unchanged. Comment block rewritten to
record both incidents, the diagnosis, and the scope/limits of the fix.

Verified the combined `--what` value is accepted and actually registers a lock before editing
production code:
```
$ systemd-inhibit --what=sleep:idle:handle-lid-switch --mode=block --who="test-verify" \
    --why="verify combined what list accepted" sleep 5 &
$ systemd-inhibit --list | grep test-verify
test-verify   1000 gnesh 2269442 systemd-inhibit sleep:idle:handle-lid-switch  ...  block
```
Lock is process-scoped (tied to the `systemd-inhibit` wrapper's lifetime around
`dss_scheduler.py`), so it is released automatically the moment the chain exits -- this
directly satisfies "processing continues while the lid is closed, normal suspend-on-lid-close
resumes once the run is done," with no global change to `logind.conf` and no change to the
systemd unit/timer.

**Scope note, stated plainly:** this stops logind's own suspend action on lid close. It does
not, and is not intended to, change screen blanking -- that is handled by the desktop
session's own screensaver/DPMS logic, which the 07-31 journal shows reacting
(`org.cinnamon.ScreenSaver: Trying to connect to logind...`) at the same lid-close timestamp,
independently of whether suspend itself fires. It should therefore keep blanking the screen on
lid close unchanged. This half of the requirement is inferred from that log line, not directly
tested against a real lid-close with the new lock held -- flagged as the one open item.

## 4. Verification evidence

```
$ bash -n scripts/run_dss_daily.sh
(no output -- syntax OK)
```
```
$ python3 testdata/run_test_suite.py
Ran 829 tests in 404.785s
OK
SUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.
SUITE SUMMARY: 0 baseline guards reported headline-metric drift.
```
Reconciliation: 829 before, 829 after -- identity, as expected (the change is a one-line
`systemd-inhibit` flag in a bash wrapper script outside the Python test tree; the full suite
was run anyway as a blanket regression check per project convention, not because this class of
change is reachable by it).

No test file references `run_dss_daily.sh` or `systemd-inhibit` (`grep -rl` over `tests/`
returned nothing), so there is no automated coverage for this script -- consistent with it
being an ops wrapper outside the Python production path, not a gap introduced here.

## 5. Git state

```
$ git status --porcelain
 M scratch/tiingo_fetch_adapter.py          (pre-existing, unrelated)
 M scripts/run_dss_daily.sh                  (this fix)
 M testdata/baselines/rs_v2_tiingo_baseline.json  (unrelated live-chain side effect)
?? scratch/*                                 (pre-existing, unrelated)
```
Only `scripts/run_dss_daily.sh` is committed for this phase.

## 6. Open items

1. Screen-blanking-on-lid-close behaviour with the lock held is inferred, not directly
   verified (see Section 3) -- confirm on the next real overnight run or a manual test.
2. The `runner.py` decomposition plan (Clusters 1, 4, 6, then 3 and 5) remains queued,
   unaffected by this phase.
3. `testdata/baselines/rs_v2_tiingo_baseline.json`'s modification remains an unrelated
   live-chain side effect (timestamp-only), left uncommitted per the same reasoning as
   phase12b.
