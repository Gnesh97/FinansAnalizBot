import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

import bot  # noqa: E402
from testdata.dss_report import DSSReport, DSSPosition, DSSDataNotReadyError  # noqa: E402


def _args(print_only):
    return MagicMock(print_only=print_only, verbose=False)


def _sample_report():
    return DSSReport(
        as_of_data_date="2024-01-05",
        generated_at="2024-01-05T00:00:00Z",
        universe_count=177,
        universe_hash="026f66d1" + "0" * 56,
        strategy_id="rs_m126_top30_sma100_no_regime_v2_clean_stock",
        buy_list=[DSSPosition(symbol="AAA", action="BUY", price=100.0, target_weight_pct=3.33, rank=1, score=1.5, reason="top_rank")],
        sell_list=[],
    )


class TestBotDssReportSendPath(unittest.TestCase):
    def test_send_function_called_with_only_text_and_chat_id(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(bot, "BASE_DIR", Path(temp)), \
             patch.object(bot, "TELEGRAM_BOT_TOKEN", "dummy-token"), \
             patch.object(bot, "TELEGRAM_CHAT_ID", 999888), \
             patch("testdata.dss_report.generate_dss_report", return_value=_sample_report()), \
             patch.object(bot, "send_telegram_message", return_value=True) as mock_send:
            bot.run_dss_report(_args(print_only=False))

        mock_send.assert_called_once()
        call_args, call_kwargs = mock_send.call_args
        self.assertEqual(len(call_args), 2)
        text, chat_id = call_args
        self.assertEqual(chat_id, 999888)
        self.assertIn("AAA", text)
        self.assertEqual(call_kwargs, {})
        # No broker/order/live-trading parameter appears anywhere in the call.
        forbidden = {"broker", "order", "submit", "live_trading"}
        self.assertFalse(forbidden & set(call_kwargs.keys()))

    def test_print_only_never_sends(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(bot, "BASE_DIR", Path(temp)), \
             patch.object(bot, "TELEGRAM_BOT_TOKEN", "dummy-token"), \
             patch.object(bot, "TELEGRAM_CHAT_ID", 999888), \
             patch("testdata.dss_report.generate_dss_report", return_value=_sample_report()), \
             patch.object(bot, "send_telegram_message") as mock_send:
            bot.run_dss_report(_args(print_only=True))

            mock_send.assert_not_called()
            artifact_dir = Path(temp) / "reports" / "dss"
            artifacts = list(artifact_dir.glob("dss_report_*.md"))
            self.assertEqual(len(artifacts), 1)
            self.assertIn("AAA", artifacts[0].read_text(encoding="utf-8"))


class TestBotDssReportFailLoud(unittest.TestCase):
    def test_stale_data_sends_failure_notice_not_normal_report(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(bot, "BASE_DIR", Path(temp)), \
             patch.object(bot, "TELEGRAM_BOT_TOKEN", "dummy-token"), \
             patch.object(bot, "TELEGRAM_CHAT_ID", 999888), \
             patch("testdata.dss_report.generate_dss_report", side_effect=DSSDataNotReadyError("data is stale: as of 2024-01-02")), \
             patch.object(bot, "send_telegram_message", return_value=True) as mock_send:
            bot.run_dss_report(_args(print_only=False))

        mock_send.assert_called_once()
        text, chat_id = mock_send.call_args[0]
        self.assertEqual(chat_id, 999888)
        self.assertIn("DSS did not run today", text)
        self.assertIn("stale", text)
        self.assertNotIn("BUY (", text)

    def test_missing_credentials_fail_closed_no_send(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(bot, "BASE_DIR", Path(temp)), \
             patch.object(bot, "TELEGRAM_BOT_TOKEN", ""), \
             patch.object(bot, "TELEGRAM_CHAT_ID", ""), \
             patch("testdata.dss_report.generate_dss_report", return_value=_sample_report()), \
             patch.object(bot, "send_telegram_message") as mock_send:
            bot.run_dss_report(_args(print_only=False))

        mock_send.assert_not_called()

    def test_missing_credentials_fail_closed_on_failure_path_too(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(bot, "BASE_DIR", Path(temp)), \
             patch.object(bot, "TELEGRAM_BOT_TOKEN", ""), \
             patch.object(bot, "TELEGRAM_CHAT_ID", ""), \
             patch("testdata.dss_report.generate_dss_report", side_effect=DSSDataNotReadyError("boom")), \
             patch.object(bot, "send_telegram_message") as mock_send:
            bot.run_dss_report(_args(print_only=False))

        mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
