import unittest
from pathlib import Path
import re

from testdata.alpaca_paper_broker import AlpacaPaperBroker
from testdata.broker_interface import BrokerInterface


class DummyBroker(BrokerInterface):
    def get_account(self):
        return {}

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def preview_order(self, order_intent):
        return dict(order_intent)


class BrokerSubmitSafetyTest(unittest.TestCase):
    def test_broker_interface_submit_is_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "Order submission disabled"):
            DummyBroker().submit_order({"symbol": "AAPL"})

    def test_alpaca_paper_broker_rejects_non_paper_endpoint(self):
        with self.assertRaisesRegex(ValueError, "paper-api"):
            AlpacaPaperBroker(base_url="https://api.alpaca.markets")

    def test_alpaca_paper_broker_submit_is_disabled(self):
        broker = AlpacaPaperBroker(base_url="https://paper-api.alpaca.markets")
        with self.assertRaisesRegex(RuntimeError, "Alpaca paper submit is disabled"):
            broker.submit_order({"symbol": "AAPL"})

    def test_alpaca_paper_broker_submit_paper_order_gates(self):
        broker = AlpacaPaperBroker(base_url="https://paper-api.alpaca.markets")
        # 1. Raise if not enabled
        with self.assertRaisesRegex(RuntimeError, "Alpaca paper submit is disabled"):
            broker.submit_paper_order({"symbol": "AAPL", "side": "buy"}, paper_submit_enabled=False)
        # 2. Raise if incorrect token
        with self.assertRaisesRegex(ValueError, "Invalid confirmation token"):
            broker.submit_paper_order({"symbol": "AAPL", "side": "buy"}, paper_submit_enabled=True, confirm_token="WRONG")
        # 3. Raise if live endpoint
        broker_live = AlpacaPaperBroker(base_url="https://paper-api.alpaca.markets")
        broker_live.base_url = "https://api.alpaca.markets"  # bypass init URL check
        with self.assertRaisesRegex(ValueError, "Refusing Alpaca live connection"):
            broker_live.submit_paper_order({"symbol": "AAPL", "side": "buy"}, paper_submit_enabled=True, confirm_token="CONFIRM_PAPER_SUBMIT")
        # 4. Raise if short side
        with self.assertRaisesRegex(ValueError, "Only BUY orders are allowed"):
            broker.submit_paper_order({"symbol": "AAPL", "side": "sell"}, paper_submit_enabled=True, confirm_token="CONFIRM_PAPER_SUBMIT")

    def test_application_code_has_no_post_order_path(self):
        root = Path(__file__).resolve().parents[1] / "testdata"
        scanned = []
        forbidden_patterns = [
            re.compile(r"\brequests\.post\s*\("),
            re.compile(r"\brequests\.request\s*\(\s*['\"]POST['\"]"),
            re.compile(r"\bmethod\s*=\s*['\"]POST['\"]"),
            re.compile(r"\burllib\.request\.Request\s*\([^)]*\bmethod\s*=\s*['\"]POST['\"]", re.DOTALL),
        ]
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if path.name.endswith("_legacy.py") or {"reports", "backups", "__pycache__"} & parts:
                continue
            # POST is allowed ONLY inside the alpaca_paper_broker.py file
            if path.name == "alpaca_paper_broker.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            scanned.append(str(path.relative_to(root)))
            for pattern in forbidden_patterns:
                if pattern.search(text):
                    self.fail(f"Forbidden submit/POST pattern {pattern.pattern!r} found in {path.relative_to(root)}")
        self.assertNotIn("alpaca_paper_broker.py", scanned)
        self.assertIn(str(Path("strategies") / "relative_strength_rotation.py"), scanned)

    def test_submit_caps_and_ranking_in_dry_run(self):
        import tempfile
        import shutil
        import pandas as pd
        from testdata.execution_engine import run_alpaca_paper_submit
        
        temp_dir = tempfile.mkdtemp()
        try:
            # Write a dummy paper_pending_orders.csv
            pending_df = pd.DataFrame([
                {
                    "created_at": "2026-06-24 12:00:00",
                    "signal_date": "2026-06-23 13:50:00-04:00",
                    "strategy_name": "rs_m126_top30_sma100_no_regime",
                    "symbol": "AAPL",
                    "action": "BUY",
                    "paper_rank": 2,
                    "ranking_score": 1.5,
                    "paper_position_notional": 20.0,
                    "liquidity_guard_pass": "True",
                    "status": "PENDING",
                    "user_decision": "ACCEPT",
                    "fill_status": "PENDING_NEXT_OPEN",
                },
                {
                    "created_at": "2026-06-24 12:00:00",
                    "signal_date": "2026-06-23 13:50:00-04:00",
                    "strategy_name": "rs_m126_top30_sma100_no_regime",
                    "symbol": "MSFT",
                    "action": "BUY",
                    "paper_rank": 1,
                    "ranking_score": 1.8,
                    "paper_position_notional": 15.0,
                    "liquidity_guard_pass": "True",
                    "status": "PENDING",
                    "user_decision": "ACCEPT",
                    "fill_status": "PENDING_NEXT_OPEN",
                },
                {
                    "created_at": "2026-06-24 12:00:00",
                    "signal_date": "2026-06-23 13:50:00-04:00",
                    "strategy_name": "rs_m126_top30_sma100_no_regime",
                    "symbol": "GOOG",
                    "action": "BUY",
                    "paper_rank": 3,
                    "ranking_score": 1.2,
                    "paper_position_notional": 10.0,
                    "liquidity_guard_pass": "True",
                    "status": "PENDING",
                    "user_decision": "ACCEPT",
                    "fill_status": "PENDING_NEXT_OPEN",
                }
            ])
            pending_df.to_csv(Path(temp_dir) / "paper_pending_orders.csv", index=False)
            
            # Run alpaca submit in dry-run with max_submit_orders=1, max_submit_notional=25
            preview = run_alpaca_paper_submit(
                latest_dir=temp_dir,
                paper_submit_enabled=False,
                max_submit_orders=1,
                max_submit_notional=25
            )
            
            # MSFT is rank 1 (15 notional) - fits.
            # AAPL is rank 2 (20 notional) - would exceed max_submit_orders (which is 1).
            # GOOG is rank 3 (10 notional) - would exceed max_submit_orders.
            
            # Verify MSFT has status DRY_RUN_ONLY and reason paper_submit_disabled_by_default
            msft_row = preview[preview["symbol"] == "MSFT"].iloc[0]
            self.assertEqual(msft_row["status"], "DRY_RUN_ONLY")
            self.assertEqual(msft_row["reason"], "paper_submit_disabled_by_default")
            
            # Verify AAPL has reason max_submit_orders_cap_exceeded
            aapl_row = preview[preview["symbol"] == "AAPL"].iloc[0]
            self.assertEqual(aapl_row["reason"], "max_submit_orders_cap_exceeded")
            
            # Verify GOOG has reason max_submit_orders_cap_exceeded
            goog_row = preview[preview["symbol"] == "GOOG"].iloc[0]
            self.assertEqual(goog_row["reason"], "max_submit_orders_cap_exceeded")
            
            # Also test notional cap
            # Run with max_submit_orders=2, max_submit_notional=25
            # MSFT (15) + AAPL (20) = 35 (exceeds notional). So AAPL should exceed notional cap.
            preview_notional = run_alpaca_paper_submit(
                latest_dir=temp_dir,
                paper_submit_enabled=False,
                max_submit_orders=2,
                max_submit_notional=25
            )
            
            msft_row2 = preview_notional[preview_notional["symbol"] == "MSFT"].iloc[0]
            self.assertEqual(msft_row2["reason"], "paper_submit_disabled_by_default")
            
            aapl_row2 = preview_notional[preview_notional["symbol"] == "AAPL"].iloc[0]
            self.assertEqual(aapl_row2["reason"], "max_submit_notional_cap_exceeded")
            
        finally:
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    unittest.main()
