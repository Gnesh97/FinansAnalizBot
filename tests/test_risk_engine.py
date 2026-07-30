import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.risk_engine import build_risk_engine_report, write_risk_engine_report


def _write_fixture_daily_bars(daily_dir: Path, symbol: str, closes: list[float]) -> None:
    daily_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, close in enumerate(closes):
        date = f"2026-01-{i + 1:02d} 04:00:00+00:00" if i < 31 else f"2026-02-{i - 30:02d} 04:00:00+00:00"
        rows.append(
            {
                "Date": date,
                "Open": close,
                "High": close * 1.02,
                "Low": close * 0.98,
                "Close": close,
                "Volume": 1_000_000,
            }
        )
    pd.DataFrame(rows).to_csv(daily_dir / f"{symbol}_1D.csv", index=False)


def _write_fixture_reports(
    latest: Path,
    *,
    total_unrealized_pnl_pct: float = 1.0,
    aaa_shares: float = 10,
    bbb_shares: float = 5,
) -> None:
    pd.DataFrame(
        [
            {
                "date": "fixture-fill-date-1",
                "strategy_name": "fixture_strategy",
                "symbol": "AAA",
                "action": "BUY",
                "signal_date": "fixture-signal-date-1",
                "assumed_fill_price": 10,
                "fill_status": "PAPER_FILLED",
                "shares": aaa_shares,
                "notional": aaa_shares * 10,
            },
            {
                "date": "fixture-fill-date-2",
                "strategy_name": "fixture_strategy",
                "symbol": "BBB",
                "action": "BUY",
                "signal_date": "fixture-signal-date-2",
                "assumed_fill_price": 20,
                "fill_status": "PAPER_FILLED",
                "shares": bbb_shares,
                "notional": bbb_shares * 20,
            },
        ]
    ).to_csv(latest / "paper_trade_log.csv", index=False)
    total_notional = aaa_shares * 10 + bbb_shares * 20
    monitor_rows = [
        {"section": "summary", "metric": "open_paper_positions", "value": 2},
        {"section": "summary", "metric": "total_open_notional", "value": total_notional},
        {"section": "summary", "metric": "total_unrealized_pnl", "value": total_notional * total_unrealized_pnl_pct / 100},
        {"section": "summary", "metric": "total_unrealized_pnl_pct", "value": total_unrealized_pnl_pct},
        {"section": "open_position", "symbol": "AAA", "strategy_name": "fixture_strategy", "notional": aaa_shares * 10},
        {"section": "open_position", "symbol": "BBB", "strategy_name": "fixture_strategy", "notional": bbb_shares * 20},
        {"section": "risk_check", "metric": "missing_mark_price_symbols", "value": "none"},
        {"section": "risk_check", "metric": "stale_mark_price_symbols", "value": "none"},
    ]
    pd.DataFrame(monitor_rows).to_csv(latest / "paper_portfolio_monitor.csv", index=False)


