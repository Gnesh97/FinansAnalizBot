import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from testdata.alpaca_paper_submit_one import CONFIRM_TOKEN, build_alpaca_paper_submit_one, write_alpaca_paper_submit_one
from testdata.broker_ledger import append_order_to_ledger, load_broker_ledger
from testdata.broker_submit_recovery import SubmitIntentStore, intent_journal_path
from testdata.broker_reconciliation import ReadOnlyBrokerSnapshot
from testdata.runner import run_alpaca_paper_submit_one


def _snapshot(*, buying_power="1000", orders=None):
    return ReadOnlyBrokerSnapshot(
        positions=[],
        open_orders=orders or [],
        fills=None,
        source="ALPACA_PAPER_GET_ONLY",
        metadata={
            "alpaca_snapshot_status": "PASS",
            "alpaca_snapshot_reason": "ok",
            "alpaca_account_read_success": True,
            "alpaca_positions_read_success": True,
            "alpaca_open_orders_read_success": True,
            "alpaca_endpoint_mode": "paper",
            "request_methods_used": "GET",
            "alpaca_account_summary": {"status": "ACTIVE", "currency": "USD", "buying_power": buying_power},
        },
    )


def _write_baseline(latest: Path):
    (latest / "broker_cutover_baseline.json").write_text(json.dumps({"forward_only": True}), encoding="utf-8")


def _write_candidate(
    latest: Path,
    *,
    symbol="FIXA",
    side="buy",
    notional=100.0,
    order_intent_id="fixture-intent",
    execution_idempotency_key="PREVIEW:fixture-key",
    status="PREVIEW_ONLY",
):
    pd.DataFrame(
        [
            {
                "order_intent_id": order_intent_id,
                "idempotency_key": "fixture-key",
                "execution_idempotency_key": execution_idempotency_key,
                "strategy_name": "fixture_strategy",
                "symbol": symbol,
                "side": side,
                "notional": notional,
                "estimated_qty": 1.25,
                "order_type": "market",
                "time_in_force": "day",
                "source_signal_date": "fixture-signal-date",
                "status": status,
            }
        ]
    ).to_csv(latest / "order_intents.csv", index=False)
    pd.DataFrame([{"execution_idempotency_key": execution_idempotency_key, "risk_pass": True}]).to_csv(
        latest / "order_risk_check_report.csv",
        index=False,
    )


def _asset(symbol="FIXA", tradable=True):
    return {symbol: {"symbol": symbol, "status": "active", "tradable": tradable, "fractionable": True}}


class FakeSubmitClient:
    def __init__(self, *, timeout=False, lookup="auto", post_response=None):
        self.timeout = timeout
        self.lookup = lookup
        self.post_response = post_response
        self.post_calls = 0
        self.readback_calls = 0
        self.payloads = []

    def post_order(self, payload):
        self.post_calls += 1
        self.payloads.append(payload)
        if self.timeout:
            raise socket.timeout("timed out")
        return self.post_response or {"id": "post-id", "client_order_id": payload["client_order_id"]}

    def read_order_by_client_order_id(self, client_order_id):
        self.readback_calls += 1
        lookup = self.lookup.pop(0) if isinstance(self.lookup, list) and self.lookup else self.lookup
        if callable(lookup):
            return lookup(client_order_id)
        if isinstance(lookup, Exception):
            raise lookup
        if lookup == "not_found":
            return None
        if isinstance(lookup, dict):
            return lookup
        if not self.payloads:
            return None
        return {
            "id": "readback-id",
            "client_order_id": client_order_id,
            "status": "accepted",
            "symbol": self.payloads[-1]["symbol"] if self.payloads else "FIXA",
            "side": self.payloads[-1]["side"] if self.payloads else "buy",
            "qty": "1.25",
            "filled_qty": "0",
        }


