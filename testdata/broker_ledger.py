from __future__ import annotations

import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
import pandas as pd

try:
    from .broker_reconciliation import ReadOnlyBrokerSnapshot
except ImportError:
    from broker_reconciliation import ReadOnlyBrokerSnapshot

LEDGER_COLUMNS = [
    "client_order_id",
    "broker_order_id",
    "symbol",
    "side",
    "notional",
    "qty",
    "status",
    "readback_status",
    "broker_position_match",
    "filled_qty",
]

VALID_SIDES = {"buy", "sell"}
VALID_STATUSES = {
    "SUBMITTED",
    "SUBMITTED_READBACK_OK",
    "SUBMITTED_READBACK_WARNING",
    "UNKNOWN_RESULT_READBACK_OK",
    "UNKNOWN_RESULT_READBACK_FAILED",
    "ACCEPTED/NEW",
    "NEW",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "EXPIRED",
    "REJECTED",
    "SUSPENDED",
    "CALCULATED",
    "DONE_FOR_DAY",
    "PENDING_CANCEL",
    "PENDING_REPLACE",
    "REPLACED",
    "STOPPED",
}
CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


class BrokerLedgerError(RuntimeError):
    """Blocking durable-ledger storage or validation error."""


class BrokerLedgerLockError(BrokerLedgerError):
    """A concurrent process owns the durable-ledger lock."""

def _ledger_paths(latest_dir: str | Path) -> tuple[Path, Path]:
    latest = Path(latest_dir)
    state_dir = None
    curr = latest.resolve()
    for _ in range(4):
        if curr.name == "reports":
            state_dir = curr.parent / "state"
            break
        curr = curr.parent

    if state_dir is None:
        state_dir = latest / "state"

    return state_dir / "broker_order_ledger.csv", latest / "broker_order_ledger.csv"


def _empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=LEDGER_COLUMNS)


def _clean_text(value, field: str) -> str:
    if pd.isna(value):
        raise BrokerLedgerError(f"invalid durable ledger: {field} is empty")
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        raise BrokerLedgerError(f"invalid durable ledger: {field} is empty")
    return text


