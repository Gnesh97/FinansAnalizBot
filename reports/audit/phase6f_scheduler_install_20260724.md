# Phase 6F — Nightly Scheduler Install and Verification (Linux)

**Date:** 2026-07-24 (installed 2026-07-25 00:5x Europe/Istanbul)
**Host:** Linux, project at `/home/gnesh/Desktop/BOT - Kopya`, user `gnesh`
**Scope:** Diagnose the failed `crontab -e` paste, harden the Linux launcher, install and prove the nightly schedule, run one real-delivery pre-flight, and assess trigger timing.

---

## 1. Diagnosis of the Failed Paste — Nothing Was Installed

```
$ crontab -l
no crontab for gnesh
```

**There is no malformed entry to clean, because nothing was ever written.** `crontab` validates the whole file before installing it; the `bad minute` error means it rejected the input outright and left the (non-existent) crontab untouched. The editor had wrapped the long command across lines, so the continuation fragment (`>> /path/... 2>&1`) was parsed as a new crontab record whose first field was not a valid minute.

No unrelated user cron entries existed, so nothing needed preserving.

**Root cause worth naming:** the failure mode is *pasting a long line into an interactive editor*, not cron itself. Both mechanisms below avoid it — the systemd units are short, single-purpose files, and the cron fallback (§6) is installed non-interactively so no editor can split anything.

## 2. Prerequisites Verified

| Check | Result |
|---|---|
| `scripts/run_dss_daily.sh` exists, executable | Yes — `-rwxrwxr-x` |
| Interpreter resolution hardened | **Was not** — used bare `python3` with no fallback and no error logging. Hardened this turn (below). |
| System timezone | `Europe/Istanbul (+03, +0300)`, NTP active, clock synchronized — **the 23:30 trigger is on the intended clock** |
| `.env` `TELEGRAM_BOT_TOKEN` | Present, non-empty (presence only — value never read or printed) |
| `.env` `TELEGRAM_CHAT_ID` | Present, non-empty |
| `.env` `PYTHON_PATH` / `PYTHON_EXECUTABLE` | Absent — not required; resolution falls through to `python3` |
| Python | `/usr/bin/python3`, 3.12.3 |
| systemd user manager | `running`, systemd 255 |

### Launcher hardening

`scripts/run_dss_daily.sh` now mirrors the `.bat` multi-tier resolution: (1) `.env` `PYTHON_PATH`/`PYTHON_EXECUTABLE`, each verified by actually executing `--version`; (2) `python3` then `python` on `PATH`; (3) absolute fallbacks `/usr/bin/python3`, `/usr/local/bin/python3`, `/opt/homebrew/bin/python3`. If none resolves it writes a timestamped `ERROR 9009` to `reports/dss/scheduler.log` and exits 9009 — this failure happens *before* the scheduler starts, so the log file is the only channel available; there is no Telegram notice for it, by necessity.

All output now tees to `reports/dss/scheduler.log`, with the real exit code preserved via `${PIPESTATUS[0]}` rather than `tee`'s. `.env` is read with a targeted `grep` for the two interpreter keys only — **the file is never sourced**, so no other secret in it is ever expanded into the shell environment.

Verified against a deliberately empty environment:

```
$ env -i HOME="$HOME" bash scripts/run_dss_daily.sh --help
[2026-07-25T00:55:13+03:00] [dss-daily] using python: /usr/bin/python3
[2026-07-25T00:55:13+03:00] [dss-daily] starting scheduler chain --help
usage: dss_scheduler.py [-h] [--skip-refresh] [--force] [--no-send] ...
[2026-07-25T00:55:13+03:00] [dss-daily] completed with exit code 0
```

## 3. Mechanism Chosen: systemd User Timer (not cron)

**Recommended and installed: systemd user timer.** Reasoning, specific to this machine:

- **cron does not run missed jobs.** If the machine is asleep, off, or shut down at the trigger time, cron silently skips that night entirely and the operator receives nothing — no report and no failure notice, because nothing ran to report a failure. `Persistent=true` makes systemd record the last elapse and run a missed trigger once on next start.
- **Neither mechanism can wake a sleeping machine** in this configuration. systemd's `WakeSystem=` requires `CAP_WAKE_ALARM` and is not reliable for a *user* unit, so it was deliberately not used. The honest guarantee is catch-up-after-wake, not wake-to-run.
- **Unit files cannot be line-split.** The exact failure that broke the paste is structurally impossible here.
- **`TimeoutStartSec` can cover the 3.8h refresh.** This matters more than it looks: `Type=oneshot` otherwise inherits `DefaultTimeoutStartSec` (90 s), which would kill the run ~88 minutes before the refresh finished. Set to `8h`.

