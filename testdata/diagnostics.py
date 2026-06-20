import pandas as pd
import numpy as np


PORTFOLIO_REJECTION_COLUMNS = [
    "Rejected Already In Position",
    "Rejected Same Bar Exit",
    "Rejected Missing Open",
    "Rejected Missing Signal Close",
    "Rejected Missing Stop",
    "Rejected Missing ATR",
    "Rejected Missing Target Distance",
    "Rejected Missing Ranking Score",
    "Rejected Risk Off",
    "Rejected Max Positions",
    "Rejected Invalid Risk Per Share",
    "Rejected Insufficient Cash",
    "Rejected Zero Shares",
]


def _validate_asset_signal_columns(asset_data):
    required_columns = [
        "Quality_Filter_OK",
        "Raw_Entry_Signal",
        "Confirmed_Entry_Signal",
        "ENTRY_SIGNAL",
    ]
    for symbol, data in asset_data.items():
        missing = [column for column in required_columns if column not in data]
        if missing:
            raise ValueError(
                f"{symbol} icin sinyal kolonu eksik: {', '.join(missing)}"
            )
        for column in required_columns:
            if data[column].isna().any():
                raise ValueError(f"{symbol} icin {column} NaN iceriyor.")
            if not pd.api.types.is_bool_dtype(data[column]):
                data[column] = data[column].fillna(False).astype(bool)


def _asset_signal_diagnostics(asset_data):
    if not isinstance(asset_data, dict):
        asset_data = {}
    symbols_loaded = len(asset_data)
    total_bars = 0
    indicators_ready_count = 0
    raw_trigger_count = 0
    raw_entry_signal_count = 0
    quality_filter_ok_count = 0
    confirmed_entry_signal_count = 0
    hurst_filter_ok_count = 0
    final_entry_signal_count = 0
    risk_off_rejected_signal_count = 0
    entry_context_filter_ok_count = 0
    entry_context_rejected_signal_count = 0
    for data in asset_data.values():
        total_bars += len(data)
        indicators_ready = data.get(
            "Indicators_Ready",
            pd.Series(False, index=data.index),
        ).fillna(False).astype(bool)
        raw_trigger = data.get("Trigger", pd.Series(False, index=data.index))
        quality_ok = data.get("Quality_Filter_OK", pd.Series(False, index=data.index))
        raw_signal = data.get("Raw_Entry_Signal", pd.Series(False, index=data.index))
        confirmed_signal = data.get(
            "Confirmed_Entry_Signal",
            pd.Series(False, index=data.index),
        )
        hurst_ok = data.get("Hurst_Filter_OK", pd.Series(False, index=data.index))
        risk_off = data.get("Risk_Off", pd.Series(True, index=data.index))
        context_ok = data.get(
            "Entry_Context_Filter_OK",
            pd.Series(True, index=data.index),
        )
        final_signal = data.get("ENTRY_SIGNAL", pd.Series(False, index=data.index))
        raw_trigger = raw_trigger.fillna(False).astype(bool)
        quality_ok = quality_ok.fillna(False).astype(bool)
        raw_signal = raw_signal.fillna(False).astype(bool)
        confirmed_signal = confirmed_signal.fillna(False).astype(bool)
        hurst_ok = hurst_ok.fillna(False).astype(bool)
        risk_off = risk_off.astype("boolean").fillna(True).astype(bool)
        context_ok = context_ok.fillna(False).astype(bool)
        final_signal = final_signal.fillna(False).astype(bool)
        post_hurst_signal = confirmed_signal & hurst_ok

        indicators_ready_count += int(indicators_ready.sum())
        raw_trigger_count += int(raw_trigger.sum())
        raw_entry_signal_count += int(raw_signal.sum())
        quality_filter_ok_count += int(quality_ok.sum())
        confirmed_entry_signal_count += int(confirmed_signal.sum())
        hurst_filter_ok_count += int(post_hurst_signal.sum())
        final_entry_signal_count += int(final_signal.sum())
        risk_off_rejected_signal_count += int((post_hurst_signal & risk_off).sum())
        entry_context_filter_ok_count += int(context_ok.sum())
        entry_context_rejected_signal_count += int(
            (post_hurst_signal & (~risk_off) & (~context_ok)).sum()
        )
    return {
        "Symbols Loaded": symbols_loaded,
        "Total Bars": total_bars,
        "Indicators Ready Count": indicators_ready_count,
        "Raw Trigger Count": raw_trigger_count,
        "Raw Entry Signal Count": raw_entry_signal_count,
        "Quality Filter OK Count": quality_filter_ok_count,
        "Confirmed Entry Signal Count": confirmed_entry_signal_count,
        "Hurst Filter OK Count": hurst_filter_ok_count,
        "Final Entry Signal Count": final_entry_signal_count,
        "Risk Off Rejected Signal Count": risk_off_rejected_signal_count,
        "Entry Context Filter OK Count": entry_context_filter_ok_count,
        "Entry Context Rejected Signal Count": entry_context_rejected_signal_count,
        "symbols_loaded": symbols_loaded,
        "quality_filter_active_symbols": sum(
            1
            for data in asset_data.values()
            if data.get(
                "Quality_Filter_OK",
                pd.Series(False, index=data.index),
            ).fillna(False).astype(bool).any()
        ),
        "total_raw_entry_signals": raw_entry_signal_count,
        "total_confirmed_entry_signals": confirmed_entry_signal_count,
        "total_final_entry_signals": final_entry_signal_count,
    }


