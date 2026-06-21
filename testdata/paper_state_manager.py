from pathlib import Path

import pandas as pd

try:
    from .report_schemas import (
        PAPER_ORDER_REVIEW_COLUMNS,
        PAPER_PENDING_ORDERS_COLUMNS,
        PAPER_TRADE_LOG_COLUMNS,
        ensure_columns,
        validate_required_columns,
    )
except ImportError:
    from report_schemas import (
        PAPER_ORDER_REVIEW_COLUMNS,
        PAPER_PENDING_ORDERS_COLUMNS,
        PAPER_TRADE_LOG_COLUMNS,
        ensure_columns,
        validate_required_columns,
    )


PENDING_ORDERS_FILE = "paper_pending_orders.csv"
ORDER_REVIEW_FILE = "paper_order_review.csv"
TRADE_LOG_FILE = "paper_trade_log.csv"
TRADE_LOG_TEMPLATE_FILE = "paper_trade_log_template.csv"


def _reports_dir(reports_dir):
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_state_csv(path, columns, fill_value=""):
    if not Path(path).exists():
        return pd.DataFrame(columns=columns)
    try:
        frame = pd.read_csv(path)
    except Exception:
        frame = pd.DataFrame(columns=columns)
    return ensure_columns(frame, columns, fill_value=fill_value)


def _write_state_csv(path, df, columns, fill_value=""):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    frame = ensure_columns(df, columns, fill_value=fill_value)
    frame.to_csv(path, index=False)
    return frame


def build_empty_paper_state():
    return {
        "pending_orders": pd.DataFrame(columns=PAPER_PENDING_ORDERS_COLUMNS),
        "order_review": pd.DataFrame(columns=PAPER_ORDER_REVIEW_COLUMNS),
        "trade_log": pd.DataFrame(columns=PAPER_TRADE_LOG_COLUMNS),
    }


def read_pending_orders(reports_dir):
    return _read_state_csv(
        _reports_dir(reports_dir) / PENDING_ORDERS_FILE,
        PAPER_PENDING_ORDERS_COLUMNS,
        fill_value=pd.NA,
    )


def write_pending_orders(reports_dir, df):
    return _write_state_csv(
        _reports_dir(reports_dir) / PENDING_ORDERS_FILE,
        df,
        PAPER_PENDING_ORDERS_COLUMNS,
        fill_value=pd.NA,
    )


def read_order_review(reports_dir):
    return _read_state_csv(
        _reports_dir(reports_dir) / ORDER_REVIEW_FILE,
        PAPER_ORDER_REVIEW_COLUMNS,
    )


def write_order_review(reports_dir, df):
    return _write_state_csv(
        _reports_dir(reports_dir) / ORDER_REVIEW_FILE,
        df,
        PAPER_ORDER_REVIEW_COLUMNS,
    )


def read_trade_log(reports_dir):
    return _read_state_csv(
        _reports_dir(reports_dir) / TRADE_LOG_FILE,
        PAPER_TRADE_LOG_COLUMNS,
        fill_value=pd.NA,
    )


def write_trade_log(reports_dir, df):
    return _write_state_csv(
        _reports_dir(reports_dir) / TRADE_LOG_FILE,
        df,
        PAPER_TRADE_LOG_COLUMNS,
        fill_value=pd.NA,
    )


def append_trade_log(reports_dir, rows):
    current = read_trade_log(reports_dir)
    if rows:
        new_rows = pd.DataFrame(rows)
        current = new_rows if current.empty else pd.concat([current, new_rows], ignore_index=True)
    return write_trade_log(reports_dir, current)


def create_trade_log_template(reports_dir):
    return _write_state_csv(
        _reports_dir(reports_dir) / TRADE_LOG_TEMPLATE_FILE,
        pd.DataFrame(columns=PAPER_TRADE_LOG_COLUMNS),
        PAPER_TRADE_LOG_COLUMNS,
        fill_value=pd.NA,
    )


def ensure_paper_state_files(reports_dir):
    reports_path = _reports_dir(reports_dir)
    pending = read_pending_orders(reports_path)
    review = read_order_review(reports_path)
    trade_log = read_trade_log(reports_path)
    write_pending_orders(reports_path, pending)
    write_order_review(reports_path, review)
    write_trade_log(reports_path, trade_log)
    return {
        "pending_orders": pending,
        "order_review": review,
        "trade_log": trade_log,
    }


def validate_paper_state(reports_dir):
    reports_path = _reports_dir(reports_dir)
    checks = [
        ("pending_orders", PENDING_ORDERS_FILE, PAPER_PENDING_ORDERS_COLUMNS),
        ("order_review", ORDER_REVIEW_FILE, PAPER_ORDER_REVIEW_COLUMNS),
        ("trade_log", TRADE_LOG_FILE, PAPER_TRADE_LOG_COLUMNS),
    ]
    rows = []
    schema_ok = True
    for name, filename, columns in checks:
        path = reports_path / filename
        exists = path.exists()
        frame = _read_state_csv(path, columns, fill_value=pd.NA if name != "order_review" else "")
        validation = validate_required_columns(frame, columns)
        schema_ok = schema_ok and bool(validation["ok"])
        rows.append(
            {
                "name": name,
                "exists": exists,
                "rows": len(frame),
                "missing_columns": ",".join(validation["missing_columns"]),
                "extra_columns": ",".join(validation["extra_columns"]),
                "schema_status": "OK" if validation["ok"] else "WARNING",
            }
        )
    return {
        "pending_orders_exists": bool((reports_path / PENDING_ORDERS_FILE).exists()),
        "order_review_exists": bool((reports_path / ORDER_REVIEW_FILE).exists()),
        "trade_log_exists": bool((reports_path / TRADE_LOG_FILE).exists()),
        "pending_orders_rows": int(rows[0]["rows"]),
        "order_review_rows": int(rows[1]["rows"]),
        "trade_log_rows": int(rows[2]["rows"]),
        "schema_status": "OK" if schema_ok else "WARNING",
        "status": "OK" if schema_ok and all(row["exists"] for row in rows) else "WARNING",
        "rows": rows,
    }
