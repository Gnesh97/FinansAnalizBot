from pathlib import Path

import pandas as pd


RISK_REPORT_COLUMNS = [
    "symbol",
    "strategy_name",
    "side",
    "notional",
    "risk_pass",
    "block_reason",
    "warning",
]


def _is_true(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _num(value):
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


def evaluate_order_risk(order_intents, source_orders=None, max_new_orders_per_run=5):
    intents = order_intents.copy() if order_intents is not None else pd.DataFrame()
    source = source_orders.copy() if source_orders is not None else pd.DataFrame()
    source_lookup = {}
    if not source.empty:
        for _, row in source.iterrows():
            key = (str(row.get("symbol", "")).upper(), str(row.get("strategy_name", "")))
            source_lookup[key] = row

    rows = []
    seen = set()
    for index, intent in intents.reset_index(drop=True).iterrows():
        symbol = str(intent.get("symbol", "")).strip().upper()
        strategy_name = str(intent.get("strategy_name", "")).strip()
        source_row = source_lookup.get((symbol, strategy_name), intent)
        side = str(intent.get("side", "")).strip().lower()
        notional = _num(intent.get("notional", source_row.get("paper_position_notional", pd.NA)))
        block = []
        warning = []

        if not symbol:
            block.append("missing_symbol")
        if pd.isna(notional):
            block.append("missing_notional")
        elif float(notional) <= 0:
            block.append("notional_not_positive")
        if symbol and symbol in seen:
            block.append("duplicate_symbol_order")
        seen.add(symbol)
        if index >= int(max_new_orders_per_run):
            block.append("max_new_orders_per_run_exceeded")
        if not _is_true(source_row.get("liquidity_guard_pass", intent.get("liquidity_guard_pass", False))):
            block.append("liquidity_guard_failed")

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
