from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    from .alpaca_paper_broker import AlpacaPaperOrderSubmitClient
    from .broker_ledger import BrokerLedgerError, load_broker_ledger, write_broker_ledger
    from .broker_reconciliation import ReadOnlyBrokerSnapshot
except ImportError:
    from alpaca_paper_broker import AlpacaPaperOrderSubmitClient
    from broker_ledger import BrokerLedgerError, load_broker_ledger, write_broker_ledger
    from broker_reconciliation import ReadOnlyBrokerSnapshot


ALPACA_LIFECYCLE_MAP = {
    "new": "ACCEPTED/NEW",
    "accepted": "ACCEPTED/NEW",
    "pending_new": "ACCEPTED/NEW",
    "accepted_for_bidding": "ACCEPTED/NEW",
    "partially_filled": "PARTIALLY_FILLED",
    "filled": "FILLED",
    "canceled": "CANCELED",
    "pending_cancel": "PENDING_CANCEL",
    "expired": "EXPIRED",
    "rejected": "REJECTED",
    "pending_replace": "PENDING_REPLACE",
    "replaced": "REPLACED",
    "suspended": "SUSPENDED",
    "stopped": "STOPPED",
    "calculated": "CALCULATED",
}
STALE_ORDER_THRESHOLD_SECONDS = 300
TERMINAL_STATUSES = {"FILLED", "CANCELED", "EXPIRED", "REJECTED", "REPLACED"}
STATUS_RANK = {
    "SUBMITTED": 10,
    "ACCEPTED/NEW": 20,
    "PENDING_CANCEL": 25,
    "PENDING_REPLACE": 25,
    "PARTIALLY_FILLED": 30,
    "SUSPENDED": 30,
    "STOPPED": 30,
    "CALCULATED": 30,
    "FILLED": 40,
    "CANCELED": 40,
    "EXPIRED": 40,
    "REJECTED": 40,
    "REPLACED": 40,
}


def _parse_rfc3339(ts_str: str) -> datetime | None:
    if not ts_str:
        return None
    text = str(ts_str).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


class LifecycleSubmitClient(AlpacaPaperOrderSubmitClient):
    def read_order_by_id(self, order_id: str):
        return self._request_json("GET", f"/v2/orders/{order_id}")


def _finite_quantity(value, field: str) -> float:
    try:
        quantity = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not math.isfinite(quantity) or quantity < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return quantity


