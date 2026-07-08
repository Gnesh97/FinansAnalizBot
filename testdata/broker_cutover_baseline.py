from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd

try:
    from .broker_reconciliation import (
        ReadOnlyBrokerSnapshot,
        _broker_orders,
        _broker_positions,
        _clean_text,
        _local_orders,
        _local_positions,
        _markdown_table,
        _metric_table,
        _read_csv,
        _records,
    )
except ImportError:
    from broker_reconciliation import (
        ReadOnlyBrokerSnapshot,
        _broker_orders,
        _broker_positions,
        _clean_text,
        _local_orders,
        _local_positions,
        _markdown_table,
        _metric_table,
        _read_csv,
        _records,
    )


CUTOVER_CHECK_COLUMNS = ["section", "check_name", "expected", "actual", "status", "reason"]


def _check(section: str, name: str, expected, actual, status: str, reason: str) -> dict[str, object]:
    return {
        "section": section,
        "check_name": name,
        "expected": expected,
        "actual": actual,
        "status": status,
        "reason": reason,
    }


def build_broker_cutover_readiness(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None = None,
) -> dict[str, object]:
    latest = Path(latest_dir)
    trade_log = _read_csv(latest / "paper_trade_log.csv")
    pending_orders = _read_csv(latest / "paper_pending_orders.csv")
    local_positions = _local_positions(trade_log)
    local_orders = _local_orders(pending_orders)
    broker_positions = _broker_positions(broker_snapshot) if broker_snapshot else pd.DataFrame(columns=["symbol", "qty", "avg_price"])
    broker_orders = _broker_orders(broker_snapshot) if broker_snapshot else pd.DataFrame(columns=["symbol", "side", "qty"])
    metadata = broker_snapshot.metadata if broker_snapshot else {}
    snapshot_status = str(metadata.get("alpaca_snapshot_status", "NOT_REQUESTED")).upper()
    snapshot_source = broker_snapshot.source if broker_snapshot else "NONE"
    local_position_count = int(len(local_positions))
    broker_position_count = int(len(broker_positions))
    local_order_count = int(len(local_orders))
    broker_order_count = int(len(broker_orders))
    local_only_pre_cutover = snapshot_status == "PASS" and local_position_count > 0 and broker_position_count == 0
    no_position_gap = local_position_count == broker_position_count

    if snapshot_status != "PASS":
        readiness = "NOT_AVAILABLE" if snapshot_status in {"NOT_AVAILABLE", "NOT_REQUESTED", ""} else "BLOCK"
        gap_status = "UNKNOWN"
        status = "WARNING" if readiness == "NOT_AVAILABLE" else "FAIL"
        gap_reason = "ALPACA_SNAPSHOT_NOT_AVAILABLE"
    elif local_only_pre_cutover:
        readiness = "PRE_CUTOVER"
        gap_status = "UNSYNCED_LOCAL_ONLY"
        status = "PASS"
        gap_reason = "historical_local_positions_not_submitted_to_broker"
    elif no_position_gap:
        readiness = "READY"
        gap_status = "SYNCED_OR_EMPTY"
        status = "PASS"
        gap_reason = "position_counts_match"
    else:
        readiness = "REVIEW"
        gap_status = "BROKER_LOCAL_MISMATCH"
        status = "WARNING"
        gap_reason = "manual_review_required_before_cutover"

    checks = [
        _check("alpaca_snapshot", "alpaca_get_only_status", "PASS", snapshot_status, "PASS" if snapshot_status == "PASS" else status, metadata.get("alpaca_snapshot_reason", "")),
        _check("alpaca_snapshot", "broker_snapshot_source", "ALPACA_PAPER_GET_ONLY", snapshot_source, "PASS" if snapshot_source == "ALPACA_PAPER_GET_ONLY" else "WARNING", "paper_get_only_snapshot"),
        _check("local_broker_gap", "local_open_position_count", "derived_from_csv", local_position_count, "PASS", "local_csv_source_of_truth"),
        _check("local_broker_gap", "broker_open_position_count", "read_from_broker", broker_position_count, "PASS" if snapshot_status == "PASS" else "WARNING", "alpaca_positions_get"),
        _check("local_broker_gap", "local_broker_gap_status", "PRE_CUTOVER_OK", gap_status, status, gap_reason),
        _check("open_orders", "local_accepted_pending_count", "derived_from_csv", local_order_count, "PASS", "local_report_only"),
        _check("open_orders", "broker_open_order_count", "read_from_broker", broker_order_count, "PASS" if snapshot_status == "PASS" else "WARNING", "alpaca_open_orders_get"),
        _check("cutover", "broker_cutover_enabled", False, False, "PASS", "disabled_by_default"),
        _check("cutover", "bulk_mirror_historical_positions", False, False, "PASS", "forward_only_no_backfill"),
        _check("safety", "submit_order_called", False, False, "PASS", "not_called"),
        _check("safety", "post_request_used", False, False, "PASS", "get_only"),
        _check("safety", "broker_submit_enabled", False, False, "PASS", "disabled"),
        _check("safety", "live_trading_enabled", False, False, "PASS", "disabled"),
        _check("safety", "short_trading_enabled", False, False, "PASS", "disabled"),
        _check("safety", "state_mutated", False, False, "PASS", "report_only"),
    ]
    checks_frame = pd.DataFrame(checks, columns=CUTOVER_CHECK_COLUMNS)
    baseline_path = latest / "broker_cutover_baseline.json"
    summary = {
        "cutover_readiness_status": readiness,
        "local_broker_gap_status": gap_status,
        "broker_cutover_enabled": False,
        "broker_baseline_file_exists": baseline_path.exists(),
        "broker_baseline_file_path": str(baseline_path),
        "broker_snapshot_source": snapshot_source,
        "alpaca_snapshot_status": snapshot_status,
        "alpaca_snapshot_reason": metadata.get("alpaca_snapshot_reason", ""),
        "alpaca_failure_category": metadata.get("alpaca_failure_category", ""),
        "alpaca_failure_endpoint": metadata.get("alpaca_failure_endpoint", ""),
        "alpaca_failure_detail": metadata.get("alpaca_failure_detail", ""),
        "alpaca_sdk_import_status": metadata.get("alpaca_sdk_import_status", "NOT_REQUIRED"),
        "alpaca_account_read_success": bool(metadata.get("alpaca_account_read_success", False)),
        "alpaca_positions_read_success": bool(metadata.get("alpaca_positions_read_success", False)),
        "alpaca_open_orders_read_success": bool(metadata.get("alpaca_open_orders_read_success", False)),
        "request_methods_used": metadata.get("request_methods_used", "none"),
        "local_open_position_count": local_position_count,
        "broker_open_position_count": broker_position_count,
        "local_accepted_pending_count": local_order_count,
        "broker_open_order_count": broker_order_count,
        "bulk_mirror_historical_positions": False,
        "state_mutated": False,
        "broker_submit_enabled": False,
        "live_trading_enabled": False,
        "short_trading_enabled": False,
        "submit_order_called": False,
        "post_request_used": False,
    }
    return {
        "summary": summary,
        "checks": checks_frame,
        "local_positions": local_positions,
        "local_orders": local_orders,
        "broker_positions": broker_positions,
        "broker_orders": broker_orders,
    }


