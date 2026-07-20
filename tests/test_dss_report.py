import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

from testdata.dss_report import (  # noqa: E402
    MAIN_STRATEGY_NAME,
    DSSDataNotReadyError,
    generate_dss_report,
    render_dss_report_text,
)


def _write_manifest(root: Path, target_end_date: str):
    run_path = root / "manifests" / "run1" / "acquisition_manifest.json"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps({"target_end_date": target_end_date, "status": "COMPLETE_VALIDATED"}), encoding="utf-8")
    sha = hashlib.sha256(run_path.read_bytes()).hexdigest()
    pointer = {
        "acquisition_run_id": "run1",
        "immutable_manifest_relative_path": "manifests/run1/acquisition_manifest.json",
        "immutable_manifest_sha256": sha,
    }
    (root / "manifests" / "latest_manifest.json").write_text(json.dumps(pointer), encoding="utf-8")


def _write_signal_csv(root: Path, rows):
    reports_dir = root / "reports" / "latest"
    reports_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(reports_dir / "paper_signal_report.csv", index=False)


def _row(symbol, action, price, notional, rank, score, reason, strategy=MAIN_STRATEGY_NAME):
    return {
        "symbol": symbol,
        "action": action,
        "strategy_name": strategy,
        "entry_price_reference": price,
        "paper_position_notional": notional,
        "paper_capital_assumption": 25000,
        "paper_rank": rank,
        "ranking_score": score,
        "reason": reason,
        "signal_date": "2024-01-05",
    }


