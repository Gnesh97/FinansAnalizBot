"""Alpaca paper-trading and broker reconciliation reporting.

Extracted from testdata/runner.py (Phase 14, Cluster 1) to shrink that file.
Behavior-preserving move -- function bodies are unchanged, only their file
and a handful of now-necessary imports moved. This is dry-run/paper-only
reporting: no order is ever actually submitted (ALPACA_ORDER_PREVIEW rows
are always `would_submit: False`, and submit gating stays enforced inside
the imported write_alpaca_paper_submit_one). None of this is reachable from
the live nightly DSS chain (testdata/dss_scheduler.py documents "Report/
refresh CLIs only -- no broker entry points"); the only production caller is
bot.py's `alpaca-check` CLI mode, which calls run_alpaca_paper_check.

This module calls four runner.py functions that were not part of this
extraction (_read_env_file, _paper_order_key, _load_paper_order_review,
_report_output_paths) -- those are imported locally, inside the functions
that need them, specifically to avoid a circular top-level import (runner.py
imports this module at load time; this module must not import runner.py at
load time in return). _read_env_file in particular stays in runner.py
because testdata/alpaca_inactive_asset_feasibility.py already imports it
from there.

Conversely, runner.py imports several names back from here: bot.py imports
run_alpaca_paper_check directly from testdata.runner, several tests import
run_broker_reconciliation / run_alpaca_paper_submit_readiness /
run_alpaca_paper_submit_one / _write_alpaca_order_preview /
run_broker_cutover_readiness / run_create_broker_cutover_baseline the same
way, and runner.py's own _fetch_pending_raw_5min calls _alpaca_config.

run_alpaca_paper_check talks to Alpaca via the alpaca-py SDK's TradingClient
(Phase 18) rather than a hand-rolled urllib call -- alpaca-py was already a
declared project dependency and already used for market-data fetching
elsewhere (runner.py's _fetch_pending_raw_5min); this made the account/
positions/orders read path consistent with that instead of being its own
one-off REST client. The broker-snapshot path used by every other function
in this module (alpaca_paper_snapshot.py's AlpacaPaperSnapshotClient) is a
deliberately separate, more heavily safety-engineered client (read-only
enforcement, credential redaction in errors, endpoint-mode gating) and was
NOT migrated -- that would mean rebuilding those guarantees against the
SDK's own exception surface, a larger and more careful undertaking left for
a future phase.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

try:
    from .report_schemas import ALPACA_ORDER_PREVIEW_COLUMNS, ensure_columns
    from .paper_state_manager import read_pending_orders
    from .chatgpt_master_report import _chatgpt_useful_commands_section
    from .broker_reconciliation import write_broker_reconciliation
    from .broker_cutover_baseline import create_broker_cutover_baseline, write_broker_cutover_readiness
    from .alpaca_paper_snapshot import read_alpaca_paper_assets, read_alpaca_paper_snapshot
    from .alpaca_submit_readiness import write_alpaca_paper_submit_readiness
    from .alpaca_paper_submit_one import write_alpaca_paper_submit_one
except ImportError:
    from report_schemas import ALPACA_ORDER_PREVIEW_COLUMNS, ensure_columns
    from paper_state_manager import read_pending_orders
    from chatgpt_master_report import _chatgpt_useful_commands_section
    from broker_reconciliation import write_broker_reconciliation
    from broker_cutover_baseline import create_broker_cutover_baseline, write_broker_cutover_readiness
    from alpaca_paper_snapshot import read_alpaca_paper_assets, read_alpaca_paper_snapshot
    from alpaca_submit_readiness import write_alpaca_paper_submit_readiness
    from alpaca_paper_submit_one import write_alpaca_paper_submit_one


def _alpaca_config(env_path=None):
    try:
        from .runner import _read_env_file
    except ImportError:
        from runner import _read_env_file
    env_values = _read_env_file(env_path or Path.cwd() / ".env")
    api_key = os.environ.get("ALPACA_API_KEY") or env_values.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY") or env_values.get("ALPACA_SECRET_KEY")
    base_url = (
        os.environ.get("ALPACA_BASE_URL")
        or env_values.get("ALPACA_BASE_URL")
        or "https://paper-api.alpaca.markets"
    ).rstrip("/")
    return api_key, secret_key, base_url


def _alpaca_report_lines(summary):
    warnings = summary.get("warnings") or []
    return [
        "# Alpaca Paper Check Report",
        "",
        f"- connection_status: {summary.get('connection_status', 'unknown')}",
        f"- endpoint: {summary.get('endpoint', '')}",
        f"- account_status: {summary.get('account_status', '')}",
        f"- buying_power: {summary.get('buying_power', '')}",
        f"- cash: {summary.get('cash', '')}",
        f"- equity: {summary.get('equity', '')}",
        f"- portfolio_value: {summary.get('portfolio_value', '')}",
        f"- pattern_day_trader: {summary.get('pattern_day_trader', '')}",
        f"- open_positions_count: {summary.get('open_positions_count', 0)}",
        f"- open_orders_count: {summary.get('open_orders_count', 0)}",
        f"- warnings: {warnings if warnings else 'none'}",
        f"- next action: {summary.get('next_action', '')}",
        "",
        "Safety: dry-run only; no submit_order call is made.",
        "",
    ]


def _paper_order_review_decisions(latest_dir):
    try:
        from .runner import _load_paper_order_review, _paper_order_key
    except ImportError:
        from runner import _load_paper_order_review, _paper_order_key
    review = _load_paper_order_review(Path(latest_dir) / "paper_order_review.csv")
    decisions = {}
    for _, row in review.iterrows():
        decisions[_paper_order_key(row)] = str(row.get("user_decision", "HOLD_REVIEW")).strip().upper()
    return decisions


def _paper_signal_lookup(latest_dir):
    try:
        from .runner import _paper_order_key
    except ImportError:
        from runner import _paper_order_key
    path = latest_dir / "paper_signal_report.csv"
    if not path.exists():
        return {}
    try:
        signals = pd.read_csv(path)
    except Exception:
        return {}
    lookup = {}
    for _, row in signals.iterrows():
        lookup[_paper_order_key(row)] = row
    return lookup


def _write_alpaca_order_preview(latest_dir, output_path):
    try:
        from .runner import _paper_order_key
    except ImportError:
        from runner import _paper_order_key
    pending_path = latest_dir / "paper_pending_orders.csv"
    rows = []
    if pending_path.exists():
        pending = read_pending_orders(latest_dir)
        decisions = _paper_order_review_decisions(latest_dir)
        signal_lookup = _paper_signal_lookup(latest_dir)
        for _, row in pending.iterrows():
            key = _paper_order_key(row)
            decision = str(row.get("user_decision", "REVIEW_REQUIRED")).strip().upper()
            if decision == "REVIEW_REQUIRED":
                decision = decisions.get(key, decision)

            fill_status = str(row.get("fill_status", "")).strip().upper()
            signal = None
            if not fill_status:
                signal = signal_lookup.get(key)
                fill_status = str(signal.get("fill_status", "")).strip().upper() if signal is not None else ""

            if decision != "ACCEPT" or fill_status != "PENDING_NEXT_OPEN":
                continue

            price = pd.to_numeric(
                pd.Series([row.get("entry_price_reference", pd.NA)]),
                errors="coerce",
            ).iloc[0]
            if pd.isna(price):
                if signal is None:
                    signal = signal_lookup.get(key)
                price = pd.to_numeric(
                    pd.Series([signal.get("entry_price_reference", pd.NA) if signal is not None else pd.NA]),
                    errors="coerce",
                ).iloc[0]
            notional = pd.to_numeric(
                pd.Series([row.get("paper_position_notional", pd.NA)]),
                errors="coerce",
            ).iloc[0]
            estimated_qty = round(float(notional) / float(price), 6) if pd.notna(price) and pd.notna(notional) and float(price) > 0 else pd.NA
            rows.append(
                {
                    "symbol": row.get("symbol", ""),
                    "action": row.get("action", ""),
                    "status": "DRY_RUN_ONLY",
                    "paper_position_notional": row.get("paper_position_notional", pd.NA),
                    "estimated_qty": estimated_qty,
                    "order_type": "market",
                    "time_in_force": "day",
                    "would_submit": False,
                    "reason": "accepted_local_pending_order_pending_next_open",
                }
            )
    preview = ensure_columns(pd.DataFrame(rows), ALPACA_ORDER_PREVIEW_COLUMNS)
    preview.to_csv(output_path, index=False)
    return preview


def _write_alpaca_master_section(latest_dir, summary, preview):
    path = latest_dir / "chatgpt_master_report.md"
    section = [
        "## Alpaca Paper Readiness",
        "",
        f"- alpaca connection status: {summary.get('connection_status', 'unknown')}",
        f"- account status: {summary.get('account_status', '')}",
        f"- buying power: {summary.get('buying_power', '')}",
        f"- open positions count: {summary.get('open_positions_count', 0)}",
        f"- open orders count: {summary.get('open_orders_count', 0)}",
        f"- pending local paper orders count: {len(preview)}",
        "- dry-run order preview path: reports/latest/alpaca_order_preview.csv",
        "- live trading enabled: false",
        "- order submit enabled: false",
        "",
    ]
    if path.exists():
        text = path.read_text(encoding="utf-8")
        marker = "## Alpaca Paper Readiness"
        if marker in text:
            text = text[: text.index(marker)].rstrip() + "\n\n"
    else:
        text = "# ChatGPT Master Report\n\n"
    if "## Useful Commands" not in text:
        text = text.rstrip() + "\n\n" + "\n".join(_chatgpt_useful_commands_section())
    path.write_text(text.rstrip() + "\n\n" + "\n".join(section), encoding="utf-8")


def run_alpaca_paper_check(data_folder="hisseler", report_output_dir="reports", make_chatgpt_report=False):
    try:
        from .runner import _report_output_paths
    except ImportError:
        from runner import _report_output_paths
    latest_dir, run_dir = _report_output_paths(report_output_dir, data_folder)
    env_path = Path(data_folder).resolve().parents[1] / ".env"
    if not env_path.exists():
        env_path = Path.cwd() / ".env"
    api_key, secret_key, base_url = _alpaca_config(env_path)
    summary = {
        "connection_status": "not_checked",
        "endpoint": base_url,
        "account_status": "",
        "buying_power": "",
        "cash": "",
        "equity": "",
        "portfolio_value": "",
        "pattern_day_trader": "",
        "open_positions_count": 0,
        "open_orders_count": 0,
        "warnings": [],
        "next_action": "Resolve warnings, then rerun paper check.",
    }
    if "paper-api" not in base_url:
        summary["connection_status"] = "refused"
        summary["warnings"].append("Refusing Alpaca connection: paper endpoint required.")
    elif not api_key or not secret_key:
        summary["connection_status"] = "missing_credentials"
        summary["warnings"].append("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY.")
    else:
        try:
            try:
                from alpaca.trading.client import TradingClient
                from alpaca.trading.enums import QueryOrderStatus
                from alpaca.trading.requests import GetOrdersRequest
            except ModuleNotFoundError as import_error:
                if import_error.name != "alpaca":
                    raise
                raise RuntimeError("missing alpaca-py; install with python -m pip install alpaca-py") from import_error

            client = TradingClient(api_key, secret_key, paper=True)
            account = client.get_account()
            positions = client.get_all_positions()
            orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100))
            summary.update(
                {
                    "connection_status": "ok",
                    "account_status": account.status.value if account.status is not None else "",
                    "buying_power": account.buying_power or "",
                    "cash": account.cash or "",
                    "equity": account.equity or "",
                    "portfolio_value": account.portfolio_value or "",
                    "pattern_day_trader": account.pattern_day_trader,
                    "open_positions_count": len(positions) if isinstance(positions, list) else 0,
                    "open_orders_count": len(orders) if isinstance(orders, list) else 0,
                    "next_action": "Review dry-run order preview; no broker orders submitted.",
                }
            )
        except Exception as error:
            summary["connection_status"] = "failed"
            summary["warnings"].append(str(error))
    preview = _write_alpaca_order_preview(latest_dir, latest_dir / "alpaca_order_preview.csv")
    for directory in [latest_dir, run_dir]:
        (directory / "alpaca_paper_check_report.md").write_text(
            "\n".join(_alpaca_report_lines(summary)),
            encoding="utf-8",
        )
        if directory != latest_dir:
            preview.to_csv(directory / "alpaca_order_preview.csv", index=False)
    if make_chatgpt_report:
        _write_alpaca_master_section(latest_dir, summary, preview)
        _write_alpaca_master_section(run_dir, summary, preview)
    print(f"Alpaca paper check report: {latest_dir / 'alpaca_paper_check_report.md'}")
    print(f"Alpaca order preview: {latest_dir / 'alpaca_order_preview.csv'}")
    print("Order submit enabled: false")
    return summary, preview


def run_broker_reconciliation(data_folder="hisseler", report_output_dir="reports", alpaca_paper_snapshot_readonly=False):
    try:
        from .runner import _report_output_paths
    except ImportError:
        from runner import _report_output_paths
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    broker_snapshot = None
    if alpaca_paper_snapshot_readonly:
        env_path = Path(data_folder).resolve().parents[1] / ".env"
        if not env_path.exists():
            env_path = Path.cwd() / ".env"
        api_key, secret_key, base_url = _alpaca_config(env_path)
        broker_snapshot = read_alpaca_paper_snapshot(
            api_key=api_key,
            secret_key=secret_key,
            base_url=base_url,
            broker_submit_enabled=False,
            live_trading_enabled=False,
        )
    result = write_broker_reconciliation(latest_dir, broker_snapshot=broker_snapshot)
    summary = result.get("summary", {})
    print(f"Broker reconciliation: {latest_dir / 'broker_reconciliation.md'}")
    print(f"Broker reconciliation csv: {latest_dir / 'broker_reconciliation.csv'}")
    print(f"Broker reconciliation json: {latest_dir / 'broker_reconciliation.json'}")
    print(f"reconciliation_status: {summary.get('reconciliation_status', '')}")
    print(f"broker_snapshot_source: {summary.get('broker_snapshot_source', '')}")
    return result


def run_broker_order_lifecycle_monitor(data_folder="hisseler", report_output_dir="reports", *, lifecycle_write_authorized=False):
    try:
        from .runner import _report_output_paths
    except ImportError:
        from runner import _report_output_paths
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    env_path = Path(data_folder).resolve().parents[1] / ".env"
    if not env_path.exists():
        env_path = Path.cwd() / ".env"
    api_key, secret_key, base_url = _alpaca_config(env_path)
    broker_snapshot = read_alpaca_paper_snapshot(
        api_key=api_key,
        secret_key=secret_key,
        base_url=base_url,
        allow_state_write=lifecycle_write_authorized,
        broker_submit_enabled=False,
        live_trading_enabled=False,
    )
    try:
        from .broker_lifecycle import check_broker_order_lifecycle
    except ImportError:
        from broker_lifecycle import check_broker_order_lifecycle
    result = check_broker_order_lifecycle(
        latest_dir=latest_dir,
        broker_snapshot=broker_snapshot,
        api_key=api_key,
        secret_key=secret_key,
        base_url=base_url,
        allow_state_write=lifecycle_write_authorized,
    )
    summary = result.get("summary", {})
    print(f"Broker order lifecycle monitor: {latest_dir / 'broker_order_lifecycle.md'}")
    print(f"Broker order lifecycle json: {latest_dir / 'broker_order_lifecycle.json'}")
    print(f"lifecycle_status: {summary.get('status', '')}")
    print(f"state_mutated: {summary.get('state_mutated', False)}")
    print(f"lifecycle_write_authorized: {summary.get('lifecycle_write_authorized', False)}")
    return result


def _read_alpaca_paper_snapshot_for_data_folder(data_folder):
    env_path = Path(data_folder).resolve().parents[1] / ".env"
    if not env_path.exists():
        env_path = Path.cwd() / ".env"
    api_key, secret_key, base_url = _alpaca_config(env_path)
    return read_alpaca_paper_snapshot(
        api_key=api_key,
        secret_key=secret_key,
        base_url=base_url,
        broker_submit_enabled=False,
        live_trading_enabled=False,
    )


def _alpaca_config_for_data_folder(data_folder):
    env_path = Path(data_folder).resolve().parents[1] / ".env"
    if not env_path.exists():
        env_path = Path.cwd() / ".env"
    return _alpaca_config(env_path)


def _submit_candidate_symbols(latest_dir):
    intents_path = Path(latest_dir) / "order_intents.csv"
    if not intents_path.exists():
        return []
    try:
        intents = pd.read_csv(intents_path)
    except Exception:
        return []
    if intents.empty or "symbol" not in intents:
        return []
    return sorted({str(value).strip().upper() for value in intents["symbol"].dropna().tolist() if str(value).strip()})


def run_broker_cutover_readiness(data_folder="hisseler", report_output_dir="reports"):
    try:
        from .runner import _report_output_paths
    except ImportError:
        from runner import _report_output_paths
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    broker_snapshot = _read_alpaca_paper_snapshot_for_data_folder(data_folder)
    result = write_broker_cutover_readiness(latest_dir, broker_snapshot=broker_snapshot)
    summary = result.get("summary", {})
    print(f"Broker cutover readiness: {latest_dir / 'broker_cutover_readiness.md'}")
    print(f"Broker cutover readiness csv: {latest_dir / 'broker_cutover_readiness.csv'}")
    print(f"Broker cutover readiness json: {latest_dir / 'broker_cutover_readiness.json'}")
    print(f"cutover_readiness_status: {summary.get('cutover_readiness_status', '')}")
    print(f"local_broker_gap_status: {summary.get('local_broker_gap_status', '')}")
    print(f"broker_cutover_enabled: {str(bool(summary.get('broker_cutover_enabled', False))).lower()}")
    print(f"submit_order_called: {str(bool(summary.get('submit_order_called', False))).lower()}")
    print(f"post_request_used: {str(bool(summary.get('post_request_used', False))).lower()}")
    return result


def run_alpaca_paper_submit_readiness(data_folder="hisseler", report_output_dir="reports"):
    try:
        from .runner import _report_output_paths
    except ImportError:
        from runner import _report_output_paths
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    api_key, secret_key, base_url = _alpaca_config_for_data_folder(data_folder)
    broker_snapshot = read_alpaca_paper_snapshot(
        api_key=api_key,
        secret_key=secret_key,
        base_url=base_url,
        broker_submit_enabled=False,
        live_trading_enabled=False,
    )
    symbols = _submit_candidate_symbols(latest_dir)
    assets_result = read_alpaca_paper_assets(api_key=api_key, secret_key=secret_key, base_url=base_url, symbols=symbols) if symbols else {"assets": {}, "metadata": {"alpaca_assets_read_success": True, "request_methods_used": "none"}}
    result = write_alpaca_paper_submit_readiness(
        latest_dir,
        broker_snapshot=broker_snapshot,
        base_url=base_url,
        assets=assets_result.get("assets", {}),
        assets_metadata=assets_result.get("metadata", {}),
    )
    summary = result.get("summary", {})
    print(f"Alpaca paper submit readiness: {latest_dir / 'alpaca_paper_submit_readiness.md'}")
    print(f"Alpaca paper submit readiness csv: {latest_dir / 'alpaca_paper_submit_readiness.csv'}")
    print(f"Alpaca paper submit readiness json: {latest_dir / 'alpaca_paper_submit_readiness.json'}")
    print(f"submit_readiness_status: {summary.get('submit_readiness_status', '')}")
    print(f"submit_candidate_count: {summary.get('submit_candidate_count', '')}")
    print(f"broker_submit_enabled: {str(bool(summary.get('broker_submit_enabled', False))).lower()}")
    print(f"submit_order_called: {str(bool(summary.get('submit_order_called', False))).lower()}")
    print(f"post_request_used: {str(bool(summary.get('post_request_used', False))).lower()}")
    return result


def run_alpaca_paper_submit_one(
    data_folder="hisseler",
    report_output_dir="reports",
    *,
    confirm_token=None,
    broker_submit_enabled=False,
    live_trading_enabled=False,
    short_trading_enabled=False,
):
    try:
        from .runner import _report_output_paths
    except ImportError:
        from runner import _report_output_paths
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    api_key, secret_key, base_url = _alpaca_config_for_data_folder(data_folder)
    broker_snapshot = read_alpaca_paper_snapshot(
        api_key=api_key,
        secret_key=secret_key,
        base_url=base_url,
        broker_submit_enabled=False,
        live_trading_enabled=False,
    )
    symbols = _submit_candidate_symbols(latest_dir)
    assets_result = read_alpaca_paper_assets(api_key=api_key, secret_key=secret_key, base_url=base_url, symbols=symbols) if symbols else {"assets": {}, "metadata": {"alpaca_assets_read_success": True, "request_methods_used": "none"}}
    result = write_alpaca_paper_submit_one(
        latest_dir,
        broker_snapshot=broker_snapshot,
        base_url=base_url,
        assets=assets_result.get("assets", {}),
        assets_metadata=assets_result.get("metadata", {}),
        broker_submit_enabled=broker_submit_enabled,
        confirm_token=confirm_token,
        live_trading_enabled=live_trading_enabled,
        short_trading_enabled=short_trading_enabled,
        api_key=api_key,
        secret_key=secret_key,
    )
    summary = result.get("summary", {})
    print(f"Alpaca paper submit one audit: {latest_dir / 'alpaca_paper_submit_one_audit.md'}")
    print(f"Alpaca paper submit one json: {latest_dir / 'alpaca_paper_submit_one_audit.json'}")
    print(f"alpaca_paper_submit_one_status: {summary.get('alpaca_paper_submit_one_status', '')}")
    print(f"submit_gate_status: {summary.get('submit_gate_status', '')}")
    print(f"submit_candidate_count: {summary.get('submit_candidate_count', '')}")
    print(f"broker_submit_enabled: {str(bool(summary.get('broker_submit_enabled', False))).lower()}")
    print(f"submit_order_called: {str(bool(summary.get('submit_order_called', False))).lower()}")
    print(f"post_request_used: {str(bool(summary.get('post_request_used', False))).lower()}")
    print(f"live_trading_enabled: {str(bool(summary.get('live_trading_enabled', False))).lower()}")
    print(f"short_trading_enabled: {str(bool(summary.get('short_trading_enabled', False))).lower()}")
    return result


def run_create_broker_cutover_baseline(data_folder="hisseler", report_output_dir="reports"):
    try:
        from .runner import _report_output_paths
    except ImportError:
        from runner import _report_output_paths
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    broker_snapshot = _read_alpaca_paper_snapshot_for_data_folder(data_folder)
    result = create_broker_cutover_baseline(latest_dir, broker_snapshot=broker_snapshot)
    summary = result.get("summary", {})
    print(f"Broker cutover baseline: {latest_dir / 'broker_cutover_baseline.json'}")
    print(f"baseline_created: {str(bool(result.get('created', False))).lower()}")
    print(f"baseline_reason: {result.get('reason', '')}")
    print(f"alpaca_snapshot_status: {summary.get('alpaca_snapshot_status', '')}")
    print(f"local_broker_gap_status: {summary.get('local_broker_gap_status', '')}")
    print(f"broker_cutover_enabled: {str(bool(summary.get('broker_cutover_enabled', False))).lower()}")
    print(f"submit_order_called: {str(bool(summary.get('submit_order_called', False))).lower()}")
    print(f"post_request_used: {str(bool(summary.get('post_request_used', False))).lower()}")
    return result
