from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from testdata.v2_provider_contract_forensics import (
    CURRENT_CONTRACT,
    DATE_END_EXCLUSIVE_WINDOW,
    ForensicsRunSpec,
    NYSE_SESSION_UTC_WINDOW,
    PROVIDER_CONTRACT_VARIANTS,
    build_request_window,
    build_parser,
    classify_forensics_outcome,
    classify_session_shift,
    persist_provider_response_evidence,
    planned_forensic_request_count,
    make_stock_request_builder,
    load_resolved_provider_contract,
    request_object_evidence,
    reclassify_forensics_reports,
    response_forensics,
    execute_provider_contract_forensics,
    select_provider_contract,
)


TARGET_SESSION = "2021-04-19"


def _schedule() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "market_open": [pd.Timestamp("2021-04-19 13:30:00+00:00")],
            "market_close": [pd.Timestamp("2021-04-19 20:00:00+00:00")],
        },
        index=pd.DatetimeIndex([pd.Timestamp(TARGET_SESSION)]),
    )


def _raw_multiindex_frame() -> pd.DataFrame:
    index = pd.MultiIndex.from_tuples(
        [
            ("AAPL", pd.Timestamp("2021-04-19 13:30:00+00:00")),
            ("AAPL", pd.Timestamp("2021-04-20 13:30:00+00:00")),
            ("MSFT", pd.Timestamp("2021-04-19 13:30:00+00:00")),
        ],
        names=["symbol", "timestamp"],
    )
    return pd.DataFrame(
        {
            "open": [10.0, 11.0, 20.0],
            "high": [10.5, 11.5, 20.5],
            "low": [9.5, 10.5, 19.5],
            "close": [10.2, 11.2, 20.2],
            "volume": [100, 110, 200],
        },
        index=index,
    )


class TestRequestWindowContracts(unittest.TestCase):
    def test_exact_three_variants_and_bounded_request_budget(self):
        self.assertEqual(
            PROVIDER_CONTRACT_VARIANTS,
            (
                CURRENT_CONTRACT,
                NYSE_SESSION_UTC_WINDOW,
                DATE_END_EXCLUSIVE_WINDOW,
            ),
        )
        self.assertEqual(planned_forensic_request_count(4), 24)
        with self.assertRaisesRegex(ValueError, "PROVIDER_FORENSICS_REQUEST_BUDGET_EXCEEDED"):
            planned_forensic_request_count(5)

    def test_current_contract_remains_unchanged(self):
        daily = build_request_window(
            CURRENT_CONTRACT, "daily", TARGET_SESSION, _schedule()
        )
        raw = build_request_window(
            CURRENT_CONTRACT, "raw_5min", TARGET_SESSION, _schedule()
        )
        self.assertEqual(daily.start.isoformat(), "2021-04-18T00:00:00-04:00")
        self.assertEqual(daily.end.isoformat(), "2021-04-21T00:00:00-04:00")
        self.assertEqual(raw.start.isoformat(), "2021-04-19T09:30:00-04:00")
        self.assertEqual(raw.end.isoformat(), "2021-04-19T16:00:00-04:00")

    def test_nyse_session_variant_uses_exact_utc_open_close(self):
        for endpoint in ("daily", "raw_5min"):
            window = build_request_window(
                NYSE_SESSION_UTC_WINDOW, endpoint, TARGET_SESSION, _schedule()
            )
            self.assertEqual(window.start.isoformat(), "2021-04-19T13:30:00+00:00")
            self.assertEqual(window.end.isoformat(), "2021-04-19T20:00:00+00:00")
            self.assertEqual(window.documented_end_semantics, "INCLUSIVE")

    def test_date_end_exclusive_variant_uses_next_utc_calendar_date(self):
        for endpoint in ("daily", "raw_5min"):
            window = build_request_window(
                DATE_END_EXCLUSIVE_WINDOW, endpoint, TARGET_SESSION, _schedule()
            )
            self.assertEqual(window.start.isoformat(), "2021-04-19T00:00:00+00:00")
            self.assertEqual(window.end.isoformat(), "2021-04-20T00:00:00+00:00")
            self.assertEqual(window.variant_end_semantics, "EXCLUSIVE_HYPOTHESIS")

    def test_live_request_builder_uses_endpoint_timeframe_and_exact_window(self):
        captured = []

        class FakeRequest:
            def __init__(self, **kwargs):
                captured.append(kwargs)

        builder = make_stock_request_builder(
            FakeRequest,
            day_timeframe="DAY",
            raw_timeframe="FIVE_MIN",
            feed="IEX",
            adjustment="ALL",
        )
        target = {"symbol": "AAPL", "target_session": TARGET_SESSION}
        window = build_request_window(
            DATE_END_EXCLUSIVE_WINDOW, "daily", TARGET_SESSION, _schedule()
        )
        builder(DATE_END_EXCLUSIVE_WINDOW, target, "daily", window)
        self.assertEqual(captured[0]["timeframe"], "DAY")
        self.assertEqual(captured[0]["start"], window.start.to_pydatetime())
        self.assertEqual(captured[0]["end"], window.end.to_pydatetime())
        self.assertEqual(captured[0]["symbol_or_symbols"], "AAPL")

    def test_cli_defaults_to_exact_frozen_contract_paths(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.audit_run, "acq_20260712_212749")
        self.assertEqual(args.request_budget, 24)
        self.assertTrue(str(args.preflight_report).endswith("rs_v2_provider_preflight.json"))


