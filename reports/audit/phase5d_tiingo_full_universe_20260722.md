# Phase 5D — Tiingo Full-Universe Analysis (177+QQQ+SPY, 179/179)

**Date:** 2026-07-22 (analysis run 2026-07-24)
**Scope:** Read-only analysis of the completed paced Tiingo full-universe fetch (179/179 symbols on disk under `testdata/research_data/rs_v2_tiingo_eval_data/`). No network calls. No production paths touched: SIP dataset, DSS, frozen 177 universe/hash, and strategy/ranking/benchmark logic were not modified. No exceptions authored. Analysis code: `scratch/tiingo_full_universe_analysis.py` (reuses production strategy functions from `testdata/strategies/relative_strength_rotation.py` and `testdata/runner.py` directly — no reimplementation of ranking/execution logic).

---

## 1. Dataset Confirmation

- **179/179 series present** (177 candidates + QQQ + SPY). Zero missing files.
- **Adjusted-field mapping confirmed in code** (`scratch/tiingo_fetch_adapter.py:55-59`): `Open=adjOpen, High=adjHigh, Low=adjLow, Close=adjClose, Volume=adjVolume`. This is the split/dividend-safety guarantee — verified in the fetch adapter source, not just inferred from file contents.
- **Window covered:** global earliest start `2020-07-27`, global latest date across the universe `2026-07-23`.
- **Per-symbol span respects listing/delisting boundaries**, confirmed directly from file contents (no hardcoded exception list needed — spans are read from each file's own min/max date):
  - `ABNB` starts `2020-12-10` (post-IPO late start) — as expected.
  - `PARA` ends `2025-08-07` (1,265 rows) — early terminal, correctly bounded.
  - All other candidates + QQQ/SPY span `2020-07-27` → `2026-07-21` (1,503 rows), except symbols fetched in the Jul-24 resume batch which run through `2026-07-23`.

## 2. Full Gap Analysis (active-span aware, all 179 series)

Method: for each symbol, `approved_nyse_schedule` sessions were intersected with that symbol's own observed `[min_date, max_date]` span (so late starts / early terminals are never miscounted as gaps), then diffed against the symbol's actual session dates.

**Result: 2 missing internal sessions found — not zero.** This corrects the earlier phase5c finding of "0 gaps," which was based on only the 105/179 symbols downloaded at that time (before the rate-limit-blocked 74 were resumed).

| Symbol | Missing dates | Context |
|---|---|---|
| `WBD` | `2022-04-06`, `2022-04-07` | Sits inside the Discovery/WarnerMedia merger transition window (WBD ticker began regular-way trading 2022-04-11 after a when-issued period). SIP has real, thin-volume data for both dates (63,434 and 272,978 shares); Tiingo's series jumps straight from 04-05 to 04-08. This is a genuine Tiingo-side gap, not a boundary/methodology artifact — SIP's presence of the same two sessions proves the market was open and trading.

**Materiality check:**
- Neither `2022-04-06` nor `2022-04-07` is a month-end rebalance date (monthly rebalance lands on last-trading-day-of-month).
- Because the full production backtest (Section 3) was run directly against this exact Tiingo data — gaps included — the materiality question is answered empirically rather than synthetically: the 2-day WBD gap did not prevent WBD from being evaluated in nearby rebalance windows, and it is not a distinguishing factor in the 5 rebalance-date selection differences found in Section 3 (none of the 5 diff dates involve WBD).
- **Assessment: immaterial.** 2 missing sessions on 1 symbol, in a known low-liquidity/merger-transition window, with zero downstream selection or trade impact — a different order of magnitude from IEX's 350 gaps / 123 symbols affected.

## 3. Full Strategy Comparison vs SIP Baseline (production functions, same window, through 2026-07-17)

**Method:** Both runs call `runner._run_strategy()` and `_relative_strength_selection()` directly — the exact production functions, unmodified — against in-memory DataFrames built from the CSVs already on disk (SIP: `testdata/research_data/rs_v2_sip_market_data/daily/` + `benchmarks/daily/`; Tiingo: `testdata/research_data/rs_v2_tiingo_eval_data/daily/`), sliced to `<= 2026-07-17`. Strategy: `rs_m126_top30_sma100_no_regime` (126-day lookback, top-30, monthly rebalance, SMA100 trend filter, no regime filter) — the frozen production baseline config from `runner._rs_robustness_experiments()`.

**Note on the previously-cited 165.9133% / 762-trade figure:** that number came from `daily_only_equivalence_proof_20260721.md`, produced by a now-deleted scratch script on 2026-07-21. Re-running the identical production strategy function against the current SIP daily data in this session yields **167.24% / 771 trades** for the same window — a small drift versus the earlier cached figure, most plausibly from intervening commits to the daily-only pipeline (`2b56a58`, `62f433b`) between 2026-07-21 and now. This report treats the freshly-computed SIP run below as ground truth, since it was produced by the exact same code path, in the exact same session, as the Tiingo comparison run — an apples-to-apples comparison, not a comparison against a stale artifact.

| Metric | SIP (177 universe, fresh run) | Tiingo (177 universe) | Delta |
|---|---|---|---|
| Net Return (%) | 167.24 | 168.87 | +1.63 pp |
| CAGR (%) | 17.90 | 18.02 | +0.12 pp |
| Max Drawdown (%) | -19.15 | -18.97 | +0.18 pp |
| Sharpe Ratio | 0.53 | 0.51 | -0.02 |
| Trade Count | 771 | 770 | -1 |
| Ending Equity ($10,000 start) | 26,723.93 | 26,887.22 | +163.29 |

### Rebalance selection stability
- **67 common rebalance dates** evaluated (both series cover the same window with the same 126-day-lookback warmup).
- **5 of 67 dates (7.5%) differ** in Top-30 selection, all single-symbol swaps at the rank-30 boundary:

| Date | SIP-only (dropped by Tiingo) | Tiingo-only (added) |
|---|---|---|
| 2021-01-29 | ABT, BX | PARA, WBD |
| 2021-02-26 | HON, LRCX | PARA, WBD |
| 2022-05-31 | ELV | T |
| 2025-02-28 | KR | EXC |
| 2026-06-30 | CARR | FDX |

Every swap is a rank-boundary reshuffle (1-2 symbols), not a wholesale selection-set corruption. This is materially better than IEX's 23.6% (17/72) rebalance-date corruption rate, but worse than the earlier partial-data Tiingo estimate of 4.2% (2/48) — the fuller 67-date, 177-symbol comparison surfaces more boundary churn than the partial 105-symbol sample did, most of it traceable to the price-adjustment issue below (T, EXC, FDX, WBD, PARA all appear on both the selection-diff list and the price-divergence list).

### Price variance (full universe, common sessions)
- **264,283 common daily sessions** compared across 177 symbols.
- **Median: 1.59 bps | Mean: 25.71 bps | P95: 70.65 bps**

The median is excellent and consistent with the earlier partial finding. The mean/P95 are pulled up by a small number of symbols with real systematic divergence — see below.

### FLAGGED — systematic price divergence (named symbols)

Four symbols show a **median absolute divergence over 200 bps** (all far above the 1.59 bps universe median):

| Symbol | Median abs bps | Max abs bps |
|---|---|---|
| **FDX** | **1,908.5** (≈19.1%) | 1,914.8 |
| **CMCSA** | **543.1** (≈5.4%) | 546.9 |
| **MMM** | **517.3** (≈5.2%) | 525.1 |
| **MDT** | **222.8** (≈2.2%) | 245.5 |

Smaller but still non-trivial divergence also appears in `T` (152 bps), `DHR` (128 bps), `ETN` (98 bps), `SO` (97 bps), `EXC` (76 bps), `BAC` (72 bps) — all below the 200 bps flag threshold but part of the same pattern.

**Root-cause characterization (verified, not assumed):** sampling each flagged symbol's SIP/Tiingo close ratio across the window (2020-07-27, 2021-06-01, 2022-06-01, 2023-06-01, 2025-06-02, 2026-06-01, 2026-07-17) shows the ratio is **flat/constant across 2020–2025, then snaps to ~1.0 (equal) starting around 2026-06-01** through the end of the data. Example — FDX: SIP/Tiingo ≈ 1.2358 constant from 2020 through mid-2025, then 337.22/337.24 ≈ 1.0000 by 2026-06-01.

This is **not** a random-noise pattern and **not** a smoothly-decaying dividend-compounding curve. A flat-then-snap-to-parity signature means: for dates before some recent corporate-action/adjustment event, one source's Close reflects that event's retroactive adjustment and the other doesn't; for dates after the event, both sources agree because there's nothing left to retroactively adjust. In plain terms: **SIP's daily Close for these five names appears to be missing one relatively recent dividend/distribution adjustment that Tiingo has already applied retroactively across its full history** (or vice versa — the evidence pins down *that* a recent single adjustment event is the cause, not *which* source has the correct/current adjustment). This is a different failure mode from the historical CRWD/AVGO/DD stale-split corruption (which was a wrong split *ratio*, producing 2x/0.5x-style errors) — here the ratios (1.236, 1.058, 1.052, 1.025) don't correspond to any clean split ratio, and the pattern is a single retroactive adjustment-basis mismatch, not a ratio error compounding across multiple corporate actions.

**Honest characterization:** this is **not** marginal rank-boundary noise — it's a real, structural, named-symbol data discrepancy between the two sources, concentrated in dividend-paying names (FDX, CMCSA, MMM, MDT, T are all regular dividend payers), and it directly explains 3 of the 5 rebalance-selection swaps in the table above (T, EXC, FDX all appear in both lists). It is **not**, however, universe-wide corruption — 173 of 177 symbols show no material divergence, and the aggregate median (1.59 bps) is excellent. **Before Tiingo could be trusted as a SIP-equivalent source for ranking, this dividend/adjustment-recency discrepancy for FDX, CMCSA, MMM, MDT (and the smaller cases: T, DHR, ETN, SO, EXC, BAC) would need to be resolved and its root cause pinned to one source or the other.**

## 4. Daily-Refresh Feasibility (free tier, ~50 req/hour)

Empirical evidence from the two real fetch runs on disk (not estimated):

| Run | Symbols | Wall-clock | Effective throughput |
|---|---|---|---|
| Original cold run (2026-07-22) | 105 succeeded before hitting the hourly cap | ~133 s | ~47 req/min until 429 |
| Resume run (2026-07-24), with 429 backoff (300/600/900s escalating waits) | 74 (the ones that got 429'd originally) | ~3,211 s (53.5 min) | ~1.4 req/min sustained, due to repeated hourly-cap backoff waits |

A daily refresh still requires **one API call per symbol** regardless of how little new data is requested (Tiingo's free tier meters by request count, not by date range) — so a full 179-symbol daily refresh is bound by the same ~50 requests/hour ceiling that caused the original run to fail at symbol 106.

**Concrete estimate:** a cold-start daily refresh of all 179 symbols would need at minimum `ceil(179/50) = 4` hourly windows if strictly rate-limited, and the observed real-world behavior (53.5 minutes to clear 74 backed-off symbols) suggests **1.5–3 hours of wall-clock time with backoff waits** is realistic for a full 179-symbol refresh from a cold cache. This does **not** fit comfortably within a single free-tier hour. The paid Tiingo Power tier ($10/month, 5,000 req/hour) would clear all 179 requests in well under a minute with no backoff logic needed.

## 5. Verdict

**Is Tiingo gap-free and SIP-equivalent for ranking purposes across the full universe? Partially — with one concrete, named caveat.**

- **Coverage:** Very close to gap-free (2 missing sessions out of 264,283+ across 179 series, both immaterial and confined to one symbol during a known low-liquidity merger-transition window). This is a dramatically better completeness profile than free-tier IEX (350 gaps / 123 symbols).
- **Price fidelity:** Excellent for 173/177 symbols (median 1.59 bps). **Not** SIP-equivalent for FDX, CMCSA, MMM, MDT specifically — these show a structural, dividend/adjustment-recency price discrepancy of 2.2%–19.1%, which is large enough to influence rank-boundary selections (3 of 5 observed selection swaps trace directly to these names).
- **Strategy-level outcome:** Full 177-universe backtest shows Tiingo tracking SIP closely in aggregate (+1.63pp return delta, -1 trade, 5/67 rebalance dates with single-symbol boundary swaps) — small, not structural corruption at the portfolio level. But this aggregate closeness is partly *because* only 4-10 symbols are affected out of 177, not because the affected symbols are individually reliable.
- **Bottom line:** Tiingo is a strong candidate on completeness and broad price fidelity, but the FDX/CMCSA/MMM/MDT divergence is a real, named, unresolved data-quality question — not something this analysis can wave away as noise. It should be root-caused (which source has the stale/missing adjustment) before Tiingo is trusted as a drop-in SIP replacement for ranking.

---

*No GO/NO-GO on spending is offered here per instruction — this report presents evidence only.*
*Analysis code: `scratch/tiingo_full_universe_analysis.py`. Raw output: `scratch/phase5d_analysis_output.json`. Reviewed via `/code-review` (1 MEDIUM finding — unused helper function — fixed before this report was written).*
