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
        with self.assertRaisesRegex(RuntimeError, "Order submission disabled"):
            broker.submit_order({"symbol": "AAPL"})

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
            text = path.read_text(encoding="utf-8", errors="replace")
            scanned.append(str(path.relative_to(root)))
            for pattern in forbidden_patterns:
                if pattern.search(text):
                    self.fail(f"Forbidden submit/POST pattern {pattern.pattern!r} found in {path.relative_to(root)}")
        self.assertIn("alpaca_paper_broker.py", scanned)
        self.assertIn(str(Path("strategies") / "relative_strength_rotation.py"), scanned)


if __name__ == "__main__":
    unittest.main()