class RiskEngineTest(unittest.TestCase):
    def test_reports_atr_pct_and_dollar_atr_per_open_position_reusing_strategy_atr(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            _write_fixture_reports(latest)
            _write_fixture_daily_bars(daily_dir, "AAA", [10.0 + 0.1 * i for i in range(40)])
            _write_fixture_daily_bars(daily_dir, "BBB", [20.0 + 0.2 * i for i in range(40)])

            from testdata.strategies.relative_strength_rotation import _atr_from_ohlc

            report = build_risk_engine_report(latest, daily_dir)

            position_risk = {row["symbol"]: row for row in report["position_risk"]}
            self.assertIn("AAA", position_risk)
            self.assertIn("BBB", position_risk)
            aaa_daily = pd.read_csv(daily_dir / "AAA_1D.csv")
            expected_atr = _atr_from_ohlc(aaa_daily, 20).iloc[-1]
            expected_atr_pct = 100 * expected_atr / aaa_daily["Close"].iloc[-1]
            self.assertAlmostEqual(position_risk["AAA"]["atr_pct"], expected_atr_pct, places=6)
            self.assertGreater(position_risk["AAA"]["dollar_atr"], 0)

    def test_computes_portfolio_heat_as_pct_of_open_notional(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            _write_fixture_reports(latest, aaa_shares=10, bbb_shares=5)
            _write_fixture_daily_bars(daily_dir, "AAA", [10.0] * 40)
            _write_fixture_daily_bars(daily_dir, "BBB", [20.0] * 40)

            report = build_risk_engine_report(latest, daily_dir)

            heat = report["portfolio_heat"]
            position_risk = {row["symbol"]: row for row in report["position_risk"]}
            expected_dollar_risk = (
                10 * position_risk["AAA"]["dollar_atr"] + 5 * position_risk["BBB"]["dollar_atr"]
            )
            expected_heat_pct = expected_dollar_risk / 200.0 * 100
            self.assertAlmostEqual(heat["heat_pct"], expected_heat_pct, places=4)
            self.assertIn("open_notional", heat["heat_basis"])

    def test_flags_daily_loss_limit_warning_from_open_book_unrealized_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            _write_fixture_reports(latest, total_unrealized_pnl_pct=-4.0)
            _write_fixture_daily_bars(daily_dir, "AAA", [10.0] * 40)
            _write_fixture_daily_bars(daily_dir, "BBB", [20.0] * 40)

            report = build_risk_engine_report(latest, daily_dir)

            loss_limit = report["daily_loss_limit"]
            self.assertTrue(loss_limit["breached"])
            self.assertEqual(loss_limit["open_book_unrealized_pnl_pct"], -4.0)

    def test_does_not_flag_daily_loss_limit_when_within_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            _write_fixture_reports(latest, total_unrealized_pnl_pct=-1.0)
            _write_fixture_daily_bars(daily_dir, "AAA", [10.0] * 40)
            _write_fixture_daily_bars(daily_dir, "BBB", [20.0] * 40)

            report = build_risk_engine_report(latest, daily_dir)

            self.assertFalse(report["daily_loss_limit"]["breached"])

    def test_kill_switch_triggers_advisory_flag_on_deep_loss_without_blocking_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            _write_fixture_reports(latest, total_unrealized_pnl_pct=-6.0)
            _write_fixture_daily_bars(daily_dir, "AAA", [10.0] * 40)
            _write_fixture_daily_bars(daily_dir, "BBB", [20.0] * 40)

            report = build_risk_engine_report(latest, daily_dir)

            kill_switch = report["kill_switch"]
            self.assertTrue(kill_switch["advisory_triggered"])
            self.assertIn("reason", kill_switch)

    def test_kill_switch_triggers_advisory_flag_on_high_portfolio_heat_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            daily_dir.mkdir()
            # total_unrealized_pnl_pct stays shallow; only heat (wide ATR% via a
            # 10% High/Low spread) should trip the kill switch here.
            _write_fixture_reports(latest, total_unrealized_pnl_pct=-1.0)
            for symbol, base_close in [("AAA", 10.0), ("BBB", 20.0)]:
                rows = []
                for i in range(40):
                    date = f"2026-01-{i + 1:02d} 04:00:00+00:00" if i < 31 else f"2026-02-{i - 30:02d} 04:00:00+00:00"
                    rows.append(
                        {
                            "Date": date,
                            "Open": base_close,
                            "High": base_close * 1.05,
                            "Low": base_close * 0.95,
                            "Close": base_close,
                            "Volume": 1_000_000,
                        }
                    )
                pd.DataFrame(rows).to_csv(daily_dir / f"{symbol}_1D.csv", index=False)

            report = build_risk_engine_report(latest, daily_dir)

            self.assertTrue(report["portfolio_heat"]["heat_warning"])
            self.assertFalse(report["daily_loss_limit"]["breached"])
            kill_switch = report["kill_switch"]
            self.assertTrue(kill_switch["advisory_triggered"])
            self.assertIn("heat", kill_switch["reason"].lower())

    def test_kill_switch_not_triggered_on_shallow_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            _write_fixture_reports(latest, total_unrealized_pnl_pct=-1.0)
            _write_fixture_daily_bars(daily_dir, "AAA", [10.0] * 40)
            _write_fixture_daily_bars(daily_dir, "BBB", [20.0] * 40)

            report = build_risk_engine_report(latest, daily_dir)

            self.assertFalse(report["kill_switch"]["advisory_triggered"])

    def test_all_loss_limit_and_kill_switch_fields_carry_paper_book_only_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            _write_fixture_reports(latest, total_unrealized_pnl_pct=-6.0)
            _write_fixture_daily_bars(daily_dir, "AAA", [10.0] * 40)
            _write_fixture_daily_bars(daily_dir, "BBB", [20.0] * 40)

            report = build_risk_engine_report(latest, daily_dir)

            self.assertIn("paper", report["daily_loss_limit"]["basis_note"].lower())
            self.assertIn("not", report["daily_loss_limit"]["basis_note"].lower())
            self.assertIn("paper", report["kill_switch"]["basis_note"].lower())
            self.assertIn("advisory", report["kill_switch"]["basis_note"].lower())

    def test_advisory_report_does_not_mutate_any_input_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            _write_fixture_reports(latest)
            _write_fixture_daily_bars(daily_dir, "AAA", [10.0] * 40)
            _write_fixture_daily_bars(daily_dir, "BBB", [20.0] * 40)
            watched = [
                latest / "paper_trade_log.csv",
                latest / "paper_portfolio_monitor.csv",
                daily_dir / "AAA_1D.csv",
                daily_dir / "BBB_1D.csv",
            ]
            before = {path: path.read_text(encoding="utf-8") for path in watched}

            write_risk_engine_report(latest, daily_dir)

            after = {path: path.read_text(encoding="utf-8") for path in watched}
            self.assertEqual(before, after)

    def test_writes_md_csv_json_triplet(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            _write_fixture_reports(latest)
            _write_fixture_daily_bars(daily_dir, "AAA", [10.0] * 40)
            _write_fixture_daily_bars(daily_dir, "BBB", [20.0] * 40)

            result = write_risk_engine_report(latest, daily_dir)

            self.assertTrue(Path(result["csv_path"]).exists())
            self.assertTrue(Path(result["md_path"]).exists())
            self.assertTrue(Path(result["json_path"]).exists())
            json_payload = json.loads(Path(result["json_path"]).read_text(encoding="utf-8"))
            self.assertIn("kill_switch", json_payload)

    def test_handles_missing_daily_bars_for_a_symbol_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            daily_dir = Path(tmp) / "daily"
            _write_fixture_reports(latest)
            _write_fixture_daily_bars(daily_dir, "AAA", [10.0] * 40)
            # BBB daily bars intentionally missing.

            report = build_risk_engine_report(latest, daily_dir)

            position_risk = {row["symbol"]: row for row in report["position_risk"]}
            self.assertIn("BBB", position_risk)
            self.assertTrue(position_risk["BBB"]["data_missing"])

    def test_fixture_uses_marked_fixture_dates_and_symbols_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp) / "latest"
            latest.mkdir()
            _write_fixture_reports(latest)

            text = (latest / "paper_trade_log.csv").read_text(encoding="utf-8")

            self.assertIn("fixture-fill-date", text)
            self.assertIn("fixture-signal-date", text)


if __name__ == "__main__":
    unittest.main()