class TestDssReportNormalRender(unittest.TestCase):
    def test_normal_report_renders_buy_sell_and_reasons(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_manifest(root, "2024-01-05")
            _write_signal_csv(root, [
                _row("AAA", "BUY", 100.0, 833.33, 1, 1.5, "top_rank_entry"),
                _row("BBB", "SELL", 50.0, 0.0, 40, 0.2, "dropped_out_of_top30"),
                _row("CCC", "WATCH", 20.0, 0.0, 15, 0.9, "watching_only"),
            ])
            with patch("testdata.dss_report.validate_v2_clean_dataset"), \
                 patch("testdata.dss_report._latest_expected_session", return_value="2024-01-05"), \
                 patch("testdata.dss_report.load_v2_frozen_universe",
                       return_value=(["AAA", "BBB", "CCC"], {"universe": {"candidate_universe_hash": "deadbeef"}})):
                report = generate_dss_report(root)

            self.assertEqual(len(report.buy_list), 1)
            self.assertEqual(len(report.sell_list), 1)
            self.assertFalse(report.is_no_change_day)

            text = render_dss_report_text(report)
            self.assertIn("DECISION SUPPORT", text)
            self.assertIn("enter orders manually at next open", text)
            self.assertIn("BUY (1)", text)
            self.assertIn("AAA", text)
            self.assertIn("top_rank_entry", text)
            self.assertIn("SELL (1)", text)
            self.assertIn("BBB", text)
            self.assertIn("dropped_out_of_top30", text)
            self.assertNotIn("CCC", text)  # WATCH rows are neither BUY nor SELL

    def test_no_change_day_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_manifest(root, "2024-01-05")
            _write_signal_csv(root, [_row("AAA", "WATCH", 100.0, 0.0, 5, 1.0, "no_change")])
            with patch("testdata.dss_report.validate_v2_clean_dataset"), \
                 patch("testdata.dss_report._latest_expected_session", return_value="2024-01-05"), \
                 patch("testdata.dss_report.load_v2_frozen_universe",
                       return_value=(["AAA"], {"universe": {"candidate_universe_hash": "deadbeef"}})):
                report = generate_dss_report(root)
            self.assertTrue(report.is_no_change_day)
            text = render_dss_report_text(report)
            self.assertIn("no-change day", text)

    def test_off_universe_symbol_is_excluded_and_flagged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_manifest(root, "2024-01-05")
            _write_signal_csv(root, [
                _row("AAA", "BUY", 100.0, 833.33, 1, 1.5, "top_rank_entry"),
                _row("ZZZ", "BUY", 10.0, 833.33, 2, 1.4, "should_not_exist"),
            ])
            with patch("testdata.dss_report.validate_v2_clean_dataset"), \
                 patch("testdata.dss_report._latest_expected_session", return_value="2024-01-05"), \
                 patch("testdata.dss_report.load_v2_frozen_universe",
                       return_value=(["AAA"], {"universe": {"candidate_universe_hash": "deadbeef"}})):
                report = generate_dss_report(root)
            self.assertEqual([pos.symbol for pos in report.buy_list], ["AAA"])
            self.assertEqual(report.off_universe_dropped, ["ZZZ"])
            text = render_dss_report_text(report)
            self.assertIn("excluded from this report", text)
            self.assertIn("ZZZ", text)

    def test_qqq_and_spy_survive_the_off_universe_filter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_manifest(root, "2024-01-05")
            _write_signal_csv(root, [
                _row("AAA", "BUY", 100.0, 833.33, 1, 1.5, "top_rank_entry"),
                _row("QQQ", "SELL", 400.0, 0.0, None, None, "benchmark_row"),
            ])
            with patch("testdata.dss_report.validate_v2_clean_dataset"), \
                 patch("testdata.dss_report._latest_expected_session", return_value="2024-01-05"), \
                 patch("testdata.dss_report.load_v2_frozen_universe",
                       return_value=(["AAA"], {"universe": {"candidate_universe_hash": "deadbeef"}})):
                report = generate_dss_report(root)
            self.assertEqual(report.off_universe_dropped, [])
            self.assertEqual([pos.symbol for pos in report.sell_list], ["QQQ"])


class TestDssReportFailLoud(unittest.TestCase):
    def test_stale_data_raises_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_manifest(root, "2024-01-02")  # stale vs. expected 2024-01-05
            _write_signal_csv(root, [_row("AAA", "BUY", 100.0, 833.33, 1, 1.5, "top_rank_entry")])
            with patch("testdata.dss_report.validate_v2_clean_dataset"), \
                 patch("testdata.dss_report._latest_expected_session", return_value="2024-01-05"):
                with self.assertRaisesRegex(DSSDataNotReadyError, "stale"):
                    generate_dss_report(root)

    def test_failed_strict_validation_raises_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("testdata.dss_report.validate_v2_clean_dataset", side_effect=ValueError("V2_DATASET_NOT_VALIDATED: boom")):
                with self.assertRaisesRegex(DSSDataNotReadyError, "failed strict validation"):
                    generate_dss_report(root)

    def test_missing_signal_file_raises_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_manifest(root, "2024-01-05")
            with patch("testdata.dss_report.validate_v2_clean_dataset"), \
                 patch("testdata.dss_report._latest_expected_session", return_value="2024-01-05"):
                with self.assertRaisesRegex(DSSDataNotReadyError, "not found"):
                    generate_dss_report(root)

    def test_empty_signal_file_raises_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_manifest(root, "2024-01-05")
            reports_dir = root / "reports" / "latest"
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / "paper_signal_report.csv").write_text(
                "symbol,action,strategy_name,entry_price_reference,paper_position_notional,"
                "paper_capital_assumption,paper_rank,ranking_score,reason,signal_date\n",
                encoding="utf-8",
            )
            with patch("testdata.dss_report.validate_v2_clean_dataset"), \
                 patch("testdata.dss_report._latest_expected_session", return_value="2024-01-05"):
                with self.assertRaisesRegex(DSSDataNotReadyError, "empty"):
                    generate_dss_report(root)

    def test_no_main_strategy_rows_raises_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_manifest(root, "2024-01-05")
            _write_signal_csv(root, [_row("AAA", "BUY", 100.0, 833.33, 1, 1.5, "x", strategy="some_other_strategy")])
            with patch("testdata.dss_report.validate_v2_clean_dataset"), \
                 patch("testdata.dss_report._latest_expected_session", return_value="2024-01-05"):
                with self.assertRaisesRegex(DSSDataNotReadyError, "no rows for main strategy"):
                    generate_dss_report(root)

    def test_missing_columns_raises_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_manifest(root, "2024-01-05")
            reports_dir = root / "reports" / "latest"
            reports_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([{"symbol": "AAA", "action": "BUY"}]).to_csv(reports_dir / "paper_signal_report.csv", index=False)
            with patch("testdata.dss_report.validate_v2_clean_dataset"), \
                 patch("testdata.dss_report._latest_expected_session", return_value="2024-01-05"):
                with self.assertRaisesRegex(DSSDataNotReadyError, "missing required columns"):
                    generate_dss_report(root)

    def test_unparseable_csv_raises_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_manifest(root, "2024-01-05")
            reports_dir = root / "reports" / "latest"
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / "paper_signal_report.csv").write_text('"unterminated,quote\nrow', encoding="utf-8")
            with patch("testdata.dss_report.validate_v2_clean_dataset"), \
                 patch("testdata.dss_report._latest_expected_session", return_value="2024-01-05"):
                with self.assertRaisesRegex(DSSDataNotReadyError, "failed to parse"):
                    generate_dss_report(root)

    def test_unreadable_manifest_pointer_raises_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "manifests").mkdir(parents=True, exist_ok=True)
            (root / "manifests" / "latest_manifest.json").write_text("{not valid json", encoding="utf-8")
            with patch("testdata.dss_report.validate_v2_clean_dataset"):
                with self.assertRaisesRegex(DSSDataNotReadyError, "failed to read validated manifest"):
                    generate_dss_report(root)


if __name__ == "__main__":
    unittest.main()
