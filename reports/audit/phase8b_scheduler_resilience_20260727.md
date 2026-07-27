# Phase 8B — Nightly Scheduler Resilience

**Date:** 2026-07-27
**Scope:** Diagnose and fix the failed first catch-up run of the nightly DSS chain. No strategy/ranking/benchmark logic, frozen 177 universe/hash, validator or DSS freshness check touched. No `--live` refresh run. No broker/order calls. Timer left enabled on its existing schedule.

---

## Part 1 — Resume Status Check

The prior session was interrupted **after implementing all four fixes but before writing this report**. Nothing was half-applied.

| Item | State at resume |
|---|---|
| `reports/audit/phase8b_scheduler_resilience_20260727.md` | **did not exist** — created by this session |
| Fix 1 — network readiness | **implemented** in `scripts/run_dss_daily.sh` |
| Fix 2 — session-completion gate | **implemented** in `testdata/dss_scheduler.py` |
| Fix 3 — notice resilience | **implemented** in `testdata/dss_scheduler.py` |
| Fix 4 — tests | **implemented**, 35 passing in `tests/test_dss_scheduler.py` |
| systemd unit | **deliberately unmodified** — see §1.2 |

Working tree at resume:

```
 M scripts/run_dss_daily.sh
 M testdata/dss_scheduler.py
 M tests/test_dss_scheduler.py
 M scratch/tiingo_fetch_adapter.py        (pre-existing, unrelated)
?? reports/audit/phase8a_risk_measurements_20260727.md
?? scratch/phase8a_*                       (Phase 8A evidence)

 scripts/run_dss_daily.sh        |  76 ++++++++-
 testdata/dss_scheduler.py       | 303 +++++++++++++++++++++++++++++++++---
 tests/test_dss_scheduler.py     | 210 ++++++++++++++++++++++-
```

**Nothing protected was touched.** `git status --porcelain` over `testdata/runner.py`, `testdata/dss_report.py`, `testdata/strategies/`, `testdata/v2_session_coverage.py`, `testdata/vericekici_updated.py` and `testdata/baselines/` is empty.

**Timer intact and unchanged:** `enabled`, next fire `Tue 2026-07-28 01:00:00 +03`. Both unit files carry their pre-session mtimes (service Jul 26 13:21, timer Jul 25 02:36).

---

## Part 2 — What Actually Failed

Three independent defects stacked on one run. Any one of them alone would have produced a failed or wrong report.

1. **No connectivity at boot.** `Persistent=true` fired the missed 01:00 timer at 14:24 on boot, before DNS was up. The refresh step died on `URLError: [Errno -2] Name or service not known`.
2. **The failure notice shared the broken transport.** `send_failure_notice` made a single `urlopen` call to `api.telegram.org` — which failed for the same DNS reason. `"sent": false`, and the operator was told nothing. A network outage was indistinguishable from a clean weekend skip.
3. **The run targeted a session that had not closed.** At 14:24 TR (11:24 UTC) the chain targeted `2026-07-27`, whose close is 20:00 UTC. Even on a healthy network it would have spent hours refreshing toward bars that did not exist, then failed the freshness check.

---

## 1. Network Readiness

### 1.1 Launcher wait

`scripts/run_dss_daily.sh` now blocks until the chain's own dependency actually resolves, before starting anything:

```bash
NETWORK_HOST="${DSS_NETWORK_PROBE_HOST:-api.tiingo.com}"
NETWORK_WAIT_SECONDS="${DSS_NETWORK_WAIT_SECONDS:-900}"
NETWORK_PROBE_INTERVAL="${DSS_NETWORK_PROBE_INTERVAL:-15}"
```

**DNS resolution is probed, not merely a routable interface** — resolution is precisely what failed. `getent hosts` is used where available, with a `socket.getaddrinfo` fallback. On timeout the launcher logs `ERROR 9010`, queues a late notice (§3.3), and exits **without starting the chain**.

Verified live, both directions:

```
# bogus host, zero budget
[...] ERROR 9010: no network connectivity after 0s (cannot resolve no-such-host.invalid).
[...] ERROR 9010: chain NOT started. Queuing a late notice for the next run that can send it.
{"status": "QUEUED_NETWORK_ABORT", "queued_at": "2026-07-27T12:26:52.760632Z"}
abort exit code: 10

# resolvable host
[...] using python: /usr/bin/python3
[...] starting scheduler chain --help
passthrough exit: 0
```

