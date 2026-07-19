"""Phase 2F Task A -- full-ranking selection snapshots for hisseler vs SIP.

Read-only: imports and calls the existing, unmodified
_relative_strength_daily_features / _relative_strength_selection from
testdata/strategies/relative_strength_rotation.py. Builds asset_data purely
from daily CSVs (the ranking engine never reads intraday data -- confirmed by
code trace in reports/audit/sip_residual_impact_20260716.md), for both the
hisseler and SIP datasets, over the identical canonical experiment params.

For every one of the 73 monthly rebalance dates, writes the FULL eligible
ranking (not just top-30) to CSV: date, symbol, rank, score, momentum,
volatility, sma100_pass, eligible.

Outputs:
    reports/audit/phase2f_selection_snapshot_hisseler.csv
    reports/audit/phase2f_selection_snapshot_sip.csv
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "testdata"))

from strategies.relative_strength_rotation import _relative_strength_selection  # noqa: E402
from vericekici_updated import load_v2_frozen_universe  # noqa: E402
from v2_coverage_forensics import _monthly_rebalance_dates  # noqa: E402
from v2_session_coverage import approved_nyse_schedule  # noqa: E402

PARAMS = {
    "strategy_name": "relative_strength_rotation",
    "rs_lookback_days": 126,
    "rs_top_n": 30,
    "rs_rebalance_frequency": "monthly",
    "rs_score_mode": "return_over_vol",
    "rs_vol_lookback_days": 60,
    "rs_trend_sma_days": 100,
    "rs_use_market_regime": False,
    "rs_market_regime_sma_days": 200,
    "rs_min_price": 10,
    "rs_max_atr_pct": 12,
    # deliberately NOT setting strategy_identifier to the v2_clean value --
    # that only gates a blocked-ETF assertion irrelevant here since the
    # candidate list already excludes all sector/benchmark ETFs.
}

TARGET_START = "2020-07-27"
TARGET_END = "2026-07-07"


def _load_daily_asset_data(daily_dir: Path, symbols: list[str]) -> dict[str, pd.DataFrame]:
    asset_data = {}
    for symbol in symbols:
        path = daily_dir / f"{symbol}_1D.csv"
        if not path.exists():
            continue
        daily = pd.read_csv(path)
        # Normalize to tz-naive midnight dates, matching the convention used by
        # approved_nyse_schedule()/_monthly_rebalance_dates() (session-date keyed,
        # not exact intraday timestamp) -- raw daily bars are timestamped 04:00 UTC.
        daily["Date"] = pd.to_datetime(daily["Date"], utc=True).dt.tz_localize(None).dt.normalize()
        daily = daily.set_index("Date").sort_index()
        daily = daily[~daily.index.duplicated()]
        # Minimal placeholder frame: the ranking engine reads .attrs["daily_ohlcv"]
        # exclusively (see _daily_ohlcv_from_frame) and never touches the frame's
        # own columns/index beyond confirming it's a DatetimeIndex -- so no
        # intraday data is loaded or needed for this read-only ranking analysis.
        placeholder = pd.DataFrame(index=daily.index)
        placeholder.attrs["daily_ohlcv"] = daily
        asset_data[symbol] = placeholder
    return asset_data


def _recompute_reporting_fields(daily: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Read-only duplication of the two intermediate values _relative_strength_daily_features
    computes internally but does not return (total_return, annualized_vol), purely for
    reporting -- identical formula, not a modification of the source function."""
    total_return = daily["Close"] / daily["Close"].shift(params["rs_lookback_days"]) - 1
    annualized_vol = daily["Close"].pct_change(fill_method=None).rolling(
        params["rs_vol_lookback_days"], min_periods=params["rs_vol_lookback_days"]
    ).std() * np.sqrt(252)
    return pd.DataFrame({"momentum": total_return, "volatility": annualized_vol}, index=daily.index)


def build_snapshot(daily_dir: Path, symbols: list[str], rebalance_dates: list[pd.Timestamp], label: str) -> pd.DataFrame:
    asset_data = _load_daily_asset_data(daily_dir, symbols)
    features, selections, regime, rebalance = _relative_strength_selection(asset_data, PARAMS)

    reporting = {
        symbol: _recompute_reporting_fields(asset_data[symbol].attrs["daily_ohlcv"], PARAMS)
        for symbol in asset_data
    }

    rows = []
    for date in rebalance_dates:
        date_ts = pd.Timestamp(date).tz_localize(None).normalize() if pd.Timestamp(date).tzinfo else pd.Timestamp(date).normalize()
        candidates = []
        for symbol, frame in features.items():
            if date_ts not in frame.index:
                continue
            frow = frame.loc[date_ts]
            score = frow.get("RS_Score")
            eligible = bool(frow.get("RS_Eligible", False))
            momentum = reporting[symbol].loc[date_ts, "momentum"] if date_ts in reporting[symbol].index else np.nan
            volatility = reporting[symbol].loc[date_ts, "volatility"] if date_ts in reporting[symbol].index else np.nan
            sma100_pass = bool(frow.get("Close") > frow.get("RS_Trend_SMA")) if pd.notna(frow.get("RS_Trend_SMA")) else False
            candidates.append({
                "date": date_ts.date().isoformat(),
                "symbol": symbol,
                "score": float(score) if pd.notna(score) else np.nan,
                "momentum": float(momentum) if pd.notna(momentum) else np.nan,
                "volatility": float(volatility) if pd.notna(volatility) else np.nan,
                "sma100_pass": sma100_pass,
                "eligible": eligible,
            })
        candidates.sort(key=lambda r: (r["score"] if pd.notna(r["score"]) else -np.inf), reverse=True)
        for rank, row in enumerate(candidates, start=1):
            row["rank"] = rank
            row["selected_top30"] = row["eligible"] and rank <= PARAMS["rs_top_n"]
            rows.append(row)

    out = pd.DataFrame(rows, columns=["date", "symbol", "rank", "score", "momentum", "volatility", "sma100_pass", "eligible", "selected_top30"])
    out_path = REPO_ROOT / "reports" / "audit" / f"phase2f_selection_snapshot_{label}.csv"
    out.to_csv(out_path, index=False)
    print(f"{label}: wrote {len(out)} rows across {len(rebalance_dates)} rebalance dates to {out_path}")
    return out


def main() -> None:
    candidates, _ = load_v2_frozen_universe()
    schedule = approved_nyse_schedule(TARGET_START, TARGET_END)
    rebalance_dates = _monthly_rebalance_dates(schedule.index)
    print(f"Rebalance date count: {len(rebalance_dates)}")

    sip_dir = REPO_ROOT / "testdata" / "research_data" / "rs_v2_sip_market_data" / "daily"
    hisseler_dir = REPO_ROOT / "testdata" / "hisseler" / "daily"

    build_snapshot(hisseler_dir, candidates, rebalance_dates, "hisseler")
    build_snapshot(sip_dir, candidates, rebalance_dates, "sip")


if __name__ == "__main__":
    main()
