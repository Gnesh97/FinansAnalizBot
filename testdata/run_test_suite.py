from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LATEST_DIR = ROOT / "testdata" / "reports" / "latest"


COMPILE_TARGETS = [
    "testdata/report_schemas.py",
    "testdata/paper_state_manager.py",
    "testdata/portfolio_manager.py",
    "testdata/paper_portfolio_monitor.py",
    "testdata/intraday_paper_monitor.py",
    "testdata/paper_ops_dashboard.py",
    "testdata/paper_risk_dashboard.py",
    "testdata/closed_trade_report.py",
    "testdata/no_lookahead_audit.py",
    "testdata/execution_costs.py",
    "testdata/sqlite_state_reconciliation.py",
    "testdata/broker_reconciliation.py",
    "testdata/broker_cutover_baseline.py",
    "testdata/alpaca_paper_snapshot.py",
    "testdata/alpaca_submit_readiness.py",
    "testdata/alpaca_paper_submit_one.py",
    "testdata/paper_decision_inbox.py",
    "testdata/runner.py",
    "testdata/universe_scanner.py",
    "testdata/execution_engine.py",
    "testdata/pre_submit_gate.py",
    "testdata/data_policy_review.py",
    "testdata/risk_gate.py",
    "testdata/broker_interface.py",
    "testdata/alpaca_paper_broker.py",
    "testdata/vericekici_updated.py",
    "testdata/v2_session_coverage.py",
    "testdata/v2_coverage_forensics.py",
    "testdata/v2_corrected_aggregation_audit.py",
    "testdata/v2_daily_gap_selection_audit.py",
    "testdata/v2_provider_recovery.py",
    "testdata/v2_gap_recovery_runner.py",
    "testdata/broker_ledger.py",
    "testdata/broker_lifecycle.py",
    "testdata/baseline_parity.py",
    "testdata/quantconnect_port_contract.py",
    "quantconnect/rs_m126_top30_sma100_no_regime/port_helpers.py",
    "quantconnect/rs_m126_top30_sma100_no_regime/main.py",
    "tests/test_rs_v2_clean_data.py",
    "tests/test_rs_v2_session_coverage.py",
    "tests/test_rs_v2_coverage_audit.py",
    "tests/test_rs_v2_corrected_aggregation.py",
    "tests/test_rs_v2_daily_gap_selection_audit.py",
    "tests/test_rs_v2_gap_recovery_probe.py",
    "tests/test_rs_v2_provider_recovery.py",
    "tests/test_v2_internal_session_exception.py",
    "tests/test_v2_sip_profile.py",
]


def _run(command: list[str]) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    output = (completed.stdout or "") + (completed.stderr or "")
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    return completed.returncode, output


def _tests_ran_count(output: str) -> int:
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    return int(match.group(1)) if match else 0


def _inactive_baseline_guards(output: str) -> list[str]:
    """Guards that announced themselves inactive (see tests/baseline_guard_visibility.py).

    A skipped baseline guard still yields a green suite, so it is surfaced here
    and in the run report rather than being left to a bare "OK (skipped=N)".
    """
    return _markered_guard_names(output, "[BASELINE-GUARD-INACTIVE]")


def _markered_guard_names(output: str, marker: str) -> list[str]:
    """Guard names from marker lines shaped `<marker> guard=<name> [key=value ...]`."""
    names = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith(marker):
            continue
        payload = stripped[len(marker):].strip()
        for token in payload.split():
            if token.startswith("guard="):
                names.append(token[len("guard="):])
                break
        else:
            names.append(payload)
    return sorted(set(names))


def _metric_drift_guards(output: str) -> list[str]:
    """Guards reporting headline-metric drift beyond threshold.

    See tests/baseline_metric_drift.py. This is INFORMATIONAL -- the suite stays
    green -- so it must be surfaced here or it would only exist mid-scrollback.
    """
    return _markered_guard_names(output, "[BASELINE-METRIC-DRIFT]")


