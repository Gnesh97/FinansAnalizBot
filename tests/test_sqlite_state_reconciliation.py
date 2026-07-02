import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.execution_costs import build_execution_cost_preview
from testdata.execution_engine import build_order_intents
from testdata.no_lookahead_audit import build_no_lookahead_audit
from testdata.paper_state_manager import read_trade_log
from testdata.runner import (
    _apply_previous_pending_next_open_orders,
    _paper_signal_columns,
    _write_paper_pending_orders,
)
from testdata.sqlite_state_reconciliation import build_sqlite_state_reconciliation, write_sqlite_state_reconciliation
from testdata.state_store import append_paper_orders, append_paper_trades, append_pipeline_run, init_state_db


FIXTURE_STRATEGY = "fixture_strategy"
FIXTURE_RUN_ID = "fixture_run"
FIXTURE_RUN_DATETIME = "2999-01-01 00:00:00"


def _trade(symbol="FIXA", action="BUY", shares=2.0, price=10.0, signal_date="fixture-signal-date"):
    return {
        "date": "fixture-fill-date",
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": symbol,
        "action": action,
        "signal_date": signal_date,
        "signal_price": price,
        "assumed_fill_price": price,
        "fill_status": "PAPER_FILLED",
        "shares": shares,
        "notional": shares * price,
        "paper_capital": 1000.0,
        "reason": "fixture_reason",
        "liquidity_guard_pass": True,
        "notes": "fixture_notes",
    }


def _order(symbol="FIXA", action="BUY", status="PENDING", decision="ACCEPT", reason="fixture_reason"):
    return {
        "created_at": "fixture-created",
        "signal_date": "fixture-signal-date",
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": symbol,
        "action": action,
        "paper_rank": 1,
        "ranking_score": 1.0,
        "paper_capital": 1000.0,
        "paper_position_notional": 100.0,
        "liquidity_guard_pass": True,
        "status": status,
        "user_decision": decision,
        "reason": reason,
    }


def _write_csv_state(latest: Path, *, trades=None, orders=None, review=None, execution=None):
    pd.DataFrame(trades or []).to_csv(latest / "paper_trade_log.csv", index=False)
    pd.DataFrame(orders or []).to_csv(latest / "paper_pending_orders.csv", index=False)
    pd.DataFrame(review or []).to_csv(latest / "paper_order_review.csv", index=False)
    pd.DataFrame(execution or []).to_csv(latest / "execution_audit_log.csv", index=False)


