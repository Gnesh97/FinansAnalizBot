"""Fail-fast provider preflight and resumable V2 gap-recovery primitives.

This module is deliberately independent from strategy, benchmark, broker, and
paper-trading code. Network operations are injected by callers so the safety
contract can be tested without contacting Alpaca.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import socket
import ssl
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


PREFLIGHT_OK = "PREFLIGHT_OK"
PREFLIGHT_BLOCKED_DNS = "PREFLIGHT_BLOCKED_DNS"
PREFLIGHT_BLOCKED_SOCKET = "PREFLIGHT_BLOCKED_SOCKET"
PREFLIGHT_BLOCKED_TLS = "PREFLIGHT_BLOCKED_TLS"
PREFLIGHT_CREDENTIALS_MISSING = "PREFLIGHT_CREDENTIALS_MISSING"
PREFLIGHT_AUTH_FAILED = "PREFLIGHT_AUTH_FAILED"
PREFLIGHT_PROVIDER_ERROR = "PREFLIGHT_PROVIDER_ERROR"
PREFLIGHT_EMPTY_BUT_REACHABLE = "PREFLIGHT_EMPTY_BUT_REACHABLE"
PREFLIGHT_UNKNOWN_FAILURE = "PREFLIGHT_UNKNOWN_FAILURE"

APPROVED_TARGET_DATES = (
    "2021-04-19",
    "2021-10-25",
    "2022-03-08",
    "2022-04-06",
)
BENCHMARK_SYMBOLS = frozenset({"QQQ", "SPY"})
RECOVERY_STATUSES = frozenset(
    {
        "PLANNED",
        "REQUESTING",
        "COMPLETE",
        "EMPTY_RESPONSE",
        "INVALID_RESPONSE",
        "FAILED_TRANSIENT",
        "FAILED_SYSTEMIC",
        "SKIPPED_ALREADY_COMPLETE",
        "INTERRUPTED",
    }
)
TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
AUTH_HTTP_STATUSES = frozenset({401, 403})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(payload: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_write_text(text: str, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _http_status(error: BaseException) -> int | None:
    for value in (
        getattr(error, "status_code", None),
        getattr(error, "status", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _is_winerror_10013(error: BaseException) -> bool:
    return getattr(error, "winerror", None) == 10013 or (
        isinstance(error, OSError) and getattr(error, "errno", None) == 10013
    )


def _is_systemic_network_error(error: BaseException) -> bool:
    return _is_winerror_10013(error) or isinstance(
        error,
        (socket.gaierror, ssl.SSLError, PermissionError, ConnectionError),
    )


def _is_transient_provider_error(error: BaseException) -> bool:
    return _http_status(error) in TRANSIENT_HTTP_STATUSES or isinstance(
        error,
        (TimeoutError, ConnectionResetError),
    )


def _safe_error(error: BaseException) -> tuple[str, str]:
    return type(error).__name__, str(error)[:500]


def _result_row_count(result: object) -> int:
    if result is None:
        return 0
    if hasattr(result, "empty") and bool(getattr(result, "empty")):
        return 0
    try:
        return len(result)  # type: ignore[arg-type]
    except TypeError:
        return 1


def _write_preflight_reports(result: dict, report_dir: Path) -> dict[str, Path]:
    report_dir = Path(report_dir)
    json_path = report_dir / "rs_v2_provider_preflight.json"
    markdown_path = report_dir / "rs_v2_provider_preflight.md"
    _atomic_write_json(result, json_path)
    lines = ["# RS V2 Provider Preflight", ""]
    for field in (
        "status",
        "host",
        "port",
        "dns_status",
        "tcp_status",
        "tls_status",
        "credential_status",
        "authenticated_request_status",
        "symbol",
        "request_start",
        "request_end",
        "provider_request_count",
        "retry_count",
        "response_row_count",
        "error_type",
        "error_message",
    ):
        lines.append(f"- {field}: {result.get(field, '')}")
    lines.extend(
        [
            "- credentials_recorded: false",
            "- provider_post_used: false",
            "",
        ]
    )
    _atomic_write_text("\n".join(lines), markdown_path)
    return {"json": json_path, "markdown": markdown_path}


def run_provider_preflight(
    *,
    dns_probe: Callable[[str], object],
    tcp_probe: Callable[[str, int, float], object],
    tls_probe: Callable[[str, int, float], object],
    credential_loader: Callable[[], tuple[str, str]],
    authenticated_request: Callable[[tuple[str, str], str, str, str], object],
    request_start: str,
    request_end: str,
    host: str = "data.alpaca.markets",
    port: int = 443,
    timeout_seconds: float = 5.0,
    symbol: str = "QQQ",
    max_retries: int = 0,
    report_dir: Path | None = None,
) -> dict:
    """Run ordered DNS/TCP/TLS/credential/auth checks with bounded retries."""
    if max_retries not in {0, 1}:
        raise ValueError("PREFLIGHT_MAX_RETRIES_MUST_BE_ZERO_OR_ONE")
    result = {
        "status": PREFLIGHT_UNKNOWN_FAILURE,
        "generated_at": _utc_now(),
        "host": host,
        "port": port,
        "dns_status": "NOT_RUN",
        "tcp_status": "NOT_RUN",
        "tls_status": "NOT_RUN",
        "credential_status": "NOT_RUN",
        "authenticated_request_status": "NOT_RUN",
        "symbol": symbol,
        "request_start": request_start,
        "request_end": request_end,
        "provider_request_count": 0,
        "retry_count": 0,
        "response_row_count": 0,
        "error_type": "",
        "error_message": "",
    }

    def finish(status: str, error: BaseException | None = None) -> dict:
        result["status"] = status
        if error is not None:
            result["error_type"], result["error_message"] = _safe_error(error)
        if report_dir is not None:
            _write_preflight_reports(result, report_dir)
        return result

    try:
        dns_probe(host)
        result["dns_status"] = "OK"
    except Exception as error:
        result["dns_status"] = "BLOCKED"
        return finish(PREFLIGHT_BLOCKED_DNS, error)

    try:
        tcp_probe(host, port, timeout_seconds)
        result["tcp_status"] = "OK"
    except Exception as error:
        result["tcp_status"] = "BLOCKED"
        return finish(PREFLIGHT_BLOCKED_SOCKET, error)

    try:
        tls_probe(host, port, timeout_seconds)
        result["tls_status"] = "OK"
    except Exception as error:
        result["tls_status"] = "BLOCKED"
        return finish(PREFLIGHT_BLOCKED_TLS, error)

    try:
        credentials = credential_loader()
        if (
            not isinstance(credentials, tuple)
            or len(credentials) != 2
            or not all(isinstance(value, str) and value.strip() for value in credentials)
        ):
            raise RuntimeError("provider credentials missing")
        result["credential_status"] = "AVAILABLE"
    except Exception as error:
        result["credential_status"] = "MISSING"
        return finish(PREFLIGHT_CREDENTIALS_MISSING, error)

    for attempt in range(max_retries + 1):
        result["provider_request_count"] += 1
        try:
            response = authenticated_request(
                credentials,
                symbol,
                request_start,
                request_end,
            )
        except Exception as error:
            status_code = _http_status(error)
            if status_code in AUTH_HTTP_STATUSES:
                result["authenticated_request_status"] = "AUTH_FAILED"
                return finish(PREFLIGHT_AUTH_FAILED, error)
            if _is_winerror_10013(error) or isinstance(error, PermissionError):
                result["authenticated_request_status"] = "BLOCKED_SOCKET"
                return finish(PREFLIGHT_BLOCKED_SOCKET, error)
            if isinstance(error, socket.gaierror):
                result["authenticated_request_status"] = "BLOCKED_DNS"
                return finish(PREFLIGHT_BLOCKED_DNS, error)
            if isinstance(error, ssl.SSLError):
                result["authenticated_request_status"] = "BLOCKED_TLS"
                return finish(PREFLIGHT_BLOCKED_TLS, error)
            if _is_transient_provider_error(error) and attempt < max_retries:
                result["retry_count"] += 1
                continue
            if status_code is not None or _is_transient_provider_error(error):
                result["authenticated_request_status"] = "PROVIDER_ERROR"
                return finish(PREFLIGHT_PROVIDER_ERROR, error)
            if _is_systemic_network_error(error):
                result["authenticated_request_status"] = "BLOCKED_SOCKET"
                return finish(PREFLIGHT_BLOCKED_SOCKET, error)
            result["authenticated_request_status"] = "UNKNOWN_FAILURE"
            return finish(PREFLIGHT_UNKNOWN_FAILURE, error)
        row_count = _result_row_count(response)
        result["response_row_count"] = row_count
        if row_count == 0:
            result["authenticated_request_status"] = "EMPTY"
            return finish(PREFLIGHT_EMPTY_BUT_REACHABLE)
        result["authenticated_request_status"] = "OK"
        return finish(PREFLIGHT_OK)
    return finish(PREFLIGHT_UNKNOWN_FAILURE)


def default_dns_probe(host: str) -> list[tuple]:
    return socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)


def default_tcp_probe(host: str, port: int, timeout_seconds: float) -> None:
    with socket.create_connection((host, port), timeout=timeout_seconds):
        return None


def default_tls_probe(host: str, port: int, timeout_seconds: float) -> None:
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout_seconds) as raw:
        with context.wrap_socket(raw, server_hostname=host):
            return None


def _validate_targets(targets: list[dict], expected_target_count: int) -> None:
    pairs = {
        (str(row.get("symbol", "")), str(row.get("target_session", "")))
        for row in targets
    }
    symbols = {symbol for symbol, _ in pairs}
    dates = {session for _, session in pairs}
    if (
        len(targets) != expected_target_count
        or len(pairs) != expected_target_count
        or dates != set(APPROVED_TARGET_DATES)
        or BENCHMARK_SYMBOLS & symbols
        or any(row.get("asset_role") != "CANDIDATE" for row in targets)
    ):
        raise ValueError("GAP_RECOVERY_TARGET_CONTRACT_MISMATCH")
    if expected_target_count == 350 and len(symbols) != 123:
        raise ValueError("GAP_RECOVERY_TARGET_CONTRACT_MISMATCH")


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _batch_id(session: str, endpoint: str, index: int, symbols: list[str]) -> str:
    digest = hashlib.sha256(",".join(symbols).encode("utf-8")).hexdigest()[:10]
    return f"{session.replace('-', '')}_{endpoint}_{index:03d}_{digest}"


def build_batched_recovery_plan(
    targets: list[dict],
    *,
    batch_size: int = 20,
    retry_limit: int = 0,
    run_id: str,
    expected_target_count: int = 350,
) -> dict:
    """Group exact symbol/session pairs into daily and raw multi-symbol batches."""
    if not 10 <= batch_size <= 30:
        raise ValueError("GAP_RECOVERY_BATCH_SIZE_OUT_OF_RANGE")
    if retry_limit not in {0, 1}:
        raise ValueError("GAP_RECOVERY_RETRY_LIMIT_OUT_OF_RANGE")
    if not run_id.startswith("gaprec_"):
        raise ValueError("GAP_RECOVERY_RUN_ID_INVALID")
    _validate_targets(targets, expected_target_count)
    by_session: dict[str, list[str]] = {}
    for row in targets:
        by_session.setdefault(str(row["target_session"]), []).append(
            str(row["symbol"])
        )
    batches: list[dict] = []
    for session in APPROVED_TARGET_DATES:
        symbols = sorted(set(by_session[session]))
        for endpoint in ("daily", "raw_5min"):
            for index, chunk in enumerate(_chunks(symbols, batch_size), start=1):
                batches.append(
                    {
                        "batch_id": _batch_id(session, endpoint, index, chunk),
                        "target_session": session,
                        "endpoint": endpoint,
                        "symbols": chunk,
                        "logical_pair_count": len(chunk),
                        "status": "PLANNED",
                    }
                )
    daily_count = sum(batch["endpoint"] == "daily" for batch in batches)
    raw_count = len(batches) - daily_count
    request_count = len(batches) * (1 + retry_limit)
    return {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "generated_at": _utc_now(),
        "batch_size": batch_size,
        "retry_limit": retry_limit,
        "batches": batches,
        "summary": {
            "logical_target_count": len(targets),
            "daily_batch_count": daily_count,
            "raw_batch_count": raw_count,
            "maximum_provider_requests": request_count,
            "maximum_retries": len(batches) * retry_limit,
            "estimated_files": len(targets) * 2,
            "blocked_preflight_recovery_request_count": 0,
        },
    }


def select_deterministic_canary(targets: list[dict]) -> list[dict]:
    """Select one stable pair per affected date, preferring WBD on 2022-04-06."""
    candidates = sorted(
        targets,
        key=lambda row: (str(row.get("target_session")), str(row.get("symbol"))),
    )
    selected: list[dict] = []
    for session in APPROVED_TARGET_DATES:
        rows = [row for row in candidates if row.get("target_session") == session]
        if session == "2022-04-06":
            wbd = [row for row in rows if row.get("symbol") == "WBD"]
            rows = wbd or rows
        if not rows:
            raise ValueError("GAP_RECOVERY_CANARY_CONTRACT_MISMATCH")
        selected.append(dict(rows[0]))
    _validate_targets(selected, 4)
    return selected


def write_batched_recovery_plan(
    plan: dict,
    report_dir: Path,
    staging_root: Path,
) -> dict[str, Path]:
    report_dir = Path(report_dir)
    csv_path = report_dir / "rs_v2_gap_recovery_batched_plan.csv"
    markdown_path = report_dir / "rs_v2_gap_recovery_batched_plan.md"
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_temp = csv_path.with_name(f".{csv_path.name}.tmp")
    with csv_temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "batch_id",
                "target_session",
                "endpoint",
                "symbol_count",
                "symbols",
                "status",
                "staging_run_root",
            ),
        )
        writer.writeheader()
        staging_run_root = Path(staging_root) / "runs" / plan["run_id"]
        for batch in plan["batches"]:
            writer.writerow(
                {
                    "batch_id": batch["batch_id"],
                    "target_session": batch["target_session"],
                    "endpoint": batch["endpoint"],
                    "symbol_count": len(batch["symbols"]),
                    "symbols": ",".join(batch["symbols"]),
                    "status": batch["status"],
                    "staging_run_root": staging_run_root.as_posix(),
                }
            )
    os.replace(csv_temp, csv_path)
    lines = ["# RS V2 Gap Recovery Batched Plan", ""]
    lines.extend(f"- {key}: {value}" for key, value in plan["summary"].items())
    lines.extend(
        [
            f"- batch_size: {plan['batch_size']}",
            f"- retry_limit: {plan['retry_limit']}",
            f"- staging_run_root: {(Path(staging_root) / 'runs' / plan['run_id']).as_posix()}",
            "- preflight_required_before_provider_requests: true",
            "- blocked_environment_retry_storm_possible: false",
            "",
        ]
    )
    _atomic_write_text("\n".join(lines), markdown_path)
    return {"csv": csv_path, "markdown": markdown_path}


def _plan_digest(plan: dict) -> str:
    stable_plan = {key: value for key, value in plan.items() if key != "generated_at"}
    payload = json.dumps(stable_plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def initialize_recovery_checkpoint(
    path: Path,
    plan: dict,
    *,
    resume: bool = False,
) -> dict:
    path = Path(path)
    digest = _plan_digest(plan)
    if path.exists():
        if not resume:
            raise FileExistsError("RECOVERY_CHECKPOINT_EXISTS")
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if (
            checkpoint.get("run_id") != plan.get("run_id")
            or checkpoint.get("plan_digest") != digest
        ):
            raise ValueError("RECOVERY_CHECKPOINT_PLAN_MISMATCH")
        changed = False
        for record in checkpoint.get("batches", {}).values():
            if record.get("status") == "REQUESTING":
                record["status"] = "INTERRUPTED"
                record["updated_at"] = _utc_now()
                changed = True
        if changed:
            checkpoint["updated_at"] = _utc_now()
            _atomic_write_json(checkpoint, path)
        return checkpoint
    checkpoint = {
        "schema_version": "1.0.0",
        "run_id": plan["run_id"],
        "plan_digest": digest,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "batches": {
            batch["batch_id"]: {
                "status": "PLANNED",
                "updated_at": _utc_now(),
                "attempt_count": 0,
                "retry_count": 0,
            }
            for batch in plan["batches"]
        },
    }
    _atomic_write_json(checkpoint, path)
    return checkpoint


def update_batch_checkpoint(
    path: Path,
    batch_id: str,
    status: str,
    evidence: dict | None = None,
) -> dict:
    if status not in RECOVERY_STATUSES:
        raise ValueError("RECOVERY_STATUS_INVALID")
    path = Path(path)
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if batch_id not in checkpoint.get("batches", {}):
        raise KeyError("RECOVERY_BATCH_NOT_FOUND")
    current = checkpoint["batches"][batch_id]
    if current.get("status") == "COMPLETE" and status != "COMPLETE":
        raise ValueError("RECOVERY_COMPLETE_BATCH_IMMUTABLE")
    checkpoint["batches"][batch_id] = {
        **current,
        **(evidence or {}),
        "status": status,
        "updated_at": _utc_now(),
    }
    checkpoint["updated_at"] = _utc_now()
    _atomic_write_json(checkpoint, path)
    return checkpoint


class RequestBudget:
    def __init__(self, maximum_requests: int):
        if maximum_requests < 0:
            raise ValueError("REQUEST_BUDGET_INVALID")
        self.maximum_requests = maximum_requests
        self._used = 0
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def claim(self) -> int:
        with self._lock:
            if self._used >= self.maximum_requests:
                raise RuntimeError("REQUEST_BUDGET_EXHAUSTED")
            self._used += 1
            return self._used


class SystemicCircuitBreaker:
    def __init__(self, threshold: int = 3):
        if threshold < 1:
            raise ValueError("CIRCUIT_BREAKER_THRESHOLD_INVALID")
        self.threshold = threshold
        self._counts: dict[str, int] = {}
        self._open = False
        self._signature = ""
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open

    @property
    def signature(self) -> str:
        with self._lock:
            return self._signature

    def record_failure(self, error: BaseException) -> None:
        if not _is_systemic_network_error(error):
            return
        signature = (
            f"{type(error).__name__}|{getattr(error, 'winerror', '')}|{str(error)}"
        )
        with self._lock:
            self._counts[signature] = self._counts.get(signature, 0) + 1
            if _is_winerror_10013(error) or self._counts[signature] >= self.threshold:
                self._open = True
                self._signature = signature


def _attempt_batch(
    batch: dict,
    request_batch: Callable[[dict], dict],
    retry_limit: int,
    budget: RequestBudget,
    breaker: SystemicCircuitBreaker,
) -> dict:
    attempts = 0
    retries = 0
    while attempts <= retry_limit:
        try:
            budget.claim()
        except RuntimeError as error:
            return {
                "status": "INTERRUPTED",
                "attempt_count": attempts,
                "retry_count": retries,
                "error_type": type(error).__name__,
                "error_message": str(error),
            }
        attempts += 1
        try:
            response = request_batch(batch)
            status = str(response.get("status", "INVALID_RESPONSE"))
            if status not in RECOVERY_STATUSES:
                status = "INVALID_RESPONSE"
            return {
                **response,
                "status": status,
                "attempt_count": attempts,
                "retry_count": retries,
            }
        except Exception as error:
            breaker.record_failure(error)
            transient = _is_transient_provider_error(error)
            if transient and not breaker.is_open and attempts <= retry_limit:
                retries += 1
                continue
            error_type, error_message = _safe_error(error)
            return {
                "status": (
                    "FAILED_SYSTEMIC"
                    if _is_systemic_network_error(error)
                    else "FAILED_TRANSIENT"
                    if transient
                    else "INVALID_RESPONSE"
                ),
                "attempt_count": attempts,
                "retry_count": retries,
                "error_type": error_type,
                "error_message": error_message,
            }
    raise AssertionError("unreachable")


def execute_recovery_batches(
    plan: dict,
    checkpoint_path: Path,
    request_batch: Callable[[dict], dict],
    *,
    workers: int = 2,
    request_budget: int,
    retry_limit: int = 0,
    resume: bool = False,
    systemic_threshold: int = 3,
) -> dict:
    """Execute a bounded window of batches and checkpoint every transition."""
    if not 1 <= workers <= 4:
        raise ValueError("RECOVERY_WORKERS_OUT_OF_RANGE")
    if retry_limit not in {0, 1}:
        raise ValueError("GAP_RECOVERY_RETRY_LIMIT_OUT_OF_RANGE")
    checkpoint_path = Path(checkpoint_path)
    checkpoint = initialize_recovery_checkpoint(
        checkpoint_path,
        plan,
        resume=resume,
    )
    complete_ids = {
        batch_id
        for batch_id, record in checkpoint["batches"].items()
        if record.get("status") == "COMPLETE"
    }
    pending = [
        batch for batch in plan["batches"] if batch["batch_id"] not in complete_ids
    ]
    prior_attempt_count = sum(
        int(record.get("attempt_count", 0))
        for record in checkpoint["batches"].values()
    )
    if prior_attempt_count > request_budget:
        raise ValueError("RECOVERY_REQUEST_BUDGET_ALREADY_EXCEEDED")
    budget = RequestBudget(request_budget - prior_attempt_count)
    breaker = SystemicCircuitBreaker(systemic_threshold)
    in_flight: dict[Future, dict] = {}
    finished: list[dict] = [
        {
            "batch_id": batch_id,
            **checkpoint["batches"][batch_id],
            "checkpoint_status": "COMPLETE",
            "status": "SKIPPED_ALREADY_COMPLETE",
        }
        for batch_id in sorted(complete_ids)
    ]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        while pending or in_flight:
            while pending and len(in_flight) < workers and not breaker.is_open:
                batch = pending.pop(0)
                update_batch_checkpoint(
                    checkpoint_path,
                    batch["batch_id"],
                    "REQUESTING",
                )
                future = executor.submit(
                    _attempt_batch,
                    batch,
                    request_batch,
                    retry_limit,
                    budget,
                    breaker,
                )
                in_flight[future] = batch
            if not in_flight:
                break
            completed, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
            for future in completed:
                batch = in_flight.pop(future)
                evidence = future.result()
                prior_record = checkpoint["batches"][batch["batch_id"]]
                evidence = {
                    **evidence,
                    "attempt_count": int(prior_record.get("attempt_count", 0))
                    + int(evidence.get("attempt_count", 0)),
                    "retry_count": int(prior_record.get("retry_count", 0))
                    + int(evidence.get("retry_count", 0)),
                }
                update_batch_checkpoint(
                    checkpoint_path,
                    batch["batch_id"],
                    evidence["status"],
                    evidence,
                )
                finished.append({"batch_id": batch["batch_id"], **evidence})

    if pending:
        for batch in pending:
            update_batch_checkpoint(
                checkpoint_path,
                batch["batch_id"],
                "INTERRUPTED",
                {"error_message": "SYSTEMIC_CIRCUIT_BREAKER_OPEN"},
            )
    final_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    cumulative_attempt_count = sum(
        int(record.get("attempt_count", 0))
        for record in final_checkpoint["batches"].values()
    )
    cumulative_retry_count = sum(
        int(record.get("retry_count", 0))
        for record in final_checkpoint["batches"].values()
    )
    final_statuses = [
        str(record.get("status"))
        for record in final_checkpoint["batches"].values()
    ]
    interrupted_count = sum(status == "INTERRUPTED" for status in final_statuses)
    terminal_count = sum(
        status not in {"PLANNED", "REQUESTING", "INTERRUPTED"}
        for status in final_statuses
    )
    return {
        "completed_batch_count": sum(
            row.get("status") == "COMPLETE" for row in finished
        ),
        "skipped_already_complete_count": len(complete_ids),
        "provider_request_count": cumulative_attempt_count,
        "retry_count": cumulative_retry_count,
        "systemic_circuit_breaker_fired": breaker.is_open,
        "systemic_error_signature": breaker.signature,
        "planned_batch_count": len(plan["batches"]),
        "terminal_batch_count": terminal_count,
        "interrupted_batch_count": interrupted_count,
        "results": finished,
    }


def can_run_full_acquisition(
    preflight_status: str,
    canary_status: str,
    recovery_status: str,
) -> bool:
    return (
        preflight_status == PREFLIGHT_OK
        and canary_status == "CANARY_PASSED"
        and recovery_status == "FULL_RECOVERY_SUFFICIENT"
    )


def can_run_canonical_backtest(manifest_status: str) -> bool:
    return manifest_status == "COMPLETE_VALIDATED"
