import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from testdata.broker_cutover_baseline import build_broker_cutover_readiness, create_broker_cutover_baseline, write_broker_cutover_readiness
from testdata.broker_reconciliation import ReadOnlyBrokerSnapshot, build_broker_reconciliation
from testdata.runner import run_broker_cutover_readiness, run_create_broker_cutover_baseline


FIXTURE_STRATEGY = "fixture_strategy"


def _trade(symbol="FIXA", shares=2.0, price=10.0):
    return {
        "date": "fixture-fill-date",
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": symbol,
        "action": "BUY",
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


def _write_state(latest: Path, *, trades=None, orders=None):
    latest.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trades or []).to_csv(latest / "paper_trade_log.csv", index=False)
    pd.DataFrame(orders or []).to_csv(latest / "paper_pending_orders.csv", index=False)
    pd.DataFrame([{"symbol": "FIXR", "user_decision": "HOLD_REVIEW"}]).to_csv(latest / "paper_order_review.csv", index=False)


def _snapshot(*, positions=None, orders=None, status="PASS"):
    return ReadOnlyBrokerSnapshot(
        positions=positions or [],
        open_orders=orders or [],
        fills=None,
        source="ALPACA_PAPER_GET_ONLY",
        metadata={
            "alpaca_snapshot_status": status,
            "alpaca_snapshot_reason": "fixture",
            "alpaca_account_summary": {"status": "ACTIVE", "currency": "USD", "portfolio_value": "1000"},
            "alpaca_account_read_success": status == "PASS",
            "alpaca_positions_read_success": status == "PASS",
            "alpaca_open_orders_read_success": status == "PASS",
            "alpaca_endpoint_mode": "paper",
            "alpaca_live_endpoint_blocked": False,
            "request_methods_used": "GET" if status == "PASS" else "none",
        },
    )


class BrokerCutoverBaselineTest(unittest.TestCase):
    def test_pre_cutover_local_only_positions_are_not_cutover_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade()], orders=[])

            readiness = build_broker_cutover_readiness(latest, _snapshot(positions=[]))
            strict = build_broker_reconciliation(latest, _snapshot(positions=[]))

            self.assertEqual(strict["summary"]["reconciliation_status"], "FAIL")
            self.assertEqual(readiness["summary"]["cutover_readiness_status"], "PRE_CUTOVER")
            self.assertEqual(readiness["summary"]["local_broker_gap_status"], "UNSYNCED_LOCAL_ONLY")
            self.assertFalse(readiness["summary"]["broker_cutover_enabled"])
            self.assertFalse(readiness["summary"]["bulk_mirror_historical_positions"])
            self.assertFalse(readiness["summary"]["submit_order_called"])
            self.assertFalse(readiness["summary"]["post_request_used"])

    def test_matching_empty_state_is_ready_but_cutover_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[], orders=[])

            readiness = build_broker_cutover_readiness(latest, _snapshot(positions=[]))

            self.assertEqual(readiness["summary"]["cutover_readiness_status"], "READY")
            self.assertEqual(readiness["summary"]["local_broker_gap_status"], "SYNCED_OR_EMPTY")
            self.assertFalse(readiness["summary"]["broker_cutover_enabled"])

    def test_snapshot_not_available_is_warning_not_state_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade()], orders=[])
            watched = [latest / "paper_trade_log.csv", latest / "paper_pending_orders.csv", latest / "paper_order_review.csv"]
            before = {path.name: path.read_text(encoding="utf-8") for path in watched}

            result = write_broker_cutover_readiness(latest, _snapshot(status="NOT_AVAILABLE"))

            after = {path.name: path.read_text(encoding="utf-8") for path in watched}
            self.assertEqual(before, after)
            self.assertEqual(result["summary"]["cutover_readiness_status"], "NOT_AVAILABLE")
            self.assertFalse((latest / "broker_cutover_baseline.json").exists())

    def test_explicit_baseline_create_writes_forward_only_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade()], orders=[])

            result = create_broker_cutover_baseline(latest, _snapshot(positions=[]))

            self.assertTrue(result["created"])
            baseline_path = latest / "broker_cutover_baseline.json"
            payload = json.loads(baseline_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["forward_only"])
            self.assertFalse(payload["broker_cutover_enabled"])
            self.assertEqual(payload["historical_local_positions_policy"], "LOCAL_ONLY_DO_NOT_MIRROR_TO_ALPACA")
            self.assertEqual(payload["local_historical_open_position_count"], 1)
            self.assertEqual(payload["broker_open_position_count"], 0)
            self.assertEqual(payload["broker_open_order_count"], 0)
            self.assertEqual(payload["broker_account_snapshot_summary"]["status"], "ACTIVE")
            self.assertFalse(payload["submit_order_called"])
            self.assertFalse(payload["post_request_used"])

    def test_baseline_create_requires_pass_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade()], orders=[])

            result = create_broker_cutover_baseline(latest, _snapshot(status="NOT_AVAILABLE"))

            self.assertFalse(result["created"])
            self.assertFalse((latest / "broker_cutover_baseline.json").exists())

    def test_runner_writes_cutover_reports_with_get_only_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_folder = Path(tmp) / "testdata" / "hisseler"
            latest = Path(tmp) / "testdata" / "reports" / "latest"
            data_folder.mkdir(parents=True)
            _write_state(latest, trades=[_trade()], orders=[])
            snapshot = _snapshot(positions=[])

            with patch("testdata.runner.read_alpaca_paper_snapshot", return_value=snapshot) as reader:
                result = run_broker_cutover_readiness(str(data_folder))

            self.assertTrue(reader.called)
            self.assertEqual(result["summary"]["cutover_readiness_status"], "PRE_CUTOVER")
            payload = json.loads((latest / "broker_cutover_readiness.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["local_broker_gap_status"], "UNSYNCED_LOCAL_ONLY")
            self.assertFalse(payload["summary"]["broker_cutover_enabled"])

    def test_runner_explicit_baseline_create_uses_get_only_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_folder = Path(tmp) / "testdata" / "hisseler"
            latest = Path(tmp) / "testdata" / "reports" / "latest"
            data_folder.mkdir(parents=True)
            _write_state(latest, trades=[_trade()], orders=[])
            snapshot = _snapshot(positions=[])

            with patch("testdata.runner.read_alpaca_paper_snapshot", return_value=snapshot) as reader:
                result = run_create_broker_cutover_baseline(str(data_folder))

            self.assertTrue(reader.called)
            self.assertTrue(result["created"])
            payload = json.loads((latest / "broker_cutover_baseline.json").read_text(encoding="utf-8"))
            self.assertTrue(payload["forward_only"])
            self.assertEqual(payload["local_historical_open_position_count"], 1)

    def test_no_submit_cancel_replace_or_post_in_cutover_module(self):
        source = Path("testdata/broker_cutover_baseline.py").read_text(encoding="utf-8")

        for text in ["submit_order(", "cancel_order(", "replace_order(", "method=\"POST\"", "method='POST'"]:
            self.assertNotIn(text, source)


if __name__ == "__main__":
    unittest.main()
