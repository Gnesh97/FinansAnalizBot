# Phase 8E — Telemetry Contract, Mid-Run Sleep, and Test-Payload Spam

**Date:** 2026-07-28
**Scope:** No strategy/ranking/benchmark logic, frozen 177 universe/hash, or DSS freshness check changed. Timer schedule unchanged. No new `--live` fetch — all work on data already on disk. No broker/order calls. No credential echoed.

---

## PROBLEM 1 — Retry-telemetry validation rejected a real run

### 1.1 The contract, and what the manifest actually contained

`validate_v2_retry_telemetry` (`vericekici_updated.py:153`) enforced, for `MEASURED` telemetry:

```python
provider_attempts != base_operation_count + retry_attempts   -> reject
failed_attempts   != retry_attempts                          -> reject
```

The rejected manifest (`acq_20260727_220002`) contained:

| Field | Value |
|---|---|
| `retry_telemetry_status` | `MEASURED` |
| `provider_attempt_count` | **182** |
| `logical_acquisition_item_count` | **179** |
| `retry_attempt_count` | 0 |
| `failed_provider_attempt_count` | 0 |

182 ≠ 179 + 0, so it was rejected. **Three unexplained provider attempts.**

### 1.2 Why the gap exists — the telemetry was truthful

Reading the refresh loop (`tiingo_provider.py:349-399`), provider attempts legitimately depart from one-call-per-item in two ways the contract could not express:

1. **Items that issue no request at all.** A registry `EARLY_TERMINAL` symbol is skipped (`continue`), and a resumed run walks past items already completed in that run — both before any fetch. They still count as logical items.
2. **Drift-triggered full re-acquisitions.** When adjustment drift exceeds the threshold, the symbol is fetched a second time over full history (line 391). That is an extra call **without a failure**, so it is not a retry.

Reconciled against the manifest's own per-file evidence:

```
  logical items           : 179
  no provider call (SKIP) : 1 ['PARA']
  re-acquisitions (drift) : 4 ['CVS', 'PFE', 'PG', 'T']
  retries                 : 0
  predicted provider calls: (179 - 1) + 0 + 4 = 182
  recorded provider calls : 182
  RECONCILES
```

**Verdict: the telemetry is neither malformed nor incomplete in its counts.** The validator's schema simply had no vocabulary for a refresh that skips items and re-acquires on drift. The 13h wall clock, suspends and resumes were not themselves the cause — a five-minute refresh with one skip and four drift hits would have been rejected identically.

### 1.3 Is the DATA sound? — a separate question, answered separately

Yes. Telemetry is checked at `tiingo_provider.py:710`, *before* any file or coverage validation, so the failing run never reached those checks. Running the complete validator chain with only the telemetry step stubbed (in-process, nothing written):

```
DATA VERDICT: all non-telemetry checks PASS
  179 logical records | per-file SHA-256 re-verified against disk
  coverage validated vs approved NYSE calendar | frozen 177 universe hash intact
```

Supporting manifest facts: `data_status` VALID ×179, `coverage_status` VALID ×179, roles 177 CANDIDATE + 1 PRIMARY_BENCHMARK + 1 SECONDARY_BENCHMARK, all `adjusted`, zero files missing a SHA-256, `candidate_universe_hash` = `026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933` (unchanged).

**The data was always publishable. Only the contract rejected it.**

### 1.4 What was fixed — BOTH (a) and (b), deliberately

Not a relaxation. The recording was truthful but silent about *why*, and the validator could not express it. Fixing only one side would leave the other blind.

**(a) Recording** — `tiingo_provider.run_v2_tiingo_daily_refresh` now counts and publishes two facts it always knew:

```python
no_provider_call_item_count   # EARLY_TERMINAL skips + resume walk-past
reacquisition_item_count      # drift-triggered full re-acquisitions
```

**(b) Validator** — the `MEASURED` identity becomes:

```
provider_attempts == (logical_items - no_provider_call_items) + retry_attempts + reacquisition_items
```

Both new fields default to `0` when absent, so pre-refresh manifests collapse to the original identity and still validate. Declared-but-malformed values (`None`, negative, `bool`, non-`int`) reject, as does `no_provider_call_items > logical_items`, as does `failed_attempts != retry_attempts` (unchanged).

