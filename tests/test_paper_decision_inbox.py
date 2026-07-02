import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.paper_decision_inbox import build_paper_decision_inbox, write_paper_decision_inbox


def _signal(symbol, action="BUY", **overrides):
    row = {
        "symbol": symbol,
        "action": action,
        "strategy_name": "fixture_strategy",
        "signal_date": "fixture-signal-date",
        "fill_status": "PENDING_NEXT_OPEN",
        "user_decision": "HOLD_REVIEW",
        "paper_rank": 1,
        "ranking_score": 1.0,
        "liquidity_guard_pass": True,
        "already_in_position": False,
        "paper_actionable": True,
        "reason": "fixture_reason",
        "warning": "",
        "current_position_status": "flat",
    }
    row.update(overrides)
    return row


def _write_base(latest: Path, signals, pending=None, review=None, trades=None, policy=None):
    pd.DataFrame(signals).to_csv(latest / "paper_signal_report.csv", index=False)
    pd.DataFrame(pending or []).to_csv(latest / "paper_pending_orders.csv", index=False)
    pd.DataFrame(review or [], columns=["symbol", "strategy_name", "action", "user_decision", "notes"]).to_csv(
        latest / "paper_order_review.csv",
        index=False,
    )
    pd.DataFrame(trades or []).to_csv(latest / "paper_trade_log.csv", index=False)
    pd.DataFrame(policy or []).to_csv(latest / "data_policy_decisions.csv", index=False)
    (latest / "execution_preview_report.md").write_text(
        "\n".join(
            [
                "- broker_submit_enabled: false",
                "- live_trading_enabled: false",
                "- short_trading_enabled: false",
                "- would_submit count: 0",
            ]
        ),
        encoding="utf-8",
    )
    (latest / "chatgpt_master_report.md").write_text(
        "- submit_order_called: false\n- post_request_used: false\n",
        encoding="utf-8",
    )


class PaperDecisionInboxTest(unittest.TestCase):
    def test_buy_candidate_is_accept_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_base(latest, [_signal("FIXB")])

            inbox = build_paper_decision_inbox(latest)

            self.assertEqual(inbox.iloc[0]["recommended_operator_action"], "ACCEPT_ELIGIBLE")

    def test_buy_already_open_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_base(
                latest,
                [_signal("FIXO")],
                trades=[
                    {
                        "strategy_name": "fixture_strategy",
                        "symbol": "FIXO",
                        "action": "BUY",
                        "shares": 1,
                        "fill_status": "PAPER_FILLED",
                    }
                ],
            )

            inbox = build_paper_decision_inbox(latest)

            self.assertEqual(inbox.iloc[0]["recommended_operator_action"], "BLOCK_DO_NOT_ACCEPT")
            self.assertTrue(bool(inbox.iloc[0]["already_open_position"]))

    def test_sell_with_matching_open_long_is_eligible_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_base(
                latest,
                [_signal("FIXS", action="SELL", reason="fixture_latest_exit_signal", current_position_status="open")],
                trades=[
                    {
                        "strategy_name": "fixture_strategy",
                        "symbol": "FIXS",
                        "action": "BUY",
                        "shares": 1,
                        "fill_status": "PAPER_FILLED",
                    }
                ],
            )

            inbox = build_paper_decision_inbox(latest)

            self.assertEqual(inbox.iloc[0]["recommended_operator_action"], "ACCEPT_ELIGIBLE")
            self.assertTrue(bool(inbox.iloc[0]["valid_exit_signal"]))

    def test_sell_without_open_long_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_base(latest, [_signal("FIXS", action="SELL", reason="fixture_latest_exit_signal")])

            inbox = build_paper_decision_inbox(latest)

            self.assertEqual(inbox.iloc[0]["recommended_operator_action"], "BLOCK_DO_NOT_ACCEPT")

    def test_acked_data_policy_warning_needs_review_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_base(
                latest,
                [_signal("FIXP")],
                policy=[
                    {
                        "symbol": "FIXP",
                        "warning": "fixture_warning",
                        "policy_status": "BLOCK_UNTIL_POLICY_ACK",
                        "operator_decision": "ACK",
                        "operator_note": "fixture acknowledgement",
                    }
                ],
            )

            inbox = build_paper_decision_inbox(latest)

            self.assertEqual(inbox.iloc[0]["recommended_operator_action"], "REVIEW_WARNING")
            self.assertFalse(bool(inbox.iloc[0]["data_policy_blocked"]))

    def test_already_accepted_pending_waits_for_next_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pending = [
                {
                    "symbol": "FIXA",
                    "action": "BUY",
                    "strategy_name": "fixture_strategy",
                    "signal_date": "fixture-signal-date",
                    "status": "PENDING",
                    "user_decision": "ACCEPT",
                    "liquidity_guard_pass": True,
                }
            ]
            _write_base(latest, [], pending=pending)

            inbox = build_paper_decision_inbox(latest)

            self.assertEqual(inbox.iloc[0]["recommended_operator_action"], "WAIT_FOR_NEXT_OPEN")

    def test_already_accepted_buy_now_open_is_block_do_not_accept(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pending = [
                {
                    "symbol": "FIXA",
                    "action": "BUY",
                    "strategy_name": "fixture_strategy",
                    "signal_date": "fixture-signal-date",
                    "status": "PENDING",
                    "user_decision": "ACCEPT",
                    "liquidity_guard_pass": True,
                }
            ]
            _write_base(
                latest,
                [],
                pending=pending,
                trades=[
                    {
                        "strategy_name": "fixture_strategy",
                        "symbol": "FIXA",
                        "action": "BUY",
                        "shares": 1,
                        "fill_status": "PAPER_FILLED",
                    }
                ],
            )

            inbox = build_paper_decision_inbox(latest)

            self.assertEqual(inbox.iloc[0]["recommended_operator_action"], "BLOCK_DO_NOT_ACCEPT")
            self.assertEqual(inbox.iloc[0]["eligibility_status"], "BLOCKED")
            self.assertTrue(bool(inbox.iloc[0]["already_open_position"]))

    def test_write_report_does_not_mutate_paper_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pending = [
                {
                    "symbol": "FIXA",
                    "action": "BUY",
                    "strategy_name": "fixture_strategy",
                    "signal_date": "fixture-signal-date",
                    "status": "PENDING",
                    "user_decision": "ACCEPT",
                    "liquidity_guard_pass": True,
                }
            ]
            _write_base(latest, [], pending=pending)
            pending_path = latest / "paper_pending_orders.csv"
            before = pending_path.read_text(encoding="utf-8")

            result = write_paper_decision_inbox(latest)

            self.assertEqual(pending_path.read_text(encoding="utf-8"), before)
            self.assertTrue(Path(result["csv_path"]).exists())
            self.assertTrue(Path(result["md_path"]).exists())


if __name__ == "__main__":
    unittest.main()
