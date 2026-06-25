import os
import unittest
import tempfile
import shutil
import pandas as pd
from datetime import datetime
from testdata.state_store import (
    init_state_db,
    append_pipeline_run,
    append_paper_orders,
    append_paper_trades,
    append_order_intents,
    append_execution_audit,
    append_pre_submit_checks,
    append_short_readiness_checks,
    get_state_summary
)

class TestStateStore(unittest.TestCase):
    def setUp(self):
        # Create a temp directory for sqlite db
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_trading_state.sqlite3")

    def tearDown(self):
        # Cleanup temp directory
        shutil.rmtree(self.test_dir)

    def test_init_db(self):
        success = init_state_db(self.db_path)
        self.assertTrue(success)
        self.assertTrue(os.path.exists(self.db_path))

        summary = get_state_summary(self.db_path)
        self.assertEqual(summary["status"], "OK")
        self.assertTrue(summary["table_count"] >= 7)

    def test_append_pipeline_run(self):
        init_state_db(self.db_path)
        run_id = "run_123"
        success, err = append_pipeline_run(
            self.db_path,
            run_id=run_id,
            run_datetime=datetime.now().isoformat(),
            command="python scanner.py",
            mode="local-paper",
            status="SUCCESS",
            strategy_metrics_changed=False,
            benchmark_changed=False,
            regression_guard_status="OK",
            total_runtime_sec=42.5
        )
        self.assertTrue(success)
        self.assertIsNone(err)

        summary = get_state_summary(self.db_path)
        self.assertEqual(summary["pipeline_runs_count"], 1)
        self.assertEqual(summary["latest_run_id"], run_id)

    def test_append_order_intents_and_duplicates(self):
        init_state_db(self.db_path)
        run_id = "run_123"
        
        intents_df = pd.DataFrame([{
            "intent_id": "intent_001",
            "idempotency_key": "key_001",
            "idempotency_phase": "PREVIEW",
            "execution_idempotency_key": "PREVIEW:key_001",
            "symbol": "AAPL",
            "side": "BUY",
            "notional": 1000.0,
            "qty": 5,
            "status": "PREVIEW_OK",
            "reason": "Passed filters"
        }])

        success, err = append_order_intents(self.db_path, run_id, intents_df)
        self.assertTrue(success)
        self.assertIsNone(err)

        summary = get_state_summary(self.db_path)
        self.assertEqual(summary["order_intents_count"], 1)

        # Duplicate append of same intent_id under same run_id should be ignored and not crash
        success2, err2 = append_order_intents(self.db_path, run_id, intents_df)
        self.assertTrue(success2)
        self.assertIsNone(err2)

        summary = get_state_summary(self.db_path)
        self.assertEqual(summary["order_intents_count"], 1) # Count remains 1

    def test_append_execution_audit(self):
        init_state_db(self.db_path)
        run_id = "run_123"

        audit_data = [{
            "event": "PREVIEW_INTENT_CREATED",
            "intent_id": "intent_001",
            "idempotency_key": "key_001",
            "idempotency_phase": "PREVIEW",
            "execution_idempotency_key": "PREVIEW:key_001",
            "symbol": "AAPL",
            "action": "BUY",
            "status": "PREVIEW_OK",
            "risk_pass": 1,
            "message": "Preview created"
        }]

        success, err = append_execution_audit(self.db_path, run_id, audit_data)
        self.assertTrue(success)
        self.assertIsNone(err)

        summary = get_state_summary(self.db_path)
        self.assertEqual(summary["execution_audit_count"], 1)

    def test_append_readiness_checks(self):
        init_state_db(self.db_path)
        run_id = "run_123"

        pre_submit_checks = [{
            "check_name": "broker_submit_enabled",
            "status": "BLOCK",
            "value": False,
            "required_value": True,
            "message": "Blocked intentionally"
        }]

        success1, err1 = append_pre_submit_checks(self.db_path, run_id, pre_submit_checks)
        self.assertTrue(success1)
        self.assertIsNone(err1)

        short_checks = [{
            "check_name": "short_trading_enabled",
            "status": "BLOCK",
            "value": False,
            "required_value": False,
            "message": "Blocked"
        }]

        success2, err2 = append_short_readiness_checks(self.db_path, run_id, short_checks)
        self.assertTrue(success2)
        self.assertIsNone(err2)

        summary = get_state_summary(self.db_path)
        self.assertEqual(summary["readiness_checks_count"], 2)

    def test_append_paper_trades_deduplication(self):
        init_state_db(self.db_path)
        run_id = "run_123"
        trades_1 = [{
            "strategy_name": "test_strat",
            "symbol": "MSFT",
            "action": "BUY",
            "signal_date": "2026-06-25",
            "fill_bar_timestamp": "2026-06-25 09:30:00",
            "fill_price_source": "next_5min_open",
            "fill_status": "PAPER_FILLED",
            "shares": 10,
            "notional": 1000.0,
            "paper_capital": 25000.0,
            "reason": "testing",
            "liquidity_guard_pass": True,
            "notes": ""
        }]
        success1, err1 = append_paper_trades(self.db_path, run_id, trades_1)
        self.assertTrue(success1)
        self.assertIsNone(err1)

        trades_2 = [{
            "strategy_name": "test_strat",
            "symbol": "MSFT",
            "action": "BUY",
            "signal_date": "2026-06-25",
            "fill_bar_timestamp": "2026-06-25 09:35:00",
            "fill_price_source": "next_130min_open",
            "fill_status": "PAPER_FILLED",
            "shares": 10,
            "notional": 1000.0,
            "paper_capital": 25000.0,
            "reason": "testing",
            "liquidity_guard_pass": True,
            "notes": ""
        }]
        success2, err2 = append_paper_trades(self.db_path, run_id, trades_2)
        self.assertTrue(success2)
        self.assertIsNone(err2)

        summary = get_state_summary(self.db_path)
        self.assertEqual(summary["paper_trades_count"], 1)


if __name__ == "__main__":
    unittest.main()
