"""Informational headline-metric drift reporting for frozen baselines (Phase 9A).

WHY THIS EXISTS
---------------
The rs_v2_tiingo guard pinned six headline metrics as exact assertions. Phase 8E
established that this makes the suite fail for a legitimate reason: when Tiingo
retroactively re-adjusts a symbol for a corporate action, the nightly refresh
performs a drift-triggered full re-acquisition and rewrites that symbol's entire
price history. On 2026-07-27 four symbols (CVS, PFE, PG, T) were re-adjusted and
the headline metrics moved -- while the per-rebalance SELECTION FINGERPRINT
matched exactly, i.e. the ranking logic was provably unchanged.

A guard that fails regularly for legitimate reasons trains the operator to
ignore it. That is the same failure mode as the notice spam closed in Phase 8E,
and it is more dangerous here because this is the governing regression guard.

THE SPLIT
---------
- The selection fingerprint stays an EXACT, HARD assertion. It is the real
  invariant: it proves ranking/selection is unchanged regardless of what the
  vendor did to prices.
- Headline metrics become INFORMATIONAL. They are always recorded and reported.
  A move beyond the threshold below emits a marker line that
  testdata/run_test_suite.py lifts into the suite-level SUITE SUMMARY -- the
  same mechanism already used for inactive guards, printed last so it cannot be
  scrolled past. It does NOT fail the suite.

WHY DRIFT DOES NOT FAIL THE SUITE
---------------------------------
Deliberate. A vendor re-adjustment is not a regression, and there is no
threshold that separates "vendor re-adjusted four symbols" from "vendor
re-adjusted forty" by size alone -- both are legitimate. What distinguishes a
real regression is the FINGERPRINT, which is why that assertion stayed hard.
Metric drift is a prompt to run the re-freeze procedure and record why, not a
verdict that something is broken.

THE THRESHOLD, AND ITS LIMITS
-----------------------------
DRIFT_RELATIVE_THRESHOLD is 10% relative, applied per metric.

Calibrated against the only re-adjustment event measured end to end (2026-07-27,
four symbols). Relative moves observed then:

    max_drawdown_pct   -18.97 -> -19.36   2.06%   <-- largest
    trade_count           770 ->    768   0.26%
    win_rate_pct         50.1 ->   50.0   0.20%
    net_return_pct     175.49 -> 175.70   0.12%
    cagr_pct            18.46 ->  18.47   0.05%

10% sits roughly 5x above the largest observed legitimate move, so an ordinary
re-adjustment stays quiet, while a move large enough to suggest something other
than price re-adjustment is surfaced.

Stated honestly: this is calibrated on ONE event (n=1). It is a defensible
order-of-magnitude choice, NOT a statistically derived bound, and it should be
revisited as further re-adjustments accumulate. Each re-freeze artifact records
the observed deltas, so that evidence builds up over time.
"""

from __future__ import annotations

import sys

# Parsed by testdata/run_test_suite.py. Keep the prefix stable.
MARKER_PREFIX = "[BASELINE-METRIC-DRIFT]"

DRIFT_RELATIVE_THRESHOLD = 0.10

# Metrics whose movement is expected for a known, recorded reason and should not
# be raised as drift. Empty by default: the Phase 9A Sharpe convention change is
# handled by re-freezing the pinned value, not by exempting the metric, so the
# guard keeps watching it afterwards.
DRIFT_EXEMPT_METRICS: frozenset[str] = frozenset()

_announced: set[str] = set()


def _relative_delta(expected: float, observed: float) -> float | None:
    """Fractional change, or None when the baseline is zero (no scale to divide by)."""
    if expected == 0:
        return None
    return abs(observed - expected) / abs(expected)


def compare_metrics(expected: dict, observed: dict) -> list[dict]:
    """Per-metric comparison records.

    Only keys present in `expected` are compared; a key missing from `observed`
    yields observed=None, which the caller should treat as a structural error
    rather than drift.
    """
    records = []
    for key in sorted(expected):
        want = expected[key]
        got = observed.get(key)
        comparable = (
            got is not None
            and isinstance(want, (int, float))
            and not isinstance(want, bool)
            and isinstance(got, (int, float))
            and not isinstance(got, bool)
        )
        if not comparable:
            records.append({
                "metric": key, "expected": want, "observed": got,
                "absolute_delta": None, "relative_delta": None, "beyond_threshold": False,
            })
            continue
        relative = _relative_delta(float(want), float(got))
        beyond = (
            key not in DRIFT_EXEMPT_METRICS
            and relative is not None
            and relative > DRIFT_RELATIVE_THRESHOLD
        )
        records.append({
            "metric": key,
            "expected": want,
            "observed": got,
            "absolute_delta": float(got) - float(want),
            "relative_delta": relative,
            "beyond_threshold": beyond,
        })
    return records


def _format_row(record: dict) -> str:
    relative = record["relative_delta"]
    relative_text = "n/a" if relative is None else f"{relative * 100:.2f}%"
    absolute = record["absolute_delta"]
    absolute_text = "n/a" if absolute is None else f"{absolute:+.4f}"
    flag = "  <-- BEYOND THRESHOLD" if record["beyond_threshold"] else ""
    return (
        f"    {record['metric']:<20} {str(record['expected']):>12} -> "
        f"{str(record['observed']):>12}  {absolute_text:>12}  {relative_text:>8}{flag}"
    )


def report_metric_drift(guard_name: str, expected: dict, observed: dict) -> list[dict]:
    """Record headline-metric drift; announce loudly only beyond the threshold.

    Always returns the full comparison records so a caller can assert on
    structure. Printing is informational: this function never raises and never
    fails a test.
    """
    records = compare_metrics(expected, observed)
    moved = [r for r in records if r["absolute_delta"] not in (None, 0.0)]
    breaches = [r for r in records if r["beyond_threshold"]]

    if not moved:
        print(
            f"[baseline-metrics] {guard_name}: all {len(records)} headline metrics "
            f"match the frozen baseline exactly.",
            file=sys.stdout, flush=True,
        )
        return records

    if not breaches:
        # Quiet but recorded: movement within the tolerated band.
        print(
            f"\n[baseline-metrics] {guard_name}: {len(moved)} headline metric(s) moved, "
            f"all within the {DRIFT_RELATIVE_THRESHOLD * 100:.0f}% informational threshold.\n"
            f"    Expected after a vendor price re-adjustment. The selection fingerprint\n"
            f"    is asserted separately and exactly.",
            file=sys.stdout, flush=True,
        )
        for record in moved:
            print(_format_row(record), file=sys.stdout, flush=True)
        return records

    if guard_name not in _announced:
        _announced.add(guard_name)
        banner = "!" * 78
        print(
            f"\n{banner}\n"
            f"{MARKER_PREFIX} guard={guard_name} count={len(breaches)}\n"
            f"  HEADLINE METRICS MOVED BEYOND THE "
            f"{DRIFT_RELATIVE_THRESHOLD * 100:.0f}% INFORMATIONAL THRESHOLD.\n"
            f"  This did NOT fail the suite -- the selection fingerprint is the hard\n"
            f"  invariant and is asserted separately. But a move this large is not a\n"
            f"  routine vendor re-adjustment. Investigate before re-freezing.\n"
            f"  procedure: python3 scripts/refreeze_tiingo_strategy_baseline.py --help\n"
            f"{banner}",
            file=sys.stdout, flush=True,
        )
    for record in moved:
        print(_format_row(record), file=sys.stdout, flush=True)
    return records


def reset_for_testing() -> None:
    """Clear the one-time announcement registry (used by this module's own tests)."""
    _announced.clear()
