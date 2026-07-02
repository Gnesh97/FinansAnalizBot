import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.broker_reconciliation import (
    ReadOnlyBrokerSnapshot,
    build_broker_reconciliation,
    write_broker_reconciliation,
)
from testdata.execution_costs import build_execution_cost_preview
from testdata.execution_engine import build_order_intents
from testdata.no_lookahead_audit import build_no_lookahead_audit
from testdata.paper_state_manager import read_trade_log
from testdata.runner import (
    _apply_previous_pending_next_open_orders,
    _paper_signal_columns,
    _write_paper_pending_orders,
)
from testdata.sqlite_state_reconciliation import build_sqlite_state_reconciliation
from testdata.state_store import append_paper_orders, append_paper_trades, append_pipeline_run, init_state_db


FIXTURE_STRATEGY = "fixture_strategy"


def _trade(symbol="FIXA", action="BUY", shares=2.0, price=10.0):
    return {
        "date": "fixture-fill-date",
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": symbol,
        "action": action,
        "signal_date": "fixture-signal-date",
        "signal_price": price,
        "assumed_fill_price": price,
        "fill_status": "PAPER_FILLED",
        "shares": shares,
        "notional": shares * price,
        "paper_capital": 1000.0,
        "reason": "fixture_reason",
        "liquidity_guard_pass": True,
    }


def _order(symbol="FIXA", action="BUY", status="PENDING", decision="ACCEPT", reason="fixture_reason"):
    return {
        "created_at": "fixture-created",
        "signal_date": "fixture-signal-date",
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": symbol,
        "action": action,
        "paper_position_notional": 100.0,
        "liquidity_guard_pass": True,
        "status": status,
        "user_decision": decision,
        "reason": reason,
    }


def _write_state(latest: Path, *, trades=None, orders=None):
    pd.DataFrame(trades or []).to_csv(latest / "paper_trade_log.csv", index=False)
    pd.DataFrame(orders or []).to_csv(latest / "paper_pending_orders.csv", index=False)
    pd.DataFrame([{"symbol": "FIXR", "user_decision": "HOLD_REVIEW"}]).to_csv(
        latest / "paper_order_review.csv",
        index=False,
    )
    pd.DataFrame().to_csv(latest / "execution_audit_log.csv", index=False)


def _snapshot(*, positions=None, orders=None, fills=None, live=False):
    return ReadOnlyBrokerSnapshot(
        positions=positions or [],
        open_orders=orders or [],
        fills=fills,
        live_endpoint=live,
        live_trading_enabled=live,
    )


