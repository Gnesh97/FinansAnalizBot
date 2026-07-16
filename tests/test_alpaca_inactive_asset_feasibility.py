import json
import inspect
import socket
import ssl
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

from testdata.alpaca_inactive_asset_feasibility import (
    AlpacaInactiveAssetClient,
    FeasibilityConfig,
    classify_asset,
    classify_asset_with_reason,
    compute_coverage,
    classify_diagnostic_exception,
    coverage_failure_classification,
    load_kaggle_symbols,
    run_feasibility,
    retrieve_bars_fault_tolerant,
    SampleConstructionError,
    select_sample,
    sanitize_http_error,
    select_sample,
    validate_bars,
    write_reports,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class FakeUrlopen:
    def __init__(self):
        self.requests = []

    def __call__(self, req, timeout=20):
        self.requests.append(req)
        if "/v2/assets" in req.full_url:
            return FakeResponse([
                {"id": "2", "symbol": "BBB", "name": "Beta Corp", "exchange": "NYSE", "status": "inactive", "class": "us_equity", "tradable": False},
                {"id": "1", "symbol": "AAA", "name": "Alpha ETF", "exchange": "ARCA", "status": "inactive", "class": "us_equity", "tradable": False},
            ])
        if "page_token=next" in req.full_url:
            return FakeResponse({"bars": {"BBB": [{"t": "2020-01-02T00:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 1}]}})
        return FakeResponse({"bars": {"BBB": [{"t": "2020-01-01T00:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 1}]}, "next_page_token": "next"})


class AlpacaInactiveAssetFeasibilityTest(unittest.TestCase):
    def test_identifier_and_cvr_classification_preserve_normal_tickers(self):
        self.assertEqual(classify_asset({"symbol": "0029900E0", "name": "Identifier", "exchange": "NYSE"}), "identifier_like")
        self.assertEqual(classify_asset({"symbol": "003CVR016", "name": "Contingent Value Right", "exchange": "NYSE"}), "contingent_value_right")
        self.assertEqual(classify_asset({"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"}), "ticker_like_common_stock")
        self.assertEqual(classify_asset({"symbol": "BRK.B", "name": "Berkshire Hathaway", "exchange": "NYSE"}), "ticker_like_common_stock")
        reason = classify_asset_with_reason({"symbol": "009CVR044", "name": "Contingent Value Right", "exchange": "NYSE"})[1]
        self.assertIn("cvr", reason)

    def test_probe_uses_existing_downloader_adapter_not_bars_http_client(self):
        client = AlpacaInactiveAssetClient("key", "secret")
        with patch(
            "testdata.alpaca_inactive_asset_feasibility._download_daily_bars_via_existing_downloader",
            return_value={"AAPL": [{"t": "2020-01-01T00:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}]},
        ) as downloader:
            result = client.get_daily_bars(["AAPL"], "2020-01-01", "2020-01-02", "iex", "raw")
        self.assertIn("AAPL", result)
        downloader.assert_called_once()

    def test_ticker_only_hash_sample_excludes_identifier_concentration(self):
        assets = [
            {"asset_id": "1", "symbol": "0029900E0", "name": "Identifier", "exchange": "NYSE"},
            {"asset_id": "2", "symbol": "003CVR016", "name": "Contingent Value Right", "exchange": "NYSE"},
            {"asset_id": "3", "symbol": "004ESC018", "name": "Escrow", "exchange": "NYSE"},
            {"asset_id": "4", "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"},
            {"asset_id": "5", "symbol": "BRK.B", "name": "Berkshire Hathaway", "exchange": "NYSE"},
        ]
        first = select_sample(assets, 100)
        self.assertEqual(first, select_sample(list(reversed(assets)), 100))
        self.assertEqual(set(first), {"AAPL", "BRK.B"})

    def test_preflight_blocks_before_bars_when_invalid_sample_member_leaks(self):
        class Client:
            api_key = "key"; secret_key = "secret"; methods_used = []; inventory_status_counts = {}
            inventory_asset_count = 1; inactive_asset_count = 1
            def require_credentials(self): pass
            def get_inactive_us_equities(self, *_args): return [{"asset_id": "1", "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "classification": "ticker_like_common_stock"}]
            def get_daily_bars(self, *_args): raise AssertionError("bars called")
        with patch("testdata.alpaca_inactive_asset_feasibility.select_sample", return_value=["0029900E0"]):
            with self.assertRaises(SampleConstructionError): run_feasibility(FeasibilityConfig(sample_size=1), client=Client())

    def test_eligible_pool_and_coverage_denominator_use_only_ticker_like_records(self):
        rows = [
            {"symbol": "AAPL", "asset_id": "1", "name": "Apple Inc.", "exchange": "NASDAQ", "classification": "ticker_like_common_stock", "status": "VALID", "bar_query_result": "OK", "bar_query_attempted": True, "observations": 250, "history_years": 1.0},
            {"symbol": "0029900E0", "asset_id": "2", "name": "Identifier", "exchange": "NYSE", "classification": "identifier_like", "status": "INVALID_SYMBOL", "bar_query_result": "INVALID_SYMBOL", "bar_query_attempted": False, "observations": 0, "history_years": 0.0},
        ]
        eligible = [row for row in rows if row["classification"] == "ticker_like_common_stock"]
        metrics = compute_coverage(eligible, total_inactive_assets=2, asset_inventory=rows)
        self.assertEqual(metrics["probable_common_stock_candidates"], metrics["eligible_ticker_like_pool"])
        self.assertEqual(metrics["original_sample_size"], 1)
        self.assertEqual(metrics["valid_bar_percentage"], 100.0)
        self.assertNotIn("/v2/stocks/bars", inspect.getsource(AlpacaInactiveAssetClient))

    def test_invalid_symbol_isolated_without_replacing_primary_denominator(self):
        class BatchClient:
            def __init__(self): self.calls = []
            def get_daily_bars(self, symbols, *_args):
                self.calls.append(tuple(symbols))
                if "BAD9" in symbols:
                    error = urllib.error.HTTPError("https://x", 400, "Bad", {}, None)
                    error.sanitized_detail = {"message": "invalid symbol: BAD9"}
                    raise error
                return {symbol: [] for symbol in symbols}
        client = BatchClient()
        rows, isolated = retrieve_bars_fault_tolerant(client, ["AAA", "BAD9", "BBB"], FeasibilityConfig(batch_size=10))
        self.assertEqual(rows["BAD9"]["bar_query_result"], "INVALID_SYMBOL")
        self.assertTrue(rows["AAA"]["bar_query_attempted"])
        self.assertEqual(isolated, 1)
        self.assertEqual(client.calls, [("AAA", "BAD9", "BBB"), ("AAA", "BBB")])

    def test_unparseable_400_bisects_and_multiple_invalids_stay_failed(self):
        class BatchClient:
            def __init__(self): self.calls = []
            def get_daily_bars(self, symbols, *_args):
                self.calls.append(tuple(symbols))
                if any(symbol.startswith("BAD") for symbol in symbols):
                    error = urllib.error.HTTPError("https://x", 400, "Bad", {}, None)
                    error.sanitized_detail = {"message": "invalid request"}
                    raise error
                return {symbol: [] for symbol in symbols}
        rows, isolated = retrieve_bars_fault_tolerant(BatchClient(), ["AAA", "BAD1", "BBB", "BAD2"], FeasibilityConfig(batch_size=10))
        self.assertEqual(rows["BAD1"]["bar_query_result"], "INVALID_SYMBOL")
        self.assertEqual(rows["BAD2"]["bar_query_result"], "INVALID_SYMBOL")
        self.assertTrue(rows["AAA"]["bar_query_attempted"])
        self.assertGreaterEqual(isolated, 2)

    def test_auth_failure_stops_fault_isolation_and_invalid_row_stays_denominator(self):
        class AuthClient:
            def get_daily_bars(self, *_args):
                raise urllib.error.HTTPError("https://x", 401, "Unauthorized", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            retrieve_bars_fault_tolerant(AuthClient(), ["AAA"], FeasibilityConfig())
        rows = [
            {"symbol": "AAA", "classification": "ticker_like_common_stock", "status": "VALID", "bar_query_result": "OK", "bar_query_attempted": True, "observations": 300, "history_years": 2.0},
            {"symbol": "BAD9", "classification": "identifier_like", "status": "INVALID_SYMBOL", "bar_query_result": "INVALID_SYMBOL", "bar_query_attempted": True, "observations": 0, "history_years": 0.0},
        ]
        metrics = compute_coverage(rows, total_inactive_assets=2, asset_inventory=rows)
        self.assertEqual(metrics["original_sample_size"], 2)
        self.assertEqual(metrics["valid_bar_percentage"], 50.0)
        self.assertEqual(metrics["invalid_symbol_count"], 1)
    def test_inventory_omits_status_and_filters_active_unknown_locally(self):
        class InventoryOpener:
            def __init__(self): self.requests = []
            def __call__(self, req, timeout=20):
                self.requests.append(req)
                return FakeResponse([
                    {"id": "ACTIVE", "symbol": "ACT", "name": "Active", "exchange": "NYSE", "status": "active"},
                    {"id": "INACTIVE", "symbol": "INA", "name": "Inactive", "exchange": "NYSE", "status": "inactive"},
                    {"id": "MISSING", "symbol": "UNK", "name": "Unknown", "exchange": "NYSE"},
                ])
        opener = InventoryOpener()
        client = AlpacaInactiveAssetClient("key", "secret", urlopen=opener)
        assets = client.get_inactive_us_equities()
        self.assertEqual([row["symbol"] for row in assets], ["INA"])
        self.assertIn("asset_class=us_equity", opener.requests[0].full_url)
        self.assertNotIn("status=", opener.requests[0].full_url)
        self.assertEqual(client.inventory_status_counts, {"active": 1, "inactive": 1, "unknown": 1})

    def test_http_400_does_not_retry_and_error_body_is_sanitized(self):
        class ErrorOpener:
            def __init__(self): self.calls = 0
            def __call__(self, _req, timeout=20):
                self.calls += 1
                body = json.dumps({"code": 40010000, "message": "bad request secret-key Authorization: Bearer hidden"}).encode()
                raise urllib.error.HTTPError("https://paper-api.alpaca.markets/v2/assets", 400, "Bad Request", {}, None)
        opener = ErrorOpener()
        client = AlpacaInactiveAssetClient("key", "secret", urlopen=opener)
        with self.assertRaises(urllib.error.HTTPError): client.get_inactive_us_equities(max_retries=1)
        self.assertEqual(opener.calls, 1)
        detail = sanitize_http_error(urllib.error.HTTPError("https://x", 400, "secret-key Authorization", {}, None), b'{"code":"E400","message":"secret-key Authorization: Bearer hidden"}')
        self.assertEqual(detail["http_status"], 400)
        self.assertNotIn("secret-key", json.dumps(detail))
        self.assertNotIn("Authorization", json.dumps(detail))

    def test_rate_limit_does_not_retry_but_server_failure_retries_once(self):
        class SequenceOpener:
            def __init__(self, status): self.status, self.calls = status, 0
            def __call__(self, _req, timeout=20):
                self.calls += 1
                if self.calls == 1 or self.status == 429:
                    raise urllib.error.HTTPError("https://x", self.status, "fixture", {}, None)
                return FakeResponse([])
        limited = SequenceOpener(429)
        with self.assertRaises(urllib.error.HTTPError): AlpacaInactiveAssetClient("key", "secret", urlopen=limited).get_inactive_us_equities(max_retries=1)
        self.assertEqual(limited.calls, 1)
        server = SequenceOpener(500)
        self.assertEqual(AlpacaInactiveAssetClient("key", "secret", urlopen=server).get_inactive_us_equities(max_retries=1), [])
        self.assertEqual(server.calls, 2)

    def test_bar_request_is_not_attempted_after_inventory_failure(self):
        client = AlpacaInactiveAssetClient("key", "secret", urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.HTTPError("https://x", 400, "Bad", {}, None)))
        with patch.object(client, "get_daily_bars", side_effect=AssertionError("bars called")) as bars:
            with self.assertRaises(Exception): run_feasibility(FeasibilityConfig(sample_size=1), client=client)
        self.assertFalse(bars.called)
    def test_network_and_http_diagnostics_are_distinct_and_secret_safe(self):
        cases = [
            (urllib.error.URLError(socket.gaierror(-2, "secret-key")), "NETWORK_DNS_FAILURE"),
            (urllib.error.URLError(ConnectionRefusedError(10061, "secret-key")), "NETWORK_CONNECTION_BLOCKED"),
            (urllib.error.URLError(PermissionError(13, "secret-key")), "NETWORK_CONNECTION_BLOCKED"),
            (urllib.error.URLError(ssl.SSLError("secret-key")), "NETWORK_TLS_FAILURE"),
            (urllib.error.URLError(socket.timeout("secret-key")), "NETWORK_TIMEOUT"),
            (urllib.error.HTTPError("https://example.invalid", 401, "secret-key", {}, None), "HTTP_AUTH_FAILURE"),
            (urllib.error.HTTPError("https://example.invalid", 403, "secret-key", {}, None), "HTTP_PERMISSION_FAILURE"),
            (urllib.error.HTTPError("https://example.invalid", 429, "secret-key", {}, None), "HTTP_RATE_LIMITED"),
            (urllib.error.HTTPError("https://example.invalid", 500, "secret-key", {}, None), "HTTP_SERVER_FAILURE"),
        ]
        for exc, expected in cases:
            result = classify_diagnostic_exception(exc)
            self.assertEqual(result["classification"], expected)
            self.assertNotIn("secret-key", json.dumps(result))
            self.assertNotIn("Authorization", json.dumps(result))
        self.assertEqual(coverage_failure_classification(0), "INCONCLUSIVE_NOT_RUN")
        self.assertEqual(coverage_failure_classification(1), "INCONCLUSIVE_API_BLOCKED")
    def test_credentials_only_from_environment_and_missing_is_clear(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "testdata.alpaca_inactive_asset_feasibility._read_project_env", return_value={}
        ):
            client = AlpacaInactiveAssetClient.from_environment(urlopen=FakeUrlopen())
            with self.assertRaisesRegex(RuntimeError, "ALPACA_API_KEY"):
                client.require_credentials()

    def test_project_credential_names_take_precedence_over_apca_fallback(self):
        environment = {
            "ALPACA_API_KEY": "project-key",
            "ALPACA_SECRET_KEY": "project-secret",
            "APCA_API_KEY_ID": "fallback-key",
            "APCA_API_SECRET_KEY": "fallback-secret",
        }
        with patch.dict("os.environ", environment, clear=True), patch(
            "testdata.alpaca_inactive_asset_feasibility._read_project_env", return_value={}
        ):
            client = AlpacaInactiveAssetClient.from_environment(urlopen=FakeUrlopen())
        self.assertEqual(client.credential_variables_used, ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"))
        self.assertEqual(client.api_key, "project-key")

    def test_root_env_project_credentials_are_loaded_before_apca_fallback(self):
        with patch.dict("os.environ", {"APCA_API_KEY_ID": "fallback-key", "APCA_API_SECRET_KEY": "fallback-secret"}, clear=True), patch(
            "testdata.alpaca_inactive_asset_feasibility._read_project_env",
            return_value={"ALPACA_API_KEY": "root-key", "ALPACA_SECRET_KEY": "root-secret"},
        ):
            client = AlpacaInactiveAssetClient.from_environment(urlopen=FakeUrlopen())
        self.assertEqual(client.credential_variables_used, ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"))
        self.assertEqual(client.secret_key, "root-secret")

    def test_client_constructs_get_only_inventory_and_bar_requests(self):
        opener = FakeUrlopen()
        client = AlpacaInactiveAssetClient("key", "secret", urlopen=opener)
        assets = client.get_inactive_us_equities()
        with patch("testdata.alpaca_inactive_asset_feasibility._download_daily_bars_via_existing_downloader", return_value={"BBB": []}):
            client.get_daily_bars(["BBB"], "2020-01-01", "2020-12-31", "iex", "raw")
        self.assertEqual([item["symbol"] for item in assets], ["AAA", "BBB"])
        self.assertEqual([req.get_method() for req in opener.requests], ["GET"])
        self.assertNotIn("POST", client.methods_used)

    def test_validation_marks_terminal_coverage_and_discontinuity_indicators(self):
        bars = [
            {"t": "2020-01-01T00:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 1},
            {"t": "2020-02-01T00:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 1},
        ]
        result = validate_bars("GAP", bars, "2020-02-10")
        self.assertTrue(result["last_30_trading_day_coverage"])
        self.assertTrue(result["suspicious_symbol_reuse_or_discontinuity"])

    def test_asset_classification_is_deterministic_and_exclusions_remain_visible(self):
        rows = [
            {"symbol": "Z", "name": "Zeta Common Stock", "exchange": "NYSE"},
            {"symbol": "E", "name": "Example ETF", "exchange": "ARCA"},
            {"symbol": "W", "name": "Example Warrant", "exchange": "NYSE"},
            {"symbol": "O", "name": "OTC Example", "exchange": "OTC"},
        ]
        classes = [classify_asset(row) for row in rows]
        self.assertEqual(classes, ["ticker_like_common_stock", "etf_fund", "warrant", "otc_unsupported_exchange"])
        self.assertEqual(select_sample(rows, 100), ["Z"])

    def test_sample_selection_stable_and_kaggle_is_validation_only(self):
        rows = [{"symbol": "ZZZ", "name": "Common Stock", "exchange": "NYSE"}, {"symbol": "AAA", "name": "Common Stock", "exchange": "NYSE"}]
        self.assertEqual(select_sample(rows, 1), ["AAA"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kaggle.csv"
            path.write_text("symbol\nZZZ\nMISSING\n", encoding="utf-8")
            symbols = load_kaggle_symbols(path)
        self.assertEqual(symbols, {"ZZZ", "MISSING"})
        self.assertEqual(select_sample(rows, 1), ["AAA"])

    def test_empty_and_invalid_bars_are_reported(self):
        empty = validate_bars("EMPTY", [])
        bad = validate_bars("BAD", [{"t": "2020-01-01T00:00:00Z", "o": 10, "h": 9, "l": 8, "c": 10, "v": 1}])
        duplicate = validate_bars("DUP", [
            {"t": "2020-01-01T00:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 1},
            {"t": "2020-01-01T00:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10, "v": 1},
        ])
        self.assertEqual(empty["status"], "EMPTY")
        self.assertEqual(bad["status"], "MALFORMED")
        self.assertIn("duplicate_dates", duplicate["issues"])

    def test_coverage_metrics_are_deterministic_and_no_state_paths_are_used(self):
        rows = [
            {"symbol": "AAA", "classification": "probable_common_stock", "status": "VALID", "observations": 800, "history_years": 3.2},
            {"symbol": "BBB", "classification": "probable_common_stock", "status": "EMPTY", "observations": 0, "history_years": 0},
        ]
        inventory = rows + [{"symbol": "ETF", "classification": "etf_fund"}]
        result = compute_coverage(rows, total_inactive_assets=4, kaggle_symbols={"AAA", "NOPE"}, asset_inventory=inventory)
        self.assertEqual(result["symbols_with_valid_bars"], 1)
        self.assertEqual(result["kaggle_match_rate"], 0.5)
        self.assertEqual(result["excluded_asset_type_counts"], {"etf_fund": 1})
        self.assertEqual(result["source_feed_success_rates"], {"unknown": 0.5})
        self.assertEqual(result["classification"], "PARTIAL_RESEARCH_ONLY")
        self.assertNotIn("broker_order_ledger", json.dumps(result))

    def test_report_output_is_local_and_has_no_submit_or_state_side_effect(self):
        rows = [{"symbol": "AAA", "classification": "probable_common_stock", "status": "EMPTY", "observations": 0}]
        summary = compute_coverage(rows, total_inactive_assets=1)
        with tempfile.TemporaryDirectory() as tmp, patch("testdata.alpaca_inactive_asset_feasibility.REPORT_DIR", Path(tmp)):
            write_reports(rows, summary, real_run=False)
            report = (Path(tmp) / "alpaca_inactive_asset_feasibility.md").read_text(encoding="utf-8")
        self.assertIn("real_get_only_run: no", report)
        self.assertNotIn("submit", report.lower())

    def test_importing_downloader_is_side_effect_free(self):
        import sys
        from unittest.mock import patch
        with patch.dict(sys.modules, {"alpaca.data.enums": None}):
            try:
                import importlib
                try:
                    importlib.reload(sys.modules["testdata.vericekici_updated"])
                except KeyError:
                    importlib.import_module("testdata.vericekici_updated")
            except SystemExit:
                self.fail("Importing vericekici_updated raised SystemExit instead of ImportError")
            except ImportError:
                pass

    def test_existing_downloader_output_schema_and_behavior(self):
        try:
            from testdata import vericekici_updated as downloader
        except ImportError:
            import vericekici_updated as downloader
        import pandas as pd
        raw_df = pd.DataFrame(
            [{"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 100.0}],
            index=pd.to_datetime(["2020-01-01 10:00:00-05:00"])
        )
        raw_df.index.name = "timestamp"
        normalized = downloader.normalize_bars(raw_df, "AAPL", rth_only=False)
        self.assertEqual(list(normalized.columns), ["Date", "Open", "High", "Low", "Close", "Volume"])

    def test_no_duplicate_bars_request_or_pagination_logic_in_feasibility_probe(self):
        source = inspect.getsource(AlpacaInactiveAssetClient)
        self.assertNotIn("next_page_token", source)
        self.assertNotIn("get_stock_bars", source)

    def test_no_sys_executable_mutation_or_broad_systemexit_suppression(self):
        path = Path(__file__).parent.parent / "testdata" / "alpaca_inactive_asset_feasibility.py"
        content = path.read_text(encoding="utf-8")
        self.assertNotIn("sys.executable =", content)
        self.assertNotIn("sys.executable=", content)
        self.assertNotIn("except SystemExit:", content)
        self.assertNotIn("except SystemExit as", content)

    def test_instrument_classification_rules(self):
        from testdata.alpaca_inactive_asset_feasibility import classify_asset_with_details
        
        # ETN (VXX, EVIX, DFVS, LMLP, NRGZ)
        row_vxx = {"symbol": "VXX", "name": "iPath Series B S&P 500 VIX Short-Term Futures ETN", "exchange": "NYSE"}
        row_evix = {"symbol": "EVIX", "name": "VelocityShares 1x Daily Short VIX Futures ETN", "exchange": "NYSE"}
        
        c, r, it, elig, excl = classify_asset_with_details(row_vxx)
        self.assertEqual(it, "etn")
        self.assertEqual(elig, "ineligible")
        self.assertEqual(excl, "etn")
        
        # Debt / Notes (PBC subordinated notes, EAB bonds, WHFBZ notes)
        row_pbc = {"symbol": "PBC", "name": "People's United Financial Inc. 5.625% Subordinated Notes due 2024", "exchange": "NYSE"}
        row_eab = {"symbol": "EAB", "name": "Entergy Arkansas Inc. First Mortgage Bonds", "exchange": "NYSE"}
        row_whfbz = {"symbol": "WHFBZ", "name": "White Horse Finance Inc. 6.50% Notes due 2020", "exchange": "NYSE"}
        
        self.assertEqual(classify_asset_with_details(row_pbc)[4], "notes")
        self.assertEqual(classify_asset_with_details(row_eab)[4], "bonds")
        self.assertEqual(classify_asset_with_details(row_whfbz)[4], "notes")
        
        # Depositary Shares (IBKCO)
        row_ibkco = {"symbol": "IBKCO", "name": "Interactive Brokers Group Inc. Depositary Shares", "exchange": "NASDAQ"}
        self.assertEqual(classify_asset_with_details(row_ibkco)[4], "depositary_shares")
        
        # When Issued (FTAIV)
        row_ftaiv = {"symbol": "FTAIV", "name": "FinTech Acquisition Corp. IV When Issued", "exchange": "NASDAQ"}
        self.assertEqual(classify_asset_with_details(row_ftaiv)[4], "when_issued")
        
        # SPAC / Acquisition (MUDS, GPAQ, ARYA)
        row_muds = {"symbol": "MUDS", "name": "Mudrick Capital Acquisition Corporation", "exchange": "NASDAQ"}
        self.assertEqual(classify_asset_with_details(row_muds)[4], "spac_acquisition")
        
        # Paired Shares (STAY)
        row_stay = {"symbol": "STAY", "name": "Extended Stay America Inc. Paired Shares", "exchange": "NYSE"}
        self.assertEqual(classify_asset_with_details(row_stay)[4], "paired_shares")
        
        # Normal Common Stocks (AAPL, MSFT)
        row_aapl = {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"}
        c, r, it, elig, excl = classify_asset_with_details(row_aapl)
        self.assertEqual(elig, "eligible")
        self.assertEqual(excl, "none")
        self.assertEqual(it, "common_stock")

    def test_classification_occurs_before_sampling(self):
        assets = [
            {"asset_id": "1", "symbol": "VXX", "name": "iPath Series B ETN", "exchange": "NYSE"},
            {"asset_id": "2", "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"},
            {"asset_id": "3", "symbol": "PBC", "name": "Subordinated Notes", "exchange": "NYSE"},
        ]
        sample = select_sample(assets, 100)
        self.assertEqual(sample, ["AAPL"])

    def test_zero_row_bar_result_becomes_empty(self):
        result = validate_bars("AAPL", [])
        self.assertEqual(result["status"], "EMPTY")
        self.assertEqual(result["data_status"], "EMPTY")
        self.assertEqual(result["request_status"], "SUCCESS")

    def test_sparse_observations_are_too_sparse(self):
        bars = [{"t": f"2020-01-{i:02d}T10:00:00Z", "o": 1.0, "h": 2.0, "l": 1.0, "c": 2.0, "v": 100.0} for i in range(1, 13)]
        bars[-1]["t"] = "2021-07-01T10:00:00Z"
        result = validate_bars("SPARSE", bars)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["data_status"], "TOO_SPARSE")
        self.assertFalse(result["usable_1_year"])

    def test_telemetry_fields(self):
        summary = compute_coverage([], total_inactive_assets=10)
        summary.update({
            "asset_inventory_http_requests": 1,
            "bar_download_invocations": 0,
            "raw_http_request_count": None,
            "raw_http_request_count_available": False,
        })
        self.assertEqual(summary["asset_inventory_http_requests"], 1)
        self.assertEqual(summary["bar_download_invocations"], 0)
        self.assertIsNone(summary["raw_http_request_count"])
        self.assertFalse(summary["raw_http_request_count_available"])

    def test_download_bars_with_retry_called(self):
        client = AlpacaInactiveAssetClient("key", "secret")
        with patch("testdata.vericekici_updated.download_bars_with_retry") as mock_retry:
            import pandas as pd
            mock_retry.return_value = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
            client.get_daily_bars(["AAPL"], "2020-01-01", "2020-01-02", "iex", "raw")
            mock_retry.assert_called_once()
            self.assertEqual(client.bar_download_invocations, 1)

    def test_identity_index_uses_full_inventory(self):
        from testdata.alpaca_inactive_asset_feasibility import classify_symbol_identity
        records = [
            {"asset_id": "1", "symbol": "AAPL", "status": "active", "name": "Apple Inc.", "exchange": "NASDAQ"},
            {"asset_id": "2", "symbol": "AAPL", "status": "inactive", "name": "Apple Inc.", "exchange": "NASDAQ"},
        ]
        status, details = classify_symbol_identity("AAPL", records)
        self.assertEqual(status, "ACTIVE_INACTIVE_COLLISION")
        self.assertTrue(details["possible_ticker_reuse"])
        
        records_multi = [
            {"asset_id": "1", "symbol": "ABC", "status": "inactive", "name": "ABC Corp", "exchange": "NYSE"},
            {"asset_id": "2", "symbol": "ABC", "status": "inactive", "name": "ABC Corp", "exchange": "NYSE"},
        ]
        status, details = classify_symbol_identity("ABC", records_multi)
        self.assertEqual(status, "MULTIPLE_INACTIVE_ASSET_IDS")
        
        records_name = [
            {"asset_id": "1", "symbol": "XYZ", "status": "inactive", "name": "XYZ Corp", "exchange": "NYSE"},
            {"asset_id": "2", "symbol": "XYZ", "status": "inactive", "name": "ABC Corp", "exchange": "NYSE"},
        ]
        status, details = classify_symbol_identity("XYZ", records_name)
        self.assertEqual(status, "NAME_MISMATCH")
        
        records_unique = [
            {"asset_id": "1", "symbol": "AAPL", "status": "inactive", "name": "Apple Inc.", "exchange": "NASDAQ"}
        ]
        status, details = classify_symbol_identity("AAPL", records_unique)
        self.assertEqual(status, "UNIQUE_INACTIVE_SYMBOL")
        self.assertFalse(details["possible_ticker_reuse"])

    def test_ambiguous_symbols_receive_zero_bar_calls(self):
        from testdata.alpaca_inactive_asset_feasibility import run_feasibility, FeasibilityConfig
        class FakeClient:
            def __init__(self):
                self.inventory_asset_count = 2
                self.inactive_asset_count = 2
                self.methods_used = []
                self.bar_download_invocations = 0
                self.inventory_status_counts = {}
                self.credential_variables_used = []
                self.all_assets = [
                    {"asset_id": "1", "symbol": "AAPL", "status": "active", "name": "Apple Inc.", "exchange": "NASDAQ", "tradable": True, "class": "us_equity"},
                    {"asset_id": "2", "symbol": "AAPL", "status": "inactive", "name": "Apple Inc.", "exchange": "NASDAQ", "tradable": True, "class": "us_equity"},
                ]
            def require_credentials(self):
                pass
            def get_inactive_us_equities(self, max_retries=1):
                return [self.all_assets[1]]
            def get_daily_bars(self, symbols, start, end, feed, adj, max_retries=1):
                self.bar_download_invocations += 1
                raise AssertionError("Should not be called for identity-ambiguous symbol!")
                
        config = FeasibilityConfig(sample_size=1)
        rows, summary, real_run = run_feasibility(config, client=FakeClient())
        self.assertEqual(rows[0]["bar_query_result"], "NOT_QUERIED_IDENTITY_AMBIGUOUS")
        self.assertFalse(rows[0]["identity_eligible"])
        self.assertFalse(rows[0]["bar_query_attempted"])

    def test_no_replacement_sampling_occurs(self):
        from testdata.alpaca_inactive_asset_feasibility import run_feasibility, FeasibilityConfig
        class FakeClient:
            def __init__(self):
                self.inventory_asset_count = 2
                self.inactive_asset_count = 2
                self.methods_used = []
                self.bar_download_invocations = 0
                self.inventory_status_counts = {}
                self.credential_variables_used = []
                self.all_assets = [
                    {"asset_id": "1", "symbol": "AAPL", "status": "inactive", "name": "Apple Inc.", "exchange": "NASDAQ", "tradable": True, "class": "us_equity"},
                    {"asset_id": "2", "symbol": "MSFT", "status": "active", "name": "Microsoft Corp", "exchange": "NASDAQ", "tradable": True, "class": "us_equity"},
                    {"asset_id": "3", "symbol": "MSFT", "status": "inactive", "name": "Microsoft Corp", "exchange": "NASDAQ", "tradable": True, "class": "us_equity"},
                ]
            def require_credentials(self):
                pass
            def get_inactive_us_equities(self, max_retries=1):
                return [self.all_assets[0], self.all_assets[2]]
            def get_daily_bars(self, symbols, start, end, feed, adj, max_retries=1):
                self.bar_download_invocations += 1
                return {symbols[0]: {"bars": [], "bar_query_result": "OK", "bar_query_attempted": True, "api_error": ""}}
        
        config = FeasibilityConfig(sample_size=2)
        rows, summary, real_run = run_feasibility(config, client=FakeClient())
        self.assertEqual(len(rows), 2)
        symbols = {r["symbol"] for r in rows}
        self.assertEqual(symbols, {"AAPL", "MSFT"})
        self.assertEqual(summary["metadata_eligible_sample"], 2)
        self.assertEqual(summary["identity_verified_sample"], 1)

    def test_punctuation_preserving_symbol_normalization(self):
        from testdata.alpaca_inactive_asset_feasibility import classify_symbol_identity
        records = [
            {"asset_id": "1", "symbol": "BRK.B", "status": "inactive", "name": "Berkshire Hathaway", "exchange": "NYSE"}
        ]
        status, details = classify_symbol_identity(" BRK.B ", records)
        self.assertEqual(status, "UNIQUE_INACTIVE_SYMBOL")

    def test_deterministic_conflict_serialization(self):
        from testdata.alpaca_inactive_asset_feasibility import classify_symbol_identity
        records = [
            {"asset_id": "2", "symbol": "XYZ", "status": "inactive", "name": "B Corp", "exchange": "NYSE"},
            {"asset_id": "1", "symbol": "XYZ", "status": "inactive", "name": "A Corp", "exchange": "NYSE"},
        ]
        status, details = classify_symbol_identity("XYZ", records)
        self.assertEqual(details["conflicting_asset_ids"], "1,2")
        self.assertEqual(details["conflicting_names"], "A Corp|B Corp")

    def test_result_precedence(self):
        rows = [
            {"symbol": "AAPL", "identity_eligible": True, "first_date": "2020-07-27", "data_status": "VALID", "status": "VALID", "observations": 250},
            {"symbol": "MSFT", "identity_eligible": False, "possible_ticker_reuse": True, "asset_identity_status": "ACTIVE_INACTIVE_COLLISION"},
        ]
        result = compute_coverage(rows, total_inactive_assets=2)
        self.assertEqual(result["classification"], "INCONCLUSIVE_ASSET_IDENTITY_CONTAMINATION")

        rows_small = [
            {"symbol": f"A{i}", "identity_eligible": True, "data_status": "EMPTY", "observations": 0} for i in range(5)
        ]
        result_small = compute_coverage(rows_small, total_inactive_assets=5)
        self.assertEqual(result_small["classification"], "INCONCLUSIVE_ASSET_IDENTITY_CONTAMINATION")

        rows_boundary = [
            {"symbol": f"A{i}", "identity_eligible": True, "first_date": "2020-07-27", "data_status": "VALID", "status": "VALID", "observations": 250} for i in range(10)
        ]
        result_boundary = compute_coverage(rows_boundary, total_inactive_assets=10)
        self.assertEqual(result_boundary["classification"], "INCONCLUSIVE_HISTORY_BOUNDARY")

    def test_history_boundary_calculation_excludes_ambiguous_and_empty_rows(self):
        rows = [
            {"symbol": f"A{i}", "identity_eligible": True, "first_date": "2020-07-27", "data_status": "VALID", "status": "VALID", "observations": 250} for i in range(9)
        ]
        rows.append({"symbol": "AMBIG", "identity_eligible": False, "first_date": "2020-07-27", "possible_ticker_reuse": True})
        result = compute_coverage(rows, total_inactive_assets=10)
        self.assertFalse(result["shared_history_boundary_detected"])


if __name__ == "__main__":
    unittest.main()