class TestProviderResponseForensics(unittest.TestCase):
    def test_multiindex_schema_symbol_filter_and_session_mapping_are_preserved(self):
        evidence = response_forensics(
            _raw_multiindex_frame(),
            symbol="AAPL",
            endpoint="raw_5min",
            target_session=TARGET_SESSION,
            market_open=pd.Timestamp("2021-04-19 13:30:00+00:00"),
            market_close=pd.Timestamp("2021-04-19 20:00:00+00:00"),
        )
        self.assertEqual(evidence["returned_row_count_before_filtering"], 3)
        self.assertEqual(evidence["symbol_filtered_row_count"], 2)
        self.assertEqual(evidence["target_session_row_count"], 1)
        self.assertEqual(json.loads(evidence["response_index_names_json"]), ["symbol", "timestamp"])
        self.assertEqual(json.loads(evidence["response_index_levels_json"]), [2, 2])
        self.assertEqual(
            json.loads(evidence["returned_timestamps_utc_json"]),
            ["2021-04-19T13:30:00+00:00", "2021-04-20T13:30:00+00:00"],
        )
        self.assertEqual(
            json.loads(evidence["mapped_nyse_sessions_json"]),
            ["2021-04-19", "2021-04-20"],
        )
        self.assertEqual(
            json.loads(evidence["adjacent_session_row_counts_json"]),
            {"2021-04-20": 1},
        )
        self.assertEqual(evidence["symbol_filter_status"], "SYMBOL_FOUND")
        self.assertIn("open", json.loads(evidence["response_schema_json"]))

    def test_shift_classifier_distinguishes_exact_backward_forward_and_empty(self):
        self.assertEqual(classify_session_shift([], TARGET_SESSION), "EMPTY")
        self.assertEqual(
            classify_session_shift(["2021-04-16"], TARGET_SESSION),
            "SHIFTED_BACKWARD",
        )
        self.assertEqual(
            classify_session_shift(["2021-04-20"], TARGET_SESSION),
            "SHIFTED_FORWARD",
        )
        self.assertEqual(
            classify_session_shift([TARGET_SESSION], TARGET_SESSION),
            "EXACT_TARGET_ONLY",
        )
        self.assertEqual(
            classify_session_shift([TARGET_SESSION, "2021-04-20"], TARGET_SESSION),
            "TARGET_WITH_ADJACENT",
        )

    def test_empty_provider_response_still_creates_hashed_evidence_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.csv"
            digest = persist_provider_response_evidence(pd.DataFrame(), path)
            self.assertTrue(path.is_file())
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertGreater(path.stat().st_size, 0)

    def test_sdk_request_representation_and_serialized_fields_are_recorded(self):
        class FakeRequest:
            def to_request_fields(self):
                return {
                    "symbols": "AAPL",
                    "start": "2021-04-19T00:00:00+00:00",
                    "end": "2021-04-20T00:00:00+00:00",
                }

            def __repr__(self):
                return "FakeRequest(symbols=AAPL)"

        evidence = request_object_evidence(FakeRequest())
        self.assertEqual(evidence["sdk_request_repr"], "FakeRequest(symbols=AAPL)")
        self.assertEqual(
            json.loads(evidence["sdk_request_fields_json"])["end"],
            "2021-04-20T00:00:00+00:00",
        )

    def test_contract_selection_requires_exact_daily_and_raw_for_every_pair(self):
        rows = []
        for variant in PROVIDER_CONTRACT_VARIANTS:
            for pair in range(4):
                for endpoint in ("daily", "raw_5min"):
                    exact = variant == DATE_END_EXCLUSIVE_WINDOW
                    rows.append(
                        {
                            "variant": variant,
                            "pair_id": pair,
                            "endpoint": endpoint,
                            "target_session_row_count": 1 if exact else 0,
                            "session_shift_class": (
                                "EXACT_TARGET_ONLY" if exact else "SHIFTED_FORWARD"
                            ),
                            "provider_error_class": "",
                        }
                    )
        self.assertEqual(select_provider_contract(rows), DATE_END_EXCLUSIVE_WINDOW)
        for row in rows:
            if row["variant"] == DATE_END_EXCLUSIVE_WINDOW and row["endpoint"] == "raw_5min":
                row["target_session_row_count"] = 0
                row["session_shift_class"] = "EMPTY"
                break
        self.assertIsNone(select_provider_contract(rows))

    def test_adjacent_daily_and_exact_window_empty_proves_provider_insufficiency(self):
        rows = []
        for variant in PROVIDER_CONTRACT_VARIANTS:
            for pair in range(4):
                for endpoint in ("daily", "raw_5min"):
                    current_daily = variant == CURRENT_CONTRACT and endpoint == "daily"
                    rows.append(
                        {
                            "variant": variant,
                            "pair_id": str(pair),
                            "endpoint": endpoint,
                            "returned_row_count_before_filtering": 2 if current_daily else 0,
                            "target_session_row_count": 0,
                            "session_shift_class": "SHIFTED_FORWARD" if current_daily else "EMPTY",
                            "provider_error_class": "",
                        }
                    )

        outcome = classify_forensics_outcome(rows, circuit_breaker_fired=False)

        self.assertEqual(outcome["decision_status"], "PROVIDER_DATA_INSUFFICIENT")
        self.assertEqual(outcome["root_cause"], "PROVIDER_TARGET_SESSION_EMPTY")
        self.assertIsNone(outcome["selected_request_window_contract"])

    def test_partial_endpoint_errors_are_not_provider_insufficiency(self):
        rows = []
        for variant in PROVIDER_CONTRACT_VARIANTS:
            for pair in range(4):
                for endpoint in ("daily", "raw_5min"):
                    valid_daily = variant == CURRENT_CONTRACT and endpoint == "daily"
                    rows.append(
                        {
                            "variant": variant,
                            "pair_id": str(pair),
                            "endpoint": endpoint,
                            "returned_row_count_before_filtering": 1 if valid_daily else 0,
                            "target_session_row_count": 1 if valid_daily else 0,
                            "session_shift_class": "EXACT_TARGET_ONLY" if valid_daily else "EMPTY",
                            "provider_error_class": "TimeoutError" if pair == 0 and endpoint == "raw_5min" else "",
                        }
                    )

        outcome = classify_forensics_outcome(rows, circuit_breaker_fired=False)

        self.assertEqual(outcome["decision_status"], "CONTRACT_UNRESOLVED")
        self.assertEqual(outcome["root_cause"], "UNKNOWN_CONTRACT_DEFECT")

    def test_resolved_contract_loader_binds_preflight_and_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "reports"
            run_root = root / "run"
            report_dir.mkdir()
            evidence_rows = []
            for index in range(24):
                evidence_path = run_root / "provider_responses" / f"response_{index:02d}.csv"
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text(
                    f"Date,Close\n2021-04-19T13:{index:02d}:00Z,10.0\n",
                    encoding="utf-8",
                )
                evidence_rows.append(
                    {
                        "variant": PROVIDER_CONTRACT_VARIANTS[index // 8],
                        "pair_id": f"pair-{(index % 8) // 2}",
                        "endpoint": "daily" if index % 2 == 0 else "raw_5min",
                        "returned_row_count_before_filtering": 1 if index < 8 else 0,
                        "target_session_row_count": 1 if index < 8 else 0,
                        "session_shift_class": "EXACT_TARGET_ONLY" if index < 8 else "EMPTY",
                        "provider_error_class": "",
                        "evidence_csv_relative_path": evidence_path.relative_to(run_root).as_posix(),
                        "evidence_csv_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                    }
                )
            detail_path = report_dir / "rs_v2_provider_contract_forensics_detail.csv"
            pd.DataFrame(evidence_rows).to_csv(detail_path, index=False)
            evidence_digest = hashlib.sha256(
                "\n".join(
                    sorted(
                        f"{row['evidence_csv_relative_path']}|{row['evidence_csv_sha256']}"
                        for row in evidence_rows
                    )
                ).encode("utf-8")
            ).hexdigest()
            path = report_dir / "rs_v2_provider_contract_forensics_status.json"
            payload = {
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "decision_status": "PROVIDER_CONTRACT_RESOLVED",
                "root_cause": "UTC_DATE_SESSION_MAPPING_ERROR",
                "selected_request_window_contract": CURRENT_CONTRACT,
                "selected_daily_contract": CURRENT_CONTRACT,
                "selected_raw_contract": CURRENT_CONTRACT,
                "preflight_generated_at": "2026-07-13T09:17:31Z",
                "planned_request_count": 24,
                "provider_request_count": 24,
                "retry_count": 0,
                "detail_row_count": 24,
                "detail_csv_sha256": hashlib.sha256(detail_path.read_bytes()).hexdigest(),
                "provider_evidence_digest": evidence_digest,
                "staging_run_root": str(run_root),
                "frozen_dataset_mutation_count": 0,
                "systemic_circuit_breaker_fired": False,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_resolved_provider_contract(
                path,
                preflight_generated_at="2026-07-13T09:17:31Z",
            )
            self.assertEqual(
                loaded["selected_request_window_contract"], CURRENT_CONTRACT
            )

            semantically_invalid = [dict(row) for row in evidence_rows]
            semantically_invalid[0]["target_session_row_count"] = 0
            semantically_invalid[0]["session_shift_class"] = "SHIFTED_FORWARD"
            pd.DataFrame(semantically_invalid).to_csv(detail_path, index=False)
            payload["detail_csv_sha256"] = hashlib.sha256(
                detail_path.read_bytes()
            ).hexdigest()
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "PROVIDER_CONTRACT_EVIDENCE_INVALID"):
                load_resolved_provider_contract(
                    path,
                    preflight_generated_at="2026-07-13T09:17:31Z",
                )

            pd.DataFrame(evidence_rows).to_csv(detail_path, index=False)
            payload["detail_csv_sha256"] = hashlib.sha256(
                detail_path.read_bytes()
            ).hexdigest()
            path.write_text(json.dumps(payload), encoding="utf-8")
            first_evidence = run_root / evidence_rows[0]["evidence_csv_relative_path"]
            first_evidence.write_text("corrupt\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "PROVIDER_CONTRACT_EVIDENCE_INVALID"):
                load_resolved_provider_contract(
                    path,
                    preflight_generated_at="2026-07-13T09:17:31Z",
                )

            payload["decision_status"] = "PROVIDER_DATA_INSUFFICIENT"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "PROVIDER_CONTRACT_NOT_RESOLVED"):
                load_resolved_provider_contract(
                    path,
                    preflight_generated_at="2026-07-13T09:17:31Z",
                )

    def test_resolved_contract_loader_rejects_unbound_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rs_v2_provider_contract_forensics_status.json"
            path.write_text(
                json.dumps(
                    {
                        "decision_status": "PROVIDER_CONTRACT_RESOLVED",
                        "selected_request_window_contract": CURRENT_CONTRACT,
                        "preflight_generated_at": "2026-07-13T09:17:31Z",
                        "provider_request_count": 24,
                        "retry_count": 0,
                        "frozen_dataset_mutation_count": 0,
                        "systemic_circuit_breaker_fired": False,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "PROVIDER_CONTRACT_EVIDENCE_INVALID"):
                load_resolved_provider_contract(
                    path,
                    preflight_generated_at="2026-07-13T09:17:31Z",
                )


class TestProviderForensicsOrchestration(unittest.TestCase):
    @staticmethod
    def _targets_and_schedule():
        targets = [
            {"symbol": "AAA", "target_session": "2021-04-19"},
            {"symbol": "BBB", "target_session": "2021-10-25"},
            {"symbol": "CCC", "target_session": "2022-03-08"},
            {"symbol": "DDD", "target_session": "2022-04-06"},
        ]
        sessions = pd.DatetimeIndex(pd.to_datetime([row["target_session"] for row in targets]))
        schedule = pd.DataFrame(
            {
                "market_open": [
                    session.tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30)
                    for session in sessions
                ],
                "market_close": [
                    session.tz_localize("America/New_York") + pd.Timedelta(hours=16)
                    for session in sessions
                ],
            },
            index=sessions,
        )
        return targets, schedule

    def test_bounded_run_writes_all_evidence_and_required_reports(self):
        targets, schedule = self._targets_and_schedule()

        class FakeRequest:
            def __init__(self, variant, target, endpoint, window):
                self.variant = variant
                self.target = target
                self.endpoint = endpoint
                self.window = window

            def to_request_fields(self):
                return {
                    "symbols": self.target["symbol"],
                    "start": self.window.start.isoformat(),
                    "end": self.window.end.isoformat(),
                }

            def __repr__(self):
                return f"FakeRequest({self.variant},{self.endpoint},{self.target['symbol']})"

        def request_builder(variant, target, endpoint, window):
            return FakeRequest(variant, target, endpoint, window)

        def request_executor(request):
            session = pd.Timestamp(request.target["target_session"])
            timestamp = (
                session.tz_localize("UTC")
                if request.endpoint == "daily"
                else session.tz_localize("America/New_York")
                + pd.Timedelta(hours=9, minutes=30)
            )
            index = pd.MultiIndex.from_tuples(
                [(request.target["symbol"], timestamp)],
                names=["symbol", "timestamp"],
            )
            return pd.DataFrame(
                {
                    "open": [10.0],
                    "high": [11.0],
                    "low": [9.0],
                    "close": [10.5],
                    "volume": [100],
                },
                index=index,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "frozen"
            frozen.mkdir()
            marker = frozen / "immutable.txt"
            marker.write_text("frozen", encoding="utf-8")
            before = hashlib.sha256(marker.read_bytes()).hexdigest()
            spec = ForensicsRunSpec(
                run_id="forensics_fixture",
                targets=targets,
                schedule=schedule,
                run_root=root / "staging" / "forensics_fixture",
                report_dir=root / "reports",
                frozen_root=frozen,
            )
            result = execute_provider_contract_forensics(
                spec,
                request_builder=request_builder,
                request_executor=request_executor,
            )
            detail = pd.read_csv(
                root / "reports" / "rs_v2_provider_contract_forensics_detail.csv"
            )

            self.assertEqual(result["provider_request_count"], 24)
            self.assertEqual(result["retry_count"], 0)
            self.assertEqual(result["decision_status"], "PROVIDER_CONTRACT_RESOLVED")
            self.assertEqual(result["selected_request_window_contract"], CURRENT_CONTRACT)
            self.assertEqual(len(detail), 24)
            self.assertTrue(detail["evidence_csv_sha256"].str.len().eq(64).all())
            self.assertTrue(
                (root / "reports" / "rs_v2_provider_contract_forensics.md").is_file()
            )
            self.assertTrue(
                (root / "reports" / "rs_v2_provider_contract_forensics_status.json").is_file()
            )
            self.assertEqual(before, hashlib.sha256(marker.read_bytes()).hexdigest())
            self.assertEqual(result["frozen_dataset_mutation_count"], 0)

    def test_three_identical_systemic_failures_stop_remaining_requests(self):
        targets, schedule = self._targets_and_schedule()

        class FakeRequest:
            def to_request_fields(self):
                return {}

        def request_builder(_variant, _target, _endpoint, _window):
            return FakeRequest()

        calls = {"count": 0}

        def request_executor(_request):
            calls["count"] += 1
            raise ConnectionError(f"same-class-dynamic-message-{calls['count']}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "frozen"
            frozen.mkdir()
            (frozen / "immutable.txt").write_text("frozen", encoding="utf-8")
            result = execute_provider_contract_forensics(
                ForensicsRunSpec(
                    run_id="systemic_fixture",
                    targets=targets,
                    schedule=schedule,
                    run_root=root / "staging" / "systemic_fixture",
                    report_dir=root / "reports",
                    frozen_root=frozen,
                ),
                request_builder=request_builder,
                request_executor=request_executor,
            )

        self.assertEqual(result["decision_status"], "CONTRACT_SYSTEMIC_FAILURE")
        self.assertEqual(result["provider_request_count"], 3)
        self.assertTrue(result["systemic_circuit_breaker_fired"])

    def test_existing_run_root_is_immutable(self):
        targets, schedule = self._targets_and_schedule()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "frozen"
            frozen.mkdir()
            (frozen / "immutable.txt").write_text("frozen", encoding="utf-8")
            run_root = root / "staging" / "existing"
            run_root.mkdir(parents=True)
            with self.assertRaisesRegex(
                FileExistsError, "PROVIDER_FORENSICS_RUN_ROOT_EXISTS"
            ):
                execute_provider_contract_forensics(
                    ForensicsRunSpec(
                        run_id="existing",
                        targets=targets,
                        schedule=schedule,
                        run_root=run_root,
                        report_dir=root / "reports",
                        frozen_root=frozen,
                    ),
                    request_builder=lambda *_args: object(),
                    request_executor=lambda _request: pd.DataFrame(),
                )

    def test_existing_evidence_is_reclassified_without_provider_calls(self):
        rows = []
        for variant in PROVIDER_CONTRACT_VARIANTS:
            for pair in range(4):
                for endpoint in ("daily", "raw_5min"):
                    current_daily = variant == CURRENT_CONTRACT and endpoint == "daily"
                    rows.append(
                        {
                            "variant": variant,
                            "pair_id": str(pair),
                            "endpoint": endpoint,
                            "returned_row_count_before_filtering": 2 if current_daily else 0,
                            "target_session_row_count": 0,
                            "session_shift_class": "SHIFTED_FORWARD" if current_daily else "EMPTY",
                            "provider_error_class": "",
                        }
                    )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            detail = reports / "rs_v2_provider_contract_forensics_detail.csv"
            pd.DataFrame(rows).to_csv(detail, index=False)
            status_path = reports / "rs_v2_provider_contract_forensics_status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "run_id": "fixture",
                        "decision_status": "CONTRACT_UNRESOLVED",
                        "systemic_circuit_breaker_fired": False,
                        "provider_request_count": 24,
                        "retry_count": 0,
                        "frozen_dataset_mutation_count": 0,
                    }
                ),
                encoding="utf-8",
            )

            updated = reclassify_forensics_reports(reports)

            persisted = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["decision_status"], "PROVIDER_DATA_INSUFFICIENT")
            self.assertEqual(persisted["root_cause"], "PROVIDER_TARGET_SESSION_EMPTY")
            self.assertEqual(len(pd.read_csv(detail)), 24)


if __name__ == "__main__":
    unittest.main()
