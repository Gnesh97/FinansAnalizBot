import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.error

import pandas as pd

from testdata.alpaca_paper_snapshot import AlpacaPaperSnapshotClient
from testdata.broker_reconciliation import ReadOnlyBrokerSnapshot, build_broker_reconciliation, write_broker_reconciliation
from testdata.runner import run_broker_reconciliation


FIXTURE_STRATEGY = "fixture_strategy"


class FakeResponse:
    def __init__(self, payload, raw=None):
        self.payload = raw if raw is not None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class FakeUrlopen:
    def __init__(self, payloads=None, fail=False, exception=None, raw_payloads=None):
        self.payloads = payloads or {}
        self.raw_payloads = raw_payloads or {}
        self.fail = fail
        self.exception = exception
        self.requests = []

    def __call__(self, req, timeout=20):
        self.requests.append(req)
        if self.exception:
            raise self.exception
        if self.fail:
            raise OSError("fixture read failed")
        url = req.full_url
        if "/v2/account" in url:
            return FakeResponse(self.payloads.get("account", {"status": "ACTIVE"}), self.raw_payloads.get("account"))
        if "/v2/positions" in url:
            return FakeResponse(self.payloads.get("positions", []), self.raw_payloads.get("positions"))
        if "/v2/orders" in url:
            return FakeResponse(self.payloads.get("orders", []), self.raw_payloads.get("orders"))
        raise AssertionError(url)


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


def _order(symbol="FIXA", action="BUY", status="PENDING", decision="ACCEPT"):
    return {
        "created_at": "fixture-created",
        "signal_date": "fixture-signal-date",
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": symbol,
        "action": action,
        "paper_position_notional": 100.0,
        "status": status,
        "user_decision": decision,
        "reason": "fixture_reason",
    }


def _write_state(latest: Path, *, trades=None, orders=None):
    latest.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trades or []).to_csv(latest / "paper_trade_log.csv", index=False)
    pd.DataFrame(orders or []).to_csv(latest / "paper_pending_orders.csv", index=False)
    pd.DataFrame([{"symbol": "FIXR", "user_decision": "HOLD_REVIEW"}]).to_csv(latest / "paper_order_review.csv", index=False)


def _snapshot(*, positions=None, orders=None, status="PASS", fills=None):
    return ReadOnlyBrokerSnapshot(
        positions=positions or [],
        open_orders=orders or [],
        fills=[] if fills is None else fills,
        source="ALPACA_PAPER_GET_ONLY",
        metadata={
            "alpaca_snapshot_status": status,
            "alpaca_snapshot_reason": "fixture",
            "alpaca_account_read_success": status == "PASS",
            "alpaca_positions_read_success": status == "PASS",
            "alpaca_open_orders_read_success": status == "PASS",
            "alpaca_endpoint_mode": "paper",
            "alpaca_live_endpoint_blocked": False,
            "request_methods_used": "GET" if status == "PASS" else "none",
        },
    )