### Installed units

`~/.config/systemd/user/dss-daily.service`
```ini
[Unit]
Description=Nightly DSS chain (Tiingo refresh -> validation -> signals -> DSS report -> Telegram)

[Service]
Type=oneshot
WorkingDirectory=/home/gnesh/Desktop/BOT - Kopya
ExecStart=/home/gnesh/Desktop/BOT - Kopya/scripts/run_dss_daily.sh
TimeoutStartSec=8h
```

`~/.config/systemd/user/dss-daily.timer`
```ini
[Unit]
Description=Trigger the nightly DSS chain at 23:30 Europe/Istanbul

[Timer]
OnCalendar=*-*-* 23:30:00 Europe/Istanbul
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
```

The timezone is pinned in `OnCalendar` (systemd ≥ 240) so the schedule stays correct even if the system timezone is later changed. No weekday filter, as instructed — the scheduler self-gates on the approved NYSE calendar, and a `Mon..Fri` filter would only add a second, drift-prone source of truth.

### Lingering — required, and enabled

```
$ loginctl show-user gnesh --property=Linger
Linger=no      # before
Linger=yes     # after `loginctl enable-linger gnesh`
```

**Lingering was needed and I enabled it.** Without it the user systemd manager is torn down at logout, and the timer simply would not exist while the operator is logged out — a 23:30 trigger on a machine nobody is sitting at would never fire. With `Linger=yes` the user manager starts at boot and persists across logout. This is a persistent system-level setting for this user; to undo it: `loginctl disable-linger gnesh`.

## 4. Proof It Is Scheduled

```
$ systemctl --user is-enabled dss-daily.timer
enabled
$ systemctl --user is-active dss-daily.timer
active

$ systemctl --user list-timers dss-daily.timer --all
NEXT                        LEFT LAST PASSED UNIT            ACTIVATES
Sat 2026-07-25 23:30:00 +03  22h -         - dss-daily.timer dss-daily.service
```

**Next fire time: Saturday 2026-07-25 at 23:30 Europe/Istanbul** (~22 h from install).

What that run will do, stated precisely so it is not mistaken for a failure: 23:30 TR Saturday is 20:30 UTC Saturday, and Saturday is not an NYSE session, so the chain will exit `SKIPPED_NON_TRADING_DAY` without fetching anything or sending anything. Sunday behaves identically.

**First run that actually produces a report: Monday 2026-07-27, 23:30 TR.** That is also the first session whose decision events do not already exist in the archive (per Phase 6E), so it is the first end-to-end clean night.

## 5. Pre-Flight — Real Telegram Delivery Confirmed

```
$ python3 testdata/dss_scheduler.py --skip-refresh --force --state-path reports/dss/preflight_test_state.json
[dss-scheduler] signals: paper signal generation
[stage] experiment 10/10 done in 6.47 sec
Experiment batch done in 54.54 sec
[stage] paper report started
DSS did not run: signals step exited 1:     results.append(append_decision_event(events_dir, build_decision_event(decision, event_type)))
  File "/home/gnesh/Desktop/BOT - Kopya/testdata/baseline_parity.py", line 227, in append_decision_event
    raise ValueError(f"conflicting duplicate event id: {event['event_id']}")
ValueError: conflicting duplicate event id: 1bfb61a438b209e3336f8e92ec4163a3
{
  "status": "FAILED",
  "target_session": "2026-07-24",
  "failed_step": "signals",
  "notice": {
    "sent": true,
    "text": "DSS did not run: signals step exited 1: ... conflicting duplicate event id: 1bfb61a4..."
  }
}
=== EXIT CODE: 1 ===
```

**`"sent": true` — the Telegram message was delivered.** This is exactly the delivery test Phase 6E predicted, and it proves the full notification path end to end: `.env` loading under a non-interactive environment, bot token authentication, network egress, and delivery to the configured chat.

The underlying failure is the expected, already-documented archive conflict: session `2026-07-24` holds decision events written under commit `a49e1f9`, and the archive fail-closes when the same decision identity reappears with a different content hash. Not a scheduler defect, and **no archived event was deleted or modified**.

**Production state confirmed untouched:**

```
$ cat reports/dss/scheduler_state.json
STILL DOES NOT EXIST -- production state untouched
```