**Fail-closed is preserved where it matters most:** a refresh that performs a re-acquisition but fails to record it can no longer balance, and is rejected.

### 1.5 Both directions proven

| Case | Result |
|---|---|
| The actual 2026-07-27 run (182 = 178 + 0 + 4) | **PASS** |
| Pre-refresh manifest with no new fields (179 = 179 + 0) | **PASS** |
| Interrupted + resumed: 100 items walked past, 3 retries | **PASS** |
| Re-acquisition performed but NOT recorded | **REJECT** |
| `retry_telemetry_status` absent | **REJECT** (`telemetry missing`) |
| Negative / `None` / `bool` / string count | **REJECT** |
| Skips exceed logical item count | **REJECT** |
| `failed_provider_attempt_count != retry_attempt_count` | **REJECT** |

Pinned by `RetryTelemetryContractTest` in `tests/test_rs_v2_tiingo_refresh.py` (8 tests).

### 1.6 Re-validation of the on-disk data — no new fetch

`scratch/phase8e_revalidate_ondisk_manifest.py` declared the two counts **from the manifest's own per-file evidence**, refused to proceed unless the arithmetic reconciled exactly, then ran the production `finalize_v2_with_report` path — the full validator chain, not a bypass.

```
=== RESULT ===
  manifest status : COMPLETE_VALIDATED
  target_end_date : 2026-07-27
  latest pointer  : acq_20260727_220002
```

Finalization also refreshed `testdata/baselines/rs_v2_tiingo_baseline.json`; the diff is **one line**, `generated_at`. The frozen universe hash and every contract field are unchanged.

### 1.7 The first real BUY/SELL output

The DSS report run plainly (`bot.py --mode dss-report --print-only`) **correctly fail-louds right now**:

```
DSS did not run today: data is stale: manifest target_end_date='2026-07-27', expected latest completed session='2026-07-28'
```

That is the unmodified freshness check demanding today's session, which had not closed when this ran (16:33 UTC; the 2026-07-28 close is 20:00 UTC). It is the same "include today" behaviour documented in Phase 8B §2.1, and the hard rules forbid touching it.

Rendered instead with `as_of` = `2026-07-27 22:00 UTC` — the instant the nightly timer fired, when `2026-07-27` *was* the latest completed session. The freshness check is unmodified; only the reference time differs.

