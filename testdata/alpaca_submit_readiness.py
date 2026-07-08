from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

try:
    from .alpaca_paper_snapshot import alpaca_endpoint_mode
    from .broker_reconciliation import ReadOnlyBrokerSnapshot, _clean_text, _markdown_table, _metric_table, _records
except ImportError:
    from alpaca_paper_snapshot import alpaca_endpoint_mode
    from broker_reconciliation import ReadOnlyBrokerSnapshot, _clean_text, _markdown_table, _metric_table, _records


READINESS_COLUMNS = ["section", "check_name", "expected", "actual", "status", "reason"]
CLIENT_ORDER_PREVIEW_COLUMNS = [
    "symbol",
    "side",
    "type",
    "time_in_force",
    "client_order_id",
    "notional",
    "qty",
    "extended_hours",
    "qty_notional_exclusive",
    "would_submit",
]
DOC_RULES = [
    "deterministic_client_order_id_required",
    "timeout_unknown_result_requires_broker_order_readback_before_retry",
    "max_one_order_until_explicit_expansion",
    "extended_hours_false_until_explicit_approval",
    "qty_and_notional_mutually_exclusive",
    "check_account_positions_orders_assets_buying_power_before_submit",
    "check_open_order_and_wash_trade_conflicts_before_submit",
]


def _check(section: str, name: str, expected, actual, status: str, reason: str) -> dict[str, object]:
    return {
        "section": section,
        "check_name": name,
        "expected": expected,
        "actual": actual,
        "status": status,
        "reason": reason,
    }


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def deterministic_client_order_id(row: dict[str, object]) -> str:
    source = (
        _clean_text(row.get("execution_idempotency_key"))
        or _clean_text(row.get("idempotency_key"))
        or _clean_text(row.get("order_intent_id"))
        or "|".join(_clean_text(row.get(key)) for key in ["strategy_name", "symbol", "side", "source_signal_date"])
    )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:40]
    return f"fpap-{digest}"


def _candidate_rows(latest: Path) -> pd.DataFrame:
    intents = _read_csv(latest / "order_intents.csv")
    if intents.empty:
        return pd.DataFrame()
    risk = _read_csv(latest / "order_risk_check_report.csv")
    candidates = intents.copy()
    if not risk.empty and "execution_idempotency_key" in risk and "execution_idempotency_key" in candidates:
        risk_cols = ["execution_idempotency_key"]
        for column in ["risk_pass", "block_reason"]:
            if column in risk:
                risk_cols.append(column)
        candidates = candidates.merge(risk[risk_cols], on="execution_idempotency_key", how="left")
    if "risk_pass" in candidates:
        mask = candidates["risk_pass"].astype(str).str.lower().isin({"true", "1", "yes"})
        candidates = candidates[mask].copy()
    if "status" in candidates:
        candidates = candidates[~candidates["status"].astype(str).str.upper().str.contains("DUPLICATE|BLOCK", na=False)].copy()
    return candidates.reset_index(drop=True)


