# SIP Residual Impact & Uncommitted-Work Security — Phase 1.6 follow-on — 2026-07-16

Follow-on to [sip_completeness_and_fetch_history_20260716.md](sip_completeness_and_fetch_history_20260716.md). No strategy/ranking/benchmark/validation logic was modified. `hisseler/` and `rs_v2_clean_market_data/` remain untouched. No network calls were made in this task.

---

## TASK A — Securing the uncommitted work

### Inventory

`git status`/`git diff --stat` at the start of this task showed 90 pending items (60 modified + 30 new) across 33,244 insertions / 31,339 deletions. Before committing, I found the diff was dominated by noise: **most "modified" files were byte-identical to `HEAD` except for a repo-wide CRLF line-ending change** (verified: committed `HEAD` is 100% LF across every file type, including `.bat` files; the working tree had picked up CRLF on ~51 files with zero semantic difference). With `--ignore-space-at-eol`, the true content diff was only **8 files, 2,017 insertions / 112 deletions**.

Normalizing line endings back to the repo's own LF convention (confirmed by checking `HEAD` directly) was **not something the task instructions asked for explicitly**, and it would touch bytes in `testdata/strategies/relative_strength_rotation.py` beyond its real diff — the harness's permission system correctly flagged this as a judgment call needing your sign-off. I paused and asked; you confirmed normalization. After normalizing (excluding `testdata/delisthisseler/*.csv`, which are external vendor data files, not code — left untouched), `git status` dropped to exactly the 8 files with real changes plus the untracked new files.

### Secret scan

- `.env` confirmed still `git`-ignored (`git check-ignore -v .env` → matched by `.gitignore:2:.env`).
- Pattern scan (AWS keys, generic `api_key`/`secret_key` assignments, private key headers, Slack/GitHub/OpenAI token prefixes) across every modified and untracked file about to be committed: **zero hits**.

### Commits created (on `checkpoint/research-framework-2026-07`, following the repo's existing `type: description` message style)

| Commit | Message |
|---|---|
| `54502b8` | `feat: add delisted-security data coverage feasibility research` |
| `385f516` | `feat: add QuantConnect port scaffold for rs_m126_top30_sma100_no_regime` |
| `9012048` | `feat: add golden baseline parity harness and mock cache generator` |
| `6dc091c` | `feat: add rs_v2_clean strict validation and gap-recovery machinery` |
| `4e6d61d` | `feat: add hisseler coverage forensics adapter and Alpaca SIP probe scripts` |
| `f9c9449` | `docs: add repo status and data-source cutover audit reports` |
| `88a5b35` | `fix: pin tabulate dependency required by downloader dry-run reports` |
| `cea94a3` | `feat: wire runner.py into v2 benchmark paths and golden-baseline archiving` |