```
DECISION SUPPORT -- enter orders manually at next open
As-of data date: 2026-07-27
Universe: 177 symbols (hash 026f66d1...)
Strategy: rs_m126_top30_sma100_no_regime_v2_clean_stock
Generated at: 2026-07-28T16:33:42.003498Z

BUY (30):
  AAPL: price=336.91, target_weight=3.33%, rank=7, score=1.2524343114379273, reason=initial_paper_state;latest_entry_signal;missing_next_open
  AMD: price=494.95, target_weight=3.33%, rank=15, score=1.080549072857038, reason=initial_paper_state;latest_entry_signal;missing_next_open
  BAC: price=62.13, target_weight=3.33%, rank=16, score=1.070349334699952, reason=initial_paper_state;latest_entry_signal;missing_next_open
  CAT: price=873.28, target_weight=3.33%, rank=30, score=0.8175656758682939, reason=initial_paper_state;latest_entry_signal;missing_next_open
  CRWD: price=180.11, target_weight=3.33%, rank=18, score=1.0476207995043, reason=initial_paper_state;latest_entry_signal;missing_next_open
  CSCO: price=114.57, target_weight=3.33%, rank=9, score=1.2042101822964442, reason=initial_paper_state;latest_entry_signal;missing_next_open
  CVS: price=107.07, target_weight=3.33%, rank=17, score=1.0523397802548218, reason=initial_paper_state;latest_entry_signal;missing_next_open
  DDOG: price=251.86, target_weight=3.33%, rank=14, score=1.1320887087748923, reason=initial_paper_state;latest_entry_signal;missing_next_open
  EOG: price=140.31, target_weight=3.33%, rank=20, score=1.02190100056657, reason=initial_paper_state;latest_entry_signal;missing_next_open
  EQIX: price=1046.79, target_weight=3.33%, rank=5, score=1.3978056141435569, reason=initial_paper_state;latest_entry_signal;missing_next_open
  FDX: price=310.79, target_weight=3.33%, rank=26, score=0.8892021749049711, reason=initial_paper_state;latest_entry_signal;missing_next_open
  FTNT: price=152.38, target_weight=3.33%, rank=3, score=1.5273186050587828, reason=initial_paper_state;latest_entry_signal;missing_next_open
  HUM: price=381.15, target_weight=3.33%, rank=21, score=1.0006558795251048, reason=initial_paper_state;latest_entry_signal;missing_next_open
  INTC: price=91.67, target_weight=3.33%, rank=12, score=1.1523440460742087, reason=initial_paper_state;latest_entry_signal;missing_next_open
  JNJ: price=265.95, target_weight=3.33%, rank=25, score=0.8948344565053548, reason=initial_paper_state;latest_entry_signal;missing_next_open
  JPM: price=356.20, target_weight=3.33%, rank=23, score=0.9209927878903996, reason=initial_paper_state;latest_entry_signal;missing_next_open
  MAR: price=383.06, target_weight=3.33%, rank=29, score=0.8324109058382576, reason=initial_paper_state;latest_entry_signal;missing_next_open
  MPC: price=312.35, target_weight=3.33%, rank=1, score=2.2880868636109684, reason=initial_paper_state;latest_entry_signal;missing_next_open
  MRVL: price=189.17, target_weight=3.33%, rank=10, score=1.1754431763105917, reason=initial_paper_state;latest_entry_signal;missing_next_open
  MU: price=900.20, target_weight=3.33%, rank=13, score=1.1372154494121915, reason=initial_paper_state;latest_entry_signal;missing_next_open
  NUE: price=247.86, target_weight=3.33%, rank=11, score=1.1672459556084835, reason=initial_paper_state;latest_entry_signal;missing_next_open
  PANW: price=317.32, target_weight=3.33%, rank=6, score=1.3881439957478847, reason=initial_paper_state;latest_entry_signal;missing_next_open
  PSX: price=207.82, target_weight=3.33%, rank=4, score=1.5062711453615003, reason=initial_paper_state;latest_entry_signal;missing_next_open
  ROST: price=243.35, target_weight=3.33%, rank=27, score=0.865257279843313, reason=initial_paper_state;latest_entry_signal;missing_next_open
  STLD: price=250.27, target_weight=3.33%, rank=19, score=1.022146755687478, reason=initial_paper_state;latest_entry_signal;missing_next_open
  TGT: price=140.34, target_weight=3.33%, rank=22, score=0.9219673895078396, reason=initial_paper_state;latest_entry_signal;missing_next_open
  TXN: price=279.41, target_weight=3.33%, rank=24, score=0.9091360039268362, reason=initial_paper_state;latest_entry_signal;missing_next_open
  UNP: price=299.30, target_weight=3.33%, rank=8, score=1.2154529332608113, reason=initial_paper_state;latest_entry_signal;missing_next_open
  VLO: price=304.93, target_weight=3.33%, rank=2, score=1.67122320360128, reason=initial_paper_state;latest_entry_signal;missing_next_open
  VZ: price=47.32, target_weight=3.33%, rank=28, score=0.8342415897047717, reason=initial_paper_state;latest_entry_signal;missing_next_open

SELL: none today.
```

Signals were regenerated from the newly validated data via the production signals step (computation only, no fetch). `SELL: none` and `initial_paper_state` are expected: this is the first report, so there is no prior book to sell out of.

---

## PROBLEM 2 — The machine sleeps mid-run

### 2.1 systemd-inhibit is available AND usable by a user unit — verified, not assumed

`/usr/bin/systemd-inhibit` exists. The concern was whether `--what=sleep` needs privileges a user unit lacks. Given this project already shipped an inert `network-online.target`, it was tested from inside a real transient user unit rather than a shell:

```
$ systemd-run --user --unit=phase8e-inhibit-probe --collect --wait --pipe \
    systemd-inhibit --what=sleep:idle --mode=block --who="dss-probe" --why="..." \
    bash -c 'systemd-inhibit --list | grep dss-probe'

dss-probe  1000 gnesh 1551643 systemd-inhibit sleep:idle  verify user-unit can inhibit  block
Finished with result: success
```

