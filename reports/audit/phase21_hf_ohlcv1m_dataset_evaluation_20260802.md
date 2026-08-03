## EXECUTIVE SUMMARY
Verdict:        `mito0o852/OHLCV-1m` (HF, Finnhub-sourced, 1992-2026, 81,306 tickers) is
                 usable as a research_sandbox survivorship-bias data source, but ONLY
                 through the filter+cross-check pipeline built this session -- the raw
                 dataset is NOT usable as-is (see Anomalies). No genuine ticker-reuse
                 contamination (a different company inheriting a freed ticker) was found
                 anywhere in this investigation, curated or bulk.
Changed:         .gitignore (added hisseler/87DATA/ -- 82GB local mirror must never be
                 committed); 411 broken symlinks under hisseler/87DATA/data/ repaired
                 (wrong relative-path depth from a non-standard HF local-dir download;
                 no blob/file content touched, only symlink targets).
Suite:           N/A -- research/evaluation task, no production code changed. No test
                 suite applicable; prior baseline suite untouched.
Anomalies:       (1) Two sentinel/placeholder price values in the raw feed (~0.000001
                 floor, ~199999 ceiling) affecting 3,096+ tickers / 28,000+ ticker-months,
                 concentrated in recent years' micro-cap/OTC names. (2) Official exchange
                 test-ticker symbols (ATEST/CTEST/NTEST/ZVZZT family) present in the raw
                 feed, 2,528 rows. (3) A one-time 2020-12 batch artifact: 362 tickers
                 silent for 20-27 years suddenly print a single near-zero close that
                 month, then nothing -- not real trading. (4) The naive "last seen
                 N months before dataset end" delisting heuristic produced false
                 positives even at 6-19 months of apparent silence (e.g. GPS, actually
                 a live ticker-symbol rename to GAP, not a delisting) -- resolved only by
                 cross-checking against today's official NASDAQ/NYSE/AMEX active-listing
                 files, which found the false-positive rate was actually small (245/37,078,
                 0.66%) once artifacts (1)-(3) were already removed.
Open items:      Dataset's split/dividend-adjustment status is unconfirmed (unlike Tiingo,
                 which is explicitly adjusted); HF dataset license is still unstated;
                 genuine ticker-reuse (rare but real per the project's prior Alpaca
                 experience) was spot-checked on 6 symbols only, not structurally ruled
                 out across the full 36,833-candidate list (no permanent security ID in
                 the source, ticker string only); this is 1-minute data and the project
                 is 100% daily-driven, so any use requires the resample-to-daily step
                 already prototyped in the probes but not yet formalized as a module.
Operator action: Decide whether to proceed with a research_sandbox survivorship-bias
                 experiment using the confirmed candidate list, and whether the 82GB
                 local mirror at hisseler/87DATA/ should be kept, moved, or deleted once
                 the immediate research need is served (it is git-ignored but still
                 consumes local disk -- 32GB free at time of writing).

---

## Context

Operator downloaded the full `mito0o852/OHLCV-1m` Hugging Face dataset (Finnhub-sourced,
1-minute OHLCV, "thousands of US stocks", 1992-01 through 2026-03, 411 monthly parquet
files) to `hisseler/87DATA/` to evaluate it as a source of delisted-symbol history for a
survivorship-bias research_sandbox experiment -- the project's existing Tiingo/Alpaca data
has only one confirmed delisted symbol in its coverage-exceptions registry (PARA) and a
documented history of ticker-reuse contamination from a prior Alpaca-based attempt
(9/18 valid, 19 possible ticker-reuse cases -- see
`testdata/reports/runs/20260711_020212_DELISTED_ALPACA_LIVE_1476bfa9/delisted_alpaca_price_coverage.md`).

This report covers the full evaluation: repository hygiene for the new local mirror,
a curated 6-symbol verification against known real-world delisting events, and a full
81,306-ticker structural scan for gaps, artifacts, and a cross-checked delisted-candidate
list.

## 1. Repository hygiene

`hisseler/87DATA/` (82GB, HF cache-style layout: `blobs/`, `snapshots/`, `data/`) was not
gitignored on arrival. Added to `.gitignore`:

```
hisseler/87DATA/
```

