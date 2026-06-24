import os
import sqlite3
import pandas as pd
from datetime import datetime

SCHEMA_VERSION = "1.0.0"

def connect_state_db(db_path):
    """Establishes connection to the SQLite database and returns the connection object."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    # Enable foreign keys and journal WAL mode for concurrent operations
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def init_state_db(db_path):
    """Initializes the SQLite database tables and indices if they do not exist."""
    conn = None
    try:
        conn = connect_state_db(db_path)
        with conn:
            # Table 1: schema_meta
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    schema_version TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
            """)
            # Insert initial version if missing
            conn.execute("INSERT OR IGNORE INTO schema_meta (schema_version, created_at) VALUES (?, ?);", 
                         (SCHEMA_VERSION, datetime.now().isoformat()))

            # Table 2: pipeline_runs
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    run_datetime TEXT NOT NULL,
                    command TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    strategy_metrics_changed INTEGER,
                    benchmark_changed INTEGER,
                    regression_guard_status TEXT,
                    total_runtime_sec REAL
                );
            """)

            # Table 3: paper_orders
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    signal_date TEXT,
                    strategy_name TEXT,
                    symbol TEXT,
                    action TEXT,
                    paper_rank INTEGER,
                    ranking_score REAL,
                    paper_capital REAL,
                    paper_position_notional REAL,
                    liquidity_guard_pass INTEGER,
                    status TEXT,
                    user_decision TEXT,
                    reason TEXT
                );
            """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_unique ON paper_orders(run_id, symbol, strategy_name, signal_date, action);")
            # Table 4: paper_trades
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    date TEXT,
                    strategy_name TEXT,
                    symbol TEXT,
                    action TEXT,
                    signal_date TEXT,
                    signal_price REAL,
                    assumed_fill_price REAL,
                    fill_status TEXT,
                    shares REAL,
                    notional REAL,
                    paper_capital REAL,
                    reason TEXT,
                    liquidity_guard_pass INTEGER,
                    notes TEXT
                );
            """)
            try:
                conn.execute("""
                    DELETE FROM paper_trades 
                    WHERE id NOT IN (
                        SELECT MIN(id) 
                        FROM paper_trades 
                        GROUP BY strategy_name, symbol, action, signal_date
                    );
                """)
            except Exception as e_mig:
                print(f"WARNING: failed to deduplicate SQLite paper_trades: {e_mig}")
            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_trades_unique ON paper_trades(strategy_name, symbol, action, signal_date);")
            except Exception as e_idx:
                print(f"WARNING: failed to create unique index on paper_trades: {e_idx}")

            # Table 5: order_intents
            conn.execute("""
                CREATE TABLE IF NOT EXISTS order_intents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    order_intent_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    idempotency_phase TEXT,
                    execution_idempotency_key TEXT,
                    created_at TEXT,
                    strategy_name TEXT,
                    symbol TEXT,
                    side TEXT,
                    notional REAL,
                    estimated_qty REAL,
                    order_type TEXT,
                    time_in_force TEXT,
                    source_signal_date TEXT,
                    status TEXT,
                    reason TEXT
                );
            """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_order_intents_intent_id ON order_intents(run_id, order_intent_id);")

            # Table 6: execution_audit
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    run_datetime TEXT,
                    event_type TEXT,
                    order_intent_id TEXT,
                    idempotency_key TEXT,
                    idempotency_phase TEXT,
                    execution_idempotency_key TEXT,
                    strategy_name TEXT,
                    symbol TEXT,
                    side TEXT,
                    status TEXT,
                    risk_pass INTEGER,
                    block_reason TEXT,
                    would_submit INTEGER,
                    broker_submit_enabled INTEGER,
                    live_trading_enabled INTEGER,
                    message TEXT
                );
            """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_exec_audit_idempotency ON execution_audit(run_id, execution_idempotency_key, idempotency_phase, event_type);")

            # Table 7: readiness_checks
            conn.execute("""
                CREATE TABLE IF NOT EXISTS readiness_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    check_type TEXT NOT NULL,
                    check_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    value TEXT,
                    required_value TEXT,
                    message TEXT
                );
            """)
            # Create a unique index for readiness checks to prevent duplicates in the same run
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_readiness_checks_unique ON readiness_checks(run_id, check_type, check_name);")

        return True
    except Exception as e:
        print(f"WARNING: SQLite init_state_db failed: {e}")
        return False
    finally:
        if conn:
            conn.close()