def _number(value, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BrokerLedgerError(f"invalid durable ledger: {field} is not numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise BrokerLedgerError(f"invalid durable ledger: {field} must be finite and non-negative")
    return result


def _validate_ledger_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise BrokerLedgerError("invalid durable ledger: expected DataFrame")
    missing = [column for column in LEDGER_COLUMNS if column not in frame.columns]
    extra = [column for column in frame.columns if column not in LEDGER_COLUMNS]
    if missing or extra:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("unexpected=" + ",".join(extra))
        raise BrokerLedgerError("invalid durable ledger schema: " + "; ".join(details))

    normalized = frame.loc[:, LEDGER_COLUMNS].copy()
    for index, row in normalized.iterrows():
        client_order_id = _clean_text(row["client_order_id"], "client_order_id")
        if not CLIENT_ORDER_ID_PATTERN.fullmatch(client_order_id):
            raise BrokerLedgerError("invalid durable ledger: client_order_id is not a canonical deterministic token")
        normalized.at[index, "client_order_id"] = client_order_id
        normalized.at[index, "broker_order_id"] = _clean_text(row["broker_order_id"], "broker_order_id")
        symbol = _clean_text(row["symbol"], "symbol").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol):
            raise BrokerLedgerError("invalid durable ledger: symbol is invalid")
        normalized.at[index, "symbol"] = symbol
        side = _clean_text(row["side"], "side").lower()
        if side not in VALID_SIDES:
            raise BrokerLedgerError("invalid durable ledger: side is not recognized")
        normalized.at[index, "side"] = side
        status = _clean_text(row["status"], "status").upper()
        if status not in VALID_STATUSES and not status.startswith("UNKNOWN:"):
            raise BrokerLedgerError("invalid durable ledger: status is not recognized")
        normalized.at[index, "status"] = status
        normalized.at[index, "readback_status"] = _clean_text(row["readback_status"], "readback_status")
        for column in ("notional", "qty", "filled_qty"):
            normalized.at[index, column] = _number(row[column], column)
        match = _clean_text(row["broker_position_match"], "broker_position_match")
        if match.lower() not in {"true", "false"} and not match.upper().startswith(("WARNING:", "FAIL")):
            raise BrokerLedgerError("invalid durable ledger: broker_position_match is invalid")

    if normalized["client_order_id"].duplicated().any():
        raise BrokerLedgerError("invalid durable ledger: duplicate client_order_id")
    return normalized


def _read_csv_validated(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, dtype=object, keep_default_na=False)
    except Exception as exc:
        raise BrokerLedgerError(f"invalid durable ledger: cannot read {path.name}") from exc
    return _validate_ledger_frame(frame)


@contextmanager
def _ledger_lock(durable_path: Path):
    durable_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = durable_path.with_name(durable_path.name + ".lock")
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise BrokerLedgerLockError(_lock_failure_message(lock_path)) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(f"pid={os.getpid()}\n")
            lock_file.flush()
            os.fsync(lock_file.fileno())
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _lock_failure_message(lock_path: Path) -> str:
    try:
        lock_text = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"durable ledger lock is held: {lock_path.name}; operator inspection required"
    match = re.search(r"(?:^|\n)pid=(\d+)(?:\n|$)", lock_text)
    if not match:
        return f"durable ledger lock is held: {lock_path.name}; owner unknown, fail closed"
    try:
        os.kill(int(match.group(1)), 0)
    except ProcessLookupError:
        return f"durable ledger stale lock detected: {lock_path.name}; operator must inspect and remove it explicitly"
    except PermissionError:
        return f"durable ledger lock may be active: {lock_path.name}; fail closed"
    except OSError:
        return f"durable ledger lock state unknown: {lock_path.name}; fail closed"
    return f"durable ledger lock is active: {lock_path.name}; fail closed"


def _atomic_write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            frame.to_csv(handle, index=False)
            handle.flush()
            os.fsync(handle.fileno())
        _read_csv_validated(temp_path)
        os.replace(temp_path, path)
    except BrokerLedgerError:
        raise
    except OSError as exc:
        raise BrokerLedgerError(f"durable ledger atomic write failed: {path.name}") from exc
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def load_broker_ledger(latest_dir: str | Path) -> pd.DataFrame:
    """Pure durable-ledger read. Never creates, migrates, or syncs files."""
    durable_path, _ = _ledger_paths(latest_dir)
    if not durable_path.exists():
        return _empty_ledger()
    return _read_csv_validated(durable_path)


def find_order_by_client_order_id(latest_dir: str | Path, client_order_id: str) -> dict[str, object] | None:
    """Validated durable lookup only; report copies never participate."""
    frame = load_broker_ledger(latest_dir)
    matches = frame[frame["client_order_id"].astype(str) == str(client_order_id)]
    if matches.empty:
        return None
    return matches.iloc[0].to_dict()


def _write_broker_ledger_locked(durable_path: Path, report_copy_path: Path, frame: pd.DataFrame, *, sync_report_copy: bool) -> pd.DataFrame:
    validated = _validate_ledger_frame(frame)
    _atomic_write_csv(durable_path, validated)
    if sync_report_copy:
        _atomic_write_csv(report_copy_path, validated)
    return validated


def write_broker_ledger(latest_dir: str | Path, frame: pd.DataFrame, *, sync_report_copy: bool = False) -> pd.DataFrame:
    """Authorized durable write. Durable file commits before optional report copy."""
    durable_path, report_copy_path = _ledger_paths(latest_dir)
    with _ledger_lock(durable_path):
        return _write_broker_ledger_locked(durable_path, report_copy_path, frame, sync_report_copy=sync_report_copy)


def sync_broker_ledger_report_copy(latest_dir: str | Path) -> pd.DataFrame:
    """Explicitly synchronize secondary report copy from validated durable state."""
    durable_path, report_copy_path = _ledger_paths(latest_dir)
    with _ledger_lock(durable_path):
        if not durable_path.exists():
            raise BrokerLedgerError("cannot sync report copy: durable ledger does not exist")
        frame = _read_csv_validated(durable_path)
        _atomic_write_csv(report_copy_path, frame)
        return frame

def append_order_to_ledger(
    latest_dir: str | Path,
    client_order_id: str,
    broker_order_id: str,
    symbol: str,
    side: str,
    notional: float,
    qty: float,
    status: str,
    readback_status: str,
    broker_position_match: bool,
    filled_qty: float = 0.0,
) -> pd.DataFrame:
    durable_path, report_copy_path = _ledger_paths(latest_dir)
    with _ledger_lock(durable_path):
        df = _read_csv_validated(durable_path) if durable_path.exists() else _empty_ledger()
        if str(client_order_id) in df["client_order_id"].astype(str).values:
            return df
        new_row = {
            "client_order_id": client_order_id,
            "broker_order_id": broker_order_id,
            "symbol": symbol,
            "side": side,
            "notional": notional,
            "qty": qty,
            "status": status,
            "readback_status": readback_status,
            "broker_position_match": broker_position_match,
            "filled_qty": filled_qty,
        }
        new_frame = pd.DataFrame([new_row], columns=LEDGER_COLUMNS)
        combined = new_frame if df.empty else pd.concat([df, new_frame], ignore_index=True)
        return _write_broker_ledger_locked(durable_path, report_copy_path, combined, sync_report_copy=True)

def reconcile_forward_only(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None = None,
) -> dict[str, object]:
    df = load_broker_ledger(latest_dir)
    checks = []

    if df.empty:
        return {
            "status": "PASS",
            "checks": pd.DataFrame(columns=["section", "check_name", "expected", "actual", "status", "reason"]),
            "summary": {"reconciliation_status": "PASS"},
        }

    if broker_snapshot is None:
        for _, row in df.iterrows():
            sym = str(row["symbol"]).upper()
            checks.append({
                "section": "forward_only_reconciliation",
                "check_name": f"broker_position_match_{sym}",
                "local_value": row["qty"],
                "broker_value": "not_available",
                "status": "WARNING",
                "reason": "BROKER_SNAPSHOT_NOT_AVAILABLE",
            })
        return {
            "status": "WARNING",
            "checks": pd.DataFrame(checks),
            "summary": {"reconciliation_status": "WARNING"},
        }

    # Extract broker positions and open orders
    broker_positions = {}
    for pos in broker_snapshot.positions:
        sym = str(pos.get("symbol", "")).upper()
        if sym:
            broker_positions[sym] = {
                "qty": float(pos.get("qty", pos.get("shares", 0.0))),
                "avg_price": float(pos.get("avg_price", pos.get("avg_entry_price", 0.0))),
            }

    broker_open_orders = {}
    for ord_ in broker_snapshot.open_orders:
        sym = str(ord_.get("symbol", "")).upper()
        if sym:
            broker_open_orders[sym] = broker_open_orders.get(sym, 0) + 1

    status_pass = True
    has_warnings = False
    for _, row in df.iterrows():
        sym = str(row["symbol"]).upper()
        expected_qty = float(row["qty"])
        if "filled_qty" in row and pd.notna(row["filled_qty"]) and str(row["filled_qty"]).strip() != "":
            try:
                fq = float(row["filled_qty"])
                if fq > 0.0:
                    expected_qty = fq
            except ValueError:
                pass
        actual_pos = broker_positions.get(sym, {"qty": 0.0, "avg_price": 0.0})
        actual_qty = actual_pos["qty"]

        # Position qty check
        qty_match = abs(expected_qty - actual_qty) < 1e-6
        if qty_match:
            check_status = "PASS"
            check_reason = "qty_match"
        else:
            if expected_qty > 0.0 and actual_qty == 0.0 and (str(row.get("status", "")).upper() in {"FILLED", "PARTIALLY_FILLED"}):
                check_status = "WARNING"
                check_reason = "TRANSIENT_POSITION_PENDING"
                has_warnings = True
            else:
                check_status = "FAIL"
                check_reason = "qty_mismatch"
                status_pass = False

        checks.append({
            "section": "forward_only_reconciliation",
            "check_name": f"broker_position_match_{sym}",
            "local_value": expected_qty,
            "broker_value": actual_qty,
            "status": check_status,
            "reason": check_reason,
        })

        # Open orders check for this symbol (we expect 0 open orders in forward-only unless the ledger row status is a non-terminal open state)
        open_count = broker_open_orders.get(sym, 0)
        row_status = str(row.get("status", "")).upper()
        expected_open_count = 0.0
        if row_status in {"ACCEPTED/NEW", "NEW", "PARTIALLY_FILLED", "SUBMITTED", "ACCEPTED"}:
            expected_open_count = 1.0
        order_match = abs(float(open_count) - expected_open_count) < 1e-6
        order_check_status = "PASS" if order_match else "FAIL"
        if not order_match:
            status_pass = False

        checks.append({
            "section": "forward_only_reconciliation",
            "check_name": f"broker_open_order_count_{sym}",
            "local_value": expected_open_count,
            "broker_value": float(open_count),
            "status": order_check_status,
            "reason": "open_orders_match" if order_match else f"{open_count}_open_orders_exist_vs_{expected_open_count}_expected",
        })

    final_status = "PASS"
    if not status_pass:
        final_status = "FAIL"
    elif has_warnings:
        final_status = "WARNING"
    return {
        "status": final_status,
        "checks": pd.DataFrame(checks),
        "summary": {
            "reconciliation_status": final_status,
        },
    }

def write_broker_ledger_report(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None = None,
) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    df = load_broker_ledger(latest)
    recon = reconcile_forward_only(latest, broker_snapshot)

    md_path = latest / "broker_ledger_report.md"
    csv_path = latest / "broker_ledger_report.csv"
    json_path = latest / "broker_ledger_report.json"

    recon["checks"].to_csv(csv_path, index=False)

    # helper functions for tables
    def clean(val):
        if pd.isna(val): return ""
        return str(val).strip()

    def to_markdown(frame):
        if frame is None or frame.empty:
            return "None"
        cols = list(frame.columns)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
        for _, r in frame.iterrows():
            lines.append("| " + " | ".join(clean(r.get(c, "")) for c in cols) + " |")
        return "\n".join(lines)

    summary_values = {
        "forward_only_reconciliation_status": recon["status"],
        "tracked_order_count": len(df),
        "broker_snapshot_source": broker_snapshot.source if broker_snapshot else "NONE",
    }
    summary_table = to_markdown(pd.DataFrame([{"metric": k, "value": v} for k, v in summary_values.items()]))
    orders_table = to_markdown(df)
    checks_table = to_markdown(recon["checks"])

    report_content = f"""# Broker Order Ledger & Forward-Only Report

## Summary
{summary_table}

## Tracked Broker Orders
{orders_table}

## Forward-Only Reconciliation Checks
{checks_table}
"""
    md_path.write_text(report_content, encoding="utf-8")

    payload = {
        "summary": summary_values,
        "orders": json.loads(df.to_json(orient="records")),
        "checks": json.loads(recon["checks"].to_json(orient="records")),
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "md_path": str(md_path),
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "status": recon["status"],
    }

def write_broker_forward_monitor(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None = None,
) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    df = load_broker_ledger(latest)

    symbols_tracked = set(df["symbol"].dropna().str.upper().tolist()) if not df.empty else set()

    positions_info = []
    if broker_snapshot:
        for pos in broker_snapshot.positions:
            sym = str(pos.get("symbol", "")).upper()
            if sym in symbols_tracked:
                qty = float(pos.get("qty", 0.0))
                avg_price = float(pos.get("avg_entry_price", pos.get("avg_price", 0.0)))
                mval_str = pos.get("market_value", "")
                try:
                    mval = float(mval_str) if mval_str else 0.0
                except ValueError:
                    mval = 0.0
                cost = qty * avg_price
                unrealized_pl = mval - cost if mval > 0 else 0.0

                symbol_rows = df[df["symbol"].str.upper() == sym]
                ledger_qty = 0.0
                for _, r in symbol_rows.iterrows():
                    row_qty = float(r["qty"])
                    if "filled_qty" in r and pd.notna(r["filled_qty"]) and str(r["filled_qty"]).strip() != "":
                        try:
                            fq = float(r["filled_qty"])
                            if fq > 0.0:
                                row_qty = fq
                        except ValueError:
                            pass
                    ledger_qty += row_qty
                qty_match_bool = abs(qty - ledger_qty) < 1e-6
                if qty_match_bool:
                    qty_match_val = True
                else:
                    is_pending = False
                    for _, r in symbol_rows.iterrows():
                        if str(r.get("status", "")).upper() in {"FILLED", "PARTIALLY_FILLED"}:
                            row_expected = float(r["qty"])
                            if "filled_qty" in r and pd.notna(r["filled_qty"]) and str(r["filled_qty"]).strip() != "":
                                try:
                                    fq = float(r["filled_qty"])
                                    if fq > 0.0:
                                        row_expected = fq
                                except ValueError:
                                    pass
                            if row_expected > 0.0 and qty == 0.0:
                                is_pending = True
                    qty_match_val = "WARNING:TRANSIENT_POSITION_PENDING" if is_pending else False

                positions_info.append({
                    "symbol": sym,
                    "broker_qty": qty,
                    "avg_price": avg_price,
                    "market_value": mval if mval > 0 else "not_available",
                    "unrealized_pl": unrealized_pl if mval > 0 else "not_available",
                    "ledger_qty": ledger_qty,
                    "ledger_match": qty_match_val,
                })

    for sym in symbols_tracked:
        if not any(p["symbol"] == sym for p in positions_info):
            symbol_rows = df[df["symbol"].str.upper() == sym]
            ledger_qty = 0.0
            for _, r in symbol_rows.iterrows():
                row_qty = float(r["qty"])
                if "filled_qty" in r and pd.notna(r["filled_qty"]) and str(r["filled_qty"]).strip() != "":
                    try:
                        fq = float(r["filled_qty"])
                        if fq > 0.0:
                            row_qty = fq
                    except ValueError:
                        pass
                ledger_qty += row_qty
            is_pending = False
            for _, r in symbol_rows.iterrows():
                if str(r.get("status", "")).upper() in {"FILLED", "PARTIALLY_FILLED"}:
                    row_expected = float(r["qty"])
                    if "filled_qty" in r and pd.notna(r["filled_qty"]) and str(r["filled_qty"]).strip() != "":
                        try:
                            fq = float(r["filled_qty"])
                            if fq > 0.0:
                                row_expected = fq
                        except ValueError:
                            pass
                    if row_expected > 0.0:
                        is_pending = True
            qty_match_val = "WARNING:TRANSIENT_POSITION_PENDING" if is_pending else (ledger_qty == 0.0)
            positions_info.append({
                "symbol": sym,
                "broker_qty": 0.0,
                "avg_price": 0.0,
                "market_value": 0.0,
                "unrealized_pl": 0.0,
                "ledger_qty": ledger_qty,
                "ledger_match": qty_match_val,
            })

    open_orders_info = []
    if broker_snapshot:
        for ord_ in broker_snapshot.open_orders:
            sym = str(ord_.get("symbol", "")).upper()
            if sym in symbols_tracked:
                open_orders_info.append({
                    "broker_order_id": ord_.get("broker_order_id", ""),
                    "client_order_id": ord_.get("client_order_id", ""),
                    "symbol": sym,
                    "side": ord_.get("side", ""),
                    "qty": ord_.get("qty", ""),
                    "status": ord_.get("status", ""),
                    "submitted_at": ord_.get("submitted_at", ""),
                })

    stale_risk = False
    stale_count = len(open_orders_info)
    if stale_count > 0:
        stale_risk = True

    safety_info = {
        "live_trading_enabled": broker_snapshot.live_trading_enabled if broker_snapshot else False,
        "short_trading_enabled": False,
        "broker_submit_enabled": False,
        "submit_order_called": False,
        "post_request_used": False,
    }

    def clean(val):
        if pd.isna(val): return ""
        return str(val).strip()

    def to_markdown(list_of_dicts):
        if not list_of_dicts:
            return "None"
        frame = pd.DataFrame(list_of_dicts)
        cols = list(frame.columns)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
        for _, r in frame.iterrows():
            lines.append("| " + " | ".join(clean(r.get(c, "")) for c in cols) + " |")
        return "\n".join(lines)

    pos_table = to_markdown(positions_info)
    orders_table = to_markdown(open_orders_info)
    safety_table = to_markdown([{"setting": k, "value": v} for k, v in safety_info.items()])

    report_content = f"""# Broker-Forward Position Monitor

## Summary & Positions
{pos_table}

## Open Orders
{orders_table}

## Stale Order Risk
- Stale Order Count: {stale_count}
- Stale Risk Status: {"HIGH RISK" if stale_risk else "PASS"}

## Safety Status
{safety_table}
"""

    md_path = latest / "broker_forward_monitor.md"
    json_path = latest / "broker_forward_monitor.json"

    md_path.write_text(report_content, encoding="utf-8")

    payload = {
        "positions": positions_info,
        "open_orders": open_orders_info,
        "stale_order_risk": {
            "stale_count": stale_count,
            "stale_risk": stale_risk,
        },
        "safety": safety_info,
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return payload