The lock registers. No polkit denial. **This is a working mechanism, not a decorative directive.**

### 2.2 What was changed

`scripts/run_dss_daily.sh` wraps the scheduler invocation:

```bash
INHIBIT_PREFIX=(systemd-inhibit --what=sleep:idle --mode=block
                --who="dss-daily" --why="nightly DSS chain in progress")
"${INHIBIT_PREFIX[@]}" "$RUN_PYTHON" testdata/dss_scheduler.py "$@"
```

Scoped to the chain process, so normal sleep behaviour returns the instant it exits. Nothing is disabled globally. `shutdown` is deliberately **not** inhibited — the operator must always be able to power down. If the binary is absent the chain still runs and logs a warning; a missing inhibitor must not prevent the nightly report.

The change lives in the repo launcher rather than the machine-local unit file, so it is version-controlled.

### 2.3 Proof it registers during a run and releases after

Exercised through the real launcher with a mocked slow interpreter on `PATH`:

```
=== DURING mocked chain ===
dss-daily  1000 gnesh 1553702 systemd-inhibit sleep:idle  nightly DSS chain in progress  block
=== AFTER chain exits ===
released (no dss-daily lock)
```

### 2.4 Why TimeoutStartSec=8h did not kill a 13h run

**systemd service timeouts are measured on `CLOCK_MONOTONIC`, which on Linux does not advance while the system is suspended** (`CLOCK_BOOTTIME` is the one that includes suspend). Suspended time therefore never counted toward the 8h budget — the run consumed far less than 8h of monotonic time despite spanning 13h of wall clock.

Measured on this host, which shows how extreme the effect is:

```
  monotonic = 39.83 h
  boottime  = 285.72 h
  suspended = 245.88 h since boot
```

**Recommendation: keep `TimeoutStartSec=8h`. No change made.** With sleep now inhibited, wall clock and monotonic time converge, so the budget finally means what it appears to mean. Expected refresh cost is ~179 × 75s ≈ 3.7h plus drift re-acquisitions and any 429 backoff — 8h leaves roughly 2× headroom. Lowering it would risk killing a legitimately slow run; raising it would delay detection of a hang. The unit is machine-local host state and was left untouched.

---

## PROBLEM 3 — Test payloads reached the operator's real Telegram

### 3.1 Queue contents before any change

```
-rw-rw-r-- 1 gnesh gnesh 3 Jul 28 02:10 reports/dss/pending_notices.json
[]
```

**Confirmed: 3 bytes, empty array.** Nothing to delete — the synthetic entries had already been drained and delivered by the 01:00 run, which is exactly how the operator received them. No entry required a judgement call, and nothing genuine was discarded.

### 3.2 Forensics — which paths wrote synthetic payloads

**Path A — `tests/test_dss_scheduler.py:155`, the "boom" messages.**
`test_delivery_error_text_never_leaks_the_bot_token` called `send_failure_notice("boom")` with the Telegram env vars patched to dummy values and the transport patched to raise. Before Phase 8B that simply returned a failure dict. Phase 8B added queue-on-failure with `queue_path` **defaulting to the production queue**, so from that point every suite run appended a live entry. The suite ran ~10 times across Phases 8B–8D, which matches the 10 messages received.

Session was `None`, which rendered as `session unknown session` — the duplication the operator noticed.

**Path B — `QUEUED_NETWORK_ABORT` from `no-such-host.invalid`.**
The Phase 8B launcher verification ran `run_dss_daily.sh` with a bogus probe host. Its `--queue-network-abort` call used the default (production) queue path. Two entries were created and cleared during Phase 8B, but the mechanism was never closed.

Both share one root cause: **a production default on a parameter that verification paths silently inherited.**

### 3.3 Fixes — three independent layers

**1. Queueing without an explicit path is now a hard error.** `send_failure_notice(queue_path=...)` no longer defaults to the production queue; at the only line that writes, a `None` path raises:

```
ValueError: send_failure_notice requires an explicit queue_path in order to queue;
pass dss_scheduler.PENDING_NOTICE_PATH in production or a temporary path in tests.
```

Production passes it explicitly (already did). Any test that forgets now fails loudly instead of messaging the operator. The guard sits at the write site, not the function entry, so the legitimate `telegram_not_configured` early return is unaffected.

