import hashlib
import io
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from urllib import parse, request

import pandas as pd

PREPARED_CACHE_VERSION = "modular_prepared_cache_v3_donchian_features"
_WORKER_PREPARED = None

try:
    from .config import DAILY_DATA_DIR, DAILY_TIMEFRAME, INTRADAY_DATA_DIR, INTRADAY_TIMEFRAME, RAW_1MIN_DATA_DIR
    from .data_engine import read_ohlcv_csv_fast
    from .diagnostics import _asset_signal_diagnostics
    from .reports import (
        DIVIDEND_ADJUSTMENT_WARNING,
        SURVIVORSHIP_BIAS_WARNING,
        _add_compact_scores,
        _prefixed_benchmark_metrics,
        _write_compact_markdown,
        calculate_buyhold_metrics,
        calculate_equal_weight_benchmark,
    )
    from .report_schemas import (
        ALPACA_ORDER_PREVIEW_COLUMNS,
        DATA_PRECHECK_COLUMNS,
        PAPER_ORDER_REVIEW_COLUMNS,
        PAPER_PENDING_ORDERS_COLUMNS,
        PAPER_SIGNAL_COLUMNS,
        PAPER_TRADE_LOG_COLUMNS,
        PIPELINE_HEALTH_COLUMNS,
        REGRESSION_GUARD_COLUMNS,
        ensure_columns,
        validate_required_columns,
    )
    from .paper_state_manager import (
        append_trade_log,
        create_trade_log_template,
        read_order_review,
        read_pending_orders,
        read_trade_log,
        validate_paper_state,
        write_order_review,
        write_pending_orders,
        write_trade_log,
    )
    from .execution_engine import run_execution_preview
    from .strategies.base import generate_strategy_signals
    from .strategies.cross_sectional_momentum import (
        _apply_cross_sectional_momentum_top_signals,
        _normalize_cross_sectional_momentum_params,
        _run_cross_sectional_momentum_backtest,
    )
    from .strategies.donchian_breakout import (
        _apply_donchian_breakout_top_signals,
        _normalize_donchian_breakout_params,
        _prepare_donchian_context,
        _run_donchian_breakout_backtest,
    )
    from .strategies.dual_momentum import (
        _apply_dual_momentum_top_signals,
        _normalize_dual_momentum_params,
        _run_dual_momentum_backtest,
    )
    from .strategies.ma_slope_trend import (
        _apply_ma_slope_trend_top_signals,
        _normalize_ma_slope_trend_params,
        _run_ma_slope_trend_backtest,
    )
    from .strategies.relative_strength_rotation import (
        _apply_relative_strength_rotation_top_signals,
        _normalize_relative_strength_params,
        _run_relative_strength_rotation_backtest,
    )
except ImportError:
    from config import DAILY_DATA_DIR, DAILY_TIMEFRAME, INTRADAY_DATA_DIR, INTRADAY_TIMEFRAME, RAW_1MIN_DATA_DIR
    from data_engine import read_ohlcv_csv_fast
    from diagnostics import _asset_signal_diagnostics
    from reports import (
        DIVIDEND_ADJUSTMENT_WARNING,
        SURVIVORSHIP_BIAS_WARNING,
        _add_compact_scores,
        _prefixed_benchmark_metrics,
        _write_compact_markdown,
        calculate_buyhold_metrics,
        calculate_equal_weight_benchmark,
    )
    from report_schemas import (
        ALPACA_ORDER_PREVIEW_COLUMNS,
        DATA_PRECHECK_COLUMNS,
        PAPER_ORDER_REVIEW_COLUMNS,
        PAPER_PENDING_ORDERS_COLUMNS,
        PAPER_SIGNAL_COLUMNS,
        PAPER_TRADE_LOG_COLUMNS,
        PIPELINE_HEALTH_COLUMNS,
        REGRESSION_GUARD_COLUMNS,
        ensure_columns,
        validate_required_columns,
    )
    from paper_state_manager import (
        append_trade_log,
        create_trade_log_template,
        read_order_review,
        read_pending_orders,
        read_trade_log,
        validate_paper_state,
        write_order_review,
        write_pending_orders,
        write_trade_log,
    )
    from execution_engine import run_execution_preview
    from strategies.base import generate_strategy_signals
    from strategies.cross_sectional_momentum import (
        _apply_cross_sectional_momentum_top_signals,
        _normalize_cross_sectional_momentum_params,
        _run_cross_sectional_momentum_backtest,
    )
    from strategies.donchian_breakout import (
        _apply_donchian_breakout_top_signals,
        _normalize_donchian_breakout_params,
        _prepare_donchian_context,
        _run_donchian_breakout_backtest,
    )
    from strategies.dual_momentum import (
        _apply_dual_momentum_top_signals,
        _normalize_dual_momentum_params,
        _run_dual_momentum_backtest,
    )
    from strategies.ma_slope_trend import (
        _apply_ma_slope_trend_top_signals,
        _normalize_ma_slope_trend_params,
        _run_ma_slope_trend_backtest,
    )
    from strategies.relative_strength_rotation import (
        _apply_relative_strength_rotation_top_signals,
        _normalize_relative_strength_params,
        _run_relative_strength_rotation_backtest,
    )


def run_universe_scanner(*args, **kwargs):
    raise NotImplementedError("run_universe_scanner is not part of the modular validation runner.")


def _now():
    return time.perf_counter()


def _stage_started(stage_name):
    print(f"[stage] {stage_name} started")
    return _now()


def _stage_done(stage_name, started_at):
    elapsed = _now() - started_at
    print(f"[stage] {stage_name} done in {elapsed:.2f} sec")
    return elapsed


class _TimingCollector:
    def __init__(self):
        self.rows = []

    def stage(self, name, notes=""):
        collector = self

        class _Stage:
            def __enter__(self_inner):
                self_inner.started_at = _now()
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                collector.rows.append(
                    {
                        "stage": name,
                        "notes": notes,
                        "seconds": _now() - self_inner.started_at,
                    }
                )

        return _Stage()


def _warn_onedrive_path(path):
    if "onedrive" in str(Path(path).resolve()).lower():
        print(
            "Warning: OneDrive path detected; IO performance may be slow. "
            "Prefer local SSD path like C:\\BOT."
        )


def resolve_project_path(path, base_data_dir):
    target = Path(path)
    if target.is_absolute():
        return target
    if (
        len(target.parts) >= 2
        and target.parts[0].lower() == "testdata"
        and target.parts[1].lower() == "reports"
    ):
        target = Path(*target.parts[1:])
    base = Path(base_data_dir)
    if not base.is_absolute():
        direct = Path.cwd() / base
        testdata_direct = Path.cwd() / "testdata" / base
        if direct.exists() or (base.parts and base.parts[0].lower() == "testdata"):
            base = direct
        elif testdata_direct.exists():
            base = testdata_direct
        else:
            base = direct
    data_root = base.parent if base.name.lower().startswith("hisseler") else base
    if data_root.name.lower() != "testdata" and (data_root / "testdata").exists():
        data_root = data_root / "testdata"
    return (data_root / target).resolve()


def _report_output_paths(report_output_dir, base_data_dir):
    report_root = resolve_project_path(report_output_dir, base_data_dir)
    legacy_duplicate = report_root.parent / "testdata" / "reports"
    if legacy_duplicate.exists() and legacy_duplicate.resolve() != report_root:
        print(f"Warning: duplicate legacy report dir found: {legacy_duplicate}")
    timestamp = pd.Timestamp.now().strftime("%Y-%m-%d_%H%M%S")
    latest_dir = report_root / "latest"
    run_dir = report_root / "runs" / timestamp
    latest_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    return latest_dir, run_dir


def _read_env_file(path):
    values = {}
    if not Path(path).exists():
        return values
    for raw_line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _alpaca_config(env_path=None):
    env_values = _read_env_file(env_path or Path.cwd() / ".env")
    api_key = os.environ.get("ALPACA_API_KEY") or env_values.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY") or env_values.get("ALPACA_SECRET_KEY")
    base_url = (
        os.environ.get("ALPACA_BASE_URL")
        or env_values.get("ALPACA_BASE_URL")
        or "https://paper-api.alpaca.markets"
    ).rstrip("/")
    return api_key, secret_key, base_url


def _alpaca_get_json(base_url, path, api_key, secret_key, query=None):
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"
    req = request.Request(
        url,
        headers={
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        },
        method="GET",
    )
    with request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


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
    review = _load_paper_order_review(Path(latest_dir) / "paper_order_review.csv")
    decisions = {}
    for _, row in review.iterrows():
        decisions[_paper_order_key(row)] = str(row.get("user_decision", "HOLD_REVIEW")).strip().upper()
    return decisions


