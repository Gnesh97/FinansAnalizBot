from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from testdata.v2_provider_recovery import (
    PREFLIGHT_OK,
    RequestBudget,
    SystemicCircuitBreaker,
    build_batched_recovery_plan,
    can_run_canonical_backtest,
    can_run_full_acquisition,
    execute_recovery_batches,
    initialize_recovery_checkpoint,
    run_provider_preflight,
    select_deterministic_canary,
    update_batch_checkpoint,
    write_batched_recovery_plan,
)


DATES = ("2021-04-19", "2021-10-25", "2022-03-08", "2022-04-06")
REQUEST_CONTRACT = "CURRENT_CONTRACT"


def _targets() -> list[dict]:
    distributions = (122, 108, 119, 1)
    rows: list[dict] = []
    for date, count in zip(DATES, distributions):
        symbols = [f"S{index:03d}" for index in range(count)]
        if date == "2022-04-06":
            symbols = ["WBD"]
        rows.extend(
            {
                "symbol": symbol,
                "target_session": date,
                "asset_role": "CANDIDATE",
            }
            for symbol in symbols
        )
    return rows


def _provider_record(
    target: dict,
    endpoint: str,
    validation_status: str,
    row_count: int,
    evidence_root: Path,
) -> dict:
    evidence_path = (
        evidence_root / "provider" / f"{target['symbol']}_{target['target_session']}_{endpoint}.csv"
    )
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range("2021-04-19T13:30:00Z", periods=row_count, freq="5min")
    frame = pd.DataFrame({"Date": dates})
    frame.to_csv(evidence_path, index=False)
    timestamp_values = [value.isoformat() for value in dates]
    timestamp_digest = hashlib.sha256(
        "\n".join(timestamp_values).encode("utf-8")
    ).hexdigest()
    return {
        **target,
        "endpoint": endpoint,
        "validation_status": validation_status,
        "row_count": row_count,
        "provider_row_count": row_count,
        "provider_evidence_relative_path": evidence_path.relative_to(evidence_root).as_posix(),
        "provider_evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "provider_timestamp_sha256": timestamp_digest,
        "provider_first_timestamp": timestamp_values[0] if timestamp_values else "",
        "provider_last_timestamp": timestamp_values[-1] if timestamp_values else "",
        "request_start": "2021-04-19T00:00:00+00:00",
        "request_end": "2021-04-20T00:00:00+00:00",
        "request_window_contract": REQUEST_CONTRACT,
    }


def _preflight(**overrides):
    calls: list[str] = []

    def stage(name, result=None):
        def call(*_args, **_kwargs):
            calls.append(name)
            return result

        return call

    kwargs = {
        "dns_probe": stage("dns", ["127.0.0.1"]),
        "tcp_probe": stage("tcp"),
        "tls_probe": stage("tls"),
        "credential_loader": stage("credentials", ("key", "secret")),
        "authenticated_request": stage("auth", [{"bar": 1}]),
        "request_start": "2026-07-06",
        "request_end": "2026-07-07",
    }
    kwargs.update(overrides)
    return run_provider_preflight(**kwargs), calls


