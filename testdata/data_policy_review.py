from pathlib import Path

import pandas as pd


RESOLVING_VALUES = [
    "ACK",
    "ACKNOWLEDGED",
    "ALLOW",
    "ALLOWED",
    "REPAIRED",
    "REPAIR_DONE",
    "REMOVED",
    "REMOVE_FROM_UNIVERSE",
]


def _is_resolved(value):
    return str(value or "").strip().upper() in set(RESOLVING_VALUES)


def _has_note(value):
    if pd.isna(value):
        return False
    return str(value).strip() != ""


def _read_csv(path):
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _first_value(frame, column):
    if frame.empty or column not in frame:
        return "none"
    value = frame[column].dropna()
    return str(value.iloc[0]) if not value.empty else "none"


def _last_value(frame, column):
    if frame.empty or column not in frame:
        return "none"
    value = frame[column].dropna()
    return str(value.iloc[-1]) if not value.empty else "none"


def _first_existing_value(frame, columns):
    for column in columns:
        value = _first_value(frame, column)
        if value != "none":
            return value
    return "none"


def _last_existing_value(frame, columns):
    for column in columns:
        value = _last_value(frame, column)
        if value != "none":
            return value
    return "none"


def _zero_volume_rows(frame):
    if frame.empty or "Volume" not in frame:
        return 0
    volume = pd.to_numeric(frame["Volume"], errors="coerce")
    return int(volume.fillna(0).eq(0).sum())


def _latest_date_value(values):
    parsed = pd.to_datetime(pd.Series(list(values)), errors="coerce").dropna()
    if parsed.empty:
        return pd.NaT
    return parsed.max()


def _local_next_action(policy_row, evidence):
    warning = str(policy_row.get("warning", "")).strip()
    symbol = str(policy_row.get("symbol", "")).strip().upper()
    if warning == "early_daily_end":
        return "verify_corporate_action_or_vendor_stale_history_before_ack"
    if warning == "psa_zero_volume":
        return "inspect_zero_volume_vendor_row_or_repair_source_before_ack"
    if warning == "dow_empty_daily":
        return "refresh_or_remove_empty_daily_file_before_ack"
    if not bool(evidence.get("daily_exists", False)) or not bool(evidence.get("intraday_exists", False)):
        return "repair_missing_local_csv_before_ack"
    return f"review_{symbol}_local_data_warning_before_ack"


def _symbol_evidence(symbol, latest_dir, data_dir):
    symbol = str(symbol).strip().upper()
    daily = _read_csv(data_dir / "daily" / f"{symbol}_1D.csv")
    intraday = _read_csv(data_dir / "bars_130min" / f"{symbol}_130min.csv")
    return {
        "symbol": symbol,
        "daily_exists": (data_dir / "daily" / f"{symbol}_1D.csv").exists(),
        "daily_rows": int(len(daily)),
        "daily_first_date": _first_value(daily, "Date"),
        "daily_last_date": _last_value(daily, "Date"),
        "daily_zero_volume_rows": _zero_volume_rows(daily),
        "intraday_exists": (data_dir / "bars_130min" / f"{symbol}_130min.csv").exists(),
        "intraday_rows": int(len(intraday)),
        "intraday_first_datetime": _first_existing_value(intraday, ["Datetime", "Date"]),
        "intraday_last_datetime": _last_existing_value(intraday, ["Datetime", "Date"]),
        "source_note": "local_csv_only_external_corporate_action_not_verified",
    }