def _validate_readback(payload, client_order_id: str, broker_order_id: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("response is not a dict")
    response_client_id = str(payload.get("client_order_id", "")).strip()
    response_broker_id = str(payload.get("id", payload.get("broker_order_id", ""))).strip()
    client_match = bool(response_client_id and response_client_id == client_order_id)
    broker_match = bool(response_broker_id and response_broker_id == broker_order_id)
    if not client_match and not broker_match:
        raise ValueError("response order identifiers do not match ledger row")
    raw_status = str(payload.get("status", "")).strip().lower()
    if raw_status not in ALPACA_LIFECYCLE_MAP:
        raise ValueError("response status is not recognized")
    filled_qty = _finite_quantity(payload.get("filled_qty", 0.0), "filled_qty")
    if "qty" in payload:
        _finite_quantity(payload["qty"], "qty")
    replacement_order_id = ""
    for field in ("replaced_by_order_id", "replaced_by", "replacement_order_id"):
        value = str(payload.get(field, "")).strip()
        if value:
            replacement_order_id = value
            break
    return {
        "raw_status": raw_status,
        "status": ALPACA_LIFECYCLE_MAP[raw_status],
        "filled_qty": filled_qty,
        "submitted_at": str(payload.get("submitted_at", payload.get("created_at", ""))).strip(),
        "replacement_order_id": replacement_order_id,
    }


def _readback(client, client_order_id: str, broker_order_id: str) -> tuple[dict[str, object] | None, str]:
    try:
        payload = client.read_order_by_client_order_id(client_order_id)
    except Exception as first_error:
        try:
            payload = client.read_order_by_id(broker_order_id)
        except Exception as second_error:
            return None, f"readback_failed:{type(first_error).__name__}/{type(second_error).__name__}"
    try:
        return _validate_readback(payload, client_order_id, broker_order_id), "PASS"
    except ValueError as error:
        return None, f"readback_invalid:{error}"


def _progression_allowed(previous: str, observed: str) -> bool:
    old = str(previous).upper()
    new = str(observed).upper()
    if old in TERMINAL_STATUSES and new != old:
        return False
    return STATUS_RANK.get(new, 0) >= STATUS_RANK.get(old, 0)


def _broker_positions(snapshot: ReadOnlyBrokerSnapshot | None) -> tuple[dict[str, float] | None, str]:
    if snapshot is None:
        return None, "UNKNOWN:no_snapshot"
    positions: dict[str, float] = {}
    try:
        for position in snapshot.positions:
            if not isinstance(position, dict):
                return None, "UNKNOWN:malformed_position"
            symbol = str(position.get("symbol", "")).upper()
            if not symbol:
                return None, "UNKNOWN:missing_position_symbol"
            positions[symbol] = _finite_quantity(position.get("qty", position.get("shares", 0.0)), "position_qty")
    except ValueError:
        return None, "UNKNOWN:invalid_position_qty"
    return positions, "PASS"


def _expected_signed_quantity(frame: pd.DataFrame, symbol: str) -> float:
    total = 0.0
    for _, row in frame.iterrows():
        if str(row["symbol"]).upper() != symbol:
            continue
        if str(row["status"]).upper() not in {"PARTIALLY_FILLED", "FILLED"}:
            continue
        quantity = float(row["filled_qty"])
        total += quantity if str(row["side"]).lower() == "buy" else -quantity
    return total


def check_broker_order_lifecycle(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None = None,
    api_key: str = "",
    secret_key: str = "",
    base_url: str = "https://paper-api.alpaca.markets",
    *,
    allow_state_write: bool = False,
    readback_client=None,
) -> dict[str, object]:
    """Inspect lifecycle. Durable writes require explicit authorization."""
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    frame = load_broker_ledger(latest)
    if frame.empty:
        summary = _summary(frame, broker_snapshot, False, allow_state_write, 0, 0, 0)
        _write_reports(latest, summary, [], [])
        return {"summary": summary, "orders": [], "issues": []}

    client = readback_client
    if client is None and api_key and secret_key:
        client = LifecycleSubmitClient(api_key=api_key, secret_key=secret_key, base_url=base_url)
    positions, position_evidence = _broker_positions(broker_snapshot)
    candidate = frame.copy()
    issues: list[str] = []
    details: list[dict[str, object]] = []
    valid_events: dict[int, dict[str, object]] = {}
    current_time = datetime.now(timezone.utc)

    for index, row in frame.iterrows():
        client_order_id = str(row["client_order_id"])
        broker_order_id = str(row["broker_order_id"])
        observed = None
        readback_status = "FAIL:missing_credentials" if client is None else "FAIL:not_read"
        if client is not None:
            observed, readback_status = _readback(client, client_order_id, broker_order_id)
        if observed is None:
            issues.append(f"{client_order_id}: {readback_status}; durable row preserved")
            details.append(_detail(row, str(row["status"]), readback_status, 0.0, "UNKNOWN:readback_unavailable", ""))
            continue
        observed_status = str(observed["status"])
        if not _progression_allowed(str(row["status"]), observed_status):
            issues.append(f"{client_order_id}: status regression {row['status']} -> {observed_status} ignored")
            details.append(_detail(row, str(row["status"]), "WARNING:status_regression_ignored", 0.0, "UNKNOWN:preserved", ""))
            continue
        candidate.at[index, "status"] = observed_status
        candidate.at[index, "readback_status"] = "PASS"
        candidate.at[index, "filled_qty"] = observed["filled_qty"]
        replacement_order_id = str(observed["replacement_order_id"])
        if observed["raw_status"] in {"pending_replace", "replaced"} and not replacement_order_id:
            issues.append(f"{client_order_id}: replacement linkage unavailable; original identifiers preserved")
        valid_events[index] = observed

    for index, observed in valid_events.items():
        row = candidate.loc[index]
        symbol = str(row["symbol"]).upper()
        match_status = position_evidence
        position_match = None
        if positions is not None:
            expected = _expected_signed_quantity(candidate, symbol)
            actual = positions.get(symbol, 0.0)
            position_match = abs(actual - expected) < 1e-6
            match_status = "PASS" if position_match else "FAIL"
            candidate.at[index, "broker_position_match"] = str(position_match)
        submitted_at = _parse_rfc3339(str(observed["submitted_at"]))
        age_seconds = (current_time - submitted_at).total_seconds() if submitted_at else 0.0
        details.append(
            _detail(
                row,
                str(row["status"]),
                "PASS",
                age_seconds,
                match_status,
                str(observed["raw_status"]),
                position_match,
                str(observed["replacement_order_id"]),
            )
        )

    changes_detected = not candidate.equals(frame)
    state_mutated = False
    if changes_detected and allow_state_write:
        try:
            write_broker_ledger(latest, candidate, sync_report_copy=True)
            state_mutated = True
        except BrokerLedgerError as error:
            issues.append(f"authorized lifecycle write blocked: {error}")
    elif changes_detected:
        issues.append("report-only lifecycle inspection: observed updates not persisted")

    stale_count = sum(
        detail["status"] in {"SUBMITTED", "ACCEPTED/NEW", "PARTIALLY_FILLED"}
        and float(detail["age_seconds"]) > STALE_ORDER_THRESHOLD_SECONDS
        for detail in details
    )
    summary = _summary(candidate, broker_snapshot, state_mutated, allow_state_write, len(valid_events), len(issues), stale_count)
    _write_reports(latest, summary, details, issues)
    return {"summary": summary, "orders": details, "issues": issues}


def _detail(row, status: str, readback_status: str, age_seconds: float, position_match_status: str, raw_status: str, position_match=None, replacement_order_id: str = "") -> dict[str, object]:
    return {
        "client_order_id": str(row["client_order_id"]),
        "broker_order_id": str(row["broker_order_id"]),
        "replacement_order_id": replacement_order_id,
        "symbol": str(row["symbol"]),
        "side": str(row["side"]),
        "qty": float(row["qty"]),
        "status": status,
        "alpaca_raw_status": raw_status,
        "readback_status": readback_status,
        "age_seconds": age_seconds,
        "filled_qty": float(row["filled_qty"]),
        "position_match": position_match,
        "position_match_status": position_match_status,
    }


def _summary(frame: pd.DataFrame, snapshot: ReadOnlyBrokerSnapshot | None, state_mutated: bool, write_authorized: bool, valid_events: int, issue_count: int, stale_count: int) -> dict[str, object]:
    statuses = frame.get("status", pd.Series(dtype=str)).astype(str).str.upper()
    return {
        "status": "WARNING" if issue_count else "PASS",
        "tracked_orders": len(frame),
        "stale_open_orders": stale_count,
        "partial_fills": int(statuses.eq("PARTIALLY_FILLED").sum()),
        "rejected_orders": int(statuses.eq("REJECTED").sum()),
        "state_mutated": state_mutated,
        "lifecycle_write_authorized": write_authorized,
        "valid_readback_events": valid_events,
        "broker_snapshot_source": snapshot.source if snapshot else "NONE",
        "submit_order_called": False,
        "post_request_used": False,
        "cancel_replace_used": False,
    }


def _write_reports(latest: Path, summary: dict[str, object], orders: list[dict[str, object]], issues: list[str]) -> None:
    json_path = latest / "broker_order_lifecycle.json"
    json_path.write_text(json.dumps({"summary": summary, "orders": orders, "issues": issues}, indent=2, default=str), encoding="utf-8")
    frame = pd.DataFrame(orders)
    if frame.empty:
        orders_table = "None"
    else:
        columns = list(frame.columns)
        rows = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
        for _, row in frame.iterrows():
            rows.append("| " + " | ".join(str(row.get(column, "")).strip() for column in columns) + " |")
        orders_table = "\n".join(rows)
    summary_table = "\n".join(f"- {key}: {value}" for key, value in summary.items())
    issue_text = "\n".join(f"- {issue}" for issue in issues) if issues else "None"
    (latest / "broker_order_lifecycle.md").write_text(
        f"# Broker Order Lifecycle Report\n\n## Summary\n{summary_table}\n\n## Tracked Orders\n{orders_table}\n\n## Issues / Warnings\n{issue_text}\n",
        encoding="utf-8",
    )
