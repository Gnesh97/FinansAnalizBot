import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from testdata.alpaca_paper_submit_one import CONFIRM_TOKEN, build_alpaca_paper_submit_one, write_alpaca_paper_submit_one
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


def _write_candidate(latest: Path, *, symbol="FIXA", side="buy", notional=100.0):
    pd.DataFrame(
        [
            {
                "order_intent_id": "fixture-intent",
                "idempotency_key": "fixture-key",
                "execution_idempotency_key": "PREVIEW:fixture-key",
                "strategy_name": "fixture_strategy",
                "symbol": symbol,
                "side": side,
                "notional": notional,
                "estimated_qty": 1.25,
                "order_type": "market",
                "time_in_force": "day",
                "source_signal_date": "fixture-signal-date",
                "status": "PREVIEW_ONLY",
            }
        ]
    ).to_csv(latest / "order_intents.csv", index=False)
    pd.DataFrame([{"execution_idempotency_key": "PREVIEW:fixture-key", "risk_pass": True}]).to_csv(
        latest / "order_risk_check_report.csv",
        index=False,
    )


def _asset(symbol="FIXA", tradable=True):
    return {symbol: {"symbol": symbol, "status": "active", "tradable": tradable, "fractionable": True}}


class FakeSubmitClient:
    def __init__(self, *, timeout=False):
        self.timeout = timeout
        self.post_calls = 0
        self.readback_calls = 0
        self.payloads = []

    def post_order(self, payload):
        self.post_calls += 1
        self.payloads.append(payload)
        if self.timeout:
            raise socket.timeout("timed out")
        return {"id": "post-id", "client_order_id": payload["client_order_id"]}

    def read_order_by_client_order_id(self, client_order_id):
        self.readback_calls += 1
        return {"id": "readback-id", "client_order_id": client_order_id, "status": "accepted"}


class AlpacaPaperSubmitOneTest(unittest.TestCase):
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
            self.assertEqual(client.readback_calls, 1)
            self.assertFalse(client.payloads[0]["extended_hours"])
            self.assertIn("client_order_id", client.payloads[0])
            self.assertIn("notional", client.payloads[0])
            self.assertNotIn("qty", client.payloads[0])

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

            self.assertEqual(result["summary"]["alpaca_paper_submit_one_status"], "UNKNOWN_RESULT_READBACK_OK")
            self.assertEqual(client.post_calls, 1)
            self.assertEqual(client.readback_calls, 1)
            self.assertTrue(str(result["audit"].iloc[0]["reason"]).startswith("submit_unknown_no_retry"))

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
