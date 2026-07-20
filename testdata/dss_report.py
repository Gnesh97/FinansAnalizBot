"""Daily decision-support (DSS) BUY/SELL report generator.

READ-ONLY decision support. This module never places, previews, or prepares
broker/order/live-trading calls -- it only reads the already-validated,
already-computed strategy output the existing pipeline produces
(paper_signal_report.csv, written by universe_scanner.py --paper-report) and
formats it for a human to read and manually enter orders at the next open.

It never recomputes ranking/selection itself. Freshness/validity of the
underlying dataset is checked by re-using the existing, unmodified
runner.validate_v2_clean_dataset() validator -- the same one the rest of the
pipeline already trusts -- plus an explicit "as-of session" recency check
against the approved NYSE calendar.

Fail-loud contract: any problem (missing manifest, failed validation, stale
data, missing/empty signal file, no rows for the main strategy) raises
DSSDataNotReadyError with a human-readable reason. Callers MUST turn that
into an explicit failure notice rather than silently doing nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    from .runner import validate_v2_clean_dataset
    from .v2_session_coverage import approved_nyse_schedule
    from .vericekici_updated import V2_STRATEGY_IDENTIFIER, load_v2_frozen_universe
except ImportError:
    from runner import validate_v2_clean_dataset
    from v2_session_coverage import approved_nyse_schedule
    from vericekici_updated import V2_STRATEGY_IDENTIFIER, load_v2_frozen_universe

MAIN_STRATEGY_NAME = "rs_m126_top30_sma100_no_regime"
FROZEN_TARGET_START = "2020-07-27"

# Read-only decision support: this module never submits, previews, or
# constructs broker/order/live-trading calls. Asserted here, not just in docs.
BROKER_SUBMIT_ENABLED = False
LIVE_TRADING_ENABLED = False
SHORT_TRADING_ENABLED = False


class DSSDataNotReadyError(RuntimeError):
    """Raised when the DSS report cannot be honestly produced today.

    Callers must send an explicit failure notice, never a silent no-send and
    never a blank/placeholder BUY list.
    """


@dataclass
class DSSPosition:
    symbol: str
    action: str
    price: float
    target_weight_pct: float
    rank: int | None
    score: float | None
    reason: str


@dataclass
class DSSReport:
    as_of_data_date: str
    generated_at: str
    universe_count: int
    universe_hash: str
    strategy_id: str
    buy_list: list[DSSPosition] = field(default_factory=list)
    sell_list: list[DSSPosition] = field(default_factory=list)
    off_universe_dropped: list[str] = field(default_factory=list)

    @property
    def is_no_change_day(self) -> bool:
        return not self.buy_list and not self.sell_list


def _latest_expected_session(as_of: datetime | None = None) -> str:
    """Most recent NYSE session that should already be complete, per the
    approved calendar -- the same freshness bar --refresh-daily itself uses.
    """
    today_str = (as_of or datetime.now(timezone.utc)).date().isoformat()
    schedule = approved_nyse_schedule(FROZEN_TARGET_START, today_str)
    return schedule.index[-1].strftime("%Y-%m-%d")


def _assert_dataset_ready(data_folder: Path, as_of: datetime | None = None) -> dict:
    """Fail-loud dataset check: structurally valid (existing validator, not
    re-implemented) AND fresh (target_end_date matches the latest session
    that should already be complete). Returns the immutable manifest dict.
    """
    try:
        validate_v2_clean_dataset(str(data_folder))
    except Exception as exc:
        raise DSSDataNotReadyError(f"dataset failed strict validation: {exc}") from exc

    try:
        latest_pointer_path = data_folder / "manifests" / "latest_manifest.json"
        latest_pointer = json.loads(latest_pointer_path.read_text(encoding="utf-8"))
        immutable_path = data_folder / latest_pointer["immutable_manifest_relative_path"]
        manifest = json.loads(immutable_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DSSDataNotReadyError(f"failed to read validated manifest: {exc}") from exc

    expected_session = _latest_expected_session(as_of)
    actual_end = manifest.get("target_end_date")
    if actual_end != expected_session:
        raise DSSDataNotReadyError(
            f"data is stale: manifest target_end_date={actual_end!r}, "
            f"expected latest completed session={expected_session!r}"
        )
    return manifest


def _load_signal_rows(data_folder: Path) -> pd.DataFrame:
    signal_path = data_folder / "reports" / "latest" / "paper_signal_report.csv"
    if not signal_path.exists():
        raise DSSDataNotReadyError(f"paper_signal_report.csv not found at {signal_path}")
    try:
        df = pd.read_csv(signal_path)
    except Exception as exc:
        raise DSSDataNotReadyError(f"failed to parse paper_signal_report.csv: {exc}") from exc
    if df.empty:
        raise DSSDataNotReadyError("paper_signal_report.csv is empty")
    required_columns = {"symbol", "action", "strategy_name", "entry_price_reference",
                         "paper_position_notional", "paper_capital_assumption",
                         "paper_rank", "ranking_score", "reason", "signal_date"}
    missing = required_columns - set(df.columns)
    if missing:
        raise DSSDataNotReadyError(f"paper_signal_report.csv missing required columns: {sorted(missing)}")
    main = df[df["strategy_name"] == MAIN_STRATEGY_NAME]
    if main.empty:
        raise DSSDataNotReadyError(
            f"no rows for main strategy {MAIN_STRATEGY_NAME!r} in paper_signal_report.csv "
            "(strategy step likely errored or produced no output)"
        )
    return main


def _to_position(row: pd.Series) -> DSSPosition:
    notional = row.get("paper_position_notional")
    capital = row.get("paper_capital_assumption")
    weight_pct = (
        float(notional) / float(capital) * 100.0
        if capital not in (None, 0) and pd.notna(capital) and pd.notna(notional)
        else 0.0
    )
    rank = int(row["paper_rank"]) if pd.notna(row.get("paper_rank")) else None
    score = float(row["ranking_score"]) if pd.notna(row.get("ranking_score")) else None
    return DSSPosition(
        symbol=str(row["symbol"]),
        action=str(row["action"]),
        price=float(row["entry_price_reference"]) if pd.notna(row.get("entry_price_reference")) else 0.0,
        target_weight_pct=round(weight_pct, 3),
        rank=rank,
        score=score,
        reason=str(row.get("reason", "")),
    )


def generate_dss_report(data_folder: str | Path, as_of: datetime | None = None) -> DSSReport:
    """Build today's BUY/SELL decision-support report.

    Raises DSSDataNotReadyError (never returns a partial/blank report) if the
    dataset is not fresh + COMPLETE_VALIDATED, or the signal file is missing,
    empty, malformed, or has no rows for the main strategy.
    """
    data_folder = Path(data_folder)
    manifest = _assert_dataset_ready(data_folder, as_of)
    signal_rows = _load_signal_rows(data_folder)

    try:
        candidates, baseline = load_v2_frozen_universe()
    except Exception as exc:
        raise DSSDataNotReadyError(f"failed to load frozen universe: {exc}") from exc
    frozen_symbols = set(candidates) | {"QQQ", "SPY"}
    off_universe = sorted(set(signal_rows["symbol"]) - frozen_symbols)
    if off_universe:
        signal_rows = signal_rows[signal_rows["symbol"].isin(frozen_symbols)]

    buy_rows = signal_rows[signal_rows["action"] == "BUY"]
    sell_rows = signal_rows[signal_rows["action"] == "SELL"]

    return DSSReport(
        as_of_data_date=manifest.get("target_end_date", ""),
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        universe_count=len(candidates),
        universe_hash=baseline["universe"]["candidate_universe_hash"],
        strategy_id=V2_STRATEGY_IDENTIFIER,
        buy_list=[_to_position(row) for _, row in buy_rows.iterrows()],
        sell_list=[_to_position(row) for _, row in sell_rows.iterrows()],
        off_universe_dropped=off_universe,
    )


def render_dss_report_text(report: DSSReport) -> str:
    lines = []
    lines.append("DECISION SUPPORT -- enter orders manually at next open")
    lines.append(f"As-of data date: {report.as_of_data_date}")
    lines.append(f"Universe: {report.universe_count} symbols (hash {report.universe_hash[:8]}...)")
    lines.append(f"Strategy: {report.strategy_id}")
    lines.append(f"Generated at: {report.generated_at}")
    lines.append("")

    if report.off_universe_dropped:
        lines.append(
            f"NOTE: {len(report.off_universe_dropped)} signal row(s) referenced symbol(s) outside the "
            f"frozen universe and were excluded from this report: {', '.join(report.off_universe_dropped)}"
        )
        lines.append("")

    if report.is_no_change_day:
        lines.append("No BUY/SELL changes today -- no-change day.")
        return "\n".join(lines)

    if report.buy_list:
        lines.append(f"BUY ({len(report.buy_list)}):")
        for pos in report.buy_list:
            lines.append(
                f"  {pos.symbol}: price={pos.price:.2f}, target_weight={pos.target_weight_pct:.2f}%, "
                f"rank={pos.rank}, score={pos.score}, reason={pos.reason}"
            )
        lines.append("")
    else:
        lines.append("BUY: none today.")
        lines.append("")

    if report.sell_list:
        lines.append(f"SELL ({len(report.sell_list)}):")
        for pos in report.sell_list:
            lines.append(
                f"  {pos.symbol}: price={pos.price:.2f}, target_weight={pos.target_weight_pct:.2f}%, "
                f"rank={pos.rank}, score={pos.score}, reason={pos.reason}"
            )
    else:
        lines.append("SELL: none today.")

    return "\n".join(lines)