The production state file has never been created (no production run has completed yet); the pre-flight wrote only to `reports/dss/preflight_test_state.json`. Because production state remains absent, Monday's first real run is unaffected by this test — it will see "no session reported yet" and proceed.

## 6. Timing Risk — 30 Minutes Is Thin; Recommend Moving to 01:00 TR

**The concern is real.** The US close is 16:00 ET = 23:00 TR, and the trigger is 23:30 TR, leaving Tiingo ~30 minutes to publish the completed session's EOD bar. Consolidated EOD data generally settles later than that — closing-auction prints, late reporting, and adjustment processing all land after the bell — so a 30-minute margin is optimistic for a free-tier feed.

**Being straight about the evidence:** this project has not yet measured Tiingo's actual post-close publication lag. The Phase 6B observation (latest published session was `2026-07-23` while `2026-07-24` was absent) was recorded at 19:34 TR, *before* that day's close, so it says nothing about post-close timing. The recommendation below is based on how consolidated EOD feeds behave generally, not on a measurement of this one.

**What happens if Tiingo has not published yet:** the refresh returns data ending one session short, the coverage check finds the target session missing, finalization fails, and the operator gets a `DSS did not run` notice. So the failure mode is a wasted night plus a loud notice — **not** silently wrong data. That is correct behavior, but a needless failure is still worth avoiding.

**Recommendation: move the trigger to 01:00 Europe/Istanbul** (18:00 ET, two hours after the close). A 3.8h refresh started at 01:00 finishes ~04:50 TR, still ~11.6 hours before the next US open at 16:30 TR.

**Hard ceiling the operator must respect: do not schedule at or after 03:00 TR.** The trading-day gate resolves the session from UTC, and 03:00 TR is 00:00 UTC — a later trigger rolls onto the next UTC day, which is a weekend or next-day date, and the chain would skip the very session it was meant to report. The usable window is **23:30–02:59 TR**. This constraint is now documented in both `run_dss_daily.sh` and the timer unit.

With a 01:00 TR trigger each US session is processed at 01:00 the following morning (Monday's session → Tuesday 01:00 TR; Friday's → Saturday 01:00 TR); Sunday and Monday 01:00 TR map to weekend UTC dates and correctly skip. All five weekly sessions remain covered exactly once.

### Exactly how to change it

```bash
systemctl --user edit --full dss-daily.timer     # change OnCalendar to: *-*-* 01:00:00 Europe/Istanbul
systemctl --user daemon-reload
systemctl --user restart dss-daily.timer
systemctl --user list-timers dss-daily.timer     # confirm the new NEXT time
```

Or edit `~/.config/systemd/user/dss-daily.timer` directly, then run the last three commands.

### If the operator prefers cron after all

Install non-interactively so no editor can split the line:

```bash
( crontab -l 2>/dev/null; echo "0 1 * * * /home/gnesh/Desktop/BOT\ -\ Kopya/scripts/run_dss_daily.sh" ) | crontab -
crontab -l
```

First disable the timer to avoid two schedulers racing: `systemctl --user disable --now dss-daily.timer`. Note this gives up missed-run catch-up.

## 7. Hard-Rule Compliance

- No validator, freshness check, or guard weakened or bypassed. The pre-flight failure was reported, not worked around.
- Strategy/ranking/benchmark logic, the frozen 177 universe/hash, and archived decision events all untouched.
- `rs_v2_sip` / `rs_v2_clean` datasets untouched.
- Credentials: presence checked by key name only; no `TIINGO_API_KEY` or `TELEGRAM_*` value was echoed, printed, or logged. The hardened `.sh` greps only the two interpreter keys and never sources `.env`.
- No broker/order/live-trading calls; the scheduled unit runs the same fixed three-CLI chain with no order path.
- No full refresh was run this session; zero Tiingo API calls.

## 8. Open Items

1. **Trigger time** (§6): keep 23:30 TR, or move to 01:00 TR for publication headroom? Installed at 23:30 as specified; the change is one edit plus a reload.
2. **First real report: Monday 2026-07-27, 23:30 TR.** Saturday and Sunday triggers will correctly skip.
3. **Unmeasured:** Tiingo's actual post-close publication lag. Monday's run will settle it — if it fails on a missing session, that is the answer, and §6's move to 01:00 is the fix.
4. `reports/dss/preflight_test_state.json` is a test artifact and can be deleted at any time; it is not read by the production chain.

---

*No GO/NO-GO — evidence and state only.*
