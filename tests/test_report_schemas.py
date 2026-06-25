import unittest

import pandas as pd

from testdata.report_schemas import (
    DATA_POLICY_DECISION_COLUMNS,
    DATA_POLICY_REVIEW_EVIDENCE_COLUMNS,
    PIPELINE_HEALTH_COLUMNS,
    PAPER_ORDER_REVIEW_COLUMNS,
    PAPER_PENDING_ORDERS_COLUMNS,
    PAPER_SIGNAL_COLUMNS,
    PAPER_TRADE_LOG_COLUMNS,
    ensure_columns,
    validate_required_columns,
)
from testdata.runner import (
    _data_policy_decision_columns,
    _data_policy_review_evidence_columns,
    _paper_order_review_columns,
    _paper_pending_order_columns,
    _paper_signal_columns,
    _paper_trade_log_columns,
    _pipeline_health_columns,
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

    def test_runner_uses_central_report_schema_columns(self):
        self.assertEqual(_paper_signal_columns(), PAPER_SIGNAL_COLUMNS)
        self.assertEqual(_paper_pending_order_columns(), PAPER_PENDING_ORDERS_COLUMNS)
        self.assertEqual(_paper_order_review_columns(), PAPER_ORDER_REVIEW_COLUMNS)
        self.assertEqual(_paper_trade_log_columns(), PAPER_TRADE_LOG_COLUMNS)
        self.assertEqual(_pipeline_health_columns(), PIPELINE_HEALTH_COLUMNS)
        self.assertEqual(_data_policy_decision_columns(), DATA_POLICY_DECISION_COLUMNS)
        self.assertEqual(_data_policy_review_evidence_columns(), DATA_POLICY_REVIEW_EVIDENCE_COLUMNS)


if __name__ == "__main__":
    unittest.main()
