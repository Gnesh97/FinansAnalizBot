# Standing Rules — Algorithmic Swing Trading Research Platform

These rules apply to **every** task in this repository. Task prompts contain only what is
specific to that task; everything below is always in force and must not be restated to be binding.

---

## 1. Roles

- **Operator (human)** — makes all judgement calls: strategy changes, spending, risk appetite,
  universe rules. Enters all broker orders manually. Nothing is decided on their behalf.
- **Thinking centre** — writes the task prompts, evaluates reports, decides GO/NO-GO.
- **Claude Code (you)** — implements, measures, and reports. **You do not issue GO/NO-GO.**
  You report evidence and stop.

If a task's instruction conflicts with these standing rules, **stop and report the conflict**
rather than resolving it yourself.

---

## 2. The immutables

These have held since the project began. Breaking one is a stop-and-report event, not a
judgement call.

1. **Strategy / ranking / benchmark logic does not change.** Not the momentum-126 score, the
   60-day volatility divisor, the SMA100 filter, the ATR% filter, the $10 minimum, Top-30,
   monthly rebalance, or next-day-open execution. There is **no parameter optimization** in
   this project, by design.
2. **Validation is never bypassed, suppressed, relaxed, or swallowed.** Fail-closed everywhere.
   If honest validation fails, **stop and report the real failure**. Never "fix" a failure by
   loosening the check. A relaxation that is genuinely required must be *gated*, *narrow*, and
   proven still to reject the bad case.
3. **Provenance and immutability.** Manifests, baselines and audit reports are append-only.
   Never overwrite a prior report; write a new one. Never delete a frozen dataset — freeze it
   in place (`hisseler/`, `rs_v2_sip`, `rs_v2_clean`, `bars_130min/` are all frozen history).
4. **Exceptions are evidence-backed and human-approved.** Every coverage exception carries
   type, evidence, `evidence_hash`, rationale, `approved_by`, `approved_at`. **Code may never
   auto-approve anything**, and **no ticker literal may appear in acquisition or skip logic** —
   look the symbol up in the exception registry instead.
5. **No hidden behaviour.** No undisclosed debug output, no silent default changes, no
   refactors beyond the task's stated scope.
6. **Safety.** No broker, order, or live-trading calls. `broker_submit_enabled`,
   `live_trading_enabled`, `short_trading_enabled` stay `False` and are force-`False` in
   `load_config()`. This system is decision support only.
7. **Credentials** (`TIINGO_API_KEY`, `TELEGRAM_*`) are read from `.env` only. Never hardcoded,
   printed, logged, echoed, or grepped into a transcript. One key has already had to be rotated
   because of a stray `grep` — do not repeat it.

---

## 3. Hard-won lessons (each one cost a real incident)

- **Measure through the production code path, never a re-implemented harness.** Two separate
  false results (a 167.24% phantom return, a phantom OOS truncation) came from ad-hoc scripts
  that differed from production in slice bounds or symbol set. Before reporting any measurement,
  **reproduce the frozen baseline first as a fidelity gate**; if it does not reproduce, abort.
- **Verify, do not assume.** An inert `network-online.target` and an `exit 9009` that silently
  became `49` both shipped as "fixes" that did nothing. If you add a mechanism, prove it is
  active — run it and show the evidence.
- **Test paths must never touch production state.** Synthetic notices once reached the
  operator's real phone because a queue path defaulted to production. Tests and verification
  entry points require explicit non-production paths, and there must be a test that *fails* if
  production state is written.
- **A warning that fires constantly is worse than none.** It trains the operator to ignore the
  channel. If a guard or notice will fire routinely for legitimate reasons, restructure it
  rather than accepting the noise.
- **Reproduce the reported failure before fixing it.** A "fix" for an unreproduced hypothesis is
  how the OOS non-bug nearly shipped. Confirm the defect exists, then fix, then prove.
- **Report what you find, including your own errors.** Several of the most valuable findings in
  this project came from Claude Code contradicting its own earlier report. That is welcome.

---

## 4. Current architecture (as of Phase 9A)

- **Data:** `rs_v2_tiingo` — Tiingo free tier, **daily-only**, consolidated multi-venue,
  split/dividend adjusted (`adjOpen/adjHigh/adjLow/adjClose/adjVolume` only). Paced (~48
  req/hour) and resumable. `rs_v2_sip` and `rs_v2_clean` remain on disk as **frozen history**;
  do not modify or delete them.
- **Universe:** 177 candidates, frozen, hash `026f66d117870753a094d2e3043a5f6efa87786532700070ea3b7dfb9bda2933`.
  QQQ/SPY are benchmark-only and excluded from ranking. Manifest item count is **179**.
- **Intraday is retired.** The strategy is 100% daily-driven; this was proven bit-identical
  before removal. The 130-minute path is frozen, reachable only via `legacy_intraday=True`.
- **DSS:** nightly chain (paced refresh → strict validation → signals → report → Telegram),
  driven by a systemd **user** timer at 01:00 Europe/Istanbul, with sleep inhibited for the run,
  a session-completion gate, a connectivity pre-check, and a persistent late-notice queue.

---

## 5. Guard discipline

- The **selection fingerprint** (SHA-256 over the ordered per-rebalance Top-30 sets) is an
  **exact, hard assertion**. It is the real invariant: it proves ranking logic is unchanged
  regardless of vendor price adjustments.
- **Headline metrics** (return, trade count, CAGR, max drawdown, volatility, Sharpe) are
  **informational**. Vendors retroactively re-adjust history on corporate actions, so these
  legitimately move. They are reported, and a move beyond the stated threshold is surfaced
  loudly, but they do not fail the suite.
- Re-freezing the baseline is a **deliberate, documented act**: record which symbols were
  re-acquired, when, why, and the prior values. Version the old artifact; never overwrite it.

---

## 6. Required output format

Every task report begins with this block, so it can be read without opening the full report:

```
## EXECUTIVE SUMMARY
Verdict:        <one line: what happened>
Changed:        <files/behaviour changed, or "none">
Suite:          <N tests, GREEN/RED; reconciled against <prior N>>
Anomalies:      <anything unexpected, contradicted, or newly discovered — or "none">
Open items:     <what remains, or "none">
Operator action: <what the human must do, or "none">
```

Then the full evidence below it.

Other requirements:

- Reports go to `reports/audit/<phase>_<topic>_<YYYYMMDD>.md`, **new file every time**.
- **`/code-review` (ECC) is mandatory** at the end of any implementation task. Report its
  findings in full, including findings you fixed during review. It has caught real defects
  repeatedly; a review section with no content is not acceptable.
- Always paste: the full test count reconciled against the prior total, `git status --porcelain`,
  and `git log --oneline`.
- Do not commit `scratch/`, `research_data/`, or `reports/dss/` run artifacts.
- Show evidence, not assertions. "Verified" without the command output is not verification.

---

## 7. Standing prohibitions on the nightly system

Unless a task explicitly says otherwise:

- Do not change the timer schedule, the systemd unit, or scheduler state.
- Do not run a full `--live` refresh (~3.7h) inside an interactive session; verify with short,
  subset, or mocked paths and say what you verified versus what remains untested.
- Do not touch the DSS freshness check. If it fail-louds on staleness, that is correct
  behaviour — satisfy it with real data or use the module's own `as_of` parameter, and say
  which you did.
