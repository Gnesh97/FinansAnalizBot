import tempfile
import unittest
from pathlib import Path

import pandas as pd

from testdata.runner import _write_regression_guard_report


class RegressionGuardTest(unittest.TestCase):
    def _decision(self):
        return pd.DataFrame(
            [
                {
                    "experiment_name": "rs_m126_top30_sma100_no_regime",
                    "final_role": "main",
                }
            ]
        )

    def _report(self, qqq_return, ew_return, qqq_end, ew_end):
        return pd.DataFrame(
            [
                {
                    "Experiment Name": "rs_m126_top30_sma100_no_regime",
                    "QQQ BuyHold Return (%)": qqq_return,
                    "EqualWeight Universe Return (%)": ew_return,
                    "QQQ Benchmark Start": "2026-01-01 09:30:00-04:00",
                    "QQQ Benchmark End": qqq_end,
                    "EqualWeight Benchmark Start": "2026-01-01 09:30:00-04:00",
                    "EqualWeight Benchmark End": ew_end,
                }
            ]
        )

    def _write_baseline(self, directory):
        content = "\n".join(
            [
                "# Regression Baseline Update Report",
                "",
                "| Parameter | Previous Baseline Value | New Baseline Value | Status |",
                "|---|---|---|---|",
                "| **Baseline End Date** | 2026-01-02 | 2026-01-03 | Updated |",
                "| **QQQ Expected Return (%)** | 10.0 | 11.0 | Updated |",
                "| **EqualWeight Expected Return (%)** | 20.0 | 21.0 | Updated |",
                "",
            ]
        )
        (Path(directory) / "regression_baseline_update_report.md").write_text(content, encoding="utf-8")

    def _write_pending(self, directory):
        pd.DataFrame(
            [
                {
                    "symbol": "PANW",
                    "strategy_name": "rs_m126_top30_sma100_no_regime",
                    "action": "BUY",
                    "user_decision": "ACCEPT",
                }
            ]
        ).to_csv(Path(directory) / "paper_pending_orders.csv", index=False)

    def test_same_date_range_benchmark_return_change_is_critical(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_pending(tmp)
            self._write_baseline(tmp)
            path = Path(tmp) / "regression_guard_report.md"
            result = _write_regression_guard_report(
                self._report(
                    12.0,
                    22.0,
                    "2026-01-03 00:00:00-04:00",
                    "2026-01-03 00:00:00-04:00",
                ),
                self._decision(),
                tmp,
                path,
            )
            content = path.read_text(encoding="utf-8")
            self.assertEqual(result["status"], "CRITICAL")
            self.assertIn("qqq_return_expected", result["critical"][0])
            self.assertIn("- benchmark changed: false", content)

    def test_newer_actual_date_range_benchmark_return_change_is_baseline_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_pending(tmp)
            self._write_baseline(tmp)
            path = Path(tmp) / "regression_guard_report.md"
            result = _write_regression_guard_report(
                self._report(
                    12.0,
                    22.0,
                    "2026-01-06 00:00:00-04:00",
                    "2026-01-06 00:00:00-04:00",
                ),
                self._decision(),
                tmp,
                path,
            )
            content = path.read_text(encoding="utf-8")
            self.assertEqual(result["status"], "BASELINE_STALE")
            self.assertEqual(result["critical"], [])
            self.assertIn("manual baseline refresh command", content)
            self.assertIn("- strategy metrics changed: false", content)
            self.assertIn("- benchmark changed: false", content)


if __name__ == "__main__":
    unittest.main()
