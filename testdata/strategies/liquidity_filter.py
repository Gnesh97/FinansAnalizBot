import math

import pandas as pd


ADV_FIELD = "Signal_Avg_Dollar_Volume"
FALLBACK_ADV_FIELD = "Avg_Dollar_Volume"


def _finite_float(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def evaluate_entry_liquidity(symbol, signal_date, signal_row, min_dollar_volume):
    threshold = _finite_float(min_dollar_volume) or 0.0
    if threshold <= 0:
        return {
            "passed": True,
            "symbol": symbol,
            "signal_date": signal_date,
            "adv": None,
            "threshold": threshold,
            "field": ADV_FIELD,
            "reason": "disabled",
        }

    field = ADV_FIELD
    adv = None
    if signal_row is not None:
        if hasattr(signal_row, "get"):
            adv = signal_row.get(ADV_FIELD, pd.NA)
            if pd.isna(adv) and FALLBACK_ADV_FIELD in signal_row:
                field = FALLBACK_ADV_FIELD
                adv = signal_row.get(FALLBACK_ADV_FIELD, pd.NA)
        else:
            adv = pd.NA

    numeric_adv = _finite_float(adv)
    if numeric_adv is None:
        reason = "missing_dollar_volume"
    elif numeric_adv <= 0:
        reason = "zero_dollar_volume"
    elif numeric_adv < threshold:
        reason = "below_min_dollar_volume"
    else:
        return {
            "passed": True,
            "symbol": symbol,
            "signal_date": signal_date,
            "adv": numeric_adv,
            "threshold": threshold,
            "field": field,
            "reason": "passed",
        }
    return {
        "passed": False,
        "symbol": symbol,
        "signal_date": signal_date,
        "adv": numeric_adv,
        "threshold": threshold,
        "field": field,
        "reason": reason,
    }


def liquidity_skip_summary(skipped_entries, min_dollar_volume):
    threshold = _finite_float(min_dollar_volume) or 0.0
    skipped_entries = list(skipped_entries or [])
    symbols = sorted({str(entry.get("symbol", "")) for entry in skipped_entries if entry.get("symbol")})
    reasons = sorted({str(entry.get("reason", "")) for entry in skipped_entries if entry.get("reason")})
    return {
        "Dynamic Liquidity Filter Enabled": bool(threshold > 0),
        "Dynamic Liquidity Min Dollar Volume": float(threshold),
        "Dynamic Liquidity ADV Field": ADV_FIELD,
        "Dynamic Liquidity ADV Fallback Field": FALLBACK_ADV_FIELD,
        "Liquidity Skipped Entry Count": int(len(skipped_entries)),
        "Liquidity Skipped Symbols": ",".join(symbols),
        "Liquidity Skipped Reasons": ",".join(reasons),
    }
