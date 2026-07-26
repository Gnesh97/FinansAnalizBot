"""Make an inactive baseline guard impossible to miss (Phase 7C).

testdata/research_data/ is gitignored, so on a clean clone -- or on CI, or on any
machine that has not fetched the ~27MB dataset -- every frozen-baseline guard
skips and unittest still prints a green "OK (skipped=N)". An operator could
therefore read a passing suite while the governing regression guard never ran.

Deliberately NOT converting skip into failure: that would break clean-clone and
developer environments, and it would diverge from the pre-existing SIP guard
convention. Instead the skip is announced loudly, once per guard, at import time,
and each announcement emits a machine-readable marker line that
testdata/run_test_suite.py counts into a suite-level summary and into
testdata/reports/latest/test_run_report.md.

Usage in a guard module::

    DATASET_PRESENT = announce_if_inactive(
        "rs_v2_tiingo_strategy_baseline",
        DATA_FOLDER.exists() and BASELINE_PATH.exists(),
        reason="rs_v2_tiingo dataset or frozen baseline artifact not present",
        restore_hint="acquire rs_v2_tiingo via vericekici_updated, then rerun",
    )

    @unittest.skipUnless(DATASET_PRESENT, "...")
    class ...
"""

from __future__ import annotations

import sys

# Parsed by testdata/run_test_suite.py. Keep the prefix stable.
MARKER_PREFIX = "[BASELINE-GUARD-INACTIVE]"

_announced: set[str] = set()


def announce_if_inactive(guard_name: str, is_active: bool, reason: str, restore_hint: str) -> bool:
    """Return is_active; when False, print a prominent one-time notice.

    The notice goes to stdout so it survives unittest's stderr buffering and is
    captured by run_test_suite's subprocess pipe.
    """
    if is_active or guard_name in _announced:
        return is_active
    _announced.add(guard_name)
    banner = "!" * 78
    print(
        f"\n{banner}\n"
        f"{MARKER_PREFIX} guard={guard_name}\n"
        f"  THIS REGRESSION GUARD DID NOT RUN. A green suite does NOT mean this\n"
        f"  baseline was verified.\n"
        f"  reason : {reason}\n"
        f"  restore: {restore_hint}\n"
        f"{banner}\n",
        file=sys.stdout,
        flush=True,
    )
    return is_active


def announced_guards() -> list[str]:
    """Guards announced inactive in this process, for in-process assertions."""
    return sorted(_announced)


def reset_for_testing() -> None:
    """Clear the one-time announcement registry (used by this module's own tests)."""
    _announced.clear()
