import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTDATA = ROOT / "testdata"


class LegacyIsolationTest(unittest.TestCase):
    def test_production_code_does_not_import_legacy_scanner(self):
        scanned = []
        for path in TESTDATA.rglob("*.py"):
            parts = set(path.parts)
            if path.name.endswith("_legacy.py") or {"reports", "backups", "__pycache__"} & parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
            scanned.append(str(path.relative_to(TESTDATA)))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            "universe_scanner_legacy",
                            alias.name,
                            f"Legacy scanner import found in {path.relative_to(TESTDATA)}",
                        )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertNotIn(
                        "universe_scanner_legacy",
                        module,
                        f"Legacy scanner import found in {path.relative_to(TESTDATA)}",
                    )
        self.assertIn("runner.py", scanned)
        self.assertIn("universe_scanner.py", scanned)

    def test_production_code_does_not_exec_or_eval_legacy_extraction(self):
        forbidden_tokens = ["exec(", "eval(", "SourceFileLoader", "runpy.run_path"]
        for path in TESTDATA.rglob("*.py"):
            parts = set(path.parts)
            if path.name.endswith("_legacy.py") or {"reports", "backups", "__pycache__"} & parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "universe_scanner_legacy" not in text and not any(token in text for token in forbidden_tokens):
                continue
            for token in forbidden_tokens:
                self.assertNotIn(token, text, f"Forbidden dynamic extraction token {token!r} in {path.relative_to(TESTDATA)}")
            self.assertNotIn("universe_scanner_legacy", text, f"Legacy scanner reference in {path.relative_to(TESTDATA)}")


if __name__ == "__main__":
    unittest.main()
