import unittest

import pandas as pd

from testdata.execution_engine import build_order_intents


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
