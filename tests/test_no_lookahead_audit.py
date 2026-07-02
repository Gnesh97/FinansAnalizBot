import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.execution_engine import build_order_intents
from testdata.no_lookahead_audit import build_no_lookahead_audit, write_no_lookahead_audit
from testdata.paper_state_manager import read_trade_log
from testdata.runner import (
    _apply_previous_pending_next_open_orders,
    _next_open_for_signal,
    _paper_signal_columns,
    _write_paper_pending_orders,
)


FIXTURE_STRATEGY = "fixture_strategy"


def _filled_trade(fill_timestamp, signal_date="2026-01-02 13:50:00-04:00"):
    return {
        "date": "fixture-log-created",
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": "FIXA",
        "action": "BUY",
        "signal_date": signal_date,
        "signal_price": 10.0,
        "assumed_fill_price": 11.0,
        "fill_status": "PAPER_FILLED",
        "shares": 1.0,
        "notional": 11.0,
        "notes": f"fixture;fill_bar_timestamp={fill_timestamp};fill_price_source=next_130min_open",
    }


def _write_latest_files(latest: Path, *, trade_log=None, pending=None, signals=None):
    (pd.DataFrame(trade_log or [])).to_csv(latest / "paper_trade_log.csv", index=False)
    (pd.DataFrame(pending or [])).to_csv(latest / "paper_pending_orders.csv", index=False)
    (pd.DataFrame(signals or [])).to_csv(latest / "paper_signal_report.csv", index=False)
    pd.DataFrame([{"symbol": "FIXR", "user_decision": "HOLD_REVIEW"}]).to_csv(
        latest / "paper_order_review.csv",
        index=False,
    )


def _pending_row(expected_next_open_date=""):
    return {
        "created_at": "fixture-run",
        "signal_date": "2026-01-02 13:50:00-04:00",
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": "FIXP",
        "action": "BUY",
        "status": "PENDING",
        "user_decision": "ACCEPT",
        "expected_next_open_date": expected_next_open_date,
        "reason": "fixture_missing_next_open",
    }


def _signal_row(symbol="FIXA"):
    return {
        "strategy_name": FIXTURE_STRATEGY,
        "symbol": symbol,
        "action": "BUY",
        "signal_date": "2026-01-02 13:50:00-04:00",
        "paper_actionable": True,
        "fill_bar_timestamp": "",
        "fill_price_source": "next_130min_open",
    }


def _write_raw_5min(data_dir: Path, symbol: str, timestamps):
    raw_dir = data_dir / "raw_5min"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "Date": timestamps,
            "Open": [10.0] * len(timestamps),
            "High": [10.0] * len(timestamps),
            "Low": [10.0] * len(timestamps),
            "Close": [10.0] * len(timestamps),
            "Volume": [100] * len(timestamps),
        }
    ).to_csv(raw_dir / f"{symbol}_5min.csv", index=False)


def _write_130min(data_dir: Path, symbol: str, timestamps):
    bars_dir = data_dir / "bars_130min"
    bars_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "Date": timestamps,
            "Open": [10.0] * len(timestamps),
            "High": [10.0] * len(timestamps),
            "Low": [10.0] * len(timestamps),
            "Close": [10.0] * len(timestamps),
            "Volume": [100] * len(timestamps),
        }
    ).to_csv(bars_dir / f"{symbol}_130min.csv", index=False)


