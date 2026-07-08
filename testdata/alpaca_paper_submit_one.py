from __future__ import annotations

import json
import socket
from pathlib import Path
import urllib.error

import pandas as pd

try:
    from .alpaca_paper_broker import AlpacaPaperOrderSubmitClient
    from .alpaca_paper_snapshot import alpaca_endpoint_mode
    from .alpaca_submit_readiness import (
        CLIENT_ORDER_PREVIEW_COLUMNS,
        READINESS_COLUMNS,
        _asset_checks,
        _candidate_rows,
        _check,
        _number,
        _open_order_conflicts,
        _preview_payloads,
    )
    from .broker_reconciliation import ReadOnlyBrokerSnapshot, _markdown_table, _metric_table, _records
except ImportError:
    from alpaca_paper_broker import AlpacaPaperOrderSubmitClient
    from alpaca_paper_snapshot import alpaca_endpoint_mode
    from alpaca_submit_readiness import (
        CLIENT_ORDER_PREVIEW_COLUMNS,
        READINESS_COLUMNS,
        _asset_checks,
        _candidate_rows,
        _check,
        _number,
        _open_order_conflicts,
        _preview_payloads,
    )
    from broker_reconciliation import ReadOnlyBrokerSnapshot, _markdown_table, _metric_table, _records


CONFIRM_TOKEN = "CONFIRM_ALPACA_PAPER_SUBMIT_ONE"
AUDIT_COLUMNS = [
    "symbol",
    "side",
    "client_order_id",
    "status",
    "reason",
    "would_submit",
    "submit_order_called",
    "post_request_used",
    "readback_attempted",
    "readback_status",
    "broker_order_id",
]


def _bool_text(value) -> str:
    return str(bool(value)).lower()


def _sanitize_error(error: Exception) -> str:
    text = str(error or "").replace("\n", " ").replace("\r", " ").strip()
    lowered = text.lower()
    if "key" in lowered or "secret" in lowered or "token" in lowered:
        return "redacted"
    return text[:120] or error.__class__.__name__


def _safe_status(value) -> str:
    return str(value or "").strip().upper()


def _order_payload(preview_row: pd.Series) -> dict[str, object]:
    payload = {
        "symbol": str(preview_row.get("symbol", "")).upper(),
        "side": str(preview_row.get("side", "")).lower(),
        "type": str(preview_row.get("type", "market")).lower() or "market",
        "time_in_force": str(preview_row.get("time_in_force", "day")).lower() or "day",
        "extended_hours": False,
        "client_order_id": str(preview_row.get("client_order_id", "")),
    }
    notional = preview_row.get("notional", "")
    qty = preview_row.get("qty", "")
    if str(notional).strip() not in {"", "nan", "None", "<NA>"}:
        payload["notional"] = str(notional)
    elif str(qty).strip() not in {"", "nan", "None", "<NA>"}:
        payload["qty"] = str(qty)
    return payload


