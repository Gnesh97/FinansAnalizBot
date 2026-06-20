import unittest

import pandas as pd

from testdata.report_schemas import (
    PAPER_PENDING_ORDERS_COLUMNS,
    PAPER_SIGNAL_COLUMNS,
    PAPER_TRADE_LOG_COLUMNS,
    ensure_columns,
    validate_required_columns,
)


class ReportSchemasTest(unittest.TestCase):
    def test_paper_signal_columns_not_empty(self):
        self.assertTrue(PAPER_SIGNAL_COLUMNS)

    def test_pending_orders_required_status_columns(self):
        self.assertIn("status", PAPER_PENDING_ORDERS_COLUMNS)
        self.assertIn("user_decision", PAPER_PENDING_ORDERS_COLUMNS)

    def test_trade_log_has_fill_status(self):
        self.assertIn("fill_status", PAPER_TRADE_LOG_COLUMNS)

    def test_ensure_columns_adds_missing_columns(self):
        frame = ensure_columns(pd.DataFrame({"a": [1]}), ["a", "b"])
        self.assertIn("b", frame.columns)

    def test_validate_required_columns_reports_missing(self):
        validation = validate_required_columns(pd.DataFrame({"a": [1]}), ["a", "b"])
        self.assertFalse(validation["ok"])
        self.assertEqual(validation["missing_columns"], ["b"])

    def test_ensure_columns_keeps_extra_columns(self):
        frame = ensure_columns(pd.DataFrame({"a": [1], "extra": [2]}), ["a", "b"])
        self.assertIn("extra", frame.columns)
        self.assertEqual(list(frame.columns), ["a", "b", "extra"])


if __name__ == "__main__":
    unittest.main()