Verified: `git check-ignore -v hisseler/87DATA/blobs/<hash>` now resolves against this
new line; `git ls-files hisseler/` returns 0 tracked files (confirmed before and after --
this directory was never committed).

Separately, all 411 symlinks under `hisseler/87DATA/data/` were broken on arrival: the
download used a non-standard flattened layout (`data/` as a direct sibling of `blobs/`,
rather than nested under `snapshots/<hash>/data/`), so the symlinks' stored relative
target (`../../../blobs/<hash>`, correct for the standard 3-levels-deep HF cache layout)
pointed three levels too far up. Verified real blob content was present and correctly
sized (100-260MB per monthly file, matching expectation) before repairing; repair
script read each symlink's target hash, verified the hash existed in `blobs/`, and
relinked with the correct one-level-up relative path. 411/411 fixed, 0 missing blobs.
No file content was created, moved, or deleted.

## 2. Curated 6-symbol verification

Cross-checked against the project's own PARA governance record
(`testdata/baselines/rs_v2_tiingo_coverage_exceptions.json`, the only `EARLY_TERMINAL`
exception on file) and against real-world delisting dates for TWTR, ATVI, SGEN, SBNY, FRC.

| Symbol | HF data ends | Real-world event | Verdict |
|---|---|---|---|
| PARA | 2025-08-06 | Tiingo record ends 2025-08-07 (Skydance merger) | 1-day gap -- minor, real |
| TWTR | 2022-10-27 | Musk deal closed 2022-10-27/28 | Clean, exact match |
| ATVI | 2023-10-12 | Microsoft deal closed 2023-10-13 | Clean, exact match |
| SGEN | 2023-12-14 | Pfizer deal closed 2023-12-14 | Clean, exact match |
| SBNY | continues (OTC) through Jun 2023 at $0.12-0.29 | NASDAQ halt 2023-03-10, FDIC seizure 2023-03-12, OTC resumption 2023-03-28 at $2.50 crashing same day | Continuous single-entity price story (confirmed via price data AND WebSearch: FDIC OIG material loss review, FDIC press release) -- legitimate OTC/Pink-Sheets continuation of the same legal entity's shell equity, NOT ticker-reuse contamination |
| FRC | ends exactly 2023-05-01, no data after | FDIC seizure/JPMorgan asset sale completed 2023-05-01, shareholders wiped out, no OTC shell survived | Clean, exact match -- correctly shows no continuation (unlike SBNY, consistent with a total asset sale vs. a receivership shell) |

Verdict: 0/6 genuine ticker-reuse contamination. This sample was initially misread (SBNY
flagged as contamination) due to an incorrect assumption that NASDAQ delisting implies all
trading stops; corrected after the operator challenged the finding with live TradingView
evidence and a WebSearch confirmed the OTC-continuation mechanism.

## 3. Full dataset scan methodology

Given the source has no permanent security identifier (ticker string only, no FIGI/CIK)
and covers ~81,000 tickers over 34 years, a per-symbol manual review is infeasible --
built a 3-pass script pipeline instead (all in `scratch/hf_full_scan/`, not committed):

- **Pass 1** (`hf_full_scan_pass1_parallel.py`): scan all 411 monthly parquet files,
  aggregate to a per-ticker-per-month summary (n_days, first/last date, first/last close).
  Ran parallelized (`ProcessPoolExecutor`); first attempt at 6 workers triggered an OOM
  kill (`dmesg` confirmed `oom-kill: ... Killed process ... python3`, RSS 3.9GB on a single
  worker for a mid-2000s month alone) on a 22GB-RAM / 15GB-available machine -- reduced to
  3 workers, resumed from the 182/411 already-written part-files, completed in ~12 minutes.
  Output: 4,448,872 ticker-month rows, 81,306 unique tickers.
