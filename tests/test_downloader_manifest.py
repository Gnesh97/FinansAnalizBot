import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DownloaderManifestTest(unittest.TestCase):
    def test_manifest_records_downloaded_date_ranges(self):
        text = (ROOT / "testdata" / "vericekici_updated.py").read_text(encoding="utf-8")
        required = [
            "def date_range_summary",
            '"daily_first_date"',
            '"daily_last_date"',
            '"raw_5min_first_date"',
            '"raw_5min_last_date"',
            '"bar_130min_first_date"',
            '"bar_130min_last_date"',
        ]
        for needle in required:
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
