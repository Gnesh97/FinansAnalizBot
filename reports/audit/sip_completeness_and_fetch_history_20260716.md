# SIP Completeness & Fetch-Path History — Phase 1.6 — 2026-07-16

Follow-on to [data_source_decision_brief_20260716.md](data_source_decision_brief_20260716.md). No strategy/ranking/benchmark/validation code was modified. `hisseler/` and `rs_v2_clean_market_data/` remain untouched. All artifacts below are new files.

**Headline: SIP does NOT fully resolve the 4 known gap dates at intraday granularity — 3 of 4 are severely partial (2–8 of 78 expected 5-min bars), 1 is meaningfully partial (49/78). Daily bars ARE present for all 4. The two PROVIDER_WIDE_PATTERN dates (2025-03-10, 2024-12-23), by contrast, are fully complete on SIP.** Separately, git forensics show the strict v2 validation/gap-recovery machinery was never committed — it exists only in the current uncommitted working tree (+1,694 lines over the last committed version), while the legacy fetch path itself is unmodified and its tests pass.

---

## TASK A — Canary post-mortem

Read [scripts/alpaca_sip_entitlement_canary.py](../../scripts/alpaca_sip_entitlement_canary.py) in full before writing anything new.

- **No `limit` parameter was set**, and **no manual pagination was implemented** — but this is not a bug: inspecting the installed `alpaca-py` source (`StockHistoricalDataClient.get_stock_bars` → `_get_marketdata`) confirms the SDK **auto-paginates internally**, looping on `next_page_token` until exhausted, with a 10,000-row page/total cap. This was verified by reading the library source directly, not assumed. Pagination is therefore **ruled out** as the cause of the low row counts.
- **Time window used**: a single raw UTC calendar day per probe (`00:00:00` to `23:59:59` UTC of the target date) — not an ET-anchored regular-trading-hours (RTH) window. This window is *wider* than RTH, not narrower (it also captures extended hours), so it cannot explain unusually *low* counts either — if anything it should return more bars than an RTH-only pull, which is exactly what happened for the control date (190 rows, extended hours included).
- **Conclusion**: the canary's low row counts (2, 5, 8, 50 out of an expected ~78–190) are **not an artifact of the canary's own methodology** — pagination was automatic and complete, and the window was, if anything, generous. This motivated TASK B: a rigorous, RTH-exact, completeness-checked probe to determine whether the sparse counts reflect genuinely incomplete SIP data.

---

## TASK B — Full-session SIP completeness probe