def _clean_records(data):
    """Utility function to clean dataframe or dict list into sqlite-friendly format."""
    if data is None:
        return []
    
    records = []
    if isinstance(data, pd.DataFrame):
        source = data.to_dict(orient="records")
    elif isinstance(data, list):
        source = data
    else:
        return []

    for r in source:
        if not isinstance(r, dict):
            continue
        record = {}
        for k, v in r.items():
            try:
                # pandas na detection
                if pd.isna(v):
                    record[k] = None
                elif isinstance(v, bool):
                    record[k] = int(v)
                else:
                    record[k] = v
            except Exception:
                record[k] = v
        records.append(record)
    return records

def append_pipeline_run(db_path, run_id, run_datetime, command, mode, status, 
                          strategy_metrics_changed, benchmark_changed, regression_guard_status, total_runtime_sec):
    """Inserts a new pipeline run. Returns tuple (success, warning_msg)."""
    conn = None
    try:
        conn = connect_state_db(db_path)
        with conn:
            conn.execute("""
                INSERT OR IGNORE INTO pipeline_runs (
                    run_id, run_datetime, command, mode, status, 
                    strategy_metrics_changed, benchmark_changed, regression_guard_status, total_runtime_sec
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                run_id, run_datetime, command, mode, status,
                int(strategy_metrics_changed) if strategy_metrics_changed is not None else None,
                int(benchmark_changed) if benchmark_changed is not None else None,
                regression_guard_status,
                total_runtime_sec
            ))
        return True, None
    except Exception as e:
        msg = f"SQLite append_pipeline_run failed: {e}"
        print(f"WARNING: {msg}")
        return False, msg
    finally:
        if conn:
            conn.close()

def append_paper_orders(db_path, run_id, orders_data):
    """Inserts paper orders. Returns tuple (success, warning_msg)."""
    conn = None
    try:
        records = _clean_records(orders_data)
        if not records:
            return True, None
        conn = connect_state_db(db_path)
        with conn:
            for r in records:
                conn.execute("""
                    INSERT OR IGNORE INTO paper_orders (
                        run_id, created_at, signal_date, strategy_name, symbol, action,
                        paper_rank, ranking_score, paper_capital, paper_position_notional,
                        liquidity_guard_pass, status, user_decision, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    run_id,
                    r.get("created_at") or datetime.now().isoformat(),
                    r.get("signal_date") or r.get("run_datetime"),
                    r.get("strategy_name") or r.get("strategy"),
                    r.get("symbol"),
                    r.get("action"),
                    r.get("paper_rank") or r.get("rank"),
                    r.get("ranking_score") or r.get("score"),
                    r.get("paper_capital"),
                    r.get("paper_position_notional") or r.get("notional"),
                    r.get("liquidity_guard_pass"),
                    r.get("status") or r.get("fill_status"),
                    r.get("user_decision") or r.get("decision"),
                    r.get("reason")
                ))
        return True, None
    except Exception as e:
        msg = f"SQLite append_paper_orders failed: {e}"
        print(f"WARNING: {msg}")
        return False, msg
    finally:
        if conn:
            conn.close()