### 1.2 systemd unit — network-online.target is NOT usable for user units

**I did not modify the unit, and the honest reason is that it would not help.** Measured on this machine:

```
systemctl --user show network-online.target -p LoadState  ->  LoadState=not-found
systemctl        show network-online.target -p LoadState  ->  LoadState=loaded
```

`network-online.target` exists only in the **system** manager. A user unit that declares `After=network-online.target` orders against a unit its manager cannot see — the dependency is silently inert, and a `Wants=` would additionally try to pull in a non-existent unit. Adding it would create the false impression that the ordering problem is solved.

The launcher-level wait is therefore the actual fix, and it has the further advantage of being transport-truthful: it tests the specific hostname the chain needs, not a generic "the network is up" signal that can be true while DNS is still unusable.

### 1.3 Latent bug found and fixed: exit codes were truncated

`exit 9009` (and my new `exit 9010`) cannot work: shell exit statuses are taken modulo 256, so systemd recorded **49** and **50**. The identifier documented in Phase 6F never reached the service manager. Fixed by keeping the familiar `ERROR 9009` / `ERROR 9010` text in the log while returning valid statuses:

```bash
readonly EXIT_NO_INTERPRETER=9
readonly EXIT_NO_NETWORK=10
```

---

## 2. Session-Completion Gate

### 2.1 The constraint that shaped the design

The obvious fix — "target the latest *completed* session instead of the latest calendar session" — is **not available**, because `dss_report._latest_expected_session` (line 84-90) computes the freshness bar the same way:

```python
schedule = approved_nyse_schedule(FROZEN_TARGET_START, today_str)
return schedule.index[-1].strftime("%Y-%m-%d")
```

and `_assert_dataset_ready` requires `manifest.target_end_date == expected_session`. Changing the freshness check is forbidden by the hard rules. So retargeting the scheduler to Friday while the freshness check still demanded Monday would simply relocate the failure.

**Therefore the scheduler defers instead of retargeting.** It runs only when the session the freshness check will demand has actually closed and published; otherwise it exits cleanly and waits for the next trigger.

### 2.2 Implementation

```python
def latest_published_session(as_of=None, lag_minutes=None, lookback_days=15) -> str:
    ...
    published = [session for session, close in zip(schedule.index, schedule["market_close"])
                 if close.tz_convert("UTC") <= now - timedelta(minutes=lag)]
```

- `target_session()` — unchanged; still delegates to the provider so it cannot drift from the freshness bar.
- `latest_published_session()` — the different question: which session has actually finished and had time to publish.
- `session_completion_gate()` — `ready` iff the two agree.
- Publication lag defaults to **90 minutes** after the 16:00 ET close, configurable by `DSS_PUBLICATION_LAG_MINUTES` or `--publication-lag-minutes`.
- Fails closed: raises `V2_NO_PUBLISHED_SESSION_IN_WINDOW` rather than guessing if no session closed inside the lookback.
- `--force` **does not evaluate the gate at all**, so an override run cannot inherit that fail-closed path.
- A deferral **does not write state**, so it cannot clobber the record of the last real outcome, and `already_reported` idempotency is unchanged.

### 2.3 Tested scenarios

| Scenario | `as_of` (UTC) | Expected | Published | Outcome |
|---|---|---|---|---|
| Normal 01:00 TR run | Mon 22:00 | 2026-07-27 | 2026-07-27 | **runs** |
| Mid-day catch-up (the real failure) | Mon 11:24 | 2026-07-27 | 2026-07-24 | **DEFERRED** |
| Lag boundary, one minute early | Mon 21:29 | — | 2026-07-24 | not yet publishable |
| Lag boundary, exactly at lag | Mon 21:30 | — | 2026-07-27 | publishable |
| Weekend | Sat 20:30 | — | 2026-07-24 | last weekday session |
| Holiday (2026-01-01) | 22:00 | — | 2025-12-31 | prior session |

The mid-day catch-up test asserts `_run_step` is **never called** and no state file is written.

### 2.4 Behaviour change the operator should know about

