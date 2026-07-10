import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from testdata.broker_ledger import (
    BrokerLedgerError,
    BrokerLedgerLockError,
    append_order_to_ledger,
    load_broker_ledger,
    sync_broker_ledger_report_copy,
    write_broker_ledger,
    _ledger_paths,
)


def _row(**overrides):
    row = {
        "client_order_id": "test-client-001",
        "broker_order_id": "test-broker-001",
        "symbol": "TEST",
        "side": "buy",
        "notional": 100.0,
        "qty": 1.5,
        "status": "SUBMITTED",
        "readback_status": "PASS",
        "broker_position_match": False,
        "filled_qty": 0.0,
    }
    row.update(overrides)
    return row


class BrokerLedgerStorageTest(unittest.TestCase):
    def test_missing_durable_read_is_empty_and_pure(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            before = sorted(path.relative_to(tmp) for path in Path(tmp).rglob("*"))

            frame = load_broker_ledger(latest)

            after = sorted(path.relative_to(tmp) for path in Path(tmp).rglob("*"))
            self.assertTrue(frame.empty)
            self.assertEqual(before, after)

    def test_report_copy_cannot_seed_durable_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            _, report_copy = _ledger_paths(latest)
            pd.DataFrame([_row()]).to_csv(report_copy, index=False)

            frame = load_broker_ledger(latest)
            durable, _ = _ledger_paths(latest)

            self.assertTrue(frame.empty)
            self.assertFalse(durable.exists())
            self.assertTrue(report_copy.exists())

    def test_corrupt_csv_fails_closed_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            durable, _ = _ledger_paths(latest)
            durable.parent.mkdir(parents=True)
            durable.write_bytes(b"client_order_id,qty\nunterminated,\"bad")
            before = durable.read_bytes()

            with self.assertRaises(BrokerLedgerError):
                load_broker_ledger(latest)

            self.assertEqual(durable.read_bytes(), before)

    def test_missing_required_columns_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            durable, _ = _ledger_paths(latest)
            durable.parent.mkdir(parents=True)
            pd.DataFrame([{"client_order_id": "test-client-001"}]).to_csv(durable, index=False)

            with self.assertRaises(BrokerLedgerError):
                load_broker_ledger(latest)

    def test_duplicate_client_order_id_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            durable, _ = _ledger_paths(latest)
            durable.parent.mkdir(parents=True)
            pd.DataFrame([_row(), _row(broker_order_id="test-broker-002")]).to_csv(durable, index=False)

            with self.assertRaises(BrokerLedgerError):
                load_broker_ledger(latest)

    def test_authorized_atomic_write_produces_valid_durable_then_report_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            frame = pd.DataFrame([_row()])

            written = write_broker_ledger(latest, frame, sync_report_copy=True)
            durable, report_copy = _ledger_paths(latest)

            self.assertEqual(len(written), 1)
            self.assertTrue(durable.exists())
            self.assertTrue(report_copy.exists())
            self.assertEqual(load_broker_ledger(latest).iloc[0]["client_order_id"], "test-client-001")

    def test_failed_replace_preserves_prior_durable_and_does_not_sync_report_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            write_broker_ledger(latest, pd.DataFrame([_row()]), sync_report_copy=True)
            durable, report_copy = _ledger_paths(latest)
            before_durable = durable.read_bytes()
            before_report = report_copy.read_bytes()
            replacement = pd.DataFrame([_row(client_order_id="test-client-002", broker_order_id="test-broker-002")])

            with patch("testdata.broker_ledger.os.replace", side_effect=OSError("replace blocked")):
                with self.assertRaises(BrokerLedgerError):
                    write_broker_ledger(latest, replacement, sync_report_copy=True)

            self.assertEqual(durable.read_bytes(), before_durable)
            self.assertEqual(report_copy.read_bytes(), before_report)

    def test_locked_write_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            durable, _ = _ledger_paths(latest)
            durable.parent.mkdir(parents=True)
            lock_path = durable.with_name(durable.name + ".lock")
            lock_path.write_text("held", encoding="utf-8")

            with self.assertRaises(BrokerLedgerLockError):
                write_broker_ledger(latest, pd.DataFrame([_row()]))

            self.assertFalse(durable.exists())

    def test_stale_lock_is_detected_but_not_deleted_automatically(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)
            durable, _ = _ledger_paths(latest)
            durable.parent.mkdir(parents=True)
            lock_path = durable.with_name(durable.name + ".lock")
            lock_path.write_text("pid=12345\n", encoding="utf-8")

            with patch("testdata.broker_ledger.os.kill", side_effect=ProcessLookupError):
                with self.assertRaisesRegex(BrokerLedgerLockError, "stale lock detected"):
                    write_broker_ledger(latest, pd.DataFrame([_row()]))

            self.assertTrue(lock_path.exists())

    def test_sync_report_copy_requires_existing_valid_durable_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)

            with self.assertRaises(BrokerLedgerError):
                sync_broker_ledger_report_copy(latest)

    def test_repeated_identical_append_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "reports" / "latest"
            latest.mkdir(parents=True)

            first = append_order_to_ledger(latest, **_row())
            second = append_order_to_ledger(latest, **_row())

            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertEqual(len(load_broker_ledger(latest)), 1)


if __name__ == "__main__":
    unittest.main()