def append_paper_trades(db_path, run_id, trades_data):
    """Inserts paper trades. Returns tuple (success, warning_msg)."""
    conn = None
    try:
        from paper_state_manager import get_stable_key
    except ImportError:
        try:
            from .paper_state_manager import get_stable_key
        except ImportError:
            # Fallback local implementation if imports fail
            def get_stable_key(row):
                import re
                strategy_name = str(row.get("strategy_name") or row.get("strategy") or "").strip()
                symbol = str(row.get("symbol") or "").strip().upper()
                action = str(row.get("action") or "").strip().upper()
                signal_date = str(row.get("signal_date") or "").strip()
                notes = str(row.get("notes") or "")
                fill_bar_timestamp = row.get("fill_bar_timestamp")
                if fill_bar_timestamp is None or (hasattr(pd, "isna") and pd.isna(fill_bar_timestamp)) or str(fill_bar_timestamp) == "nan":
                    m = re.search(r"fill_bar_timestamp=([^;]+)", notes)
                    fill_bar_timestamp = m.group(1).strip() if m else ""
                else:
                    fill_bar_timestamp = str(fill_bar_timestamp).strip()
                fill_price_source = row.get("fill_price_source")
                if fill_price_source is None or (hasattr(pd, "isna") and pd.isna(fill_price_source)) or str(fill_price_source) == "nan":
                    m = re.search(r"fill_price_source=([^;]+)", notes)
                    fill_price_source = m.group(1).strip() if m else ""
                else:
                    fill_price_source = str(fill_price_source).strip()
                return (strategy_name, symbol, action, signal_date, fill_bar_timestamp, fill_price_source)

    try:
        records = _clean_records(trades_data)
        if not records:
            return True, None
        conn = connect_state_db(db_path)
        existing_keys = set()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT strategy_name, symbol, action, signal_date, notes FROM paper_trades")
            for row in cursor.fetchall():
                row_dict = {
                    "strategy_name": row[0],
                    "symbol": row[1],
                    "action": row[2],
                    "signal_date": row[3],
                    "notes": row[4]
                }
                existing_keys.add(get_stable_key(row_dict))
        except Exception as e_read:
            print(f"WARNING: failed to read existing paper_trades for deduplication: {e_read}")
            
        with conn:
            for r in records:
                key = get_stable_key(r)
                if key in existing_keys:
                    continue
                conn.execute("""
                    INSERT OR IGNORE INTO paper_trades (
                        run_id, date, strategy_name, symbol, action, signal_date,
                        signal_price, assumed_fill_price, fill_status, shares, notional,
                        paper_capital, reason, liquidity_guard_pass, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    run_id,
                    r.get("date") or r.get("timestamp") or datetime.now().isoformat(),
                    r.get("strategy_name") or r.get("strategy"),
                    r.get("symbol"),
                    r.get("action"),
                    r.get("signal_date"),
                    r.get("signal_price"),
                    r.get("assumed_fill_price") or r.get("fill_price") or r.get("price"),
                    r.get("fill_status") or r.get("status"),
                    r.get("shares") or r.get("qty"),
                    r.get("notional"),
                    r.get("paper_capital"),
                    r.get("reason"),
                    r.get("liquidity_guard_pass"),
                    r.get("notes") or r.get("message")
                ))
                existing_keys.add(key)
        return True, None
    except Exception as e:
        msg = f"SQLite append_paper_trades failed: {e}"
        print(f"WARNING: {msg}")
        return False, msg
    finally:
        if conn:
            conn.close()

def append_order_intents(db_path, run_id, intents_data):
    """Inserts order intents. Returns tuple (success, warning_msg)."""
    conn = None
    try:
        records = _clean_records(intents_data)
        if not records:
            return True, None
        conn = connect_state_db(db_path)
        with conn:
            for r in records:
                conn.execute("""
                    INSERT OR IGNORE INTO order_intents (
                        run_id, order_intent_id, idempotency_key, idempotency_phase, execution_idempotency_key,
                        created_at, strategy_name, symbol, side, notional, estimated_qty,
                        order_type, time_in_force, source_signal_date, status, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    run_id,
                    r.get("order_intent_id") or r.get("intent_id"),
                    r.get("idempotency_key"),
                    r.get("idempotency_phase"),
                    r.get("execution_idempotency_key"),
                    r.get("created_at") or datetime.now().isoformat(),
                    r.get("strategy_name") or r.get("strategy"),
                    r.get("symbol"),
                    r.get("side") or r.get("action"),
                    r.get("notional"),
                    r.get("estimated_qty") or r.get("qty"),
                    r.get("order_type"),
                    r.get("time_in_force"),
                    r.get("source_signal_date") or r.get("signal_date"),
                    r.get("status"),
                    r.get("reason")
                ))
        return True, None
    except Exception as e:
        msg = f"SQLite append_order_intents failed: {e}"
        print(f"WARNING: {msg}")
        return False, msg
    finally:
        if conn:
            conn.close()