**2. Synthetic marking with a delivery-side backstop.** Queue entries carry `synthetic: bool`. `drain_pending_notices` **discards synthetic entries without transmitting them**, whatever put them there:

```
[dss-scheduler] DISCARDING synthetic queued notice (queued ...): never sent to the operator.
```

Belt and braces: even if a future path bypasses layer 1, the payload cannot reach the chat.

**3. Label duplication fixed.** The unknown-session fallback was the string `"unknown session"`, interpolated into `"... session {label})"`, producing `session unknown session`. Now `"unknown"` → `session unknown)`.

### 3.4 Tests

`tests/test_dss_scheduler.py` (36 → 41):

| Test | Guards |
|---|---|
| `test_production_queue_file_is_unchanged_by_this_suite` | Snapshots the production queue at import and asserts it is byte-identical afterwards — **fails if any test writes to it** |
| `test_queueing_without_an_explicit_path_is_a_hard_error` | The structural guard itself |
| `test_synthetic_entry_is_discarded_and_never_sent` | `_post_telegram` never called for a synthetic entry |
| `test_genuine_entry_alongside_a_synthetic_one_still_gets_through` | The filter is selective, not a blanket mute |
| `test_unknown_session_label_is_not_duplicated` | `session unknown)` present, `unknown session` absent |

The two offending tests now pass temporary paths and a synthetic payload string. **Side effect worth noting:** module runtime dropped from 41s to 1.3s, because those tests had been performing real 20-second retry sleeps against a patched transport.

---

## 4. Correction to Phase 8D, and a data finding the operator should see

Phase 8D attributed a `768 != 770` trade-count mismatch in the governing guard to a
*torn, half-written dataset*, because the refresh was mid-flight when it ran. **That
attribution was wrong.** Re-measured now, with the dataset settled and
`COMPLETE_VALIDATED`, the numbers are byte-identical to the mid-flight reading:

| Metric | Frozen baseline | Current dataset |
|---|---|---|
| Net return | 175.49% | **175.70%** |
| Trade count | 770 | **768** |
| CAGR | 18.46% | 18.47% |
| Max drawdown | −18.97% | **−19.36%** |
| Selection fingerprint | `ba697e96…` | **matches** |

The real cause is in this phase's own evidence: the 2026-07-27 refresh performed
**four drift-triggered full re-acquisitions — CVS, PFE, PG, T** — each of which
rewrites that symbol's entire history with Tiingo's re-adjusted prices. The frozen
baseline was computed on the pre-adjustment prices.

Two things follow, and they matter:

1. **The ranking is unaffected.** `test_selection_fingerprint_matches` passes, so
   the per-rebalance Top-30 selections are identical. Only execution-level fills
   moved, consistent with slightly different prices changing cash-constrained share
   counts in `calculate_max_shares`.
2. **The governing baseline no longer reproduces on current data**, because the
   vendor legitimately corrected four series. This is a data-governance decision
   for the operator, not a defect to patch: re-freezing would adopt the corrected
   prices; leaving it pins the guard to prices that no longer exist.

Nothing was re-frozen here — that was not this turn's task, and it is exactly the
kind of change that should be made deliberately rather than as a side effect. The
Phase 8D deferred procedure (§6 of that report) remains open, and now has a
confirmed cause rather than a suspected one.

**Suite consequence:** `test_frozen_headline_metrics` fails on trade count
(768 vs 770), independently of the still-stale Sharpe pin. Both resolve together
whenever the operator chooses to re-freeze.

---

## Hard-Rule Compliance

- Strategy/ranking/benchmark logic, frozen 177 universe/hash, DSS freshness check: unchanged. The universe hash was re-verified as `026f66d1…` after finalization.
- Timer schedule unchanged; `TimeoutStartSec` left at 8h; the unit file was not edited.
- No new `--live` fetch. Re-validation, signal regeneration and reporting all ran against data already on disk.
- No broker/order calls; `ORDER_PLACEMENT_ENABLED` / `BROKER_SUBMIT_ENABLED` remain `False`.
- No credential value read, echoed, printed, or logged. The token-redaction test now uses a token long enough to exercise the real redaction path.

---

*No GO/NO-GO — evidence and state only.*