def _init_db(db_path: Path, *, trades=None, orders=None, execution_rows=None):
    init_state_db(str(db_path))
    append_pipeline_run(
        str(db_path),
        FIXTURE_RUN_ID,
        FIXTURE_RUN_DATETIME,
        "fixture command",
        "fixture",
        "SUCCESS",
        False,
        False,
        "OK",
        0.0,
    )
    if trades is not None:
        append_paper_trades(str(db_path), FIXTURE_RUN_ID, pd.DataFrame(trades))
    if orders is not None:
        append_paper_orders(str(db_path), FIXTURE_RUN_ID, pd.DataFrame(orders))
    if execution_rows:
        conn = sqlite3.connect(str(db_path))
        try:
            for row in execution_rows:
                conn.execute(
                    """
                    INSERT INTO execution_audit (
                        run_id, run_datetime, event_type, order_intent_id, idempotency_phase,
                        execution_idempotency_key, strategy_name, symbol, side, status,
                        risk_pass, block_reason, would_submit, broker_submit_enabled,
                        live_trading_enabled, message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        FIXTURE_RUN_ID,
                        row.get("run_datetime", FIXTURE_RUN_DATETIME),
                        row.get("event_type", "fixture_event"),
                        row.get("order_intent_id", "fixture_intent"),
                        row.get("idempotency_phase", "PREVIEW"),
                        row.get("execution_idempotency_key", "fixture_key"),
                        row.get("strategy_name", FIXTURE_STRATEGY),
                        row.get("symbol", "FIXA"),
                        row.get("side", "BUY"),
                        row.get("status", "PREVIEW_OK"),
                        row.get("risk_pass", 1),
                        row.get("block_reason", ""),
                        row.get("would_submit", 0),
                        row.get("broker_submit_enabled", 0),
                        row.get("live_trading_enabled", 0),
                        row.get("message", "fixture"),
                    ),
                )
            conn.commit()
        finally:
            conn.close()


class SQLiteStateReconciliationTest(unittest.TestCase):
    def test_missing_sqlite_db_reports_clear_failure_without_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            _write_csv_state(latest)

            result = build_sqlite_state_reconciliation(latest, Path(tmp) / "missing.sqlite3")

            self.assertEqual(result["summary"]["reconciliation_status"], "FAIL")
            self.assertFalse(result["database"]["sqlite_db_exists"])

    def test_matching_csv_and_sqlite_rows_produce_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            trades = [_trade()]
            orders = [_order()]
            _write_csv_state(latest, trades=trades, orders=orders)
            _init_db(db, trades=trades, orders=orders)

            result = build_sqlite_state_reconciliation(latest, db)

            self.assertEqual(result["summary"]["reconciliation_status"], "PASS")

    def test_csv_trade_key_missing_in_sqlite_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            _write_csv_state(latest, trades=[_trade()], orders=[])
            _init_db(db, trades=[], orders=[])

            result = build_sqlite_state_reconciliation(latest, db)

            self.assertGreater(result["summary"]["key_mismatch_count"], 0)

    def test_sqlite_extra_trade_key_missing_in_csv_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            _write_csv_state(latest, trades=[], orders=[])
            _init_db(db, trades=[_trade()], orders=[])

            result = build_sqlite_state_reconciliation(latest, db)

            self.assertGreater(result["summary"]["key_mismatch_count"], 0)

    def test_duplicate_trade_key_in_csv_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            trades = [_trade(), _trade()]
            _write_csv_state(latest, trades=trades, orders=[])
            _init_db(db, trades=[_trade()], orders=[])

            result = build_sqlite_state_reconciliation(latest, db)

            self.assertGreater(result["summary"]["duplicate_key_count"], 0)

    def test_accepted_pending_count_mismatch_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            _write_csv_state(latest, trades=[], orders=[_order(decision="ACCEPT")])
            _init_db(db, trades=[], orders=[_order(decision="REVIEW_REQUIRED")])

            result = build_sqlite_state_reconciliation(latest, db)

            self.assertGreater(result["summary"]["table_count_mismatch_count"], 0)

    def test_review_required_rows_are_not_counted_as_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            orders = [_order(decision="REVIEW_REQUIRED")]
            _write_csv_state(latest, trades=[], orders=orders)
            _init_db(db, trades=[], orders=orders)

            result = build_sqlite_state_reconciliation(latest, db)

            accepted = result["checks"][result["checks"]["check_name"].eq("accepted_pending_count")].iloc[0]
            self.assertEqual(int(accepted["csv_value"]), 0)

    def test_blocked_duplicate_buy_is_not_counted_as_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            orders = [_order(status="BLOCKED", decision="REVIEW_REQUIRED", reason="fixture;buy_already_open")]
            _write_csv_state(latest, trades=[], orders=orders)
            _init_db(db, trades=[], orders=orders)

            result = build_sqlite_state_reconciliation(latest, db)

            accepted = result["checks"][result["checks"]["check_name"].eq("accepted_pending_count")].iloc[0]
            self.assertEqual(int(accepted["csv_value"]), 0)

    def test_reconstructed_open_position_count_matches_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            trades = [_trade(symbol="FIXA", shares=2.0), _trade(symbol="FIXA", action="SELL", shares=1.0)]
            _write_csv_state(latest, trades=trades, orders=[])
            _init_db(db, trades=trades, orders=[])

            result = build_sqlite_state_reconciliation(latest, db)

            row = result["checks"][result["checks"]["check_name"].eq("open_position_count_reconstructed")].iloc[0]
            self.assertEqual(row["status"], "PASS")

    def test_report_writer_does_not_mutate_paper_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            trades = [_trade()]
            orders = [_order()]
            _write_csv_state(latest, trades=trades, orders=orders, review=[{"symbol": "FIXR"}])
            _init_db(db, trades=trades, orders=orders)
            watched = [latest / "paper_trade_log.csv", latest / "paper_pending_orders.csv", latest / "paper_order_review.csv"]
            before = {path.name: path.read_text(encoding="utf-8") for path in watched}

            write_sqlite_state_reconciliation(latest, db)

            after = {path.name: path.read_text(encoding="utf-8") for path in watched}
            self.assertEqual(before, after)

    def test_existing_no_lookahead_and_cost_preview_still_pass_synthetic_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_csv_state(latest, trades=[_trade()], orders=[])

            self.assertFalse(build_no_lookahead_audit(latest)["summary"]["state_mutated"])
            self.assertEqual(build_execution_cost_preview(pd.DataFrame([_trade()]))["summary"]["total_execution_cost"], 0.0)

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
            latest = Path(tmp) / "latest"
            latest.mkdir()
            db = Path(tmp) / "state.sqlite3"
            _write_csv_state(latest)
            _init_db(db, trades=[], orders=[])

            summary = build_sqlite_state_reconciliation(latest, db)["summary"]

            self.assertFalse(summary["state_mutated"])
            self.assertFalse(summary["broker_submit_enabled"])
            self.assertFalse(summary["live_trading_enabled"])
            self.assertFalse(summary["short_trading_enabled"])
            self.assertFalse(summary["submit_order_called"])
            self.assertFalse(summary["post_request_used"])

    def test_no_hardcoded_production_dates_or_symbols_in_reconciliation_module(self):
        source = Path("testdata/sqlite_state_reconciliation.py").read_text(encoding="utf-8")

        self.assertNotIn("2026-07", source)
        self.assertNotIn("EQIX", source)
        self.assertNotIn("NUE", source)
        self.assertNotIn("UNH", source)
        self.assertNotIn("CVS", source)


if __name__ == "__main__":
    unittest.main()
