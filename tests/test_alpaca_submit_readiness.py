import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from testdata.alpaca_submit_readiness import build_alpaca_paper_submit_readiness, deterministic_client_order_id, write_alpaca_paper_submit_readiness
from testdata.broker_reconciliation import ReadOnlyBrokerSnapshot
from testdata.runner import run_alpaca_paper_submit_readiness


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
    (latest / "broker_cutover_baseline.json").write_text(
        json.dumps(
            {
                "forward_only": True,
                "broker_cutover_enabled": False,
                "historical_local_positions_policy": "LOCAL_ONLY_DO_NOT_MIRROR_TO_ALPACA",
            }
        ),
        encoding="utf-8",
    )


def _write_candidate(latest: Path, symbol="FIXA", side="buy", notional=100.0):
    pd.DataFrame(
        [
            {
                "order_intent_id": "fixture-intent",
                "idempotency_phase": "PREVIEW",
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
                "reason": "fixture",
            }
        ]
    ).to_csv(latest / "order_intents.csv", index=False)
    pd.DataFrame(
        [
            {
                "execution_idempotency_key": "PREVIEW:fixture-key",
                "risk_pass": True,
                "block_reason": "",
            }
        ]
    ).to_csv(latest / "order_risk_check_report.csv", index=False)


def _asset(symbol="FIXA", tradable=True, fractionable=True):
    return {symbol: {"symbol": symbol, "status": "active", "tradable": tradable, "fractionable": fractionable}}


class AlpacaSubmitReadinessTest(unittest.TestCase):
    def test_blocks_without_baseline_even_with_good_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            latest.mkdir(exist_ok=True)

            result = build_alpaca_paper_submit_readiness(latest, _snapshot(), assets={})

            self.assertEqual(result["summary"]["submit_readiness_status"], "BLOCK")
            self.assertFalse(result["summary"]["broker_cutover_baseline_exists"])
            self.assertFalse(result["summary"]["submit_order_called"])
            self.assertFalse(result["summary"]["post_request_used"])

    def test_one_candidate_gets_deterministic_client_order_id_and_notional_only_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            latest.mkdir(exist_ok=True)
            _write_baseline(latest)
            _write_candidate(latest)

            result = build_alpaca_paper_submit_readiness(latest, _snapshot(), assets=_asset())
            preview = result["client_order_preview"].iloc[0]

            self.assertEqual(result["summary"]["submit_readiness_status"], "BLOCK")
            self.assertEqual(result["summary"]["submit_candidate_count"], 1)
            self.assertEqual(preview["client_order_id"], deterministic_client_order_id({"execution_idempotency_key": "PREVIEW:fixture-key"}))
            self.assertEqual(float(preview["notional"]), 100.0)
            self.assertEqual(preview["qty"], "")
            self.assertFalse(bool(preview["extended_hours"]))
            self.assertFalse(bool(preview["would_submit"]))

    def test_more_than_one_candidate_blocks_one_order_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            latest.mkdir(exist_ok=True)
            _write_baseline(latest)
            _write_candidate(latest)
            intents = pd.read_csv(latest / "order_intents.csv")
            second = intents.iloc[0].copy()
            second["order_intent_id"] = "fixture-intent-2"
            second["idempotency_key"] = "fixture-key-2"
            second["execution_idempotency_key"] = "PREVIEW:fixture-key-2"
            second["symbol"] = "FIXB"
            pd.concat([intents, pd.DataFrame([second])]).to_csv(latest / "order_intents.csv", index=False)
            pd.DataFrame(
                [
                    {"execution_idempotency_key": "PREVIEW:fixture-key", "risk_pass": True},
                    {"execution_idempotency_key": "PREVIEW:fixture-key-2", "risk_pass": True},
                ]
            ).to_csv(latest / "order_risk_check_report.csv", index=False)

            result = build_alpaca_paper_submit_readiness(latest, _snapshot(), assets={**_asset("FIXA"), **_asset("FIXB")})

            self.assertEqual(result["summary"]["submit_candidate_count"], 2)
            cap = result["checks"][result["checks"]["check_name"].eq("max_submit_candidates")].iloc[0]
            self.assertEqual(cap["status"], "BLOCK")

    def test_buying_power_and_wash_conflicts_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            latest.mkdir(exist_ok=True)
            _write_baseline(latest)
            _write_candidate(latest, symbol="FIXA", side="buy", notional=100.0)

            result = build_alpaca_paper_submit_readiness(
                latest,
                _snapshot(buying_power="10", orders=[{"symbol": "FIXA", "side": "sell", "qty": "1"}]),
                assets=_asset("FIXA"),
            )

            self.assertEqual(result["summary"]["open_order_conflict_count"], 1)
            self.assertEqual(result["summary"]["wash_trade_conflict_count"], 1)
            self.assertEqual(result["summary"]["required_notional"], 100.0)
            self.assertEqual(result["summary"]["buying_power"], 10.0)

    def test_writer_and_runner_do_not_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_folder = Path(tmp) / "testdata" / "hisseler"
            latest = Path(tmp) / "testdata" / "reports" / "latest"
            data_folder.mkdir(parents=True)
            latest.mkdir(parents=True)
            _write_baseline(latest)
            _write_candidate(latest)

            with patch("testdata.runner.read_alpaca_paper_snapshot", return_value=_snapshot()) as snapshot_reader:
                with patch("testdata.runner.read_alpaca_paper_assets", return_value={"assets": _asset(), "metadata": {"alpaca_assets_read_success": True}}) as asset_reader:
                    result = run_alpaca_paper_submit_readiness(str(data_folder))

            self.assertTrue(snapshot_reader.called)
            self.assertTrue(asset_reader.called)
            self.assertEqual(result["summary"]["submit_readiness_status"], "BLOCK")
            self.assertTrue((latest / "alpaca_paper_submit_readiness.json").exists())
            self.assertFalse(result["summary"]["submit_order_called"])
            self.assertFalse(result["summary"]["post_request_used"])

    def test_no_submit_cancel_replace_or_post_in_module(self):
        source = Path("testdata/alpaca_submit_readiness.py").read_text(encoding="utf-8")

        for text in ["submit_order(", "cancel_order(", "replace_order(", "method=\"POST\"", "method='POST'"]:
            self.assertNotIn(text, source)


if __name__ == "__main__":
    unittest.main()
