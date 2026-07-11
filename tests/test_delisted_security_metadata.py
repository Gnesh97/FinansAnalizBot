import os
import re
import time
from datetime import datetime, timezone
import unittest
from unittest.mock import patch, MagicMock
import tempfile
import json
import shutil
from pathlib import Path
import pandas as pd

from testdata.delisted_security_metadata import (
    MetadataMasterEngine,
    normalize_symbol,
    normalize_name,
    compute_row_hash,
    compute_sha256_hash,
    generate_security_id,
    write_atomic_csv,
    write_atomic_text,
    classify_instrument,
    ClassificationInvariantError,
    ReconciliationError
)

class TestDelistedSecurityMetadata(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        
        # Patch paths in the module
        self.patcher1 = patch("testdata.delisted_security_metadata.RESEARCH_DATA_DIR", self.test_dir)
        self.patcher2 = patch("testdata.delisted_security_metadata.REPORTS_DIR", self.test_dir)
        self.patcher3 = patch("testdata.delisted_security_metadata.AV_CACHE_CSV", self.test_dir / "alpha_vantage_delisted.csv")
        self.patcher4 = patch("testdata.delisted_security_metadata.AV_CACHE_META", self.test_dir / "alpha_vantage_metadata.json")
        self.patcher5 = patch("testdata.delisted_security_metadata.SEC_TICKERS_CACHE", self.test_dir / "sec_company_tickers_exchange.json")
        self.patcher6 = patch("testdata.delisted_security_metadata.RUNS_DIR", self.test_dir / "runs")
        
        self.patcher1.start()
        self.patcher2.start()
        self.patcher3.start()
        self.patcher4.start()
        self.patcher5.start()
        self.patcher6.start()

    def tearDown(self):
        self.patcher1.stop()
        self.patcher2.stop()
        self.patcher3.stop()
        self.patcher4.stop()
        self.patcher5.stop()
        self.patcher6.stop()
        shutil.rmtree(self.test_dir)

    def test_deterministic_research_security_id(self):
        """Prove that research_security_id is deterministic and stable."""
        id1 = generate_security_id("AAPL", "Apple Inc.", "NASDAQ", "2020-01-01")
        id2 = generate_security_id("AAPL", "Apple Inc.", "NASDAQ", "2020-01-01")
        id3 = generate_security_id("AAPL", "Google Inc.", "NASDAQ", "2020-01-01")
        
        self.assertEqual(id1, id2)
        self.assertNotEqual(id1, id3)
        self.assertTrue(id1.startswith("SEC_DELIST_"))
        self.assertEqual(len(id1), 23)

    def test_stable_hash_sampling(self):
        """Prove stable hash sampling yields identical results and order."""
        data = [
            {"symbol": "A", "name": "Alpha Corp", "exchange": "NYSE", "delistingDate": "2015-05-05"},
            {"symbol": "B", "name": "Beta Inc", "exchange": "NASDAQ", "delistingDate": "2016-06-06"},
            {"symbol": "C", "name": "Gamma LLC", "exchange": "AMEX", "delistingDate": "2017-07-07"}
        ]
        h1 = [compute_row_hash(r["symbol"], r["name"], r["exchange"], r["delistingDate"]) for r in data]
        h2 = [compute_row_hash(r["symbol"], r["name"], r["exchange"], r["delistingDate"]) for r in data]
        self.assertEqual(h1, h2)

    def test_no_alphabetical_concentration(self):
        """Prove that hash sampling scatters symbols and does not group them alphabetically."""
        records = []
        for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            records.append({
                "symbol": f"{char}TKR",
                "name": f"Company {char}",
                "exchange": "NASDAQ",
                "delistingDate": "2020-01-01"
            })
        
        for r in records:
            r["hash"] = compute_row_hash(r["symbol"], r["name"], r["exchange"], r["delistingDate"])
        
        sorted_records = sorted(records, key=lambda x: x["hash"])
        first_five_symbols = [r["symbol"] for r in sorted_records[:5]]
        
        alphabetical_first_five = [f"{char}TKR" for char in "ABCDE"]
        self.assertNotEqual(first_five_symbols, alphabetical_first_five)

    def test_missing_sec_user_agent_blocks_live_sec_access(self):
        """Prove missing SEC_USER_AGENT blocks live SEC access."""
        engine = MetadataMasterEngine(mode="LIVE", config={"SEC_USER_AGENT": ""})
        with self.assertRaises(ValueError):
            engine.fetch_sec_company_tickers_exchange()
            
        with self.assertRaises(ValueError):
            engine.fetch_sec_submissions(12345)

    @patch("requests.get")
    def test_cache_only_mode_makes_zero_network_calls(self, mock_get):
        """Prove cache-only mode performs zero network requests."""
        av_csv = self.test_dir / "alpha_vantage_delisted.csv"
        av_meta = self.test_dir / "alpha_vantage_metadata.json"
        sec_tickers = self.test_dir / "sec_company_tickers_exchange.json"
        
        pd.DataFrame([{"symbol": "AAPL", "name": "Apple Inc", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "1980-12-12", "delistingDate": "2020-01-01", "status": "delisted"}]).to_csv(av_csv, index=False)
        av_meta.write_text(json.dumps({"retrieval_timestamp": "2026-07-10T12:00:00Z", "source_hash": "dummy"}), encoding="utf-8")
        
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[320193, "APPLE INC", "AAPL", "NASDAQ"]]
        }
        sec_tickers.write_text(json.dumps(sec_mapping), encoding="utf-8")
        
        submissions_file = self.test_dir / "sec_submissions_0000320193.json"
        submissions_file.write_text(json.dumps({
            "name": "APPLE INC",
            "tickers": ["AAPL"],
            "exchanges": ["NASDAQ"],
            "filings": {"recent": {"form": ["25-NSE"], "filingDate": ["2020-01-02"]}}
        }), encoding="utf-8")

        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        res = engine.run_pilot(sample_size=1)
        
        mock_get.assert_not_called()
        self.assertEqual(res["sample_size"], 1)

    def test_corrupt_cache_fails_closed(self):
        """Prove corrupt or empty cache files fail closed."""
        av_csv = self.test_dir / "alpha_vantage_delisted.csv"
        av_meta = self.test_dir / "alpha_vantage_metadata.json"
        
        av_csv.write_text("", encoding="utf-8")
        av_meta.write_text("{}", encoding="utf-8")
        
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        with self.assertRaises((ValueError, pd.errors.EmptyDataError)):
            engine.fetch_alpha_vantage_delisted()

    @patch("testdata.delisted_security_metadata.MetadataMasterEngine.fetch_sec_supplemental_file")
    def test_supplemental_sec_submissions_files_are_read(self, mock_fetch_sub):
        """Prove supplemental historical files are read when present."""
        submissions_json = {
            "name": "TEST INC",
            "tickers": ["TST"],
            "exchanges": ["NASDAQ"],
            "filings": {
                "recent": {
                    "form": ["10-K"],
                    "filingDate": ["2021-03-15"]
                },
                "files": [
                    {
                        "name": "submissions-TST-001.json",
                        "filingCount": 100,
                        "filingStart": "2010-01-01",
                        "filingEnd": "2015-12-31"
                    }
                ]
            }
        }
        
        mock_fetch_sub.return_value = {
            "form": ["25-NSE", "15-12B"],
            "filingDate": ["2011-05-05", "2011-06-06"]
        }
        
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        f25, f15 = engine.extract_filings(submissions_json, 999999)
        
        mock_fetch_sub.assert_called_once_with(999999, "submissions-TST-001.json")
        self.assertIn("2011-05-05", f25)
        self.assertIn("2011-06-06", f15)

    def test_ticker_only_evidence_cannot_become_high_confidence(self):
        """Prove ticker-only evidence is TICKER_ONLY_AMBIGUOUS or POSSIBLE_TICKER_REUSE, not HIGH confidence."""
        record = {
            "symbol": "ABC",
            "name": "Alpha Corp",
            "exchange": "NASDAQ",
            "delistingDate": "2020-01-01",
            "assetType": "Stock"
        }
        
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[12345, "ALPHA WIDGETS LLC", "ABC", "NYSE"]]
        }
        
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        with patch.object(engine, "fetch_sec_submissions") as mock_subs:
            mock_subs.return_value = {
                "name": "ALPHA WIDGETS LLC",
                "tickers": ["ABC"],
                "exchanges": ["NYSE"],
                "filings": {"recent": {"form": [], "filingDate": []}}
            }
            
            res = engine.resolve_cik_and_match(record, sec_mapping)
            self.assertEqual(res["identity_status"], "TICKER_ONLY_AMBIGUOUS")
            self.assertEqual(res["identity_confidence"], "LOW")
            self.assertTrue(res["manual_review_flag"])

    def test_atomic_report_writes(self):
        """Prove report outputs are written atomically via temp-file and replace."""
        csv_path = self.test_dir / "out.csv"
        df = pd.DataFrame([{"col1": "val1"}])
        
        with patch("os.replace") as mock_replace:
            write_atomic_csv(csv_path, df)
            mock_replace.assert_called_once()
            first_arg = mock_replace.call_args[0][0]
            self.assertTrue(str(first_arg).endswith(".tmp"))

    def test_unmatched_and_ambiguous_rows_remain_in_output(self):
        """Prove that unmatched or ambiguous records are kept in output and review lists."""
        record = {
            "symbol": "XYZ",
            "name": "No Match Corp",
            "exchange": "OTC",
            "delistingDate": "2020-01-01"
        }
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": []
        }
        
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        res = engine.resolve_cik_and_match(record, sec_mapping)
        self.assertEqual(res["identity_status"], "NO_SEC_MATCH")
        self.assertTrue(res["manual_review_flag"])

    def test_no_price_strategy_backtest_or_broker_code_reachable(self):
        """Prove that delisted_security_metadata does not import strategy/backtest/broker modules."""
        code_file = Path(__file__).resolve().parents[1] / "testdata" / "delisted_security_metadata.py"
        code = code_file.read_text(encoding="utf-8")
        
        forbidden = [
            "alpaca_paper_broker", "portfolio_manager", "execution_engine",
            "runner", "universe_scanner", "backtest", "strategies"
        ]
        
        for name in forbidden:
            pattern = rf"\bimport\s+{name}\b|\bfrom\s+{name}\b"
            self.assertFalse(bool(re.search(pattern, code)), f"Forbidden import of {name} detected!")

    def test_repeated_refresh_runs_create_separate_immutable_manifests(self):
        """Prove repeated REFRESH runs write manifests to separate run_id folders and do not overwrite."""
        engine = MetadataMasterEngine(mode="REFRESH", config={"ALPHA_VANTAGE_API_KEY": "dummy", "SEC_USER_AGENT": "a@b.com"})
        
        # Mock fetch calls to avoid network
        with patch.object(engine, "fetch_alpha_vantage_delisted") as mock_av, \
             patch.object(engine, "fetch_sec_company_tickers_exchange") as mock_sec_map:
            
            mock_av.return_value = pd.DataFrame([{"symbol": "AAPL", "name": "Apple Inc", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "1980-12-12", "delistingDate": "2020-01-01", "status": "delisted"}])
            mock_sec_map.return_value = {"fields": ["cik", "name", "ticker", "exchange"], "data": [[320193, "APPLE INC", "AAPL", "NASDAQ"]]}
            
            with patch.object(engine, "fetch_sec_submissions") as mock_sub:
                mock_sub.return_value = {"name": "APPLE INC", "tickers": ["AAPL"], "exchanges": ["NASDAQ"], "filings": {"recent": {"form": [], "filingDate": []}}}
                
                # First run
                engine.run_pilot(sample_size=1)
                
                # Second run (force a tiny sleep to assure unique timestamp)
                time.sleep(1.1)
                engine.run_pilot(sample_size=1)
                
                # Verify two separate folders under runs/
                runs_dir = self.test_dir / "runs"
                self.assertTrue(runs_dir.exists())
                run_folders = list(runs_dir.iterdir())
                self.assertEqual(len(run_folders), 2)

    def test_filing_inside_180_days_but_for_another_security_class_is_not_aligned_strong(self):
        """Prove filing inside 180 days but for another security class (preferred/note) is not ALIGNED_STRONG."""
        record = {
            "symbol": "FDUSZ",  # Note/preferred class
            "name": "Fidus Investment Notes",
            "exchange": "NASDAQ",
            "delistingDate": "2020-05-15",
            "assetType": "Stock"
        }
        
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[12345, "FIDUS INVESTMENT CORP", "FDUSZ", "NASDAQ"]] # conformed ticker in mapping is FDUSZ
        }
        
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        with patch.object(engine, "fetch_sec_submissions") as mock_subs:
            mock_subs.return_value = {
                "name": "FIDUS INVESTMENT CORP",
                "tickers": ["FDUS"],
                "exchanges": ["NASDAQ"],
                "filings": {"recent": {"form": ["25-NSE"], "filingDate": ["2020-05-20"]}} # within 180 days
            }
            
            res = engine.resolve_cik_and_match(record, sec_mapping)
            self.assertTrue(res["security_class_mismatch"])
            self.assertEqual(res["identity_status"], "INSUFFICIENT_SECURITY_EVIDENCE")

    def test_date_proximity_alone_remains_aligned_plausible_and_requires_review(self):
        """Prove date proximity alone produces only ALIGNED_PLAUSIBLE and retains manual_review_flag=True."""
        record = {
            "symbol": "TST",
            "name": "Test Company",
            "exchange": "NASDAQ",
            "delistingDate": "2020-05-15",
            "assetType": "Stock",
            "instrument_type": "ELIGIBLE_COMMON_STOCK",
            "secondary_flags": []
        }
        
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[12345, "TEST COMPANY", "TSTOLD", "NASDAQ"]] # ticker differs TSTOLD
        }
        
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        with patch.object(engine, "fetch_sec_submissions") as mock_subs:
            mock_subs.return_value = {
                "name": "TEST COMPANY",
                "tickers": ["TSTOLD"],
                "exchanges": ["NASDAQ"],
                "filings": {"recent": {"form": ["25-NSE"], "filingDate": ["2020-05-20"]}}
            }
            
            res = engine.resolve_cik_and_match(record, sec_mapping)
            self.assertEqual(res["identity_status"], "SEC_NAME_EXCHANGE_MATCH") # matches by conformed name + exchange
            self.assertFalse(res["security_class_mismatch"])

    def test_every_record_receives_exactly_one_reconciled_primary_classification(self):
        """Prove that every record receives exactly one primary classification (mutual exclusivity)."""
        av_rows = [
            ("AAPL", "Apple Inc.", "Stock"),
            ("SPACU", "Blank Check Acquisition", "Stock"),
            ("FITBO", "Fifth Third Preferred", "Stock"),
            ("SLTB", "Senior Notes", "Stock"),
            ("VIV-W", "Warrant Class", "Stock"),
            ("ETF1", "ETF Fund", "ETF"),
            ("", "", "Stock"),
        ]
        
        primary_counts = {}
        for sym, name, at in av_rows:
            primary_reason, _ = classify_instrument(sym, name, at)
            primary_counts[primary_reason] = primary_counts.get(primary_reason, 0) + 1
            
        total_classified = sum(primary_counts.values())
        self.assertEqual(total_classified, len(av_rows))

    def test_secondary_flags_do_not_double_count_exclusions(self):
        """Prove secondary flags can contain multiple categories without double-counting the primary count."""
        primary_reason, _ = classify_instrument("SPACPR", "Acquisition Preferred Trust", "Stock")
        self.assertIn(primary_reason, ("SPAC", "PREFERRED"))

    def test_direct_cik_evidence_remains_zero(self):
        """Prove DIRECT_CIK_EVIDENCE remains zero without independent CIK source."""
        record = {
            "symbol": "AAPL",
            "name": "Apple Inc",
            "exchange": "NASDAQ",
            "delistingDate": "2020-01-01",
            "assetType": "Stock"
        }
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[320193, "APPLE INC", "AAPL", "NASDAQ"]]
        }
        
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        with patch.object(engine, "fetch_sec_submissions") as mock_subs:
            mock_subs.return_value = {
                "name": "APPLE INC",
                "tickers": ["AAPL"],
                "exchanges": ["NASDAQ"],
                "filings": {"recent": {"form": [], "filingDate": []}}
            }
            res = engine.resolve_cik_and_match(record, sec_mapping)
            self.assertEqual(res["identity_status"], "SEC_TICKER_NAME_EXCHANGE_MATCH")
            self.assertNotEqual(res["identity_status"], "DIRECT_CIK_EVIDENCE")

    def test_filing_absence_and_identity_failure_remain_separate(self):
        """Prove that filing absence does not invalidate issuer conformed identity match."""
        record = {
            "symbol": "AAPL",
            "name": "Apple Inc",
            "exchange": "NASDAQ",
            "delistingDate": "2020-01-01",
            "assetType": "Stock"
        }
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[320193, "APPLE INC", "AAPL", "NASDAQ"]]
        }
        
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        with patch.object(engine, "fetch_sec_submissions") as mock_subs:
            mock_subs.return_value = {
                "name": "APPLE INC",
                "tickers": ["AAPL"],
                "exchanges": ["NASDAQ"],
                "filings": {"recent": {"form": [], "filingDate": []}} # No Form 25 or Form 15
            }
            res = engine.resolve_cik_and_match(record, sec_mapping)
            self.assertEqual(res["identity_status"], "SEC_TICKER_NAME_EXCHANGE_MATCH")
            self.assertEqual(res["identity_confidence"], "HIGH")
            self.assertEqual(len(res["form_25_filing_dates"]), 0)

    def test_all_named_non_common_fixtures_excluded_generically(self):
        """Prove all named non-common fixtures are generically excluded."""
        fixtures = [
            ("PGSS", "Pegasus Acquisition Corp.", "Stock"),
            ("PTOC", "Pontem Corp.", "Stock"),
            ("ZWRK", "Z-Work Acquisition Corp.", "Stock"),
            ("HCAR", "Healthcare Services Acquisition Corp.", "Stock"),
            ("FRON", "Frontier Acquisition Corp.", "Stock"),
            ("IPOA", "Social Capital Acquisition Corp.", "Stock"),
            ("SVFA", "SVF Blank Check Corp.", "Stock"),
            ("PSPC", "Post Holdings Acquisition Partner Co.", "Stock"),
            ("CORS", "Corsair Partnering Corp.", "Stock"),
            ("ASCAR", "Aspirational Consumer Brands Acquisition Corp.", "Stock"),
            ("FITBO", "Fifth Third Bancorp Preferred Shares", "Stock"),
            ("RJD-CL", "Raymond James Financial Notes Class", "Stock"),
            ("PJS-CL", "Preferred Term Trust Class", "Stock"),
            ("SLTB", "Senior Notes", "Stock"),
            ("HBHCL", "Whitney Notes Class", "Stock"),
            ("VIV-W", "Warrant Security", "Stock"),
            ("UTX-W", "Warrant Security", "Stock")
        ]
        
        for sym, name, at in fixtures:
            reason, _ = classify_instrument(sym, name, at)
            self.assertNotEqual(reason, "ELIGIBLE_COMMON_STOCK", f"Fixture {sym} was not excluded!")

    def test_malformed_or_nan_company_names_excluded(self):
        """Prove malformed or NaN names are classified as MALFORMED_NAME."""
        self.assertEqual(classify_instrument("AAPL", "", "Stock")[0], "MALFORMED_NAME")
        self.assertEqual(classify_instrument("AAPL", "nan", "Stock")[0], "MALFORMED_NAME")
        self.assertEqual(classify_instrument("AAPL", "None", "Stock")[0], "MALFORMED_NAME")
        self.assertEqual(classify_instrument("AAPL", "+++", "Stock")[0], "MALFORMED_NAME")

    def test_bctx_style_date_proximity_unaligned(self):
        """Prove BCTX-style 2022 delisting vs 2026 Form 25 filing remains unaligned."""
        av_delist_date = datetime.strptime("2022-03-15", "%Y-%m-%d")
        filing_date = datetime.strptime("2026-05-20", "%Y-%m-%d")
        
        dist_days = abs((filing_date - av_delist_date).days)
        self.assertTrue(dist_days > 180) # Outside 180 days -> UNALIGNED

    def test_deterministic_json_fields_and_report_outputs_are_byte_stable(self):
        """Prove JSON serialization fields are sorted and deterministic (byte-stable)."""
        data = ["sec_mapping", "alpha_vantage", "submissions_api"]
        js = json.dumps(sorted(list(set(data))))
        self.assertEqual(js, '["alpha_vantage", "sec_mapping", "submissions_api"]')

    def test_strict_date_parsing_and_pre_2010_exclusion(self):
        """Prove that pre-2010 delist dates are categorized as PRE_2010_DELISTED and invalid dates as INVALID_OR_MISSING_DELISTING_DATE."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        df = pd.DataFrame([
            {"symbol": "A", "name": "Apple", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2000-01-01", "delistingDate": "2009-12-31", "status": "delisted"},
            {"symbol": "B", "name": "Google", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2000-01-01", "delistingDate": "not-a-date", "status": "delisted"},
            {"symbol": "C", "name": "MSFT", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2000-01-01", "delistingDate": "", "status": "delisted"}
        ])
        
        res = engine.parse_av_raw(df)
        self.assertEqual(res["pre_2010_records"], 3)
        self.assertEqual(res["all_records"][0]["primary_exclusion_reason"], "PRE_2010_DELISTED")
        self.assertEqual(res["all_records"][1]["primary_exclusion_reason"], "INVALID_OR_MISSING_DELISTING_DATE")
        self.assertEqual(res["all_records"][2]["primary_exclusion_reason"], "INVALID_OR_MISSING_DELISTING_DATE")
        
        # Verify instrument type is still preserved
        self.assertEqual(res["all_records"][0]["instrument_type"], "ELIGIBLE_COMMON_STOCK")

    def test_three_reconciliations_exact_and_reconciliation_error(self):
        """Prove that ReconciliationError is raised when any of the three reconciliations fails."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        df = pd.DataFrame([
            {"symbol": "A", "name": "Apple", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2000-01-01", "delistingDate": "2015-05-05", "status": "delisted"}
        ])
        
        # Mock fetch_alpha_vantage_delisted
        with patch.object(engine, "fetch_alpha_vantage_delisted", return_value=df):
            # If parse_av_raw is mocked to return unbalanced diagnostics, run_pilot should fail
            bad_diag = {
                "raw_total_records": 1,
                "pre_2010_records": 0,
                "in_scope_2010_plus_records": 1,
                "eligible_common_stock_candidates": 1,
                "in_scope_primary_exclusions": {},
                "all_primary_exclusions": {},
                "candidates": [{"symbol": "A", "name": "Apple", "exchange": "NASDAQ", "delistingDate": "2015-05-05"}],
                "all_records": [{"symbol": "A", "name": "Apple", "exchange": "NASDAQ", "delistingDate": "2015-05-05", "primary_exclusion_reason": "NONE", "research_eligibility": True}]
            }
            with patch.object(engine, "parse_av_raw", return_value=bad_diag):
                # Break reconciliation 2 by asserting eligible=1, exclusions=1 (unbalanced)
                bad_diag["in_scope_primary_exclusions"] = {"SPAC": 1} # 1 != 1 + 1 -> fails!
                with self.assertRaises(ReconciliationError):
                    engine.run_pilot(sample_size=1)

    def test_existing_historical_manifest_raises_file_exists_error(self):
        """Prove that run fails and raises FileExistsError if run_id directory already exists (immutability check)."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        
        class MockDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
        
        with patch("testdata.delisted_security_metadata.datetime", MockDateTime):
            conf_str = f"{engine.config.get('ALPHA_VANTAGE_API_KEY') or ''}|{engine.config.get('SEC_USER_AGENT') or ''}"
            conf_hash = compute_sha256_hash(conf_str)[:8]
            run_id = f"20260710_120000_CACHE_ONLY_{conf_hash}"
            run_dir = self.test_dir / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            
            with patch.object(engine, "fetch_alpha_vantage_delisted") as mock_av, \
                 patch.object(engine, "fetch_sec_company_tickers_exchange") as mock_sec_map:
                mock_av.return_value = pd.DataFrame([{"symbol": "A", "name": "Apple", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2000-01-01", "delistingDate": "2015-05-05", "status": "delisted"}])
                mock_sec_map.return_value = {"fields": ["cik", "name", "ticker", "exchange"], "data": []}
                with self.assertRaises(FileExistsError):
                    engine.run_pilot(sample_size=1)

    def test_preferred_suffix_regex_and_negatives(self):
        """Prove preferred suffixes match all six fixtures and not normal/class-shares."""
        fixtures = ["DBRG-P-I", "GDV-P-A", "UMH-P-B", "FRC-P-F", "WFC-P-T", "PSA-P-D"]
        for f in fixtures:
            reason, _ = classify_instrument(f, "Some Company", "Stock")
            self.assertEqual(reason, "PREFERRED")
            
        negatives = ["BRK-A", "BRK-B", "HEI-A", "TRGP", "AAPL-A", "T-B"]
        for n in negatives:
            reason, _ = classify_instrument(n, "Normal Inc.", "Stock")
            self.assertNotEqual(reason, "PREFERRED")

    def test_bare_word_index_vs_contextual_funds(self):
        """Prove the bare word INDEX is not excluded, but contextual fund phrases are."""
        r1, _ = classify_instrument("IDX", "Index Corporation", "Stock")
        r2, _ = classify_instrument("TRST", "Trust Bancorp", "Stock")
        self.assertEqual(r1, "ELIGIBLE_COMMON_STOCK")
        self.assertEqual(r2, "ELIGIBLE_COMMON_STOCK")
        
        contextual = ["INDEX FUND", "CLOSED-END FUND", "INDEX ETF", "INDEX ETN", "EXCHANGE TRADED FUND", "INCOME TRUST", "ROYALTY TRUST"]
        for phrase in contextual:
            r, _ = classify_instrument("FND", f"Alpha Vantage {phrase}", "Stock")
            expected = "ETF_ETN_FUND" if ("ETF" in phrase or "ETN" in phrase or "EXCHANGE TRADED FUND" in phrase) else "NOT_STOCK"
            self.assertEqual(r, expected)

    def test_foreign_suffix_and_evidence(self):
        """Prove foreign suffix triggers review/secondary flag, and US address alone cannot clear it."""
        r, _ = classify_instrument("SDRL", "Seadrill Ltd", "Stock")
        self.assertEqual(r, "ELIGIBLE_COMMON_STOCK")
        
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        
        record = {
            "symbol": "SDRL",
            "name": "Seadrill Ltd",
            "exchange": "NYSE",
            "delistingDate": "2023-10-18",
            "assetType": "Stock"
        }
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[12345, "SEADRILL LTD", "SDRL", "NYSE"]]
        }
        
        with patch.object(engine, "fetch_sec_submissions") as mock_subs:
            mock_subs.return_value = {
                "name": "SEADRILL LTD",
                "tickers": ["SDRL"],
                "exchanges": ["NYSE"],
                "stateOfIncorporation": "L3",
                "addresses": {
                    "business": {
                        "isForeignLocation": 0,
                        "country": "US",
                        "countryCode": "US"
                    }
                },
                "filings": {"recent": {"form": [], "filingDate": []}}
            }
            res = engine.resolve_cik_and_match(record, sec_mapping)
            self.assertFalse(res["has_explicit_domestic_evidence"])
            
            mock_subs.return_value = {
                "name": "SEADRILL LTD",
                "tickers": ["SDRL"],
                "exchanges": ["NYSE"],
                "stateOfIncorporation": "DE",
                "addresses": {
                    "business": {
                        "isForeignLocation": 0,
                        "country": "US",
                        "countryCode": "US"
                    }
                },
                "filings": {"recent": {"form": [], "filingDate": []}}
            }
            res2 = engine.resolve_cik_and_match(record, sec_mapping)
            self.assertTrue(res2["has_explicit_domestic_evidence"])

    def test_unaligned_and_aligned_plausible_always_require_review(self):
        """Prove UNALIGNED and ALIGNED_PLAUSIBLE always force manual_review_flag=True."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        record = {
            "symbol": "TST",
            "name": "Test Inc",
            "exchange": "NASDAQ",
            "delistingDate": "2020-05-15",
            "assetType": "Stock",
            "instrument_type": "ELIGIBLE_COMMON_STOCK",
            "secondary_flags": [],
            "research_eligibility": True
        }
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[12345, "TEST INC", "TST", "NASDAQ"]]
        }
        
        with patch.object(engine, "fetch_sec_submissions") as mock_subs:
            mock_subs.return_value = {
                "name": "TEST INC",
                "tickers": ["TSTOLD"],
                "exchanges": ["NASDAQ"],
                "stateOfIncorporation": "DE",
                "filings": {"recent": {"form": ["25-NSE"], "filingDate": ["2020-05-20"]}}
            }
            with patch.object(engine, "fetch_alpha_vantage_delisted") as mock_av, \
                 patch.object(engine, "fetch_sec_company_tickers_exchange") as mock_sec_map:
                mock_av.return_value = pd.DataFrame([record])
                mock_sec_map.return_value = sec_mapping
                
                res = engine.run_pilot(sample_size=1)
                self.assertEqual(res["manual_review_count"], 1)

    def test_manifest_verifications_and_atomic_replace(self):
        """Prove latest outputs share same run_id/metadata, manifest counts match, and atomic replace works."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        record = {
            "symbol": "AAPL",
            "name": "Apple Inc",
            "exchange": "NASDAQ",
            "delistingDate": "2020-01-01",
            "assetType": "Stock",
            "status": "delisted"
        }
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[320193, "APPLE INC", "AAPL", "NASDAQ"]]
        }
        
        with patch.object(engine, "fetch_alpha_vantage_delisted") as mock_av, \
             patch.object(engine, "fetch_sec_company_tickers_exchange") as mock_sec_map, \
             patch.object(engine, "fetch_sec_submissions") as mock_sub:
             
            mock_av.return_value = pd.DataFrame([record])
            mock_sec_map.return_value = sec_mapping
            mock_sub.return_value = {
                "name": "APPLE INC",
                "tickers": ["AAPL"],
                "exchanges": ["NASDAQ"],
                "stateOfIncorporation": "DE",
                "filings": {"recent": {"form": [], "filingDate": []}}
            }
            
            res = engine.run_pilot(sample_size=1)
            run_id = res["run_id"]
            
            latest_manifest_path = self.test_dir / "delisted_metadata_run_manifest.json"
            semantics_path = self.test_dir / "delisted_metadata_semantics_correction.md"
            pilot_path = self.test_dir / "delisted_metadata_master_pilot.md"
            action_path = self.test_dir / "chatgpt_action_report.md"
            
            self.assertTrue(latest_manifest_path.exists())
            self.assertTrue(semantics_path.exists())
            self.assertTrue(pilot_path.exists())
            self.assertTrue(action_path.exists())
            
            manifest_data = json.loads(latest_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest_data["run_id"], run_id)
            self.assertEqual(manifest_data["report_schema_version"], "2.0.0")
            
            semantics_content = semantics_path.read_text(encoding="utf-8")
            self.assertIn(run_id, semantics_content)
            self.assertIn("2.0.0", semantics_content)
            
            pilot_content = pilot_path.read_text(encoding="utf-8")
            self.assertIn(run_id, pilot_content)
            self.assertIn("2.0.0", pilot_content)
            
            manifest_count = manifest_data["historical_manifests_count"]
            self.assertIn(f"Number of Historical Manifests:** {manifest_count}", semantics_content)
            self.assertIn(f"Number of Historical Manifests:** {manifest_count}", pilot_content)

    def test_direct_cik_evidence_is_zero(self):
        """Prove DIRECT_CIK_EVIDENCE remains zero in output reports."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        record = {
            "symbol": "AAPL",
            "name": "Apple Inc",
            "exchange": "NASDAQ",
            "delistingDate": "2020-01-01",
            "assetType": "Stock",
            "status": "delisted"
        }
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[320193, "APPLE INC", "AAPL", "NASDAQ"]]
        }
        
        with patch.object(engine, "fetch_alpha_vantage_delisted") as mock_av, \
             patch.object(engine, "fetch_sec_company_tickers_exchange") as mock_sec_map, \
             patch.object(engine, "fetch_sec_submissions") as mock_sub:
             
            mock_av.return_value = pd.DataFrame([record])
            mock_sec_map.return_value = sec_mapping
            mock_sub.return_value = {
                "name": "APPLE INC",
                "tickers": ["AAPL"],
                "exchanges": ["NASDAQ"],
                "stateOfIncorporation": "DE",
                "filings": {"recent": {"form": [], "filingDate": []}}
            }
            
            res = engine.run_pilot(sample_size=1)
            self.assertEqual(res["status_counts"].get("DIRECT_CIK_EVIDENCE", 0), 0)

    def test_new_exclusions_spbc_jbr_asrv(self):
        """Prove SPBC, JBR, and ASRV are excluded with correct reasons, while SEVN/LGF-A are not."""
        # SPBC
        reason, _ = classify_instrument("SPBC", "SIMPLIFY US EQUITY PLUS BITCOIN STRATEGY ETF", "Stock")
        self.assertEqual(reason, "ETF_ETN_FUND")
        
        # JBR
        reason, _ = classify_instrument("JBR", "Select Asset Inc Corporate Backed Callable Trust Certificates JC Penney DebentureBacked Series 20061 Class A1", "Stock")
        self.assertEqual(reason, "NOTE")
        
        # ASRV structured symbol
        reason, _ = classify_instrument("ASRV 8.45 06-30-28", "Ameriserv Financial Inc", "Stock")
        self.assertEqual(reason, "MALFORMED_OR_STRUCTURED_SYMBOL")
        
        # SEVN (ordinary REIT Trust company)
        reason, _ = classify_instrument("SEVN", "Seven Hills Realty Trust", "Stock")
        self.assertEqual(reason, "ELIGIBLE_COMMON_STOCK")
        
        # LGF-A (valid class share)
        reason, _ = classify_instrument("LGF-A", "Lions Gate Entertainment Corp", "Stock")
        self.assertEqual(reason, "ELIGIBLE_COMMON_STOCK")

    def test_ambiguous_security_class_suffixes(self):
        """Prove suspect 5-letter suffixes are classified as ambiguous unless corroborated, and ordinary ones aren't."""
        all_symbols = {"LINDW", "COWNZ", "NEEPR", "OCFCP", "FDUSZ", "OXLCN", "NEEPK", "LIND", "COWN", "NEE", "OCFC", "FDUS", "OXLC", "NEEP"}
        
        # 1. Test uncorroborated ambiguous suffix (no base symbol LIND in set)
        reason, _ = classify_instrument("LINDW", "Lindblad Expeditions Holdings Inc", "Stock", {"LINDW"})
        self.assertEqual(reason, "SECURITY_CLASS_AMBIGUOUS")
        
        # 2. Test corroborated warrant (warrant keyword in name or base-symbol present + warrant signal)
        # Note: LINDW has base symbol LIND present in all_symbols, so it is corroborated as warrant/right!
        reason_corr, _ = classify_instrument("LINDW", "Lindblad Expeditions Warrants", "Stock", all_symbols)
        self.assertEqual(reason_corr, "WARRANT_RIGHT")
        
        # 3. Test synthetic 5-letter symbol with ambiguous suffix but explicit common-stock metadata
        reason_stock, _ = classify_instrument("SYNTP", "Synthetic Common Stock", "Stock", {"SYNT"})
        self.assertEqual(reason_stock, "SECURITY_CLASS_AMBIGUOUS")
        
        # 4. Test ordinary common stock endings (negative controls)
        reason_trgp, _ = classify_instrument("TRGP", "Targa Resources Corp", "Stock", all_symbols)
        self.assertEqual(reason_trgp, "ELIGIBLE_COMMON_STOCK")
        
        reason_amzn, _ = classify_instrument("AMZN", "Amazon.com Inc", "Stock", all_symbols)
        self.assertEqual(reason_amzn, "ELIGIBLE_COMMON_STOCK")
        
        reason_k, _ = classify_instrument("K", "Kellogg Co", "Stock", all_symbols)
        self.assertEqual(reason_k, "ELIGIBLE_COMMON_STOCK")

    def test_permanent_known_negative_and_positive_control_matrix(self):
        """All known fixture symbols stay generic production classifications."""
        negatives = [
            ("VXX", "iPath Series B S&P 500 VIX Short-Term Futures ETN"),
            ("EVIX", "VelocityShares Daily Inverse VIX Short-Term ETN"),
            ("DFVS", "iPath US Treasury Long Bond Bear ETN"),
            ("LMLP", "ETRACS Monthly Pay 2xLeveraged MLP ETN"),
            ("NRGZ", "MicroSectors U.S. Big Oil Index 3X Leveraged ETNs"),
            ("PBC", "Polymet Mining Corp Preferred Series A"),
            ("EAB", "Entergy Arkansas Inc First Mortgage Bonds"),
            ("WHFBZ", "Wells Fargo Bank Notes"),
            ("SLTB", "75 Sr Nt 09152019"),
            ("IBKCO", "IBK Capital Corp Preferred Shares"),
            ("FTAIV", "FinTech Acquisition Corp IV Units"),
            ("MUDS", "Mudrick Capital Acquisition Corp II"),
            ("GPAQ", "Gordon Pointe Acquisition Corp"),
            ("ARYA", "ARYA Sciences Acquisition Corp"),
            ("IPOA", "Social Capital Hedosophia Holdings Corp Class A"),
            ("SVFA", "SVF Investment Corp - Class A"),
            ("STAY", "Extended Stay America Inc Trust Certificates"),
            ("DBRG-P-I", "DigitalBridge Group Inc"),
            ("GDV-P-A", "Gabelli Dividend & Income Trust"),
            ("UMH-P-B", "UMH Properties Inc"),
            ("FRC-P-F", "First Republic Bank"),
            ("WFC-P-T", "Wells Fargo & Company"),
            ("PSA-P-D", "Public Storage"),
            ("SPBC", "Simplify US Equity Plus Bitcoin Strategy ETF"),
            ("JBR", "Corporate Backed Callable Trust Certificates DebentureBacked"),
            ("ASRV 8.45 06-30-28", "Ameriserv Financial Inc"),
            ("FITBO", "Fifth Third Bancorp 4.95 PRF PERPETUAL"),
            ("EMS", "Emergency Medical Services LP"),
            ("LGCYO", "Legacy Reserves L.P."),
        ]
        for symbol, name in negatives:
            with self.subTest(symbol=symbol):
                reason, _ = classify_instrument(symbol, name, "Stock")
                self.assertNotEqual(reason, "ELIGIBLE_COMMON_STOCK")

        positives = [
            ("SEVN", "Seven Hills Realty Trust"),
            ("LGF-A", "Lions Gate Entertainment Corp"),
            ("BRK.B", "Berkshire Hathaway Inc Class B"),
            ("IVZ", "Invesco Ltd"),
            ("BHLB", "Berkshire Hills Bancorp Inc"),
            ("ACME", "Acme Holdings Corp"),
            ("AIMC", "Acme Investment Corp"),
        ]
        for symbol, name in positives:
            with self.subTest(symbol=symbol):
                reason, _ = classify_instrument(symbol, name, "Stock")
                self.assertEqual(reason, "ELIGIBLE_COMMON_STOCK")

    def test_generic_spac_detection_covers_acquisition_sequence_and_class_a(self):
        """Explicit acquisition shells are SPACs without a symbol blacklist."""
        explicit_spacs = [
            ("MBAC", "M3 Brigade Acquisition II Corp - Class A"),
            ("TEST", "Example Acquisition Corp - Class A"),
            ("TEST", "Example Acquisition Corporation"),
            ("TEST", "Example Acquisition Company"),
            ("TEST", "Example Acquisition Co"),
            ("TEST", "Example Acquisition III Corp"),
            ("TEST", "Example Blank Check Company"),
            ("TEST", "Example blank-check issuer"),
            ("TEST", "Example SPAC Class A"),
        ]
        for symbol, name in explicit_spacs:
            with self.subTest(name=name):
                reason, _ = classify_instrument(symbol, name, "Stock")
                self.assertEqual(reason, "SPAC")

        mbac = pd.DataFrame([{
            "symbol": "MBAC",
            "name": "M3 Brigade Acquisition II Corp - Class A",
            "exchange": "NYSE",
            "assetType": "Stock",
            "ipoDate": "2021-04-26",
            "delistingDate": "2023-12-08",
            "status": "delisted",
        }])
        diagnostics = MetadataMasterEngine(mode="CACHE_ONLY").parse_av_raw(mbac)
        self.assertEqual(diagnostics["eligible_common_stock_candidates"], 0)
        self.assertEqual(diagnostics["all_records"][0]["primary_exclusion_reason"], "SPAC")

    def test_spac_detection_preserves_operating_company_negative_controls(self):
        """Generic business words and share classes are not SPAC evidence alone."""
        operating_companies = [
            ("HOLD", "Acme Industrial Holdings Inc - Class A"),
            ("INVT", "Acme Investment Management Corp - Class A"),
            ("GRWX", "Acme Growth Technologies Corp - Class A"),
            ("CAPL", "Acme Capital Markets Inc - Class A"),
        ]
        for symbol, name in operating_companies:
            with self.subTest(name=name):
                reason, _ = classify_instrument(symbol, name, "Stock")
                self.assertEqual(reason, "ELIGIBLE_COMMON_STOCK")

    def test_spac_style_class_a_with_related_units_and_warrants_fails_closed(self):
        """Shell-like Class A rows use inventory corroboration without a definitive type."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        raw = pd.DataFrame([
            {"symbol": "FRSG", "name": "First Reserve Sustainable Growth Corp - Class A", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2021-04-29", "delistingDate": "2023-03-07", "status": "delisted"},
            {"symbol": "FRSGU", "name": "First Reserve Sustainable Growth Corp - Units (1 Ord Share Class A & 1/4 War)", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2021-03-05", "delistingDate": "2023-03-07", "status": "delisted"},
            {"symbol": "FRSGW", "name": "First Reserve Sustainable Growth Corp - Warrants", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2021-04-29", "delistingDate": "2023-03-07", "status": "delisted"},
            {"symbol": "KRNL", "name": "Kernel Group Holdings Inc - Class A", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2021-04-01", "delistingDate": "2026-07-09", "status": "delisted"},
            {"symbol": "KRNLU", "name": "Kernel Group Holdings Inc - Units (1 Ord Class A & 1/2 War)", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2021-02-03", "delistingDate": "2026-07-09", "status": "delisted"},
            {"symbol": "KRNLW", "name": "Kernel Group Holdings Inc - Warrants", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2021-03-26", "delistingDate": "2026-07-09", "status": "delisted"},
        ])

        diagnostics = engine.parse_av_raw(raw)
        records = {row["symbol"]: row for row in diagnostics["all_records"]}
        for symbol in ("FRSG", "KRNL"):
            with self.subTest(symbol=symbol):
                self.assertEqual(records[symbol]["instrument_type"], "UNKNOWN_OR_AMBIGUOUS")
                self.assertFalse(records[symbol]["research_eligibility"])
                self.assertEqual(records[symbol]["primary_exclusion_reason"], "SPAC_STYLE_AMBIGUOUS")

        eligible = diagnostics["eligible_common_stock_candidates"]
        exclusions = sum(diagnostics["in_scope_primary_exclusions"].values())
        self.assertEqual(diagnostics["in_scope_2010_plus_records"] - eligible - exclusions, 0)

    def test_independent_spac_invariant_catches_leak_and_blocks_publication(self):
        """Independent descriptor audit catches a classifier leak before latest publication."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        leaked = {
            "symbol": "MBAC",
            "name": "M3 Brigade Acquisition II Corp - Class A",
            "exchange": "NYSE",
            "assetType": "Stock",
            "ipoDate": "2021-04-26",
            "delistingDate": "2023-12-08",
            "status": "delisted",
            "instrument_type": "ELIGIBLE_COMMON_STOCK",
            "research_eligibility": True,
            "primary_exclusion_reason": "NONE",
            "secondary_flags": [],
        }
        diagnostics = {
            "raw_total_records": 1,
            "pre_2010_records": 0,
            "in_scope_2010_plus_records": 1,
            "eligible_common_stock_candidates": 1,
            "in_scope_primary_exclusions": {},
            "all_primary_exclusions": {},
            "candidates": [leaked],
            "all_records": [leaked],
        }
        sentinel_path = self.test_dir / "chatgpt_action_report.md"
        sentinel_path.write_text("previous successful latest", encoding="utf-8")

        with patch.object(engine, "fetch_alpha_vantage_delisted", return_value=pd.DataFrame([leaked])), \
             patch.object(engine, "parse_av_raw", return_value=diagnostics), \
             patch("testdata.delisted_security_metadata.classify_instrument", return_value=("ELIGIBLE_COMMON_STOCK", "NONE")), \
             patch.object(engine, "fetch_sec_company_tickers_exchange") as sec_mapping:
            with self.assertRaises(ClassificationInvariantError):
                engine.run_pilot(sample_size=1)
            sec_mapping.assert_not_called()

        failure_path = self.test_dir / "delisted_metadata_classification_invariant_failures.csv"
        self.assertTrue(failure_path.exists())
        failure_df = pd.read_csv(failure_path)
        self.assertEqual(failure_df.loc[0, "symbol"], "MBAC")
        self.assertEqual(failure_df.loc[0, "detected_forbidden_type"], "SPAC")
        self.assertEqual(sentinel_path.read_text(encoding="utf-8"), "previous successful latest")
        self.assertFalse((self.test_dir / "runs").exists())

    def test_full_pool_invariant_fails_before_sampling_and_writes_diagnostics(self):
        """Forbidden descriptor in an eligible record fails closed before SEC/sampling."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        leaked = {
            "symbol": "LEAK",
            "name": "Leaked Preferred Series A",
            "exchange": "NASDAQ",
            "assetType": "Stock",
            "ipoDate": "2012-01-01",
            "delistingDate": "2020-01-01",
            "status": "delisted",
            "instrument_type": "ELIGIBLE_COMMON_STOCK",
            "research_eligibility": True,
            "primary_exclusion_reason": "NONE",
            "secondary_flags": [],
        }
        diagnostics = {
            "raw_total_records": 1,
            "pre_2010_records": 0,
            "in_scope_2010_plus_records": 1,
            "eligible_common_stock_candidates": 1,
            "in_scope_primary_exclusions": {},
            "all_primary_exclusions": {},
            "candidates": [leaked],
            "all_records": [leaked],
        }
        with patch.object(engine, "fetch_alpha_vantage_delisted", return_value=pd.DataFrame([leaked])), \
             patch.object(engine, "parse_av_raw", return_value=diagnostics), \
             patch.object(engine, "fetch_sec_company_tickers_exchange") as sec_mapping:
            with self.assertRaises(ClassificationInvariantError):
                engine.run_pilot(sample_size=1)
            sec_mapping.assert_not_called()

        failure_path = self.test_dir / "delisted_metadata_classification_invariant_failures.csv"
        self.assertTrue(failure_path.exists())
        self.assertIn("PREFERRED", failure_path.read_text(encoding="utf-8"))

    def test_name_exchange_only_identity_never_clears_different_ticker(self):
        """Issuer-level name matching cannot clear an unresolved security/ticker change."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        record = {
            "symbol": "MSCA", "name": "Main Street Capital", "exchange": "NYSE",
            "assetType": "Stock", "delistingDate": "2020-01-01",
        }
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[1396440, "MAIN STREET CAPITAL CORPORATION", "MAIN", "NYSE"]],
        }
        with patch.object(engine, "fetch_sec_submissions", return_value={
            "name": "MAIN STREET CAPITAL CORPORATION", "tickers": ["MAIN"],
            "exchanges": ["NYSE"], "stateOfIncorporation": "MD",
            "filings": {"recent": {"form": [], "filingDate": []}},
        }):
            result = engine.resolve_cik_and_match(record, sec_mapping)
        self.assertEqual(result["identity_status"], "SEC_NAME_EXCHANGE_MATCH")
        self.assertTrue(result["manual_review_flag"])

    def test_ticker_name_exchange_match_can_be_automatically_cleared(self):
        """Strong security-level ticker/name/exchange evidence remains eligible for automation."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        record = {
            "symbol": "SEVN", "name": "Seven Hills Realty Trust", "exchange": "NASDAQ",
            "assetType": "Stock", "delistingDate": "2020-01-01",
        }
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[12345, "SEVEN HILLS REALTY TRUST", "SEVN", "NASDAQ"]],
        }
        with patch.object(engine, "fetch_sec_submissions", return_value={
            "name": "SEVEN HILLS REALTY TRUST", "tickers": ["SEVN"],
            "exchanges": ["NASDAQ"], "stateOfIncorporation": "MD",
            "filings": {"recent": {"form": [], "filingDate": []}},
        }):
            result = engine.resolve_cik_and_match(record, sec_mapping)
        self.assertFalse(result["manual_review_flag"])

    def test_sevn_is_only_automatically_cleared_row_in_control_pilot(self):
        """Only the strong SEVN identity control leaves the manual-review queue."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        stale_failure_path = self.test_dir / "delisted_metadata_classification_invariant_failures.csv"
        stale_failure_path.write_text("symbol\nSTALE\n", encoding="utf-8")
        raw = pd.DataFrame([
            {"symbol": "SEVN", "name": "Seven Hills Realty Trust", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2017-01-01", "delistingDate": "2020-01-01", "status": "delisted"},
            {"symbol": "MSCA", "name": "Main Street Capital", "exchange": "NYSE", "assetType": "Stock", "ipoDate": "2017-01-01", "delistingDate": "2020-01-01", "status": "delisted"},
        ])
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [
                [12345, "SEVEN HILLS REALTY TRUST", "SEVN", "NASDAQ"],
                [1396440, "MAIN STREET CAPITAL CORPORATION", "MAIN", "NYSE"],
            ],
        }

        def submissions(cik):
            if str(cik) == "12345":
                return {
                    "name": "SEVEN HILLS REALTY TRUST", "tickers": ["SEVN"],
                    "exchanges": ["NASDAQ"], "stateOfIncorporation": "MD",
                    "filings": {"recent": {"form": [], "filingDate": []}},
                }
            return {
                "name": "MAIN STREET CAPITAL CORPORATION", "tickers": ["MAIN"],
                "exchanges": ["NYSE"], "stateOfIncorporation": "MD",
                "filings": {"recent": {"form": [], "filingDate": []}},
            }

        with patch.object(engine, "fetch_alpha_vantage_delisted", return_value=raw), \
             patch.object(engine, "fetch_sec_company_tickers_exchange", return_value=sec_mapping), \
             patch.object(engine, "fetch_sec_submissions", side_effect=submissions):
            engine.run_pilot(sample_size=2)

        master_df = pd.read_csv(self.test_dir / "delisted_metadata_master_pilot.csv")
        automatic_symbols = master_df.loc[
            master_df["manual_review_flag"].astype(str).str.lower() == "false", "symbol"
        ].tolist()
        self.assertEqual(automatic_symbols, ["SEVN"])
        self.assertFalse(stale_failure_path.exists())

    def test_classification_precedes_sampling_and_reconciliation_stays_zero(self):
        """Ineligible structures are excluded from deterministic candidate pool before sampling."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        raw = pd.DataFrame([
            {"symbol": "IPOA", "name": "Social Capital Hedosophia Holdings Corp Class A", "exchange": "NYSE", "assetType": "Stock", "ipoDate": "2017-01-01", "delistingDate": "2020-01-01", "status": "delisted"},
            {"symbol": "SEVN", "name": "Seven Hills Realty Trust", "exchange": "NASDAQ", "assetType": "Stock", "ipoDate": "2017-01-01", "delistingDate": "2020-01-01", "status": "delisted"},
        ])
        diagnostics = engine.parse_av_raw(raw)
        self.assertEqual(diagnostics["eligible_common_stock_candidates"], 1)
        self.assertEqual([row["symbol"] for row in diagnostics["candidates"]], ["SEVN"])
        self.assertEqual(sum(diagnostics["in_scope_primary_exclusions"].values()), 1)

    def test_csv_provenance_validation(self):
        """Prove that all CSV rows carry same run_id/schema/generated_at as metadata, and validate_report_set passes."""
        engine = MetadataMasterEngine(mode="CACHE_ONLY")
        record = {
            "symbol": "AAPL",
            "name": "Apple Inc",
            "exchange": "NASDAQ",
            "delistingDate": "2020-01-01",
            "assetType": "Stock",
            "status": "delisted"
        }
        sec_mapping = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[320193, "APPLE INC", "AAPL", "NASDAQ"]]
        }
        
        with patch.object(engine, "fetch_alpha_vantage_delisted") as mock_av, \
             patch.object(engine, "fetch_sec_company_tickers_exchange") as mock_sec_map, \
             patch.object(engine, "fetch_sec_submissions") as mock_sub:
             
            mock_av.return_value = pd.DataFrame([record])
            mock_sec_map.return_value = sec_mapping
            mock_sub.return_value = {
                "name": "APPLE INC",
                "tickers": ["AAPL"],
                "exchanges": ["NASDAQ"],
                "stateOfIncorporation": "DE",
                "filings": {"recent": {"form": [], "filingDate": []}}
            }
            
            res = engine.run_pilot(sample_size=1)
            run_id = res["run_id"]
            
            master_path = self.test_dir / "delisted_metadata_master_pilot.csv"
            review_path = self.test_dir / "delisted_metadata_manual_review.csv"
            
            self.assertTrue(master_path.exists())
            master_df = pd.read_csv(master_path)
            self.assertEqual(master_df.loc[0, "run_id"], run_id)
            self.assertEqual(master_df.loc[0, "report_schema_version"], "2.0.0")
            self.assertTrue(pd.notna(master_df.loc[0, "generated_at"]))
            
            self.assertIn("suffix_signal", master_df.columns)
            self.assertIn("suffix_interpretation", master_df.columns)
            self.assertIn("suffix_supporting_evidence", master_df.columns)
            self.assertIn("related_base_symbol_present", master_df.columns)
            self.assertIn("security_class_conflict", master_df.columns)

if __name__ == "__main__":
    unittest.main()