def _gate_checks(
    latest: Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None,
    preview: pd.DataFrame,
    assets: dict[str, object],
    assets_metadata: dict[str, object],
    *,
    base_url: str,
    broker_submit_enabled: bool,
    live_trading_enabled: bool,
    short_trading_enabled: bool,
    confirm_token: str | None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    baseline_exists = (latest / "broker_cutover_baseline.json").exists()
    metadata = broker_snapshot.metadata if broker_snapshot else {}
    snapshot_status = _safe_status(metadata.get("alpaca_snapshot_status", "NOT_REQUESTED"))
    endpoint_mode = alpaca_endpoint_mode(base_url)
    account = metadata.get("alpaca_account_summary", {}) if isinstance(metadata.get("alpaca_account_summary", {}), dict) else {}
    buying_power = _number(account.get("buying_power", 0.0))
    required_notional = float(pd.to_numeric(preview.get("notional", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if not preview.empty else 0.0
    open_conflicts, wash_conflicts = _open_order_conflicts(preview, broker_snapshot)
    missing_assets, not_tradable_assets = _asset_checks(preview, assets)
    candidate_count = int(len(preview))
    confirm_ok = confirm_token == CONFIRM_TOKEN
    qty_xor_ok = bool(preview.empty or preview["qty_notional_exclusive"].all())

    checks = [
        _check("baseline", "broker_cutover_baseline_exists", True, baseline_exists, "PASS" if baseline_exists else "BLOCK", "explicit_forward_only_baseline_required"),
        _check("alpaca_snapshot", "alpaca_get_only_status", "PASS", snapshot_status, "PASS" if snapshot_status == "PASS" else "BLOCK", metadata.get("alpaca_snapshot_reason", "")),
        _check("alpaca_snapshot", "paper_endpoint_only", "paper", endpoint_mode, "PASS" if endpoint_mode == "paper" else "BLOCK", "paper_endpoint_required"),
        _check("submit_gate", "broker_submit_enabled", True, broker_submit_enabled, "PASS" if broker_submit_enabled else "BLOCK", "explicit_enable_required"),
        _check("submit_gate", "confirm_token", CONFIRM_TOKEN, "provided" if confirm_token else "missing", "PASS" if confirm_ok else "BLOCK", "operator_confirm_token_required"),
        _check("submit_gate", "max_submit_candidates", 1, candidate_count, "PASS" if candidate_count == 1 else "BLOCK", "exactly_one_candidate_required"),
        _check("submit_gate", "live_trading_enabled", False, live_trading_enabled, "BLOCK" if live_trading_enabled else "PASS", "live_trading_must_stay_disabled"),
        _check("submit_gate", "short_trading_enabled", False, short_trading_enabled, "BLOCK" if short_trading_enabled else "PASS", "short_trading_must_stay_disabled"),
        _check("submit_gate", "extended_hours", False, False, "PASS", "extended_hours_false_until_approved"),
        _check("submit_gate", "qty_notional_exclusive", True, qty_xor_ok, "PASS" if qty_xor_ok else "BLOCK", "do_not_send_qty_and_notional_together"),
        _check("buying_power", "buying_power_available", ">= required_notional", buying_power, "PASS" if required_notional <= buying_power else "BLOCK", f"required_notional={required_notional:.2f}"),
        _check("open_orders", "open_order_conflict_count", 0, open_conflicts, "PASS" if open_conflicts == 0 else "BLOCK", "existing_open_order_same_symbol"),
        _check("open_orders", "wash_trade_conflict_count", 0, wash_conflicts, "PASS" if wash_conflicts == 0 else "BLOCK", "opposite_side_open_order_same_symbol"),
        _check("assets", "assets_get_only_status", "PASS", bool(assets_metadata.get("alpaca_assets_read_success", True)), "PASS" if assets_metadata.get("alpaca_assets_read_success", True) else "BLOCK", assets_metadata.get("alpaca_assets_failure_category", "")),
        _check("assets", "missing_asset_count", 0, missing_assets, "PASS" if missing_assets == 0 else "BLOCK", "asset_lookup_required_for_candidate"),
        _check("assets", "not_tradable_asset_count", 0, not_tradable_assets, "PASS" if not_tradable_assets == 0 else "BLOCK", "asset_must_be_tradable"),
        _check("timeout_policy", "unknown_submit_result_policy", "readback_before_retry", "readback_before_retry", "PASS", "never_resend_blindly_after_timeout"),
    ]
    summary = {
        "broker_cutover_baseline_exists": baseline_exists,
        "broker_submit_enabled": broker_submit_enabled,
        "confirm_token_provided": bool(confirm_token),
        "confirm_token_valid": confirm_ok,
        "submit_candidate_count": candidate_count,
        "alpaca_snapshot_status": snapshot_status,
        "paper_endpoint_mode": endpoint_mode,
        "buying_power": buying_power,
        "required_notional": required_notional,
        "open_order_conflict_count": open_conflicts,
        "wash_trade_conflict_count": wash_conflicts,
        "missing_asset_count": missing_assets,
        "not_tradable_asset_count": not_tradable_assets,
        "live_trading_enabled": live_trading_enabled,
        "short_trading_enabled": short_trading_enabled,
        "extended_hours": False,
        "timeout_policy": "READBACK_BEFORE_RETRY",
    }
    return pd.DataFrame(checks, columns=READINESS_COLUMNS), summary


def _readback(client, client_order_id: str) -> tuple[bool, dict[str, object] | None, str]:
    try:
        payload = client.read_order_by_client_order_id(client_order_id)
    except Exception as error:
        return False, None, _sanitize_error(error)
    return True, payload if isinstance(payload, dict) else {}, "ok"


def build_alpaca_paper_submit_one(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None,
    *,
    base_url: str = "https://paper-api.alpaca.markets",
    assets: dict[str, object] | None = None,
    assets_metadata: dict[str, object] | None = None,
    broker_submit_enabled: bool = False,
    confirm_token: str | None = None,
    live_trading_enabled: bool = False,
    short_trading_enabled: bool = False,
    api_key: str = "",
    secret_key: str = "",
    submit_client=None,
) -> dict[str, object]:
    latest = Path(latest_dir)
    assets = assets or {}
    assets_metadata = assets_metadata or {}
    candidates = _candidate_rows(latest)
    preview = _preview_payloads(candidates)
    if preview.empty:
        preview = pd.DataFrame(columns=CLIENT_ORDER_PREVIEW_COLUMNS)
    checks, summary = _gate_checks(
        latest,
        broker_snapshot,
        preview,
        assets,
        assets_metadata,
        base_url=base_url,
        broker_submit_enabled=broker_submit_enabled,
        live_trading_enabled=live_trading_enabled,
        short_trading_enabled=short_trading_enabled,
        confirm_token=confirm_token,
    )
    status_values = checks["status"].astype(str).str.upper()
    gates_pass = not status_values.isin(["BLOCK", "FAIL"]).any()

    submit_order_called = False
    post_request_used = False
    readback_attempted = False
    submit_status = "BLOCKED"
    submit_reason = "submit_gates_blocked"
    readback_status = "NOT_ATTEMPTED"
    broker_order_id = ""

    if gates_pass:
        row = preview.iloc[0]
        payload = _order_payload(row)
        client = submit_client or AlpacaPaperOrderSubmitClient(api_key=api_key, secret_key=secret_key, base_url=base_url)
        try:
            submit_order_called = True
            post_request_used = True
            response = client.post_order(payload)
            readback_attempted = True
            readback_ok, readback_payload, readback_reason = _readback(client, payload["client_order_id"])
            if readback_ok:
                submit_status = "SUBMITTED_READBACK_OK"
                submit_reason = "api_submit_success_readback_ok"
                readback_status = "PASS"
                broker_order_id = str((readback_payload or response or {}).get("id", ""))
            else:
                submit_status = "SUBMITTED_READBACK_WARNING"
                submit_reason = f"readback_failed:{readback_reason}"
                readback_status = "WARNING"
                broker_order_id = str((response or {}).get("id", ""))
        except (TimeoutError, socket.timeout, urllib.error.URLError) as error:
            readback_attempted = True
            readback_ok, readback_payload, readback_reason = _readback(client, payload["client_order_id"])
            submit_status = "UNKNOWN_RESULT_READBACK_OK" if readback_ok else "UNKNOWN_RESULT_READBACK_FAILED"
            submit_reason = f"submit_unknown_no_retry:{_sanitize_error(error)}"
            readback_status = "PASS" if readback_ok else f"FAIL:{readback_reason}"
            broker_order_id = str((readback_payload or {}).get("id", ""))
        except Exception as error:
            submit_status = "SUBMIT_FAILED"
            submit_reason = _sanitize_error(error)
    else:
        payload = _order_payload(preview.iloc[0]) if not preview.empty else {}

    audit = pd.DataFrame(
        [
            {
                "symbol": payload.get("symbol", "") if "payload" in locals() else "",
                "side": payload.get("side", "") if "payload" in locals() else "",
                "client_order_id": payload.get("client_order_id", "") if "payload" in locals() else "",
                "status": submit_status,
                "reason": submit_reason,
                "would_submit": gates_pass,
                "submit_order_called": submit_order_called,
                "post_request_used": post_request_used,
                "readback_attempted": readback_attempted,
                "readback_status": readback_status,
                "broker_order_id": broker_order_id,
            }
        ],
        columns=AUDIT_COLUMNS,
    )
    final_status = submit_status if gates_pass else "BLOCK"
    summary.update(
        {
            "alpaca_paper_submit_one_status": final_status,
            "submit_gate_status": "PASS" if gates_pass else "BLOCK",
            "submit_order_called": submit_order_called,
            "post_request_used": post_request_used,
            "readback_attempted": readback_attempted,
            "readback_status": readback_status,
            "submitted_count": 1 if submit_status == "SUBMITTED_READBACK_OK" else 0,
            "state_mutated": False,
        }
    )
    return {"summary": summary, "checks": checks, "client_order_preview": preview, "audit": audit}


def write_alpaca_paper_submit_one(latest_dir: str | Path, broker_snapshot: ReadOnlyBrokerSnapshot | None, **kwargs) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    result = build_alpaca_paper_submit_one(latest, broker_snapshot, **kwargs)
    csv_path = latest / "alpaca_paper_submit_one_gates.csv"
    audit_path = latest / "alpaca_paper_submit_one_audit.csv"
    md_path = latest / "alpaca_paper_submit_one_audit.md"
    json_path = latest / "alpaca_paper_submit_one_audit.json"
    result["checks"].to_csv(csv_path, index=False)
    result["audit"].to_csv(audit_path, index=False)
    payload = {
        "summary": result["summary"],
        "checks": _records(result["checks"]),
        "client_order_preview": _records(result["client_order_preview"]),
        "audit": _records(result["audit"]),
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Alpaca Paper Submit One Audit",
                "",
                "## Summary",
                "",
                _metric_table(result["summary"]),
                "",
                "## Gates",
                "",
                _markdown_table(result["checks"]),
                "",
                "## Audit",
                "",
                _markdown_table(result["audit"]),
                "",
                "## Timeout Policy",
                "",
                "- On timeout or unknown submit result, do not retry.",
                "- Read back by deterministic client_order_id before any future retry decision.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "summary": result["summary"],
        "csv_path": str(csv_path),
        "audit_path": str(audit_path),
        "md_path": str(md_path),
        "json_path": str(json_path),
    }
