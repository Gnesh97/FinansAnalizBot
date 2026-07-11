import ast
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from testdata.delisted_alpaca_price_coverage import (
    CoverageConfig,
    InputValidationError,
    classify_coverage,
    detect_shared_history_boundary,
    evaluate_asset_identity,
    fetch_bars_via_existing_downloader,
    run_coverage,
    run_pilot,
    validate_metadata_input,
    validate_price_bars,
    write_reports,
)


def _symbol(index):
    value = index
    chars = []
    for _ in range(4):
        chars.append(chr(ord("A") + value % 26))
        value //= 26
    return "".join(reversed(chars))


def _metadata(count=100):
    rows = []
    for index in range(count):
        symbol = _symbol(index)
        rows.append({
            "research_security_id": f"SEC_FIXTURE_{index:03d}",
            "symbol": symbol,
            "company_name": f"Fixture Company {index}",
            "exchange": "NASDAQ",
            "delisting_date": "2020-01-15",
            "research_eligibility": True,
            "exclusion_reason": "NONE",
            "manual_review_flag": index != 0,
            "run_id": "20260711_METADATA_FIXTURE",
            "report_schema_version": "2.0.0",
            "generated_at": "2026-07-11T00:00:00Z",
        })
    return pd.DataFrame(rows)


def _inventory(metadata):
    return [{
        "asset_id": f"asset-{row.research_security_id}",
        "symbol": row.symbol,
        "name": row.company_name,
        "exchange": row.exchange,
        "status": "inactive",
        "raw_source_status": "inactive",
    } for row in metadata.itertuples(index=False)]


