"""Provider-backed runner for the fail-fast V2 gap-recovery engine."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    from .v2_provider_contract_forensics import (
        build_request_window,
        load_resolved_provider_contract,
        persist_provider_response_evidence,
    )
    from .v2_provider_recovery import (
        build_batched_recovery_plan,
        execute_recovery_batches,
        select_deterministic_canary,
    )
    from .vericekici_updated import (
        GAP_RECOVERY_AUDIT_RUN,
        _atomic_write_json,
        _frozen_hashes,
        _gap_recovery_windows,
        _load_alpaca,
        atomic_write_csv,
        build_gap_recovery_targets,
        download_bars_batch,
        frozen_snapshot_digest,
        get_adjustment,
        get_credentials,
        get_data_feed,
        load_provider_preflight_evidence,
        resolve_gap_recovery_run_paths,
        validate_daily_recovery,
        validate_gap_recovery_invocation,
        validate_raw_recovery,
    )
except ImportError:
    from v2_provider_contract_forensics import (
        build_request_window,
        load_resolved_provider_contract,
        persist_provider_response_evidence,
    )
    from v2_provider_recovery import (
        build_batched_recovery_plan,
        execute_recovery_batches,
        select_deterministic_canary,
    )
    from vericekici_updated import (
        GAP_RECOVERY_AUDIT_RUN,
        _atomic_write_json,
        _frozen_hashes,
        _gap_recovery_windows,
        _load_alpaca,
        atomic_write_csv,
        build_gap_recovery_targets,
        download_bars_batch,
        frozen_snapshot_digest,
        get_adjustment,
        get_credentials,
        get_data_feed,
        load_provider_preflight_evidence,
        resolve_gap_recovery_run_paths,
        validate_daily_recovery,
        validate_gap_recovery_invocation,
        validate_raw_recovery,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _atomic_write_text(text: str, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_staged_frame(
    frame: pd.DataFrame,
    path: Path,
    *,
    allow_overwrite: bool,
) -> None:
    """Write staging evidence without replacing prior bytes by default."""
    path = Path(path)
    if path.exists() and not allow_overwrite:
        raise FileExistsError("RECOVERY_STAGING_OVERWRITE_BLOCKED")
    atomic_write_csv(frame, path)


def provider_frame_metadata(frame: pd.DataFrame) -> dict:
    """Return compact timestamp evidence even when target validation fails."""
    if frame.empty or "Date" not in frame.columns:
        return {
            "provider_row_count": len(frame),
            "provider_first_timestamp": "",
            "provider_last_timestamp": "",
            "provider_timestamp_sha256": hashlib.sha256(b"").hexdigest(),
        }
    timestamps = pd.to_datetime(frame["Date"], errors="coerce", utc=True).dropna()
    values = [value.isoformat() for value in timestamps]
    payload = "\n".join(values).encode("utf-8")
    return {
        "provider_row_count": len(frame),
        "provider_first_timestamp": values[0] if values else "",
        "provider_last_timestamp": values[-1] if values else "",
        "provider_timestamp_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _canary_outcome(daily_status: str, raw_status: str) -> str:
    invalid_tokens = ("INVALID", "WRONG", "DUPLICATE")
    if any(token in daily_status or token in raw_status for token in invalid_tokens):
        return "CANARY_INVALID"
    daily_valid = daily_status == "DAILY_RECOVERED_VALID"
    raw_valid = raw_status == "RAW_RECOVERED_COMPLETE"
    if daily_valid and raw_valid:
        return "CANARY_DAILY_AND_RAW_VALID"
    if daily_valid:
        return "CANARY_DAILY_ONLY_VALID"
    if raw_valid:
        return "CANARY_RAW_ONLY_VALID"
    if "FAILED" in daily_status or "FAILED" in raw_status:
        return "CANARY_REQUEST_FAILED"
    return "CANARY_EMPTY"


def _full_outcome(daily_status: str, raw_status: str) -> str:
    invalid_tokens = ("INVALID", "WRONG", "DUPLICATE")
    if any(token in daily_status or token in raw_status for token in invalid_tokens):
        return "INVALID_EVIDENCE"
    if "SYSTEMIC" in daily_status or "SYSTEMIC" in raw_status:
        return "FAILED_SYSTEMIC"
    if "FAILED" in daily_status or "FAILED" in raw_status:
        return "FAILED_TRANSIENT"
    daily_valid = daily_status == "DAILY_RECOVERED_VALID"
    raw_valid = raw_status == "RAW_RECOVERED_COMPLETE"
    if daily_valid and raw_valid:
        return "RECOVERED_DAILY_AND_RAW"
    if daily_valid:
        return "RECOVERED_DAILY_ONLY"
    if raw_valid:
        return "RECOVERED_RAW_ONLY"
    return "RECOVERED_NEITHER"


def _is_sha256(value: object) -> bool:
    text = str(value)
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text.lower())


def _request_evidence_complete(
    record: dict,
    request_contract: str,
    run_root: Path,
) -> bool:
    provider_rows = record.get("provider_row_count")
    metadata_complete = bool(
        record
        and record.get("request_window_contract") == request_contract
        and record.get("request_start")
        and record.get("request_end")
        and record.get("provider_evidence_relative_path")
        and _is_sha256(record.get("provider_evidence_sha256"))
        and _is_sha256(record.get("provider_timestamp_sha256"))
        and isinstance(provider_rows, int)
        and provider_rows >= 0
    )
    if not metadata_complete:
        return False
    relative_path = Path(str(record["provider_evidence_relative_path"]))
    root = Path(run_root).resolve()
    if relative_path.is_absolute():
        return False
    evidence_path = (root / relative_path).resolve()
    if not evidence_path.is_relative_to(root) or not evidence_path.is_file():
        return False
    try:
        if _hash_file(evidence_path) != record["provider_evidence_sha256"]:
            return False
        frame = pd.read_csv(evidence_path)
    except (OSError, pd.errors.ParserError, UnicodeError):
        return False
    metadata = provider_frame_metadata(frame)
    return bool(
        metadata["provider_row_count"] == provider_rows
        and metadata["provider_timestamp_sha256"]
        == record["provider_timestamp_sha256"]
        and metadata["provider_first_timestamp"]
        == record.get("provider_first_timestamp", "")
        and metadata["provider_last_timestamp"]
        == record.get("provider_last_timestamp", "")
    )


def _endpoint_evidence_state(
    record: dict,
    endpoint: str,
    request_contract: str,
    run_root: Path,
) -> str:
    if not _request_evidence_complete(record, request_contract, run_root):
        return "INCOMPLETE"
    status = str(record.get("validation_status", ""))
    row_count = record.get("row_count")
    provider_rows = record.get("provider_row_count")
    valid_status = (
        "DAILY_RECOVERED_VALID"
        if endpoint == "daily"
        else "RAW_RECOVERED_COMPLETE"
    )
    empty_status = "DAILY_EMPTY_RESPONSE" if endpoint == "daily" else "RAW_EMPTY_RESPONSE"
    if status == valid_status:
        if (
            isinstance(row_count, int)
            and row_count > 0
            and provider_rows >= row_count
            and record.get("provider_first_timestamp")
            and record.get("provider_last_timestamp")
        ):
            return "VALID"
        return "INCOMPLETE"
    if status == empty_status:
        return "EMPTY" if row_count == 0 and provider_rows == 0 else "INCOMPLETE"
    if any(token in status for token in ("INVALID", "WRONG", "DUPLICATE")):
        return "INVALID"
    if "FAILED" in status and record.get("provider_error_class"):
        return "REQUEST_FAILED"
    return "INCOMPLETE"


def summarize_pair_evidence(
    targets: list[dict],
    records: list[dict],
    *,
    mode: str,
    request_contract: str,
    run_root: Path,
) -> dict:
    """Reduce batch evidence to one fail-closed row per target pair."""
    if mode not in {"canary", "full"}:
        raise ValueError("GAP_RECOVERY_MODE_INVALID")
    evidence: dict[tuple[str, str, str], list[dict]] = {}
    for record in records:
        key = (
            str(record.get("symbol")),
            str(record.get("target_session")),
            str(record.get("endpoint")),
        )
        evidence.setdefault(key, []).append(record)
    detail_rows: list[dict] = []
    for target in sorted(
        targets,
        key=lambda row: (str(row["target_session"]), str(row["symbol"])),
    ):
        symbol = str(target["symbol"])
        session = str(target["target_session"])
        daily_matches = evidence.get((symbol, session, "daily"), [])
        raw_matches = evidence.get((symbol, session, "raw_5min"), [])
        daily = daily_matches[0] if len(daily_matches) == 1 else {}
        raw = raw_matches[0] if len(raw_matches) == 1 else {}
        daily_status = str(daily.get("validation_status", "DAILY_NOT_REQUESTED"))
        raw_status = str(raw.get("validation_status", "RAW_NOT_REQUESTED"))
        daily_state = (
            _endpoint_evidence_state(daily, "daily", request_contract, run_root)
            if len(daily_matches) == 1
            else "INCOMPLETE"
        )
        raw_state = (
            _endpoint_evidence_state(raw, "raw_5min", request_contract, run_root)
            if len(raw_matches) == 1
            else "INCOMPLETE"
        )
        states = {daily_state, raw_state}
        if "INCOMPLETE" in states:
            outcome = "CANARY_INCOMPLETE" if mode == "canary" else "INCOMPLETE_EVIDENCE"
        elif "INVALID" in states:
            outcome = "CANARY_INVALID" if mode == "canary" else "INVALID_EVIDENCE"
        elif "REQUEST_FAILED" in states:
            outcome = "CANARY_REQUEST_FAILED" if mode == "canary" else "FAILED_TRANSIENT"
        elif daily_state == raw_state == "VALID":
            outcome = "CANARY_DAILY_AND_RAW_VALID" if mode == "canary" else "RECOVERED_DAILY_AND_RAW"
        elif daily_state == "VALID":
            outcome = "CANARY_DAILY_ONLY_VALID" if mode == "canary" else "RECOVERED_DAILY_ONLY"
        elif raw_state == "VALID":
            outcome = "CANARY_RAW_ONLY_VALID" if mode == "canary" else "RECOVERED_RAW_ONLY"
        else:
            outcome = "CANARY_EMPTY" if mode == "canary" else "RECOVERED_NEITHER"
        detail_rows.append(
            {
                "symbol": symbol,
                "target_session": session,
                "asset_role": target.get("asset_role", "CANDIDATE"),
                "daily_request_batch_id": daily.get("batch_id", ""),
                "raw_request_batch_id": raw.get("batch_id", ""),
                "daily_validation_status": daily_status,
                "raw_validation_status": raw_status,
                "daily_evidence_state": daily_state,
                "raw_evidence_state": raw_state,
                "request_window_contract": request_contract,
                "exact_target_daily_row_count": int(daily.get("row_count", 0)),
                "exact_target_raw_rth_row_count": int(raw.get("row_count", 0)),
                "daily_evidence_sha256": daily.get(
                    "provider_evidence_sha256",
                    daily.get("staged_file_sha256", ""),
                ),
                "raw_evidence_sha256": raw.get(
                    "provider_evidence_sha256",
                    raw.get("staged_file_sha256", ""),
                ),
                "daily_request_start": daily.get("request_start", ""),
                "daily_request_end": daily.get("request_end", ""),
                "raw_request_start": raw.get("request_start", ""),
                "raw_request_end": raw.get("request_end", ""),
                "outcome_class": outcome,
            }
        )
    daily_count = sum(row["daily_evidence_state"] == "VALID" for row in detail_rows)
    raw_count = sum(row["raw_evidence_state"] == "VALID" for row in detail_rows)
    if mode == "canary":
        invalid = any(row["outcome_class"] == "CANARY_INVALID" for row in detail_rows)
        incomplete = any(row["outcome_class"] == "CANARY_INCOMPLETE" for row in detail_rows)
        request_failed = any(row["outcome_class"] == "CANARY_REQUEST_FAILED" for row in detail_rows)
        all_pairs_complete = all(row["outcome_class"] == "CANARY_DAILY_AND_RAW_VALID" for row in detail_rows)
        decision = (
            "CANARY_INVALID"
            if invalid
            else "CANARY_INCOMPLETE"
            if incomplete
            else "CANARY_REQUEST_FAILED"
            if request_failed
            else "CANARY_PASSED"
            if all_pairs_complete and len(detail_rows) == len(targets)
            else "CANARY_PROVIDER_DATA_EMPTY"
        )
    else:
        incomplete = any(
            row["outcome_class"] in {"INCOMPLETE_EVIDENCE", "INVALID_EVIDENCE", "FAILED_TRANSIENT"}
            for row in detail_rows
        )
        decision = (
            "FULL_RECOVERY_INCOMPLETE"
            if incomplete
            else "FULL_RECOVERY_SUFFICIENT"
            if daily_count == len(targets) == 350
            else "FULL_RECOVERY_INSUFFICIENT"
        )
    return {
        "decision_status": decision,
        "daily_recovery_count": daily_count,
        "raw_recovery_count": raw_count,
        "complete_provider_evidence_count": sum(
            row["daily_evidence_state"] != "INCOMPLETE"
            for row in detail_rows
        )
        + sum(
            row["raw_evidence_state"] != "INCOMPLETE"
            for row in detail_rows
        ),
        "detail_rows": detail_rows,
    }


def resolve_recovery_decision(
    mode: str,
    evidence_summary: dict,
    execution: dict,
    mutation_count: int,
) -> str:
    """Apply execution and immutability gates after pair-level reduction."""
    if mutation_count:
        return "IMPLEMENTATION_BLOCKED"
    has_systemic_failure = any(
        row.get("status") == "FAILED_SYSTEMIC"
        for row in execution.get("results", [])
    )
    if execution.get("systemic_circuit_breaker_fired") or has_systemic_failure:
        return "NETWORK_ENVIRONMENT_BLOCKED"
    if mode == "canary" and (
        int(execution.get("interrupted_batch_count", 0)) > 0
        or int(execution.get("terminal_batch_count", 0))
        < int(execution.get("planned_batch_count", 0))
    ):
        return "CANARY_INCOMPLETE"
    if mode == "canary" and any(
        row.get("status") in {"FAILED_TRANSIENT", "INVALID_RESPONSE"}
        and not row.get("records")
        for row in execution.get("results", [])
    ):
        return "CANARY_REQUEST_FAILED"
    return str(evidence_summary["decision_status"])


def _make_alpaca_batch_request(
    paths,
    schedule: pd.DataFrame,
    request_contract: str,
    *,
    allow_staging_overwrite: bool,
):
    api_key, secret_key = get_credentials()
    _, _, StockHistoricalDataClient, _, TimeFrame, TimeFrameUnit = _load_alpaca()
    feed = get_data_feed("iex")
    adjustment = get_adjustment("all")
    day_timeframe = (
        TimeFrame.Day
        if hasattr(TimeFrame, "Day")
        else TimeFrame(1, TimeFrameUnit.Day)
    )
    raw_timeframe = TimeFrame(5, TimeFrameUnit.Minute)
    batch_evidence_dir = paths.manifests / "batches"

    def request_batch(batch: dict) -> dict:
        windows = _gap_recovery_windows(batch["target_session"], schedule)
        is_daily = batch["endpoint"] == "daily"
        request_window = build_request_window(
            request_contract,
            batch["endpoint"],
            batch["target_session"],
            schedule,
        )
        request_start = request_window.start
        request_end = request_window.end
        timeframe = day_timeframe if is_daily else raw_timeframe
        client = StockHistoricalDataClient(api_key, secret_key)
        frames = download_bars_batch(
            client,
            batch["symbols"],
            request_start.to_pydatetime(),
            request_end.to_pydatetime(),
            timeframe,
            feed,
            adjustment,
        )
        records = []
        for symbol in batch["symbols"]:
            frame = frames.get(symbol, pd.DataFrame())
            provider_metadata = provider_frame_metadata(frame)
            provider_evidence_path = (
                paths.run_root
                / "provider_responses"
                / batch["endpoint"]
                / batch["batch_id"]
                / f"{symbol}.csv"
            )
            provider_evidence_hash = persist_provider_response_evidence(
                frame,
                provider_evidence_path,
                allow_overwrite=allow_staging_overwrite,
            )
            provider_evidence_relative_path = provider_evidence_path.relative_to(
                paths.run_root
            ).as_posix()
            if is_daily:
                validation_status, staged = validate_daily_recovery(
                    frame,
                    batch["target_session"],
                )
                output_path = (
                    paths.daily
                    / f"{symbol}_{batch['target_session']}_1D.csv"
                )
            else:
                validation_status, staged = validate_raw_recovery(
                    frame,
                    batch["target_session"],
                    windows["market_open"],
                    windows["market_close"],
                )
                output_path = (
                    paths.raw_5min
                    / f"{symbol}_{batch['target_session']}_5min.csv"
                )
            staged_hash = ""
            staged_relative_path = ""
            if not staged.empty:
                write_staged_frame(
                    staged,
                    output_path,
                    allow_overwrite=allow_staging_overwrite,
                )
                staged_hash = _hash_file(output_path)
                staged_relative_path = output_path.relative_to(paths.run_root).as_posix()
            records.append(
                {
                    "batch_id": batch["batch_id"],
                    "symbol": symbol,
                    "target_session": batch["target_session"],
                    "asset_role": "CANDIDATE",
                    "endpoint": batch["endpoint"],
                    "request_start": request_start.isoformat(),
                    "request_end": request_end.isoformat(),
                    "request_window_contract": request_contract,
                    "validation_status": validation_status,
                    **provider_metadata,
                    "provider_evidence_relative_path": provider_evidence_relative_path,
                    "provider_evidence_sha256": provider_evidence_hash,
                    "row_count": len(staged),
                    "staged_relative_path": staged_relative_path,
                    "staged_file_sha256": staged_hash,
                }
            )
        statuses = {record["validation_status"] for record in records}
        invalid = any(
            any(token in status for token in ("INVALID", "WRONG", "DUPLICATE"))
            for status in statuses
        )
        empty = all(record["row_count"] == 0 for record in records)
        batch_status = (
            "INVALID_RESPONSE"
            if invalid
            else "EMPTY_RESPONSE"
            if empty
            else "COMPLETE"
        )
        evidence_path = batch_evidence_dir / f"{batch['batch_id']}.json"
        evidence_payload = {
            "batch_id": batch["batch_id"],
            "status": batch_status,
            "generated_at": _utc_now(),
            "records": records,
        }
        if evidence_path.exists() and not allow_staging_overwrite:
            raise FileExistsError("RECOVERY_STAGING_OVERWRITE_BLOCKED")
        _atomic_write_json(evidence_payload, evidence_path)
        return {
            "status": batch_status,
            "records": records,
            "evidence_relative_path": evidence_path.relative_to(
                paths.run_root
            ).as_posix(),
            "evidence_sha256": _hash_file(evidence_path),
        }

    return request_batch


def _write_recovery_reports(
    mode: str,
    report_dir: Path,
    run_report_dir: Path,
    detail_rows: list[dict],
    summary: dict,
) -> dict[str, Path]:
    detail = pd.DataFrame(detail_rows)
    if mode == "canary":
        names = {
            "detail": "rs_v2_gap_recovery_canary_detail.csv",
            "summary": "rs_v2_gap_recovery_canary_summary.md",
            "status": "rs_v2_gap_recovery_canary_status.json",
        }
    else:
        names = {
            "detail": "rs_v2_gap_recovery_final_detail.csv",
            "by_date": "rs_v2_gap_recovery_final_by_date.csv",
            "by_symbol": "rs_v2_gap_recovery_final_by_symbol.csv",
            "summary": "rs_v2_gap_recovery_final_summary.md",
            "status": "rs_v2_gap_recovery_final_status.json",
        }
    folders = (Path(report_dir), Path(run_report_dir))
    for folder in folders:
        atomic_write_csv(detail, folder / names["detail"])
        if mode == "full":
            by_date = (
                detail.groupby(["target_session", "outcome_class"])
                .size()
                .reset_index(name="pair_count")
            )
            by_symbol = (
                detail.groupby(["symbol", "outcome_class"])
                .size()
                .reset_index(name="pair_count")
            )
            atomic_write_csv(by_date, folder / names["by_date"])
            atomic_write_csv(by_symbol, folder / names["by_symbol"])
        lines = [
            "# RS V2 Gap Recovery " + ("Canary" if mode == "canary" else "Final"),
            "",
        ]
        lines.extend(f"- {key}: {value}" for key, value in summary.items())
        lines.extend(
            [
                "- frozen_dataset_merge_performed: false",
                "- provider_post_used: false",
                "- broker_order_call_used: false",
                "",
            ]
        )
        _atomic_write_text("\n".join(lines), folder / names["summary"])
        _atomic_write_json(summary, folder / names["status"])
    return {key: Path(report_dir) / value for key, value in names.items()}


def run_gap_recovery_mode(
    *,
    mode: str,
    frozen_root: Path,
    staging_root: Path,
    report_dir: Path,
    preflight_report_path: Path,
    provider_contract_report_path: Path,
    audit_run: str = GAP_RECOVERY_AUDIT_RUN,
    batch_size: int = 20,
    retry_limit: int = 0,
    workers: int = 2,
    request_budget: int | None = None,
    resume_run_id: str | None = None,
    allow_staging_overwrite: bool = False,
) -> dict:
    """Execute canary or full recovery after validating one recent preflight."""
    if mode not in {"canary", "full"}:
        raise ValueError("GAP_RECOVERY_MODE_INVALID")
    preflight = load_provider_preflight_evidence(preflight_report_path)
    provider_contract = load_resolved_provider_contract(
        provider_contract_report_path,
        preflight_generated_at=str(preflight["generated_at"]),
    )
    request_contract = str(
        provider_contract["selected_request_window_contract"]
    )
    targets, context = build_gap_recovery_targets(frozen_root, audit_run)
    validate_gap_recovery_invocation(
        dataset_profile="rs_v2_clean",
        feed="iex",
        adjustment="all",
        audit_run=audit_run,
        frozen_root=frozen_root,
        staging_root=staging_root,
        targets=targets,
    )
    selected_targets = (
        select_deterministic_canary(targets) if mode == "canary" else targets
    )
    if mode == "canary" and workers > 2:
        raise ValueError("GAP_RECOVERY_CANARY_WORKERS_EXCEED_TWO")
    run_id = resume_run_id or (
        f"gaprec_{mode}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    paths = resolve_gap_recovery_run_paths(frozen_root, staging_root, run_id)
    plan = build_batched_recovery_plan(
        selected_targets,
        batch_size=batch_size,
        retry_limit=retry_limit,
        run_id=run_id,
        expected_target_count=len(selected_targets),
    )
    maximum_budget = 20 if mode == "canary" else plan["summary"][
        "maximum_provider_requests"
    ]
    resolved_budget = request_budget if request_budget is not None else maximum_budget
    if resolved_budget < 0 or resolved_budget > maximum_budget:
        raise ValueError("GAP_RECOVERY_REQUEST_BUDGET_INVALID")
    checkpoint_path = paths.manifests / "recovery_checkpoint.json"
    frozen_before = _frozen_hashes(frozen_root)
    request_batch = _make_alpaca_batch_request(
        paths,
        context["schedule"],
        request_contract,
        allow_staging_overwrite=allow_staging_overwrite,
    )
    execution = execute_recovery_batches(
        plan,
        checkpoint_path,
        request_batch,
        workers=workers,
        request_budget=resolved_budget,
        retry_limit=retry_limit,
        resume=resume_run_id is not None,
    )
    records = [
        record
        for batch_result in execution["results"]
        for record in batch_result.get("records", [])
    ]
    evidence_summary = summarize_pair_evidence(
        selected_targets,
        records,
        mode=mode,
        request_contract=request_contract,
        run_root=paths.run_root,
    )
    frozen_after = _frozen_hashes(frozen_root)
    mutation_count = sum(
        frozen_before.get(key) != frozen_after.get(key)
        for key in set(frozen_before) | set(frozen_after)
    )
    decision_status = resolve_recovery_decision(
        mode,
        evidence_summary,
        execution,
        mutation_count,
    )
    immutable_manifest_path = (
        Path(frozen_root)
        / "manifests"
        / audit_run
        / "acquisition_manifest.json"
    )
    summary = {
        "generated_at": _utc_now(),
        "recovery_run_id": run_id,
        "mode": mode,
        "decision_status": decision_status,
        "preflight_status": preflight["status"],
        "preflight_generated_at": preflight["generated_at"],
        "provider_contract_generated_at": provider_contract.get("generated_at", ""),
        "request_window_contract": request_contract,
        "frozen_manifest_sha256": _hash_file(immutable_manifest_path),
        "frozen_snapshot_digest": frozen_snapshot_digest(frozen_before),
        "exact_target_pair_count": len(selected_targets),
        "daily_recovery_count": evidence_summary["daily_recovery_count"],
        "raw_recovery_count": evidence_summary["raw_recovery_count"],
        "complete_provider_evidence_count": evidence_summary[
            "complete_provider_evidence_count"
        ],
        "network_request_count": execution["provider_request_count"],
        "retry_count": execution["retry_count"],
        "request_budget": resolved_budget,
        "planned_batch_count": execution["planned_batch_count"],
        "terminal_batch_count": execution["terminal_batch_count"],
        "interrupted_batch_count": execution["interrupted_batch_count"],
        "systemic_circuit_breaker_fired": execution[
            "systemic_circuit_breaker_fired"
        ],
        "frozen_dataset_mutation_count": mutation_count,
        "staging_run_root": paths.run_root.as_posix(),
    }
    report_paths = _write_recovery_reports(
        mode,
        report_dir,
        paths.reports,
        evidence_summary["detail_rows"],
        summary,
    )
    _atomic_write_json(
        {
            "schema_version": "1.0.0",
            "status": decision_status,
            "generated_at": _utc_now(),
            "summary": summary,
            "records": evidence_summary["detail_rows"],
        },
        paths.manifests / "recovery_manifest.json",
    )
    return {
        "status": decision_status,
        "summary": summary,
        "report_paths": report_paths,
        "paths": paths,
        "execution": execution,
    }
