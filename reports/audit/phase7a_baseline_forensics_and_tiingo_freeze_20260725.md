# Phase 7A — Baseline Forensics and Tiingo Strategy Freeze

**Date:** 2026-07-26 (task opened 2026-07-25; a first attempt was interrupted mid-forensic and left no artifacts)
**Scope:** Root-cause the 165.9133%/762 vs 167.24%/771 discrepancy, classify it, and — no violation having been found — freeze a governing baseline on the validated rs_v2_tiingo dataset.

---

## 0. Resumption Status (what the interrupted run had left)

| Item | State found |
|---|---|
| `reports/audit/phase7a_*.md` | **Not created** — no partial report existed |
| Commits | **None** — `git log` head was still `2fc25c1` (Phase 6F) |
| Working tree | Unchanged from pre-Phase-7A (only pre-existing Phase 5D scratch leftovers) |
| Forensic scripts | Two, in the session scratchpad (outside the repo), complete and re-runnable |
| Frozen universe | 177 symbols, hash `026f66d1…`, BK absent — intact |
| rs_v2_tiingo dataset | Re-validated: `COMPLETE_VALIDATED`, all 179 file hashes verified |
| SIP equivalence guard | Still passes (165.9133/762) |

The interrupted run *had* reached a forensic conclusion; it was re-derived and re-verified here rather than trusted from memory.

### Live defect found during the status check — Monday's run was already broken

Part 1 asked me to confirm the installed timer was undisturbed. It was undisturbed, but it was **already broken**, by me, in Phase 6F:

```
$ systemctl --user status dss-daily.service
× dss-daily.service   Active: failed (Result: exit-code)
  Process: ExecStart=/home/gnesh/Desktop/BOT - Kopya/scripts/run_dss_daily.sh (code=exited, status=203/EXEC)
```

`ExecStart=` is whitespace-split by systemd, so the space in `BOT - Kopya` made it try to exec `/home/gnesh/Desktop/BOT`. The unit had **never once executed successfully**. Sunday's `Persistent=true` catch-up at 13:16 failed the same way, and Monday 01:00 would have too — silently, because the failure occurs *before* the scheduler starts and therefore sends no Telegram notice. This is precisely the pre-scheduler failure mode Phase 6F documented for the Windows `.bat` (exit 9009); it landed on Linux instead and I did not catch it, because Phase 6F proved the *timer was scheduled* without ever proving the *service could execute*.

Fixed and verified:

```
ExecStart="/home/gnesh/Desktop/BOT - Kopya/scripts/run_dss_daily.sh"   # quoted: ExecStart IS whitespace-split
WorkingDirectory=/home/gnesh/Desktop/BOT - Kopya                        # unquoted: this directive is NOT split
Documentation=file:///…/BOT%%20-%%20Kopya/…                             # %% escapes the systemd specifier
```

My first attempt at the fix quoted `WorkingDirectory` too and made things worse — the unit went from `203/EXEC` to `bad-setting` (would not load at all). `systemd-analyze verify` caught it; the value above is the verified-correct form.

```
$ systemd-analyze --user verify ~/.config/systemd/user/dss-daily.service   # (clean, no output)
$ systemctl --user show dss-daily.service -p ExecStart
ExecStart={ path=/home/gnesh/Desktop/BOT - Kopya/scripts/run_dss_daily.sh ; argv[]=/home/gnesh/Desktop/BOT - Kopya/scripts/run_dss_daily.sh ; … }
$ systemd-run --user --wait --pipe … "/home/gnesh/Desktop/BOT - Kopya/scripts/run_dss_daily.sh" --help
[2026-07-26T13:21:30+03:00] [dss-daily] using python: /usr/bin/python3
usage: dss_scheduler.py [-h] [--skip-refresh] …
=== exec probe exit: 0 ===
```

