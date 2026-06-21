import tempfile
import unittest
from pathlib import Path

from testdata.runner import resolve_project_path


class ReportPathsTest(unittest.TestCase):
    def test_testdata_reports_input_does_not_duplicate_testdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "testdata" / "hisseler"
            data_dir.mkdir(parents=True)
            resolved = resolve_project_path("testdata/reports", data_dir)
            self.assertEqual(resolved, root / "testdata" / "reports")
            self.assertNotIn("testdata\\testdata\\reports", str(resolved).lower())

    def test_reports_input_resolves_under_testdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "testdata" / "hisseler"
            data_dir.mkdir(parents=True)
            resolved = resolve_project_path("reports", data_dir)
            self.assertEqual(resolved, root / "testdata" / "reports")


if __name__ == "__main__":
    unittest.main()