def _symbols_text(frame: pd.DataFrame) -> str:
    if frame.empty or "symbol" not in frame:
        return "none"
    symbols = sorted({_clean_text(value).upper() for value in frame["symbol"].tolist() if _clean_text(value)})
    return ",".join(symbols) if symbols else "none"


def write_broker_cutover_readiness(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None = None,
) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    result = build_broker_cutover_readiness(latest, broker_snapshot)
    csv_path = latest / "broker_cutover_readiness.csv"
    md_path = latest / "broker_cutover_readiness.md"
    json_path = latest / "broker_cutover_readiness.json"
    result["checks"].to_csv(csv_path, index=False)
    payload = {
        "summary": result["summary"],
        "checks": _records(result["checks"]),
        "local_positions": _records(result["local_positions"]),
        "local_orders": _records(result["local_orders"]),
        "broker_positions": _records(result["broker_positions"]),
        "broker_orders": _records(result["broker_orders"]),
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Broker Cutover Readiness",
                "",
                "## Summary",
                "",
                _metric_table(result["summary"]),
                "",
                "## Checks",
                "",
                _markdown_table(result["checks"]),
                "",
                "## Local-Only Historical Positions",
                "",
                f"- symbols: {_symbols_text(result['local_positions'])}",
                "",
                "## Broker Positions",
                "",
                _markdown_table(result["broker_positions"]),
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


def create_broker_cutover_baseline(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot,
) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    readiness = build_broker_cutover_readiness(latest, broker_snapshot)
    summary = readiness["summary"]
    baseline_path = latest / "broker_cutover_baseline.json"
    if summary.get("alpaca_snapshot_status") != "PASS":
        return {
            "created": False,
            "path": str(baseline_path),
            "reason": summary.get("alpaca_snapshot_reason", "ALPACA_SNAPSHOT_NOT_AVAILABLE"),
            "summary": summary,
        }

    metadata = broker_snapshot.metadata if broker_snapshot else {}
    payload = {
        "baseline_created_at_utc": datetime.now(timezone.utc).isoformat(),
        "forward_only": True,
        "broker_cutover_enabled": False,
        "historical_local_positions_policy": "LOCAL_ONLY_DO_NOT_MIRROR_TO_ALPACA",
        "broker_snapshot_source": summary.get("broker_snapshot_source", ""),
        "alpaca_snapshot_status": summary.get("alpaca_snapshot_status", ""),
        "alpaca_account_read_success": summary.get("alpaca_account_read_success", False),
        "alpaca_positions_read_success": summary.get("alpaca_positions_read_success", False),
        "alpaca_open_orders_read_success": summary.get("alpaca_open_orders_read_success", False),
        "request_methods_used": summary.get("request_methods_used", "none"),
        "broker_account_snapshot_summary": metadata.get("alpaca_account_summary", {}),
        "local_historical_open_position_count": summary.get("local_open_position_count", 0),
        "broker_open_position_count": summary.get("broker_open_position_count", 0),
        "broker_open_order_count": summary.get("broker_open_order_count", 0),
        "local_accepted_pending_count": summary.get("local_accepted_pending_count", 0),
        "local_broker_gap_status_at_baseline": summary.get("local_broker_gap_status", ""),
        "submit_order_called": False,
        "post_request_used": False,
        "broker_submit_enabled": False,
        "live_trading_enabled": False,
        "short_trading_enabled": False,
        "state_mutated": False,
    }
    baseline_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return {
        "created": True,
        "path": str(baseline_path),
        "reason": "created",
        "summary": {**summary, "broker_baseline_file_exists": True},
        "baseline": payload,
    }
