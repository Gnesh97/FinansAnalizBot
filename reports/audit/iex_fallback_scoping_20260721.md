# IEX (rs_v2_clean) Fallback — Scoping Report

**Date:** 2026-07-21
**Mode:** READ-ONLY. No code modified, nothing committed, no acquisition or `--live` run.
**Trigger:** the currently-active SIP feed is blocked by an Alpaca subscription entitlement issue (`"subscription does not permit querying recent SIP data"` — see `reports/audit/phase4a_dss_first_real_report_20260721.md`). This scopes what it would take to run the daily DSS pipeline on the IEX feed (`rs_v2_clean` profile) instead, while that's resolved.

No GO/NO-GO conclusion — evidence and an effort estimate only.

---

## 1. Profile state — does `rs_v2_clean` (IEX) still exist and function?

Yes. It is a fully separate, still-present profile in `testdata/vericekici_updated.py`, dispatched via `is_v2_clean = (args.dataset_profile == "rs_v2_clean")` (line 2254). Its own paths, exception file, and CLI enforcement all remain distinct from SIP:

```python
V2_COVERAGE_EXCEPTIONS_PATH = BASE_DIR / "baselines" / "rs_v2_clean_coverage_exceptions.json"   # line 99
DEFAULT_V2_FROZEN_ROOT = BASE_DIR / "research_data" / "rs_v2_clean_market_data"                  # line 106
```

Its acquisition-time enforcement block (`if is_v2_clean:`, line 2437):
```python
if args.feed != "iex":
    raise ValueError("V2 profile requires --feed iex")
...
if args.start != "2020-07-27" or args.end != "2026-07-07":
    raise ValueError("V2 profile target dates must be start=2020-07-27, end=2026-07-07")
```
— a fixed full-window acquisition only (matches its original, pre-SIP design; no incremental refresh was ever built for it — see §5).

**Phase 2A's "zero behavior change to rs_v2_clean" claim: confirmed still true for the *code path itself*.** The `is_v2_clean` branch, `validate_v2_manifest_and_files` (the shared clean validator), and `_load_gap_recovery_frozen_context` are all untouched by the SIP-cutover work. However, this report's own investigation (§3) found the **on-disk dataset** now fails strict validation — not because the clean code path was touched, but because the shared frozen-universe file it reads from *was* updated (BK retirement, Phase 3B) and the on-disk clean dataset was never re-acquired against it. See §2/§3.

## 2. Universe sync — did BK retirement propagate to `rs_v2_clean`?

**Yes, automatically — no separate constant needed updating.** Both profiles' validators call the exact same shared function:

```python
# validate_v2_manifest_and_files (the rs_v2_clean validator), line 218-233
candidates, baseline = load_v2_frozen_universe(V2_BASELINE_PATH)
...
exact = {
    ...
    "candidate_count": len(candidates), "candidate_universe_hash": baseline["universe"]["candidate_universe_hash"],
    "logical_acquisition_item_count": 358, "successful_item_count": 358, "failed_item_count": 0,
}
```

`load_v2_frozen_universe()` reads `testdata/baselines/rs_m126_top30_sma100_no_regime_v2_clean_stock.json` — the same file Phase 3B updated to 177 symbols / hash `026f66d1...`. `candidate_count`/`candidate_universe_hash` here are computed dynamically from that file, not hardcoded, so `rs_v2_clean`'s validator already expects 177/358 too. The `358`/`354` literals fixed in `runner.py`'s `validate_v2_clean_dataset` earlier this session (the stale-count-fix task) are in the **same shared function** used by both profiles — so that fix also already applies to `rs_v2_clean`.

**Conclusion: universe sync is a non-issue. Nothing to do here.**

## 3. Data on disk — where, current state, staleness

```
$ ls testdata/research_data/rs_v2_clean_market_data/manifests/
acq_20260712_212749/  latest_manifest.json  download_manifest.csv

$ cat .../manifests/latest_manifest.json
{"acquisition_run_id": "acq_20260712_212749", ...}
```

