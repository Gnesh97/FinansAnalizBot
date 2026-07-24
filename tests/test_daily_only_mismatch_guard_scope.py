"""Scope proofs for the daily-vs-130min new-signal guard (Phase 6C).

The guard blocks new actionable BUY/SELL signals when a dataset's daily and
130-minute feeds disagree on their latest session. Daily-only profiles
(rs_v2_tiingo) carry no intraday directory at all, which made the guard fire
permanently -- an alarm that always fires hides real problems.

These tests pin BOTH directions: the guard must stay silent when there is no
intraday feed to compare against, and must still fire whenever an intraday
feed IS present and disagrees (including present-but-empty).
"""

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "testdata"))

from testdata.runner import _block_new_signal_generation_on_daily_mismatch

MARKER = "new_signal_generation_blocked_by_daily_130min_mismatch"


def _write_bars(directory: Path, name: str, sessions: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame({
        "Date": [f"{session} 04:00:00+00:00" for session in sessions],
        "Open": [10.0] * len(sessions),
        "High": [11.0] * len(sessions),
        "Low": [9.0] * len(sessions),
        "Close": [10.5] * len(sessions),
        "Volume": [100.0] * len(sessions),
    })
    frame.to_csv(directory / name, index=False)


def _paper_report() -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": ["MPC", "FTNT"],
        "strategy_name": ["rs_m126_top30_sma100_no_regime"] * 2,
        "action": ["BUY", "BUY"],
        "is_new_signal": [True, True],
        "paper_actionable": [True, True],
        "fill_status": ["", ""],
        "user_decision": ["", ""],
        "reason": ["latest_entry_signal", "latest_entry_signal"],
    })


class TestDailyOnlyProfileDoesNotFire(unittest.TestCase):
    def test_absent_intraday_dir_leaves_signals_actionable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            daily = root / "daily"
            _write_bars(daily, "MPC_1D.csv", ["2026-07-20", "2026-07-21"])
            missing_intraday = root / "bars_130min"
            self.assertFalse(missing_intraday.exists())

            result = _block_new_signal_generation_on_daily_mismatch(_paper_report(), daily, missing_intraday)

            self.assertTrue(result["paper_actionable"].all())
            self.assertFalse(result["reason"].str.contains(MARKER).any())
            self.assertTrue((result["user_decision"] == "").all())


class TestIntradayPresentStillFires(unittest.TestCase):
    def test_genuine_daily_130min_mismatch_still_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            daily = root / "daily"
            intraday = root / "bars_130min"
            _write_bars(daily, "MPC_1D.csv", ["2026-07-20", "2026-07-21"])
            # Intraday feed one session behind: the exact condition the guard exists for.
            _write_bars(intraday, "MPC_130min.csv", ["2026-07-20"])

            result = _block_new_signal_generation_on_daily_mismatch(_paper_report(), daily, intraday)

            self.assertFalse(result["paper_actionable"].any())
            self.assertTrue(result["reason"].str.contains(MARKER).all())
            self.assertTrue((result["user_decision"] == "HOLD_REVIEW").all())
            self.assertTrue((result["fill_status"] == "NOT_APPLICABLE").all())

    def test_present_but_empty_intraday_dir_still_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            daily = root / "daily"
            intraday = root / "bars_130min"
            _write_bars(daily, "MPC_1D.csv", ["2026-07-21"])
            intraday.mkdir(parents=True)

            result = _block_new_signal_generation_on_daily_mismatch(_paper_report(), daily, intraday)

            self.assertFalse(result["paper_actionable"].any())
            self.assertTrue(result["reason"].str.contains(MARKER).all())

    def test_matching_feeds_do_not_block(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            daily = root / "daily"
            intraday = root / "bars_130min"
            _write_bars(daily, "MPC_1D.csv", ["2026-07-20", "2026-07-21"])
            _write_bars(intraday, "MPC_130min.csv", ["2026-07-20", "2026-07-21"])

            result = _block_new_signal_generation_on_daily_mismatch(_paper_report(), daily, intraday)

            self.assertTrue(result["paper_actionable"].all())
            self.assertFalse(result["reason"].str.contains(MARKER).any())


if __name__ == "__main__":
    unittest.main()
