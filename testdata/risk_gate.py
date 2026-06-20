from pathlib import Path

import pandas as pd

try:
    from .config import SHORT_TRADING_ENABLED
except ImportError:
    from config import SHORT_TRADING_ENABLED


RISK_REPORT_COLUMNS = [
    "order_intent_id",
    "idempotency_phase",
    "idempotency_key",
    "execution_idempotency_key",
    "symbol",
    "strategy_name",
    "side",
    "notional",
    "risk_pass",
    "block_reason",
    "warning",
]

VALID_ORDER_SIDES = {"BUY", "SELL", "SHORT_SELL", "BUY_TO_COVER"}
SHORT_ORDER_SIDES = {"SHORT_SELL", "BUY_TO_COVER"}


def _is_true(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _num(value):
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


def normalize_order_side(value):
    raw = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "B": "BUY",
        "BUY": "BUY",
        "S": "SELL",
        "SELL": "SELL",
        "SHORT": "SHORT_SELL",
        "SELL_SHORT": "SHORT_SELL",
        "SHORT_SELL": "SHORT_SELL",
        "BUYTOCOVER": "BUY_TO_COVER",
        "BUY_TO_COVER": "BUY_TO_COVER",
        "COVER": "BUY_TO_COVER",
    }
    return aliases.get(raw, raw)


def _unknown(value):
    return pd.isna(value) or str(value).strip() == ""


def evaluate_order_risk(order_intents, source_orders=None, max_new_orders_per_run=5, short_trading_enabled=SHORT_TRADING_ENABLED):
    intents = order_intents.copy() if order_intents is not None else pd.DataFrame()
    source = source_orders.copy() if source_orders is not None else pd.DataFrame()
    source_lookup = {}
    if not source.empty:
        for _, row in source.iterrows():
            key = (str(row.get("symbol", "")).upper(), str(row.get("strategy_name", "")))
            source_lookup[key] = row

    rows = []
    seen_symbols = set()
    seen_execution_keys = set()
    for index, intent in intents.reset_index(drop=True).iterrows():
        order_intent_id = str(intent.get("order_intent_id", "")).strip()
        idempotency_phase = str(intent.get("idempotency_phase", "PREVIEW")).strip().upper() or "PREVIEW"
        idempotency_key = str(intent.get("idempotency_key", "")).strip()
        execution_idempotency_key = str(
            intent.get("execution_idempotency_key", f"{idempotency_phase}:{idempotency_key}")
        ).strip()
        symbol = str(intent.get("symbol", "")).strip().upper()
        strategy_name = str(intent.get("strategy_name", "")).strip()
        source_row = source_lookup.get((symbol, strategy_name), intent)
        side = normalize_order_side(intent.get("side", ""))
        notional = _num(intent.get("notional", source_row.get("paper_position_notional", pd.NA)))
        block = []
        warning = []

        if not symbol:
            block.append("missing_symbol")
        if side not in VALID_ORDER_SIDES:
            block.append("invalid_order_side")
        if pd.isna(notional):
            block.append("missing_notional")
        elif float(notional) <= 0:
            block.append("notional_not_positive")
        if execution_idempotency_key and execution_idempotency_key in seen_execution_keys:
            block.append("duplicate_execution_idempotency_key")
        if execution_idempotency_key:
            seen_execution_keys.add(execution_idempotency_key)
        if symbol and symbol in seen_symbols:
            block.append("duplicate_symbol_order")
            warning.append("duplicate_symbol_order_same_run")
        if symbol:
            seen_symbols.add(symbol)
        if index >= int(max_new_orders_per_run):
            block.append("max_new_orders_per_run_exceeded")
        if not _is_true(source_row.get("liquidity_guard_pass", intent.get("liquidity_guard_pass", False))):
            block.append("liquidity_guard_failed")
        if side in SHORT_ORDER_SIDES:
            if not bool(short_trading_enabled):
                block.append("short_trading_disabled")
            borrow_available = source_row.get("borrow_available", intent.get("borrow_available", pd.NA))
            borrow_fee = source_row.get("borrow_fee", intent.get("borrow_fee", pd.NA))
            hard_to_borrow = source_row.get("hard_to_borrow", intent.get("hard_to_borrow", pd.NA))
            if _unknown(borrow_available):
                block.append("borrow_available_unknown")
            elif not _is_true(borrow_available):
                block.append("borrow_not_available")
            if _unknown(borrow_fee):
                warning.append("borrow_fee_unknown")
                block.append("borrow_fee_unknown")
            if _unknown(hard_to_borrow):
                warning.append("hard_to_borrow_unknown")
                block.append("hard_to_borrow_unknown")
            max_short_notional = _num(source_row.get("max_short_notional", intent.get("max_short_notional", pd.NA)))
            if (
                side == "SHORT_SELL"
                and pd.notna(max_short_notional)
                and pd.notna(notional)
                and float(notional) > float(max_short_notional)
            ):
                block.append("max_short_notional_exceeded")
            short_position_exists = _is_true(
                source_row.get("short_position_exists", intent.get("short_position_exists", False))
            )
            if side == "BUY_TO_COVER" and not short_position_exists:
                block.append("buy_to_cover_without_short_position")

        order_pct = _num(
            source_row.get(
                "order_size_pct_of_dollar_volume",
                intent.get("order_size_pct_of_dollar_volume", pd.NA),
            )
        )
        if pd.notna(order_pct) and float(order_pct) > 1.0:
            warning.append("order_size_pct_of_dollar_volume_above_1pct")

        rows.append(
            {
                "order_intent_id": order_intent_id,
                "idempotency_phase": idempotency_phase,
                "idempotency_key": idempotency_key,
                "execution_idempotency_key": execution_idempotency_key,
                "symbol": symbol,
                "strategy_name": strategy_name,
                "side": side,
                "notional": notional,
                "risk_pass": not bool(block),
                "block_reason": ";".join(block),
                "warning": ";".join(warning),
            }
        )
    return pd.DataFrame(rows, columns=RISK_REPORT_COLUMNS)


def write_risk_gate_reports(risk_report, csv_path, markdown_path):
    frame = risk_report.copy() if risk_report is not None else pd.DataFrame(columns=RISK_REPORT_COLUMNS)
    frame.to_csv(csv_path, index=False)
    passed = int(frame["risk_pass"].astype(bool).sum()) if not frame.empty else 0
    blocked = int(len(frame) - passed)
    table = "No order intents."
    if not frame.empty:
        markdown_rows = [
            "| " + " | ".join(RISK_REPORT_COLUMNS) + " |",
            "| " + " | ".join(["---"] * len(RISK_REPORT_COLUMNS)) + " |",
        ]
        for _, row in frame.iterrows():
            markdown_rows.append(
                "| "
                + " | ".join(str(row.get(column, "")).replace("\n", " ") for column in RISK_REPORT_COLUMNS)
                + " |"
            )
        table = "\n".join(markdown_rows)
    lines = [
        "# Order Risk Check Report",
        "",
        f"- risk passed count: {passed}",
        f"- blocked count: {blocked}",
        f"- max_new_orders_per_run enforced: true",
        "",
        table,
        "",
    ]
    Path(markdown_path).write_text("\n".join(lines), encoding="utf-8")
    return frame