Reading the immutable manifest (no acquisition, just a file read):
```
status = COMPLETE_VALIDATED
candidate_count = 178          <- pre-BK-retirement
candidate_universe_hash = 0187534f...  <- OLD hash
logical_acquisition_item_count = 360   <- OLD count
target_start_date = 2020-07-27
target_end_date = 2026-07-07
feed = iex
num files: 360
```

This is a real, single, immutable run from **2026-07-12** — before both the BK retirement (Phase 3B, 2026-07-18/19) and the current date. It was validly `COMPLETE_VALIDATED` *at the time*, but is now stale on two independent axes:

1. **Universe mismatch**: 178/360/old-hash vs. the current 177/358/`026f66d1...` requirement.
2. **Date staleness**: `target_end_date=2026-07-07`, 14 days behind today, with no incremental-refresh mechanism available for this profile (§5).

**Confirmed by direct read-only re-validation** (calling the existing validator function against the on-disk folder, no acquisition):
```
$ python -c "from runner import validate_v2_clean_dataset; validate_v2_clean_dataset('testdata/research_data/rs_v2_clean_market_data')"
FAIL: V2_DATASET_NOT_VALIDATED: frozen contract mismatch
```

**The on-disk IEX dataset currently fails strict validation and cannot be used as-is.** A full re-acquisition against the 177-symbol universe would be required — the same class of work as Phase 3B's SIP baseline rebuild.

## 4. Known IEX gaps — status in the exception registry

```
$ cat testdata/baselines/rs_v2_clean_coverage_exceptions.json
{"exceptions": [], "schema_version": "1.0.0"}
```

**The `rs_v2_clean` exception registry is completely empty.** Zero exceptions of any type.

The 4 missing daily sessions and 2 intraday provider-wide gaps are well-documented from the original SIP-migration investigation (`reports/audit/data_source_decision_brief_20260716.md`):

| Date | Symbols affected (rs_v2_clean, 178-universe) | Classification |
|---|---:|---|
| 2021-04-19 | 122 | `PROVIDER_WIDE_PATTERN` — IEX-wide, confirmed missing at the source |
| 2021-10-25 | 108 | same |
| 2022-03-08 | 119 | same |
| 2022-04-06 | 1 | same |
| 2025-03-10 (intraday) | 180/180 (100%) | `PROVIDER_WIDE_PATTERN` — total outage day |
| 2024-12-23 (intraday) | 180/180 (100%) | same |

A live SIP canary at the time (same report) proved these sessions genuinely exist on SIP but return zero rows on IEX for the identical requests — i.e. these are real IEX-feed holes, not acquisition bugs, and **cannot be fixed by re-acquiring** — this was the whole reason SIP was adopted in the first place.

**None of these are currently covered by any exception.** Bringing `rs_v2_clean` to a strict-pass state requires authoring new, evidence-backed exceptions in `rs_v2_clean_coverage_exceptions.json` — mirroring the work already done for the SIP registry (`rs_v2_sip_coverage_exceptions.json`, which now has 121 `MISSING_INTERNAL_SESSION` + 12 `LATE_START` + 4 `PARTIAL_BOUNDARY_SESSION` + 2 `EARLY_TERMINAL` records). The boundary-symbol exceptions (ABNB/PLTR/RBLX/SNOW listing-day `LATE_START`/`PARTIAL_BOUNDARY_SESSION`, PARA/BK `EARLY_TERMINAL`) describe real corporate facts independent of data feed, so the *dates and reasoning* carry over directly — but each exception's `evidence_hash` is schema-bound to its own record, so fresh IEX-specific records still need to be authored (not copy-pasted) even though the underlying evidence is the same.

## 5. DSS compatibility — what needs to change to point back at IEX

`dss_report.py` itself is feed-agnostic — it takes a `data_folder` parameter and calls the shared `validate_v2_clean_dataset`, which dispatches on `manifest.get("dataset_profile")`. No profile-specific logic lives inside `dss_report.py`.

The actual pointer is hardcoded in the caller:

```python
# bot.py:494
data_folder = BASE_DIR / "testdata" / "research_data" / "rs_v2_sip_market_data"
```

**But this is not the only place.** The upstream step that *produces* `paper_signal_report.csv` (which `dss_report.py` reads) is `universe_scanner.py --reports basic --paper-report`, and its default `data_folder` is *also* hardcoded to SIP:

```python
# universe_scanner.py:361
data_folder = Path(__file__).resolve().parent / "research_data" / "rs_v2_sip_market_data"
```

`scripts/run_daily_paper_pipeline.sh` step [1/4] is SIP-specific too:
```bash
"$PYTHON" vericekici_updated.py --dataset-profile rs_v2_sip --refresh-daily --live $DATA_REFRESH_FLAGS
```

There IS an existing, separate, already-wired path for the clean profile: `universe_scanner.py --experiment rs_m126_top30_sma100_no_regime_v2_clean_stock` (line 442-463) correctly points `data_folder` at `rs_v2_clean_market_data` and defines the matching experiment config — but it's a distinct invocation, not the one the daily pipeline script or `--paper-report`/`--make-chatgpt-report` flags currently exercise by default; whether `--paper-report` composes cleanly with `--experiment` was not traced further (would need direct verification, not just reading).

**Is `--refresh-daily` implemented for `rs_v2_clean`? No.** Confirmed by direct code search: every `args.refresh_daily` reference in `_main_impl` sits inside `elif is_v2_sip:` (line 2473 onward) or in code gated by `manifest.get("refresh_daily")`/`payload.get("refresh_daily")` that only ever gets set `True` from that same SIP branch. The `if is_v2_clean:` branch (line 2437) has zero references to `refresh_daily`, and its date-window check (`args.start != "2020-07-27" or args.end != "2026-07-07"`) would itself reject any attempt to request a later end date — this hardcoded exact-window requirement is exactly what SIP's `--refresh-daily` feature (Phase 3A) was built to relax, safely, for one profile only. That entire feature (CLI flag, drift-detection worker branch, fail-closed guards, idempotency check, registry-driven skip-symbol logic) does not exist for `rs_v2_clean` and would need to be built from scratch or generalized to be shared.