class NoLookaheadAuditTest(unittest.TestCase):
    def test_filled_trade_after_signal_passes_next_open_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_latest_files(latest, trade_log=[_filled_trade("2026-01-05 09:30:00-04:00")])

            result = build_no_lookahead_audit(latest)

            fill = result["fill_timing"].iloc[0]
            self.assertEqual(fill["expected_next_open_rule_status"], "PASS")
            self.assertTrue(fill["fill_after_signal"])

    def test_filled_trade_equal_signal_fails_next_open_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            timestamp = "2026-01-02 13:50:00-04:00"
            _write_latest_files(latest, trade_log=[_filled_trade(timestamp, signal_date=timestamp)])

            result = build_no_lookahead_audit(latest)

            fill = result["fill_timing"].iloc[0]
            self.assertEqual(fill["expected_next_open_rule_status"], "FAIL")
            self.assertTrue(fill["same_bar_fill_detected"])

    def test_filled_trade_before_signal_fails_next_open_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_latest_files(latest, trade_log=[_filled_trade("2026-01-02 09:30:00-04:00")])

            result = build_no_lookahead_audit(latest)

            self.assertEqual(result["fill_timing"].iloc[0]["expected_next_open_rule_status"], "FAIL")

    def test_pending_row_with_missing_next_open_is_warning_not_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_latest_files(latest, pending=[_pending_row()])

            result = build_no_lookahead_audit(latest)

            pending = result["fill_timing"].iloc[0]
            self.assertEqual(pending["expected_next_open_rule_status"], "WARNING")
            self.assertIn("missing_next_open", pending["reason"])

    def test_incomplete_130min_block_is_marked_excluded_expected(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            data = Path(tmp) / "hisseler"
            latest.mkdir()
            _write_latest_files(latest, signals=[_signal_row()])
            _write_130min(data, "FIXA", ["2026-01-02 18:50:00+00:00"])
            _write_raw_5min(data, "FIXA", ["2026-01-02 14:30:00+00:00", "2026-01-02 14:35:00+00:00"])

            result = build_no_lookahead_audit(latest, data)

            bar = result["bar_completeness"].iloc[0]
            self.assertTrue(bar["incomplete_bar_excluded_expected"])
            self.assertFalse(bar["current_block_complete"])

    def test_signal_after_latest_available_data_fails_no_future_row_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            data = Path(tmp) / "hisseler"
            latest.mkdir()
            _write_latest_files(latest, signals=[_signal_row()])
            _write_130min(data, "FIXA", ["2026-01-02 14:30:00+00:00"])
            _write_raw_5min(data, "FIXA", ["2026-01-02 14:30:00+00:00", "2026-01-02 16:00:00+00:00"])

            result = build_no_lookahead_audit(latest, data)

            self.assertEqual(result["signal_timing"].iloc[0]["lookahead_status"], "FAIL")

    def test_next_open_helper_never_uses_signal_bar(self):
        asset_data = {
            "FIXA": pd.DataFrame(
                {"Open": [10.0, 12.0]},
                index=pd.to_datetime(["2026-01-02 13:50:00-04:00", "2026-01-05 09:30:00-04:00"]),
            )
        }

        self.assertEqual(float(_next_open_for_signal(asset_data, "FIXA", "2026-01-02 13:50:00-04:00")), 12.0)

    def test_next_open_helper_returns_pending_when_no_future_bar_exists(self):
        asset_data = {
            "FIXA": pd.DataFrame(
                {"Open": [10.0]},
                index=pd.to_datetime(["2026-01-02 13:50:00-04:00"]),
            )
        }

        self.assertTrue(pd.isna(_next_open_for_signal(asset_data, "FIXA", "2026-01-02 13:50:00-04:00")))

    def test_audit_writer_does_not_mutate_paper_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_latest_files(latest, trade_log=[_filled_trade("2026-01-05 09:30:00-04:00")], pending=[_pending_row()])
            watched = [latest / "paper_trade_log.csv", latest / "paper_pending_orders.csv", latest / "paper_order_review.csv"]
            before = {path.name: path.read_text(encoding="utf-8") for path in watched}

            write_no_lookahead_audit(latest)

            after = {path.name: path.read_text(encoding="utf-8") for path in watched}
            self.assertEqual(before, after)

    def test_existing_duplicate_buy_block_behavior_remains_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame(
                [
                    {
                        "date": "fixture-date",
                        "strategy_name": FIXTURE_STRATEGY,
                        "symbol": "FIXA",
                        "action": "BUY",
                        "signal_date": "fixture-signal-date",
                        "assumed_fill_price": 10.0,
                        "fill_status": "PAPER_FILLED",
                        "shares": 1.0,
                    }
                ]
            ).to_csv(latest / "paper_trade_log.csv", index=False)
            report = pd.DataFrame(
                [
                    {
                        "run_datetime": "fixture-run",
                        "signal_date": "fixture-signal-date-2",
                        "strategy_name": FIXTURE_STRATEGY,
                        "symbol": "FIXA",
                        "action": "BUY",
                        "paper_actionable": True,
                        "user_decision": "ACCEPT",
                        "fill_status": "PENDING_NEXT_OPEN",
                        "reason": "fixture_latest_entry_signal",
                        "liquidity_guard_pass": True,
                    }
                ],
                columns=_paper_signal_columns(),
            )

            _write_paper_pending_orders(report, latest / "paper_pending_orders.csv", trade_log_path=latest / "paper_trade_log.csv")

            pending = pd.read_csv(latest / "paper_pending_orders.csv")
            intents, _ = build_order_intents(pending)
            self.assertEqual(pending.iloc[0]["status"], "BLOCKED")
            self.assertIn("buy_already_open", pending.iloc[0]["reason"])
            self.assertTrue(intents.empty)

    def test_existing_sell_quantity_matching_behavior_remains_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame(
                [
                    {
                        "date": "fixture-date",
                        "strategy_name": FIXTURE_STRATEGY,
                        "symbol": "FIXA",
                        "action": "BUY",
                        "signal_date": "fixture-signal-date",
                        "assumed_fill_price": 10.0,
                        "fill_status": "PAPER_FILLED",
                        "shares": 3.0,
                        "notional": 30.0,
                    }
                ]
            ).to_csv(latest / "paper_trade_log.csv", index=False)
            pending = pd.DataFrame(
                [
                    {
                        "run_datetime": "fixture-run",
                        "paper_strategy_mode": "main",
                        "paper_portfolio_state": "existing",
                        "strategy_name": FIXTURE_STRATEGY,
                        "signal_date": "2026-01-02 13:50:00-04:00",
                        "symbol": "FIXA",
                        "action": "SELL",
                        "fill_status": "PENDING_NEXT_OPEN",
                        "user_decision": "ACCEPT",
                        "reason": "fixture_exit",
                        "paper_actionable": True,
                        "paper_position_notional": 999.0,
                        "entry_price_reference": 10.0,
                    }
                ]
            )
            asset_data = {
                "FIXA": pd.DataFrame(
                    {"Open": [10.0, 20.0]},
                    index=pd.to_datetime(["2026-01-02 13:50:00-04:00", "2026-01-05 09:30:00-04:00"]),
                )
            }

            _apply_previous_pending_next_open_orders(
                pd.DataFrame(columns=_paper_signal_columns()),
                pending,
                pd.DataFrame(),
                latest / "paper_trade_log.csv",
                asset_data,
            )

            log = read_trade_log(latest)
            sell = log[log["action"].astype(str).str.upper().eq("SELL")].iloc[0]
            self.assertEqual(float(sell["shares"]), 3.0)

    def test_safety_flags_remain_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_latest_files(latest)

            summary = build_no_lookahead_audit(latest)["summary"]

            self.assertFalse(summary["state_mutated"])
            self.assertFalse(summary["broker_submit_enabled"])
            self.assertFalse(summary["live_trading_enabled"])
            self.assertFalse(summary["short_trading_enabled"])
            self.assertFalse(summary["submit_order_called"])
            self.assertFalse(summary["post_request_used"])

    def test_no_hardcoded_production_dates_or_symbols_in_audit_module(self):
        source = Path("testdata/no_lookahead_audit.py").read_text(encoding="utf-8")

        self.assertNotIn("2026-07", source)
        self.assertNotIn("EQIX", source)
        self.assertNotIn("NUE", source)
        self.assertNotIn("UNH", source)
        self.assertNotIn("CVS", source)


if __name__ == "__main__":
    unittest.main()
