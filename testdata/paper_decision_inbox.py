from __future__ import annotations

from pathlib import Path

import pandas as pd

try:
    from .paper_state_manager import net_open_positions_from_trade_log
    from .paper_ops_dashboard import RESOLVING_DATA_POLICY_VALUES, build_paper_ops_dashboard
except ImportError:
    from paper_state_manager import net_open_positions_from_trade_log
    from paper_ops_dashboard import RESOLVING_DATA_POLICY_VALUES, build_paper_ops_dashboard


INBOX_COLUMNS = [
    "symbol",
    "action",
    "strategy_name",
    "signal_date",
    "fill_status",
    "user_decision",
    "paper_rank",
    "ranking_score",
    "liquidity_guard_pass",
    "already_open_position",
    "valid_exit_signal",
    "data_policy_blocked",
    "warning",
    "reason",
    "eligibility_status",
    "recommended_operator_action",
]


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _bool_value(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _decision_key(row) -> tuple[str, str, str, str]:
    return (
        _text(row.get("strategy_name", "")),
        _text(row.get("symbol", "")).upper(),
        _text(row.get("action", "")).upper(),
        _text(row.get("signal_date", "")),
    )


def _review_lookup(review: pd.DataFrame) -> dict[tuple[str, str, str, str], str]:
    if review.empty:
        return {}
    decisions = {}
    for _, row in review.iterrows():
        decisions[_decision_key(row)] = _text(row.get("user_decision", "HOLD_REVIEW")).upper() or "HOLD_REVIEW"
    return decisions


def _pending_lookup(pending: pd.DataFrame) -> dict[tuple[str, str, str, str], pd.Series]:
    if pending.empty:
        return {}
    return {_decision_key(row): row for _, row in pending.iterrows()}


def _data_policy_maps(policy: pd.DataFrame) -> tuple[set[str], set[str]]:
    if policy.empty:
        return set(), set()
    policy_status = policy.get("policy_status", pd.Series("", index=policy.index)).fillna("").astype(str).str.upper()
    operator_decision = policy.get("operator_decision", pd.Series("", index=policy.index)).fillna("").astype(str).str.upper()
    operator_note = policy.get("operator_note", pd.Series("", index=policy.index)).fillna("").astype(str).str.strip()
    symbol = policy.get("symbol", pd.Series("", index=policy.index)).fillna("").astype(str).str.upper()
    blocking = policy_status.str.startswith("BLOCK")
    resolved = operator_decision.isin(RESOLVING_DATA_POLICY_VALUES) & operator_note.ne("")
    blocked_symbols = set(symbol[blocking & ~resolved].replace("", pd.NA).dropna().tolist())
    warning_symbols = set(symbol[blocking & resolved].replace("", pd.NA).dropna().tolist())
    return blocked_symbols, warning_symbols


def _open_symbols(trade_log: pd.DataFrame) -> set[str]:
    open_positions = net_open_positions_from_trade_log(trade_log)
    if open_positions.empty or "symbol" not in open_positions:
        return set()
    return set(open_positions["symbol"].dropna().astype(str).str.upper().tolist())


def _is_pending_fill(fill_status: str) -> bool:
    return fill_status in {"PENDING_NEXT_OPEN", "PENDING"}


def _is_review_decision(user_decision: str) -> bool:
    return user_decision in {"HOLD_REVIEW", "REVIEW_REQUIRED", ""}


def _valid_exit(row, open_symbols: set[str]) -> bool:
    symbol = _text(row.get("symbol", "")).upper()
    if symbol not in open_symbols:
        return False
    reason = _text(row.get("reason", "")).lower()
    warning = _text(row.get("warning", "")).lower()
    status = _text(row.get("current_position_status", "")).lower()
    combined = " ".join([reason, warning, status])
    return any(token in combined for token in ["latest_exit_signal", "exit", "close", "in_position", "open"])


def _candidate_rows(signals: pd.DataFrame, pending: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if not signals.empty:
        action = signals.get("action", pd.Series("", index=signals.index)).fillna("").astype(str).str.upper()
        actionable = signals.get("paper_actionable", pd.Series(False, index=signals.index)).map(_bool_value)
        rows.append(signals[action.isin(["BUY", "SELL"]) & actionable].copy())
    if not pending.empty:
        pending_rows = pending.copy()
        pending_rows["fill_status"] = pending_rows.get("fill_status", pending_rows.get("status", "PENDING"))
        pending_rows["paper_actionable"] = True
        rows.append(pending_rows)
    if not rows:
        return pd.DataFrame()
    combined = pd.concat(rows, ignore_index=True, sort=False)
    combined["_key"] = combined.apply(_decision_key, axis=1)
    return combined.drop_duplicates("_key", keep="last").drop(columns=["_key"])


def _classify(row, open_symbols: set[str], blocked_symbols: set[str], warning_symbols: set[str]) -> tuple[str, str, bool, bool, bool]:
    symbol = _text(row.get("symbol", "")).upper()
    action = _text(row.get("action", "")).upper()
    fill_status = _text(row.get("fill_status", row.get("status", ""))).upper()
    user_decision = _text(row.get("user_decision", "HOLD_REVIEW")).upper()
    liquidity_pass = _bool_value(row.get("liquidity_guard_pass", False))
    already_open = symbol in open_symbols
    data_blocked = symbol in blocked_symbols
    valid_exit = action == "SELL" and _valid_exit(row, open_symbols)
    warning = _text(row.get("warning", ""))
    has_nonblocking_warning = bool(warning) or symbol in warning_symbols

    if user_decision == "ACCEPT":
        if _is_pending_fill(fill_status):
            return "WAIT_FOR_NEXT_OPEN", "WAIT_FOR_NEXT_OPEN", already_open, valid_exit, data_blocked
        return "ALREADY_ACCEPTED", "ALREADY_ACCEPTED", already_open, valid_exit, data_blocked
    if user_decision == "REJECT":
        return "ALREADY_REJECTED", "ALREADY_REJECTED", already_open, valid_exit, data_blocked
    if data_blocked:
        return "BLOCKED", "BLOCK_DO_NOT_ACCEPT", already_open, valid_exit, data_blocked
    if not liquidity_pass:
        return "BLOCKED", "BLOCK_DO_NOT_ACCEPT", already_open, valid_exit, data_blocked
    if not _is_pending_fill(fill_status):
        return "NEEDS_REVIEW", "REVIEW_WARNING", already_open, valid_exit, data_blocked
    if action == "BUY":
        if already_open:
            return "BLOCKED", "BLOCK_DO_NOT_ACCEPT", already_open, valid_exit, data_blocked
        if not _is_review_decision(user_decision):
            return "NEEDS_REVIEW", "REVIEW_WARNING", already_open, valid_exit, data_blocked
        if has_nonblocking_warning:
            return "NEEDS_REVIEW", "REVIEW_WARNING", already_open, valid_exit, data_blocked
        return "ACCEPT_ELIGIBLE", "ACCEPT_ELIGIBLE", already_open, valid_exit, data_blocked
    if action == "SELL":
        if not valid_exit:
            return "BLOCKED", "BLOCK_DO_NOT_ACCEPT", already_open, valid_exit, data_blocked
        if has_nonblocking_warning:
            return "NEEDS_REVIEW", "REVIEW_WARNING", already_open, valid_exit, data_blocked
        return "ACCEPT_ELIGIBLE", "ACCEPT_ELIGIBLE", already_open, valid_exit, data_blocked
    return "NEEDS_REVIEW", "REVIEW_WARNING", already_open, valid_exit, data_blocked


def build_paper_decision_inbox(latest_dir: str | Path) -> pd.DataFrame:
    latest = Path(latest_dir)
    signals = _read_csv(latest / "paper_signal_report.csv")
    pending = _read_csv(latest / "paper_pending_orders.csv")
    review = _read_csv(latest / "paper_order_review.csv")
    trade_log = _read_csv(latest / "paper_trade_log.csv")
    policy = _read_csv(latest / "data_policy_decisions.csv")
    decisions = _review_lookup(review)
    pending_rows = _pending_lookup(pending)
    open_symbols = _open_symbols(trade_log)
    blocked_symbols, warning_symbols = _data_policy_maps(policy)
    candidates = _candidate_rows(signals, pending)
    rows = []
    for _, source in candidates.iterrows():
        key = _decision_key(source)
        pending_source = pending_rows.get(key)
        row = source.copy()
        if pending_source is not None:
            for col in pending_source.index:
                if col not in row or not _text(row.get(col, "")):
                    row[col] = pending_source[col]
            if not _text(row.get("fill_status", "")):
                row["fill_status"] = pending_source.get("status", "")
        review_decision = decisions.get(key)
        if review_decision and _text(row.get("user_decision", "")).upper() in {"", "HOLD_REVIEW", "REVIEW_REQUIRED"}:
            row["user_decision"] = review_decision
        status, recommended, already_open, valid_exit, data_blocked = _classify(
            row,
            open_symbols,
            blocked_symbols,
            warning_symbols,
        )
        rows.append(
            {
                "symbol": _text(row.get("symbol", "")).upper(),
                "action": _text(row.get("action", "")).upper(),
                "strategy_name": _text(row.get("strategy_name", "")),
                "signal_date": _text(row.get("signal_date", "")),
                "fill_status": _text(row.get("fill_status", row.get("status", ""))).upper(),
                "user_decision": _text(row.get("user_decision", "HOLD_REVIEW")).upper(),
                "paper_rank": row.get("paper_rank", ""),
                "ranking_score": row.get("ranking_score", ""),
                "liquidity_guard_pass": _bool_value(row.get("liquidity_guard_pass", False)),
                "already_open_position": already_open,
                "valid_exit_signal": valid_exit,
                "data_policy_blocked": data_blocked,
                "warning": _text(row.get("warning", "")),
                "reason": _text(row.get("reason", "")),
                "eligibility_status": status,
                "recommended_operator_action": recommended,
            }
        )
    return pd.DataFrame(rows, columns=INBOX_COLUMNS)


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "none"
    columns = list(frame.columns)
    lines = [
        "| " + " | ".join(str(col) for col in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for _, row in frame.iterrows():
        values = [str(row.get(col, "")).replace("|", "/") for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _metric_table(values: dict[str, object]) -> str:
    rows = [{"metric": key, "value": value} for key, value in values.items()]
    return _markdown_table(pd.DataFrame(rows, columns=["metric", "value"]))


def _safety_flags(latest_dir: Path) -> dict[str, object]:
    try:
        return build_paper_ops_dashboard(latest_dir).get("safety_gates", {})
    except Exception:
        return {
            "broker_submit_enabled": "unknown",
            "live_trading_enabled": "unknown",
            "short_trading_enabled": "unknown",
            "submit_order_called": "unknown",
            "post_request_used": "unknown",
        }


def write_paper_decision_inbox(latest_dir: str | Path) -> dict[str, object]:
    latest = Path(latest_dir)
    latest.mkdir(parents=True, exist_ok=True)
    inbox = build_paper_decision_inbox(latest)
    csv_path = latest / "paper_decision_inbox.csv"
    md_path = latest / "paper_decision_inbox.md"
    inbox.to_csv(csv_path, index=False)
    recommended = inbox.get("recommended_operator_action", pd.Series(dtype=str))
    fill_status = inbox.get("fill_status", pd.Series(dtype=str)).astype(str).str.upper()
    action = inbox.get("action", pd.Series(dtype=str)).astype(str).str.upper()
    summary = {
        "total_decision_candidates": int(len(inbox)),
        "accept_eligible_buy_count": int(((recommended == "ACCEPT_ELIGIBLE") & action.eq("BUY")).sum()),
        "accept_eligible_sell_exit_count": int(((recommended == "ACCEPT_ELIGIBLE") & action.eq("SELL")).sum()),
        "blocked_count": int((recommended == "BLOCK_DO_NOT_ACCEPT").sum()),
        "needs_review_count": int((recommended == "REVIEW_WARNING").sum()),
        "already_accepted_count": int((recommended == "ALREADY_ACCEPTED").sum()),
        "pending_next_open_count": int(fill_status.isin(["PENDING", "PENDING_NEXT_OPEN"]).sum()),
        "wait_for_next_open_count": int((recommended == "WAIT_FOR_NEXT_OPEN").sum()),
    }
    safety = _safety_flags(latest)
    lines = [
        "# Paper Decision Inbox",
        "",
        "## Summary",
        "",
        _metric_table(summary),
        "",
        "## ACCEPT_ELIGIBLE",
        "",
        _markdown_table(inbox[inbox["recommended_operator_action"].eq("ACCEPT_ELIGIBLE")]),
        "",
        "## REVIEW_WARNING",
        "",
        _markdown_table(inbox[inbox["recommended_operator_action"].eq("REVIEW_WARNING")]),
        "",
        "## BLOCK_DO_NOT_ACCEPT",
        "",
        _markdown_table(inbox[inbox["recommended_operator_action"].eq("BLOCK_DO_NOT_ACCEPT")]),
        "",
        "## ALREADY_ACCEPTED / WAIT_FOR_NEXT_OPEN",
        "",
        _markdown_table(inbox[inbox["recommended_operator_action"].isin(["ALREADY_ACCEPTED", "WAIT_FOR_NEXT_OPEN"])]),
        "",
        "## Safety Footer",
        "",
        _metric_table(
            {
                "broker_submit_enabled": safety.get("broker_submit_enabled", "unknown"),
                "live_trading_enabled": safety.get("live_trading_enabled", "unknown"),
                "short_trading_enabled": safety.get("short_trading_enabled", "unknown"),
                "submit_order_called": safety.get("submit_order_called", "unknown"),
                "post_request_used": safety.get("post_request_used", "unknown"),
            }
        ),
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"summary": summary, "csv_path": str(csv_path), "md_path": str(md_path)}