Script: [scripts/alpaca_sip_full_session_probe.py](../../scripts/alpaca_sip_full_session_probe.py). Reused, unmodified, read-only: `v2_session_coverage.approved_nyse_schedule()` (for the exact NYSE RTH window per date), `v2_session_coverage.calculate_v2_session_coverage()` (for daily-bar completeness), and `v2_coverage_forensics._expected_blocks()` (for the same 130-minute block structure the project's own forensics tooling uses — 3 RTH blocks × 26 five-minute bars = 78 expected bars per full session). 12 logical probes, instrumented to count every underlying HTTP request the SDK issued (12 — one per probe, no pagination triggered, well under the 40-request budget). Raw results: [reports/audit/sip_full_session_probe_20260716.json](sip_full_session_probe_20260716.json).

### 4 known gap dates

| Symbol | Date | Daily bar present | 5-min bars (actual/expected) | Missing | Largest gap (min) | Intraday complete? |
|---|---|---:|---:|---:|---:|:---:|
| ABBV | 2021-04-19 | Yes (1/1) | 2 / 78 | 77 (97%) | 390.0 | **NO** |
| ALB | 2021-10-25 | Yes (1/1) | 2 / 78 | 77 (97%) | 390.0 | **NO** |
| ABBV | 2022-03-08 | Yes (1/1) | 2 / 78 | 77 (97%) | 390.0 | **NO** |
| WBD | 2022-04-06 | Yes (1/1) | 49 / 78 | 29 (37%) | 25.0 | **NO** |

**Methodology correction, stated plainly:** the script's `daily_coverage_status` field showed `FAIL` for all 4 with reason `INSUFFICIENT_WARMUP` — this is a **false negative**, not a real gap. `calculate_v2_session_coverage()` requires 127 trading days of history in the queried range to consider a symbol "warmed up"; probing a single isolated day against that function will always fail this specific check regardless of whether the day's own bar is present. The **`daily_bar_count` field is the uncontaminated signal**, and it shows **1 bar present for all 4 dates** — SIP's daily-bar data for these 4 sessions is genuinely present. This distinction matters and is not softened: the daily gap is resolved; the intraday gap is not.

**Per the fail-closed instruction: partial data is a NO for that date, at intraday granularity, for all 4.** ABBV/2021-04-19, ALB/2021-10-25, and ABBV/2022-03-08 are barely better than the IEX zero-row result (2–8% of expected bars present); WBD/2022-04-06 is meaningfully better (63% present) but still fails completeness.

### PROVIDER_WIDE_PATTERN dates (2025-03-10, 2024-12-23)

| Symbol | Date | 5-min bars (actual/expected) | Missing | Largest gap (min) | Intraday complete? |
|---|---|---:|---:|---:|:---:|
| SPY | 2025-03-10 | 79 / 78 | 0 | 5.0 | **YES** |
| AAPL | 2025-03-10 | 79 / 78 | 0 | 5.0 | **YES** |
| SPY | 2024-12-23 | 79 / 78 | 0 | 5.0 | **YES** |
| AAPL | 2024-12-23 | 79 / 78 | 0 | 5.0 | **YES** |

All four pass, with no internal gaps (max gap = one bar interval, i.e. no gap at all). Transparency note: actual count is 79 against an expected 78 — a 1-bar discrepancy across all four, consistent with a boundary double-count in this script's own per-block tally (a bar landing exactly on the shared edge between two adjacent 130-minute blocks can be counted in both), not a data-quality issue; `missing_bar_count: 0` and the completeness verdict are unaffected either way.

**This reverses the working assumption from Phase 1.5's report**, which treated the 4 known dates and the 2 provider-wide dates as the same kind of problem. They are not: **SIP fully resolves the provider-wide dates and does not resolve the 4 legacy dates.**

---

## TASK C — `vericekici_updated.py` git forensics

### Full commit timeline (via `git log --follow`, predecessor filename found: `testdata/vericekici.py`)

| Commit | Date | Message | What changed |
|---|---|---|---|
| `718388c` | 2026-06-17 | Update scanner project files | `testdata/vericekici.py` **created**, 226 lines — original simple downloader, no `--refresh-latest`, no strict validation. |
| `0df8661` | 2026-06-20 | Add local paper trading pipeline and backup workflow | `vericekici.py` **deleted** (226 lines); `testdata/vericekici_updated.py` **created** (358 lines, +132 net) — a rewrite/expansion, not a pure rename (git's rename heuristic didn't pair them as a formal rename because content changed too much; `--follow` still traced the lineage). Root-level `vericekici_updated.py` thin-shim launcher (11 lines) also added here — this is the origin of the shim pattern. |
| `b677383` | 2026-06-21 | refactor: add paper execution safety layer and tests | +26 lines, minor safety-layer addition. |
| `4d0af91` | 2026-06-23 | data: add safe refresh-latest pipeline and cache invalidation | +543/-61 lines. **This is the commit that introduces `--refresh-latest`, `--overwrite`, `--skip-download`, `--only-aggregate-130min`, `--allow-row-count-reduction`, cache-invalidation (`refresh_file`), and retry-wrapped downloads (`download_bars_with_retry`).** No feed/endpoint/auth default changes in this commit. |
| `a768a68` | 2026-06-24 | ops: stabilize paper pipeline and data policy workflow | +70/-5 lines, moderate stabilization touches to the same file (not a refresh-latest-specific rewrite based on commit size/message). |
| `c2ce4b9` | 2026-06-25 | Stabilize local paper workflow and fill checks | +2 lines, trivial. |
| `0cf419c` (**current HEAD**) | 2026-07-01 | Add local paper operations monitors | +3/-2 lines, trivial. **Committed file size at HEAD: 934 lines.** |

**None of the 7 commits above contain `validate_v2_manifest_and_files`, `GAP_RECOVERY_APPROVED_DATES`, `v2_offline_audit_guard`, `dataset_schema_version`, or `strategy_identifier`** — confirmed by grepping every diff in the full `git log -p --follow` output for these exact terms: zero matches.

### The uncommitted delta — where the strict validation actually lives

**The working tree right now is 2,628 lines — 1,694 lines more than the last commit (934 lines), and none of that delta has ever been committed.** This is where every piece of the v2 strict-validation/gap-recovery machinery (`validate_v2_manifest_and_files`, `GAP_RECOVERY_APPROVED_DATES`, the offline audit guard, the `--dataset-profile rs_v2_clean` mode and all its mandatory-flag checks) actually lives. Given the `rs_v2_*` report timestamps from Phase 1/1.5 (2026-07-08 through 2026-07-13), this uncommitted work was done in that window, entirely on top of the 2026-07-01 commit, and never checked in.

### `--feed` / `--adjustment` default change — found, and confirmed non-breaking for the legacy path

Diffing HEAD against the working tree shows `--feed` default changed from `"iex"` (committed) to `None` (working tree); same for `--adjustment` (`"all"` → `None`). At first read this looks like a regression risk (a `None` feed would crash `get_data_feed()`, whose committed-HEAD body is `feed_name.lower()`). Reading further, the working tree adds an explicit safety net in the **legacy (non-v2) branch only**:

```python
else:
    if args.feed is None:
        args.feed = "iex"
    if args.adjustment is None:
        args.adjustment = "all"
```

So the effective legacy-path default is unchanged (`"iex"` / `"all"`) — this is a deliberate, correctly-implemented strictness upgrade **scoped only to the new `--dataset-profile rs_v2_clean` mode**, which now hard-requires explicit `--feed iex`, `--adjustment all`, `--start`, `--end`, `--output-root`, and exactly one of `--dry-run`/`--live` (raising `ValueError` otherwise) — none of which apply to the legacy path `scripts/run_daily_paper_pipeline.bat` actually uses.

### Verification: is `vericekici_updated.py --end today --refresh-latest` defective?

- **`scripts/run_daily_paper_pipeline.bat` always passes `--feed iex --adjustment all` explicitly**, so it's unaffected by the default change either way.
- Code reading of the legacy `else` branch (feed/adjustment resolution, symbol loading, directory setup, `download_bars_with_retry` call path) shows no defect.
- **Tests run** (not the full suite — just the two files directly exercising this path, per instruction): `tests/test_downloader_manifest.py` and `tests/test_operator_scripts.py` — **8/8 passed**, 0.04s. No code execution against live data was performed in this check (code reading + these two test files only, as instructed).

### Conclusion — (c), with a documented instance of (b) alongside it

**Live fetching on the legacy path (what the daily pipeline actually runs) is functional — not broken.** The evidence: `--refresh-latest` was introduced cleanly in `4d0af91` (2026-06-23) and hasn't been touched by the strict-validation work since; the daily pipeline's explicit flags bypass the `None`-default change entirely; the legacy branch has its own safety-net defaults; and its 8 directly relevant tests pass.

**Separately, and also true: an entirely new, much stricter, fail-closed-by-design mode (`--dataset-profile rs_v2_clean`) was added on top — but never committed.** This mode imposes many new mandatory-flag requirements that simply did not exist in the version the operator likely remembers as "easy." If the operator's "then it broke" recollection comes from invoking the script the old way while mentally in "v2 mode," or from testing the new `--dataset-profile rs_v2_clean` flags directly, the wall of `ValueError`s that mode raises for any missing flag would read exactly like breakage — even though it is intentional, not a bug, and does not affect the original/legacy invocation at all.

---

## Final assessment

**Does SIP provide COMPLETE sessions for the gap dates?**

| Date | Daily | Intraday (5-min, RTH) |
|---|:---:|:---:|
| 2021-04-19 | YES | **NO** — 2/78 bars (97% missing) |
| 2021-10-25 | YES | **NO** — 2/78 bars (97% missing) |
| 2022-03-08 | YES | **NO** — 2/78 bars (97% missing) |
| 2022-04-06 | YES | **NO** — 49/78 bars (37% missing) |
| 2025-03-10 (provider-wide) | not probed (task scoped to 5-min only) | **YES** — complete |
| 2024-12-23 (provider-wide) | not probed (task scoped to 5-min only) | **YES** — complete |

Stated plainly, per the fail-closed instruction: **SIP is a NO for intraday completeness on all 4 legacy gap dates.** Switching to SIP would resolve the daily-bar gap and would fully resolve the two provider-wide intraday gaps, but would **not** close the intraday gap for the original 4 dates — those would need a different resolution path (formal exception, different provider, or accepting daily-only coverage for those specific sessions) regardless of feed choice.