def _zero_trade_metrics(**extra):
    metrics = {
        "Baslangic Kasa": 10000.0,
        "Bitis Kasa": 10000.0,
        "Net Getiri (%)": 0.0,
        "CAGR (%)": 0.0,
        "Max Drawdown (%)": 0.0,
        "Sharpe Ratio": 0.0,
        "Islem Sayisi": 0,
        "Trade Count": 0,
        "Win Rate (%)": 0.0,
        "Profit Factor": 0.0,
        "Avg R": 0.0,
        "Median R": 0.0,
        "Best R": 0.0,
        "Worst R": 0.0,
        "Avg Winner R": 0.0,
        "Avg Loser R": 0.0,
        "Expectancy R": 0.0,
        "Max Consecutive Losses": 0,
        "Max Consecutive Wins": 0,
        "Stop Exit Count": 0,
        "Take Profit Exit Count": 0,
        "SMA20 Reversion Exit Count": 0,
        "Time Stop Exit Count": 0,
        "Risk Off Exit Count": 0,
        "Forced Final Exit Count": 0,
        "Max Acik Pozisyon": 0,
        "Max Portfolio Heat (%)": 0.0,
        "Avg Holding Bars": 0.0,
        "Median Holding Bars": 0.0,
        "Turnover Estimate": 0.0,
        "Avg Positions Held": 0.0,
        "Max Positions Held": 0,
        "Reddedilen Sinyal": 0,
        "Makro Rejimde Reddedilen Sinyal": 0,
    }
    metrics.update({column: 0 for column in PORTFOLIO_REJECTION_COLUMNS})
    metrics.update(extra)
    return metrics


def _calendar_report_summary(asset_data):
    return {
        "calendar_fallback": any(
            bool(data.attrs.get("calendar_fallback", False))
            for data in asset_data.values()
        ),
        "Partial Session Bar SayÄ±sÄ±": sum(
            int(
                data.get(
                    "Is_Partial_Session_Bar",
                    pd.Series(False, index=data.index),
                ).fillna(False).sum()
            )
            for data in asset_data.values()
        ),
    }


__all__ = [
    "PORTFOLIO_REJECTION_COLUMNS",
    "_asset_signal_diagnostics",
    "_calendar_report_summary",
    "_validate_asset_signal_columns",
    "_zero_trade_metrics",
]

