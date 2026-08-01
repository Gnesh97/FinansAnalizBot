import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.strategy_dashboard import (
    _load_live_paper_summary,
    _load_paper_equity_series,
    _load_strategy_compact_report,
    build_strategy_dashboard,
    write_strategy_dashboard,
)


def _write_compact_report(latest: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(latest / "strategy_compact_report.csv", index=False)


def _compact_row(name: str, family: str, sharpe: float, net_return: float, score: float) -> dict:
    return {
        "Experiment Name": name,
        "Strategy Family": family,
        "Net Getiri (%)": net_return,
        "CAGR (%)": net_return / 5,
        "Max Drawdown (%)": -abs(net_return) / 4,
        "Sharpe Ratio": sharpe,
        "Trade Count": 100,
        "Win Rate (%)": 45.0,
        "Strategy Excess Return vs QQQ (%)": net_return - 50,
        "Compact Score": score,
    }


class StrategyCompactReportLoadingTest(unittest.TestCase):
    def test_reads_and_sorts_by_compact_score_descending(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_compact_report(
                latest,
                [
                    _compact_row("low_score", "donchian_breakout", 0.5, 20.0, 1.0),
                    _compact_row("high_score", "relative_strength_rotation", 1.2, 100.0, 9.0),
                ],
            )
            frame = _load_strategy_compact_report(latest)
            self.assertEqual(list(frame["Experiment Name"]), ["high_score", "low_score"])

    def test_missing_file_returns_empty_frame_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = _load_strategy_compact_report(Path(tmp))
            self.assertTrue(frame.empty)


class LivePaperSummaryLoadingTest(unittest.TestCase):
    def test_reads_summary_section_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame(
                [
                    {"section": "summary", "metric": "total_filled_trades", "value": 3},
                    {"section": "summary", "metric": "open_paper_positions", "value": 2},
                    {"section": "position", "metric": "unused_row", "value": 999},
                ]
            ).to_csv(latest / "paper_portfolio_monitor.csv", index=False)
            summary = _load_live_paper_summary(latest)
            self.assertEqual(summary.get("total_filled_trades"), "3")
            self.assertEqual(summary.get("open_paper_positions"), "2")
            self.assertNotIn("unused_row", summary)

    def test_missing_file_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = _load_live_paper_summary(Path(tmp))
            self.assertEqual(summary, {})


class PaperEquitySeriesLoadingTest(unittest.TestCase):
    def test_header_only_trade_log_returns_empty_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame(
                columns=["date", "strategy_name", "symbol", "action", "paper_capital"]
            ).to_csv(latest / "paper_trade_log.csv", index=False)
            series = _load_paper_equity_series(latest)
            self.assertTrue(series.empty)

    def test_trade_log_with_rows_returns_date_sorted_capital_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            pd.DataFrame(
                [
                    {"date": "2026-07-05", "strategy_name": "rs_v2", "symbol": "AAPL", "action": "BUY", "paper_capital": 24500.0},
                    {"date": "2026-07-01", "strategy_name": "rs_v2", "symbol": "MSFT", "action": "BUY", "paper_capital": 25000.0},
                ]
            ).to_csv(latest / "paper_trade_log.csv", index=False)
            series = _load_paper_equity_series(latest)
            self.assertEqual(list(series["date"]), ["2026-07-01", "2026-07-05"])
            self.assertEqual(list(series["paper_capital"]), [25000.0, 24500.0])


class BuildStrategyDashboardTest(unittest.TestCase):
    def test_html_is_self_contained_with_no_external_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_compact_report(latest, [_compact_row("rs_v2_top30", "relative_strength_rotation", 0.9, 172.29, 6.3)])
            html = build_strategy_dashboard(latest)
            self.assertIn("<html", html)
            self.assertIn("data:image/png;base64,", html)
            for forbidden in ("http://", "https://", "<script src", "cdn."):
                self.assertNotIn(forbidden, html)

    def test_empty_paper_book_renders_placeholder_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_compact_report(latest, [_compact_row("rs_v2_top30", "relative_strength_rotation", 0.9, 172.29, 6.3)])
            pd.DataFrame(
                [
                    {"section": "summary", "metric": "total_filled_trades", "value": 0},
                    {"section": "summary", "metric": "open_paper_positions", "value": 0},
                ]
            ).to_csv(latest / "paper_portfolio_monitor.csv", index=False)
            html = build_strategy_dashboard(latest)
            self.assertIn("No paper trades yet", html)

    def test_experiment_name_is_html_escaped_not_injected_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            latest = Path(tmp)
            _write_compact_report(
                latest,
                [_compact_row("<script>alert(1)</script>", "relative_strength_rotation", 0.9, 172.29, 6.3)],
            )
            html = build_strategy_dashboard(latest)
            self.assertNotIn("<script>alert(1)</script>", html)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_no_strategy_data_renders_placeholder_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = build_strategy_dashboard(Path(tmp))
            self.assertIn("<html", html)
            self.assertIn("No backtest comparison data yet", html)


class WriteStrategyDashboardTest(unittest.TestCase):
    def test_writes_to_both_latest_and_run_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_folder = Path(tmp) / "testdata" / "hisseler"
            data_folder.mkdir(parents=True)
            latest = Path(tmp) / "testdata" / "reports" / "latest"
            latest.mkdir(parents=True)
            _write_compact_report(latest, [_compact_row("rs_v2_top30", "relative_strength_rotation", 0.9, 172.29, 6.3)])

            result = write_strategy_dashboard(str(data_folder), report_output_dir=str(Path(tmp) / "testdata" / "reports"))

            self.assertTrue((latest / "strategy_dashboard.html").exists())
            self.assertIn("output_path", result)


class ScriptModeImportTest(unittest.TestCase):
    def test_imports_cleanly_in_script_mode_like_the_nightly_cli_does(self):
        completed = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, 'testdata'); import strategy_dashboard"],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