def _preview_payloads(candidates: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in candidates.iterrows():
        row_dict = row.to_dict()
        notional = pd.to_numeric(pd.Series([row_dict.get("notional")]), errors="coerce").iloc[0]
        qty = pd.to_numeric(pd.Series([row_dict.get("estimated_qty")]), errors="coerce").iloc[0]
        use_notional = pd.notna(notional) and float(notional) > 0
        use_qty = not use_notional and pd.notna(qty) and float(qty) > 0
        rows.append(
            {
                "symbol": _clean_text(row_dict.get("symbol")).upper(),
                "side": _clean_text(row_dict.get("side")).lower(),
                "type": _clean_text(row_dict.get("order_type")) or "market",
                "time_in_force": _clean_text(row_dict.get("time_in_force")) or "day",
                "client_order_id": deterministic_client_order_id(row_dict),
                "notional": float(notional) if use_notional else "",
                "qty": float(qty) if use_qty else "",
                "extended_hours": False,
                "qty_notional_exclusive": bool(use_notional) != bool(use_qty),
                "would_submit": False,
            }
        )
    return pd.DataFrame(rows, columns=CLIENT_ORDER_PREVIEW_COLUMNS)


def _number(value, default=0.0) -> float:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(parsed) if pd.notna(parsed) else float(default)


def _opposite(side: str) -> str:
    text = str(side or "").lower()
    if text == "buy":
        return "sell"
    if text == "sell":
        return "buy"
    return ""


def _open_order_conflicts(candidates: pd.DataFrame, broker_snapshot: ReadOnlyBrokerSnapshot) -> tuple[int, int]:
    if candidates.empty:
        return 0, 0
    open_orders = broker_snapshot.open_orders if broker_snapshot else []
    conflict_count = 0
    wash_count = 0
    for _, candidate in candidates.iterrows():
        symbol = _clean_text(candidate.get("symbol")).upper()
        side = _clean_text(candidate.get("side")).lower()
        for order in open_orders:
            order_symbol = _clean_text(order.get("symbol")).upper()
            order_side = _clean_text(order.get("side")).lower()
            if symbol and symbol == order_symbol:
                conflict_count += 1
                if order_side == _opposite(side):
                    wash_count += 1
    return conflict_count, wash_count


def _asset_checks(preview: pd.DataFrame, assets: dict[str, object]) -> tuple[int, int]:
    missing = 0
    not_tradable = 0
    for symbol in preview.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().tolist():
        asset = assets.get(symbol)
        if not asset:
            missing += 1
        elif not bool(asset.get("tradable", False)):
            not_tradable += 1
    return missing, not_tradable


def build_alpaca_paper_submit_readiness(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None,
    *,
    base_url: str = "https://paper-api.alpaca.markets",
    assets: dict[str, object] | None = None,
    assets_metadata: dict[str, object] | None = None,
    max_submit_candidates: int = 1,
) -> dict[str, object]:
    latest = Path(latest_dir)
    baseline_path = latest / "broker_cutover_baseline.json"
    baseline_exists = baseline_path.exists()
    candidates = _candidate_rows(latest)
    preview = _preview_payloads(candidates)
    metadata = broker_snapshot.metadata if broker_snapshot else {}
    snapshot_status = str(metadata.get("alpaca_snapshot_status", "NOT_REQUESTED")).upper()
    endpoint_mode = alpaca_endpoint_mode(base_url)
    account = metadata.get("alpaca_account_summary", {}) if isinstance(metadata.get("alpaca_account_summary", {}), dict) else {}
    buying_power = _number(account.get("buying_power", 0.0))
    required_notional = float(pd.to_numeric(preview.get("notional", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if not preview.empty else 0.0
    open_conflicts, wash_conflicts = _open_order_conflicts(preview, broker_snapshot)
    assets = assets or {}
    assets_metadata = assets_metadata or {}
    missing_assets, not_tradable_assets = _asset_checks(preview, assets)
    candidate_count = int(len(preview))

    checks = [
        _check("baseline", "broker_cutover_baseline_exists", True, baseline_exists, "PASS" if baseline_exists else "BLOCK", "explicit_forward_only_baseline_required"),
        _check("alpaca_snapshot", "alpaca_get_only_status", "PASS", snapshot_status, "PASS" if snapshot_status == "PASS" else "BLOCK", metadata.get("alpaca_snapshot_reason", "")),
        _check("alpaca_snapshot", "paper_endpoint_only", "paper", endpoint_mode, "PASS" if endpoint_mode == "paper" else "BLOCK", "paper_endpoint_required"),
        _check("submit_gate", "broker_submit_enabled", False, False, "BLOCK", "future_explicit_submit_enable_required"),
        _check("submit_gate", "max_submit_candidates", "<=1", candidate_count, "PASS" if candidate_count <= max_submit_candidates else "BLOCK", "one_order_cap"),
        _check("submit_gate", "extended_hours", False, False, "PASS", "extended_hours_false_until_approved"),
        _check("submit_gate", "qty_notional_exclusive", True, bool(preview.empty or preview["qty_notional_exclusive"].all()), "PASS" if preview.empty or preview["qty_notional_exclusive"].all() else "BLOCK", "do_not_send_qty_and_notional_together"),
        _check("buying_power", "buying_power_available", ">= required_notional", buying_power, "PASS" if required_notional <= buying_power else "BLOCK", f"required_notional={required_notional:.2f}"),
        _check("open_orders", "open_order_conflict_count", 0, open_conflicts, "PASS" if open_conflicts == 0 else "BLOCK", "existing_open_order_same_symbol"),
        _check("open_orders", "wash_trade_conflict_count", 0, wash_conflicts, "PASS" if wash_conflicts == 0 else "BLOCK", "opposite_side_open_order_same_symbol"),
        _check("assets", "assets_get_only_status", "PASS", bool(assets_metadata.get("alpaca_assets_read_success", True)), "PASS" if assets_metadata.get("alpaca_assets_read_success", True) else "WARNING", assets_metadata.get("alpaca_assets_failure_category", "")),
        _check("assets", "missing_asset_count", 0, missing_assets, "PASS" if missing_assets == 0 else "BLOCK", "asset_lookup_required_for_candidates"),
        _check("assets", "not_tradable_asset_count", 0, not_tradable_assets, "PASS" if not_tradable_assets == 0 else "BLOCK", "asset_must_be_tradable"),
        _check("timeout_policy", "unknown_submit_result_policy", "readback_before_retry", "readback_before_retry", "PASS", "never_resend_blindly_after_timeout"),
        _check("safety", "submit_order_called", False, False, "PASS", "not_called"),
        _check("safety", "post_request_used", False, False, "PASS", "not_used"),
        _check("safety", "live_trading_enabled", False, False, "PASS", "disabled"),
        _check("safety", "short_trading_enabled", False, False, "PASS", "disabled"),
    ]
    checks_frame = pd.DataFrame(checks, columns=READINESS_COLUMNS)
    statuses = checks_frame["status"].astype(str).str.upper()
    readiness_status = "BLOCK" if statuses.isin(["BLOCK", "FAIL"]).any() else ("WARNING" if statuses.eq("WARNING").any() else "PASS")
    summary = {
        "submit_readiness_status": readiness_status,
        "doc_rules_applied": ",".join(DOC_RULES),
        "broker_cutover_baseline_exists": baseline_exists,
        "broker_submit_enabled": False,
        "max_submit_candidates": max_submit_candidates,
        "submit_candidate_count": candidate_count,
        "alpaca_snapshot_status": snapshot_status,
        "alpaca_account_read_success": bool(metadata.get("alpaca_account_read_success", False)),
        "alpaca_positions_read_success": bool(metadata.get("alpaca_positions_read_success", False)),
        "alpaca_open_orders_read_success": bool(metadata.get("alpaca_open_orders_read_success", False)),
        "paper_endpoint_mode": endpoint_mode,
        "buying_power": buying_power,
        "required_notional": required_notional,
        "open_order_conflict_count": open_conflicts,
        "wash_trade_conflict_count": wash_conflicts,
        "asset_check_count": len(assets),
        "missing_asset_count": missing_assets,
        "not_tradable_asset_count": not_tradable_assets,
        "timeout_policy": "READBACK_BEFORE_RETRY",
        "submit_order_called": False,
        "post_request_used": False,
        "live_trading_enabled": False,
        "short_trading_enabled": False,
        "state_mutated": False,
    }
    return {"summary": summary, "checks": checks_frame, "client_order_preview": preview}


def write_alpaca_paper_submit_readiness(
    latest_dir: str | Path,
    broker_snapshot: ReadOnlyBrokerSnapshot | None,
    **kwargs,
) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    result = build_alpaca_paper_submit_readiness(latest, broker_snapshot, **kwargs)
    csv_path = latest / "alpaca_paper_submit_readiness.csv"
    md_path = latest / "alpaca_paper_submit_readiness.md"
    json_path = latest / "alpaca_paper_submit_readiness.json"
    preview_path = latest / "alpaca_paper_submit_client_order_preview.csv"
    result["checks"].to_csv(csv_path, index=False)
    result["client_order_preview"].to_csv(preview_path, index=False)
    payload = {
        "summary": result["summary"],
        "checks": _records(result["checks"]),
        "client_order_preview": _records(result["client_order_preview"]),
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Alpaca Paper Submit Readiness",
                "",
                "## Summary",
                "",
                _metric_table(result["summary"]),
                "",
                "## Checks",
                "",
                _markdown_table(result["checks"]),
                "",
                "## Client Order ID Preview",
                "",
                _markdown_table(result["client_order_preview"]),
                "",
                "## Timeout Policy",
                "",
                "- If submit timeout or result unknown occurs, do not resend blindly.",
                "- First read broker open orders/by client_order_id and reconcile before any retry.",
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
        "preview_path": str(preview_path),
    }
