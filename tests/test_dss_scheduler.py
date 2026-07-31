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

# Phase 8E: snapshot the PRODUCTION notice queue before any test runs, so a test
# that writes to it is caught rather than discovered via the operator's phone.
_PRODUCTION_QUEUE = dss_scheduler.PENDING_NOTICE_PATH
_PRODUCTION_QUEUE_AT_IMPORT = (
    _PRODUCTION_QUEUE.read_text(encoding="utf-8") if _PRODUCTION_QUEUE.exists() else None
)

SATURDAY = datetime(2026, 7, 25, 20, 30, tzinfo=timezone.utc)
# 22:00 UTC == 01:00 Europe/Istanbul, the installed timer's actual fire time and
# two hours after the 20:00 UTC close. Phase 8B moved this off 20:30 UTC: with
# the publication-lag gate a 20:30 UTC run is 30 minutes BEFORE that session is
# publishable, so it now defers rather than running.
FRIDAY = datetime(2026, 7, 24, 22, 0, tzinfo=timezone.utc)


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
            dss_scheduler.send_failure_notice("synthetic self-test payload",
                                              queue_path=Path("/nonexistent/never-written.json"))
        load_env.assert_called_once()

    def test_delivery_error_text_never_leaks_the_bot_token(self):
        # Phase 8E: an explicit temporary queue_path is mandatory. Without it
        # this test appended a real "boom" entry to the production queue on
        # every suite run, which the next connected run then delivered.
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(dss_scheduler, "_load_project_env"), \
             patch.dict(dss_scheduler.os.environ, {"TELEGRAM_BOT_TOKEN": "SECRET-TOKEN-THAT-IS-LONG", "TELEGRAM_CHAT_ID": "42"}, clear=False), \
             patch.object(dss_scheduler.urllib.request, "urlopen", side_effect=RuntimeError("failed calling bot SECRET-TOKEN-THAT-IS-LONG endpoint")):
            result = dss_scheduler.send_failure_notice(
                "synthetic self-test payload", attempts=1, retry_sleep_seconds=0,
                queue_path=Path(temp) / "pending.json", sleep=lambda _: None,
            )
        self.assertFalse(result["sent"])
        self.assertNotIn("SECRET-TOKEN-THAT-IS-LONG", result["reason"])
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


