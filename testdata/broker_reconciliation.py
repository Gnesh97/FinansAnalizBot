from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path

import pandas as pd

try:
    from .paper_state_manager import net_open_positions_from_trade_log
except ImportError:
    from paper_state_manager import net_open_positions_from_trade_log


PENDING_STATUSES = {"PENDING", "PENDING_NEXT_OPEN", "WAIT_FOR_NEXT_OPEN"}
REPORT_COLUMNS = ["section", "check_name", "local_value", "broker_value", "status", "reason"]


@dataclass(frozen=True)
class ReadOnlyBrokerSnapshot:
    positions: list[dict[str, object]] = field(default_factory=list)
    open_orders: list[dict[str, object]] = field(default_factory=list)
    fills: list[dict[str, object]] | None = None
    source: str = "MOCK"
    live_trading_enabled: bool = False
    live_endpoint: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _number(value, default=0.0) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(parsed) if pd.notna(parsed) else float(default)


def _strict_number(value) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed) or not math.isfinite(float(parsed)):
        raise ValueError("numeric value required")
    return float(parsed)


def _status_series(frame: pd.DataFrame) -> pd.Series:
    return frame.get("status", frame.get("fill_status", pd.Series("", index=frame.index))).fillna("").astype(str).str.upper()


def _decision_series(frame: pd.DataFrame) -> pd.Series:
    return frame.get("user_decision", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper()


def _action_series(frame: pd.DataFrame) -> pd.Series:
    return frame.get("action", frame.get("side", pd.Series("", index=frame.index))).fillna("").astype(str).str.upper()


def _local_positions(trade_log: pd.DataFrame) -> pd.DataFrame:
    open_positions = net_open_positions_from_trade_log(trade_log)
    if open_positions.empty:
        return pd.DataFrame(columns=["symbol", "qty", "avg_price"])
    rows = []
    filled = trade_log[
        trade_log.get("fill_status", pd.Series("", index=trade_log.index)).fillna("").astype(str).str.upper().eq("PAPER_FILLED")
    ].copy() if not trade_log.empty else pd.DataFrame()
    for _, row in open_positions.iterrows():
        symbol = _clean_text(row.get("symbol", "")).upper()
        strategy = _clean_text(row.get("strategy_name", ""))
        qty = _number(row.get("net_shares", 0.0))
        buys = filled[
            filled.get("symbol", pd.Series("", index=filled.index)).fillna("").astype(str).str.upper().eq(symbol)
            & filled.get("strategy_name", pd.Series("", index=filled.index)).fillna("").astype(str).eq(strategy)
            & filled.get("action", pd.Series("", index=filled.index)).fillna("").astype(str).str.upper().eq("BUY")
        ]
        buy_qty = pd.to_numeric(buys.get("shares", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        buy_price = pd.to_numeric(buys.get("assumed_fill_price", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        total_qty = float(buy_qty.sum())
        avg_price = float((buy_qty * buy_price).sum() / total_qty) if total_qty else 0.0
        rows.append({"symbol": symbol, "qty": qty, "avg_price": avg_price})
    return pd.DataFrame(rows)


def _local_orders(pending_orders: pd.DataFrame) -> pd.DataFrame:
    if pending_orders.empty:
        return pd.DataFrame(columns=["symbol", "side", "qty"])
    accepted = pending_orders[
        _decision_series(pending_orders).eq("ACCEPT")
        & _status_series(pending_orders).isin(PENDING_STATUSES)
        & _action_series(pending_orders).isin(["BUY", "SELL"])
    ].copy()
    if accepted.empty:
        return pd.DataFrame(columns=["symbol", "side", "qty"])
    return pd.DataFrame(
        {
            "symbol": accepted.get("symbol", pd.Series("", index=accepted.index)).fillna("").astype(str).str.upper(),
            "side": _action_series(accepted),
            "qty": pd.to_numeric(accepted.get("shares", accepted.get("estimated_qty", pd.Series(0.0, index=accepted.index))), errors="coerce").fillna(0.0),
        }
    )


def _broker_positions(snapshot: ReadOnlyBrokerSnapshot) -> pd.DataFrame:
    rows = []
    for row in snapshot.positions:
        rows.append(
            {
                "symbol": _clean_text(row.get("symbol", "")).upper(),
                "qty": _number(row.get("qty", row.get("shares", 0.0))),
                "avg_price": _number(row.get("avg_price", row.get("avg_entry_price", 0.0))),
            }
        )
    return pd.DataFrame(rows, columns=["symbol", "qty", "avg_price"])


def _broker_orders(snapshot: ReadOnlyBrokerSnapshot) -> pd.DataFrame:
    rows = []
    for row in snapshot.open_orders:
        rows.append(
            {
                "symbol": _clean_text(row.get("symbol", "")).upper(),
                "side": _clean_text(row.get("side", row.get("action", ""))).upper(),
                "qty": _number(row.get("qty", row.get("estimated_qty", 0.0))),
            }
        )
    return pd.DataFrame(rows, columns=["symbol", "side", "qty"])


def _order_keys(frame: pd.DataFrame) -> list[tuple[str, str]]:
    if frame.empty:
        return []
    return [(str(row.get("symbol", "")).upper(), str(row.get("side", "")).upper()) for _, row in frame.iterrows()]


def _duplicate_count(keys: list[tuple[str, str]]) -> int:
    return len(keys) - len(set(keys))


def _check(section: str, name: str, local_value, broker_value, status: str, reason: str) -> dict[str, object]:
    return {
        "section": section,
        "check_name": name,
        "local_value": local_value,
        "broker_value": broker_value,
        "status": status,
        "reason": reason,
    }


def _symbols(frame: pd.DataFrame) -> set[str]:
    if frame.empty or "symbol" not in frame:
        return set()
    return {item for item in frame["symbol"].fillna("").astype(str).str.upper().tolist() if item}


def _qty_map(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        return {}
    return {str(row.get("symbol", "")).upper(): _number(row.get("qty", 0.0)) for _, row in frame.iterrows()}


def _avg_map(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        return {}
    return {str(row.get("symbol", "")).upper(): _number(row.get("avg_price", 0.0)) for _, row in frame.iterrows()}


FORWARD_OPEN_STATUSES = {"SUBMITTED", "ACCEPTED/NEW", "PENDING_CANCEL", "PENDING_REPLACE", "PARTIALLY_FILLED"}
FORWARD_FILL_STATUSES = {"PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED", "REJECTED"}


def _forward_baseline(latest: Path) -> tuple[dict[str, float] | None, str]:
    path = latest / "broker_cutover_baseline.json"
    if not path.exists():
        return None, "BASELINE_MISSING"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "BASELINE_MALFORMED"
    if not isinstance(payload, dict) or payload.get("forward_only") is not True or not isinstance(payload.get("broker_baseline_positions"), list):
        return None, "BASELINE_MALFORMED"
    positions: dict[str, float] = {}
    try:
        for row in payload["broker_baseline_positions"]:
            if not isinstance(row, dict):
                raise ValueError
            symbol = _clean_text(row.get("symbol", "")).upper()
            if not symbol:
                raise ValueError
            positions[symbol] = _strict_number(row.get("qty", 0.0))
    except (TypeError, ValueError):
        return None, "BASELINE_MALFORMED"
    return positions, "PASS"


def _strict_broker_snapshot(snapshot: ReadOnlyBrokerSnapshot) -> tuple[dict[str, float] | None, list[dict[str, object]] | None, str]:
    positions: dict[str, float] = {}
    orders: list[dict[str, object]] = []
    try:
        for row in snapshot.positions:
            if not isinstance(row, dict):
                raise ValueError
            symbol = _clean_text(row.get("symbol", "")).upper()
            if not symbol:
                raise ValueError
            qty = _strict_number(row.get("qty", row.get("shares", 0.0)))
            positions[symbol] = qty
        for row in snapshot.open_orders:
            if not isinstance(row, dict):
                raise ValueError
            client_id = _clean_text(row.get("client_order_id", ""))
            broker_id = _clean_text(row.get("id", row.get("broker_order_id", "")))
            if not client_id and not broker_id:
                raise ValueError
            orders.append({"client_order_id": client_id, "broker_order_id": broker_id, "status": _clean_text(row.get("status", "")).upper()})
    except (TypeError, ValueError):
        return None, None, "BROKER_SNAPSHOT_MALFORMED"
    return positions, orders, "PASS"


def build_forward_broker_reconciliation(latest_dir: str | Path, broker_snapshot: ReadOnlyBrokerSnapshot | None = None, *, tolerance: float = 1e-6) -> dict[str, object]:
    """Pure forward-only reconciliation: baseline position plus durable-ledger delta."""
    latest = Path(latest_dir)
    checks: list[dict[str, object]] = []
    baseline, baseline_status = _forward_baseline(latest)
    if baseline is None:
        checks.append(_check("forward_baseline", "baseline_available", "valid forward baseline", baseline_status, "WARNING", baseline_status))
        return _forward_result(checks, 0, 0)
    if broker_snapshot is None:
        checks.append(_check("broker_snapshot", "broker_snapshot_available", "snapshot", "none", "WARNING", "BROKER_SNAPSHOT_NOT_AVAILABLE"))
        return _forward_result(checks, 0, 0)
    positions, broker_orders, snapshot_status = _strict_broker_snapshot(broker_snapshot)
    if positions is None or broker_orders is None:
        checks.append(_check("broker_snapshot", "broker_snapshot_valid", "valid", snapshot_status, "WARNING", snapshot_status))
        return _forward_result(checks, 0, 0)
    try:
        from .broker_ledger import BrokerLedgerError, load_broker_ledger
    except ImportError:
        from broker_ledger import BrokerLedgerError, load_broker_ledger
    try:
        ledger = load_broker_ledger(latest)
    except BrokerLedgerError as error:
        checks.append(_check("durable_ledger", "ledger_valid", "valid", "invalid", "WARNING", str(error)))
        return _forward_result(checks, 0, 0)

    delta: dict[str, float] = {}
    ledger_open: list[dict[str, object]] = []
    for _, row in ledger.iterrows():
        status = _clean_text(row.get("status", "")).upper()
        symbol = _clean_text(row.get("symbol", "")).upper()
        filled = _number(row.get("filled_qty", 0.0))
        if status in FORWARD_FILL_STATUSES and filled > 0:
            delta[symbol] = delta.get(symbol, 0.0) + (filled if _clean_text(row.get("side", "")).lower() == "buy" else -filled)
        if status in FORWARD_OPEN_STATUSES:
            ledger_open.append({"client_order_id": _clean_text(row.get("client_order_id", "")), "broker_order_id": _clean_text(row.get("broker_order_id", "")), "status": status})
    broker_client = {order["client_order_id"] for order in broker_orders if order["client_order_id"]}
    broker_ids = {order["broker_order_id"] for order in broker_orders if order["broker_order_id"]}
    ledger_only = [row for row in ledger_open if row["client_order_id"] not in broker_client and row["broker_order_id"] not in broker_ids]
    ledger_client = {row["client_order_id"] for row in ledger_open if row["client_order_id"]}
    ledger_ids = {row["broker_order_id"] for row in ledger_open if row["broker_order_id"]}
    broker_only = [row for row in broker_orders if row["client_order_id"] not in ledger_client and row["broker_order_id"] not in ledger_ids]
    checks.append(_check("forward_orders", "ledger_only_order_count", 0, len(ledger_only), "FAIL" if ledger_only else "PASS", "identifier_match_required"))
    checks.append(_check("forward_orders", "broker_only_order_count", 0, len(broker_only), "FAIL" if broker_only else "PASS", "identifier_match_required"))
    expected_symbols = set(baseline) | set(delta) | set(positions)
    for symbol in sorted(expected_symbols):
        expected = baseline.get(symbol, 0.0) + delta.get(symbol, 0.0)
        actual = positions.get(symbol, 0.0)
        checks.append(_check("forward_positions", f"position_{symbol}", expected, actual, "PASS" if abs(expected - actual) <= tolerance else "FAIL", "baseline_plus_signed_forward_delta"))
    return _forward_result(checks, len(ledger_only), len(broker_only))


def _forward_result(checks: list[dict[str, object]], ledger_only_count: int, broker_only_count: int) -> dict[str, object]:
    statuses = [str(check["status"]).upper() for check in checks]
    status = "FAIL" if "FAIL" in statuses else ("WARNING" if "WARNING" in statuses else "PASS")
    return {"summary": {"reconciliation_status": status, "ledger_only_order_count": ledger_only_count, "broker_only_order_count": broker_only_count, "state_mutated": False, "read_only_mode": True, "forward_only_excludes_pre_cutover_local_positions": True}, "checks": pd.DataFrame(checks, columns=REPORT_COLUMNS)}


def build_broker_reconciliation(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None = None,
) -> dict[str, object]:
    latest = Path(latest_dir)
    trade_log = _read_csv(latest / "paper_trade_log.csv")
    pending_orders = _read_csv(latest / "paper_pending_orders.csv")
    local_positions = _local_positions(trade_log)
    local_orders = _local_orders(pending_orders)
    checks: list[dict[str, object]] = []

    snapshot_source = "NONE"
    if broker_snapshot is None:
        checks.append(_check("database", "broker_snapshot_available", "local_state", "none", "WARNING", "BROKER_SNAPSHOT_NOT_AVAILABLE"))
        checks.extend(_safety_checks())
        summary = _summary(checks, snapshot_source)
        return {
            "summary": summary,
            "checks": pd.DataFrame(checks, columns=REPORT_COLUMNS),
            "local_positions": local_positions,
            "local_orders": local_orders,
        }

    snapshot_source = broker_snapshot.source
    broker_positions = _broker_positions(broker_snapshot)
    broker_orders = _broker_orders(broker_snapshot)
    checks.extend(_snapshot_metadata_checks(broker_snapshot.metadata))

    if broker_snapshot.live_endpoint or broker_snapshot.live_trading_enabled:
        checks.append(_check("safety", "broker_snapshot_live_mode", False, True, "FAIL", "live_endpoint_or_live_trading_enabled"))
    else:
        checks.append(_check("safety", "broker_snapshot_live_mode", False, False, "PASS", "read_only_paper_or_mock_snapshot"))

    metadata_status = str(broker_snapshot.metadata.get("alpaca_snapshot_status", "")).upper() if broker_snapshot.metadata else ""
    if metadata_status and metadata_status != "PASS":
        checks.extend(_broker_snapshot_unavailable_checks(metadata_status))
        checks.extend(_safety_checks())
        checks_frame = pd.DataFrame(checks, columns=REPORT_COLUMNS)
        summary = _summary(checks, snapshot_source, broker_snapshot.metadata)
        return {
            "summary": summary,
            "checks": checks_frame,
            "local_positions": local_positions,
            "local_orders": local_orders,
            "broker_positions": broker_positions,
            "broker_orders": broker_orders,
        }

    local_syms = _symbols(local_positions)
    broker_syms = _symbols(broker_positions)
    missing_broker = sorted(local_syms - broker_syms)
    extra_broker = sorted(broker_syms - local_syms)
    local_qty = _qty_map(local_positions)
    broker_qty = _qty_map(broker_positions)
    qty_mismatch = [symbol for symbol in sorted(local_syms & broker_syms) if abs(local_qty.get(symbol, 0.0) - broker_qty.get(symbol, 0.0)) > 1e-6]
    local_avg = _avg_map(local_positions)
    broker_avg = _avg_map(broker_positions)
    avg_mismatch = [
        symbol
        for symbol in sorted(local_syms & broker_syms)
        if broker_avg.get(symbol, 0.0) and abs(local_avg.get(symbol, 0.0) - broker_avg.get(symbol, 0.0)) > 1e-4
    ]
    position_fail = bool(missing_broker or extra_broker or qty_mismatch or avg_mismatch)
    position_status_reason = "positions_match"
    if position_fail:
        position_status_reason = (
            "position_mismatch_pre_cutover_possible_local_only_history"
            if missing_broker and not broker_syms
            else "position_mismatch"
        )
    checks.extend(
        [
            _check("position_reconciliation", "local_open_position_count", len(local_syms), len(broker_syms), "PASS" if len(local_syms) == len(broker_syms) else "FAIL", "position_count"),
            _check("position_reconciliation", "symbols_in_local_not_broker", ",".join(missing_broker) or "none", "none", "PASS" if not missing_broker else "FAIL", "missing_broker_positions"),
            _check("position_reconciliation", "symbols_in_broker_not_local", "none", ",".join(extra_broker) or "none", "PASS" if not extra_broker else "FAIL", "extra_broker_positions"),
            _check("position_reconciliation", "share_qty_mismatch_count", len(qty_mismatch), ",".join(qty_mismatch) or "none", "PASS" if not qty_mismatch else "FAIL", "share_quantity"),
            _check("position_reconciliation", "avg_price_mismatch_count", len(avg_mismatch), ",".join(avg_mismatch) or "none", "PASS" if not avg_mismatch else "FAIL", "average_price"),
            _check("position_reconciliation", "position_reconciliation_status", "local", "broker", "FAIL" if position_fail else "PASS", position_status_reason),
        ]
    )

    local_order_keys = _order_keys(local_orders)
    broker_order_keys = _order_keys(broker_orders)
    missing_orders = sorted(set(local_order_keys) - set(broker_order_keys))
    extra_orders = sorted(set(broker_order_keys) - set(local_order_keys))
    duplicate_local = _duplicate_count(local_order_keys)
    duplicate_broker = _duplicate_count(broker_order_keys)
    local_order_qty = {key: _number(row.get("qty", 0.0)) for key, (_, row) in zip(local_order_keys, local_orders.iterrows())}
    broker_order_qty = {key: _number(row.get("qty", 0.0)) for key, (_, row) in zip(broker_order_keys, broker_orders.iterrows())}
    qty_order_mismatch = [
        key for key in sorted(set(local_order_keys) & set(broker_order_keys)) if broker_order_qty.get(key, 0.0) and abs(local_order_qty.get(key, 0.0) - broker_order_qty.get(key, 0.0)) > 1e-6
    ]
    order_fail = bool(missing_orders or extra_orders or duplicate_local or duplicate_broker or qty_order_mismatch)
    checks.extend(
        [
            _check("open_order_reconciliation", "local_accepted_pending_count", len(local_order_keys), len(broker_order_keys), "PASS" if len(local_order_keys) == len(broker_order_keys) else "FAIL", "open_order_count"),
            _check("open_order_reconciliation", "local_pending_not_broker", len(missing_orders), ";".join(f"{a}|{b}" for a, b in missing_orders) or "none", "PASS" if not missing_orders else "FAIL", "missing_broker_open_orders"),
            _check("open_order_reconciliation", "broker_order_not_local", len(extra_orders), ";".join(f"{a}|{b}" for a, b in extra_orders) or "none", "PASS" if not extra_orders else "FAIL", "extra_broker_open_orders"),
            _check("open_order_reconciliation", "duplicate_broker_order_count", duplicate_broker, 0, "PASS" if duplicate_broker == 0 else "FAIL", "duplicate_broker_orders"),
            _check("open_order_reconciliation", "duplicate_local_order_count", duplicate_local, 0, "PASS" if duplicate_local == 0 else "FAIL", "duplicate_local_orders"),
            _check("open_order_reconciliation", "order_side_mismatch_count", 0, 0, "PASS", "side_included_in_order_key"),
            _check("open_order_reconciliation", "order_qty_mismatch_count", len(qty_order_mismatch), 0, "PASS" if not qty_order_mismatch else "FAIL", "order_quantity"),
            _check("open_order_reconciliation", "open_order_reconciliation_status", "local", "broker", "FAIL" if order_fail else "PASS", "order_mismatch" if order_fail else "open_orders_match"),
        ]
    )

    if broker_snapshot.fills is None:
        checks.append(_check("fill_reconciliation", "broker_fills_without_local_trade_count", "not_available", "not_available", "WARNING", "BROKER_FILLS_NOT_AVAILABLE"))
        checks.append(_check("fill_reconciliation", "local_trades_without_broker_fill_count", "not_available", "not_available", "WARNING", "BROKER_FILLS_NOT_AVAILABLE"))
    else:
        local_filled_count = _filled_count(trade_log)
        checks.append(_check("fill_reconciliation", "broker_fills_without_local_trade_count", 0, len(broker_snapshot.fills), "WARNING" if broker_snapshot.fills else "PASS", "fill_reconciliation_placeholder"))
        checks.append(_check("fill_reconciliation", "local_trades_without_broker_fill_count", local_filled_count, "not_compared", "WARNING" if local_filled_count else "PASS", "fill_reconciliation_placeholder"))

    recon_forward = build_forward_broker_reconciliation(latest, broker_snapshot)

    checks.extend(_safety_checks())
    checks_frame = pd.DataFrame(checks, columns=REPORT_COLUMNS)
    summary = _summary(checks, snapshot_source, broker_snapshot.metadata)
    summary["forward_only_reconciliation_status"] = recon_forward["summary"]["reconciliation_status"]
    return {
        "summary": summary,
        "checks": checks_frame,
        "local_positions": local_positions,
        "local_orders": local_orders,
        "broker_positions": broker_positions,
        "broker_orders": broker_orders,
    }


def _filled_count(trade_log: pd.DataFrame) -> int:
    if trade_log.empty:
        return 0
    return int(trade_log.get("fill_status", pd.Series("", index=trade_log.index)).fillna("").astype(str).str.upper().eq("PAPER_FILLED").sum())


def _safety_checks() -> list[dict[str, object]]:
    return [
        _check("safety", "broker_submit_enabled", False, False, "PASS", "disabled"),
        _check("safety", "live_trading_enabled", False, False, "PASS", "disabled"),
        _check("safety", "short_trading_enabled", False, False, "PASS", "disabled"),
        _check("safety", "submit_order_called", False, False, "PASS", "not_called"),
        _check("safety", "post_request_used", False, False, "PASS", "not_used"),
        _check("safety", "would_submit_count", 0, 0, "PASS", "read_only_mode"),
        _check("safety", "read_only_mode", True, True, "PASS", "read_only_mode"),
    ]


def _broker_snapshot_unavailable_checks(status: str) -> list[dict[str, object]]:
    unavailable = status or "NOT_AVAILABLE"
    return [
        _check("position_reconciliation", "position_reconciliation_status", "local", unavailable, "WARNING", "BROKER_SNAPSHOT_NOT_AVAILABLE"),
        _check("open_order_reconciliation", "open_order_reconciliation_status", "local", unavailable, "WARNING", "BROKER_SNAPSHOT_NOT_AVAILABLE"),
        _check("fill_reconciliation", "broker_fills_without_local_trade_count", "not_available", "not_available", "WARNING", "BROKER_FILLS_NOT_AVAILABLE"),
        _check("fill_reconciliation", "local_trades_without_broker_fill_count", "not_available", "not_available", "WARNING", "BROKER_FILLS_NOT_AVAILABLE"),
    ]


def _snapshot_metadata_checks(metadata: dict[str, object] | None) -> list[dict[str, object]]:
    if not metadata:
        return []
    status = str(metadata.get("alpaca_snapshot_status", "")).upper()
    check_status = status if status in {"PASS", "WARNING", "FAIL"} else "WARNING"
    return [
        _check("alpaca_snapshot", "alpaca_snapshot_status", "adapter", status or "unknown", check_status, metadata.get("alpaca_snapshot_reason", "")),
        _check("alpaca_snapshot", "alpaca_account_read_success", True, bool(metadata.get("alpaca_account_read_success", False)), "PASS" if metadata.get("alpaca_account_read_success") else "WARNING", "account_get"),
        _check("alpaca_snapshot", "alpaca_positions_read_success", True, bool(metadata.get("alpaca_positions_read_success", False)), "PASS" if metadata.get("alpaca_positions_read_success") else "WARNING", "positions_get"),
        _check("alpaca_snapshot", "alpaca_open_orders_read_success", True, bool(metadata.get("alpaca_open_orders_read_success", False)), "PASS" if metadata.get("alpaca_open_orders_read_success") else "WARNING", "open_orders_get"),
        _check("alpaca_snapshot", "alpaca_endpoint_mode", "paper", metadata.get("alpaca_endpoint_mode", "unknown"), "PASS" if metadata.get("alpaca_endpoint_mode") == "paper" else "FAIL", "paper_endpoint_required"),
        _check("alpaca_snapshot", "alpaca_live_endpoint_blocked", False, bool(metadata.get("alpaca_live_endpoint_blocked", False)), "PASS" if not metadata.get("alpaca_live_endpoint_blocked", False) else "FAIL", "live_endpoint_block"),
        _check("alpaca_snapshot", "request_methods_used", "GET", metadata.get("request_methods_used", "none"), "PASS" if str(metadata.get("request_methods_used", "none")) in {"GET", "none"} else "FAIL", "get_only"),
        _check("alpaca_snapshot", "alpaca_failure_category", "none", metadata.get("alpaca_failure_category", "") or "none", "PASS" if not metadata.get("alpaca_failure_category") else "WARNING", "sanitized_failure_category"),
        _check("alpaca_snapshot", "alpaca_failure_endpoint", "none", metadata.get("alpaca_failure_endpoint", "") or "none", "PASS" if not metadata.get("alpaca_failure_endpoint") else "WARNING", "sanitized_endpoint"),
        _check("alpaca_snapshot", "alpaca_sdk_import_status", "NOT_REQUIRED", metadata.get("alpaca_sdk_import_status", "NOT_REQUIRED"), "PASS", "urllib_get_only_no_sdk_import"),
    ]


def _summary(checks: list[dict[str, object]], snapshot_source: str, snapshot_metadata: dict[str, object] | None = None) -> dict[str, object]:
    frame = pd.DataFrame(checks)
    statuses = frame.get("status", pd.Series(dtype=str)).astype(str).str.upper()
    fail_count = int(statuses.eq("FAIL").sum())
    warning_count = int(statuses.eq("WARNING").sum())

    def section_fail(section: str) -> int:
        subset = frame[frame["section"].astype(str).eq(section)] if not frame.empty else pd.DataFrame()
        return int(subset["status"].astype(str).str.upper().eq("FAIL").sum()) if not subset.empty else 0

    summary = {
        "reconciliation_status": "FAIL" if fail_count else ("WARNING" if warning_count else "PASS"),
        "broker_snapshot_source": snapshot_source,
        "local_source_of_truth": "CSV",
        "position_mismatch_count": section_fail("position_reconciliation"),
        "order_mismatch_count": section_fail("open_order_reconciliation"),
        "duplicate_order_count": int(
            pd.to_numeric(
                frame[frame.get("check_name", pd.Series(dtype=str)).astype(str).str.contains("duplicate", case=False, na=False)].get("local_value", pd.Series(dtype=float)),
                errors="coerce",
            ).fillna(0).sum()
        )
        if not frame.empty
        else 0,
        "fill_mismatch_count": section_fail("fill_reconciliation"),
        "state_mutated": False,
        "broker_submit_enabled": False,
        "live_trading_enabled": False,
        "short_trading_enabled": False,
        "submit_order_called": False,
        "post_request_used": False,
        "would_submit_count": 0,
        "read_only_mode": True,
        "alpaca_snapshot_status": "NOT_REQUESTED",
        "alpaca_snapshot_reason": "",
        "alpaca_failure_category": "",
        "alpaca_failure_endpoint": "",
        "alpaca_failure_detail": "",
        "alpaca_sdk_import_status": "NOT_REQUIRED",
        "alpaca_account_read_success": False,
        "alpaca_positions_read_success": False,
        "alpaca_open_orders_read_success": False,
        "alpaca_endpoint_mode": "unknown",
        "alpaca_live_endpoint_blocked": False,
        "request_methods_used": "none",
    }
    if snapshot_metadata:
        for key in [
            "alpaca_snapshot_status",
            "alpaca_snapshot_reason",
            "alpaca_failure_category",
            "alpaca_failure_endpoint",
            "alpaca_failure_detail",
            "alpaca_sdk_import_status",
            "alpaca_account_read_success",
            "alpaca_positions_read_success",
            "alpaca_open_orders_read_success",
            "alpaca_endpoint_mode",
            "alpaca_live_endpoint_blocked",
            "request_methods_used",
        ]:
            if key in snapshot_metadata:
                summary[key] = snapshot_metadata[key]
    return summary


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    return json.loads(frame.fillna("").to_json(orient="records"))


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "none"
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(_clean_text(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def _metric_table(values: dict[str, object]) -> str:
    return _markdown_table(pd.DataFrame([{"metric": key, "value": value} for key, value in values.items()]))


def write_broker_reconciliation(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None = None,
) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)

    result = build_broker_reconciliation(latest, broker_snapshot)
    csv_path = latest / "broker_reconciliation.csv"
    md_path = latest / "broker_reconciliation.md"
    json_path = latest / "broker_reconciliation.json"
    result["checks"].to_csv(csv_path, index=False)
    payload = {
        "summary": result["summary"],
        "checks": _records(result["checks"]),
        "local_positions": _records(result["local_positions"]),
        "local_orders": _records(result["local_orders"]),
        "broker_positions": _records(result.get("broker_positions", pd.DataFrame())),
        "broker_orders": _records(result.get("broker_orders", pd.DataFrame())),
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Broker Reconciliation",
                "",
                "## Summary",
                "",
                _metric_table(result["summary"]),
                "",
                "## Checks",
                "",
                _markdown_table(result["checks"]),
                "",
                "## Local Positions",
                "",
                _markdown_table(result["local_positions"]),
                "",
                "## Local Orders",
                "",
                _markdown_table(result["local_orders"]),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "summary": result["summary"],
        "csv_path": str(csv_path),
        "md_path": str(md_path),
        "json_path": str(json_path),
    }
