"""Tests for the inactive-baseline-guard announcement (Phase 7C).

Guards the mechanism that stops a green suite from implying a verified baseline
when testdata/research_data/ is absent.
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_guard_visibility as visibility
import testdata.run_test_suite as suite
from testdata.run_test_suite import _inactive_baseline_guards, _write_report


class AnnounceIfInactiveTest(unittest.TestCase):
    def setUp(self):
        visibility.reset_for_testing()
        self.addCleanup(visibility.reset_for_testing)

    def test_active_guard_is_silent_and_returns_true(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = visibility.announce_if_inactive("g", True, reason="r", restore_hint="h")
        self.assertTrue(result)
        self.assertEqual(buffer.getvalue(), "")
        self.assertEqual(visibility.announced_guards(), [])

    def test_inactive_guard_prints_named_marker_and_returns_false(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = visibility.announce_if_inactive(
                "rs_v2_tiingo_strategy_baseline", False,
                reason="dataset missing", restore_hint="fetch it",
            )
        output = buffer.getvalue()
        self.assertFalse(result)
        self.assertIn(visibility.MARKER_PREFIX, output)
        self.assertIn("rs_v2_tiingo_strategy_baseline", output)
        self.assertIn("dataset missing", output)
        self.assertIn("fetch it", output)
        self.assertIn("DID NOT RUN", output)
        self.assertEqual(visibility.announced_guards(), ["rs_v2_tiingo_strategy_baseline"])

    def test_announcement_is_emitted_once_per_guard(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            visibility.announce_if_inactive("g", False, reason="r", restore_hint="h")
            visibility.announce_if_inactive("g", False, reason="r", restore_hint="h")
        self.assertEqual(buffer.getvalue().count(visibility.MARKER_PREFIX), 1)


class SuiteSummaryTest(unittest.TestCase):
    def _write_to_temp(self, inactive_guards):
        with tempfile.TemporaryDirectory() as tmp:
            original = suite.LATEST_DIR
            suite.LATEST_DIR = Path(tmp)
            try:
                _write_report("OK", "cmd", 1.0, 730, [], inactive_guards)
                return (Path(tmp) / "test_run_report.md").read_text(encoding="utf-8")
            finally:
                suite.LATEST_DIR = original

    def test_parser_extracts_guard_names_from_suite_output(self):
        output = (
            "test_a (tests.x) ... ok\n"
            f"{visibility.MARKER_PREFIX} guard=rs_v2_tiingo_strategy_baseline\n"
            "  THIS REGRESSION GUARD DID NOT RUN.\n"
            f"{visibility.MARKER_PREFIX} guard=rs_v2_sip_daily_only_equivalence\n"
            "OK (skipped=7)\n"
        )
        self.assertEqual(
            _inactive_baseline_guards(output),
            ["rs_v2_sip_daily_only_equivalence", "rs_v2_tiingo_strategy_baseline"],
        )

    def test_parser_returns_empty_when_all_guards_active(self):
        self.assertEqual(_inactive_baseline_guards("Ran 730 tests\nOK\n"), [])

    def test_report_records_inactive_guards_alongside_a_green_status(self):
        content = self._write_to_temp(["rs_v2_tiingo_strategy_baseline"])
        self.assertIn("- status: OK", content)
        self.assertIn("- inactive baseline guards: 1", content)
        self.assertIn("## Inactive Baseline Guards", content)
        self.assertIn("rs_v2_tiingo_strategy_baseline", content)

    def test_report_says_none_when_every_guard_ran(self):
        content = self._write_to_temp([])
        self.assertIn("- inactive baseline guards: 0", content)
        self.assertIn("- none (all baseline guards active)", content)


if __name__ == "__main__":
    unittest.main()