class TestSessionCompletionGate(unittest.TestCase):
    """Phase 8B: never target a session that has not closed and published.

    Monday 2026-07-27 closes 16:00 ET = 20:00 UTC; with the 90-minute default
    lag it becomes publishable at 21:30 UTC.
    """

    def test_normal_0100_istanbul_run_targets_the_session_that_just_closed(self):
        # 01:00 Tue Europe/Istanbul == 22:00 UTC Mon, after close + lag.
        as_of = datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc)
        gate = dss_scheduler.session_completion_gate(as_of)
        self.assertEqual(gate["expected_session"], "2026-07-27")
        self.assertEqual(gate["published_session"], "2026-07-27")
        self.assertTrue(gate["ready"])

    def test_midday_catchup_run_is_not_ready(self):
        # The actual 2026-07-27 14:24 Europe/Istanbul catch-up == 11:24 UTC.
        as_of = datetime(2026, 7, 27, 11, 24, tzinfo=timezone.utc)
        gate = dss_scheduler.session_completion_gate(as_of)
        self.assertEqual(gate["expected_session"], "2026-07-27")
        self.assertEqual(gate["published_session"], "2026-07-24")
        self.assertFalse(gate["ready"])

    def test_publication_lag_boundary_flips_exactly_at_close_plus_lag(self):
        just_before = datetime(2026, 7, 27, 21, 29, tzinfo=timezone.utc)
        just_after = datetime(2026, 7, 27, 21, 30, tzinfo=timezone.utc)
        self.assertEqual(dss_scheduler.latest_published_session(just_before), "2026-07-24")
        self.assertEqual(dss_scheduler.latest_published_session(just_after), "2026-07-27")

    def test_weekend_resolves_to_the_last_weekday_session(self):
        as_of = datetime(2026, 7, 25, 20, 30, tzinfo=timezone.utc)  # Saturday
        self.assertEqual(dss_scheduler.latest_published_session(as_of), "2026-07-24")

    def test_holiday_resolves_to_the_session_before_it(self):
        # 2026-01-01 is a market holiday; the prior session is 2025-12-31.
        as_of = datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc)
        self.assertEqual(dss_scheduler.latest_published_session(as_of), "2025-12-31")

    def test_chain_defers_on_uncompleted_session_without_running_steps(self):
        as_of = datetime(2026, 7, 27, 11, 24, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            with patch.object(dss_scheduler, "_run_step") as run_step:
                result = dss_scheduler.run_daily_chain(as_of=as_of, state_path=state_path, send=False)
            self.assertEqual(result["status"], "DEFERRED_SESSION_NOT_COMPLETE")
            self.assertEqual(result["published_session"], "2026-07-24")
            run_step.assert_not_called()
            # Deferral must not clobber the record of the last real outcome.
            self.assertFalse(state_path.exists())

    def test_force_bypasses_the_gate_without_consulting_it(self):
        # latest_published_session fails closed on an empty window; --force must
        # not inherit that failure mode.
        as_of = datetime(2026, 7, 27, 11, 24, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(dss_scheduler, "latest_published_session",
                              side_effect=AssertionError("must not be consulted under --force")) as published:
                with patch.object(dss_scheduler, "_run_step",
                                  return_value={"name": "refresh", "ok": True, "elapsed_sec": 0.1}):
                    result = dss_scheduler.run_daily_chain(
                        as_of=as_of, state_path=Path(temp) / "s.json",
                        pending_notice_path=Path(temp) / "p.json", force=True, send=False,
                    )
            published.assert_not_called()
        self.assertEqual(result["status"], "SENT")

    def test_deferral_is_a_success_exit_code(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(dss_scheduler, "run_daily_chain") as chain:
                chain.return_value = {"status": "DEFERRED_SESSION_NOT_COMPLETE",
                                      "target_session": "2026-07-27"}
                code = dss_scheduler.main(["--state-path", str(Path(temp) / "s.json")])
        self.assertEqual(code, 0)


class TestNetworkUnavailablePath(unittest.TestCase):
    """Phase 8B: the launcher aborts before the chain when there is no network."""

    def test_queue_network_abort_persists_a_notice_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending_notices.json"
            code = dss_scheduler.main([
                "--queue-network-abort", "cannot resolve api.tiingo.com after 900s",
                "--pending-notice-path", str(pending),
            ])
            self.assertEqual(code, 0)
            queued = dss_scheduler.load_pending_notices(pending)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["reason"], "network_unavailable_at_launch")
        self.assertIn(dss_scheduler.DSS_FAILURE_MARKER, queued[0]["text"])
        self.assertIn("no network connectivity", queued[0]["text"])

    def test_queue_network_abort_does_not_run_the_chain(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(dss_scheduler, "run_daily_chain") as chain:
                dss_scheduler.main([
                    "--queue-network-abort", "no dns",
                    "--pending-notice-path", str(Path(temp) / "pending.json"),
                ])
            chain.assert_not_called()


class TestUndeliveredNoticeQueueing(unittest.TestCase):
    """Phase 8B: a notice that cannot be sent is retried, then queued, never lost."""

    def setUp(self):
        patcher = patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"})
        patcher.start()
        self.addCleanup(patcher.stop)
        env_patcher = patch.object(dss_scheduler, "_load_project_env", lambda: None)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def test_failed_send_is_retried_then_queued(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending.json"
            with patch.object(dss_scheduler, "_post_telegram",
                              side_effect=OSError("Name or service not known")) as post:
                result = dss_scheduler.send_failure_notice(
                    "refresh step exited 1", session="2026-07-24",
                    attempts=3, retry_sleep_seconds=0, queue_path=pending, sleep=lambda _: None,
                )
            self.assertEqual(post.call_count, 3)
            self.assertFalse(result["sent"])
            self.assertTrue(result["queued"])
            queued = dss_scheduler.load_pending_notices(pending)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["session"], "2026-07-24")

    def test_send_that_succeeds_on_retry_is_not_queued(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending.json"
            with patch.object(dss_scheduler, "_post_telegram",
                              side_effect=[OSError("transient"), None]) as post:
                result = dss_scheduler.send_failure_notice(
                    "refresh step exited 1", attempts=3, retry_sleep_seconds=0,
                    queue_path=pending, sleep=lambda _: None,
                )
            self.assertEqual(post.call_count, 2)
            self.assertTrue(result["sent"])
            self.assertFalse(result["queued"])
            self.assertEqual(dss_scheduler.load_pending_notices(pending), [])

    def test_queued_notice_is_delivered_late_and_marked_as_such(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending.json"
            dss_scheduler.queue_notice("DSS did not run: refresh step exited 1",
                                       reason="dns", session="2026-07-24", path=pending)
            with patch.object(dss_scheduler, "_post_telegram") as post:
                drained = dss_scheduler.drain_pending_notices(pending)
            self.assertEqual(drained["delivered"], 1)
            self.assertEqual(drained["remaining"], 0)
            sent_text = post.call_args[0][0]
            self.assertIn(dss_scheduler.LATE_NOTICE_MARKER, sent_text)
            self.assertIn("2026-07-24", sent_text)
            self.assertEqual(dss_scheduler.load_pending_notices(pending), [])

    def test_queued_notice_survives_a_failed_drain_and_counts_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending.json"
            dss_scheduler.queue_notice("DSS did not run: x", reason="dns",
                                       session="2026-07-24", path=pending)
            with patch.object(dss_scheduler, "_post_telegram", side_effect=OSError("still down")):
                drained = dss_scheduler.drain_pending_notices(pending)
            self.assertEqual(drained["delivered"], 0)
            self.assertEqual(drained["remaining"], 1)
            self.assertEqual(dss_scheduler.load_pending_notices(pending)[0]["attempts"], 1)

    def test_queued_notice_is_dropped_after_the_attempt_ceiling(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending.json"
            dss_scheduler.queue_notice("DSS did not run: x", reason="dns",
                                       session="2026-07-24", path=pending)
            notices = dss_scheduler.load_pending_notices(pending)
            notices[0]["attempts"] = dss_scheduler.MAX_QUEUED_NOTICE_ATTEMPTS - 1
            dss_scheduler.save_pending_notices(notices, pending)
            with patch.object(dss_scheduler, "_post_telegram", side_effect=OSError("still down")):
                drained = dss_scheduler.drain_pending_notices(pending)
            self.assertEqual(drained["dropped"], 1)
            self.assertEqual(dss_scheduler.load_pending_notices(pending), [])

    def test_realistic_token_is_redacted_from_log_text(self):
        token = "123456789:AAEyBcDeFgHiJkLmNoPqRsTuVwXyZ0123456"
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": token}):
            redacted = dss_scheduler._redact(f"failed calling https://api.telegram.org/bot{token}/sendMessage")
        self.assertNotIn(token, redacted)
        self.assertIn("<redacted>", redacted)

    def test_short_token_does_not_shred_the_error_text(self):
        # Guards the diagnostic: a 1-char token must not turn
        # "Name or service not known" into "Name or service no<redacted> known".
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "t"}):
            self.assertEqual(dss_scheduler._redact("Name or service not known"),
                             "Name or service not known")

    def test_deferred_run_still_delivers_a_queued_notice(self):
        # The 2026-07-27 case: machine boots mid-day, network returns, the run
        # defers -- but the operator must still hear about the earlier failure
        # rather than waiting another day.
        as_of = datetime(2026, 7, 27, 11, 24, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending.json"
            dss_scheduler.queue_notice("DSS did not run: refresh step exited 1",
                                       reason="dns", session="2026-07-24", path=pending)
            with patch.object(dss_scheduler, "_post_telegram") as post:
                with patch.object(dss_scheduler, "_run_step") as run_step:
                    result = dss_scheduler.run_daily_chain(
                        as_of=as_of, state_path=Path(temp) / "s.json",
                        pending_notice_path=pending, send=True,
                    )
            self.assertEqual(result["status"], "DEFERRED_SESSION_NOT_COMPLETE")
            run_step.assert_not_called()
            post.assert_called_once()
            self.assertIn(dss_scheduler.LATE_NOTICE_MARKER, post.call_args[0][0])
            self.assertEqual(dss_scheduler.load_pending_notices(pending), [])

    def test_drain_is_a_noop_when_nothing_is_queued(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending.json"
            with patch.object(dss_scheduler, "_post_telegram") as post:
                drained = dss_scheduler.drain_pending_notices(pending)
            post.assert_not_called()
            self.assertEqual(drained, {"delivered": 0, "remaining": 0, "dropped": 0})


class TestProductionQueueIsNeverTouchedByTests(unittest.TestCase):
    """Phase 8E: the operator received 10 real Telegram messages saying
    "DSS did not run: boom" because send_failure_notice defaulted queue_path to
    the production queue, so every suite run appended a live entry that the next
    connected run dutifully delivered."""

    def test_production_queue_file_is_unchanged_by_this_suite(self):
        current = (
            _PRODUCTION_QUEUE.read_text(encoding="utf-8")
            if _PRODUCTION_QUEUE.exists() else None
        )
        self.assertEqual(
            current, _PRODUCTION_QUEUE_AT_IMPORT,
            f"a test wrote to the PRODUCTION notice queue {_PRODUCTION_QUEUE}; "
            f"those entries get delivered to the operator's real chat",
        )

    def test_queueing_without_an_explicit_path_is_a_hard_error(self):
        with patch.object(dss_scheduler, "_load_project_env"), \
             patch.dict(dss_scheduler.os.environ,
                        {"TELEGRAM_BOT_TOKEN": "token-long-enough", "TELEGRAM_CHAT_ID": "42"}, clear=False), \
             patch.object(dss_scheduler, "_post_telegram", side_effect=OSError("down")):
            with self.assertRaises(ValueError) as caught:
                dss_scheduler.send_failure_notice(
                    "synthetic self-test payload", attempts=1,
                    retry_sleep_seconds=0, sleep=lambda _: None,
                )
        self.assertIn("explicit queue_path", str(caught.exception))


class TestSyntheticPayloadsAreNeverDelivered(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict("os.environ",
                             {"TELEGRAM_BOT_TOKEN": "token-long-enough", "TELEGRAM_CHAT_ID": "c"})
        patcher.start()
        self.addCleanup(patcher.stop)
        env_patcher = patch.object(dss_scheduler, "_load_project_env", lambda: None)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def test_synthetic_entry_is_discarded_and_never_sent(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending.json"
            dss_scheduler.queue_notice("DSS did not run: boom", reason="self-test",
                                       session=None, path=pending, synthetic=True)
            with patch.object(dss_scheduler, "_post_telegram") as post:
                drained = dss_scheduler.drain_pending_notices(pending)
            post.assert_not_called()
            self.assertEqual(drained["delivered"], 0)
            self.assertEqual(drained["dropped"], 1)
            self.assertEqual(dss_scheduler.load_pending_notices(pending), [])

    def test_genuine_entry_alongside_a_synthetic_one_still_gets_through(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending.json"
            dss_scheduler.queue_notice("DSS did not run: boom", reason="self-test",
                                       path=pending, synthetic=True)
            dss_scheduler.queue_notice("DSS did not run: refresh step exited 1",
                                       reason="dns", session="2026-07-27", path=pending)
            with patch.object(dss_scheduler, "_post_telegram") as post:
                drained = dss_scheduler.drain_pending_notices(pending)
            self.assertEqual(drained["delivered"], 1)
            self.assertEqual(drained["dropped"], 1)
            self.assertIn("2026-07-27", post.call_args[0][0])

    def test_unknown_session_label_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            pending = Path(temp) / "pending.json"
            dss_scheduler.queue_notice("DSS did not run: x", reason="dns",
                                       session=None, path=pending)
            with patch.object(dss_scheduler, "_post_telegram") as post:
                dss_scheduler.drain_pending_notices(pending)
            sent = post.call_args[0][0]
        self.assertIn("session unknown)", sent)
        self.assertNotIn("unknown session", sent)


class TestMissedSessionDetection(unittest.TestCase):
    """A machine that is off overnight means the timer never fires -- no run,
    no state update, no notice. The state file only ever holds the *last*
    reported session, so a multi-day gap was previously invisible: the next
    successful run just quietly reports the newest session as if nothing had
    been skipped. This detects the gap by walking the approved NYSE calendar
    between the last reported session and the one about to be processed."""

    def test_no_gap_between_consecutive_trading_sessions(self):
        self.assertEqual(dss_scheduler.detect_missed_sessions("2026-07-23", "2026-07-24"), [])

    def test_same_session_has_no_gap(self):
        self.assertEqual(dss_scheduler.detect_missed_sessions("2026-07-24", "2026-07-24"), [])

    def test_no_prior_state_has_no_gap(self):
        # First-ever run: nothing to compare against, not "all history missing".
        self.assertEqual(dss_scheduler.detect_missed_sessions(None, "2026-07-24"), [])
        self.assertEqual(dss_scheduler.detect_missed_sessions("", "2026-07-24"), [])

    def test_detects_missed_sessions_across_a_weekend_plus_machine_gap(self):
        # Last reported Wed 2026-07-22; machine off Thu/Fri; next run targets
        # Mon 2026-07-27. Weekend itself must not appear (not trading days).
        missed = dss_scheduler.detect_missed_sessions("2026-07-22", "2026-07-27")
        self.assertEqual(missed, ["2026-07-23", "2026-07-24"])

    def test_missed_session_notice_lists_the_dates_and_uses_its_own_marker(self):
        with patch.object(dss_scheduler, "_load_project_env"), \
             patch.dict(dss_scheduler.os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}, clear=False):
            result = dss_scheduler.send_missed_session_notice(["2026-07-23", "2026-07-24"])
        self.assertFalse(result["sent"])
        self.assertIn(dss_scheduler.MISSED_SESSION_MARKER, result["text"])
        self.assertIn("2026-07-23", result["text"])
        self.assertIn("2026-07-24", result["text"])
        self.assertNotIn(dss_scheduler.DSS_FAILURE_MARKER, result["text"])

    def test_missed_session_notice_is_retried_then_queued(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(dss_scheduler, "_load_project_env"), \
             patch.dict(dss_scheduler.os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}, clear=False), \
             patch.object(dss_scheduler, "_post_telegram", side_effect=OSError("down")) as post:
            result = dss_scheduler.send_missed_session_notice(
                ["2026-07-23"], attempts=2, retry_sleep_seconds=0,
                queue_path=Path(temp) / "pending.json", sleep=lambda _: None,
            )
        self.assertEqual(post.call_count, 2)
        self.assertFalse(result["sent"])
        self.assertTrue(result["queued"])

    def test_chain_sends_missed_session_notice_when_gap_detected_before_running(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            pending_path = Path(temp) / "pending.json"
            dss_scheduler.save_state({"last_target_session": "2026-07-22", "status": "SENT"}, state_path)
            with patch.object(dss_scheduler, "_run_step",
                              return_value={"name": "refresh", "ok": True, "elapsed_sec": 0.1}), \
                 patch.object(dss_scheduler, "send_missed_session_notice",
                              return_value={"sent": True}) as notice, \
                 patch.object(dss_scheduler, "target_session", return_value="2026-07-27"):
                result = dss_scheduler.run_daily_chain(
                    as_of=datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc),
                    state_path=state_path, pending_notice_path=pending_path,
                )
            notice.assert_called_once_with(["2026-07-23", "2026-07-24"], queue_path=pending_path)
            self.assertEqual(result["status"], "SENT")
            self.assertEqual(result["missed_sessions"], ["2026-07-23", "2026-07-24"])

    def test_chain_does_not_send_missed_session_notice_when_gap_is_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            dss_scheduler.save_state({"last_target_session": "2026-07-24", "status": "SENT"}, state_path)
            with patch.object(dss_scheduler, "_run_step",
                              return_value={"name": "refresh", "ok": True, "elapsed_sec": 0.1}), \
                 patch.object(dss_scheduler, "send_missed_session_notice") as notice, \
                 patch.object(dss_scheduler, "target_session", return_value="2026-07-27"):
                dss_scheduler.run_daily_chain(
                    as_of=datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc),
                    state_path=state_path, pending_notice_path=Path(temp) / "pending.json",
                )
            notice.assert_not_called()

    def test_chain_detects_gap_but_does_not_send_when_send_is_false(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            dss_scheduler.save_state({"last_target_session": "2026-07-22", "status": "SENT"}, state_path)
            with patch.object(dss_scheduler, "_run_step",
                              return_value={"name": "refresh", "ok": True, "elapsed_sec": 0.1}), \
                 patch.object(dss_scheduler, "send_missed_session_notice") as notice, \
                 patch.object(dss_scheduler, "target_session", return_value="2026-07-27"):
                result = dss_scheduler.run_daily_chain(
                    as_of=datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc),
                    state_path=state_path, pending_notice_path=Path(temp) / "pending.json",
                    send=False,
                )
            notice.assert_not_called()
            self.assertEqual(result["missed_sessions"], ["2026-07-23", "2026-07-24"])


class TestRiskEngineChainStep(unittest.TestCase):
    """Phase 11B: Risk Engine Mode A (ATR/heat/loss-limit/kill-switch) is
    advisory-only reporting -- it must never gate or delay the BUY/SELL
    report, so it runs last and its failure must not fail the chain."""

    def test_chain_includes_a_risk_engine_step(self):
        steps = dss_scheduler._chain_steps("python", skip_refresh=False)
        names = [step["name"] for step in steps]
        self.assertIn("risk_engine", names)
        risk_step = next(step for step in steps if step["name"] == "risk_engine")
        self.assertIn("--risk-engine-dashboard", risk_step["command"])
        self.assertEqual(Path(risk_step["command"][1]).name, "universe_scanner.py")

    def test_risk_engine_step_runs_after_the_report_step(self):
        steps = dss_scheduler._chain_steps("python", skip_refresh=False)
        names = [step["name"] for step in steps]
        self.assertLess(names.index("report"), names.index("risk_engine"))

    def test_risk_engine_step_is_non_critical_unlike_the_other_three(self):
        steps = dss_scheduler._chain_steps("python", skip_refresh=False)
        by_name = {step["name"]: step for step in steps}
        self.assertFalse(by_name["risk_engine"].get("critical", True))
        for critical_name in ("refresh", "signals", "report"):
            self.assertTrue(by_name[critical_name].get("critical", True))

    def test_chain_still_invokes_only_report_clis_with_the_new_step(self):
        # risk_engine reuses universe_scanner.py (same binary as signals), so
        # the set of distinct scripts invoked must not grow.
        steps = dss_scheduler._chain_steps("python", skip_refresh=False)
        scripts = {Path(step["command"][1]).name for step in steps}
        self.assertEqual(scripts, {"vericekici_updated.py", "universe_scanner.py", "bot.py"})

    def test_chain_does_not_fail_when_only_the_risk_engine_step_fails(self):
        def _run(step, timeout_seconds):
            if step["name"] == "risk_engine":
                return {"name": "risk_engine", "ok": False, "error": "risk_engine blew up"}
            return {"name": step["name"], "ok": True}

        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            with patch.object(dss_scheduler, "_run_step", side_effect=_run), \
                 patch.object(dss_scheduler, "send_failure_notice") as notice, \
                 patch.object(dss_scheduler, "target_session", return_value="2026-07-24"):
                result = dss_scheduler.run_daily_chain(as_of=FRIDAY, state_path=state_path)
            self.assertEqual(result["status"], "SENT")
            notice.assert_not_called()
            state = dss_scheduler.load_state(state_path)
            self.assertEqual(state["status"], "SENT")

    def test_chain_still_fails_when_the_report_step_fails_even_with_risk_engine_present(self):
        def _run(step, timeout_seconds):
            if step["name"] == "report":
                return {"name": "report", "ok": False, "error": "report blew up"}
            return {"name": step["name"], "ok": True}

        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "scheduler_state.json"
            with patch.object(dss_scheduler, "_run_step", side_effect=_run), \
                 patch.object(dss_scheduler, "send_failure_notice", return_value={"sent": True}) as notice, \
                 patch.object(dss_scheduler, "target_session", return_value="2026-07-24"):
                result = dss_scheduler.run_daily_chain(as_of=FRIDAY, state_path=state_path)
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(result["failed_step"], "report")
            notice.assert_called_once()


if __name__ == "__main__":
    unittest.main()
