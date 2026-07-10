import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from testdata.broker_ledger import BrokerLedgerLockError, _ledger_paths, append_order_to_ledger, load_broker_ledger
from testdata.broker_lifecycle import ALPACA_LIFECYCLE_MAP, _parse_rfc3339, _validate_readback, check_broker_order_lifecycle
from testdata.broker_reconciliation import ReadOnlyBrokerSnapshot


class FakeReadbackClient:
    def __init__(self, payloads=None, error=None):
        self.payloads = payloads or {}
        self.error = error
        self.calls = 0

    def read_order_by_client_order_id(self, client_order_id):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payloads[client_order_id]

    def read_order_by_id(self, order_id):
        self.calls += 1
        if self.error:
            raise self.error
        for payload in self.payloads.values():
            if payload.get("id") == order_id:
                return payload
        raise LookupError("missing order")


def _append(latest, *, client_order_id="test-client-001", broker_order_id="test-broker-001", symbol="TEST", side="buy", status="SUBMITTED", qty=2.0, filled_qty=0.0):
    return append_order_to_ledger(
        latest,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        symbol=symbol,
        side=side,
        notional=100.0,
        qty=qty,
        status=status,
        readback_status="PASS",
        broker_position_match=False,
        filled_qty=filled_qty,
    )


def _payload(*, client_order_id="test-client-001", broker_order_id="test-broker-001", status="new", filled_qty=0.0, replacement_order_id=None):
    payload = {
        "id": broker_order_id,
        "client_order_id": client_order_id,
        "status": status,
        "filled_qty": filled_qty,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    if replacement_order_id is not None:
        payload["replaced_by_order_id"] = replacement_order_id
    return payload


class BrokerLifecycleTest(unittest.TestCase):
    def test_parse_rfc3339(self):
        self.assertEqual(_parse_rfc3339("2026-07-08T15:20:10Z").year, 2026)
        self.assertIsNone(_parse_rfc3339("not-a-date"))

    def test_supported_lifecycle_statuses_validate_explicitly(self):
        expected = {
            "new": "ACCEPTED/NEW",
            "accepted": "ACCEPTED/NEW",
            "pending_new": "ACCEPTED/NEW",
            "partially_filled": "PARTIALLY_FILLED",
            "filled": "FILLED",
            "canceled": "CANCELED",
            "expired": "EXPIRED",
            "rejected": "REJECTED",
            "pending_cancel": "PENDING_CANCEL",
            "pending_replace": "PENDING_REPLACE",
            "replaced": "REPLACED",
            "suspended": "SUSPENDED",
            "stopped": "STOPPED",
            "calculated": "CALCULATED",
        }
        self.assertTrue(set(expected).issubset(ALPACA_LIFECYCLE_MAP))
        for raw_status, mapped_status in expected.items():
            self.assertEqual(_validate_readback(_payload(status=raw_status), "test-client-001", "test-broker-001")["status"], mapped_status)

    def test_no_credentials_preserves_durable_row_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _append(latest)
            durable, _ = _ledger_paths(latest)
            before = durable.read_bytes()

            result = check_broker_order_lifecycle(latest, allow_state_write=True)

            self.assertFalse(result["summary"]["state_mutated"])
            self.assertEqual(durable.read_bytes(), before)

    def test_timeout_error_and_malformed_response_preserve_durable_row(self):
        cases = [socket.timeout("timeout"), RuntimeError("http error"), ["not-a-dict"]]
        for case in cases:
            with self.subTest(case=type(case).__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    latest = Path(tmp) / "reports" / "latest"
                    latest.mkdir(parents=True)
                    _append(latest)
                    durable, _ = _ledger_paths(latest)
                    before = durable.read_bytes()
                    client = FakeReadbackClient(error=case) if isinstance(case, Exception) else FakeReadbackClient({"test-client-001": case})

                    result = check_broker_order_lifecycle(latest, api_key="test", secret_key="test", readback_client=client, allow_state_write=True)

                    self.assertFalse(result["summary"]["state_mutated"])
                    self.assertEqual(durable.read_bytes(), before)
                    self.assertGreater(client.calls, 0)

    def test_missing_order_preserves_durable_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _append(latest)
            durable, _ = _ledger_paths(latest)
            before = durable.read_bytes()

            result = check_broker_order_lifecycle(latest, api_key="test", secret_key="test", readback_client=FakeReadbackClient(), allow_state_write=True)

            self.assertFalse(result["summary"]["state_mutated"])
            self.assertEqual(durable.read_bytes(), before)

    def test_default_report_only_mode_does_not_mutate_valid_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _append(latest)
            durable, _ = _ledger_paths(latest)
            before = durable.read_bytes()

            result = check_broker_order_lifecycle(
                latest,
                api_key="test",
                secret_key="test",
                readback_client=FakeReadbackClient({"test-client-001": _payload()}),
            )

            self.assertFalse(result["summary"]["state_mutated"])
            self.assertEqual(durable.read_bytes(), before)
            self.assertEqual(result["orders"][0]["status"], "ACCEPTED/NEW")

    def test_explicit_write_uses_authorized_storage_api_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _append(latest)
            client = FakeReadbackClient({"test-client-001": _payload()})

            first = check_broker_order_lifecycle(latest, api_key="test", secret_key="test", readback_client=client, allow_state_write=True)
            durable, report_copy = _ledger_paths(latest)
            first_bytes = durable.read_bytes()
            second = check_broker_order_lifecycle(latest, api_key="test", secret_key="test", readback_client=client, allow_state_write=True)

            self.assertTrue(first["summary"]["state_mutated"])
            self.assertFalse(second["summary"]["state_mutated"])
            self.assertEqual(durable.read_bytes(), first_bytes)
            self.assertTrue(report_copy.exists())

    def test_terminal_status_cannot_regress_to_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _append(latest, status="FILLED", filled_qty=2.0)

            result = check_broker_order_lifecycle(
                latest,
                api_key="test",
                secret_key="test",
                readback_client=FakeReadbackClient({"test-client-001": _payload(status="new")}),
                allow_state_write=True,
            )

            self.assertFalse(result["summary"]["state_mutated"])
            self.assertEqual(load_broker_ledger(latest).iloc[0]["status"], "FILLED")

    def test_partial_to_filled_transition_and_side_aware_position_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _append(latest, client_order_id="test-client-buy", broker_order_id="test-broker-buy", side="buy", status="PARTIALLY_FILLED", qty=5.0, filled_qty=2.0)
            _append(latest, client_order_id="test-client-sell", broker_order_id="test-broker-sell", side="sell", status="PARTIALLY_FILLED", qty=5.0, filled_qty=2.0)
            snapshot = ReadOnlyBrokerSnapshot(positions=[], open_orders=[], source="TEST")
            client = FakeReadbackClient(
                {
                    "test-client-buy": _payload(client_order_id="test-client-buy", broker_order_id="test-broker-buy", status="filled", filled_qty=5.0),
                    "test-client-sell": _payload(client_order_id="test-client-sell", broker_order_id="test-broker-sell", status="filled", filled_qty=5.0),
                }
            )

            result = check_broker_order_lifecycle(latest, broker_snapshot=snapshot, api_key="test", secret_key="test", readback_client=client, allow_state_write=True)

            self.assertTrue(result["summary"]["state_mutated"])
            self.assertTrue(all(order["position_match"] is True for order in result["orders"]))
            self.assertEqual(set(load_broker_ledger(latest)["status"]), {"FILLED"})

    def test_pending_replace_and_replaced_preserve_ids_and_record_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _append(latest)
            client = FakeReadbackClient({"test-client-001": _payload(status="pending_replace", replacement_order_id="replacement-broker-002")})

            pending = check_broker_order_lifecycle(latest, api_key="test", secret_key="test", readback_client=client, allow_state_write=True)
            client.payloads["test-client-001"] = _payload(status="replaced", replacement_order_id="replacement-broker-002")
            replaced = check_broker_order_lifecycle(latest, api_key="test", secret_key="test", readback_client=client, allow_state_write=True)
            row = load_broker_ledger(latest).iloc[0]

            self.assertEqual(pending["orders"][0]["status"], "PENDING_REPLACE")
            self.assertEqual(replaced["orders"][0]["status"], "REPLACED")
            self.assertEqual(row["client_order_id"], "test-client-001")
            self.assertEqual(row["broker_order_id"], "test-broker-001")
            self.assertEqual(replaced["orders"][0]["replacement_order_id"], "replacement-broker-002")

    def test_lock_failure_leaves_prior_ledger_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _append(latest)
            durable, _ = _ledger_paths(latest)
            before = durable.read_bytes()
            durable.with_name(durable.name + ".lock").write_text("pid=held", encoding="utf-8")

            result = check_broker_order_lifecycle(
                latest,
                api_key="test",
                secret_key="test",
                readback_client=FakeReadbackClient({"test-client-001": _payload()}),
                allow_state_write=True,
            )

            self.assertFalse(result["summary"]["state_mutated"])
            self.assertIn("lock", " ".join(result["issues"]).lower())
            self.assertEqual(durable.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
