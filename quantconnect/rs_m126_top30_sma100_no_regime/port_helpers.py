"""Pure local parity helpers for the frozen QuantConnect port contract."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable

import pandas as pd


STATIC_PARITY = "STATIC_PARITY"
PIT_VALIDATION = "PIT_VALIDATION"
EXCLUDED_BENCHMARKS = {"QQQ", "SPY"}


def static_universe_hash(symbols: Iterable[str]) -> str:
    canonical = sorted({str(symbol).strip().upper() for symbol in symbols})
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_static_universe(symbols: Iterable[str]) -> list[str]:
    return [symbol for symbol in sorted({str(item).strip().upper() for item in symbols}) if symbol]


def compute_return_over_vol(close: pd.Series, momentum_lookback_days: int, volatility_lookback_days: int) -> dict[str, pd.Series]:
    numeric = pd.to_numeric(close, errors="coerce")
    momentum = numeric / numeric.shift(momentum_lookback_days) - 1
    annualized_volatility = numeric.pct_change(fill_method=None).rolling(volatility_lookback_days, min_periods=volatility_lookback_days).std() * (252**0.5)
    score = momentum / annualized_volatility.replace(0, float("nan"))
    return {"momentum": momentum, "annualized_volatility": annualized_volatility, "score": score}


def rank_symbols(scores: dict[str, float], top_n: int) -> list[str]:
    eligible = [(str(symbol).upper(), float(score)) for symbol, score in scores.items() if pd.notna(score)]
    return [symbol for symbol, _ in sorted(eligible, key=lambda item: (-item[1], item[0]))[:top_n]]


def ensure_long_only_targets(selected: Iterable[str] | dict[str, float]) -> dict[str, float]:
    if isinstance(selected, dict):
        if any(float(weight) < 0 for weight in selected.values()):
            raise ValueError("long-only targets cannot be negative")
        return {str(symbol).upper(): float(weight) for symbol, weight in selected.items()}
    symbols = list(selected)
    return {str(symbol).upper(): 1 / len(symbols) for symbol in symbols} if symbols else {}


def validate_universe_mode(universe_mode: str, static_symbols: Iterable[str] | None = None) -> str:
    mode = str(universe_mode).upper()
    if mode not in {STATIC_PARITY, PIT_VALIDATION}:
        raise ValueError("universe_mode must be STATIC_PARITY or PIT_VALIDATION")
    if mode == PIT_VALIDATION and static_symbols:
        raise ValueError("PIT_VALIDATION cannot use a static current constituent list")
    return mode


def can_publish_executable(contract_fields: Iterable[dict[str, str]]) -> bool:
    return not any(field.get("blocker_scope") in {"STATIC_BLOCKING", "BOTH"} for field in contract_fields)