def append_execution_audit(db_path, run_id, audit_data):
    """Inserts execution audits. Returns tuple (success, warning_msg)."""
    conn = None
    try:
        records = _clean_records(audit_data)
        if not records:
            return True, None
        conn = connect_state_db(db_path)
        with conn:
            for r in records:
                conn.execute("""
                    INSERT OR IGNORE INTO execution_audit (
                        run_id, run_datetime, event_type, order_intent_id, idempotency_key,
                        idempotency_phase, execution_idempotency_key, strategy_name, symbol, side,
                        status, risk_pass, block_reason, would_submit, broker_submit_enabled,
                        live_trading_enabled, message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    run_id,
                    r.get("run_datetime") or r.get("timestamp") or datetime.now().isoformat(),
                    r.get("event_type") or r.get("event"),
                    r.get("order_intent_id") or r.get("intent_id"),
                    r.get("idempotency_key"),
                    r.get("idempotency_phase"),
                    r.get("execution_idempotency_key"),
                    r.get("strategy_name") or r.get("strategy"),
                    r.get("symbol"),
                    r.get("side") or r.get("action"),
                    r.get("status"),
                    r.get("risk_pass"),
                    r.get("block_reason"),
                    r.get("would_submit"),
                    r.get("broker_submit_enabled"),
                    r.get("live_trading_enabled"),
                    r.get("message")
                ))
        return True, None
    except Exception as e:
        msg = f"SQLite append_execution_audit failed: {e}"
        print(f"WARNING: {msg}")
        return False, msg
    finally:
        if conn:
            conn.close()

def append_pre_submit_checks(db_path, run_id, checks_data):
    """Inserts pre-submit safety checks. Returns tuple (success, warning_msg)."""
    conn = None
    try:
        records = _clean_records(checks_data)
        if not records:
            return True, None
        conn = connect_state_db(db_path)
        with conn:
            for r in records:
                conn.execute("""
                    INSERT OR IGNORE INTO readiness_checks (
                        run_id, check_type, check_name, status, value, required_value, message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """, (
                    run_id,
                    "pre_submit",
                    r.get("check_name"),
                    r.get("status"),
                    str(r.get("value")) if r.get("value") is not None else None,
                    str(r.get("required_value")) if r.get("required_value") is not None else None,
                    r.get("message")
                ))
        return True, None
    except Exception as e:
        msg = f"SQLite append_pre_submit_checks failed: {e}"
        print(f"WARNING: {msg}")
        return False, msg
    finally:
        if conn:
            conn.close()

def append_short_readiness_checks(db_path, run_id, checks_data):
    """Inserts short readiness checks. Returns tuple (success, warning_msg)."""
    conn = None
    try:
        records = _clean_records(checks_data)
        if not records:
            return True, None
        conn = connect_state_db(db_path)
        with conn:
            for r in records:
                conn.execute("""
                    INSERT OR IGNORE INTO readiness_checks (
                        run_id, check_type, check_name, status, value, required_value, message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """, (
                    run_id,
                    "short_readiness",
                    r.get("check_name"),
                    r.get("status"),
                    str(r.get("value")) if r.get("value") is not None else None,
                    str(r.get("required_value")) if r.get("required_value") is not None else None,
                    r.get("message")
                ))
        return True, None
    except Exception as e:
        msg = f"SQLite append_short_readiness_checks failed: {e}"
        print(f"WARNING: {msg}")
        return False, msg
    finally:
        if conn:
            conn.close()

def get_state_summary(db_path):
    """Reads SQLite database table counts and status information. Returns a summary dictionary."""
    summary = {
        "db_path": os.path.abspath(db_path),
        "db_exists": os.path.exists(db_path),
        "table_count": 0,
        "pipeline_runs_count": 0,
        "paper_orders_count": 0,
        "paper_trades_count": 0,
        "order_intents_count": 0,
        "execution_audit_count": 0,
        "readiness_checks_count": 0,
        "latest_run_id": None,
        "status": "WARNING",
        "error_msg": None
    }
    
    if not summary["db_exists"]:
        return summary
        
    conn = None
    try:
        conn = connect_state_db(db_path)
        cursor = conn.cursor()
        
        # Get table list
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cursor.fetchall()]
        summary["table_count"] = len(tables)
        
        # Ensure schema meta is initialized or check tables
        if "schema_meta" in tables:
            cursor.execute("SELECT schema_version FROM schema_meta LIMIT 1;")
            row = cursor.fetchone()
            # If we could read, status is OK so far
            summary["status"] = "OK"

        if "pipeline_runs" in tables:
            cursor.execute("SELECT COUNT(*) FROM pipeline_runs;")
            summary["pipeline_runs_count"] = cursor.fetchone()[0]
            cursor.execute("SELECT run_id FROM pipeline_runs ORDER BY run_datetime DESC LIMIT 1;")
            latest_run = cursor.fetchone()
            if latest_run:
                summary["latest_run_id"] = latest_run[0]
                
        if "paper_orders" in tables:
            cursor.execute("SELECT COUNT(*) FROM paper_orders;")
            summary["paper_orders_count"] = cursor.fetchone()[0]
            
        if "paper_trades" in tables:
            cursor.execute("SELECT COUNT(*) FROM paper_trades;")
            summary["paper_trades_count"] = cursor.fetchone()[0]
            
        if "order_intents" in tables:
            cursor.execute("SELECT COUNT(*) FROM order_intents;")
            summary["order_intents_count"] = cursor.fetchone()[0]
            
        if "execution_audit" in tables:
            cursor.execute("SELECT COUNT(*) FROM execution_audit;")
            summary["execution_audit_count"] = cursor.fetchone()[0]
            
        if "readiness_checks" in tables:
            cursor.execute("SELECT COUNT(*) FROM readiness_checks;")
            summary["readiness_checks_count"] = cursor.fetchone()[0]
            
        if summary["table_count"] >= 8: # updated to 8 tables including sqlite_sequence if autoincrement is used
            summary["status"] = "OK"
            
    except Exception as e:
        summary["status"] = "WARNING"
        summary["error_msg"] = str(e)
    finally:
        if conn:
            conn.close()
            
    return summary
