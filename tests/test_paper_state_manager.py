import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.paper_state_manager import (
    append_trade_log,
    ensure_paper_state_files,
    read_pending_orders,
    read_trade_log,
    write_pending_orders,
)
from testdata.report_schemas import PAPER_PENDING_ORDERS_COLUMNS


class PaperStateManagerTest(unittest.TestCase):
    def test_ensure_paper_state_files_creates_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp)
            ensure_paper_state_files(reports_dir)
            self.assertTrue((reports_dir / "paper_pending_orders.csv").exists())
            self.assertTrue((reports_dir / "paper_order_review.csv").exists())
            self.assertTrue((reports_dir / "paper_trade_log.csv").exists())

    def test_read_pending_orders_empty_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = read_pending_orders(Path(tmp))
            self.assertEqual(list(frame.columns), PAPER_PENDING_ORDERS_COLUMNS)
            self.assertTrue(frame.empty)

    def test_write_pending_orders_adds_missing_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = write_pending_orders(Path(tmp), pd.DataFrame({"symbol": ["AAPL"]}))
            self.assertIn("status", written.columns)
            self.assertEqual(len(written), 1)

    def test_write_pending_orders_keeps_extra_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = write_pending_orders(
                Path(tmp),
                pd.DataFrame({"symbol": ["AAPL"], "extra_col": ["keep"]}),
            )
            self.assertIn("extra_col", written.columns)
            read_back = read_pending_orders(Path(tmp))
            self.assertIn("extra_col", read_back.columns)

    def test_append_trade_log_adds_and_preserves_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp)
            append_trade_log(
                reports_dir,
                [
                    {
                        "date": "2026-06-20",
                        "strategy_name": "test",
                        "symbol": "AAPL",
                        "action": "BUY",
                        "fill_status": "PAPER_FILLED",
                    }
                ],
            )
            log = read_trade_log(reports_dir)
            self.assertEqual(len(log), 1)
            self.assertEqual(log.iloc[0]["symbol"], "AAPL")
            self.assertEqual(log.iloc[0]["fill_status"], "PAPER_FILLED")


if __name__ == "__main__":
    unittest.main()
