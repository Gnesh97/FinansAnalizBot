import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from testdata.alpaca_broker_reporting import run_alpaca_paper_check


def _fake_account(status="ACTIVE", buying_power="1000.00", cash="500.00", equity="1500.00", portfolio_value="1500.00", pdt=False):
    account = MagicMock()
    account.status.value = status
    account.buying_power = buying_power
    account.cash = cash
    account.equity = equity
    account.portfolio_value = portfolio_value
    account.pattern_day_trader = pdt
    return account


class RunAlpacaPaperCheckTest(unittest.TestCase):
    def test_refuses_non_paper_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_folder = Path(tmp) / "testdata" / "hisseler"
            data_folder.mkdir(parents=True)
            with patch(
                "testdata.alpaca_broker_reporting._alpaca_config",
                return_value=("key", "secret", "https://api.alpaca.markets"),
            ):
                with patch("alpaca.trading.client.TradingClient") as trading_client_cls:
                    summary, _preview = run_alpaca_paper_check(str(data_folder))

            trading_client_cls.assert_not_called()
            self.assertEqual(summary["connection_status"], "refused")

    def test_missing_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_folder = Path(tmp) / "testdata" / "hisseler"
            data_folder.mkdir(parents=True)
            with patch(
                "testdata.alpaca_broker_reporting._alpaca_config",
                return_value=("", "", "https://paper-api.alpaca.markets"),
            ):
                with patch("alpaca.trading.client.TradingClient") as trading_client_cls:
                    summary, _preview = run_alpaca_paper_check(str(data_folder))

            trading_client_cls.assert_not_called()
            self.assertEqual(summary["connection_status"], "missing_credentials")

    def test_successful_connection_uses_trading_client_and_populates_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_folder = Path(tmp) / "testdata" / "hisseler"
            data_folder.mkdir(parents=True)
            client_instance = MagicMock()
            client_instance.get_account.return_value = _fake_account()
            client_instance.get_all_positions.return_value = [MagicMock(), MagicMock()]
            client_instance.get_orders.return_value = [MagicMock()]

            with patch(
                "testdata.alpaca_broker_reporting._alpaca_config",
                return_value=("key", "secret", "https://paper-api.alpaca.markets"),
            ):
                with patch("alpaca.trading.client.TradingClient", return_value=client_instance) as trading_client_cls:
                    summary, _preview = run_alpaca_paper_check(str(data_folder))

            trading_client_cls.assert_called_once_with("key", "secret", paper=True)
            self.assertEqual(summary["connection_status"], "ok")
            self.assertEqual(summary["account_status"], "ACTIVE")
            self.assertEqual(summary["buying_power"], "1000.00")
            self.assertEqual(summary["cash"], "500.00")
            self.assertEqual(summary["equity"], "1500.00")
            self.assertEqual(summary["portfolio_value"], "1500.00")
            self.assertEqual(summary["pattern_day_trader"], False)
            self.assertEqual(summary["open_positions_count"], 2)
            self.assertEqual(summary["open_orders_count"], 1)
            self.assertEqual(summary["warnings"], [])

    def test_trading_client_error_is_captured_as_failed_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_folder = Path(tmp) / "testdata" / "hisseler"
            data_folder.mkdir(parents=True)
            client_instance = MagicMock()
            client_instance.get_account.side_effect = RuntimeError("network unreachable")

            with patch(
                "testdata.alpaca_broker_reporting._alpaca_config",
                return_value=("key", "secret", "https://paper-api.alpaca.markets"),
            ):
                with patch("alpaca.trading.client.TradingClient", return_value=client_instance):
                    summary, _preview = run_alpaca_paper_check(str(data_folder))

            self.assertEqual(summary["connection_status"], "failed")
            self.assertIn("network unreachable", summary["warnings"][0])

    def test_no_submit_order_or_cancel_order_in_module(self):
        source = Path("testdata/alpaca_broker_reporting.py").read_text(encoding="utf-8")
        for text in ["submit_order(", "cancel_order(", "replace_order("]:
            self.assertNotIn(text, source)


if __name__ == "__main__":
    unittest.main()
