import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.intraday_paper_monitor import build_intraday_paper_monitor, write_intraday_paper_monitor
from testdata.paper_ops_dashboard import build_paper_ops_dashboard
from testdata.paper_state_manager import write_order_review, write_pending_orders, write_trade_log


FIXTURE_SIGNAL_DATE = "2026-01-05 13:50:00-04:00"
FIXTURE_NEXT_OPEN = "2026-01-06"
FIXTURE_STRATEGY = "fixture_strategy"


def _trade(symbol, action="BUY", price=10.0, shares=2.0):
    return {
        "date": "2026-01-05 16:00:00",
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": symbol,
        "action": action,
        "signal_date": FIXTURE_SIGNAL_DATE,
        "assumed_fill_price": price,
        "fill_status": "PAPER_FILLED",
        "shares": shares,
        "notional": price * shares,
        "paper_capital": 1000.0,
        "reason": "fixture",
        "liquidity_guard_pass": True,
        "notes": "fixture",
    }


def _pending(symbol, *, status="PENDING_NEXT_OPEN", user_decision="ACCEPT", action="BUY", reason="fixture_pending"):
    return {
        "created_at": "2026-01-05 16:00:00",
        "signal_date": FIXTURE_SIGNAL_DATE,
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": symbol,
        "action": action,
        "paper_rank": 1,
        "ranking_score": 1.0,
        "paper_capital": 1000.0,
        "paper_position_notional": 100.0,
        "liquidity_guard_pass": True,
        "estimated_dollar_volume": 1000000.0,
        "order_size_pct_of_dollar_volume": 0.01,
        "status": status,
        "user_decision": user_decision,
        "reason": reason,
        "expected_next_open_date": FIXTURE_NEXT_OPEN,
    }


def _write_raw_5min(root: Path, symbol: str, rows: list[dict[str, object]]) -> None:
    raw_dir = root / "hisseler" / "raw_5min"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(raw_dir / f"{symbol}_5min.csv", index=False)


def _rth_rows(date: str, closes: list[float]) -> list[dict[str, object]]:
    start = pd.Timestamp(f"{date} 09:30:00", tz="America/New_York")
    rows = []
    for idx, close in enumerate(closes):
        ts = start + pd.Timedelta(minutes=5 * idx)
        rows.append(
            {
                "Date": ts.tz_convert("UTC"),
                "Open": close - 0.25,
                "High": close + 0.5,
                "Low": close - 0.5,
                "Close": close,
                "Volume": 100 + idx,
            }
        )
    return rows