class TestProviderPreflight(unittest.TestCase):
    def test_ok_runs_stages_in_order_once(self):
        result, calls = _preflight()
        self.assertEqual(result["status"], PREFLIGHT_OK)
        self.assertEqual(calls, ["dns", "tcp", "tls", "credentials", "auth"])
        self.assertEqual((result["provider_request_count"], result["retry_count"]), (1, 0))

    def test_dns_failure_stops_before_other_stages(self):
        def fail_dns(_host):
            raise OSError("name resolution denied")

        result, calls = _preflight(dns_probe=fail_dns)
        self.assertEqual(result["status"], "PREFLIGHT_BLOCKED_DNS")
        self.assertEqual(calls, [])
        self.assertEqual(result["provider_request_count"], 0)

    def test_winerror_10013_stops_immediately_without_retry(self):
        denied = OSError(10013, "permission denied")
        denied.winerror = 10013

        def fail_socket(*_args):
            raise denied

        result, calls = _preflight(tcp_probe=fail_socket)
        self.assertEqual(result["status"], "PREFLIGHT_BLOCKED_SOCKET")
        self.assertEqual(calls, ["dns"])
        self.assertEqual((result["provider_request_count"], result["retry_count"]), (0, 0))

    def test_tls_and_missing_credentials_are_distinct(self):
        def fail_tls(*_args):
            raise RuntimeError("certificate failure")

        tls_result, _ = _preflight(tls_probe=fail_tls)
        self.assertEqual(tls_result["status"], "PREFLIGHT_BLOCKED_TLS")

        def missing_credentials():
            raise RuntimeError("credentials missing")

        credential_result, _ = _preflight(credential_loader=missing_credentials)
        self.assertEqual(credential_result["status"], "PREFLIGHT_CREDENTIALS_MISSING")

    def test_auth_failure_is_not_retried(self):
        error = RuntimeError("unauthorized")
        error.status_code = 401

        def fail_auth(*_args):
            raise error

        result, _ = _preflight(authenticated_request=fail_auth, max_retries=1)
        self.assertEqual(result["status"], "PREFLIGHT_AUTH_FAILED")
        self.assertEqual((result["provider_request_count"], result["retry_count"]), (1, 0))

    def test_one_transient_provider_retry_can_recover(self):
        attempts = 0

        def request(*_args):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                error = RuntimeError("service unavailable")
                error.status_code = 503
                raise error
            return [{"bar": 1}]

        result, _ = _preflight(authenticated_request=request, max_retries=1)
        self.assertEqual(result["status"], PREFLIGHT_OK)
        self.assertEqual((result["provider_request_count"], result["retry_count"]), (2, 1))

    def test_empty_authenticated_response_is_reachable_not_success(self):
        result, _ = _preflight(authenticated_request=lambda *_args: [])
        self.assertEqual(result["status"], "PREFLIGHT_EMPTY_BUT_REACHABLE")
        self.assertEqual(result["provider_request_count"], 1)

    def test_preflight_writes_required_reports_without_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = _preflight(report_dir=Path(tmp))
            payload = json.loads((Path(tmp) / "rs_v2_provider_preflight.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], result["status"])
            self.assertTrue((Path(tmp) / "rs_v2_provider_preflight.md").is_file())
            self.assertNotIn("key", json.dumps(payload).lower())
            self.assertNotIn("secret", json.dumps(payload).lower())


class TestBatchedRecovery(unittest.TestCase):
    def test_plan_has_exact_350_pairs_and_40_requests_at_batch_20(self):
        plan = build_batched_recovery_plan(_targets(), batch_size=20, retry_limit=0, run_id="gaprec_test")
        self.assertEqual(plan["summary"]["logical_target_count"], 350)
        self.assertEqual(plan["summary"]["daily_batch_count"], 20)
        self.assertEqual(plan["summary"]["raw_batch_count"], 20)
        self.assertEqual(plan["summary"]["maximum_provider_requests"], 40)
        self.assertEqual(plan["summary"]["maximum_retries"], 0)
        planned_pairs = {
            (symbol, batch["target_session"])
            for batch in plan["batches"]
            if batch["endpoint"] == "daily"
            for symbol in batch["symbols"]
        }
        self.assertEqual(len(planned_pairs), 350)
        self.assertFalse({"QQQ", "SPY"} & {symbol for symbol, _ in planned_pairs})

    def test_plan_rejects_contract_or_unsafe_settings(self):
        for kwargs in (
            {"targets": _targets()[:-1]},
            {"targets": _targets(), "batch_size": 9},
            {"targets": _targets(), "batch_size": 31},
            {"targets": _targets(), "retry_limit": 2},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    build_batched_recovery_plan(run_id="gaprec_test", **kwargs)

    def test_canary_is_deterministic_covers_dates_and_wbd(self):
        first = select_deterministic_canary(_targets())
        second = select_deterministic_canary(list(reversed(_targets())))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        self.assertEqual({row["target_session"] for row in first}, set(DATES))
        self.assertIn({"symbol": "WBD", "target_session": "2022-04-06", "asset_role": "CANDIDATE"}, first)
        plan = build_batched_recovery_plan(first, batch_size=10, retry_limit=1, run_id="gaprec_canary", expected_target_count=4)
        self.assertLessEqual(plan["summary"]["maximum_provider_requests"], 20)

    def test_request_budget_and_systemic_breaker_fail_closed(self):
        budget = RequestBudget(2)
        budget.claim()
        budget.claim()
        with self.assertRaisesRegex(RuntimeError, "REQUEST_BUDGET_EXHAUSTED"):
            budget.claim()

        breaker = SystemicCircuitBreaker(threshold=3)
        denied = OSError(10013, "permission denied")
        denied.winerror = 10013
        breaker.record_failure(denied)
        self.assertTrue(breaker.is_open)

        breaker = SystemicCircuitBreaker(threshold=3)
        for attempt in range(3):
            breaker.record_failure(ConnectionError("same systemic failure"))
            self.assertEqual(breaker.is_open, attempt == 2)

    def test_checkpoint_resume_marks_interrupted_and_refuses_overwrite(self):
        plan = build_batched_recovery_plan(_targets(), batch_size=20, run_id="gaprec_test")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            initialize_recovery_checkpoint(path, plan)
            batch_id = plan["batches"][0]["batch_id"]
            update_batch_checkpoint(path, batch_id, "REQUESTING")
            with self.assertRaisesRegex(FileExistsError, "RECOVERY_CHECKPOINT_EXISTS"):
                initialize_recovery_checkpoint(path, plan)
            resumed = initialize_recovery_checkpoint(path, plan, resume=True)
            self.assertEqual(resumed["batches"][batch_id]["status"], "INTERRUPTED")
            self.assertFalse(path.with_name(f".{path.name}.tmp").exists())

            regenerated = copy.deepcopy(plan)
            regenerated["generated_at"] = "2099-01-01T00:00:00Z"
            resumed_again = initialize_recovery_checkpoint(path, regenerated, resume=True)
            self.assertEqual(resumed_again["run_id"], plan["run_id"])

    def test_resume_skips_complete_batches_without_duplicate_request(self):
        plan = build_batched_recovery_plan(_targets(), batch_size=20, run_id="gaprec_test")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            initialize_recovery_checkpoint(path, plan)
            completed = plan["batches"][0]["batch_id"]
            prior_record = {
                "symbol": "S000",
                "target_session": "2021-04-19",
                "endpoint": "daily",
                "validation_status": "DAILY_RECOVERED_VALID",
            }
            update_batch_checkpoint(
                path,
                completed,
                "COMPLETE",
                {
                    "evidence_sha256": "abc",
                    "records": [prior_record],
                    "attempt_count": 1,
                },
            )
            calls: list[str] = []

            def request(batch):
                calls.append(batch["batch_id"])
                return {"status": "COMPLETE", "provider_attempt_count": 1, "retry_count": 0}

            result = execute_recovery_batches(
                plan,
                path,
                request,
                workers=2,
                request_budget=40,
                retry_limit=0,
                resume=True,
            )
            self.assertNotIn(completed, calls)
            self.assertEqual(len(calls), 39)
            self.assertEqual(result["skipped_already_complete_count"], 1)
            self.assertEqual(result["provider_request_count"], 40)
            prior_results = [
                row for row in result["results"] if row["batch_id"] == completed
            ]
            self.assertEqual(prior_results[0]["records"], [prior_record])

    def test_resume_accumulates_attempts_for_retried_incomplete_batch(self):
        canary_targets = select_deterministic_canary(_targets())
        plan = build_batched_recovery_plan(
            canary_targets,
            batch_size=10,
            run_id="gaprec_canary_resume",
            expected_target_count=4,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            initialize_recovery_checkpoint(path, plan)
            retried_batch = plan["batches"][0]["batch_id"]
            update_batch_checkpoint(
                path,
                retried_batch,
                "FAILED_TRANSIENT",
                {"attempt_count": 1, "retry_count": 0},
            )

            result = execute_recovery_batches(
                plan,
                path,
                lambda _batch: {"status": "COMPLETE", "records": []},
                workers=2,
                request_budget=8,
                retry_limit=0,
                resume=True,
            )
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                checkpoint["batches"][retried_batch]["attempt_count"],
                2,
            )
            self.assertEqual(result["provider_request_count"], 8)

    def test_plan_reports_use_required_paths(self):
        plan = build_batched_recovery_plan(_targets(), batch_size=20, run_id="gaprec_test")
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_batched_recovery_plan(plan, Path(tmp), Path(tmp) / "staging")
            self.assertEqual(paths["csv"].name, "rs_v2_gap_recovery_batched_plan.csv")
            self.assertEqual(paths["markdown"].name, "rs_v2_gap_recovery_batched_plan.md")
            self.assertTrue(paths["csv"].is_file())
            self.assertTrue(paths["markdown"].is_file())

    def test_conditional_acquisition_and_backtest_gates(self):
        self.assertTrue(can_run_full_acquisition(PREFLIGHT_OK, "CANARY_PASSED", "FULL_RECOVERY_SUFFICIENT"))
        self.assertFalse(can_run_full_acquisition("PREFLIGHT_BLOCKED_SOCKET", "CANARY_PASSED", "FULL_RECOVERY_SUFFICIENT"))
        self.assertTrue(can_run_canonical_backtest("COMPLETE_VALIDATED"))
        for status in ("IN_PROGRESS", "VALIDATION_PENDING", "FAILED_VALIDATION", "INTERRUPTED"):
            self.assertFalse(can_run_canonical_backtest(status))


class TestDownloaderCliIntegration(unittest.TestCase):
    def test_batch_download_uses_one_multi_symbol_provider_call(self):
        from testdata.vericekici_updated import download_bars_batch

        index = pd.MultiIndex.from_tuples(
            [
                ("AAPL", pd.Timestamp("2021-04-19 13:30:00+00:00")),
                ("MSFT", pd.Timestamp("2021-04-19 13:30:00+00:00")),
            ],
            names=["symbol", "timestamp"],
        )
        raw = pd.DataFrame(
            {
                "open": [10.0, 20.0],
                "high": [11.0, 21.0],
                "low": [9.0, 19.0],
                "close": [10.5, 20.5],
                "volume": [100, 200],
            },
            index=index,
        )

        class Request:
            def __init__(self, **kwargs):
                self.symbols = kwargs["symbol_or_symbols"]

        class Client:
            def __init__(self):
                self.calls = 0

            def get_stock_bars(self, request):
                self.calls += 1
                self.request = request
                return type("Response", (), {"df": raw})()

        client = Client()
        with patch(
            "testdata.vericekici_updated._load_alpaca",
            return_value=(object(), object(), object(), Request, object(), object()),
        ):
            frames = download_bars_batch(
                client,
                ["AAPL", "MSFT"],
                pd.Timestamp("2021-04-19", tz="UTC").to_pydatetime(),
                pd.Timestamp("2021-04-20", tz="UTC").to_pydatetime(),
                object(),
                object(),
                object(),
            )
        self.assertEqual(client.calls, 1)
        self.assertEqual(client.request.symbols, ["AAPL", "MSFT"])
        self.assertEqual({symbol: len(frame) for symbol, frame in frames.items()}, {"AAPL": 1, "MSFT": 1})

    def test_parser_exposes_preflight_and_batched_plan_flags(self):
        from testdata.vericekici_updated import build_parser

        args = build_parser().parse_args(
            [
                "--dataset-profile",
                "rs_v2_clean",
                "--provider-preflight",
                "--feed",
                "iex",
                "--adjustment",
                "all",
                "--batch-size",
                "20",
                "--recovery-retry-limit",
                "0",
            ]
        )
        self.assertTrue(args.provider_preflight)
        self.assertEqual(args.batch_size, 20)
        self.assertEqual(args.recovery_retry_limit, 0)

        recovery_args = build_parser().parse_args(
            [
                "--dataset-profile",
                "rs_v2_clean",
                "--gap-recovery-canary",
                "--feed",
                "iex",
                "--adjustment",
                "all",
                "--use-preflight-report",
            ]
        )
        self.assertTrue(recovery_args.gap_recovery_canary)
        self.assertTrue(recovery_args.use_preflight_report)

    def test_offline_plan_uses_exact_targets_and_writes_required_reports(self):
        from testdata.vericekici_updated import create_gap_recovery_plan

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "testdata.vericekici_updated.build_gap_recovery_targets",
                return_value=(_targets(), {}),
            ):
                result = create_gap_recovery_plan(
                    frozen_root=root / "frozen",
                    audit_run="acq_20260712_212749",
                    report_dir=root / "reports",
                    staging_root=root / "staging",
                    batch_size=20,
                    retry_limit=0,
                    run_id="gaprec_plan_test",
                )
        self.assertEqual(result["plan"]["summary"]["maximum_provider_requests"], 40)
        self.assertEqual(result["plan"]["summary"]["logical_target_count"], 350)
        self.assertEqual(
            {path.name for path in result["report_paths"].values()},
            {
                "rs_v2_gap_recovery_batched_plan.csv",
                "rs_v2_gap_recovery_batched_plan.md",
            },
        )

    def test_preflight_evidence_must_be_recent_and_ok(self):
        from testdata.vericekici_updated import load_provider_preflight_evidence

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rs_v2_provider_preflight.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "PREFLIGHT_BLOCKED_SOCKET",
                        "generated_at": "2026-07-13T00:00:00Z",
                        "provider_request_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "PREFLIGHT_EVIDENCE_NOT_OK"):
                load_provider_preflight_evidence(path, max_age_minutes=30)

    def test_full_gate_rejects_unbound_canary_status(self):
        from testdata.vericekici_updated import load_canary_recovery_evidence

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = (
                root
                / "manifests"
                / "acq_20260712_212749"
                / "acquisition_manifest.json"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}", encoding="utf-8")
            status = root / "canary_status.json"
            status.write_text(
                json.dumps({"decision_status": "CANARY_PASSED"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "GAP_RECOVERY_CANARY_EVIDENCE_CONTRACT_MISMATCH",
            ):
                load_canary_recovery_evidence(status, root)

    def test_full_gate_binds_complete_canary_to_selected_contract(self):
        from testdata.vericekici_updated import (
            _frozen_hashes,
            frozen_snapshot_digest,
            load_canary_recovery_evidence,
        )

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "frozen"
            manifest = (
                root
                / "manifests"
                / "acq_20260712_212749"
                / "acquisition_manifest.json"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}", encoding="utf-8")
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            payload = {
                "decision_status": "CANARY_PASSED",
                "mode": "canary",
                "exact_target_pair_count": 4,
                "daily_recovery_count": 4,
                "raw_recovery_count": 4,
                "complete_provider_evidence_count": 8,
                "planned_batch_count": 8,
                "terminal_batch_count": 8,
                "interrupted_batch_count": 0,
                "network_request_count": 8,
                "retry_count": 0,
                "systemic_circuit_breaker_fired": False,
                "frozen_dataset_mutation_count": 0,
                "preflight_status": "PREFLIGHT_OK",
                "frozen_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "frozen_snapshot_digest": frozen_snapshot_digest(_frozen_hashes(root)),
                "preflight_generated_at": now,
                "generated_at": now,
                "request_window_contract": REQUEST_CONTRACT,
            }
            status = base / "canary.json"
            status.write_text(json.dumps(payload), encoding="utf-8")

            loaded = load_canary_recovery_evidence(
                status,
                root,
                expected_request_contract=REQUEST_CONTRACT,
            )
            self.assertEqual(loaded["daily_recovery_count"], 4)
            with self.assertRaisesRegex(
                ValueError, "GAP_RECOVERY_CANARY_EVIDENCE_CONTRACT_MISMATCH"
            ):
                load_canary_recovery_evidence(
                    status,
                    root,
                    expected_request_contract="DATE_END_EXCLUSIVE_WINDOW",
                )


class TestRecoveryEvidenceSummary(unittest.TestCase):
    def test_staging_collision_requires_explicit_overwrite(self):
        from testdata.v2_gap_recovery_runner import write_staged_frame

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.csv"
            frame = pd.DataFrame({"Date": ["2021-04-19"]})
            write_staged_frame(frame, path, allow_overwrite=False)
            with self.assertRaisesRegex(
                FileExistsError,
                "RECOVERY_STAGING_OVERWRITE_BLOCKED",
            ):
                write_staged_frame(frame, path, allow_overwrite=False)
            write_staged_frame(frame, path, allow_overwrite=True)

    def test_any_systemic_canary_failure_blocks_pass_before_threshold(self):
        from testdata.v2_gap_recovery_runner import resolve_recovery_decision

        evidence = {"decision_status": "CANARY_PASSED"}
        execution = {
            "results": [{"status": "FAILED_SYSTEMIC"}],
            "systemic_circuit_breaker_fired": False,
        }
        self.assertEqual(
            resolve_recovery_decision("canary", evidence, execution, 0),
            "NETWORK_ENVIRONMENT_BLOCKED",
        )

        incomplete_execution = {
            "results": [
                {"status": "COMPLETE", "records": [{}]},
                {"status": "INTERRUPTED", "records": []},
            ],
            "systemic_circuit_breaker_fired": False,
            "interrupted_batch_count": 1,
        }
        self.assertEqual(
            resolve_recovery_decision(
                "canary",
                evidence,
                incomplete_execution,
                0,
            ),
            "CANARY_INCOMPLETE",
        )

    def test_provider_frame_metadata_preserves_timestamp_evidence(self):
        from testdata.v2_gap_recovery_runner import provider_frame_metadata

        frame = pd.DataFrame(
            {
                "Date": [
                    "2021-04-18 20:00:00-04:00",
                    "2021-04-19 20:00:00-04:00",
                ]
            }
        )
        metadata = provider_frame_metadata(frame)
        self.assertEqual(metadata["provider_row_count"], 2)
        self.assertEqual(metadata["provider_first_timestamp"], "2021-04-19T00:00:00+00:00")
        self.assertEqual(metadata["provider_last_timestamp"], "2021-04-20T00:00:00+00:00")
        self.assertEqual(len(metadata["provider_timestamp_sha256"]), 64)

    def test_daily_validator_uses_provider_utc_session_date(self):
        from testdata.vericekici_updated import validate_daily_recovery

        normalized_provider_frame = pd.DataFrame(
            {
                "Date": ["2021-04-18 20:00:00-04:00"],
                "Open": [10.0],
                "High": [11.0],
                "Low": [9.0],
                "Close": [10.5],
                "Volume": [100],
            }
        )
        status, staged = validate_daily_recovery(
            normalized_provider_frame,
            "2021-04-19",
        )
        self.assertEqual(status, "DAILY_RECOVERED_VALID")
        self.assertEqual(len(staged), 1)

    def test_canary_summary_requires_valid_provider_evidence(self):
        from testdata.v2_gap_recovery_runner import summarize_pair_evidence

        targets = select_deterministic_canary(_targets())
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            records = []
            for target in targets:
                records.extend(
                    [
                        _provider_record(target, "daily", "DAILY_RECOVERED_VALID", 1, evidence_root),
                        _provider_record(target, "raw_5min", "RAW_RECOVERED_COMPLETE", 10, evidence_root),
                    ]
                )
            summary = summarize_pair_evidence(
                targets,
                records,
                mode="canary",
                request_contract=REQUEST_CONTRACT,
                run_root=evidence_root,
            )
        self.assertEqual(summary["decision_status"], "CANARY_PASSED")
        self.assertEqual(summary["daily_recovery_count"], 4)
        self.assertEqual(summary["raw_recovery_count"], 4)
        self.assertTrue(all(row["outcome_class"] == "CANARY_DAILY_AND_RAW_VALID" for row in summary["detail_rows"]))

    def test_canary_does_not_pass_when_any_endpoint_is_empty(self):
        from testdata.v2_gap_recovery_runner import summarize_pair_evidence

        targets = select_deterministic_canary(_targets())
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            records = []
            for index, target in enumerate(targets):
                raw_status = "RAW_EMPTY_RESPONSE" if index == len(targets) - 1 else "RAW_RECOVERED_COMPLETE"
                raw_rows = 0 if index == len(targets) - 1 else 10
                records.extend(
                    [
                        _provider_record(target, "daily", "DAILY_RECOVERED_VALID", 1, evidence_root),
                        _provider_record(target, "raw_5min", raw_status, raw_rows, evidence_root),
                    ]
                )

            summary = summarize_pair_evidence(
                targets,
                records,
                mode="canary",
                request_contract=REQUEST_CONTRACT,
                run_root=evidence_root,
            )

        self.assertEqual(summary["decision_status"], "CANARY_PROVIDER_DATA_EMPTY")
        self.assertEqual(len(summary["detail_rows"][-1]["raw_evidence_sha256"]), 64)

    def test_hashless_or_duplicate_canary_records_are_incomplete(self):
        from testdata.v2_gap_recovery_runner import summarize_pair_evidence

        targets = select_deterministic_canary(_targets())
        status_only = []
        for target in targets:
            status_only.extend(
                [
                    {**target, "endpoint": "daily", "validation_status": "DAILY_RECOVERED_VALID", "row_count": 1},
                    {**target, "endpoint": "raw_5min", "validation_status": "RAW_RECOVERED_COMPLETE", "row_count": 10},
                ]
            )
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            hashless = summarize_pair_evidence(
                targets,
                status_only,
                mode="canary",
                request_contract=REQUEST_CONTRACT,
                run_root=evidence_root,
            )
            self.assertEqual(hashless["decision_status"], "CANARY_INCOMPLETE")

            complete = []
            for target in targets:
                complete.extend(
                    [
                        _provider_record(target, "daily", "DAILY_RECOVERED_VALID", 1, evidence_root),
                        _provider_record(target, "raw_5min", "RAW_RECOVERED_COMPLETE", 10, evidence_root),
                    ]
                )
            complete.append(dict(complete[0]))
            duplicate = summarize_pair_evidence(
                targets,
                complete,
                mode="canary",
                request_contract=REQUEST_CONTRACT,
                run_root=evidence_root,
            )
        self.assertEqual(duplicate["decision_status"], "CANARY_INCOMPLETE")

    def test_request_contract_mismatch_is_incomplete(self):
        from testdata.v2_gap_recovery_runner import summarize_pair_evidence

        targets = select_deterministic_canary(_targets())
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            records = []
            for target in targets:
                records.extend(
                    [
                        _provider_record(target, "daily", "DAILY_RECOVERED_VALID", 1, evidence_root),
                        _provider_record(target, "raw_5min", "RAW_RECOVERED_COMPLETE", 10, evidence_root),
                    ]
                )
            records[-1]["request_window_contract"] = "DATE_END_EXCLUSIVE_WINDOW"
            summary = summarize_pair_evidence(
                targets,
                records,
                mode="canary",
                request_contract=REQUEST_CONTRACT,
                run_root=evidence_root,
            )
        self.assertEqual(summary["decision_status"], "CANARY_INCOMPLETE")

    def test_corrupt_provider_evidence_is_incomplete(self):
        from testdata.v2_gap_recovery_runner import summarize_pair_evidence

        targets = select_deterministic_canary(_targets())
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            records = []
            for target in targets:
                records.extend(
                    [
                        _provider_record(target, "daily", "DAILY_RECOVERED_VALID", 1, evidence_root),
                        _provider_record(target, "raw_5min", "RAW_RECOVERED_COMPLETE", 10, evidence_root),
                    ]
                )
            corrupt_path = evidence_root / records[0]["provider_evidence_relative_path"]
            corrupt_path.write_text("Date\n2022-01-01T00:00:00Z\n", encoding="utf-8")
            summary = summarize_pair_evidence(
                targets,
                records,
                mode="canary",
                request_contract=REQUEST_CONTRACT,
                run_root=evidence_root,
            )

        self.assertEqual(summary["decision_status"], "CANARY_INCOMPLETE")

    def test_empty_canary_and_incomplete_full_recovery_fail_closed(self):
        from testdata.v2_gap_recovery_runner import summarize_pair_evidence

        canary = select_deterministic_canary(_targets())
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            empty = summarize_pair_evidence(
                canary,
                [],
                mode="canary",
                request_contract=REQUEST_CONTRACT,
                run_root=evidence_root,
            )
            self.assertEqual(empty["decision_status"], "CANARY_INCOMPLETE")

            incomplete = summarize_pair_evidence(
                _targets(),
                [],
                mode="full",
                request_contract=REQUEST_CONTRACT,
                run_root=evidence_root,
            )
        self.assertEqual(incomplete["decision_status"], "FULL_RECOVERY_INCOMPLETE")
        self.assertEqual(incomplete["daily_recovery_count"], 0)


if __name__ == "__main__":
    unittest.main()