The probe used `--help`, so it proved systemd can exec the path without starting the 3.8h chain. Timer remains enabled, `Persistent=true`, next fire **Mon 2026-07-27 01:00 +03** (the operator's revised time). Production `reports/dss/scheduler_state.json` still does not exist, so Monday will correctly see "no session reported yet".

---

## 1. STEP 1 — Forensic Classification

### Verdict: **(a) METHODOLOGY.** Two independent harness defects, both in the Phase 5D comparison. No strategy-logic violation.

**(b) BEHAVIOR CHANGE is ruled out by direct evidence.** Today's code, unmodified, reproduces the frozen number exactly:

| Variant | symbols | net_return | trades | win% | max_dd |
|---|---|---|---|---|---|
| **A — guard-exact harness** | 178 | **165.91** | **762** | 50.5 | -20.47 |
| B — guard harness, frozen 177 | 177 | 165.40 | 771 | 49.5 | -19.15 |
| C — guard harness, `fast`/`quick=True` | 178 | 165.91 | 762 | 50.5 | -20.47 |
| D — Phase 5D style, frozen 177 | 177 | **167.24** | **771** | 49.7 | -19.15 |
| E — Phase 5D style, 178 | 178 | 167.64 | 762 | 50.7 | -20.47 |

Variant A reproducing 165.9133/762 on current HEAD is only possible if no commit altered strategy output. **Phase 5D's "intervening commits (2b56a58, 62f433b)" hypothesis is therefore falsified** — it was never verified, and it was wrong.

**(c) STALE CACHE is ruled out empirically.** Forcing a rebuild changes nothing:

| | net_return | trades | rebuilt |
|---|---|---|---|
| `force_rebuild=False` | 165.91 | 762 | False |
| `force_rebuild=True` | 165.91 | 762 | True |

The cache is also better protected than the task brief assumed: beyond the key (which includes `PREPARED_CACHE_VERSION`, dirs, window, symbols, `legacy_intraday`), `_load_or_build_prepared_cache` compares each source CSV's mtime against the pickle's and forces a rebuild if any file is newer (`runner.py:1668-1678`). Verified directly — appending a session to a copied dataset caused the next load to pick it up. **No latent stale-cache bug; the nightly refresh cannot silently reuse yesterday's features.**

### The two actual causes, isolated one variable at a time

**Cause 1 — symbol set (drives trade count and drawdown).** The SIP `daily/` directory holds **178** series; the frozen universe is **177**. The extra is **BK**, retired from the universe in Phase 3B but still on disk. The guard globs `*_1D.csv`, so it silently measures 178 symbols.

- 178 (incl. BK) → 762 trades, −20.47 DD — variants **A and E**
- 177 (frozen) → 771 trades, −19.15 DD — variants **B and D**

Two independent harnesses agree, so the factor is isolated, not confounded.

**Cause 2 — end-date slice dropped the final session (drives return).** Phase 5D sliced with `index <= pd.Timestamp("2026-07-17", tz="UTC")`. Bars are stamped at NY-midnight-in-UTC — 04:00Z in EDT — so that bound resolves to 00:00Z and **excludes the 04:00Z bar of the very session it names**. Demonstrated:

| Slice | AAPL rows | net_return | trades |
|---|---|---|---|
| `<= 2026-07-17T00:00Z` (Phase 5D) | 1500 | 167.64 | 762 |
| `<= 2026-07-17T23:59Z` (inclusive) | 1501 | **165.91** | 762 |
| no slice (guard) | 1501 | **165.91** | 762 |

An inclusive bound converges exactly on the guard. Combining both causes reconstructs each reported figure: guard = 178 symbols + full data = 165.9133/762; Phase 5D = 177 symbols + truncated final session = 167.24/771.

**Systemic footnote worth naming:** this is not confined to the Phase 5D script. Production's own `runner._slice_frame` uses the same `index <= Timestamp(end_date)` comparison, so *any* caller passing a bare `"YYYY-MM-DD"` end date silently loses the final session. I did not change `_slice_frame` (it is shared strategy-path code and altering it could move results — forbidden here); instead the new baseline builder passes an explicit end-of-day bound and asserts the target session survived. Flagged for an operator decision.

Two hypotheses I held and the evidence killed, recorded because they were plausible and wrong:
- *`attrs["is_daily"]` / resample path*: frames lacking the attribute do take a `resample("1D")` branch, but marking them made **zero** difference (167.24 either way). Not a factor.
- *`run_profile`/`quick_mode`*: inert for this strategy (variant C identical to A).

### Which number is the true frozen strategy

For **SIP**, on the 178-series on-disk set, **165.9133% / 762** is correct and remains reproducible. **167.24% / 771 was an artifact** of a truncated final session on a 177-symbol subset and should not be cited. Since I authored the Phase 5D report, I'll be explicit: its absolute SIP and Tiingo figures are both affected by the truncation, though its SIP-vs-Tiingo *delta* remains meaningful because both sides carried the identical flaw.

---

## 2. STEP 2 — New Governing Baseline on rs_v2_tiingo

Computed with the **production harness** (`_load_or_build_prepared_cache` → `_run_relative_strength_rotation_backtest`), the same path the guard uses, over a fixed reproducible window. Artifact: `testdata/baselines/rs_v2_tiingo_strategy_baseline.json` (new file; the SIP-era baselines are untouched).

| Field | Value |
|---|---|
| Window | 2020-07-27 → **2026-07-21** (upper bound `2026-07-21 23:59:59`, target session asserted present) |
| Universe | 177, hash `026f66d1…` (Tiingo `daily/` contains exactly the frozen 177 — no BK contamination) |
| **Net return** | **175.49 %** |
| **Trade count** | **770** |
| CAGR | 18.46 % |
| Max drawdown | −18.97 % |
| Sharpe | 0.53 |
| Win rate | 50.1 % |
| Rebalance dates | 67 (2021-01-29 → 2026-07-21) |
| **Selection fingerprint** | `ba697e9653db37bdf348af1abc50de3366fe2cfbd26a271498cb31359fcada73` |

The fingerprint is SHA-256 over the ordered per-rebalance Top-30 sets (symbols sorted within each date, dates ascending), so a single symbol changing on a single rebalance date breaks it — drift is caught at selection level, not merely when it happens to move the headline return.

### Guard strategy — decision and rationale

**Keep the SIP guard, relabelled. Do not retire it.** It still tests something real: that the daily-only execution path reproduces the pre-conversion result bit-for-bit on unchanged inputs. That property is not decorative — it is exactly the control that let this forensic rule out a behaviour change. Retiring it would delete the only anchor to pre-Tiingo behaviour. Its numbers are no longer *governing*, but they remain *true* for what they measure.

What I changed (no pinned value altered): a docstring stating plainly that it is historical, superseded by the Tiingo guard, and computed on SIP data carrying stale spinoff adjustments — plus two assertions pinning the measured scope:

```python
self.assertEqual(len(symbols), 178, "SIP on-disk file set changed; frozen numbers below no longer apply")
self.assertIn("BK", symbols, "retired BK absent; this is no longer the historical 178-series set")
```

That converts the silent 178-vs-177 trap into an explicit contract: if the file set ever changes, the test says so instead of quietly measuring something else.

**New governing guard:** `tests/test_rs_v2_tiingo_strategy_baseline.py`, 6 tests, tight tolerances — trade count and fingerprint exact, metrics to stored precision, no ranges.

| Test | Pins |
|---|---|
| `test_universe_is_exactly_the_frozen_177` | symbol set == frozen 177 (BK-style contamination cannot recur) |
| `test_window_retains_the_target_end_session` | the 2026-07-21 session survives the slice (Cause 2 cannot recur) |
| `test_frozen_headline_metrics` | return, trades, CAGR, DD, Sharpe, win rate |
| `test_selection_fingerprint_matches` | exact SHA-256 |
| `test_fingerprint_fails_on_price_perturbation` | **proof of non-vacuity** — ×1.5 on one symbol's Close must break the fingerprint |
| `test_fingerprint_fails_on_universe_perturbation` | dropping one candidate must break it |

Both perturbation tests pass, i.e. the guard demonstrably fails on drift rather than passing trivially. Perturbations are in-memory copies; no dataset file is touched.

---

## 3. Which Number Now Governs

> **175.49 % net return / 770 trades**, on rs_v2_tiingo over 2020-07-27 → 2026-07-21, selection fingerprint `ba697e96…`, is **the reference figure for this system.**

The SIP-era **165.9133 % / 762 is superseded**. Not because it was miscomputed — it is exactly reproducible — but because rs_v2_sip carries **stale spinoff adjustments** for FDX, CMCSA, MMM and MDT (Phase 6A: FedEx Freight, Versant, Solventum, MiniMed; Tiingo applied them retroactively, Alpaca SIP did not). Its prices no longer reflect those corporate actions, so returns derived from them describe a security history that did not happen. Production runs on rs_v2_tiingo, and the governing baseline must be measured on the data the system actually trades from.

---

## 4. Hard-Rule Compliance

- Strategy/ranking/benchmark logic unchanged. `_slice_frame` deliberately **not** modified despite the trap it carries — flagged instead.
- Frozen 177 universe/hash unchanged; validators and the DSS freshness check untouched.
- rs_v2_sip / rs_v2_clean source data unmodified. *Disclosure:* forensic variants wrote derived `.cache/prepared_features/*.pkl` entries under the SIP dataset. That is regenerable cache, not source data; the dataset re-validates clean.
- Archived decision events untouched.
- No `--live` acquisition; zero Tiingo API calls. No broker/order/live-trading calls.
- Credentials: no `TIINGO_API_KEY` or `TELEGRAM_*` value read, echoed, or logged.
- Systemd timer: not disturbed in schedule or state — **repaired** from a pre-existing non-executing condition, as documented in §0.

## 5. Open Items

1. **`_slice_frame` end-date trap** (§1): any caller passing a bare `"YYYY-MM-DD"` silently drops that session. A fix would touch shared strategy-path code and shift results, so it needs an explicit operator decision.
2. **Monday 2026-07-27, 01:00 TR** is the first real nightly run, and the first end-to-end exercise of a service that has never completed one.
3. **The governing guard skips silently without the dataset** (raised by `/code-review`, CONFIRMED). `testdata/research_data/` is gitignored (`.gitignore:45`), so on a clean clone all 6 Tiingo guard tests skip and the suite still reports `OK` — the governing guard would be inactive in any environment lacking the 27 MB dataset, including CI. The committed baseline artifact cannot be re-verified there either. I did not change skip→fail unilaterally, because that would break every developer environment without the data and matches the existing SIP guard's convention. Options: keep as-is and rely on this machine, gate CI on dataset presence, or commit a small fixture slice. Operator decision.
4. The Tiingo baseline is pinned to the 2026-07-21 window. After the refresh extends the dataset, the baseline stays valid because the window is explicit — re-freeze only if the strategy or universe deliberately changes, via `python3 scripts/build_tiingo_strategy_baseline.py`.

---

*No GO/NO-GO — evidence and state only.*
