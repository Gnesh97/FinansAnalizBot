from __future__ import annotations

import json
import math
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
    from .broker_ledger import append_order_to_ledger, find_order_by_client_order_id
    from .broker_lifecycle import ALPACA_LIFECYCLE_MAP
    from .broker_submit_recovery import SubmitIntentError, SubmitIntentStore
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
    from broker_ledger import append_order_to_ledger, find_order_by_client_order_id
    from broker_lifecycle import ALPACA_LIFECYCLE_MAP
    from broker_submit_recovery import SubmitIntentError, SubmitIntentStore


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
    candidate_symbols_valid = bool(
        candidate_count == 1
        and preview["symbol"].astype(str).str.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}").all()
    )
    candidate_sides_valid = bool(
        candidate_count == 1 and preview["side"].astype(str).str.lower().eq("buy").all()
    )
    deterministic_client_order_id = bool(
        candidate_count == 1
        and preview["client_order_id"].astype(str).str.fullmatch(r"fpap-[0-9a-f]{40}").all()
    )

    checks = [
        _check("baseline", "broker_cutover_baseline_exists", True, baseline_exists, "PASS" if baseline_exists else "BLOCK", "explicit_forward_only_baseline_required"),
        _check("alpaca_snapshot", "alpaca_get_only_status", "PASS", snapshot_status, "PASS" if snapshot_status == "PASS" else "BLOCK", metadata.get("alpaca_snapshot_reason", "")),
        _check("alpaca_snapshot", "paper_endpoint_only", "paper", endpoint_mode, "PASS" if endpoint_mode == "paper" else "BLOCK", "paper_endpoint_required"),
        _check("submit_gate", "broker_submit_enabled", True, broker_submit_enabled, "PASS" if broker_submit_enabled else "BLOCK", "explicit_enable_required"),
        _check("submit_gate", "confirm_token", CONFIRM_TOKEN, "provided" if confirm_token else "missing", "PASS" if confirm_ok else "BLOCK", "operator_confirm_token_required"),
        _check("submit_gate", "max_submit_candidates", 1, candidate_count, "PASS" if candidate_count == 1 else "BLOCK", "exactly_one_candidate_required"),
        _check("submit_gate", "candidate_symbol_valid", True, candidate_symbols_valid, "PASS" if candidate_symbols_valid else "BLOCK", "selected_candidate_symbol_required"),
        _check("submit_gate", "candidate_side_long_only", "buy", "buy" if candidate_sides_valid else "invalid", "PASS" if candidate_sides_valid else "BLOCK", "long_only_paper_submit"),
        _check("submit_gate", "deterministic_client_order_id", True, deterministic_client_order_id, "PASS" if deterministic_client_order_id else "BLOCK", "derived_from_selected_candidate"),
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
        "candidate_symbol_valid": candidate_symbols_valid,
        "candidate_side_long_only": candidate_sides_valid,
        "deterministic_client_order_id": deterministic_client_order_id,
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


def _finite_non_negative(value, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return number


def _validate_broker_evidence(payload: object, order: dict[str, object]) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("response is not a dict")
    client_order_id = str(payload.get("client_order_id", "")).strip()
    if client_order_id != str(order["client_order_id"]):
        raise ValueError("client_order_id mismatch")
    broker_order_id = str(payload.get("id", payload.get("broker_order_id", ""))).strip()
    if not broker_order_id:
        raise ValueError("broker order id is missing")
    raw_status = str(payload.get("status", "")).strip().lower()
    if raw_status not in ALPACA_LIFECYCLE_MAP:
        raise ValueError("broker status is not recognized")
    if str(payload.get("symbol", "")).strip().upper() != str(order["symbol"]).upper():
        raise ValueError("symbol mismatch")
    if str(payload.get("side", "")).strip().lower() != str(order["side"]).lower():
        raise ValueError("side mismatch")
    quantities: dict[str, float] = {}
    for field in ("qty", "notional", "filled_qty"):
        if field not in payload:
            if field == "filled_qty":
                raise ValueError("filled_qty is missing")
            continue
        quantities[field] = _finite_non_negative(payload[field], field)
    if not {"qty", "notional"}.intersection(quantities):
        raise ValueError("qty or notional is missing")
    return {
        "broker_order_id": broker_order_id,
        "raw_status": raw_status,
        "status": ALPACA_LIFECYCLE_MAP[raw_status],
        "qty": quantities.get("qty", 0.0),
        "notional": quantities.get("notional", 0.0),
        "filled_qty": quantities["filled_qty"],
    }


def _lookup_broker(client, order: dict[str, object]) -> tuple[str, dict[str, object] | None, str]:
    try:
        response = client.read_order_by_client_order_id(str(order["client_order_id"]))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return "NOT_FOUND", None, "broker_not_found"
        return "UNCERTAIN", None, _sanitize_error(error)
    except LookupError:
        return "NOT_FOUND", None, "broker_not_found"
    except Exception as error:
        return "UNCERTAIN", None, _sanitize_error(error)
    if response is None:
        return "NOT_FOUND", None, "broker_not_found"
    try:
        return "CONFIRMED", _validate_broker_evidence(response, order), "broker_confirmed"
    except ValueError as error:
        return "UNCERTAIN", None, f"invalid_broker_evidence:{error}"


def _commit_confirmed_order(store: SubmitIntentStore, latest: Path, order: dict[str, object], evidence: dict[str, object]) -> tuple[bool, str]:
    client_order_id = str(order["client_order_id"])
    try:
        store.transition(client_order_id, "BROKER_CONFIRMED", evidence={"event": "broker_confirmed", "broker_order_id": evidence["broker_order_id"]})
        append_order_to_ledger(
            latest_dir=latest,
            client_order_id=client_order_id,
            broker_order_id=str(evidence["broker_order_id"]),
            symbol=str(order["symbol"]),
            side=str(order["side"]),
            notional=float(evidence["notional"]),
            qty=float(evidence["qty"]),
            status=str(evidence["status"]),
            readback_status="PASS",
            broker_position_match=False,
            filled_qty=float(evidence["filled_qty"]),
        )
        store.transition(client_order_id, "LEDGER_COMMITTED", evidence={"event": "ledger_committed", "broker_order_id": evidence["broker_order_id"]})
        return True, "ledger_committed"
    except Exception as error:
        try:
            store.transition(client_order_id, "AMBIGUOUS_RESULT", evidence={"event": "ledger_append_failed", "reason": _sanitize_error(error)})
        except SubmitIntentError:
            pass
        return False, f"ledger_append_failed:{_sanitize_error(error)}"


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

    payload = _order_payload(preview.iloc[0]) if not preview.empty else {}
    if gates_pass:
        client = submit_client or AlpacaPaperOrderSubmitClient(api_key=api_key, secret_key=secret_key, base_url=base_url)
        store = SubmitIntentStore(latest)
        client_order_id = str(payload["client_order_id"])
        try:
            durable = find_order_by_client_order_id(latest, client_order_id)
            if durable is not None:
                submit_status = "DUPLICATE_DURABLE_LEDGER"
                submit_reason = "durable_client_order_id_exists"
                broker_order_id = str(durable.get("broker_order_id", ""))
            else:
                existing = store.get(client_order_id)
                lookup_state, evidence, lookup_reason = _lookup_broker(client, payload)
                readback_attempted = True
                readback_status = "PASS" if lookup_state == "CONFIRMED" else lookup_state
                if lookup_state == "CONFIRMED":
                    if existing is None:
                        store.prepare({**payload, "endpoint": f"{str(base_url).rstrip('/')}/v2/orders"})
                    broker_order_id = str(evidence["broker_order_id"])
                    committed, commit_reason = _commit_confirmed_order(store, latest, payload, evidence)
                    submit_status = "BROKER_CONFIRMED_LEDGER_COMMITTED" if committed else "AMBIGUOUS_RESULT"
                    submit_reason = commit_reason
                elif lookup_state == "UNCERTAIN":
                    submit_status = "BLOCKED_BROKER_LOOKUP"
                    submit_reason = lookup_reason
                    if existing is not None:
                        store.transition(client_order_id, "BLOCKED", evidence={"event": "uncertain_get", "reason": lookup_reason})
                elif existing is not None and str(existing["recovery_state"]) in {"AMBIGUOUS_RESULT", "POST_IN_PROGRESS", "BROKER_CONFIRMED", "LEDGER_COMMITTED", "BLOCKED"}:
                    submit_status = "BLOCKED_AMBIGUOUS_RECOVERY"
                    submit_reason = f"unresolved_intent:{existing['recovery_state']}"
                else:
                    if existing is None:
                        store.prepare({**payload, "endpoint": f"{str(base_url).rstrip('/')}/v2/orders"})
                    store.transition(client_order_id, "POST_IN_PROGRESS", evidence={"event": "post_started"})
                    try:
                        submit_order_called = True
                        post_request_used = True
                        response = client.post_order(payload)
                    except Exception as error:
                        submit_reason = f"post_unknown_no_retry:{_sanitize_error(error)}"
                        lookup_state, evidence, lookup_reason = _lookup_broker(client, payload)
                        readback_attempted = True
                        readback_status = "PASS" if lookup_state == "CONFIRMED" else lookup_state
                        if lookup_state == "CONFIRMED":
                            broker_order_id = str(evidence["broker_order_id"])
                            committed, commit_reason = _commit_confirmed_order(store, latest, payload, evidence)
                            submit_status = "BROKER_CONFIRMED_LEDGER_COMMITTED" if committed else "AMBIGUOUS_RESULT"
                            submit_reason = commit_reason if committed else submit_reason + ";" + commit_reason
                        else:
                            store.transition(client_order_id, "AMBIGUOUS_RESULT", evidence={"event": "post_exception", "reason": submit_reason, "readback": lookup_reason})
                            submit_status = "AMBIGUOUS_RESULT"
                    else:
                        if (
                            not isinstance(response, dict)
                            or not str(response.get("id", response.get("broker_order_id", ""))).strip()
                            or str(response.get("client_order_id", "")).strip() != client_order_id
                        ):
                            store.transition(client_order_id, "POST_IN_PROGRESS", evidence={"event": "malformed_post_response"})
                        lookup_state, evidence, lookup_reason = _lookup_broker(client, payload)
                        readback_attempted = True
                        readback_status = "PASS" if lookup_state == "CONFIRMED" else lookup_state
                        if lookup_state == "CONFIRMED":
                            broker_order_id = str(evidence["broker_order_id"])
                            committed, commit_reason = _commit_confirmed_order(store, latest, payload, evidence)
                            submit_status = "SUBMITTED_READBACK_OK" if committed else "AMBIGUOUS_RESULT"
                            submit_reason = "api_submit_success_readback_ok" if committed else commit_reason
                        else:
                            store.transition(client_order_id, "AMBIGUOUS_RESULT", evidence={"event": "post_success_readback_failed", "reason": lookup_reason})
                            submit_status = "AMBIGUOUS_RESULT"
                            submit_reason = f"post_success_readback_failed:{lookup_reason}"
        except SubmitIntentError as error:
            submit_status = "BLOCKED_INTENT_JOURNAL"
            submit_reason = _sanitize_error(error)
        except Exception as error:
            submit_status = "BLOCKED_DURABLE_STATE"
            submit_reason = _sanitize_error(error)

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
            "state_mutated": bool(gates_pass and (submit_order_called or submit_status in {"BROKER_CONFIRMED_LEDGER_COMMITTED", "AMBIGUOUS_RESULT"})),
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
