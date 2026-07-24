"""Tests for the nightly DSS chain orchestrator (Phase 6C).

Pins: trading-day gating, idempotent no-double-send, fail-loud notices for
every step, no double-notice when the report step already sent its own, and
the standing guarantee that the scheduler has no order path.
"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

from testdata import dss_scheduler

SATURDAY = datetime(2026, 7, 25, 20, 30, tzinfo=timezone.utc)
FRIDAY = datetime(2026, 7, 24, 20, 30, tzinfo=timezone.utc)


class TestTradingDayGate(unittest.TestCase):
    def test_weekend_is_not_a_trading_day(self):
        self.assertFalse(dss_scheduler.is_trading_day("2026-07-25"))

    def test_weekday_session_is_a_trading_day(self):
        self.assertTrue(dss_scheduler.is_trading_day("2026-07-24"))

    def test_new_year_holiday_is_not_a_trading_day(self):
        self.assertFalse(dss_scheduler.is_trading_day("2026-01-01"))

    def test_chain_skips_on_non_trading_day_without_running_steps(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            with patch.object(dss_scheduler, "_run_step") as run_step:
                result = dss_scheduler.run_daily_chain(as_of=SATURDAY, state_path=state_path)
            self.assertEqual(result["status"], "SKIPPED_NON_TRADING_DAY")
            run_step.assert_not_called()
            self.assertFalse(state_path.exists())


class TestIdempotency(unittest.TestCase):
    def test_already_reported_session_is_not_rerun_or_resent(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            dss_scheduler.save_state({"last_target_session": "2026-07-24", "status": "SENT"}, state_path)
            with patch.object(dss_scheduler, "_run_step") as run_step, \
                 patch.object(dss_scheduler, "send_failure_notice") as notice, \
                 patch.object(dss_scheduler, "target_session", return_value="2026-07-24"):
                result = dss_scheduler.run_daily_chain(as_of=FRIDAY, state_path=state_path)
            self.assertEqual(result["status"], "ALREADY_REPORTED")
            run_step.assert_not_called()
            notice.assert_not_called()

    def test_failed_session_is_retried_on_next_invocation(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            dss_scheduler.save_state({"last_target_session": "2026-07-24", "status": "FAILED"}, state_path)
            with patch.object(dss_scheduler, "_run_step", return_value={"name": "signals", "ok": True}), \
                 patch.object(dss_scheduler, "target_session", return_value="2026-07-24"):
                result = dss_scheduler.run_daily_chain(as_of=FRIDAY, state_path=state_path, skip_refresh=True)
            self.assertEqual(result["status"], "SENT")
            self.assertEqual(dss_scheduler.load_state(state_path)["status"], "SENT")


class TestFailLoud(unittest.TestCase):
    def _fail_on(self, failing_step):
        def _run(step, timeout_seconds):
            if step["name"] == failing_step:
                return {"name": step["name"], "ok": False, "error": f"{step['name']} blew up"}
            return {"name": step["name"], "ok": True}
        return _run

    def test_refresh_failure_sends_notice_and_records_state(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            with patch.object(dss_scheduler, "_run_step", side_effect=self._fail_on("refresh")), \
                 patch.object(dss_scheduler, "send_failure_notice", return_value={"sent": True}) as notice, \
                 patch.object(dss_scheduler, "target_session", return_value="2026-07-24"):
                result = dss_scheduler.run_daily_chain(as_of=FRIDAY, state_path=state_path)
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(result["failed_step"], "refresh")
            notice.assert_called_once()
            self.assertIn("refresh blew up", notice.call_args[0][0])
            state = dss_scheduler.load_state(state_path)
            self.assertEqual(state["status"], "FAILED")
            self.assertEqual(state["failed_step"], "refresh")

    def test_signal_failure_sends_notice(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            with patch.object(dss_scheduler, "_run_step", side_effect=self._fail_on("signals")), \
                 patch.object(dss_scheduler, "send_failure_notice", return_value={"sent": True}) as notice, \
                 patch.object(dss_scheduler, "target_session", return_value="2026-07-24"):
                result = dss_scheduler.run_daily_chain(as_of=FRIDAY, state_path=state_path)
            self.assertEqual(result["failed_step"], "signals")
            notice.assert_called_once()

    def test_report_step_failure_does_not_double_send(self):
        # bot.py already sends its own "DSS did not run" notice and exits 0.
        def _run(step, timeout_seconds):
            if step["name"] == "report":
                return {"name": "report", "ok": False, "error": "DSS did not run today: data is stale",
                        "notice_already_sent": True}
            return {"name": step["name"], "ok": True}

        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            with patch.object(dss_scheduler, "_run_step", side_effect=_run), \
                 patch.object(dss_scheduler, "send_failure_notice") as notice, \
                 patch.object(dss_scheduler, "target_session", return_value="2026-07-24"):
                result = dss_scheduler.run_daily_chain(as_of=FRIDAY, state_path=state_path)
            self.assertEqual(result["status"], "FAILED")
            notice.assert_not_called()
            self.assertEqual(dss_scheduler.load_state(state_path)["status"], "FAILED")

    def test_stale_data_marker_in_report_stdout_is_treated_as_failure(self):
        class _Completed:
            returncode = 0
            stdout = "DSS did not run today: data is stale: manifest target_end_date='2026-07-21'"
            stderr = ""

        with patch.object(dss_scheduler.subprocess, "run", return_value=_Completed()):
            outcome = dss_scheduler._run_step(
                {"name": "report", "description": "d", "command": ["x"]}, 60,
            )
        self.assertFalse(outcome["ok"])
        self.assertTrue(outcome["notice_already_sent"])

    def test_failure_notice_fails_closed_without_telegram_config(self):
        with patch.object(dss_scheduler, "_load_project_env"), \
             patch.dict(dss_scheduler.os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}, clear=False):
            result = dss_scheduler.send_failure_notice("something broke")
        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "telegram_not_configured")
        self.assertIn("DSS did not run", result["text"])

    def test_failure_notice_loads_env_so_cron_can_deliver(self):
        # Task Scheduler and cron start with a bare environment; without the
        # .env load the notice would silently fail closed every night.
        with patch.object(dss_scheduler, "_load_project_env") as load_env, \
             patch.dict(dss_scheduler.os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}, clear=False):
            dss_scheduler.send_failure_notice("boom")
        load_env.assert_called_once()

    def test_delivery_error_text_never_leaks_the_bot_token(self):
        with patch.object(dss_scheduler, "_load_project_env"), \
             patch.dict(dss_scheduler.os.environ, {"TELEGRAM_BOT_TOKEN": "SECRET-TOKEN", "TELEGRAM_CHAT_ID": "42"}, clear=False), \
             patch.object(dss_scheduler.urllib.request, "urlopen", side_effect=RuntimeError("failed calling bot SECRET-TOKEN endpoint")):
            result = dss_scheduler.send_failure_notice("boom")
        self.assertFalse(result["sent"])
        self.assertNotIn("SECRET-TOKEN", result["reason"])
        self.assertIn("<redacted>", result["reason"])


class TestNoOrderPath(unittest.TestCase):
    def test_chain_invokes_only_report_clis(self):
        steps = dss_scheduler._chain_steps("python", skip_refresh=False)
        scripts = {Path(step["command"][1]).name for step in steps}
        self.assertEqual(scripts, {"vericekici_updated.py", "universe_scanner.py", "bot.py"})

    def test_no_broker_or_order_flags_anywhere_in_chain(self):
        forbidden = ("submit", "order", "broker", "live", "execute", "buy", "sell")
        for step in dss_scheduler._chain_steps("python", skip_refresh=False):
            for argument in step["command"]:
                lowered = str(argument).lower()
                for token in forbidden:
                    self.assertNotIn(token, lowered, f"{token!r} appeared in chain command {step['command']}")

    def test_order_placement_is_disabled_constant(self):
        self.assertFalse(dss_scheduler.ORDER_PLACEMENT_ENABLED)
        self.assertFalse(dss_scheduler.BROKER_SUBMIT_ENABLED)


class TestStatePersistence(unittest.TestCase):
    def test_state_round_trips_and_tolerates_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            dss_scheduler.save_state({"last_target_session": "2026-07-24", "status": "SENT"}, state_path)
            self.assertTrue(dss_scheduler.already_reported("2026-07-24", state_path))
            self.assertFalse(dss_scheduler.already_reported("2026-07-23", state_path))
            state_path.write_text("{not json", encoding="utf-8")
            self.assertEqual(dss_scheduler.load_state(state_path), {})
            self.assertFalse(dss_scheduler.already_reported("2026-07-24", state_path))


if __name__ == "__main__":
    unittest.main()
