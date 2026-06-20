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


if __name__ == "__main__":
    unittest.main()