class BrokerReconciliationTest(unittest.TestCase):
    def test_no_broker_snapshot_warns_without_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest)

            result = build_broker_reconciliation(latest)

            self.assertEqual(result["summary"]["reconciliation_status"], "WARNING")
            self.assertEqual(result["summary"]["broker_snapshot_source"], "NONE")

    def test_matching_mock_broker_positions_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade("FIXA", "BUY", 2.0, 10.0)], orders=[])

            result = build_broker_reconciliation(
                latest,
                _snapshot(positions=[{"symbol": "FIXA", "qty": 2.0, "avg_price": 10.0}], fills=[]),
            )

            self.assertEqual(result["summary"]["position_mismatch_count"], 0)

    def test_broker_missing_local_position_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade()], orders=[])

            result = build_broker_reconciliation(latest, _snapshot(positions=[], fills=[]))

            self.assertGreater(result["summary"]["position_mismatch_count"], 0)

    def test_broker_extra_position_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[], orders=[])

            result = build_broker_reconciliation(latest, _snapshot(positions=[{"symbol": "FIXA", "qty": 1.0}], fills=[]))

            self.assertGreater(result["summary"]["position_mismatch_count"], 0)

    def test_share_quantity_mismatch_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade(shares=2.0)], orders=[])

            result = build_broker_reconciliation(latest, _snapshot(positions=[{"symbol": "FIXA", "qty": 1.0}], fills=[]))

            row = result["checks"][result["checks"]["check_name"].eq("share_qty_mismatch_count")].iloc[0]
            self.assertEqual(row["status"], "FAIL")

    def test_matching_local_pending_order_and_broker_open_order_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[], orders=[_order("FIXA", "SELL")])

            result = build_broker_reconciliation(latest, _snapshot(orders=[{"symbol": "FIXA", "side": "SELL"}], fills=[]))

            self.assertEqual(result["summary"]["order_mismatch_count"], 0)

    def test_broker_extra_open_order_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[], orders=[])

            result = build_broker_reconciliation(latest, _snapshot(orders=[{"symbol": "FIXA", "side": "BUY"}], fills=[]))

            self.assertGreater(result["summary"]["order_mismatch_count"], 0)

    def test_local_accepted_pending_missing_from_broker_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[], orders=[_order("FIXA", "BUY")])

            result = build_broker_reconciliation(latest, _snapshot(orders=[], fills=[]))

            self.assertGreater(result["summary"]["order_mismatch_count"], 0)

    def test_review_required_rows_are_not_executable_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[], orders=[_order("FIXA", "BUY", decision="REVIEW_REQUIRED")])

            result = build_broker_reconciliation(latest, _snapshot(orders=[], fills=[]))

            self.assertEqual(result["summary"]["order_mismatch_count"], 0)

    def test_blocked_duplicate_buy_rows_are_not_executable_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[], orders=[_order("FIXA", "BUY", status="BLOCKED", decision="REVIEW_REQUIRED", reason="fixture;buy_already_open")])

            result = build_broker_reconciliation(latest, _snapshot(orders=[], fills=[]))

            self.assertEqual(result["summary"]["order_mismatch_count"], 0)

    def test_duplicate_broker_orders_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[], orders=[_order("FIXA", "BUY")])

            result = build_broker_reconciliation(
                latest,
                _snapshot(orders=[{"symbol": "FIXA", "side": "BUY"}, {"symbol": "FIXA", "side": "BUY"}], fills=[]),
            )

            row = result["checks"][result["checks"]["check_name"].eq("duplicate_broker_order_count")].iloc[0]
            self.assertEqual(row["status"], "FAIL")

    def test_live_endpoint_snapshot_hard_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest)

            result = build_broker_reconciliation(latest, _snapshot(live=True, fills=[]))

            self.assertEqual(result["summary"]["reconciliation_status"], "FAIL")

    def test_writer_does_not_mutate_paper_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade()], orders=[_order()])
            watched = [latest / "paper_trade_log.csv", latest / "paper_pending_orders.csv", latest / "paper_order_review.csv"]
            before = {path.name: path.read_text(encoding="utf-8") for path in watched}

            write_broker_reconciliation(latest)

            after = {path.name: path.read_text(encoding="utf-8") for path in watched}
            self.assertEqual(before, after)

    def test_existing_audit_cost_and_sqlite_smoke_checks_still_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            trades = [_trade()]
            orders = [_order()]
            _write_state(latest, trades=trades, orders=orders)
            init_state_db(str(db))
            append_pipeline_run(str(db), "fixture_run", "2999-01-01", "fixture", "fixture", "SUCCESS", False, False, "OK", 0.0)
            append_paper_trades(str(db), "fixture_run", pd.DataFrame(trades))
            append_paper_orders(str(db), "fixture_run", pd.DataFrame(orders))

            self.assertEqual(build_execution_cost_preview(pd.DataFrame(trades))["summary"]["total_execution_cost"], 0.0)
            self.assertFalse(build_no_lookahead_audit(latest)["summary"]["state_mutated"])
            self.assertEqual(build_sqlite_state_reconciliation(latest, db)["summary"]["key_mismatch_count"], 0)

    def test_existing_duplicate_buy_block_behavior_remains_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame([_trade("FIXA", "BUY", 1.0)]).to_csv(latest / "paper_trade_log.csv", index=False)
            report = pd.DataFrame(
                [
                    {
                        "run_datetime": "fixture-run",
                        "signal_date": "fixture-signal-date-2",
                        "strategy_name": FIXTURE_STRATEGY,
                        "symbol": "FIXA",
                        "action": "BUY",
                        "paper_actionable": True,
                        "user_decision": "ACCEPT",
                        "fill_status": "PENDING_NEXT_OPEN",
                        "reason": "fixture_latest_entry_signal",
                        "liquidity_guard_pass": True,
                    }
                ],
                columns=_paper_signal_columns(),
            )

            _write_paper_pending_orders(report, latest / "paper_pending_orders.csv", trade_log_path=latest / "paper_trade_log.csv")

            pending = pd.read_csv(latest / "paper_pending_orders.csv")
            intents, _ = build_order_intents(pending)
            self.assertEqual(pending.iloc[0]["status"], "BLOCKED")
            self.assertTrue(intents.empty)

    def test_existing_sell_quantity_matching_behavior_remains_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame([_trade("FIXA", "BUY", 3.0, 10.0)]).to_csv(latest / "paper_trade_log.csv", index=False)
            pending = pd.DataFrame(
                [
                    {
                        "run_datetime": "fixture-run",
                        "strategy_name": FIXTURE_STRATEGY,
                        "signal_date": "2026-01-02 13:50:00-04:00",
                        "symbol": "FIXA",
                        "action": "SELL",
                        "fill_status": "PENDING_NEXT_OPEN",
                        "user_decision": "ACCEPT",
                        "paper_actionable": True,
                        "paper_position_notional": 999.0,
                    }
                ]
            )
            asset_data = {
                "FIXA": pd.DataFrame(
                    {"Open": [10.0, 20.0]},
                    index=pd.to_datetime(["2026-01-02 13:50:00-04:00", "2026-01-05 09:30:00-04:00"]),
                )
            }

            _apply_previous_pending_next_open_orders(pd.DataFrame(columns=_paper_signal_columns()), pending, pd.DataFrame(), latest / "paper_trade_log.csv", asset_data)

            sell = read_trade_log(latest)[lambda frame: frame["action"].astype(str).str.upper().eq("SELL")].iloc[0]
            self.assertEqual(float(sell["shares"]), 3.0)

    def test_safety_flags_remain_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest)

            summary = build_broker_reconciliation(latest)["summary"]

            self.assertFalse(summary["broker_submit_enabled"])
            self.assertFalse(summary["live_trading_enabled"])
            self.assertFalse(summary["short_trading_enabled"])
            self.assertFalse(summary["submit_order_called"])
            self.assertFalse(summary["post_request_used"])
            self.assertEqual(summary["would_submit_count"], 0)
            self.assertTrue(summary["read_only_mode"])

    def test_no_hardcoded_production_dates_or_symbols_in_module(self):
        source = Path("testdata/broker_reconciliation.py").read_text(encoding="utf-8")

        self.assertNotIn("2026-07", source)
        self.assertNotIn("EQIX", source)
        self.assertNotIn("NUE", source)
        self.assertNotIn("UNH", source)
        self.assertNotIn("CVS", source)


if __name__ == "__main__":
    unittest.main()
