import unittest

import pandas as pd

from testdata.reports import calculate_buyhold_metrics, calculate_equal_weight_benchmark


class BenchmarkIntegrityTest(unittest.TestCase):
    def test_equal_weight_benchmark_keeps_valid_losing_symbols(self):
        index = pd.to_datetime(["2026-01-01", "2026-01-02"])
        asset_data = {
            "WINNER": pd.DataFrame({"Close": [100.0, 200.0]}, index=index),
            "LOSER": pd.DataFrame({"Close": [100.0, 50.0]}, index=index),
        }
        metrics = calculate_equal_weight_benchmark(
            asset_data,
            pd.Timestamp("2026-01-01"),
            pd.Timestamp("2026-01-02"),
        )
        self.assertEqual(metrics["Return (%)"], 25.0)

    def test_buyhold_benchmark_uses_start_or_prior_price_not_future_price(self):
        close = pd.Series(
            [100.0, 200.0, 300.0],
            index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
        )
        metrics = calculate_buyhold_metrics(
            close,
            pd.Timestamp("2026-01-02"),
            pd.Timestamp("2026-01-03"),
        )
        self.assertEqual(metrics["Return (%)"], 50.0)

    def test_buyhold_benchmark_requires_price_at_or_before_start(self):
        close = pd.Series(
            [100.0],
            index=pd.to_datetime(["2026-01-03"]),
        )
        with self.assertRaisesRegex(ValueError, "baslangic"):
            calculate_buyhold_metrics(
                close,
                pd.Timestamp("2026-01-02"),
                pd.Timestamp("2026-01-03"),
            )


if __name__ == "__main__":
    unittest.main()
