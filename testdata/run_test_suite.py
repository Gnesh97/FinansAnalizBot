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
    "testdata/broker_ledger.py",
    "testdata/broker_lifecycle.py",
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


def _failed_tests(output: str) -> list[str]:
    failures = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(("FAIL:", "ERROR:")):
            failures.append(stripped)
    return failures


def _write_report(status: str, command: str, duration: float, tests_ran: int, failures: list[str]) -> None:
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Test Run Report",
        "",
        f"- run datetime: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- status: {status}",
        f"- tests ran count: {tests_ran}",
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
    _write_report(
        status,
        command_text,
        time.perf_counter() - started,
        _tests_ran_count(combined),
        _failed_tests(combined),
    )
    return 0 if status == "OK" else (compile_code or test_code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
