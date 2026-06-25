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


if __name__ == "__main__":
    unittest.main()
