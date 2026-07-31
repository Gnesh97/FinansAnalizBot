"""Risk Engine, Mode A: advisory-only reporting layer over the paper book.

Reports four metrics per operator decision (see reports/audit/phase10a_*):
ATR/volatility per open position, portfolio heat, a daily-loss-limit
warning, and an advisory kill-switch flag. It never changes position
sizing, signals, or orders -- it only reports. The loss-limit and
kill-switch metrics are computed from the paper book's current
unrealized P&L, not a real broker account, and are labeled as such in
every output field: there is no persisted equity/cash ledger in the
live paper-trading path (only the backtest engine tracks an equity
curve), so a true same-calendar-day delta is not available here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

try:
    from .paper_risk_dashboard import (
        _markdown_table,
        _monitor_sections,
        _number,
        _open_positions_from_trade_log,
        _read_csv,
        _section_rows,
    )
    from .strategies.relative_strength_rotation import _atr_from_ohlc
except ImportError:
    from paper_risk_dashboard import (
        _markdown_table,
        _monitor_sections,
        _number,
        _open_positions_from_trade_log,
        _read_csv,
        _section_rows,
    )
    from strategies.relative_strength_rotation import _atr_from_ohlc

# Advisory-only thresholds; do not touch strategy/ranking logic, so the
# no-parameter-optimization rule for strategy decisions does not apply, but
# the same "no false precision" discipline does. Calibrated in Phase 10B
# (reports/audit/phase10b_*) against the frozen baseline's own history, not
# fit to it: historical portfolio heat under equal weight ranged 1.60%-4.29%
# across all 67 live rebalances (p99 4.26%) -- the original 6.0% literature
# default (Tharp) was loose enough to never fire historically, so it is set
# here to 5.0%, a round number with margin above the observed range rather
# than pinned to the observed max. The loss-limit levels were checked against
# the daily equity-curve return distribution (n=1502 trading days): -3.0%
# sits near the 1st percentile (fires ~1.4% of days, 21/1502) and -5.0% is a
# true tail event (3/1502, ~0.2%) -- both left unchanged, evidence already
# supported them.
ATR_LENGTH = 20
PORTFOLIO_HEAT_WARNING_PCT = 5.0
DAILY_LOSS_LIMIT_WARNING_PCT = -3.0
KILL_SWITCH_LOSS_LIMIT_PCT = -5.0

PAPER_BOOK_LOSS_LIMIT_NOTE = (
    "paper book only: current unrealized P&L on open paper positions right now, "
    "not a same-calendar-day delta, and not verified against any real broker account"
)
PAPER_BOOK_KILL_SWITCH_NOTE = (
    "advisory only, paper book only: does not block orders, submit trades, or change "
    "position sizing -- operator decides"
)


def _open_shares_by_symbol(trade_log: pd.DataFrame) -> dict[str, float]:
    if trade_log.empty:
        return {}
    filled = trade_log[
        trade_log.get("fill_status", pd.Series("", index=trade_log.index)).astype(str).str.upper().eq("PAPER_FILLED")
    ].copy()
    if filled.empty:
        return {}
    side = filled.get("action", pd.Series("", index=filled.index)).astype(str).str.upper().map(
        {"BUY": 1, "SELL": -1}
    ).fillna(0)
    signed_shares = pd.to_numeric(filled.get("shares", 0), errors="coerce").fillna(0.0) * side
    symbols = filled.get("symbol", pd.Series("", index=filled.index)).astype(str).str.upper()
    shares_by_symbol = signed_shares.groupby(symbols).sum()
    return {symbol: float(shares) for symbol, shares in shares_by_symbol.items() if shares > 0}


def _position_risk_row(symbol: str, shares: float, daily_dir: Path) -> dict[str, object]:
    daily = _read_csv(daily_dir / f"{symbol}_1D.csv")
    required_columns = {"High", "Low", "Close"}
    if daily.empty or len(daily) < ATR_LENGTH + 1 or not required_columns.issubset(daily.columns):
        return {
            "symbol": symbol,
            "shares": shares,
            "close": None,
            "atr_pct": None,
            "dollar_atr": None,
            "data_missing": True,
        }
    atr_value = float(_atr_from_ohlc(daily, ATR_LENGTH).iloc[-1])
    close = float(daily["Close"].iloc[-1])
    if pd.isna(atr_value) or close <= 0:
        return {
            "symbol": symbol,
            "shares": shares,
            "close": close,
            "atr_pct": None,
            "dollar_atr": None,
            "data_missing": True,
        }
    return {
        "symbol": symbol,
        "shares": shares,
        "close": close,
        "atr_pct": 100 * atr_value / close,
        "dollar_atr": atr_value,
        "data_missing": False,
    }


def _portfolio_heat(position_risk: list[dict[str, object]], total_open_notional: float) -> dict[str, object]:
    dollar_risk = sum(row["shares"] * row["dollar_atr"] for row in position_risk if not row["data_missing"])
    heat_pct = (dollar_risk / total_open_notional * 100.0) if total_open_notional else 0.0
    return {
        "heat_pct": heat_pct,
        "heat_basis": (
            "pct_of_open_notional (no cash/equity ledger exists in the live paper path; "
            "this is not pct of total account equity)"
        ),
        "heat_warning_threshold_pct": PORTFOLIO_HEAT_WARNING_PCT,
        "heat_warning": heat_pct > PORTFOLIO_HEAT_WARNING_PCT,
        "positions_missing_atr_data": [row["symbol"] for row in position_risk if row["data_missing"]],
    }


def _daily_loss_limit(open_book_unrealized_pnl_pct: float) -> dict[str, object]:
    return {
        "open_book_unrealized_pnl_pct": open_book_unrealized_pnl_pct,
        "warning_threshold_pct": DAILY_LOSS_LIMIT_WARNING_PCT,
        "breached": open_book_unrealized_pnl_pct <= DAILY_LOSS_LIMIT_WARNING_PCT,
        "basis_note": PAPER_BOOK_LOSS_LIMIT_NOTE,
    }


def _kill_switch(open_book_unrealized_pnl_pct: float, heat_pct: float, heat_warning: bool) -> dict[str, object]:
    loss_breach = open_book_unrealized_pnl_pct <= KILL_SWITCH_LOSS_LIMIT_PCT
    triggered = loss_breach or heat_warning
    if loss_breach and heat_warning:
        reason = (
            f"open book unrealized P&L {open_book_unrealized_pnl_pct:.2f}% <= "
            f"kill-switch threshold {KILL_SWITCH_LOSS_LIMIT_PCT}% AND portfolio heat "
            f"{heat_pct:.2f}% > {PORTFOLIO_HEAT_WARNING_PCT}%"
        )
    elif loss_breach:
        reason = (
            f"open book unrealized P&L {open_book_unrealized_pnl_pct:.2f}% <= "
            f"kill-switch threshold {KILL_SWITCH_LOSS_LIMIT_PCT}%"
        )
    elif heat_warning:
        reason = f"portfolio heat {heat_pct:.2f}% > warning threshold {PORTFOLIO_HEAT_WARNING_PCT}%"
    else:
        reason = "not triggered"
    return {
        "advisory_triggered": triggered,
        "reason": reason,
        "loss_limit_threshold_pct": KILL_SWITCH_LOSS_LIMIT_PCT,
        "basis_note": PAPER_BOOK_KILL_SWITCH_NOTE,
    }


def build_risk_engine_report(latest_dir: str | Path, daily_dir: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    daily_dir = Path(daily_dir)
    trade_log = _read_csv(latest / "paper_trade_log.csv")
    portfolio_monitor = _read_csv(latest / "paper_portfolio_monitor.csv")
    open_positions, summary, _risk = _monitor_sections(portfolio_monitor)
    if open_positions.empty:
        open_positions = _open_positions_from_trade_log(trade_log)

    total_open_notional = _number(summary.get("total_open_notional", 0.0))
    open_book_unrealized_pnl_pct = _number(summary.get("total_unrealized_pnl_pct", 0.0))

    shares_by_symbol = _open_shares_by_symbol(trade_log)
    monitor_symbols = set(open_positions.get("symbol", pd.Series(dtype=str)).astype(str).str.upper())
    symbols = sorted(monitor_symbols | set(shares_by_symbol))
    position_risk = [
        _position_risk_row(symbol, shares_by_symbol.get(symbol, 0.0), daily_dir) for symbol in symbols
    ]

    heat = _portfolio_heat(position_risk, total_open_notional)
    loss_limit = _daily_loss_limit(open_book_unrealized_pnl_pct)
    kill_switch = _kill_switch(open_book_unrealized_pnl_pct, heat["heat_pct"], heat["heat_warning"])

    return {
        "position_risk": position_risk,
        "portfolio_heat": heat,
        "daily_loss_limit": loss_limit,
        "kill_switch": kill_switch,
    }


def _position_risk_markdown(position_risk: list[dict[str, object]]) -> str:
    lines = ["| symbol | shares | close | atr_pct | dollar_atr | data_missing |", "| --- | --- | --- | --- | --- | --- |"]
    for row in position_risk:
        lines.append(
            f"| {row['symbol']} | {row['shares']} | {row['close']} | {row['atr_pct']} | "
            f"{row['dollar_atr']} | {row['data_missing']} |"
        )
    return "\n".join(lines)


def write_risk_engine_report(latest_dir: str | Path, daily_dir: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    report = build_risk_engine_report(latest, daily_dir)

    rows = [{"section": "position_risk", **row} for row in report["position_risk"]]
    for section in ["portfolio_heat", "daily_loss_limit", "kill_switch"]:
        rows.extend(_section_rows(section, report[section]))
    csv_path = latest / "risk_engine_report.csv"
    md_path = latest / "risk_engine_report.md"
    json_path = latest / "risk_engine_report.json"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = [
        "# Risk Engine Report (Mode A -- Advisory Only)",
        "",
        "This report is informational. It does not size positions, place orders, or",
        "change signals. Loss-limit and kill-switch figures are paper-book only.",
        "",
        "## Position Risk (ATR / Volatility)",
        "",
        _position_risk_markdown(report["position_risk"]),
        "",
        "## Portfolio Heat",
        "",
        _markdown_table(report["portfolio_heat"]),
        "",
        "## Daily Loss Limit",
        "",
        _markdown_table(report["daily_loss_limit"]),
        "",
        "## Kill Switch (Advisory)",
        "",
        _markdown_table(report["kill_switch"]),
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "report": report,
        "csv_path": str(csv_path),
        "md_path": str(md_path),
        "json_path": str(json_path),
    }
