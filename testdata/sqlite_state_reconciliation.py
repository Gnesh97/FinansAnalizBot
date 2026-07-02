from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

try:
    from .paper_state_manager import get_stable_key, net_open_positions_from_trade_log
except ImportError:
    from paper_state_manager import get_stable_key, net_open_positions_from_trade_log


REQUIRED_TABLES = {"schema_meta", "pipeline_runs", "paper_orders", "paper_trades", "execution_audit"}
PENDING_STATUSES = {"PENDING", "PENDING_NEXT_OPEN", "WAIT_FOR_NEXT_OPEN"}
REPORT_COLUMNS = ["section", "check_name", "csv_value", "sqlite_value", "status", "reason"]


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _clean_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _status_series(frame: pd.DataFrame) -> pd.Series:
    return frame.get("status", frame.get("fill_status", pd.Series("", index=frame.index))).fillna("").astype(str).str.upper()


def _decision_series(frame: pd.DataFrame) -> pd.Series:
    return frame.get("user_decision", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper()


def _action_series(frame: pd.DataFrame) -> pd.Series:
    return frame.get("action", frame.get("side", pd.Series("", index=frame.index))).fillna("").astype(str).str.upper()


def _accepted_pending_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    return int((_decision_series(frame).eq("ACCEPT") & _status_series(frame).isin(PENDING_STATUSES) & _action_series(frame).isin(["BUY", "SELL"])).sum())


def _review_required_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    return int(_decision_series(frame).isin(["REVIEW_REQUIRED", "HOLD_REVIEW"]).sum())


def _blocked_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    reason = frame.get("reason", pd.Series("", index=frame.index)).fillna("").astype(str)
    return int((_status_series(frame).eq("BLOCKED") | reason.str.contains("buy_already_open|already_open_position", case=False, na=False)).sum())


def _filled_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    return int(frame.get("fill_status", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper().eq("PAPER_FILLED").sum())


def _order_key(row) -> tuple[str, str, str, str]:
    return (
        _clean_text(row.get("strategy_name", "")),
        _clean_text(row.get("symbol", "")).upper(),
        _clean_text(row.get("action", "")).upper(),
        _clean_text(row.get("signal_date", "")),
    )


def _keys(frame: pd.DataFrame, kind: str) -> list[tuple[str, str, str, str]]:
    if frame.empty:
        return []
    if kind == "trade":
        return [get_stable_key(row) for _, row in frame.iterrows()]
    return [_order_key(row) for _, row in frame.iterrows()]


def _dupe_count(keys: list[tuple[str, str, str, str]]) -> int:
    return len(keys) - len(set(keys))


def _key_preview(keys: set[tuple[str, str, str, str]]) -> str:
    if not keys:
        return "none"
    return ";".join("|".join(item) for item in sorted(keys)[:10])


def _check(section: str, name: str, csv_value, sqlite_value, status: str, reason: str) -> dict[str, object]:
    return {
        "section": section,
        "check_name": name,
        "csv_value": csv_value,
        "sqlite_value": sqlite_value,
        "status": status,
        "reason": reason,
    }


def _compare_count(section: str, name: str, csv_value: int, sqlite_value: int) -> dict[str, object]:
    status = "PASS" if int(csv_value) == int(sqlite_value) else "FAIL"
    reason = "counts_match" if status == "PASS" else "count_mismatch"
    return _check(section, name, int(csv_value), int(sqlite_value), status, reason)


def _db_table_names(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def _read_sql_table(db_path: Path, table: str) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(str(db_path))
    try:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _schema_version(db_path: Path) -> str:
    if not db_path.exists():
        return ""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT schema_version FROM schema_meta LIMIT 1").fetchone()
        return _clean_text(row[0]) if row else ""
    except Exception:
        return ""
    finally:
        conn.close()


def _latest_run_id_for_table(db_path: Path, table: str) -> str:
    if not db_path.exists():
        return ""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            f"""
            SELECT t.run_id
            FROM {table} t
            LEFT JOIN pipeline_runs p ON p.run_id = t.run_id
            GROUP BY t.run_id
            ORDER BY COALESCE(p.run_datetime, t.run_id) DESC
            LIMIT 1
            """
        ).fetchone()
        return _clean_text(row[0]) if row else ""
    except Exception:
        return ""
    finally:
        conn.close()


def _latest_run_datetime(db_path: Path, run_id: str) -> str:
    if not db_path.exists() or not run_id:
        return ""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT run_datetime FROM pipeline_runs WHERE run_id = ? LIMIT 1", (run_id,)).fetchone()
        return _clean_text(row[0]) if row else ""
    except Exception:
        return ""
    finally:
        conn.close()


def _table_for_run(frame: pd.DataFrame, run_id: str) -> pd.DataFrame:
    if frame.empty or not run_id or "run_id" not in frame:
        return pd.DataFrame()
    return frame[frame["run_id"].astype(str).eq(str(run_id))].copy()


def _latest_csv_timestamp(paths: list[Path]) -> str:
    mtimes = [path.stat().st_mtime for path in paths if path.exists()]
    if not mtimes:
        return ""
    return pd.Timestamp(max(mtimes), unit="s").isoformat()


def build_sqlite_state_reconciliation(latest_dir: str | Path, db_path: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    db = Path(db_path)
    trade_log = _read_csv(latest / "paper_trade_log.csv")
    pending_orders = _read_csv(latest / "paper_pending_orders.csv")
    order_review = _read_csv(latest / "paper_order_review.csv")
    execution_audit = _read_csv(latest / "execution_audit_log.csv")
    rows: list[dict[str, object]] = []

    tables = _db_table_names(db)
    missing_required = sorted(REQUIRED_TABLES - tables)
    db_exists = db.exists()
    rows.append(_check("database_availability", "sqlite_db_exists", "expected", db_exists, "PASS" if db_exists else "FAIL", str(db)))
    rows.append(_check("database_availability", "required_tables_present", ",".join(sorted(REQUIRED_TABLES)), ",".join(sorted(tables)), "PASS" if not missing_required else "FAIL", ",".join(missing_required) or "all_required_tables_present"))
    rows.append(_check("database_availability", "schema_version", "", _schema_version(db), "PASS" if _schema_version(db) else "WARNING", "schema_meta_available" if _schema_version(db) else "schema_version_unavailable"))
    rows.append(_check("database_availability", "source_of_truth", "CSV", "SQLite mirror", "PASS", "csv_source_of_truth"))

    sqlite_trades = _read_sql_table(db, "paper_trades") if "paper_trades" in tables else pd.DataFrame()
    sqlite_orders_all = _read_sql_table(db, "paper_orders") if "paper_orders" in tables else pd.DataFrame()
    sqlite_execution_all = _read_sql_table(db, "execution_audit") if "execution_audit" in tables else pd.DataFrame()
    paper_orders_run_id = _latest_run_id_for_table(db, "paper_orders") if "paper_orders" in tables else ""
    execution_run_id = _latest_run_id_for_table(db, "execution_audit") if "execution_audit" in tables else ""
    sqlite_orders = _table_for_run(sqlite_orders_all, paper_orders_run_id)
    sqlite_execution = _table_for_run(sqlite_execution_all, execution_run_id)

    if "paper_trades" in tables:
        rows.append(_compare_count("count_reconciliation", "paper_trade_log_row_count", len(trade_log), len(sqlite_trades)))
        rows.append(_compare_count("count_reconciliation", "filled_trade_count", _filled_count(trade_log), _filled_count(sqlite_trades)))
        rows.append(_compare_count("count_reconciliation", "open_position_count_reconstructed", len(net_open_positions_from_trade_log(trade_log)), len(net_open_positions_from_trade_log(sqlite_trades))))
    if "paper_orders" in tables:
        rows.append(_compare_count("count_reconciliation", "paper_pending_orders_row_count", len(pending_orders), len(sqlite_orders)))
        rows.append(_compare_count("count_reconciliation", "accepted_pending_count", _accepted_pending_count(pending_orders), _accepted_pending_count(sqlite_orders)))
        rows.append(_compare_count("count_reconciliation", "review_required_count", _review_required_count(pending_orders), _review_required_count(sqlite_orders)))
        rows.append(_compare_count("count_reconciliation", "blocked_count", _blocked_count(pending_orders), _blocked_count(sqlite_orders)))
    rows.append(
        _check(
            "count_reconciliation",
            "paper_order_review_row_count",
            len(order_review),
            "not_mirrored",
            "PASS" if len(order_review) == 0 else "WARNING",
            "paper_order_review_empty" if len(order_review) == 0 else "paper_order_review_has_no_sqlite_table",
        )
    )
    if "execution_audit" in tables:
        rows.append(_compare_count("count_reconciliation", "execution_audit_log_row_count", len(execution_audit), len(sqlite_execution)))

    csv_trade_keys = _keys(trade_log, "trade")
    sqlite_trade_keys = _keys(sqlite_trades, "trade")
    csv_order_keys = _keys(pending_orders, "order")
    sqlite_order_keys = _keys(sqlite_orders, "order")
    missing_trade = set(csv_trade_keys) - set(sqlite_trade_keys)
    extra_trade = set(sqlite_trade_keys) - set(csv_trade_keys)
    missing_order = set(csv_order_keys) - set(sqlite_order_keys)
    extra_order = set(sqlite_order_keys) - set(csv_order_keys)
    key_checks = [
        ("stable_trade_keys_missing_in_sqlite", len(missing_trade), _key_preview(missing_trade)),
        ("stable_trade_keys_extra_in_sqlite", len(extra_trade), _key_preview(extra_trade)),
        ("duplicate_stable_trade_keys_csv", _dupe_count(csv_trade_keys), ""),
        ("duplicate_stable_trade_keys_sqlite", _dupe_count(sqlite_trade_keys), ""),
        ("pending_order_keys_missing_in_sqlite", len(missing_order), _key_preview(missing_order)),
        ("pending_order_keys_extra_in_sqlite", len(extra_order), _key_preview(extra_order)),
        ("duplicate_pending_order_keys_csv", _dupe_count(csv_order_keys), ""),
        ("duplicate_pending_order_keys_sqlite", _dupe_count(sqlite_order_keys), ""),
    ]
    for name, count, reason in key_checks:
        rows.append(_check("key_reconciliation", name, count, 0, "PASS" if count == 0 else "FAIL", reason or ("no_mismatch" if count == 0 else "key_mismatch")))
    order_review_key_count = len(_keys(order_review, "order"))
    rows.append(
        _check(
            "key_reconciliation",
            "order_review_keys_mismatch",
            order_review_key_count,
            "not_mirrored",
            "PASS" if order_review_key_count == 0 else "WARNING",
            "paper_order_review_empty" if order_review_key_count == 0 else "paper_order_review_has_no_sqlite_table",
        )
    )

    csv_review_executable = int((_decision_series(pending_orders).isin(["REVIEW_REQUIRED", "HOLD_REVIEW"]) & _status_series(pending_orders).isin(PENDING_STATUSES) & _action_series(pending_orders).isin(["BUY", "SELL"]) & _decision_series(pending_orders).eq("ACCEPT")).sum()) if not pending_orders.empty else 0
    sqlite_review_executable = int((_decision_series(sqlite_orders).isin(["REVIEW_REQUIRED", "HOLD_REVIEW"]) & _status_series(sqlite_orders).isin(PENDING_STATUSES) & _action_series(sqlite_orders).isin(["BUY", "SELL"]) & _decision_series(sqlite_orders).eq("ACCEPT")).sum()) if not sqlite_orders.empty else 0
    blocked_executable_csv = int((_blocked_count(pending_orders) > 0) and (_accepted_pending_count(pending_orders[pending_orders.get("status", pd.Series("", index=pending_orders.index)).astype(str).str.upper().eq("BLOCKED")]) > 0)) if not pending_orders.empty else 0
    blocked_executable_sqlite = int((_blocked_count(sqlite_orders) > 0) and (_accepted_pending_count(sqlite_orders[sqlite_orders.get("status", pd.Series("", index=sqlite_orders.index)).astype(str).str.upper().eq("BLOCKED")]) > 0)) if not sqlite_orders.empty else 0
    status_checks = [
        ("accepted_pending_status_match", _accepted_pending_count(pending_orders), _accepted_pending_count(sqlite_orders)),
        ("review_required_not_executable_csv", csv_review_executable, 0),
        ("review_required_not_executable_sqlite", sqlite_review_executable, 0),
        ("blocked_duplicate_buy_not_executable_csv", blocked_executable_csv, 0),
        ("blocked_duplicate_buy_not_executable_sqlite", blocked_executable_sqlite, 0),
        ("filled_rows_not_pending_executable", _filled_count(trade_log), _filled_count(trade_log)),
    ]
    for name, csv_value, sqlite_value in status_checks:
        status = "PASS" if csv_value == sqlite_value else "FAIL"
        rows.append(_check("status_reconciliation", name, csv_value, sqlite_value, status, "status_match" if status == "PASS" else "status_mismatch"))

    csv_timestamp = _latest_csv_timestamp([latest / "paper_trade_log.csv", latest / "paper_pending_orders.csv", latest / "paper_order_review.csv", latest / "execution_audit_log.csv"])
    sqlite_timestamp = _latest_run_datetime(db, execution_run_id or paper_orders_run_id)
    if csv_timestamp and sqlite_timestamp:
        mirror_lag = pd.to_datetime(sqlite_timestamp, errors="coerce") < pd.to_datetime(csv_timestamp, errors="coerce").tz_localize(None)
        rows.append(_check("freshness", "mirror_lag_warning", csv_timestamp, sqlite_timestamp, "WARNING" if bool(mirror_lag) else "PASS", "sqlite_latest_run_older_than_csv_mtime" if bool(mirror_lag) else "freshness_comparable"))
    else:
        rows.append(_check("freshness", "mirror_lag_warning", csv_timestamp, sqlite_timestamp, "WARNING", "timestamp_unavailable"))
    rows.append(_check("freshness", "latest_paper_orders_run_id", "current_pending_csv", paper_orders_run_id, "PASS" if paper_orders_run_id else "WARNING", "latest_sqlite_paper_orders_run"))
    execution_run_status = "PASS" if execution_run_id or (execution_audit.empty and sqlite_execution_all.empty) else "WARNING"
    rows.append(
        _check(
            "freshness",
            "latest_execution_audit_run_id",
            "current_execution_audit_csv",
            execution_run_id,
            execution_run_status,
            "execution_audit_empty" if execution_run_status == "PASS" and not execution_run_id else "latest_sqlite_execution_audit_run",
        )
    )

    report = pd.DataFrame(rows, columns=REPORT_COLUMNS)
    summary = _summary(report)
    return {
        "summary": summary,
        "checks": report,
        "database": {
            "sqlite_db_exists": bool(db_exists),
            "sqlite_db_path": str(db),
            "required_tables_present": not missing_required,
            "missing_tables": missing_required,
            "schema_version": _schema_version(db),
            "source_of_truth": "CSV",
            "latest_paper_orders_run_id": paper_orders_run_id,
            "latest_execution_audit_run_id": execution_run_id,
        },
    }


def _summary(checks: pd.DataFrame) -> dict[str, object]:
    statuses = checks.get("status", pd.Series(dtype=str)).astype(str).str.upper()
    fail_count = int(statuses.eq("FAIL").sum())
    warning_count = int(statuses.eq("WARNING").sum())
    def section_fail_count(section: str) -> int:
        subset = checks[checks["section"].astype(str).eq(section)]
        return int(subset["status"].astype(str).str.upper().eq("FAIL").sum()) if not subset.empty else 0
    duplicate_rows = checks[checks["check_name"].astype(str).str.contains("duplicate", case=False, na=False)]
    return {
        "reconciliation_status": "FAIL" if fail_count else ("WARNING" if warning_count else "PASS"),
        "csv_source_of_truth": True,
        "sqlite_source_of_truth": False,
        "table_count_mismatch_count": section_fail_count("count_reconciliation"),
        "key_mismatch_count": section_fail_count("key_reconciliation"),
        "status_mismatch_count": section_fail_count("status_reconciliation"),
        "duplicate_key_count": int(pd.to_numeric(duplicate_rows.get("csv_value", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not duplicate_rows.empty else 0,
        "missing_table_count": section_fail_count("database_availability"),
        "mirror_lag_warning_count": int(((checks["section"].astype(str).eq("freshness")) & statuses.eq("WARNING")).sum()) if not checks.empty else 0,
        "state_mutated": False,
        "broker_submit_enabled": False,
        "live_trading_enabled": False,
        "short_trading_enabled": False,
        "submit_order_called": False,
        "post_request_used": False,
    }


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    return json.loads(frame.fillna("").to_json(orient="records"))


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "none"
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(_clean_text(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def _metric_table(values: dict[str, object]) -> str:
    rows = []
    for key, value in values.items():
        if isinstance(value, list):
            value = ",".join(value) if value else "none"
        rows.append({"metric": key, "value": value})
    return _markdown_table(pd.DataFrame(rows))


def write_sqlite_state_reconciliation(latest_dir: str | Path, db_path: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    result = build_sqlite_state_reconciliation(latest, db_path)
    csv_path = latest / "sqlite_state_reconciliation.csv"
    md_path = latest / "sqlite_state_reconciliation.md"
    json_path = latest / "sqlite_state_reconciliation.json"
    result["checks"].to_csv(csv_path, index=False)
    payload = {
        "summary": result["summary"],
        "database": result["database"],
        "checks": _records(result["checks"]),
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# SQLite State Reconciliation",
                "",
                "## Summary",
                "",
                _metric_table(result["summary"]),
                "",
                "## Database",
                "",
                _metric_table(result["database"]),
                "",
                "## Checks",
                "",
                _markdown_table(result["checks"]),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "summary": result["summary"],
        "database": result["database"],
        "csv_path": str(csv_path),
        "md_path": str(md_path),
        "json_path": str(json_path),
    }
