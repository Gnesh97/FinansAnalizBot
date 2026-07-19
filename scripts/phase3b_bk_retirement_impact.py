"""Phase 3B -- offline strategy-impact disclosure for BK retirement (178 -> 177).

Read-only: imports and calls the existing, unmodified
_run_relative_strength_rotation_backtest / _relative_strength_selection from
testdata/strategies/relative_strength_rotation.py. Builds asset_data purely
from real, local SIP daily CSVs (no network, no acquisition, no --live),
using the exact same offline pattern as scripts/phase2f_selection_snapshots.py.

Runs the identical canonical strategy over two candidate lists:
  - "178": the archived pre-retirement frozen universe (BK included)
  - "177": the current frozen universe (BK retired)

Reports the actual total-return delta and trade-count delta between the two,
and whether BK ever appears in the 178 run's trade log (i.e. was ever
actually selected/traded), to explain the size of the delta honestly.

Outputs:
    reports/audit/phase3b_bk_retirement_impact.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "testdata"))

from strategies.relative_strength_rotation import _run_relative_strength_rotation_backtest  # noqa: E402

TARGET_START = "2020-07-27"
TARGET_END = "2026-07-07"

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
}


def _load_daily_asset_data(daily_dir: Path, symbols: list[str]) -> dict:
    import pandas as pd

    asset_data = {}
    for symbol in symbols:
        path = daily_dir / f"{symbol}_1D.csv"
        if not path.exists():
            continue
        daily = pd.read_csv(path)
        daily["Date"] = pd.to_datetime(daily["Date"], utc=True).dt.tz_localize(None).dt.normalize()
        daily = daily.set_index("Date").sort_index()
        daily = daily[~daily.index.duplicated()]
        placeholder = pd.DataFrame(index=daily.index)
        placeholder.attrs["daily_ohlcv"] = daily
        asset_data[symbol] = placeholder
    return asset_data


def main() -> None:
    old_baseline_path = REPO_ROOT / "testdata" / "baselines" / "archive" / "rs_m126_top30_sma100_no_regime_v2_clean_stock.178symbols.20260718.json"
    new_baseline_path = REPO_ROOT / "testdata" / "baselines" / "rs_m126_top30_sma100_no_regime_v2_clean_stock.json"
    old_baseline = json.loads(old_baseline_path.read_text(encoding="utf-8"))
    new_baseline = json.loads(new_baseline_path.read_text(encoding="utf-8"))
    symbols_178 = old_baseline["universe"]["candidate_symbols"]
    symbols_177 = new_baseline["universe"]["candidate_symbols"]
    assert len(symbols_178) == 178 and len(symbols_177) == 177 and "BK" in symbols_178 and "BK" not in symbols_177

    sip_dir = REPO_ROOT / "testdata" / "research_data" / "rs_v2_sip_market_data" / "daily"

    asset_data_178 = _load_daily_asset_data(sip_dir, symbols_178)
    asset_data_177 = _load_daily_asset_data(sip_dir, symbols_177)
    print(f"178-universe: loaded {len(asset_data_178)}/{len(symbols_178)} symbols from local daily CSVs")
    print(f"177-universe: loaded {len(asset_data_177)}/{len(symbols_177)} symbols from local daily CSVs")

    metrics_178 = _run_relative_strength_rotation_backtest(asset_data_178, PARAMS)
    metrics_177 = _run_relative_strength_rotation_backtest(asset_data_177, PARAMS)

    trades_178 = metrics_178["_trade_log"]
    trades_177 = metrics_177["_trade_log"]
    bk_trades = [t for t in trades_178 if t["Symbol"] == "BK"]

    total_return_178 = metrics_178["Net Getiri (%)"]
    total_return_177 = metrics_177["Net Getiri (%)"]
    delta = total_return_177 - total_return_178

    lines = []
    lines.append("# Phase 3B -- BK Retirement Strategy-Impact Disclosure (offline, read-only)\n")
    lines.append(f"- Universe A (178, BK included, archived): {len(asset_data_178)} symbols loaded, {len(trades_178)} trades, total_return = {total_return_178:.4f}%")
    lines.append(f"- Universe B (177, BK retired, active): {len(asset_data_177)} symbols loaded, {len(trades_177)} trades, total_return = {total_return_177:.4f}%")
    lines.append(f"- Total return delta (177 minus 178): {delta:.4f} percentage points")
    lines.append(f"- Trade count delta: {len(trades_177) - len(trades_178)}")
    lines.append("")
    if bk_trades:
        lines.append(f"BK WAS selected/traded {len(bk_trades)} time(s) in the 178-universe run:")
        for t in bk_trades:
            lines.append(f"  - Entry {t['Entry Time']} / Exit {t['Exit Time']} / Net PnL {t['Net PnL']} / Exit Reason {t['Exit Reason']}")
    else:
        lines.append("BK was NEVER selected/traded in the 178-universe run over the full backtest window "
                     f"({TARGET_START} to {TARGET_END}) -- zero entries appear in the 178 trade log for symbol BK. "
                     "This is why the return/trade-count delta above is what it is: BK's presence or absence in the "
                     "candidate universe made no realized difference to this specific strategy's historical selections.")
    lines.append("")
    lines.append("Note: individual non-BK trade fills can still shift slightly between the two runs because "
                  "removing a candidate changes the top-30 ranking pool size by one slot on any rebalance date "
                  "where BK would otherwise have ranked in range; the total_return delta above is the authoritative, "
                  "already-net-of-that-effect comparison.")

    out_path = REPO_ROOT / "reports" / "audit" / "phase3b_bk_retirement_impact.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