def _markdown_table(frame):
    if frame.empty:
        return "none"
    columns = list(frame.columns)
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for _, row in frame.iterrows():
        values = []
        for column in columns:
            value = row.get(column, "")
            values.append("" if pd.isna(value) else str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _active_symbols(latest_dir):
    active = set()
    latest_dir = Path(latest_dir)
    # Trade log positions
    trade_log_path = latest_dir / "paper_trade_log.csv"
    if trade_log_path.exists():
        try:
            df = pd.read_csv(trade_log_path)
            if not df.empty:
                filled = df[df["fill_status"].astype(str).str.upper().eq("PAPER_FILLED")]
                active.update(filled["symbol"].dropna().astype(str).str.upper().unique())
        except Exception:
            pass
    # Pending orders
    pending_path = latest_dir / "paper_pending_orders.csv"
    if pending_path.exists():
        try:
            df = pd.read_csv(pending_path)
            if not df.empty:
                active.update(df["symbol"].dropna().astype(str).str.upper().unique())
        except Exception:
            pass
    # Review orders
    review_path = latest_dir / "paper_order_review.csv"
    if review_path.exists():
        try:
            df = pd.read_csv(review_path)
            if not df.empty:
                active.update(df["symbol"].dropna().astype(str).str.upper().unique())
        except Exception:
            pass
    return active


def _write_review_report(latest_dir, policy, unresolved, evidence, has_warnings=False):
    report_path = latest_dir / "data_policy_review_report.md"
    evidence_path = latest_dir / "data_policy_review_evidence.csv"
    evidence.to_csv(evidence_path, index=False)
    if not unresolved.empty:
        status = "BLOCK"
    elif has_warnings:
        status = "WARNING"
    else:
        status = "OK"
    lines = [
        "# Data Policy Review Report",
        "",
        f"- status: {status}",
        f"- decision_count: {len(policy)}",
        f"- unresolved_count: {len(unresolved)}",
        "- evidence_scope: local CSV dates/rows/zero-volume only; external corporate action status is not verified here.",
        "- policy_file: reports/latest/data_policy_decisions.csv",
        "- evidence_file: reports/latest/data_policy_review_evidence.csv",
        "- pre_submit_report: reports/latest/pre_submit_readiness_report.md",
        "",
        "## Resolving Values",
        "",
        "These values resolve a blocking policy only when operator_note is non-empty: "
        + ", ".join(RESOLVING_VALUES),
        "",
        "## Unresolved Decisions",
        "",
        _markdown_table(unresolved),
        "",
        "## Local Data Evidence",
        "",
        _markdown_table(evidence),
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path, evidence_path


def main():
    latest_dir = Path("reports") / "latest"
    policy_path = latest_dir / "data_policy_decisions.csv"
    precheck_path = latest_dir / "data_precheck_report.md"
    pre_submit_path = latest_dir / "pre_submit_readiness_report.md"

    print("Data Policy Review")
    print(f"policy_file: {policy_path}")
    print(f"data_precheck_report: {precheck_path}")
    print(f"pre_submit_report: {pre_submit_path}")
    print("resolving_values: " + ", ".join(RESOLVING_VALUES))
    print("blocking_values: blank, REVIEW_REQUIRED, REJECT, REPAIR_REQUIRED, or any unrecognized value")
    print()

    if not policy_path.exists():
        print("status: MISSING")
        print("next_action: Run scripts\\run_paper_report.bat first to generate data_policy_decisions.csv.")
        return 1

    policy = pd.read_csv(policy_path)
    if policy.empty:
        print("status: OK")
        print("decision_count: 0")
        print("unresolved_count: 0")
        return 0

    operator_decision = policy.get("operator_decision", pd.Series("", index=policy.index)).fillna("")
    operator_note = policy.get("operator_note", pd.Series("", index=policy.index))
    policy_status = policy.get("policy_status", pd.Series("", index=policy.index)).fillna("")
    blocking_policy = policy_status.astype(str).str.upper().str.startswith("BLOCK")
    resolved = operator_decision.map(_is_resolved) & operator_note.map(_has_note)
    unresolved = policy[blocking_policy & ~resolved].copy()
    symbols = sorted(policy.get("symbol", pd.Series(dtype=str)).dropna().astype(str).str.upper().unique().tolist())
    evidence = pd.DataFrame([_symbol_evidence(symbol, latest_dir, Path("hisseler")) for symbol in symbols])
    if not evidence.empty:
        evidence_by_symbol = {str(row.get("symbol", "")).upper(): row for _, row in evidence.iterrows()}
        evidence["recommended_operator_next_action"] = evidence["symbol"].astype(str).str.upper().map(
            lambda symbol: _local_next_action(
                policy[policy.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(symbol)].iloc[0]
                if not policy[policy.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().eq(symbol)].empty
                else {},
                evidence_by_symbol.get(symbol, {}),
            )
        )

    try:
        from data_policy_exceptions import DATA_POLICY_EXCEPTIONS
    except ImportError:
        DATA_POLICY_EXCEPTIONS = {}

    active_syms = _active_symbols(latest_dir)
    
    # Check PSA repair status against current local daily data end date.
    psa_repaired = False
    latest_daily_date = _latest_date_value(evidence.get("daily_last_date", pd.Series(dtype=str))) if not evidence.empty else pd.NaT
    psa_daily_file = Path("hisseler/daily/PSA_1D.csv")
    if psa_daily_file.exists():
        try:
            df_psa = pd.read_csv(psa_daily_file)
            psa_latest = pd.to_datetime(
                pd.Series([df_psa["Date"].iloc[-1] if not df_psa.empty else pd.NA]),
                errors="coerce",
            ).iloc[0]
            if pd.notna(psa_latest) and pd.notna(latest_daily_date) and psa_latest.date() >= latest_daily_date.date():
                psa_repaired = True
        except Exception:
            pass

    unresolved_filtered_rows = []
    has_warnings = False
    for _, row in unresolved.iterrows():
        symbol = str(row.get("symbol", "")).strip().upper()
        exception = DATA_POLICY_EXCEPTIONS.get(symbol, {})
        
        # Check SAFE_KNOWN_EXCEPTION
        if exception and exception.get("category") == "SAFE_KNOWN_EXCEPTION":
            if symbol not in active_syms:
                has_warnings = True
                continue
                
        # Check PSA
        if symbol == "PSA":
            if symbol not in active_syms:
                has_warnings = True
                continue
                
        unresolved_filtered_rows.append(row)
        
    unresolved_filtered = pd.DataFrame(unresolved_filtered_rows)

    report_path, evidence_path = _write_review_report(latest_dir, policy, unresolved_filtered, evidence, has_warnings)

    print(f"decision_count: {len(policy)}")
    print(f"unresolved_count: {len(unresolved_filtered)}")
    print(f"review_report: {report_path}")
    print(f"evidence_file: {evidence_path}")
    if unresolved_filtered.empty:
        if has_warnings:
            print("status: WARNING")
        else:
            print("status: OK")
        print("next_action: Run scripts\\run_pre_submit_check.bat to refresh readiness.")
        return 0

    print("status: BLOCK")
    print("unresolved_decisions:")
    columns = [
        "symbol",
        "warning",
        "policy_status",
        "recommended_policy_action",
        "operator_decision",
        "operator_note",
    ]
    for _, row in unresolved_filtered.iterrows():
        values = []
        for column in columns:
            value = row.get(column, "")
            values.append("" if pd.isna(value) else str(value))
        print(" | ".join(values))
    print()
    print("next_action: Edit reports\\latest\\data_policy_decisions.csv after verifying source data, then rerun scripts\\run_pre_submit_check.bat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