def _bars(start="2019-01-02", observations=252):
    days = pd.bdate_range(start, periods=observations)
    return [{"t": day.isoformat(), "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 1000} for day in days]


class DelistedAlpacaPriceCoverageTest(unittest.TestCase):
    def test_input_validation_fails_closed(self):
        metadata = _metadata(99)
        with self.assertRaises(InputValidationError):
            validate_metadata_input(metadata)

        metadata = _metadata()
        metadata.loc[1, "research_security_id"] = metadata.loc[0, "research_security_id"]
        with self.assertRaises(InputValidationError):
            validate_metadata_input(metadata)

        metadata = _metadata()
        metadata.loc[0, "exclusion_reason"] = "SPAC"
        with self.assertRaises(InputValidationError):
            validate_metadata_input(metadata)

    def test_fixed_denominator_is_preserved_without_top_up(self):
        metadata = _metadata()
        inventory = _inventory(metadata)
        inventory.append({
            "asset_id": "active-collision", "symbol": metadata.loc[0, "symbol"],
            "name": metadata.loc[0, "company_name"], "exchange": "NASDAQ",
            "status": "active", "raw_source_status": "active",
        })
        calls = []

        def fetch(symbol):
            calls.append(symbol)
            return {"request_status": "SUCCESS", "bars": []}

        rows, identities, summary = run_coverage(
            metadata, inventory, CoverageConfig(), fetch, inventory_complete=True
        )
        self.assertEqual(len(rows), 100)
        self.assertEqual(len(identities), 100)
        self.assertEqual(summary["metadata_pilot_count"], 100)
        self.assertEqual(summary["identity_price_query_eligible_count"], 99)
        self.assertEqual(summary["price_query_attempted_count"], 99)
        self.assertEqual(len(calls), 99)
        self.assertIn(metadata.loc[0, "symbol"], {row["symbol"] for row in rows})

    def test_identity_statuses_compare_complete_inventory_and_metadata(self):
        row = _metadata().iloc[0].to_dict()
        symbol = row["symbol"]
        base = {"asset_id": "1", "symbol": symbol, "name": row["company_name"], "exchange": "NASDAQ", "status": "inactive"}
        self.assertEqual(evaluate_asset_identity(row, [], True)["asset_identity_status"], "NO_ALPACA_ASSET_MATCH")
        self.assertEqual(evaluate_asset_identity(row, [base], True)["asset_identity_status"], "UNIQUE_INACTIVE_MATCH")
        active = {**base, "asset_id": "2", "status": "active"}
        self.assertEqual(evaluate_asset_identity(row, [base, active], True)["asset_identity_status"], "ACTIVE_INACTIVE_COLLISION")
        other = {**base, "asset_id": "3"}
        self.assertEqual(evaluate_asset_identity(row, [base, other], True)["asset_identity_status"], "MULTIPLE_INACTIVE_IDS")
        self.assertEqual(evaluate_asset_identity(row, [{**base, "name": "Other Issuer"}], True)["asset_identity_status"], "NAME_MISMATCH")
        self.assertEqual(evaluate_asset_identity(row, [{**base, "exchange": "NYSE"}], True)["asset_identity_status"], "EXCHANGE_MISMATCH")

    def test_ambiguous_identity_receives_zero_bar_calls(self):
        metadata = _metadata()
        inventory = _inventory(metadata)
        inventory[0]["status"] = "active"
        inventory[0]["raw_source_status"] = "active"
        calls = []
        rows, _, _ = run_coverage(
            metadata,
            inventory,
            CoverageConfig(),
            lambda symbol: calls.append(symbol),
            inventory_complete=True,
        )
        first = next(row for row in rows if row["symbol"] == metadata.loc[0, "symbol"])
        self.assertEqual(first["request_status"], "NOT_ATTEMPTED")
        self.assertEqual(first["data_status"], "NOT_QUERIED_IDENTITY_AMBIGUOUS")
        self.assertNotIn(metadata.loc[0, "symbol"], calls)

    def test_no_match_and_ambiguity_use_distinct_not_queried_statuses(self):
        metadata = _metadata()
        inventory = _inventory(metadata.iloc[2:])
        inventory[0]["status"] = "active"
        inventory[0]["raw_source_status"] = "active"
        rows, _, summary = run_coverage(
            metadata, inventory, CoverageConfig(), None, inventory_complete=True
        )
        no_match = next(row for row in rows if row["symbol"] == metadata.loc[0, "symbol"])
        ambiguous = next(row for row in rows if row["symbol"] == metadata.loc[2, "symbol"])
        self.assertEqual(no_match["data_status"], "NOT_QUERIED_NO_ASSET_MATCH")
        self.assertEqual(ambiguous["data_status"], "NOT_QUERIED_IDENTITY_AMBIGUOUS")
        self.assertEqual(summary["no_asset_match_count"], 2)
        self.assertEqual(summary["identity_ambiguous_count"], 1)

    def test_possible_ticker_reuse_is_not_every_identity_ambiguity(self):
        metadata = _metadata()
        inventory = _inventory(metadata)
        inventory[0]["status"] = "active"
        inventory[0]["raw_source_status"] = "active"
        inventory[1]["name"] = "Other Issuer"
        rows, _, summary = run_coverage(
            metadata, inventory, CoverageConfig(), None, inventory_complete=True
        )
        self.assertEqual(summary["identity_ambiguous_count"], 2)
        self.assertEqual(summary["possible_ticker_reuse_count"], 1)
        self.assertTrue(next(row for row in rows if row["symbol"] == metadata.loc[0, "symbol"])["possible_ticker_reuse"])
        self.assertFalse(next(row for row in rows if row["symbol"] == metadata.loc[1, "symbol"])["possible_ticker_reuse"])

    def test_ads_security_class_conflicts_are_detected_generically(self):
        metadata = _metadata()
        metadata.loc[0, ["symbol", "company_name"]] = ["SOGO", "Sogou Inc"]
        metadata.loc[1, ["symbol", "company_name"]] = ["TLC", "Taiwan Liposome Company Ltd"]
        inventory = _inventory(metadata)
        inventory[0]["name"] = "Sogou Inc. American Depositary Shares"
        inventory[1]["name"] = "Taiwan Liposome Company Ltd. American Depository Shares"
        rows, _, summary = run_coverage(
            metadata, inventory, CoverageConfig(), None, inventory_complete=True
        )
        conflicts = {row["symbol"]: row for row in rows if row["symbol"] in {"SOGO", "TLC"}}
        self.assertTrue(conflicts["SOGO"]["alpaca_security_class_conflict"])
        self.assertTrue(conflicts["TLC"]["alpaca_security_class_conflict"])
        self.assertEqual(summary["alpaca_security_class_conflict_count"], 2)

    def test_history_boundary_wins_when_clean_representative_subset_exists(self):
        rows = [
            {
                "first_date": "2020-07-27", "observations": 250,
                "identity_price_query_eligible": True, "identity_ambiguous": False,
                "possible_ticker_reuse": False, "alpaca_security_class_conflict": False,
                "terminal_history_mismatch": False, "data_status": "VALID",
            }
            for _ in range(10)
        ]
        rows.append({
            "identity_price_query_eligible": False, "identity_ambiguous": True,
            "possible_ticker_reuse": False, "alpaca_security_class_conflict": False,
            "terminal_history_mismatch": False, "data_status": "NOT_QUERIED_IDENTITY_AMBIGUOUS",
        })
        self.assertEqual(classify_coverage(rows, inventory_complete=True), "INCONCLUSIVE_HISTORY_BOUNDARY")

    def test_existing_downloader_path_is_invoked(self):
        config = CoverageConfig(start_date="2019-01-01", end_date="2020-01-01")
        with patch("testdata.vericekici_updated.download_bars_with_retry") as download:
            download.return_value = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
            result = fetch_bars_via_existing_downloader("key", "secret", "TEST", config)
        download.assert_called_once()
        self.assertEqual(result["request_status"], "SUCCESS")
        self.assertEqual(result["bars"], [])

    def test_zero_sparse_and_request_statuses_remain_separate(self):
        empty = validate_price_bars([], "2020-01-15", "2026-07-11")
        self.assertEqual(empty["request_status"], "SUCCESS")
        self.assertEqual(empty["data_status"], "EMPTY")

        sparse = validate_price_bars(_bars(observations=12), "2020-01-15", "2026-07-11")
        self.assertEqual(sparse["request_status"], "SUCCESS")
        self.assertEqual(sparse["data_status"], "TOO_SPARSE")
        self.assertFalse(sparse["usable_1_year"])

        failed = validate_price_bars([], "2020-01-15", "2026-07-11", request_status="HTTP_ERROR")
        self.assertEqual(failed["request_status"], "HTTP_ERROR")
        self.assertEqual(failed["data_status"], "EMPTY")

    def test_post_delist_and_current_bars_are_suspicious(self):
        current = date(2026, 7, 11)
        days = [current - timedelta(days=2), current - timedelta(days=1)]
        bars = [{"t": day.isoformat(), "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 1000} for day in days]
        result = validate_price_bars(bars, "2020-01-15", current.isoformat())
        self.assertTrue(result["bars_after_metadata_delisting_date"])
        self.assertTrue(result["bars_near_current_date"])
        self.assertTrue(result["possible_ticker_reuse"])
        self.assertTrue(result["terminal_history_mismatch"])

    def test_shared_first_date_boundary_and_precedence(self):
        rows = [{"first_date": "2020-07-27", "observations": 250, "identity_price_query_eligible": True, "data_status": "VALID"} for _ in range(10)]
        boundary = detect_shared_history_boundary(rows)
        self.assertTrue(boundary["shared_history_boundary_detected"])
        self.assertEqual(boundary["first_date_frequency"], 10)
        self.assertEqual(classify_coverage(rows, inventory_complete=True), "INCONCLUSIVE_HISTORY_BOUNDARY")

        rows[0]["possible_ticker_reuse"] = True
        self.assertEqual(classify_coverage(rows, inventory_complete=True), "INCONCLUSIVE_ASSET_IDENTITY_CONTAMINATION")

    def test_outputs_share_one_run_metadata_set(self):
        metadata = _metadata()
        rows, identities, summary = run_coverage(
            metadata, _inventory(metadata), CoverageConfig(),
            lambda symbol: {"request_status": "SUCCESS", "bars": []},
            inventory_complete=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = write_reports(rows, identities, summary, root / "latest", root / "runs")
            manifest = json.loads((root / "latest" / "delisted_alpaca_price_run_manifest.json").read_text(encoding="utf-8"))
            coverage = pd.read_csv(root / "latest" / "delisted_alpaca_price_coverage.csv")
            identity = pd.read_csv(root / "latest" / "delisted_alpaca_price_identity_diagnostics.csv")
            fallback = pd.read_csv(root / "latest" / "delisted_second_provider_fallback_manifest.csv")
            self.assertEqual(coverage["run_id"].nunique(), 1)
            self.assertEqual(identity["run_id"].nunique(), 1)
            self.assertEqual(coverage.loc[0, "run_id"], manifest["run_id"])
            self.assertEqual(identity.loc[0, "source_metadata_run_id"], "20260711_METADATA_FIXTURE")
            self.assertEqual(len(fallback), 100)
            self.assertTrue((root / "runs" / result["run_id"] / "delisted_alpaca_price_coverage.md").exists())

    def test_cache_only_is_network_free_and_reaches_no_state_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_path = root / "metadata.csv"
            _metadata().to_csv(metadata_path, index=False)
            config = CoverageConfig(mode="CACHE_ONLY")
            with patch("urllib.request.urlopen") as urlopen, \
                 patch("testdata.vericekici_updated.download_bars_with_retry") as download:
                result = run_pilot(
                    config,
                    metadata_path=metadata_path,
                    cache_dir=root / "empty-cache",
                    latest_dir=root / "latest",
                    runs_dir=root / "runs",
                )
            urlopen.assert_not_called()
            download.assert_not_called()
            self.assertEqual(result["summary"]["metadata_pilot_count"], 100)
            self.assertEqual(result["summary"]["price_query_attempted_count"], 0)
            self.assertEqual(result["summary"]["classification"], "INCONCLUSIVE_ASSET_IDENTITY_CONTAMINATION")
            self.assertFalse((root / "state").exists())

    def test_local_live_cache_has_final_exact_coverage_counts_and_100_row_fallback(self):
        cache_dir = Path("testdata/research_data/delisted_alpaca_price_coverage")
        metadata_path = Path("testdata/reports/latest/delisted_metadata_master_pilot.csv")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("urllib.request.urlopen") as urlopen, \
                 patch("testdata.vericekici_updated.download_bars_with_retry") as download:
                result = run_pilot(
                    CoverageConfig(mode="CACHE_ONLY"),
                    metadata_path=metadata_path,
                    cache_dir=cache_dir,
                    latest_dir=root / "latest",
                    runs_dir=root / "runs",
                )
            urlopen.assert_not_called()
            download.assert_not_called()
            summary = result["summary"]
            self.assertEqual(summary["no_asset_match_count"], 63)
            self.assertEqual(summary["identity_ambiguous_count"], 19)
            self.assertEqual(summary["possible_ticker_reuse_count"], 8)
            self.assertEqual(summary["classification"], "INCONCLUSIVE_HISTORY_BOUNDARY")
            self.assertEqual(summary["operational_decision"], "ALPACA_NOT_VIABLE_AS_PRIMARY_DELISTED_PRICE_SOURCE")
            fallback = pd.read_csv(root / "latest" / "delisted_second_provider_fallback_manifest.csv")
            self.assertEqual(len(fallback), 100)
            self.assertTrue(fallback.loc[fallback["symbol"].isin(["SOGO", "TLC"]), "alpaca_security_class_conflict"].all())

    def test_module_has_no_strategy_backtest_broker_state_or_bars_rest_path(self):
        module_path = Path("testdata/delisted_alpaca_price_coverage.py")
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        forbidden = ("strategy", "backtest", "broker", "ledger", "state")
        self.assertFalse(any(any(token in module.lower() for token in forbidden) for module in imported_modules))
        self.assertNotIn("/v2/stocks/bars", source)
        self.assertNotIn("requests.get", source)
        self.assertNotIn("requests.post", source)


if __name__ == "__main__":
    unittest.main()