class AlpacaPaperSnapshotTest(unittest.TestCase):
    def test_default_broker_reconciliation_does_not_call_alpaca(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_folder = Path(tmp) / "testdata" / "hisseler"
            data_folder.mkdir(parents=True)
            with patch("testdata.alpaca_broker_reporting.read_alpaca_paper_snapshot", side_effect=AssertionError("alpaca called")) as reader:
                result = run_broker_reconciliation(str(data_folder))

            self.assertFalse(reader.called)
            self.assertEqual(result["summary"]["broker_snapshot_source"], "NONE")

    def test_explicit_get_only_adapter_uses_get_requests(self):
        opener = FakeUrlopen()

        snapshot = AlpacaPaperSnapshotClient(
            api_key="fixture-key",
            secret_key="fixture-secret",
            base_url="https://paper-api.alpaca.markets",
            urlopen=opener,
        ).read_snapshot()

        self.assertEqual(snapshot.source, "ALPACA_PAPER_GET_ONLY")
        self.assertEqual(snapshot.metadata["alpaca_snapshot_status"], "PASS")
        self.assertEqual([req.get_method() for req in opener.requests], ["GET", "GET", "GET"])

    def test_missing_credentials_returns_not_available_without_request(self):
        opener = FakeUrlopen()

        snapshot = AlpacaPaperSnapshotClient(base_url="https://paper-api.alpaca.markets", urlopen=opener).read_snapshot()

        self.assertEqual(snapshot.metadata["alpaca_snapshot_status"], "NOT_AVAILABLE")
        self.assertEqual(snapshot.metadata["alpaca_snapshot_reason"], "missing_credentials")
        self.assertEqual(snapshot.metadata["alpaca_failure_category"], "MISSING_CREDENTIALS")
        self.assertEqual(opener.requests, [])

    def test_auth_failure_reports_sanitized_category(self):
        opener = FakeUrlopen(exception=urllib.error.HTTPError("https://paper-api.alpaca.markets/v2/account", 401, "Unauthorized fixture secret", {}, None))

        snapshot = AlpacaPaperSnapshotClient(
            api_key="fixture-key",
            secret_key="fixture-secret",
            base_url="https://paper-api.alpaca.markets",
            urlopen=opener,
        ).read_snapshot()

        self.assertEqual(snapshot.metadata["alpaca_snapshot_status"], "NOT_AVAILABLE")
        self.assertEqual(snapshot.metadata["alpaca_failure_category"], "AUTH_FAILED")
        self.assertEqual(snapshot.metadata["alpaca_failure_endpoint"], "/v2/account")
        self.assertEqual(snapshot.metadata["request_methods_used"], "GET")
        self.assertNotIn("fixture-secret", json.dumps(snapshot.metadata))

    def test_network_failure_reports_network_error(self):
        opener = FakeUrlopen(exception=urllib.error.URLError("temporary dns failure"))

        snapshot = AlpacaPaperSnapshotClient(
            api_key="fixture-key",
            secret_key="fixture-secret",
            base_url="https://paper-api.alpaca.markets",
            urlopen=opener,
        ).read_snapshot()

        self.assertEqual(snapshot.metadata["alpaca_failure_category"], "NETWORK_ERROR")
        self.assertEqual(snapshot.metadata["alpaca_snapshot_reason"], "NETWORK_ERROR")

    def test_timeout_reports_timeout(self):
        opener = FakeUrlopen(exception=socket.timeout("timed out"))

        snapshot = AlpacaPaperSnapshotClient(
            api_key="fixture-key",
            secret_key="fixture-secret",
            base_url="https://paper-api.alpaca.markets",
            urlopen=opener,
        ).read_snapshot()

        self.assertEqual(snapshot.metadata["alpaca_failure_category"], "TIMEOUT")
        self.assertEqual(snapshot.metadata["alpaca_snapshot_reason"], "TIMEOUT")

    def test_parse_failure_reports_parse_error(self):
        opener = FakeUrlopen(raw_payloads={"account": b"not-json"})

        snapshot = AlpacaPaperSnapshotClient(
            api_key="fixture-key",
            secret_key="fixture-secret",
            base_url="https://paper-api.alpaca.markets",
            urlopen=opener,
        ).read_snapshot()

        self.assertEqual(snapshot.metadata["alpaca_failure_category"], "PARSE_ERROR")
        self.assertEqual(snapshot.metadata["alpaca_failure_detail"], "invalid_json")

    def test_read_failed_is_warning_not_empty_broker_mismatch_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade()], orders=[_order()])
            opener = FakeUrlopen(fail=True)
            snapshot = AlpacaPaperSnapshotClient(
                api_key="fixture-key",
                secret_key="fixture-secret",
                base_url="https://paper-api.alpaca.markets",
                urlopen=opener,
            ).read_snapshot()

            result = build_broker_reconciliation(latest, snapshot)

            self.assertEqual(result["summary"]["reconciliation_status"], "WARNING")
            self.assertEqual(result["summary"]["position_mismatch_count"], 0)
            self.assertEqual(result["summary"]["order_mismatch_count"], 0)
            position_status = result["checks"][result["checks"]["check_name"].eq("position_reconciliation_status")].iloc[0]
            order_status = result["checks"][result["checks"]["check_name"].eq("open_order_reconciliation_status")].iloc[0]
            self.assertEqual(position_status["status"], "WARNING")
            self.assertEqual(order_status["status"], "WARNING")

    def test_live_endpoint_hard_blocks_before_request(self):
        opener = FakeUrlopen()

        snapshot = AlpacaPaperSnapshotClient(
            api_key="fixture-key",
            secret_key="fixture-secret",
            base_url="https://api.alpaca.markets",
            urlopen=opener,
        ).read_snapshot()

        self.assertEqual(snapshot.metadata["alpaca_snapshot_status"], "FAIL")
        self.assertEqual(snapshot.metadata["alpaca_failure_category"], "LIVE_ENDPOINT_BLOCKED")
        self.assertTrue(snapshot.metadata["alpaca_live_endpoint_blocked"])
        self.assertEqual(opener.requests, [])

    def test_broker_submit_enabled_hard_blocks_before_request(self):
        opener = FakeUrlopen()

        snapshot = AlpacaPaperSnapshotClient(
            api_key="fixture-key",
            secret_key="fixture-secret",
            base_url="https://paper-api.alpaca.markets",
            urlopen=opener,
        ).read_snapshot(broker_submit_enabled=True)

        self.assertEqual(snapshot.metadata["alpaca_snapshot_status"], "FAIL")
        self.assertEqual(snapshot.metadata["alpaca_snapshot_reason"], "broker_submit_enabled")
        self.assertEqual(opener.requests, [])

    def test_submit_cancel_replace_and_post_are_absent(self):
        client = AlpacaPaperSnapshotClient(api_key="fixture-key", secret_key="fixture-secret")

        for name in ["submit_order", "submit_paper_order", "cancel_order", "replace_order"]:
            self.assertFalse(hasattr(client, name))
        source = Path("testdata/alpaca_paper_snapshot.py").read_text(encoding="utf-8")
        self.assertNotIn("method=\"POST\"", source)
        self.assertNotIn("method='POST'", source)

    def test_valid_matching_snapshot_can_reconcile_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[], orders=[])

            result = build_broker_reconciliation(latest, _snapshot())

            self.assertEqual(result["summary"]["reconciliation_status"], "PASS")
            self.assertEqual(result["summary"]["broker_snapshot_source"], "ALPACA_PAPER_GET_ONLY")

    def test_valid_snapshot_missing_local_position_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade()], orders=[])

            result = build_broker_reconciliation(latest, _snapshot(positions=[]))

            self.assertEqual(result["summary"]["reconciliation_status"], "FAIL")
            self.assertGreater(result["summary"]["position_mismatch_count"], 0)

    def test_valid_snapshot_extra_broker_open_order_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[], orders=[])

            result = build_broker_reconciliation(latest, _snapshot(orders=[{"symbol": "FIXA", "side": "BUY"}]))

            self.assertEqual(result["summary"]["reconciliation_status"], "FAIL")
            self.assertGreater(result["summary"]["order_mismatch_count"], 0)

    def test_review_required_rows_are_not_local_executable_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, orders=[_order(decision="REVIEW_REQUIRED")])

            result = build_broker_reconciliation(latest, _snapshot())

            self.assertEqual(len(result["local_orders"]), 0)
            self.assertEqual(result["summary"]["order_mismatch_count"], 0)

    def test_blocked_duplicate_buy_rows_are_not_local_executable_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, orders=[_order(status="BLOCKED", decision="REVIEW_REQUIRED")])

            result = build_broker_reconciliation(latest, _snapshot())

            self.assertEqual(len(result["local_orders"]), 0)
            self.assertEqual(result["summary"]["order_mismatch_count"], 0)

    def test_current_accepted_pending_rows_are_only_local_executable_orders(self):
        latest = Path("testdata/reports/latest")
        pending_path = latest / "paper_pending_orders.csv"
        if not pending_path.exists():
            self.skipTest("latest pending orders missing")

        pending = pd.read_csv(pending_path)
        accepted = pending[
            pending.get("status", pd.Series("", index=pending.index)).fillna("").astype(str).str.upper().isin({"PENDING", "PENDING_NEXT_OPEN", "WAIT_FOR_NEXT_OPEN"})
            & pending.get("user_decision", pd.Series("", index=pending.index)).fillna("").astype(str).str.upper().eq("ACCEPT")
        ]
        result = build_broker_reconciliation(latest, _snapshot(orders=[]))

        self.assertEqual(len(result["local_orders"]), len(accepted))
        if len(accepted) == 1:
            self.assertEqual(result["local_orders"].iloc[0]["symbol"], str(accepted.iloc[0]["symbol"]).upper())
            self.assertEqual(result["local_orders"].iloc[0]["side"], str(accepted.iloc[0]["action"]).upper())

    def test_state_files_are_not_mutated(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest, trades=[_trade()], orders=[_order()])
            watched = [latest / "paper_trade_log.csv", latest / "paper_pending_orders.csv", latest / "paper_order_review.csv"]
            before = {path.name: path.read_text(encoding="utf-8") for path in watched}

            write_broker_reconciliation(latest, _snapshot(status="NOT_AVAILABLE", fills=None))

            after = {path.name: path.read_text(encoding="utf-8") for path in watched}
            self.assertEqual(before, after)

    def test_safety_flags_remain_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_state(latest)

            summary = build_broker_reconciliation(latest, _snapshot(status="NOT_AVAILABLE", fills=None))["summary"]

            self.assertFalse(summary["broker_submit_enabled"])
            self.assertFalse(summary["live_trading_enabled"])
            self.assertFalse(summary["short_trading_enabled"])
            self.assertFalse(summary["submit_order_called"])
            self.assertFalse(summary["post_request_used"])

    def test_no_hardcoded_production_dates_or_symbols_in_adapter(self):
        source = Path("testdata/alpaca_paper_snapshot.py").read_text(encoding="utf-8")

        self.assertNotIn("2026-07", source)
        for symbol in ["EQIX", "NUE", "UNH", "CVS"]:
            self.assertNotIn(symbol, source)


if __name__ == "__main__":
    unittest.main()