def _failed_tests(output: str) -> list[str]:
    failures = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(("FAIL:", "ERROR:")):
            failures.append(stripped)
    return failures


def _write_report(
    status: str,
    command: str,
    duration: float,
    tests_ran: int,
    failures: list[str],
    inactive_guards: list[str] | None = None,
    metric_drift_guards: list[str] | None = None,
) -> None:
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    inactive_guards = inactive_guards or []
    metric_drift_guards = metric_drift_guards or []
    lines = [
        "# Test Run Report",
        "",
        f"- run datetime: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- status: {status}",
        f"- tests ran count: {tests_ran}",
        f"- inactive baseline guards: {len(inactive_guards)}",
        f"- baseline guards reporting metric drift: {len(metric_drift_guards)}",
        f"- command: `{command}`",
        f"- duration_sec: {duration:.4f}",
        "- safety note: Unit tests only; no broker POST, no submit_order, no live trading.",
        "",
        "## Failed Tests",
        "",
    ]
    if failures:
        lines.extend(f"- {item}" for item in failures)
    else:
        lines.append("- none")
    lines.extend(["", "## Inactive Baseline Guards", ""])
    if inactive_guards:
        lines.append("These regression guards did NOT run. A green status above does not")
        lines.append("mean their frozen baselines were verified.")
        lines.append("")
        lines.extend(f"- {name}" for name in inactive_guards)
    else:
        lines.append("- none (all baseline guards active)")
    lines.extend(["", "## Baseline Metric Drift", ""])
    if metric_drift_guards:
        lines.append("Headline metrics moved beyond the informational threshold. This does")
        lines.append("NOT fail the suite -- the selection fingerprint is the hard invariant")
        lines.append("and is asserted exactly. Investigate before re-freezing.")
        lines.append("")
        lines.extend(f"- {name}" for name in metric_drift_guards)
    else:
        lines.append("- none (no guard reported metric drift beyond threshold)")
    lines.append("")
    (LATEST_DIR / "test_run_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    started = time.perf_counter()
    command_text = 'scripts\\run_tests.bat'
    compile_code, compile_output = _run([sys.executable, "-m", "py_compile", *COMPILE_TARGETS])
    test_code = 1
    test_output = ""
    if compile_code == 0:
        test_code, test_output = _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])
    combined = compile_output + test_output
    status = "OK" if compile_code == 0 and test_code == 0 else "FAIL"
    inactive_guards = _inactive_baseline_guards(combined)
    metric_drift_guards = _metric_drift_guards(combined)
    _write_report(
        status,
        command_text,
        time.perf_counter() - started,
        _tests_ran_count(combined),
        _failed_tests(combined),
        inactive_guards,
        metric_drift_guards,
    )
    # Printed after the unittest summary so it is the last thing on screen: a
    # green "OK" with skipped baseline guards must not read as a verified run.
    if inactive_guards:
        print(
            f"\nSUITE SUMMARY: {len(inactive_guards)} baseline guard(s) INACTIVE and "
            f"NOT verified by this run: {', '.join(inactive_guards)}",
            flush=True,
        )
    else:
        print("\nSUITE SUMMARY: 0 baseline guards inactive; all frozen baselines verified.", flush=True)
    # Metric drift never fails the suite, so a green run is exactly the case in
    # which it would otherwise go unread. Print it last, after the status line.
    if metric_drift_guards:
        print(
            f"SUITE SUMMARY: {len(metric_drift_guards)} baseline guard(s) reported HEADLINE "
            f"METRIC DRIFT beyond threshold: {', '.join(metric_drift_guards)}\n"
            f"               The suite did not fail on this -- the selection fingerprint is\n"
            f"               asserted exactly and separately. Investigate before re-freezing.",
            flush=True,
        )
    else:
        print("SUITE SUMMARY: 0 baseline guards reported headline-metric drift.", flush=True)
    return 0 if status == "OK" else (compile_code or test_code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
