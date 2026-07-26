"""Pin the invariant that makes _slice_frame's bare-date end bound safe (Phase 7C).

Phase 7A/7B reported that `index <= pd.Timestamp(end_date)` silently drops the
end session, and Phase 7B projected that onto 3 of the 5 OOS walk-forward
periods. Phase 7C measured it on the production loader and the projection was
WRONG: every frame reaching _slice_frame is America/New_York-indexed
(data_engine._to_new_york_index), so a daily bar sits at exactly 00:00 NY --
equal to a bare-date bound and therefore KEPT by `<=`.

The truncation Phase 7A saw is real but belongs to UTC-indexed frames, which is
what an ad-hoc `pd.read_csv` harness produces (bar at 04:00Z/05:00Z vs bound
00:00Z). No production path loads frames that way.

These tests lock both halves of that, so a future change to the index timezone
-- or a switch back to intraday frames -- fails loudly instead of silently
shortening every OOS window by one session. They use synthetic data only, so
they still run on a clean clone with no dataset present.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "testdata"))

from testdata.data_engine import read_ohlcv_csv_fast
from testdata.runner import _oos_walkforward_periods, _slice_frame

MARKET_TZ = "America/New_York"
END_SESSION = "2024-12-31"


def _frame(index):
    return pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 100},
        index=index,
    )


class SliceFrameEndBoundInvariantTest(unittest.TestCase):
    def test_ny_indexed_daily_bar_survives_a_bare_date_end_bound(self):
        # The invariant every production slice relies on.
        index = pd.DatetimeIndex(["2024-12-30", "2024-12-31"]).tz_localize(MARKET_TZ)
        sliced = _slice_frame(_frame(index), None, END_SESSION)
        self.assertEqual(str(sliced.index.max().date()), END_SESSION)
        self.assertEqual(len(sliced), 2)

    def test_utc_indexed_daily_bar_is_dropped_by_a_bare_date_end_bound(self):
        # The failure mode that misled Phase 7A/7B: NY-midnight stamped in UTC
        # is 05:00Z in EST, which is AFTER a 00:00Z bound.
        index = pd.DatetimeIndex(["2024-12-30 05:00:00", "2024-12-31 05:00:00"]).tz_localize("UTC")
        dropped = _slice_frame(_frame(index), None, END_SESSION)
        self.assertEqual(str(dropped.index.max().date()), "2024-12-30")
        kept = _slice_frame(_frame(index), None, f"{END_SESSION} 23:59:59")
        self.assertEqual(str(kept.index.max().date()), END_SESSION)

    def test_intraday_bar_is_dropped_by_a_bare_date_end_bound(self):
        # Why the invariant is daily-only: an intraday bar sits after 00:00 NY.
        # No caller passes legacy_intraday=True today; if one ever does, the OOS
        # windows must gain explicit end-of-day bounds.
        index = pd.DatetimeIndex(["2024-12-31 09:30:00", "2024-12-31 15:30:00"]).tz_localize(MARKET_TZ)
        sliced = _slice_frame(_frame(index), None, END_SESSION)
        self.assertTrue(sliced.empty)

    def test_production_csv_loader_indexes_in_market_timezone(self):
        # Bars are stored as NY-midnight-in-UTC; the loader must convert to NY,
        # which is what places them at exactly 00:00 local.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SYNTH_1D.csv"
            path.write_text(
                "Date,Open,High,Low,Close,Volume\n"
                "2024-12-30 05:00:00+00:00,1,1,1,1,100\n"
                "2024-12-31 05:00:00+00:00,1,1,1,1,100\n"
                "2025-07-21 04:00:00+00:00,1,1,1,1,100\n",
                encoding="utf-8",
            )
            frame = read_ohlcv_csv_fast(path)
        self.assertEqual(str(frame.index.tz), MARKET_TZ)
        self.assertEqual({str(ts.time()) for ts in frame.index}, {"00:00:00"})

    def test_every_oos_period_retains_its_end_session(self):
        # Directly guards the windows Phase 7B claimed were truncated.
        for name, start, end in _oos_walkforward_periods():
            with self.subTest(period=name):
                index = pd.DatetimeIndex([start, end]).tz_localize(MARKET_TZ)
                sliced = _slice_frame(_frame(index), start, end)
                self.assertEqual(str(sliced.index.max().date()), end)
                self.assertEqual(str(sliced.index.min().date()), start)


if __name__ == "__main__":
    unittest.main()
