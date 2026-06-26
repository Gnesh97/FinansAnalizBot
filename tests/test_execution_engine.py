import unittest

import pandas as pd

from testdata.execution_engine import build_order_intents, execution_idempotency_key


class ExecutionEngineTest(unittest.TestCase):
    def test_pending_accept_order_creates_intent(self):
        pending = pd.DataFrame(
            [
                {
                    "created_at": "2026-06-20",
                    "signal_date": "2026-06-19",
                    "strategy_name": "s",
                    "symbol": "AAPL",
                    "action": "BUY",
                    "paper_position_notional": 100,
                    "status": "PENDING_NEXT_OPEN",
                    "user_decision": "ACCEPT",
                }
            ]
        )
        intents, _ = build_order_intents(pending)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents.iloc[0]["status"], "PREVIEW_ONLY")
        self.assertTrue(str(intents.iloc[0]["order_intent_id"]))
        self.assertEqual(intents.iloc[0]["order_intent_id"], intents.iloc[0]["idempotency_key"])
        self.assertEqual(intents.iloc[0]["idempotency_phase"], "PREVIEW")
        self.assertTrue(str(intents.iloc[0]["execution_idempotency_key"]).startswith("PREVIEW:"))

    def test_same_pending_order_creates_same_id(self):
        pending = pd.DataFrame(
            [
                {
                    "created_at": "2026-06-20",
                    "signal_date": "2026-06-19",
                    "strategy_name": "s",
                    "symbol": "AAPL",
                    "action": "BUY",
                    "paper_position_notional": 100,
                    "status": "PENDING_NEXT_OPEN",
                    "user_decision": "ACCEPT",
                }
            ]
        )
        first, _ = build_order_intents(pending)
        second, _ = build_order_intents(pending)
        self.assertEqual(first.iloc[0]["order_intent_id"], second.iloc[0]["order_intent_id"])
        self.assertEqual(first.iloc[0]["execution_idempotency_key"], second.iloc[0]["execution_idempotency_key"])

    def test_preview_and_submit_phase_keys_differ(self):
        base_key = "abc123"
        self.assertNotEqual(
            execution_idempotency_key(base_key, "PREVIEW"),
            execution_idempotency_key(base_key, "SUBMIT"),
        )

    def test_hold_review_order_does_not_create_intent(self):
        pending = pd.DataFrame(
            [{"symbol": "AAPL", "status": "PENDING_NEXT_OPEN", "user_decision": "HOLD_REVIEW"}]
        )
        intents, _ = build_order_intents(pending)
        self.assertTrue(intents.empty)

    def test_reject_order_does_not_create_intent(self):
        pending = pd.DataFrame(
            [{"symbol": "AAPL", "status": "PENDING_NEXT_OPEN", "user_decision": "REJECT"}]
        )
        intents, _ = build_order_intents(pending)
        self.assertTrue(intents.empty)

    def test_write_chatgpt_action_report_files_changed(self):
        import tempfile
        from pathlib import Path
        from testdata.execution_engine import write_chatgpt_action_report
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            write_chatgpt_action_report(
                latest_path=latest,
                paper_submit_enabled=False,
                post_request_used=False,
                submitted_count=0,
                selected_symbols=[],
                blocked_symbols=[],
                files_changed=None
            )
            content = (latest / "chatgpt_action_report.md").read_text(encoding="utf-8")
            self.assertIn("3. Files changed: none", content)

            write_chatgpt_action_report(
                latest_path=latest,
                paper_submit_enabled=False,
                post_request_used=False,
                submitted_count=0,
                selected_symbols=[],
                blocked_symbols=[],
                files_changed=["a.py", "b.py"]
            )
            content = (latest / "chatgpt_action_report.md").read_text(encoding="utf-8")
            self.assertIn("3. Files changed: a.py, b.py", content)

    def test_action_to_order_side_mappings(self):
        from testdata.execution_engine import action_to_order_side, build_order_intents
        from testdata.risk_gate import evaluate_order_risk
        
        self.assertEqual(action_to_order_side("BUY"), "BUY")
        self.assertEqual(action_to_order_side("SELL"), "SELL")
        self.assertEqual(action_to_order_side("HOLD"), "INVALID_ACTION")
        self.assertEqual(action_to_order_side(""), "INVALID_ACTION")
        
        pending = pd.DataFrame(
            [
                {
                    "created_at": "2026-06-20",
                    "signal_date": "2026-06-19",
                    "strategy_name": "s",
                    "symbol": "AAPL",
                    "action": "HOLD",
                    "paper_position_notional": 100,
                    "status": "PENDING_NEXT_OPEN",
                    "user_decision": "ACCEPT",
                }
            ]
        )
        intents, accepted = build_order_intents(pending)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents.iloc[0]["side"], "INVALID_ACTION")
        
        risk = evaluate_order_risk(intents, accepted, max_new_orders_per_run=5)
        self.assertEqual(len(risk), 1)
        self.assertIn("invalid_order_side", risk.iloc[0]["block_reason"])

    def test_live_endpoint_disabled_submit_no_crash(self):
        import tempfile
        import os
        from pathlib import Path
        from testdata.execution_engine import run_alpaca_paper_submit
        
        orig_url = os.environ.get("ALPACA_BASE_URL")
        try:
            os.environ["ALPACA_BASE_URL"] = "https://api.alpaca.markets"
            with tempfile.TemporaryDirectory() as tmp:
                # Write mock pending orders
                pending_df = pd.DataFrame([
                    {
                        "created_at": "2026-06-20",
                        "signal_date": "2026-06-19",
                        "strategy_name": "s",
                        "symbol": "AAPL",
                        "action": "BUY",
                        "paper_position_notional": 20,
                        "status": "PENDING_NEXT_OPEN",
                        "user_decision": "ACCEPT",
                        "liquidity_guard_pass": True,
                    }
                ])
                pending_df.to_csv(Path(tmp) / "paper_pending_orders.csv", index=False)
                
                # Should not crash on live endpoint with disabled submit
                run_alpaca_paper_submit(
                    latest_dir=tmp,
                    paper_submit_enabled=False,
                    make_chatgpt_report=True
                )
                
                # Check report contents
                report = (Path(tmp) / "alpaca_paper_submit_report.md").read_text(encoding="utf-8")
                self.assertIn("live_endpoint_rejected: true", report)
                
                chatgpt_report = (Path(tmp) / "chatgpt_action_report.md").read_text(encoding="utf-8")
                self.assertIn("12. POST used: false", chatgpt_report)
                self.assertIn("14. submit_order_called: false", chatgpt_report)
        finally:
            if orig_url is not None:
                os.environ["ALPACA_BASE_URL"] = orig_url
            elif "ALPACA_BASE_URL" in os.environ:
                del os.environ["ALPACA_BASE_URL"]

    def test_failed_submit_reporting(self):
        import tempfile
        import os
        from pathlib import Path
        from unittest.mock import patch
        from testdata.execution_engine import run_alpaca_paper_submit
        
        orig_url = os.environ.get("ALPACA_BASE_URL")
        try:
            os.environ["ALPACA_BASE_URL"] = "https://paper-api.alpaca.markets"
            with tempfile.TemporaryDirectory() as tmp:
                # Write mock pending orders
                pending_df = pd.DataFrame([
                    {
                        "created_at": "2026-06-20",
                        "signal_date": "2026-06-19",
                        "strategy_name": "s",
                        "symbol": "AAPL",
                        "action": "BUY",
                        "paper_position_notional": 20,
                        "status": "PENDING_NEXT_OPEN",
                        "user_decision": "ACCEPT",
                        "liquidity_guard_pass": True,
                    }
                ])
                pending_df.to_csv(Path(tmp) / "paper_pending_orders.csv", index=False)
                
                # Patch AlpacaPaperBroker to throw an exception in submit_paper_order
                with patch('testdata.alpaca_paper_broker.AlpacaPaperBroker') as MockBroker:
                    mock_instance = MockBroker.return_value
                    mock_instance.submit_paper_order.side_effect = RuntimeError("Simulated Alpaca Failure")
                    
                    run_alpaca_paper_submit(
                        latest_dir=tmp,
                        paper_submit_enabled=True,
                        confirm_token="CONFIRM_PAPER_SUBMIT",
                        make_chatgpt_report=True
                    )
                    
                    # Verify status is SUBMIT_FAILED in the output csv
                    preview_df = pd.read_csv(Path(tmp) / "alpaca_paper_submit_preview.csv")
                    self.assertEqual(preview_df.iloc[0]["status"], "SUBMIT_FAILED")
                    self.assertIn("Simulated Alpaca Failure", preview_df.iloc[0]["reason"])
                    
                    # Verify report contents
                    report = (Path(tmp) / "alpaca_paper_submit_report.md").read_text(encoding="utf-8")
                    self.assertIn("submit_order_called: true", report)
                    self.assertIn("post_request_used: true", report)
                    self.assertIn("submitted_count: 0", report)
                    
                    chatgpt_report = (Path(tmp) / "chatgpt_action_report.md").read_text(encoding="utf-8")
                    self.assertIn("12. POST used: true", chatgpt_report)
                    self.assertIn("13. Broker order submitted: false", chatgpt_report)
                    self.assertIn("14. submit_order_called: true", chatgpt_report)
                    self.assertIn("15. post_request_used: true", chatgpt_report)
        finally:
            if orig_url is not None:
                os.environ["ALPACA_BASE_URL"] = orig_url
            elif "ALPACA_BASE_URL" in os.environ:
                del os.environ["ALPACA_BASE_URL"]

    def test_write_alpaca_order_preview_uses_pending_fill_status(self):
        import tempfile
        from pathlib import Path
        from testdata.runner import _write_alpaca_order_preview
        
        with tempfile.TemporaryDirectory() as tmp:
            # Write a pending order with fill_status="PENDING_NEXT_OPEN" but no signal report existing at all
            pending_df = pd.DataFrame([
                {
                    "created_at": "2026-06-20",
                    "signal_date": "2026-06-19",
                    "strategy_name": "s",
                    "symbol": "AAPL",
                    "action": "BUY",
                    "entry_price_reference": 150.0,
                    "paper_position_notional": 300,
                    "fill_status": "PENDING_NEXT_OPEN",
                    "user_decision": "ACCEPT",
                }
            ])
            pending_df.to_csv(Path(tmp) / "paper_pending_orders.csv", index=False)
            
            output_csv = Path(tmp) / "alpaca_order_preview.csv"
            preview = _write_alpaca_order_preview(Path(tmp), output_csv)
            
            # Check that it processed correctly and has AAPL order in preview
            self.assertEqual(len(preview), 1)
            self.assertEqual(preview.iloc[0]["symbol"], "AAPL")
            self.assertEqual(preview.iloc[0]["action"], "BUY")
            self.assertEqual(float(preview.iloc[0]["estimated_qty"]), 2.0)


if __name__ == "__main__":
    unittest.main()
