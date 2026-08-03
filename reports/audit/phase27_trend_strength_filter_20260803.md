## EXECUTIVE SUMMARY
Verdict:        Tested idea #2 from this session's brainstorm: does requiring SMA50 >
                 SMA100 (trend genuinely accelerating, not just "above its 100-day
                 average") on top of the existing eligibility filter improve results on
                 the survivorship-bias-corrected universe? No -- essentially a wash.
                 Baseline (existing filter): 65.86% return / 0.76 Sharpe / 20.52% max
                 drawdown. With the added filter: 64.06% / 0.74 / 20.08% -- marginally
                 lower return and Sharpe, marginally lower drawdown, no meaningful
                 improvement in either direction. This idea does not appear worth
                 pursuing further as-is.
Changed:         New file research_sandbox/rs_trend_strength_filter.py (reuses the frozen
                 production feature computation read-only, same pattern as
                 rs_factor_combination.py). No production code touched.
Suite:           N/A -- research/evaluation task, no production code or test suite
                 changed.
Anomalies:       None.
Open items:      None for this specific idea. Idea #3 from the same brainstorm (turn on
                 the existing-but-unused rs_use_market_regime gate) is still unexplored.
Operator action: None required.

---

## Method

Reused `testdata.strategies.relative_strength_rotation._relative_strength_daily_features`
(unchanged, read-only) for eligibility/score/ATR%/existing SMA100 trend filter, on the
same survivorship-bias-corrected universe as phase24-26 (production + 6,120 delisted
candidates, 6,296 symbols). Added one sandbox-only condition on top of the existing
eligibility mask: `SMA50(Close) > RS_Trend_SMA` (the existing SMA100), at production's
frozen 126-day lookback / monthly rebalance. Compared against the same baseline without
the extra condition.

## Results

| variant | net return | Sharpe | max drawdown | rebalances |
|---|---|---|---|---|
| baseline (existing SMA100 filter) | 65.86% | 0.76 | 20.52% | -- |
| plus SMA50 > SMA100 | 64.06% | 0.74 | 20.08% | -- |

Baseline number matches phase24/25/26 exactly (65.86%), confirming this script's universe
and base config line up correctly with the earlier runs.

## Interpretation

The hypothesis (a stricter "genuinely accelerating trend" filter would reject more
about-to-collapse names that fooled the existing SMA100-only filter) did not pan out at
this specific threshold. Two plausible reasons, not distinguished further given the
marginal effect size didn't seem worth the extra investigation: the existing SMA100 filter
may already reject most of the same names SMA50>SMA100 would additionally catch (overlap,
not a materially different filter), or the names it does additionally exclude are roughly
a wash between "correctly avoided a future collapse" and "incorrectly excluded a name that
would have performed fine." Not pursued further; treated as a negative result and
reported as such rather than tuned until something looked better (would risk exactly the
kind of after-the-fact parameter search this project's standing rules exist to avoid in
production, and isn't a good use of further sandbox time on a flat result).

## Evidence

```
$ python3 research_sandbox/rs_trend_strength_filter.py
Loading EXPANDED universe (production + full delisted population) ...
...
full population: 6120 loaded / 9423 had some data / 9423 candidates total (3303 dropped: <30 days or split-guard)
Loaded 6296 symbols.
  baseline (existing SMA100 filter) return=   65.86% sharpe=  0.76 maxdd=  20.52%  (11.7s)
  plus_sma50_gt_sma100             return=   64.06% sharpe=  0.74 maxdd=  20.08%  (10.0s)

Wrote /home/gnesh/Desktop/BOT - Kopya/research_sandbox/output/rs_trend_strength_filter.csv
```

## `/code-review` (ECC)

No production code changed. `research_sandbox/rs_trend_strength_filter.py` follows the
same read-only reuse pattern as `rs_factor_combination.py`. Self-review: no secrets, no
network calls, output confined to gitignored `research_sandbox/output/`, no broker/order/
live-trading paths touched.