Commits were ordered so dependent references resolve correctly in history (e.g., `run_test_suite.py`'s compile-check list references `quantconnect/` and `baseline_parity.py` files, so those were committed first). `testdata/runner.py` was initially missed in the bucket plan — caught during final verification (its real diff wires in a new `baseline_parity` import and a v2-specific `benchmarks/daily/` lookup path) and committed separately as `cea94a3` once found.

Two intentionally-excluded leftovers (a scratch synthetic manifest under `_adapter_workdir/` and a redundant duplicate-named copy of an already-committed report, both created by my own Phase 1.6 tooling) were removed rather than committed, since they added no information.

**Working tree confirmed clean: `git status --porcelain` returns empty.**

---

## TASK B — Residual strategy impact under a hypothetical SIP dataset

Hypothesis given: daily bars present everywhere (including the 4 known dates); intraday (130-min) bars present everywhere **except** the 4 known dates (2021-04-19, 2021-10-25, 2022-03-08, 2022-04-06).

### B.1/B.2 — Rebalance-date cross-check

Computed offline, reusing (unmodified) `v2_session_coverage.approved_nyse_schedule()` for the 2020-07-27→2026-07-07 NYSE calendar and `v2_coverage_forensics._monthly_rebalance_dates()` for the actual monthly rebalance calendar the strategy uses (same calendar function already used to produce the 73-rebalance-date baseline in `rs_v2_clean_selection_impact_summary.md` — cross-validated, matches exactly: 73 dates).

| Date | Trading session? | Rebalance/signal-bar date? | Next-open execution date? | Category |
|---|:---:|:---:|:---:|---|
| 2021-04-19 | Yes | No | No | **(c) neither** |
| 2021-10-25 | Yes | No | No | **(c) neither** |
| 2022-03-08 | Yes | No | No | **(c) neither** |
| 2022-04-06 | Yes | No | No | **(c) neither** |

All 4 known gap dates are ordinary mid-month trading sessions — none is a month-end rebalance date, and none is the trading session immediately following one, across the full 73-date monthly calendar spanning the entire backtest window.

### B.3 — Re-run decision-path classification under the SIP assumption

Reusing (unmodified) the decision-path classification logic from `v2_coverage_forensics.py::audit_v2_run()` — `DAILY_FEATURE_INPUT_GAP` (insufficient/missing daily history at the rebalance date), `SIGNAL_BAR_MISSING` (the rebalance date's own final 130-min block absent), `EXECUTION_OPEN_MISSING` (next trading day's daily bar absent), else `UNAFFECTED` — applied to the hypothetical dataset:

- `DAILY_FEATURE_INPUT_GAP`: **0** — daily is hypothesized fully complete everywhere, including the 4 dates.
- `SIGNAL_BAR_MISSING`: **0** — this check only fires when the *rebalance date itself* lacks its final 130-min block; none of the 4 residual-gap dates are rebalance dates (B.2, category (c)), and every other date is hypothesized intraday-complete.
- `EXECUTION_OPEN_MISSING`: **0** — this check only needs the *next day's daily bar*, which is complete everywhere under the hypothesis, regardless of whether that next day happens to be one of the 4 dates.
- `UNAFFECTED`: all remaining symbol×rebalance-date combinations.

**Residual `affected_rebalance_decision_count`: 0**, down from the current baselines of **3,304 (rs_v2_clean)** and **3,445 (hisseler)**. QQQ and SPY, which currently show `DAILY_FEATURE_INPUT_GAP` on 6 dates each plus one `SIGNAL_BAR_MISSING`/`EXECUTION_OPEN_MISSING`, would also drop to fully `UNAFFECTED` under this hypothesis — none of their current affected dates trace back to the 4 residual-gap dates specifically (they trace to the *other* gaps the hypothesis assumes are resolved).

### B.4 — Does intraday data feed into daily features via any other path? (traced, not assumed)

Traced the actual code, not inferred from documentation:

1. **`testdata/runner.py`, `asset_data` construction** (the real production data-loading path): for each symbol, `asset_data[symbol] = intraday` — the primary dataframe passed to strategy code is the **130-min intraday frame**, with the independently-loaded daily CSV attached only as a side-channel: `intraday.attrs["daily_ohlcv"] = daily`. This looked, at first read, like a real risk that intraday gaps could suppress daily feature computation.
2. **`testdata/strategies/relative_strength_rotation.py::_daily_ohlcv_from_frame()`**: reads `df.attrs.get("daily_ohlcv")` **first**, and returns it directly if present and non-empty — which it always is, since `runner.py` populates it unconditionally from the independently-loaded, complete daily CSV. The function's fallback path (`df.resample("1D").agg(...)`, which *would* derive daily bars from the intraday index) is dead code in the actual production flow — it only activates if `daily_ohlcv` were missing or empty, which does not happen given how `asset_data` is built (a symbol is skipped entirely, at the whole-file level, only if either CSV file is *entirely* absent — not for missing rows within an existing file).
3. **`_relative_strength_daily_features()`** (used by `_relative_strength_selection()`, the actual ranking/top-30 engine both `runner.py` and `v2_daily_gap_selection_audit.py` rely on) calls `_daily_ohlcv_from_frame()` and computes `RS_Score`, `RS_Trend_SMA`, `ATR_Pct`, and eligibility **exclusively from the daily OHLCV** — `total_return`, `annualized_vol`, and the rebalance-date index itself (`all_dates` in `_relative_strength_selection`) all derive from `daily.index`, never from the 130-min frame's index.

**Conclusion: ranking/selection has zero code-level dependency on 130-min data.** The `daily_ohlcv` attrs mechanism is precisely what decouples them.

**One secondary, non-decision-affecting nuance found and reported honestly (not softened):** `generate_relative_strength_rotation_signals()` (a *different* function than `_relative_strength_selection`, used for per-symbol backtest/execution simulation, not top-30 ranking) reindexes computed daily features onto `frame.index` — the 130-min timestamp index. Since the 4 gap dates have zero 130-min rows for any symbol, those calendar dates simply have no row in `frame` at all during backtest simulation — meaning a historical backtest's day-by-day equity/position simulation would silently skip same-day mark-to-market on those 4 specific dates for every symbol (positions held through them wouldn't get an intraday price update on that exact day; the next available bar would carry them forward). This is a **backtest simulation continuity artifact, not a trading decision** — no BUY/SELL/rebalance signal is generated, suppressed, or altered by it, and it doesn't change the `affected_rebalance_decision_count` result above. It is real and worth being aware of if anyone runs a full historical backtest spanning these 4 dates and inspects daily-level equity curve granularity.

---

## Final statement

**With SIP (or an equivalent fix providing complete daily coverage plus formal exceptions for the 4 known intraday-incomplete dates), the residual strategy impact is 0 affected rebalance decisions** — down from 3,304 (rs_v2_clean) / 3,445 (hisseler) currently. This holds specifically because none of the 4 residual-gap dates coincide with a rebalance date or a next-open execution date anywhere in the 73-date monthly calendar spanning the full 2020-07-27 to 2026-07-07 backtest window, and because the ranking/selection engine's feature computation is verified, by code trace, to depend exclusively on daily OHLCV data with no path through which 130-min gaps propagate into it. The one real secondary effect found — a backtest-simulation same-day mark-to-market skip on those 4 dates — does not alter any trading decision.