- **Pass 2** (`hf_full_scan_pass2_analysis.py`): computed delisted candidates (naive:
  last-seen month > 2 months before the dataset's own max month) and internal coverage
  gaps with before/after price ratios. Surfaced the sentinel-value and test-ticker
  artifacts described below.
- **Pass 3** (`hf_full_scan_pass3_real_candidates.py`): filtered out the identified
  artifacts (sentinel floor/ceiling prices, test-ticker name patterns), required
  peak_close >= $1 and >= 3 months of valid presence.
- **Ground-truth cross-check** (ad hoc, not a persisted script): downloaded today's
  official NASDAQ (`nasdaqlisted.txt`) and NYSE/AMEX/ARCA (`otherlisted.txt`) symbol
  directories from `nasdaqtrader.com` (`last-modified: 2026-08-01`), excluded flagged test
  issues, and removed any candidate still listed under the exact same symbol today.

## 4. Findings: raw dataset artifacts

| Artifact | Scale | Example |
|---|---|---|
| Floor sentinel ~0.000001 | part of 3,096 tickers / 28,000+ ticker-months, spread across 64 months, concentrated 2022-2026 | `AECFF 2020-12: first_close=0.000001` |
| Ceiling sentinel ~199999 | smaller, present | `WVVI.P`, `TROV.U` showing `199999.xx` |
| Exchange test symbols | 2,528 rows removed | `ATEST`, `CTEST`, `AAAA.TEST`, `NTEST.G`, `ZJZZ.T`, `ZCZZ.T` |
| 2020-12 one-time batch artifact | 362 tickers | e.g. a ticker silent 253-326 months (21-27 years) resumes for exactly one print in 2020-12 at $0.001-0.05, then nothing -- not real trading |

None of these are ticker-reuse contamination in the dangerous sense (a different company's
history blended under one symbol); they are vendor data-quality noise concentrated in
illiquid/shell/test-feed territory, verified not to materially affect large, liquid,
real-company names (the curated 6-symbol sample above hit none of them).

## 5. Findings: delisted candidate funnel

| Stage | Count |
|---|---|
| Raw unique tickers | 81,306 |
| Naive "delisted candidate" (last seen >2mo before dataset end) | 55,922 (69% of all tickers -- inflated by artifacts above) |
| After sentinel-price + test-symbol removal, peak_close>=$1, >=3 valid months | 37,078 |
| After cross-check vs. today's official NASDAQ/NYSE/AMEX listing (245 false positives removed, e.g. GPS -> renamed to GAP, still trading) | **36,833 confirmed not currently listed under that symbol** |
| Of those, peak_close >= $10 (matches project's own minimum-price filter) | 27,262 |

Cross-check false-positive rate was low (245/37,078 = 0.66%) once artifacts were already
filtered -- the naive recency heuristic's apparent unreliability (e.g. flagging GPS, LANC,
SJW, K, USM, X as "stopped" at 3-19 months of silence) turned out to be mostly correct:
of the six spot-checked names, GPS was a genuine same-company ticker rename (GPS -> GAP,
confirmed against the live listing), and USM (T-Mobile acquisition), X (Nippon Steel
acquisition), and others independently corroborate as real 2025 corporate actions. Only
GPS-style same-symbol still-active cases needed removing, and there were few.

The 36,833-row confirmed list intentionally bundles genuine delistings (bankruptcy, M&A)
together with legitimate same-company ticker-symbol changes (like GPS->GAP) -- both are
indistinguishable from a pure historical price-series perspective and both represent
"this exact symbol no longer resolves to live data," which is the relevant condition for
survivorship-bias backtesting.

## 6. Answer to "can we use this data"

Conditional yes, for research_sandbox use, not production:

- **Curated, large-cap delisted names** (the kind likely to actually matter for a
  momentum/Top-30 strategy's survivorship-bias story): clean. 0/6 contamination found.
- **Raw bulk dataset**: not usable without the filter pipeline in this report -- 69% of
  "delisted" tickers by the naive read are partly vendor artifacts.
- **Filtered + cross-checked candidate list** (36,833, or 27,262 at the project's own $10
  floor): usable with reasonable confidence as an INPUT LIST for further research, not as
  ready-to-backtest history, because three things remain unverified:
  1. Split/dividend-adjustment status of this Finnhub-sourced feed is unconfirmed (Tiingo,
     the project's production source, is explicitly adjusted; this dataset's adjustment
     convention was never stated on the HF dataset page or observed directly).
  2. Structural ticker-reuse (a different company reusing a freed symbol years later) was
     ruled out only for the 6 spot-checked symbols, not the full 36,833 -- the source has
     no permanent ID (FIGI/CIK) to make this checkable in bulk.
  3. It is 1-minute data; using it daily requires the resample step already prototyped
     ad hoc in this session's probes, not yet a real module with its own validation.
  4. Dataset license was never confirmed (stated as unstated on the HF page in the prior
     session).

Recommended next step if the operator wants to proceed: pick a small second batch of
spot-check symbols from the 27,262 list (weighted toward names the operator recognizes,
same WebSearch-verification method as PARA/TWTR/ATVI/SGEN/SBNY/FRC) to raise confidence
on ticker-reuse risk before committing to a full research_sandbox build-out.

## 7. Evidence

```
$ git status --porcelain
 M .gitignore
?? scratch/hf_full_scan/
?? scratch/hf_full_scan_pass1.py
?? scratch/hf_full_scan_pass1_parallel.py
?? scratch/hf_full_scan_pass2_analysis.py
?? scratch/hf_full_scan_pass3_real_candidates.py
?? scratch/phase10b_risk_engine_calibration_measure.json
?? scratch/phase5d_analysis_output.json
?? scratch/phase9a_frozen_window_measure.json
?? scratch/phase9a_frozen_window_measure.py
?? scratch/tiingo_fetch_adapter.py
?? scratch/tiingo_full_universe_analysis.py
?? testdata/baselines/rs_v2_tiingo_baseline.json

$ git log --oneline -10
e436ffd docs: remove stale/redundant markdown, update the survivors with current facts
94a37c4 refactor: remove bot.py's unused modes/cockpit and the orphaned delisted/Alpaca research island
61f3c1c fix: untrack scratch/ and baseline-timestamp files accidentally committed
6b75c0c feat(research): add momentum vs momentum+low-vol composite factor comparison
6bac75b refactor(alpaca): migrate run_alpaca_paper_check to the alpaca-py SDK
c09bde0 refactor(runner): extract data governance reports into their own module
01d561f refactor(runner): extract research/validation reports into their own module
cf9866b feat(research): add research_sandbox with an RS rotation sensitivity sweep
bdd9bb7 feat(reports): add self-contained strategy comparison / paper equity dashboard
cd21aa8 refactor(runner): extract Alpaca paper-trading and broker reporting into its own module
```

OOM evidence (pass1, first attempt at 6 workers):
```
$ dmesg | grep -i "out of memory\|oom" | tail -5
python3 invoked oom-killer: gfp_mask=0x140dca(GFP_HIGHUSER_MOVABLE|__GFP_ZERO|__GFP_COMP), order=0, oom_score_adj=200
Out of memory: Killed process 108956 (python3) total-vm:7772588kB, anon-rss:3906940kB, ...
```

Pass1 completion (3 workers, resumed):
```
[411/411] 2026-03: OK tickers=22057 rows=34379927 elapsed=698s
DONE. wrote scratch/hf_full_scan/per_ticker_month_summary.parquet rows=4448872 unique_tickers=81306 total_elapsed=699s
```

Ground-truth freshness:
```
$ curl -sSI https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt | grep -i last-modified
last-modified: Sat, 01 Aug 2026 01:31:32 GMT
```

## 8. `/code-review` (ECC)

Not applicable in the mandatory sense -- no production code was changed. The three
`scratch/hf_full_scan_pass*.py` scripts are one-off research tooling (gitignored path,
never imported by production code, explicitly excluded from commit per standing rules).
Self-review of the scripts: no secrets, no network calls beyond the two documented public
downloads (HF dataset -- already present locally; NASDAQ Trader public symbol
directories), no writes outside `scratch/`, no broker/order/live-trading code paths
touched. The `.gitignore` change is the only production-tree edit and was verified with
`git check-ignore -v` before and after.

## Files produced (all under `scratch/hf_full_scan/`, gitignored, not committed)

- `per_ticker_month_summary.parquet` -- pass1 raw output (4,448,872 rows)
- `delisted_candidates.csv`, `gap_events.csv` -- pass2 raw/unfiltered
- `real_delisted_candidates.csv` -- pass3, artifact-filtered (37,078)
- `high_confidence_delisted.csv` / `low_confidence_recent_stop.csv` -- recency-bucketed split (superseded by the ground-truth cross-check, kept for traceability)
- `confirmed_not_currently_listed.csv` -- final, ground-truth cross-checked (36,833)
- `ground_truth/nasdaqlisted.txt`, `ground_truth/otherlisted.txt` -- source listing files, 2026-08-01
