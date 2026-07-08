import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.execution_engine import build_order_intents
from testdata.runner import run_apply_paper_order_review_decisions


def _pending(symbol, action, decision="REVIEW_REQUIRED", status="PENDING"):
    return {
        "created_at": "fixture-created",
        "signal_date": "fixture-signal-date",
        "strategy_name": "fixture_strategy",
        "symbol": symbol,
        "action": action,
        "paper_rank": 1,
        "ranking_score": 1.0,
        "paper_capital": 1000.0,
        "paper_position_notional": 100.0,
        "liquidity_guard_pass": True,
        "estimated_dollar_volume": 1000000.0,
        "order_size_pct_of_dollar_volume": 0.01,
        "status": status,
        "user_decision": decision,
        "reason": "fixture_reason",
    }


def _review(symbol, action, decision):
    return {
        "symbol": symbol,
        "strategy_name": "fixture_strategy",
        "action": action,
        "user_decision": decision,
        "notes": "",
    }


class DecisionApplyWorkflowTest(unittest.TestCase):
    def test_review_decisions_propagate_to_pending_before_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "testdata" / "reports" / "latest"
            latest.mkdir(parents=True)
            data_folder = Path(tmp) / "testdata" / "hisseler"
            data_folder.mkdir(parents=True)
            trade_log = latest / "paper_trade_log.csv"
            trade_log.write_text(
                "date,strategy_name,symbol,action,signal_date,signal_price,assumed_fill_price,fill_status,shares,notional,paper_capital,reason,liquidity_guard_pass,notes\n",
                encoding="utf-8",
            )
            before_trade_log = trade_log.read_text(encoding="utf-8")
            pd.DataFrame(
                [
                    _pending("FIXSELL", "SELL"),
                    _pending("FIXBUY", "BUY"),
                    _pending("FIXHOLD", "BUY"),
                ]
            ).to_csv(latest / "paper_pending_orders.csv", index=False)
            pd.DataFrame(
                [
                    _review("FIXSELL", "SELL", "ACCEPT"),
                    _review("FIXBUY", "BUY", "ACCEPT"),
                    _review("FIXHOLD", "BUY", "HOLD_REVIEW"),
                ]
            ).to_csv(latest / "paper_order_review.csv", index=False)

            result = run_apply_paper_order_review_decisions(str(data_folder))

            pending = pd.read_csv(latest / "paper_pending_orders.csv")
            decisions = dict(zip(pending["symbol"], pending["user_decision"]))
            intents, accepted = build_order_intents(pending)
            self.assertEqual(decisions["FIXSELL"], "ACCEPT")
            self.assertEqual(decisions["FIXBUY"], "ACCEPT")
            self.assertEqual(decisions["FIXHOLD"], "HOLD_REVIEW")
            self.assertEqual(set(intents["symbol"]), {"FIXSELL", "FIXBUY"})
            self.assertEqual(set(accepted["symbol"]), {"FIXSELL", "FIXBUY"})
            self.assertEqual(trade_log.read_text(encoding="utf-8"), before_trade_log)
            self.assertFalse(result["summary"]["broker_submit_enabled"])
            self.assertFalse(result["summary"]["live_trading_enabled"])
            self.assertFalse(result["summary"]["post_request_used"])


if __name__ == "__main__":
    unittest.main()
