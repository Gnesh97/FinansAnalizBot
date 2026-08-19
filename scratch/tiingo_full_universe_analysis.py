"""Phase 5D — Tiingo Full-Universe Analysis (Sandbox, READ-ONLY).

Analyzes data already on disk under testdata/research_data/rs_v2_tiingo_eval_data.
No network calls. No production paths touched (no DSS repointing, no SIP
mutation, no exception authoring). Reuses production strategy functions
(testdata/strategies/relative_strength_rotation.py, testdata/runner.py)
directly against in-memory DataFrames built from CSVs already on disk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "testdata"))

from vericekici_updated import load_v2_frozen_universe  # noqa: E402
from v2_session_coverage import approved_nyse_schedule  # noqa: E402
import runner as runner_module  # noqa: E402

END_DATE = pd.Timestamp("2026-07-17", tz="UTC")
SIP_DAILY_DIR = ROOT / "testdata" / "research_data" / "rs_v2_sip_market_data" / "daily"
TIINGO_DAILY_DIR = ROOT / "testdata" / "research_data" / "rs_v2_tiingo_eval_data" / "daily"
BENCHMARK_SYMBOLS = {"QQQ", "SPY"}


def load_daily_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], utc=True)
    df = df.set_index("Date").sort_index()
    return df


# ---------------------------------------------------------------------------
# 1. Dataset confirmation
# ---------------------------------------------------------------------------

def confirm_dataset(candidates: list[str]) -> dict:
    all_symbols = sorted(set(candidates) | BENCHMARK_SYMBOLS)
    present = []
    missing = []
    windows = {}
    for symbol in all_symbols:
        path = TIINGO_DAILY_DIR / f"{symbol}_1D.csv"
        if not path.exists():
            missing.append(symbol)
            continue
        df = load_daily_csv(path)
        present.append(symbol)
        windows[symbol] = (str(df.index.min().date()), str(df.index.max().date()), len(df))
    latest_dates = [pd.Timestamp(w[1]) for w in windows.values()]
    earliest_starts = [pd.Timestamp(w[0]) for w in windows.values()]
    return {
        "expected_total": len(all_symbols),
        "present_count": len(present),
        "missing": missing,
        "windows": windows,
        "global_earliest_start": str(min(earliest_starts).date()) if earliest_starts else None,
        "global_latest_date": str(max(latest_dates).date()) if latest_dates else None,
        "global_min_latest_date": str(min(latest_dates).date()) if latest_dates else None,
    }


# ---------------------------------------------------------------------------
# 2. Gap analysis (active-span aware)
# ---------------------------------------------------------------------------

def gap_analysis(all_symbols: list[str], daily_dir: Path) -> dict:
    global_start = "2020-07-27"
    global_end = "2026-07-23"
    full_schedule = approved_nyse_schedule(global_start, global_end)
    full_sessions = pd.DatetimeIndex(full_schedule.index).tz_localize(None).normalize()

    per_symbol_gaps = {}
    total_gap_dates = []
    for symbol in all_symbols:
        path = daily_dir / f"{symbol}_1D.csv"
        if not path.exists():
            continue
        df = load_daily_csv(path)
        active_start = df.index.min().tz_localize(None).normalize()
        active_end = df.index.max().tz_localize(None).normalize()
        expected = full_sessions[(full_sessions >= active_start) & (full_sessions <= active_end)]
        actual = pd.DatetimeIndex(df.index.tz_localize(None).normalize().unique())
        missing = expected.difference(actual)
        if len(missing):
            dates = [str(d.date()) for d in sorted(missing)]
            per_symbol_gaps[symbol] = dates
            total_gap_dates.extend((symbol, d) for d in dates)

    return {
        "schedule_window": (global_start, global_end),
        "symbols_checked": len([s for s in all_symbols if (daily_dir / f'{s}_1D.csv').exists()]),
        "total_gap_count": len(total_gap_dates),
        "symbols_affected": len(per_symbol_gaps),
        "per_symbol_gaps": per_symbol_gaps,
    }


# ---------------------------------------------------------------------------
# 3. Strategy comparison
# ---------------------------------------------------------------------------

def build_experiment() -> dict:
    for experiment in runner_module._rs_robustness_experiments():
        if experiment["name"] == "rs_m126_top30_sma100_no_regime":
            return dict(experiment)
    raise RuntimeError("baseline experiment not found")


def run_backtest(daily_dir: Path, candidates: list[str], label: str, benchmark_dir: Path | None = None) -> dict:
    experiment = build_experiment()
    params = runner_module._default_params(experiment, label, "fast", True)

    asset_data = {}
    for symbol in candidates:
        path = daily_dir / f"{symbol}_1D.csv"
        if not path.exists():
            continue
        df = load_daily_csv(path)
        df = df[df.index <= END_DATE]
        if df.empty:
            continue
        asset_data[symbol] = df

    bench_dir = benchmark_dir if benchmark_dir is not None else daily_dir
    bench_path = bench_dir / "QQQ_1D.csv"
    benchmark_data = load_daily_csv(bench_path)
    benchmark_data = benchmark_data[benchmark_data.index <= END_DATE]

    final_data, metrics = runner_module._run_strategy(asset_data, benchmark_data, params)

    from strategies.relative_strength_rotation import _relative_strength_selection, _normalize_relative_strength_params

    strategy_params = _normalize_relative_strength_params(params)
    signaled = {
        symbol: runner_module.generate_strategy_signals(frame, params)
        for symbol, frame in asset_data.items()
    }
    _, selections, _, _ = _relative_strength_selection(signaled, strategy_params, benchmark_data=benchmark_data)
    rebalance_selections = {
        str(date.date()): sorted(symbols) for date, symbols in selections.items() if symbols
    }

    return {
        "label": label,
        "symbols_loaded": len(asset_data),
        "metrics": {
            "Net Getiri (%)": metrics.get("Net Getiri (%)"),
            "CAGR (%)": metrics.get("CAGR (%)"),
            "Max Drawdown (%)": metrics.get("Max Drawdown (%)"),
            "Sharpe Ratio": metrics.get("Sharpe Ratio"),
            "Trade Count": metrics.get("Trade Count"),
            "Bitis Kasa": metrics.get("Bitis Kasa"),
        },
        "rebalance_selections": rebalance_selections,
        "trade_log": metrics.get("_trade_log", []),
    }


def compare_selections(sip_selections: dict, tiingo_selections: dict) -> dict:
    common_dates = sorted(set(sip_selections) & set(tiingo_selections))
    diffs = {}
    for date in common_dates:
        sip_set = set(sip_selections[date])
        tiingo_set = set(tiingo_selections[date])
        if sip_set != tiingo_set:
            diffs[date] = {
                "sip_only": sorted(sip_set - tiingo_set),
                "tiingo_only": sorted(tiingo_set - sip_set),
            }
    return {
        "common_rebalance_dates": len(common_dates),
        "dates_with_differences": len(diffs),
        "diffs": diffs,
    }


# ---------------------------------------------------------------------------
# Price variance
# ---------------------------------------------------------------------------

def price_variance(candidates: list[str]) -> dict:
    per_symbol_stats = {}
    all_diffs_bps = []
    flagged = []
    for symbol in candidates:
        sip_path = SIP_DAILY_DIR / f"{symbol}_1D.csv"
        tiingo_path = TIINGO_DAILY_DIR / f"{symbol}_1D.csv"
        if not sip_path.exists() or not tiingo_path.exists():
            continue
        sip_df = load_daily_csv(sip_path)[["Close"]].rename(columns={"Close": "sip_close"})
        sip_df.index = sip_df.index.tz_localize(None).normalize()
        tiingo_df = load_daily_csv(tiingo_path)[["Close"]].rename(columns={"Close": "tiingo_close"})
        tiingo_df.index = tiingo_df.index.tz_localize(None).normalize()
        joined = sip_df.join(tiingo_df, how="inner").dropna()
        if joined.empty:
            continue
        rel_diff_bps = ((joined["tiingo_close"] - joined["sip_close"]) / joined["sip_close"]) * 1e4
        median_bps = float(rel_diff_bps.abs().median())
        mean_bps = float(rel_diff_bps.abs().mean())
        max_bps = float(rel_diff_bps.abs().max())
        per_symbol_stats[symbol] = {
            "n_common_days": int(len(joined)),
            "median_abs_bps": round(median_bps, 4),
            "mean_abs_bps": round(mean_bps, 4),
            "max_abs_bps": round(max_bps, 4),
        }
        all_diffs_bps.extend(rel_diff_bps.abs().tolist())
        # Flag systematic divergence: median > 200bps signals persistent mispricing (split/adj issue)
        if median_bps > 200.0:
            flagged.append({"symbol": symbol, "median_abs_bps": round(median_bps, 4), "max_abs_bps": round(max_bps, 4)})

    arr = np.array(all_diffs_bps)
    overall = {
        "common_sessions_total": int(len(arr)),
        "median_bps": round(float(np.median(arr)), 4) if len(arr) else None,
        "mean_bps": round(float(np.mean(arr)), 4) if len(arr) else None,
        "p95_bps": round(float(np.percentile(arr, 95)), 4) if len(arr) else None,
    }
    return {"overall": overall, "per_symbol": per_symbol_stats, "flagged_systematic_divergence": flagged}


def main():
    candidates, _ = load_v2_frozen_universe()
    candidates = sorted(candidates)
    all_symbols = sorted(set(candidates) | BENCHMARK_SYMBOLS)

    result = {}
    result["dataset_confirmation"] = confirm_dataset(candidates)
    result["gap_analysis"] = gap_analysis(all_symbols, TIINGO_DAILY_DIR)

    sip_benchmark_dir = SIP_DAILY_DIR.parent / "benchmarks" / "daily"
    sip_run = run_backtest(SIP_DAILY_DIR, candidates, "SIP_V2_DAILY", benchmark_dir=sip_benchmark_dir)
    tiingo_run = run_backtest(TIINGO_DAILY_DIR, candidates, "TIINGO_EVAL_DAILY")
    result["sip_backtest"] = {k: v for k, v in sip_run.items() if k != "trade_log"}
    result["tiingo_backtest"] = {k: v for k, v in tiingo_run.items() if k != "trade_log"}
    result["sip_trade_count_full"] = len(sip_run["trade_log"])
    result["tiingo_trade_count_full"] = len(tiingo_run["trade_log"])

    result["selection_comparison"] = compare_selections(
        sip_run["rebalance_selections"], tiingo_run["rebalance_selections"]
    )
    result["price_variance"] = price_variance(candidates)

    out_path = ROOT / "scratch" / "phase5d_analysis_output.json"
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(json.dumps({
        "dataset_present": result["dataset_confirmation"]["present_count"],
        "gap_total": result["gap_analysis"]["total_gap_count"],
        "sip_return": result["sip_backtest"]["metrics"]["Net Getiri (%)"],
        "tiingo_return": result["tiingo_backtest"]["metrics"]["Net Getiri (%)"],
        "sip_trades": result["sip_trade_count_full"],
        "tiingo_trades": result["tiingo_trade_count_full"],
        "selection_diffs": result["selection_comparison"]["dates_with_differences"],
        "price_variance_median_bps": result["price_variance"]["overall"]["median_bps"],
        "flagged_symbols": result["price_variance"]["flagged_systematic_divergence"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