class AlpacaPaperSubmitOneTest(unittest.TestCase):
    def _authorized(self, latest, client, *, symbol="FIXA"):
        return build_alpaca_paper_submit_one(
            latest, _snapshot(), assets=_asset(symbol), broker_submit_enabled=True,
            confirm_token=CONFIRM_TOKEN, submit_client=client,
        )

    def _candidate_id(self, latest):
        result = build_alpaca_paper_submit_one(latest, _snapshot(), assets=_asset())
        return str(result["client_order_preview"].iloc[0]["client_order_id"])

    def test_existing_durable_client_id_prevents_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)
            client_order_id = self._candidate_id(latest)
            append_order_to_ledger(latest, client_order_id, "broker-1", "FIXA", "buy", 100.0, 1.25, "ACCEPTED/NEW", "PASS", False)
            client = FakeSubmitClient()

            result = self._authorized(latest, client)

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "DUPLICATE_DURABLE_LEDGER")
            self.assertEqual(client.post_calls, 0)
            self.assertEqual(client.readback_calls, 0)

    def test_broker_confirmed_pre_post_repairs_ledger_without_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)
            client = FakeSubmitClient(lookup=lambda cid: {
                "id": "broker-1", "client_order_id": cid, "status": "accepted", "symbol": "FIXA", "side": "buy", "qty": "1.25", "filled_qty": "0",
            })

            result = self._authorized(latest, client)

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "BROKER_CONFIRMED_LEDGER_COMMITTED")
            self.assertEqual(client.post_calls, 0)
            self.assertEqual(len(load_broker_ledger(latest)), 1)

    def test_prepared_intent_not_found_uses_get_before_single_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)
            client_order_id = self._candidate_id(latest)
            store = SubmitIntentStore(latest)
            store.prepare({"client_order_id": client_order_id, "symbol": "FIXA", "side": "buy", "notional": "100", "endpoint": "https://paper-api.alpaca.markets/v2/orders"})
            client = FakeSubmitClient(lookup=["not_found", lambda cid: {"id": "broker-1", "client_order_id": cid, "status": "accepted", "symbol": "FIXA", "side": "buy", "qty": "1.25", "filled_qty": "0"}])

            result = self._authorized(latest, client)

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "SUBMITTED_READBACK_OK")
            self.assertEqual(client.post_calls, 1)
            self.assertGreaterEqual(client.readback_calls, 2)

    def test_timeout_never_causes_automatic_second_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)
            first = FakeSubmitClient(timeout=True, lookup=["not_found", "not_found"])
            first_result = self._authorized(latest, first)
            second = FakeSubmitClient(lookup="not_found")
            second_result = self._authorized(latest, second)

            self.assertEqual(first.post_calls, 1)
            self.assertEqual(first_result["summary"]["alpaca_paper_submit_one_status"], "AMBIGUOUS_RESULT")
            self.assertEqual(second_result["summary"]["alpaca_paper_submit_one_status"], "BLOCKED_AMBIGUOUS_RECOVERY")
            self.assertEqual(second.post_calls, 0)

    def test_post_success_readback_failure_stays_recoverable_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)
            client = FakeSubmitClient(lookup=["not_found", RuntimeError("readback unavailable")])

            result = self._authorized(latest, client)

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "AMBIGUOUS_RESULT")
            self.assertTrue(intent_journal_path(latest).exists())
            self.assertEqual(client.post_calls, 1)

    def test_ledger_write_failure_is_recoverable_without_second_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)
            evidence = lambda cid: {"id": "broker-1", "client_order_id": cid, "status": "accepted", "symbol": "FIXA", "side": "buy", "qty": "1.25", "filled_qty": "0"}
            with patch("testdata.alpaca_paper_submit_one.append_order_to_ledger", side_effect=RuntimeError("disk failure")):
                first = self._authorized(latest, FakeSubmitClient(lookup=["not_found", evidence]))
            second = self._authorized(latest, FakeSubmitClient(lookup=evidence))

            self.assertEqual(first["summary"]["alpaca_paper_submit_one_status"], "AMBIGUOUS_RESULT")
            self.assertEqual(second["summary"]["alpaca_paper_submit_one_status"], "BROKER_CONFIRMED_LEDGER_COMMITTED")
            self.assertEqual(second["summary"]["submit_order_called"], False)

    def test_transient_get_and_corrupt_journal_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)
            transient = self._authorized(latest, FakeSubmitClient(lookup=RuntimeError("network down")))
            self.assertEqual(transient["summary"]["alpaca_paper_submit_one_status"], "BLOCKED_BROKER_LOOKUP")
            self.assertFalse(transient["summary"]["submit_order_called"])
            intent_journal_path(latest).parent.mkdir(parents=True, exist_ok=True)
            intent_journal_path(latest).write_text("not-json", encoding="utf-8")
            corrupt = self._authorized(latest, FakeSubmitClient())
            self.assertEqual(corrupt["summary"]["alpaca_paper_submit_one_status"], "BLOCKED_INTENT_JOURNAL")
            self.assertFalse(corrupt["summary"]["submit_order_called"])

    def test_malformed_broker_evidence_blocks_before_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)
            client = FakeSubmitClient(lookup={"id": "broker-1", "client_order_id": "wrong", "status": "unknown"})

            result = self._authorized(latest, client)

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "BLOCKED_BROKER_LOOKUP")
            self.assertEqual(client.post_calls, 0)

    def test_repeated_broker_recovery_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)
            evidence = lambda cid: {"id": "broker-1", "client_order_id": cid, "status": "accepted", "symbol": "FIXA", "side": "buy", "qty": "1.25", "filled_qty": "0"}
            self._authorized(latest, FakeSubmitClient(lookup=evidence))
            repeated = self._authorized(latest, FakeSubmitClient(lookup=evidence))

            self.assertEqual(len(load_broker_ledger(latest)), 1)
            self.assertEqual(repeated["summary"]["alpaca_paper_submit_one_status"], "DUPLICATE_DURABLE_LEDGER")
    def test_default_blocks_without_confirm_and_submit_enable(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_baseline(latest)
            _write_candidate(latest)
            client = FakeSubmitClient()

            result = build_alpaca_paper_submit_one(latest, _snapshot(), assets=_asset(), submit_client=client)

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "BLOCK")
            self.assertEqual(result["summary"]["submit_gate_status"], "BLOCK")
            self.assertFalse(result["summary"]["broker_submit_enabled"])
            self.assertFalse(result["summary"]["submit_order_called"])
            self.assertFalse(result["summary"]["post_request_used"])
            self.assertEqual(client.post_calls, 0)

    def test_successful_mock_post_then_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_baseline(latest)
            _write_candidate(latest)
            client = FakeSubmitClient()

            result = build_alpaca_paper_submit_one(
                latest,
                _snapshot(),
                assets=_asset(),
                broker_submit_enabled=True,
                confirm_token=CONFIRM_TOKEN,
                submit_client=client,
            )

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "SUBMITTED_READBACK_OK")
            self.assertTrue(result["summary"]["submit_order_called"])
            self.assertTrue(result["summary"]["post_request_used"])
            self.assertTrue(result["summary"]["readback_attempted"])
            self.assertEqual(client.post_calls, 1)
            self.assertEqual(client.readback_calls, 2)
            self.assertFalse(client.payloads[0]["extended_hours"])
            self.assertIn("client_order_id", client.payloads[0])
            self.assertIn("notional", client.payloads[0])
            self.assertNotIn("qty", client.payloads[0])

    def test_authorized_non_cvs_candidate_uses_derived_client_order_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_baseline(latest)
            _write_candidate(
                latest,
                symbol="ACME",
                order_intent_id="synthetic-non-cvs-intent",
                execution_idempotency_key="PREVIEW:synthetic-non-cvs-key",
            )
            client = FakeSubmitClient()

            result = build_alpaca_paper_submit_one(
                latest,
                _snapshot(),
                assets=_asset("ACME"),
                broker_submit_enabled=True,
                confirm_token=CONFIRM_TOKEN,
                submit_client=client,
            )

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "SUBMITTED_READBACK_OK")
            self.assertEqual(client.post_calls, 1)
            self.assertEqual(client.readback_calls, 2)
            self.assertEqual(client.payloads[0]["symbol"], "ACME")
            self.assertTrue(client.payloads[0]["client_order_id"].startswith("fpap-"))

    def test_wrong_authorization_blocks_non_cvs_candidate_without_client_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_baseline(latest)
            _write_candidate(latest, symbol="ACME", order_intent_id="synthetic-auth-intent")
            client = FakeSubmitClient()

            result = build_alpaca_paper_submit_one(
                latest,
                _snapshot(),
                assets=_asset("ACME"),
                broker_submit_enabled=True,
                confirm_token="WRONG_TOKEN",
                submit_client=client,
            )

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "BLOCK")
            self.assertEqual(client.post_calls, 0)
            self.assertEqual(client.readback_calls, 0)

    def test_duplicate_candidate_status_blocks_without_client_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_baseline(latest)
            _write_candidate(latest, symbol="ACME", status="DUPLICATE_BLOCKED")
            client = FakeSubmitClient()

            result = build_alpaca_paper_submit_one(
                latest,
                _snapshot(),
                assets=_asset("ACME"),
                broker_submit_enabled=True,
                confirm_token=CONFIRM_TOKEN,
                submit_client=client,
            )

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "BLOCK")
            self.assertEqual(client.post_calls, 0)
            self.assertEqual(client.readback_calls, 0)

    def test_timeout_does_not_retry_and_reads_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_baseline(latest)
            _write_candidate(latest)
            client = FakeSubmitClient(timeout=True)

            result = build_alpaca_paper_submit_one(
                latest,
                _snapshot(),
                assets=_asset(),
                broker_submit_enabled=True,
                confirm_token=CONFIRM_TOKEN,
                submit_client=client,
            )

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "BROKER_CONFIRMED_LEDGER_COMMITTED")
            self.assertEqual(client.post_calls, 1)
            self.assertEqual(client.readback_calls, 2)

    def test_blocks_live_short_endpoint_and_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_baseline(latest)
            _write_candidate(latest)

            result = build_alpaca_paper_submit_one(
                latest,
                _snapshot(orders=[{"symbol": "FIXA", "side": "sell"}]),
                base_url="https://api.alpaca.markets",
                assets=_asset(),
                broker_submit_enabled=True,
                confirm_token=CONFIRM_TOKEN,
                live_trading_enabled=True,
                short_trading_enabled=True,
            )

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "BLOCK")
            self.assertEqual(result["summary"]["paper_endpoint_mode"], "live")
            self.assertEqual(result["summary"]["open_order_conflict_count"], 1)
            self.assertEqual(result["summary"]["wash_trade_conflict_count"], 1)
            self.assertFalse(result["summary"]["post_request_used"])

    def test_writer_and_runner_default_do_not_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_folder = Path(tmp) / "testdata" / "hisseler"
            latest = Path(tmp) / "testdata" / "reports" / "latest"
            data_folder.mkdir(parents=True)
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)

            with patch("testdata.runner.read_alpaca_paper_snapshot", return_value=_snapshot()):
                with patch("testdata.runner.read_alpaca_paper_assets", return_value={"assets": _asset(), "metadata": {"alpaca_assets_read_success": True}}):
                    result = run_alpaca_paper_submit_one(str(data_folder))

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "BLOCK")
            self.assertFalse(result["summary"]["submit_order_called"])
            self.assertFalse(result["summary"]["post_request_used"])
            self.assertTrue((latest / "alpaca_paper_submit_one_audit.json").exists())

    def test_no_post_literal_in_submit_one_module(self):
        source = Path("testdata/alpaca_paper_submit_one.py").read_text(encoding="utf-8")

        for text in ["method=\"POST\"", "method='POST'", "requests.post("]:
            self.assertNotIn(text, source)


if __name__ == "__main__":
    unittest.main()