class IntradayPaperMonitorTest(unittest.TestCase):
    def test_open_buy_position_uses_latest_raw_5min_close_for_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame([_trade("AAA", price=10.0, shares=3.0)]))
            write_pending_orders(latest, pd.DataFrame())
            write_order_review(latest, pd.DataFrame())
            _write_raw_5min(root, "AAA", _rth_rows("2026-01-06", [10.0, 11.0, 12.0]))

            result = build_intraday_paper_monitor(root / "hisseler", latest)
            open_rows = result["open_positions"]

            self.assertEqual(len(open_rows), 1)
            self.assertEqual(float(open_rows.iloc[0]["latest_5min_price"]), 12.0)
            self.assertEqual(float(open_rows.iloc[0]["intraday_unrealized_pnl"]), 6.0)
            self.assertEqual(open_rows.iloc[0]["mark_status"], "OK")

    def test_buy_then_sell_netted_position_does_not_appear_as_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame([_trade("AAA", "BUY", 10.0, 2.0), _trade("AAA", "SELL", 12.0, 2.0)]))
            write_pending_orders(latest, pd.DataFrame())
            write_order_review(latest, pd.DataFrame())
            _write_raw_5min(root, "AAA", _rth_rows("2026-01-06", [12.0]))

            result = build_intraday_paper_monitor(root / "hisseler", latest)

            self.assertEqual(len(result["open_positions"]), 0)
            self.assertEqual(result["summary"]["open_positions_count"], 0)

    def test_accept_pending_next_open_reports_first_rth_5min_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame())
            write_pending_orders(latest, pd.DataFrame([_pending("BBB")]))
            write_order_review(latest, pd.DataFrame())
            rows = _rth_rows(FIXTURE_NEXT_OPEN, [21.0, 22.0])
            rows[0]["Open"] = 20.5
            _write_raw_5min(root, "BBB", rows)

            result = build_intraday_paper_monitor(root / "hisseler", latest)
            pending = result["pending_readiness"]

            self.assertEqual(len(pending), 1)
            self.assertEqual(bool(pending.iloc[0]["has_target_session_5min_rows"]), True)
            self.assertEqual(float(pending.iloc[0]["hypothetical_5min_fill_price"]), 20.5)
            self.assertEqual(bool(pending.iloc[0]["would_fill_if_5min_execution_enabled"]), True)

    def test_accept_wait_for_next_open_counts_as_accepted_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame())
            write_pending_orders(latest, pd.DataFrame([_pending("WAIT", status="WAIT_FOR_NEXT_OPEN")]))
            write_order_review(latest, pd.DataFrame())
            _write_raw_5min(root, "WAIT", _rth_rows(FIXTURE_NEXT_OPEN, [21.0]))

            result = build_intraday_paper_monitor(root / "hisseler", latest)

            self.assertEqual(result["summary"]["accepted_pending_orders_count"], 1)
            self.assertEqual(len(result["pending_readiness"]), 1)

    def test_accept_pending_status_counts_as_accepted_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame())
            write_pending_orders(latest, pd.DataFrame([_pending("PEND", status="PENDING")]))
            write_order_review(latest, pd.DataFrame())
            _write_raw_5min(root, "PEND", _rth_rows(FIXTURE_NEXT_OPEN, [21.0]))

            result = build_intraday_paper_monitor(root / "hisseler", latest)

            self.assertEqual(result["summary"]["accepted_pending_orders_count"], 1)

    def test_review_and_blocked_rows_do_not_count_as_accepted_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame())
            rows = [
                _pending("REV", status="PENDING", user_decision="REVIEW_REQUIRED"),
                _pending("HOLD", status="PENDING", user_decision="HOLD_REVIEW"),
                _pending("BLK", status="BLOCKED", user_decision="REVIEW_REQUIRED", reason="fixture;buy_already_open"),
            ]
            write_pending_orders(latest, pd.DataFrame(rows))
            write_order_review(latest, pd.DataFrame())

            result = build_intraday_paper_monitor(root / "hisseler", latest)

            self.assertEqual(result["summary"]["accepted_pending_orders_count"], 0)
            self.assertTrue(result["pending_readiness"].empty)

    def test_accepted_pending_count_matches_ops_dashboard_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame())
            write_pending_orders(
                latest,
                pd.DataFrame(
                    [
                        _pending("SELL", status="PENDING", action="SELL"),
                        _pending("REV", status="PENDING", user_decision="REVIEW_REQUIRED"),
                        _pending("BLK", status="BLOCKED", user_decision="REVIEW_REQUIRED", reason="fixture;buy_already_open"),
                    ]
                ),
            )
            write_order_review(latest, pd.DataFrame())
            _write_raw_5min(root, "SELL", _rth_rows(FIXTURE_NEXT_OPEN, [21.0]))

            intraday = build_intraday_paper_monitor(root / "hisseler", latest)
            ops = build_paper_ops_dashboard(latest)

            self.assertEqual(
                intraday["summary"]["accepted_pending_orders_count"],
                ops["pending_workflow"]["accepted_pending_orders_count"],
            )

    def test_missing_raw_5min_file_becomes_warning_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame([_trade("MISS")]))
            write_pending_orders(latest, pd.DataFrame())
            write_order_review(latest, pd.DataFrame())

            result = build_intraday_paper_monitor(root / "hisseler", latest)
            row = result["open_positions"].iloc[0]

            self.assertEqual(row["mark_status"], "MISSING_RAW_5MIN")
            self.assertEqual(row["warning"], "MISSING_RAW_5MIN")

    def test_incomplete_130min_block_is_detected_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame([_trade("PART")]))
            write_pending_orders(latest, pd.DataFrame())
            write_order_review(latest, pd.DataFrame())
            _write_raw_5min(root, "PART", _rth_rows("2026-01-06", [10.0, 10.5, 11.0]))

            result = build_intraday_paper_monitor(root / "hisseler", latest)
            bars = result["current_130min_bars"]

            self.assertEqual(bool(bars.iloc[0]["current_block_complete"]), False)
            self.assertEqual(result["summary"]["current_130min_incomplete_count"], 1)

    def test_monitor_does_not_mutate_paper_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame([_trade("SAFE")]))
            write_pending_orders(latest, pd.DataFrame([_pending("SAFE")]))
            write_order_review(latest, pd.DataFrame([{"symbol": "SAFE", "strategy_name": FIXTURE_STRATEGY, "action": "BUY", "user_decision": "ACCEPT"}]))
            _write_raw_5min(root, "SAFE", _rth_rows(FIXTURE_NEXT_OPEN, [10.0, 11.0]))
            state_paths = [
                latest / "paper_trade_log.csv",
                latest / "paper_pending_orders.csv",
                latest / "paper_order_review.csv",
            ]
            before = {path.name: path.read_text(encoding="utf-8") for path in state_paths}

            result = write_intraday_paper_monitor(root / "hisseler", latest)
            after = {path.name: path.read_text(encoding="utf-8") for path in state_paths}

            self.assertEqual(before, after)
            self.assertTrue((latest / "intraday_paper_monitor.csv").exists())
            self.assertTrue((latest / "intraday_paper_monitor.md").exists())
            self.assertTrue((latest / "intraday_paper_monitor.json").exists())
            self.assertEqual(result["summary"]["state_mutated"], False)

    def test_safety_flags_false_and_no_post_or_submit_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "reports" / "latest"
            write_trade_log(latest, pd.DataFrame())
            write_pending_orders(latest, pd.DataFrame())
            write_order_review(latest, pd.DataFrame())

            result = build_intraday_paper_monitor(root / "hisseler", latest)

            for key in [
                "state_mutated",
                "broker_submit_enabled",
                "live_trading_enabled",
                "short_trading_enabled",
                "submit_order_called",
                "post_request_used",
            ]:
                self.assertEqual(result["summary"][key], False)

    def test_no_hardcoded_production_dates_or_symbols(self):
        source = Path("testdata/intraday_paper_monitor.py").read_text(encoding="utf-8")

        self.assertNotIn("2026-", source)
        self.assertNotIn("AAPL", source)
        self.assertNotIn("AMAT", source)
        self.assertNotIn("PENDING_NEXT_OPEN,2026", source)


if __name__ == "__main__":
    unittest.main()