### Gaps list (DSS/pipeline repoint)
1. `bot.py:494` — `data_folder` constant, SMALL.
2. `universe_scanner.py:361` — default `data_folder` constant (used by the `--reports basic --paper-report` path the daily script actually calls), SMALL, but needs care since other scanner flags reuse this same default and would silently repoint too.
3. `scripts/run_daily_paper_pipeline.sh` step [1/4] — `--dataset-profile` argument, SMALL, but blocked until item 4 exists.
4. `--refresh-daily` for `rs_v2_clean` — does not exist. LARGE (new feature, mirroring all of Phase 3A's work for a second profile).
5. Verify `--experiment rs_m126_top30_sma100_no_regime_v2_clean_stock` actually composes with `--paper-report`/`--make-chatgpt-report` to produce a usable `paper_signal_report.csv` — unverified, SMALL to check, unknown-size fix if it doesn't compose.

## 6. Acquisition feasibility under the Free tier

No proactive rate-limiter exists in the acquisition code — only exponential-backoff retry on failure (`time.sleep(min(2 ** (attempt - 1), 8))`, line 1269) and a flat `time.sleep(1)` elsewhere (line 2764), not a request-budget throttle. The feed itself is passed as a plain string/enum (`DataFeed.SIP` vs `DataFeed.IEX`, `get_data_feed()` line 751) — fully generic, no SIP-specific code to remove. The 30-symbol websocket-stream cap mentioned in the Free-tier description is irrelevant here: this codebase only uses the REST historical-bars endpoint (`StockHistoricalDataClient`/`StockBarsRequest`), never a websocket stream.

For 358 total items (177 candidates + QQQ + SPY, 2 timeframes) with `--download-workers 4`, this is the same order of magnitude as the SIP Step-1 rebuild already performed this session (~15-20 minutes wall-clock, per that run's logs) — no code-level blocker identified from static analysis. **This was not run** (forbidden this task) and the actual 200-calls/min ceiling was not empirically tested; a real acquisition attempt could still surface throttling behavior not visible from reading the code.

---

## Structured effort estimate

| # | Item | Size | Notes |
|---|---|---|---|
| 1 | Universe sync (178→177) for `rs_v2_clean` | **Done already** | Automatic via shared `load_v2_frozen_universe()` + this session's `runner.py` stale-count fix. No work needed. |
| 2 | Repoint `bot.py`'s `data_folder` to `rs_v2_clean_market_data` | **SMALL** | One constant. |
| 3 | Repoint `universe_scanner.py`'s default `data_folder` (or add a `--profile`/env-based switch) | **SMALL–MEDIUM** | One constant, but touches every scanner mode that relies on the default — needs a decision (hardcode swap vs. parameterize) rather than a blind edit. |
| 4 | Full re-acquisition of `rs_v2_clean` against the current 177-symbol frozen baseline | **LARGE** | Same class of work as Phase 3B Step 1 (SIP rebuild): a real `--live` acquisition, needs operator authorization, ~15-20 min, subject to the Free-tier rate limits (untested). |
| 5 | Author evidence-backed exceptions for 4 daily + 2 intraday provider-wide IEX gaps | **MEDIUM** | Registry currently empty. Facts are already documented (§4); needs new schema-compliant records with fresh evidence hashes — most of the *investigation* work is already done, this is mostly authoring + review. |
| 6 | Author boundary-symbol exceptions (ABNB/PLTR/RBLX/SNOW/PARA/BK) for the `rs_v2_clean` registry | **MEDIUM** | Same underlying facts as the SIP registry's records, but schema-bound per-registry — cannot be copy-pasted; needs re-authoring with correct evidence hashes. |
| 7 | Build `--refresh-daily` for `rs_v2_clean` (or a shared/generalized implementation) | **LARGE** | Does not exist today. Mirrors all of Phase 3A's original SIP work: CLI flag, drift-detection worker path, fail-closed guards (skip/aggregate/dry-run rejection), idempotency check, exception-registry-driven skip-symbol logic. This is the single biggest item. |
| 8 | Repoint `scripts/run_daily_paper_pipeline.sh` step [1/4] | **SMALL** | Trivial once item 7 exists; blocked until then. |
| 9 | Verify/fix `--experiment` + `--paper-report` composition | **SMALL to verify, unknown to fix** | Not traced to completion this pass. |
| 10 | IEX-vs-SIP research-grade parity work (full backtest parity check, matching Phase 2E's hisseler-vs-SIP parity backtest) | **LARGE, and separate from "runs at all"** | Only needed if IEX is meant to *match* SIP's validated research bar, not just produce a usable daily report. Not required to get DSS minimally running on IEX. |

### "Needed to run DSS on IEX at all" (minimum path)
Items 2, 3, 4, 5, 6 — repoint the two constants, re-acquire once against 177 (LARGE, one-time, needs `--live` authorization), author the known-gap exceptions (MEDIUM). This gets a single, static, honestly-validated IEX dataset that DSS can read — but it would go stale again immediately with no way to refresh it (item 7 not required for a one-shot report, but required for *daily* operation).

### "Needed for daily, ongoing operation" (full path)
All of the above **plus** item 7 (`--refresh-daily` for clean) and item 8 — without it, every day would require a fresh full re-acquisition (LARGE, repeated) rather than a cheap incremental refresh.

### "Needed to match SIP's research-grade bar" (parity, not required to function)
Item 10 — a formal parity backtest between the new IEX 177-universe dataset and the existing SIP 177-universe dataset, mirroring Phase 2E's hisseler-vs-SIP parity work, would be needed only if the goal is confidence that switching feeds doesn't change strategy behavior. Not required just to get a running DSS report.

---

## Stop

Read-only scoping only. No GO/NO-GO conclusion offered. Stopping after reporting.