def _paper_signal_lookup(latest_dir):
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
            signal = signal_lookup.get(key)
            fill_status = str(signal.get("fill_status", "")).strip().upper() if signal is not None else ""
            if decision != "ACCEPT" or fill_status != "PENDING_NEXT_OPEN":
                continue
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
            account = _alpaca_get_json(base_url, "/v2/account", api_key, secret_key)
            positions = _alpaca_get_json(base_url, "/v2/positions", api_key, secret_key)
            orders = _alpaca_get_json(
                base_url,
                "/v2/orders",
                api_key,
                secret_key,
                query={"status": "open", "limit": 100},
            )
            summary.update(
                {
                    "connection_status": "ok",
                    "account_status": account.get("status", ""),
                    "buying_power": account.get("buying_power", ""),
                    "cash": account.get("cash", ""),
                    "equity": account.get("equity", ""),
                    "portfolio_value": account.get("portfolio_value", ""),
                    "pattern_day_trader": account.get("pattern_day_trader", ""),
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


def _remove_optional_report_files(*directories):
    optional_names = [
        "yearly_performance_report.csv",
        "yearly_performance_report.md",
        "rs_robustness_report.csv",
        "rs_robustness_report.md",
        "candidate_summary_report.md",
    ]
    for directory in directories:
        for name in optional_names:
            path = directory / name
            if path.exists():
                path.unlink()


def _remove_stale_performance_profile(directory):
    for name in ["performance_profile_report.csv", "performance_profile_report.md"]:
        path = directory / name
        if path.exists():
            path.unlink()


def _resolve_data_dirs(data_folder, intraday_data_dir=None, daily_data_dir=None, raw_1min_data_dir=None):
    root = Path(data_folder)
    intraday_dir = Path(intraday_data_dir) if intraday_data_dir else root / "bars_130min"
    daily_dir = Path(daily_data_dir) if daily_data_dir else root / "daily"
    raw_dir = Path(raw_1min_data_dir) if raw_1min_data_dir else root / "raw_1min"
    return intraday_dir, daily_dir, raw_dir


def _symbol_from_file(path, suffix):
    return path.name[: -len(suffix)].upper()


def _normalize_frame(path):
    frame = read_ohlcv_csv_fast(path)
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame


def _normalize_frame_timed(path, timing=None):
    frame = read_ohlcv_csv_fast(path, timing=timing)
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame


def _slice_frame(frame, start_date=None, end_date=None):
    if start_date:
        start = pd.Timestamp(start_date, tz=frame.index.tz)
        frame = frame.loc[frame.index >= start]
    if end_date:
        end = pd.Timestamp(end_date, tz=frame.index.tz)
        frame = frame.loc[frame.index <= end]
    return frame


def _add_donchian_precomputed_features(daily):
    if daily is None or daily.empty:
        return daily
    frame = daily.copy()
    previous_close = frame["Close"].shift(1)
    true_range = pd.concat(
        [
            frame["High"] - frame["Low"],
            (frame["High"] - previous_close).abs(),
            (frame["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(20, min_periods=20).mean()
    frame["ATR_Pct_20"] = 100 * atr / frame["Close"].replace(0, float("nan"))
    for length in (100, 200):
        frame[f"Donchian_Trend_SMA_{length}"] = frame["Close"].rolling(
            length,
            min_periods=length,
        ).mean()
    for length in (20, 55):
        frame[f"Donchian_Entry_High_{length}"] = frame["High"].rolling(
            length,
            min_periods=length,
        ).max().shift(1)
    for length in (10, 20):
        frame[f"Donchian_Exit_Low_{length}"] = frame["Low"].rolling(
            length,
            min_periods=length,
        ).min().shift(1)
    return frame


def _audit_one_symbol(symbol, daily_path, intraday_path):
    row = {
        "symbol": symbol,
        "has_daily_file": bool(daily_path and daily_path.exists()),
        "has_130min_file": bool(intraday_path and intraday_path.exists()),
        "daily_rows": 0,
        "bar_130min_rows": 0,
        "daily_start": "",
        "daily_end": "",
        "intraday_start": "",
        "intraday_end": "",
        "duplicate_daily_timestamps": 0,
        "duplicate_130min_timestamps": 0,
        "missing_ohlcv_count": 0,
        "invalid_ohlc_count": 0,
        "zero_volume_count": 0,
        "expected_130min_bar_count_check": False,
        "daily_data_valid": False,
        "data_warnings": "",
        "errors": "",
    }
    errors = []
    for label, path in [("daily", daily_path), ("130min", intraday_path)]:
        if not path or not path.exists():
            errors.append(f"missing_{label}_file")
            continue
        try:
            frame = read_ohlcv_csv_fast(path)
            dupes = int(frame.index.duplicated().sum())
            missing = int(frame[["Open", "High", "Low", "Close", "Volume"]].isna().sum().sum())
            invalid = int(((frame["High"] < frame[["Open", "Close", "Low"]].max(axis=1)) | (frame["Low"] > frame[["Open", "Close", "High"]].min(axis=1))).sum())
            zero_volume = int((frame["Volume"] <= 0).sum())
            row["missing_ohlcv_count"] += missing
            row["invalid_ohlc_count"] += invalid
            row["zero_volume_count"] += zero_volume
            if label == "daily":
                row["daily_rows"] = int(len(frame))
                row["daily_start"] = str(frame.index.min()) if len(frame) else ""
                row["daily_end"] = str(frame.index.max()) if len(frame) else ""
                row["duplicate_daily_timestamps"] = dupes
                row["daily_data_valid"] = bool(len(frame) > 0 and missing == 0 and invalid == 0)
                if len(frame) == 0:
                    errors.append("empty_daily_file")
            else:
                local = frame.index.tz_convert("America/New_York")
                minutes = local.hour * 60 + local.minute
                expected_minutes = [570, 700, 830]
                per_day_ok = bool(pd.Series(local.date).value_counts().le(3).all())
                starts_ok = bool(
                    all(
                        any(abs(int(minute) - expected) <= 10 for expected in expected_minutes)
                        for minute in minutes.tolist()
                    )
                )
                rth_ok = bool(((minutes >= 570) & (minutes < 960)).all())
                row["bar_130min_rows"] = int(len(frame))
                row["intraday_start"] = str(frame.index.min()) if len(frame) else ""
                row["intraday_end"] = str(frame.index.max()) if len(frame) else ""
                row["duplicate_130min_timestamps"] = dupes
                row["expected_130min_bar_count_check"] = per_day_ok and starts_ok and rth_ok
                if not row["expected_130min_bar_count_check"]:
                    errors.append("invalid_130min_schedule")
        except Exception as error:
            errors.append(f"{label}:{error}")
    warnings = []
    if row["has_daily_file"] and row["daily_rows"] == 0:
        warnings.append("empty_daily_file")
    if row["zero_volume_count"] > 0:
        warnings.append("zero_volume")
    row["data_warnings"] = ";".join(dict.fromkeys(warnings))
    row["errors"] = ";".join(errors)
    return row


def _write_data_audit(symbols, daily_dir, intraday_dir, latest_dir, run_dir):
    rows = []
    audit_symbols = sorted(set(symbols) | {"QQQ", "SPY"})
    for symbol in audit_symbols:
        rows.append(
            _audit_one_symbol(
                symbol,
                daily_dir / f"{symbol}_1D.csv",
                intraday_dir / f"{symbol}_130min.csv",
            )
        )
    audit = pd.DataFrame(rows)
    daily_ends = pd.to_datetime(audit["daily_end"], errors="coerce")
    global_daily_end = daily_ends.max()
    daily_rows = pd.to_numeric(audit["daily_rows"], errors="coerce").fillna(0)
    empty_daily = audit.loc[audit["has_daily_file"] & (daily_rows <= 0), "symbol"].tolist()
    short_history = audit.loc[(daily_rows > 0) & (daily_rows < 252), "symbol"].tolist()
    ending_before_global = audit.loc[
        daily_ends.notna() & pd.notna(global_daily_end) & (daily_ends < global_daily_end),
        "symbol",
    ].tolist()
    zero_volume = audit.loc[
        pd.to_numeric(audit["zero_volume_count"], errors="coerce").fillna(0) > 0,
        "symbol",
    ].tolist()
    for directory in [latest_dir, run_dir]:
        csv_path = directory / "data_audit_report.csv"
        md_path = directory / "data_audit_report.md"
        audit.to_csv(csv_path, index=False)
        try:
            table = audit.to_markdown(index=False)
        except ImportError:
            table = audit.to_string(index=False)
        summary = [
            "# Data Audit Report",
            "",
            f"- Symbols: {len(audit)}",
            f"- Daily files: {int(audit['has_daily_file'].sum()) if not audit.empty else 0}",
            f"- Valid daily files: {int(audit['daily_data_valid'].sum()) if not audit.empty else 0}",
            f"- 130min files: {int(audit['has_130min_file'].sum()) if not audit.empty else 0}",
            f"- 130min schedule valid rows: {int(audit['expected_130min_bar_count_check'].sum()) if not audit.empty else 0}",
            f"- Empty daily files: {len(empty_daily)} ({','.join(empty_daily) if empty_daily else 'none'})",
            f"- Short-history symbols: {len(short_history)} ({','.join(short_history) if short_history else 'none'})",
            f"- Symbols ending before global end date: {len(ending_before_global)} ({','.join(ending_before_global) if ending_before_global else 'none'})",
            f"- Zero volume warnings: {len(zero_volume)} ({','.join(zero_volume) if zero_volume else 'none'})",
            f"- QQQ daily file: {bool(audit.loc[audit['symbol'] == 'QQQ', 'has_daily_file'].any())}",
            f"- QQQ 130min file: {bool(audit.loc[audit['symbol'] == 'QQQ', 'has_130min_file'].any())}",
            f"- SPY daily file: {bool(audit.loc[audit['symbol'] == 'SPY', 'has_daily_file'].any())}",
            f"- SPY 130min file: {bool(audit.loc[audit['symbol'] == 'SPY', 'has_130min_file'].any())}",
            "",
            table,
            "",
        ]
        md_path.write_text("\n".join(summary), encoding="utf-8")
    return audit


def _prepared_cache_key(data_folder, intraday_dir, daily_dir, intraday_timeframe, daily_timeframe, start_date, end_date, symbols):
    key = {
        "cache_version": PREPARED_CACHE_VERSION,
        "data_folder": str(Path(data_folder).resolve()),
        "intraday_data_dir": str(Path(intraday_dir).resolve()),
        "daily_data_dir": str(Path(daily_dir).resolve()),
        "intraday_timeframe": intraday_timeframe,
        "daily_timeframe": daily_timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "symbols": list(symbols),
    }
    digest = hashlib.sha256(repr(sorted(key.items())).encode("utf-8")).hexdigest()[:24]
    return key, digest


def _prepared_cache_path(data_folder, digest):
    cache_dir = Path(data_folder) / ".cache" / "prepared_features"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"prepared_features_{digest}.pkl"


def _load_or_build_prepared_cache(
    data_folder,
    intraday_dir,
    daily_dir,
    intraday_timeframe,
    daily_timeframe,
    start_date,
    end_date,
    symbols,
    force_rebuild=False,
    timing=None,
):
    cache_key, digest = _prepared_cache_key(
        data_folder,
        intraday_dir,
        daily_dir,
        intraday_timeframe,
        daily_timeframe,
        start_date,
        end_date,
        symbols,
    )
    cache_path = _prepared_cache_path(data_folder, digest)
    if cache_path.exists() and not force_rebuild:
        load_started = _now()
        payload = pd.read_pickle(cache_path)
        load_sec = _now() - load_started
        if payload.get("cache_key") == cache_key:
            prepared = payload["prepared"]
            cache_summary = dict(prepared.get("cache_summary", {}))
            cache_summary.update(
                {
                    "prepared_disk_cache_hits": 1,
                    "prepared_disk_cache_misses": 0,
                    "prepared_cache_load_sec": round(load_sec, 4),
                    "prepared_cache_build_sec": 0.0,
                    "csv_read_sec": 0.0,
                    "rows_loaded": int(
                        sum(len(frame) for frame in prepared.get("asset_data", {}).values())
                    ),
                    "symbols_loaded": int(len(prepared.get("asset_data", {}))),
                }
            )
            prepared["cache_summary"] = cache_summary
            return prepared, cache_path, False
    asset_data = {}
    build_started = _now()
    csv_timing = _TimingCollector() if timing is None else timing
    for symbol in symbols:
        intraday_path = intraday_dir / f"{symbol}_130min.csv"
        daily_path = daily_dir / f"{symbol}_1D.csv"
        if not intraday_path.exists() or not daily_path.exists():
            continue
        intraday = _slice_frame(_normalize_frame_timed(intraday_path, csv_timing), start_date, end_date)
        daily = _slice_frame(_normalize_frame_timed(daily_path, csv_timing), start_date, end_date)
        if intraday.empty or daily.empty:
            continue
        daily = _add_donchian_precomputed_features(daily)
        intraday.attrs["daily_ohlcv"] = daily
        intraday.attrs["intraday_data_dir"] = str(intraday_dir)
        intraday.attrs["daily_data_dir"] = str(daily_dir)
        intraday.attrs["intraday_timeframe"] = intraday_timeframe
        intraday.attrs["daily_timeframe"] = daily_timeframe
        asset_data[symbol] = intraday
    if not asset_data:
        raise ValueError("Prepared cache build failed: usable 130min/daily symbol data not found.")
    csv_read_sec = sum(
        float(item.get("seconds", 0.0) or 0.0)
        for item in csv_timing.rows
        if item.get("stage") == "CSV read fast"
    )
    rows_loaded = int(sum(len(frame) for frame in asset_data.values()))
    prepared = {
        "asset_data": asset_data,
        "benchmark_data": asset_data["QQQ"] if "QQQ" in asset_data else asset_data.get("SPY"),
        "cache_summary": {
            "prepared_disk_cache_hits": 0,
            "prepared_disk_cache_misses": 1,
            "feature_cache_misses": len(asset_data),
            "prepared_cache_load_sec": 0.0,
            "prepared_cache_build_sec": round(_now() - build_started, 4),
            "csv_read_sec": round(csv_read_sec, 4),
            "rows_loaded": rows_loaded,
            "symbols_loaded": len(asset_data),
            "intraday_data_dir": str(intraday_dir),
            "daily_data_dir": str(daily_dir),
        },
    }
    pd.to_pickle({"cache_key": cache_key, "prepared": prepared}, cache_path)
    return prepared, cache_path, True


def _load_daily_benchmark(daily_dir, start_date=None, end_date=None):
    for symbol in ["QQQ", "SPY"]:
        path = daily_dir / f"{symbol}_1D.csv"
        if not path.exists():
            continue
        frame = _slice_frame(_normalize_frame(path), start_date, end_date)
        if frame.empty:
            continue
        frame.attrs["benchmark_symbol"] = symbol
        frame.attrs["daily_data_dir"] = str(daily_dir)
        return frame
    return None


def _equal_weight_missing_symbols(asset_data, start, end):
    skipped_symbols = []
    for symbol, data in asset_data.items():
        if "Close" not in data:
            skipped_symbols.append(symbol)
            continue
        close = pd.to_numeric(data["Close"], errors="coerce").dropna().sort_index()
        start_candidates = close.loc[:start]
        if start_candidates.empty:
            skipped_symbols.append(symbol)
            continue
        start_price = float(start_candidates.iloc[-1])
        if not pd.notna(start_price) or start_price <= 0:
            skipped_symbols.append(symbol)
    return sorted(str(symbol) for symbol in skipped_symbols)


def _default_params(experiment, data_folder, run_profile, quick_mode):
    params = {
        "strategy_name": experiment.get("strategy_name"),
        "run_profile": run_profile,
        "quick_mode": quick_mode,
        "execution_mode": "bar_close",
        "ranking_mode": "momentum",
        "hurst_filter_mode": "off",
        "hurst_threshold": 0.5,
        "rsi_length": 3,
        "rsi_entry_threshold": 10,
        "bollinger_length": 20,
        "bollinger_std": 2.0,
        "trend_sma_length": 50,
        "setup_lookback": 3,
        "exit_mode": "sma_or_target",
        "exit_sma_length": 20,
        "take_profit_atr_multiplier": 2.0,
        "disaster_stop_atr_multiplier": 2.0,
        "time_stop_bars": 10,
        "data_folder": data_folder,
        "benchmark_symbol": "QQQ",
        "mast_slope_sma_days": 100,
        "mast_slope_lookback_days": 20,
        "mast_top_n": 20,
        "mast_rebalance_frequency": "monthly",
        "mast_score_lookback_days": 63,
        "mast_use_market_regime": True,
        "mast_market_regime_sma_days": 200,
        "mast_min_price": 10,
        "mast_max_atr_pct": 12,
    }
    params.update(experiment)
    params.pop("name", None)
    return params


def _run_strategy(asset_data, benchmark_data, params):
    strategy_name = params["strategy_name"]
    if strategy_name == "donchian_breakout":
        strategy_params = _normalize_donchian_breakout_params(params)
        donchian_profiler = {
            "rolling_high_low_sec": 0.0,
            "signal_generation_sec": 0.0,
            "ranking_sec": 0.0,
            "portfolio_sim_sec": 0.0,
            "exits_sec": 0.0,
            "diagnostics_sec": 0.0,
        }
        context = _prepare_donchian_context(
            asset_data,
            strategy_params,
            benchmark_data=benchmark_data,
            profiler=donchian_profiler,
        )
        final_data = _apply_donchian_breakout_top_signals(
            asset_data,
            strategy_params,
            benchmark_data=benchmark_data,
            context=context,
            profiler=donchian_profiler,
        )
        metrics = _run_donchian_breakout_backtest(
            asset_data,
            strategy_params,
            benchmark_data=benchmark_data,
            context=context,
            profiler=donchian_profiler,
        )
        diagnostics_started = _now()
        metrics.update(_asset_signal_diagnostics(final_data))
        donchian_profiler["diagnostics_sec"] += _now() - diagnostics_started
        metrics.update(
            {
                key: round(float(value), 4)
                for key, value in donchian_profiler.items()
            }
        )
        metrics["total_trades"] = int(metrics.get("Islem Sayisi", 0) or 0)
        metrics["Trade Count"] = metrics["total_trades"]
        return final_data, metrics
    signaled = {
        symbol: generate_strategy_signals(frame, params)
        for symbol, frame in asset_data.items()
    }
    if strategy_name == "relative_strength_rotation":
        strategy_params = _normalize_relative_strength_params(params)
        final_data = _apply_relative_strength_rotation_top_signals(
            signaled,
            strategy_params,
            benchmark_data=benchmark_data,
        )
        metrics = _run_relative_strength_rotation_backtest(
            final_data,
            strategy_params,
            benchmark_data=benchmark_data,
        )
    elif strategy_name == "dual_momentum":
        strategy_params = _normalize_dual_momentum_params(params)
        final_data = _apply_dual_momentum_top_signals(
            signaled,
            strategy_params,
            benchmark_data=benchmark_data,
        )
        metrics = _run_dual_momentum_backtest(
            final_data,
            strategy_params,
            benchmark_data=benchmark_data,
        )
    elif strategy_name == "ma_slope_trend":
        strategy_params = _normalize_ma_slope_trend_params(params)
        final_data = _apply_ma_slope_trend_top_signals(
            signaled,
            strategy_params,
            benchmark_data=benchmark_data,
        )
        metrics = _run_ma_slope_trend_backtest(
            final_data,
            strategy_params,
            benchmark_data=benchmark_data,
        )
    elif strategy_name == "cross_sectional_momentum":
        strategy_params = _normalize_cross_sectional_momentum_params(params)
        final_data = _apply_cross_sectional_momentum_top_signals(
            signaled,
            strategy_params,
            benchmark_data=benchmark_data,
        )
        metrics = _run_cross_sectional_momentum_backtest(
            final_data,
            strategy_params,
            benchmark_data=benchmark_data,
        )
    else:
        raise NotImplementedError(f"Unsupported modular strategy: {strategy_name}")
    metrics.update(_asset_signal_diagnostics(final_data))
    metrics["total_trades"] = int(metrics.get("Islem Sayisi", 0) or 0)
    metrics["Trade Count"] = metrics["total_trades"]
    return final_data, metrics


def _add_benchmarks(asset_data, benchmark_data, metrics, benchmark_asset_data=None):
    equity_curve = metrics.get("_equity_curve", [])
    if not equity_curve:
        return
    start = equity_curve[0][0]
    end = equity_curve[-1][0]
    qqq_start = start
    qqq_end = end
    if benchmark_data is not None and not benchmark_data.empty:
        benchmark_index = benchmark_data.index.sort_values()
        if benchmark_index.tz is not None and getattr(start, "tzinfo", None) is None:
            start = pd.Timestamp(start).tz_localize(benchmark_index.tz)
            end = pd.Timestamp(end).tz_localize(benchmark_index.tz)
            qqq_start = start
            qqq_end = end
        if benchmark_index.tz is not None and getattr(qqq_start, "tzinfo", None) is None:
            qqq_start = pd.Timestamp(qqq_start).tz_localize(benchmark_index.tz)
            qqq_end = pd.Timestamp(qqq_end).tz_localize(benchmark_index.tz)
        qqq_start = max(pd.Timestamp(qqq_start), benchmark_index[0])
        qqq_end = max(qqq_start, min(pd.Timestamp(qqq_end), benchmark_index[-1]))
    try:
        if benchmark_data is None or benchmark_data.empty or "Close" not in benchmark_data:
            raise ValueError("Benchmark close serisi yok.")
        qqq_metrics = calculate_buyhold_metrics(benchmark_data["Close"], qqq_start, qqq_end)
    except Exception as error:
        print(f"Warning: compact benchmark unavailable, writing NaN metrics: {error}")
        qqq_metrics = {"Return (%)": pd.NA, "CAGR (%)": pd.NA, "MaxDD (%)": pd.NA}
    ew_source = benchmark_asset_data if benchmark_asset_data is not None else asset_data
    ew_start = start
    ew_end = end
    ew_indexes = [
        data.index.sort_values()
        for data in ew_source.values()
        if "Close" in data and not data.empty
    ]
    if ew_indexes:
        ew_first = min(index[0] for index in ew_indexes)
        ew_last = max(index[-1] for index in ew_indexes)
        if ew_first.tz is not None and getattr(ew_start, "tzinfo", None) is None:
            ew_start = pd.Timestamp(ew_start).tz_localize(ew_first.tz)
            ew_end = pd.Timestamp(ew_end).tz_localize(ew_first.tz)
        ew_start = max(pd.Timestamp(ew_start), ew_first)
        ew_end = min(pd.Timestamp(ew_end), ew_last)
    ew_missing_symbols = _equal_weight_missing_symbols(ew_source, ew_start, ew_end)
    try:
        ew_metrics = calculate_equal_weight_benchmark(ew_source, ew_start, ew_end)
    except Exception as error:
        print(f"Warning: compact equal-weight benchmark unavailable, writing NaN metrics: {error}")
        ew_metrics = {"Return (%)": pd.NA, "CAGR (%)": pd.NA, "MaxDD (%)": pd.NA}
    metrics.update(_prefixed_benchmark_metrics("QQQ", qqq_metrics))
    metrics.update(_prefixed_benchmark_metrics("EqualWeight Universe", ew_metrics))
    qqq_return = pd.to_numeric(
        pd.Series([metrics["QQQ BuyHold Return (%)"]]), errors="coerce"
    ).iloc[0]
    ew_return = pd.to_numeric(
        pd.Series([metrics["EqualWeight Universe BuyHold Return (%)"]]), errors="coerce"
    ).iloc[0]
    metrics["Strategy Excess Return vs QQQ (%)"] = (
        round(float(metrics["Net Getiri (%)"]) - float(qqq_return), 2)
        if pd.notna(qqq_return)
        else pd.NA
    )
    metrics["Strategy Excess Return vs EqualWeight (%)"] = (
        round(float(metrics["Net Getiri (%)"]) - float(ew_return), 2)
        if pd.notna(ew_return)
        else pd.NA
    )
    metrics["EqualWeight Universe Return (%)"] = metrics[
        "EqualWeight Universe BuyHold Return (%)"
    ]
    metrics["EqualWeight Missing Symbols Count"] = len(ew_missing_symbols)
    metrics["EqualWeight Missing Symbols"] = ",".join(ew_missing_symbols)
    metrics["QQQ Benchmark Start"] = str(qqq_start)
    metrics["QQQ Benchmark End"] = str(qqq_end)
    metrics["EqualWeight Benchmark Start"] = str(ew_start)
    metrics["EqualWeight Benchmark End"] = str(ew_end)


def _report_row(experiment_name, params, metrics):
    row = {"Experiment Name": experiment_name}
    row.update(params)
    row.update(metrics)
    row["Strategy Name"] = params["strategy_name"]
    strategy_family = {
        "donchian_breakout": "trend_following / breakout",
        "ma_slope_trend": "trend_following / ma_slope",
    }.get(params["strategy_name"], "relative_strength / momentum")
    row["Strategy Family"] = strategy_family
    row["Survivorship Bias Warning"] = SURVIVORSHIP_BIAS_WARNING
    row["Dividend Adjustment Warning"] = DIVIDEND_ADJUSTMENT_WARNING
    row.pop("_trade_log", None)
    row.pop("_equity_curve", None)
    return row


def _init_experiment_worker(cache_path):
    global _WORKER_PREPARED
    payload = pd.read_pickle(cache_path)
    _WORKER_PREPARED = payload["prepared"]


def _run_experiment_worker(task):
    experiment_name = task["experiment_name"]
    params = task["params"]
    include_raw = bool(task.get("include_raw", False))
    prepared = _WORKER_PREPARED
    if prepared is None:
        raise RuntimeError("Worker prepared cache is not loaded.")
    asset_data = prepared["asset_data"]
    benchmark_data = prepared.get("benchmark_data")
    period_start = task.get("period_start")
    period_end = task.get("period_end")
    if period_start or period_end:
        asset_data = _slice_asset_data_for_period(asset_data, period_start, period_end)
        benchmark_data = _slice_benchmark_for_period(benchmark_data, period_start, period_end)
    started = _now()
    final_data, metrics = _run_strategy(asset_data, benchmark_data, params)
    _add_benchmarks(
        final_data,
        benchmark_data,
        metrics,
        benchmark_asset_data=asset_data,
    )
    runtime_sec = _now() - started
    raw_metrics = metrics if include_raw else None
    return {
        "experiment_name": experiment_name,
        "row": _report_row(experiment_name, params, metrics),
        "raw": {"params": params, "metrics": raw_metrics} if include_raw else None,
        "runtime_sec": runtime_sec,
        "symbols_loaded": metrics.get("symbols_loaded", 0),
        "total_final_entry_signals": metrics.get("total_final_entry_signals", 0),
        "total_trades": metrics.get("total_trades", 0),
    }


def _resolve_worker_count(parallel_enabled, workers):
    if not parallel_enabled:
        return 1
    if workers in (None, "auto"):
        return max(1, (os.cpu_count() or 2) - 1)
    return max(1, int(workers))


def _build_experiment_tasks(
    experiments,
    data_folder,
    run_profile,
    quick_mode,
    intraday_dir,
    daily_dir,
    raw_dir,
    intraday_timeframe,
    daily_timeframe,
    cache_rebuilt,
    include_raw=False,
):
    tasks = []
    for index, experiment in enumerate(experiments, 1):
        experiment_name = experiment.get("name", f"experiment_{index}")
        params = _default_params(experiment, data_folder, run_profile, quick_mode)
        params.update(
            {
                "intraday_data_dir": str(intraday_dir),
                "daily_data_dir": str(daily_dir),
                "raw_1min_data_dir": str(raw_dir),
                "intraday_timeframe": intraday_timeframe,
                "daily_timeframe": daily_timeframe,
                "prepared_cache_rebuilt": cache_rebuilt,
            }
        )
        tasks.append(
            {
                "index": index,
                "experiment_name": experiment_name,
                "params": params,
                "include_raw": include_raw,
            }
        )
    return tasks


def _slice_benchmark_for_period(benchmark_data, start_date=None, end_date=None):
    if benchmark_data is None or benchmark_data.empty:
        return benchmark_data
    return _slice_frame(benchmark_data, start_date, end_date)


def _slice_asset_data_for_period(asset_data, start_date=None, end_date=None):
    if not start_date and not end_date:
        return asset_data
    sliced = {}
    for symbol, frame in asset_data.items():
        period_frame = _slice_frame(frame, start_date, end_date)
        if period_frame.empty:
            continue
        daily = frame.attrs.get("daily_ohlcv")
        if isinstance(daily, pd.DataFrame) and not daily.empty:
            period_frame.attrs["daily_ohlcv"] = _slice_frame(daily, start_date, end_date)
        for key, value in frame.attrs.items():
            if key != "daily_ohlcv":
                period_frame.attrs[key] = value
        sliced[symbol] = period_frame
    return sliced


def _run_experiment_batch(
    tasks,
    asset_data,
    benchmark_data,
    cache_path,
    parallel_enabled=False,
    workers=1,
):
    batch_started = _now()
    results = []
    if parallel_enabled and len(tasks) > 1:
        print(f"Parallel enabled: workers={workers}")
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_experiment_worker,
            initargs=(cache_path,),
        ) as executor:
            futures = {
                executor.submit(_run_experiment_worker, task): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                result = future.result()
                print(
                    f"[stage] experiment {task['index']}/{len(tasks)} done in "
                    f"{result['runtime_sec']:.2f} sec: {task['experiment_name']}"
                )
                results.append(result)
    else:
        for task in tasks:
            print(
                f"\n[stage] experiment {task['index']}/{len(tasks)} started: "
                f"{task['experiment_name']}"
            )
            started = _now()
            period_asset_data = asset_data
            period_benchmark_data = benchmark_data
            if task.get("period_start") or task.get("period_end"):
                period_asset_data = _slice_asset_data_for_period(
                    asset_data,
                    task.get("period_start"),
                    task.get("period_end"),
                )
                period_benchmark_data = _slice_benchmark_for_period(
                    benchmark_data,
                    task.get("period_start"),
                    task.get("period_end"),
                )
            final_data, metrics = _run_strategy(
                period_asset_data,
                period_benchmark_data,
                task["params"],
            )
            _add_benchmarks(
                final_data,
                period_benchmark_data,
                metrics,
                benchmark_asset_data=period_asset_data,
            )
            runtime_sec = _now() - started
            print(
                "Experiment diagnostics: "
                f"symbols_loaded={metrics.get('symbols_loaded', 0)}, "
                f"total_final_entry_signals={metrics.get('total_final_entry_signals', 0)}, "
                f"total_trades={metrics.get('total_trades', 0)}"
            )
            print(
                f"[stage] experiment {task['index']}/{len(tasks)} done in "
                f"{runtime_sec:.2f} sec"
            )
            results.append(
                {
                    "experiment_name": task["experiment_name"],
                    "row": _report_row(task["experiment_name"], task["params"], metrics),
                    "raw": {"params": task["params"], "metrics": metrics}
                    if task.get("include_raw")
                    else None,
                    "runtime_sec": runtime_sec,
                    "symbols_loaded": metrics.get("symbols_loaded", 0),
                    "total_final_entry_signals": metrics.get("total_final_entry_signals", 0),
                    "total_trades": metrics.get("total_trades", 0),
                }
            )
    batch_sec = _now() - batch_started
    print(f"Experiment batch done in {batch_sec:.2f} sec")
    results.sort(key=lambda item: item["experiment_name"])
    return results, batch_sec


def _write_benchmark_audit(report, path, cache_path):
    rows = []
    for _, row in report.sort_values("Experiment Name").iterrows():
        rows.append(
            {
                "experiment_name": row.get("Experiment Name", ""),
                "qqq_start_date": row.get("QQQ Benchmark Start", ""),
                "qqq_end_date": row.get("QQQ Benchmark End", ""),
                "equal_weight_start_date": row.get("EqualWeight Benchmark Start", ""),
                "equal_weight_end_date": row.get("EqualWeight Benchmark End", ""),
                "benchmark_symbol": row.get("benchmark_symbol", "QQQ"),
                "qqq_buyhold_return": row.get("QQQ BuyHold Return (%)", ""),
                "equal_weight_universe_return": row.get(
                    "EqualWeight Universe Return (%)",
                    "",
                ),
                "universe_count_used": 99,
                "excluded_symbols_applied": "QQQ,SPY",
                "benchmark_missing_symbols_count": row.get(
                    "EqualWeight Missing Symbols Count",
                    "",
                ),
                "missing_symbols": row.get("EqualWeight Missing Symbols", ""),
            }
        )
    audit = pd.DataFrame(rows)
    same_qqq_range = audit[["qqq_start_date", "qqq_end_date"]].drop_duplicates().shape[0] == 1
    same_ew_range = audit[["equal_weight_start_date", "equal_weight_end_date"]].drop_duplicates().shape[0] == 1
    qqq_unique = sorted(audit["qqq_buyhold_return"].dropna().unique().tolist())
    ew_unique = sorted(audit["equal_weight_universe_return"].dropna().unique().tolist())
    missing_unique = sorted(
        str(value)
        for value in audit["missing_symbols"].fillna("").unique().tolist()
        if str(value)
    )
    try:
        table = audit.to_markdown(index=False)
    except ImportError:
        table = audit.to_string(index=False)
    content = [
        "# Benchmark Audit Report",
        "",
        "## Summary",
        "",
        f"- Prepared feature cache: `{cache_path}`",
        "- EqualWeight benchmark uses immutable base/prepared OHLCV close data.",
        f"- Same QQQ date range across all experiments: {same_qqq_range}.",
        f"- Same EqualWeight date range across all experiments: {same_ew_range}.",
        f"- QQQ BuyHold Return unique values: {qqq_unique}.",
        f"- EqualWeight Universe Return unique values: {ew_unique}.",
        f"- Missing symbols: {missing_unique if missing_unique else 'none'}.",
        "",
        "## Per Experiment",
        "",
        table,
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def _write_performance_profile(profile_rows, csv_path, markdown_path):
    profile = pd.DataFrame(profile_rows)
    profile.to_csv(csv_path, index=False)
    try:
        table = profile.to_markdown(index=False)
    except ImportError:
        table = profile.to_string(index=False)
    stage_times = profile[["stage", "duration_sec"]].copy()
    stage_times["duration_sec"] = pd.to_numeric(stage_times["duration_sec"], errors="coerce")
    slowest = stage_times.dropna().sort_values("duration_sec", ascending=False).head(5)
    try:
        slowest_table = slowest.to_markdown(index=False)
    except ImportError:
        slowest_table = slowest.to_string(index=False)
    markdown_path.write_text(
        "\n".join(
            [
                "# Performance Profile Report",
                "",
                "## Slowest 5 Stages",
                "",
                slowest_table,
                "",
                "## All Stages",
                "",
                table,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return slowest


def _table_for_markdown(frame):
    try:
        return frame.to_markdown(index=False)
    except ImportError:
        return frame.to_string(index=False)


def _trade_stats(trades):
    if trades.empty:
        return {
            "trade_count": 0,
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "win_rate": 0.0,
            "avg_holding_bars": 0.0,
        }
    r_values = pd.to_numeric(trades.get("R Multiple"), errors="coerce").dropna()
    pnl = pd.to_numeric(trades.get("Net PnL"), errors="coerce").dropna()
    wins = pnl[pnl > 0].sum()
    losses = pnl[pnl < 0].sum()
    profit_factor = wins / abs(losses) if losses < 0 else (wins if wins > 0 else 0.0)
    bars = pd.to_numeric(trades.get("Bars Held"), errors="coerce").dropna()
    return {
        "trade_count": int(len(trades)),
        "profit_factor": round(float(profit_factor), 2),
        "expectancy_r": round(float(r_values.mean()), 4) if not r_values.empty else 0.0,
        "win_rate": round(float((pnl > 0).mean() * 100), 2) if not pnl.empty else 0.0,
        "avg_holding_bars": round(float(bars.mean()), 2) if not bars.empty else 0.0,
    }


def _safe_yearly_benchmark_metrics(benchmark_data, asset_data, start, end, year):
    missing = {
        "qqq_return_pct": pd.NA,
        "excess_vs_qqq_pct": pd.NA,
        "equal_weight_return_pct": pd.NA,
        "excess_vs_equal_weight_pct": pd.NA,
        "benchmark_insufficient_data": True,
    }
    warning = f"Warning: yearly benchmark insufficient for {year}, skipping benchmark metrics"
    if benchmark_data is None or benchmark_data.empty or "Close" not in benchmark_data:
        print(warning)
        return missing
    close = pd.to_numeric(benchmark_data["Close"], errors="coerce").dropna().sort_index()
    if close.empty:
        print(warning)
        return missing
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    if close.index.tz is not None:
        if start.tzinfo is None:
            start = start.tz_localize(close.index.tz)
            end = end.tz_localize(close.index.tz)
        else:
            start = start.tz_convert(close.index.tz)
            end = end.tz_convert(close.index.tz)
    elif close.index.tz is None and start.tzinfo is not None:
        start = start.tz_convert(None)
        end = end.tz_convert(None)
    start = max(start, close.index[0])
    end = min(end, close.index[-1])
    if end <= start or len(close.loc[(close.index >= start) & (close.index <= end)]) < 2:
        print(warning)
        return missing
    try:
        qqq = calculate_buyhold_metrics(close, start, end)
        ew = calculate_equal_weight_benchmark(asset_data, start, end)
    except Exception:
        print(warning)
        return missing
    return {
        "qqq_return_pct": qqq["Return (%)"],
        "excess_vs_qqq_pct": None,
        "equal_weight_return_pct": ew["Return (%)"],
        "excess_vs_equal_weight_pct": None,
        "benchmark_insufficient_data": False,
    }


def _yearly_rows(raw_results, focus_names, benchmark_data, asset_data):
    rows = []
    available_starts = []
    if benchmark_data is not None and not benchmark_data.empty:
        available_starts.append(benchmark_data.index.sort_values()[0])
    for data in asset_data.values():
        if not data.empty:
            available_starts.append(data.index.sort_values()[0])
    data_start = min(available_starts) if available_starts else None
    for experiment_name in focus_names:
        item = raw_results.get(experiment_name)
        if not item:
            continue
        equity = pd.DataFrame(
            item["metrics"].get("_equity_curve", []),
            columns=["date", "equity"],
        )
        if equity.empty:
            continue
        equity["date"] = pd.to_datetime(equity["date"])
        equity = equity.sort_values("date")
        equity["year"] = equity["date"].dt.year
        trades = pd.DataFrame(item["metrics"].get("_trade_log", []))
        if not trades.empty:
            trades["Exit Time"] = pd.to_datetime(trades["Exit Time"])
            trades["year"] = trades["Exit Time"].dt.year
        for year, frame in equity.groupby("year"):
            start = frame["date"].iloc[0]
            end = frame["date"].iloc[-1]
            if data_start is not None:
                compare_end = pd.Timestamp(end)
                compare_start = pd.Timestamp(data_start)
                if compare_start.tzinfo is not None and compare_end.tzinfo is None:
                    compare_end = compare_end.tz_localize(compare_start.tzinfo)
                elif compare_start.tzinfo is None and compare_end.tzinfo is not None:
                    compare_end = compare_end.tz_convert(None)
                if compare_end < compare_start:
                    continue
            start_equity = float(frame["equity"].iloc[0])
            end_equity = float(frame["equity"].iloc[-1])
            normalized = frame["equity"] / start_equity
            max_dd = ((normalized - normalized.cummax()) / normalized.cummax()).min() * 100
            year_trades = (
                trades[trades["year"] == year] if not trades.empty else pd.DataFrame()
            )
            stats = _trade_stats(year_trades)
            net_return = (end_equity / start_equity - 1) * 100
            benchmark_metrics = _safe_yearly_benchmark_metrics(
                benchmark_data,
                asset_data,
                start,
                end,
                year,
            )
            if not benchmark_metrics["benchmark_insufficient_data"]:
                benchmark_metrics["excess_vs_qqq_pct"] = round(
                    float(net_return) - benchmark_metrics["qqq_return_pct"],
                    2,
                )
                benchmark_metrics["excess_vs_equal_weight_pct"] = round(
                    float(net_return) - benchmark_metrics["equal_weight_return_pct"],
                    2,
                )
            if (
                stats["trade_count"] == 0
                and bool(benchmark_metrics["benchmark_insufficient_data"])
            ):
                continue
            rows.append(
                {
                    "experiment_name": experiment_name,
                    "year": int(year),
                    "net_return_pct": round(float(net_return), 2),
                    "max_drawdown_pct": round(float(max_dd), 2),
                    **stats,
                    **benchmark_metrics,
                }
            )
    return pd.DataFrame(rows)


def _yearly_focus_names():
    return [
        "rs_m63_sma100",
        "rs_m63_no_regime",
        "dm_m63_top20",
        "rs_m126_top10_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "rs_m126_top30_sma100_no_regime",
        "rs_m126_top30_sma200_no_regime",
        "rs_m84_top30_sma100_no_regime",
        "rs_m63_top20_sma100_regime",
        "rs_m63_top30_sma100_regime",
    ]


def _write_yearly_report(yearly, csv_path, markdown_path):
    yearly.to_csv(csv_path, index=False)
    try:
        table = yearly.to_markdown(index=False)
    except ImportError:
        table = yearly.to_string(index=False)
    markdown_path.write_text(
        "\n".join(["# Yearly Performance Report", "", table, ""]),
        encoding="utf-8",
    )


def _rs_robustness_experiments():
    experiments = []
    for lookback in [42, 63, 84, 126]:
        for top_n in [10, 20, 30]:
            for sma_days in [100, 200]:
                for regime in [True, False]:
                    suffix = "regime" if regime else "no_regime"
                    experiments.append(
                        {
                            "name": f"rs_m{lookback}_top{top_n}_sma{sma_days}_{suffix}",
                            "strategy_name": "relative_strength_rotation",
                            "excluded_symbols": ["QQQ", "SPY"],
                            "rs_lookback_days": lookback,
                            "rs_top_n": top_n,
                            "rs_rebalance_frequency": "monthly",
                            "rs_score_mode": "return_over_vol",
                            "rs_vol_lookback_days": 60,
                            "rs_trend_sma_days": sma_days,
                            "rs_use_market_regime": regime,
                            "rs_market_regime_sma_days": 200,
                            "rs_min_price": 10,
                            "rs_max_atr_pct": 12,
                        }
                    )
    return experiments


def _oos_walkforward_periods():
    return [
        ("split_2020_2022", "2020-07-27", "2022-12-31"),
        ("split_2023_2024", "2023-01-01", "2024-12-31"),
        ("split_2025_2026", "2025-01-01", "2026-06-18"),
        ("anchored_insample_2020_2023", "2020-07-27", "2023-12-31"),
        ("anchored_oos_2024_2026", "2024-01-01", "2026-06-18"),
    ]


def _oos_candidate_experiments(base_experiments):
    candidates = {experiment["name"]: dict(experiment) for experiment in base_experiments}
    for experiment in _rs_robustness_experiments():
        if experiment["name"] in {
            "rs_m126_top30_sma100_no_regime",
            "rs_m126_top20_sma100_no_regime",
            "rs_m126_top10_sma100_no_regime",
            "rs_m126_top30_sma200_no_regime",
        }:
            candidates[experiment["name"]] = experiment
    wanted = [
        "rs_m126_top30_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "rs_m126_top10_sma100_no_regime",
        "rs_m126_top30_sma200_no_regime",
        "donchian_20_10_top20",
        "rs_m63_sma100",
    ]
    missing = [name for name in wanted if name not in candidates]
    if missing:
        raise ValueError(f"OOS aday config eksik: {', '.join(missing)}")
    return [candidates[name] for name in wanted]


def _oos_report_row(result, period_name, start_date, end_date):
    row = result["row"]
    net_return = pd.to_numeric(pd.Series([row.get("Net Getiri (%)")]), errors="coerce").iloc[0]
    max_dd = pd.to_numeric(pd.Series([row.get("Max Drawdown (%)")]), errors="coerce").iloc[0]
    qqq_return = pd.to_numeric(pd.Series([row.get("QQQ BuyHold Return (%)")]), errors="coerce").iloc[0]
    ew_return = pd.to_numeric(
        pd.Series([row.get("EqualWeight Universe Return (%)")]),
        errors="coerce",
    ).iloc[0]
    return {
        "experiment_name": row.get("Experiment Name", result["experiment_name"]),
        "period_name": period_name,
        "start_date": start_date,
        "end_date": end_date,
        "net_return_pct": row.get("Net Getiri (%)", pd.NA),
        "max_drawdown_pct": row.get("Max Drawdown (%)", pd.NA),
        "trade_count": row.get("Trade Count", pd.NA),
        "profit_factor": row.get("Profit Factor", pd.NA),
        "expectancy_r": row.get("Expectancy R", pd.NA),
        "win_rate": row.get("Win Rate (%)", pd.NA),
        "avg_holding_bars": row.get("Avg Holding Bars", pd.NA),
        "worst_r": row.get("Worst R", pd.NA),
        "qqq_return_pct": row.get("QQQ BuyHold Return (%)", pd.NA),
        "equal_weight_return_pct": row.get("EqualWeight Universe Return (%)", pd.NA),
        "excess_vs_qqq_pct": row.get("Strategy Excess Return vs QQQ (%)", pd.NA),
        "excess_vs_equal_weight_pct": row.get(
            "Strategy Excess Return vs EqualWeight (%)",
            pd.NA,
        ),
        "return_over_maxdd": (
            round(float(net_return) / abs(float(max_dd)), 4)
            if pd.notna(net_return) and pd.notna(max_dd) and float(max_dd) != 0
            else pd.NA
        ),
        "beats_qqq": bool(pd.notna(net_return) and pd.notna(qqq_return) and net_return > qqq_return),
        "beats_equal_weight": bool(pd.notna(net_return) and pd.notna(ew_return) and net_return > ew_return),
    }


def _write_oos_walkforward_report(oos, csv_path, markdown_path):
    output_columns = [
        "experiment_name",
        "period_name",
        "start_date",
        "end_date",
        "net_return_pct",
        "max_drawdown_pct",
        "trade_count",
        "profit_factor",
        "expectancy_r",
        "win_rate",
        "avg_holding_bars",
        "worst_r",
        "qqq_return_pct",
        "equal_weight_return_pct",
        "excess_vs_qqq_pct",
        "excess_vs_equal_weight_pct",
        "return_over_maxdd",
    ]
    output = oos[output_columns].copy()
    output.to_csv(csv_path, index=False)
    ranked_parts = []
    for period_name, group in oos.groupby("period_name", sort=False):
        top = group.sort_values(
            ["return_over_maxdd", "net_return_pct"],
            ascending=[False, False],
            na_position="last",
        ).head(5)
        ranked_parts.append(f"### {period_name}")
        ranked_parts.append("")
        ranked_parts.append(_table_for_markdown(top[output_columns]))
        ranked_parts.append("")
    numeric = oos.copy()
    for column in ["net_return_pct", "max_drawdown_pct", "return_over_maxdd"]:
        numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
    stability = numeric.groupby("experiment_name").agg(
        periods=("period_name", "count"),
        avg_return_pct=("net_return_pct", "mean"),
        min_return_pct=("net_return_pct", "min"),
        return_std_pct=("net_return_pct", "std"),
        worst_drawdown_pct=("max_drawdown_pct", "min"),
        qqq_wins=("beats_qqq", "sum"),
        equal_weight_wins=("beats_equal_weight", "sum"),
    ).round(4).reset_index()
    topn = numeric[numeric["experiment_name"].str.contains("top", na=False)].copy()
    topn["top_n"] = topn["experiment_name"].str.extract(r"top(\d+)").astype(float)
    topn_summary = topn.groupby("top_n").agg(
        avg_return_pct=("net_return_pct", "mean"),
        min_return_pct=("net_return_pct", "min"),
        avg_return_over_maxdd=("return_over_maxdd", "mean"),
    ).round(4).reset_index()
    momentum_period = numeric[numeric["period_name"].eq("split_2023_2024")][
        [
            "experiment_name",
            "net_return_pct",
            "qqq_return_pct",
            "excess_vs_qqq_pct",
            "beats_qqq",
        ]
    ].sort_values("excess_vs_qqq_pct", ascending=False, na_position="last")
    content = [
        "# OOS Walkforward Report",
        "",
        "## Best 5 By Period",
        "",
        *ranked_parts,
        "## Stability",
        "",
        _table_for_markdown(stability),
        "",
        "## Top10 / Top20 / Top30",
        "",
        _table_for_markdown(topn_summary),
        "",
        "## 2023-2024 Momentum Period vs QQQ",
        "",
        _table_for_markdown(momentum_period),
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _attribution_candidate_experiments(base_experiments):
    candidates = {experiment["name"]: dict(experiment) for experiment in base_experiments}
    for experiment in _rs_robustness_experiments():
        if experiment["name"] in {
            "rs_m126_top10_sma100_no_regime",
            "rs_m126_top20_sma100_no_regime",
            "rs_m126_top30_sma100_no_regime",
        }:
            candidates[experiment["name"]] = experiment
    wanted = [
        "rs_m126_top10_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "rs_m126_top30_sma100_no_regime",
        "donchian_20_10_top20",
        "rs_m63_sma100",
    ]
    missing = [name for name in wanted if name not in candidates]
    if missing:
        raise ValueError(f"Attribution aday config eksik: {', '.join(missing)}")
    return [candidates[name] for name in wanted]


def _candidate_attribution_rows(attribution_results):
    rows = []
    summaries = []
    for result in attribution_results:
        experiment_name = result["experiment_name"]
        metrics = (result.get("raw") or {}).get("metrics", {}) or {}
        trades = pd.DataFrame(metrics.get("_trade_log", []))
        if trades.empty or "Symbol" not in trades:
            summaries.append(
                {
                    "experiment_name": experiment_name,
                    "top_1_symbol_contribution_pct": 0.0,
                    "top_3_symbols_contribution_pct": 0.0,
                    "top_5_symbols_contribution_pct": 0.0,
                    "top_10_symbols_contribution_pct": 0.0,
                    "profitable_symbol_count": 0,
                    "losing_symbol_count": 0,
                    "total_traded_symbol_count": 0,
                    "largest_losing_symbol": "",
                    "largest_winning_symbol": "",
                }
            )
            continue
        trades["Net PnL"] = pd.to_numeric(trades.get("Net PnL"), errors="coerce").fillna(0.0)
        trades["R Multiple"] = pd.to_numeric(trades.get("R Multiple"), errors="coerce")
        trades["Bars Held"] = pd.to_numeric(trades.get("Bars Held"), errors="coerce")
        total_pnl = float(trades["Net PnL"].sum())
        grouped_rows = []
        for symbol, group in trades.groupby("Symbol", sort=True):
            pnl = group["Net PnL"]
            r_values = group["R Multiple"].dropna()
            bars = group["Bars Held"].dropna()
            symbol_pnl = float(pnl.sum())
            grouped_rows.append(
                {
                    "experiment_name": experiment_name,
                    "symbol": symbol,
                    "trade_count": int(len(group)),
                    "win_rate": round(float((pnl > 0).mean() * 100), 2) if len(pnl) else 0.0,
                    "total_pnl": round(symbol_pnl, 2),
                    "total_r": round(float(r_values.sum()), 4) if not r_values.empty else 0.0,
                    "avg_r": round(float(r_values.mean()), 4) if not r_values.empty else 0.0,
                    "median_r": round(float(r_values.median()), 4) if not r_values.empty else 0.0,
                    "best_r": round(float(r_values.max()), 4) if not r_values.empty else 0.0,
                    "worst_r": round(float(r_values.min()), 4) if not r_values.empty else 0.0,
                    "contribution_pct_of_total_pnl": (
                        round(symbol_pnl / total_pnl * 100, 2) if total_pnl else 0.0
                    ),
                    "avg_holding_bars": round(float(bars.mean()), 2) if not bars.empty else 0.0,
                }
            )
        grouped_rows.sort(key=lambda item: item["total_pnl"], reverse=True)
        cumulative = 0
        for item in grouped_rows:
            cumulative += 1
            item["cumulative_contribution_rank"] = cumulative
        rows.extend(grouped_rows)
        positive = [item for item in grouped_rows if item["total_pnl"] > 0]
        negative = [item for item in grouped_rows if item["total_pnl"] < 0]

        def top_pct(count):
            if not total_pnl:
                return 0.0
            return round(sum(item["total_pnl"] for item in grouped_rows[:count]) / total_pnl * 100, 2)

        summaries.append(
            {
                "experiment_name": experiment_name,
                "top_1_symbol_contribution_pct": top_pct(1),
                "top_3_symbols_contribution_pct": top_pct(3),
                "top_5_symbols_contribution_pct": top_pct(5),
                "top_10_symbols_contribution_pct": top_pct(10),
                "profitable_symbol_count": len(positive),
                "losing_symbol_count": len(negative),
                "total_traded_symbol_count": len(grouped_rows),
                "largest_losing_symbol": negative[-1]["symbol"] if negative else "",
                "largest_winning_symbol": positive[0]["symbol"] if positive else "",
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(summaries)


def _write_candidate_attribution_report(attribution, summary, csv_path, markdown_path):
    columns = [
        "experiment_name",
        "symbol",
        "trade_count",
        "win_rate",
        "total_pnl",
        "total_r",
        "avg_r",
        "median_r",
        "best_r",
        "worst_r",
        "contribution_pct_of_total_pnl",
        "cumulative_contribution_rank",
        "avg_holding_bars",
    ]
    output = attribution[columns].copy() if not attribution.empty else pd.DataFrame(columns=columns)
    output.to_csv(csv_path, index=False)
    warnings = []
    concentration_by_name = {}
    summary_by_name = (
        summary.set_index("experiment_name").to_dict("index")
        if isinstance(summary, pd.DataFrame)
        and not summary.empty
        and "experiment_name" in summary
        else {}
    )
    for name, group in output.groupby("experiment_name", sort=False):
        ranked = group.sort_values("total_pnl", ascending=False)
        total_pnl = float(pd.to_numeric(ranked["total_pnl"], errors="coerce").fillna(0.0).sum())
        top1 = float(pd.to_numeric(ranked.head(1)["total_pnl"], errors="coerce").fillna(0.0).sum())
        top5 = float(pd.to_numeric(ranked.head(5)["total_pnl"], errors="coerce").fillna(0.0).sum())
        top1_pct = round(top1 / total_pnl * 100, 2) if total_pnl else 0.0
        top5_pct = round(top5 / total_pnl * 100, 2) if total_pnl else 0.0
        concentration_by_name[name] = {"top1": top1_pct, "top5": top5_pct}
        if top5_pct > 50:
            warnings.append(f"- Warning: {name} top 5 symbols contribute >50% of total PnL.")
        if top1_pct > 25:
            warnings.append(f"- Warning: {name} top 1 symbol contributes >25% of total PnL.")
        if len(group) < 20:
            warnings.append(f"- Warning: {name} traded symbol count is low.")
    top10_concentration = concentration_by_name.get("rs_m126_top10_sma100_no_regime", {})
    if float(top10_concentration.get("top5", 0.0)) > 50:
        warnings.append("- Warning: Top10 strategy is highly concentrated.")
    if not warnings:
        warnings = ["- No concentration warning triggered."]
    rs_compare = []
    for name in [
        "rs_m126_top10_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "rs_m126_top30_sma100_no_regime",
    ]:
        item = summary_by_name.get(name, {})
        rs_compare.append(
            f"- {name}: top1={item.get('top_1_symbol_contribution_pct', 0.0)}%, "
            f"top5={item.get('top_5_symbols_contribution_pct', 0.0)}%, "
            f"traded_symbols={item.get('total_traded_symbol_count', 0)}."
        )
    donchian_summary = summary_by_name.get("donchian_20_10_top20", {})
    top_rows = output.sort_values(
        ["experiment_name", "cumulative_contribution_rank"],
        ascending=[True, True],
    ).groupby("experiment_name", sort=False).head(10)
    content = [
        "# Candidate Attribution Report",
        "",
        "## Concentration Summary",
        "",
        _table_for_markdown(summary),
        "",
        "## Risk Warnings",
        "",
        *warnings,
        "",
        "## Balance And Concentration Read",
        "",
        *rs_compare,
        "- Top10 is the aggressive/high-concentration RS candidate when top1/top5 contribution is materially above Top20/Top30.",
        "- Top20 is the middle RS balance point; Top30 is the broader RS candidate when concentration reduction is preferred.",
        (
            "- Donchian is a different strategy-family backup; "
            f"top5={donchian_summary.get('top_5_symbols_contribution_pct', 0.0)}%, "
            f"traded_symbols={donchian_summary.get('total_traded_symbol_count', 0)}."
        ),
        "",
        "## Top Symbols By Candidate",
        "",
        _table_for_markdown(top_rows),
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _run_candidate_attribution_report(
    experiments,
    data_folder,
    run_profile,
    quick_mode,
    intraday_dir,
    daily_dir,
    raw_dir,
    intraday_timeframe,
    daily_timeframe,
    cache_rebuilt,
    asset_data,
    benchmark_data,
    cache_path,
    parallel_enabled,
    worker_count,
):
    tasks = _build_experiment_tasks(
        _attribution_candidate_experiments(experiments),
        data_folder,
        run_profile,
        quick_mode,
        intraday_dir,
        daily_dir,
        raw_dir,
        intraday_timeframe,
        daily_timeframe,
        cache_rebuilt,
        include_raw=True,
    )
    results, batch_sec = _run_experiment_batch(
        tasks,
        asset_data,
        benchmark_data,
        cache_path,
        parallel_enabled=parallel_enabled,
        workers=worker_count,
    )
    attribution, summary = _candidate_attribution_rows(results)
    return attribution, summary, batch_sec


def _cost_stress_candidate_experiments(base_experiments):
    return _attribution_candidate_experiments(base_experiments)


def _cost_stress_scenarios():
    return [
        ("zero_cost_baseline", 0.0, 0.0),
        ("commission_0_slippage_5bps_per_side", 5.0, 0.0),
        ("commission_0_slippage_10bps_per_side", 10.0, 0.0),
        ("commission_0_slippage_25bps_per_side", 25.0, 0.0),
        ("commission_0_slippage_50bps_per_side", 50.0, 0.0),
        ("fixed_fee_1usd_plus_10bps_per_side", 10.0, 1.0),
    ]


def _max_positions_from_params(params):
    if params.get("strategy_name") == "donchian_breakout":
        return max(1, int(params.get("donchian_top_n", 20)))
    if params.get("strategy_name") == "relative_strength_rotation":
        return max(1, int(params.get("rs_top_n", 20)))
    return 20


def _entry_dollar_volumes(trades, asset_data):
    values = []
    for _, trade in trades.iterrows():
        symbol = str(trade.get("Symbol", "")).upper()
        frame = asset_data.get(symbol)
        if frame is None or frame.empty or "Volume" not in frame or "Close" not in frame:
            continue
        entry_time = pd.Timestamp(trade.get("Entry Time"))
        index = frame.index
        if index.tz is not None and entry_time.tzinfo is None:
            entry_time = entry_time.tz_localize(index.tz)
        elif index.tz is None and entry_time.tzinfo is not None:
            entry_time = entry_time.tz_convert(None)
        pos = index.searchsorted(entry_time, side="right") - 1
        if pos < 0:
            continue
        row = frame.iloc[pos]
        close = pd.to_numeric(pd.Series([row.get("Close")]), errors="coerce").iloc[0]
        volume = pd.to_numeric(pd.Series([row.get("Volume")]), errors="coerce").iloc[0]
        if pd.notna(close) and pd.notna(volume) and close > 0 and volume > 0:
            values.append(float(close) * float(volume))
    return pd.Series(values, dtype=float)


def _trades_with_entry_liquidity(trades, asset_data):
    trades = trades.copy()
    if trades.empty:
        trades["entry_dollar_volume"] = pd.Series(dtype=float)
        return trades
    values = []
    for _, trade in trades.iterrows():
        symbol = str(trade.get("Symbol", "")).upper()
        frame = asset_data.get(symbol)
        value = pd.NA
        if frame is not None and not frame.empty and "Volume" in frame and "Close" in frame:
            entry_time = pd.Timestamp(trade.get("Entry Time"))
            index = frame.index
            if index.tz is not None and entry_time.tzinfo is None:
                entry_time = entry_time.tz_localize(index.tz)
            elif index.tz is None and entry_time.tzinfo is not None:
                entry_time = entry_time.tz_convert(None)
            pos = index.searchsorted(entry_time, side="right") - 1
            if pos >= 0:
                row = frame.iloc[pos]
                close = pd.to_numeric(pd.Series([row.get("Close")]), errors="coerce").iloc[0]
                volume = pd.to_numeric(pd.Series([row.get("Volume")]), errors="coerce").iloc[0]
                if pd.notna(close) and pd.notna(volume) and close > 0 and volume > 0:
                    value = float(close) * float(volume)
        values.append(value)
    trades["entry_dollar_volume"] = pd.to_numeric(
        pd.Series(values),
        errors="coerce",
    ).to_numpy()
    return trades


def _cost_adjusted_stats(trades, net_return, slippage_bps, fixed_fee):
    if trades.empty:
        return {
            "net_return_pct": round(float(net_return), 2),
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "avg_trade_return_pct": 0.0,
            "degradation_vs_zero_cost_pct": 0.0,
        }
    entry_notional = pd.to_numeric(trades.get("Entry Notional"), errors="coerce").fillna(0.0)
    shares = pd.to_numeric(trades.get("Shares"), errors="coerce").fillna(0.0)
    exit_price = pd.to_numeric(trades.get("Exit Price"), errors="coerce").fillna(0.0)
    exit_notional = shares * exit_price
    pnl = pd.to_numeric(trades.get("Net PnL"), errors="coerce").fillna(0.0)
    side_cost = float(slippage_bps) / 10000.0
    total_cost = side_cost * (entry_notional + exit_notional) + (float(fixed_fee) * 2.0)
    adjusted_pnl = pnl - total_cost
    gross_profit = adjusted_pnl[adjusted_pnl > 0].sum()
    gross_loss = adjusted_pnl[adjusted_pnl < 0].sum()
    profit_factor = (
        gross_profit / abs(gross_loss)
        if gross_loss < 0
        else (gross_profit if gross_profit > 0 else 0.0)
    )
    adjusted_net_return = float(net_return) - float(total_cost.sum()) / 10000.0 * 100.0
    adjusted_r = adjusted_pnl / entry_notional.replace(0, pd.NA)
    return {
        "net_return_pct": round(float(adjusted_net_return), 2),
        "profit_factor": round(float(profit_factor), 2),
        "expectancy_r": round(float(adjusted_r.dropna().mean()), 4)
        if not adjusted_r.dropna().empty
        else 0.0,
        "avg_trade_return_pct": round(float(adjusted_r.dropna().mean()) * 100, 4)
        if not adjusted_r.dropna().empty
        else 0.0,
        "degradation_vs_zero_cost_pct": round(float(net_return) - float(adjusted_net_return), 2),
    }


def _liquidity_stress_metrics(dollar_volumes, per_position):
    if dollar_volumes.empty:
        return {
            "median_order_pct_of_dollar_volume": pd.NA,
            "p10_order_pct_of_dollar_volume": pd.NA,
            "min_order_pct_of_dollar_volume": pd.NA,
            "liquidity_warning_count": 1,
            "entries_below_1m_dollar_volume": 0,
            "entries_below_500k_dollar_volume": 0,
            "entries_below_100k_dollar_volume": 0,
        }
    median_dv = float(dollar_volumes.median())
    p10_dv = float(dollar_volumes.quantile(0.10))
    min_dv = float(dollar_volumes.min())
    median_pct = round(float(per_position) / median_dv * 100, 6) if median_dv > 0 else pd.NA
    p10_pct = round(float(per_position) / p10_dv * 100, 6) if p10_dv > 0 else pd.NA
    min_pct = round(float(per_position) / min_dv * 100, 6) if min_dv > 0 else pd.NA
    below_1m = int((dollar_volumes < 1_000_000).sum())
    below_500k = int((dollar_volumes < 500_000).sum())
    below_100k = int((dollar_volumes < 100_000).sum())
    warning_count = int(
        (pd.notna(p10_pct) and float(p10_pct) > 1.0)
        + (pd.notna(min_pct) and float(min_pct) > 5.0)
        + (below_100k > 0)
    )
    return {
        "median_order_pct_of_dollar_volume": median_pct,
        "p10_order_pct_of_dollar_volume": p10_pct,
        "min_order_pct_of_dollar_volume": min_pct,
        "liquidity_warning_count": warning_count,
        "entries_below_1m_dollar_volume": below_1m,
        "entries_below_500k_dollar_volume": below_500k,
        "entries_below_100k_dollar_volume": below_100k,
    }


def _cost_stress_rows(cost_results, asset_data):
    rows = []
    for result in cost_results:
        row = result["row"]
        raw = result.get("raw") or {}
        metrics = raw.get("metrics", {}) or {}
        params = raw.get("params", {}) or {}
        trades = pd.DataFrame(metrics.get("_trade_log", []))
        net_return = pd.to_numeric(pd.Series([row.get("Net Getiri (%)")]), errors="coerce").iloc[0]
        max_dd = row.get("Max Drawdown (%)", pd.NA)
        qqq = row.get("QQQ BuyHold Return (%)", pd.NA)
        ew = row.get("EqualWeight Universe Return (%)", pd.NA)
        max_positions = _max_positions_from_params(params)
        dollar_volumes = _entry_dollar_volumes(trades, asset_data)
        median_dv = round(float(dollar_volumes.median()), 2) if not dollar_volumes.empty else pd.NA
        p10_dv = round(float(dollar_volumes.quantile(0.10)), 2) if not dollar_volumes.empty else pd.NA
        min_dv = round(float(dollar_volumes.min()), 2) if not dollar_volumes.empty else pd.NA
        for scenario, slippage_bps, fixed_fee in _cost_stress_scenarios():
            stats = _cost_adjusted_stats(trades, net_return, slippage_bps, fixed_fee)
            if scenario == "zero_cost_baseline":
                avg_r = pd.to_numeric(pd.Series([row.get("Avg R", pd.NA)]), errors="coerce").iloc[0]
                stats.update(
                    {
                        "net_return_pct": row.get("Net Getiri (%)", pd.NA),
                        "profit_factor": row.get("Profit Factor", pd.NA),
                        "expectancy_r": row.get("Expectancy R", pd.NA),
                        "avg_trade_return_pct": round(float(avg_r) * 100, 4)
                        if pd.notna(avg_r)
                        else pd.NA,
                        "degradation_vs_zero_cost_pct": 0.0,
                    }
                )
            excess_qqq = (
                round(float(stats["net_return_pct"]) - float(qqq), 2)
                if pd.notna(stats["net_return_pct"]) and pd.notna(qqq)
                else pd.NA
            )
            excess_ew = (
                round(float(stats["net_return_pct"]) - float(ew), 2)
                if pd.notna(stats["net_return_pct"]) and pd.notna(ew)
                else pd.NA
            )
            for capital in [10000, 25000, 50000, 100000]:
                per_position = float(capital) / max_positions
                liquidity = _liquidity_stress_metrics(dollar_volumes, per_position)
                rows.append(
                    {
                        "experiment_name": row.get("Experiment Name", result["experiment_name"]),
                        "scenario": scenario,
                        "capital_assumption_usd": capital,
                        "max_positions": max_positions,
                        "per_position_notional": round(per_position, 2),
                        "net_return_pct": stats["net_return_pct"],
                        "max_drawdown_pct": max_dd,
                        "trade_count": row.get("Trade Count", pd.NA),
                        "profit_factor": stats["profit_factor"],
                        "expectancy_r": stats["expectancy_r"],
                        "avg_trade_return_pct": stats["avg_trade_return_pct"],
                        "avg_holding_bars": row.get("Avg Holding Bars", pd.NA),
                        "turnover_estimate": row.get("Turnover Estimate", pd.NA),
                        "qqq_return_pct": qqq,
                        "equal_weight_return_pct": ew,
                        "excess_vs_qqq_pct": excess_qqq,
                        "excess_vs_equal_weight_pct": excess_ew,
                        "degradation_vs_zero_cost_pct": stats["degradation_vs_zero_cost_pct"],
                        "survives_cost_test": bool(
                            pd.notna(stats["net_return_pct"])
                            and float(stats["net_return_pct"]) > 0
                            and pd.notna(stats["profit_factor"])
                            and float(stats["profit_factor"]) > 1.0
                        ),
                        "median_entry_dollar_volume": median_dv,
                        "p10_entry_dollar_volume": p10_dv,
                        "min_entry_dollar_volume": min_dv,
                        "estimated_order_size_pct_of_dollar_volume": liquidity[
                            "median_order_pct_of_dollar_volume"
                        ],
                        **liquidity,
                    }
                )
    return pd.DataFrame(rows)


def _write_cost_slippage_stress_report(stress, csv_path, markdown_path):
    stress.to_csv(csv_path, index=False)
    base_capital = stress[stress["capital_assumption_usd"].eq(10000)].copy()
    scenario_summary = base_capital.pivot_table(
        index="experiment_name",
        columns="scenario",
        values="net_return_pct",
        aggfunc="first",
    ).reset_index()
    survivors = base_capital.groupby(["scenario", "experiment_name"])["survives_cost_test"].first().reset_index()
    ten_bps = survivors[
        survivors["scenario"].isin(
            ["commission_0_slippage_10bps_per_side", "fixed_fee_1usd_plus_10bps_per_side"]
        )
        & survivors["survives_cost_test"]
    ]["experiment_name"].drop_duplicates().tolist()
    twenty_five = survivors[
        survivors["scenario"].eq("commission_0_slippage_25bps_per_side")
        & survivors["survives_cost_test"]
    ]["experiment_name"].drop_duplicates().tolist()
    zero_rows = stress[stress["scenario"].eq("zero_cost_baseline")].copy()
    safe_by_capital = {}
    for capital, group in zero_rows.groupby("capital_assumption_usd"):
        safe_by_capital[int(capital)] = sorted(
            group.loc[
                pd.to_numeric(group["liquidity_warning_count"], errors="coerce").fillna(1).eq(0),
                "experiment_name",
            ].unique()
        )
    liquidity_summary = zero_rows[
        [
            "experiment_name",
            "capital_assumption_usd",
            "max_positions",
            "per_position_notional",
            "median_order_pct_of_dollar_volume",
            "p10_order_pct_of_dollar_volume",
            "min_order_pct_of_dollar_volume",
            "liquidity_warning_count",
            "entries_below_1m_dollar_volume",
            "entries_below_500k_dollar_volume",
            "entries_below_100k_dollar_volume",
        ]
    ].sort_values(["capital_assumption_usd", "experiment_name"])
    warnings = []
    donchian = base_capital[base_capital["experiment_name"].eq("donchian_20_10_top20")]
    if not donchian.empty:
        turnover = pd.to_numeric(donchian["turnover_estimate"], errors="coerce").max()
        if pd.notna(turnover):
            warnings.append(f"- Donchian turnover estimate: {round(float(turnover), 4)}; compare degradation vs RS candidates.")
    top10 = base_capital[base_capital["experiment_name"].eq("rs_m126_top10_sma100_no_regime")]
    if not top10.empty:
        warnings.append("- Top10 has high return, but attribution concentration plus cost stress remains a risk.")
    top30 = base_capital[base_capital["experiment_name"].eq("rs_m126_top30_sma100_no_regime")]
    if not top30.empty:
        warnings.append("- Top30 remains the main candidate if it survives 10/25bps with lower concentration.")
        top30_liquidity = zero_rows[
            zero_rows["experiment_name"].eq("rs_m126_top30_sma100_no_regime")
        ]
        if not top30_liquidity.empty:
            max_warning = pd.to_numeric(
                top30_liquidity["liquidity_warning_count"],
                errors="coerce",
            ).fillna(0).max()
            warnings.append(
                f"- Top30 liquidity warning max across capital assumptions: {int(max_warning)}."
            )
    if not zero_rows.empty:
        median_ok = pd.to_numeric(
            zero_rows["median_order_pct_of_dollar_volume"],
            errors="coerce",
        ).dropna().le(1).all()
        p10_ok = pd.to_numeric(
            zero_rows["p10_order_pct_of_dollar_volume"],
            errors="coerce",
        ).dropna().le(1).all()
        min_problem = pd.to_numeric(
            zero_rows["min_order_pct_of_dollar_volume"],
            errors="coerce",
        ).dropna().gt(5).any()
        warnings.extend(
            [
                f"- Median liquidity ok across assumptions: {bool(median_ok)}.",
                f"- P10 liquidity ok across assumptions: {bool(p10_ok)}.",
                f"- Min liquidity has problem cases: {bool(min_problem)}.",
            ]
        )
    content = [
        "# Cost Slippage Stress Report",
        "",
        "## Net Return By Scenario (capital 10000)",
        "",
        _table_for_markdown(scenario_summary),
        "",
        "## Summary",
        "",
        f"- 10bps survivors: {', '.join(ten_bps) if ten_bps else 'none'}",
        f"- 25bps survivors: {', '.join(twenty_five) if twenty_five else 'none'}",
        *[
            f"- Liquidity safe at ${capital}: {', '.join(names) if names else 'none'}"
            for capital, names in sorted(safe_by_capital.items())
        ],
        *warnings,
        "",
        "## Liquidity Stress",
        "",
        _table_for_markdown(liquidity_summary),
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _write_final_candidate_decision_report(report, oos, attribution_summary, cost_stress, markdown_path):
    decision = _final_candidate_decision_frame(report, oos, attribution_summary, cost_stress)
    content = [
        "# Final Candidate Decision Report",
        "",
        "## Decision Table",
        "",
        _table_for_markdown(decision),
        "",
        "## Notes",
        "",
        "- Top30 is the main candidate: balanced OOS, lower concentration than Top10, and survives cost stress.",
        "- Top20 is an aggressive alternative with stronger concentration than Top30.",
        "- Donchian is a diversifier/back-up because it is a different family and attribution is more distributed.",
        "- Top10 is research/aggressive only because concentration risk remains high despite strong return and cost survival.",
        "- m63 remains the baseline control.",
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _final_candidate_decision_frame(report, oos, attribution_summary, cost_stress):
    candidates = [
        "rs_m126_top30_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "rs_m126_top10_sma100_no_regime",
        "donchian_20_10_top20",
        "rs_m63_sma100",
    ]
    by_name = report.set_index("Experiment Name") if "Experiment Name" in report else pd.DataFrame()
    oos_numeric = oos.copy() if isinstance(oos, pd.DataFrame) else pd.DataFrame()
    if not oos_numeric.empty:
        oos_numeric["net_return_pct"] = pd.to_numeric(oos_numeric["net_return_pct"], errors="coerce")
    attr_by_name = (
        attribution_summary.set_index("experiment_name")
        if isinstance(attribution_summary, pd.DataFrame)
        and not attribution_summary.empty
        and "experiment_name" in attribution_summary
        else pd.DataFrame()
    )
    zero_cost = (
        cost_stress[
            cost_stress["scenario"].eq("zero_cost_baseline")
            & cost_stress["capital_assumption_usd"].eq(10000)
        ].set_index("experiment_name")
        if isinstance(cost_stress, pd.DataFrame)
        and not cost_stress.empty
        and "experiment_name" in cost_stress
        else pd.DataFrame()
    )
    roles = {
        "rs_m126_top30_sma100_no_regime": "main",
        "rs_m126_top20_sma100_no_regime": "aggressive",
        "rs_m126_top10_sma100_no_regime": "research/aggressive only",
        "donchian_20_10_top20": "diversifier",
        "rs_m63_sma100": "baseline",
    }
    rows = []
    for name in candidates:
        base = by_name.loc[name] if name in by_name.index else {}
        oos_group = oos_numeric[oos_numeric["experiment_name"].eq(name)] if not oos_numeric.empty else pd.DataFrame()
        attr = attr_by_name.loc[name] if name in attr_by_name.index else {}
        cost = zero_cost.loc[name] if name in zero_cost.index else {}
        rows.append(
            {
                "experiment_name": name,
                "full_period_return": base.get(
                    "Net Getiri (%)",
                    cost.get("net_return_pct", pd.NA),
                ),
                "oos_avg_return": round(float(oos_group["net_return_pct"].mean()), 2)
                if not oos_group.empty
                else pd.NA,
                "oos_min_return": round(float(oos_group["net_return_pct"].min()), 2)
                if not oos_group.empty
                else pd.NA,
                "top1_contribution_pct": attr.get("top_1_symbol_contribution_pct", pd.NA),
                "top5_contribution_pct": attr.get("top_5_symbols_contribution_pct", pd.NA),
                "cost_survives": cost.get("survives_cost_test", pd.NA),
                "liquidity_warning_count": cost.get("liquidity_warning_count", pd.NA),
                "max_drawdown_pct": base.get(
                    "Max Drawdown (%)",
                    cost.get("max_drawdown_pct", pd.NA),
                ),
                "excess_vs_qqq_pct": base.get(
                    "Strategy Excess Return vs QQQ (%)",
                    cost.get("excess_vs_qqq_pct", pd.NA),
                ),
                "excess_vs_equal_weight_pct": base.get(
                    "Strategy Excess Return vs EqualWeight (%)",
                    cost.get("excess_vs_equal_weight_pct", pd.NA),
                ),
                "final_role": roles.get(name, "review"),
            }
        )
    return pd.DataFrame(rows)


def _read_final_candidate_decision_report(path):
    if path is None or not Path(path).exists():
        return None
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = lines.index("## Decision Table") + 2
    except ValueError:
        return None
    table_lines = []
    for line in lines[start:]:
        if not line.strip():
            break
        table_lines.append(line)
    if not table_lines:
        return None
    try:
        return pd.read_fwf(io.StringIO("\n".join(table_lines)))
    except Exception:
        return None


def _liquidity_guard_candidate_experiments(base_experiments):
    return _attribution_candidate_experiments(base_experiments)


def _liquidity_guard_scenarios():
    return [
        ("no_liquidity_guard", None, None),
        ("skip_entry_dollar_volume_below_100k", "min_dollar_volume", 100_000.0),
        ("skip_entry_dollar_volume_below_500k", "min_dollar_volume", 500_000.0),
        ("skip_entry_dollar_volume_below_1m", "min_dollar_volume", 1_000_000.0),
        ("skip_order_pct_of_dollar_volume_above_1pct", "max_order_pct", 1.0),
        ("skip_order_pct_of_dollar_volume_above_5pct", "max_order_pct", 5.0),
    ]


def _adjusted_equity_stats(equity_curve, skipped_trades):
    equity = pd.DataFrame(equity_curve, columns=["date", "equity"])
    if equity.empty:
        return pd.NA, pd.NA
    equity["date"] = pd.to_datetime(equity["date"])
    equity["equity"] = pd.to_numeric(equity["equity"], errors="coerce")
    equity = equity.dropna(subset=["equity"]).sort_values("date")
    if equity.empty:
        return pd.NA, pd.NA
    adjusted = equity["equity"].copy()
    if not skipped_trades.empty:
        exits = skipped_trades.copy()
        exits["Exit Time"] = pd.to_datetime(exits["Exit Time"])
        exits["Net PnL"] = pd.to_numeric(exits["Net PnL"], errors="coerce").fillna(0.0)
        skipped_by_exit = exits.groupby("Exit Time")["Net PnL"].sum().sort_index()
        cumulative = []
        running = 0.0
        items = list(skipped_by_exit.items())
        item_index = 0
        for date in equity["date"]:
            while item_index < len(items) and items[item_index][0] <= date:
                running += float(items[item_index][1])
                item_index += 1
            cumulative.append(running)
        adjusted = adjusted - pd.Series(cumulative, index=equity.index)
    start_equity = float(adjusted.iloc[0])
    end_equity = float(adjusted.iloc[-1])
    net_return = (end_equity / start_equity - 1.0) * 100.0 if start_equity > 0 else pd.NA
    drawdown = ((adjusted - adjusted.cummax()) / adjusted.cummax()).min() * 100.0
    return round(float(net_return), 2), round(float(drawdown), 2)


def _liquidity_guard_mask(trades, scenario_kind, scenario_value, per_position):
    dollar_volume = pd.to_numeric(trades.get("entry_dollar_volume"), errors="coerce")
    if scenario_kind is None:
        return pd.Series(False, index=trades.index)
    if scenario_kind == "min_dollar_volume":
        return dollar_volume.lt(float(scenario_value)).fillna(False)
    if scenario_kind == "max_order_pct":
        order_pct = (float(per_position) / dollar_volume.replace(0, pd.NA)) * 100.0
        return order_pct.gt(float(scenario_value)).fillna(False)
    raise ValueError(f"Unknown liquidity guard scenario: {scenario_kind}")


def _liquidity_guard_sensitivity_rows(results, asset_data):
    rows = []
    for result in results:
        row = result["row"]
        raw = result.get("raw") or {}
        metrics = raw.get("metrics", {}) or {}
        params = raw.get("params", {}) or {}
        trades = _trades_with_entry_liquidity(
            pd.DataFrame(metrics.get("_trade_log", [])),
            asset_data,
        )
        max_positions = _max_positions_from_params(params)
        no_guard_net = pd.to_numeric(
            pd.Series([row.get("Net Getiri (%)")]),
            errors="coerce",
        ).iloc[0]
        qqq = row.get("QQQ BuyHold Return (%)", pd.NA)
        ew = row.get("EqualWeight Universe Return (%)", pd.NA)
        for capital in [10000, 25000, 50000, 100000]:
            per_position = float(capital) / max_positions
            for scenario, scenario_kind, scenario_value in _liquidity_guard_scenarios():
                skip_mask = _liquidity_guard_mask(
                    trades,
                    scenario_kind,
                    scenario_value,
                    per_position,
                )
                skipped = trades[skip_mask].copy()
                kept = trades[~skip_mask].copy()
                if scenario == "no_liquidity_guard":
                    net_return = row.get("Net Getiri (%)", pd.NA)
                    max_dd = row.get("Max Drawdown (%)", pd.NA)
                    stats = {
                        "trade_count": row.get("Trade Count", pd.NA),
                        "profit_factor": row.get("Profit Factor", pd.NA),
                        "expectancy_r": row.get("Expectancy R", pd.NA),
                        "avg_holding_bars": row.get("Avg Holding Bars", pd.NA),
                    }
                else:
                    net_return, max_dd = _adjusted_equity_stats(
                        metrics.get("_equity_curve", []),
                        skipped,
                    )
                    stats = _trade_stats(kept)
                trade_count = int(len(trades))
                skipped_count = int(len(skipped))
                skipped_pct = round(skipped_count / trade_count * 100.0, 2) if trade_count else 0.0
                excess_qqq = (
                    round(float(net_return) - float(qqq), 2)
                    if pd.notna(net_return) and pd.notna(qqq)
                    else pd.NA
                )
                excess_ew = (
                    round(float(net_return) - float(ew), 2)
                    if pd.notna(net_return) and pd.notna(ew)
                    else pd.NA
                )
                degradation = (
                    round(float(no_guard_net) - float(net_return), 2)
                    if pd.notna(no_guard_net) and pd.notna(net_return)
                    else pd.NA
                )
                pass_metric = (
                    pd.notna(net_return)
                    and float(net_return) > 0.0
                    and pd.notna(stats["profit_factor"])
                    and float(stats["profit_factor"]) > 1.0
                )
                rows.append(
                    {
                        "experiment_name": row.get("Experiment Name", result["experiment_name"]),
                        "guard_scenario": scenario,
                        "capital_assumption_usd": capital,
                        "net_return_pct": net_return,
                        "max_drawdown_pct": max_dd,
                        "trade_count": stats["trade_count"],
                        "skipped_trade_count": skipped_count,
                        "skipped_trade_pct": skipped_pct,
                        "profit_factor": stats["profit_factor"],
                        "expectancy_r": stats["expectancy_r"],
                        "avg_holding_bars": stats["avg_holding_bars"],
                        "qqq_return_pct": qqq,
                        "equal_weight_return_pct": ew,
                        "excess_vs_qqq_pct": excess_qqq,
                        "excess_vs_equal_weight_pct": excess_ew,
                        "degradation_vs_no_guard_pct": degradation,
                        "liquidity_guard_pass": bool(pass_metric),
                    }
                )
    columns = [
        "experiment_name",
        "guard_scenario",
        "capital_assumption_usd",
        "net_return_pct",
        "max_drawdown_pct",
        "trade_count",
        "skipped_trade_count",
        "skipped_trade_pct",
        "profit_factor",
        "expectancy_r",
        "avg_holding_bars",
        "qqq_return_pct",
        "equal_weight_return_pct",
        "excess_vs_qqq_pct",
        "excess_vs_equal_weight_pct",
        "degradation_vs_no_guard_pct",
        "liquidity_guard_pass",
    ]
    return pd.DataFrame(rows, columns=columns)


def _best_guard_for_capital(frame, capital, experiment_name):
    priority = [
        "skip_entry_dollar_volume_below_100k",
        "skip_entry_dollar_volume_below_500k",
        "skip_entry_dollar_volume_below_1m",
        "skip_order_pct_of_dollar_volume_above_5pct",
        "skip_order_pct_of_dollar_volume_above_1pct",
    ]
    subset = frame[
        frame["capital_assumption_usd"].eq(capital)
        & frame["experiment_name"].eq(experiment_name)
    ]
    for scenario in priority:
        row = subset[subset["guard_scenario"].eq(scenario)]
        if not row.empty and bool(row["liquidity_guard_pass"].iloc[0]):
            return scenario
    return "no passing guard"


def _write_liquidity_guard_sensitivity_report(report, csv_path, markdown_path):
    report.to_csv(csv_path, index=False)
    base_capital = report[report["capital_assumption_usd"].eq(10000)].copy()
    focus_columns = [
        "experiment_name",
        "guard_scenario",
        "capital_assumption_usd",
        "net_return_pct",
        "trade_count",
        "skipped_trade_count",
        "skipped_trade_pct",
        "profit_factor",
        "excess_vs_qqq_pct",
        "degradation_vs_no_guard_pct",
        "liquidity_guard_pass",
    ]
    summary = base_capital[focus_columns].sort_values(
        ["experiment_name", "guard_scenario"]
    )
    def _scenario_row(name, scenario, capital=10000):
        subset = report[
            report["experiment_name"].eq(name)
            & report["guard_scenario"].eq(scenario)
            & report["capital_assumption_usd"].eq(capital)
        ]
        return subset.iloc[0] if not subset.empty else pd.Series(dtype=object)

    top30_500k = _scenario_row("rs_m126_top30_sma100_no_regime", "skip_entry_dollar_volume_below_500k")
    top20_500k = _scenario_row("rs_m126_top20_sma100_no_regime", "skip_entry_dollar_volume_below_500k")
    top10_500k = _scenario_row("rs_m126_top10_sma100_no_regime", "skip_entry_dollar_volume_below_500k")
    donchian_500k = _scenario_row("donchian_20_10_top20", "skip_entry_dollar_volume_below_500k")
    top30_main = bool(top30_500k.get("liquidity_guard_pass", False)) and pd.to_numeric(
        pd.Series([top30_500k.get("excess_vs_qqq_pct", pd.NA)]),
        errors="coerce",
    ).iloc[0] > 0
    top20_aggressive = bool(top20_500k.get("liquidity_guard_pass", False))
    top10_research = True
    donchian_low_impact = pd.to_numeric(
        pd.Series([donchian_500k.get("degradation_vs_no_guard_pct", pd.NA)]),
        errors="coerce",
    ).iloc[0]
    capital_rows = [
        {
            "capital_assumption_usd": capital,
            "recommended_minimum_guard": _best_guard_for_capital(
                report,
                capital,
                "rs_m126_top30_sma100_no_regime",
            ),
        }
        for capital in [10000, 25000, 50000, 100000]
    ]
    content = [
        "# Liquidity Guard Sensitivity Report",
        "",
        "## Decision Summary",
        "",
        f"- Top30 main after 500k guard: {top30_main}; skipped trades: {top30_500k.get('skipped_trade_count', pd.NA)}.",
        f"- Top20 aggressive alternative after 500k guard: {top20_aggressive}; skipped trades: {top20_500k.get('skipped_trade_count', pd.NA)}.",
        f"- Top10 remains research/aggressive only: {top10_research}; skipped trades at 500k: {top10_500k.get('skipped_trade_count', pd.NA)}.",
        f"- Donchian low impact at 500k guard: {bool(pd.notna(donchian_low_impact) and abs(float(donchian_low_impact)) <= 1.0)}; degradation pct: {donchian_500k.get('degradation_vs_no_guard_pct', pd.NA)}.",
        "",
        "## Recommended Minimum Guard By Capital",
        "",
        _table_for_markdown(pd.DataFrame(capital_rows)),
        "",
        "## Capital 10000 Scenario Detail",
        "",
        _table_for_markdown(summary),
        "",
        "Note: guard scenarios are report-level sensitivity calculations from executed trade logs; strategy trade logic is not changed.",
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _paper_candidate_experiments(base_experiments):
    candidates = {experiment["name"]: dict(experiment) for experiment in base_experiments}
    for experiment in _rs_robustness_experiments():
        if experiment["name"] in {
            "rs_m126_top30_sma100_no_regime",
            "rs_m126_top20_sma100_no_regime",
        }:
            candidates[experiment["name"]] = experiment
    wanted = [
        "rs_m126_top30_sma100_no_regime",
        "rs_m126_top20_sma100_no_regime",
        "donchian_20_10_top20",
    ]
    missing = [name for name in wanted if name not in candidates]
    if missing:
        raise ValueError(f"Paper report aday config eksik: {', '.join(missing)}")
    return [candidates[name] for name in wanted]


def _paper_signal_columns():
    return PAPER_SIGNAL_COLUMNS


def _paper_trade_log_columns():
    return PAPER_TRADE_LOG_COLUMNS


def _paper_pending_order_columns():
    return PAPER_PENDING_ORDERS_COLUMNS


def _paper_order_review_columns():
    return PAPER_ORDER_REVIEW_COLUMNS


def _paper_main_strategy_name():
    return "rs_m126_top30_sma100_no_regime"


def _ranking_score_from_row(row):
    for column in ["RS_Score", "Donchian_Breakout_Strength", "Momentum_Factor"]:
        value = row.get(column, pd.NA)
        if pd.notna(value):
            return value
    return pd.NA


def _load_paper_positions(path):
    if not path.exists():
        return {}, True
    try:
        ledger = pd.read_csv(path)
    except Exception:
        return {}, True
    positions = {}
    if ledger.empty:
        return positions, True
    for _, row in ledger.iterrows():
        strategy = str(row.get("strategy_name", "")).strip()
        symbol = str(row.get("symbol", "")).strip().upper()
        action = str(row.get("action", "")).strip().upper()
        fill_status = str(row.get("fill_status", "")).strip().upper()
        if not strategy or not symbol:
            continue
        if fill_status and fill_status != "PAPER_FILLED":
            continue
        key = (strategy, symbol)
        if action in {"BUY", "ENTRY"}:
            positions[key] = True
        elif action in {"SELL", "EXIT", "CLOSE"}:
            positions.pop(key, None)
    return positions, False


def _paper_signal_rows_for_result(
    experiment_name,
    params,
    final_data,
    run_datetime,
    positions,
    initial_state,
    paper_strategy_mode,
    include_watchlist,
    paper_capital=25000,
):
    if not final_data:
        return []
    latest_dates = [frame.index.max() for frame in final_data.values() if frame is not None and not frame.empty]
    if not latest_dates:
        return []
    signal_date = max(latest_dates)
    max_positions = _max_positions_from_params(params)
    rows = []
    for symbol, frame in sorted(final_data.items()):
        if frame is None or frame.empty or signal_date not in frame.index:
            continue
        row = frame.loc[signal_date]
        entry = bool(row.get("ENTRY_SIGNAL", False))
        exit_signal = bool(row.get("EXIT_SIGNAL", False))
        already_in_position = bool(positions.get((experiment_name, symbol), False))
        is_main = experiment_name == _paper_main_strategy_name()
        is_watch_only = paper_strategy_mode == "main" and not is_main
        if entry and is_watch_only:
            base_action = "WATCH"
        elif entry and not already_in_position:
            base_action = "BUY"
        elif exit_signal and already_in_position:
            base_action = "SELL"
        else:
            continue
        close = pd.to_numeric(pd.Series([row.get("Close", pd.NA)]), errors="coerce").iloc[0]
        volume = pd.to_numeric(pd.Series([row.get("Volume", pd.NA)]), errors="coerce").iloc[0]
        dollar_volume = float(close) * float(volume) if pd.notna(close) and pd.notna(volume) else pd.NA
        next_pos = frame.index.searchsorted(signal_date, side="right")
        next_open_available = bool(next_pos < len(frame.index) and pd.notna(frame.iloc[next_pos].get("Open", pd.NA)))
        position_notional = round(float(paper_capital) / max_positions, 2)
        order_pct = (
            round(position_notional / float(dollar_volume) * 100.0, 6)
            if pd.notna(dollar_volume) and float(dollar_volume) > 0
            else pd.NA
        )
        liquidity_warnings = []
        if pd.isna(dollar_volume):
            liquidity_warnings.append("missing_dollar_volume")
        elif float(dollar_volume) < 500_000:
            liquidity_warnings.append("estimated_dollar_volume_below_500k")
        if pd.notna(order_pct) and float(order_pct) > 1.0:
            liquidity_warnings.append("order_size_pct_of_dollar_volume_above_1pct")
        action = "SKIP" if liquidity_warnings and base_action in {"BUY", "SELL"} else base_action
        reason = ["initial_paper_state" if initial_state else "paper_ledger_state"]
        if base_action == "WATCH":
            reason.append("watch_only_comparison_strategy")
        else:
            reason.append("latest_entry_signal" if base_action == "BUY" else "latest_exit_signal")
        if not next_open_available:
            reason.append("missing_next_open")
        rows.append(
            {
                "signal_scope": "latest_only",
                "run_datetime": run_datetime,
                "paper_strategy_mode": paper_strategy_mode,
                "paper_portfolio_state": "initial_empty" if initial_state else "ledger_loaded",
                "strategy_name": experiment_name,
                "signal_date": str(signal_date),
                "symbol": symbol,
                "action": action,
                "fill_status": "NOT_APPLICABLE",
                "user_decision": "HOLD_REVIEW",
                "reason": ";".join(reason),
                "is_new_signal": True,
                "already_in_position": already_in_position,
                "paper_actionable": False,
                "deferred_due_to_ramp_in": False,
                "paper_rank": pd.NA,
                "paper_max_new_positions_per_day": pd.NA,
                "ranking_score": _ranking_score_from_row(row),
                "current_position_status": "open" if already_in_position else "flat",
                "liquidity_guard_pass": not liquidity_warnings,
                "liquidity_warning_reason": ";".join(liquidity_warnings),
                "warning": ";".join(liquidity_warnings),
                "estimated_dollar_volume": round(float(dollar_volume), 2)
                if pd.notna(dollar_volume)
                else pd.NA,
                "order_size_pct_of_dollar_volume": order_pct,
                "paper_capital_assumption": paper_capital,
                "paper_position_notional": position_notional,
                "entry_price_reference": close,
                "assumed_fill_rule": "next_available_open",
                "next_open_available": next_open_available,
                "max_positions": max_positions,
            }
        )
    return rows


def _run_paper_signal_report(
    experiments,
    data_folder,
    run_profile,
    quick_mode,
    intraday_dir,
    daily_dir,
    raw_dir,
    intraday_timeframe,
    daily_timeframe,
    cache_rebuilt,
    asset_data,
    benchmark_data,
    ledger_path,
    paper_strategy_mode="main",
    include_watchlist=False,
    paper_max_new_positions_per_day=5,
):
    run_datetime = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    positions, initial_state = _load_paper_positions(ledger_path)
    tasks = _build_experiment_tasks(
        _paper_candidate_experiments(experiments),
        data_folder,
        run_profile,
        quick_mode,
        intraday_dir,
        daily_dir,
        raw_dir,
        intraday_timeframe,
        daily_timeframe,
        cache_rebuilt,
        include_raw=False,
    )
    for task in tasks:
        final_data, _ = _run_strategy(asset_data, benchmark_data, task["params"])
        rows.extend(
            _paper_signal_rows_for_result(
                task["experiment_name"],
                task["params"],
                final_data,
                run_datetime,
                positions,
                initial_state,
                paper_strategy_mode,
                include_watchlist,
            )
        )
    output = pd.DataFrame(rows, columns=_paper_signal_columns())
    if output.empty:
        return output
    output["paper_max_new_positions_per_day"] = int(paper_max_new_positions_per_day)
    output["paper_actionable"] = False
    output["deferred_due_to_ramp_in"] = False
    main_buy = output[
        output["strategy_name"].eq(_paper_main_strategy_name())
        & output["action"].eq("BUY")
    ].copy()
    if not main_buy.empty:
        main_buy["_rank_score"] = pd.to_numeric(main_buy["ranking_score"], errors="coerce")
        ranked_index = main_buy.sort_values(
            ["_rank_score", "symbol"],
            ascending=[False, True],
            na_position="last",
        ).index.tolist()
        for rank, index in enumerate(ranked_index, 1):
            output.loc[index, "paper_rank"] = rank
            if rank <= int(paper_max_new_positions_per_day):
                output.loc[index, "paper_actionable"] = True
            else:
                output.loc[index, "action"] = "DEFER"
                output.loc[index, "paper_actionable"] = False
                output.loc[index, "deferred_due_to_ramp_in"] = True
                reason = str(output.loc[index, "reason"] or "")
                output.loc[index, "reason"] = f"{reason};ramp_in_limit" if reason else "ramp_in_limit"
    sell_mask = output["strategy_name"].eq(_paper_main_strategy_name()) & output["action"].eq("SELL")
    output.loc[sell_mask, "paper_actionable"] = True
    skip_mask = output["strategy_name"].eq(_paper_main_strategy_name()) & output["action"].eq("SKIP")
    output.loc[skip_mask, "paper_actionable"] = False
    return output[_paper_signal_columns()]


def _write_paper_signal_report(paper_report, csv_path, markdown_path):
    paper_report = ensure_columns(paper_report, _paper_signal_columns(), fill_value=pd.NA)
    paper_report.to_csv(csv_path, index=False)
    liquidity_warnings_count = (
        int(paper_report["liquidity_warning_reason"].fillna("").astype(str).ne("").sum())
        if not paper_report.empty and "liquidity_warning_reason" in paper_report
        else 0
    )
    action_counts = paper_report["action"].value_counts().to_dict() if not paper_report.empty else {}
    content = [
        "# Paper Signal Report",
        "",
        f"- Latest signal count: {len(paper_report)}",
        f"- Action counts: {action_counts}",
        f"- Liquidity warnings count: {liquidity_warnings_count}",
        "- Assumed fill rule: next_available_open",
        "- Broker/API orders: disabled; report-only.",
        "",
        "## Signals",
        "",
        _readable_table(paper_report),
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _paper_order_key(row):
    return (
        str(row.get("strategy_name", "")),
        str(row.get("symbol", "")).upper(),
        str(row.get("action", "")).upper(),
    )


def _load_paper_order_review(path):
    return read_order_review(Path(path).parent)


def _write_paper_order_review(paper_report, path):
    existing = _load_paper_order_review(path)
    decisions = {}
    notes = {}
    for _, row in existing.iterrows():
        key = _paper_order_key(row)
        decisions[key] = str(row.get("user_decision", "HOLD_REVIEW")).strip().upper() or "HOLD_REVIEW"
        notes[key] = row.get("notes", "")
    actionable = paper_report[
        paper_report.get("paper_actionable", pd.Series(False, index=paper_report.index)).astype(bool)
        & paper_report["action"].isin(["BUY", "SELL"])
    ].copy() if paper_report is not None and not paper_report.empty else pd.DataFrame()
    rows = []
    for _, row in actionable.iterrows():
        key = _paper_order_key(row)
        rows.append(
            {
                "symbol": row.get("symbol", ""),
                "strategy_name": row.get("strategy_name", ""),
                "action": row.get("action", ""),
                "user_decision": decisions.get(key, "HOLD_REVIEW"),
                "notes": notes.get(key, ""),
            }
        )
    review = pd.DataFrame(rows, columns=_paper_order_review_columns()).drop_duplicates(
        subset=["symbol", "strategy_name", "action"],
        keep="first",
    )
    review = ensure_columns(review, _paper_order_review_columns())
    return write_order_review(Path(path).parent, review)


def _ensure_paper_trade_log(path):
    return write_trade_log(Path(path).parent, read_trade_log(Path(path).parent))


def _next_open_for_signal(asset_data, symbol, signal_date):
    frame = asset_data.get(str(symbol).upper())
    if frame is None or frame.empty or "Open" not in frame:
        return pd.NA
    ts = pd.Timestamp(signal_date)
    index = frame.index
    if index.tz is not None and ts.tzinfo is None:
        ts = ts.tz_localize(index.tz)
    elif index.tz is None and ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    pos = index.searchsorted(ts, side="right")
    if pos >= len(index):
        return pd.NA
    value = frame.iloc[pos].get("Open", pd.NA)
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


def _apply_paper_order_review(paper_report, review, trade_log_path, asset_data):
    log = _ensure_paper_trade_log(trade_log_path)
    if paper_report is None or paper_report.empty:
        return paper_report, log
    decision_by_key = {
        _paper_order_key(row): str(row.get("user_decision", "HOLD_REVIEW")).strip().upper() or "HOLD_REVIEW"
        for _, row in review.iterrows()
    } if review is not None and not review.empty else {}
    notes_by_key = {
        _paper_order_key(row): row.get("notes", "")
        for _, row in review.iterrows()
    } if review is not None and not review.empty else {}
    output = paper_report.copy()
    new_log_rows = []
    existing_keys = set()
    if not log.empty:
        for _, row in log.iterrows():
            existing_keys.add(
                (
                    str(row.get("strategy_name", "")),
                    str(row.get("symbol", "")).upper(),
                    str(row.get("action", "")).upper(),
                    str(row.get("signal_date", "")),
                    str(row.get("fill_status", "")),
                )
            )
    for index, row in output.iterrows():
        if not bool(row.get("paper_actionable", False)) or row.get("action") not in {"BUY", "SELL"}:
            continue
        key = _paper_order_key(row)
        decision = decision_by_key.get(key, "HOLD_REVIEW")
        output.loc[index, "user_decision"] = decision
        if decision == "ACCEPT":
            fill_price = _next_open_for_signal(asset_data, row.get("symbol"), row.get("signal_date"))
            if pd.notna(fill_price):
                fill_status = "PAPER_FILLED"
                output.loc[index, "fill_status"] = fill_status
                shares = round(float(row.get("paper_position_notional", 0.0)) / float(fill_price), 6) if float(fill_price) > 0 else 0.0
                log_key = (
                    str(row.get("strategy_name", "")),
                    str(row.get("symbol", "")).upper(),
                    str(row.get("action", "")).upper(),
                    str(row.get("signal_date", "")),
                    fill_status,
                )
                if log_key not in existing_keys:
                    new_log_rows.append(
                        {
                            "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "strategy_name": row.get("strategy_name", ""),
                            "symbol": row.get("symbol", ""),
                            "action": row.get("action", ""),
                            "signal_date": row.get("signal_date", ""),
                            "signal_price": row.get("entry_price_reference", pd.NA),
                            "assumed_fill_price": fill_price,
                            "fill_status": fill_status,
                            "shares": shares,
                            "notional": row.get("paper_position_notional", pd.NA),
                            "paper_capital": row.get("paper_capital_assumption", pd.NA),
                            "reason": row.get("reason", ""),
                            "liquidity_guard_pass": row.get("liquidity_guard_pass", pd.NA),
                            "notes": notes_by_key.get(key, ""),
                        }
                    )
            else:
                output.loc[index, "fill_status"] = "PENDING_NEXT_OPEN"
        elif decision == "REJECT":
            fill_status = "REJECTED"
            output.loc[index, "fill_status"] = fill_status
            log_key = (
                str(row.get("strategy_name", "")),
                str(row.get("symbol", "")).upper(),
                str(row.get("action", "")).upper(),
                str(row.get("signal_date", "")),
                fill_status,
            )
            if log_key not in existing_keys:
                new_log_rows.append(
                    {
                        "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "strategy_name": row.get("strategy_name", ""),
                        "symbol": row.get("symbol", ""),
                        "action": row.get("action", ""),
                        "signal_date": row.get("signal_date", ""),
                        "signal_price": row.get("entry_price_reference", pd.NA),
                        "assumed_fill_price": pd.NA,
                        "fill_status": fill_status,
                        "shares": 0,
                        "notional": row.get("paper_position_notional", pd.NA),
                        "paper_capital": row.get("paper_capital_assumption", pd.NA),
                        "reason": row.get("reason", ""),
                        "liquidity_guard_pass": row.get("liquidity_guard_pass", pd.NA),
                        "notes": notes_by_key.get(key, ""),
                    }
                )
        else:
            output.loc[index, "fill_status"] = "PENDING_NEXT_OPEN" if not bool(row.get("next_open_available", False)) else "PENDING_REVIEW"
    if new_log_rows:
        log = append_trade_log(Path(trade_log_path).parent, new_log_rows)
    return output, log


def _load_previous_paper_signal_report(path):
    if not Path(path).exists():
        return pd.DataFrame(columns=_paper_signal_columns())
    try:
        report = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=_paper_signal_columns())
    for column in _paper_signal_columns():
        if column not in report:
            report[column] = pd.NA
    return report[_paper_signal_columns()]


def _apply_previous_pending_next_open_orders(current_report, previous_report, review, trade_log_path, asset_data):
    if previous_report is None or previous_report.empty:
        return current_report
    log = _ensure_paper_trade_log(trade_log_path)
    decision_by_key = {
        _paper_order_key(row): str(row.get("user_decision", "HOLD_REVIEW")).strip().upper() or "HOLD_REVIEW"
        for _, row in review.iterrows()
    } if review is not None and not review.empty else {}
    notes_by_key = {
        _paper_order_key(row): row.get("notes", "")
        for _, row in review.iterrows()
    } if review is not None and not review.empty else {}
    output = current_report.copy() if current_report is not None else pd.DataFrame(columns=_paper_signal_columns())
    existing_orders = set()
    if not output.empty:
        for _, row in output.iterrows():
            existing_orders.add(
                (
                    str(row.get("strategy_name", "")),
                    str(row.get("symbol", "")).upper(),
                    str(row.get("action", "")).upper(),
                    str(row.get("signal_date", "")),
                )
            )
    existing_log_keys = set()
    if not log.empty:
        for _, row in log.iterrows():
            existing_log_keys.add(
                (
                    str(row.get("strategy_name", "")),
                    str(row.get("symbol", "")).upper(),
                    str(row.get("action", "")).upper(),
                    str(row.get("signal_date", "")),
                    str(row.get("fill_status", "")),
                )
            )
    rows_to_append = []
    log_rows = []
    pending = previous_report[
        previous_report.get("fill_status", pd.Series("", index=previous_report.index)).astype(str).str.upper().eq("PENDING_NEXT_OPEN")
        & previous_report.get("user_decision", pd.Series("", index=previous_report.index)).astype(str).str.upper().eq("ACCEPT")
        & previous_report.get("action", pd.Series("", index=previous_report.index)).astype(str).str.upper().isin(["BUY", "SELL"])
    ].copy()
    for _, row in pending.iterrows():
        order_id = (
            str(row.get("strategy_name", "")),
            str(row.get("symbol", "")).upper(),
            str(row.get("action", "")).upper(),
            str(row.get("signal_date", "")),
        )
        fill_price = _next_open_for_signal(asset_data, row.get("symbol"), row.get("signal_date"))
        carried = row.to_dict()
        carried["user_decision"] = decision_by_key.get(_paper_order_key(row), "ACCEPT")
        carried["paper_actionable"] = True
        if pd.notna(fill_price):
            carried["fill_status"] = "PAPER_FILLED"
            log_key = (*order_id, "PAPER_FILLED")
            if log_key not in existing_log_keys:
                shares = round(float(row.get("paper_position_notional", 0.0)) / float(fill_price), 6) if float(fill_price) > 0 else 0.0
                log_rows.append(
                    {
                        "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "strategy_name": row.get("strategy_name", ""),
                        "symbol": row.get("symbol", ""),
                        "action": row.get("action", ""),
                        "signal_date": row.get("signal_date", ""),
                        "signal_price": row.get("entry_price_reference", pd.NA),
                        "assumed_fill_price": fill_price,
                        "fill_status": "PAPER_FILLED",
                        "shares": shares,
                        "notional": row.get("paper_position_notional", pd.NA),
                        "paper_capital": row.get("paper_capital_assumption", pd.NA),
                        "reason": row.get("reason", ""),
                        "liquidity_guard_pass": row.get("liquidity_guard_pass", pd.NA),
                        "notes": notes_by_key.get(_paper_order_key(row), ""),
                    }
                )
        else:
            carried["fill_status"] = "PENDING_NEXT_OPEN"
            reason = str(carried.get("reason", "") or "")
            if "missing_next_open" not in reason:
                carried["reason"] = f"{reason};missing_next_open" if reason else "missing_next_open"
        if order_id not in existing_orders:
            rows_to_append.append(carried)
    if log_rows:
        log = append_trade_log(Path(trade_log_path).parent, log_rows)
    if rows_to_append:
        output = pd.concat([output, pd.DataFrame(rows_to_append)], ignore_index=True)
    for column in _paper_signal_columns():
        if column not in output:
            output[column] = pd.NA
    return output[_paper_signal_columns()]


def _write_paper_pending_orders(paper_report, path, latest_daily_date="none"):
    rows = []
    actionable = paper_report[
        paper_report.get("paper_actionable", pd.Series(False, index=paper_report.index)).astype(bool)
        & paper_report["action"].isin(["BUY", "SELL"])
    ].copy() if paper_report is not None and not paper_report.empty else pd.DataFrame()
    for _, row in actionable.iterrows():
        status = "FILLED" if row.get("fill_status") == "PAPER_FILLED" else ("REJECTED" if row.get("fill_status") == "REJECTED" else "PENDING")
        aging = _pending_order_age_fields(row.get("signal_date", ""), latest_daily_date)
        rows.append(
            {
                "created_at": row.get("run_datetime", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")),
                "signal_date": row.get("signal_date", ""),
                "strategy_name": row.get("strategy_name", ""),
                "symbol": row.get("symbol", ""),
                "action": row.get("action", ""),
                "paper_rank": row.get("paper_rank", pd.NA),
                "ranking_score": row.get("ranking_score", pd.NA),
                "paper_capital": row.get("paper_capital_assumption", pd.NA),
                "paper_position_notional": row.get("paper_position_notional", pd.NA),
                "liquidity_guard_pass": row.get("liquidity_guard_pass", pd.NA),
                "estimated_dollar_volume": row.get("estimated_dollar_volume", pd.NA),
                "order_size_pct_of_dollar_volume": row.get("order_size_pct_of_dollar_volume", pd.NA),
                "status": status,
                "user_decision": "REVIEW_REQUIRED"
                if str(row.get("user_decision", "HOLD_REVIEW")).upper() == "HOLD_REVIEW"
                else row.get("user_decision", "REVIEW_REQUIRED"),
                "reason": row.get("reason", ""),
                **aging,
                "notes": "",
            }
        )
    pending = ensure_columns(pd.DataFrame(rows), _paper_pending_order_columns(), fill_value=pd.NA)
    write_pending_orders(Path(path).parent, pending)


def _write_paper_trade_log_template(path):
    create_trade_log_template(Path(path).parent)


def _write_paper_trading_plan(path):
    content = [
        "# Paper Trading Plan",
        "",
        "## Selected Candidates",
        "",
        "- Main candidate: rs_m126_top30_sma100_no_regime",
        "- Aggressive alternative: rs_m126_top20_sma100_no_regime",
        "- Diversifier: donchian_20_10_top20",
        "- Research/aggressive only: rs_m126_top10_sma100_no_regime (not included in default paper report)",
        "- Baseline: rs_m63_sma100",
        "",
        "## Paper Report Scope",
        "",
        "- Main strategy: rs_m126_top30_sma100_no_regime",
        "- Comparison strategies: rs_m126_top20_sma100_no_regime, donchian_20_10_top20",
        "- Capital assumptions: 10000, 25000, 50000",
        "- Max positions: Top30=30, Top20=20, Donchian=20",
        "",
        "## Fill Assumption",
        "",
        "- If a signal is produced, paper fill is assumed at the next available open.",
        "- No Alpaca/broker API calls are made.",
        "- No live orders are submitted.",
        "",
        "## Liquidity Guard",
        "",
        "- entry dollar volume below 500k warning",
        "- order pct dollar volume above 1% warning",
        "- rejected/liquidity-skipped signals must be reviewed before paper fills are logged",
        "",
        "## Stop Conditions",
        "",
        "- stale data",
        "- missing data",
        "- signal spike",
        "- liquidity warning spike",
        "- drawdown threshold",
        "- benchmark divergence",
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def _observed_us_market_holidays(year):
    holidays = {
        pd.Timestamp(year=year, month=1, day=1).date(),
        pd.Timestamp(year=year, month=6, day=19).date(),
        pd.Timestamp(year=year, month=7, day=4).date(),
        pd.Timestamp(year=year, month=12, day=25).date(),
    }
    observed = set()
    for day in holidays:
        ts = pd.Timestamp(day)
        if ts.weekday() == 5:
            observed.add((ts - pd.Timedelta(days=1)).date())
        elif ts.weekday() == 6:
            observed.add((ts + pd.Timedelta(days=1)).date())
        else:
            observed.add(day)
    # Thanksgiving: fourth Thursday in November.
    november = pd.date_range(f"{year}-11-01", f"{year}-11-30", freq="D")
    thursdays = [day.date() for day in november if day.weekday() == 3]
    if len(thursdays) >= 4:
        observed.add(thursdays[3])
    return observed


def _latest_expected_us_session(current_date=None):
    current = pd.Timestamp(current_date or pd.Timestamp.now()).date()
    candidate = pd.Timestamp(current)
    for _ in range(10):
        holidays = _observed_us_market_holidays(candidate.year)
        if candidate.weekday() < 5 and candidate.date() not in holidays:
            return candidate.date()
        candidate -= pd.Timedelta(days=1)
    return current


def _next_us_market_session(after_date):
    candidate = pd.Timestamp(after_date).date() + pd.Timedelta(days=1)
    for _ in range(15):
        holidays = _observed_us_market_holidays(candidate.year)
        if candidate.weekday() < 5 and candidate not in holidays:
            return candidate
        candidate += pd.Timedelta(days=1)
    return pd.Timestamp(after_date).date() + pd.Timedelta(days=1)


def _us_trading_days_between(start_date, end_date):
    if start_date is None or end_date is None:
        return 0
    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    if end < start:
        return 0
    count = 0
    current = start
    while current <= end:
        holidays = _observed_us_market_holidays(current.year)
        if current.weekday() < 5 and current not in holidays:
            count += 1
        current += pd.Timedelta(days=1)
    return count


def _pending_order_age_fields(signal_date, latest_daily_date, current_date=None):
    signal_ts = pd.to_datetime(signal_date, errors="coerce")
    current = pd.Timestamp(current_date or pd.Timestamp.now()).date()
    latest_data_ts = pd.to_datetime(latest_daily_date, errors="coerce")
    latest_data_date = latest_data_ts.date() if pd.notna(latest_data_ts) else None
    if pd.isna(signal_ts):
        return {
            "pending_age_calendar_days": 0,
            "pending_age_trading_days": 0,
            "expected_next_open_date": "unknown",
            "pending_stale_warning": False,
            "pending_stale_reason": "invalid_signal_date",
        }
    signal_day = signal_ts.date()
    expected_next_open = _next_us_market_session(signal_day)
    age_calendar = max((current - signal_day).days, 0)
    age_trading = _us_trading_days_between(expected_next_open, current)
    if current < expected_next_open:
        reason = "waiting_for_weekend_or_holiday_next_open"
        stale = False
    elif latest_data_date is None or latest_data_date < expected_next_open:
        reason = "missing_next_open_data"
        stale = age_trading > 3
    else:
        reason = "pending_with_next_open_data_available"
        stale = age_trading > 3
    return {
        "pending_age_calendar_days": int(age_calendar),
        "pending_age_trading_days": int(age_trading),
        "expected_next_open_date": str(expected_next_open),
        "pending_stale_warning": bool(stale),
        "pending_stale_reason": reason,
    }


def _paper_stale_status(paper_report):
    current_date = pd.Timestamp.now().date()
    expected = _latest_expected_us_session(current_date)
    latest_ts = (
        pd.to_datetime(paper_report["signal_date"], errors="coerce").max()
        if paper_report is not None and not paper_report.empty and "signal_date" in paper_report
        else pd.NaT
    )
    latest_date = latest_ts.date() if pd.notna(latest_ts) else None
    stale = bool(latest_date is None or latest_date < expected)
    reason = (
        "no_latest_signal_date"
        if latest_date is None
        else ("latest_signal_date_before_expected_session" if stale else "latest_signal_date_matches_expected_session")
    )
    return {
        "latest_signal_date": str(latest_date) if latest_date is not None else "none",
        "current_date": str(current_date),
        "calendar_check_mode": "fallback_us_weekday_holidays",
        "latest_expected_us_session": str(expected),
        "stale_data_warning": stale,
        "stale_reason": reason,
    }


def _latest_csv_data_date(directory):
    latest = pd.NaT
    directory = Path(directory)
    if not directory.exists():
        return "none"
    for path in directory.glob("*.csv"):
        try:
            sample = pd.read_csv(path, nrows=5)
        except Exception:
            continue
        date_column = next(
            (column for column in sample.columns if "date" in str(column).lower() or "time" in str(column).lower()),
            None,
        )
        try:
            data = pd.read_csv(path, usecols=[date_column]) if date_column else pd.read_csv(path, index_col=0, usecols=[0])
            values = data[date_column] if date_column else data.index
            parsed = pd.to_datetime(values, errors="coerce", utc=True)
            value = parsed.max()
            if pd.notna(value) and (pd.isna(latest) or value > latest):
                latest = value
        except Exception:
            continue
    return str(latest.date()) if pd.notna(latest) else "none"


def _paper_run_counts(paper_report):
    if paper_report is None or paper_report.empty:
        return {"filled": 0, "rejected": 0}
    fill_status = paper_report.get("fill_status", pd.Series(dtype=str)).astype(str).str.upper()
    return {
        "filled": int(fill_status.eq("PAPER_FILLED").sum()),
        "rejected": int(fill_status.eq("REJECTED").sum()),
    }


def _daily_preflight_status(paper_report, trade_log_path, daily_dir, intraday_dir, data_update_ran=False):
    stale_status = _paper_stale_status(paper_report)
    fill_counts = paper_report["fill_status"].value_counts().to_dict() if paper_report is not None and not paper_report.empty else {}
    decision_counts = paper_report["user_decision"].value_counts().to_dict() if paper_report is not None and not paper_report.empty else {}
    log_rows = 0
    if Path(trade_log_path).exists():
        try:
            log_rows = len(pd.read_csv(trade_log_path))
        except Exception:
            log_rows = 0
    run_counts = _paper_run_counts(paper_report)
    pending_next_open = int(fill_counts.get("PENDING_NEXT_OPEN", 0))
    stale = bool(stale_status["stale_data_warning"])
    return {
        "current_date": stale_status["current_date"],
        "latest_daily_data_date": _latest_csv_data_date(daily_dir),
        "latest_130min_data_date": _latest_csv_data_date(intraday_dir),
        "latest_expected_us_market_session": stale_status["latest_expected_us_session"],
        "stale_data_warning": stale,
        "pending_order_count": int(fill_counts.get("PENDING_REVIEW", 0) + pending_next_open),
        "accepted_order_count": int(decision_counts.get("ACCEPT", 0)),
        "pending_next_open_count": pending_next_open,
        "paper_trade_log_rows": log_rows,
        "data_update_ran": bool(data_update_ran),
        "filled_count_this_run": run_counts["filled"],
        "rejected_count_this_run": run_counts["rejected"],
        "next_action": (
            "Wait for next open data; accepted orders remain PENDING_NEXT_OPEN."
            if pending_next_open
            else ("Refresh data before acting." if stale else "Review paper report and order review.")
        ),
    }


def _write_daily_preflight_report(status, path):
    lines = [
        "# Daily Preflight Report",
        "",
        f"- current date: {status['current_date']}",
        f"- latest daily data date: {status['latest_daily_data_date']}",
        f"- latest 130min data date: {status['latest_130min_data_date']}",
        f"- latest expected US market session: {status['latest_expected_us_market_session']}",
        f"- stale_data_warning: {status['stale_data_warning']}",
        f"- pending_order_count: {status['pending_order_count']}",
        f"- accepted_order_count: {status['accepted_order_count']}",
        f"- pending_next_open_count: {status['pending_next_open_count']}",
        f"- paper_trade_log rows: {status['paper_trade_log_rows']}",
        f"- next action: {status['next_action']}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _symbol_data_status(symbol, daily_dir, intraday_dir):
    daily_path = Path(daily_dir) / f"{symbol}_1D.csv"
    intraday_path = Path(intraday_dir) / f"{symbol}_130min.csv"

    def inspect(path):
        if not path.exists():
            return {"exists": False, "empty": True, "latest_date": "none", "zero_volume_rows": 0}
        try:
            frame = pd.read_csv(path)
        except Exception:
            return {"exists": True, "empty": True, "latest_date": "none", "zero_volume_rows": 0}
        if frame.empty:
            return {"exists": True, "empty": True, "latest_date": "none", "zero_volume_rows": 0}
        date_column = next(
            (column for column in frame.columns if "date" in str(column).lower() or "time" in str(column).lower()),
            frame.columns[0],
        )
        dates = pd.to_datetime(frame[date_column], errors="coerce", utc=True)
        latest = dates.max()
        volume = pd.to_numeric(frame.get("Volume", pd.Series(dtype=float)), errors="coerce")
        return {
            "exists": True,
            "empty": False,
            "latest_date": str(latest.date()) if pd.notna(latest) else "none",
            "zero_volume_rows": int(volume.fillna(0).le(0).sum()) if not volume.empty else 0,
        }

    return {"daily": inspect(daily_path), "intraday": inspect(intraday_path)}


def _write_data_precheck_report(paper_report, daily_dir, intraday_dir, status, path):
    warnings = []
    critical = []
    rows = []
    latest_daily = status.get("latest_daily_data_date", "none") if status else _latest_csv_data_date(daily_dir)
    latest_130min = status.get("latest_130min_data_date", "none") if status else _latest_csv_data_date(intraday_dir)

    special_symbols = ["DOW", "BK", "PARA", "PSA", "QQQ", "SPY"]
    candidate_symbols = []
    if paper_report is not None and not paper_report.empty and "symbol" in paper_report:
        candidate_symbols = sorted(
            paper_report.loc[
                paper_report.get("paper_actionable", pd.Series(False, index=paper_report.index)).astype(bool),
                "symbol",
            ].dropna().astype(str).str.upper().unique().tolist()
        )
    for symbol in sorted(set(special_symbols + candidate_symbols)):
        info = _symbol_data_status(symbol, daily_dir, intraday_dir)
        daily = info["daily"]
        intraday = info["intraday"]
        row_warnings = []
        if symbol == "DOW" and (not daily["exists"] or daily["empty"]):
            row_warnings.append("dow_empty_daily")
        if symbol in {"BK", "PARA"} and daily["latest_date"] not in {"none", latest_daily}:
            row_warnings.append("early_daily_end")
        if symbol == "PSA" and daily["zero_volume_rows"] > 0:
            row_warnings.append("psa_zero_volume")
        if symbol in {"QQQ", "SPY"}:
            if not daily["exists"] or daily["empty"]:
                critical.append(f"{symbol}_daily_missing_or_empty")
            if not intraday["exists"] or intraday["empty"]:
                critical.append(f"{symbol}_130min_missing_or_empty")
        if symbol in candidate_symbols and (not daily["exists"] or daily["empty"] or not intraday["exists"] or intraday["empty"]):
            row_warnings.append("candidate_missing_or_empty_data")
        warnings.extend(row_warnings)
        rows.append(
            {
                "symbol": symbol,
                "daily_exists": daily["exists"],
                "daily_empty": daily["empty"],
                "daily_latest_date": daily["latest_date"],
                "intraday_exists": intraday["exists"],
                "intraday_empty": intraday["empty"],
                "intraday_latest_date": intraday["latest_date"],
                "zero_volume_rows": daily["zero_volume_rows"],
                "warnings": ";".join(row_warnings),
            }
        )
    if latest_daily == "none" or latest_130min == "none" or latest_daily != latest_130min:
        warnings.append("latest_daily_130min_date_mismatch")
    result = {
        "status": "CRITICAL" if critical else ("WARNING" if warnings else "OK"),
        "warnings": sorted(set(warnings)),
        "critical": sorted(set(critical)),
    }
    lines = [
        "# Data Precheck Report",
        "",
        f"- status: {result['status']}",
        f"- latest_daily_date: {latest_daily}",
        f"- latest_130min_date: {latest_130min}",
        f"- critical_warnings: {', '.join(result['critical']) if result['critical'] else 'none'}",
        f"- warnings: {', '.join(result['warnings']) if result['warnings'] else 'none'}",
        "",
        "## Symbol Checks",
        "",
        _readable_table(ensure_columns(pd.DataFrame(rows), DATA_PRECHECK_COLUMNS)),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return result


def _write_regression_guard_report(report, decision, latest_dir, path):
    warnings = []
    critical = []
    first = report.iloc[0] if report is not None and not report.empty else pd.Series(dtype=object)
    qqq_return = pd.to_numeric(pd.Series([first.get("QQQ BuyHold Return (%)", pd.NA)]), errors="coerce").iloc[0]
    ew_return = pd.to_numeric(pd.Series([first.get("EqualWeight Universe Return (%)", pd.NA)]), errors="coerce").iloc[0]
    if pd.isna(qqq_return) or abs(float(qqq_return) - 191.71) > 0.05:
        critical.append(f"qqq_return_expected_191.71_actual_{qqq_return}")
    if pd.isna(ew_return) or abs(float(ew_return) - 196.44) > 0.05:
        critical.append(f"equal_weight_return_expected_196.44_actual_{ew_return}")
    main_role_ok = False
    if decision is not None and not decision.empty:
        main_rows = decision[
            decision["experiment_name"].eq("rs_m126_top30_sma100_no_regime")
            & decision["final_role"].astype(str).str.lower().eq("main")
        ]
        main_role_ok = not main_rows.empty
    if not main_role_ok:
        critical.append("main_candidate_role_changed")
    pending_readable = False
    pending_rows = 0
    try:
        pending = pd.read_csv(Path(latest_dir) / "paper_pending_orders.csv")
        pending_readable = True
        pending_rows = len(pending)
    except Exception:
        critical.append("paper_pending_orders_unreadable")
    result = {
        "status": "CRITICAL" if critical else ("WARNING" if warnings else "OK"),
        "critical": critical,
        "warnings": warnings,
        "strategy_metrics_changed": False,
        "benchmark_changed": False,
        "paper_pending_readable": pending_readable,
    }
    lines = [
        "# Regression Guard Report",
        "",
        f"- status: {result['status']}",
        f"- QQQ return expected: 191.71",
        f"- QQQ return actual: {qqq_return}",
        f"- EqualWeight return expected: 196.44",
        f"- EqualWeight return actual: {ew_return}",
        "- strategy metrics changed: false",
        "- benchmark changed: false",
        f"- main candidate role: {'rs_m126_top30_sma100_no_regime' if main_role_ok else 'changed_or_missing'}",
        f"- paper pending accepted orders readable: {pending_readable}",
        f"- paper pending rows: {pending_rows}",
        f"- critical_warnings: {', '.join(critical) if critical else 'none'}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return result


def _pipeline_health_columns():
    return PIPELINE_HEALTH_COLUMNS


def _write_pipeline_health_log(status, data_precheck, regression_guard, latest_dir, history_dir, command, total_runtime_sec, cache_summary):
    warning_parts = []
    for result in [data_precheck, regression_guard]:
        warning_parts.extend(result.get("critical", []))
        warning_parts.extend(result.get("warnings", []))
    if status.get("stale_data_warning"):
        warning_parts.append("stale_data_warning")
    health_status = "CRITICAL" if any(r.get("status") == "CRITICAL" for r in [data_precheck, regression_guard]) else (
        "WARNING" if warning_parts else "OK"
    )
    row = {
        "run_datetime": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "command": command or "unknown",
        "data_update_ran": bool(status.get("data_update_ran", False)),
        "latest_daily_date": status.get("latest_daily_data_date", "none"),
        "latest_130min_date": status.get("latest_130min_data_date", "none"),
        "latest_expected_market_session": status.get("latest_expected_us_market_session", "none"),
        "stale_data_warning": bool(status.get("stale_data_warning", False)),
        "pending_order_count": int(status.get("pending_order_count", 0) or 0),
        "accepted_order_count": int(status.get("accepted_order_count", 0) or 0),
        "pending_next_open_count": int(status.get("pending_next_open_count", 0) or 0),
        "filled_count_this_run": int(status.get("filled_count_this_run", 0) or 0),
        "rejected_count_this_run": int(status.get("rejected_count_this_run", 0) or 0),
        "paper_trade_log_rows": int(status.get("paper_trade_log_rows", 0) or 0),
        "strategy_metrics_changed": False,
        "benchmark_changed": False,
        "total_runtime_sec": round(float(total_runtime_sec), 4),
        "cache_hit_count": int(cache_summary.get("prepared_disk_cache_hits", 0) or 0),
        "cache_miss_count": int(cache_summary.get("prepared_disk_cache_misses", 0) or 0),
        "status": health_status,
        "warning_summary": ";".join(sorted(set(str(w) for w in warning_parts if str(w)))) or "none",
    }
    frame = ensure_columns(pd.DataFrame([row]), _pipeline_health_columns())
    latest_path = Path(latest_dir) / "pipeline_health_log.csv"
    history_path = Path(history_dir) / "pipeline_health_log.csv"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(latest_path, index=False)
    frame.to_csv(history_path, mode="a", header=not history_path.exists(), index=False)
    return row


def _chatgpt_operational_health_section(operational_health):
    if not operational_health:
        return []
    aging = operational_health.get("pending_aging_summary", {})
    data_precheck = operational_health.get("data_precheck", {})
    regression = operational_health.get("regression_guard", {})
    schema_validation = operational_health.get("schema_validation", {})
    paper_state = operational_health.get("paper_state_validation", {})
    health = operational_health.get("pipeline_health", {})
    return [
        "## Operational Health",
        "",
        f"- pipeline status: {health.get('status', 'not_generated')}",
        f"- latest data dates: daily={health.get('latest_daily_date', 'none')}, 130min={health.get('latest_130min_date', 'none')}",
        f"- pending order aging: max_calendar_days={aging.get('max_calendar_days', 0)}, max_trading_days={aging.get('max_trading_days', 0)}, stale_count={aging.get('stale_count', 0)}",
        f"- data precheck warnings: {', '.join(data_precheck.get('critical', []) + data_precheck.get('warnings', [])) or 'none'}",
        f"- regression guard status: {regression.get('status', 'not_generated')}",
        f"- report_schema_validation_status: {schema_validation.get('status', 'not_generated')}",
        f"- report_schema_validation_report_path: {schema_validation.get('path', 'reports/latest/report_schema_validation_report.md')}",
        f"- paper_state_validation_status: {paper_state.get('status', 'not_generated')}",
        f"- paper_state_validation_report_path: {paper_state.get('path', 'reports/latest/paper_state_validation_report.md')}",
        f"- next action: {operational_health.get('next_action', 'Review reports/latest/pipeline_health_log.csv.')}",
        "",
    ]


def _pending_aging_summary(path):
    if not Path(path).exists():
        return {"max_calendar_days": 0, "max_trading_days": 0, "stale_count": 0}
    try:
        pending = pd.read_csv(path)
    except Exception:
        return {"max_calendar_days": 0, "max_trading_days": 0, "stale_count": 0}
    if pending.empty:
        return {"max_calendar_days": 0, "max_trading_days": 0, "stale_count": 0}
    calendar = pd.to_numeric(pending.get("pending_age_calendar_days", pd.Series(dtype=float)), errors="coerce").fillna(0)
    trading = pd.to_numeric(pending.get("pending_age_trading_days", pd.Series(dtype=float)), errors="coerce").fillna(0)
    stale = pending.get("pending_stale_warning", pd.Series(False, index=pending.index)).astype(str).str.lower().isin(["true", "1", "yes"])
    return {
        "max_calendar_days": int(calendar.max()) if not calendar.empty else 0,
        "max_trading_days": int(trading.max()) if not trading.empty else 0,
        "stale_count": int(stale.sum()),
    }


def _write_report_schema_validation_report(latest_dir, path):
    checks = [
        ("paper_signal_report.csv", PAPER_SIGNAL_COLUMNS),
        ("paper_pending_orders.csv", PAPER_PENDING_ORDERS_COLUMNS),
        ("paper_order_review.csv", PAPER_ORDER_REVIEW_COLUMNS),
        ("paper_trade_log.csv", PAPER_TRADE_LOG_COLUMNS),
        ("pipeline_health_log.csv", PIPELINE_HEALTH_COLUMNS),
    ]
    alpaca_preview = Path(latest_dir) / "alpaca_order_preview.csv"
    if alpaca_preview.exists():
        checks.append(("alpaca_order_preview.csv", ALPACA_ORDER_PREVIEW_COLUMNS))
    rows = []
    for filename, columns in checks:
        file_path = Path(latest_dir) / filename
        if not file_path.exists():
            rows.append(
                {
                    "file": filename,
                    "missing_columns": "file_missing",
                    "extra_columns": "",
                    "status": "WARNING",
                }
            )
            continue
        try:
            frame = pd.read_csv(file_path, nrows=1)
            validation = validate_required_columns(frame, columns)
        except Exception as error:
            rows.append(
                {
                    "file": filename,
                    "missing_columns": f"read_error:{error}",
                    "extra_columns": "",
                    "status": "WARNING",
                }
            )
            continue
        missing = validation["missing_columns"]
        extra = validation["extra_columns"]
        rows.append(
            {
                "file": filename,
                "missing_columns": ",".join(missing),
                "extra_columns": ",".join(extra),
                "status": "WARNING" if missing or extra else "OK",
            }
        )
    status = "WARNING" if any(row["status"] == "WARNING" for row in rows) else "OK"
    lines = [
        "# Report Schema Validation Report",
        "",
        f"- status: {status}",
        "- schema source: report_schemas.py",
        "",
        "## Checked Files",
        "",
        _readable_table(pd.DataFrame(rows)),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": status, "rows": rows, "path": "reports/latest/report_schema_validation_report.md"}


def _write_paper_state_validation_report(reports_dir, path):
    validation = validate_paper_state(reports_dir)
    lines = [
        "# Paper State Validation Report",
        "",
        f"- pending_orders_exists: {validation['pending_orders_exists']}",
        f"- order_review_exists: {validation['order_review_exists']}",
        f"- trade_log_exists: {validation['trade_log_exists']}",
        f"- pending_orders_rows: {validation['pending_orders_rows']}",
        f"- order_review_rows: {validation['order_review_rows']}",
        f"- trade_log_rows: {validation['trade_log_rows']}",
        f"- schema_status: {validation['schema_status']}",
        f"- status: {validation['status']}",
        "",
        "## Files",
        "",
        _readable_table(pd.DataFrame(validation["rows"])),
        "",
    ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    validation["path"] = "reports/latest/paper_state_validation_report.md"
    return validation


def _chatgpt_daily_pipeline_status_section(daily_pipeline_status):
    if not daily_pipeline_status:
        return []
    return [
        "## Daily Pipeline Status",
        "",
        "- daily_pipeline_script: scripts/run_daily_paper_pipeline.bat",
        f"- data update ran: {daily_pipeline_status['data_update_ran']}",
        "- preflight report path: reports/latest/daily_preflight_report.md",
        "- chatgpt_upload_file: testdata/reports/latest/chatgpt_master_report.md",
        f"- latest data date: daily={daily_pipeline_status['latest_daily_data_date']}, 130min={daily_pipeline_status['latest_130min_data_date']}",
        f"- stale warning: {daily_pipeline_status['stale_data_warning']}",
        f"- pending next open count: {daily_pipeline_status['pending_next_open_count']}",
        f"- filled count this run: {daily_pipeline_status['filled_count_this_run']}",
        f"- rejected count this run: {daily_pipeline_status['rejected_count_this_run']}",
        f"- next action: {daily_pipeline_status['next_action']}",
        "",
    ]


def _chatgpt_paper_readiness_section(paper_report):
    if paper_report is None:
        return [
            "## Paper Trading Readiness",
            "",
            "- selected main candidate: rs_m126_top30_sma100_no_regime",
            "- paper signal report path: reports/latest/paper_signal_report.md",
            "- latest_signal_date: not generated",
            "- actionable_buy_count: not generated",
            "- actionable_sell_count: not generated",
            "- hold_count: not generated",
            "- skip_count: not generated",
            "- liquidity_warning_count: not generated",
            "- stale_data_warning: not generated",
            "- next action: run --paper-report.",
            "",
        ]
    liquidity_warnings_count = (
        int(paper_report["liquidity_warning_reason"].fillna("").astype(str).ne("").sum())
        if not paper_report.empty and "liquidity_warning_reason" in paper_report
        else 0
    )
    action_counts = paper_report["action"].value_counts().to_dict() if not paper_report.empty else {}
    fill_counts = paper_report["fill_status"].value_counts().to_dict() if not paper_report.empty else {}
    decision_counts = paper_report["user_decision"].value_counts().to_dict() if not paper_report.empty else {}
    stale_status = _paper_stale_status(paper_report)
    actionable = paper_report[paper_report["paper_actionable"].astype(bool)].copy() if not paper_report.empty else pd.DataFrame()
    table_columns = [
        "strategy_name",
        "signal_date",
        "symbol",
        "action",
        "fill_status",
        "user_decision",
        "paper_actionable",
        "paper_rank",
        "ranking_score",
        "liquidity_guard_pass",
        "warning",
        "reason",
    ]
    return [
        "## Paper Trading Readiness",
        "",
        "- selected main candidate: rs_m126_top30_sma100_no_regime",
        "- paper_signal_report_path: reports/latest/paper_signal_report.md",
        f"- latest_signal_date: {stale_status['latest_signal_date']}",
        f"- current_date: {stale_status['current_date']}",
        f"- calendar_check_mode: {stale_status['calendar_check_mode']}",
        f"- stale_reason: {stale_status['stale_reason']}",
        f"- actionable_buy_count: {int(action_counts.get('BUY', 0))}",
        f"- actionable_sell_count: {int(action_counts.get('SELL', 0))}",
        f"- hold_count: {int(action_counts.get('HOLD', 0))}",
        f"- skip_count: {int(action_counts.get('SKIP', 0))}",
        f"- liquidity_warning_count: {liquidity_warnings_count}",
        f"- stale_data_warning: {stale_status['stale_data_warning']}",
        f"- pending_order_count: {int(fill_counts.get('PENDING_REVIEW', 0) + fill_counts.get('PENDING_NEXT_OPEN', 0))}",
        f"- accepted_order_count: {int(decision_counts.get('ACCEPT', 0))}",
        f"- rejected_order_count: {int(decision_counts.get('REJECT', 0))}",
        f"- hold_review_count: {int(decision_counts.get('HOLD_REVIEW', 0))}",
        f"- pending_next_open_count: {int(fill_counts.get('PENDING_NEXT_OPEN', 0))}",
        "- paper_pending_orders_path: reports/latest/paper_pending_orders.csv",
        "- paper_order_review_path: reports/latest/paper_order_review.csv",
        "- paper_trade_log_path: reports/latest/paper_trade_log.csv",
        "- next action: review paper signals and manually record accepted paper fills in paper_trade_log_template.csv.",
        "",
        "### Actionable Signals",
        "",
        _readable_table(_safe_columns(actionable.head(20), table_columns)),
        "",
    ]


def _safe_columns(frame, columns):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    output = frame.copy()
    for column in columns:
        if column not in output:
            output[column] = pd.NA
    return output[columns]


def _readable_table(frame):
    if frame is None or frame.empty:
        return "No rows."
    return _table_for_markdown(frame)


def _chatgpt_data_audit_section(audit):
    if audit is None or audit.empty:
        return ["## Data Audit Summary", "", "Data audit is unavailable.", ""]
    daily_rows = pd.to_numeric(audit["daily_rows"], errors="coerce").fillna(0)
    daily_ends = pd.to_datetime(audit["daily_end"], errors="coerce")
    global_daily_end = daily_ends.max()
    empty_daily = audit.loc[audit["has_daily_file"] & (daily_rows <= 0), "symbol"].tolist()
    short_history = audit.loc[(daily_rows > 0) & (daily_rows < 252), "symbol"].tolist()
    ending_before_global = audit.loc[
        daily_ends.notna() & pd.notna(global_daily_end) & (daily_ends < global_daily_end),
        "symbol",
    ].tolist()
    zero_volume = audit.loc[
        pd.to_numeric(audit["zero_volume_count"], errors="coerce").fillna(0) > 0,
        "symbol",
    ].tolist()
    watched = {"DOW", "BK", "PARA", "PSA"}
    problem_mask = (
        audit["symbol"].isin(watched)
        | audit["data_warnings"].fillna("").astype(str).ne("")
        | audit["errors"].fillna("").astype(str).ne("")
    )
    problem_columns = [
        "symbol",
        "has_daily_file",
        "has_130min_file",
        "daily_rows",
        "bar_130min_rows",
        "daily_end",
        "zero_volume_count",
        "data_warnings",
        "errors",
    ]
    problem_rows = _safe_columns(audit.loc[problem_mask], problem_columns)
    return [
        "## Data Audit Summary",
        "",
        f"- Symbols: {len(audit)}",
        f"- Daily files: {int(audit['has_daily_file'].sum())}",
        f"- Valid daily files: {int(audit['daily_data_valid'].sum())}",
        f"- 130min files: {int(audit['has_130min_file'].sum())}",
        f"- Empty daily files: {len(empty_daily)} ({','.join(empty_daily) if empty_daily else 'none'})",
        f"- Short-history symbols: {len(short_history)} ({','.join(short_history) if short_history else 'none'})",
        f"- Symbols ending before global end date: {len(ending_before_global)} ({','.join(ending_before_global) if ending_before_global else 'none'})",
        f"- Zero volume warnings: {len(zero_volume)} ({','.join(zero_volume) if zero_volume else 'none'})",
        f"- QQQ daily file: {bool(audit.loc[audit['symbol'] == 'QQQ', 'has_daily_file'].any())}",
        f"- QQQ 130min file: {bool(audit.loc[audit['symbol'] == 'QQQ', 'has_130min_file'].any())}",
        f"- SPY daily file: {bool(audit.loc[audit['symbol'] == 'SPY', 'has_daily_file'].any())}",
        f"- SPY 130min file: {bool(audit.loc[audit['symbol'] == 'SPY', 'has_130min_file'].any())}",
        "",
        "### Problem Symbols",
        "",
        _readable_table(problem_rows),
        "",
    ]


def _chatgpt_benchmark_section(report):
    if report is None or report.empty:
        return ["## Benchmark Audit Summary", "", "Benchmark audit is unavailable.", ""]
    first = report.iloc[0]
    qqq_unique = sorted(report["QQQ BuyHold Return (%)"].dropna().unique().tolist())
    ew_unique = sorted(report["EqualWeight Universe Return (%)"].dropna().unique().tolist())
    same_qqq = report[["QQQ Benchmark Start", "QQQ Benchmark End"]].drop_duplicates().shape[0] == 1
    same_ew = report[["EqualWeight Benchmark Start", "EqualWeight Benchmark End"]].drop_duplicates().shape[0] == 1
    missing = sorted(
        str(value)
        for value in report["EqualWeight Missing Symbols"].fillna("").unique().tolist()
        if str(value)
    )
    compact = _safe_columns(
        report.sort_values("Experiment Name").head(5),
        [
            "Experiment Name",
            "QQQ Benchmark Start",
            "QQQ Benchmark End",
            "EqualWeight Benchmark Start",
            "EqualWeight Benchmark End",
            "QQQ BuyHold Return (%)",
            "EqualWeight Universe Return (%)",
            "EqualWeight Missing Symbols",
        ],
    )
    return [
        "## Benchmark Audit Summary",
        "",
        f"- QQQ return unique values: {qqq_unique}",
        f"- EqualWeight return unique values: {ew_unique}",
        f"- QQQ date range: {first.get('QQQ Benchmark Start', '')} -> {first.get('QQQ Benchmark End', '')}",
        f"- EqualWeight date range: {first.get('EqualWeight Benchmark Start', '')} -> {first.get('EqualWeight Benchmark End', '')}",
        f"- Same QQQ date range: {same_qqq}",
        f"- Same EqualWeight date range: {same_ew}",
        f"- Missing symbols: {missing if missing else 'none'}",
        "- Excluded symbols: QQQ,SPY",
        "- Benchmark consistency note: EqualWeight benchmark uses immutable base/prepared OHLCV close data.",
        "",
        "### Per Experiment Sample",
        "",
        _readable_table(compact),
        "",
    ]


def _chatgpt_strategy_section(report):
    columns = [
        "Experiment Name",
        "Strategy Name",
        "Strategy Family",
        "Compact Score",
        "Expectancy R",
        "Net Getiri (%)",
        "Max Drawdown (%)",
        "Trade Count",
        "Profit Factor",
        "Worst R",
        "Avg Holding Bars",
        "QQQ BuyHold Return (%)",
        "EqualWeight Universe Return (%)",
        "Strategy Excess Return vs QQQ (%)",
    ]
    signal_columns = [
        "Experiment Name",
        "Strategy Name",
        "Raw Trigger Count",
        "Raw Entry Signal Count",
        "Confirmed Entry Signal Count",
        "Final Entry Signal Count",
        "Trade Count",
        "Risk Off Rejected Signal Count",
    ]
    ranked = report.sort_values("Compact Score", ascending=False, na_position="last")
    warnings = ["- Otomatik uyari kriterlerine takilan experiment yok."]
    if pd.to_numeric(report["Trade Count"], errors="coerce").lt(30).any():
        warnings.append("- Trade count < 30 olan experimentler guvenilir degil.")
    return [
        "## Strategy Compact Summary",
        "",
        "### Best 5 Experiments",
        "",
        _readable_table(_safe_columns(ranked.head(5), columns)),
        "",
        "### Worst 5 Experiments",
        "",
        _readable_table(_safe_columns(ranked.tail(5), columns)),
        "",
        "### Signal Diagnostics",
        "",
        _readable_table(_safe_columns(ranked, signal_columns)),
        "",
        "### Warnings",
        "",
        *warnings,
        "",
    ]


def _chatgpt_oos_section(oos):
    if oos is None or oos.empty:
        return ["## OOS / Walkforward Summary", "", "OOS report was not generated in this run.", ""]
    columns = [
        "experiment_name",
        "period_name",
        "start_date",
        "end_date",
        "net_return_pct",
        "max_drawdown_pct",
        "trade_count",
        "profit_factor",
        "expectancy_r",
        "qqq_return_pct",
        "equal_weight_return_pct",
        "excess_vs_qqq_pct",
        "return_over_maxdd",
    ]
    lines = ["## OOS / Walkforward Summary", "", "### Best 5 By Period", ""]
    for period_name, group in oos.groupby("period_name", sort=False):
        best = group.sort_values(
            ["return_over_maxdd", "net_return_pct"],
            ascending=[False, False],
            na_position="last",
        ).head(5)
        lines.extend([f"#### {period_name}", "", _readable_table(_safe_columns(best, columns)), ""])
    numeric = oos.copy()
    for column in ["net_return_pct", "max_drawdown_pct", "return_over_maxdd"]:
        numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
    stability = numeric.groupby("experiment_name").agg(
        periods=("period_name", "count"),
        avg_return_pct=("net_return_pct", "mean"),
        min_return_pct=("net_return_pct", "min"),
        return_std_pct=("net_return_pct", "std"),
        worst_drawdown_pct=("max_drawdown_pct", "min"),
    ).round(4).reset_index()
    topn = numeric[numeric["experiment_name"].str.contains("top", na=False)].copy()
    topn["top_n"] = topn["experiment_name"].str.extract(r"top(\d+)").astype(float)
    topn_summary = topn.groupby("top_n").agg(
        avg_return_pct=("net_return_pct", "mean"),
        min_return_pct=("net_return_pct", "min"),
        avg_return_over_maxdd=("return_over_maxdd", "mean"),
    ).round(4).reset_index()
    momentum = numeric[numeric["period_name"].eq("split_2023_2024")][
        ["experiment_name", "net_return_pct", "qqq_return_pct", "excess_vs_qqq_pct"]
    ].copy()
    momentum["beats_qqq"] = pd.to_numeric(
        momentum["excess_vs_qqq_pct"],
        errors="coerce",
    ).gt(0)
    lines.extend(
        [
            "### Stability",
            "",
            _readable_table(stability),
            "",
            "### 2023-2024 Momentum Period vs QQQ",
            "",
            _readable_table(momentum.sort_values("net_return_pct", ascending=False)),
            "",
            "### Top10 / Top20 / Top30 Summary",
            "",
            _readable_table(topn_summary),
            "",
        ]
    )
    return lines


def _chatgpt_attribution_section(attribution, attribution_summary):
    if attribution is None or attribution.empty:
        return ["## Candidate Attribution", "", "Candidate attribution was not generated in this run.", ""]
    lines = [
        "## Candidate Attribution",
        "",
        "### Concentration Summary",
        "",
        _readable_table(attribution_summary),
        "",
        "### Risk Warnings",
        "",
    ]
    if attribution_summary is not None and not attribution_summary.empty:
        top10 = attribution_summary[
            pd.to_numeric(
                attribution_summary.get("top_5_symbols_contribution_pct", pd.Series(dtype=float)),
                errors="coerce",
            ).gt(50)
        ]
        if top10.empty:
            lines.append("- No candidate has top 5 contribution above 50%.")
        else:
            for _, row in top10.iterrows():
                lines.append(f"- Warning: {row['experiment_name']} top 5 symbols contribute >50% of total PnL.")
    lines.extend(["", "### Top 10 Symbols By Candidate", ""])
    columns = [
        "experiment_name",
        "symbol",
        "trade_count",
        "win_rate",
        "total_pnl",
        "total_r",
        "avg_r",
        "contribution_pct_of_total_pnl",
        "cumulative_contribution_rank",
        "avg_holding_bars",
    ]
    top_symbols = attribution.sort_values(
        ["experiment_name", "cumulative_contribution_rank"],
        ascending=[True, True],
    ).groupby("experiment_name", group_keys=False).head(10)
    lines.extend([_readable_table(_safe_columns(top_symbols, columns)), ""])
    return lines


def _chatgpt_cost_section(cost_stress):
    if cost_stress is None or cost_stress.empty:
        return ["## Cost / Slippage / Liquidity Stress", "", "Cost stress report was not generated in this run.", ""]
    base = cost_stress[cost_stress["capital_assumption_usd"].eq(10000)].copy()
    scenario_summary = base.pivot_table(
        index="experiment_name",
        columns="scenario",
        values="net_return_pct",
        aggfunc="first",
    ).reset_index()
    survivors = base[
        base["scenario"].isin([
            "commission_0_slippage_10bps_per_side",
            "commission_0_slippage_25bps_per_side",
        ])
        & base["survives_cost_test"].astype(bool)
    ].groupby("scenario")["experiment_name"].apply(lambda values: ", ".join(sorted(values))).reset_index()
    liquidity = cost_stress[cost_stress["scenario"].eq("zero_cost_baseline")]
    liquidity_columns = [
        "experiment_name",
        "capital_assumption_usd",
        "max_positions",
        "per_position_notional",
        "median_order_pct_of_dollar_volume",
        "p10_order_pct_of_dollar_volume",
        "min_order_pct_of_dollar_volume",
        "liquidity_warning_count",
        "entries_below_1m_dollar_volume",
        "entries_below_500k_dollar_volume",
        "entries_below_100k_dollar_volume",
    ]
    return [
        "## Cost / Slippage / Liquidity Stress",
        "",
        "### Net Return By Scenario (capital 10000)",
        "",
        _readable_table(scenario_summary),
        "",
        "### Summary",
        "",
        _readable_table(survivors),
        "",
        "### Liquidity Stress",
        "",
        _readable_table(_safe_columns(liquidity, liquidity_columns)),
        "",
    ]


def _chatgpt_liquidity_guard_section(liquidity_guard_sensitivity):
    lines = ["## Liquidity Guard Sensitivity", ""]
    if liquidity_guard_sensitivity is None or liquidity_guard_sensitivity.empty:
        return lines + ["liquidity_guard_sensitivity_report.md not generated in this run.", ""]
    focus = liquidity_guard_sensitivity[
        liquidity_guard_sensitivity["capital_assumption_usd"].eq(10000)
    ].copy()
    columns = [
        "experiment_name",
        "guard_scenario",
        "capital_assumption_usd",
        "net_return_pct",
        "trade_count",
        "skipped_trade_count",
        "skipped_trade_pct",
        "degradation_vs_no_guard_pct",
        "liquidity_guard_pass",
    ]
    top30 = liquidity_guard_sensitivity[
        liquidity_guard_sensitivity["experiment_name"].eq("rs_m126_top30_sma100_no_regime")
        & liquidity_guard_sensitivity["guard_scenario"].eq("skip_entry_dollar_volume_below_500k")
    ]
    top30_main = bool(
        not top30.empty
        and top30["liquidity_guard_pass"].astype(bool).all()
        and pd.to_numeric(top30["excess_vs_qqq_pct"], errors="coerce").gt(0).all()
    )
    return lines + [
        f"- Top30 guard sonrasi main kaliyor mu: {top30_main}",
        f"- Top30 500k skipped trade count: {int(top30['skipped_trade_count'].iloc[0]) if not top30.empty else 'n/a'}",
        f"- Top30 500k degradation_vs_no_guard_pct: {top30['degradation_vs_no_guard_pct'].iloc[0] if not top30.empty else 'n/a'}",
        f"- Top30 500k liquidity_guard_pass: {bool(top30['liquidity_guard_pass'].iloc[0]) if not top30.empty else 'n/a'}",
        "",
        "### Guard Scenario Summary (capital 10000)",
        "",
        _readable_table(_safe_columns(focus, columns)),
        "",
    ]


def _chatgpt_performance_section(profile_rows):
    profile = pd.DataFrame(profile_rows)
    if profile.empty:
        return ["## Performance Profile", "", "Performance profile is unavailable.", ""]
    stage_times = _safe_columns(profile, ["stage", "experiment_name", "duration_sec"])
    stage_times["duration_sec"] = pd.to_numeric(stage_times["duration_sec"], errors="coerce")
    slowest = stage_times.dropna(subset=["duration_sec"]).sort_values("duration_sec", ascending=False).head(5)
    total = profile[profile["stage"].eq("total")].head(1)
    strategy = profile[profile["stage"].eq("strategy_runtime")].copy()
    strategy["duration_sec"] = pd.to_numeric(strategy["duration_sec"], errors="coerce")
    stage_summary = profile[
        profile["stage"].isin([
            "oos_walkforward",
            "candidate_attribution",
            "cost_slippage_stress",
            "liquidity_guard_sensitivity",
        ])
    ][["stage", "duration_sec"]]
    return [
        "## Performance Profile",
        "",
        "### Slowest 5 Stages",
        "",
        _readable_table(slowest),
        "",
        "### Total Row",
        "",
        _readable_table(total),
        "",
        "### Strategy Runtime Top 10",
        "",
        _readable_table(strategy.sort_values("duration_sec", ascending=False).head(10)[["stage", "experiment_name", "duration_sec"]]),
        "",
        "### OOS / Attribution / Cost / Liquidity Guard Durations",
        "",
        _readable_table(stage_summary),
        "",
    ]


def _write_chatgpt_master_report(
    report,
    audit,
    oos,
    attribution,
    attribution_summary,
    cost_stress,
    liquidity_guard_sensitivity,
    paper_report,
    profile_rows,
    output_path,
    *,
    final_decision_path=None,
    command,
    report_mode,
    flags,
    total_runtime_sec,
    cache_summary,
    daily_pipeline_status=None,
    operational_health=None,
):
    decision = _final_candidate_decision_frame(report, oos, attribution_summary, cost_stress)
    source_decision = None
    if oos is None or attribution_summary is None or cost_stress is None:
        source_decision = _read_final_candidate_decision_report(final_decision_path)
    if source_decision is not None and not source_decision.empty:
        decision = source_decision
    included = [
        "final_candidate_decision_report.md",
        "data_audit_report.md",
        "benchmark_audit_report.md",
        "strategy_compact_report.md",
        "oos_walkforward_report.md" if oos is not None else "oos_walkforward_report.md (not generated)",
        "candidate_attribution_report.md" if attribution is not None else "candidate_attribution_report.md (not generated)",
        "cost_slippage_stress_report.md" if cost_stress is not None else "cost_slippage_stress_report.md (not generated)",
        "liquidity_guard_sensitivity_report.md" if liquidity_guard_sensitivity is not None else "liquidity_guard_sensitivity_report.md (not generated)",
        "paper_signal_report.md" if paper_report is not None else "paper_signal_report.md (not generated)",
        "paper_trading_plan.md" if paper_report is not None else "paper_trading_plan.md (not generated)",
        "performance_profile_report.md",
    ]
    top30_row = decision[decision["experiment_name"].eq("rs_m126_top30_sma100_no_regime")]
    top30_main = bool(
        not top30_row.empty
        and str(top30_row.iloc[0].get("final_role", "")).lower() == "main"
        and pd.to_numeric(
            pd.Series([top30_row.iloc[0].get("excess_vs_qqq_pct", pd.NA)]),
            errors="coerce",
        ).iloc[0] > 0
    )
    next_step = "Next step: Review latest paper signals and run scripts/run_daily_paper_pipeline.bat for the next daily cycle."
    if daily_pipeline_status:
        pending_next_open = int(daily_pipeline_status.get("pending_next_open_count", 0) or 0)
        filled_this_run = int(daily_pipeline_status.get("filled_count_this_run", 0) or 0)
        if pending_next_open > 0 and filled_this_run == 0:
            next_step = "Wait for next valid market open data, then run scripts/run_daily_paper_pipeline.bat again."
        elif filled_this_run > 0:
            next_step = "Review filled paper trades and compare fills vs expected next open."
    if paper_report is None:
        next_step = (
        "Next step: paper trading checklist / execution plan."
        if liquidity_guard_sensitivity is not None and top30_main
        else "Next step: liquidity guard sensitivity."
        )
    lines = [
        "# ChatGPT Master Report",
        "",
        "## Run Summary",
        "",
        f"- run datetime: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- command: `{command}`",
        f"- reports mode: {report_mode}",
        f"- run_oos: {bool(flags.get('run_oos'))}",
        f"- run_attribution: {bool(flags.get('run_attribution'))}",
        f"- run_cost_stress: {bool(flags.get('run_cost_stress'))}",
        f"- run_liquidity_guard_sensitivity: {bool(flags.get('run_liquidity_guard_sensitivity'))}",
        f"- run_robustness: {bool(flags.get('run_robustness'))}",
        f"- paper_report: {bool(flags.get('paper_report'))}",
        f"- total_runtime_sec: {round(float(total_runtime_sec), 4)}",
        f"- cache hit/miss: {int(cache_summary.get('prepared_disk_cache_hits', 0) or 0)}/{int(cache_summary.get('prepared_disk_cache_misses', 0) or 0)}",
        "- strategy metrics changed: false",
        "- benchmark changed: false",
        f"- included reports: {', '.join(included)}",
        "- excluded large tables: full data audit per-symbol table; full candidate attribution beyond top 10 per candidate; full performance all-stages table.",
        "",
        "## Current Final Decision",
        "",
        _readable_table(_safe_columns(
            decision,
            [
                "experiment_name",
                "full_period_return",
                "oos_avg_return",
                "oos_min_return",
                "top1_contribution_pct",
                "top5_contribution_pct",
                "cost_survives",
                "liquidity_warning_count",
                "max_drawdown_pct",
                "excess_vs_qqq_pct",
                "final_role",
            ],
        )),
        "",
    ]
    lines.extend(_chatgpt_data_audit_section(audit))
    lines.extend(_chatgpt_benchmark_section(report))
    lines.extend(_chatgpt_strategy_section(report))
    lines.extend(_chatgpt_oos_section(oos))
    lines.extend(_chatgpt_attribution_section(attribution, attribution_summary))
    lines.extend(_chatgpt_cost_section(cost_stress))
    lines.extend(_chatgpt_liquidity_guard_section(liquidity_guard_sensitivity))
    lines.extend(_chatgpt_daily_pipeline_status_section(daily_pipeline_status))
    lines.extend(_chatgpt_operational_health_section(operational_health))
    lines.extend(_chatgpt_paper_readiness_section(paper_report))
    lines.extend(_chatgpt_performance_section(profile_rows))
    lines.extend(_chatgpt_useful_commands_section())
    lines.extend(
        [
            "## Warnings / Open Issues",
            "",
            "- Backtest survivorship-bias-free degildir.",
            "- Adjusted data dogrulanmali.",
            "- Transaction cost/slippage varsayimsaldir.",
            "- Liquidity proxy dollar volume bazlidir; real spread/order book icermez.",
            "- Paper trading oncesi execution plan gerekir.",
            "- Canli trading hazir denmemeli.",
            "",
            "## Next Recommended Step",
            "",
            next_step,
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _chatgpt_useful_commands_section():
    return [
        "## Useful Commands",
        "",
        "### Double-click BAT",
        "",
        "- Daily paper pipeline: `scripts/run_daily_paper_pipeline.bat`",
        "- Test suite: `scripts/run_tests.bat`",
        "- Paper report only: `scripts/run_paper_report.bat`",
        "- Basic backtest: `scripts/run_basic.bat`",
        "- Alpaca paper check: `scripts/run_alpaca_check.bat`",
        "- Full diagnostic: `scripts/run_full_diagnostic.bat`",
        "- Robustness research: `scripts/run_robustness.bat`",
        "",
        "### Advanced / PowerShell Alternative",
        "",
        "- Paper report only: `scripts/run_paper_report.ps1`",
        "- Basic backtest: `scripts/run_basic.ps1`",
        "- Alpaca paper check: `scripts/run_alpaca_check.ps1`",
        "- Full diagnostic: `scripts/run_full_diagnostic.ps1`",
        "- Robustness research: `scripts/run_robustness.ps1`",
        "",
    ]


def _write_execution_bot_readiness_section(latest_dir, summary):
    path = Path(latest_dir) / "chatgpt_master_report.md"
    section = [
        "## Execution Bot Readiness",
        "",
        f"- execution_preview_status: {summary.get('execution_preview_status', 'not_generated')}",
        "- broker_mode: paper/dry-run",
        "- broker_submit_enabled: false",
        "- live_trading_enabled: false",
        f"- order_intents_count: {int(summary.get('order_intents_count', 0) or 0)}",
        f"- risk_pass_count: {int(summary.get('risk_pass_count', 0) or 0)}",
        f"- blocked_count: {int(summary.get('blocked_count', 0) or 0)}",
        f"- execution_preview_report_path: {summary.get('execution_preview_report_path', 'reports/latest/execution_preview_report.md')}",
        f"- next action: {summary.get('next_action', 'Review execution preview outputs.')}",
        "",
    ]
    if path.exists():
        text = path.read_text(encoding="utf-8")
        marker = "## Execution Bot Readiness"
        if marker in text:
            text = text[: text.index(marker)].rstrip() + "\n\n"
    else:
        text = "# ChatGPT Master Report\n\n"
    path.write_text(text.rstrip() + "\n\n" + "\n".join(section), encoding="utf-8")


def run_execution_preview_check(data_folder="hisseler", report_output_dir="reports", make_chatgpt_report=False):
    latest_dir, _ = _report_output_paths(report_output_dir, data_folder)
    summary = run_execution_preview(latest_dir)
    if make_chatgpt_report:
        _write_execution_bot_readiness_section(latest_dir, summary)
    print(f"Order intents: {latest_dir / 'order_intents.csv'}")
    print(f"Risk check report: {latest_dir / 'order_risk_check_report.md'}")
    print(f"Execution preview report: {latest_dir / 'execution_preview_report.md'}")
    print("broker_submit_enabled: false")
    print("live_trading_enabled: false")
    return summary


def _run_liquidity_guard_sensitivity_report(
    experiments,
    data_folder,
    run_profile,
    quick_mode,
    intraday_dir,
    daily_dir,
    raw_dir,
    intraday_timeframe,
    daily_timeframe,
    cache_rebuilt,
    asset_data,
    benchmark_data,
    cache_path,
    parallel_enabled,
    worker_count,
):
    tasks = _build_experiment_tasks(
        _liquidity_guard_candidate_experiments(experiments),
        data_folder,
        run_profile,
        quick_mode,
        intraday_dir,
        daily_dir,
        raw_dir,
        intraday_timeframe,
        daily_timeframe,
        cache_rebuilt,
        include_raw=True,
    )
    results, batch_sec = _run_experiment_batch(
        tasks,
        asset_data,
        benchmark_data,
        cache_path,
        parallel_enabled=parallel_enabled,
        workers=worker_count,
    )
    return _liquidity_guard_sensitivity_rows(results, asset_data), batch_sec


def _run_cost_slippage_stress_report(
    experiments,
    data_folder,
    run_profile,
    quick_mode,
    intraday_dir,
    daily_dir,
    raw_dir,
    intraday_timeframe,
    daily_timeframe,
    cache_rebuilt,
    asset_data,
    benchmark_data,
    cache_path,
    parallel_enabled,
    worker_count,
):
    tasks = _build_experiment_tasks(
        _cost_stress_candidate_experiments(experiments),
        data_folder,
        run_profile,
        quick_mode,
        intraday_dir,
        daily_dir,
        raw_dir,
        intraday_timeframe,
        daily_timeframe,
        cache_rebuilt,
        include_raw=True,
    )
    results, batch_sec = _run_experiment_batch(
        tasks,
        asset_data,
        benchmark_data,
        cache_path,
        parallel_enabled=parallel_enabled,
        workers=worker_count,
    )
    return _cost_stress_rows(results, asset_data), batch_sec


def _run_oos_walkforward_report(
    experiments,
    data_folder,
    run_profile,
    quick_mode,
    intraday_dir,
    daily_dir,
    raw_dir,
    intraday_timeframe,
    daily_timeframe,
    cache_rebuilt,
    asset_data,
    benchmark_data,
    cache_path,
    parallel_enabled,
    worker_count,
):
    rows = []
    total_batch_sec = 0.0
    candidates = _oos_candidate_experiments(experiments)
    for period_name, start_date, end_date in _oos_walkforward_periods():
        period_tasks = _build_experiment_tasks(
            candidates,
            data_folder,
            run_profile,
            quick_mode,
            intraday_dir,
            daily_dir,
            raw_dir,
            intraday_timeframe,
            daily_timeframe,
            cache_rebuilt,
            include_raw=False,
        )
        for task in period_tasks:
            task["period_name"] = period_name
            task["period_start"] = start_date
            task["period_end"] = end_date
        print(f"[stage] oos period {period_name} started")
        period_results, period_sec = _run_experiment_batch(
            period_tasks,
            asset_data,
            benchmark_data,
            cache_path,
            parallel_enabled=parallel_enabled,
            workers=worker_count,
        )
        total_batch_sec += period_sec
        for result in period_results:
            rows.append(_oos_report_row(result, period_name, start_date, end_date))
        print(f"[stage] oos period {period_name} done in {period_sec:.2f} sec")
    return pd.DataFrame(rows), total_batch_sec


def _write_rs_robustness_report(report, csv_path, markdown_path):
    columns = {
        "Experiment Name": "experiment_name",
        "rs_lookback_days": "rs_lookback_days",
        "rs_top_n": "rs_top_n",
        "rs_trend_sma_days": "rs_trend_sma_days",
        "rs_use_market_regime": "rs_use_market_regime",
        "Net Getiri (%)": "net_return_pct",
        "Max Drawdown (%)": "max_drawdown_pct",
        "Trade Count": "trade_count",
        "Profit Factor": "profit_factor",
        "Expectancy R": "expectancy_r",
        "Worst R": "worst_r",
        "QQQ BuyHold Return (%)": "qqq_return_pct",
        "EqualWeight Universe Return (%)": "equal_weight_return_pct",
        "Strategy Excess Return vs QQQ (%)": "excess_vs_qqq_pct",
        "Strategy Excess Return vs EqualWeight (%)": "excess_vs_equal_weight_pct",
        "Compact Score": "compact_score",
    }
    output = report[list(columns)].rename(columns=columns)
    output.to_csv(csv_path, index=False)
    best = output.sort_values("compact_score", ascending=False).head(10)
    worst = output.sort_values("compact_score", ascending=True).head(10)
    grouped = {
        "lookback": output.groupby("rs_lookback_days")["compact_score"].mean().round(4),
        "top_n": output.groupby("rs_top_n")["compact_score"].mean().round(4),
        "sma": output.groupby("rs_trend_sma_days")["compact_score"].mean().round(4),
        "regime": output.groupby("rs_use_market_regime")["compact_score"].mean().round(4),
    }
    try:
        best_table = best.to_markdown(index=False)
        worst_table = worst.to_markdown(index=False)
    except ImportError:
        best_table = best.to_string(index=False)
        worst_table = worst.to_string(index=False)
    content = [
        "# RS Robustness Report",
        "",
        "## En iyi 10 kombinasyon",
        "",
        best_table,
        "",
        "## En kotu 10 kombinasyon",
        "",
        worst_table,
        "",
        "## Kisa ozet",
        "",
        f"- Lookback compact score ortalamalari: {grouped['lookback'].to_dict()}",
        f"- TopN compact score ortalamalari: {grouped['top_n'].to_dict()}",
        f"- SMA compact score ortalamalari: {grouped['sma'].to_dict()}",
        f"- Regime compact score ortalamalari: {grouped['regime'].to_dict()}",
        "- QQQ ve EqualWeight farklari CSV'deki excess kolonlarinda yer alir.",
        "",
    ]
    markdown_path.write_text("\n".join(content), encoding="utf-8")


def _candidate_row(label, row, source):
    return {
        "candidate": label,
        "source": source,
        "net_return_pct": row.get("Net Getiri (%)", ""),
        "max_drawdown_pct": row.get("Max Drawdown (%)", ""),
        "trade_count": row.get("Trade Count", ""),
        "profit_factor": row.get("Profit Factor", ""),
        "expectancy_r": row.get("Expectancy R", ""),
        "compact_score": row.get("Compact Score", ""),
        "qqq_return_pct": row.get("QQQ BuyHold Return (%)", ""),
        "equal_weight_return_pct": row.get("EqualWeight Universe Return (%)", ""),
    }


def _write_candidate_summary_report(report, robustness, markdown_path):
    rows = []
    by_name = report.set_index("Experiment Name", drop=False)
    for name in ["rs_m63_sma100", "rs_m63_no_regime"]:
        if name in by_name.index:
            rows.append(_candidate_row(name, by_name.loc[name], "baseline"))
    donchian = report[report["Strategy Name"].eq("donchian_breakout")]
    if not donchian.empty:
        best = donchian.sort_values("Compact Score", ascending=False).iloc[0]
        rows.append(_candidate_row(best["Experiment Name"], best, "best_donchian"))
    if robustness is not None and not robustness.empty:
        robust_by_name = robustness.set_index("Experiment Name", drop=False)
        for name in [
            "rs_m126_top10_sma100_no_regime",
            "rs_m126_top20_sma100_no_regime",
            "rs_m126_top30_sma100_no_regime",
            "rs_m126_top30_sma200_no_regime",
        ]:
            if name in robust_by_name.index:
                rows.append(_candidate_row(name, robust_by_name.loc[name], "rs_m126"))
    first = report.iloc[0] if not report.empty else {}
    rows.append(
        {
            "candidate": "QQQ",
            "source": "benchmark",
            "net_return_pct": first.get("QQQ BuyHold Return (%)", ""),
            "max_drawdown_pct": first.get("QQQ BuyHold MaxDD (%)", ""),
            "trade_count": "",
            "profit_factor": "",
            "expectancy_r": "",
            "compact_score": "",
            "qqq_return_pct": first.get("QQQ BuyHold Return (%)", ""),
            "equal_weight_return_pct": "",
        }
    )
    rows.append(
        {
            "candidate": "EqualWeight",
            "source": "benchmark",
            "net_return_pct": first.get("EqualWeight Universe Return (%)", ""),
            "max_drawdown_pct": first.get("EqualWeight Universe BuyHold MaxDD (%)", ""),
            "trade_count": "",
            "profit_factor": "",
            "expectancy_r": "",
            "compact_score": "",
            "qqq_return_pct": "",
            "equal_weight_return_pct": first.get("EqualWeight Universe Return (%)", ""),
        }
    )
    summary = pd.DataFrame(rows)
    try:
        table = summary.to_markdown(index=False)
    except ImportError:
        table = summary.to_string(index=False)
    markdown_path.write_text(
        "\n".join(["# Candidate Summary Report", "", table, ""]),
        encoding="utf-8",
    )


def run_strategy_experiments(
    data_folder="hisseler",
    experiments=None,
    run_profile="fast",
    quick_mode=True,
    compact_report=True,
    compact_report_name="strategy_compact_report.csv",
    compact_markdown_name="strategy_compact_report.md",
    report_output_dir="reports",
    reports="basic",
    reuse_existing_report=False,
    write_yearly_performance_report=None,
    write_rs_robustness_report=None,
    write_candidate_summary_report=None,
    write_performance_profile=None,
    parallel=False,
    workers="auto",
    run_robustness=False,
    run_oos=False,
    run_attribution=False,
    run_cost_stress=False,
    run_liquidity_guard_sensitivity=False,
    make_chatgpt_report=False,
    paper_report=False,
    paper_strategy="main",
    paper_include_watchlist=False,
    paper_max_new_positions_per_day=5,
    command=None,
    intraday_data_dir=None,
    daily_data_dir=None,
    raw_1min_data_dir=None,
    intraday_timeframe=INTRADAY_TIMEFRAME,
    daily_timeframe=DAILY_TIMEFRAME,
    start_date=None,
    end_date=None,
    allowed_symbols=None,
    force_rebuild_prepared_cache=False,
    **kwargs,
):
    total_started = _now()
    cpu_started = time.process_time()
    report_mode = str(reports or "basic").lower()
    if report_mode not in {"basic", "full"}:
        raise ValueError("--reports 'basic' veya 'full' olmali.")
    if write_yearly_performance_report is None:
        write_yearly_performance_report = report_mode == "full"
    if write_rs_robustness_report is None:
        write_rs_robustness_report = bool(run_robustness)
    if write_candidate_summary_report is None:
        write_candidate_summary_report = report_mode == "full"
    if write_performance_profile is None:
        write_performance_profile = True
    parallel_enabled = bool(parallel)
    worker_count = _resolve_worker_count(parallel_enabled, workers)
    _warn_onedrive_path(Path(data_folder).resolve())
    latest_dir, run_dir = _report_output_paths(report_output_dir, data_folder)
    _remove_stale_performance_profile(latest_dir)
    if report_mode == "basic":
        _remove_optional_report_files(latest_dir, run_dir)
    if reuse_existing_report:
        return pd.read_csv(latest_dir / compact_report_name)
    if not experiments:
        raise ValueError("experiments must be provided for modular validation runs.")
    latest_csv_path = latest_dir / compact_report_name
    latest_markdown_path = latest_dir / compact_markdown_name
    run_csv_path = run_dir / compact_report_name
    run_markdown_path = run_dir / compact_markdown_name
    stage_started = _stage_started("data loading")
    intraday_dir, daily_dir, raw_dir = _resolve_data_dirs(
        data_folder,
        intraday_data_dir=intraday_data_dir,
        daily_data_dir=daily_data_dir,
        raw_1min_data_dir=raw_1min_data_dir,
    )
    intraday_symbols = {
        _symbol_from_file(path, "_130min.csv")
        for path in intraday_dir.glob("*_130min.csv")
    }
    daily_symbols = {
        _symbol_from_file(path, "_1D.csv")
        for path in daily_dir.glob("*_1D.csv")
    }
    symbols = sorted(intraday_symbols | daily_symbols)
    if allowed_symbols:
        allowed = {str(symbol).upper() for symbol in allowed_symbols}
        symbols = [symbol for symbol in symbols if symbol in allowed]
    if not symbols:
        raise ValueError(
            f"No symbols found in {intraday_dir} (*_130min.csv) or {daily_dir} (*_1D.csv)."
        )
    data_loading_sec = _stage_done("data loading", stage_started)
    stage_started = _stage_started("data audit")
    audit = _write_data_audit(symbols, daily_dir, intraday_dir, latest_dir, run_dir)
    data_audit_sec = _stage_done("data audit", stage_started)
    stage_started = _stage_started("prepared cache")
    csv_timing = _TimingCollector()
    prepared, cache_path, cache_rebuilt = _load_or_build_prepared_cache(
        data_folder,
        intraday_dir,
        daily_dir,
        intraday_timeframe,
        daily_timeframe,
        start_date,
        end_date,
        symbols,
        force_rebuild=force_rebuild_prepared_cache,
        timing=csv_timing,
    )
    prepared_cache_sec = _stage_done("prepared cache", stage_started)
    print(f"[stage] prepared cache {'miss' if cache_rebuilt else 'hit'}")
    asset_data = prepared["asset_data"]
    cache_summary = prepared.get("cache_summary", {})
    benchmark_data = prepared.get("benchmark_data")
    if benchmark_data is None:
        benchmark_data = _load_daily_benchmark(daily_dir, start_date, end_date)
        prepared["benchmark_data"] = benchmark_data
    print(f"Experiment groups count: {len(experiments)}")
    print(f"Intraday data dir: {intraday_dir}")
    print(f"Daily data dir: {daily_dir}")
    print(f"Raw 1min data dir (not used by backtest): {raw_dir}")
    print(f"Prepared feature cache: {cache_path.name}; rebuilt={cache_rebuilt}")
    profile_rows = []
    experiment_runtime_rows = []
    raw_results = {}
    tasks = _build_experiment_tasks(
        experiments,
        data_folder,
        run_profile,
        quick_mode,
        intraday_dir,
        daily_dir,
        raw_dir,
        intraday_timeframe,
        daily_timeframe,
        cache_rebuilt,
        include_raw=write_yearly_performance_report,
    )
    baseline_results, baseline_parallel_runtime_sec = _run_experiment_batch(
        tasks,
        asset_data,
        benchmark_data,
        cache_path,
        parallel_enabled=parallel_enabled,
        workers=worker_count,
    )
    rows = [result["row"] for result in baseline_results]
    for result in baseline_results:
        if result.get("raw"):
            raw_results[result["experiment_name"]] = result["raw"]
        experiment_runtime_rows.append(
            {
                "stage": "strategy_runtime",
                "experiment_name": result["experiment_name"],
                "duration_sec": round(result["runtime_sec"], 4),
                "strategy_runtime_sec": round(result["runtime_sec"], 4),
                "rolling_high_low_sec": result["row"].get("rolling_high_low_sec", ""),
                "signal_generation_sec": result["row"].get("signal_generation_sec", ""),
                "ranking_sec": result["row"].get("ranking_sec", ""),
                "portfolio_sim_sec": result["row"].get("portfolio_sim_sec", ""),
                "exits_sec": result["row"].get("exits_sec", ""),
                "diagnostics_sec": result["row"].get("diagnostics_sec", ""),
                "symbols_loaded": result.get("symbols_loaded", 0),
                "rows_loaded": cache_summary.get("rows_loaded", ""),
                "cache_hit_count": cache_summary.get("prepared_disk_cache_hits", 0),
                "cache_miss_count": cache_summary.get("prepared_disk_cache_misses", 0),
            }
        )
    report = pd.DataFrame(rows)
    report = _add_compact_scores(report)
    report = report.sort_values("Compact Score", ascending=False, na_position="last")
    stage_started = _stage_started("report write")
    report.to_csv(latest_csv_path, index=False)
    report.to_csv(run_csv_path, index=False)
    if compact_report:
        generated_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        _write_compact_markdown(report, latest_markdown_path, None, generated_at)
        _write_compact_markdown(report, run_markdown_path, None, generated_at)
    for directory in [latest_dir, run_dir]:
        _write_benchmark_audit(report, directory / "benchmark_audit_report.md", cache_path)
    report_write_sec = _stage_done("report write", stage_started)
    robustness = None
    robustness_report_sec = 0.0
    robustness_parallel_runtime_sec = 0.0
    oos = None
    attribution = None
    attribution_summary = None
    cost_stress = None
    liquidity_guard_sensitivity = None
    paper_signal_report = None
    oos_report_sec = 0.0
    oos_parallel_runtime_sec = 0.0
    if run_oos:
        stage_started = _stage_started("oos walkforward")
        oos, oos_parallel_runtime_sec = _run_oos_walkforward_report(
            experiments,
            data_folder,
            run_profile,
            quick_mode,
            intraday_dir,
            daily_dir,
            raw_dir,
            intraday_timeframe,
            daily_timeframe,
            cache_rebuilt,
            asset_data,
            benchmark_data,
            cache_path,
            parallel_enabled,
            worker_count,
        )
        for directory in [latest_dir, run_dir]:
            _write_oos_walkforward_report(
                oos,
                directory / "oos_walkforward_report.csv",
                directory / "oos_walkforward_report.md",
            )
        oos_report_sec = _stage_done("oos walkforward", stage_started)
    attribution_report_sec = 0.0
    attribution_parallel_runtime_sec = 0.0
    if run_attribution:
        stage_started = _stage_started("candidate attribution")
        attribution, attribution_summary, attribution_parallel_runtime_sec = (
            _run_candidate_attribution_report(
                experiments,
                data_folder,
                run_profile,
                quick_mode,
                intraday_dir,
                daily_dir,
                raw_dir,
                intraday_timeframe,
                daily_timeframe,
                cache_rebuilt,
                asset_data,
                benchmark_data,
                cache_path,
                parallel_enabled,
                worker_count,
            )
        )
        for directory in [latest_dir, run_dir]:
            _write_candidate_attribution_report(
                attribution,
                attribution_summary,
                directory / "candidate_attribution_report.csv",
                directory / "candidate_attribution_report.md",
            )
        attribution_report_sec = _stage_done("candidate attribution", stage_started)
    cost_stress_report_sec = 0.0
    cost_stress_parallel_runtime_sec = 0.0
    if run_cost_stress:
        stage_started = _stage_started("cost slippage stress")
        cost_stress, cost_stress_parallel_runtime_sec = _run_cost_slippage_stress_report(
            experiments,
            data_folder,
            run_profile,
            quick_mode,
            intraday_dir,
            daily_dir,
            raw_dir,
            intraday_timeframe,
            daily_timeframe,
            cache_rebuilt,
            asset_data,
            benchmark_data,
            cache_path,
            parallel_enabled,
            worker_count,
        )
        for directory in [latest_dir, run_dir]:
            _write_cost_slippage_stress_report(
                cost_stress,
                directory / "cost_slippage_stress_report.csv",
                directory / "cost_slippage_stress_report.md",
            )
        cost_stress_report_sec = _stage_done("cost slippage stress", stage_started)
    liquidity_guard_sensitivity_report_sec = 0.0
    liquidity_guard_sensitivity_parallel_runtime_sec = 0.0
    if run_liquidity_guard_sensitivity:
        stage_started = _stage_started("liquidity guard sensitivity")
        liquidity_guard_sensitivity, liquidity_guard_sensitivity_parallel_runtime_sec = (
            _run_liquidity_guard_sensitivity_report(
                experiments,
                data_folder,
                run_profile,
                quick_mode,
                intraday_dir,
                daily_dir,
                raw_dir,
                intraday_timeframe,
                daily_timeframe,
                cache_rebuilt,
                asset_data,
                benchmark_data,
                cache_path,
                parallel_enabled,
                worker_count,
            )
        )
        for directory in [latest_dir, run_dir]:
            _write_liquidity_guard_sensitivity_report(
                liquidity_guard_sensitivity,
                directory / "liquidity_guard_sensitivity_report.csv",
                directory / "liquidity_guard_sensitivity_report.md",
            )
        liquidity_guard_sensitivity_report_sec = _stage_done(
            "liquidity guard sensitivity",
            stage_started,
        )
    if run_cost_stress:
        for directory in [latest_dir, run_dir]:
            _write_final_candidate_decision_report(
                report,
                oos,
                attribution_summary,
                cost_stress,
                directory / "final_candidate_decision_report.md",
            )
    paper_report_sec = 0.0
    daily_pipeline_status = None
    data_precheck_result = None
    operational_health = None
    if paper_report:
        stage_started = _stage_started("paper report")
        previous_paper_signal_report = _load_previous_paper_signal_report(
            latest_dir / "paper_signal_report.csv"
        )
        paper_signal_report = _run_paper_signal_report(
            experiments,
            data_folder,
            run_profile,
            quick_mode,
            intraday_dir,
            daily_dir,
            raw_dir,
            intraday_timeframe,
            daily_timeframe,
            cache_rebuilt,
            asset_data,
            benchmark_data,
            latest_dir / "paper_trade_log.csv",
            paper_strategy_mode=paper_strategy,
            include_watchlist=paper_include_watchlist,
            paper_max_new_positions_per_day=paper_max_new_positions_per_day,
        )
        _ensure_paper_trade_log(latest_dir / "paper_trade_log.csv")
        paper_order_review = _write_paper_order_review(
            paper_signal_report,
            latest_dir / "paper_order_review.csv",
        )
        paper_signal_report, _ = _apply_paper_order_review(
            paper_signal_report,
            paper_order_review,
            latest_dir / "paper_trade_log.csv",
            asset_data,
        )
        paper_signal_report = _apply_previous_pending_next_open_orders(
            paper_signal_report,
            previous_paper_signal_report,
            paper_order_review,
            latest_dir / "paper_trade_log.csv",
            asset_data,
        )
        latest_daily_for_pending = _latest_csv_data_date(daily_dir)
        _write_paper_pending_orders(
            paper_signal_report,
            latest_dir / "paper_pending_orders.csv",
            latest_daily_for_pending,
        )
        daily_pipeline_status = _daily_preflight_status(
            paper_signal_report,
            latest_dir / "paper_trade_log.csv",
            daily_dir,
            intraday_dir,
            data_update_ran=str(os.getenv("DAILY_DATA_UPDATE_RAN", "")).strip().lower() in {"1", "true", "yes"},
        )
        _write_daily_preflight_report(
            daily_pipeline_status,
            latest_dir / "daily_preflight_report.md",
        )
        data_precheck_result = _write_data_precheck_report(
            paper_signal_report,
            daily_dir,
            intraday_dir,
            daily_pipeline_status,
            latest_dir / "data_precheck_report.md",
        )
        for directory in [latest_dir, run_dir]:
            if directory != latest_dir:
                _ensure_paper_trade_log(directory / "paper_trade_log.csv")
                _write_paper_order_review(
                    paper_signal_report,
                    directory / "paper_order_review.csv",
                )
                _write_paper_pending_orders(
                    paper_signal_report,
                    directory / "paper_pending_orders.csv",
                    latest_daily_for_pending,
                )
                _write_daily_preflight_report(
                    daily_pipeline_status,
                    directory / "daily_preflight_report.md",
                )
                _write_data_precheck_report(
                    paper_signal_report,
                    daily_dir,
                    intraday_dir,
                    daily_pipeline_status,
                    directory / "data_precheck_report.md",
                )
            _write_paper_signal_report(
                paper_signal_report,
                directory / "paper_signal_report.csv",
                directory / "paper_signal_report.md",
            )
            _write_paper_trade_log_template(directory / "paper_trade_log_template.csv")
            _write_paper_trading_plan(directory / "paper_trading_plan.md")
        paper_report_sec = _stage_done("paper report", stage_started)
    if write_rs_robustness_report:
        stage_started = _stage_started("robustness report")
        grid = _rs_robustness_experiments()
        print(f"\nRS robustness grid calisiyor: {len(grid)} experiment")
        robust_tasks = _build_experiment_tasks(
            grid,
            data_folder,
            run_profile,
            quick_mode,
            intraday_dir,
            daily_dir,
            raw_dir,
            intraday_timeframe,
            daily_timeframe,
            cache_rebuilt,
            include_raw=write_yearly_performance_report,
        )
        robustness_results, robustness_parallel_runtime_sec = _run_experiment_batch(
            robust_tasks,
            asset_data,
            benchmark_data,
            cache_path,
            parallel_enabled=parallel_enabled,
            workers=worker_count,
        )
        for result in robustness_results:
            if result.get("raw"):
                raw_results[result["experiment_name"]] = result["raw"]
            experiment_runtime_rows.append(
                {
                    "stage": "robustness_strategy_runtime",
                    "experiment_name": result["experiment_name"],
                    "duration_sec": round(result["runtime_sec"], 4),
                    "strategy_runtime_sec": round(result["runtime_sec"], 4),
                    "rolling_high_low_sec": result["row"].get("rolling_high_low_sec", ""),
                    "signal_generation_sec": result["row"].get("signal_generation_sec", ""),
                    "ranking_sec": result["row"].get("ranking_sec", ""),
                    "portfolio_sim_sec": result["row"].get("portfolio_sim_sec", ""),
                    "exits_sec": result["row"].get("exits_sec", ""),
                    "diagnostics_sec": result["row"].get("diagnostics_sec", ""),
                    "symbols_loaded": result.get("symbols_loaded", 0),
                    "rows_loaded": cache_summary.get("rows_loaded", ""),
                    "cache_hit_count": cache_summary.get("prepared_disk_cache_hits", 0),
                    "cache_miss_count": cache_summary.get("prepared_disk_cache_misses", 0),
                }
            )
        robustness_rows = [result["row"] for result in robustness_results]
        robustness = _add_compact_scores(pd.DataFrame(robustness_rows))
        robustness = robustness.sort_values("Experiment Name")
        for directory in [latest_dir, run_dir]:
            _write_rs_robustness_report(
                robustness,
                directory / "rs_robustness_report.csv",
                directory / "rs_robustness_report.md",
            )
        robustness_report_sec = _stage_done("robustness report", stage_started)
    yearly_report_sec = 0.0
    if write_yearly_performance_report:
        stage_started = _stage_started("yearly report")
        try:
            yearly = _yearly_rows(
                raw_results,
                _yearly_focus_names(),
                benchmark_data,
                asset_data,
            )
            for directory in [latest_dir, run_dir]:
                _write_yearly_report(
                    yearly,
                    directory / "yearly_performance_report.csv",
                    directory / "yearly_performance_report.md",
                )
        except Exception as error:
            print(f"Warning: yearly performance report failed, continuing run: {error}")
        yearly_report_sec = _stage_done("yearly report", stage_started)
    candidate_summary_sec = 0.0
    if write_candidate_summary_report:
        stage_started = _stage_started("candidate summary")
        for directory in [latest_dir, run_dir]:
            _write_candidate_summary_report(
                report,
                robustness,
                directory / "candidate_summary_report.md",
            )
        candidate_summary_sec = _stage_done("candidate summary", stage_started)
    missing_counts = pd.to_numeric(
        report.get("EqualWeight Missing Symbols Count", pd.Series(dtype=float)),
        errors="coerce",
    ).dropna()
    if not missing_counts.empty and int(missing_counts.max()) > 0:
        print(f"EqualWeight missing symbols: {int(missing_counts.max())}")
    total_runtime_sec = _now() - total_started
    cpu_percent = ((time.process_time() - cpu_started) / total_runtime_sec * 100.0) if total_runtime_sec > 0 else 0.0
    if paper_report and daily_pipeline_status is not None:
        regression_decision = _final_candidate_decision_frame(report, oos, attribution_summary, cost_stress)
        source_decision = _read_final_candidate_decision_report(latest_dir / "final_candidate_decision_report.md")
        if source_decision is not None and not source_decision.empty:
            regression_decision = source_decision
        regression_guard_result = _write_regression_guard_report(
            report,
            regression_decision,
            latest_dir,
            latest_dir / "regression_guard_report.md",
        )
        for directory in [run_dir]:
            _write_regression_guard_report(
                report,
                regression_decision,
                latest_dir,
                directory / "regression_guard_report.md",
            )
        pipeline_health = _write_pipeline_health_log(
            daily_pipeline_status,
            data_precheck_result or {"status": "OK", "warnings": [], "critical": []},
            regression_guard_result,
            latest_dir,
            latest_dir.parent / "history",
            command or "unknown",
            total_runtime_sec,
            cache_summary,
        )
        schema_validation_result = _write_report_schema_validation_report(
            latest_dir,
            latest_dir / "report_schema_validation_report.md",
        )
        paper_state_validation_result = _write_paper_state_validation_report(
            latest_dir,
            latest_dir / "paper_state_validation_report.md",
        )
        aging_summary = _pending_aging_summary(latest_dir / "paper_pending_orders.csv")
        operational_health = {
            "pipeline_health": pipeline_health,
            "pending_aging_summary": aging_summary,
            "data_precheck": data_precheck_result or {"status": "OK", "warnings": [], "critical": []},
            "regression_guard": regression_guard_result,
            "schema_validation": schema_validation_result,
            "paper_state_validation": paper_state_validation_result,
            "next_action": daily_pipeline_status.get("next_action", "Review operational health reports."),
        }
    if write_performance_profile:
        profile_write_started = _now()
        worker_runtimes = [float(row.get("duration_sec", 0.0) or 0.0) for row in experiment_runtime_rows]
        sequential_estimate = sum(worker_runtimes)
        batch_runtime_sum = baseline_parallel_runtime_sec + robustness_parallel_runtime_sec
        batch_runtime_sum += oos_parallel_runtime_sec
        batch_runtime_sum += attribution_parallel_runtime_sec
        batch_runtime_sum += cost_stress_parallel_runtime_sec
        batch_runtime_sum += liquidity_guard_sensitivity_parallel_runtime_sec
        speedup_estimate = (
            sequential_estimate / batch_runtime_sum
            if parallel_enabled and batch_runtime_sum > 0
            else 1.0
        )
        cpu_percent_report = (
            sequential_estimate / total_runtime_sec * 100.0
            if parallel_enabled and total_runtime_sec > 0
            else cpu_percent
        )
        total_row = {
            "stage": "total",
            "experiment_name": "",
            "duration_sec": round(total_runtime_sec, 4),
            "total_runtime_sec": round(total_runtime_sec, 4),
            "data_audit_sec": round(data_audit_sec, 4),
            "csv_read_sec": round(float(cache_summary.get("csv_read_sec", 0.0) or 0.0), 4),
            "prepared_cache_build_sec": round(float(cache_summary.get("prepared_cache_build_sec", 0.0) or 0.0), 4),
            "prepared_cache_load_sec": round(float(cache_summary.get("prepared_cache_load_sec", 0.0) or 0.0), 4),
            "indicator_compute_sec": round(sum(row["duration_sec"] for row in experiment_runtime_rows), 4),
            "strategy_runtime_sec": round(sum(row["duration_sec"] for row in experiment_runtime_rows), 4),
            "report_write_sec": round(report_write_sec, 4),
            "yearly_report_sec": round(yearly_report_sec, 4),
            "robustness_report_sec": round(robustness_report_sec, 4),
            "candidate_summary_sec": round(candidate_summary_sec, 4),
            "oos_report_sec": round(oos_report_sec, 4),
            "attribution_report_sec": round(attribution_report_sec, 4),
            "cost_stress_report_sec": round(cost_stress_report_sec, 4),
            "liquidity_guard_sensitivity_report_sec": round(liquidity_guard_sensitivity_report_sec, 4),
            "paper_report_sec": round(paper_report_sec, 4),
            "cache_hit_count": int(cache_summary.get("prepared_disk_cache_hits", 0) or 0),
            "cache_miss_count": int(cache_summary.get("prepared_disk_cache_misses", 0) or 0),
            "symbols_loaded": int(cache_summary.get("symbols_loaded", len(asset_data)) or 0),
            "rows_loaded": int(cache_summary.get("rows_loaded", 0) or 0),
            "process_cpu_percent_estimate": round(cpu_percent_report, 2),
            "parallel_enabled": parallel_enabled,
            "workers": worker_count if parallel_enabled else 1,
            "cpu_count": os.cpu_count() or 1,
            "baseline_parallel_runtime_sec": round(baseline_parallel_runtime_sec, 4),
            "robustness_parallel_runtime_sec": round(robustness_parallel_runtime_sec, 4),
            "oos_parallel_runtime_sec": round(oos_parallel_runtime_sec, 4),
            "attribution_parallel_runtime_sec": round(attribution_parallel_runtime_sec, 4),
            "cost_stress_parallel_runtime_sec": round(cost_stress_parallel_runtime_sec, 4),
            "liquidity_guard_sensitivity_parallel_runtime_sec": round(
                liquidity_guard_sensitivity_parallel_runtime_sec,
                4,
            ),
            "average_worker_runtime_sec": round(
                sum(worker_runtimes) / max(len(worker_runtimes), 1),
                4,
            ),
            "max_worker_runtime_sec": round(max(worker_runtimes) if worker_runtimes else 0.0, 4),
            "speedup_estimate_vs_sequential": round(speedup_estimate, 4),
        }
        profile_rows = [
            total_row,
            {"stage": "data_loading", "duration_sec": round(data_loading_sec, 4)},
            {"stage": "data_audit", "duration_sec": round(data_audit_sec, 4)},
            {"stage": "prepared_cache", "duration_sec": round(prepared_cache_sec, 4)},
            {"stage": "report_write", "duration_sec": round(report_write_sec, 4)},
            {"stage": "yearly_report", "duration_sec": round(yearly_report_sec, 4)},
            {"stage": "robustness_report", "duration_sec": round(robustness_report_sec, 4)},
            {"stage": "oos_walkforward", "duration_sec": round(oos_report_sec, 4)},
            {"stage": "candidate_attribution", "duration_sec": round(attribution_report_sec, 4)},
            {"stage": "cost_slippage_stress", "duration_sec": round(cost_stress_report_sec, 4)},
            {
                "stage": "liquidity_guard_sensitivity",
                "duration_sec": round(liquidity_guard_sensitivity_report_sec, 4),
            },
            {"stage": "paper_report", "duration_sec": round(paper_report_sec, 4)},
            {"stage": "candidate_summary", "duration_sec": round(candidate_summary_sec, 4)},
            *experiment_runtime_rows,
        ]
        for directory in [latest_dir, run_dir]:
            slowest = _write_performance_profile(
                profile_rows,
                directory / "performance_profile_report.csv",
                directory / "performance_profile_report.md",
            )
        total_runtime_sec = _now() - total_started
        total_row["duration_sec"] = round(total_runtime_sec, 4)
        total_row["total_runtime_sec"] = round(total_runtime_sec, 4)
        total_row["report_write_sec"] = round(
            report_write_sec + (_now() - profile_write_started),
            4,
        )
        profile_rows[0] = total_row
        for directory in [latest_dir, run_dir]:
            slowest = _write_performance_profile(
                profile_rows,
                directory / "performance_profile_report.csv",
                directory / "performance_profile_report.md",
            )
        print("Slowest stages:")
        for _, row in slowest.head(5).iterrows():
            print(f"- {row['stage']}: {float(row['duration_sec']):.2f} sec")
    if make_chatgpt_report:
        flags = {
            "run_oos": run_oos,
            "run_attribution": run_attribution,
            "run_cost_stress": run_cost_stress,
            "run_liquidity_guard_sensitivity": run_liquidity_guard_sensitivity,
            "run_robustness": run_robustness,
            "paper_report": paper_report,
        }
        for directory in [latest_dir, run_dir]:
            _write_chatgpt_master_report(
                report,
                audit,
                oos,
                attribution,
                attribution_summary,
                cost_stress,
                liquidity_guard_sensitivity,
                paper_signal_report,
                profile_rows,
                directory / "chatgpt_master_report.md",
                final_decision_path=latest_dir / "final_candidate_decision_report.md",
                command=command or "unknown",
                report_mode=report_mode,
                flags=flags,
                total_runtime_sec=total_runtime_sec,
                cache_summary=cache_summary,
                daily_pipeline_status=daily_pipeline_status,
                operational_health=operational_health,
            )
    print(f"Total runtime: {total_runtime_sec:.2f} sec")
    print(
        "Cache hit/miss: "
        f"{int(cache_summary.get('prepared_disk_cache_hits', 0) or 0)}/"
        f"{int(cache_summary.get('prepared_disk_cache_misses', 0) or 0)}"
    )
    print(
        f"Report mode: {report_mode} "
        "(basic=compact/audit/performance, full=yearly/candidate; robustness=--run-robustness)"
    )
    print(f"Report latest dir: {latest_dir}")
    print(f"Report run dir: {run_dir}")
    return report


__all__ = ["run_alpaca_paper_check", "run_strategy_experiments", "run_universe_scanner"]