# Symbol attribution helper.
def _symbol_attribution_rows(experiment_name, trade_log):
    trades = pd.DataFrame(trade_log)
    if trades.empty or "Symbol" not in trades:
        return []

    rows = []
    for symbol, group in trades.groupby("Symbol", sort=True):
        pnl = pd.to_numeric(
            group.get("Net PnL", pd.Series(dtype=float)),
            errors="coerce",
        ).dropna()
        r_multiples = pd.to_numeric(
            group.get("R Multiple", pd.Series(dtype=float)),
            errors="coerce",
        ).dropna()
        winners = pnl[pnl > 0]
        losers = pnl[pnl < 0]
        trade_count = len(group)
        win_rate = len(winners) / len(pnl) * 100 if len(pnl) else 0.0
        profit_factor = (
            winners.sum() / abs(losers.sum())
            if len(losers) and abs(losers.sum()) > 0
            else float("inf") if trade_count else 0.0
        )
        exit_reason_counts = (
            group["Exit Reason"].value_counts()
            if "Exit Reason" in group
            else pd.Series(dtype=int)
        )
        rows.append(
            {
                "Experiment Name": experiment_name,
                "Symbol": symbol,
                "Trade Count": int(trade_count),
                "Net PnL": round(float(pnl.sum()), 2) if len(pnl) else 0.0,
                "Avg R": round(float(r_multiples.mean()), 4)
                if len(r_multiples)
                else 0.0,
                "Median R": round(float(r_multiples.median()), 4)
                if len(r_multiples)
                else 0.0,
                "Best R": round(float(r_multiples.max()), 4)
                if len(r_multiples)
                else 0.0,
                "Worst R": round(float(r_multiples.min()), 4)
                if len(r_multiples)
                else 0.0,
                "Win Rate": round(win_rate, 1),
                "Profit Factor": round(float(profit_factor), 2)
                if np.isfinite(profit_factor)
                else float("inf"),
                "Stop Exit Count": int(exit_reason_counts.get("disaster_stop", 0)),
                "Take Profit Exit Count": int(
                    exit_reason_counts.get("take_profit", 0)
                ),
                "SMA20 Reversion Exit Count": int(
                    exit_reason_counts.get("sma20_reversion", 0)
                ),
                "Time Stop Exit Count": int(exit_reason_counts.get("time_stop", 0)),
                "Forced Final Exit Count": int(
                    exit_reason_counts.get("forced_final_exit", 0)
                ),
            }
        )
    return rows


def _trade_context_rows(experiment_name, trade_log, report_columns):
    trades = pd.DataFrame(trade_log)
    if trades.empty:
        return []

    rows = []
    for _, trade in trades.iterrows():
        row = {"Experiment Name": experiment_name}
        for column in report_columns:
            if column == "Experiment Name":
                continue
            row[column] = trade.get(column, np.nan)
        rows.append(row)
    return rows




def build_signal_diagnostics_table(ranked):
    signal_columns = [
        "Experiment Name",
        "Strategy Name",
        "Strategy Family",
        "Raw Trigger Count",
        "Raw Entry Signal Count",
        "Confirmed Entry Signal Count",
        "Hurst Filter OK Count",
        "Final Entry Signal Count",
        "Trade Count",
        "Risk Off Rejected Signal Count",
        "Islem Sayisi",
    ]
    return ranked[[column for column in signal_columns if column in ranked]].head(10)

def build_portfolio_rejection_table(ranked):
    rejection_columns = [
        "Experiment Name",
        "Final Entry Signal Count",
        "Islem Sayisi",
        "Rejected Risk Off",
        "Rejected Max Positions",
        "Rejected Already In Position",
        "Rejected Same Bar Exit",
        "Rejected Missing Open",
        "Rejected Missing Ranking Score",
        "Rejected Invalid Risk Per Share",
        "Rejected Insufficient Cash",
        "Rejected Zero Shares",
    ]
    return ranked[[column for column in rejection_columns if column in ranked]].head(10)

__all__.extend([
    "_symbol_attribution_rows",
    "_trade_context_rows",
    "build_signal_diagnostics_table",
    "build_portfolio_rejection_table",
])