The launcher previously documented a valid trigger window of **23:30–02:59 TR**. With a 90-minute publication lag, 23:30 TR (20:30 UTC) is 30 minutes *before* the session is publishable, so it would now defer. **The documented window is now 00:30–02:59 TR.** The installed timer fires at 01:00 TR and sits comfortably inside it — no schedule change was made.

Five pre-existing tests encoded the old assumption via `FRIDAY = 20:30 UTC`; that constant moved to `22:00 UTC` (01:00 TR, the timer's real fire time) with a comment recording why. No assertion about scheduler behaviour was weakened.

---

## 3. Notice Resilience

### 3.1 Choice, and why

Three layers, because they fail independently:

1. **Bounded in-process retry** — 3 attempts, 20s apart. Covers a transport that returns within seconds (the common boot case).
2. **An unmistakable local log entry** — the previous single `ERROR:` line was indistinguishable from ordinary noise, which is exactly how the outage went unnoticed.
3. **A persistent queue** — because retry alone cannot help when the network is down for the whole run. An undelivered notice is written to `reports/dss/pending_notices.json` and sent by the next run that has connectivity, prefixed `LATE NOTICE (queued <ts>, session <s>):`.

I implemented all three rather than only the first two: retry alone would have changed nothing on 2026-07-27, since DNS was down for the entire run. The queue is what actually closes the hole.

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
NOTICE NOT DELIVERED -- the operator was NOT told about this failure.
  message : DSS did not run: refresh step exited 1
  transport error: Name or service not known
  queued for delivery on the next successful run.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

Entries that keep failing are retried up to `MAX_QUEUED_NOTICE_ATTEMPTS = 10` and then dropped with a loud local line, so the file cannot grow without bound.

**Drain placement (changed during code review).** The queue was initially drained after the session gate and the already-reported check. Review caught that this defeats the main scenario: a mid-day boot delivers network, then *defers* — and would have sat on the queued notice for another full day. The drain now runs **before every early return**, immediately after the trading-day gate, which is the first moment connectivity is known good (the launcher has already proven DNS resolves). A deferred run therefore still tells the operator about the earlier failure. Pinned by `test_deferred_run_still_delivers_a_queued_notice`.

### 3.2 Bug found while testing: `_redact` was shredding the diagnostics

Test output showed `Name or service no<redacted> known`. `_redact` did a blind `str.replace` of `TELEGRAM_BOT_TOKEN`; with a short token it replaced every occurrence of that substring, destroying the very error text the notice exists to carry. A real Telegram token is `<bot_id>:<35 chars>`, so anything shorter cannot authenticate:

```python
MIN_REDACTABLE_TOKEN_LENGTH = 12
```

Two tests pin both halves: a realistic 46-character token is still fully redacted; a 1-character value no longer mangles `Name or service not known`.

### 3.3 Launcher-side queueing

When the launcher aborts before the chain starts, there is no scheduler process to notice anything, so the launcher calls the scheduler purely to persist the notice:

```bash
"$RUN_PYTHON" testdata/dss_scheduler.py --queue-network-abort "cannot resolve ${NETWORK_HOST} after ${NETWORK_WAIT_SECONDS}s"
```

The queue format stays owned by Python rather than being hand-written by shell.

---

## 4. Tests

`tests/test_dss_scheduler.py`: **36 tests, all passing** (was 17 at `HEAD`, verified by `grep -c "    def test_"` against `git show HEAD:tests/test_dss_scheduler.py`; +19 added here).

| Class | Covers |
|---|---|
| `TestSessionCompletionGate` (8) | normal run, mid-day catch-up, lag boundary either side, weekend, holiday, deferral runs no steps and writes no state, deferral exits 0, `--force` never consults the gate |
| `TestNetworkUnavailablePath` (2) | `--queue-network-abort` persists a notice and exits 0; it does not run the chain |
| `TestUndeliveredNoticeQueueing` (9) | retry-then-queue, success-on-retry is not queued, late delivery is marked, failed drain increments attempts, drop at the ceiling, no-op drain, **a deferred run still delivers a queued notice**, realistic token redacted, short token does not shred text |

No network call is made by any test — `_post_telegram` is patched throughout, and all state is written to `tempfile.TemporaryDirectory()`.

**Full suite: 762 tests, `OK`, 0 baseline guards inactive.** Reconciles exactly against the Phase 7C total: 743 + 19 = 762.

---

## 4a. Code Review (ECC, local mode)

Scope: `scripts/run_dss_daily.sh`, `testdata/dss_scheduler.py`, `tests/test_dss_scheduler.py` — 560 insertions, 29 deletions.

- **MEDIUM — queued notices were drained too late.** Found and **fixed** during review; see §3.1. This was the difference between the operator hearing about a failure on the boot that follows it versus a day later.
- **MEDIUM — `run_daily_chain` is now 81 lines**, over the 50-line guideline (it was ~56 before this work). **Deliberately not refactored:** it is the orchestrator for a chain whose first successful run is hours away, and a cosmetic extraction there carries more risk tonight than the readability is worth. Flagged for a calmer moment.
- **LOW — `probe_network` tests resolution, not reachability.** DNS is what actually failed, and a reachability probe would add a request against the vendor's rate limit before the chain even starts. Documented in the script.
- **Security:** no credential value is printed or logged anywhere — the only matches for `TELEGRAM_BOT_TOKEN` are the variable name in an error string and `os.environ.get`. `_redact` was strengthened (§3.2), never weakened. `.env` is still grepped, never sourced.
- **No order path:** `ORDER_PLACEMENT_ENABLED` / `BROKER_SUBMIT_ENABLED` remain `False`, still asserted by `TestNoOrderPath`.
- **Validation:** `py_compile` passes on both Python files; `bash -n` passes on the launcher; both launcher branches exercised live (§1.1).

---

## 5. What the Operator Must Do

### Is keeping the machine on at 01:00 still required?

**The catch-up path is now safe, but "safe" means it refuses to produce a wrong report — not that it recovers a missed one.**

- A boot-time catch-up **can no longer report a session that has not closed**. It defers cleanly and exits 0.
- A boot-time catch-up **can no longer die silently on DNS**. It waits up to 15 minutes, and if the network never arrives it queues a notice that the next successful run delivers, marked late.
- **However: a session whose reporting window is entirely missed is never reported.** The chain always targets the latest expected session; it never walks backwards. If the machine is off through the whole 00:30–02:59 TR window for a given session, that session's BUY/SELL report is simply not produced, and the next run moves on to the following session.

So: keeping the machine on at 01:00 TR is **still required if you want a report for every session**. It is **no longer required to avoid a wrong or silently-failed report**.

### Failure modes that remain

1. **Machine off for several days** — each of those sessions is skipped permanently. Nothing currently alerts on the gap. The state file records only `last_target_session`, so a jump from `2026-07-24` to `2026-07-30` is invisible. A "missed sessions" check comparing the new target against `last_target_session` would close this; not implemented here.
2. **A deferral is indistinguishable from success at the systemd level** — both exit 0. Intentional (a deferral is a normal outcome), but it means `systemctl status` alone will not tell you a run deferred; the log line will.
3. **Network down for several consecutive runs** — notices queue and are delivered late in a batch. Beyond 10 failed delivery attempts an entry is dropped with a local log line only.
4. **Telegram itself unreachable while DNS works** — retry and queue cover it, but the operator still learns nothing until a later run succeeds.
5. **The publication lag is a heuristic.** 90 minutes after the close is generous for Tiingo EOD, but if the vendor is late the refresh can still fail on a missing session — that path fails loud and now notifies reliably.

---

## 6. Hard-Rule Compliance

- Strategy/ranking/benchmark logic, frozen 177 universe/hash, validators, and the DSS freshness check: **unchanged**. The freshness check's behaviour is what the new gate is designed around, not altered.
- No `--live` refresh. All verification used mocked steps, the `--help` passthrough, and a bogus-host abort.
- Credentials env-only. No `TIINGO_API_KEY` or `TELEGRAM_*` value echoed, printed, or logged; `_redact` was strengthened, not weakened.
- No broker/order/live-trading calls. `ORDER_PLACEMENT_ENABLED` / `BROKER_SUBMIT_ENABLED` remain `False` and are still asserted by `TestNoOrderPath`.
- Timer left **enabled** on its existing schedule; next fire `Tue 2026-07-28 01:00:00 +03`.
- Disclosed side effect: exercising the abort path queued two test entries into `reports/dss/pending_notices.json`. Both were removed and the file reset to `[]`, which the loader treats identically to absent.

---

*No GO/NO-GO — evidence and state only.*
